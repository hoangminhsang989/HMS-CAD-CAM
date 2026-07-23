"""Deterministic mesh-backed implicit-field tracing for Z-Level finishing.

The CAD adapter supplies verified tessellation and face provenance through
``Cam3DCalculationMesh``.  Each triangle contributes the exact affine
implicit field for its differential normal; trim classification and graph
assembly then fail closed on ambiguous topology.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable

from hms_cadcam.cam.cam3d.context import Cam3DCalculationContext
from hms_cadcam.cam.cam3d.models import MachiningBoundary3D
from hms_cadcam.cam.cam3d.mesh import Cam3DCalculationMesh
from hms_cadcam.cam.cam3d.zlevel.models import (
    ContactResolver,
    ZLevelBoundaryClassification,
    ZLevelContour,
    ZLevelFinishingError,
    ZLevelFinishingParameters,
    ZLevelLoopType,
    ZLevelMachiningFrame,
    ZLevelOrientation,
    ZLevelPass,
    ZLevelPathPoint,
    ZLevelPreview,
    ZLevelRegionBounds,
    ZLevelSchedule,
    ZLevelStatistics,
)
from hms_cadcam.cam.domain.ids import GeometryReferenceId
from hms_cadcam.cam.domain.operation import DiagnosticCode
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import LengthUnit

DEFAULT_MAX_LEVELS = 10_000
DEFAULT_MAX_FACES = 100_000
DEFAULT_MAX_CONTOURS = 100_000
DEFAULT_MAX_POINTS = 1_000_000
DEFAULT_MAX_SUBDIVISIONS = 32


@dataclass(frozen=True, slots=True)
class _RawSegment:
    first: Point3
    second: Point3
    normal: Vector3
    source: GeometryReferenceId
    triangle_index: int


def build_machining_frame(
    context: Cam3DCalculationContext,
    parameters: ZLevelFinishingParameters | None = None,
) -> ZLevelMachiningFrame:
    """Resolve the setup WCS to the explicit U/V/W machining frame."""
    if not isinstance(context, Cam3DCalculationContext):
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_WORKPLANE, "Z-Level cần một CAM 3D context hiện hành.")
    frame = parameters.machining_frame if parameters is not None else None
    if frame is None:
        wcs = context.machining_zone.wcs
        frame = ZLevelMachiningFrame(wcs.origin, wcs.x_axis, wcs.y_axis, wcs.z_axis)
    if abs(frame.w_axis.dot(context.machining_zone.tool_axis) - 1.0) > 1.0e-8:
        raise ZLevelFinishingError(
            DiagnosticCode.Z_LEVEL_INVALID_WORKPLANE,
            "Trục W của Z-Level phải trùng trục dao cố định của Setup.",
        )
    return frame


def calculate_region_bounds(
    mesh: Cam3DCalculationMesh,
    frame: ZLevelMachiningFrame,
    context: Cam3DCalculationContext,
    *,
    tool_radius_mm: float = 0.0,
    allowance_mm: float = 0.0,
    max_faces: int = DEFAULT_MAX_FACES,
) -> ZLevelRegionBounds:
    """Return selected-face bounds in machining coordinates.

    W bounds are tool-center bounds, including the signed ball-normal offset.
    """
    if not isinstance(mesh, Cam3DCalculationMesh):
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_NO_GEOMETRY, "Không có calculation mesh cho Z-Level.")
    if not isinstance(frame, ZLevelMachiningFrame):
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_WORKPLANE, "Machining frame Z-Level không hợp lệ.")
    selected = {
        item.geometry.reference_id
        for item in context.machining_zone.part_surfaces.selection.surfaces
    }
    if not selected:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_NO_GEOMETRY, "Chưa chọn mặt gia công Z-Level.")
    if len(selected) > max_faces:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_CONTOUR_COUNT_EXCEEDED, "Số mặt Z-Level vượt guardrail.")
    radius = _finite_positive(tool_radius_mm, "bán kính dao")
    allowance = _finite_nonnegative(allowance_mm, "lượng dư")
    points: list[tuple[float, float, float]] = []
    for index, triangle in enumerate(mesh.triangle_indices):
        if index % 512 == 0:
            _checkpoint(None)
        if mesh.triangle_sources[index] not in selected:
            continue
        normal = mesh.triangle_normals[index]
        offset = radius + allowance
        for vertex_index in triangle:
            u, v, w = frame.coordinates(mesh.vertices[vertex_index])
            points.append((u, v, w + offset * normal.dot(frame.w_axis)))
    if not points:
        raise ZLevelFinishingError(
            DiagnosticCode.Z_LEVEL_INVALID_FACE_REFERENCE,
            "Không tìm thấy tam giác thuộc các mặt Z-Level đã chọn.",
        )
    return ZLevelRegionBounds(
        min(item[0] for item in points),
        max(item[0] for item in points),
        min(item[1] for item in points),
        max(item[1] for item in points),
        min(item[2] for item in points),
        max(item[2] for item in points),
    )


def plan_level_schedule(
    top_level: float,
    bottom_level: float,
    maximum_stepdown_mm: float,
    *,
    tolerance: float,
    max_levels: int = DEFAULT_MAX_LEVELS,
) -> ZLevelSchedule:
    """Build an inclusive, descending schedule without cumulative subtraction."""
    top = _finite(top_level, "top level")
    bottom = _finite(bottom_level, "bottom level")
    step = _finite_positive(maximum_stepdown_mm, "stepdown")
    tol = _finite_positive(tolerance, "tolerance")
    if top < bottom - tol:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_BOUNDS, "Top level phải lớn hơn bottom level.")
    if type(max_levels) is not int or max_levels <= 0:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_LEVEL_COUNT_EXCEEDED, "Guardrail số level không hợp lệ.")
    span = max(0.0, top - bottom)
    count = int(math.floor(span / step + tol * 0.1)) + 1
    if count + 1 > max_levels:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_LEVEL_COUNT_EXCEEDED, "Số level Z-Level vượt guardrail.")
    values = [round(top - index * step, 12) for index in range(count)]
    if not values:
        values = [top]
    if values[-1] > bottom + tol:
        values.append(bottom)
    elif abs(values[-1] - bottom) <= tol:
        values[-1] = bottom
    values = tuple(value for index, value in enumerate(values) if index == 0 or abs(value - values[index - 1]) > tol * 0.1)
    if values[-1] < bottom - tol:
        values = (*values[:-1], bottom)
    return ZLevelSchedule(values, top, bottom, step, tol)


def trace_z_level(
    context: Cam3DCalculationContext,
    frame: ZLevelMachiningFrame,
    bounds: ZLevelRegionBounds,
    schedule: ZLevelSchedule,
    parameters: ZLevelFinishingParameters,
    *,
    tool_radius_mm: float,
    cancellation: Callable[[], bool] | None = None,
    contact_resolver: ContactResolver | None = None,
    max_contours: int = DEFAULT_MAX_CONTOURS,
    max_points: int = DEFAULT_MAX_POINTS,
    max_subdivisions: int = DEFAULT_MAX_SUBDIVISIONS,
) -> ZLevelPreview:
    """Trace zero sets of ``g = tool_center_height - requested_level``."""
    mesh = context.calculation_mesh
    selected = {
        item.geometry.reference_id
        for item in context.machining_zone.part_surfaces.selection.surfaces
    }
    raw_total = 0
    passes: list[ZLevelPass] = []
    point_total = 0
    contour_total = 0
    rejected = 0
    ambiguous = 0
    for level_index, level in enumerate(schedule.levels):
        _checkpoint(cancellation)
        raw: list[_RawSegment] = []
        for triangle_index, triangle in enumerate(mesh.triangle_indices):
            if triangle_index % 64 == 0:
                _checkpoint(cancellation)
            if mesh.triangle_sources[triangle_index] not in selected:
                continue
            normal = mesh.triangle_normals[triangle_index]
            if (
                not all(math.isfinite(value) for value in (normal.x, normal.y, normal.z))
                or normal.magnitude <= 1.0e-12
            ):
                rejected += 1
                continue
            vertices = tuple(mesh.vertices[index] for index in triangle)
            values = tuple(
                frame.coordinates(point)[2]
                + (tool_radius_mm + parameters.surface_allowance_mm)
                * normal.dot(frame.w_axis)
                - level
                for point in vertices
            )
            if not all(math.isfinite(value) for value in values):
                raise ZLevelFinishingError(
                    DiagnosticCode.Z_LEVEL_INVALID_CONTACT,
                    "Implicit Z-Level root không hữu hạn.",
                )
            intersections = _triangle_intersections(vertices, values, tolerance=schedule.tolerance_mm)
            source = mesh.triangle_sources[triangle_index]
            if len(intersections) == 3 and all(abs(value) <= schedule.tolerance_mm for value in values):
                # A coplanar triangle contributes its three boundary edges.  The
                # graph assembler removes shared/duplicate edges deterministically.
                raw.extend(
                    _RawSegment(first, second, normal, source, triangle_index)
                    for first, second in (
                        (vertices[0], vertices[1]),
                        (vertices[1], vertices[2]),
                        (vertices[2], vertices[0]),
                    )
                )
                continue
            if len(intersections) != 2:
                if len(intersections) > 2:
                    ambiguous += 1
                continue
            first, second = intersections
            if _distance(first, second) <= schedule.tolerance_mm * 0.1:
                rejected += 1
                continue
            raw.append(_RawSegment(first, second, normal, source, triangle_index))
        raw = _deduplicate_segments(raw, schedule.tolerance_mm)
        raw_total += len(raw)
        contours = _assemble_contours(
            raw,
            frame,
            level,
            level_index,
            parameters,
            context.machining_zone.boundary,
            tool_radius_mm,
            schedule.tolerance_mm,
            cancellation,
            max_subdivisions,
            contact_resolver,
        )
        contour_total += len(contours)
        if contour_total > max_contours:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_CONTOUR_COUNT_EXCEEDED, "Số contour Z-Level vượt guardrail.")
        point_total += sum(len(item.points) for item in contours)
        if point_total > max_points:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_POINT_COUNT_EXCEEDED, "Số điểm Z-Level vượt guardrail.")
        if contours:
            passes.append(ZLevelPass(level_index, level, tuple(contours)))
    statistics = ZLevelStatistics(
        len(schedule.levels),
        len(passes),
        contour_total,
        point_total,
        rejected,
        ambiguous,
    )
    return ZLevelPreview(
        frame,
        bounds,
        schedule,
        tuple(passes),
        raw_total,
        contour_total,
        statistics,
    )


def _triangle_intersections(
    vertices: tuple[Point3, Point3, Point3],
    values: tuple[float, float, float],
    *,
    tolerance: float,
) -> tuple[Point3, ...]:
    points: list[Point3] = []
    for first_index, second_index in ((0, 1), (1, 2), (2, 0)):
        first, second = vertices[first_index], vertices[second_index]
        left, right = values[first_index], values[second_index]
        if abs(left) <= tolerance:
            points.append(first)
        if (left < -tolerance and right > tolerance) or (left > tolerance and right < -tolerance):
            ratio = left / (left - right)
            points.append(_lerp(first, second, ratio))
        elif abs(right) <= tolerance:
            points.append(second)
    result: list[Point3] = []
    for point in sorted(points, key=lambda item: (item.x, item.y, item.z)):
        if not result or _distance(result[-1], point) > tolerance:
            result.append(point)
    return tuple(result)


def _assemble_contours(
    raw: list[_RawSegment],
    frame: ZLevelMachiningFrame,
    level: float,
    pass_index: int,
    parameters: ZLevelFinishingParameters,
    boundary: MachiningBoundary3D | None,
    radius: float,
    tolerance: float,
    cancellation: Callable[[], bool] | None,
    max_subdivisions: int,
    contact_resolver: ContactResolver | None,
) -> tuple[ZLevelContour, ...]:
    if not raw:
        return ()
    nodes: dict[tuple[int, int, int], list[int]] = {}
    for edge_index, segment in enumerate(raw):
        for point in (segment.first, segment.second):
            nodes.setdefault(_point_key(point, tolerance), []).append(edge_index)
    if any(len(set(edges)) > 2 for edges in nodes.values()):
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_BRANCH_POINT, "Z-Level zero-set có branch point bất thường.")
    unused = set(range(len(raw)))
    contours: list[ZLevelContour] = []
    while unused:
        _checkpoint(cancellation)
        edge_index = min(unused, key=lambda item: _edge_key(raw[item], tolerance))
        edge = raw[edge_index]
        start_key = _point_key(edge.first, tolerance)
        end_key = _point_key(edge.second, tolerance)
        start_node = start_key if len([item for item in nodes[start_key] if item in unused]) == 1 else end_key
        path: list[tuple[Point3, Vector3, GeometryReferenceId, int]] = []
        current_key = start_node
        current_edge_index = edge_index
        closed = False
        while current_edge_index in unused:
            _checkpoint(cancellation)
            current = raw[current_edge_index]
            unused.remove(current_edge_index)
            current_start = _point_key(current.first, tolerance)
            if current_start == current_key:
                first, second = current.first, current.second
            else:
                first, second = current.second, current.first
            if not path:
                path.append((first, current.normal, current.source, current.triangle_index))
            path.append((second, current.normal, current.source, current.triangle_index))
            next_key = _point_key(second, tolerance)
            if next_key == start_node:
                closed = True
                break
            candidates = sorted(
                item for item in nodes.get(next_key, ()) if item in unused
            )
            if not candidates:
                break
            current_key = next_key
            current_edge_index = candidates[0]
        if len(path) < 2:
            continue
        if not closed and len(path) > 2:
            # Open contours are valid for walls, but never silently close them.
            loop_type = ZLevelLoopType.DISCONNECTED
        else:
            loop_type = ZLevelLoopType.OUTER if not contours else ZLevelLoopType.DISCONNECTED
        points = _discretize_path(
            path,
            frame,
            level,
            parameters,
            boundary,
            radius,
            tolerance,
            max_subdivisions,
            contact_resolver,
        )
        if len(points) < 2:
            continue
        if _has_self_intersection(points, frame, tolerance):
            raise ZLevelFinishingError(
                DiagnosticCode.Z_LEVEL_SELF_INTERSECTION,
                "Contour Z-Level tự giao; đã fail closed.",
            )
        orientation = _orientation(points, frame)
        if parameters.orientation is not ZLevelOrientation.AUTOMATIC and orientation is not parameters.orientation:
            points = tuple(reversed(points))
            orientation = parameters.orientation
        elif parameters.orientation is ZLevelOrientation.AUTOMATIC and orientation is ZLevelOrientation.CLOCKWISE:
            points = tuple(reversed(points))
            orientation = ZLevelOrientation.COUNTER_CLOCKWISE
        source_region = tuple(
            sorted(
                {
                    str(source)
                    for item in points
                    for source in item.source_surface_ids
                }
            )
        )
        region_id = (
            f"region:{','.join(source_region)}"
            if source_region
            else f"region:pass-{pass_index}-segment-{len(contours)}"
        )
        contours.append(
            ZLevelContour(
                pass_index,
                len(contours),
                level,
                region_id,
                loop_type,
                orientation,
                points,
                closed,
                len(contours) - 1 if contours else None,
            )
        )
    return _classify_loops(
        tuple(sorted(contours, key=lambda item: (item.region_id, item.segment_index))),
        frame,
        parameters.orientation,
    )


def _deduplicate_segments(raw: list[_RawSegment], tolerance: float) -> list[_RawSegment]:
    counts: dict[tuple[tuple[int, int, int], tuple[int, int, int]], int] = {}
    for segment in raw:
        if _distance(segment.first, segment.second) <= tolerance * 0.1:
            # Zero-length/degenerate edges are not topology evidence.
            continue
        keys = sorted((_point_key(segment.first, tolerance), _point_key(segment.second, tolerance)))
        key = (keys[0], keys[1])
        counts[key] = counts.get(key, 0) + 1
    if any(value > 2 for value in counts.values()):
        raise ZLevelFinishingError(
            DiagnosticCode.Z_LEVEL_UNSUPPORTED_TOPOLOGY,
            "Z-Level gặp repeated edge/non-manifold topology không thể chứng minh tương đương.",
        )
    unique: dict[tuple[tuple[int, int, int], tuple[int, int, int]], _RawSegment] = {}
    for segment in raw:
        if _distance(segment.first, segment.second) <= tolerance * 0.1:
            continue
        keys = sorted((_point_key(segment.first, tolerance), _point_key(segment.second, tolerance)))
        shared_edge = (keys[0], keys[1])
        if counts[shared_edge] > 1:
            # Shared triangle edge inside one tessellated face is not a trim.
            continue
        unique.setdefault(shared_edge, segment)
    return [unique[key] for key in sorted(unique)]


def _classify_loops(
    contours: tuple[ZLevelContour, ...],
    frame: ZLevelMachiningFrame,
    requested: ZLevelOrientation,
) -> tuple[ZLevelContour, ...]:
    """Classify nested closed loops without relying on face-selection order."""
    polygons = {
        item.segment_index: tuple(frame.coordinates(point.contact_point)[:2] for point in item.points)
        for item in contours
        if item.closed and len(item.points) >= 3
    }
    seen_polygons: set[tuple[tuple[float, float], ...]] = set()
    for polygon in polygons.values():
        if abs(_polygon_area(polygon)) <= 1.0e-12:
            raise ZLevelFinishingError(
                DiagnosticCode.Z_LEVEL_UNSUPPORTED_TOPOLOGY,
                "Z-Level tiny/sliver loop bị collapse sau quantization.",
            )
        key = tuple(sorted(polygon))
        if key in seen_polygons:
            raise ZLevelFinishingError(
                DiagnosticCode.Z_LEVEL_DUPLICATE_SEGMENT,
                "Z-Level duplicate contour không được merge ngầm.",
            )
        seen_polygons.add(key)
    result: list[ZLevelContour] = []
    for contour in contours:
        polygon = polygons.get(contour.segment_index)
        if polygon is None:
            result.append(replace(contour, loop_type=ZLevelLoopType.DISCONNECTED))
            continue
        centroid = (
            sum(point[0] for point in polygon) / len(polygon),
            sum(point[1] for point in polygon) / len(polygon),
        )
        containing = sum(
            1
            for index, candidate in polygons.items()
            if index != contour.segment_index
            and abs(_polygon_area(candidate)) > abs(_polygon_area(polygon))
            and _inside_polygon(centroid, candidate)
        )
        loop_type = ZLevelLoopType.INNER if containing % 2 else ZLevelLoopType.OUTER
        points = contour.points
        orientation = contour.orientation
        if requested is ZLevelOrientation.AUTOMATIC:
            wanted = (
                ZLevelOrientation.CLOCKWISE
                if loop_type is ZLevelLoopType.INNER
                else ZLevelOrientation.COUNTER_CLOCKWISE
            )
            if orientation is not wanted:
                points = tuple(reversed(points))
                orientation = wanted
        result.append(
            replace(
                contour,
                loop_type=loop_type,
                points=points,
                orientation=orientation,
            )
        )
    return tuple(result)


def _polygon_area(points: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:] + points[:1], strict=True)
    )


def _discretize_path(
    path: list[tuple[Point3, Vector3, GeometryReferenceId, int]],
    frame: ZLevelMachiningFrame,
    level: float,
    parameters: ZLevelFinishingParameters,
    boundary: MachiningBoundary3D | None,
    radius: float,
    tolerance: float,
    max_subdivisions: int,
    contact_resolver: ContactResolver | None,
) -> tuple[ZLevelPathPoint, ...]:
    output: list[ZLevelPathPoint] = []
    for index, current in enumerate(path):
        if index:
            previous = path[index - 1]
            distance = _distance(previous[0], current[0])
            count = min(max_subdivisions, max(1, int(math.ceil(distance / parameters.maximum_segment_length_mm))))
            for subdivision in range(1, count + 1):
                ratio = subdivision / count
                contact = _lerp(previous[0], current[0], ratio)
                normal = _normalized(_lerp_vector(previous[1], current[1], ratio), tolerance)
                source_ids = tuple(sorted({previous[2], current[2]}, key=str))
                output.append(_point_evidence(contact, normal, source_ids, current[3], frame, level, parameters, boundary, radius, tolerance, contact_resolver))
        else:
            normal = _normalized(current[1], tolerance)
            output.append(_point_evidence(current[0], normal, (current[2],), current[3], frame, level, parameters, boundary, radius, tolerance, contact_resolver))
    compact: list[ZLevelPathPoint] = []
    for point in output:
        if not compact or _distance(compact[-1].tool_center_point, point.tool_center_point) > tolerance * 0.1:
            compact.append(point)
    return tuple(compact)


def _point_evidence(
    contact: Point3,
    normal: Vector3,
    sources: tuple[GeometryReferenceId, ...],
    triangle_index: int,
    frame: ZLevelMachiningFrame,
    level: float,
    parameters: ZLevelFinishingParameters,
    boundary: MachiningBoundary3D | None,
    radius: float,
    tolerance: float,
    contact_resolver: ContactResolver | None,
) -> ZLevelPathPoint:
    # Keep the mesh differential normal as a provenance reference.  A BRep
    # resolver may return a reversed or non-unit normal; normalize it and reject
    # a tangent/undefined orientation instead of accepting UV convergence alone.
    reference_normal = _normalized(normal, tolerance)
    contact_deviation = 0.0
    if contact_resolver is not None:
        try:
            resolved = contact_resolver(sources[0], contact, tolerance)
        except Exception as error:
            raise ZLevelFinishingError(
                DiagnosticCode.Z_LEVEL_INVALID_CONTACT,
                "Không thể resolve contact/differential normal từ BRep gốc.",
            ) from error
        if resolved.source_surface_id not in sources:
            raise ZLevelFinishingError(
                DiagnosticCode.Z_LEVEL_INVALID_CONTACT,
                "BRep contact resolver trả provenance khác selected face.",
            )
        contact = resolved.contact_point
        normal = _normalized(resolved.surface_normal, tolerance)
        orientation_dot = reference_normal.dot(normal)
        if not math.isfinite(orientation_dot) or abs(orientation_dot) <= 1.0e-8:
            raise ZLevelFinishingError(
                DiagnosticCode.Z_LEVEL_INVALID_NORMAL,
                "Pháp tuyến BRep không nhất quán với differential normal của contact.",
            )
        if orientation_dot < 0.0:
            normal = Vector3(-normal.x, -normal.y, -normal.z)
        contact_deviation = resolved.projection_deviation_mm
    else:
        normal = reference_normal
    if not all(
        math.isfinite(value)
        for value in (
            contact.x,
            contact.y,
            contact.z,
            normal.x,
            normal.y,
            normal.z,
            contact_deviation,
        )
    ):
        raise ZLevelFinishingError(
            DiagnosticCode.Z_LEVEL_INVALID_CONTACT,
            "Contact hoặc differential normal Z-Level không hữu hạn.",
        )
    offset = radius + parameters.surface_allowance_mm
    raw_center = Point3(
        contact.x + normal.x * offset,
        contact.y + normal.y * offset,
        contact.z + normal.z * offset,
        LengthUnit.MM,
    )
    u, v, resolved_level = frame.coordinates(raw_center)
    level_deviation = abs(resolved_level - level)
    if contact_resolver is not None and level_deviation > tolerance:
        raise ZLevelFinishingError(
            DiagnosticCode.Z_LEVEL_UNRESOLVED_ROOT,
            "Contact 3D làm implicit root lệch quá tolerance.",
        )
    center = frame.point(u, v, level)
    actual_offset = _distance(contact, center)
    projected = Vector3(
        center.x - contact.x,
        center.y - contact.y,
        center.z - contact.z,
    )
    normal_offset = projected.dot(normal)
    tangential = math.sqrt(
        max(
            0.0,
            projected.dot(projected) - normal_offset * normal_offset,
        )
    )
    if (
        not math.isfinite(actual_offset)
        or not math.isfinite(normal_offset)
        or (
            contact_resolver is not None
            and (normal_offset <= tolerance * 0.1 or tangential > tolerance)
        )
    ):
        raise ZLevelFinishingError(
            DiagnosticCode.Z_LEVEL_INVALID_CONTACT,
            "Tool center không nằm đúng phía bề mặt hoặc lệch tiếp xúc quá tolerance.",
        )
    if contact_resolver is not None and abs(actual_offset - offset) > tolerance:
        raise ZLevelFinishingError(
            DiagnosticCode.Z_LEVEL_ALLOWANCE_DEVIATION,
            "Allowance Z-Level không được áp dụng đúng một lần.",
        )
    boundary_class = _classify_boundary(contact, frame, boundary, tolerance)
    if boundary_class is ZLevelBoundaryClassification.AMBIGUOUS:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_AMBIGUOUS_TRIM, "Phân loại trim Z-Level không xác định; đã fail closed.")
    if boundary_class is ZLevelBoundaryClassification.OUTSIDE:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_BOUNDARY_ESCAPE, "Zero contour thoát khỏi trimmed face.")
    return ZLevelPathPoint(
        contact,
        center,
        normal,
        level,
        level_deviation,
        contact_deviation,
        abs(actual_offset - offset),
        boundary_class,
        sources,
        triangle_index,
    )


def _classify_boundary(
    point: Point3,
    frame: ZLevelMachiningFrame,
    boundary: MachiningBoundary3D | None,
    tolerance: float,
) -> ZLevelBoundaryClassification:
    if boundary is None or not boundary.points:
        return ZLevelBoundaryClassification.INTERIOR
    uv = tuple(frame.coordinates(item)[:2] for item in boundary.points)
    target = frame.coordinates(point)[:2]
    if any(_distance_2d(target, item) <= tolerance for item in uv):
        return ZLevelBoundaryClassification.ON_BOUNDARY
    inside = _inside_polygon(target, uv)
    return ZLevelBoundaryClassification.INTERIOR if inside else ZLevelBoundaryClassification.OUTSIDE


def _inside_polygon(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
    crossings = 0
    for first, second in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if (first[1] > point[1]) != (second[1] > point[1]):
            x = (second[0] - first[0]) * (point[1] - first[1]) / (second[1] - first[1]) + first[0]
            if point[0] < x:
                crossings += 1
    return crossings % 2 == 1


def _has_self_intersection(
    points: tuple[ZLevelPathPoint, ...],
    frame: ZLevelMachiningFrame,
    tolerance: float,
) -> bool:
    """Reject non-adjacent intersections in the machining U/V plane."""
    values = [frame.coordinates(item.tool_center_point)[:2] for item in points]
    if len(values) > 2 and _distance_2d(values[0], values[-1]) <= tolerance:
        values.pop()
    if len(values) < 4:
        return False
    for first_index, first in enumerate(values):
        for second_index in range(first_index + 1, len(values)):
            if second_index == first_index + 1 or (
                first_index == 0 and second_index == len(values) - 1
            ):
                continue
            if _distance_2d(first, values[second_index]) <= tolerance:
                return True
    segments = tuple(zip(values, values[1:] + values[:1], strict=True))
    last = len(segments) - 1
    for first_index, first in enumerate(segments):
        for second_index in range(first_index + 1, len(segments)):
            if second_index in {first_index, first_index + 1} or (
                first_index == 0 and second_index == last
            ):
                continue
            if _segments_intersect(first, segments[second_index], tolerance):
                return True
    return False


def _segments_intersect(
    first: tuple[tuple[float, float], tuple[float, float]],
    second: tuple[tuple[float, float], tuple[float, float]],
    tolerance: float,
) -> bool:
    def signed_area(
        origin: tuple[float, float],
        left: tuple[float, float],
        right: tuple[float, float],
    ) -> float:
        return (
            (left[0] - origin[0]) * (right[1] - origin[1])
            - (left[1] - origin[1]) * (right[0] - origin[0])
        )

    a, b = first
    c, d = second
    ab_c = signed_area(a, b, c)
    ab_d = signed_area(a, b, d)
    cd_a = signed_area(c, d, a)
    cd_b = signed_area(c, d, b)
    return (
        (ab_c > tolerance and ab_d < -tolerance)
        or (ab_c < -tolerance and ab_d > tolerance)
    ) and (
        (cd_a > tolerance and cd_b < -tolerance)
        or (cd_a < -tolerance and cd_b > tolerance)
    )


def _orientation(points: tuple[ZLevelPathPoint, ...], frame: ZLevelMachiningFrame) -> ZLevelOrientation:
    area = 0.0
    for first, second in zip(points, points[1:] + points[:1], strict=True):
        u1, v1, _ = frame.coordinates(first.contact_point)
        u2, v2, _ = frame.coordinates(second.contact_point)
        area += u1 * v2 - u2 * v1
    return ZLevelOrientation.COUNTER_CLOCKWISE if area >= 0.0 else ZLevelOrientation.CLOCKWISE


def _edge_key(segment: _RawSegment, tolerance: float) -> tuple[tuple[int, int, int], tuple[int, int, int], str, int]:
    points = sorted((_point_key(segment.first, tolerance), _point_key(segment.second, tolerance)))
    return points[0], points[1], str(segment.source), segment.triangle_index


def _point_key(point: Point3, tolerance: float) -> tuple[int, int, int]:
    return tuple(round(value / max(tolerance, 1.0e-9)) for value in (point.x, point.y, point.z))


def _lerp(first: Point3, second: Point3, ratio: float) -> Point3:
    return Point3(
        first.x + (second.x - first.x) * ratio,
        first.y + (second.y - first.y) * ratio,
        first.z + (second.z - first.z) * ratio,
        LengthUnit.MM,
    )


def _lerp_vector(first: Vector3, second: Vector3, ratio: float) -> Vector3:
    return Vector3(
        first.x + (second.x - first.x) * ratio,
        first.y + (second.y - first.y) * ratio,
        first.z + (second.z - first.z) * ratio,
    )


def _normalized(value: Vector3, tolerance: float) -> Vector3:
    magnitude = value.magnitude
    if not math.isfinite(magnitude) or magnitude <= tolerance:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_NORMAL, "Pháp tuyến Z-Level suy biến hoặc không xác định.")
    return Vector3(value.x / magnitude, value.y / magnitude, value.z / magnitude)


def _distance(first: Point3, second: Point3) -> float:
    return math.dist((first.x, first.y, first.z), (second.x, second.y, second.z))


def _distance_2d(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.dist(first, second)


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_BOUNDS, f"Z-Level {name} không hữu hạn.")
    return float(value)


def _finite_positive(value: object, name: str) -> float:
    value = _finite(value, name)
    if value <= 0.0:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_STEPDOWN, f"Z-Level {name} phải lớn hơn 0.")
    return value


def _finite_nonnegative(value: object, name: str) -> float:
    value = _finite(value, name)
    if value < 0.0:
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_ALLOWANCE, f"Z-Level {name} không được âm.")
    return value


def _checkpoint(cancellation: Callable[[], bool] | None) -> None:
    if cancellation is not None and cancellation():
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_CANCELLED, "Tính toán Z-Level đã bị hủy.")
