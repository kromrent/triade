from __future__ import annotations


MAIN_MENU_TEXTS = frozenset(
    {
        "добавить задачу",
        "мои задачи",
        "активные напоминания",
        "история задач",
        "помощь",
    }
)


def normalize_user_text(text: str | None) -> str:
    return " ".join((text or "").strip().casefold().split())


def matches_user_text(text: str | None, expected: str) -> bool:
    return normalize_user_text(text) == normalize_user_text(expected)


def is_main_menu_text(text: str | None) -> bool:
    return normalize_user_text(text) in MAIN_MENU_TEXTS
