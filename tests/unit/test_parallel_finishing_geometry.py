"""Intersection, discretization, clipping and ordering tests for 8A.2.1."""

from __future__ import annotations

import pytest

from hms_cadcam.cam.cam3d.parallel import (
    ParallelCutDirection,
    ParallelFinishingError,
    ParallelFinishingGenerator,
    build_machining_frame,
    calculate_region_bounds,
    intersect_parallel_passes,
    plan_pass_positions,
)
from tests.unit._parallel_finishing_fixtures import (
    contiguous_fixture,
    curved_coarse_mesh_fixture,
    disconnected_fixture,
    inclined_fixture,
    planar_fixture,
)


def _geometry(fixture):
    parameters = fixture.operation.parameters
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    frame = build_machining_frame(
        fixture.zone,
        inputs.parameters.direction_angle_degrees,
        epsilon=fixture.zone.tolerance.calculation_epsilon,
    )
    bounds = calculate_region_bounds(
        fixture.mesh,
        frame,
        fixture.zone,
        padding=fixture.zone.tolerance.contact_tolerance,
    )
    positions = plan_pass_positions(
        bounds,
        inputs.parameters.stepover_mm,
        tolerance=fixture.zone.tolerance.contact_tolerance,
    )
    output = intersect_parallel_passes(
        fixture.context,
        frame,
        bounds,
        positions,
        inputs.parameters,
        tool_radius=inputs.tool_radius,
    )
    assert parameters == fixture.operation.parameters
    return frame, bounds, positions, output


def test_planar_face_produces_expected_straight_ordered_passes() -> None:
    fixture = planar_fixture(stepover=2.0, maximum_segment_length=2.0)
    frame, _bounds, positions, output = _geometry(fixture)
    assert positions == (0.0, 2.0, 4.0, 6.0, 8.0, 10.0)
    assert len(output.passes) == 6
    assert all(len(item.segments) == 1 for item in output.passes)
    for item in output.passes:
        points = item.segments[0].points
        assert frame.coordinates(points[0].contact_point)[0] < frame.coordinates(
            points[-1].contact_point
        )[0]
        assert {round(point.contact_point.z, 9) for point in points} == {0.0}
        assert {round(point.tool_center_point.z, 9) for point in points} == {5.0}


def test_inclined_face_changes_w_and_ball_center_coordinates() -> None:
    fixture = inclined_fixture(stepover=5.0)
    frame, _bounds, _positions, output = _geometry(fixture)
    segment = next(item.segments[0] for item in output.passes if item.segments)
    contact_w = [frame.coordinates(point.contact_point)[2] for point in segment.points]
    center_w = [frame.coordinates(point.tool_center_point)[2] for point in segment.points]
    assert max(contact_w) > min(contact_w)
    assert all(center > contact for center, contact in zip(center_w, contact_w, strict=True))


def test_curved_face_keeps_mesh_breakpoints_and_normal_variation() -> None:
    fixture = curved_coarse_mesh_fixture(stepover=2.0)
    _frame, _bounds, _positions, output = _geometry(fixture)
    normals = {
        (round(point.surface_normal.y, 3), round(point.surface_normal.z, 3))
        for item in output.passes
        for segment in item.segments
        for point in segment.points
    }
    assert len(normals) > 2
    assert output.raw_segment_count > 0


def test_coarse_curved_mesh_fails_closed_at_a_facet_normal_jump() -> None:
    fixture = curved_coarse_mesh_fixture(stepover=2.5)
    with pytest.raises(ParallelFinishingError) as captured:
        _geometry(fixture)
    assert captured.value.code.value == "parallel.contact_normal_discontinuity"


def test_contiguous_faces_stitch_and_preserve_both_source_ids() -> None:
    fixture = contiguous_fixture(stepover=5.0)
    _frame, _bounds, _positions, output = _geometry(fixture)
    middle = output.passes[1]
    assert len(middle.segments) == 1
    sources = {
        source
        for point in middle.segments[0].points
        for source in point.source_surface_ids
    }
    assert len(sources) == 2


def test_disconnected_regions_remain_two_segments_on_each_interior_pass() -> None:
    fixture = disconnected_fixture(stepover=5.0)
    _frame, _bounds, _positions, output = _geometry(fixture)
    assert len(output.passes[1].segments) == 2
    left, right = output.passes[1].segments
    assert left.points[-1].contact_point.x < right.points[0].contact_point.x


def test_zigzag_reverses_alternate_passes_deterministically() -> None:
    fixture = planar_fixture(
        stepover=2.0,
        cut_direction=ParallelCutDirection.ZIGZAG,
    )
    _frame, _bounds, _positions, output = _geometry(fixture)
    first = output.passes[0].segments[0]
    second = output.passes[1].segments[0]
    assert first.points[0].contact_point.x < first.points[-1].contact_point.x
    assert second.points[0].contact_point.x > second.points[-1].contact_point.x


def test_maximum_segment_length_controls_density_and_preserves_endpoints() -> None:
    sparse = planar_fixture(maximum_segment_length=5.0)
    dense = planar_fixture(maximum_segment_length=1.0)
    sparse_segment = _geometry(sparse)[3].passes[1].segments[0]
    dense_segment = _geometry(dense)[3].passes[1].segments[0]
    assert len(dense_segment.points) > len(sparse_segment.points)
    assert dense_segment.points[0].contact_point.x == pytest.approx(
        sparse_segment.points[0].contact_point.x
    )
    assert dense_segment.points[-1].contact_point.x == pytest.approx(
        sparse_segment.points[-1].contact_point.x
    )


def test_curve_point_count_guardrail_fails_before_large_allocation() -> None:
    fixture = planar_fixture(
        width=30.0,
        stepover=10.0,
        maximum_segment_length=0.001,
    )
    with pytest.raises(ParallelFinishingError) as captured:
        _geometry(fixture)
    assert captured.value.code.value == "parallel.limit_exceeded"


def test_closed_boundary_clips_contact_segments() -> None:
    fixture = planar_fixture(
        width=20.0,
        with_boundary=True,
        maximum_segment_length=2.0,
    )
    _frame, _bounds, _positions, output = _geometry(fixture)
    points = [
        point
        for item in output.passes
        for segment in item.segments
        for point in segment.points
    ]
    assert points
    assert max(point.contact_point.x for point in points) == pytest.approx(10.0)
    assert all(0.0 <= point.contact_point.x <= 10.0 for point in points)


def test_non_intersecting_positions_return_structured_error() -> None:
    fixture = planar_fixture()
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    frame = build_machining_frame(fixture.zone, 0.0, epsilon=1.0e-9)
    bounds = calculate_region_bounds(fixture.mesh, frame, fixture.zone, padding=0.001)
    with pytest.raises(ParallelFinishingError) as captured:
        intersect_parallel_passes(
            fixture.context,
            frame,
            bounds,
            (100.0,),
            inputs.parameters,
            tool_radius=inputs.tool_radius,
        )
    assert captured.value.code.value == "parallel.no_intersection"
