"""Pure-Python value objects for the versioned Facing 2.5D strategy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from hms_cadcam.cam.domain.errors import CamInvariantError, CamUnitError, CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import GeometryReferenceId
from hms_cadcam.cam.domain.operation import DiagnosticCode, OperationParameterSet
from hms_cadcam.cam.domain.geometry_reference import GeometryResolutionStatus
from hms_cadcam.cam.domain.revision import ContentFingerprint, GeometryFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, Length, LengthUnit, SpindleSpeed

FACING_STRATEGY_KEY = "facing_2_5d"
FACING_STRATEGY_VERSION = 1
_FORMAT = "HMS_CAM_FACING_PARAMETERS"


class FacingBoundarySource(StrEnum):
    STOCK_BOX = "stock_box"
    PLANAR_FACE = "planar_face"


class FacingCutDirection(StrEnum):
    CLIMB = "climb"
    CONVENTIONAL = "conventional"
    BIDIRECTIONAL = "bidirectional"


class FaceBoundaryCurve(StrEnum):
    LINE = "line"
    ARC = "arc"


@dataclass(frozen=True, slots=True)
class ResolvedFaceBoundary:
    """One deterministic closed polyline derived only from supported face edges."""

    points: tuple[Point3, ...]
    source_curves: tuple[FaceBoundaryCurve, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.points, tuple) or len(self.points) < 4:
            raise CamValidationError("Resolved face boundary requires a closed loop")
        if any(not isinstance(point, Point3) for point in self.points):
            raise CamValidationError("Resolved face boundary points are invalid")
        if len({point.unit for point in self.points}) != 1 or self.points[0].unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Resolved face boundary requires one known unit")
        if self.points[0] != self.points[-1]:
            raise CamInvariantError("Resolved face boundary must be explicitly closed")
        if not isinstance(self.source_curves, tuple) or not self.source_curves or any(
            not isinstance(curve, FaceBoundaryCurve) for curve in self.source_curves
        ):
            raise CamValidationError("Resolved face boundary curve provenance is invalid")


@dataclass(frozen=True, slots=True)
class PlanarFaceBounds:
    minimum: Point3
    maximum: Point3

    def __post_init__(self) -> None:
        if not isinstance(self.minimum, Point3) or not isinstance(self.maximum, Point3):
            raise CamValidationError("Planar face bounds are invalid")
        if self.minimum.unit is not self.maximum.unit or any(
            lower > upper for lower, upper in zip(
                (self.minimum.x, self.minimum.y, self.minimum.z),
                (self.maximum.x, self.maximum.y, self.maximum.z), strict=True
            )
        ):
            raise CamInvariantError("Planar face bounds are inconsistent")


@dataclass(frozen=True, slots=True)
class OccurrenceTransformProvenance:
    occurrence_path: str | None
    absolute_transform: tuple[float, ...]
    source_normal_reversed: bool = False

    def __post_init__(self) -> None:
        if self.occurrence_path is not None and (
            not isinstance(self.occurrence_path, str) or not self.occurrence_path.strip()
        ):
            raise CamValidationError("Occurrence path provenance is invalid")
        if not isinstance(self.absolute_transform, tuple) or len(self.absolute_transform) != 16 or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in self.absolute_transform
        ):
            raise CamValidationError("Occurrence transform provenance is invalid")
        if tuple(float(value) for value in self.absolute_transform[12:]) != (0.0, 0.0, 0.0, 1.0):
            raise CamInvariantError("Occurrence transform provenance must be affine")
        if type(self.source_normal_reversed) is not bool:
            raise CamValidationError("Source normal provenance flag is invalid")
        object.__setattr__(self, "absolute_transform", tuple(float(value) for value in self.absolute_transform))


def _positive(value: Length, name: str, unit: LengthUnit) -> None:
    if not isinstance(value, Length) or value.unit is not unit or value.value <= 0.0:
        raise CamValidationError(f"{name} must be a positive length in the strategy unit")


def _non_negative(value: Length, name: str, unit: LengthUnit) -> None:
    if not isinstance(value, Length) or value.unit is not unit or value.value < 0.0:
        raise CamValidationError(f"{name} must be a non-negative length in the strategy unit")


@dataclass(frozen=True, slots=True)
class FacingParameters:
    """Absolute Setup-WCS Z policy; cutting proceeds from top toward smaller Z."""

    unit: LengthUnit
    boundary_source: FacingBoundarySource
    top_height: Length
    target_height: Length
    stepdown: Length
    stepover: Length
    stock_allowance: Length
    clearance_height: Length
    retract_height: Length
    feed_rate: FeedRate
    plunge_feed_rate: FeedRate
    spindle_speed: SpindleSpeed
    direction: FacingCutDirection = FacingCutDirection.BIDIRECTIONAL
    raster_angle_degrees: float = 0.0
    overtravel: Length | None = None
    strategy_version: int = FACING_STRATEGY_VERSION
    schema_version: int = 1
    SERIALIZATION_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Facing parameters require a known length unit")
        if not isinstance(self.boundary_source, FacingBoundarySource) or not isinstance(self.direction, FacingCutDirection):
            raise CamValidationError("Facing enum value is invalid")
        if type(self.strategy_version) is not int or self.strategy_version != FACING_STRATEGY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported Facing strategy version")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise UnsupportedCamSchemaError("Unsupported Facing parameter schema version")
        for value, name in ((self.stepdown, "Stepdown"), (self.stepover, "Stepover")):
            _positive(value, name, self.unit)
        for value, name in ((self.stock_allowance, "Stock allowance"), (self.overtravel or Length(0, self.unit), "Overtravel")):
            _non_negative(value, name, self.unit)
        for value, name in ((self.top_height, "Top height"), (self.target_height, "Target height"),
                            (self.clearance_height, "Clearance height"), (self.retract_height, "Retract height")):
            if not isinstance(value, Length) or value.unit is not self.unit:
                raise CamUnitError(f"{name} must use the Facing unit")
        if self.target_height.value + self.stock_allowance.value >= self.top_height.value:
            raise CamInvariantError("Facing target plus allowance must be below top height")
        if self.retract_height.value <= self.top_height.value or self.clearance_height.value < self.retract_height.value:
            raise CamInvariantError("Facing retract must be above top and clearance must be at or above retract")
        expected_feed = FeedUnit.MM_PER_MINUTE if self.unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
        if not isinstance(self.feed_rate, FeedRate) or not isinstance(self.plunge_feed_rate, FeedRate):
            raise CamValidationError("Facing feeds are invalid")
        if self.feed_rate.unit is not expected_feed or self.plunge_feed_rate.unit is not expected_feed:
            raise CamUnitError("Facing feeds must match the strategy length unit")
        if not isinstance(self.spindle_speed, SpindleSpeed):
            raise CamValidationError("Facing spindle speed is invalid")
        if isinstance(self.raster_angle_degrees, bool) or not isinstance(self.raster_angle_degrees, (int, float)) or not math.isfinite(self.raster_angle_degrees):
            raise CamValidationError("Facing raster angle must be finite")
        object.__setattr__(self, "raster_angle_degrees", float(self.raster_angle_degrees) % 180.0)
        if self.overtravel is None:
            object.__setattr__(self, "overtravel", Length(0.0, self.unit))

    @property
    def final_cut_height(self) -> float:
        return self.target_height.value + self.stock_allowance.value

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_operation_parameters(self) -> OperationParameterSet:
        values = self.to_dict()
        return OperationParameterSet(FACING_STRATEGY_KEY, FACING_STRATEGY_VERSION,
            tuple((key, value) for key, value in values.items() if key not in {"format", "format_version", "strategy_version", "schema_version"}))

    @classmethod
    def from_operation_parameters(cls, value: OperationParameterSet) -> "FacingParameters":
        if value.strategy_key != FACING_STRATEGY_KEY:
            raise CamValidationError("Operation is not a Facing strategy")
        payload = dict(value.values)
        # Stage17A metadata is additive; legacy numeric semantics remain V1.
        payload.pop("automatic_parameter_contract", None)
        payload.update(format=_FORMAT, format_version=1, strategy_version=value.strategy_version,
                       schema_version=value.schema_version)
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        return {"format": _FORMAT, "format_version": 1, "strategy_version": self.strategy_version,
                "schema_version": self.schema_version, "unit": self.unit.value,
                "boundary_source": self.boundary_source.value, "top_height": self.top_height.value,
                "target_height": self.target_height.value, "stepdown": self.stepdown.value,
                "stepover": self.stepover.value, "stock_allowance": self.stock_allowance.value,
                "clearance_height": self.clearance_height.value, "retract_height": self.retract_height.value,
                "feed_rate": self.feed_rate.value, "plunge_feed_rate": self.plunge_feed_rate.value,
                "spindle_speed": self.spindle_speed.value, "direction": self.direction.value,
                "raster_angle_degrees": self.raster_angle_degrees, "overtravel": self.overtravel.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FacingParameters":
        fields = {"format", "format_version", "strategy_version", "schema_version", "unit", "boundary_source",
                  "top_height", "target_height", "stepdown", "stepover", "stock_allowance", "clearance_height",
                  "retract_height", "feed_rate", "plunge_feed_rate", "spindle_speed", "direction",
                  "raster_angle_degrees", "overtravel"}
        if not isinstance(data, dict) or set(data) != fields or data.get("format") != _FORMAT or data.get("format_version") != 1:
            raise CamValidationError("Facing parameter payload is malformed")
        try:
            unit = LengthUnit(data["unit"])
            feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            return cls(unit, FacingBoundarySource(data["boundary_source"]), Length(data["top_height"], unit),
                       Length(data["target_height"], unit), Length(data["stepdown"], unit), Length(data["stepover"], unit),
                       Length(data["stock_allowance"], unit), Length(data["clearance_height"], unit),
                       Length(data["retract_height"], unit), FeedRate(data["feed_rate"], feed_unit),
                       FeedRate(data["plunge_feed_rate"], feed_unit), SpindleSpeed(data["spindle_speed"]),
                       FacingCutDirection(data["direction"]), data["raster_angle_degrees"], Length(data["overtravel"], unit),
                       data["strategy_version"], data["schema_version"])
        except UnsupportedCamSchemaError:
            raise
        except (TypeError, ValueError) as error:
            raise CamValidationError("Facing parameter payload is invalid") from error


@dataclass(frozen=True, slots=True)
class FacingRegion:
    """Planar polygon in Setup WCS; points are ordered around its boundary."""

    boundary: tuple[Point3, ...]
    normal: Vector3
    fingerprint: GeometryFingerprint

    def __post_init__(self) -> None:
        if not isinstance(self.boundary, tuple) or len(self.boundary) < 3:
            raise CamValidationError("Facing region requires at least three boundary points")
        if any(not isinstance(point, Point3) for point in self.boundary) or len({point.unit for point in self.boundary}) != 1:
            raise CamUnitError("Facing boundary points require one known unit")
        if self.boundary[0].unit is LengthUnit.UNKNOWN or not isinstance(self.normal, Vector3) or not isinstance(self.fingerprint, GeometryFingerprint):
            raise CamValidationError("Facing region metadata is invalid")
        if self.normal.magnitude <= 1e-12:
            raise CamValidationError("Facing region normal is invalid")
        z = self.boundary[0].z
        if any(abs(point.z - z) > 1e-8 for point in self.boundary):
            raise CamInvariantError("Facing region must be planar in Setup WCS")


@dataclass(frozen=True, slots=True)
class PlanarFaceDescriptor:
    """Complete OCP-free planar face resolved in model/world coordinates."""

    reference_id: GeometryReferenceId
    source_id: UUID
    plane_origin: Point3
    x_axis: Vector3
    y_axis: Vector3
    normal: Vector3
    outer_boundary: ResolvedFaceBoundary
    inner_boundaries: tuple[ResolvedFaceBoundary, ...]
    bounds: PlanarFaceBounds
    unit: LengthUnit
    geometry_fingerprint: GeometryFingerprint
    transform_provenance: OccurrenceTransformProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, GeometryReferenceId) or not isinstance(self.source_id, UUID):
            raise CamValidationError("Planar face reference identity is invalid")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Planar face descriptor requires a known unit")
        if not isinstance(self.plane_origin, Point3) or self.plane_origin.unit is not self.unit:
            raise CamUnitError("Planar face origin unit is invalid")
        if any(not isinstance(axis, Vector3) for axis in (self.x_axis, self.y_axis, self.normal)):
            raise CamValidationError("Planar face basis is invalid")
        if any(abs(axis.magnitude - 1.0) > 1.0e-9 for axis in (self.x_axis, self.y_axis, self.normal)):
            raise CamInvariantError("Planar face basis must use unit vectors")
        if any(abs(first.dot(second)) > 1.0e-9 for first, second in (
            (self.x_axis, self.y_axis), (self.x_axis, self.normal), (self.y_axis, self.normal)
        )) or self.x_axis.cross(self.y_axis).dot(self.normal) < 1.0 - 1.0e-9:
            raise CamInvariantError("Planar face basis must be orthonormal and right-handed")
        if not isinstance(self.outer_boundary, ResolvedFaceBoundary) or any(
            not isinstance(loop, ResolvedFaceBoundary) for loop in self.inner_boundaries
        ):
            raise CamValidationError("Planar face boundaries are invalid")
        if not isinstance(self.inner_boundaries, tuple) or not isinstance(self.bounds, PlanarFaceBounds):
            raise CamValidationError("Planar face boundary metadata is invalid")
        points = (*self.outer_boundary.points, *(point for loop in self.inner_boundaries for point in loop.points))
        if any(point.unit is not self.unit for point in points) or self.bounds.minimum.unit is not self.unit:
            raise CamUnitError("Planar face geometry must use the descriptor unit")
        if not isinstance(self.geometry_fingerprint, GeometryFingerprint) or not isinstance(
            self.transform_provenance, OccurrenceTransformProvenance
        ):
            raise CamValidationError("Planar face fingerprint or transform provenance is invalid")


@dataclass(frozen=True, slots=True)
class ResolvedMachiningGeometry:
    """Fail-closed native-free envelope returned by a CAD face resolver."""

    status: GeometryResolutionStatus
    planar_face: PlanarFaceDescriptor | None = None
    message: str | None = None
    diagnostic_code: DiagnosticCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, GeometryResolutionStatus):
            raise CamValidationError("Machining geometry resolution status is invalid")
        if (self.status is GeometryResolutionStatus.RESOLVED) != (self.planar_face is not None):
            raise CamInvariantError("Only resolved machining geometry carries a face descriptor")
        if self.message is not None and (not isinstance(self.message, str) or not self.message.strip()):
            raise CamValidationError("Machining geometry diagnostic message is invalid")
        if self.diagnostic_code is not None and not isinstance(self.diagnostic_code, DiagnosticCode):
            raise CamValidationError("Machining geometry diagnostic code is invalid")
