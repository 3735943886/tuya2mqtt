FROM python:slim
WORKDIR /app
RUN apt update && apt install -y git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tuya2mqtt.py ./
CMD ["python", "./tuya2mqtt.py", "foreground", "/config/tuya2mqtt.conf"]
