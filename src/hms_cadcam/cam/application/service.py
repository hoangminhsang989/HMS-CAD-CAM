"""Thread-safe application state and artifact registration for project lifecycle."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from hms_cadcam.cam.domain import (
    ArtifactStatus, CamChildNotFoundError, CamJob, CamJobId, CamNodeId,
    ContourParameters, DiagnosticCode, DiagnosticSeverity, DirtyReason, FacingBoundarySource,
    DrillGeometryInput, DrillDepthDefinition, DrillingStrategy, DrillValidationError,
    FacingParameters,
    FixtureInstance, GeometryReference, Operation,
    HolderDefinition, MachineDefinition, OperationId, OperationTree, Setup,
    ResolvedContourProfile, ResolvedDrillingGeometry, ResolvedMachiningGeometry,
    ResolvedPocketGeometry, SetupId, StockDefinition, ToolAssembly,
    ReamingStrategy, ReamingValidationError,
    BoringStrategy, BoringValidationError,
    TappingStrategy, TappingValidationError, ToolDefinition,
    ValidationDiagnostic, WcsFrame, WorkOffset,
)
from hms_cadcam.cam.persistence.artifact_store import ToolpathArtifactStore
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import CamProjectSnapshot
from hms_cadcam.cam.toolpath import ToolpathArtifact
from hms_cadcam.cam.toolpath.validation import ToolpathPublishResult, publish_toolpath
from hms_cadcam.cam.domain.operation import ComputationToken
from hms_cadcam.cam.domain.revision import DependencyFingerprint
from hms_cadcam.cam.application.facing import (
    FacingComputeResult, FacingGenerationError, FacingGenerator,
)
from hms_cadcam.cam.application.contour import (
    ContourComputeResult, ContourGenerationError, ContourGenerator,
)
from hms_cadcam.cam.application.pocket import (
    PocketComputeResult, PocketGenerationError, PocketGenerator,
)
from hms_cadcam.cam.application.drilling import (
    DrillingComputeResult, DrillingGenerationError, DrillingGenerator,
)
from hms_cadcam.cam.application.tapping import (
    TappingComputeResult, TappingGenerationError, TappingGenerator,
)
from hms_cadcam.cam.application.reaming import (
    ReamingComputeResult, ReamingGenerationError, ReamingGenerator,
)
from hms_cadcam.cam.application.boring import (
    BoringComputeResult, BoringGenerationError, BoringGenerator,
)
from hms_cadcam.cam.simulation.service import SimulationRuntimeService
from hms_cadcam.cam.post.service import PostRuntimeService


class CamApplicationService:
    """Own one current native-free snapshot under a re-entrant lock."""

    def __init__(self, artifact_store: ToolpathArtifactStore | None = None) -> None:
        self._artifact_store = artifact_store or ToolpathArtifactStore()
        self._lock = threading.RLock()
        self._snapshot = CamProjectSnapshot()
        self._persisted = CamProjectSnapshot()
        self._selection = CamSelection()
        self._generation = 0
        self._simulation = SimulationRuntimeService()
        self._post = PostRuntimeService()

    @property
    def snapshot(self) -> CamProjectSnapshot:
        with self._lock:
            return _clone_snapshot(self._snapshot)

    @property
    def is_dirty(self) -> bool:
        with self._lock:
            return self._snapshot != self._persisted

    def load(self, snapshot: CamProjectSnapshot) -> None:
        if not isinstance(snapshot, CamProjectSnapshot):
            raise TypeError("CAM project snapshot is invalid")
        with self._lock:
            self._snapshot = _clone_snapshot(snapshot)
            self._persisted = _clone_snapshot(snapshot)
            self._selection = CamSelection()
            self._generation += 1
            self._simulation.bind_project(self._generation, self._generation)
            self._post.clear()

    def apply(self, mutation: Callable[[CamProjectSnapshot], CamProjectSnapshot]) -> CamProjectSnapshot:
        """Apply one validated mutation atomically in memory."""
        with self._lock:
            candidate = mutation(_clone_snapshot(self._snapshot))
            if not isinstance(candidate, CamProjectSnapshot):
                raise TypeError("CAM mutation must return CamProjectSnapshot")
            operation_ids = {operation.operation_id for job in candidate.jobs
                             for setup in job.setups
                             for operation in setup.operation_tree.operations}
            candidate = replace(candidate, artifacts=tuple(
                metadata for metadata in candidate.artifacts
                if metadata.operation_id in operation_ids
            ))
            self._snapshot = _clone_snapshot(candidate)
            self._simulation.invalidate_all()
            self._post.invalidate_all()
            return _clone_snapshot(self._snapshot)

    def execute(
        self,
        command: Callable[["CamApplicationService"], CamProjectSnapshot],
    ) -> CamProjectSnapshot:
        """Run a possibly multi-step command as one in-memory transaction."""
        with self._lock:
            before = _clone_snapshot(self._snapshot)
            persisted = _clone_snapshot(self._persisted)
            selection = self._selection
            generation = self._generation
            try:
                changed = command(self)
                if not isinstance(changed, CamProjectSnapshot):
                    raise TypeError("CAM command must return CamProjectSnapshot")
            except Exception:
                self._snapshot = before
                self._persisted = persisted
                self._selection = selection
                self._generation = generation
                raise
            return _clone_snapshot(self._snapshot)

    def mark_persisted(self, snapshot: CamProjectSnapshot | None = None) -> None:
        with self._lock:
            if snapshot is not None:
                self._snapshot = _clone_snapshot(snapshot)
            self._persisted = _clone_snapshot(self._snapshot)

    def clear(self) -> None:
        with self._lock:
            empty = CamProjectSnapshot()
            self._snapshot = empty
            self._persisted = empty
            self._selection = CamSelection()
            self._generation += 1
            self._simulation.clear()
            self._post.clear()

    @property
    def selection(self) -> "CamSelection":
        with self._lock:
            return self._selection

    @property
    def generation(self) -> int:
        """Identify the active project so queued UI callbacks can be rejected."""
        with self._lock:
            return self._generation

    @property
    def simulation_service(self) -> SimulationRuntimeService:
        """Return the project-scoped, runtime-only simulation registry."""
        return self._simulation

    @property
    def post_runtime(self) -> PostRuntimeService:
        """Return the project-scoped, runtime-only post registry."""
        return self._post

    @property
    def post_service(self) -> PostRuntimeService:
        """Compatibility name matching the simulation-service accessor."""
        return self._post

    def select(self, selection: "CamSelection", *, generation: int | None = None) -> bool:
        """Select CAM identities, rejecting a callback from an older project."""
        if not isinstance(selection, CamSelection):
            raise TypeError("CAM selection is invalid")
        with self._lock:
            if generation is not None and generation != self._generation:
                return False
            self._selection = selection
            return True

    def create_job(self, name: str) -> CamProjectSnapshot:
        job = CamJob(CamJobId.new(), name)
        return self.apply(lambda state: replace(
            state, jobs=(*state.jobs, job), active_job_id=job.job_id
        ))

    def rename_job(self, job_id: CamJobId, name: str) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.rename(name))

    def delete_job(self, job_id: CamJobId) -> CamProjectSnapshot:
        def mutation(state: CamProjectSnapshot) -> CamProjectSnapshot:
            _job(state, job_id)
            jobs = tuple(item for item in state.jobs if item.job_id != job_id)
            active = state.active_job_id
            if active == job_id:
                active = jobs[0].job_id if jobs else None
            return replace(state, jobs=jobs, active_job_id=active,
                           artifacts=_referenced_artifacts(jobs, state.artifacts))
        return self.apply(mutation)

    def reorder_job(self, job_id: CamJobId, new_index: int) -> CamProjectSnapshot:
        def mutation(state: CamProjectSnapshot) -> CamProjectSnapshot:
            jobs = list(state.jobs)
            old_index = next((i for i, item in enumerate(jobs) if item.job_id == job_id), -1)
            if old_index < 0:
                raise CamChildNotFoundError(f"CAM job does not exist: {job_id}")
            if type(new_index) is not int or not 0 <= new_index < len(jobs):
                raise ValueError("CAM job position is out of range")
            jobs.insert(new_index, jobs.pop(old_index))
            return replace(state, jobs=tuple(jobs))
        return self.apply(mutation)

    def set_active_job(self, job_id: CamJobId | None) -> CamProjectSnapshot:
        def mutation(state: CamProjectSnapshot) -> CamProjectSnapshot:
            if job_id is not None:
                _job(state, job_id)
            return replace(state, active_job_id=job_id)
        return self.apply(mutation)

    def add_setup(self, job_id: CamJobId, setup: Setup) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.add_setup(setup))

    def rename_setup(self, job_id: CamJobId, setup_id: SetupId, name: str) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.rename_setup(setup_id, name))

    def replace_setup(self, job_id: CamJobId, setup: Setup) -> CamProjectSnapshot:
        """Replace a complete validated setup through the aggregate boundary."""
        def change(job: CamJob) -> None:
            current = job.get_setup(setup.setup_id)
            job.replace_setup(_invalidate_setup_dependencies(current, setup))
        return self._mutate_job(job_id, change)

    def delete_setup(self, job_id: CamJobId, setup_id: SetupId) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.remove_setup(setup_id))

    def reorder_setup(self, job_id: CamJobId, setup_id: SetupId, new_index: int) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.reorder_setup(setup_id, new_index))

    def set_active_setup(self, job_id: CamJobId, setup_id: SetupId | None) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.set_active_setup(setup_id))

    def update_wcs(self, job_id: CamJobId, setup_id: SetupId, value: WcsFrame) -> CamProjectSnapshot:
        def change(job: CamJob) -> None:
            current = job.get_setup(setup_id)
            job.replace_setup(_invalidate_setup_dependencies(current, current.with_wcs(value)))
        return self._mutate_job(job_id, change)

    def update_work_offset(self, job_id: CamJobId, setup_id: SetupId, value: WorkOffset) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.update_work_offset(setup_id, value))

    def update_stock(self, job_id: CamJobId, setup_id: SetupId, value: StockDefinition) -> CamProjectSnapshot:
        def change(job: CamJob) -> None:
            current = job.get_setup(setup_id)
            job.replace_setup(_invalidate_setup_dependencies(current, current.with_stock(value)))
        return self._mutate_job(job_id, change)

    def add_fixture(self, job_id: CamJobId, setup_id: SetupId, value: FixtureInstance) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.add_fixture(setup_id, value))

    def update_fixture(self, job_id: CamJobId, setup_id: SetupId, value: FixtureInstance) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.update_fixture(setup_id, value))

    def delete_fixture(self, job_id: CamJobId, setup_id: SetupId, fixture_id) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.remove_fixture(setup_id, fixture_id))

    def update_tree(self, job_id: CamJobId, setup_id: SetupId,
                    mutation: Callable[[OperationTree], OperationTree]) -> CamProjectSnapshot:
        def change(job: CamJob) -> None:
            tree = job.get_setup(setup_id).operation_tree
            candidate = mutation(tree)
            if not isinstance(candidate, OperationTree):
                raise TypeError("Operation tree mutation must return OperationTree")
            job.update_operation_tree(setup_id, candidate)
        return self._mutate_job(job_id, change)

    def add_tool_definition(self, value: ToolDefinition) -> CamProjectSnapshot:
        return self._append_unique("tool_definitions", value, "tool_id")

    def add_holder_definition(self, value: HolderDefinition) -> CamProjectSnapshot:
        return self._append_unique("holder_definitions", value, "holder_id")

    def add_tool_assembly(self, value: ToolAssembly) -> CamProjectSnapshot:
        return self._append_unique("tool_assemblies", value, "assembly_id")

    def add_machine_definition(self, value: MachineDefinition) -> CamProjectSnapshot:
        return self._append_unique("machine_definitions", value, "machine_id")

    def add_basic_resources(self, tool: ToolDefinition, holder: HolderDefinition,
                            assembly: ToolAssembly, machine: MachineDefinition) -> CamProjectSnapshot:
        """Atomically add one project-owned tooling and MILL machine bundle."""
        if (
            assembly.tool_id != tool.tool_id
            or assembly.expected_tool_revision != tool.revision
            or assembly.expected_tool_fingerprint != tool.content_fingerprint
            or assembly.holder_id != holder.holder_id
            or assembly.expected_holder_revision != holder.revision
            or assembly.expected_holder_fingerprint != holder.content_fingerprint
        ):
            raise ValueError("Tool assembly does not reference the supplied bundle definitions")
        if not (tool.unit is holder.unit is assembly.unit is machine.unit):
            raise ValueError("CAM resource bundle units must match explicitly")

        def mutation(state: CamProjectSnapshot) -> CamProjectSnapshot:
            return replace(state, tool_definitions=(*state.tool_definitions, tool),
                           holder_definitions=(*state.holder_definitions, holder),
                           tool_assemblies=(*state.tool_assemblies, assembly),
                           machine_definitions=(*state.machine_definitions, machine))
        return self.apply(mutation)

    def _append_unique(self, field: str, value: object, identity: str) -> CamProjectSnapshot:
        def mutation(state: CamProjectSnapshot) -> CamProjectSnapshot:
            values = getattr(state, field)
            key = getattr(value, identity)
            if any(getattr(item, identity) == key for item in values):
                raise ValueError(f"Duplicate CAM snapshot identity: {key}")
            return replace(state, **{field: (*values, value)})
        return self.apply(mutation)

    def register_artifact(self, project_root: Path, operation_id: OperationId,
                          candidate: ToolpathArtifact, token: ComputationToken,
                          current_input: DependencyFingerprint) -> ToolpathPublishResult:
        """Validate candidate, atomically publish its file, then stage metadata/state."""
        with self._lock:
            operation = _find_operation(self._snapshot, operation_id)
            result = publish_toolpath(operation, candidate, token, current_input)
            if not result.accepted or result.artifact is None:
                self._snapshot = _replace_operation(self._snapshot, result.operation)
                return result
            metadata = self._artifact_store.publish(project_root, result.artifact)
            artifacts = tuple(item for item in self._snapshot.artifacts if item.operation_id != operation_id)
            staged = replace(self._snapshot, artifacts=(*artifacts, metadata))
            self._snapshot = _replace_operation(staged, result.operation)
            self._post.mark_stale(operation_id)
            return result

    def load_artifact(self, project_root: Path, operation_id: OperationId) -> ToolpathArtifact | None:
        """Load one verified published artifact for a read-only consumer."""
        with self._lock:
            metadata = next((item for item in self._snapshot.artifacts
                             if item.operation_id == operation_id), None)
            if metadata is None:
                return None
            try:
                return self._artifact_store.load(project_root, metadata)
            except ToolpathArtifactStoreError:
                return None

    def invalidate_operation(self, operation_id: OperationId,
                             reason: DirtyReason) -> CamProjectSnapshot:
        with self._lock:
            operation = _find_operation(self._snapshot, operation_id)
            changed = replace(operation, artifact_state=operation.artifact_state.mark_dirty(reason))
            self._snapshot = _replace_operation(self._snapshot, changed)
            self._post.mark_stale(operation_id)
            return _clone_snapshot(self._snapshot)

    def compute_facing(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        face_resolver: Callable[[GeometryReference], ResolvedMachiningGeometry] | None = None,
    ) -> FacingComputeResult:
        """Synchronously compute and publish Facing while retaining stale-token contracts."""
        with self._lock:
            before_compute = _clone_snapshot(self._snapshot)
            operation = _find_operation(self._snapshot, operation_id)
            setup = next(setup for job in self._snapshot.jobs for setup in job.setups
                         if setup.setup_id == operation.setup_id)
            assembly = next((item for item in self._snapshot.tool_assemblies
                             if item.assembly_id == operation.tool_assembly.assembly_id), None)
            tool = None if assembly is None else next((item for item in self._snapshot.tool_definitions
                                                       if item.tool_id == assembly.tool_id), None)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            machine = next((item for item in self._snapshot.machine_definitions
                            if item.machine_id == machine_id), None)
            generator = FacingGenerator()
            try:
                try:
                    parameters = FacingParameters.from_operation_parameters(operation.parameters)
                except (TypeError, ValueError) as error:
                    raise FacingGenerationError(
                        DiagnosticCode.FACING_INVALID_PARAMETERS, str(error)
                    ) from error
                resolved_face = None
                if parameters.boundary_source is FacingBoundarySource.PLANAR_FACE:
                    if len(operation.geometry_inputs) != 1 or face_resolver is None:
                        raise FacingGenerationError(
                            DiagnosticCode.FACING_FACE_REFERENCE_MISSING,
                            "Planar Facing requires one resolvable persistent FACE reference.",
                        )
                    try:
                        resolved_face = face_resolver(operation.geometry_inputs[0].reference)
                    except (RuntimeError, TypeError, ValueError) as error:
                        raise FacingGenerationError(
                            DiagnosticCode.FACING_GEOMETRY_RESOLUTION_FAILED,
                            str(error) or "Planar FACE resolution failed.",
                        ) from error
                inputs = generator.resolve_inputs(operation, setup, assembly=assembly, tool=tool,
                                                  machine=machine, resolved_face=resolved_face)
                if operation.artifact_state.status is ArtifactStatus.VALID:
                    operation = replace(operation, artifact_state=operation.artifact_state.mark_dirty(
                        DirtyReason.PARAMETERS_CHANGED))
                    inputs = replace(inputs, operation=operation)
                    self._snapshot = _replace_operation(self._snapshot, operation)
                computing, token = generator.begin(inputs)
                self._snapshot = _replace_operation(self._snapshot, computing.operation)
                candidate = generator.generate(computing)
                current = _find_operation(self._snapshot, operation_id)
                publish = publish_toolpath(current, candidate, token, inputs.input_fingerprint)
                if not publish.accepted or publish.artifact is None:
                    self._snapshot = _replace_operation(self._snapshot, publish.operation)
                    diagnostic = ValidationDiagnostic(DiagnosticSeverity.ERROR,
                        DiagnosticCode.FACING_STALE_RESULT, "Kết quả Facing đã stale và không được publish.")
                    return FacingComputeResult(publish.operation, None, False, (diagnostic,))
                metadata = self._artifact_store.publish(project_root, publish.artifact)
                artifacts = tuple(item for item in self._snapshot.artifacts if item.operation_id != operation_id)
                staged = replace(self._snapshot, artifacts=(*artifacts, metadata))
                self._snapshot = _replace_operation(staged, publish.operation)
                self._post.mark_stale(operation_id)
                return FacingComputeResult(publish.operation, publish.artifact, True)
            except (FacingGenerationError, ToolpathArtifactStoreError) as error:
                if isinstance(error, FacingGenerationError):
                    diagnostic = error.diagnostic
                else:
                    diagnostic = ValidationDiagnostic(DiagnosticSeverity.ERROR,
                        DiagnosticCode.FACING_GENERATION_FAILED,
                        "Không thể publish file toolpath Facing an toàn.")
                original = _find_operation(before_compute, operation_id)
                if original.artifact_state.status is ArtifactStatus.VALID:
                    self._snapshot = before_compute
                    return FacingComputeResult(original, None, False, (diagnostic,))
                current = _find_operation(self._snapshot, operation_id)
                state = current.artifact_state
                if state.status is ArtifactStatus.COMPUTING and state.token is not None:
                    state, _ = state.fail(state.token, (diagnostic,))
                else:
                    state = replace(state, status=ArtifactStatus.FAILED, token=None,
                                    diagnostics=(diagnostic,))
                failed = replace(current, artifact_state=state,
                                 diagnostics=(*current.diagnostics, diagnostic))
                self._snapshot = _replace_operation(self._snapshot, failed)
                return FacingComputeResult(failed, None, False, (diagnostic,))

    def compute_contour(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        profile_resolver: Callable[[GeometryReference], ResolvedContourProfile] | None = None,
    ) -> ContourComputeResult:
        """Synchronously compute/publish 2D Contour with the shared stale-token contract."""
        with self._lock:
            before_compute = _clone_snapshot(self._snapshot)
            operation = _find_operation(self._snapshot, operation_id)
            setup = next(setup for job in self._snapshot.jobs for setup in job.setups
                         if setup.setup_id == operation.setup_id)
            assembly = next((item for item in self._snapshot.tool_assemblies
                             if item.assembly_id == operation.tool_assembly.assembly_id), None)
            tool = None if assembly is None else next((item for item in self._snapshot.tool_definitions
                                                       if item.tool_id == assembly.tool_id), None)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            machine = next((item for item in self._snapshot.machine_definitions
                            if item.machine_id == machine_id), None)
            generator = ContourGenerator()
            try:
                try:
                    ContourParameters.from_operation_parameters(operation.parameters)
                except (TypeError, ValueError) as error:
                    raise ContourGenerationError(DiagnosticCode.CONTOUR_INVALID_PARAMETERS, str(error)) from error
                if len(operation.geometry_inputs) != 1 or profile_resolver is None:
                    raise ContourGenerationError(DiagnosticCode.CONTOUR_PROFILE_MISSING,
                                                 "2D Contour requires one resolvable persistent profile reference.")
                try:
                    resolved = profile_resolver(operation.geometry_inputs[0].reference)
                except (RuntimeError, TypeError, ValueError) as error:
                    raise ContourGenerationError(DiagnosticCode.CONTOUR_PROFILE_MISSING,
                                                 str(error) or "2D Contour profile resolution failed.") from error
                inputs = generator.resolve_inputs(operation, setup, assembly=assembly, tool=tool,
                                                  machine=machine, resolved_profile=resolved)
                if operation.artifact_state.status is ArtifactStatus.VALID:
                    operation = replace(operation, artifact_state=operation.artifact_state.mark_dirty(
                        DirtyReason.PARAMETERS_CHANGED))
                    inputs = replace(inputs, operation=operation)
                    self._snapshot = _replace_operation(self._snapshot, operation)
                computing, token = generator.begin(inputs)
                self._snapshot = _replace_operation(self._snapshot, computing.operation)
                candidate = generator.generate(computing)
                current = _find_operation(self._snapshot, operation_id)
                publish = publish_toolpath(current, candidate, token, inputs.input_fingerprint)
                if not publish.accepted or publish.artifact is None:
                    self._snapshot = _replace_operation(self._snapshot, publish.operation)
                    diagnostic = ValidationDiagnostic(DiagnosticSeverity.ERROR,
                        DiagnosticCode.CONTOUR_STALE_RESULT,
                        "Kết quả 2D Contour đã stale và không được publish.")
                    return ContourComputeResult(publish.operation, None, False, (diagnostic,))
                metadata = self._artifact_store.publish(project_root, publish.artifact)
                artifacts = tuple(item for item in self._snapshot.artifacts if item.operation_id != operation_id)
                staged = replace(self._snapshot, artifacts=(*artifacts, metadata))
                self._snapshot = _replace_operation(staged, publish.operation)
                self._post.mark_stale(operation_id)
                return ContourComputeResult(publish.operation, publish.artifact, True)
            except (ContourGenerationError, ToolpathArtifactStoreError) as error:
                diagnostic = (error.diagnostic if isinstance(error, ContourGenerationError) else
                    ValidationDiagnostic(DiagnosticSeverity.ERROR, DiagnosticCode.CONTOUR_GENERATION_FAILED,
                                         "Không thể publish file toolpath 2D Contour an toàn."))
                original = _find_operation(before_compute, operation_id)
                if original.artifact_state.status is ArtifactStatus.VALID:
                    self._snapshot = before_compute
                    return ContourComputeResult(original, None, False, (diagnostic,))
                current = _find_operation(self._snapshot, operation_id)
                state = current.artifact_state
                if state.status is ArtifactStatus.COMPUTING and state.token is not None:
                    state, _ = state.fail(state.token, (diagnostic,))
                else:
                    state = replace(state, status=ArtifactStatus.FAILED, token=None,
                                    diagnostics=(diagnostic,))
                failed = replace(current, artifact_state=state,
                                 diagnostics=(*current.diagnostics, diagnostic))
                self._snapshot = _replace_operation(self._snapshot, failed)
                return ContourComputeResult(failed, None, False, (diagnostic,))

    def compute_pocket(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        geometry_resolver: Callable[[GeometryReference], ResolvedPocketGeometry] | None = None,
    ) -> PocketComputeResult:
        """Synchronously compute/publish Pocket with the shared stale-token contract."""
        with self._lock:
            before_compute = _clone_snapshot(self._snapshot)
            operation = _find_operation(self._snapshot, operation_id)
            setup = next(setup for job in self._snapshot.jobs for setup in job.setups
                         if setup.setup_id == operation.setup_id)
            assembly = next((item for item in self._snapshot.tool_assemblies
                             if item.assembly_id == operation.tool_assembly.assembly_id), None)
            tool = None if assembly is None else next((item for item in self._snapshot.tool_definitions
                                                       if item.tool_id == assembly.tool_id), None)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            machine = next((item for item in self._snapshot.machine_definitions
                            if item.machine_id == machine_id), None)
            generator = PocketGenerator()
            try:
                if len(operation.geometry_inputs) != 1 or geometry_resolver is None:
                    raise PocketGenerationError(
                        DiagnosticCode.POCKET_PROFILE_MISSING,
                        "Pocket requires one resolvable persistent boundary reference.",
                    )
                try:
                    resolved = geometry_resolver(operation.geometry_inputs[0].reference)
                except (RuntimeError, TypeError, ValueError) as error:
                    raise PocketGenerationError(
                        DiagnosticCode.POCKET_PROFILE_INVALID,
                        str(error) or "Pocket geometry resolution failed.",
                    ) from error
                inputs = generator.resolve_inputs(operation, setup, assembly=assembly, tool=tool,
                                                  machine=machine, resolved_geometry=resolved)
                if operation.artifact_state.status is ArtifactStatus.VALID:
                    operation = replace(operation, artifact_state=operation.artifact_state.mark_dirty(
                        DirtyReason.PARAMETERS_CHANGED))
                    inputs = replace(inputs, operation=operation)
                    self._snapshot = _replace_operation(self._snapshot, operation)
                computing, token = generator.begin(inputs)
                self._snapshot = _replace_operation(self._snapshot, computing.operation)
                candidate = generator.generate(computing)
                current = _find_operation(self._snapshot, operation_id)
                publish = publish_toolpath(current, candidate, token, inputs.input_fingerprint)
                if not publish.accepted or publish.artifact is None:
                    self._snapshot = _replace_operation(self._snapshot, publish.operation)
                    diagnostic = ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.POCKET_STALE_RESULT,
                        "Pocket result is stale and was not published.",
                    )
                    return PocketComputeResult(publish.operation, None, False, (diagnostic,))
                metadata = self._artifact_store.publish(project_root, publish.artifact)
                artifacts = tuple(item for item in self._snapshot.artifacts
                                  if item.operation_id != operation_id)
                staged = replace(self._snapshot, artifacts=(*artifacts, metadata))
                self._snapshot = _replace_operation(staged, publish.operation)
                self._post.mark_stale(operation_id)
                return PocketComputeResult(publish.operation, publish.artifact, True)
            except (PocketGenerationError, ToolpathArtifactStoreError) as error:
                diagnostic = (error.diagnostic if isinstance(error, PocketGenerationError) else
                    ValidationDiagnostic(DiagnosticSeverity.ERROR,
                                         DiagnosticCode.POCKET_GENERATION_FAILED,
                                         "Pocket toolpath file could not be published safely."))
                original = _find_operation(before_compute, operation_id)
                if original.artifact_state.status is ArtifactStatus.VALID:
                    self._snapshot = before_compute
                    return PocketComputeResult(original, None, False, (diagnostic,))
                current = _find_operation(self._snapshot, operation_id)
                state = current.artifact_state
                if state.status is ArtifactStatus.COMPUTING and state.token is not None:
                    state, _ = state.fail(state.token, (diagnostic,))
                else:
                    state = replace(state, status=ArtifactStatus.FAILED, token=None,
                                    diagnostics=(diagnostic,))
                failed = replace(current, artifact_state=state,
                                 diagnostics=(*current.diagnostics, diagnostic))
                self._snapshot = _replace_operation(self._snapshot, failed)
                return PocketComputeResult(failed, None, False, (diagnostic,))

    def compute_drilling(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        geometry_resolver: Callable[
            [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
        ] | None = None,
    ) -> DrillingComputeResult:
        """Synchronously compute/publish Drilling with the shared stale-token contract."""
        with self._lock:
            before_compute = _clone_snapshot(self._snapshot)
            operation = _find_operation(self._snapshot, operation_id)
            setup = next(
                setup for job in self._snapshot.jobs for setup in job.setups
                if setup.setup_id == operation.setup_id
            )
            assembly = next((
                item for item in self._snapshot.tool_assemblies
                if item.assembly_id == operation.tool_assembly.assembly_id
            ), None)
            tool = None if assembly is None else next((
                item for item in self._snapshot.tool_definitions
                if item.tool_id == assembly.tool_id
            ), None)
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            machine = next((
                item for item in self._snapshot.machine_definitions
                if item.machine_id == machine_id
            ), None)
            generator = DrillingGenerator()
            try:
                if geometry_resolver is None:
                    raise DrillingGenerationError(
                        DiagnosticCode.DRILL_GEOMETRY_MISSING,
                        "Drilling requires a resolvable geometry input.",
                    )
                try:
                    strategy = DrillingStrategy.from_operation_parameters(
                        operation.parameters
                    )
                    resolved = geometry_resolver(strategy.geometry, strategy.depth)
                except DrillingGenerationError:
                    raise
                except DrillValidationError as error:
                    raise DrillingGenerationError(error.code, str(error)) from error
                except (RuntimeError, TypeError, ValueError) as error:
                    raise DrillingGenerationError(
                        DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                        str(error) or "Drilling geometry resolution failed.",
                    ) from error
                inputs = generator.resolve_inputs(
                    operation, setup, assembly=assembly, tool=tool, machine=machine,
                    resolved_geometry=resolved,
                )
                if operation.artifact_state.status is ArtifactStatus.VALID:
                    operation = replace(
                        operation,
                        artifact_state=operation.artifact_state.mark_dirty(
                            DirtyReason.PARAMETERS_CHANGED
                        ),
                    )
                    inputs = replace(inputs, operation=operation)
                    self._snapshot = _replace_operation(self._snapshot, operation)
                computing, token = generator.begin(inputs)
                self._snapshot = _replace_operation(self._snapshot, computing.operation)
                candidate = generator.generate(computing)
                current = _find_operation(self._snapshot, operation_id)
                publish = publish_toolpath(
                    current, candidate, token, inputs.input_fingerprint
                )
                if not publish.accepted or publish.artifact is None:
                    self._snapshot = _replace_operation(
                        self._snapshot, publish.operation
                    )
                    diagnostic = ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.DRILL_STALE_RESULT,
                        "Drilling result is stale and was not published.",
                    )
                    return DrillingComputeResult(
                        publish.operation, None, False, (diagnostic,)
                    )
                metadata = self._artifact_store.publish(project_root, publish.artifact)
                artifacts = tuple(
                    item for item in self._snapshot.artifacts
                    if item.operation_id != operation_id
                )
                staged = replace(self._snapshot, artifacts=(*artifacts, metadata))
                self._snapshot = _replace_operation(staged, publish.operation)
                self._post.mark_stale(operation_id)
                return DrillingComputeResult(
                    publish.operation, publish.artifact, True
                )
            except (DrillingGenerationError, ToolpathArtifactStoreError) as error:
                diagnostic = (
                    error.diagnostic
                    if isinstance(error, DrillingGenerationError)
                    else ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.DRILL_GENERATION_FAILED,
                        "Drilling toolpath file could not be published safely.",
                    )
                )
                original = _find_operation(before_compute, operation_id)
                if original.artifact_state.status is ArtifactStatus.VALID:
                    self._snapshot = before_compute
                    return DrillingComputeResult(
                        original, None, False, (diagnostic,)
                    )
                current = _find_operation(self._snapshot, operation_id)
                state = current.artifact_state
                if state.status is ArtifactStatus.COMPUTING and state.token is not None:
                    state, _ = state.fail(state.token, (diagnostic,))
                else:
                    state = replace(
                        state, status=ArtifactStatus.FAILED, token=None,
                        diagnostics=(diagnostic,),
                    )
                failed = replace(
                    current,
                    artifact_state=state,
                    diagnostics=(*current.diagnostics, diagnostic),
                )
                self._snapshot = _replace_operation(self._snapshot, failed)
                return DrillingComputeResult(failed, None, False, (diagnostic,))

    def compute_tapping(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        geometry_resolver: Callable[
            [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
        ] | None = None,
    ) -> TappingComputeResult:
        """Synchronously compute/publish Tapping with the shared stale contract."""
        with self._lock:
            before_compute = _clone_snapshot(self._snapshot)
            operation = _find_operation(self._snapshot, operation_id)
            setup = next(
                setup for job in self._snapshot.jobs for setup in job.setups
                if setup.setup_id == operation.setup_id
            )
            assembly = next((
                item for item in self._snapshot.tool_assemblies
                if item.assembly_id == operation.tool_assembly.assembly_id
            ), None)
            tool = None if assembly is None else next((
                item for item in self._snapshot.tool_definitions
                if item.tool_id == assembly.tool_id
            ), None)
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            machine = next((
                item for item in self._snapshot.machine_definitions
                if item.machine_id == machine_id
            ), None)
            generator = TappingGenerator()
            try:
                if geometry_resolver is None:
                    raise TappingGenerationError(
                        DiagnosticCode.TAP_GEOMETRY_MISSING,
                        "Tapping requires a resolvable geometry input.",
                    )
                try:
                    strategy = TappingStrategy.from_operation_parameters(
                        operation.parameters
                    )
                    resolved = geometry_resolver(strategy.geometry, strategy.depth)
                except TappingGenerationError:
                    raise
                except TappingValidationError as error:
                    raise TappingGenerationError(error.code, str(error)) from error
                except (RuntimeError, TypeError, ValueError) as error:
                    raise TappingGenerationError(
                        DiagnosticCode.TAP_INVALID_PARAMETERS,
                        str(error) or "Tapping geometry resolution failed.",
                    ) from error
                inputs = generator.resolve_inputs(
                    operation,
                    setup,
                    assembly=assembly,
                    tool=tool,
                    machine=machine,
                    resolved_geometry=resolved,
                )
                if operation.artifact_state.status is ArtifactStatus.VALID:
                    operation = replace(
                        operation,
                        artifact_state=operation.artifact_state.mark_dirty(
                            DirtyReason.PARAMETERS_CHANGED
                        ),
                    )
                    inputs = replace(inputs, operation=operation)
                    self._snapshot = _replace_operation(self._snapshot, operation)
                computing, token = generator.begin(inputs)
                self._snapshot = _replace_operation(
                    self._snapshot, computing.operation
                )
                candidate = generator.generate(computing)
                current = _find_operation(self._snapshot, operation_id)
                publish = publish_toolpath(
                    current, candidate, token, inputs.input_fingerprint
                )
                if not publish.accepted or publish.artifact is None:
                    self._snapshot = _replace_operation(
                        self._snapshot, publish.operation
                    )
                    diagnostic = ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.TAP_STALE_RESULT,
                        "Tapping result is stale and was not published.",
                    )
                    return TappingComputeResult(
                        publish.operation, None, False, (diagnostic,)
                    )
                metadata = self._artifact_store.publish(
                    project_root, publish.artifact
                )
                artifacts = tuple(
                    item for item in self._snapshot.artifacts
                    if item.operation_id != operation_id
                )
                staged = replace(self._snapshot, artifacts=(*artifacts, metadata))
                self._snapshot = _replace_operation(staged, publish.operation)
                self._post.mark_stale(operation_id)
                return TappingComputeResult(
                    publish.operation, publish.artifact, True
                )
            except (TappingGenerationError, ToolpathArtifactStoreError) as error:
                diagnostic = (
                    error.diagnostic
                    if isinstance(error, TappingGenerationError)
                    else ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.TAP_GENERATION_FAILED,
                        "Tapping toolpath file could not be published safely.",
                    )
                )
                original = _find_operation(before_compute, operation_id)
                if original.artifact_state.status is ArtifactStatus.VALID:
                    self._snapshot = before_compute
                    return TappingComputeResult(
                        original, None, False, (diagnostic,)
                    )
                current = _find_operation(self._snapshot, operation_id)
                state = current.artifact_state
                if state.status is ArtifactStatus.COMPUTING and state.token is not None:
                    state, _ = state.fail(state.token, (diagnostic,))
                else:
                    state = replace(
                        state,
                        status=ArtifactStatus.FAILED,
                        token=None,
                        diagnostics=(diagnostic,),
                    )
                failed = replace(
                    current,
                    artifact_state=state,
                    diagnostics=(*current.diagnostics, diagnostic),
                )
                self._snapshot = _replace_operation(self._snapshot, failed)
                return TappingComputeResult(failed, None, False, (diagnostic,))

    def compute_reaming(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        geometry_resolver: Callable[
            [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
        ] | None = None,
    ) -> ReamingComputeResult:
        """Synchronously compute/publish Reaming with the shared stale contract."""
        with self._lock:
            before_compute = _clone_snapshot(self._snapshot)
            operation = _find_operation(self._snapshot, operation_id)
            setup = next(
                setup for job in self._snapshot.jobs for setup in job.setups
                if setup.setup_id == operation.setup_id
            )
            assembly = next((
                item for item in self._snapshot.tool_assemblies
                if item.assembly_id == operation.tool_assembly.assembly_id
            ), None)
            tool = None if assembly is None else next((
                item for item in self._snapshot.tool_definitions
                if item.tool_id == assembly.tool_id
            ), None)
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            machine = next((
                item for item in self._snapshot.machine_definitions
                if item.machine_id == machine_id
            ), None)
            generator = ReamingGenerator()
            try:
                if geometry_resolver is None:
                    raise ReamingGenerationError(
                        DiagnosticCode.REAM_GEOMETRY_MISSING,
                        "Reaming requires a resolvable geometry input.",
                    )
                try:
                    strategy = ReamingStrategy.from_operation_parameters(
                        operation.parameters
                    )
                    resolved = geometry_resolver(strategy.geometry, strategy.depth)
                except ReamingGenerationError:
                    raise
                except ReamingValidationError as error:
                    raise ReamingGenerationError(error.code, str(error)) from error
                except (RuntimeError, TypeError, ValueError) as error:
                    raise ReamingGenerationError(
                        DiagnosticCode.REAM_INVALID_PARAMETERS,
                        str(error) or "Reaming geometry resolution failed.",
                    ) from error
                inputs = generator.resolve_inputs(
                    operation,
                    setup,
                    assembly=assembly,
                    tool=tool,
                    machine=machine,
                    resolved_geometry=resolved,
                )
                if operation.artifact_state.status is ArtifactStatus.VALID:
                    operation = replace(
                        operation,
                        artifact_state=operation.artifact_state.mark_dirty(
                            DirtyReason.PARAMETERS_CHANGED
                        ),
                    )
                    inputs = replace(inputs, operation=operation)
                    self._snapshot = _replace_operation(self._snapshot, operation)
                computing, token = generator.begin(inputs)
                self._snapshot = _replace_operation(
                    self._snapshot, computing.operation
                )
                candidate = generator.generate(computing)
                current = _find_operation(self._snapshot, operation_id)
                publish = publish_toolpath(
                    current, candidate, token, inputs.input_fingerprint
                )
                if not publish.accepted or publish.artifact is None:
                    self._snapshot = _replace_operation(
                        self._snapshot, publish.operation
                    )
                    diagnostic = ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.REAM_STALE_RESULT,
                        "Reaming result is stale and was not published.",
                    )
                    return ReamingComputeResult(
                        publish.operation, None, False, (diagnostic,)
                    )
                metadata = self._artifact_store.publish(
                    project_root, publish.artifact
                )
                artifacts = tuple(
                    item for item in self._snapshot.artifacts
                    if item.operation_id != operation_id
                )
                staged = replace(self._snapshot, artifacts=(*artifacts, metadata))
                self._snapshot = _replace_operation(staged, publish.operation)
                self._post.mark_stale(operation_id)
                return ReamingComputeResult(
                    publish.operation, publish.artifact, True
                )
            except (ReamingGenerationError, ToolpathArtifactStoreError) as error:
                diagnostic = (
                    error.diagnostic
                    if isinstance(error, ReamingGenerationError)
                    else ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.REAM_GENERATION_FAILED,
                        "Reaming toolpath file could not be published safely.",
                    )
                )
                original = _find_operation(before_compute, operation_id)
                if original.artifact_state.status is ArtifactStatus.VALID:
                    self._snapshot = before_compute
                    return ReamingComputeResult(
                        original, None, False, (diagnostic,)
                    )
                current = _find_operation(self._snapshot, operation_id)
                state = current.artifact_state
                if state.status is ArtifactStatus.COMPUTING and state.token is not None:
                    state, _ = state.fail(state.token, (diagnostic,))
                else:
                    state = replace(
                        state,
                        status=ArtifactStatus.FAILED,
                        token=None,
                        diagnostics=(diagnostic,),
                    )
                failed = replace(
                    current,
                    artifact_state=state,
                    diagnostics=(*current.diagnostics, diagnostic),
                )
                self._snapshot = _replace_operation(self._snapshot, failed)
                return ReamingComputeResult(failed, None, False, (diagnostic,))

    def compute_boring(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        geometry_resolver: Callable[
            [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
        ] | None = None,
    ) -> BoringComputeResult:
        """Synchronously compute/publish Boring with the shared stale contract."""
        with self._lock:
            before_compute = _clone_snapshot(self._snapshot)
            operation = _find_operation(self._snapshot, operation_id)
            setup = next(
                setup for job in self._snapshot.jobs for setup in job.setups
                if setup.setup_id == operation.setup_id
            )
            assembly = next((
                item for item in self._snapshot.tool_assemblies
                if item.assembly_id == operation.tool_assembly.assembly_id
            ), None)
            tool = None if assembly is None else next((
                item for item in self._snapshot.tool_definitions
                if item.tool_id == assembly.tool_id
            ), None)
            holder = None if assembly is None or assembly.holder_id is None else next((
                item for item in self._snapshot.holder_definitions
                if item.holder_id == assembly.holder_id
            ), None)
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            machine = next((
                item for item in self._snapshot.machine_definitions
                if item.machine_id == machine_id
            ), None)
            generator = BoringGenerator()
            try:
                if geometry_resolver is None:
                    raise BoringGenerationError(
                        DiagnosticCode.BORE_GEOMETRY_MISSING,
                        "Boring requires a resolvable geometry input.",
                    )
                try:
                    strategy = BoringStrategy.from_operation_parameters(
                        operation.parameters
                    )
                    resolved = geometry_resolver(strategy.geometry, strategy.depth)
                except BoringGenerationError:
                    raise
                except BoringValidationError as error:
                    raise BoringGenerationError(error.code, str(error)) from error
                except (RuntimeError, TypeError, ValueError) as error:
                    raise BoringGenerationError(
                        DiagnosticCode.BORE_INVALID_PARAMETERS,
                        str(error) or "Boring geometry resolution failed.",
                    ) from error
                inputs = generator.resolve_inputs(
                    operation,
                    setup,
                    assembly=assembly,
                    tool=tool,
                    holder=holder,
                    machine=machine,
                    resolved_geometry=resolved,
                )
                if operation.artifact_state.status is ArtifactStatus.VALID:
                    operation = replace(
                        operation,
                        artifact_state=operation.artifact_state.mark_dirty(
                            DirtyReason.PARAMETERS_CHANGED
                        ),
                    )
                    inputs = replace(inputs, operation=operation)
                    self._snapshot = _replace_operation(self._snapshot, operation)
                computing, token = generator.begin(inputs)
                self._snapshot = _replace_operation(
                    self._snapshot, computing.operation
                )
                candidate = generator.generate(computing)
                current = _find_operation(self._snapshot, operation_id)
                publish = publish_toolpath(
                    current, candidate, token, inputs.input_fingerprint
                )
                if not publish.accepted or publish.artifact is None:
                    self._snapshot = _replace_operation(
                        self._snapshot, publish.operation
                    )
                    diagnostic = ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.BORE_STALE_RESULT,
                        "Boring result is stale and was not published.",
                    )
                    return BoringComputeResult(
                        publish.operation, None, False, (diagnostic,)
                    )
                metadata = self._artifact_store.publish(
                    project_root, publish.artifact
                )
                artifacts = tuple(
                    item for item in self._snapshot.artifacts
                    if item.operation_id != operation_id
                )
                staged = replace(self._snapshot, artifacts=(*artifacts, metadata))
                self._snapshot = _replace_operation(staged, publish.operation)
                self._post.mark_stale(operation_id)
                return BoringComputeResult(
                    publish.operation, publish.artifact, True
                )
            except (BoringGenerationError, ToolpathArtifactStoreError) as error:
                diagnostic = (
                    error.diagnostic
                    if isinstance(error, BoringGenerationError)
                    else ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.BORE_GENERATION_FAILED,
                        "Boring toolpath file could not be published safely.",
                    )
                )
                original = _find_operation(before_compute, operation_id)
                if original.artifact_state.status is ArtifactStatus.VALID:
                    self._snapshot = before_compute
                    return BoringComputeResult(
                        original, None, False, (diagnostic,)
                    )
                current = _find_operation(self._snapshot, operation_id)
                state = current.artifact_state
                if state.status is ArtifactStatus.COMPUTING and state.token is not None:
                    state, _ = state.fail(state.token, (diagnostic,))
                else:
                    state = replace(
                        state,
                        status=ArtifactStatus.FAILED,
                        token=None,
                        diagnostics=(diagnostic,),
                    )
                failed = replace(
                    current,
                    artifact_state=state,
                    diagnostics=(*current.diagnostics, diagnostic),
                )
                self._snapshot = _replace_operation(self._snapshot, failed)
                return BoringComputeResult(failed, None, False, (diagnostic,))

    def _mutate_job(self, job_id: CamJobId, mutation: Callable[[CamJob], object]) -> CamProjectSnapshot:
        """Clone an aggregate first so failed validation cannot leak partial state."""
        def change(state: CamProjectSnapshot) -> CamProjectSnapshot:
            current = _job(state, job_id)
            candidate = CamJob.from_dict(current.to_dict())
            mutation(candidate)
            jobs = tuple(candidate if item.job_id == job_id else item for item in state.jobs)
            return replace(state, jobs=jobs,
                           artifacts=_referenced_artifacts(jobs, state.artifacts))
        return self.apply(change)


@dataclass(frozen=True, slots=True)
class CamSelection:
    """Toolkit-free selection by stable domain identity."""

    job_id: CamJobId | None = None
    setup_id: SetupId | None = None
    node_id: CamNodeId | None = None


def _job(snapshot: CamProjectSnapshot, job_id: CamJobId) -> CamJob:
    for item in snapshot.jobs:
        if item.job_id == job_id:
            return item
    raise CamChildNotFoundError(f"CAM job does not exist: {job_id}")


def _clone_snapshot(snapshot: CamProjectSnapshot) -> CamProjectSnapshot:
    """Detach mutable aggregate roots at every public service boundary."""
    return replace(
        snapshot,
        jobs=tuple(CamJob.from_dict(job.to_dict()) for job in snapshot.jobs),
    )


def reconcile_artifacts(project_root: Path, snapshot: CamProjectSnapshot,
                        store: ToolpathArtifactStore) -> CamProjectSnapshot:
    """Keep editable state loadable while degrading missing/corrupt derived files."""
    valid_metadata = []
    invalid: dict[OperationId, DiagnosticCode] = {}
    for metadata in snapshot.artifacts:
        try:
            path = store.resolve_metadata_path(project_root, metadata)
            if not path.is_file():
                invalid[metadata.operation_id] = DiagnosticCode.ARTIFACT_MISSING
                continue
            store.load(project_root, metadata)
            valid_metadata.append(metadata)
        except ToolpathArtifactStoreError:
            invalid[metadata.operation_id] = DiagnosticCode.ARTIFACT_CORRUPT
    metadata_operations = {item.operation_id for item in valid_metadata}
    result = replace(snapshot, artifacts=tuple(valid_metadata))
    for job in result.jobs:
        for setup in job.setups:
            for operation in setup.operation_tree.operations:
                code = invalid.get(operation.operation_id)
                if code is None and operation.artifact_state.status is ArtifactStatus.VALID and operation.operation_id not in metadata_operations:
                    code = DiagnosticCode.ARTIFACT_MISSING
                if code is not None:
                    diagnostic = ValidationDiagnostic(DiagnosticSeverity.ERROR, code,
                        "Published toolpath artifact is missing or invalid",
                        (("operation_id", str(operation.operation_id)),))
                    state = operation.artifact_state.mark_dirty(DirtyReason.ARTIFACT_MISSING)
                    changed = replace(operation, artifact_state=state,
                                      diagnostics=(*operation.diagnostics, diagnostic))
                    result = _replace_operation(result, changed)
    return result


def _find_operation(snapshot: CamProjectSnapshot, operation_id: OperationId) -> Operation:
    for job in snapshot.jobs:
        for setup in job.setups:
            for operation in setup.operation_tree.operations:
                if operation.operation_id == operation_id:
                    return operation
    raise CamChildNotFoundError(f"Operation does not exist: {operation_id}")


def _replace_operation(snapshot: CamProjectSnapshot, changed: Operation) -> CamProjectSnapshot:
    jobs = []
    found = False
    for job in snapshot.jobs:
        setups = []
        job_changed = False
        for setup in job.setups:
            operations = tuple(changed if item.operation_id == changed.operation_id else item
                               for item in setup.operation_tree.operations)
            if operations != setup.operation_tree.operations:
                found = True
                job_changed = True
                tree = OperationTree(setup.setup_id, setup.operation_tree.root_id,
                    setup.operation_tree.nodes, operations, setup.operation_tree.dependency_graph,
                    setup.operation_tree.revision)
                setups.append(replace(setup, operation_tree=tree))
            else:
                setups.append(setup)
        if job_changed:
            from hms_cadcam.cam.domain import CamJob
            jobs.append(CamJob(job.job_id, job.name, revision=job.revision,
                               setups=tuple(setups), active_setup_id=job.active_setup_id))
        else:
            jobs.append(job)
    if not found:
        raise CamChildNotFoundError(f"Operation does not exist: {changed.operation_id}")
    return replace(snapshot, jobs=tuple(jobs))


def _invalidate_setup_dependencies(current: Setup, candidate: Setup) -> Setup:
    reasons = []
    if current.wcs != candidate.wcs:
        reasons.append(DirtyReason.WCS_CHANGED)
    if current.stock != candidate.stock:
        reasons.append(DirtyReason.STOCK_CHANGED)
    if not reasons:
        return candidate
    operations = candidate.operation_tree.operations
    for reason in reasons:
        operations = tuple(replace(operation,
            artifact_state=operation.artifact_state.mark_dirty(reason))
            for operation in operations)
    tree = OperationTree(candidate.setup_id, candidate.operation_tree.root_id,
        candidate.operation_tree.nodes, operations,
        candidate.operation_tree.dependency_graph,
        candidate.operation_tree.revision.next())
    return replace(candidate, operation_tree=tree, revision=candidate.revision.next())


def _referenced_artifacts(jobs: tuple[CamJob, ...], artifacts: tuple) -> tuple:
    operation_ids = {operation.operation_id for job in jobs for setup in job.setups
                     for operation in setup.operation_tree.operations}
    return tuple(metadata for metadata in artifacts
                 if metadata.operation_id in operation_ids)
