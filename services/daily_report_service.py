from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from html import escape
from pathlib import Path

from config import Settings
from services.task_service import TaskService
from models import Task, TaskStatus, utc_now


STATUS_LABELS = {
    TaskStatus.PENDING: "ждет старта",
    TaskStatus.NUDGING: "дожимается",
    TaskStatus.SNOOZED: "отложена",
    TaskStatus.IN_PROGRESS: "в процессе",
    TaskStatus.DONE: "завершена",
    TaskStatus.CANCELLED: "отменена",
}


@dataclass(frozen=True, slots=True)
class DailyReport:
    telegram_user_id: int
    chat_id: int
    local_now: datetime
    completed_tasks: list[Task]
    active_tasks: list[Task]
    created_count: int
    snoozed_count: int
    cancelled_count: int

    @property
    def has_activity(self) -> bool:
        return bool(
            self.completed_tasks
            or self.active_tasks
            or self.created_count
            or self.snoozed_count
            or self.cancelled_count
        )


class DailyReportService:
    def __init__(self, task_service: TaskService, settings: Settings) -> None:
        self.task_service = task_service
        self.settings = settings
        self.reports_dir = settings.database_path.parent / "reports"

    def recipients(self) -> list[tuple[int, int]]:
        return self.task_service.database.list_report_recipients()

    def build_report(self, telegram_user_id: int, chat_id: int) -> DailyReport:
        local_now = utc_now().astimezone(self.settings.tzinfo)
        start_of_day_utc = _start_of_local_day_utc(local_now)

        all_tasks = self.task_service.list_tasks(
            telegram_user_id,
            include_closed=True,
            limit=1000,
        )
        completed_tasks = [
            task
            for task in all_tasks
            if task.status == TaskStatus.DONE
            and task.completed_at is not None
            and task.completed_at >= start_of_day_utc
        ]
        active_tasks = [
            task
            for task in all_tasks
            if task.status not in {TaskStatus.DONE, TaskStatus.CANCELLED}
        ]

        return DailyReport(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            local_now=local_now,
            completed_tasks=completed_tasks,
            active_tasks=active_tasks,
            created_count=self.task_service.database.count_user_events_since(
                telegram_user_id,
                "task_created",
                start_of_day_utc,
            ),
            snoozed_count=self.task_service.database.count_user_events_since(
                telegram_user_id,
                "task_snoozed",
                start_of_day_utc,
            ),
            cancelled_count=self.task_service.database.count_user_events_since(
                telegram_user_id,
                "task_cancelled",
                start_of_day_utc,
            ),
        )

    def format_report(self, report: DailyReport) -> str:
        lines = [
            "<b>Итог дня</b>",
            report.local_now.strftime("%d.%m.%Y"),
            "",
            f"Сделано: <b>{len(report.completed_tasks)}</b>",
            f"Осталось активных: <b>{len(report.active_tasks)}</b>",
            f"Переносов: <b>{report.snoozed_count}</b>",
            f"Создано задач: <b>{report.created_count}</b>",
        ]

        lines.extend(["", "<b>Что сделал</b>"])
        if report.completed_tasks:
            lines.extend(f"- {escape(task.title)}" for task in report.completed_tasks[:8])
            if len(report.completed_tasks) > 8:
                lines.append(f"...и еще {len(report.completed_tasks) - 8}")
        else:
            lines.append("- сегодня закрытых задач нет")

        lines.extend(["", "<b>Что осталось</b>"])
        if report.active_tasks:
            lines.extend(
                f"- {escape(task.title)} — {STATUS_LABELS[task.status]}"
                for task in report.active_tasks[:8]
            )
            if len(report.active_tasks) > 8:
                lines.append(f"...и еще {len(report.active_tasks) - 8}")
        else:
            lines.append("- активных задач нет")

        lines.extend(["", "<b>Вывод</b>", escape(_build_conclusion(report))])
        return "\n".join(lines)

    def render_chart(self, report: DailyReport) -> Path | None:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.reports_dir / f"daily_report_{report.telegram_user_id}_{report.local_now:%Y%m%d}.png"

        width, height = 1000, 620
        image = Image.new("RGB", (width, height), "#090d12")
        draw = ImageDraw.Draw(image)

        title_font = _font(ImageFont, 42, bold=True)
        subtitle_font = _font(ImageFont, 24)
        label_font = _font(ImageFont, 26, bold=True)
        small_font = _font(ImageFont, 20)
        value_font = _font(ImageFont, 34, bold=True)

        draw.rectangle((0, 0, width, height), fill="#090d12")
        draw.text((60, 46), "Итог дня", fill="#f7f7f2", font=title_font)
        draw.text((60, 100), report.local_now.strftime("%d.%m.%Y"), fill="#9aa4b2", font=subtitle_font)

        metrics = [
            ("Сделано", len(report.completed_tasks), "#35d07f"),
            ("Осталось", len(report.active_tasks), "#f5c542"),
            ("Переносы", report.snoozed_count, "#ef6f6c"),
            ("Создано", report.created_count, "#73a7ff"),
        ]
        max_value = max(1, *(value for _, value, _ in metrics))
        chart_left = 90
        chart_top = 190
        chart_bottom = 500
        bar_width = 150
        gap = 70

        draw.line((chart_left, chart_bottom, width - 80, chart_bottom), fill="#2b3440", width=3)
        for index, (label, value, color) in enumerate(metrics):
            x0 = chart_left + index * (bar_width + gap)
            bar_height = int((chart_bottom - chart_top) * (value / max_value))
            y0 = chart_bottom - bar_height
            draw.rectangle((x0, y0, x0 + bar_width, chart_bottom), fill=color)
            draw.text((x0 + bar_width / 2, y0 - 46), str(value), fill="#f7f7f2", font=value_font, anchor="mm")
            draw.text((x0 + bar_width / 2, chart_bottom + 34), label, fill="#d8dee9", font=label_font, anchor="mm")

        conclusion = _build_conclusion(report)
        draw.text((60, 560), _clip_text(conclusion, 82), fill="#d8dee9", font=small_font)
        image.save(path, format="PNG")
        return path


def _start_of_local_day_utc(local_now: datetime) -> datetime:
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    return local_start.astimezone(timezone.utc)


def _build_conclusion(report: DailyReport) -> str:
    done = len(report.completed_tasks)
    active = len(report.active_tasks)
    snoozed = report.snoozed_count

    if done and not active:
        return "Хороший день: хвостов на завтра не осталось. Можно спокойно закрывать вечер."
    if done >= active and done > 0:
        return "День не пустой: результат есть. Завтра добираем оставшееся без героизма."
    if active and snoozed >= 3:
        return "Главный риск завтра — переносы. Лучше начать с самой легкой задачи и сразу войти в ритм."
    if active:
        return "День еще не провален: часть задач просто переехала. Завтра начинаем с одного короткого старта."
    return "Сегодня почти без движухи. Завтра задача простая: не идеальный день, а первый нормальный старт."


def _font(image_font_module, size: int, bold: bool = False):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
    ]
    for candidate in candidates:
        try:
            return image_font_module.truetype(candidate, size=size)
        except OSError:
            continue
    return image_font_module.load_default()


def _clip_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"
