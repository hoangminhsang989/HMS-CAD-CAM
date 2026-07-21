"""Deterministic calculation mesh contracts and canonical builder."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Protocol

from hms_cadcam.cad.models import BoundingBox
from hms_cadcam.cam.cam3d.models import (
    Cam3DDiagnostic,
    Cam3DDiagnosticCode,
    Cam3DDiagnosticSeverity,
    Cam3DStatistics,
    Cam3DTolerancePolicy,
    CamSurfaceOrientation,
    CamSurfaceReference,
)
from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import GeometryReferenceId
from hms_cadcam.cam.domain.revision import GeometryFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import LengthUnit

_FORMAT = "HMS_CAM3D_CALCULATION_MESH"
_VERSION = 1


class Cam3DMeshError(RuntimeError):
    """Fail-closed mesh construction error with structured evidence."""

    def __init__(self, diagnostic: Cam3DDiagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class Cam3DCancelledError(Cam3DMeshError):
    """Cooperative cancellation observed at a bounded checkpoint."""


@dataclass(frozen=True, slots=True)
class Cam3DResolvedSurfaceMesh:
    """Native-free tessellation fragment produced by a CAD adapter."""

    surface: CamSurfaceReference
    vertices: tuple[Point3, ...]
    triangles: tuple[tuple[int, int, int], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.surface, CamSurfaceReference):
            raise CamValidationError("Resolved CAM 3D surface is invalid")
        if not isinstance(self.vertices, tuple) or any(
            not isinstance(item, Point3) or item.unit is not LengthUnit.MM
            for item in self.vertices
        ):
            raise CamValidationError("Resolved CAM 3D vertices are invalid")
        if not isinstance(self.triangles, tuple):
            raise CamValidationError("Resolved CAM 3D triangles must be immutable")
        for triangle in self.triangles:
            if (
                not isinstance(triangle, tuple)
                or len(triangle) != 3
                or any(type(index) is not int or index < 0 for index in triangle)
                or any(index >= len(self.vertices) for index in triangle)
            ):
                raise CamValidationError("Resolved CAM 3D triangle index is invalid")


class Cam3DSurfaceMesher(Protocol):
    """Adapter boundary for resolving and tessellating one persistent face."""

    def tessellate(
        self,
        surface: CamSurfaceReference,
        tolerance: Cam3DTolerancePolicy,
        cancellation: Callable[[], bool] | None = None,
    ) -> Cam3DResolvedSurfaceMesh:
        """Return one verified native-free fragment or fail closed."""
        ...


@dataclass(frozen=True, slots=True)
class Cam3DCalculationMesh:
    """Canonical CAM calculation mesh, independent from Viewer quality."""

    vertices: tuple[Point3, ...]
    triangle_indices: tuple[tuple[int, int, int], ...]
    triangle_normals: tuple[Vector3, ...]
    triangle_sources: tuple[GeometryReferenceId, ...]
    bounding_box: BoundingBox
    chordal_tolerance: float
    angular_tolerance: float
    unit: LengthUnit
    source_geometry_fingerprint: GeometryFingerprint
    mesh_fingerprint: GeometryFingerprint
    statistics: Cam3DStatistics
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if self.unit is not LengthUnit.MM:
            raise CamValidationError("CAM 3D calculation mesh supports MM only")
        if not self.vertices or not self.triangle_indices:
            raise CamValidationError("CAM 3D calculation mesh must not be empty")
        if any(
            not isinstance(item, Point3) or item.unit is not self.unit
            for item in self.vertices
        ):
            raise CamValidationError("CAM 3D calculation mesh vertices are invalid")
        count = len(self.triangle_indices)
        if len(self.triangle_normals) != count or len(self.triangle_sources) != count:
            raise CamValidationError("CAM 3D triangle metadata is incomplete")
        for triangle in self.triangle_indices:
            if (
                not isinstance(triangle, tuple)
                or len(triangle) != 3
                or len(set(triangle)) != 3
                or any(type(index) is not int or not 0 <= index < len(self.vertices) for index in triangle)
            ):
                raise CamValidationError("CAM 3D triangle indices are invalid")
        if any(
            not isinstance(item, Vector3)
            or not math.isclose(item.magnitude, 1.0, rel_tol=0.0, abs_tol=1.0e-9)
            for item in self.triangle_normals
        ):
            raise CamValidationError("CAM 3D triangle normals are invalid")
        if any(not isinstance(item, GeometryReferenceId) for item in self.triangle_sources):
            raise CamValidationError("CAM 3D triangle source mapping is invalid")
        if not isinstance(self.bounding_box, BoundingBox):
            raise CamValidationError("CAM 3D mesh bounding box is invalid")
        for value, name in (
            (self.chordal_tolerance, "chordal tolerance"),
            (self.angular_tolerance, "angular tolerance"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0.0:
                raise CamValidationError(f"CAM 3D mesh {name} is invalid")
        if not isinstance(self.source_geometry_fingerprint, GeometryFingerprint) or not isinstance(
            self.mesh_fingerprint, GeometryFingerprint
        ):
            raise CamValidationError("CAM 3D mesh fingerprint is invalid")
        if not isinstance(self.statistics, Cam3DStatistics):
            raise CamValidationError("CAM 3D mesh statistics are invalid")
        expected_statistics = Cam3DStatistics(
            len(set(self.triangle_sources)), len(self.vertices), count
        )
        if self.statistics != expected_statistics:
            raise CamValidationError("CAM 3D mesh statistics do not match arrays")
        expected = GeometryFingerprint.from_payload(self.identity_payload())
        if expected != self.mesh_fingerprint:
            raise CamValidationError("CAM 3D mesh fingerprint does not match content")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "vertices": [item.to_dict() for item in self.vertices],
            "triangle_indices": [list(item) for item in self.triangle_indices],
            "triangle_normals": [item.to_dict() for item in self.triangle_normals],
            "triangle_sources": [str(item) for item in self.triangle_sources],
            "bounding_box": _bounds_to_dict(self.bounding_box),
            "chordal_tolerance": self.chordal_tolerance,
            "angular_tolerance": self.angular_tolerance,
            "unit": self.unit.value,
            "source_geometry_fingerprint": self.source_geometry_fingerprint.to_dict(),
            "statistics": self.statistics.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _FORMAT,
            "format_version": _VERSION,
            **self.identity_payload(),
            "mesh_fingerprint": self.mesh_fingerprint.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cam3DCalculationMesh":
        fields = {
            "format",
            "format_version",
            "vertices",
            "triangle_indices",
            "triangle_normals",
            "triangle_sources",
            "bounding_box",
            "chordal_tolerance",
            "angular_tolerance",
            "unit",
            "source_geometry_fingerprint",
            "statistics",
            "mesh_fingerprint",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("CAM 3D calculation mesh payload is malformed")
        if data["format"] != _FORMAT:
            raise UnsupportedCamSchemaError("Unsupported CAM 3D calculation mesh format")
        if type(data["format_version"]) is not int or data["format_version"] != _VERSION:
            raise UnsupportedCamSchemaError("Unsupported CAM 3D calculation mesh version")
        try:
            unit = LengthUnit(data["unit"])
            triangles = tuple(tuple(item) for item in data["triangle_indices"])
            return cls(
                tuple(Point3.from_dict(item) for item in data["vertices"]),
                triangles,
                tuple(Vector3.from_dict(item) for item in data["triangle_normals"]),
                tuple(GeometryReferenceId.parse(item) for item in data["triangle_sources"]),
                _bounds_from_dict(data["bounding_box"]),
                data["chordal_tolerance"],
                data["angular_tolerance"],
                unit,
                GeometryFingerprint.from_dict(data["source_geometry_fingerprint"]),
                GeometryFingerprint.from_dict(data["mesh_fingerprint"]),
                Cam3DStatistics.from_dict(data["statistics"]),
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise CamValidationError("CAM 3D calculation mesh payload is invalid") from error


def build_calculation_mesh(
    fragments: tuple[Cam3DResolvedSurfaceMesh, ...],
    tolerance: Cam3DTolerancePolicy,
    source_geometry_fingerprint: GeometryFingerprint,
    *,
    cancellation: Callable[[], bool] | None = None,
    max_vertices: int = 2_000_000,
    max_triangles: int = 4_000_000,
) -> Cam3DCalculationMesh:
    """Canonicalize verified fragments without using Viewer tessellation."""
    if not isinstance(fragments, tuple) or any(
        not isinstance(item, Cam3DResolvedSurfaceMesh) for item in fragments
    ):
        raise CamValidationError("CAM 3D mesh fragments are invalid")
    if not isinstance(tolerance, Cam3DTolerancePolicy) or not isinstance(
        source_geometry_fingerprint, GeometryFingerprint
    ):
        raise CamValidationError("CAM 3D mesh build inputs are invalid")
    if type(max_vertices) is not int or type(max_triangles) is not int or min(max_vertices, max_triangles) <= 0:
        raise CamValidationError("CAM 3D mesh size limits are invalid")
    _checkpoint(cancellation)
    if not fragments:
        raise _mesh_error(Cam3DDiagnosticCode.MESH_EMPTY, "No CAM 3D surfaces were tessellated")
    ordered = tuple(sorted(fragments, key=lambda item: item.surface.fingerprint.digest))
    triangle_records: list[
        tuple[
            str,
            tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
            GeometryReferenceId,
            tuple[float, float, float],
        ]
    ] = []
    points_by_key: dict[tuple[int, int, int], Point3] = {}
    epsilon = tolerance.calculation_epsilon
    triangle_count = 0
    for fragment in ordered:
        _checkpoint(cancellation)
        if not fragment.triangles:
            raise _mesh_error(
                Cam3DDiagnosticCode.MESH_EMPTY,
                "A selected CAM 3D surface produced no triangles",
                source=fragment.surface.geometry.reference_id,
            )
        for local_index, raw_triangle in enumerate(fragment.triangles):
            if local_index % 512 == 0:
                _checkpoint(cancellation)
            triangle_count += 1
            if triangle_count > max_triangles:
                raise _mesh_error(Cam3DDiagnosticCode.MESH_TOO_LARGE, "CAM 3D triangle limit exceeded")
            triangle = raw_triangle
            if fragment.surface.orientation is CamSurfaceOrientation.REVERSED:
                triangle = (triangle[0], triangle[2], triangle[1])
            points = tuple(fragment.vertices[index] for index in triangle)
            keys = tuple(_point_key(point, epsilon) for point in points)
            if len(set(keys)) != 3:
                raise _mesh_error(
                    Cam3DDiagnosticCode.MESH_DEGENERATE,
                    "CAM 3D tessellation contains a collapsed triangle",
                    source=fragment.surface.geometry.reference_id,
                    triangle=local_index,
                )
            canonical_points = tuple(_point_from_key(key, epsilon) for key in keys)
            normal = _triangle_normal(canonical_points, epsilon)
            if normal is None:
                raise _mesh_error(
                    Cam3DDiagnosticCode.MESH_DEGENERATE,
                    "CAM 3D tessellation contains a degenerate triangle",
                    source=fragment.surface.geometry.reference_id,
                    triangle=local_index,
                )
            for key, point in zip(keys, canonical_points, strict=True):
                points_by_key[key] = point
            rotations = (keys, (keys[1], keys[2], keys[0]), (keys[2], keys[0], keys[1]))
            canonical_keys = min(rotations)
            triangle_records.append(
                (
                    fragment.surface.fingerprint.digest,
                    canonical_keys,
                    fragment.surface.geometry.reference_id,
                    (normal.x, normal.y, normal.z),
                )
            )
    if len(points_by_key) > max_vertices:
        raise _mesh_error(Cam3DDiagnosticCode.MESH_TOO_LARGE, "CAM 3D vertex limit exceeded")
    sorted_keys = tuple(sorted(points_by_key))
    vertex_index = {key: index for index, key in enumerate(sorted_keys)}
    vertices = tuple(points_by_key[key] for key in sorted_keys)
    records = tuple(sorted(triangle_records, key=lambda item: (item[0], item[1])))
    triangle_indices = tuple(
        tuple(vertex_index[key] for key in record[1]) for record in records
    )
    triangle_sources = tuple(record[2] for record in records)
    triangle_normals = tuple(Vector3(*record[3]) for record in records)
    bounds = BoundingBox(
        min(item.x for item in vertices),
        min(item.y for item in vertices),
        min(item.z for item in vertices),
        max(item.x for item in vertices),
        max(item.y for item in vertices),
        max(item.z for item in vertices),
    )
    statistics = Cam3DStatistics(
        len(set(triangle_sources)), len(vertices), len(triangle_indices)
    )
    identity = {
        "vertices": [item.to_dict() for item in vertices],
        "triangle_indices": [list(item) for item in triangle_indices],
        "triangle_normals": [item.to_dict() for item in triangle_normals],
        "triangle_sources": [str(item) for item in triangle_sources],
        "bounding_box": _bounds_to_dict(bounds),
        "chordal_tolerance": tolerance.chordal_tolerance,
        "angular_tolerance": tolerance.angular_tolerance,
        "unit": LengthUnit.MM.value,
        "source_geometry_fingerprint": source_geometry_fingerprint.to_dict(),
        "statistics": statistics.to_dict(),
    }
    fingerprint = GeometryFingerprint.from_payload(identity)
    return Cam3DCalculationMesh(
        vertices,
        triangle_indices,
        triangle_normals,
        triangle_sources,
        bounds,
        tolerance.chordal_tolerance,
        tolerance.angular_tolerance,
        LengthUnit.MM,
        source_geometry_fingerprint,
        fingerprint,
        statistics,
    )


def _checkpoint(cancellation: Callable[[], bool] | None) -> None:
    if cancellation is not None and cancellation():
        diagnostic = Cam3DDiagnostic(
            Cam3DDiagnosticCode.CANCELLED,
            Cam3DDiagnosticSeverity.WARNING,
            "CAM 3D calculation was cancelled",
        )
        raise Cam3DCancelledError(diagnostic)


def _mesh_error(
    code: Cam3DDiagnosticCode,
    message: str,
    *,
    source: GeometryReferenceId | None = None,
    triangle: int | None = None,
) -> Cam3DMeshError:
    return Cam3DMeshError(
        Cam3DDiagnostic(
            code,
            Cam3DDiagnosticSeverity.ERROR,
            message,
            source_reference_id=source,
            triangle_index=triangle,
        )
    )


def _point_key(point: Point3, epsilon: float) -> tuple[int, int, int]:
    values = (point.x, point.y, point.z)
    if not all(math.isfinite(value) for value in values):
        raise _mesh_error(Cam3DDiagnosticCode.MESH_NON_FINITE, "CAM 3D vertex is non-finite")
    return tuple(round(value / epsilon) for value in values)


def _point_from_key(key: tuple[int, int, int], epsilon: float) -> Point3:
    return Point3(*(0.0 if value == 0 else value * epsilon for value in key), LengthUnit.MM)


def _triangle_normal(
    points: tuple[Point3, Point3, Point3] | tuple[Point3, ...], epsilon: float
) -> Vector3 | None:
    first, second, third = points
    left = Vector3(second.x - first.x, second.y - first.y, second.z - first.z)
    right = Vector3(third.x - first.x, third.y - first.y, third.z - first.z)
    cross = left.cross(right)
    if cross.magnitude <= epsilon * epsilon:
        return None
    return Vector3(cross.x / cross.magnitude, cross.y / cross.magnitude, cross.z / cross.magnitude)


def _bounds_to_dict(bounds: BoundingBox) -> dict[str, float]:
    return {
        "x_min": bounds.x_min,
        "y_min": bounds.y_min,
        "z_min": bounds.z_min,
        "x_max": bounds.x_max,
        "y_max": bounds.y_max,
        "z_max": bounds.z_max,
    }


def _bounds_from_dict(data: dict[str, Any]) -> BoundingBox:
    fields = {"x_min", "y_min", "z_min", "x_max", "y_max", "z_max"}
    if not isinstance(data, dict) or set(data) != fields:
        raise CamValidationError("CAM 3D bounding-box payload is malformed")
    return BoundingBox(**data)
