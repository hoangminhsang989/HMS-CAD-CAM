"""Pure-Python value objects for the versioned 2D Contour strategy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from hms_cadcam.cam.domain.errors import (
    CamInvariantError,
    CamUnitError,
    CamValidationError,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.facing import OccurrenceTransformProvenance
from hms_cadcam.cam.domain.geometry_reference import GeometryReference, GeometryResolutionStatus
from hms_cadcam.cam.domain.operation import DiagnosticCode, OperationParameterSet
from hms_cadcam.cam.domain.revision import ContentFingerprint, GeometryFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, Length, LengthUnit, SpindleSpeed

CONTOUR_STRATEGY_KEY = "contour_2d"
CONTOUR_STRATEGY_VERSION = 1
_PARAMETER_FORMAT = "HMS_CAM_CONTOUR_2D_PARAMETERS"
_TOLERANCE = 1.0e-8


class ContourProfileSource(StrEnum):
    PLANAR_FACE_OUTER = "planar_face_outer"
    CLOSED_WIRE = "closed_wire"


class ContourSide(StrEnum):
    ON = "on"
    INSIDE = "inside"
    OUTSIDE = "outside"


class ContourCutDirection(StrEnum):
    CLIMB = "climb"
    CONVENTIONAL = "conventional"


class ContourStartPolicy(StrEnum):
    MIN_X_THEN_Y = "min_x_then_y"


class ContourLeadPolicy(StrEnum):
    LINEAR = "linear"


class ContourCurveKind(StrEnum):
    LINE = "line"
    ARC = "arc"


class ContourOrientation(StrEnum):
    CLOCKWISE = "clockwise"
    COUNTERCLOCKWISE = "counterclockwise"


def _known_length(value: Length, name: str, unit: LengthUnit) -> None:
    if not isinstance(value, Length) or value.unit is not unit:
        raise CamUnitError(f"{name} must use the Contour unit")


def _positive(value: Length, name: str, unit: LengthUnit) -> None:
    _known_length(value, name, unit)
    if value.value <= 0.0:
        raise CamValidationError(f"{name} must be positive")


def _non_negative(value: Length, name: str, unit: LengthUnit) -> None:
    _known_length(value, name, unit)
    if value.value < 0.0:
        raise CamValidationError(f"{name} must not be negative")


@dataclass(frozen=True, slots=True)
class ContourParameters:
    """Absolute Setup-WCS Z values; cutting proceeds from top toward smaller Z."""

    unit: LengthUnit
    profile_source: ContourProfileSource
    side: ContourSide
    top_height: Length
    final_depth: Length
    stepdown: Length
    radial_stock_allowance: Length
    axial_stock_allowance: Length
    clearance_height: Length
    retract_height: Length
    cutting_feed_rate: FeedRate
    plunge_feed_rate: FeedRate
    spindle_speed: SpindleSpeed
    direction: ContourCutDirection = ContourCutDirection.CLIMB
    start_policy: ContourStartPolicy = ContourStartPolicy.MIN_X_THEN_Y
    lead_policy: ContourLeadPolicy = ContourLeadPolicy.LINEAR
    lead_length: Length | None = None
    finishing_pass: bool = False
    multiple_depth_passes: bool = True
    strategy_version: int = CONTOUR_STRATEGY_VERSION
    schema_version: int = 1
    SERIALIZATION_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("2D Contour parameters require a known length unit")
        if not all(isinstance(value, expected) for value, expected in (
            (self.profile_source, ContourProfileSource),
            (self.side, ContourSide),
            (self.direction, ContourCutDirection),
            (self.start_policy, ContourStartPolicy),
            (self.lead_policy, ContourLeadPolicy),
        )):
            raise CamValidationError("2D Contour enum value is invalid")
        if self.strategy_version != CONTOUR_STRATEGY_VERSION or type(self.strategy_version) is not int:
            raise UnsupportedCamSchemaError("Unsupported 2D Contour strategy version")
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise UnsupportedCamSchemaError("Unsupported 2D Contour parameter schema version")
        _positive(self.stepdown, "Stepdown", self.unit)
        _non_negative(self.radial_stock_allowance, "Radial stock allowance", self.unit)
        _non_negative(self.axial_stock_allowance, "Axial stock allowance", self.unit)
        for value, name in (
            (self.top_height, "Top height"),
            (self.final_depth, "Final depth"),
            (self.clearance_height, "Clearance height"),
            (self.retract_height, "Retract height"),
        ):
            _known_length(value, name, self.unit)
        lead = self.lead_length if self.lead_length is not None else Length(0.0, self.unit)
        _positive(lead, "Lead length", self.unit)
        object.__setattr__(self, "lead_length", lead)
        if self.final_cut_depth >= self.top_height.value:
            raise CamInvariantError("Final depth plus axial allowance must be below top height")
        if self.retract_height.value <= self.top_height.value:
            raise CamInvariantError("Retract height must be above top height")
        if self.clearance_height.value < self.retract_height.value:
            raise CamInvariantError("Clearance height must be at or above retract height")
        expected_feed = FeedUnit.MM_PER_MINUTE if self.unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
        if not isinstance(self.cutting_feed_rate, FeedRate) or not isinstance(self.plunge_feed_rate, FeedRate):
            raise CamValidationError("2D Contour feeds are invalid")
        if self.cutting_feed_rate.unit is not expected_feed or self.plunge_feed_rate.unit is not expected_feed:
            raise CamUnitError("2D Contour feeds must match the strategy length unit")
        if not isinstance(self.spindle_speed, SpindleSpeed):
            raise CamValidationError("2D Contour spindle speed is invalid")
        if type(self.finishing_pass) is not bool or type(self.multiple_depth_passes) is not bool:
            raise CamValidationError("2D Contour pass flags must be boolean")

    @property
    def final_cut_depth(self) -> float:
        """Return the final cutter-tip Z after leaving explicit axial stock."""
        return self.final_depth.value + self.axial_stock_allowance.value

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_operation_parameters(self) -> OperationParameterSet:
        payload = self.to_dict()
        excluded = {"format", "format_version", "strategy_version", "schema_version"}
        return OperationParameterSet(CONTOUR_STRATEGY_KEY, CONTOUR_STRATEGY_VERSION,
                                     tuple((key, value) for key, value in payload.items() if key not in excluded))

    @classmethod
    def from_operation_parameters(cls, value: OperationParameterSet) -> "ContourParameters":
        if value.strategy_key != CONTOUR_STRATEGY_KEY:
            raise CamValidationError("Operation is not a 2D Contour strategy")
        payload = dict(value.values)
        # Stage17A metadata is additive; legacy numeric Contour semantics stay V1.
        payload.pop("automatic_parameter_contract", None)
        payload.update(format=_PARAMETER_FORMAT, format_version=1,
                       strategy_version=value.strategy_version, schema_version=value.schema_version)
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _PARAMETER_FORMAT,
            "format_version": 1,
            "strategy_version": self.strategy_version,
            "schema_version": self.schema_version,
            "unit": self.unit.value,
            "profile_source": self.profile_source.value,
            "side": self.side.value,
            "top_height": self.top_height.value,
            "final_depth": self.final_depth.value,
            "stepdown": self.stepdown.value,
            "radial_stock_allowance": self.radial_stock_allowance.value,
            "axial_stock_allowance": self.axial_stock_allowance.value,
            "clearance_height": self.clearance_height.value,
            "retract_height": self.retract_height.value,
            "cutting_feed_rate": self.cutting_feed_rate.value,
            "plunge_feed_rate": self.plunge_feed_rate.value,
            "spindle_speed": self.spindle_speed.value,
            "direction": self.direction.value,
            "start_policy": self.start_policy.value,
            "lead_policy": self.lead_policy.value,
            "lead_length": self.lead_length.value,
            "finishing_pass": self.finishing_pass,
            "multiple_depth_passes": self.multiple_depth_passes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContourParameters":
        fields = {
            "format", "format_version", "strategy_version", "schema_version", "unit",
            "profile_source", "side", "top_height", "final_depth", "stepdown",
            "radial_stock_allowance", "axial_stock_allowance", "clearance_height",
            "retract_height", "cutting_feed_rate", "plunge_feed_rate", "spindle_speed",
            "direction", "start_policy", "lead_policy", "lead_length", "finishing_pass",
            "multiple_depth_passes",
        }
        if not isinstance(data, dict) or set(data) != fields or data.get("format") != _PARAMETER_FORMAT:
            raise CamValidationError("2D Contour parameter payload is malformed")
        if data.get("format_version") != 1:
            raise UnsupportedCamSchemaError("Unsupported 2D Contour parameter format")
        try:
            unit = LengthUnit(data["unit"])
            feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            return cls(
                unit, ContourProfileSource(data["profile_source"]), ContourSide(data["side"]),
                Length(data["top_height"], unit), Length(data["final_depth"], unit),
                Length(data["stepdown"], unit), Length(data["radial_stock_allowance"], unit),
                Length(data["axial_stock_allowance"], unit), Length(data["clearance_height"], unit),
                Length(data["retract_height"], unit), FeedRate(data["cutting_feed_rate"], feed_unit),
                FeedRate(data["plunge_feed_rate"], feed_unit), SpindleSpeed(data["spindle_speed"]),
                ContourCutDirection(data["direction"]), ContourStartPolicy(data["start_policy"]),
                ContourLeadPolicy(data["lead_policy"]), Length(data["lead_length"], unit),
                data["finishing_pass"], data["multiple_depth_passes"],
                data["strategy_version"], data["schema_version"],
            )
        except UnsupportedCamSchemaError:
            raise
        except (TypeError, ValueError) as error:
            raise CamValidationError("2D Contour parameter payload is invalid") from error


@dataclass(frozen=True, slots=True)
class ContourSegment:
    """One exact LINE or circular ARC in profile coordinates."""

    kind: ContourCurveKind
    start: Point3
    end: Point3
    center: Point3 | None = None
    sweep_radians: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ContourCurveKind) or not isinstance(self.start, Point3) or not isinstance(self.end, Point3):
            raise CamValidationError("Contour segment is invalid")
        if self.start.unit is LengthUnit.UNKNOWN or self.start.unit is not self.end.unit:
            raise CamUnitError("Contour segment requires one known unit")
        if _distance(self.start, self.end) <= _TOLERANCE:
            raise CamInvariantError("Contour segment cannot have zero length")
        if self.kind is ContourCurveKind.LINE:
            if self.center is not None or self.sweep_radians is not None:
                raise CamValidationError("LINE segment cannot carry ARC fields")
            return
        if not isinstance(self.center, Point3) or self.center.unit is not self.start.unit:
            raise CamUnitError("ARC center unit is invalid")
        if isinstance(self.sweep_radians, bool) or not isinstance(self.sweep_radians, (int, float)):
            raise CamValidationError("ARC sweep is invalid")
        sweep = float(self.sweep_radians)
        if not math.isfinite(sweep) or abs(sweep) <= _TOLERANCE or abs(sweep) >= math.tau - _TOLERANCE:
            raise CamValidationError("ARC sweep must be finite and less than one full turn")
        start_radius, end_radius = _distance(self.start, self.center), _distance(self.end, self.center)
        if start_radius <= _TOLERANCE or abs(start_radius - end_radius) > _TOLERANCE:
            raise CamInvariantError("ARC endpoints must share one positive radius")
        object.__setattr__(self, "sweep_radians", sweep)

    @property
    def unit(self) -> LengthUnit:
        return self.start.unit

    @property
    def radius(self) -> float | None:
        return None if self.center is None else _distance(self.start, self.center)

    def reversed(self) -> "ContourSegment":
        return ContourSegment(self.kind, self.end, self.start, self.center,
                              None if self.sweep_radians is None else -self.sweep_radians)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "start": self.start.to_dict(), "end": self.end.to_dict(),
                "center": None if self.center is None else self.center.to_dict(), "sweep_radians": self.sweep_radians}


@dataclass(frozen=True, slots=True)
class ContourLoop:
    segments: tuple[ContourSegment, ...]
    orientation: ContourOrientation
    closed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.segments, tuple) or len(self.segments) < 2:
            raise CamValidationError("Contour loop requires ordered segments")
        if any(not isinstance(value, ContourSegment) for value in self.segments):
            raise CamValidationError("Contour loop segment is invalid")
        if not isinstance(self.orientation, ContourOrientation) or type(self.closed) is not bool:
            raise CamValidationError("Contour loop metadata is invalid")
        if not self.closed:
            raise CamInvariantError("2D Contour v1 accepts only closed loops")
        unit = self.segments[0].unit
        for current, following in zip(self.segments, (*self.segments[1:], self.segments[0]), strict=True):
            if current.unit is not unit or _distance(current.end, following.start) > _TOLERANCE:
                raise CamInvariantError("Contour loop must be continuous and explicitly closed")

    def reversed(self) -> "ContourLoop":
        orientation = (ContourOrientation.CLOCKWISE if self.orientation is ContourOrientation.COUNTERCLOCKWISE
                       else ContourOrientation.COUNTERCLOCKWISE)
        return ContourLoop(tuple(segment.reversed() for segment in reversed(self.segments)), orientation)

    def to_dict(self) -> dict[str, Any]:
        return {"closed": self.closed, "orientation": self.orientation.value,
                "segments": [segment.to_dict() for segment in self.segments]}


@dataclass(frozen=True, slots=True)
class ContourBounds:
    minimum: Point3
    maximum: Point3

    def __post_init__(self) -> None:
        if not isinstance(self.minimum, Point3) or not isinstance(self.maximum, Point3):
            raise CamValidationError("Contour bounds are invalid")
        if self.minimum.unit is not self.maximum.unit or any(
            first > second for first, second in zip(
                (self.minimum.x, self.minimum.y, self.minimum.z),
                (self.maximum.x, self.maximum.y, self.maximum.z), strict=True)
        ):
            raise CamInvariantError("Contour bounds are inconsistent")


@dataclass(frozen=True, slots=True)
class ProfileProvenance:
    source_kind: ContourProfileSource
    occurrence_transform: OccurrenceTransformProvenance
    selector_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, ContourProfileSource) or not isinstance(
            self.occurrence_transform, OccurrenceTransformProvenance
        ):
            raise CamValidationError("Contour profile provenance is invalid")
        if type(self.selector_version) is not int or self.selector_version != 1:
            raise UnsupportedCamSchemaError("Unsupported Contour selector provenance version")


@dataclass(frozen=True, slots=True)
class ContourProfileDescriptor:
    """OCP-free resolved profile in model/world coordinates."""

    reference: GeometryReference
    plane_origin: Point3
    x_axis: Vector3
    y_axis: Vector3
    normal: Vector3
    outer_loop: ContourLoop
    inner_loops: tuple[ContourLoop, ...]
    bounds: ContourBounds
    unit: LengthUnit
    geometry_fingerprint: GeometryFingerprint
    provenance: ProfileProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.reference, GeometryReference):
            raise CamValidationError("Contour descriptor reference is invalid")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Contour descriptor requires a known unit")
        if not isinstance(self.plane_origin, Point3) or self.plane_origin.unit is not self.unit:
            raise CamUnitError("Contour plane origin unit is invalid")
        axes = (self.x_axis, self.y_axis, self.normal)
        if any(not isinstance(axis, Vector3) or abs(axis.magnitude - 1.0) > 1.0e-9 for axis in axes):
            raise CamInvariantError("Contour plane basis must use unit vectors")
        if any(abs(first.dot(second)) > 1.0e-9 for first, second in (
            (self.x_axis, self.y_axis), (self.x_axis, self.normal), (self.y_axis, self.normal)
        )) or self.x_axis.cross(self.y_axis).dot(self.normal) < 1.0 - 1.0e-9:
            raise CamInvariantError("Contour plane basis must be orthonormal and right-handed")
        if not isinstance(self.outer_loop, ContourLoop) or not isinstance(self.inner_loops, tuple):
            raise CamValidationError("Contour loop payload is invalid")
        if any(not isinstance(loop, ContourLoop) for loop in self.inner_loops):
            raise CamValidationError("Contour inner loop is invalid")
        if not isinstance(self.bounds, ContourBounds) or self.bounds.minimum.unit is not self.unit:
            raise CamUnitError("Contour bounds unit is invalid")
        if any(segment.unit is not self.unit for loop in (self.outer_loop, *self.inner_loops) for segment in loop.segments):
            raise CamUnitError("Contour geometry must use the descriptor unit")
        if not isinstance(self.geometry_fingerprint, GeometryFingerprint) or not isinstance(self.provenance, ProfileProvenance):
            raise CamValidationError("Contour fingerprint or provenance is invalid")


@dataclass(frozen=True, slots=True)
class ResolvedContourProfile:
    status: GeometryResolutionStatus
    profile: ContourProfileDescriptor | None = None
    message: str | None = None
    diagnostic_code: DiagnosticCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, GeometryResolutionStatus):
            raise CamValidationError("Contour resolution status is invalid")
        if (self.status is GeometryResolutionStatus.RESOLVED) != (self.profile is not None):
            raise CamInvariantError("Only a resolved Contour result may carry a descriptor")
        if self.message is not None and (not isinstance(self.message, str) or not self.message.strip()):
            raise CamValidationError("Contour resolution message is invalid")
        if self.diagnostic_code is not None and not isinstance(self.diagnostic_code, DiagnosticCode):
            raise CamValidationError("Contour resolution diagnostic is invalid")


def _distance(first: Point3, second: Point3) -> float:
    return math.sqrt((first.x - second.x) ** 2 + (first.y - second.y) ** 2 + (first.z - second.z) ** 2)
