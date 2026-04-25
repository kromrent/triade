from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from config import Settings
from handlers.text_utils import matches_user_text
from keyboards.inline import quick_add_keyboard, task_actions_keyboard, task_list_keyboard
from keyboards.reply import (
    interval_keyboard,
    main_menu_keyboard,
    priority_keyboard,
    skip_keyboard,
    time_keyboard,
)
from models import AIScenario, Priority, ToneMode, utc_now
from services.motivation_service import MotivationService
from scheduler import ReminderScheduler
from services.formatting import (
    format_active_reminders,
    format_task_already_exists,
    format_task_created,
    format_task_details,
    format_task_list,
)
from services.parser import (
    TimeParseError,
    parse_interval_minutes,
    parse_natural_reminder,
    parse_time_input,
)
from services.task_service import InvalidTaskTransitionError, TaskNotFoundError, TaskService

router = Router(name=__name__)


class AddTask(StatesGroup):
    title = State()
    description = State()
    reminder_time = State()
    interval = State()
    priority = State()


class QuickTask(StatesGroup):
    confirm = State()


@router.message(Command("add"))
@router.message(lambda message: matches_user_text(message.text, "Добавить задачу"))
async def start_add_task(message: Message, state: FSMContext, task_service: TaskService) -> None:
    if message.from_user:
        task_service.ensure_user(message.from_user, message.chat.id)
    await state.clear()
    await state.set_state(AddTask.title)
    await message.answer("Как назвать задачу?", reply_markup=ReplyKeyboardRemove())


@router.message(AddTask.title)
async def add_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title or len(title) < 2:
        await message.answer("Название слишком короткое. Напиши понятное название задачи.")
        return

    await state.update_data(title=title[:255])
    await state.set_state(AddTask.description)
    await message.answer("Добавь описание или нажми «Пропустить».", reply_markup=skip_keyboard())


@router.message(AddTask.description)
async def add_description(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    description = None if text.lower() in {"пропустить", "-", "нет"} else text[:1000]
    await state.update_data(description=description)
    await state.set_state(AddTask.reminder_time)
    await message.answer(
        "Когда напомнить первый раз? Примеры: «сейчас», «через 20 минут», «15:30».",
        reply_markup=time_keyboard(),
    )


@router.message(AddTask.reminder_time)
async def add_time(message: Message, state: FSMContext, settings: Settings) -> None:
    try:
        reminder_at = parse_time_input(message.text or "", utc_now(), settings.tzinfo)
    except TimeParseError as exc:
        await message.answer(f"{exc}. Напиши «сейчас», «через 20 минут» или «15:30».")
        return

    await state.update_data(reminder_at=reminder_at)
    await state.set_state(AddTask.interval)
    await message.answer(
        f"Как часто повторять напоминание о старте? По умолчанию {settings.default_repeat_minutes} минут.",
        reply_markup=interval_keyboard(),
    )


@router.message(AddTask.interval)
async def add_interval(message: Message, state: FSMContext, settings: Settings) -> None:
    try:
        interval = parse_interval_minutes(message.text or "", settings.default_repeat_minutes)
    except TimeParseError as exc:
        await message.answer(f"{exc}. Напиши число минут, например 5 или 10.")
        return

    await state.update_data(interval=interval)
    await state.set_state(AddTask.priority)
    await message.answer("Выбери приоритет: низкий, средний или высокий.", reply_markup=priority_keyboard())


@router.message(AddTask.priority)
async def add_priority(
    message: Message,
    state: FSMContext,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    settings: Settings,
) -> None:
    priority = _parse_priority(message.text or "")
    if priority is None:
        await message.answer("Выбери один из вариантов: низкий, средний или высокий.")
        return

    data = await state.get_data()
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя.")
        await state.clear()
        return

    task_service.ensure_user(message.from_user, message.chat.id)
    creation = task_service.create_task(
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
        title=data["title"],
        description=data.get("description"),
        start_reminder_at=data["reminder_at"],
        repeat_every_minutes=data["interval"],
        priority=priority,
    )
    reminder_scheduler.schedule_reminder(creation.reminder)
    await state.clear()
    await message.answer(
        (
            format_task_created(creation.task, settings.timezone)
            if creation.created_new
            else format_task_already_exists(creation.task, settings.timezone)
        ),
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("tasks"))
@router.message(lambda message: matches_user_text(message.text, "Мои задачи"))
async def show_tasks(message: Message, task_service: TaskService, settings: Settings) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)
    tasks = task_service.list_tasks(message.from_user.id)
    await message.answer(
        format_task_list(
            tasks,
            settings.timezone,
            title="Активные задачи",
            empty_text="Активных задач нет. Завершенные и отмененные задачи лежат в /history.",
        ),
        reply_markup=task_list_keyboard(tasks, show_history_button=True) if tasks else main_menu_keyboard(),
    )


@router.message(Command("history"))
@router.message(lambda message: matches_user_text(message.text, "История задач"))
async def show_task_history(message: Message, task_service: TaskService, settings: Settings) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)
    tasks = task_service.list_task_history(message.from_user.id)
    await message.answer(
        format_task_list(
            tasks,
            settings.timezone,
            title="История задач",
            empty_text="История задач пока пустая.",
        ),
        reply_markup=task_list_keyboard(tasks, show_active_button=True) if tasks else main_menu_keyboard(),
    )


@router.message(Command("active"))
@router.message(lambda message: matches_user_text(message.text, "Активные напоминания"))
async def show_active_reminders(message: Message, task_service: TaskService, settings: Settings) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)
    items = task_service.list_active_reminders(message.from_user.id)
    await message.answer(format_active_reminders(items, settings.timezone), reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "task:list")
async def show_tasks_callback(callback: CallbackQuery, task_service: TaskService, settings: Settings) -> None:
    tasks = task_service.list_tasks(callback.from_user.id)
    await _safe_edit(
        callback,
        format_task_list(
            tasks,
            settings.timezone,
            title="Активные задачи",
            empty_text="Активных задач нет. Завершенные и отмененные задачи лежат в /history.",
        ),
        task_list_keyboard(tasks, show_history_button=True),
    )
    await callback.answer()


@router.callback_query(F.data == "task:history")
async def show_task_history_callback(callback: CallbackQuery, task_service: TaskService, settings: Settings) -> None:
    tasks = task_service.list_task_history(callback.from_user.id)
    await _safe_edit(
        callback,
        format_task_list(
            tasks,
            settings.timezone,
            title="История задач",
            empty_text="История задач пока пустая.",
        ),
        task_list_keyboard(tasks, show_active_button=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:open:"))
async def open_task(callback: CallbackQuery, task_service: TaskService, settings: Settings) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Не удалось открыть задачу.", show_alert=True)
        return

    try:
        task = task_service.get_task_for_user(task_id, callback.from_user.id)
    except TaskNotFoundError:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    await _safe_edit(callback, format_task_details(task, settings.timezone), task_actions_keyboard(task))
    await callback.answer()


@router.callback_query(F.data.startswith("task:start:"))
async def start_task_callback(
    callback: CallbackQuery,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    settings: Settings,
    motivation_service: MotivationService,
) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Некорректная задача.", show_alert=True)
        return

    try:
        task, reminder = task_service.start_task(task_id, callback.from_user.id)
    except (TaskNotFoundError, InvalidTaskTransitionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    reminder_scheduler.schedule_reminder(reminder)
    focus_reminder = task_service.plan_focus_nudge(task.id)
    if focus_reminder:
        reminder_scheduler.schedule_reminder(focus_reminder)
    await _safe_edit(callback, format_task_details(task, settings.timezone), task_actions_keyboard(task))
    if callback.message and motivation_service.should_send_proactive(callback.from_user.id, AIScenario.FOCUS, 30):
        text = await motivation_service.compose(callback.from_user.id, AIScenario.FOCUS, task=task)
        await callback.message.answer(escape(text))
    await callback.answer("Старт зафиксирован.")


@router.callback_query(F.data.startswith("task:snooze:"))
async def snooze_task_callback(
    callback: CallbackQuery,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    settings: Settings,
    motivation_service: MotivationService,
) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Некорректная задача.", show_alert=True)
        return

    try:
        task, reminder = task_service.snooze_task(task_id, callback.from_user.id)
    except (TaskNotFoundError, InvalidTaskTransitionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    reminder_scheduler.schedule_reminder(reminder)
    await _safe_edit(callback, format_task_details(task, settings.timezone), task_actions_keyboard(task))
    if (
        callback.message
        and task_service.database.count_task_events(task.id, "task_snoozed") >= 3
        and motivation_service.should_send_proactive(callback.from_user.id)
    ):
        text = await motivation_service.compose(
            callback.from_user.id,
            AIScenario.PROCRASTINATION,
            task=task,
            tone_override=ToneMode.TOUGH,
        )
        await callback.message.answer(escape(text))
    await callback.answer("Отложено на 10 минут.")


@router.callback_query(F.data.startswith("task:done:"))
async def complete_task_callback(
    callback: CallbackQuery,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    settings: Settings,
    motivation_service: MotivationService,
) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Некорректная задача.", show_alert=True)
        return

    try:
        task, next_reminder = task_service.complete_task(task_id, callback.from_user.id)
    except TaskNotFoundError:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    except InvalidTaskTransitionError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if next_reminder:
        reminder_scheduler.schedule_reminder(next_reminder)
    await _safe_edit(callback, format_task_details(task, settings.timezone), task_actions_keyboard(task))
    if callback.message and motivation_service.should_send_proactive(callback.from_user.id):
        text = await motivation_service.compose(callback.from_user.id, AIScenario.COMPLETION, task=task)
        await callback.message.answer(escape(text))
    if next_reminder:
        await callback.answer("Next recurring task is already scheduled.")
        return
    if next_reminder:
        await callback.answer("Р—Р°РґР°С‡Р° Р·Р°РІРµСЂС€РµРЅР°; СЃР»РµРґСѓСЋС‰РµРµ РїРѕРІС‚РѕСЂРµРЅРёРµ СѓР¶Рµ Р·Р°РїР»Р°РЅРёСЂРѕРІР°РЅРѕ.")
        return
    await callback.answer("Задача завершена.")


@router.callback_query(F.data.startswith("task:cancel:"))
async def cancel_task_callback(callback: CallbackQuery, task_service: TaskService, settings: Settings) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Некорректная задача.", show_alert=True)
        return

    try:
        task, _ = task_service.cancel_task(task_id, callback.from_user.id)
    except TaskNotFoundError:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    except InvalidTaskTransitionError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await _safe_edit(callback, format_task_details(task, settings.timezone), task_actions_keyboard(task))
    await callback.answer("Задача отменена.")


@router.callback_query(F.data.startswith("check:continue:"))
async def continue_checkin_callback(
    callback: CallbackQuery,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Некорректная задача.", show_alert=True)
        return

    try:
        _, reminder = task_service.continue_checkin(task_id, callback.from_user.id)
    except (TaskNotFoundError, InvalidTaskTransitionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    reminder_scheduler.schedule_reminder(reminder)
    await _safe_edit(callback, "Продолжаю. Следующая проверка будет позже.", None)
    await callback.answer("Вернусь с проверкой позже.")


@router.callback_query(F.data.startswith("check:almost:"))
async def almost_done_callback(
    callback: CallbackQuery,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Некорректная задача.", show_alert=True)
        return

    try:
        _, reminder = task_service.almost_done_checkin(task_id, callback.from_user.id)
    except (TaskNotFoundError, InvalidTaskTransitionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    reminder_scheduler.schedule_reminder(reminder)
    await _safe_edit(callback, "Почти готово. Проверю еще раз через 10 минут.", None)
    await callback.answer("Хорошо, проверю через 10 минут.")


@router.callback_query(F.data.startswith("check:help:"))
async def help_checkin_callback(
    callback: CallbackQuery,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    motivation_service: MotivationService,
) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Некорректная задача.", show_alert=True)
        return

    try:
        _, reminder = task_service.need_help_checkin(task_id, callback.from_user.id)
    except (TaskNotFoundError, InvalidTaskTransitionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    reminder_scheduler.schedule_reminder(reminder)
    if callback.message:
        text = await motivation_service.compose(callback.from_user.id, AIScenario.HELP_TASK, task=task_service.get_task_for_user(task_id, callback.from_user.id))
        await callback.message.answer(escape(text))
    await _safe_edit(callback, "Подсказка отправлена. Проверю еще раз через 10 минут.", None)
    await callback.answer("Подсказка отправлена.")


@router.callback_query(F.data == "quick:save")
async def save_quick_task(
    callback: CallbackQuery,
    state: FSMContext,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    settings: Settings,
) -> None:
    data = await state.get_data()
    title = data.get("quick_title")
    if not title:
        await callback.answer("Черновик не найден.", show_alert=True)
        await state.clear()
        return
    if callback.message is None:
        await callback.answer("Не удалось определить чат.", show_alert=True)
        await state.clear()
        return

    task_service.ensure_user(callback.from_user, callback.message.chat.id)
    creation = task_service.create_task(
        telegram_user_id=callback.from_user.id,
        chat_id=callback.message.chat.id,
        title=title,
        description=None,
        start_reminder_at=utc_now(),
        repeat_every_minutes=settings.default_repeat_minutes,
        priority=Priority.MEDIUM,
    )
    reminder_scheduler.schedule_reminder(creation.reminder)
    await state.clear()
    await _safe_edit(
        callback,
        (
            format_task_created(creation.task, settings.timezone)
            if creation.created_new
            else format_task_already_exists(creation.task, settings.timezone)
        ),
        None,
    )
    await callback.answer("Задача сохранена." if creation.created_new else "Такая задача уже есть.")


@router.callback_query(F.data == "quick:ignore")
async def ignore_quick_task(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(callback, "Не сохраняю.", None)
    await callback.answer()


@router.message(QuickTask.confirm)
async def replace_quick_task(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Напиши текст задачи или нажми кнопку под предыдущим сообщением.")
        return
    await state.update_data(quick_title=text[:255])
    await message.answer("Сохранить это как задачу с напоминанием сейчас?", reply_markup=quick_add_keyboard())


# Freeform text is handled by handlers.ai; keep legacy quick-add callbacks only for old messages.
@router.message(F.text, lambda _message: False)
async def plain_text_task(
    message: Message,
    state: FSMContext,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    settings: Settings,
) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return
    if message.from_user is None:
        return

    task_service.ensure_user(message.from_user, message.chat.id)
    parsed = parse_natural_reminder(text, utc_now(), settings.tzinfo)
    if parsed:
        creation = task_service.create_task(
            telegram_user_id=message.from_user.id,
            chat_id=message.chat.id,
            title=parsed.title,
            description=None,
            start_reminder_at=parsed.remind_at,
            repeat_every_minutes=settings.default_repeat_minutes,
            priority=Priority.MEDIUM,
        )
        reminder_scheduler.schedule_reminder(creation.reminder)
        await state.clear()
        await message.answer(
            (
                format_task_created(creation.task, settings.timezone)
                if creation.created_new
                else format_task_already_exists(creation.task, settings.timezone)
            ),
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.set_state(QuickTask.confirm)
    await state.update_data(quick_title=text[:255])
    await message.answer("Похоже, это задача. Сохранить с напоминанием сейчас?", reply_markup=quick_add_keyboard())


def _parse_priority(text: str) -> Priority | None:
    normalized = text.strip().lower()
    if normalized in {"низкий", "low"}:
        return Priority.LOW
    if normalized in {"средний", "medium", "нормальный"}:
        return Priority.MEDIUM
    if normalized in {"высокий", "high"}:
        return Priority.HIGH
    return None


def _extract_id(data: str | None) -> int | None:
    if not data:
        return None
    try:
        return int(data.rsplit(":", maxsplit=1)[1])
    except (ValueError, IndexError):
        return None


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)
