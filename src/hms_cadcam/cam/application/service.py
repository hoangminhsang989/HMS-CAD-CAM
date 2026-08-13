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
    HolderDefinition, MachineDefinition, OperationId, OperationTree, Setup,
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
            operation_ids = {operation.operation_id for job in candidate.jobs
                             for setup in job.setups
                             for operation in setup.operation_tree.operations}
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
