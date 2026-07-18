"""Typed in-memory representation of an HMS project."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID


class UnitSystem(StrEnum):
    """Units supported by the initial project format."""

    MILLIMETER = "mm"
    INCH = "inch"


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def datetime_to_json(value: datetime) -> str:
    """Serialize a timezone-aware timestamp using a trailing Z for UTC."""
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def datetime_from_json(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and require explicit timezone data."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone information")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class SourceFileRecord:
    """Metadata for an immutable source copy stored below source/."""

    source_id: UUID
    original_name: str
    stored_path: str
    size_bytes: int
    sha256: str
    imported_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert the source record to JSON-compatible data."""
        return {
            "source_id": str(self.source_id),
            "original_name": self.original_name,
            "stored_path": self.stored_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "imported_at": datetime_to_json(self.imported_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceFileRecord":
        """Create a source record from decoded manifest data."""
        return cls(
            source_id=UUID(str(data["source_id"])),
            original_name=str(data["original_name"]),
            stored_path=str(data["stored_path"]),
            size_bytes=int(data["size_bytes"]),
            sha256=str(data["sha256"]),
            imported_at=datetime_from_json(str(data["imported_at"])),
        )


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    """Versioned contents of project.hms.json."""

    format: str
    format_version: int
    application: str
    application_version: str
    project_id: UUID
    project_name: str
    created_at: datetime
    modified_at: datetime
    units: UnitSystem
    source_files: tuple[SourceFileRecord, ...]
    active_document: str | None
    database: str

    def to_dict(self) -> dict[str, Any]:
        """Convert the manifest to stable JSON-compatible data."""
        return {
            "format": self.format,
            "format_version": self.format_version,
            "application": self.application,
            "application_version": self.application_version,
            "project_id": str(self.project_id),
            "project_name": self.project_name,
            "created_at": datetime_to_json(self.created_at),
            "modified_at": datetime_to_json(self.modified_at),
            "units": self.units.value,
            "source_files": [record.to_dict() for record in self.source_files],
            "active_document": self.active_document,
            "database": self.database,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectManifest":
        """Create a typed manifest from decoded JSON data."""
        raw_sources = data["source_files"]
        if not isinstance(raw_sources, list):
            raise TypeError("source_files must be a list")
        return cls(
            format=str(data["format"]),
            format_version=int(data["format_version"]),
            application=str(data["application"]),
            application_version=str(data["application_version"]),
            project_id=UUID(str(data["project_id"])),
            project_name=str(data["project_name"]),
            created_at=datetime_from_json(str(data["created_at"])),
            modified_at=datetime_from_json(str(data["modified_at"])),
            units=UnitSystem(str(data["units"])),
            source_files=tuple(SourceFileRecord.from_dict(item) for item in raw_sources),
            active_document=(
                None if data["active_document"] is None else str(data["active_document"])
            ),
            database=str(data["database"]),
        )

    def with_modified_time(self, value: datetime | None = None) -> "ProjectManifest":
        """Return a manifest carrying a new modification timestamp."""
        return replace(self, modified_at=value or utc_now())


@dataclass(slots=True)
class ProjectSession:
    """A currently opened project and its unsaved state."""

    root_path: Path
    manifest: ProjectManifest
    is_dirty: bool = False
