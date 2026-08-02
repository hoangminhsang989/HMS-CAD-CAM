"""Application service over the pure deterministic Lathe simulation engine."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Callable, Iterator

from hms_cadcam.cam.lathe.simulation.engine import run_engine
from hms_cadcam.cam.lathe.simulation.models import AxisymmetricStock, SimulationFrame, SimulationPlan, SimulationResult, SimulationSettings, ToolEnvelope
from hms_cadcam.cam.lathe.simulation.planner import build_simulation_plan
from hms_cadcam.cam.lathe.simulation.stock import cylindrical_stock
from hms_cadcam.cam.lathe.toolpath.model import LatheToolpathResult
from hms_cadcam.cam.lathe.toolpath.stock import LatheStockSnapshotV1


class SimulationCancellationToken:
    """Small thread-safe cancellation token with no Qt dependency."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def reset(self) -> None:
        self._event.clear()


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    toolpaths: tuple[LatheToolpathResult, ...]
    stock: LatheStockSnapshotV1
    tool: ToolEnvelope
    settings: SimulationSettings = SimulationSettings()


class LatheSimulationService:
    """Validate, plan, run, replay and decimate without mutating source paths."""

    def build_plan(self, request: SimulationRequest) -> SimulationPlan:
        if not isinstance(request, SimulationRequest):
            raise TypeError("Simulation request is invalid")
        return build_simulation_plan(request.toolpaths)

    def run(
        self,
        request: SimulationRequest,
        *,
        cancellation: SimulationCancellationToken | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> SimulationResult:
        plan = self.build_plan(request)
        station_count = min(
            request.settings.maximum_stock_stations,
            max(2, int(request.stock.axial_length_mm / request.settings.sampling_resolution_mm) + 1),
        )
        initial = cylindrical_stock(request.stock, station_count=station_count)
        token = cancellation or SimulationCancellationToken()
        return run_engine(plan, initial, request.tool, request.settings, cancelled=lambda: token.cancelled, progress=progress)

    @staticmethod
    def iter_frames(result: SimulationResult) -> Iterator[SimulationFrame]:
        if not isinstance(result, SimulationResult):
            raise TypeError("Simulation result is invalid")
        return iter(result.frames)

    @staticmethod
    def display_frames(result: SimulationResult, decimation: int) -> tuple[SimulationFrame, ...]:
        if type(decimation) is not int or decimation < 1:
            raise ValueError("Display decimation must be positive")
        if not result.frames:
            return ()
        selected = result.frames[::decimation]
        return selected if selected[-1] is result.frames[-1] else (*selected, result.frames[-1])


__all__ = ["LatheSimulationService", "SimulationCancellationToken", "SimulationRequest"]
