from datetime import datetime


def build_reminder_message(task_name: str, reminder_message: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"Reminder '{task_name}' executed at {timestamp}: {reminder_message}"
