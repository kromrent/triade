from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from models import AIScenario, RecurrenceKind
from services.parser import ParsedReminder, TimeParseError, parse_natural_reminder, parse_time_input


class IntentType(str, Enum):
    CREATE_TASK = "create_task"
    GENERAL_CHAT = "general_chat"
    TASK_QUERY = "task_query"
    PROCRASTINATION = "procrastination"
    TASK_HELP = "task_help"
    TASK_MANAGEMENT = "task_management"
    EMOTIONAL_STATE = "emotional_state"
    MOTIVATION = "motivation"
    UNKNOWN = "unknown"


class TaskAction(str, Enum):
    START = "start"
    DONE = "done"
    SNOOZE = "snooze"
    CANCEL = "cancel"
    CANCEL_ALL = "cancel_all"


class TaskQuery(str, Enum):
    COUNT_ACTIVE = "count_active"
    LIST_ACTIVE = "list_active"
    COUNT_CLOSED = "count_closed"
    LIST_HISTORY = "list_history"


@dataclass(frozen=True, slots=True)
class Intent:
    type: IntentType
    confidence: float
    scenario: AIScenario | None = None
    task_title: str | None = None
    reminder_at: datetime | None = None
    recurrence_kind: RecurrenceKind | None = None
    action: TaskAction | None = None
    query: TaskQuery | None = None
    minutes: int | None = None
    repeat_every_minutes: int | None = None
    task_creations: tuple[ParsedTaskCreation, ...] = ()
    user_message: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedTaskCreation:
    title: str
    remind_at: datetime
    recurrence_kind: RecurrenceKind = RecurrenceKind.NONE
    repeat_every_minutes: int | None = None
    description: str | None = None


DAILY_RECURRENCE_RE = (
    "(?:"
    "\u0435\u0436\u0435\u0434\u043d\u0435\u0432\u043d\\w*|"
    "\u043a\u0430\u0436\u0434\u044b\u0439\\s+\u0434\u0435\u043d\u044c|"
    "\u043a\u0430\u0436\u0434\u043e\u0435\\s+\u0443\u0442\u0440\u043e|"
    "\u043a\u0430\u0436\u0434\u044b\u0439\\s+\u0432\u0435\u0447\u0435\u0440"
    ")"
)

WEEKLY_RECURRENCE_RE = (
    "(?:"
    "\u0435\u0436\u0435\u043d\u0435\u0434\u0435\u043b\u044c\u043d\\w*|"
    "\u043a\u0430\u0436\u0434\u0443\u044e\\s+\u043d\u0435\u0434\u0435\u043b\u044e"
    ")"
)

RECURRENCE_PHRASES_RE = f"(?:{DAILY_RECURRENCE_RE}|{WEEKLY_RECURRENCE_RE})"
TASK_CREATION_MODIFIER_RE = (
    "(?:"
    "\u0435\u0449\u0435|"
    "\u0435\u0449\u0451|"
    "\u043d\u043e\u0432\u0443\u044e|"
    "\u043d\u043e\u0432\u043e\u0435|"
    "\u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0443\u044e|"
    "\u0435\u0436\u0435\u0434\u043d\u0435\u0432\u043d\\w*|"
    "\u0435\u0436\u0435\u043d\u0435\u0434\u0435\u043b\u044c\u043d\\w*|"
    "\u043f\u043e\u0432\u0442\u043e\u0440\u044f\u044e\u0449\\w*"
    ")"
)
TASK_NOUN_RE = "(?:\u0437\u0430\u0434\u0430\u0447[\u0430\u0443\u0438]?|\u0434\u0435\u043b\u043e|\u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d(?:\u0438\u0435|\u0438\u044f|\u0438\u044e)?)"
EXPLICIT_DAY_HINT_RE = (
    "(?:"
    "\u0441\u0435\u0433\u043e\u0434\u043d\u044f|"
    "\u0441\u0435\u0433\u043e\u0434\u043d\u044f\u0448\u043d\u0435\u0433\u043e\\s+\u0434\u043d\u044f|"
    "\u0437\u0430\u0432\u0442\u0440\u0430"
    ")"
)

FUZZY_CREATE_VERBS = (
    "\u0434\u043e\u0431\u0430\u0432\u044c",
    "\u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c",
    "\u0441\u043e\u0437\u0434\u0430\u0439",
    "\u0441\u043e\u0437\u0434\u0430\u0442\u044c",
    "\u0437\u0430\u043f\u0438\u0448\u0438",
    "\u043f\u043e\u0441\u0442\u0430\u0432\u044c",
    "\u043f\u043e\u0441\u0442\u0430\u0432\u0438\u0442\u044c",
    "\u043d\u0430\u043f\u043e\u043c\u043d\u0438",
    "\u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u0442\u044c",
)

FUZZY_TASK_NOUNS = (
    "\u0437\u0430\u0434\u0430\u0447\u0430",
    "\u0437\u0430\u0434\u0430\u0447\u0443",
    "\u0437\u0430\u0434\u0430\u0447\u0438",
    "\u0434\u0435\u043b\u043e",
    "\u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435",
    "\u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f",
    "\u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044e",
)

FUZZY_DAILY_RECURRENCE_WORDS = (
    "\u0435\u0436\u0435\u0434\u043d\u0435\u0432\u043d\u043e",
    "\u0435\u0436\u0435\u0434\u043d\u0435\u0432\u043d\u0430\u044f",
    "\u0435\u0436\u0435\u0434\u043d\u0435\u0432\u043d\u0443\u044e",
    "\u0435\u0436\u0435\u0434\u043d\u0435\u0432\u043d\u044b\u0439",
    "\u0435\u0436\u0435\u0434\u043d\u0435\u0432\u043d\u043e\u0435",
)

FUZZY_WEEKLY_RECURRENCE_WORDS = (
    "\u0435\u0436\u0435\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u043e",
    "\u0435\u0436\u0435\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u0430\u044f",
    "\u0435\u0436\u0435\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u0443\u044e",
    "\u0435\u0436\u0435\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u044b\u0439",
    "\u0435\u0436\u0435\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u043e\u0435",
)


class IntentService:
    async def detect_smart(
        self,
        text: str,
        now: datetime,
        timezone: ZoneInfo,
        ai_service: object | None = None,
        conversation_context: str | None = None,
        use_ai_classifier: bool = True,
    ) -> Intent:
        normalized = _normalize_intent_text(text)
        if not normalized:
            return Intent(IntentType.UNKNOWN, 0.0, user_message=text)

        create_signal = _has_create_task_signal(normalized) or _looks_like_schedule_plan(normalized)
        if _prefers_plain_chat(normalized) and not create_signal:
            return Intent(IntentType.GENERAL_CHAT, 0.94, scenario=AIScenario.GENERAL_CHAT, user_message=text)

        support_scenario = _detect_support_scenario(normalized)
        if support_scenario is not None and not create_signal:
            return Intent(
                _support_intent_type(support_scenario),
                0.93,
                scenario=support_scenario,
                user_message=text,
            )

        if _is_low_energy_state(normalized) and not create_signal:
            return Intent(IntentType.EMOTIONAL_STATE, 0.92, scenario=AIScenario.COMEBACK, user_message=text)

        classifier = getattr(ai_service, "classify_intent", None) if use_ai_classifier else None
        if classifier is not None:
            payload = await classifier(text, now.astimezone(timezone), str(timezone), conversation_context)
            ai_intent = _intent_from_ai_payload(payload, text, now, timezone)
            task_advice = _detect_task_advice_query(normalized)
            if task_advice and (
                ai_intent is None
                or ai_intent.type in {IntentType.GENERAL_CHAT, IntentType.TASK_QUERY, IntentType.UNKNOWN}
            ):
                return Intent(IntentType.TASK_HELP, 0.9, scenario=task_advice, user_message=text)
            if create_signal and (
                ai_intent is None
                or ai_intent.type != IntentType.CREATE_TASK
                or not _is_confident_ai_intent(ai_intent, ai_service)
            ):
                forced_intent = await _force_ai_create_task_plan(
                    text,
                    now,
                    timezone,
                    ai_service,
                )
                if forced_intent is not None and forced_intent.task_creations:
                    return forced_intent
                if forced_intent is not None and _is_confident_ai_intent(forced_intent, ai_service):
                    return forced_intent
                if ai_intent is not None and ai_intent.type == IntentType.CREATE_TASK and ai_intent.task_creations:
                    return ai_intent
                local_intent = self.detect(text, now, timezone)
                if local_intent.type == IntentType.CREATE_TASK and local_intent.task_creations:
                    return local_intent
                if payload is not None:
                    return Intent(IntentType.CREATE_TASK, 0.0, user_message=text)

            if payload is not None and _is_confident_ai_intent(ai_intent, ai_service):
                return ai_intent
            if payload is not None:
                return Intent(IntentType.GENERAL_CHAT, 0.5, scenario=AIScenario.GENERAL_CHAT, user_message=text)

        return self.detect(text, now, timezone)

    def detect(self, text: str, now: datetime, timezone: ZoneInfo) -> Intent:
        normalized = _normalize_intent_text(text)
        if not normalized:
            return Intent(IntentType.UNKNOWN, 0.0, user_message=text)

        management = _detect_management(normalized)
        if management:
            return Intent(
                IntentType.TASK_MANAGEMENT,
                0.95,
                action=management[0],
                minutes=management[1],
                user_message=text,
            )

        task_query = _detect_task_query(normalized)
        if task_query:
            return Intent(
                IntentType.TASK_QUERY,
                0.95,
                query=task_query,
                user_message=text,
            )

        if _prefers_plain_chat(normalized):
            return Intent(IntentType.GENERAL_CHAT, 0.9, scenario=AIScenario.GENERAL_CHAT, user_message=text)

        support_scenario = _detect_support_scenario(normalized)
        if support_scenario is not None:
            return Intent(
                _support_intent_type(support_scenario),
                0.9,
                scenario=support_scenario,
                user_message=text,
            )

        task_advice = _detect_task_advice_query(normalized)
        if task_advice:
            return Intent(IntentType.TASK_HELP, 0.9, scenario=task_advice, user_message=text)

        if _is_emotional_state(normalized):
            return Intent(IntentType.EMOTIONAL_STATE, 0.85, scenario=AIScenario.COMEBACK, user_message=text)

        scenario = _detect_procrastination(normalized)
        if scenario:
            return Intent(IntentType.PROCRASTINATION, 0.9, scenario=scenario, user_message=text)

        motivation = _detect_motivation(normalized)
        if motivation:
            return Intent(IntentType.MOTIVATION, 0.9, scenario=motivation, user_message=text)

        schedule_tasks = _parse_schedule_plan(normalized, now, timezone)
        if schedule_tasks:
            first = schedule_tasks[0]
            return Intent(
                IntentType.CREATE_TASK,
                0.72,
                task_title=first.title,
                reminder_at=first.remind_at,
                recurrence_kind=first.recurrence_kind,
                repeat_every_minutes=first.repeat_every_minutes,
                task_creations=tuple(schedule_tasks),
                user_message=text,
            )

        help_scenario = _detect_task_help(normalized)
        if help_scenario:
            return Intent(IntentType.TASK_HELP, 0.85, scenario=help_scenario, user_message=text)

        parsed = _parse_creation(text, normalized, now, timezone)
        if parsed:
            return Intent(
                IntentType.CREATE_TASK,
                0.8,
                task_title=parsed.title,
                reminder_at=parsed.remind_at,
                recurrence_kind=parsed.recurrence_kind,
                repeat_every_minutes=parsed.repeat_every_minutes,
                task_creations=(parsed,),
                user_message=text,
            )

        if _is_general_chat(normalized):
            return Intent(IntentType.GENERAL_CHAT, 0.75, scenario=AIScenario.GENERAL_CHAT, user_message=text)

        parsed = _parse_creation(text, normalized, now, timezone)
        if parsed:
            return Intent(
                IntentType.CREATE_TASK,
                0.8,
                task_title=parsed.title,
                reminder_at=parsed.remind_at,
                recurrence_kind=parsed.recurrence_kind,
                repeat_every_minutes=parsed.repeat_every_minutes,
                task_creations=(parsed,),
                user_message=text,
            )

        return Intent(IntentType.UNKNOWN, 0.2, scenario=AIScenario.HELP_TASK, user_message=text)


def _parse_creation(
    raw_text: str,
    normalized: str,
    now: datetime,
    timezone: ZoneInfo,
) -> ParsedTaskCreation | None:
    if _detect_task_query(normalized):
        return None

    recurrence_kind = _extract_recurrence_kind(normalized)
    loose = _parse_loose_task_request(normalized, now, timezone)
    if loose:
        return loose

    try:
        parsed = parse_natural_reminder(raw_text, now, timezone)
        if parsed:
            return ParsedTaskCreation(
                title=_cleanup_title(parsed.title),
                remind_at=_adjust_due_at_for_recurrence(parsed.remind_at, normalized, now, timezone, recurrence_kind),
                recurrence_kind=recurrence_kind,
                repeat_every_minutes=_extract_repeat_minutes(normalized),
                description=None,
            )
    except TimeParseError:
        return None

    title = _extract_task_title(normalized)
    if title:
        return ParsedTaskCreation(
            title=title,
            remind_at=_extract_due_at(normalized, now, timezone, recurrence_kind) or now.astimezone(timezone),
            recurrence_kind=recurrence_kind,
            repeat_every_minutes=_extract_repeat_minutes(normalized),
            description=None,
        )

    return None


def _intent_from_ai_payload(
    payload: object,
    raw_text: str,
    now: datetime,
    timezone: ZoneInfo,
) -> Intent | None:
    if not isinstance(payload, dict):
        return None

    intent_type = str(payload.get("intent") or "").strip().lower()
    confidence = _clamp_confidence(payload.get("confidence"))
    normalized = _normalize_intent_text(raw_text)

    if intent_type == IntentType.TASK_QUERY.value:
        query = _parse_task_query(payload.get("query")) or _detect_task_query(normalized)
        if query is None:
            query = TaskQuery.LIST_ACTIVE
        return Intent(IntentType.TASK_QUERY, confidence, query=query, user_message=raw_text)

    if intent_type == IntentType.CREATE_TASK.value:
        task_creations = _parse_ai_task_creations(payload, raw_text, now, timezone)
        if not task_creations:
            return None
        first = task_creations[0]
        return Intent(
            IntentType.CREATE_TASK,
            confidence,
            task_title=first.title,
            reminder_at=first.remind_at,
            recurrence_kind=first.recurrence_kind,
            repeat_every_minutes=first.repeat_every_minutes,
            task_creations=tuple(task_creations),
            user_message=raw_text,
        )

    if intent_type == IntentType.TASK_MANAGEMENT.value:
        action = _parse_task_action(payload.get("action"))
        if action is None:
            return None
        return Intent(
            IntentType.TASK_MANAGEMENT,
            confidence,
            action=action,
            minutes=_parse_optional_int(payload.get("minutes")),
            user_message=raw_text,
        )

    if intent_type == IntentType.GENERAL_CHAT.value:
        return Intent(IntentType.GENERAL_CHAT, confidence, scenario=AIScenario.GENERAL_CHAT, user_message=raw_text)

    if intent_type in {IntentType.UNKNOWN.value, ""}:
        return Intent(IntentType.GENERAL_CHAT, confidence, scenario=AIScenario.GENERAL_CHAT, user_message=raw_text)

    scenario = _parse_ai_scenario(payload.get("scenario"))
    mapping = {
        IntentType.PROCRASTINATION.value: IntentType.PROCRASTINATION,
        IntentType.TASK_HELP.value: IntentType.TASK_HELP,
        IntentType.EMOTIONAL_STATE.value: IntentType.EMOTIONAL_STATE,
        IntentType.MOTIVATION.value: IntentType.MOTIVATION,
    }
    if intent_type in mapping:
        return Intent(
            mapping[intent_type],
            confidence,
            scenario=scenario or AIScenario.HELP_TASK,
            user_message=raw_text,
        )

    return None


async def _force_ai_create_task_plan(
    text: str,
    now: datetime,
    timezone: ZoneInfo,
    ai_service: object | None,
) -> Intent | None:
    planner = getattr(ai_service, "plan_task_creation", None)
    if planner is None:
        return None

    payload = await planner(text, now.astimezone(timezone), str(timezone))
    intent = _intent_from_ai_payload(payload, text, now, timezone)
    if intent is None or intent.type != IntentType.CREATE_TASK:
        return None
    return intent


def _is_confident_ai_intent(intent: Intent | None, ai_service: object | None) -> bool:
    if intent is None:
        return False

    settings = getattr(ai_service, "settings", None)
    default_threshold = float(getattr(settings, "openai_intent_confidence_threshold", 0.55))
    task_threshold = float(getattr(settings, "openai_create_task_confidence_threshold", 0.75))
    threshold = task_threshold if intent.type == IntentType.CREATE_TASK else default_threshold
    return intent.confidence >= threshold


def _parse_ai_task_creations(
    payload: dict,
    raw_text: str,
    now: datetime,
    timezone: ZoneInfo,
) -> list[ParsedTaskCreation]:
    payload_recurrence_kind = _parse_recurrence_kind(payload.get("recurrence_kind")) or _extract_recurrence_kind(
        _normalize_intent_text(raw_text)
    )
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        raw_tasks = [
            {
                "title": payload.get("task_title"),
                "reminder_phrase": payload.get("reminder_phrase"),
                "reminder_at": payload.get("reminder_at"),
                "repeat_every_minutes": payload.get("repeat_every_minutes"),
                "description": payload.get("description"),
                "recurrence_kind": payload.get("recurrence_kind"),
            }
        ]

    result: list[ParsedTaskCreation] = []
    normalized_raw_text = _normalize_intent_text(raw_text)
    for item in raw_tasks[:10]:
        if not isinstance(item, dict):
            continue

        title = _cleanup_title(_remove_due_phrase_from_title(str(item.get("title") or item.get("task_title") or "")))
        if not title or _detect_task_query(_normalize(title)):
            continue

        reminder_phrase = str(item.get("reminder_phrase") or item.get("time") or "").strip()
        reminder_at_raw = str(item.get("reminder_at") or item.get("remind_at") or "").strip()
        recurrence_kind = _parse_recurrence_kind(item.get("recurrence_kind")) or payload_recurrence_kind
        remind_at = _parse_ai_reminder_at(
            reminder_at_raw,
            reminder_phrase,
            normalized_raw_text,
            now,
            timezone,
            recurrence_kind,
        )
        repeat_every_minutes = _parse_optional_int(item.get("repeat_every_minutes"))
        description = _cleanup_description(
            str(item.get("description") or item.get("comment") or item.get("note") or "").strip()
        )
        result.append(
            ParsedTaskCreation(
                title=title,
                remind_at=remind_at,
                recurrence_kind=recurrence_kind,
                repeat_every_minutes=repeat_every_minutes,
                description=description,
            )
        )

    return result


def _parse_ai_reminder_at(
    reminder_at_raw: str,
    reminder_phrase: str,
    normalized_raw_text: str,
    now: datetime,
    timezone: ZoneInfo,
    recurrence_kind: RecurrenceKind = RecurrenceKind.NONE,
) -> datetime:
    if reminder_at_raw:
        try:
            parsed = datetime.fromisoformat(reminder_at_raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone)
            return _adjust_due_at_for_recurrence(parsed.astimezone(timezone), normalized_raw_text, now, timezone, recurrence_kind)
        except ValueError:
            pass

    if reminder_phrase:
        phrase = _normalize(reminder_phrase)
        extracted = _extract_due_at(phrase, now, timezone, recurrence_kind)
        if extracted is not None:
            return extracted
        try:
            parsed = parse_time_input(reminder_phrase, now, timezone)
            return _adjust_due_at_for_recurrence(parsed, phrase, now, timezone, recurrence_kind)
        except TimeParseError:
            pass

    extracted = _extract_due_at(normalized_raw_text, now, timezone, recurrence_kind)
    if extracted is not None:
        return extracted
    return now.astimezone(timezone)


def _parse_ai_reminder_phrase(
    reminder_phrase: str,
    normalized: str,
    now: datetime,
    timezone: ZoneInfo,
) -> datetime:
    if reminder_phrase:
        extracted = _extract_due_at(_normalize(reminder_phrase), now, timezone)
        if extracted is not None:
            return extracted
        try:
            return parse_time_input(reminder_phrase, now, timezone)
        except TimeParseError:
            pass
    return _extract_due_at(normalized, now, timezone) or now.astimezone(timezone)


def _clamp_confidence(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.7
    return max(0.0, min(result, 1.0))


def _parse_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_task_action(value: object) -> TaskAction | None:
    try:
        return TaskAction(str(value).strip().lower())
    except ValueError:
        return None


def _parse_task_query(value: object) -> TaskQuery | None:
    try:
        return TaskQuery(str(value).strip().lower())
    except ValueError:
        return None


def _parse_ai_scenario(value: object) -> AIScenario | None:
    try:
        return AIScenario(str(value).strip().lower())
    except ValueError:
        return None


def _is_general_chat(normalized: str) -> bool:
    if re.fullmatch(r"(привет|здравствуй|здравствуйте|хай|hello|hi|спасибо|спс|окей|ок)", normalized):
        return True

    direct_patterns = [
        r"\b(сколько\s+(?:сейчас\s+)?времени|который\s+час|текущее\s+время)\b",
        r"\b(какая\s+сегодня\s+дата|какое\s+сегодня\s+число|какой\s+сегодня\s+день)\b",
        r"\b(что\s+ты\s+умеешь|кто\s+ты|как\s+тобой\s+пользоваться)\b",
        r"\b(расскажи|объясни|поясни|придумай|посоветуй)\b",
        r"\b(что[\s-]?нибудь\s+интересн|что-то\s+интересн|интересный\s+факт)\b",
    ]
    if any(re.search(pattern, normalized) for pattern in direct_patterns):
        return True

    question_starts = (
        "что ",
        "кто ",
        "как ",
        "где ",
        "когда ",
        "почему ",
        "сколько ",
        "какой ",
        "какая ",
        "какое ",
        "какие ",
        "поясни ",
        "объясни ",
        "расскажи ",
        "можешь ",
        "можешь ли ",
        "как работает ",
        "что такое ",
    )
    if normalized.endswith("?") and normalized.startswith(question_starts):
        return True

    if _extract_recurrence_kind(normalized) != RecurrenceKind.NONE:
        cleaned = _cleanup_title(_remove_due_phrase_from_title(normalized))
        if len(cleaned) >= 3 and not _looks_like_general_question(cleaned):
            return True
    return False


def _parse_loose_task_request(
    normalized: str,
    now: datetime,
    timezone: ZoneInfo,
) -> ParsedTaskCreation | None:
    if _detect_task_query(normalized):
        return None

    recurrence_kind = _extract_recurrence_kind(normalized)
    if not _has_create_task_signal(normalized) and recurrence_kind == RecurrenceKind.NONE:
        return None

    title = _extract_marked_title(normalized)
    if title is None:
        cleaned = _remove_schedule_and_instruction_parts(normalized)
        title = _extract_task_title(cleaned)
        if title is None and cleaned and not _looks_like_general_question(cleaned):
            title = _cleanup_title(cleaned)
    if title is None:
        return None

    return ParsedTaskCreation(
        title=title,
        remind_at=_extract_due_at(normalized, now, timezone, recurrence_kind) or now.astimezone(timezone),
        recurrence_kind=recurrence_kind,
        repeat_every_minutes=_extract_repeat_minutes(normalized),
        description=None,
    )


def _parse_schedule_plan(
    normalized: str,
    now: datetime,
    timezone: ZoneInfo,
) -> list[ParsedTaskCreation]:
    if not _looks_like_schedule_plan(normalized):
        return []

    local_now = now.astimezone(timezone)
    default_day = _target_date(normalized, local_now)
    recurrence_kind = _extract_recurrence_kind(normalized)
    parts = _split_schedule_parts(normalized)
    tasks: list[ParsedTaskCreation] = []
    seen: set[tuple[str, datetime]] = set()

    for part in parts:
        parsed = _parse_schedule_part(part, default_day, local_now, timezone, recurrence_kind)
        if parsed is None:
            continue
        key = (parsed.title, parsed.remind_at)
        if key in seen:
            continue
        seen.add(key)
        tasks.append(parsed)

    return tasks[:10] if len(tasks) >= 2 else []


def _looks_like_schedule_plan(normalized: str) -> bool:
    if _detect_task_query(normalized):
        return False

    time_mentions = _count_time_mentions(normalized)
    if time_mentions < 2:
        return False

    if re.search(r"\b(план|расписание|график|распорядок|список)\b", normalized):
        return True

    separators = len(re.findall(r"[,;\n]", normalized))
    if separators >= 1 and _count_schedule_like_parts(normalized) >= 2:
        return True

    return False


def _count_time_mentions(normalized: str) -> int:
    patterns = [
        r"\b(?:в|к|на|с)\s+\d{1,2}[:.]\d{2}\b",
        r"\b(?:в|к|на|с)\s+\d{1,2}\s+\d{2}\b",
        r"\b(?:в|к|на|с)\s+\d{1,2}\s*(?:утра|дня|вечера|ночи)\b",
        r"\b(?:в|к|на|с)\s+\d{1,2}\b",
        r"\b\d{1,2}[:.]\d{2}\b",
    ]
    return sum(len(re.findall(pattern, normalized)) for pattern in patterns)


def _count_schedule_like_parts(normalized: str) -> int:
    return sum(1 for part in _split_schedule_parts(normalized) if _parse_schedule_time_match(part) is not None)


def _split_schedule_parts(normalized: str) -> list[str]:
    text = re.sub(r"^.*?\b(?:план|расписание|график|распорядок)\b[^:]*:\s*", "", normalized)
    text = re.sub(r"^.*?\b(?:на\s+завтра|на\s+сегодня)\s*:\s*", "", text)
    return [part.strip(" .,!?:;") for part in re.split(r"[,;\n]+", text) if part.strip(" .,!?:;")]


def _parse_schedule_part(
    part: str,
    default_day: date | None,
    local_now: datetime,
    timezone: ZoneInfo,
    recurrence_kind: RecurrenceKind = RecurrenceKind.NONE,
) -> ParsedTaskCreation | None:
    match = _parse_schedule_time_match(part)
    if match is None:
        return None

    groups = match.groupdict()
    hour = int(groups["hour"])
    minute = int(groups.get("minute") or 0)
    suffix = groups.get("suffix")
    converted_hour = _convert_hour(hour, suffix)
    if converted_hour is None or minute > 59:
        return None

    title_before = part[: match.start()].strip(" .,!?:;-")
    title_after = part[match.end() :].strip(" .,!?:;-")
    title = _cleanup_title(_remove_due_phrase_from_title(f"{title_before} {title_after}"))
    if not title or len(title) < 2 or _looks_like_general_question(title):
        return None

    target_day = default_day or local_now.date()
    remind_at = datetime.combine(target_day, time(hour=converted_hour, minute=minute), tzinfo=timezone)
    if default_day is None and remind_at < local_now - timedelta(seconds=30):
        remind_at += timedelta(days=7 if recurrence_kind == RecurrenceKind.WEEKLY else 1)

    return ParsedTaskCreation(
        title=title,
        remind_at=remind_at,
        recurrence_kind=recurrence_kind,
        repeat_every_minutes=None,
        description=None,
    )


def _parse_schedule_time_match(part: str) -> re.Match[str] | None:
    patterns = [
        r"\b(?:в|к|на|с)\s+(?P<hour>\d{1,2})[:.](?P<minute>\d{2})\s*(?P<suffix>утра|дня|вечера|ночи)?\b",
        r"\b(?:в|к|на|с)\s+(?P<hour>\d{1,2})\s+(?P<minute>\d{2})\s*(?P<suffix>утра|дня|вечера|ночи)?\b",
        r"\b(?:в|к|на|с)\s+(?P<hour>\d{1,2})\s*(?P<suffix>утра|дня|вечера|ночи)\b",
        r"\b(?P<hour>\d{1,2})[:.](?P<minute>\d{2})\s*(?P<suffix>утра|дня|вечера|ночи)?\b",
        r"\b(?:в|к|на|с)\s+(?P<hour>\d{1,2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, part)
        if match:
            return match
    return None


def _extract_marked_title(normalized: str) -> str | None:
    patterns = [
        r"(?:задача\s+такая|задач[ау]?|дело)\s*[:\-—]\s*(.+)$",
        r"(?:суть|название)\s*[:\-—]\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            title = _cleanup_title(_remove_due_phrase_from_title(_cut_instruction_tail(match.group(1))))
            return title if len(title) >= 2 else None
    return None


def _extract_due_at(
    normalized: str,
    now: datetime,
    timezone: ZoneInfo,
    recurrence_kind: RecurrenceKind = RecurrenceKind.NONE,
) -> datetime | None:
    relative = re.search(
        r"\bчерез\s+(\d{1,4})\s*(минут(?:у|ы)?|мин|м|час(?:а|ов)?|ч)\b",
        normalized,
    )
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = timedelta(hours=amount) if unit.startswith(("час", "ч")) else timedelta(minutes=amount)
        return now.astimezone(timezone) + delta

    local_now = now.astimezone(timezone)
    calendar_day = _target_date(normalized, local_now)

    absolute_day_time = _extract_time_of_day(normalized)
    if absolute_day_time is not None and (
        calendar_day is not None
        or re.search(r"\d{1,2}[:.]\d{2}", normalized)
        or re.search(r"\b(утра|дня|вечера|ночи)\b", normalized)
        or re.search(r"\b(?:в|к|на)\s+\d{1,2}\b", normalized)
    ):
        target_day = calendar_day or local_now.date()
        target = datetime.combine(target_day, absolute_day_time, tzinfo=timezone)
        return _adjust_due_at_for_recurrence(
            target,
            normalized,
            now,
            timezone,
            recurrence_kind,
            has_explicit_day=calendar_day is not None,
        )

    absolute = re.search(
        r"(?:\bв|\bна)?\s*(\d{1,2})[:.](\d{2})(?:\s*(?:сегодня|сегодняшнего\s+дня))?",
        normalized,
    )
    if absolute:
        hour = int(absolute.group(1))
        minute = int(absolute.group(2))
        if hour > 23 or minute > 59:
            return None
        target = datetime.combine(local_now.date(), time(hour=hour, minute=minute), tzinfo=timezone)
        return _adjust_due_at_for_recurrence(target, normalized, now, timezone, recurrence_kind)

    if re.search(r"\b(сейчас|прямо сейчас)\b", normalized):
        return now.astimezone(timezone)

    return None


def _adjust_due_at_for_recurrence(
    due_at: datetime,
    normalized: str,
    now: datetime,
    timezone: ZoneInfo,
    recurrence_kind: RecurrenceKind,
    has_explicit_day: bool = False,
) -> datetime:
    localized = due_at.astimezone(timezone)
    local_now = now.astimezone(timezone)
    if localized >= local_now - timedelta(seconds=30):
        return localized
    if has_explicit_day or _has_explicit_day_hint(normalized):
        return localized
    if recurrence_kind == RecurrenceKind.WEEKLY:
        return localized + timedelta(days=7)
    return localized + timedelta(days=1)


def _has_explicit_day_hint(normalized: str) -> bool:
    return bool(re.search(r"\b(СЃРµРіРѕРґРЅСЏ|СЃРµРіРѕРґРЅСЏС€РЅРµРіРѕ\s+РґРЅСЏ|Р·Р°РІС‚СЂР°)\b", normalized))


def _parse_recurrence_kind(value: object) -> RecurrenceKind | None:
    try:
        return RecurrenceKind(str(value).strip().lower())
    except ValueError:
        return None


def _extract_recurrence_kind(normalized: str) -> RecurrenceKind:
    if re.search(r"\b(РµР¶РµРґРЅРµРІРЅРѕ|РєР°Р¶РґС‹Р№\s+РґРµРЅСЊ|РєР°Р¶РґРѕРµ\s+СѓС‚СЂРѕ|РєР°Р¶РґС‹Р№\s+РІРµС‡РµСЂ)\b", normalized):
        return RecurrenceKind.DAILY
    if re.search(r"\b(РµР¶РµРЅРµРґРµР»СЊРЅРѕ|РєР°Р¶РґСѓСЋ\s+РЅРµРґРµР»СЋ|РєР°Р¶РґС‹Р№\s+РїРѕРЅРµРґРµР»СЊРЅРёРє|РєР°Р¶РґС‹Р№\s+РІС‚РѕСЂРЅРёРє|РєР°Р¶РґСѓСЋ\s+СЃСЂРµРґСѓ|РєР°Р¶РґС‹Р№\s+С‡РµС‚РІРµСЂРі|РєР°Р¶РґСѓСЋ\s+РїСЏС‚РЅРёС†Сѓ|РєР°Р¶РґСѓСЋ\s+СЃСѓР±Р±РѕС‚Сѓ|РєР°Р¶РґРѕРµ\s+РІРѕСЃРєСЂРµСЃРµРЅСЊРµ)\b", normalized):
        return RecurrenceKind.WEEKLY
    return RecurrenceKind.NONE


def _target_date(normalized: str, local_now: datetime) -> date | None:
    if re.search(r"\bзавтра\b", normalized):
        return local_now.date() + timedelta(days=1)
    if re.search(r"\b(сегодня|сегодняшнего\s+дня)\b", normalized):
        return local_now.date()
    return None


def _extract_time_of_day(normalized: str) -> time | None:
    patterns = [
        r"\b(?:завтра|сегодня|сегодняшнего\s+дня)\s+(?:в\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(утра|дня|вечера|ночи)?\b",
        r"\b(?:в\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(утра|дня|вечера|ночи)?\s*(?:завтра|сегодня|сегодняшнего\s+дня)\b",
        r"\bв\s+(\d{1,2})(?:[:.](\d{2}))?\s*(утра|дня|вечера|ночи)\b",
        r"\b(?:в|к|на)\s+(\d{1,2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        hour = int(match.group(1))
        minute = int(match.group(2) or 0) if match.lastindex and match.lastindex >= 2 else 0
        suffix = match.group(3) if match.lastindex and match.lastindex >= 3 else None
        converted_hour = _convert_hour(hour, suffix)
        if converted_hour is None or minute > 59:
            return None
        return time(hour=converted_hour, minute=minute)
    return None


def _convert_hour(hour: int, suffix: str | None) -> int | None:
    if hour < 0 or hour > 23:
        return None

    if suffix is None:
        return hour
    if suffix == "утра":
        return 0 if hour == 12 else hour if 1 <= hour <= 11 else None
    if suffix in {"дня", "вечера"}:
        if hour == 12:
            return 12
        return hour + 12 if 1 <= hour <= 11 else hour if 13 <= hour <= 23 else None
    if suffix == "ночи":
        return 0 if hour == 12 else hour if 0 <= hour <= 6 else None
    return hour


def _remove_due_phrase_from_title(value: str) -> str:
    text = value
    text = re.sub(
        r"\b(?:завтра|сегодня|сегодняшнего\s+дня)\s+(?:в\s+)?\d{1,2}(?:[:.]\d{2})?\s*(?:утра|дня|вечера|ночи)?\b",
        " ",
        text,
    )
    text = re.sub(
        r"\b(?:в\s+)?\d{1,2}(?:[:.]\d{2})?\s*(?:утра|дня|вечера|ночи)?\s*(?:завтра|сегодня|сегодняшнего\s+дня)\b",
        " ",
        text,
    )
    text = re.sub(r"\bв\s+\d{1,2}(?:[:.]\d{2})?\s*(?:утра|дня|вечера|ночи)\b", " ", text)
    text = re.sub(r"\b(?:в|к|на)\s+\d{1,2}\b", " ", text)
    text = re.sub(r"\b(?:утром|днем|днём|вечером|ночью)\b", " ", text)
    text = re.sub(r"\b(?:РµР¶РµРґРЅРµРІРЅРѕ|РµР¶РµРЅРµРґРµР»СЊРЅРѕ|РєР°Р¶РґС‹Р№\s+РґРµРЅСЊ|РєР°Р¶РґСѓСЋ\s+РЅРµРґРµР»СЋ)\b", " ", text)
    return _normalize(text)


def _extract_repeat_minutes(normalized: str) -> int | None:
    match = re.search(
        r"\bкажд(?:ые|ую|ый)\s+(\d{1,3})\s*(минут(?:у|ы)?|мин|м|час(?:а|ов)?|ч)?\b",
        normalized,
    )
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2) or "мин"
    minutes = value * 60 if unit.startswith(("час", "ч")) else value
    return max(1, min(minutes, 1440))


def _remove_schedule_and_instruction_parts(normalized: str) -> str:
    text = normalized
    text = re.sub(r"\b(?:в|на)?\s*\d{1,2}[:.]\d{2}(?:\s*(?:сегодня|сегодняшнего\s+дня))?", " ", text)
    text = re.sub(r"\bчерез\s+\d{1,4}\s*(?:минут(?:у|ы)?|мин|м|час(?:а|ов)?|ч)\b", " ", text)
    text = re.sub(r"\b(?:мне\s+)?(?:надо|нужно)\s+добавить\s+задачу\b", " ", text)
    text = re.sub(
        r"\b(?:добавь|добавить|создай|создать|поставь|поставить|запиши)\s+"
        r"(?:мне\s+)?(?:еще|ещё|новую|новое|следующую)?\s*"
        r"(?:задач[ауи]?|дело|напоминание)\b",
        " ",
        text,
    )
    text = re.sub(r"\b(?:напомни|напоминать|поставь\s+напоминание)\s+(?:мне\s+)?", " ", text)
    text = re.sub(r"\b(?:утром|днем|днём|вечером|ночью)\b", " ", text)
    text = re.sub(r"\b(?:РµР¶РµРґРЅРµРІРЅРѕ|РµР¶РµРЅРµРґРµР»СЊРЅРѕ|РєР°Р¶РґС‹Р№\s+РґРµРЅСЊ|РєР°Р¶РґСѓСЋ\s+РЅРµРґРµР»СЋ)\b", " ", text)
    text = _cut_instruction_tail(text)
    return _remove_due_phrase_from_title(text)


def _cut_instruction_tail(value: str) -> str:
    patterns = [
        r",?\s*пинай\b.*$",
        r",?\s*напоминай\b.*$",
        r",?\s*напомни\b.*$",
        r",?\s*дожимай\b.*$",
        r",?\s*пока\s+я\b.*$",
        r",?\s*если\s+я\b.*$",
        r",?\s*кажд(?:ые|ую|ый)\s+\d{1,3}\b.*$",
    ]
    result = value
    for pattern in patterns:
        result = re.sub(pattern, "", result)
    return result


def _extract_task_title(normalized: str) -> str | None:
    if len(normalized) < 4:
        return None

    if _detect_task_query(normalized):
        return None

    if re.fullmatch(r"(да|нет|ок|окей|ладно|хорошо|ага|угу|спасибо)", normalized):
        return None

    patterns = [
        (r"^(?:надо|нужно|необходимо|пора|важно)\s+(.+)$", True),
        (r"^(?:сделать|доделать|закончить|дописать|подготовить|разобрать|созвониться|позвонить)\s+(.+)$", True),
        (r"^(?:хочу|планирую|собираюсь)\s+(.+)$", False),
    ]
    for pattern, explicit_action in patterns:
        match = re.match(pattern, normalized)
        if match:
            title = _cleanup_title(_remove_due_phrase_from_title(match.group(1)))
            if len(title) < 2 or _looks_like_general_question(title):
                return None
            if explicit_action or _looks_like_task(title):
                return title

    return None


def _detect_management(normalized: str) -> tuple[TaskAction, int | None] | None:
    if re.search(r"\b(отмени|отменить|убери|сними|удали|удалить|очисти|очистить)\s+(?:все|всё|все мои|все активные)\s+задач", normalized):
        return TaskAction.CANCEL_ALL, None
    if re.search(r"\b(я\s+)?(начал|начала|стартовал|стартовала|открыл|открыла|приступил|приступила)\b", normalized):
        return TaskAction.START, None
    if re.search(r"\b(готово|сделал|сделала|закончил|закончила|выполнил|выполнила|закрыто)\b", normalized):
        return TaskAction.DONE, None
    if re.search(r"\b(отмени|отменить|убери задачу|сними задачу|удали задачу|удалить задачу)\b", normalized):
        return TaskAction.CANCEL, None
    if re.search(r"\b(перенеси|отложи|позже|не сейчас)\b", normalized):
        return TaskAction.SNOOZE, _extract_minutes(normalized)
    return None


def _has_create_task_signal(normalized: str) -> bool:
    if re.search(r"\b(напомни|напоминать|дожимай|пинай)\b", normalized):
        return True
    if re.search(
        rf"\b(добавь|добавить|создай|создать|запиши|поставь|поставить)\s+"
        rf"(?:мне\s+)?(?:(?:{TASK_CREATION_MODIFIER_RE})\s+)*(?:{TASK_NOUN_RE})",
        normalized,
    ):
        return True
    if re.match(r"^(?:надо|нужно|необходимо|пора|важно)\s+", normalized):
        return True
    if re.match(r"^(?:сделать|доделать|закончить|дописать|подготовить|разобрать|созвониться|позвонить)\s+", normalized):
        return True
    return False


def _detect_task_query(normalized: str) -> TaskQuery | None:
    if not re.search(r"\b(задач|дел[ао]?|напоминан)", normalized):
        return None

    closed_words = r"(завершенн|завершен|закрыт|отмененн|отменен|истори|стар\w*|прошл\w*)"
    count_words = r"(сколько|количество|число|счетчик|счётчик)"
    list_words = r"(покажи|выведи|дай|открой|список|какие|что\s+у\s+меня|мои|активные)"
    active_words = r"(активн|текущ|открыт|незаверш|в\s+работе)"

    if re.search(count_words, normalized):
        if re.search(closed_words, normalized):
            return TaskQuery.COUNT_CLOSED
        return TaskQuery.COUNT_ACTIVE

    if re.search(closed_words, normalized):
        return TaskQuery.LIST_HISTORY

    if re.search(list_words, normalized) and (
        re.search(active_words, normalized) or re.search(r"\b(задач\w*|дел[ао]?)\b", normalized)
    ):
        return TaskQuery.LIST_ACTIVE

    return None


def _detect_task_advice_query(normalized: str) -> AIScenario | None:
    if _prefers_plain_chat(normalized):
        return None
    if _detect_support_scenario(normalized) is not None:
        return None
    if not re.search(r"\b(задач|дел[ао]?|напоминан)", normalized):
        return None

    if re.search(r"\b(как\s+лучше|как\s+выполнить|как\s+сделать|что\s+думаешь|что\s+скажешь|посоветуй|совет|разобрать|приоритет|порядок)\b", normalized):
        return AIScenario.ADVICE

    if re.search(r"\b(план|распланируй|раскидай|распредели|очередность|с\s+чего\s+начать)\b", normalized):
        return AIScenario.PLAN

    if re.search(r"\b(остальн\w*\s+задач|друг\w*\s+задач|мо\w*\s+задач\w*.*выполн|задач\w*.*выполн)\b", normalized):
        return AIScenario.ADVICE

    return None


def _detect_procrastination(normalized: str) -> AIScenario | None:
    panic_words = [
        "хаос",
        "очень много",
        "все горит",
        "всё горит",
        "не понимаю что делать",
        "не понимаю, что делать",
        "не вывожу",
    ]
    if any(word in normalized for word in panic_words):
        return AIScenario.PANIC

    procrastination_words = [
        "не могу начать",
        "не хочу делать",
        "не хочу",
        "сливаюсь",
        "сливаю",
        "опять все сливаю",
        "опять всё сливаю",
        "страшно открывать",
        "боюсь открывать",
        "не знаю с чего начать",
        "не знаю, с чего начать",
        "потом сделаю",
    ]
    if any(word in normalized for word in procrastination_words):
        return AIScenario.PROCRASTINATION

    comeback_words = [
        "отвлекся",
        "отвлеклась",
        "залип",
        "залипла",
        "выпал",
        "выпала",
        "сдаюсь",
        "опускаются руки",
        "не тяну",
        "не вывожу",
        "все достало",
        "всё достало",
    ]
    if any(word in normalized for word in comeback_words):
        return AIScenario.COMEBACK
    return None


def _is_emotional_state(normalized: str) -> bool:
    words = [
        "устал",
        "устала",
        "устал от задач",
        "устала от задач",
        "ничего не хочу",
        "ничего не хочется",
        "нет желания",
        "нет настроения",
        "все бесит",
        "всё бесит",
        "нет сил",
        "нет ресурса",
        "выгорел",
        "выгорела",
        "вымотался",
        "вымоталась",
        "задолбался",
        "задолбалась",
        "не вывожу",
        "тяжело",
        "раздражает",
        "раздражена",
    ]
    return any(word in normalized for word in words)


def _is_low_energy_state(normalized: str) -> bool:
    phrases = [
        "ничего не хочу",
        "ничего не хочется",
        "нет желания",
        "нет настроения",
        "нет сил",
        "нет ресурса",
        "апатия",
        "не могу заставить себя",
        "все лень",
        "всё лень",
    ]
    return any(phrase in normalized for phrase in phrases)


def _detect_motivation(normalized: str) -> AIScenario | None:
    if _asks_for_motivation_media(normalized):
        return AIScenario.BOOST
    if any(word in normalized for word in ["дай пинок", "пни", "мотивируй", "мотивацию", "подбодри"]):
        return AIScenario.BOOST
    if any(word in normalized for word in ["зачем", "ради чего", "напомни зачем", "напомни, зачем"]):
        return AIScenario.WHY
    return None


def _asks_for_motivation_media(normalized: str) -> bool:
    action_words = ["кинь", "скинь", "дай", "давай", "пришли", "отправь", "можешь кинуть", "можешь скинуть"]
    media_words = ["трек", "видос", "видосик", "видео", "ролик", "буст"]
    repeat_words = ["еще", "ещё", "еще один", "ещё один", "еще раз", "ещё раз", "другой", "другую", "следующий", "следующее", "новый", "новое"]
    return any(word in normalized for word in media_words) and (
        any(word in normalized for word in action_words)
        or any(word in normalized for word in repeat_words)
    )


def _detect_task_help(normalized: str) -> AIScenario | None:
    if any(word in normalized for word in ["помоги разбить", "разбей", "разложи на шаги"]):
        return AIScenario.BREAKDOWN
    if any(word in normalized for word in ["с чего начать", "как начать", "первый шаг"]):
        return AIScenario.START_STEP
    if any(word in normalized for word in ["как лучше сделать", "как сделать", "что делать с"]):
        return AIScenario.ADVICE
    if re.search(r"\bплан\b", normalized):
        return AIScenario.PLAN
    return None


def _extract_minutes(normalized: str) -> int | None:
    match = re.search(r"(?:на|через)\s+(\d{1,3})\s*(минут(?:у|ы)?|мин|м|час(?:а|ов)?|ч)?", normalized)
    if match:
        value = int(match.group(1))
        unit = match.group(2) or "мин"
        return value * 60 if unit.startswith(("час", "ч")) else value

    try:
        dt = parse_time_input(normalized, datetime.now(), ZoneInfo("UTC"))
    except Exception:
        return None

    delta = dt - datetime.now(tz=dt.tzinfo)
    minutes = max(1, int(delta.total_seconds() // 60))
    return minutes


def _cleanup_title(value: str) -> str:
    title = value.strip(" .,!?:;—-")
    title = _cut_instruction_tail(title)
    title = re.sub(
        r"^(?:добавь|добавить|создай|создать|поставь|поставить|запиши)\s+"
        r"(?:мне\s+)?(?:еще|ещё|новую|новое|следующую)?\s*"
        r"(?:задач[ауи]?|дело|напоминание)\s*",
        "",
        title,
    )
    title = re.sub(r"^(?:сделать|доделать|закончить|подготовить)\s+", "", title)
    title = title.strip(" .,!?:;—-")
    title = re.sub(r"\b(?:РµР¶РµРґРЅРµРІРЅРѕ|РµР¶РµРЅРµРґРµР»СЊРЅРѕ|РєР°Р¶РґС‹Р№\s+РґРµРЅСЊ|РєР°Р¶РґСѓСЋ\s+РЅРµРґРµР»СЋ)\b", "", title)
    return title[:255]


def _cleanup_description(value: str) -> str | None:
    description = " ".join(value.strip(" .,!?:;—-").split())
    return description[:1000] if description else None


def _looks_like_task(value: str) -> bool:
    if len(value) < 4:
        return False
    blocked = [
        "не могу",
        "не хочу",
        "устал",
        "устала",
        "бесит",
        "спасибо",
        "привет",
        "как дела",
        "добавить задачу",
        "мои задачи",
        "история задач",
        "активные напоминания",
        "помощь",
    ]
    if any(item in value for item in blocked):
        return False
    task_words = [
        "диплом",
        "проект",
        "задач",
        "бизнес",
        "отчет",
        "отчёт",
        "работ",
        "учеб",
        "созвон",
        "позвон",
        "напис",
        "додел",
        "сдел",
        "подготов",
        "разобрать",
        "тренир",
        "потрен",
        "спорт",
    ]
    return any(word in value for word in task_words)


def _looks_like_general_question(value: str) -> bool:
    normalized = _normalize(value)
    starts = (
        "что ",
        "кто ",
        "как ",
        "где ",
        "когда ",
        "почему ",
        "сколько ",
        "какой ",
        "какая ",
        "какое ",
        "какие ",
        "расскажи ",
        "объясни ",
        "поясни ",
    )
    return normalized.startswith(starts) or normalized.endswith("?")


def _normalize_intent_text(text: str) -> str:
    normalized = _normalize(text)
    if not normalized:
        return normalized
    return _normalize_control_words(normalized)


def _normalize_control_words(normalized: str) -> str:
    tokens = normalized.split()
    corrected: list[str] = []
    for index, token in enumerate(tokens):
        replacement = None
        if index < 3:
            replacement = _best_control_word_match(token, FUZZY_CREATE_VERBS)
        if replacement is None and index < 5:
            replacement = _best_control_word_match(token, FUZZY_TASK_NOUNS)
        if replacement is None:
            replacement = _best_control_word_match(token, FUZZY_DAILY_RECURRENCE_WORDS)
        if replacement is None:
            replacement = _best_control_word_match(token, FUZZY_WEEKLY_RECURRENCE_WORDS)
        corrected.append(replacement or token)
    return " ".join(corrected)


def _best_control_word_match(token: str, candidates: tuple[str, ...]) -> str | None:
    if token in candidates:
        return token

    best_match: str | None = None
    best_length_delta: int | None = None
    for candidate in candidates:
        if not _is_single_typo_variant(token, candidate):
            continue
        length_delta = abs(len(candidate) - len(token))
        if best_match is None or best_length_delta is None or length_delta < best_length_delta:
            best_match = candidate
            best_length_delta = length_delta
    return best_match


def _is_single_typo_variant(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False

    if len(left) == len(right):
        diffs = [index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2:
            first, second = diffs
            return (
                second == first + 1
                and left[first] == right[second]
                and left[second] == right[first]
            )
        return False

    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = 0
    long_index = 0
    skipped = False
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        if skipped:
            return False
        skipped = True
        long_index += 1
    return True


def _prefers_plain_chat(normalized: str) -> bool:
    phrases = [
        "как человек",
        "просто поговорить",
        "просто общаться",
        "поговори со мной",
        "выслушай",
        "мне надо выговориться",
        "хочу выговориться",
        "мне нужно выговориться",
        "не про задачи",
        "без задач",
    ]
    return any(phrase in normalized for phrase in phrases)


def _detect_support_scenario(normalized: str) -> AIScenario | None:
    if _is_emotional_state(normalized):
        return AIScenario.COMEBACK
    return _detect_procrastination(normalized)


def _support_intent_type(scenario: AIScenario) -> IntentType:
    if scenario in {AIScenario.COMEBACK, AIScenario.PANIC}:
        return IntentType.EMOTIONAL_STATE
    return IntentType.PROCRASTINATION


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _has_explicit_day_hint(normalized: str) -> bool:
    return bool(re.search(rf"\b{EXPLICIT_DAY_HINT_RE}\b", normalized))


def _extract_recurrence_kind(normalized: str) -> RecurrenceKind:
    if re.search(rf"\b{DAILY_RECURRENCE_RE}\b", normalized):
        return RecurrenceKind.DAILY
    if re.search(rf"\b{WEEKLY_RECURRENCE_RE}\b", normalized):
        return RecurrenceKind.WEEKLY
    return RecurrenceKind.NONE


def _remove_due_phrase_from_title(value: str) -> str:
    day_words = "(?:\u0437\u0430\u0432\u0442\u0440\u0430|\u0441\u0435\u0433\u043e\u0434\u043d\u044f|\u0441\u0435\u0433\u043e\u0434\u043d\u044f\u0448\u043d\u0435\u0433\u043e\\s+\u0434\u043d\u044f)"
    part_of_day = "(?:\u0443\u0442\u0440\u0430|\u0434\u043d\u044f|\u0432\u0435\u0447\u0435\u0440\u0430|\u043d\u043e\u0447\u0438)"
    text = value
    text = re.sub(rf"\b{day_words}\s+(?:\u0432\s+)?\d{{1,2}}(?:[:.]\d{{2}})?\s*{part_of_day}?\b", " ", text)
    text = re.sub(rf"\b(?:\u0432\s+)?\d{{1,2}}(?:[:.]\d{{2}})?\s*{part_of_day}?\s*{day_words}\b", " ", text)
    text = re.sub(rf"\b\u0432\s+\d{{1,2}}(?:[:.]\d{{2}})?\s*{part_of_day}\b", " ", text)
    text = re.sub(r"\b(?:\u0432|\u043a|\u043d\u0430)\s+\d{1,2}\b", " ", text)
    text = re.sub(
        r"\b(?:\u0443\u0442\u0440\u043e\u043c|\u0434\u043d\u0435\u043c|\u0434\u043d\u0451\u043c|\u0432\u0435\u0447\u0435\u0440\u043e\u043c|\u043d\u043e\u0447\u044c\u044e)\b",
        " ",
        text,
    )
    text = re.sub(rf"\b{RECURRENCE_PHRASES_RE}\b", " ", text)
    return _normalize(text)


def _remove_schedule_and_instruction_parts(normalized: str) -> str:
    text = normalized
    text = re.sub(
        r"\b(?:\u0432|\u043d\u0430)?\s*\d{1,2}[:.]\d{2}(?:\s*(?:\u0441\u0435\u0433\u043e\u0434\u043d\u044f|\u0441\u0435\u0433\u043e\u0434\u043d\u044f\u0448\u043d\u0435\u0433\u043e\s+\u0434\u043d\u044f))?",
        " ",
        text,
    )
    text = re.sub(
        r"\b\u0447\u0435\u0440\u0435\u0437\s+\d{1,4}\s*(?:\u043c\u0438\u043d\u0443\u0442(?:\u0443|\u044b)?|\u043c\u0438\u043d|\u043c|\u0447\u0430\u0441(?:\u0430|\u043e\u0432)?|\u0447)\b",
        " ",
        text,
    )
    text = re.sub(
        rf"\b(?:\u043c\u043d\u0435\s+)?(?:\u043d\u0430\u0434\u043e|\u043d\u0443\u0436\u043d\u043e)\s+\u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c\s+(?:(?:{TASK_CREATION_MODIFIER_RE})\s+)*(?:{TASK_NOUN_RE})\b",
        " ",
        text,
    )
    text = re.sub(
        rf"\b(?:\u0434\u043e\u0431\u0430\u0432\u044c|\u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c|\u0441\u043e\u0437\u0434\u0430\u0439|\u0441\u043e\u0437\u0434\u0430\u0442\u044c|\u043f\u043e\u0441\u0442\u0430\u0432\u044c|\u043f\u043e\u0441\u0442\u0430\u0432\u0438\u0442\u044c|\u0437\u0430\u043f\u0438\u0448\u0438)\s+"
        rf"(?:\u043c\u043d\u0435\s+)?(?:(?:{TASK_CREATION_MODIFIER_RE})\s+)*(?:{TASK_NOUN_RE})\b",
        " ",
        text,
    )
    text = re.sub(
        r"\b(?:\u043d\u0430\u043f\u043e\u043c\u043d\u0438|\u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u0442\u044c|\u043f\u043e\u0441\u0442\u0430\u0432\u044c\s+\u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435)\s+(?:\u043c\u043d\u0435\s+)?",
        " ",
        text,
    )
    text = re.sub(
        r"\b(?:\u0443\u0442\u0440\u043e\u043c|\u0434\u043d\u0435\u043c|\u0434\u043d\u0451\u043c|\u0432\u0435\u0447\u0435\u0440\u043e\u043c|\u043d\u043e\u0447\u044c\u044e)\b",
        " ",
        text,
    )
    text = re.sub(rf"\b{RECURRENCE_PHRASES_RE}\b", " ", text)
    text = _cut_instruction_tail(text)
    return _remove_due_phrase_from_title(text)


def _cleanup_title(value: str) -> str:
    title = value.strip(" .,!?:;—-")
    title = _cut_instruction_tail(title)
    title = re.sub(
        rf"^(?:\u0434\u043e\u0431\u0430\u0432\u044c|\u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c|\u0441\u043e\u0437\u0434\u0430\u0439|\u0441\u043e\u0437\u0434\u0430\u0442\u044c|\u043f\u043e\u0441\u0442\u0430\u0432\u044c|\u043f\u043e\u0441\u0442\u0430\u0432\u0438\u0442\u044c|\u0437\u0430\u043f\u0438\u0448\u0438)\s+"
        rf"(?:\u043c\u043d\u0435\s+)?(?:(?:{TASK_CREATION_MODIFIER_RE})\s+)*(?:{TASK_NOUN_RE})\s*",
        "",
        title,
    )
    title = re.sub(
        r"^(?:\u0441\u0434\u0435\u043b\u0430\u0442\u044c|\u0434\u043e\u0434\u0435\u043b\u0430\u0442\u044c|\u0437\u0430\u043a\u043e\u043d\u0447\u0438\u0442\u044c|\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u0442\u044c)\s+",
        "",
        title,
    )
    title = re.sub(rf"\b{RECURRENCE_PHRASES_RE}\b", "", title)
    title = title.strip(" .,!?:;—-")
    return title[:255]
