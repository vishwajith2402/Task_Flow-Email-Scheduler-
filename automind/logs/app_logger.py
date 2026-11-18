import logging
from pathlib import Path

from automind.database.db_manager import DatabaseManager
from automind.utils.paths import get_base_data_dir


class AppLogger:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database
        self.log_file = get_base_data_dir() / "automind.log"
        self._logger = logging.getLogger("automind")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        if not self._logger.handlers:
            handler = logging.FileHandler(self.log_file, encoding="utf-8")
            formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

    def info(self, message: str, task_id: int | None = None, owner_user_id: int | None = None) -> None:
        self._logger.info(message)
        self.database.create_log(task_id, "INFO", message, owner_user_id)

    def error(self, message: str, task_id: int | None = None, owner_user_id: int | None = None) -> None:
        self._logger.error(message)
        self.database.create_log(task_id, "ERROR", message, owner_user_id)
