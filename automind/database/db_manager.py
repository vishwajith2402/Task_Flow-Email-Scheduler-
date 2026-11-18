import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from automind.utils.paths import get_base_data_dir, get_runtime_root


class DatabaseManager:
    def __init__(self) -> None:
        self.data_dir = get_base_data_dir()
        self.db_path = self.data_dir / "automind.db"
        self.legacy_db_paths = self._legacy_database_paths()
        self._lock = threading.Lock()
        self.initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER,
                    name TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    run_at TEXT NOT NULL,
                    repeat_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    payload TEXT NOT NULL,
                    last_run TEXT,
                    next_run TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER,
                    task_id INTEGER,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    executed_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id),
                    FOREIGN KEY(owner_user_id) REFERENCES users(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sender_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER,
                    profile_name TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    app_password TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    signature TEXT NOT NULL DEFAULT '',
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS recipient_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER,
                    group_name TEXT NOT NULL,
                    recipients TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS email_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER,
                    template_name TEXT NOT NULL,
                    subject_template TEXT NOT NULL,
                    body_template TEXT NOT NULL,
                    tone TEXT NOT NULL DEFAULT 'Professional',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id)
                )
                """
            )
            self._ensure_column(connection, "tasks", "owner_user_id", "INTEGER")
            self._ensure_column(connection, "logs", "owner_user_id", "INTEGER")
            self._ensure_column(connection, "sender_profiles", "owner_user_id", "INTEGER")
            self._ensure_column(connection, "recipient_groups", "owner_user_id", "INTEGER")
            self._ensure_column(connection, "email_templates", "owner_user_id", "INTEGER")
            self._ensure_column(connection, "sender_profiles", "display_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "sender_profiles", "signature", "TEXT NOT NULL DEFAULT ''")

    def _legacy_database_paths(self) -> list[Path]:
        candidates = [
            get_runtime_root() / "data" / "automind.db",
            Path.cwd() / "data" / "automind.db",
        ]
        unique_paths: list[Path] = []
        current = self.db_path.resolve()
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved == current or resolved in unique_paths:
                continue
            unique_paths.append(resolved)
        return unique_paths

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        existing_columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
        if column_name not in existing_columns:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    @staticmethod
    def _hash_password(password: str) -> str:
        return sha256(password.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalized_username(username: str) -> str:
        return username.strip()

    def _password_candidates(self, password: str) -> list[str]:
        raw = password if password is not None else ""
        stripped = raw.strip()
        candidates: list[str] = []
        for value in (raw, stripped):
            for candidate in (value, self._hash_password(value)):
                if candidate not in candidates:
                    candidates.append(candidate)
        return candidates

    def _password_matches(self, stored_value: str, password: str) -> bool:
        return (stored_value or "").strip() in self._password_candidates(password)

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return bool(row)

    def _upgrade_password_hash(self, user_id: int, password: str) -> None:
        canonical_hash = self._hash_password(password.strip())
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (canonical_hash, datetime.now().isoformat(timespec="seconds"), user_id),
            )

    def _migrate_matching_legacy_user(self, username: str, password: str) -> dict[str, Any] | None:
        normalized_username = self._normalized_username(username)
        if not normalized_username:
            return None

        for legacy_path in self.legacy_db_paths:
            if not legacy_path.exists():
                continue
            try:
                connection = sqlite3.connect(legacy_path, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                try:
                    if not self._table_exists(connection, "users"):
                        continue
                    row = connection.execute(
                        "SELECT * FROM users WHERE lower(trim(username)) = lower(?)",
                        (normalized_username,),
                    ).fetchone()
                    if not row or not self._password_matches(str(row["password_hash"]), password):
                        continue

                    legacy_user = dict(row)
                    with self._lock, self._connect() as current_connection:
                        existing = current_connection.execute(
                            "SELECT id FROM users WHERE lower(trim(username)) = lower(?)",
                            (normalized_username,),
                        ).fetchone()
                        if existing:
                            current_connection.execute(
                                """
                                UPDATE users
                                SET password_hash = ?, updated_at = ?
                                WHERE id = ?
                                """,
                                (
                                    str(legacy_user["password_hash"]).strip(),
                                    legacy_user.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
                                    existing["id"],
                                ),
                            )
                        else:
                            current_connection.execute(
                                """
                                INSERT INTO users (username, password_hash, created_at, updated_at)
                                VALUES (?, ?, ?, ?)
                                """,
                                (
                                    legacy_user["username"],
                                    str(legacy_user["password_hash"]).strip(),
                                    legacy_user.get("created_at") or datetime.now().isoformat(timespec="seconds"),
                                    legacy_user.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
                                ),
                            )
                    return self.fetch_user_by_username(normalized_username)
                finally:
                    connection.close()
            except sqlite3.Error:
                continue
        return None

    def create_user(self, username: str, password: str) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        normalized_username = self._normalized_username(username)
        password_hash = self._hash_password(password.strip())
        with self._lock, self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_username, password_hash, now, now),
            )
            return int(cursor.lastrowid)

    def authenticate_user(self, username: str, password: str) -> dict[str, Any] | None:
        normalized_username = self._normalized_username(username)
        if not normalized_username or password is None:
            return None

        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE lower(trim(username)) = lower(?)",
                (normalized_username,),
            ).fetchone()
        if row and self._password_matches(str(row["password_hash"]), password):
            if str(row["password_hash"]).strip() != self._hash_password(password.strip()):
                self._upgrade_password_hash(int(row["id"]), password)
                return self.fetch_user_by_username(normalized_username)
            return dict(row)

        migrated = self._migrate_matching_legacy_user(normalized_username, password)
        if migrated:
            return migrated
        return None

    def fetch_user_by_username(self, username: str) -> dict[str, Any] | None:
        normalized_username = self._normalized_username(username)
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE lower(trim(username)) = lower(?)", (normalized_username,)).fetchone()
        return dict(row) if row else None

    def has_users(self) -> bool:
        with self._lock, self._connect() as connection:
            return bool(connection.execute("SELECT 1 FROM users LIMIT 1").fetchone())

    def create_task(
        self,
        owner_user_id: int,
        name: str,
        action_type: str,
        run_at: str,
        repeat_type: str,
        payload: dict[str, Any],
        status: str = "active",
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO tasks (owner_user_id, name, action_type, run_at, repeat_type, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_user_id, name, action_type, run_at, repeat_type, status, json.dumps(payload), now, now),
            )
            return int(cursor.lastrowid)

    def fetch_tasks(self, owner_user_id: int | None = None, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks"
        params: list[Any] = []
        conditions: list[str] = []
        if owner_user_id is not None:
            conditions.append("owner_user_id = ?")
            params.append(owner_user_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY datetime(run_at) ASC"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def fetch_task(self, task_id: int, owner_user_id: int | None = None) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            if owner_user_id is None:
                row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            else:
                row = connection.execute("SELECT * FROM tasks WHERE id = ? AND owner_user_id = ?", (task_id, owner_user_id)).fetchone()
        return self._row_to_task(row) if row else None

    def update_task_status(self, task_id: int, status: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now().isoformat(timespec="seconds"), task_id),
            )

    def update_task_run_state(self, task_id: int, last_run: str | None, next_run: str | None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET last_run = ?, next_run = ?, updated_at = ?
                WHERE id = ?
                """,
                (last_run, next_run, datetime.now().isoformat(timespec="seconds"), task_id),
            )

    def update_task_schedule(self, task_id: int, next_run: str | None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE tasks SET next_run = ?, updated_at = ? WHERE id = ?",
                (next_run, datetime.now().isoformat(timespec="seconds"), task_id),
            )

    def delete_task(self, task_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM logs WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def create_log(self, task_id: int | None, level: str, message: str, owner_user_id: int | None = None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO logs (owner_user_id, task_id, level, message, executed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (owner_user_id, task_id, level, message, datetime.now().isoformat(timespec="seconds")),
            )

    def create_or_update_sender_profile(
        self,
        owner_user_id: int,
        profile_name: str,
        sender_email: str,
        app_password: str,
        display_name: str = "",
        signature: str = "",
        is_default: bool = False,
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            cursor = connection.cursor()
            if is_default:
                cursor.execute("UPDATE sender_profiles SET is_default = 0 WHERE owner_user_id = ?", (owner_user_id,))
            existing = cursor.execute(
                "SELECT id FROM sender_profiles WHERE sender_email = ? AND owner_user_id = ?",
                (sender_email, owner_user_id),
            ).fetchone()
            if existing:
                cursor.execute(
                    """
                    UPDATE sender_profiles
                    SET profile_name = ?, app_password = ?, display_name = ?, signature = ?, is_default = ?, updated_at = ?
                    WHERE sender_email = ? AND owner_user_id = ?
                    """,
                    (profile_name, app_password, display_name, signature, int(is_default), now, sender_email, owner_user_id),
                )
                return int(existing["id"])

            cursor.execute(
                """
                INSERT INTO sender_profiles (owner_user_id, profile_name, sender_email, app_password, display_name, signature, is_default, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_user_id, profile_name, sender_email, app_password, display_name, signature, int(is_default), now, now),
            )
            return int(cursor.lastrowid)

    def fetch_sender_profiles(self, owner_user_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            if owner_user_id is None:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM sender_profiles
                    ORDER BY is_default DESC, lower(profile_name) ASC, lower(sender_email) ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM sender_profiles
                    WHERE owner_user_id = ?
                    ORDER BY is_default DESC, lower(profile_name) ASC, lower(sender_email) ASC
                    """,
                    (owner_user_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def fetch_sender_profile(self, profile_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM sender_profiles WHERE id = ?", (profile_id,)).fetchone()
        return dict(row) if row else None

    def get_default_sender_profile(self, owner_user_id: int | None = None) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            if owner_user_id is None:
                row = connection.execute(
                    "SELECT * FROM sender_profiles WHERE is_default = 1 ORDER BY id ASC LIMIT 1"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM sender_profiles WHERE owner_user_id = ? AND is_default = 1 ORDER BY id ASC LIMIT 1",
                    (owner_user_id,),
                ).fetchone()
        return dict(row) if row else None

    def set_default_sender_profile(self, profile_id: int, owner_user_id: int | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            if owner_user_id is None:
                connection.execute("UPDATE sender_profiles SET is_default = 0, updated_at = ?", (now,))
                connection.execute(
                    "UPDATE sender_profiles SET is_default = 1, updated_at = ? WHERE id = ?",
                    (now, profile_id),
                )
            else:
                connection.execute("UPDATE sender_profiles SET is_default = 0, updated_at = ? WHERE owner_user_id = ?", (now, owner_user_id))
                connection.execute(
                    "UPDATE sender_profiles SET is_default = 1, updated_at = ? WHERE id = ? AND owner_user_id = ?",
                    (now, profile_id, owner_user_id),
                )

    def delete_sender_profile(self, profile_id: int) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            profile = connection.execute(
                "SELECT is_default FROM sender_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
            connection.execute("DELETE FROM sender_profiles WHERE id = ?", (profile_id,))
            if profile and profile["is_default"]:
                replacement = connection.execute(
                    "SELECT id FROM sender_profiles ORDER BY lower(profile_name) ASC, id ASC LIMIT 1"
                ).fetchone()
                if replacement:
                    connection.execute(
                        "UPDATE sender_profiles SET is_default = 1, updated_at = ? WHERE id = ?",
                        (now, replacement["id"]),
                    )

    def create_or_update_recipient_group(self, owner_user_id: int, group_name: str, recipients: list[str], notes: str = "") -> int:
        now = datetime.now().isoformat(timespec="seconds")
        recipients_json = json.dumps(recipients)
        with self._lock, self._connect() as connection:
            cursor = connection.cursor()
            existing = cursor.execute(
                "SELECT id FROM recipient_groups WHERE group_name = ? AND owner_user_id = ?",
                (group_name, owner_user_id),
            ).fetchone()
            if existing:
                cursor.execute(
                    """
                    UPDATE recipient_groups
                    SET recipients = ?, notes = ?, updated_at = ?
                    WHERE group_name = ? AND owner_user_id = ?
                    """,
                    (recipients_json, notes, now, group_name, owner_user_id),
                )
                return int(existing["id"])

            cursor.execute(
                """
                INSERT INTO recipient_groups (owner_user_id, group_name, recipients, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (owner_user_id, group_name, recipients_json, notes, now, now),
            )
            return int(cursor.lastrowid)

    def fetch_recipient_groups(self, owner_user_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            if owner_user_id is None:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM recipient_groups
                    ORDER BY lower(group_name) ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM recipient_groups
                    WHERE owner_user_id = ?
                    ORDER BY lower(group_name) ASC
                    """,
                    (owner_user_id,),
                ).fetchall()
        groups = [dict(row) for row in rows]
        for group in groups:
            group["recipients"] = json.loads(group["recipients"]) if group.get("recipients") else []
        return groups

    def delete_recipient_group(self, group_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM recipient_groups WHERE id = ?", (group_id,))

    def create_or_update_email_template(
        self,
        owner_user_id: int,
        template_name: str,
        subject_template: str,
        body_template: str,
        tone: str,
        notes: str = "",
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            cursor = connection.cursor()
            existing = cursor.execute(
                "SELECT id FROM email_templates WHERE template_name = ? AND owner_user_id = ?",
                (template_name, owner_user_id),
            ).fetchone()
            if existing:
                cursor.execute(
                    """
                    UPDATE email_templates
                    SET subject_template = ?, body_template = ?, tone = ?, notes = ?, updated_at = ?
                    WHERE template_name = ? AND owner_user_id = ?
                    """,
                    (subject_template, body_template, tone, notes, now, template_name, owner_user_id),
                )
                return int(existing["id"])

            cursor.execute(
                """
                INSERT INTO email_templates (owner_user_id, template_name, subject_template, body_template, tone, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_user_id, template_name, subject_template, body_template, tone, notes, now, now),
            )
            return int(cursor.lastrowid)

    def fetch_email_templates(self, owner_user_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            if owner_user_id is None:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM email_templates
                    ORDER BY lower(template_name) ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM email_templates
                    WHERE owner_user_id = ?
                    ORDER BY lower(template_name) ASC
                    """,
                    (owner_user_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def delete_email_template(self, template_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM email_templates WHERE id = ?", (template_id,))

    def export_email_workspace(self, export_path: Path, owner_user_id: int | None = None) -> None:
        payload = {
            "sender_profiles": self.fetch_sender_profiles(owner_user_id),
            "recipient_groups": self.fetch_recipient_groups(owner_user_id),
            "email_templates": self.fetch_email_templates(owner_user_id),
            "tasks": [task for task in self.fetch_tasks(owner_user_id) if task["action_type"] == "Email"],
        }
        export_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def import_email_workspace(self, import_path: Path, owner_user_id: int) -> None:
        payload = json.loads(import_path.read_text(encoding="utf-8"))
        for profile in payload.get("sender_profiles", []):
            profile_id = self.create_or_update_sender_profile(
                owner_user_id=owner_user_id,
                profile_name=profile.get("profile_name", ""),
                sender_email=profile.get("sender_email", ""),
                app_password=profile.get("app_password", ""),
                display_name=profile.get("display_name", ""),
                signature=profile.get("signature", ""),
                is_default=bool(profile.get("is_default", False)),
            )
            if profile.get("is_default"):
                self.set_default_sender_profile(profile_id, owner_user_id)
        for group in payload.get("recipient_groups", []):
            self.create_or_update_recipient_group(
                owner_user_id=owner_user_id,
                group_name=group.get("group_name", ""),
                recipients=group.get("recipients", []),
                notes=group.get("notes", ""),
            )
        for template in payload.get("email_templates", []):
            self.create_or_update_email_template(
                owner_user_id=owner_user_id,
                template_name=template.get("template_name", ""),
                subject_template=template.get("subject_template", ""),
                body_template=template.get("body_template", ""),
                tone=template.get("tone", "Professional"),
                notes=template.get("notes", ""),
            )

    def update_task_payload(self, task_id: int, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE tasks SET payload = ?, updated_at = ? WHERE id = ?",
                (json.dumps(payload), datetime.now().isoformat(timespec="seconds"), task_id),
            )


    def fetch_logs(self, owner_user_id: int | None = None, limit: int = 300) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            if owner_user_id is None:
                rows = connection.execute(
                    """
                    SELECT logs.id, logs.task_id, logs.level, logs.message, logs.executed_at, tasks.name AS task_name
                    FROM logs
                    LEFT JOIN tasks ON tasks.id = logs.task_id
                    ORDER BY datetime(logs.executed_at) DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT logs.id, logs.task_id, logs.level, logs.message, logs.executed_at, tasks.name AS task_name
                    FROM logs
                    LEFT JOIN tasks ON tasks.id = logs.task_id
                    WHERE logs.owner_user_id = ?
                    ORDER BY datetime(logs.executed_at) DESC
                    LIMIT ?
                    """,
                    (owner_user_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_dashboard_stats(self, owner_user_id: int | None = None) -> dict[str, int]:
        today = datetime.now().date().isoformat()
        with self._lock, self._connect() as connection:
            task_where = " WHERE owner_user_id = ?" if owner_user_id is not None else ""
            task_args: tuple[Any, ...] = (owner_user_id,) if owner_user_id is not None else ()
            total_tasks = connection.execute(f"SELECT COUNT(*) FROM tasks{task_where}", task_args).fetchone()[0]
            active_tasks = connection.execute(
                f"SELECT COUNT(*) FROM tasks{task_where}{' AND' if task_where else ' WHERE'} status = 'active'",
                task_args,
            ).fetchone()[0]
            email_tasks = connection.execute(
                f"SELECT COUNT(*) FROM tasks{task_where}{' AND' if task_where else ' WHERE'} action_type = 'Email'",
                task_args,
            ).fetchone()[0]
            reminder_tasks = connection.execute(
                f"SELECT COUNT(*) FROM tasks{task_where}{' AND' if task_where else ' WHERE'} action_type = 'Reminder'",
                task_args,
            ).fetchone()[0]
            if owner_user_id is None:
                executions_today = connection.execute(
                    "SELECT COUNT(*) FROM logs WHERE substr(executed_at, 1, 10) = ?",
                    (today,),
                ).fetchone()[0]
            else:
                executions_today = connection.execute(
                    "SELECT COUNT(*) FROM logs WHERE owner_user_id = ? AND substr(executed_at, 1, 10) = ?",
                    (owner_user_id, today),
                ).fetchone()[0]
        return {
            "total_tasks": total_tasks,
            "active_tasks": active_tasks,
            "email_tasks": email_tasks,
            "reminder_tasks": reminder_tasks,
            "executions_today": executions_today,
        }

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task["payload"] = json.loads(task["payload"]) if task.get("payload") else {}
        return task
