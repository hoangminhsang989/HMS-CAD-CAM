"""Checksummed retention backups for machine-wide resources only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

from hms_cadcam.core.paths import AppPathKind, ApplicationPathsService
from hms_cadcam.core.storage_io import (
    AtomicBytesWriter,
    AtomicJsonWriter,
    AtomicWriteError,
    MachineResource,
)
from hms_cadcam.core.storage_security import validate_storage_write_path

LOGGER = logging.getLogger(__name__)
BACKUP_SCHEMA_VERSION = 1
PRE_RESTORE_BACKUP_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class MachineBackupRecord:
    schema_version: int
    resource_type: MachineResource
    created_at_utc: str
    source_path: str
    source_version: str
    payload_path: str
    payload_size: int
    checksum_sha256: str
    metadata_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "resource_type": self.resource_type.value,
            "created_at_utc": self.created_at_utc,
            "source_path": self.source_path,
            "source_version": self.source_version,
            "payload_path": self.payload_path,
            "payload_size": self.payload_size,
            "checksum_sha256": self.checksum_sha256,
        }

    @classmethod
    def from_dict(cls, value: object, *, metadata_path: Path) -> "MachineBackupRecord":
        if not isinstance(value, dict):
            raise ValueError("Backup metadata must be an object")
        keys = {
            "schema_version",
            "resource_type",
            "created_at_utc",
            "source_path",
            "source_version",
            "payload_path",
            "payload_size",
            "checksum_sha256",
        }
        if set(value) != keys:
            raise ValueError("Backup metadata fields mismatch")
        record = cls(
            schema_version=int(value["schema_version"]),
            resource_type=MachineResource(str(value["resource_type"])),
            created_at_utc=str(value["created_at_utc"]),
            source_path=str(value["source_path"]),
            source_version=str(value["source_version"]),
            payload_path=str(value["payload_path"]),
            payload_size=int(value["payload_size"]),
            checksum_sha256=str(value["checksum_sha256"]),
            metadata_path=str(metadata_path),
        )
        if record.schema_version != BACKUP_SCHEMA_VERSION:
            raise ValueError("Unsupported backup metadata version")
        datetime.fromisoformat(record.created_at_utc)
        if len(record.checksum_sha256) != 64 or record.payload_size < 0:
            raise ValueError("Invalid backup checksum or size")
        return record


@dataclass(frozen=True, slots=True)
class PreRestoreBackupRecord:
    """Validated byte-for-byte safety copy created before restore publish."""

    resource_id: str
    category: str
    scope: str
    original_path: str
    backup_path: str
    original_size: int
    original_checksum: str
    backup_checksum: str
    created_at_utc: str
    transaction_id: str
    validation_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PRE_RESTORE_BACKUP_SCHEMA_VERSION,
            "resource_id": self.resource_id,
            "category": self.category,
            "scope": self.scope,
            "original_path": self.original_path,
            "backup_path": self.backup_path,
            "original_size": self.original_size,
            "original_checksum": self.original_checksum,
            "backup_checksum": self.backup_checksum,
            "created_at_utc": self.created_at_utc,
            "transaction_id": self.transaction_id,
            "validation_status": self.validation_status,
        }


class PreRestoreBackupService:
    """Create transaction-scoped backups without crossing storage scopes."""

    def __init__(
        self,
        paths: ApplicationPathsService,
        *,
        bytes_writer: AtomicBytesWriter | None = None,
        json_writer: AtomicJsonWriter | None = None,
    ) -> None:
        self.paths = paths
        self._bytes_writer = bytes_writer or AtomicBytesWriter()
        self._json_writer = json_writer or AtomicJsonWriter()

    def create_backup(
        self,
        source: Path,
        *,
        resource_id: str,
        category: str,
        scope: str,
        transaction_id: str,
    ) -> PreRestoreBackupRecord | None:
        source_path = Path(source)
        if not source_path.is_file():
            return None
        source_root = self._scope_root(scope)
        validation = validate_storage_write_path(
            source_root,
            source_path,
            expect_directory=False,
        )
        if not validation.safe:
            raise AtomicWriteError(
                f"Unsafe pre-restore source: {validation.code.value}"
            )
        payload = source_path.read_bytes()
        original_checksum = hashlib.sha256(payload).hexdigest()
        backup_root = self._backup_root(scope)
        transaction = _safe_component(transaction_id)
        resource_digest = hashlib.sha256(
            str(resource_id).encode("utf-8")
        ).hexdigest()[:8]
        backup_path = (
            backup_root
            / f"{transaction[:8]}-{resource_digest}-{original_checksum[:8]}.bak"
        )
        metadata_path = backup_path.with_suffix(".json")
        for candidate in (backup_path, metadata_path):
            target_validation = validate_storage_write_path(
                backup_root,
                candidate,
                expect_directory=False,
            )
            if not target_validation.safe:
                raise AtomicWriteError(
                    f"Unsafe pre-restore backup target: {target_validation.code.value}"
                )
        self._bytes_writer.write(backup_root, backup_path, payload)
        backup_checksum = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        record = PreRestoreBackupRecord(
            resource_id=str(resource_id),
            category=str(category),
            scope=str(scope),
            original_path=str(source_path),
            backup_path=str(backup_path),
            original_size=len(payload),
            original_checksum=original_checksum,
            backup_checksum=backup_checksum,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            transaction_id=transaction,
            validation_status=(
                "VALID"
                if backup_checksum == original_checksum
                else "CHECKSUM_MISMATCH"
            ),
        )
        if record.validation_status != "VALID":
            backup_path.unlink(missing_ok=True)
            raise AtomicWriteError("Pre-restore backup checksum mismatch")
        try:
            self._json_writer.write(
                backup_root,
                metadata_path,
                record.to_dict(),
            )
        except (OSError, RuntimeError, ValueError, TypeError):
            backup_path.unlink(missing_ok=True)
            raise
        if not self.validate(record):
            backup_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            raise AtomicWriteError("Pre-restore backup validation failed")
        return record

    def validate(self, record: PreRestoreBackupRecord) -> bool:
        backup_path = Path(record.backup_path)
        backup_root = self._backup_root(record.scope)
        validation = validate_storage_write_path(
            backup_root,
            backup_path,
            expect_directory=False,
        )
        if not validation.safe or not backup_path.is_file():
            return False
        try:
            payload = backup_path.read_bytes()
        except OSError:
            return False
        digest = hashlib.sha256(payload).hexdigest()
        return (
            record.validation_status == "VALID"
            and len(payload) == record.original_size
            and digest == record.original_checksum
            and digest == record.backup_checksum
        )

    def restore_bytes(self, record: PreRestoreBackupRecord) -> bytes:
        if not self.validate(record):
            raise AtomicWriteError("Pre-restore backup validation failed")
        return Path(record.backup_path).read_bytes()

    def new_transaction_id(self) -> str:
        return uuid4().hex[:12]

    def _scope_root(self, scope: str) -> Path:
        if str(scope) == "MACHINE_SHARED":
            return self.paths.path(AppPathKind.PROGRAM_DATA_ROOT)
        if str(scope) == "USER_ROAMING":
            return self.paths.path(AppPathKind.USER_ROAMING_ROOT)
        raise AtomicWriteError(f"Unsupported restore scope: {scope}")

    def _backup_root(self, scope: str) -> Path:
        if str(scope) == "MACHINE_SHARED":
            return self.paths.path(AppPathKind.MACHINE_BACKUPS) / "R"
        if str(scope) == "USER_ROAMING":
            return self.paths.path(AppPathKind.USER_ROAMING_ROOT) / "B"
        raise AtomicWriteError(f"Unsupported restore scope: {scope}")


class MachineBackupService:
    """Create and validate backups below ProgramData/Backups."""

    def __init__(
        self,
        paths: ApplicationPathsService,
        *,
        retention_per_resource: int = 5,
        bytes_writer: AtomicBytesWriter | None = None,
        json_writer: AtomicJsonWriter | None = None,
    ) -> None:
        self.paths = paths
        self.retention_per_resource = max(1, int(retention_per_resource))
        self._bytes_writer = bytes_writer or AtomicBytesWriter()
        self._json_writer = json_writer or AtomicJsonWriter()

    def create_backup(
        self,
        source: Path,
        resource: MachineResource,
        *,
        source_version: str,
    ) -> MachineBackupRecord | None:
        source_path = Path(source)
        if not source_path.exists():
            return None
        self._validate_source(source_path)
        payload = source_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        timestamp = datetime.now(timezone.utc)
        stamp = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        backup_root = self.paths.path(AppPathKind.MACHINE_BACKUPS)
        resource_root = backup_root / MachineResource(resource).value
        basename = f"{stamp}-{digest[:12]}"
        payload_path = resource_root / f"{basename}.backup"
        metadata_path = resource_root / f"{basename}.json"
        validation = validate_storage_write_path(
            backup_root,
            payload_path,
            expect_directory=False,
        )
        if not validation.safe:
            raise AtomicWriteError(f"Unsafe backup target: {validation.code.value}")
        self._bytes_writer.write(backup_root, payload_path, payload)
        record = MachineBackupRecord(
            schema_version=BACKUP_SCHEMA_VERSION,
            resource_type=MachineResource(resource),
            created_at_utc=timestamp.isoformat(),
            source_path=str(source_path),
            source_version=str(source_version),
            payload_path=str(payload_path),
            payload_size=len(payload),
            checksum_sha256=digest,
            metadata_path=str(metadata_path),
        )
        try:
            self._json_writer.write(backup_root, metadata_path, record.to_dict())
        except Exception:
            payload_path.unlink(missing_ok=True)
            raise
        self.enforce_retention(record.resource_type)
        return record

    def validate(self, record: MachineBackupRecord) -> bool:
        payload_path = Path(record.payload_path)
        metadata_path = Path(record.metadata_path)
        backup_root = self.paths.path(AppPathKind.MACHINE_BACKUPS)
        for candidate in (payload_path, metadata_path):
            validation = validate_storage_write_path(
                backup_root,
                candidate,
                expect_directory=False,
            )
            if not validation.safe:
                return False
        try:
            payload = payload_path.read_bytes()
            stored = MachineBackupRecord.from_dict(
                json.loads(metadata_path.read_text(encoding="utf-8")),
                metadata_path=metadata_path,
            )
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return False
        return (
            stored.to_dict() == record.to_dict()
            and len(payload) == record.payload_size
            and hashlib.sha256(payload).hexdigest() == record.checksum_sha256
        )

    def restore_bytes(self, record: MachineBackupRecord) -> bytes:
        if not self.validate(record):
            raise ValueError("Backup validation failed")
        return Path(record.payload_path).read_bytes()

    def records(self, resource: MachineResource) -> tuple[MachineBackupRecord, ...]:
        root = self.paths.path(AppPathKind.MACHINE_BACKUPS) / resource.value
        if not root.is_dir():
            return ()
        records: list[MachineBackupRecord] = []
        for metadata_path in sorted(root.glob("*.json"), reverse=True):
            try:
                records.append(
                    MachineBackupRecord.from_dict(
                        json.loads(metadata_path.read_text(encoding="utf-8")),
                        metadata_path=metadata_path,
                    )
                )
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                LOGGER.warning("Bỏ qua metadata backup không hợp lệ: %s", metadata_path)
        return tuple(records)

    def enforce_retention(self, resource: MachineResource) -> tuple[str, ...]:
        removed: list[str] = []
        for record in self.records(resource)[self.retention_per_resource :]:
            for candidate in (Path(record.metadata_path), Path(record.payload_path)):
                validation = validate_storage_write_path(
                    self.paths.path(AppPathKind.MACHINE_BACKUPS),
                    candidate,
                    expect_directory=False,
                )
                if not validation.safe:
                    continue
                candidate.unlink(missing_ok=True)
                removed.append(str(candidate))
        return tuple(removed)

    def _validate_source(self, source: Path) -> None:
        machine_root = self.paths.path(AppPathKind.PROGRAM_DATA_ROOT)
        validation = validate_storage_write_path(
            machine_root,
            source,
            expect_directory=False,
        )
        forbidden = source.name.casefold() == "project.db" or source.suffix.casefold() == ".hms"
        if not validation.safe or forbidden or not source.is_file():
            raise ValueError("Machine backup source is outside the shared-data contract")


def _safe_component(value: str) -> str:
    normalized = "".join(
        character
        if character.isalnum() or character in {"-", "_", "."}
        else "_"
        for character in str(value)
    ).strip("._")
    if not normalized or normalized in {".", ".."}:
        raise AtomicWriteError("Unsafe pre-restore backup identifier")
    return normalized[:48]


__all__ = [
    "BACKUP_SCHEMA_VERSION",
    "MachineBackupRecord",
    "MachineBackupService",
    "PRE_RESTORE_BACKUP_SCHEMA_VERSION",
    "PreRestoreBackupRecord",
    "PreRestoreBackupService",
]
