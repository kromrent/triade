from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Добавить задачу"), KeyboardButton(text="Мои задачи")],
            [KeyboardButton(text="Активные напоминания"), KeyboardButton(text="История задач")],
            [KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
    )


def confirm_yes_no_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Да"), KeyboardButton(text="Нет")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def skip_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def time_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Сейчас")],
            [KeyboardButton(text="Через 10 минут"), KeyboardButton(text="Через 30 минут")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def interval_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="По умолчанию")],
            [KeyboardButton(text="5"), KeyboardButton(text="10"), KeyboardButton(text="15")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def priority_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Средний")],
            [KeyboardButton(text="Низкий"), KeyboardButton(text="Высокий")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
