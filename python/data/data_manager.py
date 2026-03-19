import json
from app_logging.logger import Logger


class DataManager:
    """Handles project file I/O in a text-based, Git-friendly JSON format."""

    def __init__(self, logger: Logger | None = None):
        self._logger = logger

    def _log(self, action: str, details: str | None = None):
        if self._logger:
            self._logger.log_action(action, details)

    def save_project(self, project_data: dict, filename: str):
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(project_data, f, indent=2, sort_keys=True)
        self._log("save_project", filename)

    def load_project(self, filename: str) -> dict:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._log("load_project", filename)
        return data
