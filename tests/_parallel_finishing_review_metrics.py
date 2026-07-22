"""Deterministic geometry and Toolpath IR metrics for 8A.2.1 review."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from hms_cadcam.cam.cam3d.parallel import ParallelFinishingCandidate
from hms_cadcam.cam.toolpath import ArcMove, LinearMove, RapidMove


def geometry_metrics(fixture, candidate: ParallelFinishingCandidate) -> dict[str, object]:
    """Summarize mesh, normal and transverse tool-center quality evidence."""
    mesh = fixture.mesh
    maximum_edge = max(
        math.dist(
            _point_tuple(mesh.vertices[first]),
            _point_tuple(mesh.vertices[second]),
        )
        for triangle in mesh.triangle_indices
        for first, second in (
            (triangle[0], triangle[1]),
            (triangle[1], triangle[2]),
            (triangle[2], triangle[0]),
        )
    )
    edge_normals: dict[tuple[int, int], list] = defaultdict(list)
    for triangle, normal in zip(
        mesh.triangle_indices,
        mesh.triangle_normals,
        strict=True,
    ):
        for first, second in (
            (triangle[0], triangle[1]),
            (triangle[1], triangle[2]),
            (triangle[2], triangle[0]),
        ):
            edge_normals[tuple(sorted((first, second)))].append(normal)
    facet_jumps = [
        _angle(normals[0], normals[1])
        for normals in edge_normals.values()
        if len(normals) == 2
    ]
    points = tuple(
        point
        for pass_value in candidate.preview.passes
        for segment in pass_value.segments
        for point in segment.points
    )
    path_normal_jumps = [
        _angle(first.surface_normal, second.surface_normal)
        for pass_value in candidate.preview.passes
        for segment in pass_value.segments
        for first, second in zip(segment.points, segment.points[1:])
    ]
    representatives = tuple(
        segment.points[len(segment.points) // 2]
        for pass_value in candidate.preview.passes
        for segment in pass_value.segments[:1]
    )
    transverse_normal_jumps = [
        _angle(first.surface_normal, second.surface_normal)
        for first, second in zip(representatives, representatives[1:])
    ]
    transverse_center_jumps = [
        math.dist(
            _point_tuple(first.tool_center_point),
            _point_tuple(second.tool_center_point),
        )
        for first, second in zip(representatives, representatives[1:])
    ]
    source_counts = Counter(point.normal_source.value for point in points)
    projection_values = [
        point.surface_projection_deviation_mm
        for point in points
        if point.normal_source.value == "brep_surface"
    ]
    return {
        "declared_chordal_tolerance_mm": mesh.chordal_tolerance,
        "declared_angular_tolerance_radians": mesh.angular_tolerance,
        "minimum_triangle_size_mm": fixture.zone.tolerance.minimum_triangle_size,
        "maximum_triangle_edge_mm": maximum_edge,
        "maximum_facet_normal_jump_degrees": max(facet_jumps, default=0.0),
        "maximum_path_contact_normal_jump_degrees": max(
            path_normal_jumps,
            default=0.0,
        ),
        "maximum_transverse_contact_normal_jump_degrees": max(
            transverse_normal_jumps,
            default=0.0,
        ),
        "maximum_tool_center_transverse_jump_mm": max(
            transverse_center_jumps,
            default=0.0,
        ),
        "maximum_surface_projection_deviation_mm": (
            max(projection_values) if projection_values else None
        ),
        "normal_source_counts": dict(sorted(source_counts.items())),
        "triangle_count": mesh.statistics.triangle_count,
        "contact_point_count": len(points),
    }


def toolpath_ir_metrics(candidate: ParallelFinishingCandidate) -> dict[str, object]:
    """Validate continuity, terminal clearance and event provenance."""
    artifact = candidate.artifact
    events = artifact.events
    motions = tuple(
        event for event in events if isinstance(event, (RapidMove, LinearMove, ArcMove))
    )
    sequence_contiguous = tuple(event.sequence_index for event in events) == tuple(
        range(len(events))
    )
    motion_continuous = all(
        first.end == second.start for first, second in zip(motions, motions[1:])
    )
    cutting = tuple(
        event
        for event in motions
        if getattr(event, "motion_class", None).value == "cutting"
    )
    counts = Counter(
        getattr(event, "motion_class", event.kind).value for event in events
    )
    final = motions[-1] if motions else None
    return {
        "event_count": len(events),
        "motion_count": len(motions),
        "motion_class_counts": dict(sorted(counts.items())),
        "sequence_indices_contiguous": sequence_contiguous,
        "motion_start_matches_previous_end": motion_continuous,
        "initial_position": artifact.initial_pose.position.to_dict(),
        "final_position": final.end.position.to_dict() if final is not None else None,
        "final_motion_class": (
            final.motion_class.value if final is not None else None
        ),
        "cutting_event_count": len(cutting),
        "all_cutting_events_have_parallel_provenance": all(
            event.provenance.startswith("parallel.pass.") for event in cutting
        ),
        "all_events_have_contiguous_sequence_and_provenance": (
            sequence_contiguous and all(event.provenance for event in events)
        ),
        "artifact_sha256": artifact.artifact_fingerprint.digest,
    }


def event_text(event) -> str:
    """Return one stable human-readable Toolpath event review line."""
    fields = [
        f"{event.sequence_index:04d}",
        event.kind.value,
        f"provenance={event.provenance}",
    ]
    if isinstance(event, (RapidMove, LinearMove, ArcMove)):
        fields.extend(
            (
                f"class={event.motion_class.value}",
                f"start={_point_text(event.start.position)}",
                f"end={_point_text(event.end.position)}",
            )
        )
    return " | ".join(fields)


def _angle(first, second) -> float:
    cosine = max(-1.0, min(1.0, first.dot(second)))
    return math.degrees(math.acos(cosine))


def _point_tuple(point) -> tuple[float, float, float]:
    return point.x, point.y, point.z


def _point_text(point) -> str:
    return f"({point.x:.6f},{point.y:.6f},{point.z:.6f}) {point.unit.value}"
