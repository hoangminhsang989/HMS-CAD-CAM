"""Deterministic mesh-plane geometry for Parallel Finishing Foundation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable

from hms_cadcam.cam.cam3d.context import Cam3DCalculationContext
from hms_cadcam.cam.cam3d.mesh import Cam3DCalculationMesh
from hms_cadcam.cam.cam3d.models import (
    BoundaryInclusionPolicy3D,
    MachiningBoundary3D,
    MachiningBoundary3DKind,
    MachiningZone3D,
)
from hms_cadcam.cam.cam3d.parallel.models import (
    ContactResolver,
    ParallelCutDirection,
    ParallelFinishingError,
    ParallelFinishingParameters,
    ParallelMachiningFrame,
    ParallelNormalSource,
    ParallelPass,
    ParallelPathPoint,
    ParallelRegionBounds,
    ParallelResolvedContact,
    ParallelSegment,
)
from hms_cadcam.cam.domain.ids import GeometryReferenceId
from hms_cadcam.cam.domain.operation import DiagnosticCode
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import LengthUnit

_MAX_PASSES = 20_000
_MAX_POINTS_PER_CURVE = 25_000
_MAX_TOTAL_POINTS = 100_000


@dataclass(frozen=True, slots=True)
class ParallelIntersectionOutput:
    """Ordered passes plus raw intersection evidence before stitching."""

    passes: tuple[ParallelPass, ...]
    raw_segment_count: int
    clipped_segment_count: int


@dataclass(frozen=True, slots=True)
class _RawSegment:
    first: Point3
    second: Point3
    normal: Vector3
    source: GeometryReferenceId


@dataclass(frozen=True, slots=True)
class _ContactNode:
    point: Point3
    normal: Vector3
    sources: tuple[GeometryReferenceId, ...]


def build_machining_frame(
    zone: MachiningZone3D,
    direction_angle_degrees: float,
    *,
    epsilon: float,
) -> ParallelMachiningFrame:
    """Build deterministic right-handed U/V/W from Setup and strategy direction."""
    if not isinstance(zone, MachiningZone3D):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_NO_GEOMETRY,
            "Parallel Finishing requires a valid machining zone.",
        )
    if not math.isfinite(direction_angle_degrees):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_ZERO_DIRECTION,
            "Parallel machining direction angle must be finite.",
        )
    if not math.isfinite(epsilon) or epsilon <= 0.0:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_INVALID_TOLERANCE,
            "Parallel calculation epsilon must be positive.",
        )
    base = zone.machining_direction or zone.wcs.x_axis
    u_axis, v_axis, w_axis = build_frame_axes(
        base,
        zone.tool_axis,
        direction_angle_degrees,
        epsilon=epsilon,
    )
    return ParallelMachiningFrame(zone.wcs.origin, u_axis, v_axis, w_axis)


def build_frame_axes(
    direction: Vector3,
    tool_axis: Vector3,
    direction_angle_degrees: float,
    *,
    epsilon: float,
) -> tuple[Vector3, Vector3, Vector3]:
    """Normalize explicit direction input and return right-handed U/V/W axes."""
    if not isinstance(direction, Vector3) or not isinstance(tool_axis, Vector3):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_ZERO_DIRECTION,
            "Parallel direction/tool axis is invalid.",
        )
    if not math.isfinite(direction_angle_degrees):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_ZERO_DIRECTION,
            "Parallel machining direction angle must be finite.",
        )
    if not math.isfinite(epsilon) or epsilon <= 0.0:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_INVALID_TOLERANCE,
            "Parallel calculation epsilon must be positive.",
        )
    w_axis = _normalized(tool_axis, epsilon)
    base = direction
    projected = _subtract(base, _scaled(w_axis, base.dot(w_axis)))
    if projected.magnitude <= epsilon:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_ZERO_DIRECTION,
            "Parallel machining direction is zero or parallel to the tool axis.",
        )
    base_u = _normalized(projected, epsilon)
    radians = math.radians(direction_angle_degrees % 360.0)
    quarter = w_axis.cross(base_u)
    rotated = _add(_scaled(base_u, math.cos(radians)), _scaled(quarter, math.sin(radians)))
    u_axis = _normalized(rotated, epsilon)
    v_axis = _normalized(w_axis.cross(u_axis), epsilon)
    return u_axis, v_axis, w_axis


def calculate_region_bounds(
    mesh: Cam3DCalculationMesh,
    frame: ParallelMachiningFrame,
    zone: MachiningZone3D,
    *,
    padding: float,
) -> ParallelRegionBounds:
    """Calculate selected-PART extents independent of topology enumeration order."""
    if not isinstance(mesh, Cam3DCalculationMesh):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_NULL_SHAPE,
            "Parallel calculation mesh is missing.",
        )
    if not math.isfinite(padding) or padding < 0.0:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_INVALID_TOLERANCE,
            "Parallel bounds padding is invalid.",
        )
    selected = _part_surface_ids(zone)
    available = set(mesh.triangle_sources)
    missing = selected - available
    if missing:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_MISSING_FACE,
            "One or more selected machining faces are missing from the calculation mesh.",
        )
    vertex_indices = {
        vertex_index
        for triangle, source in zip(
            mesh.triangle_indices, mesh.triangle_sources, strict=True
        )
        if source in selected
        for vertex_index in triangle
    }
    if not vertex_indices:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_EMPTY_BOUNDS,
            "Selected machining faces have no usable mesh bounds.",
        )
    coordinates = tuple(frame.coordinates(mesh.vertices[index]) for index in vertex_indices)
    u_min = min(value[0] for value in coordinates) - padding
    u_max = max(value[0] for value in coordinates) + padding
    v_min = min(value[1] for value in coordinates)
    v_max = max(value[1] for value in coordinates)
    w_min = min(value[2] for value in coordinates)
    w_max = max(value[2] for value in coordinates)
    if zone.minimum_height is not None:
        w_min = max(w_min, zone.minimum_height)
    if zone.maximum_height is not None:
        w_max = min(w_max, zone.maximum_height)
    if u_max - u_min <= padding or v_max < v_min or w_max < w_min:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_EMPTY_BOUNDS,
            "Selected machining region is empty after limits are applied.",
        )
    return ParallelRegionBounds(u_min, u_max, v_min, v_max, w_min, w_max)


def plan_pass_positions(
    bounds: ParallelRegionBounds,
    stepover: float,
    *,
    tolerance: float,
    max_passes: int = _MAX_PASSES,
) -> tuple[float, ...]:
    """Plan deterministic V positions including both selected-region edges."""
    if not isinstance(bounds, ParallelRegionBounds):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_EMPTY_BOUNDS,
            "Parallel pass bounds are invalid.",
        )
    if not math.isfinite(stepover) or stepover <= 0.0:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_INVALID_STEPOVER,
            "Parallel stepover must be positive.",
        )
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_INVALID_TOLERANCE,
            "Parallel pass tolerance must be positive.",
        )
    if type(max_passes) is not int or max_passes <= 0:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_LIMIT_EXCEEDED,
            "Parallel pass limit is invalid.",
        )
    width = bounds.v_max - bounds.v_min
    estimated = max(1, math.ceil(width / stepover)) + 1
    if estimated > max_passes:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_LIMIT_EXCEEDED,
            f"Parallel plan exceeds the safe limit of {max_passes} passes.",
        )
    positions = [bounds.v_min]
    while positions[-1] + stepover < bounds.v_max - tolerance:
        positions.append(positions[-1] + stepover)
        if len(positions) >= max_passes:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_LIMIT_EXCEEDED,
                f"Parallel plan exceeds the safe limit of {max_passes} passes.",
            )
    if bounds.v_max - positions[-1] > tolerance:
        positions.append(bounds.v_max)
    else:
        positions[-1] = bounds.v_max
    normalized: list[float] = []
    for value in positions:
        candidate = 0.0 if value == 0.0 else value
        if not normalized or abs(candidate - normalized[-1]) > tolerance:
            normalized.append(candidate)
    return tuple(normalized)


def intersect_parallel_passes(
    context: Cam3DCalculationContext,
    frame: ParallelMachiningFrame,
    bounds: ParallelRegionBounds,
    pass_positions: tuple[float, ...],
    parameters: ParallelFinishingParameters,
    *,
    tool_radius: float,
    cancellation: Callable[[], bool] | None = None,
    pass_progress: Callable[[int, int], None] | None = None,
    discretization_progress: Callable[[int, int], None] | None = None,
    contact_resolver: ContactResolver | None = None,
) -> ParallelIntersectionOutput:
    """Intersect selected mesh triangles, clip, stitch, sample and order passes."""
    if not isinstance(context, Cam3DCalculationContext):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_NO_GEOMETRY,
            "Parallel calculation context is invalid.",
        )
    if not math.isfinite(tool_radius) or tool_radius <= 0.0:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_INVALID_TOOL,
            "Parallel ball-end radius must be positive.",
        )
    tolerance = context.tolerance_policy.contact_tolerance
    epsilon = context.tolerance_policy.calculation_epsilon
    if parameters.maximum_segment_length_mm < tolerance:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_INVALID_TOLERANCE,
            "Maximum segment length must not be smaller than contact tolerance.",
        )
    _validate_boundary(context.machining_zone.boundary)
    selected = _part_surface_ids(context.machining_zone)
    raw_count = 0
    clipped_count = 0
    raw_passes: list[list[_RawSegment]] = []
    triangles = tuple(
        (triangle, normal, source)
        for triangle, normal, source in zip(
            context.calculation_mesh.triangle_indices,
            context.calculation_mesh.triangle_normals,
            context.calculation_mesh.triangle_sources,
            strict=True,
        )
        if source in selected
    )
    if not triangles:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_NO_GEOMETRY,
            "Parallel selected faces contain no machining triangles.",
        )
    for pass_index, v_position in enumerate(pass_positions):
        _checkpoint(cancellation)
        raw_segments: list[_RawSegment] = []
        for triangle_index, (triangle, normal, source) in enumerate(triangles):
            if triangle_index % 512 == 0:
                _checkpoint(cancellation)
            points = tuple(context.calculation_mesh.vertices[index] for index in triangle)
            raw = _intersect_triangle(points, normal, source, frame, v_position, tolerance)
            if raw is None:
                continue
            raw_count += 1
            clipped = _clip_segment(
                raw,
                frame,
                bounds,
                context.machining_zone.boundary,
                tolerance,
            )
            clipped_count += len(clipped)
            raw_segments.extend(clipped)
        _checkpoint(cancellation)
        raw_passes.append(raw_segments)
        if pass_progress is not None:
            pass_progress(pass_index + 1, len(pass_positions))
    if raw_count == 0:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_NO_INTERSECTION,
            "Parallel sampling planes do not intersect the selected machining region.",
        )

    planned_points = 0
    prepared_passes: list[tuple[float, tuple[tuple[_ContactNode, ...], ...]]] = []
    facet_normal_limit = (
        context.tolerance_policy.angular_tolerance
        if contact_resolver is None
        else math.pi
    )
    for pass_index, (v_position, raw_segments) in enumerate(
        zip(pass_positions, raw_passes, strict=True)
    ):
        _checkpoint(cancellation)
        contact_curves = _stitch_segments(
            raw_segments,
            epsilon,
            tolerance,
            facet_normal_limit,
        )
        ordered = _ordered_curves(
            contact_curves,
            frame,
            pass_index,
            parameters.cut_direction,
        )
        for curve in ordered:
            planned_points += _estimated_discretized_count(
                curve,
                tolerance=tolerance,
                maximum_length=parameters.maximum_segment_length_mm,
                maximum_normal_jump=facet_normal_limit,
                max_points=_MAX_POINTS_PER_CURVE,
            )
            if planned_points > _MAX_TOTAL_POINTS:
                raise ParallelFinishingError(
                    DiagnosticCode.PARALLEL_LIMIT_EXCEEDED,
                    f"Parallel result exceeds the safe limit of {_MAX_TOTAL_POINTS} points.",
                )
        prepared_passes.append((v_position, ordered))

    total_points = 0
    passes: list[ParallelPass] = []
    if discretization_progress is not None:
        discretization_progress(0, len(pass_positions))
    for pass_index, (v_position, ordered) in enumerate(prepared_passes):
        _checkpoint(cancellation)
        segments: list[ParallelSegment] = []
        for curve in ordered:
            sampled = _discretize_curve(
                curve,
                tolerance=tolerance,
                maximum_length=parameters.maximum_segment_length_mm,
                max_points=_MAX_POINTS_PER_CURVE,
                maximum_normal_jump=facet_normal_limit,
            )
            path_points = _tool_center_points(
                sampled,
                frame.w_axis,
                tool_radius + context.stock_allowance.part_normal,
                tolerance,
                angular_tolerance=context.tolerance_policy.angular_tolerance,
                chordal_tolerance=context.tolerance_policy.chordal_tolerance,
                contact_resolver=contact_resolver,
            )
            if len(path_points) < 2:
                continue
            total_points += len(path_points)
            if total_points > _MAX_TOTAL_POINTS:
                raise ParallelFinishingError(
                    DiagnosticCode.PARALLEL_LIMIT_EXCEEDED,
                    f"Parallel result exceeds the safe limit of {_MAX_TOTAL_POINTS} points.",
                )
            segments.append(
                ParallelSegment(
                    pass_index,
                    len(segments),
                    v_position,
                    path_points,
                )
            )
        passes.append(ParallelPass(pass_index, v_position, tuple(segments)))
        if discretization_progress is not None:
            discretization_progress(pass_index + 1, len(pass_positions))
    _checkpoint(cancellation)
    if not any(item.segments for item in passes):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_ALL_PASSES_EMPTY,
            "All Parallel passes are empty after clipping and tolerance filtering.",
        )
    return ParallelIntersectionOutput(tuple(passes), raw_count, clipped_count)


def _intersect_triangle(
    points: tuple[Point3, Point3, Point3] | tuple[Point3, ...],
    normal: Vector3,
    source: GeometryReferenceId,
    frame: ParallelMachiningFrame,
    v_position: float,
    tolerance: float,
) -> _RawSegment | None:
    coordinates = tuple(frame.coordinates(point) for point in points)
    signed = tuple(value[1] - v_position for value in coordinates)
    intersections: list[Point3] = []
    for edge_index in range(3):
        next_index = (edge_index + 1) % 3
        first, second = points[edge_index], points[next_index]
        first_signed, second_signed = signed[edge_index], signed[next_index]
        first_on = abs(first_signed) <= tolerance
        second_on = abs(second_signed) <= tolerance
        if first_on:
            intersections.append(first)
        if first_on and second_on:
            intersections.append(second)
        elif not first_on and not second_on and first_signed * second_signed < 0.0:
            ratio = first_signed / (first_signed - second_signed)
            intersections.append(_lerp_point(first, second, ratio))
    unique = _unique_points(intersections, tolerance)
    if len(unique) < 2:
        return None
    pair = max(
        (
            (first, second)
            for first_index, first in enumerate(unique)
            for second in unique[first_index + 1 :]
        ),
        key=lambda item: (
            _distance(item[0], item[1]),
            _point_tuple(item[0]),
            _point_tuple(item[1]),
        ),
    )
    if _distance(*pair) <= tolerance:
        return None
    return _RawSegment(pair[0], pair[1], normal, source)


def _clip_segment(
    segment: _RawSegment,
    frame: ParallelMachiningFrame,
    bounds: ParallelRegionBounds,
    boundary: MachiningBoundary3D | None,
    tolerance: float,
) -> tuple[_RawSegment, ...]:
    breakpoints = {0.0, 1.0}
    first_coordinates = frame.coordinates(segment.first)
    second_coordinates = frame.coordinates(segment.second)
    delta_w = second_coordinates[2] - first_coordinates[2]
    if abs(delta_w) > tolerance:
        for limit in (bounds.w_min, bounds.w_max):
            ratio = (limit - first_coordinates[2]) / delta_w
            if tolerance < ratio < 1.0 - tolerance:
                breakpoints.add(ratio)
    if boundary is not None and boundary.kind is MachiningBoundary3DKind.CLOSED_PLANAR_CONTOUR:
        first_2d = _boundary_coordinates(segment.first, boundary)
        second_2d = _boundary_coordinates(segment.second, boundary)
        polygon = tuple(_boundary_coordinates(point, boundary) for point in boundary.points[:-1])
        for index, edge_first in enumerate(polygon):
            edge_second = polygon[(index + 1) % len(polygon)]
            ratio = _segment_intersection_ratio(
                first_2d, second_2d, edge_first, edge_second, tolerance
            )
            if ratio is not None and tolerance < ratio < 1.0 - tolerance:
                breakpoints.add(ratio)
    ordered = sorted(breakpoints)
    kept: list[_RawSegment] = []
    for first_t, second_t in zip(ordered, ordered[1:]):
        if second_t - first_t <= tolerance:
            continue
        midpoint = _lerp_point(segment.first, segment.second, (first_t + second_t) / 2.0)
        _u, _v, midpoint_w = frame.coordinates(midpoint)
        if midpoint_w < bounds.w_min - tolerance or midpoint_w > bounds.w_max + tolerance:
            continue
        if boundary is not None and boundary.kind is MachiningBoundary3DKind.CLOSED_PLANAR_CONTOUR:
            polygon = tuple(
                _boundary_coordinates(point, boundary) for point in boundary.points[:-1]
            )
            if not _inside_polygon(_boundary_coordinates(midpoint, boundary), polygon, tolerance):
                continue
        first = _lerp_point(segment.first, segment.second, first_t)
        second = _lerp_point(segment.first, segment.second, second_t)
        if _distance(first, second) > tolerance:
            kept.append(_RawSegment(first, second, segment.normal, segment.source))
    return tuple(kept)


def _stitch_segments(
    segments: list[_RawSegment],
    epsilon: float,
    tolerance: float,
    maximum_normal_jump: float,
) -> tuple[tuple[_ContactNode, ...], ...]:
    if not segments:
        return ()
    node_points: dict[tuple[int, int, int], list[Point3]] = {}
    edge_data: dict[
        tuple[tuple[int, int, int], tuple[int, int, int]],
        list[tuple[Vector3, GeometryReferenceId]],
    ] = {}
    for segment in segments:
        first_key = _point_key(segment.first, epsilon)
        second_key = _point_key(segment.second, epsilon)
        if first_key == second_key:
            continue
        node_points.setdefault(first_key, []).append(segment.first)
        node_points.setdefault(second_key, []).append(segment.second)
        edge = tuple(sorted((first_key, second_key)))
        edge_data.setdefault(edge, []).append((segment.normal, segment.source))
    adjacency: dict[tuple[int, int, int], set[tuple[int, int, int]]] = {
        key: set() for key in node_points
    }
    for first, second in edge_data:
        adjacency[first].add(second)
        adjacency[second].add(first)
    if any(len(neighbors) > 2 for neighbors in adjacency.values()):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_OCP_OPERATION_FAILURE,
            "Parallel mesh-plane intersection is branched or non-manifold.",
        )
    used: set[tuple[tuple[int, int, int], tuple[int, int, int]]] = set()
    paths: list[tuple[tuple[int, int, int], ...]] = []
    starts = sorted(adjacency, key=lambda key: (len(adjacency[key]) != 1, key))
    for start in starts:
        available = [
            neighbor
            for neighbor in sorted(adjacency[start])
            if tuple(sorted((start, neighbor))) not in used
        ]
        while available:
            path = [start]
            previous: tuple[int, int, int] | None = None
            current = start
            while True:
                candidates = [
                    neighbor
                    for neighbor in sorted(adjacency[current])
                    if neighbor != previous
                    and tuple(sorted((current, neighbor))) not in used
                ]
                if not candidates:
                    break
                following = candidates[0]
                used.add(tuple(sorted((current, following))))
                path.append(following)
                previous, current = current, following
                if current == start:
                    break
            if len(path) >= 2:
                paths.append(tuple(path))
            available = [
                neighbor
                for neighbor in sorted(adjacency[start])
                if tuple(sorted((start, neighbor))) not in used
            ]
    curves: list[tuple[_ContactNode, ...]] = []
    for path in paths:
        nodes: list[_ContactNode] = []
        for key in path:
            related = [
                item
                for edge, values in edge_data.items()
                if key in edge
                for item in values
            ]
            normal = _average_normal(
                tuple(item[0] for item in related),
                tolerance,
                maximum_normal_jump,
            )
            sources = tuple(sorted({item[1] for item in related}, key=str))
            point = _canonical_point(key, epsilon)
            nodes.append(_ContactNode(point, normal, sources))
        curves.append(tuple(nodes))
    return tuple(curves)


def _ordered_curves(
    curves: tuple[tuple[_ContactNode, ...], ...],
    frame: ParallelMachiningFrame,
    pass_index: int,
    direction: ParallelCutDirection,
) -> tuple[tuple[_ContactNode, ...], ...]:
    increasing = direction is ParallelCutDirection.ONE_WAY or pass_index % 2 == 0
    oriented: list[tuple[_ContactNode, ...]] = []
    for curve in curves:
        first_u = frame.coordinates(curve[0].point)[0]
        last_u = frame.coordinates(curve[-1].point)[0]
        if (increasing and first_u > last_u) or (not increasing and first_u < last_u):
            curve = tuple(reversed(curve))
        oriented.append(curve)
    return tuple(
        sorted(
            oriented,
            key=lambda curve: (
                frame.coordinates(curve[0].point)[0] * (1.0 if increasing else -1.0),
                _point_tuple(curve[0].point),
                _point_tuple(curve[-1].point),
            ),
        )
    )


def _discretize_curve(
    curve: tuple[_ContactNode, ...],
    *,
    tolerance: float,
    maximum_length: float,
    max_points: int,
    maximum_normal_jump: float,
) -> tuple[_ContactNode, ...]:
    compact = _compact_curve(
        curve,
        tolerance=tolerance,
        maximum_normal_jump=maximum_normal_jump,
    )
    if len(compact) < 2:
        return ()
    result = [compact[0]]
    for first, second in zip(compact, compact[1:]):
        length = _distance(first.point, second.point)
        divisions = max(1, math.ceil(length / maximum_length))
        if len(result) + divisions > max_points:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_LIMIT_EXCEEDED,
                f"Parallel curve exceeds the safe limit of {max_points} points.",
            )
        for index in range(1, divisions + 1):
            ratio = index / divisions
            normal = _normalized(
                _add(_scaled(first.normal, 1.0 - ratio), _scaled(second.normal, ratio)),
                tolerance,
            )
            shared_sources = set(first.sources) & set(second.sources)
            sources = tuple(
                sorted(
                    second.sources
                    if index == divisions
                    else shared_sources or set(first.sources) | set(second.sources),
                    key=str,
                )
            )
            result.append(
                _ContactNode(
                    _lerp_point(first.point, second.point, ratio), normal, sources
                )
            )
    return tuple(result)


def _estimated_discretized_count(
    curve: tuple[_ContactNode, ...],
    *,
    tolerance: float,
    maximum_length: float,
    maximum_normal_jump: float,
    max_points: int,
) -> int:
    compact = _compact_curve(
        curve,
        tolerance=tolerance,
        maximum_normal_jump=maximum_normal_jump,
    )
    if len(compact) < 2:
        return 0
    count = 1 + sum(
        max(1, math.ceil(_distance(first.point, second.point) / maximum_length))
        for first, second in zip(compact, compact[1:])
    )
    if count > max_points:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_LIMIT_EXCEEDED,
            f"Parallel curve exceeds the safe limit of {max_points} points.",
        )
    return count


def _compact_curve(
    curve: tuple[_ContactNode, ...],
    *,
    tolerance: float,
    maximum_normal_jump: float,
) -> list[_ContactNode]:
    compact: list[_ContactNode] = []
    for node in curve:
        if not compact or _distance(compact[-1].point, node.point) > tolerance:
            compact.append(node)
        else:
            compact[-1] = _merge_nodes(
                compact[-1],
                node,
                tolerance,
                maximum_normal_jump,
            )
    return compact


def _tool_center_points(
    curve: tuple[_ContactNode, ...],
    tool_axis: Vector3,
    offset: float,
    tolerance: float,
    *,
    angular_tolerance: float,
    chordal_tolerance: float,
    contact_resolver: ContactResolver | None,
) -> tuple[ParallelPathPoint, ...]:
    result: list[ParallelPathPoint] = []
    for node in curve:
        contact = _resolve_contact(
            node,
            tolerance=tolerance,
            angular_tolerance=angular_tolerance,
            chordal_tolerance=chordal_tolerance,
            contact_resolver=contact_resolver,
        )
        if contact.surface_normal.dot(tool_axis) < -tolerance:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_WORKPLANE,
                "A selected face is oriented away from the fixed tool axis.",
            )
        center = Point3(
            contact.contact_point.x + contact.surface_normal.x * offset,
            contact.contact_point.y + contact.surface_normal.y * offset,
            contact.contact_point.z + contact.surface_normal.z * offset,
            LengthUnit.MM,
        )
        value = ParallelPathPoint(
            contact.contact_point,
            center,
            contact.surface_normal,
            node.sources,
            (
                ParallelNormalSource.BREP_SURFACE
                if contact_resolver is not None
                else ParallelNormalSource.MESH_FACET
            ),
            contact.projection_deviation_mm,
        )
        if not result or _distance(result[-1].tool_center_point, center) > tolerance:
            result.append(value)
        else:
            result[-1] = value
    return tuple(result)


def _resolve_contact(
    node: _ContactNode,
    *,
    tolerance: float,
    angular_tolerance: float,
    chordal_tolerance: float,
    contact_resolver: ContactResolver | None,
) -> ParallelResolvedContact:
    if contact_resolver is None:
        return ParallelResolvedContact(
            node.sources[0],
            node.point,
            node.normal,
            0.0,
        )
    allowed_deviation = chordal_tolerance + tolerance
    try:
        values = tuple(
            contact_resolver(source, node.point, allowed_deviation)
            for source in node.sources
        )
    except ParallelFinishingError:
        raise
    except Exception as error:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_SOURCE_NORMAL_UNAVAILABLE,
            "Original source-surface normal could not be resolved.",
        ) from error
    if not values or any(
        value.source_surface_id != source
        for value, source in zip(values, node.sources, strict=True)
    ):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_SOURCE_NORMAL_UNAVAILABLE,
            "Original source-surface normal evidence is incomplete.",
        )
    if any(value.projection_deviation_mm > allowed_deviation for value in values):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_MESH_TOLERANCE_VIOLATION,
            "Mesh contact exceeds the declared chordal/contact tolerance.",
        )
    reference = values[0]
    if any(
        _distance(reference.contact_point, value.contact_point) > allowed_deviation * 2.0
        for value in values[1:]
    ):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_CONTACT_NORMAL_DISCONTINUITY,
            "Adjacent source faces do not resolve to one shared contact point.",
        )
    _validate_normal_spread(
        tuple(value.surface_normal for value in values),
        angular_tolerance,
    )
    normal = _average_normal(
        tuple(value.surface_normal for value in values),
        tolerance,
        angular_tolerance,
    )
    return ParallelResolvedContact(
        reference.source_surface_id,
        reference.contact_point,
        normal,
        max(value.projection_deviation_mm for value in values),
    )


def _validate_boundary(boundary: MachiningBoundary3D | None) -> None:
    if boundary is None or boundary.kind is MachiningBoundary3DKind.NONE:
        return
    if (
        boundary.kind is not MachiningBoundary3DKind.CLOSED_PLANAR_CONTOUR
        or boundary.inclusion is not BoundaryInclusionPolicy3D.INSIDE
    ):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_UNSUPPORTED_BOUNDARY,
            "Parallel foundation supports only an INSIDE closed planar boundary.",
        )


def _part_surface_ids(zone: MachiningZone3D) -> set[GeometryReferenceId]:
    return {
        surface.geometry.reference_id
        for surface in zone.part_surfaces.selection.surfaces
    }


def _checkpoint(cancellation: Callable[[], bool] | None) -> None:
    if cancellation is not None and cancellation():
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_CANCELLED,
            "Parallel Finishing calculation was cancelled.",
        )


def _boundary_coordinates(
    point: Point3, boundary: MachiningBoundary3D
) -> tuple[float, float]:
    delta = Vector3(
        point.x - boundary.plane.origin.x,
        point.y - boundary.plane.origin.y,
        point.z - boundary.plane.origin.z,
    )
    return delta.dot(boundary.plane.x_axis), delta.dot(boundary.plane.y_axis)


def _inside_polygon(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
    tolerance: float,
) -> bool:
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if _point_segment_distance(point, first, second) <= tolerance:
            return True
    inside = False
    x, y = point
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if (first[1] > y) == (second[1] > y):
            continue
        crossing_x = first[0] + (y - first[1]) * (second[0] - first[0]) / (
            second[1] - first[1]
        )
        if crossing_x >= x - tolerance:
            inside = not inside
    return inside


def _segment_intersection_ratio(
    first: tuple[float, float],
    second: tuple[float, float],
    edge_first: tuple[float, float],
    edge_second: tuple[float, float],
    tolerance: float,
) -> float | None:
    dx, dy = second[0] - first[0], second[1] - first[1]
    ex, ey = edge_second[0] - edge_first[0], edge_second[1] - edge_first[1]
    denominator = dx * ey - dy * ex
    if abs(denominator) <= tolerance * tolerance:
        return None
    offset_x, offset_y = edge_first[0] - first[0], edge_first[1] - first[1]
    ratio = (offset_x * ey - offset_y * ex) / denominator
    edge_ratio = (offset_x * dy - offset_y * dx) / denominator
    if -tolerance <= ratio <= 1.0 + tolerance and -tolerance <= edge_ratio <= 1.0 + tolerance:
        return min(1.0, max(0.0, ratio))
    return None


def _point_segment_distance(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    dx, dy = second[0] - first[0], second[1] - first[1]
    squared = dx * dx + dy * dy
    if squared == 0.0:
        return math.dist(point, first)
    ratio = min(
        1.0,
        max(0.0, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / squared),
    )
    projection = first[0] + ratio * dx, first[1] + ratio * dy
    return math.dist(point, projection)


def _unique_points(points: Iterable[Point3], tolerance: float) -> tuple[Point3, ...]:
    result: list[Point3] = []
    for point in sorted(points, key=_point_tuple):
        if not any(_distance(point, existing) <= tolerance for existing in result):
            result.append(point)
    return tuple(result)


def _merge_nodes(
    first: _ContactNode,
    second: _ContactNode,
    tolerance: float,
    maximum_normal_jump: float,
) -> _ContactNode:
    return _ContactNode(
        second.point,
        _average_normal(
            (first.normal, second.normal),
            tolerance,
            maximum_normal_jump,
        ),
        tuple(sorted(set(first.sources) | set(second.sources), key=str)),
    )


def _average_normal(
    normals: tuple[Vector3, ...],
    epsilon: float,
    maximum_normal_jump: float,
) -> Vector3:
    if not normals:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_OCP_OPERATION_FAILURE,
            "Parallel intersection normal evidence is missing.",
        )
    _validate_normal_spread(normals, maximum_normal_jump)
    reference = normals[0]
    aligned = tuple(
        normal if normal.dot(reference) >= 0.0 else _scaled(normal, -1.0)
        for normal in normals
    )
    total = Vector3(
        sum(item.x for item in aligned),
        sum(item.y for item in aligned),
        sum(item.z for item in aligned),
    )
    return _normalized(total, epsilon)


def _validate_normal_spread(
    normals: tuple[Vector3, ...],
    maximum_normal_jump: float,
) -> None:
    if len(normals) < 2:
        return
    minimum_dot = math.cos(maximum_normal_jump)
    if any(
        first.dot(second) < minimum_dot
        for index, first in enumerate(normals)
        for second in normals[index + 1 :]
    ):
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_CONTACT_NORMAL_DISCONTINUITY,
            "Contact normals cross a sharp or discontinuous source edge.",
        )


def _point_key(point: Point3, epsilon: float) -> tuple[int, int, int]:
    return (
        round(point.x / epsilon),
        round(point.y / epsilon),
        round(point.z / epsilon),
    )


def _canonical_point(key: tuple[int, int, int], epsilon: float) -> Point3:
    return Point3(
        *(0.0 if value == 0 else value * epsilon for value in key),
        LengthUnit.MM,
    )


def _point_tuple(point: Point3) -> tuple[float, float, float]:
    return point.x, point.y, point.z


def _lerp_point(first: Point3, second: Point3, ratio: float) -> Point3:
    return Point3(
        first.x + (second.x - first.x) * ratio,
        first.y + (second.y - first.y) * ratio,
        first.z + (second.z - first.z) * ratio,
        LengthUnit.MM,
    )


def _distance(first: Point3, second: Point3) -> float:
    return math.dist(_point_tuple(first), _point_tuple(second))


def _normalized(vector: Vector3, epsilon: float) -> Vector3:
    magnitude = vector.magnitude
    if not math.isfinite(magnitude) or magnitude <= epsilon:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_ZERO_DIRECTION,
            "Parallel direction/normal cannot be normalized.",
        )
    return Vector3(vector.x / magnitude, vector.y / magnitude, vector.z / magnitude)


def _scaled(vector: Vector3, scale: float) -> Vector3:
    return Vector3(vector.x * scale, vector.y * scale, vector.z * scale)


def _add(first: Vector3, second: Vector3) -> Vector3:
    return Vector3(first.x + second.x, first.y + second.y, first.z + second.z)


def _subtract(first: Vector3, second: Vector3) -> Vector3:
    return Vector3(first.x - second.x, first.y - second.y, first.z - second.z)
