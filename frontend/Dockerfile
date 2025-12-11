FROM python:slim
WORKDIR /app
RUN apt update && apt install -y --no-install-recommends python3-netifaces && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /config
COPY tuya2mqtt_ui.py ./
ENV CONFIG="/config/tuya2mqtt.conf"
ENV UI_PORT=8373
ENV UI_JSON_DEVICES="/config/devices.json"
ENV UI_JSON_SNAPSHOT="/config/device_snapshot.json"
ENV UI_JSON_TINYTUYA="/config/tinytuya.json"
CMD ["python", "./tuya2mqtt_ui.py"]
