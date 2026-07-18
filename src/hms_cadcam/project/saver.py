"""Atomic save, source import, and Save As operations."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from hms_cadcam.project.constants import DATABASE_FILENAME, SOURCE_DIRECTORY
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.filesystem import (
    copy_source_verified,
    project_target_path,
    publish_directory,
    remove_imported_source,
    staging_directory,
    unique_source_path,
)
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import ProjectSession, SourceFileRecord, utc_now
from hms_cadcam.project.validator import ProjectValidator


class ProjectSaver:
    """Persist current project state without modifying original CAD sources."""

    def __init__(
        self,
        manifest_store: ProjectManifestStore,
        validator: ProjectValidator,
        database: ProjectDatabase,
    ) -> None:
        self._manifest_store = manifest_store
        self._validator = validator
        self._database = database

    def save(self, session: ProjectSession) -> ProjectSession:
        """Validate and atomically save the current manifest."""
        manifest = session.manifest.with_modified_time()
        self._validator.validate_manifest(manifest)
        self._database.validate(session.root_path / DATABASE_FILENAME)
        self._manifest_store.save(session.root_path, manifest)
        session.manifest = manifest
        session.is_dirty = False
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
        session.is_dirty = False
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
            records: list[SourceFileRecord] = []
            for old_record in session.manifest.source_files:
                source = session.root_path / Path(old_record.stored_path)
                destination = source_dir / source.name
                size, digest = copy_source_verified(source, destination)
                records.append(
                    SourceFileRecord(
                        source_id=uuid4(),
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
            self._manifest_store.save(staging, manifest)
            self._validator.validate_references(staging, manifest)
            self._database.validate(staging / DATABASE_FILENAME)
            publish_directory(staging, target, overwrite=overwrite)
        return ProjectSession(root_path=target, manifest=manifest, is_dirty=False)
