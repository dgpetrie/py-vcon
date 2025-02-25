""" Implementaiton of the Vcon API entry points """

import os
import typing
import fastapi
import fastapi.responses
import py_vcon_server.db
import py_vcon_server.processor
import py_vcon_server.logging_utils
import vcon
import vcon.utils

logger = py_vcon_server.logging_utils.init_logger(__name__)


def init(restapi):
  @restapi.get("/vcon/{vcon_uuid}",
    response_model = py_vcon_server.processor.VconObject,
    responses = py_vcon_server.restful_api.ERROR_RESPONSES,
    tags = [ py_vcon_server.restful_api.VCON_TAG ])
  async def get_vcon(vcon_uuid: str):
    """
    Get the vCon object identified by the given UUID.

    Returns: dict - vCon object which may be in the unencrypted, signed or encrypted JSON forms
    """

    try:
      logger.debug("getting vcon UUID: {}".format(vcon_uuid))
      vCon = await py_vcon_server.db.VconStorage.get(vcon_uuid)

    except py_vcon_server.db.VconNotFound as e:
      py_vcon_server.restful_api.log_exception(e)
      return(py_vcon_server.restful_api.NotFoundResponse("vCon UUID: {} not found".format(vcon_uuid)))

    except Exception as e:
      py_vcon_server.restful_api.log_exception(e)
      return(py_vcon_server.restful_api.InternalErrorResponse(e))

    logger.debug(
      "Returning whole vcon for {} found: {}".format(vcon_uuid, vCon is not None))

    if(vCon is None):
      raise(fastapi.HTTPException(status_code=404, detail="Vcon not found"))

    return(fastapi.responses.JSONResponse(content=vCon.dumpd()))

  @restapi.post("/vcon",
    status_code = 204,
    responses = py_vcon_server.restful_api.ERROR_RESPONSES,
    tags = [ py_vcon_server.restful_api.VCON_TAG ])
  async def post_vcon(inbound_vcon: py_vcon_server.processor.VconObject):
    """
    Store the given vCon in VconStorage, replace if it exists for the given UUID
    """
    try:
      vcon_dict = inbound_vcon.dict(exclude_none = True)

      vcon_uuid = vcon_dict.get("uuid", None)
      logger.debug("setting vcon UUID: {}".format(vcon_uuid))

      if(vcon_uuid is None or len(vcon_uuid) < 1):
        return(py_vcon_server.restful_api.ValidationError("vCon UUID: not set"))

      vcon_object = vcon.Vcon()
      vcon_object.loadd(vcon_dict)

      await py_vcon_server.db.VconStorage.set(vcon_dict)

    except vcon.InvalidVconJson as e:
      py_vcon_server.restful_api.log_exception(e)
      return(py_vcon_server.restful_api.ValidationError(str(e)))

    except Exception as e:
      py_vcon_server.restful_api.log_exception(e)
      return(py_vcon_server.restful_api.InternalErrorResponse(e))

    # No return should emmit 204, no content

  @restapi.delete("/vcon/{vcon_uuid}",
    status_code = 204,
    responses = py_vcon_server.restful_api.ERROR_RESPONSES,
    tags = [ py_vcon_server.restful_api.VCON_TAG ])
  async def delete_vcon(vcon_uuid: str):
    """
    Delete the vCon idenfied by the given UUID from VconStorage

    Returns: None
    """
    try:
      logger.debug("deleting vcon UUID: {}".format(vcon_uuid))
      await py_vcon_server.db.VconStorage.delete(vcon_uuid)

    except Exception as e:
      py_vcon_server.restful_api.log_exception(e)
      return(py_vcon_server.restful_api.InternalErrorResponse(e))

    logger.debug("Deleted vcon: UUID={}".format(vcon_uuid))

    # no return should cause 204, no content

  @restapi.get("/vcon/{vcon_uuid}/jq",
    responses = py_vcon_server.restful_api.ERROR_RESPONSES,
    tags = [ py_vcon_server.restful_api.VCON_TAG ])
  async def get_vcon_jq_transform(vcon_uuid: str, jq_transform: str):
    """
    Apply the given jq transform to the vCon identified by the given UUID and return the results.

    Returns: list - containing jq tranform of the vCon.
    """
    try:
      logger.info("vcon UID: {} jq transform string: {}".format(vcon_uuid, jq_transform))
      transform_result = await py_vcon_server.db.VconStorage.jq_query(vcon_uuid, jq_transform)
      logger.debug("jq  transform result: {}".format(transform_result))

    except Exception as e:
      py_vcon_server.restful_api.log_exception(e)
      return(py_vcon_server.restful_api.InternalErrorResponse(e))

    return(fastapi.responses.JSONResponse(content=transform_result))

  @restapi.get("/vcon/{vcon_uuid}/jsonpath",
    responses = py_vcon_server.restful_api.ERROR_RESPONSES,
    tags = [ py_vcon_server.restful_api.VCON_TAG ])
  async def get_vcon_jsonpath_query(vcon_uuid: str, path_string: str):
    """
    Apply the given JSONpath query to the vCon idntified by the given UUID.

    Returns: list - the JSONpath query results
    """

    try:
      logger.info("vcon UID: {} jsonpath query string: {}".format(vcon_uuid, path_string))
      query_result = await py_vcon_server.db.VconStorage.json_path_query(vcon_uuid, path_string)
      logger.debug("jsonpath query result: {}".format(query_result))

    except Exception as e:
      py_vcon_server.restful_api.log_exception(e)
      return(py_vcon_server.restful_api.InternalErrorResponse(e))

    return(fastapi.responses.JSONResponse(content=query_result))


  processor_names = py_vcon_server.processor.VconProcessorRegistry.get_processor_names()
  for processor_name in processor_names:
    processor_inst = py_vcon_server.processor.VconProcessorRegistry.get_processor_instance(
      processor_name)

    @restapi.post("/process/{{vcon_uuid}}/{}".format(processor_name),
      summary = processor_inst.title(),
      description = processor_inst.description(),
      response_model = py_vcon_server.processor.VconProcessorOutput,
      responses = py_vcon_server.restful_api.ERROR_RESPONSES,
      tags = [ py_vcon_server.restful_api.PROCESSOR_TAG ])
    async def run_vcon_processor(
      options: processor_inst.processor_options_class(),
      vcon_uuid: str,
      request: fastapi.Request,
      commit_changes: bool = False,
      return_whole_vcon: bool = True
      ) -> str:

      try:
        #processor_name = processor_type_dict[type(options)]
        path = request.url.path
        processor_name_from_path = os.path.basename(path)

        logger.debug("type: {} path: {} ({}) options: {} processor: {}".format(
          processor_name, path, type(options), options, processor_name_from_path))

        processor_input = py_vcon_server.processor.VconProcessorIO()
        await processor_input.add_vcon(vcon_uuid, "fake_lock", False)

        # Get the processor form the registry
        processor_inst = py_vcon_server.processor.VconProcessorRegistry.get_processor_instance(
          processor_name)

        # Run the processor
        processor_output = await processor_inst.process(
          processor_input,
          options)

        if(commit_changes):
          # Save changed Vcons
          await py_vcon_server.db.VconStorage.commit(processor_output)

        # Get serializable output
        # TODO: don't return whole Vcon if not return_whole_vcon
        response_output = await processor_output.get_output()

      except py_vcon_server.db.VconNotFound as e:
        py_vcon_server.restful_api.log_exception(e)
        return(py_vcon_server.restful_api.NotFoundResponse("vCon UUID: {} not found".format(vcon_uuid)))

      except Exception as e:
        py_vcon_server.restful_api.log_exception(e)
        return(py_vcon_server.restful_api.InternalErrorResponse(e))

      return(fastapi.responses.JSONResponse(content = response_output.dict(exclude_none = True)))

