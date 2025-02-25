import typing
import pkgutil
import importlib
import vcon
import py_vcon_server.logging_utils
import py_vcon_server.processor

logger = py_vcon_server.logging_utils.init_logger(__name__)

class VconNotFound(Exception):
  """ Rasied when the vCon for the given UUID does not exist """


def import_bindings(path: str, module_prefix: str, label: str):
  """ Import the modules and interface registrations """
  for finder, module_name, is_package in pkgutil.iter_modules(
    path,
    module_prefix
    ):
    logger.info("{} module load: {}".format(label, module_name))
    importlib.import_module(module_name)


# Should this be a class or global methods??
class VconStorage():
  _vcon_storage_implementations = {}
  _vcon_storage_binding = None

  @staticmethod
  async def setup(db_type : str = "redis", db_url : str = "redis://localhost") -> None:
    """ Setup Vcon storage DB type, factory and connection URL """
    #  Need to setup Vcon storage type and URL

    if(VconStorage._vcon_storage_binding is not None):
      raise(Exception("Vcon storage implementation already bound to type: {}".format(type(VconStorage._vcon_storage_binding))))

    impl_class = VconStorage._vcon_storage_implementations[db_type]
    VconStorage._vcon_storage_binding = impl_class()

    VconStorage._vcon_storage_binding.setup(db_url)

  @staticmethod
  async def teardown() -> None:
    """ teardown for Vcon storage interface to force closure and clean up of connections """
    if(VconStorage._vcon_storage_binding is None):
      raise(Exception("Vcon storage implementation not setup"))

    await VconStorage._vcon_storage_binding.teardown()
    VconStorage._vcon_storage_binding = None

  @staticmethod
  def register(name : str, class_type : typing.Type):
    """ method to register storage class types """

    VconStorage._vcon_storage_implementations[name] = class_type
    logger.info("registered {} Vcon storage implementation".format(name))

  @staticmethod
  async def set(save_vcon : typing.Union[vcon.Vcon, dict, str]):
    """ add or update a Vcon in persistent storage """
    if(VconStorage._vcon_storage_binding is None):
      raise(Exception("Vcon storage implementation not setup"))

    await VconStorage._vcon_storage_binding.set(save_vcon)

  @staticmethod
  async def commit(processor_output: py_vcon_server.processor.VconProcessorIO) -> None:
    """
    Helper function to save changed **Vcon**s from the
    output of a **VconProcessor** or **Pipeline**.

    Saves **Vcon**s which have been marked as modified
    or new in the given **VconProcessorIO**
    """
    num_vcons = processor_output.num_vcons()
    for index in range(0, num_vcons):
      if(processor_output.is_vcon_modified(index)):
        vcon_dict = await processor_output.get_vcon(
          index,
          py_vcon_server.processor.VconTypes.DICT
          )

        await VconStorage.set(vcon_dict)

  @staticmethod
  async def get(vcon_uuid : str) -> typing.Union[None, vcon.Vcon]:
    """ Get a Vcon from storage using its UUID as the key """
    if(VconStorage._vcon_storage_binding is None):
      raise(Exception("Vcon storage implementation not setup"))

    vCon = await VconStorage._vcon_storage_binding.get(vcon_uuid)
    return(vCon)

  @staticmethod
  async def jq_query(vcon_uuid : str, jq_query_string : str) -> str:
    """
    Apply the given JQ query/transform on the Vcon from storage given its UUID as the key.

    Returns: json query/transform in the form of a string
    """
    if(VconStorage._vcon_storage_binding is None):
      raise(Exception("Vcon storage implementation not setup"))

    query_result = await VconStorage._vcon_storage_binding.jq_query(vcon_uuid, jq_query_string)
    return(query_result)

  @staticmethod
  async def json_path_query(vcon_uuid : str, json_path_query_string : str) -> str:
    """
    Apply the given JsonPath query on the Vcon from storage given its UUID as the key.

    Returns: json path query in the form of a string
    """
    if(VconStorage._vcon_storage_binding is None):
      raise(Exception("Vcon storage implementation not setup"))

    query_result = await VconStorage._vcon_storage_binding.json_path_query(vcon_uuid, json_path_query_string)
    return(query_result)

  @staticmethod
  async def delete(vcon_uuid : str) -> None:
    """ Delete the Vcon from storage identified by its UUID as the key """
    if(VconStorage._vcon_storage_binding is None):
      raise(Exception("Vcon storage implementation not setup"))

    await VconStorage._vcon_storage_binding.delete(vcon_uuid)


  # TODO: Need connection status method

