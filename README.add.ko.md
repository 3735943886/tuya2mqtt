### 여러 Tuya 장치를 한 번에 추가하는 가장 쉬운 방법

이 방법은 `tinytuya`의 `wizard` 기능을 사용해 로컬 네트워크의 장치 정보를 자동으로 탐색하고, 생성된 `devices.json` 파일을 `tuya2mqtt`에 일괄적으로 등록하는 방식입니다.

#### 1\. `devices.json` 파일 생성

먼저, 터미널에서 `tinytuya` 위자드를 실행하여 장치 정보를 담은 `devices.json` 파일을 만듭니다.

```sh
python -m tinytuya wizard
```

위자드를 실행하면 몇 가지 질문이 나옵니다. 모든 질문에 `yes` 또는 `y`를 입력하여 진행하세요.

  * `Download DP Name mappings?`
  * `Poll local devices?`

이 과정을 완료하면, 현재 디렉터리에 `devices.json` 파일이 생성됩니다. 이 파일에는 로컬 네트워크에서 발견된 모든 Tuya Wi-Fi 장치와 허브에 연결된 Zigbee/BLE 장치 정보가 담겨 있습니다.

#### 2\. `devices.json`으로 장치 일괄 추가

`devices.json` 파일을 열어보면, 장치 목록이 JSON 형태로 저장되어 있습니다. `tuya2mqtt`에 이 장치들을 등록하려면, `devices.json`의 각 장치 정보를 `tuya2mqtt/device/add` 토픽으로 발행해야 합니다.

이때, **Wi-Fi 장치를 먼저 등록하여 허브(parent) 역할을 하도록 하고, 그 후에 서브 디바이스(Zigbee/BLE)를 등록하는 순서가 중요합니다.**

아래 파이썬 스크립트를 작성하여 `devices.json`의 내용을 자동으로 처리할 수 있습니다.

```python
import json
import time
from paho.mqtt import publish

# tinytuya wizard로 생성된 devices.json 파일을 불러옵니다.
with open('./devices.json', 'r') as f:
    devices = json.load(f)

# 1. Wi-Fi 장치(허브 포함)를 먼저 등록합니다.
for device in devices:
    if 'node_id' not in device or device['node_id'] == '':
        publish.single('tuya2mqtt/device/add', json.dumps(device), hostname = 'localhost')

# 허브 초기화가 완료 되기를 기다립니다.
time.sleep(5)

# 2. 서브 디바이스(Zigbee/BLE)를 나중에 등록합니다.
for device in devices:
    if 'node_id' in device and device['node_id'] != '':
        publish.single('tuya2mqtt/device/add', json.dumps(device), hostname = 'localhost')

print("All devices from devices.json have been sent to tuya2mqtt.")
```

#### 3\. 장치 모니터링 및 제어

이제 장치가 모두 등록되었으니, MQTT를 통해 장치를 모니터링하고 제어할 수 있습니다.

  * **장치 상태 모니터링**: `tuya2mqtt/data/command`와 `tuya2mqtt/data/status` 토픽을 subscribe하여 Tuya 장치의 실시간 업데이트를 받을 수 있습니다.
  * **장치 제어**: `tuya2mqtt/device/set` 및 `tuya2mqtt/device/get` 토픽을 사용해 특정 장치에 명령을 보내거나 현재 상태를 요청할 수 있습니다.
