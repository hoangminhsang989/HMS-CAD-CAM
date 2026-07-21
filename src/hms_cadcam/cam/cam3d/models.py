"""Immutable, versioned CAM 3D geometry-foundation contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, ClassVar, TypeVar
from uuid import UUID

from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.geometry_reference import (
    GeometryReference,
    GeometryReferenceKind,
)
from hms_cadcam.cam.domain.ids import (
    Cam3DGeometrySnapshotId,
    CamJobId,
    CamSurfaceSelectionId,
    GeometryReferenceId,
    MachiningZone3DId,
    SetupId,
)
from hms_cadcam.cam.domain.revision import (
    ContentFingerprint,
    DependencyFingerprint,
    GeometryFingerprint,
    Revision,
)
from hms_cadcam.cam.domain.spatial import Point3, Vector3, WcsFrame
from hms_cadcam.cam.domain.units import LengthUnit

_VERSION = 1
_MAX_ALLOWANCE_MM = 1_000.0
_MAX_TOLERANCE_MM = 10.0
_MIN_TOLERANCE_MM = 1.0e-6
_AXIS_TOLERANCE = 1.0e-9
_T = TypeVar("_T")


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"{name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise CamValidationError(f"{name} must be finite")
    return 0.0 if normalized == 0.0 else normalized


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CamValidationError(f"{name} must not be empty")
    return value.strip()


def _optional_text(value: str | None, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _strict_payload(
    data: dict[str, Any],
    *,
    format_name: str,
    fields: set[str],
) -> None:
    if not isinstance(data, dict) or set(data) != fields | {"format", "format_version"}:
        raise CamValidationError(f"{format_name} payload is malformed")
    if data["format"] != format_name:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} format")
    if type(data["format_version"]) is not int or data["format_version"] != _VERSION:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} version")


def _enum(enum_type: type[_T], value: object, name: str) -> _T:
    try:
        return enum_type(value)  # type: ignore[call-arg,return-value]
    except (TypeError, ValueError) as error:
        raise CamValidationError(f"{name} is invalid") from error


def _fingerprint_payload(value: ContentFingerprint) -> dict[str, Any]:
    return value.to_dict()


def _axis_equal(first: Vector3, second: Vector3) -> bool:
    return all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=_AXIS_TOLERANCE)
        for left, right in zip(
            (first.x, first.y, first.z),
            (second.x, second.y, second.z),
            strict=True,
        )
    )


def _unit_axis(value: Vector3, name: str) -> None:
    if not isinstance(value, Vector3) or not math.isclose(
        value.magnitude, 1.0, rel_tol=0.0, abs_tol=_AXIS_TOLERANCE
    ):
        raise CamValidationError(f"{name} must be a unit vector")


class CamSurfaceRole(StrEnum):
    """Explicit CAM meaning assigned by the programmer."""

    PART = "part"
    CHECK = "check"
    FIXTURE = "fixture"
    STOCK_REFERENCE = "stock_reference"


class CamSurfaceOrientation(StrEnum):
    """Required orientation of one selected face."""

    FORWARD = "forward"
    REVERSED = "reversed"


@dataclass(frozen=True, slots=True)
class CamSurfaceReference:
    """Stable project-owned face reference with explicit CAM role."""

    project_id: UUID
    geometry: GeometryReference
    orientation: CamSurfaceOrientation
    role: CamSurfaceRole
    body_identity: str | None = None
    face_identity: str | None = None
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("CAM surface project ID is invalid")
        if not isinstance(self.geometry, GeometryReference):
            raise CamValidationError("CAM surface geometry reference is invalid")
        if self.geometry.kind is not GeometryReferenceKind.FACE:
            raise CamValidationError("CAM surface reference must target a FACE")
        if not isinstance(self.orientation, CamSurfaceOrientation):
            raise CamValidationError("CAM surface orientation is invalid")
        if not isinstance(self.role, CamSurfaceRole):
            raise CamValidationError("CAM surface role is invalid")
        object.__setattr__(
            self, "body_identity", _optional_text(self.body_identity, "Body identity")
        )
        face_identity = self.face_identity or self.geometry.subshape_selector
        object.__setattr__(
            self, "face_identity", _optional_text(face_identity, "Face identity")
        )

    @property
    def target_key(self) -> tuple[object, ...]:
        """Return stable geometry identity without display or runtime state."""
        return (self.project_id, *self.geometry.target_key)

    @property
    def fingerprint(self) -> GeometryFingerprint:
        """Fingerprint the selected face, orientation and semantic role."""
        return GeometryFingerprint.from_payload(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "geometry": self.geometry.to_dict(),
            "orientation": self.orientation.value,
            "role": self.role.value,
            "body_identity": self.body_identity,
            "face_identity": self.face_identity,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_SURFACE_REFERENCE",
            "format_version": _VERSION,
            **self.identity_payload(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CamSurfaceReference":
        _strict_payload(
            data,
            format_name="HMS_CAM3D_SURFACE_REFERENCE",
            fields={
                "project_id",
                "geometry",
                "orientation",
                "role",
                "body_identity",
                "face_identity",
            },
        )
        try:
            return cls(
                UUID(data["project_id"]),
                GeometryReference.from_dict(data["geometry"]),
                _enum(CamSurfaceOrientation, data["orientation"], "Surface orientation"),
                _enum(CamSurfaceRole, data["role"], "Surface role"),
                data["body_identity"],
                data["face_identity"],
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise CamValidationError("CAM surface reference payload is invalid") from error


@dataclass(frozen=True, slots=True)
class CamSurfaceSelection:
    """Deterministic, role-preserving group from one project/revision."""

    selection_id: CamSurfaceSelectionId
    project_id: UUID
    geometry_revision: Revision
    surfaces: tuple[CamSurfaceReference, ...]
    allow_empty: bool = False
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.selection_id, CamSurfaceSelectionId):
            raise CamValidationError("CAM surface selection ID is invalid")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("CAM surface selection project ID is invalid")
        if not isinstance(self.geometry_revision, Revision):
            raise CamValidationError("CAM surface selection revision is invalid")
        if not isinstance(self.surfaces, tuple) or any(
            not isinstance(item, CamSurfaceReference) for item in self.surfaces
        ):
            raise CamValidationError("CAM surface selection must be an immutable tuple")
        if type(self.allow_empty) is not bool:
            raise CamValidationError("CAM surface empty policy must be boolean")
        if not self.surfaces and not self.allow_empty:
            raise CamValidationError("CAM surface selection must not be empty")
        if any(item.project_id != self.project_id for item in self.surfaces):
            raise CamValidationError("CAM surfaces must belong to the same project")
        if any(
            item.geometry.expected_source_revision != self.geometry_revision
            for item in self.surfaces
        ):
            raise CamValidationError("CAM surfaces must use one current geometry revision")
        keys = tuple(item.target_key for item in self.surfaces)
        if len(keys) != len(set(keys)):
            raise CamValidationError("Duplicate CAM surface reference")
        canonical = tuple(
            sorted(
                self.surfaces,
                key=lambda item: (item.role.value, item.fingerprint.digest),
            )
        )
        object.__setattr__(self, "surfaces", canonical)

    @property
    def fingerprint(self) -> DependencyFingerprint:
        """Fingerprint semantic selection content, excluding its editable ID."""
        return DependencyFingerprint.from_payload(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "geometry_revision": self.geometry_revision.to_dict(),
            "surfaces": [item.to_dict() for item in self.surfaces],
            "allow_empty": self.allow_empty,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_SURFACE_SELECTION",
            "format_version": _VERSION,
            "selection_id": str(self.selection_id),
            **self.identity_payload(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CamSurfaceSelection":
        _strict_payload(
            data,
            format_name="HMS_CAM3D_SURFACE_SELECTION",
            fields={
                "selection_id",
                "project_id",
                "geometry_revision",
                "surfaces",
                "allow_empty",
            },
        )
        surfaces = data["surfaces"]
        if not isinstance(surfaces, list):
            raise CamValidationError("CAM surface selection surfaces must be a list")
        try:
            return cls(
                CamSurfaceSelectionId.parse(data["selection_id"]),
                UUID(data["project_id"]),
                Revision.from_dict(data["geometry_revision"]),
                tuple(CamSurfaceReference.from_dict(item) for item in surfaces),
                data["allow_empty"],
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise CamValidationError("CAM surface selection payload is invalid") from error


@dataclass(frozen=True, slots=True)
class PartSurfaceSet:
    """Required set of surfaces that define the part to machine."""

    selection: CamSurfaceSelection

    def __post_init__(self) -> None:
        _validate_role_set(self.selection, CamSurfaceRole.PART, empty_allowed=False)

    def to_dict(self) -> dict[str, Any]:
        return {"selection": self.selection.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PartSurfaceSet":
        return cls(_selection_from_wrapper(data))


@dataclass(frozen=True, slots=True)
class CheckSurfaceSet:
    """Optional surfaces that cutting motion must not enter."""

    selection: CamSurfaceSelection

    def __post_init__(self) -> None:
        _validate_role_set(self.selection, CamSurfaceRole.CHECK, empty_allowed=True)

    def to_dict(self) -> dict[str, Any]:
        return {"selection": self.selection.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CheckSurfaceSet":
        return cls(_selection_from_wrapper(data))


@dataclass(frozen=True, slots=True)
class FixtureSurfaceSet:
    """Optional project fixture surfaces used for fail-closed checks."""

    selection: CamSurfaceSelection

    def __post_init__(self) -> None:
        _validate_role_set(self.selection, CamSurfaceRole.FIXTURE, empty_allowed=True)

    def to_dict(self) -> dict[str, Any]:
        return {"selection": self.selection.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FixtureSurfaceSet":
        return cls(_selection_from_wrapper(data))


def _selection_from_wrapper(data: dict[str, Any]) -> CamSurfaceSelection:
    if not isinstance(data, dict) or set(data) != {"selection"}:
        raise CamValidationError("CAM surface-set payload is malformed")
    return CamSurfaceSelection.from_dict(data["selection"])


def _validate_role_set(
    selection: CamSurfaceSelection,
    role: CamSurfaceRole,
    *,
    empty_allowed: bool,
) -> None:
    if not isinstance(selection, CamSurfaceSelection):
        raise CamValidationError("CAM surface set selection is invalid")
    if not selection.surfaces and not empty_allowed:
        raise CamValidationError("Part surface set must not be empty")
    if selection.allow_empty is not empty_allowed:
        raise CamValidationError("CAM surface set empty policy is inconsistent")
    if any(item.role is not role for item in selection.surfaces):
        raise CamValidationError(f"CAM surface set requires role {role.value}")


@dataclass(frozen=True, slots=True)
class Cam3DTolerancePolicy:
    """Independent MM tolerances for tessellation, boundaries and contact."""

    chordal_tolerance: float
    angular_tolerance: float
    calculation_epsilon: float
    boundary_tolerance: float
    contact_tolerance: float
    minimum_triangle_size: float | None = None
    unit: LengthUnit = LengthUnit.MM
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if self.unit is not LengthUnit.MM:
            raise CamValidationError("CAM 3D tolerance policy supports MM only")
        for field_name in (
            "chordal_tolerance",
            "boundary_tolerance",
            "contact_tolerance",
        ):
            value = _finite(getattr(self, field_name), field_name)
            if not _MIN_TOLERANCE_MM <= value <= _MAX_TOLERANCE_MM:
                raise CamValidationError(f"{field_name} is outside safe limits")
            object.__setattr__(self, field_name, value)
        angular = _finite(self.angular_tolerance, "angular_tolerance")
        if not 1.0e-6 <= angular <= math.pi / 2.0:
            raise CamValidationError("angular_tolerance is outside safe limits")
        object.__setattr__(self, "angular_tolerance", angular)
        epsilon = _finite(self.calculation_epsilon, "calculation_epsilon")
        if not 1.0e-12 <= epsilon <= 1.0e-2:
            raise CamValidationError("calculation_epsilon is outside safe limits")
        object.__setattr__(self, "calculation_epsilon", epsilon)
        if self.minimum_triangle_size is not None:
            minimum = _finite(self.minimum_triangle_size, "minimum_triangle_size")
            if not _MIN_TOLERANCE_MM <= minimum <= _MAX_TOLERANCE_MM:
                raise CamValidationError("minimum_triangle_size is outside safe limits")
            object.__setattr__(self, "minimum_triangle_size", minimum)

    @property
    def fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_TOLERANCE_POLICY",
            "format_version": _VERSION,
            "chordal_tolerance": self.chordal_tolerance,
            "angular_tolerance": self.angular_tolerance,
            "calculation_epsilon": self.calculation_epsilon,
            "boundary_tolerance": self.boundary_tolerance,
            "contact_tolerance": self.contact_tolerance,
            "minimum_triangle_size": self.minimum_triangle_size,
            "unit": self.unit.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cam3DTolerancePolicy":
        _strict_payload(
            data,
            format_name="HMS_CAM3D_TOLERANCE_POLICY",
            fields={
                "chordal_tolerance",
                "angular_tolerance",
                "calculation_epsilon",
                "boundary_tolerance",
                "contact_tolerance",
                "minimum_triangle_size",
                "unit",
            },
        )
        return cls(
            data["chordal_tolerance"],
            data["angular_tolerance"],
            data["calculation_epsilon"],
            data["boundary_tolerance"],
            data["contact_tolerance"],
            data["minimum_triangle_size"],
            _enum(LengthUnit, data["unit"], "Tolerance unit"),
        )


@dataclass(frozen=True, slots=True)
class Cam3DStockAllowance:
    """Semantic stock values, separate from calculation tolerances."""

    part_normal: float = 0.0
    axial: float = 0.0
    check_surface_clearance: float = 0.0
    boundary_offset: float = 0.0
    unit: LengthUnit = LengthUnit.MM
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if self.unit is not LengthUnit.MM:
            raise CamValidationError("CAM 3D stock allowance supports MM only")
        for field_name in (
            "part_normal",
            "axial",
            "check_surface_clearance",
            "boundary_offset",
        ):
            value = _finite(getattr(self, field_name), field_name)
            if not 0.0 <= value <= _MAX_ALLOWANCE_MM:
                raise CamValidationError(f"{field_name} is outside safe limits")
            object.__setattr__(self, field_name, value)

    @property
    def fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_STOCK_ALLOWANCE",
            "format_version": _VERSION,
            "part_normal": self.part_normal,
            "axial": self.axial,
            "check_surface_clearance": self.check_surface_clearance,
            "boundary_offset": self.boundary_offset,
            "unit": self.unit.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cam3DStockAllowance":
        _strict_payload(
            data,
            format_name="HMS_CAM3D_STOCK_ALLOWANCE",
            fields={
                "part_normal",
                "axial",
                "check_surface_clearance",
                "boundary_offset",
                "unit",
            },
        )
        return cls(
            data["part_normal"],
            data["axial"],
            data["check_surface_clearance"],
            data["boundary_offset"],
            _enum(LengthUnit, data["unit"], "Allowance unit"),
        )


class MachiningBoundary3DKind(StrEnum):
    CLOSED_PLANAR_CONTOUR = "closed_planar_contour"
    SURFACE_SILHOUETTE_REFERENCE = "surface_silhouette_reference"
    NONE = "none"


class BoundaryOrientation3D(StrEnum):
    CLOCKWISE = "clockwise"
    COUNTERCLOCKWISE = "counterclockwise"


class BoundaryInclusionPolicy3D(StrEnum):
    INSIDE = "inside"
    OUTSIDE = "outside"
    TOUCHING = "touching"


@dataclass(frozen=True, slots=True)
class MachiningBoundary3D:
    """Fail-closed v1 boundary in one Setup coordinate frame."""

    kind: MachiningBoundary3DKind
    setup_id: SetupId
    plane: WcsFrame
    tolerance: float
    orientation: BoundaryOrientation3D
    inclusion: BoundaryInclusionPolicy3D
    geometry_revision: Revision
    points: tuple[Point3, ...] = ()
    source_references: tuple[GeometryReference, ...] = ()
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MachiningBoundary3DKind):
            raise CamValidationError("CAM 3D boundary kind is invalid")
        if not isinstance(self.setup_id, SetupId) or not isinstance(self.plane, WcsFrame):
            raise CamValidationError("CAM 3D boundary setup/plane is invalid")
        if self.plane.origin.unit is not LengthUnit.MM:
            raise CamValidationError("CAM 3D boundary supports MM only")
        tolerance = _finite(self.tolerance, "Boundary tolerance")
        if not _MIN_TOLERANCE_MM <= tolerance <= _MAX_TOLERANCE_MM:
            raise CamValidationError("Boundary tolerance is outside safe limits")
        object.__setattr__(self, "tolerance", tolerance)
        if not isinstance(self.orientation, BoundaryOrientation3D) or not isinstance(
            self.inclusion, BoundaryInclusionPolicy3D
        ):
            raise CamValidationError("CAM 3D boundary policy is invalid")
        if not isinstance(self.geometry_revision, Revision):
            raise CamValidationError("CAM 3D boundary revision is invalid")
        if not isinstance(self.points, tuple) or any(
            not isinstance(item, Point3) or item.unit is not LengthUnit.MM
            for item in self.points
        ):
            raise CamValidationError("CAM 3D boundary points are invalid")
        if not isinstance(self.source_references, tuple) or any(
            not isinstance(item, GeometryReference) for item in self.source_references
        ):
            raise CamValidationError("CAM 3D boundary provenance is invalid")
        if any(
            item.expected_source_revision != self.geometry_revision
            for item in self.source_references
        ):
            raise CamValidationError("CAM 3D boundary source geometry is stale")
        if self.kind is MachiningBoundary3DKind.NONE:
            if self.points or self.source_references:
                raise CamValidationError("NONE boundary cannot contain geometry")
        elif self.kind is MachiningBoundary3DKind.SURFACE_SILHOUETTE_REFERENCE:
            if self.points or not self.source_references:
                raise CamValidationError("Silhouette boundary requires only source geometry")
        else:
            if len(self.points) < 4 or not self.source_references:
                raise CamValidationError("Closed boundary requires provenance and four points")
            _validate_closed_planar_boundary(self)

    @property
    def fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_MACHINING_BOUNDARY",
            "format_version": _VERSION,
            "kind": self.kind.value,
            "setup_id": str(self.setup_id),
            "plane": self.plane.to_dict(),
            "tolerance": self.tolerance,
            "orientation": self.orientation.value,
            "inclusion": self.inclusion.value,
            "geometry_revision": self.geometry_revision.to_dict(),
            "points": [item.to_dict() for item in self.points],
            "source_references": [item.to_dict() for item in self.source_references],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MachiningBoundary3D":
        _strict_payload(
            data,
            format_name="HMS_CAM3D_MACHINING_BOUNDARY",
            fields={
                "kind",
                "setup_id",
                "plane",
                "tolerance",
                "orientation",
                "inclusion",
                "geometry_revision",
                "points",
                "source_references",
            },
        )
        points = data["points"]
        sources = data["source_references"]
        if not isinstance(points, list) or not isinstance(sources, list):
            raise CamValidationError("CAM 3D boundary arrays are invalid")
        return cls(
            _enum(MachiningBoundary3DKind, data["kind"], "Boundary kind"),
            SetupId.parse(data["setup_id"]),
            WcsFrame.from_dict(data["plane"]),
            data["tolerance"],
            _enum(BoundaryOrientation3D, data["orientation"], "Boundary orientation"),
            _enum(BoundaryInclusionPolicy3D, data["inclusion"], "Boundary inclusion"),
            Revision.from_dict(data["geometry_revision"]),
            tuple(Point3.from_dict(item) for item in points),
            tuple(GeometryReference.from_dict(item) for item in sources),
        )


def _plane_coordinates(point: Point3, plane: WcsFrame) -> tuple[float, float, float]:
    delta = Vector3(
        point.x - plane.origin.x,
        point.y - plane.origin.y,
        point.z - plane.origin.z,
    )
    return (
        delta.dot(plane.x_axis),
        delta.dot(plane.y_axis),
        delta.dot(plane.z_axis),
    )


def _validate_closed_planar_boundary(boundary: MachiningBoundary3D) -> None:
    coordinates = tuple(_plane_coordinates(item, boundary.plane) for item in boundary.points)
    if any(abs(value[2]) > boundary.tolerance for value in coordinates):
        raise CamValidationError("Boundary points are outside the declared plane")
    if math.dist(coordinates[0], coordinates[-1]) > boundary.tolerance:
        raise CamValidationError("Closed boundary is open")
    vertices = coordinates[:-1]
    if any(
        math.dist(vertices[index], vertices[(index + 1) % len(vertices)])
        <= boundary.tolerance
        for index in range(len(vertices))
    ):
        raise CamValidationError("Boundary contains a zero-length edge")
    if _has_self_intersection(tuple((x, y) for x, y, _z in vertices), boundary.tolerance):
        raise CamValidationError("Boundary is self-intersecting")
    signed_area = sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(vertices, (*vertices[1:], vertices[0]), strict=True)
    )
    if abs(signed_area) <= boundary.tolerance * boundary.tolerance:
        raise CamValidationError("Boundary has no usable area")
    expected_positive = boundary.orientation is BoundaryOrientation3D.COUNTERCLOCKWISE
    if (signed_area > 0.0) is not expected_positive:
        raise CamValidationError("Boundary orientation does not match point order")


def _has_self_intersection(
    vertices: tuple[tuple[float, float], ...], tolerance: float
) -> bool:
    def orientation(
        first: tuple[float, float],
        second: tuple[float, float],
        third: tuple[float, float],
    ) -> float:
        return (second[0] - first[0]) * (third[1] - first[1]) - (
            second[1] - first[1]
        ) * (third[0] - first[0])

    count = len(vertices)
    for first_index in range(count):
        first = vertices[first_index]
        second = vertices[(first_index + 1) % count]
        for second_index in range(first_index + 1, count):
            if second_index in {
                first_index,
                (first_index + 1) % count,
                (first_index - 1) % count,
            }:
                continue
            third = vertices[second_index]
            fourth = vertices[(second_index + 1) % count]
            turns = (
                orientation(first, second, third),
                orientation(first, second, fourth),
                orientation(third, fourth, first),
                orientation(third, fourth, second),
            )
            if turns[0] * turns[1] < -(tolerance**2) and turns[2] * turns[3] < -(
                tolerance**2
            ):
                return True
    return False


@dataclass(frozen=True, slots=True)
class MachiningZone3D:
    """V1 one-Setup, fixed-axis, three-axis CAM geometry scope."""

    zone_id: MachiningZone3DId
    project_id: UUID
    job_id: CamJobId
    setup_id: SetupId
    setup_revision: Revision
    wcs: WcsFrame
    part_surfaces: PartSurfaceSet
    check_surfaces: CheckSurfaceSet | None
    fixture_surfaces: FixtureSurfaceSet | None
    boundary: MachiningBoundary3D | None
    tool_axis: Vector3
    machining_direction: Vector3 | None
    minimum_height: float | None
    maximum_height: float | None
    tolerance: Cam3DTolerancePolicy
    allowance: Cam3DStockAllowance
    geometry_revision: Revision
    geometry_fingerprint: GeometryFingerprint
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.zone_id, MachiningZone3DId):
            raise CamValidationError("CAM 3D zone ID is invalid")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("CAM 3D zone project ID is invalid")
        if not isinstance(self.job_id, CamJobId) or not isinstance(self.setup_id, SetupId):
            raise CamValidationError("CAM 3D zone Job/Setup ID is invalid")
        if not isinstance(self.setup_revision, Revision) or not isinstance(
            self.geometry_revision, Revision
        ):
            raise CamValidationError("CAM 3D zone revision is invalid")
        if not isinstance(self.wcs, WcsFrame) or self.wcs.origin.unit is not LengthUnit.MM:
            raise CamValidationError("CAM 3D zone requires an MM Setup WCS")
        if not isinstance(self.part_surfaces, PartSurfaceSet):
            raise CamValidationError("CAM 3D zone requires part surfaces")
        optional_sets = (self.check_surfaces, self.fixture_surfaces)
        if self.check_surfaces is not None and not isinstance(
            self.check_surfaces, CheckSurfaceSet
        ):
            raise CamValidationError("CAM 3D check surfaces are invalid")
        if self.fixture_surfaces is not None and not isinstance(
            self.fixture_surfaces, FixtureSurfaceSet
        ):
            raise CamValidationError("CAM 3D fixture surfaces are invalid")
        selections = (
            self.part_surfaces.selection,
            *(item.selection for item in optional_sets if item is not None),
        )
        if any(item.project_id != self.project_id for item in selections):
            raise CamValidationError("CAM 3D zone surfaces belong to another project")
        if any(item.geometry_revision != self.geometry_revision for item in selections):
            raise CamValidationError("CAM 3D zone surfaces use a stale revision")
        if self.boundary is not None:
            if not isinstance(self.boundary, MachiningBoundary3D):
                raise CamValidationError("CAM 3D boundary is invalid")
            if self.boundary.setup_id != self.setup_id:
                raise CamValidationError("CAM 3D boundary belongs to another Setup")
            if self.boundary.geometry_revision != self.geometry_revision:
                raise CamValidationError("CAM 3D boundary is stale")
            if not _axis_equal(self.boundary.plane.z_axis, self.wcs.z_axis):
                raise CamValidationError("CAM 3D boundary uses the wrong Setup plane")
        _unit_axis(self.tool_axis, "CAM 3D tool axis")
        if not _axis_equal(self.tool_axis, self.wcs.z_axis):
            raise CamValidationError("CAM 3D v1 tool axis must equal Setup Z")
        if self.machining_direction is not None:
            _unit_axis(self.machining_direction, "CAM 3D machining direction")
            if abs(self.machining_direction.dot(self.tool_axis)) > _AXIS_TOLERANCE:
                raise CamValidationError("Machining direction must be normal to tool axis")
        minimum = (
            None
            if self.minimum_height is None
            else _finite(self.minimum_height, "Minimum machining height")
        )
        maximum = (
            None
            if self.maximum_height is None
            else _finite(self.maximum_height, "Maximum machining height")
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise CamValidationError("CAM 3D height limits are reversed")
        object.__setattr__(self, "minimum_height", minimum)
        object.__setattr__(self, "maximum_height", maximum)
        if not isinstance(self.tolerance, Cam3DTolerancePolicy) or not isinstance(
            self.allowance, Cam3DStockAllowance
        ):
            raise CamValidationError("CAM 3D zone calculation policies are invalid")
        if not isinstance(self.geometry_fingerprint, GeometryFingerprint):
            raise CamValidationError("CAM 3D zone geometry fingerprint is invalid")

    @property
    def fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(self.to_dict())

    def all_surfaces(self) -> tuple[CamSurfaceReference, ...]:
        """Return every selected surface in deterministic role order."""
        selections = [self.part_surfaces.selection]
        if self.check_surfaces is not None:
            selections.append(self.check_surfaces.selection)
        if self.fixture_surfaces is not None:
            selections.append(self.fixture_surfaces.selection)
        return tuple(surface for selection in selections for surface in selection.surfaces)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_MACHINING_ZONE",
            "format_version": _VERSION,
            "zone_id": str(self.zone_id),
            "project_id": str(self.project_id),
            "job_id": str(self.job_id),
            "setup_id": str(self.setup_id),
            "setup_revision": self.setup_revision.to_dict(),
            "wcs": self.wcs.to_dict(),
            "part_surfaces": self.part_surfaces.to_dict(),
            "check_surfaces": (
                self.check_surfaces.to_dict() if self.check_surfaces is not None else None
            ),
            "fixture_surfaces": (
                self.fixture_surfaces.to_dict()
                if self.fixture_surfaces is not None
                else None
            ),
            "boundary": self.boundary.to_dict() if self.boundary is not None else None,
            "tool_axis": self.tool_axis.to_dict(),
            "machining_direction": (
                self.machining_direction.to_dict()
                if self.machining_direction is not None
                else None
            ),
            "minimum_height": self.minimum_height,
            "maximum_height": self.maximum_height,
            "tolerance": self.tolerance.to_dict(),
            "allowance": self.allowance.to_dict(),
            "geometry_revision": self.geometry_revision.to_dict(),
            "geometry_fingerprint": self.geometry_fingerprint.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MachiningZone3D":
        _strict_payload(
            data,
            format_name="HMS_CAM3D_MACHINING_ZONE",
            fields={
                "zone_id",
                "project_id",
                "job_id",
                "setup_id",
                "setup_revision",
                "wcs",
                "part_surfaces",
                "check_surfaces",
                "fixture_surfaces",
                "boundary",
                "tool_axis",
                "machining_direction",
                "minimum_height",
                "maximum_height",
                "tolerance",
                "allowance",
                "geometry_revision",
                "geometry_fingerprint",
            },
        )
        return cls(
            MachiningZone3DId.parse(data["zone_id"]),
            UUID(data["project_id"]),
            CamJobId.parse(data["job_id"]),
            SetupId.parse(data["setup_id"]),
            Revision.from_dict(data["setup_revision"]),
            WcsFrame.from_dict(data["wcs"]),
            PartSurfaceSet.from_dict(data["part_surfaces"]),
            (
                CheckSurfaceSet.from_dict(data["check_surfaces"])
                if data["check_surfaces"] is not None
                else None
            ),
            (
                FixtureSurfaceSet.from_dict(data["fixture_surfaces"])
                if data["fixture_surfaces"] is not None
                else None
            ),
            (
                MachiningBoundary3D.from_dict(data["boundary"])
                if data["boundary"] is not None
                else None
            ),
            Vector3.from_dict(data["tool_axis"]),
            (
                Vector3.from_dict(data["machining_direction"])
                if data["machining_direction"] is not None
                else None
            ),
            data["minimum_height"],
            data["maximum_height"],
            Cam3DTolerancePolicy.from_dict(data["tolerance"]),
            Cam3DStockAllowance.from_dict(data["allowance"]),
            Revision.from_dict(data["geometry_revision"]),
            GeometryFingerprint.from_dict(data["geometry_fingerprint"]),
        )


class Cam3DSafeTransitionPolicy(StrEnum):
    RETRACT_THEN_RAPID = "retract_then_rapid"
    CUTTING_FEED_ONLY = "cutting_feed_only"


@dataclass(frozen=True, slots=True)
class Cam3DSafeMotionPolicy:
    """Explicit safe-motion inputs expressed only in Setup WCS."""

    setup_id: SetupId
    setup_revision: Revision
    wcs_fingerprint: DependencyFingerprint
    clearance_z: float | None
    retract_z: float | None
    approach_distance: float
    link_clearance: float
    transition_policy: Cam3DSafeTransitionPolicy
    tool_axis: Vector3
    unit: LengthUnit = LengthUnit.MM
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.setup_id, SetupId) or not isinstance(
            self.setup_revision, Revision
        ):
            raise CamValidationError("CAM 3D safe-motion Setup is invalid")
        if not isinstance(self.wcs_fingerprint, DependencyFingerprint):
            raise CamValidationError("CAM 3D safe-motion WCS fingerprint is invalid")
        if self.unit is not LengthUnit.MM:
            raise CamValidationError("CAM 3D safe motion supports MM only")
        for field_name in ("clearance_z", "retract_z"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _finite(value, field_name))
        object.__setattr__(
            self,
            "approach_distance",
            _finite(self.approach_distance, "approach_distance"),
        )
        object.__setattr__(
            self, "link_clearance", _finite(self.link_clearance, "link_clearance")
        )
        if not isinstance(self.transition_policy, Cam3DSafeTransitionPolicy):
            raise CamValidationError("CAM 3D transition policy is invalid")
        _unit_axis(self.tool_axis, "CAM 3D safe-motion tool axis")

    @property
    def fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_SAFE_MOTION_POLICY",
            "format_version": _VERSION,
            "setup_id": str(self.setup_id),
            "setup_revision": self.setup_revision.to_dict(),
            "wcs_fingerprint": self.wcs_fingerprint.to_dict(),
            "clearance_z": self.clearance_z,
            "retract_z": self.retract_z,
            "approach_distance": self.approach_distance,
            "link_clearance": self.link_clearance,
            "transition_policy": self.transition_policy.value,
            "tool_axis": self.tool_axis.to_dict(),
            "unit": self.unit.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cam3DSafeMotionPolicy":
        _strict_payload(
            data,
            format_name="HMS_CAM3D_SAFE_MOTION_POLICY",
            fields={
                "setup_id",
                "setup_revision",
                "wcs_fingerprint",
                "clearance_z",
                "retract_z",
                "approach_distance",
                "link_clearance",
                "transition_policy",
                "tool_axis",
                "unit",
            },
        )
        return cls(
            SetupId.parse(data["setup_id"]),
            Revision.from_dict(data["setup_revision"]),
            DependencyFingerprint.from_dict(data["wcs_fingerprint"]),
            data["clearance_z"],
            data["retract_z"],
            data["approach_distance"],
            data["link_clearance"],
            _enum(
                Cam3DSafeTransitionPolicy,
                data["transition_policy"],
                "Safe transition policy",
            ),
            Vector3.from_dict(data["tool_axis"]),
            _enum(LengthUnit, data["unit"], "Safe-motion unit"),
        )


class Cam3DDiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Cam3DDiagnosticCode(StrEnum):
    INVALID_REQUEST = "cam3d.invalid_request"
    SURFACE_MISSING = "cam3d.surface_missing"
    SURFACE_STALE = "cam3d.surface_stale"
    SURFACE_DUPLICATE = "cam3d.surface_duplicate"
    PART_SURFACE_EMPTY = "cam3d.part_surface_empty"
    CHECK_SURFACE_INVALID = "cam3d.check_surface_invalid"
    FIXTURE_SURFACE_INVALID = "cam3d.fixture_surface_invalid"
    BOUNDARY_INVALID = "cam3d.boundary_invalid"
    BOUNDARY_OPEN = "cam3d.boundary_open"
    SETUP_MISMATCH = "cam3d.setup_mismatch"
    TOOL_AXIS_UNSUPPORTED = "cam3d.tool_axis_unsupported"
    TOLERANCE_INVALID = "cam3d.tolerance_invalid"
    ALLOWANCE_INVALID = "cam3d.allowance_invalid"
    MESH_EMPTY = "cam3d.mesh_empty"
    MESH_DEGENERATE = "cam3d.mesh_degenerate"
    MESH_TOO_LARGE = "cam3d.mesh_too_large"
    MESH_NON_FINITE = "cam3d.mesh_non_finite"
    ORIENTATION_INVALID = "cam3d.orientation_invalid"
    SAFE_MOTION_INVALID = "cam3d.safe_motion_invalid"
    TOOL_UNSUPPORTED = "cam3d.tool_unsupported"
    CONTACT_INVALID = "cam3d.contact_invalid"
    GEOMETRY_CHANGED = "cam3d.geometry_changed"
    STALE = "cam3d.stale"
    CANCELLED = "cam3d.cancelled"
    FAILED = "cam3d.failed"


@dataclass(frozen=True, slots=True)
class Cam3DDiagnostic:
    """Structured evidence that can identify geometry or policy inputs."""

    code: Cam3DDiagnosticCode
    severity: Cam3DDiagnosticSeverity
    message: str
    source_reference_id: GeometryReferenceId | None = None
    triangle_index: int | None = None
    boundary_fingerprint: str | None = None
    setup_id: SetupId | None = None
    evidence: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, Cam3DDiagnosticCode) or not isinstance(
            self.severity, Cam3DDiagnosticSeverity
        ):
            raise CamValidationError("CAM 3D diagnostic classification is invalid")
        object.__setattr__(self, "message", _text(self.message, "Diagnostic message"))
        if self.source_reference_id is not None and not isinstance(
            self.source_reference_id, GeometryReferenceId
        ):
            raise CamValidationError("CAM 3D diagnostic source is invalid")
        if self.triangle_index is not None and (
            type(self.triangle_index) is not int or self.triangle_index < 0
        ):
            raise CamValidationError("CAM 3D diagnostic triangle index is invalid")
        object.__setattr__(
            self,
            "boundary_fingerprint",
            _optional_text(self.boundary_fingerprint, "Boundary fingerprint"),
        )
        if self.setup_id is not None and not isinstance(self.setup_id, SetupId):
            raise CamValidationError("CAM 3D diagnostic Setup is invalid")
        try:
            normalized = tuple(sorted(self.evidence))
        except TypeError as error:
            raise CamValidationError("CAM 3D diagnostic evidence is invalid") from error
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(value, str) and value.strip() for value in item)
            for item in normalized
        ):
            raise CamValidationError("CAM 3D diagnostic evidence is invalid")
        if len({key for key, _value in normalized}) != len(normalized):
            raise CamValidationError("CAM 3D diagnostic evidence keys must be unique")
        object.__setattr__(self, "evidence", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "source_reference_id": (
                str(self.source_reference_id)
                if self.source_reference_id is not None
                else None
            ),
            "triangle_index": self.triangle_index,
            "boundary_fingerprint": self.boundary_fingerprint,
            "setup_id": str(self.setup_id) if self.setup_id is not None else None,
            "evidence": [
                {"key": key, "value": value} for key, value in self.evidence
            ],
        }


@dataclass(frozen=True, slots=True)
class Cam3DStatistics:
    """Deterministic calculation counts; elapsed time is intentionally absent."""

    surface_count: int
    vertex_count: int
    triangle_count: int
    rejected_degenerate_triangles: int = 0

    def __post_init__(self) -> None:
        values = (
            self.surface_count,
            self.vertex_count,
            self.triangle_count,
            self.rejected_degenerate_triangles,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise CamValidationError("CAM 3D statistics must be non-negative integers")

    def to_dict(self) -> dict[str, int]:
        return {
            "surface_count": self.surface_count,
            "vertex_count": self.vertex_count,
            "triangle_count": self.triangle_count,
            "rejected_degenerate_triangles": self.rejected_degenerate_triangles,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cam3DStatistics":
        fields = {
            "surface_count",
            "vertex_count",
            "triangle_count",
            "rejected_degenerate_triangles",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("CAM 3D statistics payload is malformed")
        return cls(**data)


@dataclass(frozen=True, slots=True)
class Cam3DGeometrySnapshot:
    """Immutable project/CAD input captured before worker calculation."""

    snapshot_id: Cam3DGeometrySnapshotId
    project_id: UUID
    project_generation: int
    setup_revision: Revision
    geometry_revision: Revision
    geometry_fingerprint: GeometryFingerprint
    zone: MachiningZone3D
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, Cam3DGeometrySnapshotId):
            raise CamValidationError("CAM 3D geometry snapshot ID is invalid")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("CAM 3D geometry snapshot project ID is invalid")
        if type(self.project_generation) is not int or self.project_generation < 0:
            raise CamValidationError("CAM 3D project generation is invalid")
        if not isinstance(self.setup_revision, Revision) or not isinstance(
            self.geometry_revision, Revision
        ):
            raise CamValidationError("CAM 3D snapshot revision is invalid")
        if not isinstance(self.geometry_fingerprint, GeometryFingerprint) or not isinstance(
            self.zone, MachiningZone3D
        ):
            raise CamValidationError("CAM 3D snapshot geometry is invalid")
        if self.zone.project_id != self.project_id:
            raise CamValidationError("CAM 3D snapshot belongs to another project")
        if self.zone.setup_revision != self.setup_revision:
            raise CamValidationError("CAM 3D snapshot Setup is stale")
        if (
            self.zone.geometry_revision != self.geometry_revision
            or self.zone.geometry_fingerprint != self.geometry_fingerprint
        ):
            raise CamValidationError("CAM 3D snapshot geometry is stale")

    @property
    def fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "project_generation": self.project_generation,
            "setup_revision": self.setup_revision.to_dict(),
            "geometry_revision": self.geometry_revision.to_dict(),
            "geometry_fingerprint": self.geometry_fingerprint.to_dict(),
            "zone": self.zone.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM3D_GEOMETRY_SNAPSHOT",
            "format_version": _VERSION,
            "snapshot_id": str(self.snapshot_id),
            **self.identity_payload(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cam3DGeometrySnapshot":
        _strict_payload(
            data,
            format_name="HMS_CAM3D_GEOMETRY_SNAPSHOT",
            fields={
                "snapshot_id",
                "project_id",
                "project_generation",
                "setup_revision",
                "geometry_revision",
                "geometry_fingerprint",
                "zone",
            },
        )
        return cls(
            Cam3DGeometrySnapshotId.parse(data["snapshot_id"]),
            UUID(data["project_id"]),
            data["project_generation"],
            Revision.from_dict(data["setup_revision"]),
            Revision.from_dict(data["geometry_revision"]),
            GeometryFingerprint.from_dict(data["geometry_fingerprint"]),
            MachiningZone3D.from_dict(data["zone"]),
        )

    def rebind_project(self, project_id: UUID) -> "Cam3DGeometrySnapshot":
        """Create an isolated Save-As snapshot without retaining old project identity."""
        zone = rebind_zone_project(self.zone, project_id)
        return replace(self, project_id=project_id, zone=zone)


def rebind_zone_project(zone: MachiningZone3D, project_id: UUID) -> MachiningZone3D:
    """Rebase only project ownership; CAD source identities remain unchanged."""
    def rebind_selection(selection: CamSurfaceSelection) -> CamSurfaceSelection:
        surfaces = tuple(replace(item, project_id=project_id) for item in selection.surfaces)
        return replace(selection, project_id=project_id, surfaces=surfaces)

    part = PartSurfaceSet(rebind_selection(zone.part_surfaces.selection))
    check = (
        CheckSurfaceSet(rebind_selection(zone.check_surfaces.selection))
        if zone.check_surfaces is not None
        else None
    )
    fixture = (
        FixtureSurfaceSet(rebind_selection(zone.fixture_surfaces.selection))
        if zone.fixture_surfaces is not None
        else None
    )
    return replace(
        zone,
        project_id=project_id,
        part_surfaces=part,
        check_surfaces=check,
        fixture_surfaces=fixture,
    )


def wcs_fingerprint(wcs: WcsFrame) -> DependencyFingerprint:
    """Return canonical Setup-WCS identity for stale checks."""
    if not isinstance(wcs, WcsFrame):
        raise CamValidationError("WCS is invalid")
    return DependencyFingerprint.from_payload(wcs.to_dict())
