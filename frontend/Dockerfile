FROM python:slim
WORKDIR /app
RUN apt update && apt install -y --no-install-recommends python3-netifaces && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /config
COPY tuya2mqtt_ui.py ./
CMD ["python", "./tuya2mqtt_ui.py", "--config", "/config/tuya2mqtt.conf", "--ui-port", "8373", "--ui-json-devices", "/config/devices.json", "--ui-json-snapshot", "/config/device_snapshot.json", "--ui-json-tinytuya", "/config/tinytuya.json"]
