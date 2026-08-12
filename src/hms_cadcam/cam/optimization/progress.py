"""Typed, low-frequency progress events for production CAM calculations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CamPhaseState(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class CamCalculationProgress:
    """One coalesced phase boundary; never one event per geometric point."""

    operation_id: str
    strategy: str
    phase: str
    state: CamPhaseState
    percentage: float
    elapsed_ns: int = 0
    cache_status: str | None = None

    def __post_init__(self) -> None:
        if not self.operation_id or not self.strategy or not self.phase:
            raise ValueError("CAM calculation progress identity is invalid")
        if not isinstance(self.state, CamPhaseState):
            raise TypeError("CAM calculation progress state is invalid")
        if not 0.0 <= self.percentage <= 100.0:
            raise ValueError("CAM calculation progress percentage is invalid")
        if type(self.elapsed_ns) is not int or self.elapsed_ns < 0:
            raise ValueError("CAM calculation progress elapsed time is invalid")
