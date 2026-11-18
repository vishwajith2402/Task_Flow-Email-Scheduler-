from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from automind.actions.email_sender import send_email
from automind.actions.reminder_action import build_reminder_message
from automind.config import DATETIME_FORMAT, SCHEDULE_GRACE_SECONDS
from automind.database.db_manager import DatabaseManager
from automind.logs.app_logger import AppLogger


class SchedulerEngine:
    def __init__(self, database: DatabaseManager, logger: AppLogger) -> None:
        self.database = database
        self.logger = logger
        self.scheduler = BackgroundScheduler()

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            self.logger.info("Scheduler engine started.")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self.logger.info("Scheduler engine stopped.")

    def clear_all_jobs(self) -> None:
        self.scheduler.remove_all_jobs()

    def load_tasks(self, owner_user_id: int | None = None) -> None:
        self.clear_all_jobs()
        for task in self.database.fetch_tasks(owner_user_id=owner_user_id, status="active"):
            self._recover_recent_missed_task(task)
            task = self.database.fetch_task(task["id"], owner_user_id=owner_user_id)
            if not task or task["status"] != "active":
                continue
            self.schedule_task(task)

    def schedule_task(self, task: dict) -> None:
        job_id = self._job_id(task["id"])
        self.scheduler.remove_job(job_id) if self.scheduler.get_job(job_id) else None

        trigger = self._build_trigger(task)
        if trigger is None:
            self.database.update_task_status(task["id"], "inactive")
            self.database.update_task_schedule(task["id"], None)
            self.logger.error(f"Task '{task['name']}' could not be scheduled because its time is in the past.", task["id"])
            return

        job = self.scheduler.add_job(
            self.execute_task,
            trigger=trigger,
            args=[task["id"]],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        next_run = job.next_run_time.isoformat(timespec="seconds") if job.next_run_time else None
        self.database.update_task_schedule(task["id"], next_run)
        self.logger.info(f"Task '{task['name']}' scheduled for {next_run or 'no upcoming run'}.", task["id"])

    def refresh_task(self, task_id: int) -> None:
        task = self.database.fetch_task(task_id)
        if not task:
            return

        self.remove_task(task_id)
        if task["status"] == "active":
            self.schedule_task(task)

    def remove_task(self, task_id: int) -> None:
        job_id = self._job_id(task_id)
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def execute_task(self, task_id: int) -> None:
        task = self.database.fetch_task(task_id)
        if not task or task["status"] != "active":
            return

        try:
            if task["action_type"] == "Email":
                payload = task["payload"]
                send_email(
                    sender_email=payload["sender_email"],
                    app_password=payload["app_password"],
                    recipient=payload["recipient"],
                    subject=payload["subject"],
                    message=payload["message"],
                    attachments=payload.get("attachments", []),
                )
                message = f"Email task '{task['name']}' sent to {payload['recipient']}."
            elif task["action_type"] == "Reminder":
                payload = task["payload"]
                message = build_reminder_message(task["name"], payload["message"])
            else:
                raise ValueError(f"Unsupported action type: {task['action_type']}")

            self.logger.info(message, task_id, task.get("owner_user_id"))
            self._update_after_execution(task)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"Task '{task['name']}' failed: {exc}", task_id, task.get("owner_user_id"))
            self._update_after_execution(task, failed=True)

    def _update_after_execution(self, task: dict, failed: bool = False) -> None:
        last_run = datetime.now().isoformat(timespec="seconds")
        job = self.scheduler.get_job(self._job_id(task["id"]))
        next_run = job.next_run_time.isoformat(timespec="seconds") if job and job.next_run_time else None

        if task["repeat_type"] == "None":
            self.database.update_task_status(task["id"], "inactive")
            self.database.update_task_run_state(task["id"], last_run, None)
            self.remove_task(task["id"])
            if not failed:
                self.logger.info(f"One-time task '{task['name']}' completed and was deactivated.", task["id"])
        else:
            self.database.update_task_run_state(task["id"], last_run, next_run)

    @staticmethod
    def _job_id(task_id: int) -> str:
        return f"task_{task_id}"

    @staticmethod
    def _build_trigger(task: dict):
        scheduled_time = datetime.strptime(task["run_at"], DATETIME_FORMAT)
        repeat_type = task["repeat_type"]
        now = datetime.now()
        grace_deadline = scheduled_time + timedelta(seconds=SCHEDULE_GRACE_SECONDS)

        if repeat_type == "None":
            if scheduled_time <= now <= grace_deadline:
                return DateTrigger(run_date=now + timedelta(seconds=1))
            if scheduled_time <= now:
                return None
            return DateTrigger(run_date=scheduled_time)

        if repeat_type == "Daily":
            return CronTrigger(hour=scheduled_time.hour, minute=scheduled_time.minute, start_date=scheduled_time)

        if repeat_type == "Weekly":
            return CronTrigger(
                day_of_week=scheduled_time.weekday(),
                hour=scheduled_time.hour,
                minute=scheduled_time.minute,
                start_date=scheduled_time,
            )

        raise ValueError(f"Unsupported repeat type: {repeat_type}")

    def _recover_recent_missed_task(self, task: dict) -> None:
        if not self._should_run_immediately(task):
            return

        self.logger.info(
            f"Recovering task '{task['name']}' because its scheduled time was missed by less than {SCHEDULE_GRACE_SECONDS // 60} minutes.",
            task["id"],
        )
        self.execute_task(task["id"])

    @staticmethod
    def _should_run_immediately(task: dict) -> bool:
        now = datetime.now()
        scheduled_time = datetime.strptime(task["run_at"], DATETIME_FORMAT)
        grace_window = timedelta(seconds=SCHEDULE_GRACE_SECONDS)
        last_run = datetime.fromisoformat(task["last_run"]) if task.get("last_run") else None

        if task["repeat_type"] == "None":
            return task["status"] == "active" and last_run is None and scheduled_time <= now <= scheduled_time + grace_window

        candidate = scheduled_time.replace(year=now.year, month=now.month, day=now.day)
        if task["repeat_type"] == "Weekly":
            if now.weekday() != scheduled_time.weekday():
                return False
            start_of_window = candidate
        elif task["repeat_type"] == "Daily":
            start_of_window = candidate
        else:
            return False

        if not (start_of_window <= now <= start_of_window + grace_window):
            return False

        return last_run is None or last_run < start_of_window
