from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from types import SimpleNamespace

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from keyboards.inline import ai_task_menu_keyboard, task_list_keyboard, tone_mode_keyboard
from keyboards.reply import main_menu_keyboard
from models import AIScenario, MotivationalTrack, Priority, TaskStatus, ToneMode, utc_now
from scheduler import ReminderScheduler
from services.formatting import format_task_list
from services.intent_service import IntentService, IntentType, TaskAction, TaskQuery
from services.motivation_service import MotivationService
from services.task_service import InvalidTaskTransitionError, TaskNotFoundError, TaskService
from services.tracks_service import TracksService, parse_track_draft
from services.user_context_service import UserContextService

router = Router(name=__name__)


class AskAI(StatesGroup):
    question = State()


class ConfirmTaskDraft(StatesGroup):
    waiting = State()


@router.message(Command("help_task"))
async def help_task_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    motivation_service: MotivationService,
) -> None:
    await _reply_for_command(message, command, task_service, motivation_service, AIScenario.HELP_TASK)


@router.message(Command("motivate"))
async def motivate_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    motivation_service: MotivationService,
) -> None:
    await _reply_for_command(message, command, task_service, motivation_service, AIScenario.ENCOURAGEMENT)


@router.message(Command("focus_me"))
async def focus_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    motivation_service: MotivationService,
) -> None:
    await _reply_for_command(message, command, task_service, motivation_service, AIScenario.FOCUS)


@router.message(Command("why"))
async def why_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    motivation_service: MotivationService,
) -> None:
    await _reply_for_command(message, command, task_service, motivation_service, AIScenario.WHY)


@router.message(Command("start_step"))
async def start_step_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    motivation_service: MotivationService,
) -> None:
    await _reply_for_command(message, command, task_service, motivation_service, AIScenario.START_STEP)


@router.message(Command("panic"))
async def panic_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    motivation_service: MotivationService,
) -> None:
    await _reply_for_command(message, command, task_service, motivation_service, AIScenario.PANIC)


@router.message(Command("boost"))
async def boost_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    motivation_service: MotivationService,
    user_context_service: UserContextService,
) -> None:
    if message.from_user is None:
        return

    task_service.ensure_user(message.from_user, message.chat.id)
    task = _task_from_command(command, message.from_user.id, task_service)
    if task is None:
        task = user_context_service.latest_active_task(message.from_user.id)

    text, track = await motivation_service.compose_boost(message.from_user.id, task)
    await _send_boost(message, text, track, main_menu_keyboard())
    _schedule_energy_followup(reminder_scheduler, message.from_user.id, message.chat.id, track, task)


@router.message(Command("tone"))
async def tone_command(message: Message, command: CommandObject, task_service: TaskService) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)

    args = (command.args or "").strip().lower()
    if not args:
        await message.answer("Выбери стиль AI-ответов.", reply_markup=tone_mode_keyboard())
        return

    tone = _parse_tone(args)
    if tone is None:
        await message.answer("Доступные режимы: supportive, balanced, tough, bro.")
        return

    task_service.database.set_user_tone_mode(message.from_user.id, tone)
    await message.answer(f"Ок, стиль AI: {tone.value}.", reply_markup=main_menu_keyboard())


@router.message(Command("ai_on"))
async def ai_on_command(message: Message, task_service: TaskService) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)
    task_service.database.set_user_ai_enabled(message.from_user.id, True)
    await message.answer("AI-помощь включена для кнопок, команд и мягких триггеров.", reply_markup=main_menu_keyboard())


@router.message(Command("ai_off"))
async def ai_off_command(message: Message, task_service: TaskService) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)
    task_service.database.set_user_ai_enabled(message.from_user.id, False)
    await message.answer(
        "AI-помощь выключена для автотриггеров. Базовые напоминания продолжат работать.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("goal"))
async def goal_command(message: Message, command: CommandObject, task_service: TaskService) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)

    args = (command.args or "").strip()
    if args.lower().startswith("add "):
        text = args[4:].strip()
        if len(text) < 3:
            await message.answer("Напиши так: /goal add закончить диплом")
            return
        task_service.database.add_user_goal(message.from_user.id, text[:500])
        await message.answer("Цель сохранена. Я буду учитывать ее в AI-сообщениях.")
        return

    await _send_goals(message, task_service)


@router.message(Command("goals"))
async def goals_command(message: Message, task_service: TaskService) -> None:
    await _send_goals(message, task_service)


@router.message(Command("phrase"))
async def phrase_command(message: Message, command: CommandObject, task_service: TaskService) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)

    args = (command.args or "").strip()
    if args.lower().startswith("add "):
        text = args[4:].strip()
        if len(text) < 3:
            await message.answer("Напиши так: /phrase add Я делаю это ради нормальной жизни")
            return
        task_service.database.add_motivation_entry(message.from_user.id, text[:500])
        await message.answer("Фраза сохранена.")
        return

    entries = task_service.database.list_motivation_entries(message.from_user.id)
    if not entries:
        await message.answer("Фраз пока нет. Добавь: /phrase add Я делаю это ради нормальной жизни")
        return
    lines = ["Твои мотивационные фразы:"]
    lines.extend(f"#{entry.id} {entry.text}" for entry in entries[:20])
    await message.answer(escape("\n".join(lines)))


@router.message(Command("track"))
async def track_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    tracks_service: TracksService,
) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)

    args = (command.args or "").strip()
    if args.lower() == "scan":
        added = tracks_service.scan_local_files(message.from_user.id)
        if not added:
            await message.answer(
                "Новых локальных бустов не нашел. Закинь файлы в media/boosts/focus.",
                reply_markup=main_menu_keyboard(),
            )
            return
        lines = [f"Добавил локальных бустов: {len(added)}."]
        lines.extend(f"#{track.id} [{track.category.value}] {track.title}" for track in added[:20])
        await message.answer(escape("\n".join(lines)), reply_markup=main_menu_keyboard())
        return

    if args.lower().startswith("add "):
        draft = parse_track_draft(args[4:])
        if draft is None:
            await message.answer(
                "Формат: /track add focus Название | media/boosts/focus/file.mp4\n"
                "Или: /track add focus Название | https://link"
            )
            return
        track = tracks_service.add_track(message.from_user.id, draft)
        await message.answer(f"Буст сохранен: {escape(track.title)}")
        return

    await _send_tracks(message, tracks_service)


@router.message(Command("tracks"))
async def tracks_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    tracks_service: TracksService,
) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)
    args = (command.args or "").strip().lower()
    if args == "scan":
        added = tracks_service.scan_local_files(message.from_user.id)
        if not added:
            await message.answer(
                "Новых локальных бустов не нашел. Закинь файлы в media/boosts/focus.",
                reply_markup=main_menu_keyboard(),
            )
            return
        lines = [f"Добавил локальных бустов: {len(added)}."]
        lines.extend(f"#{track.id} [{track.category.value}] {track.title}" for track in added[:20])
        await message.answer(escape("\n".join(lines)), reply_markup=main_menu_keyboard())
        return

    await _send_tracks(message, tracks_service)


@router.callback_query(F.data.startswith("ai:menu:"))
async def ai_menu_callback(callback: CallbackQuery, task_service: TaskService) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Не удалось открыть AI-меню.", show_alert=True)
        return
    try:
        task = task_service.get_task_for_user(task_id, callback.from_user.id)
    except TaskNotFoundError:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    await _safe_edit(callback, f"AI-помощь для задачи:\n{escape(task.title)}", ai_task_menu_keyboard(task.id))
    await callback.answer()


@router.callback_query(F.data.startswith("ai:tone_menu:"))
async def ai_tone_menu_callback(callback: CallbackQuery) -> None:
    task_id = _extract_id(callback.data)
    await _safe_edit(callback, "Выбери стиль AI-ответов.", tone_mode_keyboard(task_id))
    await callback.answer()


@router.callback_query(F.data.startswith("ai:tone:"))
async def ai_set_tone_callback(callback: CallbackQuery, task_service: TaskService) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 4:
        await callback.answer("Не удалось выбрать стиль.", show_alert=True)
        return

    tone = _parse_tone(parts[2])
    if tone is None:
        await callback.answer("Неизвестный стиль.", show_alert=True)
        return

    task_service.database.set_user_tone_mode(callback.from_user.id, tone)
    await _safe_edit(callback, f"Ок, стиль AI: {tone.value}.", None)
    await callback.answer()


@router.callback_query(F.data.startswith("ai:ask:"))
async def ai_ask_callback(callback: CallbackQuery, state: FSMContext, task_service: TaskService) -> None:
    task_id = _extract_id(callback.data)
    if task_id is None:
        await callback.answer("Некорректная задача.", show_alert=True)
        return
    try:
        task_service.get_task_for_user(task_id, callback.from_user.id)
    except TaskNotFoundError:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    await state.set_state(AskAI.question)
    await state.update_data(ai_task_id=task_id)
    if callback.message:
        await callback.message.answer("Напиши вопрос по задаче. Отвечу коротко и по делу.")
    await callback.answer()


@router.message(AskAI.question)
async def ai_answer_question(
    message: Message,
    state: FSMContext,
    task_service: TaskService,
    motivation_service: MotivationService,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    task_id = int(data.get("ai_task_id", 0))
    try:
        task = task_service.get_task_for_user(task_id, message.from_user.id)
    except TaskNotFoundError:
        await message.answer("Задача уже не найдена.")
        await state.clear()
        return

    text = await motivation_service.compose(
        message.from_user.id,
        AIScenario.ADVICE,
        task=task,
        user_message=message.text or "",
    )
    await state.clear()
    await message.answer(escape(text), reply_markup=main_menu_keyboard())


@router.message(ConfirmTaskDraft.waiting)
async def confirm_task_draft(
    message: Message,
    state: FSMContext,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    motivation_service: MotivationService,
    user_context_service: UserContextService,
    intent_service: IntentService,
) -> None:
    if message.from_user is None:
        return

    text = (message.text or "").strip()
    normalized = text.lower()
    data = await state.get_data()

    normalized_short = normalized.strip(" .,!?:;")

    if normalized_short in {
        "да",
        "давай",
        "ок",
        "окей",
        "ага",
        "подтверждаю",
        "верно",
        "создай",
        "добавь",
    } or normalized_short.startswith(("да ", "ок ", "окей ")):
        drafts = _deserialize_task_drafts(data.get("task_drafts", []), user_context_service)
        await state.clear()
        if not drafts:
            await message.answer(
                "Черновик потерялся. Напиши задачу еще раз одной фразой, я соберу заново.",
                reply_markup=main_menu_keyboard(),
            )
            return
        await _create_task_drafts(
            message,
            drafts,
            task_service,
            reminder_scheduler,
            user_context_service,
        )
        return

    if normalized_short in {"нет", "не", "отмена", "отмени", "не надо", "стоп"}:
        await state.clear()
        await message.answer("Окей, не добавляю.", reply_markup=main_menu_keyboard())
        return

    original_text = str(data.get("original_text") or "")
    combined_text = (
        f"Исходный запрос пользователя: {original_text}\n"
        f"Исправление пользователя: {text}\n"
        "Собери обновленный JSON-план создания задачи."
    )
    intent = await intent_service.detect_smart(
        combined_text,
        utc_now(),
        user_context_service.settings.tzinfo,
        motivation_service.ai_service,
    )
    await state.clear()

    if intent.type != IntentType.CREATE_TASK:
        await message.answer(
            "Не смог собрать обновленный черновик. Напиши одной фразой: что добавить и когда напомнить.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await _handle_create_task_intent(
        message,
        intent,
        task_service,
        reminder_scheduler,
        user_context_service,
        state,
        force_confirmation=True,
    )


@router.callback_query(F.data.startswith("ai:"))
async def ai_action_callback(
    callback: CallbackQuery,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    motivation_service: MotivationService,
) -> None:
    action, task_id = _parse_ai_action(callback.data)
    if action is None or task_id is None:
        await callback.answer("Не понял AI-действие.", show_alert=True)
        return

    try:
        task = task_service.get_task_for_user(task_id, callback.from_user.id)
    except TaskNotFoundError:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    scenario, plan_minutes, tone_override = _scenario_for_action(action)
    if scenario is None:
        await callback.answer("Пока не умею это действие.", show_alert=True)
        return

    if scenario == AIScenario.BOOST:
        text, track = await motivation_service.compose_boost(callback.from_user.id, task)
        if callback.message:
            await _send_boost(callback.message, text, track, ai_task_menu_keyboard(task.id))
            _schedule_energy_followup(
                reminder_scheduler,
                callback.from_user.id,
                callback.message.chat.id,
                track,
                task,
            )
        await callback.answer()
        return
    else:
        result = await motivation_service.compose(
            callback.from_user.id,
            scenario,
            task=task,
            plan_minutes=plan_minutes,
            tone_override=tone_override,
        )

    if callback.message:
        await callback.message.answer(escape(result), reply_markup=ai_task_menu_keyboard(task.id))
    await callback.answer()


@router.message(
    StateFilter(None),
    lambda message: bool(message.text)
    and not message.text.startswith("/")
    and message.text.strip().lower()
    not in {"добавить задачу", "мои задачи", "активные напоминания", "история задач", "помощь"}
)
async def freeform_intent_router(
    message: Message,
    state: FSMContext,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    motivation_service: MotivationService,
    user_context_service: UserContextService,
    intent_service: IntentService,
) -> None:
    if message.from_user is None:
        return

    task_service.ensure_user(message.from_user, message.chat.id)
    message_text = message.text or ""
    if _asks_for_motivation_media(message_text):
        track = motivation_service.tracks_service.random_focus_file(message.from_user.id)
        await _send_direct_media_boost(message, track)
        _schedule_energy_followup(reminder_scheduler, message.from_user.id, message.chat.id, track, None)
        return

    conversation_context = _build_classifier_context(
        user_context_service.database.list_recent_ai_messages(message.from_user.id, limit=6)
    )
    intent = await intent_service.detect_smart(
        message_text,
        utc_now(),
        user_context_service.settings.tzinfo,
        motivation_service.ai_service,
        conversation_context=conversation_context,
    )

    if intent.type == IntentType.CREATE_TASK:
        await _handle_create_task_intent(
            message,
            intent,
            task_service,
            reminder_scheduler,
            user_context_service,
            state,
            force_confirmation=True,
        )
        return

    if intent.type == IntentType.TASK_MANAGEMENT and intent.action:
        await _handle_task_management_intent(
            message,
            intent.action,
            intent.minutes,
            task_service,
            reminder_scheduler,
            motivation_service,
            user_context_service,
        )
        return

    if intent.type == IntentType.TASK_QUERY and intent.query:
        await _handle_task_query_intent(
            message,
            intent.query,
            task_service,
            user_context_service.settings.timezone,
        )
        return

    scenario = intent.scenario or AIScenario.HELP_TASK
    general_support_mode = intent.type in {IntentType.PROCRASTINATION, IntentType.EMOTIONAL_STATE} or (
        intent.type == IntentType.MOTIVATION
        and scenario in {AIScenario.BOOST, AIScenario.PROCRASTINATION, AIScenario.COMEBACK, AIScenario.PANIC}
    )
    task = None if general_support_mode else user_context_service.latest_active_task(message.from_user.id)
    if _asks_about_task_list(message_text):
        task = None
    else:
        task_id = _extract_task_reference_id(message_text)
        if task_id is not None:
            try:
                task = task_service.get_task_for_user(task_id, message.from_user.id)
            except TaskNotFoundError:
                await message.answer(
                    f"Не нашел задачу #{task_id}. Напиши «мои задачи», покажу актуальные номера.",
                    reply_markup=main_menu_keyboard(),
                )
                return

    if intent.type == IntentType.GENERAL_CHAT:
        text = await motivation_service.compose(
            message.from_user.id,
            AIScenario.GENERAL_CHAT,
            task=task,
            user_message=message.text,
        )
        await message.answer(escape(text), reply_markup=main_menu_keyboard())
        return

    if intent.type in {IntentType.PROCRASTINATION, IntentType.EMOTIONAL_STATE, IntentType.TASK_HELP, IntentType.MOTIVATION}:
        if scenario == AIScenario.BOOST:
            if _asks_for_motivation_media(message_text):
                track = motivation_service.tracks_service.random_focus_file(message.from_user.id)
                await _send_direct_media_boost(message, track)
                _schedule_energy_followup(reminder_scheduler, message.from_user.id, message.chat.id, track, task)
                return
            text = await motivation_service.compose(
                message.from_user.id,
                scenario,
                task=task,
                user_message=message.text,
            )
            await message.answer(escape(text), reply_markup=main_menu_keyboard())
            return

        text = await motivation_service.compose(
            message.from_user.id,
            scenario,
            task=task,
            user_message=message.text,
        )
        if _should_attach_focus_track(intent.type, scenario):
            track = motivation_service.tracks_service.random_focus_file(message.from_user.id)
            await _send_boost(message, text, track, main_menu_keyboard())
            _schedule_energy_followup(reminder_scheduler, message.from_user.id, message.chat.id, track, task)
            return
        await message.answer(escape(text), reply_markup=main_menu_keyboard())
        return

    text = await motivation_service.compose(
        message.from_user.id,
        AIScenario.GENERAL_CHAT,
        task=task,
        user_message=message.text,
    )
    await message.answer(escape(text), reply_markup=main_menu_keyboard())


async def _handle_create_task_intent(
    message: Message,
    intent,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    user_context_service: UserContextService,
    state: FSMContext,
    force_confirmation: bool = False,
) -> None:
    if message.from_user is None:
        return

    creations = _task_drafts_from_intent(intent)

    if not creations:
        await message.answer(
            "Я вижу запрос на задачу, но не смог собрать нормальный черновик. Напиши чуть конкретнее: что сделать и когда напомнить.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if force_confirmation or intent.confidence < user_context_service.settings.openai_create_task_confidence_threshold:
        await state.set_state(ConfirmTaskDraft.waiting)
        await state.update_data(
            original_text=intent.user_message or message.text or "",
            task_drafts=_serialize_task_drafts(creations),
        )
        await message.answer(
            escape(_format_task_draft_confirmation(creations, user_context_service)),
            reply_markup=main_menu_keyboard(),
        )
        return

    await _create_task_drafts(
        message,
        creations,
        task_service,
        reminder_scheduler,
        user_context_service,
    )


async def _create_task_drafts(
    message: Message,
    creations,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    user_context_service: UserContextService,
) -> None:
    if message.from_user is None:
        return

    local_now = utc_now().astimezone(user_context_service.settings.tzinfo)
    created = []
    skipped = []

    for draft in creations[:10]:
        reminder_local = draft.remind_at.astimezone(user_context_service.settings.tzinfo)
        if reminder_local < local_now - timedelta(seconds=30):
            skipped.append((draft.title, reminder_local))
            continue

        task, reminder = task_service.create_task(
            telegram_user_id=message.from_user.id,
            chat_id=message.chat.id,
            title=draft.title,
            description=None,
            start_reminder_at=draft.remind_at,
            repeat_every_minutes=draft.repeat_every_minutes
            or user_context_service.settings.default_repeat_minutes,
            priority=Priority.MEDIUM,
        )
        reminder_scheduler.schedule_reminder(reminder)
        created.append(task)

    if not created:
        skipped_text = "\n".join(
            f"- {title}: {reminder_at.strftime('%d.%m %H:%M')}" for title, reminder_at in skipped[:5]
        )
        await message.answer(
            escape(
                "Не поставил задачу: указанное время уже прошло.\n"
                f"Сейчас: {local_now.strftime('%H:%M')}.\n\n"
                f"{skipped_text}\n\n"
                "Напиши время позже текущего, например: «через 10 минут» или «завтра в 09:00»."
            ),
            reply_markup=main_menu_keyboard(),
        )
        return

    if len(created) == 1:
        task = created[0]
        reminder_time = task.start_reminder_at.astimezone(user_context_service.settings.tzinfo).strftime("%d.%m %H:%M")
        response = (
            f"Окей, поставил: {task.title}\n"
            f"Первое напоминание: {reminder_time}\n"
            f"Повтор: каждые {task.repeat_every_minutes} мин.\n"
            "Если не стартуешь, буду дожимать напоминаниями."
        )
    else:
        lines = [f"Окей, поставил задач: {len(created)}."]
        for task in created:
            reminder_time = task.start_reminder_at.astimezone(user_context_service.settings.tzinfo).strftime("%d.%m %H:%M")
            lines.append(f"#{task.id} {task.title} — {reminder_time}")
        response = "\n".join(lines)

    if skipped:
        response += f"\n\nНе поставил из-за прошедшего времени: {len(skipped)}."

    await message.answer(escape(response), reply_markup=main_menu_keyboard())


def _task_drafts_from_intent(intent) -> list[SimpleNamespace]:
    creations = list(intent.task_creations)
    if not creations and intent.task_title and intent.reminder_at:
        creations = [
            SimpleNamespace(
                title=intent.task_title,
                remind_at=intent.reminder_at,
                repeat_every_minutes=intent.repeat_every_minutes,
            )
        ]
    return creations


def _serialize_task_drafts(creations) -> list[dict[str, object]]:
    result = []
    for draft in creations[:10]:
        result.append(
            {
                "title": str(draft.title),
                "remind_at": draft.remind_at.isoformat(),
                "repeat_every_minutes": draft.repeat_every_minutes,
            }
        )
    return result


def _deserialize_task_drafts(raw_drafts, user_context_service: UserContextService) -> list[SimpleNamespace]:
    if not isinstance(raw_drafts, list):
        return []

    drafts: list[SimpleNamespace] = []
    timezone = user_context_service.settings.tzinfo
    for item in raw_drafts[:10]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        remind_at_raw = str(item.get("remind_at") or "").strip()
        if not title or not remind_at_raw:
            continue
        try:
            remind_at = datetime.fromisoformat(remind_at_raw)
        except ValueError:
            continue
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=timezone)
        repeat_every_minutes = item.get("repeat_every_minutes")
        try:
            repeat_every_minutes = int(repeat_every_minutes) if repeat_every_minutes else None
        except (TypeError, ValueError):
            repeat_every_minutes = None

        drafts.append(
            SimpleNamespace(
                title=title,
                remind_at=remind_at,
                repeat_every_minutes=repeat_every_minutes,
            )
        )
    return drafts


def _format_task_draft_confirmation(creations, user_context_service: UserContextService) -> str:
    timezone = user_context_service.settings.tzinfo
    lines = ["Я понял так:"]
    for index, draft in enumerate(creations[:10], start=1):
        reminder_time = draft.remind_at.astimezone(timezone).strftime("%d.%m %H:%M")
        repeat = draft.repeat_every_minutes or user_context_service.settings.default_repeat_minutes
        lines.append(f"{index}. {draft.title} — {reminder_time}, повтор каждые {repeat} мин.")
    lines.append("")
    lines.append("Добавить? Ответь «да», «нет» или напиши исправление.")
    return "\n".join(lines)


async def _reply_for_command(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    motivation_service: MotivationService,
    scenario: AIScenario,
) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)
    task = _task_from_command(command, message.from_user.id, task_service)
    if task is None:
        task = task_service.database.get_latest_active_task_for_user(message.from_user.id)
    text = await motivation_service.compose(message.from_user.id, scenario, task=task)
    await message.answer(escape(text), reply_markup=main_menu_keyboard())


async def _handle_task_query_intent(
    message: Message,
    query: TaskQuery,
    task_service: TaskService,
    timezone_name: str,
) -> None:
    if message.from_user is None:
        return

    if query == TaskQuery.COUNT_ACTIVE:
        tasks = task_service.list_tasks(message.from_user.id, limit=1000)
        await message.answer(_format_active_task_count(tasks), reply_markup=main_menu_keyboard())
        return

    if query == TaskQuery.LIST_ACTIVE:
        tasks = task_service.list_tasks(message.from_user.id)
        await message.answer(
            format_task_list(
                tasks,
                timezone_name,
                title="Активные задачи",
                empty_text="Активных задач нет.",
            ),
            reply_markup=task_list_keyboard(tasks, show_history_button=True) if tasks else main_menu_keyboard(),
        )
        return

    history = task_service.list_task_history(message.from_user.id, limit=1000)
    closed = [task for task in history if task.status in {TaskStatus.DONE, TaskStatus.CANCELLED}]

    if query == TaskQuery.COUNT_CLOSED:
        done = sum(1 for task in closed if task.status == TaskStatus.DONE)
        cancelled = sum(1 for task in closed if task.status == TaskStatus.CANCELLED)
        await message.answer(
            f"Закрытых задач: {len(closed)}.\nЗавершено: {done}.\nОтменено: {cancelled}.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await message.answer(
        format_task_list(
            closed[:20],
            timezone_name,
            title="История задач",
            empty_text="История задач пока пустая.",
        ),
        reply_markup=task_list_keyboard(closed[:20], show_active_button=True) if closed else main_menu_keyboard(),
    )


async def _handle_task_management_intent(
    message: Message,
    action: TaskAction,
    minutes: int | None,
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    motivation_service: MotivationService,
    user_context_service: UserContextService,
) -> None:
    if message.from_user is None:
        return

    if action == TaskAction.CANCEL_ALL:
        cancelled = task_service.cancel_all_active_tasks(message.from_user.id)
        if cancelled == 0:
            await message.answer("Активных задач для отмены нет.", reply_markup=main_menu_keyboard())
        else:
            await message.answer(f"Окей, отменил активные задачи: {cancelled}.", reply_markup=main_menu_keyboard())
        return

    task = user_context_service.latest_active_task(message.from_user.id)
    if task is None:
        await message.answer(
            "Не вижу активной задачи. Напиши ее прямо сюда: «надо сделать ...»",
            reply_markup=main_menu_keyboard(),
        )
        return

    try:
        if action == TaskAction.START:
            updated, reminder = task_service.start_task(task.id, message.from_user.id)
            reminder_scheduler.schedule_reminder(reminder)
            focus_reminder = task_service.plan_focus_nudge(updated.id)
            if focus_reminder:
                reminder_scheduler.schedule_reminder(focus_reminder)
            text = await motivation_service.compose(message.from_user.id, AIScenario.FOCUS, task=updated)
            await message.answer(escape(f"Старт зафиксировал.\n\n{text}"), reply_markup=main_menu_keyboard())
            return

        if action == TaskAction.DONE:
            updated = task_service.complete_task(task.id, message.from_user.id)
            text = await motivation_service.compose(message.from_user.id, AIScenario.COMPLETION, task=updated)
            await message.answer(escape(f"Готово, закрыл задачу.\n\n{text}"), reply_markup=main_menu_keyboard())
            return

        if action == TaskAction.SNOOZE:
            postpone_minutes = minutes or user_context_service.settings.default_snooze_minutes
            updated, reminder = task_service.postpone_task(task.id, message.from_user.id, postpone_minutes)
            reminder_scheduler.schedule_reminder(reminder)
            response = f"Окей, перенес на {postpone_minutes} мин."
            if task_service.database.count_task_events(updated.id, "task_snoozed") >= 3:
                push = await motivation_service.compose(
                    message.from_user.id,
                    AIScenario.PROCRASTINATION,
                    task=updated,
                    tone_override=ToneMode.TOUGH,
                )
                response = f"{response}\n\n{push}"
            await message.answer(escape(response), reply_markup=main_menu_keyboard())
            return

        if action == TaskAction.CANCEL:
            task_service.cancel_task(task.id, message.from_user.id)
            await message.answer("Окей, отменил задачу.", reply_markup=main_menu_keyboard())
            return
    except InvalidTaskTransitionError:
        await message.answer(
            "Эту задачу так уже не двинуть. Следующий честный шаг: либо «готово», либо 5 минут продолжить.",
            reply_markup=main_menu_keyboard(),
        )


def _format_active_task_count(tasks) -> str:
    if not tasks:
        return "Активных задач нет."

    lines = [f"Активных задач: {len(tasks)}."]
    preview = tasks[:5]
    if preview:
        lines.append("")
        lines.extend(f"#{task.id} {escape(task.title)}" for task in preview)
    if len(tasks) > len(preview):
        lines.append(f"...и еще {len(tasks) - len(preview)}.")
    return "\n".join(lines)


def _build_classifier_context(recent_dialog: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    for user_text, assistant_text in recent_dialog[-6:]:
        if user_text and not _is_internal_ai_prompt(user_text):
            lines.append(f"Пользователь: {_clip_context_text(user_text)}")
        if assistant_text:
            lines.append(f"Ассистент: {_clip_context_text(assistant_text)}")
    return "\n".join(lines)


def _clip_context_text(value: str) -> str:
    return " ".join(value.strip().split())[:350]


def _is_internal_ai_prompt(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith(
        (
            "прошла минута после мотивационного трека",
            "пользователь уже получил первое напоминание",
        )
    )


def _asks_about_task_list(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not any(word in normalized for word in ["задач", "дела", "дело", "напоминан"]):
        return False
    return any(
        phrase in normalized
        for phrase in [
            "мои задачи",
            "моих задач",
            "мои дела",
            "другие задачи",
            "других задач",
            "остальные задачи",
            "остальных задач",
            "все задачи",
            "все мои задачи",
        ]
    )


def _extract_task_reference_id(text: str) -> int | None:
    normalized = " ".join(text.strip().lower().split())
    patterns = [
        r"#\s*(\d{1,8})\b",
        r"\bзадач[аеуыию]?\s*#?\s*(\d{1,8})\b",
        r"\b(\d{1,8})\s*(?:-?ю|-?ую|-?я|-?ая)?\s*задач[аеуыию]?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def _task_from_command(command: CommandObject, telegram_user_id: int, task_service: TaskService):
    args = (command.args or "").strip()
    if not args:
        return None
    try:
        task_id = int(args.split()[0])
    except ValueError:
        return None
    try:
        return task_service.get_task_for_user(task_id, telegram_user_id)
    except TaskNotFoundError:
        return None


def _clarifying_next_step(task) -> str:
    if task is None:
        return (
            "Я не буду гадать вслепую. Сожми это до результата в одну строку, например: "
            "«надо сделать диплом» или «напомни через 20 минут проект».\n\n"
            "Если это сопротивление, напиши прямо: «не могу начать» — и я соберу тебя в первый шаг."
        )

    return (
        f"Понял не до конца, но активная задача у нас есть: «{task.title}».\n\n"
        "Следующий шаг: открой материалы по ней и напиши «открыл» или «готово». "
        "Если надо перенести — напиши «перенеси на 30 минут»."
    )


async def _send_goals(message: Message, task_service: TaskService) -> None:
    if message.from_user is None:
        return
    task_service.ensure_user(message.from_user, message.chat.id)
    goals = task_service.database.list_user_goals(message.from_user.id)
    if not goals:
        await message.answer("Целей пока нет. Добавь: /goal add закончить диплом")
        return
    lines = ["Твои цели:"]
    lines.extend(f"#{goal.id} {goal.text}" for goal in goals[:20])
    await message.answer(escape("\n".join(lines)))


async def _send_tracks(message: Message, tracks_service: TracksService) -> None:
    if message.from_user is None:
        return
    tracks = tracks_service.list_tracks(message.from_user.id)
    if not tracks:
        await message.answer(
            "Бустов пока нет.\n\n"
            "Локально: закинь mp4/mp3 в media/boosts/focus и напиши /tracks scan.\n"
            "Вручную: /track add focus Название | media/boosts/focus/file.mp4"
        )
        return
    lines = ["Твои бусты:"]
    lines.extend(_format_track_row(track) for track in tracks[:20])
    await message.answer(escape("\n".join(lines)))


async def _send_boost(message: Message, text: str, track: MotivationalTrack | None, reply_markup) -> None:
    await message.answer(escape(_with_track(text, track)), reply_markup=reply_markup)
    if track is None or not track.file_path:
        return

    await _send_track_file(message, track)


async def _send_direct_media_boost(message: Message, track: MotivationalTrack | None) -> None:
    await message.answer("Да, брат, конечно.\n\nЛови:", reply_markup=main_menu_keyboard())
    if track is None or not track.file_path:
        await message.answer("Пока не нашел файл в media/boosts/focus.")
        return
    await _send_track_file(message, track)


async def _send_track_file(message: Message, track: MotivationalTrack) -> None:
    path = Path(track.file_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists() or not path.is_file():
        await message.answer(f"Файл буста не найден: {escape(track.title)}")
        return

    file = FSInputFile(path)
    suffix = path.suffix.lower()
    caption = "Трек для мотивации"
    try:
        if suffix in {".mp3", ".wav", ".m4a", ".ogg", ".flac"}:
            await message.answer_audio(file, caption=escape(caption))
        elif suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
            await message.answer_video(file, caption=escape(caption), supports_streaming=True)
        else:
            await message.answer_document(file, caption=escape(caption))
    except TelegramBadRequest:
        await message.answer_document(file, caption=escape(caption))


def _format_track_row(track: MotivationalTrack) -> str:
    source = track.file_path or track.url or track.description or ""
    return f"#{track.id} [{track.category.value}] {track.title} {source}".strip()


def _should_attach_focus_track(intent_type: IntentType, scenario: AIScenario) -> bool:
    return False


def _schedule_energy_followup(
    reminder_scheduler: ReminderScheduler,
    telegram_user_id: int,
    chat_id: int,
    track: MotivationalTrack | None,
    task,
) -> None:
    if track is None or not track.file_path:
        return
    reminder_scheduler.schedule_energy_followup(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        task_id=task.id if task is not None else None,
        delay_seconds=60,
    )


def _asks_for_motivation_media(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    action_words = ["кинь", "скинь", "дай", "пришли", "отправь", "можешь кинуть", "можешь скинуть"]
    media_words = ["трек", "видос", "видосик", "видео", "ролик", "буст"]
    return any(word in normalized for word in action_words) and any(word in normalized for word in media_words)


def _parse_ai_action(data: str | None) -> tuple[str | None, int | None]:
    if not data:
        return None, None
    parts = data.split(":")
    if len(parts) != 3:
        return None, None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None, None


def _scenario_for_action(action: str) -> tuple[AIScenario | None, int | None, ToneMode | None]:
    mapping = {
        "breakdown": (AIScenario.BREAKDOWN, None, None),
        "start_step": (AIScenario.START_STEP, None, None),
        "focus": (AIScenario.FOCUS, None, None),
        "why": (AIScenario.WHY, None, None),
        "kick": (AIScenario.PROCRASTINATION, None, ToneMode.TOUGH),
        "boost": (AIScenario.BOOST, None, None),
        "plan15": (AIScenario.PLAN, 15, None),
        "plan30": (AIScenario.PLAN, 30, None),
        "plan60": (AIScenario.PLAN, 60, None),
    }
    return mapping.get(action, (None, None, None))


def _extract_id(data: str | None) -> int | None:
    if not data:
        return None
    try:
        return int(data.rsplit(":", maxsplit=1)[1])
    except (ValueError, IndexError):
        return None


def _parse_tone(value: str) -> ToneMode | None:
    try:
        return ToneMode(value.strip().lower())
    except ValueError:
        return None


def _with_track(text: str, track: MotivationalTrack | None) -> str:
    if not track:
        return text
    if track.file_path:
        return f"{text}\n\nДержи трек для мотивации."
    details = track.url or track.description
    track_text = f"{track.title}\n{details}" if details else track.title
    return f"{text}\n\nДержи трек для мотивации:\n{track_text}"


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)
