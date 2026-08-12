"""Deterministic CPU height-field material removal for 3-axis milling.

The engine models the upper stock surface on a bounded regular XY grid.  It is
useful for 3-axis top-down milling and deliberately does not claim exact B-Rep,
undercut, or five-axis verification.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass
from typing import Callable, Protocol

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.setup import BoxStock
from hms_cadcam.cam.domain.tooling import (
    BallEndGeometry,
    BullNoseGeometry,
    CylindricalGeometry,
    ToolDefinition,
)
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.toolpath.events import MotionClass
from hms_cadcam.cam.toolpath.model import ToolpathArtifact
from hms_cadcam.cam.simulation.model import SimulationSamplingPolicy
from hms_cadcam.cam.simulation.sampling import (
    SamplingOutput,
    SimulationSamplingError,
    sample_toolpath,
)

from .contracts import EngineKind, QualityMode

Cancellation = Callable[[], bool]
Progress = Callable[[int, int], None]


class MaterialRemovalError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RemainingStock:
    width: int
    height: int
    cell_size_x: float
    cell_size_y: float
    top_heights: tuple[float, ...]
    initial_volume: float
    remaining_volume: float
    removed_volume: float
    minimum_height: float
    maximum_height: float
    unit: LengthUnit

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or len(self.top_heights) != self.width * self.height:
            raise CamValidationError("Remaining-stock grid is invalid")
        if self.cell_size_x <= 0.0 or self.cell_size_y <= 0.0:
            raise CamValidationError("Remaining-stock resolution is invalid")
        if any(not math.isfinite(value) for value in self.top_heights):
            raise CamValidationError("Remaining-stock height is invalid")
        if not 0.0 <= self.removed_volume <= self.initial_volume + 1.0e-8:
            raise CamValidationError("Removed-stock volume is invalid")


@dataclass(frozen=True, slots=True)
class HeightFieldResult:
    engine: EngineKind
    quality: QualityMode
    sampled: SamplingOutput
    remaining_stock: RemainingStock
    processed_cutting_samples: int
    ignored_non_cutting_samples: int


class SimulationEngine(Protocol):
    @property
    def kind(self) -> EngineKind: ...

    def simulate(
        self,
        *,
        stock: BoxStock,
        artifact: ToolpathArtifact,
        tool: ToolDefinition,
        quality: QualityMode,
        cancellation: Cancellation | None = None,
        progress: Progress | None = None,
    ) -> HeightFieldResult: ...


_GRID_TARGETS = {
    QualityMode.FAST: 96,
    QualityMode.STANDARD: 192,
    QualityMode.DETAILED: 384,
}
_MAX_CELLS = 1_000_000


class HeightField3AxisEngine:
    """Bounded deterministic height-field implementation with cutter profiles."""

    @property
    def kind(self) -> EngineKind:
        return EngineKind.HEIGHTFIELD_3AXIS

    def simulate(
        self,
        *,
        stock: BoxStock,
        artifact: ToolpathArtifact,
        tool: ToolDefinition,
        quality: QualityMode = QualityMode.STANDARD,
        initial_stock: RemainingStock | None = None,
        cancellation: Cancellation | None = None,
        progress: Progress | None = None,
    ) -> HeightFieldResult:
        if not isinstance(stock, BoxStock):
            raise MaterialRemovalError("HEIGHTFIELD_3AXIS requires box stock")
        if artifact.unit is not stock.size_x.unit or tool.unit is not artifact.unit:
            raise MaterialRemovalError("Stock, toolpath, and cutter units differ")
        profile = _CutterProfile.from_tool(tool)
        target = _GRID_TARGETS[quality]
        aspect = stock.size_x.value / stock.size_y.value
        width = max(2, round(target * math.sqrt(aspect)))
        height = max(2, round(target / math.sqrt(aspect)))
        if width * height > _MAX_CELLS:
            scale = math.sqrt(_MAX_CELLS / (width * height))
            width, height = max(2, int(width * scale)), max(2, int(height * scale))
        dx, dy = stock.size_x.value / width, stock.size_y.value / height
        top = stock.size_z.value
        if initial_stock is None:
            values = array("d", [top]) * (width * height)
            initial_volume = stock.size_x.value * stock.size_y.value * stock.size_z.value
        else:
            if (
                initial_stock.width != width
                or initial_stock.height != height
                or initial_stock.unit is not artifact.unit
                or abs(initial_stock.cell_size_x - dx) > 1.0e-12
                or abs(initial_stock.cell_size_y - dy) > 1.0e-12
            ):
                raise MaterialRemovalError("Incremental stock grid is incompatible")
            values = array("d", initial_stock.top_heights)
            initial_volume = initial_stock.initial_volume
        try:
            sampling = sample_toolpath(
                artifact=artifact,
                wcs=stock.frame,
                policy=_sampling_policy(dx, dy, quality),
                cancellation=cancellation,
            )
        except SimulationSamplingError as error:
            raise MaterialRemovalError(str(error)) from error
        cutting_indices: list[int] = []
        ignored = 0
        for segment in sampling.segments:
            if segment.motion_class is MotionClass.CUTTING:
                cutting_indices.extend(segment.sample_indices)
            else:
                ignored += len(segment.sample_indices)
        unique_cutting = tuple(dict.fromkeys(cutting_indices))
        total = len(unique_cutting)
        if progress is not None:
            progress(0, total)
        for processed, sample_index in enumerate(unique_cutting, start=1):
            if cancellation is not None and cancellation():
                raise MaterialRemovalError("Simulation material removal cancelled")
            point = sampling.samples[sample_index].setup_pose.position
            _remove_at(values, width, height, dx, dy, point.x, point.y, point.z, profile)
            if progress is not None and (processed == total or processed % 64 == 0):
                progress(processed, total)
        cell_area = dx * dy
        remaining = sum(max(0.0, value) for value in values) * cell_area
        initial = initial_volume
        remaining = min(initial, max(0.0, remaining))
        heights = tuple(values)
        stock_result = RemainingStock(
            width,
            height,
            dx,
            dy,
            heights,
            initial,
            remaining,
            max(0.0, initial - remaining),
            min(heights),
            max(heights),
            artifact.unit,
        )
        return HeightFieldResult(
            self.kind,
            quality,
            sampling,
            stock_result,
            total,
            ignored,
        )


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
        raise MaterialRemovalError(
            f"Unsupported cutter for material removal: {geometry.kind.value}"
        )

    def surface_offset(self, radial_distance: float) -> float:
        if radial_distance > self.radius:
            return math.inf
        if self.ball:
            return self.radius - math.sqrt(max(0.0, self.radius**2 - radial_distance**2))
        if self.corner_radius > 0.0 and radial_distance > self.radius - self.corner_radius:
            local = radial_distance - (self.radius - self.corner_radius)
            return self.corner_radius - math.sqrt(max(0.0, self.corner_radius**2 - local**2))
        return 0.0


def _sampling_policy(dx: float, dy: float, quality: QualityMode) -> SimulationSamplingPolicy:
    factor = {QualityMode.FAST: 1.5, QualityMode.STANDARD: 0.75, QualityMode.DETAILED: 0.4}[quality]
    step = max(1.0e-4, min(dx, dy) * factor)
    return SimulationSamplingPolicy(
        max_linear_step=step,
        chord_tolerance=max(1.0e-5, step / 8.0),
        maximum_samples=1_000_000,
        memory_budget_bytes=512 * 1024 * 1024,
    )


def _remove_at(
    values: array,
    width: int,
    height: int,
    dx: float,
    dy: float,
    x: float,
    y: float,
    tip_z: float,
    profile: _CutterProfile,
) -> None:
    minimum_x = max(0, math.floor((x - profile.radius) / dx))
    maximum_x = min(width - 1, math.floor((x + profile.radius) / dx))
    minimum_y = max(0, math.floor((y - profile.radius) / dy))
    maximum_y = min(height - 1, math.floor((y + profile.radius) / dy))
    for row in range(minimum_y, maximum_y + 1):
        center_y = (row + 0.5) * dy
        for column in range(minimum_x, maximum_x + 1):
            center_x = (column + 0.5) * dx
            radius = math.hypot(center_x - x, center_y - y)
            offset = profile.surface_offset(radius)
            if math.isinf(offset):
                continue
            index = row * width + column
            values[index] = min(values[index], max(0.0, tip_z + offset))
