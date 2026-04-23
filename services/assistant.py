from __future__ import annotations

from typing import Protocol

from models import Task


class TaskAssistant(Protocol):
    async def suggest_start(self, task: Task) -> str:
        ...

    async def split_into_steps(self, task: Task) -> list[str]:
        ...


class NoopTaskAssistant:
    async def suggest_start(self, task: Task) -> str:
        return (
            "Сделай первый физический шаг: открой нужный файл, вкладку или инструмент, "
            "поставь таймер на 5 минут и начни с самой маленькой части задачи."
        )

    async def split_into_steps(self, task: Task) -> list[str]:
        return [
            "Уточнить, какой результат нужен.",
            "Открыть рабочие материалы.",
            "Сделать первый небольшой шаг за 5 минут.",
        ]
