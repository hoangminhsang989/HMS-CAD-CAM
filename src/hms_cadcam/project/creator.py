"""Transactional creation of new HMS project directories."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from hms_cadcam.project.constants import (
    APPLICATION_NAME,
    APPLICATION_VERSION,
    DATABASE_FILENAME,
    CAM_WORKSPACE_MANIFEST_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
    SOURCE_DIRECTORY,
    WORKING_GEOMETRY_DIRECTORY,
)
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.filesystem import (
    copy_source_verified,
    create_cam_workspace_directories,
    create_runtime_directories,
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
from hms_cadcam.cam.cam3d.persistence import Cam3DProjectConfig
from hms_cadcam.project.path_policy import (
    normalize_internal_source_filename,
    validated_cam_target,
)
from hms_cadcam.project.workspace import SourceProvenance
import json


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
            create_runtime_directories(staging)
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
            self._database.bind_project_identity(
                staging / DATABASE_FILENAME,
                manifest.project_id,
            )
            self._manifest_store.save(staging, manifest)
            self._validator.validate_references(staging, manifest)
            self._database.validate(staging / DATABASE_FILENAME)
            publish_directory(staging, target, overwrite=overwrite)
        cam3d_config = Cam3DProjectConfig(manifest.project_id)
        return ProjectSession(
            root_path=target,
            manifest=manifest,
            is_dirty=False,
            cam3d_config=cam3d_config,
            persisted_cam3d_config=cam3d_config,
        )

    def create_cam_workspace(
        self,
        parent_dir: Path,
        project_name: str,
        units: UnitSystem = UnitSystem.MILLIMETER,
        source_path: Path | None = None,
        source_provenance: SourceProvenance | None = None,
    ) -> ProjectSession:
        """Create a non-overwriting CAM folder workspace through staging."""
        target, physical_name = validated_cam_target(parent_dir, project_name)
        now = utc_now()
        with staging_directory(parent_dir, physical_name) as staging:
            create_cam_workspace_directories(staging)
            records: tuple[SourceFileRecord, ...] = ()
            if source_path is not None:
                original_name = (
                    source_path.name
                    if source_provenance is None
                    else source_provenance.original_filename
                )
                internal_name = normalize_internal_source_filename(original_name)
                source_destination = staging / SOURCE_DIRECTORY / internal_name
                size, digest = copy_source_verified(source_path, source_destination)
                working_destination = (
                    staging / WORKING_GEOMETRY_DIRECTORY / internal_name
                )
                working_size, working_digest = copy_source_verified(
                    source_path, working_destination
                )
                if (size, digest) != (working_size, working_digest):
                    raise ValueError("Working geometry fingerprint mismatch")
                geometry_type = (
                    source_path.suffix.lower().lstrip(".") or "unknown"
                    if source_provenance is None
                    else source_provenance.geometry_type
                )
                records = (
                    SourceFileRecord(
                        source_id=uuid4(),
                        original_name=original_name,
                        stored_path=f"{SOURCE_DIRECTORY}/{internal_name}",
                        size_bytes=size,
                        sha256=digest,
                        imported_at=now,
                        original_path=(
                            str(source_path)
                            if source_provenance is None
                            or source_provenance.original_path is None
                            else str(source_provenance.original_path)
                        ),
                        internal_filename=internal_name,
                        importer=(
                            geometry_type
                            if source_provenance is None
                            else source_provenance.importer
                        ),
                        units=(
                            units.value
                            if source_provenance is None
                            else source_provenance.units
                        ),
                        geometry_type=geometry_type,
                        read_only=True,
                        working_geometry_path=(
                            f"{WORKING_GEOMETRY_DIRECTORY}/{internal_name}"
                        ),
                    ),
                )
                info_path = (
                    staging
                    / WORKING_GEOMETRY_DIRECTORY
                    / "geometry-info.json"
                )
                info_path.write_text(
                    json.dumps(
                        {
                            "format": "HMS_WORKING_GEOMETRY",
                            "format_version": 1,
                            "representation": "unpacked-source-compatible",
                            "source_fingerprint": digest,
                            "working_fingerprint": working_digest,
                            "stale": False,
                            "exact_geometry_required_for_cam": True,
                            "mesh_display_cache_is_exact_geometry": False,
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            manifest = ProjectManifest(
                format=PROJECT_FORMAT,
                format_version=PROJECT_FORMAT_VERSION,
                application=APPLICATION_NAME,
                application_version=APPLICATION_VERSION,
                project_id=uuid4(),
                project_name=project_name.strip(),
                created_at=now,
                modified_at=now,
                units=units,
                source_files=records,
                active_document=None,
                database=DATABASE_FILENAME,
            )
            self._validator.validate_manifest(manifest)
            self._database.initialize(staging / DATABASE_FILENAME)
            self._database.bind_project_identity(
                staging / DATABASE_FILENAME,
                manifest.project_id,
            )
            self._manifest_store.save(
                staging,
                manifest,
                filename=CAM_WORKSPACE_MANIFEST_FILENAME,
            )
            self._validator.validate_references(staging, manifest)
            self._database.validate(staging / DATABASE_FILENAME)
            publish_directory(staging, target, overwrite=False)
        cam3d_config = Cam3DProjectConfig(manifest.project_id)
        return ProjectSession(
            root_path=target,
            manifest=manifest,
            is_dirty=False,
            cam3d_config=cam3d_config,
            persisted_cam3d_config=cam3d_config,
            replaced_directory_name="replaced",
        )
