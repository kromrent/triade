from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from models import Priority, RecurrenceKind, Reminder, ReminderKind, Task, TaskStatus


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

RECURRENCE_LABELS = {
    RecurrenceKind.DAILY: "ежедневная",
    RecurrenceKind.WEEKLY: "еженедельная",
}


def format_dt(value, timezone_name: str) -> str:
    if value is None:
        return "не задано"
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%d.%m.%Y %H:%M")


def format_task_created(task: Task, timezone_name: str) -> str:
    lines = [
        "<b>Задача создана</b>",
        "",
        f"<b>{escape(task.title)}</b>",
        f"Статус: {STATUS_LABELS[task.status]}",
        f"Приоритет: {PRIORITY_LABELS[task.priority]}",
        f"Первое напоминание: {format_dt(task.start_reminder_at, timezone_name)}",
    ]
    recurrence = format_task_recurrence(task)
    if recurrence:
        lines.append(f"Тип: {recurrence}")
        lines.append("Следующее повторение создается автоматически.")
    else:
        lines.append(f"Повтор: каждые {task.repeat_every_minutes} мин.")
    return "\n".join(lines)


def format_task_already_exists(task: Task, timezone_name: str) -> str:
    lines = [
        "<b>Такая задача уже есть</b>",
        "",
        f"<b>{escape(task.title)}</b>",
        f"Статус: {STATUS_LABELS[task.status]}",
        f"Приоритет: {PRIORITY_LABELS[task.priority]}",
        f"Первое напоминание: {format_dt(task.start_reminder_at, timezone_name)}",
    ]
    recurrence = format_task_recurrence(task)
    if recurrence:
        lines.append(f"Тип: {recurrence}")
    else:
        lines.append(f"Повтор: каждые {task.repeat_every_minutes} мин.")
    lines.append("Новую не добавляю.")
    return "\n".join(lines)


def format_task_details(task: Task, timezone_name: str) -> str:
    lines = [
        f"<b>{escape(task.title)}</b>",
        f"Статус: {STATUS_LABELS[task.status]}",
        f"Приоритет: {PRIORITY_LABELS[task.priority]}",
        f"Первое напоминание: {format_dt(task.start_reminder_at, timezone_name)}",
    ]
    if task.description:
        lines.insert(1, escape(task.description))
    recurrence = format_task_recurrence(task)
    if recurrence:
        lines.append(f"Тип: {recurrence}")
        lines.append("Следующее повторение создается автоматически.")
    else:
        lines.append(f"Повтор старта: каждые {task.repeat_every_minutes} мин.")
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
        recurrence = format_task_recurrence(task)
        recurrence_line = f"\nТип: {recurrence}" if recurrence else ""
        lines.append(
            "\n"
            f"#{task.id} <b>{escape(task.title)}</b>\n"
            f"Статус: {STATUS_LABELS[task.status]}\n"
            f"Приоритет: {PRIORITY_LABELS[task.priority]}\n"
            f"Напоминание: {format_dt(task.start_reminder_at, timezone_name)}"
            f"{recurrence_line}"
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
    recurrence = format_task_recurrence(task)
    if recurrence:
        lines.append(f"Тип: {recurrence}")
        lines.extend(
            [
                f"Приоритет: {PRIORITY_LABELS[task.priority]}",
                "Следующее повторение будет запланировано автоматически.",
            ]
        )
        return "\n".join(lines)
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


def format_task_recurrence(task: Task) -> str | None:
    if task.recurrence_kind == RecurrenceKind.NONE:
        return None
    return RECURRENCE_LABELS.get(task.recurrence_kind, task.recurrence_kind.value)
