from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from database import Database
from models import MotivationalTrack, TrackCategory

MEDIA_ROOT = Path("media/boosts")
MEDIA_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".ogg",
    ".flac",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
}
ACTIVE_MEDIA_CATEGORY = TrackCategory.FOCUS


@dataclass(frozen=True, slots=True)
class TrackDraft:
    title: str
    url: str | None
    description: str | None
    file_path: str | None
    category: TrackCategory


class TracksService:
    def __init__(self, database: Database) -> None:
        self.database = database
        ensure_media_dirs()

    def add_track(self, telegram_user_id: int, draft: TrackDraft) -> MotivationalTrack:
        return self.database.add_motivational_track(
            telegram_user_id=telegram_user_id,
            title=draft.title,
            url=draft.url,
            description=draft.description,
            file_path=draft.file_path,
            category=draft.category,
        )

    def list_tracks(self, telegram_user_id: int) -> list[MotivationalTrack]:
        return self.database.list_motivational_tracks(telegram_user_id)

    def random_track(
        self,
        telegram_user_id: int,
        categories: list[TrackCategory] | None = None,
    ) -> MotivationalTrack | None:
        tracks = self.database.list_motivational_tracks(telegram_user_id, categories)
        return random.choice(tracks) if tracks else None

    def random_focus_file(self, telegram_user_id: int) -> MotivationalTrack | None:
        self.scan_local_files(telegram_user_id)
        tracks = [
            track
            for track in self.database.list_motivational_tracks(telegram_user_id, [ACTIVE_MEDIA_CATEGORY])
            if track.file_path and _resolve_local_file(track.file_path) is not None
        ]
        return random.choice(tracks) if tracks else None

    def scan_local_files(self, telegram_user_id: int) -> list[MotivationalTrack]:
        ensure_media_dirs()
        existing_paths = {
            track.file_path
            for track in self.database.list_motivational_tracks(telegram_user_id)
            if track.file_path
        }
        added: list[MotivationalTrack] = []
        directory = MEDIA_ROOT / ACTIVE_MEDIA_CATEGORY.value
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            normalized_path = _normalize_file_path(path)
            if normalized_path in existing_paths:
                continue
            draft = TrackDraft(
                title=path.stem.replace("_", " ").replace("-", " ")[:255],
                url=None,
                description=None,
                file_path=normalized_path,
                category=ACTIVE_MEDIA_CATEGORY,
            )
            added.append(self.add_track(telegram_user_id, draft))
            existing_paths.add(normalized_path)
        return added


def parse_track_category(value: str) -> TrackCategory | None:
    normalized = value.strip().lower()
    aliases = {
        "start": TrackCategory.START,
        "старт": TrackCategory.START,
        "focus": TrackCategory.FOCUS,
        "фокус": TrackCategory.FOCUS,
        "comeback": TrackCategory.COMEBACK,
        "возврат": TrackCategory.COMEBACK,
        "push": TrackCategory.PUSH,
        "пинок": TrackCategory.PUSH,
        "finish": TrackCategory.FINISH,
        "финиш": TrackCategory.FINISH,
    }
    return aliases.get(normalized)


def parse_track_draft(raw: str) -> TrackDraft | None:
    parts = raw.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None

    category = parse_track_category(parts[0])
    if category is None:
        return None

    body = parts[1].strip()
    title_part, _, tail = body.partition("|")
    title = title_part.strip()
    if not title:
        return None

    tail = tail.strip() or None
    url = tail if tail and tail.startswith(("http://", "https://")) else None
    file_path = None
    description = None
    if tail and not url:
        candidate = _resolve_local_file(tail)
        if candidate is not None:
            file_path = _normalize_file_path(candidate)
        else:
            description = tail
    return TrackDraft(
        title=title[:255],
        url=url,
        description=description,
        file_path=file_path,
        category=category,
    )


def ensure_media_dirs() -> None:
    (MEDIA_ROOT / ACTIVE_MEDIA_CATEGORY.value).mkdir(parents=True, exist_ok=True)


def _resolve_local_file(value: str) -> Path | None:
    path = Path(value.strip().strip('"'))
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists() or not path.is_file():
        return None
    if path.suffix.lower() not in MEDIA_EXTENSIONS:
        return None
    return path


def _normalize_file_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path.resolve())
