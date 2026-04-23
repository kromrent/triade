from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    NUDGING = "nudging"
    SNOOZED = "snoozed"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReminderKind(str, Enum):
    START = "start"
    CHECKIN = "checkin"
    FOCUS_NUDGE = "focus_nudge"


class ReminderStatus(str, Enum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    CANCELLED = "cancelled"


class ToneMode(str, Enum):
    SUPPORTIVE = "supportive"
    BALANCED = "balanced"
    TOUGH = "tough"
    BRO = "bro"


class AIScenario(str, Enum):
    GENERAL_CHAT = "general_chat"
    START = "start"
    FOCUS = "focus"
    PROCRASTINATION = "procrastination"
    COMEBACK = "comeback"
    COMPLETION = "completion"
    ENCOURAGEMENT = "encouragement"
    HELP_TASK = "help_task"
    BREAKDOWN = "breakdown"
    START_STEP = "start_step"
    PLAN = "plan"
    ADVICE = "advice"
    WHY = "why"
    BOOST = "boost"
    PANIC = "panic"


class TrackCategory(str, Enum):
    START = "start"
    FOCUS = "focus"
    COMEBACK = "comeback"
    PUSH = "push"
    FINISH = "finish"


TERMINAL_TASK_STATUSES = {TaskStatus.DONE, TaskStatus.CANCELLED}


@dataclass(frozen=True, slots=True)
class Task:
    id: int
    telegram_user_id: int
    chat_id: int
    title: str
    description: Optional[str]
    status: TaskStatus
    priority: Priority
    created_at: datetime
    updated_at: datetime
    start_reminder_at: datetime
    repeat_every_minutes: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    postponed_until: Optional[datetime]


@dataclass(frozen=True, slots=True)
class Reminder:
    id: int
    task_id: int
    kind: ReminderKind
    status: ReminderStatus
    scheduled_at: datetime
    sent_at: Optional[datetime]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UserPreference:
    telegram_user_id: int
    tone_mode: ToneMode
    ai_enabled: bool
    strictness_level: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class UserGoal:
    id: int
    telegram_user_id: int
    text: str
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MotivationEntry:
    id: int
    telegram_user_id: int
    text: str
    category: str
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MotivationalTrack:
    id: int
    telegram_user_id: int
    title: str
    url: Optional[str]
    description: Optional[str]
    file_path: Optional[str]
    category: TrackCategory
    created_at: datetime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def datetime_to_db(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def datetime_from_db(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
