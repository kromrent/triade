from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import ErrorEvent, Message

from keyboards.reply import main_menu_keyboard
from scheduler import ReminderScheduler
from services.task_service import TaskService

router = Router(name=__name__)
logger = logging.getLogger(__name__)


START_TEXT = (
    "Я помогаю не откладывать задачи. Основной режим — просто пиши как в обычном чате.\n\n"
    "Примеры:\n"
    "надо сделать диплом\n"
    "Напомни через 20 минут доделать диплом\n\n"
    "Еще можно писать: «не могу начать», «дай пинок», «я начал», «готово», "
    "«перенеси на 30 минут». Я сам пойму действие."
)

HELP_TEXT = (
    "<b>Как пользоваться</b>\n\n"
    "Главное: просто пиши обычным текстом, я сам определю действие.\n"
    "Создать задачу: «надо сделать диплом» или «напомни через 20 минут позвонить».\n"
    "Обычный вопрос: «сколько сейчас времени» или «объясни коротко, что такое фокус-блок?».\n"
    "Управлять задачей: «я начал», «готово», «перенеси на 30 минут».\n"
    "Анти-прокрастинация: «не могу начать», «сливаюсь», «очень много всего».\n"
    "Быстрый ввод: «Напомни через 20 минут позвонить» или «Напомни в 15:30 отчет».\n"
    "Активные задачи: /tasks или «Мои задачи».\n"
    "История завершенных и отмененных задач: /history или «История задач».\n"
    "Активные напоминания: /active или «Активные напоминания».\n"
    "Отчет за день: /daily_report.\n"
    "AI-помощь: /help_task, /motivate, /focus_me, /why, /boost, /start_step, /panic.\n"
    "Цели: /goal add текст. Треки: /track add start Название | ссылка.\n"
    "Отменить текущий ввод: /cancel."
)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, task_service: TaskService) -> None:
    await state.clear()
    if message.from_user:
        task_service.ensure_user(message.from_user, message.chat.id)
    await message.answer(START_TEXT, reply_markup=main_menu_keyboard())


@router.message(Command("help"))
@router.message(lambda message: message.text == "Помощь")
async def help_command(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


@router.message(Command("cancel"))
async def cancel_dialog(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменен.", reply_markup=main_menu_keyboard())


@router.message(Command("daily_report"))
async def daily_report_command(
    message: Message,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)
    sent = await reminder_scheduler.send_daily_report(
        message.from_user.id,
        message.chat.id,
        force=True,
    )
    if not sent:
        await message.answer("Отчет пока недоступен.", reply_markup=main_menu_keyboard())


@router.errors()
async def errors_handler(event: ErrorEvent) -> bool:
    logger.exception("Unhandled update error", exc_info=event.exception)
    return True
