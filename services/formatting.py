from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from models import Priority, Reminder, ReminderKind, Task, TaskStatus


STATUS_LABELS = {
    TaskStatus.PENDING: "ожидает старта",
    TaskStatus.NUDGING: "ждет подтверждения старта",
    TaskStatus.SNOOZED: "отложена",
    TaskStatus.IN_PROGRESS: "в процессе",
    TaskStatus.DONE: "завершена",
    TaskStatus.CANCELLED: "отменена",
}

PRIORITY_LABELS = {
    Priority.LOW: "низкий",
    Priority.MEDIUM: "средний",
    Priority.HIGH: "высокий",
}

REMINDER_KIND_LABELS = {
    ReminderKind.START: "старт задачи",
    ReminderKind.CHECKIN: "проверка прогресса",
    ReminderKind.FOCUS_NUDGE: "возврат в фокус",
}


def format_dt(value, timezone_name: str) -> str:
    if value is None:
        return "не задано"
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%d.%m.%Y %H:%M")


def format_task_created(task: Task, timezone_name: str) -> str:
    return (
        "<b>Задача создана</b>\n\n"
        f"<b>{escape(task.title)}</b>\n"
        f"Статус: {STATUS_LABELS[task.status]}\n"
        f"Приоритет: {PRIORITY_LABELS[task.priority]}\n"
        f"Первое напоминание: {format_dt(task.start_reminder_at, timezone_name)}\n"
        f"Повтор: каждые {task.repeat_every_minutes} мин."
    )


def format_task_details(task: Task, timezone_name: str) -> str:
    lines = [
        f"<b>{escape(task.title)}</b>",
        f"Статус: {STATUS_LABELS[task.status]}",
        f"Приоритет: {PRIORITY_LABELS[task.priority]}",
        f"Первое напоминание: {format_dt(task.start_reminder_at, timezone_name)}",
        f"Повтор старта: каждые {task.repeat_every_minutes} мин.",
    ]
    if task.description:
        lines.insert(1, escape(task.description))
    if task.postponed_until:
        lines.append(f"Отложена до: {format_dt(task.postponed_until, timezone_name)}")
    if task.started_at:
        lines.append(f"Начата: {format_dt(task.started_at, timezone_name)}")
    if task.completed_at:
        lines.append(f"Завершена: {format_dt(task.completed_at, timezone_name)}")
    if task.cancelled_at:
        lines.append(f"Отменена: {format_dt(task.cancelled_at, timezone_name)}")
    return "\n".join(lines)


def format_task_list(
    tasks: list[Task],
    timezone_name: str,
    title: str = "Мои задачи",
    empty_text: str = "У тебя пока нет задач.",
) -> str:
    if not tasks:
        return empty_text

    lines = [f"<b>{escape(title)}</b>"]
    for task in tasks:
        lines.append(
            "\n"
            f"#{task.id} <b>{escape(task.title)}</b>\n"
            f"Статус: {STATUS_LABELS[task.status]}\n"
            f"Приоритет: {PRIORITY_LABELS[task.priority]}\n"
            f"Напоминание: {format_dt(task.start_reminder_at, timezone_name)}"
        )
    return "\n".join(lines)


def format_active_reminders(items: list[tuple[Reminder, Task]], timezone_name: str) -> str:
    if not items:
        return "Активных напоминаний нет."

    lines = ["<b>Активные напоминания</b>"]
    for reminder, task in items:
        lines.append(
            "\n"
            f"{format_dt(reminder.scheduled_at, timezone_name)}\n"
            f"<b>{escape(task.title)}</b>\n"
            f"Тип: {REMINDER_KIND_LABELS[reminder.kind]}"
        )
    return "\n".join(lines)


def format_start_reminder(task: Task, timezone_name: str) -> str:
    lines = [
        "<b>Пора начать задачу</b>",
        f"<b>{escape(task.title)}</b>",
    ]
    if task.description:
        lines.append(escape(task.description))
    lines.extend(
        [
            f"Приоритет: {PRIORITY_LABELS[task.priority]}",
            f"Если не нажать «Начал» или «Готово», напомню снова через {task.repeat_every_minutes} мин.",
        ]
    )
    return "\n".join(lines)


def format_checkin(task: Task) -> str:
    return (
        "<b>Как идет задача?</b>\n"
        f"<b>{escape(task.title)}</b>\n"
        "Выбери текущее состояние."
    )


def clip_button_text(value: str, limit: int = 48) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1]}..."
