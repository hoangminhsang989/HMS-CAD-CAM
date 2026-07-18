"""Versioned per-user recent HMS project configuration."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from hms_cadcam.project.constants import (
    MAX_RECENT_PROJECTS,
    RECENT_PROJECTS_FORMAT,
    RECENT_PROJECTS_VERSION,
)
from hms_cadcam.project.models import datetime_from_json, datetime_to_json, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecentProjectEntry:
    """A project path ordered by the last successful open time."""

    path: Path
    last_opened_at: datetime


class RecentProjectsService:
    """Read and atomically update recent_projects.json."""

    def __init__(self, config_dir: Path, limit: int = MAX_RECENT_PROJECTS) -> None:
        self._config_dir = config_dir
        self._path = config_dir / "recent_projects.json"
        self._limit = limit

    def list(self) -> tuple[RecentProjectEntry, ...]:
        """Return valid recent entries; malformed config is treated as empty."""
        if not self._path.exists():
            return ()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("format") != RECENT_PROJECTS_FORMAT:
                raise ValueError("Invalid recent projects format")
            if int(data.get("format_version", -1)) != RECENT_PROJECTS_VERSION:
                raise ValueError("Unsupported recent projects version")
            entries = []
            for item in data.get("projects", []):
                path = Path(str(item["path"]))
                if path.is_dir():
                    entries.append(
                        RecentProjectEntry(
                            path=path,
                            last_opened_at=datetime_from_json(str(item["last_opened_at"])),
                        )
                    )
            return tuple(entries[: self._limit])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            logger.warning("Không thể đọc danh sách dự án gần đây", exc_info=True)
            return ()

    def add(self, project_path: Path) -> None:
        """Put a successfully opened project first and remove duplicates."""
        existing = self.list()
        key = str(project_path.resolve()).casefold()
        entries = [entry for entry in existing if str(entry.path.resolve()).casefold() != key]
        entries.insert(0, RecentProjectEntry(project_path.resolve(), utc_now()))
        self._write(entries[: self._limit])

    def remove(self, project_path: Path) -> None:
        """Remove one recent entry without touching its project directory."""
        key = str(project_path.resolve()).casefold()
        entries = [entry for entry in self.list() if str(entry.path.resolve()).casefold() != key]
        self._write(entries)

    def _write(self, entries: list[RecentProjectEntry]) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        data = {
            "format": RECENT_PROJECTS_FORMAT,
            "format_version": RECENT_PROJECTS_VERSION,
            "projects": [
                {
                    "path": str(entry.path),
                    "last_opened_at": datetime_to_json(entry.last_opened_at),
                }
                for entry in entries
            ],
        }
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self._path)
        finally:
            temporary.unlink(missing_ok=True)
