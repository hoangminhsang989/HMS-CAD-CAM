"""Immutable PySide6-free values for deterministic Lathe XZ simulation V1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

from hms_cadcam.cam.lathe.toolpath.model import LatheMotionClass
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.cam.lathe.simulation.coordinates import finite_mm

MAX_INPUT_SEGMENTS = 100_000
MAX_FRAMES = 100_000
MAX_STOCK_STATIONS = 20_000
MAX_OPERATIONS = 1_000
MAX_GEOMETRY_POINTS = 4_096
MAX_EVENTS = 20_000
MAX_SETTINGS_BYTES = 16_384
MIN_SAMPLING_MM = 0.001
MAX_SAMPLING_MM = 10.0


class SimulationState(StrEnum):
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    INCOMPLETE = "INCOMPLETE"
    REJECTED = "REJECTED"


class SimulationMotionKind(StrEnum):
    RAPID = "RAPID"
    CUTTING = "CUTTING"
    LEAD_IN = "LEAD_IN"
    LEAD_OUT = "LEAD_OUT"
    THREAD_CUTTING = "THREAD_CUTTING"
    DWELL = "DWELL"
    UNSUPPORTED = "UNSUPPORTED"


class SafetySeverity(StrEnum):
    BLOCKING_ERROR = "BLOCKING_ERROR"
    COLLISION = "COLLISION"
    WARNING = "WARNING"
    INFORMATION = "INFORMATION"


class SafetyCode(StrEnum):
    RAPID_TOOL_STOCK_CONTACT = "RAPID_TOOL_STOCK_CONTACT"
    HOLDER_STOCK_CONTACT = "HOLDER_STOCK_CONTACT"
    TOOL_GEOMETRY_UNKNOWN = "TOOL_GEOMETRY_UNKNOWN"
    HOLDER_GEOMETRY_UNKNOWN = "HOLDER_GEOMETRY_UNKNOWN"
    MOTION_OUTSIDE_STOCK_DOMAIN = "MOTION_OUTSIDE_STOCK_DOMAIN"
    INVALID_SEGMENT_GEOMETRY = "INVALID_SEGMENT_GEOMETRY"
    STOCK_PROFILE_INVALID = "STOCK_PROFILE_INVALID"
    UNSUPPORTED_MOTION_KIND = "UNSUPPORTED_MOTION_KIND"
    THREAD_PROFILE_APPROXIMATION = "THREAD_PROFILE_APPROXIMATION"
    FRAME_LIMIT_REACHED = "FRAME_LIMIT_REACHED"
    STATION_LIMIT_REACHED = "STATION_LIMIT_REACHED"
    INPUT_LIMIT_REACHED = "INPUT_LIMIT_REACHED"


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    tolerance_mm: float = 1.0e-9
    sampling_resolution_mm: float = 0.25
    maximum_frame_count: int = 20_000
    maximum_stock_stations: int = 4_000
    stop_on_collision: bool = True
    thread_approximation_policy: str = "THREAD_PROFILE_APPROXIMATION_V1"
    display_decimation: int = 1
    maximum_event_count: int = 4_000
    progress_interval_frames: int = 10

    def __post_init__(self) -> None:
        tolerance = finite_mm(self.tolerance_mm, "Simulation tolerance")
        resolution = finite_mm(self.sampling_resolution_mm, "Sampling resolution")
        if tolerance <= 0.0 or not MIN_SAMPLING_MM <= resolution <= MAX_SAMPLING_MM:
            raise ValueError("Simulation tolerance or sampling resolution is invalid")
        for value, maximum, subject in (
            (self.maximum_frame_count, MAX_FRAMES, "frame"),
            (self.maximum_stock_stations, MAX_STOCK_STATIONS, "station"),
            (self.maximum_event_count, MAX_EVENTS, "event"),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"Simulation {subject} limit is invalid")
        if type(self.display_decimation) is not int or not 1 <= self.display_decimation <= 1_000:
            raise ValueError("Display decimation is invalid")
        if type(self.progress_interval_frames) is not int or not 1 <= self.progress_interval_frames <= 10_000:
            raise ValueError("Progress callback interval is invalid")
        if type(self.stop_on_collision) is not bool:
            raise TypeError("stop_on_collision must be bool")
        if self.thread_approximation_policy != "THREAD_PROFILE_APPROXIMATION_V1":
            raise ValueError("Unsupported thread approximation policy")
        object.__setattr__(self, "tolerance_mm", tolerance)
        object.__setattr__(self, "sampling_resolution_mm", resolution)


@dataclass(frozen=True, slots=True, order=True)
class SimulationPoint:
    radius_mm: float
    z_mm: float

    def __post_init__(self) -> None:
        radius = finite_mm(self.radius_mm, "Simulation radius")
        z_value = finite_mm(self.z_mm, "Simulation Z")
        if radius < 0.0:
            raise ValueError("Simulation radius must be non-negative")
        object.__setattr__(self, "radius_mm", radius)
        object.__setattr__(self, "z_mm", z_value)


@dataclass(frozen=True, slots=True, order=True)
class StockStation:
    z_mm: float
    inner_radius_mm: float
    outer_radius_mm: float

    def __post_init__(self) -> None:
        z_value = finite_mm(self.z_mm, "Stock station Z")
        inner = finite_mm(self.inner_radius_mm, "Stock inner radius")
        outer = finite_mm(self.outer_radius_mm, "Stock outer radius")
        if inner < 0.0 or outer < 0.0 or inner > outer:
            raise ValueError("Stock station wall thickness is invalid")
        object.__setattr__(self, "z_mm", z_value)
        object.__setattr__(self, "inner_radius_mm", inner)
        object.__setattr__(self, "outer_radius_mm", outer)


@dataclass(frozen=True, slots=True)
class AxisymmetricStock:
    stations: tuple[StockStation, ...]
    revision: int = 0
    approximation: str = "PIECEWISE_LINEAR_AXISYMMETRIC_V1"

    def __post_init__(self) -> None:
        if not isinstance(self.stations, tuple) or len(self.stations) < 2:
            raise ValueError("Axisymmetric stock requires at least two stations")
        if any(not isinstance(item, StockStation) for item in self.stations):
            raise TypeError("Axisymmetric stock stations are invalid")
        if tuple(item.z_mm for item in self.stations) != tuple(sorted(item.z_mm for item in self.stations)):
            raise ValueError("Axisymmetric stock stations must be sorted by Z")
        if len({item.z_mm for item in self.stations}) != len(self.stations):
            raise ValueError("Axisymmetric stock station Z values must be unique")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("Stock revision is invalid")

    @property
    def z_min_mm(self) -> float:
        return self.stations[0].z_mm

    @property
    def z_max_mm(self) -> float:
        return self.stations[-1].z_mm


@dataclass(frozen=True, slots=True)
class ToolEnvelope:
    nose_radius_mm: float | None
    insert_radius_mm: float | None
    orientation_deg: float | None
    holder_radius_mm: float | None
    holder_axial_offset_mm: float = 0.0
    holder_radial_offset_mm: float = 0.0

    def __post_init__(self) -> None:
        for name in ("nose_radius_mm", "insert_radius_mm", "holder_radius_mm"):
            raw = getattr(self, name)
            if raw is None:
                continue
            value = finite_mm(raw, name)
            if value <= 0.0:
                raise ValueError(f"{name} must be positive when known")
            object.__setattr__(self, name, value)
        if self.orientation_deg is not None:
            orientation = finite_mm(self.orientation_deg, "Tool orientation")
            if not 0.0 <= orientation < 360.0:
                raise ValueError("Tool orientation must be in [0, 360)")
            object.__setattr__(self, "orientation_deg", orientation)
        object.__setattr__(self, "holder_axial_offset_mm", finite_mm(self.holder_axial_offset_mm, "Holder axial offset"))
        object.__setattr__(self, "holder_radial_offset_mm", finite_mm(self.holder_radial_offset_mm, "Holder radial offset"))

    @property
    def tool_known(self) -> bool:
        return self.nose_radius_mm is not None and self.insert_radius_mm is not None and self.orientation_deg is not None

    @property
    def holder_known(self) -> bool:
        return self.holder_radius_mm is not None


@dataclass(frozen=True, slots=True)
class PlannedMotion:
    operation_id: str
    strategy_id: LatheStrategyId
    segment_id: str
    sequence: int
    kind: SimulationMotionKind
    start: SimulationPoint
    end: SimulationPoint


@dataclass(frozen=True, slots=True)
class SafetyEvent:
    code: SafetyCode
    severity: SafetySeverity
    sequence: int
    operation_id: str
    segment_id: str
    position: SimulationPoint


@dataclass(frozen=True, slots=True)
class RemovedMaterialSummary:
    cross_section_area_mm2: float
    estimated_volume_mm3: float
    approximation: str = "PIECEWISE_LINEAR_AXISYMMETRIC_V1"


@dataclass(frozen=True, slots=True)
class SimulationFrame:
    sequence: int
    operation_id: str
    strategy_id: LatheStrategyId
    segment_id: str
    progress: float
    tool_position: SimulationPoint
    motion_kind: SimulationMotionKind
    stock_revision: int
    removed: RemovedMaterialSummary
    events: tuple[SafetyEvent, ...] = ()

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("Frame sequence is invalid")
        if not math.isfinite(self.progress) or not 0.0 <= self.progress <= 1.0:
            raise ValueError("Frame progress is invalid")


@dataclass(frozen=True, slots=True)
class SimulationPlan:
    motions: tuple[PlannedMotion, ...]
    strategy_ids: tuple[LatheStrategyId, ...]
    operation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SimulationResult:
    state: SimulationState
    initial_stock: AxisymmetricStock
    final_stock: AxisymmetricStock
    frames: tuple[SimulationFrame, ...]
    events: tuple[SafetyEvent, ...]
    removed: RemovedMaterialSummary
    fingerprint: str
    complete_resolution: bool

    @property
    def display_frames(self) -> tuple[SimulationFrame, ...]:
        return self.frames

    @property
    def collision_count(self) -> int:
        return sum(item.severity is SafetySeverity.COLLISION for item in self.events)

    @property
    def warning_count(self) -> int:
        return sum(item.severity is SafetySeverity.WARNING for item in self.events)


def motion_kind(source: LatheMotionClass, strategy_id: LatheStrategyId) -> SimulationMotionKind:
    if strategy_id in {LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD} and source is LatheMotionClass.CUTTING:
        return SimulationMotionKind.THREAD_CUTTING
    return SimulationMotionKind(source.value)


__all__ = [name for name in globals() if name.startswith("MAX_") or name.startswith("MIN_")] + [
    "AxisymmetricStock", "PlannedMotion", "RemovedMaterialSummary", "SafetyCode",
    "SafetyEvent", "SafetySeverity", "SimulationFrame", "SimulationMotionKind",
    "SimulationPlan", "SimulationPoint", "SimulationResult", "SimulationSettings",
    "SimulationState", "StockStation", "ToolEnvelope", "motion_kind",
]
