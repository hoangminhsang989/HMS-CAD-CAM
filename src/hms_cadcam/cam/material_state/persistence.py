"""Atomic project-local persistence for material-state artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.units import LengthUnit
from .core import MaterialState, MaterialStatePrecisionPolicy, MaterialStateStatus


class MaterialStateLoadStatus(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"
    INCOMPATIBLE = "INCOMPATIBLE"


@dataclass(frozen=True, slots=True)
class MaterialStateLoad:
    status: MaterialStateLoadStatus
    state: MaterialState | None = None
    message: str | None = None


class MaterialStateStore:
    """Store COMPLETE states below ``.hms/cam/material_state`` atomically."""

    relative_root = Path(".hms") / "cam" / "material_state"

    def _root(self, project_root: Path) -> Path:
        if not isinstance(project_root, Path) or not project_root.is_dir() or project_root.is_symlink():
            raise OSError("Material-state project root is invalid")
        root = project_root / self.relative_root
        root.mkdir(parents=True, exist_ok=True)
        if root.resolve().relative_to(project_root.resolve()) != Path(".hms") / "cam" / "material_state":
            raise OSError("Material-state root escapes project")
        return root

    def write(self, project_root: Path, state: MaterialState) -> Path:
        if state.status is not MaterialStateStatus.COMPLETE:
            raise CamValidationError("Only COMPLETE material states may be published")
        root = self._root(project_root)
        document = state.to_dict()
        document["checksum_sha256"] = ""
        unsigned = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        document["checksum_sha256"] = hashlib.sha256(unsigned).hexdigest()
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        target = root / f"{state.fingerprint.digest}.state.json"
        temporary = root / f".{target.name}.{uuid4().hex}.tmp"
        with temporary.open("xb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
        if target.read_bytes() != payload:
            raise OSError("Material-state readback mismatch")
        return target

    def load(self, project_root: Path, fingerprint: ContentFingerprint) -> MaterialStateLoad:
        try:
            path = self._root(project_root) / f"{fingerprint.digest}.state.json"
            if not path.is_file() or path.is_symlink():
                return MaterialStateLoad(MaterialStateLoadStatus.MISSING, message="state missing")
            payload = path.read_bytes()
            data = json.loads(payload.decode("utf-8"))
            checksum = data.get("checksum_sha256")
            data["checksum_sha256"] = ""
            unsigned = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if checksum != hashlib.sha256(unsigned).hexdigest():
                return MaterialStateLoad(MaterialStateLoadStatus.CORRUPT, message="state checksum mismatch")
            data["checksum_sha256"] = checksum
            if data.get("format") != "HMS_CAM_MATERIAL_STATE" or data.get("format_version") != 1 or data.get("status") != "COMPLETE":
                return MaterialStateLoad(MaterialStateLoadStatus.INCOMPATIBLE, message="state schema/status incompatible")
            precision_data = data["precision"]
            from .core import MaterialStateQuality
            precision = MaterialStatePrecisionPolicy(precision_data["grid_target"], precision_data["tolerance"], precision_data["residual_threshold"], MaterialStateQuality(precision_data.get("quality", "standard")))
            state = MaterialState(1, ContentFingerprint.from_dict(data["fingerprint"]),
                ContentFingerprint.from_dict(data["parent_fingerprint"]) if data["parent_fingerprint"] else None,
                ContentFingerprint.from_dict(data["toolpath_fingerprint"]), ContentFingerprint.from_dict(data["stock_fingerprint"]),
                ContentFingerprint.from_dict(data["setup_fingerprint"]), precision, data["engine_version"], data["width"], data["height"],
                data["cell_size_x"], data["cell_size_y"], tuple(data["top_heights"]), data["initial_volume"], data["remaining_volume"], LengthUnit(data["unit"]))
            if state.fingerprint != fingerprint:
                return MaterialStateLoad(MaterialStateLoadStatus.INCOMPATIBLE, message="state fingerprint mismatch")
            return MaterialStateLoad(MaterialStateLoadStatus.VALID, state=state)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, CamValidationError) as error:
            return MaterialStateLoad(MaterialStateLoadStatus.CORRUPT, message=str(error))

    def discover(self, project_root: Path) -> tuple[MaterialStateLoad, ...]:
        """Discover project-local states deterministically without trusting filenames."""
        try:
            root = self._root(project_root)
        except OSError as error:
            return (MaterialStateLoad(MaterialStateLoadStatus.CORRUPT, message=str(error)),)
        results: list[MaterialStateLoad] = []
        for path in sorted(root.glob("*.state.json"), key=lambda item: item.name):
            digest = path.name.removesuffix(".state.json")
            try:
                fingerprint = ContentFingerprint("sha256", 1, digest)
            except CamValidationError as error:
                results.append(MaterialStateLoad(MaterialStateLoadStatus.CORRUPT, message=str(error)))
                continue
            results.append(self.load(project_root, fingerprint))
        return tuple(results)
