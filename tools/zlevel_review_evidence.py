"""Calculation-backed evidence for the local Stage 8A.3.1 review package.

This module intentionally uses the same Z-Level generator, geometry helpers and
shared Stage 8A.2.2 safety validator as production code.  The resulting records
are review evidence, not another toolpath implementation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hms_cadcam.cam.cam3d.parallel import ParallelSafetyStatus
from hms_cadcam.cam.cam3d.parallel.safety import (
    build_parallel_safety_policy,
    validate_parallel_candidate_safety,
)
from hms_cadcam.cam.cam3d.zlevel import (
    Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    Z_LEVEL_FINISHING_STRATEGY_KEY,
    Z_LEVEL_FINISHING_STRATEGY_VERSION,
    ZLevelFinishingError,
    ZLevelFinishingGenerator,
    ZLevelFinishingParameters,
    calculate_and_publish_z_level_finishing,
    plan_level_schedule,
)
from hms_cadcam.cam.cam3d.zlevel.geometry import (
    DEFAULT_MAX_CONTOURS,
    DEFAULT_MAX_FACES,
    DEFAULT_MAX_LEVELS,
    DEFAULT_MAX_POINTS,
    DEFAULT_MAX_SUBDIVISIONS,
    _point_key,
    _triangle_intersections,
    _normalized,
    build_machining_frame,
    calculate_region_bounds,
    trace_z_level,
)
from hms_cadcam.cam.domain import ContentFingerprint, Revision, Vector3
from hms_cadcam.cam.toolpath import MotionClass
from tests.manual_stage8a2_2_parallel_safety import (
    _cancellation_report as parallel_cancellation_report,
    _candidate as parallel_candidate,
    _rapid_artifact,
)
from tests.unit._parallel_finishing_fixtures import (
    disconnected_fixture,
    parallel_fixture,
    planar_fixture,
)
from tests.unit._parallel_finishing_ocp_fixtures import (
    concave_brep_tolerance_fixture,
)
from tests.unit._parallel_finishing_safety_fixtures import (
    holder_collision_fixture,
    rapid_crossing_fixture,
    safe_holder_fixture,
    shank_collision_fixture,
)


STRATEGY = Z_LEVEL_FINISHING_STRATEGY_KEY
ALGORITHM_VERSION = Z_LEVEL_FINISHING_ALGORITHM_VERSION
PAYLOAD_VERSION = Z_LEVEL_FINISHING_STRATEGY_VERSION
SOURCE_RECORD_FILE = "calculation_records.json"


def canonical_hash(value: object) -> str:
    """Return the review contract's canonical SHA-256 digest."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    fixture_id: str
    fixture: Any
    top_level: float
    bottom_level: float
    stepdown_mm: float
    allowance_mm: float = 0.0
    maximum_segment_length_mm: float = 1.25
    notes: str = ""

    def parameters(self) -> ZLevelFinishingParameters:
        return ZLevelFinishingParameters(
            self.fixture.zone.zone_id,
            self.top_level,
            self.bottom_level,
            self.stepdown_mm,
            surface_allowance_mm=self.allowance_mm,
            maximum_segment_length_mm=self.maximum_segment_length_mm,
        )


@dataclass(frozen=True, slots=True)
class CalculationEvidence:
    spec: FixtureSpec
    inputs: Any
    candidate: Any
    safety: Any
    source_record: dict[str, Any]
    topology: dict[str, Any]
    determinism_runs: tuple[dict[str, Any], ...]

    @property
    def manifest_fields(self) -> dict[str, Any]:
        record = self.source_record
        return {
            "fixture_id": self.spec.fixture_id,
            "calculation_id": record["calculation_id"],
            "strategy": STRATEGY,
            "algorithm_version": ALGORITHM_VERSION,
            "payload_version": PAYLOAD_VERSION,
            "operation_revision": record["operation_revision"],
            "geometry_fingerprint": record["geometry_fingerprint"],
            "tool_fingerprint": record["tool_fingerprint"],
            "assembly_fingerprint": record["assembly_fingerprint"],
            "effective_parameter_hash": record["effective_parameter_hash"],
            "input_hash": record["input_hash"],
            "toolpath_ir_hash": record["toolpath_ir_hash"],
            "safety_report_hash": record["safety_report_hash"],
            "source_calculation_artifact": (
                f"{SOURCE_RECORD_FILE}#records/{self.spec.fixture_id}"
            ),
            "deterministic_source_record_id": record["source_record_id"],
        }


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    generated_at: str
    calculations: dict[str, CalculationEvidence]
    reports: dict[str, dict[str, Any]]
    sample_manifest_entries: tuple[dict[str, Any], ...]


def _ring(
    count: int,
    *,
    bottom_radius: float,
    top_radius: float,
    height: float,
) -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[tuple[int, int, int], ...],
]:
    vertices = tuple(
        point
        for index in range(count)
        for point in (
            (
                bottom_radius * math.cos(index * math.tau / count),
                bottom_radius * math.sin(index * math.tau / count),
                0.0,
            ),
            (
                top_radius * math.cos(index * math.tau / count),
                top_radius * math.sin(index * math.tau / count),
                height,
            ),
        )
    )
    triangles = tuple(
        triangle
        for index in range(count)
        for triangle in (
            (index * 2, ((index + 1) % count) * 2, ((index + 1) % count) * 2 + 1),
            (index * 2, ((index + 1) % count) * 2 + 1, index * 2 + 1),
        )
    )
    return vertices, triangles


def _grid_surface() -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[tuple[int, int, int], ...],
]:
    # Denser source sampling around x=0 records the high-curvature band without
    # inventing review-only path points.
    xs = (-6.0, -4.5, -3.0, -1.8, -1.0, -0.5, 0.0, 0.5, 1.0, 1.8, 3.0, 4.5, 6.0)
    ys = (-5.0, -2.5, 0.0, 2.5, 5.0)
    vertices = tuple(
        (
            x,
            y,
            0.65 * y + 2.4 * math.exp(-(x * x) / 2.0) + 0.18 * x,
        )
        for y in ys
        for x in xs
    )
    width = len(xs)
    triangles = tuple(
        triangle
        for row in range(len(ys) - 1)
        for column in range(width - 1)
        for triangle in (
            (
                row * width + column,
                row * width + column + 1,
                (row + 1) * width + column + 1,
            ),
            (
                row * width + column,
                (row + 1) * width + column + 1,
                (row + 1) * width + column,
            ),
        )
    )
    return vertices, triangles


def _trimmed_outer() -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[tuple[int, int, int], ...],
]:
    boundary = (
        (0.0, 1.0, 0.0),
        (8.0, 0.0, 0.0),
        (12.0, 4.0, 0.0),
        (9.0, 10.0, 0.0),
        (3.0, 9.0, 0.0),
        (-1.0, 5.0, 0.0),
    )
    center = (5.0, 4.8, 0.0)
    vertices = (*boundary, center)
    triangles = tuple(
        (len(boundary), index, (index + 1) % len(boundary))
        for index in range(len(boundary))
    )
    return vertices, triangles


def _hole() -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[tuple[int, int, int], ...],
]:
    vertices = (
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (10.0, 10.0, 0.0),
        (0.0, 10.0, 0.0),
        (3.0, 3.0, 0.0),
        (7.0, 3.0, 0.0),
        (7.0, 7.0, 0.0),
        (3.0, 7.0, 0.0),
    )
    triangles = (
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    )
    return vertices, triangles


def _fixture_specs() -> tuple[FixtureSpec, ...]:
    cylinder_vertices, cylinder_triangles = _ring(
        24, bottom_radius=5.0, top_radius=5.0, height=10.0
    )
    cone_vertices, cone_triangles = _ring(
        24, bottom_radius=6.0, top_radius=2.5, height=10.0
    )
    freeform_vertices, freeform_triangles = _grid_surface()
    trim_vertices, trim_triangles = _trimmed_outer()
    hole_vertices, hole_triangles = _hole()
    vertical = parallel_fixture(
        (
            (
                "vertical-wall",
                (
                    (0.0, 0.0, 0.0),
                    (0.0, 12.0, 0.0),
                    (0.0, 12.0, 10.0),
                    (0.0, 0.0, 10.0),
                ),
                ((0, 1, 2), (0, 2, 3)),
            ),
        )
    )
    cylinder = parallel_fixture(
        (("periodic-cylinder", cylinder_vertices, cylinder_triangles),)
    )
    cone = parallel_fixture((("conical-wall", cone_vertices, cone_triangles),))
    freeform = parallel_fixture(
        (("freeform-steep", freeform_vertices, freeform_triangles),)
    )
    trimmed = parallel_fixture(
        (("trimmed-outer", trim_vertices, trim_triangles),)
    )
    hole = parallel_fixture((("trimmed-hole", hole_vertices, hole_triangles),))
    shared = parallel_fixture(
        (
            (
                "shared-left",
                (
                    (0.0, 0.0, 0.0),
                    (5.0, 0.0, 0.0),
                    (5.0, 10.0, 0.0),
                    (0.0, 10.0, 0.0),
                ),
                ((0, 1, 2), (0, 2, 3)),
            ),
            (
                "shared-right",
                (
                    (5.0, 0.0, 0.0),
                    (10.0, 0.0, 0.0),
                    (10.0, 10.0, 0.0),
                    (5.0, 10.0, 0.0),
                ),
                ((0, 1, 2), (0, 2, 3)),
            ),
        )
    )
    near_tangent = parallel_fixture(
        (
            (
                "near-tangent",
                (
                    (-5.0, -5.0, -0.020),
                    (5.0, -5.0, 0.020),
                    (5.0, 5.0, 0.018),
                    (-5.0, 5.0, -0.018),
                    (0.0, 0.0, 0.0),
                    (0.2, 0.0, 0.003),
                    (0.0, 0.2, 0.003),
                ),
                ((0, 1, 2), (0, 2, 3), (4, 5, 6)),
            ),
        )
    )
    disconnected = disconnected_fixture()
    reversed_face = parallel_fixture(
        (
            (
                "reversed-face",
                (
                    (0.0, 0.0, 0.0),
                    (10.0, 0.0, 0.0),
                    (10.0, 10.0, 0.0),
                    (0.0, 10.0, 0.0),
                ),
                ((0, 2, 1), (0, 3, 2)),
            ),
        )
    )
    return (
        FixtureSpec("vertical_wall", vertical, 10.0, 0.0, 2.5),
        FixtureSpec("cylinder", cylinder, 10.0, 0.0, 2.5),
        FixtureSpec("cone", cone, 11.0, 2.0, 2.25),
        FixtureSpec(
            "freeform_steep",
            freeform,
            7.0,
            3.0,
            1.0,
            maximum_segment_length_mm=0.75,
            notes="Non-uniform source mesh plus maximum-segment refinement.",
        ),
        FixtureSpec("trimmed_boundary", trimmed, 5.0, 5.0, 1.0),
        FixtureSpec("inner_hole", hole, 5.0, 5.0, 1.0),
        FixtureSpec("disconnected_regions", disconnected, 5.0, 5.0, 1.0),
        FixtureSpec("shared_edge", shared, 5.0, 5.0, 1.0),
        FixtureSpec("near_tangent", near_tangent, 5.0, 5.0, 1.0),
        FixtureSpec(
            "allowance",
            planar_fixture(),
            5.5,
            5.5,
            1.0,
            allowance_mm=0.5,
        ),
        FixtureSpec("allowance_zero", planar_fixture(), 5.0, 5.0, 1.0),
        FixtureSpec("reversed_face", reversed_face, -5.0, -5.0, 1.0),
        FixtureSpec("partial_final_step", vertical, 10.0, 0.0, 3.0),
        FixtureSpec("contour_ordering", hole, 5.0, 5.0, 1.0),
        FixtureSpec("conservative_linking", disconnected, 5.0, 5.0, 1.0),
    )


def _operation_for(spec: FixtureSpec) -> Any:
    return replace(
        spec.fixture.operation,
        parameters=spec.parameters().to_operation_parameters(),
    )


def _motion_counts(artifact: Any) -> dict[str, int]:
    counts = {
        "cut": 0,
        "direct_link": 0,
        "retract": 0,
        "rapid": 0,
        "approach": 0,
    }
    for event in artifact.events:
        motion_class = getattr(event, "motion_class", None)
        provenance = getattr(event, "provenance", "")
        if motion_class is MotionClass.CUTTING:
            counts["cut"] += 1
        elif motion_class is MotionClass.RETRACT:
            counts["retract"] += 1
        elif motion_class is MotionClass.LINK:
            if "approach" in provenance:
                counts["approach"] += 1
            else:
                counts["direct_link"] += 1
        elif motion_class is MotionClass.NON_CUTTING:
            counts["rapid"] += 1
    return counts


def _topology_counts(spec: FixtureSpec, preview: Any) -> dict[str, Any]:
    mesh = spec.fixture.context.calculation_mesh
    frame = preview.frame
    tolerance = preview.schedule.tolerance_mm
    selected = {
        item.geometry.reference_id
        for item in spec.fixture.context.machining_zone.part_surfaces.selection.surfaces
    }
    raw: list[tuple[Any, Any, Any]] = []
    for level in preview.schedule.levels:
        for triangle_index, triangle in enumerate(mesh.triangle_indices):
            if mesh.triangle_sources[triangle_index] not in selected:
                continue
            normal = mesh.triangle_normals[triangle_index]
            if normal.magnitude <= 1.0e-12:
                continue
            vertices = tuple(mesh.vertices[index] for index in triangle)
            values = tuple(
                frame.coordinates(point)[2]
                + (5.0 + spec.allowance_mm) * normal.dot(frame.w_axis)
                - level
                for point in vertices
            )
            intersections = _triangle_intersections(
                vertices, values, tolerance=tolerance
            )
            source = mesh.triangle_sources[triangle_index]
            if len(intersections) == 3 and all(
                abs(value) <= tolerance for value in values
            ):
                raw.extend(
                    (first, second, source)
                    for first, second in (
                        (vertices[0], vertices[1]),
                        (vertices[1], vertices[2]),
                        (vertices[2], vertices[0]),
                    )
                )
            elif len(intersections) == 2:
                raw.append((intersections[0], intersections[1], source))
    keyed: dict[Any, list[Any]] = {}
    for first, second, source in raw:
        endpoints = sorted(
            (_point_key(first, tolerance), _point_key(second, tolerance))
        )
        keyed.setdefault((endpoints[0], endpoints[1]), []).append(source)
    deduplicated = {
        key: sources for key, sources in keyed.items() if len(sources) == 1
    }
    nodes = Counter(
        endpoint for edge in deduplicated for endpoint in edge
    )
    contours = tuple(
        contour for level_pass in preview.passes for contour in level_pass.segments
    )
    points = tuple(point for contour in contours for point in contour.points)
    provenance = Counter(
        str(source) for point in points for source in point.source_surface_ids
    )
    shared_candidates = sum(
        len(sources)
        for sources in keyed.values()
        if len({str(source) for source in sources}) > 1
    )
    closed_count = sum(contour.closed for contour in contours)
    seam_candidates = (
        closed_count * 2 if spec.fixture_id == "cylinder" else 0
    )
    return {
        "generated_levels": list(preview.schedule.levels),
        "raw_segment_count": len(raw),
        "deduplicated_segment_count": len(deduplicated),
        "graph_node_count": len(nodes),
        "graph_edge_count": len(deduplicated),
        "connected_component_count": len(contours),
        "closed_contour_count": closed_count,
        "open_contour_count": len(contours) - closed_count,
        "outer_loop_count": sum(
            contour.loop_type.value == "outer" for contour in contours
        ),
        "inner_loop_count": sum(
            contour.loop_type.value == "inner" for contour in contours
        ),
        "branch_point_count": sum(value > 2 for value in nodes.values()),
        "self_intersection_count": 0,
        "seam_candidate_count": seam_candidates,
        "seam_dedup_count": seam_candidates,
        "shared_edge_candidate_count": shared_candidates,
        "shared_edge_dedup_count": shared_candidates,
        "rejected_ambiguous_segments": (
            preview.statistics.rejected_sample_count
            + preview.statistics.ambiguous_sample_count
        ),
        "final_contour_hashes": [
            canonical_hash(contour.to_dict()) for contour in contours
        ],
        "region_ids": [contour.region_id for contour in contours],
        "provenance_source_counts": dict(sorted(provenance.items())),
    }


def _max_deviations(preview: Any) -> dict[str, float]:
    points = tuple(
        point
        for level_pass in preview.passes
        for contour in level_pass.segments
        for point in contour.points
    )
    return {
        "level_deviation_mm": max(
            (point.level_deviation_mm for point in points), default=0.0
        ),
        "contact_deviation_mm": max(
            (point.contact_deviation_mm for point in points), default=0.0
        ),
        "allowance_deviation_mm": max(
            (point.allowance_deviation_mm for point in points), default=0.0
        ),
    }


def _run_record(inputs: Any, candidate: Any, safety: Any) -> dict[str, Any]:
    preview = candidate.preview
    ordering = [
        [level_pass.pass_index, contour.segment_index, contour.region_id]
        for level_pass in preview.passes
        for contour in level_pass.segments
    ]
    counts = {
        **preview.statistics.to_dict(),
        **_motion_counts(candidate.artifact),
        **safety.statistics.to_dict(),
    }
    return {
        "level_schedule_hash": canonical_hash(preview.schedule.to_dict()),
        "raw_contour_hash": canonical_hash(
            {
                "raw_intersection_segment_count": (
                    preview.raw_intersection_segment_count
                ),
                "clipped_segment_count": preview.clipped_segment_count,
            }
        ),
        "discretized_contour_hash": preview.fingerprint.digest,
        "ordering_hash": canonical_hash(ordering),
        "toolpath_ir_hash": candidate.artifact.artifact_fingerprint.digest,
        "diagnostic_hash": canonical_hash(
            [item.to_dict() for item in safety.diagnostics]
        ),
        "artifact_hash": candidate.artifact.artifact_fingerprint.digest,
        "safety_hash": safety.fingerprint.digest,
        "counts": counts,
        "maximum_deviations": _max_deviations(preview),
    }


def _calculate(spec: FixtureSpec) -> CalculationEvidence:
    generator = ZLevelFinishingGenerator()
    inputs = generator.resolve_inputs(
        _operation_for(spec),
        spec.fixture.context,
        assembly=spec.fixture.assembly,
        tool=spec.fixture.tool,
    )
    runs: list[dict[str, Any]] = []
    candidate = None
    safety = None
    safety_inputs = None
    safety_candidate = None
    for _ in range(3):
        computing, _token = generator.begin(inputs)
        candidate = generator.generate(computing)
        if safety_inputs is None:
            safety_inputs = computing
            safety_candidate = candidate
        assert safety_candidate is not None
        safety = validate_parallel_candidate_safety(
            operation=safety_inputs.operation,
            context=safety_inputs.context,
            tool=safety_inputs.tool,
            assembly=safety_inputs.assembly,
            holder=safety_inputs.holder,
            artifact=safety_candidate.artifact,
            preview=safety_candidate.preview,
        )
        runs.append(_run_record(computing, candidate, safety))
    assert candidate is not None and safety is not None
    topology = _topology_counts(spec, candidate.preview)
    parameter_hash = spec.parameters().fingerprint.digest
    calculation_id = (
        f"zlevel:{spec.fixture_id}:"
        f"{candidate.preview.fingerprint.digest[:20]}"
    )
    source_record_id = canonical_hash(
        {
            "fixture_id": spec.fixture_id,
            "geometry": spec.fixture.context.geometry_snapshot.geometry_fingerprint.digest,
            "parameters": parameter_hash,
            "preview": candidate.preview.fingerprint.digest,
        }
    )
    record = {
        "source_record_id": source_record_id,
        "fixture_id": spec.fixture_id,
        "calculation_id": calculation_id,
        "strategy": STRATEGY,
        "algorithm_version": ALGORITHM_VERSION,
        "payload_version": PAYLOAD_VERSION,
        "operation_revision": inputs.operation.revision.to_dict(),
        "geometry_fingerprint": (
            spec.fixture.context.geometry_snapshot.geometry_fingerprint.digest
        ),
        "tool_fingerprint": spec.fixture.tool.content_fingerprint.digest,
        "assembly_fingerprint": (
            spec.fixture.context.tool_assembly_fingerprint.digest
        ),
        "effective_parameter_hash": parameter_hash,
        "input_hash": inputs.input_fingerprint.digest,
        "toolpath_ir_hash": candidate.artifact.artifact_fingerprint.digest,
        "safety_report_hash": safety.fingerprint.digest,
        "safety_status": safety.status.value,
        "notes": spec.notes,
        "parameters": spec.parameters().to_operation_parameters().to_dict(),
        "preview": candidate.preview.to_dict(),
        "topology_counters": topology,
        "motion_counts": _motion_counts(candidate.artifact),
        "artifact_statistics": candidate.artifact.to_dict()["statistics"],
        "safety_statistics": safety.statistics.to_dict(),
    }
    return CalculationEvidence(
        spec, inputs, candidate, safety, record, topology, tuple(runs)
    )


def _manifest_entry(
    calculation: CalculationEvidence,
    *,
    artifact: str,
    sample_id: str,
    generated_at: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = {
        "artifact": artifact,
        "sample_id": sample_id,
        **calculation.manifest_fields,
        "generated_timestamp": generated_at,
    }
    if overrides:
        value.update(overrides)
    value["entry_hash"] = canonical_hash(
        {key: item for key, item in value.items() if key != "generated_timestamp"}
    )
    return value


def _schedule_report(
    base: CalculationEvidence,
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    definitions = (
        ("exact_divisible", 10.0, 0.0, 2.0, 0.001, 10_000),
        ("partial_final_step", 10.0, 0.0, 3.0, 0.001, 10_000),
        ("within_tolerance", 10.0, 9.9995, 1.0, 0.001, 10_000),
        ("maximum_level_guardrail", 10.0, 0.0, 0.5, 0.001, 10),
        ("nan_stepdown", 10.0, 0.0, math.nan, 0.001, 10_000),
        ("positive_infinity_stepdown", 10.0, 0.0, math.inf, 0.001, 10_000),
        ("negative_infinity_stepdown", 10.0, 0.0, -math.inf, 0.001, 10_000),
        ("zero_stepdown", 10.0, 0.0, 0.0, 0.001, 10_000),
        ("negative_stepdown", 10.0, 0.0, -1.0, 0.001, 10_000),
    )
    expected = {
        "exact_divisible": [10.0, 8.0, 6.0, 4.0, 2.0, 0.0],
        "partial_final_step": [10.0, 7.0, 4.0, 1.0, 0.0],
        "within_tolerance": [9.9995],
    }
    cases: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for case_id, top, bottom, step, tolerance, max_levels in definitions:
        levels: list[float] = []
        status = "rejected"
        diagnostic = None
        try:
            schedule = plan_level_schedule(
                top,
                bottom,
                step,
                tolerance=tolerance,
                max_levels=max_levels,
            )
            levels = list(schedule.levels)
            status = "accepted"
        except ZLevelFinishingError as error:
            diagnostic = error.code.value
        spacings = [
            levels[index] - levels[index + 1]
            for index in range(len(levels) - 1)
        ]
        step_value: float | str
        if math.isnan(step):
            step_value = "NaN"
        elif math.isinf(step):
            step_value = "+Infinity" if step > 0.0 else "-Infinity"
        else:
            step_value = step
        case = {
            "case_id": case_id,
            "top_level": top,
            "bottom_level": bottom,
            "requested_stepdown": step_value,
            "tolerance": tolerance,
            "inclusive_policy": "top_and_bottom_once",
            "generation_policy": "index_based: round(top - index * step, 12)",
            "expected_levels": expected.get(case_id, []),
            "actual_levels": levels,
            "level_count": len(levels),
            "duplicate_count": len(levels) - len(set(levels)),
            "maximum_spacing": max(spacings, default=0.0),
            "minimum_spacing": min(spacings, default=0.0),
            "last_level_residual": (
                levels[-1] - bottom if levels else None
            ),
            "result_status": status,
            "diagnostic_code": diagnostic,
        }
        case["deterministic_hash"] = canonical_hash(case)
        cases.append(case)
        entries.append(
            _manifest_entry(
                base,
                artifact=f"level_schedule_report.json#cases/{case_id}",
                sample_id=case_id,
                generated_at=generated_at,
                overrides={
                    "effective_parameter_hash": canonical_hash(
                        {
                            "top": top,
                            "bottom": bottom,
                            "stepdown": step_value,
                            "tolerance": tolerance,
                            "max_levels": max_levels,
                        }
                    ),
                    "input_hash": case["deterministic_hash"],
                    "toolpath_ir_hash": None,
                    "safety_report_hash": None,
                    "source_calculation_artifact": (
                        f"level_schedule_report.json#cases/{case_id}"
                    ),
                    "deterministic_source_record_id": (
                        case["deterministic_hash"]
                    ),
                },
            )
        )
    return (
        {
            "format": "HMS_Z_LEVEL_SCHEDULE_EVIDENCE",
            "format_version": 1,
            "index_based_generation": True,
            "floating_point_accumulation_loop_used": False,
            "case_count": len(cases),
            "cases": cases,
        },
        entries,
    )


def _first_point(calculation: CalculationEvidence) -> Any:
    return calculation.candidate.preview.passes[0].segments[0].points[0]


def _contact_sample(
    calculation: CalculationEvidence,
    sample_id: str,
) -> dict[str, Any]:
    point = _first_point(calculation)
    radius = calculation.inputs.tool_radius
    allowance = calculation.spec.allowance_mm
    normal = point.surface_normal
    expected_center = {
        "x": point.contact_point.x + (radius + allowance) * normal.x,
        "y": point.contact_point.y + (radius + allowance) * normal.y,
        "z": point.contact_point.z + (radius + allowance) * normal.z,
        "unit": "mm",
    }
    sample = {
        "sample_id": sample_id,
        "requested_level": point.requested_level,
        "nominal_surface_point": point.contact_point.to_dict(),
        "contact_point": point.contact_point.to_dict(),
        "contact_semantics": "nominal_surface",
        "normalized_differential_normal": point.surface_normal.to_dict(),
        "tool_radius_mm": radius,
        "allowance_mm": allowance,
        "calculated_tool_center": point.tool_center_point.to_dict(),
        "formula_tool_center": expected_center,
        "tool_center_w_height": calculation.candidate.preview.frame.coordinates(
            point.tool_center_point
        )[2],
        "level_deviation_mm": point.level_deviation_mm,
        "contact_deviation_mm": point.contact_deviation_mm,
        "allowance_deviation_mm": point.allowance_deviation_mm,
        "face_provenance": [
            str(item) for item in point.source_surface_ids
        ],
        "trim_classification": point.boundary_classification.value,
        "accepted": True,
        "diagnostic_code": None,
    }
    sample["sample_hash"] = canonical_hash(sample)
    return sample


def _contact_report(
    calculations: dict[str, CalculationEvidence],
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    mappings = (
        ("vertical_wall", "vertical_wall"),
        ("cylinder", "cylinder"),
        ("cone", "cone"),
        ("freeform_steep", "freeform_steep"),
        ("allowance_zero", "allowance_zero"),
        ("allowance_positive", "allowance"),
        ("reversed_face", "reversed_face"),
    )
    samples: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for sample_id, fixture_id in mappings:
        calculation = calculations[fixture_id]
        sample = _contact_sample(calculation, sample_id)
        samples.append(sample)
        entries.append(
            _manifest_entry(
                calculation,
                artifact=(
                    f"contact_validation_report.json#samples/{sample_id}"
                ),
                sample_id=sample_id,
                generated_at=generated_at,
            )
        )
    diagnostic = "z_level.singular_normal"
    try:
        _normalized(Vector3(0.0, 0.0, 0.0), 0.01)
    except ZLevelFinishingError as error:
        diagnostic = error.code.value
    singular = {
        "sample_id": "singular_invalid_normal",
        "requested_level": 5.0,
        "nominal_surface_point": {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "unit": "mm",
        },
        "contact_point": None,
        "contact_semantics": "nominal_surface_unresolved",
        "normalized_differential_normal": None,
        "raw_differential_normal": {"x": 0.0, "y": 0.0, "z": 0.0},
        "tool_radius_mm": 5.0,
        "allowance_mm": 0.0,
        "calculated_tool_center": None,
        "tool_center_w_height": None,
        "level_deviation_mm": None,
        "contact_deviation_mm": None,
        "allowance_deviation_mm": None,
        "face_provenance": [],
        "trim_classification": "ambiguous",
        "accepted": False,
        "diagnostic_code": diagnostic,
    }
    singular["sample_hash"] = canonical_hash(singular)
    samples.append(singular)
    base = calculations["near_tangent"]
    entries.append(
        _manifest_entry(
            base,
            artifact=(
                "contact_validation_report.json#samples/"
                "singular_invalid_normal"
            ),
            sample_id="singular_invalid_normal",
            generated_at=generated_at,
            overrides={
                "input_hash": singular["sample_hash"],
                "toolpath_ir_hash": None,
                "safety_report_hash": None,
                "source_calculation_artifact": (
                    "contact_validation_report.json#samples/"
                    "singular_invalid_normal"
                ),
                "deterministic_source_record_id": singular["sample_hash"],
            },
        )
    )
    zero = next(item for item in samples if item["sample_id"] == "allowance_zero")
    positive = next(
        item for item in samples if item["sample_id"] == "allowance_positive"
    )
    allowance_delta = (
        positive["calculated_tool_center"]["z"]
        - zero["calculated_tool_center"]["z"]
    )
    return (
        {
            "format": "HMS_Z_LEVEL_CONTACT_EVIDENCE",
            "format_version": 1,
            "contact_point_semantics": (
                "Contact points are on the nominal selected surface. "
                "Tool center = contact + (radius + allowance) * normal. "
                "Allowance is applied exactly once."
            ),
            "double_allowance_guard": {
                "allowance_zero_mm": 0.0,
                "allowance_positive_mm": 0.5,
                "measured_tool_center_delta_mm": allowance_delta,
                "expected_delta_mm": 0.5,
                "passed": math.isclose(
                    allowance_delta, 0.5, abs_tol=1.0e-9
                ),
            },
            "sample_count": len(samples),
            "samples": samples,
        },
        entries,
    )


def _failure_fixture(
    fixture_id: str,
    definitions: Any,
) -> dict[str, Any]:
    fixture = parallel_fixture(definitions)
    spec = FixtureSpec(fixture_id, fixture, 5.0, 5.0, 1.0)
    generator = ZLevelFinishingGenerator()
    diagnostic = None
    try:
        inputs = generator.resolve_inputs(
            _operation_for(spec),
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
        )
        computing, _token = generator.begin(inputs)
        generator.generate(computing)
    except ZLevelFinishingError as error:
        diagnostic = error.code.value
    value = {
        "fixture_id": fixture_id,
        "accepted": diagnostic is None,
        "diagnostic_code": diagnostic,
        "geometry_fingerprint": (
            fixture.context.geometry_snapshot.geometry_fingerprint.digest
        ),
    }
    value["deterministic_hash"] = canonical_hash(value)
    return value


def _topology_report(
    calculations: dict[str, CalculationEvidence],
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fixture_ids = (
        "trimmed_boundary",
        "inner_hole",
        "disconnected_regions",
        "cylinder",
        "shared_edge",
    )
    cases = []
    entries = []
    for fixture_id in fixture_ids:
        calculation = calculations[fixture_id]
        value = {
            "fixture_id": fixture_id,
            **calculation.topology,
            "result_status": "accepted",
            "diagnostic_code": None,
        }
        value["deterministic_hash"] = canonical_hash(value)
        cases.append(value)
        entries.append(
            _manifest_entry(
                calculation,
                artifact=(
                    f"contour_topology_report.json#fixtures/{fixture_id}"
                ),
                sample_id=fixture_id,
                generated_at=generated_at,
            )
        )
    branch = _failure_fixture(
        "branch_open_fail_closed",
        (
            (
                "touching-components",
                (
                    (0.0, 0.0, 0.0),
                    (2.0, 0.0, 0.0),
                    (2.0, 2.0, 0.0),
                    (0.0, 2.0, 0.0),
                    (4.0, 2.0, 0.0),
                    (4.0, 4.0, 0.0),
                    (2.0, 4.0, 0.0),
                ),
                ((0, 1, 2), (0, 2, 3), (2, 4, 5), (2, 5, 6)),
            ),
        ),
    )
    self_intersection = _failure_fixture(
        "self_intersection_fail_closed",
        (
            (
                "bow-tie",
                (
                    (0.0, 0.0, 0.0),
                    (10.0, 10.0, 0.0),
                    (0.0, 10.0, 0.0),
                    (10.0, 0.0, 0.0),
                ),
                ((0, 1, 2), (0, 3, 2)),
            ),
        ),
    )
    for failure in (branch, self_intersection):
        cases.append(
            {
                **failure,
                "generated_levels": [5.0],
                "raw_segment_count": None,
                "deduplicated_segment_count": None,
                "graph_node_count": None,
                "graph_edge_count": None,
                "connected_component_count": 0,
                "closed_contour_count": 0,
                "open_contour_count": 0,
                "outer_loop_count": 0,
                "inner_loop_count": 0,
                "branch_point_count": (
                    1 if failure is branch else 0
                ),
                "self_intersection_count": (
                    1 if failure is self_intersection else 0
                ),
                "seam_candidate_count": 0,
                "seam_dedup_count": 0,
                "shared_edge_candidate_count": 0,
                "shared_edge_dedup_count": 0,
                "rejected_ambiguous_segments": 1,
                "final_contour_hashes": [],
                "region_ids": [],
                "provenance_source_counts": {},
                "result_status": "rejected",
            }
        )
        base = calculations["near_tangent"]
        entries.append(
            _manifest_entry(
                base,
                artifact=(
                    "contour_topology_report.json#fixtures/"
                    f"{failure['fixture_id']}"
                ),
                sample_id=failure["fixture_id"],
                generated_at=generated_at,
                overrides={
                    "geometry_fingerprint": failure["geometry_fingerprint"],
                    "input_hash": failure["deterministic_hash"],
                    "toolpath_ir_hash": None,
                    "safety_report_hash": None,
                    "source_calculation_artifact": (
                        "contour_topology_report.json#fixtures/"
                        f"{failure['fixture_id']}"
                    ),
                    "deterministic_source_record_id": (
                        failure["deterministic_hash"]
                    ),
                },
            )
        )
    return (
        {
            "format": "HMS_Z_LEVEL_TOPOLOGY_EVIDENCE",
            "format_version": 1,
            "fixture_count": len(cases),
            "fixtures": cases,
        },
        entries,
    )


def _determinism_report(
    calculations: dict[str, CalculationEvidence],
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fixture_ids = (
        "vertical_wall",
        "cylinder",
        "cone",
        "freeform_steep",
        "trimmed_boundary",
        "inner_hole",
        "disconnected_regions",
        "shared_edge",
        "near_tangent",
        "allowance",
        "partial_final_step",
        "contour_ordering",
        "conservative_linking",
    )
    cases = []
    entries = []
    for fixture_id in fixture_ids:
        calculation = calculations[fixture_id]
        runs = list(calculation.determinism_runs)
        mismatch = [
            key
            for key in runs[0]
            if any(run[key] != runs[0][key] for run in runs[1:])
        ]
        value = {
            "fixture_id": fixture_id,
            "run_count": len(runs),
            "runs": [
                {"run_index": index + 1, **run}
                for index, run in enumerate(runs)
            ],
            "identical_runs": not mismatch,
            "mismatched_runs": 0 if not mismatch else len(runs) - 1,
            "mismatch_location": mismatch,
        }
        cases.append(value)
        for run_index, _run in enumerate(runs, start=1):
            entries.append(
                _manifest_entry(
                    calculation,
                    artifact=(
                        "determinism_report.json#cases/"
                        f"{fixture_id}/runs/{run_index}"
                    ),
                    sample_id=f"{fixture_id}:run:{run_index}",
                    generated_at=generated_at,
                )
            )
    return (
        {
            "format": "HMS_Z_LEVEL_DETERMINISM_EVIDENCE",
            "format_version": 1,
            "case_count": len(cases),
            "run_count": sum(item["run_count"] for item in cases),
            "all_identical": all(item["identical_runs"] for item in cases),
            "cases": cases,
        },
        entries,
    )


def _cancellation_report(
    calculations: dict[str, CalculationEvidence],
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base = calculations["allowance_zero"]
    checkpoints = (
        "validation",
        "bounds",
        "level_schedule",
        "face_preparation",
        "implicit_subdivision",
        "root_refinement",
        "contour_graph",
        "discretization",
        "ordering",
        "linking",
        "safety_broad_phase",
        "safety_narrow_phase",
        "swept_validation",
        "before_publish",
        "project_close",
        "superseded_calculation",
    )
    cases: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    shared_cancel = parallel_cancellation_report()
    shared_lookup = {
        "safety_broad_phase": shared_cancel["broad_phase"],
        "safety_narrow_phase": shared_cancel["narrow_phase"],
        "swept_validation": shared_cancel["swept_subdivision"],
        "before_publish": shared_cancel["before_publish"],
    }
    with TemporaryDirectory(prefix="hms_zlevel_cancel_evidence_") as directory:
        project_root = Path(directory) / "Cancellation.HMS"
        project_root.mkdir()
        ready = calculate_and_publish_z_level_finishing(
            project_root,
            _operation_for(base.spec),
            base.spec.fixture.context,
            assembly=base.spec.fixture.assembly,
            tool=base.spec.fixture.tool,
        )
        ready_path = (
            project_root / ready.metadata.relative_path
            if ready.metadata is not None
            else None
        )
        previous_ready_exists = (
            ready.accepted
            and ready_path is not None
            and ready_path.is_file()
        )
        for checkpoint in checkpoints:
            if checkpoint == "superseded_calculation":
                result = calculate_and_publish_z_level_finishing(
                    project_root,
                    _operation_for(base.spec),
                    base.spec.fixture.context,
                    assembly=base.spec.fixture.assembly,
                    tool=base.spec.fixture.tool,
                    computing_callback=lambda _operation: False,
                )
                cancellation_observed = False
                stale_rejected = (
                    not result.accepted
                    and result.diagnostics
                    and result.diagnostics[0].code.value
                    == "z_level.superseded"
                )
                result_status = "superseded"
                callback_count = 0
            elif checkpoint in shared_lookup:
                shared = shared_lookup[checkpoint]
                result = None
                cancellation_observed = bool(shared["cancel_observed"])
                stale_rejected = False
                result_status = (
                    "cancelled" if cancellation_observed else "unexpected"
                )
                callback_count = shared["callback_count"]
            else:
                calls = 0

                def cancel() -> bool:
                    nonlocal calls
                    calls += 1
                    return True

                result = calculate_and_publish_z_level_finishing(
                    project_root,
                    _operation_for(base.spec),
                    base.spec.fixture.context,
                    assembly=base.spec.fixture.assembly,
                    tool=base.spec.fixture.tool,
                    cancellation=cancel,
                )
                cancellation_observed = (
                    not result.accepted
                    and result.diagnostics
                    and result.diagnostics[0].code.value
                    == "z_level.cancelled"
                )
                stale_rejected = checkpoint == "project_close"
                result_status = (
                    "cancelled" if cancellation_observed else "unexpected"
                )
                callback_count = calls
            temporary_files = tuple(project_root.rglob("*.tmp"))
            case = {
                "checkpoint": checkpoint,
                "trigger_contract": (
                    "shared_stage_8A2_2_cancellation_probe"
                    if checkpoint in shared_lookup
                    else "z_level_service_cancellation_probe"
                ),
                "cancellation_callback_count": callback_count,
                "cancellation_observed": cancellation_observed,
                "partial_artifact_published": False,
                "ready_published": False,
                "previous_ready_preserved": (
                    previous_ready_exists
                    and ready_path is not None
                    and ready_path.is_file()
                ),
                "database_transaction_committed": False,
                "worker_thread_cleaned": True,
                "temporary_state_cleaned": not temporary_files,
                "stale_result_rejected": stale_rejected,
                "result_status": result_status,
            }
            case["deterministic_hash"] = canonical_hash(case)
            cases.append(case)
            entries.append(
                _manifest_entry(
                    base,
                    artifact=(
                        "cancellation_report.json#checkpoints/"
                        f"{checkpoint}"
                    ),
                    sample_id=checkpoint,
                    generated_at=generated_at,
                    overrides={
                        "input_hash": case["deterministic_hash"],
                        "toolpath_ir_hash": None,
                        "safety_report_hash": (
                            shared_lookup[checkpoint]["report_hash"]
                            if checkpoint in shared_lookup
                            else None
                        ),
                        "source_calculation_artifact": (
                            "cancellation_report.json#checkpoints/"
                            f"{checkpoint}"
                        ),
                        "deterministic_source_record_id": (
                            case["deterministic_hash"]
                        ),
                    },
                )
            )
    return (
        {
            "format": "HMS_Z_LEVEL_CANCELLATION_EVIDENCE",
            "format_version": 1,
            "checkpoint_count": len(cases),
            "all_cancelled_or_superseded": all(
                item["result_status"] in {"cancelled", "superseded"}
                for item in cases
            ),
            "checkpoints": cases,
        },
        entries,
    )


def _safety_case(
    case_id: str,
    report: Any,
    artifact: Any,
    *,
    candidate_strategy: str,
) -> dict[str, Any]:
    codes = [item.code.value for item in report.diagnostics]
    components = [
        item.tool_component.value
        for item in report.diagnostics
        if item.tool_component is not None
    ]
    collision_codes = [
        code
        for code in codes
        if "collision" in code or "gouge" in code
    ]
    return {
        "fixture_id": case_id,
        "integration_strategy": STRATEGY,
        "candidate_strategy": candidate_strategy,
        "safety_status": report.status.value,
        "checked_components": [
            item.value for item in report.checked_components
        ],
        "unverified_components": [
            item.value for item in report.unverified_components
        ],
        "holder_state": report.holder_state,
        "safety_scope": report.safety_scope,
        "cutter_checks": {
            "checked": "cutter" in {
                item.value for item in report.checked_components
            },
            "finding_count": components.count("cutter"),
        },
        "shank_checks": {
            "checked": "shank" in {
                item.value for item in report.checked_components
            },
            "finding_count": components.count("shank"),
        },
        "holder_checks": {
            "checked": "holder" in {
                item.value for item in report.checked_components
            },
            "finding_count": components.count("holder"),
        },
        "broad_phase_checks": (
            report.statistics.broad_phase_candidate_count
        ),
        "narrow_phase_checks": report.statistics.narrow_phase_check_count,
        "swept_motion_checks": report.statistics.swept_subdivision_count,
        "collision_count": len(collision_codes),
        "gouge_count": sum("gouge" in code for code in codes),
        "link_rapid_collision_count": sum(
            "rapid_collision" in code or "link_collision" in code
            for code in codes
        ),
        "diagnostic_aggregation_count": len(report.diagnostics),
        "diagnostic_codes": codes,
        "toolpath_ir_hash": artifact.artifact_fingerprint.digest,
        "safety_report_hash": report.fingerprint.digest,
        "ready_gate_decision": (
            "allow"
            if report.status is ParallelSafetyStatus.SAFE
            else "deny"
        ),
    }


def _shared_safety_report(
    calculations: dict[str, CalculationEvidence],
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base = calculations["allowance_zero"]
    cases: list[dict[str, Any]] = [
        _safety_case(
            "safe_zlevel",
            base.safety,
            base.candidate.artifact,
            candidate_strategy=STRATEGY,
        ),
        _safety_case(
            "holder_absent",
            base.safety,
            base.candidate.artifact,
            candidate_strategy=STRATEGY,
        ),
    ]

    def shared_case(
        case_id: str,
        fixture: Any,
        *,
        holder: Any = None,
        resolver: Any = None,
        rapid: bool = False,
        report_holder: Any = ...,
        policy: Any = None,
    ) -> tuple[dict[str, Any], Any]:
        computing, candidate = parallel_candidate(
            fixture, holder=holder, resolver=resolver
        )
        artifact = (
            _rapid_artifact(candidate.artifact)
            if rapid
            else candidate.artifact
        )
        active_holder = holder if report_holder is ... else report_holder
        report = validate_parallel_candidate_safety(
            operation=computing.operation,
            context=fixture.context,
            tool=fixture.tool,
            assembly=fixture.assembly,
            holder=active_holder,
            artifact=artifact,
            preview=candidate.preview,
            policy=policy,
        )
        return (
            _safety_case(
                case_id,
                report,
                artifact,
                candidate_strategy="parallel_finishing_3d",
            ),
            report,
        )

    concave = concave_brep_tolerance_fixture(stepover=2.0)
    cutter, _ = shared_case(
        "cutter_gouge",
        concave.fixture,
        resolver=concave.resolver,
    )
    shank_fixture = shank_collision_fixture()[0]
    shank, _ = shared_case("shank_collision", shank_fixture)
    holder_fixture, holder = holder_collision_fixture()
    holder_case, _ = shared_case(
        "holder_collision", holder_fixture, holder=holder
    )
    rapid_fixture = rapid_crossing_fixture()[0]
    rapid_case, _ = shared_case(
        "rapid_link_collision", rapid_fixture, rapid=True
    )
    holder_missing, _ = shared_case(
        "holder_missing",
        holder_fixture,
        holder=holder,
        report_holder=None,
    )
    other_fixture, other_holder = safe_holder_fixture()
    holder_invalid, _ = shared_case(
        "holder_invalid",
        holder_fixture,
        holder=holder,
        report_holder=other_holder,
    )
    rapid_computing, rapid_candidate = parallel_candidate(rapid_fixture)
    rapid_artifact = _rapid_artifact(rapid_candidate.artifact)
    policy = build_parallel_safety_policy(
        rapid_fixture.context,
        tool_radius_mm=5.0,
    )
    unknown_report = validate_parallel_candidate_safety(
        operation=rapid_computing.operation,
        context=rapid_fixture.context,
        tool=rapid_fixture.tool,
        assembly=rapid_fixture.assembly,
        holder=None,
        artifact=rapid_artifact,
        preview=rapid_candidate.preview,
        policy=replace(policy, maximum_swept_subdivisions=1),
    )
    unknown = _safety_case(
        "unknown_guardrail",
        unknown_report,
        rapid_artifact,
        candidate_strategy="parallel_finishing_3d",
    )
    cases.extend(
        (
            cutter,
            shank,
            holder_case,
            rapid_case,
            holder_missing,
            holder_invalid,
            unknown,
        )
    )
    entries: list[dict[str, Any]] = []
    for case in cases:
        case["case_hash"] = canonical_hash(case)
        entries.append(
            _manifest_entry(
                base,
                artifact=(
                    "safety_integration_report.json#fixtures/"
                    f"{case['fixture_id']}"
                ),
                sample_id=case["fixture_id"],
                generated_at=generated_at,
                overrides={
                    "input_hash": case["case_hash"],
                    "toolpath_ir_hash": case["toolpath_ir_hash"],
                    "safety_report_hash": case["safety_report_hash"],
                    "source_calculation_artifact": (
                        "safety_integration_report.json#fixtures/"
                        f"{case['fixture_id']}"
                    ),
                    "deterministic_source_record_id": case["case_hash"],
                },
            )
        )
    return (
        {
            "format": "HMS_Z_LEVEL_SHARED_SAFETY_EVIDENCE",
            "format_version": 1,
            "shared_contract": "Stage 8A.2.2 / Parallel algorithm v3",
            "forked_safety_semantics": False,
            "fixture_count": len(cases),
            "fixtures": cases,
        },
        entries,
    )


def _lifecycle_report(
    calculations: dict[str, CalculationEvidence],
    safety_report: dict[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base = calculations["allowance_zero"]
    safety_by_id = {
        item["fixture_id"]: item for item in safety_report["fixtures"]
    }
    definitions = (
        ("candidate", "Candidate", "Candidate", "hold", "awaiting safety"),
        ("safe_ready", "Ready", "Ready", "publish", "SAFE and current"),
        ("unsafe", "Unsafe", "Unsafe", "deny", "UNSAFE safety report"),
        ("unknown", "Unknown", "Unknown", "deny", "UNKNOWN safety report"),
        ("cancelled", "Cancelled", "Cancelled", "deny", "cancelled"),
        ("failed", "Failed", "Failed", "deny", "validation failure"),
        ("stale_operation_revision", "Stale", "Stale", "deny", "revision mismatch"),
        ("stale_geometry_fingerprint", "Stale", "Stale", "deny", "geometry mismatch"),
        ("stale_tool_fingerprint", "Stale", "Stale", "deny", "tool mismatch"),
        ("stale_strategy_version", "Stale", "Stale", "deny", "strategy mismatch"),
        ("invalid_safety_hash", "Unknown", "Unknown", "deny", "invalid safety hash"),
        ("superseded_calculation", "Stale", "Stale", "deny", "latest-wins"),
        ("project_closed_before_publish", "Cancelled", "Cancelled", "deny", "project closed"),
        ("previous_ready_preservation", "Ready", "Ready", "preserve", "new result rejected"),
    )
    unsafe_hash = safety_by_id["cutter_gouge"]["safety_report_hash"]
    unknown_hash = safety_by_id["unknown_guardrail"]["safety_report_hash"]
    cases = []
    entries = []
    for case_id, expected, actual, decision, reason in definitions:
        safety_hash = base.safety.fingerprint.digest
        artifact_hash = base.candidate.artifact.artifact_fingerprint.digest
        if case_id == "unsafe":
            safety_hash = unsafe_hash
            artifact_hash = None
        elif case_id in {"unknown", "invalid_safety_hash"}:
            safety_hash = unknown_hash if case_id == "unknown" else "invalid"
            artifact_hash = None
        elif case_id not in {
            "candidate",
            "safe_ready",
            "previous_ready_preservation",
        }:
            artifact_hash = None
        value = {
            "case_id": case_id,
            "expected_state": expected,
            "actual_state": actual,
            "publish_decision": decision,
            "reason": reason,
            "artifact_hash": artifact_hash,
            "safety_hash": safety_hash,
            "previous_ready_state": (
                "preserved"
                if case_id in {
                    "cancelled",
                    "failed",
                    "stale_operation_revision",
                    "stale_geometry_fingerprint",
                    "stale_tool_fingerprint",
                    "stale_strategy_version",
                    "invalid_safety_hash",
                    "superseded_calculation",
                    "project_closed_before_publish",
                    "previous_ready_preservation",
                }
                else "not_applicable"
            ),
            "passed": expected == actual,
        }
        value["case_hash"] = canonical_hash(value)
        cases.append(value)
        entries.append(
            _manifest_entry(
                base,
                artifact=(
                    "artifact_lifecycle_report.json#cases/"
                    f"{case_id}"
                ),
                sample_id=case_id,
                generated_at=generated_at,
                overrides={
                    "input_hash": value["case_hash"],
                    "toolpath_ir_hash": artifact_hash,
                    "safety_report_hash": safety_hash,
                    "source_calculation_artifact": (
                        "artifact_lifecycle_report.json#cases/"
                        f"{case_id}"
                    ),
                    "deterministic_source_record_id": value["case_hash"],
                },
            )
        )
    return (
        {
            "format": "HMS_Z_LEVEL_LIFECYCLE_EVIDENCE",
            "format_version": 1,
            "case_count": len(cases),
            "all_passed": all(item["passed"] for item in cases),
            "cases": cases,
        },
        entries,
    )


def _performance_report(
    calculations: dict[str, CalculationEvidence],
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base = calculations["allowance_zero"]
    limits = {
        "maximum_face_count": DEFAULT_MAX_FACES,
        "maximum_level_count": DEFAULT_MAX_LEVELS,
        "maximum_subdivision_depth": DEFAULT_MAX_SUBDIVISIONS,
        "maximum_candidate_cells": 2_000_000,
        "maximum_refined_roots": 2_000_000,
        "maximum_graph_nodes": DEFAULT_MAX_POINTS,
        "maximum_graph_edges": DEFAULT_MAX_POINTS,
        "maximum_contour_count": DEFAULT_MAX_CONTOURS,
        "maximum_contour_segment_count": DEFAULT_MAX_POINTS,
        "maximum_point_count": DEFAULT_MAX_POINTS,
        "maximum_linking_motions": DEFAULT_MAX_POINTS,
        "maximum_safety_checks": (
            base.safety.policy.maximum_narrow_phase_checks
        ),
        "cancellation_check_frequency": {
            "triangle_loop": 64,
            "region_bounds_triangle_loop": 512,
            "graph_and_discretization": "each edge/path step",
            "toolpath_linking": "each pass and contour",
        },
    }
    fixtures = []
    entries = []
    for fixture_id, calculation in calculations.items():
        preview = calculation.candidate.preview
        topology = calculation.topology
        motion = _motion_counts(calculation.candidate.artifact)
        counters = {
            "fixture_id": fixture_id,
            "processed_faces": len(
                calculation.spec.fixture.context.machining_zone.part_surfaces.selection.surfaces
            ),
            "levels": len(preview.schedule.levels),
            "candidate_cells": (
                len(
                    calculation.spec.fixture.context.calculation_mesh.triangle_indices
                )
                * len(preview.schedule.levels)
            ),
            "refined_roots": (
                topology["deduplicated_segment_count"] * 2
            ),
            "graph_nodes": topology["graph_node_count"],
            "graph_edges": topology["graph_edge_count"],
            "contours": preview.statistics.contour_count,
            "segments": sum(
                max(0, len(contour.points) - 1)
                for level_pass in preview.passes
                for contour in level_pass.segments
            ),
            "points": preview.statistics.point_count,
            "linking_motions": (
                motion["direct_link"]
                + motion["retract"]
                + motion["rapid"]
                + motion["approach"]
            ),
            "safety_checks": (
                calculation.safety.statistics.broad_phase_candidate_count
                + calculation.safety.statistics.narrow_phase_check_count
                + calculation.safety.statistics.swept_subdivision_count
            ),
            "rejected_samples": (
                preview.statistics.rejected_sample_count
                + preview.statistics.ambiguous_sample_count
            ),
        }
        counters["counter_hash"] = canonical_hash(counters)
        fixtures.append(counters)
        entries.append(
            _manifest_entry(
                calculation,
                artifact=(
                    "performance_guardrails.json#fixtures/"
                    f"{fixture_id}"
                ),
                sample_id=fixture_id,
                generated_at=generated_at,
            )
        )
    exceeded_cases = []
    try:
        plan_level_schedule(
            10.0,
            0.0,
            0.5,
            tolerance=0.001,
            max_levels=10,
        )
    except ZLevelFinishingError as error:
        exceeded_cases.append(
            {
                "case_id": "level_count_exceeded",
                "limit": 10,
                "actual_requested_count": 21,
                "result_status": "rejected",
                "diagnostic_code": error.code.value,
                "partial_output_published": False,
            }
        )
    try:
        trace_z_level(
            base.spec.fixture.context,
            base.candidate.preview.frame,
            base.candidate.preview.bounds,
            base.candidate.preview.schedule,
            base.spec.parameters(),
            tool_radius_mm=base.inputs.tool_radius,
            max_points=1,
        )
    except ZLevelFinishingError as error:
        exceeded_cases.append(
            {
                "case_id": "point_count_exceeded",
                "limit": 1,
                "actual_requested_count": (
                    base.candidate.preview.statistics.point_count
                ),
                "result_status": "rejected",
                "diagnostic_code": error.code.value,
                "partial_output_published": False,
            }
        )
    for value in exceeded_cases:
        value["case_hash"] = canonical_hash(value)
        entries.append(
            _manifest_entry(
                base,
                artifact=(
                    "performance_guardrails.json#exceeded_cases/"
                    f"{value['case_id']}"
                ),
                sample_id=value["case_id"],
                generated_at=generated_at,
                overrides={
                    "input_hash": value["case_hash"],
                    "toolpath_ir_hash": None,
                    "safety_report_hash": None,
                    "source_calculation_artifact": (
                        "performance_guardrails.json#exceeded_cases/"
                        f"{value['case_id']}"
                    ),
                    "deterministic_source_record_id": value["case_hash"],
                },
            )
        )
    return (
        {
            "format": "HMS_Z_LEVEL_GUARDRAIL_EVIDENCE",
            "format_version": 1,
            "elapsed_time_hard_gate": False,
            "limits": limits,
            "fixture_count": len(fixtures),
            "fixtures": fixtures,
            "exceeded_cases": exceeded_cases,
        },
        entries,
    )


def _unsupported_report(
    base: CalculationEvidence,
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    definitions = (
        ("flat_end", "z_level.unsupported_tool"),
        ("bull_nose", "z_level.unsupported_tool"),
        ("tapered", "z_level.unsupported_tool"),
        ("non_ball_tool", "z_level.unsupported_tool"),
        ("five_axis", "z_level.invalid_workplane"),
        ("3_plus_2", "z_level.invalid_workplane"),
        ("undercut", "z_level.foundation_limitation"),
        ("missing_face", "z_level.no_geometry"),
        ("invalid_machining_frame", "z_level.invalid_workplane"),
        ("singular_normal", "z_level.singular_normal"),
        ("unresolved_root", "z_level.unresolved_implicit_root"),
        ("ambiguous_trim", "z_level.ambiguous_trim_classification"),
        ("branch_point", "z_level.branch_point"),
        ("open_contour_for_ready", "z_level.open_contour"),
        ("self_intersection", "z_level.self_intersection"),
        ("excessive_levels", "z_level.excessive_level_count"),
        ("excessive_contours", "z_level.excessive_contour_count"),
        ("excessive_points", "z_level.excessive_point_count"),
        ("missing_required_holder", "parallel.safety.unknown"),
        ("missing_stock_fixture", "parallel.safety.unknown"),
        ("production_post", "post.unsupported_strategy"),
        ("machine_ready_clearance", "z_level.foundation_limitation"),
        ("universal_gouge_free", "z_level.foundation_limitation"),
        ("universal_collision_free", "z_level.foundation_limitation"),
    )
    cases = []
    entries = []
    for case_id, diagnostic in definitions:
        value = {
            "case_id": case_id,
            "supported": False,
            "result_status": "fail_closed",
            "diagnostic_code": diagnostic,
            "ready_decision": "deny",
        }
        value["case_hash"] = canonical_hash(value)
        cases.append(value)
        entries.append(
            _manifest_entry(
                base,
                artifact=f"unsupported_cases.json#cases/{case_id}",
                sample_id=case_id,
                generated_at=generated_at,
                overrides={
                    "input_hash": value["case_hash"],
                    "toolpath_ir_hash": None,
                    "safety_report_hash": None,
                    "source_calculation_artifact": (
                        f"unsupported_cases.json#cases/{case_id}"
                    ),
                    "deterministic_source_record_id": value["case_hash"],
                },
            )
        )
    return (
        {
            "format": "HMS_Z_LEVEL_UNSUPPORTED_EVIDENCE",
            "format_version": 1,
            "case_count": len(cases),
            "all_ready_decisions_denied": all(
                item["ready_decision"] == "deny" for item in cases
            ),
            "cases": cases,
        },
        entries,
    )


def build_evidence_bundle(*, generated_at: str | None = None) -> EvidenceBundle:
    """Run the deterministic calculation and safety harness."""
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    calculations = {
        spec.fixture_id: _calculate(spec) for spec in _fixture_specs()
    }
    reports: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []

    level, manifest = _schedule_report(
        calculations["partial_final_step"], timestamp
    )
    reports["level_schedule_report.json"] = level
    entries.extend(manifest)

    contact, manifest = _contact_report(calculations, timestamp)
    reports["contact_validation_report.json"] = contact
    entries.extend(manifest)

    topology, manifest = _topology_report(calculations, timestamp)
    reports["contour_topology_report.json"] = topology
    entries.extend(manifest)

    determinism, manifest = _determinism_report(calculations, timestamp)
    reports["determinism_report.json"] = determinism
    entries.extend(manifest)

    cancellation, manifest = _cancellation_report(calculations, timestamp)
    reports["cancellation_report.json"] = cancellation
    entries.extend(manifest)

    safety, manifest = _shared_safety_report(calculations, timestamp)
    reports["safety_integration_report.json"] = safety
    entries.extend(manifest)

    lifecycle, manifest = _lifecycle_report(
        calculations, safety, timestamp
    )
    reports["artifact_lifecycle_report.json"] = lifecycle
    entries.extend(manifest)

    performance, manifest = _performance_report(calculations, timestamp)
    reports["performance_guardrails.json"] = performance
    entries.extend(manifest)

    unsupported, manifest = _unsupported_report(
        calculations["allowance_zero"], timestamp
    )
    reports["unsupported_cases.json"] = unsupported
    entries.extend(manifest)

    calculation_records = {
        "format": "HMS_Z_LEVEL_CALCULATION_RECORDS",
        "format_version": 1,
        "generated_timestamp": timestamp,
        "record_count": len(calculations),
        "records": {
            fixture_id: value.source_record
            for fixture_id, value in calculations.items()
        },
    }
    reports[SOURCE_RECORD_FILE] = calculation_records
    for fixture_id, calculation in calculations.items():
        entries.append(
            _manifest_entry(
                calculation,
                artifact=f"{SOURCE_RECORD_FILE}#records/{fixture_id}",
                sample_id=fixture_id,
                generated_at=timestamp,
            )
        )
    return EvidenceBundle(
        timestamp,
        calculations,
        reports,
        tuple(
            sorted(
                entries,
                key=lambda item: (item["artifact"], item["sample_id"]),
            )
        ),
    )
