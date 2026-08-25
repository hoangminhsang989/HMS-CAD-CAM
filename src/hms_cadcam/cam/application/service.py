"""Thread-safe application state and artifact registration for project lifecycle."""

from __future__ import annotations

from collections import OrderedDict
import threading
import json
import logging
from time import monotonic_ns
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    ArtifactStatus, CamChildNotFoundError, CamValidationError, CamJob, CamJobId, CamNodeId,
    ContourParameters, DiagnosticCode, DiagnosticSeverity, DirtyReason, FacingBoundarySource,
    DrillGeometryInput, DrillDepthDefinition, DrillingStrategy, DrillValidationError,
    FacingParameters, FacingRegion,
    FixtureInstance, GeometryReference, Operation,
    HolderDefinition, MachineDefinition, MachineRequirement, OperationId,
    OperationTree, Setup,
    ResolvedContourProfile, ResolvedDrillingGeometry, ResolvedMachiningGeometry,
    ResolvedPocketGeometry, SetupId, StockDefinition, ToolAssembly,
    ToolAssemblyId,
    ReamingStrategy, ReamingValidationError,
    BoringStrategy, BoringValidationError,
    TappingStrategy, TappingValidationError, ToolDefinition,
    ToolCommonDefaults, ToolDefinitionId, ToolProfileDiffKind,
    ToolProfileSavePreview, ToolProfileValidationState,
    ToolProgramProfile, ToolProgramProfileId,
    ValidationDiagnostic, WcsFrame, WorkOffset,
    build_profile_from_preview, duplicate_tool_program_profile,
)
from hms_cadcam.cam.domain.dependency import DependencyEdge, DependencyKind
from hms_cadcam.cam.persistence.artifact_store import ToolpathArtifactStore
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import CamProjectSnapshot
from hms_cadcam.cam.persistence.models import MaterialStateDependency
from hms_cadcam.cam.toolpath import ToolpathArtifact, artifact_from_dict, artifact_to_dict
from hms_cadcam.cam.toolpath.fingerprint import (
    compute_material_removal_fingerprint, compute_toolpath_fingerprint,
)
from hms_cadcam.cam.toolpath.validation import ToolpathPublishResult, publish_toolpath
from hms_cadcam.cam.domain.operation import ComputationToken
from hms_cadcam.cam.domain.revision import (
    ContentFingerprint,
    DependencyFingerprint,
    Revision,
)
from hms_cadcam.cam.domain.tool_profiles import utc_profile_now
from hms_cadcam.cam.tool_library import ToolDefinitionDraft
from hms_cadcam.cam.application.facing import (
    FacingComputeResult, FacingGenerationError, FacingGenerator,
)
from hms_cadcam.cam.application.contour import (
    ContourComputeResult, ContourGenerationError, ContourGenerator, ContourPath,
    prepare_contour_machining_geometry,
)
from hms_cadcam.cam.application.pocket import (
    PocketComputeResult, PocketGenerationError, PocketGenerator,
    pocket_feed_independent_fingerprint, pocket_lead_independent_fingerprint,
    prepare_pocket_machining_geometry,
)
from hms_cadcam.cam.application.rest_pocket import RestPocketGenerator, RestPocketInputs
from hms_cadcam.cam.application.rest_finishing_application import (
    RestFinishingApplicationResult,
)
from hms_cadcam.cam.application.rest_finishing_lifecycle import (
    RestFinishingLifecyclePreparation,
)
from hms_cadcam.cam.domain.rest_finishing import (
    RestFinishingParameters,
    RestFinishingProfileSelection,
)
from hms_cadcam.cam.material_state import (MATERIAL_STATE_ENGINE_VERSION,
    MaterialState, MaterialStateLoadStatus, MaterialStateStore, calculate_material_state,
    material_state_setup_fingerprint)
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
from hms_cadcam.cam.cam3d.parallel import ParallelFinishingComputeResult
from hms_cadcam.cam.cam3d.zlevel import ZLevelFinishingComputeResult
from hms_cadcam.cam.optimization import (
    CalculationArtifactStore,
    CheckpointStore,
    CacheLookupStatus,
    CalculationTiming,
    CamCalculationProgress,
    CamPhaseState,
    PhaseTiming,
    contour_geometry_from_dict,
    contour_geometry_to_dict,
    facing_region_from_dict,
    facing_region_to_dict,
    pocket_geometry_from_dict,
    pocket_geometry_to_dict,
)

logger = logging.getLogger(__name__)
_POCKET_FINAL_CACHE_MAX_EVENT_ESTIMATE = 5_000
_POCKET_INCREMENTAL_TEMPLATE_MAX_EVENTS = 100_000


@dataclass(frozen=True, slots=True)
class _RestContourRehydratedResult:
    """Internal semantic replay of a fully verified persisted completion."""

    status: object
    preparation: object
    publication: object
    successor_publication: object
    candidate: None = None
    diagnostic_code: None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class _RestContourRehydratedPreparation:
    """Status-only replay preparation; it contains no R271 reservation."""

    status: object
    diagnostic_code: None = None
    message: str = ""


def _report_calculation_progress(
    callback: Callable[[CamCalculationProgress], None] | None,
    value: CamCalculationProgress,
) -> None:
    """Keep optional UI observation outside the machining correctness path."""
    if callback is None:
        return
    try:
        callback(value)
    except Exception:
        logger.warning("CAM calculation progress observer failed", exc_info=True)


class CamApplicationService:
    """Own one current native-free snapshot under a re-entrant lock."""

    def __init__(self, artifact_store: ToolpathArtifactStore | None = None) -> None:
        self._artifact_store = artifact_store or ToolpathArtifactStore()
        self._calculation_cache = CalculationArtifactStore()
        self._checkpoint_store = CheckpointStore()
        self._calculation_timings: dict[OperationId, CalculationTiming] = {}
        self._pocket_incremental_templates: OrderedDict[
            OperationId,
            tuple[ContentFingerprint, ContentFingerprint, ToolpathArtifact],
        ] = OrderedDict()
        self._rest_incremental_templates: OrderedDict[
            OperationId, tuple[ContentFingerprint, RestPocketInputs]
        ] = OrderedDict()
        # R272 keeps the sealed R271 reservation service-owned.  UI/project
        # callers identify an Operation only; they never receive authority to
        # mint or replay a Prepared object.
        self._rest_contour_preparations: dict[OperationId, object] = {}
        self._rest_contour_completed: dict[OperationId, object] = {}
        self._lock = threading.RLock()
        self._snapshot = CamProjectSnapshot()
        self._persisted = CamProjectSnapshot()
        self._selection = CamSelection()
        self._generation = 0
        self._simulation = SimulationRuntimeService()
        self._post = PostRuntimeService()
        self._material_states = MaterialStateStore()

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
            self._pocket_incremental_templates.clear()
            self._rest_incremental_templates.clear()
            self._rest_contour_preparations.clear()
            self._rest_contour_completed.clear()
            self._calculation_timings.clear()
            self._generation += 1
            self._simulation.bind_project(self._generation, self._generation)
            self._post.clear()

    def apply(self, mutation: Callable[[CamProjectSnapshot], CamProjectSnapshot]) -> CamProjectSnapshot:
        """Apply one validated mutation atomically in memory."""
        with self._lock:
            candidate = mutation(_clone_snapshot(self._snapshot))
            if not isinstance(candidate, CamProjectSnapshot):
                raise TypeError("CAM mutation must return CamProjectSnapshot")
            # Any aggregate mutation can affect Rest authority indirectly
            # (profile, tool, machine, stock or graph).  A completed-result
            # replay is valid only while this exact snapshot remains current.
            self._rest_contour_completed.clear()
            operation_ids = {operation.operation_id for job in candidate.jobs
                             for setup in job.setups
                             for operation in setup.operation_tree.operations}
            invalid_rest_completions = {
                dependency.consumer_operation_id
                for dependency in self._snapshot.material_state_dependencies
                if dependency.successor_publication is not None
                and _rest_contour_completion_signature(self._snapshot, dependency)
                != _rest_contour_completion_signature(candidate, dependency)
            }
            self._pocket_incremental_templates = OrderedDict(
                (operation_id, value)
                for operation_id, value in self._pocket_incremental_templates.items()
                if operation_id in operation_ids
            )
            self._rest_incremental_templates = OrderedDict(
                (operation_id, value)
                for operation_id, value in self._rest_incremental_templates.items()
                if operation_id in operation_ids
            )
            # A sealed Phase-B reservation is valid only for the exact
            # aggregate that produced it.  Even an unrelated editable change
            # must discard process-local authority rather than carrying it
            # across a snapshot generation.
            self._rest_contour_preparations.clear()
            self._rest_contour_completed = {
                operation_id: value
                for operation_id, value in self._rest_contour_completed.items()
                if operation_id in operation_ids
            }
            retained_dependencies: list[MaterialStateDependency] = []
            for dependency in candidate.material_state_dependencies:
                if dependency.consumer_operation_id not in invalid_rest_completions:
                    retained_dependencies.append(dependency)
                    continue
                try:
                    consumer = _find_operation(
                        candidate, dependency.consumer_operation_id,
                    )
                except CamChildNotFoundError:
                    continue
                if consumer.strategy_key == "rest_finishing_3axis":
                    # R274 invalidation revokes only the completed output.
                    # Its explicit R272 input edge/evidence remains necessary
                    # for a later fail-closed regeneration.
                    retained_dependencies.append(
                        replace(dependency, successor_publication=None)
                    )
            candidate = replace(candidate, artifacts=tuple(
                metadata for metadata in candidate.artifacts
                if metadata.operation_id in operation_ids
                and metadata.operation_id not in invalid_rest_completions
            ), material_state_dependencies=tuple(retained_dependencies))
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
            incremental_templates = OrderedDict(self._pocket_incremental_templates)
            rest_incremental_templates = OrderedDict(self._rest_incremental_templates)
            calculation_timings = dict(self._calculation_timings)
            try:
                changed = command(self)
                if not isinstance(changed, CamProjectSnapshot):
                    raise TypeError("CAM command must return CamProjectSnapshot")
            except Exception:
                self._snapshot = before
                self._persisted = persisted
                self._selection = selection
                self._generation = generation
                self._pocket_incremental_templates = incremental_templates
                self._rest_incremental_templates = rest_incremental_templates
                self._calculation_timings = calculation_timings
                raise
            return _clone_snapshot(self._snapshot)

    def mark_persisted(self, snapshot: CamProjectSnapshot | None = None) -> None:
        with self._lock:
            if snapshot is not None:
                self._snapshot = _clone_snapshot(snapshot)
            self._persisted = _clone_snapshot(self._snapshot)

    def preview_geometry_sources_changed(
        self,
        source_ids: frozenset[object],
    ) -> tuple[CamProjectSnapshot, tuple[OperationId, ...]]:
        """Build a scoped stale snapshot without mutating runtime state."""
        with self._lock:
            return _stale_geometry_source_operations(
                _clone_snapshot(self._snapshot),
                source_ids,
            )

    def commit_persisted_geometry_change(
        self,
        snapshot: CamProjectSnapshot,
        affected_operation_ids: tuple[OperationId, ...],
    ) -> None:
        """Publish a persisted geometry change and stale only its dependants."""
        if not isinstance(snapshot, CamProjectSnapshot):
            raise TypeError("CAM project snapshot is invalid")
        if not isinstance(affected_operation_ids, tuple) or any(
            not isinstance(item, OperationId)
            for item in affected_operation_ids
        ):
            raise TypeError("Affected operation identities are invalid")
        with self._lock:
            self._snapshot = _clone_snapshot(snapshot)
            self._persisted = _clone_snapshot(snapshot)
            # Persisted source-geometry replacement bypasses ``apply()``.
            # Process-local R271 reservations/results must never survive that
            # aggregate replacement; any durable v2 record is revalidated
            # against the new snapshot on its next use.
            self._rest_contour_preparations.clear()
            self._rest_contour_completed.clear()
            for operation_id in affected_operation_ids:
                self._simulation.mark_stale(operation_id)
                self._post.mark_stale(operation_id)

    def clear(self) -> None:
        with self._lock:
            empty = CamProjectSnapshot()
            self._snapshot = empty
            self._persisted = empty
            self._selection = CamSelection()
            self._pocket_incremental_templates.clear()
            self._rest_incremental_templates.clear()
            self._rest_contour_preparations.clear()
            self._rest_contour_completed.clear()
            self._calculation_timings.clear()
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
            previous = {item.operation_id: item for item in tree.operations}
            current = {item.operation_id: item for item in candidate.operations}
            for operation_id in sorted(previous.keys() & current.keys(), key=str):
                if _material_removal_operation_authority(previous[operation_id]) != (
                    _material_removal_operation_authority(current[operation_id])
                ):
                    candidate = candidate.mark_dependency_changed(
                        DependencyKind.MATERIAL_STATE, str(operation_id)
                    )
            job.update_operation_tree(setup_id, candidate)
        return self._mutate_job(job_id, change)

    def add_tool_definition(self, value: ToolDefinition) -> CamProjectSnapshot:
        return self._append_unique("tool_definitions", value, "tool_id")

    def create_managed_tool(
        self, draft: ToolDefinitionDraft
    ) -> CamProjectSnapshot:
        """Create one Tool and its optional assembly with service-owned IDs."""
        if not isinstance(draft, ToolDefinitionDraft):
            raise TypeError("Managed Tool creation requires a typed draft")
        with self._lock:
            tool = draft.build_tool(ToolDefinitionId.new())
            holder = None
            if draft.holder_id is not None:
                holder = next(
                    (
                        item
                        for item in self._snapshot.holder_definitions
                        if item.holder_id == draft.holder_id
                    ),
                    None,
                )
                if holder is None:
                    raise CamChildNotFoundError(
                        f"Holder does not exist: {draft.holder_id}"
                    )
                if not draft.create_assembly:
                    raise ValueError("A Holder requires a Tool Assembly")
            assembly = (
                draft.build_assembly(ToolAssemblyId.new(), tool, holder=holder)
                if draft.create_assembly
                else None
            )

            def mutation(state: CamProjectSnapshot) -> CamProjectSnapshot:
                if any(item.tool_id == tool.tool_id for item in state.tool_definitions):
                    raise ValueError(f"Duplicate Tool identity: {tool.tool_id}")
                assemblies = state.tool_assemblies
                if assembly is not None:
                    if any(
                        item.assembly_id == assembly.assembly_id
                        for item in assemblies
                    ):
                        raise ValueError(
                            f"Duplicate Tool Assembly identity: {assembly.assembly_id}"
                        )
                    assemblies = (*assemblies, assembly)
                return replace(
                    state,
                    tool_definitions=(*state.tool_definitions, tool),
                    tool_assemblies=assemblies,
                )

            return self.apply(mutation)

    def update_managed_tool(
        self,
        tool_id: ToolDefinitionId,
        draft: ToolDefinitionDraft,
        *,
        expected_revision: Revision,
        expected_configuration_revision: Revision,
    ) -> CamProjectSnapshot:
        """Atomically edit a Tool and refresh its assembly dependency snapshots."""
        if not isinstance(draft, ToolDefinitionDraft):
            raise TypeError("Managed Tool update requires a typed draft")
        with self._lock:
            current = _find_tool(self._snapshot, tool_id)
            if current.revision != expected_revision:
                raise ValueError("Tool definition revision is stale")
            if current.configuration_revision != expected_configuration_revision:
                raise ValueError("Tool configuration revision is stale")
            if draft.unit is not current.unit:
                raise ValueError("Editing a Tool cannot change its persisted unit")
            changed = draft.build_tool(
                current.tool_id,
                revision=current.revision.next(),
                configuration_revision=current.configuration_revision.next(),
                common_defaults=current.common_defaults,
                program_profiles=current.program_profiles,
            )
            refreshed_assemblies = tuple(
                replace(
                    assembly,
                    expected_tool_revision=changed.revision,
                    expected_tool_fingerprint=changed.content_fingerprint,
                    expected_tool_unit=changed.unit,
                    revision=assembly.revision.next(),
                )
                if assembly.tool_id == tool_id
                else assembly
                for assembly in self._snapshot.tool_assemblies
            )
            return self.apply(
                lambda state: replace(
                    state,
                    tool_definitions=tuple(
                        changed if item.tool_id == tool_id else item
                        for item in state.tool_definitions
                    ),
                    tool_assemblies=refreshed_assemblies,
                )
            )

    def remove_managed_tool(
        self,
        tool_id: ToolDefinitionId,
        *,
        expected_revision: Revision,
        expected_configuration_revision: Revision,
    ) -> CamProjectSnapshot:
        """Hard-delete only a truly unreferenced Tool; never cascade."""
        with self._lock:
            current = _find_tool(self._snapshot, tool_id)
            if current.revision != expected_revision:
                raise ValueError("Tool definition revision is stale")
            if current.configuration_revision != expected_configuration_revision:
                raise ValueError("Tool configuration revision is stale")
            references = tuple(
                item
                for item in self._snapshot.tool_assemblies
                if item.tool_id == tool_id
            )
            if references:
                raise ValueError(
                    "Tool is referenced by persisted Tool Assemblies; delete is blocked"
                )
            return self.apply(
                lambda state: replace(
                    state,
                    tool_definitions=tuple(
                        item for item in state.tool_definitions if item.tool_id != tool_id
                    ),
                )
            )

    def save_tool_program_profile(
        self,
        preview: ToolProfileSavePreview,
        *,
        expected_configuration_revision: Revision,
        holder_fingerprint: ContentFingerprint | None = None,
    ) -> CamProjectSnapshot:
        """Confirm one preview and stale only matching strategy operations."""
        if not isinstance(preview, ToolProfileSavePreview):
            raise TypeError("Tool profile preview is invalid")
        if any(
            item.kind is ToolProfileDiffKind.INVALID for item in preview.entries
        ):
            raise ValueError("Tool profile preview contains invalid values")
        with self._lock:
            tool = _find_tool(self._snapshot, preview.tool_id)
            if tool.configuration_revision != expected_configuration_revision:
                raise ValueError("Tool configuration revision is stale")
            profile = build_profile_from_preview(
                tool,
                preview,
                holder_fingerprint=holder_fingerprint,
            )
            profiles = tuple(
                item
                for item in tool.program_profiles
                if item.profile_id != profile.profile_id
            ) + (profile,)
            changed_tool = replace(
                tool,
                program_profiles=profiles,
                configuration_revision=tool.configuration_revision.next(),
            )
            return self._replace_tool_configuration(
                tool,
                changed_tool,
                affected_strategies=(preview.strategy_id,),
            )

    def update_tool_common_defaults(
        self,
        tool_id: ToolDefinitionId,
        defaults: ToolCommonDefaults,
        *,
        expected_configuration_revision: Revision,
    ) -> CamProjectSnapshot:
        """Update shared defaults without changing physical Tool/assembly identity."""
        if not isinstance(defaults, ToolCommonDefaults):
            raise TypeError("Tool common defaults are invalid")
        with self._lock:
            tool = _find_tool(self._snapshot, tool_id)
            if tool.configuration_revision != expected_configuration_revision:
                raise ValueError("Tool configuration revision is stale")
            changed = replace(
                tool,
                common_defaults=defaults,
                configuration_revision=tool.configuration_revision.next(),
            )
            return self._replace_tool_configuration(
                tool,
                changed,
                affected_strategies=tuple(
                    item.strategy_id
                    for item in DEFAULT_TOOL_PROFILE_REGISTRY.schemas
                ),
            )

    def set_tool_program_profile_enabled(
        self,
        tool_id: ToolDefinitionId,
        profile_id: ToolProgramProfileId,
        enabled: bool,
        *,
        expected_configuration_revision: Revision,
    ) -> CamProjectSnapshot:
        """Enable/disable explicitly; enabling never resolves an ambiguous pair."""
        if type(enabled) is not bool:
            raise TypeError("Tool profile enabled state is invalid")
        with self._lock:
            tool = _find_tool(self._snapshot, tool_id)
            if tool.configuration_revision != expected_configuration_revision:
                raise ValueError("Tool configuration revision is stale")
            profile = _find_profile(tool, profile_id)
            if enabled and any(
                item.profile_id != profile_id
                and item.strategy_id == profile.strategy_id
                and item.enabled
                for item in tool.program_profiles
            ):
                raise ValueError(
                    "Only one Tool profile per strategy can be enabled implicitly"
                )
            changed_profile = replace(
                profile,
                enabled=enabled,
                updated_at=_tool_profile_timestamp(profile.updated_at),
                revision=profile.revision.next(),
            )
            changed = replace(
                tool,
                program_profiles=tuple(
                    changed_profile if item.profile_id == profile_id else item
                    for item in tool.program_profiles
                ),
                configuration_revision=tool.configuration_revision.next(),
            )
            return self._replace_tool_configuration(
                tool, changed, affected_strategies=(profile.strategy_id,)
            )

    def reset_tool_program_profile(
        self,
        tool_id: ToolDefinitionId,
        profile_id: ToolProgramProfileId,
        *,
        expected_configuration_revision: Revision,
    ) -> CamProjectSnapshot:
        """Reset one profile to an explicitly empty sparse configuration."""
        with self._lock:
            tool = _find_tool(self._snapshot, tool_id)
            if tool.configuration_revision != expected_configuration_revision:
                raise ValueError("Tool configuration revision is stale")
            profile = _find_profile(tool, profile_id)
            reset_profile = replace(
                profile,
                values=(),
                validation_state=ToolProfileValidationState.CONFIGURED,
                updated_at=_tool_profile_timestamp(profile.updated_at),
                revision=profile.revision.next(),
            )
            changed = replace(
                tool,
                program_profiles=tuple(
                    reset_profile if item.profile_id == profile_id else item
                    for item in tool.program_profiles
                ),
                configuration_revision=tool.configuration_revision.next(),
            )
            return self._replace_tool_configuration(
                tool, changed, affected_strategies=(profile.strategy_id,)
            )

    def delete_tool_program_profile(
        self,
        tool_id: ToolDefinitionId,
        profile_id: ToolProgramProfileId,
        *,
        expected_configuration_revision: Revision,
    ) -> CamProjectSnapshot:
        """Delete only the selected optional profile."""
        with self._lock:
            tool = _find_tool(self._snapshot, tool_id)
            if tool.configuration_revision != expected_configuration_revision:
                raise ValueError("Tool configuration revision is stale")
            profile = _find_profile(tool, profile_id)
            changed = replace(
                tool,
                program_profiles=tuple(
                    item
                    for item in tool.program_profiles
                    if item.profile_id != profile_id
                ),
                configuration_revision=tool.configuration_revision.next(),
            )
            return self._replace_tool_configuration(
                tool, changed, affected_strategies=(profile.strategy_id,)
            )

    def rename_tool_program_profile(
        self,
        tool_id: ToolDefinitionId,
        profile_id: ToolProgramProfileId,
        display_name: str,
        *,
        expected_configuration_revision: Revision,
    ) -> CamProjectSnapshot:
        """Rename presentation metadata without staling calculation artifacts."""
        with self._lock:
            tool = _find_tool(self._snapshot, tool_id)
            if tool.configuration_revision != expected_configuration_revision:
                raise ValueError("Tool configuration revision is stale")
            profile = _find_profile(tool, profile_id)
            changed_profile = replace(
                profile,
                display_name=display_name,
                updated_at=_tool_profile_timestamp(profile.updated_at),
                revision=profile.revision.next(),
            )
            changed = replace(
                tool,
                program_profiles=tuple(
                    changed_profile if item.profile_id == profile_id else item
                    for item in tool.program_profiles
                ),
                configuration_revision=tool.configuration_revision.next(),
            )
            return self._replace_tool_configuration(
                tool, changed, affected_strategies=()
            )

    def duplicate_tool_definition(
        self,
        tool_id: ToolDefinitionId,
        *,
        name: str | None = None,
    ) -> ToolDefinition:
        """Copy Tool and profiles with new IDs, keeping copied profiles disabled."""
        with self._lock:
            source = _find_tool(self._snapshot, tool_id)
            duplicate_id = ToolDefinitionId.new()
            base = replace(
                source,
                tool_id=duplicate_id,
                name=name or f"{source.name} — Bản sao",
                revision=Revision(0),
                program_profiles=(),
                configuration_revision=Revision(0),
            )
            profiles = tuple(
                replace(
                    duplicate_tool_program_profile(
                        item, new_tool_id=duplicate_id
                    ),
                    source_tool_revision=base.revision,
                    source_tool_fingerprint=base.content_fingerprint,
                )
                for item in source.program_profiles
            )
            duplicate = replace(base, program_profiles=profiles)
            duplicate_assemblies = tuple(
                replace(
                    assembly,
                    assembly_id=ToolAssemblyId.new(),
                    name=f"{assembly.name} — Bản sao",
                    tool_id=duplicate_id,
                    expected_tool_revision=duplicate.revision,
                    expected_tool_fingerprint=duplicate.content_fingerprint,
                    expected_tool_unit=duplicate.unit,
                    revision=Revision(0),
                )
                for assembly in self._snapshot.tool_assemblies
                if assembly.tool_id == tool_id
            )
            self._snapshot = replace(
                self._snapshot,
                tool_definitions=(*self._snapshot.tool_definitions, duplicate),
                tool_assemblies=(
                    *self._snapshot.tool_assemblies,
                    *duplicate_assemblies,
                ),
            )
            return duplicate

    def duplicate_tool_program_profile_entry(
        self,
        tool_id: ToolDefinitionId,
        profile_id: ToolProgramProfileId,
        *,
        expected_configuration_revision: Revision,
    ) -> CamProjectSnapshot:
        """Deep-copy one profile with a service-owned ID and disabled state."""
        with self._lock:
            tool = _find_tool(self._snapshot, tool_id)
            if tool.configuration_revision != expected_configuration_revision:
                raise ValueError("Tool configuration revision is stale")
            profile = _find_profile(tool, profile_id)
            duplicate = duplicate_tool_program_profile(
                profile, new_tool_id=tool.tool_id
            )
            changed = replace(
                tool,
                program_profiles=(*tool.program_profiles, duplicate),
                configuration_revision=tool.configuration_revision.next(),
            )
            return self._replace_tool_configuration(
                tool, changed, affected_strategies=(profile.strategy_id,)
            )

    def _replace_tool_configuration(
        self,
        current: ToolDefinition,
        changed: ToolDefinition,
        *,
        affected_strategies: tuple[str, ...],
    ) -> CamProjectSnapshot:
        if current.tool_id != changed.tool_id:
            raise ValueError("Tool configuration update changed Tool identity")
        snapshot = replace(
            self._snapshot,
            tool_definitions=tuple(
                changed if item.tool_id == current.tool_id else item
                for item in self._snapshot.tool_definitions
            ),
        )
        affected_ids: set[OperationId] = set()
        if current.configuration_fingerprint != changed.configuration_fingerprint:
            for strategy_id in affected_strategies:
                snapshot, current_affected = _stale_tool_strategy_operations(
                    snapshot, current.tool_id, strategy_id
                )
                affected_ids.update(current_affected)
        self._snapshot = snapshot
        for operation_id in sorted(affected_ids, key=str):
            self._simulation.mark_stale(operation_id)
            self._post.mark_stale(operation_id)
        return _clone_snapshot(self._snapshot)

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

    def begin_parallel_calculation(self, computing: Operation) -> bool:
        """Stage one worker-produced COMPUTING token if its source is still current."""
        if not isinstance(computing, Operation):
            raise TypeError("Parallel computing operation is invalid")
        with self._lock:
            current = _find_operation(self._snapshot, computing.operation_id)
            if (
                current.revision != computing.revision
                or current.parameters != computing.parameters
                or current.geometry_inputs != computing.geometry_inputs
                or current.tool_assembly != computing.tool_assembly
                or current.machine_requirement != computing.machine_requirement
                or current.enabled != computing.enabled
            ):
                return False
            self._snapshot = _replace_operation(self._snapshot, computing)
            self._post.mark_stale(computing.operation_id)
            return True

    def commit_parallel_calculation(
        self, result: ParallelFinishingComputeResult
    ) -> bool:
        """Commit only the result matching the currently staged worker token."""
        if not isinstance(result, ParallelFinishingComputeResult):
            raise TypeError("Parallel calculation result is invalid")
        with self._lock:
            current = _find_operation(self._snapshot, result.operation.operation_id)
            current_state = current.artifact_state
            result_state = result.operation.artifact_state
            if (
                current.revision != result.operation.revision
                or current_state.status is not ArtifactStatus.COMPUTING
                or current_state.generation != result_state.generation
                or current_state.input_fingerprint != result_state.input_fingerprint
            ):
                return False
            artifacts = self._snapshot.artifacts
            if result.accepted and result.metadata is not None:
                artifacts = tuple(
                    item
                    for item in artifacts
                    if item.operation_id != result.operation.operation_id
                ) + (result.metadata,)
            staged = replace(self._snapshot, artifacts=artifacts)
            self._snapshot = _replace_operation(staged, result.operation)
            self._simulation.mark_stale(result.operation.operation_id)
            self._post.mark_stale(result.operation.operation_id)
            return True

    def begin_z_level_calculation(self, computing: Operation) -> bool:
        """Stage one Z-Level COMPUTING token if its source is still current."""
        if not isinstance(computing, Operation):
            raise TypeError("Z-Level computing operation is invalid")
        with self._lock:
            current = _find_operation(self._snapshot, computing.operation_id)
            if (
                current.revision != computing.revision
                or current.parameters != computing.parameters
                or current.geometry_inputs != computing.geometry_inputs
                or current.tool_assembly != computing.tool_assembly
                or current.machine_requirement != computing.machine_requirement
                or current.enabled != computing.enabled
            ):
                return False
            self._snapshot = _replace_operation(self._snapshot, computing)
            self._post.mark_stale(computing.operation_id)
            return True

    def commit_z_level_calculation(
        self, result: ZLevelFinishingComputeResult
    ) -> bool:
        """Commit only a Z-Level result matching the staged worker token."""
        if not isinstance(result, ZLevelFinishingComputeResult):
            raise TypeError("Z-Level calculation result is invalid")
        with self._lock:
            current = _find_operation(self._snapshot, result.operation.operation_id)
            current_state = current.artifact_state
            result_state = result.operation.artifact_state
            if (
                current.revision != result.operation.revision
                or current_state.status is not ArtifactStatus.COMPUTING
                or current_state.generation != result_state.generation
                or current_state.input_fingerprint != result_state.input_fingerprint
            ):
                return False
            artifacts = self._snapshot.artifacts
            if result.accepted and result.metadata is not None:
                artifacts = tuple(
                    item
                    for item in artifacts
                    if item.operation_id != result.operation.operation_id
                ) + (result.metadata,)
            staged = replace(self._snapshot, artifacts=artifacts)
            self._snapshot = _replace_operation(staged, result.operation)
            self._simulation.mark_stale(result.operation.operation_id)
            self._post.mark_stale(result.operation.operation_id)
            return True

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

    def calculation_timing(self, operation_id: OperationId) -> CalculationTiming | None:
        """Return the last production calculation timing snapshot, if any."""
        with self._lock:
            return self._calculation_timings.get(operation_id)

    def recover_calculation_cache(self, project_root: Path) -> int:
        """Remove abandoned atomic scratch without touching reusable artifacts."""
        with self._lock:
            return self._calculation_cache.recover_abandoned_scratch(project_root)

    def cleanup_calculation_cache(
        self,
        project_root: Path,
        *,
        max_bytes: int,
        max_age_seconds: float | None = None,
    ) -> int:
        """Apply quota/age cleanup while preserving live operation references."""
        with self._lock:
            live = frozenset(
                operation.operation_id.value.hex
                for job in self._snapshot.jobs
                for setup in job.setups
                for operation in setup.operation_tree.operations
            )
            return self._calculation_cache.cleanup(
                project_root,
                max_bytes=max_bytes,
                max_age_seconds=max_age_seconds,
                live_operation_ids=live,
            )

    def release_calculation_cache_references(
        self, project_root: Path, operation_id: OperationId
    ) -> int:
        """Release one deleted operation from shared calculation artifacts."""
        if not isinstance(operation_id, OperationId):
            raise TypeError("CAM operation identity is invalid")
        with self._lock:
            return self._calculation_cache.release_operation_references(
                project_root, operation_id.value.hex
            )

    def _optimization_cache_hit(
        self,
        project_root: Path,
        operation: Operation,
        input_fingerprint: DependencyFingerprint,
    ) -> ToolpathArtifact | None:
        """Load a final artifact only when all provenance checks pass."""
        lookup = self._calculation_cache.lookup(
            project_root,
            operation_id=operation.operation_id.value.hex,
            phase="final_assembly",
            fingerprint=input_fingerprint.digest,
        )
        if lookup.status is not CacheLookupStatus.HIT or lookup.payload is None:
            return None
        try:
            artifact = artifact_from_dict(json.loads(lookup.payload.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if (
            artifact.source_operation_id != operation.operation_id
            or artifact.operation_revision != operation.revision
            or artifact.input_fingerprint != input_fingerprint
            or artifact.artifact_fingerprint is None
        ):
            return None
        return artifact

    def _publish_optimization_cache(self, project_root: Path, artifact: ToolpathArtifact) -> None:
        """Publish a complete final artifact after normal Toolpath validation."""
        if artifact.artifact_fingerprint is None:
            return
        try:
            payload = json.dumps(
                artifact_to_dict(artifact), ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            self._calculation_cache.publish(
                project_root,
                operation_id=artifact.source_operation_id.value.hex,
                phase="final_assembly",
                fingerprint=artifact.input_fingerprint.digest,
                payload=payload,
                artifact_type="toolpath.final",
            )
        except (OSError, TypeError, ValueError) as error:
            # The correctness path remains the existing ToolpathArtifactStore;
            # optional optimization cache failure must never reject a toolpath.
            logger.debug("R247 calculation cache publish skipped: %s", error, exc_info=True)
            return

    def _restore_optimization_hit(
        self,
        operation: Operation,
        artifact: ToolpathArtifact,
    ) -> Operation:
        state = replace(
            operation.artifact_state,
            status=ArtifactStatus.VALID,
            generation=artifact.computation_token.generation,
            token=None,
            input_fingerprint=artifact.input_fingerprint,
            artifact_fingerprint=artifact.artifact_fingerprint,
            dirty_reasons=(),
            diagnostics=(),
        )
        return replace(operation, artifact_state=state)

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
        cancellation: Callable[[], bool] | None = None,
        progress: Callable[[CamCalculationProgress], None] | None = None,
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
            operation_key = operation.operation_id.value.hex
            geometry_started_ns = monotonic_ns()
            _report_calculation_progress(progress, CamCalculationProgress(
                operation_key, "facing", "geometry", CamPhaseState.RUNNING, 0.0
            ))
            try:
                try:
                    parameters = FacingParameters.from_operation_parameters(operation.parameters)
                except (TypeError, ValueError) as error:
                    raise FacingGenerationError(
                        DiagnosticCode.FACING_INVALID_PARAMETERS, str(error)
                    ) from error
                resolved_face = None
                geometry_phase_status = "BYPASS_CACHE"
                geometry_phase_fingerprint = None
                if parameters.boundary_source is FacingBoundarySource.PLANAR_FACE:
                    if len(operation.geometry_inputs) != 1:
                        raise FacingGenerationError(
                            DiagnosticCode.FACING_FACE_REFERENCE_MISSING,
                            "Planar Facing requires one resolvable persistent FACE reference.",
                        )
                    reference = operation.geometry_inputs[0].reference
                    geometry_phase_fingerprint = ContentFingerprint.from_payload({
                        "format": "HMS_R249_FACING_GEOMETRY_INPUT",
                        "format_version": 1,
                        "reference": reference.to_dict(),
                        "wcs": setup.wcs.to_dict(),
                        "algorithm_version": "facing.region.v1",
                        "precision_policy": {"planarity": 1.0e-8},
                    }).digest
                    loaded_phase = self._calculation_cache.lookup_shared(
                        project_root, phase="geometry", fingerprint=geometry_phase_fingerprint,
                        algorithm_version="facing.geometry.v1",
                    )
                    allow_geometry_reuse = (
                        face_resolver is None
                        or operation.artifact_state.status is not ArtifactStatus.VALID
                    )
                    if (allow_geometry_reuse and loaded_phase.status is CacheLookupStatus.HIT
                            and loaded_phase.payload is not None):
                        try:
                            resolved_face = facing_region_from_dict(
                                json.loads(loaded_phase.payload.decode("utf-8"))
                            )
                            self._calculation_cache.retain_shared_reference(
                                project_root, phase="geometry",
                                fingerprint=geometry_phase_fingerprint,
                                operation_id=operation.operation_id.value.hex,
                            )
                            geometry_phase_status = "CACHE_HIT"
                        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                            resolved_face = None
                    if resolved_face is None:
                        geometry_phase_status = loaded_phase.status.value
                        if face_resolver is None:
                            raise FacingGenerationError(
                                DiagnosticCode.FACING_FACE_REFERENCE_MISSING,
                                "Planar Facing requires a resolver when geometry cache is unavailable.",
                            )
                        try:
                            resolved_face = face_resolver(reference)
                        except (RuntimeError, TypeError, ValueError) as error:
                            raise FacingGenerationError(
                                DiagnosticCode.FACING_GEOMETRY_RESOLUTION_FAILED,
                                str(error) or "Planar FACE resolution failed.",
                            ) from error
                inputs = generator.resolve_inputs(operation, setup, assembly=assembly, tool=tool,
                                                  machine=machine, resolved_face=resolved_face)
                geometry_elapsed_ns = monotonic_ns() - geometry_started_ns
                _report_calculation_progress(progress, CamCalculationProgress(
                    operation_key, "facing", "geometry", CamPhaseState.COMPLETE, 45.0,
                    geometry_elapsed_ns, geometry_phase_status,
                ))
                if cancellation is not None and cancellation():
                    _report_calculation_progress(progress, CamCalculationProgress(
                        operation_key, "facing", "final_assembly", CamPhaseState.CANCELLED,
                        45.0, 0, "CANCELLED",
                    ))
                    raise FacingGenerationError(
                        DiagnosticCode.FACING_GENERATION_FAILED,
                        "Facing calculation cancelled after a safe geometry boundary.",
                    )
                if geometry_phase_fingerprint is not None and geometry_phase_status != "CACHE_HIT":
                    try:
                        phase_payload = json.dumps(
                            facing_region_to_dict(inputs.region), ensure_ascii=False, allow_nan=False,
                            sort_keys=True, separators=(",", ":"),
                        ).encode("utf-8")
                        self._calculation_cache.publish_shared(
                            project_root, phase="geometry",
                            fingerprint=geometry_phase_fingerprint, payload=phase_payload,
                            algorithm_version="facing.geometry.v1",
                            operation_references=(operation.operation_id.value.hex,),
                        )
                    except (OSError, TypeError, ValueError):
                        logger.debug("R249 Facing geometry phase publish skipped", exc_info=True)
                started_ns = monotonic_ns()
                _report_calculation_progress(progress, CamCalculationProgress(
                    operation_key, "facing", "final_assembly", CamPhaseState.RUNNING, 55.0
                ))
                cached = self._optimization_cache_hit(
                    project_root, operation, inputs.input_fingerprint
                )
                if cached is not None:
                    metadata = self._artifact_store.publish(project_root, cached)
                    restored = self._restore_optimization_hit(operation, cached)
                    staged = replace(
                        self._snapshot,
                        artifacts=tuple(item for item in self._snapshot.artifacts if item.operation_id != operation_id) + (metadata,),
                    )
                    try:
                        self._snapshot = _replace_operation(staged, restored)
                    except CamChildNotFoundError:
                        # A concurrently refreshed project snapshot may already
                        # have removed the operation; the verified artifact is
                        # still returned without mutating unrelated state.
                        self._snapshot = staged
                    self._calculation_timings[operation_id] = CalculationTiming(
                        str(operation_id), (
                            PhaseTiming("geometry", 0, geometry_phase_status),
                            PhaseTiming("final_assembly", monotonic_ns() - started_ns, "CACHE_HIT"),
                        )
                    )
                    _report_calculation_progress(progress, CamCalculationProgress(
                        operation_key, "facing", "final_assembly", CamPhaseState.COMPLETE,
                        100.0, monotonic_ns() - started_ns, "CACHE_HIT",
                    ))
                    return FacingComputeResult(restored, cached, True)
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
                self._publish_optimization_cache(project_root, publish.artifact)
                self._calculation_timings[operation_id] = CalculationTiming(
                    str(operation_id), (
                        PhaseTiming("geometry", 0, geometry_phase_status),
                        PhaseTiming("final_assembly", monotonic_ns() - started_ns, "CACHE_MISS"),
                    )
                )
                _report_calculation_progress(progress, CamCalculationProgress(
                    operation_key, "facing", "final_assembly", CamPhaseState.COMPLETE,
                    100.0, monotonic_ns() - started_ns, "CACHE_MISS",
                ))
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
        cancellation: Callable[[], bool] | None = None,
        progress: Callable[[CamCalculationProgress], None] | None = None,
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
            operation_key = operation.operation_id.value.hex
            geometry_started_ns = monotonic_ns()
            _report_calculation_progress(progress, CamCalculationProgress(
                operation_key, "contour", "contour_geometry", CamPhaseState.RUNNING, 0.0
            ))
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
                contour_phase_status = "CACHE_MISS"

                def contour_geometry_provider(descriptor, current_setup, parameters, diameter):
                    nonlocal contour_phase_status
                    fingerprint = ContentFingerprint.from_payload({
                        "format": "HMS_R250_CONTOUR_GEOMETRY_INPUT",
                        "format_version": 1,
                        "geometry": descriptor.geometry_fingerprint.to_dict(),
                        "reference": descriptor.reference.to_dict(),
                        "wcs": current_setup.wcs.to_dict(),
                        "side": parameters.side.value,
                        "direction": parameters.direction.value,
                        "radial_stock_allowance": parameters.radial_stock_allowance.value,
                        "tool_diameter": diameter,
                        "precision_policy": {"tolerance": 1.0e-8},
                        "algorithm_version": "contour.geometry.v1",
                    }).digest
                    loaded = self._calculation_cache.lookup_shared(
                        project_root, phase="contour_geometry", fingerprint=fingerprint,
                        algorithm_version="contour.geometry.v1",
                    )
                    if loaded.status is CacheLookupStatus.HIT and loaded.payload is not None:
                        try:
                            decoded = contour_geometry_from_dict(
                                json.loads(loaded.payload.decode("utf-8"))
                            )
                            self._calculation_cache.retain_shared_reference(
                                project_root, phase="contour_geometry",
                                fingerprint=fingerprint,
                                operation_id=operation.operation_id.value.hex,
                            )
                            contour_phase_status = "CACHE_HIT"
                            return ContourPath(decoded[0], decoded[1]), decoded[2], decoded[3]
                        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                            pass
                    result = prepare_contour_machining_geometry(
                        descriptor, current_setup, parameters, diameter
                    )
                    contour_phase_status = loaded.status.value
                    try:
                        payload = json.dumps(
                            contour_geometry_to_dict(
                                result[0].loop, result[0].source_fingerprint,
                                result[1], result[2],
                            ), ensure_ascii=False,
                            allow_nan=False, sort_keys=True, separators=(",", ":"),
                        ).encode("utf-8")
                        self._calculation_cache.publish_shared(
                            project_root, phase="contour_geometry",
                            fingerprint=fingerprint, payload=payload,
                            algorithm_version="contour.geometry.v1",
                            operation_references=(operation.operation_id.value.hex,),
                        )
                    except (OSError, TypeError, ValueError):
                        logger.debug("R250 Contour phase publish skipped", exc_info=True)
                    return result

                inputs = generator.resolve_inputs(operation, setup, assembly=assembly, tool=tool,
                                                  machine=machine, resolved_profile=resolved,
                                                  geometry_provider=contour_geometry_provider)
                geometry_elapsed_ns = monotonic_ns() - geometry_started_ns
                _report_calculation_progress(progress, CamCalculationProgress(
                    operation_key, "contour", "contour_geometry", CamPhaseState.COMPLETE,
                    45.0, geometry_elapsed_ns, contour_phase_status,
                ))
                if cancellation is not None and cancellation():
                    _report_calculation_progress(progress, CamCalculationProgress(
                        operation_key, "contour", "final_assembly", CamPhaseState.CANCELLED,
                        45.0, 0, "CANCELLED",
                    ))
                    raise ContourGenerationError(
                        DiagnosticCode.CONTOUR_GENERATION_FAILED,
                        "Contour calculation cancelled after a safe geometry boundary.",
                    )
                started_ns = monotonic_ns()
                _report_calculation_progress(progress, CamCalculationProgress(
                    operation_key, "contour", "final_assembly", CamPhaseState.RUNNING, 55.0
                ))
                cached = self._optimization_cache_hit(project_root, operation, inputs.input_fingerprint)
                if cached is not None:
                    metadata = self._artifact_store.publish(project_root, cached)
                    restored = self._restore_optimization_hit(operation, cached)
                    staged = replace(
                        self._snapshot,
                        artifacts=tuple(item for item in self._snapshot.artifacts if item.operation_id != operation_id) + (metadata,),
                    )
                    try:
                        self._snapshot = _replace_operation(staged, restored)
                    except CamChildNotFoundError:
                        self._snapshot = staged
                    self._calculation_timings[operation_id] = CalculationTiming(
                        str(operation_id), (
                            PhaseTiming("contour_geometry", 0, contour_phase_status),
                            PhaseTiming("final_assembly", monotonic_ns() - started_ns, "CACHE_HIT"),
                        )
                    )
                    _report_calculation_progress(progress, CamCalculationProgress(
                        operation_key, "contour", "final_assembly", CamPhaseState.COMPLETE,
                        100.0, monotonic_ns() - started_ns, "CACHE_HIT",
                    ))
                    return ContourComputeResult(restored, cached, True)
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
                self._publish_optimization_cache(project_root, publish.artifact)
                self._calculation_timings[operation_id] = CalculationTiming(
                    str(operation_id), (
                        PhaseTiming("contour_geometry", 0, contour_phase_status),
                        PhaseTiming("final_assembly", monotonic_ns() - started_ns, "CACHE_MISS"),
                    )
                )
                _report_calculation_progress(progress, CamCalculationProgress(
                    operation_key, "contour", "final_assembly", CamPhaseState.COMPLETE,
                    100.0, monotonic_ns() - started_ns, "CACHE_MISS",
                ))
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
        cancellation: Callable[[], bool] | None = None,
        progress: Callable[[CamCalculationProgress], None] | None = None,
    ) -> PocketComputeResult:
        """Synchronously compute/publish Pocket with the shared stale-token contract."""
        with self._lock:
            selected = _find_operation(self._snapshot, operation_id)
            if selected.strategy_key == "rest_pocket_3axis":
                return self.compute_rest_pocket(
                    project_root, operation_id, geometry_resolver=geometry_resolver,
                    cancellation=cancellation, progress=progress,
                )
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
            operation_key = operation.operation_id.value.hex
            geometry_started_ns = monotonic_ns()
            _report_calculation_progress(progress, CamCalculationProgress(
                operation_key, "pocket", "pocket_geometry", CamPhaseState.RUNNING, 0.0
            ))
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
                pocket_phase_status = "CACHE_MISS"

                def pocket_geometry_provider(region, current_setup, diameter, allowance,
                                             stepover, tolerance, direction):
                    nonlocal pocket_phase_status
                    fingerprint = ContentFingerprint.from_payload({
                        "format": "HMS_R250_POCKET_GEOMETRY_INPUT",
                        "format_version": 1,
                        "region": region.fingerprint.to_dict(),
                        "wcs": current_setup.wcs.to_dict(),
                        "tool_diameter": diameter,
                        "radial_stock_allowance": allowance,
                        "stepover": stepover,
                        "tolerance": tolerance,
                        "direction": direction.value,
                        "algorithm_version": "pocket.geometry.v1",
                    }).digest
                    loaded = self._calculation_cache.lookup_shared(
                        project_root, phase="pocket_geometry", fingerprint=fingerprint,
                        algorithm_version="pocket.geometry.v1",
                    )
                    if loaded.status is CacheLookupStatus.HIT and loaded.payload is not None:
                        try:
                            decoded = pocket_geometry_from_dict(
                                json.loads(loaded.payload.decode("utf-8"))
                            )
                            self._calculation_cache.retain_shared_reference(
                                project_root, phase="pocket_geometry",
                                fingerprint=fingerprint,
                                operation_id=operation.operation_id.value.hex,
                            )
                            pocket_phase_status = "CACHE_HIT"
                            return ContourPath(decoded[0], decoded[1]), decoded[2]
                        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                            pass
                    checkpoint = self._checkpoint_store.load(
                        project_root, operation.operation_id.value.hex,
                        "pocket_geometry", fingerprint,
                    )
                    if checkpoint is not None:
                        try:
                            decoded = pocket_geometry_from_dict(
                                json.loads(checkpoint[1].decode("utf-8"))
                            )
                            pocket_phase_status = "CHECKPOINT_HIT"
                            return ContourPath(decoded[0], decoded[1]), decoded[2]
                        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                            pass
                    result = prepare_pocket_machining_geometry(
                        region, current_setup, tool_diameter=diameter,
                        radial_stock_allowance=allowance, stepover=stepover,
                        tolerance=tolerance, cutting_direction=direction,
                    )
                    pocket_phase_status = loaded.status.value
                    try:
                        payload = json.dumps(
                            pocket_geometry_to_dict(
                                result[0].loop, result[0].source_fingerprint, result[1]
                            ), ensure_ascii=False,
                            allow_nan=False, sort_keys=True, separators=(",", ":"),
                        ).encode("utf-8")
                        self._calculation_cache.publish_shared(
                            project_root, phase="pocket_geometry",
                            fingerprint=fingerprint, payload=payload,
                            algorithm_version="pocket.geometry.v1",
                            operation_references=(operation.operation_id.value.hex,),
                        )
                        self._checkpoint_store.publish(
                            project_root, operation.operation_id.value.hex,
                            "pocket_geometry", fingerprint, payload,
                        )
                    except (OSError, TypeError, ValueError):
                        logger.debug("R250 Pocket phase publish skipped", exc_info=True)
                    return result

                inputs = generator.resolve_inputs(operation, setup, assembly=assembly, tool=tool,
                                                  machine=machine, resolved_geometry=resolved,
                                                  geometry_provider=pocket_geometry_provider)
                geometry_elapsed_ns = monotonic_ns() - geometry_started_ns
                _report_calculation_progress(progress, CamCalculationProgress(
                    operation_key, "pocket", "pocket_geometry", CamPhaseState.COMPLETE,
                    45.0, geometry_elapsed_ns, pocket_phase_status,
                ))
                if cancellation is not None and cancellation():
                    self._calculation_timings[operation_id] = CalculationTiming(
                        str(operation_id), (
                            PhaseTiming("pocket_geometry", 0, pocket_phase_status),
                            PhaseTiming("final_assembly", 0, "CANCELLED"),
                        )
                    )
                    _report_calculation_progress(progress, CamCalculationProgress(
                        operation_key, "pocket", "final_assembly", CamPhaseState.CANCELLED,
                        45.0, 0, "CANCELLED",
                    ))
                    raise PocketGenerationError(
                        DiagnosticCode.POCKET_GENERATION_FAILED,
                        "Pocket calculation cancelled after a complete geometry checkpoint.",
                    )
                started_ns = monotonic_ns()
                _report_calculation_progress(progress, CamCalculationProgress(
                    operation_key, "pocket", "final_assembly", CamPhaseState.RUNNING, 55.0
                ))
                final_cache_eligible = (
                    len(inputs.depth_levels)
                    * sum(len(loop.segments) + 3 for loop in inputs.offset_loops)
                    + 4
                    <= _POCKET_FINAL_CACHE_MAX_EVENT_ESTIMATE
                )
                template = self._pocket_incremental_templates.get(operation_id)
                memory_cached = (
                    template[2]
                    if (
                        template is not None
                        and template[2].source_operation_id == operation.operation_id
                        and template[2].operation_revision == operation.revision
                        and template[2].input_fingerprint == inputs.input_fingerprint
                    )
                    else None
                )
                if memory_cached is not None:
                    self._pocket_incremental_templates.move_to_end(operation_id)
                cached = (
                    memory_cached
                    or self._optimization_cache_hit(
                        project_root, operation, inputs.input_fingerprint
                    )
                    if final_cache_eligible
                    else None
                )
                if cached is not None:
                    self._remember_pocket_incremental_template(operation_id, (
                        pocket_feed_independent_fingerprint(inputs),
                        inputs.strategy.fingerprint,
                        cached,
                    ))
                    metadata = self._artifact_store.publish(project_root, cached)
                    restored = self._restore_optimization_hit(operation, cached)
                    staged = replace(
                        self._snapshot,
                        artifacts=tuple(item for item in self._snapshot.artifacts if item.operation_id != operation_id) + (metadata,),
                    )
                    try:
                        self._snapshot = _replace_operation(staged, restored)
                    except CamChildNotFoundError:
                        self._snapshot = staged
                    self._calculation_timings[operation_id] = CalculationTiming(
                        str(operation_id), (
                            PhaseTiming("pocket_geometry", 0, pocket_phase_status),
                            PhaseTiming("final_assembly", monotonic_ns() - started_ns, "CACHE_HIT"),
                        )
                    )
                    _report_calculation_progress(progress, CamCalculationProgress(
                        operation_key, "pocket", "final_assembly", CamPhaseState.COMPLETE,
                        100.0, monotonic_ns() - started_ns, "CACHE_HIT",
                    ))
                    return PocketComputeResult(restored, cached, True)
                if operation.artifact_state.status is ArtifactStatus.VALID:
                    operation = replace(operation, artifact_state=operation.artifact_state.mark_dirty(
                        DirtyReason.PARAMETERS_CHANGED))
                    inputs = replace(inputs, operation=operation)
                    self._snapshot = _replace_operation(self._snapshot, operation)
                computing, token = generator.begin(inputs)
                self._snapshot = _replace_operation(self._snapshot, computing.operation)
                template = self._pocket_incremental_templates.get(operation_id)
                if template is not None:
                    self._pocket_incremental_templates.move_to_end(operation_id)
                incremental_hit = (
                    template is not None
                    and template[0] == pocket_feed_independent_fingerprint(computing)
                    and template[1] != computing.strategy.fingerprint
                    and template[2].input_fingerprint != computing.input_fingerprint
                )
                candidate = (
                    generator.regenerate_feed_only(computing, template[2])
                    if incremental_hit and template is not None
                    else generator.generate(computing)
                )
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
                if final_cache_eligible:
                    self._publish_optimization_cache(project_root, publish.artifact)
                final_cache_status = (
                    "INCREMENTAL_HIT"
                    if incremental_hit
                    else ("CACHE_MISS" if final_cache_eligible else "BYPASS_CACHE")
                )
                self._calculation_timings[operation_id] = CalculationTiming(
                    str(operation_id), (
                        PhaseTiming("pocket_geometry", 0, pocket_phase_status),
                        PhaseTiming(
                            "final_assembly", monotonic_ns() - started_ns,
                            final_cache_status,
                        ),
                    )
                )
                _report_calculation_progress(progress, CamCalculationProgress(
                    operation_key, "pocket", "final_assembly", CamPhaseState.COMPLETE,
                    100.0, monotonic_ns() - started_ns, final_cache_status,
                ))
                artifacts = tuple(item for item in self._snapshot.artifacts
                                  if item.operation_id != operation_id)
                staged = replace(self._snapshot, artifacts=(*artifacts, metadata))
                self._snapshot = _replace_operation(staged, publish.operation)
                self._remember_pocket_incremental_template(operation_id, (
                    pocket_feed_independent_fingerprint(inputs),
                    inputs.strategy.fingerprint,
                    publish.artifact,
                ))
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

    def compute_rest_pocket(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        geometry_resolver: Callable[[GeometryReference], ResolvedPocketGeometry] | None = None,
        cancellation: Callable[[], bool] | None = None,
        progress: Callable[[CamCalculationProgress], None] | None = None,
    ) -> PocketComputeResult:
        """Compute Rest Pocket from the newest compatible published upstream artifact."""
        with self._lock:
            phases: list[PhaseTiming] = []
            before = _clone_snapshot(self._snapshot)
            operation = _find_operation(self._snapshot, operation_id)
            setup = next(setup for job in self._snapshot.jobs for setup in job.setups if setup.setup_id == operation.setup_id)
            assembly = next((item for item in self._snapshot.tool_assemblies if item.assembly_id == operation.tool_assembly.assembly_id), None)
            tool = None if assembly is None else next((item for item in self._snapshot.tool_definitions if item.tool_id == assembly.tool_id), None)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            machine = next((item for item in self._snapshot.machine_definitions if item.machine_id == machine_id), None)
            generator = RestPocketGenerator()
            try:
                if geometry_resolver is None or not operation.geometry_inputs:
                    raise PocketGenerationError(DiagnosticCode.POCKET_PROFILE_MISSING, "Rest Pocket requires a resolvable boundary.")
                resolved = geometry_resolver(operation.geometry_inputs[0].reference)
                setup_fp = material_state_setup_fingerprint(setup)
                material_started_ns = monotonic_ns()
                persisted = self.resolve_persisted_material_state(project_root, operation_id)
                if persisted.status.value in {"RESOLVED", "NO_REST_MATERIAL"} and persisted.state is not None:
                    parent = persisted.state
                    producer = _find_operation(self._snapshot, persisted.producer_operation_id)
                    phases.append(PhaseTiming(
                        "material_state_load",
                        monotonic_ns() - material_started_ns,
                        "CACHE_HIT",
                    ))
                    if operation.artifact_state.status is ArtifactStatus.VALID:
                        metadata = next(
                            (
                                item
                                for item in self._snapshot.artifacts
                                if item.operation_id == operation_id
                            ),
                            None,
                        )
                        if metadata is not None:
                            artifact = self._artifact_store.load(project_root, metadata)
                            phases.extend((
                                PhaseTiming("rest_region_extraction", 0, "CACHE_HIT"),
                                PhaseTiming("cut_generation", 0, "CACHE_HIT"),
                                PhaseTiming("final_assembly", 0, "CACHE_HIT"),
                                PhaseTiming("material_state_update", 0, "CACHE_HIT"),
                            ))
                            self._calculation_timings[operation_id] = CalculationTiming(
                                str(operation_id), tuple(phases)
                            )
                            return PocketComputeResult(
                                operation,
                                artifact,
                                True,
                                no_rest_material=not artifact.events,
                            )
                else:
                    producers = []
                    for job in self._snapshot.jobs:
                        for candidate_setup in job.setups:
                            if candidate_setup.setup_id != setup.setup_id or candidate_setup.operation_tree is None:
                                continue
                            for candidate in candidate_setup.operation_tree.operations:
                                if candidate.operation_id == operation_id:
                                    break
                                metadata = next((item for item in self._snapshot.artifacts if item.operation_id == candidate.operation_id), None)
                                if metadata is not None:
                                    artifact = self._artifact_store.load(project_root, metadata)
                                    candidate_assembly = next((item for item in self._snapshot.tool_assemblies if item.assembly_id == candidate.tool_assembly.assembly_id), None)
                                    candidate_tool = None
                                    if (candidate_assembly is not None
                                            and candidate.artifact_state.status is ArtifactStatus.VALID):
                                        candidate_tool = next((item for item in self._snapshot.tool_definitions if item.tool_id == candidate_assembly.tool_id), None)
                                    if candidate_tool is not None:
                                        producers.append((candidate, artifact, candidate_tool))
                    if not producers:
                        raise PocketGenerationError(DiagnosticCode.UPSTREAM_INVALID, "No compatible upstream published toolpath exists.")
                    if len(producers) > 1:
                        raise PocketGenerationError(DiagnosticCode.UPSTREAM_INVALID, "Multiple compatible upstream material producers require explicit provenance.")
                    producer, upstream_artifact, upstream_tool = producers[0]
                    parent_result = calculate_material_state(
                        stock=setup.stock, artifact=upstream_artifact, tool=upstream_tool,
                        setup_fingerprint=setup_fp,
                        cancellation=cancellation,
                    )
                    parent = parent_result.state
                    self._material_states.write(project_root, parent)
                    phases.append(PhaseTiming(
                        "material_state_load",
                        monotonic_ns() - material_started_ns,
                        "CACHE_MISS",
                    ))
                dependency = MaterialStateDependency(
                    operation_id, producer.operation_id, parent.fingerprint,
                    parent.toolpath_fingerprint, setup_fp,
                    ContentFingerprint.from_payload(setup.stock.to_dict()),
                    parent.engine_version, parent.precision.to_dict(),
                )
                staged_dependency = replace(
                    self._snapshot,
                    material_state_dependencies=tuple(
                        item
                        for item in self._snapshot.material_state_dependencies
                        if item.consumer_operation_id != operation_id
                    ) + (dependency,),
                )
                current_setup = next(
                    candidate
                    for job in staged_dependency.jobs
                    for candidate in job.setups
                    if candidate.setup_id == setup.setup_id
                )
                edge = DependencyEdge.material_state(
                    producer.operation_id, operation_id
                )
                if edge not in current_setup.operation_tree.dependency_graph.edges:
                    updated_graph = (
                        current_setup.operation_tree.dependency_graph.with_edge_added(edge)
                    )
                    updated_tree = OperationTree(
                        current_setup.operation_tree.setup_id,
                        current_setup.operation_tree.root_id,
                        current_setup.operation_tree.nodes,
                        current_setup.operation_tree.operations,
                        updated_graph,
                        current_setup.operation_tree.revision.next(),
                    )
                    updated_setup = replace(
                        current_setup,
                        operation_tree=updated_tree,
                        revision=current_setup.revision.next(),
                    )
                    updated_jobs = tuple(
                        CamJob(
                            job.job_id,
                            job.name,
                            revision=job.revision,
                            setups=tuple(
                                updated_setup
                                if candidate.setup_id == current_setup.setup_id
                                else candidate
                                for candidate in job.setups
                            ),
                            active_setup_id=job.active_setup_id,
                        )
                        if any(
                            candidate.setup_id == current_setup.setup_id
                            for candidate in job.setups
                        )
                        else job
                        for job in staged_dependency.jobs
                    )
                    staged_dependency = replace(
                        staged_dependency, jobs=updated_jobs
                    )
                self._snapshot = staged_dependency
                # Adding the first MATERIAL_STATE edge advances the owning Setup
                # revision.  Rest publication and optional Simulation capture must
                # therefore use that current revision, not the pre-edge snapshot
                # captured at method entry.
                setup = next(
                    candidate
                    for job in self._snapshot.jobs
                    for candidate in job.setups
                    if candidate.setup_id == setup.setup_id
                )
                if cancellation is not None and cancellation():
                    phases.append(PhaseTiming(
                        "rest_region_extraction", 0, "CANCELLED"
                    ))
                    self._calculation_timings[operation_id] = CalculationTiming(
                        str(operation_id), tuple(phases)
                    )
                    diagnostic = ValidationDiagnostic(
                        DiagnosticSeverity.ERROR,
                        DiagnosticCode.POCKET_GENERATION_FAILED,
                        "Rest Pocket calculation cancelled",
                    )
                    return PocketComputeResult(
                        _find_operation(self._snapshot, operation_id),
                        None,
                        False,
                        (diagnostic,),
                    )
                region_started_ns = monotonic_ns()
                rest_template = self._rest_incremental_templates.get(operation_id)
                inputs = generator.resolve_inputs(
                    operation, setup, assembly=assembly, tool=tool, machine=machine,
                    resolved_geometry=resolved, parent_state=parent,
                    cached_template=(
                        rest_template[1] if rest_template is not None else None
                    ),
                )
                lead_incremental = (
                    rest_template is not None
                    and rest_template[0]
                    == pocket_lead_independent_fingerprint(inputs.pocket)
                    and rest_template[1].pocket.strategy.lead_in_length
                    != inputs.pocket.strategy.lead_in_length
                )
                phases.append(PhaseTiming(
                    "rest_region_extraction",
                    monotonic_ns() - region_started_ns,
                    "CACHE_HIT" if lead_incremental else "CACHE_MISS",
                ))
                computing, token = generator.begin(inputs)
                self._snapshot = _replace_operation(self._snapshot, computing.pocket.operation)
                cut_started_ns = monotonic_ns()
                candidate = (
                    generator.regenerate_lead_only(computing)
                    if lead_incremental
                    else generator.generate(computing)
                )
                phases.append(PhaseTiming(
                    "cut_generation",
                    monotonic_ns() - cut_started_ns,
                    "CACHE_HIT" if lead_incremental else "CACHE_MISS",
                ))
                if lead_incremental:
                    phases.append(PhaseTiming("leads", 0, "CACHE_MISS"))
                current = _find_operation(self._snapshot, operation_id)
                published = publish_toolpath(current, candidate, token, computing.pocket.input_fingerprint)
                if not published.accepted or published.artifact is None:
                    self._snapshot = _replace_operation(self._snapshot, published.operation)
                    return PocketComputeResult(published.operation, None, False)
                final_started_ns = monotonic_ns()
                metadata = self._artifact_store.publish(project_root, published.artifact)
                phases.append(PhaseTiming(
                    "final_assembly", monotonic_ns() - final_started_ns, "CACHE_MISS"
                ))
                update_started_ns = monotonic_ns()
                successor = calculate_material_state(
                    stock=setup.stock, artifact=published.artifact, tool=tool, parent=parent,
                    setup_fingerprint=setup_fp,
                    cancellation=cancellation,
                )
                self._material_states.write(project_root, successor.state)
                phases.append(PhaseTiming(
                    "material_state_update",
                    monotonic_ns() - update_started_ns,
                    "CACHE_MISS",
                ))
                staged = replace(self._snapshot,
                    artifacts=tuple(item for item in self._snapshot.artifacts if item.operation_id != operation_id) + (metadata,))
                self._snapshot = _replace_operation(staged, published.operation)
                self._rest_incremental_templates[operation_id] = (
                    pocket_lead_independent_fingerprint(inputs.pocket), inputs,
                )
                self._calculation_timings[operation_id] = CalculationTiming(
                    str(operation_id), tuple(phases)
                )
                return PocketComputeResult(
                    published.operation,
                    published.artifact,
                    True,
                    no_rest_material=inputs.no_rest_material,
                )
            except (PocketGenerationError, ToolpathArtifactStoreError, OSError, CamValidationError) as error:
                self._snapshot = before
                diagnostic = error.diagnostic if isinstance(error, PocketGenerationError) else ValidationDiagnostic(DiagnosticSeverity.ERROR, DiagnosticCode.POCKET_GENERATION_FAILED, str(error))
                return PocketComputeResult(operation, None, False, (diagnostic,))

    def create_rest_contour_operation(
        self,
        job_id: CamJobId,
        setup_id: SetupId,
        parent_node_id: CamNodeId,
        *,
        operation_id: OperationId,
        node_id: CamNodeId,
        name: str,
        parameters,
        profile,
        dependency_operation_id: OperationId,
        tool_assembly_id: ToolAssemblyId,
        machine_requirement=None,
        profile_resolver=None,
        quality_profile=None,
        manual_overrides=None,
    ) -> CamProjectSnapshot:
        """Create Rest Contour through its registered aggregate factory.

        The explicit producer ID becomes a typed DAG edge in the same snapshot
        mutation.  No creation-order fallback or caller supplied MaterialState
        is accepted.
        """
        from hms_cadcam.cam.domain.dependency import DependencyEdge
        from hms_cadcam.cam.automatic_parameters import CamQualityProfile
        from hms_cadcam.cam.application.rest_contour import (
            resolve_rest_contour_application_parameters,
            validate_rest_contour_machine_authority,
        )
        from hms_cadcam.cam.domain import (
            MachineCompatibilityStatus,
            MachineEvidence,
            MachineRequirement,
            OperationCapability,
            ToolAssemblyEvidence,
            ToolAssemblyStatus,
            assess_machine_compatibility,
            assess_tool_assembly,
        )
        from hms_cadcam.cam.operation_registry import default_rest_contour_operation_registry

        with self._lock:
            setup = _find_setup(self._snapshot, setup_id)
            if _find_job_for_setup(self._snapshot, setup_id).job_id != job_id:
                raise CamChildNotFoundError("Rest Contour setup does not belong to the selected job")
            assembly = next((value for value in self._snapshot.tool_assemblies
                             if value.assembly_id == tool_assembly_id), None)
            if assembly is None:
                raise CamChildNotFoundError("Rest Contour tool assembly does not exist")
            tool = next((value for value in self._snapshot.tool_definitions
                         if value.tool_id == assembly.tool_id), None)
            if tool is None:
                raise CamChildNotFoundError("Rest Contour tool definition does not exist")
            holder = next((value for value in self._snapshot.holder_definitions
                           if value.holder_id == assembly.holder_id), None)
            assembly_status = assess_tool_assembly(
                assembly,
                ToolAssemblyEvidence(
                    True,
                    tool.revision,
                    tool.content_fingerprint,
                    tool.unit,
                    assembly.holder_id is not None and holder is not None,
                    holder.revision if holder is not None else None,
                    holder.content_fingerprint if holder is not None else None,
                    holder.unit if holder is not None else None,
                ),
            )
            if assembly_status is not ToolAssemblyStatus.VALID:
                raise CamValidationError(
                    f"Rest Contour tool assembly is not current: {assembly_status.value}"
                )
            if not isinstance(machine_requirement, MachineRequirement):
                raise CamValidationError("Rest Contour requires a current milling machine")
            if OperationCapability.MILLING not in machine_requirement.required_capabilities:
                raise CamValidationError("Rest Contour machine must require milling capability")
            machine = next((value for value in self._snapshot.machine_definitions
                            if value.machine_id == machine_requirement.machine_id), None)
            machine_status = assess_machine_compatibility(
                machine_requirement,
                MachineEvidence(
                    machine is not None,
                    machine.revision if machine is not None else None,
                    machine.content_fingerprint if machine is not None else None,
                    machine.unit if machine is not None else None,
                    machine.capabilities.operations if machine is not None else (),
                ),
            )
            if machine_status is not MachineCompatibilityStatus.COMPATIBLE:
                raise CamValidationError(
                    f"Rest Contour machine authority is not current: {machine_status.value}"
                )
            producer = next((value for value in setup.operation_tree.operations
                             if value.operation_id == dependency_operation_id), None)
            if producer is None:
                raise CamChildNotFoundError("Rest Contour material producer does not belong to the setup")
            effective_parameters = resolve_rest_contour_application_parameters(
                parameters,
                profile,
                tool,
                assembly,
                setup,
                profile_resolver,
                quality_profile=(CamQualityProfile.BALANCED
                                 if quality_profile is None else quality_profile),
                manual_overrides=manual_overrides,
            )
            validate_rest_contour_machine_authority(
                effective_parameters, machine, machine_requirement,
            )
            operation = default_rest_contour_operation_registry().create(
                operation_id=operation_id, node_id=node_id, setup_id=setup_id,
                parameters=effective_parameters, profile=profile,
                dependency_operation_id=dependency_operation_id,
                tool_assembly=assembly, machine_requirement=machine_requirement,
            )

            def mutation(state: CamProjectSnapshot) -> CamProjectSnapshot:
                current = _find_setup(state, setup_id)
                tree = current.operation_tree.add_operation(parent_node_id, name, operation)
                tree = OperationTree(
                    tree.setup_id, tree.root_id, tree.nodes, tree.operations,
                    tree.dependency_graph.with_edge_added(
                        DependencyEdge.material_state(dependency_operation_id, operation_id)
                    ), tree.revision.next(),
                )
                return _replace_setup(state, replace(current, operation_tree=tree))

            return self.apply(mutation)

    def create_rest_finishing_operation(
        self, job_id: CamJobId, setup_id: SetupId, parent_node_id: CamNodeId, *,
        operation_id: OperationId, node_id: CamNodeId, name: str,
        parameters: RestFinishingParameters,
        profile: RestFinishingProfileSelection,
        dependency_operation_id: OperationId,
        tool_assembly_id: ToolAssemblyId,
        machine_requirement: MachineRequirement,
    ) -> CamProjectSnapshot:
        """Create a manual v1 Rest Finishing operation with one explicit edge."""
        from hms_cadcam.cam.domain import MachineEvidence, ToolAssemblyEvidence
        from hms_cadcam.cam.domain.rest_finishing import (
            RestFinishingParameters, RestFinishingProfileSelection,
        )
        from hms_cadcam.cam.operation_registry import default_rest_contour_operation_registry

        if not isinstance(parameters, RestFinishingParameters) or not isinstance(profile, RestFinishingProfileSelection):
            raise CamValidationError("Rest Finishing creation parameters are invalid")
        with self._lock:
            setup = _find_setup(self._snapshot, setup_id)
            if _find_job_for_setup(self._snapshot, setup_id).job_id != job_id:
                raise CamChildNotFoundError("Rest Finishing setup does not belong to the selected job")
            assembly = next((item for item in self._snapshot.tool_assemblies if item.assembly_id == tool_assembly_id), None)
            if assembly is None:
                raise CamChildNotFoundError("Rest Finishing tool assembly does not exist")
            tool = _find_tool(self._snapshot, assembly.tool_id)
            holder = next((item for item in self._snapshot.holder_definitions if item.holder_id == assembly.holder_id), None)
            requirement = machine_requirement
            machine = None if requirement is None else next((item for item in self._snapshot.machine_definitions if item.machine_id == requirement.machine_id), None)
            # Construction accepts only current evidence; the domain factory
            # then seals profile/unit/capability shape into the aggregate.
            from hms_cadcam.cam.domain import assess_machine_compatibility, assess_tool_assembly, MachineCompatibilityStatus, ToolAssemblyStatus
            if assess_tool_assembly(assembly, ToolAssemblyEvidence(True, tool.revision, tool.content_fingerprint, tool.unit,
                    assembly.holder_id is not None and holder is not None,
                    holder.revision if holder is not None else None,
                    holder.content_fingerprint if holder is not None else None,
                    holder.unit if holder is not None else None)) is not ToolAssemblyStatus.VALID:
                raise CamValidationError("Rest Finishing tool assembly is not current")
            if assess_machine_compatibility(requirement, MachineEvidence(machine is not None,
                    machine.revision if machine is not None else None,
                    machine.content_fingerprint if machine is not None else None,
                    machine.unit if machine is not None else None,
                    machine.capabilities.operations if machine is not None else ())) is not MachineCompatibilityStatus.COMPATIBLE:
                raise CamValidationError("Rest Finishing machine authority is not current")
            if not any(item.operation_id == dependency_operation_id for item in setup.operation_tree.operations):
                raise CamChildNotFoundError("Rest Finishing material producer does not belong to the setup")
            operation = default_rest_contour_operation_registry().create_rest_finishing(
                operation_id=operation_id, node_id=node_id, setup_id=setup_id,
                parameters=parameters, profile=profile,
                dependency_operation_id=dependency_operation_id, tool_assembly=assembly,
                machine_requirement=requirement,
            )
            producer_dependency = next(
                (item for item in self._snapshot.material_state_dependencies
                 if item.consumer_operation_id == dependency_operation_id), None,
            )
            producer_completion = (
                None if producer_dependency is None
                else producer_dependency.successor_publication
            )
            if producer_completion is None:
                raise CamValidationError(
                    "Rest Finishing requires a committed R272 material-state producer"
                )
            initial_dependency = MaterialStateDependency(
                operation_id, dependency_operation_id,
                producer_completion.successor_state_fingerprint,
                producer_completion.semantic_material_removal_fingerprint,
                producer_completion.setup_fingerprint,
                producer_completion.stock_fingerprint,
                producer_completion.engine_version, producer_completion.precision,
                producer_operation_authority_fingerprint=_material_removal_operation_fingerprint(
                    _find_operation(self._snapshot, dependency_operation_id)
                ),
            )
            def mutation(state: CamProjectSnapshot) -> CamProjectSnapshot:
                current = _find_setup(state, setup_id)
                tree = current.operation_tree.add_operation(parent_node_id, name, operation)
                tree = OperationTree(tree.setup_id, tree.root_id, tree.nodes, tree.operations,
                    tree.dependency_graph.with_edge_added(DependencyEdge.material_state(dependency_operation_id, operation_id)),
                    tree.revision.next())
                changed = _replace_setup(state, replace(current, operation_tree=tree))
                return replace(
                    changed,
                    material_state_dependencies=changed.material_state_dependencies
                    + (initial_dependency,),
                )
            return self.apply(mutation)

    def _rest_finishing_failure(self, code, message: str):
        """Map aggregate/store failures to an application-safe typed result."""
        from hms_cadcam.cam.application.rest_finishing_application import (
            rest_finishing_application_failure,
        )

        return rest_finishing_application_failure(code, message)

    def _rest_finishing_context(self, project_root: Path, operation_id: OperationId, profile_resolver, *, cancellation):
        """Resolve R274 consumer authority and freshly reconstruct its R272 capability."""
        from hms_cadcam.cam.application.rest_contour import RestMaterialStateCandidate
        from hms_cadcam.cam.application.rest_contour_lifecycle import RestContourLifecycle, RestContourLifecycleStatus
        from hms_cadcam.cam.application.rest_contour_toolpath import (
            _project_r272_producer_authority_setup,
            generate_rest_contour_phase_b,
            mint_r272_validated_successor_certificate,
        )
        from hms_cadcam.cam.application.rest_finishing_lifecycle import RestFinishingLifecycleContext
        from hms_cadcam.cam.domain import MachineEvidence, ToolAssemblyEvidence
        from hms_cadcam.cam.domain.rest_finishing import RestFinishingDiagnosticCode, RestFinishingParameters, RestFinishingProfileSelection, RestFinishingValidationError

        operation = _find_operation(self._snapshot, operation_id)
        parameters = RestFinishingParameters.from_operation_parameters(operation.parameters)
        profiles = tuple(item for item in operation.geometry_inputs if item.role.value == "profile")
        if len(profiles) != 1:
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.PROFILE_INVALID, "Rest Finishing has no unique persisted profile")
        resolved = profile_resolver(profiles[0].reference)
        if getattr(resolved, "profile", None) is None:
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.PROFILE_INVALID, "Rest Finishing persisted profile cannot be resolved")
        selection = RestFinishingProfileSelection(resolved.profile)
        selection.validate_for(parameters)
        setup = _find_setup(self._snapshot, operation.setup_id)
        edges = tuple(edge for edge in setup.operation_tree.dependency_graph.edges if edge.target_operation_id == operation_id and edge.kind.value == "material_state")
        if len(edges) != 1:
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.MATERIAL_STATE_MISSING if not edges else RestFinishingDiagnosticCode.MATERIAL_STATE_AMBIGUOUS, "Rest Finishing material-state dependency is missing or ambiguous")
        dependency = next((item for item in self._snapshot.material_state_dependencies if item.consumer_operation_id == operation_id), None)
        if dependency is None or dependency.producer_operation_id != edges[0].source_operation_id:
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Finishing dependency evidence is invalid")
        state = operation.artifact_state
        prior_completion = dependency.successor_publication
        if prior_completion is None and state.status is ArtifactStatus.VALID:
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
                "Rest Finishing VALID operation lacks committed completion evidence",
            )
        if prior_completion is not None and state.status is ArtifactStatus.VALID:
            metadata_values = tuple(
                item for item in self._snapshot.artifacts
                if item.operation_id == operation_id
            )
            if len(metadata_values) != 1:
                raise RestFinishingValidationError(
                    RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
                    "Rest Finishing committed artifact metadata is missing or ambiguous",
                )
            metadata = metadata_values[0]
            try:
                prior_artifact = self._artifact_store.load(project_root, metadata)
                prior_state_load = self._material_states.load(
                    project_root, prior_completion.successor_state_fingerprint,
                )
            except (OSError, ToolpathArtifactStoreError, TypeError, ValueError) as error:
                raise RestFinishingValidationError(
                    RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
                    f"Rest Finishing committed output bytes are invalid: {error}",
                ) from error
            prior_state = prior_state_load.state
            if (
                state.token is not None
                or state.dirty_reasons
                or state.diagnostics
                or operation.diagnostics
                or state.input_fingerprint != prior_completion.input_fingerprint
                or state.artifact_fingerprint != prior_completion.artifact_fingerprint
                or state.generation != metadata.computation_generation
                or metadata.artifact_id != prior_completion.artifact_id
                or metadata.artifact_fingerprint != prior_completion.artifact_fingerprint
                or metadata.input_fingerprint != prior_completion.input_fingerprint
                or metadata.expected_operation_revision != operation.revision
                or metadata.completion_status.lower() != "complete"
                or prior_artifact.source_operation_id != operation_id
                or prior_artifact.operation_revision != operation.revision
                or prior_artifact.artifact_fingerprint != prior_completion.artifact_fingerprint
                or compute_material_removal_fingerprint(prior_artifact)
                   != prior_completion.semantic_material_removal_fingerprint
                or prior_state_load.status is not MaterialStateLoadStatus.VALID
                or prior_state is None
                or prior_state.fingerprint != prior_completion.successor_state_fingerprint
                or prior_state.content_integrity_fingerprint
                   != prior_completion.successor_state_content_seal
                or prior_state.parent_fingerprint
                   != prior_completion.parent_state_fingerprint
                or prior_state.setup_fingerprint != prior_completion.setup_fingerprint
                or prior_state.stock_fingerprint != prior_completion.stock_fingerprint
                or prior_state.engine_version != prior_completion.engine_version
                or prior_state.precision.to_dict() != prior_completion.precision
            ):
                raise RestFinishingValidationError(
                    RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
                    "Rest Finishing committed output authority is inconsistent",
                )
            # A reopen request regenerates through R273 rather than treating
            # persisted output bytes as a process-local candidate.  Preserve
            # generation while making a detached replay-only operation able to
            # enter the normal begin/publish lifecycle.
            operation = replace(
                operation,
                artifact_state=state.mark_dirty(DirtyReason.UPSTREAM_CHANGED),
                diagnostics=(),
            )
            setup = replace(
                setup,
                operation_tree=setup.operation_tree.replace_operation(operation),
            )
        # A prior R274 successor is output evidence, never predecessor input.
        # Strip it from the transient R273 material candidate; SUCCESS will
        # attach a newly replayed successor publication atomically.
        input_dependency = replace(dependency, successor_publication=None)
        producer = _find_operation(self._snapshot, dependency.producer_operation_id)
        producer_dependency = next((item for item in self._snapshot.material_state_dependencies if item.consumer_operation_id == producer.operation_id), None)
        if producer_dependency is None or producer_dependency.successor_publication is None:
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.MATERIAL_STATE_MISSING, "Rest Finishing requires a committed R272 producer")
        # Rebuild, rather than deserialize, the opaque R272 certificate from a
        # fresh deterministic R272 replay in this process.
        publication, artifact, successor = self._load_rest_contour_completion(project_root, producer.operation_id, profile_resolver=profile_resolver, cancellation=cancellation)
        producer_projection = _project_r272_producer_authority_setup(
            setup, producer.operation_id,
        )
        producer_context = self._rest_contour_context(
            project_root, producer.operation_id, profile_resolver,
            cancellation=cancellation,
            # A persisted R272 consumer is already VALID.  Reconstruct the
            # exact historical reservation with its immutable generation so
            # certificate minting can independently replay it without
            # mutating current aggregate state.
            replay_generation=artifact.computation_token.generation,
            setup_override=producer_projection,
        )
        prepared = RestContourLifecycle().prepare(producer_context)
        if prepared.status is not RestContourLifecycleStatus.PREPARED or prepared.prepared is None:
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID, "R272 producer cannot be freshly prepared")
        replay_candidate = generate_rest_contour_phase_b(prepared.prepared.phase_b_prepared, cancellation=cancellation)
        certificate = mint_r272_validated_successor_certificate(
            replay_context=prepared.prepared.phase_b_context, validation_candidate=replay_candidate,
            authoritative_setup=setup,
            authoritative_producer_operation=producer,
            exact_producer_artifact=artifact, trusted_parent_state=replay_candidate.prepared.predecessor_state,
            supplied_successor_state=successor, producer_completion=publication,
            producer_dependency=producer_dependency, cancellation=cancellation,
        )
        assembly = next((item for item in self._snapshot.tool_assemblies if item.assembly_id == operation.tool_assembly.assembly_id), None)
        if assembly is None:
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.TOOL_INELIGIBLE, "Rest Finishing tool assembly is missing")
        tool = _find_tool(self._snapshot, assembly.tool_id)
        holder = next((item for item in self._snapshot.holder_definitions if item.holder_id == assembly.holder_id), None)
        requirement = operation.machine_requirement
        machine = None if requirement is None else next((item for item in self._snapshot.machine_definitions if item.machine_id == requirement.machine_id), None)
        if requirement is None or machine is None:
            raise RestFinishingValidationError(RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE, "Rest Finishing machine is missing")
        return RestFinishingLifecycleContext(
            setup, parameters, selection,
            (RestMaterialStateCandidate(producer.operation_id, successor, input_dependency, edges[0], artifact),),
            publication, producer_dependency, replay_candidate.prepared.predecessor_state, certificate,
            setup.operation_tree.dependency_graph, assembly,
            ToolAssemblyEvidence(True, tool.revision, tool.content_fingerprint, tool.unit,
                assembly.holder_id is not None and holder is not None,
                holder.revision if holder is not None else None,
                holder.content_fingerprint if holder is not None else None,
                holder.unit if holder is not None else None),
            tool, machine, requirement,
            MachineEvidence(True, machine.revision, machine.content_fingerprint, machine.unit, machine.capabilities.operations),
            operation_id, profile_resolver, cancellation,
        )

    def prepare_rest_finishing(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        profile_resolver: Callable[[GeometryReference], ResolvedContourProfile],
        cancellation: Callable[[], bool] | None = None,
    ) -> RestFinishingLifecyclePreparation | RestFinishingApplicationResult:
        from hms_cadcam.cam.application.rest_finishing_lifecycle import prepare_rest_finishing_3axis
        from hms_cadcam.cam.domain.rest_finishing import RestFinishingDiagnosticCode, RestFinishingValidationError
        from hms_cadcam.cam.domain.rest_contour import (
            RestContourDiagnosticCode,
            RestContourValidationError,
        )
        with self._lock:
            try:
                return prepare_rest_finishing_3axis(self._rest_finishing_context(project_root, operation_id, profile_resolver, cancellation=cancellation))
            except RestFinishingValidationError as error:
                return self._rest_finishing_failure(error.code, str(error))
            except RestContourValidationError as error:
                code = (
                    RestFinishingDiagnosticCode.CANCELLED
                    if error.code is RestContourDiagnosticCode.CANCELLED
                    else RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
                )
                return self._rest_finishing_failure(code, str(error))
            except (CamValidationError, CamChildNotFoundError, ToolpathArtifactStoreError, OSError, RuntimeError, TypeError, ValueError) as error:
                return self._rest_finishing_failure(RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID, f"Rest Finishing aggregate authority is invalid: {error}")

    def generate_rest_finishing(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        profile_resolver: Callable[[GeometryReference], ResolvedContourProfile],
        cancellation: Callable[[], bool] | None = None,
    ) -> RestFinishingApplicationResult:
        from hms_cadcam.cam.application.rest_finishing_application import (
            RestFinishingApplicationStatus,
            publish_rest_finishing_candidate,
            rest_finishing_application_result,
        )
        from hms_cadcam.cam.application.rest_finishing_lifecycle import RestFinishingLifecycleStatus, generate_rest_finishing_3axis
        from hms_cadcam.cam.domain.rest_finishing import RestFinishingDiagnosticCode, RestFinishingValidationError
        from hms_cadcam.cam.persistence.models import MaterialStateDependency, MaterialStateSuccessorPublication
        with self._lock:
            before = _clone_snapshot(self._snapshot)
            persisted_artifact = None
            persisted_successor = None
            persisted_operation = _find_operation(self._snapshot, operation_id)
            persisted_dependency = next(
                (
                    item for item in self._snapshot.material_state_dependencies
                    if item.consumer_operation_id == operation_id
                ),
                None,
            )
            if (
                persisted_operation.artifact_state.status is ArtifactStatus.VALID
                and persisted_dependency is not None
                and persisted_dependency.successor_publication is not None
            ):
                persisted_metadata = tuple(
                    item for item in self._snapshot.artifacts
                    if item.operation_id == operation_id
                )
                if len(persisted_metadata) == 1:
                    try:
                        persisted_artifact = self._artifact_store.load(
                            project_root, persisted_metadata[0],
                        )
                        persisted_load = self._material_states.load(
                            project_root,
                            persisted_dependency.successor_publication.successor_state_fingerprint,
                        )
                        if persisted_load.status is MaterialStateLoadStatus.VALID:
                            persisted_successor = persisted_load.state
                    except (OSError, ToolpathArtifactStoreError, TypeError, ValueError):
                        # The prepare path below owns the typed failure mapping
                        # for malformed persisted output evidence.
                        persisted_artifact = None
                        persisted_successor = None
            preparation = self.prepare_rest_finishing(project_root, operation_id, profile_resolver=profile_resolver, cancellation=cancellation)
            if preparation.status is not RestFinishingLifecycleStatus.PREPARED:
                if preparation.status in {
                    RestFinishingApplicationStatus.CANCELLED,
                    RestFinishingApplicationStatus.FAILURE,
                }:
                    return preparation
                return rest_finishing_application_result(
                    generate_rest_finishing_3axis(preparation)
                )
            core_result = generate_rest_finishing_3axis(preparation)
            if (
                core_result.status is not RestFinishingLifecycleStatus.SUCCESS
                or core_result.candidate is None
            ):
                return rest_finishing_application_result(core_result)
            try:
                if persisted_artifact is not None:
                    artifact_matches = (
                        ContentFingerprint.from_payload(
                            _rest_finishing_artifact_output_payload(
                                persisted_artifact
                            )
                        )
                        == ContentFingerprint.from_payload(
                            _rest_finishing_artifact_output_payload(
                                core_result.candidate.artifact
                            )
                        )
                    )
                    successor_matches = (
                        persisted_successor is not None
                        and persisted_successor.to_dict()
                            == core_result.candidate.successor_state.to_dict()
                        and persisted_successor.content_integrity_fingerprint
                            == core_result.candidate.successor_state.content_integrity_fingerprint
                    )
                    if not artifact_matches or not successor_matches:
                        mismatch = (
                            "artifact and successor"
                            if not artifact_matches and not successor_matches
                            else "artifact" if not artifact_matches else "successor"
                        )
                        raise RestFinishingValidationError(
                            RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
                            "Rest Finishing persisted " + mismatch
                            + " differs from fresh deterministic replay",
                        )
                publication = publish_rest_finishing_candidate(core_result.candidate, project_root=project_root, artifact_store=self._artifact_store, material_state_store=self._material_states, cancellation=cancellation)
                candidate = core_result.candidate
                evidence = MaterialStateSuccessorPublication.create(
                    consumer_operation_id=operation_id, artifact_id=publication.artifact.artifact_id,
                    artifact_fingerprint=publication.artifact.artifact_fingerprint,
                    input_fingerprint=publication.artifact.input_fingerprint,
                    semantic_material_removal_fingerprint=candidate.semantic_material_removal_fingerprint,
                    parent_state_fingerprint=candidate.successor_provenance.parent_fingerprint,
                    parent_state_content_seal=candidate.successor_provenance.parent_content_integrity_fingerprint,
                    successor_state_fingerprint=publication.successor_state.fingerprint,
                    successor_state_content_seal=publication.successor_state.content_integrity_fingerprint,
                    setup_fingerprint=publication.successor_state.setup_fingerprint,
                    stock_fingerprint=publication.successor_state.stock_fingerprint,
                    engine_version=publication.successor_state.engine_version,
                    precision=publication.successor_state.precision.to_dict(),
                )
                input_dependency = candidate.prepared.plan.material_candidate.dependency
                staged_dependency = replace(
                    input_dependency,
                    successor_publication=evidence,
                    # An uncompleted dependency persists as legacy v1 and
                    # intentionally carries no completion-authority field.
                    # Upgrade to v2 only here, from the exact producer that
                    # the fresh R272 replay above just proved current.
                    producer_operation_authority_fingerprint=(
                        _material_removal_operation_fingerprint(
                            _find_operation(
                                self._snapshot,
                                input_dependency.producer_operation_id,
                            )
                        )
                    ),
                )
                staged = replace(self._snapshot,
                    artifacts=tuple(item for item in self._snapshot.artifacts if item.operation_id != operation_id) + (publication.artifact_metadata,),
                    material_state_dependencies=tuple(item for item in self._snapshot.material_state_dependencies if item.consumer_operation_id != operation_id) + (staged_dependency,))
                self._snapshot = _replace_operation(staged, publication.operation)
                self._post.mark_stale(operation_id)
                return rest_finishing_application_result(
                    core_result,
                    publication=publication,
                )
            except RestFinishingValidationError as error:
                self._snapshot = before
                return self._rest_finishing_failure(error.code, str(error))
            except (CamValidationError, CamChildNotFoundError, ToolpathArtifactStoreError, OSError, RuntimeError, TypeError, ValueError) as error:
                self._snapshot = before
                return self._rest_finishing_failure(RestFinishingDiagnosticCode.SUCCESSOR_INVALID, f"Rest Finishing generation authority is invalid: {error}")

    def restore_rest_finishing_snapshot(self, snapshot: CamProjectSnapshot) -> CamProjectSnapshot:
        """Discard uncommitted R274 SQLite staging; file orphans remain inert."""
        if not isinstance(snapshot, CamProjectSnapshot):
            raise TypeError("Rest Finishing rollback snapshot is invalid")
        with self._lock:
            self._snapshot = _clone_snapshot(snapshot)
            return _clone_snapshot(self._snapshot)

    def _rest_contour_failure(self, diagnostic_code, message: str):
        """Return an externally safe lifecycle failure for aggregate resolution.

        Resolution failures are ordinary project-data failures.  They must not
        leak a partially resolved authority (or an implementation exception)
        to callers that decide whether a machining action is permitted.
        """
        from hms_cadcam.cam.application.rest_contour_lifecycle import (
            RestContourLifecyclePreparation, RestContourLifecycleResult,
            RestContourLifecycleStatus,
        )

        preparation = RestContourLifecyclePreparation(
            RestContourLifecycleStatus.FAILURE,
            diagnostic_code=diagnostic_code,
            message=message,
        )
        return RestContourLifecycleResult(
            RestContourLifecycleStatus.FAILURE,
            preparation,
            diagnostic_code=diagnostic_code,
            message=message,
        )

    def prepare_rest_contour(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        profile_resolver,
        cancellation: Callable[[], bool] | None = None,
    ):
        """Resolve current aggregate authority and reserve one sealed R271 plan."""
        from hms_cadcam.cam.application.rest_contour_lifecycle import (
            RestContourLifecycle, RestContourLifecycleStatus,
        )
        from hms_cadcam.cam.domain.rest_contour import RestContourValidationError

        with self._lock:
            try:
                context = self._rest_contour_context(
                    project_root, operation_id, profile_resolver, cancellation=cancellation,
                )
                result = RestContourLifecycle().prepare(context)
            except RestContourValidationError as error:
                result = self._rest_contour_failure(error.code, str(error)).preparation
            except (CamValidationError, CamChildNotFoundError, ToolpathArtifactStoreError,
                    OSError, RuntimeError, TypeError, ValueError) as error:
                from hms_cadcam.cam.domain.rest_contour import RestContourDiagnosticCode

                result = self._rest_contour_failure(
                    RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                    f"Rest Contour aggregate authority is invalid: {error}",
                ).preparation
            if result.status is RestContourLifecycleStatus.PREPARED:
                self._rest_contour_preparations[operation_id] = result
            else:
                self._rest_contour_preparations.pop(operation_id, None)
            return result

    def generate_rest_contour(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        profile_resolver,
        cancellation: Callable[[], bool] | None = None,
    ):
        """Generate/publish Rest Contour from a fresh service-owned reservation.

        R271 writes immutable artifact/state bytes first.  Only after their
        exact readback does this method stage artifact metadata and the v2
        successor record in the editable snapshot.  A later SQLite save is the
        durable project authority; bytes without that record are ignored.
        """
        from hms_cadcam.cam.application.rest_contour_lifecycle import (
            RestContourLifecycle, RestContourLifecycleStatus,
        )

        with self._lock:
            # Same-process and fresh-reopen reuse share one verification path.
            # A process-local cached SUCCESS is never authority by itself:
            # current profile/geometry, DAG, tool, machine, artifact and v2
            # completion fingerprints are all revalidated below.
            existing = self._rehydrate_rest_contour_completion(
                project_root,
                operation_id,
                profile_resolver=profile_resolver,
                cancellation=cancellation,
            )
            if existing is not None:
                return existing
            before = _clone_snapshot(self._snapshot)
            preparation = self.prepare_rest_contour(
                project_root, operation_id, profile_resolver=profile_resolver,
                cancellation=cancellation,
            )
            lifecycle = RestContourLifecycle()
            try:
                result = lifecycle.generate(
                    preparation,
                    project_root=project_root,
                    publisher=self._publish_rest_contour_candidate,
                )
            except (CamValidationError, CamChildNotFoundError, ToolpathArtifactStoreError,
                    OSError, TypeError, ValueError) as error:
                self._snapshot = before
                self._rest_contour_preparations.pop(operation_id, None)
                from hms_cadcam.cam.domain.rest_contour import RestContourDiagnosticCode

                return self._rest_contour_failure(
                    RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                    f"Rest Contour generation authority is invalid: {error}",
                )
            if result.status is RestContourLifecycleStatus.SUCCESS:
                self._rest_contour_preparations.pop(operation_id, None)
                self._rest_contour_completed[operation_id] = result
                self._post.mark_stale(operation_id)
            elif result.status is RestContourLifecycleStatus.FAILURE:
                self._snapshot = before
                self._rest_contour_preparations.pop(operation_id, None)
                self._rest_contour_completed.pop(operation_id, None)
            return result

    def _rest_contour_context(
        self,
        project_root: Path,
        operation_id: OperationId,
        profile_resolver,
        *,
        cancellation: Callable[[], bool] | None,
        replay_generation: int | None = None,
        setup_override=None,
    ):
        """Build lifecycle context solely from the current snapshot and stores."""
        from hms_cadcam.cam.application.rest_contour import (
            RestContourFoundationInputs, RestMaterialStateCandidate,
        )
        from hms_cadcam.cam.application.rest_contour_lifecycle import RestContourLifecycleContext
        from hms_cadcam.cam.domain import MachineEvidence, ToolAssemblyEvidence
        from hms_cadcam.cam.domain.rest_contour import (
            RestContourDiagnosticCode,
            RestContourParameters,
            RestContourProfileSelection,
            RestContourValidationError,
        )
        from hms_cadcam.cam.material_state import material_state_setup_fingerprint
        from hms_cadcam.cam.persistence.models import MaterialStateDependency

        if not isinstance(project_root, Path):
            raise TypeError("Rest Contour project root is invalid")
        operation = _find_operation(self._snapshot, operation_id)
        setup = _find_setup(self._snapshot, operation.setup_id)
        if setup_override is not None:
            if not isinstance(setup_override, Setup) or setup_override.setup_id != setup.setup_id:
                raise TypeError("Rest Contour projected Setup is invalid")
            setup = setup_override
            operation = setup.operation_tree.get_operation(operation_id)
        if replay_generation is not None:
            if type(replay_generation) is not int or replay_generation <= 0:
                raise TypeError("Rest Contour replay generation is invalid")
            replay_state = replace(
                operation.artifact_state,
                status=ArtifactStatus.DIRTY,
                generation=replay_generation - 1,
                token=None,
                input_fingerprint=None,
                artifact_fingerprint=None,
                dirty_reasons=(DirtyReason.PARAMETERS_CHANGED,),
                diagnostics=(),
            )
            operation = replace(operation, artifact_state=replay_state, diagnostics=())
            setup = replace(
                setup,
                operation_tree=setup.operation_tree.replace_operation(operation),
            )
        parameters = RestContourParameters.from_operation_parameters(operation.parameters)
        profiles = tuple(value for value in operation.geometry_inputs if value.role.value == "profile")
        if len(operation.geometry_inputs) != 1 or len(profiles) != 1:
            raise RestContourValidationError(
                RestContourDiagnosticCode.PROFILE_INVALID,
                "Rest Contour has no unique persisted profile",
            )
        resolved_profile = profile_resolver(profiles[0].reference)
        if getattr(resolved_profile, "profile", None) is None:
            raise RestContourValidationError(
                RestContourDiagnosticCode.PROFILE_INVALID,
                "Rest Contour persisted profile cannot be resolved",
            )
        profile = RestContourProfileSelection(resolved_profile.profile)
        assembly = next((value for value in self._snapshot.tool_assemblies
                         if value.assembly_id == operation.tool_assembly.assembly_id), None)
        if assembly is None:
            raise RestContourValidationError(
                RestContourDiagnosticCode.TOOL_INELIGIBLE,
                "Rest Contour tool assembly is missing",
            )
        try:
            tool = _find_tool(self._snapshot, assembly.tool_id)
        except CamChildNotFoundError as error:
            raise RestContourValidationError(
                RestContourDiagnosticCode.TOOL_INELIGIBLE,
                "Rest Contour tool definition is missing",
            ) from error
        holder = next((value for value in self._snapshot.holder_definitions
                       if value.holder_id == assembly.holder_id), None)
        assembly_evidence = ToolAssemblyEvidence(
            True, tool.revision, tool.content_fingerprint, tool.unit,
            assembly.holder_id is not None and holder is not None,
            holder.revision if holder is not None else None,
            holder.content_fingerprint if holder is not None else None,
            holder.unit if holder is not None else None,
        )
        requirement = operation.machine_requirement
        if requirement is None:
            raise RestContourValidationError(
                RestContourDiagnosticCode.MACHINE_INCOMPATIBLE,
                "Rest Contour machine requirement is missing",
            )
        machine = next((value for value in self._snapshot.machine_definitions
                        if value.machine_id == requirement.machine_id), None)
        if machine is None:
            raise RestContourValidationError(
                RestContourDiagnosticCode.MACHINE_INCOMPATIBLE,
                "Rest Contour machine is missing",
            )
        machine_evidence = MachineEvidence(
            True, machine.revision, machine.content_fingerprint, machine.unit,
            machine.capabilities.operations,
        )
        edges = tuple(edge for edge in setup.operation_tree.dependency_graph.edges
                      if edge.target_operation_id == operation_id and edge.kind.value == "material_state")
        if len(edges) != 1:
            raise RestContourValidationError(
                RestContourDiagnosticCode.MATERIAL_STATE_MISSING
                if not edges else RestContourDiagnosticCode.MATERIAL_STATE_AMBIGUOUS,
                "Rest Contour material-state dependency is missing or ambiguous",
            )
        producer_id = edges[0].source_operation_id
        producer = _find_operation(self._snapshot, producer_id)
        metadata = next((value for value in self._snapshot.artifacts
                         if value.operation_id == producer_id), None)
        if metadata is None:
            raise RestContourValidationError(
                RestContourDiagnosticCode.MATERIAL_STATE_MISSING,
                "Rest Contour producer artifact metadata is missing",
            )
        artifact = self._artifact_store.load(project_root, metadata)
        # Feed/spindle-only edits make the operation artifact stale for direct
        # NC reuse, but they do not change material removal. ``apply()`` keeps
        # the downstream v2 dependency only when the producer's scoped
        # material-removal authority is unchanged. Build a detached semantic
        # projection for Phase A so the old exact artifact may still prove the
        # downstream MaterialState without relabelling the editable operation.
        producer_state = producer.artifact_state
        if _is_feed_only_material_artifact(producer, artifact):
            semantic_state = replace(
                producer_state,
                status=ArtifactStatus.VALID,
                artifact_fingerprint=artifact.artifact_fingerprint,
                dirty_reasons=(),
                diagnostics=(),
            )
            producer = replace(
                producer,
                revision=artifact.operation_revision,
                artifact_state=semantic_state,
            )
            setup = replace(
                setup,
                operation_tree=setup.operation_tree.replace_operation(producer),
            )
        # A predecessor published by a prior R272 Rest operation has an exact
        # v2 pointer.  For a pre-R272 machining producer, calculate the state
        # from that explicitly selected producer only; never scan states or
        # choose a prior list item.
        prior = next((value for value in self._snapshot.material_state_dependencies
                      if value.consumer_operation_id == producer_id
                      and getattr(value, "successor_publication", None) is not None), None)
        if prior is not None:
            _publication, _artifact, parent = self._load_rest_contour_completion(
                project_root,
                producer_id,
                profile_resolver=profile_resolver,
                cancellation=cancellation,
                material_only=True,
            )
        else:
            from hms_cadcam.cam.domain.rest_contour import REST_CONTOUR_STRATEGY_KEY

            if producer.strategy_key == REST_CONTOUR_STRATEGY_KEY:
                raise RestContourValidationError(
                    RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                    "Rest Contour producer lacks a committed v2 COMPLETE record",
                )
            producer_assembly = next((value for value in self._snapshot.tool_assemblies
                                      if value.assembly_id == producer.tool_assembly.assembly_id), None)
            if producer_assembly is None:
                raise RestContourValidationError(
                    RestContourDiagnosticCode.MATERIAL_STATE_STALE,
                    "Rest Contour producer tool assembly is missing",
                )
            try:
                producer_tool = _find_tool(self._snapshot, producer_assembly.tool_id)
            except CamChildNotFoundError as error:
                raise RestContourValidationError(
                    RestContourDiagnosticCode.MATERIAL_STATE_STALE,
                    "Rest Contour producer tool definition is missing",
                ) from error
            from hms_cadcam.cam.domain import ToolReferenceStatus

            if (
                producer.tool_assembly.assess(producer_assembly) is not ToolReferenceStatus.VALID
                or producer_assembly.expected_tool_revision != producer_tool.revision
                or producer_assembly.expected_tool_fingerprint != producer_tool.content_fingerprint
                or producer_assembly.expected_tool_unit is not producer_tool.unit
            ):
                raise RestContourValidationError(
                    RestContourDiagnosticCode.MATERIAL_STATE_STALE,
                    "Rest Contour producer tool or holder authority changed",
                )
            # A Phase-A reservation has no durable side effect.  The
            # calculator creates a process-trusted immutable state; R271 can
            # consume that exact object during this active generation.  A
            # persisted trust origin is reconstructed only from an already
            # committed v2 successor when a project is reopened.
            parent = calculate_material_state(
                stock=setup.stock, artifact=artifact, tool=producer_tool,
                setup_fingerprint=material_state_setup_fingerprint(setup),
            ).state
        dependency = MaterialStateDependency(
            operation_id, producer_id, parent.fingerprint, parent.toolpath_fingerprint,
            material_state_setup_fingerprint(setup), parent.stock_fingerprint,
            parent.engine_version, parent.precision.to_dict(),
        )
        candidate = RestMaterialStateCandidate(producer_id, parent, dependency, edges[0], artifact)
        return RestContourLifecycleContext(
            RestContourFoundationInputs(
                setup, parameters, profile, (candidate,), setup.operation_tree.dependency_graph,
                assembly, assembly_evidence, tool, machine, requirement, operation_id,
            ), machine_evidence, profile_resolver, cancellation,
        )

    def _load_rest_contour_completion(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        profile_resolver,
        cancellation,
        material_only: bool = False,
    ):
        """Rehydrate one exact v2 COMPLETE record or fail before Phase A.

        The database record is only a pointer.  Every cross-reference is
        checked again against current aggregate facts and both durable bytes so
        a self-sealed replacement record cannot become machining authority.
        """
        from hms_cadcam.cam.material_state import MaterialStateLoadStatus

        operation = _find_operation(self._snapshot, operation_id)
        setup = _find_setup(self._snapshot, operation.setup_id)
        dependency = next((item for item in self._snapshot.material_state_dependencies
                           if item.consumer_operation_id == operation_id), None)
        if dependency is None or dependency.successor_publication is None:
            raise CamValidationError("Rest Contour v2 completion record is missing")
        material_edges = tuple(
            item for item in setup.operation_tree.dependency_graph.edges
            if item.target_operation_id == operation_id
            and item.kind.value == "material_state"
        )
        if (
            len(material_edges) != 1
            or material_edges[0].source_operation_id != dependency.producer_operation_id
        ):
            raise CamValidationError(
                "Rest Contour v2 producer differs from the authoritative material-state DAG"
            )
        publication = dependency.successor_publication
        if (publication.consumer_operation_id != operation_id
                or publication.parent_state_fingerprint != dependency.parent_state_fingerprint
                or publication.setup_fingerprint != dependency.setup_fingerprint
                or publication.stock_fingerprint != dependency.stock_fingerprint
                or publication.engine_version != dependency.engine_version
                or publication.precision != dependency.precision):
            raise CamValidationError("Rest Contour v2 completion record is inconsistent")
        expected_setup = material_state_setup_fingerprint(setup)
        expected_stock = ContentFingerprint.from_payload(setup.stock.to_dict())
        if publication.setup_fingerprint != expected_setup or publication.stock_fingerprint != expected_stock:
            raise CamValidationError("Rest Contour v2 Setup or Stock provenance changed")
        metadata = next((item for item in self._snapshot.artifacts
                         if item.operation_id == operation_id), None)
        state = operation.artifact_state
        if metadata is None or (
            metadata.artifact_id != publication.artifact_id
            or metadata.artifact_fingerprint != publication.artifact_fingerprint
            or metadata.input_fingerprint != publication.input_fingerprint
            or metadata.completion_status.lower() != "complete"
            or metadata.computation_generation != state.generation
        ):
            raise CamValidationError("Rest Contour v2 artifact metadata is inconsistent")
        artifact = self._artifact_store.load(project_root, metadata)
        exact_operation_state = (
            state.status is ArtifactStatus.VALID
            and state.token is None
            and not state.dirty_reasons
            and not state.diagnostics
            and not operation.diagnostics
            and state.input_fingerprint == publication.input_fingerprint
            and state.artifact_fingerprint == publication.artifact_fingerprint
            and metadata.expected_operation_revision == operation.revision
        )
        material_operation_state = (
            material_only
            and not state.diagnostics
            and not operation.diagnostics
            and _is_feed_only_material_artifact(operation, artifact)
            and metadata.expected_operation_revision == artifact.operation_revision
        )
        if not (exact_operation_state or material_operation_state):
            raise CamValidationError("Rest Contour operation artifact state is not authoritative")
        if (artifact.artifact_id != publication.artifact_id
                or artifact.source_operation_id != operation_id
                or artifact.artifact_fingerprint != publication.artifact_fingerprint
                or artifact.input_fingerprint != publication.input_fingerprint
                or (
                    artifact.operation_revision != operation.revision
                    and not material_operation_state
                )
                or artifact.completion_status.value.lower() != "complete"
                or compute_material_removal_fingerprint(artifact)
                != publication.semantic_material_removal_fingerprint):
            raise CamValidationError("Rest Contour v2 artifact content is inconsistent")
        producer = _find_operation(self._snapshot, dependency.producer_operation_id)
        if (
            dependency.producer_operation_authority_fingerprint
            != _material_removal_operation_fingerprint(producer)
        ):
            raise CamValidationError(
                "Rest Contour producer material-removal operation authority changed"
            )
        parent_load = self._material_states.load(project_root, publication.parent_state_fingerprint)
        successor_load = self._material_states.load(project_root, publication.successor_state_fingerprint)
        if (successor_load.status is not MaterialStateLoadStatus.VALID
                or successor_load.state is None):
            raise CamValidationError("Rest Contour v2 material-state bytes are unavailable or corrupt")
        if parent_load.status is MaterialStateLoadStatus.VALID and parent_load.state is not None:
            parent = parent_load.state
        else:
            # The first Rest consumer may have calculated its predecessor from
            # an ordinary producer.  That calculation is intentionally not a
            # prepare-time durable write.  On reopen we can re-establish the
            # same trusted calculated object from the exact producer artifact;
            # a prior R272 producer, however, must resolve through its v2
            # COMPLETE successor and cannot fall back to recalculation.
            producer_dependency = next(
                (item for item in self._snapshot.material_state_dependencies
                 if item.consumer_operation_id == producer.operation_id), None,
            )
            if producer_dependency is not None and producer_dependency.successor_publication is not None:
                _prior_evidence, _prior_artifact, parent = self._load_rest_contour_completion(
                    project_root,
                    producer.operation_id,
                    profile_resolver=profile_resolver,
                    cancellation=cancellation,
                    material_only=True,
                )
            else:
                from hms_cadcam.cam.domain.rest_contour import REST_CONTOUR_STRATEGY_KEY

                if producer.strategy_key == REST_CONTOUR_STRATEGY_KEY:
                    raise CamValidationError(
                        "Rest Contour producer lacks a committed v2 COMPLETE record"
                    )
                producer_metadata = next(
                    (item for item in self._snapshot.artifacts
                     if item.operation_id == producer.operation_id), None,
                )
                if producer_metadata is None:
                    raise CamValidationError("Rest Contour producer metadata is missing")
                producer_artifact = self._artifact_store.load(project_root, producer_metadata)
                producer_assembly = next(
                    (item for item in self._snapshot.tool_assemblies
                     if item.assembly_id == producer.tool_assembly.assembly_id), None,
                )
                if producer_assembly is None:
                    raise CamValidationError("Rest Contour producer assembly is missing")
                producer_tool = _find_tool(self._snapshot, producer_assembly.tool_id)
                parent = calculate_material_state(
                    stock=setup.stock,
                    artifact=producer_artifact,
                    tool=producer_tool,
                    setup_fingerprint=expected_setup,
                ).state
        successor = successor_load.state
        successor_assembly = next(
            (item for item in self._snapshot.tool_assemblies
             if item.assembly_id == operation.tool_assembly.assembly_id),
            None,
        )
        if successor_assembly is None:
            raise CamValidationError("Rest Contour successor tool assembly is missing")
        successor_tool = _find_tool(self._snapshot, successor_assembly.tool_id)
        recompute_was_cancelled = False

        def recompute_cancellation() -> bool:
            nonlocal recompute_was_cancelled
            if not recompute_was_cancelled and cancellation is not None:
                recompute_was_cancelled = bool(cancellation())
            return recompute_was_cancelled

        try:
            recomputed_successor = calculate_material_state(
                stock=setup.stock,
                artifact=artifact,
                tool=successor_tool,
                setup_fingerprint=expected_setup,
                parent=parent,
                precision=successor.precision,
                cancellation=(recompute_cancellation if cancellation is not None else None),
            ).state
        except CamValidationError as error:
            if recompute_was_cancelled:
                from hms_cadcam.cam.domain.rest_contour import (
                    RestContourDiagnosticCode,
                    RestContourValidationError,
                )

                raise RestContourValidationError(
                    RestContourDiagnosticCode.CANCELLED,
                    "Rest Contour successor verification was cancelled",
                ) from error
            raise
        if (parent.fingerprint != publication.parent_state_fingerprint
                or dependency.producer_toolpath_fingerprint != parent.toolpath_fingerprint
                or parent.content_integrity_fingerprint != publication.parent_state_content_seal
                or parent.setup_fingerprint != publication.setup_fingerprint
                or parent.stock_fingerprint != publication.stock_fingerprint
                or parent.engine_version != publication.engine_version
                or parent.precision.to_dict() != publication.precision
                or successor.fingerprint != publication.successor_state_fingerprint
                or successor.content_integrity_fingerprint != publication.successor_state_content_seal
                or successor.parent_fingerprint != parent.fingerprint
                or successor.toolpath_fingerprint != publication.semantic_material_removal_fingerprint
                or successor.setup_fingerprint != publication.setup_fingerprint
                or successor.stock_fingerprint != publication.stock_fingerprint
                or successor.engine_version != publication.engine_version
                or successor.precision.to_dict() != publication.precision
                # The state document and v2 row are both project-local mutable
                # bytes. Recompute the heightfield from independently validated
                # parent/tool/artifact authority so coherently resealing both
                # files cannot promote altered grid bytes.
                or recomputed_successor.fingerprint != successor.fingerprint
                or recomputed_successor.content_integrity_fingerprint
                != successor.content_integrity_fingerprint):
            raise CamValidationError("Rest Contour v2 material-state provenance is inconsistent")
        if material_only and material_operation_state:
            from hms_cadcam.cam.application.rest_contour_lifecycle import (
                RestContourLifecycle,
                RestContourLifecycleStatus,
            )
            from hms_cadcam.cam.application.rest_contour_toolpath import (
                generate_rest_contour_phase_b,
            )
            from hms_cadcam.cam.domain.rest_contour import (
                RestContourDiagnosticCode,
                RestContourValidationError,
            )

            current_context = self._rest_contour_context(
                project_root,
                operation_id,
                profile_resolver,
                cancellation=cancellation,
            )
            current_preparation = RestContourLifecycle().prepare(current_context)
            if (
                current_preparation.status is not RestContourLifecycleStatus.PREPARED
                or current_preparation.prepared is None
            ):
                if (
                    current_preparation.status is RestContourLifecycleStatus.FAILURE
                    and current_preparation.diagnostic_code
                    is RestContourDiagnosticCode.CANCELLED
                ):
                    raise RestContourValidationError(
                        RestContourDiagnosticCode.CANCELLED,
                        current_preparation.message,
                    )
                raise CamValidationError(
                    "Rest Contour material-only replay cannot derive current removal semantics"
                )
            current_candidate = generate_rest_contour_phase_b(
                current_preparation.prepared.phase_b_prepared,
                cancellation=cancellation,
            )
            if (
                compute_material_removal_fingerprint(current_candidate.artifact)
                != publication.semantic_material_removal_fingerprint
            ):
                raise CamValidationError(
                    "Rest Contour material-only replay removal semantics changed"
                )
            return publication, artifact, successor
        if exact_operation_state:
            from hms_cadcam.cam.application.rest_contour_lifecycle import (
                RestContourLifecycle,
                RestContourLifecycleStatus,
            )
            from hms_cadcam.cam.application.rest_contour_toolpath import (
                generate_rest_contour_phase_b,
            )
            from hms_cadcam.cam.domain.rest_contour import (
                RestContourDiagnosticCode,
                RestContourValidationError,
            )

            replay_context = self._rest_contour_context(
                project_root,
                operation_id,
                profile_resolver,
                cancellation=cancellation,
                replay_generation=artifact.computation_token.generation,
            )
            replay_preparation = RestContourLifecycle().prepare(replay_context)
            if replay_preparation.status is not RestContourLifecycleStatus.PREPARED:
                if (
                    replay_preparation.status is RestContourLifecycleStatus.FAILURE
                    and replay_preparation.diagnostic_code
                    is RestContourDiagnosticCode.CANCELLED
                ):
                    raise RestContourValidationError(
                        RestContourDiagnosticCode.CANCELLED,
                        replay_preparation.message,
                    )
                raise CamValidationError(
                    "Rest Contour replay output cannot be independently prepared"
                )
            replay_candidate = generate_rest_contour_phase_b(
                replay_preparation.prepared.phase_b_prepared,
                cancellation=cancellation,
            )
            if (
                ContentFingerprint.from_payload(
                    _rest_contour_artifact_output_payload(replay_candidate.artifact)
                )
                != ContentFingerprint.from_payload(
                    _rest_contour_artifact_output_payload(artifact)
                )
            ):
                raise CamValidationError(
                    "Rest Contour persisted output differs from deterministic current output"
                )
        from hms_cadcam.cam.application.rest_contour_lifecycle import (
            derive_rest_contour_input_fingerprint,
        )
        from hms_cadcam.cam.domain.rest_contour import RestContourValidationError

        try:
            current_context = self._rest_contour_context(
                project_root,
                operation_id,
                profile_resolver,
                cancellation=cancellation,
            )
            expected_input = derive_rest_contour_input_fingerprint(current_context)
        except RestContourValidationError as error:
            from hms_cadcam.cam.domain.rest_contour import RestContourDiagnosticCode

            if error.code is RestContourDiagnosticCode.CANCELLED:
                raise
            raise CamValidationError(
                "Rest Contour v2 current input authority cannot be derived"
            ) from error
        except (CamValidationError, CamChildNotFoundError, ToolpathArtifactStoreError,
                OSError, RuntimeError, TypeError, ValueError) as error:
            raise CamValidationError(
                "Rest Contour v2 current input authority cannot be derived"
            ) from error
        if expected_input is None or publication.input_fingerprint != expected_input:
            raise CamValidationError("Rest Contour v2 input fingerprint is not current")
        return publication, artifact, successor

    def _rehydrate_rest_contour_completion(
        self,
        project_root: Path,
        operation_id: OperationId,
        *,
        profile_resolver,
        cancellation,
    ):
        """Return a prior COMPLETE publication only after full v2 verification."""
        from hms_cadcam.cam.application.rest_contour_lifecycle import (
            RestContourLifecycleStatus,
        )
        from hms_cadcam.cam.application.rest_contour_toolpath import RestContourPhaseBPublication
        from hms_cadcam.cam.domain.rest_contour import (
            RestContourDiagnosticCode,
            RestContourValidationError,
        )

        dependency = next((item for item in self._snapshot.material_state_dependencies
                           if item.consumer_operation_id == operation_id), None)
        if dependency is None:
            return None
        if dependency.successor_publication is None:
            return self._rest_contour_failure(
                RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                "Rest Contour existing dependency lacks a v2 COMPLETE record",
            )
        operation = _find_operation(self._snapshot, operation_id)
        metadata = next(
            (item for item in self._snapshot.artifacts
             if item.operation_id == operation_id),
            None,
        )
        if metadata is not None:
            try:
                prior_artifact = self._artifact_store.load(project_root, metadata)
            except ToolpathArtifactStoreError:
                prior_artifact = None
            if (
                prior_artifact is not None
                and _is_feed_only_material_artifact(operation, prior_artifact)
            ):
                # The old v2 result remains eligible only as a downstream
                # material-state producer. A direct request for this operation
                # must run the current feed/spindle parameters and replace it.
                return None
        try:
            evidence, artifact, successor = self._load_rest_contour_completion(
                project_root,
                operation_id,
                profile_resolver=profile_resolver,
                cancellation=cancellation,
            )
            operation = _find_operation(self._snapshot, operation_id)
        except RestContourValidationError as error:
            return self._rest_contour_failure(error.code, str(error))
        except (CamValidationError, CamChildNotFoundError, ToolpathArtifactStoreError,
                OSError, RuntimeError, TypeError, ValueError) as error:
            return self._rest_contour_failure(
                RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                f"Rest Contour v2 completion is invalid: {error}",
            )
        # This is a semantic replay result, deliberately not a R271 Prepared
        # or Candidate.  It prevents duplicate generation after a fresh reopen
        # while preserving the sole authority of the persisted COMPLETE record.
        preparation = _RestContourRehydratedPreparation(RestContourLifecycleStatus.PREPARED)
        return _RestContourRehydratedResult(
            RestContourLifecycleStatus.SUCCESS,
            preparation,
            RestContourPhaseBPublication(operation, metadata := next(
                item for item in self._snapshot.artifacts if item.operation_id == operation_id
            ), artifact, successor),
            evidence,
        )

    def _publish_rest_contour_candidate(self, candidate, phase_b_context, project_root: Path):
        """Publish R271 bytes then atomically stage v2 metadata in this snapshot."""
        from hms_cadcam.cam.application.rest_contour_toolpath import publish_rest_contour_phase_b
        from hms_cadcam.cam.persistence.models import (
            MaterialStateDependency, MaterialStateSuccessorPublication,
        )

        publication = publish_rest_contour_phase_b(
            candidate, current_context=phase_b_context, project_root=project_root,
            artifact_store=self._artifact_store, material_state_store=self._material_states,
        )
        parent = candidate.prepared.predecessor_state
        successor = publication.successor_state
        evidence = MaterialStateSuccessorPublication.create(
            consumer_operation_id=publication.operation.operation_id,
            artifact_id=publication.artifact.artifact_id,
            artifact_fingerprint=publication.artifact.artifact_fingerprint,
            input_fingerprint=publication.artifact.input_fingerprint,
            semantic_material_removal_fingerprint=candidate.successor_provenance.toolpath_fingerprint,
            parent_state_fingerprint=parent.fingerprint,
            parent_state_content_seal=parent.content_integrity_fingerprint,
            successor_state_fingerprint=successor.fingerprint,
            successor_state_content_seal=successor.content_integrity_fingerprint,
            setup_fingerprint=successor.setup_fingerprint,
            stock_fingerprint=successor.stock_fingerprint,
            engine_version=successor.engine_version,
            precision=successor.precision.to_dict(),
        )
        dependency = MaterialStateDependency(
            publication.operation.operation_id,
            phase_b_context.phase_a_inputs.foundation.material.candidate.producer_operation_id,
            parent.fingerprint, parent.toolpath_fingerprint, parent.setup_fingerprint,
            parent.stock_fingerprint, parent.engine_version, parent.precision.to_dict(), evidence,
            _material_removal_operation_fingerprint(
                _find_operation(
                    self._snapshot,
                    phase_b_context.phase_a_inputs.foundation.material.candidate.producer_operation_id,
                )
            ),
        )
        staged = replace(
            self._snapshot,
            artifacts=tuple(value for value in self._snapshot.artifacts
                            if value.operation_id != publication.operation.operation_id)
            + (publication.artifact_metadata,),
            material_state_dependencies=tuple(
                value for value in self._snapshot.material_state_dependencies
                if value.consumer_operation_id != publication.operation.operation_id
            ) + (dependency,),
        )
        self._snapshot = _replace_operation(staged, publication.operation)
        return publication

    def restore_rest_contour_snapshot(self, snapshot: CamProjectSnapshot) -> CamProjectSnapshot:
        """Discard an uncommitted Rest publication after project persistence fails.

        This intentionally does not alter ``_persisted``.  Artifact/state bytes
        may remain as unreferenced files, but without SQLite metadata they are
        not project authority and cannot be rediscovered by this path.
        """
        if not isinstance(snapshot, CamProjectSnapshot):
            raise TypeError("Rest Contour rollback snapshot is invalid")
        with self._lock:
            self._snapshot = _clone_snapshot(snapshot)
            self._rest_contour_preparations.clear()
            self._rest_contour_completed.clear()
            return _clone_snapshot(self._snapshot)

    def resolve_persisted_material_state(self, project_root: Path, operation_id: OperationId):
        """Validate and resolve persisted Rest provenance without replaying toolpaths."""
        from hms_cadcam.cam.application.rest_pocket import (
            MaterialStateResolution, MaterialStateResolutionStatus,
        )
        with self._lock:
            dependency = next((item for item in self._snapshot.material_state_dependencies
                               if item.consumer_operation_id == operation_id), None)
            if dependency is None:
                return MaterialStateResolution(MaterialStateResolutionStatus.NO_COMPATIBLE_MATERIAL_STATE,
                                               message="Persisted material-state dependency is missing")
            try:
                consumer = _find_operation(self._snapshot, operation_id)
                producer = _find_operation(self._snapshot, dependency.producer_operation_id)
                setup = next(value for job in self._snapshot.jobs for value in job.setups
                             if value.setup_id == consumer.setup_id)
            except (CamChildNotFoundError, StopIteration):
                return MaterialStateResolution(MaterialStateResolutionStatus.CORRUPT,
                                               message="Persisted dependency operation is missing")
            if producer.setup_id != consumer.setup_id:
                return MaterialStateResolution(MaterialStateResolutionStatus.STALE,
                                               message="Producer Setup no longer matches consumer")
            if DirtyReason.UPSTREAM_CHANGED in consumer.artifact_state.dirty_reasons:
                return MaterialStateResolution(
                    MaterialStateResolutionStatus.STALE,
                    message="Material-removal upstream authority changed",
                )
            setup_fp = material_state_setup_fingerprint(setup)
            stock_fp = ContentFingerprint.from_payload(setup.stock.to_dict())
            if dependency.setup_fingerprint != setup_fp or dependency.stock_fingerprint != stock_fp:
                return MaterialStateResolution(MaterialStateResolutionStatus.STALE,
                                               message="Persisted Setup or Stock provenance changed")
            if dependency.engine_version != MATERIAL_STATE_ENGINE_VERSION:
                return MaterialStateResolution(MaterialStateResolutionStatus.UNSUPPORTED,
                                               message="Persisted material-state engine is unsupported")
            loaded = self._material_states.load(project_root, dependency.parent_state_fingerprint)
            if loaded.status is MaterialStateLoadStatus.MISSING:
                return MaterialStateResolution(MaterialStateResolutionStatus.NO_COMPATIBLE_MATERIAL_STATE,
                                               message="Persisted parent material state is missing")
            if loaded.status is MaterialStateLoadStatus.CORRUPT:
                return MaterialStateResolution(MaterialStateResolutionStatus.CORRUPT,
                                               message=loaded.message or "Persisted material state is corrupt")
            if loaded.status is not MaterialStateLoadStatus.VALID or loaded.state is None:
                return MaterialStateResolution(MaterialStateResolutionStatus.STALE,
                                               message=loaded.message or "Persisted material state is incompatible")
            state = loaded.state
            if (state.toolpath_fingerprint != dependency.producer_toolpath_fingerprint
                    or state.setup_fingerprint != dependency.setup_fingerprint
                    or state.stock_fingerprint != dependency.stock_fingerprint
                    or state.engine_version != dependency.engine_version
                    or state.precision.to_dict() != dependency.precision):
                return MaterialStateResolution(MaterialStateResolutionStatus.STALE,
                                               message="Persisted material-state provenance mismatch")
            metadata = next((item for item in self._snapshot.artifacts
                             if item.operation_id == producer.operation_id), None)
            if metadata is None:
                return MaterialStateResolution(MaterialStateResolutionStatus.STALE,
                                               message="Producer toolpath metadata is missing")
            try:
                artifact = self._artifact_store.load(project_root, metadata)
            except ToolpathArtifactStoreError:
                return MaterialStateResolution(MaterialStateResolutionStatus.CORRUPT,
                                               message="Producer toolpath artifact is corrupt")
            if compute_material_removal_fingerprint(artifact) != dependency.producer_toolpath_fingerprint:
                return MaterialStateResolution(MaterialStateResolutionStatus.STALE,
                                               message="Producer semantic toolpath changed")
            status = (
                MaterialStateResolutionStatus.RESOLVED
                if state.has_rest_material
                else MaterialStateResolutionStatus.NO_REST_MATERIAL
            )
            return MaterialStateResolution(status, state, producer.operation_id)

    def _remember_pocket_incremental_template(
        self,
        operation_id: OperationId,
        value: tuple[ContentFingerprint, ContentFingerprint, ToolpathArtifact],
    ) -> None:
        """Retain bounded validated Pocket topology for feed-only recalculation."""
        self._pocket_incremental_templates.pop(operation_id, None)
        event_count = len(value[2].events)
        if event_count > _POCKET_INCREMENTAL_TEMPLATE_MAX_EVENTS:
            return
        self._pocket_incremental_templates[operation_id] = value
        total = sum(
            len(template[2].events)
            for template in self._pocket_incremental_templates.values()
        )
        while total > _POCKET_INCREMENTAL_TEMPLATE_MAX_EVENTS:
            _removed_id, removed = self._pocket_incremental_templates.popitem(last=False)
            total -= len(removed[2].events)

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
                cached = None if operation.artifact_state.status is ArtifactStatus.VALID else self._optimization_cache_hit(
                    project_root, operation, inputs.input_fingerprint
                )
                if cached is not None:
                    metadata = self._artifact_store.publish(project_root, cached)
                    restored = self._restore_optimization_hit(operation, cached)
                    staged = replace(self._snapshot, artifacts=tuple(
                        item for item in self._snapshot.artifacts if item.operation_id != operation_id
                    ) + (metadata,))
                    try:
                        self._snapshot = _replace_operation(staged, restored)
                    except CamChildNotFoundError:
                        self._snapshot = staged
                    self._calculation_timings[operation_id] = CalculationTiming(
                        str(operation_id), (PhaseTiming("final_assembly", 0, "CACHE_HIT"),)
                    )
                    return DrillingComputeResult(restored, cached, True)
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
                self._publish_optimization_cache(project_root, publish.artifact)
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
                cached = None if operation.artifact_state.status is ArtifactStatus.VALID else self._optimization_cache_hit(
                    project_root, operation, inputs.input_fingerprint
                )
                if cached is not None:
                    metadata = self._artifact_store.publish(project_root, cached)
                    restored = self._restore_optimization_hit(operation, cached)
                    staged = replace(self._snapshot, artifacts=tuple(
                        item for item in self._snapshot.artifacts if item.operation_id != operation_id
                    ) + (metadata,))
                    try:
                        self._snapshot = _replace_operation(staged, restored)
                    except CamChildNotFoundError:
                        self._snapshot = staged
                    self._calculation_timings[operation_id] = CalculationTiming(
                        str(operation_id), (PhaseTiming("final_assembly", 0, "CACHE_HIT"),)
                    )
                    return TappingComputeResult(restored, cached, True)
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
                self._publish_optimization_cache(project_root, publish.artifact)
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
                cached = None if operation.artifact_state.status is ArtifactStatus.VALID else self._optimization_cache_hit(
                    project_root, operation, inputs.input_fingerprint
                )
                if cached is not None:
                    metadata = self._artifact_store.publish(project_root, cached)
                    restored = self._restore_optimization_hit(operation, cached)
                    staged = replace(self._snapshot, artifacts=tuple(
                        item for item in self._snapshot.artifacts if item.operation_id != operation_id
                    ) + (metadata,))
                    try:
                        self._snapshot = _replace_operation(staged, restored)
                    except CamChildNotFoundError:
                        self._snapshot = staged
                    self._calculation_timings[operation_id] = CalculationTiming(
                        str(operation_id), (PhaseTiming("final_assembly", 0, "CACHE_HIT"),)
                    )
                    return ReamingComputeResult(restored, cached, True)
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
                self._publish_optimization_cache(project_root, publish.artifact)
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
                cached = None if operation.artifact_state.status is ArtifactStatus.VALID else self._optimization_cache_hit(
                    project_root, operation, inputs.input_fingerprint
                )
                if cached is not None:
                    metadata = self._artifact_store.publish(project_root, cached)
                    restored = self._restore_optimization_hit(operation, cached)
                    staged = replace(self._snapshot, artifacts=tuple(
                        item for item in self._snapshot.artifacts if item.operation_id != operation_id
                    ) + (metadata,))
                    try:
                        self._snapshot = _replace_operation(staged, restored)
                    except CamChildNotFoundError:
                        self._snapshot = staged
                    self._calculation_timings[operation_id] = CalculationTiming(
                        str(operation_id), (PhaseTiming("final_assembly", 0, "CACHE_HIT"),)
                    )
                    return BoringComputeResult(restored, cached, True)
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
                self._publish_optimization_cache(project_root, publish.artifact)
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


def _rest_contour_completion_signature(
    snapshot: CamProjectSnapshot,
    dependency: MaterialStateDependency,
) -> tuple[object, ...] | None:
    """Return only the aggregate facts that authorize one v2 completion.

    This intentionally excludes unrelated jobs/tools.  A mutation to any
    consumed Rest input drops the completed record and its artifact metadata;
    an unrelated project edit keeps the record available for exact v2 reuse.
    """
    try:
        consumer = _find_operation(snapshot, dependency.consumer_operation_id)
        producer = _find_operation(snapshot, dependency.producer_operation_id)
        if consumer.setup_id != producer.setup_id:
            return None
        setup = _find_setup(snapshot, consumer.setup_id)
        edge = tuple(
            item for item in setup.operation_tree.dependency_graph.edges
            if item.target_operation_id == consumer.operation_id
            and item.kind.value == "material_state"
        )
        consumer_assembly = next(
            (item for item in snapshot.tool_assemblies
             if item.assembly_id == consumer.tool_assembly.assembly_id), None,
        )
        producer_assembly = next(
            (item for item in snapshot.tool_assemblies
             if item.assembly_id == producer.tool_assembly.assembly_id), None,
        )
        if consumer_assembly is None or producer_assembly is None:
            return None
        consumer_tool = _find_tool(snapshot, consumer_assembly.tool_id)
        producer_tool = _find_tool(snapshot, producer_assembly.tool_id)
        requirement = consumer.machine_requirement
        machine = None if requirement is None else next(
            (item for item in snapshot.machine_definitions
             if item.machine_id == requirement.machine_id), None,
        )
        if machine is None:
            return None
        if not any(
            item.operation_id == producer.operation_id
            for item in snapshot.artifacts
        ):
            return None
        holder = None if consumer_assembly.holder_id is None else next(
            (item for item in snapshot.holder_definitions
             if item.holder_id == consumer_assembly.holder_id), None,
        )
        return (
            _material_removal_operation_authority(consumer),
            _material_removal_operation_authority(producer),
            setup.stock, setup.wcs, edge,
            consumer_assembly, consumer_tool, holder, machine,
            producer_assembly, producer_tool,
        )
    except (CamChildNotFoundError, StopIteration):
        return None


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


def _find_setup(snapshot: CamProjectSnapshot, setup_id: SetupId) -> Setup:
    """Resolve one Setup by stable identity; list position is never authority."""
    for job in snapshot.jobs:
        for setup in job.setups:
            if setup.setup_id == setup_id:
                return setup
    raise CamChildNotFoundError(f"Setup does not exist: {setup_id}")


def _find_job_for_setup(snapshot: CamProjectSnapshot, setup_id: SetupId) -> CamJob:
    for job in snapshot.jobs:
        if any(setup.setup_id == setup_id for setup in job.setups):
            return job
    raise CamChildNotFoundError(f"Setup does not exist: {setup_id}")


def _replace_setup(snapshot: CamProjectSnapshot, changed: Setup) -> CamProjectSnapshot:
    """Replace an owned Setup while preserving aggregate ownership and order."""
    jobs: list[CamJob] = []
    found = False
    for job in snapshot.jobs:
        setups = tuple(changed if setup.setup_id == changed.setup_id else setup for setup in job.setups)
        if setups != job.setups:
            found = True
            jobs.append(CamJob(
                job.job_id, job.name, revision=job.revision,
                setups=setups, active_setup_id=job.active_setup_id,
            ))
        else:
            jobs.append(job)
    if not found:
        raise CamChildNotFoundError(f"Setup does not exist: {changed.setup_id}")
    return replace(snapshot, jobs=tuple(jobs))


def _material_removal_operation_authority(operation: Operation) -> tuple[object, ...]:
    """Return operation inputs that may change its physical removal semantics."""
    non_removal_parameters = {
        "cutting_feed_rate",
        "plunge_feed_rate",
        "spindle_speed",
    }
    removal_parameters = tuple(
        (key, value)
        for key, value in operation.parameters.values
        if key not in non_removal_parameters
    )
    return (
        operation.enabled,
        operation.parameters.strategy_key,
        operation.parameters.strategy_version,
        operation.parameters.schema_version,
        removal_parameters,
        operation.geometry_inputs,
        operation.tool_assembly,
        operation.machine_requirement,
    )


def _material_removal_operation_fingerprint(operation: Operation) -> ContentFingerprint:
    """Seal editable producer facts while excluding proven non-removal controls."""
    if not isinstance(operation, Operation):
        raise TypeError("Material-removal operation authority is invalid")
    payload = operation.to_dict()
    payload.pop("revision")
    payload.pop("artifact_state")
    payload.pop("diagnostics")
    payload["parameters"] = dict(payload["parameters"])
    payload["parameters"]["values"] = [
        value for value in payload["parameters"]["values"]
        if value["name"] not in {
            "cutting_feed_rate", "plunge_feed_rate", "spindle_speed",
        }
    ]
    return ContentFingerprint.from_payload({
        "format": "HMS_CAM_MATERIAL_REMOVAL_OPERATION_AUTHORITY",
        "format_version": 1,
        "operation": payload,
    })


def _rest_contour_artifact_output_payload(artifact: ToolpathArtifact) -> dict[str, object]:
    """Canonical full Rest output with only publication identities removed."""
    if not isinstance(artifact, ToolpathArtifact):
        raise TypeError("Rest Contour artifact output is invalid")
    payload = artifact_to_dict(artifact)
    payload.pop("artifact_id")
    payload.pop("artifact_fingerprint")
    payload.pop("created_at")
    payload["computation_token"] = dict(payload["computation_token"])
    payload["computation_token"]["value"] = "<replay-token>"
    return payload


def _rest_finishing_artifact_output_payload(
    artifact: ToolpathArtifact,
) -> dict[str, object]:
    """Canonical R274 machining output without publication lifecycle IDs."""
    payload = _rest_contour_artifact_output_payload(artifact)
    payload.pop("operation_revision")
    payload.pop("input_fingerprint")
    payload["computation_token"]["generation"] = "<replay-generation>"
    payload["events"] = [
        {
            **event,
            # Event UUIDs are publication identities minted per generation;
            # their position and every machining field remain authoritative.
            "event_id": f"<replay-event-{index}>",
        }
        for index, event in enumerate(payload["events"])
    ]
    return payload


def _is_feed_only_material_artifact(operation: Operation, artifact: ToolpathArtifact) -> bool:
    """Recognize an old artifact retained solely for removal-state authority."""
    state = operation.artifact_state
    feed_only_names = {
        "cutting_feed_rate", "plunge_feed_rate", "spindle_speed",
    }
    return (
        state.status is ArtifactStatus.DIRTY
        and state.token is None
        and state.dirty_reasons == (DirtyReason.PARAMETERS_CHANGED,)
        and state.input_fingerprint == artifact.input_fingerprint
        and state.artifact_fingerprint is None
        and state.generation == artifact.computation_token.generation
        and artifact.source_operation_id == operation.operation_id
        and artifact.operation_revision.value < operation.revision.value
        # Generic producers have no strategy-specific historical decoder here.
        # Preserve them only when their complete editable shape proves that no
        # geometry or material-removal parameter exists to be hidden behind a
        # forged PARAMETERS_CHANGED label. Producers with richer shapes fail
        # closed until regenerated through their official strategy path.
        and (
            operation.strategy_key == "rest_contour_3axis"
            or (
                not operation.geometry_inputs
                and all(
                    name in feed_only_names
                    for name, _value in operation.parameters.values
                )
            )
        )
    )


def _find_tool(
    snapshot: CamProjectSnapshot, tool_id: ToolDefinitionId
) -> ToolDefinition:
    for tool in snapshot.tool_definitions:
        if tool.tool_id == tool_id:
            return tool
    raise CamChildNotFoundError(f"Tool does not exist: {tool_id}")


def _find_profile(
    tool: ToolDefinition, profile_id: ToolProgramProfileId
) -> ToolProgramProfile:
    for profile in tool.program_profiles:
        if profile.profile_id == profile_id:
            return profile
    raise CamChildNotFoundError(f"Tool profile does not exist: {profile_id}")


def _tool_profile_timestamp(previous=None):
    current = utc_profile_now()
    return current if previous is None or current >= previous else previous


def _stale_tool_strategy_operations(
    snapshot: CamProjectSnapshot,
    tool_id: ToolDefinitionId,
    strategy_id: str,
) -> tuple[CamProjectSnapshot, tuple[OperationId, ...]]:
    """Dirty only operations whose selected assembly uses this Tool/strategy."""
    assembly_ids = {
        item.assembly_id
        for item in snapshot.tool_assemblies
        if item.tool_id == tool_id
    }
    result = snapshot
    affected: list[OperationId] = []
    for job in snapshot.jobs:
        for setup in job.setups:
            for operation in setup.operation_tree.operations:
                if (
                    operation.tool_assembly.assembly_id not in assembly_ids
                    or operation.strategy_key != strategy_id
                ):
                    continue
                state = operation.artifact_state.mark_dirty(
                    DirtyReason.PARAMETERS_CHANGED
                )
                if state == operation.artifact_state:
                    continue
                affected.append(operation.operation_id)
                result = _replace_operation(
                    result, replace(operation, artifact_state=state)
                )
    return result, tuple(sorted(affected, key=str))


def _stale_geometry_source_operations(
    snapshot: CamProjectSnapshot,
    source_ids: frozenset[object],
) -> tuple[CamProjectSnapshot, tuple[OperationId, ...]]:
    """Dirty only operations carrying an explicit reference to changed sources."""
    result = snapshot
    affected: list[OperationId] = []
    for job in snapshot.jobs:
        for setup in job.setups:
            for operation in setup.operation_tree.operations:
                if not any(
                    item.reference.source_id in source_ids
                    for item in operation.geometry_inputs
                ):
                    continue
                changed_state = operation.artifact_state.mark_dirty(
                    DirtyReason.GEOMETRY_CHANGED
                )
                if changed_state == operation.artifact_state:
                    continue
                affected.append(operation.operation_id)
                result = _replace_operation(
                    result,
                    replace(operation, artifact_state=changed_state),
                )
    return result, tuple(sorted(affected, key=str))


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
