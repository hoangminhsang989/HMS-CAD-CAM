"""Stage 12.3 deterministic OD/ID thread generator tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest

from hms_cadcam.cam.lathe.toolpath import (
    EXECUTABLE_LATHE_TOOLPATH_STRATEGIES,
    LATHE_ID_THREAD_ALGORITHM_VERSION,
    LATHE_OD_THREAD_ALGORITHM_VERSION,
    LATHE_THREAD_TOOLPATH_PREVIEW_CAPABILITY,
    UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES,
    IdThreadToolpathGenerator,
    LatheMotionClass,
    LathePathSegment,
    LatheThreadPassMetadata,
    LatheToolpathDiagnosticCode,
    LatheToolpathGeneratorRegistry,
    LatheToolpathResultState,
    OdThreadToolpathGenerator,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId, LatheThreadHand
from tests.unit._lathe_toolpath_fixtures import (
    generate,
    ready_request,
    segments,
    stock_snapshot,
)


def _request(
    strategy_id: LatheStrategyId,
    *,
    parameters: dict[str, object] | None = None,
):
    return ready_request(
        strategy_id,
        parameters=parameters,
        stock=stock_snapshot(
            inner_diameter_mm=10.0
            if strategy_id is LatheStrategyId.ID_THREAD
            else 0.0
        ),
    )[2]


def _cutting(result):
    return tuple(
        item
        for item in segments(result)
        if item.motion_class is LatheMotionClass.CUTTING
    )


def test_registry_is_exact_eleven_zero_with_v3_versions() -> None:
    registry = LatheToolpathGeneratorRegistry()
    assert registry.executable_strategy_ids == tuple(LatheStrategyId)
    assert registry.executable_strategy_ids == EXECUTABLE_LATHE_TOOLPATH_STRATEGIES
    assert registry.unsupported_strategy_ids == ()
    assert UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES == ()
    assert len(set(registry.executable_strategy_ids)) == 11
    assert LATHE_OD_THREAD_ALGORITHM_VERSION == "lathe.od_thread.toolpath.v3"
    assert LATHE_ID_THREAD_ALGORITHM_VERSION == "lathe.id_thread.toolpath.v3"
    assert (
        LATHE_THREAD_TOOLPATH_PREVIEW_CAPABILITY
        == "lathe.thread.toolpath.preview.v3"
    )
    assert OdThreadToolpathGenerator().strategy_id is LatheStrategyId.OD_THREAD
    assert IdThreadToolpathGenerator().strategy_id is LatheStrategyId.ID_THREAD


@pytest.mark.parametrize(
    ("strategy_id", "expected"),
    (
        (LatheStrategyId.OD_THREAD, (19.0, 18.0, 17.0, 16.0, 16.0, 16.0)),
        (LatheStrategyId.ID_THREAD, (17.0, 18.0, 19.0, 20.0, 20.0, 20.0)),
    ),
)
def test_linear_pass_schedule_and_spring_passes_are_exact(
    strategy_id: LatheStrategyId,
    expected: tuple[float, ...],
) -> None:
    request = _request(
        strategy_id,
        parameters={
            "major_diameter_mm": 20.0,
            "minor_diameter_mm": 16.0,
            "pass_count": 4,
            "spring_passes": 2,
        },
    )
    result = generate(request)
    assert result.state is LatheToolpathResultState.SUCCESS
    assert result.pass_count == 6
    assert tuple(item.cutting_diameter_mm for item in result.thread_passes) == expected
    assert tuple(
        item.cumulative_radial_depth_mm for item in result.thread_passes
    ) == (0.5, 1.0, 1.5, 2.0, 2.0, 2.0)
    assert tuple(item.spring_pass_index for item in result.thread_passes) == (
        None,
        None,
        None,
        None,
        0,
        1,
    )
    assert tuple(item.pass_index for item in result.thread_passes) == tuple(range(6))
    assert all(item.cutting_pass_count == 4 for item in result.thread_passes)
    assert tuple(item.end.x_diameter_mm for item in _cutting(result)) == expected
    assert all(
        math.isfinite(value)
        for item in segments(result)
        for value in (
            item.start.x_diameter_mm,
            item.start.z_mm,
            item.end.x_diameter_mm,
            item.end.z_mm,
        )
    )


@pytest.mark.parametrize("strategy_id", (LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD))
@pytest.mark.parametrize(("start", "end"), ((-30.0, -5.0), (-5.0, -30.0)))
def test_one_pitch_lead_geometry_and_pitch_feed(
    strategy_id: LatheStrategyId,
    start: float,
    end: float,
) -> None:
    pitch = 1.25
    request = _request(
        strategy_id,
        parameters={
            "start_z_mm": start,
            "end_z_mm": end,
            "pitch_mm": pitch,
            "feed_mm_per_rev": 0.37,
            "pass_count": 1,
            "spring_passes": 0,
        },
    )
    result = generate(request)
    direction = 1.0 if end > start else -1.0
    lead_in = next(
        item for item in segments(result) if item.motion_class is LatheMotionClass.LEAD_IN
    )
    cutting = _cutting(result)[0]
    lead_out = next(
        item
        for item in segments(result)
        if item.motion_class is LatheMotionClass.LEAD_OUT
    )
    assert lead_in.start.z_mm == start - direction * pitch
    assert lead_in.end.z_mm == start
    assert cutting.start.z_mm == start and cutting.end.z_mm == end
    assert lead_out.start.z_mm == end
    assert lead_out.end.z_mm == end + direction * pitch
    assert all(
        item.feed_mm_per_rev == pitch
        for item in segments(result)
        if item.motion_class is not LatheMotionClass.RAPID
    )
    assert request.operation.parameters["feed_mm_per_rev"] == 0.37


def test_external_and_internal_safe_x_are_exact_and_non_negative() -> None:
    od = generate(
        _request(
            LatheStrategyId.OD_THREAD,
            parameters={"clearance_mm": 3.0, "pass_count": 1, "spring_passes": 0},
        )
    )
    id_result = generate(
        _request(
            LatheStrategyId.ID_THREAD,
            parameters={"clearance_mm": 3.0, "pass_count": 1, "spring_passes": 0},
        )
    )
    assert max(
        point
        for item in segments(od)
        for point in (item.start.x_diameter_mm, item.end.x_diameter_mm)
    ) == 106.0
    assert min(
        point
        for item in segments(id_result)
        for point in (item.start.x_diameter_mm, item.end.x_diameter_mm)
    ) == 4.0
    assert id_result.bounds is not None
    assert id_result.bounds.min_x_diameter_mm >= 0.0


def test_hand_and_infeed_are_immutable_metadata_without_geometry_compensation() -> None:
    right = generate(
        _request(
            LatheStrategyId.OD_THREAD,
            parameters={
                "thread_hand": LatheThreadHand.RIGHT,
                "infeed_angle_deg": 0.0,
                "pass_count": 2,
                "spring_passes": 0,
            },
        )
    )
    left = generate(
        _request(
            LatheStrategyId.OD_THREAD,
            parameters={
                "thread_hand": LatheThreadHand.LEFT,
                "infeed_angle_deg": 89.999,
                "pass_count": 2,
                "spring_passes": 0,
            },
        )
    )
    assert all(item.thread_hand is LatheThreadHand.RIGHT for item in right.thread_passes)
    assert all(item.thread_hand is LatheThreadHand.LEFT for item in left.thread_passes)
    assert all(item.phase_neutral for item in left.thread_passes)
    assert all(item.infeed_angle_deg == 89.999 for item in left.thread_passes)
    with pytest.raises(FrozenInstanceError):
        left.thread_passes[0].pass_index = 5  # type: ignore[misc]
    metadata = dict(left.thread_passes[0].canonical_metadata())
    assert metadata["thread_hand"] == "LEFT"
    assert metadata["phase_neutral"] is True
    assert metadata["spring_pass_index"] is None


@pytest.mark.parametrize("strategy_id", (LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD))
def test_success_diagnostics_are_complete_and_not_failures(
    strategy_id: LatheStrategyId,
) -> None:
    result = generate(_request(strategy_id))
    assert {item.code for item in result.diagnostics} == {
        LatheToolpathDiagnosticCode.PHASE_NEUTRAL_SYNCHRONIZED_CENTERLINE_PREVIEW,
        LatheToolpathDiagnosticCode.THREAD_FEED_DERIVED_FROM_PITCH,
        LatheToolpathDiagnosticCode.NOMINAL_INFEED_ANGLE_METADATA_ONLY,
        LatheToolpathDiagnosticCode.NOT_MACHINE_READY,
    }
    assert result.succeeded
    assert dict(result.generation_metadata) == {
        "cutting_feed_source": "pitch_mm",
        "global_algorithm_version": "lathe.toolpath.preview.v1",
        "infeed_model": "metadata_only",
        "phase_neutral": True,
        "preview_scope": "offline_nominal_xz",
        "strategy_algorithm_version": result.algorithm_version,
        "thread_hand": "RIGHT",
        "thread_preview_capability": "lathe.thread.toolpath.preview.v3",
    }


@pytest.mark.parametrize("strategy_id", (LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD))
def test_cancellation_discards_partial_thread_path(
    strategy_id: LatheStrategyId,
) -> None:
    request = _request(
        strategy_id,
        parameters={"pass_count": 50, "spring_passes": 5},
    )
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 20

    result = LatheToolpathGeneratorRegistry().generate(request, cancelled)
    assert result.state is LatheToolpathResultState.CANCELLED
    assert result.motions == ()
    assert result.thread_passes == ()


def test_typed_metadata_rejects_bool_pass_index_and_feed_mismatch() -> None:
    with pytest.raises(ValueError, match="pass index"):
        LatheThreadPassMetadata(
            True,  # type: ignore[arg-type]
            1,
            None,
            1.0,
            18.0,
            1.5,
            LatheThreadHand.RIGHT,
            29.0,
            True,
            1.5,
            LATHE_OD_THREAD_ALGORITHM_VERSION,
        )
    with pytest.raises(ValueError, match="equal pitch"):
        LatheThreadPassMetadata(
            0,
            1,
            None,
            1.0,
            18.0,
            1.5,
            LatheThreadHand.RIGHT,
            29.0,
            True,
            0.2,
            LATHE_OD_THREAD_ALGORITHM_VERSION,
        )
