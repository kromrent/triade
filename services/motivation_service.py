from __future__ import annotations

from datetime import timedelta, timezone

from models import AIScenario, MotivationalTrack, Task, ToneMode
from services.ai_service import AIService
from services.tracks_service import TracksService
from services.user_context_service import UserContextService


class MotivationService:
    def __init__(
        self,
        ai_service: AIService,
        context_service: UserContextService,
        tracks_service: TracksService,
    ) -> None:
        self.ai_service = ai_service
        self.context_service = context_service
        self.tracks_service = tracks_service

    async def compose(
        self,
        telegram_user_id: int,
        scenario: AIScenario,
        task: Task | None = None,
        user_message: str | None = None,
        plan_minutes: int | None = None,
        tone_override: ToneMode | None = None,
    ) -> str:
        force_bro_boost = _should_force_bro_boost(scenario, user_message)
        context = self.context_service.build_context(
            telegram_user_id,
            task,
            scenario,
            tone_override=tone_override,
            force_bro_boost=force_bro_boost,
        )
        return await self.ai_service.generate(
            context,
            scenario,
            user_message=user_message,
            plan_minutes=plan_minutes,
        )

    async def compose_for_latest_task(
        self,
        telegram_user_id: int,
        scenario: AIScenario,
        user_message: str | None = None,
        plan_minutes: int | None = None,
    ) -> tuple[str, Task | None]:
        task = self.context_service.latest_active_task(telegram_user_id)
        text = await self.compose(
            telegram_user_id,
            scenario,
            task=task,
            user_message=user_message,
            plan_minutes=plan_minutes,
        )
        return text, task

    async def compose_boost(self, telegram_user_id: int, task: Task | None = None) -> tuple[str, MotivationalTrack | None]:
        text = await self.compose(telegram_user_id, AIScenario.BOOST, task=task)
        track = self.tracks_service.random_focus_file(telegram_user_id)
        return text, track

    def should_send_proactive(
        self,
        telegram_user_id: int,
        scenario: AIScenario = AIScenario.ENCOURAGEMENT,
        cooldown_minutes: int = 60,
    ) -> bool:
        preference = self.context_service.database.get_or_create_user_preference(
            telegram_user_id,
            ToneMode.BRO,
        )
        if not preference.ai_enabled:
            return False

        context = self.context_service.build_context(
            telegram_user_id,
            None,
            scenario,
        )
        since_utc = (context.local_now - timedelta(minutes=cooldown_minutes)).astimezone(timezone.utc)
        if self.context_service.database.has_recent_ai_interaction(telegram_user_id, since_utc):
            return False
        return True


def detect_procrastination_scenario(text: str) -> AIScenario | None:
    normalized = text.lower()
    low_energy = [
        "ничего не хочу",
        "ничего не хочется",
        "нет желания",
        "нет настроения",
        "нет сил",
        "нет ресурса",
        "апатия",
    ]
    if any(word in normalized for word in low_energy):
        return AIScenario.COMEBACK

    patterns = {
        AIScenario.PANIC: [
            "хаос",
            "очень много",
            "все горит",
            "не понимаю что делать",
            "не понимаю, что делать",
        ],
        AIScenario.PROCRASTINATION: [
            "не могу начать",
            "не хочу",
            "потом",
            "сливаю",
            "опять все сливаю",
            "страшно открывать",
            "боюсь",
            "не знаю с чего начать",
            "не знаю, с чего начать",
        ],
        AIScenario.COMEBACK: [
            "отвлекся",
            "отвлеклась",
            "залип",
            "залипла",
            "выпал",
            "выпала",
        ],
    }
    for scenario, words in patterns.items():
        if any(word in normalized for word in words):
            return scenario
    return None


def _should_force_bro_boost(scenario: AIScenario, user_message: str | None) -> bool:
    if scenario not in {
        AIScenario.PROCRASTINATION,
        AIScenario.COMEBACK,
        AIScenario.PANIC,
        AIScenario.BOOST,
    }:
        return False

    normalized = " ".join((user_message or "").strip().lower().split())
    if not normalized:
        return scenario == AIScenario.BOOST

    strong_triggers = [
        "ничего не хочу",
        "ничего не хочется",
        "не могу начать",
        "не могу заставить себя",
        "нет сил",
        "нет желания",
        "нет настроения",
        "сливаюсь",
        "сливаю",
        "опять все сливаю",
        "опять всё сливаю",
        "дай пинок",
        "мотивируй",
    ]
    return any(trigger in normalized for trigger in strong_triggers)
