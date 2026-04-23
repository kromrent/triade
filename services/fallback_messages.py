from __future__ import annotations

import random

from models import AIScenario, ToneMode
from services.user_context_service import UserAIContext


FALLBACK_MESSAGES: dict[AIScenario, dict[ToneMode, list[str]]] = {
    AIScenario.START: {
        ToneMode.SUPPORTIVE: [
            "Не надо тащить всю задачу сразу. Открой ее и сделай первый маленький шаг.",
            "Спокойно. Сейчас нужна не идеальная готовность, а вход в задачу на 5 минут.",
        ],
        ToneMode.BALANCED: [
            "Начни с открытия материалов. Дальше один конкретный шаг, без разгона.",
            "Сейчас задача не про настроение. Открой и сделай первые 10 минут.",
        ],
        ToneMode.TOUGH: [
            "Еще один перенос только сдвинет давление на вечер. Открывай задачу и делай первый шаг.",
            "10 минут работы сейчас лучше часа давления потом. Открывай задачу и делай самый маленький шаг.",
        ],
        ToneMode.BRO: [
            "Бро, без героизма. Открыл задачу, сделал первый шаг, дальше разберемся.",
            "Не думай про весь объем. Включайся на 10 минут и просто входи.",
        ],
    },
    AIScenario.FOCUS: {
        ToneMode.SUPPORTIVE: [
            "Вернись мягко: убери лишнее, оставь один экран и сделай следующий шаг.",
            "Ты не обязан быть в идеальном состоянии. Достаточно снова положить внимание на задачу.",
        ],
        ToneMode.BALANCED: [
            "Стоп лишнее. Один экран, один шаг, 10 минут фокуса.",
            "Верни задачу в центр: что можно сделать за ближайшие 5 минут?",
        ],
        ToneMode.TOUGH: [
            "Сейчас не время расплываться. Закрой отвлекающее и сделай один измеримый шаг.",
            "Фокус обратно. Не обсуждаем с собой, делаем ближайшее действие.",
        ],
        ToneMode.BRO: [
            "Бро, ловим руль обратно. Один таб, один шаг, поехали.",
            "Окей, отвлекся. Бывает. Теперь обратно: 5 минут чистого движения.",
        ],
    },
    AIScenario.PROCRASTINATION: {
        ToneMode.SUPPORTIVE: [
            "Похоже, задача давит объемом. Сожми ее до первого действия: открыть, прочитать, написать одну строку.",
            "Не надо побеждать прокрастинацию целиком. Нужно только начать маленько и честно.",
        ],
        ToneMode.BALANCED: [
            "Разбей сопротивление: открой задачу и сделай самый простой кусок за 5 минут.",
            "Если страшно начинать, значит старт слишком большой. Уменьши шаг до смешного.",
        ],
        ToneMode.TOUGH: [
            "Ты уже знаешь, что «потом» не разгружает. Открывай и делай 5 минут, без переговоров.",
            "Не нужно желание. Нужен первый шаг. Сделай его сейчас, даже криво.",
        ],
        ToneMode.BRO: [
            "Бро, мозг торгуется, но мы не покупаем. Открой задачу и сделай мини-шаг.",
            "Все, хватит висеть в предбаннике. Входи на 5 минут, дальше легче.",
        ],
    },
    AIScenario.COMPLETION: {
        ToneMode.SUPPORTIVE: [
            "Готово. Нормально сработано. Выдохни и дай себе пару минут паузы.",
            "Задача закрыта. Зафиксируй это и не обесценивай результат.",
        ],
        ToneMode.BALANCED: [
            "Готово. Хороший ход. Теперь либо короткий отдых, либо следующая маленькая задача.",
            "Закрыто. Сохрани темп: пауза 5 минут или один следующий простой шаг.",
        ],
        ToneMode.TOUGH: [
            "Готово. Вот это уже действие, а не разговоры. Зафиксируй и двигайся дальше разумно.",
            "Задача закрыта. Не сливай инерцию: отдых по таймеру или следующий маленький шаг.",
        ],
        ToneMode.BRO: [
            "Есть, бро. Закрыл задачу. Выдохни и реши: пауза или следующий маленький кусок.",
            "Нормально залетел. Не обесценивай: задача реально закрыта.",
        ],
    },
    AIScenario.WHY: {
        ToneMode.SUPPORTIVE: [
            "Ты делаешь это не ради идеальности. Ты собираешь себе более спокойную и управляемую жизнь.",
        ],
        ToneMode.BALANCED: [
            "Смысл простой: меньше хаоса, больше контроля, больше результата. Сейчас важен следующий шаг.",
        ],
        ToneMode.TOUGH: [
            "Если это важно для твоей жизни, оно не сдвинется само. Один шаг сейчас.",
        ],
        ToneMode.BRO: [
            "Бро, это не просто задача. Это кирпичик в жизнь, где ты меньше тушишь пожары.",
        ],
    },
    AIScenario.BOOST: {
        ToneMode.SUPPORTIVE: [
            "Включай трек и заходи мягко: первые 5 минут без требований к идеальности.",
        ],
        ToneMode.BALANCED: [
            "Запускай трек и сразу открывай задачу. Подготовка закончилась.",
        ],
        ToneMode.TOUGH: [
            "Трек включил, задачу открыл. Без прогулок вокруг старта.",
        ],
        ToneMode.BRO: [
            "Вот буст на блок. Включай и не зависай на выборе настроя.",
        ],
    },
}


def build_fallback_response(
    context: UserAIContext,
    scenario: AIScenario,
    user_message: str | None = None,
    plan_minutes: int | None = None,
    recent_responses: list[str] | None = None,
) -> str:
    if scenario == AIScenario.GENERAL_CHAT:
        return _general_chat(context, user_message)
    if scenario == AIScenario.BREAKDOWN:
        return _breakdown(context)
    if scenario == AIScenario.START_STEP:
        return _start_step(context)
    if scenario == AIScenario.PLAN:
        return _plan(context, plan_minutes or 15)
    if scenario == AIScenario.ADVICE or scenario == AIScenario.HELP_TASK:
        return _advice(context, user_message)
    if scenario == AIScenario.PANIC:
        return _panic(context)
    if scenario in {AIScenario.COMEBACK, AIScenario.PROCRASTINATION} and _is_low_energy_message(user_message):
        return _low_energy(context)
    if scenario == AIScenario.ENCOURAGEMENT:
        return _pick(context, AIScenario.START, recent_responses)
    if scenario == AIScenario.COMEBACK:
        return _pick(context, AIScenario.FOCUS, recent_responses)

    return _pick(context, scenario, recent_responses)


def _pick(
    context: UserAIContext,
    scenario: AIScenario,
    recent_responses: list[str] | None,
) -> str:
    scenario_messages = FALLBACK_MESSAGES.get(scenario) or FALLBACK_MESSAGES[AIScenario.START]
    messages = scenario_messages.get(context.effective_tone) or scenario_messages[ToneMode.BRO]
    recent = set(recent_responses or [])
    candidates = [message for message in messages if message not in recent] or messages
    message = random.choice(candidates)
    why = _why(context)
    if scenario == AIScenario.WHY and why:
        return f"{message}\n\nТвой ориентир: {why}\nСледующий шаг: {_first_action(context)}"
    return message


def _breakdown(context: UserAIContext) -> str:
    title = _task_title(context)
    return (
        f"Разбиваем «{title}» без лишней философии:\n"
        "1. Открыть материалы.\n"
        "2. Найти самый маленький видимый кусок.\n"
        "3. Сделать его за 10 минут.\n"
        "4. После этого решить следующий шаг."
    )


def _start_step(context: UserAIContext) -> str:
    return f"Самый маленький шаг: {_first_action(context)}. Сделай только его, не всю задачу."


def _plan(context: UserAIContext, minutes: int) -> str:
    if minutes <= 15:
        return (
            "План на 15 минут:\n"
            "1. 2 минуты: открыть материалы и убрать лишнее.\n"
            "2. 10 минут: сделать один конкретный кусок.\n"
            "3. 3 минуты: зафиксировать, что дальше."
        )
    if minutes <= 30:
        return (
            "План на 30 минут:\n"
            "1. 5 минут: вход и выбор маленького результата.\n"
            "2. 20 минут: один фокус-блок.\n"
            "3. 5 минут: сохранить прогресс и выбрать следующий шаг."
        )
    return (
        "План на 60 минут:\n"
        "1. 5 минут: подготовка без залипания.\n"
        "2. 25 минут: первый фокус-блок.\n"
        "3. 5 минут: короткая пауза.\n"
        "4. 20 минут: второй кусок.\n"
        "5. 5 минут: закрыть хвосты и записать итог."
    )


def _advice(context: UserAIContext, user_message: str | None) -> str:
    normalized = " ".join((user_message or "").strip().lower().split())
    if context.active_tasks and any(word in normalized for word in ["задач", "дела", "дело", "остальные"]):
        lines = ["Я бы шел не по настроению, а по ближайшему старту:"]
        for index, task in enumerate(context.active_tasks[:5], start=1):
            reminder_time = task.start_reminder_at.astimezone(context.local_now.tzinfo).strftime("%H:%M")
            lines.append(f"{index}. {task.title} — в {reminder_time}: первый шаг на 10 минут.")
        lines.append("")
        lines.append("Выбери первую по времени и начни с подготовки: открыть, надеть, достать, включить. Не думай про весь день.")
        return "\n".join(lines)

    if user_message:
        return (
            "Я бы не пытался решить все сразу.\n\n"
            "Сформулируй ближайший результат в одну строку, потом сделай первый шаг: "
            f"{_first_action(context)}"
        )
    return (
        "Совет простой: уменьши старт. Не «сделать задачу», а открыть материалы и "
        "сделать один кусок за 10 минут."
    )


def _panic(context: UserAIContext) -> str:
    return (
        "Окей, стоп. Сейчас не разгребаем всю жизнь.\n\n"
        "1. Вода или вдох.\n"
        "2. Один экран перед собой.\n"
        f"3. Первый шаг: {_first_action(context)}.\n\n"
        "Таймер на 5 минут. Этого достаточно, чтобы вернуться в управление."
    )


def _low_energy(context: UserAIContext) -> str:
    if context.bro_boost_allowed:
        return (
            "Брат, я тебя понимаю. Когда сил ноль, хочется просто выключиться и ничего не трогать.\n\n"
            "Но ты же сам знаешь: если сейчас лечь в это состояние, оно ничего за тебя не поменяет. "
            "Ты уже столько раз пытался собрать себя, и бросать все на входе - плохая сделка.\n\n"
            "Я рядом, но двигать это можешь только ты. Не весь день, не всю жизнь, не идеальный рывок - "
            "просто первый маленький вход.\n\n"
            "Встань, вода, умойся и открой одну задачу на 5 минут. Держи трек для мотивации и начинай."
        )

    if context.active_tasks:
        opener = "Брат, понял." if context.bro_boost_allowed else "Понял."
        return (
            f"{opener} Нет желания - бывает. Но если сейчас просто лечь в это состояние, оно само тебя не вытащит.\n\n"
            "Давай без героизма: встань, вода, лицо умыть. Это не “стать продуктивным”, это просто вернуть управление телу.\n\n"
            "Потом открой список задач и выбери самую легкую на 5 минут. Не делать весь день. Только войти."
        )

    return (
        "Слушай, понял. Когда ничего не хочется, не надо ждать настроение - оно часто приходит уже после старта.\n\n"
        "Давай самый маленький вход: вода, умыться, сесть нормально. Без планов на жизнь.\n\n"
        "После этого напиши мне одно слово: “готово”. Я дальше подкину следующий шаг."
    )


def _general_chat(context: UserAIContext, user_message: str | None) -> str:
    normalized = " ".join((user_message or "").strip().lower().split())
    if any(phrase in normalized for phrase in ["сколько сейчас времени", "сколько времени", "который час", "текущее время"]):
        return f"Сейчас {context.local_now.strftime('%H:%M')} по времени бота."

    if any(phrase in normalized for phrase in ["какая сегодня дата", "какое сегодня число", "какой сегодня день"]):
        return f"Сегодня {context.local_now.strftime('%d.%m.%Y')}."

    if normalized in {"привет", "здравствуй", "здравствуйте", "хай", "hello", "hi"} or normalized.startswith(("привет ", "здравствуй ", "здравствуйте ", "хай ")):
        return "Я тут, на связи. Нормально, держу фокус за нас двоих."

    if normalized in {"спасибо", "спс"}:
        return "Принял. Если что, кидай следующую задачу или пиши, где застряла."

    if any(phrase in normalized for phrase in ["что ты умеешь", "кто ты", "как тобой пользоваться"]):
        return (
            "Я веду задачи и помогаю не сливаться со старта.\n\n"
            "Пиши свободно: «напомни через 20 минут диплом», «я начал», "
            "«готово», «не могу начать», «дай пинок»."
        )

    if any(phrase in normalized for phrase in ["расскажи", "что нибудь интересное", "что-нибудь интересное", "интересный факт"]):
        return random.choice(
            [
                "Интересная штука: старт задачи часто тяжелее самой работы. Мозг сопротивляется входу, а не процессу.\n\nПопробуй правило 7 минут: начать настолько маленько, чтобы было почти глупо отказываться.",
                "Фокус обычно ломается не из-за слабой воли, а из-за слишком мутного следующего шага.\n\nЕсли шаг нельзя сделать прямо сейчас руками, он еще слишком большой.",
                "Хороший рабочий блок начинается не с мотивации, а с среды: один экран, один файл, один следующий шаг.\n\nЭто скучно, зато работает.",
            ]
        )

    return (
        "Я тут. Могу ответить как чат, помочь с задачей или собрать тебя в первый шаг.\n\n"
        "Если хочешь прям умные свободные ответы на любые темы, включи OpenAI в `.env`. "
        "Без него я отвечаю проще, но задачи и фокус веду нормально."
    )


def _is_low_energy_message(user_message: str | None) -> bool:
    normalized = " ".join((user_message or "").strip().lower().split())
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


def _first_action(context: UserAIContext) -> str:
    if context.task is None:
        return "выпиши одну задачу, которую можно начать за 5 минут"
    if context.task.description:
        return "открой описание задачи и выдели один маленький кусок"
    return f"открой все, что нужно для «{context.task.title}»"


def _task_title(context: UserAIContext) -> str:
    return context.task.title if context.task else "текущая задача"


def _why(context: UserAIContext) -> str | None:
    if context.goals:
        return random.choice(context.goals).text
    if context.motivation_entries:
        return random.choice(context.motivation_entries).text
    return None
