from __future__ import annotations

from html import unescape

from models import AIScenario
from services.user_context_service import UserAIContext


SYSTEM_PROMPT = """
Ты - живой, адекватный, коротко отвечающий AI-ассистент для продуктивности.

Твой стиль:
- пиши по-человечески
- коротко
- разделяй смысловые куски пустой строкой, не лепи все в один абзац
- без канцелярита
- без длинных списков, если их не просили
- без психологии
- без пафосных мотивационных речей
- не как коуч, не как статья, не как приложение для медитации

Как ты помогаешь:
- понимаешь, что хочет пользователь
- отвечаешь естественно и по делу
- если пользователь сливается или не может начать, мягко или чуть жестче возвращаешь к действию
- если уместно, даешь ОДИН конкретный следующий шаг
- иногда можно сказать "брат"
- трек упоминай только если пользователь прямо просит трек/видос/буст или если в промте явно сказано, что трек будет ниже
- если пользователь просто разговаривает или отвечает на твой вопрос, продолжай диалог как нормальный собеседник
- не выдумывай детали, которых нет в контексте

Ограничения:
- обычно 1-4 коротких абзаца максимум
- в обычном чате можно ответить чуть подробнее, если пользователь спрашивает что-то по сути
- можно делать короткие отдельные строки вроде: "Стартани." или "Давай один шаг."
- не заканчивай мотивационный ответ вопросом "Сделал?"; контрольный вопрос бот отправит отдельно через минуту, если пользователь промолчит
- не используй длинные мотивационные монологи
- не повторяй банальности
- не стыди пользователя
- не унижай пользователя
- не используй формулировки "вход на 5 минут", "легкий вход", "пятиминутный вход"; говори "начни на 5 минут", "первые 5 минут", "самый простой старт" или "микрошаг"
- не называй конкретные треки и артистов; если нужен трек, напиши "включай трек ниже" или "держи трек ниже"
- не показывай пользователю внутренние номера задач: не пиши "#15", "#16", "#17" и любые похожие ID
- если упоминаешь задачи из контекста, называй их только по названию
- сохраняй названия задач ровно как в контексте; не переводи, не транслитерируй и не смешивай латиницу с кириллицей
- не используй странный сленг вроде "коток", "каток", "мув", "вайб", "имба", если пользователь сам так не написал
""".strip()


PromptMessage = dict[str, str]


class PromptBuilder:
    def build(
        self,
        context: UserAIContext,
        scenario: AIScenario,
        user_message: str | None = None,
        plan_minutes: int | None = None,
    ) -> list[PromptMessage]:
        user_text = _clean(user_message) if user_message else "нет"
        task = context.task

        about_task_list = _asks_about_task_list(user_text)
        low_energy = _is_low_energy_message(user_text)
        repeat_start_reminder = _is_repeat_start_reminder(user_text)
        energy_followup = _is_energy_followup(user_text)
        bro_boost = context.bro_boost_allowed and scenario in {
            AIScenario.PROCRASTINATION,
            AIScenario.COMEBACK,
            AIScenario.PANIC,
            AIScenario.BOOST,
        }

        style_note = self._build_style_note(
            bro_boost=bro_boost,
            low_energy=low_energy,
            about_task_list=about_task_list,
            scenario=scenario,
        )

        task_summary = _format_task_summary(context)
        active_tasks = _format_active_tasks_short(context)
        recent_user_messages = _format_recent_user_messages(context)
        goals = _format_goals(context)
        motives = _format_motives(context)
        plan_text = f"{plan_minutes} минут" if plan_minutes else "не нужен"

        user_prompt = "\n".join(
            [
                f"Сценарий: {scenario.value}",
                f"Локальное время: {context.local_now.strftime('%H:%M')}",
                f"Предпочтительный тон: {context.effective_tone.value}",
                f"Стиль ответа: {style_note}",
                f"Сегодня завершено задач: {context.completed_today}",
                f"Сегодня переносов: {context.snoozed_today}",
                f"Нужен план на: {plan_text}",
                f"Цели пользователя: {goals}",
                f"Причины / ради чего: {motives}",
                f"Текущая задача: {task_summary}",
                f"Ближайшие активные задачи:\n{active_tasks}",
                f"Недавние сообщения пользователя:\n{recent_user_messages}",
                "",
                f"Сообщение пользователя: {user_text}",
                "",
                _build_final_instruction(
                    scenario=scenario,
                    has_task=task is not None,
                    about_task_list=about_task_list,
                    bro_boost=bro_boost,
                    repeat_start_reminder=repeat_start_reminder,
                    energy_followup=energy_followup,
                ),
            ]
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _build_style_note(
        self,
        *,
        bro_boost: bool,
        low_energy: bool,
        about_task_list: bool,
        scenario: AIScenario,
    ) -> str:
        if bro_boost:
            return (
                "bro-режим: живо, по-человечески, чуть эмоциональнее обычного. "
                "Можно коротко поддержать, чуть надавить и сказать 'брат'. "
                "Без пафоса, без длинной речи. Не расписывай выполнение конкретной последней задачи, "
                "если пользователь просто сливается. Делай короткие абзацы с пустыми строками."
            )

        if low_energy:
            return (
                "мягкий режим: коротко показать, что понимаешь состояние, "
                "а потом вернуть к одному маленькому действию."
            )

        if about_task_list:
            return (
                "собранный режим: коротко разобрать список задач, "
                "помочь расставить порядок и дать ближайший старт."
            )

        if scenario == AIScenario.GENERAL_CHAT:
            return (
                "обычный живой чат: естественно, по-человечески, можно как близкий друг. "
                "Если пользователь задает вопрос, ответь на вопрос. Не уводи в задачи и продуктивность без запроса."
            )

        return "обычный рабочий режим: коротко, естественно, по делу."


def _build_final_instruction(
    *,
    scenario: AIScenario,
    has_task: bool,
    about_task_list: bool,
    bro_boost: bool,
    repeat_start_reminder: bool,
    energy_followup: bool,
) -> str:
    if scenario == AIScenario.GENERAL_CHAT:
        return (
            "Ответь прямо на сообщение пользователя. "
            "Если это обычный чат, не уводи разговор в задачи и продуктивность без запроса. "
            "Если пользователь отвечает на твой предыдущий вопрос, продолжи именно эту ветку диалога. "
            "Не предлагай трек и не запускай мотивационный режим без явной просьбы."
        )

    if bro_boost:
        return (
            "Пользователь сливается или просит пинок. Не разбирай подробно, как выполнить одну задачу. "
            "Оцени общий фон: задач может быть несколько, они могут давить кучей, но их можно вывезти маленькими блоками. "
            "Если называешь задачи, пиши только их названия без номеров. "
            "Не пиши ID задач вроде #15 и не коверкай кириллицу: «диплом» должен оставаться «диплом», не «diplom» и не «dиплом». "
            "Дай поддержку в стиле близкого брата, коротко надави без унижения и предложи один нейтральный старт: "
            "встать, вода, открыть список, выбрать самое легкое действие и начать на 5 минут. "
            "Не используй слово 'вход'. Не придумывай конкретный трек или артиста. "
            "Не обещай трек ниже, если пользователь прямо не просил трек или видос. "
            "Не спрашивай 'Сделал?' в этом сообщении."
        )

    if energy_followup:
        return (
            "Это follow-up через минуту после мотивационного трека. Пользователь ничего не написал. "
            "Сгенерируй очень короткое живое сообщение, без плана и без лекции. "
            "Смысл: спросить, как он, стало ли чуть больше энергии, и мягко подтолкнуть написать ответ. "
            "Пример тона, не копируй дословно: «Ну что, как? Энергии подприбавилось?» "
            "1-2 коротких предложения максимум."
        )

    if repeat_start_reminder:
        return (
            "Это повторное напоминание о старте задачи: первое обычное напоминание пользователь уже проигнорировал. "
            "Не повторяй стандартный шаблон. Напиши короткий AI-дожим по текущей задаче: живо, спокойно, чуть прямее. "
            "Упомяни название задачи без номера. Не расписывай подробный план выполнения. "
            "Дай один простой старт: открыть задачу, поставить таймер или начать первые 5 минут. "
            "В конце можно написать отдельной строкой: «Начал?»"
        )

    if about_task_list:
        return (
            "Разбери именно список задач из контекста. "
            "Не зацикливайся на одной последней задаче. "
            "Дай короткий порядок и ближайший старт."
        )

    if has_task:
        return (
            "Если вопрос относится к текущей задаче, отвечай именно по ней. "
            "Не выдумывай детали, которых нет. "
            "Если информации мало, дай универсальный первый шаг."
        )

    return (
        "Ответь коротко и практично. "
        "Если человек завис, верни его к одному маленькому действию."
    )


def _clean(value: str) -> str:
    return unescape(" ".join(value.strip().split()))[:1000]


def _format_task_summary(context: UserAIContext) -> str:
    task = context.task
    if task is None:
        return "нет"

    parts = [
        task.title,
        f"статус={task.status.value}",
        f"приоритет={task.priority.value}",
    ]

    if task.description:
        parts.append(f"описание={_clean(task.description)[:250]}")

    if context.task_snooze_count:
        parts.append(f"переносов={context.task_snooze_count}")

    if context.session_minutes is not None:
        parts.append(f"сессия={context.session_minutes} мин")

    return "; ".join(parts)


def _format_active_tasks_short(context: UserAIContext) -> str:
    if not context.active_tasks:
        return "нет"

    lines = []
    for task in context.active_tasks[:3]:
        marker = "текущая" if context.task is not None and task.id == context.task.id else "активная"
        lines.append(
            f"- {task.title} ({marker}) "
            f"(статус={task.status.value}, приоритет={task.priority.value})"
        )
    return "\n".join(lines)


def _format_recent_user_messages(context: UserAIContext) -> str:
    if not context.recent_dialog:
        return "нет"

    lines = []
    for user_text, _assistant_text in context.recent_dialog[-4:]:
        if user_text:
            lines.append(f"- {_clean(user_text)[:250]}")
    return "\n".join(lines) if lines else "нет"


def _format_goals(context: UserAIContext) -> str:
    if not context.goals:
        return "не заданы"
    return "; ".join(_clean(goal.text)[:120] for goal in context.goals[:3])


def _format_motives(context: UserAIContext) -> str:
    if not context.motivation_entries:
        return "не заданы"
    return "; ".join(_clean(entry.text)[:120] for entry in context.motivation_entries[:3])


def _asks_about_task_list(user_text: str) -> bool:
    normalized = user_text.lower()
    if not any(word in normalized for word in ["задач", "дела", "дело", "напоминан"]):
        return False

    return any(
        phrase in normalized
        for phrase in [
            "мои задачи",
            "моих задач",
            "другие задачи",
            "других задач",
            "остальные задачи",
            "остальных задач",
            "все задачи",
            "все мои задачи",
        ]
    )


def _is_low_energy_message(user_text: str) -> bool:
    normalized = user_text.lower()
    return any(
        phrase in normalized
        for phrase in [
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
    )


def _is_repeat_start_reminder(user_text: str) -> bool:
    normalized = user_text.lower()
    return "повторное напоминание" in normalized and "не нажал" in normalized


def _is_energy_followup(user_text: str) -> bool:
    normalized = user_text.lower()
    return "прошла минута после мотивационного трека" in normalized
