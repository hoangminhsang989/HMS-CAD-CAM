"""All exact eleven canonical Lathe strategies reach the simulation planner."""

from __future__ import annotations

from hms_cadcam.cam.lathe.simulation.models import SafetyCode, SimulationSettings, ToolEnvelope
from hms_cadcam.cam.lathe.simulation.planner import build_simulation_plan
from hms_cadcam.cam.lathe.simulation.service import LatheSimulationService, SimulationRequest
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_toolpath_fixtures import generate, ready_request, stock_snapshot


def _result(strategy: LatheStrategyId, index: int):
    parameters = None
    stock = None
    if strategy is LatheStrategyId.FACE:
        parameters = {
            "face_z_mm": -2.0,
            "outer_diameter_mm": 80.0,
            "inner_diameter_mm": 0.0,
            "max_depth_of_cut_mm": 0.75,
            "finish_allowance_mm": 0.25,
        }
    if strategy in {
        LatheStrategyId.ID_ROUGH,
        LatheStrategyId.ID_FINISH,
        LatheStrategyId.ID_GROOVE,
        LatheStrategyId.ID_THREAD,
    }:
        stock = stock_snapshot(inner_diameter_mm=10.0)
    _service, _operation, request = ready_request(
        strategy,
        parameters=parameters,
        stock=stock,
        operation_index=index,
        tool_index=index,
    )
    return generate(request), request.stock


def test_exact_11_strategy_ids_plan_in_deterministic_operation_order() -> None:
    generated = tuple(_result(strategy, index + 1)[0] for index, strategy in enumerate(LatheStrategyId))
    first = build_simulation_plan(generated)
    second = build_simulation_plan(generated)
    assert first == second
    assert first.strategy_ids == tuple(LatheStrategyId)
    assert len(set(first.strategy_ids)) == 11
    assert len(first.operation_ids) == 11
    assert all(item.strategy_id in LatheStrategyId for item in first.motions)


def test_thread_strategies_animate_with_explicit_approximation_notice() -> None:
    for index, strategy in enumerate((LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD), 1):
        result, stock = _result(strategy, index)
        simulation = LatheSimulationService().run(
            SimulationRequest(
                (result,), stock,
                ToolEnvelope(0.2, 3.0, 0.0, 4.0, holder_radial_offset_mm=100.0),
                SimulationSettings(sampling_resolution_mm=2.0, maximum_frame_count=20_000, maximum_stock_stations=200),
            )
        )
        assert simulation.frames
        assert SafetyCode.THREAD_PROFILE_APPROXIMATION in {item.code for item in simulation.events}
        assert all("exact" not in item.removed.approximation.casefold() for item in simulation.frames)
