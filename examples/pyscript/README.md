## tuya2mqtt_devmanager.py

This `pyscript` for Home Assistant acts as an intelligent bridge to seamlessly integrate Tuya devices into your Home Assistant environment. It works in conjunction with a Tuya to MQTT bridge service `tuya2mqtt`.

The primary goal of this script is to **automate the creation and deletion of Home Assistant entities** by reading a `devices.json` file and publishing configuration messages using the **MQTT Discovery** protocol. In short, it acts as a translator, converting raw Tuya device specifications into entities that Home Assistant can understand and control.

### How It Works

The script operates in a clear, step-by-step process:

1.  **Service Invocation**: The script is triggered by calling the `tuya2mqtt_ha_device_manager` service within Home Assistant. You can pass parameters to specify whether to `add` or `delete` devices.

2.  **Load Device Definitions**: It reads the `devices.json` file, which contains detailed information about your Tuya devices, including their ID, name, model, and a `mapping` dictionary that defines their functions (known as Data Points or DPs).

3.  **Communicate with Tuya2MQTT Bridge**:
    * **On Add**: It publishes the device's full data to the `tuya2mqtt/device/add` topic. This tells the bridge to start listening to and managing the device.
    * **On Delete**: It publishes the device's ID to the `tuya2mqtt/device/delete` topic to cease management.

4.  **Generate HA Entities via MQTT Discovery**: This is the core logic. The script iterates through each function (DP) listed in the device's `mapping`.
    * **Intelligent Mapping**: It analyzes the `type` of each DP (`Boolean`, `Integer`, `Enum`) and intelligently maps it to the most appropriate Home Assistant component:
        * A **`Boolean`** DP becomes a `switch` or `binary_sensor`.
        * An **`Integer`** DP can become a `sensor` (e.g., for `temperature`, `power`, `humidity`), or a `number` entity for adjustable values.
        * An **`Enum`** DP can become a `select` entity (for dropdowns) or an `event` entity (for stateless scene buttons).
    * **Publish Configuration**: It constructs a JSON payload with all the necessary configuration details (like `name`, `device_class`, `unit_of_measurement`, etc.) and publishes it to a specific MQTT topic, such as `homeassistant/sensor/DEVICE_ID_DP/config`. Home Assistant listens on these topics and automatically creates or updates the corresponding entity.

5.  **Request Initial State**: After adding a device, it sends a message to the `tuya2mqtt/device/get` topic. This prompts the bridge to immediately fetch the device's current state, ensuring that the new entities in Home Assistant display the correct values from the start.

---

## tuya2mqtt_daemon.py

This script creates a Home Assistant service to **check the health status of the `tuya2mqtt` daemon**.
Think of it as a **request-response system using a walkie-talkie**: one function asks "Are you there?" and waits for a reply. If no response is heard within 5 seconds, it assumes the other party is unavailable.

### How It Works

The process involves a coordinated handshake between two functions, arbitrated by an in-memory queue and MQTT messages.

1.  **Request Initiation**: When the `tuya2mqtt_check_daemon` service is called in Home Assistant, it first publishes a status request payload (`{"status": 1}`) to the `tuya2mqtt/device/query` MQTT topic.

2.  **Waiting for a Reply**: Immediately after sending the request, the script begins waiting for a message to arrive in an internal communication channel, `tuya2mqtt_queue`.

3.  **Daemon's Response**: The `Tuya2MQTT` daemon, which is listening on the `query` topic, receives the request and publishes its status information back to a different topic: `tuya2mqtt/log/daemon`.

4.  **Receiving and Forwarding**: The `daemon_recv` function, which is decorated with an `@mqtt_trigger` for the `log` topic, automatically executes upon receiving the daemon's response. It takes this payload and immediately places it into the `tuya2mqtt_queue`.

5.  **Returning the Result**: As soon as an item appears in the `tuya2mqtt_queue`, the waiting `tuya2mqtt_check_daemon` function retrieves it and returns its content as the result of the service call. If **5 seconds** elapse before any message is placed in the queue, a `TimeoutError` is triggered, and the service returns a standard error message: `{'tuya2mqtt': 'Unable to connect.'}`.
