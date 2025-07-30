## tuya2mqtt_ha_bridge.py

The `tuya2mqtt_ha_bridge.py` script serves as a bridge for integrating Tuya devices into a Home Assistant environment. Its primary function is to **automate the creation and deletion of Home Assistant entities** by reading a `devices.json` file and publishing configuration messages via the **MQTT Discovery** protocol.

This script acts as a translator, converting raw Tuya device specifications into entities that Home Assistant can recognize and control.

### Operational Overview

The script's process is as follows:

1.  **Service Invocation**: The script is triggered by calling the `tuya2mqtt_ha_device_manager` service within Home Assistant. This service accepts parameters to either `add` or `delete` devices.

2.  **Device Definition Loading**: It reads the `devices.json` file, which contains detailed information about Tuya devices, including their ID, name, model, and a `mapping` dictionary that defines their functions (Data Points or DPs).

3.  **Communication with `tuya2mqtt` Bridge**:
    * **On Add**: It publishes the device's full data to the `tuya2mqtt/device/add` topic, instructing the bridge to begin managing the device.
    * **On Delete**: It publishes the device's ID to the `tuya2mqtt/device/delete` topic to cease management.

4.  **HA Entity Generation via MQTT Discovery**: This is the core logic. The script iterates through each function (DP) listed in the device's `mapping`. It analyzes the `type` of each DP (`Boolean`, `Integer`, `Enum`) and intelligently maps it to the most appropriate Home Assistant component:
    * **`Boolean`** DPs are converted into a `switch` or `binary_sensor`.
    * **`Integer`** DPs can become a `sensor` (e.g., for `temperature`, `power`) or a `number` entity for adjustable values.
    * **`Enum`** DPs can be converted into a `select` entity or an `event` entity.
    * **Configuration Publishing**: It constructs a JSON payload with all necessary configuration details (such as `name`, `device_class`, `unit_of_measurement`) and publishes it to a specific MQTT topic (e.g., `homeassistant/sensor/DEVICE_ID_DP/config`). Home Assistant listens on these topics and automatically creates or updates the corresponding entity.

5.  **Initial State Request**: After a device is added, the script sends a message to the `tuya2mqtt/device/get` topic. This prompts the bridge to immediately fetch the device's current state, ensuring that the new entities in Home Assistant display the correct values from the outset.

---

## tuya2mqtt_daemon.py

The `tuya2mqtt_daemon.py` script creates a Home Assistant service to **check the health status of the `tuya2mqtt` daemon**. This system operates via a **request-response mechanism**.

### Operational Overview

The process involves a coordinated handshake between two functions, managed by an in-memory queue and MQTT messages.

1.  **Request Initiation**: When the `tuya2mqtt_check_daemon` service is called in Home Assistant, it publishes a status request payload (`{"status": True}`) to the `tuya2mqtt/device/query` MQTT topic.

2.  **Waiting for Response**: Immediately after sending the request, the script waits for a message to arrive in an internal communication channel, the `tuya2mqtt_queue`.

3.  **Daemon's Response**: The `tuya2mqtt` daemon, which is listening on the `query` topic, receives the request and publishes its status information to a different topic: `tuya2mqtt/log/daemon`.

4.  **Reception and Forwarding**: The `daemon_recv` function, which is decorated with an `@mqtt_trigger` for the `log` topic, automatically executes upon receiving the daemon's response. It takes this payload and places it into the `tuya2mqtt_queue`.

5.  **Result Return**: As soon as an item appears in the `tuya2mqtt_queue`, the waiting `tuya2mqtt_check_daemon` function retrieves it and returns its content as the result of the service call. If **5 seconds** elapse before a message is placed in the queue, a `TimeoutError` is triggered, and the service returns a standard error message: `{'tuya2mqtt': 'Unable to connect.'}`.
