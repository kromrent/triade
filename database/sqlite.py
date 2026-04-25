from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Iterable, Optional

from models import (
    MotivationEntry,
    MotivationalTrack,
    Priority,
    RecurrenceKind,
    Reminder,
    ReminderKind,
    ReminderStatus,
    ToneMode,
    Task,
    TaskStatus,
    TrackCategory,
    UserGoal,
    UserPreference,
    datetime_from_db,
    datetime_to_db,
    utc_now,
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = RLock()

    def connect(self) -> None:
        if self._connection is not None:
            return

        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(str(self.path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        self._connection = connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected")
        return self._connection

    def init_schema(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL UNIQUE,
                    chat_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    start_reminder_at TEXT NOT NULL,
                    repeat_every_minutes INTEGER NOT NULL,
                    recurrence_kind TEXT NOT NULL DEFAULT 'none',
                    recurrence_parent_task_id INTEGER,
                    started_at TEXT,
                    completed_at TEXT,
                    cancelled_at TEXT,
                    postponed_until TEXT,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id),
                    FOREIGN KEY (recurrence_parent_task_id) REFERENCES tasks(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_user_status
                    ON tasks (telegram_user_id, status);

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    sent_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_reminders_status_schedule
                    ON reminders (status, scheduled_at);

                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    telegram_user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_task_created
                    ON task_events (task_id, created_at);

                CREATE TABLE IF NOT EXISTS user_preferences (
                    telegram_user_id INTEGER PRIMARY KEY,
                    tone_mode TEXT NOT NULL,
                    ai_enabled INTEGER NOT NULL,
                    strictness_level INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
                );

                CREATE TABLE IF NOT EXISTS user_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    is_active INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_user_goals_user_active
                    ON user_goals (telegram_user_id, is_active);

                CREATE TABLE IF NOT EXISTS motivation_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    category TEXT NOT NULL,
                    is_active INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_motivation_entries_user_active
                    ON motivation_entries (telegram_user_id, is_active);

                CREATE TABLE IF NOT EXISTS motivational_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT,
                    description TEXT,
                    file_path TEXT,
                    category TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_tracks_user_category
                    ON motivational_tracks (telegram_user_id, category);

                CREATE TABLE IF NOT EXISTS ai_interactions_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    task_id INTEGER,
                    scenario TEXT NOT NULL,
                    tone_mode TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    user_message TEXT,
                    prompt TEXT,
                    response TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id),
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_ai_log_user_created
                    ON ai_interactions_log (telegram_user_id, created_at);
                """
            )
            self._ensure_column("ai_interactions_log", "user_message", "TEXT")
            self._ensure_column("motivational_tracks", "file_path", "TEXT")
            self._ensure_column("tasks", "recurrence_kind", "TEXT NOT NULL DEFAULT 'none'")
            self._ensure_column("tasks", "recurrence_parent_task_id", "INTEGER")
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_recurrence_parent
                    ON tasks (recurrence_parent_task_id)
                """
            )
            self.connection.commit()

    def _ensure_column(self, table_name: str, column_name: str, column_definition: str) -> None:
        columns = {
            str(row["name"])
            for row in self.connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in columns:
            return
        self.connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    def upsert_user(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
    ) -> None:
        now = datetime_to_db(utc_now())
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO users (
                    telegram_user_id, chat_id, username, first_name, last_name,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    updated_at = excluded.updated_at
                """,
                (telegram_user_id, chat_id, username, first_name, last_name, now, now),
            )
            self.connection.commit()

    def list_report_recipients(self) -> list[tuple[int, int]]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT telegram_user_id, chat_id
                FROM users
                ORDER BY updated_at DESC
                """
            ).fetchall()
            return [(int(row["telegram_user_id"]), int(row["chat_id"])) for row in rows]

    def create_task(
        self,
        telegram_user_id: int,
        chat_id: int,
        title: str,
        description: Optional[str],
        start_reminder_at: datetime,
        repeat_every_minutes: int,
        priority: Priority,
        recurrence_kind: RecurrenceKind = RecurrenceKind.NONE,
        recurrence_parent_task_id: Optional[int] = None,
    ) -> Task:
        now = datetime_to_db(utc_now())
        with self._lock:
            cursor = self.connection.execute(
                """
                INSERT INTO tasks (
                    telegram_user_id, chat_id, title, description, status, priority,
                    created_at, updated_at, start_reminder_at, repeat_every_minutes,
                    recurrence_kind, recurrence_parent_task_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    chat_id,
                    title,
                    description,
                    TaskStatus.PENDING.value,
                    priority.value,
                    now,
                    now,
                    datetime_to_db(start_reminder_at),
                    repeat_every_minutes,
                    recurrence_kind.value,
                    recurrence_parent_task_id,
                ),
            )
            self.connection.commit()
            return self.get_task(cursor.lastrowid)

    def get_task(self, task_id: int) -> Optional[Task]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def get_task_for_user(self, task_id: int, telegram_user_id: int) -> Optional[Task]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND telegram_user_id = ?",
                (task_id, telegram_user_id),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def get_recurring_child_task(self, parent_task_id: int) -> Optional[Task]:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT * FROM tasks
                WHERE recurrence_parent_task_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (parent_task_id,),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def list_tasks_for_user(
        self,
        telegram_user_id: int,
        limit: int = 20,
        include_closed: bool = False,
    ) -> list[Task]:
        params: list[object] = [telegram_user_id]
        closed_filter = ""
        if not include_closed:
            closed_filter = "AND status NOT IN (?, ?)"
            params.extend([TaskStatus.DONE.value, TaskStatus.CANCELLED.value])
        params.append(limit)

        with self._lock:
            rows = self.connection.execute(
                f"""
                SELECT * FROM tasks
                WHERE telegram_user_id = ?
                  {closed_filter}
                ORDER BY
                    CASE status
                        WHEN 'nudging' THEN 0
                        WHEN 'snoozed' THEN 1
                        WHEN 'pending' THEN 2
                        WHEN 'in_progress' THEN 3
                        WHEN 'done' THEN 4
                        ELSE 5
                    END,
                    created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._row_to_task(row) for row in rows]

    def list_task_history_for_user(
        self,
        telegram_user_id: int,
        limit: int = 20,
    ) -> list[Task]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT * FROM tasks
                WHERE telegram_user_id = ?
                  AND status IN (?, ?)
                ORDER BY COALESCE(completed_at, cancelled_at, updated_at) DESC
                LIMIT ?
                """,
                (
                    telegram_user_id,
                    TaskStatus.DONE.value,
                    TaskStatus.CANCELLED.value,
                    limit,
                ),
            ).fetchall()
            return [self._row_to_task(row) for row in rows]

    def get_latest_active_task_for_user(self, telegram_user_id: int) -> Optional[Task]:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT * FROM tasks
                WHERE telegram_user_id = ?
                  AND status NOT IN (?, ?)
                ORDER BY
                    CASE status
                        WHEN 'in_progress' THEN 0
                        WHEN 'nudging' THEN 1
                        WHEN 'snoozed' THEN 2
                        WHEN 'pending' THEN 3
                        ELSE 4
                    END,
                    updated_at DESC
                LIMIT 1
                """,
                (telegram_user_id, TaskStatus.DONE.value, TaskStatus.CANCELLED.value),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def update_task(self, task_id: int, **fields: object) -> Optional[Task]:
        allowed_fields = {
            "title",
            "description",
            "status",
            "priority",
            "start_reminder_at",
            "repeat_every_minutes",
            "recurrence_kind",
            "recurrence_parent_task_id",
            "started_at",
            "completed_at",
            "cancelled_at",
            "postponed_until",
        }
        updates: list[str] = []
        values: list[object] = []

        for name, value in fields.items():
            if name not in allowed_fields:
                raise ValueError(f"Cannot update task field: {name}")
            updates.append(f"{name} = ?")
            values.append(self._serialize_value(value))

        updates.append("updated_at = ?")
        values.append(datetime_to_db(utc_now()))
        values.append(task_id)

        with self._lock:
            self.connection.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            self.connection.commit()
            return self.get_task(task_id)

    def create_reminder(
        self,
        task_id: int,
        kind: ReminderKind,
        scheduled_at: datetime,
    ) -> Reminder:
        now = datetime_to_db(utc_now())
        with self._lock:
            cursor = self.connection.execute(
                """
                INSERT INTO reminders (task_id, kind, status, scheduled_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    kind.value,
                    ReminderStatus.SCHEDULED.value,
                    datetime_to_db(scheduled_at),
                    now,
                ),
            )
            self.connection.commit()
            return self.get_reminder(cursor.lastrowid)

    def get_reminder(self, reminder_id: int) -> Optional[Reminder]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
            return self._row_to_reminder(row) if row else None

    def get_scheduled_reminder_for_task(
        self,
        task_id: int,
        kind: ReminderKind | None = None,
    ) -> Optional[Reminder]:
        params: list[object] = [task_id, ReminderStatus.SCHEDULED.value]
        where = "task_id = ? AND status = ?"
        if kind is not None:
            where += " AND kind = ?"
            params.append(kind.value)

        with self._lock:
            row = self.connection.execute(
                f"""
                SELECT * FROM reminders
                WHERE {where}
                ORDER BY scheduled_at ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            return self._row_to_reminder(row) if row else None

    def list_scheduled_reminders(self) -> list[Reminder]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT * FROM reminders
                WHERE status = ?
                ORDER BY scheduled_at ASC
                """,
                (ReminderStatus.SCHEDULED.value,),
            ).fetchall()
            return [self._row_to_reminder(row) for row in rows]

    def list_active_reminders_for_user(
        self,
        telegram_user_id: int,
        limit: int = 20,
    ) -> list[tuple[Reminder, Task]]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT r.id AS reminder_id, t.id AS task_id
                FROM reminders r
                JOIN tasks t ON t.id = r.task_id
                WHERE t.telegram_user_id = ?
                  AND r.status = ?
                  AND t.status NOT IN (?, ?)
                ORDER BY r.scheduled_at ASC
                LIMIT ?
                """,
                (
                    telegram_user_id,
                    ReminderStatus.SCHEDULED.value,
                    TaskStatus.DONE.value,
                    TaskStatus.CANCELLED.value,
                    limit,
                ),
            ).fetchall()

        result: list[tuple[Reminder, Task]] = []
        for row in rows:
            reminder = self.get_reminder(row["reminder_id"])
            task = self.get_task(row["task_id"])
            if reminder and task:
                result.append((reminder, task))
        return result

    def mark_reminder_sent(self, reminder_id: int) -> Optional[Reminder]:
        with self._lock:
            self.connection.execute(
                """
                UPDATE reminders
                SET status = ?, sent_at = ?
                WHERE id = ?
                """,
                (
                    ReminderStatus.SENT.value,
                    datetime_to_db(utc_now()),
                    reminder_id,
                ),
            )
            self.connection.commit()
            return self.get_reminder(reminder_id)

    def cancel_scheduled_reminders(
        self,
        task_id: int,
        kinds: Optional[Iterable[ReminderKind]] = None,
    ) -> list[int]:
        params: list[object] = [task_id, ReminderStatus.SCHEDULED.value]
        where = "task_id = ? AND status = ?"

        if kinds:
            kind_values = [kind.value for kind in kinds]
            placeholders = ", ".join("?" for _ in kind_values)
            where += f" AND kind IN ({placeholders})"
            params.extend(kind_values)

        with self._lock:
            rows = self.connection.execute(
                f"SELECT id FROM reminders WHERE {where}",
                params,
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            self.connection.execute(
                f"UPDATE reminders SET status = ? WHERE {where}",
                [ReminderStatus.CANCELLED.value, *params],
            )
            self.connection.commit()
            return ids

    def add_event(
        self,
        task_id: Optional[int],
        telegram_user_id: int,
        event_type: str,
        details: Optional[str] = None,
    ) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO task_events (task_id, telegram_user_id, event_type, details, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    telegram_user_id,
                    event_type,
                    details,
                    datetime_to_db(utc_now()),
                ),
            )
            self.connection.commit()

    def count_task_events(self, task_id: int, event_type: str) -> int:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM task_events
                WHERE task_id = ? AND event_type = ?
                """,
                (task_id, event_type),
            ).fetchone()
            return int(row["count"])

    def count_user_events_since(
        self,
        telegram_user_id: int,
        event_type: str,
        since: datetime,
    ) -> int:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM task_events
                WHERE telegram_user_id = ?
                  AND event_type = ?
                  AND created_at >= ?
                """,
                (telegram_user_id, event_type, datetime_to_db(since)),
            ).fetchone()
            return int(row["count"])

    def count_completed_tasks_since(self, telegram_user_id: int, since: datetime) -> int:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM tasks
                WHERE telegram_user_id = ?
                  AND status = ?
                  AND completed_at >= ?
                """,
                (telegram_user_id, TaskStatus.DONE.value, datetime_to_db(since)),
            ).fetchone()
            return int(row["count"])

    def get_or_create_user_preference(
        self,
        telegram_user_id: int,
        default_tone: ToneMode,
    ) -> UserPreference:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM user_preferences WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            if row:
                return self._row_to_user_preference(row)

            now = datetime_to_db(utc_now())
            self.connection.execute(
                """
                INSERT INTO user_preferences (
                    telegram_user_id, tone_mode, ai_enabled, strictness_level,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (telegram_user_id, default_tone.value, 1, 2, now, now),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM user_preferences WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            return self._row_to_user_preference(row)

    def set_user_tone_mode(self, telegram_user_id: int, tone_mode: ToneMode) -> UserPreference:
        self.get_or_create_user_preference(telegram_user_id, ToneMode.BRO)
        with self._lock:
            self.connection.execute(
                """
                UPDATE user_preferences
                SET tone_mode = ?, updated_at = ?
                WHERE telegram_user_id = ?
                """,
                (tone_mode.value, datetime_to_db(utc_now()), telegram_user_id),
            )
            self.connection.commit()
            return self.get_or_create_user_preference(telegram_user_id, tone_mode)

    def set_user_ai_enabled(self, telegram_user_id: int, enabled: bool) -> UserPreference:
        self.get_or_create_user_preference(telegram_user_id, ToneMode.BRO)
        with self._lock:
            self.connection.execute(
                """
                UPDATE user_preferences
                SET ai_enabled = ?, updated_at = ?
                WHERE telegram_user_id = ?
                """,
                (1 if enabled else 0, datetime_to_db(utc_now()), telegram_user_id),
            )
            self.connection.commit()
            return self.get_or_create_user_preference(telegram_user_id, ToneMode.BRO)

    def add_user_goal(self, telegram_user_id: int, text: str) -> UserGoal:
        with self._lock:
            cursor = self.connection.execute(
                """
                INSERT INTO user_goals (telegram_user_id, text, is_active, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_user_id, text, 1, datetime_to_db(utc_now())),
            )
            self.connection.commit()
            return self.get_user_goal(cursor.lastrowid)

    def get_user_goal(self, goal_id: int) -> Optional[UserGoal]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM user_goals WHERE id = ?",
                (goal_id,),
            ).fetchone()
            return self._row_to_user_goal(row) if row else None

    def list_user_goals(self, telegram_user_id: int, active_only: bool = True) -> list[UserGoal]:
        params: list[object] = [telegram_user_id]
        where = "telegram_user_id = ?"
        if active_only:
            where += " AND is_active = ?"
            params.append(1)
        with self._lock:
            rows = self.connection.execute(
                f"SELECT * FROM user_goals WHERE {where} ORDER BY created_at DESC",
                params,
            ).fetchall()
            return [self._row_to_user_goal(row) for row in rows]

    def add_motivation_entry(
        self,
        telegram_user_id: int,
        text: str,
        category: str = "why",
    ) -> MotivationEntry:
        with self._lock:
            cursor = self.connection.execute(
                """
                INSERT INTO motivation_entries (
                    telegram_user_id, text, category, is_active, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (telegram_user_id, text, category, 1, datetime_to_db(utc_now())),
            )
            self.connection.commit()
            return self.get_motivation_entry(cursor.lastrowid)

    def get_motivation_entry(self, entry_id: int) -> Optional[MotivationEntry]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM motivation_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
            return self._row_to_motivation_entry(row) if row else None

    def list_motivation_entries(
        self,
        telegram_user_id: int,
        active_only: bool = True,
    ) -> list[MotivationEntry]:
        params: list[object] = [telegram_user_id]
        where = "telegram_user_id = ?"
        if active_only:
            where += " AND is_active = ?"
            params.append(1)
        with self._lock:
            rows = self.connection.execute(
                f"SELECT * FROM motivation_entries WHERE {where} ORDER BY created_at DESC",
                params,
            ).fetchall()
            return [self._row_to_motivation_entry(row) for row in rows]

    def add_motivational_track(
        self,
        telegram_user_id: int,
        title: str,
        url: Optional[str],
        description: Optional[str],
        file_path: Optional[str],
        category: TrackCategory,
    ) -> MotivationalTrack:
        with self._lock:
            cursor = self.connection.execute(
                """
                INSERT INTO motivational_tracks (
                    telegram_user_id, title, url, description, file_path, category, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    title,
                    url,
                    description,
                    file_path,
                    category.value,
                    datetime_to_db(utc_now()),
                ),
            )
            self.connection.commit()
            return self.get_motivational_track(cursor.lastrowid)

    def get_motivational_track(self, track_id: int) -> Optional[MotivationalTrack]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM motivational_tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
            return self._row_to_track(row) if row else None

    def list_motivational_tracks(
        self,
        telegram_user_id: int,
        categories: Optional[Iterable[TrackCategory]] = None,
    ) -> list[MotivationalTrack]:
        params: list[object] = [telegram_user_id]
        where = "telegram_user_id = ?"
        if categories:
            values = [category.value for category in categories]
            placeholders = ", ".join("?" for _ in values)
            where += f" AND category IN ({placeholders})"
            params.extend(values)
        with self._lock:
            rows = self.connection.execute(
                f"SELECT * FROM motivational_tracks WHERE {where} ORDER BY created_at DESC",
                params,
            ).fetchall()
            return [self._row_to_track(row) for row in rows]

    def add_ai_interaction(
        self,
        telegram_user_id: int,
        task_id: Optional[int],
        scenario: str,
        tone_mode: str,
        provider: str,
        user_message: Optional[str],
        prompt: Optional[str],
        response: Optional[str],
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO ai_interactions_log (
                    telegram_user_id, task_id, scenario, tone_mode, provider,
                    user_message, prompt, response, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    task_id,
                    scenario,
                    tone_mode,
                    provider,
                    _clip(user_message, 1500),
                    _clip(prompt, 4000),
                    _clip(response, 2000),
                    _clip(error, 1000),
                    datetime_to_db(utc_now()),
                ),
            )
            self.connection.commit()

    def list_recent_ai_messages(self, telegram_user_id: int, limit: int = 8) -> list[tuple[str, str]]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT user_message, response
                FROM ai_interactions_log
                WHERE telegram_user_id = ?
                  AND (user_message IS NOT NULL OR response IS NOT NULL)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            ).fetchall()

        messages = [
            (str(row["user_message"] or ""), str(row["response"] or ""))
            for row in rows
            if row["user_message"] or row["response"]
        ]
        messages.reverse()
        return messages

    def list_recent_ai_responses(self, telegram_user_id: int, limit: int = 12) -> list[str]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT response
                FROM ai_interactions_log
                WHERE telegram_user_id = ? AND response IS NOT NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            ).fetchall()
            return [str(row["response"]) for row in rows if row["response"]]

    def has_recent_ai_interaction(
        self,
        telegram_user_id: int,
        since: datetime,
        scenario: Optional[str] = None,
    ) -> bool:
        params: list[object] = [telegram_user_id, datetime_to_db(since)]
        where = "telegram_user_id = ? AND created_at >= ?"
        if scenario is not None:
            where += " AND scenario = ?"
            params.append(scenario)

        with self._lock:
            row = self.connection.execute(
                f"SELECT 1 FROM ai_interactions_log WHERE {where} LIMIT 1",
                params,
            ).fetchone()
            return row is not None

    @staticmethod
    def _serialize_value(value: object) -> object:
        if isinstance(value, (TaskStatus, Priority, RecurrenceKind)):
            return value.value
        if isinstance(value, datetime):
            return datetime_to_db(value)
        return value

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=int(row["id"]),
            telegram_user_id=int(row["telegram_user_id"]),
            chat_id=int(row["chat_id"]),
            title=str(row["title"]),
            description=row["description"],
            status=TaskStatus(row["status"]),
            priority=Priority(row["priority"]),
            created_at=datetime_from_db(row["created_at"]),
            updated_at=datetime_from_db(row["updated_at"]),
            start_reminder_at=datetime_from_db(row["start_reminder_at"]),
            repeat_every_minutes=int(row["repeat_every_minutes"]),
            recurrence_kind=RecurrenceKind(str(row["recurrence_kind"] or RecurrenceKind.NONE.value)),
            recurrence_parent_task_id=(
                int(row["recurrence_parent_task_id"])
                if row["recurrence_parent_task_id"] is not None
                else None
            ),
            started_at=datetime_from_db(row["started_at"]),
            completed_at=datetime_from_db(row["completed_at"]),
            cancelled_at=datetime_from_db(row["cancelled_at"]),
            postponed_until=datetime_from_db(row["postponed_until"]),
        )

    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=int(row["id"]),
            task_id=int(row["task_id"]),
            kind=ReminderKind(row["kind"]),
            status=ReminderStatus(row["status"]),
            scheduled_at=datetime_from_db(row["scheduled_at"]),
            sent_at=datetime_from_db(row["sent_at"]),
            created_at=datetime_from_db(row["created_at"]),
        )

    @staticmethod
    def _row_to_user_preference(row: sqlite3.Row) -> UserPreference:
        return UserPreference(
            telegram_user_id=int(row["telegram_user_id"]),
            tone_mode=ToneMode(row["tone_mode"]),
            ai_enabled=bool(row["ai_enabled"]),
            strictness_level=int(row["strictness_level"]),
            created_at=datetime_from_db(row["created_at"]),
            updated_at=datetime_from_db(row["updated_at"]),
        )

    @staticmethod
    def _row_to_user_goal(row: sqlite3.Row) -> UserGoal:
        return UserGoal(
            id=int(row["id"]),
            telegram_user_id=int(row["telegram_user_id"]),
            text=str(row["text"]),
            is_active=bool(row["is_active"]),
            created_at=datetime_from_db(row["created_at"]),
        )

    @staticmethod
    def _row_to_motivation_entry(row: sqlite3.Row) -> MotivationEntry:
        return MotivationEntry(
            id=int(row["id"]),
            telegram_user_id=int(row["telegram_user_id"]),
            text=str(row["text"]),
            category=str(row["category"]),
            is_active=bool(row["is_active"]),
            created_at=datetime_from_db(row["created_at"]),
        )

    @staticmethod
    def _row_to_track(row: sqlite3.Row) -> MotivationalTrack:
        return MotivationalTrack(
            id=int(row["id"]),
            telegram_user_id=int(row["telegram_user_id"]),
            title=str(row["title"]),
            url=row["url"],
            description=row["description"],
            file_path=row["file_path"],
            category=TrackCategory(row["category"]),
            created_at=datetime_from_db(row["created_at"]),
        )


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]
