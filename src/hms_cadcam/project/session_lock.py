"""Versioned exclusive session locks for HMS project directories."""

from __future__ import annotations

import ctypes
import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from hms_cadcam.project.constants import (
    APPLICATION_VERSION,
    SESSION_LOCK_FILENAME,
    SESSION_LOCK_FORMAT,
    SESSION_LOCK_FORMAT_VERSION,
)
from hms_cadcam.project.exceptions import (
    ProjectLockUnknownError,
    ProjectLockedError,
    SessionLockError,
)
from hms_cadcam.project.models import datetime_from_json, datetime_to_json, utc_now

PidChecker = Callable[[int], bool | None]


class LockState(StrEnum):
    """Safety classification for an existing session lock."""

    ACTIVE = "active"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SessionLockMetadata:
    """Versioned JSON contents stored in session.lock."""

    format: str
    format_version: int
    project_id: UUID
    session_id: UUID
    pid: int
    hostname: str
    created_at: datetime
    application_version: str

    def to_dict(self) -> dict[str, Any]:
        """Convert lock metadata to JSON-compatible values."""
        return {
            "format": self.format,
            "format_version": self.format_version,
            "project_id": str(self.project_id),
            "session_id": str(self.session_id),
            "pid": self.pid,
            "hostname": self.hostname,
            "created_at": datetime_to_json(self.created_at),
            "application_version": self.application_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionLockMetadata":
        """Decode and strictly validate session-lock JSON data."""
        if not isinstance(data, dict):
            raise TypeError("Session lock must be an object")
        if data.get("format") != SESSION_LOCK_FORMAT:
            raise ValueError("Unsupported session-lock format")
        if type(data.get("format_version")) is not int:
            raise TypeError("Session-lock format_version must be an integer")
        if data["format_version"] != SESSION_LOCK_FORMAT_VERSION:
            raise ValueError("Unsupported session-lock version")
        if type(data.get("pid")) is not int or data["pid"] <= 0:
            raise TypeError("Session-lock PID must be a positive integer")
        for field in (
            "project_id",
            "session_id",
            "hostname",
            "created_at",
            "application_version",
        ):
            if not isinstance(data.get(field), str) or not data[field]:
                raise TypeError(f"Session-lock {field} must be a non-empty string")
        return cls(
            format=data["format"],
            format_version=data["format_version"],
            project_id=UUID(data["project_id"]),
            session_id=UUID(data["session_id"]),
            pid=data["pid"],
            hostname=data["hostname"],
            created_at=datetime_from_json(data["created_at"]),
            application_version=data["application_version"],
        )


@dataclass(frozen=True, slots=True)
class LockInspection:
    """Classification and optional decoded metadata for a lock file."""

    state: LockState
    metadata: SessionLockMetadata | None


class SessionLockManager:
    """Acquire and release project locks without breaking unknown owners."""

    def __init__(
        self,
        *,
        session_id: UUID | None = None,
        pid: int | None = None,
        hostname: str | None = None,
        pid_checker: PidChecker | None = None,
    ) -> None:
        self.session_id = session_id or uuid4()
        self.pid = pid or os.getpid()
        self.hostname = hostname or socket.gethostname()
        self._pid_checker = pid_checker or process_is_alive
        self._owned: dict[Path, UUID] = {}

    def inspect(self, project_root: Path) -> LockInspection | None:
        """Read and classify a lock; malformed data is always unknown."""
        lock_path = project_root / SESSION_LOCK_FILENAME
        if not lock_path.exists():
            return None
        try:
            raw = json.loads(lock_path.read_text(encoding="utf-8"))
            metadata = SessionLockMetadata.from_dict(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return LockInspection(LockState.UNKNOWN, None)
        if metadata.hostname.casefold() != self.hostname.casefold():
            return LockInspection(LockState.UNKNOWN, metadata)
        alive = self._pid_checker(metadata.pid)
        if alive is True:
            return LockInspection(LockState.ACTIVE, metadata)
        if alive is False:
            return LockInspection(LockState.STALE, metadata)
        return LockInspection(LockState.UNKNOWN, metadata)

    def acquire(self, project_root: Path, project_id: UUID) -> SessionLockMetadata:
        """Create an exclusive lock, replacing only a confirmed stale lock."""
        root = project_root.resolve()
        lock_path = root / SESSION_LOCK_FILENAME
        inspection = self.inspect(root)
        if inspection is not None:
            if inspection.state is LockState.ACTIVE:
                raise ProjectLockedError(f"Project is locked by an active session: {root}")
            if inspection.state is LockState.UNKNOWN:
                raise ProjectLockUnknownError(f"Project lock owner is unknown: {root}")
            try:
                lock_path.unlink()
            except OSError as error:
                raise SessionLockError(str(error)) from error
        metadata = SessionLockMetadata(
            format=SESSION_LOCK_FORMAT,
            format_version=SESSION_LOCK_FORMAT_VERSION,
            project_id=project_id,
            session_id=self.session_id,
            pid=self.pid,
            hostname=self.hostname,
            created_at=utc_now(),
            application_version=APPLICATION_VERSION,
        )
        try:
            with lock_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(metadata.to_dict(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as error:
            raise ProjectLockedError(f"Project lock was acquired concurrently: {root}") from error
        except OSError as error:
            raise SessionLockError(str(error)) from error
        self._owned[root] = metadata.session_id
        return metadata

    def ensure_available(self, project_root: Path) -> None:
        """Reject active or unknown locks without modifying the lock file."""
        inspection = self.inspect(project_root.resolve())
        if inspection is None or inspection.state is LockState.STALE:
            return
        if inspection.state is LockState.ACTIVE:
            root = project_root.resolve()
            if (
                inspection.metadata is not None
                and inspection.metadata.session_id == self.session_id
                and self._owned.get(root) == self.session_id
            ):
                return
            raise ProjectLockedError(f"Project is locked by an active session: {project_root}")
        raise ProjectLockUnknownError(f"Project lock owner is unknown: {project_root}")

    def release(self, project_root: Path) -> None:
        """Remove only a lock owned by this manager's application session."""
        root = project_root.resolve()
        expected_session_id = self._owned.get(root)
        if expected_session_id is None:
            return
        inspection = self.inspect(root)
        if (
            inspection is None
            or inspection.metadata is None
            or inspection.metadata.session_id != expected_session_id
        ):
            raise SessionLockError(f"Session lock ownership changed: {root}")
        try:
            (root / SESSION_LOCK_FILENAME).unlink()
        except OSError as error:
            raise SessionLockError(str(error)) from error
        del self._owned[root]


def process_is_alive(pid: int) -> bool | None:
    """Return process liveness without sending a signal on Windows."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _windows_process_is_alive(pid: int) -> bool | None:
    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        error_code = ctypes.get_last_error()
        if error_code == error_invalid_parameter:
            return False
        if error_code == error_access_denied:
            return True
        return None
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
