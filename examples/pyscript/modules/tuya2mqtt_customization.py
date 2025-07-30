# Device-specific custom DP (Data Point) mappings.
# If a device uses non-standard DPs, define them here.
CUSTOMDEVICE_MAP = {
    'e833v6jexwfkjrij': {  # PRESENCE SENSOR
        '101': {"code": "distance", "type": "Integer", "values": {"unit": "cm", "min": 0, "max": 1000, "step": 1}},
        '102': {"code": "illuminance", "type": "Integer", "values": {"unit": "lx", "min": 0, "max": 10000}},
    },
    '5rta89nj': {  # PUSHER
        '104': {"code": "percent_control", "type": "Integer", "values": {"unit": "%", "min": 0, "max": 100, "step": 1}},
    }
}

# Tuya DP to HA class mappings.
SENSOR_MAP = {
    'add_ele': ('energy', None, None),
    'cur_current': ('current', None, None),
    'cur_power': ('power', 10000, 10),
    'cur_voltage': ('voltage', 1000, 10),
    'battery': ('battery', None, None),
    'residual_electricity': ('battery', None, None),
    'temperature': ('temperature', 100, 10),
    'humidity': ('humidity', 100, 10),
    'distance': ('distance', None, None),
    'illuminance': ('illuminance', None, None),
}

# HA unit mappings.
UNIT_MAP = {'w': 'W', 'kwh': 'kWh', 'kw': 'kW', 'v': 'V', 'ma': 'mA', 'a': 'A'}
UNIT_OVERRIDES = {'battery': '%', 'temperature': '°C', 'humidity': '%', 'illuminance': 'lx'}
