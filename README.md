# TaskFlow

TaskFlow is a local Windows desktop automation app built with Python, CustomTkinter, SQLite, APScheduler, and Gmail SMTP support. It helps you schedule reminders and automated emails from a polished desktop interface.

## Features

- Dashboard with task and execution statistics
- One-time or recurring reminder and email automations
- `Send Test Email Now` button for quick SMTP verification
- Theme changer with `Default`, `Dark`, and `Light` palettes
- SQLite-backed task storage and execution logs
- PyInstaller build support for a Windows desktop executable

## Quick Setup

```powershell
.\setup_taskflow.ps1
```

To install dependencies and open the app immediately:

```powershell
.\setup_taskflow.ps1 -Launch
```

To install dependencies and build the desktop executable:

```powershell
.\setup_taskflow.ps1 -Build
```

## Manual Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Build the Executable

```powershell
.\build_exe.ps1
```

The generated executable will be inside `dist\TaskFlow\`.

## Gmail App Password Setup

1. Turn on 2-Step Verification for the Gmail account you want to send from.
2. Create an App Password from your Google Account security settings.
3. Use that Gmail address and app password inside TaskFlow.
4. Use `Send Test Email Now` before relying on a scheduled email.

## Scheduling Notes

- Date format in the UI: `YYYY-MM-DD`
- Time format in the UI: `HH:MM` (24-hour clock)
- `None` creates a one-time task
- `Daily` repeats every day at the selected time
- `Weekly` repeats every week on the same weekday and time
- Keep the app open while waiting for a scheduled task to run

## Local Data Storage

TaskFlow stores its database and logs in:

```text
%APPDATA%\TaskFlow
```

If that directory is unavailable, it falls back to a local `data\` folder next to the app.
