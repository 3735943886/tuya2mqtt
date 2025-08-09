import json
import time
from paho.mqtt import publish

# Load the devices.json file created by the tinytuya wizard
with open('./devices.json', 'r') as f:
    devices = json.load(f)

# 1. Register Wi-Fi devices (including hubs) first.
for device in devices:
    if 'node_id' not in device or device['node_id'] is '':
        publish.single('tuya2mqtt/device/add', json.dumps(device), hostname = 'localhost')

# Wait for a few seconds to allow the hub to initialize.
time.sleep(5)

# 2. Register sub-devices (Zigbee/BLE) next.
for device in devices:
    if 'node_id' in device and device['node_id'] is not '':
        publish.single('tuya2mqtt/device/add', json.dumps(device), hostname = 'localhost')

print("All devices from devices.json have been sent to tuya2mqtt.")
