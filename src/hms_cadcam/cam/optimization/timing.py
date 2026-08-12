"""Low-overhead monotonic phase timing for cold, warm and incremental runs."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns
from typing import Iterator
from contextlib import contextmanager


@dataclass(frozen=True, slots=True)
class PhaseTiming:
    phase: str
    elapsed_ns: int
    cache_status: str | None = None

    @property
    def elapsed_seconds(self) -> float:
        return self.elapsed_ns / 1_000_000_000.0


@dataclass(frozen=True, slots=True)
class CalculationTiming:
    operation_id: str
    phases: tuple[PhaseTiming, ...]

    @property
    def total_ns(self) -> int:
        return sum(item.elapsed_ns for item in self.phases)

    @property
    def total_seconds(self) -> float:
        return self.total_ns / 1_000_000_000.0


class TimingRecorder:
    """Recorder with no wall-clock identity and no logging overhead by default."""

    def __init__(self, operation_id: str) -> None:
        self._operation_id = operation_id
        self._phases: list[PhaseTiming] = []

    @contextmanager
    def phase(self, name: str, *, cache_status: str | None = None) -> Iterator[None]:
        started = monotonic_ns()
        try:
            yield
        finally:
            self._phases.append(PhaseTiming(name, monotonic_ns() - started, cache_status))

    def record(self, name: str, elapsed_ns: int, *, cache_status: str | None = None) -> None:
        if type(elapsed_ns) is not int or elapsed_ns < 0:
            raise ValueError("Phase elapsed time must be a non-negative integer")
        self._phases.append(PhaseTiming(name, elapsed_ns, cache_status))

    def snapshot(self) -> CalculationTiming:
        return CalculationTiming(self._operation_id, tuple(self._phases))
