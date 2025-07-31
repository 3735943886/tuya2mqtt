# tuya2mqtt: Tuya Devices to MQTT Bridge
[한국어](README.ko.md)

`tuya2mqtt` is a Python script that connects Tuya smart devices to an MQTT broker. **It acts as a backend service that maintains a 24-hour TCP connection with registered Tuya devices, instantly publishing state changes to MQTT and allowing you to control devices via MQTT commands.**

-----

## Key Features

  * **Tuya Device Control**: Set the state of your Tuya devices via MQTT.
  * **Status Monitoring**: Receive real-time status updates (DPS) from your Tuya devices and publish them to MQTT.
  * **Multithreading**: Each device is handled in a separate thread for stable, concurrent communication.
  * **Daemon Mode**: Runs reliably in the background, with PID file management to prevent multiple instances.
  * **Dynamic Device Management**: Add or remove devices on the fly using MQTT commands.
  * **Wide Device Support**: Control various Tuya devices, including Wi-Fi, Zigbee, and BLE.

-----

## Installation

`tuya2mqtt` requires Python 3.8 or later and the following libraries.

```sh
pip install tinytuya paho-mqtt python-daemon
```

-----

## Usage

The `tuya2mqtt.py` script supports four command modes.

### Start as a Daemon

Run the script as a background daemon. It will continue running even after you close the terminal.

```sh
python tuya2mqtt.py start
```

### Stop the Daemon

Safely stop the running daemon.

```sh
python tuya2mqtt.py stop
```

### Restart the Daemon

Stop the current daemon instance and start a new one.

```sh
python tuya2mqtt.py restart
```

### Run in Debug Mode

Run the script in the foreground with real-time logs printed to the terminal. This is useful for development and troubleshooting.

```sh
python tuya2mqtt.py debug
```

-----

## Configuration

When you run the script for the first time, a `tuya2mqtt.conf` file will be created automatically. You can edit this file to change your MQTT broker information.

**`tuya2mqtt.conf` Example:**

```json
{
  "broker": {
    "host": "localhost",
    "port": 1883
  },
  "topic": {
    "subscribe": {
      "add": "tuya2mqtt/device/add",
      "delete": "tuya2mqtt/device/delete",
      "query": "tuya2mqtt/device/query",
      "set": "tuya2mqtt/device/set",
      "get": "tuya2mqtt/device/get",
      "send": "tuya2mqtt/device/send"
    },
    "publish": {
      "command": "tuya2mqtt/data/command",
      "status": "tuya2mqtt/data/status",
      "daemon": "tuya2mqtt/log/daemon",
      "info": "tuya2mqtt/log/info",
      "error": "tuya2mqtt/log/error"
    }
  }
}
```

-----

## Managing Devices via MQTT

### 1\. Add a Device

[Add devices in bulk via tinytuya wizard](README.add.md)

To add a new Tuya device, publish a JSON payload to the `tuya2mqtt/device/add` topic.

  * **Tuya Wi-Fi Device**: Requires `id`, `ip`, `key`, and `version`. `name` is optional.
    ```json
    {
      "id": "ebed836691xxxxxxb",
      "ip": "192.168.1.100",
      "key": "b4e4776e1f0e21a2",
      "version": 3.3,
      "name": "My_Smart_Plug"
    }
    ```
  * **Tuya Zigbee/BLE Device**: Requires `id`, `node_id`, and `parent` (the hub's ID). `name` is optional.
    ```json
    {
      "id": "ebed836691xxxxxxb",
      "node_id": "01020202111111112222",
      "parent": "ebed836691xxxxxxb",
      "name": "My_Sub_Device"
    }
    ```

### 2\. Control and Query Device Status

To control a device or request its status, publish a payload to the `tuya2mqtt/device/set` or `tuya2mqtt/device/get` topics. **You can specify a device using either its `id` or `name`.**

  * **Set State**: Publish a JSON payload with `data` (DPS) to the `tuya2mqtt/device/set` topic.
      * Using `id`:
        ```json
        {
          "id": "ebed836691xxxxxxb",
          "data": {
            "1": true
          }
        }
        ```
      * Using `name`:
        ```json
        {
          "name": "My_Smart_Plug",
          "data": {
            "1": true
          }
        }
        ```
  * **Get Status**: Publish a JSON payload containing the device's `id` or `name` to the `tuya2mqtt/device/get` topic.
      * Using `id`:
        ```json
        {
          "id": "ebed836691xxxxxxb"
        }
        ```
      * Using `name`:
        ```json
        {
          "name": "My_Smart_Plug"
        }
        ```
  * **Direct Payload Transmission (Advanced Usage)**: To send a direct command to a device, publish a payload containing the command code (`command`, an integer) and data to the `tuya2mqtt/device/send` topic. This is useful for features not covered by the `set` command.
      * Using `id`:
        ```json
        {
          "id": "ebed836691xxxxxxb",
          "command": 18,
          "data": [1, 2, 3]
        }
        ```
### 3\. Monitor Devices via MQTT

`tuya2mqtt` provides two main topics for device monitoring. By subscribing to these topics, you can get real-time updates on device status changes and command history.

 * `tuya2mqtt/data/command`: This topic is published when a **Tuya device reports a state change on its own.** For example, it publishes instantly when a smart button is pressed or a switch is physically toggled on the device itself.

 * `tuya2mqtt/data/status`: This topic is published as a **response to a command or for periodic status reports.** It's used when you request a status update via the tuya2mqtt/device/get topic or when the script periodically polls the device for its state.

### 4\. Daemon Status and Shutdown

To query the daemon's status or shut down all device connections, publish a payload to the `tuya2mqtt/device/query` topic.

  * **Query Daemon Status**:

    ```json
    {
      "status": true
    }
    ```

    The response will be published to the `tuya2mqtt/log/daemon` topic.

  * **Reset All Device Connections**:

    ```json
    {
      "reset": true
    }
    ```

    This command terminates communication with all currently connected devices. The daemon process itself remains active, but you must use the `add` command to reconnect devices.

  * **Terminate All Connections and Shut Down Daemon**:

    ```json
    {
      "stop": true
    }
    ```

    This command is equivalent to `python tuya2mqtt.py stop`. It terminates all device connections and shuts down the daemon completely.

-----

## Important Notes & Recommendations

This script is intentionally streamlined and focused on **robustness**. However, it relies on several external dependencies you should be aware of.

  * **External Module Dependencies**: `tuya2mqtt` relies entirely on the `tinytuya` and `mqtt` modules. Unexpected updates to these modules can impact the script's stability, so it's recommended to **keep the module versions stable**.
      * **[tinytuya](https://github.com/jasonacox/tinytuya)**
      * **[paho-mqtt](https://github.com/eclipse/paho.mqtt.python)**
  * **Network Resources**: As the number of Tuya devices increases, a large number of **TCP connections will be kept alive**. This can put a significant load on your router's resources, so make sure you have a router with sufficient capacity.
  * **MQTT Broker Environment**: As device connections and communication become more frequent, the MQTT broker may experience increased load. For **maximum performance**, this script is designed not to use additional security features like TLS (Transport Layer Security). To ensure security, you should prevent external access to your broker and operate it directly on **`localhost`**.
  * **File Descriptors**: The script sets the maximum file descriptor (FD) limit at startup to handle a large number of concurrent device connections.
