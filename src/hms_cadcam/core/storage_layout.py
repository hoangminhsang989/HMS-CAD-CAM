"""Versioned machine/user directory layout inspection and bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
import logging
from pathlib import Path
from typing import Callable

from hms_cadcam.core.paths import (
    APPLICATION_FAMILY,
    AppPathKind,
    ApplicationPathsService,
    PathResolutionMode,
    PROGRAM_DATA_CHILDREN,
    STORAGE_LAYOUT_VERSION,
    USER_LOCAL_CHILDREN,
    USER_ROAMING_CHILDREN,
)
from hms_cadcam.core.storage_io import (
    AtomicJsonWriter,
    AtomicWriteError,
    MachineResource,
    ResourceFileLock,
    StorageLockError,
    checksum_value,
)
from hms_cadcam.core.storage_security import validate_storage_write_path

LOGGER = logging.getLogger(__name__)
STORAGE_LAYOUT_SCHEMA_VERSION = 1
STORAGE_LAYOUT_FILENAME = "storage-layout.json"
WRITER_VERSION = "0.1.0"


class StorageLayoutStatus(StrEnum):
    READY = "READY"
    INCOMPLETE = "INCOMPLETE"
    READ_ONLY = "READ_ONLY"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    UNSAFE_PATH = "UNSAFE_PATH"
    ADMIN_INSTALL_REQUIRED = "ADMIN_INSTALL_REQUIRED"
    FAILED = "FAILED"


class BootstrapOutcome(StrEnum):
    ALREADY_READY = "ALREADY_READY"
    CREATED = "CREATED"
    REPAIRED = "REPAIRED"
    BLOCKED = "BLOCKED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True, slots=True)
class StorageLayoutManifest:
    schema_version: int
    layout_version: int
    application_family: str
    created_at: str
    updated_at: str
    install_root_reference: str
    program_data_root: str
    required_directories: tuple[str, ...]
    optional_directories: tuple[str, ...]
    migration_state: str
    writer_version: str
    checksum: str

    @classmethod
    def create(
        cls,
        paths: ApplicationPathsService,
        *,
        created_at: str | None = None,
        migration_state: str = "NOT_SCANNED",
    ) -> "StorageLayoutManifest":
        timestamp = datetime.now(timezone.utc).isoformat()
        body = {
            "schema_version": STORAGE_LAYOUT_SCHEMA_VERSION,
            "layout_version": STORAGE_LAYOUT_VERSION,
            "application_family": APPLICATION_FAMILY,
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
            "install_root_reference": str(paths.path(AppPathKind.INSTALL_ROOT)),
            "program_data_root": str(paths.path(AppPathKind.PROGRAM_DATA_ROOT)),
            "required_directories": tuple(PROGRAM_DATA_CHILDREN.values()),
            "optional_directories": (),
            "migration_state": migration_state,
            "writer_version": WRITER_VERSION,
        }
        return cls(**body, checksum=checksum_value(body))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "layout_version": self.layout_version,
            "application_family": self.application_family,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "install_root_reference": self.install_root_reference,
            "program_data_root": self.program_data_root,
            "required_directories": list(self.required_directories),
            "optional_directories": list(self.optional_directories),
            "migration_state": self.migration_state,
            "writer_version": self.writer_version,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StorageLayoutManifest":
        if not isinstance(value, dict):
            raise ValueError("Storage layout manifest must be an object")
        required_keys = {
            "schema_version",
            "layout_version",
            "application_family",
            "created_at",
            "updated_at",
            "install_root_reference",
            "program_data_root",
            "required_directories",
            "optional_directories",
            "migration_state",
            "writer_version",
            "checksum",
        }
        if set(value) != required_keys:
            raise ValueError("Storage layout manifest has unknown or missing fields")
        required = value["required_directories"]
        optional = value["optional_directories"]
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise TypeError("required_directories must be a string list")
        if not isinstance(optional, list) or not all(isinstance(item, str) for item in optional):
            raise TypeError("optional_directories must be a string list")
        manifest = cls(
            schema_version=int(value["schema_version"]),
            layout_version=int(value["layout_version"]),
            application_family=str(value["application_family"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            install_root_reference=str(value["install_root_reference"]),
            program_data_root=str(value["program_data_root"]),
            required_directories=tuple(required),
            optional_directories=tuple(optional),
            migration_state=str(value["migration_state"]),
            writer_version=str(value["writer_version"]),
            checksum=str(value["checksum"]),
        )
        datetime.fromisoformat(manifest.created_at)
        datetime.fromisoformat(manifest.updated_at)
        if manifest.schema_version != STORAGE_LAYOUT_SCHEMA_VERSION:
            raise ValueError("Unsupported storage manifest schema version")
        if manifest.layout_version != STORAGE_LAYOUT_VERSION:
            raise ValueError("Unsupported storage layout version")
        if manifest.application_family != APPLICATION_FAMILY:
            raise ValueError("Wrong application family")
        if manifest.required_directories != tuple(PROGRAM_DATA_CHILDREN.values()):
            raise ValueError("Machine directory contract mismatch")
        body = manifest.to_dict()
        body.pop("checksum")
        if checksum_value(body) != manifest.checksum:
            raise ValueError("Storage layout checksum mismatch")
        return manifest


@dataclass(frozen=True, slots=True)
class StorageLayoutInspection:
    status: StorageLayoutStatus
    install_ready: bool
    program_data_ready: bool
    user_roaming_ready: bool
    user_local_ready: bool
    missing_directories: tuple[str, ...]
    read_only_directories: tuple[str, ...]
    collision_paths: tuple[str, ...]
    manifest_path: Path
    manifest_valid: bool
    layout_version: int | None
    diagnostic_code: str

    @property
    def ready(self) -> bool:
        return self.status is StorageLayoutStatus.READY


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    outcome: BootstrapOutcome
    inspection: StorageLayoutInspection
    created_directories: tuple[str, ...]
    rolled_back_directories: tuple[str, ...]
    manifest_written: bool
    diagnostic_code: str


class StorageBootstrapService:
    """Create only permitted storage directories with transaction rollback."""

    def __init__(
        self,
        paths: ApplicationPathsService,
        *,
        writer: AtomicJsonWriter | None = None,
        mkdir: Callable[[Path], None] | None = None,
    ) -> None:
        self.paths = paths
        self._writer = writer or AtomicJsonWriter()
        self._mkdir = mkdir or (lambda path: path.mkdir())

    @property
    def manifest_path(self) -> Path:
        return self.paths.path(AppPathKind.MACHINE_CONFIG) / STORAGE_LAYOUT_FILENAME

    def inspect(self) -> StorageLayoutInspection:
        install = self.paths.resolve(AppPathKind.INSTALL_ROOT)
        machine_root = self.paths.path(AppPathKind.PROGRAM_DATA_ROOT)
        required_kinds = tuple(PROGRAM_DATA_CHILDREN)
        user_kinds = (*USER_ROAMING_CHILDREN, *USER_LOCAL_CHILDREN)
        missing: list[str] = []
        read_only: list[str] = []
        collisions: list[str] = []
        unsafe = False
        for kind in (*required_kinds, *user_kinds):
            resolved = self.paths.resolve(kind)
            target_root = self._root_for(kind)
            validation = validate_storage_write_path(
                target_root,
                resolved.physical_path,
                expect_directory=True,
            )
            if resolved.exists and not resolved.physical_path.is_dir():
                collisions.append(str(resolved.physical_path))
            elif not resolved.exists:
                missing.append(str(resolved.physical_path))
            elif not resolved.writable:
                read_only.append(str(resolved.physical_path))
            unsafe = unsafe or (
                resolved.exists and not validation.safe
            )
        manifest: StorageLayoutManifest | None = None
        manifest_valid = False
        unsupported = False
        if self.manifest_path.is_file():
            try:
                manifest = StorageLayoutManifest.from_dict(
                    json.loads(self.manifest_path.read_text(encoding="utf-8"))
                )
                manifest_valid = (
                    Path(manifest.program_data_root) == machine_root
                    and Path(manifest.install_root_reference)
                    == self.paths.path(AppPathKind.INSTALL_ROOT)
                )
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                unsupported = True
        if unsafe:
            status = StorageLayoutStatus.UNSAFE_PATH
            code = "UNSAFE_PATH"
        elif collisions:
            status = StorageLayoutStatus.UNSAFE_PATH
            code = "FILE_DIRECTORY_COLLISION"
        elif self.paths.mode is PathResolutionMode.PRODUCTION and not machine_root.exists():
            status = StorageLayoutStatus.ADMIN_INSTALL_REQUIRED
            code = "ADMIN_INSTALL_REQUIRED"
        elif read_only:
            status = StorageLayoutStatus.READ_ONLY
            code = "READ_ONLY"
        elif unsupported:
            status = StorageLayoutStatus.UNSUPPORTED_VERSION
            code = "UNSUPPORTED_LAYOUT_MANIFEST"
        elif missing or not manifest_valid:
            status = StorageLayoutStatus.INCOMPLETE
            code = "INCOMPLETE_LAYOUT"
        else:
            status = StorageLayoutStatus.READY
            code = "READY"
        return StorageLayoutInspection(
            status=status,
            install_ready=install.exists and install.readable,
            program_data_ready=all(
                self.paths.path(kind).is_dir() for kind in required_kinds
            ),
            user_roaming_ready=all(
                self.paths.path(kind).is_dir() for kind in USER_ROAMING_CHILDREN
            ),
            user_local_ready=all(
                self.paths.path(kind).is_dir() for kind in USER_LOCAL_CHILDREN
            ),
            missing_directories=tuple(missing),
            read_only_directories=tuple(read_only),
            collision_paths=tuple(collisions),
            manifest_path=self.manifest_path,
            manifest_valid=manifest_valid,
            layout_version=None if manifest is None else manifest.layout_version,
            diagnostic_code=code,
        )

    def bootstrap(self) -> BootstrapResult:
        before = self.inspect()
        if before.ready:
            return BootstrapResult(
                BootstrapOutcome.ALREADY_READY,
                before,
                (),
                (),
                False,
                "READY",
            )
        if before.collision_paths or before.status in {
            StorageLayoutStatus.UNSAFE_PATH,
            StorageLayoutStatus.UNSUPPORTED_VERSION,
        }:
            return BootstrapResult(
                BootstrapOutcome.BLOCKED,
                before,
                (),
                (),
                False,
                before.diagnostic_code,
            )
        machine_root = self.paths.path(AppPathKind.PROGRAM_DATA_ROOT)
        had_existing_layout = any(
            self.paths.path(kind).exists()
            for kind in (
                AppPathKind.PROGRAM_DATA_ROOT,
                AppPathKind.USER_ROAMING_ROOT,
                AppPathKind.USER_LOCAL_ROOT,
                *PROGRAM_DATA_CHILDREN,
                *USER_ROAMING_CHILDREN,
                *USER_LOCAL_CHILDREN,
            )
        )
        if (
            self.paths.mode is PathResolutionMode.PRODUCTION
            and not machine_root.is_dir()
        ):
            return BootstrapResult(
                BootstrapOutcome.BLOCKED,
                before,
                (),
                (),
                False,
                "ADMIN_INSTALL_REQUIRED",
            )
        created: list[Path] = []
        rolled_back: list[str] = []
        manifest_written = False
        manifest_existed = self.manifest_path.exists()
        try:
            roots = (
                machine_root,
                self.paths.path(AppPathKind.USER_ROAMING_ROOT),
                self.paths.path(AppPathKind.USER_LOCAL_ROOT),
            )
            for root in roots:
                if not root.exists():
                    self._create_directory(root, root.parent, created)
            for kind in (
                *PROGRAM_DATA_CHILDREN,
                *USER_ROAMING_CHILDREN,
                *USER_LOCAL_CHILDREN,
            ):
                target = self.paths.path(kind)
                if not target.exists():
                    self._create_directory(target, self._root_for(kind), created)
                elif not target.is_dir():
                    raise NotADirectoryError(str(target))
            with ResourceFileLock(machine_root, MachineResource.CONFIG):
                existing_created = None
                if self.manifest_path.is_file():
                    existing_created = StorageLayoutManifest.from_dict(
                        json.loads(self.manifest_path.read_text(encoding="utf-8"))
                    ).created_at
                manifest = StorageLayoutManifest.create(
                    self.paths,
                    created_at=existing_created,
                )
                self._writer.write(machine_root, self.manifest_path, manifest.to_dict())
                manifest_written = True
                StorageLayoutManifest.from_dict(
                    json.loads(self.manifest_path.read_text(encoding="utf-8"))
                )
            after = self.inspect()
            if not after.ready:
                raise RuntimeError(f"Bootstrap validation failed: {after.diagnostic_code}")
            outcome = (
                BootstrapOutcome.REPAIRED
                if had_existing_layout
                else BootstrapOutcome.CREATED
            )
            return BootstrapResult(
                outcome,
                after,
                tuple(str(path) for path in created),
                (),
                manifest_written,
                "READY",
            )
        except (
            AtomicWriteError,
            StorageLockError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            LOGGER.error("Không thể bootstrap storage layout: %s", exc)
            diagnostic = (
                "PERMISSION_DENIED"
                if isinstance(exc, PermissionError)
                else "ATOMIC_WRITE_FAILED"
                if isinstance(exc, AtomicWriteError)
                else "LOCK_FAILED"
                if isinstance(exc, StorageLockError)
                else "BOOTSTRAP_FAILED"
            )
            if manifest_written and not manifest_existed:
                self.manifest_path.unlink(missing_ok=True)
            for path in reversed(created):
                try:
                    path.rmdir()
                    rolled_back.append(str(path))
                except OSError:
                    # Never remove a directory once another process or the
                    # user has placed data inside it.
                    continue
            return BootstrapResult(
                BootstrapOutcome.ROLLED_BACK,
                self.inspect(),
                tuple(str(path) for path in created),
                tuple(rolled_back),
                manifest_written,
                diagnostic,
            )

    def _create_directory(
        self,
        target: Path,
        containment_root: Path,
        created: list[Path],
    ) -> None:
        validation = validate_storage_write_path(
            containment_root,
            target,
            expect_directory=True,
        )
        if not validation.safe:
            raise PermissionError(validation.code.value)
        self._mkdir(target)
        created.append(target)

    def _root_for(self, kind: AppPathKind) -> Path:
        if kind in PROGRAM_DATA_CHILDREN:
            return self.paths.path(AppPathKind.PROGRAM_DATA_ROOT)
        if kind in USER_ROAMING_CHILDREN:
            return self.paths.path(AppPathKind.USER_ROAMING_ROOT)
        if kind in USER_LOCAL_CHILDREN:
            return self.paths.path(AppPathKind.USER_LOCAL_ROOT)
        raise ValueError(kind.value)


__all__ = [
    "BootstrapOutcome",
    "BootstrapResult",
    "STORAGE_LAYOUT_FILENAME",
    "STORAGE_LAYOUT_SCHEMA_VERSION",
    "StorageBootstrapService",
    "StorageLayoutInspection",
    "StorageLayoutManifest",
    "StorageLayoutStatus",
]
