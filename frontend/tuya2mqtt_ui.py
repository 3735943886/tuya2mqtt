import argparse
import aiofiles
import aiomqtt
import asyncio
import ipaddress
import json
import logging
import time
import traceback
import re
import sys
import os
import qrcode
from string import Template
from datetime import datetime
from pathlib import Path
from nicegui import Event, app, ui
from tuya2mqtt import Tuya2MQTTBridge

import tinytuya.scanner
from tinytuya_wizard import TuyaWizard

# ==============================================================================
# Global Constants & Runtime State
# ==============================================================================
class SystemConstants:
    WORK_FOLDER = Path(__file__).parent.resolve()
    DEFAULT_CONF_FILE = WORK_FOLDER / "tuya2mqtt.conf"
    WIZARD_RUNNING = False

class RuntimeContext:
    """Holds runtime objects like the Bridge instance and asyncio tasks."""
    BRIDGE = None
    TASKS = []
    # Shared context passed between UI and Bridge
    SHARED = {
        "logger": logging.getLogger(),
        "mqtt_event": Event[aiomqtt.client.Client, aiomqtt.message.Message](),
        "mqtt_init_event": asyncio.Event(),
        "mqtt_info": {}
    }

# ==============================================================================
# Main UI Class
# ==============================================================================
class Tuya2MqttUi(ui.page):

    def __init__(self, **configuration):
        # --- 1. Configuration & Resources ---
        self.conf = configuration
        self.logger = self.conf.pop("logger", logging.getLogger())
        self.root_topic = self.conf.get("root_topic", "tuya2mqtt")

        # File Paths
        self.path_devices_json = self.conf["ui"]["json"].get("devices", "./devices.json")
        self.path_snapshot_json = self.conf["ui"]["json"].get("snapshot", "./device_snapshot.json")
        self.path_tinytuya_json = self.conf["ui"]["json"].get("tinytuya", "./tinytuya.json")

        # MQTT Objects (References)
        self.mqtt_event = self.conf.pop("mqtt_event", None)
        self.mqtt_info = self.conf.pop("mqtt_info", None)
        self.mqtt_client = None # Initialized in _handle_topic_registration

        # --- 2. State Flags & Data Containers ---
        self.flag_is_editing_mode = False     # Distinguish between Add and Edit modes
        self.flag_json_dirty = False          # Tracks if JSON editor has unsaved changes

        self.data_device_map = {}             # ID -> Device Data
        self.data_parent_ids = set()          # Set of potential parent device IDs
        self.data_tree_structure = []         # Tree structure for UI
        self.data_snapshot = []               # Snapshot data buffer
        self.data_daemon_stat = {}            # Daemon status buffer

        self.var_selected_row_id = None       # Currently selected ID in tree
        self.var_target_node = None           # Currently selected Node object
        self.var_json_original = None         # Original content for JSON editor

        # --- 3. Input Bindings (Form Data) ---
        # Wizard Form Data
        self.form_wizard = {
            "user_code": None
        }
        # Device Add/Edit Form Data
        self.form_device = {} # Initialized in setup_ui

        # --- 4. Async Events ---
        self.event_snapshot_ready = asyncio.Event()
        self.event_daemon_ready = asyncio.Event()

        # --- 5. Initialization ---
        self.setup_ui()
        self.mqtt_event.subscribe(lambda message: self._handler_mqtt_message(message), unsubscribe_on_delete=True)

    # ==========================================================================
    # Section 1: Static Utility Methods (Pure Functions)
    # ==========================================================================
    @staticmethod
    def _util_find_node(data_structure, target_id):
        """Recursively finds a node by ID within the tree structure."""
        if isinstance(data_structure, dict):
            if "id" in data_structure and data_structure["id"] == target_id:
                return data_structure
            for value in data_structure.values():
                result = Tuya2MqttUi._util_find_node(value, target_id)
                if result is not None:
                    return result
        elif isinstance(data_structure, (list, tuple)):
            for item in data_structure:
                result = Tuya2MqttUi._util_find_node(item, target_id)
                if result is not None:
                    return result
        return None

    @staticmethod
    def _util_dict_to_markdown(data):
        """Converts a dictionary to a Markdown list string."""
        lines = []
        for key, value in data.items():
            if value:
                escape_markdown = re.sub(r"([\\`*_{}\[\]()#+\-!<>])", r"\\\1", str(value))
                lines.append(f"**{key.capitalize()}**: {escape_markdown}")
        return "\n\n".join(lines)

    @staticmethod
    def _util_build_tree(device_list):
        """Converts a flat device list into a hierarchical tree structure."""
        nodes = {}
        tree = []
        # Pass 1: Create Nodes
        for device in device_list:
            device_id = device["id"]
            label = f"{device_id} [{device.get('name', 'N/A')}]"
            nodes[device_id] = {
                "id": label,
                "children": [],
                "device_data": device
            }
            # Set Default Icons
            if "parent" not in device:
                nodes[device_id]["icon"] = "cached"
                if "status" in device:
                    if device["status"] == "online":
                        nodes[device_id]["icon"] = "wifi"
                    elif device["status"] != "connecting":
                        nodes[device_id]["icon"] = "wifi_off"

        # Pass 2: Build Hierarchy
        for device in device_list:
            node = nodes[device["id"]]
            parent_id = device.get("parent")
            if parent_id and parent_id in nodes:
                parent_node = nodes[parent_id]
                parent_node["children"].append(node)
                parent_node["icon"] = "device_hub"
            else:
                tree.append(node)
        return tree

    @staticmethod
    def _util_validate_input(category, value, parent_ids_set):
        """Validates input fields based on category."""
        try:
            if category == "ip":
                ipaddress.IPv4Address(value)
                return True
            elif category == "key" or category == "node_id":
                return len(value) > 5
            elif category == "version":
                v = float(value)
                return 3.1 <= v <= 3.5
            elif category == "parent":
                return value in parent_ids_set
        except (ValueError, TypeError):
            return False
        return False

    # ==========================================================================
    # Section 2: MQTT & Network Logic
    # ==========================================================================
    @staticmethod
    async def _mqtt_worker(**kwargs):
        """
        Background MQTT listener.
        Runs independently to bridge MQTT messages to NiceGUI events.
        """
        mqtt_event = kwargs.pop("mqtt_event", None)
        mqtt_init_event = kwargs.pop("mqtt_init_event", None)
        mqtt_info = kwargs.pop("mqtt_info", {})
        logger = kwargs.pop("logger", logging.getLogger())

        if mqtt_event:
            reconnect_delay = kwargs.pop("reconect_delay", 30)
            root_topic = kwargs.pop("root_topic", "tuya2mqtt")
            broker = kwargs.pop("broker", {"hostname": "localhost", "port": 1883})

            while True:
                try:
                    async with aiomqtt.Client(**broker) as mqtt:
                        await mqtt.subscribe(root_topic)
                        async for message in mqtt.messages:
                            if str(message.topic) == root_topic:
                                # Retained root topic message contains registration info
                                mqtt_info["client"] = mqtt
                                mqtt_info["payload"] = message.payload
                                await mqtt.unsubscribe(root_topic)
                                if mqtt_init_event:
                                    mqtt_init_event.set()
                            else:
                                mqtt_event.emit(message)
                except aiomqtt.MqttError as err:
                    # Case 1: Standard connection issues (Broker down, Network fail) -> Level: WARNING
                    logger.warning(f"MQTT connection failed: {err}. Retrying in {reconnect_delay} seconds...")
                    await asyncio.sleep(reconnect_delay)

                except Exception as err:
                    # Case 2: Unexpected crashes (Code bugs, Library errors) -> Level: ERROR + Traceback
                    logger.error(f"CRITICAL: Unexpected error in MQTT worker loop: {err}")
                    logger.error(traceback.format_exc())  # Log full stack trace for debugging
                    await asyncio.sleep(reconnect_delay)

    async def _handle_topic_registration(self):
        """Handles initial topic subscription based on configuration received via MQTT."""
        self.mqtt_client = self.mqtt_info["client"]
        self.conf["topic"] = json.loads(self.mqtt_info["payload"].decode("utf-8"))

        topics_to_subscribe = []
        # Convert templates to wildcards
        for topic_tpl in self.conf["topic"]["publish"].values():
            topics_to_subscribe.append(re.sub(r"\$\w+", "+", topic_tpl))
        for topic in self.conf["topic"]["log"].values():
            topics_to_subscribe.append(topic)

        for topic in topics_to_subscribe:
            try:
                await self.mqtt_client.subscribe(topic)
                self.logger.info(f"Topic subscribed: {topic}")
            except Exception:
                self.logger.error(f"Cannot subscribe: {topic}")

    async def _handler_mqtt_message(self, message):
        """Main handler for incoming MQTT messages."""
        topic_str = str(message.topic)
        try:
            payload_str = message.payload.decode("utf-8") if isinstance(message.payload, bytes) else message.payload
            payload_obj = json.loads(payload_str)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self.logger.info(f"Failed to decode payload: {e}")
            payload_obj = {}

        devices_updated = False
        topic_conf = self.conf["topic"]

        # 1. Handle Daemon/Snapshot (Full List Update)
        if Tuya2MQTTBridge._match_topic(topic_str, topic_conf["publish"]["daemon"]) or \
           Tuya2MQTTBridge._match_topic(topic_str, topic_conf["publish"]["snapshot"]):

            if Tuya2MQTTBridge._match_topic(topic_str, topic_conf["publish"]["snapshot"]):
                self.event_snapshot_ready.set()
            elif Tuya2MQTTBridge._match_topic(topic_str, topic_conf["publish"]["daemon"]):
                self.event_daemon_ready.set()
                self.data_daemon_stat = {k: v for k, v in payload_obj.items() if k != "devices"}

            devices_updated = self._logic_update_device_list(payload_obj, topic_str)

        # 2. Handle Error Update
        elif dps := Tuya2MQTTBridge._match_topic(topic_str, topic_conf["publish"]["error"]):
            payload_obj.update(dps)
            devices_updated = self._logic_update_device_status(payload_obj, status_key="Error", default_status="Unknown Error")

        # 3. Handle Logs
        elif any(topic_str == topic_conf["log"].get(lvl) for lvl in ["warning", "error", "critical"]):
            ui.notify(message=payload_obj.get("Error") or payload_str, type="negative")

        if devices_updated:
            await self._ui_refresh_tree()

    async def _api_send_command(self, command_key, payload):
        """Publishes a command via MQTT."""
        topic_tpl = self.conf["topic"]["subscribe"].get(command_key)
        if not topic_tpl:
            self.logger.error(f"Command topic '{command_key}' not found.")
            return

        topic = Template(topic_tpl).safe_substitute(payload) if isinstance(payload, dict) else topic_tpl
        try:
            await self.mqtt_client.publish(topic, payload=json.dumps(payload))
            self.logger.info(f"Command '{command_key}' sent to '{topic}': {payload}")
        except Exception as e:
            ui.notify(f"Failed to send command: {e}", type="negative")

    # ==========================================================================
    # Section 3: Internal Logic (Data Processing)
    # ==========================================================================
    def _logic_update_device_list(self, payload_obj, topic_str=None):
        """Updates the internal device list from payload."""
        self.data_snapshot = payload_obj if isinstance(payload_obj, list) else payload_obj.get("devices", [])
        new_map = {}

        if not isinstance(self.data_snapshot, list):
            self.logger.warning(f"Non-list payload: {self.data_snapshot}")
            self.data_snapshot = []

        for dev in self.data_snapshot:
            if isinstance(dev, dict) and dev.get("id"):
                new_map[dev["id"]] = dev
            else:
                self.logger.warning(f"Invalid device item: {dev}")

        self.data_device_map = new_map

        # Filter potential parent devices (Devices with IP or no parent)
        self.data_parent_ids = {
            dev["id"] for dev in self.data_device_map.values()
            if "ip" in dev or not dev.get("parent")
        }

        # Update Auto-complete for Parent field
        if "parent" in self.form_device:
             self.form_device["parent"].set_autocomplete(list(self.data_parent_ids))

        if topic_str and Tuya2MQTTBridge._match_topic(topic_str, self.conf["topic"]["publish"]["daemon"]):
            # Failure to send a notification when the client is closed occasionally results in an error.
            # ui.notification(message="device list updated", timeout=0.5)
            pass
        return True

    def _logic_update_device_status(self, payload_obj, status_key=None, default_status="online"):
        """Updates the status of a specific device."""
        dev_id = payload_obj.get("id")
        device = self.data_device_map.get(dev_id)
        if device:
            device["last_seen"] = datetime.now()
            device["status"] = payload_obj.get(status_key, default_status) if status_key else default_status
            return True
        return False

    async def _ui_refresh_tree(self):
        """Rebuilds and updates the UI Tree component."""
        new_tree = self._util_build_tree(list(self.data_device_map.values()))
        self.ui_tree.props["nodes"] = new_tree
        try:
            self.ui_tree.update()
            await self.action_select_row(self.var_selected_row_id, notify_user=False)
        except RuntimeError:
            pass

    async def _io_read_file(self, filename):
        try:
            async with aiofiles.open(filename, "r", encoding="utf-8") as f:
                return await f.read()
        except Exception as e:
            ui.notify(f"File read error: {e}", type="negative")
        return None

    async def _io_write_file(self, filename, content):
        try:
            async with aiofiles.open(filename, "w", encoding="utf-8") as f:
                if isinstance(content, (dict, list)):
                    await f.write(json.dumps(content, ensure_ascii=False, indent=2))
                else:
                    await f.write(content)
            ui.notification(message=f"{filename} saved", timeout=0.5)
            return True
        except Exception as e:
            ui.notify(f"File write error: {e}", type="negative")
        return False

    # ==========================================================================
    # Section 4: UI Construction
    # ==========================================================================
    def setup_ui(self):
        ui.page_title("Tuya2Mqtt Web UI")
        self._setup_meta_tags()

        # --- Dialog: Confirmation ---
        with ui.dialog().props("persistent") as self.dialog_confirm, ui.card():
            self.ui_lbl_confirm_title = ui.label("Are you sure?").classes("text-2xl font-medium")
            with ui.row().classes("w-full"):
                ui.button("Apply", on_click=lambda: self.dialog_confirm.submit("Apply")).classes("flex-1")
                ui.button("Cancel", on_click=lambda: self.dialog_confirm.submit("Cancel")).classes("flex-1")

        # --- Dialog: Wizard Success ---
        with ui.dialog() as self.dialog_wizard_end, ui.card():
            ui.label("Successfully finished").classes("text-2xl font-medium")
            ui.label("devices.json created")
            ui.button("Close", on_click=self.dialog_wizard_end.close)

        # --- Dialog: Wizard Runner ---
        with ui.dialog() as self.dialog_wizard, ui.card().style("min-width: 300px; min-height: 300px;"):
            ui.label("Tinytuya Wizard").classes("text-2xl font-medium")
            ui.label("Enter User Code from SmartLife or TuyaSmart App")
            self.form_wizard["user_code"] = ui.input("User Code").classes("w-4/5").props("clearable")
            self.wizard_status = ui.label("Status: Ready")
            with ui.row():
                self.btn_run_wizard = ui.button("Run", on_click=self.action_run_wizard).tooltip("Run tinytuya wizard")
                ui.button("Close", on_click=self.dialog_wizard.close)

        # --- Dialog: JSON Editor ---
        with ui.dialog() as self.dialog_editor, ui.card().style("min-width: 90%; max-width: 90%;"):
            self.ui_editor = ui.codemirror(value="", language="JSON", line_wrapping=True, on_change=self.action_check_editor_state).classes("w-full h-[calc(70vh)]")
            with ui.row():
                self.btn_save_editor = ui.button("Save", on_click=self.action_save_editor)
                ui.button("Cancel", on_click=self.action_cancel_editor)

        # --- Dialog: Daemon Info ---
        with ui.dialog() as self.dialog_daemon, ui.card():
            ui.label("Tuya2MQTT").classes("text-2xl font-medium")
            self.ui_code_daemon = ui.code("").style("min-width: 250px;")
            ui.button("Close", on_click=self.dialog_daemon.close)

        # --- Dialog: Bulk Management ---
        with ui.dialog() as self.dialog_bulk_menu, ui.card():
            ui.label("Management of devices").classes("text-2xl font-medium")
            ui.button("Run Wizard", on_click=self.action_open_wizard).classes("w-full").tooltip("Generate a new devices.json")
            ui.button("View/Edit", on_click=self.action_open_editor).classes("w-full").tooltip("View or edit devices.json")
            ui.button("Add devices", on_click=self.action_open_bulk_adder).classes("w-full").tooltip("Add multiple devices using devices.json")
            ui.button("Device Snapshot", on_click=self.action_make_snapshot).classes("w-full").tooltip("Make a snapshot of registered devices")
            ui.button("Reset all", on_click=self.action_reset_devices).classes("w-full").tooltip("Reset all registered devices")

        # --- Dialog: Device Form (Add/Edit) ---
        with ui.dialog() as self.dialog_device_form, ui.card().style("min-width: 300px;"):
            self.ui_lbl_form_title = ui.label("Add a new device").classes("text-2xl font-medium")
            self.form_device["id"] = ui.input("Device ID").classes("w-4/5").props("clearable")
            self.form_device["name"] = ui.input("Name").classes("w-4/5").props("clearable")
            ui.separator()
            self.ui_rad_dev_type = ui.radio(["WiFi", "Zigbee/BLE"], value="WiFi").props("inline")

            # WiFi Fields
            with ui.column().bind_visibility_from(self.ui_rad_dev_type, "value", lambda v: v == "WiFi").classes("w-full"):
                self.form_device["ip"] = ui.input("IP", validation={"Invalid": lambda v: self._util_validate_input("ip", v, self.data_parent_ids)}).classes("w-4/5").props("clearable")
                self.form_device["key"] = ui.input("Key", validation={"Invalid": lambda v: self._util_validate_input("key", v, self.data_parent_ids)}).classes("w-4/5").props("clearable")
                self.form_device["version"] = ui.input("Version", validation={"Invalid": lambda v: self._util_validate_input("version", v, self.data_parent_ids)}).classes("w-4/5").props("clearable")

            # Zigbee/BLE Fields
            with ui.column().bind_visibility_from(self.ui_rad_dev_type, "value", lambda v: v == "Zigbee/BLE").classes("w-full"):
                self.form_device["node_id"] = ui.input("Node ID", validation={"Invalid": lambda v: self._util_validate_input("node_id", v, self.data_parent_ids)}).classes("w-4/5").props("clearable")
                self.form_device["parent"] = ui.input("Parent", validation={"Invalid": lambda v: self._util_validate_input("parent", v, self.data_parent_ids)}).classes("w-4/5").props("clearable")

            # Validation & Buttons
            with ui.row().classes("w-full"):
                class FormValidator:
                    def __init__(self, ui_instance):
                        self.ui = ui_instance
                    @property
                    def is_valid(self):
                        # Validate based on selected type
                        if self.ui.ui_rad_dev_type.value == "WiFi":
                            fields = [self.ui.form_device["ip"], self.ui.form_device["key"], self.ui.form_device["version"]]
                        else:
                            fields = [self.ui.form_device["node_id"], self.ui.form_device["parent"]]
                        return all(
                            val_func(f.value)
                            for f in fields
                            for val_func in f.validation.values()
                        )

                validator = FormValidator(self)
                ui.button("Apply", on_click=lambda: self.action_submit_form("add")).classes("flex-1").bind_enabled_from(validator, "is_valid")
                ui.button("Cancel", on_click=self.dialog_device_form.close).classes("flex-1")

        # --- Main Layout ---
        with ui.grid(columns="1fr", rows="6fr 4fr").classes("w-full h-[calc(100vh-5rem)]"):
            # Upper/Left: Tree View
            with ui.card().classes("h-full break-inside-avoid mb-2"):
                with ui.row().classes("items-center"):
                    ui.label("Device List").classes("text-2xl font-medium")
                    self.ui_input_search = ui.input("search").props("clearable")
                with ui.scroll_area().classes("h-full select-none"):
                    self.ui_tree = ui.tree(self.data_tree_structure, label_key="id", on_select=lambda e: self.action_select_row(e.value)).props("selected-color=blue")
                    self.ui_input_search.bind_value_to(self.ui_tree, "filter")

            # Lower/Right: Detail View
            with ui.card().classes("h-full break-inside-avoid mb-2"):
                with ui.row().classes("items-center"):
                    ui.label("Device Info").classes("text-2xl font-medium")
                    # Refresh Button
                    self.btn_refresh_info = ui.button(icon="refresh", on_click=lambda: self._api_send_command("get", {"id": (self.var_target_node or {}).get("device_data", {}).get("id")})).props("outline round").tooltip("Reload device status")

                with ui.scroll_area().classes("h-full"):
                    self.ui_markdown_detail = ui.markdown("")

                # Floating Menu
                with ui.row().classes("w-full"):
                    ui.space()
                    with ui.fab("menu", label="Menu", direction="up").props("vertical-actions-align=right"):
                        self.fab_about = ui.fab_action("settings", label="About", on_click=self.action_show_daemon_stat).tooltip("Tuya2MQTT daemon")
                        self.fab_wizard = ui.fab_action("auto_awesome", label="Wizard", on_click=self.dialog_bulk_menu.open).tooltip("Tinytuya Wizard")
                        self.fab_delete = ui.fab_action("delete", label="Delete", on_click=self.action_delete_device).tooltip("Delete selected device")
                        self.fab_edit = ui.fab_action("edit", label="Edit", on_click=lambda: self.action_prepare_form(is_editing=True)).tooltip("Edit selected device")
                        self.fab_add = ui.fab_action("add", label="Add", on_click=lambda: self.action_prepare_form(is_editing=False)).tooltip("Add a single device")

    def _setup_meta_tags(self):
        tags = [
            '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">',
            '<meta name="apple-mobile-web-app-capable" content="yes">',
            '<meta name="apple-mobile-web-app-status-bar-style" content="black">',
        ]
        for tag in tags:
            ui.add_head_html(tag)

    # ==========================================================================
    # Section 5: User Actions & Event Handlers
    # ==========================================================================

    # --- Tree Selection ---
    async def action_select_row(self, value, notify_user=True):
        self.var_selected_row_id = value
        self.var_target_node = None if value is None else self._util_find_node(self.ui_tree.props["nodes"], value)

        if self.var_target_node:
            self.ui_markdown_detail.set_content(self._util_dict_to_markdown(self.var_target_node["device_data"]))
            self.btn_refresh_info.enable()
            self.fab_edit.enable()
            self.fab_delete.enable()
            if notify_user:
                ui.notification(message=f"{value} selected", timeout=0.5)
        else:
            self.ui_markdown_detail.set_content("")
            self.btn_refresh_info.disable()
            self.fab_edit.disable()
            self.fab_delete.disable()

    # --- Daemon Status ---
    async def action_show_daemon_stat(self):
        self.event_daemon_ready.clear()
        await self._api_send_command("query", {"status": True})
        try:
            await asyncio.wait_for(self.event_daemon_ready.wait(), timeout=10)
            content = ""
            for key, value in self.data_daemon_stat.items():
                content += f"{key}: {value}\n"
            self.ui_code_daemon.set_content(content)
            self.dialog_daemon.open()
        except:
            ui.notify(f"Unable to retrieve daemon status", type="negative")

    # --- Snapshot & Reset ---
    async def action_make_snapshot(self):
        if self.data_snapshot:
            self.ui_lbl_confirm_title.set_text("Make a new snapshot?")
            result = await self.dialog_confirm
            if result == "Apply":
                filtered_snapshot = []
                for _snapshot in self.data_snapshot:
                    filtered_snapshot.append({
                        key: value for key, value in _snapshot.items()
                        if key not in {"status", "last_seen", "type"}
                    })
                await self._io_write_file(self.path_snapshot_json, filtered_snapshot)
        else:
            ui.notify("No registered devices", type="negative")

    async def action_reset_devices(self):
        self.ui_lbl_confirm_title.set_text("Remove all devices?")
        result = await self.dialog_confirm
        if result == "Apply":
            await self._api_send_command("query", {"reset": True})

    # --- Wizard Logic ---
    async def action_open_wizard(self):
        try:
            creds = json.loads(await self._io_read_file(self.path_tinytuya_json))
            self.form_wizard["user_code"].set_value(creds.get("user_code", ""))
        except Exception:
            pass
        if SystemConstants.WIZARD_RUNNING:
            self.form_wizard["user_code"].disable()
            self.btn_run_wizard.disable()
            self.wizard_status.set_text("Status: Running")
        else:
            self.form_wizard["user_code"].enable()
            self.btn_run_wizard.enable()
            self.wizard_status.set_text("Status: Ready")
        self.dialog_wizard.open()

    async def action_run_wizard(self):
        # Disable UI
        self.wizard_status.set_text("Status: Running")
        self.form_wizard["user_code"].disable()
        self.btn_run_wizard.disable()

        with ui.dialog().props("persistent") as _qr_code, ui.card():
            ui.label("Scan the QR code with SmartLife or TuyaSmart App")
            _qr_img = ui.image().style("min-width: 200px;")

        def qr_callback(url):
            if url:
                qr = qrcode.QRCode(border=1)
                qr.add_data(url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                _qr_img.set_source(img.get_image())
                _qr_code.open()
            else:
                _qr_code.close()

        # Execute Process
        try:
            SystemConstants.WIZARD_RUNNING = True
            loop = asyncio.get_event_loop()
            tuya = TuyaWizard(info_file=self.path_tinytuya_json)
            await loop.run_in_executor(None, lambda: tuya.login_auto(user_code=self.form_wizard["user_code"].value, qr_callback=qr_callback))
            tuyadevices = await loop.run_in_executor(None, lambda: tuya.fetch_devices())

            result = await loop.run_in_executor(None, lambda: tinytuya.scanner.poll_and_display(tuyadevices, snapshot=False))
            iplist = {}
            for itm in result:
                if "gwId" in itm and itm["gwId"]:
                    gwid = itm["gwId"]
                    ip = itm["ip"] if "ip" in itm and itm["ip"] else ""
                    ver = itm["version"] if "version" in itm and itm["version"] else ""
                    iplist[gwid] = (ip, ver)
            for k in range(len(tuyadevices)):
                gwid = tuyadevices[k]["id"]
                if gwid in iplist:
                    tuyadevices[k]["ip"] = iplist[gwid][0]
                    tuyadevices[k]["version"] = iplist[gwid][1]
            no_key_list = []
            no_parent_list = {}
            for tuyadevice in tuyadevices:
                if tuyadevice.get("key", "") == "":
                    no_key_list.append({"id": tuyadevice["id"], "name": tuyadevice["name"]})
                if "node_id" in tuyadevice and "sub" in tuyadevice and tuyadevice["sub"] and tuyadevice.get("parent", "") == "":
                    if "key" in tuyadevice and tuyadevice["key"]:
                        if tuyadevice["key"] not in no_parent_list:
                            no_parent_list[tuyadevice["key"]] = {"id": [], "name": []}
                        no_parent_list[tuyadevice["key"]]["id"].append(tuyadevice["id"])
                        no_parent_list[tuyadevice["key"]]["name"].append(tuyadevice["name"])
        except Exception as e:
            ui.notify(str(e), type="negative")
        finally:
            SystemConstants.WIZARD_RUNNING = False

        def selected(no_key, value):
            for tuyadevice in tuyadevices:
                if tuyadevice["id"] == no_key["id"]:
                    tuyadevice["key"] = value
                    no_key["text"].set_text(f"{no_parent_list[value]['name'][0]}...")
                    for child in no_key["text"]:
                        if isinstance(child, ui.tooltip):
                            no_key["text"].remove(child)
                    no_key["text"].tooltip(json.dumps(no_parent_list[value]["name"])[2:-2])
                if tuyadevice["id"] in no_parent_list[value]["id"]:
                    tuyadevice["parent"] = no_key["id"]
        with ui.dialog().props("persistent") as get_key, ui.card():
            ui.label("Manually matching the local key").classes("text-2xl font-medium")
            for no_key in no_key_list:
                ui.label(f"{no_key['id']} [{no_key['name']}]")
                no_key["select"] = ui.select(options=list(no_parent_list), value=list(no_parent_list)[0], on_change=lambda e: selected(no_key, e.value))
                no_key["text"] = ui.label("")
                selected(no_key, list(no_parent_list)[0])
                ui.separator()
            ui.button("Close", on_click=get_key.close)
        if no_key_list and no_parent_list:
            self.dialog_wizard.open()
            await get_key
        await self._io_write_file(self.path_devices_json, json.dumps(tuyadevices, indent=4))

        # Re-enable UI
        self.wizard_status.set_text("Status: Ready")
        self.form_wizard["user_code"].enable()
        self.btn_run_wizard.enable()
        self.dialog_wizard.close()
        self.dialog_wizard_end.open()

    # --- Editor Logic ---
    def _util_scroll_editor(self, editor, pos):
        ui.run_javascript(f"""
            function sleep(ms) {{ return new Promise(resolve => setTimeout(resolve, ms)); }}
            const cme = getElement({editor.id}).editor;
            async function editor_scroller() {{
                while(true) {{
                    await sleep(100);
                    if(cme.viewState.editorHeight) break;
                }}
                cme.focus();
                cme.dispatch({{ selection: {{ anchor: {pos}, head: {pos} }}, scrollIntoView: true }});
            }}
            editor_scroller();
        """)

    async def action_open_editor(self):
        content = await self._io_read_file(self.path_devices_json) or ""
        self.var_json_original = content
        self.dialog_editor.open()
        self.ui_editor.value = content
        self.ui_editor.update()
        self._util_scroll_editor(self.ui_editor, 0)
        self.flag_json_dirty = True
        self.action_check_editor_state()

    def action_check_editor_state(self):
        current_val = self.ui_editor.value
        if current_val != self.var_json_original:
            if not self.flag_json_dirty:
                self.flag_json_dirty = True
                self.dialog_editor.props("persistent")
                self.btn_save_editor.enable()
                self.dialog_editor.update()
        else:
            if self.flag_json_dirty:
                self.flag_json_dirty = False
                self.dialog_editor.props("persistent=False")
                self.btn_save_editor.disable()
                self.dialog_editor.update()

    async def action_cancel_editor(self):
        if self.ui_editor.value == self.var_json_original:
            self.dialog_editor.close()
        else:
            self.dialog_editor.open()
            self.ui_lbl_confirm_title.set_text("Discard changes?")
            result = await self.dialog_confirm
            if result == "Apply":
                self.dialog_editor.close()

    async def action_save_editor(self):
        try:
            json.loads(self.ui_editor.value)
        except json.JSONDecodeError as e:
            ui.notify(e, type="negative")
            self._util_scroll_editor(self.ui_editor, e.pos)
            return

        if self.ui_editor.value == self.var_json_original:
            ui.notify("Unchanged", type="negative")
            return

        self.ui_lbl_confirm_title.set_text("Save changes?")
        result = await self.dialog_confirm
        if result == "Apply":
            if await self._io_write_file(self.path_devices_json, self.ui_editor.value):
                self.dialog_editor.close()

    # --- Device Form (Add/Edit/Delete) Logic ---
    async def action_prepare_form(self, is_editing: bool):
        self.flag_is_editing_mode = is_editing

        # Initialize Form Fields
        for key, input_field in self.form_device.items():
            if is_editing and self.var_target_node:
                value = self.var_target_node["device_data"].get(key, "")
                input_field.set_value(value)
            else:
                input_field.set_value("")
            input_field.props("error=False")
            input_field.update()

        # Set Title and Type
        if is_editing and self.var_target_node:
            self.ui_lbl_form_title.set_text("Modify device information")
            if "node_id" in self.var_target_node["device_data"]:
                self.ui_rad_dev_type.set_value("Zigbee/BLE")
            else:
                self.ui_rad_dev_type.set_value("WiFi")
        else:
            self.ui_lbl_form_title.set_text("Add a new device")
            self.ui_rad_dev_type.set_value("WiFi")

        self.dialog_device_form.open()

    async def action_delete_device(self):
        if self.var_target_node:
            self.ui_lbl_confirm_title.set_text("Delete selected device?")
            result = await self.dialog_confirm
            if result == "Apply":
                del_payload = {"id": self.var_target_node["device_data"]["id"]}
                await self._api_send_command("delete", del_payload)
                ui.notification(message="Device deleted", timeout=0.5)
                self.logger.info(f"Message published: {del_payload}")

    async def action_submit_form(self, mode):
        device_type_val = self.ui_rad_dev_type.value

        # Generate Auto ID
        auto_id = self.form_device["ip"].value if device_type_val == "WiFi" else self.form_device["node_id"].value
        if self.form_device["name"].value:
            auto_id = f"{auto_id}_{self.form_device['name'].value}"

        self.ui_lbl_confirm_title.set_text("Proceed?")
        result = await self.dialog_confirm

        if result == "Apply":
            # Logic for Edit Mode (Delete existing first)
            if self.flag_is_editing_mode:
                if not self.var_target_node:
                    return

                mode = "modified"
                del_payload = {"id": self.var_target_node["device_data"]["id"]}
                self.event_snapshot_ready.clear()
                await self._api_send_command("delete", del_payload)
                try:
                    await asyncio.wait_for(self.event_snapshot_ready.wait(), timeout=10)
                except:
                    ui.notify(f"Unable to connect", type="negative")
                    self.dialog_device_form.close()
                    return
            else:
                mode = "added"

            # Construct Payload
            add_payload = {"id": self.form_device["id"].value if self.form_device["id"].value else auto_id }
            if self.form_device["name"].value:
                add_payload["name"] = self.form_device["name"].value

            keys_to_add = ["ip", "key", "version"] if device_type_val == "WiFi" else ["node_id", "parent"]
            for k in keys_to_add:
                add_payload[k] = self.form_device[k].value

            await self._api_send_command("add", add_payload)
            ui.notification(message=f"Device {mode}", timeout=0.5)
            self.logger.info(f"Message published: {add_payload}")
            self.dialog_device_form.close()

    # --- Bulk Add Logic ---
    async def action_open_bulk_adder(self):
        try:
            cloud_list = json.loads(await self._io_read_file(self.path_devices_json))
        except Exception as e:
            ui.notify(message=e, type="negative")
            return

        if not cloud_list: return

        with ui.dialog() as dialog_bulk_tree, ui.card():
            list_valid, list_invalid, list_added = [], [], []

            for device in cloud_list:
                # Check for necessary fields
                flags = {k: device.get(k, "") for k in ["ip", "key", "version", "node_id", "parent"]}
                dev_type = "No informations"

                if flags["ip"] and flags["key"] and flags["version"]: dev_type = "WiFi device"
                elif flags["node_id"] and flags["parent"]: dev_type = "Sub device"

                label = f"{device.get('id', 'NO_ID')} [{device.get('name', 'N/A')}]"
                item = {"id": label, "description": dev_type}

                if dev_type in {"WiFi device", "Sub device"}:
                    if device.get("id") in self.data_device_map:
                        list_added.append(item)
                    else:
                        list_valid.append(item)
                else:
                    list_invalid.append(item)

            with ui.scroll_area().classes("h-[calc(70vh)]").style("min-width: 300px;"):
                bulk_tree = ui.tree([
                    {"id": "Valid", "children": list_valid},
                    {"id": "Invalid", "children": list_invalid, "disabled": True},
                    {"id": "Added", "children": list_added, "disabled": True},
                ], label_key="id", tick_strategy="leaf").expand()

                bulk_tree.add_slot("default-body", """<span :props="props">{{ props.node.description }}</span>""")

            with ui.row():
                ui.button("Add", on_click=lambda: self._action_process_bulk_add(bulk_tree, dialog_bulk_tree, cloud_list))
                ui.button("Cancel", on_click=dialog_bulk_tree.close)
        dialog_bulk_tree.open()

    async def _action_process_bulk_add(self, tree_element, dialog_element, cloud_list):
        selected_labels = [item for item in tree_element.props["ticked"] if item != "Valid"]
        selected_ids = [label.split(" ")[0] for label in selected_labels]

        if not selected_ids:
            ui.notify("No devices selected", type="negative")
            return

        devices_to_add = []
        for dev in cloud_list:
            if dev.get("id") in selected_ids:
                # Filter keys
                filtered = {k: v for k, v in dev.items() if k in ["id", "name", "ip", "key", "version", "node_id", "parent"]}
                devices_to_add.append(filtered)

        await self._api_send_command("add", devices_to_add)
        if self._logic_update_device_list(devices_to_add):
            await self._ui_refresh_tree()

        ui.notify(f"Published 'add' command for {len(devices_to_add)} devices")
        dialog_element.close()


# ==============================================================================
# Entry Point & Argument Parsing
# ==============================================================================
GLOBAL_CONFIG = None
def main():
    global GLOBAL_CONFIG
    parser = argparse.ArgumentParser()
    arg_config_list = [
        {"name": ["root_topic"], "default": "tuya2mqtt", "type": str},
        {"name": ["broker", "hostname"], "default": "localhost", "type": str},
        {"name": ["broker", "port"], "default": 1883, "type": int},
        {"name": ["broker", "username"], "default": "", "type": str},
        {"name": ["broker", "password"], "default": "", "type": str},
        {"name": ["ui", "port"], "default": 0, "type": int},
        {"name": ["ui", "json", "devices"], "default": "./devices.json", "type": str},
        {"name": ["ui", "json", "snapshot"], "default": "./device_snapshot.json", "type": str},
        {"name": ["ui", "json", "tinytuya"], "default": "./tinytuya.json", "type": str},
    ]
    _, GLOBAL_CONFIG = Tuya2MQTTBridge.config_parser(parser, SystemConstants.DEFAULT_CONF_FILE, arg_config_list)
    if GLOBAL_CONFIG["ui"]["port"]:
        ui.run(favicon="🚀", port=GLOBAL_CONFIG["ui"]["port"])
    else:
        print("Tuya2MQTT launched without UI")
        RuntimeContext.BRIDGE = Tuya2MQTTBridge(**GLOBAL_CONFIG)
        RuntimeContext.BRIDGE.start()

@ui.page("/")
@ui.page("/{dummy}")
async def ui_entry_page():
  """Web UI entry point. Redirects to the timestamp base address for iOS 26 clients."""
  ui.add_head_html("""
  <script>
  var currentPath = window.location.pathname;
  if (currentPath === "/") {
    var userAgent = navigator.userAgent;
    if (userAgent.includes("iPhone") && userAgent.includes("Version/26")) {
      window.location.replace("/" + Date.now());
    }
  }
  </script>
  """)
  root = Tuya2MqttUi(**GLOBAL_CONFIG, **RuntimeContext.SHARED)
  await RuntimeContext.SHARED["mqtt_init_event"].wait()
  await root._handle_topic_registration()

@app.on_startup
async def ui_start_up():
  """Application startup handler: Initializes Bridge and MQTT worker."""
  RuntimeContext.SHARED["mqtt_init_event"].clear()
  RuntimeContext.BRIDGE = Tuya2MQTTBridge(foreground=False, **GLOBAL_CONFIG, **RuntimeContext.SHARED)
  RuntimeContext.TASKS.append(asyncio.create_task(RuntimeContext.BRIDGE.run(signal_handler=False)))
  RuntimeContext.TASKS.append(asyncio.create_task(Tuya2MqttUi._mqtt_worker(**GLOBAL_CONFIG, **RuntimeContext.SHARED)))

@app.on_shutdown
async def ui_shut_down():
  """Application shutdown handler: Cleans up tasks and Bridge."""
  await RuntimeContext.BRIDGE._terminator(True)
  for task in RuntimeContext.TASKS:
    task.cancel()
  await asyncio.gather(*RuntimeContext.TASKS, return_exceptions=True)

if __name__ in {"__main__", "__mp_main__"}:
  main()
