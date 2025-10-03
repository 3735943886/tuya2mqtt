## 🚀 Tuya2MQTT Web UI

A **web-based user interface (UI)** for managing devices connected through the **Tuya2MQTT Bridge**. Built with **NiceGUI**, it communicates with the underlying `tuya2mqtt` daemon via **MQTT** to provide real-time monitoring and configuration capabilities.

---

## ✨ Key Features

* **Real-time Device Tree:** Displays all registered devices in a hierarchical structure with online/offline status indicators.
* **MQTT Integration:** Interacts with the `tuya2mqtt` daemon for status updates and command publishing.
* **Comprehensive Device Management:**

  * **Add/Edit/Delete** individual WiFi or Zigbee/BLE sub-devices.
  * **Bulk Management Wizard:**

    * Run the **Tinytuya Wizard** to fetch device credentials from the Tuya Cloud.
    * Directly **Edit/Save** the device configuration file.
    * Bulk-add multiple devices from a configuration file.
    * Create a device **Snapshot**.

---

## ⚙️ Requirements & Setup

### 1. Prerequisites

**NiceGUI** and **Tuya2MQTT** must be installed, as this UI is designed to run alongside the Tuya2MQTT bridge.

### 2. Installation

Install the required Python libraries:

```bash
pip install nicegui tuya2mqtt
```

### 3. Configuration File (`config.json`)

An optional **JSON configuration file** defines the MQTT broker, the UI port, and the location of management files.

The default path in the example code is `./tuya2mqtt.conf`, but when using JSON-based configuration, a custom file or environment variables may be applied depending on the bridge setup. Ensure the settings match the environment.

Example structure:

```json
{
  "broker": {
    "hostname": "localhost",
    "port": 1883,
    "username": "",
    "password": ""
  },
  "ui": {
    "port": 8373,
    "json": {
      "devices": "./devices.json",
      "snapshot": "./device_snapshot.json",
      "tinytuya": "./tinytuya.json"
    }
  }
}
```

### 4. Running the UI

Execute the main script:

```bash
python3 tuya2mqtt_ui.py --ui-port 8373
```

The console displays the loaded configuration, and the UI becomes accessible from a web browser at the configured port (e.g., `http://<host-ip>:8373`).

---

## 🖥️ Web UI Functionality

The main interface consists of a **Device List** (tree view) and a **Device Info** panel.

### Device List

* Shows device ID, name, and status icon.
* Selecting a device loads its details in the info panel.
* A search bar filters the device list.

### Device Info & Management

The selected device’s attributes are displayed in the info area.

| Button      | Action                                                      |
| :---------- | :---------------------------------------------------------- |
| **Add**     | Opens a form to manually add a WiFi or Zigbee/BLE device.   |
| **Edit**    | Opens a form to modify the selected device’s configuration. |
| **Del**     | Removes the selected device from the bridge configuration.  |
| **Wizard**  | Opens the **Bulk Management Dialog**.                       |
| **About**   | Shows information                                           |

### Bulk Management Dialog (Wizard)

Provides tooling for large-scale configuration and file operations:

* **Run Wizard:** Executes `tinytuya wizard` to fetch all Tuya Cloud devices and store them in `devices.json`.
* **View/Edit:** Opens an editor to modify and save `devices.json`.
* **Add devices:** Validates entries in `devices.json`, then allows selecting multiple valid devices for MQTT-based registration in the bridge.
* **Device Snapshot:** Saves the current device list to `device_snapshot.json`.
* **Reset all:** Sends a command to remove all registered devices from the bridge.

---

## 📄 Device Configuration Files

The UI interacts with the following files for configuration and state management:

| File                   | Purpose                                                                 | Example                        |
| :--------------------- | :---------------------------------------------------------------------- | :----------------------------- |
| `device_snapshot.json` | Stores a clean backup of device configurations.                         | Array of device objects.       |
| `tinytuya.json`        | Stores Tuya Cloud API credentials (`apiKey`, `apiSecret`, `apiRegion`). | Single JSON credential object. |
