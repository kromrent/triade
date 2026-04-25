from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from keyboards.inline import ai_task_menu_keyboard, task_list_keyboard, tone_mode_keyboard
from handlers.text_utils import is_main_menu_text
from keyboards.reply import confirm_yes_no_keyboard, main_menu_keyboard
from models import AIScenario, MotivationalTrack, Priority, RecurrenceKind, TaskStatus, ToneMode, utc_now
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

    comment_update = _parse_task_comment_request(text)
    if comment_update is not None:
        drafts = _deserialize_task_drafts(data.get("task_drafts", []), user_context_service)
        updated = _apply_description_to_drafts(drafts, comment_update[0], comment_update[1])
        if updated:
            await state.update_data(task_drafts=_serialize_task_drafts(drafts))
            await message.answer(
                escape(_format_task_draft_confirmation(drafts, user_context_service)),
                reply_markup=confirm_yes_no_keyboard(),
            )
            return
        await message.answer(
            "Не нашел такую задачу в черновике. Напиши точное название из списка или ответь «да», если добавлять без комментария.",
            reply_markup=confirm_yes_no_keyboard(),
        )
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
        use_ai_classifier=user_context_service.is_ai_enabled(message.from_user.id),
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
    and not is_main_menu_text(message.text)
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
    comment_update = _parse_task_comment_request(message_text)
    if comment_update is not None:
        await _handle_task_comment_update(message, comment_update, task_service)
        return

    conversation_context = _build_classifier_context(
        user_context_service.database.list_recent_ai_messages(message.from_user.id, limit=6)
    )
    if _asks_for_motivation_media(message_text, conversation_context):
        track = motivation_service.tracks_service.random_focus_file(message.from_user.id)
        await _send_direct_media_boost(message, track)
        _log_local_media_interaction(task_service, message.from_user.id, message_text, track)
        _schedule_energy_followup(reminder_scheduler, message.from_user.id, message.chat.id, track, None)
        return

    intent = await intent_service.detect_smart(
        message_text,
        utc_now(),
        user_context_service.settings.tzinfo,
        motivation_service.ai_service,
        conversation_context=conversation_context,
        use_ai_classifier=user_context_service.is_ai_enabled(message.from_user.id),
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
    task = None if general_support_mode or intent.type in {IntentType.GENERAL_CHAT, IntentType.UNKNOWN} else user_context_service.latest_active_task(message.from_user.id)
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
            if _asks_for_motivation_media(message_text, conversation_context):
                track = motivation_service.tracks_service.random_focus_file(message.from_user.id)
                await _send_direct_media_boost(message, track)
                _log_local_media_interaction(task_service, message.from_user.id, message_text, track)
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
            reply_markup=confirm_yes_no_keyboard(),
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
    existing = []
    skipped = []

    for draft in creations[:10]:
        recurrence_kind = getattr(draft, "recurrence_kind", RecurrenceKind.NONE)
        resolved_remind_at = _resolve_draft_reminder_at(
            draft.remind_at,
            recurrence_kind,
            local_now,
            user_context_service.settings.tzinfo,
        )
        if resolved_remind_at is None:
            reminder_local = draft.remind_at.astimezone(user_context_service.settings.tzinfo)
            skipped.append((draft.title, reminder_local))
            continue

        creation = task_service.create_task(
            telegram_user_id=message.from_user.id,
            chat_id=message.chat.id,
            title=draft.title,
            description=getattr(draft, "description", None),
            start_reminder_at=resolved_remind_at,
            repeat_every_minutes=draft.repeat_every_minutes
            or user_context_service.settings.default_repeat_minutes,
            priority=Priority.MEDIUM,
            recurrence_kind=recurrence_kind,
        )
        reminder_scheduler.schedule_reminder(creation.reminder)
        if creation.created_new:
            created.append(creation.task)
        else:
            existing.append(creation.task)

    if not created and not existing:
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

    if len(created) == 1 and not existing:
        response = _format_created_task_response(created[0], user_context_service.settings.timezone)
    elif len(existing) == 1 and not created:
        response = _format_existing_task_response(existing[0], user_context_service.settings.timezone)
    else:
        lines = []
        if created:
            lines.append(f"Новых задач поставил: {len(created)}.")
            for task in created:
                reminder_time = task.start_reminder_at.astimezone(user_context_service.settings.tzinfo).strftime("%d.%m %H:%M")
                lines.append(f"+ #{task.id} {task.title} — {reminder_time}")
        if existing:
            if lines:
                lines.append("")
            lines.append(f"Такие задачи уже были: {len(existing)}.")
            for task in existing:
                reminder_time = task.start_reminder_at.astimezone(user_context_service.settings.tzinfo).strftime("%d.%m %H:%M")
                lines.append(f"= #{task.id} {task.title} — {reminder_time}")
        response = "\n".join(lines)

    if skipped:
        response += f"\n\nНе поставил из-за прошедшего времени: {len(skipped)}."

    await message.answer(escape(response), reply_markup=main_menu_keyboard())


def _format_created_task_response(task, timezone_name: str) -> str:
    reminder_time = task.start_reminder_at.astimezone(ZoneInfo(timezone_name)).strftime("%d.%m %H:%M")
    recurrence = _format_recurrence_label(task.recurrence_kind)

    lines = [
        f"Окей, поставил: {task.title}",
        f"Первое напоминание: {reminder_time}",
    ]
    if recurrence:
        lines.append(f"Тип: {recurrence}")
        lines.append("Следующее повторение создается автоматически.")
    else:
        lines.append(f"Повтор: каждые {task.repeat_every_minutes} мин.")
        lines.append("Если не стартуешь, буду дожимать напоминаниями.")
    if task.description:
        lines.append(f"Комментарий: {task.description}")
    return "\n".join(lines)


def _format_existing_task_response(task, timezone_name: str) -> str:
    reminder_time = task.start_reminder_at.astimezone(ZoneInfo(timezone_name)).strftime("%d.%m %H:%M")
    recurrence = _format_recurrence_label(task.recurrence_kind)

    lines = [
        "Такая задача уже есть, новую не добавляю.",
        f"Задача: {task.title}",
        f"Первое напоминание: {reminder_time}",
    ]
    if recurrence:
        lines.append(f"Тип: {recurrence}")
    else:
        lines.append(f"Повтор: каждые {task.repeat_every_minutes} мин.")
    if task.description:
        lines.append(f"Комментарий: {task.description}")
    return "\n".join(lines)


def _resolve_draft_reminder_at(
    remind_at: datetime,
    recurrence_kind: RecurrenceKind,
    local_now: datetime,
    timezone: ZoneInfo,
) -> datetime | None:
    reminder_local = remind_at.astimezone(timezone)
    threshold = local_now - timedelta(seconds=30)
    if reminder_local >= threshold:
        return reminder_local
    if recurrence_kind == RecurrenceKind.DAILY:
        while reminder_local < threshold:
            reminder_local += timedelta(days=1)
        return reminder_local
    if recurrence_kind == RecurrenceKind.WEEKLY:
        while reminder_local < threshold:
            reminder_local += timedelta(days=7)
        return reminder_local
    return None


def _task_drafts_from_intent(intent) -> list[SimpleNamespace]:
    creations = list(intent.task_creations)
    if not creations and intent.task_title and intent.reminder_at:
        creations = [
            SimpleNamespace(
                title=intent.task_title,
                remind_at=intent.reminder_at,
                repeat_every_minutes=intent.repeat_every_minutes,
                description=getattr(intent, "description", None),
                recurrence_kind=getattr(intent, "recurrence_kind", RecurrenceKind.NONE),
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
                "description": getattr(draft, "description", None),
                "recurrence_kind": getattr(draft, "recurrence_kind", RecurrenceKind.NONE).value,
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
        recurrence_kind = _parse_recurrence_kind_value(item.get("recurrence_kind"))

        drafts.append(
            SimpleNamespace(
                title=title,
                remind_at=remind_at,
                repeat_every_minutes=repeat_every_minutes,
                description=_clean_optional_text(item.get("description")),
                recurrence_kind=recurrence_kind,
            )
        )
    return drafts


def _format_task_draft_confirmation(creations, user_context_service: UserContextService) -> str:
    timezone = user_context_service.settings.tzinfo
    lines = ["Я понял так:"]
    for index, draft in enumerate(creations[:10], start=1):
        reminder_time = draft.remind_at.astimezone(timezone).strftime("%d.%m %H:%M")
        repeat = draft.repeat_every_minutes or user_context_service.settings.default_repeat_minutes
        recurrence = _format_recurrence_label(getattr(draft, "recurrence_kind", RecurrenceKind.NONE))
        lines.append(f"{index}. {draft.title} — {reminder_time}, повтор каждые {repeat} мин.")
        if recurrence:
            lines.append(f"   Тип: {recurrence}")
        if getattr(draft, "description", None):
            lines.append(f"   Комментарий: {draft.description}")
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

    message_text = message.text or ""

    if action == TaskAction.CANCEL_ALL:
        cancelled, recurring_reminders = task_service.cancel_all_active_tasks(message.from_user.id)
        for reminder in recurring_reminders:
            reminder_scheduler.schedule_reminder(reminder)
        if cancelled == 0:
            await message.answer("Активных задач для отмены нет.", reply_markup=main_menu_keyboard())
        else:
            await message.answer(f"Окей, отменил активные задачи: {cancelled}.", reply_markup=main_menu_keyboard())
        return

    tasks = task_service.list_tasks(message.from_user.id, limit=100)
    if not tasks:
        await message.answer(
            "Не вижу активной задачи. Напиши ее прямо сюда: «надо сделать ...»",
            reply_markup=main_menu_keyboard(),
        )
        return

    task, resolution_error = _resolve_management_task(
        message_text=message_text,
        action=action,
        tasks=tasks,
        timezone=user_context_service.settings.tzinfo,
    )
    if resolution_error:
        await message.answer(resolution_error, reply_markup=main_menu_keyboard())
        return
    if task is None:
        await message.answer(_clarify_management_task_message(action), reply_markup=main_menu_keyboard())
        return

    try:
        if action == TaskAction.START:
            updated, reminder = task_service.start_task(task.id, message.from_user.id)
            reminder_scheduler.schedule_reminder(reminder)
            focus_reminder = task_service.plan_focus_nudge(updated.id)
            if focus_reminder:
                reminder_scheduler.schedule_reminder(focus_reminder)
            text = await motivation_service.compose(message.from_user.id, AIScenario.FOCUS, task=updated)
            await message.answer(
                escape(f"Старт зафиксировал: «{updated.title}».\n\n{text}"),
                reply_markup=main_menu_keyboard(),
            )
            return

        if action == TaskAction.DONE:
            updated, next_reminder = task_service.complete_task(task.id, message.from_user.id)
            if next_reminder:
                reminder_scheduler.schedule_reminder(next_reminder)
            await message.answer(
                escape(
                    _format_completed_task_response(
                        updated,
                        next_reminder,
                        user_context_service.settings.timezone,
                    )
                ),
                reply_markup=main_menu_keyboard(),
            )
            return

        if action == TaskAction.SNOOZE:
            postpone_minutes = minutes or user_context_service.settings.default_snooze_minutes
            updated, reminder = task_service.postpone_task(task.id, message.from_user.id, postpone_minutes)
            reminder_scheduler.schedule_reminder(reminder)
            response = f"Окей, перенес «{updated.title}» на {postpone_minutes} мин."
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
            await message.answer(
                escape(f"Окей, отменил задачу «{task.title}»."),
                reply_markup=main_menu_keyboard(),
            )
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


async def _handle_task_comment_update(
    message: Message,
    comment_update: tuple[str, str],
    task_service: TaskService,
) -> None:
    if message.from_user is None:
        return

    target, description = comment_update
    tasks = task_service.list_tasks(message.from_user.id, limit=100)
    task = _find_task_by_title(tasks, target)
    if task is None:
        await message.answer(
            escape(f"Не нашел активную задачу «{target}». Напиши «мои задачи» и выбери точное название."),
            reply_markup=main_menu_keyboard(),
        )
        return

    try:
        updated = task_service.update_description(task.id, message.from_user.id, description)
    except InvalidTaskTransitionError:
        await message.answer("К закрытой задаче комментарий уже не добавляю.", reply_markup=main_menu_keyboard())
        return

    await message.answer(
        escape(f"Окей, добавил комментарий к задаче «{updated.title}»:\n{updated.description}"),
        reply_markup=main_menu_keyboard(),
    )


def _parse_task_comment_request(text: str) -> tuple[str, str] | None:
    cleaned = " ".join(text.strip().split())
    patterns = [
        r"(?i)^(?:добавь|добавить|запиши|сохрани|поставь)\s+(?:комментарий|коммент|описание|заметку)\s+(?:к|для)\s+(?:задач[еи]\s+)?(.+?)[,;:]?\s+(?:что|чтобы|:|-)\s+(.+)$",
        r"(?i)^(?:к|для)\s+(?:задач[еи]\s+)?(.+?)\s+(?:комментарий|коммент|описание|заметка)[,;:]?\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned)
        if not match:
            continue
        target = match.group(1).strip(" .,!?:;—-")
        description = match.group(2).strip(" .,!?:;—-")
        if target and description:
            return target[:255], description[:1000]
    return None


def _apply_description_to_drafts(drafts: list[SimpleNamespace], target: str, description: str) -> bool:
    draft = _find_task_by_title(drafts, target)
    if draft is None:
        return False
    draft.description = description[:1000]
    return True


def _find_task_by_title(items, target: str):
    normalized_target = _normalize_match_text(target)
    exact = [item for item in items if _normalize_match_text(item.title) == normalized_target]
    if exact:
        return exact[0]

    partial = [
        item
        for item in items
        if normalized_target in _normalize_match_text(item.title)
        or _normalize_match_text(item.title) in normalized_target
    ]
    return partial[0] if len(partial) == 1 else None


def _resolve_management_task(
    message_text: str,
    action: TaskAction,
    tasks,
    timezone: ZoneInfo,
):
    task_id = _extract_task_reference_id(message_text)
    if task_id is not None:
        for task in tasks:
            if task.id == task_id:
                return task, None
        return None, f"Не нашел активную задачу #{task_id}. Напиши «Мои задачи», покажу актуальные номера."

    title_matches = _find_tasks_mentioned_in_text(tasks, message_text)
    schedule_matches = _filter_tasks_by_schedule_hint(tasks, message_text, timezone)

    if title_matches:
        schedule_title_matches = _filter_tasks_by_schedule_hint(title_matches, message_text, timezone)
        if len(schedule_title_matches) == 1:
            return schedule_title_matches[0], None
        if len(title_matches) == 1:
            return title_matches[0], None
        return None, _clarify_management_task_message(action)

    if schedule_matches:
        if len(schedule_matches) == 1:
            return schedule_matches[0], None
        return None, _clarify_management_task_message(action)

    fallback = _default_management_task(tasks, action)
    return fallback, None


def _find_tasks_mentioned_in_text(tasks, message_text: str):
    normalized_message = f" {_normalize_match_text(message_text)} "
    matched = []
    for task in tasks:
        normalized_title = _normalize_match_text(task.title)
        if not normalized_title:
            continue
        if f" {normalized_title} " in normalized_message:
            matched.append(task)
            continue

        title_tokens = [token for token in normalized_title.split() if len(token) >= 3]
        if len(title_tokens) >= 2 and all(f" {token} " in normalized_message for token in title_tokens):
            matched.append(task)
    return matched


def _filter_tasks_by_schedule_hint(tasks, message_text: str, timezone: ZoneInfo):
    schedule_hint = _extract_management_schedule_hint(message_text, timezone)
    if schedule_hint is None:
        return []

    target_date, hour, minute = schedule_hint
    matched = []
    for task in tasks:
        local_start = task.start_reminder_at.astimezone(timezone)
        if hour is not None and minute is not None and (local_start.hour != hour or local_start.minute != minute):
            continue
        if target_date is not None and local_start.date() != target_date:
            continue
        matched.append(task)
    return matched


def _extract_management_schedule_hint(message_text: str, timezone: ZoneInfo):
    normalized = " ".join(message_text.strip().lower().replace("ё", "е").split())
    if not normalized:
        return None

    hour = minute = None
    time_patterns = (
        r"\bв\s+(\d{1,2})[:.](\d{2})\b",
        r"\bв\s+(\d{1,2})\s+(\d{2})\b",
        r"\b(\d{1,2})[:.](\d{2})\b",
    )
    for pattern in time_patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        hour = int(match.group(1))
        minute = int(match.group(2))
        break

    if hour is None or minute is None:
        meridiem_match = re.search(r"\bв\s+(\d{1,2})\s*(утра|дня|вечера|ночи)\b", normalized)
        if meridiem_match:
            hour = int(meridiem_match.group(1))
            minute = 0
            meridiem = meridiem_match.group(2)
            if meridiem in {"дня", "вечера"} and 1 <= hour <= 11:
                hour += 12
            elif meridiem == "ночи" and hour == 12:
                hour = 0

    if hour is not None and not 0 <= hour <= 23:
        return None
    if minute is not None and not 0 <= minute <= 59:
        return None

    local_now = utc_now().astimezone(timezone)
    target_date = None
    if "сегодня" in normalized:
        target_date = local_now.date()
    elif "завтра" in normalized:
        target_date = local_now.date() + timedelta(days=1)
    else:
        date_match = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b", normalized)
        if date_match:
            day = int(date_match.group(1))
            month = int(date_match.group(2))
            year = int(date_match.group(3) or local_now.year)
            try:
                target_date = datetime(year, month, day, tzinfo=timezone).date()
            except ValueError:
                target_date = None

    if target_date is None and hour is None and minute is None:
        return None
    return target_date, hour, minute


def _default_management_task(tasks, action: TaskAction):
    if len(tasks) == 1:
        return tasks[0]

    if action == TaskAction.DONE:
        in_progress = [task for task in tasks if task.status == TaskStatus.IN_PROGRESS]
        if len(in_progress) == 1:
            return in_progress[0]
        return None

    if action in {TaskAction.START, TaskAction.SNOOZE}:
        actionable = [task for task in tasks if task.status in {TaskStatus.PENDING, TaskStatus.NUDGING, TaskStatus.SNOOZED}]
        return actionable[0] if len(actionable) == 1 else None

    if action == TaskAction.CANCEL:
        cancellable = [task for task in tasks if task.status not in {TaskStatus.DONE, TaskStatus.CANCELLED}]
        return cancellable[0] if len(cancellable) == 1 else None

    return None


def _clarify_management_task_message(action: TaskAction) -> str:
    action_text = {
        TaskAction.START: "запустить",
        TaskAction.DONE: "отметить выполненной",
        TaskAction.SNOOZE: "перенести",
        TaskAction.CANCEL: "отменить",
    }.get(action, "изменить")
    return (
        f"Понял действие, но не понял, какую именно задачу нужно {action_text}. "
        "Напиши название, время или номер из «Мои задачи»."
    )


def _format_completed_task_response(task, next_reminder, timezone_name: str) -> str:
    lines = [f"Готово, отметил выполненной: {task.title}."]
    if task.recurrence_kind != RecurrenceKind.NONE:
        if next_reminder is not None:
            next_time = next_reminder.scheduled_at.astimezone(ZoneInfo(timezone_name)).strftime("%d.%m %H:%M")
            lines.append(f"Следующее повторение: {next_time}.")
        else:
            lines.append("Следующее повторение уже стоит в расписании.")
    return "\n".join(lines)


def _normalize_match_text(value: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", " ", value.lower().replace("ё", "е")).strip()


def _clean_optional_text(value) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    return cleaned[:1000] if cleaned else None


def _parse_recurrence_kind_value(value) -> RecurrenceKind:
    try:
        return RecurrenceKind(str(value).strip().lower())
    except ValueError:
        return RecurrenceKind.NONE


def _format_recurrence_label(recurrence_kind: RecurrenceKind) -> str | None:
    if recurrence_kind == RecurrenceKind.DAILY:
        return "Ежедневная"
    if recurrence_kind == RecurrenceKind.WEEKLY:
        return "Еженедельная"
    return None


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


def _log_local_media_interaction(
    task_service: TaskService,
    telegram_user_id: int,
    user_message: str,
    track: MotivationalTrack | None,
) -> None:
    response = (
        "Отправил мотивационный файл из media/boosts/focus."
        if track is not None and track.file_path
        else "Пользователь попросил мотивационный файл, но локальный файл не найден."
    )
    task_service.database.add_ai_interaction(
        telegram_user_id=telegram_user_id,
        task_id=None,
        scenario=AIScenario.BOOST.value,
        tone_mode=ToneMode.BRO.value,
        provider="local:media",
        user_message=user_message,
        prompt=None,
        response=response,
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


def _asks_for_motivation_media(text: str, conversation_context: str | None = None) -> bool:
    normalized = " ".join(text.strip().lower().split())
    action_words = [
        "кинь",
        "скинь",
        "дай",
        "давай",
        "пришли",
        "отправь",
        "можешь кинуть",
        "можешь скинуть",
    ]
    media_words = ["трек", "видос", "видосик", "видео", "ролик", "буст"]
    repeat_words = [
        "еще",
        "ещё",
        "еще один",
        "ещё один",
        "еще раз",
        "ещё раз",
        "другой",
        "другую",
        "следующий",
        "следующее",
        "новый",
        "новое",
    ]
    has_media = any(word in normalized for word in media_words)
    has_action = any(word in normalized for word in action_words)
    has_repeat = any(word in normalized for word in repeat_words)
    if has_media and (has_action or has_repeat):
        return True
    return has_repeat and _recent_context_mentions_media(conversation_context)


def _recent_context_mentions_media(conversation_context: str | None) -> bool:
    if not conversation_context:
        return False
    normalized = conversation_context.lower()
    return any(
        phrase in normalized
        for phrase in [
            "мотивационный файл",
            "трек для мотивации",
            "кинь видос",
            "скинь видос",
            "видосик",
            "видео",
            "ролик",
            "local:media",
        ]
    )


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
