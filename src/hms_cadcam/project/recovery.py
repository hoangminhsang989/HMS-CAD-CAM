"""Crash assessment, transactional autosave recovery, and .replaced restoration."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.project.autosave import (
    AutosaveFileMetadata,
    AutosaveManager,
    AutosaveSnapshot,
)
from hms_cadcam.project.constants import (
    AUTOSAVE_METADATA_FILENAME,
    BACKUPS_DIRECTORY,
    DATABASE_FILENAME,
    MANIFEST_FILENAME,
    OWNED_DIRECTORY_METADATA_FILENAME,
    RECOVERY_BACKUP_FORMAT,
    RECOVERY_BACKUP_FORMAT_VERSION,
    RECOVERY_BACKUP_METADATA_FILENAME,
)
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import (
    AutosaveSnapshotError,
    ProjectLockUnknownError,
    ProjectLockedError,
    RecoveryRollbackError,
    RecoverySnapshotInvalidError,
    RecoveryTransactionError,
    ReplacedProjectAmbiguousError,
    ReplacedProjectInvalidError,
)
from hms_cadcam.project.filesystem import (
    project_target_path,
    publish_directory,
    sha256_file,
    staging_directory,
)
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import (
    ProjectManifest,
    datetime_from_json,
    datetime_to_json,
    utc_now,
)
from hms_cadcam.project.session_lock import (
    LockInspection,
    LockState,
    SessionLockManager,
    SessionLockMetadata,
)
from hms_cadcam.project.validator import ProjectValidator
from hms_cadcam.cam.cam3d.persistence import (
    CAM3D_CONFIG_DIRECTORY,
    CAM3D_CONFIG_FILENAME,
)

_REPLACED_PATTERN = re.compile(
    r"^\.(?P<target>.+\.HMS)\.(?P<transaction>[0-9a-f]{32})\.replaced$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """Read-only crash assessment for one project before opening it."""

    project_root: Path
    project_id: UUID
    abnormal_close: bool
    stale_lock: SessionLockMetadata | None
    snapshot: AutosaveSnapshot | None


@dataclass(frozen=True, slots=True)
class ReplacedProjectAssessment:
    """One unambiguous validated .replaced directory and its intended target."""

    target_path: Path
    candidate_path: Path
    project_id: UUID


@dataclass(frozen=True, slots=True)
class RecoveryBackupMetadata:
    """Versioned checksums for main files saved before recovery."""

    format: str
    format_version: int
    recovery_id: UUID
    project_id: UUID
    snapshot_id: UUID
    created_at: datetime
    manifest: AutosaveFileMetadata
    database: AutosaveFileMetadata
    cam3d: AutosaveFileMetadata | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert backup metadata to JSON-compatible values."""
        return {
            "format": self.format,
            "format_version": self.format_version,
            "recovery_id": str(self.recovery_id),
            "project_id": str(self.project_id),
            "snapshot_id": str(self.snapshot_id),
            "created_at": datetime_to_json(self.created_at),
            "manifest": self.manifest.to_dict(),
            "database": self.database.to_dict(),
            "cam3d": self.cam3d.to_dict() if self.cam3d is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecoveryBackupMetadata":
        """Strictly decode recovery-backup metadata."""
        if not isinstance(data, dict):
            raise TypeError("Recovery backup metadata must be an object")
        if data.get("format") != RECOVERY_BACKUP_FORMAT:
            raise ValueError("Unsupported recovery backup format")
        if type(data.get("format_version")) is not int:
            raise TypeError("Recovery backup version must be an integer")
        if data["format_version"] != RECOVERY_BACKUP_FORMAT_VERSION:
            raise ValueError("Unsupported recovery backup version")
        return cls(
            format=data["format"],
            format_version=data["format_version"],
            recovery_id=UUID(data["recovery_id"]),
            project_id=UUID(data["project_id"]),
            snapshot_id=UUID(data["snapshot_id"]),
            created_at=datetime_from_json(data["created_at"]),
            manifest=AutosaveFileMetadata.from_dict(data["manifest"]),
            database=AutosaveFileMetadata.from_dict(data["database"]),
            cam3d=(
                AutosaveFileMetadata.from_dict(data["cam3d"])
                if data.get("cam3d") is not None
                else None
            ),
        )


class RecoveryManager:
    """Assess crashes and restore versioned main/config data through safe I/O."""

    def __init__(
        self,
        autosave: AutosaveManager,
        manifest_store: ProjectManifestStore,
        validator: ProjectValidator,
        database: ProjectDatabase,
        session_locks: SessionLockManager,
    ) -> None:
        self._autosave = autosave
        self._manifest_store = manifest_store
        self._validator = validator
        self._database = database
        self._session_locks = session_locks

    def assess(
        self,
        project_root: Path,
        manifest: ProjectManifest,
        lock: LockInspection | None,
    ) -> RecoveryAssessment:
        """Return a recovery candidate only for a matching confirmed stale session."""
        if lock is None or lock.state is not LockState.STALE or lock.metadata is None:
            return RecoveryAssessment(
                project_root=project_root,
                project_id=manifest.project_id,
                abnormal_close=False,
                stale_lock=None,
                snapshot=None,
            )
        try:
            snapshot = self._autosave.load_latest(project_root)
        except AutosaveSnapshotError as error:
            raise RecoverySnapshotInvalidError(str(error)) from error
        if snapshot is not None and snapshot.metadata.project_id != manifest.project_id:
            raise RecoverySnapshotInvalidError("Autosave belongs to another project")
        if snapshot is not None and (
            snapshot.metadata.session_id != lock.metadata.session_id
            or snapshot.metadata.created_at < lock.metadata.created_at
        ):
            snapshot = None
        return RecoveryAssessment(
            project_root=project_root,
            project_id=manifest.project_id,
            abnormal_close=True,
            stale_lock=lock.metadata,
            snapshot=snapshot,
        )

    def recover(self, assessment: RecoveryAssessment) -> Path:
        """Restore a validated autosave and return the persistent rollback backup."""
        if assessment.snapshot is None:
            raise RecoverySnapshotInvalidError("No valid autosave snapshot was selected")
        try:
            latest = self._autosave.load_latest(assessment.project_root)
        except AutosaveSnapshotError as error:
            raise RecoverySnapshotInvalidError(str(error)) from error
        if latest is None or latest.metadata.snapshot_id != assessment.snapshot.metadata.snapshot_id:
            raise RecoverySnapshotInvalidError("Autosave selection changed before recovery")
        current_manifest = self._manifest_store.load(assessment.project_root)
        if current_manifest.project_id != assessment.project_id:
            raise RecoverySnapshotInvalidError("Project identity changed before recovery")
        snapshot_manifest = self._manifest_store.load(latest.path)
        self._validator.validate_references(assessment.project_root, snapshot_manifest)
        if snapshot_manifest.project_id != assessment.project_id:
            raise RecoverySnapshotInvalidError("Snapshot project identity does not match")
        try:
            backup_path = self._create_backup(assessment, latest)
        except RecoveryTransactionError:
            raise
        except Exception as error:
            raise RecoveryTransactionError("Could not create recovery rollback backup") from error
        try:
            self._replace_main_files(assessment.project_root, latest.path)
            restored = self._manifest_store.load(assessment.project_root)
            self._validator.validate_references(assessment.project_root, restored)
            self._database.validate(assessment.project_root / DATABASE_FILENAME)
        except Exception as recovery_error:
            try:
                self._replace_main_files(assessment.project_root, backup_path)
            except Exception as rollback_error:
                raise RecoveryRollbackError(
                    f"Recovery and rollback both failed; backup retained at {backup_path}"
                ) from rollback_error
            raise RecoveryTransactionError(
                f"Recovery failed; original files restored from {backup_path}"
            ) from recovery_error
        return backup_path

    def inspect_replaced_for_open(self, requested_path: Path) -> ReplacedProjectAssessment | None:
        """Find one valid .replaced candidate for a missing requested project."""
        target = requested_path
        match = _REPLACED_PATTERN.fullmatch(requested_path.name)
        if match is not None:
            target = requested_path.parent / match.group("target")
        if target.exists():
            return None
        candidates = self._replaced_candidates(target)
        if not candidates:
            return None
        if len(candidates) != 1:
            raise ReplacedProjectAmbiguousError(
                f"Multiple .replaced candidates exist for {target}"
            )
        candidate = candidates[0]
        try:
            manifest = self._manifest_store.load(candidate)
            self._validator.validate_references(candidate, manifest)
            self._database.validate(candidate / DATABASE_FILENAME)
            expected_target = project_target_path(target.parent, manifest.project_name)
            if expected_target.name.casefold() != target.name.casefold():
                raise ValueError("Replaced project name does not match its target")
            lock = self._session_locks.inspect(candidate)
            if lock is not None and lock.state is LockState.ACTIVE:
                raise ProjectLockedError(f"Replaced project is still active: {candidate}")
            if lock is not None and lock.state is LockState.UNKNOWN:
                raise ProjectLockUnknownError(
                    f"Replaced project lock owner is unknown: {candidate}"
                )
        except (ProjectLockedError, ProjectLockUnknownError):
            raise
        except Exception as error:
            raise ReplacedProjectInvalidError(
                f"Invalid .replaced project candidate: {candidate}"
            ) from error
        return ReplacedProjectAssessment(
            target_path=target,
            candidate_path=candidate,
            project_id=manifest.project_id,
        )

    def restore_replaced(self, assessment: ReplacedProjectAssessment) -> Path:
        """Rename one revalidated candidate back to its missing .HMS target."""
        current = self.inspect_replaced_for_open(assessment.target_path)
        if current is None or current.candidate_path.resolve() != assessment.candidate_path.resolve():
            raise ReplacedProjectAmbiguousError(".replaced candidates changed before restoration")
        try:
            current.candidate_path.rename(current.target_path)
        except OSError as error:
            raise RecoveryTransactionError(str(error)) from error
        return current.target_path

    def _create_backup(
        self,
        assessment: RecoveryAssessment,
        snapshot: AutosaveSnapshot,
    ) -> Path:
        backups_root = assessment.project_root / BACKUPS_DIRECTORY
        backups_root.mkdir(exist_ok=True)
        if self._is_link_or_junction(backups_root) or not backups_root.is_dir():
            raise RecoveryTransactionError("Recovery backups root must be a real directory")
        recovery_id = uuid4()
        target = backups_root / f"recovery-{recovery_id}"
        with staging_directory(backups_root, "recovery-backup") as staging:
            self._copy_fsynced(
                assessment.project_root / MANIFEST_FILENAME,
                staging / MANIFEST_FILENAME,
            )
            self._copy_fsynced(
                assessment.project_root / DATABASE_FILENAME,
                staging / DATABASE_FILENAME,
            )
            source_cam3d = (
                assessment.project_root
                / CAM3D_CONFIG_DIRECTORY
                / CAM3D_CONFIG_FILENAME
            )
            cam3d_metadata = None
            if source_cam3d.exists() and (
                self._is_link_or_junction(source_cam3d)
                or self._is_link_or_junction(source_cam3d.parent)
            ):
                raise RecoveryTransactionError(
                    "Recovery source CAM 3D config must not use links"
                )
            if source_cam3d.is_file():
                destination_cam3d = (
                    staging / CAM3D_CONFIG_DIRECTORY / CAM3D_CONFIG_FILENAME
                )
                destination_cam3d.parent.mkdir()
                self._copy_fsynced(source_cam3d, destination_cam3d)
                cam3d_metadata = self._file_metadata(
                    destination_cam3d,
                    f"{CAM3D_CONFIG_DIRECTORY}/{CAM3D_CONFIG_FILENAME}",
                )
            metadata = RecoveryBackupMetadata(
                format=RECOVERY_BACKUP_FORMAT,
                format_version=RECOVERY_BACKUP_FORMAT_VERSION,
                recovery_id=recovery_id,
                project_id=assessment.project_id,
                snapshot_id=snapshot.metadata.snapshot_id,
                created_at=utc_now(),
                manifest=self._file_metadata(staging / MANIFEST_FILENAME),
                database=self._file_metadata(staging / DATABASE_FILENAME),
                cam3d=cam3d_metadata,
            )
            self._write_json(
                staging / RECOVERY_BACKUP_METADATA_FILENAME,
                metadata.to_dict(),
            )
            self._validate_backup(staging, metadata)
            publish_directory(staging, target)
        return target

    def _validate_backup(
        self,
        backup_path: Path,
        expected: RecoveryBackupMetadata | None = None,
    ) -> RecoveryBackupMetadata:
        if self._is_link_or_junction(backup_path) or not backup_path.is_dir():
            raise RecoveryTransactionError("Recovery backup must be a real directory")
        entries = tuple(backup_path.iterdir())
        if any(self._is_link_or_junction(path) for path in entries):
            raise RecoveryTransactionError("Recovery backup cannot contain links or junctions")
        names = {path.name for path in entries}
        allowed = {
            MANIFEST_FILENAME,
            DATABASE_FILENAME,
            RECOVERY_BACKUP_METADATA_FILENAME,
        }
        optional = {OWNED_DIRECTORY_METADATA_FILENAME, CAM3D_CONFIG_DIRECTORY}
        if not allowed.issubset(names) or names - allowed - optional:
            raise RecoveryTransactionError("Recovery backup file set is invalid")
        if expected is None:
            data = json.loads(
                (backup_path / RECOVERY_BACKUP_METADATA_FILENAME).read_text(encoding="utf-8")
            )
            expected = RecoveryBackupMetadata.from_dict(data)
        has_cam3d = CAM3D_CONFIG_DIRECTORY in names
        if has_cam3d != (expected.cam3d is not None):
            raise RecoveryTransactionError("Recovery backup CAM 3D layout is invalid")
        if expected.cam3d is not None:
            expected_name = f"{CAM3D_CONFIG_DIRECTORY}/{CAM3D_CONFIG_FILENAME}"
            cam3d_root = backup_path / CAM3D_CONFIG_DIRECTORY
            if (
                expected.cam3d.filename != expected_name
                or not cam3d_root.is_dir()
                or self._is_link_or_junction(cam3d_root)
                or {item.name for item in cam3d_root.iterdir()}
                != {CAM3D_CONFIG_FILENAME}
            ):
                raise RecoveryTransactionError("Recovery backup CAM 3D config is invalid")
        self._verify_file(backup_path, expected.manifest)
        self._verify_file(backup_path, expected.database)
        if expected.cam3d is not None:
            self._verify_file(backup_path, expected.cam3d)
        return expected

    def _replace_main_files(self, project_root: Path, source_root: Path) -> None:
        self._validate_replacement_source(source_root)
        transaction_id = uuid4().hex
        manifest_temp = project_root / f".{MANIFEST_FILENAME}.{transaction_id}.recovering"
        database_temp = project_root / f".{DATABASE_FILENAME}.{transaction_id}.recovering"
        cam3d_temp = project_root / f".{CAM3D_CONFIG_FILENAME}.{transaction_id}.recovering"
        source_cam3d = source_root / CAM3D_CONFIG_DIRECTORY / CAM3D_CONFIG_FILENAME
        target_cam3d = project_root / CAM3D_CONFIG_DIRECTORY / CAM3D_CONFIG_FILENAME
        try:
            if target_cam3d.parent.exists() and self._is_link_or_junction(
                target_cam3d.parent
            ):
                raise RecoveryTransactionError(
                    "Project CAM 3D config directory must not be a link"
                )
            self._copy_fsynced(source_root / MANIFEST_FILENAME, manifest_temp)
            self._copy_fsynced(source_root / DATABASE_FILENAME, database_temp)
            if source_cam3d.is_file():
                self._copy_fsynced(source_cam3d, cam3d_temp)
            database_temp.replace(project_root / DATABASE_FILENAME)
            if source_cam3d.is_file():
                target_cam3d.parent.mkdir(exist_ok=True)
                cam3d_temp.replace(target_cam3d)
            else:
                target_cam3d.unlink(missing_ok=True)
            manifest_temp.replace(project_root / MANIFEST_FILENAME)
        finally:
            manifest_temp.unlink(missing_ok=True)
            database_temp.unlink(missing_ok=True)
            cam3d_temp.unlink(missing_ok=True)

    def _validate_replacement_source(self, source_root: Path) -> None:
        if (source_root / AUTOSAVE_METADATA_FILENAME).is_file():
            self._autosave.load_snapshot(source_root)
            return
        self._validate_backup(source_root)

    @staticmethod
    def _copy_fsynced(source: Path, destination: Path) -> None:
        shutil.copy2(source, destination)
        with destination.open("rb+") as stream:
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _file_metadata(
        path: Path, filename: str | None = None
    ) -> AutosaveFileMetadata:
        return AutosaveFileMetadata(
            filename=filename or path.name,
            size_bytes=path.stat().st_size,
            sha256=sha256_file(path),
        )

    @staticmethod
    def _verify_file(root: Path, metadata: AutosaveFileMetadata) -> None:
        path = root / metadata.filename
        if not path.is_file() or path.stat().st_size != metadata.size_bytes:
            raise RecoveryTransactionError(f"Recovery backup size mismatch: {path.name}")
        if sha256_file(path) != metadata.sha256:
            raise RecoveryTransactionError(f"Recovery backup checksum mismatch: {path.name}")

    def _replaced_candidates(self, target: Path) -> tuple[Path, ...]:
        if not target.parent.is_dir():
            return ()
        prefix = f".{target.name}.".casefold()
        candidates = []
        for path in target.parent.iterdir():
            if not path.is_dir() or self._is_link_or_junction(path):
                continue
            name = path.name.casefold()
            if name.startswith(prefix) and name.endswith(".replaced"):
                match = _REPLACED_PATTERN.fullmatch(path.name)
                if match is not None and match.group("target").casefold() == target.name.casefold():
                    candidates.append(path)
        return tuple(candidates)

    @staticmethod
    def _is_link_or_junction(path: Path) -> bool:
        return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
