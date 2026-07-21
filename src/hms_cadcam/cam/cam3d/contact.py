"""Limited, testable CAM 3D tool-contact primitives for Stage 8A.1."""

from __future__ import annotations

import math
from dataclasses import dataclass

from hms_cadcam.cam.cam3d.mesh import Cam3DCalculationMesh
from hms_cadcam.cam.cam3d.models import (
    Cam3DDiagnostic,
    Cam3DDiagnosticCode,
    Cam3DDiagnosticSeverity,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.ids import GeometryReferenceId
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.tooling import (
    BallEndGeometry,
    CylindricalGeometry,
    ToolDefinition,
)
from hms_cadcam.cam.domain.units import LengthUnit


class Cam3DContactError(RuntimeError):
    """Unsupported or invalid contact calculation with structured evidence."""

    def __init__(self, diagnostic: Cam3DDiagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class Cam3DProjectedPoint:
    """Orthogonal projection and barycentric evidence on one triangle."""

    point: Point3
    barycentric: tuple[float, float, float]

    def __post_init__(self) -> None:
        if not isinstance(self.point, Point3) or self.point.unit is not LengthUnit.MM:
            raise CamValidationError("Projected CAM 3D point is invalid")
        if (
            not isinstance(self.barycentric, tuple)
            or len(self.barycentric) != 3
            or not all(math.isfinite(value) for value in self.barycentric)
            or not math.isclose(sum(self.barycentric), 1.0, abs_tol=1.0e-8)
        ):
            raise CamValidationError("Projected CAM 3D barycentric values are invalid")


@dataclass(frozen=True, slots=True)
class Cam3DToolContact:
    """One valid contact and corresponding tool-center point."""

    contact_point: Point3
    tool_center_point: Point3
    surface_normal: Vector3
    tool_axis: Vector3
    source_triangle_index: int
    source_surface_id: GeometryReferenceId
    local_curvature: float | None = None
    evidence: tuple[tuple[str, str], ...] = ()
    contact_valid: bool = True

    def __post_init__(self) -> None:
        if any(
            not isinstance(item, Point3) or item.unit is not LengthUnit.MM
            for item in (self.contact_point, self.tool_center_point)
        ):
            raise CamValidationError("CAM 3D contact points are invalid")
        for value, name in (
            (self.surface_normal, "surface normal"),
            (self.tool_axis, "tool axis"),
        ):
            if not isinstance(value, Vector3) or not math.isclose(
                value.magnitude, 1.0, rel_tol=0.0, abs_tol=1.0e-9
            ):
                raise CamValidationError(f"CAM 3D contact {name} is invalid")
        if type(self.source_triangle_index) is not int or self.source_triangle_index < 0:
            raise CamValidationError("CAM 3D contact triangle index is invalid")
        if not isinstance(self.source_surface_id, GeometryReferenceId):
            raise CamValidationError("CAM 3D contact surface source is invalid")
        if self.local_curvature is not None and (
            not math.isfinite(self.local_curvature) or self.local_curvature < 0.0
        ):
            raise CamValidationError("CAM 3D local curvature evidence is invalid")
        if type(self.contact_valid) is not bool or not self.contact_valid:
            raise CamValidationError("Invalid contacts must not be published")
        try:
            normalized = tuple(sorted(self.evidence))
        except TypeError as error:
            raise CamValidationError("CAM 3D contact evidence is invalid") from error
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(value, str) and value.strip() for value in item)
            for item in normalized
        ):
            raise CamValidationError("CAM 3D contact evidence is invalid")
        object.__setattr__(self, "evidence", normalized)


def project_point_to_triangle(
    point: Point3,
    first: Point3,
    second: Point3,
    third: Point3,
    *,
    tolerance: float,
) -> Cam3DProjectedPoint:
    """Project orthogonally and require the result to remain inside the triangle."""
    points = (point, first, second, third)
    if any(not isinstance(item, Point3) or item.unit is not LengthUnit.MM for item in points):
        raise _contact_error("CAM 3D projection requires finite MM points")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance <= 0.0:
        raise _contact_error("CAM 3D projection tolerance is invalid")
    edge_a = _vector(first, second)
    edge_b = _vector(first, third)
    normal = edge_a.cross(edge_b)
    squared_normal = normal.dot(normal)
    if squared_normal <= tolerance * tolerance:
        raise _contact_error("Cannot project onto a degenerate triangle")
    offset = _vector(first, point)
    scale = offset.dot(normal) / squared_normal
    projected = Point3(
        point.x - scale * normal.x,
        point.y - scale * normal.y,
        point.z - scale * normal.z,
        LengthUnit.MM,
    )
    relative = _vector(first, projected)
    aa = edge_a.dot(edge_a)
    ab = edge_a.dot(edge_b)
    bb = edge_b.dot(edge_b)
    ar = edge_a.dot(relative)
    br = edge_b.dot(relative)
    denominator = aa * bb - ab * ab
    if abs(denominator) <= tolerance * tolerance:
        raise _contact_error("Cannot calculate triangle barycentric coordinates")
    second_weight = (bb * ar - ab * br) / denominator
    third_weight = (aa * br - ab * ar) / denominator
    first_weight = 1.0 - second_weight - third_weight
    barycentric = (first_weight, second_weight, third_weight)
    if any(value < -tolerance or value > 1.0 + tolerance for value in barycentric):
        raise _contact_error("Projected point falls outside the source triangle")
    return Cam3DProjectedPoint(projected, barycentric)


def calculate_tool_contact(
    mesh: Cam3DCalculationMesh,
    triangle_index: int,
    sample_point: Point3,
    tool: ToolDefinition,
    tool_axis: Vector3,
    *,
    contact_tolerance: float,
) -> Cam3DToolContact:
    """Calculate one limited ball-end or planar flat-end contact."""
    if not isinstance(mesh, Cam3DCalculationMesh):
        raise _contact_error("CAM 3D contact mesh is invalid")
    if type(triangle_index) is not int or not 0 <= triangle_index < len(mesh.triangle_indices):
        raise _contact_error("CAM 3D contact triangle index is invalid", triangle=triangle_index if isinstance(triangle_index, int) and triangle_index >= 0 else None)
    if not isinstance(tool, ToolDefinition):
        raise _contact_error("CAM 3D tool definition is missing", code=Cam3DDiagnosticCode.TOOL_UNSUPPORTED)
    if not isinstance(tool_axis, Vector3) or not math.isclose(
        tool_axis.magnitude, 1.0, rel_tol=0.0, abs_tol=1.0e-9
    ):
        raise _contact_error("CAM 3D tool axis is invalid")
    triangle = mesh.triangle_indices[triangle_index]
    projected = project_point_to_triangle(
        sample_point,
        *(mesh.vertices[index] for index in triangle),
        tolerance=contact_tolerance,
    )
    normal = mesh.triangle_normals[triangle_index]
    source = mesh.triangle_sources[triangle_index]
    geometry = tool.cutting_geometry
    if isinstance(geometry, BallEndGeometry):
        if geometry.diameter.unit is not LengthUnit.MM:
            raise _contact_error("Ball-end tool must use MM", code=Cam3DDiagnosticCode.TOOL_UNSUPPORTED)
        radius = geometry.diameter.value / 2.0
        center = _translated(projected.point, normal, radius)
        evidence = (("tool_geometry", "ball_end"), ("offset", f"{radius:.12g}"))
    elif isinstance(geometry, CylindricalGeometry):
        if geometry.diameter.unit is not LengthUnit.MM:
            raise _contact_error("Flat-end tool must use MM", code=Cam3DDiagnosticCode.TOOL_UNSUPPORTED)
        alignment = normal.dot(tool_axis)
        if alignment < 1.0 - contact_tolerance:
            raise _contact_error(
                "Flat-end contact v1 supports only a plane normal aligned with the tool axis",
                code=Cam3DDiagnosticCode.TOOL_UNSUPPORTED,
                triangle=triangle_index,
                source=source,
            )
        center = projected.point
        evidence = (("tool_geometry", "flat_end"), ("alignment", f"{alignment:.12g}"))
    else:
        raise _contact_error(
            "Tool geometry is outside the limited Stage 8A.1 contact primitive",
            code=Cam3DDiagnosticCode.TOOL_UNSUPPORTED,
            triangle=triangle_index,
            source=source,
        )
    if not all(
        math.isfinite(value)
        for value in (center.x, center.y, center.z, projected.point.x, projected.point.y, projected.point.z)
    ):
        raise _contact_error("CAM 3D tool contact is non-finite")
    return Cam3DToolContact(
        projected.point,
        center,
        normal,
        tool_axis,
        triangle_index,
        source,
        evidence=evidence,
    )


def _vector(first: Point3, second: Point3) -> Vector3:
    return Vector3(second.x - first.x, second.y - first.y, second.z - first.z)


def _translated(point: Point3, direction: Vector3, distance: float) -> Point3:
    if not math.isfinite(distance) or distance < 0.0:
        raise _contact_error("CAM 3D contact offset is impossible")
    return Point3(
        point.x + direction.x * distance,
        point.y + direction.y * distance,
        point.z + direction.z * distance,
        LengthUnit.MM,
    )


def _contact_error(
    message: str,
    *,
    code: Cam3DDiagnosticCode = Cam3DDiagnosticCode.CONTACT_INVALID,
    triangle: int | None = None,
    source: GeometryReferenceId | None = None,
) -> Cam3DContactError:
    return Cam3DContactError(
        Cam3DDiagnostic(
            code,
            Cam3DDiagnosticSeverity.ERROR,
            message,
            source_reference_id=source,
            triangle_index=triangle,
        )
    )
