from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from models import Task, TaskStatus
from services.formatting import clip_button_text


def task_reminder_actions_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Начал", callback_data=f"task:start:{task_id}"),
                InlineKeyboardButton(text="Готово", callback_data=f"task:done:{task_id}"),
            ],
            [
                InlineKeyboardButton(text="Отложить на 10 минут", callback_data=f"task:snooze:{task_id}"),
                InlineKeyboardButton(text="Отменить", callback_data=f"task:cancel:{task_id}"),
            ],
            [InlineKeyboardButton(text="AI-помощь", callback_data=f"ai:menu:{task_id}")],
        ]
    )


def checkin_actions_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Продолжаю", callback_data=f"check:continue:{task_id}"),
                InlineKeyboardButton(text="Почти готово", callback_data=f"check:almost:{task_id}"),
            ],
            [
                InlineKeyboardButton(text="Завершил", callback_data=f"task:done:{task_id}"),
                InlineKeyboardButton(text="Нужна помощь", callback_data=f"check:help:{task_id}"),
            ],
            [InlineKeyboardButton(text="AI-помощь", callback_data=f"ai:menu:{task_id}")],
        ]
    )


def task_list_keyboard(
    tasks: list[Task],
    show_history_button: bool = False,
    show_active_button: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=clip_button_text(f"#{task.id} {task.title}"), callback_data=f"task:open:{task.id}")]
        for task in tasks
    ]
    if show_history_button:
        rows.append([InlineKeyboardButton(text="История задач", callback_data="task:history")])
    if show_active_button:
        rows.append([InlineKeyboardButton(text="Активные задачи", callback_data="task:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def task_actions_keyboard(task: Task) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if task.status in {TaskStatus.PENDING, TaskStatus.NUDGING, TaskStatus.SNOOZED}:
        rows.append(
            [
                InlineKeyboardButton(text="Начал", callback_data=f"task:start:{task.id}"),
                InlineKeyboardButton(text="Отложить на 10 минут", callback_data=f"task:snooze:{task.id}"),
            ]
        )

    if task.status not in {TaskStatus.DONE, TaskStatus.CANCELLED}:
        rows.append([InlineKeyboardButton(text="Готово", callback_data=f"task:done:{task.id}")])

    if task.status not in {TaskStatus.DONE, TaskStatus.CANCELLED}:
        rows.append([InlineKeyboardButton(text="Отменить", callback_data=f"task:cancel:{task.id}")])

    rows.append([InlineKeyboardButton(text="AI-помощь", callback_data=f"ai:menu:{task.id}")])
    rows.append([InlineKeyboardButton(text="Назад к списку", callback_data="task:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quick_add_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сохранить как задачу", callback_data="quick:save"),
                InlineKeyboardButton(text="Не нужно", callback_data="quick:ignore"),
            ]
        ]
    )


def ai_task_menu_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Разбить на шаги", callback_data=f"ai:breakdown:{task_id}"),
                InlineKeyboardButton(text="Помочь начать", callback_data=f"ai:start_step:{task_id}"),
            ],
            [
                InlineKeyboardButton(text="План 15", callback_data=f"ai:plan15:{task_id}"),
                InlineKeyboardButton(text="План 30", callback_data=f"ai:plan30:{task_id}"),
                InlineKeyboardButton(text="План 60", callback_data=f"ai:plan60:{task_id}"),
            ],
            [
                InlineKeyboardButton(text="Спросить совет", callback_data=f"ai:ask:{task_id}"),
                InlineKeyboardButton(text="Вернуть фокус", callback_data=f"ai:focus:{task_id}"),
            ],
            [
                InlineKeyboardButton(text="Ради чего", callback_data=f"ai:why:{task_id}"),
                InlineKeyboardButton(text="Пинок", callback_data=f"ai:kick:{task_id}"),
            ],
            [
                InlineKeyboardButton(text="Стиль ответа", callback_data=f"ai:tone_menu:{task_id}"),
                InlineKeyboardButton(text="Буст-трек", callback_data=f"ai:boost:{task_id}"),
            ],
        ]
    )


def tone_mode_keyboard(task_id: int | None = None) -> InlineKeyboardMarkup:
    suffix = str(task_id) if task_id is not None else "0"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мягко", callback_data=f"ai:tone:supportive:{suffix}"),
                InlineKeyboardButton(text="Нейтрально", callback_data=f"ai:tone:balanced:{suffix}"),
            ],
            [
                InlineKeyboardButton(text="Жестче", callback_data=f"ai:tone:tough:{suffix}"),
                InlineKeyboardButton(text="Bro", callback_data=f"ai:tone:bro:{suffix}"),
            ],
        ]
    )
