import asyncio

tuya2mqtt_queue = asyncio.Queue()
@service(supports_response = 'only')
def tuya2mqtt_check_daemon(topic = 'tuya2mqtt/device/query'):
  """yaml
name: Query Tuya2MQTT Daemon Status
description: To query the daemon's status, publish a payload to the tuya2mqtt/device/query topic.
fields:
  topic:
    example: tuya2mqtt/device/query
"""
  global tuya2mqtt_queue
  empty_queue(tuya2mqtt_queue)
  mqtt.publish(topic=topic, payload='{"status": 1}')
  try:
    response = queue_timeout(tuya2mqtt_queue.get, 5)
    tuya2mqtt_queue.task_done()
    return response
  except TimeoutError:
    return {'tuya2mqtt': 'Unable to connect.'}

@mqtt_trigger('tuya2mqtt/log/daemon')
def daemon_recv(payload_obj, **kwargs):
  global tuya2mqtt_queue
  empty_queue(tuya2mqtt_queue)
  tuya2mqtt_queue.put_nowait(payload_obj)

def empty_queue(q):
  while not q.empty():
    try:
      q.get_nowait()
      q.task_done()
    except asyncio.QueueEmpty:
      break

@pyscript_compile
async def queue_timeout(q, timeout):
  return await asyncio.wait_for(q(), timeout)
