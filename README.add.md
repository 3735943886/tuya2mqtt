### The Easiest Way to Add Multiple Tuya Devices at Once

This method uses the `tinytuya` wizard to automatically discover devices on the local network. The resulting `devices.json` file is then processed to register them with `tuya2mqtt`.

#### 1\. Generate the `devices.json` file

First, run the `tinytuya` wizard from the terminal to create a `devices.json` file containing device information.

```sh
python -m tinytuya wizard
```

When the wizard asks questions, answer `yes` or `y` to proceed with the following options:

  * `Download DP Name mappings?`
  * `Poll local devices?`

Once completed, a `devices.json` file will be created in the current directory. This file contains all discovered Tuya Wi-Fi devices and any Zigbee/BLE sub-devices connected to a hub.

#### 2\. Add devices from `devices.json` in bulk

The `devices.json` file stores the device list in JSON format. To register these devices with `tuya2mqtt`, it is necessary to publish each device's information from the file to the `tuya2mqtt/device/add` topic.

It is crucial to **add the Wi-Fi devices first** so that the parent gateways are registered, followed by the sub-devices (Zigbee/BLE).

The following Python script can be used to automate this process.

```python
import json
import time
from paho.mqtt import publish

# Load the devices.json file created by the tinytuya wizard
with open('./devices.json', 'r') as f:
    devices = json.load(f)

# 1. Register Wi-Fi devices (including hubs) first.
for device in devices:
    if 'node_id' not in device or device['node_id'] == '':
        publish.single('tuya2mqtt/device/add', json.dumps(device), hostname = 'localhost')

# Wait for a few seconds to allow the hub to initialize.
time.sleep(5)

# 2. Register sub-devices (Zigbee/BLE) next.
for device in devices:
    if 'node_id' in device and device['node_id'] != '':
        publish.single('tuya2mqtt/device/add', json.dumps(device), hostname = 'localhost')

print("All devices from devices.json have been sent to tuya2mqtt.")
```

#### 3\. Monitor and control devices

Now that the devices are registered, they can be monitored and controlled via MQTT.

  * **Monitor Device Status**: Subscribe to the `tuya2mqtt/data/command` and `tuya2mqtt/data/status` topics to receive real-time updates.
  * **Control Devices**: The `tuya2mqtt/device/set` and `tuya2mqtt/device/get` topics can be used to send commands or request the current status of a specific device.
