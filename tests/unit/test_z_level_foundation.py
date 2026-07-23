"""Focused deterministic checks for Stage 8A.3.1 Z-Level foundation."""

from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path

import pytest

from hms_cadcam.cam.cam3d.zlevel import (
    Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    ZLevelBoundaryClassification,
    ZLevelFinishingError,
    ZLevelFinishingGenerator,
    ZLevelFinishingParameters,
    ZLevelMachiningFrame,
    ZLevelOrientation,
    calculate_and_publish_z_level_finishing,
    plan_level_schedule,
)
from hms_cadcam.cam.domain import CamValidationError, Vector3
from tests.unit._parallel_finishing_fixtures import (
    disconnected_fixture,
    parallel_fixture,
    planar_fixture,
)
from tests.unit._cam3d_fixtures import tool as build_tool


def _zlevel_operation(fixture, parameters: ZLevelFinishingParameters):
    return replace(fixture.operation, parameters=parameters.to_operation_parameters())


def _preview(fixture, parameters: ZLevelFinishingParameters):
    generator = ZLevelFinishingGenerator()
    inputs = generator.resolve_inputs(
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    return generator.generate(computing).preview


def test_strategy_versions_are_strategy_specific_and_parallel_is_unchanged() -> None:
    assert Z_LEVEL_FINISHING_ALGORITHM_VERSION == 1
    assert planar_fixture().operation.parameters.strategy_version == 1


def test_level_schedule_is_inclusive_index_based_and_deterministic() -> None:
    first = plan_level_schedule(10.0, 0.0, 3.0, tolerance=0.001)
    second = plan_level_schedule(10.0, 0.0, 3.0, tolerance=0.001)
    assert first == second
    assert first.levels == (10.0, 7.0, 4.0, 1.0, 0.0)
    assert len(first.levels) == len(set(first.levels))


def test_parameter_round_trip_keeps_frame_and_policies() -> None:
    fixture = planar_fixture()
    frame = ZLevelMachiningFrame(
        fixture.zone.wcs.origin,
        fixture.zone.wcs.x_axis,
        fixture.zone.wcs.y_axis,
        fixture.zone.wcs.z_axis,
    )
    value = ZLevelFinishingParameters(
        fixture.zone.zone_id,
        5.0,
        5.0,
        1.0,
        orientation=ZLevelOrientation.CLOCKWISE,
        setup_reference=str(fixture.zone.setup_id),
        machining_frame=frame,
    )
    restored = ZLevelFinishingParameters.from_operation_parameters(value.to_operation_parameters())
    assert restored == value
    assert restored.fingerprint == value.fingerprint


def test_horizontal_planar_face_traces_one_closed_tool_center_loop() -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    generator = ZLevelFinishingGenerator()
    inputs = generator.resolve_inputs(
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    candidate = generator.generate(computing)
    contour = candidate.preview.passes[0].segments[0]
    assert contour.closed
    assert contour.loop_type.value == "outer"
    assert len(contour.points) >= 4
    assert all(
        point.boundary_classification is ZLevelBoundaryClassification.INTERIOR
        for point in contour.points
    )
    assert all(abs(point.level_deviation_mm) <= parameters.tolerance_mm for point in contour.points)
    assert all(abs(computing.context.machining_zone.wcs.z_axis.dot(point.surface_normal)) > 0.9 for point in contour.points)


def test_vertical_wall_uses_tool_center_level_not_contact_level_offset() -> None:
    fixture = parallel_fixture(
        (
            (
                "vertical-wall",
                ((0.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 10.0, 10.0), (0.0, 0.0, 10.0)),
                ((0, 1, 2), (0, 2, 3)),
            ),
        )
    )
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 10.0, 0.0, 2.5)
    generator = ZLevelFinishingGenerator()
    inputs = generator.resolve_inputs(
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    preview = generator.generate(computing).preview
    assert len(preview.passes) >= 4
    for level_pass in preview.passes:
        for segment in level_pass.segments:
            for point in segment.points:
                assert computing.context.machining_zone.wcs.z_axis.dot(
                    Vector3(
                        point.tool_center_point.x,
                        point.tool_center_point.y,
                        point.tool_center_point.z,
                    )
                ) == pytest.approx(level_pass.level, abs=parameters.tolerance_mm)


def test_positive_allowance_changes_contact_to_center_distance() -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(
        fixture.zone.zone_id, 5.5, 5.5, 1.0, surface_allowance_mm=0.5
    )
    generator = ZLevelFinishingGenerator()
    inputs = generator.resolve_inputs(
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    preview = generator.generate(computing).preview
    point = preview.passes[0].segments[0].points[0]
    distance = (
        (point.tool_center_point.x - point.contact_point.x) ** 2
        + (point.tool_center_point.y - point.contact_point.y) ** 2
        + (point.tool_center_point.z - point.contact_point.z) ** 2
    ) ** 0.5
    assert distance == pytest.approx(5.5, abs=1.0e-8)
    assert point.allowance_deviation_mm <= parameters.tolerance_mm


def test_allowance_uses_nominal_contact_once_and_rejects_negative_sign() -> None:
    fixture = planar_fixture()
    zero = _preview(
        fixture,
        ZLevelFinishingParameters(
            fixture.zone.zone_id,
            5.0,
            5.0,
            1.0,
            surface_allowance_mm=0.0,
        ),
    ).passes[0].segments[0].points[0]
    positive = _preview(
        fixture,
        ZLevelFinishingParameters(
            fixture.zone.zone_id,
            5.5,
            5.5,
            1.0,
            surface_allowance_mm=0.5,
        ),
    ).passes[0].segments[0].points[0]
    assert positive.contact_point == zero.contact_point
    assert positive.tool_center_point.z - zero.tool_center_point.z == pytest.approx(0.5)
    assert positive.tool_center_point.z == pytest.approx(
        positive.contact_point.z
        + (5.0 + 0.5) * positive.surface_normal.z
    )
    with pytest.raises(CamValidationError):
        ZLevelFinishingParameters(
            fixture.zone.zone_id,
            5.0,
            5.0,
            1.0,
            surface_allowance_mm=-0.1,
        )


def test_cylinder_side_produces_closed_loops_without_seam_break() -> None:
    count = 12
    vertices = tuple(
        point
        for index in range(count)
        for point in (
            (5.0 * math.cos(index * math.tau / count), 5.0 * math.sin(index * math.tau / count), 0.0),
            (5.0 * math.cos(index * math.tau / count), 5.0 * math.sin(index * math.tau / count), 10.0),
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
    fixture = parallel_fixture((("cylinder-side", vertices, triangles),))
    preview = _preview(
        fixture,
        ZLevelFinishingParameters(fixture.zone.zone_id, 10.0, 0.0, 2.5),
    )
    assert len(preview.passes) == 5
    assert all(len(level.segments) == 1 and level.segments[0].closed for level in preview.passes)


def test_trimmed_planar_hole_keeps_inner_loop_and_opposite_orientation() -> None:
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
        (0, 1, 5), (0, 5, 4),
        (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6),
        (3, 0, 4), (3, 4, 7),
    )
    fixture = parallel_fixture((("trimmed-hole", vertices, triangles),))
    preview = _preview(
        fixture,
        ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0),
    )
    loops = preview.passes[0].segments
    assert {item.loop_type.value for item in loops} == {"outer", "inner"}
    assert {item.orientation.value for item in loops} == {"clockwise", "counter_clockwise"}


def test_disconnected_regions_remain_separate_closed_components() -> None:
    fixture = disconnected_fixture()
    preview = _preview(
        fixture,
        ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0),
    )
    assert len(preview.passes[0].segments) == 2
    assert all(item.closed and item.loop_type.value == "outer" for item in preview.passes[0].segments)


def test_self_intersecting_zero_contour_fails_closed() -> None:
    fixture = parallel_fixture(
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
        )
    )
    with pytest.raises(ZLevelFinishingError) as captured:
        _preview(
            fixture,
            ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0),
        )
    assert captured.value.code.value == "z_level.self_intersection"


def test_determinism_repeats_preview_hash_three_times() -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    digests = tuple(_preview(fixture, parameters).fingerprint.digest for _ in range(3))
    assert len(set(digests)) == 1


def test_non_ball_tool_fails_closed_without_parallel_fallback() -> None:
    fixture = parallel_fixture(
        (
            (
                "flat",
                ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)),
                ((0, 1, 2), (0, 2, 3)),
            ),
        )
    )
    non_ball_tool = build_tool(ball=False)
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 0.0, 0.0, 1.0)
    with pytest.raises(ZLevelFinishingError) as captured:
        ZLevelFinishingGenerator().resolve_inputs(
            _zlevel_operation(fixture, parameters),
            fixture.context,
            assembly=fixture.assembly,
            tool=non_ball_tool,
        )
    assert captured.value.code.value in {"z_level.invalid_tool", "z_level.unsupported_tool"}


def test_cancellation_does_not_publish_partial_artifact() -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 0.0, 0.5)
    root = Path(".pytest_tmp/zlevel_cancel.HMS")
    root.mkdir(parents=True, exist_ok=True)
    result = calculate_and_publish_z_level_finishing(
        root,
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        cancellation=lambda: True,
    )
    assert not result.accepted
    assert result.artifact is None
    assert result.diagnostics[0].code.value == "z_level.cancelled"


def test_full_publish_is_safe_and_strategy_marker_is_current(tmp_path: Path) -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    project_root = tmp_path / "Smoke.HMS"
    project_root.mkdir()
    result = calculate_and_publish_z_level_finishing(
        project_root,
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.accepted
    assert result.artifact is not None
    assert result.safety_report is not None
    assert result.safety_report.status.value == "safe"
