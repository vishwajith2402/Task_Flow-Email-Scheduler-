from automind.config import APP_NAME
from automind.database.db_manager import DatabaseManager
from automind.logs.app_logger import AppLogger
from automind.scheduler.engine import SchedulerEngine
from automind.ui.app import AutoMindApp


def main() -> None:
    database = DatabaseManager()
    logger = AppLogger(database)
    scheduler = SchedulerEngine(database, logger)
    scheduler.start()

    app = AutoMindApp(database, scheduler, logger)
    app.mainloop()
