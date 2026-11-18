from __future__ import annotations

import math
import tkinter.ttk as ttk
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread
from tkinter import filedialog, messagebox
import tkinter as tk

import customtkinter as ctk

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - optional runtime dependency
    pystray = None
    Image = None
    ImageDraw = None

from automind.actions.email_sender import send_email
from automind.config import (
    APP_NAME,
    APP_SLUG,
    DATETIME_FORMAT,
    DATE_FORMAT,
    DEFAULT_THEME,
    MIN_WINDOW_SIZE,
    TIME_FORMAT,
    WINDOW_SIZE,
    get_theme_palette,
)
from automind.database.db_manager import DatabaseManager
from automind.logs.app_logger import AppLogger
from automind.scheduler.engine import SchedulerEngine
from automind.ui.styles import setup_theme, style_treeview
from automind.utils.validators import (
    parse_schedule_datetime,
    parse_recipient_list,
    validate_email_payload,
    validate_email_template,
    validate_recipient_group,
    validate_reminder_payload,
    validate_sender_profile,
)


def build_signature_block(display_name: str, signature: str) -> str:
    parts = [part.strip() for part in [display_name, signature] if part and part.strip()]
    return "\n".join(parts)


def render_email_from_template(
    subject_template: str,
    body_template: str,
    variables: dict[str, str],
    signature_block: str = "",
) -> tuple[str, str]:
    subject = subject_template or ""
    body = body_template or ""
    for key, value in variables.items():
        subject = subject.replace(f"{{{key}}}", value)
        body = body.replace(f"{{{key}}}", value)
    if signature_block:
        body = f"{body}\n\n{signature_block}"
    return subject, body


def normalize_attachment_list(paths: list[str]) -> list[str]:
    return [path for path in (paths or []) if path]


def find_placeholders(text: str) -> list[str]:
    import re

    return sorted(set(match.group(1).strip() for match in re.finditer(r"{([^{}]+)}", text or "") if match.group(1).strip()))


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _mix_colors(first: str, second: str, ratio: float) -> str:
    left = _hex_to_rgb(first)
    right = _hex_to_rgb(second)
    mixed = tuple(int(left[index] * (1 - ratio) + right[index] * ratio) for index in range(3))
    return _rgb_to_hex(mixed)


class AutoMindApp(ctk.CTk):
    def __init__(self, database: DatabaseManager, scheduler: SchedulerEngine, logger: AppLogger) -> None:
        super().__init__()
        self.database = database
        self.scheduler = scheduler
        self.logger = logger

        self.theme_name = DEFAULT_THEME
        self.palette = get_theme_palette(self.theme_name)
        self.current_view = "Dashboard"
        self.current_user: dict | None = None
        self.selected_task_id: int | None = None
        self.selected_log_id: int | None = None

        self.views: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.stats_labels: dict[str, ctk.CTkLabel] = {}
        self.tree_meta: dict[ttk.Treeview, dict[str, object]] = {}
        self.sender_profiles: list[dict] = []
        self.profile_labels: list[str] = []
        self.recipient_groups: list[dict] = []
        self.recipient_group_labels: list[str] = []
        self.email_templates: list[dict] = []
        self.email_template_labels: list[str] = []
        self.selected_attachment_paths: list[str] = []
        self.nav_items = ["Dashboard", "Create Automation", "Scheduled Tasks", "Email Automation", "Logs", "About", "Settings"]
        self.nav_icons = {
            "Dashboard": "◆",
            "Create Automation": "✦",
            "Scheduled Tasks": "◈",
            "Email Automation": "✉",
            "Logs": "▤",
            "About": "i",
            "Settings": "⚙",
        }
        self.active_scroll_frame: ctk.CTkScrollableFrame | None = None
        self.scrollable_frames: list[ctk.CTkScrollableFrame] = []
        self._scroll_animation_after_id: str | None = None
        self._scroll_animation_axis = "y"
        self._scroll_animation_target = 0.0
        self._tray_icon = None
        self._tray_thread: Thread | None = None
        self._tray_hidden = False
        self._quitting = False
        self._tray_notice_shown = False
        self._close_after_tasks_complete = False
        self._background_canvas: tk.Canvas | None = None
        self._sidebar_background_canvas: tk.Canvas | None = None
        self._header_background_canvas: tk.Canvas | None = None
        self._background_after_id: str | None = None
        self._background_particles: list[dict[str, float | int]] = []
        self._background_phase = 0.0
        self._logo_canvas: tk.Canvas | None = None
        self._logo_after_id: str | None = None
        self._logo_phase = 0.0
        self._auth_canvas: tk.Canvas | None = None
        self._auth_after_id: str | None = None
        self._auth_phase = 0.0
        self._auth_reveal_progress = 0.0
        self._auth_card: ctk.CTkFrame | None = None
        self.toast_stack: list[ctk.CTkFrame] = []
        self.command_palette_window: ctk.CTkToplevel | None = None
        self.command_palette_entry = None
        self.command_palette_results: ctk.CTkFrame | None = None
        self.global_search_entry = None
        self.activity_feed_labels: list[ctk.CTkLabel] = []
        self.timeline_rows: list[ctk.CTkFrame] = []

        setup_theme(self.palette)
        style_treeview(self.palette)

        self.title(APP_NAME)
        self.geometry(WINDOW_SIZE)
        self.minsize(*MIN_WINDOW_SIZE)
        self.configure(fg_color=self.color("bg"))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind_all("<Control-k>", self._open_command_palette_event, add="+")

        self._rebuild_ui()
        self.after(5000, self._auto_refresh)

    def color(self, key: str) -> str:
        return self.palette[key]

    def _user_id(self) -> int | None:
        return int(self.current_user["id"]) if self.current_user else None

    def _content_wrap(self, width: int) -> int:
        return width

    def _auth_palette(self) -> dict[str, str]:
        return {
            "shell": "#1f1f1f",
            "card": "#f5f5f5",
            "panel": "#16e4c9",
            "panel_text": "#101010",
            "accent": "#ff007f",
            "accent_hover": "#d10069",
            "tab_idle": "#e7e7e7",
            "tab_hover": "#d8d8d8",
            "field_bg": "#ffffff",
            "field_border": "#2a2a2a",
            "field_text": "#181818",
            "muted": "#4b4b4b",
        }

    def _rebuild_ui(self) -> None:
        for widget in self.winfo_children():
            widget.destroy()

        self.palette = get_theme_palette(self.theme_name)
        setup_theme(self.palette)
        style_treeview(self.palette)
        self.configure(fg_color=self.color("bg"))

        self.views = {}
        self.nav_buttons = {}
        self.stats_labels = {}
        self.tree_meta = {}
        self.selected_task_id = None
        self.selected_log_id = None
        self.sender_profiles = []
        self.profile_labels = []
        self.recipient_groups = []
        self.recipient_group_labels = []
        self.email_templates = []
        self.email_template_labels = []
        self.selected_attachment_paths = []
        self.scrollable_frames = []
        self._background_particles = []
        self._background_phase = 0.0
        if self._background_after_id:
            self.after_cancel(self._background_after_id)
            self._background_after_id = None
        if self._logo_after_id:
            self.after_cancel(self._logo_after_id)
            self._logo_after_id = None
        if self._auth_after_id:
            self.after_cancel(self._auth_after_id)
            self._auth_after_id = None
        self.toast_stack = []
        self.command_palette_window = None
        self.command_palette_entry = None
        self.command_palette_results = None
        self.global_search_entry = None
        self.activity_feed_labels = []
        self.timeline_rows = []
        self._auth_canvas = None
        self._auth_phase = 0.0
        self._auth_reveal_progress = 0.0
        self._auth_card = None

        if not self.current_user:
            self._build_auth_view()
            return

        self._build_layout()
        self.refresh_sender_profiles()
        self.refresh_recipient_groups()
        self.refresh_email_templates()
        self.show_view(self.current_view)
        self.refresh_all()

    def _build_auth_view(self) -> None:
        auth = self._auth_palette()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        shell = ctk.CTkFrame(self, fg_color=auth["shell"])
        shell.grid(row=0, column=0, sticky="nsew")
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(0, weight=1)

        card = ctk.CTkFrame(shell, fg_color=auth["card"], corner_radius=24, border_width=1, border_color=auth["field_border"])
        card.place(relx=0.5, rely=0.5, anchor="center")
        self._auth_card = card
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)
        card.configure(width=1060, height=610)
        card.grid_propagate(False)
        self._position_auth_card()

        welcome = ctk.CTkFrame(card, fg_color=auth["panel"], corner_radius=22)
        welcome.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
        ctk.CTkLabel(welcome, text="SECURE WORKSPACE", font=ctk.CTkFont(size=11, weight="bold"), text_color=auth["panel_text"]).pack(anchor="w", padx=24, pady=(24, 6))
        ctk.CTkLabel(welcome, text="Welcome to TaskFlow", font=ctk.CTkFont(size=30, weight="bold"), text_color=auth["panel_text"]).pack(anchor="w", padx=24, pady=(0, 10))
        ctk.CTkLabel(
            welcome,
            text="Sign in to your private workspace. Sender profiles, saved credentials, tasks, templates, and logs stay scoped to your account.",
            font=ctk.CTkFont(size=13),
            text_color=auth["panel_text"],
            wraplength=360,
            justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 18))

        for text in [
            "Private sender profiles and app passwords",
            "User-specific scheduled tasks and logs",
            "Separate templates and recipient groups",
        ]:
            ctk.CTkLabel(welcome, text=f"- {text}", font=ctk.CTkFont(size=12), text_color=auth["panel_text"], anchor="w", justify="left").pack(anchor="w", padx=24, pady=4)

        self._auth_canvas = tk.Canvas(
            welcome,
            height=220,
            highlightthickness=0,
            bd=0,
            relief="flat",
            background=auth["panel"],
        )
        self._auth_canvas.pack(fill="both", expand=True, padx=18, pady=(12, 18))
        self._auth_canvas.bind("<Configure>", self._on_auth_canvas_resize, add="+")
        self.after(150, self._animate_auth_scene)

        auth_tabs = ctk.CTkTabview(
            card,
            fg_color=auth["card"],
            segmented_button_fg_color=auth["tab_idle"],
            segmented_button_selected_color=auth["accent"],
            segmented_button_selected_hover_color=auth["accent_hover"],
            segmented_button_unselected_color=auth["field_bg"],
            segmented_button_unselected_hover_color=auth["tab_hover"],
            text_color=auth["field_text"],
        )
        auth_tabs.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        login_tab = auth_tabs.add("Login")
        signup_tab = auth_tabs.add("Create Account")
        for tab in (login_tab, signup_tab):
            tab.configure(fg_color=auth["card"])
            tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(login_tab, text="Sign In", font=ctk.CTkFont(size=22, weight="bold"), text_color=auth["field_text"]).grid(row=0, column=0, padx=20, pady=(18, 10), sticky="w")
        self.login_username_entry = self._form_entry(login_tab, "Username", 1, 0, placeholder="vishwajith")
        self.login_password_entry = self._form_entry(login_tab, "Password", 2, 0, show="*", placeholder="Password")
        ctk.CTkLabel(login_tab, text="Accounts stay on this PC and only unlock that user's saved workspace.", font=ctk.CTkFont(size=12), text_color=auth["muted"], wraplength=440, justify="left").grid(row=6, column=0, padx=20, pady=(4, 0), sticky="w")
        ctk.CTkButton(login_tab, text="Login", command=self.login_user, fg_color=auth["accent"], hover_color=auth["accent_hover"], text_color=auth["field_bg"], height=44, corner_radius=14).grid(row=7, column=0, padx=20, pady=(14, 18), sticky="ew")
        self.login_username_entry.bind("<Return>", lambda _event: self.login_user(), add="+")
        self.login_password_entry.bind("<Return>", lambda _event: self.login_user(), add="+")

        ctk.CTkLabel(signup_tab, text="Create Your Account", font=ctk.CTkFont(size=22, weight="bold"), text_color=auth["field_text"]).grid(row=0, column=0, padx=20, pady=(18, 10), sticky="w")
        self.signup_username_entry = self._form_entry(signup_tab, "Username", 1, 0, placeholder="Choose username")
        self.signup_password_entry = self._form_entry(signup_tab, "Password", 2, 0, show="*", placeholder="Create password")
        self.signup_confirm_entry = self._form_entry(signup_tab, "Confirm Password", 3, 0, show="*", placeholder="Confirm password")
        ctk.CTkLabel(signup_tab, text="Create an account to keep sender profiles, templates, and tasks private to that user.", font=ctk.CTkFont(size=12), text_color=auth["muted"], wraplength=440, justify="left").grid(row=8, column=0, padx=20, pady=(4, 0), sticky="w")
        ctk.CTkButton(signup_tab, text="Create Account", command=self.create_account, fg_color=auth["accent"], hover_color=auth["accent_hover"], text_color=auth["field_bg"], height=44, corner_radius=14).grid(row=9, column=0, padx=20, pady=(14, 18), sticky="ew")
        self.signup_username_entry.bind("<Return>", lambda _event: self.create_account(), add="+")
        self.signup_password_entry.bind("<Return>", lambda _event: self.create_account(), add="+")
        self.signup_confirm_entry.bind("<Return>", lambda _event: self.create_account(), add="+")

        for entry in (
            self.login_username_entry,
            self.login_password_entry,
            self.signup_username_entry,
            self.signup_password_entry,
            self.signup_confirm_entry,
        ):
            entry.configure(
                fg_color=auth["field_bg"],
                border_color=auth["field_border"],
                text_color=auth["field_text"],
                placeholder_text_color=auth["muted"],
            )

        if not self.database.has_users():
            auth_tabs.set("Create Account")

    def login_user(self) -> None:
        username = self.login_username_entry.get().strip() if hasattr(self, "login_username_entry") else ""
        password = self.login_password_entry.get() if hasattr(self, "login_password_entry") else ""
        if not username or not password:
            self.show_toast("Enter both username and password.", "error", 3000)
            return
        user = self.database.authenticate_user(username, password)
        if not user:
            self.show_toast("Invalid username or password.", "error", 3200)
            return
        self.current_user = user
        self.scheduler.load_tasks(self._user_id())
        self.logger.info(f"User '{user['username']}' logged in.", owner_user_id=self._user_id())
        self._rebuild_ui()
        self.show_toast(f"Welcome back, {user['username']}.", "success")

    def create_account(self) -> None:
        username = self.signup_username_entry.get().strip() if hasattr(self, "signup_username_entry") else ""
        password = self.signup_password_entry.get() if hasattr(self, "signup_password_entry") else ""
        confirm = self.signup_confirm_entry.get() if hasattr(self, "signup_confirm_entry") else ""
        if not username:
            self.show_toast("Username is required.", "error", 3000)
            return
        if len(password.strip()) < 4:
            self.show_toast("Password must be at least 4 characters.", "error", 3200)
            return
        if password != confirm:
            self.show_toast("Passwords do not match.", "error", 3200)
            return
        if self.database.fetch_user_by_username(username):
            self.show_toast("That username already exists.", "error", 3200)
            return
        self.database.create_user(username, password)
        self.current_user = self.database.authenticate_user(username, password)
        if self.current_user:
            self.scheduler.load_tasks(self._user_id())
            self.logger.info(f"User '{self.current_user['username']}' created an account.", owner_user_id=self._user_id())
            self._rebuild_ui()
            self.show_toast(f"Welcome, {self.current_user['username']}. Your workspace is ready.", "success")
            return
        self.show_toast("Account created. You can log in now.", "success")

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=356, fg_color=self.color("sidebar"), corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_rowconfigure(8, weight=1)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self._sidebar_background_canvas = tk.Canvas(
            self.sidebar,
            highlightthickness=0,
            bd=0,
            relief="flat",
            background=self.color("sidebar"),
        )
        self._sidebar_background_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tk.call("lower", self._sidebar_background_canvas._w)

        brand_block = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand_block.grid(row=0, column=0, padx=24, pady=(28, 18), sticky="ew")
        brand_block.grid_columnconfigure(1, weight=1)

        self._logo_canvas = tk.Canvas(
            brand_block,
            width=58,
            height=58,
            highlightthickness=0,
            bd=0,
            relief="flat",
            background=self.color("sidebar"),
        )
        self._logo_canvas.grid(row=0, column=0, rowspan=2, padx=(0, 12), sticky="nw")

        brand_text = ctk.CTkFrame(brand_block, fg_color="transparent")
        brand_text.grid(row=0, column=1, sticky="ew")

        brand_title_frame = ctk.CTkFrame(brand_text, fg_color="transparent", width=268, height=58)
        brand_title_frame.pack(anchor="w", pady=(0, 0), fill="x")
        brand_title_frame.pack_propagate(False)

        brand_title = ctk.CTkLabel(
            brand_title_frame,
            text=APP_NAME.upper(),
            font=ctk.CTkFont(family="Baskerville Old Face", size=27, weight="bold"),
            text_color=self.color("text"),
            height=54,
            anchor="w",
            justify="left",
        )
        brand_title.pack(anchor="w", fill="x", padx=(0, 12), pady=(2, 0))
        ctk.CTkLabel(
            brand_text,
            text="Smart desktop scheduling for reminders and email",
            font=ctk.CTkFont(size=13),
            text_color=self.color("muted"),
            wraplength=270,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        self._draw_logo_frame()
        self.after(140, self._animate_logo)

        nav_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav_frame.grid(row=1, column=0, padx=20, pady=(12, 0), sticky="ew")
        for item in self.nav_items:
            button = ctk.CTkButton(
                nav_frame,
                text=f"{self.nav_icons.get(item, '•')}  {item}",
                command=lambda value=item: self.show_view(value),
                fg_color="transparent",
                hover_color=self.color("card"),
                text_color=self.color("text"),
                anchor="w",
                height=46,
                corner_radius=14,
                border_width=0,
            )
            button.pack(fill="x", pady=6)
            self.nav_buttons[item] = button

        sidebar_footer = ctk.CTkFrame(
            self.sidebar,
            fg_color=self.color("card"),
            corner_radius=18,
            border_width=1,
            border_color=self.color("border"),
        )
        sidebar_footer.grid(row=9, column=0, padx=20, pady=20, sticky="sew")
        ctk.CTkLabel(
            sidebar_footer,
            text="Scheduler Status",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.color("muted"),
        ).pack(anchor="w", padx=16, pady=(14, 4))
        self.sidebar_status = ctk.CTkLabel(
            sidebar_footer,
            text="Running",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=self.color("success"),
        )
        self.sidebar_status.pack(anchor="w", padx=16, pady=(0, 4))
        ctk.CTkLabel(
            sidebar_footer,
            text="Closing the window sends TaskFlow to the system tray so scheduled tasks can keep running.",
            font=ctk.CTkFont(size=12),
            text_color=self.color("muted"),
            wraplength=280,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 14))

        content_host = ctk.CTkFrame(self, fg_color=self.color("bg"))
        content_host.grid(row=0, column=1, sticky="nsew", padx=24, pady=20)
        content_host.grid_columnconfigure(0, weight=1)
        content_host.grid_rowconfigure(0, weight=1)

        self._build_animated_background(content_host)

        self.content_shell = ctk.CTkFrame(content_host, fg_color="transparent")
        self.content_shell.grid(row=0, column=0, sticky="nsew")
        self.content_shell.grid_columnconfigure(0, weight=1)
        self.content_shell.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_quick_action_bar()

        self.content = ctk.CTkFrame(self.content_shell, fg_color=self.color("bg"))
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.views["Dashboard"] = self._build_dashboard_view()
        self.views["Create Automation"] = self._build_create_view()
        self.views["Scheduled Tasks"] = self._build_tasks_view()
        self.views["Email Automation"] = self._build_email_view()
        self.views["Logs"] = self._build_logs_view()
        self.views["About"] = self._build_about_view()
        self.views["Settings"] = self._build_settings_view()
        self._enable_smooth_scrolling()

    def _build_animated_background(self, parent) -> None:
        self._background_canvas = tk.Canvas(
            parent,
            highlightthickness=0,
            bd=0,
            relief="flat",
            background=self.color("bg"),
        )
        self._background_canvas.grid(row=0, column=0, sticky="nsew")
        self._background_canvas.bind("<Configure>", self._on_background_resize)
        self._init_background_particles()
        self.after(120, self._animate_background)

    def _background_palette(self) -> dict[str, str]:
        return {
            "base": self.color("bg"),
            "grid": _mix_colors(self.color("accent"), self.color("bg"), 0.74),
            "glow": _mix_colors(self.color("accent"), self.color("card"), 0.24),
            "pulse": _mix_colors(self.color("accent"), self.color("border"), 0.14),
            "particle": _mix_colors(self.color("accent"), self.color("card"), 0.14),
            "stream": _mix_colors(self.color("accent"), self.color("card"), 0.10),
            "highlight": _mix_colors(self.color("accent"), self.color("card"), 0.04),
        }

    def _logo_palette(self) -> dict[str, str]:
        return {
            "base": self.color("sidebar"),
            "ring": _mix_colors(self.color("accent"), self.color("card"), 0.22),
            "orbit": _mix_colors(self.color("accent"), self.color("sidebar"), 0.40),
            "core": self.color("accent"),
            "spark": _mix_colors(self.color("card"), self.color("accent"), 0.45),
        }

    def _draw_logo_frame(self) -> None:
        canvas = self._logo_canvas
        if canvas is None or not canvas.winfo_exists():
            return

        palette = self._logo_palette()
        width = max(canvas.winfo_width(), 58)
        height = max(canvas.winfo_height(), 58)
        cx = width / 2
        cy = height / 2
        radius = min(width, height) * 0.33
        angle = self._logo_phase
        node_x = cx + radius * 0.95 * math.cos(angle)
        node_y = cy + radius * 0.95 * math.sin(angle)
        opposite_x = cx + radius * 0.72 * math.cos(-angle * 1.4)
        opposite_y = cy + radius * 0.72 * math.sin(-angle * 1.4)

        canvas.delete("all")
        canvas.configure(background=palette["base"])
        canvas.create_oval(cx - radius * 1.28, cy - radius * 1.28, cx + radius * 1.28, cy + radius * 1.28, outline=palette["ring"], width=2)
        canvas.create_arc(cx - radius, cy - radius * 0.62, cx + radius, cy + radius * 0.62, start=0, extent=180, style="arc", outline=palette["orbit"], width=2)
        canvas.create_arc(cx - radius, cy - radius * 0.62, cx + radius, cy + radius * 0.62, start=180, extent=180, style="arc", outline=palette["orbit"], width=2)
        canvas.create_arc(cx - radius * 0.62, cy - radius, cx + radius * 0.62, cy + radius, start=90, extent=180, style="arc", outline=palette["orbit"], width=2)
        canvas.create_arc(cx - radius * 0.62, cy - radius, cx + radius * 0.62, cy + radius, start=270, extent=180, style="arc", outline=palette["orbit"], width=2)
        canvas.create_line(cx - radius * 0.95, cy, cx + radius * 0.95, cy, fill=palette["orbit"], width=1)
        canvas.create_line(cx, cy - radius * 0.95, cx, cy + radius * 0.95, fill=palette["orbit"], width=1)
        canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, fill=palette["core"], outline="")
        canvas.create_oval(node_x - 5, node_y - 5, node_x + 5, node_y + 5, fill=palette["spark"], outline="")
        canvas.create_oval(opposite_x - 4, opposite_y - 4, opposite_x + 4, opposite_y + 4, fill=palette["spark"], outline="")
        canvas.create_line(cx, cy, node_x, node_y, fill=palette["ring"], width=2)

    def _animate_logo(self) -> None:
        canvas = self._logo_canvas
        if canvas is None or not canvas.winfo_exists() or self._quitting:
            self._logo_after_id = None
            return

        self._logo_phase += 0.16
        self._draw_logo_frame()
        self._logo_after_id = self.after(70, self._animate_logo)

    def _on_auth_canvas_resize(self, _event=None) -> None:
        self._draw_auth_scene()

    def _position_auth_card(self) -> None:
        if self._auth_card is None or not self._auth_card.winfo_exists():
            return
        progress = max(0.0, min(1.0, self._auth_reveal_progress))
        rely = 0.53 - (0.03 * progress)
        self._auth_card.place_configure(relx=0.5, rely=rely, anchor="center")

    def _draw_rotated_panel(
        self,
        canvas: tk.Canvas,
        center_x: float,
        center_y: float,
        half_width: float,
        half_height: float,
        angle_degrees: float,
        fill: str,
    ) -> None:
        radians = math.radians(angle_degrees)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        points: list[float] = []
        for px, py in (
            (-half_width, -half_height),
            (half_width, -half_height),
            (half_width, half_height),
            (-half_width, half_height),
        ):
            rotated_x = center_x + (px * cosine - py * sine)
            rotated_y = center_y + (px * sine + py * cosine)
            points.extend((rotated_x, rotated_y))
        canvas.create_polygon(points, fill=fill, outline="")

    def _draw_auth_scene(self) -> None:
        canvas = self._auth_canvas
        if canvas is None or not canvas.winfo_exists():
            return

        auth = self._auth_palette()
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        base = auth["panel"]
        grid = _mix_colors(auth["panel_text"], auth["panel"], 0.84)
        glow = _mix_colors(auth["field_bg"], auth["panel"], 0.74)
        stream = _mix_colors(auth["accent"], auth["panel"], 0.28)
        node = auth["panel_text"]
        canvas.delete("all")
        canvas.configure(background=base)

        spacing = 42
        drift = int((self._auth_phase * 10) % spacing)
        for x in range(-spacing, width + spacing, spacing):
            canvas.create_line(x + drift, 0, x + drift, height, fill=grid, width=1)
        for y in range(-spacing, height + spacing, spacing):
            canvas.create_line(0, y + drift, width, y + drift, fill=grid, width=1)

        center_x = width * 0.50
        center_y = height * 0.58
        pulse = 1.0 + 0.04 * math.sin(self._auth_phase * 1.5)
        radius = max(min(width, height) * 0.17, 52) * pulse

        canvas.create_oval(width * 0.08, height * 0.18, width * 0.44, height * 0.92, fill=glow, outline="")
        canvas.create_oval(width * 0.60, height * 0.10, width * 0.92, height * 0.62, fill=_mix_colors(auth["accent"], auth["panel"], 0.82), outline="")

        ring_colors = [
            _mix_colors(auth["field_bg"], auth["panel"], 0.38),
            _mix_colors(auth["accent"], auth["panel"], 0.52),
            _mix_colors(auth["panel_text"], auth["panel"], 0.58),
        ]
        for scale, color in zip((1.0, 1.38, 1.76), ring_colors):
            canvas.create_oval(
                center_x - radius * scale,
                center_y - radius * scale,
                center_x + radius * scale,
                center_y + radius * scale,
                outline=color,
                width=2,
            )

        canvas.create_oval(center_x - 10, center_y - 10, center_x + 10, center_y + 10, fill=auth["panel_text"], outline="")

        orbit_radius = radius * 1.40
        for phase_offset, scale, node_radius, color in (
            (0.0, 1.0, 6, auth["accent"]),
            (2.1, 0.82, 5, auth["panel_text"]),
        ):
            angle = self._auth_phase + phase_offset
            px = center_x + math.cos(angle) * orbit_radius * scale
            py = center_y + math.sin(angle) * orbit_radius * 0.55 * scale
            canvas.create_line(center_x, center_y, px, py, fill=_mix_colors(color, auth["panel"], 0.50), width=1)
            canvas.create_oval(px - node_radius, py - node_radius, px + node_radius, py + node_radius, fill=color, outline="")

        wave_base_y = height * 0.76
        for index, amplitude in enumerate((8, 12)):
            points: list[float] = []
            for step in range(18):
                x = width * 0.18 + (width * 0.64) * (step / 17)
                y = wave_base_y + index * 18 + math.sin(self._auth_phase * 1.2 + step * 0.46 + index) * amplitude
                points.extend((x, y))
            canvas.create_line(points, fill=ring_colors[index], width=2, smooth=True)

    def _animate_auth_scene(self) -> None:
        canvas = self._auth_canvas
        if canvas is None or not canvas.winfo_exists() or self._quitting or self.current_user:
            self._auth_after_id = None
            return

        self._auth_phase += 0.025
        self._auth_reveal_progress = min(1.0, self._auth_reveal_progress + 0.07)
        self._position_auth_card()
        self._draw_auth_scene()
        self._auth_after_id = self.after(95, self._animate_auth_scene)

    def _init_background_particles(self) -> None:
        self._background_particles = []
        specs = [
            (0.14, 0.22, 0.20, 0.12, 64),
            (0.78, 0.18, -0.18, 0.10, 54),
            (0.24, 0.72, 0.16, -0.14, 48),
            (0.84, 0.74, -0.12, -0.11, 70),
            (0.56, 0.48, 0.10, 0.18, 44),
            (0.42, 0.16, -0.08, 0.12, 36),
        ]
        for x, y, vx, vy, size in specs:
            self._background_particles.append({"x": x, "y": y, "vx": vx, "vy": vy, "size": size})

    def _on_background_resize(self, _event=None) -> None:
        self._draw_background_frame()

    def _draw_data_stream(self, canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, line_color: str, node_color: str, phase_shift: float = 0.0) -> None:
        canvas.create_line(x1, y1, x2, y2, fill=line_color, width=2, smooth=True)
        progress = (self._background_phase * 0.65 + phase_shift) % 1.0
        px = x1 + (x2 - x1) * progress
        py = y1 + (y2 - y1) * progress
        canvas.create_oval(px - 6, py - 6, px + 6, py + 6, fill=node_color, outline="")

    def _draw_globe(self, canvas: tk.Canvas, center_x: float, center_y: float, radius: float, line_color: str, glow_color: str) -> None:
        canvas.create_oval(center_x - radius, center_y - radius, center_x + radius, center_y + radius, outline=line_color, width=2)
        canvas.create_oval(center_x - radius * 0.72, center_y - radius, center_x + radius * 0.72, center_y + radius, outline=glow_color, width=1)
        canvas.create_oval(center_x - radius * 0.28, center_y - radius, center_x + radius * 0.28, center_y + radius, outline=glow_color, width=1)
        for scale in (0.66, 0.28):
            canvas.create_oval(
                center_x - radius,
                center_y - radius * scale,
                center_x + radius,
                center_y + radius * scale,
                outline=glow_color,
                width=1,
            )
        spin = (self._background_phase * 55) % 360
        sweep = max(radius * 0.92, 10)
        canvas.create_arc(
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
            start=spin,
            extent=34,
            style="arc",
            outline=line_color,
            width=3,
        )
        canvas.create_oval(center_x - 8, center_y - 8, center_x + 8, center_y + 8, fill=line_color, outline="")

    def _draw_background_frame(self) -> None:
        canvas = self._background_canvas
        if canvas is None or not canvas.winfo_exists():
            return

        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        palette = self._background_palette()
        canvas.delete("all")
        canvas.configure(background=palette["base"])

        grid_spacing = 42
        offset = int((self._background_phase * 28) % grid_spacing)
        for x in range(-grid_spacing, width + grid_spacing, grid_spacing):
            canvas.create_line(x + offset, 0, x + offset, height, fill=palette["grid"], width=1)
        for y in range(-grid_spacing, height + grid_spacing, grid_spacing):
            canvas.create_line(0, y + offset, width, y + offset, fill=palette["grid"], width=1)

        pulse_width = int(width * 0.42)
        pulse_height = int(height * 0.28)
        pulse_shift = int((self._background_phase * 45) % max(width, 1))
        canvas.create_oval(
            width - pulse_width - 70 + pulse_shift * 0.05,
            40,
            width - 70 + pulse_shift * 0.05,
            40 + pulse_height,
            fill=palette["pulse"],
            outline="",
        )
        canvas.create_oval(
            40 - pulse_shift * 0.03,
            height - pulse_height - 80,
            40 + pulse_width - pulse_shift * 0.03,
            height - 80,
            fill=palette["pulse"],
            outline="",
        )

        for particle in self._background_particles:
            px = float(particle["x"]) * width
            py = float(particle["y"]) * height
            radius = int(particle["size"])
            canvas.create_oval(px - radius, py - radius, px + radius, py + radius, fill=palette["glow"], outline="")
            inner = max(radius * 0.24, 8)
            canvas.create_oval(px - inner, py - inner, px + inner, py + inner, fill=palette["particle"], outline="")
            canvas.create_line(px - radius * 0.9, py, px + radius * 0.9, py, fill=palette["grid"], width=1)
            canvas.create_line(px, py - radius * 0.9, px, py + radius * 0.9, fill=palette["grid"], width=1)

        globe_radius = max(min(width, height) * 0.14, 80)
        globe_x = width * 0.82
        globe_y = height * 0.26
        self._draw_globe(canvas, globe_x, globe_y, globe_radius, palette["grid"], palette["highlight"])
        self._draw_data_stream(canvas, width * 0.08, height * 0.18, globe_x - globe_radius * 0.7, globe_y - 12, palette["stream"], palette["grid"], 0.08)
        self._draw_data_stream(canvas, width * 0.16, height * 0.78, globe_x - globe_radius * 0.3, globe_y + globe_radius * 0.55, palette["stream"], palette["grid"], 0.42)
        self._draw_data_stream(canvas, globe_x + globe_radius * 0.42, globe_y + globe_radius * 0.72, width * 0.92, height * 0.62, palette["stream"], palette["grid"], 0.74)

        self._draw_sidebar_background()
        self._draw_header_background()

    def _draw_sidebar_background(self) -> None:
        canvas = self._sidebar_background_canvas
        if canvas is None or not canvas.winfo_exists():
            return

        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        base = self.color("sidebar")
        line = _mix_colors(self.color("accent"), self.color("sidebar"), 0.48)
        glow = _mix_colors(self.color("accent"), self.color("card"), 0.18)
        canvas.delete("all")
        canvas.configure(background=base)

        spacing = 36
        drift = int((self._background_phase * 38) % spacing)
        for y in range(-spacing, height + spacing, spacing):
            canvas.create_line(0, y + drift, width, y + drift, fill=line, width=1)
        for x in range(-spacing, width + spacing, spacing):
            canvas.create_line(x + drift, 0, x + drift, height, fill=line, width=1)

        band_y = int(height * 0.16 + ((self._background_phase * 26) % 110))
        canvas.create_rectangle(0, band_y, width, min(height, band_y + 110), fill=glow, outline="")
        self._draw_data_stream(canvas, width * 0.10, height * 0.12, width * 0.88, height * 0.12, line, self.color("card"), 0.16)
        self._draw_data_stream(canvas, width * 0.16, height * 0.86, width * 0.82, height * 0.28, line, self.color("card"), 0.58)

        for particle in self._background_particles[:4]:
            px = float(particle["x"]) * width
            py = float(particle["y"]) * height
            radius = int(particle["size"] * 0.58)
            canvas.create_oval(px - radius, py - radius, px + radius, py + radius, outline=line, width=2)
            canvas.create_line(px - radius, py, px + radius, py, fill=line, width=1)
            canvas.create_line(px, py - radius, px, py + radius, fill=line, width=1)

        globe_radius = max(min(width, height) * 0.16, 42)
        self._draw_globe(canvas, width * 0.50, height * 0.58, globe_radius, line, glow)

    def _draw_header_background(self) -> None:
        canvas = self._header_background_canvas
        if canvas is None or not canvas.winfo_exists():
            return

        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        base = self.color("card")
        line = _mix_colors(self.color("accent"), self.color("card"), 0.44)
        glow = _mix_colors(self.color("accent"), self.color("border"), 0.18)
        canvas.delete("all")
        canvas.configure(background=base)

        drift = int((self._background_phase * 42) % 62)
        for x in range(-80, width + 80, 34):
            canvas.create_line(x + drift, 0, x + 62 + drift, height, fill=line, width=1)

        pulse_width = min(300, max(180, width // 4))
        pulse_offset = int((self._background_phase * 34) % max(width, 1))
        canvas.create_oval(
            width - pulse_width - 40 - pulse_offset * 0.12,
            10,
            width - 40 - pulse_offset * 0.12,
            height - 10,
            fill=glow,
            outline="",
        )
        canvas.create_rectangle(width - 220, 18, width - 32, 30, fill=line, outline="")
        canvas.create_rectangle(width - 176, 42, width - 32, 50, fill=line, outline="")
        canvas.create_rectangle(width - 132, 62, width - 32, 68, fill=line, outline="")
        globe_radius = max(min(height * 0.36, 46), 28)
        globe_x = width - 118
        globe_y = height * 0.52
        self._draw_globe(canvas, globe_x, globe_y, globe_radius, line, glow)
        self._draw_data_stream(canvas, width * 0.38, height * 0.30, globe_x - globe_radius * 0.8, globe_y - 10, line, self.color("accent"), 0.10)
        self._draw_data_stream(canvas, width * 0.30, height * 0.72, globe_x - globe_radius * 0.55, globe_y + globe_radius * 0.45, line, self.color("accent"), 0.46)

    def _animate_background(self) -> None:
        canvas = self._background_canvas
        if canvas is None or not canvas.winfo_exists() or self._quitting:
            self._background_after_id = None
            return

        self._background_phase += 0.018
        for particle in self._background_particles:
            particle["x"] = (float(particle["x"]) + float(particle["vx"]) * 0.0035) % 1.15 - 0.075
            particle["y"] = (float(particle["y"]) + float(particle["vy"]) * 0.0035) % 1.15 - 0.075

        self._draw_background_frame()
        self._background_after_id = self.after(60, self._animate_background)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(
            self.content_shell,
            fg_color=self.color("card"),
            corner_radius=20,
            border_width=1,
            border_color=self.color("border"),
        )
        header.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        header.grid_columnconfigure(0, weight=1)
        self._header_background_canvas = tk.Canvas(
            header,
            highlightthickness=0,
            bd=0,
            relief="flat",
            background=self.color("card"),
            height=120,
        )
        self._header_background_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tk.call("lower", self._header_background_canvas._w)

        title_block = ctk.CTkFrame(header, fg_color="transparent")
        title_block.grid(row=0, column=0, padx=24, pady=20, sticky="w")

        self.header_title = ctk.CTkLabel(
            title_block,
            text=self.current_view,
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=self.color("text"),
        )
        self.header_title.pack(anchor="w")
        self.header_subtitle = ctk.CTkLabel(
            title_block,
            text="A cleaner command center for your automations.",
            font=ctk.CTkFont(size=13),
            text_color=self.color("muted"),
        )
        self.header_subtitle.pack(anchor="w", pady=(4, 0))

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=1, padx=24, pady=20, sticky="e")

        self.header_scheduler_chip = ctk.CTkLabel(
            actions,
            text=" Scheduler Live ",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.color("success"),
            fg_color=self.color("bg"),
            corner_radius=999,
            padx=14,
            pady=6,
        )
        self.header_scheduler_chip.pack(side="right", padx=(10, 0))

        ctk.CTkButton(
            actions,
            text="↻ Refresh",
            command=self.refresh_all,
            fg_color=self.color("accent"),
            hover_color=self.color("accent_hover"),
            text_color=self.color("bg"),
            height=38,
            width=110,
            corner_radius=12,
        ).pack(side="right")

    def _build_quick_action_bar(self) -> None:
        bar = ctk.CTkFrame(
            self.content_shell,
            fg_color=self.color("card"),
            corner_radius=18,
            border_width=1,
            border_color=self.color("border"),
        )
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 18))
        bar.grid_columnconfigure(0, weight=1)
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, padx=18, pady=14, sticky="w")
        ctk.CTkLabel(left, text="Quick Actions", font=ctk.CTkFont(size=14, weight="bold"), text_color=self.color("text")).pack(anchor="w")
        ctk.CTkLabel(left, text="Jump into common actions without switching context.", font=ctk.CTkFont(size=12), text_color=self.color("muted")).pack(anchor="w", pady=(3, 0))

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, padx=18, pady=14, sticky="e")
        search_box = ctk.CTkFrame(right, fg_color="transparent")
        search_box.pack(side="left", padx=(0, 10))
        self.global_search_entry = ctk.CTkEntry(
            search_box,
            placeholder_text="Search tasks, logs, profiles...",
            height=38,
            width=230,
            fg_color=self.color("bg"),
            border_color=self.color("border"),
            text_color=self.color("text"),
            corner_radius=12,
        )
        self.global_search_entry.pack(side="left")
        self.global_search_entry.bind("<Return>", lambda _event: self.run_global_search())
        actions = [
            ("✦ New Task", lambda: self.show_view("Create Automation")),
            ("✉ Test Email", lambda: self.show_view("Email Automation")),
            ("▤ Logs", lambda: self.show_view("Logs")),
            ("⌕ Search", self.run_global_search),
            ("⌘ Command", self.open_command_palette),
        ]
        for text, command in actions:
            ctk.CTkButton(
                right,
                text=text,
                command=command,
                fg_color=self.color("bg"),
                hover_color=self.color("section"),
                text_color=self.color("text"),
                border_width=1,
                border_color=self.color("border"),
                height=38,
                corner_radius=12,
            ).pack(side="left", padx=6)

    def show_view(self, name: str) -> None:
        self.current_view = name
        for frame in self.views.values():
            frame.grid_forget()
        self.views[name].grid(row=0, column=0, sticky="nsew")
        self._update_navigation()
        self._update_header_copy()
        if name in {"Dashboard", "Scheduled Tasks", "Logs"}:
            self.refresh_all()

    def _update_navigation(self) -> None:
        for name, button in self.nav_buttons.items():
            active = name == self.current_view
            button.configure(
                text=f"{self.nav_icons.get(name, '•')}  {name}",
                fg_color=self.color("accent") if active else "transparent",
                hover_color=self.color("accent_hover") if active else self.color("card"),
                text_color=self.color("bg") if active else self.color("text"),
                border_width=1 if active else 0,
                border_color=self.color("accent") if active else self.color("border"),
            )

    def _update_header_copy(self) -> None:
        descriptions = {
            "Dashboard": "See the health, pace, and next actions across the whole workspace.",
            "Create Automation": "Build new flows with guided sections and cleaner defaults.",
            "Scheduled Tasks": "Inspect live automations, details, and execution timing in one place.",
            "Email Automation": "Compose and verify delivery before trusting the schedule.",
            "Logs": "Review history with clearer context for success and failure events.",
            "Settings": "Tune startup, tray behavior, themes, motion, and workflow preferences.",
        }
        self.header_title.configure(text=self.current_view)
        self.header_subtitle.configure(text=descriptions.get(self.current_view, "Manage your automations."))

    def run_global_search(self) -> None:
        query = self.global_search_entry.get().strip() if self.global_search_entry is not None else ""
        self.open_command_palette()
        if self.command_palette_entry is not None:
            self.command_palette_entry.delete(0, "end")
            self.command_palette_entry.insert(0, query)
            self._refresh_command_palette_results()

    def _open_command_palette_event(self, _event=None):
        self.open_command_palette()
        return "break"

    def show_toast(self, message: str, tone: str = "info", duration_ms: int = 2600) -> None:
        tone_map = {
            "info": self.color("accent"),
            "success": self.color("success"),
            "warning": self.color("warning"),
            "error": self.color("danger"),
        }
        toast = ctk.CTkFrame(
            self,
            fg_color=self.color("card"),
            corner_radius=16,
            border_width=1,
            border_color=tone_map.get(tone, self.color("accent")),
        )
        label = ctk.CTkLabel(
            toast,
            text=message,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.color("text"),
            wraplength=260,
            justify="left",
        )
        label.pack(anchor="w", padx=14, pady=12)
        self.toast_stack.append(toast)
        self._reposition_toasts()
        self.after(duration_ms, lambda frame=toast: self._dismiss_toast(frame))

    def _reposition_toasts(self) -> None:
        base_y = 26
        for index, toast in enumerate(self.toast_stack):
            toast.place(relx=1.0, x=-24, y=base_y + index * 72, anchor="ne")

    def _dismiss_toast(self, toast: ctk.CTkFrame) -> None:
        if toast in self.toast_stack:
            self.toast_stack.remove(toast)
        if toast.winfo_exists():
            toast.destroy()
        self._reposition_toasts()

    def open_command_palette(self) -> None:
        if self.command_palette_window is not None and self.command_palette_window.winfo_exists():
            self.command_palette_window.focus_force()
            if self.command_palette_entry is not None:
                self.command_palette_entry.focus_force()
            return

        window = ctk.CTkToplevel(self)
        window.title("Command Palette")
        window.geometry("520x420")
        window.transient(self)
        window.attributes("-topmost", True)
        window.configure(fg_color=self.color("bg"))
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(1, weight=1)
        self.command_palette_window = window
        window.bind("<Escape>", lambda _event: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        entry = ctk.CTkEntry(
            window,
            placeholder_text="Search commands, screens, and quick actions...",
            height=44,
            fg_color=self.color("card"),
            border_color=self.color("border"),
            text_color=self.color("text"),
            corner_radius=14,
        )
        entry.grid(row=0, column=0, padx=18, pady=18, sticky="ew")
        self.command_palette_entry = entry

        results = ctk.CTkScrollableFrame(window, fg_color=self.color("bg"))
        results.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="nsew")
        self.command_palette_results = results

        entry.bind("<KeyRelease>", lambda _event: self._refresh_command_palette_results())
        window.bind("<Destroy>", self._on_command_palette_destroy, add="+")
        self._refresh_command_palette_results()
        entry.focus_force()

    def _on_command_palette_destroy(self, _event=None) -> None:
        if self.command_palette_window is not None and not self.command_palette_window.winfo_exists():
            self.command_palette_window = None
            self.command_palette_entry = None
            self.command_palette_results = None

    def _command_items(self) -> list[tuple[str, str, object]]:
        return [
            ("Open Dashboard", "View health, timeline, and live activity.", lambda: self.show_view("Dashboard")),
            ("Create Automation", "Open the guided automation builder.", lambda: self.show_view("Create Automation")),
            ("Open Scheduled Tasks", "Inspect scheduled tasks and details.", lambda: self.show_view("Scheduled Tasks")),
            ("Open Email Automation", "Compose or schedule email flows.", lambda: self.show_view("Email Automation")),
            ("Open Logs", "Review recent activity and failures.", lambda: self.show_view("Logs")),
            ("Open About", "View developer, features, and usage details.", lambda: self.show_view("About")),
            ("Open Settings", "Adjust tray behavior, motion, and workspace preferences.", lambda: self.show_view("Settings")),
            ("Refresh Workspace", "Refresh dashboard, tasks, and logs.", self.refresh_all),
            ("Send Test Email", "Jump to email automation for testing.", lambda: self.show_view("Email Automation")),
        ]

    def _refresh_command_palette_results(self) -> None:
        if self.command_palette_results is None or self.command_palette_entry is None:
            return
        for child in self.command_palette_results.winfo_children():
            child.destroy()
        query = self.command_palette_entry.get().strip().lower()
        for title, desc, command in self._command_items():
            haystack = f"{title} {desc}".lower()
            if query and query not in haystack:
                continue
            row = ctk.CTkButton(
                self.command_palette_results,
                text=f"{title}\n{desc}",
                command=lambda action=command: self._run_palette_command(action),
                fg_color=self.color("card"),
                hover_color=self.color("section"),
                text_color=self.color("text"),
                anchor="w",
                corner_radius=14,
                height=64,
            )
            row.pack(fill="x", padx=8, pady=6)

    def _run_palette_command(self, command) -> None:
        if self.command_palette_window is not None and self.command_palette_window.winfo_exists():
            self.command_palette_window.destroy()
        command()
        self.show_toast("Command executed.", "info", 1800)

    def _section_title(self, parent: ctk.CTkFrame, title: str, description: str) -> None:
        ctk.CTkLabel(
            parent,
            text=title,
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=self.color("text"),
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            parent,
            text=description,
            font=ctk.CTkFont(size=13),
            text_color=self.color("muted"),
        ).pack(anchor="w", pady=(0, 22))

    def _build_dashboard_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkScrollableFrame(self.content, fg_color=self.color("bg"))
        self._section_title(frame, "Dashboard", "A more visual control center for tasks, health, and next actions.")

        self.dashboard_welcome = ctk.CTkFrame(frame, fg_color=self.color("card"), corner_radius=22, border_width=1, border_color=self.color("border"))
        self.dashboard_welcome.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(self.dashboard_welcome, text="Welcome to TaskFlow", font=ctk.CTkFont(size=22, weight="bold"), text_color=self.color("text")).pack(anchor="w", padx=20, pady=(18, 6))
        ctk.CTkLabel(
            self.dashboard_welcome,
            text="Start by saving a sender profile, creating your first automation, and sending a quick test email to verify delivery.",
            font=ctk.CTkFont(size=12),
            text_color=self.color("muted"),
            wraplength=920,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 12))
        welcome_actions = ctk.CTkFrame(self.dashboard_welcome, fg_color="transparent")
        welcome_actions.pack(anchor="w", padx=20, pady=(0, 18))
        for text, command in [("Create First Task", lambda: self.show_view("Create Automation")), ("Setup Profile", lambda: self.show_view("Email Automation")), ("Review Settings", lambda: self.show_view("Settings"))]:
            ctk.CTkButton(welcome_actions, text=text, command=command, fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=40, corner_radius=12).pack(side="left", padx=(0, 8))

        hero = ctk.CTkFrame(
            frame,
            fg_color=self.color("accent"),
            corner_radius=24,
            border_width=1,
            border_color=self.color("accent"),
        )
        hero.pack(fill="x", pady=(0, 16))
        hero.grid_columnconfigure(0, weight=3)
        hero.grid_columnconfigure(1, weight=2)

        left = ctk.CTkFrame(hero, fg_color="transparent")
        left.grid(row=0, column=0, padx=24, pady=24, sticky="nsew")
        ctk.CTkLabel(left, text="Automation Command Center", font=ctk.CTkFont(size=30, weight="bold"), text_color=self.color("bg")).pack(anchor="w")
        ctk.CTkLabel(
            left,
            text="Create reminders, schedule recurring mail, and inspect live execution without leaving the desktop workspace.",
            font=ctk.CTkFont(size=14),
            text_color=self.color("bg"),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(8, 18))
        hero_actions = ctk.CTkFrame(left, fg_color="transparent")
        hero_actions.pack(anchor="w")
        ctk.CTkButton(
            hero_actions,
            text="Create New Task",
            command=lambda: self.show_view("Create Automation"),
            fg_color=self.color("bg"),
            hover_color=self.color("card"),
            text_color=self.color("text"),
            corner_radius=12,
            height=40,
            width=150,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            hero_actions,
            text="Open Email Tools",
            command=lambda: self.show_view("Email Automation"),
            fg_color="transparent",
            hover_color=self.color("accent_hover"),
            text_color=self.color("bg"),
            border_width=1,
            border_color=self.color("bg"),
            corner_radius=12,
            height=40,
            width=150,
        ).pack(side="left")

        right = ctk.CTkFrame(hero, fg_color=self.color("bg"), corner_radius=20)
        right.grid(row=0, column=1, padx=22, pady=22, sticky="nsew")
        ctk.CTkLabel(right, text="Next Up", font=ctk.CTkFont(size=12, weight="bold"), text_color=self.color("muted")).pack(anchor="w", padx=18, pady=(18, 4))
        self.next_task_name = ctk.CTkLabel(right, text="No tasks yet", font=ctk.CTkFont(size=20, weight="bold"), text_color=self.color("text"), wraplength=300, justify="left")
        self.next_task_name.pack(anchor="w", padx=18)
        self.next_task_meta = ctk.CTkLabel(right, text="Create a task to start the schedule.", font=ctk.CTkFont(size=13), text_color=self.color("muted"), wraplength=300, justify="left")
        self.next_task_meta.pack(anchor="w", padx=18, pady=(6, 16))

        cards = ctk.CTkFrame(frame, fg_color=self.color("bg"))
        cards.pack(fill="x")
        for column in range(4):
            cards.grid_columnconfigure(column, weight=1)

        metrics = [
            ("total_tasks", "Total Tasks", "Everything currently tracked"),
            ("active_tasks", "Active", "Ready to execute"),
            ("email_tasks", "Email Flows", "SMTP-driven automations"),
            ("executions_today", "Runs Today", "Activity in the last day"),
        ]
        for index, (key, label, helper) in enumerate(metrics):
            card = self._metric_card(cards, label, helper)
            card.grid(row=0, column=index, padx=8, pady=8, sticky="nsew")
            self.stats_labels[key] = card._value_label  # type: ignore[attr-defined]

        lower = ctk.CTkFrame(frame, fg_color=self.color("bg"))
        lower.pack(fill="both", expand=True, pady=(16, 0))
        lower.grid_columnconfigure(0, weight=3)
        lower.grid_columnconfigure(1, weight=2)
        lower.grid_columnconfigure(2, weight=2)
        lower.grid_rowconfigure(0, weight=1)

        upcoming_card = self._panel(lower, "Upcoming Tasks", "A live preview of the next tasks in line.")
        upcoming_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.dashboard_tasks = self._create_treeview(
            upcoming_card,
            ("ID", "Task", "Type", "Run At", "Repeat", "Status"),
            (50, 240, 110, 170, 90, 100),
            height=13,
            empty_text="No tasks scheduled yet. Create one from the automation form.",
        )

        summary_card = self._panel(lower, "Workspace Summary", "Clearer context for the app state and storage.")
        summary_card.grid(row=0, column=1, sticky="nsew", padx=8)
        self.health_label = self._status_chip(summary_card, "Scheduler Running", self.color("success"))
        self.health_label.pack(anchor="w", padx=18, pady=(0, 10))
        self.storage_label = ctk.CTkLabel(summary_card, text="", font=ctk.CTkFont(size=13), text_color=self.color("muted"), wraplength=320, justify="left")
        self.storage_label.pack(anchor="w", padx=18, pady=(0, 18))

        highlights = [
            "Create Automation combines reminders and email scheduling in one guided flow.",
            "Scheduled Tasks now includes a live detail panel for the selected row.",
            "Email Automation has a test-send button so delivery issues show up before schedule time.",
        ]
        for text in highlights:
            self._insight_row(summary_card, text).pack(fill="x", padx=18, pady=5)

        telemetry_card = self._panel(lower, "Live Feed", "Recent sends, failures, and automation changes.")
        telemetry_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        self.activity_feed = ctk.CTkFrame(telemetry_card, fg_color="transparent")
        self.activity_feed.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        timeline_card = self._panel(frame, "Upcoming Timeline", "A compact calendar-style lane for the next automation window.")
        timeline_card.pack(fill="x", pady=(16, 0))
        self.timeline_frame = ctk.CTkFrame(timeline_card, fg_color="transparent")
        self.timeline_frame.pack(fill="x", padx=18, pady=(0, 18))

        recent_card = self._panel(frame, "Recent Actions Dock", "A compact memory of the latest important changes across the workspace.")
        recent_card.pack(fill="x", pady=(16, 0))
        self.recent_actions_frame = ctk.CTkFrame(recent_card, fg_color="transparent")
        self.recent_actions_frame.pack(fill="x", padx=18, pady=(0, 18))

        return frame

    def _metric_card(self, parent, title: str, helper: str):
        card = ctk.CTkFrame(
            parent,
            fg_color=self.color("card"),
            corner_radius=18,
            border_width=1,
            border_color=self.color("border"),
        )
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=13, weight="bold"), text_color=self.color("text")).pack(anchor="w", padx=18, pady=(16, 6))
        value = ctk.CTkLabel(card, text="0", font=ctk.CTkFont(size=34, weight="bold"), text_color=self.color("text"))
        value.pack(anchor="w", padx=18)
        ctk.CTkLabel(card, text=helper, font=ctk.CTkFont(size=12), text_color=self.color("muted")).pack(anchor="w", padx=18, pady=(4, 16))
        card._value_label = value  # type: ignore[attr-defined]
        return card

    def _panel(self, parent, title: str, description: str) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(
            parent,
            fg_color=self.color("card"),
            corner_radius=20,
            border_width=1,
            border_color=self.color("border"),
        )
        ctk.CTkLabel(panel, text=title, font=ctk.CTkFont(size=18, weight="bold"), text_color=self.color("text")).pack(anchor="w", padx=20, pady=(20, 6))
        ctk.CTkLabel(panel, text=description, font=ctk.CTkFont(size=12), text_color=self.color("muted"), wraplength=self._content_wrap(760), justify="left").pack(anchor="w", padx=20, pady=(0, 16))
        return panel

    def _insight_row(self, parent, text: str) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color=self.color("bg"), corner_radius=14)
        ctk.CTkLabel(row, text="INFO", width=46, fg_color=self.color("accent"), text_color=self.color("bg"), corner_radius=999, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=10, pady=10)
        ctk.CTkLabel(row, text=text, text_color=self.color("text"), font=ctk.CTkFont(size=12), wraplength=250, justify="left").pack(side="left", padx=(0, 10), pady=10)
        return row

    def _build_create_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkScrollableFrame(self.content, fg_color=self.color("bg"))
        self._section_title(frame, "Create Automation", "A more guided form with stronger grouping, helper text, and better defaults.")

        intro = ctk.CTkFrame(
            frame,
            fg_color=self.color("card"),
            corner_radius=20,
            border_width=1,
            border_color=self.color("border"),
        )
        intro.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(intro, text="Build Flow", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.color("text")).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            intro,
            text="Step 1: choose the task type and schedule. Step 2: fill the matching action section. Step 3: create the task.",
            font=ctk.CTkFont(size=12),
            text_color=self.color("muted"),
        ).pack(anchor="w", padx=18, pady=(0, 16))
        steps = ctk.CTkFrame(intro, fg_color="transparent")
        steps.pack(fill="x", padx=18, pady=(0, 16))
        for chip in ["1 Select Type", "2 Fill Content", "3 Review & Create"]:
            ctk.CTkLabel(steps, text=f" {chip} ", font=ctk.CTkFont(size=11, weight="bold"), text_color=self.color("accent"), fg_color=self.color("section"), corner_radius=999, padx=10, pady=5).pack(side="left", padx=(0, 8))

        card = ctk.CTkFrame(
            frame,
            fg_color=self.color("card"),
            corner_radius=22,
            border_width=1,
            border_color=self.color("border"),
        )
        card.pack(fill="both", expand=True)
        for column in range(2):
            card.grid_columnconfigure(column, weight=1)

        self.task_name_entry = self._form_entry(card, "Task Name", 0, 0, placeholder="Pay electricity bill")
        self.task_type_menu = self._form_menu(card, "Action Type", ["Reminder", "Email"], 0, 1, self._toggle_general_action_fields)
        self.task_date_entry = self._form_entry(card, "Date (YYYY-MM-DD)", 1, 0, datetime.now().strftime(DATE_FORMAT), placeholder="2026-04-25")
        self.task_time_entry = self._form_entry(card, "Time (HH:MM)", 1, 1, (datetime.now() + timedelta(minutes=10)).strftime(TIME_FORMAT), placeholder="18:30")
        self.task_repeat_menu = self._form_menu(card, "Repeat", ["None", "Daily", "Weekly"], 2, 0)

        self.general_form_hint = ctk.CTkLabel(
            card,
            text="Choose Email to unlock sender, recipient, subject, and message fields.",
            font=ctk.CTkFont(size=12),
            text_color=self.color("muted"),
            justify="left",
        )
        self.general_form_hint.grid(row=5, column=0, columnspan=2, padx=18, pady=(0, 12), sticky="w")

        self.reminder_section = self._section_card(card, row=6, title="Reminder Details", subtitle="Write a short message that should appear when the task runs.")
        self.reminder_section.grid_columnconfigure(0, weight=1)
        self.reminder_message_box = self._form_textbox(self.reminder_section, "Reminder Message", 1, 0, height=140)

        self.email_section = self._section_card(card, row=7, title="Email Details", subtitle="Use your Gmail address and Gmail App Password, then fill in the recipient and content.")
        for column in range(2):
            self.email_section.grid_columnconfigure(column, weight=1)
        self.general_profile_menu = self._form_menu(self.email_section, "Sender Profile", ["No saved profiles"], 2, 0, self._on_general_profile_selected, colspan=2)
        self._section_note(
            self.email_section,
            "Choose a saved sender profile to fill the Gmail address and app password automatically.",
            row=2,
        )
        self.sender_email_entry = self._form_entry(self.email_section, "Sender Gmail", 4, 0, placeholder="yourname@gmail.com")
        self.sender_password_entry = self._form_entry(self.email_section, "Gmail App Password", 4, 1, show="*", placeholder="16-character app password")
        self.recipient_entry = self._form_entry(self.email_section, "Recipient Email", 5, 0, placeholder="friend@example.com")
        self.email_subject_entry = self._form_entry(self.email_section, "Email Subject", 5, 1, placeholder="Meeting reminder")
        self.email_message_box = self._form_textbox(self.email_section, "Email Message", 6, 0, colspan=2, height=160)

        action_row = ctk.CTkFrame(card, fg_color="transparent")
        action_row.grid(row=8, column=0, columnspan=2, padx=18, pady=(8, 20), sticky="ew")
        for column in range(2):
            action_row.grid_columnconfigure(column, weight=1)

        self.create_task_button = ctk.CTkButton(
            action_row,
            text="Create Task",
            command=self.create_general_task,
            fg_color=self.color("accent"),
            hover_color=self.color("accent_hover"),
            text_color=self.color("bg"),
            height=44,
            corner_radius=14,
        )
        self.create_task_button.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            action_row,
            text="Reset Form",
            command=self._reset_general_form,
            fg_color=self.color("bg"),
            hover_color=self.color("section"),
            text_color=self.color("text"),
            border_width=1,
            border_color=self.color("border"),
            height=44,
            corner_radius=14,
        ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

        self._toggle_general_action_fields("Reminder")
        return frame

    def _section_card(self, parent, row: int, title: str, subtitle: str) -> ctk.CTkFrame:
        section = ctk.CTkFrame(
            parent,
            fg_color=self.color("section"),
            corner_radius=18,
            border_width=1,
            border_color=self.color("border"),
        )
        section.grid(row=row, column=0, columnspan=2, padx=18, pady=(0, 12), sticky="ew")
        ctk.CTkLabel(section, text=title, font=ctk.CTkFont(size=18, weight="bold"), text_color=self.color("text")).grid(row=0, column=0, columnspan=2, padx=18, pady=(18, 6), sticky="w")
        ctk.CTkLabel(section, text=subtitle, font=ctk.CTkFont(size=12), text_color=self.color("muted"), wraplength=720, justify="left").grid(row=1, column=0, columnspan=2, padx=18, pady=(0, 10), sticky="w")
        return section

    def _build_tasks_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content, fg_color=self.color("bg"))
        self._section_title(frame, "Scheduled Tasks", "Manage live tasks with a richer table and a dedicated detail panel.")

        content = ctk.CTkFrame(frame, fg_color=self.color("bg"))
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0, minsize=390)
        content.grid_rowconfigure(0, weight=1)

        left = self._panel(content, "Automation Table", "Sort columns, browse the schedule, and select a row for details.")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        toolbar = ctk.CTkFrame(left, fg_color="transparent")
        toolbar.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkButton(toolbar, text="Refresh", command=self.refresh_tasks, fg_color=self.color("card"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=38).pack(side="left", padx=(0, 8))
        ctk.CTkButton(toolbar, text="Activate / Deactivate", command=self.toggle_selected_task, fg_color=self.color("card"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=38).pack(side="left", padx=8)
        ctk.CTkButton(toolbar, text="Delete Task", command=self.delete_selected_task, fg_color=self.color("danger"), hover_color=self.color("danger_hover"), height=38).pack(side="left", padx=8)

        self.tasks_tree = self._create_treeview(
            left,
            ("ID", "Task", "Type", "Run At", "Repeat", "Status", "Next Run", "Last Run"),
            (60, 230, 100, 160, 90, 100, 170, 170),
            height=18,
            on_select=self._capture_selected_task,
            empty_text="No scheduled tasks yet. Create one to populate this workspace.",
        )

        right = self._panel(content, "Task Detail", "A quick side panel for the selected automation.")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.configure(width=390)
        self.task_detail_status = self._status_chip(right, "No task selected", self.color("muted"))
        self.task_detail_status.pack(anchor="w", padx=20, pady=(0, 12))
        self.task_detail_title = ctk.CTkLabel(right, text="Select a task from the table", font=ctk.CTkFont(size=22, weight="bold"), text_color=self.color("text"), wraplength=300, justify="left")
        self.task_detail_title.pack(anchor="w", fill="x", padx=20)
        self.task_detail_meta = ctk.CTkLabel(right, text="The right panel will show type, timing, and payload details here.", font=ctk.CTkFont(size=13), text_color=self.color("muted"), wraplength=300, justify="left")
        self.task_detail_meta.pack(anchor="w", padx=20, pady=(8, 18))
        self.task_detail_rows = ctk.CTkFrame(right, fg_color="transparent")
        self.task_detail_rows.pack(fill="x", padx=20)
        self.task_detail_actions = ctk.CTkFrame(right, fg_color="transparent")
        self.task_detail_actions.pack(fill="x", padx=20, pady=(14, 0))
        ctk.CTkButton(self.task_detail_actions, text="Open Builder", command=lambda: self.show_view("Create Automation"), fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=38, corner_radius=12).pack(side="left", padx=(0, 8))
        ctk.CTkButton(self.task_detail_actions, text="Open Email Tools", command=lambda: self.show_view("Email Automation"), fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=38, corner_radius=12).pack(side="left")
        self.task_payload_box = ctk.CTkTextbox(right, height=180, fg_color=self.color("bg"), border_width=1, border_color=self.color("border"), text_color=self.color("text"))
        self.task_payload_box.pack(fill="both", expand=True, padx=20, pady=(18, 20))
        self.task_payload_box.insert("1.0", "Payload details will appear here.")
        self.task_payload_box.configure(state="disabled")
        return frame

    def _build_email_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkScrollableFrame(self.content, fg_color=self.color("bg"))
        self._section_title(frame, "Email Automation", "Advanced email tools: draft from requirements, manage templates and groups, preview, attach files, and schedule with retries.")

        overview = ctk.CTkFrame(frame, fg_color=self.color("card"), corner_radius=18, border_width=1, border_color=self.color("border"))
        overview.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(overview, text="Pro Email Workspace", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.color("text")).pack(anchor="w", padx=18, pady=(14, 4))
        ctk.CTkLabel(
            overview,
            text="Save sender profiles with signatures, use recipient groups, create templates, add attachments, and preview the final result before scheduling.",
            font=ctk.CTkFont(size=12),
            text_color=self.color("muted"),
            wraplength=900,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 14))

        manager_tabs = ctk.CTkTabview(frame, fg_color=self.color("bg"), segmented_button_fg_color=self.color("section"), segmented_button_selected_color=self.color("accent"), segmented_button_selected_hover_color=self.color("accent_hover"), segmented_button_unselected_color=self.color("card"), segmented_button_unselected_hover_color=self.color("section"))
        manager_tabs.pack(fill="x", pady=(0, 16))
        profile_tab = manager_tabs.add("Profiles")
        groups_tab = manager_tabs.add("Groups")
        templates_tab = manager_tabs.add("Templates")
        library_tab = manager_tabs.add("Library")

        for tab in (profile_tab, groups_tab, templates_tab, library_tab):
            for column in range(3):
                tab.grid_columnconfigure(column, weight=1)

        ctk.CTkLabel(profile_tab, text="Sender Profiles", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.color("text")).grid(row=0, column=0, columnspan=3, padx=18, pady=(16, 4), sticky="w")
        self.profile_name_entry = self._form_entry(profile_tab, "Profile Name", 1, 0, placeholder="John Work Mail")
        self.profile_sender_entry = self._form_entry(profile_tab, "Sender Gmail", 1, 1, placeholder="yourname@gmail.com")
        self.profile_password_entry = self._form_entry(profile_tab, "Gmail App Password", 1, 2, show="*", placeholder="16-character app password")
        self.profile_display_name_entry = self._form_entry(profile_tab, "Display Name", 2, 0, placeholder="John Carter")
        self.profile_signature_box = self._form_textbox(profile_tab, "Signature", 2, 1, colspan=2, height=120)
        self.saved_profile_menu = self._form_menu(profile_tab, "Saved Profiles", ["No saved profiles"], 3, 0, self._on_saved_profile_selected, colspan=2)
        ctk.CTkButton(profile_tab, text="Load Selected", command=self.load_selected_profile, fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=42, corner_radius=12).grid(row=7, column=2, padx=18, pady=(2, 12), sticky="ew")
        self._section_note(profile_tab, "Signatures are appended automatically to generated drafts and scheduled emails when a profile is selected.", row=3, colspan=3)
        self.profile_identity_card = ctk.CTkFrame(profile_tab, fg_color=self.color("section"), corner_radius=16, border_width=1, border_color=self.color("border"))
        self.profile_identity_card.grid(row=7, column=0, columnspan=2, padx=18, pady=(0, 12), sticky="ew")
        self.profile_identity_name = ctk.CTkLabel(self.profile_identity_card, text="No profile selected", font=ctk.CTkFont(size=15, weight="bold"), text_color=self.color("text"))
        self.profile_identity_name.pack(anchor="w", padx=14, pady=(14, 4))
        self.profile_identity_meta = ctk.CTkLabel(self.profile_identity_card, text="Saved sender profiles will show identity details here.", font=ctk.CTkFont(size=12), text_color=self.color("muted"), wraplength=460, justify="left")
        self.profile_identity_meta.pack(anchor="w", padx=14, pady=(0, 14))
        profile_actions = ctk.CTkFrame(profile_tab, fg_color="transparent")
        profile_actions.grid(row=8, column=0, columnspan=3, padx=18, pady=(6, 20), sticky="ew")
        for column in range(3):
            profile_actions.grid_columnconfigure(column, weight=1)
        ctk.CTkButton(profile_actions, text="Save Profile", command=lambda: self.save_sender_profile(set_default=False), fg_color=self.color("accent"), hover_color=self.color("accent_hover"), text_color=self.color("bg"), height=42, corner_radius=12).grid(row=0, column=0, padx=(0, 8), sticky="ew")
        ctk.CTkButton(profile_actions, text="Save as Default", command=lambda: self.save_sender_profile(set_default=True), fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=42, corner_radius=12).grid(row=0, column=1, padx=8, sticky="ew")
        ctk.CTkButton(profile_actions, text="Delete Profile", command=self.delete_selected_profile, fg_color=self.color("danger"), hover_color=self.color("danger_hover"), height=42, corner_radius=12).grid(row=0, column=2, padx=(8, 0), sticky="ew")

        ctk.CTkLabel(groups_tab, text="Recipient Groups", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.color("text")).grid(row=0, column=0, columnspan=3, padx=18, pady=(16, 4), sticky="w")
        self.group_name_entry = self._form_entry(groups_tab, "Group Name", 1, 0, placeholder="Marketing Team")
        self.group_recipients_box = self._form_textbox(groups_tab, "Recipients", 1, 1, colspan=2, height=120)
        self.group_notes_box = self._form_textbox(groups_tab, "Notes", 2, 0, colspan=3, height=90)
        self.saved_group_menu = self._form_menu(groups_tab, "Saved Groups", ["No saved groups"], 3, 0, self._on_saved_group_selected, colspan=2)
        ctk.CTkButton(groups_tab, text="Apply Group to Email", command=self.apply_selected_group, fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=42, corner_radius=12).grid(row=7, column=2, padx=18, pady=(0, 10), sticky="ew")
        self._section_note(groups_tab, "Enter recipient emails separated by commas, semicolons, or new lines.", row=3, colspan=3)
        group_actions = ctk.CTkFrame(groups_tab, fg_color="transparent")
        group_actions.grid(row=8, column=0, columnspan=3, padx=18, pady=(6, 20), sticky="ew")
        group_actions.grid_columnconfigure(0, weight=1)
        group_actions.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(group_actions, text="Save Group", command=self.save_recipient_group, fg_color=self.color("accent"), hover_color=self.color("accent_hover"), text_color=self.color("bg"), height=42, corner_radius=12).grid(row=0, column=0, padx=(0, 8), sticky="ew")
        ctk.CTkButton(group_actions, text="Delete Group", command=self.delete_selected_group, fg_color=self.color("danger"), hover_color=self.color("danger_hover"), height=42, corner_radius=12).grid(row=0, column=1, padx=(8, 0), sticky="ew")

        ctk.CTkLabel(templates_tab, text="Email Templates", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.color("text")).grid(row=0, column=0, columnspan=3, padx=18, pady=(16, 4), sticky="w")
        self.template_name_entry = self._form_entry(templates_tab, "Template Name", 1, 0, placeholder="Weekly Status")
        self.template_tone_menu = self._form_menu(templates_tab, "Tone", ["Professional", "Friendly", "Urgent"], 1, 1)
        self.template_notes_entry = self._form_entry(templates_tab, "Notes", 1, 2, placeholder="Use for Friday summary mail")
        self.template_subject_entry = self._form_entry(templates_tab, "Subject Template", 2, 0, placeholder="Weekly Update - {date}", colspan=3)
        self.template_body_box = self._form_textbox(templates_tab, "Body Template", 3, 0, colspan=3, height=160)
        self.saved_template_menu = self._form_menu(templates_tab, "Saved Templates", ["No saved templates"], 4, 0, self._on_saved_template_selected, colspan=2)
        ctk.CTkButton(templates_tab, text="Apply Template", command=self.apply_selected_template, fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=42, corner_radius=12).grid(row=9, column=2, padx=18, pady=(0, 10), sticky="ew")
        self._section_note(templates_tab, "Use placeholders like {name}, {date}, {project}, {deadline}.", row=4, colspan=3)
        template_actions = ctk.CTkFrame(templates_tab, fg_color="transparent")
        template_actions.grid(row=10, column=0, columnspan=3, padx=18, pady=(6, 20), sticky="ew")
        template_actions.grid_columnconfigure(0, weight=1)
        template_actions.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(template_actions, text="Save Template", command=self.save_email_template, fg_color=self.color("accent"), hover_color=self.color("accent_hover"), text_color=self.color("bg"), height=42, corner_radius=12).grid(row=0, column=0, padx=(0, 8), sticky="ew")
        ctk.CTkButton(template_actions, text="Delete Template", command=self.delete_selected_template, fg_color=self.color("danger"), hover_color=self.color("danger_hover"), height=42, corner_radius=12).grid(row=0, column=1, padx=(8, 0), sticky="ew")

        ctk.CTkLabel(library_tab, text="Workspace Backup", font=ctk.CTkFont(size=18, weight="bold"), text_color=self.color("text")).grid(row=0, column=0, columnspan=3, padx=18, pady=(16, 4), sticky="w")
        ctk.CTkLabel(library_tab, text="Export or import sender profiles, recipient groups, and templates as JSON.", font=ctk.CTkFont(size=12), text_color=self.color("muted")).grid(row=1, column=0, columnspan=3, padx=18, pady=(0, 16), sticky="w")
        ctk.CTkButton(library_tab, text="Export Workspace", command=self.export_email_workspace, fg_color=self.color("accent"), hover_color=self.color("accent_hover"), text_color=self.color("bg"), height=42, corner_radius=12).grid(row=2, column=0, padx=18, pady=(0, 18), sticky="ew")
        ctk.CTkButton(library_tab, text="Import Workspace", command=self.import_email_workspace, fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=42, corner_radius=12).grid(row=2, column=1, padx=18, pady=(0, 18), sticky="ew")
        card = ctk.CTkFrame(frame, fg_color=self.color("card"), corner_radius=22, border_width=1, border_color=self.color("border"))
        card.pack(fill="both", expand=True)
        for column in range(2):
            card.grid_columnconfigure(column, weight=1)

        self.email_profile_menu = self._form_menu(card, "Sender Profile", ["No saved profiles"], 0, 0, self._on_email_profile_selected, colspan=2)
        self.email_group_menu = self._form_menu(card, "Recipient Group", ["No saved groups"], 1, 0, colspan=2)
        self.email_template_apply_menu = self._form_menu(card, "Apply Template", ["No saved templates"], 2, 0, colspan=2)
        self.email_task_name_entry = self._form_entry(card, "Task Name", 3, 0, placeholder="Send weekly report")
        self.email_sender_entry = self._form_entry(card, "Sender Gmail", 3, 1, placeholder="yourname@gmail.com")
        self.email_password_entry = self._form_entry(card, "Gmail App Password", 4, 0, show="*", placeholder="16-character app password")
        self.email_recipient_entry = self._form_entry(card, "Recipient Email", 4, 1, placeholder="manager@example.com or team list")
        self.email_subject_only_entry = self._form_entry(card, "Subject", 5, 0, placeholder="Weekly update")
        self.email_date_entry = self._form_entry(card, "Date (YYYY-MM-DD)", 5, 1, datetime.now().strftime(DATE_FORMAT), placeholder="2026-04-25")
        self.email_time_entry = self._form_entry(card, "Time (HH:MM)", 6, 0, (datetime.now() + timedelta(minutes=15)).strftime(TIME_FORMAT), placeholder="09:00")
        self.email_repeat_menu = self._form_menu(card, "Repeat", ["None", "Daily", "Weekly"], 6, 1)
        self.email_message_only_box = self._form_textbox(card, "Message", 7, 0, colspan=2, height=190)

        attachment_row = ctk.CTkFrame(card, fg_color="transparent")
        attachment_row.grid(row=18, column=0, columnspan=2, padx=18, pady=(10, 12), sticky="ew")
        attachment_row.grid_columnconfigure(0, weight=1)
        attachment_row.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(attachment_row, text="Add Attachments", command=self.select_attachments, fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=42, corner_radius=12).grid(row=0, column=0, padx=(0, 8), sticky="ew")
        ctk.CTkButton(attachment_row, text="Clear Attachments", command=self.clear_attachments, fg_color=self.color("card"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=42, corner_radius=12).grid(row=0, column=1, padx=(8, 0), sticky="ew")
        self.attachment_label = ctk.CTkLabel(card, text="No attachments selected.", font=ctk.CTkFont(size=12), text_color=self.color("muted"), wraplength=820, justify="left")
        self.attachment_label.grid(row=19, column=0, columnspan=2, padx=18, pady=(0, 10), sticky="w")

        preview_card = ctk.CTkFrame(card, fg_color=self.color("section"), corner_radius=16, border_width=1, border_color=self.color("border"))
        preview_card.grid(row=20, column=0, columnspan=2, padx=18, pady=(0, 12), sticky="ew")
        ctk.CTkLabel(preview_card, text="Preview", font=ctk.CTkFont(size=16, weight="bold"), text_color=self.color("text")).pack(anchor="w", padx=16, pady=(14, 4))
        self.preview_info_label = ctk.CTkLabel(preview_card, text="Preview the final rendered email before you send or schedule it.", font=ctk.CTkFont(size=12), text_color=self.color("muted"), wraplength=820, justify="left")
        self.preview_info_label.pack(anchor="w", padx=16, pady=(0, 10))
        self.preview_box = ctk.CTkTextbox(preview_card, height=180, fg_color=self.color("bg"), border_width=1, border_color=self.color("border"), text_color=self.color("text"))
        self.preview_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=21, column=0, columnspan=2, padx=18, pady=(8, 20), sticky="ew")
        for column in range(3):
            actions.grid_columnconfigure(column, weight=1)

        ctk.CTkButton(actions, text="Create Email Automation", command=self.create_email_task, fg_color=self.color("accent"), hover_color=self.color("accent_hover"), text_color=self.color("bg"), height=44, corner_radius=14).grid(row=0, column=0, padx=(0, 8), sticky="ew")
        ctk.CTkButton(actions, text="Send Test Email Now", command=self.send_test_email, fg_color=self.color("bg"), hover_color=self.color("section"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=44, corner_radius=14).grid(row=0, column=1, padx=8, sticky="ew")
        ctk.CTkButton(actions, text="Reset Email Form", command=self._reset_email_form, fg_color=self.color("card"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=44, corner_radius=14).grid(row=0, column=2, padx=(8, 0), sticky="ew")
        return frame

    def _build_about_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkScrollableFrame(self.content, fg_color=self.color("bg"))
        self._section_title(frame, "About", "A simple overview of TaskFlow, who built it, and how to use it.")

        hero = ctk.CTkFrame(frame, fg_color=self.color("card"), corner_radius=22, border_width=1, border_color=self.color("border"))
        hero.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(hero, text="TASKFLOW", font=ctk.CTkFont(family="Baskerville Old Face", size=28, weight="bold"), text_color=self.color("text")).pack(anchor="w", padx=20, pady=(18, 6))
        ctk.CTkLabel(
            hero,
            text="Desktop scheduling for reminders and email with private user workspaces, saved sender profiles, and scheduled execution.",
            font=ctk.CTkFont(size=13),
            text_color=self.color("muted"),
            wraplength=920,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 18))

        developer_card = self._panel(frame, "Developer", "Created and maintained by:")
        developer_card.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(developer_card, text="Vishwajith", font=ctk.CTkFont(size=24, weight="bold"), text_color=self.color("text")).pack(anchor="w", padx=18, pady=(0, 6))
        ctk.CTkLabel(developer_card, text="Built as a smart desktop workflow tool for reminders, scheduled email, and organized personal workspaces.", font=ctk.CTkFont(size=12), text_color=self.color("muted"), wraplength=900, justify="left").pack(anchor="w", padx=18, pady=(0, 18))

        features_card = self._panel(frame, "Features", "Core things TaskFlow can do right now.")
        features_card.pack(fill="x", pady=(0, 16))
        for text in [
            "Create reminder and email automations from one desktop app.",
            "Save sender profiles with Gmail address and app password.",
            "Keep tasks, templates, groups, and profiles scoped to each user account.",
            "Review scheduled tasks, logs, and execution details in one place.",
            "Run in the background and support scheduled execution from the tray.",
        ]:
            self._insight_row(features_card, text).pack(fill="x", padx=18, pady=5)

        usage_card = self._panel(frame, "Usage", "A quick flow for using the application.")
        usage_card.pack(fill="x", pady=(0, 16))
        for step in [
            "1. Sign in or create your account on the welcome screen.",
            "2. Open Email Automation and save your sender profile.",
            "3. Create a reminder or email task with date and time.",
            "4. Keep TaskFlow open, or let it continue in the system tray for scheduled runs.",
            "5. Use Scheduled Tasks and Logs to monitor execution.",
        ]:
            ctk.CTkLabel(usage_card, text=step, font=ctk.CTkFont(size=12), text_color=self.color("text"), wraplength=900, justify="left").pack(anchor="w", padx=18, pady=5)

        notes_card = self._panel(frame, "Notes", "Important reminders for users.")
        notes_card.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(
            notes_card,
            text="For Gmail sending, use your Gmail address along with a Google App Password. Scheduled tasks continue only while the TaskFlow background process is running.",
            font=ctk.CTkFont(size=12),
            text_color=self.color("muted"),
            wraplength=900,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 18))

        return frame

    def _build_logs_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content, fg_color=self.color("bg"))
        self._section_title(frame, "Logs", "A cleaner history view with context in the side panel.")

        content = ctk.CTkFrame(frame, fg_color=self.color("bg"))
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=5)
        content.grid_columnconfigure(1, weight=3)
        content.grid_rowconfigure(0, weight=1)

        left = self._panel(content, "Execution History", "Browse recent success and failure events.")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkButton(left, text="Refresh Logs", command=self.refresh_logs, fg_color=self.color("card"), text_color=self.color("text"), border_width=1, border_color=self.color("border"), height=38).pack(anchor="w", padx=18, pady=(0, 12))
        self.log_summary_strip = ctk.CTkFrame(left, fg_color="transparent")
        self.log_summary_strip.pack(fill="x", padx=18, pady=(0, 12))
        self.log_info_card = self._status_chip(self.log_summary_strip, "INFO 0", self.color("success"))
        self.log_info_card.pack(side="left", padx=(0, 8))
        self.log_error_card = self._status_chip(self.log_summary_strip, "ERROR 0", self.color("danger"))
        self.log_error_card.pack(side="left", padx=8)
        self.logs_tree = self._create_treeview(
            left,
            ("Time", "Level", "Task", "Message"),
            (170, 90, 220, 700),
            height=20,
            on_select=self._capture_selected_log,
            empty_text="No log entries yet. Run or test a task to see activity here.",
        )

        right = self._panel(content, "Log Detail", "Selected log information and message text.")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.log_level_chip = self._status_chip(right, "No log selected", self.color("muted"))
        self.log_level_chip.pack(anchor="w", padx=20, pady=(0, 12))
        self.log_task_label = ctk.CTkLabel(right, text="Select a row from the log table", font=ctk.CTkFont(size=20, weight="bold"), text_color=self.color("text"), wraplength=320, justify="left")
        self.log_task_label.pack(anchor="w", padx=20)
        self.log_time_label = ctk.CTkLabel(right, text="", font=ctk.CTkFont(size=13), text_color=self.color("muted"), wraplength=320, justify="left")
        self.log_time_label.pack(anchor="w", padx=20, pady=(8, 18))
        self.log_message_box = ctk.CTkTextbox(right, height=250, fg_color=self.color("bg"), border_width=1, border_color=self.color("border"), text_color=self.color("text"))
        self.log_message_box.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.log_message_box.insert("1.0", "The selected log message will appear here.")
        self.log_message_box.configure(state="disabled")
        return frame

    def _build_settings_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkScrollableFrame(self.content, fg_color=self.color("bg"))
        self._section_title(frame, "Settings", "Tune the workspace behavior, tray flow, motion, and keyboard-first shortcuts.")

        shell = ctk.CTkFrame(frame, fg_color=self.color("card"), corner_radius=22, border_width=1, border_color=self.color("border"))
        shell.pack(fill="x", pady=(0, 16))
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_columnconfigure(1, weight=1)

        general = self._section_card(shell, row=0, title="Workspace Layout", subtitle="Shape how TaskFlow behaves the moment you open it.")
        general.grid_columnconfigure(0, weight=1)
        general.grid_columnconfigure(1, weight=1)
        self.settings_navigation_mode = self._form_menu(general, "Navigation Layout", ["Expanded"], 1, 0)
        self.settings_navigation_mode.set("Expanded")
        self._section_note(general, "TaskFlow now uses a fixed expanded sidebar and a built-in visual style for a steadier desktop layout.", row=1, colspan=2)

        runtime = self._section_card(shell, row=1, title="Runtime Behavior", subtitle="Background operation, tray support, and motion controls.")
        runtime.grid_columnconfigure(0, weight=1)
        runtime.grid_columnconfigure(1, weight=1)
        self.settings_tray_mode = self._form_menu(runtime, "Close Behavior", ["Finish Active Tasks In Tray", "Exit Immediately"], 1, 0)
        self.settings_tray_mode.set("Finish Active Tasks In Tray")
        self.settings_motion_mode = self._form_menu(runtime, "Motion Style", ["Cinematic", "Balanced", "Calm"], 1, 1)
        self.settings_motion_mode.set("Balanced")
        self._section_note(runtime, "Tray mode keeps the scheduler alive after the window closes. Motion style is a visual preference placeholder for future fine-tuning.", row=1)

        shortcuts = self._panel(frame, "Keyboard Shortcuts", "Fast paths for command-heavy workflows.")
        shortcuts.pack(fill="x", pady=(0, 16))
        shortcut_rows = [
            ("Ctrl+K", "Open command palette"),
            ("Enter", "Run the current global search"),
            ("Refresh button", "Reload dashboard, tasks, and logs"),
            ("Quick Actions", "Jump to common screens and actions"),
        ]
        for key, desc in shortcut_rows:
            self._detail_row(shortcuts, key, desc).pack(fill="x", padx=18, pady=5)

        diagnostics = self._panel(frame, "Diagnostics", "Quick status readouts for core app subsystems.")
        diagnostics.pack(fill="x")
        for label, value in [
            ("Scheduler", "Running" if self.scheduler.scheduler.running else "Stopped"),
            ("Tray Support", "Available" if self._tray_supported() else "Needs dependencies"),
            ("Database", str(self.database.db_path)),
        ]:
            self._detail_row(diagnostics, label, value).pack(fill="x", padx=18, pady=5)

        session = self._panel(frame, "Account Session", "Only the signed-in user can access their saved credentials and workspace data.")
        session.pack(fill="x", pady=(16, 0))
        self._detail_row(session, "Signed In As", self.current_user["username"] if self.current_user else "Guest").pack(fill="x", padx=18, pady=5)
        ctk.CTkButton(session, text="Logout", command=self.logout_user, fg_color=self.color("danger"), hover_color=self.color("danger_hover"), height=42, corner_radius=12).pack(anchor="w", padx=18, pady=(10, 18))

        return frame

    def _form_entry(self, parent, label, row, column, default_text="", show=None, placeholder=None, colspan=1):
        ctk.CTkLabel(parent, text=label, text_color=self.color("text"), font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=row * 2,
            column=column,
            columnspan=colspan,
            padx=18,
            pady=(20, 8),
            sticky="w",
        )
        entry = ctk.CTkEntry(
            parent,
            height=44,
            fg_color=self.color("bg"),
            border_color=self.color("border"),
            text_color=self.color("text"),
            show=show,
            placeholder_text=placeholder,
            placeholder_text_color=self.color("muted"),
            corner_radius=12,
        )
        entry.grid(row=row * 2 + 1, column=column, columnspan=colspan, padx=18, pady=(0, 14), sticky="ew")
        if default_text:
            entry.insert(0, default_text)
        return entry

    def _form_menu(self, parent, label, values, row, column, command=None, colspan=1):
        ctk.CTkLabel(parent, text=label, text_color=self.color("text"), font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=row * 2,
            column=column,
            columnspan=colspan,
            padx=18,
            pady=(20, 8),
            sticky="w",
        )
        menu = ctk.CTkOptionMenu(
            parent,
            values=values,
            command=command,
            height=44,
            fg_color=self.color("accent"),
            button_color=self.color("accent"),
            button_hover_color=self.color("accent_hover"),
            text_color=self.color("bg"),
            dropdown_fg_color=self.color("card"),
            dropdown_text_color=self.color("text"),
            corner_radius=12,
        )
        menu.grid(row=row * 2 + 1, column=column, columnspan=colspan, padx=18, pady=(0, 14), sticky="ew")
        menu.set(values[0])
        return menu

    def _section_note(self, parent, text: str, row: int, colspan: int = 2) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=12),
            text_color=self.color("muted"),
            wraplength=820,
            justify="left",
        ).grid(
            row=row * 2 + 2,
            column=0,
            columnspan=colspan,
            padx=18,
            pady=(2, 14),
            sticky="w",
        )

    def _form_textbox(self, parent, label, row, column, colspan=1, height=120):
        ctk.CTkLabel(parent, text=label, text_color=self.color("text"), font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=row * 2,
            column=column,
            columnspan=colspan,
            padx=18,
            pady=(20, 8),
            sticky="w",
        )
        textbox = ctk.CTkTextbox(
            parent,
            height=height,
            fg_color=self.color("bg"),
            border_width=1,
            border_color=self.color("border"),
            text_color=self.color("text"),
            corner_radius=12,
        )
        textbox.grid(row=row * 2 + 1, column=column, columnspan=colspan, padx=18, pady=(0, 14), sticky="ew")
        return textbox

    def _status_chip(self, parent, text: str, text_color: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=f" {text} ",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=text_color,
            fg_color=self.color("bg"),
            corner_radius=999,
            padx=12,
            pady=5,
        )

    def _enable_smooth_scrolling(self) -> None:
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Shift-MouseWheel>")
        self.bind_all("<MouseWheel>", self._handle_global_mousewheel, add="+")
        self.bind_all("<Shift-MouseWheel>", self._handle_global_shift_mousewheel, add="+")
        for scrollable_name in ("Dashboard", "Create Automation", "Email Automation"):
            frame = self.views.get(scrollable_name)
            if isinstance(frame, ctk.CTkScrollableFrame):
                self.scrollable_frames.append(frame)

    def _detect_active_scroll_frame(self):
        pointer_widget = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        if pointer_widget is None:
            self.active_scroll_frame = None
            return None

        for scrollable_frame in self.scrollable_frames:
            canvas = getattr(scrollable_frame, "_parent_canvas", None)
            if canvas is None:
                continue
            if pointer_widget == canvas or self._widget_is_descendant_of(pointer_widget, canvas):
                self.active_scroll_frame = scrollable_frame
                return scrollable_frame

        self.active_scroll_frame = None
        return None

    @staticmethod
    def _widget_is_descendant_of(widget, ancestor) -> bool:
        current = widget
        while current is not None:
            if current == ancestor:
                return True
            parent_name = current.winfo_parent()
            if not parent_name:
                return False
            try:
                current = current.nametowidget(parent_name)
            except Exception:  # noqa: BLE001
                return False
        return False

    def _handle_global_mousewheel(self, event) -> None:
        widget = event.widget
        if isinstance(widget, (ttk.Treeview, ctk.CTkTextbox)):
            return
        target = self._detect_active_scroll_frame() or self.active_scroll_frame
        if target is not None:
            self._on_mousewheel(event, target)

    def _handle_global_shift_mousewheel(self, event) -> None:
        widget = event.widget
        if isinstance(widget, (ttk.Treeview, ctk.CTkTextbox)):
            return
        target = self._detect_active_scroll_frame() or self.active_scroll_frame
        if target is not None:
            self._on_mousewheel(event, target, horizontal=True)

    def _on_mousewheel(self, event, scrollable_frame, horizontal: bool = False):
        canvas = getattr(scrollable_frame, "_parent_canvas", None)
        if canvas is None:
            return
        delta = event.delta
        if delta == 0:
            return
        bbox = canvas.bbox("all")
        if not bbox:
            return

        content_span = (bbox[2] - bbox[0]) if horizontal else (bbox[3] - bbox[1])
        viewport_span = canvas.winfo_width() if horizontal else canvas.winfo_height()
        scrollable_span = max(1, content_span - viewport_span)
        current_fraction = canvas.xview()[0] if horizontal else canvas.yview()[0]
        current_offset = current_fraction * scrollable_span

        pixels_per_notch = 14
        movement = (delta / 120) * pixels_per_notch
        new_offset = min(max(0, current_offset - movement), scrollable_span)
        new_fraction = 0 if scrollable_span <= 0 else new_offset / scrollable_span

        self._animate_scroll_to(canvas, new_fraction, axis="x" if horizontal else "y")

    def _animate_scroll_to(self, canvas, target_fraction: float, axis: str = "y") -> None:
        target_fraction = min(max(0.0, target_fraction), 1.0)
        self._scroll_animation_axis = axis
        self._scroll_animation_target = target_fraction

        if self._scroll_animation_after_id is None:
            self._run_scroll_animation(canvas)

    def _run_scroll_animation(self, canvas) -> None:
        axis = self._scroll_animation_axis
        current_fraction = canvas.xview()[0] if axis == "x" else canvas.yview()[0]
        target_fraction = self._scroll_animation_target
        distance = target_fraction - current_fraction

        if abs(distance) < 0.0015:
            if axis == "x":
                canvas.xview_moveto(target_fraction)
            else:
                canvas.yview_moveto(target_fraction)
            self._scroll_animation_after_id = None
            return

        eased_step = distance * 0.28
        next_fraction = current_fraction + eased_step
        if axis == "x":
            canvas.xview_moveto(next_fraction)
        else:
            canvas.yview_moveto(next_fraction)

        self._scroll_animation_after_id = self.after(12, lambda: self._run_scroll_animation(canvas))


    def _create_treeview(self, parent, columns, widths, height=16, on_select=None, empty_text="No data to show."):
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(container, columns=columns, show="headings", height=height)
        for column, width in zip(columns, widths, strict=True):
            tree.heading(column, text=column, command=lambda col=column: self._sort_treeview(tree, col, False))
            tree.column(column, width=width, anchor="w")
        tree.tag_configure("evenrow", background=self.color("card"))
        tree.tag_configure("oddrow", background=self.color("section"))

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        empty_label = ctk.CTkLabel(
            container,
            text=empty_text,
            text_color=self.color("muted"),
            font=ctk.CTkFont(size=13),
            wraplength=520,
            justify="center",
        )
        empty_label.place(relx=0.5, rely=0.5, anchor="center")

        if on_select:
            tree.bind("<<TreeviewSelect>>", on_select)

        self.tree_meta[tree] = {"empty_label": empty_label, "insert_index": 0}
        return tree

    def _sort_treeview(self, tree: ttk.Treeview, column: str, reverse: bool) -> None:
        rows = [(tree.set(item, column), item) for item in tree.get_children("")]

        def convert(value: str):
            stripped = value.strip("[] ")
            try:
                return int(stripped)
            except ValueError:
                return stripped.lower()

        rows.sort(key=lambda pair: convert(pair[0]), reverse=reverse)
        for index, (_, item) in enumerate(rows):
            tree.move(item, "", index)
            tree.item(item, tags=("evenrow" if index % 2 == 0 else "oddrow",))
        tree.heading(column, command=lambda: self._sort_treeview(tree, column, not reverse))

    def _toggle_general_action_fields(self, selected_type: str) -> None:
        if selected_type == "Email":
            self.reminder_section.grid_remove()
            self.email_section.grid()
            self.general_form_hint.configure(text="Email mode is active. Sender and recipient fields are ready.")
            self.create_task_button.configure(text="Create Email Task")
            self._apply_selected_profile(self.general_profile_menu, self.sender_email_entry, self.sender_password_entry, allow_default=True)
        else:
            self.email_section.grid_remove()
            self.reminder_section.grid()
            self.general_form_hint.configure(text="Reminder mode is active. Switch Action Type to Email for mail delivery.")
            self.create_task_button.configure(text="Create Reminder")

    def refresh_sender_profiles(self) -> None:
        self.sender_profiles = self.database.fetch_sender_profiles(self._user_id())
        self.profile_labels = [self._profile_label(profile) for profile in self.sender_profiles]
        values = self.profile_labels or ["No saved profiles"]

        for menu_name in ("general_profile_menu", "saved_profile_menu", "email_profile_menu"):
            menu = getattr(self, menu_name, None)
            if menu is not None:
                menu.configure(values=values)
                menu.set(values[0])

        default_profile = self.database.get_default_sender_profile(self._user_id())
        if default_profile:
            label = self._profile_label(default_profile)
            for menu_name in ("general_profile_menu", "saved_profile_menu", "email_profile_menu"):
                menu = getattr(self, menu_name, None)
                if menu is not None:
                    menu.set(label)
            if hasattr(self, "profile_name_entry"):
                self._populate_profile_editor(default_profile)
            if hasattr(self, "sender_email_entry"):
                self._populate_sender_fields(self.sender_email_entry, self.sender_password_entry, default_profile)
            if hasattr(self, "email_sender_entry"):
                self._populate_sender_fields(self.email_sender_entry, self.email_password_entry, default_profile)

    def refresh_recipient_groups(self) -> None:
        self.recipient_groups = self.database.fetch_recipient_groups(self._user_id())
        self.recipient_group_labels = [group["group_name"] for group in self.recipient_groups]
        values = self.recipient_group_labels or ["No saved groups"]
        for menu_name in ("saved_group_menu", "email_group_menu"):
            menu = getattr(self, menu_name, None)
            if menu is not None:
                menu.configure(values=values)
                menu.set(values[0])

    def refresh_email_templates(self) -> None:
        self.email_templates = self.database.fetch_email_templates(self._user_id())
        self.email_template_labels = [template["template_name"] for template in self.email_templates]
        values = self.email_template_labels or ["No saved templates"]
        for menu_name in ("saved_template_menu", "email_template_apply_menu"):
            menu = getattr(self, menu_name, None)
            if menu is not None:
                menu.configure(values=values)
                menu.set(values[0])

    def _profile_label(self, profile: dict) -> str:
        suffix = " [Default]" if profile.get("is_default") else ""
        return f"{profile['profile_name']} - {profile['sender_email']}{suffix}"

    def _task_health(self, task: dict) -> tuple[str, str]:
        if task["status"] != "active":
            return "Dormant", self.color("muted")
        if task["action_type"] == "Email":
            payload = task.get("payload", {})
            if not payload.get("sender_email") or not payload.get("app_password"):
                return "Needs Sender", self.color("warning")
            return "Ready to Send", self.color("success")
        return "Ready", self.color("success")

    def _get_profile_by_label(self, label: str | None) -> dict | None:
        if not label:
            return None
        for profile in self.sender_profiles:
            if self._profile_label(profile) == label:
                return profile
        return None

    def _get_group_by_name(self, group_name: str | None) -> dict | None:
        if not group_name:
            return None
        for group in self.recipient_groups:
            if group["group_name"] == group_name:
                return group
        return None

    def _get_template_by_name(self, template_name: str | None) -> dict | None:
        if not template_name:
            return None
        for template in self.email_templates:
            if template["template_name"] == template_name:
                return template
        return None

    def _on_general_profile_selected(self, _value: str) -> None:
        self._apply_selected_profile(self.general_profile_menu, self.sender_email_entry, self.sender_password_entry)

    def _on_saved_profile_selected(self, value: str) -> None:
        profile = self._get_profile_by_label(value)
        if profile:
            self._populate_profile_editor(profile)

    def load_selected_profile(self) -> None:
        profile = self._get_profile_by_label(self.saved_profile_menu.get()) if hasattr(self, "saved_profile_menu") else None
        if not profile:
            messagebox.showwarning(APP_NAME, "Select a sender profile first.")
            return
        self._populate_profile_editor(profile)
        if hasattr(self, "sender_email_entry"):
            self._populate_sender_fields(self.sender_email_entry, self.sender_password_entry, profile)
        if hasattr(self, "email_sender_entry"):
            self._populate_sender_fields(self.email_sender_entry, self.email_password_entry, profile)

    def _on_email_profile_selected(self, _value: str) -> None:
        self._apply_selected_profile(self.email_profile_menu, self.email_sender_entry, self.email_password_entry)

    def _apply_selected_profile(self, menu, email_entry, password_entry, allow_default: bool = False) -> dict | None:
        profile = self._get_profile_by_label(menu.get()) if menu else None
        if profile is None and allow_default:
            profile = self.database.get_default_sender_profile(self._user_id())
        if profile:
            self._populate_sender_fields(email_entry, password_entry, profile)
        return profile

    def _populate_sender_fields(self, email_entry, password_entry, profile: dict) -> None:
        email_entry.delete(0, "end")
        email_entry.insert(0, profile["sender_email"])
        password_entry.delete(0, "end")
        password_entry.insert(0, profile["app_password"])

    def _populate_profile_editor(self, profile: dict) -> None:
        self.profile_name_entry.delete(0, "end")
        self.profile_name_entry.insert(0, profile["profile_name"])
        self.profile_sender_entry.delete(0, "end")
        self.profile_sender_entry.insert(0, profile["sender_email"])
        self.profile_password_entry.delete(0, "end")
        self.profile_password_entry.insert(0, profile["app_password"])
        if hasattr(self, "profile_display_name_entry"):
            self.profile_display_name_entry.delete(0, "end")
            self.profile_display_name_entry.insert(0, profile.get("display_name", ""))
        if hasattr(self, "profile_signature_box"):
            self.profile_signature_box.delete("1.0", "end")
            self.profile_signature_box.insert("1.0", profile.get("signature", ""))
        if hasattr(self, "profile_identity_name"):
            self.profile_identity_name.configure(text=profile["profile_name"])
        if hasattr(self, "profile_identity_meta"):
            tag = "Default profile" if profile.get("is_default") else "Saved sender profile"
            signature = profile.get("signature", "").strip() or "No signature saved yet."
            self.profile_identity_meta.configure(text=f"{tag}\n{profile['sender_email']}\n{signature}")

    def save_sender_profile(self, set_default: bool) -> None:
        try:
            profile_data = validate_sender_profile(
                self.profile_name_entry.get(),
                self.profile_sender_entry.get(),
                self.profile_password_entry.get(),
            )
            profile_id = self.database.create_or_update_sender_profile(
                owner_user_id=self._user_id(),
                profile_name=profile_data["profile_name"],
                sender_email=profile_data["sender_email"],
                app_password=profile_data["app_password"],
                display_name=self.profile_display_name_entry.get().strip() if hasattr(self, "profile_display_name_entry") else "",
                signature=self.profile_signature_box.get("1.0", "end").strip() if hasattr(self, "profile_signature_box") else "",
                is_default=set_default,
            )
            if set_default:
                self.database.set_default_sender_profile(profile_id, self._user_id())
            self.logger.info(f"Sender profile '{profile_data['profile_name']}' saved.", owner_user_id=self._user_id())
            self.refresh_sender_profiles()
            self.show_toast("Sender profile saved.", "success")
        except Exception as exc:  # noqa: BLE001
            self.show_toast(str(exc), "error", 3200)
            messagebox.showerror(APP_NAME, str(exc))

    def delete_selected_profile(self) -> None:
        profile = self._get_profile_by_label(self.saved_profile_menu.get()) if hasattr(self, "saved_profile_menu") else None
        if not profile:
            messagebox.showwarning(APP_NAME, "Select a sender profile first.")
            return
        if not messagebox.askyesno(APP_NAME, f"Delete sender profile '{profile['profile_name']}'?"):
            return
        self.database.delete_sender_profile(profile["id"])
        self.logger.info(f"Sender profile '{profile['profile_name']}' deleted.")
        self.profile_name_entry.delete(0, "end")
        self.profile_sender_entry.delete(0, "end")
        self.profile_password_entry.delete(0, "end")
        if hasattr(self, "profile_display_name_entry"):
            self.profile_display_name_entry.delete(0, "end")
        if hasattr(self, "profile_signature_box"):
            self.profile_signature_box.delete("1.0", "end")
        self.refresh_sender_profiles()

    def save_recipient_group(self) -> None:
        try:
            group_data = validate_recipient_group(
                self.group_name_entry.get(),
                self.group_recipients_box.get("1.0", "end"),
                self.group_notes_box.get("1.0", "end").strip(),
            )
            self.database.create_or_update_recipient_group(
                self._user_id(),
                group_name=group_data["group_name"],
                recipients=group_data["recipients"],
                notes=group_data["notes"],
            )
            self.logger.info(f"Recipient group '{group_data['group_name']}' saved.", owner_user_id=self._user_id())
            self.refresh_recipient_groups()
            self.show_toast("Recipient group saved.", "success")
        except Exception as exc:  # noqa: BLE001
            self.show_toast(str(exc), "error", 3200)
            messagebox.showerror(APP_NAME, str(exc))

    def _on_saved_group_selected(self, group_name: str) -> None:
        group = self._get_group_by_name(group_name)
        if not group:
            return
        self.group_name_entry.delete(0, "end")
        self.group_name_entry.insert(0, group["group_name"])
        self.group_recipients_box.delete("1.0", "end")
        self.group_recipients_box.insert("1.0", "\n".join(group["recipients"]))
        self.group_notes_box.delete("1.0", "end")
        self.group_notes_box.insert("1.0", group.get("notes", ""))

    def apply_selected_group(self) -> None:
        group = self._get_group_by_name(self.email_group_menu.get()) if hasattr(self, "email_group_menu") else None
        if not group:
            messagebox.showwarning(APP_NAME, "Select a recipient group first.")
            return
        self.email_recipient_entry.delete(0, "end")
        self.email_recipient_entry.insert(0, ", ".join(group["recipients"]))

    def delete_selected_group(self) -> None:
        group = self._get_group_by_name(self.saved_group_menu.get()) if hasattr(self, "saved_group_menu") else None
        if not group:
            messagebox.showwarning(APP_NAME, "Select a recipient group first.")
            return
        if not messagebox.askyesno(APP_NAME, f"Delete recipient group '{group['group_name']}'?"):
            return
        self.database.delete_recipient_group(group["id"])
        self.logger.info(f"Recipient group '{group['group_name']}' deleted.")
        self.group_name_entry.delete(0, "end")
        self.group_recipients_box.delete("1.0", "end")
        self.group_notes_box.delete("1.0", "end")
        self.refresh_recipient_groups()

    def save_email_template(self) -> None:
        try:
            template_data = validate_email_template(
                self.template_name_entry.get(),
                self.template_subject_entry.get(),
                self.template_body_box.get("1.0", "end").strip(),
                self.template_tone_menu.get(),
                self.template_notes_entry.get(),
            )
            self.database.create_or_update_email_template(self._user_id(), **template_data)
            self.logger.info(f"Email template '{template_data['template_name']}' saved.", owner_user_id=self._user_id())
            self.refresh_email_templates()
            self.show_toast("Email template saved.", "success")
        except Exception as exc:  # noqa: BLE001
            self.show_toast(str(exc), "error", 3200)
            messagebox.showerror(APP_NAME, str(exc))

    def _on_saved_template_selected(self, template_name: str) -> None:
        template = self._get_template_by_name(template_name)
        if not template:
            return
        self.template_name_entry.delete(0, "end")
        self.template_name_entry.insert(0, template["template_name"])
        self.template_tone_menu.set(template.get("tone", "Professional"))
        self.template_notes_entry.delete(0, "end")
        self.template_notes_entry.insert(0, template.get("notes", ""))
        self.template_subject_entry.delete(0, "end")
        self.template_subject_entry.insert(0, template["subject_template"])
        self.template_body_box.delete("1.0", "end")
        self.template_body_box.insert("1.0", template["body_template"])

    def apply_selected_template(self) -> None:
        template = self._get_template_by_name(self.email_template_apply_menu.get()) if hasattr(self, "email_template_apply_menu") else None
        if not template:
            messagebox.showwarning(APP_NAME, "Select an email template first.")
            return
        profile = self._get_profile_by_label(self.email_profile_menu.get()) if hasattr(self, "email_profile_menu") else self.database.get_default_sender_profile(self._user_id())
        signature = self._get_signature_block(profile)
        subject, body = render_email_from_template(template["subject_template"], template["body_template"], {}, signature)
        self.email_subject_only_entry.delete(0, "end")
        self.email_subject_only_entry.insert(0, subject)
        self.email_message_only_box.delete("1.0", "end")
        self.email_message_only_box.insert("1.0", body)

    def delete_selected_template(self) -> None:
        template = self._get_template_by_name(self.saved_template_menu.get()) if hasattr(self, "saved_template_menu") else None
        if not template:
            messagebox.showwarning(APP_NAME, "Select an email template first.")
            return
        if not messagebox.askyesno(APP_NAME, f"Delete email template '{template['template_name']}'?"):
            return
        self.database.delete_email_template(template["id"])
        self.logger.info(f"Email template '{template['template_name']}' deleted.")
        self.template_name_entry.delete(0, "end")
        self.template_notes_entry.delete(0, "end")
        self.template_subject_entry.delete(0, "end")
        self.template_body_box.delete("1.0", "end")
        self.refresh_email_templates()

    def export_email_workspace(self) -> None:
        export_path = filedialog.asksaveasfilename(
            title="Export Email Workspace",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
        )
        if not export_path:
            return
        self.database.export_email_workspace(Path(export_path), self._user_id())
        self.show_toast("Workspace exported.", "success")

    def import_email_workspace(self) -> None:
        import_path = filedialog.askopenfilename(
            title="Import Email Workspace",
            filetypes=[("JSON files", "*.json")],
        )
        if not import_path:
            return
        self.database.import_email_workspace(Path(import_path), self._user_id())
        self.refresh_sender_profiles()
        self.refresh_recipient_groups()
        self.refresh_email_templates()
        self.show_toast("Workspace imported.", "success")

    def _get_signature_block(self, profile: dict | None) -> str:
        if not profile:
            return ""
        return build_signature_block(profile.get("display_name", ""), profile.get("signature", ""))

    def create_general_task(self) -> None:
        try:
            run_time = parse_schedule_datetime(self.task_date_entry.get(), self.task_time_entry.get())
            action_type = self.task_type_menu.get()
            repeat_type = self.task_repeat_menu.get()
            task_name = self.task_name_entry.get().strip()
            if not task_name:
                raise ValueError("Task Name is required.")

            if action_type == "Reminder":
                payload = validate_reminder_payload(self.reminder_message_box.get("1.0", "end").strip())
            else:
                sender_email, app_password = self._resolve_sender_credentials(
                    self.sender_email_entry,
                    self.sender_password_entry,
                    getattr(self, "general_profile_menu", None),
                )
                payload = validate_email_payload(
                    sender_email,
                    app_password,
                    self.recipient_entry.get(),
                    self.email_subject_entry.get(),
                    self.email_message_box.get("1.0", "end").strip(),
                )

            task_id = self.database.create_task(
                owner_user_id=self._user_id(),
                name=task_name,
                action_type=action_type,
                run_at=run_time.strftime(DATETIME_FORMAT),
                repeat_type=repeat_type,
                payload=payload,
            )
            self.scheduler.refresh_task(task_id)
            self.logger.info(f"Task '{task_name}' created from the main automation form.", task_id, self._user_id())
            self._reset_general_form()
            self.refresh_all()
            self.show_toast("Automation task created.", "success")
        except Exception as exc:  # noqa: BLE001
            self.show_toast(str(exc), "error", 3200)
            messagebox.showerror(APP_NAME, str(exc))

    def create_email_task(self) -> None:
        try:
            run_time = parse_schedule_datetime(self.email_date_entry.get(), self.email_time_entry.get())
            task_name = self.email_task_name_entry.get().strip()
            if not task_name:
                raise ValueError("Task Name is required.")

            payload = self._email_form_payload()

            task_id = self.database.create_task(
                owner_user_id=self._user_id(),
                name=task_name,
                action_type="Email",
                run_at=run_time.strftime(DATETIME_FORMAT),
                repeat_type=self.email_repeat_menu.get(),
                payload=payload,
            )
            self.scheduler.refresh_task(task_id)
            self.logger.info(f"Email automation '{task_name}' created.", task_id, self._user_id())
            self._reset_email_form()
            self.refresh_all()
            self.show_toast("Email automation created.", "success")
        except Exception as exc:  # noqa: BLE001
            self.show_toast(str(exc), "error", 3200)
            messagebox.showerror(APP_NAME, str(exc))

    def select_attachments(self) -> None:
        paths = filedialog.askopenfilenames(title="Select Attachments")
        if not paths:
            return
        self.selected_attachment_paths = normalize_attachment_list(list(paths))
        self._update_attachment_label()

    def clear_attachments(self) -> None:
        self.selected_attachment_paths = []
        self._update_attachment_label()

    def _update_attachment_label(self) -> None:
        if not hasattr(self, "attachment_label"):
            return
        if not self.selected_attachment_paths:
            self.attachment_label.configure(text="No attachments selected.")
            return
        names = ", ".join(Path(path).name for path in self.selected_attachment_paths)
        self.attachment_label.configure(text=f"Attachments: {names}")

    def preview_email_content(self) -> None:
        try:
            payload = self._email_form_payload()
            profile = self._get_profile_by_label(self.email_profile_menu.get()) if hasattr(self, "email_profile_menu") else self.database.get_default_sender_profile(self._user_id())
            placeholders = find_placeholders(payload["subject"]) + find_placeholders(payload["message"])
            preview_text = (
                f"From: {payload['sender_email']}\n"
                f"To: {payload['recipient']}\n"
                f"Subject: {payload['subject']}\n"
                f"Profile: {profile['profile_name'] if profile else 'Manual entry'}\n"
                f"Attachments: {', '.join(Path(path).name for path in payload.get('attachments', [])) or 'None'}\n"
                f"Unfilled placeholders: {', '.join(sorted(set(placeholders))) or 'None'}\n\n"
                f"{payload['message']}"
            )
            self.preview_box.delete("1.0", "end")
            self.preview_box.insert("1.0", preview_text)
            self.preview_info_label.configure(text="Preview updated from the current email form.")
        except Exception as exc:  # noqa: BLE001
            self.show_toast(str(exc), "error", 3200)
            messagebox.showerror(APP_NAME, str(exc))

    def _email_form_payload(self) -> dict:
        sender_email, app_password = self._resolve_sender_credentials(
            self.email_sender_entry,
            self.email_password_entry,
            getattr(self, "email_profile_menu", None),
        )
        payload = validate_email_payload(
            sender_email,
            app_password,
            ", ".join(parse_recipient_list(self.email_recipient_entry.get())),
            self.email_subject_only_entry.get(),
            self.email_message_only_box.get("1.0", "end").strip(),
        )
        payload["attachments"] = list(self.selected_attachment_paths)
        payload["template_name"] = self.email_template_apply_menu.get() if hasattr(self, "email_template_apply_menu") else ""
        selected_group = self.email_group_menu.get() if hasattr(self, "email_group_menu") else ""
        payload["recipient_group"] = "" if selected_group == "No saved groups" else selected_group
        selected_profile = self._get_profile_by_label(self.email_profile_menu.get()) if hasattr(self, "email_profile_menu") else None
        if selected_profile:
            payload["profile_name"] = selected_profile["profile_name"]
            payload["signature"] = self._get_signature_block(selected_profile)
        return payload

    def _resolve_sender_credentials(self, email_entry, password_entry, menu) -> tuple[str, str]:
        sender_email = email_entry.get().strip()
        app_password = password_entry.get().strip()
        if sender_email and app_password:
            return sender_email, app_password

        profile = self._get_profile_by_label(menu.get()) if menu else None
        if profile is None:
            profile = self.database.get_default_sender_profile(self._user_id())
        if profile:
            return profile["sender_email"], profile["app_password"]
        return sender_email, app_password

    def send_test_email(self) -> None:
        try:
            payload = self._email_form_payload()
            send_email(
                sender_email=payload["sender_email"],
                app_password=payload["app_password"],
                recipient=payload["recipient"],
                subject=payload["subject"],
                message=payload["message"],
                attachments=payload.get("attachments", []),
            )
            self.logger.info(f"Test email sent to {payload['recipient']}.", owner_user_id=self._user_id())
            self.refresh_logs()
            self.show_toast(f"Test email sent to {payload['recipient']}.", "success")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"Test email failed: {exc}", owner_user_id=self._user_id())
            self.refresh_logs()
            self.show_toast(str(exc), "error", 3200)
            messagebox.showerror(APP_NAME, str(exc))

    def refresh_all(self) -> None:
        if not self.current_user:
            return
        self.refresh_dashboard()
        self.refresh_tasks()
        self.refresh_logs()
        self._check_background_completion()

    def refresh_dashboard(self) -> None:
        stats = self.database.get_dashboard_stats(self._user_id())
        for key, label in self.stats_labels.items():
            label.configure(text=str(stats.get(key, 0)))

        scheduler_running = self.scheduler.scheduler.running
        self.sidebar_status.configure(text="Running" if scheduler_running else "Stopped", text_color=self.color("success") if scheduler_running else self.color("danger"))
        self.header_scheduler_chip.configure(
            text=" Scheduler Live " if scheduler_running else " Scheduler Offline ",
            text_color=self.color("success") if scheduler_running else self.color("danger"),
        )
        self.health_label.configure(
            text=" Scheduler Running " if scheduler_running else " Scheduler Stopped ",
            text_color=self.color("success") if scheduler_running else self.color("danger"),
        )
        self.storage_label.configure(text=f"Database and logs are stored in:\n{self.database.db_path.parent}")

        tasks = self.database.fetch_tasks(self._user_id())
        logs = self.database.fetch_logs(self._user_id(), limit=6)
        profiles = self.database.fetch_sender_profiles(self._user_id())
        if hasattr(self, "dashboard_welcome"):
            if tasks or profiles:
                self.dashboard_welcome.pack_forget()
            elif not self.dashboard_welcome.winfo_manager():
                self.dashboard_welcome.pack(fill="x", pady=(0, 16), before=self.dashboard_welcome.master.winfo_children()[1] if len(self.dashboard_welcome.master.winfo_children()) > 1 else None)
        self._clear_tree(self.dashboard_tasks)
        for task in tasks[:20]:
            self._insert_tree_row(
                self.dashboard_tasks,
                (
                    task["id"],
                    task["name"],
                    task["action_type"],
                    task["run_at"],
                    task["repeat_type"],
                    self._status_text(task["status"]),
                ),
            )
        self._set_next_task_summary(tasks)
        self._render_activity_feed(logs)
        self._render_upcoming_timeline(tasks)
        self._render_recent_actions(logs)

    def _set_next_task_summary(self, tasks: list[dict]) -> None:
        active = [task for task in tasks if task["status"] == "active"]
        if not active:
            self.next_task_name.configure(text="No upcoming task")
            self.next_task_meta.configure(text="Create an automation to see the next scheduled run here.")
            return
        task = active[0]
        self.next_task_name.configure(text=task["name"])
        self.next_task_meta.configure(text=f"{task['action_type']} | {task['run_at']} | {task['repeat_type']} repeat")

    def _render_activity_feed(self, logs: list[dict]) -> None:
        if not hasattr(self, "activity_feed"):
            return
        for child in self.activity_feed.winfo_children():
            child.destroy()
        if not logs:
            ctk.CTkLabel(self.activity_feed, text="No recent activity yet.", text_color=self.color("muted"), font=ctk.CTkFont(size=12)).pack(anchor="w")
            return
        for log in logs:
            row = ctk.CTkFrame(self.activity_feed, fg_color=self.color("bg"), corner_radius=14)
            row.pack(fill="x", pady=5)
            accent = self.color("danger") if log["level"] == "ERROR" else self.color("success")
            ctk.CTkLabel(row, text=log["level"], width=58, fg_color=accent, text_color=self.color("bg"), corner_radius=999, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=10, pady=10)
            copy = ctk.CTkFrame(row, fg_color="transparent")
            copy.pack(fill="both", expand=True, padx=(0, 10), pady=8)
            ctk.CTkLabel(copy, text=log.get("task_name") or "System", font=ctk.CTkFont(size=12, weight="bold"), text_color=self.color("text"), anchor="w").pack(anchor="w")
            ctk.CTkLabel(copy, text=log["message"], font=ctk.CTkFont(size=11), text_color=self.color("muted"), wraplength=250, justify="left").pack(anchor="w", pady=(2, 0))

    def _render_upcoming_timeline(self, tasks: list[dict]) -> None:
        if not hasattr(self, "timeline_frame"):
            return
        for child in self.timeline_frame.winfo_children():
            child.destroy()
        active = [task for task in tasks if task["status"] == "active"][:5]
        if not active:
            ctk.CTkLabel(self.timeline_frame, text="No active tasks to place on the timeline yet.", text_color=self.color("muted"), font=ctk.CTkFont(size=12)).pack(anchor="w")
            return
        lane = ctk.CTkFrame(self.timeline_frame, fg_color="transparent")
        lane.pack(fill="x")
        for column in range(len(active)):
            lane.grid_columnconfigure(column, weight=1)
        for index, task in enumerate(active):
            item = ctk.CTkFrame(lane, fg_color=self.color("section"), corner_radius=16, border_width=1, border_color=self.color("border"))
            item.grid(row=0, column=index, padx=6, sticky="ew")
            ctk.CTkLabel(item, text=task["run_at"][5:10], font=ctk.CTkFont(size=11, weight="bold"), text_color=self.color("accent")).pack(anchor="w", padx=12, pady=(12, 4))
            ctk.CTkLabel(item, text=task["name"], font=ctk.CTkFont(size=13, weight="bold"), text_color=self.color("text"), wraplength=150, justify="left").pack(anchor="w", padx=12)
            health_text, health_color = self._task_health(task)
            ctk.CTkLabel(item, text=f"{task['action_type']} • {task['repeat_type']}", font=ctk.CTkFont(size=11), text_color=self.color("muted")).pack(anchor="w", padx=12, pady=(4, 6))
            ctk.CTkLabel(item, text=f" {health_text} ", font=ctk.CTkFont(size=10, weight="bold"), text_color=health_color, fg_color=self.color("bg"), corner_radius=999, padx=8, pady=4).pack(anchor="w", padx=12, pady=(0, 12))

    def _render_recent_actions(self, logs: list[dict]) -> None:
        if not hasattr(self, "recent_actions_frame"):
            return
        for child in self.recent_actions_frame.winfo_children():
            child.destroy()
        if not logs:
            ctk.CTkLabel(self.recent_actions_frame, text="No recent actions yet.", text_color=self.color("muted"), font=ctk.CTkFont(size=12)).pack(anchor="w")
            return
        lane = ctk.CTkFrame(self.recent_actions_frame, fg_color="transparent")
        lane.pack(fill="x")
        for index, log in enumerate(logs[:4]):
            lane.grid_columnconfigure(index, weight=1)
            card = ctk.CTkFrame(lane, fg_color=self.color("section"), corner_radius=16, border_width=1, border_color=self.color("border"))
            card.grid(row=0, column=index, padx=6, sticky="ew")
            accent = self.color("danger") if log["level"] == "ERROR" else self.color("success")
            ctk.CTkLabel(card, text=log["level"], font=ctk.CTkFont(size=10, weight="bold"), text_color=accent).pack(anchor="w", padx=12, pady=(12, 4))
            ctk.CTkLabel(card, text=log.get("task_name") or "System", font=ctk.CTkFont(size=12, weight="bold"), text_color=self.color("text"), wraplength=180, justify="left").pack(anchor="w", padx=12)
            ctk.CTkLabel(card, text=log["executed_at"][11:16], font=ctk.CTkFont(size=11), text_color=self.color("muted")).pack(anchor="w", padx=12, pady=(4, 12))

    def refresh_tasks(self) -> None:
        tasks = self.database.fetch_tasks(self._user_id())
        self._clear_tree(self.tasks_tree)
        for task in tasks:
            self._insert_tree_row(
                self.tasks_tree,
                (
                    task["id"],
                    task["name"],
                    task["action_type"],
                    task["run_at"],
                    task["repeat_type"],
                    self._status_text(task["status"]),
                    task["next_run"] or "-",
                    task["last_run"] or "-",
                ),
            )
        selected = self.database.fetch_task(self.selected_task_id, self._user_id()) if self.selected_task_id else None
        self._render_task_detail(selected)

    def refresh_logs(self) -> None:
        logs = self.database.fetch_logs(self._user_id())
        self._clear_tree(self.logs_tree)
        info_count = 0
        error_count = 0
        for log in logs:
            if log["level"] == "ERROR":
                error_count += 1
            else:
                info_count += 1
            task_label = log.get("task_name") or ("System" if not log["task_id"] else f"Task #{log['task_id']}")
            self._insert_tree_row(
                self.logs_tree,
                (
                    log["executed_at"],
                    log["level"],
                    task_label,
                    log["message"],
                ),
            )
        if hasattr(self, "log_info_card"):
            self.log_info_card.configure(text=f" INFO {info_count} ")
        if hasattr(self, "log_error_card"):
            self.log_error_card.configure(text=f" ERROR {error_count} ")
        selected = next((log for log in logs if log["id"] == self.selected_log_id), None) if self.selected_log_id else None
        self._render_log_detail(selected)

    def _status_text(self, status: str) -> str:
        return f"[{status.upper()}]"

    def _insert_tree_row(self, tree: ttk.Treeview, values: tuple) -> None:
        meta = self.tree_meta[tree]
        index = int(meta["insert_index"])
        tree.insert("", "end", values=values, tags=("evenrow" if index % 2 == 0 else "oddrow",))
        meta["insert_index"] = index + 1
        self._update_tree_empty_state(tree)

    def _update_tree_empty_state(self, tree: ttk.Treeview) -> None:
        empty_label = self.tree_meta[tree]["empty_label"]
        if tree.get_children():
            empty_label.place_forget()
        else:
            empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def toggle_selected_task(self) -> None:
        task = self._get_selected_task()
        if not task:
            messagebox.showwarning(APP_NAME, "Select a task first.")
            return

        new_status = "inactive" if task["status"] == "active" else "active"
        self.database.update_task_status(task["id"], new_status)
        self.scheduler.refresh_task(task["id"])
        self.logger.info(f"Task '{task['name']}' status changed to {new_status}.", task["id"], self._user_id())
        self.refresh_all()

    def delete_selected_task(self) -> None:
        task = self._get_selected_task()
        if not task:
            messagebox.showwarning(APP_NAME, "Select a task first.")
            return

        if not messagebox.askyesno(APP_NAME, f"Delete '{task['name']}'?"):
            return

        self.scheduler.remove_task(task["id"])
        self.database.delete_task(task["id"])
        self.logger.info(f"Task '{task['name']}' deleted.", task["id"], self._user_id())
        self.selected_task_id = None
        self.refresh_all()
        self.show_toast("Task deleted.", "success")

    def _capture_selected_task(self, _event=None) -> None:
        item = self.tasks_tree.focus()
        if not item:
            self.selected_task_id = None
            self._render_task_detail(None)
            return
        values = self.tasks_tree.item(item, "values")
        self.selected_task_id = int(values[0])
        self._render_task_detail(self.database.fetch_task(self.selected_task_id, self._user_id()))

    def _capture_selected_log(self, _event=None) -> None:
        item = self.logs_tree.focus()
        if not item:
            self.selected_log_id = None
            self._render_log_detail(None)
            return
        values = self.logs_tree.item(item, "values")
        time_value = values[0]
        level_value = values[1]
        message_value = values[3]
        for log in self.database.fetch_logs(self._user_id()):
            if log["executed_at"] == time_value and log["level"] == level_value and log["message"] == message_value:
                self.selected_log_id = log["id"]
                self._render_log_detail(log)
                return

    def _render_task_detail(self, task: dict | None) -> None:
        for widget in self.task_detail_rows.winfo_children():
            widget.destroy()
        self.task_payload_box.configure(state="normal")
        self.task_payload_box.delete("1.0", "end")

        if not task:
            self.task_detail_status.configure(text=" No task selected ", text_color=self.color("muted"))
            self.task_detail_title.configure(text="Select a task from the table")
            self.task_detail_meta.configure(text="The right panel will show type, timing, and payload details here.")
            self.task_payload_box.insert("1.0", "Payload details will appear here.")
            self.task_payload_box.configure(state="disabled")
            return

        accent = self.color("success") if task["status"] == "active" else self.color("warning")
        self.task_detail_status.configure(text=f" {task['status'].upper()} ", text_color=accent)
        self.task_detail_title.configure(text=task["name"])
        health_text, health_color = self._task_health(task)
        self.task_detail_meta.configure(text=f"{task['action_type']} automation scheduled for {task['run_at']} • {health_text}")

        detail_rows = [
            ("Repeat", task["repeat_type"]),
            ("Next Run", task["next_run"] or "-"),
            ("Last Run", task["last_run"] or "-"),
            ("Status", task["status"]),
            ("Health", health_text),
        ]
        for label, value in detail_rows:
            self._detail_row(self.task_detail_rows, label, value).pack(fill="x", pady=4)

        payload_lines = []
        for key, value in task["payload"].items():
            pretty_key = key.replace("_", " ").title()
            payload_lines.append(f"{pretty_key}: {value}")
        self.task_payload_box.insert("1.0", "\n".join(payload_lines) if payload_lines else "No payload details.")
        self.task_payload_box.configure(state="disabled")

    def _render_log_detail(self, log: dict | None) -> None:
        self.log_message_box.configure(state="normal")
        self.log_message_box.delete("1.0", "end")
        if not log:
            self.log_level_chip.configure(text=" No log selected ", text_color=self.color("muted"))
            self.log_task_label.configure(text="Select a row from the log table")
            self.log_time_label.configure(text="")
            self.log_message_box.insert("1.0", "The selected log message will appear here.")
            self.log_message_box.configure(state="disabled")
            return

        color = self.color("danger") if log["level"] == "ERROR" else self.color("success")
        task_label = log.get("task_name") or ("System" if not log["task_id"] else f"Task #{log['task_id']}")
        self.log_level_chip.configure(text=f" {log['level']} ", text_color=color)
        self.log_task_label.configure(text=task_label)
        self.log_time_label.configure(text=f"Executed at {log['executed_at']}")
        self.log_message_box.insert("1.0", log["message"])
        self.log_message_box.configure(state="disabled")

    def _detail_row(self, parent, label: str, value: str) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color=self.color("bg"), corner_radius=12)
        row.grid_columnconfigure(0, weight=0)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=12, weight="bold"), text_color=self.color("muted")).grid(row=0, column=0, padx=(12, 10), pady=10, sticky="w")
        ctk.CTkLabel(row, text=value, font=ctk.CTkFont(size=12), text_color=self.color("text"), wraplength=170, justify="right").grid(row=0, column=1, padx=(0, 12), pady=10, sticky="e")
        return row

    def _get_selected_task(self) -> dict | None:
        if not self.selected_task_id:
            return None
        return self.database.fetch_task(self.selected_task_id, self._user_id())

    def _reset_general_form(self) -> None:
        for entry in [
            self.task_name_entry,
            self.sender_email_entry,
            self.sender_password_entry,
            self.recipient_entry,
            self.email_subject_entry,
        ]:
            entry.delete(0, "end")
        for box in [self.reminder_message_box, self.email_message_box]:
            box.delete("1.0", "end")
        self.task_date_entry.delete(0, "end")
        self.task_date_entry.insert(0, datetime.now().strftime(DATE_FORMAT))
        self.task_time_entry.delete(0, "end")
        self.task_time_entry.insert(0, (datetime.now() + timedelta(minutes=10)).strftime(TIME_FORMAT))
        self.task_type_menu.set("Reminder")
        self.task_repeat_menu.set("None")
        self._toggle_general_action_fields("Reminder")
        if self.sender_profiles:
            self._apply_selected_profile(self.general_profile_menu, self.sender_email_entry, self.sender_password_entry, allow_default=True)

    def _reset_email_form(self) -> None:
        for entry in [
            self.email_task_name_entry,
            self.email_sender_entry,
            self.email_password_entry,
            self.email_recipient_entry,
            self.email_subject_only_entry,
        ]:
            entry.delete(0, "end")
        self.email_message_only_box.delete("1.0", "end")
        self.email_date_entry.delete(0, "end")
        self.email_date_entry.insert(0, datetime.now().strftime(DATE_FORMAT))
        self.email_time_entry.delete(0, "end")
        self.email_time_entry.insert(0, (datetime.now() + timedelta(minutes=15)).strftime(TIME_FORMAT))
        self.email_repeat_menu.set("None")
        self.selected_attachment_paths = []
        self._update_attachment_label()
        if hasattr(self, "preview_box"):
            self.preview_box.delete("1.0", "end")
            self.preview_box.insert("1.0", "Preview the final rendered email before you send or schedule it.")
        if hasattr(self, "preview_info_label"):
            self.preview_info_label.configure(text="Preview the final rendered email before you send or schedule it.")
        if self.sender_profiles:
            self._apply_selected_profile(self.email_profile_menu, self.email_sender_entry, self.email_password_entry, allow_default=True)

    def _clear_tree(self, tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)
        self.tree_meta[tree]["insert_index"] = 0
        self._update_tree_empty_state(tree)

    def _auto_refresh(self) -> None:
        if self._quitting or not self.winfo_exists():
            return
        if not self.current_user:
            self.after(5000, self._auto_refresh)
            return
        try:
            self.refresh_all()
        except tk.TclError:
            return
        self.after(5000, self._auto_refresh)

    def _get_active_tasks(self) -> list[dict]:
        if not self.current_user:
            return []
        return self.database.fetch_tasks(self._user_id(), status="active")

    def logout_user(self) -> None:
        if self.current_user:
            self.logger.info(f"User '{self.current_user['username']}' logged out.", owner_user_id=self._user_id())
        self.scheduler.clear_all_jobs()
        self.current_user = None
        self.current_view = "Dashboard"
        self._rebuild_ui()

    def _check_background_completion(self) -> None:
        if self._close_after_tasks_complete and not self._get_active_tasks():
            self.logger.info("All active scheduled tasks are complete. TaskFlow is closing automatically.")
            self._exit_application()

    def _tray_supported(self) -> bool:
        return pystray is not None and Image is not None and ImageDraw is not None

    def _create_tray_image(self):
        image = Image.new("RGBA", (64, 64), self.color("card"))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=14, fill=self.color("accent"))
        draw.rectangle((18, 16, 46, 22), fill=self.color("card"))
        draw.rectangle((18, 28, 40, 34), fill=self.color("card"))
        draw.rectangle((18, 40, 34, 46), fill=self.color("card"))
        return image

    def _on_tray_open(self, _icon=None, _item=None) -> None:
        self.after(0, self._restore_from_tray)

    def _on_tray_exit(self, _icon=None, _item=None) -> None:
        self.after(0, self._exit_application)

    def _start_tray_icon(self) -> bool:
        if not self._tray_supported():
            return False
        if self._tray_icon is not None:
            return True

        menu = pystray.Menu(
            pystray.MenuItem("Open TaskFlow", self._on_tray_open),
            pystray.MenuItem("Exit TaskFlow", self._on_tray_exit),
        )
        self._tray_icon = pystray.Icon(APP_SLUG.lower(), self._create_tray_image(), APP_NAME, menu)
        self._tray_thread = Thread(target=self._tray_icon.run, daemon=True)
        self._tray_thread.start()
        return True

    def _stop_tray_icon(self) -> None:
        icon = self._tray_icon
        self._tray_icon = None
        self._tray_thread = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass

    def _minimize_to_tray(self) -> None:
        if self._tray_hidden:
            return
        if not self._start_tray_icon():
            messagebox.showwarning(APP_NAME, "System tray support is not installed yet. Run setup again to enable background mode.")
            self._exit_application()
            return

        if not self._tray_notice_shown:
            self._tray_notice_shown = True
            active_tasks = self._get_active_tasks()
            repeating_tasks = [task for task in active_tasks if task["repeat_type"] != "None"]
            if self._close_after_tasks_complete and repeating_tasks:
                message = "TaskFlow is running in the system tray. Repeating tasks stay active continuously, so the app will keep running until you reopen it and deactivate them or choose Exit from the tray."
            elif self._close_after_tasks_complete:
                message = "TaskFlow is running in the system tray and will close automatically after all active scheduled tasks are completed."
            else:
                message = "TaskFlow will keep running in the system tray. Use the tray icon to reopen or fully exit the app."
            messagebox.showinfo(APP_NAME, message)

        self.withdraw()
        self._tray_hidden = True
        if self._close_after_tasks_complete:
            self.logger.info("TaskFlow minimized to the system tray and will stay active until all scheduled tasks complete.")
        else:
            self.logger.info("TaskFlow minimized to the system tray. Scheduler remains active.")

    def _restore_from_tray(self) -> None:
        if not self._tray_hidden:
            return
        self._tray_hidden = False
        self._close_after_tasks_complete = False
        self._stop_tray_icon()
        self.deiconify()
        self.after(50, self.lift)
        self.after(100, self.focus_force)
        self.logger.info("TaskFlow restored from the system tray.")

    def _exit_application(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._close_after_tasks_complete = False
        if self.command_palette_window is not None and self.command_palette_window.winfo_exists():
            try:
                self.command_palette_window.destroy()
            except tk.TclError:
                pass
            self.command_palette_window = None
            self.command_palette_entry = None
            self.command_palette_results = None
        self._stop_tray_icon()
        self.scheduler.shutdown()
        self.destroy()

    def on_close(self) -> None:
        active_tasks = self._get_active_tasks()
        if not active_tasks:
            self._exit_application()
            return
        self._close_after_tasks_complete = True
        self._minimize_to_tray()
