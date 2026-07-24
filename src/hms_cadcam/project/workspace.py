"""Typed state shared by standalone CAD documents and CAM project workspaces."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.project.models import utc_now


class DocumentMode(StrEnum):
    """The two persistence/lifecycle modes exposed by HMS."""

    CAD_DOCUMENT = "cad_document"
    CAM_PROJECT = "cam_project"

    @property
    def display_text(self) -> str:
        """Return production Vietnamese text without leaking the enum value."""
        return {
            DocumentMode.CAD_DOCUMENT: "Tài liệu CAD",
            DocumentMode.CAM_PROJECT: "Dự án CAM",
        }[self]


@dataclass(frozen=True, slots=True)
class WorkspaceState:
    """Mode-aware identity and lifecycle state consumed by application/UI code."""

    mode: DocumentMode
    document_id: UUID | None
    project_id: UUID | None
    display_name: str
    physical_path: Path | None
    source_path: Path | None
    suggested_save_directory: Path
    dirty: bool
    read_only: bool
    opened_at: datetime
    session_id: UUID
    format_version: int
    lifecycle_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.mode, DocumentMode):
            raise TypeError("Workspace mode must be DocumentMode")
        if self.mode is DocumentMode.CAD_DOCUMENT:
            if self.document_id is None or self.project_id is not None:
                raise ValueError("CAD document identity is invalid")
        elif self.project_id is None or self.document_id is not None:
            raise ValueError("CAM project identity is invalid")
        if not self.display_name.strip():
            raise ValueError("Workspace display name cannot be empty")
        if self.format_version < 1 or self.lifecycle_generation < 1:
            raise ValueError("Workspace versions must be positive")
        if self.opened_at.tzinfo is None:
            raise ValueError("opened_at must include timezone information")

    @property
    def identity(self) -> UUID:
        """Return the active document/project UUID."""
        identity = self.document_id or self.project_id
        if identity is None:  # guarded by __post_init__
            raise RuntimeError("Workspace identity is unavailable")
        return identity

    def with_changes(self, **changes: object) -> "WorkspaceState":
        """Return a validated state carrying a lifecycle update."""
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Trace one immutable source without changing its external filename."""

    original_filename: str
    original_path: Path | None
    internal_filename: str
    source_fingerprint: str
    imported_at: datetime
    importer: str
    units: str
    geometry_type: str
    read_only: bool

    def __post_init__(self) -> None:
        if not self.original_filename or not self.internal_filename:
            raise ValueError("Source filenames cannot be empty")
        if len(self.source_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.source_fingerprint
        ):
            raise ValueError("Source fingerprint must be lowercase SHA-256")
        if self.imported_at.tzinfo is None:
            raise ValueError("imported_at must include timezone information")

    def to_dict(self) -> dict[str, object]:
        """Convert provenance to strict JSON-compatible data."""
        from hms_cadcam.project.models import datetime_to_json

        return {
            "original_filename": self.original_filename,
            "original_path": (
                None if self.original_path is None else str(self.original_path)
            ),
            "internal_filename": self.internal_filename,
            "source_fingerprint": self.source_fingerprint,
            "imported_at": datetime_to_json(self.imported_at),
            "importer": self.importer,
            "units": self.units,
            "geometry_type": self.geometry_type,
            "read_only": self.read_only,
        }

    @classmethod
    def from_dict(cls, data: object) -> "SourceProvenance":
        """Decode strict source provenance from a container/workspace manifest."""
        from hms_cadcam.project.models import datetime_from_json

        if not isinstance(data, dict):
            raise TypeError("source_provenance must be an object")
        required_text = (
            "original_filename",
            "internal_filename",
            "source_fingerprint",
            "imported_at",
            "importer",
            "units",
            "geometry_type",
        )
        if any(not isinstance(data.get(key), str) for key in required_text):
            raise TypeError("source_provenance text fields must be strings")
        raw_original_path = data.get("original_path")
        if raw_original_path is not None and not isinstance(raw_original_path, str):
            raise TypeError("original_path must be a string or null")
        if not isinstance(data.get("read_only"), bool):
            raise TypeError("read_only must be bool")
        return cls(
            original_filename=str(data["original_filename"]),
            original_path=(
                None if raw_original_path is None else Path(raw_original_path)
            ),
            internal_filename=str(data["internal_filename"]),
            source_fingerprint=str(data["source_fingerprint"]),
            imported_at=datetime_from_json(str(data["imported_at"])),
            importer=str(data["importer"]),
            units=str(data["units"]),
            geometry_type=str(data["geometry_type"]),
            read_only=bool(data["read_only"]),
        )


@dataclass(slots=True)
class CadDocumentSession:
    """Runtime-only standalone document and the geometry used by the importer."""

    state: WorkspaceState
    geometry_path: Path
    provenance: SourceProvenance
    container_id: UUID = field(default_factory=uuid4)
    geometry_version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    cad_metadata: dict[str, Any] = field(default_factory=dict)
    display_state: dict[str, Any] = field(default_factory=dict)
    extraction_root: Path | None = None
    recovery_metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.container_id, UUID) or self.container_id.int == 0:
            raise ValueError("Container identity must be a non-zero UUID")
        if type(self.geometry_version) is not int or self.geometry_version < 1:
            raise ValueError("Geometry version must be positive")

    def mark_dirty(self, value: bool = True) -> None:
        """Update dirty state without exposing mutable enum/string routing."""
        self.state = self.state.with_changes(dirty=value)


@dataclass(frozen=True, slots=True)
class PreparedDocumentOpen:
    """Validated input that can be handed to the existing CAD import pipeline."""

    request_id: UUID
    session: CadDocumentSession

    @classmethod
    def for_session(cls, session: CadDocumentSession) -> "PreparedDocumentOpen":
        """Create a unique prepared-open token."""
        return cls(request_id=uuid4(), session=session)
