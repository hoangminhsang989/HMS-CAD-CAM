"""Resource-scoped locks and same-volume atomic JSON publication."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable
from uuid import uuid4

from hms_cadcam.core.storage_security import validate_storage_write_path

LOGGER = logging.getLogger(__name__)


class MachineResource(StrEnum):
    TOOL_LIBRARY = "tool-library"
    POSTS = "posts"
    MACHINES = "machines"
    CONFIG = "config"
    MATERIALS = "materials"
    PROGRAM_TEMPLATES = "program-templates"
    SCHEMAS = "schemas"


class StorageLockError(RuntimeError):
    """Base error for one resource lock."""


class StorageLockTimeoutError(StorageLockError):
    """The resource remained locked until the declared timeout."""


class AtomicWriteError(RuntimeError):
    """Atomic publication or read-after-write validation failed."""


@dataclass(frozen=True, slots=True)
class ResourceLockMetadata:
    resource: MachineResource
    process_id: int
    session_id: str
    created_at_utc: str
    token: str

    def to_dict(self) -> dict[str, object]:
        return {
            "resource": self.resource.value,
            "process_id": self.process_id,
            "session_id": self.session_id,
            "created_at_utc": self.created_at_utc,
            "token": self.token,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ResourceLockMetadata":
        if not isinstance(value, dict):
            raise ValueError("Lock metadata must be an object")
        return cls(
            resource=MachineResource(str(value["resource"])),
            process_id=int(value["process_id"]),
            session_id=str(value["session_id"]),
            created_at_utc=str(value["created_at_utc"]),
            token=str(value["token"]),
        )


class ResourceFileLock(AbstractContextManager["ResourceFileLock"]):
    """Exclusive file lock scoped to one machine-wide resource."""

    def __init__(
        self,
        machine_root: Path,
        resource: MachineResource,
        *,
        timeout_seconds: float = 2.0,
        stale_after_seconds: float = 30.0,
        session_id: str | None = None,
    ) -> None:
        self._machine_root = Path(machine_root)
        self.resource = MachineResource(resource)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.stale_after_seconds = max(1.0, float(stale_after_seconds))
        self.session_id = session_id or uuid4().hex
        self.path = (
            self._machine_root
            / "Config"
            / ".locks"
            / f"{self.resource.value}.lock"
        )
        self._metadata: ResourceLockMetadata | None = None

    @property
    def acquired(self) -> bool:
        return self._metadata is not None

    @property
    def metadata(self) -> ResourceLockMetadata | None:
        return self._metadata

    def acquire(self) -> ResourceLockMetadata:
        validation = validate_storage_write_path(
            self._machine_root,
            self.path,
            expect_directory=False,
        )
        if not validation.safe:
            raise StorageLockError(
                f"Unsafe resource lock path: {validation.code.value}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            metadata = ResourceLockMetadata(
                resource=self.resource,
                process_id=os.getpid(),
                session_id=self.session_id,
                created_at_utc=datetime.now(timezone.utc).isoformat(),
                token=uuid4().hex,
            )
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(metadata.to_dict(), stream, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                self._metadata = metadata
                return metadata
            except FileExistsError:
                if self._remove_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise StorageLockTimeoutError(
                        f"Timed out waiting for {self.resource.value} lock"
                    )
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            except OSError as exc:
                raise StorageLockError(str(exc)) from exc

    def release(self) -> None:
        owned = self._metadata
        if owned is None:
            return
        try:
            current = self._read_metadata()
            if current.token != owned.token:
                raise StorageLockError("Refusing to release a lock owned by another process")
            self.path.unlink(missing_ok=True)
            try:
                self.path.parent.rmdir()
            except OSError:
                # Another resource lock or diagnostic file still owns the
                # private Config/.locks directory.
                pass
        finally:
            self._metadata = None

    def __enter__(self) -> "ResourceFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()

    def _read_metadata(self) -> ResourceLockMetadata:
        try:
            return ResourceLockMetadata.from_dict(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise StorageLockError("Invalid resource lock metadata") from exc

    def _remove_stale_lock(self) -> bool:
        try:
            metadata = self._read_metadata()
            created = datetime.fromisoformat(metadata.created_at_utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age < self.stale_after_seconds or _process_exists(metadata.process_id):
                return False
            self.path.unlink()
            LOGGER.warning(
                "Đã loại bỏ lock hết hạn resource=%s pid=%s",
                self.resource.value,
                metadata.process_id,
            )
            return True
        except FileNotFoundError:
            return True
        except StorageLockError:
            try:
                age = time.time() - self.path.stat().st_mtime
                if age >= self.stale_after_seconds:
                    self.path.unlink()
                    return True
            except OSError:
                return False
            return False
        except OSError:
            return False


class AtomicJsonWriter:
    """Publish canonical UTF-8 JSON and validate the exact bytes afterwards."""

    def __init__(
        self,
        *,
        replace: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self._replace = replace or os.replace

    def write(
        self,
        root: Path,
        target: Path,
        value: object,
    ) -> str:
        owner = Path(root)
        destination = Path(target)
        validation = validate_storage_write_path(
            owner,
            destination,
            expect_directory=False,
        )
        if not validation.safe or not validation.atomic_rename_capable:
            raise AtomicWriteError(
                f"Unsafe atomic target: {validation.code.value}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_json_bytes(value)
        digest = hashlib.sha256(payload).hexdigest()
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp"
        )
        temporary_validation = validate_storage_write_path(
            owner,
            temporary,
            expect_directory=False,
        )
        if not temporary_validation.safe:
            raise AtomicWriteError(
                f"Unsafe atomic staging path: {temporary_validation.code.value}"
            )
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._replace(temporary, destination)
            published = destination.read_bytes()
            if hashlib.sha256(published).hexdigest() != digest:
                raise AtomicWriteError("Read-after-write checksum mismatch")
            json.loads(published.decode("utf-8"))
            return digest
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AtomicWriteError(str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)


class AtomicBytesWriter:
    """Publish arbitrary bytes with same-volume replace and checksum verify."""

    def __init__(
        self,
        *,
        replace: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self._replace = replace or os.replace

    def write(self, root: Path, target: Path, payload: bytes) -> str:
        owner = Path(root)
        destination = Path(target)
        validation = validate_storage_write_path(
            owner,
            destination,
            expect_directory=False,
        )
        if not validation.safe or not validation.atomic_rename_capable:
            raise AtomicWriteError(
                f"Unsafe atomic target: {validation.code.value}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(payload).hexdigest()
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp"
        )
        temporary_validation = validate_storage_write_path(
            owner,
            temporary,
            expect_directory=False,
        )
        if not temporary_validation.safe:
            raise AtomicWriteError(
                f"Unsafe atomic staging path: {temporary_validation.code.value}"
            )
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._replace(temporary, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise AtomicWriteError("Read-after-write checksum mismatch")
            return digest
        except OSError as exc:
            raise AtomicWriteError(str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def checksum_value(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _process_exists(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if process_id == os.getpid():
        return True
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


__all__ = [
    "AtomicBytesWriter",
    "AtomicJsonWriter",
    "AtomicWriteError",
    "MachineResource",
    "ResourceFileLock",
    "ResourceLockMetadata",
    "StorageLockError",
    "StorageLockTimeoutError",
    "canonical_json_bytes",
    "checksum_value",
]
