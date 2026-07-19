"""Pure-Python drilling geometry, pattern, depth, and resolution contracts."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.facing import OccurrenceTransformProvenance
from hms_cadcam.cam.domain.geometry_reference import (
    GeometryReference,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionStatus,
)
from hms_cadcam.cam.domain.operation import (
    DiagnosticCode,
    OperationParameterSet,
    ValidationDiagnostic,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint, GeometryFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import (
    FeedRate,
    FeedUnit,
    Length,
    LengthUnit,
    SpindleSpeed,
)

_VERSION = 1
_TOLERANCE = 1.0e-8
_DEPTH_FORMAT = "HMS_CAM_DRILL_DEPTH"
_REFERENCE_FORMAT = "HMS_CAM_HOLE_REFERENCE"
_LOCATION_FORMAT = "HMS_CAM_HOLE_LOCATION"
_PATTERN_FORMAT = "HMS_CAM_HOLE_PATTERN"
_INPUT_FORMAT = "HMS_CAM_DRILL_GEOMETRY_INPUT"
_REGION_FORMAT = "HMS_CAM_DRILLING_REGION"
_STRATEGY_FORMAT = "HMS_CAM_DRILLING_STRATEGY"
DRILLING_STRATEGY_KEY = "drilling_v1"
DRILLING_STRATEGY_VERSION = 1
_GEOMETRY_CHUNK_SIZE = 4000


class DrillValidationError(CamValidationError):
    """Drilling geometry validation failed with one stable diagnostic code."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class HoleSourceKind(StrEnum):
    """Geometry sources supported by drilling foundation v1."""

    EXPLICIT_POINT = "explicit_point"
    BREP_VERTEX = "brep_vertex"
    CIRCULAR_EDGE = "circular_edge"


class DrillingCycle(StrEnum):
    """Controller-neutral drilling process semantics."""

    SPOT_DRILL = "spot_drill"
    DRILL = "drill"
    PECK_DRILL = "peck_drill"


class DrillRetractPolicy(StrEnum):
    """Safe Z level reached between pecks."""

    RETRACT_HEIGHT = "retract_height"
    CLEARANCE_HEIGHT = "clearance_height"


class DrillApproachPolicy(StrEnum):
    """Approach policies supported by drilling core v1."""

    RAPID_CLEARANCE_FEED_RETRACT = "rapid_clearance_feed_retract"


def _known_unit(unit: LengthUnit) -> None:
    if not isinstance(unit, LengthUnit) or unit is LengthUnit.UNKNOWN:
        raise DrillValidationError(
            DiagnosticCode.DRILL_UNIT_MISSING,
            "Drilling geometry requires an explicit known length unit",
        )


def _unit_axis(axis: Vector3) -> None:
    if not isinstance(axis, Vector3) or abs(axis.magnitude - 1.0) > _TOLERANCE:
        raise DrillValidationError(
            DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
            "Hole axis must be a finite unit vector",
        )


def _point_in_unit(point: Point3, unit: LengthUnit, name: str) -> None:
    if not isinstance(point, Point3) or point.unit is not unit:
        raise DrillValidationError(
            DiagnosticCode.DRILL_UNIT_MISSING,
            f"{name} must use the drilling geometry unit",
        )


def _payload(data: Any, format_name: str, fields: set[str]) -> None:
    if not isinstance(data, dict) or set(data) != {
        "format", "format_version", *fields,
    }:
        raise DrillValidationError(
            DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
            f"{format_name} payload is malformed",
        )
    if data["format"] != format_name or type(data["format_version"]) is not int:
        raise DrillValidationError(
            DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
            f"Unsupported {format_name} payload",
        )
    if data["format_version"] != _VERSION:
        raise DrillValidationError(
            DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
            f"Unsupported {format_name} version",
        )


def _provenance_dict(value: OccurrenceTransformProvenance) -> dict[str, Any]:
    return {
        "occurrence_path": value.occurrence_path,
        "absolute_transform": list(value.absolute_transform),
        "source_normal_reversed": value.source_normal_reversed,
    }


def _provenance_from_dict(data: Any) -> OccurrenceTransformProvenance:
    if not isinstance(data, dict) or set(data) != {
        "occurrence_path", "absolute_transform", "source_normal_reversed",
    } or not isinstance(data["absolute_transform"], list):
        raise DrillValidationError(
            DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
            "Hole provenance payload is malformed",
        )
    try:
        return OccurrenceTransformProvenance(
            data["occurrence_path"],
            tuple(data["absolute_transform"]),
            data["source_normal_reversed"],
        )
    except (TypeError, ValueError, CamValidationError) as error:
        raise DrillValidationError(
            DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
            "Hole provenance payload is invalid",
        ) from error


@dataclass(frozen=True, slots=True)
class DrillDepthDefinition:
    """Absolute top/bottom Z convention with one positive drilling depth."""

    unit: LengthUnit
    top_z: Length
    bottom_z: Length
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        _known_unit(self.unit)
        if any(
            not isinstance(value, Length) or value.unit is not self.unit
            for value in (self.top_z, self.bottom_z)
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNIT_MISSING,
                "Drill top and bottom Z must use the drilling unit",
            )
        if self.bottom_z.value >= self.top_z.value - _TOLERANCE:
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_DEPTH,
                "Drill bottom Z must be below top Z",
            )

    @property
    def depth(self) -> Length:
        return Length(self.top_z.value - self.bottom_z.value, self.unit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _DEPTH_FORMAT,
            "format_version": _VERSION,
            "unit": self.unit.value,
            "top_z": self.top_z.value,
            "bottom_z": self.bottom_z.value,
            "depth": self.depth.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DrillDepthDefinition":
        try:
            _payload(data, _DEPTH_FORMAT, {"unit", "top_z", "bottom_z", "depth"})
        except DrillValidationError as error:
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_DEPTH,
                "Drill depth payload is malformed or unsupported",
            ) from error
        try:
            unit = LengthUnit(data["unit"])
            result = cls(
                unit,
                Length(data["top_z"], unit),
                Length(data["bottom_z"], unit),
            )
            supplied = Length(data["depth"], unit)
        except DrillValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_DEPTH,
                "Drill depth payload is invalid",
            ) from error
        if abs(supplied.value - result.depth.value) > _TOLERANCE:
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_DEPTH,
                "Drill depth does not match top and bottom Z",
            )
        return result


@dataclass(frozen=True, slots=True)
class HoleReference:
    """Persistent hole target plus explicitly bound drilling plane and axis."""

    reference: GeometryReference
    axis: Vector3
    plane_origin: Point3
    unit: LengthUnit
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.reference, GeometryReference):
            raise DrillValidationError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "Hole GeometryReference is missing",
            )
        _known_unit(self.unit)
        if (
            self.reference.geometry_kind is not GeometryRepresentationKind.BREP
            or self.reference.kind not in {
                GeometryReferenceKind.VERTEX,
                GeometryReferenceKind.EDGE,
            }
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "HoleReference requires one persistent BREP VERTEX or EDGE",
            )
        _unit_axis(self.axis)
        _point_in_unit(self.plane_origin, self.unit, "Hole reference plane origin")

    @property
    def fingerprint(self) -> ContentFingerprint:
        reference = self.reference
        return ContentFingerprint.from_payload({
            "format": "hms_hole_reference_fingerprint_v1",
            "target": {
                "scheme": reference.scheme,
                "scheme_version": reference.scheme_version,
                "source_id": str(reference.source_id),
                "kind": reference.kind.value,
                "geometry_kind": reference.geometry_kind.value,
                "occurrence_path": reference.occurrence_path,
                "subshape_selector": reference.subshape_selector,
                "expected_geometry_fingerprint": (
                    reference.expected_geometry_fingerprint.to_dict()
                ),
                "expected_source_revision": reference.expected_source_revision.to_dict(),
            },
            "axis": self.axis.to_dict(),
            "plane_origin": self.plane_origin.to_dict(),
            "unit": self.unit.value,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _REFERENCE_FORMAT,
            "format_version": _VERSION,
            "reference": self.reference.to_dict(),
            "axis": self.axis.to_dict(),
            "plane_origin": self.plane_origin.to_dict(),
            "unit": self.unit.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HoleReference":
        try:
            _payload(data, _REFERENCE_FORMAT, {"reference", "axis", "plane_origin", "unit"})
            return cls(
                GeometryReference.from_dict(data["reference"]),
                Vector3.from_dict(data["axis"]),
                Point3.from_dict(data["plane_origin"]),
                LengthUnit(data["unit"]),
            )
        except DrillValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "HoleReference payload is invalid",
            ) from error


@dataclass(frozen=True, slots=True)
class HoleLocation:
    """One normalized native-free drilling location."""

    position: Point3
    axis: Vector3
    plane_origin: Point3
    diameter: Length | None
    unit: LengthUnit
    source_kind: HoleSourceKind = HoleSourceKind.EXPLICIT_POINT
    reference: HoleReference | None = None
    provenance: OccurrenceTransformProvenance = OccurrenceTransformProvenance(
        None,
        (1.0, 0.0, 0.0, 0.0,
         0.0, 1.0, 0.0, 0.0,
         0.0, 0.0, 1.0, 0.0,
         0.0, 0.0, 0.0, 1.0),
    )
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        _known_unit(self.unit)
        _point_in_unit(self.position, self.unit, "Hole position")
        _point_in_unit(self.plane_origin, self.unit, "Hole plane origin")
        _unit_axis(self.axis)
        if not isinstance(self.source_kind, HoleSourceKind):
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "Hole source kind is invalid",
            )
        if not isinstance(self.provenance, OccurrenceTransformProvenance):
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "Hole provenance is invalid",
            )
        distance = (
            (self.position.x - self.plane_origin.x) * self.axis.x
            + (self.position.y - self.plane_origin.y) * self.axis.y
            + (self.position.z - self.plane_origin.z) * self.axis.z
        )
        if abs(distance) > _TOLERANCE:
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "Hole position must lie on its declared plane",
            )
        if self.diameter is not None and (
            not isinstance(self.diameter, Length)
            or self.diameter.unit is not self.unit
            or self.diameter.value <= 0.0
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNIT_MISSING,
                "Hole diameter must be positive in the drilling unit",
            )
        requires_reference = self.source_kind is not HoleSourceKind.EXPLICIT_POINT
        if requires_reference != (self.reference is not None):
            raise DrillValidationError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "BREP hole locations require exactly one HoleReference",
            )
        if self.reference is not None:
            if self.reference.unit is not self.unit:
                raise DrillValidationError(
                    DiagnosticCode.DRILL_UNIT_MISSING,
                    "Hole location and reference units do not match",
                )
            if self.reference.reference.kind is GeometryReferenceKind.VERTEX:
                expected = HoleSourceKind.BREP_VERTEX
            else:
                expected = HoleSourceKind.CIRCULAR_EDGE
            if self.source_kind is not expected:
                raise DrillValidationError(
                    DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                    "Hole source kind does not match its GeometryReference",
                )
            if (
                self.axis.dot(self.reference.axis) < 1.0 - _TOLERANCE
                or math.dist(
                    (self.plane_origin.x, self.plane_origin.y, self.plane_origin.z),
                    (
                        self.reference.plane_origin.x,
                        self.reference.plane_origin.y,
                        self.reference.plane_origin.z,
                    ),
                ) > _TOLERANCE
            ):
                raise DrillValidationError(
                    DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                    "Hole location does not match its bound plane and axis",
                )

    @property
    def fingerprint(self) -> GeometryFingerprint:
        return GeometryFingerprint.from_payload({
            "format": "hms_hole_location_v1",
            "position": self.position.to_dict(),
            "axis": self.axis.to_dict(),
            "plane_origin": self.plane_origin.to_dict(),
            "diameter": None if self.diameter is None else self.diameter.value,
            "unit": self.unit.value,
            "source_kind": self.source_kind.value,
            "reference": None if self.reference is None else self.reference.fingerprint.to_dict(),
            "provenance": _provenance_dict(self.provenance),
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _LOCATION_FORMAT,
            "format_version": _VERSION,
            "position": self.position.to_dict(),
            "axis": self.axis.to_dict(),
            "plane_origin": self.plane_origin.to_dict(),
            "diameter": None if self.diameter is None else self.diameter.value,
            "unit": self.unit.value,
            "source_kind": self.source_kind.value,
            "reference": None if self.reference is None else self.reference.to_dict(),
            "provenance": _provenance_dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HoleLocation":
        try:
            _payload(data, _LOCATION_FORMAT, {
                "position", "axis", "plane_origin", "diameter", "unit",
                "source_kind", "reference", "provenance",
            })
            unit = LengthUnit(data["unit"])
            reference = (
                None if data["reference"] is None
                else HoleReference.from_dict(data["reference"])
            )
            return cls(
                Point3.from_dict(data["position"]),
                Vector3.from_dict(data["axis"]),
                Point3.from_dict(data["plane_origin"]),
                None if data["diameter"] is None else Length(data["diameter"], unit),
                unit,
                HoleSourceKind(data["source_kind"]),
                reference,
                _provenance_from_dict(data["provenance"]),
            )
        except DrillValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "Hole location payload is invalid",
            ) from error


@dataclass(frozen=True, slots=True)
class HolePattern:
    """Canonical explicit list of coplanar, unique hole locations."""

    locations: tuple[HoleLocation, ...]
    unit: LengthUnit
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        _known_unit(self.unit)
        if (
            not isinstance(self.locations, tuple)
            or not self.locations
            or any(not isinstance(value, HoleLocation) for value in self.locations)
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "HolePattern requires at least one explicit location",
            )
        if any(value.unit is not self.unit for value in self.locations):
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNIT_MISSING,
                "HolePattern locations must use one unit",
            )
        canonical = tuple(sorted(self.locations, key=_location_key))
        if _has_duplicate_locations(canonical):
            raise DrillValidationError(
                DiagnosticCode.DRILL_DUPLICATE_LOCATION,
                "HolePattern contains duplicate locations",
            )
        first = canonical[0]
        for location in canonical[1:]:
            if first.axis.dot(location.axis) < 1.0 - _TOLERANCE:
                raise DrillValidationError(
                    DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                    "HolePattern axes must use one direction",
                )
            delta = Vector3(
                location.position.x - first.plane_origin.x,
                location.position.y - first.plane_origin.y,
                location.position.z - first.plane_origin.z,
            )
            if abs(delta.dot(first.axis)) > _TOLERANCE:
                raise DrillValidationError(
                    DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                    "HolePattern locations must be coplanar",
                )
        object.__setattr__(self, "locations", canonical)

    @property
    def fingerprint(self) -> GeometryFingerprint:
        return GeometryFingerprint.from_payload({
            "format": "hms_hole_pattern_v1",
            "unit": self.unit.value,
            "locations": [value.fingerprint.to_dict() for value in self.locations],
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _PATTERN_FORMAT,
            "format_version": _VERSION,
            "unit": self.unit.value,
            "locations": [value.to_dict() for value in self.locations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HolePattern":
        try:
            _payload(data, _PATTERN_FORMAT, {"unit", "locations"})
            if not isinstance(data["locations"], list):
                raise TypeError("locations must be a list")
            return cls(
                tuple(HoleLocation.from_dict(value) for value in data["locations"]),
                LengthUnit(data["unit"]),
            )
        except DrillValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "HolePattern payload is invalid",
            ) from error


@dataclass(frozen=True, slots=True)
class DrillGeometryInput:
    """Exactly one persistent hole reference or explicit point pattern."""

    source: HoleReference | HolePattern
    unit: LengthUnit
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        _known_unit(self.unit)
        if not isinstance(self.source, (HoleReference, HolePattern)):
            raise DrillValidationError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "DrillGeometryInput requires one reference or explicit pattern",
            )
        if self.source.unit is not self.unit:
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNIT_MISSING,
                "Drill geometry input unit does not match its source",
            )

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload({
            "format": "hms_drill_geometry_input_v1",
            "unit": self.unit.value,
            "source_type": "reference" if isinstance(self.source, HoleReference) else "pattern",
            "source_fingerprint": self.source.fingerprint.to_dict(),
        })

    def to_dict(self) -> dict[str, Any]:
        source_type = "reference" if isinstance(self.source, HoleReference) else "pattern"
        return {
            "format": _INPUT_FORMAT,
            "format_version": _VERSION,
            "unit": self.unit.value,
            "source_type": source_type,
            "source": self.source.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DrillGeometryInput":
        try:
            _payload(data, _INPUT_FORMAT, {"unit", "source_type", "source"})
            if data["source_type"] == "reference":
                source = HoleReference.from_dict(data["source"])
            elif data["source_type"] == "pattern":
                source = HolePattern.from_dict(data["source"])
            else:
                raise ValueError("unknown drilling source")
            return cls(source, LengthUnit(data["unit"]))
        except DrillValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "DrillGeometryInput payload is invalid",
            ) from error


@dataclass(frozen=True, slots=True)
class DrillingRegion:
    """Resolved coplanar drilling locations and validated depth."""

    geometry_input: DrillGeometryInput
    pattern: HolePattern
    depth: DrillDepthDefinition
    unit: LengthUnit
    source_fingerprint: GeometryFingerprint
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        _known_unit(self.unit)
        if not isinstance(self.geometry_input, DrillGeometryInput):
            raise DrillValidationError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "DrillingRegion geometry input is missing",
            )
        if not isinstance(self.pattern, HolePattern) or not isinstance(
            self.depth, DrillDepthDefinition
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "DrillingRegion pattern or depth is invalid",
            )
        if any(value.unit is not self.unit for value in (
            self.geometry_input, self.pattern, self.depth,
        )):
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNIT_MISSING,
                "DrillingRegion values must use one unit",
            )
        if not isinstance(self.source_fingerprint, GeometryFingerprint):
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "DrillingRegion source fingerprint is invalid",
            )
        source = self.geometry_input.source
        if isinstance(source, HolePattern):
            matches_input = self.pattern == source
        else:
            matches_input = (
                len(self.pattern.locations) == 1
                and self.pattern.locations[0].reference == source
            )
        if not matches_input:
            raise DrillValidationError(
                DiagnosticCode.DRILL_SOURCE_MISMATCH,
                "DrillingRegion pattern does not match its geometry input",
            )

    @property
    def fingerprint(self) -> GeometryFingerprint:
        return GeometryFingerprint.from_payload({
            "format": "hms_drilling_region_v1",
            "geometry_input": self.geometry_input.fingerprint.to_dict(),
            "pattern": self.pattern.fingerprint.to_dict(),
            "depth": self.depth.to_dict(),
            "unit": self.unit.value,
            "source_fingerprint": self.source_fingerprint.to_dict(),
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _REGION_FORMAT,
            "format_version": _VERSION,
            "geometry_input": self.geometry_input.to_dict(),
            "pattern": self.pattern.to_dict(),
            "depth": self.depth.to_dict(),
            "unit": self.unit.value,
            "source_fingerprint": self.source_fingerprint.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DrillingRegion":
        try:
            _payload(data, _REGION_FORMAT, {
                "geometry_input", "pattern", "depth", "unit", "source_fingerprint",
            })
            return cls(
                DrillGeometryInput.from_dict(data["geometry_input"]),
                HolePattern.from_dict(data["pattern"]),
                DrillDepthDefinition.from_dict(data["depth"]),
                LengthUnit(data["unit"]),
                GeometryFingerprint.from_dict(data["source_fingerprint"]),
            )
        except DrillValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "DrillingRegion payload is invalid",
            ) from error


@dataclass(frozen=True, slots=True)
class DrillingStrategy:
    """Versioned, controller-neutral drilling process definition v1."""

    unit: LengthUnit
    geometry: DrillGeometryInput
    depth: DrillDepthDefinition
    cycle: DrillingCycle
    clearance_height: Length
    retract_height: Length
    feed_rate: FeedRate
    spindle_speed: SpindleSpeed
    dwell_seconds: float = 0.0
    peck_depth: Length | None = None
    retract_policy: DrillRetractPolicy = DrillRetractPolicy.RETRACT_HEIGHT
    approach_policy: DrillApproachPolicy = (
        DrillApproachPolicy.RAPID_CLEARANCE_FEED_RETRACT
    )
    tolerance: Length | None = None
    strategy_version: int = DRILLING_STRATEGY_VERSION
    schema_version: int = _VERSION
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        _known_unit(self.unit)
        if not isinstance(self.geometry, DrillGeometryInput):
            raise DrillValidationError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "Drilling strategy geometry is missing",
            )
        if not isinstance(self.depth, DrillDepthDefinition):
            raise DrillValidationError(
                DiagnosticCode.DRILL_DEPTH_INVALID,
                "Drilling strategy depth is invalid",
            )
        if self.geometry.unit is not self.unit or self.depth.unit is not self.unit:
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNIT_MISSING,
                "Drilling geometry and depth must use the strategy unit",
            )
        if not isinstance(self.cycle, DrillingCycle):
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling cycle is invalid",
            )
        for value, name in (
            (self.clearance_height, "clearance height"),
            (self.retract_height, "retract height"),
        ):
            if not isinstance(value, Length) or value.unit is not self.unit:
                raise DrillValidationError(
                    DiagnosticCode.DRILL_UNIT_MISSING,
                    f"Drilling {name} must use the strategy unit",
                )
        if (
            self.retract_height.value <= self.depth.top_z.value
            or self.clearance_height.value <= self.retract_height.value
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling retract must be above top Z and clearance above retract",
            )
        expected_feed = (
            FeedUnit.MM_PER_MINUTE
            if self.unit is LengthUnit.MM
            else FeedUnit.INCH_PER_MINUTE
        )
        if not isinstance(self.feed_rate, FeedRate) or self.feed_rate.unit is not expected_feed:
            raise DrillValidationError(
                DiagnosticCode.DRILL_UNIT_MISSING,
                "Drilling feed must match the strategy length unit",
            )
        if not isinstance(self.spindle_speed, SpindleSpeed):
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling spindle speed is invalid",
            )
        if isinstance(self.dwell_seconds, bool) or not isinstance(
            self.dwell_seconds, (int, float)
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling dwell must be finite and non-negative",
            )
        dwell = float(self.dwell_seconds)
        if not math.isfinite(dwell) or dwell < 0.0:
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling dwell must be finite and non-negative",
            )
        object.__setattr__(self, "dwell_seconds", dwell)
        if not isinstance(self.retract_policy, DrillRetractPolicy) or not isinstance(
            self.approach_policy, DrillApproachPolicy
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling retract or approach policy is invalid",
            )
        if self.cycle is DrillingCycle.PECK_DRILL:
            if (
                not isinstance(self.peck_depth, Length)
                or self.peck_depth.unit is not self.unit
                or self.peck_depth.value <= 0.0
                or self.peck_depth.value >= self.depth.depth.value
            ):
                raise DrillValidationError(
                    DiagnosticCode.DRILL_INVALID_PECK,
                    "Peck depth must be positive and smaller than total drilling depth",
                )
        elif self.peck_depth is not None:
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PECK,
                "Peck depth is only valid for PECK_DRILL",
            )
        tolerance = self.tolerance or Length(_TOLERANCE, self.unit)
        if (
            not isinstance(tolerance, Length)
            or tolerance.unit is not self.unit
            or tolerance.value <= 0.0
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling tolerance must be positive in the strategy unit",
            )
        object.__setattr__(self, "tolerance", tolerance)
        if (
            type(self.strategy_version) is not int
            or self.strategy_version != DRILLING_STRATEGY_VERSION
            or type(self.schema_version) is not int
            or self.schema_version != _VERSION
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Unsupported drilling strategy version",
            )

    @property
    def top_z(self) -> Length:
        return self.depth.top_z

    @property
    def final_depth(self) -> Length:
        return self.depth.bottom_z

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_operation_parameters(self) -> OperationParameterSet:
        geometry_json = json.dumps(
            self.geometry.to_dict(), sort_keys=True, separators=(",", ":")
        )
        chunks = tuple(
            geometry_json[index:index + _GEOMETRY_CHUNK_SIZE]
            for index in range(0, len(geometry_json), _GEOMETRY_CHUNK_SIZE)
        )
        values: list[tuple[str, object]] = [
            ("unit", self.unit.value),
            ("cycle", self.cycle.value),
            ("top_z", self.top_z.value),
            ("final_depth", self.final_depth.value),
            ("clearance_height", self.clearance_height.value),
            ("retract_height", self.retract_height.value),
            ("feed_rate", self.feed_rate.value),
            ("spindle_speed", self.spindle_speed.value),
            ("dwell_seconds", self.dwell_seconds),
            ("peck_depth", None if self.peck_depth is None else self.peck_depth.value),
            ("retract_policy", self.retract_policy.value),
            ("approach_policy", self.approach_policy.value),
            ("tolerance", self.tolerance.value),
            ("geometry_chunk_count", len(chunks)),
        ]
        values.extend((f"geometry_{index:04d}", chunk) for index, chunk in enumerate(chunks))
        return OperationParameterSet(
            DRILLING_STRATEGY_KEY,
            DRILLING_STRATEGY_VERSION,
            tuple(values),
        )

    @classmethod
    def from_operation_parameters(cls, value: OperationParameterSet) -> "DrillingStrategy":
        if value.strategy_key != DRILLING_STRATEGY_KEY:
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Operation is not a drilling strategy",
            )
        data = dict(value.values)
        try:
            chunk_count = data.pop("geometry_chunk_count")
            if type(chunk_count) is not int or not 1 <= chunk_count <= 1024:
                raise ValueError("invalid geometry chunk count")
            chunk_keys = tuple(f"geometry_{index:04d}" for index in range(chunk_count))
            if any(type(data.get(key)) is not str for key in chunk_keys):
                raise ValueError("invalid geometry chunks")
            geometry_text = "".join(str(data.pop(key)) for key in chunk_keys)
            fields = {
                "unit", "cycle", "top_z", "final_depth", "clearance_height",
                "retract_height", "feed_rate", "spindle_speed", "dwell_seconds",
                "peck_depth", "retract_policy", "approach_policy", "tolerance",
            }
            if set(data) != fields:
                raise ValueError("unexpected drilling parameters")
            unit = LengthUnit(data["unit"])
            feed_unit = (
                FeedUnit.MM_PER_MINUTE
                if unit is LengthUnit.MM
                else FeedUnit.INCH_PER_MINUTE
            )
            peck = data["peck_depth"]
            return cls(
                unit=unit,
                geometry=DrillGeometryInput.from_dict(json.loads(geometry_text)),
                depth=DrillDepthDefinition(
                    unit, Length(data["top_z"], unit), Length(data["final_depth"], unit)
                ),
                cycle=DrillingCycle(data["cycle"]),
                clearance_height=Length(data["clearance_height"], unit),
                retract_height=Length(data["retract_height"], unit),
                feed_rate=FeedRate(data["feed_rate"], feed_unit),
                spindle_speed=SpindleSpeed(data["spindle_speed"]),
                dwell_seconds=data["dwell_seconds"],
                peck_depth=None if peck is None else Length(peck, unit),
                retract_policy=DrillRetractPolicy(data["retract_policy"]),
                approach_policy=DrillApproachPolicy(data["approach_policy"]),
                tolerance=Length(data["tolerance"], unit),
                strategy_version=value.strategy_version,
                schema_version=value.schema_version,
            )
        except DrillValidationError:
            raise
        except (KeyError, TypeError, ValueError, CamValidationError) as error:
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling operation parameters are malformed",
            ) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _STRATEGY_FORMAT,
            "format_version": _VERSION,
            "strategy_key": DRILLING_STRATEGY_KEY,
            "strategy_version": self.strategy_version,
            "schema_version": self.schema_version,
            "unit": self.unit.value,
            "geometry": self.geometry.to_dict(),
            "depth": self.depth.to_dict(),
            "cycle": self.cycle.value,
            "clearance_height": self.clearance_height.value,
            "retract_height": self.retract_height.value,
            "feed_rate": self.feed_rate.value,
            "spindle_speed": self.spindle_speed.value,
            "dwell_seconds": self.dwell_seconds,
            "peck_depth": None if self.peck_depth is None else self.peck_depth.value,
            "retract_policy": self.retract_policy.value,
            "approach_policy": self.approach_policy.value,
            "tolerance": self.tolerance.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DrillingStrategy":
        fields = {
            "format", "format_version", "strategy_key", "strategy_version",
            "schema_version", "unit", "geometry", "depth", "cycle",
            "clearance_height", "retract_height", "feed_rate", "spindle_speed",
            "dwell_seconds", "peck_depth", "retract_policy", "approach_policy",
            "tolerance",
        }
        if (
            not isinstance(data, dict)
            or set(data) != fields
            or data.get("format") != _STRATEGY_FORMAT
            or data.get("format_version") != _VERSION
            or data.get("strategy_key") != DRILLING_STRATEGY_KEY
        ):
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling strategy payload is malformed",
            )
        try:
            unit = LengthUnit(data["unit"])
            feed_unit = (
                FeedUnit.MM_PER_MINUTE
                if unit is LengthUnit.MM
                else FeedUnit.INCH_PER_MINUTE
            )
            peck = data["peck_depth"]
            return cls(
                unit=unit,
                geometry=DrillGeometryInput.from_dict(data["geometry"]),
                depth=DrillDepthDefinition.from_dict(data["depth"]),
                cycle=DrillingCycle(data["cycle"]),
                clearance_height=Length(data["clearance_height"], unit),
                retract_height=Length(data["retract_height"], unit),
                feed_rate=FeedRate(data["feed_rate"], feed_unit),
                spindle_speed=SpindleSpeed(data["spindle_speed"]),
                dwell_seconds=data["dwell_seconds"],
                peck_depth=None if peck is None else Length(peck, unit),
                retract_policy=DrillRetractPolicy(data["retract_policy"]),
                approach_policy=DrillApproachPolicy(data["approach_policy"]),
                tolerance=Length(data["tolerance"], unit),
                strategy_version=data["strategy_version"],
                schema_version=data["schema_version"],
            )
        except DrillValidationError:
            raise
        except (TypeError, ValueError, CamValidationError) as error:
            raise DrillValidationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling strategy payload is invalid",
            ) from error


@dataclass(frozen=True, slots=True)
class ResolvedHoleLocation:
    """Fail-closed native-free result for one persistent hole reference."""

    status: GeometryResolutionStatus
    location: HoleLocation | None = None
    diagnostics: tuple[ValidationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, GeometryResolutionStatus):
            raise CamValidationError("Hole resolution status is invalid")
        if (self.status is GeometryResolutionStatus.RESOLVED) != (self.location is not None):
            raise CamValidationError("Only resolved hole geometry may carry a location")
        if self.status is not GeometryResolutionStatus.RESOLVED and not self.diagnostics:
            raise CamValidationError("Failed hole resolution requires a diagnostic")


@dataclass(frozen=True, slots=True)
class ResolvedDrillingGeometry:
    """Fail-closed result for a complete drilling geometry input."""

    status: GeometryResolutionStatus
    region: DrillingRegion | None = None
    diagnostics: tuple[ValidationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, GeometryResolutionStatus):
            raise CamValidationError("Drilling resolution status is invalid")
        if (self.status is GeometryResolutionStatus.RESOLVED) != (self.region is not None):
            raise CamValidationError("Only resolved drilling geometry may carry a region")
        if self.status is not GeometryResolutionStatus.RESOLVED and not self.diagnostics:
            raise CamValidationError("Failed drilling resolution requires a diagnostic")


def _position_key(value: Point3) -> tuple[int, int, int]:
    return tuple(round(coordinate / _TOLERANCE) for coordinate in (value.x, value.y, value.z))


def _has_duplicate_locations(locations: tuple[HoleLocation, ...]) -> bool:
    cells: dict[tuple[int, int, int], list[Point3]] = {}
    for location in locations:
        position = location.position
        cell = tuple(
            math.floor(coordinate / _TOLERANCE)
            for coordinate in (position.x, position.y, position.z)
        )
        for x_offset in (-1, 0, 1):
            for y_offset in (-1, 0, 1):
                for z_offset in (-1, 0, 1):
                    neighbor = (
                        cell[0] + x_offset,
                        cell[1] + y_offset,
                        cell[2] + z_offset,
                    )
                    if any(
                        math.dist(
                            (position.x, position.y, position.z),
                            (other.x, other.y, other.z),
                        ) <= _TOLERANCE
                        for other in cells.get(neighbor, ())
                    ):
                        return True
        cells.setdefault(cell, []).append(position)
    return False


def _location_key(value: HoleLocation) -> tuple[object, ...]:
    return (*_position_key(value.position), value.source_kind.value, value.fingerprint.digest)
