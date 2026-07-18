"""Transactional creation of new HMS project directories."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from hms_cadcam.project.constants import (
    APPLICATION_NAME,
    APPLICATION_VERSION,
    DATABASE_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
    SOURCE_DIRECTORY,
)
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.filesystem import (
    copy_source_verified,
    project_target_path,
    publish_directory,
    staging_directory,
)
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import (
    ProjectManifest,
    ProjectSession,
    SourceFileRecord,
    UnitSystem,
    utc_now,
)
from hms_cadcam.project.validator import ProjectValidator


class ProjectCreator:
    """Build complete projects in staging before making them visible."""

    def __init__(
        self,
        manifest_store: ProjectManifestStore,
        validator: ProjectValidator,
        database: ProjectDatabase,
    ) -> None:
        self._manifest_store = manifest_store
        self._validator = validator
        self._database = database

    def create(
        self,
        parent_dir: Path,
        project_name: str,
        units: UnitSystem = UnitSystem.MILLIMETER,
        source_path: Path | None = None,
        overwrite: bool = False,
    ) -> ProjectSession:
        """Create an empty project or one containing an immutable source copy."""
        stem = self._validator.validate_project_name(project_name)
        target = project_target_path(parent_dir, stem)
        now = utc_now()
        with staging_directory(parent_dir, stem) as staging:
            source_dir = staging / SOURCE_DIRECTORY
            source_dir.mkdir()
            records: tuple[SourceFileRecord, ...] = ()
            if source_path is not None:
                destination = source_dir / source_path.name
                size, digest = copy_source_verified(source_path, destination)
                records = (
                    SourceFileRecord(
                        source_id=uuid4(),
                        original_name=source_path.name,
                        stored_path=f"{SOURCE_DIRECTORY}/{destination.name}",
                        size_bytes=size,
                        sha256=digest,
                        imported_at=now,
                    ),
                )
            manifest = ProjectManifest(
                format=PROJECT_FORMAT,
                format_version=PROJECT_FORMAT_VERSION,
                application=APPLICATION_NAME,
                application_version=APPLICATION_VERSION,
                project_id=uuid4(),
                project_name=stem,
                created_at=now,
                modified_at=now,
                units=units,
                source_files=records,
                active_document=None,
                database=DATABASE_FILENAME,
            )
            self._validator.validate_manifest(manifest)
            self._database.initialize(staging / DATABASE_FILENAME)
            self._manifest_store.save(staging, manifest)
            self._validator.validate_references(staging, manifest)
            self._database.validate(staging / DATABASE_FILENAME)
            publish_directory(staging, target, overwrite=overwrite)
        return ProjectSession(root_path=target, manifest=manifest, is_dirty=False)
