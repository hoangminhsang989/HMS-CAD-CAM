"""Application-facing orchestration for HMS project workflows."""

from __future__ import annotations

import logging
from pathlib import Path

from hms_cadcam.project.creator import ProjectCreator
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import ProjectError, UnsavedChangesError
from hms_cadcam.project.loader import ProjectLoader
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.filesystem import project_target_path
from hms_cadcam.project.models import ProjectSession, UnitSystem
from hms_cadcam.project.recent_projects import RecentProjectEntry, RecentProjectsService
from hms_cadcam.project.saver import ProjectSaver
from hms_cadcam.project.validator import ProjectValidator

logger = logging.getLogger(__name__)


class ProjectService:
    """The only project API consumed by the user interface."""

    def __init__(
        self,
        creator: ProjectCreator,
        loader: ProjectLoader,
        saver: ProjectSaver,
        validator: ProjectValidator,
        database: ProjectDatabase,
        recent_projects: RecentProjectsService,
    ) -> None:
        self._creator = creator
        self._loader = loader
        self._saver = saver
        self._validator = validator
        self._database = database
        self._recent_projects = recent_projects
        self._current_project: ProjectSession | None = None

    @classmethod
    def create_default(cls, config_dir: Path) -> "ProjectService":
        """Build the default project service graph for the application."""
        manifest_store = ProjectManifestStore()
        validator = ProjectValidator()
        database = ProjectDatabase()
        return cls(
            creator=ProjectCreator(manifest_store, validator, database),
            loader=ProjectLoader(manifest_store, validator, database),
            saver=ProjectSaver(manifest_store, validator, database),
            validator=validator,
            database=database,
            recent_projects=RecentProjectsService(config_dir),
        )

    @property
    def current_project(self) -> ProjectSession | None:
        """Return the current session without exposing persistence adapters."""
        return self._current_project

    @property
    def has_project(self) -> bool:
        """Return whether a project is currently open."""
        return self._current_project is not None

    @property
    def is_dirty(self) -> bool:
        """Return whether the current manifest has unsaved changes."""
        return bool(self._current_project and self._current_project.is_dirty)

    def new_project(
        self,
        parent_dir: Path,
        project_name: str,
        units: UnitSystem = UnitSystem.MILLIMETER,
        overwrite: bool = False,
    ) -> ProjectSession:
        """Create and select a new empty project."""
        session = self._creator.create(parent_dir, project_name, units, overwrite=overwrite)
        return self._activate(session)

    def create_project_from_source(
        self,
        parent_dir: Path,
        project_name: str,
        source_path: Path,
        units: UnitSystem = UnitSystem.MILLIMETER,
        overwrite: bool = False,
    ) -> ProjectSession:
        """Create and select a project containing one verified source copy."""
        session = self._creator.create(
            parent_dir,
            project_name,
            units,
            source_path=source_path,
            overwrite=overwrite,
        )
        return self._activate(session)

    def import_source(self, source_path: Path) -> ProjectSession:
        """Import a source into the current project."""
        session = self._require_current()
        self._saver.import_source(session, source_path)
        logger.info("Đã sao chép file nguồn %s vào %s", source_path, session.root_path)
        return session

    def open_project(self, project_root: Path) -> ProjectSession:
        """Open a valid project without replacing current state on failure."""
        session = self._loader.load(project_root)
        return self._activate(session)

    def save(self) -> ProjectSession:
        """Persist the current project."""
        return self._saver.save(self._require_current())

    def save_as(
        self,
        parent_dir: Path,
        project_name: str,
        overwrite: bool = False,
    ) -> ProjectSession:
        """Create and select an independent copy of the current project."""
        session = self._saver.save_as(
            self._require_current(),
            parent_dir,
            project_name,
            overwrite=overwrite,
        )
        return self._activate(session)

    def close_project(self, discard_changes: bool = False) -> None:
        """Close the current session, protecting dirty state by default."""
        if self._current_project is None:
            return
        if self._current_project.is_dirty and not discard_changes:
            raise UnsavedChangesError("Current project contains unsaved changes")
        logger.info("Đã đóng dự án %s", self._current_project.root_path)
        self._current_project = None

    def recent_projects(self) -> tuple[RecentProjectEntry, ...]:
        """Return recently opened project paths."""
        return self._recent_projects.list()

    def remove_recent_project(self, project_path: Path) -> None:
        """Remove one missing or invalid recent-project entry."""
        self._recent_projects.remove(project_path)

    def target_path(self, parent_dir: Path, project_name: str) -> Path:
        """Return the validated normalized destination used for confirmation UI."""
        stem = self._validator.validate_project_name(project_name)
        return project_target_path(parent_dir, stem)

    def project_exists(self, parent_dir: Path, project_name: str) -> bool:
        """Check a validated normalized destination without exposing filesystem to UI."""
        return self.target_path(parent_dir, project_name).exists()

    def _activate(self, session: ProjectSession) -> ProjectSession:
        self._validator.validate_manifest(session.manifest)
        self._database.validate(session.root_path / session.manifest.database)
        self._current_project = session
        try:
            self._recent_projects.add(session.root_path)
        except OSError:
            logger.warning("Không thể cập nhật danh sách dự án gần đây", exc_info=True)
        logger.info("Dự án hiện hành: %s", session.root_path)
        return session

    def _require_current(self) -> ProjectSession:
        if self._current_project is None:
            raise ProjectError("No HMS project is currently open")
        return self._current_project
