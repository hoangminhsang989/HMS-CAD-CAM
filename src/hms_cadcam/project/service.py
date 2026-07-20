"""Application-facing orchestration for HMS project workflows."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from collections.abc import Callable
from uuid import UUID

from hms_cadcam.project.autosave import AutosaveManager, AutosaveSnapshot
from hms_cadcam.project.cad_state import CadViewState, default_cad_view_state
from hms_cadcam.project.cad_state_store import CadViewStateStore
from hms_cadcam.project.constants import TEMP_DIRECTORY
from hms_cadcam.project.creator import ProjectCreator
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import (
    ProjectError,
    RecoveryRequiredError,
    RecoverySnapshotInvalidError,
    ReplacedProjectRecoveryRequiredError,
    UnsavedChangesError,
)
from hms_cadcam.project.loader import ProjectLoader
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.filesystem import project_target_path
from hms_cadcam.project.models import ProjectSession, UnitSystem
from hms_cadcam.project.owned_directories import cleanup_stale_owned_directories
from hms_cadcam.project.recent_projects import RecentProjectEntry, RecentProjectsService
from hms_cadcam.project.recovery import (
    RecoveryAssessment,
    RecoveryManager,
    ReplacedProjectAssessment,
)
from hms_cadcam.project.saver import ProjectSaver
from hms_cadcam.project.session_lock import SessionLockManager
from hms_cadcam.project.validator import ProjectValidator
from hms_cadcam.cam.application import (
    CamApplicationService, DrillingComputeResult, FacingComputeResult, PocketComputeResult,
    BoringComputeResult, ReamingComputeResult, TappingComputeResult,
)
from hms_cadcam.cam.persistence import CamProjectSnapshot, CamSqliteRepository, ToolpathArtifactStore
from hms_cadcam.cam.domain import (
    DrillDepthDefinition, DrillGeometryInput, GeometryReference, OperationId,
    ResolvedContourProfile, ResolvedDrillingGeometry, ResolvedMachiningGeometry,
    ResolvedPocketGeometry,
)
from hms_cadcam.cam.application.contour import ContourComputeResult
from hms_cadcam.cam.domain.operation import ComputationToken
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.toolpath import ToolpathArtifact, ToolpathPublishResult
from hms_cadcam.cam.simulation import (
    SimulationCacheLoad,
    SimulationCacheStatus,
    SimulationCacheStore,
    SimulationInputSnapshot,
    SimulationIssueCode,
    SimulationPreflightError,
    SimulationResult,
    SimulationRunController,
    SimulationSamplingPolicy,
    build_simulation_request,
)

logger = logging.getLogger(__name__)
_CLEANUP_MINIMUM_AGE = timedelta(days=1)


class ProjectService:
    """The only project API consumed by the user interface."""

    def __init__(
        self,
        creator: ProjectCreator,
        loader: ProjectLoader,
        saver: ProjectSaver,
        validator: ProjectValidator,
        database: ProjectDatabase,
        recent_projects: RecentProjectsService,
        session_locks: SessionLockManager,
        autosave: AutosaveManager,
        recovery: RecoveryManager,
        cam_application: CamApplicationService | None = None,
        simulation_cache: SimulationCacheStore | None = None,
    ) -> None:
        self._creator = creator
        self._loader = loader
        self._saver = saver
        self._validator = validator
        self._database = database
        self._recent_projects = recent_projects
        self._session_locks = session_locks
        self._autosave = autosave
        self._recovery = recovery
        self._cam_application = cam_application or CamApplicationService()
        self._simulation_runs = SimulationRunController(
            self._cam_application.simulation_service
        )
        self._simulation_cache = simulation_cache or SimulationCacheStore()
        self._current_project: ProjectSession | None = None

    @classmethod
    def create_default(cls, config_dir: Path) -> "ProjectService":
        """Build the default project service graph for the application."""
        manifest_store = ProjectManifestStore()
        validator = ProjectValidator()
        database = ProjectDatabase()
        cad_state_store = CadViewStateStore()
        cam_repository = CamSqliteRepository()
        artifact_store = ToolpathArtifactStore()
        session_locks = SessionLockManager()
        autosave = AutosaveManager(
            manifest_store,
            validator,
            database,
            cad_state_store,
            cam_repository,
        )
        return cls(
            creator=ProjectCreator(manifest_store, validator, database),
            loader=ProjectLoader(
                manifest_store,
                validator,
                database,
                cad_state_store,
                cam_repository,
                artifact_store,
            ),
            saver=ProjectSaver(
                manifest_store,
                validator,
                database,
                cad_state_store,
                cam_repository,
                artifact_store,
            ),
            validator=validator,
            database=database,
            recent_projects=RecentProjectsService(config_dir),
            session_locks=session_locks,
            autosave=autosave,
            recovery=RecoveryManager(
                autosave,
                manifest_store,
                validator,
                database,
                session_locks,
            ),
            cam_application=CamApplicationService(artifact_store),
        )

    @property
    def current_project(self) -> ProjectSession | None:
        """Return the current session without exposing persistence adapters."""
        return self._current_project

    @property
    def has_project(self) -> bool:
        """Return whether a project is currently open."""
        return self._current_project is not None

    @property
    def is_dirty(self) -> bool:
        """Return whether the current manifest has unsaved changes."""
        return bool(self._current_project and self._current_project.is_dirty)

    def new_project(
        self,
        parent_dir: Path,
        project_name: str,
        units: UnitSystem = UnitSystem.MILLIMETER,
        overwrite: bool = False,
    ) -> ProjectSession:
        """Create and select a new empty project."""
        self._cleanup_staging(parent_dir)
        self._ensure_overwrite_target_available(parent_dir, project_name, overwrite)
        session = self._creator.create(parent_dir, project_name, units, overwrite=overwrite)
        return self._activate(session)

    def create_project_from_source(
        self,
        parent_dir: Path,
        project_name: str,
        source_path: Path,
        units: UnitSystem = UnitSystem.MILLIMETER,
        overwrite: bool = False,
    ) -> ProjectSession:
        """Create and select a project containing one verified source copy."""
        self._cleanup_staging(parent_dir)
        self._ensure_overwrite_target_available(parent_dir, project_name, overwrite)
        session = self._creator.create(
            parent_dir,
            project_name,
            units,
            source_path=source_path,
            overwrite=overwrite,
        )
        return self._activate(session)

    def import_source(self, source_path: Path) -> ProjectSession:
        """Import a source into the current project."""
        session = self._require_current()
        self._saver.import_source(session, source_path)
        logger.info("Đã sao chép file nguồn %s vào %s", source_path, session.root_path)
        return session

    def open_project(
        self,
        project_root: Path,
        *,
        discard_recovery: bool = False,
    ) -> ProjectSession:
        """Open a valid project without replacing current state on failure."""
        replaced = self._recovery.inspect_replaced_for_open(project_root)
        if replaced is not None:
            raise ReplacedProjectRecoveryRequiredError(replaced)
        resolved = project_root.resolve()
        if (
            self._current_project is not None
            and self._current_project.root_path.resolve() == resolved
        ):
            session = self._loader.load(project_root)
            return self._complete_activation(session)
        manifest = self._loader.read_manifest(project_root)
        assessment = self._recovery.assess(
            project_root,
            manifest,
            self._session_locks.inspect(project_root),
        )
        if assessment.snapshot is not None and not discard_recovery:
            raise RecoveryRequiredError(assessment)
        if assessment.abnormal_close and assessment.snapshot is None:
            logger.warning(
                "Phát hiện lần đóng bất thường nhưng không có snapshot phù hợp: %s",
                project_root,
            )
        self._session_locks.acquire(project_root, manifest.project_id)
        try:
            session = self._loader.load(project_root)
            if session.manifest.project_id != manifest.project_id:
                raise ProjectError("Project identity changed while opening")
            return self._complete_activation(session)
        except Exception:
            self._session_locks.release(project_root)
            raise

    def recover_project(self, assessment: RecoveryAssessment) -> ProjectSession:
        """Recover one revalidated autosave while preserving the current session on failure."""
        manifest = self._loader.read_manifest(assessment.project_root)
        current = self._recovery.assess(
            assessment.project_root,
            manifest,
            self._session_locks.inspect(assessment.project_root),
        )
        if (
            current.snapshot is None
            or assessment.snapshot is None
            or current.snapshot.metadata.snapshot_id
            != assessment.snapshot.metadata.snapshot_id
        ):
            raise RecoverySnapshotInvalidError("Recovery candidate changed before approval")
        self._session_locks.acquire(assessment.project_root, manifest.project_id)
        try:
            self._recovery.recover(current)
            if current.snapshot is not None:
                try:
                    self._simulation_cache.copy_valid_entries(
                        current.snapshot.path,
                        assessment.project_root,
                        assessment.project_id,
                        assessment.project_id,
                    )
                except Exception:
                    logger.warning(
                        "Không thể phục hồi simulation cache từ autosave",
                        exc_info=True,
                    )
            session = self._loader.load(assessment.project_root)
            return self._complete_activation(session)
        except Exception:
            self._session_locks.release(assessment.project_root)
            raise

    def restore_replaced_and_open(
        self,
        assessment: ReplacedProjectAssessment,
    ) -> ProjectSession:
        """Restore one approved .replaced directory and continue normal opening."""
        target = self._recovery.restore_replaced(assessment)
        return self.open_project(target)

    def save(self) -> ProjectSession:
        """Persist the current project."""
        current = self._require_current()
        self.flush_simulation_cache()
        current.cam_snapshot = self._cam_application.snapshot
        session = self._saver.save(current)
        self._cam_application.mark_persisted(session.cam_snapshot)
        return session

    @property
    def cam_snapshot(self) -> CamProjectSnapshot:
        """Return the current immutable CAM project snapshot."""
        self._require_current()
        return self._cam_application.snapshot

    def stage_cam_snapshot(self, snapshot: CamProjectSnapshot) -> ProjectSession:
        """Stage complete validated CAM editable state without writing SQLite."""
        if not isinstance(snapshot, CamProjectSnapshot):
            raise TypeError("CAM project snapshot is invalid")
        session = self._require_current()
        before = self._cam_application.snapshot
        changed = self._cam_application.apply(lambda _current: snapshot)
        session.cam_snapshot = changed
        if changed != before:
            session.is_dirty = True
            self._simulation_runs.cancel_all(stale=True)
            self._remove_deleted_operation_simulations(session, before, changed)
        return session

    def apply_cam_mutation(
        self,
        mutation: Callable[[CamProjectSnapshot], CamProjectSnapshot],
    ) -> ProjectSession:
        """Apply one atomic CAM snapshot mutation and mark project dirty on change."""
        session = self._require_current()
        before = self._cam_application.snapshot
        changed = self._cam_application.apply(mutation)
        session.cam_snapshot = changed
        if changed != before:
            session.is_dirty = True
            self._simulation_runs.cancel_all(stale=True)
            self._remove_deleted_operation_simulations(session, before, changed)
        return session

    def execute_cam_command(
        self,
        command: Callable[[CamApplicationService], CamProjectSnapshot],
        *,
        expected_generation: int | None = None,
    ) -> CamProjectSnapshot:
        """Execute one CAM application command and propagate project dirty state."""
        session = self._require_current()
        if (
            expected_generation is not None
            and expected_generation != self._cam_application.generation
        ):
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        try:
            changed = self._cam_application.execute(command)
        except Exception:
            session.cam_snapshot = self._cam_application.snapshot
            raise
        session.cam_snapshot = changed
        if changed != before:
            session.is_dirty = True
            self._simulation_runs.cancel_all(stale=True)
            self._remove_deleted_operation_simulations(session, before, changed)
        return changed

    @property
    def cam_generation(self) -> int:
        """Return the active CAM project generation for stale-signal guards."""
        self._require_current()
        return self._cam_application.generation

    def register_toolpath_artifact(
        self,
        operation_id: OperationId,
        candidate: ToolpathArtifact,
        token: ComputationToken,
        current_input: DependencyFingerprint,
    ) -> ToolpathPublishResult:
        """Publish a verified artifact file and stage its metadata for Save."""
        session = self._require_current()
        result = self._cam_application.register_artifact(
            session.root_path, operation_id, candidate, token, current_input
        )
        session.cam_snapshot = self._cam_application.snapshot
        if result.accepted or session.cam_snapshot != session.persisted_cam_snapshot:
            session.is_dirty = True
            self._simulation_runs.mark_stale(
                operation_id,
                "Source ToolpathArtifact was recomputed",
            )
        return result

    def compute_facing(self, operation_id: OperationId,
                       *, expected_generation: int | None = None,
                       face_resolver: Callable[[GeometryReference], ResolvedMachiningGeometry] | None = None,
                       ) -> FacingComputeResult:
        """Generate/publish one Facing operation through the project lifecycle gateway."""
        session = self._require_current()
        if expected_generation is not None and expected_generation != self._cam_application.generation:
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        result = self._cam_application.compute_facing(
            session.root_path, operation_id, face_resolver=face_resolver
        )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
        return result

    def compute_contour(self, operation_id: OperationId,
                        *, expected_generation: int | None = None,
                        profile_resolver: Callable[[GeometryReference], ResolvedContourProfile] | None = None,
                        ) -> ContourComputeResult:
        """Generate/publish one 2D Contour through the project lifecycle gateway."""
        session = self._require_current()
        if expected_generation is not None and expected_generation != self._cam_application.generation:
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        result = self._cam_application.compute_contour(
            session.root_path, operation_id, profile_resolver=profile_resolver
        )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
        return result

    def compute_pocket(self, operation_id: OperationId,
                       *, expected_generation: int | None = None,
                       geometry_resolver: Callable[[GeometryReference], ResolvedPocketGeometry] | None = None,
                       ) -> PocketComputeResult:
        """Generate/publish one Pocket operation through the project lifecycle gateway."""
        session = self._require_current()
        if expected_generation is not None and expected_generation != self._cam_application.generation:
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        result = self._cam_application.compute_pocket(
            session.root_path, operation_id, geometry_resolver=geometry_resolver
        )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
        return result

    def compute_drilling(
        self,
        operation_id: OperationId,
        *,
        expected_generation: int | None = None,
        geometry_resolver: Callable[
            [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
        ] | None = None,
    ) -> DrillingComputeResult:
        """Generate/publish one Drilling operation through the project gateway."""
        session = self._require_current()
        if (
            expected_generation is not None
            and expected_generation != self._cam_application.generation
        ):
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        result = self._cam_application.compute_drilling(
            session.root_path, operation_id, geometry_resolver=geometry_resolver
        )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
        return result

    def compute_tapping(
        self,
        operation_id: OperationId,
        *,
        expected_generation: int | None = None,
        geometry_resolver: Callable[
            [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
        ] | None = None,
    ) -> TappingComputeResult:
        """Generate/publish one Tapping operation through the project gateway."""
        session = self._require_current()
        if (
            expected_generation is not None
            and expected_generation != self._cam_application.generation
        ):
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        result = self._cam_application.compute_tapping(
            session.root_path,
            operation_id,
            geometry_resolver=geometry_resolver,
        )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
        return result

    def compute_reaming(
        self,
        operation_id: OperationId,
        *,
        expected_generation: int | None = None,
        geometry_resolver: Callable[
            [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
        ] | None = None,
    ) -> ReamingComputeResult:
        """Generate/publish one Reaming operation through the project gateway."""
        session = self._require_current()
        if (
            expected_generation is not None
            and expected_generation != self._cam_application.generation
        ):
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        result = self._cam_application.compute_reaming(
            session.root_path,
            operation_id,
            geometry_resolver=geometry_resolver,
        )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
        return result

    def compute_boring(
        self,
        operation_id: OperationId,
        *,
        expected_generation: int | None = None,
        geometry_resolver: Callable[
            [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
        ] | None = None,
    ) -> BoringComputeResult:
        """Generate/publish one Boring operation through the project gateway."""
        session = self._require_current()
        if (
            expected_generation is not None
            and expected_generation != self._cam_application.generation
        ):
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        result = self._cam_application.compute_boring(
            session.root_path,
            operation_id,
            geometry_resolver=geometry_resolver,
        )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
        return result

    def load_toolpath_artifact(self, operation_id: OperationId) -> ToolpathArtifact | None:
        """Load one verified derived artifact for presentation; never expose its path."""
        session = self._require_current()
        return self._cam_application.load_artifact(session.root_path, operation_id)

    @property
    def simulation_runs(self) -> SimulationRunController:
        """Return the runtime-only, project-bound simulation run controller."""
        self._require_current()
        return self._simulation_runs

    def capture_simulation_inputs(
        self,
        operation_id: OperationId,
        *,
        sampling_policy: SimulationSamplingPolicy | None = None,
        safe_height: float | None = None,
    ) -> SimulationInputSnapshot:
        """Capture one immutable, native-free simulation input boundary."""
        self._require_current()
        snapshot = self._cam_application.snapshot
        operation = None
        setup = None
        for job in snapshot.jobs:
            for candidate_setup in job.setups:
                candidate = next(
                    (
                        value
                        for value in candidate_setup.operation_tree.operations
                        if value.operation_id == operation_id
                    ),
                    None,
                )
                if candidate is not None:
                    operation = candidate
                    setup = candidate_setup
                    break
            if operation is not None:
                break
        if operation is None or setup is None:
            raise SimulationPreflightError(
                SimulationIssueCode.SOURCE_MISSING,
                "Simulation source operation is missing",
            )
        artifact = self.load_toolpath_artifact(operation_id)
        if artifact is None:
            raise SimulationPreflightError(
                SimulationIssueCode.SOURCE_MISSING,
                "Simulation source ToolpathArtifact is missing",
            )
        assembly = next(
            (
                value
                for value in snapshot.tool_assemblies
                if value.assembly_id == operation.tool_assembly.assembly_id
            ),
            None,
        )
        if assembly is None:
            raise SimulationPreflightError(
                SimulationIssueCode.TOOL_MISSING,
                "Simulation tool assembly is missing",
            )
        tool = next(
            (
                value
                for value in snapshot.tool_definitions
                if value.tool_id == assembly.tool_id
            ),
            None,
        )
        if tool is None:
            raise SimulationPreflightError(
                SimulationIssueCode.TOOL_MISSING,
                "Simulation tool definition is missing",
            )
        holder = next(
            (
                value
                for value in snapshot.holder_definitions
                if value.holder_id == assembly.holder_id
            ),
            None,
        )
        machine = next(
            (
                value
                for value in snapshot.machine_definitions
                if value.machine_id == artifact.machine_id
            ),
            None,
        )
        effective_safe_height = safe_height
        if effective_safe_height is None:
            parameters = dict(operation.parameters.values)
            candidate_height = parameters.get("clearance_height")
            if type(candidate_height) in {int, float}:
                effective_safe_height = float(candidate_height)
        request = build_simulation_request(
            operation=operation,
            artifact=artifact,
            setup=setup,
            tool=tool,
            assembly=assembly,
            holder=holder,
            machine=machine,
            sampling_policy=sampling_policy,
            safe_height=effective_safe_height,
        )
        return SimulationInputSnapshot(
            operation,
            artifact,
            setup,
            tool,
            assembly,
            holder,
            machine,
            request,
        )

    def load_cached_simulation(
        self,
        inputs: SimulationInputSnapshot,
    ) -> SimulationCacheLoad:
        """Load one matching cache entry without changing project dirty state."""
        session = self._require_current()
        return self._simulation_cache.load_current(
            session.root_path,
            session.manifest.project_id,
            inputs.operation.operation_id,
            inputs.request.artifact_fingerprint,
            inputs.request.input_fingerprint,
        )

    def load_cached_simulation_for_source(
        self,
        operation_id: OperationId,
        artifact_fingerprint: ContentFingerprint,
    ) -> SimulationCacheLoad:
        """Discover a cached result before restoring its sampling policy."""
        session = self._require_current()
        return self._simulation_cache.load_latest_for_source(
            session.root_path,
            session.manifest.project_id,
            operation_id,
            artifact_fingerprint,
        )

    def persist_simulation_result(
        self,
        result: SimulationResult,
    ) -> None:
        """Persist a derived current result outside SQLite."""
        session = self._require_current()
        current = self._simulation_runs.result(result.operation_id)
        if current is not result:
            raise SimulationPreflightError(
                SimulationIssueCode.STALE_RESULT,
                "Simulation result is no longer current for this project",
            )
        self._simulation_cache.write(
            session.root_path,
            session.manifest.project_id,
            result,
        )

    def flush_simulation_cache(self) -> tuple[str, ...]:
        """Flush every current runtime result; cache failure never corrupts Save."""
        session = self._require_current()
        failures: list[str] = []
        for result in self._simulation_runs.results():
            try:
                self._simulation_cache.write(
                    session.root_path,
                    session.manifest.project_id,
                    result,
                )
            except Exception as error:
                failures.append(str(error))
                logger.warning("Không thể flush simulation cache", exc_info=True)
        return tuple(failures)

    def clear_simulation_result(
        self,
        operation_id: OperationId,
        *,
        delete_cache: bool = False,
    ) -> None:
        """Clear runtime result without touching its ToolpathArtifact."""
        session = self._require_current()
        self._simulation_runs.clear_result(operation_id)
        if delete_cache:
            self._simulation_cache.delete_operation(session.root_path, operation_id)

    def _remove_deleted_operation_simulations(
        self,
        session: ProjectSession,
        before: CamProjectSnapshot,
        after: CamProjectSnapshot,
    ) -> None:
        """Remove derived runtime/cache state for operations deleted by any parent mutation."""
        before_ids = {
            operation.operation_id
            for job in before.jobs
            for setup in job.setups
            for operation in setup.operation_tree.operations
        }
        after_ids = {
            operation.operation_id
            for job in after.jobs
            for setup in job.setups
            for operation in setup.operation_tree.operations
        }
        for operation_id in sorted(before_ids - after_ids, key=str):
            self._simulation_runs.clear_result(operation_id)
            try:
                self._simulation_cache.delete_operation(session.root_path, operation_id)
            except (OSError, RuntimeError):
                logger.warning(
                    "Không thể xóa simulation cache của operation %s",
                    operation_id,
                    exc_info=True,
                )

    def cad_view_state(self, source_id: UUID) -> CadViewState:
        """Return effective pending-or-persisted state for one project source."""
        session = self._require_current()
        self._require_source(session, source_id)
        return session.cad_view_states.get(source_id, default_cad_view_state(source_id))

    def stage_cad_view_state(self, state: CadViewState) -> ProjectSession:
        """Stage validated CAD state in memory without writing project.db."""
        if not isinstance(state, CadViewState):
            raise TypeError("CAD view state is invalid")
        session = self._require_current()
        self._require_source(session, state.source_id)
        normalized = state.normalized()
        if normalized.is_default:
            session.cad_view_states.pop(state.source_id, None)
        else:
            session.cad_view_states[state.source_id] = normalized
        if session.cad_view_states != session.persisted_cad_view_states:
            session.is_dirty = True
        return session

    def autosave(
        self, *, expected_project_id: UUID | None = None
    ) -> AutosaveSnapshot | None:
        """Snapshot the current dirty session without changing its dirty state."""
        session = self._require_current()
        if (
            expected_project_id is not None
            and session.manifest.project_id != expected_project_id
        ):
            return None
        if not session.is_dirty:
            return None
        snapshot_session = ProjectSession(
            root_path=session.root_path,
            manifest=session.manifest,
            is_dirty=True,
            cad_view_states=dict(session.cad_view_states),
            persisted_cad_view_states=dict(session.persisted_cad_view_states),
            cam_snapshot=self._cam_application.snapshot,
            persisted_cam_snapshot=session.persisted_cam_snapshot,
        )
        self.flush_simulation_cache()
        autosave_snapshot = self._autosave.create_snapshot(
            snapshot_session,
            self._session_locks.session_id,
        )
        try:
            self._simulation_cache.copy_valid_entries(
                session.root_path,
                autosave_snapshot.path,
                session.manifest.project_id,
                session.manifest.project_id,
            )
        except Exception:
            logger.warning("Không thể sao chép simulation cache vào autosave", exc_info=True)
        return autosave_snapshot

    def save_as(
        self,
        parent_dir: Path,
        project_name: str,
        overwrite: bool = False,
    ) -> ProjectSession:
        """Create and select an independent copy of the current project."""
        current = self._require_current()
        current.cam_snapshot = self._cam_application.snapshot
        target = self.target_path(parent_dir, project_name)
        if target.resolve() == current.root_path.resolve():
            return self.save()
        self._cleanup_staging(parent_dir)
        self._ensure_overwrite_target_available(parent_dir, project_name, overwrite)
        self.flush_simulation_cache()
        session = self._saver.save_as(
            current,
            parent_dir,
            project_name,
            overwrite=overwrite,
        )
        try:
            self._simulation_cache.copy_valid_entries(
                current.root_path,
                session.root_path,
                current.manifest.project_id,
                session.manifest.project_id,
            )
        except Exception:
            logger.warning("Không thể sao chép simulation cache khi Save As", exc_info=True)
        return self._activate(session)

    def close_project(self, discard_changes: bool = False) -> None:
        """Close the current session, protecting dirty state by default."""
        if self._current_project is None:
            return
        if self._current_project.is_dirty and not discard_changes:
            raise UnsavedChangesError("Current project contains unsaved changes")
        logger.info("Đã đóng dự án %s", self._current_project.root_path)
        self._simulation_runs.bind_project(None, None)
        self._session_locks.release(self._current_project.root_path)
        self._current_project = None
        self._cam_application.clear()

    def recent_projects(self) -> tuple[RecentProjectEntry, ...]:
        """Return recently opened project paths."""
        return self._recent_projects.list()

    def remove_recent_project(self, project_path: Path) -> None:
        """Remove one missing or invalid recent-project entry."""
        self._recent_projects.remove(project_path)

    def target_path(self, parent_dir: Path, project_name: str) -> Path:
        """Return the validated normalized destination used for confirmation UI."""
        stem = self._validator.validate_project_name(project_name)
        return project_target_path(parent_dir, stem)

    def project_exists(self, parent_dir: Path, project_name: str) -> bool:
        """Check a validated normalized destination without exposing filesystem to UI."""
        return self.target_path(parent_dir, project_name).exists()

    def _activate(self, session: ProjectSession) -> ProjectSession:
        self._session_locks.acquire(session.root_path, session.manifest.project_id)
        try:
            return self._complete_activation(session)
        except Exception:
            self._session_locks.release(session.root_path)
            raise

    def _complete_activation(self, session: ProjectSession) -> ProjectSession:
        self._validator.validate_manifest(session.manifest)
        self._database.validate(session.root_path / session.manifest.database)
        previous = self._current_project
        if previous is not None and previous.root_path.resolve() != session.root_path.resolve():
            try:
                self._session_locks.release(previous.root_path)
            except Exception:
                self._session_locks.release(session.root_path)
                raise
        self._current_project = session
        self._cam_application.load(session.cam_snapshot)
        self._simulation_runs.bind_project(
            session.manifest.project_id,
            self._cam_application.generation,
        )
        session.cam_snapshot = self._cam_application.snapshot
        session.persisted_cam_snapshot = self._cam_application.snapshot
        try:
            self._recent_projects.add(session.root_path)
        except OSError:
            logger.warning("Không thể cập nhật danh sách dự án gần đây", exc_info=True)
        logger.info("Dự án hiện hành: %s", session.root_path)
        self._cleanup_temp(session.root_path)
        return session

    def _ensure_overwrite_target_available(
        self,
        parent_dir: Path,
        project_name: str,
        overwrite: bool,
    ) -> None:
        if not overwrite:
            return
        target = self.target_path(parent_dir, project_name)
        if target.exists():
            self._session_locks.ensure_available(target)

    @staticmethod
    def _cleanup_staging(parent_dir: Path) -> None:
        try:
            cleanup_stale_owned_directories(parent_dir, _CLEANUP_MINIMUM_AGE)
        except OSError:
            logger.warning("Không thể dọn staging HMS cũ tại %s", parent_dir, exc_info=True)

    @staticmethod
    def _cleanup_temp(project_root: Path) -> None:
        try:
            cleanup_stale_owned_directories(
                project_root / TEMP_DIRECTORY,
                _CLEANUP_MINIMUM_AGE,
            )
        except OSError:
            logger.warning("Không thể dọn temp HMS cũ tại %s", project_root, exc_info=True)

    def _require_current(self) -> ProjectSession:
        if self._current_project is None:
            raise ProjectError("No HMS project is currently open")
        return self._current_project

    @staticmethod
    def _require_source(session: ProjectSession, source_id: UUID) -> None:
        if not isinstance(source_id, UUID) or all(
            record.source_id != source_id for record in session.manifest.source_files
        ):
            raise ProjectError(f"CAD source is not part of the current project: {source_id}")
