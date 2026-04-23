from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from config import Settings
from database import Database
from models import AIScenario
from services.ai_system_rules import AI_SYSTEM_INSTRUCTIONS, GENERAL_CHAT_SYSTEM_INSTRUCTIONS
from services.fallback_messages import build_fallback_response
from services.prompt_builder import PromptBuilder
from services.user_context_service import UserAIContext

logger = logging.getLogger(__name__)


class AIService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        prompt_builder: PromptBuilder,
    ) -> None:
        self.settings = settings
        self.database = database
        self.prompt_builder = prompt_builder
        self._client = None

        if settings.ai_openai_enabled and settings.openai_api_key:
            try:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(
                    api_key=settings.openai_api_key,
                    timeout=float(settings.openai_timeout_seconds),
                    max_retries=1,
                )
            except Exception as exc:
                logger.warning("OpenAI client disabled: %s", exc)

    @property
    def openai_available(self) -> bool:
        return self._client is not None

    async def classify_intent(
        self,
        user_message: str,
        local_now: datetime,
        timezone_name: str,
        conversation_context: str | None = None,
    ) -> dict[str, Any] | None:
        if self._client is None or not self.settings.ai_openai_enabled:
            return None

        prompt = _build_intent_classifier_prompt(user_message, local_now, timezone_name, conversation_context)
        try:
            response = await self._client.responses.create(
                model=self.settings.openai_classifier_model,
                instructions=INTENT_CLASSIFIER_INSTRUCTIONS,
                input=prompt,
                **_response_options(max_output_tokens=700),
            )
            return _parse_json_object(_extract_response_text(response))
        except Exception as exc:
            logger.warning("OpenAI intent classification failed, using rule fallback: %s", exc)
            return None

    async def plan_task_creation(
        self,
        user_message: str,
        local_now: datetime,
        timezone_name: str,
    ) -> dict[str, Any] | None:
        if self._client is None or not self.settings.ai_openai_enabled:
            return None

        prompt = _build_intent_classifier_prompt(user_message, local_now, timezone_name)
        try:
            response = await self._client.responses.create(
                model=self.settings.openai_classifier_model,
                instructions=FORCE_CREATE_TASK_INSTRUCTIONS,
                input=prompt,
                **_response_options(max_output_tokens=700),
            )
            return _parse_json_object(_extract_response_text(response))
        except Exception as exc:
            logger.warning("OpenAI task planning failed, using rule fallback: %s", exc)
            return None

    async def generate(
        self,
        context: UserAIContext,
        scenario: AIScenario,
        user_message: str | None = None,
        plan_minutes: int | None = None,
    ) -> str:
        recent = self.database.list_recent_ai_responses(context.telegram_user_id)
        fallback = build_fallback_response(
            context,
            scenario,
            user_message=user_message,
            plan_minutes=plan_minutes,
            recent_responses=recent,
        )

        if self._client is None or not context.preference.ai_enabled:
            self._log(context, scenario, "fallback", None, fallback, user_message=user_message)
            return fallback

        prompt = self.prompt_builder.build(context, scenario, user_message, plan_minutes)
        model = _select_response_model(self.settings, scenario, user_message, plan_minutes)
        try:
            response_options = _response_options_for(scenario, user_message, plan_minutes)
            request_payload = _build_response_payload(model, prompt, scenario)
            response = await self._client.responses.create(
                **request_payload,
                **response_options,
            )
            text = _clean_response(_extract_response_text(response))
            if not text:
                raise RuntimeError("OpenAI returned an empty response")
            self._log(context, scenario, f"openai:{model}", prompt, text, user_message=user_message)
            return text
        except Exception as exc:
            logger.warning("OpenAI request failed, using fallback: %s", exc)
            self._log(context, scenario, "fallback", prompt, fallback, error=str(exc), user_message=user_message)
            return fallback

    def _log(
        self,
        context: UserAIContext,
        scenario: AIScenario,
        provider: str,
        prompt: object | None,
        response: str,
        error: str | None = None,
        user_message: str | None = None,
    ) -> None:
        self.database.add_ai_interaction(
            telegram_user_id=context.telegram_user_id,
            task_id=context.task.id if context.task else None,
            scenario=scenario.value,
            tone_mode=context.effective_tone.value,
            provider=provider,
            user_message=user_message,
            prompt=_serialize_prompt(prompt),
            response=response,
            error=error,
        )


def _clean_response(value: str) -> str:
    text = value.strip()
    if len(text) <= 3000:
        return text
    return text[:3000].rstrip()


def _build_response_payload(model: str, prompt: object, scenario: AIScenario) -> dict[str, object]:
    if isinstance(prompt, list):
        instructions, input_messages = _split_system_message(prompt)
        payload: dict[str, object] = {
            "model": model,
            "input": input_messages,
        }
        payload["instructions"] = instructions or _select_system_instructions(scenario)
        return payload

    return {
        "model": model,
        "instructions": _select_system_instructions(scenario),
        "input": prompt,
    }


def _split_system_message(prompt: list[object]) -> tuple[str | None, list[object]]:
    instructions: str | None = None
    input_messages: list[object] = []
    for item in prompt:
        if (
            isinstance(item, dict)
            and item.get("role") == "system"
            and instructions is None
        ):
            instructions = str(item.get("content") or "")
            continue
        input_messages.append(item)
    return instructions, input_messages


def _serialize_prompt(prompt: object | None) -> str | None:
    if prompt is None:
        return None
    if isinstance(prompt, str):
        return prompt
    try:
        return json.dumps(prompt, ensure_ascii=False)
    except TypeError:
        return str(prompt)


def _select_system_instructions(scenario: AIScenario) -> str:
    if scenario == AIScenario.GENERAL_CHAT:
        return GENERAL_CHAT_SYSTEM_INSTRUCTIONS
    return AI_SYSTEM_INSTRUCTIONS


def _response_options(max_output_tokens: int, verbosity: str = "low") -> dict[str, object]:
    return {
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": "minimal"},
        "text": {"verbosity": verbosity},
    }


def _response_options_for(
    scenario: AIScenario,
    user_message: str | None,
    plan_minutes: int | None,
) -> dict[str, object]:
    text = (user_message or "").lower()
    discussion_words = [
        "обсуд",
        "поговор",
        "подроб",
        "объясни",
        "почему",
        "что со мной",
        "не хочу",
        "нет настроения",
        "нет желания",
        "нет сил",
        "ничего не хочется",
        "ничего не хочу",
    ]
    discussion_scenarios = {
        AIScenario.GENERAL_CHAT,
        AIScenario.COMEBACK,
        AIScenario.PROCRASTINATION,
        AIScenario.PANIC,
        AIScenario.ADVICE,
        AIScenario.HELP_TASK,
    }
    if scenario in discussion_scenarios or any(word in text for word in discussion_words):
        return _response_options(max_output_tokens=950, verbosity="medium")
    if plan_minutes:
        return _response_options(max_output_tokens=900, verbosity="medium")
    return _response_options(max_output_tokens=900, verbosity="low")


def _extract_response_text(response: object) -> str:
    direct = getattr(response, "output_text", None)
    if direct:
        return str(direct)

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(str(text))
                continue

            if isinstance(content, dict):
                value = content.get("text")
                if value:
                    chunks.append(str(value))

    return "\n".join(chunks).strip()


def _select_response_model(
    settings: Settings,
    scenario: AIScenario,
    user_message: str | None,
    plan_minutes: int | None,
) -> str:
    complex_scenarios = {
        AIScenario.PROCRASTINATION,
        AIScenario.COMEBACK,
        AIScenario.FOCUS,
        AIScenario.HELP_TASK,
        AIScenario.BREAKDOWN,
        AIScenario.START_STEP,
        AIScenario.PLAN,
        AIScenario.ADVICE,
        AIScenario.PANIC,
    }
    if scenario in complex_scenarios or plan_minutes:
        return settings.openai_complex_model

    text = (user_message or "").lower()
    complex_words = [
        "подробно",
        "разбери",
        "разбей",
        "план",
        "стратег",
        "сложно",
        "почему",
        "как лучше",
        "помоги",
    ]
    if len(text) > 260 or any(word in text for word in complex_words):
        return settings.openai_complex_model

    return settings.openai_simple_model


INTENT_CLASSIFIER_INSTRUCTIONS = """
Ты action planner для Telegram-бота задач.
Верни только JSON без Markdown.
Твоя задача - решить, какое backend-действие должен выполнить код, и передать параметры.
Не обещай пользователю, что действие выполнено. Ты только возвращаешь JSON-план.

Выбери intent:
- create_task: пользователь хочет создать/запланировать задачу.
- task_query: пользователь спрашивает про свои задачи, количество, список или историю.
- task_management: пользователь управляет текущей задачей: начал, готово, отложи, отмени, удали.
- procrastination: не может начать, сливается, откладывает, страшно.
- task_help: просит план, шаги, совет по одной задаче или по всем своим задачам.
- emotional_state: усталость, раздражение, нет сил, нет настроения, ничего не хочется.
- motivation: дай пинок, мотивируй, напомни зачем.
- general_chat: обычный вопрос или разговор, не действие с задачей.
- unknown: непонятно.

Поля JSON:
{
  "intent": "create_task|task_query|task_management|procrastination|task_help|emotional_state|motivation|general_chat|unknown",
  "confidence": 0.0-1.0,
  "task_title": string|null,
  "reminder_phrase": string|null,
  "tasks": [
    {
      "title": string,
      "reminder_phrase": string|null,
      "reminder_at": string|null,
      "repeat_every_minutes": number|null
    }
  ],
  "repeat_every_minutes": number|null,
  "action": "start|done|snooze|cancel|cancel_all"|null,
  "minutes": number|null,
  "query": "count_active|list_active|count_closed|list_history"|null,
  "scenario": "procrastination|comeback|panic|breakdown|start_step|plan|advice|why|boost|encouragement|general_chat"|null
}

Важные правила:
- Если пользователь просит добавить/создать/поставить/запланировать задачу или напоминание, intent обязан быть create_task.
- Вопросы вроде "сколько у меня активных задач", "какие задачи", "покажи задачи" это task_query, не create_task.
- Вопросы вроде "что думаешь насчет моих задач", "как лучше выполнить мои задачи", "что скажешь про остальные задачи", "а что насчет других задач" это task_help со scenario="advice", не task_query.
- "надо сделать диплом", "напомни через 20 минут диплом", "добавь задачу: проснуться завтра в 9", "добавь еще задачу пробежаться в 12" это create_task.
- Если пользователь пишет "добавь задачу", "добавь еще задачу", "создай задачу", "поставь задачу", "напомни" или "надо", не отвечай текстом "готово"; верни create_task.
- Если пользователь просит создать несколько задач, intent=create_task и заполни массив tasks. Не объединяй несколько задач в одну.
- Если пользователь пишет расписание или план с несколькими пунктами и временем, это create_task, даже если нет слова "добавь". Например: "план на завтра: встать в 10, умыться в 10:10, диплом в 11".
- Если пользователь просто прислал список действий с временами через запятую, считай это черновиком нескольких задач и верни create_task.
- Для каждой задачи передай title без времени. Время положи в reminder_phrase, например "завтра 09:00" или "через 20 минут".
- Если время не указано, reminder_phrase=null.
- Если дата относительная, учитывай местное время из промта.
- Если пользователь указал время без даты, выбери ближайшее будущее в местном часовом поясе. Например, если сейчас позже 09:00, "в 9 утра" означает завтра 09:00.
- Если пришлось додумать дату или слегка очистить формулировку, верни confidence 0.55-0.74: бот покажет черновик пользователю на подтверждение.
- Даже если формулировка кривая, но видно задачу и время, верни лучший черновик, а не unknown.
- "удали все задачи", "отмени все задачи", "убери все задачи" это task_management с action="cancel_all".
- "удали задачу", "отмени задачу" это task_management с action="cancel".
- "сколько сейчас времени", "что такое..." это general_chat.
- "ничего не хочу делать", "нет настроения", "нет желания", "нет сил" это emotional_state со scenario="comeback", а не жесткий procrastination.
- Если пользователь отвечает на предыдущий вопрос ассистента коротко: "да", "нет", "да прибавилось", "норм", "чуть лучше", "не особо", "пока нет" - это продолжение обычного диалога, intent=general_chat, если нет явной команды с задачей.
- Не запускай motivation/boost только потому, что в контексте был мотивационный трек. Для boost нужен явный текущий запрос пользователя: "дай пинок", "мотивируй", "кинь трек", "скинь видос", "дай буст".
- Если пользователь просто хочет поговорить, спрашивает мнение, задает общий вопрос или отвечает на реплику ассистента, выбирай general_chat. Бот ответит как собеседник, без создания задач и без отправки трека.
- Не придумывай задачу из вопроса.
""".strip()


FORCE_CREATE_TASK_INSTRUCTIONS = """
Ты action planner для Telegram-бота задач.
Пользователь явно просит создать задачу/напоминание.

Верни только JSON без Markdown. Никакого текста пользователю.

Формат:
{
  "intent": "create_task",
  "confidence": 0.0-1.0,
  "task_title": string|null,
  "reminder_phrase": string|null,
  "tasks": [
    {
      "title": string,
      "reminder_phrase": string|null,
      "reminder_at": string|null,
      "repeat_every_minutes": number|null
    }
  ],
  "repeat_every_minutes": number|null,
  "action": null,
  "minutes": null,
  "query": null,
  "scenario": null
}

Правила:
- Всегда intent="create_task".
- Если задач несколько, заполни tasks несколькими элементами.
- Не объединяй несколько задач в одну.
- Расписание или список действий с временами разбей на отдельные tasks. Например: "встать в 10, умыться в 10:10, диплом в 11" это три задачи.
- Если в сообщении есть общий день ("на завтра", "сегодня"), применяй его ко всем пунктам списка.
- title должен быть без времени и без слов "добавь задачу", "добавь еще задачу", "напомни", "поставь".
- Время положи в reminder_phrase: "завтра 09:00", "сегодня 15:30", "через 20 минут".
- Если время не указано, reminder_phrase=null.
- Учитывай местное время и часовой пояс из промта.
- Если пользователь указал время без даты, выбери ближайшее будущее в местном часовом поясе. Например, если сейчас позже 09:00, "в 9 утра" означает завтра 09:00.
- Если пришлось додумать дату или слегка очистить формулировку, ставь confidence 0.55-0.74, но все равно верни черновик.
- Если видны задача и время, не отказывайся. Верни лучший JSON-план, пользователь потом подтвердит или поправит.
- Не обещай, что задача создана. Только JSON-план.
""".strip()


def _build_intent_classifier_prompt(
    user_message: str,
    local_now: datetime,
    timezone_name: str,
    conversation_context: str | None = None,
) -> str:
    parts = [
        f"Местное время: {local_now.strftime('%Y-%m-%d %H:%M')}",
        f"Часовой пояс: {timezone_name}",
    ]
    if conversation_context:
        parts.append(f"Контекст последних реплик:\n{conversation_context.strip()[:1800]}")
    parts.append(f"Сообщение пользователя: {user_message.strip()[:1000]}")
    return "\n".join(parts)


def _parse_json_object(value: str) -> dict[str, Any] | None:
    text = value.strip()
    if not text:
        return None

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

    return parsed if isinstance(parsed, dict) else None
