"""Pure-Python geometry and depth contracts for Pocket foundation v1."""

from __future__ import annotations

from dataclasses import dataclass
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
from hms_cadcam.cam.domain.operation import DiagnosticCode, ValidationDiagnostic
from hms_cadcam.cam.domain.revision import ContentFingerprint, GeometryFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import Length, LengthUnit

POCKET_STRATEGY_KEY = "pocket_geometry"
POCKET_STRATEGY_VERSION = 1
_DEPTH_FORMAT = "HMS_CAM_POCKET_DEPTH"
_GEOMETRY_INPUT_FORMAT = "HMS_CAM_POCKET_GEOMETRY_INPUT"
_STRATEGY_FORMAT = "HMS_CAM_POCKET_STRATEGY"
_FORMAT_VERSION = 1
_TOLERANCE = 1.0e-8


class PocketValidationError(CamValidationError):
    """Pocket model validation failed with a stable diagnostic code."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


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
    """Versioned Pocket geometry/depth aggregate without a clearing algorithm."""

    geometry: PocketGeometryInput
    depth: PocketDepthDefinition
    strategy_version: int = POCKET_STRATEGY_VERSION
    schema_version: int = _FORMAT_VERSION
    SERIALIZATION_VERSION: ClassVar[int] = _FORMAT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.geometry, PocketGeometryInput) or not isinstance(
                self.depth, PocketDepthDefinition):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket strategy geometry or depth is invalid")
        if self.geometry.unit is not self.depth.unit:
            raise PocketValidationError(DiagnosticCode.POCKET_UNIT_MISSING,
                                        "Pocket geometry and depth units must match")
        if (type(self.strategy_version) is not int or self.strategy_version != POCKET_STRATEGY_VERSION
                or type(self.schema_version) is not int or self.schema_version != _FORMAT_VERSION):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Unsupported Pocket strategy version")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _STRATEGY_FORMAT,
            "format_version": _FORMAT_VERSION,
            "strategy_key": POCKET_STRATEGY_KEY,
            "strategy_version": self.strategy_version,
            "schema_version": self.schema_version,
            "geometry": self.geometry.to_dict(),
            "depth": self.depth.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PocketStrategy":
        fields = {"format", "format_version", "strategy_key", "strategy_version",
                  "schema_version", "geometry", "depth"}
        if (not isinstance(data, dict) or set(data) != fields
                or data.get("format") != _STRATEGY_FORMAT
                or type(data.get("format_version")) is not int
                or data["format_version"] != _FORMAT_VERSION
                or data.get("strategy_key") != POCKET_STRATEGY_KEY):
            raise PocketValidationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket strategy payload is malformed")
        return cls(PocketGeometryInput.from_dict(data["geometry"]),
                   PocketDepthDefinition.from_dict(data["depth"]),
                   data["strategy_version"], data["schema_version"])


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
