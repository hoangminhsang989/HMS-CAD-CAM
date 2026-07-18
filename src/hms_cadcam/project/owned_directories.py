"""Ownership metadata and conservative cleanup for HMS temporary directories."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.project.constants import (
    APPLICATION_VERSION,
    OWNED_DIRECTORY_FORMAT,
    OWNED_DIRECTORY_FORMAT_VERSION,
    OWNED_DIRECTORY_METADATA_FILENAME,
)
from hms_cadcam.project.models import datetime_from_json, datetime_to_json, utc_now
from hms_cadcam.project.session_lock import PidChecker, process_is_alive

_STAGING_PATTERN = re.compile(r"^\..+\.[A-Za-z0-9_-]+\.creating$")
_TEMP_PATTERN = re.compile(r"^\.[0-9a-f]{32}\.hms-temp$")


class OwnedDirectoryPurpose(StrEnum):
    """Kinds of application-owned directories recognized by cleanup."""

    STAGING = "staging"
    TEMP = "temp"
    TEMP_ROOT = "temp_root"


@dataclass(frozen=True, slots=True)
class OwnedDirectoryMetadata:
    """Proof that HMS created a temporary or staging directory."""

    format: str
    format_version: int
    purpose: OwnedDirectoryPurpose
    session_id: UUID
    pid: int
    hostname: str
    created_at: datetime
    application_version: str

    def to_dict(self) -> dict[str, Any]:
        """Convert ownership metadata to JSON-compatible values."""
        return {
            "format": self.format,
            "format_version": self.format_version,
            "purpose": self.purpose.value,
            "session_id": str(self.session_id),
            "pid": self.pid,
            "hostname": self.hostname,
            "created_at": datetime_to_json(self.created_at),
            "application_version": self.application_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OwnedDirectoryMetadata":
        """Strictly validate ownership metadata."""
        if not isinstance(data, dict):
            raise TypeError("Owned-directory metadata must be an object")
        if data.get("format") != OWNED_DIRECTORY_FORMAT:
            raise ValueError("Unsupported owned-directory format")
        if data.get("format_version") != OWNED_DIRECTORY_FORMAT_VERSION:
            raise ValueError("Unsupported owned-directory version")
        if type(data.get("format_version")) is not int:
            raise TypeError("Owned-directory version must be an integer")
        if type(data.get("pid")) is not int or data["pid"] <= 0:
            raise TypeError("Owned-directory PID must be a positive integer")
        for field in ("purpose", "session_id", "hostname", "created_at", "application_version"):
            if not isinstance(data.get(field), str) or not data[field]:
                raise TypeError(f"Owned-directory {field} must be a non-empty string")
        return cls(
            format=data["format"],
            format_version=data["format_version"],
            purpose=OwnedDirectoryPurpose(data["purpose"]),
            session_id=UUID(data["session_id"]),
            pid=data["pid"],
            hostname=data["hostname"],
            created_at=datetime_from_json(data["created_at"]),
            application_version=data["application_version"],
        )


def write_owned_directory_metadata(
    directory: Path,
    purpose: OwnedDirectoryPurpose,
    *,
    session_id: UUID | None = None,
) -> OwnedDirectoryMetadata:
    """Write versioned ownership metadata into an HMS-created directory."""
    metadata = OwnedDirectoryMetadata(
        format=OWNED_DIRECTORY_FORMAT,
        format_version=OWNED_DIRECTORY_FORMAT_VERSION,
        purpose=purpose,
        session_id=session_id or uuid4(),
        pid=os.getpid(),
        hostname=socket.gethostname(),
        created_at=utc_now(),
        application_version=APPLICATION_VERSION,
    )
    path = directory / OWNED_DIRECTORY_METADATA_FILENAME
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(metadata.to_dict(), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return metadata


def read_owned_directory_metadata(directory: Path) -> OwnedDirectoryMetadata:
    """Read strict ownership metadata, raising on any ambiguity."""
    path = directory / OWNED_DIRECTORY_METADATA_FILENAME
    if path.is_symlink():
        raise ValueError("Ownership metadata cannot be a symbolic link")
    data = json.loads(path.read_text(encoding="utf-8"))
    return OwnedDirectoryMetadata.from_dict(data)


def create_owned_temp_directory(temp_root: Path) -> Path:
    """Create an allowlisted HMS temporary child with ownership metadata."""
    directory = temp_root / f".{uuid4().hex}.hms-temp"
    directory.mkdir()
    try:
        write_owned_directory_metadata(directory, OwnedDirectoryPurpose.TEMP)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return directory


def cleanup_stale_owned_directories(
    parent: Path,
    minimum_age: timedelta,
    *,
    now: datetime | None = None,
    hostname: str | None = None,
    pid_checker: PidChecker | None = None,
) -> tuple[Path, ...]:
    """Delete only recognized, old HMS directories whose local process is dead."""
    if not parent.is_dir():
        return ()
    reference_time = now or utc_now()
    local_hostname = hostname or socket.gethostname()
    check_pid = pid_checker or process_is_alive
    removed: list[Path] = []
    for candidate in parent.iterdir():
        if (
            not candidate.is_dir()
            or candidate.is_symlink()
            or (hasattr(candidate, "is_junction") and candidate.is_junction())
        ):
            continue
        try:
            metadata = read_owned_directory_metadata(candidate)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not _matches_allowlist(candidate, metadata.purpose):
            continue
        if reference_time - metadata.created_at < minimum_age:
            continue
        if metadata.hostname.casefold() != local_hostname.casefold():
            continue
        if check_pid(metadata.pid) is not False:
            continue
        shutil.rmtree(candidate)
        removed.append(candidate)
    return tuple(removed)


def _matches_allowlist(path: Path, purpose: OwnedDirectoryPurpose) -> bool:
    if purpose is OwnedDirectoryPurpose.STAGING:
        return bool(_STAGING_PATTERN.fullmatch(path.name))
    if purpose is OwnedDirectoryPurpose.TEMP:
        return bool(_TEMP_PATTERN.fullmatch(path.name))
    return False
