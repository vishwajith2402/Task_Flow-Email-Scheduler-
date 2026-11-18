import os
import sys
from pathlib import Path

from automind.config import APP_SLUG


def get_base_data_dir() -> Path:
    """Return a stable writable directory for database and logs."""
    candidates: list[Path] = []
    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / APP_SLUG)
    candidates.append(Path.home() / ".automind" / APP_SLUG)
    candidates.append(get_runtime_root() / "data")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except PermissionError:
            continue

    raise PermissionError("TaskFlow could not create a writable data directory.")


def get_runtime_root() -> Path:
    """Support both normal Python execution and PyInstaller bundles."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
