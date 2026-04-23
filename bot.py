from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Dispatcher
from aiogram.types import Message, TelegramObject

from config import Settings
from handlers import ai, common, tasks
from scheduler import ReminderScheduler
from services.intent_service import IntentService
from services.motivation_service import MotivationService
from services.task_service import TaskService
from services.tracks_service import TracksService
from services.user_context_service import UserContextService


class UserActivityMiddleware(BaseMiddleware):
    def __init__(self, reminder_scheduler: ReminderScheduler) -> None:
        self.reminder_scheduler = reminder_scheduler

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            self.reminder_scheduler.mark_user_activity(event.from_user.id)
        return await handler(event, data)


def create_dispatcher(
    task_service: TaskService,
    reminder_scheduler: ReminderScheduler,
    settings: Settings,
    motivation_service: MotivationService,
    tracks_service: TracksService,
    user_context_service: UserContextService,
    intent_service: IntentService,
) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.workflow_data.update(
        task_service=task_service,
        reminder_scheduler=reminder_scheduler,
        settings=settings,
        motivation_service=motivation_service,
        tracks_service=tracks_service,
        user_context_service=user_context_service,
        intent_service=intent_service,
    )
    dispatcher.message.middleware(UserActivityMiddleware(reminder_scheduler))
    dispatcher.include_routers(common.router, ai.router, tasks.router)
    return dispatcher
