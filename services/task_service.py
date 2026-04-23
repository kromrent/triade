from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from config import Settings
from database import Database
from models import (
    Priority,
    Reminder,
    ReminderKind,
    ReminderStatus,
    Task,
    TaskStatus,
    TERMINAL_TASK_STATUSES,
    utc_now,
)


class TaskNotFoundError(ValueError):
    pass


class InvalidTaskTransitionError(ValueError):
    pass


class TaskService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def ensure_user(self, user, chat_id: int) -> None:
        if user is None:
            return
        self.database.upsert_user(
            telegram_user_id=user.id,
            chat_id=chat_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )

    def create_task(
        self,
        telegram_user_id: int,
        chat_id: int,
        title: str,
        description: Optional[str],
        start_reminder_at: datetime,
        repeat_every_minutes: int,
        priority: Priority,
    ) -> tuple[Task, Reminder]:
        task = self.database.create_task(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            title=title.strip()[:255],
            description=description.strip() if description else None,
            start_reminder_at=start_reminder_at,
            repeat_every_minutes=repeat_every_minutes,
            priority=priority,
        )
        reminder = self.database.create_reminder(task.id, ReminderKind.START, start_reminder_at)
        self.database.add_event(task.id, telegram_user_id, "task_created")
        return task, reminder

    def get_task_for_user(self, task_id: int, telegram_user_id: int) -> Task:
        task = self.database.get_task_for_user(task_id, telegram_user_id)
        if task is None:
            raise TaskNotFoundError("Task not found")
        return task

    def list_tasks(
        self,
        telegram_user_id: int,
        include_closed: bool = False,
        limit: int = 20,
    ) -> list[Task]:
        return self.database.list_tasks_for_user(
            telegram_user_id,
            limit=limit,
            include_closed=include_closed,
        )

    def list_task_history(self, telegram_user_id: int, limit: int = 20) -> list[Task]:
        return self.list_tasks(telegram_user_id, include_closed=True, limit=limit)

    def list_active_reminders(self, telegram_user_id: int) -> list[tuple[Reminder, Task]]:
        return self.database.list_active_reminders_for_user(telegram_user_id)

    def list_scheduled_reminders(self) -> list[Reminder]:
        return self.database.list_scheduled_reminders()

    def take_due_reminder(self, reminder_id: int) -> Optional[tuple[Task, Reminder]]:
        reminder = self.database.get_reminder(reminder_id)
        if reminder is None or reminder.status != ReminderStatus.SCHEDULED:
            return None

        task = self.database.get_task(reminder.task_id)
        if task is None:
            return None

        if task.status in TERMINAL_TASK_STATUSES:
            self.database.cancel_scheduled_reminders(task.id)
            return None

        if reminder.kind == ReminderKind.START:
            if task.status not in {TaskStatus.PENDING, TaskStatus.NUDGING, TaskStatus.SNOOZED}:
                self.database.cancel_scheduled_reminders(task.id, [ReminderKind.START])
                return None
            sent_reminder = self.database.mark_reminder_sent(reminder.id)
            updated_task = self.database.update_task(
                task.id,
                status=TaskStatus.NUDGING,
                postponed_until=None,
            )
            self.database.add_event(task.id, task.telegram_user_id, "start_reminder_sent")
            return updated_task, sent_reminder

        if reminder.kind == ReminderKind.CHECKIN:
            if task.status != TaskStatus.IN_PROGRESS:
                self.database.cancel_scheduled_reminders(task.id, [ReminderKind.CHECKIN])
                return None
            sent_reminder = self.database.mark_reminder_sent(reminder.id)
            self.database.add_event(task.id, task.telegram_user_id, "checkin_sent")
            return task, sent_reminder

        if reminder.kind == ReminderKind.FOCUS_NUDGE:
            if task.status != TaskStatus.IN_PROGRESS:
                self.database.cancel_scheduled_reminders(task.id, [ReminderKind.FOCUS_NUDGE])
                return None
            sent_reminder = self.database.mark_reminder_sent(reminder.id)
            self.database.add_event(task.id, task.telegram_user_id, "focus_nudge_due")
            return task, sent_reminder

        return None

    def plan_next_start_reminder(self, task_id: int) -> Optional[Reminder]:
        task = self.database.get_task(task_id)
        if task is None or task.status != TaskStatus.NUDGING:
            return None

        next_at = utc_now() + timedelta(minutes=task.repeat_every_minutes)
        return self.database.create_reminder(task.id, ReminderKind.START, next_at)

    def start_task(self, task_id: int, telegram_user_id: int) -> tuple[Task, Reminder]:
        task = self.get_task_for_user(task_id, telegram_user_id)
        if task.status in TERMINAL_TASK_STATUSES:
            raise InvalidTaskTransitionError("Cannot start a closed task")

        self.database.cancel_scheduled_reminders(task.id, [ReminderKind.START])
        updated = self.database.update_task(
            task.id,
            status=TaskStatus.IN_PROGRESS,
            started_at=task.started_at or utc_now(),
            postponed_until=None,
        )
        checkin_at = utc_now() + timedelta(minutes=self.settings.checkin_after_minutes)
        reminder = self.database.create_reminder(task.id, ReminderKind.CHECKIN, checkin_at)
        self.database.add_event(task.id, telegram_user_id, "task_started")
        return updated, reminder

    def plan_focus_nudge(self, task_id: int, minutes: Optional[int] = None) -> Optional[Reminder]:
        task = self.database.get_task(task_id)
        if task is None or task.status != TaskStatus.IN_PROGRESS:
            return None

        self.database.cancel_scheduled_reminders(task.id, [ReminderKind.FOCUS_NUDGE])
        nudge_after = minutes or (self.settings.checkin_after_minutes + 20)
        scheduled_at = utc_now() + timedelta(minutes=nudge_after)
        return self.database.create_reminder(task.id, ReminderKind.FOCUS_NUDGE, scheduled_at)

    def snooze_task(self, task_id: int, telegram_user_id: int) -> tuple[Task, Reminder]:
        task = self.get_task_for_user(task_id, telegram_user_id)
        if task.status in TERMINAL_TASK_STATUSES or task.status == TaskStatus.IN_PROGRESS:
            raise InvalidTaskTransitionError("Cannot snooze this task")

        return self.postpone_task(task_id, telegram_user_id, self.settings.default_snooze_minutes)

    def postpone_task(
        self,
        task_id: int,
        telegram_user_id: int,
        minutes: int,
    ) -> tuple[Task, Reminder]:
        task = self.get_task_for_user(task_id, telegram_user_id)
        if task.status in TERMINAL_TASK_STATUSES or task.status == TaskStatus.IN_PROGRESS:
            raise InvalidTaskTransitionError("Cannot postpone this task")

        minutes = max(1, min(minutes, 1440))
        snooze_until = utc_now() + timedelta(minutes=minutes)
        self.database.cancel_scheduled_reminders(task.id, [ReminderKind.START])
        updated = self.database.update_task(
            task.id,
            status=TaskStatus.SNOOZED,
            start_reminder_at=snooze_until,
            postponed_until=snooze_until,
        )
        reminder = self.database.create_reminder(task.id, ReminderKind.START, snooze_until)
        self.database.add_event(task.id, telegram_user_id, "task_snoozed", f"{minutes} minutes")
        return updated, reminder

    def complete_task(self, task_id: int, telegram_user_id: int) -> Task:
        task = self.get_task_for_user(task_id, telegram_user_id)
        if task.status == TaskStatus.DONE:
            return task
        if task.status == TaskStatus.CANCELLED:
            raise InvalidTaskTransitionError("Cannot complete a cancelled task")

        self.database.cancel_scheduled_reminders(task.id)
        updated = self.database.update_task(
            task.id,
            status=TaskStatus.DONE,
            completed_at=task.completed_at or utc_now(),
            postponed_until=None,
        )
        self.database.add_event(task.id, telegram_user_id, "task_completed")
        return updated

    def cancel_task(self, task_id: int, telegram_user_id: int) -> Task:
        task = self.get_task_for_user(task_id, telegram_user_id)
        if task.status == TaskStatus.CANCELLED:
            return task
        if task.status == TaskStatus.DONE:
            raise InvalidTaskTransitionError("Cannot cancel a completed task")

        self.database.cancel_scheduled_reminders(task.id)
        updated = self.database.update_task(
            task.id,
            status=TaskStatus.CANCELLED,
            cancelled_at=task.cancelled_at or utc_now(),
            postponed_until=None,
        )
        self.database.add_event(task.id, telegram_user_id, "task_cancelled")
        return updated

    def cancel_all_active_tasks(self, telegram_user_id: int) -> int:
        tasks = self.database.list_tasks_for_user(
            telegram_user_id,
            limit=1000,
            include_closed=False,
        )
        cancelled = 0
        for task in tasks:
            if task.status in TERMINAL_TASK_STATUSES:
                continue
            self.database.cancel_scheduled_reminders(task.id)
            self.database.update_task(
                task.id,
                status=TaskStatus.CANCELLED,
                cancelled_at=task.cancelled_at or utc_now(),
                postponed_until=None,
            )
            self.database.add_event(task.id, telegram_user_id, "task_cancelled", "bulk cancel")
            cancelled += 1
        return cancelled

    def continue_checkin(self, task_id: int, telegram_user_id: int) -> tuple[Task, Reminder]:
        task = self.get_task_for_user(task_id, telegram_user_id)
        if task.status != TaskStatus.IN_PROGRESS:
            raise InvalidTaskTransitionError("Task is not in progress")

        next_at = utc_now() + timedelta(minutes=self.settings.checkin_after_minutes)
        reminder = self.database.create_reminder(task.id, ReminderKind.CHECKIN, next_at)
        self.database.add_event(task.id, telegram_user_id, "checkin_continue")
        return task, reminder

    def almost_done_checkin(self, task_id: int, telegram_user_id: int) -> tuple[Task, Reminder]:
        task = self.get_task_for_user(task_id, telegram_user_id)
        if task.status != TaskStatus.IN_PROGRESS:
            raise InvalidTaskTransitionError("Task is not in progress")

        next_at = utc_now() + timedelta(minutes=10)
        reminder = self.database.create_reminder(task.id, ReminderKind.CHECKIN, next_at)
        self.database.add_event(task.id, telegram_user_id, "checkin_almost_done")
        return task, reminder

    def need_help_checkin(self, task_id: int, telegram_user_id: int) -> tuple[Task, Reminder]:
        task = self.get_task_for_user(task_id, telegram_user_id)
        if task.status != TaskStatus.IN_PROGRESS:
            raise InvalidTaskTransitionError("Task is not in progress")

        next_at = utc_now() + timedelta(minutes=10)
        reminder = self.database.create_reminder(task.id, ReminderKind.CHECKIN, next_at)
        self.database.add_event(task.id, telegram_user_id, "checkin_help_requested")
        return task, reminder

    def log_event(
        self,
        task_id: Optional[int],
        telegram_user_id: int,
        event_type: str,
        details: Optional[str] = None,
    ) -> None:
        self.database.add_event(task_id, telegram_user_id, event_type, details)
