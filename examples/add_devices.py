import json
import time
from paho.mqtt import publish

MQTT = json.load(open('./tuya2mqtt.conf'))

# Load the devices.json file created by the tinytuya wizard
with open('./devices.json', 'r') as f:
    devices = json.load(f)

# Register devices.
for device in devices:
    if MQTT['login']['username'] != '':
        publish.single(MQTT['topic']['subscribe']['add'], json.dumps(device), hostname = MQTT['broker']['host'], port = MQTT['broker']['port'], auth = MQTT['login'])
    else:
        publish.single(MQTT['topic']['subscribe']['add'], json.dumps(device), hostname = MQTT['broker']['host'], port = MQTT['broker']['port'])

print("All devices from devices.json have been sent to tuya2mqtt.")
