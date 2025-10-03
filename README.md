# tuya2mqtt: Tuya Devices to MQTT Bridge

`tuya2mqtt` is a Python script that connects Tuya smart devices to an MQTT broker. **It acts as a backend service that maintains a 24-hour TCP connection with registered Tuya devices. This allows it to instantly publish state changes to MQTT and enable device control via MQTT commands.**
`tuya2mqtt` is daemonized with `python-daemon` to run as a robust and lightweight background process, minimizing overhead and dependencies to maximize performance.

-----

## Key Features

  * **Tuya Device Control**: Device state can be set via MQTT.
  * **Status Monitoring**: Real-time status updates (DPS) are received from Tuya devices and published to MQTT.
  * **Asyncio**: Each device is handled in a separate task for stable, concurrent communication.
  * **Daemon Mode**: Runs reliably in the background, with PID file management to prevent multiple instances.
  * **Dynamic Device Management**: Add or remove devices on the fly using MQTT commands without restarting the daemon.
  * **Wide Device Support**: Control various Tuya devices, including Wi-Fi, Zigbee, and BLE.

-----

## Installation

`tuya2mqtt` requires Python 3.8 or later.

Install the package and its dependencies using `pip`:

```sh
pip install tuya2mqtt
```

Alternatively, when running from a manual clone, install the required libraries manually:

```sh
pip install tinytuya aiomqtt python-daemon
```

-----

## Usage

### **Built in Daemon (default)**

  * **Start:** Run the script as a background daemon.
    ```sh
    python -m tuya2mqtt --mode start
    ```
  * **Stop:** Safely stop the running daemon.
    ```sh
    python -m tuya2mqtt --mode stop
    ```
  * **Restart:** Stop the current daemon instance and start a new one.
    ```sh
    python -m tuya2mqtt --mode restart
    ```

### **Foreground Mode (advanced)**

  * **Foreground:** Run the script in the foreground. Useful for use with service managers like **systemd**.
    ```sh
    python -m tuya2mqtt --mode foreground
    ```

### **Docker**

A pre-built image is available on [Docker Hub](https://hub.docker.com/r/3735943886/tuya2mqtt) for easy containerized deployment.

-----

## Configuration

Create a `tuya2mqtt.conf` to change the MQTT broker information.

**`tuya2mqtt.conf` Example:**

```json
{
  "broker": {
    "host": "localhost",
    "port": 1883
    "username": "",
    "password": ""
  }
}
```

-----

## Managing Devices via MQTT

### 1\. Add a Device

To add a new Tuya device, publish a JSON payload to the `tuya2mqtt/intro/device/add` topic.

  * **Tuya Wi-Fi Device**: Requires `ip`, `key`, and `version`. `id` and `name` are optional.
    ```json
    {
      "id": "ebed836691xxxxxxb",
      "ip": "192.168.1.100",
      "key": "b4e4776e1f0e21a2",
      "version": 3.3,
      "name": "My_Smart_Plug"
    }
    ```
  * **Tuya Zigbee/BLE Device**: Requires `node_id`, and `parent` (the hub's ID). `id` and `name` are optional.
    ```json
    {
      "id": "ebed836691xxxxxxb",
      "node_id": "01020202111111112222",
      "parent": "ebed836691xxxxxxb",
      "name": "My_Sub_Device"
    }
    ```

### 2\. Control and Query Device Status

To control a device or request its status, publish a payload to the relevant topic. **A device can be specified by either its `id` or `name`.**

  * **Set State**: Publish a JSON payload with `data` (dict of datapoint: value pairs) to the `tuya2mqtt/intro/device/set` topic.
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
  * **Get Status**: Publish a JSON payload containing the device's `id` or `name` to the `tuya2mqtt/intro/device/get` topic.
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
  * **Execute Direct Commands (Advanced Usage)**: To call specific `tinytuya` methods on a device (e.g., `turn_on()`, `set_value()`), publish a payload to the `tuya2mqtt/intro/device/command` topic.
      * Using `name`:
        ```json
        {
          "name": "My_Smart_Plug",
          "command": "turn_on",
          "data": { "switch": 1 }
        }
        ```
      * Using `id`:
        ```json
        {
          "id": "ebed836691xxxxxxb",
          "command": "set_value",
          "data": { "1": true }
        }
        ```

### 3\. Monitor Devices via MQTT

`tuya2mqtt` provides two main topics for device monitoring. By subscribing to these topics, real-time updates on device status changes and command history can be obtained.

  * `tuya2mqtt/extra/device/active`: This topic is used for instant updates. It publishes when **a Tuya device reports a state change on its own**, like when a smart button is pressed or a switch is physically toggled.
  * `tuya2mqtt/extra/device/passive`: This topic is for status reports. It publishes in **response to a** `tuya2mqtt/intro/device/get` or as a part of a periodic device status check.
  * `tuya2mqtt/extra/device/error`: This topic is for error messages. It publishes when **daemon receives an error message from tuya device**.

Data can be received in this format by subscribing to the topic:

```bash
$ mosquitto_sub -t tuya2mqtt/extra/device/#
```

```json
{"id": "ebed836691xxxxxxb", "name": "My_Smart_Plug", "data": {"1": true}}
{"id": "ebed836691xxxxxxb", "name": "My_Smart_Plug", "data": {"1": false}}
```

The **`id`** field represents the `id` for Wi-Fi devices and the `node_id` for sub-devices.

### 4\. Delete a Device

To remove a device from the running instance, publish a JSON payload with the device's `id` or `name` to the `tuya2mqtt/intro/device/delete` topic. The device will be disconnected.

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

### 5\. Daemon Status and Shutdown

To query the daemon's status or manage its state, publish a payload to the `tuya2mqtt/intro/daemon/query` topic.

  * **Query Daemon Status**: Request a detailed status report.

    ```json
    {
      "status": true
    }
    ```

    The response, published to `tuya2mqtt/extra/daemon/status`, will include uptime, device counts, and a detailed list of all connected devices and their current state.

  * **Reset All Device Connections**:

    ```json
    {
      "reset": true
    }
    ```

    This command terminates communication with all currently connected devices. The daemon process itself remains active, but the `add` command must be used to reconnect devices.

  * **Terminate All Connections and Shut Down Daemon**:

    ```json
    {
      "stop": true
    }
    ```

    This command is equivalent to `--mode stop`. It terminates all device connections and shuts down the daemon completely.

-----

## Important Notes & Recommendations

This script is intentionally streamlined and focused on **robustness**. However, it relies on several external dependencies.

  * **External Module Dependencies**: `tuya2mqtt` relies entirely on the `tinytuya` and `aiomqtt` modules. Unexpected updates to these modules can impact the script's stability, so keeping the module versions stable is recommended.
      * **[tinytuya](https://github.com/jasonacox/tinytuya)**
      * **[aiomqtt](https://github.com/empicano/aiomqtt)**
  * **Network Resources**: As the number of Tuya devices increases, a large number of **TCP connections will be kept alive**. This can put a significant load on a router's resources, so a router with sufficient capacity should be used.
  * **MQTT Broker Environment**: As device connections and communication become more frequent, the MQTT broker may experience increased load. For **maximum performance**, it is recommended to be operated directly on `localhost`.
