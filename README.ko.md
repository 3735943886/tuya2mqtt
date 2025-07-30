# tuya2mqtt: Tuya Devices to MQTT Bridge
[English](README.md)

`tuya2mqtt`는 Tuya 스마트 장치를 MQTT 브로커와 연결해 주는 Python 스크립트입니다. **`add` 토픽으로 등록된 Tuya 장치와 24시간 TCP 연결을 유지하여 상태 변화가 발생하면 즉시 MQTT로 발행하고, MQTT를 통해 장치의 상태를 제어할 수 있는 백엔드(backend) 역할을 합니다.**
`tuya2mqtt`는 오버헤드와 종속성을 최소화하여 성능을 극대화하기 위해 컨테이너 대신 `python-daemon`으로 데모나이즈되어 있습니다.
>
-----

## 주요 기능

  * **Tuya 장치 제어**: MQTT를 통해 Tuya 장치의 상태를 설정합니다.
  * **상태 모니터링**: Tuya 장치로부터 실시간으로 상태 업데이트(DPS)를 받아 MQTT로 발행합니다.
  * **멀티스레딩**: 각 장치를 개별 스레드에서 처리하여 안정적인 병렬 통신을 지원합니다.
  * **데몬 모드**: 백그라운드에서 안정적으로 실행되며, PID 파일을 관리해 중복 실행을 방지합니다.
  * **동적 장치 관리**: MQTT 메시지를 통해 실행 중에 장치를 추가하거나 삭제할 수 있습니다.
  * **다양한 장치 지원**: Wi-Fi, Zigbee, BLE 등 다양한 Tuya 장치들을 제어할 수 있습니다.

-----

## 설치

`tuya2mqtt`를 실행하려면 Python 3.8 이상과 아래 라이브러리들이 필요합니다.

```sh
pip install tinytuya paho-mqtt python-daemon
```

-----

## 사용법
### **Daemon 모드 (기본)**

* **시작:** 스크립트를 백그라운드 데몬으로 실행합니다. 터미널을 닫아도 계속 실행됩니다.
    ```sh
    python tuya2mqtt.py start
    ```

* **중지:** 실행 중인 데몬을 안전하게 중지합니다.
    ```sh
    python tuya2mqtt.py stop
    ```

* **재시작:** 현재 데몬을 중지하고 새 인스턴스를 시작합니다.
    ```sh
    python tuya2mqtt.py restart
    ```

### **포그라운드 및 디버그 모드 (전문가용)**

* **포그라운드:** 포그라운드 모드로  실행합니다. **systemd**와 같은 서비스 관리자와 함께 사용하기에 유용합니다.
    ```sh
    python tuya2mqtt.py foreground
    ```

* **디버그:** 포그라운드 모드로 실행하며, 실시간 로그를 터미널에 출력합니다. **개발** 및 **문제 해결**에 사용하기 좋습니다.
    ```sh
    python tuya2mqtt.py debug
    ```

-----

## 설정

스크립트를 처음 실행하면 `tuya2mqtt.conf` 파일이 자동으로 생성됩니다. 이 파일을 수정하여 MQTT 브로커 정보를 변경할 수 있습니다.

**`tuya2mqtt.conf` 예시:**

```json
{
  "broker": {
    "host": "localhost",
    "port": 1883
  },
  "login": {
    "username": "",
    "password": ""
  },
  "topic": {
    "subscribe": {
      "add": "tuya2mqtt/device/add",
      "delete": "tuya2mqtt/device/delete",
      "query": "tuya2mqtt/device/query",
      "set": "tuya2mqtt/device/set",
      "get": "tuya2mqtt/device/get",
      "send": "tuya2mqtt/device/send"
    },
    "publish": {
      "command": "tuya2mqtt/data/command",
      "status": "tuya2mqtt/data/status",
      "daemon": "tuya2mqtt/log/daemon",
      "info": "tuya2mqtt/log/info",
      "error": "tuya2mqtt/log/error"
    }
  }
}
```

-----

## MQTT를 통한 장치 관리

### 1\. 장치 추가

[tinytuya를 활용하여 장치를 한꺼번에 추가하기](README.add.ko.md)

새로운 Tuya 장치를 추가하려면 `tuya2mqtt/device/add` 토픽에 아래와 같은 JSON 페이로드를 발행하세요.

  * **Tuya Wi-Fi 장치**: `id`, `ip`, `key`, `version` 정보가 필요합니다. `name`은 선택사항 입니다.
    ```json
    {
      "id": "ebed836691xxxxxxb",
      "ip": "192.168.1.100",
      "key": "b4e4776e1f0e21a2",
      "version": 3.3,
      "name": "My_Smart_Plug"
    }
    ```
  * **Tuya Zigbee/BLE 장치**: `id`, `node_id`, `parent`(허브 ID) 정보가 필요합니다. `name`은 선택사항 입니다.
    ```json
    {
      "id": "ebed836691xxxxxxb",
      "node_id": "01020202111111112222",
      "parent": "ebed836691xxxxxxb",
      "name": "My_Sub_Device"
    }
    ```

### 2\. 장치 제어 및 상태 조회

장치를 제어하거나 상태를 요청하려면 `tuya2mqtt/device/set` 또는 `tuya2mqtt/device/get` 토픽에 페이로드를 발행합니다. **`id` 또는 `name`을 사용하여 특정 장치를 지정할 수 있습니다.**

  * **상태 설정**: `tuya2mqtt/device/set` 토픽으로 변경할 `data`(DPS)를 포함하는 JSON 페이로드를 발행합니다.
      * `id`를 사용하는 경우:
        ```json
        {
          "id": "ebed836691xxxxxxb",
          "data": {
            "1": true
          }
        }
        ```
      * `name`을 사용하는 경우:
        ```json
        {
          "name": "My_Smart_Plug",
          "data": {
            "1": true
          }
        }
        ```
  * **상태 조회**: `tuya2mqtt/device/get` 토픽으로 조회할 장치의 `id` 또는 `name`을 포함하는 JSON 페이로드를 발행합니다.
      * `id`를 사용하는 경우:
        ```json
        {
          "id": "ebed836691xxxxxxb"
        }
        ```
      * `name`을 사용하는 경우:
        ```json
        {
          "name": "My_Smart_Plug"
        }
        ```
  * **페이로드 직접 전송 (고급 사용)**: `tuya2mqtt/device/send` 토픽으로 명령 코드(`command`, 정수형)와 데이터를 포함하는 페이로드를 발행합니다. `set` 명령으로 제어할 수 없는 특정 기능에 사용됩니다.
      * `id`를 사용하는 경우:
        ```json
        {
          "id": "ebed836691xxxxxxb",
          "command": 18,
          "data": [1, 2, 3]
        }
        ```
### 3\. 장치 모니터링

`tuya2mqtt`는 장치 모니터링을 위해 두 가지 주요 토픽을 제공합니다. 이 토픽들을 subscribe하면 장치의 상태 변화와 명령 이력을 실시간으로 확인할 수 있습니다.

  * `tuya2mqtt/data/command`: 이 토픽은 **Tuya 장치가 스스로 상태 변화를 보고할 때** 발행됩니다. 예를 들어, 스마트 버튼을 눌렀을 때, 스위치 상태가 물리적으로 변경되었을 때처럼 장치에서 이벤트가 발생하면 즉시 발행되는 토픽입니다.
  * `tuya2mqtt/data/status`: 이 토픽은 **`tuya2mqtt/device/get` 토픽 대한 응답이나 주기적인 상태 보고**를 위해 발행됩니다.

해당 토픽을 subscribe하면 다음과 같은 형식으로 데이터를 받을 수 있습니다:

```bash
$ mosquitto_sub -t tuya2mqtt/data/command
```

```json
{"id": "ebed836691xxxxxxb", "name": "My_Smart_Plug", "data": {"1": true}}
{"id": "ebed836691xxxxxxb", "name": "My_Smart_Plug", "data": {"1": false}}
```

**`id`** 필드는 Wi-Fi 디바이스의 경우 `id`를, 서브 디바이스의 경우 `node_id`를 나타냅니다.

### 4\. 데몬 상태 조회 및 종료

데몬의 상태를 조회하거나 연결된 모든 장치를 종료하려면 `tuya2mqtt/device/query` 토픽에 아래 페이로드를 발행합니다.

  * **데몬 상태 조회**:

    ```json
    {
      "status": true
    }
    ```

    이 메시지를 보내면 `tuya2mqtt/log/daemon` 토픽으로 응답이 발행됩니다.

  * **모든 장치 연결 재설정**:

    ```json
    {
      "reset": true
    }
    ```

    이 명령은 현재 연결된 모든 장치와의 통신을 종료합니다. 데몬 프로세스 자체는 계속 실행되지만, 장치를 다시 사용하려면 `add` 명령을 통해 다시 연결해야 합니다.

  * **모든 장치 연결 종료 및 데몬 종료**:

    ```json
    {
      "stop": true
    }
    ```

    이 명령은 `python tuya2mqtt.py stop` 명령과 동일하게 동작합니다. 모든 장치와의 연결을 끊고, 데몬을 완전히 종료합니다.

-----

## 중요 사항 및 권장 사항

이 스크립트는 최대한 기능을 간소화하고 **안정성**에 초점을 맞춰 작성되었습니다. 그러나 몇 가지 외부 의존성에 대한 이해와 주의가 필요합니다.

  * **외부 모듈 의존성**: `tuya2mqtt`는 `tinytuya`와 `mqtt` 모듈을 100% 신뢰하며 작동합니다. 모듈의 예기치 않은 업데이트는 스크립트의 안정성에 영향을 줄 수 있으므로, 가능하면 **모듈 버전을 안정적으로 유지**하는 것을 권장합니다.
      * **[tinytuya](https://github.com/jasonacox/tinytuya)**
      * **[paho-mqtt](https://github.com/eclipse/paho.mqtt.python)**
  * **네트워크 자원**: Tuya 장치 수가 많아질수록 **많은 TCP 연결이 `keep-alive` 상태로 유지**됩니다. 이로 인해 라우터 자원에 상당한 부하가 발생할 수 있으니, 충분한 네트워크 자원을 갖춘 라우터를 사용해야 합니다.
  * **MQTT 브로커 환경**: 이 스크립트는 수많은 장치가 연결되고 통신이 빈번해질수록 MQTT 브로커에 부하가 걸릴 수 있습니다. **최대 성능**을 위해 TLS(Transport Layer Security)와 같은 추가적인 보안 기능을 사용하지 않도록 설계되었습니다. 따라서 외부에서 브로커에 직접 접속하는 것을 금지하여 보안을 확보하고, 브로커를 **`localhost`에 직접 운용**하는 것을 권장합니다.
  * **파일 디스크립터**: 스크립트는 시작 시 파일 디스크립터(FD) 제한을 최댓값으로 설정합니다. 이는 많은 장치와의 연결을 동시에 처리하기 위함입니다.
