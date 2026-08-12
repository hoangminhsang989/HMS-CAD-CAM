"""Incremental multi-operation runner for R241 3-axis simulation."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Callable

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.simulation.runtime import SimulationInputSnapshot

from .cache import BoundedSimulationCache, CacheKey
from .contracts import OperationCoverage, QualityMode, StageTiming
from .heightfield import HeightField3AxisEngine, HeightFieldResult, RemainingStock


@dataclass(frozen=True, slots=True)
class JobSimulationResult:
    operation_results: tuple[tuple[str, HeightFieldResult], ...]
    remaining_stock: RemainingStock
    coverage: OperationCoverage
    timings: tuple[StageTiming, ...]


class IncrementalJobSimulator:
    """Sequential material state with deterministic per-operation cache reuse."""

    def __init__(
        self,
        cache: BoundedSimulationCache[HeightFieldResult] | None = None,
    ) -> None:
        self._cache = cache or BoundedSimulationCache(
            maximum_entries=24,
            maximum_bytes=768 * 1024 * 1024,
        )
        self.material_computations = 0

    def run(
        self,
        inputs: tuple[SimulationInputSnapshot, ...],
        *,
        quality: QualityMode,
        coverage: OperationCoverage,
        cancellation: Callable[[], bool] | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> JobSimulationResult:
        if not inputs:
            raise ValueError("Simulation job has no operation inputs")
        unit = inputs[0].artifact.unit
        stock = inputs[0].setup.stock
        if any(value.artifact.unit is not unit or value.setup.stock != stock for value in inputs):
            raise ValueError("Simulation job operations must share one setup stock and unit")
        current: RemainingStock | None = None
        operation_results: list[tuple[str, HeightFieldResult]] = []
        timings: list[StageTiming] = []
        total = len(inputs)
        for index, value in enumerate(inputs):
            if cancellation is not None and cancellation():
                raise RuntimeError("Simulation job cancelled")
            prior = "initial" if current is None else ContentFingerprint.from_payload({
                "heights": list(current.top_heights),
                "removed": current.removed_volume,
            }).digest
            fingerprint = ContentFingerprint.from_payload({
                "artifact": value.artifact.artifact_fingerprint.to_dict(),
                "tool": value.tool.content_fingerprint.to_dict(),
                "holder": None if value.holder is None else value.holder.content_fingerprint.to_dict(),
                "stock": value.request.stock_fingerprint.to_dict(),
                "quality": quality.value,
                "prior": prior,
                "engine": "heightfield-r241.1",
            })
            key = CacheKey("operation_stock", fingerprint)
            started = perf_counter()
            result = self._cache.get(key)
            cache_hit = result is not None
            if result is None:
                self.material_computations += 1
                result = HeightField3AxisEngine().simulate(
                    stock=stock,
                    artifact=value.artifact,
                    tool=value.tool,
                    quality=quality,
                    initial_stock=current,
                    cancellation=cancellation,
                )
                byte_count = len(result.remaining_stock.top_heights) * 8
                self._cache.put(key, result, byte_count=byte_count)
            duration = perf_counter() - started
            operation_id = str(value.operation.operation_id)
            operation_results.append((operation_id, result))
            timings.append(StageTiming(f"material_removal:{operation_id}", duration, cache_hit))
            current = result.remaining_stock
            if progress is not None:
                progress(operation_id, index + 1, total)
        assert current is not None
        return JobSimulationResult(
            tuple(operation_results),
            current,
            coverage,
            tuple(timings),
        )
