from __future__ import annotations

import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Settings
from keyboards.inline import checkin_actions_keyboard, task_reminder_actions_keyboard
from models import AIScenario, Reminder, ReminderKind, Task, ToneMode, utc_now
from services.daily_report_service import DailyReportService
from services.motivation_service import MotivationService
from services.formatting import format_checkin, format_start_reminder
from services.task_service import TaskService

logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(
        self,
        bot: Bot,
        task_service: TaskService,
        settings: Settings,
        motivation_service: MotivationService | None = None,
        daily_report_service: DailyReportService | None = None,
    ) -> None:
        self.bot = bot
        self.task_service = task_service
        self.settings = settings
        self.motivation_service = motivation_service
        self.daily_report_service = daily_report_service
        self.scheduler = AsyncIOScheduler(timezone=settings.tzinfo)
        self._user_activity: dict[int, datetime] = {}

    async def start(self) -> None:
        self.scheduler.start()
        restored = 0
        for reminder in self.task_service.list_scheduled_reminders():
            self.schedule_reminder(reminder)
            restored += 1
        logger.info("Restored %s scheduled reminders", restored)
        self._schedule_daily_reports()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def schedule_reminder(self, reminder: Reminder) -> None:
        run_date = reminder.scheduled_at
        if run_date <= utc_now():
            run_date = utc_now() + timedelta(seconds=1)

        self.scheduler.add_job(
            self._dispatch_reminder,
            trigger="date",
            run_date=run_date,
            args=[reminder.id],
            id=self._job_id(reminder.id),
            replace_existing=True,
            misfire_grace_time=300,
        )

    def mark_user_activity(self, telegram_user_id: int) -> None:
        self._user_activity[telegram_user_id] = utc_now()

    def schedule_energy_followup(
        self,
        telegram_user_id: int,
        chat_id: int,
        task_id: int | None = None,
        delay_seconds: int = 60,
    ) -> None:
        if self.motivation_service is None:
            return

        scheduled_from = utc_now()
        self.scheduler.add_job(
            self._send_energy_followup,
            trigger="date",
            run_date=scheduled_from + timedelta(seconds=delay_seconds),
            args=[telegram_user_id, chat_id, scheduled_from.isoformat(), task_id],
            id=f"energy_followup:{telegram_user_id}",
            replace_existing=True,
            misfire_grace_time=60,
        )

    async def _send_energy_followup(
        self,
        telegram_user_id: int,
        chat_id: int,
        scheduled_from_raw: str,
        task_id: int | None,
    ) -> None:
        if self.motivation_service is None:
            return

        scheduled_from = datetime.fromisoformat(scheduled_from_raw)
        last_activity = self._user_activity.get(telegram_user_id)
        if last_activity is not None and last_activity > scheduled_from:
            return

        task = None
        if task_id is not None:
            task = self.task_service.database.get_task(task_id)

        try:
            text = await self.motivation_service.compose(
                telegram_user_id,
                AIScenario.COMEBACK,
                task=task,
                user_message=(
                    "Прошла минута после мотивационного трека, пользователь ничего не написал. "
                    "Спроси коротко и живо, стало ли больше энергии, без лекции и без плана. "
                    "Пример направления: «Ну что, как? Энергии подприбавилось?»"
                ),
            )
            await self.bot.send_message(chat_id=chat_id, text=escape(text))
        except TelegramAPIError as exc:
            logger.warning("Failed to send energy follow-up to user %s: %s", telegram_user_id, exc)

    def _schedule_daily_reports(self) -> None:
        if self.daily_report_service is None:
            return
        self.scheduler.add_job(
            self._send_daily_reports,
            trigger="cron",
            hour=22,
            minute=0,
            id="daily_report:22:00",
            replace_existing=True,
            misfire_grace_time=900,
        )
        logger.info("Scheduled daily reports at 22:00")

    async def send_daily_report(self, telegram_user_id: int, chat_id: int, force: bool = False) -> bool:
        if self.daily_report_service is None:
            return False

        report = self.daily_report_service.build_report(telegram_user_id, chat_id)
        if not force and not report.has_activity:
            return False

        text = self.daily_report_service.format_report(report)
        chart_path = self.daily_report_service.render_chart(report)
        await self.bot.send_message(chat_id=chat_id, text=text)
        if chart_path is not None:
            await self.bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(chart_path),
                caption="График дня",
            )
        return True

    async def _send_daily_reports(self) -> None:
        if self.daily_report_service is None:
            return

        sent = 0
        for telegram_user_id, chat_id in self.daily_report_service.recipients():
            try:
                if await self.send_daily_report(telegram_user_id, chat_id):
                    sent += 1
            except TelegramAPIError as exc:
                logger.warning("Failed to send daily report to user %s: %s", telegram_user_id, exc)
        logger.info("Daily reports sent: %s", sent)

    async def _dispatch_reminder(self, reminder_id: int) -> None:
        due = self.task_service.take_due_reminder(reminder_id)
        if due is None:
            return

        task, reminder = due
        if reminder.kind == ReminderKind.START:
            await self._send_start_reminder(task)
            recurring = self.task_service.ensure_future_recurring_task(task.id)
            if recurring:
                self.schedule_reminder(recurring[1])
            next_reminder = self.task_service.plan_next_start_reminder(task.id)
            if next_reminder:
                self.schedule_reminder(next_reminder)
            return

        if reminder.kind == ReminderKind.CHECKIN:
            await self._send_checkin(task)
            return

        if reminder.kind == ReminderKind.FOCUS_NUDGE:
            await self._send_focus_nudge(task)

    async def _send_start_reminder(self, task: Task) -> None:
        try:
            reminder_count = self.task_service.database.count_task_events(task.id, "start_reminder_sent")
            if reminder_count > 1 and self.motivation_service is not None:
                text = await self.motivation_service.compose(
                    task.telegram_user_id,
                    AIScenario.PROCRASTINATION,
                    task=task,
                    user_message=(
                        "Пользователь уже получил первое напоминание, но не нажал «Начал» или «Готово». "
                        f"Это повторное напоминание о старте задачи «{task.title}». "
                        "Напиши короткий AI-дожим: живо, по-человечески, без длинного плана выполнения."
                    ),
                    tone_override=ToneMode.TOUGH if reminder_count >= 3 else None,
                )
                await self.bot.send_message(
                    chat_id=task.chat_id,
                    text=escape(text),
                    reply_markup=task_reminder_actions_keyboard(task.id),
                )
                return

            await self.bot.send_message(
                chat_id=task.chat_id,
                text=format_start_reminder(task, self.settings.timezone),
                reply_markup=task_reminder_actions_keyboard(task.id),
            )
        except TelegramAPIError as exc:
            logger.warning("Failed to send start reminder for task %s: %s", task.id, exc)
            self.task_service.log_event(
                task.id,
                task.telegram_user_id,
                "start_reminder_send_failed",
                str(exc),
            )

    async def _send_checkin(self, task: Task) -> None:
        try:
            await self.bot.send_message(
                chat_id=task.chat_id,
                text=format_checkin(task),
                reply_markup=checkin_actions_keyboard(task.id),
            )
        except TelegramAPIError as exc:
            logger.warning("Failed to send checkin for task %s: %s", task.id, exc)
            self.task_service.log_event(
                task.id,
                task.telegram_user_id,
                "checkin_send_failed",
                str(exc),
            )

    async def _send_focus_nudge(self, task: Task) -> None:
        if self.motivation_service is None:
            return
        if not self.motivation_service.should_send_proactive(
            task.telegram_user_id,
            AIScenario.FOCUS,
            cooldown_minutes=75,
        ):
            return

        try:
            text = await self.motivation_service.compose(
                task.telegram_user_id,
                AIScenario.FOCUS,
                task=task,
            )
            await self.bot.send_message(
                chat_id=task.chat_id,
                text=escape(text),
                reply_markup=checkin_actions_keyboard(task.id),
            )
        except TelegramAPIError as exc:
            logger.warning("Failed to send focus nudge for task %s: %s", task.id, exc)
            self.task_service.log_event(
                task.id,
                task.telegram_user_id,
                "focus_nudge_send_failed",
                str(exc),
            )

    @staticmethod
    def _job_id(reminder_id: int) -> str:
        return f"reminder:{reminder_id}"
