import asyncio

DAEMON_STATUS = {
    'status': None,
    'cond': asyncio.Condition(),
}

@service(supports_response = 'only')
def tuya2mqtt_check_daemon(device_snapshot=True, topic='tuya2mqtt/device/query'):
    """yaml
name: Query Tuya2MQTT Daemon Status
description: To query the daemon's status, publish a payload to the tuya2mqtt/device/query topic.
fields:
  device_snapshot:
    example: true
  topic:
    example: tuya2mqtt/device/query
"""
    global DAEMON_STATUS
    async with DAEMON_STATUS['cond']:
        mqtt.publish(topic=topic, payload='{"status": true}' if device_snapshot else '{"status": false}')
        wait_for(DAEMON_STATUS['cond'].wait, 5)
        return DAEMON_STATUS['status']

@mqtt_trigger('tuya2mqtt/log/daemon')
def daemon_recv(payload_obj, **kwargs):
    global DAEMON_STATUS
    async with DAEMON_STATUS['cond']:
        DAEMON_STATUS['status'] = payload_obj
        DAEMON_STATUS['cond'].notify_all()

@pyscript_compile
async def wait_for(func, timeout):
  return await asyncio.wait_for(func(), timeout)
