"""Deterministic primitive-to-triangle collision math for Parallel safety."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.ids import GeometryReferenceId
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import LengthUnit

from .safety_models import ParallelGeometrySource, ParallelToolComponent


class ParallelPrimitiveKind(StrEnum):
    SPHERE = "sphere"
    CYLINDER = "cylinder"
    FRUSTUM = "frustum"


@dataclass(frozen=True, slots=True)
class ParallelCollisionPrimitive:
    """Fixed-axis primitive with offsets relative to the ball-center pose."""

    kind: ParallelPrimitiveKind
    component: ParallelToolComponent
    axial_start_mm: float
    axial_end_mm: float
    lower_radius_mm: float
    upper_radius_mm: float
    label: str
    approximation: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ParallelPrimitiveKind) or not isinstance(
            self.component, ParallelToolComponent
        ):
            raise CamValidationError("Parallel collision primitive kind is invalid")
        values = (
            self.axial_start_mm,
            self.axial_end_mm,
            self.lower_radius_mm,
            self.upper_radius_mm,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in values
        ):
            raise CamValidationError("Parallel collision primitive dimensions are invalid")
        if self.axial_end_mm < self.axial_start_mm or min(values[2:]) < 0.0:
            raise CamValidationError("Parallel collision primitive dimensions are invalid")
        if max(values[2:]) <= 0.0:
            raise CamValidationError("Parallel collision primitive radius must be positive")
        if self.kind is ParallelPrimitiveKind.SPHERE and not math.isclose(
            self.axial_start_mm, self.axial_end_mm, rel_tol=0.0, abs_tol=1.0e-12
        ):
            raise CamValidationError("Parallel sphere must have one axial center")
        if not isinstance(self.label, str) or not self.label.strip():
            raise CamValidationError("Parallel collision primitive label is invalid")
        if not isinstance(self.approximation, str) or not self.approximation.strip():
            raise CamValidationError("Parallel collision primitive support is invalid")

    @property
    def radius_mm(self) -> float:
        return max(self.lower_radius_mm, self.upper_radius_mm)

    def to_dict(self) -> dict[str, str | float]:
        return {
            "kind": self.kind.value,
            "component": self.component.value,
            "axial_start_mm": self.axial_start_mm,
            "axial_end_mm": self.axial_end_mm,
            "lower_radius_mm": self.lower_radius_mm,
            "upper_radius_mm": self.upper_radius_mm,
            "label": self.label,
            "approximation": self.approximation,
        }


@dataclass(frozen=True, slots=True)
class ParallelAabb:
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    def __post_init__(self) -> None:
        if any(
            not isinstance(values, tuple)
            or len(values) != 3
            or any(not math.isfinite(value) for value in values)
            for values in (self.minimum, self.maximum)
        ) or any(a > b for a, b in zip(self.minimum, self.maximum, strict=True)):
            raise CamValidationError("Parallel collision AABB is invalid")

    def expanded(self, margin: float) -> "ParallelAabb":
        if not math.isfinite(margin) or margin < 0.0:
            raise CamValidationError("Parallel AABB margin is invalid")
        return ParallelAabb(
            tuple(value - margin for value in self.minimum),
            tuple(value + margin for value in self.maximum),
        )

    def overlaps(self, other: "ParallelAabb") -> bool:
        return all(
            first_min <= second_max and first_max >= second_min
            for first_min, first_max, second_min, second_max in zip(
                self.minimum,
                self.maximum,
                other.minimum,
                other.maximum,
                strict=True,
            )
        )


@dataclass(frozen=True, slots=True)
class ParallelCollisionTriangle:
    triangle_index: int
    face_id: GeometryReferenceId
    geometry_source: ParallelGeometrySource
    points: tuple[Point3, Point3, Point3]
    bounds: ParallelAabb

    def __post_init__(self) -> None:
        if type(self.triangle_index) is not int or self.triangle_index < 0:
            raise CamValidationError("Parallel collision triangle index is invalid")
        if not isinstance(self.face_id, GeometryReferenceId) or not isinstance(
            self.geometry_source, ParallelGeometrySource
        ):
            raise CamValidationError("Parallel collision triangle provenance is invalid")
        if any(
            not isinstance(point, Point3) or point.unit is not LengthUnit.MM
            for point in self.points
        ) or not isinstance(self.bounds, ParallelAabb):
            raise CamValidationError("Parallel collision triangle geometry is invalid")


def triangle_bounds(points: tuple[Point3, Point3, Point3]) -> ParallelAabb:
    return ParallelAabb(
        tuple(min(getattr(point, axis) for point in points) for axis in ("x", "y", "z")),
        tuple(max(getattr(point, axis) for point in points) for axis in ("x", "y", "z")),
    )


def swept_primitive_bounds(
    primitive: ParallelCollisionPrimitive,
    start: Point3,
    end: Point3,
    axis: Vector3,
    margin: float,
) -> ParallelAabb:
    radius = primitive.radius_mm + margin
    points = tuple(
        _translated(position, axis, axial)
        for position in (start, end)
        for axial in (primitive.axial_start_mm, primitive.axial_end_mm)
    )
    return ParallelAabb(
        tuple(min(getattr(point, name) for point in points) - radius for name in ("x", "y", "z")),
        tuple(max(getattr(point, name) for point in points) + radius for name in ("x", "y", "z")),
    )


def swept_axis_triangle_distance(
    primitive: ParallelCollisionPrimitive,
    start: Point3,
    end: Point3,
    axis: Vector3,
    triangle: ParallelCollisionTriangle,
) -> float:
    """Exact axis-sweep distance; primitive radius is applied by the caller."""
    lower_start = _translated(start, axis, primitive.axial_start_mm)
    upper_start = _translated(start, axis, primitive.axial_end_mm)
    lower_end = _translated(end, axis, primitive.axial_start_mm)
    upper_end = _translated(end, axis, primitive.axial_end_mm)
    if primitive.kind is ParallelPrimitiveKind.SPHERE:
        return segment_triangle_distance(lower_start, lower_end, triangle.points)
    if _distance(start, end) <= 1.0e-15:
        return segment_triangle_distance(lower_start, upper_start, triangle.points)
    first = (lower_start, upper_start, upper_end)
    second = (lower_start, upper_end, lower_end)
    return min(
        triangle_triangle_distance(first, triangle.points),
        triangle_triangle_distance(second, triangle.points),
    )


def closest_point_on_triangle(
    point: Point3,
    triangle: tuple[Point3, Point3, Point3],
) -> tuple[Point3, float]:
    """Return the Ericson closest point and its Euclidean distance."""
    a, b, c = triangle
    ab, ac, ap = _vector(a, b), _vector(a, c), _vector(a, point)
    d1, d2 = ab.dot(ap), ac.dot(ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a, _distance(point, a)
    bp = _vector(b, point)
    d3, d4 = ab.dot(bp), ac.dot(bp)
    if d3 >= 0.0 and d4 <= d3:
        return b, _distance(point, b)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        ratio = d1 / (d1 - d3)
        value = _lerp(a, b, ratio)
        return value, _distance(point, value)
    cp = _vector(c, point)
    d5, d6 = ab.dot(cp), ac.dot(cp)
    if d6 >= 0.0 and d5 <= d6:
        return c, _distance(point, c)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        ratio = d2 / (d2 - d6)
        value = _lerp(a, c, ratio)
        return value, _distance(point, value)
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and d4 - d3 >= 0.0 and d5 - d6 >= 0.0:
        ratio = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        value = _lerp(b, c, ratio)
        return value, _distance(point, value)
    denominator = va + vb + vc
    if abs(denominator) <= 1.0e-30:
        candidates = tuple(
            _closest_point_on_segment(point, first, second)
            for first, second in ((a, b), (b, c), (c, a))
        )
        value = min(candidates, key=lambda item: _distance(point, item))
        return value, _distance(point, value)
    inverse = 1.0 / denominator
    v, w = vb * inverse, vc * inverse
    value = Point3(
        a.x + ab.x * v + ac.x * w,
        a.y + ab.y * v + ac.y * w,
        a.z + ab.z * v + ac.z * w,
        LengthUnit.MM,
    )
    return value, _distance(point, value)


def segment_triangle_distance(
    first: Point3,
    second: Point3,
    triangle: tuple[Point3, Point3, Point3],
) -> float:
    if _segment_intersects_triangle(first, second, triangle):
        return 0.0
    distances = [
        closest_point_on_triangle(first, triangle)[1],
        closest_point_on_triangle(second, triangle)[1],
    ]
    for edge_first, edge_second in _edges(triangle):
        distances.append(segment_segment_distance(first, second, edge_first, edge_second))
    return min(distances)


def triangle_triangle_distance(
    first: tuple[Point3, Point3, Point3],
    second: tuple[Point3, Point3, Point3],
) -> float:
    values = [
        segment_triangle_distance(edge_first, edge_second, second)
        for edge_first, edge_second in _edges(first)
    ]
    values.extend(
        segment_triangle_distance(edge_first, edge_second, first)
        for edge_first, edge_second in _edges(second)
    )
    return min(values)


def segment_segment_distance(
    first_start: Point3,
    first_end: Point3,
    second_start: Point3,
    second_end: Point3,
) -> float:
    """Stable closest distance between two finite 3D segments."""
    u = _vector(first_start, first_end)
    v = _vector(second_start, second_end)
    w = _vector(second_start, first_start)
    a, b, c = u.dot(u), u.dot(v), v.dot(v)
    d, e = u.dot(w), v.dot(w)
    denominator = a * c - b * b
    small = 1.0e-24
    s_numerator, s_denominator = denominator, denominator
    t_numerator, t_denominator = denominator, denominator
    if denominator < small:
        s_numerator, s_denominator = 0.0, 1.0
        t_numerator, t_denominator = e, c
    else:
        s_numerator = b * e - c * d
        t_numerator = a * e - b * d
        if s_numerator < 0.0:
            s_numerator = 0.0
            t_numerator, t_denominator = e, c
        elif s_numerator > s_denominator:
            s_numerator = s_denominator
            t_numerator, t_denominator = e + b, c
    if t_numerator < 0.0:
        t_numerator = 0.0
        if -d < 0.0:
            s_numerator = 0.0
        elif -d > a:
            s_numerator = s_denominator
        else:
            s_numerator, s_denominator = -d, a
    elif t_numerator > t_denominator:
        t_numerator = t_denominator
        if -d + b < 0.0:
            s_numerator = 0.0
        elif -d + b > a:
            s_numerator = s_denominator
        else:
            s_numerator, s_denominator = -d + b, a
    first_ratio = 0.0 if abs(s_numerator) < small else s_numerator / s_denominator
    second_ratio = 0.0 if abs(t_numerator) < small else t_numerator / t_denominator
    delta = Vector3(
        w.x + first_ratio * u.x - second_ratio * v.x,
        w.y + first_ratio * u.y - second_ratio * v.y,
        w.z + first_ratio * u.z - second_ratio * v.z,
    )
    return delta.magnitude


def _segment_intersects_triangle(
    start: Point3,
    end: Point3,
    triangle: tuple[Point3, Point3, Point3],
) -> bool:
    direction = _vector(start, end)
    edge1 = _vector(triangle[0], triangle[1])
    edge2 = _vector(triangle[0], triangle[2])
    cross = direction.cross(edge2)
    determinant = edge1.dot(cross)
    epsilon = 1.0e-12
    if abs(determinant) <= epsilon:
        return False
    inverse = 1.0 / determinant
    offset = _vector(triangle[0], start)
    u = offset.dot(cross) * inverse
    if u < -epsilon or u > 1.0 + epsilon:
        return False
    q = offset.cross(edge1)
    v = direction.dot(q) * inverse
    if v < -epsilon or u + v > 1.0 + epsilon:
        return False
    ratio = edge2.dot(q) * inverse
    return -epsilon <= ratio <= 1.0 + epsilon


def _edges(
    triangle: tuple[Point3, Point3, Point3],
) -> tuple[tuple[Point3, Point3], ...]:
    return (
        (triangle[0], triangle[1]),
        (triangle[1], triangle[2]),
        (triangle[2], triangle[0]),
    )


def _closest_point_on_segment(point: Point3, first: Point3, second: Point3) -> Point3:
    direction = _vector(first, second)
    squared = direction.dot(direction)
    if squared <= 1.0e-30:
        return first
    ratio = max(0.0, min(1.0, _vector(first, point).dot(direction) / squared))
    return _lerp(first, second, ratio)


def _translated(point: Point3, axis: Vector3, distance: float) -> Point3:
    return Point3(
        point.x + axis.x * distance,
        point.y + axis.y * distance,
        point.z + axis.z * distance,
        LengthUnit.MM,
    )


def _vector(first: Point3, second: Point3) -> Vector3:
    return Vector3(second.x - first.x, second.y - first.y, second.z - first.z)


def _lerp(first: Point3, second: Point3, ratio: float) -> Point3:
    return Point3(
        first.x + (second.x - first.x) * ratio,
        first.y + (second.y - first.y) * ratio,
        first.z + (second.z - first.z) * ratio,
        LengthUnit.MM,
    )


def _distance(first: Point3, second: Point3) -> float:
    return math.dist((first.x, first.y, first.z), (second.x, second.y, second.z))
