"""Versioned JSON config and project-isolated derived mesh cache for CAM 3D."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from hms_cadcam.cam.cam3d.mesh import Cam3DCalculationMesh
from hms_cadcam.cam.cam3d.models import MachiningZone3D, rebind_zone_project
from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError

CAM3D_CONFIG_DIRECTORY = "cam"
CAM3D_CONFIG_FILENAME = "cam3d_foundation.hms.json"
CAM3D_CACHE_SUBDIRECTORY = "cam3d"
_CONFIG_FORMAT = "HMS_CAM3D_PROJECT_CONFIG"
_CONFIG_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")


class Cam3DPersistenceError(RuntimeError):
    """Safe I/O or payload failure at the CAM 3D project boundary."""


@dataclass(frozen=True, slots=True)
class Cam3DProjectConfig:
    """Editable CAM 3D zone configuration; calculation meshes are excluded."""

    project_id: UUID
    zones: tuple[MachiningZone3D, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("CAM 3D config project ID is invalid")
        if not isinstance(self.zones, tuple) or any(
            not isinstance(item, MachiningZone3D) for item in self.zones
        ):
            raise CamValidationError("CAM 3D config zones must be immutable")
        if any(item.project_id != self.project_id for item in self.zones):
            raise CamValidationError("CAM 3D config zone belongs to another project")
        zone_ids = tuple(item.zone_id for item in self.zones)
        if len(zone_ids) != len(set(zone_ids)):
            raise CamValidationError("CAM 3D config zone IDs must be unique")
        object.__setattr__(
            self,
            "zones",
            tuple(sorted(self.zones, key=lambda item: str(item.zone_id))),
        )

    @property
    def is_empty(self) -> bool:
        return not self.zones

    def to_dict(self) -> dict[str, object]:
        return {
            "format": _CONFIG_FORMAT,
            "format_version": _CONFIG_VERSION,
            "project_id": str(self.project_id),
            "zones": [item.to_dict() for item in self.zones],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Cam3DProjectConfig":
        fields = {"format", "format_version", "project_id", "zones"}
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("CAM 3D config payload is malformed")
        if data["format"] != _CONFIG_FORMAT:
            raise UnsupportedCamSchemaError("Unsupported CAM 3D config format")
        if type(data["format_version"]) is not int or data["format_version"] != _CONFIG_VERSION:
            raise UnsupportedCamSchemaError("Unsupported CAM 3D config version")
        zones = data["zones"]
        if not isinstance(zones, list):
            raise CamValidationError("CAM 3D config zones must be a list")
        try:
            return cls(
                UUID(data["project_id"]),  # type: ignore[arg-type]
                tuple(MachiningZone3D.from_dict(item) for item in zones),  # type: ignore[arg-type]
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise CamValidationError("CAM 3D config payload is invalid") from error

    def rebind_project(self, project_id: UUID) -> "Cam3DProjectConfig":
        """Return an independent Save-As config with no old project identity."""
        return Cam3DProjectConfig(
            project_id,
            tuple(rebind_zone_project(item, project_id) for item in self.zones),
        )


class Cam3DProjectStore:
    """Atomic project-relative config store that never writes source CAD."""

    def load(self, project_root: Path, project_id: UUID) -> Cam3DProjectConfig:
        path = self.path_for(project_root)
        if not path.exists():
            return Cam3DProjectConfig(project_id)
        self._require_regular_project_file(project_root, path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            config = Cam3DProjectConfig.from_dict(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, CamValidationError) as error:
            raise Cam3DPersistenceError("CAM 3D project config is invalid") from error
        if config.project_id != project_id:
            raise Cam3DPersistenceError("CAM 3D project config identity mismatch")
        return config

    def save(self, project_root: Path, config: Cam3DProjectConfig) -> Path:
        if not isinstance(config, Cam3DProjectConfig):
            raise CamValidationError("CAM 3D project config is invalid")
        root = self._require_project_root(project_root)
        directory = root / CAM3D_CONFIG_DIRECTORY
        try:
            directory.mkdir(exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise Cam3DPersistenceError("CAM 3D config directory must be real")
            path = directory / CAM3D_CONFIG_FILENAME
            _atomic_json(path, config.to_dict())
            return path
        except Cam3DPersistenceError:
            raise
        except OSError as error:
            raise Cam3DPersistenceError("Could not save CAM 3D project config") from error

    def copy_for_save_as(
        self,
        source_root: Path,
        destination_root: Path,
        source_project_id: UUID,
        destination_project_id: UUID,
    ) -> Cam3DProjectConfig:
        """Copy editable config only; derived mesh cache is deliberately omitted."""
        config = self.load(source_root, source_project_id)
        rebased = config.rebind_project(destination_project_id)
        if not rebased.is_empty:
            self.save(destination_root, rebased)
        return rebased

    def copy_for_workspace(
        self,
        source_root: Path,
        destination_root: Path,
        project_id: UUID,
    ) -> Cam3DProjectConfig:
        """Copy editable config into an autosave/recovery workspace."""
        config = self.load(source_root, project_id)
        if not config.is_empty:
            self.save(destination_root, config)
        return config

    @staticmethod
    def path_for(project_root: Path) -> Path:
        return project_root / CAM3D_CONFIG_DIRECTORY / CAM3D_CONFIG_FILENAME

    @staticmethod
    def _require_project_root(project_root: Path) -> Path:
        if not isinstance(project_root, Path):
            raise Cam3DPersistenceError("CAM 3D project root must be Path")
        root = project_root.resolve()
        if not root.is_dir() or project_root.is_symlink():
            raise Cam3DPersistenceError("CAM 3D project root must be a real directory")
        return root

    @classmethod
    def _require_regular_project_file(cls, project_root: Path, path: Path) -> None:
        root = cls._require_project_root(project_root)
        resolved = path.resolve()
        if path.is_symlink() or not path.is_file() or root not in resolved.parents:
            raise Cam3DPersistenceError("CAM 3D config path escapes the project")


class Cam3DCalculationMeshCache:
    """Derived cache keyed by project and canonical mesh fingerprint."""

    def publish(
        self,
        project_root: Path,
        project_id: UUID,
        mesh: Cam3DCalculationMesh,
    ) -> Path:
        if not isinstance(mesh, Cam3DCalculationMesh):
            raise CamValidationError("CAM 3D calculation mesh is invalid")
        root = Cam3DProjectStore._require_project_root(project_root)
        cache_root = root / "cache" / CAM3D_CACHE_SUBDIRECTORY / project_id.hex
        try:
            cache_root.mkdir(parents=True, exist_ok=True)
            if any(path.is_symlink() for path in (root / "cache", root / "cache" / CAM3D_CACHE_SUBDIRECTORY, cache_root)):
                raise Cam3DPersistenceError("CAM 3D cache path must not contain links")
            path = cache_root / f"{mesh.mesh_fingerprint.digest}.mesh.json"
            _atomic_json(path, mesh.to_dict())
            return path
        except Cam3DPersistenceError:
            raise
        except OSError as error:
            raise Cam3DPersistenceError("Could not publish CAM 3D calculation mesh") from error

    def load(
        self,
        project_root: Path,
        project_id: UUID,
        digest: str,
    ) -> Cam3DCalculationMesh:
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise Cam3DPersistenceError("CAM 3D mesh digest is invalid")
        root = Cam3DProjectStore._require_project_root(project_root)
        cache_root = root / "cache" / CAM3D_CACHE_SUBDIRECTORY / project_id.hex
        path = cache_root / f"{digest}.mesh.json"
        resolved = path.resolve()
        if path.is_symlink() or not path.is_file() or cache_root.resolve() not in resolved.parents:
            raise Cam3DPersistenceError("CAM 3D mesh cache entry is missing")
        try:
            mesh = Cam3DCalculationMesh.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, CamValidationError) as error:
            raise Cam3DPersistenceError("CAM 3D mesh cache entry is invalid") from error
        if mesh.mesh_fingerprint.digest != digest:
            raise Cam3DPersistenceError("CAM 3D mesh cache fingerprint mismatch")
        return mesh

    def cleanup_orphans(
        self,
        project_root: Path,
        project_id: UUID,
        retained_digests: tuple[str, ...],
    ) -> tuple[Path, ...]:
        """Remove only recognized derived entries for the selected project."""
        if any(not isinstance(item, str) or not _SHA256.fullmatch(item) for item in retained_digests):
            raise Cam3DPersistenceError("CAM 3D retained mesh digest is invalid")
        root = Cam3DProjectStore._require_project_root(project_root)
        cache_root = root / "cache" / CAM3D_CACHE_SUBDIRECTORY / project_id.hex
        if not cache_root.exists():
            return ()
        if cache_root.is_symlink() or not cache_root.is_dir():
            raise Cam3DPersistenceError("CAM 3D cache root must be a real directory")
        retained = set(retained_digests)
        removed = []
        for candidate in cache_root.iterdir():
            match = re.fullmatch(r"([0-9a-f]{64})\.mesh\.json", candidate.name)
            if (
                match is None
                or match.group(1) in retained
                or candidate.is_symlink()
                or not candidate.is_file()
            ):
                continue
            candidate.unlink()
            removed.append(candidate)
        return tuple(sorted(removed))


def _atomic_json(path: Path, data: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.writing")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
