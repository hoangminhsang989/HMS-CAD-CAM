"""Foreground-first resource policy shared by optional background work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BackgroundDecision(StrEnum):
    RUN = "run"
    THROTTLE = "throttle"
    SUSPEND = "suspend"


@dataclass(frozen=True, slots=True)
class ResourcePressure:
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    disk_busy_percent: float = 0.0


class ResourceGovernor:
    """Fail-safe governor. Foreground calculation always pauses background work."""

    def __init__(self, probe=None, *, throttle_cpu: float = 75.0, suspend_memory: float = 90.0) -> None:
        self._probe = probe or (lambda: ResourcePressure())
        self._throttle_cpu = throttle_cpu
        self._suspend_memory = suspend_memory

    def decide(self, *, foreground_active: bool) -> BackgroundDecision:
        if foreground_active:
            return BackgroundDecision.SUSPEND
        pressure = self._probe()
        if not isinstance(pressure, ResourcePressure):
            return BackgroundDecision.SUSPEND
        if pressure.memory_percent >= self._suspend_memory:
            return BackgroundDecision.SUSPEND
        if pressure.cpu_percent >= self._throttle_cpu or pressure.disk_busy_percent >= self._throttle_cpu:
            return BackgroundDecision.THROTTLE
        return BackgroundDecision.RUN
