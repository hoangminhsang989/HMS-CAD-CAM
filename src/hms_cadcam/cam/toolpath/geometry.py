"""Controller-neutral toolpath poses, bounds and arc geometry."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hms_cadcam.cam.domain.errors import CamInvariantError, CamUnitError, CamValidationError
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import LengthUnit

GEOMETRY_TOLERANCE = 1.0e-8


class CoordinateSpace(StrEnum):
    SETUP_WCS = "setup_wcs"
    MACHINE = "machine"
    TOOL_LOCAL = "tool_local"
    MODEL_SOURCE = "model_source"


def _same_point(first: Point3, second: Point3, tolerance: float = GEOMETRY_TOLERANCE) -> bool:
    return first.unit is second.unit and distance(first, second) <= tolerance


def distance(first: Point3, second: Point3) -> float:
    """Return Euclidean distance without implicit unit conversion."""
    if not isinstance(first, Point3) or not isinstance(second, Point3):
        raise CamValidationError("Distance requires two points")
    if first.unit is not second.unit or first.unit is LengthUnit.UNKNOWN:
        raise CamUnitError("Distance points require one known unit")
    return math.sqrt((second.x - first.x) ** 2 + (second.y - first.y) ** 2 + (second.z - first.z) ** 2)


@dataclass(frozen=True, slots=True)
class Pose:
    """Tool-tip position and normalized tool direction; no IK or controller axes."""

    position: Point3
    tool_axis: Vector3

    def __post_init__(self) -> None:
        if not isinstance(self.position, Point3) or self.position.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Pose position requires a known unit")
        if not isinstance(self.tool_axis, Vector3) or self.tool_axis.magnitude <= 1.0e-15:
            raise CamValidationError("Pose tool axis must be non-zero")
        magnitude = self.tool_axis.magnitude
        object.__setattr__(self, "tool_axis", Vector3(
            self.tool_axis.x / magnitude, self.tool_axis.y / magnitude, self.tool_axis.z / magnitude
        ))

    def to_dict(self) -> dict[str, Any]:
        return {"position": self.position.to_dict(), "tool_axis": self.tool_axis.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pose":
        if not isinstance(data, dict) or set(data) != {"position", "tool_axis"}:
            raise CamValidationError("Pose payload is malformed")
        return cls(Point3.from_dict(data["position"]), Vector3.from_dict(data["tool_axis"]))


def same_pose(first: Pose, second: Pose, tolerance: float = GEOMETRY_TOLERANCE) -> bool:
    return _same_point(first.position, second.position, tolerance) and all(
        abs(a - b) <= tolerance for a, b in zip(
            (first.tool_axis.x, first.tool_axis.y, first.tool_axis.z),
            (second.tool_axis.x, second.tool_axis.y, second.tool_axis.z), strict=True
        )
    )


@dataclass(frozen=True, slots=True)
class Bounds3:
    minimum: Point3
    maximum: Point3

    def __post_init__(self) -> None:
        if not isinstance(self.minimum, Point3) or not isinstance(self.maximum, Point3):
            raise CamValidationError("Bounds require points")
        if self.minimum.unit is not self.maximum.unit or self.minimum.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Bounds require one known unit")
        if any(low > high for low, high in zip(
            (self.minimum.x, self.minimum.y, self.minimum.z),
            (self.maximum.x, self.maximum.y, self.maximum.z), strict=True
        )):
            raise CamInvariantError("Bounds minimum exceeds maximum")

    @classmethod
    def from_points(cls, points: tuple[Point3, ...]) -> "Bounds3":
        if not points:
            raise CamValidationError("Bounds require at least one point")
        unit = points[0].unit
        if unit is LengthUnit.UNKNOWN or any(item.unit is not unit for item in points):
            raise CamUnitError("Bounds points require one known unit")
        return cls(Point3(min(p.x for p in points), min(p.y for p in points), min(p.z for p in points), unit),
                   Point3(max(p.x for p in points), max(p.y for p in points), max(p.z for p in points), unit))

    @classmethod
    def union(cls, values: tuple["Bounds3", ...]) -> "Bounds3":
        if not values:
            raise CamValidationError("Bounds union requires values")
        return cls.from_points(tuple(point for item in values for point in (item.minimum, item.maximum)))

    def to_dict(self) -> dict[str, Any]:
        return {"minimum": self.minimum.to_dict(), "maximum": self.maximum.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Bounds3":
        if not isinstance(data, dict) or set(data) != {"minimum", "maximum"}:
            raise CamValidationError("Bounds payload is malformed")
        return cls(Point3.from_dict(data["minimum"]), Point3.from_dict(data["maximum"]))


def validate_arc(start: Pose, end: Pose, center: Point3, normal: Vector3, sweep_radians: float) -> tuple[float, Vector3, Vector3]:
    """Validate explicit arc and return radius plus in-plane basis vectors."""
    if not all(isinstance(item, expected) for item, expected in ((start, Pose), (end, Pose), (center, Point3), (normal, Vector3))):
        raise CamValidationError("Arc geometry is invalid")
    if center.unit is not start.position.unit or end.position.unit is not center.unit:
        raise CamUnitError("Arc geometry requires one unit")
    if isinstance(sweep_radians, bool) or not isinstance(sweep_radians, (int, float)) or not math.isfinite(sweep_radians):
        raise CamValidationError("Arc sweep must be finite")
    sweep = float(sweep_radians)
    if abs(sweep) <= GEOMETRY_TOLERANCE:
        raise CamInvariantError("Arc sweep must be non-zero")
    if abs(sweep) >= math.tau - GEOMETRY_TOLERANCE:
        raise CamInvariantError("Full-circle and multi-turn arcs are unsupported in IR v1")
    if normal.magnitude <= 1.0e-15:
        raise CamValidationError("Arc plane normal must be non-zero")
    n = Vector3(normal.x / normal.magnitude, normal.y / normal.magnitude, normal.z / normal.magnitude)
    start_vector = Vector3(start.position.x - center.x, start.position.y - center.y, start.position.z - center.z)
    end_vector = Vector3(end.position.x - center.x, end.position.y - center.y, end.position.z - center.z)
    radius = start_vector.magnitude
    if radius <= GEOMETRY_TOLERANCE or not math.isclose(radius, end_vector.magnitude, rel_tol=0.0, abs_tol=GEOMETRY_TOLERANCE):
        raise CamInvariantError("Arc start and end radii do not match")
    if abs(start_vector.dot(n)) > GEOMETRY_TOLERANCE or abs(end_vector.dot(n)) > GEOMETRY_TOLERANCE:
        raise CamInvariantError("Arc points are not coplanar")
    if not same_pose(Pose(start.position, start.tool_axis), Pose(start.position, end.tool_axis)):
        raise CamInvariantError("IR v1 arc requires constant tool orientation")
    u = Vector3(start_vector.x / radius, start_vector.y / radius, start_vector.z / radius)
    v = n.cross(u)
    expected = Point3(center.x + radius * (u.x * math.cos(sweep) + v.x * math.sin(sweep)),
                      center.y + radius * (u.y * math.cos(sweep) + v.y * math.sin(sweep)),
                      center.z + radius * (u.z * math.cos(sweep) + v.z * math.sin(sweep)), center.unit)
    if not _same_point(expected, end.position):
        raise CamInvariantError("Arc end does not match center, normal and signed sweep")
    return radius, u, v


def arc_bounds(start: Pose, end: Pose, center: Point3, normal: Vector3, sweep_radians: float) -> Bounds3:
    radius, u, v = validate_arc(start, end, center, normal, sweep_radians)
    sweep = float(sweep_radians)

    def included(angle: float) -> bool:
        normalized = angle % math.tau
        if sweep > 0:
            return normalized <= sweep + GEOMETRY_TOLERANCE
        return ((-angle) % math.tau) <= -sweep + GEOMETRY_TOLERANCE

    angles = [0.0, sweep]
    for component_u, component_v in zip((u.x, u.y, u.z), (v.x, v.y, v.z), strict=True):
        candidate = math.atan2(component_v, component_u)
        for angle in (candidate, candidate + math.pi):
            if included(angle):
                angles.append(angle if sweep > 0 else -((-angle) % math.tau))
    points = tuple(Point3(center.x + radius * (u.x * math.cos(a) + v.x * math.sin(a)),
                          center.y + radius * (u.y * math.cos(a) + v.y * math.sin(a)),
                          center.z + radius * (u.z * math.cos(a) + v.z * math.sin(a)), center.unit) for a in angles)
    return Bounds3.from_points(points)
