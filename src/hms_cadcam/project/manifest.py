"""UTF-8 JSON persistence for HMS project manifests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from hms_cadcam.project.constants import (
    CAM_WORKSPACE_MANIFEST_FILENAME,
    MANIFEST_FILENAME,
)
from hms_cadcam.project.exceptions import (
    ManifestDecodeError,
    ManifestMissingError,
    ProjectPermissionError,
)
from hms_cadcam.project.models import ProjectManifest


class ProjectManifestStore:
    """Read and atomically write legacy or folder-workspace manifests."""

    @staticmethod
    def filename_for(project_root: Path) -> str:
        """Return the manifest contract already owned by a project root."""
        legacy = project_root / MANIFEST_FILENAME
        workspace = project_root / CAM_WORKSPACE_MANIFEST_FILENAME
        if legacy.is_file():
            return MANIFEST_FILENAME
        if workspace.is_file():
            return CAM_WORKSPACE_MANIFEST_FILENAME
        return MANIFEST_FILENAME

    def load(self, project_root: Path) -> ProjectManifest:
        """Load a typed manifest or raise a controlled manifest failure."""
        manifest_path = project_root / self.filename_for(project_root)
        if not manifest_path.is_file():
            raise ManifestMissingError(f"Missing manifest: {manifest_path}")
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise TypeError("Manifest root must be an object")
            return ProjectManifest.from_dict(data)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ManifestDecodeError(f"Invalid manifest: {manifest_path}") from error
        except PermissionError as error:
            raise ProjectPermissionError(str(error)) from error

    def save(
        self,
        project_root: Path,
        manifest: ProjectManifest,
        *,
        filename: str | None = None,
    ) -> Path:
        """Write the manifest through a flushed sibling temporary file."""
        manifest_name = filename or self.filename_for(project_root)
        if manifest_name not in {
            MANIFEST_FILENAME,
            CAM_WORKSPACE_MANIFEST_FILENAME,
        }:
            raise ValueError("Unsupported project manifest filename")
        manifest_path = project_root / manifest_name
        temporary = manifest_path.with_name(f".{manifest_name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(manifest.to_dict(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(manifest_path)
        except PermissionError as error:
            raise ProjectPermissionError(str(error)) from error
        finally:
            temporary.unlink(missing_ok=True)
        return manifest_path
