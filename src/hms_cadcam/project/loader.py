"""Validated opening of existing HMS project directories."""

from __future__ import annotations

from pathlib import Path

from hms_cadcam.project.constants import DATABASE_FILENAME
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.cad_state_store import CadViewStateStore
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import ProjectManifest, ProjectSession
from hms_cadcam.project.validator import ProjectValidator
from hms_cadcam.cam.application import reconcile_artifacts
from hms_cadcam.cam.persistence import CamSqliteRepository, ToolpathArtifactStore
from hms_cadcam.cam.cam3d.persistence import Cam3DProjectStore
from hms_cadcam.cam.lathe.persistence import LatheProjectPersistenceService


class ProjectLoader:
    """Load a project only after manifest, references, and database pass checks."""

    def __init__(
        self,
        manifest_store: ProjectManifestStore,
        validator: ProjectValidator,
        database: ProjectDatabase,
        cad_state_store: CadViewStateStore,
        cam_repository: CamSqliteRepository | None = None,
        artifact_store: ToolpathArtifactStore | None = None,
        cam3d_store: Cam3DProjectStore | None = None,
        lathe_persistence: LatheProjectPersistenceService | None = None,
        lathe_enabled: bool = False,
    ) -> None:
        self._manifest_store = manifest_store
        self._validator = validator
        self._database = database
        self._cad_state_store = cad_state_store
        self._cam_repository = cam_repository or CamSqliteRepository()
        self._artifact_store = artifact_store or ToolpathArtifactStore()
        self._cam3d_store = cam3d_store or Cam3DProjectStore()
        self._lathe_persistence = (
            lathe_persistence or LatheProjectPersistenceService()
        )
        if type(lathe_enabled) is not bool:
            raise TypeError("lathe_enabled must be bool")
        self._lathe_enabled = lathe_enabled

    def set_lathe_enabled(self, enabled: bool) -> None:
        """Configure feature-on hydration before the next project load."""

        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        self._lathe_enabled = enabled

    def read_manifest(self, project_root: Path) -> ProjectManifest:
        """Validate project identity without opening or migrating SQLite."""
        manifest = self._manifest_store.load(project_root)
        self._validator.validate_project_directory_name(project_root)
        self._validator.validate_references(project_root, manifest)
        return manifest

    def load(
        self,
        project_root: Path,
        *,
        read_only: bool = False,
    ) -> ProjectSession:
        """Open and migrate a supported HMS project."""
        if type(read_only) is not bool:
            raise TypeError("read_only must be bool")
        manifest = self.read_manifest(project_root)
        database_path = project_root / DATABASE_FILENAME
        self._database.open_and_migrate(database_path, read_only=read_only)
        self._database.validate(database_path)
        states = self._cad_state_store.load(
            database_path,
            (record.source_id for record in manifest.source_files),
        )
        cam_snapshot = reconcile_artifacts(
            project_root,
            self._cam_repository.load(database_path),
            self._artifact_store,
        )
        cam3d_config = self._cam3d_store.load(project_root, manifest.project_id)
        lathe_result = (
            self._lathe_persistence.load_project(
                database_path,
                manifest.project_id,
                read_only=read_only,
            )
            if self._lathe_enabled
            else None
        )
        return ProjectSession(
            root_path=project_root,
            manifest=manifest,
            is_dirty=False,
            cad_view_states=dict(states),
            persisted_cad_view_states=dict(states),
            cam_snapshot=cam_snapshot,
            persisted_cam_snapshot=cam_snapshot,
            cam3d_config=cam3d_config,
            persisted_cam3d_config=cam3d_config,
            lathe_snapshot=(None if lathe_result is None else lathe_result.snapshot),
            persisted_lathe_snapshot=(
                None if lathe_result is None else lathe_result.snapshot
            ),
            lathe_restore_diagnostics=(
                () if lathe_result is None else lathe_result.diagnostics
            ),
            lathe_persistence_loaded=lathe_result is not None,
            read_only=read_only,
            replaced_directory_name=(
                "replaced"
                if (project_root / "replaced").is_dir()
                else ".replaced"
                if (project_root / ".replaced").is_dir()
                else None
            ),
        )
