from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


class TimeParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedReminder:
    title: str
    remind_at: datetime


_RELATIVE_RE = re.compile(
    r"^(?:напомни(?:\s+мне)?\s+)?через\s+(\d{1,4})\s*"
    r"(минут(?:у|ы)?|мин|м|час(?:а|ов)?|ч)\s+(.+)$",
    re.IGNORECASE,
)
_ABSOLUTE_RE = re.compile(
    r"^(?:напомни(?:\s+мне)?\s+)?(?:сегодня\s+)?(?:в\s+)?"
    r"(\d{1,2})[:.](\d{2})\s+(.+)$",
    re.IGNORECASE,
)
_NOW_RE = re.compile(
    r"^(?:напомни(?:\s+мне)?\s+)?(?:сейчас|прямо\s+сейчас)\s+(.+)$",
    re.IGNORECASE,
)


def parse_natural_reminder(
    text: str,
    now: datetime,
    timezone: ZoneInfo,
) -> ParsedReminder | None:
    normalized = " ".join(text.strip().split())
    if not normalized:
        return None

    match = _RELATIVE_RE.match(normalized)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        title = _clean_title(match.group(3))
        delta = timedelta(hours=amount) if unit.startswith(("час", "ч")) else timedelta(minutes=amount)
        return ParsedReminder(title=title, remind_at=now.astimezone(timezone) + delta)

    match = _ABSOLUTE_RE.match(normalized)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        title = _clean_title(match.group(3))
        return ParsedReminder(title=title, remind_at=_today_at(hour, minute, now, timezone))

    match = _NOW_RE.match(normalized)
    if match:
        return ParsedReminder(
            title=_clean_title(match.group(1)),
            remind_at=now.astimezone(timezone),
        )

    return None


def parse_time_input(text: str, now: datetime, timezone: ZoneInfo) -> datetime:
    normalized = " ".join(text.strip().lower().split())
    if normalized in {"сейчас", "прямо сейчас", "now"}:
        return now.astimezone(timezone)

    relative = re.match(
        r"^через\s+(\d{1,4})\s*(минут(?:у|ы)?|мин|м|час(?:а|ов)?|ч)$",
        normalized,
    )
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = timedelta(hours=amount) if unit.startswith(("час", "ч")) else timedelta(minutes=amount)
        return now.astimezone(timezone) + delta

    absolute = re.match(r"^(?:сегодня\s+)?(?:в\s+)?(\d{1,2})[:.](\d{2})$", normalized)
    if absolute:
        return _today_at(
            int(absolute.group(1)),
            int(absolute.group(2)),
            now,
            timezone,
        )

    raise TimeParseError("Не понял время")


def parse_interval_minutes(text: str, default: int) -> int:
    normalized = text.strip().lower()
    if normalized in {"", "-", "по умолчанию", "пропустить", "default"}:
        return default

    match = re.match(r"^(?:каждые\s+)?(\d{1,4})(?:\s*минут(?:у|ы)?|\s*мин|\s*м)?$", normalized)
    if not match:
        raise TimeParseError("Не понял интервал")

    minutes = int(match.group(1))
    if minutes < 1 or minutes > 1440:
        raise TimeParseError("Интервал должен быть от 1 до 1440 минут")
    return minutes


def _today_at(hour: int, minute: int, now: datetime, timezone: ZoneInfo) -> datetime:
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise TimeParseError("Время должно быть в формате ЧЧ:ММ")

    local_now = now.astimezone(timezone)
    candidate = datetime.combine(local_now.date(), time(hour=hour, minute=minute), tzinfo=timezone)
    if candidate <= local_now:
        raise TimeParseError("Это время уже прошло. Укажи время позже текущего")
    return candidate


def _clean_title(value: str) -> str:
    title = value.strip(" .")
    if not title:
        raise TimeParseError("Пустое название задачи")
    return title[:255]
