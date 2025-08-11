import json
from typing import Any, Dict, Optional, Tuple

# --- Constants ---
BASE_TOPIC = 'homeassistant'
STATE_TOPIC_BASE = f'{BASE_TOPIC}/tuya2mqtt/state'
COMMAND_TOPIC_BASE = f'{BASE_TOPIC}/tuya2mqtt/command'
ADD_TOPIC = 'tuya2mqtt/device/add'
DEL_TOPIC = 'tuya2mqtt/device/delete'
GET_TOPIC = 'tuya2mqtt/device/get'
SET_TOPIC = 'tuya2mqtt/device/set'
MANUFACTURER = 'Tuya2MQTT (3735943886)'
EXCLUDED_CATEGORIES = None

# --- Device Customizations ---
CUSTOMIZATIONS: Dict[str, Dict[str, Any]] = {
    'e833v6jexwfkjrij': {  # PRESENCE SENSOR
        '101': {
            "code": "distance", "type": "Integer",
            "values": {"unit": "cm", "min": 0, "max": 1000, "scale": 1, "step": 1}
        },
        '102': {
            "code": "illuminance", "type": "Integer",
            "values": {"unit": "lx", "min": 0, "max": 10000, "scale": 0, "step": 1}
        }
    },
    '5rta89nj': {  # PUSHER
        '104': {
            "code": "percent_control", "type": "Integer",
            "values": {"unit": "%", "min": 0, "max": 100, "step": 1}
        }
    }
}

# --- Topic ---
TUYA2MQTT_TOPIC = {
    'listener': {
        'trigger': 'tuya2mqtt/data/#',
        'publish': STATE_TOPIC_BASE + '/{}_{}',
    },
    'translater': {
        'trigger': f'{COMMAND_TOPIC_BASE}/#',
        'publish': SET_TOPIC,
    },
}

# --- Helper Functions for Processing Mappings ---
def _get_ha_unit(raw_unit: str) -> str:
    """Cleans and maps raw units to Home Assistant standards."""
    unit_map = {'w': 'W', 'kwh': 'kWh', 'kw': 'kW', 'v': 'V', 'ma': 'mA', 'a': 'A'}
    cleaned_unit = ''.join(filter(str.isalpha, raw_unit)).lower()
    return unit_map.get(cleaned_unit, raw_unit)

def _handle_boolean(mapping: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Handles 'Boolean' type mappings."""
    mappingcode = mapping['code']
    options = {'payload_on': True, 'payload_off': False}
    if 'state' in mappingcode:
        devicetype = 'binary_sensor'
        if 'door' in mappingcode:
            options['device_class'] = 'door'
    else:
        devicetype = 'switch'
    return devicetype, options

def _handle_enum(mapping: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Handles 'Enum' type mappings."""
    mappingcode = mapping['code']
    options = {}
    if 'switch_mode' in mappingcode:
        devicetype = 'event'
        # Tuya scene buttons often have incorrect ENUM ranges.
        options['event_types'] = ['single_click', 'double_click', 'long_press']
    elif 'control' in mappingcode:
        devicetype = 'select'
        options['options'] = mapping['values']['range']
    else:
        devicetype = 'sensor'
    return devicetype, options

def _handle_integer(mapping: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Handles 'Integer' type mappings."""
    mappingcode = mapping['code']
    values = mapping['values']
    devicetype = 'number'  # Default for Integer
    options = {
        'min': values.get('min'),
        'max': values.get('max'),
        'step': values.get('step'),
    }

    # Sensor-specific overrides
    if mappingcode == 'add_ele':
        devicetype, options['device_class'] = 'sensor', 'energy'
    elif mappingcode == 'cur_current':
        devicetype, options['device_class'] = 'sensor', 'current'
    elif mappingcode == 'cur_power':
        devicetype, options['device_class'] = 'sensor', 'power'
        if values.get('max') > 10000:
            options['value_template'] = '{{ (value | float / 10) | round(1) }}'
            options['step'] = 0.1
    elif mappingcode == 'cur_voltage':
        devicetype, options['device_class'] = 'sensor', 'voltage'
        if values.get('max') > 1000:
            options['value_template'] = '{{ (value | float / 10) | round(1) }}'
            options['step'] = 0.1
    elif 'battery' in mappingcode or 'residual_electricity' in mappingcode:
        devicetype, options['device_class'] = 'sensor', 'battery'
        options['unit_of_measurement'] = '%'
    elif 'temperature' in mappingcode:
        devicetype, options['device_class'] = 'sensor', 'temperature'
        options['unit_of_measurement'] = '°C'
        if values.get('max') > 100:
            options['value_template'] = '{{ (value | float / 10) | round(1) }}'
            options['step'] = 0.1
    elif 'humidity' in mappingcode:
        devicetype, options['device_class'] = 'sensor', 'humidity'
        options['unit_of_measurement'] = '%'
        if values.get('max') > 100:
            options['value_template'] = '{{ (value | float / 10) | round(1) }}'
            options['step'] = 0.1
    elif 'countdown' in mappingcode:
        options['device_class'] = 'duration'
    elif 'distance' in mappingcode:
        devicetype, options['device_class'] = 'sensor', 'distance'
    elif 'illuminance' in mappingcode:
        devicetype, options['device_class'] = 'sensor', 'illuminance'
        options['unit_of_measurement'] = 'lx'
    elif 'value' in mappingcode or 'state' in mappingcode:
        devicetype = 'sensor'

    if 'unit' in values:
        if 'unit_of_measurement' not in options:
            options['unit_of_measurement'] = _get_ha_unit(values['unit'])
    # Remove None values from options
    return devicetype, {k: v for k, v in options.items() if v is not None}

# --- Core Functions ---
def customize_device(device: Dict[str, Any]) -> None:
    """Applies device-specific mappings from the CUSTOMIZATIONS dictionary."""
    product_id = device.get('product_id')
    if product_id in CUSTOMIZATIONS:
        device['mapping'].update(CUSTOMIZATIONS[product_id])

def set_device(device: Dict[str, Any], topic_root: str = BASE_TOPIC, add: bool = True) -> None:
    """
    Generates and publishes Home Assistant MQTT discovery configurations for a device.
    """
    mapping_handlers = {
        'Boolean': _handle_boolean,
        'Enum': _handle_enum,
        'Integer': _handle_integer,
    }

    is_sub_device = True if 'parent' in device and 'node_id' in device else False
    device_id = device['node_id'] if is_sub_device else device['id']

    base_payload = {
        'device': {
            'identifiers': [device_id],
            'name': device['name'],
            'manufacturer': MANUFACTURER,
            'model': device.get('model') or device.get('product_name'),
            'sw_version': device['version'],
        },
    }
    if device.get('mac'):
        base_payload['device']['connections'] = [['mac', device['mac']]]
    if device.get('ip'):
        base_payload['device'].setdefault('connections', []).append(['ip', device['ip']])

    for mapping_key, mapping_data in device.get('mapping', {}).items():
        mapping_type = mapping_data.get('type')
        handler = mapping_handlers.get(mapping_type)

        if not handler:
            continue

        devicetype, options = handler(mapping_data)
        if not devicetype or 'test' in mapping_data['code']:
            continue

        unique_id_suffix = f"{device_id}_{mapping_key}"

        payload = {
            **base_payload,
            'name': mapping_data['code'],
            'unique_id': unique_id_suffix,
            'object_id': f"{device['name']}_{mapping_data['code']}",
            'state_topic': f"{STATE_TOPIC_BASE}/{unique_id_suffix}",
        }

        if 'sensor' not in devicetype and 'event' not in devicetype:
            payload['command_topic'] = f"{COMMAND_TOPIC_BASE}/{unique_id_suffix}"

        # Merge the generated options and publish
        full_payload = {**payload, **options}
        config_topic = f"{topic_root}/{devicetype}/{unique_id_suffix}/config"

        if add:
            mqtt.publish(topic=config_topic, payload=json.dumps(full_payload), retain=True)
        else:
            mqtt.publish(topic=config_topic, retain=True)


@service
def tuya2mqtt_ha_device_manager(add=True, devices_file='/config/pyscript/devices.json', excluded_categories={'wg2', 'zjq', 'jzq'}):
    """yaml
name: Manage devices on Tuya2MQTT and Home Assistant
description: Read devices.json and manage registration of devices on Tuya2MQTT and Home Assistant.
fields:
  add:
    example: true
    description: true to add device, false to delete device
  devices_file:
    example: /config/pyscript/devices.json
    description: devices.json file created by tinytuya wizard
  excluded_categories:
    example: [wg2, zjq, jzq]
    description: categories not to be added to home assistant (gateway, repeater, etc.)
"""

    global EXCLUDED_CATEGORIES

    devices = device_open(devices_file)
    if 'error' in devices:
        log.error(devices['error'])
        return

    EXCLUDED_CATEGORIES = excluded_categories
    for subdev in [False, True]:
        # Process all devices in a single loop, handling both main and sub-devices.
        for device in devices:

            is_sub_device = True if 'node_id' in device and 'parent' in device else False
            if is_sub_device == subdev:
                device_id = device['node_id'] if is_sub_device else device['id']
                add_payload = {}

                # The original code sent 'disabledetect' only for non-sub devices.
                if not is_sub_device:
                    add_payload['disabledetect'] = 1

                if add:
                    mqtt.publish(topic=ADD_TOPIC, payload=json.dumps({**device, **add_payload}))
                else:
                    mqtt.publish(topic=DEL_TOPIC, payload=json.dumps({'id': device_id}))

                if device.get('category') not in EXCLUDED_CATEGORIES:
                    customize_device(device)
                    set_device(device=device, add=add)

                    if add:
                        # Request updated state for the device/node.
                        mqtt.publish(topic=GET_TOPIC, payload=json.dumps({'id': device_id}))

        task.sleep(5)


@pyscript_executor
def device_open(file):
    try:
        with open(file, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {'error': e}


@mqtt_trigger(TUYA2MQTT_TOPIC['listener']['trigger'])
def tuya2mqtt_listener(**kwargs):
    global TUYA2MQTT_TOPIC
    if 'topic' in kwargs and 'payload_obj' in kwargs:
        if 'command' in kwargs['topic'] or 'status' in kwargs['topic']:
            if 'data' in kwargs['payload_obj']:
                for data in kwargs['payload_obj']['data']:
                    payload = kwargs['payload_obj']['data'][data]
                    if 'command' in kwargs['topic']:
                        event.fire('tuya2mqtt_command_event', id = kwargs['payload_obj']['id'], name = kwargs['payload_obj']['name'], key = data, value = payload)
                        if payload == 'remote_control':
                            # SET BACK TO WIRELESS_SWITCH MODE IN CASE OF SWICHING TO REMOTE_CONTROL MODE
                            mqtt.publish(topic = TUYA2MQTT_TOPIC['translater']['publish'], payload = json.dumps([{'id': kwargs['payload_obj']['id'], 'data': {data: 'wireless_switch'}}]))
                        if payload in ('single_click', 'double_click', 'long_press'):
                            # MUST BE SET AS EVENT_TYPE FOR TUYA SCENE BUTTONS
                            payload = json.dumps({ 'event_type': payload })
                    else:
                        if payload in ('single_click', 'double_click', 'long_press'):
                            # IGNORE BUTTON EVENT IF STATUS TOPIC RECEIVED
                            continue
                    mqtt.publish(topic = TUYA2MQTT_TOPIC['listener']['publish'].format(kwargs['payload_obj']['id'], data), payload = payload, retain = True)


@mqtt_trigger(TUYA2MQTT_TOPIC['translater']['trigger'])
def tuya2mqtt_translater(**kwargs):
    global TUYA2MQTT_TOPIC
    if 'topic' in kwargs:
        items = kwargs['topic'].split('/')
        ids = items[3].split('_')
        payload = _auto_type_convert(kwargs['payload'])
        mqtt.publish(topic = TUYA2MQTT_TOPIC['translater']['publish'], payload = json.dumps([{'id': ids[0], 'data': {ids[1]: payload}}]))


@pyscript_compile
def _auto_type_convert(value_str):
    if isinstance(value_str, str):
        lower_str = value_str.lower()
        if lower_str == 'true':
            return True
        if lower_str == 'false':
            return False
    try:
        return int(value_str)
    except ValueError:
        try:
            return float(value_str)
        except ValueError:
            pass
    return value_str
