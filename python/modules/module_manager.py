from app_logging.logger import Logger


class ModuleManager:
    """Manages the collection of VHDL modules in the project."""

    def __init__(self, logger: Logger):
        self._logger = logger
        self.modules: list[dict] = []
        self._logger.log_action("ModuleManager initialized")

    def add_module(self, name: str, entity: str, ports: list[dict],
                   library: str = 'work', x: int = 100, y: int = 100):
        module = {
            'name': name,
            'entity': entity,
            'library': library,
            'ports': ports,
            'x': x,
            'y': y,
        }
        self.modules.append(module)
        self._logger.log_action("add_module", f"{name} entity={entity} ports={len(ports)} pos=({x},{y})")

    def remove_module(self, name: str):
        self.modules = [m for m in self.modules if m['name'] != name]
        self._logger.log_action("remove_module", name)

    def get_module(self, name: str) -> dict | None:
        return next((m for m in self.modules if m['name'] == name), None)

    def get_names(self) -> list[str]:
        return [m['name'] for m in self.modules]

    def to_list(self) -> list[dict]:
        return self.modules

    def load(self, modules: list[dict]):
        self.modules = modules
        self._logger.log_action("modules_loaded", f"{len(modules)} modules")
