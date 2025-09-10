# -*- coding: utf-8 -*-
import sys
import json
from datetime import datetime
import asyncio
import aiofiles
import tinytuya
import aiomqtt
import os
import resource
import signal
import time
import traceback
import logging


class MqttLogHandler(logging.Handler):
    def __init__(self, bridge_instance):
        super().__init__()
        self.bridge = bridge_instance
        self.log_queue = asyncio.Queue(maxsize=1000)
        self.bridge.log_publisher_task = self.bridge.task_creator(self._log_publisher())

        self.level_to_topic_map = {
            logging.DEBUG: 'debug',
            logging.INFO: 'info',
            logging.WARNING: 'warning',
            logging.ERROR: 'error',
            logging.CRITICAL: 'critical',
        }

    def emit(self, record):
        try:
            message = self.format(record)
            category = self.level_to_topic_map.get(record.levelno, 'info')
            self.log_queue.put_nowait((category, message))
        except asyncio.QueueFull:
            sys.stderr.write(f"MQTT_LOG_QUEUE_FULL: Log message dropped. | {message}\n")
        except Exception as e:
            sys.stderr.write(f"MQTT_LOG_FAIL: {e} | {message}\n")

    async def _log_publisher(self):
        while True:
            category, message = await self.log_queue.get()
            try:
                if self.bridge.mqtt_publisher:
                    topic = self.bridge.mqtt_config['topic']['publish'][category]
                    await self.bridge.mqtt_publisher(topic=topic, payload=message)
                else:
                    sys.stderr.write(f"{message}\n")
            except aiomqtt.MqttError as e:
                sys.stderr.write(f"MQTT_LOG_FAIL: {e} | {message}\n")
            finally:
                self.log_queue.task_done()

class TuyaMQTTBridge:
    def __init__(self, config, foreground=False, debug=False, logger=None, mqtt_publisher=None, task_creator=None):
        # --- Configuration & State ---
        self.config = config
        self.foreground = foreground
        self.logger = logger
        self.debug = debug

        self.daemon_stat = {
            'version': '1.1.0',
            'start_time': datetime.now(),
        }
        self.root_topic = 'tuya2mqtt'
        self.mqtt_config = {
            'broker': {
                'hostname': 'localhost',
                'port': 1883,
            },
            'login': {
                'username': '',
                'password': '',
            },
            'topic': {
                'subscribe': {
                    'add': f"{self.root_topic}/device/add",
                    'delete': f"{self.root_topic}/device/delete",
                    'query': f"{self.root_topic}/device/query",
                    'set': f"{self.root_topic}/device/set",
                    'get': f"{self.root_topic}/device/get",
                    'command': f"{self.root_topic}/device/command",
                },
                'publish': {
                    'command': f"{self.root_topic}/data/command",
                    'status': f"{self.root_topic}/data/status",
                    'daemon': f"{self.root_topic}/log/daemon",
                    'debug': f"{self.root_topic}/log/debug",
                    'info': f"{self.root_topic}/log/info",
                    'warning': f"{self.root_topic}/log/warning",
                    'error': f"{self.root_topic}/log/error",
                    'critical': f"{self.root_topic}/log/critical",
                },
            },
            'daemon': {
                'subdevice_add_retries': 10,
                'retry_delay_seconds': 5,
                'max_retry_delay_seconds': 320,
                'heartbeat_interval': 10,
                'instance_lock_timeout': 0.2,
            },
        }
        self.mqtt_publisher = mqtt_publisher
        self.task_creator = task_creator or asyncio.create_task
        self.tuya_state = {
            'device': {
                'id': {},
                'name': {},
            },
            'lock': asyncio.Lock(),
        }
        self.shutdown_event = asyncio.Event()

    # --- Logging ---
    def _setup_logging(self, debug=False):
        self.logger = logging.getLogger('TuyaMQTTBridge')
        log_level = logging.DEBUG if debug else logging.INFO
        self.logger.setLevel(log_level)
        self.logger.propagate = False

        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        if self.foreground:
            handler = logging.StreamHandler(sys.stdout)
        else:
            handler = MqttLogHandler(self)

        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    # --- Configuration ---
    async def _load_conf(self):
        def deep_update(d, u):
            for k, v in u.items():
                if isinstance(v, dict) and isinstance(d.get(k), dict):
                    deep_update(d[k], v)
                else:
                    d[k] = v
            return d
        if isinstance(self.config, dict):
            deep_update(self.mqtt_config, self.config)
        elif isinstance(self.config, str):
            try:
                async with aiofiles.open(self.config, mode='r', encoding='utf-8') as f:
                    config_data = json.loads(await f.read())
                    deep_update(self.mqtt_config, config_data)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"CONFIG_ERROR: Could not parse {self.config}: {e}\n")
                sys.stderr.write(f"Configuration file {self.config} is corrupted. Using default settings.\n")
            except FileNotFoundError:
                async with aiofiles.open(self.config, mode='w', encoding='utf-8') as f:
                    await f.write(json.dumps(self.mqtt_config, indent=2))
        else:
            raise TypeError('config must be either filename or dictionary')

    # --- Core Logic ---
    async def _recv_topic(self, msg):
        try:
            if isinstance(msg.payload, bytes):
                payload_str = msg.payload.decode('utf-8')
            else:
                payload_str = msg.payload
            self.logger.info(f"Topic: {msg.topic}, Payload: {payload_str}")
            payloads = json.loads(payload_str)
        except json.decoder.JSONDecodeError:
            self.logger.info('Payload ignored')
            return
        except Exception:
            self.logger.error(traceback.format_exc())
            return

        if not isinstance(payloads, list):
            payloads = [payloads]

        topic_str = str(msg.topic)
        topic_map = self.mqtt_config['topic']['subscribe']

        # --- ADD DEVICE ---
        if topic_str == topic_map['add']:
            for payload in payloads:
                task = None
                added = None
                parent = None
                try:
                    # Subdevice
                    if 'parent' in payload and 'node_id' in payload:
                        for _ in range(self.mqtt_config['daemon']['subdevice_add_retries']):
                            async with self.tuya_state['lock']:
                                if payload['parent'] in self.tuya_state['device']['id']:
                                    if self.tuya_state['device']['id'][payload['parent']].get('status') == 'online':
                                        parent = self.tuya_state['device']['id'][payload['parent']].get('device')
                            if parent:
                                added = tinytuya.DeviceAsync(dev_id=payload['id'], cid=payload['node_id'], parent=parent)
                                async with self.tuya_state['lock']:
                                    self.tuya_state['device']['id'][payload['node_id']] = {'device': added, 'parent': payload['parent']}
                                    keys_to_save = ['id', 'node_id', 'parent', 'name']
                                    self.tuya_state['device']['id'][payload['node_id']]['payload'] = {k: payload[k] for k in keys_to_save if k in payload}
                                    self.tuya_state['device']['id'][payload['node_id']]['last_seen'] = None
                                    if payload['node_id'] not in self.tuya_state['device']['id'][payload['parent']]['children']:
                                        self.tuya_state['device']['id'][payload['parent']]['children'].append(payload['node_id'])
                                    self._add_device_name_mapping(payload['node_id'], payload.get('name'))
                                    self.logger.info(f"Subdevice added: {payload['node_id']}")
                                    break
                            self.logger.warning(f"Parent not found, retrying: {payload['node_id']}")
                            await asyncio.sleep(self.mqtt_config['daemon']['retry_delay_seconds'])
                        if added is None:
                            self.logger.error(f"Parent not found, fail to add subdevice: {payload['node_id']}")
                    # Device
                    elif 'id' in payload and payload['id']:
                        async with self.tuya_state['lock']:
                            if payload['id'] in self.tuya_state['device']['id']:
                                self.logger.warning(f"Already added, ignoring: {payload['id']}")
                            else:
                                self.tuya_state['device']['id'][payload['id']] = {}
                                keys_to_save = ['id', 'ip', 'key', 'version', 'name']
                                self.tuya_state['device']['id'][payload['id']]['payload'] = {k: payload[k] for k in keys_to_save if k in payload}
                                self.tuya_state['device']['id'][payload['id']]['status'] = 'connecting'
                                self.tuya_state['device']['id'][payload['id']]['last_seen'] = None
                                task = self.task_creator(self._tuya_receiver(payload))
                                self.tuya_state['device']['id'][payload['id']]['task'] = task
                                self.tuya_state['device']['id'][payload['id']]['children'] = []
                                self._add_device_name_mapping(payload['id'], payload.get('name'))
                                self.logger.info(f"Device added and listener task created: {payload['id']}")
                except Exception:
                    self.logger.error(traceback.format_exc())
                    if added:
                        try:
                            added.close()
                        except:
                            pass

        # --- DELETE DEVICE ---
        elif topic_str == topic_map['delete']:
            for payload in payloads:
                try:
                    async with self.tuya_state['lock']:
                        target_ids = self._get_target_ids(payload)
                        for device_id in target_ids:
                            if device_id in self.tuya_state['device']['id']:
                                device_info = self.tuya_state['device']['id'][device_id]
                                if 'task' in device_info:
                                    device_info['task'].cancel()
                                else: # Subdevice
                                    try:
                                        parent_id = device_info.get('parent')
                                        if parent_id and parent_id in self.tuya_state['device']['id']:
                                            self.tuya_state['device']['id'][parent_id]['children'].remove(device_id)
                                    except (KeyError, ValueError):
                                        pass
                                    await self._cleanup_device(device_id)
                                self.logger.info(f"Device deleted: {device_id}")
                except Exception:
                    self.logger.error(traceback.format_exc())

        # --- SET/GET/COMMAND ---
        elif topic_str in (topic_map['set'], topic_map['get'], topic_map['command']):
            for payload in payloads:
                try:
                    target_devices = await self._get_target_devices(payload)
                    for device in target_devices:
                        if topic_str == topic_map['set']:
                            await device.set_multiple_values(payload['data'])
                            device.tuya2mqtt_last_sent_time = time.monotonic()
                            self.logger.info(f"Data set for device {device.id}")
                        elif topic_str == topic_map['get']:
                            await device.status()
                            self.logger.info(f"Status requested for device {device.id}")
                        elif topic_str == topic_map['command']:
                            command_name = payload.get('command')
                            if not command_name or command_name.startswith('_'):
                                self.logger.warning(f"Attempt to call an invalid or private command ('{command_name}') on device {device.id}. Ignoring.")
                                continue
                            try:
                                # last_sent_time not set here since command is not guaranteed to be a socket io
                                command = getattr(device, command_name)
                                if asyncio.iscoroutinefunction(command):
                                    await command(**payload.get('data', {}))
                                else:
                                    command(**payload.get('data', {}))
                                self.logger.info(f"{payload['command']} performed for device {device.id}")
                            except AttributeError:
                                self.logger.error(f"Device {device.id} has no command named '{command_name}'.")
                            except TypeError as e:
                                self.logger.error(f"Invalid arguments for command '{command_name}' on device {device.id}: {e}")
                except Exception:
                    self.logger.error(traceback.format_exc())

        # --- QUERY DAEMON ---
        elif topic_str == topic_map['query']:
            for payload in payloads:
                if 'stop' in payload or 'reset' in payload:
                    await self._terminator('stop' in payload)
                elif 'status' in payload:
                    await self._publish_daemon_status(payload)

    # --- Terminator ---
    async def _terminator(self, quit_program):
        self.logger.info('Shutdown process initiated... Publishing snapshot...')

        device_snapshot = await self._get_device_payloads(False)
        try:
            await self.mqtt_publisher(
                topic=self.mqtt_config['topic']['publish']['daemon'],
                payload=json.dumps(device_snapshot, ensure_ascii=False)
            )
        except:
            self.logger.info(json.dumps(device_snapshot, ensure_ascii=False))

        tasks_to_cancel = []
        async with self.tuya_state['lock']:
            for device_id in list(self.tuya_state['device']['id'].keys()):
                device_info = self.tuya_state['device']['id'].get(device_id, {})
                if 'task' in device_info:
                    tasks_to_cancel.append(device_info['task'])

        if hasattr(self, 'log_publisher_task'):
            tasks_to_cancel.append(self.log_publisher_task)
        for task in tasks_to_cancel:
            task.cancel()

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        self.logger.info(f"All device tasks cancelled. Exiting: {quit_program}")

        if quit_program:
            self.shutdown_event.set()

    # --- Helper Functions ---
    def _add_device_name_mapping(self, device_id, name):
        """Adds a device ID to the name->ID mapping. MUST be called inside TUYA lock."""
        if not name:
            return
        self.tuya_state['device']['id'][device_id]['name'] = name
        if name not in self.tuya_state['device']['name']:
            self.tuya_state['device']['name'][name] = []
        if device_id not in self.tuya_state['device']['name'][name]:
            self.tuya_state['device']['name'][name].append(device_id)

    async def _cleanup_device(self, device_id):
        """Cleans up a device's entries. MUST be called inside TUYA lock."""
        if device_id in self.tuya_state['device']['id']:
            device_info = self.tuya_state['device']['id'].pop(device_id)
            name = device_info.get('name')
            if name and name in self.tuya_state['device']['name']:
                if device_id in self.tuya_state['device']['name'][name]:
                    self.tuya_state['device']['name'][name].remove(device_id)
                if not self.tuya_state['device']['name'][name]:
                    del self.tuya_state['device']['name'][name]
            try:
                self.task_creator(device_info['device'].close())
            except:
                pass

    def _get_target_ids(self, payload):
        """Get a list of device IDs from a payload (by 'id' or 'name'). MUST be called inside TUYA lock."""
        target_ids = []
        if 'id' in payload:
            if payload['id'] in self.tuya_state['device']['id']:
                target_ids.append(payload['id'])
        elif 'name' in payload and payload['name'] in self.tuya_state['device']['name']:
            target_ids.extend(self.tuya_state['device']['name'][payload['name']])
        return target_ids

    async def _get_target_devices(self, payload):
        """Get a list of tinytuya device objects from a payload."""
        devices = []
        async with self.tuya_state['lock']:
            target_ids = self._get_target_ids(payload)
            for device_id in target_ids:
                if device_id in self.tuya_state['device']['id'] and 'device' in self.tuya_state['device']['id'][device_id]:
                    devices.append(self.tuya_state['device']['id'][device_id]['device'])
        return devices

    async def _get_device_payloads(self, details=True):
        device_payloads = []
        async with self.tuya_state['lock']:
            all_devices_info = list(self.tuya_state['device']['id'].values())
        for device_info in all_devices_info:
            device_entry = device_info.get('payload', {}).copy()
            if details:
                if 'status' in device_info:
                    device_entry['status'] = device_info['status']
                if 'last_seen' in device_info:
                    device_entry['last_seen'] = device_info['last_seen']
                if 'task' in device_info:
                    children_count = len(device_info.get('children', []))
                    device_entry['type'] = 'hub device' if children_count > 0 else 'end device'
                    if children_count > 0:
                        device_entry['children_count'] = children_count
                else:
                    device_entry['type'] = 'sub device'
            device_payloads.append(device_entry)
        return device_payloads

    async def _publish_daemon_status(self, payload):
        """Gathers and publishes the daemon's current status."""
        daemon_status = {
            'tuya2mqtt': self.daemon_stat['version'],
            'uptime(min)': int((datetime.now() - self.daemon_stat['start_time']).total_seconds() / 60),
            'mqtt_broker': self.mqtt_config['broker']['hostname'],
            'connected_devices_count': len(self.tuya_state['device']['id']),
        }

        if payload['status'] is True:
            daemon_status['devices'] = await self._get_device_payloads(True)

        if self.mqtt_publisher:
            await self.mqtt_publisher(
                topic=self.mqtt_config['topic']['publish']['daemon'],
                payload=json.dumps(daemon_status, ensure_ascii=False)
            )
        else:
            self.logger.info(json.dumps(daemon_status, ensure_ascii=False))
        self.logger.info('Daemon status requested and published.')

    # --- Tuya Device Listener ---
    async def _tuya_receiver(self, payload):
        device_id = payload['id']
        self.logger.info(f"Starting listener for {device_id}")
        reconnect_delay = self.mqtt_config['daemon']['retry_delay_seconds']

        try:
            async with tinytuya.DeviceAsync(
                dev_id=payload['id'],
                address=payload.get('ip', 'Auto'),
                local_key=payload.get('key', ''),
                version=payload.get('version', 3.3),
                persist=True
            ) as device:

                async with self.tuya_state['lock']:
                    self.tuya_state['device']['id'][device_id]['device'] = device

                # WARNING: This optimization relies on the internal locking mechanism of tinytuya v2-async.
                # We don't need receive lock, since we have only one receiving task
                class DummyAsyncContext:
                    def locked(self):
                        return False
                    async def __aenter__(self):
                        pass
                    async def __aexit__(self, exc_type, exc_val, exc_tb):
                        pass
                # device._conn_lock = DummyAsyncContext()
                # device._send_lock = DummyAsyncContext()
                device._recv_lock = DummyAsyncContext()
                device._rcv2_lock = DummyAsyncContext()

                # We don't need callbacks
                async def DummyCallback(*args, **kwargs):
                    return
                device._defer_callbacks = DummyCallback

                # Set HB timer
                device.tuya2mqtt_last_sent_time = time.monotonic()

                if 'dev_type' in payload:
                    device.dev_type = payload['dev_type']
                    # Disable autodetect only if dev_type is explicitly set as "default"
                    # Otherwise, keep autodetect enabled and let tinytuya determine
                    device.disabledetect = (payload['dev_type'] == 'default')

                while True:
                    if time.monotonic() - device.tuya2mqtt_last_sent_time >= self.mqtt_config['daemon']['heartbeat_interval']:
                        await device.heartbeat()
                        device.tuya2mqtt_last_sent_time = time.monotonic()

                    try:
                        data = await device.receive()

                        if data is not None:
                            if 'Error' in data:
                                # Connection error
                                async with self.tuya_state['lock']:
                                    if device_id in self.tuya_state['device']['id']:
                                        self.tuya_state['device']['id'][device_id]['status'] = data['Error']
                                self.logger.error(json.dumps({'id': payload['id'], **data}))

                                # TinyTuya does automatically reconnect.
                                # Devices can sometimes take a while to re-connect to the WiFi, so if you get that error you can just wait a bit and retry the send/receive.
                                await asyncio.sleep(reconnect_delay)
                                reconnect_delay = min(reconnect_delay * 2, self.mqtt_config['daemon']['max_retry_delay_seconds'])
                                continue

                            if reconnect_delay != self.mqtt_config['daemon']['retry_delay_seconds']:
                                self.logger.info(f"Device {device_id} reconnected successfully. Resetting backoff delay.")
                                reconnect_delay = self.mqtt_config['daemon']['retry_delay_seconds']

                            if 'dps' in data:
                                cid = device_id
                                topic = self.mqtt_config['topic']['publish']['command']

                                if 'data' in data:
                                    if 'cid' in data['data']:
                                        cid = data['data']['cid']
                                else:
                                    topic = self.mqtt_config['topic']['publish']['status']
                                    if 'cid' in data:
                                        cid = data['cid']

                                name = None
                                async with self.tuya_state['lock']:
                                    if cid in self.tuya_state['device']['id']:
                                        self.tuya_state['device']['id'][cid]['last_seen'] = datetime.now().isoformat()
                                        name = self.tuya_state['device']['id'][cid].get('name')

                                await self.mqtt_publisher(
                                    topic=topic,
                                    payload=json.dumps({'id': cid, 'name': name, 'data': data['dps']}, ensure_ascii=False)
                                )

                        async with self.tuya_state['lock']:
                            if device_id in self.tuya_state['device']['id']:
                                self.tuya_state['device']['id'][device_id]['status'] = 'online'

                    except Exception as e:
                        self.logger.error(f"Error in receive loop for {device_id}: {e}")
                        async with self.tuya_state['lock']:
                            if device_id in self.tuya_state['device']['id']:
                                self.tuya_state['device']['id'][device_id]['status'] = str(e)
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, self.mqtt_config['daemon']['max_retry_delay_seconds'])

        except asyncio.CancelledError:
            self.logger.info(f"Listener task for {device_id} was cancelled.")
        except Exception:
            self.logger.error(f"Unhandled exception: {traceback.format_exc()}")
        finally:
            async with self.tuya_state['lock']:
                if device_id in self.tuya_state['device']['id']:
                    for child_id in list(self.tuya_state['device']['id'][device_id].get('children', [])):
                        await self._cleanup_device(child_id)
                    await self._cleanup_device(device_id)
            self.logger.info(f"Cleaned up resources for {device_id}.")

    # --- Main Application ---
    async def run(self):
        await self._load_conf()

        if self.logger is None:
            self._setup_logging(self.debug)

        if self.debug:
            tinytuya.set_debug(True)

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: self.task_creator(self._terminator(True)))
        mqtt = self.task_creator(self.mqtt_handler())

        await self.shutdown_event.wait()
        mqtt.cancel()

        try:
            await mqtt
        except asyncio.CancelledError:
            print("Daemon terminated.")

    # --- Mqtt Lock ---
    async def mqtt_lock(self, mqtt):
        await mqtt.subscribe(self.mqtt_config['topic']['publish']['daemon'])
        await mqtt.publish(topic=self.mqtt_config['topic']['subscribe']['query'], payload='{"status":false}', retain=True)
        try:
            await asyncio.wait_for(mqtt.messages.__anext__(), timeout=self.mqtt_config['daemon']['instance_lock_timeout'])
            return True
        except:
            return False
        finally:
            await mqtt.publish(topic=self.mqtt_config['topic']['subscribe']['query'], retain=True)
            await mqtt.unsubscribe(self.mqtt_config['topic']['publish']['daemon'])

    # --- Mqtt Handler ---
    async def mqtt_handler(self):
        reconnect_delay = self.mqtt_config['daemon']['retry_delay_seconds']
        while True:
            try:
                # 1. Cooperative Check (via anonymous client):
                #    A graceful pre-check using a retained message.
                async with aiomqtt.Client(
                    hostname=self.mqtt_config['broker']['hostname'],
                    port=self.mqtt_config['broker']['port'],
                    username=self.mqtt_config['login'].get('username'),
                    password=self.mqtt_config['login'].get('password'),
                ) as client:
                    self.logger.info(f"Using configuration file: {self.config}")
                    self.logger.info('Successfully connected to MQTT broker.')

                    if await self.mqtt_lock(client):
                        self.logger.info(f"Another instance is already running.")
                        self.shutdown_event.set()
                        break

                # 2. Enforced Lock (via unique client ID):
                #    A fail-safe against race conditions.
                async with aiomqtt.Client(
                    hostname=self.mqtt_config['broker']['hostname'],
                    port=self.mqtt_config['broker']['port'],
                    username=self.mqtt_config['login'].get('username'),
                    password=self.mqtt_config['login'].get('password'),
                    identifier=f"{self.root_topic}_{self.daemon_stat['version']}",
                ) as self.mqtt:

                    if reconnect_delay != self.mqtt_config['daemon']['retry_delay_seconds']:
                        self.logger.info(f"Mqtt reconnected successfully. Resetting backoff delay.")
                        reconnect_delay = self.mqtt_config['daemon']['retry_delay_seconds']
                    self.mqtt_publisher = self.mqtt.publish

                    # Subscribe to all necessary topics
                    for topic in self.mqtt_config['topic']['subscribe'].values():
                        await self.mqtt.subscribe(topic)
                        self.logger.info(f"Topic subscribed: {topic}")

                    # Main message processing loop
                    async for message in self.mqtt.messages:
                        self.task_creator(self._recv_topic(message))

            except aiomqtt.MqttError as error:
                self.logger.error(f"MQTT connection error: {error}. Reconnecting...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, self.mqtt_config['daemon']['max_retry_delay_seconds'])
            except asyncio.CancelledError:
                break
            except Exception:
                self.logger.error(f"An unexpected error occurred: {traceback.format_exc()}")
                await self._terminator(True)
        self.logger.info('MQTT disconnected')

    def start(self):
        """Wrapper to run the main async function."""
        try:
            asyncio.run(self.run())
        except KeyboardInterrupt:
            print("Foreground process interrupted by user.")


# --- main() ---
def main():
    # Increase file descriptor limit if possible
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < hard:
            resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except (ValueError, OSError) as e:
        print(f"Could not set rlimit: {e}")

    # Initialize paths
    WORK_FOLDER = os.path.dirname(os.path.realpath(__file__))
    FILE_NAME = os.path.splitext(os.path.basename(__file__))[0]
    PID_FILE = f"{WORK_FOLDER}/{FILE_NAME}.pid"
    DEFAULT_CONF_FILE = f"{WORK_FOLDER}/{FILE_NAME}.conf"

    # Command-line argument parsing
    if len(sys.argv) < 2:
        print(f"Usage: tuya2mqtt start|stop|restart|foreground|debug [config_file(optional)]")
        sys.exit(1)

    command = sys.argv[1].lower()
    CONF_FILE = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CONF_FILE

    no_daemon = False
    try:
        import daemon
        from daemon import pidfile
    except:
        print("Canont import daemon module. Only foreground or debug modes are available.")
        no_daemon = True

    if command in ('debug', 'foreground'):
        print("Running in foreground mode...")
        bridge = TuyaMQTTBridge(
            config=CONF_FILE,
            foreground=True,
            debug=(command == 'debug')
        )
        bridge.start()
        no_daemon = True

    if no_daemon:
        sys.exit(0)

    # --- Daemon control logic ---
    pid_file_obj = pidfile.TimeoutPIDLockFile(PID_FILE)

    if command == 'stop' or command == 'restart':
        print("Stopping daemon...")
        try:
            if pid_file_obj.is_locked():
                pid = pid_file_obj.read_pid()
                os.kill(pid, signal.SIGTERM)
                terminated_gracefully = False
                for _ in range(5):
                    time.sleep(2)
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        print("Process stopped successfully.")
                        terminated_gracefully = True
                        break

                if not terminated_gracefully:
                    print(f"Process {pid} did not terminate gracefully. Sending SIGKILL.")
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        print("Process was already gone before SIGKILL.")
            else:
                print("PID file not locked. Is the daemon running?")
        except Exception as e:
            print(f"Error while stopping: {e}")
        finally:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        if command == 'stop':
            sys.exit(0)

    if command == 'start' or command == 'restart':
        if pid_file_obj.is_locked():
            print(f"Daemon already running with PID {pid_file_obj.read_pid()}. Use 'restart' or 'stop' first.")
            sys.exit(1)

        print("Starting daemon...")
        context = daemon.DaemonContext(
            working_directory=WORK_FOLDER,
            umask=0o002,
            pidfile=pid_file_obj,
        )
        with context:
            bridge = TuyaMQTTBridge(config=CONF_FILE)
            bridge.start()


# --- Entry Point ---
if __name__ == "__main__":
    main()
