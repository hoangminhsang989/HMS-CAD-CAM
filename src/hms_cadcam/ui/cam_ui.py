"""CAM 7B.1 tree, command area and conservative properties editor."""

from __future__ import annotations

import math
import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Callable
from uuid import UUID

from PySide6.QtCore import QEventLoop, QObject, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QApplication, QMessageBox, QPushButton, QSplitter, QTabWidget, QToolBar,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from hms_cadcam.cam.domain import (
    ArtifactState, ArtifactStatus, BoringBarGeometry, BoringCoolantMode,
    BoringRetractPolicy, BoringStrategy, BoringValidationError,
    BoxStock, CamJobId, CamNodeId, CamSurfaceSelectionId, ContentFingerprint,
    ContourCutDirection, ContourParameters, ContourProfileSource, ContourSide,
    DiagnosticCode, DirtyReason, DrillApproachPolicy, DrillDepthDefinition,
    DrillGeometryInput,
    DrillRetractPolicy, DrillValidationError, DrillingCycle, DrillingStrategy,
    FacingBoundarySource, FacingCutDirection, FacingParameters,
    FeedRate, FeedUnit,
    GeometryFingerprint, GeometryInputId, GeometryInputRole,
    GeometryReference, GeometryReferenceId,
    GeometryReferenceKind, GeometryRepresentationKind, GeometryResolutionStatus,
    HolePattern, HoleReference,
    Length, LengthUnit,
    MachineKind, MachineRequirement, Operation, OperationCapability,
    OperationFamily,
    OperationGeometryInput,
    OperationId, OperationParameterSet, Point3,
    PocketCuttingDirection, PocketDepthDefinition, PocketEntryPolicy,
    PocketGeometryInput, PocketStrategy,
    ReamingCoolantMode, ReamingRetractPolicy, ReamingStrategy,
    ReamingValidationError,
    ResolvedContourProfile, ResolvedMachiningGeometry, Revision, Setup, SetupId, SetupKind, SourceScope, StockKind,
    ResolvedDrillingGeometry, ResolvedPocketGeometry,
    SpindleDirection, SpindleSpeed, TappingHand, TappingStrategy,
    TappingSynchronizationPolicy, TappingValidationError, ToolAssemblyId,
    ToolAssemblyReference, ToolProfileSaveMode,
    ToolFamily,
    DEFAULT_TOOL_PROFILE_REGISTRY,
    preview_tool_profile_capture,
    Vector3, WcsFrame, WorkOffset,
    HMS_GEOMETRY_REFERENCE_SCHEME, HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.project.models import ProjectSession, UnitSystem
from hms_cadcam.project.exceptions import ProjectError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.cam.application import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    BoringGenerationError,
    BoringGenerator,
    ContourGenerationError,
    ContourGenerator,
    FacingGenerationError,
    FacingGenerator,
    DrillingGenerationError,
    DrillingGenerator,
    PocketGenerationError,
    PocketGenerator,
    ReamingGenerationError,
    ReamingGenerator,
    TappingGenerationError,
    TappingGenerator,
    basic_boring_resources,
    basic_drilling_resources,
    basic_mill_resources,
    basic_parallel_resources,
    basic_reaming_resources,
    basic_tapping_resources,
)
from hms_cadcam.cam.application.facing import resolve_planar_face_region
from hms_cadcam.viewer.toolpath import ToolpathPresentation
from hms_cadcam.cam.simulation import (
    CollisionBackend, CollisionScene, SimulationCacheStatus,
    SimulationInputSnapshot, SimulationIssueCode, SimulationPreflightError,
    SimulationProgress, SimulationRunHandle, SimulationRunRecord,
    SimulationSamplingPolicy, build_tool_envelope,
)
from hms_cadcam.viewer.simulation import (
    SimulationDisplayContext, SimulationDisplayPolicy,
)
from hms_cadcam.ui.simulation_ui import (
    SimulationIssueSelection, SimulationPanel,
)
from hms_cadcam.ui.post_ui import PostProcessorPanel
from hms_cadcam.ui.program_assembly_ui import ProgramAssemblyPanel
from hms_cadcam.ui.function_editor.model import (
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorPreviewRequest,
    PresentationValue,
)
from hms_cadcam.ui.function_editor.production import (
    FunctionEditorProductionSession,
    ToolProfileSaveInteraction,
)
from hms_cadcam.ui.function_editor.strategies import (
    ContourEditorContext,
    ContourEditorDraftContext,
    FacingEditorContext,
    FacingEditorDraftContext,
    FacingEditorVariant,
    PocketEditorContext,
    PocketEditorDraftContext,
    DrillingFamilyEditorContext,
    DrillingFamilyEditorDraftContext,
    DrillingFamilyEditorKind,
    build_boring_schema,
    build_contour_schema,
    build_drilling_schema,
    build_facing_schema,
    build_planar_face_facing_schema,
    build_pocket_schema,
    build_reaming_schema,
    build_tapping_schema,
    contour_applied_values,
    facing_applied_values,
    pocket_applied_values,
    drilling_family_applied_values,
    drilling_family_geometry_values,
    prepare_contour_update,
    prepare_facing_update,
    prepare_pocket_update,
    prepare_drilling_family_update,
    ParallelEditorContext,
    ParallelEditorDraftContext,
    build_parallel_schema,
    parallel_applied_values,
    parallel_draft_derived_values,
    parallel_validation_diagnostics,
    prepare_parallel_update,
    ZLevelEditorContext,
    ZLevelEditorDraftContext,
    build_z_level_schema,
    prepare_z_level_update,
    z_level_applied_values,
    z_level_draft_derived_values,
    z_level_validation_diagnostics,
)
from hms_cadcam.cam.cam3d import (
    Cam3DCalculationRequest,
    Cam3DGeometryService,
    Cam3DProjectConfig,
)
from hms_cadcam.cam.operation_creation import (
    OperationCreationSession,
    default_drilling_creation_strategy,
)
from hms_cadcam.ui.operation_creation_adapter import Stage16AOperationCreationAdapter
from hms_cadcam.ui.operation_creation_wizard import OperationCreationWizard
from hms_cadcam.cam.cam3d.parallel import (
    PARALLEL_FINISHING_ALGORITHM_VERSION,
    ParallelGeometryEvidence,
    ParallelFinishingComputeResult,
    ParallelFinishingParameters,
    ParallelProgress,
    calculate_and_publish_parallel_finishing,
)
from hms_cadcam.cam.cam3d.zlevel import (
    Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    ZLevelFinishingComputeResult,
    ZLevelFinishingParameters,
    ZLevelGeometryEvidence,
    ZLevelProgress,
    calculate_and_publish_z_level_finishing,
)
from hms_cadcam.cam.domain import MachiningZone3DId
from hms_cadcam.ui.parallel_finishing_worker import ParallelFinishingTask
from hms_cadcam.ui.zlevel_finishing_worker import ZLevelFinishingTask
from hms_cadcam.ui.localization import (
    display_value,
    localize_widget_tree,
    translate_status,
    ui_text,
)

_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 20
_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 21
_JOB_ROLE = int(Qt.ItemDataRole.UserRole) + 22
_SETUP_ROLE = int(Qt.ItemDataRole.UserRole) + 23

logger = logging.getLogger(__name__)


class CamWorkspace(QWidget):
    """One self-contained CAM management surface; CAD viewport remains central."""

    message = Signal(str)
    projection_changed = Signal()
    selection_identity_changed = Signal(object)
    parallel_progress_changed = Signal(object)
    parallel_calculation_active = Signal(bool)
    operation_created = Signal(str)

    def __init__(self, service: ProjectService,
                 source_provider: Callable[[], UUID | None],
                 pick_provider: Callable[[], GeometryReference] | None = None,
                 toolpath_display: Callable[[object], object] | None = None,
                 toolpath_clear: Callable[[], None] | None = None,
                 parent: QWidget | None = None,
                 toolpath_remove: Callable[[OperationId], None] | None = None,
                 face_resolver: Callable[[GeometryReference], ResolvedMachiningGeometry] | None = None,
                 contour_pick_provider: Callable[[], GeometryReference] | None = None,
                 profile_resolver: Callable[[GeometryReference], ResolvedContourProfile] | None = None,
                 pocket_resolver: Callable[[GeometryReference], ResolvedPocketGeometry] | None = None,
                 drilling_pick_provider: Callable[
                     [Vector3], HoleReference | HolePattern
                 ] | None = None,
                 drilling_resolver: Callable[
                     [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
                 ] | None = None,
                 simulation_scene_builder: Callable[
                     [SimulationInputSnapshot], tuple[CollisionScene, CollisionBackend]
                 ] | None = None,
                 parallel_surface_provider: Callable[[], tuple[object, ...]] | None = None,
                 parallel_adapter_provider: Callable[[], object] | None = None,
                 parallel_geometry_bounds_provider: Callable[[], tuple[object, ...]] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CamWorkspace")
        self._service = service
        self._source_provider = source_provider
        self._pick_provider = pick_provider
        self._toolpath_display = toolpath_display
        self._toolpath_clear = toolpath_clear
        self._toolpath_remove = toolpath_remove
        self._displayed_operation_id: OperationId | None = None
        self._toolpath_visibility: dict[OperationId, bool] = {}
        self._face_resolver = face_resolver
        self._contour_pick_provider = contour_pick_provider
        self._profile_resolver = profile_resolver
        self._pocket_resolver = pocket_resolver
        self._drilling_pick_provider = drilling_pick_provider
        self._drilling_resolver = drilling_resolver
        self._simulation_scene_builder = simulation_scene_builder
        self._parallel_surface_provider = parallel_surface_provider
        self._parallel_adapter_provider = parallel_adapter_provider
        self._parallel_geometry_bounds_provider = parallel_geometry_bounds_provider
        self._operation_creation_wizard: OperationCreationWizard | None = None
        self._parallel_drafts: dict[OperationId, tuple[object, ...]] = {}
        self._parallel_draft_evidence: dict[
            OperationId, ParallelGeometryEvidence
        ] = {}
        self._parallel_reports: dict[OperationId, object] = {}
        self._parallel_task: ParallelFinishingTask | None = None
        self._parallel_task_generation: int | None = None
        self._z_level_drafts: dict[OperationId, tuple[object, ...]] = {}
        self._z_level_draft_evidence: dict[
            OperationId, ZLevelGeometryEvidence
        ] = {}
        self._z_level_reports: dict[OperationId, object] = {}
        self._z_level_task: ZLevelFinishingTask | None = None
        self._z_level_task_generation: int | None = None
        self._simulation_policies: dict[
            OperationId, tuple[SimulationSamplingPolicy, SimulationDisplayPolicy]
        ] = {}
        self._simulation_cache_attempts: set[tuple[OperationId, str]] = set()
        self._simulation_handle: SimulationRunHandle | None = None
        self._simulation_project_id: UUID | None = None
        self._picked_reference: GeometryReference | None = None
        self._picked_hole_reference: HoleReference | None = None
        self._picked_hole_source: HoleReference | HolePattern | None = None
        self._picked_reference_resolved = False
        self._geometry_resolution_error = ""
        self._pocket_drafts: dict[
            OperationId, tuple[dict[str, object], GeometryReference | None]
        ] = {}
        self._drilling_drafts: dict[
            OperationId,
            tuple[dict[str, object], HoleReference | HolePattern | None],
        ] = {}
        self._tapping_drafts: dict[
            OperationId,
            tuple[dict[str, object], HoleReference | HolePattern | None],
        ] = {}
        self._reaming_drafts: dict[
            OperationId,
            tuple[dict[str, object], HoleReference | HolePattern | None],
        ] = {}
        self._boring_drafts: dict[
            OperationId,
            tuple[dict[str, object], HoleReference | HolePattern | None],
        ] = {}
        self._active_editor_operation_id: OperationId | None = None
        self._active_editor_strategy_key: str | None = None
        self._production_facing_editors_enabled = True
        self._production_contour_editor_enabled = True
        self._production_pocket_editor_enabled = True
        self._production_drilling_family_editors_enabled = True
        self._production_parallel_editor_enabled = True
        self._production_z_level_editor_enabled = True
        self._child_dialog_opener: Callable[..., object] | None = None
        self._facing_preview: tuple[str, ...] = ()
        self._contour_preview: tuple[str, ...] = ()
        self._pocket_preview: tuple[str, ...] = ()
        self._generation: int | None = None
        self._guard = False
        self._selected_key: tuple[str, str] | None = None
        self.tree = QTreeWidget()
        self.tree.setObjectName("CamOperationTree")
        self.tree.setHeaderLabels(["Dự án CAM / Nguyên công", "Trạng thái"])
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemChanged.connect(self._item_changed)
        self.editor = _CamPropertiesEditor(self._apply_properties)
        self.simulation_panel = SimulationPanel()
        self.post_panel = PostProcessorPanel(service)
        self.program_assembly_panel = ProgramAssemblyPanel(service)
        self.post_tabs = QTabWidget()
        self.post_tabs.setObjectName("CamPostTabs")
        self.post_tabs.addTab(self.post_panel, "Bộ xử lý Post")
        self.post_tabs.addTab(self.program_assembly_panel, "Lắp ráp chương trình")
        self.splitter = QSplitter()
        self.splitter.setObjectName("ClassicCamWorkspaceSplitter")
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.editor)
        self.splitter.addWidget(self.simulation_panel)
        self.splitter.addWidget(self.post_tabs)
        self.splitter.setSizes([280, 320, 360, 460])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QToolBar("Lệnh CAM")
        layout.addWidget(self.toolbar)
        layout.addWidget(self.splitter)
        self.actions = self._actions()
        self.editor.draft_changed.connect(self._update_generate_action)
        self.simulation_panel.run_requested.connect(self._run_simulation)
        self.simulation_panel.cancel_requested.connect(self._cancel_simulation)
        self.simulation_panel.clear_requested.connect(self._clear_simulation_result)
        self.simulation_panel.visibility_requested.connect(
            self._set_simulation_visibility
        )
        self.simulation_panel.policy_applied.connect(
            self._apply_simulation_policy
        )
        self.simulation_panel.issue_focus_requested.connect(
            self._focus_simulation_issue
        )
        self.simulation_panel.issue_selection_cleared.connect(
            self._clear_simulation_issue_focus
        )
        self.post_panel.message.connect(self.message.emit)
        self.program_assembly_panel.message.connect(self.message.emit)
        for key in (
            "job", "setup", "resources", "parallel_resources",
            "tapping_resources", "reaming_resources", "boring_resources",
            "group", "create_operation", "operation", "contour_operation",
            "pocket_operation", "parallel_operation", "z_level_operation",
            "drilling_operation", "tapping_operation", "reaming_operation",
            "boring_operation", "generate", "visibility", "pick", "clear_pick",
            "up", "down", "delete",
        ):
            self.toolbar.addAction(self.actions[key])
        self.bind_project(service.current_project)
        localize_widget_tree(self)

    def _actions(self) -> dict[str, QAction]:
        definitions = {
            "job": ("Tạo công việc", self.create_job), "setup": ("Tạo thiết lập", self.create_setup),
            "resources": ("Tạo Tool và máy cơ bản", self.create_basic_resources),
            "parallel_resources": (
                "Tạo Tool cầu/Máy cho Gia công tinh song song",
                self.create_basic_parallel_resources,
            ),
            "tapping_resources": (
                "Tạo Tool taro và máy cơ bản",
                self.create_basic_tapping_resources,
            ),
            "reaming_resources": (
                "Tạo Tool doa và máy cơ bản",
                self.create_basic_reaming_resources,
            ),
            "boring_resources": (
                "Tạo Tool cán khoét và máy cơ bản",
                self.create_basic_boring_resources,
            ),
            "group": ("Thêm nhóm", self.add_group),
            "create_operation": (ui_text("Create operation"), self.open_operation_creation),
            "operation": ("Thêm Phay mặt 2.5D", self.add_operation),
            "contour_operation": ("Thêm Phay biên dạng 2D", self.add_contour_operation),
            "pocket_operation": ("Thêm Phay hốc 2.5D", self.add_pocket_operation),
            "parallel_operation": ("Thêm Gia công tinh song song", self.add_parallel_operation),
            "z_level_operation": (
                "Thêm Gia công tinh theo cao độ Z",
                self.add_z_level_operation,
            ),
            "drilling_operation": ("Thêm Khoan", self.add_drilling_operation),
            "tapping_operation": ("Thêm Taro", self.add_tapping_operation),
            "reaming_operation": ("Thêm Doa lỗ", self.add_reaming_operation),
            "boring_operation": ("Thêm Khoét lỗ", self.add_boring_operation),
            "generate": ("Tạo/Tính lại", self.generate_selected),
            "visibility": ("Hiện/ẩn toolpath", self.toggle_toolpath_visibility),
            "pick": ("Liên kết/Liên kết lại hình học", self.pick_geometry),
            "clear_pick": ("Xóa hình học", self.clear_geometry_pick),
            "up": ("Lên", lambda: self.move_selected(-1)),
            "down": ("Xuống", lambda: self.move_selected(1)),
            "delete": ("Xóa", self.delete_selected),
        }
        result = {}
        for key, (label, callback) in definitions.items():
            action = QAction(label, self)
            action.setObjectName(f"Cam{key.title()}Action")
            action.triggered.connect(callback)
            result[key] = action
        return result

    def bind_project(self, session: object) -> None:
        """Clear old identities before rendering a new project snapshot."""
        if self._operation_creation_wizard is not None:
            self._operation_creation_wizard.close()
            self._operation_creation_wizard = None
        if self._parallel_task is not None:
            self._parallel_task.abandon()
            self._parallel_task = None
            self.parallel_calculation_active.emit(False)
        self._parallel_task_generation = None
        self._parallel_drafts.clear()
        self._parallel_draft_evidence.clear()
        self._parallel_reports.clear()
        if self._z_level_task is not None:
            self._z_level_task.abandon()
            self._z_level_task = None
            self.parallel_calculation_active.emit(False)
        self._z_level_task_generation = None
        self._z_level_drafts.clear()
        self._z_level_draft_evidence.clear()
        self._z_level_reports.clear()
        self._simulation_handle = None
        self._simulation_project_id = None
        self._simulation_policies.clear()
        self._simulation_cache_attempts.clear()
        self.simulation_panel.clear_source()
        self.post_panel.bind_project(session)
        self.program_assembly_panel.bind_project(session)
        self.simulation_panel.set_policy(
            SimulationSamplingPolicy(),
            SimulationDisplayPolicy(),
        )
        self._clear_simulation_issue_focus()
        self._selected_key = None
        self._picked_reference = None
        self._picked_hole_reference = None
        self._picked_hole_source = None
        self._picked_reference_resolved = False
        self._geometry_resolution_error = ""
        self._pocket_drafts.clear()
        self._drilling_drafts.clear()
        self._tapping_drafts.clear()
        self._reaming_drafts.clear()
        self._boring_drafts.clear()
        self._toolpath_visibility.clear()
        self._active_editor_operation_id = None
        self._active_editor_strategy_key = None
        self._facing_preview = ()
        self._contour_preview = ()
        self.editor.clear()
        if self._toolpath_clear is not None:
            self._toolpath_clear()
        self._displayed_operation_id = None
        if not isinstance(session, ProjectSession):
            self._generation = None
            self._render(None)
            return
        self._generation = self._service.cam_generation
        self._simulation_project_id = session.manifest.project_id
        self._render(None)

    def open_operation_creation(self) -> bool:
        """Open the bounded Stage16A wizard without mutating the CAM snapshot."""
        if not self._service.has_project or self._generation is None:
            self._error("Hãy mở một dự án CAM trước khi tạo nguyên công.")
            return False
        context = self._tree_context()
        project = self._service.current_project
        if context is None or project is None:
            self._error(
                "Hãy chọn công việc CAM, thiết lập hoặc nhóm đích trước khi "
                "tạo nguyên công."
            )
            return False
        job_id, setup_id, _tree, parent_id = context
        adapter = Stage16AOperationCreationAdapter(
            self._service,
            surface_provider=self._parallel_surface_provider,
            geometry_bounds_provider=self._parallel_geometry_bounds_provider,
            drilling_pick_provider=self._drilling_pick_provider,
            drilling_resolver=self._drilling_resolver,
        )
        session = OperationCreationSession.start(
            project_id=project.manifest.project_id,
            project_generation=self._generation,
            job_id=job_id,
            setup_id=setup_id,
            parent_node_id=parent_id,
        )
        wizard = OperationCreationWizard(session, adapter, self)
        wizard.operation_created.connect(self._operation_creation_finished)
        wizard.session_changed.connect(
            lambda _session: self._operation_creation_live_update()
        )
        self.projection_changed.connect(wizard.refresh_live_state)
        wizard.finished.connect(
            lambda _result, owned=wizard: self._dispose_operation_creation_wizard(owned)
        )
        self._operation_creation_wizard = wizard
        wizard.show()
        wizard.raise_()
        wizard.activateWindow()
        return True

    def _operation_creation_finished(self, operation_id: str) -> None:
        self.refresh(("operation", operation_id))
        self.projection_changed.emit()
        self.operation_created.emit(operation_id)

    def _operation_creation_live_update(self) -> None:
        if self._operation_creation_wizard is not None:
            self._operation_creation_wizard.refresh_live_state()

    def _dispose_operation_creation_wizard(
        self, wizard: OperationCreationWizard
    ) -> None:
        try:
            self.projection_changed.disconnect(wizard.refresh_live_state)
        except (RuntimeError, TypeError):
            pass
        if self._operation_creation_wizard is wizard:
            self._operation_creation_wizard = None

    def refresh(self, preserve: tuple[str, str] | None = None) -> None:
        if self._generation is None or self._generation != self._service.cam_generation:
            return
        self._render(preserve or self._selected_key)

    @property
    def selected_identity(self) -> tuple[str, str] | None:
        """Return the transient CAM selection as its stable presentation key."""
        return self._selected_key

    def select_identity(self, kind: str, identity: str) -> bool:
        """Select one classic coordinator item by stable typed presentation ID."""
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if (
                item.data(0, _KIND_ROLE) == kind
                and item.data(0, _ID_ROLE) == identity
            ):
                self.tree.setCurrentItem(item)
                return True
            iterator += 1
        return False

    def selection_exists(self, selection_key: tuple[str, str]) -> bool:
        """Return whether one stable classic-tree selection still exists."""
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if (
                item.data(0, _KIND_ROLE) == selection_key[0]
                and item.data(0, _ID_ROLE) == selection_key[1]
            ):
                return True
            iterator += 1
        return False

    def report_function_editor_fallback(self, message: str) -> None:
        """Expose a safe production-schema fallback without mutating CAM state."""
        logger.error("Production Function Editor fallback: %s", message)
        self._error(f"Trình chỉnh sửa chức năng dự phòng: {ui_text(message)}")

    def set_child_dialog_opener(
        self, opener: Callable[..., object] | None
    ) -> None:
        """Route complex editor details through the shared popup child slot."""
        self._child_dialog_opener = opener

    def production_function_editor_session(
        self,
    ) -> FunctionEditorProductionSession | None:
        """Resolve a migrated milling editor or request one safe legacy fallback."""
        if not self._service.has_project:
            return None
        item = self.tree.currentItem()
        if item is None or item.data(0, _KIND_ROLE) != "operation":
            return None
        job = self._find_job(item)
        setup = self._find_setup(item, job)
        if job is None or setup is None or self._generation is None:
            return None
        node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
        node = setup.operation_tree.get_node(node_id)
        if node.operation_id is None:
            return None
        operation = setup.operation_tree.get_operation(node.operation_id)
        if operation.strategy_key == "parallel_finishing_3d":
            if not self._production_parallel_editor_enabled:
                return None
            return self._parallel_production_session(
                job.job_id, setup, node_id, node.name, operation
            )
        if operation.strategy_key == "z_level_finishing_3d":
            if not self._production_z_level_editor_enabled:
                return None
            return self._z_level_production_session(
                job.job_id, setup, node_id, node.name, operation
            )
        if operation.strategy_key == "contour_2d":
            if not self._production_contour_editor_enabled:
                return None
            return self._contour_production_session(
                job.job_id, setup, node_id, node.name, operation
            )
        if operation.strategy_key == "pocket_2_5d":
            if not self._production_pocket_editor_enabled:
                return None
            return self._pocket_production_session(
                job.job_id, setup, node_id, node.name, operation
            )
        hole_kind = {
            "drilling_v1": DrillingFamilyEditorKind.DRILLING,
            "tapping_v1": DrillingFamilyEditorKind.TAPPING,
            "reaming_v1": DrillingFamilyEditorKind.REAMING,
            "boring_v1": DrillingFamilyEditorKind.BORING,
        }.get(operation.strategy_key)
        if hole_kind is not None:
            if not self._production_drilling_family_editors_enabled:
                return None
            return self._drilling_family_production_session(
                job.job_id, setup, node_id, node.name, operation, hole_kind
            )
        if not self._production_facing_editors_enabled:
            return None
        if operation.strategy_key != "facing_2_5d":
            return None
        parameters = FacingParameters.from_operation_parameters(operation.parameters)
        variant = (
            FacingEditorVariant.STOCK
            if parameters.boundary_source is FacingBoundarySource.STOCK_BOX
            else FacingEditorVariant.PLANAR_FACE
        )
        geometry_reference = (
            operation.geometry_inputs[0].reference
            if len(operation.geometry_inputs) == 1
            else None
        )
        snapshot = self._service.cam_snapshot
        context = FacingEditorContext(
            node.name,
            operation,
            setup,
            snapshot.tool_assemblies,
            snapshot.tool_definitions,
            snapshot.holder_definitions,
            snapshot.machine_definitions,
            geometry_reference,
            self._picked_reference_resolved,
        )
        draft = FacingEditorDraftContext(geometry_reference)
        schema = (
            build_facing_schema(context)
            if variant is FacingEditorVariant.STOCK
            else build_planar_face_facing_schema(context)
        )
        applied = facing_applied_values(context, variant)
        project = self._service.current_project
        if project is None:
            return None
        return FunctionEditorProductionSession(
            selection_key=("operation", str(node_id)),
            schema=schema,
            applied_values=tuple(
                (field.field_id, applied[field.field_id]) for field in schema.fields
            ),
            project_key=str(project.manifest.project_id),
            operation_key=str(operation.operation_id),
            generation=self._generation,
            apply_callback=lambda values: self._apply_facing_production(
                job.job_id, setup.setup_id, node_id, context, draft, variant, values
            ),
            validation_callback=lambda values: self._validate_facing_production(
                schema, context, draft, variant, values
            ),
            preview_callback=lambda request: self._preview_facing_production(
                schema, context, draft, variant, request
            ),
            calculate_callback=lambda values: self._calculate_facing_production(
                context, variant, values
            ),
            field_action_callback=lambda action_id, values: self._facing_field_action(
                context, draft, variant, action_id, values
            ),
        )

    def _z_level_production_session(
        self,
        job_id: CamJobId,
        setup: Setup,
        node_id: CamNodeId,
        operation_name: str,
        operation: Operation,
    ) -> FunctionEditorProductionSession | None:
        """Build one native-free Z-Level session over applied project state."""
        if self._generation is None:
            return None
        project = self._service.current_project
        if project is None:
            return None
        parameters = ZLevelFinishingParameters.from_operation_parameters(
            operation.parameters
        )
        zone = next(
            (
                item
                for item in self._service.cam3d_config.zones
                if item.zone_id == parameters.zone_id
            ),
            None,
        )
        surfaces = (
            zone.part_surfaces.selection.surfaces if zone is not None else ()
        )
        draft_surfaces = self._z_level_drafts.get(
            operation.operation_id, surfaces
        )
        snapshot = self._service.cam_snapshot
        context = ZLevelEditorContext(
            operation_name,
            operation,
            setup,
            job_id,
            project.manifest.project_id,
            zone,
            snapshot.tool_assemblies,
            snapshot.tool_definitions,
            snapshot.holder_definitions,
            snapshot.machine_definitions,
            self._service.load_toolpath_artifact(operation.operation_id),
            self._z_level_reports.get(operation.operation_id),  # type: ignore[arg-type]
            geometry_resolved=(
                zone is not None
                and bool(surfaces)
                and {item.geometry.reference_id for item in surfaces}
                == {
                    item.reference.reference_id
                    for item in operation.geometry_inputs
                    if item.role is GeometryInputRole.DRIVE_GEOMETRY
                }
            ),
            geometry_diagnostic=(
                "Thiếu vùng gia công"
                if zone is None
                else "Tham chiếu bề mặt đã stale"
                if not surfaces
                else ""
            ),
            geometry_evidence=self._z_level_draft_evidence.get(
                operation.operation_id
            ),
        )
        draft = ZLevelEditorDraftContext(
            tuple(draft_surfaces),  # type: ignore[arg-type]
            geometry_evidence=self._z_level_draft_evidence.get(
                operation.operation_id
            ),
        )
        schema = build_z_level_schema(context)
        applied = z_level_applied_values(context)
        return FunctionEditorProductionSession(
            selection_key=("operation", str(node_id)),
            schema=schema,
            applied_values=tuple(
                (field.field_id, applied[field.field_id]) for field in schema.fields
            ),
            project_key=str(project.manifest.project_id),
            operation_key=str(operation.operation_id),
            generation=self._generation,
            apply_callback=lambda values: self._apply_z_level_production(
                job_id, setup.setup_id, node_id, context, draft, values
            ),
            validation_callback=lambda values: self._validate_z_level_production(
                schema, context, draft, values
            ),
            preview_callback=lambda request: self._preview_z_level_production(
                context, draft, request
            ),
            calculate_callback=lambda values: self._calculate_z_level_production(
                context, draft, values
            ),
            draft_transform_callback=lambda values: z_level_draft_derived_values(
                context, draft, values
            ),
            field_action_callback=lambda action_id, values: self._z_level_field_action(
                context, draft, action_id, values
            ),
            tool_profile_interaction_callback=lambda values, changed: (
                self._tool_profile_save_interaction(
                    "z_level_finishing_3d",
                    operation.operation_id,
                    values,
                    changed,
                )
            ),
        )

    def _z_level_context_is_current(
        self, context: ZLevelEditorContext
    ) -> bool:
        current = self._current_facing_operation(context.operation.operation_id)
        if current is None or self._generation != self._service.cam_generation:
            return False
        return (
            current.revision == context.operation.revision
            and current.parameters == context.operation.parameters
            and current.geometry_inputs == context.operation.geometry_inputs
            and current.tool_assembly == context.operation.tool_assembly
            and current.machine_requirement == context.operation.machine_requirement
            and current.enabled == context.operation.enabled
        )

    def _validate_z_level_production(
        self,
        schema,
        context: ZLevelEditorContext,
        draft: ZLevelEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> tuple[FunctionEditorDiagnostic, ...]:
        if not self._z_level_context_is_current(context):
            return (
                FunctionEditorDiagnostic(
                    "z_level.stale_editor",
                    "Nguyên công gia công theo cao độ Z đã thay đổi; "
                    "hãy mở lại trình chỉnh sửa.",
                    FunctionEditorDiagnosticSeverity.ERROR,
                    "operation_name",
                    "operation",
                ),
            )
        return z_level_validation_diagnostics(schema, context, draft, values)

    def _z_level_field_action(
        self,
        context: ZLevelEditorContext,
        draft: ZLevelEditorDraftContext,
        action_id: str,
        _values: Mapping[str, PresentationValue],
    ) -> Mapping[str, PresentationValue] | None:
        if action_id == "select_z_level_faces":
            if self._parallel_surface_provider is None:
                raise ValueError("Bộ chọn bề mặt CAM 3D chưa sẵn sàng.")
            selected = self._parallel_surface_provider()
            if not selected:
                raise ValueError("Hãy chọn một hoặc nhiều mặt BRep trong viewport.")
            merged = {_parallel_surface_key(item): item for item in draft.surfaces}
            for item in selected:
                if not hasattr(item, "geometry"):
                    raise TypeError("Bộ chọn trả về tham chiếu mặt không hợp lệ.")
                merged.setdefault(_parallel_surface_key(item), item)
            draft.surfaces = tuple(
                sorted(merged.values(), key=lambda item: item.fingerprint.digest)
            )
            if self._parallel_geometry_bounds_provider is not None:
                bounds = self._parallel_geometry_bounds_provider()
                if len(bounds) != len(selected):
                    raise ValueError(
                        "Hộp bao hình học đã stale; hãy chọn lại các mặt."
                    )
                evidence = self._z_level_evidence_from_bounds(context, bounds)
                draft.geometry_evidence = self._union_z_level_evidence(
                    draft.geometry_evidence, evidence
                )
                self._z_level_draft_evidence[
                    context.operation.operation_id
                ] = draft.geometry_evidence
            self._z_level_drafts[context.operation.operation_id] = draft.surfaces
            return self._z_level_geometry_presentation(context, draft.surfaces)
        if action_id == "clear_z_level_faces":
            draft.surfaces = ()
            draft.geometry_evidence = None
            self._z_level_draft_evidence.pop(
                context.operation.operation_id, None
            )
            self._z_level_drafts[context.operation.operation_id] = ()
            return self._z_level_geometry_presentation(context, ())
        if action_id == "reselect_z_level_faces":
            draft.surfaces = ()
            draft.geometry_evidence = None
            self._z_level_draft_evidence.pop(
                context.operation.operation_id, None
            )
            return self._z_level_field_action(
                context, draft, "select_z_level_faces", _values
            )
        if action_id == "remove_z_level_faces":
            if self._parallel_surface_provider is None:
                raise ValueError("Bộ chọn bề mặt CAM 3D chưa sẵn sàng.")
            selected = self._parallel_surface_provider()
            removed = {_parallel_surface_key(item) for item in selected}
            draft.surfaces = tuple(
                item
                for item in draft.surfaces
                if _parallel_surface_key(item) not in removed
            )
            draft.geometry_evidence = None
            self._z_level_draft_evidence.pop(
                context.operation.operation_id, None
            )
            self._z_level_drafts[context.operation.operation_id] = draft.surfaces
            return self._z_level_geometry_presentation(context, draft.surfaces)
        if action_id == "open_z_level_safety_details":
            self._show_z_level_safety_details(context)
            return None
        if action_id == "open_z_level_simulation":
            artifact = context.artifact
            from hms_cadcam.cam.cam3d.zlevel import (
                z_level_artifact_has_safe_contract,
            )

            if (
                artifact is None
                or context.operation.artifact_state.status is not ArtifactStatus.VALID
                or artifact.operation_revision != context.operation.revision
                or not z_level_artifact_has_safe_contract(artifact)
            ):
                raise ValueError(
                    "Mô phỏng yêu cầu artifact READY + SAFE algorithm v2 hiện hành."
                )
            self._run_simulation()
            return None
        raise ValueError(
            f"Thao tác gia công theo cao độ Z không được hỗ trợ: {action_id}"
        )

    @staticmethod
    def _z_level_evidence_from_bounds(
        context: ZLevelEditorContext,
        bounds: tuple[object, ...],
    ) -> ZLevelGeometryEvidence:
        frame = context.setup.wcs
        origin = frame.origin
        projected: list[tuple[float, float, float]] = []
        for value in bounds:
            coordinates = (
                getattr(value, "x_min"),
                getattr(value, "y_min"),
                getattr(value, "z_min"),
                getattr(value, "x_max"),
                getattr(value, "y_max"),
                getattr(value, "z_max"),
            )
            if not all(isinstance(item, (int, float)) for item in coordinates):
                raise TypeError(
                    "Hộp bao hình học gia công theo cao độ Z không hợp lệ."
                )
            x_min, y_min, z_min, x_max, y_max, z_max = coordinates
            for x in (x_min, x_max):
                for y in (y_min, y_max):
                    for z in (z_min, z_max):
                        dx, dy, dz = x - origin.x, y - origin.y, z - origin.z
                        projected.append(
                            (
                                dx * frame.x_axis.x
                                + dy * frame.x_axis.y
                                + dz * frame.x_axis.z,
                                dx * frame.y_axis.x
                                + dy * frame.y_axis.y
                                + dz * frame.y_axis.z,
                                dx * frame.z_axis.x
                                + dy * frame.z_axis.y
                                + dz * frame.z_axis.z,
                            )
                        )
        if not projected:
            raise ValueError("Hộp bao hình học gia công theo cao độ Z trống.")
        return ZLevelGeometryEvidence(
            float(min(item[0] for item in projected)),
            float(max(item[0] for item in projected)),
            float(min(item[1] for item in projected)),
            float(max(item[1] for item in projected)),
            float(min(item[2] for item in projected)),
            float(max(item[2] for item in projected)),
            "Hộp bao các bề mặt đã chọn trong viewport",
        )

    @staticmethod
    def _union_z_level_evidence(
        current: ZLevelGeometryEvidence | None,
        added: ZLevelGeometryEvidence,
    ) -> ZLevelGeometryEvidence:
        if current is None:
            return added
        return ZLevelGeometryEvidence(
            min(current.u_min, added.u_min),
            max(current.u_max, added.u_max),
            min(current.v_min, added.v_min),
            max(current.v_max, added.v_max),
            min(current.w_min, added.w_min),
            max(current.w_max, added.w_max),
            "Hộp bao hợp nhất của các bề mặt đã chọn",
        )

    @staticmethod
    def _z_level_geometry_presentation(
        context: ZLevelEditorContext,
        surfaces: tuple[object, ...],
    ) -> dict[str, PresentationValue]:
        count = len(surfaces)
        return {
            "geometry_summary": (
                f"{count} bề mặt gia công · "
                f"{'BẢN NHÁP' if count else 'CHƯA CHỌN'}"
            ),
            "selected_face_count": str(count),
            "geometry_reference_summary": (
                ", ".join(
                    str(item.geometry.reference_id)[:8] for item in surfaces
                )
                if surfaces
                else "Không có"
            ),
            "selected_body_setup_summary": (
                f"Thiết lập {context.setup.name} · "
                f"{context.setup.work_offset.name}"
            ),
        }

    def _preview_z_level_production(
        self,
        context: ZLevelEditorContext,
        draft: ZLevelEditorDraftContext,
        request: FunctionEditorPreviewRequest,
    ) -> str:
        if not self._z_level_context_is_current(context):
            raise ValueError(
                "Bản xem trước gia công theo cao độ Z đã lỗi thời."
            )
        update = prepare_z_level_update(context, draft, dict(request.values))
        height = update.parameters.top_level - update.parameters.bottom_level
        count = int(math.ceil(height / update.parameters.stepdown_mm)) + 1
        return (
            f"Ứng viên · {len(update.zone.part_surfaces.selection.surfaces)} mặt · "
            f"{count} lớp Z · bước xuống {update.parameters.stepdown_mm:g} mm · "
            "chưa tính và chưa SAFE"
        )

    def _apply_z_level_production(
        self,
        job_id: CamJobId,
        setup_id: SetupId,
        node_id: CamNodeId,
        context: ZLevelEditorContext,
        draft: ZLevelEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> object:
        if not self._z_level_context_is_current(context):
            raise ValueError(
                "Nguyên công gia công theo cao độ Z đã thay đổi; "
                "hãy mở lại trình chỉnh sửa."
            )
        update = prepare_z_level_update(context, draft, values)
        old_config = self._service.cam3d_config
        zones = tuple(
            item for item in old_config.zones if item.zone_id != update.zone.zone_id
        ) + (update.zone,)
        new_config = Cam3DProjectConfig(old_config.project_id, zones)
        try:
            self._service.stage_cam3d_config(new_config)
            changed = self._service.execute_cam_command(
                lambda app: app.update_tree(
                    job_id,
                    setup_id,
                    lambda tree: tree.replace_operation(
                        update.operation
                    ).rename_node(node_id, update.operation_name),
                ),
                expected_generation=self._generation,
            )
        except (KeyError, RuntimeError, TypeError, ValueError):
            self._service.stage_cam3d_config(old_config)
            raise
        self._z_level_drafts.pop(context.operation.operation_id, None)
        self._z_level_draft_evidence.pop(context.operation.operation_id, None)
        self._z_level_reports.pop(context.operation.operation_id, None)
        self.refresh(("operation", str(node_id)))
        self.projection_changed.emit()
        self.message.emit(
            ui_text(
                "Đã áp dụng bản nháp Gia công tinh theo cao độ Z; chưa tính toolpath."
            )
        )
        return changed

    def _calculate_z_level_production(
        self,
        context: ZLevelEditorContext,
        draft: ZLevelEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> str:
        if self._z_level_task is not None or self._parallel_task is not None:
            raise ValueError("Một tác vụ tính CAM 3D đang chạy.")
        if not self._z_level_context_is_current(context):
            raise ValueError(
                "Trạng thái gia công theo cao độ Z đã áp dụng bị lỗi thời."
            )
        applied = z_level_applied_values(context)
        if any(values.get(key) != value for key, value in applied.items()):
            raise ValueError(
                "Tính đường chạy dao chỉ dùng trạng thái đã áp dụng; hãy Áp dụng trước."
            )
        update = prepare_z_level_update(context, draft, applied)
        if update.operation != context.operation or update.zone != context.zone:
            raise ValueError(
                "Tính đường chạy dao chỉ dùng trạng thái đã áp dụng; hãy Áp dụng trước."
            )
        if self._parallel_adapter_provider is None:
            raise ValueError("Bộ điều hợp tính toán OCP CAM 3D chưa sẵn sàng.")
        adapter = self._parallel_adapter_provider()
        generation = self._generation
        project = self._service.current_project
        if generation is None or project is None:
            raise ValueError(
                "Ngữ cảnh dự án gia công theo cao độ Z không còn hiện hành."
            )
        request = Cam3DCalculationRequest.create(
            project_id=project.manifest.project_id,
            project_generation=generation,
            job_id=context.job_id,
            setup_id=context.operation.setup_id,
            zone=update.zone,
            tool_assembly_fingerprint=ContentFingerprint.from_payload(
                update.assembly.to_dict()
            ),
            tool_definition_fingerprint=update.tool.content_fingerprint,
            safe_motion_policy=update.safe_motion_policy,
            algorithm="hms_z_level_implicit_ball_center",
            algorithm_version=Z_LEVEL_FINISHING_ALGORITHM_VERSION,
        )

        def calculate(cancelled, progress):
            contact_resolver = adapter.contact_resolver(
                update.zone.part_surfaces.selection.surfaces
            )
            geometry = Cam3DGeometryService()
            geometry.bind_project(project.manifest.project_id, generation)
            execution = geometry.calculate(
                request,
                adapter,
                cancellation=cancelled,
            )
            if not execution.published or execution.context is None:
                message = (
                    execution.diagnostics[0].message
                    if execution.diagnostics
                    else "Chuẩn bị hình học gia công theo cao độ Z thất bại."
                )
                raise RuntimeError(message)
            return calculate_and_publish_z_level_finishing(
                project.root_path,
                context.operation,
                execution.context,
                assembly=update.assembly,
                tool=update.tool,
                holder=update.holder,
                cancellation=cancelled,
                progress=progress,
                contact_resolver=contact_resolver,
                computing_callback=lambda operation: self._service.begin_z_level_calculation(
                    operation, expected_generation=generation
                ),
                current_operation=lambda: self._current_z_level_operation(
                    context.operation.operation_id
                ),
            )

        task = ZLevelFinishingTask(calculate)
        self._z_level_task = task
        self._z_level_task_generation = generation
        task.signals.progress.connect(self._z_level_progress)
        task.signals.completed.connect(self._z_level_completed)
        task.signals.failed.connect(self._z_level_failed)
        task.signals.finished.connect(self._z_level_finished)
        self.parallel_calculation_active.emit(True)
        QThreadPool.globalInstance().start(task)
        return "Đã bắt đầu tính đường chạy dao theo cao độ Z"

    def _current_z_level_operation(self, operation_id: OperationId) -> Operation:
        current = self._current_facing_operation(operation_id)
        if current is None:
            raise RuntimeError(
                "Nguyên công gia công theo cao độ Z không còn hiện hành."
            )
        return current

    def _z_level_progress(self, progress: ZLevelProgress) -> None:
        if self._z_level_task is not None:
            self.parallel_progress_changed.emit(progress)

    def _z_level_completed(self, result: object) -> None:
        if not isinstance(result, ZLevelFinishingComputeResult):
            self._error(
                "Tác vụ gia công theo cao độ Z trả về kết quả không hợp lệ."
            )
            return
        generation = self._z_level_task_generation
        if generation is None:
            return
        try:
            accepted = self._service.commit_z_level_calculation(
                result, expected_generation=generation
            )
        except (ProjectError, RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if not accepted:
            self._error(
                "Kết quả gia công theo cao độ Z đã lỗi thời và bị loại bỏ."
            )
            return
        if result.safety_report is not None:
            self._z_level_reports[
                result.operation.operation_id
            ] = result.safety_report
        self.refresh(self._selected_key)
        self.projection_changed.emit()
        if result.accepted:
            self.message.emit(
                "Gia công theo cao độ Z SẴN SÀNG · AN TOÀN trong phạm vi "
                "đã khai báo; chưa xác minh khả năng sẵn sàng chạy máy."
            )
        else:
            self._error(
                result.diagnostics[0].message
                if result.diagnostics
                else (
                    "Gia công theo cao độ Z không công bố kết quả tính toán "
                    "SẴN SÀNG."
                )
            )

    def _z_level_failed(self, error: object) -> None:
        self._error(
            str(error)
            or "Tính toán gia công theo cao độ Z thất bại an toàn."
        )

    def _z_level_finished(self) -> None:
        self._z_level_task = None
        self._z_level_task_generation = None
        self.parallel_calculation_active.emit(False)

    def _show_z_level_safety_details(
        self, context: ZLevelEditorContext
    ) -> None:
        from hms_cadcam.ui.function_editor.zlevel_widgets import (
            ZLevelSafetyDiagnosticsDialog,
        )

        dialog = ZLevelSafetyDiagnosticsDialog(
            context.safety_report,
            context.operation.diagnostics,
            self,
        )
        if self._child_dialog_opener is not None:
            self._child_dialog_opener("z_level_diagnostics", dialog, None)
        else:
            dialog.open()

    def _parallel_production_session(
        self,
        job_id: CamJobId,
        setup: Setup,
        node_id: CamNodeId,
        operation_name: str,
        operation: Operation,
    ) -> FunctionEditorProductionSession | None:
        """Build one native-free Parallel session over applied project state."""
        if self._generation is None:
            return None
        project = self._service.current_project
        if project is None:
            return None
        parameters = ParallelFinishingParameters.from_operation_parameters(
            operation.parameters
        )
        zone = next(
            (
                item
                for item in self._service.cam3d_config.zones
                if item.zone_id == parameters.zone_id
            ),
            None,
        )
        surfaces = (
            zone.part_surfaces.selection.surfaces if zone is not None else ()
        )
        draft_surfaces = self._parallel_drafts.get(operation.operation_id, surfaces)
        snapshot = self._service.cam_snapshot
        artifact = self._service.load_toolpath_artifact(operation.operation_id)
        context = ParallelEditorContext(
            operation_name,
            operation,
            setup,
            job_id,
            project.manifest.project_id,
            zone,
            snapshot.tool_assemblies,
            snapshot.tool_definitions,
            snapshot.holder_definitions,
            snapshot.machine_definitions,
            artifact,
            self._parallel_reports.get(operation.operation_id),  # type: ignore[arg-type]
            geometry_resolved=(
                zone is not None
                and bool(surfaces)
                and {item.geometry.reference_id for item in surfaces}
                == {
                    item.reference.reference_id
                    for item in operation.geometry_inputs
                    if item.role is GeometryInputRole.DRIVE_GEOMETRY
                }
            ),
            geometry_diagnostic=(
                "Machining zone missing"
                if zone is None
                else "Selected face references are stale"
                if not surfaces
                else ""
            ),
            geometry_evidence=self._parallel_draft_evidence.get(
                operation.operation_id
            ),
        )
        draft = ParallelEditorDraftContext(
            tuple(draft_surfaces),  # type: ignore[arg-type]
            geometry_evidence=self._parallel_draft_evidence.get(
                operation.operation_id
            ),
        )
        schema = build_parallel_schema(context)
        applied = parallel_applied_values(context)
        return FunctionEditorProductionSession(
            selection_key=("operation", str(node_id)),
            schema=schema,
            applied_values=tuple(
                (field.field_id, applied[field.field_id]) for field in schema.fields
            ),
            project_key=str(project.manifest.project_id),
            operation_key=str(operation.operation_id),
            generation=self._generation,
            apply_callback=lambda values: self._apply_parallel_production(
                job_id, setup.setup_id, node_id, context, draft, values
            ),
            validation_callback=lambda values: self._validate_parallel_production(
                schema, context, draft, values
            ),
            preview_callback=lambda request: self._preview_parallel_production(
                context, draft, request
            ),
            calculate_callback=lambda values: self._calculate_parallel_production(
                context, draft, values
            ),
            draft_transform_callback=lambda values: parallel_draft_derived_values(
                context, draft, values
            ),
            field_action_callback=lambda action_id, values: self._parallel_field_action(
                context, draft, action_id, values
            ),
            tool_profile_interaction_callback=lambda values, changed: (
                self._tool_profile_save_interaction(
                    "parallel_finishing_3d",
                    operation.operation_id,
                    values,
                    changed,
                )
            ),
        )

    def _parallel_context_is_current(self, context: ParallelEditorContext) -> bool:
        current = self._current_facing_operation(context.operation.operation_id)
        if current is None or self._generation != self._service.cam_generation:
            return False
        return (
            current.revision == context.operation.revision
            and current.parameters == context.operation.parameters
            and current.geometry_inputs == context.operation.geometry_inputs
            and current.tool_assembly == context.operation.tool_assembly
            and current.machine_requirement == context.operation.machine_requirement
            and current.enabled == context.operation.enabled
        )

    def _validate_parallel_production(
        self,
        schema,
        context: ParallelEditorContext,
        draft: ParallelEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> tuple[FunctionEditorDiagnostic, ...]:
        if not self._parallel_context_is_current(context):
            return (
                FunctionEditorDiagnostic(
                    "parallel.stale-editor",
                    "Parallel operation changed; reopen the editor.",
                    FunctionEditorDiagnosticSeverity.ERROR,
                    "operation_name",
                    "operation",
                ),
            )
        return parallel_validation_diagnostics(schema, context, draft, values)

    def _parallel_field_action(
        self,
        context: ParallelEditorContext,
        draft: ParallelEditorDraftContext,
        action_id: str,
        _values: Mapping[str, PresentationValue],
    ) -> Mapping[str, PresentationValue] | None:
        if action_id == "select_parallel_faces":
            if self._parallel_surface_provider is None:
                raise ValueError("Parallel FACE selection adapter is unavailable.")
            selected = self._parallel_surface_provider()
            if not selected:
                raise ValueError("Select one or more BRep faces in the viewport.")
            merged = {_parallel_surface_key(item): item for item in draft.surfaces}
            for item in selected:
                if not hasattr(item, "geometry"):
                    raise TypeError("Parallel selector returned an invalid face reference")
                key = _parallel_surface_key(item)
                # Preserve the already-applied stable GeometryReference identity
                # when the same persistent BRep face is selected again.
                merged.setdefault(key, item)
            draft.surfaces = tuple(
                sorted(merged.values(), key=lambda item: item.fingerprint.digest)
            )
            if self._parallel_geometry_bounds_provider is not None:
                bounds = self._parallel_geometry_bounds_provider()
                if len(bounds) != len(selected):
                    raise ValueError("Parallel geometry bounds are stale; select faces again.")
                evidence = self._parallel_evidence_from_bounds(context, bounds)
                draft.geometry_evidence = self._union_parallel_evidence(
                    draft.geometry_evidence, evidence
                )
                self._parallel_draft_evidence[
                    context.operation.operation_id
                ] = draft.geometry_evidence
            self._parallel_drafts[context.operation.operation_id] = draft.surfaces
            return self._parallel_geometry_presentation(context, draft.surfaces)
        if action_id == "clear_parallel_faces":
            draft.surfaces = ()
            draft.geometry_evidence = None
            self._parallel_draft_evidence.pop(context.operation.operation_id, None)
            self._parallel_drafts[context.operation.operation_id] = ()
            return self._parallel_geometry_presentation(context, ())
        if action_id == "reselect_parallel_faces":
            draft.surfaces = ()
            draft.geometry_evidence = None
            self._parallel_draft_evidence.pop(context.operation.operation_id, None)
            return self._parallel_field_action(
                context, draft, "select_parallel_faces", _values
            )
        if action_id == "remove_parallel_faces":
            if self._parallel_surface_provider is None:
                raise ValueError("Parallel FACE selection adapter is unavailable.")
            selected = self._parallel_surface_provider()
            removed = {_parallel_surface_key(item) for item in selected}
            draft.surfaces = tuple(
                item
                for item in draft.surfaces
                if _parallel_surface_key(item) not in removed
            )
            draft.geometry_evidence = None
            self._parallel_draft_evidence.pop(context.operation.operation_id, None)
            self._parallel_drafts[context.operation.operation_id] = draft.surfaces
            return self._parallel_geometry_presentation(context, draft.surfaces)
        if action_id == "open_parallel_safety_details":
            self._show_parallel_safety_details(context)
            return None
        if action_id == "open_parallel_simulation":
            artifact = context.artifact
            from hms_cadcam.cam.cam3d.parallel import parallel_artifact_has_safe_contract

            if (
                artifact is None
                or context.operation.artifact_state.status is not ArtifactStatus.VALID
                or not parallel_artifact_has_safe_contract(artifact)
            ):
                raise ValueError(
                    "Simulation requires a current READY + SAFE algorithm v3 artifact."
                )
            self._run_simulation()
            return None
        raise ValueError(f"Unsupported Parallel field action: {action_id}")

    @staticmethod
    def _parallel_evidence_from_bounds(
        context: ParallelEditorContext,
        bounds: tuple[object, ...],
    ) -> ParallelGeometryEvidence:
        frame = getattr(context.setup, "wcs")
        origin = frame.origin
        projected: list[tuple[float, float]] = []
        for value in bounds:
            coordinates = (
                getattr(value, "x_min"),
                getattr(value, "y_min"),
                getattr(value, "z_min"),
                getattr(value, "x_max"),
                getattr(value, "y_max"),
                getattr(value, "z_max"),
            )
            if not all(isinstance(item, (int, float)) for item in coordinates):
                raise TypeError("Parallel geometry bounds are invalid")
            x_min, y_min, z_min, x_max, y_max, z_max = coordinates
            for x in (x_min, x_max):
                for y in (y_min, y_max):
                    for z in (z_min, z_max):
                        dx, dy, dz = x - origin.x, y - origin.y, z - origin.z
                        projected.append(
                            (
                                dx * frame.x_axis.x
                                + dy * frame.x_axis.y
                                + dz * frame.x_axis.z,
                                dx * frame.y_axis.x
                                + dy * frame.y_axis.y
                                + dz * frame.y_axis.z,
                            )
                        )
        if not projected:
            raise ValueError("Parallel geometry bounds are empty")
        return ParallelGeometryEvidence(
            float(min(item[0] for item in projected)),
            float(max(item[0] for item in projected)),
            float(min(item[1] for item in projected)),
            float(max(item[1] for item in projected)),
            "Hộp bao các bề mặt được chọn trong viewport",
        )

    @staticmethod
    def _union_parallel_evidence(
        current: ParallelGeometryEvidence | None,
        added: ParallelGeometryEvidence,
    ) -> ParallelGeometryEvidence:
        if current is None:
            return added
        return ParallelGeometryEvidence(
            min(current.u_min, added.u_min),
            max(current.u_max, added.u_max),
            min(current.v_min, added.v_min),
            max(current.v_max, added.v_max),
            "Hộp bao hợp nhất của các bề mặt được chọn",
        )

    @staticmethod
    def _parallel_geometry_presentation(
        context: ParallelEditorContext,
        surfaces: tuple[object, ...],
    ) -> dict[str, PresentationValue]:
        count = len(surfaces)
        return {
            "geometry_summary": f"{count} machining face(s) · {'DRAFT' if count else 'MISSING'}",
            "selected_face_count": str(count),
            "geometry_reference_summary": (
                ", ".join(str(item.geometry.reference_id)[:8] for item in surfaces)
                if surfaces
                else "none"
            ),
            "selected_body_setup_summary": (
                f"Setup {getattr(context.setup, 'name', '')} · "
                f"{getattr(context.setup, 'work_offset').name}"
            ),
        }

    def _preview_parallel_production(
        self,
        context: ParallelEditorContext,
        draft: ParallelEditorDraftContext,
        request: FunctionEditorPreviewRequest,
    ) -> str:
        if not self._parallel_context_is_current(context):
            raise ValueError("Parallel preview is stale.")
        values = dict(request.values)
        update = prepare_parallel_update(context, draft, values)
        return (
            f"Candidate preview · {len(update.zone.part_surfaces.selection.surfaces)} face(s) · "
            f"angle {update.parameters.direction_angle_degrees:g}° · "
            f"stepover {update.parameters.stepover_mm:g} mm · "
            "not calculated and not SAFE"
        )

    def _apply_parallel_production(
        self,
        job_id: CamJobId,
        setup_id: SetupId,
        node_id: CamNodeId,
        context: ParallelEditorContext,
        draft: ParallelEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> object:
        if not self._parallel_context_is_current(context):
            raise ValueError("Parallel operation changed; reopen the editor.")
        update = prepare_parallel_update(context, draft, values)
        old_config = self._service.cam3d_config
        zones = tuple(
            item for item in old_config.zones if item.zone_id != update.zone.zone_id
        ) + (update.zone,)
        new_config = Cam3DProjectConfig(old_config.project_id, zones)
        try:
            self._service.stage_cam3d_config(new_config)
            changed = self._service.execute_cam_command(
                lambda app: app.update_tree(
                    job_id,
                    setup_id,
                    lambda tree: tree.replace_operation(update.operation).rename_node(
                        node_id, update.operation_name
                    ),
                ),
                expected_generation=self._generation,
            )
        except (KeyError, RuntimeError, TypeError, ValueError):
            self._service.stage_cam3d_config(old_config)
            raise
        self._parallel_drafts.pop(context.operation.operation_id, None)
        self._parallel_draft_evidence.pop(context.operation.operation_id, None)
        self._parallel_reports.pop(context.operation.operation_id, None)
        self.refresh(("operation", str(node_id)))
        self.projection_changed.emit()
        self.message.emit(
            "Bản nháp Gia công tinh song song đã áp dụng; chưa tự tính toán."
        )
        return changed

    def _calculate_parallel_production(
        self,
        context: ParallelEditorContext,
        draft: ParallelEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> str:
        if self._parallel_task is not None:
            raise ValueError("A Parallel calculation is already running.")
        if not self._parallel_context_is_current(context):
            raise ValueError("Parallel applied state is stale.")
        applied = parallel_applied_values(context)
        if any(values.get(key) != value for key, value in applied.items()):
            raise ValueError("Calculate uses applied state only; Apply the draft first.")
        update = prepare_parallel_update(context, draft, applied)
        if update.operation != context.operation or update.zone != context.zone:
            raise ValueError("Calculate uses applied state only; Apply the draft first.")
        if self._parallel_adapter_provider is None:
            raise ValueError("Parallel OCP calculation adapter is unavailable.")
        adapter = self._parallel_adapter_provider()
        generation = self._generation
        project = self._service.current_project
        if generation is None or project is None:
            raise ValueError("Parallel project context is unavailable.")
        request = Cam3DCalculationRequest.create(
            project_id=project.manifest.project_id,
            project_generation=generation,
            job_id=context.job_id,
            setup_id=context.operation.setup_id,
            zone=update.zone,
            tool_assembly_fingerprint=ContentFingerprint.from_payload(
                update.assembly.to_dict()
            ),
            tool_definition_fingerprint=update.tool.content_fingerprint,
            safe_motion_policy=update.safe_motion_policy,
            algorithm="hms_parallel_finishing_mesh_plane",
            algorithm_version=PARALLEL_FINISHING_ALGORITHM_VERSION,
        )

        def calculate(cancelled, progress):
            contact_resolver = adapter.contact_resolver(
                update.zone.part_surfaces.selection.surfaces
            )
            geometry = Cam3DGeometryService()
            geometry.bind_project(project.manifest.project_id, generation)
            execution = geometry.calculate(
                request,
                adapter,
                cancellation=cancelled,
            )
            if not execution.published or execution.context is None:
                message = (
                    execution.diagnostics[0].message
                    if execution.diagnostics
                    else "Parallel geometry preparation failed."
                )
                raise RuntimeError(message)
            return calculate_and_publish_parallel_finishing(
                project.root_path,
                context.operation,
                execution.context,
                assembly=update.assembly,
                tool=update.tool,
                holder=update.holder,
                cancellation=cancelled,
                progress=progress,
                contact_resolver=contact_resolver,
                computing_callback=lambda operation: self._service.begin_parallel_calculation(
                    operation, expected_generation=generation
                ),
                current_operation=lambda: self._current_parallel_operation(
                    context.operation.operation_id
                ),
            )

        task = ParallelFinishingTask(calculate)
        self._parallel_task = task
        self._parallel_task_generation = generation
        task.signals.progress.connect(self._parallel_progress)
        task.signals.completed.connect(self._parallel_completed)
        task.signals.failed.connect(self._parallel_failed)
        task.signals.finished.connect(self._parallel_finished)
        self.parallel_calculation_active.emit(True)
        QThreadPool.globalInstance().start(task)
        return "Parallel calculation started"

    def _current_parallel_operation(self, operation_id: OperationId) -> Operation:
        current = self._current_facing_operation(operation_id)
        if current is None:
            raise RuntimeError("Parallel operation is no longer current")
        return current

    def _parallel_progress(self, progress: ParallelProgress) -> None:
        if self._parallel_task is None:
            return
        self.parallel_progress_changed.emit(progress)

    def _parallel_completed(self, result: object) -> None:
        if not isinstance(result, ParallelFinishingComputeResult):
            self._error("Tác vụ Gia công tinh song song trả về kết quả không hợp lệ.")
            return
        generation = self._parallel_task_generation
        if generation is None:
            return
        try:
            accepted = self._service.commit_parallel_calculation(
                result, expected_generation=generation
            )
        except (ProjectError, RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if not accepted:
            self._error("Kết quả Gia công tinh song song đã lỗi thời và bị loại bỏ.")
            return
        if result.safety_report is not None:
            self._parallel_reports[result.operation.operation_id] = result.safety_report
        self.refresh(self._selected_key)
        self.projection_changed.emit()
        if result.accepted:
            self.message.emit(
                "Parallel Finishing READY · safety verified within declared scope."
            )
        else:
            self._error(
                result.diagnostics[0].message
                if result.diagnostics
                else "Parallel calculation did not publish a READY artifact."
            )

    def _parallel_failed(self, error: object) -> None:
        self._error(str(error) or "Parallel calculation failed safely.")

    def _parallel_finished(self) -> None:
        self._parallel_task = None
        self._parallel_task_generation = None
        self.parallel_calculation_active.emit(False)

    def cancel_parallel_calculation(self) -> None:
        """Cancel the active CAM 3D finishing task without partial publication."""
        if self._parallel_task is not None:
            self._parallel_task.cancel()
            self.message.emit("Đang hủy tính toán Gia công tinh song song…")
        if self._z_level_task is not None:
            self._z_level_task.cancel()
            self.message.emit(
                "Đang hủy tính toán Gia công tinh theo cao độ Z…"
            )

    def _show_parallel_safety_details(self, context: ParallelEditorContext) -> None:
        from hms_cadcam.ui.function_editor.parallel_widgets import (
            ParallelSafetyDiagnosticsDialog,
        )

        dialog = ParallelSafetyDiagnosticsDialog(
            context.safety_report,
            context.operation.diagnostics,
            self,
        )
        if self._child_dialog_opener is not None:
            self._child_dialog_opener("parallel_diagnostics", dialog, None)
        else:
            dialog.open()

    def _contour_production_session(
        self,
        job_id: CamJobId,
        setup: Setup,
        node_id: CamNodeId,
        operation_name: str,
        operation: Operation,
    ) -> FunctionEditorProductionSession | None:
        """Build one typed Contour session without touching project state."""
        if self._generation is None:
            return None
        reference = (
            operation.geometry_inputs[0].reference
            if len(operation.geometry_inputs) == 1
            else None
        )
        resolved = None
        if reference is not None and self._profile_resolver is not None:
            resolved = self._profile_resolver(reference)
        geometry_resolved = (
            resolved is not None
            and resolved.status is GeometryResolutionStatus.RESOLVED
            and resolved.profile is not None
        )
        segment_count = (
            len(resolved.profile.outer_loop.segments)
            if geometry_resolved and resolved is not None and resolved.profile is not None
            else None
        )
        orientation = (
            resolved.profile.outer_loop.orientation.value
            if geometry_resolved and resolved is not None and resolved.profile is not None
            else ""
        )
        snapshot = self._service.cam_snapshot
        context = ContourEditorContext(
            operation_name,
            operation,
            setup,
            snapshot.tool_assemblies,
            snapshot.tool_definitions,
            snapshot.holder_definitions,
            snapshot.machine_definitions,
            reference,
            geometry_resolved,
            segment_count,
            orientation,
        )
        draft = ContourEditorDraftContext(reference)
        schema = build_contour_schema(context)
        applied = contour_applied_values(context)
        project = self._service.current_project
        if project is None:
            return None
        return FunctionEditorProductionSession(
            selection_key=("operation", str(node_id)),
            schema=schema,
            applied_values=tuple(
                (field.field_id, applied[field.field_id]) for field in schema.fields
            ),
            project_key=str(project.manifest.project_id),
            operation_key=str(operation.operation_id),
            generation=self._generation,
            apply_callback=lambda values: self._apply_contour_production(
                job_id, setup.setup_id, node_id, context, draft, values
            ),
            validation_callback=lambda values: self._validate_contour_production(
                schema, context, draft, values
            ),
            preview_callback=lambda request: self._preview_contour_production(
                schema, context, draft, request
            ),
            calculate_callback=lambda values: self._calculate_contour_production(
                context, values
            ),
            field_action_callback=lambda action_id, values: self._contour_field_action(
                context, draft, action_id, values
            ),
        )

    def _pocket_production_session(
        self,
        job_id: CamJobId,
        setup: Setup,
        node_id: CamNodeId,
        operation_name: str,
        operation: Operation,
    ) -> FunctionEditorProductionSession | None:
        """Build one typed Pocket session without touching project state."""
        if self._generation is None:
            return None
        reference = (
            operation.geometry_inputs[0].reference
            if len(operation.geometry_inputs) == 1
            else None
        )
        resolved_pocket = None
        if reference is not None and self._pocket_resolver is not None:
            resolved_pocket = self._pocket_resolver(reference)
        geometry_resolved = (
            resolved_pocket is not None
            and resolved_pocket.status is GeometryResolutionStatus.RESOLVED
            and resolved_pocket.region is not None
        )
        segment_count = (
            len(resolved_pocket.region.boundary.outer_loop.segments)
            if geometry_resolved
            and resolved_pocket is not None
            and resolved_pocket.region is not None
            else None
        )
        orientation = (
            resolved_pocket.region.boundary.outer_loop.orientation.value
            if geometry_resolved
            and resolved_pocket is not None
            and resolved_pocket.region is not None
            else ""
        )
        island_count: int | None = 0 if geometry_resolved else None
        diagnostic = ""
        if reference is not None and self._profile_resolver is not None:
            profile_result = self._profile_resolver(reference)
            profile = getattr(profile_result, "profile", None)
            if (
                profile_result.status is GeometryResolutionStatus.RESOLVED
                and profile is not None
            ):
                island_count = len(profile.inner_loops)
                if segment_count is None:
                    segment_count = len(profile.outer_loop.segments)
                    orientation = profile.outer_loop.orientation.value
                if island_count:
                    diagnostic = "island unsupported v1"
        if (
            not geometry_resolved
            and resolved_pocket is not None
            and resolved_pocket.diagnostics
        ):
            diagnostic = resolved_pocket.diagnostics[0].message
        if len(operation.geometry_inputs) > 1:
            diagnostic = "duplicate/additional geometry input"
        snapshot = self._service.cam_snapshot
        context = PocketEditorContext(
            operation_name,
            operation,
            setup,
            snapshot.tool_assemblies,
            snapshot.tool_definitions,
            snapshot.holder_definitions,
            snapshot.machine_definitions,
            reference,
            geometry_resolved,
            segment_count,
            orientation,
            island_count,
            diagnostic,
        )
        draft = PocketEditorDraftContext(reference)
        schema = build_pocket_schema(context)
        applied = pocket_applied_values(context)
        project = self._service.current_project
        if project is None:
            return None
        return FunctionEditorProductionSession(
            selection_key=("operation", str(node_id)),
            schema=schema,
            applied_values=tuple(
                (field.field_id, applied[field.field_id]) for field in schema.fields
            ),
            project_key=str(project.manifest.project_id),
            operation_key=str(operation.operation_id),
            generation=self._generation,
            apply_callback=lambda values: self._apply_pocket_production(
                job_id, setup.setup_id, node_id, context, draft, values
            ),
            validation_callback=lambda values: self._validate_pocket_production(
                schema, context, draft, values
            ),
            preview_callback=lambda request: self._preview_pocket_production(
                schema, context, draft, request
            ),
            calculate_callback=lambda values: self._calculate_pocket_production(
                context, values
            ),
            field_action_callback=lambda action_id, values: self._pocket_field_action(
                context, draft, action_id, values
            ),
        )

    def _drilling_family_production_session(
        self,
        job_id: CamJobId,
        setup: Setup,
        node_id: CamNodeId,
        operation_name: str,
        operation: Operation,
        kind: DrillingFamilyEditorKind,
    ) -> FunctionEditorProductionSession | None:
        """Build one shared drilling-family production session without mutation."""
        if self._generation is None:
            return None
        strategy = _hole_strategy(operation)
        resolved = None
        diagnostic = ""
        if self._drilling_resolver is not None:
            try:
                resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
            except (RuntimeError, TypeError, ValueError) as error:
                diagnostic = str(error)
        geometry_resolved = (
            resolved is not None
            and resolved.status is GeometryResolutionStatus.RESOLVED
            and resolved.region is not None
        )
        if not geometry_resolved and resolved is not None and resolved.diagnostics:
            diagnostic = resolved.diagnostics[0].message
        if self._drilling_resolver is None:
            diagnostic = "Hole geometry resolver chưa sẵn sàng"
        snapshot = self._service.cam_snapshot
        context = DrillingFamilyEditorContext(
            kind,
            operation_name,
            operation,
            setup,
            snapshot.tool_assemblies,
            snapshot.tool_definitions,
            snapshot.holder_definitions,
            snapshot.machine_definitions,
            strategy.geometry.source,
            geometry_resolved,
            diagnostic,
        )
        draft = DrillingFamilyEditorDraftContext(context.hole_source)
        builder = {
            DrillingFamilyEditorKind.DRILLING: build_drilling_schema,
            DrillingFamilyEditorKind.TAPPING: build_tapping_schema,
            DrillingFamilyEditorKind.REAMING: build_reaming_schema,
            DrillingFamilyEditorKind.BORING: build_boring_schema,
        }[kind]
        schema = builder(context)
        applied = drilling_family_applied_values(context)
        project = self._service.current_project
        if project is None:
            return None
        return FunctionEditorProductionSession(
            selection_key=("operation", str(node_id)),
            schema=schema,
            applied_values=tuple(
                (field.field_id, applied[field.field_id]) for field in schema.fields
            ),
            project_key=str(project.manifest.project_id),
            operation_key=str(operation.operation_id),
            generation=self._generation,
            apply_callback=lambda values: self._apply_drilling_family_production(
                job_id, setup.setup_id, node_id, context, draft, values
            ),
            validation_callback=lambda values: self._validate_drilling_family_production(
                schema, context, draft, values
            ),
            preview_callback=lambda request: self._preview_drilling_family_production(
                schema, context, draft, request
            ),
            calculate_callback=lambda values: self._calculate_drilling_family_production(
                context, values
            ),
            field_action_callback=lambda action_id, values: self._drilling_family_field_action(
                context, draft, action_id, values
            ),
            tool_profile_interaction_callback=(
                (
                    lambda values, changed: self._tool_profile_save_interaction(
                        "drilling_v1",
                        operation.operation_id,
                        values,
                        changed,
                    )
                )
                if kind is DrillingFamilyEditorKind.DRILLING
                else None
            ),
        )

    def _tool_profile_save_interaction(
        self,
        strategy_id: str,
        operation_id: OperationId,
        values: Mapping[str, PresentationValue],
        changed_fields: frozenset[str],
    ) -> ToolProfileSaveInteraction:
        """Build preview/confirm callbacks without mutating the current draft."""
        if self._generation is None:
            raise RuntimeError("Dự án hiện tại không còn hoạt động.")
        operation = self._current_facing_operation(operation_id)
        if operation is None:
            raise RuntimeError("Nguyên công hiện tại không còn tồn tại.")
        snapshot = self._service.cam_snapshot
        assembly_text = str(
            values.get(
                "tool_assembly_id", operation.tool_assembly.assembly_id
            )
        )
        assembly = next(
            (
                item
                for item in snapshot.tool_assemblies
                if str(item.assembly_id) == assembly_text
            ),
            None,
        )
        if assembly is None:
            raise ValueError("Cụm Tool đã chọn không còn tồn tại.")
        tool = next(
            (
                item
                for item in snapshot.tool_definitions
                if item.tool_id == assembly.tool_id
            ),
            None,
        )
        if tool is None:
            raise ValueError("Tool đã chọn không còn tồn tại.")
        holder = next(
            (
                item
                for item in snapshot.holder_definitions
                if assembly.holder_id is not None
                and item.holder_id == assembly.holder_id
            ),
            None,
        )
        schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy_id)
        overridden: set[str] = set()
        for descriptor in schema.fields:
            if (
                descriptor.override_flag_id is not None
                and bool(values.get(descriptor.override_flag_id, False))
            ):
                overridden.add(descriptor.field_id)
            if (
                descriptor.field_id in changed_fields
                or descriptor.operation_field_id in changed_fields
            ):
                overridden.add(descriptor.field_id)
            if (
                strategy_id == "drilling_v1"
                and descriptor.operation_field_id in values
            ):
                overridden.add(descriptor.field_id)

        def make_preview(mode: ToolProfileSaveMode):
            return preview_tool_profile_capture(
                tool,
                strategy_id,
                schema.display_name_vi,
                values,
                overridden_field_ids=frozenset(overridden),
                mode=mode,
                source_unit=(
                    assembly.unit
                    if strategy_id == "drilling_v1"
                    else None
                ),
                registry=DEFAULT_TOOL_PROFILE_REGISTRY,
            )

        preview = make_preview(ToolProfileSaveMode.OVERRIDES_ONLY)

        def confirm(mode: ToolProfileSaveMode) -> object:
            if not isinstance(mode, ToolProfileSaveMode):
                raise TypeError("Lựa chọn lưu cấu hình Tool không hợp lệ.")
            confirmed = make_preview(mode)
            return self._service.execute_cam_command(
                lambda app: app.save_tool_program_profile(
                    confirmed,
                    expected_configuration_revision=(
                        tool.configuration_revision
                    ),
                    holder_fingerprint=(
                        holder.content_fingerprint
                        if holder is not None
                        else None
                    ),
                ),
                expected_generation=self._generation,
            )

        return ToolProfileSaveInteraction(preview, confirm, make_preview)

    def _drilling_family_context_is_current(
        self, context: DrillingFamilyEditorContext
    ) -> bool:
        current = self._current_facing_operation(context.operation.operation_id)
        if current is None:
            return False
        snapshot = self._service.cam_snapshot
        return (
            current.revision == context.operation.revision
            and current.parameters == context.operation.parameters
            and current.tool_assembly == context.operation.tool_assembly
            and current.machine_requirement == context.operation.machine_requirement
            and current.geometry_inputs == context.operation.geometry_inputs
            and current.enabled == context.operation.enabled
            and snapshot.tool_assemblies == context.tool_assemblies
            and snapshot.tool_definitions == context.tool_definitions
            and snapshot.holder_definitions == context.holder_definitions
            and snapshot.machine_definitions == context.machine_definitions
        )

    @staticmethod
    def _drilling_family_error_target(
        schema,
        message: str,
        code: str,
    ) -> str | None:
        folded = f"{code} {message}".casefold()
        candidates = (
            (("geometry", "source", "hole", "lỗ"), "geometry_summary"),
            (("tool", "dao"), "tool_assembly_id"),
            (("machine", "sync", "capability", "máy"), "machine_id"),
            (("peck",), "peck_depth"),
            (("pitch", "bước ren"), "pitch"),
            (("hand", "chiều ren"), "hand"),
            (("prebore", "pre-bore"), "pre_bore_diameter"),
            (("prehole", "pre-hole"), "pre_hole_diameter"),
            (("diameter", "đường kính"), "finished_bore_diameter"),
            (("clearance",), "clearance_height"),
            (("retract",), "retract_height"),
            (("depth", "độ sâu"), "final_depth"),
            (("spindle", "rpm"), "spindle_speed"),
            (("feed",), "feed_rate"),
            (("coolant",), "coolant"),
            (("dwell",), "dwell_seconds"),
        )
        available = {field.field_id for field in schema.fields}
        for tokens, field_id in candidates:
            if field_id in available and any(token in folded for token in tokens):
                return field_id
        if "diameter" in folded or "đường kính" in folded:
            for field_id in ("nominal_diameter", "finished_bore_diameter"):
                if field_id in available:
                    return field_id
        if "feed" in folded and "feed_per_revolution" in available:
            return "feed_per_revolution"
        return None

    def _drilling_family_diagnostic(
        self,
        schema,
        code: str,
        message: str,
    ) -> FunctionEditorDiagnostic:
        field_id = self._drilling_family_error_target(schema, message, code)
        return FunctionEditorDiagnostic(
            code=code,
            message=message,
            severity=FunctionEditorDiagnosticSeverity.ERROR,
            field_id=field_id,
            section_id=(
                schema.section_for_field(field_id).section_id
                if field_id is not None
                else None
            ),
        )

    def _resolve_drilling_family_inputs(
        self,
        context: DrillingFamilyEditorContext,
        update,
    ):
        if self._drilling_resolver is None:
            raise RuntimeError("Hole geometry resolver chưa sẵn sàng.")
        resolved = self._drilling_resolver(
            update.strategy.geometry, update.strategy.depth
        )
        operation = replace(update.operation, enabled=True)
        if context.kind is DrillingFamilyEditorKind.DRILLING:
            return DrillingGenerator().resolve_inputs(
                operation,
                context.setup,
                assembly=update.assembly,
                tool=update.tool,
                machine=update.machine,
                resolved_geometry=resolved,
            )
        if context.kind is DrillingFamilyEditorKind.TAPPING:
            return TappingGenerator().resolve_inputs(
                operation,
                context.setup,
                assembly=update.assembly,
                tool=update.tool,
                machine=update.machine,
                resolved_geometry=resolved,
            )
        if context.kind is DrillingFamilyEditorKind.REAMING:
            return ReamingGenerator().resolve_inputs(
                operation,
                context.setup,
                assembly=update.assembly,
                tool=update.tool,
                machine=update.machine,
                resolved_geometry=resolved,
            )
        return BoringGenerator().resolve_inputs(
            operation,
            context.setup,
            assembly=update.assembly,
            tool=update.tool,
            holder=update.holder,
            machine=update.machine,
            resolved_geometry=resolved,
        )

    def _validate_drilling_family_production(
        self,
        schema,
        context: DrillingFamilyEditorContext,
        draft: DrillingFamilyEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> tuple[FunctionEditorDiagnostic, ...]:
        """Run existing domain/generator validation without mutating project state."""
        if self._generation is None or self._generation != self._service.cam_generation:
            return (
                self._drilling_family_diagnostic(
                    schema,
                    f"{context.kind.value}.ui.stale_editor",
                    "Function Editor thuộc project generation cũ.",
                ),
            )
        if not self._drilling_family_context_is_current(context):
            return (
                self._drilling_family_diagnostic(
                    schema,
                    f"{context.kind.value}.ui.stale_editor",
                    "Operation hoặc resource đã thay đổi; hãy mở lại editor.",
                ),
            )
        try:
            update = prepare_drilling_family_update(context, draft, values)
            self._resolve_drilling_family_inputs(context, update)
            if context.kind is DrillingFamilyEditorKind.TAPPING:
                return (
                    FunctionEditorDiagnostic(
                        code="tap.post_capability_unbound",
                        message=(
                            "Post chưa được ràng buộc vào operation; tapping cycle "
                            "sẽ được kiểm tra khi Generate Post."
                        ),
                        severity=FunctionEditorDiagnosticSeverity.WARNING,
                        field_id="capability_summary",
                        section_id="capability",
                    ),
                )
            return ()
        except (
            DrillingGenerationError,
            TappingGenerationError,
            ReamingGenerationError,
            BoringGenerationError,
        ) as error:
            return (
                self._drilling_family_diagnostic(
                    schema, error.code.value, str(error)
                ),
            )
        except (RuntimeError, TypeError, ValueError) as error:
            return (
                self._drilling_family_diagnostic(
                    schema,
                    f"{context.kind.value}.invalid_parameters",
                    str(error) or "Thông số drilling family không hợp lệ.",
                ),
            )

    def _apply_drilling_family_production(
        self,
        job_id: CamJobId,
        setup_id: SetupId,
        node_id: CamNodeId,
        context: DrillingFamilyEditorContext,
        draft: DrillingFamilyEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> bool:
        """Commit one validated drilling-family draft through the existing service."""
        schema = {
            DrillingFamilyEditorKind.DRILLING: build_drilling_schema,
            DrillingFamilyEditorKind.TAPPING: build_tapping_schema,
            DrillingFamilyEditorKind.REAMING: build_reaming_schema,
            DrillingFamilyEditorKind.BORING: build_boring_schema,
        }[context.kind](context)
        diagnostics = self._validate_drilling_family_production(
            schema, context, draft, values
        )
        if any(
            item.severity is FunctionEditorDiagnosticSeverity.ERROR
            for item in diagnostics
        ):
            raise RuntimeError(diagnostics[0].message)
        update = prepare_drilling_family_update(context, draft, values)
        if self._generation is None:
            raise RuntimeError("Project generation không còn active.")
        self._service.execute_cam_command(
            lambda app: app.update_tree(
                job_id,
                setup_id,
                lambda tree: tree.rename_node(
                    node_id, update.operation_name
                ).replace_operation(update.operation),
            ),
            expected_generation=self._generation,
        )
        if self._selected_key == ("operation", str(node_id)):
            item = self.tree.currentItem()
            self._guard = True
            try:
                if item is not None:
                    item.setText(0, update.operation_name)
                    item.setText(1, update.operation.artifact_state.status.value.upper())
            finally:
                self._guard = False
            snapshot = self._service.cam_snapshot
            self.editor.show_node(
                update.operation_name,
                update.operation,
                snapshot.tool_assemblies,
                snapshot.machine_definitions,
                snapshot.tool_definitions,
                snapshot.holder_definitions,
            )
            self._set_picked_hole_source(update.hole_source)
            self._picked_reference_resolved = True
            self.editor.show_hole_source(update.hole_source, True)
        self.message.emit(
            f"Đã áp dụng {ui_text(context.kind.title)}; chưa tự tính đường chạy dao."
        )
        QTimer.singleShot(0, self.projection_changed.emit)
        return True

    def _drilling_family_field_action(
        self,
        context: DrillingFamilyEditorContext,
        draft: DrillingFamilyEditorDraftContext,
        action_id: str,
        values: Mapping[str, PresentationValue],
    ) -> Mapping[str, PresentationValue] | None:
        """Select and resolve one hole source into draft state only."""
        if action_id != "select_holes":
            raise ValueError(f"Field action không hỗ trợ: {action_id}")
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            raise RuntimeError("Hole selection workflow chưa sẵn sàng.")
        generation = self._generation
        source = self._drilling_pick_provider(context.setup.wcs.z_axis)
        if not isinstance(source, (HoleReference, HolePattern)):
            raise ValueError("Hole picker trả về source không hợp lệ.")
        if generation is None or generation != self._service.cam_generation:
            raise RuntimeError("Hole selection callback đã stale sau project switch.")
        try:
            unit = context.setup.wcs.origin.unit
            depth = DrillDepthDefinition(
                unit,
                Length(float(values["top_z"]), unit),
                Length(float(values["final_depth"]), unit),
            )
            resolved = self._drilling_resolver(DrillGeometryInput(source, unit), depth)
        except (DrillValidationError, TypeError, ValueError) as error:
            raise ValueError(str(error)) from error
        if (
            resolved.status is not GeometryResolutionStatus.RESOLVED
            or resolved.region is None
        ):
            message = (
                resolved.diagnostics[0].message
                if resolved.diagnostics
                else "Hole source đã chọn không resolve được an toàn."
            )
            raise ValueError(message)
        draft.hole_source = source
        draft.pending_input_ids = {}
        self._set_picked_hole_source(source)
        self._picked_reference_resolved = True
        self.editor.show_hole_source(source, True)
        return drilling_family_geometry_values(source, True)

    def _preview_drilling_family_production(
        self,
        schema,
        context: DrillingFamilyEditorContext,
        draft: DrillingFamilyEditorDraftContext,
        request: FunctionEditorPreviewRequest,
    ) -> str:
        """Create a validated artifact-free drilling-family summary."""
        if (
            request.operation_key != str(context.operation.operation_id)
            or request.generation != self._generation
        ):
            raise RuntimeError("Preview request đã stale.")
        values = dict(request.values)
        diagnostics = self._validate_drilling_family_production(
            schema, context, draft, values
        )
        if any(
            item.severity is FunctionEditorDiagnosticSeverity.ERROR
            for item in diagnostics
        ):
            raise RuntimeError(diagnostics[0].message)
        update = prepare_drilling_family_update(context, draft, values)
        inputs = self._resolve_drilling_family_inputs(context, update)
        strategy = update.strategy
        if isinstance(strategy, DrillingStrategy):
            cycle = strategy.cycle.value
        elif isinstance(strategy, TappingStrategy):
            cycle = f"{strategy.hand.value} · {strategy.synchronization_policy.value}"
        elif isinstance(strategy, ReamingStrategy):
            cycle = strategy.retract_policy.value
        else:
            assert isinstance(strategy, BoringStrategy)
            cycle = strategy.retract_policy.value
        return (
            f"Preview approximate · {len(inputs.holes)} lỗ · {cycle} · "
            f"Top/Depth {strategy.top_z.value:g}/{strategy.final_depth.value:g} "
            f"{strategy.unit.value} · Safe {strategy.clearance_height.value:g}/"
            f"{strategy.retract_height.value:g} · Tool {update.assembly.name} · "
            "controller-neutral, chưa tạo artifact"
        )

    def _calculate_drilling_family_production(
        self,
        context: DrillingFamilyEditorContext,
        _values: Mapping[str, PresentationValue],
    ) -> bool:
        """Calculate only the current applied drilling-family operation."""
        if (
            self._generation is None
            or not self._drilling_family_context_is_current(context)
        ):
            raise RuntimeError("Applied drilling-family state đã stale; hãy mở lại editor.")
        compute = {
            DrillingFamilyEditorKind.DRILLING: self._service.compute_drilling,
            DrillingFamilyEditorKind.TAPPING: self._service.compute_tapping,
            DrillingFamilyEditorKind.REAMING: self._service.compute_reaming,
            DrillingFamilyEditorKind.BORING: self._service.compute_boring,
        }[context.kind]
        result = compute(
            context.operation.operation_id,
            expected_generation=self._generation,
            geometry_resolver=self._drilling_resolver,
        )
        if not result.accepted or result.artifact is None:
            message = (
                result.diagnostics[0].message
                if result.diagnostics
                else f"{context.kind.title} Calculate thất bại."
            )
            raise RuntimeError(message)
        selected = self._selected_operation()
        if (
            selected is not None
            and selected.operation_id == context.operation.operation_id
            and self._toolpath_display is not None
        ):
            displayed = self._toolpath_display(result.artifact)
            if displayed is not False:
                self.editor.show_toolpath_metadata(
                    ToolpathPresentation.from_artifact(result.artifact)
                )
                self._displayed_operation_id = context.operation.operation_id
                self._toolpath_visibility[context.operation.operation_id] = True
        self.message.emit(
            f"{ui_text(context.kind.title)} đã tính toán và công bố kết quả hợp lệ."
        )
        QTimer.singleShot(0, self.projection_changed.emit)
        return True

    def _current_facing_operation(self, operation_id: OperationId) -> Operation | None:
        if not self._service.has_project:
            return None
        for job in self._service.cam_snapshot.jobs:
            for setup in job.setups:
                operation = next(
                    (
                        item
                        for item in setup.operation_tree.operations
                        if item.operation_id == operation_id
                    ),
                    None,
                )
                if operation is not None:
                    return operation
        return None

    def _facing_context_is_current(self, context: FacingEditorContext) -> bool:
        current = self._current_facing_operation(context.operation.operation_id)
        if current is None:
            return False
        snapshot = self._service.cam_snapshot
        return (
            current.revision == context.operation.revision
            and current.parameters == context.operation.parameters
            and current.tool_assembly == context.operation.tool_assembly
            and current.machine_requirement == context.operation.machine_requirement
            and current.geometry_inputs == context.operation.geometry_inputs
            and current.enabled == context.operation.enabled
            and snapshot.tool_assemblies == context.tool_assemblies
            and snapshot.tool_definitions == context.tool_definitions
            and snapshot.holder_definitions == context.holder_definitions
            and snapshot.machine_definitions == context.machine_definitions
        )

    def _contour_context_is_current(self, context: ContourEditorContext) -> bool:
        current = self._current_facing_operation(context.operation.operation_id)
        if current is None:
            return False
        snapshot = self._service.cam_snapshot
        return (
            current.revision == context.operation.revision
            and current.parameters == context.operation.parameters
            and current.tool_assembly == context.operation.tool_assembly
            and current.machine_requirement == context.operation.machine_requirement
            and current.geometry_inputs == context.operation.geometry_inputs
            and current.enabled == context.operation.enabled
            and snapshot.tool_assemblies == context.tool_assemblies
            and snapshot.tool_definitions == context.tool_definitions
            and snapshot.holder_definitions == context.holder_definitions
            and snapshot.machine_definitions == context.machine_definitions
        )

    def _pocket_context_is_current(self, context: PocketEditorContext) -> bool:
        current = self._current_facing_operation(context.operation.operation_id)
        if current is None:
            return False
        snapshot = self._service.cam_snapshot
        return (
            current.revision == context.operation.revision
            and current.parameters == context.operation.parameters
            and current.tool_assembly == context.operation.tool_assembly
            and current.machine_requirement == context.operation.machine_requirement
            and current.geometry_inputs == context.operation.geometry_inputs
            and current.enabled == context.operation.enabled
            and snapshot.tool_assemblies == context.tool_assemblies
            and snapshot.tool_definitions == context.tool_definitions
            and snapshot.holder_definitions == context.holder_definitions
            and snapshot.machine_definitions == context.machine_definitions
        )

    @staticmethod
    def _facing_error_target(message: str, code: str) -> str | None:
        folded = f"{code} {message}".casefold()
        for token, field_id in (
            ("stepover", "stepover"),
            ("stepdown", "stepdown"),
            ("allowance", "stock_allowance"),
            ("overtravel", "overtravel"),
            ("clearance", "clearance_height"),
            ("retract", "retract_height"),
            ("target", "target_height"),
            ("top height", "top_height"),
            ("top z", "top_height"),
            ("spindle", "spindle_speed"),
            ("plunge", "plunge_feed_rate"),
            ("feed", "feed_rate"),
            ("tool", "tool_assembly_id"),
            ("dao", "tool_assembly_id"),
            ("machine", "machine_id"),
            ("máy", "machine_id"),
            ("geometry", "geometry_summary"),
            ("face", "geometry_summary"),
            ("mặt phẳng", "geometry_summary"),
        ):
            if token in folded:
                return field_id
        return None

    def _facing_diagnostic(
        self,
        schema,
        code: str,
        message: str,
    ) -> FunctionEditorDiagnostic:
        field_id = self._facing_error_target(message, code)
        if field_id is not None and field_id not in {
            field.field_id for field in schema.fields
        }:
            field_id = None
        return FunctionEditorDiagnostic(
            code=code,
            message=message,
            severity=FunctionEditorDiagnosticSeverity.ERROR,
            field_id=field_id,
            section_id=(
                schema.section_for_field(field_id).section_id
                if field_id is not None
                else None
            ),
        )

    def _validate_facing_production(
        self,
        schema,
        context: FacingEditorContext,
        draft: FacingEditorDraftContext,
        variant: FacingEditorVariant,
        values: Mapping[str, PresentationValue],
    ) -> tuple[FunctionEditorDiagnostic, ...]:
        """Map unchanged domain/generator validation to production UI targets."""
        if self._generation is None or self._generation != self._service.cam_generation:
            return (
                self._facing_diagnostic(
                    schema,
                    "facing.stale_editor",
                    "Function Editor thuộc project generation cũ.",
                ),
            )
        if not self._facing_context_is_current(context):
            return (
                self._facing_diagnostic(
                    schema,
                    "facing.stale_editor",
                    "Operation hoặc resource đã thay đổi; hãy mở lại editor.",
                ),
            )
        try:
            update = prepare_facing_update(context, draft, variant, values)
            resolved = None
            if variant is FacingEditorVariant.PLANAR_FACE:
                if update.geometry_reference is None or self._face_resolver is None:
                    raise FacingGenerationError(
                        DiagnosticCode.FACING_FACE_REFERENCE_MISSING,
                        "Planar Face Facing thiếu geometry resolver hoặc FACE reference.",
                    )
                resolved = self._face_resolver(update.geometry_reference)
            FacingGenerator().resolve_inputs(
                replace(update.operation, enabled=True),
                context.setup,
                assembly=update.assembly,
                tool=update.tool,
                machine=update.machine,
                resolved_face=resolved,
            )
            return ()
        except FacingGenerationError as error:
            return (
                self._facing_diagnostic(
                    schema, error.code.value, str(error)
                ),
            )
        except (RuntimeError, TypeError, ValueError) as error:
            return (
                self._facing_diagnostic(
                    schema, "facing.invalid_parameters", str(error)
                ),
            )

    def _apply_facing_production(
        self,
        job_id: CamJobId,
        setup_id: SetupId,
        node_id: CamNodeId,
        context: FacingEditorContext,
        draft: FacingEditorDraftContext,
        variant: FacingEditorVariant,
        values: Mapping[str, PresentationValue],
    ) -> bool:
        """Commit one validated Facing draft through the existing atomic service."""
        schema = (
            build_facing_schema(context)
            if variant is FacingEditorVariant.STOCK
            else build_planar_face_facing_schema(context)
        )
        diagnostics = self._validate_facing_production(
            schema, context, draft, variant, values
        )
        if diagnostics:
            raise RuntimeError(diagnostics[0].message)
        update = prepare_facing_update(context, draft, variant, values)
        if self._generation is None:
            raise RuntimeError("Project generation không còn active.")
        self._service.execute_cam_command(
            lambda app: app.update_tree(
                job_id,
                setup_id,
                lambda tree: tree.rename_node(
                    node_id, update.operation_name
                ).replace_operation(update.operation),
            ),
            expected_generation=self._generation,
        )
        if self._selected_key == ("operation", str(node_id)):
            item = self.tree.currentItem()
            self._guard = True
            try:
                if item is not None:
                    item.setText(0, update.operation_name)
                    item.setText(1, update.operation.artifact_state.status.value.upper())
            finally:
                self._guard = False
            snapshot = self._service.cam_snapshot
            self.editor.show_node(
                update.operation_name,
                update.operation,
                snapshot.tool_assemblies,
                snapshot.machine_definitions,
                snapshot.tool_definitions,
                snapshot.holder_definitions,
            )
            self._picked_reference = update.geometry_reference
            self._picked_reference_resolved = (
                variant is FacingEditorVariant.STOCK
                or update.geometry_reference is not None
            )
            if variant is FacingEditorVariant.PLANAR_FACE:
                self.editor.show_reference(
                    update.geometry_reference,
                    self._picked_reference_resolved,
                    subject="face",
                )
        self.message.emit("Đã áp dụng Phay mặt; chưa tự tính đường chạy dao.")
        QTimer.singleShot(0, self.projection_changed.emit)
        return True

    def _facing_field_action(
        self,
        context: FacingEditorContext,
        draft: FacingEditorDraftContext,
        variant: FacingEditorVariant,
        action_id: str,
        _values: Mapping[str, PresentationValue],
    ) -> Mapping[str, PresentationValue] | None:
        """Bind one persistent FACE to the draft without mutating the operation."""
        if action_id != "select_geometry" or variant is not FacingEditorVariant.PLANAR_FACE:
            raise ValueError(f"Field action không hỗ trợ: {action_id}")
        if self._pick_provider is None or self._face_resolver is None:
            raise RuntimeError("Geometry selection workflow chưa sẵn sàng.")
        generation = self._generation
        reference = self._pick_provider()
        if not isinstance(reference, GeometryReference) or reference.kind is not GeometryReferenceKind.FACE:
            raise ValueError("Planar Face Facing yêu cầu đúng một persistent FACE.")
        if generation is None or generation != self._service.cam_generation:
            raise RuntimeError("Geometry selection callback đã stale sau project switch.")
        resolved = self._face_resolver(reference)
        if (
            resolved.status is not GeometryResolutionStatus.RESOLVED
            or resolved.planar_face is None
        ):
            raise ValueError(
                resolved.message or "Planar FACE đã chọn không resolve được an toàn."
            )
        region = resolve_planar_face_region(resolved.planar_face, context.setup)
        draft.geometry_reference = reference
        current_id = (
            context.geometry_reference.reference_id
            if context.geometry_reference is not None
            else None
        )
        if reference.reference_id != current_id:
            draft.pending_input_id = GeometryInputId.new()
        self._picked_reference = reference
        self._picked_reference_resolved = True
        self.editor.show_reference(reference, True, subject="face")
        return {
            "geometry_summary": f"{reference.hint or 'Planar FACE'} · RESOLVED",
            "geometry_reference_id": str(reference.reference_id),
            "target_height": str(region.boundary[0].z),
        }

    def _preview_facing_production(
        self,
        schema,
        context: FacingEditorContext,
        draft: FacingEditorDraftContext,
        variant: FacingEditorVariant,
        request: FunctionEditorPreviewRequest,
    ) -> str:
        """Validate and expose a stale-safe draft overlay summary only."""
        if (
            request.operation_key != str(context.operation.operation_id)
            or request.generation != self._generation
        ):
            raise RuntimeError("Preview request đã stale.")
        values = dict(request.values)
        diagnostics = self._validate_facing_production(
            schema, context, draft, variant, values
        )
        if diagnostics:
            raise RuntimeError(diagnostics[0].message)
        parameters = prepare_facing_update(
            context, draft, variant, values
        ).parameters
        boundary = "Stock BOX" if variant is FacingEditorVariant.STOCK else "Planar FACE"
        self._facing_preview = (
            boundary,
            parameters.direction.value,
            str(parameters.top_height.value),
            str(parameters.target_height.value),
            str(parameters.clearance_height.value),
            str(parameters.retract_height.value),
        )
        return (
            f"Preview CURRENT · {boundary} · {parameters.direction.value} · "
            f"Top/Target {parameters.top_height.value:g}/{parameters.target_height.value:g} "
            f"{parameters.unit.value} · Safe {parameters.clearance_height.value:g}/"
            f"{parameters.retract_height.value:g}"
        )

    def _calculate_facing_production(
        self,
        context: FacingEditorContext,
        variant: FacingEditorVariant,
        _values: Mapping[str, PresentationValue],
    ) -> bool:
        """Calculate only the current applied operation; never consume draft values."""
        if self._generation is None or not self._facing_context_is_current(context):
            raise RuntimeError("Applied Facing state đã stale; hãy mở lại editor.")
        result = self._service.compute_facing(
            context.operation.operation_id,
            expected_generation=self._generation,
            face_resolver=(
                self._face_resolver
                if variant is FacingEditorVariant.PLANAR_FACE
                else None
            ),
        )
        if not result.accepted or result.artifact is None:
            message = (
                result.diagnostics[0].message
                if result.diagnostics
                else "Facing Calculate thất bại."
            )
            raise RuntimeError(message)
        selected = self._selected_operation()
        if (
            selected is not None
            and selected.operation_id == context.operation.operation_id
            and self._toolpath_display is not None
        ):
            displayed = self._toolpath_display(result.artifact)
            if displayed is not False:
                self.editor.show_toolpath_metadata(
                    ToolpathPresentation.from_artifact(result.artifact)
                )
                self._displayed_operation_id = context.operation.operation_id
                self._toolpath_visibility[context.operation.operation_id] = True
        self.message.emit("Phay mặt đã tính toán và công bố kết quả hợp lệ.")
        QTimer.singleShot(0, self.projection_changed.emit)
        return True

    @staticmethod
    def _contour_error_target(message: str, code: str) -> str | None:
        folded = f"{code} {message}".casefold()
        for token, field_id in (
            ("stepdown", "stepdown"),
            ("allowance", "radial_stock_allowance"),
            ("offset", "side"),
            ("side", "side"),
            ("clearance", "clearance_height"),
            ("retract", "retract_height"),
            ("final depth", "final_depth"),
            ("chiều sâu", "final_depth"),
            ("top height", "top_height"),
            ("top z", "top_height"),
            ("spindle", "spindle_speed"),
            ("plunge", "plunge_feed_rate"),
            ("feed", "cutting_feed_rate"),
            ("lead", "lead_length"),
            ("tool", "tool_assembly_id"),
            ("dao", "tool_assembly_id"),
            ("machine", "machine_id"),
            ("máy", "machine_id"),
            ("profile source", "profile_source"),
            ("profile", "geometry_summary"),
            ("geometry", "geometry_summary"),
            ("wire", "geometry_summary"),
            ("face", "geometry_summary"),
        ):
            if token in folded:
                return field_id
        return None

    def _contour_diagnostic(
        self,
        schema,
        code: str,
        message: str,
    ) -> FunctionEditorDiagnostic:
        field_id = self._contour_error_target(message, code)
        if field_id is not None and field_id not in {
            field.field_id for field in schema.fields
        }:
            field_id = None
        return FunctionEditorDiagnostic(
            code=code,
            message=message,
            severity=FunctionEditorDiagnosticSeverity.ERROR,
            field_id=field_id,
            section_id=(
                schema.section_for_field(field_id).section_id
                if field_id is not None
                else None
            ),
        )

    def _validate_contour_production(
        self,
        schema,
        context: ContourEditorContext,
        draft: ContourEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> tuple[FunctionEditorDiagnostic, ...]:
        """Run the unchanged Contour generator validation without mutation."""
        if self._generation is None or self._generation != self._service.cam_generation:
            return (
                self._contour_diagnostic(
                    schema,
                    "contour.ui.stale_editor",
                    "Function Editor thuộc project generation cũ.",
                ),
            )
        if not self._contour_context_is_current(context):
            return (
                self._contour_diagnostic(
                    schema,
                    "contour.ui.stale_editor",
                    "Operation hoặc resource đã thay đổi; hãy mở lại editor.",
                ),
            )
        try:
            update = prepare_contour_update(context, draft, values)
            if self._profile_resolver is None:
                raise ContourGenerationError(
                    DiagnosticCode.CONTOUR_PROFILE_MISSING,
                    "Contour geometry resolver chưa sẵn sàng.",
                )
            resolved = self._profile_resolver(update.geometry_reference)
            ContourGenerator().resolve_inputs(
                replace(update.operation, enabled=True),
                context.setup,
                assembly=update.assembly,
                tool=update.tool,
                machine=update.machine,
                resolved_profile=resolved,
            )
            return ()
        except ContourGenerationError as error:
            return (
                self._contour_diagnostic(schema, error.code.value, str(error)),
            )
        except (RuntimeError, TypeError, ValueError) as error:
            return (
                self._contour_diagnostic(
                    schema, "contour.invalid_parameters", str(error)
                ),
            )

    def _apply_contour_production(
        self,
        job_id: CamJobId,
        setup_id: SetupId,
        node_id: CamNodeId,
        context: ContourEditorContext,
        draft: ContourEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> bool:
        """Commit one validated Contour draft through the existing service."""
        schema = build_contour_schema(context)
        diagnostics = self._validate_contour_production(
            schema, context, draft, values
        )
        if diagnostics:
            raise RuntimeError(diagnostics[0].message)
        update = prepare_contour_update(context, draft, values)
        if self._generation is None:
            raise RuntimeError("Project generation không còn active.")
        self._service.execute_cam_command(
            lambda app: app.update_tree(
                job_id,
                setup_id,
                lambda tree: tree.rename_node(
                    node_id, update.operation_name
                ).replace_operation(update.operation),
            ),
            expected_generation=self._generation,
        )
        if self._selected_key == ("operation", str(node_id)):
            item = self.tree.currentItem()
            self._guard = True
            try:
                if item is not None:
                    item.setText(0, update.operation_name)
                    item.setText(1, update.operation.artifact_state.status.value.upper())
            finally:
                self._guard = False
            snapshot = self._service.cam_snapshot
            self.editor.show_node(
                update.operation_name,
                update.operation,
                snapshot.tool_assemblies,
                snapshot.machine_definitions,
                snapshot.tool_definitions,
                snapshot.holder_definitions,
            )
            self._picked_reference = update.geometry_reference
            self._picked_reference_resolved = True
            self.editor.show_reference(update.geometry_reference, True)
        self.message.emit("Đã áp dụng Biên dạng 2D; chưa tự tính đường chạy dao.")
        QTimer.singleShot(0, self.projection_changed.emit)
        return True

    def _contour_field_action(
        self,
        context: ContourEditorContext,
        draft: ContourEditorDraftContext,
        action_id: str,
        _values: Mapping[str, PresentationValue],
    ) -> Mapping[str, PresentationValue] | None:
        """Select one persistent FACE/WIRE into the draft without mutation."""
        if action_id != "select_geometry":
            raise ValueError(f"Field action không hỗ trợ: {action_id}")
        if self._contour_pick_provider is None or self._profile_resolver is None:
            raise RuntimeError("Contour geometry selection workflow chưa sẵn sàng.")
        generation = self._generation
        reference = self._contour_pick_provider()
        if not isinstance(reference, GeometryReference) or reference.kind not in {
            GeometryReferenceKind.FACE,
            GeometryReferenceKind.SKETCH_OR_PROFILE,
        }:
            raise ValueError("2D Contour yêu cầu planar FACE hoặc closed WIRE.")
        if generation is None or generation != self._service.cam_generation:
            raise RuntimeError("Geometry selection callback đã stale sau project switch.")
        resolved = self._profile_resolver(reference)
        if (
            resolved.status is not GeometryResolutionStatus.RESOLVED
            or resolved.profile is None
        ):
            raise ValueError(
                resolved.message or "Contour profile đã chọn không resolve được an toàn."
            )
        draft.geometry_reference = reference
        current_id = (
            context.geometry_reference.reference_id
            if context.geometry_reference is not None
            else None
        )
        if reference.reference_id != current_id:
            draft.pending_input_id = GeometryInputId.new()
        self._picked_reference = reference
        self._picked_reference_resolved = True
        self.editor.show_reference(reference, True)
        profile = resolved.profile
        profile_source = (
            ContourProfileSource.PLANAR_FACE_OUTER
            if reference.kind is GeometryReferenceKind.FACE
            else ContourProfileSource.CLOSED_WIRE
        )
        kind = "Planar FACE" if reference.kind is GeometryReferenceKind.FACE else "Closed WIRE"
        return {
            "geometry_summary": (
                f"1 chain · {len(profile.outer_loop.segments)} segments · closed · "
                f"{kind} · RESOLVED · {profile.outer_loop.orientation.value}"
            ),
            "geometry_reference_id": str(reference.reference_id),
            "profile_source": profile_source.value,
        }

    def _preview_contour_production(
        self,
        schema,
        context: ContourEditorContext,
        draft: ContourEditorDraftContext,
        request: FunctionEditorPreviewRequest,
    ) -> str:
        """Create a validated, artifact-free summary of the draft path inputs."""
        if (
            request.operation_key != str(context.operation.operation_id)
            or request.generation != self._generation
        ):
            raise RuntimeError("Preview request đã stale.")
        values = dict(request.values)
        diagnostics = self._validate_contour_production(
            schema, context, draft, values
        )
        if diagnostics:
            raise RuntimeError(diagnostics[0].message)
        update = prepare_contour_update(context, draft, values)
        if self._profile_resolver is None:
            raise RuntimeError("Contour geometry resolver chưa sẵn sàng.")
        resolved = self._profile_resolver(update.geometry_reference)
        inputs = ContourGenerator().resolve_inputs(
            replace(update.operation, enabled=True),
            context.setup,
            assembly=update.assembly,
            tool=update.tool,
            machine=update.machine,
            resolved_profile=resolved,
        )
        parameters = update.parameters
        pass_count = 1
        if parameters.multiple_depth_passes:
            pass_count = max(
                1,
                math.ceil(
                    (parameters.top_height.value - parameters.final_cut_depth)
                    / parameters.stepdown.value
                ),
            )
        if parameters.finishing_pass:
            pass_count += 1
        self._contour_preview = (
            str(len(inputs.path.loop.segments)),
            inputs.path.loop.orientation.value,
            parameters.side.value,
            parameters.direction.value,
            str(parameters.top_height.value),
            str(parameters.final_cut_depth),
            str(parameters.clearance_height.value),
            str(parameters.retract_height.value),
            str(parameters.lead_length.value),
        )
        return (
            f"Preview CURRENT · 1 closed chain · {len(inputs.path.loop.segments)} segments · "
            f"{inputs.path.loop.orientation.value} · {parameters.side.value}/"
            f"{parameters.direction.value} · {pass_count} pass · "
            f"Top/Depth {parameters.top_height.value:g}/{parameters.final_cut_depth:g} "
            f"{parameters.unit.value} · Lead linear {parameters.lead_length.value:g} · "
            f"Safe {parameters.clearance_height.value:g}/{parameters.retract_height.value:g}"
        )

    def _calculate_contour_production(
        self,
        context: ContourEditorContext,
        _values: Mapping[str, PresentationValue],
    ) -> bool:
        """Calculate only the current applied Contour operation."""
        if self._generation is None or not self._contour_context_is_current(context):
            raise RuntimeError("Applied Contour state đã stale; hãy mở lại editor.")
        if self._profile_resolver is None:
            raise RuntimeError("Contour geometry resolver chưa sẵn sàng.")
        result = self._service.compute_contour(
            context.operation.operation_id,
            expected_generation=self._generation,
            profile_resolver=self._profile_resolver,
        )
        if not result.accepted or result.artifact is None:
            message = (
                result.diagnostics[0].message
                if result.diagnostics
                else "2D Contour Calculate thất bại."
            )
            raise RuntimeError(message)
        selected = self._selected_operation()
        if (
            selected is not None
            and selected.operation_id == context.operation.operation_id
            and self._toolpath_display is not None
        ):
            displayed = self._toolpath_display(result.artifact)
            if displayed is not False:
                self.editor.show_toolpath_metadata(
                    ToolpathPresentation.from_artifact(result.artifact)
                )
                self._displayed_operation_id = context.operation.operation_id
                self._toolpath_visibility[context.operation.operation_id] = True
        self.message.emit("Biên dạng 2D đã tính toán và công bố kết quả hợp lệ.")
        QTimer.singleShot(0, self.projection_changed.emit)
        return True

    @staticmethod
    def _pocket_error_target(message: str, code: str) -> str | None:
        folded = f"{code} {message}".casefold()
        for token, field_id in (
            ("stepover", "stepover"),
            ("stepdown", "stepdown"),
            ("radial", "radial_stock_allowance"),
            ("floor", "axial_allowance"),
            ("allowance", "axial_allowance"),
            ("clearance", "clearance_height"),
            ("retract", "retract_height"),
            ("bottom", "bottom_z"),
            ("depth", "bottom_z"),
            ("top z", "top_z"),
            ("spindle", "spindle_speed"),
            ("plunge", "plunge_feed_rate"),
            ("entry", "entry_policy"),
            ("feed", "cutting_feed_rate"),
            ("tolerance", "tolerance"),
            ("tool", "tool_assembly_id"),
            ("dao", "tool_assembly_id"),
            ("machine", "machine_id"),
            ("máy", "machine_id"),
            ("island", "island_summary"),
            ("inner loop", "island_summary"),
            ("profile", "geometry_summary"),
            ("boundary", "geometry_summary"),
            ("geometry", "geometry_summary"),
        ):
            if token in folded:
                return field_id
        return None

    def _pocket_diagnostic(
        self,
        schema,
        code: str,
        message: str,
    ) -> FunctionEditorDiagnostic:
        field_id = self._pocket_error_target(message, code)
        if field_id is not None and field_id not in {
            field.field_id for field in schema.fields
        }:
            field_id = None
        return FunctionEditorDiagnostic(
            code=code,
            message=message,
            severity=FunctionEditorDiagnosticSeverity.ERROR,
            field_id=field_id,
            section_id=(
                schema.section_for_field(field_id).section_id
                if field_id is not None
                else None
            ),
        )

    def _validate_pocket_production(
        self,
        schema,
        context: PocketEditorContext,
        draft: PocketEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> tuple[FunctionEditorDiagnostic, ...]:
        """Run the unchanged Pocket generator validation without mutation."""
        if self._generation is None or self._generation != self._service.cam_generation:
            return (
                self._pocket_diagnostic(
                    schema,
                    "pocket.ui.stale_editor",
                    "Function Editor thuộc project generation cũ.",
                ),
            )
        if not self._pocket_context_is_current(context):
            return (
                self._pocket_diagnostic(
                    schema,
                    "pocket.ui.stale_editor",
                    "Operation hoặc resource đã thay đổi; hãy mở lại editor.",
                ),
            )
        try:
            update = prepare_pocket_update(context, draft, values)
            if self._pocket_resolver is None:
                raise PocketGenerationError(
                    DiagnosticCode.POCKET_PROFILE_MISSING,
                    "Pocket geometry resolver chưa sẵn sàng.",
                )
            resolved = self._pocket_resolver(update.geometry_reference)
            PocketGenerator().resolve_inputs(
                replace(update.operation, enabled=True),
                context.setup,
                assembly=update.assembly,
                tool=update.tool,
                machine=update.machine,
                resolved_geometry=resolved,
            )
            return ()
        except PocketGenerationError as error:
            return (
                self._pocket_diagnostic(schema, error.code.value, str(error)),
            )
        except (RuntimeError, TypeError, ValueError) as error:
            return (
                self._pocket_diagnostic(
                    schema, "pocket.invalid_parameters", str(error)
                ),
            )

    def _apply_pocket_production(
        self,
        job_id: CamJobId,
        setup_id: SetupId,
        node_id: CamNodeId,
        context: PocketEditorContext,
        draft: PocketEditorDraftContext,
        values: Mapping[str, PresentationValue],
    ) -> bool:
        """Commit one validated Pocket draft through the existing service."""
        schema = build_pocket_schema(context)
        diagnostics = self._validate_pocket_production(
            schema, context, draft, values
        )
        if diagnostics:
            raise RuntimeError(diagnostics[0].message)
        update = prepare_pocket_update(context, draft, values)
        if self._generation is None:
            raise RuntimeError("Project generation không còn active.")
        self._service.execute_cam_command(
            lambda app: app.update_tree(
                job_id,
                setup_id,
                lambda tree: tree.rename_node(
                    node_id, update.operation_name
                ).replace_operation(update.operation),
            ),
            expected_generation=self._generation,
        )
        if self._selected_key == ("operation", str(node_id)):
            item = self.tree.currentItem()
            self._guard = True
            try:
                if item is not None:
                    item.setText(0, update.operation_name)
                    item.setText(1, update.operation.artifact_state.status.value.upper())
            finally:
                self._guard = False
            snapshot = self._service.cam_snapshot
            self.editor.show_node(
                update.operation_name,
                update.operation,
                snapshot.tool_assemblies,
                snapshot.machine_definitions,
                snapshot.tool_definitions,
                snapshot.holder_definitions,
            )
            self._picked_reference = update.geometry_reference
            self._picked_reference_resolved = True
            self.editor.show_reference(update.geometry_reference, True)
        self.message.emit("Đã áp dụng Hốc 2.5D; chưa tự tính đường chạy dao.")
        QTimer.singleShot(0, self.projection_changed.emit)
        return True

    def _pocket_field_action(
        self,
        context: PocketEditorContext,
        draft: PocketEditorDraftContext,
        action_id: str,
        _values: Mapping[str, PresentationValue],
    ) -> Mapping[str, PresentationValue] | None:
        """Select one persistent FACE/WIRE Pocket region without mutation."""
        if action_id != "select_geometry":
            raise ValueError(f"Field action không hỗ trợ: {action_id}")
        if self._contour_pick_provider is None or self._pocket_resolver is None:
            raise RuntimeError("Pocket geometry selection workflow chưa sẵn sàng.")
        generation = self._generation
        reference = self._contour_pick_provider()
        if not isinstance(reference, GeometryReference) or reference.kind not in {
            GeometryReferenceKind.FACE,
            GeometryReferenceKind.SKETCH_OR_PROFILE,
        }:
            raise ValueError("Pocket yêu cầu planar FACE hoặc closed WIRE.")
        if generation is None or generation != self._service.cam_generation:
            raise RuntimeError("Geometry selection callback đã stale sau project switch.")
        resolved = self._pocket_resolver(reference)
        if (
            resolved.status is not GeometryResolutionStatus.RESOLVED
            or resolved.region is None
        ):
            message = (
                resolved.diagnostics[0].message
                if resolved.diagnostics
                else "Pocket region đã chọn không resolve được an toàn."
            )
            raise ValueError(message)
        draft.geometry_reference = reference
        current_id = (
            context.geometry_reference.reference_id
            if context.geometry_reference is not None
            else None
        )
        if reference.reference_id != current_id:
            draft.pending_input_id = GeometryInputId.new()
        self._picked_reference = reference
        self._picked_reference_resolved = True
        self.editor.show_reference(reference, True)
        loop = resolved.region.boundary.outer_loop
        return {
            "geometry_summary": (
                f"1 Closed Region · {len(loop.segments)} segments · 0 Islands · "
                f"RESOLVED · {loop.orientation.value}"
            ),
            "geometry_reference_id": str(reference.reference_id),
            "island_summary": "0 island · Pocket v1 chỉ gia công một outer region",
        }

    def _preview_pocket_production(
        self,
        schema,
        context: PocketEditorContext,
        draft: PocketEditorDraftContext,
        request: FunctionEditorPreviewRequest,
    ) -> str:
        """Create a validated, artifact-free summary of draft Pocket inputs."""
        if (
            request.operation_key != str(context.operation.operation_id)
            or request.generation != self._generation
        ):
            raise RuntimeError("Preview request đã stale.")
        values = dict(request.values)
        diagnostics = self._validate_pocket_production(
            schema, context, draft, values
        )
        if diagnostics:
            raise RuntimeError(diagnostics[0].message)
        update = prepare_pocket_update(context, draft, values)
        if self._pocket_resolver is None:
            raise RuntimeError("Pocket geometry resolver chưa sẵn sàng.")
        resolved = self._pocket_resolver(update.geometry_reference)
        inputs = PocketGenerator().resolve_inputs(
            replace(update.operation, enabled=True),
            context.setup,
            assembly=update.assembly,
            tool=update.tool,
            machine=update.machine,
            resolved_geometry=resolved,
        )
        strategy = update.strategy
        self._pocket_preview = (
            str(len(inputs.offset_loops)),
            str(len(inputs.depth_levels)),
            strategy.cutting_direction.value,
            str(strategy.stepover.value),
            str(strategy.top_z.value),
            str(strategy.final_depth.value),
            strategy.entry_policy.value,
            str(strategy.clearance_height.value),
            str(strategy.retract_height.value),
        )
        return (
            f"Preview approximate · 1 closed region · 0 islands · Offset · "
            f"{len(inputs.offset_loops)} loops · {len(inputs.depth_levels)} levels · "
            f"{strategy.cutting_direction.value} · Stepover {strategy.stepover.value:g} · "
            f"Top/Depth {strategy.top_z.value:g}/{strategy.final_depth.value:g} "
            f"{strategy.unit.value} · Entry vertical plunge · Safe "
            f"{strategy.clearance_height.value:g}/{strategy.retract_height.value:g} · "
            "không phải bằng chứng collision-safe"
        )

    def _calculate_pocket_production(
        self,
        context: PocketEditorContext,
        _values: Mapping[str, PresentationValue],
    ) -> bool:
        """Calculate only the current applied Pocket operation."""
        if self._generation is None or not self._pocket_context_is_current(context):
            raise RuntimeError("Applied Pocket state đã stale; hãy mở lại editor.")
        if self._pocket_resolver is None:
            raise RuntimeError("Pocket geometry resolver chưa sẵn sàng.")
        result = self._service.compute_pocket(
            context.operation.operation_id,
            expected_generation=self._generation,
            geometry_resolver=self._pocket_resolver,
        )
        if not result.accepted or result.artifact is None:
            message = (
                result.diagnostics[0].message
                if result.diagnostics
                else "Pocket 2.5D Calculate thất bại."
            )
            raise RuntimeError(message)
        selected = self._selected_operation()
        if (
            selected is not None
            and selected.operation_id == context.operation.operation_id
            and self._toolpath_display is not None
        ):
            displayed = self._toolpath_display(result.artifact)
            if displayed is not False:
                self.editor.show_toolpath_metadata(
                    ToolpathPresentation.from_artifact(result.artifact)
                )
                self._displayed_operation_id = context.operation.operation_id
                self._toolpath_visibility[context.operation.operation_id] = True
        self.message.emit("Hốc 2.5D đã tính toán và công bố kết quả hợp lệ.")
        QTimer.singleShot(0, self.projection_changed.emit)
        return True

    def clear_selection(self) -> None:
        """Clear transient CAM selection without mutating project or CAD data."""
        self.tree.clearSelection()
        self.tree.setCurrentItem(None)

    def set_selected_enabled(self, enabled: bool) -> None:
        """Route enabled state through the existing OperationTree command."""
        if type(enabled) is not bool:
            raise TypeError("Operation enabled state must be boolean")
        item = self.tree.currentItem()
        context = self._tree_context()
        if (
            item is None
            or context is None
            or item.data(0, _KIND_ROLE) not in {"group", "operation"}
        ):
            return
        job_id, setup_id, _tree, _parent_id = context
        node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
        changed = self._execute(
            lambda app: app.update_tree(
                job_id,
                setup_id,
                lambda tree: tree.set_enabled(node_id, enabled),
            )
        )
        if changed is not None:
            self.refresh((item.data(0, _KIND_ROLE), str(node_id)))

    def _render(self, preserve: tuple[str, str] | None) -> None:
        self._stash_active_operation_draft()
        self._guard = True
        self.tree.clear()
        matches: list[QTreeWidgetItem] = []
        if not self._service.has_project:
            item = QTreeWidgetItem(["Chưa mở dự án CAM", "—"])
            item.setDisabled(True)
            self.tree.addTopLevelItem(item)
        else:
            snapshot = self._service.cam_snapshot
            if not snapshot.jobs:
                item = QTreeWidgetItem([ui_text("No CAM jobs"), "MISSING"])
                item.setDisabled(True)
                self.tree.addTopLevelItem(item)
            for job in snapshot.jobs:
                job_item = self._item(job.name, "job", str(job.job_id), job.job_id, None,
                                      "ACTIVE" if job.job_id == snapshot.active_job_id else "")
                self.tree.addTopLevelItem(job_item)
                for setup in job.setups:
                    setup_item = self._item(setup.name, "setup", str(setup.setup_id), job.job_id,
                                            setup.setup_id, "ACTIVE" if setup.setup_id == job.active_setup_id else "")
                    job_item.addChild(setup_item)
                    setup_item.addChild(self._item(f"Stock ({setup.stock.kind.value})", "stock", "stock", job.job_id, setup.setup_id, ""))
                    fixtures = self._item("Fixtures", "fixtures", "fixtures", job.job_id, setup.setup_id, str(len(setup.fixtures)))
                    setup_item.addChild(fixtures)
                    for fixture in setup.fixtures:
                        fixtures.addChild(self._item(fixture.name, "fixture", str(fixture.fixture_id), job.job_id, setup.setup_id,
                                                    "DISABLED" if not fixture.enabled else ""))
                    self._append_nodes(setup_item, setup.operation_tree, setup.operation_tree.root_id, job.job_id, setup.setup_id)
                job_item.setExpanded(True)
            if preserve:
                iterator = QTreeWidgetItemIterator(self.tree)
                while iterator.value():
                    item = iterator.value()
                    if (item.data(0, _KIND_ROLE), item.data(0, _ID_ROLE)) == preserve:
                        matches.append(item)
                    iterator += 1
        if matches:
            self.tree.setCurrentItem(matches[0])
        self._guard = False
        if matches:
            self._selected_key = preserve
            self._show_properties(matches[0])
        else:
            self._selected_key = None
            self._active_editor_operation_id = None
            self.editor.clear()
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
        self.program_assembly_panel.refresh_sources()
        self.selection_identity_changed.emit(self._selected_key)
        self.projection_changed.emit()
        self._guard = True
        try:
            localize_widget_tree(self)
        finally:
            self._guard = False

    def _append_nodes(self, parent: QTreeWidgetItem, tree, parent_id, job_id, setup_id) -> None:
        node = tree.get_node(parent_id)
        for child_id in node.child_ids:
            child = tree.get_node(child_id)
            operation = (
                tree.get_operation(child.operation_id)
                if child.operation_id is not None else None
            )
            status = ""
            if operation is not None:
                status = operation.artifact_state.status.value.upper()
                if not operation.enabled:
                    status = f"DISABLED · {status}"
                snapshot = self._service.cam_snapshot
                assembly = next((value for value in snapshot.tool_assemblies
                                 if value.assembly_id == operation.tool_assembly.assembly_id), None)
                tool_status = operation.tool_assembly.assess(assembly)
                if tool_status.value != "valid":
                    status += f" · TOOL {tool_status.value.upper()}"
                if operation.machine_requirement is not None:
                    machine = next((value for value in snapshot.machine_definitions
                                    if value.machine_id == operation.machine_requirement.machine_id), None)
                    if machine is None:
                        status += " · MACHINE MISSING"
                is_hole_operation = operation.strategy_key in {
                    "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                }
                if operation.geometry_inputs:
                    status += (
                        " · HOLE BOUND" if is_hole_operation
                        else " · PROFILE BOUND"
                    )
                elif is_hole_operation:
                    try:
                        strategy = _hole_strategy(operation)
                        has_explicit_pattern = (
                            isinstance(strategy.geometry.source, HolePattern)
                            and not _hole_references(strategy.geometry.source)
                        )
                    except (RuntimeError, TypeError, ValueError):
                        has_explicit_pattern = False
                    status += (
                        " · HOLE PATTERN" if has_explicit_pattern
                        else " · HOLE MISSING"
                    )
                elif operation.strategy_key in {"contour_2d", "pocket_2_5d"}:
                    status += " · PROFILE MISSING"
            item = self._item(child.name, child.kind.value, str(child.node_id), job_id, setup_id, status)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            parent.addChild(item)
            if operation is None:
                self._append_nodes(item, tree, child.node_id, job_id, setup_id)

    @staticmethod
    def _item(label: str, kind: str, identity: str, job_id, setup_id, status: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label, status])
        item.setData(0, _KIND_ROLE, kind)
        item.setData(0, _ID_ROLE, identity)
        item.setData(0, _JOB_ROLE, str(job_id) if job_id else None)
        item.setData(0, _SETUP_ROLE, str(setup_id) if setup_id else None)
        return item

    def _selection_changed(self) -> None:
        if self._guard:
            return
        item = self.tree.currentItem()
        if item is None:
            self._stash_active_operation_draft()
            self._active_editor_operation_id = None
            self._active_editor_strategy_key = None
            self._selected_key = None
            self.editor.clear()
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
            self.selection_identity_changed.emit(None)
            return
        self._selected_key = (item.data(0, _KIND_ROLE), item.data(0, _ID_ROLE))
        self._show_properties(item)
        localize_widget_tree(self.editor)
        self.selection_identity_changed.emit(self._selected_key)

    def _show_properties(self, item: QTreeWidgetItem) -> None:
        self._stash_active_operation_draft()
        self._active_editor_operation_id = None
        self._active_editor_strategy_key = None
        self.simulation_panel.clear_source()
        self._clear_simulation_issue_focus()
        kind = item.data(0, _KIND_ROLE)
        job = self._find_job(item)
        setup = self._find_setup(item, job)
        if kind == "job" and job:
            self.editor.show_job(job.name)
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
        elif kind in {"setup", "stock"} and setup:
            self.editor.show_setup(setup)
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
        elif kind in {"group", "operation"} and setup:
            node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE)))
            operation = (
                setup.operation_tree.get_operation(node.operation_id)
                if node.operation_id is not None else None
            )
            if operation is None:
                self._picked_reference = None
                self._picked_hole_reference = None
                self._picked_hole_source = None
                self.editor.show_node(node.name, None)
                self._remove_displayed_toolpath()
                self.post_panel.clear_operation()
                self.program_assembly_panel.clear_selected_operation()
            else:
                self.post_panel.set_operation(
                    operation.operation_id,
                    generation=self._generation,
                    operation_name=node.name,
                )
                self.program_assembly_panel.set_selected_operation(
                    operation.operation_id,
                    operation_name=node.name,
                )
                self._picked_hole_reference = None
                self._picked_hole_source = None
                self._picked_reference = (
                    operation.geometry_inputs[0].reference
                    if len(operation.geometry_inputs) == 1 else None
                )
                if operation.strategy_key in {
                    "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                }:
                    try:
                        hole_strategy = _hole_strategy(operation)
                    except (TypeError, ValueError):
                        hole_strategy = None
                    source = (
                        hole_strategy.geometry.source
                        if hole_strategy is not None else None
                    )
                    if source is not None and _operation_matches_hole_source(
                        operation, source,
                    ):
                        self._set_picked_hole_source(source)
                self._picked_reference_resolved = self._resolve_picked_reference()
                snapshot = self._service.cam_snapshot
                self.editor.show_node(
                    node.name,
                    operation,
                    snapshot.tool_assemblies,
                    snapshot.machine_definitions,
                    snapshot.tool_definitions,
                    snapshot.holder_definitions,
                )
                if operation.strategy_key == "pocket_2_5d":
                    saved = self._pocket_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_pocket_state(saved[0])
                        self._picked_reference = saved[1]
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                elif operation.strategy_key == "drilling_v1":
                    saved = self._drilling_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_drilling_state(saved[0])
                        self._set_picked_hole_source(saved[1])
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                elif operation.strategy_key == "tapping_v1":
                    saved = self._tapping_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_tapping_state(saved[0])
                        self._set_picked_hole_source(saved[1])
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                elif operation.strategy_key == "reaming_v1":
                    saved = self._reaming_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_reaming_state(saved[0])
                        self._set_picked_hole_source(saved[1])
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                elif operation.strategy_key == "boring_v1":
                    saved = self._boring_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_boring_state(saved[0])
                        self._set_picked_hole_source(saved[1])
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                if operation.strategy_key in {
                    "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                }:
                    self.editor.show_hole_source(
                        self._picked_hole_source,
                        self._picked_reference_resolved,
                    )
                else:
                    self.editor.show_reference(
                        self._picked_reference,
                        self._picked_reference_resolved,
                    )
                artifact = (self._service.load_toolpath_artifact(operation.operation_id)
                            if operation.artifact_state.status is ArtifactStatus.VALID else None)
                if artifact is not None and self._toolpath_display is not None:
                    displayed = self._toolpath_display(artifact)
                    if displayed is not False:
                        self.editor.show_toolpath_metadata(
                            ToolpathPresentation.from_artifact(artifact)
                        )
                        previous = self._displayed_operation_id
                        self._displayed_operation_id = operation.operation_id
                        self._toolpath_visibility[operation.operation_id] = True
                        if previous is not None and previous != operation.operation_id:
                            self._remove_toolpath(previous)
                else:
                    self._remove_displayed_toolpath()
                self._bind_simulation_operation(operation, artifact)
        else:
            self._picked_reference = None
            self.editor.clear()
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
        self._update_generate_action()

    def _remove_toolpath(self, operation_id: OperationId) -> None:
        if self._toolpath_remove is not None:
            self._toolpath_remove(operation_id)
        elif self._toolpath_clear is not None:
            self._toolpath_clear()

    def _remove_displayed_toolpath(self) -> None:
        if self._displayed_operation_id is not None:
            self._remove_toolpath(self._displayed_operation_id)
            self._displayed_operation_id = None

    def _bind_simulation_operation(
        self,
        operation: Operation,
        artifact: object | None,
    ) -> None:
        policies = self._simulation_policies.get(
            operation.operation_id,
            (SimulationSamplingPolicy(), SimulationDisplayPolicy()),
        )
        self.simulation_panel.set_policy(*policies)
        if artifact is None:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                "sim.source_missing: ToolpathArtifact chưa COMPLETE/current",
            )
            return
        try:
            inputs = self._service.capture_simulation_inputs(
                operation.operation_id,
                sampling_policy=policies[0],
            )
            build_tool_envelope(
                tool=inputs.tool,
                assembly=inputs.assembly,
                holder=inputs.holder,
            )
            if any(
                fixture.enabled
                and (
                    fixture.geometry_reference.kind
                    not in {GeometryReferenceKind.BODY, GeometryReferenceKind.OCCURRENCE}
                    or fixture.geometry_reference.geometry_kind
                    is not GeometryRepresentationKind.BREP
                )
                for fixture in inputs.setup.fixtures
            ):
                raise SimulationPreflightError(
                    SimulationIssueCode.UNSUPPORTED_GEOMETRY,
                    "Fixture geometry is unsupported",
                )
        except SimulationPreflightError as error:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                f"{error.code.value}: {error}",
            )
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                f"sim.unsupported_geometry: {error}",
            )
            return
        if self._simulation_scene_builder is not None:
            try:
                # Validate native stock/fixture ownership on the owner thread
                # before enabling Run. The scene is rebuilt at execution time
                # so this validation never retains native handles in UI state.
                self._simulation_scene_builder(inputs)
            except SimulationPreflightError as error:
                self.simulation_panel.show_unavailable(
                    operation.operation_id,
                    f"{error.code.value}: {error}",
                )
                return
            except (RuntimeError, TypeError, ValueError) as error:
                self.simulation_panel.show_unavailable(
                    operation.operation_id,
                    f"sim.source_unsupported: {error}",
                )
                return
        can_run = (
            self._simulation_scene_builder is not None
            and operation.enabled
            and operation.artifact_state.status is ArtifactStatus.VALID
        )
        self.simulation_panel.show_source(inputs, can_run=can_run)
        record = self._service.simulation_runs.record(operation.operation_id)
        result = self._service.simulation_runs.result(operation.operation_id)
        if (
            result is not None
            and result.artifact_fingerprint == inputs.request.artifact_fingerprint
            and result.input_fingerprint == inputs.request.input_fingerprint
        ):
            self.simulation_panel.set_run_record(record)
            self.simulation_panel.set_result(
                result,
                self._simulation_presentation(operation.operation_id),
                current=True,
            )
            return
        cache_key = (operation.operation_id, inputs.request.artifact_fingerprint.digest)
        if cache_key in self._simulation_cache_attempts:
            if record is not None:
                self.simulation_panel.set_run_record(record)
            return
        self._simulation_cache_attempts.add(cache_key)
        loaded = self._service.load_cached_simulation_for_source(
            operation.operation_id,
            inputs.request.artifact_fingerprint,
        )
        if loaded.status is not SimulationCacheStatus.VALID or loaded.result is None:
            self.simulation_panel.set_cache_diagnostic(
                loaded.message or loaded.status.value
            )
            return
        cached_result = loaded.result
        cached_policies = (cached_result.sampling_policy, policies[1])
        try:
            cached_inputs = self._service.capture_simulation_inputs(
                operation.operation_id,
                sampling_policy=cached_result.sampling_policy,
            )
        except (RuntimeError, TypeError, ValueError):
            self.simulation_panel.set_cache_diagnostic("bộ nhớ đệm đã lỗi thời")
            return
        if cached_inputs.request.input_fingerprint != cached_result.input_fingerprint:
            self.simulation_panel.set_cache_diagnostic("bộ nhớ đệm đã lỗi thời")
            return
        # A cache result retains the original request UUID.  Rebind the
        # freshly captured immutable request to that UUID for the runtime
        # provenance check; request identity/fingerprints remain unchanged.
        cached_inputs = replace(
            cached_inputs,
            request=replace(
                cached_inputs.request,
                request_id=cached_result.request_id,
            ),
        )
        self._simulation_policies[operation.operation_id] = cached_policies
        self.simulation_panel.set_policy(*cached_policies)
        self.simulation_panel.show_source(cached_inputs, can_run=can_run)
        if not self._service.simulation_runs.restore_cached(
            cached_inputs.request,
            cached_result,
            state_callback=self._simulation_state_changed,
        ):
            self.simulation_panel.set_cache_diagnostic("bộ nhớ đệm đã lỗi thời")
            return
        self._display_simulation_result(cached_inputs, cached_result)

    def _run_simulation(self) -> None:
        operation = self._selected_operation()
        if operation is None or self._simulation_scene_builder is None:
            return
        controller = self._service.simulation_runs
        if (
            self._simulation_handle is not None
            and controller.is_active(self._simulation_handle.request.operation_id)
        ):
            return
        sampling = self.simulation_panel.sampling_policy
        try:
            inputs = self._service.capture_simulation_inputs(
                operation.operation_id,
                sampling_policy=sampling,
            )
            # OCP geometry is resolved here, on the UI/owner thread, and is never
            # passed to a worker or retained by the runtime record.
            scene, backend = self._simulation_scene_builder(inputs)
            handle = controller.start(
                inputs.request,
                state_callback=self._simulation_state_changed,
            )
        except SimulationPreflightError as error:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                f"{error.code.value}: {error}",
            )
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                f"sim.unsupported_geometry: {error}",
            )
            return
        self._simulation_handle = handle
        project_id = self._simulation_project_id
        generation = self._generation
        QTimer.singleShot(
            0,
            lambda: self._execute_simulation(
                handle,
                inputs,
                scene,
                backend,
                project_id,
                generation,
            ),
        )

    def _execute_simulation(
        self,
        handle: SimulationRunHandle,
        inputs: SimulationInputSnapshot,
        scene: CollisionScene,
        backend: CollisionBackend,
        project_id: UUID | None,
        generation: int | None,
    ) -> None:
        controller = self._service.simulation_runs

        def current_request():
            try:
                return self._service.capture_simulation_inputs(
                    handle.request.operation_id,
                    sampling_policy=handle.request.sampling_policy,
                ).request
            except Exception as error:
                raise SimulationPreflightError(
                    SimulationIssueCode.STALE_RESULT,
                    "Simulation inputs changed before publish",
                ) from error

        execution = controller.execute(
            handle,
            snapshot=inputs,
            scene=scene,
            backend=backend,
            current_request=current_request,
            progress_callback=self._simulation_progress_changed,
            state_callback=self._simulation_state_changed,
        )
        if not self._simulation_callback_is_current(
            handle,
            project_id,
            generation,
        ):
            return
        self.simulation_panel.set_run_record(
            controller.record(handle.request.operation_id)
        )
        if not execution.accepted or execution.result is None:
            code = execution.code.value if execution.code is not None else "sim.failed"
            self.message.emit(
                f"Mô phỏng: {code} · {ui_text(execution.message or '')}"
            )
            return
        try:
            self._service.persist_simulation_result(execution.result)
        except Exception as error:
            logger.warning("Không thể ghi bộ nhớ đệm mô phỏng", exc_info=True)
            self.simulation_panel.set_cache_diagnostic(
                f"cache write failure: {error}"
            )
        displayed = self._display_simulation_result(inputs, execution.result)
        if displayed:
            self.message.emit(
                f"Mô phỏng {translate_status(execution.result.status.value)} "
                "đã công bố và hiển thị."
            )
        else:
            self.message.emit(
                "Kết quả mô phỏng đã công bố nhưng lớp phủ mới không thể hiển thị; "
                "lớp phủ cũ được giữ."
            )

    def _simulation_progress_changed(self, progress: SimulationProgress) -> None:
        handle = self._simulation_handle
        if handle is None or handle.request.request_id != progress.request_id:
            return
        panel_inputs = self.simulation_panel.inputs
        if (
            panel_inputs is not None
            and panel_inputs.operation.operation_id == handle.request.operation_id
        ):
            self.simulation_panel.set_progress(progress)
        # Native narrow phase stays on its owner thread. Pump bounded event slices
        # so Cancel/project-switch can invalidate the request cooperatively.
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 5)

    def _simulation_state_changed(self, record: SimulationRunRecord) -> None:
        inputs = self.simulation_panel.inputs
        if inputs is None or inputs.operation.operation_id != record.operation_id:
            return
        self.simulation_panel.set_run_record(record)
        self.projection_changed.emit()

    def _cancel_simulation(self) -> None:
        handle = self._simulation_handle
        if handle is None:
            return
        if self._service.simulation_runs.cancel(handle.request.operation_id):
            record = self._service.simulation_runs.record(
                handle.request.operation_id
            )
            self.simulation_panel.set_run_record(record)

    def _apply_simulation_policy(
        self,
        sampling: SimulationSamplingPolicy,
        display: SimulationDisplayPolicy,
    ) -> None:
        operation = self._selected_operation()
        if operation is None:
            return
        self._simulation_policies[operation.operation_id] = (sampling, display)
        self._service.simulation_runs.mark_stale(
            operation.operation_id,
            "Sampling policy changed",
        )
        viewport = self._simulation_viewport()
        if viewport is not None and callable(getattr(viewport, "remove_simulation", None)):
            viewport.remove_simulation(operation.operation_id)
        self.simulation_panel.mark_result_stale(
            "STALE / NON-CURRENT · sampling policy changed"
        )
        artifact = self._service.load_toolpath_artifact(operation.operation_id)
        self._bind_simulation_operation(operation, artifact)

    def _clear_simulation_result(self) -> None:
        operation = self._selected_operation()
        if operation is None:
            return
        self._service.clear_simulation_result(
            operation.operation_id,
            delete_cache=True,
        )
        viewport = self._simulation_viewport()
        if viewport is not None and callable(getattr(viewport, "remove_simulation", None)):
            viewport.remove_simulation(operation.operation_id)
        self.simulation_panel.clear_result_display()
        self.projection_changed.emit()

    def _set_simulation_visibility(self, visible: bool) -> None:
        operation = self._selected_operation()
        viewport = self._simulation_viewport()
        if (
            operation is None
            or viewport is None
            or not callable(getattr(viewport, "set_simulation_visibility", None))
        ):
            return
        viewport.set_simulation_visibility(operation.operation_id, visible)
        presentation = self._simulation_presentation(operation.operation_id)
        if presentation is not None:
            self.simulation_panel.source_labels["overlay"].setText(
                f"{presentation.displayed_path_point_count} / "
                f"{presentation.total_path_point_count} points · "
                f"{presentation.displayed_marker_count} / "
                f"{presentation.total_marker_count} markers · "
                f"{'visible' if presentation.visible else 'hidden'}"
            )

    def _focus_simulation_issue(self, selection: object) -> None:
        if not isinstance(selection, SimulationIssueSelection):
            return
        result = self._service.simulation_runs.result(selection.operation_id)
        viewport = self._simulation_viewport()
        if (
            result is None
            or viewport is None
            or selection.marker_id is None
            or self._simulation_project_id is None
            or not callable(getattr(viewport, "focus_simulation_issue", None))
        ):
            return
        viewport.focus_simulation_issue(
            project_id=self._simulation_project_id,
            operation_id=selection.operation_id,
            result_id=result.result_id,
            marker_id=selection.marker_id,
        )

    def _clear_simulation_issue_focus(self) -> None:
        viewport = self._simulation_viewport()
        if viewport is not None and callable(
            getattr(viewport, "clear_simulation_issue_focus", None)
        ):
            viewport.clear_simulation_issue_focus()

    def _display_simulation_result(
        self,
        inputs: SimulationInputSnapshot,
        result,
    ) -> bool:
        viewport = self._simulation_viewport()
        session = self._service.current_project
        if viewport is None or not isinstance(session, ProjectSession):
            self.simulation_panel.set_result(result, None, current=True)
            return False
        generation = self._service.cam_generation
        request = viewport.request_simulation_display(
            inputs.operation.operation_id,
            generation=generation,
        )
        context = SimulationDisplayContext(
            project_id=session.manifest.project_id,
            project_generation=generation,
            operation_id=inputs.operation.operation_id,
            operation_revision=inputs.operation.revision,
            operation_exists=True,
            operation_enabled=inputs.operation.enabled,
            artifact_id=inputs.artifact.artifact_id,
            artifact_fingerprint=inputs.artifact.artifact_fingerprint,
            simulation_input_fingerprint=result.input_fingerprint,
            current_result_id=result.result_id,
            current_result_fingerprint=result.result_fingerprint,
        )
        self._service.simulation_runs.report_rendering(
            result.request_id,
            processed=0,
            total=1,
            callback=self._simulation_progress_changed,
        )
        displayed = viewport.display_simulation(
            result,
            inputs.artifact,
            inputs.setup.wcs,
            context,
            request,
            self.simulation_panel.display_policy,
        )
        self._service.simulation_runs.report_rendering(
            result.request_id,
            processed=1,
            total=1,
            callback=self._simulation_progress_changed,
        )
        presentation = self._simulation_presentation(inputs.operation.operation_id)
        current = (
            self._service.simulation_runs.result(inputs.operation.operation_id)
            is result
        )
        self.simulation_panel.set_result(
            result,
            presentation if displayed else None,
            current=current,
        )
        return displayed

    def _simulation_presentation(self, operation_id: OperationId):
        viewport = self._simulation_viewport()
        if viewport is None:
            return None
        return next(
            (
                value
                for value in viewport.simulation_presentations
                if value.key.operation_id == operation_id
            ),
            None,
        )

    def _simulation_viewport(self):
        return (
            self._toolpath_display.__self__
            if self._toolpath_display is not None
            and hasattr(self._toolpath_display, "__self__")
            else None
        )

    def _simulation_callback_is_current(
        self,
        handle: SimulationRunHandle,
        project_id: UUID | None,
        generation: int | None,
    ) -> bool:
        session = self._service.current_project
        if (
            not isinstance(session, ProjectSession)
            or project_id is None
            or session.manifest.project_id != project_id
            or generation != self._generation
            or generation != self._service.cam_generation
        ):
            return False
        record = self._service.simulation_runs.record(
            handle.request.operation_id
        )
        return record is not None and record.request_id == handle.request.request_id

    def create_job(self) -> None:
        if not self._service.has_project:
            return
        count = len(self._service.cam_snapshot.jobs) + 1
        snapshot = self._execute(lambda app: app.create_job(f"CAM Job {count}"))
        if snapshot:
            self.refresh(("job", str(snapshot.active_job_id)))

    def create_setup(self) -> None:
        snapshot = self._service.cam_snapshot if self._service.has_project else None
        source_id = self._source_provider()
        if snapshot is None or snapshot.active_job_id is None or source_id is None:
            self._error("Cần một công việc CAM và một nguồn CAD trong dự án trước khi tạo thiết lập.")
            return
        unit = _length_unit(self._service.current_project)
        setup = _default_setup(source_id, unit, len(_active_job(snapshot).setups) + 1)
        changed = self._execute(lambda app: app.add_setup(snapshot.active_job_id, setup))
        if changed:
            self.refresh(("setup", str(setup.setup_id)))

    def add_group(self) -> None:
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, tree, parent_id = context
        node_id = CamNodeId.new()
        changed = self._execute(lambda app: app.update_tree(job_id, setup_id,
            lambda value: value.add_group(parent_id, node_id, "Nhóm mới")))
        if changed:
            self.refresh(("group", str(node_id)))

    def create_basic_resources(self) -> None:
        """Create the minimal project-owned resource bundle without a global library."""
        if not self._service.has_project:
            return
        unit = _length_unit(self._service.current_project)
        mill_values = basic_mill_resources(unit)
        drill, center_drill, holder, drill_assembly, center_assembly = (
            basic_drilling_resources(unit)
        )
        def add_resources(app):
            app.add_basic_resources(*mill_values)
            app.add_tool_definition(drill)
            app.add_tool_definition(center_drill)
            app.add_holder_definition(holder)
            app.add_tool_assembly(drill_assembly)
            return app.add_tool_assembly(center_assembly)

        changed = self._execute(add_resources)
        if changed is not None:
            self.refresh(self._selected_key)

    def create_basic_parallel_resources(self) -> None:
        """Create a supported ball-end assembly for Parallel Finishing."""
        if not self._service.has_project:
            return
        unit = _length_unit(self._service.current_project)
        if unit is not LengthUnit.MM:
            self._error("Gia công tinh song song v1 hiện yêu cầu dự án dùng đơn vị mm.")
            return
        tool, holder, assembly, machine = basic_parallel_resources(unit)

        def add_resources(app):
            app.add_tool_definition(tool)
            app.add_holder_definition(holder)
            app.add_tool_assembly(assembly)
            return app.add_machine_definition(machine)

        changed = self._execute(add_resources)
        if changed is not None:
            self.refresh(self._selected_key)

    def create_basic_tapping_resources(self) -> None:
        """Create the project-owned RH/LH TAP bundle explicitly."""
        if not self._service.has_project:
            return
        unit = _length_unit(self._service.current_project)
        (
            right_tap,
            left_tap,
            tapping_holder,
            right_tap_assembly,
            left_tap_assembly,
            tapping_machine,
        ) = basic_tapping_resources(unit)

        def add_resources(app):
            app.add_tool_definition(right_tap)
            app.add_tool_definition(left_tap)
            app.add_holder_definition(tapping_holder)
            app.add_tool_assembly(right_tap_assembly)
            app.add_tool_assembly(left_tap_assembly)
            return app.add_machine_definition(tapping_machine)

        changed = self._execute(add_resources)
        if changed is not None:
            self.refresh(self._selected_key)

    def create_basic_reaming_resources(self) -> None:
        """Create one project-owned REAMER and compatible milling machine."""
        if not self._service.has_project:
            return
        unit = _length_unit(self._service.current_project)
        reamer, holder, assembly, machine = basic_reaming_resources(unit)

        def add_resources(app):
            app.add_tool_definition(reamer)
            app.add_holder_definition(holder)
            app.add_tool_assembly(assembly)
            return app.add_machine_definition(machine)

        changed = self._execute(add_resources)
        if changed is not None:
            self.refresh(self._selected_key)

    def create_basic_boring_resources(self) -> None:
        """Create one project-owned BORING_BAR and compatible milling machine."""
        if not self._service.has_project:
            return
        unit = _length_unit(self._service.current_project)
        tool, holder, assembly, machine = basic_boring_resources(unit)

        def add_resources(app):
            app.add_tool_definition(tool)
            app.add_holder_definition(holder)
            app.add_tool_assembly(assembly)
            return app.add_machine_definition(machine)

        changed = self._execute(add_resources)
        if changed is not None:
            self.refresh(self._selected_key)

    def pick_geometry(self) -> None:
        """Explicitly bind the current unambiguous CAD selection."""
        operation = self._selected_operation()
        is_hole_operation = (
            operation is not None
            and operation.strategy_key in {
                "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
            }
        )
        provider = (
            self._drilling_pick_provider if is_hole_operation
            else self._contour_pick_provider if operation is not None and
            operation.strategy_key in {"contour_2d", "pocket_2_5d"}
            else self._pick_provider
        )
        if provider is None:
            self._error("Bộ tiếp hợp chọn hình học chưa sẵn sàng.")
            return
        generation = self._generation
        try:
            if is_hole_operation:
                item = self.tree.currentItem()
                setup = self._find_setup(item, self._find_job(item))
                if setup is None:
                    raise ValueError("Hole operation chưa có Setup hợp lệ.")
                hole_source = provider(setup.wcs.z_axis)
                if not isinstance(hole_source, (HoleReference, HolePattern)):
                    raise TypeError("Hole picker returned an invalid source")
                references = _hole_references(hole_source)
                reference = (
                    references[0].reference if len(references) == 1 else None
                )
            else:
                hole_source = None
                reference = provider()
        except Exception as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("Phiên chọn hình học đã bị hủy vì dự án đã thay đổi.")
            return
        previous = self._picked_reference
        previous_hole = self._picked_hole_reference
        previous_hole_source = self._picked_hole_source
        previous_status = self._picked_reference_resolved
        if is_hole_operation:
            self._set_picked_hole_source(hole_source)
        else:
            self._picked_reference = reference
        self._picked_reference_resolved = self._resolve_picked_reference()
        if not self._picked_reference_resolved:
            self._picked_reference = previous
            self._picked_hole_reference = previous_hole
            self._picked_hole_source = previous_hole_source
            self._picked_reference_resolved = previous_status
            self._error(
                self._geometry_resolution_error
                or "Persistent geometry could not be resolved unambiguously."
            )
            return
        if is_hole_operation:
            self.editor.show_hole_source(self._picked_hole_source, True)
        else:
            self.editor.show_reference(self._picked_reference, True)
        self._stash_active_operation_draft()
        self._update_generate_action()
        self.message.emit("Đã tạo GeometryReference; dùng Rebind để thay thế rõ ràng.")

    def clear_geometry_pick(self) -> None:
        """Clear an explicit binding; never choose a replacement automatically."""
        operation = self._selected_operation()
        if (operation is not None
                and operation.strategy_key in {
                    "contour_2d", "drilling_v1", "pocket_2_5d", "tapping_v1",
                    "reaming_v1", "boring_v1",
                }
                and operation.geometry_inputs):
            item = self.tree.currentItem()
            job = self._find_job(item)
            setup = self._find_setup(item, job)
            if item is not None and job is not None and setup is not None:
                changed = replace(operation, geometry_inputs=(), revision=operation.revision.next(),
                                  artifact_state=operation.artifact_state.mark_dirty(
                                      DirtyReason.GEOMETRY_CHANGED))
                updated = self._execute(lambda app: app.update_tree(job.job_id, setup.setup_id,
                    lambda tree: tree.replace_operation(changed)))
                if updated is None:
                    return
                self.refresh(self._selected_key)
        self._picked_reference = None
        self._picked_hole_reference = None
        self._picked_hole_source = None
        self._picked_reference_resolved = False
        self._geometry_resolution_error = ""
        if operation is not None and operation.strategy_key in {
            "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
        }:
            self.editor.show_hole_source(None)
        else:
            self.editor.show_reference(None)
        self._stash_active_operation_draft()
        self._update_generate_action()

    def cad_context_changed(self, *, force_invalidate: bool = False) -> None:
        """Re-resolve displayed references after CAD reload without rebinding them."""
        self._picked_reference_resolved = self._resolve_picked_reference()
        invalidated = False
        if not self._service.has_project:
            self._update_generate_action()
            return
        if force_invalidate:
            self._service.simulation_runs.cancel_all(stale=True)
            viewport = self._simulation_viewport()
            if viewport is not None:
                viewport.clear_simulations()
            self.simulation_panel.mark_result_stale(
                "STALE / NON-CURRENT · CAD source reimported"
            )
        if (
            self._face_resolver is not None
            or self._profile_resolver is not None
            or self._drilling_resolver is not None
            or self._parallel_adapter_provider is not None
        ) and self._generation is not None:
            for job in self._service.cam_snapshot.jobs:
                for setup in job.setups:
                    for operation in setup.operation_tree.operations:
                        if operation.artifact_state.status is not ArtifactStatus.VALID:
                            continue
                        if operation.strategy_key == "parallel_finishing_3d":
                            if force_invalidate:
                                invalidated = self._execute(
                                    lambda app, operation_id=operation.operation_id:
                                    app.invalidate_operation(
                                        operation_id, DirtyReason.GEOMETRY_CHANGED,
                                    )
                                ) is not None or invalidated
                            continue
                        if (
                            operation.strategy_key not in {
                                "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                            }
                            and len(operation.geometry_inputs) != 1
                        ):
                            continue
                        resolver = None
                        if operation.strategy_key == "facing_2_5d":
                            try:
                                parameters = FacingParameters.from_operation_parameters(operation.parameters)
                            except (RuntimeError, TypeError, ValueError):
                                continue
                            if parameters.boundary_source is FacingBoundarySource.PLANAR_FACE:
                                resolver = self._face_resolver
                        elif operation.strategy_key in {"contour_2d", "pocket_2_5d"}:
                            resolver = self._profile_resolver
                        elif operation.strategy_key in {
                            "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                        }:
                            try:
                                hole_strategy = _hole_strategy(operation)
                            except (RuntimeError, TypeError, ValueError):
                                continue
                            resolver = self._drilling_resolver
                        if resolver is None:
                            continue
                        if force_invalidate:
                            invalidated = self._execute(
                                lambda app, operation_id=operation.operation_id:
                                app.invalidate_operation(
                                    operation_id, DirtyReason.GEOMETRY_CHANGED,
                                )
                            ) is not None or invalidated
                            continue
                        try:
                            result = (
                                resolver(hole_strategy.geometry, hole_strategy.depth)
                                if operation.strategy_key in {
                                    "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                                }
                                else resolver(operation.geometry_inputs[0].reference)
                            )
                        except (RuntimeError, TypeError, ValueError):
                            invalidated = self._execute(
                                lambda app, operation_id=operation.operation_id:
                                app.invalidate_operation(
                                    operation_id, DirtyReason.GEOMETRY_CHANGED,
                                )
                            ) is not None or invalidated
                            continue
                        if getattr(result, "status", None) is not GeometryResolutionStatus.RESOLVED:
                            invalidated = self._execute(
                                lambda app, operation_id=operation.operation_id:
                                app.invalidate_operation(
                                    operation_id, DirtyReason.GEOMETRY_CHANGED,
                                )
                            ) is not None or invalidated
        if invalidated:
            self.refresh(self._selected_key)
        self._update_generate_action()

    def _resolve_picked_reference(self) -> bool:
        self._geometry_resolution_error = ""
        if self._picked_hole_source is not None:
            if self._drilling_resolver is None:
                return True
            operation = self._selected_operation()
            if operation is None or operation.strategy_key not in {
                "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
            }:
                return False
            try:
                current = _hole_strategy(operation)
                geometry = DrillGeometryInput(
                    self._picked_hole_source,
                    self._picked_hole_source.unit,
                )
                result = self._drilling_resolver(geometry, current.depth)
            except (RuntimeError, TypeError, ValueError) as error:
                if operation.strategy_key == "boring_v1":
                    self._geometry_resolution_error = (
                        f"bore.geometry_missing: {error}"
                    )
                return False
            status = getattr(result, "status", None)
            if status is GeometryResolutionStatus.RESOLVED:
                return True
            if operation.strategy_key == "boring_v1":
                code = {
                    GeometryResolutionStatus.MISSING: "bore.geometry_missing",
                    GeometryResolutionStatus.STALE: "bore.geometry_stale",
                    GeometryResolutionStatus.TOPOLOGY_CHANGED: "bore.geometry_stale",
                    GeometryResolutionStatus.AMBIGUOUS: "bore.geometry_ambiguous",
                    GeometryResolutionStatus.SOURCE_MISMATCH: "bore.source_mismatch",
                }.get(status, "bore.invalid_parameters")
                diagnostics = getattr(result, "diagnostics", ())
                message = (
                    diagnostics[0].message
                    if diagnostics else "Boring geometry resolve thất bại."
                )
                self._geometry_resolution_error = f"{code}: {message}"
            return False
        if self._picked_reference is None:
            return False
        is_contour = (self._picked_reference.subshape_selector or "").startswith("hms_profile_v1:")
        resolver = self._profile_resolver if is_contour else self._face_resolver
        if resolver is None:
            return True
        try:
            result = resolver(self._picked_reference)
        except (RuntimeError, TypeError, ValueError):
            return False
        return getattr(result, "status", None) is GeometryResolutionStatus.RESOLVED

    def _selected_operation(self) -> Operation | None:
        item = self.tree.currentItem()
        if item is None or item.data(0, _KIND_ROLE) != "operation":
            return None
        setup = self._find_setup(item, self._find_job(item))
        if setup is None:
            return None
        node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE)))
        return setup.operation_tree.get_operation(node.operation_id) if node.operation_id else None

    def _set_picked_hole_source(
        self,
        source: HoleReference | HolePattern | None,
    ) -> None:
        """Set one native-free hole source and its single-reference compatibility view."""
        self._picked_hole_source = source
        self._picked_hole_reference = (
            source if isinstance(source, HoleReference) else None
        )
        references = () if source is None else _hole_references(source)
        self._picked_reference = (
            references[0].reference if len(references) == 1 else None
        )

    def _stash_active_operation_draft(self) -> None:
        """Keep unapplied operation drafts by stable domain identity only."""
        operation_id = self._active_editor_operation_id
        if operation_id is None:
            return
        if self._active_editor_strategy_key == "pocket_2_5d":
            self._pocket_drafts[operation_id] = (
                self.editor.pocket_state(),
                self._picked_reference,
            )
        elif self._active_editor_strategy_key == "drilling_v1":
            self._drilling_drafts[operation_id] = (
                self.editor.drilling_state(),
                self._picked_hole_source,
            )
        elif self._active_editor_strategy_key == "tapping_v1":
            self._tapping_drafts[operation_id] = (
                self.editor.tapping_state(),
                self._picked_hole_source,
            )
        elif self._active_editor_strategy_key == "reaming_v1":
            self._reaming_drafts[operation_id] = (
                self.editor.reaming_state(),
                self._picked_hole_source,
            )
        elif self._active_editor_strategy_key == "boring_v1":
            self._boring_drafts[operation_id] = (
                self.editor.boring_state(),
                self._picked_hole_source,
            )

    def add_operation(self) -> None:
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, _tree, parent_id = context
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        snapshot = self._service.cam_snapshot
        assembly = snapshot.tool_assemblies[0] if snapshot.tool_assemblies else None
        tool_reference = ToolAssemblyReference.from_assembly(assembly) if assembly else ToolAssemblyReference(
            ToolAssemblyId.new(), Revision(0), ContentFingerprint.from_payload({"missing": True}), _length_unit(self._service.current_project))
        machine = snapshot.machine_definitions[0] if snapshot.machine_definitions else None
        machine_requirement = None if machine is None else MachineRequirement(
            machine.machine_id, machine.revision, ContentFingerprint.from_payload(machine.to_dict()),
            machine.unit, (OperationCapability.MILLING,))
        setup = next(value for job in snapshot.jobs for value in job.setups if value.setup_id == setup_id)
        parameters = _default_facing_parameters(setup)
        operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup_id,
                              tool_reference, (), parameters.to_operation_parameters(), machine_requirement)
        changed = self._execute(lambda app: app.update_tree(job_id, setup_id,
            lambda value: value.add_operation(parent_id, "Facing 2.5D", operation)))
        if changed:
            self._present_created_operation(node_id)

    def add_contour_operation(self) -> None:
        """Add one editable 2D Contour operation without guessing its profile."""
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, _tree, parent_id = context
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        snapshot = self._service.cam_snapshot
        assembly = snapshot.tool_assemblies[0] if snapshot.tool_assemblies else None
        tool_reference = ToolAssemblyReference.from_assembly(assembly) if assembly else ToolAssemblyReference(
            ToolAssemblyId.new(), Revision(0), ContentFingerprint.from_payload({"missing": True}),
            _length_unit(self._service.current_project))
        machine = snapshot.machine_definitions[0] if snapshot.machine_definitions else None
        machine_requirement = None if machine is None else MachineRequirement(
            machine.machine_id, machine.revision, ContentFingerprint.from_payload(machine.to_dict()),
            machine.unit, (OperationCapability.MILLING,))
        setup = next(value for job in snapshot.jobs for value in job.setups if value.setup_id == setup_id)
        parameters = _default_contour_parameters(setup)
        operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup_id,
                              tool_reference, (), parameters.to_operation_parameters(), machine_requirement)
        changed = self._execute(lambda app: app.update_tree(job_id, setup_id,
            lambda value: value.add_operation(parent_id, "2D Contour", operation)))
        if changed:
            self._present_created_operation(node_id)

    def add_pocket_operation(self) -> None:
        """Add one unbound Pocket operation without guessing its boundary."""
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, _tree, parent_id = context
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        snapshot = self._service.cam_snapshot
        assembly = snapshot.tool_assemblies[0] if snapshot.tool_assemblies else None
        tool_reference = ToolAssemblyReference.from_assembly(assembly) if assembly else ToolAssemblyReference(
            ToolAssemblyId.new(), Revision(0), ContentFingerprint.from_payload({"missing": True}),
            _length_unit(self._service.current_project))
        machine = snapshot.machine_definitions[0] if snapshot.machine_definitions else None
        machine_requirement = None if machine is None else MachineRequirement(
            machine.machine_id, machine.revision, ContentFingerprint.from_payload(machine.to_dict()),
            machine.unit, (OperationCapability.MILLING,))
        setup = next(value for job in snapshot.jobs for value in job.setups
                     if value.setup_id == setup_id)
        operation = Operation(
            operation_id, node_id, OperationFamily.MILLING, setup_id,
            tool_reference, (), _default_pocket_parameters(setup), machine_requirement,
        )
        changed = self._execute(lambda app: app.update_tree(
            job_id, setup_id,
            lambda value: value.add_operation(parent_id, "Pocket 2.5D", operation),
        ))
        if changed:
            self._present_created_operation(node_id)

    def add_parallel_operation(self) -> None:
        """Add an unbound Parallel operation; geometry is selected in its editor."""
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value
            for job in snapshot.jobs
            for value in job.setups
            if value.setup_id == setup_id
        )
        if setup.wcs.origin.unit is not LengthUnit.MM:
            self._error("Gia công tinh song song v1 hiện yêu cầu thiết lập dùng đơn vị mm.")
            return
        assembly = next(
            (
                item
                for item in snapshot.tool_assemblies
                if any(
                    tool.tool_id == item.tool_id
                    and tool.family is ToolFamily.BALL_END_MILL
                    for tool in snapshot.tool_definitions
                )
            ),
            None,
        )
        if assembly is None:
            assembly = snapshot.tool_assemblies[0] if snapshot.tool_assemblies else None
        tool_reference = (
            ToolAssemblyReference.from_assembly(assembly)
            if assembly is not None
            else ToolAssemblyReference(
                ToolAssemblyId.new(),
                Revision(0),
                ContentFingerprint.from_payload({"missing": True}),
                LengthUnit.MM,
            )
        )
        machine = next(
            (
                item
                for item in snapshot.machine_definitions
                if OperationCapability.MILLING in item.capabilities.operations
            ),
            None,
        )
        requirement = (
            None
            if machine is None
            else MachineRequirement(
                machine.machine_id,
                machine.revision,
                machine.content_fingerprint,
                machine.unit,
                (OperationCapability.MILLING,),
            )
        )
        top = setup.wcs.origin.z
        parameters = ParallelFinishingParameters(
            MachiningZone3DId.new(),
            1.0,
            clearance_z_mm=top + 50.0,
            retract_z_mm=top + 40.0,
            link_clearance_mm=1.0,
        )
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        operation = Operation(
            operation_id,
            node_id,
            OperationFamily.MILLING,
            setup_id,
            tool_reference,
            (),
            parameters.to_operation_parameters(),
            requirement,
        )
        changed = self._execute(
            lambda app: app.update_tree(
                job_id,
                setup_id,
                lambda value: value.add_operation(
                    parent_id, "Parallel Finishing", operation
                ),
            )
        )
        if changed is not None:
            self._present_created_operation(node_id)
            self.projection_changed.emit()

    def add_z_level_operation(self) -> None:
        """Add an unbound Z-Level operation; geometry is chosen in its editor."""
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value
            for job in snapshot.jobs
            for value in job.setups
            if value.setup_id == setup_id
        )
        if setup.wcs.origin.unit is not LengthUnit.MM:
            self._error(
                "Gia công tinh theo cao độ Z v1 yêu cầu Thiết lập dùng đơn vị mm."
            )
            return
        assembly = next(
            (
                item
                for item in snapshot.tool_assemblies
                if any(
                    tool.tool_id == item.tool_id
                    and tool.family is ToolFamily.BALL_END_MILL
                    for tool in snapshot.tool_definitions
                )
            ),
            None,
        )
        if assembly is None:
            assembly = (
                snapshot.tool_assemblies[0]
                if snapshot.tool_assemblies
                else None
            )
        tool_reference = (
            ToolAssemblyReference.from_assembly(assembly)
            if assembly is not None
            else ToolAssemblyReference(
                ToolAssemblyId.new(),
                Revision(0),
                ContentFingerprint.from_payload({"missing": True}),
                LengthUnit.MM,
            )
        )
        machine = next(
            (
                item
                for item in snapshot.machine_definitions
                if item.unit is LengthUnit.MM
                and OperationCapability.MILLING in item.capabilities.operations
            ),
            None,
        )
        requirement = (
            None
            if machine is None
            else MachineRequirement(
                machine.machine_id,
                machine.revision,
                machine.content_fingerprint,
                machine.unit,
                (OperationCapability.MILLING,),
            )
        )
        origin_z = setup.wcs.origin.z
        parameters = ZLevelFinishingParameters(
            MachiningZone3DId.new(),
            origin_z + 1.0,
            origin_z,
            1.0,
            clearance_z_mm=origin_z + 50.0,
            retract_z_mm=origin_z + 40.0,
            link_clearance_mm=1.0,
            setup_reference=str(setup.setup_id),
        )
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        operation = Operation(
            operation_id,
            node_id,
            OperationFamily.MILLING,
            setup_id,
            tool_reference,
            (),
            parameters.to_operation_parameters(),
            requirement,
        )
        changed = self._execute(
            lambda app: app.update_tree(
                job_id,
                setup_id,
                lambda value: value.add_operation(
                    parent_id,
                    "Gia công tinh theo cao độ Z",
                    operation,
                ),
            )
        )
        if changed is not None:
            self._present_created_operation(node_id)
            self.projection_changed.emit()

    def add_drilling_operation(self) -> None:
        """Add one explicitly bound Drilling operation without hole recognition."""
        context = self._tree_context()
        if context is None:
            return
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            self._error("Bộ tiếp hợp hình học Khoan chưa sẵn sàng.")
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value for job in snapshot.jobs for value in job.setups
            if value.setup_id == setup_id
        )
        generation = self._generation
        try:
            hole_source = self._drilling_pick_provider(setup.wcs.z_axis)
            if not isinstance(hole_source, (HoleReference, HolePattern)):
                raise TypeError("Drilling picker returned an invalid hole source")
            strategy = _default_drilling_strategy(setup, hole_source)
            resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
            if resolved.status is not GeometryResolutionStatus.RESOLVED:
                message = (
                    resolved.diagnostics[0].message
                    if resolved.diagnostics else "Drilling geometry is invalid"
                )
                raise ValueError(message)
            assembly = _find_drilling_assembly(snapshot, ToolFamily.DRILL)
            machine = next((
                value for value in snapshot.machine_definitions
                if OperationCapability.DRILLING in value.capabilities.operations
            ), None)
            if assembly is None or machine is None:
                raise ValueError(
                    "Hãy tạo Tool/Machine cơ bản trước khi thêm Drilling."
                )
        except (RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("Phiên tạo Khoan đã lỗi thời vì dự án thay đổi.")
            return
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        requirement = MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        )
        operation = Operation(
            operation_id, node_id, OperationFamily.DRILLING, setup_id,
            ToolAssemblyReference.from_assembly(assembly),
            _hole_geometry_inputs((), hole_source),
            strategy.to_operation_parameters(), requirement,
        )
        changed = self._execute(lambda app: app.update_tree(
            job_id, setup_id,
            lambda value: value.add_operation(parent_id, "Drilling", operation),
        ))
        if changed:
            self._present_created_operation(node_id)

    def add_tapping_operation(self) -> None:
        """Add one validated Tapping operation bound to an explicit hole source."""
        context = self._tree_context()
        if context is None:
            return
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            self._error("tap.geometry_missing: Bộ tiếp hợp hình học Ta rô chưa sẵn sàng.")
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value for job in snapshot.jobs for value in job.setups
            if value.setup_id == setup_id
        )
        generation = self._generation
        try:
            hole_source = self._drilling_pick_provider(setup.wcs.z_axis)
            if not isinstance(hole_source, (HoleReference, HolePattern)):
                raise ValueError("tap.geometry_missing: Hole source không hợp lệ.")
            strategy = _default_tapping_strategy(setup, hole_source)
            resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
            assembly = _find_drilling_assembly(snapshot, ToolFamily.TAP)
            if assembly is None:
                raise ValueError("tap.tool_missing: Chưa có cụm Tool TAP.")
            tool = next((
                value for value in snapshot.tool_definitions
                if value.tool_id == assembly.tool_id
            ), None)
            machine = next((
                value for value in snapshot.machine_definitions
                if OperationCapability.TAPPING in value.capabilities.operations
            ), None)
            if machine is None:
                raise ValueError(
                    "tap.machine_incompatible: Chưa có máy hỗ trợ Tapping."
                )
            node_id, operation_id = CamNodeId.new(), OperationId.new()
            requirement = MachineRequirement(
                machine.machine_id,
                machine.revision,
                machine.content_fingerprint,
                machine.unit,
                (OperationCapability.TAPPING,),
            )
            operation = Operation(
                operation_id,
                node_id,
                OperationFamily.DRILLING,
                setup_id,
                ToolAssemblyReference.from_assembly(assembly),
                _hole_geometry_inputs((), hole_source),
                strategy.to_operation_parameters(),
                requirement,
            )
            TappingGenerator().resolve_inputs(
                operation,
                setup,
                assembly=assembly,
                tool=tool,
                machine=machine,
                resolved_geometry=resolved,
            )
        except TappingGenerationError as error:
            self._error(f"{error.code.value}: {error}")
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("tap.stale_result: Phiên tạo Ta rô đã lỗi thời.")
            return
        changed = self._execute(lambda app: app.update_tree(
            job_id,
            setup_id,
            lambda value: value.add_operation(parent_id, "Tapping", operation),
        ))
        if changed:
            self._present_created_operation(node_id)

    def add_reaming_operation(self) -> None:
        """Add one validated Reaming operation with an explicit pre-hole value."""
        context = self._tree_context()
        if context is None:
            return
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            self._error("ream.geometry_missing: Bộ tiếp hợp hình học Doa chưa sẵn sàng.")
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value for job in snapshot.jobs for value in job.setups
            if value.setup_id == setup_id
        )
        generation = self._generation
        try:
            hole_source = self._drilling_pick_provider(setup.wcs.z_axis)
            if not isinstance(hole_source, (HoleReference, HolePattern)):
                raise ValueError("ream.geometry_missing: Hole source không hợp lệ.")
            strategy = _default_reaming_strategy(setup, hole_source)
            resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
            assembly = _find_drilling_assembly(snapshot, ToolFamily.REAMER)
            if assembly is None:
                raise ValueError("ream.tool_missing: Chưa có cụm Tool REAMER.")
            tool = next((
                value for value in snapshot.tool_definitions
                if value.tool_id == assembly.tool_id
            ), None)
            machine = next((
                value for value in snapshot.machine_definitions
                if OperationCapability.DRILLING in value.capabilities.operations
                and any(
                    spindle.minimum_speed.value
                    <= strategy.spindle_speed.value
                    <= spindle.maximum_speed.value
                    and strategy.spindle_direction in spindle.directions
                    for spindle in value.spindles
                )
            ), None)
            if machine is None:
                raise ValueError(
                    "ream.machine_incompatible: Chưa có máy phay hỗ trợ drilling."
                )
            node_id, operation_id = CamNodeId.new(), OperationId.new()
            requirement = MachineRequirement(
                machine.machine_id,
                machine.revision,
                machine.content_fingerprint,
                machine.unit,
                (OperationCapability.DRILLING,),
            )
            operation = Operation(
                operation_id,
                node_id,
                OperationFamily.DRILLING,
                setup_id,
                ToolAssemblyReference.from_assembly(assembly),
                _hole_geometry_inputs((), hole_source),
                strategy.to_operation_parameters(),
                requirement,
            )
            ReamingGenerator().resolve_inputs(
                operation,
                setup,
                assembly=assembly,
                tool=tool,
                machine=machine,
                resolved_geometry=resolved,
            )
        except ReamingGenerationError as error:
            self._error(f"{error.code.value}: {error}")
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("ream.stale_result: Phiên tạo Doa đã lỗi thời.")
            return
        changed = self._execute(lambda app: app.update_tree(
            job_id,
            setup_id,
            lambda value: value.add_operation(parent_id, "Reaming", operation),
        ))
        if changed:
            self._present_created_operation(node_id)

    def add_boring_operation(self) -> None:
        """Add one validated Boring operation with an explicit pre-bore value."""
        context = self._tree_context()
        if context is None:
            return
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            self._error("bore.geometry_missing: Bộ tiếp hợp hình học Tiện lỗ chưa sẵn sàng.")
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value for job in snapshot.jobs for value in job.setups
            if value.setup_id == setup_id
        )
        generation = self._generation
        try:
            hole_source = self._drilling_pick_provider(setup.wcs.z_axis)
            if not isinstance(hole_source, (HoleReference, HolePattern)):
                raise ValueError("bore.geometry_missing: Hole source không hợp lệ.")
            strategy = _default_boring_strategy(setup, hole_source)
            resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
            assembly = _find_drilling_assembly(snapshot, ToolFamily.BORING_BAR)
            if assembly is None:
                raise ValueError("bore.tool_missing: Chưa có cụm Tool BORING_BAR.")
            tool = next((
                value for value in snapshot.tool_definitions
                if value.tool_id == assembly.tool_id
            ), None)
            holder = next((
                value for value in snapshot.holder_definitions
                if value.holder_id == assembly.holder_id
            ), None)
            machine = next((
                value for value in snapshot.machine_definitions
                if value.kind in {MachineKind.MILL, MachineKind.MILL_TURN}
                and OperationCapability.DRILLING in value.capabilities.operations
                and any(
                    spindle.minimum_speed.value
                    <= strategy.spindle_rpm.value
                    <= spindle.maximum_speed.value
                    and strategy.spindle_direction in spindle.directions
                    for spindle in value.spindles
                )
            ), None)
            if machine is None:
                raise ValueError(
                    "bore.machine_incompatible: Chưa có máy phay hỗ trợ drilling."
                )
            node_id, operation_id = CamNodeId.new(), OperationId.new()
            requirement = MachineRequirement(
                machine.machine_id,
                machine.revision,
                machine.content_fingerprint,
                machine.unit,
                (OperationCapability.DRILLING,),
            )
            operation = Operation(
                operation_id,
                node_id,
                OperationFamily.DRILLING,
                setup_id,
                ToolAssemblyReference.from_assembly(assembly),
                _hole_geometry_inputs((), hole_source),
                strategy.to_operation_parameters(),
                requirement,
            )
            BoringGenerator().resolve_inputs(
                operation,
                setup,
                assembly=assembly,
                tool=tool,
                holder=holder,
                machine=machine,
                resolved_geometry=resolved,
            )
        except BoringGenerationError as error:
            self._error(f"{error.code.value}: {error}")
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("bore.stale_result: Phiên tạo Tiện lỗ đã lỗi thời.")
            return
        changed = self._execute(lambda app: app.update_tree(
            job_id,
            setup_id,
            lambda value: value.add_operation(parent_id, "Boring", operation),
        ))
        if changed:
            self._present_created_operation(node_id)

    def _present_created_operation(self, node_id: CamNodeId) -> None:
        """Select and announce a newly persisted operation to the UI shell."""
        self.refresh(("operation", str(node_id)))
        self.operation_created.emit(str(node_id))

    def generate_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.data(0, _KIND_ROLE) != "operation" or self._generation is None:
            self._error("Hãy chọn một operation CAM trước khi Generate.")
            return
        setup = self._find_setup(item, self._find_job(item))
        node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE))) if setup else None
        operation = setup.operation_tree.get_operation(node.operation_id) if setup and node else None
        if operation is None or operation.strategy_key not in {
            "facing_2_5d", "contour_2d", "drilling_v1", "pocket_2_5d",
            "tapping_v1", "reaming_v1", "boring_v1", "parallel_finishing_3d",
            "z_level_finishing_3d",
        }:
            self._error("Operation đã chọn không hỗ trợ Generate.")
            return
        if operation.strategy_key == "parallel_finishing_3d":
            session = self.production_function_editor_session()
            if session is None:
                self._error("Trình sửa Gia công tinh song song cho sản xuất không khả dụng.")
                return
            try:
                session.calculate_callback(session.applied_mapping())
            except (KeyError, RuntimeError, TypeError, ValueError) as error:
                self._error(str(error))
            return
        if operation.strategy_key == "z_level_finishing_3d":
            session = self.production_function_editor_session()
            if session is None:
                self._error(
                    "Trình chỉnh sửa Gia công tinh theo cao độ Z không khả dụng."
                )
                return
            try:
                session.calculate_callback(session.applied_mapping())
            except (KeyError, RuntimeError, TypeError, ValueError) as error:
                self._error(str(error))
            return
        if operation.strategy_key == "boring_v1":
            generation = self._generation
            draft = self.editor.boring_draft(
                setup.wcs.origin.unit,
                self._picked_hole_source,
            )
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            if (
                draft is None
                or draft.to_operation_parameters() != operation.parameters
                or self.editor.tool.currentData()
                != str(operation.tool_assembly.assembly_id)
                or self.editor.machine.currentData()
                != (str(machine_id) if machine_id else None)
                or self._picked_hole_source is None
                or not _operation_matches_hole_source(
                    operation, self._picked_hole_source,
                )
                or not self._picked_reference_resolved
                or not operation.enabled
            ):
                self._error(
                    "bore.invalid_parameters: Bản nháp Tiện lỗ chưa hợp lệ "
                    "hoặc chưa được áp dụng."
                )
                return
            result = self._service.compute_boring(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._drilling_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (
                generation != self._generation
                or generation != service_generation
                or current is None
                or current.operation_id != operation.operation_id
            ):
                self._error(
                    "bore.stale_result: Kết quả Tiện lỗ đã lỗi thời và không "
                    "được hiển thị."
                )
                return
            if result.accepted and result.artifact is not None:
                self.message.emit(
                    "Tiện lỗ đã tạo và công bố kết quả hợp lệ."
                )
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (
                    f"{result.diagnostics[0].code.value}: "
                    f"{result.diagnostics[0].message}"
                    if result.diagnostics
                    else "bore.generation_failed: Boring generation thất bại."
                )
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "reaming_v1":
            generation = self._generation
            draft = self.editor.reaming_draft(
                setup.wcs.origin.unit,
                self._picked_hole_source,
            )
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            if (
                draft is None
                or draft.to_operation_parameters() != operation.parameters
                or self.editor.tool.currentData()
                != str(operation.tool_assembly.assembly_id)
                or self.editor.machine.currentData()
                != (str(machine_id) if machine_id else None)
                or self._picked_hole_source is None
                or not _operation_matches_hole_source(
                    operation, self._picked_hole_source,
                )
                or not self._picked_reference_resolved
                or not operation.enabled
            ):
                self._error(
                    "ream.invalid_parameters: Bản nháp Doa chưa hợp lệ "
                    "hoặc chưa được áp dụng."
                )
                return
            result = self._service.compute_reaming(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._drilling_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (
                generation != self._generation
                or generation != service_generation
                or current is None
                or current.operation_id != operation.operation_id
            ):
                self._error(
                    "ream.stale_result: Kết quả Doa đã lỗi thời và không "
                    "được hiển thị."
                )
                return
            if result.accepted and result.artifact is not None:
                self.message.emit(
                    "Doa đã tạo và công bố kết quả hợp lệ."
                )
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (
                    f"{result.diagnostics[0].code.value}: "
                    f"{result.diagnostics[0].message}"
                    if result.diagnostics
                    else "ream.generation_failed: Reaming generation thất bại."
                )
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "tapping_v1":
            generation = self._generation
            draft = self.editor.tapping_draft(
                setup.wcs.origin.unit,
                self._picked_hole_source,
            )
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            if (
                draft is None
                or draft.to_operation_parameters() != operation.parameters
                or self.editor.tool.currentData()
                != str(operation.tool_assembly.assembly_id)
                or self.editor.machine.currentData()
                != (str(machine_id) if machine_id else None)
                or self._picked_hole_source is None
                or not _operation_matches_hole_source(
                    operation, self._picked_hole_source,
                )
                or not self._picked_reference_resolved
                or not operation.enabled
            ):
                self._error(
                    "tap.invalid_parameters: Bản nháp Ta rô chưa hợp lệ "
                    "hoặc chưa được áp dụng."
                )
                return
            result = self._service.compute_tapping(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._drilling_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (
                generation != self._generation
                or generation != service_generation
                or current is None
                or current.operation_id != operation.operation_id
            ):
                self._error(
                    "tap.stale_result: Kết quả Ta rô đã lỗi thời và không "
                    "được hiển thị."
                )
                return
            if result.accepted and result.artifact is not None:
                self.message.emit(
                    "Ta rô đã tạo và công bố kết quả hợp lệ."
                )
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (
                    f"{result.diagnostics[0].code.value}: "
                    f"{result.diagnostics[0].message}"
                    if result.diagnostics
                    else "tap.generation_failed: Tapping generation thất bại."
                )
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "drilling_v1":
            generation = self._generation
            draft = self.editor.drilling_draft(
                setup.wcs.origin.unit, self._picked_hole_source,
            )
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            if (
                draft is None
                or draft.to_operation_parameters() != operation.parameters
                or self.editor.tool.currentData()
                != str(operation.tool_assembly.assembly_id)
                or self.editor.machine.currentData()
                != (str(machine_id) if machine_id else None)
                or self._picked_hole_source is None
                or not _operation_matches_hole_source(
                    operation, self._picked_hole_source,
                )
                or not self._picked_reference_resolved
                or not operation.enabled
            ):
                self._error("Bản nháp Khoan chưa hợp lệ hoặc chưa được áp dụng.")
                return
            result = self._service.compute_drilling(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._drilling_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (
                generation != self._generation
                or generation != service_generation
                or current is None
                or current.operation_id != operation.operation_id
            ):
                self._error("Kết quả Khoan đã lỗi thời và không được hiển thị.")
                return
            if result.accepted and result.artifact is not None:
                self.message.emit("Khoan đã tạo và công bố kết quả hợp lệ.")
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (
                    result.diagnostics[0].message
                    if result.diagnostics else "Drilling generation thất bại."
                )
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "pocket_2_5d":
            generation = self._generation
            draft = self.editor.pocket_draft(setup.wcs.origin.unit, self._picked_reference)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            if (draft is None or draft.to_operation_parameters() != operation.parameters or
                    self.editor.tool.currentData() != str(operation.tool_assembly.assembly_id) or
                    self.editor.machine.currentData() != (str(machine_id) if machine_id else None) or
                    len(operation.geometry_inputs) != 1 or
                    self._picked_reference != operation.geometry_inputs[0].reference or
                    not self._picked_reference_resolved or not operation.enabled):
                self._error("Bản nháp Hốc chưa hợp lệ hoặc chưa được áp dụng.")
                return
            result = self._service.compute_pocket(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._pocket_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (generation != self._generation or generation != service_generation
                    or current is None or current.operation_id != operation.operation_id):
                self._error("Kết quả Hốc đã lỗi thời và không được hiển thị.")
                return
            if result.accepted and result.artifact is not None:
                self.message.emit("Hốc 2.5D đã tạo và công bố kết quả hợp lệ.")
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (result.diagnostics[0].message if result.diagnostics
                                   else "Pocket generation thất bại.")
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "contour_2d":
            draft = self.editor.contour_draft(setup.wcs.origin.unit)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            if (draft is None or draft.to_operation_parameters() != operation.parameters or
                    self.editor.tool.currentData() != str(operation.tool_assembly.assembly_id) or
                    self.editor.machine.currentData() != (str(machine_id) if machine_id else None) or
                    len(operation.geometry_inputs) != 1 or
                    self._picked_reference != operation.geometry_inputs[0].reference or
                    not self._picked_reference_resolved or not operation.enabled):
                self._error("Bản nháp Biên dạng 2D chưa hợp lệ hoặc chưa được áp dụng.")
                return
            result = self._service.compute_contour(
                operation.operation_id, expected_generation=self._generation,
                profile_resolver=self._profile_resolver,
            )
            if result.accepted and result.artifact is not None:
                if self._toolpath_display is not None:
                    self._toolpath_display(result.artifact)
                self.message.emit("Biên dạng 2D đã tạo và công bố kết quả hợp lệ.")
                self.editor.set_error("")
            else:
                self._error(result.diagnostics[0].message if result.diagnostics else "2D Contour generation thất bại.")
            self.refresh(self._selected_key)
            return
        draft = self.editor.facing_draft(setup.wcs.origin.unit)
        machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
        if (draft is None or draft.to_operation_parameters() != operation.parameters or
                self.editor.tool.currentData() != str(operation.tool_assembly.assembly_id) or
                self.editor.machine.currentData() != (str(machine_id) if machine_id else None) or
                not operation.enabled):
            self._error("Bản nháp Phay mặt chưa hợp lệ hoặc chưa được áp dụng.")
            return
        result = self._service.compute_facing(
            operation.operation_id,
            expected_generation=self._generation,
            face_resolver=self._face_resolver,
        )
        if result.accepted and result.artifact is not None:
            if self._toolpath_display is not None:
                self._toolpath_display(result.artifact)
            self.message.emit("Phay mặt 2.5D đã tạo và công bố kết quả hợp lệ.")
            self.editor.set_error("")
        else:
            self._error(result.diagnostics[0].message if result.diagnostics else "Facing generation thất bại.")
        self.refresh(self._selected_key)

    def toggle_toolpath_visibility(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.data(0, _KIND_ROLE) != "operation":
            return
        setup = self._find_setup(item, self._find_job(item))
        node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE))) if setup else None
        operation = setup.operation_tree.get_operation(node.operation_id) if setup and node else None
        if operation is not None and hasattr(self._toolpath_display, "__self__"):
            viewport = self._toolpath_display.__self__
            visible = not self._toolpath_visibility.get(operation.operation_id, True)
            viewport.set_toolpath_visibility(operation.operation_id, visible)
            self._toolpath_visibility[operation.operation_id] = visible

    def move_selected(self, delta: int) -> None:
        item = self.tree.currentItem()
        context = self._tree_context()
        if item is None or context is None or item.data(0, _KIND_ROLE) not in {"group", "operation"}:
            return
        job_id, setup_id, tree, _ = context
        node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
        node = tree.get_node(node_id)
        siblings = tree.get_node(node.parent_id).child_ids
        new_index = max(0, min(len(siblings) - 1, siblings.index(node_id) + delta))
        changed = self._execute(lambda app: app.update_tree(job_id, setup_id,
            lambda value: value.reorder_node(node_id, new_index)))
        if changed is not None:
            self.refresh((item.data(0, _KIND_ROLE), str(node_id)))

    def delete_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        kind = item.data(0, _KIND_ROLE)
        if kind not in {"job", "setup", "group", "operation"}:
            return
        if QMessageBox.question(self, "Xác nhận xóa", f"Xóa '{item.text(0)}'?") != QMessageBox.StandardButton.Yes:
            return
        job_id = CamJobId.parse(item.data(0, _JOB_ROLE))
        removed_operation_id: OperationId | None = None
        removed_parallel_zone_id: MachiningZone3DId | None = None
        if kind == "job":
            changed = self._execute(lambda app: app.delete_job(job_id))
        elif kind == "setup":
            changed = self._execute(lambda app: app.delete_setup(job_id, SetupId.parse(item.data(0, _SETUP_ROLE))))
        else:
            setup_id = SetupId.parse(item.data(0, _SETUP_ROLE))
            node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
            if kind == "operation":
                setup = self._find_setup(item, self._find_job(item))
                node = setup.operation_tree.get_node(node_id) if setup is not None else None
                removed_operation_id = node.operation_id if node is not None else None
                if removed_operation_id is not None and setup is not None:
                    operation = setup.operation_tree.get_operation(removed_operation_id)
                    if operation.strategy_key == "parallel_finishing_3d":
                        removed_parallel_zone_id = (
                            ParallelFinishingParameters.from_operation_parameters(
                                operation.parameters
                            ).zone_id
                        )
                    elif operation.strategy_key == "z_level_finishing_3d":
                        removed_parallel_zone_id = (
                            ZLevelFinishingParameters.from_operation_parameters(
                                operation.parameters
                            ).zone_id
                        )
            changed = self._execute(lambda app: app.update_tree(job_id, setup_id, lambda tree: tree.remove_node(node_id)))
        if changed is not None:
            if removed_parallel_zone_id is not None:
                config = self._service.cam3d_config
                self._service.stage_cam3d_config(
                    Cam3DProjectConfig(
                        config.project_id,
                        tuple(
                            zone
                            for zone in config.zones
                            if zone.zone_id != removed_parallel_zone_id
                        ),
                    )
                )
            if removed_operation_id is not None:
                self._parallel_drafts.pop(removed_operation_id, None)
                self._parallel_draft_evidence.pop(removed_operation_id, None)
                self._parallel_reports.pop(removed_operation_id, None)
                self._z_level_drafts.pop(removed_operation_id, None)
                self._z_level_draft_evidence.pop(removed_operation_id, None)
                self._z_level_reports.pop(removed_operation_id, None)
                self._service.clear_simulation_result(
                    removed_operation_id,
                    delete_cache=True,
                )
                self._remove_toolpath(removed_operation_id)
                self._toolpath_visibility.pop(removed_operation_id, None)
                if self._displayed_operation_id == removed_operation_id:
                    self._displayed_operation_id = None
            elif self._toolpath_clear is not None:
                self._toolpath_clear()
                self._displayed_operation_id = None
            self.refresh()

    def duplicate_selected_operation(self) -> None:
        """Duplicate one operation with fresh identities and no derived artifact."""
        item = self.tree.currentItem()
        context = self._tree_context()
        if (
            item is None
            or context is None
            or item.data(0, _KIND_ROLE) != "operation"
        ):
            return
        job_id, setup_id, tree, _parent_id = context
        node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
        node = tree.get_node(node_id)
        if node.operation_id is None or node.parent_id is None:
            return
        original = tree.get_operation(node.operation_id)
        duplicate_node_id = CamNodeId.new()
        duplicate_operation_id = OperationId.new()
        duplicate_geometry = tuple(
            replace(value, input_id=GeometryInputId.new())
            for value in original.geometry_inputs
        )
        duplicate_parameters = original.parameters
        duplicated_zone = None
        if original.strategy_key == "parallel_finishing_3d":
            parameters = ParallelFinishingParameters.from_operation_parameters(
                original.parameters
            )
            new_zone_id = MachiningZone3DId.new()
            duplicate_parameters = replace(
                parameters, zone_id=new_zone_id
            ).to_operation_parameters()
            source_zone = next(
                (
                    value
                    for value in self._service.cam3d_config.zones
                    if value.zone_id == parameters.zone_id
                ),
                None,
            )
            if source_zone is not None:
                duplicated_zone = replace(
                    source_zone,
                    zone_id=new_zone_id,
                    part_surfaces=replace(
                        source_zone.part_surfaces,
                        selection=replace(
                            source_zone.part_surfaces.selection,
                            selection_id=CamSurfaceSelectionId.new(),
                        ),
                    ),
                )
        elif original.strategy_key == "z_level_finishing_3d":
            parameters = ZLevelFinishingParameters.from_operation_parameters(
                original.parameters
            )
            new_zone_id = MachiningZone3DId.new()
            base = replace(
                parameters, zone_id=new_zone_id
            ).to_operation_parameters()
            automatic = dict(original.parameters.values).get(
                AUTOMATIC_PARAMETER_CONTRACT_KEY
            )
            duplicate_parameters = OperationParameterSet(
                base.strategy_key,
                base.strategy_version,
                (
                    base.values
                    if automatic is None
                    else base.values
                    + ((AUTOMATIC_PARAMETER_CONTRACT_KEY, automatic),)
                ),
                base.schema_version,
            )
            source_zone = next(
                (
                    value
                    for value in self._service.cam3d_config.zones
                    if value.zone_id == parameters.zone_id
                ),
                None,
            )
            if source_zone is not None:
                duplicated_zone = replace(
                    source_zone,
                    zone_id=new_zone_id,
                    part_surfaces=replace(
                        source_zone.part_surfaces,
                        selection=replace(
                            source_zone.part_surfaces.selection,
                            selection_id=CamSurfaceSelectionId.new(),
                        ),
                    ),
                )
        duplicate = replace(
            original,
            operation_id=duplicate_operation_id,
            node_id=duplicate_node_id,
            geometry_inputs=duplicate_geometry,
            parameters=duplicate_parameters,
            revision=Revision(0),
            artifact_state=ArtifactState(),
            diagnostics=(),
        )
        sibling_names = {
            tree.get_node(value).name
            for value in tree.get_node(node.parent_id).child_ids
        }
        base_name = f"{node.name} — Bản sao"
        name = base_name
        suffix = 2
        while name in sibling_names:
            name = f"{base_name} {suffix}"
            suffix += 1
        original_index = tree.get_node(node.parent_id).child_ids.index(node_id)
        changed = self._execute(
            lambda app: app.update_tree(
                job_id,
                setup_id,
                lambda value: value.add_operation(
                    node.parent_id, name, duplicate
                ).reorder_node(duplicate_node_id, original_index + 1),
            )
        )
        if changed is not None:
            if duplicated_zone is not None:
                config = self._service.cam3d_config
                self._service.stage_cam3d_config(
                    Cam3DProjectConfig(
                        config.project_id, (*config.zones, duplicated_zone)
                    )
                )
            self.refresh(("operation", str(duplicate_node_id)))
            self.projection_changed.emit()
            self.message.emit(
                f"Đã nhân bản '{node.name}' thành '{name}'; "
                "đường chạy dao cần tính lại."
            )

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._guard or column != 0:
            return
        kind = item.data(0, _KIND_ROLE)
        old_key = (kind, item.data(0, _ID_ROLE))
        job_id = CamJobId.parse(item.data(0, _JOB_ROLE))
        changed = None
        if kind == "job":
            changed = self._execute(lambda app: app.rename_job(job_id, item.text(0)))
        elif kind == "setup":
            changed = self._execute(lambda app: app.rename_setup(job_id, SetupId.parse(item.data(0, _SETUP_ROLE)), item.text(0)))
        elif kind in {"group", "operation"}:
            setup_id, node_id = SetupId.parse(item.data(0, _SETUP_ROLE)), CamNodeId.parse(item.data(0, _ID_ROLE))
            changed = self._execute(lambda app: app.update_tree(job_id, setup_id, lambda tree: tree.rename_node(node_id, item.text(0))))
        if changed is not None:
            self.refresh(old_key)

    def _apply_properties(self, values: dict[str, object]) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        job = self._find_job(item)
        setup = self._find_setup(item, job)
        kind = item.data(0, _KIND_ROLE)
        try:
            if kind == "job" and job:
                result = self._execute(lambda app: app.rename_job(job.job_id, str(values["name"])))
            elif kind in {"setup", "stock"} and job and setup:
                unit = setup.wcs.origin.unit
                origin = Point3(float(values["x"]), float(values["y"]), float(values["z"]), unit)
                wcs = WcsFrame(origin, setup.wcs.x_axis, setup.wcs.y_axis, setup.wcs.z_axis)
                stock_kind = StockKind(str(values["stock_kind"]))
                if stock_kind is StockKind.BOX:
                    stock = BoxStock(Length(float(values["a"]), unit), Length(float(values["b"]), unit), Length(float(values["c"]), unit), wcs)
                elif stock_kind is StockKind.CYLINDER:
                    from hms_cadcam.cam.domain import CylinderStock
                    stock = CylinderStock(Length(float(values["a"]), unit), Length(float(values["b"]), unit), wcs)
                else:
                    raise ValueError(f"Stock {stock_kind.value} chưa được hỗ trợ trong editor 7B.1")
                changed = replace(setup, name=str(values["name"]), kind=SetupKind(str(values["setup_kind"])),
                                  wcs=wcs, work_offset=WorkOffset(str(values["offset"])), stock=stock)
                result = self._execute(lambda app: app.replace_setup(job.job_id, changed))
            elif kind in {"group", "operation"} and job and setup:
                node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
                node = setup.operation_tree.get_node(node_id)
                current = setup.operation_tree.get_operation(node.operation_id) if node.operation_id else None
                if current is not None and current.strategy_key == "facing_2_5d":
                    unit = setup.wcs.origin.unit
                    feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
                    parameters = FacingParameters(unit, FacingBoundarySource(str(values["boundary_source"])),
                        Length(float(values["top"]), unit), Length(float(values["target"]), unit),
                        Length(float(values["stepdown"]), unit), Length(float(values["stepover"]), unit),
                        Length(float(values["allowance"]), unit), Length(float(values["clearance"]), unit),
                        Length(float(values["retract"]), unit), FeedRate(float(values["feed"]), feed_unit),
                        FeedRate(float(values["plunge"]), feed_unit), SpindleSpeed(float(values["spindle"])),
                        FacingCutDirection(str(values["direction"])), float(values["angle"]),
                        Length(float(values["overtravel"]), unit))
                    snapshot = self._service.cam_snapshot
                    assembly = next(value for value in snapshot.tool_assemblies
                                    if str(value.assembly_id) == values["tool_id"])
                    machine = next(value for value in snapshot.machine_definitions
                                   if str(value.machine_id) == values["machine_id"])
                    requirement = MachineRequirement(machine.machine_id, machine.revision,
                        machine.content_fingerprint, machine.unit, (OperationCapability.MILLING,))
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    geometry_inputs = ()
                    if (parameters.boundary_source is FacingBoundarySource.PLANAR_FACE and
                            self._picked_reference is not None):
                        existing = current.geometry_inputs[0] if len(current.geometry_inputs) == 1 else None
                        input_id = (existing.input_id if existing is not None and
                                    existing.reference.reference_id == self._picked_reference.reference_id
                                    else GeometryInputId.new())
                        geometry_inputs = (OperationGeometryInput(
                            input_id, GeometryInputRole.BOUNDARY, self._picked_reference,
                            True, GeometryReferenceKind.FACE, 0,
                        ),)
                    enabled = bool(values["enabled"])
                    inputs_changed = (parameter_set != current.parameters or
                                      tool_reference != current.tool_assembly or
                                      requirement != current.machine_requirement or
                                      geometry_inputs != current.geometry_inputs)
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if inputs_changed or enabled_changed:
                        reason = (DirtyReason.PARAMETERS_CHANGED if inputs_changed
                                  else DirtyReason.UPSTREAM_CHANGED)
                        changed_operation = replace(current, parameters=parameter_set,
                            tool_assembly=tool_reference, machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled, revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason))
                    tree_mutation = lambda tree: tree.rename_node(node_id, str(values["name"])).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "contour_2d":
                    unit = setup.wcs.origin.unit
                    parameters = self.editor.contour_draft(unit)
                    if parameters is None:
                        raise ValueError("Draft 2D Contour chưa hợp lệ.")
                    snapshot = self._service.cam_snapshot
                    assembly = next(value for value in snapshot.tool_assemblies
                                    if str(value.assembly_id) == values["tool_id"])
                    machine = next(value for value in snapshot.machine_definitions
                                   if str(value.machine_id) == values["machine_id"])
                    requirement = MachineRequirement(machine.machine_id, machine.revision,
                        machine.content_fingerprint, machine.unit, (OperationCapability.MILLING,))
                    expected_kind = (GeometryReferenceKind.FACE
                                     if parameters.profile_source is ContourProfileSource.PLANAR_FACE_OUTER
                                     else GeometryReferenceKind.SKETCH_OR_PROFILE)
                    if self._picked_reference is None or self._picked_reference.kind is not expected_kind:
                        raise ValueError("Hãy Bind profile đúng loại trước khi Áp dụng 2D Contour.")
                    existing = current.geometry_inputs[0] if len(current.geometry_inputs) == 1 else None
                    input_id = (existing.input_id if existing is not None and
                                existing.reference.reference_id == self._picked_reference.reference_id
                                else GeometryInputId.new())
                    geometry_inputs = (OperationGeometryInput(
                        input_id, GeometryInputRole.PROFILE, self._picked_reference,
                        True, expected_kind, 0,
                    ),)
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    inputs_changed = (parameter_set != current.parameters or
                                      tool_reference != current.tool_assembly or
                                      requirement != current.machine_requirement or
                                      geometry_inputs != current.geometry_inputs)
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if inputs_changed or enabled_changed:
                        reason = DirtyReason.PARAMETERS_CHANGED if inputs_changed else DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(current, parameters=parameter_set,
                            tool_assembly=tool_reference, machine_requirement=requirement,
                            geometry_inputs=geometry_inputs, enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason))
                    tree_mutation = lambda tree: tree.rename_node(node_id, str(values["name"])).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "pocket_2_5d":
                    unit = setup.wcs.origin.unit
                    if self._picked_reference is None:
                        raise ValueError("Pocket thiếu profile. Hãy Bind profile trước khi Áp dụng.")
                    if not self._picked_reference_resolved:
                        raise ValueError("Pocket profile đã stale hoặc không còn hợp lệ.")
                    parameters = self.editor.pocket_draft(unit, self._picked_reference)
                    if parameters is None:
                        raise ValueError("Thông số Pocket chưa hợp lệ.")
                    snapshot = self._service.cam_snapshot
                    assembly = next((value for value in snapshot.tool_assemblies
                                     if str(value.assembly_id) == values["tool_id"]), None)
                    if assembly is None:
                        raise ValueError("Hốc thiếu cụm Tool hợp lệ.")
                    machine = next((value for value in snapshot.machine_definitions
                                    if str(value.machine_id) == values["machine_id"]), None)
                    if machine is None:
                        raise ValueError("Pocket thiếu máy phay hợp lệ.")
                    requirement = MachineRequirement(
                        machine.machine_id, machine.revision, machine.content_fingerprint,
                        machine.unit, (OperationCapability.MILLING,),
                    )
                    existing = current.geometry_inputs[0] if len(current.geometry_inputs) == 1 else None
                    input_id = (existing.input_id if existing is not None and
                                existing.reference.reference_id == self._picked_reference.reference_id
                                else GeometryInputId.new())
                    geometry_inputs = (OperationGeometryInput(
                        input_id, GeometryInputRole.BOUNDARY, self._picked_reference,
                        True, self._picked_reference.kind, 0,
                    ),)
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((parameter_changed, geometry_changed, tool_changed,
                            machine_changed, enabled_changed)):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "boring_v1":
                    unit = setup.wcs.origin.unit
                    if self._picked_hole_source is None:
                        raise ValueError(
                            "bore.geometry_missing: Boring thiếu hole geometry."
                        )
                    parameters = self.editor.boring_draft(
                        unit,
                        self._picked_hole_source,
                    )
                    if parameters is None:
                        raise ValueError(
                            self.editor.boring_draft_error
                            or "bore.invalid_parameters: Thông số Boring chưa hợp lệ."
                        )
                    if self._drilling_resolver is None:
                        raise ValueError(
                            "bore.geometry_missing: Boring resolver chưa sẵn sàng."
                        )
                    resolved = self._drilling_resolver(
                        parameters.geometry,
                        parameters.depth,
                    )
                    snapshot = self._service.cam_snapshot
                    assembly = next((
                        value for value in snapshot.tool_assemblies
                        if str(value.assembly_id) == values["tool_id"]
                    ), None)
                    if assembly is None:
                        raise ValueError(
                            "bore.tool_missing: Tiện lỗ thiếu cụm Tool."
                        )
                    tool = next((
                        value for value in snapshot.tool_definitions
                        if value.tool_id == assembly.tool_id
                    ), None)
                    holder = next((
                        value for value in snapshot.holder_definitions
                        if value.holder_id == assembly.holder_id
                    ), None)
                    machine = next((
                        value for value in snapshot.machine_definitions
                        if str(value.machine_id) == values["machine_id"]
                    ), None)
                    if machine is None:
                        raise ValueError(
                            "bore.machine_incompatible: Boring thiếu Machine."
                        )
                    requirement = MachineRequirement(
                        machine.machine_id,
                        machine.revision,
                        machine.content_fingerprint,
                        machine.unit,
                        (OperationCapability.DRILLING,),
                    )
                    geometry_inputs = _hole_geometry_inputs(
                        current.geometry_inputs,
                        self._picked_hole_source,
                    )
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    validation_operation = replace(
                        current,
                        parameters=parameter_set,
                        tool_assembly=tool_reference,
                        machine_requirement=requirement,
                        geometry_inputs=geometry_inputs,
                        enabled=True,
                    )
                    try:
                        BoringGenerator().resolve_inputs(
                            validation_operation,
                            setup,
                            assembly=assembly,
                            tool=tool,
                            holder=holder,
                            machine=machine,
                            resolved_geometry=resolved,
                        )
                    except BoringGenerationError as error:
                        raise ValueError(
                            f"{error.code.value}: {error}"
                        ) from error
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((
                        parameter_changed,
                        geometry_changed,
                        tool_changed,
                        machine_changed,
                        enabled_changed,
                    )):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "reaming_v1":
                    unit = setup.wcs.origin.unit
                    if self._picked_hole_source is None:
                        raise ValueError(
                            "ream.geometry_missing: Reaming thiếu hole geometry."
                        )
                    parameters = self.editor.reaming_draft(
                        unit,
                        self._picked_hole_source,
                    )
                    if parameters is None:
                        raise ValueError(
                            self.editor.reaming_draft_error
                            or "ream.invalid_parameters: Thông số Reaming chưa hợp lệ."
                        )
                    if self._drilling_resolver is None:
                        raise ValueError(
                            "ream.geometry_missing: Reaming resolver chưa sẵn sàng."
                        )
                    resolved = self._drilling_resolver(
                        parameters.geometry,
                        parameters.depth,
                    )
                    snapshot = self._service.cam_snapshot
                    assembly = next((
                        value for value in snapshot.tool_assemblies
                        if str(value.assembly_id) == values["tool_id"]
                    ), None)
                    if assembly is None:
                        raise ValueError(
                            "ream.tool_missing: Doa thiếu cụm Tool."
                        )
                    tool = next((
                        value for value in snapshot.tool_definitions
                        if value.tool_id == assembly.tool_id
                    ), None)
                    machine = next((
                        value for value in snapshot.machine_definitions
                        if str(value.machine_id) == values["machine_id"]
                    ), None)
                    if machine is None:
                        raise ValueError(
                            "ream.machine_incompatible: Reaming thiếu Machine."
                        )
                    requirement = MachineRequirement(
                        machine.machine_id,
                        machine.revision,
                        machine.content_fingerprint,
                        machine.unit,
                        (OperationCapability.DRILLING,),
                    )
                    geometry_inputs = _hole_geometry_inputs(
                        current.geometry_inputs,
                        self._picked_hole_source,
                    )
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    validation_operation = replace(
                        current,
                        parameters=parameter_set,
                        tool_assembly=tool_reference,
                        machine_requirement=requirement,
                        geometry_inputs=geometry_inputs,
                        enabled=True,
                    )
                    try:
                        ReamingGenerator().resolve_inputs(
                            validation_operation,
                            setup,
                            assembly=assembly,
                            tool=tool,
                            machine=machine,
                            resolved_geometry=resolved,
                        )
                    except ReamingGenerationError as error:
                        raise ValueError(
                            f"{error.code.value}: {error}"
                        ) from error
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((
                        parameter_changed,
                        geometry_changed,
                        tool_changed,
                        machine_changed,
                        enabled_changed,
                    )):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "tapping_v1":
                    unit = setup.wcs.origin.unit
                    if self._picked_hole_source is None:
                        raise ValueError(
                            "tap.geometry_missing: Tapping thiếu hole geometry."
                        )
                    parameters = self.editor.tapping_draft(
                        unit,
                        self._picked_hole_source,
                    )
                    if parameters is None:
                        raise ValueError(
                            self.editor.tapping_draft_error
                            or "tap.invalid_parameters: Thông số Tapping chưa hợp lệ."
                        )
                    if self._drilling_resolver is None:
                        raise ValueError(
                            "tap.geometry_missing: Tapping resolver chưa sẵn sàng."
                        )
                    resolved = self._drilling_resolver(
                        parameters.geometry,
                        parameters.depth,
                    )
                    snapshot = self._service.cam_snapshot
                    assembly = next((
                        value for value in snapshot.tool_assemblies
                        if str(value.assembly_id) == values["tool_id"]
                    ), None)
                    if assembly is None:
                        raise ValueError(
                            "tap.tool_missing: Ta rô thiếu cụm Tool."
                        )
                    tool = next((
                        value for value in snapshot.tool_definitions
                        if value.tool_id == assembly.tool_id
                    ), None)
                    machine = next((
                        value for value in snapshot.machine_definitions
                        if str(value.machine_id) == values["machine_id"]
                    ), None)
                    if machine is None:
                        raise ValueError(
                            "tap.machine_incompatible: Tapping thiếu Machine."
                        )
                    requirement = MachineRequirement(
                        machine.machine_id,
                        machine.revision,
                        machine.content_fingerprint,
                        machine.unit,
                        (OperationCapability.TAPPING,),
                    )
                    geometry_inputs = _hole_geometry_inputs(
                        current.geometry_inputs,
                        self._picked_hole_source,
                    )
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    validation_operation = replace(
                        current,
                        parameters=parameter_set,
                        tool_assembly=tool_reference,
                        machine_requirement=requirement,
                        geometry_inputs=geometry_inputs,
                        enabled=True,
                    )
                    try:
                        TappingGenerator().resolve_inputs(
                            validation_operation,
                            setup,
                            assembly=assembly,
                            tool=tool,
                            machine=machine,
                            resolved_geometry=resolved,
                        )
                    except TappingGenerationError as error:
                        raise ValueError(
                            f"{error.code.value}: {error}"
                        ) from error
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((
                        parameter_changed,
                        geometry_changed,
                        tool_changed,
                        machine_changed,
                        enabled_changed,
                    )):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "drilling_v1":
                    unit = setup.wcs.origin.unit
                    if self._picked_hole_source is None:
                        raise ValueError(
                            "Drilling thiếu hole geometry. Hãy Bind trước khi Áp dụng."
                        )
                    parameters = self.editor.drilling_draft(
                        unit, self._picked_hole_source,
                    )
                    if parameters is None:
                        raise ValueError("Thông số Drilling chưa hợp lệ.")
                    if self._drilling_resolver is None:
                        raise ValueError("Drilling resolver chưa sẵn sàng.")
                    resolved = self._drilling_resolver(
                        parameters.geometry, parameters.depth,
                    )
                    if resolved.status is not GeometryResolutionStatus.RESOLVED:
                        raise ValueError(
                            resolved.diagnostics[0].message
                            if resolved.diagnostics
                            else "Drilling geometry không còn hợp lệ."
                        )
                    snapshot = self._service.cam_snapshot
                    assembly = next((
                        value for value in snapshot.tool_assemblies
                        if str(value.assembly_id) == values["tool_id"]
                    ), None)
                    if assembly is None:
                        raise ValueError("Khoan thiếu cụm Tool hợp lệ.")
                    tool = next((
                        value for value in snapshot.tool_definitions
                        if value.tool_id == assembly.tool_id
                    ), None)
                    expected_family = (
                        ToolFamily.CENTER_DRILL
                        if parameters.cycle is DrillingCycle.SPOT_DRILL
                        else ToolFamily.DRILL
                    )
                    if tool is None or tool.family is not expected_family:
                        raise ValueError(
                            f"{parameters.cycle.value} yêu cầu tool {expected_family.value}."
                        )
                    machine = next((
                        value for value in snapshot.machine_definitions
                        if str(value.machine_id) == values["machine_id"]
                    ), None)
                    if (
                        machine is None
                        or OperationCapability.DRILLING
                        not in machine.capabilities.operations
                    ):
                        raise ValueError("Drilling thiếu máy có capability DRILLING.")
                    requirement = MachineRequirement(
                        machine.machine_id, machine.revision,
                        machine.content_fingerprint, machine.unit,
                        (OperationCapability.DRILLING,),
                    )
                    geometry_inputs = _hole_geometry_inputs(
                        current.geometry_inputs,
                        self._picked_hole_source,
                    )
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((parameter_changed, geometry_changed, tool_changed,
                            machine_changed, enabled_changed)):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                else:
                    tree_mutation = lambda tree: tree.rename_node(node_id, str(values["name"])).set_enabled(node_id, bool(values["enabled"]))
                result = self._execute(lambda app: app.update_tree(job.job_id, setup.setup_id, tree_mutation))
            else:
                result = None
            if result is not None:
                self.editor.set_error("")
                self.refresh(self._selected_key)
        except (RuntimeError, TypeError, ValueError) as error:
            message = str(error)
            if self._active_editor_strategy_key in {"reaming_v1", "boring_v1"}:
                strategy_key = self._active_editor_strategy_key
                operation_id = self._active_editor_operation_id
                self._active_editor_operation_id = None
                if operation_id is not None:
                    drafts = (
                        self._reaming_drafts
                        if strategy_key == "reaming_v1"
                        else self._boring_drafts
                    )
                    drafts.pop(operation_id, None)
                self.refresh(self._selected_key)
            self.editor.set_error(message)

    def _tree_context(self):
        item = self.tree.currentItem()
        snapshot = self._service.cam_snapshot if self._service.has_project else None
        if snapshot is None or snapshot.active_job_id is None:
            return None
        job = self._find_job(item) if item else _active_job(snapshot)
        if job is None or job.active_setup is None:
            self._error("Hãy chọn hoặc tạo thiết lập trước.")
            return None
        setup = self._find_setup(item, job) if item else job.active_setup
        setup = setup or job.active_setup
        parent_id = setup.operation_tree.root_id
        if item and item.data(0, _KIND_ROLE) == "group":
            parent_id = CamNodeId.parse(item.data(0, _ID_ROLE))
        return job.job_id, setup.setup_id, setup.operation_tree, parent_id

    def _find_job(self, item):
        if item is None or not self._service.has_project:
            return None
        value = item.data(0, _JOB_ROLE)
        return next((job for job in self._service.cam_snapshot.jobs if str(job.job_id) == value), None)

    @staticmethod
    def _find_setup(item, job):
        if item is None or job is None:
            return None
        value = item.data(0, _SETUP_ROLE)
        return next((setup for setup in job.setups if str(setup.setup_id) == value), None)

    def _execute(self, command):
        if self._generation is None:
            return None
        try:
            result = self._service.execute_cam_command(
                command,
                expected_generation=self._generation,
            )
            self.message.emit("Đã cập nhật CAM; dự án có thay đổi chưa lưu.")
            return result
        except Exception as error:
            if self._active_editor_strategy_key in {
                "tapping_v1", "reaming_v1", "boring_v1",
            }:
                strategy_key = self._active_editor_strategy_key
                operation_id = self._active_editor_operation_id
                self._active_editor_operation_id = None
                if operation_id is not None:
                    drafts = (
                        self._tapping_drafts
                        if strategy_key == "tapping_v1"
                        else self._reaming_drafts
                        if strategy_key == "reaming_v1"
                        else self._boring_drafts
                    )
                    drafts.pop(operation_id, None)
                self.refresh(self._selected_key)
            self._error(str(error))
            return None

    def _error(self, text: str) -> None:
        self.editor.set_error(text)
        self.message.emit(f"CAM: {text}")

    def _update_generate_action(self) -> None:
        action = self.actions.get("generate") if hasattr(self, "actions") else None
        item = self.tree.currentItem()
        setup = self._find_setup(item, self._find_job(item)) if item is not None else None
        valid = bool(item is not None and item.data(0, _KIND_ROLE) == "operation" and
                     setup is not None and
                     self._draft_matches_selected_operation(item, setup))
        if action is not None:
            action.setEnabled(valid)

    def _draft_matches_selected_operation(self, item: QTreeWidgetItem, setup: Setup) -> bool:
        try:
            node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE)))
            operation = setup.operation_tree.get_operation(node.operation_id)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            if operation.strategy_key == "contour_2d":
                draft = self.editor.contour_draft(setup.wcs.origin.unit)
                return bool(operation.enabled and draft is not None and
                    draft.to_operation_parameters() == operation.parameters and
                    len(operation.geometry_inputs) == 1 and
                    self._picked_reference == operation.geometry_inputs[0].reference and
                    self._picked_reference_resolved and
                    self.editor.tool.currentData() == str(operation.tool_assembly.assembly_id) and
                    self.editor.machine.currentData() == (str(machine_id) if machine_id else None))
            if operation.strategy_key == "pocket_2_5d":
                draft = self.editor.pocket_draft(
                    setup.wcs.origin.unit, self._picked_reference,
                )
                return bool(operation.enabled and draft is not None and
                    draft.to_operation_parameters() == operation.parameters and
                    len(operation.geometry_inputs) == 1 and
                    self._picked_reference == operation.geometry_inputs[0].reference and
                    self._picked_reference_resolved and
                    self.editor.tool.currentData() == str(operation.tool_assembly.assembly_id) and
                    self.editor.machine.currentData() == (str(machine_id) if machine_id else None))
            if operation.strategy_key == "drilling_v1":
                draft = self.editor.drilling_draft(
                    setup.wcs.origin.unit, self._picked_hole_source,
                )
                return bool(
                    operation.enabled
                    and draft is not None
                    and draft.to_operation_parameters() == operation.parameters
                    and self._picked_hole_source is not None
                    and _operation_matches_hole_source(
                        operation, self._picked_hole_source,
                    )
                    and self._picked_reference_resolved
                    and self.editor.tool.currentData()
                    == str(operation.tool_assembly.assembly_id)
                    and self.editor.machine.currentData()
                    == (str(machine_id) if machine_id else None)
                )
            if operation.strategy_key == "tapping_v1":
                draft = self.editor.tapping_draft(
                    setup.wcs.origin.unit,
                    self._picked_hole_source,
                )
                return bool(
                    operation.enabled
                    and draft is not None
                    and draft.to_operation_parameters() == operation.parameters
                    and self._picked_hole_source is not None
                    and _operation_matches_hole_source(
                        operation, self._picked_hole_source,
                    )
                    and self._picked_reference_resolved
                    and self.editor.tool.currentData()
                    == str(operation.tool_assembly.assembly_id)
                    and self.editor.machine.currentData()
                    == (str(machine_id) if machine_id else None)
                )
            if operation.strategy_key == "reaming_v1":
                draft = self.editor.reaming_draft(
                    setup.wcs.origin.unit,
                    self._picked_hole_source,
                )
                return bool(
                    operation.enabled
                    and draft is not None
                    and draft.to_operation_parameters() == operation.parameters
                    and self._picked_hole_source is not None
                    and _operation_matches_hole_source(
                        operation, self._picked_hole_source,
                    )
                    and self._picked_reference_resolved
                    and self.editor.tool.currentData()
                    == str(operation.tool_assembly.assembly_id)
                    and self.editor.machine.currentData()
                    == (str(machine_id) if machine_id else None)
                )
            if operation.strategy_key == "boring_v1":
                draft = self.editor.boring_draft(
                    setup.wcs.origin.unit,
                    self._picked_hole_source,
                )
                return bool(
                    operation.enabled
                    and draft is not None
                    and draft.to_operation_parameters() == operation.parameters
                    and self._picked_hole_source is not None
                    and _operation_matches_hole_source(
                        operation, self._picked_hole_source,
                    )
                    and self._picked_reference_resolved
                    and self.editor.tool.currentData()
                    == str(operation.tool_assembly.assembly_id)
                    and self.editor.machine.currentData()
                    == (str(machine_id) if machine_id else None)
                )
            draft = self.editor.facing_draft(setup.wcs.origin.unit)
            return bool(operation.enabled and draft is not None and
                draft.to_operation_parameters() == operation.parameters and
                (draft.boundary_source is FacingBoundarySource.STOCK_BOX or
                 (len(operation.geometry_inputs) == 1 and
                  self._picked_reference == operation.geometry_inputs[0].reference and
                  self._picked_reference_resolved)) and
                self.editor.tool.currentData() == str(operation.tool_assembly.assembly_id) and
                self.editor.machine.currentData() == (str(machine_id) if machine_id else None))
        except (TypeError, ValueError):
            return False


class _LocalizedValueComboBox(QComboBox):
    """Show Vietnamese labels while preserving legacy raw-text contracts."""

    def addItems(self, texts) -> None:  # noqa: N802
        for text in texts:
            raw = str(text)
            self.addItem(ui_text(raw), raw)

    def currentText(self) -> str:  # noqa: N802
        raw = self.currentData()
        return raw if isinstance(raw, str) else super().currentText()

    def setCurrentText(self, text: str) -> None:  # noqa: N802
        index = self.findData(text)
        if index >= 0:
            self.setCurrentIndex(index)
            return
        super().setCurrentText(text)


class _CamPropertiesEditor(QWidget):
    draft_changed = Signal()

    def __init__(self, commit: Callable[[dict[str, object]], None]) -> None:
        super().__init__()
        self._commit = commit
        self._tapping_draft_error = ""
        self._reaming_draft_error = ""
        self._boring_draft_error = ""
        self._tool_definitions_by_id = {}
        self._holders_by_id = {}
        self._assemblies_by_id = {}
        self._fields = {key: QLineEdit() for key in ("name", "offset", "x", "y", "z", "a", "b", "c")}
        self._facing_fields = {key: QLineEdit() for key in (
            "top", "target", "stepdown", "stepover", "allowance", "clearance", "retract",
            "feed", "plunge", "spindle", "angle", "overtravel")}
        self._contour_fields = {key: QLineEdit() for key in (
            "top", "final", "stepdown", "radial", "axial", "clearance", "retract",
            "feed", "plunge", "spindle", "lead")}
        self._pocket_fields = {key: QLineEdit() for key in (
            "top", "bottom", "stepdown", "stepover", "allowance", "axial", "clearance",
            "retract", "feed", "plunge", "spindle", "tolerance")}
        self._drilling_fields = {key: QLineEdit() for key in (
            "top", "depth", "peck", "clearance", "retract", "feed",
            "spindle", "dwell", "tolerance",
        )}
        self._tapping_fields = {key: QLineEdit() for key in (
            "top", "final", "clearance", "retract", "diameter", "pitch",
            "spindle", "dwell", "tolerance",
        )}
        self._reaming_fields = {key: QLineEdit() for key in (
            "top", "final", "clearance", "retract", "diameter", "pre_hole",
            "spindle", "feed_per_revolution", "dwell", "tolerance",
        )}
        self._boring_fields = {key: QLineEdit() for key in (
            "top", "final", "clearance", "retract", "finished_diameter",
            "pre_bore", "spindle", "feed_per_revolution", "dwell",
            "tolerance",
        )}
        self.boundary_source = _LocalizedValueComboBox(); self.boundary_source.addItems([item.value for item in FacingBoundarySource])
        self.direction = _LocalizedValueComboBox(); self.direction.addItems([item.value for item in FacingCutDirection])
        self.profile_source = _LocalizedValueComboBox(); self.profile_source.addItems([item.value for item in ContourProfileSource])
        self.contour_side = _LocalizedValueComboBox(); self.contour_side.addItems([item.value for item in ContourSide])
        self.contour_direction = _LocalizedValueComboBox(); self.contour_direction.addItems([item.value for item in ContourCutDirection])
        self.pocket_entry = _LocalizedValueComboBox(); self.pocket_entry.addItems([item.value for item in PocketEntryPolicy])
        self.pocket_direction = _LocalizedValueComboBox(); self.pocket_direction.addItems([item.value for item in PocketCuttingDirection])
        self.drilling_cycle = _LocalizedValueComboBox(); self.drilling_cycle.addItems([item.value for item in DrillingCycle])
        self.drilling_retract = _LocalizedValueComboBox(); self.drilling_retract.addItems([item.value for item in DrillRetractPolicy])
        self.tapping_hand = _LocalizedValueComboBox(); self.tapping_hand.addItems([item.value for item in TappingHand])
        self.tapping_mode = _LocalizedValueComboBox(); self.tapping_mode.addItems([item.value for item in TappingSynchronizationPolicy])
        self.reaming_spindle_direction = _LocalizedValueComboBox(); self.reaming_spindle_direction.addItems([item.value for item in SpindleDirection])
        self.reaming_retract_policy = _LocalizedValueComboBox(); self.reaming_retract_policy.addItems([item.value for item in ReamingRetractPolicy])
        self.reaming_coolant = _LocalizedValueComboBox(); self.reaming_coolant.addItems([item.value for item in ReamingCoolantMode])
        self.reaming_derived = QLabel("—")
        self.reaming_derived.setWordWrap(True)
        self.boring_spindle_direction = _LocalizedValueComboBox(); self.boring_spindle_direction.addItems([item.value for item in SpindleDirection])
        self.boring_retract_policy = _LocalizedValueComboBox(); self.boring_retract_policy.addItems([item.value for item in BoringRetractPolicy])
        self.boring_coolant = _LocalizedValueComboBox(); self.boring_coolant.addItems([item.value for item in BoringCoolantMode])
        self.boring_derived = QLabel("—")
        self.boring_derived.setWordWrap(True)
        self.boring_tool_details = QLabel("—")
        self.boring_tool_details.setWordWrap(True)
        self.finishing_pass = QCheckBox("Finishing pass")
        self.multiple_depth_passes = QCheckBox("Nhiều lớp chiều sâu"); self.multiple_depth_passes.setChecked(True)
        self.tool = QComboBox()
        self.tool.setAccessibleName("Cụm Tool")
        self.machine = QComboBox()
        self.machine.setAccessibleName("Máy")
        self.setup_kind = _LocalizedValueComboBox(); self.setup_kind.addItems([item.value for item in SetupKind])
        self.stock_kind = _LocalizedValueComboBox(); self.stock_kind.addItems([item.value for item in StockKind])
        self.enabled = QCheckBox("Được bật")
        self.status = QLabel("—")
        self.toolpath_metadata = QLabel("—")
        self.toolpath_metadata.setWordWrap(True)
        self.error = QLabel(); self.error.setStyleSheet("color: #9b241b")
        form = QFormLayout(self)
        for label, key in (("Tên", "name"), ("Work offset", "offset"), ("WCS X", "x"), ("WCS Y", "y"), ("WCS Z", "z")):
            form.addRow(label, self._fields[key])
        form.addRow("Loại thiết lập", self.setup_kind); form.addRow("Loại phôi", self.stock_kind)
        form.addRow("Kích thước A", self._fields["a"]); form.addRow("Kích thước B", self._fields["b"]); form.addRow("Kích thước C", self._fields["c"])
        form.addRow("Nguồn Facing", self.boundary_source); form.addRow("Tool Assembly", self.tool); form.addRow("Máy", self.machine)
        for label, key in (("Top Z", "top"), ("Target Z", "target"), ("Stepdown", "stepdown"),
                           ("Stepover", "stepover"), ("Allowance", "allowance"), ("Clearance Z", "clearance"),
                           ("Retract Z", "retract"), ("Feed", "feed"), ("Plunge feed", "plunge"),
                           ("Spindle RPM", "spindle"), ("Raster angle", "angle"), ("Overtravel", "overtravel")):
            form.addRow(label, self._facing_fields[key])
        form.addRow("Hướng cắt", self.direction)
        form.addRow("Nguồn cấu hình", self.profile_source)
        form.addRow("Phía biên dạng", self.contour_side)
        for label, key in (("Contour Top Z", "top"), ("Final depth Z", "final"),
                           ("Contour stepdown", "stepdown"), ("Radial allowance", "radial"),
                           ("Axial allowance", "axial"), ("Contour clearance Z", "clearance"),
                           ("Contour retract Z", "retract"), ("Contour feed", "feed"),
                           ("Contour plunge", "plunge"), ("Contour spindle", "spindle"),
                           ("Linear lead length", "lead")):
            form.addRow(label, self._contour_fields[key])
        form.addRow("Contour direction", self.contour_direction)
        form.addRow("", self.multiple_depth_passes); form.addRow("", self.finishing_pass)
        for label, key in (("Pocket Top Z", "top"), ("Pocket Bottom Z", "bottom"),
                           ("Pocket stepdown", "stepdown"), ("Pocket stepover", "stepover"),
                           ("Pocket radial allowance", "allowance"),
                           ("Pocket floor allowance", "axial"),
                           ("Pocket clearance Z", "clearance"),
                           ("Pocket retract Z", "retract"), ("Pocket feed", "feed"),
                           ("Pocket plunge", "plunge"), ("Pocket spindle", "spindle"),
                           ("Pocket tolerance", "tolerance")):
            form.addRow(label, self._pocket_fields[key])
        form.addRow("Pocket entry", self.pocket_entry)
        form.addRow("Pocket direction", self.pocket_direction)
        form.addRow("Drilling cycle", self.drilling_cycle)
        for label, key in (
            ("Drilling Top Z", "top"), ("Drilling depth", "depth"),
            ("Peck depth", "peck"), ("Drilling clearance Z", "clearance"),
            ("Drilling retract Z", "retract"), ("Drilling feed", "feed"),
            ("Drilling spindle", "spindle"), ("Drilling dwell (s)", "dwell"),
            ("Drilling tolerance", "tolerance"),
        ):
            form.addRow(label, self._drilling_fields[key])
        form.addRow("Rút dao khi khoan nhấp", self.drilling_retract)
        form.addRow("Tapping hand", self.tapping_hand)
        form.addRow("Tapping mode", self.tapping_mode)
        for label, key in (
            ("Tapping Top Z", "top"),
            ("Tapping final depth Z", "final"),
            ("Tapping clearance Z", "clearance"),
            ("Tapping retract Z", "retract"),
            ("Tap nominal diameter", "diameter"),
            ("Tap pitch", "pitch"),
            ("Tapping spindle RPM", "spindle"),
            ("Tapping dwell (s, optional)", "dwell"),
            ("Tapping tolerance", "tolerance"),
        ):
            form.addRow(label, self._tapping_fields[key])
        form.addRow("Reaming spindle direction", self.reaming_spindle_direction)
        form.addRow("Chính sách rút dao khi doa", self.reaming_retract_policy)
        form.addRow("Reaming coolant", self.reaming_coolant)
        for label, key in (
            ("Reaming Top Z", "top"),
            ("Reaming final depth Z", "final"),
            ("Reaming clearance Z", "clearance"),
            ("Reaming retract Z", "retract"),
            ("Finished nominal diameter", "diameter"),
            ("Pre-hole diameter (required)", "pre_hole"),
            ("Reaming spindle RPM", "spindle"),
            ("Feed per revolution", "feed_per_revolution"),
            ("Reaming dwell (s, optional)", "dwell"),
            ("Reaming tolerance", "tolerance"),
        ):
            form.addRow(label, self._reaming_fields[key])
        form.addRow("Derived (read-only)", self.reaming_derived)
        form.addRow("Boring spindle direction", self.boring_spindle_direction)
        form.addRow("Chính sách rút dao khi khoét lỗ", self.boring_retract_policy)
        form.addRow("Boring coolant", self.boring_coolant)
        for label, key in (
            ("Boring Top Z", "top"),
            ("Boring final depth Z", "final"),
            ("Boring clearance Z", "clearance"),
            ("Boring retract Z", "retract"),
            ("Finished bore diameter", "finished_diameter"),
            ("Pre-bore diameter (required)", "pre_bore"),
            ("Boring spindle RPM", "spindle"),
            ("Boring feed per revolution", "feed_per_revolution"),
            ("Boring dwell (s, optional)", "dwell"),
            ("Boring tolerance", "tolerance"),
        ):
            form.addRow(label, self._boring_fields[key])
        form.addRow("Boring derived (read-only)", self.boring_derived)
        form.addRow("BORING_BAR current", self.boring_tool_details)
        form.addRow("Trạng thái", self.status); form.addRow("Toolpath", self.toolpath_metadata)
        form.addRow("", self.enabled); form.addRow("Lỗi", self.error)
        self.apply_button = QPushButton("Áp dụng")
        self.apply_button.setObjectName("ClassicCamApplyButton")
        self.apply_button.setAccessibleName("Áp dụng bản nháp CAM")
        self.apply_button.clicked.connect(self._submit)
        form.addRow(self.apply_button)
        for row in range(form.rowCount()):
            label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            label_widget = None if label_item is None else label_item.widget()
            field_widget = None if field_item is None else field_item.widget()
            if (
                isinstance(label_widget, QLabel)
                and isinstance(field_widget, (QLineEdit, QComboBox))
                and label_widget.text().strip()
                and not field_widget.accessibleName()
            ):
                field_widget.setAccessibleName(ui_text(label_widget.text()))
        for field in self._facing_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._contour_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._pocket_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._drilling_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._tapping_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._reaming_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
            field.textChanged.connect(self._update_reaming_preview)
        for field in self._boring_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
            field.textChanged.connect(self._update_boring_preview)
        for combo in (self.boundary_source, self.direction, self.profile_source, self.contour_side,
                      self.contour_direction, self.pocket_entry, self.pocket_direction,
                      self.drilling_cycle, self.drilling_retract,
                      self.tapping_hand, self.tapping_mode,
                      self.reaming_spindle_direction, self.reaming_retract_policy,
                      self.reaming_coolant, self.boring_spindle_direction,
                      self.boring_retract_policy, self.boring_coolant,
                      self.tool, self.machine):
            combo.currentIndexChanged.connect(lambda _index: self.draft_changed.emit())
        self.tool.currentIndexChanged.connect(self._update_boring_tool_details)
        self.finishing_pass.toggled.connect(lambda _checked: self.draft_changed.emit())
        self.multiple_depth_passes.toggled.connect(lambda _checked: self.draft_changed.emit())

    def clear(self) -> None:
        for field in self._fields.values(): field.clear()
        for field in self._facing_fields.values(): field.clear()
        for field in self._contour_fields.values(): field.clear()
        for field in self._pocket_fields.values(): field.clear()
        for field in self._drilling_fields.values(): field.clear()
        for field in self._tapping_fields.values(): field.clear()
        for field in self._reaming_fields.values(): field.clear()
        for field in self._boring_fields.values(): field.clear()
        self.reaming_derived.setText("—")
        self.boring_derived.setText("—")
        self.boring_tool_details.setText("—")
        self.tool.clear(); self.machine.clear()
        self._tool_definitions_by_id = {}
        self._holders_by_id = {}
        self._assemblies_by_id = {}
        self.boring_tool_details.setText("—")
        self.status.setText("—"); self.toolpath_metadata.setText("—"); self.error.clear()

    def show_job(self, name: str) -> None:
        self.clear(); self._fields["name"].setText(name); self.status.setText("Editable")

    def show_setup(self, setup: Setup) -> None:
        self.clear(); self._fields["name"].setText(setup.name); self._fields["offset"].setText(setup.work_offset.name)
        for key, value in zip(("x", "y", "z"), (setup.wcs.origin.x, setup.wcs.origin.y, setup.wcs.origin.z), strict=True): self._fields[key].setText(str(value))
        self.setup_kind.setCurrentText(setup.kind.value); self.stock_kind.setCurrentText(setup.stock.kind.value)
        dimensions = setup.stock.to_dict()
        if setup.stock.kind is StockKind.BOX: values = (dimensions["size_x"]["value"], dimensions["size_y"]["value"], dimensions["size_z"]["value"])
        elif setup.stock.kind is StockKind.CYLINDER: values = (dimensions["diameter"]["value"], dimensions["length"]["value"], "")
        else: values = ("", "", "")
        for key, value in zip(("a", "b", "c"), values, strict=True): self._fields[key].setText(str(value))
        if setup.stock.kind in {StockKind.BOX, StockKind.CYLINDER}:
            self.status.setText("MISSING — chưa có artifact")
        else:
            self.status.setText(
                f"KHÔNG ĐƯỢC HỖ TRỢ — Phôi "
                f"{display_value(setup.stock.kind, 'stock_kind')}"
            )

    def show_node(
        self,
        name: str,
        operation: Operation | None,
        assemblies=(),
        machines=(),
        tools=(),
        holders=(),
    ) -> None:
        self.clear(); self._fields["name"].setText(name); self.enabled.setChecked(True if operation is None else operation.enabled)
        self.status.setText("GROUP" if operation is None else operation.artifact_state.status.value.upper())
        self.tool.clear(); self.machine.clear()
        self._tool_definitions_by_id = {value.tool_id: value for value in tools}
        self._holders_by_id = {value.holder_id: value for value in holders}
        self._assemblies_by_id = {
            str(value.assembly_id): value for value in assemblies
        }
        visible_assemblies = assemblies
        if operation is not None and operation.strategy_key == "boring_v1":
            boring_tool_ids = {
                value.tool_id for value in tools
                if value.family is ToolFamily.BORING_BAR
            }
            visible_assemblies = tuple(
                value for value in assemblies if value.tool_id in boring_tool_ids
            )
        for value in visible_assemblies: self.tool.addItem(value.name, str(value.assembly_id))
        for value in machines: self.machine.addItem(value.name, str(value.machine_id))
        self.tool.setCurrentIndex(-1); self.machine.setCurrentIndex(-1)
        if operation is not None and operation.strategy_key == "facing_2_5d":
            parameters = FacingParameters.from_operation_parameters(operation.parameters)
            values = {"top": parameters.top_height.value, "target": parameters.target_height.value,
                      "stepdown": parameters.stepdown.value, "stepover": parameters.stepover.value,
                      "allowance": parameters.stock_allowance.value, "clearance": parameters.clearance_height.value,
                      "retract": parameters.retract_height.value, "feed": parameters.feed_rate.value,
                      "plunge": parameters.plunge_feed_rate.value, "spindle": parameters.spindle_speed.value,
                      "angle": parameters.raster_angle_degrees, "overtravel": parameters.overtravel.value}
            for key, value in values.items(): self._facing_fields[key].setText(str(value))
            self.boundary_source.setCurrentText(parameters.boundary_source.value)
            self.direction.setCurrentText(parameters.direction.value)
            self.tool.setCurrentIndex(self.tool.findData(str(operation.tool_assembly.assembly_id)))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(str(operation.machine_requirement.machine_id)))
        elif operation is not None and operation.strategy_key == "contour_2d":
            parameters = ContourParameters.from_operation_parameters(operation.parameters)
            values = {"top": parameters.top_height.value, "final": parameters.final_depth.value,
                      "stepdown": parameters.stepdown.value, "radial": parameters.radial_stock_allowance.value,
                      "axial": parameters.axial_stock_allowance.value,
                      "clearance": parameters.clearance_height.value, "retract": parameters.retract_height.value,
                      "feed": parameters.cutting_feed_rate.value, "plunge": parameters.plunge_feed_rate.value,
                      "spindle": parameters.spindle_speed.value, "lead": parameters.lead_length.value}
            for key, value in values.items(): self._contour_fields[key].setText(str(value))
            self.profile_source.setCurrentText(parameters.profile_source.value)
            self.contour_side.setCurrentText(parameters.side.value)
            self.contour_direction.setCurrentText(parameters.direction.value)
            self.finishing_pass.setChecked(parameters.finishing_pass)
            self.multiple_depth_passes.setChecked(parameters.multiple_depth_passes)
            self.tool.setCurrentIndex(self.tool.findData(str(operation.tool_assembly.assembly_id)))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(str(operation.machine_requirement.machine_id)))
        elif operation is not None and operation.strategy_key == "pocket_2_5d":
            values = dict(operation.parameters.values)
            mapping = {
                "top": "top_z", "bottom": "bottom_z", "stepdown": "stepdown",
                "stepover": "stepover", "allowance": "radial_stock_allowance",
                "axial": "axial_allowance",
                "clearance": "clearance_height", "retract": "retract_height",
                "feed": "cutting_feed_rate", "plunge": "plunge_feed_rate",
                "spindle": "spindle_speed", "tolerance": "tolerance",
            }
            for field, parameter in mapping.items():
                self._pocket_fields[field].setText(str(values.get(parameter, "")))
            self.pocket_entry.setCurrentText(str(values.get("entry_policy", "")))
            self.pocket_direction.setCurrentText(str(values.get("cutting_direction", "")))
            self.tool.setCurrentIndex(self.tool.findData(str(operation.tool_assembly.assembly_id)))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(str(operation.machine_requirement.machine_id)))
        elif operation is not None and operation.strategy_key == "drilling_v1":
            parameters = DrillingStrategy.from_operation_parameters(operation.parameters)
            values = {
                "top": parameters.top_z.value,
                "depth": parameters.depth.depth.value,
                "peck": (
                    "" if parameters.peck_depth is None
                    else parameters.peck_depth.value
                ),
                "clearance": parameters.clearance_height.value,
                "retract": parameters.retract_height.value,
                "feed": parameters.feed_rate.value,
                "spindle": parameters.spindle_speed.value,
                "dwell": parameters.dwell_seconds,
                "tolerance": parameters.tolerance.value,
            }
            for key, value in values.items():
                self._drilling_fields[key].setText(str(value))
            self.drilling_cycle.setCurrentText(parameters.cycle.value)
            self.drilling_retract.setCurrentText(parameters.retract_policy.value)
            self.tool.setCurrentIndex(
                self.tool.findData(str(operation.tool_assembly.assembly_id))
            )
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(
                    str(operation.machine_requirement.machine_id)
                ))
        elif operation is not None and operation.strategy_key == "tapping_v1":
            parameters = TappingStrategy.from_operation_parameters(
                operation.parameters
            )
            values = {
                "top": parameters.top_z.value,
                "final": parameters.final_depth.value,
                "clearance": parameters.clearance_height.value,
                "retract": parameters.retract_height.value,
                "diameter": parameters.nominal_diameter.value,
                "pitch": parameters.pitch.value,
                "spindle": parameters.spindle_speed.value,
                "dwell": parameters.dwell_seconds,
                "tolerance": parameters.tolerance.value,
            }
            for key, value in values.items():
                self._tapping_fields[key].setText(str(value))
            self.tapping_hand.setCurrentText(parameters.hand.value)
            self.tapping_mode.setCurrentText(
                parameters.synchronization_policy.value
            )
            self.tool.setCurrentIndex(self.tool.findData(
                str(operation.tool_assembly.assembly_id)
            ))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(
                    str(operation.machine_requirement.machine_id)
                ))
        elif operation is not None and operation.strategy_key == "reaming_v1":
            parameters = ReamingStrategy.from_operation_parameters(
                operation.parameters
            )
            values = {
                "top": parameters.top_z.value,
                "final": parameters.final_depth.value,
                "clearance": parameters.clearance_height.value,
                "retract": parameters.retract_height.value,
                "diameter": parameters.nominal_diameter.value,
                "pre_hole": parameters.pre_hole_diameter.value,
                "spindle": parameters.spindle_speed.value,
                "feed_per_revolution": parameters.feed_per_revolution.value,
                "dwell": parameters.dwell_seconds,
                "tolerance": parameters.tolerance.value,
            }
            for key, value in values.items():
                self._reaming_fields[key].setText(str(value))
            self.reaming_spindle_direction.setCurrentText(
                parameters.spindle_direction.value
            )
            self.reaming_retract_policy.setCurrentText(
                parameters.retract_policy.value
            )
            self.reaming_coolant.setCurrentText(parameters.coolant.value)
            self.tool.setCurrentIndex(self.tool.findData(
                str(operation.tool_assembly.assembly_id)
            ))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(
                    str(operation.machine_requirement.machine_id)
                ))
            self._update_reaming_preview()
        elif operation is not None and operation.strategy_key == "boring_v1":
            parameters = BoringStrategy.from_operation_parameters(
                operation.parameters
            )
            values = {
                "top": parameters.top_z.value,
                "final": parameters.final_depth.value,
                "clearance": parameters.clearance_height.value,
                "retract": parameters.retract_height.value,
                "finished_diameter": parameters.finished_bore_diameter.value,
                "pre_bore": parameters.pre_bore_diameter.value,
                "spindle": parameters.spindle_rpm.value,
                "feed_per_revolution": parameters.feed_per_revolution.value,
                "dwell": parameters.dwell_seconds,
                "tolerance": parameters.tolerance.value,
            }
            for key, value in values.items():
                self._boring_fields[key].setText(str(value))
            self.boring_spindle_direction.setCurrentText(
                parameters.spindle_direction.value
            )
            self.boring_retract_policy.setCurrentText(
                parameters.retract_policy.value
            )
            self.boring_coolant.setCurrentText(parameters.coolant.value)
            self.tool.setCurrentIndex(self.tool.findData(
                str(operation.tool_assembly.assembly_id)
            ))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(
                    str(operation.machine_requirement.machine_id)
                ))
            self._update_boring_preview()
            self._update_boring_tool_details()

    def set_error(self, text: str) -> None: self.error.setText(text)

    def apply_draft(self) -> None:
        """Apply the current draft through the existing validated commit path."""
        self._submit()

    def show_toolpath_metadata(self, value: ToolpathPresentation) -> None:
        """Show native-free artifact metadata; never expose runtime or NC data."""
        bounds = value.bounds
        minimum, maximum = bounds.minimum, bounds.maximum
        tapping = ""
        if value.strategy_key == "tapping_v1":
            diameter = (
                "?" if value.nominal_diameter is None
                else f"{value.nominal_diameter.value:g}"
            )
            pitch = "?" if value.pitch is None else f"{value.pitch.value:g}"
            rpm = (
                "?" if value.spindle_speed is None
                else f"{value.spindle_speed.value:g}"
            )
            depth = "?" if value.depth is None else f"{value.depth.value:g}"
            tapping = (
                f" · {value.thread_hand.value if value.thread_hand else '?'}"
                f" · {value.tapping_mode.value if value.tapping_mode else '?'}"
                f" · {value.hole_count} hole"
                f" · D{diameter} · P{pitch} · {rpm} RPM · depth {depth}"
            )
        if value.strategy_key == "reaming_v1":
            def number(item) -> str:
                return "?" if item is None else f"{item.value:g}"

            tapping = (
                f" · {value.hole_count} hole"
                f" · D{number(value.nominal_diameter)}"
                f" · pre-hole {number(value.pre_hole_diameter)}"
                f" · stock/side {number(value.stock_per_side)}"
                f" · feed/rev {number(value.feed_per_revolution)}"
                f" · feed/min {number(value.feed_per_minute)}"
                f" · {number(value.spindle_speed)} RPM"
                f" · {value.spindle_direction.value if value.spindle_direction else '?'}"
                f" · {value.retract_policy.value if value.retract_policy else '?'}"
                f" · {value.coolant_mode.value if value.coolant_mode else '?'}"
            )
        if value.strategy_key == "boring_v1":
            def number(item) -> str:
                return "?" if item is None else f"{item.value:g}"

            tapping = (
                f" · {value.hole_count} hole"
                f" · D{number(value.finished_bore_diameter)}"
                f" · pre-bore {number(value.pre_bore_diameter)}"
                f" · radial stock {number(value.radial_stock)}"
                f" · feed/rev {number(value.feed_per_revolution)}"
                f" · feed/min {number(value.feed_per_minute)}"
                f" · {number(value.spindle_speed)} RPM"
                f" · {value.spindle_direction.value if value.spindle_direction else '?'}"
                f" · {value.retract_policy.value if value.retract_policy else '?'}"
                f" · {value.coolant_mode.value if value.coolant_mode else '?'}"
                f" · tool {value.boring_tool_family.value if value.boring_tool_family else '?'}"
                f" · access {number(value.minimum_bore_diameter)}-"
                f"{number(value.maximum_bore_diameter)}"
            )
        self.toolpath_metadata.setText(
            f"{value.operation_id} · {value.strategy_key} · {value.pass_count} lượt · "
            f"[{minimum.x:.3f}, {minimum.y:.3f}, {minimum.z:.3f}] → "
            f"[{maximum.x:.3f}, {maximum.y:.3f}, {maximum.z:.3f}] · "
            f"{value.artifact_status.value.upper()}{tapping}"
        )

    def show_hole_source(
        self,
        source: HoleReference | HolePattern | None,
        resolved: bool | None = None,
    ) -> None:
        """Show a persistent single-hole or pattern binding without runtime IDs."""
        if source is None:
            self.status.setText("THIẾU LỖ · chưa liên kết mẫu lỗ")
            return
        state = "RESOLVED" if resolved else "STALE/INVALID"
        references = _hole_references(source)
        if isinstance(source, HolePattern):
            kinds = sorted({
                location.source_kind.value for location in source.locations
            })
            self.status.setText(
                f"Hình học: {translate_status(state)} · MẪU LỖ "
                f"{len(source.locations)} · "
                f"{', '.join(kinds)}"
            )
            return
        reference = references[0].reference
        self.status.setText(
            f"Hình học: {translate_status(state)} · {ui_text(reference.kind.value)} · "
            f"{reference.hint or reference.reference_id}"
        )

    def show_reference(
        self,
        reference: GeometryReference | None,
        resolved: bool | None = None,
        *,
        subject: str = "profile",
    ) -> None:
        if reference is None:
            localized_subject = ui_text(subject)
            self.status.setText(
                f"THIẾU {localized_subject.upper()} — "
                f"chưa liên kết {localized_subject}"
            )
        else:
            state = "RESOLVED" if resolved else "STALE/INVALID"
            self.status.setText(
                f"Hình học: {translate_status(state)} · "
                f"{ui_text(reference.kind.value)} · "
                f"{reference.hint or reference.reference_id}"
            )

    def pocket_state(self) -> dict[str, object]:
        """Capture only transient editor primitives; this state is never persisted."""
        return {
            "name": self._fields["name"].text(),
            "fields": {key: field.text() for key, field in self._pocket_fields.items()},
            "entry": self.pocket_entry.currentText(),
            "direction": self.pocket_direction.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_pocket_state(self, state: dict[str, object]) -> None:
        """Restore a Pocket draft by operation ID after tree selection changes."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._pocket_fields.items():
            field.setText(str(fields.get(key, "")))
        self.pocket_entry.setCurrentText(str(state.get("entry", "")))
        self.pocket_direction.setCurrentText(str(state.get("direction", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))

    def drilling_state(self) -> dict[str, object]:
        """Capture transient Drilling editor primitives without persistence."""
        return {
            "name": self._fields["name"].text(),
            "fields": {
                key: field.text() for key, field in self._drilling_fields.items()
            },
            "cycle": self.drilling_cycle.currentText(),
            "retract": self.drilling_retract.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_drilling_state(self, state: dict[str, object]) -> None:
        """Restore one unapplied Drilling draft by stable operation ID."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._drilling_fields.items():
            field.setText(str(fields.get(key, "")))
        self.drilling_cycle.setCurrentText(str(state.get("cycle", "")))
        self.drilling_retract.setCurrentText(str(state.get("retract", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))

    def tapping_state(self) -> dict[str, object]:
        """Capture one transient Tapping draft keyed outside the editor by operation ID."""
        return {
            "name": self._fields["name"].text(),
            "fields": {
                key: field.text() for key, field in self._tapping_fields.items()
            },
            "hand": self.tapping_hand.currentText(),
            "mode": self.tapping_mode.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_tapping_state(self, state: dict[str, object]) -> None:
        """Restore one unapplied Tapping draft by stable operation ID."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._tapping_fields.items():
            field.setText(str(fields.get(key, "")))
        self.tapping_hand.setCurrentText(str(state.get("hand", "")))
        self.tapping_mode.setCurrentText(str(state.get("mode", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))

    def reaming_state(self) -> dict[str, object]:
        """Capture one transient Reaming draft without derived or runtime state."""
        return {
            "name": self._fields["name"].text(),
            "fields": {
                key: field.text() for key, field in self._reaming_fields.items()
            },
            "spindle_direction": self.reaming_spindle_direction.currentText(),
            "retract_policy": self.reaming_retract_policy.currentText(),
            "coolant": self.reaming_coolant.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_reaming_state(self, state: dict[str, object]) -> None:
        """Restore one unapplied Reaming draft by stable operation ID."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._reaming_fields.items():
            field.setText(str(fields.get(key, "")))
        self.reaming_spindle_direction.setCurrentText(
            str(state.get("spindle_direction", ""))
        )
        self.reaming_retract_policy.setCurrentText(
            str(state.get("retract_policy", ""))
        )
        self.reaming_coolant.setCurrentText(str(state.get("coolant", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))
        self._update_reaming_preview()

    def boring_state(self) -> dict[str, object]:
        """Capture one transient Boring draft without derived/runtime values."""
        return {
            "name": self._fields["name"].text(),
            "fields": {
                key: field.text() for key, field in self._boring_fields.items()
            },
            "spindle_direction": self.boring_spindle_direction.currentText(),
            "retract_policy": self.boring_retract_policy.currentText(),
            "coolant": self.boring_coolant.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_boring_state(self, state: dict[str, object]) -> None:
        """Restore one unapplied Boring draft by stable operation ID."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._boring_fields.items():
            field.setText(str(fields.get(key, "")))
        self.boring_spindle_direction.setCurrentText(
            str(state.get("spindle_direction", ""))
        )
        self.boring_retract_policy.setCurrentText(
            str(state.get("retract_policy", ""))
        )
        self.boring_coolant.setCurrentText(str(state.get("coolant", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))
        self._update_boring_preview()
        self._update_boring_tool_details()

    def _submit(self) -> None:
        self._commit({**{key: field.text() for key, field in self._fields.items()},
                      **{key: field.text() for key, field in self._facing_fields.items()},
                      **{f"contour_{key}": field.text() for key, field in self._contour_fields.items()},
                      **{f"pocket_{key}": field.text() for key, field in self._pocket_fields.items()},
                      **{f"drilling_{key}": field.text() for key, field in self._drilling_fields.items()},
                      **{f"tapping_{key}": field.text() for key, field in self._tapping_fields.items()},
                      **{f"reaming_{key}": field.text() for key, field in self._reaming_fields.items()},
                      **{f"boring_{key}": field.text() for key, field in self._boring_fields.items()},
                      "setup_kind": self.setup_kind.currentText(), "stock_kind": self.stock_kind.currentText(),
                      "enabled": self.enabled.isChecked(), "boundary_source": self.boundary_source.currentText(),
                      "direction": self.direction.currentText(), "tool_id": self.tool.currentData(),
                      "machine_id": self.machine.currentData(), "profile_source": self.profile_source.currentText(),
                      "contour_side": self.contour_side.currentText(),
                      "contour_direction": self.contour_direction.currentText(),
                      "pocket_entry": self.pocket_entry.currentText(),
                      "pocket_direction": self.pocket_direction.currentText(),
                      "drilling_cycle": self.drilling_cycle.currentText(),
                      "drilling_retract": self.drilling_retract.currentText(),
                      "tapping_hand": self.tapping_hand.currentText(),
                      "tapping_mode": self.tapping_mode.currentText(),
                      "reaming_spindle_direction": self.reaming_spindle_direction.currentText(),
                      "reaming_retract_policy": self.reaming_retract_policy.currentText(),
                      "reaming_coolant": self.reaming_coolant.currentText(),
                      "boring_spindle_direction": self.boring_spindle_direction.currentText(),
                      "boring_retract_policy": self.boring_retract_policy.currentText(),
                      "boring_coolant": self.boring_coolant.currentText(),
                      "finishing_pass": self.finishing_pass.isChecked(),
                      "multiple_depth_passes": self.multiple_depth_passes.isChecked()})

    def has_valid_facing_draft(self, unit: LengthUnit) -> bool:
        return self.facing_draft(unit) is not None

    def facing_draft(self, unit: LengthUnit) -> FacingParameters | None:
        try:
            feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            value = FacingParameters(unit, FacingBoundarySource(self.boundary_source.currentText()),
                Length(float(self._facing_fields["top"].text()), unit),
                Length(float(self._facing_fields["target"].text()), unit),
                Length(float(self._facing_fields["stepdown"].text()), unit),
                Length(float(self._facing_fields["stepover"].text()), unit),
                Length(float(self._facing_fields["allowance"].text()), unit),
                Length(float(self._facing_fields["clearance"].text()), unit),
                Length(float(self._facing_fields["retract"].text()), unit),
                FeedRate(float(self._facing_fields["feed"].text()), feed_unit),
                FeedRate(float(self._facing_fields["plunge"].text()), feed_unit),
                SpindleSpeed(float(self._facing_fields["spindle"].text())),
                FacingCutDirection(self.direction.currentText()),
                float(self._facing_fields["angle"].text()),
                Length(float(self._facing_fields["overtravel"].text()), unit))
            return value if self.tool.currentData() is not None and self.machine.currentData() is not None else None
        except (TypeError, ValueError):
            return None

    def contour_draft(self, unit: LengthUnit) -> ContourParameters | None:
        try:
            feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            value = ContourParameters(
                unit, ContourProfileSource(self.profile_source.currentText()),
                ContourSide(self.contour_side.currentText()),
                Length(float(self._contour_fields["top"].text()), unit),
                Length(float(self._contour_fields["final"].text()), unit),
                Length(float(self._contour_fields["stepdown"].text()), unit),
                Length(float(self._contour_fields["radial"].text()), unit),
                Length(float(self._contour_fields["axial"].text()), unit),
                Length(float(self._contour_fields["clearance"].text()), unit),
                Length(float(self._contour_fields["retract"].text()), unit),
                FeedRate(float(self._contour_fields["feed"].text()), feed_unit),
                FeedRate(float(self._contour_fields["plunge"].text()), feed_unit),
                SpindleSpeed(float(self._contour_fields["spindle"].text())),
                ContourCutDirection(self.contour_direction.currentText()),
                lead_length=Length(float(self._contour_fields["lead"].text()), unit),
                finishing_pass=self.finishing_pass.isChecked(),
                multiple_depth_passes=self.multiple_depth_passes.isChecked(),
            )
            return value if self.tool.currentData() is not None and self.machine.currentData() is not None else None
        except (TypeError, ValueError):
            return None

    def pocket_draft(
        self,
        unit: LengthUnit,
        reference: GeometryReference | None,
    ) -> PocketStrategy | None:
        try:
            if reference is None:
                return None
            feed_unit = (FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM
                         else FeedUnit.INCH_PER_MINUTE)
            return PocketStrategy(
                unit,
                PocketGeometryInput(reference, unit),
                PocketDepthDefinition(
                    unit,
                    Length(float(self._pocket_fields["top"].text()), unit),
                    Length(float(self._pocket_fields["bottom"].text()), unit),
                    Length(float(self._pocket_fields["axial"].text()), unit),
                ),
                Length(float(self._pocket_fields["stepover"].text()), unit),
                Length(float(self._pocket_fields["stepdown"].text()), unit),
                Length(float(self._pocket_fields["allowance"].text()), unit),
                Length(float(self._pocket_fields["clearance"].text()), unit),
                Length(float(self._pocket_fields["retract"].text()), unit),
                FeedRate(float(self._pocket_fields["feed"].text()), feed_unit),
                FeedRate(float(self._pocket_fields["plunge"].text()), feed_unit),
                SpindleSpeed(float(self._pocket_fields["spindle"].text())),
                PocketEntryPolicy(self.pocket_entry.currentText()),
                PocketCuttingDirection(self.pocket_direction.currentText()),
                Length(float(self._pocket_fields["tolerance"].text()), unit),
            )
        except (TypeError, ValueError):
            return None

    def drilling_draft(
        self,
        unit: LengthUnit,
        hole_source: HoleReference | HolePattern | None,
    ) -> DrillingStrategy | None:
        try:
            if hole_source is None or hole_source.unit is not unit:
                return None
            feed_unit = (
                FeedUnit.MM_PER_MINUTE
                if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            )
            top_z = float(self._drilling_fields["top"].text())
            depth = float(self._drilling_fields["depth"].text())
            cycle = DrillingCycle(self.drilling_cycle.currentText())
            peck = (
                Length(float(self._drilling_fields["peck"].text()), unit)
                if cycle is DrillingCycle.PECK_DRILL else None
            )
            return DrillingStrategy(
                unit=unit,
                geometry=DrillGeometryInput(hole_source, unit),
                depth=DrillDepthDefinition(
                    unit, Length(top_z, unit), Length(top_z - depth, unit),
                ),
                cycle=cycle,
                clearance_height=Length(
                    float(self._drilling_fields["clearance"].text()), unit,
                ),
                retract_height=Length(
                    float(self._drilling_fields["retract"].text()), unit,
                ),
                feed_rate=FeedRate(
                    float(self._drilling_fields["feed"].text()), feed_unit,
                ),
                spindle_speed=SpindleSpeed(
                    float(self._drilling_fields["spindle"].text())
                ),
                dwell_seconds=float(self._drilling_fields["dwell"].text()),
                peck_depth=peck,
                retract_policy=DrillRetractPolicy(
                    self.drilling_retract.currentText()
                ),
                approach_policy=(
                    DrillApproachPolicy.RAPID_CLEARANCE_FEED_RETRACT
                ),
                tolerance=Length(
                    float(self._drilling_fields["tolerance"].text()), unit,
                ),
            )
        except (TypeError, ValueError):
            return None

    def tapping_draft(
        self,
        unit: LengthUnit,
        hole_source: HoleReference | HolePattern | None,
    ) -> TappingStrategy | None:
        """Build an immutable Tapping draft without mutating the project domain."""
        self._tapping_draft_error = ""
        try:
            if hole_source is None or hole_source.unit is not unit:
                return None
            dwell_text = self._tapping_fields["dwell"].text().strip()
            return TappingStrategy(
                unit=unit,
                geometry=DrillGeometryInput(hole_source, unit),
                depth=DrillDepthDefinition(
                    unit,
                    Length(float(self._tapping_fields["top"].text()), unit),
                    Length(float(self._tapping_fields["final"].text()), unit),
                ),
                nominal_diameter=Length(
                    float(self._tapping_fields["diameter"].text()), unit,
                ),
                pitch=Length(
                    float(self._tapping_fields["pitch"].text()), unit,
                ),
                hand=TappingHand(self.tapping_hand.currentText()),
                spindle_speed=SpindleSpeed(
                    float(self._tapping_fields["spindle"].text())
                ),
                clearance_height=Length(
                    float(self._tapping_fields["clearance"].text()), unit,
                ),
                retract_height=Length(
                    float(self._tapping_fields["retract"].text()), unit,
                ),
                synchronization_policy=TappingSynchronizationPolicy(
                    self.tapping_mode.currentText()
                ),
                dwell_seconds=0.0 if not dwell_text else float(dwell_text),
                tolerance=Length(
                    float(self._tapping_fields["tolerance"].text()), unit,
                ),
            )
        except TappingValidationError as error:
            self._tapping_draft_error = f"{error.code.value}: {error}"
            return None
        except DrillValidationError as error:
            code = (
                "tap.depth_invalid"
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else "tap.invalid_parameters"
            )
            self._tapping_draft_error = f"{code}: {error}"
            return None
        except (TypeError, ValueError) as error:
            self._tapping_draft_error = (
                f"tap.invalid_parameters: {error or 'Thông số Tapping không hợp lệ.'}"
            )
            return None

    @property
    def tapping_draft_error(self) -> str:
        """Return the last stable diagnostic produced while parsing a Tapping draft."""
        return self._tapping_draft_error

    def reaming_draft(
        self,
        unit: LengthUnit,
        hole_source: HoleReference | HolePattern | None,
    ) -> ReamingStrategy | None:
        """Build an immutable Reaming draft without mutating project state."""
        self._reaming_draft_error = ""
        try:
            if hole_source is None or hole_source.unit is not unit:
                self._reaming_draft_error = (
                    "ream.geometry_missing: Reaming thiếu hole geometry hợp lệ."
                )
                return None
            pre_hole_text = self._reaming_fields["pre_hole"].text().strip()
            if not pre_hole_text:
                self._reaming_draft_error = (
                    "ream.prehole_missing: Pre-hole diameter là bắt buộc."
                )
                return None
            dwell_text = self._reaming_fields["dwell"].text().strip()
            feed_unit = (
                FeedUnit.MM_PER_REVOLUTION
                if unit is LengthUnit.MM
                else FeedUnit.INCH_PER_REVOLUTION
            )
            return ReamingStrategy(
                unit=unit,
                geometry=DrillGeometryInput(hole_source, unit),
                depth=DrillDepthDefinition(
                    unit,
                    Length(float(self._reaming_fields["top"].text()), unit),
                    Length(float(self._reaming_fields["final"].text()), unit),
                ),
                nominal_diameter=Length(
                    float(self._reaming_fields["diameter"].text()), unit,
                ),
                pre_hole_diameter=Length(float(pre_hole_text), unit),
                spindle_speed=SpindleSpeed(
                    float(self._reaming_fields["spindle"].text())
                ),
                feed_per_revolution=FeedRate(
                    float(self._reaming_fields["feed_per_revolution"].text()),
                    feed_unit,
                ),
                clearance_height=Length(
                    float(self._reaming_fields["clearance"].text()), unit,
                ),
                retract_height=Length(
                    float(self._reaming_fields["retract"].text()), unit,
                ),
                spindle_direction=SpindleDirection(
                    self.reaming_spindle_direction.currentText()
                ),
                retract_policy=ReamingRetractPolicy(
                    self.reaming_retract_policy.currentText()
                ),
                coolant=ReamingCoolantMode(self.reaming_coolant.currentText()),
                dwell_seconds=0.0 if not dwell_text else float(dwell_text),
                tolerance=Length(
                    float(self._reaming_fields["tolerance"].text()), unit,
                ),
            )
        except ReamingValidationError as error:
            self._reaming_draft_error = f"{error.code.value}: {error}"
            return None
        except DrillValidationError as error:
            code = (
                "ream.depth_invalid"
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else "ream.invalid_parameters"
            )
            self._reaming_draft_error = f"{code}: {error}"
            return None
        except (TypeError, ValueError) as error:
            self._reaming_draft_error = (
                f"ream.invalid_parameters: "
                f"{error or 'Thông số Reaming không hợp lệ.'}"
            )
            return None

    @property
    def reaming_draft_error(self) -> str:
        """Return the last stable diagnostic produced while parsing Reaming."""
        return self._reaming_draft_error

    def _update_reaming_preview(self, *_args: object) -> None:
        """Update derived-only values without constructing or mutating an operation."""
        try:
            nominal_text = self._reaming_fields["diameter"].text().strip()
            pre_hole_text = self._reaming_fields["pre_hole"].text().strip()
            if not pre_hole_text:
                self.reaming_derived.setText(
                    "ream.prehole_missing · bắt buộc khai báo đường kính lỗ có trước"
                )
                return
            nominal = float(nominal_text)
            pre_hole = float(pre_hole_text)
            tolerance = float(self._reaming_fields["tolerance"].text())
            if not math.isfinite(nominal) or nominal <= 0.0:
                raise ValueError("ream.invalid_parameters · nominal diameter phải > 0")
            if (
                not math.isfinite(pre_hole)
                or pre_hole <= 0.0
                or pre_hole >= nominal
            ):
                raise ValueError(
                    "ream.prehole_invalid · pre-hole phải > 0 và nhỏ hơn nominal"
                )
            stock = (nominal - pre_hole) / 2.0
            if (
                not math.isfinite(tolerance)
                or tolerance <= 0.0
                or stock <= tolerance
                or stock >= nominal / 2.0 - tolerance
            ):
                raise ValueError(
                    "ream.stock_invalid · stock mỗi phía ngoài giới hạn tolerance"
                )
            rpm = float(self._reaming_fields["spindle"].text())
            feed_per_revolution = float(
                self._reaming_fields["feed_per_revolution"].text()
            )
            top = float(self._reaming_fields["top"].text())
            final = float(self._reaming_fields["final"].text())
            derived = (rpm, feed_per_revolution, top, final)
            if any(not math.isfinite(value) for value in derived):
                raise ValueError("ream.invalid_parameters · derived input không hữu hạn")
            if rpm <= 0.0 or feed_per_revolution <= 0.0:
                raise ValueError("ream.invalid_parameters · RPM/feed mỗi vòng phải > 0")
            self.reaming_derived.setText(
                f"Phôi/mặt bên: {stock:g} · Lượng chạy dao/phút: "
                f"{rpm * feed_per_revolution:g} · Chiều sâu cắt: {top - final:g}"
            )
        except ValueError as error:
            self.reaming_derived.setText(str(error) or "ream.invalid_parameters")

    def boring_draft(
        self,
        unit: LengthUnit,
        hole_source: HoleReference | HolePattern | None,
    ) -> BoringStrategy | None:
        """Build an immutable Boring draft without mutating project state."""
        self._boring_draft_error = ""
        try:
            if hole_source is None or hole_source.unit is not unit:
                self._boring_draft_error = (
                    "bore.geometry_missing: Boring thiếu hole geometry hợp lệ."
                )
                return None
            pre_bore_text = self._boring_fields["pre_bore"].text().strip()
            if not pre_bore_text:
                self._boring_draft_error = (
                    "bore.prebore_missing: Pre-bore diameter là bắt buộc."
                )
                return None
            dwell_text = self._boring_fields["dwell"].text().strip()
            feed_unit = (
                FeedUnit.MM_PER_REVOLUTION
                if unit is LengthUnit.MM
                else FeedUnit.INCH_PER_REVOLUTION
            )
            return BoringStrategy(
                unit=unit,
                geometry=DrillGeometryInput(hole_source, unit),
                depth=DrillDepthDefinition(
                    unit,
                    Length(float(self._boring_fields["top"].text()), unit),
                    Length(float(self._boring_fields["final"].text()), unit),
                ),
                finished_bore_diameter=Length(
                    float(self._boring_fields["finished_diameter"].text()),
                    unit,
                ),
                pre_bore_diameter=Length(float(pre_bore_text), unit),
                spindle_rpm=SpindleSpeed(
                    float(self._boring_fields["spindle"].text())
                ),
                feed_per_revolution=FeedRate(
                    float(self._boring_fields["feed_per_revolution"].text()),
                    feed_unit,
                ),
                clearance_height=Length(
                    float(self._boring_fields["clearance"].text()), unit,
                ),
                retract_height=Length(
                    float(self._boring_fields["retract"].text()), unit,
                ),
                spindle_direction=SpindleDirection(
                    self.boring_spindle_direction.currentText()
                ),
                retract_policy=BoringRetractPolicy(
                    self.boring_retract_policy.currentText()
                ),
                coolant=BoringCoolantMode(self.boring_coolant.currentText()),
                dwell_seconds=0.0 if not dwell_text else float(dwell_text),
                tolerance=Length(
                    float(self._boring_fields["tolerance"].text()), unit,
                ),
            )
        except BoringValidationError as error:
            self._boring_draft_error = f"{error.code.value}: {error}"
            return None
        except DrillValidationError as error:
            code = (
                "bore.depth_invalid"
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else "bore.invalid_parameters"
            )
            self._boring_draft_error = f"{code}: {error}"
            return None
        except (TypeError, ValueError) as error:
            self._boring_draft_error = (
                f"bore.invalid_parameters: "
                f"{error or 'Thông số Boring không hợp lệ.'}"
            )
            return None

    @property
    def boring_draft_error(self) -> str:
        """Return the stable diagnostic produced while parsing Boring."""
        return self._boring_draft_error

    def _update_boring_preview(self, *_args: object) -> None:
        """Update derived Boring values without storing them as source data."""
        try:
            finished_text = self._boring_fields["finished_diameter"].text().strip()
            pre_bore_text = self._boring_fields["pre_bore"].text().strip()
            if not pre_bore_text:
                self.boring_derived.setText(
                    "bore.prebore_missing · bắt buộc khai báo đường kính lỗ sơ bộ"
                )
                return
            finished = float(finished_text)
            pre_bore = float(pre_bore_text)
            tolerance = float(self._boring_fields["tolerance"].text())
            if not math.isfinite(finished) or finished <= 0.0:
                raise ValueError(
                    "bore.invalid_parameters · finished diameter phải > 0"
                )
            if (
                not math.isfinite(pre_bore)
                or pre_bore <= 0.0
                or pre_bore >= finished
            ):
                raise ValueError(
                    "bore.prebore_invalid · pre-bore phải > 0 và nhỏ hơn finished"
                )
            stock = (finished - pre_bore) / 2.0
            if (
                not math.isfinite(tolerance)
                or tolerance <= 0.0
                or stock <= tolerance
                or stock >= finished / 2.0 - tolerance
            ):
                raise ValueError(
                    "bore.stock_invalid · radial stock ngoài giới hạn tolerance"
                )
            rpm = float(self._boring_fields["spindle"].text())
            feed_per_revolution = float(
                self._boring_fields["feed_per_revolution"].text()
            )
            top = float(self._boring_fields["top"].text())
            final = float(self._boring_fields["final"].text())
            derived = (rpm, feed_per_revolution, top, final)
            if any(not math.isfinite(value) for value in derived):
                raise ValueError(
                    "bore.invalid_parameters · derived input không hữu hạn"
                )
            if rpm <= 0.0 or feed_per_revolution <= 0.0:
                raise ValueError(
                    "bore.invalid_parameters · RPM/feed mỗi vòng phải > 0"
                )
            self.boring_derived.setText(
                f"Lượng dư hướng kính: {stock:g} · Lượng chạy dao/phút: "
                f"{rpm * feed_per_revolution:g} · Chiều sâu cắt: {top - final:g}"
            )
        except ValueError as error:
            self.boring_derived.setText(str(error) or "bore.invalid_parameters")

    def _update_boring_tool_details(self, *_args: object) -> None:
        """Expose the current BORING_BAR access envelope and provenance read-only."""
        assembly = self._assemblies_by_id.get(str(self.tool.currentData()))
        if assembly is None:
            self.boring_tool_details.setText("bore.tool_missing · chưa chọn cụm Tool")
            return
        tool = self._tool_definitions_by_id.get(assembly.tool_id)
        holder = self._holders_by_id.get(assembly.holder_id)
        if (
            tool is None
            or tool.family is not ToolFamily.BORING_BAR
            or not isinstance(tool.cutting_geometry, BoringBarGeometry)
        ):
            self.boring_tool_details.setText(
                "bore.unsupported_tool · chỉ chấp nhận BORING_BAR"
            )
            return
        geometry = tool.cutting_geometry
        current = (
            tool.revision == assembly.expected_tool_revision
            and tool.content_fingerprint == assembly.expected_tool_fingerprint
            and holder is not None
            and holder.revision == assembly.expected_holder_revision
            and holder.content_fingerprint == assembly.expected_holder_fingerprint
        )
        holder_text = "MISSING" if holder is None else holder.name
        holder_revision = "?" if holder is None else str(holder.revision.value)
        holder_fingerprint = (
            "?" if holder is None else holder.content_fingerprint.digest[:12]
        )
        self.boring_tool_details.setText(
            f"{tool.family.value} · D nhỏ nhất "
            f"{geometry.minimum_bore_diameter.value:g} · D lớn nhất "
            f"{geometry.maximum_bore_diameter.value:g} · chiều dài cắt "
            f"{geometry.cutting_length.value:g} · tay {geometry.hand.value} · "
            f"đơn vị {tool.unit.value} · D cán {tool.shank.diameter.value:g} · "
            f"chiều dài dùng được {tool.usable_length.value:g} · nhô dao "
            f"{assembly.stickout.value:g} · cụm Tool bản sửa đổi "
            f"{assembly.revision.value}/fp {assembly.content_fingerprint.digest[:12]} · "
            f"Tool bản sửa đổi {tool.revision.value}/fp "
            f"{tool.content_fingerprint.digest[:12]} · Holder {holder_text} "
            f"bản sửa đổi {holder_revision}/fp {holder_fingerprint} · ảnh chụp "
            f"{translate_status('CURRENT' if current else 'STALE')}"
        )


def _length_unit(session: ProjectSession | None) -> LengthUnit:
    return LengthUnit.INCH if session and session.manifest.units is UnitSystem.INCH else LengthUnit.MM


def _default_setup(source_id: UUID, unit: LengthUnit, number: int) -> Setup:
    wcs = WcsFrame.identity(unit)
    reference = GeometryReference(GeometryReferenceId.new(), HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, source_id, GeometryReferenceKind.DOCUMENT,
        GeometryRepresentationKind.BREP, GeometryFingerprint.from_payload({"source_id": str(source_id)}), Revision(0))
    return Setup(SetupId.new(), f"Setup {number}", SetupKind.MILL, wcs, WorkOffset("G54", 1),
                 BoxStock(Length(100, unit), Length(100, unit), Length(50, unit), wcs), reference, SourceScope(source_id))


def _default_facing_parameters(setup: Setup) -> FacingParameters:
    if not isinstance(setup.stock, BoxStock):
        raise ValueError("Facing mặc định yêu cầu Stock BOX.")
    unit = setup.wcs.origin.unit
    feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
    top = setup.stock.size_z.value
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    return FacingParameters(unit, FacingBoundarySource.STOCK_BOX, Length(top, unit),
        Length(top - scale, unit), Length(scale, unit), Length(5 * scale, unit), Length(0, unit),
        Length(top + 5 * scale, unit), Length(top + 2 * scale, unit),
        FeedRate(500 * scale, feed_unit), FeedRate(100 * scale, feed_unit), SpindleSpeed(1000),
        FacingCutDirection.BIDIRECTIONAL, 0.0, Length(scale, unit))


def _default_contour_parameters(setup: Setup) -> ContourParameters:
    if not isinstance(setup.stock, BoxStock):
        raise ValueError("2D Contour mặc định yêu cầu Stock BOX.")
    unit = setup.wcs.origin.unit
    feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
    top = setup.stock.size_z.value
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    return ContourParameters(
        unit, ContourProfileSource.PLANAR_FACE_OUTER, ContourSide.ON,
        Length(top, unit), Length(top - scale, unit), Length(scale, unit),
        Length(0, unit), Length(0, unit), Length(top + 5 * scale, unit),
        Length(top + 2 * scale, unit), FeedRate(500 * scale, feed_unit),
        FeedRate(100 * scale, feed_unit), SpindleSpeed(1000),
        ContourCutDirection.CLIMB, lead_length=Length(scale, unit),
    )


def _default_pocket_parameters(setup: Setup) -> OperationParameterSet:
    """Create an unbound but schema-complete Pocket draft parameter set."""
    if not isinstance(setup.stock, BoxStock):
        raise ValueError("Pocket mặc định yêu cầu Stock BOX.")
    unit = setup.wcs.origin.unit
    top = setup.stock.size_z.value
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    return OperationParameterSet(
        "pocket_2_5d",
        1,
        (
            ("unit", unit.value),
            ("top_z", top),
            ("bottom_z", top - scale),
            ("axial_allowance", 0.0),
            ("stepover", 4.0 * scale),
            ("stepdown", scale),
            ("radial_stock_allowance", 0.0),
            ("clearance_height", top + 5.0 * scale),
            ("retract_height", top + 2.0 * scale),
            ("cutting_feed_rate", 500.0 * scale),
            ("plunge_feed_rate", 100.0 * scale),
            ("spindle_speed", 1000.0),
            ("entry_policy", PocketEntryPolicy.VERTICAL_PLUNGE.value),
            ("cutting_direction", PocketCuttingDirection.CLIMB.value),
            ("tolerance", 1.0e-7 * scale),
        ),
    )


def _default_drilling_strategy(
    setup: Setup,
    hole_source: HoleReference | HolePattern,
) -> DrillingStrategy:
    """Compatibility wrapper over the canonical Stage16A working-copy defaults."""
    return default_drilling_creation_strategy(setup, hole_source)


def _default_tapping_strategy(
    setup: Setup,
    hole_source: HoleReference | HolePattern,
) -> TappingStrategy:
    """Create a conservative RH rigid Tapping draft at the selected hole plane."""
    unit = setup.wcs.origin.unit
    if hole_source.unit is not unit:
        raise ValueError("tap.invalid_parameters: Hole source unit không khớp WCS.")
    plane_origin = (
        hole_source.plane_origin
        if isinstance(hole_source, HoleReference)
        else hole_source.locations[0].plane_origin
    )
    delta = Vector3(
        plane_origin.x - setup.wcs.origin.x,
        plane_origin.y - setup.wcs.origin.y,
        plane_origin.z - setup.wcs.origin.z,
    )
    top_z = delta.dot(setup.wcs.z_axis)
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    return TappingStrategy(
        unit=unit,
        geometry=DrillGeometryInput(hole_source, unit),
        depth=DrillDepthDefinition(
            unit,
            Length(top_z, unit),
            Length(top_z - 10.0 * scale, unit),
        ),
        nominal_diameter=Length(8.0 * scale, unit),
        pitch=Length(1.25 * scale, unit),
        hand=TappingHand.RIGHT_HAND_TAP,
        spindle_speed=SpindleSpeed(500.0),
        clearance_height=Length(top_z + 8.0 * scale, unit),
        retract_height=Length(top_z + 3.0 * scale, unit),
        synchronization_policy=TappingSynchronizationPolicy.RIGID,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7 * scale, unit),
    )


def _default_reaming_strategy(
    setup: Setup,
    hole_source: HoleReference | HolePattern,
) -> ReamingStrategy:
    """Create a conservative D8 Reaming draft with explicit D7.8 pre-hole."""
    unit = setup.wcs.origin.unit
    if hole_source.unit is not unit:
        raise ValueError("ream.invalid_parameters: Hole source unit không khớp WCS.")
    plane_origin = (
        hole_source.plane_origin
        if isinstance(hole_source, HoleReference)
        else hole_source.locations[0].plane_origin
    )
    delta = Vector3(
        plane_origin.x - setup.wcs.origin.x,
        plane_origin.y - setup.wcs.origin.y,
        plane_origin.z - setup.wcs.origin.z,
    )
    top_z = delta.dot(setup.wcs.z_axis)
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    feed_unit = (
        FeedUnit.MM_PER_REVOLUTION
        if unit is LengthUnit.MM else FeedUnit.INCH_PER_REVOLUTION
    )
    return ReamingStrategy(
        unit=unit,
        geometry=DrillGeometryInput(hole_source, unit),
        depth=DrillDepthDefinition(
            unit,
            Length(top_z, unit),
            Length(top_z - 10.0 * scale, unit),
        ),
        nominal_diameter=Length(8.0 * scale, unit),
        pre_hole_diameter=Length(7.8 * scale, unit),
        spindle_speed=SpindleSpeed(500.0),
        feed_per_revolution=FeedRate(0.1 * scale, feed_unit),
        clearance_height=Length(top_z + 8.0 * scale, unit),
        retract_height=Length(top_z + 3.0 * scale, unit),
        spindle_direction=SpindleDirection.CLOCKWISE,
        retract_policy=ReamingRetractPolicy.CONTROLLED_FEED,
        coolant=ReamingCoolantMode.OFF,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7 * scale, unit),
    )


def _default_boring_strategy(
    setup: Setup,
    hole_source: HoleReference | HolePattern,
) -> BoringStrategy:
    """Create a conservative D20 Boring draft with explicit D18 pre-bore."""
    unit = setup.wcs.origin.unit
    if hole_source.unit is not unit:
        raise ValueError("bore.invalid_parameters: Hole source unit không khớp WCS.")
    plane_origin = (
        hole_source.plane_origin
        if isinstance(hole_source, HoleReference)
        else hole_source.locations[0].plane_origin
    )
    delta = Vector3(
        plane_origin.x - setup.wcs.origin.x,
        plane_origin.y - setup.wcs.origin.y,
        plane_origin.z - setup.wcs.origin.z,
    )
    top_z = delta.dot(setup.wcs.z_axis)
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    feed_unit = (
        FeedUnit.MM_PER_REVOLUTION
        if unit is LengthUnit.MM else FeedUnit.INCH_PER_REVOLUTION
    )
    return BoringStrategy(
        unit=unit,
        geometry=DrillGeometryInput(hole_source, unit),
        depth=DrillDepthDefinition(
            unit,
            Length(top_z, unit),
            Length(top_z - 10.0 * scale, unit),
        ),
        finished_bore_diameter=Length(20.0 * scale, unit),
        pre_bore_diameter=Length(18.0 * scale, unit),
        spindle_rpm=SpindleSpeed(600.0),
        feed_per_revolution=FeedRate(0.1 * scale, feed_unit),
        clearance_height=Length(top_z + 8.0 * scale, unit),
        retract_height=Length(top_z + 3.0 * scale, unit),
        spindle_direction=SpindleDirection.CLOCKWISE,
        retract_policy=BoringRetractPolicy.CONTROLLED_FEED,
        coolant=BoringCoolantMode.OFF,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7 * scale, unit),
    )


def _hole_strategy(
    operation: Operation,
) -> DrillingStrategy | TappingStrategy | ReamingStrategy | BoringStrategy:
    """Decode one supported hole strategy without changing its binding."""
    if operation.strategy_key == "tapping_v1":
        return TappingStrategy.from_operation_parameters(operation.parameters)
    if operation.strategy_key == "reaming_v1":
        return ReamingStrategy.from_operation_parameters(operation.parameters)
    if operation.strategy_key == "boring_v1":
        return BoringStrategy.from_operation_parameters(operation.parameters)
    if operation.strategy_key == "drilling_v1":
        return DrillingStrategy.from_operation_parameters(operation.parameters)
    raise ValueError("Operation is not a supported hole strategy")


def _hole_references(
    source: HoleReference | HolePattern,
) -> tuple[HoleReference, ...]:
    if isinstance(source, HoleReference):
        return (source,)
    return tuple(
        location.reference
        for location in source.locations
        if location.reference is not None
    )


def _hole_geometry_inputs(
    existing: tuple[OperationGeometryInput, ...],
    source: HoleReference | HolePattern,
) -> tuple[OperationGeometryInput, ...]:
    existing_by_reference = {
        value.reference.reference_id: value for value in existing
    }
    return tuple(
        OperationGeometryInput(
            (
                previous.input_id
                if (previous := existing_by_reference.get(
                    hole.reference.reference_id
                )) is not None
                else GeometryInputId.new()
            ),
            GeometryInputRole.DRIVE_GEOMETRY,
            hole.reference,
            True,
            hole.reference.kind,
            index,
        )
        for index, hole in enumerate(_hole_references(source))
    )


def _operation_matches_hole_source(
    operation: Operation,
    source: HoleReference | HolePattern,
) -> bool:
    expected = tuple(hole.reference for hole in _hole_references(source))
    supplied = tuple(
        item.reference for item in sorted(
            operation.geometry_inputs,
            key=lambda value: value.selection_order,
        )
    )
    return supplied == expected


def _find_drilling_assembly(snapshot, family: ToolFamily):
    tools = {value.tool_id: value for value in snapshot.tool_definitions}
    return next((
        assembly for assembly in snapshot.tool_assemblies
        if tools.get(assembly.tool_id) is not None
        and tools[assembly.tool_id].family is family
    ), None)


def _parallel_surface_key(surface: object) -> tuple[object, ...]:
    """Return the persisted face identity while keeping native OCP handles out."""
    geometry = getattr(surface, "geometry", None)
    if geometry is None:
        raise TypeError("Parallel selector returned an invalid face reference")
    return (
        geometry.source_id,
        geometry.scheme,
        geometry.scheme_version,
        geometry.occurrence_path,
        geometry.subshape_selector,
        geometry.expected_source_revision,
    )


def _active_job(snapshot):
    return next(job for job in snapshot.jobs if job.job_id == snapshot.active_job_id)


from PySide6.QtWidgets import QTreeWidgetItemIterator  # noqa: E402
