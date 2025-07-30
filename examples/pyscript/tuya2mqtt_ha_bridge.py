"""
Pyscript Module: An integrated bridge and device manager between Tuya devices and Home Assistant.

This script performs two main functions:
1.  Real-time Bridge (MQTT Triggers):
    - Listens for data (status/events) from the tuya2mqtt daemon and publishes it to Home Assistant state topics.
    - Listens for commands from Home Assistant, translates them into the format expected by the tuya2mqtt daemon, and sends them.

2.  Device Manager (Service Call):
    - Provides a service to read a devices.json file and automatically register or delete Tuya devices via Home Assistant MQTT Discovery.
    - Supports custom device mappings and attribute conversions.
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from tuya2mqtt_customization import CUSTOMDEVICE_MAP, SENSOR_MAP, UNIT_MAP, UNIT_OVERRIDES

# ==============================================================================
# --- 1. Unified Constants & Configuration ---
# ==============================================================================

# --- Base Topics ---
HA_BASE_TOPIC = "homeassistant"
T2M_BASE_TOPIC = "tuya2mqtt"

# --- Tuya2MQTT Daemon Topics ---
T2M_DATA_TRIGGER_TOPIC = f"{T2M_BASE_TOPIC}/data/#"
T2M_DEVICE_SET_TOPIC = f"{T2M_BASE_TOPIC}/device/set"
T2M_DEVICE_ADD_TOPIC = f"{T2M_BASE_TOPIC}/device/add"
T2M_DEVICE_DEL_TOPIC = f"{T2M_BASE_TOPIC}/device/delete"
T2M_DEVICE_GET_TOPIC = f"{T2M_BASE_TOPIC}/device/get"

# --- Home Assistant Topics ---
HA_STATE_TOPIC_TEMPLATE = f"{HA_BASE_TOPIC}/tuya2mqtt/state/{{}}_{{}}"
HA_COMMAND_TOPIC_TEMPLATE = f"{HA_BASE_TOPIC}/tuya2mqtt/command/{{}}_{{}}"
HA_DISCOVERY_TOPIC_TEMPLATE = f"{HA_BASE_TOPIC}/{{}}/{{}}_{{}}/config"

# --- Common Keys & Values ---
KEY_ID = "id"
KEY_DATA = "data"
KEY_NAME = "name"
KEY_TOPIC = "topic"
KEY_PAYLOAD = "payload"
KEY_PAYLOAD_OBJ = "payload_obj"
TOPIC_ID_COMMAND = "command"
BUTTON_EVENTS = {"single_click", "double_click", "long_press"}
EVENT_TYPE_KEY = "event_type"
MODE_REMOTE_CONTROL = "remote_control"
MODE_WIRELESS_SWITCH = "wireless_switch"

# --- Device Manager Settings ---
MANUFACTURER = "Tuya2MQTT (3735943886)"


# ==============================================================================
# --- 2. Helper Functions ---
# ==============================================================================

def _auto_type_convert(value: str) -> Any:
    """Automatically converts a string to its likely type (bool, int, float, or str)."""
    if not isinstance(value, str):
        return value
    lower_value = value.lower()
    if lower_value == 'true': return True
    if lower_value == 'false': return False
    try: return int(value)
    except ValueError:
        try: return float(value)
        except ValueError: return value

def _get_ha_unit(raw_unit: str) -> str:
    """Cleans and maps raw units to Home Assistant standards."""
    cleaned_unit = ''.join(filter(str.isalpha, raw_unit)).lower()
    return UNIT_MAP.get(cleaned_unit, raw_unit)

# --- Handlers for DP Types ---

def _handle_boolean(mapping: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    """Handles 'Boolean' type DPs and returns the HA entity type and options."""
    code = mapping['code']
    options = {'payload_on': True, 'payload_off': False}
    if 'state' in code:
        dev_type = 'binary_sensor'
        if 'door' in code: options['device_class'] = 'door'
    else:
        dev_type = 'switch'
    return dev_type, options

def _handle_enum(mapping: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    """Handles 'Enum' type DPs."""
    code = mapping['code']
    if 'switch_mode' in code:
        # Scene switches are typically handled as 'event' type.
        return 'event', {'event_types': list(BUTTON_EVENTS)}
    if 'control' in code:
        return 'select', {'options': mapping['values']['range']}
    return 'sensor', {}

def _handle_integer(mapping: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    """Handles 'Integer' type DPs."""
    code = mapping['code']
    values = mapping['values']
    dev_type = 'number'  # Default type
    options = {'min': values.get('min'), 'max': values.get('max'), 'step': values.get('step')}

    # Override to sensor type and set device_class based on specific codes.
    for key, (dev_class, scale_threshold, scale_factor) in SENSOR_MAP.items():
        if key in code:
            dev_type = 'sensor'
            options['device_class'] = dev_class
            if dev_class in UNIT_OVERRIDES:
                options['unit_of_measurement'] = UNIT_OVERRIDES[dev_class]
            if scale_threshold and values.get('max', 0) > scale_threshold:
                options['value_template'] = f'{{{{ (value | float / {scale_factor}) | round(1) }}}}'
                if options.get('step') == 1: options['step'] = 1.0 / scale_factor
            break

    if 'countdown' in code:
        options['device_class'] = 'duration'
    elif 'value' in code or 'state' in code and dev_type == 'number':
        dev_type = 'sensor'

    if 'unit' in values and 'unit_of_measurement' not in options:
        options['unit_of_measurement'] = _get_ha_unit(values['unit'])

    return dev_type, {k: v for k, v in options.items() if v is not None}


# ==============================================================================
# --- 3. Core Logic Functions ---
# ==============================================================================

def _process_device_data(topic: str, payload_obj: Dict[str, Any]) -> None:
    """Processes a single data payload from the tuya2mqtt daemon."""
    device_id = payload_obj.get(KEY_ID)
    device_name = payload_obj.get(KEY_NAME)
    device_data = payload_obj.get(KEY_DATA)

    if not all([device_id, device_data]):
        log.warning(f"Ignoring incomplete message on topic {topic}: {payload_obj}")
        return

    is_command_topic = TOPIC_ID_COMMAND in topic

    for dp_key, dp_value in device_data.items():
        publish_payload = dp_value

        if is_command_topic:
            event.fire('tuya2mqtt_command_event', id=device_id, name=device_name, key=dp_key, value=dp_value)
            if dp_value == MODE_REMOTE_CONTROL:
                set_payload = json.dumps([{KEY_ID: device_id, KEY_DATA: {dp_key: MODE_WIRELESS_SWITCH}}])
                mqtt.publish(topic=T2M_DEVICE_SET_TOPIC, payload=set_payload)
            if dp_value in BUTTON_EVENTS:
                publish_payload = json.dumps({EVENT_TYPE_KEY: dp_value})
        else:  # Status topic
            if dp_value in BUTTON_EVENTS:
                continue

        publish_topic = HA_STATE_TOPIC_TEMPLATE.format(device_id, dp_key)
        mqtt.publish(topic=publish_topic, payload=publish_payload, retain=True)

def _set_ha_discovery_config(device: Dict[str, Any], add: bool = True) -> None:
    """Publishes the HA MQTT Discovery configuration for a single device."""
    mapping_handlers = {'Boolean': _handle_boolean, 'Enum': _handle_enum, 'Integer': _handle_integer}

    is_sub_device = 'parent' in device and 'node_id' in device
    device_id = device['node_id'] if is_sub_device else device['id']
    device_name = device['name']

    device_info = {
        'identifiers': [device_id],
        'name': device_name,
        'manufacturer': MANUFACTURER,
        'model': device.get('model') or device.get('product_name'),
        'sw_version': device['version'],
    }
    connections = []
    if device.get('mac'): connections.append(['mac', device['mac']])
    if device.get('ip'): connections.append(['ip', device['ip']])
    if connections: device_info['connections'] = connections

    for dp_key, mapping in device.get('mapping', {}).items():
        handler = mapping_handlers.get(mapping.get('type'))
        if not handler or 'test' in mapping.get('code', ''):
            continue

        dev_type, options = handler(mapping)
        if not dev_type:
            continue

        unique_id = f"{device_id}_{dp_key}"
        payload = {
            'device': device_info,
            'name': mapping['code'],
            'unique_id': unique_id,
            'object_id': f"{device_name.lower().replace(' ', '_')}_{mapping['code']}",
            'state_topic': HA_STATE_TOPIC_TEMPLATE.format(device_id, dp_key),
            **options,
        }

        if dev_type not in ['sensor', 'binary_sensor', 'event']:
            payload['command_topic'] = HA_COMMAND_TOPIC_TEMPLATE.format(device_id, dp_key)

        config_topic = HA_DISCOVERY_TOPIC_TEMPLATE.format(dev_type, device_id, dp_key)

        # Publish the config payload if add=True, or an empty payload to delete.
        config_payload = json.dumps(payload) if add else ""
        mqtt.publish(topic=config_topic, payload=config_payload, retain=True)

@pyscript_executor
def _read_devices_file(file_path: str) -> List[Dict[str, Any]]:
    """Reads the device file and parses its JSON content."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        log.error(f"Device file not found: {file_path}")
    except json.JSONDecodeError:
        log.error(f"Error decoding JSON from file: {file_path}")
    return []


# ==============================================================================
# --- 4. Pyscript Triggers & Services ---
# ==============================================================================

@mqtt_trigger(T2M_DATA_TRIGGER_TOPIC)
def tuya_realtime_bridge_listener(**kwargs: Any) -> None:
    """
    Listens for all data from the tuya2mqtt daemon, processes it, and relays the state to HA.
    """
    topic = kwargs.get(KEY_TOPIC)
    payload_obj = kwargs.get(KEY_PAYLOAD_OBJ)
    if not topic or not payload_obj or KEY_DATA not in payload_obj:
        return
    _process_device_data(topic, payload_obj)

@mqtt_trigger(HA_COMMAND_TOPIC_TEMPLATE.replace('{}_{}', '#'))
def tuya_realtime_bridge_translater(**kwargs: Any) -> None:
    """
    Listens for commands from HA and translates them for the tuya2mqtt daemon.
    """
    topic = kwargs.get(KEY_TOPIC)
    payload_str = kwargs.get(KEY_PAYLOAD)
    if not topic: return

    try:
        topic_parts = topic.split('/')
        device_id, dp_key = topic_parts[-1].split('_')
    except (IndexError, ValueError) as e:
        log.error(f"Could not parse command topic '{topic}': {e}")
        return

    set_payload = json.dumps([{
        KEY_ID: device_id,
        KEY_DATA: {dp_key: _auto_type_convert(payload_str)}
    }])
    mqtt.publish(topic=T2M_DEVICE_SET_TOPIC, payload=set_payload)

@service
def tuya2mqtt_device_manager(add: bool = True, devices_file: str = '/config/pyscript/devices.json', excluded_categories: List[str] = ['wg2', 'zjq', 'jzq']):
    """yaml
name: Tuya2MQTT Device Manager
description: Reads a devices.json file to register or delete Tuya devices in Home Assistant.
fields:
  add:
    description: "Set to true to add devices, false to delete."
    example: true
  devices_file:
    description: "Path to the devices.json file generated by tinytuya wizard."
    example: "/config/pyscript/devices.json"
  excluded_categories:
    description: "Device categories to exclude from HA (e.g., gateways, repeaters)."
    example: ['wg2', 'zjq', 'jzq']
"""
    devices = _read_devices_file(devices_file)
    if not devices:
        return

    def process_device_registration(device_to_process, add_mode, excluded_cats):
        """Helper function to avoid code repetition."""
        # Apply custom DPs
        product_id = device_to_process.get('product_id')
        if product_id in CUSTOMDEVICE_MAP:
            device_to_process.setdefault('mapping', {}).update(CUSTOMDEVICE_MAP[product_id])

        is_sub = 'parent' in device_to_process and 'node_id' in device_to_process
        dev_id = device_to_process['node_id'] if is_sub else device_to_process['id']

        # Send add/delete request to the tuya2mqtt daemon
        if add_mode:
            add_payload = {'disabledetect': 1} if not is_sub else {}
            mqtt.publish(topic=T2M_DEVICE_ADD_TOPIC, payload=json.dumps({**device_to_process, **add_payload}))
        else:
            mqtt.publish(topic=T2M_DEVICE_DEL_TOPIC, payload=json.dumps({'id': dev_id}))

        # If not in excluded categories, publish HA Discovery config
        if device_to_process.get('category') not in excluded_cats:
            _set_ha_discovery_config(device=device_to_process, add=add_mode)

        # Request a state refresh (on add only)
        if add_mode:
            mqtt.publish(topic=T2M_DEVICE_GET_TOPIC, payload=json.dumps({'id': dev_id}))

    # 1. Process Parent Devices
    log.info("Processing parent devices...")
    for device in devices:
        is_sub_device = 'parent' in device and 'node_id' in device
        if not is_sub_device:
            process_device_registration(device, add, excluded_categories)

    # 2. Wait for 5 seconds before processing sub-devices
    log.info("Waiting 5 seconds before processing sub-devices...")
    task.sleep(5)

    # 3. Process Sub-devices
    log.info("Processing sub-devices...")
    for device in devices:
        is_sub_device = 'parent' in device and 'node_id' in device
        if is_sub_device:
            process_device_registration(device, add, excluded_categories)

    log.info(f"Tuya Device Manager: Finished {'adding' if add else 'deleting'} devices.")
