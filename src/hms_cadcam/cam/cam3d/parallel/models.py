"""Native-free contracts for Parallel Finishing Foundation Stage 8A.2.1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, ClassVar

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.ids import (
    GeometryReferenceId,
    MachiningZone3DId,
    OperationId,
)
from hms_cadcam.cam.domain.operation import (
    DiagnosticCode,
    DiagnosticSeverity,
    OperationParameterSet,
    ValidationDiagnostic,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import LengthUnit

PARALLEL_FINISHING_STRATEGY_KEY = "parallel_finishing_3d"
PARALLEL_FINISHING_STRATEGY_VERSION = 1
PARALLEL_FINISHING_ALGORITHM_VERSION = 2
_ORTHOGONAL_TOLERANCE = 1.0e-9


class ParallelCutDirection(StrEnum):
    """Supported ordering semantics for adjacent passes."""

    ONE_WAY = "one_way"
    ZIGZAG = "zigzag"


class ParallelLinkingMode(StrEnum):
    """Foundation linking modes; every transition remains retract based."""

    RETRACT_BETWEEN_SEGMENTS = "retract_between_segments"


class ParallelNormalSource(StrEnum):
    """Evidence used to offset a contact point into a ball-center point."""

    MESH_FACET = "mesh_facet"
    BREP_SURFACE = "brep_surface"


class ParallelProgressPhase(StrEnum):
    """Stable phases reported by the synchronous worker-safe generator."""

    VALIDATION = "validation"
    FRAME_BOUNDS = "frame_bounds"
    PASS_GENERATION = "pass_generation"
    INTERSECTION = "intersection"
    DISCRETIZATION = "discretization"
    ORDERING_LINKING = "ordering_linking"
    IR_BUILD = "ir_build"
    FINALIZATION = "finalization"


@dataclass(frozen=True, slots=True)
class ParallelProgress:
    """One monotonic progress report for a calculation phase."""

    operation_id: OperationId
    phase: ParallelProgressPhase
    processed: int
    total: int

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, OperationId) or not isinstance(
            self.phase, ParallelProgressPhase
        ):
            raise CamValidationError("Parallel progress identity is invalid")
        if (
            type(self.processed) is not int
            or type(self.total) is not int
            or self.processed < 0
            or self.total < 0
            or self.processed > self.total
        ):
            raise CamValidationError("Parallel progress range is invalid")

    @property
    def percentage(self) -> float:
        return 100.0 if self.total == 0 else self.processed * 100.0 / self.total


ProgressCallback = Callable[[ParallelProgress], None]


@dataclass(frozen=True, slots=True)
class ParallelResolvedContact:
    """One source-surface projection and differential normal result."""

    source_surface_id: GeometryReferenceId
    contact_point: Point3
    surface_normal: Vector3
    projection_deviation_mm: float

    def __post_init__(self) -> None:
        if not isinstance(self.source_surface_id, GeometryReferenceId):
            raise CamValidationError("Parallel resolved-contact source is invalid")
        if (
            not isinstance(self.contact_point, Point3)
            or self.contact_point.unit is not LengthUnit.MM
        ):
            raise CamValidationError("Parallel resolved contact point is invalid")
        if not isinstance(self.surface_normal, Vector3) or not math.isclose(
            self.surface_normal.magnitude,
            1.0,
            rel_tol=0.0,
            abs_tol=_ORTHOGONAL_TOLERANCE,
        ):
            raise CamValidationError("Parallel resolved contact normal is invalid")
        deviation = _finite(
            self.projection_deviation_mm,
            "Parallel surface projection deviation",
        )
        if deviation < 0.0:
            raise CamValidationError(
                "Parallel surface projection deviation must not be negative"
            )
        object.__setattr__(self, "projection_deviation_mm", deviation)


ContactResolver = Callable[
    [GeometryReferenceId, Point3, float],
    ParallelResolvedContact,
]


class ParallelFinishingError(RuntimeError):
    """Fail-closed strategy error carrying one stable domain diagnostic."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        if not isinstance(code, DiagnosticCode) or not code.value.startswith("parallel."):
            raise CamValidationError("Parallel diagnostic code is invalid")
        if not isinstance(message, str) or not message.strip():
            raise CamValidationError("Parallel diagnostic message is invalid")
        super().__init__(message.strip())
        self.code = code

    @property
    def diagnostic(self) -> ValidationDiagnostic:
        return ValidationDiagnostic(DiagnosticSeverity.ERROR, self.code, str(self))


@dataclass(frozen=True, slots=True)
class ParallelFinishingParameters:
    """Editable strategy values not already owned by the Stage 8A.1 zone."""

    zone_id: MachiningZone3DId
    stepover_mm: float
    direction_angle_degrees: float = 0.0
    cut_direction: ParallelCutDirection = ParallelCutDirection.ONE_WAY
    linking_mode: ParallelLinkingMode = ParallelLinkingMode.RETRACT_BETWEEN_SEGMENTS
    feed_rate_mm_per_minute: float = 500.0
    maximum_segment_length_mm: float = 2.0
    SERIALIZATION_VERSION: ClassVar[int] = PARALLEL_FINISHING_STRATEGY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.zone_id, MachiningZone3DId):
            raise CamValidationError("Parallel machining-zone reference is invalid")
        stepover = _finite(self.stepover_mm, "Parallel stepover")
        if not 1.0e-6 <= stepover <= 1_000.0:
            raise CamValidationError("Parallel stepover is outside safe limits")
        angle = _finite(self.direction_angle_degrees, "Parallel direction angle") % 360.0
        if math.isclose(angle, 360.0, rel_tol=0.0, abs_tol=1.0e-12):
            angle = 0.0
        feed = _finite(self.feed_rate_mm_per_minute, "Parallel feed rate")
        if not 0.0 < feed <= 1_000_000.0:
            raise CamValidationError("Parallel feed rate is outside safe limits")
        spacing = _finite(
            self.maximum_segment_length_mm, "Parallel maximum segment length"
        )
        if not 1.0e-6 <= spacing <= 1_000.0:
            raise CamValidationError(
                "Parallel maximum segment length is outside safe limits"
            )
        if not isinstance(self.cut_direction, ParallelCutDirection) or not isinstance(
            self.linking_mode, ParallelLinkingMode
        ):
            raise CamValidationError("Parallel direction/linking policy is invalid")
        object.__setattr__(self, "stepover_mm", stepover)
        object.__setattr__(self, "direction_angle_degrees", angle)
        object.__setattr__(self, "feed_rate_mm_per_minute", feed)
        object.__setattr__(self, "maximum_segment_length_mm", spacing)

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_operation_parameters().to_dict())

    def to_operation_parameters(self) -> OperationParameterSet:
        """Encode using the existing SQLite-v4-compatible primitive parameter set."""
        return OperationParameterSet(
            PARALLEL_FINISHING_STRATEGY_KEY,
            PARALLEL_FINISHING_STRATEGY_VERSION,
            (
                ("cut_direction", self.cut_direction.value),
                ("direction_angle_degrees", self.direction_angle_degrees),
                ("feed_rate_mm_per_minute", self.feed_rate_mm_per_minute),
                ("linking_mode", self.linking_mode.value),
                ("maximum_segment_length_mm", self.maximum_segment_length_mm),
                ("stepover_mm", self.stepover_mm),
                ("zone_id", str(self.zone_id)),
            ),
        )

    @classmethod
    def from_operation_parameters(
        cls, value: OperationParameterSet
    ) -> "ParallelFinishingParameters":
        if not isinstance(value, OperationParameterSet):
            raise CamValidationError("Parallel operation parameters are invalid")
        if (
            value.strategy_key != PARALLEL_FINISHING_STRATEGY_KEY
            or value.strategy_version != PARALLEL_FINISHING_STRATEGY_VERSION
        ):
            raise CamValidationError("Parallel strategy key/version is unsupported")
        payload = dict(value.values)
        required = {
            "zone_id",
            "stepover_mm",
            "direction_angle_degrees",
            "cut_direction",
            "linking_mode",
            "feed_rate_mm_per_minute",
        }
        optional = {"maximum_segment_length_mm"}
        if not required.issubset(payload) or not set(payload).issubset(required | optional):
            raise CamValidationError("Parallel parameter payload is malformed")
        try:
            return cls(
                MachiningZone3DId.parse(payload["zone_id"]),  # type: ignore[arg-type]
                payload["stepover_mm"],  # type: ignore[arg-type]
                payload["direction_angle_degrees"],  # type: ignore[arg-type]
                ParallelCutDirection(payload["cut_direction"]),
                ParallelLinkingMode(payload["linking_mode"]),
                payload["feed_rate_mm_per_minute"],  # type: ignore[arg-type]
                payload.get("maximum_segment_length_mm", 2.0),  # type: ignore[arg-type]
            )
        except (TypeError, ValueError) as error:
            raise CamValidationError("Parallel parameter payload is invalid") from error


@dataclass(frozen=True, slots=True)
class ParallelMachiningFrame:
    """Right-handed U/V/W frame expressed in model/world coordinates."""

    origin: Point3
    u_axis: Vector3
    v_axis: Vector3
    w_axis: Vector3

    def __post_init__(self) -> None:
        if not isinstance(self.origin, Point3) or self.origin.unit is not LengthUnit.MM:
            raise CamValidationError("Parallel frame requires an MM origin")
        axes = (self.u_axis, self.v_axis, self.w_axis)
        if any(
            not isinstance(axis, Vector3)
            or not math.isclose(
                axis.magnitude, 1.0, rel_tol=0.0, abs_tol=_ORTHOGONAL_TOLERANCE
            )
            for axis in axes
        ):
            raise CamValidationError("Parallel frame axes must be normalized")
        if any(
            abs(first.dot(second)) > _ORTHOGONAL_TOLERANCE
            for first, second in (
                (self.u_axis, self.v_axis),
                (self.u_axis, self.w_axis),
                (self.v_axis, self.w_axis),
            )
        ):
            raise CamValidationError("Parallel frame axes must be orthogonal")
        if self.u_axis.cross(self.v_axis).dot(self.w_axis) < 1.0 - _ORTHOGONAL_TOLERANCE:
            raise CamValidationError("Parallel frame must be right handed")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def coordinates(self, point: Point3) -> tuple[float, float, float]:
        if not isinstance(point, Point3) or point.unit is not self.origin.unit:
            raise CamValidationError("Parallel frame point is invalid")
        offset = Vector3(
            point.x - self.origin.x,
            point.y - self.origin.y,
            point.z - self.origin.z,
        )
        return (
            offset.dot(self.u_axis),
            offset.dot(self.v_axis),
            offset.dot(self.w_axis),
        )

    def point(self, u: float, v: float, w: float) -> Point3:
        values = tuple(_finite(value, "Parallel frame coordinate") for value in (u, v, w))
        return Point3(
            self.origin.x
            + self.u_axis.x * values[0]
            + self.v_axis.x * values[1]
            + self.w_axis.x * values[2],
            self.origin.y
            + self.u_axis.y * values[0]
            + self.v_axis.y * values[1]
            + self.w_axis.y * values[2],
            self.origin.z
            + self.u_axis.z * values[0]
            + self.v_axis.z * values[1]
            + self.w_axis.z * values[2],
            LengthUnit.MM,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin.to_dict(),
            "u_axis": self.u_axis.to_dict(),
            "v_axis": self.v_axis.to_dict(),
            "w_axis": self.w_axis.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ParallelRegionBounds:
    """Selected machining-region extents in the machining frame."""

    u_min: float
    u_max: float
    v_min: float
    v_max: float
    w_min: float
    w_max: float

    def __post_init__(self) -> None:
        values = tuple(
            _finite(getattr(self, name), f"Parallel bounds {name}")
            for name in ("u_min", "u_max", "v_min", "v_max", "w_min", "w_max")
        )
        if values[0] > values[1] or values[2] > values[3] or values[4] > values[5]:
            raise CamValidationError("Parallel region bounds are reversed")
        for name, value in zip(
            ("u_min", "u_max", "v_min", "v_max", "w_min", "w_max"),
            values,
            strict=True,
        ):
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, float]:
        return {
            "u_min": self.u_min,
            "u_max": self.u_max,
            "v_min": self.v_min,
            "v_max": self.v_max,
            "w_min": self.w_min,
            "w_max": self.w_max,
        }


@dataclass(frozen=True, slots=True)
class ParallelPathPoint:
    """Mesh contact and ball-center sample with source-face evidence."""

    contact_point: Point3
    tool_center_point: Point3
    surface_normal: Vector3
    source_surface_ids: tuple[GeometryReferenceId, ...]
    normal_source: ParallelNormalSource = ParallelNormalSource.MESH_FACET
    surface_projection_deviation_mm: float = 0.0

    def __post_init__(self) -> None:
        if any(
            not isinstance(point, Point3) or point.unit is not LengthUnit.MM
            for point in (self.contact_point, self.tool_center_point)
        ):
            raise CamValidationError("Parallel path points are invalid")
        if not isinstance(self.surface_normal, Vector3) or not math.isclose(
            self.surface_normal.magnitude,
            1.0,
            rel_tol=0.0,
            abs_tol=_ORTHOGONAL_TOLERANCE,
        ):
            raise CamValidationError("Parallel path normal is invalid")
        if not self.source_surface_ids or any(
            not isinstance(item, GeometryReferenceId) for item in self.source_surface_ids
        ):
            raise CamValidationError("Parallel path source evidence is invalid")
        if not isinstance(self.normal_source, ParallelNormalSource):
            raise CamValidationError("Parallel path normal source is invalid")
        deviation = _finite(
            self.surface_projection_deviation_mm,
            "Parallel path surface projection deviation",
        )
        if deviation < 0.0:
            raise CamValidationError(
                "Parallel path surface projection deviation must not be negative"
            )
        object.__setattr__(self, "surface_projection_deviation_mm", deviation)
        object.__setattr__(
            self,
            "source_surface_ids",
            tuple(sorted(set(self.source_surface_ids), key=str)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contact_point": self.contact_point.to_dict(),
            "tool_center_point": self.tool_center_point.to_dict(),
            "surface_normal": self.surface_normal.to_dict(),
            "source_surface_ids": [str(item) for item in self.source_surface_ids],
            "normal_source": self.normal_source.value,
            "surface_projection_deviation_mm": (
                self.surface_projection_deviation_mm
            ),
        }


@dataclass(frozen=True, slots=True)
class ParallelSegment:
    """One disconnected, ordered cutting segment on a pass."""

    pass_index: int
    segment_index: int
    v_position: float
    points: tuple[ParallelPathPoint, ...]

    def __post_init__(self) -> None:
        if (
            type(self.pass_index) is not int
            or self.pass_index < 0
            or type(self.segment_index) is not int
            or self.segment_index < 0
        ):
            raise CamValidationError("Parallel segment identity is invalid")
        object.__setattr__(
            self, "v_position", _finite(self.v_position, "Parallel pass position")
        )
        if not isinstance(self.points, tuple) or len(self.points) < 2:
            raise CamValidationError("Parallel segment requires at least two points")
        if any(not isinstance(point, ParallelPathPoint) for point in self.points):
            raise CamValidationError("Parallel segment points are invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_index": self.pass_index,
            "segment_index": self.segment_index,
            "v_position": self.v_position,
            "points": [point.to_dict() for point in self.points],
        }


@dataclass(frozen=True, slots=True)
class ParallelPass:
    """All disconnected segments at one deterministic stepover position."""

    pass_index: int
    v_position: float
    segments: tuple[ParallelSegment, ...]

    def __post_init__(self) -> None:
        if type(self.pass_index) is not int or self.pass_index < 0:
            raise CamValidationError("Parallel pass index is invalid")
        object.__setattr__(
            self, "v_position", _finite(self.v_position, "Parallel pass position")
        )
        if not isinstance(self.segments, tuple) or any(
            not isinstance(segment, ParallelSegment)
            or segment.pass_index != self.pass_index
            or not math.isclose(
                segment.v_position,
                self.v_position,
                rel_tol=0.0,
                abs_tol=1.0e-9,
            )
            for segment in self.segments
        ):
            raise CamValidationError("Parallel pass segments are invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_index": self.pass_index,
            "v_position": self.v_position,
            "segments": [segment.to_dict() for segment in self.segments],
        }


@dataclass(frozen=True, slots=True)
class ParallelStatistics:
    """Deterministic strategy counts; timing is deliberately excluded."""

    planned_pass_count: int
    non_empty_pass_count: int
    segment_count: int
    contact_point_count: int
    toolpath_event_count: int

    def __post_init__(self) -> None:
        values = (
            self.planned_pass_count,
            self.non_empty_pass_count,
            self.segment_count,
            self.contact_point_count,
            self.toolpath_event_count,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise CamValidationError("Parallel statistics are invalid")

    def to_dict(self) -> dict[str, int]:
        return {
            "planned_pass_count": self.planned_pass_count,
            "non_empty_pass_count": self.non_empty_pass_count,
            "segment_count": self.segment_count,
            "contact_point_count": self.contact_point_count,
            "toolpath_event_count": self.toolpath_event_count,
        }


@dataclass(frozen=True, slots=True)
class ParallelPreview:
    """Stable debug geometry suitable for review artifacts and Viewer adapters."""

    frame: ParallelMachiningFrame
    bounds: ParallelRegionBounds
    pass_positions: tuple[float, ...]
    passes: tuple[ParallelPass, ...]
    raw_intersection_segment_count: int
    clipped_segment_count: int
    statistics: ParallelStatistics

    def __post_init__(self) -> None:
        if not isinstance(self.frame, ParallelMachiningFrame) or not isinstance(
            self.bounds, ParallelRegionBounds
        ):
            raise CamValidationError("Parallel preview frame/bounds are invalid")
        if not isinstance(self.pass_positions, tuple) or any(
            not math.isfinite(value) for value in self.pass_positions
        ):
            raise CamValidationError("Parallel preview pass positions are invalid")
        if not isinstance(self.passes, tuple) or any(
            not isinstance(item, ParallelPass) for item in self.passes
        ):
            raise CamValidationError("Parallel preview passes are invalid")
        if (
            type(self.raw_intersection_segment_count) is not int
            or self.raw_intersection_segment_count < 0
            or type(self.clipped_segment_count) is not int
            or self.clipped_segment_count < 0
            or not isinstance(self.statistics, ParallelStatistics)
        ):
            raise CamValidationError("Parallel preview statistics are invalid")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_PARALLEL_PREVIEW",
            "format_version": 1,
            "frame": self.frame.to_dict(),
            "bounds": self.bounds.to_dict(),
            "pass_positions": list(self.pass_positions),
            "passes": [item.to_dict() for item in self.passes],
            "raw_intersection_segment_count": self.raw_intersection_segment_count,
            "clipped_segment_count": self.clipped_segment_count,
            "statistics": self.statistics.to_dict(),
        }


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"{name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise CamValidationError(f"{name} must be finite")
    return 0.0 if normalized == 0.0 else normalized
