import os
from pathlib import Path

VCON_STORAGE_TYPE = os.getenv("VCON_STORAGE_TYPE", "redis")
VCON_STORAGE_URL = os.getenv("STORAGE_URL", "redis://localhost")
QUEUE_DB_URL = os.getenv("STORAGE_URL", VCON_STORAGE_URL)
STATE_DB_URL = os.getenv("STATE_DB_URL", VCON_STORAGE_URL)
REST_URL = os.getenv("REST_URL", "http://localhost:8000")
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
LOGGING_CONFIG_FILE = os.getenv("LOGGING_CONFIG_FILE", Path(__file__).parent / 'logging.conf')
LAUNCH_VCON_API = os.getenv("LAUNCH_VCON_API", True)
LAUNCH_ADMIN_API = os.getenv("LAUNCH_ADMIN_API", True)
NUM_WORKERS = os.getenv("NUM_WORKERS", os. cpu_count())

# parse out optional weights from name for each queue
WORK_QUEUES = {}
queue_tokens = os.getenv("WORK_QUEUES", "").split()
for token in queue_tokens:
  name_weight = token.split(":")
  name = name_weight[0]
  if(len(name_weight) == 1 or
    name_weight[1] is None or
    name_weight[1] == ""):
    weight = 1
  elif(len(name_weight) == 2):
    try:
      weight = int(name_weight[1])
    except ValueError as e:
      raise Exception("WORK_QUEUE weights must be an integer value for queue: {}".format(name))
  else:
    raise Exception(
      "Invalid WORK_QUEUE token: {} should be name:weight where name is a string and weight is an integer".
      format(token))
  WORK_QUEUES[name] = {"weight": weight}

