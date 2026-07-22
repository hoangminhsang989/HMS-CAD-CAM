"""Stage 8A.2.2 tool, collision, swept-path and lifecycle safety tests."""

from __future__ import annotations

from dataclasses import replace
import pytest

from hms_cadcam.cam.cam3d.parallel import (
    PARALLEL_FINISHING_ALGORITHM_VERSION,
    ParallelAabb,
    ParallelCollisionPrimitive,
    ParallelCollisionTriangle,
    ParallelGeometrySource,
    ParallelPrimitiveKind,
    ParallelSafetyPolicy,
    ParallelSafetyDiagnostic,
    ParallelSafetyStatus,
    ParallelToolComponent,
    ParallelFinishingGenerator,
    build_parallel_safety_policy,
    build_parallel_tool_assembly_model,
    calculate_and_publish_parallel_finishing,
    closest_point_on_triangle,
    parallel_artifact_has_safe_contract,
    parallel_clearance_is_satisfied,
    aggregate_parallel_safety_diagnostics,
    segment_triangle_distance,
    swept_axis_triangle_distance,
    swept_primitive_bounds,
    validate_parallel_candidate_safety,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ContentFingerprint,
    GeometryReferenceId,
    LengthUnit,
    ToolpathArtifactId,
)
from hms_cadcam.cam.domain.operation import DiagnosticCode, DiagnosticSeverity
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit
from hms_cadcam.cam.toolpath import FeedMode, MotionClass, Pose, ToolpathBuilder
from hms_cadcam.cam.simulation import (
    SimulationIssueCode,
    SimulationPreflightError,
    build_simulation_request,
)

from tests.unit._parallel_finishing_fixtures import (
    disconnected_fixture,
    parallel_fixture,
    planar_fixture,
)
from tests.unit._parallel_finishing_safety_fixtures import (
    adjacent_wall_fixture,
    holder_collision_fixture,
    rapid_crossing_fixture,
    safe_holder_fixture,
    shank_collision_fixture,
)


def _candidate(fixture):
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    return computing, generator.generate(computing)


def _single_motion_artifact(
    source,
    *,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    provenance: str,
    motion_class: MotionClass,
    rapid: bool,
):
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(),
        operation_id=source.source_operation_id,
        operation_revision=source.operation_revision,
        computation_token=source.computation_token,
        input_fingerprint=source.input_fingerprint,
        unit=source.unit,
        setup_id=source.setup_id,
        setup_revision=source.setup_revision,
        wcs_fingerprint=source.wcs_fingerprint,
        tool_assembly_id=source.tool_assembly_id,
        tool_assembly_fingerprint=source.tool_assembly_fingerprint,
    )
    axis = Vector3(0.0, 0.0, 1.0)
    builder.set_initial_pose(Pose(Point3(*start, LengthUnit.MM), axis))
    builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
    target = Pose(Point3(*end, LengthUnit.MM), axis)
    if rapid:
        builder.rapid_to(target, motion_class=motion_class, provenance=provenance)
    else:
        builder.linear_to(
            target,
            FeedRate(500.0, FeedUnit.MM_PER_MINUTE),
            motion_class=motion_class,
            provenance=provenance,
        )
    return builder.finalize()


def test_algorithm_v3_keeps_strategy_payload_v1() -> None:
    fixture = planar_fixture()
    assert PARALLEL_FINISHING_ALGORITHM_VERSION == 3
    assert fixture.operation.parameters.strategy_version == 1


def test_tool_assembly_uses_ball_cutting_shank_and_declared_absent_holder() -> None:
    fixture = planar_fixture()
    model = build_parallel_tool_assembly_model(
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=None,
    )
    assert model.ball_radius_mm == 5.0
    assert model.holder_state == "declared_absent"
    assert {item.component for item in model.primitives} == {
        ParallelToolComponent.CUTTER,
        ParallelToolComponent.SHANK,
    }
    ball = next(item for item in model.primitives if item.kind is ParallelPrimitiveKind.SPHERE)
    assert ball.axial_start_mm == ball.axial_end_mm == 0.0


def test_holder_profile_is_geometry_faithful_and_missing_snapshot_is_unknown(tmp_path) -> None:
    fixture, holder = holder_collision_fixture()
    model = build_parallel_tool_assembly_model(
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=holder,
    )
    assert model.holder_state == "geometry_faithful"
    assert any(item.component is ParallelToolComponent.HOLDER for item in model.primitives)
    result = calculate_and_publish_parallel_finishing(
        tmp_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert not result.accepted and result.artifact is None
    assert result.safety_report is not None
    assert result.safety_report.status is ParallelSafetyStatus.UNKNOWN
    assert result.safety_report.diagnostics[0].code.value == (
        "parallel.safety.missing_holder_geometry"
    )
    assert result.safety_report.holder_state == "missing"
    assert result.safety_report.checked_components == ()
    assert set(result.safety_report.unverified_components) == set(ParallelToolComponent)
    assert result.safety_report.safety_scope == "incomplete_tool_assembly"


def test_policy_separates_collision_meanings_and_rejects_invalid_margin() -> None:
    policy = build_parallel_safety_policy(planar_fixture().context, tool_radius_mm=5.0)
    assert policy.numeric_epsilon_mm > 0.0
    assert policy.contact_tolerance_mm >= policy.numeric_epsilon_mm
    assert policy.gouge_tolerance_mm != policy.contact_tolerance_mm
    with pytest.raises(Exception, match="invalid"):
        replace(policy, holder_clearance_mm=float("nan"))


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -0.001))
def test_operational_clearance_rejects_non_finite_or_negative_values(value) -> None:
    policy = build_parallel_safety_policy(planar_fixture().context, tool_radius_mm=5.0)
    with pytest.raises(Exception, match="invalid"):
        replace(policy, rapid_clearance_mm=value)


def test_operational_clearance_boundary_is_strict_and_epsilon_independent() -> None:
    assert not parallel_clearance_is_satisfied(0.0009, 0.001)
    assert not parallel_clearance_is_satisfied(0.001, 0.001)
    assert parallel_clearance_is_satisfied(0.0011, 0.001)
    assert parallel_clearance_is_satisfied(0.0011, 0.001) is (
        1.0e-3 > 1.0e-12
    )


def test_operational_clearance_change_updates_safety_report_hash() -> None:
    fixture = planar_fixture(stepover=5.0)
    computing, candidate = _candidate(fixture)
    policy = build_parallel_safety_policy(fixture.context, tool_radius_mm=5.0)
    first = validate_parallel_candidate_safety(
        operation=computing.operation,
        context=fixture.context,
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=None,
        artifact=candidate.artifact,
        preview=candidate.preview,
        policy=policy,
    )
    second = validate_parallel_candidate_safety(
        operation=computing.operation,
        context=fixture.context,
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=None,
        artifact=candidate.artifact,
        preview=candidate.preview,
        policy=replace(
            policy,
            rapid_clearance_mm=policy.rapid_clearance_mm + 0.001,
        ),
    )
    assert first.status is second.status is ParallelSafetyStatus.SAFE
    assert first.fingerprint != second.fingerprint


def _aggregation_finding(
    *,
    fixture,
    face_id: GeometryReferenceId,
    sample_index: int,
    penetration: float,
) -> ParallelSafetyDiagnostic:
    return ParallelSafetyDiagnostic(
        ParallelSafetyStatus.UNSAFE,
        DiagnosticCode.PARALLEL_SAFETY_SHANK_COLLISION,
        DiagnosticSeverity.ERROR,
        f"sample {sample_index}",
        fixture.operation.operation_id,
        2,
        3,
        7,
        ParallelToolComponent.SHANK,
        ParallelGeometrySource.PROTECTED_PART,
        face_id,
        closest_distance_mm=8.0 - penetration,
        penetration_depth_mm=penetration,
        tolerance_mm=0.001,
        debug_metadata=(("sample", str(sample_index)),),
        first_sample_index=sample_index,
        last_sample_index=sample_index,
        minimum_clearance_mm=-penetration,
        maximum_penetration_mm=penetration,
        required_clearance_mm=0.001,
        swept_interval_start=sample_index / 10.0,
        swept_interval_end=(sample_index + 1) / 10.0,
    )


def test_repeated_collision_samples_aggregate_and_keep_deepest_penetration() -> None:
    fixture = planar_fixture()
    face_id = GeometryReferenceId.new()
    findings = tuple(
        _aggregation_finding(
            fixture=fixture,
            face_id=face_id,
            sample_index=sample,
            penetration=penetration,
        )
        for sample, penetration in ((1, 0.1), (2, 0.5), (3, 0.2))
    )
    aggregated = aggregate_parallel_safety_diagnostics("calculation-1", findings)
    assert len(aggregated) == 1
    item = aggregated[0]
    assert item.occurrence_count == 3
    assert item.first_sample_index == 1 and item.last_sample_index == 3
    assert item.maximum_penetration_mm == pytest.approx(0.5)
    assert item.minimum_clearance_mm == pytest.approx(-0.5)
    assert dict(item.debug_metadata)["sample"] == "2"
    assert item.swept_interval_start == pytest.approx(0.1)
    assert item.swept_interval_end == pytest.approx(0.4)


def test_aggregation_preserves_distinct_geometry_and_deterministic_order() -> None:
    fixture = planar_fixture()
    face_ids = (GeometryReferenceId.new(), GeometryReferenceId.new())
    findings = tuple(
        _aggregation_finding(
            fixture=fixture,
            face_id=face_id,
            sample_index=index + 1,
            penetration=0.2,
        )
        for index, face_id in enumerate(face_ids)
    )
    first = aggregate_parallel_safety_diagnostics("calculation-2", findings)
    second = aggregate_parallel_safety_diagnostics(
        "calculation-2", tuple(reversed(findings))
    )
    assert len(first) == 2
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]


def test_report_limit_counts_unique_collisions_not_repeated_triangles() -> None:
    fixture, _holder = rapid_crossing_fixture()
    computing, candidate = _candidate(fixture)
    artifact = _single_motion_artifact(
        candidate.artifact,
        start=(-10.0, 5.0, 6.0),
        end=(20.0, 5.0, 6.0),
        provenance="parallel.pass.0.segment.0.direct.rapid",
        motion_class=MotionClass.NON_CUTTING,
        rapid=True,
    )
    policy = replace(
        build_parallel_safety_policy(fixture.context, tool_radius_mm=5.0),
        maximum_report_items=1,
    )
    report = validate_parallel_candidate_safety(
        operation=computing.operation,
        context=fixture.context,
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=None,
        artifact=artifact,
        preview=candidate.preview,
        policy=policy,
    )
    assert report.status is ParallelSafetyStatus.UNSAFE
    assert len(report.diagnostics) == 1
    assert report.diagnostics[0].occurrence_count >= 2
    assert report.diagnostics[0].code is DiagnosticCode.PARALLEL_SAFETY_RAPID_COLLISION


def test_sphere_triangle_tangent_and_penetration_distances() -> None:
    unit = LengthUnit.MM
    triangle = (
        Point3(-10.0, -10.0, 0.0, unit),
        Point3(10.0, -10.0, 0.0, unit),
        Point3(0.0, 10.0, 0.0, unit),
    )
    tangent = Point3(0.0, 0.0, 5.0, unit)
    penetrated = Point3(0.0, 0.0, 4.5, unit)
    assert segment_triangle_distance(tangent, tangent, triangle) == pytest.approx(5.0)
    assert segment_triangle_distance(penetrated, penetrated, triangle) == pytest.approx(4.5)
    closest, distance = closest_point_on_triangle(tangent, triangle)
    assert closest.z == 0.0 and distance == pytest.approx(5.0)


def test_broad_phase_swept_bounds_cover_both_endpoints_and_tolerance() -> None:
    unit = LengthUnit.MM
    primitive = ParallelCollisionPrimitive(
        ParallelPrimitiveKind.SPHERE,
        ParallelToolComponent.CUTTER,
        0.0,
        0.0,
        5.0,
        5.0,
        "ball",
        "exact",
    )
    bounds = swept_primitive_bounds(
        primitive,
        Point3(-10.0, 0.0, 0.0, unit),
        Point3(10.0, 0.0, 0.0, unit),
        Vector3(0.0, 0.0, 1.0),
        0.25,
    )
    assert bounds.minimum == (-15.25, -5.25, -5.25)
    assert bounds.maximum == (15.25, 5.25, 5.25)
    assert bounds.overlaps(ParallelAabb((-0.1, -0.1, -0.1), (0.1, 0.1, 0.1)))


def test_swept_narrow_phase_detects_collision_between_safe_endpoints() -> None:
    unit = LengthUnit.MM
    primitive = ParallelCollisionPrimitive(
        ParallelPrimitiveKind.SPHERE,
        ParallelToolComponent.CUTTER,
        0.0,
        0.0,
        1.0,
        1.0,
        "ball",
        "exact",
    )
    points = (
        Point3(0.0, -2.0, -2.0, unit),
        Point3(0.0, 2.0, -2.0, unit),
        Point3(0.0, 0.0, 2.0, unit),
    )
    triangle = ParallelCollisionTriangle(
        0,
        planar_fixture().zone.part_surfaces.selection.surfaces[0].geometry.reference_id,
        ParallelGeometrySource.PROTECTED_PART,
        points,
        ParallelAabb((0.0, -2.0, -2.0), (0.0, 2.0, 2.0)),
    )
    assert swept_axis_triangle_distance(
        primitive,
        Point3(-5.0, 0.0, 0.0, unit),
        Point3(5.0, 0.0, 0.0, unit),
        Vector3(0.0, 0.0, 1.0),
        triangle,
    ) == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("fixture_factory", "holder", "code", "component"),
    (
        (
            adjacent_wall_fixture,
            False,
            "parallel.safety.protected_face_collision",
            ParallelToolComponent.CUTTER,
        ),
        (
            shank_collision_fixture,
            False,
            "parallel.safety.shank_collision",
            ParallelToolComponent.SHANK,
        ),
        (
            holder_collision_fixture,
            True,
            "parallel.safety.holder_collision",
            ParallelToolComponent.HOLDER,
        ),
    ),
)
def test_protected_geometry_collisions_are_component_specific(
    tmp_path,
    fixture_factory,
    holder,
    code,
    component,
) -> None:
    fixture, holder_value = fixture_factory()
    result = calculate_and_publish_parallel_finishing(
        tmp_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        holder=holder_value if holder else None,
    )
    assert not result.accepted and result.metadata is None
    assert result.operation.artifact_state.status is ArtifactStatus.FAILED
    assert result.safety_report is not None
    assert result.safety_report.status is ParallelSafetyStatus.UNSAFE
    first = result.safety_report.diagnostics[0]
    assert first.code.value == code and first.tool_component is component
    assert first.motion_index is not None and first.closest_distance_mm is not None
    if holder:
        assert result.safety_report.holder_state == "geometry_faithful"
        assert ParallelToolComponent.HOLDER in result.safety_report.checked_components


def test_planar_expected_contact_is_safe_and_publishes_ready(tmp_path) -> None:
    fixture = planar_fixture(stepover=5.0)
    result = calculate_and_publish_parallel_finishing(
        tmp_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.accepted and result.artifact is not None and result.metadata is not None
    assert result.safety_report is not None
    assert result.safety_report.status is ParallelSafetyStatus.SAFE
    assert result.operation.artifact_state.status is ArtifactStatus.VALID
    assert parallel_artifact_has_safe_contract(result.artifact)
    assert result.safety_report.checked_components == (
        ParallelToolComponent.CUTTER,
        ParallelToolComponent.SHANK,
    )
    assert result.safety_report.unverified_components == (
        ParallelToolComponent.HOLDER,
    )
    assert result.safety_report.holder_state == "declared_absent"
    assert result.safety_report.safety_scope == "declared_assembly_holder_absent"
    assert not parallel_artifact_has_safe_contract(
        result.artifact,
        require_holder_verified=True,
    )


def test_valid_safe_holder_is_checked_and_changes_scope_hash(tmp_path) -> None:
    absent = planar_fixture(stepover=5.0)
    (tmp_path / "absent").mkdir()
    absent_result = calculate_and_publish_parallel_finishing(
        tmp_path / "absent",
        absent.operation,
        absent.context,
        assembly=absent.assembly,
        tool=absent.tool,
    )
    fixture, holder = safe_holder_fixture()
    (tmp_path / "holder").mkdir()
    safe_result = calculate_and_publish_parallel_finishing(
        tmp_path / "holder",
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        holder=holder,
    )
    assert safe_result.accepted and safe_result.artifact is not None
    assert safe_result.safety_report is not None
    assert safe_result.safety_report.checked_components == (
        ParallelToolComponent.CUTTER,
        ParallelToolComponent.HOLDER,
        ParallelToolComponent.SHANK,
    )
    assert safe_result.safety_report.unverified_components == ()
    assert safe_result.safety_report.holder_state == "geometry_faithful"
    assert safe_result.safety_report.safety_scope == (
        "declared_assembly_holder_verified"
    )
    assert parallel_artifact_has_safe_contract(
        safe_result.artifact,
        require_holder_verified=True,
    )
    assert absent_result.safety_report is not None
    assert (
        absent_result.safety_report.fingerprint
        != safe_result.safety_report.fingerprint
    )


def test_safe_contract_is_bound_to_artifact_assembly_fingerprint(tmp_path) -> None:
    fixture = planar_fixture(stepover=5.0)
    result = calculate_and_publish_parallel_finishing(
        tmp_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.artifact is not None
    changed = replace(
        result.artifact,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(
            {"changed_scope": True}
        ),
        artifact_fingerprint=None,
    )
    assert not parallel_artifact_has_safe_contract(changed)


def test_candidate_and_stale_algorithm_markers_are_not_safe_contracts() -> None:
    fixture = planar_fixture(stepover=5.0)
    _computing, candidate = _candidate(fixture)
    assert not parallel_artifact_has_safe_contract(candidate.artifact)
    marker_index = next(
        index
        for index, event in enumerate(candidate.artifact.events)
        if event.provenance == "parallel.safety.contract"
    )
    marker = candidate.artifact.events[marker_index]
    stale_marker = replace(
        marker,
        metadata=(
            ("algorithm_version", "2"),
            ("safety_report_fingerprint", "0" * 64),
            ("safety_status", "safe"),
        ),
    )
    events = list(candidate.artifact.events)
    events[marker_index] = stale_marker
    stale = replace(
        candidate.artifact,
        events=tuple(events),
        artifact_fingerprint=None,
    )
    assert not parallel_artifact_has_safe_contract(stale)


def test_simulation_preflight_rejects_candidate_even_if_lifecycle_is_forged_valid() -> None:
    fixture = planar_fixture(stepover=5.0)
    computing, candidate = _candidate(fixture)
    fingerprint = candidate.artifact.artifact_fingerprint
    assert fingerprint is not None
    state, accepted = computing.operation.artifact_state.publish(
        candidate.artifact.computation_token,
        candidate.artifact.input_fingerprint,
        fingerprint,
    )
    assert accepted
    operation = replace(computing.operation, artifact_state=state)
    with pytest.raises(SimulationPreflightError, match="SAFE") as captured:
        build_simulation_request(
            operation=operation,
            artifact=candidate.artifact,
            setup=None,  # type: ignore[arg-type]
            tool=fixture.tool,
            assembly=fixture.assembly,
            holder=None,
            machine=None,
        )
    assert captured.value.code is SimulationIssueCode.SOURCE_UNSUPPORTED


def test_disconnected_regions_use_retract_clearance_instead_of_direct_low_link() -> None:
    fixture = disconnected_fixture(stepover=5.0)
    _computing, candidate = _candidate(fixture)
    provenances = tuple(event.provenance for event in candidate.artifact.events)
    assert any(value.endswith(".retract") for value in provenances)
    assert any(value.endswith(".position.clearance") for value in provenances)
    assert not any("direct" in value for value in provenances)


def test_inner_hole_region_is_not_bridged_by_contact_segments() -> None:
    fixture = parallel_fixture(
        (
            ("bottom", ((0, 0, 0), (10, 0, 0), (10, 4, 0), (0, 4, 0)), ((0, 1, 2), (0, 2, 3))),
            ("top", ((0, 6, 0), (10, 6, 0), (10, 10, 0), (0, 10, 0)), ((0, 1, 2), (0, 2, 3))),
            ("left", ((0, 4, 0), (4, 4, 0), (4, 6, 0), (0, 6, 0)), ((0, 1, 2), (0, 2, 3))),
            ("right", ((6, 4, 0), (10, 4, 0), (10, 6, 0), (6, 6, 0)), ((0, 1, 2), (0, 2, 3))),
        ),
        stepover=1.0,
        maximum_segment_length=1.0,
    )
    _computing, candidate = _candidate(fixture)
    middle = min(candidate.preview.passes, key=lambda item: abs(item.v_position - 5.0))
    assert len(middle.segments) == 2
    for segment in middle.segments:
        x_values = tuple(point.contact_point.x for point in segment.points)
        assert max(x_values) <= 4.001 or min(x_values) >= 5.999


def test_sliver_face_is_bounded_and_deterministic() -> None:
    fixture = parallel_fixture(
        (
            ("main", ((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)), ((0, 1, 2), (0, 2, 3))),
            (
                "sliver",
                ((20, 0, 0), (20.0005, 0, 0), (20.0005, 10, 0), (20, 10, 0)),
                ((0, 1, 2), (0, 2, 3)),
            ),
        ),
        stepover=2.0,
        maximum_segment_length=1.0,
    )
    first = _candidate(fixture)[1]
    second = _candidate(fixture)[1]
    assert first.preview.statistics.contact_point_count < 1_000
    assert first.artifact.events == second.artifact.events
    assert first.artifact.artifact_fingerprint == second.artifact.artifact_fingerprint


def test_diagnostic_catalog_covers_required_safety_failures() -> None:
    codes = {item.value for item in DiagnosticCode}
    assert {
        "parallel.safety.cutter_gouge",
        "parallel.safety.shank_collision",
        "parallel.safety.holder_collision",
        "parallel.safety.rapid_collision",
        "parallel.safety.link_collision",
        "parallel.safety.approach_collision",
        "parallel.safety.retract_collision",
        "parallel.safety.protected_face_collision",
        "parallel.safety.boundary_ambiguity",
        "parallel.safety.sharp_edge_ambiguity",
        "parallel.safety.normal_ambiguity",
        "parallel.safety.unsupported_curvature",
        "parallel.safety.impossible_access",
        "parallel.safety.insufficient_clearance",
        "parallel.safety.missing_holder_geometry",
        "parallel.safety.missing_tool_geometry",
        "parallel.safety.invalid_margin",
        "parallel.safety.limit_exceeded",
        "parallel.safety.unknown",
        "parallel.safety.stale_artifact",
        "parallel.safety.cancelled",
    } <= codes


def test_rapid_crossing_is_found_on_full_motion_not_only_endpoints() -> None:
    fixture, _holder = rapid_crossing_fixture()
    computing, candidate = _candidate(fixture)
    source = candidate.artifact
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(),
        operation_id=source.source_operation_id,
        operation_revision=source.operation_revision,
        computation_token=source.computation_token,
        input_fingerprint=source.input_fingerprint,
        unit=source.unit,
        setup_id=source.setup_id,
        setup_revision=source.setup_revision,
        wcs_fingerprint=source.wcs_fingerprint,
        tool_assembly_id=source.tool_assembly_id,
        tool_assembly_fingerprint=source.tool_assembly_fingerprint,
    )
    axis = Vector3(0.0, 0.0, 1.0)
    builder.set_initial_pose(Pose(Point3(-10.0, 5.0, 6.0, LengthUnit.MM), axis))
    builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
    builder.rapid_to(
        Pose(Point3(20.0, 5.0, 6.0, LengthUnit.MM), axis),
        motion_class=MotionClass.NON_CUTTING,
        provenance="parallel.pass.0.segment.0.direct.rapid",
    )
    artifact = builder.finalize()
    report = validate_parallel_candidate_safety(
        operation=computing.operation,
        context=fixture.context,
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=None,
        artifact=artifact,
        preview=candidate.preview,
    )
    assert report.status is ParallelSafetyStatus.UNSAFE
    assert report.diagnostics[0].code.value == "parallel.safety.rapid_collision"
    assert report.statistics.swept_subdivision_count > 1


@pytest.mark.parametrize(
    ("start", "end", "provenance", "motion_class", "expected_code"),
    (
        (
            (10.0, 5.0, 40.0),
            (10.0, 5.0, 5.0),
            "parallel.pass.0.segment.0.approach",
            MotionClass.LINK,
            "parallel.safety.approach_collision",
        ),
        (
            (10.0, 5.0, 5.0),
            (10.0, 5.0, 40.0),
            "parallel.pass.0.segment.0.retract",
            MotionClass.RETRACT,
            "parallel.safety.retract_collision",
        ),
        (
            (-10.0, 5.0, 6.0),
            (20.0, 5.0, 6.0),
            "parallel.pass.0.segment.0.direct.link",
            MotionClass.LINK,
            "parallel.safety.link_collision",
        ),
    ),
)
def test_approach_retract_and_direct_link_use_the_same_swept_validator(
    start,
    end,
    provenance,
    motion_class,
    expected_code,
) -> None:
    fixture, _holder = adjacent_wall_fixture()
    computing, candidate = _candidate(fixture)
    artifact = _single_motion_artifact(
        candidate.artifact,
        start=start,
        end=end,
        provenance=provenance,
        motion_class=motion_class,
        rapid=False,
    )
    report = validate_parallel_candidate_safety(
        operation=computing.operation,
        context=fixture.context,
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=None,
        artifact=artifact,
        preview=candidate.preview,
    )
    assert report.status is ParallelSafetyStatus.UNSAFE
    assert report.diagnostics[0].code.value == expected_code


def test_swept_limit_and_cancellation_are_structured_and_never_safe() -> None:
    fixture = planar_fixture(stepover=5.0)
    computing, candidate = _candidate(fixture)
    policy = build_parallel_safety_policy(fixture.context, tool_radius_mm=5.0)
    limited = validate_parallel_candidate_safety(
        operation=computing.operation,
        context=fixture.context,
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=None,
        artifact=candidate.artifact,
        preview=candidate.preview,
        policy=replace(policy, maximum_swept_subdivisions=1),
    )
    assert limited.status is ParallelSafetyStatus.UNKNOWN
    assert limited.diagnostics[0].code.value == "parallel.safety.limit_exceeded"
    cancelled = validate_parallel_candidate_safety(
        operation=computing.operation,
        context=fixture.context,
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=None,
        artifact=candidate.artifact,
        preview=candidate.preview,
        cancellation=lambda: True,
    )
    assert cancelled.status is ParallelSafetyStatus.CANCELLED
    assert cancelled.diagnostics[0].code.value == "parallel.safety.cancelled"


def test_safety_report_and_collision_order_are_deterministic_three_runs() -> None:
    fixture, _holder = shank_collision_fixture()
    computing, candidate = _candidate(fixture)
    reports = tuple(
        validate_parallel_candidate_safety(
            operation=computing.operation,
            context=fixture.context,
            tool=fixture.tool,
            assembly=fixture.assembly,
            holder=None,
            artifact=candidate.artifact,
            preview=candidate.preview,
        )
        for _index in range(3)
    )
    assert len({item.fingerprint.digest for item in reports}) == 1
    assert len({tuple(value.code for value in item.diagnostics) for item in reports}) == 1
    assert all(item.statistics == reports[0].statistics for item in reports)


def test_unsafe_result_does_not_replace_previous_ready_artifact(tmp_path) -> None:
    safe = planar_fixture(stepover=5.0)
    ready = calculate_and_publish_parallel_finishing(
        tmp_path,
        safe.operation,
        safe.context,
        assembly=safe.assembly,
        tool=safe.tool,
    )
    assert ready.metadata is not None
    ready_path = tmp_path / ready.metadata.relative_path
    checksum = ready_path.read_bytes()
    unsafe, _holder = adjacent_wall_fixture()
    failed = calculate_and_publish_parallel_finishing(
        tmp_path,
        unsafe.operation,
        unsafe.context,
        assembly=unsafe.assembly,
        tool=unsafe.tool,
    )
    assert not failed.accepted and failed.metadata is None
    assert ready_path.read_bytes() == checksum


@pytest.mark.ocp
def test_convex_brep_is_safe_and_concave_channel_fails_closed(tmp_path) -> None:
    from tests.unit._parallel_finishing_ocp_fixtures import (
        concave_brep_tolerance_fixture,
        curved_brep_tolerance_fixture,
    )

    convex = curved_brep_tolerance_fixture(stepover=2.0)
    (tmp_path / "convex").mkdir()
    safe = calculate_and_publish_parallel_finishing(
        tmp_path / "convex",
        convex.fixture.operation,
        convex.fixture.context,
        assembly=convex.fixture.assembly,
        tool=convex.fixture.tool,
        contact_resolver=convex.resolver,
    )
    assert safe.accepted and safe.safety_report is not None
    assert safe.safety_report.status is ParallelSafetyStatus.SAFE
    concave = concave_brep_tolerance_fixture(stepover=2.0)
    unsafe = calculate_and_publish_parallel_finishing(
        tmp_path / "concave",
        concave.fixture.operation,
        concave.fixture.context,
        assembly=concave.fixture.assembly,
        tool=concave.fixture.tool,
        contact_resolver=concave.resolver,
    )
    assert not unsafe.accepted and unsafe.safety_report is not None
    assert unsafe.safety_report.status in {
        ParallelSafetyStatus.UNSAFE,
        ParallelSafetyStatus.UNKNOWN,
    }
    assert unsafe.safety_report.diagnostics[0].code.value in {
        "parallel.safety.cutter_gouge",
        "parallel.safety.unsupported_curvature",
    }
