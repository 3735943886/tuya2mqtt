# Device-specific custom DP (Data Point) mappings.
# If a device uses non-standard DPs, define them here.
CUSTOMIZATIONS: Dict[str, Dict[str, Any]] = {
    'e833v6jexwfkjrij': {  # PRESENCE SENSOR
        '101': {"code": "distance", "type": "Integer", "values": {"unit": "cm", "min": 0, "max": 1000, "step": 1}},
        '102': {"code": "illuminance", "type": "Integer", "values": {"unit": "lx", "min": 0, "max": 10000}},
    },
    '5rta89nj': {  # PUSHER
        '104': {"code": "percent_control", "type": "Integer", "values": {"unit": "%", "min": 0, "max": 100, "step": 1}},
    }
}
