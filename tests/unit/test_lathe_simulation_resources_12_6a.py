"""Bounded replay/cancellation cycle probes required before the full gate."""

from __future__ import annotations

from dataclasses import replace
import threading

from hms_cadcam.cam.lathe.simulation.models import SimulationSettings, SimulationState, ToolEnvelope
from hms_cadcam.cam.lathe.simulation.service import LatheSimulationService, SimulationCancellationToken, SimulationRequest
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_toolpath_fixtures import generate, ready_request


def _request() -> SimulationRequest:
    _service, _operation, request = ready_request(LatheStrategyId.OD_ROUGH)
    return SimulationRequest(
        (generate(request),), request.stock,
        ToolEnvelope(0.4, 4.0, 0.0, 4.0, holder_radial_offset_mm=100.0),
        SimulationSettings(sampling_resolution_mm=5.0, maximum_stock_stations=100),
    )


def test_100_replays_are_byte_semantic_deterministic() -> None:
    service = LatheSimulationService()
    request = _request()
    baseline = service.run(request)
    for _index in range(99):
        replay = service.run(request)
        assert replay.fingerprint == baseline.fingerprint
        assert replay.final_stock == baseline.final_stock


def test_50_cancellation_reset_cycles_do_not_leak_threads() -> None:
    service = LatheSimulationService()
    request = _request()
    before = tuple(thread.ident for thread in threading.enumerate())
    token = SimulationCancellationToken()
    for _index in range(50):
        token.cancel()
        assert service.run(request, cancellation=token).state is SimulationState.CANCELLED
        token.reset()
    after = tuple(thread.ident for thread in threading.enumerate())
    assert after == before


def test_payload_and_callback_frequency_are_bounded() -> None:
    service = LatheSimulationService()
    request = _request()
    callbacks: list[float] = []
    result = service.run(
        replace(request, settings=replace(request.settings, progress_interval_frames=5)),
        progress=callbacks.append,
    )
    assert callbacks == sorted(set(callbacks))
    assert len(callbacks) <= len(result.frames)
    assert callbacks[-1] == 1.0
