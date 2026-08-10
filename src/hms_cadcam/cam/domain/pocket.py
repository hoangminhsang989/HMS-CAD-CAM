"""Pure-Python geometry and depth contracts for Pocket foundation v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from hms_cadcam.cam.domain.contour import (
    ContourBounds,
    ContourLoop,
    ContourOrientation,
    ProfileProvenance,
)
from hms_cadcam.cam.domain.errors import CamInvariantError, CamUnitError, CamValidationError
from hms_cadcam.cam.domain.geometry_reference import (
    GeometryReference,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionStatus,
)
from hms_cadcam.cam.domain.operation import DiagnosticCode, OperationParameterSet, ValidationDiagnostic
from hms_cadcam.cam.domain.revision import ContentFingerprint, GeometryFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, Length, LengthUnit, SpindleSpeed

POCKET_STRATEGY_KEY = "pocket_2_5d"
POCKET_STRATEGY_VERSION = 1
_DEPTH_FORMAT = "HMS_CAM_POCKET_DEPTH"
_GEOMETRY_INPUT_FORMAT = "HMS_CAM_POCKET_GEOMETRY_INPUT"
_STRATEGY_FORMAT = "HMS_CAM_POCKET_STRATEGY"
_FORMAT_VERSION = 1
_STRATEGY_FORMAT_VERSION = 2
_TOLERANCE = 1.0e-8


class PocketValidationError(CamValidationError):
    """Pocket model validation failed with a stable diagnostic code."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class PocketEntryPolicy(StrEnum):
    """Entry policies implemented by Pocket core v1."""

    VERTICAL_PLUNGE = "vertical_plunge"


class PocketCuttingDirection(StrEnum):
    """Cutter travel direction for a clockwise spindle viewed from +Z."""

    CLIMB = "climb"
    CONVENTIONAL = "conventional"


def _known_unit(unit: LengthUnit) -> None:
    if not isinstance(unit, LengthUnit) or unit is LengthUnit.UNKNOWN:
        raise PocketValidationError(DiagnosticCode.POCKET_UNIT_MISSING,
                                    "Pocket requires an explicit known length unit")


def _known_length(value: Length, unit: LengthUnit, name: str) -> None:
    if not isinstance(value, Length) or value.unit is not unit:
        raise PocketValidationError(DiagnosticCode.POCKET_UNIT_MISSING,
                                    f"{name} must use the Pocket unit")


@dataclass(frozen=True, slots=True)
class PocketDepthDefinition:
    """Absolute Setup-WCS Z limits with a non-negative floor allowance."""

    unit: LengthUnit
    top_z: Length
    bottom_z: Length
    allowance: Length
    SERIALIZATION_VERSION: ClassVar[int] = _FORMAT_VERSION

    def __post_init__(self) -> None:
        _known_unit(self.unit)
        _known_length(self.top_z, self.unit, "Pocket top Z")
        _known_length(self.bottom_z, self.unit, "Pocket bottom Z")
        _known_length(self.allowance, self.unit, "Pocket allowance")
        if self.allowance.value < 0.0:
            raise PocketValidationError(DiagnosticCode.POCKET_INVALID_DEPTH,
                                        "Pocket allowance must not be negative")
        if self.bottom_z.value >= self.top_z.value:
            raise PocketValidationError(DiagnosticCode.POCKET_INVALID_DEPTH,
                                        "Pocket bottom Z must be below top Z")
        if self.final_bottom_z.value >= self.top_z.value - _TOLERANCE:
            raise PocketValidationError(DiagnosticCode.POCKET_INVALID_DEPTH,
                                        "Pocket allowance must leave a positive cutting depth")

    @property
    def depth(self) -> Length:
        """Return the positive nominal depth from top Z to bottom Z."""
        return Length(self.top_z.value - self.bottom_z.value, self.unit)

    @property
    def final_bottom_z(self) -> Length:
        """Return the floor Z after leaving axial allowance."""
        return Length(self.bottom_z.value + self.allowance.value, self.unit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _DEPTH_FORMAT,
            "format_version": _FORMAT_VERSION,
            "unit": self.unit.value,
            "top_z": self.top_z.value,
            "bottom_z": self.bottom_z.value,
            "depth": self.depth.value,
            "allowance": self.allowance.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PocketDepthDefinition":
        fields = {"format", "format_version", "unit", "top_z", "bottom_z", "depth", "allowance"}
        if not isinstance(data, dict) or set(data) != fields or data.get("format") != _DEPTH_FORMAT:
            raise PocketValidationError(DiagnosticCode.POCKET_INVALID_DEPTH,
                                        "Pocket depth payload is malformed")
        if (type(data.get("format_version")) is not int
                or data["format_version"] != _FORMAT_VERSION):
            raise PocketValidationError(DiagnosticCode.POCKET_INVALID_DEPTH,
                                        "Unsupported Pocket depth format")
        try:
            unit = LengthUnit(data["unit"])
            value = cls(unit, Length(data["top_z"], unit), Length(data["bottom_z"], unit),
                        Length(data["allowance"], unit))
            supplied_depth = Length(data["depth"], unit)
        except PocketValidationError:
            raise
        except (TypeError, ValueError, CamUnitError) as error:
            raise PocketValidationError(DiagnosticCode.POCKET_INVALID_DEPTH,
                                        "Pocket depth payload is invalid") from error
        if abs(supplied_depth.value - value.depth.value) > _TOLERANCE:
            raise PocketValidationError(DiagnosticCode.POCKET_INVALID_DEPTH,
                                        "Pocket depth does not match top and bottom Z")
        return value


@dataclass(frozen=True, slots=True)
class PocketGeometryInput:
    """Persistent Pocket boundary input; no runtime CAD identity is retained."""

    reference: GeometryReference
    unit: LengthUnit
    SERIALIZATION_VERSION: ClassVar[int] = _FORMAT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.reference, GeometryReference):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_MISSING,
                                        "Pocket GeometryReference is missing")
        _known_unit(self.unit)
        if (self.reference.kind not in {GeometryReferenceKind.FACE,
                                        GeometryReferenceKind.SKETCH_OR_PROFILE}
                or self.reference.geometry_kind is not GeometryRepresentationKind.BREP):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket requires a persistent BREP FACE or profile reference")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _GEOMETRY_INPUT_FORMAT,
            "format_version": _FORMAT_VERSION,
            "unit": self.unit.value,
            "reference": self.reference.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PocketGeometryInput":
        fields = {"format", "format_version", "unit", "reference"}
        if (not isinstance(data, dict) or set(data) != fields
                or data.get("format") != _GEOMETRY_INPUT_FORMAT
                or type(data.get("format_version")) is not int
                or data["format_version"] != _FORMAT_VERSION):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket geometry input payload is malformed")
        try:
            return cls(GeometryReference.from_dict(data["reference"]), LengthUnit(data["unit"]))
        except PocketValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket geometry input payload is invalid") from error


@dataclass(frozen=True, slots=True)
class PocketBoundary:
    """One canonical closed LINE/ARC outer loop; islands are absent by construction."""

    outer_loop: ContourLoop
    unit: LengthUnit

    def __post_init__(self) -> None:
        _known_unit(self.unit)
        if not isinstance(self.outer_loop, ContourLoop) or not self.outer_loop.closed:
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket requires one closed outer loop")
        if self.outer_loop.orientation is not ContourOrientation.COUNTERCLOCKWISE:
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket outer loop must be canonical counterclockwise")
        if any(segment.unit is not self.unit for segment in self.outer_loop.segments):
            raise PocketValidationError(DiagnosticCode.POCKET_UNIT_MISSING,
                                        "Pocket boundary unit is inconsistent")

    @property
    def fingerprint(self) -> GeometryFingerprint:
        return GeometryFingerprint.from_payload({
            "format": "hms_pocket_boundary_v1",
            "unit": self.unit.value,
            "outer_loop": self.outer_loop.to_dict(),
        })


@dataclass(frozen=True, slots=True)
class PocketRegion:
    """OCP-free resolved Pocket region in model/world coordinates."""

    reference: GeometryReference
    boundary: PocketBoundary
    plane_origin: Point3
    x_axis: Vector3
    y_axis: Vector3
    normal: Vector3
    bounds: ContourBounds
    unit: LengthUnit
    source_fingerprint: GeometryFingerprint
    provenance: ProfileProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.reference, GeometryReference) or not isinstance(self.boundary, PocketBoundary):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket region reference or boundary is invalid")
        _known_unit(self.unit)
        if (self.boundary.unit is not self.unit or not isinstance(self.plane_origin, Point3)
                or self.plane_origin.unit is not self.unit):
            raise PocketValidationError(DiagnosticCode.POCKET_UNIT_MISSING,
                                        "Pocket region units are inconsistent")
        axes = (self.x_axis, self.y_axis, self.normal)
        if any(not isinstance(axis, Vector3) or abs(axis.magnitude - 1.0) > 1.0e-9 for axis in axes):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket plane basis must use unit vectors")
        if any(abs(first.dot(second)) > 1.0e-9 for first, second in (
            (self.x_axis, self.y_axis), (self.x_axis, self.normal), (self.y_axis, self.normal)
        )) or self.x_axis.cross(self.y_axis).dot(self.normal) < 1.0 - 1.0e-9:
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket plane basis must be orthonormal and right-handed")
        if not isinstance(self.bounds, ContourBounds) or self.bounds.minimum.unit is not self.unit:
            raise PocketValidationError(DiagnosticCode.POCKET_UNIT_MISSING,
                                        "Pocket bounds unit is inconsistent")
        if not isinstance(self.source_fingerprint, GeometryFingerprint) or not isinstance(
                self.provenance, ProfileProvenance):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket fingerprint or provenance is invalid")

    @property
    def fingerprint(self) -> GeometryFingerprint:
        reference = self.reference
        return GeometryFingerprint.from_payload({
            "format": "hms_pocket_region_v1",
            "reference_target": {
                "scheme": reference.scheme,
                "scheme_version": reference.scheme_version,
                "source_id": str(reference.source_id),
                "kind": reference.kind.value,
                "geometry_kind": reference.geometry_kind.value,
                "occurrence_path": reference.occurrence_path,
                "subshape_selector": reference.subshape_selector,
                "expected_source_revision": reference.expected_source_revision.to_dict(),
            },
            "source_fingerprint": self.source_fingerprint.to_dict(),
            "plane_origin": self.plane_origin.to_dict(),
            "basis": [axis.to_dict() for axis in (self.x_axis, self.y_axis, self.normal)],
            "boundary": self.boundary.fingerprint.to_dict(),
        })


@dataclass(frozen=True, slots=True)
class PocketStrategy:
    """Versioned parameters for deterministic Pocket offset clearing v1."""

    unit: LengthUnit
    geometry: PocketGeometryInput
    depth: PocketDepthDefinition
    stepover: Length
    stepdown: Length
    radial_stock_allowance: Length
    clearance_height: Length
    retract_height: Length
    cutting_feed_rate: FeedRate
    plunge_feed_rate: FeedRate
    spindle_speed: SpindleSpeed
    entry_policy: PocketEntryPolicy = PocketEntryPolicy.VERTICAL_PLUNGE
    cutting_direction: PocketCuttingDirection = PocketCuttingDirection.CLIMB
    tolerance: Length | None = None
    strategy_version: int = POCKET_STRATEGY_VERSION
    schema_version: int = _FORMAT_VERSION
    SERIALIZATION_VERSION: ClassVar[int] = _STRATEGY_FORMAT_VERSION

    def __post_init__(self) -> None:
        _known_unit(self.unit)
        if not isinstance(self.geometry, PocketGeometryInput) or not isinstance(
                self.depth, PocketDepthDefinition):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket strategy geometry or depth is invalid")
        if self.geometry.unit is not self.unit or self.depth.unit is not self.unit:
            raise PocketValidationError(DiagnosticCode.POCKET_UNIT_MISSING,
                                        "Pocket geometry and depth units must match")
        _known_length(self.stepover, self.unit, "Pocket stepover")
        if self.stepover.value <= 0.0:
            raise PocketValidationError(DiagnosticCode.POCKET_INVALID_STEPOVER,
                                        "Pocket stepover must be positive")
        _known_length(self.stepdown, self.unit, "Pocket stepdown")
        if self.stepdown.value <= 0.0:
            raise PocketValidationError(DiagnosticCode.POCKET_INVALID_STEPDOWN,
                                        "Pocket stepdown must be positive")
        _known_length(self.radial_stock_allowance, self.unit, "Pocket radial stock allowance")
        if self.radial_stock_allowance.value < 0.0:
            raise PocketValidationError(DiagnosticCode.POCKET_OFFSET_FAILED,
                                        "Pocket radial stock allowance must not be negative")
        for value, name in ((self.clearance_height, "Pocket clearance height"),
                            (self.retract_height, "Pocket retract height")):
            _known_length(value, self.unit, name)
        if (self.retract_height.value <= self.depth.top_z.value
                or self.clearance_height.value < self.retract_height.value):
            raise PocketValidationError(DiagnosticCode.POCKET_ENTRY_UNSAFE,
                                        "Pocket retract must be above top Z and clearance at or above retract")
        if not isinstance(self.entry_policy, PocketEntryPolicy) or not isinstance(
                self.cutting_direction, PocketCuttingDirection):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket entry policy or cutting direction is invalid")
        tolerance = self.tolerance if self.tolerance is not None else Length(_TOLERANCE, self.unit)
        _known_length(tolerance, self.unit, "Pocket tolerance")
        if tolerance.value <= 0.0:
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket tolerance must be positive")
        object.__setattr__(self, "tolerance", tolerance)
        expected_feed = (FeedUnit.MM_PER_MINUTE if self.unit is LengthUnit.MM
                         else FeedUnit.INCH_PER_MINUTE)
        if (not isinstance(self.cutting_feed_rate, FeedRate)
                or not isinstance(self.plunge_feed_rate, FeedRate)
                or self.cutting_feed_rate.unit is not expected_feed
                or self.plunge_feed_rate.unit is not expected_feed):
            raise PocketValidationError(DiagnosticCode.POCKET_UNIT_MISSING,
                                        "Pocket feeds must match the strategy length unit")
        if not isinstance(self.spindle_speed, SpindleSpeed):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket spindle speed is invalid")
        if (type(self.strategy_version) is not int or self.strategy_version != POCKET_STRATEGY_VERSION
                or type(self.schema_version) is not int or self.schema_version != _FORMAT_VERSION):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Unsupported Pocket strategy version")

    @property
    def top_z(self) -> Length:
        return self.depth.top_z

    @property
    def bottom_z(self) -> Length:
        return self.depth.bottom_z

    @property
    def final_depth(self) -> Length:
        return self.depth.final_bottom_z

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_operation_parameters(self) -> OperationParameterSet:
        values = (
            ("unit", self.unit.value),
            ("top_z", self.top_z.value),
            ("bottom_z", self.bottom_z.value),
            ("axial_allowance", self.depth.allowance.value),
            ("stepover", self.stepover.value),
            ("stepdown", self.stepdown.value),
            ("radial_stock_allowance", self.radial_stock_allowance.value),
            ("clearance_height", self.clearance_height.value),
            ("retract_height", self.retract_height.value),
            ("cutting_feed_rate", self.cutting_feed_rate.value),
            ("plunge_feed_rate", self.plunge_feed_rate.value),
            ("spindle_speed", self.spindle_speed.value),
            ("entry_policy", self.entry_policy.value),
            ("cutting_direction", self.cutting_direction.value),
            ("tolerance", self.tolerance.value),
        )
        return OperationParameterSet(POCKET_STRATEGY_KEY, POCKET_STRATEGY_VERSION, values)

    @classmethod
    def from_operation_parameters(
        cls,
        value: OperationParameterSet,
        reference: GeometryReference,
    ) -> "PocketStrategy":
        if value.strategy_key != POCKET_STRATEGY_KEY:
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Operation is not a Pocket strategy")
        data = dict(value.values)
        # Stage17A metadata is additive; legacy Pocket numeric semantics stay V1.
        data.pop("automatic_parameter_contract", None)
        fields = {"unit", "top_z", "bottom_z", "axial_allowance", "stepover", "stepdown",
                  "radial_stock_allowance", "clearance_height", "retract_height",
                  "cutting_feed_rate", "plunge_feed_rate", "spindle_speed", "entry_policy",
                  "cutting_direction", "tolerance"}
        if set(data) != fields:
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket operation parameters are malformed")
        try:
            unit = LengthUnit(data["unit"])
            feed_unit = (FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM
                         else FeedUnit.INCH_PER_MINUTE)
            return cls(
                unit,
                PocketGeometryInput(reference, unit),
                PocketDepthDefinition(unit, Length(data["top_z"], unit),
                                      Length(data["bottom_z"], unit),
                                      Length(data["axial_allowance"], unit)),
                Length(data["stepover"], unit),
                Length(data["stepdown"], unit),
                Length(data["radial_stock_allowance"], unit),
                Length(data["clearance_height"], unit),
                Length(data["retract_height"], unit),
                FeedRate(data["cutting_feed_rate"], feed_unit),
                FeedRate(data["plunge_feed_rate"], feed_unit),
                SpindleSpeed(data["spindle_speed"]),
                PocketEntryPolicy(data["entry_policy"]),
                PocketCuttingDirection(data["cutting_direction"]),
                Length(data["tolerance"], unit),
                value.strategy_version,
                value.schema_version,
            )
        except PocketValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket operation parameters are invalid") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _STRATEGY_FORMAT,
            "format_version": _STRATEGY_FORMAT_VERSION,
            "strategy_key": POCKET_STRATEGY_KEY,
            "strategy_version": self.strategy_version,
            "schema_version": self.schema_version,
            "geometry": self.geometry.to_dict(),
            "depth": self.depth.to_dict(),
            "stepover": self.stepover.value,
            "stepdown": self.stepdown.value,
            "radial_stock_allowance": self.radial_stock_allowance.value,
            "clearance_height": self.clearance_height.value,
            "retract_height": self.retract_height.value,
            "cutting_feed_rate": self.cutting_feed_rate.value,
            "plunge_feed_rate": self.plunge_feed_rate.value,
            "spindle_speed": self.spindle_speed.value,
            "entry_policy": self.entry_policy.value,
            "cutting_direction": self.cutting_direction.value,
            "tolerance": self.tolerance.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PocketStrategy":
        fields = {"format", "format_version", "strategy_key", "strategy_version",
                  "schema_version", "geometry", "depth", "stepover", "stepdown",
                  "radial_stock_allowance", "clearance_height", "retract_height",
                  "cutting_feed_rate", "plunge_feed_rate", "spindle_speed", "entry_policy",
                  "cutting_direction", "tolerance"}
        if (not isinstance(data, dict) or set(data) != fields
                or data.get("format") != _STRATEGY_FORMAT
                or type(data.get("format_version")) is not int
                or data["format_version"] != _STRATEGY_FORMAT_VERSION
                or data.get("strategy_key") != POCKET_STRATEGY_KEY):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket strategy payload is malformed")
        geometry = PocketGeometryInput.from_dict(data["geometry"])
        depth = PocketDepthDefinition.from_dict(data["depth"])
        unit = geometry.unit
        try:
            feed_unit = (FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM
                         else FeedUnit.INCH_PER_MINUTE)
            return cls(
                unit, geometry, depth, Length(data["stepover"], unit),
                Length(data["stepdown"], unit), Length(data["radial_stock_allowance"], unit),
                Length(data["clearance_height"], unit), Length(data["retract_height"], unit),
                FeedRate(data["cutting_feed_rate"], feed_unit),
                FeedRate(data["plunge_feed_rate"], feed_unit), SpindleSpeed(data["spindle_speed"]),
                PocketEntryPolicy(data["entry_policy"]),
                PocketCuttingDirection(data["cutting_direction"]), Length(data["tolerance"], unit),
                data["strategy_version"], data["schema_version"],
            )
        except PocketValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket strategy payload is invalid") from error


@dataclass(frozen=True, slots=True)
class ResolvedPocketGeometry:
    """Fail-closed Pocket resolution result containing only native-free values."""

    status: GeometryResolutionStatus
    region: PocketRegion | None = None
    diagnostics: tuple[ValidationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, GeometryResolutionStatus):
            raise CamValidationError("Pocket resolution status is invalid")
        if (self.status is GeometryResolutionStatus.RESOLVED) != (self.region is not None):
            raise CamInvariantError("Only resolved Pocket geometry may carry a region")
        if not isinstance(self.diagnostics, tuple) or any(
                not isinstance(value, ValidationDiagnostic) for value in self.diagnostics):
            raise CamValidationError("Pocket resolution diagnostics are invalid")
        if self.status is not GeometryResolutionStatus.RESOLVED and not self.diagnostics:
            raise CamInvariantError("Failed Pocket resolution requires a diagnostic")
