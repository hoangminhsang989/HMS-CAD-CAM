"""Application-facing orchestration for HMS project workflows."""

from __future__ import annotations

import logging
import os
import shutil
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from collections.abc import Callable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from hms_cadcam.project.autosave import AutosaveManager, AutosaveSnapshot
from hms_cadcam.project.cad_state import CadViewState, default_cad_view_state
from hms_cadcam.project.cad_state_store import CadViewStateStore
from hms_cadcam.project.constants import (
    BACKUPS_DIRECTORY,
    CAM_WORKSPACE_MANIFEST_FILENAME,
    DATABASE_FILENAME,
    INCOMING_GEOMETRY_DIRECTORY,
    INCOMING_GEOMETRY_STAGING_DIRECTORY,
    REPLACED_DIRECTORY,
    TEMP_DIRECTORY,
)
from hms_cadcam.project.creator import ProjectCreator
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.document_cad_state import (
    cad_view_state_from_dict,
    cad_view_state_to_dict,
)
from hms_cadcam.project.document_container import HmsDocumentContainer
from hms_cadcam.project.geometry_transfer import (
    APPLYING_SUFFIX,
    REQUEST_DIRECTORY_PREFIX,
    REQUEST_GEOMETRY_DIRECTORY,
    CamProjectTargetInspection,
    ClaimedGeometryRequest,
    GeometryApplyChoice,
    GeometryApplyPlan,
    GeometryApplyPhase,
    GeometryApplyResult,
    GeometryTransferInbox,
    GeometryTransferRequest,
    IncomingGeometryPreview,
)
from hms_cadcam.project.exceptions import (
    DocumentSavePathRequiredError,
    GeometryTransferApplyError,
    GeometryTransferIntegrityError,
    GeometryTransferRecoveryError,
    ProjectError,
    RecoveryRequiredError,
    RecoverySnapshotInvalidError,
    ReplacedProjectRecoveryRequiredError,
    UnsavedChangesError,
)
from hms_cadcam.project.loader import ProjectLoader
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.filesystem import project_target_path
from hms_cadcam.project.filesystem import copy_source_verified, sha256_file
from hms_cadcam.project.models import (
    ProjectSession,
    UnitSystem,
    datetime_to_json,
    utc_now,
)
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
from hms_cadcam.project.workspace import (
    CadDocumentSession,
    DocumentMode,
    PreparedDocumentOpen,
    WorkspaceState,
)
from hms_cadcam.cam.application import (
    CamApplicationService, DrillingComputeResult, FacingComputeResult, PocketComputeResult,
    BoringComputeResult, ReamingComputeResult, TappingComputeResult,
)
from hms_cadcam.cam.persistence import CamProjectSnapshot, CamSqliteRepository, ToolpathArtifactStore
from hms_cadcam.cam.cam3d.persistence import Cam3DProjectConfig
from hms_cadcam.cam.cam3d.parallel import ParallelFinishingComputeResult
from hms_cadcam.cam.cam3d.zlevel import ZLevelFinishingComputeResult
from hms_cadcam.cam.domain import (
    DrillDepthDefinition, DrillGeometryInput, GeometryReference, Operation, OperationId,
    ResolvedContourProfile, ResolvedDrillingGeometry, ResolvedMachiningGeometry,
    ResolvedPocketGeometry,
)
from hms_cadcam.cam.application.contour import ContourComputeResult
from hms_cadcam.cam.optimization import CamCalculationProgress
from hms_cadcam.cam.domain.operation import ComputationToken
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import SetupId
from hms_cadcam.cam.lathe.domain import LatheOperationState
from hms_cadcam.cam.lathe.lathe_post.identity import LatheProgramIdentity
from hms_cadcam.cam.lathe.lathe_post.ir import NEUTRAL_PROFILE_ID
from hms_cadcam.cam.lathe.persistence import (
    LathePostConfiguration,
    LatheProgramState,
    LatheProjectPersistenceService,
    LatheProjectSnapshot,
)
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
from hms_cadcam.cam.post.export_model import NCAssemblyExportRequest, NCExportRequest
from hms_cadcam.cam.post.export_service import (
    NCAssemblyExportSourceSnapshot,
    NCExportExecution,
    NCExportService,
    NCExportSourceSnapshot,
)
from hms_cadcam.cam.post.export_store import NCArtifactStore, NCArtifactStoreError
from hms_cadcam.cam.post.service import PostRuntimeService
from hms_cadcam.cam.post.lowering import PostSourceSnapshot
from hms_cadcam.cam.post.model import PostRequest
from hms_cadcam.cam.post.service import PostExecution

logger = logging.getLogger(__name__)
_CLEANUP_MINIMUM_AGE = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class RestContourPreparationSummary:
    """Public, operation-scoped prepare outcome without R271 sealed bytes."""

    operation_id: OperationId
    status: object
    diagnostic_code: object | None
    message: str


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
        nc_export_service: NCExportService | None = None,
        document_container: HmsDocumentContainer | None = None,
        geometry_inbox: GeometryTransferInbox | None = None,
        lathe_persistence: LatheProjectPersistenceService | None = None,
        lathe_persistence_enabled: bool = False,
        lifecycle_hook: Callable[[str], None] | None = None,
        background_simulation_precompute: bool = False,
        background_simulation_coordinator: object | None = None,
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
        self._manifest_store = ProjectManifestStore()
        self._cam_application = cam_application or CamApplicationService()
        self._simulation_runs = SimulationRunController(
            self._cam_application.simulation_service
        )
        self._simulation_cache = simulation_cache or SimulationCacheStore()
        self._nc_export_service = nc_export_service or NCExportService()
        self._current_project: ProjectSession | None = None
        default_document_directory = recent_projects.config_dir / "documents"
        self._document_container = document_container or HmsDocumentContainer(
            recent_projects.config_dir / "document-runtime",
            default_document_directory,
        )
        self._current_document: CadDocumentSession | None = None
        self._geometry_inbox = geometry_inbox or GeometryTransferInbox(
            ProjectManifestStore(),
            validator,
            database,
            session_locks,
        )
        self._lathe_persistence = (
            lathe_persistence or LatheProjectPersistenceService()
        )
        if type(lathe_persistence_enabled) is not bool:
            raise TypeError("lathe_persistence_enabled must be bool")
        self._lathe_persistence_enabled = lathe_persistence_enabled
        self._lifecycle_hook = lifecycle_hook
        self._loader.set_lathe_enabled(lathe_persistence_enabled)
        self._lifecycle_generation = 0
        self._project_opened_at = None
        self._project_session_id: UUID | None = None
        if type(background_simulation_precompute) is not bool:
            raise TypeError("background_simulation_precompute must be bool")
        self._background_simulation_enabled = background_simulation_precompute
        self._background_simulation = background_simulation_coordinator
        self._background_calculation_claims: set[OperationId] = set()

    @classmethod
    def create_default(
        cls,
        config_dir: Path,
        *,
        document_runtime_root: Path | None = None,
        default_document_directory: Path | None = None,
        lathe_persistence_enabled: bool = False,
        lifecycle_hook: Callable[[str], None] | None = None,
        background_simulation_precompute: bool = False,
    ) -> "ProjectService":
        """Build the default project service graph for the application."""
        manifest_store = ProjectManifestStore()
        validator = ProjectValidator()
        database = ProjectDatabase()
        cad_state_store = CadViewStateStore()
        cam_repository = CamSqliteRepository()
        artifact_store = ToolpathArtifactStore()
        nc_artifact_store = NCArtifactStore()
        lathe_persistence = LatheProjectPersistenceService()
        session_locks = SessionLockManager()
        autosave = AutosaveManager(
            manifest_store,
            validator,
            database,
            cad_state_store,
            cam_repository,
            nc_artifact_store,
            lathe_persistence=lathe_persistence,
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
                lathe_persistence=lathe_persistence,
                lathe_enabled=lathe_persistence_enabled,
            ),
            saver=ProjectSaver(
                manifest_store,
                validator,
                database,
                cad_state_store,
                cam_repository,
                artifact_store,
                nc_artifact_store,
                lathe_persistence=lathe_persistence,
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
            nc_export_service=NCExportService(nc_artifact_store),
            document_container=HmsDocumentContainer(
                document_runtime_root or config_dir / "document-runtime",
                default_document_directory or config_dir / "documents",
            ),
            geometry_inbox=GeometryTransferInbox(
                manifest_store,
                validator,
                database,
                session_locks,
            ),
            lathe_persistence=lathe_persistence,
            lathe_persistence_enabled=lathe_persistence_enabled,
            lifecycle_hook=lifecycle_hook,
            background_simulation_precompute=background_simulation_precompute,
        )

    def configure_background_simulation_precompute(self, enabled: bool) -> None:
        """Enable optional spare-resource Simulation preparation for future publishes."""
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        if not enabled and self._current_project is not None:
            self._cancel_background_project(self._current_project)
        self._background_simulation_enabled = enabled
        if enabled:
            self._background_simulation_coordinator()

    @property
    def background_simulation_precompute_enabled(self) -> bool:
        """Return the optional precompute policy without importing R241 modules."""
        return self._background_simulation_enabled

    @property
    def config_dir(self) -> Path:
        """Return the user-only runtime configuration directory."""
        return self._recent_projects.config_dir

    @property
    def current_project(self) -> ProjectSession | None:
        """Return the current session without exposing persistence adapters."""
        return self._current_project

    def configure_lathe_persistence(self, enabled: bool) -> None:
        """Enable/disable Lathe hydration without rewriting opaque database rows."""

        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        session = self._current_project
        if not enabled and session is not None and (
            session.lathe_snapshot != session.persisted_lathe_snapshot
        ):
            raise UnsavedChangesError(
                "Cannot disable Lathe persistence with staged authored state"
            )
        self._lathe_persistence_enabled = enabled
        self._loader.set_lathe_enabled(enabled)
        if session is None:
            return
        if enabled and not session.lathe_persistence_loaded:
            result = self._lathe_persistence.load_project(
                session.root_path / DATABASE_FILENAME,
                session.manifest.project_id,
                read_only=session.read_only,
            )
            session.lathe_snapshot = result.snapshot
            session.persisted_lathe_snapshot = result.snapshot
            session.lathe_restore_diagnostics = result.diagnostics
            session.lathe_persistence_loaded = True
        elif not enabled:
            session.lathe_snapshot = None
            session.persisted_lathe_snapshot = None
            session.lathe_restore_diagnostics = ()
            session.lathe_persistence_loaded = False

    @property
    def current_document(self) -> CadDocumentSession | None:
        """Return the active standalone document without persistence adapters."""
        return self._current_document

    @property
    def current_workspace(self) -> WorkspaceState | None:
        """Return typed state for either CAD_DOCUMENT or CAM_PROJECT."""
        if self._current_document is not None:
            return self._current_document.state
        session = self._current_project
        if session is None:
            return None
        opened_at = self._project_opened_at or session.manifest.created_at
        session_id = self._project_session_id or self._session_locks.session_id
        return WorkspaceState(
            mode=DocumentMode.CAM_PROJECT,
            document_id=None,
            project_id=session.manifest.project_id,
            display_name=session.manifest.project_name,
            physical_path=session.root_path,
            source_path=(
                None
                if not session.manifest.source_files
                else Path(session.manifest.source_files[0].original_path)
                if session.manifest.source_files[0].original_path
                else session.root_path
                / Path(session.manifest.source_files[0].stored_path)
            ),
            suggested_save_directory=session.root_path.parent,
            dirty=session.is_dirty,
            read_only=session.read_only,
            opened_at=opened_at,
            session_id=session_id,
            format_version=session.manifest.format_version,
            lifecycle_generation=max(self._lifecycle_generation, 1),
        )

    @property
    def has_project(self) -> bool:
        """Return whether a project is currently open."""
        return self._current_project is not None

    @property
    def has_workspace(self) -> bool:
        """Return whether either supported workspace mode is active."""
        return self._current_project is not None or self._current_document is not None

    @property
    def is_dirty(self) -> bool:
        """Return whether the current manifest has unsaved changes."""
        if self._current_document is not None:
            return self._current_document.state.dirty
        return bool(self._current_project and self._current_project.is_dirty)

    def prepare_document_open(self, path: Path) -> PreparedDocumentOpen:
        """Validate a source/container without replacing the current workspace."""
        suffix = path.suffix.casefold()
        if suffix == ".hms" and path.is_file():
            return self._document_container.prepare_container(path)
        if suffix not in {
            ".step",
            ".stp",
            ".brep",
            ".brp",
            ".iges",
            ".igs",
            ".stl",
        }:
            raise ProjectError(f"Định dạng file chưa được hỗ trợ: {suffix or path.name}")
        return self._document_container.prepare_source(path)

    def commit_document_open(
        self,
        prepared: PreparedDocumentOpen,
    ) -> WorkspaceState:
        """Atomically switch mode only after the existing importer has succeeded."""
        if not isinstance(prepared, PreparedDocumentOpen):
            raise TypeError("Prepared document request is invalid")
        candidate = prepared.session
        previous_document = self._current_document
        previous_project = self._current_project
        if previous_project is not None:
            self._simulation_runs.bind_project(None, None)
            self._session_locks.release(previous_project.root_path)
            self._cam_application.clear()
            self._nc_export_service.bind_project(None, None, None)
        self._current_project = None
        self._current_document = candidate
        self._lifecycle_generation += 1
        candidate.state = candidate.state.with_changes(
            lifecycle_generation=self._lifecycle_generation
        )
        self._project_opened_at = None
        self._project_session_id = None
        if previous_document is not None and previous_document is not candidate:
            self._document_container.close(previous_document)
        logger.info("Tài liệu CAD hiện hành: %s", candidate.state.display_name)
        return candidate.state

    def discard_document_open(self, prepared: PreparedDocumentOpen) -> None:
        """Release a prepared container that never reached mode commit."""
        if (
            not isinstance(prepared, PreparedDocumentOpen)
            or self._current_document is prepared.session
        ):
            return
        self._document_container.close(prepared.session)

    def save_document(self, target: Path | None = None) -> WorkspaceState:
        """Save or Save As the active CAD_DOCUMENT container."""
        document = self._require_document()
        if target is None and document.state.physical_path is None:
            raise DocumentSavePathRequiredError(
                "Lần lưu đầu tiên phải dùng Lưu thành tài liệu HMS."
            )
        self._document_container.save(document, target)
        self._lifecycle_generation += 1
        document.state = document.state.with_changes(
            lifecycle_generation=self._lifecycle_generation
        )
        return document.state

    def record_document_geometry_metadata(
        self,
        metadata: dict[str, object],
    ) -> None:
        """Retain importer evidence for transfer without dirtying the document."""
        document = self._require_document()
        if not isinstance(metadata, dict):
            raise TypeError("Document geometry metadata must be a dictionary")
        document.cad_metadata = dict(metadata)

    def inspect_geometry_transfer_target(
        self,
        project_root: Path,
    ) -> CamProjectTargetInspection:
        """Return UI-safe validation for a selected CAM project root."""
        required = 0
        if self._current_document is not None:
            try:
                required = self._current_document.geometry_path.stat().st_size
            except OSError:
                required = 0
        return self._geometry_inbox.inspect_target(
            project_root,
            required_payload_bytes=required,
        )

    def send_document_geometry(
        self,
        project_root: Path,
    ) -> GeometryTransferRequest:
        """Send exact current geometry while keeping the HMS document active."""
        document = self._require_document()
        request = self._geometry_inbox.send(document, project_root)
        if self._current_document is not document:
            raise ProjectError(
                "Tài liệu HMS đã thay đổi trước khi hoàn tất nạp 3D."
            )
        return request

    def scan_incoming_geometry(self) -> tuple[GeometryTransferRequest, ...]:
        """Scan only complete requests after project activation/recovery."""
        session = self._require_current()
        return self._geometry_inbox.scan(
            session.root_path,
            session.manifest.project_id,
        )

    def incoming_geometry_preview(
        self,
        request_id: UUID,
    ) -> IncomingGeometryPreview:
        """Build a native-free preview without changing project state."""
        session = self._require_current()
        request = self._geometry_inbox.request(session.root_path, request_id)
        if request.target_project_id != session.manifest.project_id:
            raise ProjectError("Yêu cầu nạp 3D thuộc dự án khác.")
        return self._geometry_inbox.preview(session, request)

    def defer_incoming_geometry(
        self,
        request_id: UUID,
    ) -> GeometryTransferRequest:
        """Persist DEFERRED without deleting request data."""
        session = self._require_current()
        return self._geometry_inbox.defer(session.root_path, request_id)

    def reject_incoming_geometry(
        self,
        request_id: UUID,
    ) -> GeometryTransferRequest:
        """Persist REJECTED and retain request data for audit."""
        session = self._require_current()
        return self._geometry_inbox.reject(session.root_path, request_id)

    def apply_incoming_geometry(
        self,
        request_id: UUID,
        choice: GeometryApplyChoice,
        *,
        target_source_id: UUID | None = None,
    ) -> GeometryApplyResult:
        """Apply one claimed request with project-local backup and rollback."""
        session = self._require_current()
        before_manifest = session.manifest
        before_cad_states = dict(session.cad_view_states)
        before_snapshot = self._cam_application.snapshot
        try:
            claim = self._geometry_inbox.claim(session.root_path, request_id)
        except GeometryTransferIntegrityError as error:
            raise GeometryTransferApplyError(
                "Yêu cầu nạp 3D không còn nguyên vẹn hoặc không thể nhận xử lý."
            ) from error
        plan = None
        backup_root = (
            session.root_path
            / BACKUPS_DIRECTORY
            / f"geometry-transfer-{request_id}"
        )
        source_temp: Path | None = None
        working_temp: Path | None = None
        try:
            self._validate_claim_geometry(claim)
            plan = self._geometry_inbox.apply_plan(
                session,
                claim.request,
                choice,
                target_source_id,
                payload_size=claim.payload_path.stat().st_size,
            )
            if plan.source_path.exists() or plan.working_path.exists():
                raise GeometryTransferIntegrityError(
                    "Đường dẫn geometry asset đích đã tồn tại."
                )
            backup_root.mkdir()
            manifest_name = self._manifest_store.filename_for(
                session.root_path
            )
            manifest_backup = backup_root / manifest_name
            database_backup = backup_root / DATABASE_FILENAME
            shutil.copy2(session.root_path / manifest_name, manifest_backup)
            self._database.backup(
                session.root_path / DATABASE_FILENAME,
                database_backup,
            )
            if plan.previous_working_path is not None and (
                plan.previous_working_path.is_file()
            ):
                old_backup = backup_root / "previous-working" / (
                    plan.previous_working_path.name
                )
                copy_source_verified(
                    plan.previous_working_path,
                    old_backup,
                )
            else:
                old_backup = None
            evidence = self._geometry_apply_evidence(
                claim,
                plan,
                backup_root,
                manifest_backup,
                database_backup,
                old_backup,
                GeometryApplyPhase.PREPARED,
            )
            self._geometry_inbox.write_apply_evidence(claim, evidence)
            source_temp = plan.source_path.with_name(
                f".{plan.source_path.name}.{request_id.hex}.applying"
            )
            working_temp = plan.working_path.with_name(
                f".{plan.working_path.name}.{request_id.hex}.applying"
            )
            source_size, source_digest = copy_source_verified(
                claim.payload_path,
                source_temp,
            )
            working_size, working_digest = copy_source_verified(
                claim.payload_path,
                working_temp,
            )
            if (
                source_size != working_size
                or source_digest != working_digest
                or source_digest != claim.request.payload_checksum
            ):
                raise GeometryTransferIntegrityError(
                    "Bản sao source/working geometry không khớp request."
                )
            os.replace(source_temp, plan.source_path)
            os.replace(working_temp, plan.working_path)
            source_temp = None
            working_temp = None
            if (
                sha256_file(plan.source_path)
                != claim.request.payload_checksum
                or sha256_file(plan.working_path)
                != claim.request.payload_checksum
            ):
                raise GeometryTransferIntegrityError(
                    "Geometry asset sau atomic commit không khớp checksum."
                )
            evidence = dict(evidence)
            evidence["phase"] = GeometryApplyPhase.FILES_COMMITTED.value
            self._geometry_inbox.write_apply_evidence(claim, evidence)
            changed_sources = (
                frozenset()
                if choice is GeometryApplyChoice.ADD_NEW
                else frozenset({plan.source_id})
            )
            candidate_snapshot, affected = (
                self._cam_application.preview_geometry_sources_changed(
                    changed_sources
                )
            )
            candidate_cad_states = dict(session.cad_view_states)
            if changed_sources:
                candidate_cad_states.pop(plan.source_id, None)
            candidate = ProjectSession(
                root_path=session.root_path,
                manifest=plan.manifest,
                is_dirty=True,
                cad_view_states=candidate_cad_states,
                persisted_cad_view_states=dict(
                    session.persisted_cad_view_states
                ),
                cam_snapshot=candidate_snapshot,
                persisted_cam_snapshot=session.persisted_cam_snapshot,
                cam3d_config=session.cam3d_config,
                persisted_cam3d_config=session.persisted_cam3d_config,
                replaced_directory_name=session.replaced_directory_name,
            )
            self._validator.validate_references(
                session.root_path,
                candidate.manifest,
            )
            persisted = self._saver.save(candidate)
            evidence["phase"] = GeometryApplyPhase.PERSISTED.value
            self._geometry_inbox.write_apply_evidence(claim, evidence)
            applied = self._geometry_inbox.finish_applied(
                session.root_path,
                claim,
            )
            session.manifest = persisted.manifest
            session.cad_view_states = dict(persisted.cad_view_states)
            session.persisted_cad_view_states = dict(
                persisted.persisted_cad_view_states
            )
            session.cam_snapshot = persisted.cam_snapshot
            session.persisted_cam_snapshot = persisted.persisted_cam_snapshot
            session.cam3d_config = persisted.cam3d_config
            session.persisted_cam3d_config = persisted.persisted_cam3d_config
            session.is_dirty = False
            self._cam_application.commit_persisted_geometry_change(
                persisted.cam_snapshot,
                affected,
            )
            for operation_id in affected:
                self._simulation_runs.mark_stale(
                    operation_id,
                    "Hình học nguồn đã được cập nhật.",
                )
                self._nc_export_service.mark_operation_stale(operation_id)
            self._reconcile_nc_artifacts(session, persisted.cam_snapshot)
            self._archive_previous_working_geometry(
                session.root_path,
                plan,
            )
            return GeometryApplyResult(
                request=applied,
                choice=choice,
                source_id=plan.source_id,
                affected_operation_ids=tuple(
                    str(item) for item in affected
                ),
                working_geometry_path=plan.working_path,
                project_root=session.root_path,
            )
        except Exception as error:
            if source_temp is not None:
                source_temp.unlink(missing_ok=True)
            if working_temp is not None:
                working_temp.unlink(missing_ok=True)
            rollback_error = self._rollback_geometry_apply(
                session.root_path,
                plan,
                backup_root,
            )
            session.manifest = before_manifest
            session.cad_view_states = before_cad_states
            session.cam_snapshot = before_snapshot
            if claim.request_path.is_dir():
                try:
                    self._geometry_inbox.fail_claim(
                        session.root_path,
                        claim,
                        "Cập nhật thất bại, mô hình cũ được giữ nguyên.",
                    )
                except Exception:
                    logger.exception(
                        "Không thể chuyển request apply lỗi sang failed"
                    )
            if rollback_error is not None:
                raise GeometryTransferRecoveryError(
                    "Cập nhật và rollback hình học đều thất bại; "
                    f"backup được giữ tại {backup_root}"
                ) from rollback_error
            raise GeometryTransferApplyError(
                "Cập nhật thất bại, mô hình cũ được giữ nguyên."
            ) from error

    def suggested_document_path(self) -> Path:
        """Return the UI suggestion for first Save or later Save As."""
        document = self._require_document()
        directory = self._document_container.suggested_save_directory(
            physical_path=document.state.physical_path,
            source_path=document.state.source_path,
            last_valid_directory=document.state.suggested_save_directory,
        )
        stem = (
            document.state.physical_path.stem
            if document.state.physical_path is not None
            else Path(document.provenance.original_filename).stem
        )
        return directory / f"{stem}.HMS"

    def autosave_workspace(
        self,
        *,
        expected_identity: UUID | None = None,
    ) -> AutosaveSnapshot | Path | None:
        """Route autosave to document recovery or project-root autosave."""
        workspace = self.current_workspace
        if workspace is None:
            return None
        if expected_identity is not None and workspace.identity != expected_identity:
            return None
        if workspace.mode is DocumentMode.CAD_DOCUMENT:
            return self._document_container.autosave(self._require_document())
        return self.autosave(expected_project_id=workspace.identity)

    def create_cam_workspace(
        self,
        parent_dir: Path,
        project_name: str,
        units: UnitSystem = UnitSystem.MILLIMETER,
    ) -> ProjectSession:
        """Create and activate a non-overwriting folder-based CAM project."""
        session = self._creator.create_cam_workspace(
            parent_dir,
            project_name,
            units,
        )
        try:
            return self._activate(session)
        except Exception:
            self._rollback_new_cam_workspace(session)
            raise

    def create_cam_workspace_from_document(
        self,
        parent_dir: Path,
        project_name: str,
        units: UnitSystem = UnitSystem.MILLIMETER,
    ) -> ProjectSession:
        """Convert the current document without changing mode until publish succeeds."""
        document = self._require_document()
        session = self._creator.create_cam_workspace(
            parent_dir,
            project_name,
            units,
            source_path=document.geometry_path,
            source_provenance=document.provenance,
        )
        try:
            return self._activate(session)
        except Exception:
            self._rollback_new_cam_workspace(session)
            raise

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
        self._require_writable(session)
        self._saver.import_source(session, source_path)
        logger.info("Đã sao chép file nguồn %s vào %s", source_path, session.root_path)
        return session

    def open_project(
        self,
        project_root: Path,
        *,
        discard_recovery: bool = False,
        read_only: bool = False,
    ) -> ProjectSession:
        """Open a valid project without replacing current state on failure."""
        if type(read_only) is not bool:
            raise TypeError("read_only must be bool")
        with self._background_foreground("project_open"):
            return self._open_project_foreground(
                project_root,
                discard_recovery=discard_recovery,
                read_only=read_only,
            )

    def _open_project_foreground(
        self,
        project_root: Path,
        *,
        discard_recovery: bool,
        read_only: bool,
    ) -> ProjectSession:
        replaced = self._recovery.inspect_replaced_for_open(project_root)
        if replaced is not None:
            raise ReplacedProjectRecoveryRequiredError(replaced)
        if read_only:
            session = self._loader.load(project_root, read_only=True)
            return self._complete_activation(session)
        resolved = project_root.resolve()
        if (
            self._current_project is not None
            and self._current_project.root_path.resolve() == resolved
        ):
            self._recover_incomplete_geometry_transfers(project_root)
            session = self._loader.load(project_root, read_only=read_only)
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
            self._recover_incomplete_geometry_transfers(project_root)
            session = self._loader.load(project_root, read_only=read_only)
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
                try:
                    self._nc_export_service.store.copy_workspace(
                        current.snapshot.path,
                        assessment.project_root,
                        assessment.project_id,
                        assessment.project_id,
                    )
                except (OSError, NCArtifactStoreError):
                    logger.warning(
                        "Không thể phục hồi NC artifact từ autosave",
                        exc_info=True,
                    )
            self._recover_incomplete_geometry_transfers(
                assessment.project_root
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
        with self._background_foreground("project_save"):
            current = self._require_current()
            self._require_writable(current)
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
        self._require_writable(session)
        before = self._cam_application.snapshot
        changed = self._cam_application.apply(lambda _current: snapshot)
        session.cam_snapshot = changed
        if changed != before:
            session.is_dirty = True
            self._simulation_runs.cancel_all(stale=True)
            self._remove_deleted_operation_simulations(session, before, changed)
            self._reconcile_nc_artifacts(session, changed)
        return session

    @property
    def lathe_snapshot(self) -> LatheProjectSnapshot | None:
        """Return hydrated immutable Lathe state, or None while feature-off."""

        return self._require_current().lathe_snapshot

    def stage_lathe_snapshot(
        self,
        snapshot: LatheProjectSnapshot,
    ) -> ProjectSession:
        """Stage complete Lathe authoring through the project service boundary."""

        if not isinstance(snapshot, LatheProjectSnapshot):
            raise TypeError("Lathe snapshot is invalid")
        session = self._require_current()
        self._require_writable(session)
        if not self._lathe_persistence_enabled or not session.lathe_persistence_loaded:
            raise ProjectError("Lathe persistence facade is not active")
        project_id = str(session.manifest.project_id)
        if any(
            program.identity.project_id != project_id
            for program in snapshot.programs
        ):
            raise ValueError("Lathe snapshot belongs to another project")
        before = session.lathe_snapshot
        session.lathe_snapshot = snapshot
        if snapshot != before:
            session.is_dirty = True
        return session

    def stage_lathe_operations(
        self,
        *,
        document_id: CadDocumentId,
        source_id: UUID,
        generation: int,
        setup_id: SetupId,
        operations: tuple[LatheOperationState, ...],
    ) -> ProjectSession:
        """Stage presenter-authored operations as one exact owned Lathe program."""

        if not isinstance(document_id, CadDocumentId):
            raise TypeError("document_id must be CadDocumentId")
        if not isinstance(source_id, UUID) or source_id.int == 0:
            raise ValueError("source_id must be a non-nil UUID")
        if type(generation) is not int or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if not isinstance(setup_id, SetupId):
            raise TypeError("setup_id must be SetupId")
        if not isinstance(operations, tuple) or any(
            not isinstance(item, LatheOperationState) for item in operations
        ):
            raise TypeError("operations must be an immutable Lathe tuple")
        session = self._require_current()
        self._require_writable(session)
        current = session.lathe_snapshot
        if (
            not self._lathe_persistence_enabled
            or not session.lathe_persistence_loaded
            or current is None
        ):
            raise ProjectError("Lathe persistence facade is not active")
        owner = (
            str(session.manifest.project_id),
            str(document_id),
            str(source_id),
            str(setup_id),
        )
        existing_index = next(
            (
                index
                for index, program in enumerate(current.programs)
                if (
                    program.identity.project_id,
                    program.identity.document_id,
                    program.identity.source_id,
                    program.identity.setup_id,
                )
                == owner
            ),
            None,
        )
        existing = (
            None if existing_index is None else current.programs[existing_index]
        )
        if existing is None and not operations:
            return session
        if existing is not None and existing.operations == operations:
            return session
        if existing is None:
            stable_program_id = str(
                uuid5(
                    NAMESPACE_URL,
                    "hms.lathe.program.v1/" + "/".join(owner),
                )
            )
            identity = LatheProgramIdentity(
                project_id=owner[0],
                document_id=owner[1],
                source_id=owner[2],
                source_generation=generation,
                setup_id=owner[3],
                program_id=stable_program_id,
                revision=0,
            )
            program = LatheProgramState(
                identity,
                "LATHE_PROGRAM",
                operations,
                NEUTRAL_PROFILE_ID,
                LathePostConfiguration(),
            )
            programs = (*current.programs, program)
            old_operation_ids: set[str] = set()
        else:
            identity = replace(
                existing.identity,
                source_generation=generation,
                revision=existing.identity.revision + 1,
            )
            program = replace(existing, identity=identity, operations=operations)
            mutable_programs = list(current.programs)
            assert existing_index is not None
            mutable_programs[existing_index] = program
            programs = tuple(mutable_programs)
            old_operation_ids = {
                str(item.ownership.operation_id) for item in existing.operations
            }
        derived = tuple(
            item
            for item in current.derived_snapshots
            if item.program_id != program.identity.program_id
            and item.operation_id not in old_operation_ids
        )
        return self.stage_lathe_snapshot(LatheProjectSnapshot(programs, derived))

    @property
    def cam3d_config(self) -> Cam3DProjectConfig:
        """Return the current immutable CAM 3D editable configuration."""
        session = self._require_current()
        if session.cam3d_config is None:
            session.cam3d_config = Cam3DProjectConfig(session.manifest.project_id)
        return session.cam3d_config

    def stage_cam3d_config(self, config: Cam3DProjectConfig) -> ProjectSession:
        """Stage CAM 3D zones without creating a mesh or dirtying CAD state."""
        if not isinstance(config, Cam3DProjectConfig):
            raise TypeError("CAM 3D project config is invalid")
        session = self._require_current()
        self._require_writable(session)
        if config.project_id != session.manifest.project_id:
            raise ValueError("CAM 3D config belongs to another project")
        before = session.cam3d_config or Cam3DProjectConfig(config.project_id)
        session.cam3d_config = config
        if config != before:
            session.is_dirty = True
        return session

    def apply_cam_mutation(
        self,
        mutation: Callable[[CamProjectSnapshot], CamProjectSnapshot],
    ) -> ProjectSession:
        """Apply one atomic CAM snapshot mutation and mark project dirty on change."""
        session = self._require_current()
        self._require_writable(session)
        before = self._cam_application.snapshot
        changed = self._cam_application.apply(mutation)
        session.cam_snapshot = changed
        if changed != before:
            session.is_dirty = True
            self._simulation_runs.cancel_all(stale=True)
            self._remove_deleted_operation_simulations(session, before, changed)
            self._reconcile_nc_artifacts(session, changed)
        return session

    def execute_cam_command(
        self,
        command: Callable[[CamApplicationService], CamProjectSnapshot],
        *,
        expected_generation: int | None = None,
    ) -> CamProjectSnapshot:
        """Execute one CAM application command and propagate project dirty state."""
        session = self._require_current()
        self._require_writable(session)
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
            self._mark_changed_operation_simulations(before, changed)
            self._remove_deleted_operation_simulations(session, before, changed)
            self._reconcile_nc_artifacts(session, changed)
        return changed

    def execute_cam_creation(
        self,
        command: Callable[[CamApplicationService], CamProjectSnapshot],
        *,
        expected_project_id: UUID,
        expected_generation: int,
        cam3d_config: Cam3DProjectConfig | None = None,
    ) -> CamProjectSnapshot:
        """Create one operation through a project-owned all-or-nothing boundary.

        The optional CAM 3D zone is prevalidated and published only after the
        in-memory operation command succeeds.  Validation failures therefore
        leave both the operation tree and CAM 3D configuration untouched.
        """
        session = self._require_current()
        self._require_writable(session)
        if session.manifest.project_id != expected_project_id:
            raise RuntimeError("Operation creation belongs to another project")
        if self._cam_application.generation != expected_generation:
            raise RuntimeError(
                "Operation creation belongs to an inactive project generation"
            )
        if cam3d_config is not None:
            if not isinstance(cam3d_config, Cam3DProjectConfig):
                raise TypeError("CAM 3D creation configuration is invalid")
            if cam3d_config.project_id != expected_project_id:
                raise ValueError(
                    "CAM 3D creation configuration belongs to another project"
                )
        before = self._cam_application.snapshot
        changed = self._cam_application.execute(command)
        session.cam_snapshot = changed
        if cam3d_config is not None:
            session.cam3d_config = cam3d_config
        if changed != before:
            session.is_dirty = True
            self._mark_changed_operation_simulations(before, changed)
            self._remove_deleted_operation_simulations(session, before, changed)
            self._reconcile_nc_artifacts(session, changed)
        return changed

    def create_rest_contour_operation(
        self, job_id, setup_id: SetupId, parent_node_id, *, operation_id: OperationId,
        node_id, name: str, parameters, profile, dependency_operation_id: OperationId,
        tool_assembly_id, machine_requirement=None, profile_resolver=None, quality_profile=None,
        manual_overrides=None, expected_generation: int | None = None,
    ) -> CamProjectSnapshot:
        """Create one registered Rest Contour through the project gateway."""
        session = self._require_current()
        self._require_writable(session)
        if expected_generation is not None and expected_generation != self._cam_application.generation:
            raise RuntimeError("Rest Contour creation belongs to an inactive project generation")
        before = self._cam_application.snapshot
        changed = self._cam_application.create_rest_contour_operation(
            job_id, setup_id, parent_node_id,
            operation_id=operation_id, node_id=node_id, name=name,
            parameters=parameters, profile=profile,
            dependency_operation_id=dependency_operation_id,
            tool_assembly_id=tool_assembly_id,
            machine_requirement=machine_requirement,
            profile_resolver=profile_resolver,
            quality_profile=quality_profile,
            manual_overrides=manual_overrides,
        )
        session.cam_snapshot = changed
        if changed != before:
            session.is_dirty = True
            self._mark_changed_operation_simulations(before, changed)
            self._remove_deleted_operation_simulations(session, before, changed)
            self._reconcile_nc_artifacts(session, changed)
        return changed

    def prepare_rest_contour(
        self, operation_id: OperationId, *, profile_resolver,
        cancellation: Callable[[], bool] | None = None,
        expected_generation: int | None = None,
    ):
        """Prepare current Rest Contour authority without durable publication."""
        session = self._require_current()
        self._require_writable(session)
        if expected_generation is not None and expected_generation != self._cam_application.generation:
            raise RuntimeError("Rest Contour preparation belongs to an inactive project generation")
        with self._background_foreground("toolpath_calculation"):
            result = self._cam_application.prepare_rest_contour(
                session.root_path, operation_id, profile_resolver=profile_resolver,
                cancellation=cancellation,
            )
        return RestContourPreparationSummary(
            operation_id, result.status, result.diagnostic_code, result.message,
        )

    def generate_rest_contour(
        self, operation_id: OperationId, *, profile_resolver,
        cancellation: Callable[[], bool] | None = None,
        expected_generation: int | None = None, persist: bool = True,
    ):
        """Run Rest Contour and atomically save its v2 completion record."""
        from hms_cadcam.cam.application.rest_contour_lifecycle import (
            RestContourLifecycleStatus,
        )

        if type(persist) is not bool:
            raise TypeError("Rest Contour persistence option must be boolean")
        session = self._require_current()
        self._require_writable(session)
        if expected_generation is not None and expected_generation != self._cam_application.generation:
            raise RuntimeError("Rest Contour generation belongs to an inactive project generation")
        before = self._cam_application.snapshot
        with self._background_foreground("toolpath_calculation"):
            result = self._cam_application.generate_rest_contour(
                session.root_path, operation_id, profile_resolver=profile_resolver,
                cancellation=cancellation,
            )
        changed = self._cam_application.snapshot
        session.cam_snapshot = changed
        if changed != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
            self._nc_export_service.mark_operation_stale(operation_id)
        # A semantic FAILURE or NO_REST result is not a persistence command.
        # Preserve any unrelated pre-existing dirty project edits and the
        # previous database snapshot; only a verified v2 SUCCESS may make the
        # persist=True transition durable.
        if result.status is not RestContourLifecycleStatus.SUCCESS:
            return result
        if not persist or (changed == before and not session.is_dirty):
            return result
        try:
            self.save()
        except Exception:
            restored = self._cam_application.restore_rest_contour_snapshot(before)
            session.cam_snapshot = restored
            session.is_dirty = restored != session.persisted_cam_snapshot
            raise
        return result

    @property
    def cam_generation(self) -> int:
        """Return the active CAM project generation for stale-signal guards."""
        self._require_current()
        return self._cam_application.generation

    @property
    def nc_export_service(self) -> NCExportService:
        """Return the project-bound non-UI NC export application service."""
        self._require_current()
        return self._nc_export_service

    @property
    def post_service(self) -> PostRuntimeService:
        """Return the current project-scoped Post runtime service."""
        self._require_current()
        return self._cam_application.post_runtime

    def export_nc(
        self,
        request: NCExportRequest,
        source: NCExportSourceSnapshot,
        *,
        current_source: Callable[[], NCExportSourceSnapshot] | None = None,
    ) -> NCExportExecution:
        """Export one current production PostResult through the project lifecycle."""
        with self._background_foreground("nc_export"):
            session = self._require_current()
            if request.project_id != session.manifest.project_id:
                raise ProjectError("NC export request belongs to another project")
            if source.project_generation != self._cam_application.generation:
                raise ProjectError("NC export source belongs to an inactive project generation")
            return self._nc_export_service.export(
                session.root_path,
                request,
                source,
                current_source=current_source,
                current_project_generation=lambda: self._cam_application.generation,
                current_post_result=lambda: self._cam_application.post_runtime.current(
                    source.post_request
                ),
            )

    def export_assembly_nc(
        self,
        request: NCAssemblyExportRequest,
        source: NCAssemblyExportSourceSnapshot,
        *,
        current_source: Callable[[], NCAssemblyExportSourceSnapshot] | None = None,
    ) -> NCExportExecution:
        """Export one published multi-operation assembly via 7D.2.2 storage."""
        with self._background_foreground("nc_assembly_export"):
            session = self._require_current()
            if request.project_id != session.manifest.project_id:
                raise ProjectError("NC assembly export request belongs to another project")
            if source.project_generation != self._cam_application.generation:
                raise ProjectError("NC assembly export source belongs to an inactive project generation")
            return self._nc_export_service.export_assembly(
                session.root_path,
                request,
                source,
                current_source=current_source,
                current_project_generation=lambda: self._cam_application.generation,
            )

    def post_toolpath(
        self,
        request: PostRequest,
        source: PostSourceSnapshot,
        *,
        current_source: Callable[[], PostSourceSnapshot] | None = None,
    ) -> PostExecution:
        """Run production Post while foreground ownership pauses precompute."""
        self._require_current()
        with self._background_foreground("post"):
            return self._cam_application.post_runtime.post(
                request,
                source,
                current_source=current_source,
            )

    def register_toolpath_artifact(
        self,
        operation_id: OperationId,
        candidate: ToolpathArtifact,
        token: ComputationToken,
        current_input: DependencyFingerprint,
    ) -> ToolpathPublishResult:
        """Publish a verified artifact file and stage its metadata for Save."""
        session = self._require_current()
        with self._background_foreground("toolpath_calculation"):
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
            self._nc_export_service.mark_operation_stale(operation_id)
        if result.accepted and result.artifact is not None:
            self._schedule_background_simulation(operation_id)
        return result

    def begin_parallel_calculation(
        self,
        computing: Operation,
        *,
        expected_generation: int,
    ) -> bool:
        """Stage a Parallel worker token only for the active project generation."""
        session = self._require_current()
        if expected_generation != self._cam_application.generation:
            return False
        before = self._cam_application.snapshot
        self._begin_background_calculation(computing.operation_id)
        accepted = self._cam_application.begin_parallel_calculation(computing)
        if not accepted:
            self._end_background_calculation(computing.operation_id)
        session.cam_snapshot = self._cam_application.snapshot
        if accepted and session.cam_snapshot != before:
            session.is_dirty = True
        return accepted

    def commit_parallel_calculation(
        self,
        result: ParallelFinishingComputeResult,
        *,
        expected_generation: int,
    ) -> bool:
        """Commit a SAFE/failed Parallel result through the project lifecycle gate."""
        session = self._require_current()
        if expected_generation != self._cam_application.generation:
            self._end_background_calculation(result.operation.operation_id)
            return False
        before = self._cam_application.snapshot
        try:
            accepted = self._cam_application.commit_parallel_calculation(result)
        finally:
            self._end_background_calculation(result.operation.operation_id)
        session.cam_snapshot = self._cam_application.snapshot
        if accepted and session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(result.operation.operation_id)
            self._nc_export_service.mark_operation_stale(result.operation.operation_id)
            if result.artifact is not None:
                self._schedule_background_simulation(result.operation.operation_id)
        return accepted

    def begin_z_level_calculation(
        self,
        computing: Operation,
        *,
        expected_generation: int,
    ) -> bool:
        """Stage a Z-Level worker token only for the active project generation."""
        session = self._require_current()
        if expected_generation != self._cam_application.generation:
            return False
        before = self._cam_application.snapshot
        self._begin_background_calculation(computing.operation_id)
        accepted = self._cam_application.begin_z_level_calculation(computing)
        if not accepted:
            self._end_background_calculation(computing.operation_id)
        session.cam_snapshot = self._cam_application.snapshot
        if accepted and session.cam_snapshot != before:
            session.is_dirty = True
        return accepted

    def commit_z_level_calculation(
        self,
        result: ZLevelFinishingComputeResult,
        *,
        expected_generation: int,
    ) -> bool:
        """Commit a SAFE/failed Z-Level result through the project lifecycle."""
        session = self._require_current()
        if expected_generation != self._cam_application.generation:
            self._end_background_calculation(result.operation.operation_id)
            return False
        before = self._cam_application.snapshot
        try:
            accepted = self._cam_application.commit_z_level_calculation(result)
        finally:
            self._end_background_calculation(result.operation.operation_id)
        session.cam_snapshot = self._cam_application.snapshot
        if accepted and session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(result.operation.operation_id)
            self._nc_export_service.mark_operation_stale(result.operation.operation_id)
            if result.artifact is not None:
                self._schedule_background_simulation(result.operation.operation_id)
        return accepted

    def compute_facing(self, operation_id: OperationId,
                       *, expected_generation: int | None = None,
                       face_resolver: Callable[[GeometryReference], ResolvedMachiningGeometry] | None = None,
                       cancellation: Callable[[], bool] | None = None,
                       progress: Callable[[CamCalculationProgress], None] | None = None,
                       ) -> FacingComputeResult:
        """Generate/publish one Facing operation through the project lifecycle gateway."""
        session = self._require_current()
        if expected_generation is not None and expected_generation != self._cam_application.generation:
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        with self._background_foreground("toolpath_calculation"):
            result = self._cam_application.compute_facing(
                session.root_path, operation_id, face_resolver=face_resolver,
                cancellation=cancellation, progress=progress,
            )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
            self._nc_export_service.mark_operation_stale(operation_id)
        if result.accepted and result.artifact is not None:
            self._schedule_background_simulation(operation_id)
        return result

    def compute_contour(self, operation_id: OperationId,
                        *, expected_generation: int | None = None,
                        profile_resolver: Callable[[GeometryReference], ResolvedContourProfile] | None = None,
                        cancellation: Callable[[], bool] | None = None,
                        progress: Callable[[CamCalculationProgress], None] | None = None,
                        ) -> ContourComputeResult:
        """Generate/publish one 2D Contour through the project lifecycle gateway."""
        session = self._require_current()
        if expected_generation is not None and expected_generation != self._cam_application.generation:
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        with self._background_foreground("toolpath_calculation"):
            result = self._cam_application.compute_contour(
                session.root_path, operation_id,
                profile_resolver=profile_resolver, cancellation=cancellation,
                progress=progress,
            )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
            self._nc_export_service.mark_operation_stale(operation_id)
        if result.accepted and result.artifact is not None:
            self._schedule_background_simulation(operation_id)
        return result

    def compute_pocket(self, operation_id: OperationId,
                       *, expected_generation: int | None = None,
                       geometry_resolver: Callable[[GeometryReference], ResolvedPocketGeometry] | None = None,
                       cancellation: Callable[[], bool] | None = None,
                       progress: Callable[[CamCalculationProgress], None] | None = None,
                       ) -> PocketComputeResult:
        """Generate/publish one Pocket operation through the project lifecycle gateway."""
        session = self._require_current()
        if expected_generation is not None and expected_generation != self._cam_application.generation:
            raise RuntimeError("CAM command belongs to an inactive project generation")
        before = self._cam_application.snapshot
        with self._background_foreground("toolpath_calculation"):
            result = self._cam_application.compute_pocket(
                session.root_path, operation_id, geometry_resolver=geometry_resolver,
                cancellation=cancellation,
                progress=progress,
            )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
            self._nc_export_service.mark_operation_stale(operation_id)
        if result.accepted and result.artifact is not None:
            self._schedule_background_simulation(operation_id)
        return result

    def resolve_persisted_material_state(self, operation_id: OperationId):
        """Resolve Rest provenance through the active project service boundary."""
        session = self._require_current()
        return self._cam_application.resolve_persisted_material_state(
            session.root_path, operation_id
        )

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
        with self._background_foreground("toolpath_calculation"):
            result = self._cam_application.compute_drilling(
                session.root_path, operation_id, geometry_resolver=geometry_resolver
            )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
            self._nc_export_service.mark_operation_stale(operation_id)
        if result.accepted and result.artifact is not None:
            self._schedule_background_simulation(operation_id)
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
        with self._background_foreground("toolpath_calculation"):
            result = self._cam_application.compute_tapping(
                session.root_path,
                operation_id,
                geometry_resolver=geometry_resolver,
            )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
            self._nc_export_service.mark_operation_stale(operation_id)
        if result.accepted and result.artifact is not None:
            self._schedule_background_simulation(operation_id)
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
        with self._background_foreground("toolpath_calculation"):
            result = self._cam_application.compute_reaming(
                session.root_path,
                operation_id,
                geometry_resolver=geometry_resolver,
            )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
            self._nc_export_service.mark_operation_stale(operation_id)
        if result.accepted and result.artifact is not None:
            self._schedule_background_simulation(operation_id)
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
        with self._background_foreground("toolpath_calculation"):
            result = self._cam_application.compute_boring(
                session.root_path,
                operation_id,
                geometry_resolver=geometry_resolver,
            )
        session.cam_snapshot = self._cam_application.snapshot
        if session.cam_snapshot != before:
            session.is_dirty = True
            self._simulation_runs.mark_stale(operation_id)
            self._nc_export_service.mark_operation_stale(operation_id)
        if result.accepted and result.artifact is not None:
            self._schedule_background_simulation(operation_id)
        return result

    def load_toolpath_artifact(self, operation_id: OperationId) -> ToolpathArtifact | None:
        """Load one verified derived artifact for presentation; never expose its path."""
        session = self._require_current()
        return self._cam_application.load_artifact(session.root_path, operation_id)

    def capture_post_source(self, operation_id: OperationId) -> PostSourceSnapshot:
        """Capture one immutable, native-free Post source from the active project.

        The UI uses this boundary for validation, generation and export.  No
        QWidget or mutable project object crosses the worker boundary.
        """
        session = self._require_current()
        snapshot = self._cam_application.snapshot
        operation = None
        setup = None
        for job in snapshot.jobs:
            for candidate_setup in job.setups:
                candidate = next(
                    (value for value in candidate_setup.operation_tree.operations
                     if value.operation_id == operation_id),
                    None,
                )
                if candidate is not None:
                    operation = candidate
                    setup = candidate_setup
                    break
            if operation is not None:
                break
        if operation is None or setup is None:
            raise ProjectError("Post source operation is missing")
        artifact = self.load_toolpath_artifact(operation_id)
        if artifact is None:
            raise ProjectError("Post source ToolpathArtifact is missing")
        assembly = next(
            (value for value in snapshot.tool_assemblies
             if value.assembly_id == operation.tool_assembly.assembly_id),
            None,
        )
        if assembly is None:
            raise ProjectError("Post source tool assembly is missing")
        tool = next(
            (value for value in snapshot.tool_definitions
             if value.tool_id == assembly.tool_id),
            None,
        )
        holder = next(
            (value for value in snapshot.holder_definitions
             if value.holder_id == assembly.holder_id),
            None,
        )
        machine = next(
            (value for value in snapshot.machine_definitions
             if value.machine_id == artifact.machine_id),
            None,
        )
        simulation = self._simulation_runs.result(operation_id)
        expected_simulation = simulation.input_fingerprint if simulation else None
        return PostSourceSnapshot(
            project_id=session.manifest.project_id,
            operation=operation,
            artifact=artifact,
            setup=setup,
            assembly=assembly,
            tool=tool,
            holder=holder,
            machine=machine,
            simulation_result=simulation,
            expected_simulation_input_fingerprint=expected_simulation,
        )

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

    def load_background_simulation_precompute(
        self,
        inputs: SimulationInputSnapshot,
    ) -> object | None:
        """Load an optional complete R242 stock checkpoint for manual Simulation."""
        if not self._background_simulation_enabled:
            return None
        session = self._require_current()
        coordinator = self._background_simulation_coordinator()
        load = None if coordinator is None else getattr(coordinator, "load_completed", None)
        if not callable(load):
            return None
        try:
            return load(
                project_root=session.root_path,
                project_id=session.manifest.project_id,
                project_generation=self._cam_application.generation,
                operation_id=inputs.operation.operation_id,
                inputs=inputs,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning("Cannot load background Simulation precompute", exc_info=True)
            return None

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
                self._cam_application.release_calculation_cache_references(
                    session.root_path, operation_id
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.warning(
                    "Không thể release shared CAM cache của operation %s",
                    operation_id,
                    exc_info=True,
                )
            try:
                self._simulation_cache.delete_operation(session.root_path, operation_id)
            except (OSError, RuntimeError):
                logger.warning(
                    "Không thể xóa simulation cache của operation %s",
                    operation_id,
                    exc_info=True,
                )

    def _mark_changed_operation_simulations(
        self,
        before: CamProjectSnapshot,
        after: CamProjectSnapshot,
    ) -> None:
        """Stale only runtime results whose operation dependency changed."""
        before_operations = {
            operation.operation_id: operation
            for job in before.jobs
            for setup in job.setups
            for operation in setup.operation_tree.operations
        }
        after_operations = {
            operation.operation_id: operation
            for job in after.jobs
            for setup in job.setups
            for operation in setup.operation_tree.operations
        }
        for operation_id in sorted(
            before_operations.keys() & after_operations.keys(),
            key=str,
        ):
            if before_operations[operation_id] != after_operations[operation_id]:
                self._simulation_runs.mark_stale(
                    operation_id,
                    "Phụ thuộc của nguyên công đã thay đổi.",
                )

    def _reconcile_nc_artifacts(
        self, session: ProjectSession, snapshot: CamProjectSnapshot
    ) -> None:
        """Invalidate managed NC entries whose operation/toolpath source changed."""
        current = {
            item.operation_id: (item.artifact_id, item.artifact_fingerprint)
            for item in snapshot.artifacts
        }
        try:
            self._nc_export_service.store.reconcile_sources(
                session.root_path,
                session.manifest.project_id,
                current,
            )
        except (OSError, NCArtifactStoreError):
            logger.warning("Không thể cập nhật trạng thái NC artifact", exc_info=True)
            return
        # Refresh the in-memory view without treating a persistence load as a
        # project edit. Post results remain runtime-only and are not regenerated.
        self._nc_export_service.bind_project(
            session.root_path,
            session.manifest.project_id,
            self._cam_application.generation,
        )

    def cad_view_state(self, source_id: UUID) -> CadViewState:
        """Return effective pending-or-persisted state for one project source."""
        if self._current_document is not None:
            document = self._current_document
            if source_id != document.state.identity:
                raise ProjectError(
                    f"CAD source is not the active standalone document: {source_id}"
                )
            if not document.display_state:
                return default_cad_view_state(source_id)
            return cad_view_state_from_dict(
                document.display_state,
                expected_source_id=source_id,
            )
        session = self._require_current()
        self._require_source(session, source_id)
        return session.cad_view_states.get(source_id, default_cad_view_state(source_id))

    def stage_cad_view_state(
        self, state: CadViewState
    ) -> ProjectSession | CadDocumentSession:
        """Stage validated CAD state in memory without writing project.db."""
        if not isinstance(state, CadViewState):
            raise TypeError("CAD view state is invalid")
        if self._current_document is not None:
            document = self._current_document
            if state.source_id != document.state.identity:
                raise ProjectError(
                    "CAD view state belongs to another standalone document"
                )
            document.display_state = cad_view_state_to_dict(state)
            document.mark_dirty(True)
            return document
        session = self._require_current()
        self._require_writable(session)
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
        with self._background_foreground("project_autosave"):
            return self._autosave_foreground(expected_project_id=expected_project_id)

    def _autosave_foreground(
        self, *, expected_project_id: UUID | None = None
    ) -> AutosaveSnapshot | None:
        session = self._require_current()
        self._require_writable(session)
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
            cam3d_config=session.cam3d_config,
            persisted_cam3d_config=session.persisted_cam3d_config,
            lathe_snapshot=session.lathe_snapshot,
            persisted_lathe_snapshot=session.persisted_lathe_snapshot,
            lathe_restore_diagnostics=session.lathe_restore_diagnostics,
            lathe_persistence_loaded=session.lathe_persistence_loaded,
            read_only=False,
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
        with self._background_foreground("project_save_as"):
            return self._save_as_foreground(parent_dir, project_name, overwrite)

    def _save_as_foreground(
        self,
        parent_dir: Path,
        project_name: str,
        overwrite: bool,
    ) -> ProjectSession:
        current = self._require_current()
        self._require_writable(current)
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
        if self._lifecycle_hook is not None:
            try:
                self._lifecycle_hook("PROJECT_CLOSED")
            except (RuntimeError, TypeError, ValueError):
                logger.warning("Stage 13B project lifecycle hook failed", exc_info=True)
        logger.info("Đã đóng dự án %s", self._current_project.root_path)
        self._cancel_background_project(self._current_project)
        self._simulation_runs.bind_project(None, None)
        if not self._current_project.read_only:
            self._session_locks.release(self._current_project.root_path)
        self._current_project = None
        self._cam_application.clear()
        self._nc_export_service.bind_project(None, None, None)
        self._project_opened_at = None
        self._project_session_id = None
        self._lifecycle_generation += 1

    def close_workspace(self, discard_changes: bool = False) -> None:
        """Close either mode while preserving dirty state by default."""
        if self._current_document is not None:
            if self._current_document.state.dirty and not discard_changes:
                raise UnsavedChangesError(
                    "Current CAD document contains unsaved changes"
                )
            document = self._current_document
            if self._lifecycle_hook is not None:
                try:
                    self._lifecycle_hook("PROJECT_UNLOADED")
                except (RuntimeError, TypeError, ValueError):
                    logger.warning("Stage 13B document lifecycle hook failed", exc_info=True)
            self._current_document = None
            self._document_container.close(document)
            self._lifecycle_generation += 1
            logger.info("Đã đóng tài liệu CAD %s", document.state.display_name)
            return
        self.close_project(discard_changes=discard_changes)

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

    @staticmethod
    def _validate_claim_geometry(claim: ClaimedGeometryRequest) -> None:
        request = claim.request
        if (
            not request.geometry_representation.exact_for_cam
            or not claim.payload_path.is_file()
            or claim.payload_path.stat().st_size <= 0
            or sha256_file(claim.payload_path) != request.payload_checksum
        ):
            raise GeometryTransferIntegrityError(
                "Không đủ dữ liệu hình học chính xác và nguyên vẹn để cập nhật CAM."
            )
        suffix = claim.payload_path.suffix.casefold()
        if suffix not in {".brep", ".brp", ".step", ".stp", ".iges", ".igs"}:
            raise GeometryTransferIntegrityError(
                "Representation hình học không phù hợp cho CAM chính xác."
            )

    @staticmethod
    def _geometry_apply_evidence(
        claim: ClaimedGeometryRequest,
        plan: GeometryApplyPlan,
        backup_root: Path,
        manifest_backup: Path,
        database_backup: Path,
        old_working_backup: Path | None,
        phase: GeometryApplyPhase,
    ) -> dict[str, object]:
        root = plan.source_path.parents[1]

        def relative(path: Path | None) -> str | None:
            if path is None:
                return None
            return path.relative_to(root).as_posix()

        return {
            "format": "HMS_GEOMETRY_APPLY_TRANSACTION",
            "format_version": 1,
            "request_id": str(claim.request.request_id),
            "target_project_id": str(claim.request.target_project_id),
            "phase": phase.value,
            "choice": plan.choice.value,
            "source_id": str(plan.source_id),
            "payload_checksum": claim.request.payload_checksum,
            "source_path": relative(plan.source_path),
            "working_path": relative(plan.working_path),
            "previous_working_path": relative(plan.previous_working_path),
            "backup_root": relative(backup_root),
            "manifest_backup": relative(manifest_backup),
            "database_backup": relative(database_backup),
            "old_working_backup": relative(old_working_backup),
            "created_at": datetime_to_json(utc_now()),
        }

    def _rollback_geometry_apply(
        self,
        project_root: Path,
        plan: GeometryApplyPlan | None,
        backup_root: Path,
    ) -> Exception | None:
        """Restore manifest/database and remove only this request's new assets."""
        try:
            if plan is not None:
                for candidate in (plan.source_path, plan.working_path):
                    if candidate.is_file() and (
                        sha256_file(candidate)
                        == plan.request.payload_checksum
                    ):
                        candidate.unlink()
            manifest_backup = backup_root / CAM_WORKSPACE_MANIFEST_FILENAME
            database_backup = backup_root / DATABASE_FILENAME
            if manifest_backup.is_file():
                temporary = (
                    project_root
                    / f".{CAM_WORKSPACE_MANIFEST_FILENAME}."
                    f"{uuid4().hex}.rollback"
                )
                try:
                    shutil.copy2(manifest_backup, temporary)
                    os.replace(
                        temporary,
                        project_root / CAM_WORKSPACE_MANIFEST_FILENAME,
                    )
                finally:
                    temporary.unlink(missing_ok=True)
            if database_backup.is_file():
                self._database.backup(
                    database_backup,
                    project_root / DATABASE_FILENAME,
                )
            return None
        except Exception as error:
            logger.exception("Rollback geometry transfer thất bại")
            return error

    @staticmethod
    def _archive_previous_working_geometry(
        project_root: Path,
        plan: GeometryApplyPlan,
    ) -> None:
        previous = plan.previous_working_path
        if previous is None or not previous.is_file():
            return
        replaced_root = project_root / REPLACED_DIRECTORY
        destination = replaced_root / (
            f"{previous.stem}-before-{plan.request.request_id.hex[:12]}"
            f"{previous.suffix}"
        )
        try:
            if not destination.exists():
                os.replace(previous, destination)
        except OSError:
            logger.warning(
                "Không thể chuyển working geometry cũ vào replaced; "
                "bản không còn tham chiếu được giữ nguyên.",
                exc_info=True,
            )

    def _recover_incomplete_geometry_transfers(
        self,
        project_root: Path,
    ) -> None:
        """Recover APPLYING requests only after the normal project lock is owned."""
        staging = (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_STAGING_DIRECTORY
        )
        if not staging.is_dir():
            return
        for request_path in sorted(staging.iterdir(), key=lambda item: item.name):
            if (
                not request_path.is_dir()
                or not request_path.name.endswith(APPLYING_SUFFIX)
            ):
                continue
            try:
                request = self._geometry_inbox.validate_request_directory(
                    request_path
                )
                claim = ClaimedGeometryRequest(
                    request=request,
                    request_path=request_path,
                    payload_path=(
                        request_path
                        / REQUEST_GEOMETRY_DIRECTORY
                        / request.payload_filename
                    ),
                )
                evidence = self._geometry_inbox.read_apply_evidence(
                    request_path
                )
                if evidence is None:
                    self._geometry_inbox.return_claim_to_pending(
                        project_root,
                        claim,
                        "Yêu cầu đang cập nhật được phục hồi trước khi thay đổi dự án.",
                    )
                    continue
                self._validate_geometry_apply_evidence(request, evidence)
                if self._geometry_apply_was_persisted(
                    project_root,
                    request,
                    evidence,
                ):
                    self._geometry_inbox.finish_applied(project_root, claim)
                    continue
                self._rollback_geometry_from_evidence(
                    project_root,
                    request,
                    evidence,
                )
                self._geometry_inbox.return_claim_to_pending(
                    project_root,
                    claim,
                    "Đã rollback lần cập nhật bị gián đoạn; có thể thử lại.",
                )
            except Exception as error:
                raise GeometryTransferRecoveryError(
                    f"Không thể phục hồi yêu cầu nạp 3D: {request_path.name}"
                ) from error

    @staticmethod
    def _validate_geometry_apply_evidence(
        request: GeometryTransferRequest,
        evidence: dict[str, object],
    ) -> None:
        """Reject ambiguous or redirected recovery evidence before mutation."""
        required = {
            "format",
            "format_version",
            "request_id",
            "target_project_id",
            "phase",
            "choice",
            "source_id",
            "payload_checksum",
            "source_path",
            "working_path",
            "previous_working_path",
            "backup_root",
            "manifest_backup",
            "database_backup",
            "old_working_backup",
            "created_at",
        }
        if set(evidence) != required:
            raise GeometryTransferRecoveryError(
                "Evidence cập nhật geometry thiếu hoặc thừa trường."
            )
        try:
            request_id = UUID(str(evidence["request_id"]))
            target_id = UUID(str(evidence["target_project_id"]))
            UUID(str(evidence["source_id"]))
            GeometryApplyPhase(str(evidence["phase"]))
            GeometryApplyChoice(str(evidence["choice"]))
        except (TypeError, ValueError) as error:
            raise GeometryTransferRecoveryError(
                "Identity hoặc trạng thái trong evidence không hợp lệ."
            ) from error
        if (
            evidence["format"] != "HMS_GEOMETRY_APPLY_TRANSACTION"
            or evidence["format_version"] != 1
            or request_id != request.request_id
            or target_id != request.target_project_id
            or evidence["payload_checksum"] != request.payload_checksum
            or not isinstance(evidence["created_at"], str)
        ):
            raise GeometryTransferRecoveryError(
                "Evidence cập nhật geometry không khớp request."
            )

        token = request.request_id.hex[:12]

        def relative_path(key: str, *, optional: bool = False) -> Path | None:
            value = evidence[key]
            if value is None and optional:
                return None
            if not isinstance(value, str):
                raise GeometryTransferRecoveryError(
                    f"Evidence path không hợp lệ: {key}"
                )
            path = Path(value)
            if (
                path.is_absolute()
                or value != path.as_posix()
                or ".." in path.parts
            ):
                raise GeometryTransferRecoveryError(
                    f"Evidence path không an toàn: {key}"
                )
            return path

        source = relative_path("source_path")
        working = relative_path("working_path")
        previous = relative_path("previous_working_path", optional=True)
        backup = relative_path("backup_root")
        manifest_backup = relative_path("manifest_backup")
        database_backup = relative_path("database_backup")
        old_backup = relative_path("old_working_backup", optional=True)
        if (
            source is None
            or source.parent != Path("source")
            or token not in source.name
            or working is None
            or working.parent != Path("working-geometry")
            or working.name != source.name
            or (
                previous is not None
                and previous.parent != Path("working-geometry")
            )
            or backup != Path("backups") / f"geometry-transfer-{request.request_id}"
            or manifest_backup != backup / CAM_WORKSPACE_MANIFEST_FILENAME
            or database_backup != backup / DATABASE_FILENAME
            or (
                old_backup is not None
                and old_backup.parent != backup / "previous-working"
            )
        ):
            raise GeometryTransferRecoveryError(
                "Evidence path không khớp transaction geometry."
            )

    def _geometry_apply_was_persisted(
        self,
        project_root: Path,
        request: GeometryTransferRequest,
        evidence: dict[str, object],
    ) -> bool:
        if evidence.get("phase") != GeometryApplyPhase.PERSISTED.value:
            return False
        manifest = self._manifest_store.load(project_root)
        matching = tuple(
            record
            for record in manifest.source_files
            if record.transfer_request_id == request.request_id
        )
        if len(matching) != 1:
            return False
        record = matching[0]
        source = project_root / Path(record.stored_path)
        working = (
            None
            if record.working_geometry_path is None
            else project_root / Path(record.working_geometry_path)
        )
        return (
            source.is_file()
            and working is not None
            and working.is_file()
            and sha256_file(source) == request.payload_checksum
            and sha256_file(working) == request.payload_checksum
        )

    def _rollback_geometry_from_evidence(
        self,
        project_root: Path,
        request: GeometryTransferRequest,
        evidence: dict[str, object],
    ) -> None:
        def resolve_relative(key: str) -> Path | None:
            value = evidence.get(key)
            if value is None:
                return None
            if not isinstance(value, str):
                raise GeometryTransferRecoveryError(
                    f"Evidence path không hợp lệ: {key}"
                )
            candidate = project_root / Path(value)
            resolved = candidate.resolve()
            root = project_root.resolve()
            if root not in resolved.parents:
                raise GeometryTransferRecoveryError(
                    f"Evidence path thoát project root: {key}"
                )
            return candidate

        for key, parent_name in (
            ("source_path", "source"),
            ("working_path", "working-geometry"),
        ):
            candidate = resolve_relative(key)
            if candidate is None or not candidate.exists():
                continue
            if (
                candidate.parent.resolve()
                != (project_root / parent_name).resolve()
                or not candidate.is_file()
                or sha256_file(candidate) != request.payload_checksum
            ):
                raise GeometryTransferRecoveryError(
                    "Từ chối xóa geometry asset không khớp evidence."
                )
            candidate.unlink()
        manifest_backup = resolve_relative("manifest_backup")
        database_backup = resolve_relative("database_backup")
        if manifest_backup is None or not manifest_backup.is_file():
            raise GeometryTransferRecoveryError(
                "Thiếu manifest backup cho rollback."
            )
        if database_backup is None or not database_backup.is_file():
            raise GeometryTransferRecoveryError(
                "Thiếu database backup cho rollback."
            )
        temporary = (
            project_root
            / f".{CAM_WORKSPACE_MANIFEST_FILENAME}.{uuid4().hex}.rollback"
        )
        try:
            shutil.copy2(manifest_backup, temporary)
            os.replace(
                temporary,
                project_root / CAM_WORKSPACE_MANIFEST_FILENAME,
            )
        finally:
            temporary.unlink(missing_ok=True)
        self._database.backup(
            database_backup,
            project_root / DATABASE_FILENAME,
        )

    def _activate(self, session: ProjectSession) -> ProjectSession:
        previous_project = self._current_project
        self._session_locks.acquire(session.root_path, session.manifest.project_id)
        try:
            return self._complete_activation(session)
        except Exception:
            self._session_locks.release(session.root_path)
            if self._current_project is session:
                self._current_project = previous_project
            raise

    def _complete_activation(self, session: ProjectSession) -> ProjectSession:
        self._validator.validate_manifest(session.manifest)
        self._database.validate(session.root_path / session.manifest.database)
        self._database.validate_project_identity(
            session.root_path / session.manifest.database,
            session.manifest.project_id,
            require_bound=(
                session.root_path / CAM_WORKSPACE_MANIFEST_FILENAME
            ).is_file(),
        )
        if self._lathe_persistence_enabled and not session.lathe_persistence_loaded:
            lathe_result = self._lathe_persistence.load_project(
                session.root_path / session.manifest.database,
                session.manifest.project_id,
                read_only=session.read_only,
            )
            session.lathe_snapshot = lathe_result.snapshot
            session.persisted_lathe_snapshot = lathe_result.snapshot
            session.lathe_restore_diagnostics = lathe_result.diagnostics
            session.lathe_persistence_loaded = True
        previous = self._current_project
        if previous is not None and previous.root_path.resolve() != session.root_path.resolve():
            try:
                self._cancel_background_project(previous)
                if not previous.read_only:
                    self._session_locks.release(previous.root_path)
            except Exception:
                if not session.read_only:
                    self._session_locks.release(session.root_path)
                raise
        self._current_project = session
        if session.cam3d_config is None:
            session.cam3d_config = Cam3DProjectConfig(session.manifest.project_id)
        if session.persisted_cam3d_config is None:
            session.persisted_cam3d_config = session.cam3d_config
        self._cam_application.load(session.cam_snapshot)
        try:
            self._cam_application.recover_calculation_cache(session.root_path)
        except OSError:
            logger.warning("Không thể dọn scratch cache CAM bị bỏ dở", exc_info=True)
        self._simulation_runs.bind_project(
            session.manifest.project_id,
            self._cam_application.generation,
        )
        self._nc_export_service.bind_project(
            session.root_path,
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
        if not session.read_only:
            self._cleanup_temp(session.root_path)
        previous_document = self._current_document
        self._current_document = None
        if previous_document is not None:
            self._document_container.close(previous_document)
        self._lifecycle_generation += 1
        self._project_opened_at = utc_now()
        self._project_session_id = self._session_locks.session_id
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

    def _background_simulation_coordinator(self):
        """Create the optional coordinator lazily, outside normal imports."""
        coordinator = self._background_simulation
        if coordinator is None and self._background_simulation_enabled:
            from hms_cadcam.simulation.background import (
                BackgroundSimulationCoordinator,
            )

            coordinator = BackgroundSimulationCoordinator()
            self._background_simulation = coordinator
        return coordinator

    def _background_foreground(self, name: str):
        if not self._background_simulation_enabled:
            return nullcontext()
        coordinator = self._background_simulation
        if coordinator is None:
            return nullcontext()
        foreground = getattr(coordinator, "foreground", None)
        return nullcontext() if not callable(foreground) else foreground(name)

    def _begin_background_calculation(self, operation_id: OperationId) -> None:
        if not self._background_simulation_enabled:
            return
        coordinator = self._background_simulation_coordinator()
        begin = None if coordinator is None else getattr(coordinator, "begin_foreground", None)
        if callable(begin) and operation_id not in self._background_calculation_claims:
            begin("toolpath_calculation")
            self._background_calculation_claims.add(operation_id)

    def _end_background_calculation(self, operation_id: OperationId) -> None:
        if operation_id not in self._background_calculation_claims:
            return
        self._background_calculation_claims.discard(operation_id)
        coordinator = self._background_simulation
        end = None if coordinator is None else getattr(coordinator, "end_foreground", None)
        if callable(end):
            end("toolpath_calculation")

    def _schedule_background_simulation(self, operation_id: OperationId) -> None:
        if not self._background_simulation_enabled:
            return
        session = self._require_current()
        if session.read_only:
            return
        try:
            coordinator = self._background_simulation_coordinator()
            if coordinator is None:
                return
            generation = self._cam_application.generation
            coordinator.schedule(
                project_root=session.root_path,
                project_id=session.manifest.project_id,
                project_generation=generation,
                operation_id=operation_id,
                load_inputs=lambda: self.capture_simulation_inputs(operation_id),
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning(
                "Background Simulation precompute is unavailable; CAM remains active",
                exc_info=True,
            )

    def _cancel_background_project(self, session: ProjectSession) -> None:
        coordinator = self._background_simulation
        if coordinator is None:
            return
        for operation_id in tuple(self._background_calculation_claims):
            self._end_background_calculation(operation_id)
        cancel = getattr(coordinator, "cancel_project", None)
        if callable(cancel):
            try:
                cancel(session.root_path, self._cam_application.generation)
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.warning(
                    "Background Simulation cleanup failed during project close",
                    exc_info=True,
                )

    @staticmethod
    def _require_writable(session: ProjectSession) -> None:
        if session.read_only:
            raise ProjectError("Read-only project cannot be staged or saved")

    def _require_document(self) -> CadDocumentSession:
        if self._current_document is None:
            raise ProjectError("No standalone HMS CAD document is currently open")
        return self._current_document

    def _rollback_new_cam_workspace(self, session: ProjectSession) -> None:
        """Remove only the just-created, identity-matching CAM workspace."""
        root = session.root_path
        try:
            manifest_path = root / CAM_WORKSPACE_MANIFEST_FILENAME
            if (
                not root.is_dir()
                or not manifest_path.is_file()
                or root.parent == root
            ):
                return
            manifest = self._loader.read_manifest(root)
            if manifest.project_id != session.manifest.project_id:
                logger.error(
                    "Từ chối rollback workspace do identity đã thay đổi: %s",
                    root,
                )
                return
            self._session_locks.release(root)
            shutil.rmtree(root)
            logger.info("Đã rollback workspace CAM chưa kích hoạt: %s", root)
        except Exception:
            logger.exception(
                "Không thể rollback workspace CAM chưa kích hoạt: %s",
                root,
            )

    @staticmethod
    def _require_source(session: ProjectSession, source_id: UUID) -> None:
        if not isinstance(source_id, UUID) or all(
            record.source_id != source_id for record in session.manifest.source_files
        ):
            raise ProjectError(f"CAD source is not part of the current project: {source_id}")
