"""Deterministic OD rough/finish and axial-drill generator acceptance tests."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pytest

from hms_cadcam.cam.lathe.toolpath import (
    EXECUTABLE_LATHE_TOOLPATH_STRATEGIES,
    UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES,
    AxialDrillToolpathGenerator,
    LatheDwellEvent,
    LatheMotionClass,
    LathePathSegment,
    LatheToolpathDiagnosticCode,
    LatheToolpathGeneratorRegistry,
    LatheToolpathResultState,
    OdFinishToolpathGenerator,
    OdRoughToolpathGenerator,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_toolpath_fixtures import (
    generate,
    ready_request,
    segments,
    stock_snapshot,
)


def _motions_for_pass(result, pass_index: int) -> tuple[LathePathSegment, ...]:
    return tuple(
        event
        for event in result.motions
        if isinstance(event, LathePathSegment)
        and dict(event.metadata)["pass_index"] == pass_index
    )


def _cutting_segments(result) -> tuple[LathePathSegment, ...]:
    return tuple(
        event
        for event in result.motions
        if isinstance(event, LathePathSegment)
        and event.motion_class is LatheMotionClass.CUTTING
    )


def test_od_rough_one_pass_reaches_exact_allowance_target() -> None:
    request = ready_request(
        LatheStrategyId.OD_ROUGH,
        stock=stock_snapshot(outer_diameter_mm=44.0),
    )[2]
    result = generate(request)
    assert result.succeeded and result.pass_count == 1
    path = _motions_for_pass(result, 0)
    assert tuple(item.motion_class for item in path) == (
        LatheMotionClass.RAPID,
        LatheMotionClass.LEAD_IN,
        LatheMotionClass.CUTTING,
        LatheMotionClass.LEAD_OUT,
        LatheMotionClass.RAPID,
    )
    assert path[1].end.x_diameter_mm == 41.0
    assert path[2].start.x_diameter_mm == path[2].end.x_diameter_mm == 41.0
    assert path[2].end.z_mm == -49.8


def test_od_rough_multi_pass_converts_radial_doc_to_diameter_decrement() -> None:
    request = ready_request(LatheStrategyId.OD_ROUGH)[2]
    result = generate(request)
    lead_ins = tuple(
        event
        for event in segments(result)
        if event.motion_class is LatheMotionClass.LEAD_IN
    )
    diameters = tuple(event.end.x_diameter_mm for event in lead_ins)
    assert result.pass_count == len(diameters) == 15
    assert diameters[:3] == (96.0, 92.0, 88.0)
    assert all(
        math.isclose(first - second, 4.0, rel_tol=0.0, abs_tol=1.0e-9)
        for first, second in zip(diameters[:-2], diameters[1:-1], strict=True)
    )
    assert diameters[-2:] == (44.0, 41.0)


def test_od_rough_radial_and_axial_allowances_are_not_silently_changed() -> None:
    request = ready_request(
        LatheStrategyId.OD_ROUGH,
        parameters={
            "target_diameter_mm": 30.0,
            "radial_stock_to_leave_mm": 1.25,
            "axial_stock_to_leave_mm": 1.5,
            "max_depth_of_cut_mm": 5.0,
        },
    )[2]
    result = generate(request)
    final_cut = _cutting_segments(result)[-1]
    assert final_cut.start.x_diameter_mm == 32.5
    assert final_cut.end.x_diameter_mm == 32.5
    assert final_cut.end.z_mm == -48.5
    assert request.operation.parameters["radial_stock_to_leave_mm"] == 1.25


@pytest.mark.parametrize(
    ("stock", "start", "end", "expected_end"),
    (
        (stock_snapshot(), 0.0, -50.0, -49.8),
        (
            stock_snapshot(front_z_mm=-100.0, back_z_mm=0.0),
            -50.0,
            -10.0,
            -10.2,
        ),
    ),
)
def test_od_rough_supports_both_axial_directions(
    stock, start: float, end: float, expected_end: float
) -> None:
    request = ready_request(
        LatheStrategyId.OD_ROUGH,
        stock=stock,
        parameters={"start_z_mm": start, "end_z_mm": end},
    )[2]
    cuts = _cutting_segments(generate(request))
    assert cuts
    assert all(item.start.z_mm == start for item in cuts)
    assert all(item.end.z_mm == expected_end for item in cuts)


def test_od_rough_has_no_zero_length_motion_and_stays_in_validated_envelope() -> None:
    result = generate(ready_request(LatheStrategyId.OD_ROUGH)[2])
    assert all(item.length_mm > 1.0e-9 for item in segments(result))
    assert result.bounds is not None
    assert result.bounds.min_x_diameter_mm == 41.0
    assert result.bounds.max_x_diameter_mm == 104.0
    assert result.bounds.min_z_mm == -49.8
    assert result.bounds.max_z_mm == 2.0
    assert result.cutting_length_mm > 0.0
    assert result.rapid_length_mm > 0.0


def test_od_rough_cooperative_cancellation_discards_partial_generation() -> None:
    request = ready_request(
        LatheStrategyId.OD_ROUGH,
        parameters={"max_depth_of_cut_mm": 0.1},
    )[2]
    calls = 0

    def cancellation() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 25

    result = LatheToolpathGeneratorRegistry().generate(request, cancellation)
    assert calls >= 25
    assert result.state is LatheToolpathResultState.CANCELLED
    assert result.motions == () and result.bounds is None and result.pass_count == 0


def test_od_finish_one_multiple_and_spring_passes_share_exact_nominal_target() -> None:
    one = generate(ready_request(LatheStrategyId.OD_FINISH)[2])
    assert one.pass_count == 1
    assert len(_cutting_segments(one)) == 1

    multiple_request = ready_request(
        LatheStrategyId.OD_FINISH,
        parameters={"finish_passes": 2, "spring_passes": 2},
    )[2]
    multiple = generate(multiple_request)
    assert multiple.pass_count == 4
    assert tuple(item.start.x_diameter_mm for item in _cutting_segments(multiple)) == (
        40.0,
        40.0,
        40.0,
        40.0,
    )
    assert multiple.diagnostics[0].code is (
        LatheToolpathDiagnosticCode.NOMINAL_CENTERLINE_PREVIEW
    )
    assert dict(multiple.generation_metadata)["preview_scope"] == "offline_nominal_xz"


@pytest.mark.parametrize(
    ("stock", "start", "end"),
    (
        (stock_snapshot(), 0.0, -50.0),
        (stock_snapshot(front_z_mm=-100.0, back_z_mm=0.0), -50.0, -10.0),
    ),
)
def test_od_finish_supports_both_z_directions_without_compensation(
    stock, start: float, end: float
) -> None:
    request = ready_request(
        LatheStrategyId.OD_FINISH,
        stock=stock,
        parameters={"start_z_mm": start, "end_z_mm": end},
    )[2]
    result = generate(request)
    cut = _cutting_segments(result)[0]
    assert (cut.start.z_mm, cut.end.z_mm) == (start, end)
    assert cut.start.x_diameter_mm == cut.end.x_diameter_mm == 40.0
    assert "compensation" not in dict(result.generation_metadata)


def test_od_finish_cancellation_is_checked_between_passes() -> None:
    request = ready_request(
        LatheStrategyId.OD_FINISH,
        parameters={"finish_passes": 20, "spring_passes": 5},
    )[2]
    calls = 0

    def cancellation() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 18

    result = LatheToolpathGeneratorRegistry().generate(request, cancellation)
    assert result.state is LatheToolpathResultState.CANCELLED
    assert result.motions == ()


def test_axial_drill_simple_is_centerline_z_only_and_reaches_exact_depth() -> None:
    result = generate(ready_request(LatheStrategyId.AXIAL_DRILL)[2])
    path = segments(result)
    assert result.pass_count == 1 and len(path) == 4
    assert tuple(item.motion_class for item in path) == (
        LatheMotionClass.RAPID,
        LatheMotionClass.CUTTING,
        LatheMotionClass.RAPID,
        LatheMotionClass.RAPID,
    )
    assert all(
        point.x_diameter_mm == 0.0
        for item in path
        for point in (item.start, item.end)
    )
    assert _cutting_segments(result)[0].end.z_mm == -30.0
    assert path[0].end.z_mm == path[2].end.z_mm == 2.0


def test_axial_drill_pecks_retract_each_time_and_final_partial_peck_is_exact() -> None:
    request = ready_request(
        LatheStrategyId.AXIAL_DRILL,
        parameters={"peck_depth_mm": 8.0},
    )[2]
    result = generate(request)
    cuts = _cutting_segments(result)
    assert result.pass_count == 4
    assert tuple(item.end.z_mm for item in cuts) == (-8.0, -16.0, -24.0, -30.0)
    assert all(item.start.z_mm == 2.0 for item in cuts)
    retracts = tuple(
        item
        for item in segments(result)
        if ".retract" in item.semantic_source
        and item.semantic_source != "axial_drill.retract_to_safe"
    )
    assert len(retracts) == 4
    assert all(item.end.z_mm == 2.0 for item in retracts)
    assert all(item.length_mm > 1.0e-9 for item in segments(result))


def test_axial_drill_positive_dwell_is_typed_at_final_depth_only() -> None:
    request = ready_request(
        LatheStrategyId.AXIAL_DRILL,
        parameters={"peck_depth_mm": 8.0, "dwell_seconds": 0.5},
    )[2]
    result = generate(request)
    dwells = tuple(item for item in result.motions if isinstance(item, LatheDwellEvent))
    assert len(dwells) == 1
    assert dwells[0].position == _cutting_segments(result)[-1].end
    assert dwells[0].duration_seconds == 0.5
    assert tuple(item.sequence_index for item in result.motions) == tuple(
        range(len(result.motions))
    )


def test_axial_drill_zero_dwell_and_optional_peck_emit_no_dwell_or_fake_segment() -> None:
    result = generate(ready_request(LatheStrategyId.AXIAL_DRILL)[2])
    assert not any(isinstance(item, LatheDwellEvent) for item in result.motions)
    assert all(item.length_mm > 1.0e-9 for item in segments(result))


def test_axial_drill_uses_normalized_stock_front_to_back_direction() -> None:
    request = ready_request(
        LatheStrategyId.AXIAL_DRILL,
        stock=stock_snapshot(front_z_mm=-100.0, back_z_mm=0.0),
        parameters={"depth_mm": 30.0, "retract_plane_z_mm": -102.0},
    )[2]
    cut = _cutting_segments(generate(request))[0]
    assert cut.start.z_mm == -102.0
    assert cut.end.z_mm == -70.0


def test_axial_drill_cancellation_between_pecks_discards_partial_path() -> None:
    request = ready_request(
        LatheStrategyId.AXIAL_DRILL,
        parameters={"peck_depth_mm": 1.0},
    )[2]
    calls = 0

    def cancellation() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 12

    result = LatheToolpathGeneratorRegistry().generate(request, cancellation)
    assert result.state is LatheToolpathResultState.CANCELLED
    assert result.motions == ()


def test_registry_preserves_stage12_1_generators_inside_v2_partition() -> None:
    registry = LatheToolpathGeneratorRegistry()
    assert registry.executable_strategy_ids == EXECUTABLE_LATHE_TOOLPATH_STRATEGIES
    assert registry.unsupported_strategy_ids == UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES
    assert len(registry.executable_strategy_ids) == 9
    assert len(registry.unsupported_strategy_ids) == 2
    assert set(registry.executable_strategy_ids).isdisjoint(
        registry.unsupported_strategy_ids
    )


def test_generator_output_is_byte_semantic_deterministic_across_repeated_runs() -> None:
    for strategy_id in (LatheStrategyId.OD_ROUGH, LatheStrategyId.OD_FINISH, LatheStrategyId.AXIAL_DRILL):
        request = ready_request(strategy_id)[2]
        results = tuple(generate(request) for _index in range(5))
        first = results[0]
        assert all(item.motions == first.motions for item in results)
        assert all(item.bounds == first.bounds for item in results)
        assert all(item.pass_count == first.pass_count for item in results)
        assert all(item.generation_metadata == first.generation_metadata for item in results)


@dataclass(frozen=True)
class _ExplodingRoughGenerator:
    strategy_id: LatheStrategyId = LatheStrategyId.OD_ROUGH

    def generate(self, request, cancellation):
        raise RuntimeError("native detail must not escape")


def test_unexpected_generator_exception_maps_to_structured_failure_without_path() -> None:
    registry = LatheToolpathGeneratorRegistry(
        (
            _ExplodingRoughGenerator(),
            OdFinishToolpathGenerator(),
            AxialDrillToolpathGenerator(),
        )
    )
    result = registry.generate(
        ready_request(LatheStrategyId.OD_ROUGH)[2],
        lambda: False,
    )
    assert result.state is LatheToolpathResultState.GENERATION_FAILED
    assert result.diagnostics[0].code is LatheToolpathDiagnosticCode.GENERATION_FAILED
    assert result.motions == () and result.bounds is None


def test_registry_rejects_duplicate_or_incomplete_registration() -> None:
    with pytest.raises(ValueError, match="duplicated"):
        LatheToolpathGeneratorRegistry(
            (
                OdRoughToolpathGenerator(),
                OdRoughToolpathGenerator(),
            )
        )
    with pytest.raises(ValueError, match="exact Stage 12.1 override set"):
        LatheToolpathGeneratorRegistry(
            (OdRoughToolpathGenerator(), OdFinishToolpathGenerator())
        )
    registry = LatheToolpathGeneratorRegistry(
        (
            OdRoughToolpathGenerator(),
            OdFinishToolpathGenerator(),
            AxialDrillToolpathGenerator(),
        )
    )
    assert len(registry.executable_strategy_ids) == 9
