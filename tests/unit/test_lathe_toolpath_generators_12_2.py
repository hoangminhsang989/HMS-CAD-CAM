"""Stage 12.2 deterministic FACE, ID, groove and PART_OFF acceptance."""

from __future__ import annotations

import math

import pytest

from hms_cadcam.cam.lathe.toolpath import (
    EXECUTABLE_LATHE_TOOLPATH_STRATEGIES,
    LATHE_FACE_ALGORITHM_VERSION,
    LATHE_ID_FINISH_ALGORITHM_VERSION,
    LATHE_ID_GROOVE_ALGORITHM_VERSION,
    LATHE_ID_ROUGH_ALGORITHM_VERSION,
    LATHE_OD_GROOVE_ALGORITHM_VERSION,
    LATHE_PART_OFF_ALGORITHM_VERSION,
    UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES,
    LatheMotionClass,
    LathePathSegment,
    LatheToolpathDiagnosticCode,
    LatheToolpathGeneratorRegistry,
    LatheToolpathResultState,
    strategy_algorithm_version,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_toolpath_fixtures import (
    generate,
    ready_request,
    segments,
    stock_snapshot,
)


NEW_STRATEGIES = (
    LatheStrategyId.FACE,
    LatheStrategyId.ID_ROUGH,
    LatheStrategyId.ID_FINISH,
    LatheStrategyId.OD_GROOVE,
    LatheStrategyId.ID_GROOVE,
    LatheStrategyId.PART_OFF,
)

EXPECTED_DIAGNOSTIC = {
    LatheStrategyId.FACE: LatheToolpathDiagnosticCode.NOMINAL_FACING_CENTERLINE_PREVIEW,
    LatheStrategyId.ID_ROUGH: LatheToolpathDiagnosticCode.NOMINAL_INTERNAL_CENTERLINE_PREVIEW,
    LatheStrategyId.ID_FINISH: LatheToolpathDiagnosticCode.NOMINAL_INTERNAL_CENTERLINE_PREVIEW,
    LatheStrategyId.OD_GROOVE: LatheToolpathDiagnosticCode.NOMINAL_MULTI_PLUNGE_GROOVE_PREVIEW,
    LatheStrategyId.ID_GROOVE: LatheToolpathDiagnosticCode.NOMINAL_INTERNAL_MULTI_PLUNGE_GROOVE_PREVIEW,
    LatheStrategyId.PART_OFF: LatheToolpathDiagnosticCode.NOMINAL_PART_OFF_CENTERLINE_PREVIEW,
}


def _request(
    strategy_id: LatheStrategyId,
    *,
    parameters: dict[str, object] | None = None,
    stock=None,
):
    values: dict[str, object] = {}
    selected_stock = stock
    if strategy_id is LatheStrategyId.FACE:
        values = {
            "face_z_mm": -2.0,
            "outer_diameter_mm": 80.0,
            "inner_diameter_mm": 0.0,
            "max_depth_of_cut_mm": 0.75,
            "finish_allowance_mm": 0.25,
        }
    elif strategy_id in {
        LatheStrategyId.ID_ROUGH,
        LatheStrategyId.ID_FINISH,
        LatheStrategyId.ID_GROOVE,
    }:
        selected_stock = selected_stock or stock_snapshot(inner_diameter_mm=10.0)
    if parameters:
        values.update(parameters)
    return ready_request(
        strategy_id,
        parameters=values,
        stock=selected_stock,
    )[2]


def _cuts(result) -> tuple[LathePathSegment, ...]:
    return tuple(
        item
        for item in result.motions
        if isinstance(item, LathePathSegment)
        and item.motion_class is LatheMotionClass.CUTTING
    )


def test_registry_is_exact_ordered_eleven_executable_zero_unsupported() -> None:
    registry = LatheToolpathGeneratorRegistry()
    assert registry.executable_strategy_ids == EXECUTABLE_LATHE_TOOLPATH_STRATEGIES
    assert registry.unsupported_strategy_ids == UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES
    assert registry.executable_strategy_ids == tuple(LatheStrategyId)
    assert registry.unsupported_strategy_ids == ()
    assert len(set(registry.executable_strategy_ids)) == 11


def test_six_algorithm_versions_are_exact() -> None:
    assert LATHE_FACE_ALGORITHM_VERSION == "lathe.face.toolpath.v2"
    assert LATHE_ID_ROUGH_ALGORITHM_VERSION == "lathe.id_rough.toolpath.v2"
    assert LATHE_ID_FINISH_ALGORITHM_VERSION == "lathe.id_finish.toolpath.v2"
    assert LATHE_OD_GROOVE_ALGORITHM_VERSION == "lathe.od_groove.toolpath.v2"
    assert LATHE_ID_GROOVE_ALGORITHM_VERSION == "lathe.id_groove.toolpath.v2"
    assert LATHE_PART_OFF_ALGORITHM_VERSION == "lathe.part_off.toolpath.v2"
    assert tuple(strategy_algorithm_version(item) for item in NEW_STRATEGIES) == (
        LATHE_FACE_ALGORITHM_VERSION,
        LATHE_ID_ROUGH_ALGORITHM_VERSION,
        LATHE_ID_FINISH_ALGORITHM_VERSION,
        LATHE_OD_GROOVE_ALGORITHM_VERSION,
        LATHE_ID_GROOVE_ALGORITHM_VERSION,
        LATHE_PART_OFF_ALGORITHM_VERSION,
    )


@pytest.mark.parametrize("strategy_id", NEW_STRATEGIES)
def test_new_generators_are_deterministic_finite_contiguous_and_safe(
    strategy_id: LatheStrategyId,
) -> None:
    request = _request(strategy_id)
    first = generate(request)
    repeated = tuple(generate(request) for _index in range(4))
    assert first.succeeded
    assert all(item.motions == first.motions for item in repeated)
    assert all(item.bounds == first.bounds for item in repeated)
    assert first.diagnostics[0].code is EXPECTED_DIAGNOSTIC[strategy_id]
    assert tuple(item.sequence_index for item in first.motions) == tuple(
        range(len(first.motions))
    )
    path = segments(first)
    assert path
    assert all(item.length_mm > 1.0e-9 for item in path)
    assert all(
        math.isfinite(value)
        for item in path
        for value in (
            item.start.x_diameter_mm,
            item.start.z_mm,
            item.end.x_diameter_mm,
            item.end.z_mm,
        )
    )
    assert all(
        point.x_diameter_mm >= 0.0
        for item in path
        for point in (item.start, item.end)
    )
    assert all(
        not (
            first_item.motion_class is second_item.motion_class
            and first_item.start == second_item.start
            and first_item.end == second_item.end
        )
        for first_item, second_item in zip(path, path[1:], strict=False)
    )


@pytest.mark.parametrize(
    ("stock", "face", "allowance", "expected_planes"),
    (
        (stock_snapshot(), -2.0, 0.25, (-0.75, -1.5, -1.75)),
        (
            stock_snapshot(front_z_mm=-100.0, back_z_mm=0.0),
            -98.0,
            0.25,
            (-99.25, -98.5, -98.25),
        ),
    ),
)
def test_face_multiple_slices_follow_stock_direction_and_exact_effective_plane(
    stock, face: float, allowance: float, expected_planes: tuple[float, ...]
) -> None:
    result = generate(
        _request(
            LatheStrategyId.FACE,
            stock=stock,
            parameters={
                "face_z_mm": face,
                "finish_allowance_mm": allowance,
            },
        )
    )
    cuts = _cuts(result)
    assert result.pass_count == 3
    assert tuple(item.start.z_mm for item in cuts) == expected_planes
    assert tuple(item.end.z_mm for item in cuts) == expected_planes
    assert all(item.start.x_diameter_mm == 80.0 for item in cuts)
    assert all(item.end.x_diameter_mm == 0.0 for item in cuts)


def test_face_one_slice_reaches_exact_plane_with_safe_motion_classes() -> None:
    result = generate(
        _request(
            LatheStrategyId.FACE,
            parameters={
                "face_z_mm": -0.5,
                "finish_allowance_mm": 0.0,
                "max_depth_of_cut_mm": 1.0,
            },
        )
    )
    assert result.pass_count == 1
    assert _cuts(result)[0].start.z_mm == -0.5
    assert tuple(item.motion_class for item in segments(result)) == (
        LatheMotionClass.RAPID,
        LatheMotionClass.LEAD_IN,
        LatheMotionClass.CUTTING,
        LatheMotionClass.LEAD_OUT,
        LatheMotionClass.RAPID,
    )


@pytest.mark.parametrize(
    ("stock", "start", "end", "expected_end"),
    (
        (stock_snapshot(inner_diameter_mm=10.0), 0.0, -30.0, -29.8),
        (
            stock_snapshot(
                inner_diameter_mm=10.0,
                front_z_mm=-100.0,
                back_z_mm=0.0,
            ),
            -80.0,
            -20.0,
            -20.2,
        ),
    ),
)
def test_id_rough_doc_allowances_and_both_directions(
    stock, start: float, end: float, expected_end: float
) -> None:
    result = generate(
        _request(
            LatheStrategyId.ID_ROUGH,
            stock=stock,
            parameters={
                "start_z_mm": start,
                "end_z_mm": end,
                "target_diameter_mm": 24.0,
                "max_depth_of_cut_mm": 2.0,
                "radial_stock_to_leave_mm": 0.5,
                "axial_stock_to_leave_mm": 0.2,
            },
        )
    )
    cuts = _cuts(result)
    assert tuple(item.start.x_diameter_mm for item in cuts) == (
        14.0,
        18.0,
        22.0,
        23.0,
    )
    assert cuts[-1].end.x_diameter_mm == 23.0
    assert all(item.end.z_mm == expected_end for item in cuts)
    assert min(point.x_diameter_mm for item in segments(result) for point in (item.start, item.end)) == 6.0


def test_id_finish_finish_and_spring_passes_share_target() -> None:
    result = generate(
        _request(
            LatheStrategyId.ID_FINISH,
            parameters={"finish_passes": 2, "spring_passes": 2},
        )
    )
    assert result.pass_count == 4
    assert tuple(item.start.x_diameter_mm for item in _cuts(result)) == (
        20.0,
        20.0,
        20.0,
        20.0,
    )


@pytest.mark.parametrize(
    ("strategy_id", "stock", "expected_positions"),
    (
        (
            LatheStrategyId.OD_GROOVE,
            stock_snapshot(),
            (-18.6, -19.53333333333333, -20.46666666666667, -21.4),
        ),
        (
            LatheStrategyId.ID_GROOVE,
            stock_snapshot(inner_diameter_mm=10.0),
            (-18.6, -19.53333333333333, -20.46666666666667, -21.4),
        ),
        (
            LatheStrategyId.OD_GROOVE,
            stock_snapshot(front_z_mm=-100.0, back_z_mm=0.0),
            (-21.4, -20.46666666666667, -19.53333333333333, -18.6),
        ),
    ),
)
def test_groove_boundaries_spacing_order_and_exact_target(
    strategy_id: LatheStrategyId,
    stock,
    expected_positions: tuple[float, ...],
) -> None:
    result = generate(_request(strategy_id, stock=stock))
    cuts = _cuts(result)
    assert result.pass_count == 4
    actual = tuple(item.start.z_mm for item in cuts)
    assert actual == pytest.approx(expected_positions)
    ordered = tuple(sorted(actual))
    assert ordered[0] == pytest.approx(-21.4)
    assert ordered[-1] == pytest.approx(-18.6)
    assert max(second - first for first, second in zip(ordered, ordered[1:], strict=False)) <= 1.0
    assert all(item.end.x_diameter_mm == 35.0 for item in cuts) if strategy_id is LatheStrategyId.OD_GROOVE else all(item.end.x_diameter_mm == 25.0 for item in cuts)


def test_part_off_stages_reach_centerline_without_negative_x() -> None:
    result = generate(
        _request(
            LatheStrategyId.PART_OFF,
            parameters={"target_diameter_mm": 0.0, "max_step_mm": 10.0},
        )
    )
    cuts = _cuts(result)
    assert result.pass_count == 5
    assert tuple(item.end.x_diameter_mm for item in cuts) == (
        80.0,
        60.0,
        40.0,
        20.0,
        0.0,
    )
    assert cuts[-1].end.x_diameter_mm == 0.0
    assert min(point.x_diameter_mm for item in segments(result) for point in (item.start, item.end)) == 0.0


def test_part_off_side_clearance_uses_axial_lead_at_stock_od() -> None:
    result = generate(
        _request(
            LatheStrategyId.PART_OFF,
            parameters={"cutoff_z_mm": -20.0, "side_clearance_mm": 0.5},
        )
    )
    axial_leads = tuple(
        item
        for item in segments(result)
        if item.semantic_source.endswith(".axial_lead_to_cutoff")
    )
    assert len(axial_leads) == result.pass_count
    assert all(item.motion_class is LatheMotionClass.LEAD_IN for item in axial_leads)
    assert all(item.start.x_diameter_mm == 100.0 for item in axial_leads)
    assert all(item.end.x_diameter_mm == 100.0 for item in axial_leads)
    assert all(item.start.z_mm == -19.5 for item in axial_leads)
    assert all(item.end.z_mm == -20.0 for item in axial_leads)


def test_part_off_hollow_stock_stops_at_existing_bore() -> None:
    result = generate(
        _request(
            LatheStrategyId.PART_OFF,
            stock=stock_snapshot(inner_diameter_mm=10.0),
            parameters={"target_diameter_mm": 10.0, "max_step_mm": 15.0},
        )
    )
    assert result.succeeded
    assert _cuts(result)[-1].end.x_diameter_mm == 10.0


@pytest.mark.parametrize("strategy_id", NEW_STRATEGIES)
def test_cancellation_discards_partial_path_for_each_new_strategy(
    strategy_id: LatheStrategyId,
) -> None:
    parameters: dict[str, object] = {}
    if strategy_id is LatheStrategyId.FACE:
        parameters = {"face_z_mm": -20.0, "max_depth_of_cut_mm": 0.1}
    elif strategy_id is LatheStrategyId.ID_ROUGH:
        parameters = {"max_depth_of_cut_mm": 0.1}
    elif strategy_id is LatheStrategyId.ID_FINISH:
        parameters = {"finish_passes": 50, "spring_passes": 10}
    elif strategy_id in {LatheStrategyId.OD_GROOVE, LatheStrategyId.ID_GROOVE}:
        parameters = {"groove_width_mm": 20.0, "max_step_mm": 0.2}
    elif strategy_id is LatheStrategyId.PART_OFF:
        parameters = {"max_step_mm": 0.2}
    request = _request(strategy_id, parameters=parameters)
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 12

    result = LatheToolpathGeneratorRegistry().generate(request, cancelled)
    assert result.state is LatheToolpathResultState.CANCELLED
    assert result.motions == ()
    assert result.bounds is None
