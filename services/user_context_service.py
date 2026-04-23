from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from config import Settings
from database import Database
from models import (
    AIScenario,
    MotivationEntry,
    Task,
    ToneMode,
    UserGoal,
    UserPreference,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class UserAIContext:
    telegram_user_id: int
    task: Task | None
    active_tasks: list[Task]
    recent_dialog: list[tuple[str, str]]
    bro_boost_allowed: bool
    preference: UserPreference
    effective_tone: ToneMode
    goals: list[UserGoal]
    motivation_entries: list[MotivationEntry]
    task_snooze_count: int
    completed_today: int
    snoozed_today: int
    session_minutes: int | None
    local_now: datetime


class UserContextService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def build_context(
        self,
        telegram_user_id: int,
        task: Task | None,
        scenario: AIScenario,
        tone_override: ToneMode | None = None,
        force_bro_boost: bool = False,
    ) -> UserAIContext:
        default_tone = _parse_tone(self.settings.ai_default_tone)
        preference = self.database.get_or_create_user_preference(telegram_user_id, default_tone)
        task_snooze_count = (
            self.database.count_task_events(task.id, "task_snoozed")
            if task is not None
            else 0
        )
        local_now = utc_now().astimezone(ZoneInfo(self.settings.timezone))
        start_of_day = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
        start_of_day_utc = start_of_day.astimezone(timezone.utc)
        completed_today = self.database.count_completed_tasks_since(telegram_user_id, start_of_day_utc)
        snoozed_today = self.database.count_user_events_since(
            telegram_user_id,
            "task_snoozed",
            start_of_day_utc,
        )

        return UserAIContext(
            telegram_user_id=telegram_user_id,
            task=task,
            active_tasks=self.database.list_tasks_for_user(telegram_user_id, limit=10),
            recent_dialog=self.database.list_recent_ai_messages(telegram_user_id, limit=8),
            bro_boost_allowed=self._bro_boost_allowed(scenario, task_snooze_count, snoozed_today, force_bro_boost),
            preference=preference,
            effective_tone=self._choose_tone(preference, task_snooze_count, scenario, tone_override),
            goals=self.database.list_user_goals(telegram_user_id),
            motivation_entries=self.database.list_motivation_entries(telegram_user_id),
            task_snooze_count=task_snooze_count,
            completed_today=completed_today,
            snoozed_today=snoozed_today,
            session_minutes=_session_minutes(task),
            local_now=local_now,
        )

    def latest_active_task(self, telegram_user_id: int) -> Task | None:
        return self.database.get_latest_active_task_for_user(telegram_user_id)

    @staticmethod
    def _choose_tone(
        preference: UserPreference,
        task_snooze_count: int,
        scenario: AIScenario,
        tone_override: ToneMode | None,
    ) -> ToneMode:
        if tone_override is not None:
            return tone_override
        if task_snooze_count >= 3 and scenario in {
            AIScenario.START,
            AIScenario.FOCUS,
            AIScenario.PROCRASTINATION,
            AIScenario.COMEBACK,
            AIScenario.START_STEP,
        }:
            return ToneMode.TOUGH
        return preference.tone_mode

    @staticmethod
    def _bro_boost_allowed(
        scenario: AIScenario,
        task_snooze_count: int,
        snoozed_today: int,
        force_bro_boost: bool,
    ) -> bool:
        if force_bro_boost:
            return True

        eligible = scenario in {
            AIScenario.PROCRASTINATION,
            AIScenario.COMEBACK,
            AIScenario.PANIC,
            AIScenario.START_STEP,
        } or task_snooze_count >= 3 or snoozed_today >= 3
        return eligible and random.random() < 0.08


def _parse_tone(value: str) -> ToneMode:
    try:
        return ToneMode(value)
    except ValueError:
        return ToneMode.BRO


def _session_minutes(task: Task | None) -> int | None:
    if task is None or task.started_at is None:
        return None
    seconds = max(0, int((utc_now() - task.started_at).total_seconds()))
    return max(1, seconds // 60)
