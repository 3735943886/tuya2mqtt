# -*- coding: utf-8 -*-
import sys
import json
from datetime import datetime
import time
import threading
import tinytuya
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
import os
import resource
import signal
import daemon
import traceback
from daemon import pidfile

FOREGROUND = False
CONF_FILE = None
DAEMON_STAT = {
  'version': '1.0.2',
  'start_time': datetime.now(),
}
ROOT_TOPIC = 'tuya2mqtt'
MQTT = {
  'broker': {
    'host': 'localhost',
    'port': 1883,
  },
  'login': {
    'username': '',
    'password': '',
  },
  'topic': {
    'subscribe': {
      'add': f'{ROOT_TOPIC}/device/add',
      'delete': f'{ROOT_TOPIC}/device/delete',
      'query': f'{ROOT_TOPIC}/device/query',
      'set': f'{ROOT_TOPIC}/device/set',
      'get': f'{ROOT_TOPIC}/device/get',
      'send': f'{ROOT_TOPIC}/device/send',
      },
    'publish': {
      'command': f'{ROOT_TOPIC}/data/command',
      'status': f'{ROOT_TOPIC}/data/status',
      'daemon': f'{ROOT_TOPIC}/log/daemon',
      'debug': f'{ROOT_TOPIC}/log/debug',
      'info': f'{ROOT_TOPIC}/log/info',
      'error': f'{ROOT_TOPIC}/log/error',
    },
  },
}
TUYA = {
  'device': {
    'id': {},
    'name': {},
  },
  'lock': threading.Lock(),
}

def logging(category, message):
  global FOREGROUND

  if FOREGROUND:
    if category == 'error':
      sys.stderr.write(message)
    else:
      print(f'{category} - {message}')
  else:
    MQTT['client'].publish(topic = MQTT['topic']['publish'][category], payload = message)

def OnConnectMqtt(client, userdata, flags, reason_code, properties):
  global MQTT
  if reason_code == 0:
    for topics in MQTT['topic']['subscribe']:
      client.subscribe(MQTT['topic']['subscribe'][topics])
      logging('info', 'Topic subscribed: {}'.format(MQTT['topic']['subscribe'][topics]))
  else:
    logging('error', f'Failed to connect, return code {reason_code}')
    Terminator(True)

def ReloadConf():
  global MQTT, CONF_FILE
  try:
    MQTT.update(json.load(open(CONF_FILE)))
  except:
    with open(CONF_FILE, 'w') as f:
      json.dump({key: value for key, value in MQTT.items() if key != 'client'}, f, indent = 2)

def RecvTopic(client, user, msg):
  global DAEMON_STAT, MQTT, TUYA

  try:
    logging('info', 'Topic: {}, Payload: {}'.format(msg.topic, msg.payload.decode('utf-8')))
    payloads = json.loads(msg.payload.decode('utf-8'))
  except Exception as e:
    logging('error', traceback.format_exc())
    return

  if type(payloads) != type([]):
    payloads = [payloads]

  if msg.topic == MQTT['topic']['subscribe']['add']:
    for payload in payloads:
      added = None
      try:
        with TUYA['lock']:
          if 'parent' in payload and payload['parent'] is not None and payload['parent'] != '' and payload['parent'] in TUYA['device']['id']:
            # Subdevice
            parent = TUYA['device']['id'][payload['parent']]['device']
            added = tinytuya.Device(dev_id = payload['id'], cid = payload['node_id'], parent = parent)
            TUYA['device']['id'][payload['node_id']] = { 'device': added, 'parent': payload['parent'] }
            keys_to_save = ['id', 'node_id', 'parent', 'name']
            TUYA['device']['id'][payload['node_id']]['payload'] = {key: payload[key] for key in keys_to_save if key in payload}
            if payload['node_id'] not in TUYA['device']['id'][payload['parent']]['children']:
              TUYA['device']['id'][payload['parent']]['children'].append(payload['node_id'])
            if 'name' in payload:
              TUYA['device']['id'][payload['node_id']]['name'] = payload['name']
              if payload['name'] in TUYA['device']['name']:
                if payload['node_id'] not in TUYA['device']['name'][payload['name']]:
                  TUYA['device']['name'][payload['name']].append(payload['node_id'])
              else:
                TUYA['device']['name'][payload['name']] = [payload['node_id']]
          elif 'id' in payload and payload['id'] not in TUYA['device']['id']:
            # Device
            TUYA['device']['id'][payload['id']] = {}
            keys_to_save = ['id', 'ip', 'key', 'version', 'name']
            TUYA['device']['id'][payload['id']]['payload'] = {key: payload[key] for key in keys_to_save if key in payload}
            added = threading.Thread(target = TuyaReceiver, args = (payload,))
            TUYA['device']['id'][payload['id']]['thread'] = added
            TUYA['device']['id'][payload['id']]['running'] = True
            TUYA['device']['id'][payload['id']]['children'] = []
            if 'name' in payload:
              TUYA['device']['id'][payload['id']]['name'] = payload['name']
              if payload['name'] in TUYA['device']['name']:
                if payload['id'] not in TUYA['device']['name'][payload['name']]:
                  TUYA['device']['name'][payload['name']].append(payload['id'])
              else:
                TUYA['device']['name'][payload['name']] = [payload['id']]
      except Exception as e:
        logging('error', traceback.format_exc())
        added = None
      if added is not None:
        if isinstance(added, threading.Thread):
          added.start()

  elif msg.topic == MQTT['topic']['subscribe']['delete']:
    for payload in payloads:
      try:
        target_devices = []
        with TUYA['lock']:
          if 'id' in payload:
            target_devices.append(payload['id'])
          elif 'name' in payload:
            for device_id in TUYA['device']['name'][payload['name']]:
              target_devices.append(device_id)
        for target_device in target_devices:
          with TUYA['lock']:
            if target_device in TUYA['device']['id']:
              if 'thread' in TUYA['device']['id'][target_device]:
                TUYA['device']['id'][target_device]['running'] = False
              else:
                try:
                  TUYA['device']['id'][TUYA['device']['id'][target_device]['parent']]['children'].remove(target_device)
                except:
                  pass
                _cleanup_device(target_device)
          logging('info', 'Device deleted {}'.format(target_device))
      except Exception as e:
        logging('error', traceback.format_exc())

  elif msg.topic == MQTT['topic']['subscribe']['set']:
    for payload in payloads:
      try:
        target_devices = []
        with TUYA['lock']:
          if 'id' in payload:
            target_devices.append(TUYA['device']['id'][payload['id']]['device'])
          elif 'name' in payload:
            for device_id in TUYA['device']['name'][payload['name']]:
              target_devices.append(TUYA['device']['id'][device_id]['device'])
        for target_device in target_devices:
          target_device.set_multiple_values(payload['data'], nowait = True)
        logging('info', 'Data set')
      except Exception as e:
        logging('error', traceback.format_exc())

  elif msg.topic == MQTT['topic']['subscribe']['get']:
    for payload in payloads:
      try:
        target_devices = []
        with TUYA['lock']:
          if 'id' in payload:
            target_devices.append(TUYA['device']['id'][payload['id']]['device'])
          elif 'name' in payload:
            for device_id in TUYA['device']['name'][payload['name']]:
              target_devices.append(TUYA['device']['id'][device_id]['device'])
        for target_device in target_devices:
          target_device.status(nowait = True)
        logging('info', 'Status requested')
      except Exception as e:
        logging('error', traceback.format_exc())

  elif msg.topic == MQTT['topic']['subscribe']['send']:
    for payload in payloads:
      try:
        if 'command' in payload and 'data' in payload:
          target_devices = []
          with TUYA['lock']:
            if 'id' in payload:
              target_devices.append(TUYA['device']['id'][payload['id']]['device'])
            elif 'name' in payload:
              for device_id in TUYA['device']['name'][payload['name']]:
                target_devices.append(TUYA['device']['id'][device_id]['device'])
          for target_device in target_devices:
            target_payload = target_device.generate_payload(payload['command'], payload['data'])
            target_device.send(target_payload)
          logging('info', 'Payload sent')
      except Exception as e:
        logging('error', traceback.format_exc())

  elif msg.topic == MQTT['topic']['subscribe']['query']:
    for payload in payloads:
      if 'stop' in payload or 'reset' in payload:
        with TUYA['lock']:
          for device_id in list(TUYA['device']['id'].keys()):
            device_info = TUYA['device']['id'].get(device_id, {})
            if 'running' in device_info:
              device_info['running'] = False
            else:
              _cleanup_device(device_id)
        threading.Thread(target = Terminator, args = (True if 'stop' in payload else False,)).start()
      elif 'status' in payload:
        daemon_status = {
          'tuya2mqtt': DAEMON_STAT['version'],
          'uptime(min)': int((datetime.now() - DAEMON_STAT['start_time']).total_seconds() / 60),
          'mqtt_broker': MQTT['broker']['host'],
          'connected_devices_count': len(TUYA['device']['id']),
          'devices': []
        }
        with TUYA['lock']:
          for device_id, device_info in TUYA['device']['id'].items():
            device_entry = device_info['payload'].copy()
            if 'thread' in device_info:
              children_count = len(device_info.get('children', []))
              if children_count == 0:
                device_entry['type'] = 'end device'
              else:
                device_entry['type'] = 'hub device'
                device_entry['children_count'] = children_count
            else:
              device_entry['type'] = 'sub device'
            daemon_status['devices'].append(device_entry)
        MQTT['client'].publish(topic = MQTT['topic']['publish']['daemon'], payload = json.dumps(daemon_status, ensure_ascii = False))
        logging('info', 'Daemon status requested and published.')


def Terminator(quit):
  global MQTT, TUYA
  logging('info', 'Process stop requested')
  threads_to_join = []
  with TUYA['lock']:
    for device_id in list(TUYA['device']['id'].keys()):
      device_info = TUYA['device']['id'].get(device_id, {})
      if 'thread' in device_info:
        threads_to_join.append(device_info['thread'])
  for thread in threads_to_join:
    thread.join()
  if quit:
    if MQTT['client']:
      logging('info', 'Process exited')
      MQTT['client'].disconnect()
      MQTT['client'].loop_stop()
  else:
    logging('info', 'Connection reset')


# CALLER MUST LOCK THE TUYA
def _cleanup_device(device_id):
  global TUYA
  if device_id in TUYA['device']['id']:
    device_info = TUYA['device']['id'][device_id]
    if 'name' in device_info and device_info['name'] in TUYA['device']['name']:
      name_list = TUYA['device']['name'][device_info['name']]
      if device_id in name_list:
        name_list.remove(device_id)
      if not name_list:
        del TUYA['device']['name'][device_info['name']]
    del TUYA['device']['id'][device_id]


def TuyaReceiver(payload):
  global MQTT, TUYA
  device = None

  try:
    device = tinytuya.Device(dev_id = payload['id'], address = payload['ip'], local_key = payload['key'], version = payload['version'], persist = True)

    if 'disabledetect' in payload:
      device.disabledetect = True

    with TUYA['lock']:
      TUYA['device']['id'][payload['id']]['device'] = device

    heartbeat_counter = 0
    while True:

      with TUYA['lock']:
        if not TUYA['device']['id'][payload['id']]['running']:
          break

      if heartbeat_counter == 2:
        # Heartbeat
        device.heartbeat(nowait = True)
        heartbeat_counter = 0

      # Receiving DPS
      data = device.receive()

      if data == None:
        heartbeat_counter = 2

      else:
        heartbeat_counter = heartbeat_counter + 1

        if 'Error' in data:
          # Connection error
          logging('error', json.dumps({'id': payload['id'], **data}))

          # TinyTuya does automatically reconnect.
          # Devices can sometimes take a while to re-connect to the WiFi, so if you get that error you can just wait a bit and retry the send/receive.
          time.sleep(4)
          continue

        else:
          if 'dps' in data:
            cid = payload['id']
            name = None
            topic = MQTT['topic']['publish']['command']
            if 'data' in data:
              if 'cid' in data['data']:
                cid = data['data']['cid']
            else:
              topic = MQTT['topic']['publish']['status']
              if 'cid' in data:
                cid = data['cid']
            with TUYA['lock']:
              if cid in TUYA['device']['id']:
                if 'name' in TUYA['device']['id'][cid]:
                  name = TUYA['device']['id'][cid]['name']
            MQTT['client'].publish(topic = topic, payload = json.dumps({'id': cid, 'name': name, 'data': data['dps']}, ensure_ascii = False))

    logging('info', 'Thread exited: {}'.format(payload['id']))

  except Exception as e:
    logging('error', 'Thread terminated with exception: {}'.format(traceback.format_exc()))

  finally:
    if device:
      with TUYA['lock']:
        if 'id' in payload:
          if payload['id'] in TUYA['device']['id']:
            for child_id in list(TUYA['device']['id'][payload['id']]['children']):
              _cleanup_device(child_id)
          _cleanup_device(payload['id'])
      device.close()


def start():
  global MQTT

  # INIT
  ReloadConf()

  # SIGTERM HANDLING
  def sigterm_handler(signum, frame):
    threading.Thread(target = Terminator, args = (True,)).start()
  signal.signal(signal.SIGTERM, sigterm_handler)

  # Connect MQTT broker
  MQTT['client'] = mqtt.Client(CallbackAPIVersion.VERSION2)
  MQTT['client'].on_connect = OnConnectMqtt
  MQTT['client'].on_message = RecvTopic
  if MQTT['login'].get('username', '') != '' and MQTT['login'].get('password', '') != '':
    MQTT['client'].username_pw_set(MQTT['login']['username'], MQTT['login']['password'])
  try:
    MQTT['client'].connect(**MQTT['broker'], keepalive = 60)
    MQTT['client'].loop_forever()
  except:
    logging('error', 'Fail to connnect to MQTT broker')


def main():
  global CONF_FILE, FOREGROUND

  # INCREASE FD
  soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
  if soft < hard:
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))

  # INIT
  WORK_FOLDER = os.path.dirname(os.path.realpath(__file__))
  FILE_NAME = os.path.splitext(os.path.basename(__file__))
  PID_FILE = '{}/{}.pid'.format(WORK_FOLDER, FILE_NAME[0])
  CONF_FILE = '{}/{}.conf'.format(WORK_FOLDER, FILE_NAME[0])

  LAUNCH_MODE = 0
  if len(sys.argv) >= 2:

    if sys.argv[1].lower() == 'start':
      LAUNCH_MODE = 0

    elif sys.argv[1].lower() == 'stop':
      LAUNCH_MODE = 1

    elif sys.argv[1].lower() == 'restart':
      LAUNCH_MODE = 2

    elif sys.argv[1].lower() in ('debug', 'foreground'):
      if sys.argv[1].lower() == 'debug':
        tinytuya.set_debug(True)
      FOREGROUND = True
      start()
      exit()

  # try cleaning up pid
  try:
    pid_file = daemon.pidfile.TimeoutPIDLockFile(PID_FILE)

    if pid_file.is_locked():
      pid = pid_file.read_pid()
      os.kill(pid, 0)

      # Already running
      if LAUNCH_MODE == 0:
        print('already running')
        exit(1)

      else:
        print('stopping previous instance')
        os.kill(pid, signal.SIGTERM)

        for _ in range(10):
          time.sleep(1)
          os.kill(pid, 0)

        os.kill(pid, signal.SIGKILL)

  except Exception as e:
    pass

  if os.path.exists(PID_FILE):
    os.remove(PID_FILE)

  if LAUNCH_MODE == 0 or LAUNCH_MODE == 2:
    print('launching new instance')
    with daemon.DaemonContext(
      working_directory = WORK_FOLDER,
      umask = 0o002,
      pidfile = pidfile.TimeoutPIDLockFile(PID_FILE)
      ) as context:
      start()


if __name__ == "__main__":
  main()
