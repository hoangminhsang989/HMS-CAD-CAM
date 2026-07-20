"""Atomic save, source import, and Save As operations."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from hms_cadcam.project.constants import DATABASE_FILENAME, SOURCE_DIRECTORY
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.cad_state_store import CadViewStateStore
from hms_cadcam.project.filesystem import (
    copy_source_verified,
    create_runtime_directories,
    project_target_path,
    publish_directory,
    remove_imported_source,
    staging_directory,
    unique_source_path,
)
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import ProjectSession, SourceFileRecord, utc_now
from hms_cadcam.project.validator import ProjectValidator
from hms_cadcam.cam.persistence import (
    CamSqliteRepository, ToolpathArtifactStore, ToolpathArtifactStoreError,
)
from hms_cadcam.cam.post.export_store import NCArtifactStore
import logging

logger = logging.getLogger(__name__)


class ProjectSaver:
    """Persist current project state without modifying original CAD sources."""

    def __init__(
        self,
        manifest_store: ProjectManifestStore,
        validator: ProjectValidator,
        database: ProjectDatabase,
        cad_state_store: CadViewStateStore,
        cam_repository: CamSqliteRepository | None = None,
        artifact_store: ToolpathArtifactStore | None = None,
        nc_artifact_store: NCArtifactStore | None = None,
    ) -> None:
        self._manifest_store = manifest_store
        self._validator = validator
        self._database = database
        self._cad_state_store = cad_state_store
        self._cam_repository = cam_repository or CamSqliteRepository()
        self._artifact_store = artifact_store or ToolpathArtifactStore()
        self._nc_artifact_store = nc_artifact_store or NCArtifactStore()

    def save(self, session: ProjectSession) -> ProjectSession:
        """Validate and atomically save the current manifest."""
        self._nc_artifact_store.flush(
            session.root_path, session.manifest.project_id
        )
        manifest = session.manifest.with_modified_time()
        self._validator.validate_manifest(manifest)
        database_path = session.root_path / DATABASE_FILENAME
        self._database.validate(database_path)
        with self._cad_state_store.transaction(database_path) as connection:
            self._cad_state_store.replace_all(
                connection,
                session.cad_view_states.values(),
                (record.source_id for record in manifest.source_files),
            )
            persisted_cam = self._cam_repository.replace_all(connection, session.cam_snapshot)
            self._manifest_store.save(session.root_path, manifest)
        session.manifest = manifest
        session.persisted_cad_view_states = dict(session.cad_view_states)
        session.cam_snapshot = persisted_cam
        session.persisted_cam_snapshot = persisted_cam
        session.is_dirty = False
        try:
            self._artifact_store.cleanup_orphans(session.root_path, persisted_cam.artifacts)
        except (OSError, ToolpathArtifactStoreError):
            logger.warning("Không thể dọn toolpath artifact mồ côi", exc_info=True)
        return session

    def import_source(self, session: ProjectSession, source_path: Path) -> ProjectSession:
        """Copy a source into source/ and atomically append its manifest record."""
        source_dir = session.root_path / SOURCE_DIRECTORY
        destination = unique_source_path(source_dir, source_path.name)
        size, digest = copy_source_verified(source_path, destination)
        record = SourceFileRecord(
            source_id=uuid4(),
            original_name=source_path.name,
            stored_path=f"{SOURCE_DIRECTORY}/{destination.name}",
            size_bytes=size,
            sha256=digest,
            imported_at=utc_now(),
        )
        manifest = replace(
            session.manifest,
            source_files=(*session.manifest.source_files, record),
            modified_at=utc_now(),
        )
        try:
            self._validator.validate_manifest(manifest)
            self._manifest_store.save(session.root_path, manifest)
        except Exception:
            remove_imported_source(session.root_path, record.stored_path)
            raise
        session.manifest = manifest
        session.is_dirty = (
            session.cad_view_states != session.persisted_cad_view_states
            or session.cam_snapshot != session.persisted_cam_snapshot
        )
        return session

    def save_as(
        self,
        session: ProjectSession,
        parent_dir: Path,
        project_name: str,
        overwrite: bool = False,
    ) -> ProjectSession:
        """Create an independent project identity without changing the original."""
        stem = self._validator.validate_project_name(project_name)
        target = project_target_path(parent_dir, stem)
        if target.resolve() == session.root_path.resolve():
            return self.save(session)
        now = utc_now()
        with staging_directory(parent_dir, stem) as staging:
            source_dir = staging / SOURCE_DIRECTORY
            source_dir.mkdir()
            create_runtime_directories(staging)
            records: list[SourceFileRecord] = []
            for old_record in session.manifest.source_files:
                source = session.root_path / Path(old_record.stored_path)
                destination = source_dir / source.name
                size, digest = copy_source_verified(source, destination)
                records.append(
                    SourceFileRecord(
                        source_id=old_record.source_id,
                        original_name=old_record.original_name,
                        stored_path=f"{SOURCE_DIRECTORY}/{destination.name}",
                        size_bytes=size,
                        sha256=digest,
                        imported_at=now,
                    )
                )
            manifest = replace(
                session.manifest,
                project_id=uuid4(),
                project_name=stem,
                created_at=now,
                modified_at=now,
                source_files=tuple(records),
                active_document=None,
            )
            self._validator.validate_manifest(manifest)
            self._database.backup(
                session.root_path / DATABASE_FILENAME,
                staging / DATABASE_FILENAME,
            )
            copied_artifacts = self._artifact_store.copy_referenced(
                session.root_path,
                staging,
                session.cam_snapshot.artifacts,
            )
            staged_cam = replace(session.cam_snapshot, artifacts=copied_artifacts)
            self._nc_artifact_store.copy_workspace(
                session.root_path,
                staging,
                session.manifest.project_id,
                manifest.project_id,
            )
            with self._cad_state_store.transaction(
                staging / DATABASE_FILENAME
            ) as connection:
                self._cad_state_store.replace_all(
                    connection,
                    session.cad_view_states.values(),
                    (record.source_id for record in records),
                )
                persisted_cam = self._cam_repository.replace_all(connection, staged_cam)
            self._manifest_store.save(staging, manifest)
            self._validator.validate_references(staging, manifest)
            self._database.validate(staging / DATABASE_FILENAME)
            publish_directory(staging, target, overwrite=overwrite)
        return ProjectSession(
            root_path=target,
            manifest=manifest,
            is_dirty=False,
            cad_view_states=dict(session.cad_view_states),
            persisted_cad_view_states=dict(session.cad_view_states),
            cam_snapshot=persisted_cam,
            persisted_cam_snapshot=persisted_cam,
        )
