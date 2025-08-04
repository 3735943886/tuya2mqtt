### Easiest Way to Add Multiple Tuya Devices at Once

This method uses the `tinytuya` wizard to automatically discover devices on your local network and then processes the resulting `devices.json` file to register them with `tuya2mqtt`.

#### 1\. Generate the `devices.json` file

First, run the `tinytuya` wizard from your terminal to create a `devices.json` file containing your device information.

```sh
python -m tinytuya wizard
```

When the wizard asks questions, answer `yes` or `y` to proceed with the following options:

  * `Download DP Name mappings?`
  * `Poll local devices?`

Once completed, a `devices.json` file will be created in your current directory. This file contains all the discovered Tuya Wi-Fi devices and any Zigbee/BLE sub-devices connected to a hub.

#### 2\. Add devices from `devices.json` in bulk

The `devices.json` file stores your device list in JSON format. To register these devices with `tuya2mqtt`, you need to publish each device's information from the file to the `tuya2mqtt/device/add` topic.

It is crucial to **add the Wi-Fi devices first** so that the parent gateways are registered, followed by the sub-devices (Zigbee/BLE).

You can use the following Python script to automate this process.

```python
import json
from paho.mqtt import publish

# Load the devices.json file created by the tinytuya wizard
with open('./devices.json', 'r') as f:
    devices = json.load(f)

# 1. Register Wi-Fi devices (including hubs) first.
for device in devices:
    if 'node_id' not in device and device['node_id'] is not '':
        publish.single('tuya2mqtt/device/add', json.dumps(device), hostname = 'localhost')

# 2. Register sub-devices (Zigbee/BLE) next.
for device in devices:
    if 'node_id' in device or device['node_id'] is '':
        publish.single('tuya2mqtt/device/add', json.dumps(device), hostname = 'localhost')

print("All devices from devices.json have been sent to tuya2mqtt.")
```

#### 3\. Monitor and control your devices

Now that your devices are registered, you can start monitoring and controlling them via MQTT.

  * **Monitor Device Status**: Subscribe to the `tuya2mqtt/data/command` and `tuya2mqtt/data/status` topics to receive real-time updates on your Tuya devices.
  * **Control Devices**: Use the `tuya2mqtt/device/set` and `tuya2mqtt/device/get` topics to send commands or request the current status of a specific device.
