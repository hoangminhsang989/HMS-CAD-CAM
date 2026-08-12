"""Optional spare-resource precompute for R241 height-field simulation.

The coordinator is deliberately imported only after an owner/user enables
background precompute.  It owns one daemon worker, yields cooperatively to
foreground leases, and publishes resumable immutable stock checkpoints under
``project/cache/simulation/precompute``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import struct
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from threading import Condition, Event, RLock, Thread
from time import monotonic, sleep, time
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.simulation.runtime import SimulationInputSnapshot

from .contracts import QualityMode
from .heightfield import HeightField3AxisEngine, MaterialRemovalError, RemainingStock

LOGGER = logging.getLogger(__name__)

PRECOMPUTE_FORMAT = "HMS_SIMULATION_PRECOMPUTE_CHECKPOINT"
PRECOMPUTE_VERSION = 1
PRECOMPUTE_ROOT = Path("cache") / "simulation" / "precompute"
_MANIFEST = "manifest.json"
_SCRATCH_SUFFIX = ".writing"


class ResourceDecision(StrEnum):
    RUN = "run"
    THROTTLE = "throttle"
    SUSPEND = "suspend"


class PrecomputeState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_FOREGROUND = "paused_foreground"
    PAUSED_PRESSURE = "paused_pressure"
    PARTIAL = "partial"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ResourcePressure:
    """Normalized optional resource readings used by the cooperative governor."""

    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_percent: float | None = None
    gpu_percent: float | None = None
    worker_available: bool = True

    def __post_init__(self) -> None:
        for value in (
            self.cpu_percent,
            self.memory_percent,
            self.disk_percent,
            self.gpu_percent,
        ):
            if value is not None and not 0.0 <= value <= 100.0:
                raise ValueError("Resource pressure percentage is invalid")
        if type(self.worker_available) is not bool:
            raise TypeError("worker_available must be bool")


class ResourceGovernor:
    """Map foreground ownership and resource pressure to one bounded worker."""

    def __init__(
        self,
        probe: Callable[[], ResourcePressure] | None = None,
        *,
        throttle_seconds: float = 0.02,
    ) -> None:
        if throttle_seconds < 0.0:
            raise ValueError("Governor throttle must be non-negative")
        self._probe = probe or (lambda: ResourcePressure())
        self.throttle_seconds = throttle_seconds

    def decide(self, *, foreground_active: bool) -> ResourceDecision:
        if foreground_active:
            return ResourceDecision.SUSPEND
        pressure = self._probe()
        values = tuple(
            value
            for value in (
                pressure.cpu_percent,
                pressure.memory_percent,
                pressure.disk_percent,
                pressure.gpu_percent,
            )
            if value is not None
        )
        if not pressure.worker_available or any(value >= 85.0 for value in values):
            return ResourceDecision.SUSPEND
        if any(value >= 70.0 for value in values):
            return ResourceDecision.THROTTLE
        return ResourceDecision.RUN


@dataclass(frozen=True, slots=True)
class PrecomputeStatus:
    operation_id: str
    state: PrecomputeState
    completed_chunks: int = 0
    next_sample: int = 0
    total_samples: int = 0
    resumed_from_sample: int = 0
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PrecomputedStockResult:
    """Complete material state reusable by the manually opened R241 UI."""

    remaining_stock: RemainingStock
    quality: QualityMode
    completed_chunks: int


@dataclass(frozen=True, slots=True)
class _QueuedJob:
    project_root: Path
    project_id: UUID
    project_generation: int
    operation_id: object
    load_inputs: Callable[[], SimulationInputSnapshot]
    quality: QualityMode


@dataclass(frozen=True, slots=True)
class _Checkpoint:
    run_fingerprint: str
    next_sample: int
    total_samples: int
    completed_chunks: int
    state: PrecomputeState
    stock: RemainingStock | None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _safe_component(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32]


class PrecomputeCheckpointStore:
    """Fail-closed checkpoint storage with quota/age/LRU cleanup."""

    def __init__(
        self,
        *,
        maximum_bytes: int = 256 * 1024 * 1024,
        maximum_runs: int = 64,
        maximum_age_seconds: float = 30.0 * 24.0 * 60.0 * 60.0,
        retained_checkpoints_per_run: int = 4,
    ) -> None:
        if maximum_bytes <= 0 or maximum_runs <= 0 or maximum_age_seconds <= 0.0:
            raise ValueError("Precompute cache bounds must be positive")
        if retained_checkpoints_per_run <= 0:
            raise ValueError("Checkpoint retention must be positive")
        self.maximum_bytes = maximum_bytes
        self.maximum_runs = maximum_runs
        self.maximum_age_seconds = maximum_age_seconds
        self.retained_checkpoints_per_run = retained_checkpoints_per_run
        self._active_runs: set[Path] = set()
        self._active_temporaries: set[Path] = set()
        self._lock = RLock()

    def run_root(
        self,
        project_root: Path,
        operation_id: object,
        run_fingerprint: str,
    ) -> Path:
        if len(run_fingerprint) != 64 or any(c not in "0123456789abcdef" for c in run_fingerprint):
            raise ValueError("Precompute run fingerprint is invalid")
        return (
            project_root
            / PRECOMPUTE_ROOT
            / _safe_component(operation_id)
            / run_fingerprint
        )

    @contextmanager
    def active(self, run_root: Path) -> Iterator[None]:
        with self._lock:
            self._active_runs.add(run_root)
        try:
            yield
        finally:
            with self._lock:
                self._active_runs.discard(run_root)

    def load(self, run_root: Path, run_fingerprint: str) -> _Checkpoint:
        manifest_path = run_root / _MANIFEST
        if not manifest_path.is_file() or self._is_link(manifest_path):
            return _Checkpoint(run_fingerprint, 0, 0, 0, PrecomputeState.QUEUED, None)
        try:
            raw = json.loads(manifest_path.read_text(encoding="ascii"))
            fields = {
                "format", "format_version", "run_fingerprint", "state",
                "next_sample", "total_samples", "completed_chunks",
                "stock_filename", "stock_sha256", "stock_bytes", "stock",
                "updated_at",
            }
            if not isinstance(raw, dict) or set(raw) != fields:
                raise ValueError("checkpoint manifest fields are invalid")
            if raw["format"] != PRECOMPUTE_FORMAT or raw["format_version"] != PRECOMPUTE_VERSION:
                raise ValueError("checkpoint version is unsupported")
            if raw["run_fingerprint"] != run_fingerprint:
                raise ValueError("checkpoint fingerprint is stale")
            state = PrecomputeState(raw["state"])
            next_sample = raw["next_sample"]
            total_samples = raw["total_samples"]
            completed_chunks = raw["completed_chunks"]
            if any(type(value) is not int or value < 0 for value in (next_sample, total_samples, completed_chunks)):
                raise ValueError("checkpoint counters are invalid")
            stock_name = raw["stock_filename"]
            stock_meta = raw["stock"]
            stock: RemainingStock | None = None
            if stock_name is not None:
                if not isinstance(stock_name, str) or Path(stock_name).name != stock_name:
                    raise ValueError("checkpoint stock filename is unsafe")
                stock_path = run_root / stock_name
                payload = stock_path.read_bytes()
                if (
                    self._is_link(stock_path)
                    or len(payload) != raw["stock_bytes"]
                    or hashlib.sha256(payload).hexdigest() != raw["stock_sha256"]
                ):
                    raise ValueError("checkpoint stock payload verification failed")
                stock = self._decode_stock(stock_meta, payload)
            return _Checkpoint(
                run_fingerprint,
                next_sample,
                total_samples,
                completed_chunks,
                state,
                stock,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            LOGGER.warning("Ignoring invalid Simulation precompute checkpoint at %s", run_root)
            return _Checkpoint(run_fingerprint, 0, 0, 0, PrecomputeState.QUEUED, None)

    def publish(
        self,
        run_root: Path,
        *,
        run_fingerprint: str,
        stock: RemainingStock,
        next_sample: int,
        total_samples: int,
        completed_chunks: int,
        complete: bool,
    ) -> _Checkpoint:
        if next_sample < 0 or total_samples < 0 or next_sample > total_samples:
            raise ValueError("Checkpoint sample range is invalid")
        self._ensure_run_root(run_root)
        stock_name = f"stock-{next_sample:08d}.bin"
        stock_path = run_root / stock_name
        payload = struct.pack(f"<{len(stock.top_heights)}d", *stock.top_heights)
        self._atomic_write(stock_path, payload)
        state = PrecomputeState.COMPLETE if complete else PrecomputeState.PARTIAL
        metadata = {
            "format": PRECOMPUTE_FORMAT,
            "format_version": PRECOMPUTE_VERSION,
            "run_fingerprint": run_fingerprint,
            "state": state.value,
            "next_sample": next_sample,
            "total_samples": total_samples,
            "completed_chunks": completed_chunks,
            "stock_filename": stock_name,
            "stock_sha256": hashlib.sha256(payload).hexdigest(),
            "stock_bytes": len(payload),
            "stock": self._stock_metadata(stock),
            "updated_at": time(),
        }
        self._atomic_write(run_root / _MANIFEST, _canonical_bytes(metadata))
        self._trim_run(run_root, keep_stock=stock_name)
        return _Checkpoint(
            run_fingerprint,
            next_sample,
            total_samples,
            completed_chunks,
            state,
            stock,
        )

    def cleanup(self, project_root: Path) -> tuple[Path, ...]:
        root = project_root / PRECOMPUTE_ROOT
        if not root.is_dir() or self._is_link(root):
            return ()
        removed: list[Path] = []
        with self._lock:
            active_runs = {path.resolve() for path in self._active_runs}
            active_temporaries = {path.resolve() for path in self._active_temporaries}
        run_entries: list[tuple[int, int, Path]] = []
        for operation_root in root.iterdir():
            if not operation_root.is_dir() or self._is_link(operation_root):
                continue
            for run_root in operation_root.iterdir():
                if not run_root.is_dir() or self._is_link(run_root):
                    continue
                resolved = run_root.resolve()
                if resolved in active_runs:
                    continue
                for temporary in run_root.glob(f"*{_SCRATCH_SUFFIX}"):
                    if (
                        temporary.is_file()
                        and not self._is_link(temporary)
                        and temporary.resolve() not in active_temporaries
                    ):
                        temporary.unlink(missing_ok=True)
                        removed.append(temporary)
                files = tuple(path for path in run_root.iterdir() if path.is_file() and not self._is_link(path))
                size = sum(path.stat().st_size for path in files)
                modified = max((path.stat().st_mtime_ns for path in files), default=0)
                run_entries.append((modified, size, run_root))
        now_ns = time() * 1_000_000_000
        keep: list[tuple[int, int, Path]] = []
        for modified, size, run_root in sorted(run_entries, reverse=True):
            too_old = modified and (now_ns - modified) / 1_000_000_000 > self.maximum_age_seconds
            if too_old:
                removed.extend(self._remove_run(run_root))
            else:
                keep.append((modified, size, run_root))
        total = 0
        for index, (_modified, size, run_root) in enumerate(keep):
            if index >= self.maximum_runs or total + size > self.maximum_bytes:
                removed.extend(self._remove_run(run_root))
            else:
                total += size
        return tuple(removed)

    def cleanup_scratch(self, project_root: Path) -> tuple[Path, ...]:
        """Remove only inactive precompute-owned temporary files."""
        root = project_root / PRECOMPUTE_ROOT
        if not root.is_dir() or self._is_link(root):
            return ()
        with self._lock:
            active = {path.resolve() for path in self._active_temporaries}
        removed: list[Path] = []
        for path in root.glob(f"*/*/*{_SCRATCH_SUFFIX}"):
            if path.is_file() and not self._is_link(path) and path.resolve() not in active:
                path.unlink(missing_ok=True)
                removed.append(path)
        return tuple(removed)

    def _ensure_run_root(self, run_root: Path) -> None:
        project_root = run_root.parents[4]
        if not project_root.is_dir() or self._is_link(project_root):
            raise OSError("Project root is unsafe")
        current = project_root
        for part in PRECOMPUTE_ROOT.parts:
            current = current / part
            current.mkdir(exist_ok=True)
            if self._is_link(current) or not current.is_dir():
                raise OSError("Precompute cache root is unsafe")
        operation_root = run_root.parent
        operation_root.mkdir(exist_ok=True)
        run_root.mkdir(exist_ok=True)
        if self._is_link(operation_root) or self._is_link(run_root):
            raise OSError("Precompute cache directory is unsafe")

    def _atomic_write(self, target: Path, payload: bytes) -> None:
        temporary = target.parent / f".{target.name}.{uuid4().hex}{_SCRATCH_SUFFIX}"
        with self._lock:
            self._active_temporaries.add(temporary)
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
            with self._lock:
                self._active_temporaries.discard(temporary)

    def _trim_run(self, run_root: Path, *, keep_stock: str) -> None:
        stock_files = sorted(
            (
                path
                for path in run_root.glob("stock-*.bin")
                if path.is_file() and not self._is_link(path)
            ),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
        retained = {path.name for path in stock_files[: self.retained_checkpoints_per_run]}
        retained.add(keep_stock)
        for path in stock_files:
            if path.name not in retained:
                path.unlink(missing_ok=True)

    @staticmethod
    def _stock_metadata(stock: RemainingStock) -> dict[str, Any]:
        return {
            "width": stock.width,
            "height": stock.height,
            "cell_size_x": stock.cell_size_x,
            "cell_size_y": stock.cell_size_y,
            "initial_volume": stock.initial_volume,
            "remaining_volume": stock.remaining_volume,
            "removed_volume": stock.removed_volume,
            "minimum_height": stock.minimum_height,
            "maximum_height": stock.maximum_height,
            "unit": stock.unit.value,
        }

    @staticmethod
    def _decode_stock(metadata: object, payload: bytes) -> RemainingStock:
        from hms_cadcam.cam.domain.units import LengthUnit

        if not isinstance(metadata, dict):
            raise ValueError("checkpoint stock metadata is invalid")
        fields = {
            "width", "height", "cell_size_x", "cell_size_y", "initial_volume",
            "remaining_volume", "removed_volume", "minimum_height",
            "maximum_height", "unit",
        }
        if set(metadata) != fields:
            raise ValueError("checkpoint stock metadata fields are invalid")
        count = metadata["width"] * metadata["height"]
        if type(count) is not int or count <= 0 or len(payload) != count * 8:
            raise ValueError("checkpoint stock payload length is invalid")
        heights = struct.unpack(f"<{count}d", payload)
        return RemainingStock(
            metadata["width"],
            metadata["height"],
            metadata["cell_size_x"],
            metadata["cell_size_y"],
            heights,
            metadata["initial_volume"],
            metadata["remaining_volume"],
            metadata["removed_volume"],
            metadata["minimum_height"],
            metadata["maximum_height"],
            LengthUnit(metadata["unit"]),
        )

    @staticmethod
    def _remove_run(run_root: Path) -> tuple[Path, ...]:
        removed = tuple(path for path in run_root.rglob("*") if path.is_file())
        shutil.rmtree(run_root)
        try:
            run_root.parent.rmdir()
        except OSError:
            pass
        return removed

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )


class BackgroundSimulationCoordinator:
    """Single-worker, latest-per-operation, cooperatively governed precompute."""

    def __init__(
        self,
        *,
        governor: ResourceGovernor | None = None,
        store: PrecomputeCheckpointStore | None = None,
        chunk_samples: int = 128,
        pressure_poll_seconds: float = 0.02,
    ) -> None:
        if chunk_samples <= 0 or pressure_poll_seconds <= 0.0:
            raise ValueError("Background precompute bounds must be positive")
        self._governor = governor or ResourceGovernor()
        self._store = store or PrecomputeCheckpointStore()
        self._chunk_samples = chunk_samples
        self._pressure_poll_seconds = pressure_poll_seconds
        self._condition = Condition(RLock())
        self._foreground = 0
        self._jobs: OrderedDict[tuple[str, str], _QueuedJob] = OrderedDict()
        self._cancelled_projects: set[tuple[str, int]] = set()
        self._statuses: dict[str, PrecomputeStatus] = {}
        self._shutdown = False
        self._active = False
        self._idle = Event()
        self._idle.set()
        self._thread = Thread(
            target=self._worker_loop,
            name="HMS-Simulation-Precompute",
            daemon=True,
        )
        self._thread.start()

    @contextmanager
    def foreground(self, _name: str) -> Iterator[None]:
        self.begin_foreground(_name)
        try:
            yield
        finally:
            self.end_foreground(_name)

    def begin_foreground(self, _name: str) -> None:
        """Acquire one non-blocking foreground claim."""
        with self._condition:
            self._foreground += 1
            self._condition.notify_all()

    def end_foreground(self, _name: str) -> None:
        """Release one foreground claim and wake suspended precompute."""
        with self._condition:
            self._foreground = max(0, self._foreground - 1)
            self._condition.notify_all()

    def schedule(
        self,
        *,
        project_root: Path,
        project_id: UUID,
        project_generation: int,
        operation_id: object,
        load_inputs: Callable[[], SimulationInputSnapshot],
        quality: QualityMode = QualityMode.FAST,
    ) -> None:
        job = _QueuedJob(
            project_root,
            project_id,
            project_generation,
            operation_id,
            load_inputs,
            quality,
        )
        key = (str(project_root.resolve()), str(operation_id))
        with self._condition:
            self._cancelled_projects.discard((key[0], project_generation))
            self._jobs.pop(key, None)
            self._jobs[key] = job
            self._statuses[str(operation_id)] = PrecomputeStatus(
                str(operation_id), PrecomputeState.QUEUED
            )
            self._idle.clear()
            self._condition.notify_all()

    def cancel_project(self, project_root: Path, project_generation: int) -> None:
        root = str(project_root.resolve())
        with self._condition:
            self._cancelled_projects.add((root, project_generation))
            for key in tuple(self._jobs):
                if key[0] == root:
                    job = self._jobs.pop(key)
                    self._statuses[str(job.operation_id)] = PrecomputeStatus(
                        str(job.operation_id), PrecomputeState.CANCELLED
                    )
            self._condition.notify_all()
        self._store.cleanup_scratch(project_root)

    def status(self, operation_id: object) -> PrecomputeStatus | None:
        with self._condition:
            return self._statuses.get(str(operation_id))

    def wait_idle(self, timeout: float = 5.0) -> bool:
        return self._idle.wait(timeout)

    def load_completed(
        self,
        *,
        project_root: Path,
        project_id: UUID,
        project_generation: int,
        operation_id: object,
        inputs: SimulationInputSnapshot,
        quality: QualityMode = QualityMode.FAST,
    ) -> PrecomputedStockResult | None:
        """Return only a verified complete checkpoint; partial stays internal."""
        job = _QueuedJob(
            project_root,
            project_id,
            project_generation,
            operation_id,
            lambda: inputs,
            quality,
        )
        fingerprint = self._run_fingerprint(job, inputs)
        checkpoint = self._store.load(
            self._store.run_root(project_root, operation_id, fingerprint),
            fingerprint,
        )
        if checkpoint.state is not PrecomputeState.COMPLETE or checkpoint.stock is None:
            return None
        return PrecomputedStockResult(
            checkpoint.stock, quality, checkpoint.completed_chunks
        )

    def shutdown(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        with self._condition:
            self._shutdown = True
            self._jobs.clear()
            self._condition.notify_all()
        if wait:
            self._thread.join(timeout)

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._shutdown and not self._jobs:
                    self._active = False
                    self._idle.set()
                    self._condition.wait()
                if self._shutdown:
                    self._active = False
                    self._idle.set()
                    return
                _key, job = self._jobs.popitem(last=False)
                self._active = True
            try:
                self._run_job(job)
            except MaterialRemovalError as error:
                if self._cancelled(job):
                    self._set_status(job, PrecomputeState.CANCELLED)
                else:
                    LOGGER.warning("Background Simulation precompute failed", exc_info=True)
                    self._set_status(job, PrecomputeState.FAILED, message=str(error))
            except Exception as error:  # background boundary: normal CAM stays available
                LOGGER.warning("Background Simulation precompute failed", exc_info=True)
                self._set_status(job, PrecomputeState.FAILED, message=str(error))
            finally:
                with self._condition:
                    if not self._jobs:
                        self._active = False
                        self._idle.set()

    def _run_job(self, job: _QueuedJob) -> None:
        self._store.cleanup_scratch(job.project_root)
        if self._cancelled(job):
            self._set_status(job, PrecomputeState.CANCELLED)
            return
        self._wait_for_resources(job)
        if self._cancelled(job):
            self._set_status(job, PrecomputeState.CANCELLED)
            return
        inputs = job.load_inputs()
        fingerprint = self._run_fingerprint(job, inputs)
        run_root = self._store.run_root(
            job.project_root, job.operation_id, fingerprint
        )
        with self._store.active(run_root):
            checkpoint = self._store.load(run_root, fingerprint)
            resumed = checkpoint.next_sample
            if checkpoint.state is PrecomputeState.COMPLETE:
                self._set_status(
                    job,
                    PrecomputeState.COMPLETE,
                    checkpoint=checkpoint,
                    resumed=resumed,
                )
                return
            current = checkpoint.stock
            next_sample = checkpoint.next_sample
            chunks = checkpoint.completed_chunks
            total = checkpoint.total_samples
            while True:
                self._wait_for_resources(job)
                after_wait = self.status(job.operation_id)
                if (
                    after_wait is not None
                    and after_wait.state
                    in {
                        PrecomputeState.PAUSED_FOREGROUND,
                        PrecomputeState.PAUSED_PRESSURE,
                    }
                ):
                    resumed = max(resumed, checkpoint.next_sample)
                if self._cancelled(job):
                    self._set_status(
                        job,
                        PrecomputeState.CANCELLED,
                        checkpoint=checkpoint,
                        resumed=resumed,
                    )
                    return
                self._set_status(
                    job,
                    PrecomputeState.RUNNING,
                    checkpoint=checkpoint,
                    resumed=resumed,
                )
                try:
                    chunk = HeightField3AxisEngine().simulate_chunk(
                        stock=inputs.setup.stock,
                        artifact=inputs.artifact,
                        tool=inputs.tool,
                        quality=job.quality,
                        initial_stock=current,
                        start_cutting_sample=next_sample,
                        maximum_cutting_samples=self._chunk_samples,
                        cancellation=lambda: (
                            self._cancelled(job) or self._should_pause_now()
                        ),
                    )
                except MaterialRemovalError:
                    if self._cancelled(job):
                        self._set_status(
                            job,
                            PrecomputeState.CANCELLED,
                            checkpoint=checkpoint,
                            resumed=resumed,
                        )
                        return
                    # No checkpoint was published for this interrupted chunk;
                    # wait cooperatively and restart it from the prior stock.
                    self._wait_for_resources(job)
                    resumed = max(resumed, checkpoint.next_sample)
                    continue
                current = chunk.result.remaining_stock
                next_sample = chunk.next_cutting_sample
                total = chunk.total_cutting_samples
                chunks += 1
                checkpoint = self._store.publish(
                    run_root,
                    run_fingerprint=fingerprint,
                    stock=current,
                    next_sample=next_sample,
                    total_samples=total,
                    completed_chunks=chunks,
                    complete=chunk.complete,
                )
                self._store.cleanup(job.project_root)
                self._set_status(
                    job,
                    checkpoint.state,
                    checkpoint=checkpoint,
                    resumed=resumed,
                )
                if chunk.complete:
                    return

    def _wait_for_resources(self, job: _QueuedJob) -> None:
        while True:
            if self._cancelled(job):
                return
            with self._condition:
                foreground = self._foreground > 0
            decision = self._governor.decide(foreground_active=foreground)
            if decision is ResourceDecision.RUN:
                return
            state = (
                PrecomputeState.PAUSED_FOREGROUND
                if foreground
                else PrecomputeState.PAUSED_PRESSURE
            )
            self._set_status(job, state)
            if decision is ResourceDecision.THROTTLE:
                sleep(self._governor.throttle_seconds)
                return
            with self._condition:
                self._condition.wait(self._pressure_poll_seconds)

    def _cancelled(self, job: _QueuedJob) -> bool:
        key = (str(job.project_root.resolve()), job.project_generation)
        with self._condition:
            return self._shutdown or key in self._cancelled_projects

    def _should_pause_now(self) -> bool:
        with self._condition:
            foreground = self._foreground > 0
        return (
            self._governor.decide(foreground_active=foreground)
            is ResourceDecision.SUSPEND
        )

    def _set_status(
        self,
        job: _QueuedJob,
        state: PrecomputeState,
        *,
        checkpoint: _Checkpoint | None = None,
        resumed: int = 0,
        message: str | None = None,
    ) -> None:
        if checkpoint is None:
            with self._condition:
                previous = self._statuses.get(str(job.operation_id))
            value = _Checkpoint(
                "",
                0 if previous is None else previous.next_sample,
                0 if previous is None else previous.total_samples,
                0 if previous is None else previous.completed_chunks,
                state,
                None,
            )
            if previous is not None and resumed == 0:
                resumed = previous.resumed_from_sample
        else:
            value = checkpoint
        status = PrecomputeStatus(
            str(job.operation_id),
            state,
            value.completed_chunks,
            value.next_sample,
            value.total_samples,
            resumed,
            message,
        )
        with self._condition:
            self._statuses[str(job.operation_id)] = status
            self._condition.notify_all()

    @staticmethod
    def _run_fingerprint(
        job: _QueuedJob,
        inputs: SimulationInputSnapshot,
    ) -> str:
        return ContentFingerprint.from_payload(
            {
                "project_id": str(job.project_id),
                "operation_id": str(job.operation_id),
                "artifact": inputs.artifact.artifact_fingerprint.to_dict(),
                "tool": inputs.tool.content_fingerprint.to_dict(),
                "holder": (
                    None
                    if inputs.holder is None
                    else inputs.holder.content_fingerprint.to_dict()
                ),
                "stock": inputs.request.stock_fingerprint.to_dict(),
                "quality": job.quality.value,
                "engine": "heightfield-r242.1",
            }
        ).digest


__all__ = [
    "BackgroundSimulationCoordinator",
    "PrecomputeCheckpointStore",
    "PrecomputedStockResult",
    "PrecomputeState",
    "PrecomputeStatus",
    "ResourceDecision",
    "ResourceGovernor",
    "ResourcePressure",
]
