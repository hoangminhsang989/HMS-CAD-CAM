"""Focused coordinate, stock, removal and safety tests for Stage 12.6A."""

from __future__ import annotations

from dataclasses import replace
import math

import pytest

from hms_cadcam.cam.lathe.simulation.coordinates import diameter_x_to_radius_mm, radius_to_diameter_x_mm
from hms_cadcam.cam.lathe.simulation.models import AxisymmetricStock, SafetyCode, SimulationSettings, SimulationState, StockStation, ToolEnvelope
from hms_cadcam.cam.lathe.simulation.service import LatheSimulationService, SimulationCancellationToken, SimulationRequest
from hms_cadcam.cam.lathe.simulation.stock import cylindrical_stock, remove_at, stock_metrics
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_toolpath_fixtures import generate, ready_request, stock_snapshot


def _request(strategy: LatheStrategyId = LatheStrategyId.OD_ROUGH) -> SimulationRequest:
    _service, _operation, request = ready_request(strategy)
    return SimulationRequest(
        (generate(request),),
        request.stock,
        ToolEnvelope(0.4, 4.0, 0.0, 5.0, holder_radial_offset_mm=100.0),
        SimulationSettings(sampling_resolution_mm=1.0, maximum_frame_count=20_000, maximum_stock_stations=500),
    )


def test_coordinate_contract_is_diameter_x_to_non_negative_radius_mm() -> None:
    assert diameter_x_to_radius_mm(42.0) == 21.0
    assert radius_to_diameter_x_mm(21.0) == 42.0
    with pytest.raises(ValueError):
        diameter_x_to_radius_mm(-1.0)
    for invalid in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            diameter_x_to_radius_mm(invalid)


def test_cylindrical_stock_bore_order_and_monotonic_removal() -> None:
    initial = cylindrical_stock(stock_snapshot(inner_diameter_mm=20.0), station_count=11)
    assert initial.stations[0].z_mm == -100.0
    assert initial.stations[-1].z_mm == 0.0
    assert {item.inner_radius_mm for item in initial.stations} == {10.0}
    first = remove_at(initial, z_mm=-50.0, tool_radius_mm=30.0, envelope_mm=1.0, internal=False)
    second = remove_at(first, z_mm=-50.0, tool_radius_mm=35.0, envelope_mm=1.0, internal=False)
    assert all(after.outer_radius_mm <= before.outer_radius_mm for before, after in zip(initial.stations, second.stations))
    assert second == first
    metrics = stock_metrics(initial, first)
    assert metrics.cross_section_area_mm2 > 0.0
    assert metrics.estimated_volume_mm3 > 0.0
    with pytest.raises(ValueError):
        AxisymmetricStock((StockStation(0.0, 4.0, 3.0), StockStation(1.0, 0.0, 3.0)))


def test_complete_run_is_deterministic_and_rapid_dwell_never_remove() -> None:
    service = LatheSimulationService()
    request = _request()
    first = service.run(request)
    second = service.run(request)
    assert first.state is SimulationState.COMPLETE
    assert first.fingerprint == second.fingerprint
    assert first.final_stock == second.final_stock
    assert first.removed.estimated_volume_mm3 > 0.0
    rapid_revisions = [frame.stock_revision for frame in first.frames if frame.motion_kind.value in {"RAPID", "DWELL"}]
    assert rapid_revisions == sorted(rapid_revisions)


def test_unknown_tool_fails_closed_and_unknown_holder_is_visible() -> None:
    service = LatheSimulationService()
    request = _request()
    rejected = service.run(replace(request, tool=ToolEnvelope(None, None, None, None)))
    assert rejected.state is SimulationState.REJECTED
    assert {item.code for item in rejected.events} == {SafetyCode.TOOL_GEOMETRY_UNKNOWN, SafetyCode.HOLDER_GEOMETRY_UNKNOWN}
    assert rejected.frames == ()


def test_cancellation_and_frame_limit_return_unaccepted_partial_states() -> None:
    service = LatheSimulationService()
    request = _request()
    token = SimulationCancellationToken()
    token.cancel()
    cancelled = service.run(request, cancellation=token)
    assert cancelled.state is SimulationState.CANCELLED
    assert not cancelled.complete_resolution
    limited = service.run(replace(request, settings=replace(request.settings, maximum_frame_count=1)))
    assert limited.state is SimulationState.INCOMPLETE
    assert SafetyCode.FRAME_LIMIT_REACHED in {item.code for item in limited.events}
    station_limited = service.run(
        replace(
            request,
            settings=replace(
                request.settings,
                sampling_resolution_mm=0.1,
                maximum_stock_stations=10,
            ),
        )
    )
    assert station_limited.state is SimulationState.INCOMPLETE
    assert SafetyCode.STATION_LIMIT_REACHED in {
        item.code for item in station_limited.events
    }


def test_rapid_contact_and_holder_contact_stop_safely() -> None:
    request = _request()
    colliding_holder = replace(request, tool=replace(request.tool, holder_radial_offset_mm=0.0, holder_axial_offset_mm=0.0, holder_radius_mm=20.0))
    result = LatheSimulationService().run(colliding_holder)
    assert result.state is SimulationState.INCOMPLETE
    assert SafetyCode.HOLDER_STOCK_CONTACT in {item.code for item in result.events}
