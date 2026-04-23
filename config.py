from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _read_positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _read_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc

    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    database_path: Path
    timezone: str
    default_repeat_minutes: int
    checkin_after_minutes: int
    default_snooze_minutes: int
    ai_openai_enabled: bool
    openai_api_key: str | None
    openai_model: str
    openai_classifier_model: str
    openai_simple_model: str
    openai_complex_model: str
    openai_timeout_seconds: int
    openai_intent_confidence_threshold: float
    openai_create_task_confidence_threshold: float
    ai_default_tone: str

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _read_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    from dotenv import load_dotenv

    load_dotenv()

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env")

    timezone = os.getenv("BOT_TIMEZONE", "Europe/Astrakhan")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Unknown BOT_TIMEZONE: {timezone}") from exc

    legacy_openai_model = os.getenv("OPENAI_MODEL", "gpt-5-mini")

    return Settings(
        bot_token=bot_token,
        database_path=Path(os.getenv("DATABASE_PATH", "storage/time_manager.sqlite3")),
        timezone=timezone,
        default_repeat_minutes=_read_positive_int("DEFAULT_REPEAT_MINUTES", 5),
        checkin_after_minutes=_read_positive_int("CHECKIN_AFTER_MINUTES", 25),
        default_snooze_minutes=_read_positive_int("DEFAULT_SNOOZE_MINUTES", 10),
        ai_openai_enabled=_read_bool("AI_OPENAI_ENABLED", False),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=legacy_openai_model,
        openai_classifier_model=os.getenv("OPENAI_CLASSIFIER_MODEL", "gpt-5-nano"),
        openai_simple_model=os.getenv("OPENAI_SIMPLE_MODEL", "gpt-5-nano"),
        openai_complex_model=os.getenv("OPENAI_COMPLEX_MODEL", legacy_openai_model),
        openai_timeout_seconds=_read_positive_int("OPENAI_TIMEOUT_SECONDS", 8),
        openai_intent_confidence_threshold=_read_float("OPENAI_INTENT_CONFIDENCE_THRESHOLD", 0.55, 0.0, 1.0),
        openai_create_task_confidence_threshold=_read_float("OPENAI_CREATE_TASK_CONFIDENCE_THRESHOLD", 0.75, 0.0, 1.0),
        ai_default_tone=os.getenv("AI_DEFAULT_TONE", "bro"),
    )
