"""Typed in-memory representation of an HMS project."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from hms_cadcam.project.cad_state import CadViewState
from hms_cadcam.cam.persistence.models import CamProjectSnapshot
from hms_cadcam.cam.cam3d.persistence import Cam3DProjectConfig
from hms_cadcam.cam.lathe.persistence.models import (
    LatheProjectSnapshot,
    LatheRestoreDiagnostic,
)


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
    original_path: str | None = None
    internal_filename: str | None = None
    importer: str = "unknown"
    units: str = "unknown"
    geometry_type: str = "unknown"
    read_only: bool = True
    working_geometry_path: str | None = None
    geometry_version: int = 1
    source_document_id: UUID | None = None
    source_container_id: UUID | None = None
    source_geometry_fingerprint: str | None = None
    source_container_fingerprint: str | None = None
    transfer_request_id: UUID | None = None
    geometry_representation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the source record to JSON-compatible data."""
        return {
            "source_id": str(self.source_id),
            "original_name": self.original_name,
            "stored_path": self.stored_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "imported_at": datetime_to_json(self.imported_at),
            "original_path": self.original_path,
            "internal_filename": self.internal_filename,
            "importer": self.importer,
            "units": self.units,
            "geometry_type": self.geometry_type,
            "read_only": self.read_only,
            "working_geometry_path": self.working_geometry_path,
            "geometry_version": self.geometry_version,
            "source_document_id": (
                None
                if self.source_document_id is None
                else str(self.source_document_id)
            ),
            "source_container_id": (
                None
                if self.source_container_id is None
                else str(self.source_container_id)
            ),
            "source_geometry_fingerprint": self.source_geometry_fingerprint,
            "source_container_fingerprint": self.source_container_fingerprint,
            "transfer_request_id": (
                None
                if self.transfer_request_id is None
                else str(self.transfer_request_id)
            ),
            "geometry_representation": self.geometry_representation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceFileRecord":
        """Create a source record from decoded manifest data."""
        if not isinstance(data, dict):
            raise TypeError("Source record must be an object")
        if not all(
            isinstance(data[field], str)
            for field in ("source_id", "original_name", "stored_path", "sha256", "imported_at")
        ):
            raise TypeError("Source record text fields must be strings")
        if type(data["size_bytes"]) is not int:
            raise TypeError("size_bytes must be an integer")
        original_path = data.get("original_path")
        internal_filename = data.get("internal_filename")
        working_geometry_path = data.get("working_geometry_path")
        optional_fingerprints = (
            data.get("source_geometry_fingerprint"),
            data.get("source_container_fingerprint"),
        )
        optional_ids = (
            data.get("source_document_id"),
            data.get("source_container_id"),
            data.get("transfer_request_id"),
        )
        geometry_representation = data.get("geometry_representation")
        if any(
            value is not None and not isinstance(value, str)
            for value in (
                original_path,
                internal_filename,
                working_geometry_path,
                *optional_fingerprints,
                *optional_ids,
                geometry_representation,
            )
        ):
            raise TypeError("Optional source paths/names must be strings or null")
        geometry_version = data.get("geometry_version", 1)
        if type(geometry_version) is not int or geometry_version < 1:
            raise TypeError("geometry_version must be a positive integer")
        if "read_only" in data and not isinstance(data["read_only"], bool):
            raise TypeError("read_only must be bool")
        for key in ("importer", "units", "geometry_type"):
            if key in data and not isinstance(data[key], str):
                raise TypeError(f"{key} must be a string")
        return cls(
            source_id=UUID(data["source_id"]),
            original_name=data["original_name"],
            stored_path=data["stored_path"],
            size_bytes=data["size_bytes"],
            sha256=data["sha256"],
            imported_at=datetime_from_json(data["imported_at"]),
            original_path=original_path,
            internal_filename=internal_filename,
            importer=data.get("importer", "unknown"),
            units=data.get("units", "unknown"),
            geometry_type=data.get("geometry_type", "unknown"),
            read_only=bool(data.get("read_only", True)),
            working_geometry_path=working_geometry_path,
            geometry_version=geometry_version,
            source_document_id=(
                None
                if optional_ids[0] is None
                else UUID(optional_ids[0])
            ),
            source_container_id=(
                None
                if optional_ids[1] is None
                else UUID(optional_ids[1])
            ),
            source_geometry_fingerprint=optional_fingerprints[0],
            source_container_fingerprint=optional_fingerprints[1],
            transfer_request_id=(
                None
                if optional_ids[2] is None
                else UUID(optional_ids[2])
            ),
            geometry_representation=geometry_representation,
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
        if type(data["format_version"]) is not int:
            raise TypeError("format_version must be an integer")
        if not all(
            isinstance(data[field], str)
            for field in (
                "format",
                "application",
                "application_version",
                "project_id",
                "project_name",
                "created_at",
                "modified_at",
                "units",
                "database",
            )
        ):
            raise TypeError("Manifest text fields must be strings")
        if data["active_document"] is not None and not isinstance(
            data["active_document"], str
        ):
            raise TypeError("active_document must be null or a string")
        return cls(
            format=data["format"],
            format_version=data["format_version"],
            application=data["application"],
            application_version=data["application_version"],
            project_id=UUID(data["project_id"]),
            project_name=data["project_name"],
            created_at=datetime_from_json(data["created_at"]),
            modified_at=datetime_from_json(data["modified_at"]),
            units=UnitSystem(data["units"]),
            source_files=tuple(SourceFileRecord.from_dict(item) for item in raw_sources),
            active_document=data["active_document"],
            database=data["database"],
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
    cad_view_states: dict[UUID, CadViewState] = field(default_factory=dict)
    persisted_cad_view_states: dict[UUID, CadViewState] = field(default_factory=dict)
    cam_snapshot: CamProjectSnapshot = field(default_factory=CamProjectSnapshot)
    persisted_cam_snapshot: CamProjectSnapshot = field(default_factory=CamProjectSnapshot)
    cam3d_config: Cam3DProjectConfig | None = None
    persisted_cam3d_config: Cam3DProjectConfig | None = None
    lathe_snapshot: LatheProjectSnapshot | None = None
    persisted_lathe_snapshot: LatheProjectSnapshot | None = None
    lathe_restore_diagnostics: tuple[LatheRestoreDiagnostic, ...] = ()
    lathe_persistence_loaded: bool = False
    read_only: bool = False
    replaced_directory_name: str | None = None
