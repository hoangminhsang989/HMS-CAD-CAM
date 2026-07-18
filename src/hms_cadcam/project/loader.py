"""Validated opening of existing HMS project directories."""

from __future__ import annotations

from pathlib import Path

from hms_cadcam.project.constants import DATABASE_FILENAME
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import ProjectSession
from hms_cadcam.project.validator import ProjectValidator


class ProjectLoader:
    """Load a project only after manifest, references, and database pass checks."""

    def __init__(
        self,
        manifest_store: ProjectManifestStore,
        validator: ProjectValidator,
        database: ProjectDatabase,
    ) -> None:
        self._manifest_store = manifest_store
        self._validator = validator
        self._database = database

    def load(self, project_root: Path) -> ProjectSession:
        """Open and migrate a supported HMS project."""
        self._validator.validate_project_directory_name(project_root)
        manifest = self._manifest_store.load(project_root)
        self._validator.validate_references(project_root, manifest)
        database_path = project_root / DATABASE_FILENAME
        self._database.open_and_migrate(database_path)
        self._database.validate(database_path)
        return ProjectSession(root_path=project_root, manifest=manifest, is_dirty=False)
