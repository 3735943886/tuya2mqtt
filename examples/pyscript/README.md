## tuya2mqtt_devmanager.py

This `pyscript` for Home Assistant acts as an intelligent bridge to seamlessly integrate Tuya devices into your Home Assistant environment. It works in conjunction with a Tuya to MQTT bridge service `tuya2mqtt`.

The primary goal of this script is to **automate the creation and deletion of Home Assistant entities** by reading a `devices.json` file and publishing configuration messages using the **MQTT Discovery** protocol. In short, it acts as a translator, converting raw Tuya device specifications into entities that Home Assistant can understand and control.



---

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
