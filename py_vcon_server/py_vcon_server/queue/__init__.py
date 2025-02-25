"""
   Interface for managing job queues and in progress jobs.

   DB keys:

   "jobid" last job ID used new obtained via INCR
   "queues" hash of names of queues
   "queue:<queue_name" list of jobs
   "inprogress" hash of job ids for in progress jobs
"""
import asyncio
import typing
import json
import py_vcon_server.db.redis.redis_mgr
import py_vcon_server.logging_utils

logger = py_vcon_server.logging_utils.init_logger(__name__)

JOB_ID_KEY = "jobid"
QUEUE_NAMES_KEY = "queues"
IN_PROGRESS_JOBS_KEY = "inprogress"
QUEUE_NAME_PREFIX = "queue:"

JOB_QUEUE = None

class EmptyJobQueue(Exception):
  """ Raised when trying to pop a job from an empty queue """


class QueueDoesNotExist(Exception):
  """ Raised when attempting to operate on a queue which does not exist. """

class JobDoesNotExist(Exception):
  """ Raised when attempting to operate on an in progress job which does not exist. """


class JobQueue():
  def __init__(self, redis_url: str):
    logger.info("connecting JobQueue redis_mgr")
    self._redis_mgr = py_vcon_server.db.redis.redis_mgr.RedisMgr(redis_url)
    self._redis_mgr.create_pool()

    # we can gain some optimization by registering all of the Lua scripts here
    redis_con = self._redis_mgr.get_client()

    #keys = [ QUEUE_NAMES_KEY ]
    #args = [ name ]
    lua_script_create_new_queue = """
    if redis.call("SISMEMBER", KEYS[1], ARGV[1]) == 0 then
      local num_added = redis.call("SADD", KEYS[1],  ARGV[1])
      local num_queues = redis.call("SCARD", KEYS[1])
      return num_queues
    else
      return -1
    end
    """
    self._do_lua_create_new_queue = redis_con.register_script(lua_script_create_new_queue)

    # KEYS = [ QUEUE_NAMES_KEY, QUEUE_NAME_PREFIX + name ]
    # ARGV = [ name ]
    lua_script_delete_queue = """
    if redis.call("SISMEMBER", KEYS[1], ARGV[1]) == 1 then
      -- queue name is in the queue list, remove it
      redis.call("SREM", KEYS[1], ARGV[1])

      -- get contents of list
      local jobs = redis.call("LRANGE", KEYS[2], 0, -1)

      -- delete list
      redis.call("DEL", KEYS[2])

      return jobs
    else
      -- error queue does not exist in the queue list
      return -1
    end
    """
    self._do_lua_delete_queue = redis_con.register_script(lua_script_delete_queue)

    # KEYS = [ QUEUE_NAMES_KEY, QUEUE_NAME_PREFIX + name]
    # ARGV = [ name, vcon_uuids]
    lua_script_push_vcon_uuid_queue_job = """
    if redis.call("SISMEMBER", KEYS[1], ARGV[1]) == 1 then
      -- add job to end of list
      local num_jobs = redis.call("RPUSH", KEYS[2], ARGV[2])
      return num_jobs
    else
      -- error queue does not exist
      return -1
    end
    """
    self._do_lua_push_vcon_uuid_queue_job = redis_con.register_script(lua_script_push_vcon_uuid_queue_job)

    # KEYS = [ QUEUE_NAMES_KEY, QUEUE_NAME_PREFIX + name ]
    # ARGV = [ name ]
    lua_script_get_queue_jobs = """
    -- if the queue exists
    if redis.call("SISMEMBER", KEYS[1], ARGV[1]) == 1 then
      -- get contents of list
      local jobs = redis.call("LRANGE", KEYS[2], 0, -1)
      return jobs
    else
      -- error queue does not exist
      return -1
    end
    """
    self._do_lua_get_queue_jobs = redis_con.register_script(lua_script_get_queue_jobs)

    # KEYS = [ QUEUE_NAMES_KEY, QUEUE_NAME_PREFIX + name, JOB_ID_KEY, IN_PROGRESS_JOBS_KEY ]
    # ARGS = [ name, server_key ]
    lua_script_pop_queued_job = """
    -- if the queue exists
    if redis.call("SISMEMBER", KEYS[1], ARGV[1]) == 1 then
      local queue_job = redis.call("LPOP", KEYS[2])
      if queue_job then
        -- get next available job id
        local new_job_id = redis.call("INCR", KEYS[3])
        local time = redis.call("TIME")

        -- build an in progress object
        local in_progress_job = {}
        in_progress_job["jobid"] = new_job_id
        in_progress_job["queue"] = ARGV[1]
        in_progress_job["server"] = ARGV[2]
        in_progress_job["start"] = time[1] .. "." .. time[2]
        in_progress_job["job"] = cjson.decode(queue_job)

        -- add it to the in progress hash
        local in_progress_job_json = cjson.encode(in_progress_job)
        local was_added = redis.call("HSET", KEYS[4], new_job_id, in_progress_job_json)
        return in_progress_job_json
      else
        return 0
      end
    else
      -- error queue does not exist
      return -1
    end
    """
    self._do_lua_pop_queued_job = redis_con.register_script(lua_script_pop_queued_job)

    # KEYS = [ IN_PROGRESS_JOBS_KEY, QUEUE_NAMES_KEY ]
    # ARGV = [ job_id, QUEUE_NAME_PREFIX ]
    lua_script_requeue_in_progress_job = """
    local job_json = redis.call("HGET", KEYS[1], ARGV[1])
    if job_json then
      -- convert to Lua table
      local job_table = cjson.decode(job_json)
      local name = job_table["queue"]

      -- check if queue exists
      if redis.call("SISMEMBER", KEYS[2], name) == 0 then
        -- queue not found, return error code and queue name
        local ret = {}
        ret[1] = -2
        ret[2] = name
        return ret
      end

      local queue_job = job_table["job"]
      local queue_job_json = cjson.encode(queue_job)

      -- push the job to the front of the queue
      local num_jobs = redis.call("LPUSH", ARGV[2] .. name, queue_job_json)
      if num_jobs < 1 then
        -- push failed??
        return -3
      end

      -- remove the job from the in progress list
      if redis.call("HDEL", KEYS[1], ARGV[1]) then
        return 0
      else
        -- remove failed??
        return -4
      end
    else
      -- job not found
      return -1
    end
    """
    self._do_lua_requeue_in_progress_job = redis_con.register_script(lua_script_requeue_in_progress_job)

    # KEYS = [ IN_PROGRESS_JOBS_KEY]
    # ARGV = [ job_id ]
    lua_script_remove_in_progress_job = """
    local job_json = redis.call("HGET", KEYS[1], ARGV[1])
    if job_json then

      -- remove the job from the in progress list
      if redis.call("HDEL", KEYS[1], ARGV[1]) then
        return job_json
      else
        -- remove failed??
        return -4
      end
    else
      -- job not found
      return -1
    end
    """
    self._do_lua_remove_in_progress_job = redis_con.register_script(lua_script_remove_in_progress_job)


  async def shutdown(self):
    if(self._redis_mgr):
      logger.debug("shutting down JobQueue redis_mgr")
      await self._redis_mgr.shutdown_pool()
      self._redis_mgr = None
      logger.info("shutdown JobQueue redis_mgr")
    
  async def get_queue_names(self) -> typing.List[str]:
    """ Get the list of names of all of the existing job queues """
    redis_con = self._redis_mgr.get_client()
    return(await redis_con.smembers(QUEUE_NAMES_KEY))


  async def get_queue_jobs(self, name: str) -> typing.List[dict]:
    """ Get the list of all jobs in the named queue """

    keys = [ QUEUE_NAMES_KEY, QUEUE_NAME_PREFIX + name ]
    args = [ name ]
    jobs = queue_count = await self._do_lua_get_queue_jobs(keys = keys, args = args)
    if(jobs == -1):
      raise QueueDoesNotExist("get_queue_jobs({}): queue does not exist".format(name))

    job_dicts = []
    for job in jobs:
     job_dicts.append(json.loads(job))

    return(job_dicts)

  async def get_last_job_id(self) -> int:
    """ Get the id that was used for the last job """
    redis_con = self._redis_mgr.get_client()
    return(int(await redis_con.get(JOB_ID_KEY)))

  async def create_new_queue(self, name: str) -> None:
    """ Create a new job queue with the given name """

    assert(isinstance(name, str))
    keys = [ QUEUE_NAMES_KEY ]
    args = [ name ]
    queue_count = await self._do_lua_create_new_queue(keys = keys, args = args)
    if(queue_count == -1):
      raise Exception("create_new_queue({}): queue already exists".format(name))

    return(queue_count)
    
  async def delete_queue(self, name: str) -> typing.List[dict]:
    """
    Pull all of the jobs off of the named, queue and
    remove the queue and its name from the list of queues.

    returns: the list of jobs removed from the queue
    """

    keys = [ QUEUE_NAMES_KEY, QUEUE_NAME_PREFIX + name ]
    args = [ name ]
    jobs = await self._do_lua_delete_queue(keys = keys, args = args)
    if(jobs == -1):
      raise QueueDoesNotExist("delete_queue({}): queue does not exist".format(name))

    job_dicts = []
    for job in jobs:
     job_dicts.append(json.loads(job))

    return(job_dicts)

  async def pop_queued_job(self,
    name: str,
    server_key: str
    ) -> typing.Union[dict, None]:
    """
    Retrieve the next available job, if one eixst,
    from the named queue for the named server,
    assign it a job id and add it to the in progress
    hash.

    Returns: an in progress job object (dict) or None
      keys:
        jobid: int - unique job id for this job on the given server
        queue: str - name of the queue from which the job was popped
        job: dict - queue job object
        start: float - epoch time UTC when the job was dequeued
        server: str - server_key: "<host>:<port>:<pid>:start_time>" for server
          which will run the job, this is attained from the "/servers" entry 
          point in the admin REST API or from ServerState.server_key()
    """

    keys = [ QUEUE_NAMES_KEY, QUEUE_NAME_PREFIX + name, JOB_ID_KEY, IN_PROGRESS_JOBS_KEY ]
    args = [ name, server_key ]
    job = await self._do_lua_pop_queued_job(keys = keys, args = args)
    if(job == -1):
      raise QueueDoesNotExist("pop_queue_job({}) queue does not exist".format(name))

    if(job == 0):
      raise EmptyJobQueue("No jobs in queue: {}".format(name))

    job_json = json.loads(job)
    # convert the start time string to a float
    if(isinstance(job_json.get("start", None), str)):
      job_json["start"] = float(job_json["start"])
    # convert the job id string to a int
    if(isinstance(job_json.get("jobid", None), str)):
      job_json["jobid"] = int(job_json["jobid"])

    return(job_json)


  async def get_in_progress_jobs(self) -> typing.Dict[int, dict]:
    """
    Get the list of jobs which are in progress
    """
    redis_con = self._redis_mgr.get_client()
    in_progress_jobs = {}
    jobs_dict = await redis_con.hgetall(IN_PROGRESS_JOBS_KEY)

    for jobid in jobs_dict:
      job_dict = json.loads(jobs_dict[jobid])

      # convert the start time string to a float
      if(isinstance(job_dict.get("start", None), str)):
        job_dict["start"] = float(job_dict["start"])
      # convert the job id string to a int
      if(isinstance(job_dict.get("jobid", None), str)):
        job_dict["jobid"] = int(job_dict["jobid"])

      in_progress_jobs[int(jobid)] = job_dict

    return(in_progress_jobs)

  async def requeue_in_progress_job(self, job_id: int) -> None:
    """
    Roll back an incompleted, in progress queue job.

    The job is removed from the in progress list and pushed
    to the front of the queue from which the job was 
    previously popped before being added to the in progress
    hash.
    """

    if(not isinstance(job_id, int) or job_id < 0):
      raise Exception("job_id: {} must be a positive integer",format(job_id))

    keys = [ IN_PROGRESS_JOBS_KEY, QUEUE_NAMES_KEY ]
    args = [ job_id, QUEUE_NAME_PREFIX ]
    result = await self._do_lua_requeue_in_progress_job(keys = keys, args = args)
    if(result == -1):
      raise JobDoesNotExist("requeue_in_progress_job({}): job does not exist".format(job_id))

    if(isinstance(result, list) and result[0] == -2):
      raise QueueDoesNotExist("requeue_in_progress_job({}): queue: {} does not exist".format(job_id, result[1]))

    if(result != 0):
      raise Exception("requeue_in_progress_job({}): unknown error: {}".format(job_id, result))


  async def remove_in_progress_job(self, job_id: int) -> dict:
    """
    Remove the indentified job from the in progress list as completed.
    """

    if(not isinstance(job_id, int) or job_id < 0):
      raise Exception("job_id: {} must be a positive integer",format(job_id))

    keys = [ IN_PROGRESS_JOBS_KEY]
    args = [ job_id ]
    job_json = await self._do_lua_remove_in_progress_job(keys = keys, args = args)

    if(job_json == -1):
      raise JobDoesNotExist("requeue_in_progress_job({}): job does not exist".format(job_id))

    if(isinstance(job_json, int) and job_json != 0):
      raise Exception("requeue_in_progress_job({}): unknown error: {}".format(job_id, job_json))

    job_dict = json.loads(job_json)
    # convert the start time string to a float
    if(isinstance(job_dict.get("start", None), str)):
      job_dict["start"] = float(job_dict["start"])
    # convert the job id string to a int
    if(isinstance(job_dict.get("jobid", None), str)):
      job_dict["jobid"] = int(job_dict["jobid"])

    return(job_dict)

  async def push_vcon_uuid_queue_job(self,
    name: str,
    vcon_uuids: [str]
    ) -> int:
    """
    Push a vCon UUID queue job object onto the named queue.

    params:
      vcon_uuid: list of one or more vCon UUID strings.
        The UUID must reference a vCon in VconStorage

        The queue gets a queue jobe object which is a json
        dict object with the following keys and values:
          job_type: str = "vcon_uuid"
          vcon_uuid: list[str] vcon_uuids

    returns: length of the queue (where the new job is in the queue)
    """

    if(not isinstance(vcon_uuids, list)):
      raise Exception("expecting vcon_uuids to be a list, got: {}".format(type(vcon_uuids)))

    if(len(vcon_uuids) < 1):
      raise Exception("vcon_uuids array must contain at least one UUID")

    # TODO: limitation to be removed
    if(len(vcon_uuids) > 1):
      raise Exception("currently only support exactly 1 UUID")

    job_json = { "job_type": "vcon_uuid", "vcon_uuid": vcon_uuids }

    keys = [ QUEUE_NAMES_KEY, QUEUE_NAME_PREFIX + name]
    args = [ name, json.dumps(job_json)]
    num_jobs = await self._do_lua_push_vcon_uuid_queue_job(keys = keys, args = args)
    if(num_jobs == -1):
      raise QueueDoesNotExist("push_vcon_uuid_queue_job({}): queue does not exist".format(name))

    return(num_jobs)

