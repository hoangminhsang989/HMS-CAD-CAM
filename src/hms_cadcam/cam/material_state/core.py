"""UI-independent deterministic 3-axis material-state calculations."""

from __future__ import annotations

import hashlib
import math
from array import array
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from hms_cadcam.cam.domain import BoxStock, ContentFingerprint, Setup, ToolDefinition
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.simulation.model import SimulationSamplingPolicy
from hms_cadcam.cam.simulation.sampling import sample_toolpath, SimulationSamplingError
from hms_cadcam.cam.toolpath.events import MotionClass
from hms_cadcam.cam.toolpath.fingerprint import compute_material_removal_fingerprint
from hms_cadcam.cam.toolpath.model import ToolpathArtifact
from hms_cadcam.cam.domain.tooling import BallEndGeometry, BullNoseGeometry, CylindricalGeometry

MATERIAL_STATE_ENGINE_VERSION = "heightfield-3axis-v1"


def material_state_setup_fingerprint(setup: Setup) -> ContentFingerprint:
    """Fingerprint only Setup authority that can change material removal.

    The operation tree and Setup revision are lifecycle/runtime state.  Including
    them would make publishing a Rest dependency invalidate its own provenance.
    Stock is deliberately bound by its separate fingerprint.
    """
    if not isinstance(setup, Setup):
        raise TypeError("Material-state Setup is invalid")
    return ContentFingerprint.from_payload({
        "format": "HMS_CAM_MATERIAL_STATE_SETUP_AUTHORITY",
        "format_version": 1,
        "setup_id": str(setup.setup_id),
        "kind": setup.kind.value,
        "wcs": setup.wcs.to_dict(),
        "work_offset": setup.work_offset.to_dict(),
    })


class MaterialStateStatus(StrEnum):
    BUILDING = "BUILDING"
    COMPLETE = "COMPLETE"


class MaterialStateQuality(StrEnum):
    """Persisted CAM precision label without importing optional Simulation."""

    FAST = "fast"
    STANDARD = "standard"
    DETAILED = "detailed"


class NoRestMaterial(RuntimeError):
    """Raised only when a valid state contains no meaningful rest material."""


@dataclass(frozen=True, slots=True)
class MaterialStatePrecisionPolicy:
    """Deterministic CAM precision, independent from display quality."""

    grid_target: int = 192
    tolerance: float = 1.0e-4
    residual_threshold: float = 2.0e-4
    quality: MaterialStateQuality = MaterialStateQuality.STANDARD

    def __post_init__(self) -> None:
        if type(self.grid_target) is not int or self.grid_target < 2:
            raise CamValidationError("Material-state grid target is invalid")
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0.0
               for value in (self.tolerance, self.residual_threshold)):
            raise CamValidationError("Material-state precision values are invalid")
        if not isinstance(self.quality, MaterialStateQuality):
            raise CamValidationError("Material-state quality is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {"grid_target": self.grid_target, "tolerance": self.tolerance,
                "residual_threshold": self.residual_threshold, "quality": self.quality.value}


MaterialStateFingerprint = ContentFingerprint


@dataclass(frozen=True, slots=True)
class MaterialState:
    """Immutable software-estimated stock state with explicit provenance."""

    format_version: int
    fingerprint: MaterialStateFingerprint
    parent_fingerprint: MaterialStateFingerprint | None
    toolpath_fingerprint: ContentFingerprint
    stock_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    precision: MaterialStatePrecisionPolicy
    engine_version: str
    width: int
    height: int
    cell_size_x: float
    cell_size_y: float
    top_heights: tuple[float, ...]
    initial_volume: float
    remaining_volume: float
    unit: LengthUnit
    status: MaterialStateStatus = MaterialStateStatus.COMPLETE

    def __post_init__(self) -> None:
        if self.format_version != 1 or self.width < 2 or self.height < 2:
            raise CamValidationError("Material-state schema or dimensions are invalid")
        if len(self.top_heights) != self.width * self.height or any(
            not math.isfinite(value) or value < 0.0 for value in self.top_heights
        ):
            raise CamValidationError("Material-state heights are invalid")
        if self.cell_size_x <= 0.0 or self.cell_size_y <= 0.0:
            raise CamValidationError("Material-state cell size is invalid")
        if not 0.0 <= self.remaining_volume <= self.initial_volume + self.precision.tolerance:
            raise CamValidationError("Material-state volume is invalid")
        if not isinstance(self.status, MaterialStateStatus):
            raise CamValidationError("Material-state status is invalid")

    @property
    def meaningful_remaining_volume(self) -> float:
        return max(0.0, self.remaining_volume - self.precision.residual_threshold * self.cell_size_x * self.cell_size_y)

    @property
    def has_rest_material(self) -> bool:
        return self.meaningful_remaining_volume > self.precision.tolerance

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM_MATERIAL_STATE",
            "format_version": self.format_version,
            "fingerprint": self.fingerprint.to_dict(),
            "parent_fingerprint": self.parent_fingerprint.to_dict() if self.parent_fingerprint else None,
            "toolpath_fingerprint": self.toolpath_fingerprint.to_dict(),
            "stock_fingerprint": self.stock_fingerprint.to_dict(),
            "setup_fingerprint": self.setup_fingerprint.to_dict(),
            "precision": self.precision.to_dict(),
            "engine_version": self.engine_version,
            "width": self.width, "height": self.height,
            "cell_size_x": self.cell_size_x, "cell_size_y": self.cell_size_y,
            "top_heights": list(self.top_heights),
            "initial_volume": self.initial_volume, "remaining_volume": self.remaining_volume,
            "unit": self.unit.value, "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class MaterialRemovalResult:
    state: MaterialState
    removed_volume: float
    no_rest_material: bool


@dataclass(frozen=True, slots=True)
class _CutterProfile:
    radius: float
    corner_radius: float
    ball: bool

    @classmethod
    def from_tool(cls, tool: ToolDefinition) -> "_CutterProfile":
        geometry = tool.cutting_geometry
        if isinstance(geometry, CylindricalGeometry):
            return cls(geometry.diameter.value / 2.0, 0.0, False)
        if isinstance(geometry, BallEndGeometry):
            return cls(geometry.diameter.value / 2.0, geometry.diameter.value / 2.0, True)
        if isinstance(geometry, BullNoseGeometry):
            return cls(geometry.diameter.value / 2.0, geometry.corner_radius.value, False)
        raise CamValidationError(f"Unsupported cutter for material state: {geometry.kind.value}")

    def surface_offset(self, radial_distance: float) -> float:
        if radial_distance > self.radius:
            return math.inf
        if self.ball:
            return self.radius - math.sqrt(max(0.0, self.radius**2 - radial_distance**2))
        if self.corner_radius > 0.0 and radial_distance > self.radius - self.corner_radius:
            local = radial_distance - (self.radius - self.corner_radius)
            return self.corner_radius - math.sqrt(max(0.0, self.corner_radius**2 - local**2))
        return 0.0


def _remove_at(values: array, width: int, height: int, dx: float, dy: float,
               x: float, y: float, tip_z: float, profile: _CutterProfile) -> None:
    min_x, max_x = max(0, math.floor((x - profile.radius) / dx)), min(width - 1, math.floor((x + profile.radius) / dx))
    min_y, max_y = max(0, math.floor((y - profile.radius) / dy)), min(height - 1, math.floor((y + profile.radius) / dy))
    for row in range(min_y, max_y + 1):
        center_y = (row + 0.5) * dy
        for column in range(min_x, max_x + 1):
            radius = math.hypot((column + 0.5) * dx - x, center_y - y)
            offset = profile.surface_offset(radius)
            if not math.isinf(offset):
                values[row * width + column] = min(values[row * width + column], max(0.0, tip_z + offset))


def calculate_material_state(*, stock: BoxStock, artifact: ToolpathArtifact, tool: ToolDefinition,
                             setup_fingerprint: ContentFingerprint,
                             parent: MaterialState | None = None,
                             precision: MaterialStatePrecisionPolicy | None = None,
                             cancellation: Callable[[], bool] | None = None) -> MaterialRemovalResult:
    """Calculate one complete state from an actual semantic toolpath."""
    policy = precision or MaterialStatePrecisionPolicy()
    if not isinstance(stock, BoxStock):
        raise CamValidationError("R260 Tranche-1 supports Box Stock only")
    if artifact.unit is not stock.size_x.unit or tool.unit is not artifact.unit:
        raise CamValidationError("Material-state units differ")
    profile = _CutterProfile.from_tool(tool)
    aspect = stock.size_x.value / stock.size_y.value
    width = max(2, round(policy.grid_target * math.sqrt(aspect)))
    height = max(2, round(policy.grid_target / math.sqrt(aspect)))
    dx, dy = stock.size_x.value / width, stock.size_y.value / height
    initial_volume = stock.size_x.value * stock.size_y.value * stock.size_z.value
    if parent is None:
        values = array("d", [stock.size_z.value]) * (width * height)
        parent_fp = None
    else:
        if (parent.width, parent.height, parent.unit, parent.cell_size_x, parent.cell_size_y) != (width, height, artifact.unit, dx, dy):
            raise CamValidationError("Parent material state grid is incompatible")
        values = array("d", parent.top_heights)
        initial_volume = parent.initial_volume
        parent_fp = parent.fingerprint
    try:
        sampling = sample_toolpath(artifact=artifact, wcs=stock.frame,
            policy=SimulationSamplingPolicy(max_linear_step=max(1.0e-4, min(dx, dy) * 0.75),
                chord_tolerance=max(1.0e-5, min(dx, dy) / 6.0), maximum_samples=1_000_000,
                memory_budget_bytes=512 * 1024 * 1024), cancellation=cancellation)
    except SimulationSamplingError as error:
        raise CamValidationError(str(error)) from error
    cutting = [index for segment in sampling.segments if segment.motion_class is MotionClass.CUTTING for index in segment.sample_indices]
    for index in dict.fromkeys(cutting):
        if cancellation is not None and cancellation():
            raise CamValidationError("Material-state calculation cancelled")
        point = sampling.samples[index].setup_pose.position
        _remove_at(values, width, height, dx, dy, point.x, point.y, point.z, profile)
    remaining = min(initial_volume, max(0.0, sum(values) * dx * dy))
    stock_fp = ContentFingerprint.from_payload(stock.to_dict())
    toolpath_fp = compute_material_removal_fingerprint(artifact)
    fingerprint = ContentFingerprint.from_payload({
        "format": "HMS_CAM_MATERIAL_STATE_FINGERPRINT", "format_version": 1,
        "parent": parent_fp.to_dict() if parent_fp else None,
        "toolpath": toolpath_fp.to_dict(), "tool": tool.to_dict(),
        "stock": stock_fp.to_dict(), "setup": setup_fingerprint.to_dict(),
        "precision": policy.to_dict(), "engine_version": MATERIAL_STATE_ENGINE_VERSION,
    })
    state = MaterialState(1, fingerprint, parent_fp, toolpath_fp, stock_fp, setup_fingerprint,
        policy, MATERIAL_STATE_ENGINE_VERSION, width, height, dx, dy, tuple(values),
        initial_volume, remaining, artifact.unit)
    return MaterialRemovalResult(state, max(0.0, initial_volume - remaining), not state.has_rest_material)
