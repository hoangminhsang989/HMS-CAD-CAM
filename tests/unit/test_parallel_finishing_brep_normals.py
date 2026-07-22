"""Original-surface normal and ball-center offset tests for Stage 8A.2.1."""

from __future__ import annotations

import dataclasses
import math

import pytest

from hms_cadcam.cam.cam3d.parallel import (
    ParallelFinishingError,
    ParallelFinishingGenerator,
    ParallelNormalSource,
    calculate_and_publish_parallel_finishing,
)
from hms_cadcam.cam.adapters.ocp_cam3d import _meshing_parameters
from tests.unit._cam3d_fixtures import tolerance
from tests.unit._parallel_finishing_fixtures import (
    contiguous_fixture,
    parallel_fixture,
)
from tests.unit._parallel_finishing_ocp_fixtures import (
    curved_brep_tolerance_fixture,
    inclined_brep_tolerance_fixture,
)

pytestmark = pytest.mark.ocp


def _candidate(fixture, resolver=None):
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    return generator.generate(computing, contact_resolver=resolver)


def _points(candidate):
    return tuple(
        point
        for pass_value in candidate.preview.passes
        for segment in pass_value.segments
        for point in segment.points
    )


def _representative_points(candidate):
    return tuple(
        segment.points[len(segment.points) // 2]
        for pass_value in candidate.preview.passes
        for segment in pass_value.segments[:1]
    )


def _angle_degrees(first, second) -> float:
    cosine = max(-1.0, min(1.0, first.dot(second)))
    return math.degrees(math.acos(cosine))


def test_brep_ball_center_offset_uses_exact_radius_and_surface_normal() -> None:
    value = curved_brep_tolerance_fixture()
    candidate = _candidate(value.fixture, value.resolver)
    for point in _points(candidate):
        offset = (
            point.tool_center_point.x - point.contact_point.x,
            point.tool_center_point.y - point.contact_point.y,
            point.tool_center_point.z - point.contact_point.z,
        )
        assert math.dist((0.0, 0.0, 0.0), offset) == pytest.approx(5.0)
        normalized = tuple(component / 5.0 for component in offset)
        assert normalized == pytest.approx(
            (
                point.surface_normal.x,
                point.surface_normal.y,
                point.surface_normal.z,
            ),
            abs=1.0e-9,
        )
        assert point.normal_source is ParallelNormalSource.BREP_SURFACE


def test_cylinder_brep_normals_match_analytic_and_do_not_flip() -> None:
    value = curved_brep_tolerance_fixture()
    candidate = _candidate(value.fixture, value.resolver)
    representatives = _representative_points(candidate)
    for point in _points(candidate):
        expected = (
            0.0,
            point.contact_point.y / 5.0,
            point.contact_point.z / 5.0,
        )
        assert (
            point.surface_normal.x,
            point.surface_normal.y,
            point.surface_normal.z,
        ) == pytest.approx(expected, abs=2.0e-7)
        assert point.surface_normal.z > 0.0
        assert point.surface_projection_deviation_mm <= 0.011
    jumps = [
        _angle_degrees(first.surface_normal, second.surface_normal)
        for first, second in zip(representatives, representatives[1:])
    ]
    assert max(jumps) < 14.0
    assert all(
        first.surface_normal.dot(second.surface_normal) > 0.0
        for first, second in zip(representatives, representatives[1:])
    )


def test_curved_brep_tool_center_has_no_unexplained_transverse_jump() -> None:
    value = curved_brep_tolerance_fixture()
    candidate = _candidate(value.fixture, value.resolver)
    representatives = _representative_points(candidate)
    jumps = [
        math.dist(
            (
                first.tool_center_point.x,
                first.tool_center_point.y,
                first.tool_center_point.z,
            ),
            (
                second.tool_center_point.x,
                second.tool_center_point.y,
                second.tool_center_point.z,
            ),
        )
        for first, second in zip(representatives, representatives[1:])
    ]
    assert max(jumps) < 2.4


def test_inclined_brep_plane_normal_is_exact() -> None:
    value = inclined_brep_tolerance_fixture()
    points = _points(_candidate(value.fixture, value.resolver))
    expected = (-1.0 / math.sqrt(5.0), 0.0, 2.0 / math.sqrt(5.0))
    assert points
    assert all(
        (
            point.surface_normal.x,
            point.surface_normal.y,
            point.surface_normal.z,
        )
        == pytest.approx(expected, abs=1.0e-9)
        for point in points
    )


def test_sharp_edge_is_rejected_instead_of_averaging_face_normals() -> None:
    fixture = parallel_fixture(
        (
            (
                "sharp-left",
                ((0, 0, 0), (5, 0, 0), (5, 10, 0), (0, 10, 0)),
                ((0, 1, 2), (0, 2, 3)),
            ),
            (
                "sharp-right",
                ((5, 0, 0), (10, 0, 5), (10, 10, 5), (5, 10, 0)),
                ((0, 1, 2), (0, 2, 3)),
            ),
        ),
        stepover=5.0,
    )
    with pytest.raises(ParallelFinishingError) as captured:
        _candidate(fixture)
    assert captured.value.code.value == "parallel.contact_normal_discontinuity"


def test_source_face_association_survives_stitch_and_discretization() -> None:
    fixture = contiguous_fixture(stepover=5.0)
    candidate = _candidate(fixture)
    selected = {
        item.geometry.reference_id
        for item in fixture.zone.part_surfaces.selection.surfaces
    }
    points = _points(candidate)
    assert all(set(point.source_surface_ids) <= selected for point in points)
    assert any(len(point.source_surface_ids) == 2 for point in points)


def test_brep_normal_and_tool_center_are_deterministic_for_same_input() -> None:
    value = curved_brep_tolerance_fixture()
    first = _candidate(value.fixture, value.resolver)
    second = _candidate(value.fixture, value.resolver)
    assert first.preview.to_dict() == second.preview.to_dict()
    assert first.artifact.events == second.artifact.events
    assert first.artifact.artifact_fingerprint == second.artifact.artifact_fingerprint


def test_ocp_meshing_parameters_apply_complete_deterministic_policy() -> None:
    policy = dataclasses.replace(tolerance(0.01), minimum_triangle_size=0.002)
    parameters = _meshing_parameters(policy)
    assert parameters.Deflection == pytest.approx(0.01)
    assert parameters.DeflectionInterior == pytest.approx(0.01)
    assert parameters.Angle == pytest.approx(0.2)
    assert parameters.AngleInterior == pytest.approx(0.2)
    assert parameters.MinSize == pytest.approx(0.002)
    assert parameters.Relative is False
    assert parameters.InParallel is False


def test_brep_publish_does_not_emit_mesh_normal_approximation(tmp_path) -> None:
    value = curved_brep_tolerance_fixture()
    project = tmp_path / "BRepNormal.HMS"
    project.mkdir()
    result = calculate_and_publish_parallel_finishing(
        project,
        value.fixture.operation,
        value.fixture.context,
        assembly=value.fixture.assembly,
        tool=value.fixture.tool,
        contact_resolver=value.resolver,
    )
    assert result.accepted
    assert "parallel.foundation_limitation" in {
        item.code.value for item in result.diagnostics
    }
    assert "parallel.mesh_normal_approximation" not in {
        item.code.value for item in result.diagnostics
    }
