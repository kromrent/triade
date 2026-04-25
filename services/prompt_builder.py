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
- не долби одним и тем же советом. Если уже говорил "встань", "вода", "разомнись", не повторяй это без прямой причины
- если пользователь пишет "спасибо", "да, вернулся", "стало лучше", "понял", сначала нормально отреагируй на это, а не начинай новый дожим
- всегда учитывай время задачи. Если задача запланирована на будущее, не говори делать ее прямо сейчас
- если пользователь просит план на день, расставляй задачи по их времени напоминания и говори, что делать сейчас только если задача уже актуальна
- если задача стоит на завтра или другой будущий день, не включай ее как действие на сегодня; можно написать, что она запланирована позже
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


GENERAL_CHAT_SYSTEM_PROMPT = """
You are a natural Russian-speaking chat companion inside a Telegram bot.
Sound like a real person, not like a productivity dashboard.

Core behavior:
- if the user wants to talk, listen first;
- do not drag every message back to tasks;
- answer warmly, simply, and like a normal human;
- one natural follow-up question is better than a checklist;
- no coaching jargon, no canned motivation, no robotic tone.
""".strip()


SUPPORT_SYSTEM_PROMPT = """
You are a calm, human, Russian-speaking supportive companion.
The user may be tired, overloaded, or just wants to be heard.

Core behavior:
- start by acknowledging the feeling in plain language;
- do not jump straight into lists, schedules, or lectures;
- do not push tasks unless the user explicitly asks for that;
- if you suggest something, keep it optional and very small;
- sounding heard matters more than sounding productive.
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
        plain_chat = scenario == AIScenario.GENERAL_CHAT
        hide_task_context = _should_hide_task_context(
            scenario=scenario,
            low_energy=low_energy,
            about_task_list=about_task_list,
        )
        repeat_start_reminder = _is_repeat_start_reminder(user_text)
        energy_followup = _is_energy_followup(user_text)
        bro_boost = context.bro_boost_allowed and scenario in {
            AIScenario.PROCRASTINATION,
            AIScenario.COMEBACK,
            AIScenario.PANIC,
            AIScenario.BOOST,
        }
        system_prompt = _select_system_prompt(
            scenario=scenario,
            low_energy=low_energy,
            about_task_list=about_task_list,
        )

        style_note = self._build_style_note(
            bro_boost=bro_boost,
            low_energy=low_energy,
            about_task_list=about_task_list,
            scenario=scenario,
        )

        task_summary = _format_task_summary(context) if not hide_task_context else "hidden for this reply"
        active_tasks = _format_active_tasks_short(context) if not hide_task_context else "hidden for this reply"
        recent_dialog = _format_recent_dialog(context, include_assistant=not hide_task_context)
        goals = _format_goals(context) if not hide_task_context else "not relevant for this reply"
        motives = _format_motives(context) if not hide_task_context else "not relevant for this reply"
        plan_text = f"{plan_minutes} минут" if plan_minutes else "не нужен"

        user_prompt = "\n".join(
            [
                f"Сценарий: {scenario.value}",
                f"Локальная дата и время: {context.local_now.strftime('%d.%m.%Y %H:%M')}",
                f"Предпочтительный тон: {context.effective_tone.value}",
                f"Стиль ответа: {style_note}",
                f"Сегодня завершено задач: {context.completed_today}",
                f"Сегодня переносов: {context.snoozed_today}",
                f"Нужен план на: {plan_text}",
                f"Цели пользователя: {goals}",
                f"Причины / ради чего: {motives}",
                f"Текущая задача: {task_summary}",
                f"Ближайшие активные задачи:\n{active_tasks}",
                f"Недавний диалог:\n{recent_dialog}",
                "",
                f"Сообщение пользователя: {user_text}",
                "",
                _build_final_instruction(
                    scenario=scenario,
                    has_task=task is not None,
                    about_task_list=about_task_list,
                    low_energy=low_energy,
                    plain_chat=plain_chat,
                    bro_boost=bro_boost,
                    repeat_start_reminder=repeat_start_reminder,
                    energy_followup=energy_followup,
                ),
            ]
        )

        return [
            {"role": "system", "content": system_prompt},
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
                "мягкий режим: сначала по-человечески признать состояние и дать почувствовать, что пользователя услышали. "
                "Не скатывайся сразу в инструкцию. Один мягкий вопрос или одно очень маленькое предложение помощи лучше, чем список действий."
            )

        if about_task_list:
            return (
                "собранный режим: коротко разобрать список задач, "
                "помочь расставить порядок по времени. Если ближайшая задача запланирована позже, "
                "не заставляй делать ее сейчас; предложи подготовку или свободный слот до нее."
            )

        if scenario == AIScenario.GENERAL_CHAT:
            return (
                "обычный живой чат: естественно, по-человечески, можно как близкий друг. "
                "Если пользователь задает вопрос, ответь на вопрос. Не уводи в задачи и продуктивность без запроса."
            )

        return "обычный рабочий режим: коротко, естественно, по делу."


def _select_system_prompt(
    *,
    scenario: AIScenario,
    low_energy: bool,
    about_task_list: bool,
) -> str:
    if scenario == AIScenario.GENERAL_CHAT:
        return GENERAL_CHAT_SYSTEM_PROMPT
    if low_energy and not about_task_list:
        return SUPPORT_SYSTEM_PROMPT
    if scenario in {AIScenario.COMEBACK, AIScenario.PANIC} and not about_task_list:
        return SUPPORT_SYSTEM_PROMPT
    return SYSTEM_PROMPT


def _should_hide_task_context(
    *,
    scenario: AIScenario,
    low_energy: bool,
    about_task_list: bool,
) -> bool:
    if about_task_list:
        return False
    if scenario == AIScenario.GENERAL_CHAT:
        return True
    if low_energy:
        return True
    return scenario in {AIScenario.COMEBACK, AIScenario.PANIC, AIScenario.PROCRASTINATION}


def _build_final_instruction(
    *,
    scenario: AIScenario,
    has_task: bool,
    about_task_list: bool,
    low_energy: bool,
    plain_chat: bool,
    bro_boost: bool,
    repeat_start_reminder: bool,
    energy_followup: bool,
) -> str:
    if plain_chat:
        return (
            "Reply like a normal human in Russian. "
            "Do not drag this reply back to tasks or productivity unless the user explicitly asks. "
            "If the user wants to talk, listen first. "
            "A warm natural response or one gentle follow-up question is enough. "
            "Do not mention plans, reminders, tracks, backend, OpenAI, or settings."
        )

    if low_energy and not about_task_list:
        return (
            "The user sounds tired, overloaded, or emotionally done. "
            "Start by acknowledging that state in plain human language. "
            "Do not jump into a checklist, schedule, or lecture. "
            "Do not list tasks from context unless the user explicitly asks. "
            "If helpful, offer one optional tiny suggestion or one gentle follow-up question, not both."
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
            "Дай короткий порядок по времени напоминаний. "
            "Не предлагай делать сейчас задачу, которая запланирована на будущее. "
            "Если пользователь просит план на сегодня, задачи на завтра пометь отдельно, а не ставь их в текущий план."
        )

    if has_task:
        return (
            "Если вопрос относится к текущей задаче, отвечай именно по ней. "
            "Если время этой задачи еще не пришло, не заставляй стартовать сейчас; предложи подготовку или скажи, когда к ней вернуться. "
            "Не выдумывай детали, которых нет. "
            "Если информации мало, дай универсальный первый шаг."
        )

    return (
        "Ответь на конкретное сообщение пользователя, учитывая недавний диалог. "
        "Если человек завис, верни его к одному маленькому действию, но не повторяй одинаковые советы. "
        "Если задача запланирована на будущее, не говори делать ее прямо сейчас."
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
        _format_task_timing(task, context.local_now),
    ]

    if task.description:
        parts.append(f"описание={_clean(task.description)[:250]}")

    if context.task_snooze_count:
        parts.append(f"переносов={context.task_snooze_count}")

    if context.session_minutes is not None:
        parts.append(f"сессия={context.session_minutes} мин")

    return "; ".join(parts)


def _format_task_timing(task, local_now) -> str:
    reminder_local = task.start_reminder_at.astimezone(local_now.tzinfo)
    reminder_text = reminder_local.strftime("%d.%m %H:%M")
    day_text = _format_schedule_day(reminder_local, local_now)

    if task.status.value == "in_progress":
        return f"время={reminder_text} ({day_text}); уже начата"

    delta_seconds = int((reminder_local - local_now).total_seconds())
    if delta_seconds > 60:
        return f"время={reminder_text} ({day_text}); запланирована на будущее; осталось {_format_duration(delta_seconds)}"
    if delta_seconds < -60:
        return f"время={reminder_text} ({day_text}); время уже прошло; просрочено на {_format_duration(abs(delta_seconds))}"
    return f"время={reminder_text} ({day_text}); актуальна сейчас"


def _format_schedule_day(reminder_local, local_now) -> str:
    days = (reminder_local.date() - local_now.date()).days
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    if days == -1:
        return "вчера"
    if days > 1:
        return f"через {days} дн"
    return f"{abs(days)} дн назад"


def _format_duration(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    if rest == 0:
        return f"{hours} ч"
    return f"{hours} ч {rest} мин"


def _format_active_tasks_short(context: UserAIContext) -> str:
    if not context.active_tasks:
        return "нет"

    lines = []
    for task in context.active_tasks[:10]:
        marker = "текущая" if context.task is not None and task.id == context.task.id else "активная"
        lines.append(
            f"- {task.title} ({marker}) "
            f"(статус={task.status.value}, приоритет={task.priority.value}, "
            f"{_format_task_timing(task, context.local_now)})"
        )
    return "\n".join(lines)


def _format_recent_dialog(context: UserAIContext, include_assistant: bool = True) -> str:
    if not context.recent_dialog:
        return "нет"

    lines = []
    for user_text, _assistant_text in context.recent_dialog[-4:]:
        if user_text:
            lines.append(f"Пользователь: {_clean(user_text)[:250]}")
        if include_assistant and _assistant_text:
            lines.append(f"Ассистент: {_clean(_assistant_text)[:350]}")
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
