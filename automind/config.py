from pathlib import Path

APP_NAME = "TaskFlow"
APP_SLUG = "TaskFlow"

THEMES: dict[str, dict[str, str]] = {
    "Default": {
        "appearance_mode": "light",
        "bg": "#f1f1f1",
        "card": "#ffffff",
        "accent": "#ff007f",
        "text": "#111111",
        "muted": "#505050",
        "border": "#cfcfcf",
        "success": "#16e4c9",
        "warning": "#ff9f43",
        "danger": "#d7267a",
        "sidebar": "#16e4c9",
        "input": "#ffffff",
        "section": "#ededed",
        "accent_hover": "#d10069",
        "danger_hover": "#b31f65",
    },
    "Dark": {
        "appearance_mode": "dark",
        "bg": "#11151c",
        "card": "#1b2430",
        "accent": "#4f6d8c",
        "text": "#ecf1f7",
        "muted": "#9ba9bb",
        "border": "#314052",
        "success": "#4fb286",
        "warning": "#d8a657",
        "danger": "#d96c6c",
        "sidebar": "#0d1117",
        "input": "#151d27",
        "section": "#202b38",
        "accent_hover": "#3f5b77",
        "danger_hover": "#bb5252",
    },
    "Light": {
        "appearance_mode": "light",
        "bg": "#f7f7f2",
        "card": "#ffffff",
        "accent": "#1a1a1a",
        "text": "#141414",
        "muted": "#666666",
        "border": "#d9d9d1",
        "success": "#245c3c",
        "warning": "#9a6a12",
        "danger": "#a12a2a",
        "sidebar": "#ecece4",
        "input": "#ffffff",
        "section": "#f1f1eb",
        "accent_hover": "#303030",
        "danger_hover": "#842020",
    },
}

DEFAULT_THEME = "Default"

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"
DATETIME_FORMAT = "%Y-%m-%d %H:%M"

WINDOW_SIZE = "1400x860"
MIN_WINDOW_SIZE = (1180, 720)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SCHEDULE_GRACE_SECONDS = 300

PACKAGE_ROOT = Path(__file__).resolve().parent


def get_theme_palette(theme_name: str) -> dict[str, str]:
    return dict(THEMES.get(theme_name, THEMES[DEFAULT_THEME]))
