"""Thread-safe application state and artifact registration for project lifecycle."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from hms_cadcam.cam.domain import (
    ArtifactStatus, CamChildNotFoundError, CamJob, CamJobId, CamNodeId,
    DiagnosticCode, DiagnosticSeverity, DirtyReason, FixtureInstance, Operation,
    HolderDefinition, MachineDefinition, OperationId, OperationTree, Setup,
    SetupId, StockDefinition, ToolAssembly, ToolDefinition, ValidationDiagnostic,
    WcsFrame, WorkOffset,
)
from hms_cadcam.cam.persistence.artifact_store import ToolpathArtifactStore
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import CamProjectSnapshot
from hms_cadcam.cam.toolpath import ToolpathArtifact
from hms_cadcam.cam.toolpath.validation import ToolpathPublishResult, publish_toolpath
from hms_cadcam.cam.domain.operation import ComputationToken
from hms_cadcam.cam.domain.revision import DependencyFingerprint


class CamApplicationService:
    """Own one current native-free snapshot under a re-entrant lock."""

    def __init__(self, artifact_store: ToolpathArtifactStore | None = None) -> None:
        self._artifact_store = artifact_store or ToolpathArtifactStore()
        self._lock = threading.RLock()
        self._snapshot = CamProjectSnapshot()
        self._persisted = CamProjectSnapshot()
        self._selection = CamSelection()
        self._generation = 0

    @property
    def snapshot(self) -> CamProjectSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def is_dirty(self) -> bool:
        with self._lock:
            return self._snapshot != self._persisted

    def load(self, snapshot: CamProjectSnapshot) -> None:
        if not isinstance(snapshot, CamProjectSnapshot):
            raise TypeError("CAM project snapshot is invalid")
        with self._lock:
            self._snapshot = snapshot
            self._persisted = snapshot
            self._selection = CamSelection()
            self._generation += 1

    def apply(self, mutation: Callable[[CamProjectSnapshot], CamProjectSnapshot]) -> CamProjectSnapshot:
        """Apply one validated mutation atomically in memory."""
        with self._lock:
            candidate = mutation(self._snapshot)
            if not isinstance(candidate, CamProjectSnapshot):
                raise TypeError("CAM mutation must return CamProjectSnapshot")
            self._snapshot = candidate
            return candidate

    def mark_persisted(self, snapshot: CamProjectSnapshot | None = None) -> None:
        with self._lock:
            if snapshot is not None:
                self._snapshot = snapshot
            self._persisted = self._snapshot

    def clear(self) -> None:
        with self._lock:
            empty = CamProjectSnapshot()
            self._snapshot = empty
            self._persisted = empty
            self._selection = CamSelection()
            self._generation += 1

    @property
    def selection(self) -> "CamSelection":
        with self._lock:
            return self._selection

    @property
    def generation(self) -> int:
        """Identify the active project so queued UI callbacks can be rejected."""
        with self._lock:
            return self._generation

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
            return replace(state, jobs=jobs, active_job_id=active)
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
            job.get_setup(setup.setup_id)
            job.replace_setup(setup)
        return self._mutate_job(job_id, change)

    def delete_setup(self, job_id: CamJobId, setup_id: SetupId) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.remove_setup(setup_id))

    def reorder_setup(self, job_id: CamJobId, setup_id: SetupId, new_index: int) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.reorder_setup(setup_id, new_index))

    def set_active_setup(self, job_id: CamJobId, setup_id: SetupId | None) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.set_active_setup(setup_id))

    def update_wcs(self, job_id: CamJobId, setup_id: SetupId, value: WcsFrame) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.update_wcs(setup_id, value))

    def update_work_offset(self, job_id: CamJobId, setup_id: SetupId, value: WorkOffset) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.update_work_offset(setup_id, value))

    def update_stock(self, job_id: CamJobId, setup_id: SetupId, value: StockDefinition) -> CamProjectSnapshot:
        return self._mutate_job(job_id, lambda job: job.set_stock(setup_id, value))

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
            return result

    def invalidate_operation(self, operation_id: OperationId,
                             reason: DirtyReason) -> CamProjectSnapshot:
        with self._lock:
            operation = _find_operation(self._snapshot, operation_id)
            changed = replace(operation, artifact_state=operation.artifact_state.mark_dirty(reason))
            self._snapshot = _replace_operation(self._snapshot, changed)
            return self._snapshot

    def _mutate_job(self, job_id: CamJobId, mutation: Callable[[CamJob], object]) -> CamProjectSnapshot:
        """Clone an aggregate first so failed validation cannot leak partial state."""
        def change(state: CamProjectSnapshot) -> CamProjectSnapshot:
            current = _job(state, job_id)
            candidate = CamJob.from_dict(current.to_dict())
            mutation(candidate)
            jobs = tuple(candidate if item.job_id == job_id else item for item in state.jobs)
            return replace(state, jobs=jobs)
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
