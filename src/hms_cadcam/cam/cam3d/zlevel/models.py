"""Immutable domain contracts for the Stage 8A.3.1 Z-Level foundation.

The foundation deliberately stores only JSON-safe operation parameters and
native-free geometry evidence.  CAD kernel objects, workers and cancellation
tokens never cross this boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, ClassVar

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.ids import GeometryReferenceId, MachiningZone3DId, OperationId
from hms_cadcam.cam.domain.operation import (
    DiagnosticCode,
    DiagnosticSeverity,
    OperationParameterSet,
    ValidationDiagnostic,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import LengthUnit

Z_LEVEL_FINISHING_STRATEGY_KEY = "z_level_finishing_3d"
Z_LEVEL_FINISHING_STRATEGY_VERSION = 1
# Version 2 is an algorithm-only hardening revision.  The persisted strategy
# payload remains v1 so existing operations can be recalculated explicitly.
Z_LEVEL_FINISHING_ALGORITHM_VERSION = 2
_EPSILON = 1.0e-9


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"{name} phải là số thực")
    result = float(value)
    if not math.isfinite(result):
        raise CamValidationError(f"{name} phải hữu hạn")
    return 0.0 if result == 0.0 else result


class ZLevelOrientation(StrEnum):
    CLOCKWISE = "clockwise"
    COUNTER_CLOCKWISE = "counter_clockwise"
    AUTOMATIC = "automatic"


class ZLevelBoundaryPolicy(StrEnum):
    TRIMMED_FACE = "trimmed_face"
    CONSERVATIVE = "conservative"


class ZLevelLinkingMode(StrEnum):
    RETRACT_CLEARANCE = "retract_clearance"
    CONSERVATIVE_DIRECT = "conservative_direct"


class ZLevelBoundaryClassification(StrEnum):
    INTERIOR = "interior"
    ON_BOUNDARY = "on_boundary"
    OUTSIDE = "outside"
    AMBIGUOUS = "ambiguous"


class ZLevelLoopType(StrEnum):
    OUTER = "outer"
    INNER = "inner"
    DISCONNECTED = "disconnected"


class ZLevelArtifactStatus(StrEnum):
    CANDIDATE = "candidate"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


class ZLevelProgressPhase(StrEnum):
    VALIDATION = "validation"
    BOUNDS = "bounds"
    LEVEL_SCHEDULE = "level_schedule"
    FACE_PREPARATION = "face_preparation"
    IMPLICIT_FIELD = "implicit_field"
    CONTOUR_GRAPH = "contour_graph"
    DISCRETIZATION = "discretization"
    ORDERING = "ordering"
    LINKING = "linking"
    SAFETY = "safety"
    FINALIZATION = "finalization"


@dataclass(frozen=True, slots=True)
class ZLevelProgress:
    operation_id: OperationId
    phase: ZLevelProgressPhase
    processed: int
    total: int

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, OperationId) or not isinstance(
            self.phase, ZLevelProgressPhase
        ):
            raise CamValidationError("Z-Level progress identity không hợp lệ")
        if (
            type(self.processed) is not int
            or type(self.total) is not int
            or self.processed < 0
            or self.total < 0
            or self.processed > self.total
        ):
            raise CamValidationError("Z-Level progress range không hợp lệ")

    @property
    def percentage(self) -> float:
        return 100.0 if self.total == 0 else self.processed * 100.0 / self.total


ProgressCallback = Callable[[ZLevelProgress], None]


@dataclass(frozen=True, slots=True)
class ZLevelResolvedContact:
    """Optional CAD-adapter result using the original differential surface."""

    source_surface_id: GeometryReferenceId
    contact_point: Point3
    surface_normal: Vector3
    projection_deviation_mm: float

    def __post_init__(self) -> None:
        if not isinstance(self.source_surface_id, GeometryReferenceId):
            raise CamValidationError("Z-Level resolved contact source không hợp lệ")
        if not isinstance(self.contact_point, Point3) or self.contact_point.unit is not LengthUnit.MM:
            raise CamValidationError("Z-Level resolved contact point không hợp lệ")
        if not isinstance(self.surface_normal, Vector3) or not math.isclose(
            self.surface_normal.magnitude, 1.0, rel_tol=0.0, abs_tol=1.0e-8
        ):
            raise CamValidationError("Z-Level resolved differential normal không hợp lệ")
        deviation = _finite(self.projection_deviation_mm, "Z-Level projection deviation")
        if deviation < 0.0:
            raise CamValidationError("Z-Level projection deviation không được âm")
        object.__setattr__(self, "projection_deviation_mm", deviation)


ContactResolver = Callable[
    [GeometryReferenceId, Point3, float],
    ZLevelResolvedContact,
]


@dataclass(frozen=True, slots=True)
class ZLevelMachiningFrame:
    """Right-handed U/V/W frame.  W is the fixed three-axis tool direction."""

    origin: Point3
    u_axis: Vector3
    v_axis: Vector3
    w_axis: Vector3

    def __post_init__(self) -> None:
        if not isinstance(self.origin, Point3) or self.origin.unit is not LengthUnit.MM:
            raise CamValidationError("Z-Level frame origin phải dùng MM")
        axes = (self.u_axis, self.v_axis, self.w_axis)
        if any(
            not isinstance(axis, Vector3)
            or not math.isclose(axis.magnitude, 1.0, rel_tol=0.0, abs_tol=_EPSILON)
            for axis in axes
        ):
            raise CamValidationError("Z-Level frame axes phải chuẩn hóa")
        if any(
            abs(first.dot(second)) > _EPSILON
            for first, second in (
                (self.u_axis, self.v_axis),
                (self.u_axis, self.w_axis),
                (self.v_axis, self.w_axis),
            )
        ) or self.u_axis.cross(self.v_axis).dot(self.w_axis) < 1.0 - _EPSILON:
            raise CamValidationError("Z-Level frame phải trực giao thuận tay phải")

    def coordinates(self, point: Point3) -> tuple[float, float, float]:
        if not isinstance(point, Point3) or point.unit is not LengthUnit.MM:
            raise CamValidationError("Điểm Z-Level không dùng MM")
        delta = Vector3(
            point.x - self.origin.x,
            point.y - self.origin.y,
            point.z - self.origin.z,
        )
        return (
            delta.dot(self.u_axis),
            delta.dot(self.v_axis),
            delta.dot(self.w_axis),
        )

    def point(self, u: float, v: float, w: float) -> Point3:
        u, v, w = (_finite(item, "Tọa độ frame") for item in (u, v, w))
        return Point3(
            self.origin.x + self.u_axis.x * u + self.v_axis.x * v + self.w_axis.x * w,
            self.origin.y + self.u_axis.y * u + self.v_axis.y * v + self.w_axis.y * w,
            self.origin.z + self.u_axis.z * u + self.v_axis.z * v + self.w_axis.z * w,
            LengthUnit.MM,
        )

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin.to_dict(),
            "u_axis": self.u_axis.to_dict(),
            "v_axis": self.v_axis.to_dict(),
            "w_axis": self.w_axis.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ZLevelMachiningFrame":
        if not isinstance(data, dict) or set(data) != {
            "origin",
            "u_axis",
            "v_axis",
            "w_axis",
        }:
            raise CamValidationError("Z-Level frame payload không hợp lệ")
        return cls(
            Point3.from_dict(data["origin"]),
            Vector3.from_dict(data["u_axis"]),
            Vector3.from_dict(data["v_axis"]),
            Vector3.from_dict(data["w_axis"]),
        )


@dataclass(frozen=True, slots=True)
class ZLevelFinishingParameters:
    """Persistable strategy values.  Face references remain operation inputs."""

    zone_id: MachiningZone3DId
    top_level: float
    bottom_level: float
    stepdown_mm: float
    tolerance_mm: float = 0.01
    surface_allowance_mm: float = 0.0
    orientation: ZLevelOrientation = ZLevelOrientation.AUTOMATIC
    boundary_policy: ZLevelBoundaryPolicy = ZLevelBoundaryPolicy.TRIMMED_FACE
    linking_mode: ZLevelLinkingMode = ZLevelLinkingMode.RETRACT_CLEARANCE
    feed_rate_mm_per_minute: float = 500.0
    maximum_segment_length_mm: float = 2.0
    clearance_z_mm: float = 50.0
    retract_z_mm: float = 40.0
    link_clearance_mm: float = 1.0
    setup_reference: str | None = None
    machining_frame: ZLevelMachiningFrame | None = None
    SERIALIZATION_VERSION: ClassVar[int] = Z_LEVEL_FINISHING_STRATEGY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.zone_id, MachiningZone3DId):
            raise CamValidationError("Z-Level machining-zone reference không hợp lệ")
        top = _finite(self.top_level, "Z-Level top level")
        bottom = _finite(self.bottom_level, "Z-Level bottom level")
        stepdown = _finite(self.stepdown_mm, "Z-Level stepdown")
        tolerance = _finite(self.tolerance_mm, "Z-Level tolerance")
        allowance = _finite(self.surface_allowance_mm, "Z-Level allowance")
        if top < bottom:
            raise CamValidationError("Z-Level top level phải lớn hơn hoặc bằng bottom level")
        if stepdown <= 0.0:
            raise CamValidationError("Z-Level stepdown phải lớn hơn 0")
        if tolerance <= 0.0:
            raise CamValidationError("Z-Level tolerance phải lớn hơn 0")
        if allowance < 0.0:
            raise CamValidationError("Z-Level allowance không được âm")
        feed = _finite(self.feed_rate_mm_per_minute, "Z-Level feed")
        segment = _finite(self.maximum_segment_length_mm, "Z-Level segment length")
        clearance = _finite(self.clearance_z_mm, "Z-Level clearance")
        retract = _finite(self.retract_z_mm, "Z-Level retract")
        link = _finite(self.link_clearance_mm, "Z-Level link clearance")
        if feed <= 0.0 or segment <= 0.0 or link < 0.0:
            raise CamValidationError("Z-Level feed/segment/linking policy không hợp lệ")
        if clearance < retract:
            raise CamValidationError("Z-Level clearance phải lớn hơn hoặc bằng retract")
        if not isinstance(self.orientation, ZLevelOrientation) or not isinstance(
            self.boundary_policy, ZLevelBoundaryPolicy
        ) or not isinstance(self.linking_mode, ZLevelLinkingMode):
            raise CamValidationError("Z-Level orientation/boundary/linking policy không hợp lệ")
        if self.setup_reference is not None and (
            not isinstance(self.setup_reference, str) or not self.setup_reference.strip()
        ):
            raise CamValidationError("Z-Level setup reference không hợp lệ")
        if self.machining_frame is not None and not isinstance(
            self.machining_frame, ZLevelMachiningFrame
        ):
            raise CamValidationError("Z-Level machining frame không hợp lệ")
        for name, value in (
            ("top_level", top),
            ("bottom_level", bottom),
            ("stepdown_mm", stepdown),
            ("tolerance_mm", tolerance),
            ("surface_allowance_mm", allowance),
            ("feed_rate_mm_per_minute", feed),
            ("maximum_segment_length_mm", segment),
            ("clearance_z_mm", clearance),
            ("retract_z_mm", retract),
            ("link_clearance_mm", link),
        ):
            object.__setattr__(self, name, value)

    @property
    def allowance_mm(self) -> float:
        return self.surface_allowance_mm

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_operation_parameters().to_dict())

    def to_operation_parameters(self) -> OperationParameterSet:
        values: list[tuple[str, object]] = [
            ("bottom_level", self.bottom_level),
            ("boundary_policy", self.boundary_policy.value),
            ("clearance_z_mm", self.clearance_z_mm),
            ("feed_rate_mm_per_minute", self.feed_rate_mm_per_minute),
            ("link_clearance_mm", self.link_clearance_mm),
            ("linking_mode", self.linking_mode.value),
            ("maximum_segment_length_mm", self.maximum_segment_length_mm),
            ("orientation", self.orientation.value),
            ("retract_z_mm", self.retract_z_mm),
            ("setup_reference", self.setup_reference),
            ("stepdown_mm", self.stepdown_mm),
            ("surface_allowance_mm", self.surface_allowance_mm),
            ("tolerance_mm", self.tolerance_mm),
            ("top_level", self.top_level),
            ("zone_id", str(self.zone_id)),
        ]
        if self.machining_frame is not None:
            frame = self.machining_frame
            values.extend(
                (f"frame_{axis}_{component}", value)
                for axis, vector in (
                    ("origin", frame.origin),
                    ("u", frame.u_axis),
                    ("v", frame.v_axis),
                    ("w", frame.w_axis),
                )
                for component in ("x", "y", "z")
                for value in (getattr(vector, component),)
            )
        return OperationParameterSet(
            Z_LEVEL_FINISHING_STRATEGY_KEY,
            Z_LEVEL_FINISHING_STRATEGY_VERSION,
            tuple(values),
        )

    @classmethod
    def from_operation_parameters(cls, value: OperationParameterSet) -> "ZLevelFinishingParameters":
        if not isinstance(value, OperationParameterSet) or (
            value.strategy_key != Z_LEVEL_FINISHING_STRATEGY_KEY
            or value.strategy_version != Z_LEVEL_FINISHING_STRATEGY_VERSION
        ):
            raise CamValidationError("Z-Level strategy key/version không được hỗ trợ")
        payload = dict(value.values)
        required = {
            "zone_id",
            "top_level",
            "bottom_level",
            "stepdown_mm",
            "tolerance_mm",
            "surface_allowance_mm",
            "orientation",
            "boundary_policy",
            "linking_mode",
            "feed_rate_mm_per_minute",
            "maximum_segment_length_mm",
            "clearance_z_mm",
            "retract_z_mm",
            "link_clearance_mm",
        }
        if not required.issubset(payload) or set(payload) - required - {"setup_reference"} - {
            f"frame_{axis}_{component}"
            for axis in ("origin", "u", "v", "w")
            for component in ("x", "y", "z")
        }:
            raise CamValidationError("Z-Level parameter payload không đầy đủ")
        frame: ZLevelMachiningFrame | None = None
        frame_keys = {
            f"frame_{axis}_{component}"
            for axis in ("origin", "u", "v", "w")
            for component in ("x", "y", "z")
        }
        if frame_keys.issubset(payload):
            frame = ZLevelMachiningFrame(
                Point3(
                    payload["frame_origin_x"], payload["frame_origin_y"], payload["frame_origin_z"], LengthUnit.MM
                ),
                Vector3(payload["frame_u_x"], payload["frame_u_y"], payload["frame_u_z"]),
                Vector3(payload["frame_v_x"], payload["frame_v_y"], payload["frame_v_z"]),
                Vector3(payload["frame_w_x"], payload["frame_w_y"], payload["frame_w_z"]),
            )
        try:
            return cls(
                MachiningZone3DId.parse(payload["zone_id"]),  # type: ignore[arg-type]
                payload["top_level"],  # type: ignore[arg-type]
                payload["bottom_level"],  # type: ignore[arg-type]
                payload["stepdown_mm"],  # type: ignore[arg-type]
                payload["tolerance_mm"],  # type: ignore[arg-type]
                payload["surface_allowance_mm"],  # type: ignore[arg-type]
                ZLevelOrientation(payload["orientation"]),
                ZLevelBoundaryPolicy(payload["boundary_policy"]),
                ZLevelLinkingMode(payload["linking_mode"]),
                payload["feed_rate_mm_per_minute"],  # type: ignore[arg-type]
                payload["maximum_segment_length_mm"],  # type: ignore[arg-type]
                payload["clearance_z_mm"],  # type: ignore[arg-type]
                payload["retract_z_mm"],  # type: ignore[arg-type]
                payload["link_clearance_mm"],  # type: ignore[arg-type]
                payload.get("setup_reference"),  # type: ignore[arg-type]
                frame,
            )
        except (TypeError, ValueError, KeyError) as error:
            raise CamValidationError("Z-Level parameter payload không hợp lệ") from error


@dataclass(frozen=True, slots=True)
class ZLevelRegionBounds:
    u_min: float
    u_max: float
    v_min: float
    v_max: float
    w_min: float
    w_max: float

    def __post_init__(self) -> None:
        values = tuple(
            _finite(getattr(self, name), f"Z-Level bounds {name}")
            for name in ("u_min", "u_max", "v_min", "v_max", "w_min", "w_max")
        )
        if values[0] > values[1] or values[2] > values[3] or values[4] > values[5]:
            raise CamValidationError("Z-Level bounds bị đảo")
        for name, value in zip(
            ("u_min", "u_max", "v_min", "v_max", "w_min", "w_max"), values, strict=True
        ):
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in ("u_min", "u_max", "v_min", "v_max", "w_min", "w_max")}


@dataclass(frozen=True, slots=True)
class ZLevelSchedule:
    levels: tuple[float, ...]
    top_level: float
    bottom_level: float
    stepdown_mm: float
    tolerance_mm: float

    def __post_init__(self) -> None:
        if not self.levels or any(not math.isfinite(value) for value in self.levels):
            raise CamValidationError("Z-Level schedule rỗng hoặc không hữu hạn")
        if tuple(sorted(self.levels, reverse=True)) != self.levels or len(set(self.levels)) != len(self.levels):
            raise CamValidationError("Z-Level schedule phải giảm dần và không trùng")
        if abs(self.levels[0] - self.top_level) > self.tolerance_mm or abs(self.levels[-1] - self.bottom_level) > self.tolerance_mm:
            raise CamValidationError("Z-Level schedule không bao phủ top/bottom")

    def to_dict(self) -> dict[str, Any]:
        return {
            "levels": list(self.levels),
            "top_level": self.top_level,
            "bottom_level": self.bottom_level,
            "stepdown_mm": self.stepdown_mm,
            "tolerance_mm": self.tolerance_mm,
        }


@dataclass(frozen=True, slots=True)
class ZLevelPathPoint:
    contact_point: Point3
    tool_center_point: Point3
    surface_normal: Vector3
    requested_level: float
    level_deviation_mm: float
    contact_deviation_mm: float
    allowance_deviation_mm: float
    boundary_classification: ZLevelBoundaryClassification
    source_surface_ids: tuple[GeometryReferenceId, ...]
    source_triangle_index: int | None = None

    def __post_init__(self) -> None:
        if any(not isinstance(item, Point3) or item.unit is not LengthUnit.MM for item in (self.contact_point, self.tool_center_point)):
            raise CamValidationError("Z-Level contact/tool-center point không hợp lệ")
        if not isinstance(self.surface_normal, Vector3) or not math.isclose(self.surface_normal.magnitude, 1.0, rel_tol=0.0, abs_tol=1.0e-8):
            raise CamValidationError("Z-Level contact normal không chuẩn hóa")
        values = (
            _finite(self.requested_level, "Z-Level requested level"),
            _finite(self.level_deviation_mm, "Z-Level deviation"),
            _finite(self.contact_deviation_mm, "Z-Level contact deviation"),
            _finite(self.allowance_deviation_mm, "Z-Level allowance deviation"),
        )
        if values[1] < 0.0 or values[2] < 0.0 or values[3] < 0.0:
            raise CamValidationError("Z-Level deviations không được âm")
        if not isinstance(self.boundary_classification, ZLevelBoundaryClassification):
            raise CamValidationError("Z-Level boundary classification không hợp lệ")
        if not self.source_surface_ids or any(not isinstance(item, GeometryReferenceId) for item in self.source_surface_ids):
            raise CamValidationError("Z-Level face provenance không hợp lệ")
        if self.source_triangle_index is not None and (type(self.source_triangle_index) is not int or self.source_triangle_index < 0):
            raise CamValidationError("Z-Level triangle provenance không hợp lệ")
        object.__setattr__(self, "requested_level", values[0])
        object.__setattr__(self, "level_deviation_mm", values[1])
        object.__setattr__(self, "contact_deviation_mm", values[2])
        object.__setattr__(self, "allowance_deviation_mm", values[3])
        object.__setattr__(self, "source_surface_ids", tuple(sorted(set(self.source_surface_ids), key=str)))

    @property
    def contact_normal(self) -> Vector3:
        return self.surface_normal

    @property
    def face_provenance(self) -> tuple[GeometryReferenceId, ...]:
        return self.source_surface_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "contact_point": self.contact_point.to_dict(),
            "tool_center_point": self.tool_center_point.to_dict(),
            "surface_normal": self.surface_normal.to_dict(),
            "requested_level": self.requested_level,
            "level_deviation_mm": self.level_deviation_mm,
            "contact_deviation_mm": self.contact_deviation_mm,
            "allowance_deviation_mm": self.allowance_deviation_mm,
            "boundary_classification": self.boundary_classification.value,
            "source_surface_ids": [str(item) for item in self.source_surface_ids],
            "source_triangle_index": self.source_triangle_index,
        }


@dataclass(frozen=True, slots=True)
class ZLevelContour:
    pass_index: int
    segment_index: int
    level: float
    region_id: str
    loop_type: ZLevelLoopType
    orientation: ZLevelOrientation
    points: tuple[ZLevelPathPoint, ...]
    closed: bool
    predecessor: int | None = None
    pass_index_alias: int | None = None

    def __post_init__(self) -> None:
        if type(self.pass_index) is not int or self.pass_index < 0 or type(self.segment_index) is not int or self.segment_index < 0:
            raise CamValidationError("Z-Level contour identity không hợp lệ")
        if not isinstance(self.points, tuple) or len(self.points) < 2 or any(not isinstance(item, ZLevelPathPoint) for item in self.points):
            raise CamValidationError("Z-Level contour points không hợp lệ")
        if not isinstance(self.loop_type, ZLevelLoopType) or not isinstance(self.orientation, ZLevelOrientation):
            raise CamValidationError("Z-Level contour policy không hợp lệ")
        if type(self.closed) is not bool:
            raise CamValidationError("Z-Level contour closed flag không hợp lệ")
        if self.predecessor is not None and (type(self.predecessor) is not int or self.predecessor < 0):
            raise CamValidationError("Z-Level predecessor không hợp lệ")

    @property
    def v_position(self) -> float:
        return self.level

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_index": self.pass_index,
            "segment_index": self.segment_index,
            "level": self.level,
            "region_id": self.region_id,
            "loop_type": self.loop_type.value,
            "orientation": self.orientation.value,
            "closed": self.closed,
            "predecessor": self.predecessor,
            "points": [item.to_dict() for item in self.points],
        }


ZLevelSegment = ZLevelContour


@dataclass(frozen=True, slots=True)
class ZLevelPass:
    pass_index: int
    level: float
    segments: tuple[ZLevelContour, ...]

    def __post_init__(self) -> None:
        if type(self.pass_index) is not int or self.pass_index < 0 or not isinstance(self.segments, tuple):
            raise CamValidationError("Z-Level pass identity không hợp lệ")
        if any(item.pass_index != self.pass_index for item in self.segments):
            raise CamValidationError("Z-Level pass/contour identity không khớp")

    def to_dict(self) -> dict[str, Any]:
        return {"pass_index": self.pass_index, "level": self.level, "segments": [item.to_dict() for item in self.segments]}


@dataclass(frozen=True, slots=True)
class ZLevelStatistics:
    planned_level_count: int
    non_empty_level_count: int
    contour_count: int
    point_count: int
    rejected_sample_count: int = 0
    ambiguous_sample_count: int = 0

    def __post_init__(self) -> None:
        values = (self.planned_level_count, self.non_empty_level_count, self.contour_count, self.point_count, self.rejected_sample_count, self.ambiguous_sample_count)
        if any(type(value) is not int or value < 0 for value in values):
            raise CamValidationError("Z-Level statistics không hợp lệ")

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in ("planned_level_count", "non_empty_level_count", "contour_count", "point_count", "rejected_sample_count", "ambiguous_sample_count")}


@dataclass(frozen=True, slots=True)
class ZLevelPreview:
    frame: ZLevelMachiningFrame
    bounds: ZLevelRegionBounds
    schedule: ZLevelSchedule
    passes: tuple[ZLevelPass, ...]
    raw_intersection_segment_count: int
    clipped_segment_count: int
    statistics: ZLevelStatistics

    def __post_init__(self) -> None:
        if not isinstance(self.frame, ZLevelMachiningFrame) or not isinstance(self.bounds, ZLevelRegionBounds) or not isinstance(self.schedule, ZLevelSchedule):
            raise CamValidationError("Z-Level preview frame/bounds/schedule không hợp lệ")
        if not isinstance(self.passes, tuple) or any(not isinstance(item, ZLevelPass) for item in self.passes):
            raise CamValidationError("Z-Level preview passes không hợp lệ")
        if any(type(value) is not int or value < 0 for value in (self.raw_intersection_segment_count, self.clipped_segment_count)) or not isinstance(self.statistics, ZLevelStatistics):
            raise CamValidationError("Z-Level preview statistics không hợp lệ")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_Z_LEVEL_PREVIEW",
            "format_version": 1,
            "frame": self.frame.to_dict(),
            "bounds": self.bounds.to_dict(),
            "schedule": self.schedule.to_dict(),
            "passes": [item.to_dict() for item in self.passes],
            "raw_intersection_segment_count": self.raw_intersection_segment_count,
            "clipped_segment_count": self.clipped_segment_count,
            "statistics": self.statistics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ZLevelArtifactLifecycle:
    """Strategy-specific lifecycle evidence layered over shared ArtifactState."""

    status: ZLevelArtifactStatus
    operation_revision: Revision
    input_fingerprint: DependencyFingerprint
    artifact_fingerprint: ContentFingerprint | None = None
    safety_status: str = "unknown"
    safety_report_hash: str | None = None
    superseded: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, ZLevelArtifactStatus):
            raise CamValidationError("Z-Level artifact status không hợp lệ")
        if not isinstance(self.operation_revision, Revision) or not isinstance(self.input_fingerprint, DependencyFingerprint):
            raise CamValidationError("Z-Level artifact provenance không hợp lệ")
        if self.artifact_fingerprint is not None and not isinstance(self.artifact_fingerprint, ContentFingerprint):
            raise CamValidationError("Z-Level artifact fingerprint không hợp lệ")
        if not isinstance(self.safety_status, str) or not self.safety_status.strip():
            raise CamValidationError("Z-Level safety status không hợp lệ")
        if self.safety_report_hash is not None and (
            not isinstance(self.safety_report_hash, str) or not self.safety_report_hash.strip()
        ):
            raise CamValidationError("Z-Level safety hash không hợp lệ")
        if type(self.superseded) is not bool:
            raise CamValidationError("Z-Level superseded flag không hợp lệ")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "operation_revision": self.operation_revision.to_dict(),
            "input_fingerprint": self.input_fingerprint.to_dict(),
            "artifact_fingerprint": self.artifact_fingerprint.to_dict() if self.artifact_fingerprint else None,
            "safety_status": self.safety_status,
            "safety_report_hash": self.safety_report_hash,
            "superseded": self.superseded,
        }


class ZLevelFinishingError(RuntimeError):
    """Fail-closed error with a stable z_level.* diagnostic code."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        if not isinstance(code, DiagnosticCode) or not code.value.startswith("z_level."):
            raise CamValidationError("Z-Level diagnostic code không hợp lệ")
        if not isinstance(message, str) or not message.strip():
            raise CamValidationError("Z-Level diagnostic message không hợp lệ")
        super().__init__(message.strip())
        self.code = code

    @property
    def diagnostic(self) -> ValidationDiagnostic:
        return ValidationDiagnostic(DiagnosticSeverity.ERROR, self.code, str(self))
