from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot import create_dispatcher
from config import load_settings
from database import Database
from scheduler import ReminderScheduler
from services.ai_service import AIService
from services.daily_report_service import DailyReportService
from services.intent_service import IntentService
from services.motivation_service import MotivationService
from services.prompt_builder import PromptBuilder
from services.task_service import TaskService
from services.tracks_service import TracksService
from services.user_context_service import UserContextService


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    settings = load_settings()
    database = Database(settings.database_path)
    database.connect()
    database.init_schema()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    task_service = TaskService(database, settings)
    user_context_service = UserContextService(database, settings)
    tracks_service = TracksService(database)
    ai_service = AIService(settings, database, PromptBuilder())
    intent_service = IntentService()
    motivation_service = MotivationService(ai_service, user_context_service, tracks_service)
    daily_report_service = DailyReportService(task_service, settings)
    reminder_scheduler = ReminderScheduler(
        bot,
        task_service,
        settings,
        motivation_service,
        daily_report_service,
    )
    dispatcher = create_dispatcher(
        task_service,
        reminder_scheduler,
        settings,
        motivation_service,
        tracks_service,
        user_context_service,
        intent_service,
    )

    await reminder_scheduler.start()
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        reminder_scheduler.shutdown()
        await bot.session.close()
        database.close()


if __name__ == "__main__":
    asyncio.run(main())
