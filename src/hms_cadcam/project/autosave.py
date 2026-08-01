"""Atomic, checksummed autosave snapshots for dirty HMS project sessions."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.project.constants import (
    APPLICATION_VERSION,
    AUTOSAVE_DIRECTORY,
    AUTOSAVE_FORMAT,
    AUTOSAVE_FORMAT_VERSION,
    AUTOSAVE_LATEST_FILENAME,
    AUTOSAVE_LATEST_FORMAT,
    AUTOSAVE_LATEST_VERSION,
    AUTOSAVE_METADATA_FILENAME,
    CACHE_DIRECTORY,
    CAM_WORKSPACE_MANIFEST_FILENAME,
    DATABASE_FILENAME,
    MANIFEST_FILENAME,
    OWNED_DIRECTORY_METADATA_FILENAME,
    SIMULATION_CACHE_SUBDIRECTORY,
    POST_DIRECTORY,
    NC_DIRECTORY,
)
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.cad_state_store import CadViewStateStore
from hms_cadcam.project.exceptions import (
    AutosaveBusyError,
    AutosaveSnapshotError,
    ProjectError,
)
from hms_cadcam.project.filesystem import publish_directory, sha256_file, staging_directory
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import (
    ProjectSession,
    datetime_from_json,
    datetime_to_json,
    utc_now,
)
from hms_cadcam.project.owned_directories import cleanup_stale_owned_directories
from hms_cadcam.project.validator import ProjectValidator
from hms_cadcam.cam.persistence import CamSqliteRepository
from hms_cadcam.cam.post.export_store import NCArtifactStore, NCArtifactStoreError
from hms_cadcam.cam.cam3d.persistence import (
    CAM3D_CONFIG_DIRECTORY,
    CAM3D_CONFIG_FILENAME,
    Cam3DProjectConfig,
    Cam3DProjectStore,
    Cam3DPersistenceError,
)
from hms_cadcam.cam.lathe.persistence import LatheProjectPersistenceService

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SNAPSHOT_PREFIX = "snapshot-"
_STAGING_MINIMUM_AGE = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class AutosaveFileMetadata:
    """Checksum and size of one required snapshot file."""

    filename: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        """Convert file metadata to JSON-compatible values."""
        return {
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutosaveFileMetadata":
        """Strictly decode one checksummed-file record."""
        if not isinstance(data, dict):
            raise TypeError("Autosave file metadata must be an object")
        if not isinstance(data.get("filename"), str) or not data["filename"]:
            raise TypeError("Autosave filename must be a non-empty string")
        if type(data.get("size_bytes")) is not int or data["size_bytes"] < 0:
            raise TypeError("Autosave file size must be a non-negative integer")
        if not isinstance(data.get("sha256"), str) or not _SHA256_PATTERN.fullmatch(
            data["sha256"]
        ):
            raise TypeError("Autosave file SHA-256 is invalid")
        return cls(
            filename=data["filename"],
            size_bytes=data["size_bytes"],
            sha256=data["sha256"],
        )


@dataclass(frozen=True, slots=True)
class AutosaveMetadata:
    """Versioned identity and checksums for an immutable snapshot."""

    format: str
    format_version: int
    snapshot_id: UUID
    project_id: UUID
    session_id: UUID
    created_at: datetime
    application_version: str
    manifest: AutosaveFileMetadata
    database: AutosaveFileMetadata
    cam3d: AutosaveFileMetadata | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert snapshot metadata to JSON-compatible values."""
        return {
            "format": self.format,
            "format_version": self.format_version,
            "snapshot_id": str(self.snapshot_id),
            "project_id": str(self.project_id),
            "session_id": str(self.session_id),
            "created_at": datetime_to_json(self.created_at),
            "application_version": self.application_version,
            "manifest": self.manifest.to_dict(),
            "database": self.database.to_dict(),
            "cam3d": self.cam3d.to_dict() if self.cam3d is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutosaveMetadata":
        """Strictly decode versioned autosave metadata."""
        if not isinstance(data, dict):
            raise TypeError("Autosave metadata must be an object")
        if data.get("format") != AUTOSAVE_FORMAT:
            raise ValueError("Unsupported autosave format")
        if type(data.get("format_version")) is not int:
            raise TypeError("Autosave format_version must be an integer")
        if data["format_version"] != AUTOSAVE_FORMAT_VERSION:
            raise ValueError("Unsupported autosave format version")
        for field in (
            "snapshot_id",
            "project_id",
            "session_id",
            "created_at",
            "application_version",
        ):
            if not isinstance(data.get(field), str) or not data[field]:
                raise TypeError(f"Autosave {field} must be a non-empty string")
        return cls(
            format=data["format"],
            format_version=data["format_version"],
            snapshot_id=UUID(data["snapshot_id"]),
            project_id=UUID(data["project_id"]),
            session_id=UUID(data["session_id"]),
            created_at=datetime_from_json(data["created_at"]),
            application_version=data["application_version"],
            manifest=AutosaveFileMetadata.from_dict(data["manifest"]),
            database=AutosaveFileMetadata.from_dict(data["database"]),
            cam3d=(
                AutosaveFileMetadata.from_dict(data["cam3d"])
                if data.get("cam3d") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class AutosaveLatestPointer:
    """Small atomic pointer to the most recently published snapshot."""

    format: str
    format_version: int
    snapshot_id: UUID

    def to_dict(self) -> dict[str, Any]:
        """Convert the pointer to JSON-compatible values."""
        return {
            "format": self.format,
            "format_version": self.format_version,
            "snapshot_id": str(self.snapshot_id),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutosaveLatestPointer":
        """Strictly decode the latest-snapshot pointer."""
        if not isinstance(data, dict):
            raise TypeError("Autosave latest pointer must be an object")
        if data.get("format") != AUTOSAVE_LATEST_FORMAT:
            raise ValueError("Unsupported autosave latest-pointer format")
        if type(data.get("format_version")) is not int:
            raise TypeError("Autosave latest-pointer version must be an integer")
        if data["format_version"] != AUTOSAVE_LATEST_VERSION:
            raise ValueError("Unsupported autosave latest-pointer version")
        if not isinstance(data.get("snapshot_id"), str):
            raise TypeError("Autosave latest-pointer snapshot_id must be a string")
        return cls(
            format=data["format"],
            format_version=data["format_version"],
            snapshot_id=UUID(data["snapshot_id"]),
        )


@dataclass(frozen=True, slots=True)
class AutosaveSnapshot:
    """Validated snapshot path and decoded metadata."""

    path: Path
    metadata: AutosaveMetadata


class AutosaveManager:
    """Create one autosave at a time and publish latest only after validation."""

    def __init__(
        self,
        manifest_store: ProjectManifestStore,
        validator: ProjectValidator,
        database: ProjectDatabase,
        cad_state_store: CadViewStateStore | None = None,
        cam_repository: CamSqliteRepository | None = None,
        nc_artifact_store: NCArtifactStore | None = None,
        cam3d_store: Cam3DProjectStore | None = None,
        lathe_persistence: LatheProjectPersistenceService | None = None,
    ) -> None:
        self._manifest_store = manifest_store
        self._validator = validator
        self._database = database
        self._cad_state_store = cad_state_store or CadViewStateStore()
        self._cam_repository = cam_repository or CamSqliteRepository()
        self._nc_artifact_store = nc_artifact_store or NCArtifactStore()
        self._cam3d_store = cam3d_store or Cam3DProjectStore()
        self._lathe_persistence = (
            lathe_persistence or LatheProjectPersistenceService()
        )
        self._operation_lock = threading.Lock()

    def create_snapshot(
        self,
        session: ProjectSession,
        session_id: UUID,
    ) -> AutosaveSnapshot:
        """Create and atomically select a validated immutable snapshot."""
        if session.read_only:
            raise AutosaveSnapshotError("Read-only project cannot be autosaved")
        if not self._operation_lock.acquire(blocking=False):
            raise AutosaveBusyError("Another autosave operation is already running")
        try:
            return self._create_snapshot(session, session_id)
        finally:
            self._operation_lock.release()

    def load_latest(self, project_root: Path) -> AutosaveSnapshot | None:
        """Load and validate the snapshot referenced by the latest pointer."""
        if not (project_root / AUTOSAVE_DIRECTORY).exists():
            return None
        autosave_root = self._autosave_root(project_root)
        pointer_path = autosave_root / AUTOSAVE_LATEST_FILENAME
        if not pointer_path.exists():
            return None
        if pointer_path.is_symlink():
            raise AutosaveSnapshotError("Autosave latest pointer cannot be a symbolic link")
        try:
            data = json.loads(pointer_path.read_text(encoding="utf-8"))
            pointer = AutosaveLatestPointer.from_dict(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise AutosaveSnapshotError("Invalid autosave latest pointer") from error
        snapshot_path = autosave_root / self._snapshot_directory_name(pointer.snapshot_id)
        snapshot = self._load_snapshot(snapshot_path)
        if snapshot.metadata.snapshot_id != pointer.snapshot_id:
            raise AutosaveSnapshotError("Autosave pointer and snapshot identity differ")
        return snapshot

    def load_snapshot(self, snapshot_path: Path) -> AutosaveSnapshot:
        """Validate and load one explicitly selected immutable snapshot."""
        return self._load_snapshot(snapshot_path)

    def _create_snapshot(
        self,
        session: ProjectSession,
        session_id: UUID,
    ) -> AutosaveSnapshot:
        autosave_root = session.root_path / AUTOSAVE_DIRECTORY
        try:
            autosave_root.mkdir(exist_ok=True)
            autosave_root = self._autosave_root(session.root_path)
            cleanup_stale_owned_directories(autosave_root, _STAGING_MINIMUM_AGE)
            snapshot_id = uuid4()
            final_path = autosave_root / self._snapshot_directory_name(snapshot_id)
            with staging_directory(autosave_root, "autosave") as staging:
                manifest_name = self._manifest_store.filename_for(session.root_path)
                self._manifest_store.save(
                    staging,
                    session.manifest,
                    filename=manifest_name,
                )
                self._database.backup(
                    session.root_path / DATABASE_FILENAME,
                    staging / DATABASE_FILENAME,
                )
                with self._cad_state_store.transaction(
                    staging / DATABASE_FILENAME
                ) as connection:
                    self._cad_state_store.replace_all(
                        connection,
                        session.cad_view_states.values(),
                        (record.source_id for record in session.manifest.source_files),
                    )
                    self._cam_repository.replace_all(connection, session.cam_snapshot)
                    if session.lathe_snapshot is not None:
                        self._lathe_persistence.replace_all(
                            connection,
                            session.lathe_snapshot,
                        )
                self._nc_artifact_store.copy_workspace(
                    session.root_path,
                    staging,
                    session.manifest.project_id,
                    session.manifest.project_id,
                )
                cam3d_config = session.cam3d_config or Cam3DProjectConfig(
                    session.manifest.project_id
                )
                cam3d_metadata = None
                if not cam3d_config.is_empty:
                    cam3d_path = self._cam3d_store.save(staging, cam3d_config)
                    cam3d_metadata = self._file_metadata(
                        cam3d_path,
                        f"{CAM3D_CONFIG_DIRECTORY}/{CAM3D_CONFIG_FILENAME}",
                    )
                metadata = AutosaveMetadata(
                    format=AUTOSAVE_FORMAT,
                    format_version=AUTOSAVE_FORMAT_VERSION,
                    snapshot_id=snapshot_id,
                    project_id=session.manifest.project_id,
                    session_id=session_id,
                    created_at=utc_now(),
                    application_version=APPLICATION_VERSION,
                    manifest=self._file_metadata(staging / manifest_name),
                    database=self._file_metadata(staging / DATABASE_FILENAME),
                    cam3d=cam3d_metadata,
                )
                self._write_json_exclusive(
                    staging / AUTOSAVE_METADATA_FILENAME,
                    metadata.to_dict(),
                )
                self._validate_snapshot(staging, allow_owner_metadata=True)
                publish_directory(staging, final_path)
            snapshot = self._load_snapshot(final_path)
            try:
                self._write_latest_pointer(autosave_root, snapshot_id)
            except Exception:
                shutil.rmtree(final_path, ignore_errors=True)
                raise
            return snapshot
        except ProjectError:
            raise
        except Cam3DPersistenceError as error:
            raise AutosaveSnapshotError(str(error)) from error
        except OSError as error:
            raise AutosaveSnapshotError(str(error)) from error

    def _load_snapshot(self, snapshot_path: Path) -> AutosaveSnapshot:
        try:
            metadata = self._validate_snapshot(snapshot_path, allow_owner_metadata=False)
        except AutosaveSnapshotError:
            raise
        except (
            ProjectError,
            Cam3DPersistenceError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise AutosaveSnapshotError(f"Invalid autosave snapshot: {snapshot_path}") from error
        return AutosaveSnapshot(path=snapshot_path, metadata=metadata)

    def _validate_snapshot(
        self,
        snapshot_path: Path,
        *,
        allow_owner_metadata: bool,
    ) -> AutosaveMetadata:
        expected_names = {DATABASE_FILENAME, AUTOSAVE_METADATA_FILENAME}
        if allow_owner_metadata:
            expected_names.add(OWNED_DIRECTORY_METADATA_FILENAME)
        if self._is_link_or_junction(snapshot_path) or not snapshot_path.is_dir():
            raise AutosaveSnapshotError("Autosave snapshot must be a real directory")
        entries = tuple(snapshot_path.iterdir())
        names = {path.name for path in entries}
        optional_names = {
            MANIFEST_FILENAME,
            CAM_WORKSPACE_MANIFEST_FILENAME,
            CACHE_DIRECTORY,
            POST_DIRECTORY,
            NC_DIRECTORY,
            CAM3D_CONFIG_DIRECTORY,
        }
        if not expected_names.issubset(names) or names - expected_names - optional_names:
            raise AutosaveSnapshotError("Autosave snapshot file set is incomplete")
        if (POST_DIRECTORY in names) != (NC_DIRECTORY in names):
            raise AutosaveSnapshotError("Autosave NC artifact layout is incomplete")
        if any(self._is_link_or_junction(path) for path in entries):
            raise AutosaveSnapshotError("Autosave snapshot cannot contain links or junctions")
        cache_root = snapshot_path / CACHE_DIRECTORY
        if cache_root.exists():
            simulation_root = cache_root / SIMULATION_CACHE_SUBDIRECTORY
            if (
                not cache_root.is_dir()
                or not simulation_root.is_dir()
                or self._is_link_or_junction(cache_root)
                or self._is_link_or_junction(simulation_root)
                or {path.name for path in cache_root.iterdir()}
                != {SIMULATION_CACHE_SUBDIRECTORY}
                or any(
                    self._is_link_or_junction(path)
                    for path in simulation_root.rglob("*")
                )
            ):
                raise AutosaveSnapshotError("Autosave simulation cache is invalid")
        data = json.loads(
            (snapshot_path / AUTOSAVE_METADATA_FILENAME).read_text(encoding="utf-8")
        )
        metadata = AutosaveMetadata.from_dict(data)
        if metadata.manifest.filename not in {
            MANIFEST_FILENAME,
            CAM_WORKSPACE_MANIFEST_FILENAME,
        }:
            raise AutosaveSnapshotError("Autosave manifest filename is invalid")
        if metadata.manifest.filename not in names:
            raise AutosaveSnapshotError("Autosave manifest file is missing")
        if metadata.database.filename != DATABASE_FILENAME:
            raise AutosaveSnapshotError("Autosave database filename is invalid")
        has_cam3d = CAM3D_CONFIG_DIRECTORY in names
        if has_cam3d != (metadata.cam3d is not None):
            raise AutosaveSnapshotError("Autosave CAM 3D config layout is incomplete")
        if metadata.cam3d is not None:
            expected_cam3d = f"{CAM3D_CONFIG_DIRECTORY}/{CAM3D_CONFIG_FILENAME}"
            if metadata.cam3d.filename != expected_cam3d:
                raise AutosaveSnapshotError("Autosave CAM 3D config filename is invalid")
            cam3d_root = snapshot_path / CAM3D_CONFIG_DIRECTORY
            if (
                not cam3d_root.is_dir()
                or self._is_link_or_junction(cam3d_root)
                or {item.name for item in cam3d_root.iterdir()}
                != {CAM3D_CONFIG_FILENAME}
            ):
                raise AutosaveSnapshotError("Autosave CAM 3D config directory is invalid")
        self._verify_file(snapshot_path, metadata.manifest)
        self._verify_file(snapshot_path, metadata.database)
        if metadata.cam3d is not None:
            self._verify_file(snapshot_path, metadata.cam3d)
        manifest = self._manifest_store.load(snapshot_path)
        self._validator.validate_manifest(manifest)
        if manifest.project_id != metadata.project_id:
            raise AutosaveSnapshotError("Autosave project identity differs from manifest")
        if metadata.cam3d is not None:
            self._cam3d_store.load(snapshot_path, metadata.project_id)
        self._database.validate(snapshot_path / DATABASE_FILENAME)
        if POST_DIRECTORY in names:
            try:
                self._nc_artifact_store.inspect(snapshot_path, metadata.project_id)
            except NCArtifactStoreError as error:
                raise AutosaveSnapshotError("Autosave NC artifacts are invalid") from error
        return metadata

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
    def _verify_file(snapshot_path: Path, metadata: AutosaveFileMetadata) -> None:
        path = snapshot_path / metadata.filename
        if not path.is_file() or path.stat().st_size != metadata.size_bytes:
            raise AutosaveSnapshotError(f"Autosave file size mismatch: {metadata.filename}")
        if sha256_file(path) != metadata.sha256:
            raise AutosaveSnapshotError(f"Autosave checksum mismatch: {metadata.filename}")

    @staticmethod
    def _snapshot_directory_name(snapshot_id: UUID) -> str:
        return f"{_SNAPSHOT_PREFIX}{snapshot_id}"

    @classmethod
    def _autosave_root(cls, project_root: Path) -> Path:
        autosave_root = project_root / AUTOSAVE_DIRECTORY
        if cls._is_link_or_junction(autosave_root) or not autosave_root.is_dir():
            raise AutosaveSnapshotError("Autosave root must be a real directory")
        return autosave_root

    @staticmethod
    def _is_link_or_junction(path: Path) -> bool:
        return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())

    @staticmethod
    def _write_json_exclusive(path: Path, data: dict[str, Any]) -> None:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _write_latest_pointer(self, autosave_root: Path, snapshot_id: UUID) -> None:
        pointer = AutosaveLatestPointer(
            format=AUTOSAVE_LATEST_FORMAT,
            format_version=AUTOSAVE_LATEST_VERSION,
            snapshot_id=snapshot_id,
        )
        temporary = autosave_root / f".{AUTOSAVE_LATEST_FILENAME}.{uuid4().hex}.tmp"
        try:
            self._write_json_exclusive(temporary, pointer.to_dict())
            temporary.replace(autosave_root / AUTOSAVE_LATEST_FILENAME)
        finally:
            temporary.unlink(missing_ok=True)
