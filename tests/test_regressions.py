from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config import Settings
from database import Database
from handlers.ai import (
    _format_created_task_response,
    _format_completed_task_response,
    _format_existing_task_response,
    _resolve_management_task,
    _resolve_draft_reminder_at,
)
from keyboards.reply import confirm_yes_no_keyboard
from handlers.text_utils import is_main_menu_text, matches_user_text
from services.formatting import format_task_already_exists
from models import AIScenario, Priority, RecurrenceKind, Reminder, ReminderKind, ReminderStatus, Task, TaskStatus, ToneMode, UserPreference
from services.intent_service import IntentService, IntentType, TaskAction
from services.prompt_builder import PromptBuilder
from services.task_service import InvalidTaskTransitionError, TaskService
from services.user_context_service import UserAIContext


def build_settings(database_path: Path) -> Settings:
    return Settings(
        bot_token="test-token",
        database_path=database_path,
        timezone="UTC",
        default_repeat_minutes=5,
        checkin_after_minutes=25,
        default_snooze_minutes=10,
        ai_openai_enabled=False,
        openai_api_key=None,
        openai_model="gpt-5-mini",
        openai_classifier_model="gpt-5-nano",
        openai_simple_model="gpt-5-nano",
        openai_complex_model="gpt-5-mini",
        openai_timeout_seconds=8,
        openai_intent_confidence_threshold=0.55,
        openai_create_task_confidence_threshold=0.75,
        ai_default_tone="bro",
    )


class TaskServiceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.settings = build_settings(Path(self.tempdir.name) / "test.sqlite3")
        self.database = Database(self.settings.database_path)
        self.database.connect()
        self.database.init_schema()
        self.task_service = TaskService(self.database, self.settings)
        self.user_id = 1001
        self.chat_id = 2002
        self.database.upsert_user(self.user_id, self.chat_id, None, "Test", None)

    def tearDown(self) -> None:
        self.database.close()
        self.tempdir.cleanup()

    def create_task(
        self,
        title: str,
        recurrence_kind: RecurrenceKind = RecurrenceKind.NONE,
        remind_at: datetime | None = None,
    ):
        remind_at = remind_at or (datetime.now(timezone.utc) + timedelta(minutes=30))
        creation = self.task_service.create_task(
            telegram_user_id=self.user_id,
            chat_id=self.chat_id,
            title=title,
            description=None,
            start_reminder_at=remind_at,
            repeat_every_minutes=self.settings.default_repeat_minutes,
            priority=Priority.MEDIUM,
            recurrence_kind=recurrence_kind,
        )
        return creation.task, creation.reminder

    def test_task_history_excludes_active_tasks(self) -> None:
        active_task, _ = self.create_task("active task")
        done_task, _ = self.create_task("done task")
        cancelled_task, _ = self.create_task("cancelled task")

        self.task_service.complete_task(done_task.id, self.user_id)
        self.task_service.cancel_task(cancelled_task.id, self.user_id)

        history = self.task_service.list_task_history(self.user_id, limit=10)

        self.assertTrue(history)
        self.assertNotIn(active_task.id, {task.id for task in history})
        self.assertTrue(
            all(task.status in {TaskStatus.DONE, TaskStatus.CANCELLED} for task in history)
        )

    def test_start_task_rejects_second_start_without_duplicate_checkin(self) -> None:
        task, _ = self.create_task("single start")

        self.task_service.start_task(task.id, self.user_id)

        with self.assertRaises(InvalidTaskTransitionError):
            self.task_service.start_task(task.id, self.user_id)

        scheduled = self.database.list_scheduled_reminders()
        self.assertEqual(
            1,
            sum(1 for reminder in scheduled if reminder.kind == ReminderKind.CHECKIN),
        )
        self.assertEqual(
            0,
            sum(1 for reminder in scheduled if reminder.kind == ReminderKind.START),
        )

    def test_continue_checkin_replaces_previous_scheduled_checkin(self) -> None:
        task, _ = self.create_task("replace checkin")
        self.task_service.start_task(task.id, self.user_id)

        _, first_reminder = self.task_service.continue_checkin(task.id, self.user_id)
        _, second_reminder = self.task_service.continue_checkin(task.id, self.user_id)

        scheduled = self.database.list_scheduled_reminders()
        checkins = [reminder for reminder in scheduled if reminder.kind == ReminderKind.CHECKIN]

        self.assertEqual(1, len(checkins))
        self.assertEqual(second_reminder.id, checkins[0].id)
        self.assertNotEqual(first_reminder.id, second_reminder.id)

    def test_complete_recurring_task_creates_next_occurrence(self) -> None:
        start_at = datetime(2026, 4, 23, 9, 0, tzinfo=timezone.utc)
        task, _ = self.create_task(
            "daily standup",
            recurrence_kind=RecurrenceKind.DAILY,
            remind_at=start_at,
        )

        updated, next_reminder = self.task_service.complete_task(task.id, self.user_id)

        self.assertEqual(TaskStatus.DONE, updated.status)
        self.assertIsNotNone(next_reminder)

        child = self.database.get_task(next_reminder.task_id if next_reminder else 0)
        self.assertIsNotNone(child)
        self.assertEqual(RecurrenceKind.DAILY, child.recurrence_kind)
        self.assertEqual(task.id, child.recurrence_parent_task_id)
        self.assertEqual(start_at + timedelta(days=1), child.start_reminder_at)

    def test_cancel_recurring_task_stops_series(self) -> None:
        task, _ = self.create_task(
            "weekly review",
            recurrence_kind=RecurrenceKind.WEEKLY,
            remind_at=datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc),
        )

        updated, next_reminder = self.task_service.cancel_task(task.id, self.user_id)

        self.assertEqual(TaskStatus.CANCELLED, updated.status)
        self.assertIsNone(next_reminder)
        self.assertIsNone(self.database.get_recurring_child_task(task.id))

    def test_create_task_prevents_duplicate_active_task(self) -> None:
        remind_at = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)

        first_creation = self.task_service.create_task(
            telegram_user_id=self.user_id,
            chat_id=self.chat_id,
            title="проснуться",
            description=None,
            start_reminder_at=remind_at,
            repeat_every_minutes=self.settings.default_repeat_minutes,
            priority=Priority.MEDIUM,
            recurrence_kind=RecurrenceKind.DAILY,
        )
        second_creation = self.task_service.create_task(
            telegram_user_id=self.user_id,
            chat_id=self.chat_id,
            title="  проснуться  ",
            description=None,
            start_reminder_at=remind_at,
            repeat_every_minutes=self.settings.default_repeat_minutes,
            priority=Priority.MEDIUM,
            recurrence_kind=RecurrenceKind.DAILY,
        )

        self.assertTrue(first_creation.created_new)
        self.assertFalse(second_creation.created_new)
        self.assertEqual(first_creation.task.id, second_creation.task.id)
        self.assertEqual(first_creation.reminder.id, second_creation.reminder.id)
        self.assertEqual(1, len(self.task_service.list_tasks(self.user_id, limit=20)))
        scheduled = self.database.list_scheduled_reminders()
        self.assertEqual(
            1,
            sum(1 for reminder in scheduled if reminder.kind == ReminderKind.START),
        )


class FakeAIService:
    def __init__(self) -> None:
        self.called = False

    async def classify_intent(self, *args, **kwargs):
        self.called = True
        raise AssertionError("AI classifier should not be called")

    async def plan_task_creation(self, *args, **kwargs):
        self.called = True
        raise AssertionError("AI task planner should not be called")


class LowConfidenceGeneralAIService:
    async def classify_intent(self, *args, **kwargs):
        return {"intent": "general_chat", "confidence": 0.1}


class IntentServiceRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_detect_smart_skips_ai_classifier_when_disabled(self) -> None:
        service = IntentService()
        fake_ai_service = FakeAIService()
        now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)

        intent = await service.detect_smart(
            "надо сделать диплом",
            now,
            ZoneInfo("UTC"),
            ai_service=fake_ai_service,
            use_ai_classifier=False,
        )

        self.assertFalse(fake_ai_service.called)
        self.assertEqual(IntentType.CREATE_TASK, intent.type)
        self.assertEqual("диплом", intent.task_creations[0].title)

    async def test_detect_daily_recurring_task(self) -> None:
        service = IntentService()
        now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)

        intent = service.detect(
            "\u043a\u0430\u0436\u0434\u044b\u0439 \u0434\u0435\u043d\u044c \u0432 09:00 \u0437\u0430\u0440\u044f\u0434\u043a\u0430",
            now,
            ZoneInfo("UTC"),
        )

        self.assertEqual(IntentType.CREATE_TASK, intent.type)
        self.assertEqual(RecurrenceKind.DAILY, intent.task_creations[0].recurrence_kind)
        self.assertEqual("\u0437\u0430\u0440\u044f\u0434\u043a\u0430", intent.task_creations[0].title)
        self.assertEqual(
            datetime(2026, 4, 24, 9, 0, tzinfo=timezone.utc),
            intent.task_creations[0].remind_at,
        )

    async def test_detect_weekly_recurring_task(self) -> None:
        service = IntentService()
        now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)

        intent = service.detect(
            "\u043a\u0430\u0436\u0434\u0443\u044e \u043d\u0435\u0434\u0435\u043b\u044e \u0432 10 \u043e\u0442\u0447\u0435\u0442",
            now,
            ZoneInfo("UTC"),
        )

        self.assertEqual(IntentType.CREATE_TASK, intent.type)
        self.assertEqual(RecurrenceKind.WEEKLY, intent.task_creations[0].recurrence_kind)
        self.assertEqual("\u043e\u0442\u0447\u0435\u0442", intent.task_creations[0].title)
        self.assertEqual(
            datetime(2026, 4, 30, 10, 0, tzinfo=timezone.utc),
            intent.task_creations[0].remind_at,
        )

    async def test_detect_daily_recurring_task_with_command_style_phrase(self) -> None:
        service = IntentService()
        now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)

        intent = service.detect(
            "добавь ежедневную задачу проснуться в 10 утра",
            now,
            ZoneInfo("UTC"),
        )

        self.assertEqual(IntentType.CREATE_TASK, intent.type)
        self.assertEqual(RecurrenceKind.DAILY, intent.task_creations[0].recurrence_kind)
        self.assertEqual("проснуться", intent.task_creations[0].title)
        self.assertEqual(
            datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
            intent.task_creations[0].remind_at,
        )

    async def test_detect_smart_falls_back_to_local_parse_for_recurring_command(self) -> None:
        service = IntentService()
        now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)

        intent = await service.detect_smart(
            "добавь ежедневную задачу проснуться в 10 утра",
            now,
            ZoneInfo("UTC"),
            ai_service=LowConfidenceGeneralAIService(),
            use_ai_classifier=True,
        )

        self.assertEqual(IntentType.CREATE_TASK, intent.type)
        self.assertEqual(RecurrenceKind.DAILY, intent.task_creations[0].recurrence_kind)
        self.assertEqual("проснуться", intent.task_creations[0].title)


    async def test_detect_smart_handles_single_typos_in_create_command(self) -> None:
        service = IntentService()
        timezone_local = ZoneInfo("Europe/Astrakhan")
        now = datetime(2026, 4, 24, 0, 14, tzinfo=timezone_local)

        intent = await service.detect_smart(
            "\u043e\u0431\u0430\u0432\u044c \u0437\u0430\u0434\u0430\u0447\u0443 \u043f\u0440\u043e\u0441\u043d\u0443\u0442\u044c\u0441\u044f \u0432 10 \u0443\u0442\u0440\u0430 \u0435\u0436\u0434\u0435\u043d\u0435\u0432\u043d\u0443\u044e",
            now,
            timezone_local,
            ai_service=LowConfidenceGeneralAIService(),
            use_ai_classifier=True,
        )

        self.assertEqual(IntentType.CREATE_TASK, intent.type)
        self.assertEqual("проснуться", intent.task_creations[0].title)
        self.assertEqual(RecurrenceKind.DAILY, intent.task_creations[0].recurrence_kind)
        self.assertEqual(
            datetime(2026, 4, 24, 10, 0, tzinfo=timezone_local),
            intent.task_creations[0].remind_at,
        )

    async def test_detect_plain_chat_preference_as_general_chat(self) -> None:
        service = IntentService()
        now = datetime(2026, 4, 24, 0, 31, tzinfo=timezone.utc)

        intent = await service.detect_smart(
            "а ты можешь как человек отвечать",
            now,
            ZoneInfo("UTC"),
            ai_service=LowConfidenceGeneralAIService(),
            use_ai_classifier=True,
        )

        self.assertEqual(IntentType.GENERAL_CHAT, intent.type)

    async def test_detect_emotional_state_takes_priority_over_task_advice(self) -> None:
        service = IntentService()
        now = datetime(2026, 4, 24, 0, 31, tzinfo=timezone.utc)

        intent = await service.detect_smart(
            "да я что-то устал от всех этих задач что думаешь?",
            now,
            ZoneInfo("UTC"),
            ai_service=LowConfidenceGeneralAIService(),
            use_ai_classifier=True,
        )

        self.assertEqual(IntentType.EMOTIONAL_STATE, intent.type)
        self.assertEqual(AIScenario.COMEBACK, intent.scenario)

    async def test_detect_sdayus_as_supportive_state(self) -> None:
        service = IntentService()
        now = datetime(2026, 4, 24, 0, 31, tzinfo=timezone.utc)

        intent = await service.detect_smart(
            "сдаюсь братка",
            now,
            ZoneInfo("UTC"),
            ai_service=LowConfidenceGeneralAIService(),
            use_ai_classifier=True,
        )

        self.assertEqual(IntentType.EMOTIONAL_STATE, intent.type)
        self.assertEqual(AIScenario.COMEBACK, intent.scenario)


class PromptBuilderRegressionTests(unittest.TestCase):
    def _build_context(self) -> UserAIContext:
        task = Task(
            id=7,
            telegram_user_id=1001,
            chat_id=2002,
            title="проснуться",
            description=None,
            status=TaskStatus.PENDING,
            priority=Priority.MEDIUM,
            created_at=datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc),
            start_reminder_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
            repeat_every_minutes=5,
            recurrence_kind=RecurrenceKind.DAILY,
            recurrence_parent_task_id=None,
            started_at=None,
            completed_at=None,
            cancelled_at=None,
            postponed_until=None,
        )
        preference = UserPreference(
            telegram_user_id=1001,
            tone_mode=ToneMode.BALANCED,
            ai_enabled=True,
            strictness_level=0,
            created_at=datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc),
        )
        return UserAIContext(
            telegram_user_id=1001,
            task=task,
            active_tasks=[task],
            recent_dialog=[
                ("давай разберем задачи", "окей, вот список"),
                ("а ты можешь как человек отвечать", "могу"),
            ],
            bro_boost_allowed=False,
            preference=preference,
            effective_tone=ToneMode.BALANCED,
            goals=[],
            motivation_entries=[],
            task_snooze_count=0,
            completed_today=0,
            snoozed_today=0,
            session_minutes=None,
            local_now=datetime(2026, 4, 24, 0, 31, tzinfo=timezone.utc),
        )

    def test_general_chat_prompt_hides_task_context(self) -> None:
        prompt = PromptBuilder().build(
            self._build_context(),
            AIScenario.GENERAL_CHAT,
            user_message="а ты можешь как человек отвечать",
        )

        self.assertIn("do not drag every message back to tasks", prompt[0]["content"].lower())
        self.assertIn("hidden for this reply", prompt[1]["content"])

    def test_low_energy_prompt_uses_support_instructions(self) -> None:
        prompt = PromptBuilder().build(
            self._build_context(),
            AIScenario.COMEBACK,
            user_message="я что-то устал от всех этих задач",
        )

        self.assertIn("start by acknowledging the feeling", prompt[0]["content"].lower())
        self.assertIn("hidden for this reply", prompt[1]["content"])


class MenuTextRegressionTests(unittest.TestCase):
    def test_main_menu_text_matching_ignores_case_and_extra_spaces(self) -> None:
        self.assertTrue(matches_user_text("мои задачи", "Мои задачи"))
        self.assertTrue(matches_user_text("  ПОМОЩЬ  ", "Помощь"))
        self.assertTrue(is_main_menu_text("  мои   задачи  "))
        self.assertTrue(is_main_menu_text("АКТИВНЫЕ   НАПОМИНАНИЯ"))

    def test_confirm_yes_no_keyboard_contains_only_yes_and_no(self) -> None:
        keyboard = confirm_yes_no_keyboard()

        self.assertEqual([["Да", "Нет"]], [[button.text for button in row] for row in keyboard.keyboard])


class AIFormattingRegressionTests(unittest.TestCase):
    def test_recurring_created_task_response_does_not_duplicate_recurrence_type(self) -> None:
        task = Task(
            id=1,
            telegram_user_id=1001,
            chat_id=2002,
            title="проснуться",
            description=None,
            status=TaskStatus.PENDING,
            priority=Priority.MEDIUM,
            created_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            start_reminder_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
            repeat_every_minutes=5,
            recurrence_kind=RecurrenceKind.DAILY,
            recurrence_parent_task_id=None,
            started_at=None,
            completed_at=None,
            cancelled_at=None,
            postponed_until=None,
        )

        response = _format_created_task_response(task, "UTC")

        self.assertEqual(1, response.count("Тип: Ежедневная"))

    def test_duplicate_task_response_mentions_existing_task(self) -> None:
        task = Task(
            id=1,
            telegram_user_id=1001,
            chat_id=2002,
            title="проснуться",
            description=None,
            status=TaskStatus.PENDING,
            priority=Priority.MEDIUM,
            created_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            start_reminder_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
            repeat_every_minutes=5,
            recurrence_kind=RecurrenceKind.DAILY,
            recurrence_parent_task_id=None,
            started_at=None,
            completed_at=None,
            cancelled_at=None,
            postponed_until=None,
        )

        ai_response = _format_existing_task_response(task, "UTC")
        ui_response = format_task_already_exists(task, "UTC")

        self.assertIn("Такая задача уже есть", ai_response)
        self.assertIn("новую не добавляю", ai_response.casefold())
        self.assertIn("Такая задача уже есть", ui_response)

    def test_resolve_draft_reminder_at_moves_daily_past_time_to_next_day(self) -> None:
        timezone_local = ZoneInfo("Europe/Astrakhan")
        local_now = datetime(2026, 4, 24, 0, 9, tzinfo=timezone_local)
        remind_at = datetime(2026, 4, 23, 10, 0, tzinfo=timezone_local)

        resolved = _resolve_draft_reminder_at(
            remind_at,
            RecurrenceKind.DAILY,
            local_now,
            timezone_local,
        )

        self.assertEqual(
            datetime(2026, 4, 24, 10, 0, tzinfo=timezone_local),
            resolved,
        )


class AIManagementRegressionTests(unittest.TestCase):
    @staticmethod
    def _task(
        task_id: int,
        title: str,
        remind_at: datetime,
        *,
        status: TaskStatus = TaskStatus.PENDING,
        recurrence_kind: RecurrenceKind = RecurrenceKind.NONE,
    ) -> Task:
        return Task(
            id=task_id,
            telegram_user_id=1001,
            chat_id=2002,
            title=title,
            description=None,
            status=status,
            priority=Priority.MEDIUM,
            created_at=datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc),
            start_reminder_at=remind_at,
            repeat_every_minutes=5,
            recurrence_kind=recurrence_kind,
            recurrence_parent_task_id=None,
            started_at=None,
            completed_at=None,
            cancelled_at=None,
            postponed_until=None,
        )

    def test_resolve_management_task_prefers_title_and_time_from_message(self) -> None:
        timezone_local = ZoneInfo("Europe/Astrakhan")
        tasks = [
            self._task(25, "умыться", datetime(2026, 4, 24, 10, 5, tzinfo=timezone_local)),
            self._task(21, "проснуться", datetime(2026, 4, 24, 10, 0, tzinfo=timezone_local)),
        ]

        task, error = _resolve_management_task(
            "пометь мою задачу проснуться в 10 00 сегодня как выполненную",
            TaskAction.DONE,
            tasks,
            timezone_local,
        )

        self.assertIsNone(error)
        self.assertIsNotNone(task)
        self.assertEqual("проснуться", task.title)
        self.assertEqual(21, task.id)

    def test_resolve_management_task_prefers_in_progress_for_generic_done(self) -> None:
        timezone_local = ZoneInfo("UTC")
        tasks = [
            self._task(7, "проснуться", datetime(2026, 4, 24, 10, 0, tzinfo=timezone_local)),
            self._task(
                8,
                "умыться",
                datetime(2026, 4, 24, 10, 5, tzinfo=timezone_local),
                status=TaskStatus.IN_PROGRESS,
            ),
        ]

        task, error = _resolve_management_task("готово", TaskAction.DONE, tasks, timezone_local)

        self.assertIsNone(error)
        self.assertIsNotNone(task)
        self.assertEqual(8, task.id)

    def test_format_completed_task_response_is_direct_and_recurring_safe(self) -> None:
        task = self._task(
            21,
            "проснуться",
            datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
            recurrence_kind=RecurrenceKind.DAILY,
        )
        next_reminder = Reminder(
            id=1,
            task_id=22,
            kind=ReminderKind.START,
            status=ReminderStatus.SCHEDULED,
            scheduled_at=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
            sent_at=None,
            created_at=datetime(2026, 4, 24, 0, 10, tzinfo=timezone.utc),
        )

        response = _format_completed_task_response(task, next_reminder, "UTC")

        self.assertIn("Готово, отметил выполненной: проснуться.", response)
        self.assertIn("Следующее повторение: 25.04 10:00.", response)
        self.assertNotIn("Текущая задача", response)


if __name__ == "__main__":
    unittest.main()
