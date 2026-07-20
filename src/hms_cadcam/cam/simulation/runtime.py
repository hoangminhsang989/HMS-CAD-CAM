"""Project-scoped Simulation 7C.3 run state, progress, and cancellation.

This module is deliberately Qt-, viewer-, cache-, and OCP-free. Native collision
backends may be supplied by an owner-thread adapter, but are never retained in
the public run record or passed through progress callbacks.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from threading import Event, RLock
from time import monotonic
from typing import Callable, Hashable

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.ids import OperationId, SimulationRequestId, SimulationResultId
from hms_cadcam.cam.domain.machine import MachineDefinition
from hms_cadcam.cam.domain.operation import Operation
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.setup import Setup
from hms_cadcam.cam.domain.tooling import HolderDefinition, ToolAssembly, ToolDefinition
from hms_cadcam.cam.toolpath.model import ToolpathArtifact

from .collision import CollisionBackend, CollisionScene, run_collision_analysis
from .envelope import UnsupportedToolGeometryError, build_tool_envelope
from .model import SimulationIssueCode, SimulationRequest, SimulationResult
from .sampling import SimulationSamplingError, sample_toolpath
from .service import (
    SimulationComputationToken,
    SimulationExecution,
    SimulationPreflightError,
    SimulationRuntimeService,
)


class SimulationRunState(StrEnum):
    IDLE = "idle"
    VALIDATING = "validating"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class SimulationProgressPhase(StrEnum):
    VALIDATING = "validating"
    RESOLVING = "resolving"
    SAMPLING = "sampling"
    BROAD_PHASE = "broad_phase"
    NARROW_PHASE = "narrow_phase"
    BUILDING_RESULT = "building_result"
    PUBLISHING = "publishing"
    RENDERING_OVERLAY = "rendering_overlay"


@dataclass(frozen=True, slots=True)
class SimulationProgress:
    request_id: SimulationRequestId
    phase: SimulationProgressPhase
    processed: int
    total: int | None
    issue_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, SimulationRequestId):
            raise CamValidationError("Simulation progress request ID is invalid")
        if not isinstance(self.phase, SimulationProgressPhase):
            raise CamValidationError("Simulation progress phase is invalid")
        if type(self.processed) is not int or self.processed < 0:
            raise CamValidationError("Simulation progress processed count is invalid")
        if self.total is not None and (
            type(self.total) is not int
            or self.total < 0
            or self.processed > self.total
        ):
            raise CamValidationError("Simulation progress total is invalid")
        if type(self.issue_count) is not int or self.issue_count < 0:
            raise CamValidationError("Simulation progress issue count is invalid")

    @property
    def percentage(self) -> float | None:
        if self.total is None:
            return None
        if self.total == 0:
            return 100.0
        return self.processed * 100.0 / self.total


@dataclass(frozen=True, slots=True)
class SimulationRunRecord:
    request_id: SimulationRequestId
    project_generation: int
    operation_id: OperationId
    operation_revision: Revision
    artifact_fingerprint: ContentFingerprint
    input_fingerprint: DependencyFingerprint
    state: SimulationRunState
    progress: SimulationProgress | None
    started_at: datetime | None
    completed_at: datetime | None
    result_id: SimulationResultId | None = None
    diagnostic_code: SimulationIssueCode | None = None
    diagnostic_message: str | None = None


class SimulationCancellationToken:
    """Thread-safe cooperative cancellation flag excluded from persistence."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


@dataclass(frozen=True, slots=True)
class SimulationInputSnapshot:
    """Immutable native-free inputs captured before execution begins."""

    operation: Operation
    artifact: ToolpathArtifact
    setup: Setup
    tool: ToolDefinition
    assembly: ToolAssembly
    holder: HolderDefinition | None
    machine: MachineDefinition | None
    request: SimulationRequest

    def __post_init__(self) -> None:
        if self.request.operation_id != self.operation.operation_id:
            raise CamValidationError("Simulation snapshot operation mismatch")
        if self.request.artifact_id != self.artifact.artifact_id:
            raise CamValidationError("Simulation snapshot artifact mismatch")


@dataclass(frozen=True, slots=True)
class SimulationRunHandle:
    request: SimulationRequest
    project_generation: int
    computation_token: SimulationComputationToken
    cancellation: SimulationCancellationToken


ProgressCallback = Callable[[SimulationProgress], None]
StateCallback = Callable[[SimulationRunRecord], None]
CurrentRequest = Callable[[], SimulationRequest]


class _ProgressEmitter:
    def __init__(
        self,
        controller: "SimulationRunController",
        handle: SimulationRunHandle,
        callback: ProgressCallback | None,
        *,
        minimum_interval: float,
        clock: Callable[[], float],
    ) -> None:
        self._controller = controller
        self._handle = handle
        self._callback = callback
        self._minimum_interval = minimum_interval
        self._clock = clock
        self._last_time = float("-inf")
        self._last_phase: SimulationProgressPhase | None = None

    def emit(
        self,
        phase: SimulationProgressPhase,
        processed: int,
        total: int | None,
        issue_count: int = 0,
        *,
        force: bool = False,
    ) -> None:
        now = self._clock()
        phase_changed = phase is not self._last_phase
        completed = total is not None and processed == total
        if not (force or phase_changed or completed or now - self._last_time >= self._minimum_interval):
            return
        progress = SimulationProgress(
            self._handle.request.request_id,
            phase,
            processed,
            total,
            issue_count,
        )
        if not self._controller._accept_progress(self._handle, progress):
            return
        self._last_time = now
        self._last_phase = phase
        if self._callback is not None:
            self._callback(progress)


class SimulationRunController:
    """Latest-wins runtime controller; it performs no persistence itself."""

    def __init__(
        self,
        runtime: SimulationRuntimeService | None = None,
        *,
        progress_interval_seconds: float = 0.05,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if progress_interval_seconds < 0.0:
            raise ValueError("Simulation progress interval cannot be negative")
        self._runtime = runtime or SimulationRuntimeService()
        self._lock = RLock()
        self._project_id: Hashable | None = None
        self._project_generation: int | None = None
        self._records: dict[OperationId, SimulationRunRecord] = {}
        self._handles: dict[OperationId, SimulationRunHandle] = {}
        self._progress_interval = progress_interval_seconds
        self._clock = clock

    @property
    def runtime(self) -> SimulationRuntimeService:
        return self._runtime

    def bind_project(self, project_id: Hashable | None, generation: int | None) -> None:
        if (project_id is None) != (generation is None):
            raise CamValidationError("Simulation run project binding must be paired")
        if generation is not None and (type(generation) is not int or generation <= 0):
            raise CamValidationError("Simulation run project generation is invalid")
        self.cancel_all(stale=True)
        with self._lock:
            self._project_id = project_id
            self._project_generation = generation
            self._records.clear()
            self._handles.clear()
        if project_id is None:
            self._runtime.clear()
        else:
            self._runtime.bind_project(project_id, generation)

    def start(
        self,
        request: SimulationRequest,
        *,
        state_callback: StateCallback | None = None,
    ) -> SimulationRunHandle:
        with self._lock:
            if self._project_id is None or self._project_generation is None:
                raise SimulationPreflightError(
                    SimulationIssueCode.INVALID_REQUEST,
                    "No active project is bound for simulation",
                )
            active = self._handles.get(request.operation_id)
            active_record = self._records.get(request.operation_id)
            if (
                active is not None
                and active_record is not None
                and active_record.state
                in {
                    SimulationRunState.VALIDATING,
                    SimulationRunState.RUNNING,
                    SimulationRunState.CANCELLING,
                }
            ):
                raise SimulationPreflightError(
                    SimulationIssueCode.INVALID_REQUEST,
                    "A simulation run is already active for this operation",
                )
            token = self._runtime.begin(request)
            handle = SimulationRunHandle(
                request,
                self._project_generation,
                token,
                SimulationCancellationToken(),
            )
            record = SimulationRunRecord(
                request_id=request.request_id,
                project_generation=self._project_generation,
                operation_id=request.operation_id,
                operation_revision=request.operation_revision,
                artifact_fingerprint=request.artifact_fingerprint,
                input_fingerprint=request.input_fingerprint,
                state=SimulationRunState.VALIDATING,
                progress=None,
                started_at=datetime.now(timezone.utc),
                completed_at=None,
            )
            self._handles[request.operation_id] = handle
            self._records[request.operation_id] = record
        if state_callback is not None:
            state_callback(record)
        return handle

    def execute(
        self,
        handle: SimulationRunHandle,
        *,
        snapshot: SimulationInputSnapshot,
        scene: CollisionScene,
        backend: CollisionBackend,
        current_request: CurrentRequest | None = None,
        progress_callback: ProgressCallback | None = None,
        state_callback: StateCallback | None = None,
    ) -> SimulationExecution:
        emitter = _ProgressEmitter(
            self,
            handle,
            progress_callback,
            minimum_interval=self._progress_interval,
            clock=self._clock,
        )
        try:
            emitter.emit(SimulationProgressPhase.VALIDATING, 0, 1, force=True)
            self._require_current_handle(handle)
            if snapshot.request.identity_payload() != handle.request.identity_payload():
                raise SimulationPreflightError(
                    SimulationIssueCode.STALE_RESULT,
                    "Simulation snapshot differs from the active request",
                )
            if (
                snapshot.artifact.artifact_id != handle.request.artifact_id
                or snapshot.artifact.artifact_fingerprint
                != handle.request.artifact_fingerprint
            ):
                raise SimulationPreflightError(
                    SimulationIssueCode.SOURCE_STALE,
                    "Toolpath differs from the captured simulation request",
                )
            emitter.emit(SimulationProgressPhase.VALIDATING, 1, 1, force=True)
            self._set_state(handle, SimulationRunState.RUNNING, state_callback)
            emitter.emit(SimulationProgressPhase.RESOLVING, 0, 1, force=True)
            envelope = build_tool_envelope(
                tool=snapshot.tool,
                assembly=snapshot.assembly,
                holder=snapshot.holder,
            )
            emitter.emit(SimulationProgressPhase.RESOLVING, 1, 1, force=True)
            sampling = sample_toolpath(
                artifact=snapshot.artifact,
                wcs=snapshot.setup.wcs,
                policy=handle.request.sampling_policy,
                cancellation=lambda: handle.cancellation.cancelled,
                progress=lambda processed, total: emitter.emit(
                    SimulationProgressPhase.SAMPLING,
                    processed,
                    total,
                ),
            )
            candidate = run_collision_analysis(
                request=handle.request,
                artifact=snapshot.artifact,
                sampling=sampling,
                envelope=envelope,
                scene=scene,
                backend=backend,
                result_id=SimulationResultId.new(),
                cancellation=lambda: handle.cancellation.cancelled,
                broad_progress=lambda processed, total, issues: emitter.emit(
                    SimulationProgressPhase.BROAD_PHASE,
                    processed,
                    total,
                    issues,
                ),
                narrow_progress=lambda processed, total, issues: emitter.emit(
                    SimulationProgressPhase.NARROW_PHASE,
                    processed,
                    total,
                    issues,
                ),
            )
            emitter.emit(
                SimulationProgressPhase.BUILDING_RESULT,
                1,
                1,
                len(candidate.issues),
                force=True,
            )
            if handle.cancellation.cancelled:
                raise SimulationSamplingError(
                    SimulationIssueCode.CANCELLED,
                    "Simulation cancelled before publish",
                )
            live_request = current_request() if current_request is not None else handle.request
            emitter.emit(
                SimulationProgressPhase.PUBLISHING,
                0,
                1,
                len(candidate.issues),
                force=True,
            )
            execution = self._runtime.publish(
                request=handle.request,
                token=handle.computation_token,
                candidate=candidate,
                current_request=live_request,
            )
            if not execution.accepted:
                self._finish_rejected(handle, execution, state_callback)
                return execution
            emitter.emit(
                SimulationProgressPhase.PUBLISHING,
                1,
                1,
                len(candidate.issues),
                force=True,
            )
            self._finish_completed(handle, candidate, state_callback)
            return execution
        except SimulationSamplingError as error:
            execution = SimulationExecution(False, None, error.code, str(error))
        except UnsupportedToolGeometryError as error:
            execution = SimulationExecution(False, None, error.code, str(error))
        except SimulationPreflightError as error:
            execution = SimulationExecution(False, None, error.code, str(error))
        except (CamValidationError, ValueError, RuntimeError) as error:
            execution = SimulationExecution(
                False,
                None,
                SimulationIssueCode.FAILED,
                str(error),
            )
        except Exception as error:  # I/O/native adapter boundary: fail closed.
            execution = SimulationExecution(
                False,
                None,
                SimulationIssueCode.FAILED,
                str(error),
            )
        self._finish_rejected(handle, execution, state_callback)
        return execution

    def report_rendering(
        self,
        request_id: SimulationRequestId,
        *,
        processed: int,
        total: int,
        callback: ProgressCallback | None = None,
    ) -> bool:
        with self._lock:
            handle = next(
                (
                    item
                    for item in self._handles.values()
                    if item.request.request_id == request_id
                ),
                None,
            )
        if handle is None:
            return False
        progress = SimulationProgress(
            request_id,
            SimulationProgressPhase.RENDERING_OVERLAY,
            processed,
            total,
        )
        if not self._accept_progress(handle, progress):
            return False
        if callback is not None:
            callback(progress)
        return True

    def cancel(self, operation_id: OperationId) -> bool:
        with self._lock:
            handle = self._handles.get(operation_id)
            record = self._records.get(operation_id)
            if handle is None or record is None:
                return False
            if record.state is SimulationRunState.COMPLETED:
                return False
            handle.cancellation.cancel()
            self._runtime.abort(operation_id, handle.computation_token)
            self._records[operation_id] = replace(
                record,
                state=SimulationRunState.CANCELLING,
                diagnostic_code=SimulationIssueCode.CANCELLED,
                diagnostic_message="Simulation cancellation requested",
            )
            return True

    def cancel_all(self, *, stale: bool = False) -> None:
        with self._lock:
            operation_ids = tuple(self._handles)
        for operation_id in operation_ids:
            self.cancel(operation_id)
            if stale:
                self._runtime.mark_stale(operation_id)
                with self._lock:
                    record = self._records.get(operation_id)
                    if record is not None:
                        self._records[operation_id] = replace(
                            record,
                            state=SimulationRunState.STALE,
                            completed_at=datetime.now(timezone.utc),
                            diagnostic_code=SimulationIssueCode.STALE_RESULT,
                            diagnostic_message="Simulation project context changed",
                        )

    def clear_result(self, operation_id: OperationId) -> None:
        self.cancel(operation_id)
        self._runtime.remove(operation_id)
        with self._lock:
            self._handles.pop(operation_id, None)
            self._records.pop(operation_id, None)

    def mark_stale(self, operation_id: OperationId, message: str = "Simulation inputs changed") -> None:
        self.cancel(operation_id)
        self._runtime.mark_stale(operation_id)
        with self._lock:
            record = self._records.get(operation_id)
            if record is not None:
                self._records[operation_id] = replace(
                    record,
                    state=SimulationRunState.STALE,
                    completed_at=datetime.now(timezone.utc),
                    diagnostic_code=SimulationIssueCode.STALE_RESULT,
                    diagnostic_message=message,
                )

    def record(self, operation_id: OperationId) -> SimulationRunRecord | None:
        with self._lock:
            return self._records.get(operation_id)

    def result(self, operation_id: OperationId) -> SimulationResult | None:
        return self._runtime.get(operation_id)

    def results(self) -> tuple[SimulationResult, ...]:
        with self._lock:
            operation_ids = tuple(sorted(self._records, key=str))
        return tuple(
            result
            for operation_id in operation_ids
            if (result := self._runtime.get(operation_id)) is not None
        )

    def is_active(self, operation_id: OperationId) -> bool:
        with self._lock:
            record = self._records.get(operation_id)
            return record is not None and record.state in {
                SimulationRunState.VALIDATING,
                SimulationRunState.RUNNING,
                SimulationRunState.CANCELLING,
            }

    def restore_cached(
        self,
        request: SimulationRequest,
        result: SimulationResult,
        *,
        state_callback: StateCallback | None = None,
    ) -> bool:
        handle = self.start(request, state_callback=state_callback)
        execution = self._runtime.publish(
            request=request,
            token=handle.computation_token,
            candidate=result,
            current_request=request,
        )
        if not execution.accepted:
            self._finish_rejected(handle, execution, state_callback)
            return False
        self._finish_completed(handle, result, state_callback)
        return True

    def _accept_progress(
        self,
        handle: SimulationRunHandle,
        progress: SimulationProgress,
    ) -> bool:
        with self._lock:
            if self._handles.get(handle.request.operation_id) is not handle:
                return False
            record = self._records.get(handle.request.operation_id)
            if (
                record is None
                or record.request_id != progress.request_id
                or record.project_generation != handle.project_generation
                or record.state
                in {SimulationRunState.CANCELLING, SimulationRunState.STALE}
            ):
                return False
            self._records[handle.request.operation_id] = replace(
                record,
                progress=progress,
            )
            return True

    def _require_current_handle(self, handle: SimulationRunHandle) -> None:
        with self._lock:
            if (
                self._handles.get(handle.request.operation_id) is not handle
                or self._project_generation != handle.project_generation
                or handle.cancellation.cancelled
            ):
                raise SimulationSamplingError(
                    SimulationIssueCode.CANCELLED,
                    "Simulation run is no longer current",
                )

    def _set_state(
        self,
        handle: SimulationRunHandle,
        state: SimulationRunState,
        callback: StateCallback | None,
    ) -> None:
        with self._lock:
            if self._handles.get(handle.request.operation_id) is not handle:
                return
            record = replace(self._records[handle.request.operation_id], state=state)
            self._records[handle.request.operation_id] = record
        if callback is not None:
            callback(record)

    def _finish_completed(
        self,
        handle: SimulationRunHandle,
        result: SimulationResult,
        callback: StateCallback | None,
    ) -> None:
        with self._lock:
            if self._handles.get(handle.request.operation_id) is not handle:
                return
            record = replace(
                self._records[handle.request.operation_id],
                state=SimulationRunState.COMPLETED,
                completed_at=datetime.now(timezone.utc),
                result_id=result.result_id,
                diagnostic_code=None,
                diagnostic_message=None,
            )
            self._records[handle.request.operation_id] = record
        if callback is not None:
            callback(record)

    def _finish_rejected(
        self,
        handle: SimulationRunHandle,
        execution: SimulationExecution,
        callback: StateCallback | None,
    ) -> None:
        self._runtime.abort(handle.request.operation_id, handle.computation_token)
        with self._lock:
            if self._handles.get(handle.request.operation_id) is not handle:
                return
            current = self._records[handle.request.operation_id]
            if current.state is SimulationRunState.STALE:
                # An input/project invalidation owns the terminal state even
                # when the cooperative sampling loop reports cancellation.
                state = SimulationRunState.STALE
                diagnostic_code = current.diagnostic_code or SimulationIssueCode.STALE_RESULT
                diagnostic_message = current.diagnostic_message or execution.message
            elif execution.code is SimulationIssueCode.CANCELLED:
                state = SimulationRunState.IDLE
                diagnostic_code = execution.code
                diagnostic_message = execution.message
            elif execution.code is SimulationIssueCode.STALE_RESULT:
                state = SimulationRunState.STALE
                diagnostic_code = execution.code
                diagnostic_message = execution.message
            else:
                state = SimulationRunState.FAILED
                diagnostic_code = execution.code
                diagnostic_message = execution.message
            record = replace(
                current,
                state=state,
                completed_at=datetime.now(timezone.utc),
                diagnostic_code=diagnostic_code,
                diagnostic_message=diagnostic_message,
            )
            self._records[handle.request.operation_id] = record
        if callback is not None:
            callback(record)
