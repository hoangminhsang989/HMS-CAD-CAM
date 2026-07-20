"""Headless synchronous simulation orchestration and atomic runtime registry."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable, Hashable
from uuid import UUID, uuid4

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.ids import OperationId, SimulationRequestId, SimulationResultId
from hms_cadcam.cam.domain.machine import MachineDefinition
from hms_cadcam.cam.domain.operation import ArtifactStatus, Operation
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.domain.setup import BoxStock, Setup
from hms_cadcam.cam.domain.tooling import HolderDefinition, ToolAssembly, ToolDefinition
from hms_cadcam.cam.toolpath.geometry import CoordinateSpace
from hms_cadcam.cam.toolpath.model import ToolpathArtifact, ToolpathCompletionStatus
from .collision import CollisionBackend, CollisionScene, run_collision_analysis
from .envelope import UnsupportedToolGeometryError, build_tool_envelope
from .model import SimulationIssueCode, SimulationRequest, SimulationResult, SimulationSamplingPolicy
from .sampling import SimulationSamplingError, sample_toolpath


class SimulationPreflightError(RuntimeError):
    """Validation failure for which a new result must not be published."""

    def __init__(self, code: SimulationIssueCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SimulationComputationToken:
    value: UUID
    project_generation: int
    sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID) or self.value.int == 0 or type(self.project_generation) is not int or self.project_generation <= 0 or type(self.sequence) is not int or self.sequence <= 0:
            raise CamValidationError("Simulation computation token is invalid")


@dataclass(frozen=True, slots=True)
class SimulationExecution:
    accepted: bool
    result: SimulationResult | None
    code: SimulationIssueCode | None = None
    message: str | None = None


def _source_error(code: SimulationIssueCode, message: str) -> None:
    raise SimulationPreflightError(code, message)


def build_simulation_request(*, operation: Operation, artifact: ToolpathArtifact, setup: Setup, tool: ToolDefinition, assembly: ToolAssembly, holder: HolderDefinition | None, machine: MachineDefinition | None, sampling_policy: SimulationSamplingPolicy | None = None, safe_height: float | None = None) -> SimulationRequest:
    """Validate live immutable snapshots and capture one deterministic request."""
    if not operation.enabled:
        _source_error(SimulationIssueCode.INVALID_REQUEST, "Operation is disabled")
    if operation.artifact_state.status is not ArtifactStatus.VALID:
        _source_error(SimulationIssueCode.SOURCE_STALE, "Operation toolpath lifecycle is not VALID")
    if artifact.completion_status is not ToolpathCompletionStatus.COMPLETE or artifact.artifact_fingerprint is None:
        _source_error(SimulationIssueCode.SOURCE_UNSUPPORTED, "Only COMPLETE fingerprinted toolpaths can be simulated")
    if artifact.coordinate_space is not CoordinateSpace.SETUP_WCS:
        _source_error(SimulationIssueCode.SOURCE_UNSUPPORTED, "Simulation v1 requires SETUP_WCS toolpath coordinates")
    if artifact.source_operation_id != operation.operation_id or artifact.operation_revision != operation.revision:
        _source_error(SimulationIssueCode.SOURCE_STALE, "Toolpath operation provenance is stale")
    state = operation.artifact_state
    if state.artifact_fingerprint != artifact.artifact_fingerprint or state.input_fingerprint != artifact.input_fingerprint or state.generation != artifact.computation_token.generation:
        _source_error(SimulationIssueCode.SOURCE_STALE, "Toolpath is not the current published operation artifact")
    if artifact.setup_id != setup.setup_id or artifact.setup_revision != setup.revision or operation.setup_id != setup.setup_id:
        _source_error(SimulationIssueCode.SOURCE_STALE, "Setup provenance is stale")
    wcs_fp = ContentFingerprint.from_payload(setup.wcs.to_dict())
    if artifact.wcs_fingerprint != wcs_fp:
        _source_error(SimulationIssueCode.SOURCE_STALE, "WCS provenance is stale")
    if not isinstance(setup.stock, BoxStock):
        _source_error(SimulationIssueCode.UNSUPPORTED_GEOMETRY, "Simulation v1 supports BOX stock only")
    if artifact.unit is not setup.wcs.origin.unit or tool.unit is not artifact.unit or assembly.unit is not artifact.unit:
        _source_error(SimulationIssueCode.UNIT_MISMATCH, "Simulation inputs use incompatible length units")
    assembly_fingerprint = ContentFingerprint.from_payload(assembly.to_dict())
    legacy_assembly_fingerprint = assembly.content_fingerprint
    if assembly.assembly_id != artifact.tool_assembly_id or artifact.tool_assembly_fingerprint not in {
        assembly_fingerprint,
        legacy_assembly_fingerprint,
    }:
        _source_error(SimulationIssueCode.TOOL_STALE, "Tool assembly provenance is stale")
    if assembly.tool_id != tool.tool_id or assembly.expected_tool_revision != tool.revision or assembly.expected_tool_fingerprint != tool.content_fingerprint:
        _source_error(SimulationIssueCode.TOOL_STALE, "Tool definition provenance is stale")
    if assembly.holder_id is None or holder is None:
        _source_error(SimulationIssueCode.TOOL_MISSING, "A current holder definition is required")
    if assembly.holder_id != holder.holder_id or assembly.expected_holder_revision != holder.revision or assembly.expected_holder_fingerprint != holder.content_fingerprint:
        _source_error(SimulationIssueCode.TOOL_STALE, "Holder definition provenance is stale")
    if (artifact.machine_id is None) != (machine is None):
        _source_error(SimulationIssueCode.MACHINE_STALE, "Machine provenance is missing")
    if machine is not None and (artifact.machine_id != machine.machine_id or artifact.machine_fingerprint != machine.content_fingerprint or machine.unit is not artifact.unit):
        _source_error(SimulationIssueCode.MACHINE_STALE, "Machine definition provenance is stale")
    fixture_fps = tuple((fixture.fixture_id, ContentFingerprint.from_payload(fixture.to_dict())) for fixture in setup.fixtures if fixture.enabled)
    stock_fp = ContentFingerprint.from_payload(setup.stock.to_dict())
    policy = sampling_policy or SimulationSamplingPolicy()
    request_input = DependencyFingerprint.from_payload({
        "algorithm_version": 1, "artifact": artifact.artifact_fingerprint.to_dict(),
        "operation": {"id": str(operation.operation_id), "revision": operation.revision.value, "enabled": operation.enabled},
        "setup": {"id": str(setup.setup_id), "revision": setup.revision.value, "wcs": setup.wcs.to_dict(), "stock": setup.stock.to_dict(), "fixtures": [fixture.to_dict() for fixture in setup.fixtures if fixture.enabled]},
        "tool_assembly": assembly.to_dict(), "tool": tool.to_dict(), "holder": holder.to_dict(),
        "machine": machine.to_dict() if machine else None, "policy": policy.to_dict(), "safe_height": safe_height,
    })
    return SimulationRequest(request_id=SimulationRequestId.new(), operation_id=operation.operation_id, operation_revision=operation.revision, artifact_id=artifact.artifact_id, artifact_fingerprint=artifact.artifact_fingerprint, input_fingerprint=request_input, setup_id=setup.setup_id, setup_revision=setup.revision, wcs_fingerprint=wcs_fp, stock_fingerprint=stock_fp, fixture_fingerprints=fixture_fps, tool_assembly_id=assembly.assembly_id, tool_assembly_fingerprint=assembly_fingerprint, tool_id=tool.tool_id, tool_fingerprint=tool.content_fingerprint, holder_id=holder.holder_id, holder_fingerprint=holder.content_fingerprint, machine_id=machine.machine_id if machine else None, machine_fingerprint=machine.content_fingerprint if machine else None, unit=artifact.unit, sampling_policy=policy, safe_height=safe_height)


class SimulationRuntimeService:
    """Project-scoped latest-result registry with atomic token guarded publish."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._project_id: Hashable | None = None
        self._generation = 1
        self._sequence = 0
        self._tokens: dict[OperationId, SimulationComputationToken] = {}
        self._results: dict[OperationId, SimulationResult] = {}

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def bind_project(self, project_id: Hashable | None, generation: int | None = None) -> None:
        with self._lock:
            if project_id != self._project_id:
                self._tokens.clear(); self._results.clear(); self._project_id = project_id
            self._generation = generation if generation is not None and generation > 0 else self._generation + 1

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear(); self._results.clear(); self._project_id = None; self._generation += 1

    def invalidate_all(self) -> None:
        """Invalidate runtime-only results after any CAM input mutation."""
        with self._lock:
            self._tokens.clear(); self._results.clear(); self._generation += 1

    def begin(self, request: SimulationRequest) -> SimulationComputationToken:
        with self._lock:
            self._sequence += 1
            token = SimulationComputationToken(uuid4(), self._generation, self._sequence)
            self._tokens[request.operation_id] = token
            return token

    def get(self, operation_id: OperationId) -> SimulationResult | None:
        with self._lock:
            return self._results.get(operation_id)

    def remove(self, operation_id: OperationId) -> None:
        with self._lock:
            self._tokens.pop(operation_id, None); self._results.pop(operation_id, None)

    def mark_stale(self, operation_id: OperationId) -> None:
        """Drop a result when its source operation/artifact input changes."""
        self.remove(operation_id)

    def abort(
        self,
        operation_id: OperationId,
        token: SimulationComputationToken,
    ) -> bool:
        """Invalidate one active computation without dropping its prior result."""
        with self._lock:
            if self._tokens.get(operation_id) != token:
                return False
            self._tokens.pop(operation_id, None)
            return True

    def publish(self, *, request: SimulationRequest, token: SimulationComputationToken, candidate: SimulationResult, current_request: SimulationRequest | None = None) -> SimulationExecution:
        with self._lock:
            current = self._tokens.get(request.operation_id)
            if current != token or token.project_generation != self._generation:
                return SimulationExecution(False, None, SimulationIssueCode.STALE_RESULT, "Simulation token/generation is stale")
            if current_request is not None and current_request.identity_payload() != request.identity_payload():
                return SimulationExecution(False, None, SimulationIssueCode.STALE_RESULT, "Simulation inputs changed before publish")
            if candidate.request_id != request.request_id or candidate.operation_id != request.operation_id or candidate.artifact_id != request.artifact_id or candidate.input_fingerprint != request.input_fingerprint or candidate.artifact_fingerprint != request.artifact_fingerprint:
                return SimulationExecution(False, None, SimulationIssueCode.STALE_RESULT, "Simulation candidate provenance mismatch")
            self._results[request.operation_id] = candidate
            self._tokens.pop(request.operation_id, None)
            return SimulationExecution(True, candidate)

    def run(self, *, request: SimulationRequest, artifact: ToolpathArtifact, setup: Setup, tool: ToolDefinition, assembly: ToolAssembly, holder: HolderDefinition | None, scene: CollisionScene, backend: CollisionBackend, cancellation: Callable[[], bool] | None = None, current_request: Callable[[], SimulationRequest] | None = None) -> SimulationExecution:
        """Run synchronously on the caller thread; never publish partial/error candidates."""
        token = self.begin(request)
        try:
            if artifact.artifact_id != request.artifact_id or artifact.artifact_fingerprint != request.artifact_fingerprint:
                raise SimulationPreflightError(SimulationIssueCode.SOURCE_STALE, "Toolpath differs from captured request")
            sampling = sample_toolpath(artifact=artifact, wcs=setup.wcs, policy=request.sampling_policy, cancellation=cancellation)
            envelope = build_tool_envelope(tool=tool, assembly=assembly, holder=holder)
            candidate = run_collision_analysis(request=request, artifact=artifact, sampling=sampling, envelope=envelope, scene=scene, backend=backend, result_id=SimulationResultId.new(), cancellation=cancellation)
        except SimulationSamplingError as error:
            return SimulationExecution(False, None, error.code, str(error))
        except UnsupportedToolGeometryError as error:
            return SimulationExecution(False, None, error.code, str(error))
        except SimulationPreflightError as error:
            return SimulationExecution(False, None, error.code, str(error))
        except (CamValidationError, ValueError, RuntimeError) as error:
            return SimulationExecution(False, None, SimulationIssueCode.FAILED, str(error))
        try:
            live_request = current_request() if current_request is not None else request
        except Exception as error:
            return SimulationExecution(False, None, SimulationIssueCode.FAILED, str(error))
        return self.publish(request=request, token=token, candidate=candidate, current_request=live_request)
