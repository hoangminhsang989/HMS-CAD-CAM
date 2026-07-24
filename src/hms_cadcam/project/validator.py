"""Validation rules for Windows HMS project paths and manifests."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from hms_cadcam.project.constants import (
    DATABASE_FILENAME,
    CAM_WORKSPACE_MANIFEST_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
    PROJECT_SUFFIX,
    SOURCE_DIRECTORY,
)
from hms_cadcam.project.exceptions import (
    InvalidProjectNameError,
    UnsupportedFormatVersionError,
    UnsupportedProjectFormatError,
)
from hms_cadcam.project.filesystem import normalized_project_stem
from hms_cadcam.project.models import ProjectManifest
from hms_cadcam.project.path_policy import normalize_cam_project_name

_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ProjectValidator:
    """Validate project identity, names, manifest values, and references."""

    def validate_project_name(self, name: str) -> str:
        """Return a suffix-free valid Windows project name."""
        if name != name.strip():
            raise InvalidProjectNameError("Project name cannot start or end with whitespace")
        stem = normalized_project_stem(name)
        if not stem or stem in {".", ".."}:
            raise InvalidProjectNameError("Project name is empty")
        if stem.endswith((" ", ".")) or _INVALID_WINDOWS_CHARS.search(stem):
            raise InvalidProjectNameError(f"Invalid Windows project name: {name}")
        device_name = stem.split(".", 1)[0].upper()
        if device_name in _RESERVED_WINDOWS_NAMES:
            raise InvalidProjectNameError(f"Reserved Windows name: {name}")
        return stem

    def validate_project_directory_name(self, project_root: Path) -> None:
        """Accept legacy .HMS directories and safe CAM workspace directories."""
        if project_root.suffix.casefold() == PROJECT_SUFFIX.casefold():
            self.validate_project_name(project_root.name)
            return
        if not (project_root / CAM_WORKSPACE_MANIFEST_FILENAME).is_file():
            raise InvalidProjectNameError(
                f"Project directory is neither legacy .HMS nor CAM workspace: {project_root}"
            )
        if normalize_cam_project_name(project_root.name) != project_root.name:
            raise InvalidProjectNameError(
                f"CAM workspace directory name is unsafe: {project_root.name}"
            )

    def validate_manifest(self, manifest: ProjectManifest) -> None:
        """Validate semantic rules for manifest format version 1."""
        if manifest.format != PROJECT_FORMAT:
            raise UnsupportedProjectFormatError(manifest.format)
        if manifest.format_version != PROJECT_FORMAT_VERSION:
            raise UnsupportedFormatVersionError(str(manifest.format_version))
        if (
            not isinstance(manifest.project_name, str)
            or not manifest.project_name.strip()
            or manifest.project_name != manifest.project_name.strip()
        ):
            raise InvalidProjectNameError("Project display name is empty or padded")
        if manifest.modified_at < manifest.created_at:
            raise ValueError("modified_at cannot precede created_at")
        if manifest.active_document is not None:
            raise ValueError("active_document must remain null in project format version 1")
        if manifest.database != DATABASE_FILENAME:
            raise ValueError("database must be project.db")
        for source in manifest.source_files:
            relative = PurePosixPath(source.stored_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Source path must be relative and cannot escape the project")
            if not relative.parts or relative.parts[0] != SOURCE_DIRECTORY or len(relative.parts) != 2:
                raise ValueError("Source path must be a direct child of source/")
            if source.size_bytes < 0 or not re.fullmatch(r"[0-9a-f]{64}", source.sha256):
                raise ValueError("Invalid source size or SHA-256")
            if source.working_geometry_path is not None:
                working = PurePosixPath(source.working_geometry_path)
                if (
                    working.is_absolute()
                    or ".." in working.parts
                    or not working.parts
                    or working.parts[0] != "working-geometry"
                ):
                    raise ValueError("Working geometry path must remain in project root")

    def validate_references(self, project_root: Path, manifest: ProjectManifest) -> None:
        """Require all manifest source references to exist within source/."""
        self.validate_manifest(manifest)
        for source in manifest.source_files:
            path = project_root / Path(source.stored_path)
            if not path.is_file():
                raise ValueError(f"Missing project source copy: {path}")
            if source.working_geometry_path is not None:
                working_path = project_root / Path(source.working_geometry_path)
                if not working_path.is_file():
                    raise ValueError(
                        f"Missing unpacked working geometry: {working_path}"
                    )
