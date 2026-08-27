"""Main application window and Stage 1 workspace composition."""

from __future__ import annotations

from dataclasses import replace
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import (
    QAbstractItemModel,
    QByteArray,
    QRect,
    QSize,
    QTimer,
    Qt,
    Slot,
)
from shiboken6 import isValid
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QGuiApplication,
    QResizeEvent,
    QShowEvent,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QDockWidget,
    QColorDialog,
    QHeaderView,
    QLabel,
    QInputDialog,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabBar,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.export_service import CadExportService
from hms_cadcam.core.paths import ApplicationPathsService
from hms_cadcam.core.hms_backup import HmsBackupService, HmsRestoreService
from hms_cadcam.core.storage_layout import StorageBootstrapService
from hms_cadcam.core.user_profiles import (
    ProfileError,
    ProfileSwitchReport,
    UserProfile,
    UserProfileService,
)
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cam.adapters import (
    OcpContourProfileResolver,
    OcpCam3DSurfaceAdapter,
    OcpDrillingGeometryResolver,
    OcpPlanarFaceResolver,
)
from hms_cadcam.cam.adapters.ocp_simulation import OcpSimulationCollisionBackend
from hms_cadcam.cam.simulation import CollisionBackend, CollisionScene, SimulationInputSnapshot
from hms_cadcam.cam.application import DrillingGeometryResolver, PocketGeometryResolver
from hms_cadcam.cam.adapters.ocp_cam3d_preview import OcpCam3DPreviewTessellator
from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DPreviewCoordinator,
    Cam3DPreviewResult,
)
from hms_cadcam.cam.application.cam3d_request import (
    Cam3DActiveSetupContext,
    Cam3DCalculationOwnershipKey,
)
from hms_cadcam.cam.application.cam3d_workflow import (
    Cam3DPreviewWorkflow,
    Cam3DWorkflowInput,
    Cam3DWorkflowState,
    Cam3DWorkflowStatus,
)
from hms_cadcam.cam.domain import (
    DrillDepthDefinition, DrillGeometryInput, GeometryReference,
    GeometryResolutionStatus, HolePattern, HoleReference,
    LengthUnit, PocketGeometryInput, ResolvedContourProfile,
    ResolvedDrillingGeometry, ResolvedMachiningGeometry, ResolvedPocketGeometry,
    Vector3,
    Revision,
)
from hms_cadcam.cam.domain.setup import CylinderStock
from hms_cadcam.cam.cam3d import CamSurfaceReference, CamSurfaceRole
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectionApplicationService,
    Cam3DSelectionRole,
    Cam3DSelectionSource,
)
from hms_cadcam.cad.measurement import (
    AreaMeasurement,
    BoundingDimensions,
    CircularEdgeMeasurement,
    DistanceMeasurement,
    EdgeLengthMeasurement,
    MeasurementResult,
    PointCoordinates,
    VolumeMeasurement,
)
from hms_cadcam.cad.models import (
    CadDocumentId,
    CadDocumentMetadata,
    CadDocumentTree,
    CadObjectId,
    CadObjectNode,
)
from hms_cadcam.project.models import ProjectSession
from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.workspace import DocumentMode, WorkspaceState
from hms_cadcam.ui.cad_controller import CadUiController
from hms_cadcam.ui.cad_export import CadExportUiController
from hms_cadcam.ui.cad_export_status import CadExportStatusSurface
from hms_cadcam.ui.cad_loading_status import CadLoadingStatusSurface
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.ui.cam3d_function_panel import Cam3DFunctionPanel
from hms_cadcam.cam.application.cam3d_editor import (
    Cam3DEditorField,
    Cam3DProjectContext,
    Cam3DToolAssemblyChoice,
    Cam3DToolProfileChoice,
)
from hms_cadcam.ui.cam3d_editor_binding import Cam3DEditorBindingController
from hms_cadcam.ui.cam3d_preview_worker import Cam3DQtWorkerBridge
from hms_cadcam.ui.cam3d_viewport import Cam3DViewportPreviewSink
from hms_cadcam.ui.cam_geometry_adapter import (
    GeometryPickError,
)
from hms_cadcam.ui.project_controller import ProjectUiController
from hms_cadcam.ui.geometry_transfer_ui import (
    IncomingGeometryNotificationBar,
    IncomingGeometryPanel,
)
from hms_cadcam.ui.cam_function_popup import CAMFunctionPopupHost
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ai_assist.controller import AiAssistController
from hms_cadcam.ai_assist.resources import WindowsResourceProvider
from hms_cadcam.ai_assist.settings import AiAssistSettingsService
from hms_cadcam.ai_assist.stage13b_settings import AdvisorSettingsService
from hms_cadcam.ui.lathe_adapters import LatheSelectionContext
from hms_cadcam.ui.lathe_session import (
    LatheSessionController,
    LatheUiContext,
)
from hms_cadcam.ui.lathe_toolpath import LatheViewportPreviewSink
from hms_cadcam.ui.lathe_simulation import LatheSimulationWindowManager
from hms_cadcam.ui.lathe_workspace import LatheWorkspace
from hms_cadcam.ui.post_assembly_panel import (
    PostAssemblyProjectionAdapter,
    UnifiedPostAssemblyPanel,
)
from hms_cadcam.ui.post_studio import PostProcessorStudioPanel
from hms_cadcam.ui.ribbon import (
    RibbonMetrics,
    RibbonWidget,
    ribbon_menu_style_sheet,
)
from hms_cadcam.ui.theme import APP_STYLE
from hms_cadcam.ui.design_system import NATIVE_CAD_STYLE
from hms_cadcam.ui.ui_tokens import (
    DIAGNOSTICS_DEFAULT_HEIGHT,
    DIAGNOSTICS_MAX_HEIGHT,
    FUNCTION_EDITOR_MAX_WIDTH,
    FUNCTION_EDITOR_MIN_WIDTH,
    OPERATION_MANAGER_DEFAULT_WIDTH,
    OPERATION_MANAGER_MAX_WIDTH,
    OPERATION_MANAGER_MIN_WIDTH,
    SECONDARY_PANEL_MAX_WIDTH,
    SECONDARY_PANEL_MIN_WIDTH,
    WORKSPACE_STYLE,
)
from hms_cadcam.ui.workspace_layout import (
    WorkspaceLayoutStore,
    clamp_window_to_available_screens,
)
from hms_cadcam.ui.workspace_panels import (
    DiagnosticsHost,
    FunctionEditorHost,
    OperationManagerHost,
    SecondaryPanelHost,
)
from hms_cadcam.ui.workspace_shell import (
    WorkspaceBar,
    WorkspaceId,
)
from hms_cadcam.ui.workspace_dialog import DropOpenOverlay
from hms_cadcam.ui.simulation_geometry_adapter import ActiveOcpFixtureResolver
from hms_cadcam.ui.localization import localize_widget_tree, ui_text
from hms_cadcam.ui.i18n import (
    LocaleSettingsService,
    UiLanguage,
    apply_application_font,
    apply_widget_font_tree,
    translation_service,
)
from hms_cadcam.ui.language_settings import LanguageSettingsDialog
from hms_cadcam.ui.settings import (
    GeneralSettingsDialog,
    UiScaleManager,
    ViewportBackgroundManager,
)
from hms_cadcam.ui.settings.export_defaults import ExportDefaultsSettingsService
from hms_cadcam.ui.data_locations import (
    DataLocationsDialog,
    StorageNotificationBar,
)
from hms_cadcam.ui.backup_profiles import (
    BackupWizardDialog,
    RestoreWizardDialog,
    UserProfilesDialog,
)
from hms_cadcam.viewer.backend import CadViewportBackend
from hms_cadcam.viewer.models import ObjectAppearance, ObjectColor, SelectionMetadata, SelectionMode
from hms_cadcam.viewer.widget import CadViewportWidget

_logger = logging.getLogger(__name__)



_OBJECT_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_DOCUMENT_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_OBJECT_NODE_ROLE = int(Qt.ItemDataRole.UserRole) + 3
_PLACEHOLDER_ROLE = int(Qt.ItemDataRole.UserRole) + 4
_TOPOLOGY_GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 5
_MAIN_WINDOW_BASE_MINIMUM = QSize(1024, 680)


def responsive_minimum_size(
    available_geometry: QRect,
    requested_minimum: QSize = _MAIN_WINDOW_BASE_MINIMUM,
    frame_delta: QSize = QSize(),
) -> QSize:
    """Fit a requested client minimum inside the available screen geometry."""

    if not isinstance(available_geometry, QRect):
        raise TypeError("available_geometry must be QRect")
    if not isinstance(requested_minimum, QSize):
        raise TypeError("requested_minimum must be QSize")
    if not isinstance(frame_delta, QSize):
        raise TypeError("frame_delta must be QSize")
    width_budget = max(1, available_geometry.width() - max(0, frame_delta.width()))
    height_budget = max(1, available_geometry.height() - max(0, frame_delta.height()))
    return QSize(
        min(requested_minimum.width(), width_budget),
        min(requested_minimum.height(), height_budget),
    )


class _NoFixtureResolver:
    """Fail closed if a supposedly fixture-free scene unexpectedly resolves one."""

    def resolve_fixture(self, _reference: GeometryReference):
        raise RuntimeError("Fixture resolver was not configured")


class MainWindow(QMainWindow):
    """Compose the HMS CAD/CAM desktop workspace."""

    def __init__(
        self,
        project_service: ProjectService,
        cad_kernel: CadKernel,
        viewport_backend: CadViewportBackend,
        *,
        layout_store: WorkspaceLayoutStore | None = None,
        application_paths: ApplicationPathsService | None = None,
        storage_bootstrap: StorageBootstrapService | None = None,
        user_profile_service: UserProfileService | None = None,
        hms_backup_service: HmsBackupService | None = None,
        hms_restore_service: HmsRestoreService | None = None,
        ui_feature_flags: UiFeatureFlags | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("HmsMainWindow")
        self.setWindowTitle("HMS CAD/CAM — Thiết kế")
        self.resize(1500, 900)
        self._requested_minimum_size = QSize(_MAIN_WINDOW_BASE_MINIMUM)
        self._effective_minimum_size = QSize(_MAIN_WINDOW_BASE_MINIMUM)
        self.setMinimumSize(self._effective_minimum_size)
        self.setDockNestingEnabled(True)
        # The owner-reference design system is appended last so its light
        # technical surfaces and navy chrome supersede legacy overrides without changing widget
        # ownership, dock identity or the certified central OCP viewport.
        self._base_style_sheet = APP_STYLE + WORKSPACE_STYLE + NATIVE_CAD_STYLE
        self.setStyleSheet(self._base_style_sheet)
        self._cad_kernel = cad_kernel
        self._project_service = project_service
        self._ui_feature_flags = ui_feature_flags or UiFeatureFlags.for_production()
        self._cam3d_review_host = self._ui_feature_flags.is_enabled(
            UiFeatureFlag.CAM_3D_9A8
        )
        self._lathe_review_host = self._ui_feature_flags.is_enabled(
            UiFeatureFlag.LATHE_9A9
        )
        self._lathe_toolpath_preview_host = (
            self._lathe_review_host
            and self._ui_feature_flags.is_enabled(UiFeatureFlag.LATHE_TOOLPATH_12_1)
        )
        self._lathe_persistence_host = (
            self._lathe_review_host
            and self._ui_feature_flags.is_enabled(
                UiFeatureFlag.LATHE_PERSISTENCE_12_5A
            )
        )
        self._lathe_simulation_host = self._ui_feature_flags.is_enabled(
            UiFeatureFlag.LATHE_SIMULATION_12_6A
        )
        self._project_service.configure_lathe_persistence(
            self._lathe_persistence_host
        )
        self._layout_store = layout_store or WorkspaceLayoutStore.for_config_directory(
            project_service.config_dir
        )
        self._ai_assist_capability = self._ui_feature_flags.is_enabled(
            UiFeatureFlag.OFFLINE_CAM_AI_ASSIST_13A
        )
        self._advisor_capability = self._ui_feature_flags.is_enabled(
            UiFeatureFlag.OFFLINE_CAM_AI_PARAMETER_ADVISOR_13B
        )
        self._advisor_settings_service = (
            AdvisorSettingsService(self._layout_store.settings)
            if self._advisor_capability else None
        )
        self._ai_assist_controller = (
            AiAssistController(
                AiAssistSettingsService(self._layout_store.settings),
                WindowsResourceProvider(),
                capability_enabled=True,
            )
            if self._ai_assist_capability
            else None
        )
        self._translation_service = translation_service()
        self._locale_settings = LocaleSettingsService(self._layout_store.settings)
        selected_locale = self._locale_settings.load()
        self._user_profile_service = user_profile_service
        active_profile: UserProfile | None = None
        if self._user_profile_service is None and application_paths is not None:
            self._user_profile_service = UserProfileService(application_paths)
        if self._user_profile_service is not None:
            active_profile = self._user_profile_service.bootstrap(
                locale=selected_locale.value
            )
            selected_locale = UiLanguage.coerce(active_profile.locale)
        self._translation_service.set_language(selected_locale)
        apply_application_font(self._translation_service.language)
        self._translation_service.language_changed.connect(
            self._language_changed
        )
        self._ui_scale_manager = UiScaleManager(
            self._layout_store.settings, parent=self
        )
        self._export_defaults_service = ExportDefaultsSettingsService(
            self._layout_store.settings
        )
        self._viewport_background_manager = ViewportBackgroundManager(
            self._layout_store.settings,
            parent=self,
        )
        self._ui_scale_manager.preview_changed.connect(self._apply_ui_scale)
        self._ui_scale_manager.scale_changed.connect(self._apply_ui_scale)
        self._ui_scale_manager.apply_runtime()
        self._language_dialog: LanguageSettingsDialog | None = None
        self._general_settings_dialog: GeneralSettingsDialog | None = None
        self._application_paths = application_paths
        self._storage_bootstrap = storage_bootstrap
        self._data_locations_dialog: DataLocationsDialog | None = None
        self._storage_notification: StorageNotificationBar | None = None
        self._hms_backup_service = hms_backup_service
        if self._hms_backup_service is None and application_paths is not None:
            self._hms_backup_service = HmsBackupService(
                application_paths,
                profile_service=self._user_profile_service,
            )
        self._hms_restore_service = hms_restore_service
        if self._hms_restore_service is None and application_paths is not None:
            self._hms_restore_service = HmsRestoreService(
                application_paths,
                backup_service=self._hms_backup_service,
                profile_service=self._user_profile_service,
            )
        self._backup_dialog: BackupWizardDialog | None = None
        self._restore_dialog: RestoreWizardDialog | None = None
        self._profiles_dialog: UserProfilesDialog | None = None
        self._responsive_collapsed_operation_manager = False
        self._responsive_collapsed_output_dock = False
        self._post_assembly_dock_containment_scheduled = False
        self._offscreen_dock_group_realization_scheduled = False
        self._offscreen_realized_dock_groups: set[tuple[str, str]] = set()
        self._post_assembly_compact_chrome_visibility: tuple[bool, ...] | None = None
        self._managed_output_lines: dict[
            str,
            tuple[str, dict[str, object], frozenset[str]],
        ] = {}
        self._reported_lathe_diagnostics: set[tuple[str, str]] = set()
        self._machining_simulation_window: QMainWindow | None = None

        self.viewport = CadViewportWidget(cad_kernel, viewport_backend, self)
        self.viewport.set_background_color(
            self._viewport_background_manager.current_color
        )
        self._viewport_background_manager.preview_changed.connect(
            self._apply_viewport_background
        )
        self._viewport_baseline_minimum = QSize(self.viewport.minimumSize())
        self.viewport.set_status_text_resolver(ui_text)
        if self._lathe_toolpath_preview_host:
            self._lathe_toolpath_sink = LatheViewportPreviewSink(self.viewport)
        self.setAcceptDrops(True)
        self._drop_overlay = DropOpenOverlay(self.viewport)
        self.project_controller = ProjectUiController(self, project_service)
        self.cad_controller = CadUiController(
            self,
            cad_kernel,
            self.viewport,
            project_service=project_service,
        )
        if self._cam3d_review_host:
            self._cam3d_selection_service = Cam3DSelectionApplicationService(
                self._current_cam3d_selection_source,
                self._bind_cam3d_selection_surface,
            )
            self._cam3d_editor_binding_controller = Cam3DEditorBindingController()
            self._cam3d_workflow: Cam3DPreviewWorkflow | None = None
            self._cam3d_worker_bridge = None
            self._cam3d_workflow_runtime_key = None
            self._cam3d_preview_sink = Cam3DViewportPreviewSink(self.viewport)
        self.cad_controller.set_open_command(
            self.project_controller.request_open_path
        )
        self.project_controller.document_open_requested.connect(
            self.cad_controller.open_prepared_document
        )
        self.cad_controller.workspace_changed.connect(
            self.project_controller.document_open_succeeded
        )
        self._active_document_metadata: CadDocumentMetadata | None = None
        self._active_document_tree: CadDocumentTree | None = None
        self._active_selection: tuple[SelectionMetadata, ...] = ()
        self._selected_object_ids: tuple[CadObjectId, ...] = ()
        self._object_appearances: dict[CadObjectId, ObjectAppearance] = {}
        self._object_items: dict[CadObjectId, QTreeWidgetItem] = {}
        self._tree_sync_guard = False
        self._active_measurements: tuple[MeasurementResult, ...] = ()
        self.export_controller = CadExportUiController(
            self,
            CadExportService.create_for_kernel(cad_kernel),
            project_service,
            lambda: (
                None
                if self._active_document_metadata is None
                else self._active_document_metadata.document_id
            ),
            lambda: (
                None
                if self._active_document_metadata is None
                else self._active_document_metadata.geometry_kind
            ),
            lambda: self._active_selection,
            lambda: (
                not self.project_controller.is_busy
                and not self.cad_controller.is_busy
            ),
            defaults_service=self._export_defaults_service,
        )
        self.project_controller.set_save_as_export_router(
            self.export_controller.route_save_as
        )
        self._build_menu_bar()
        self._build_quick_access_toolbar()
        self._build_cad_toolbar()
        self.workspace_bar = WorkspaceBar(self)
        self.workspace_bar.workspace_changed.connect(self._workspace_changed)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.workspace_bar)
        self._ribbon = RibbonWidget(
            self.project_controller.actions,
            self.cad_controller.actions,
            self,
            workspace_actions={
                "post_assembly": self.post_assembly_action,
                "general_settings": self._general_settings_action,
            },
            ui_scale_manager=self._ui_scale_manager,
        )
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        ribbon_toolbar = QToolBar("Ribbon", self)
        ribbon_toolbar.setObjectName("RibbonContainer")
        ribbon_toolbar.setMovable(False)
        ribbon_toolbar.setFloatable(False)
        ribbon_toolbar.addWidget(self._ribbon)
        self._ribbon_toolbar = ribbon_toolbar
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, ribbon_toolbar)

        self.setCentralWidget(self.viewport)
        self.project_dock = self._create_project_dock()
        self.properties_dock = self._create_properties_dock()
        self.output_dock = self._create_output_dock()
        self.cam_workspace = CamWorkspace(
            project_service,
            lambda: self.cad_controller.active_source_id,
            self._current_geometry_reference,
            self.viewport.display_toolpath,
            self.viewport.clear_toolpaths,
            self,
            toolpath_remove=self.viewport.remove_toolpath,
            face_resolver=self._resolve_planar_face_reference,
            contour_pick_provider=self._current_contour_reference,
            profile_resolver=self._resolve_contour_profile_reference,
            pocket_resolver=self._resolve_pocket_geometry_reference,
            drilling_pick_provider=self._current_drilling_reference,
            drilling_resolver=self._resolve_drilling_geometry,
            simulation_scene_builder=self._build_simulation_scene,
            parallel_surface_provider=self._current_parallel_surfaces,
            parallel_adapter_provider=self._parallel_surface_adapter,
            parallel_geometry_bounds_provider=self._current_parallel_bounds,
        )
        self.cam_workspace.message.connect(self._append_output)
        self.cam_workspace.toolbar.hide()
        if self._cam3d_review_host:
            self.cam3d_function_panel = Cam3DFunctionPanel(
                feature_enabled=True,
                parent=self,
            )
            self.cam3d_function_panel.selection_assign_requested.connect(
                self._assign_cam3d_selection_role
            )
            self.cam3d_function_panel.selection_clear_requested.connect(
                self._clear_cam3d_selection_role
            )
            self.cam3d_function_panel.tool_assembly_changed.connect(
                self._assign_cam3d_tool_assembly
            )
            self.cam3d_function_panel.tool_profile_changed.connect(
                self._assign_cam3d_tool_profile
            )
            self.cam3d_function_panel.numeric_field_changed.connect(
                self._replace_cam3d_numeric_field
            )
            self.cam3d_function_panel.preview_requested.connect(
                self._request_cam3d_preview
            )
            self.cam3d_function_panel.cancel_requested.connect(
                self._cancel_cam3d_preview
            )

        self._post_assembly_adapter = PostAssemblyProjectionAdapter(
            project_service, project_service.current_project
        )
        self.unified_post_assembly_panel = UnifiedPostAssemblyPanel(
            self._post_assembly_adapter, parent=self
        )
        self._post_assembly_review_host = self._ui_feature_flags.is_enabled(
            UiFeatureFlag.POST_ASSEMBLY_9A7
        )
        self._update_responsive_minimum()

        self.operation_manager_host = OperationManagerHost(
            self.cam_workspace,
            project_service,
            self._layout_store.settings,
            self.cam_workspace.actions,
            self.project_controller.actions,
        )
        self.operation_manager_dock = QDockWidget("Operations", self)
        self.operation_manager_dock.setObjectName("OperationManagerDock")
        self.operation_manager_dock.setProperty(
            "compactTitleSource",
            "Operations",
        )
        self.operation_manager_dock.setProperty(
            "fullTitleSource",
            "Operation Manager",
        )
        self.operation_manager_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.operation_manager_dock.setMinimumWidth(OPERATION_MANAGER_MIN_WIDTH)
        self.operation_manager_dock.setMaximumWidth(OPERATION_MANAGER_MAX_WIDTH)
        self.operation_manager_dock.setWidget(self.operation_manager_host)

        self.function_editor_host = FunctionEditorHost(
            self.cam_workspace.editor,
            self.cam_workspace.tree,
            self.cam_workspace.editor.apply_draft,
            settings=self._layout_store.settings,
            production_provider=self.cam_workspace.production_function_editor_session,
            selection_restore=self.cam_workspace.select_identity,
            selection_exists=self.cam_workspace.selection_exists,
            fallback_callback=self.cam_workspace.report_function_editor_fallback,
            follow_selection=False,
        )
        self.cam_workspace.projection_changed.connect(
            self.function_editor_host.refresh_current
        )
        self.cam_workspace.parallel_progress_changed.connect(
            self.function_editor_host.update_calculation_progress
        )
        self.cam_workspace.parallel_calculation_active.connect(
            self.function_editor_host.set_calculation_active
        )
        self.cam_workspace.rest_result_changed.connect(
            self.function_editor_host.set_rest_result
        )
        self.function_editor_host.calculation_cancel_requested.connect(
            self.cam_workspace.cancel_parallel_calculation
        )
        self.cam_function_popup = CAMFunctionPopupHost(
            self.function_editor_host,
            self._layout_store.settings,
            self,
        )
        self.cam_workspace.set_child_dialog_opener(
            self.cam_function_popup.adopt_child_dialog
        )
        self.project_controller.set_project_change_guard(
            self._prepare_cam_popup_for_project_change
        )
        # Hidden compatibility object for older saved dock layouts. Production
        # CAM editing is hosted exclusively by ``cam_function_popup``.
        self.function_editor_dock = QDockWidget(
            "Trình chỉnh sửa chức năng", self
        )
        self.function_editor_dock.setObjectName("FunctionEditorDock")
        self.function_editor_dock.setAccessibleName("Trình chỉnh sửa chức năng")
        self.function_editor_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.function_editor_dock.setMinimumWidth(FUNCTION_EDITOR_MIN_WIDTH)
        self.function_editor_dock.setMaximumWidth(FUNCTION_EDITOR_MAX_WIDTH)
        self.function_editor_dock.setWidget(QWidget())

        self.diagnostics_host = DiagnosticsHost(self._output)
        self.output_dock.setWindowTitle("Chẩn đoán & Hoạt động")
        self.output_dock.setAccessibleName("Chẩn đoán và tác vụ nền")
        self.output_dock.setMaximumHeight(DIAGNOSTICS_MAX_HEIGHT)
        self.output_dock.setWidget(self.diagnostics_host)
        self.diagnostics_dock = self.output_dock

        self.secondary_panel_host = SecondaryPanelHost(
            self.cam_workspace.simulation_panel,
            self.cam_workspace.post_tabs,
        )
        self.secondary_dock = QDockWidget("Post", self)
        self.secondary_dock.setObjectName("SecondaryWorkflowDock")
        self.secondary_dock.setProperty("compactTitleSource", "Post")
        self.secondary_dock.setProperty("fullTitleSource", "Simulation / Post")
        self.secondary_dock.setAccessibleName("Bảng Mô phỏng và Post")
        self.secondary_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.secondary_dock.setMinimumWidth(SECONDARY_PANEL_MIN_WIDTH)
        self.secondary_dock.setMaximumWidth(SECONDARY_PANEL_MAX_WIDTH)
        self.secondary_dock.setWidget(self.secondary_panel_host)

        if self._cam3d_review_host:
            self.cam3d_function_dock = QDockWidget(
                ui_text("CAM 3D Function UI"), self
            )
            self.cam3d_function_dock.setObjectName("Cam3DFunctionDock")
            self.cam3d_function_dock.setAccessibleName(
                ui_text("CAM 3D Function UI")
            )
            self.cam3d_function_dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.cam3d_function_dock.setMinimumWidth(360)
            self.cam3d_function_dock.setMaximumWidth(620)
            self.cam3d_function_dock.setWidget(self.cam3d_function_panel)
            self.cam3d_function_dock.hide()

        if self._post_assembly_review_host:
            self.post_assembly_dock = QDockWidget(
                ui_text("Post / Program Assembly"), self
            )
            self.post_assembly_dock.setObjectName("PostAssemblyDock")
            self.post_assembly_dock.setAccessibleName(
                ui_text("Post / Program Assembly")
            )
            self.post_assembly_dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.post_assembly_dock.setWidget(
                self.unified_post_assembly_panel
            )
            self.post_assembly_dock.visibilityChanged.connect(
                lambda _visible: self._apply_post_assembly_compact_layout()
            )
        else:
            # Production/development keeps the legacy dock topology intact.
            self.post_assembly_dock = self.secondary_dock

        # Studio is a modeless window, deliberately not a dock: the main
        # workspace keeps its one semantic Post dock/tab group invariant.
        self.post_studio_window: QDialog | None = None
        self.post_studio_panel: PostProcessorStudioPanel | None = None

        self.incoming_geometry_bar = IncomingGeometryNotificationBar(self)
        self.incoming_geometry_dock = QDockWidget(
            "Thông báo dữ liệu 3D",
            self,
        )
        self.incoming_geometry_dock.setObjectName(
            "IncomingGeometryNotificationDock"
        )
        self.incoming_geometry_dock.setAccessibleName(
            "Thông báo dữ liệu 3D mới"
        )
        self.incoming_geometry_dock.setAllowedAreas(
            Qt.DockWidgetArea.TopDockWidgetArea
        )
        self.incoming_geometry_dock.setFeatures(
            QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
        )
        notification_title = QWidget()
        notification_title.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.incoming_geometry_dock.setTitleBarWidget(notification_title)
        self.incoming_geometry_dock.setWidget(self.incoming_geometry_bar)
        self.incoming_geometry_dock.setMaximumHeight(72)

        self.incoming_geometry_panel = IncomingGeometryPanel(self)
        self.incoming_geometry_panel_dock = QDockWidget(
            "Xem thay đổi dữ liệu 3D",
            self,
        )
        self.incoming_geometry_panel_dock.setObjectName(
            "IncomingGeometryChangeDock"
        )
        self.incoming_geometry_panel_dock.setAccessibleName(
            "Xem thay đổi dữ liệu 3D"
        )
        self.incoming_geometry_panel_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.incoming_geometry_panel_dock.setMinimumWidth(380)
        self.incoming_geometry_panel_dock.setMaximumWidth(560)
        self.incoming_geometry_panel_dock.setWidget(
            self.incoming_geometry_panel
        )

        # Compatibility alias for existing callers; the CAM coordinator remains
        # available as ``cam_workspace`` while its tree is hosted in this dock.
        self.cam_dock = self.operation_manager_dock
        if self._lathe_review_host:
            self.lathe_workspace = LatheWorkspace(self)
            self.lathe_dock = QDockWidget(ui_text("lathe.workspace.title"), self)
            self.lathe_dock.setObjectName("LatheWorkspaceDock")
            self.lathe_dock.setAccessibleName(ui_text("lathe.workspace.title"))
            self.lathe_dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
            )
            self.lathe_dock.setMinimumWidth(520)
            self.lathe_dock.setMaximumWidth(1000)
            self.lathe_dock.setWidget(self.lathe_workspace)
            if self._lathe_simulation_host:
                self._lathe_simulation_manager = LatheSimulationWindowManager(
                    self, enabled=True
                )
                self.lathe_workspace.bind_simulation_manager(
                    self._lathe_simulation_manager
                )
            self._lathe_session_controller = LatheSessionController(
                self.lathe_workspace,
                self._current_lathe_selection_context,
                toolpath_sink=(
                    self._lathe_toolpath_sink
                    if self._lathe_toolpath_preview_host
                    else None
                ),
                persistence_port=(
                    self._project_service
                    if self._lathe_persistence_host
                    else None
                ),
                parent=self,
            )
            self._lathe_session_controller.availability_changed.connect(
                self._lathe_availability_changed
            )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)
        self.addDockWidget(
            Qt.DockWidgetArea.LeftDockWidgetArea, self.operation_manager_dock
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.properties_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.secondary_dock)
        if self._post_assembly_review_host:
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea, self.post_assembly_dock
            )
        if self._cam3d_review_host:
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea, self.cam3d_function_dock
            )
            self.tabifyDockWidget(self.properties_dock, self.cam3d_function_dock)
        if self._lathe_review_host:
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea, self.lathe_dock
            )
            self.tabifyDockWidget(self.properties_dock, self.lathe_dock)
        self.project_dock.show()
        self.operation_manager_dock.show()
        self.properties_dock.show()
        self.secondary_dock.show()
        self.tabifyDockWidget(self.project_dock, self.operation_manager_dock)
        self.tabifyDockWidget(self.properties_dock, self.secondary_dock)
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, self.function_editor_dock
        )
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.incoming_geometry_panel_dock,
        )
        self.function_editor_dock.show()
        self.incoming_geometry_panel_dock.show()
        self.tabifyDockWidget(
            self.properties_dock,
            self.function_editor_dock,
        )
        self.tabifyDockWidget(
            self.properties_dock,
            self.incoming_geometry_panel_dock,
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.output_dock)
        self.addDockWidget(
            Qt.DockWidgetArea.TopDockWidgetArea,
            self.incoming_geometry_dock,
        )
        # Dock groups are established once before unrelated right-side docks
        # are inserted. Later locale passes update presentation only.
        self.operation_manager_dock.raise_()
        self.project_dock.hide()
        self.properties_dock.hide()
        self.secondary_dock.hide()
        if self._post_assembly_review_host:
            self.post_assembly_dock.hide()
        if self._cam3d_review_host:
            self.cam3d_function_dock.hide()
        self.function_editor_dock.hide()
        if self._lathe_review_host:
            self.lathe_dock.hide()
        self.incoming_geometry_panel_dock.hide()
        self.incoming_geometry_dock.hide()
        self.resizeDocks(
            [self.operation_manager_dock],
            [OPERATION_MANAGER_DEFAULT_WIDTH],
            Qt.Orientation.Horizontal,
        )
        self.resizeDocks(
            [self.output_dock], [DIAGNOSTICS_DEFAULT_HEIGHT], Qt.Orientation.Vertical
        )
        for dock in (
            self.project_dock,
            self.operation_manager_dock,
            self.properties_dock,
            self.secondary_dock,
        ):
            dock.visibilityChanged.connect(
                self._schedule_offscreen_dock_group_realization
            )
        self.operation_manager_host.collapse_requested.connect(
            self.operation_manager_dock.hide
        )
        self.operation_manager_host.simulation_requested.connect(
            lambda: self._workspace_changed(WorkspaceId.SIMULATION.value)
        )
        self.operation_manager_host.post_requested.connect(
            lambda: self._workspace_changed(WorkspaceId.POST.value)
        )
        self.operation_manager_host.editor_requested.connect(
            self._show_function_editor
        )
        self.cam_workspace.operation_created.connect(self._show_function_editor)
        self.diagnostics_host.collapse_requested.connect(self.output_dock.hide)
        self.secondary_panel_host.collapse_requested.connect(self.secondary_dock.hide)
        self._build_panel_visibility_actions()
        if self._lathe_review_host:
            self.cam_workspace.projection_changed.connect(
                self._sync_lathe_context
            )
        self._build_status_bar()
        self.project_controller.project_changed.connect(self._handle_project_change)
        self.cam_workspace.projection_changed.connect(
            self._refresh_post_assembly_panel
        )
        self.project_controller.incoming_geometry_changed.connect(
            self._incoming_geometry_changed
        )
        self.project_controller.incoming_geometry_preview_ready.connect(
            self._incoming_geometry_preview_ready
        )
        self.project_controller.geometry_apply_completed.connect(
            self._incoming_geometry_apply_completed
        )
        self.incoming_geometry_bar.view_requested.connect(
            self.project_controller.request_incoming_preview
        )
        self.incoming_geometry_bar.apply_requested.connect(
            self.project_controller.request_incoming_preview
        )
        self.incoming_geometry_bar.defer_requested.connect(
            self.project_controller.defer_incoming_geometry
        )
        self.incoming_geometry_bar.reject_requested.connect(
            self.project_controller.reject_incoming_geometry
        )
        self.incoming_geometry_panel.apply_requested.connect(
            self.project_controller.apply_incoming_geometry
        )
        self.incoming_geometry_panel.cancel_requested.connect(
            self.incoming_geometry_panel_dock.hide
        )
        self.project_controller.message.connect(self._append_output)
        self.cad_controller.message.connect(self._append_output)
        self.export_controller.message.connect(self._append_output)
        self.export_controller.busy_changed.connect(
            self.project_controller.set_external_busy
        )
        self.project_controller.busy_changed.connect(
            lambda _busy: self.export_controller.refresh_action_states()
        )
        self.cad_controller.busy_changed.connect(
            lambda _busy: self.export_controller.refresh_action_states()
        )
        self.cad_controller.progress_changed.connect(self._update_import_status)
        self.cad_controller.document_changed.connect(self._update_cad_document)
        self.cad_controller.document_changed.connect(
            lambda _value: self.export_controller.refresh_action_states()
        )
        self.cad_controller.topology_tree_changed.connect(self._update_topology_tree)
        self.viewport.selection_context_changed.connect(
            self.cad_controller.handle_selection_event
        )
        self.cad_controller.selection_context_changed.connect(self._update_selection)
        self.cad_controller.selection_context_changed.connect(
            lambda _document_id, _items: self.export_controller.refresh_action_states()
        )
        if self._lathe_review_host:
            self.cad_controller.selection_context_changed.connect(
                self._lathe_selection_changed
            )
        self.cad_controller.object_selection_context_changed.connect(
            self._update_object_selection
        )
        self.cad_controller.appearance_context_changed.connect(
            self._update_object_appearances
        )
        self.cad_controller.project_state_changed.connect(
            lambda: self._update_project_display(
                self._current_display_state()
            )
        )
        self.cad_controller.measurement_context_changed.connect(
            self._update_measurements
        )
        self._handle_project_change(self._current_display_state())
        self._restore_workspace_layout()
        if active_profile is not None:
            try:
                if active_profile.ui_state:
                    self._apply_user_profile(active_profile)
                else:
                    # The first profile adopts the already-restored legacy HMS
                    # presentation; project/document state is never captured.
                    self._user_profile_service.save(
                        self._capture_active_profile(active_profile)
                    )
            except (OSError, RuntimeError, ValueError, TypeError, ProfileError) as exc:
                self._append_output(str(exc))
        localize_widget_tree(self)
        self.refresh_localized_layout()
        self._apply_ui_scale(self._ui_scale_manager.current_percent)
        viewport_status = self.viewport.viewport_status
        if not viewport_status.available:
            reason = (
                viewport_status.error
                or "CAD rendering backend is unavailable."
            )
            self._append_localized_output(
                "CAD Viewer unavailable: {reason}",
                localized_arguments=frozenset({"reason"}),
                reason=reason,
            )

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setObjectName("MainMenuBar")
        menu_bar.setStyleSheet(
            ribbon_menu_style_sheet(
                RibbonMetrics.from_scale_manager(self._ui_scale_manager)
            )
        )
        file_menu = menu_bar.addMenu("Tệp")
        file_menu.addAction(self.project_controller.actions["new"])
        file_menu.addAction(self.project_controller.actions["new_from_document"])
        file_menu.addSeparator()
        file_menu.addAction(self.project_controller.actions["open"])
        file_menu.addAction(self.project_controller.actions["open_project"])
        file_menu.addSeparator()
        file_menu.addAction(self.project_controller.actions["import"])
        file_menu.addAction(self.project_controller.actions["send_geometry"])
        self._recent_menu = file_menu.addMenu("Dự án gần đây")
        self._recent_menu.aboutToShow.connect(
            lambda: self.project_controller.populate_recent_menu(self._recent_menu)
        )
        file_menu.addSeparator()
        file_menu.addAction(self.project_controller.actions["save"])
        file_menu.addAction(self.project_controller.actions["save_as"])
        file_menu.addAction(self.project_controller.actions["close"])
        file_menu.addSeparator()
        menu_bar.addMenu("Sửa")
        self._view_menu = menu_bar.addMenu("Hiển thị")
        cad_viewer_menu = menu_bar.addMenu("CAD")
        cam_menu = menu_bar.addMenu("CAM")
        cam_workspace_action = QAction("Mở không gian làm việc CAM", self)
        cam_workspace_action.triggered.connect(self._show_cam_workspace)
        cam_menu.addAction(cam_workspace_action)
        self.machining_simulation_action = QAction(
            ui_text("Machining simulation"), self
        )
        self.machining_simulation_action.setObjectName(
            "MachiningSimulationOpenAction"
        )
        self.machining_simulation_action.setProperty(
            "commandId", "cam.simulation.open"
        )
        self.machining_simulation_action.triggered.connect(
            self._open_machining_simulation
        )
        cam_menu.addAction(self.machining_simulation_action)
        self.post_assembly_action = QAction(
            ui_text("Post / Program Assembly"), self
        )
        self.post_assembly_action.setObjectName("PostAssemblyOpenAction")
        self.post_assembly_action.setProperty(
            "commandId", "cam.post_assembly.open"
        )
        self.post_assembly_action.setProperty(
            "accessibleName", ui_text("Post / Program Assembly")
        )
        self.post_assembly_action.setToolTip(
            ui_text("Open Post and Program Assembly")
        )
        self.post_assembly_action.triggered.connect(
            self._open_post_assembly
        )
        cam_menu.addAction(self.post_assembly_action)
        self.post_studio_action = QAction(ui_text("post_studio.open"), self)
        self.post_studio_action.setObjectName("PostProcessorStudioOpenAction")
        self.post_studio_action.setProperty("commandId", "cam.post_studio.open")
        self.post_studio_action.triggered.connect(self._open_post_studio)
        cam_menu.addAction(self.post_studio_action)
        if self._cam3d_review_host:
            self.cam3d_function_action = QAction(
                ui_text("CAM 3D Function UI"), self
            )
            self.cam3d_function_action.setObjectName("Cam3DFunctionOpenAction")
            self.cam3d_function_action.setProperty(
                "commandId", "cam.cam3d.open"
            )
            self.cam3d_function_action.setProperty(
                "accessibleName", ui_text("CAM 3D Function UI")
            )
            self.cam3d_function_action.setToolTip(
                ui_text("Open CAM 3D Function UI")
            )
            self.cam3d_function_action.triggered.connect(
                self._open_cam3d_function_ui
            )
            cam_menu.addAction(self.cam3d_function_action)
        cam_menu.addAction(self.project_controller.actions["new"])
        cam_menu.addAction(
            self.project_controller.actions["new_from_document"]
        )
        cam_menu.addAction(self.project_controller.actions["send_geometry"])
        for title in ("Máy", "Toolpath", "Setup"):
            menu_bar.addMenu(title)
        settings_menu = menu_bar.addMenu("Cài đặt")
        interface_menu = settings_menu.addMenu("Giao diện")
        self._language_action = QAction("Ngôn ngữ…", self)
        self._language_action.setObjectName("InterfaceLanguageAction")
        self._language_action.setToolTip(
            "Đổi ngôn ngữ giao diện mà không thay đổi dữ liệu dự án"
        )
        self._language_action.triggered.connect(self._show_language_settings)
        interface_menu.addAction(self._language_action)
        self._general_settings_action = QAction(
            ui_text("General settings"), self
        )
        self._general_settings_action.setObjectName("GeneralSettingsAction")
        self._general_settings_action.setShortcut(
            QKeySequence.fromString(
                "Ctrl+,",
                QKeySequence.SequenceFormat.PortableText,
            )
        )
        self._general_settings_action.setToolTip(
            ui_text("Open general settings...")
        )
        self._general_settings_action.triggered.connect(
            self._show_general_settings
        )
        interface_menu.addAction(self._general_settings_action)
        self._profiles_action = QAction("User profiles…", self)
        self._profiles_action.setObjectName("UserProfilesAction")
        self._profiles_action.setToolTip(
            "Manage per-user interface profiles without changing project data"
        )
        self._profiles_action.setEnabled(self._user_profile_service is not None)
        self._profiles_action.triggered.connect(self._show_user_profiles)
        interface_menu.addAction(self._profiles_action)
        backup_menu = settings_menu.addMenu("Backup and restore")
        self._backup_action = QAction("Back up HMS…", self)
        self._backup_action.setObjectName("HmsBackupAction")
        self._backup_action.setToolTip(
            "Back up HMS settings, profiles and library data"
        )
        self._backup_action.setEnabled(
            self._hms_backup_service is not None
            and self._user_profile_service is not None
        )
        self._backup_action.triggered.connect(self._show_hms_backup)
        backup_menu.addAction(self._backup_action)
        self._restore_action = QAction("Restore HMS…", self)
        self._restore_action.setObjectName("HmsRestoreAction")
        self._restore_action.setEnabled(self._hms_restore_service is not None)
        self._restore_action.triggered.connect(self._show_hms_restore)
        backup_menu.addAction(self._restore_action)
        system_menu = settings_menu.addMenu("System")
        self._data_locations_action = QAction("Data locations…", self)
        self._data_locations_action.setObjectName("DataLocationsAction")
        self._data_locations_action.setToolTip(
            "Inspect storage locations and permissions without changing production roots"
        )
        self._data_locations_action.setEnabled(
            self._application_paths is not None
            and self._storage_bootstrap is not None
        )
        self._data_locations_action.triggered.connect(
            self._show_data_locations
        )
        system_menu.addAction(self._data_locations_action)
        help_menu = menu_bar.addMenu("Trợ giúp")

        exit_action = QAction("Thoát", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        about_action = QAction("Giới thiệu HMS CAD/CAM", self)
        about_action.setEnabled(False)
        help_menu.addAction(about_action)

        for key in (
            "open_step",
            "open_brep",
            "open_iges",
            "open_stl",
            "fit_all",
            "view_isometric",
            "measurement",
        ):
            cad_viewer_menu.addAction(self.cad_controller.actions[key])
        cad_viewer_menu.addSeparator()
        cad_viewer_menu.addAction(self.export_controller.actions["export_3d"])
        cad_viewer_menu.addAction(self.export_controller.actions["export_selected"])
        directions_menu = cad_viewer_menu.addMenu("Hướng nhìn")
        for direction in ("top", "bottom", "front", "back", "left", "right"):
            directions_menu.addAction(
                self.cad_controller.actions[f"view_{direction}"]
            )
        display_menu = cad_viewer_menu.addMenu("Hiển thị")
        for mode in ("shaded", "wireframe", "shaded_with_edges"):
            display_menu.addAction(self.cad_controller.actions[f"display_{mode}"])
        selection_menu = cad_viewer_menu.addMenu("Lựa chọn")
        for mode in ("solid", "face", "wire", "edge", "vertex"):
            selection_menu.addAction(self.cad_controller.actions[f"selection_{mode}"])

    def _build_quick_access_toolbar(self) -> None:
        toolbar = QToolBar("Truy cập nhanh", self)
        toolbar.setObjectName("QuickAccess")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        brand = QLabel("HMS  CAD/CAM", toolbar)
        brand.setObjectName("HmsBrandLabel")
        brand.setAccessibleName("HMS CAD/CAM")
        self._brand_label = brand
        for key in ("new", "open", "save"):
            action = self.project_controller.actions[key]
            action.setProperty("profileCommandId", f"project.{key}")
            toolbar.addAction(action)
        for standard_icon, tooltip in (
            (QStyle.StandardPixmap.SP_ArrowBack, "Hoàn tác"),
            (QStyle.StandardPixmap.SP_ArrowForward, "Làm lại"),
        ):
            action = toolbar.addAction(self.style().standardIcon(standard_icon), "")
            action.setToolTip(f"{tooltip} — chưa khả dụng")
            action.setEnabled(False)
        # Keep the historical QAction order stable for keyboard/automation
        # contracts; branding is a trailing native widget, never an action.
        toolbar.addSeparator()
        toolbar.addWidget(brand)
        self._quick_access_toolbar = toolbar
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    def _build_panel_visibility_actions(self) -> None:
        """Expose every closable panel through one recoverable View menu."""
        self.panel_actions: dict[str, QAction] = {}
        definitions = (
            (
                "operation_manager",
                "Quản lý nguyên công",
                self.operation_manager_dock,
                "Bật hoặc tắt Quản lý nguyên công",
            ),
            (
                "diagnostics",
                "Chẩn đoán & Hoạt động",
                self.output_dock,
                "Bật hoặc tắt Chẩn đoán và nhật ký tác vụ",
            ),
            (
                "secondary",
                "Mô phỏng / Post",
                self.secondary_dock,
                "Bật hoặc tắt bảng quy trình phụ",
            ),
            (
                "post_assembly",
                ui_text("Post / Program Assembly"),
                self.post_assembly_dock,
                ui_text("Open Post and Program Assembly"),
            ),
            (
                "project_topology",
                "Dự án / Topology",
                self.project_dock,
                "Bật hoặc tắt cây Dự án và Topology CAD",
            ),
            (
                "cad_properties",
                "Thuộc tính CAD",
                self.properties_dock,
                "Bật hoặc tắt bảng thuộc tính CAD",
            ),
            (
                "incoming_geometry",
                "Xem thay đổi dữ liệu 3D",
                self.incoming_geometry_panel_dock,
                "Mở bảng xem trước dữ liệu 3D đang chờ",
            ),
        )
        if not self._post_assembly_review_host:
            definitions = tuple(
                item for item in definitions if item[0] != "post_assembly"
            )
        for key, text, dock, tooltip in definitions:
            action = dock.toggleViewAction()
            action.setText(text)
            action.setObjectName(f"View{key.title().replace('_', '')}Action")
            action.setToolTip(tooltip)
            action.setStatusTip(tooltip)
            self._view_menu.addAction(action)
            self.panel_actions[key] = action
        popup_action = QAction("Chỉnh sửa nguyên công CAM", self)
        popup_action.setObjectName("ViewFunctionEditorAction")
        popup_action.setToolTip(
            "Mở cửa sổ chỉnh sửa cho nguyên công đang chọn; không tự áp dụng"
        )
        popup_action.setStatusTip(popup_action.toolTip())
        popup_action.triggered.connect(self._show_function_editor)
        self._view_menu.addAction(popup_action)
        self.panel_actions["function_editor"] = popup_action
        self._view_menu.addSeparator()
        self.reset_workspace_action = QAction("Khôi phục bố cục làm việc", self)
        self.reset_workspace_action.setObjectName("ResetWorkspaceLayoutAction")
        self.reset_workspace_action.setToolTip(
            "Khôi phục bố cục UI mặc định; không thay đổi dữ liệu dự án"
        )
        self.reset_workspace_action.setStatusTip(
            "Chỉ khôi phục vị trí và trạng thái bảng của giao diện"
        )
        self.reset_workspace_action.triggered.connect(self.reset_workspace_layout)
        self._view_menu.addAction(self.reset_workspace_action)

    def _open_post_assembly(self) -> None:
        """Open one idempotent Post/Assembly presentation entry."""
        if not self._ui_feature_flags.is_enabled(
            UiFeatureFlag.POST_ASSEMBLY_9A7
        ):
            self._workspace_changed(WorkspaceId.POST.value)
            return
        self._update_responsive_minimum()
        self._refresh_post_assembly_panel()
        self.post_assembly_dock.show()
        self._apply_post_assembly_compact_layout()
        self.post_assembly_dock.raise_()
        self.post_assembly_dock.activateWindow()
        self._schedule_post_assembly_dock_containment()

    def _open_post_studio(self) -> None:
        """Open the isolated Post Studio without changing CAM/Post runtime state."""
        if self.post_studio_window is None:
            window = QDialog(self)
            window.setObjectName("PostProcessorStudioWindow")
            window.setWindowTitle(ui_text("post_studio.title"))
            window.setModal(False)
            layout = QVBoxLayout(window)
            layout.setContentsMargins(0, 0, 0, 0)
            self.post_studio_panel = PostProcessorStudioPanel(parent=window)
            layout.addWidget(self.post_studio_panel)
            window.resize(1180, 720)
            window.finished.connect(self._close_post_studio_window)
            self.post_studio_window = window
        if self.post_studio_panel is not None:
            self.post_studio_panel.refresh()
        self.post_studio_window.show()
        self.post_studio_window.raise_()
        self.post_studio_window.activateWindow()

    def _close_post_studio_window(self, _result: int) -> None:
        self.post_studio_window = None
        self.post_studio_panel = None

    def _refresh_post_assembly_panel(self) -> None:
        if not hasattr(self, "unified_post_assembly_panel"):
            return
        self._post_assembly_adapter.set_session(
            self._project_service.current_project
        )
        try:
            self.unified_post_assembly_panel.refresh_from_adapter()
        except (RuntimeError, TypeError, ValueError):
            self.unified_post_assembly_panel.set_available_operations(())

    def _open_cam3d_function_ui(self) -> None:
        """Open the review-only CAM 3D shell or preserve the legacy route."""
        if not self._cam3d_review_host:
            self._show_cam_workspace()
            return
        session = self._project_service.current_project
        self._bind_cam3d_selection_project(session)
        self.cam3d_function_dock.show()
        self.cam3d_function_dock.raise_()
        self.cam3d_function_dock.activateWindow()

    def _bind_cam3d_selection_project(
        self,
        session: ProjectSession | None,
    ) -> None:
        """Bind WP2A selection and WP2B-B editor to immutable live facts."""

        if not self._cam3d_review_host:
            return
        if session is None:
            state = self._cam3d_selection_service.reset()
            self.cam3d_function_panel.bind_project(None, generation=None)
            render = self._cam3d_editor_binding_controller.reset()
        else:
            workspace = self._project_service.current_workspace
            read_only = bool(
                workspace is not None
                and workspace.mode is DocumentMode.CAM_PROJECT
                and workspace.read_only
            )
            generation = self._project_service.cam_generation
            context = Cam3DProjectContext.open(
                session.manifest.project_id,
                generation,
                document_id=self.cad_controller.active_document_id,
                source_id=self.cad_controller.active_source_id,
                read_only=read_only,
            )
            bound = self._cam3d_editor_binding_controller.state.context
            identity_changed = (
                bound.is_open
                and (
                    bound.project_id != context.project_id
                    or bound.project_generation != context.project_generation
                    or bound.document_id != context.document_id
                    or bound.source_id != context.source_id
                )
            )
            if identity_changed:
                self._cam3d_selection_service.reset()
            state = self._cam3d_selection_service.bind_project(
                session.manifest.project_id,
                generation,
                read_only=read_only,
            )
            self.cam3d_function_panel.bind_project(
                session,
                generation=generation,
                read_only=read_only,
            )
            snapshot = session.cam_snapshot
            render = self._cam3d_editor_binding_controller.bind(
                context,
                state,
                tools=snapshot.tool_definitions,
                holders=snapshot.holder_definitions,
                assemblies=snapshot.tool_assemblies,
            )
        self.cam3d_function_panel.set_selection_state(state)
        self.cam3d_function_panel.set_editor_render_state(render)
        self._sync_cam3d_workflow()

    def _current_cam3d_editor_context(self) -> Cam3DProjectContext:
        session = self._project_service.current_project
        if session is None:
            return Cam3DProjectContext.closed()
        workspace = self._project_service.current_workspace
        read_only = bool(
            workspace is not None
            and workspace.mode is DocumentMode.CAM_PROJECT
            and workspace.read_only
        )
        return Cam3DProjectContext.open(
            session.manifest.project_id,
            self._project_service.cam_generation,
            document_id=self.cad_controller.active_document_id,
            source_id=self.cad_controller.active_source_id,
            read_only=read_only,
        )

    def _current_cam3d_active_setup(self) -> Cam3DActiveSetupContext | None:
        """Derive active Setup facts from the immutable CAM project snapshot."""
        session = self._project_service.current_project
        context = self._current_cam3d_editor_context()
        if session is None or not context.is_open:
            return None
        snapshot = session.cam_snapshot
        active_job = next(
            (
                job
                for job in snapshot.jobs
                if job.job_id == snapshot.active_job_id
            ),
            None,
        )
        setup = active_job.active_setup if active_job is not None else None
        if (
            setup is None
            or context.project_id is None
            or context.project_generation is None
            or context.document_id is None
            or context.source_id is None
        ):
            return None
        try:
            ownership = Cam3DCalculationOwnershipKey(
                context.project_id,
                context.document_id,
                context.source_id,
                setup.setup_id,
            )
            return Cam3DActiveSetupContext(
                ownership,
                context.project_generation,
                setup.revision,
                setup.wcs,
                active=setup.enabled,
            )
        except (TypeError, ValueError):
            return None

    def _current_cam3d_workflow_input(self) -> Cam3DWorkflowInput:
        controller = self._cam3d_editor_binding_controller
        context = self._current_cam3d_editor_context()
        try:
            editor_ready = controller.service.evaluate(context).valid
        except (RuntimeError, TypeError, ValueError):
            editor_ready = False
        return Cam3DWorkflowInput(
            controller.state,
            context,
            self._cam3d_selection_service.state,
            self._current_cam3d_active_setup(),
            editor_ready,
        )

    def _teardown_cam3d_workflow(self, *, wait: bool = False) -> None:
        workflow = self._cam3d_workflow
        bridge = self._cam3d_worker_bridge
        self._cam3d_workflow = None
        self._cam3d_worker_bridge = None
        self._cam3d_workflow_runtime_key = None
        if bridge is not None:
            try:
                bridge.set_receiver(None)
            except (RuntimeError, TypeError, ValueError):
                pass
        if workflow is not None:
            workflow.shutdown(wait=wait)
        if bridge is not None:
            try:
                bridge.deleteLater()
            except RuntimeError:
                pass

    def _sync_cam3d_workflow(self) -> None:
        """Compose/reuse one runtime and bind the latest immutable UI facts."""
        if not self._cam3d_review_host or not hasattr(
            self, "_cam3d_editor_binding_controller"
        ):
            return
        inputs = self._current_cam3d_workflow_input()
        ownership = inputs.ownership
        generation = inputs.live_context.project_generation
        if ownership is None or generation is None:
            self._teardown_cam3d_workflow()
            self.cam3d_function_panel.set_workflow_state(
                Cam3DWorkflowState.closed()
            )
            return
        runtime_key = (ownership, generation)
        if runtime_key != self._cam3d_workflow_runtime_key:
            self._teardown_cam3d_workflow()
            try:
                surface_mesher = self._parallel_surface_adapter()
                tessellator = OcpCam3DPreviewTessellator(
                    surface_mesher,
                    ownership,
                )
                coordinator = Cam3DPreviewCoordinator(tessellator)
                bridge = Cam3DQtWorkerBridge(coordinator, self)
                workflow = Cam3DPreviewWorkflow(
                    bridge,
                    self._cam3d_preview_sink,
                )
                bridge.set_receiver(self)
            except (RuntimeError, TypeError, ValueError):
                _logger.exception("CAM 3D preview runtime composition failed")
                self.cam3d_function_panel.set_workflow_state(
                    Cam3DWorkflowState(
                        Cam3DWorkflowStatus.BLOCKED,
                        ownership,
                        generation,
                    )
                )
                return
            self._cam3d_worker_bridge = bridge
            self._cam3d_workflow = workflow
            self._cam3d_workflow_runtime_key = runtime_key
        assert self._cam3d_workflow is not None
        state = self._cam3d_workflow.bind_inputs(inputs)
        self.cam3d_function_panel.set_workflow_state(state)

    def _request_cam3d_preview(self) -> None:
        if self._cam3d_workflow is None:
            self._sync_cam3d_workflow()
        if self._cam3d_workflow is not None:
            self.cam3d_function_panel.set_workflow_state(
                self._cam3d_workflow.submit_preview()
            )

    def _cancel_cam3d_preview(self) -> None:
        workflow = self._cam3d_workflow
        if workflow is not None:
            workflow.cancel_preview()
            self.cam3d_function_panel.set_workflow_state(workflow.state)

    def handle_cam3d_preview(self, result: object) -> None:
        """Receive only immutable queued results from the WP3-B Qt bridge."""
        if not isinstance(result, Cam3DPreviewResult):
            return
        workflow = self._cam3d_workflow
        if workflow is None:
            return
        workflow.accept_result(result)
        self.cam3d_function_panel.set_workflow_state(workflow.state)

    def _assign_cam3d_tool_assembly(self, choice: object) -> None:
        if choice is not None and not isinstance(choice, Cam3DToolAssemblyChoice):
            return
        controller = self._cam3d_editor_binding_controller
        controller.set_live_context(self._current_cam3d_editor_context())
        render = (
            controller.clear_tool_assembly()
            if choice is None
            else controller.assign_tool_assembly(choice)
        )
        self.cam3d_function_panel.set_editor_render_state(render)
        self._sync_cam3d_workflow()

    def _assign_cam3d_tool_profile(self, choice: object) -> None:
        if choice is not None and not isinstance(choice, Cam3DToolProfileChoice):
            return
        controller = self._cam3d_editor_binding_controller
        controller.set_live_context(self._current_cam3d_editor_context())
        render = (
            controller.clear_tool_profile()
            if choice is None
            else controller.assign_tool_profile(choice)
        )
        self.cam3d_function_panel.set_editor_render_state(render)
        self._sync_cam3d_workflow()

    def _replace_cam3d_numeric_field(self, field: object, value: object) -> None:
        if not isinstance(field, Cam3DEditorField):
            return
        controller = self._cam3d_editor_binding_controller
        controller.set_live_context(self._current_cam3d_editor_context())
        render = (
            controller.replace_numeric_text(field, value)
            if isinstance(value, str)
            else controller.replace_numeric_field(field, value)
        )
        self.cam3d_function_panel.set_editor_render_state(render)
        self._sync_cam3d_workflow()

    def _assign_cam3d_selection_role(self, role: object) -> None:
        """Assign current eligible viewport faces through the application service."""

        if not isinstance(role, Cam3DSelectionRole):
            return
        state = self._cam3d_selection_service.assign_current(role)
        controller = self._cam3d_editor_binding_controller
        controller.set_live_context(self._current_cam3d_editor_context())
        render = controller.set_selection(state)
        self.cam3d_function_panel.set_selection_state(state)
        self.cam3d_function_panel.set_editor_render_state(render)
        self._sync_cam3d_workflow()

    def _clear_cam3d_selection_role(self, role: object) -> None:
        """Clear one role without persistence or geometry calculation."""

        if not isinstance(role, Cam3DSelectionRole):
            return
        state = self._cam3d_selection_service.clear_role(role)
        controller = self._cam3d_editor_binding_controller
        controller.set_live_context(self._current_cam3d_editor_context())
        render = controller.set_selection(state)
        self.cam3d_function_panel.set_selection_state(state)
        self.cam3d_function_panel.set_editor_render_state(render)
        self._sync_cam3d_workflow()

    def _show_cam_workspace(self) -> None:
        """Switch to MILL 2D without replacing the CAD/OCP viewport."""
        self.workspace_bar.set_active_workspace(WorkspaceId.MILL_2D)
        self.operation_manager_dock.show()
        self.operation_manager_dock.raise_()

    def _show_general_settings(self) -> None:
        """Open one modeless General Settings dialog instance."""
        if self._general_settings_dialog is None:
            self._general_settings_dialog = GeneralSettingsDialog(
                self._ui_scale_manager,
                service=self._translation_service,
                ai_assist_controller=self._ai_assist_controller,
                advisor_settings_service=self._advisor_settings_service,
                export_defaults_service=self._export_defaults_service,
                viewport_background_manager=self._viewport_background_manager,
                parent=self,
            )
            self._general_settings_dialog.destroyed.connect(
                lambda _object=None: setattr(
                    self, "_general_settings_dialog", None
                )
            )
        self._general_settings_dialog.show()
        self._general_settings_dialog.raise_()
        self._general_settings_dialog.activateWindow()

    def _apply_viewport_background(self, color: ObjectColor) -> None:
        """Update CAD and any live Simulation viewport without scene work."""
        self.viewport.set_background_color(color)
        window = self._machining_simulation_window
        setter = getattr(window, "set_background_color", None)
        if callable(setter):
            setter(color)

    def _apply_ui_scale(self, _percent: int | None = None) -> None:
        """Apply the shared logical scale without touching Windows/Qt DPI."""
        manager = self._ui_scale_manager
        manager.apply_runtime(self)
        self.setStyleSheet(manager.scale_stylesheet(self._base_style_sheet))
        self.menuBar().setStyleSheet(
            ribbon_menu_style_sheet(RibbonMetrics.from_scale_manager(manager))
        )
        # Applying the root stylesheet re-evaluates its RibbonButton
        # min-width rule. Reapply the component-owned, font-derived
        # minimums afterwards so localized labels remain authoritative.
        self._ribbon.apply_ui_scale(manager.current_percent)
        # Queue the native dock solve before the panel's deferred scroll restore
        # so the latter observes the final viewport/range.
        self._schedule_post_assembly_dock_containment()
        if hasattr(self, "unified_post_assembly_panel"):
            self.unified_post_assembly_panel.apply_ui_scale(manager)
        self._apply_post_assembly_compact_layout()
        if self.layout() is not None:
            self.layout().activate()

    def _schedule_post_assembly_dock_containment(self) -> None:
        """Coalesce one native dock pass after layout-affecting UI work."""

        if self._post_assembly_dock_containment_scheduled:
            return
        self._post_assembly_dock_containment_scheduled = True
        QTimer.singleShot(0, self._ensure_post_assembly_dock_contained)

    def _ensure_post_assembly_dock_contained(self) -> None:
        """Re-solve a stale native dock split only when it exceeds the client."""

        self._post_assembly_dock_containment_scheduled = False
        dock = getattr(self, "post_assembly_dock", None)
        if (
            dock is None
            or not dock.isVisible()
            or dock.isFloating()
            or self.dockWidgetArea(dock)
            not in {
                Qt.DockWidgetArea.LeftDockWidgetArea,
                Qt.DockWidgetArea.RightDockWidgetArea,
            }
        ):
            return
        layout = self.layout()
        if layout is not None:
            layout.activate()

        def dock_rect_in_client() -> QRect:
            top_left = dock.mapTo(self, dock.rect().topLeft())
            bottom_right = dock.mapTo(self, dock.rect().bottomRight())
            return QRect(top_left, bottom_right).normalized()

        client = self.rect()
        mapped = dock_rect_in_client()
        if (
            mapped.left() >= client.left()
            and mapped.top() >= client.top()
            and mapped.right() <= client.right()
            and mapped.bottom() <= client.bottom()
        ):
            return

        available_width = max(
            1,
            min(mapped.right(), client.right())
            - max(mapped.left(), client.left())
            + 1,
        )
        width_floor = max(dock.minimumWidth(), dock.minimumSizeHint().width())
        target_width = max(width_floor, min(dock.width(), available_width))
        self.resizeDocks(
            [dock],
            [target_width],
            Qt.Orientation.Horizontal,
        )
        if layout is not None:
            layout.activate()

        # QMainWindow can retain a stale splitter allocation when a locale
        # lowers a dock size hint. If the exact client-span request was not
        # honored, asking for Qt's own floor forces a fresh private layout
        # solve while leaving the dock fully resizable.
        mapped = dock_rect_in_client()
        if mapped.left() < client.left() or mapped.right() > client.right():
            horizontal_docks = [
                candidate
                for candidate in self.findChildren(QDockWidget)
                if candidate.isVisible()
                and not candidate.isFloating()
                and self.dockWidgetArea(candidate)
                in {
                    Qt.DockWidgetArea.LeftDockWidgetArea,
                    Qt.DockWidgetArea.RightDockWidgetArea,
                }
            ]
            width_floors = [
                max(candidate.minimumWidth(), candidate.minimumSizeHint().width())
                for candidate in horizontal_docks
            ]
            if horizontal_docks:
                self.resizeDocks(
                    horizontal_docks,
                    width_floors,
                    Qt.Orientation.Horizontal,
                )
                if layout is not None:
                    layout.activate()
            mapped = dock_rect_in_client()
            legacy_dock = getattr(self, "operation_manager_dock", None)
            if (
                (mapped.left() < client.left() or mapped.right() > client.right())
                and legacy_dock is not None
                and legacy_dock is not dock
                and legacy_dock.isVisible()
            ):
                legacy_dock.hide()
                self._responsive_collapsed_operation_manager = True
                if layout is not None:
                    layout.activate()
            mapped = dock_rect_in_client()
            if mapped.left() < client.left() or mapped.right() > client.right():
                # Re-adding an already docked standalone host asks Qt to rebuild
                # its private split from current style metrics and size hints.
                self.addDockWidget(self.dockWidgetArea(dock), dock)
                if layout is not None:
                    layout.activate()

    def _show_language_settings(self) -> None:
        """Open one modeless user-preference dialog."""
        if self._language_dialog is None:
            self._language_dialog = LanguageSettingsDialog(
                self._translation_service,
                self._locale_settings,
                self,
            )
            self._language_dialog.finished.connect(
                lambda _result: self._language_dialog.deleteLater()
                if self._language_dialog is not None
                else None
            )
            self._language_dialog.destroyed.connect(
                lambda _object=None: setattr(self, "_language_dialog", None)
            )
        self._language_dialog.show()
        self._language_dialog.raise_()
        self._language_dialog.activateWindow()

    def _show_data_locations(self) -> None:
        """Open the modeless fixed-root storage diagnostics page."""
        if self._application_paths is None or self._storage_bootstrap is None:
            return
        if self._data_locations_dialog is None:
            self._data_locations_dialog = DataLocationsDialog(
                self._application_paths,
                self._storage_bootstrap,
                parent=self,
            )
            self._data_locations_dialog.inspection_changed.connect(
                self._update_storage_notification
            )
            self._data_locations_dialog.destroyed.connect(
                lambda _object=None: setattr(
                    self,
                    "_data_locations_dialog",
                    None,
                )
            )
        self._data_locations_dialog.show()
        self._data_locations_dialog.raise_()
        self._data_locations_dialog.activateWindow()

    def _show_hms_backup(self, profile_ids: tuple[str, ...] = ()) -> None:
        if self._hms_backup_service is None or self._user_profile_service is None:
            return
        if self._backup_dialog is None:
            self._backup_dialog = BackupWizardDialog(
                self._hms_backup_service,
                self._user_profile_service,
                self,
            )
            if profile_ids:
                for identifier, checkbox in self._backup_dialog._profile_checks.items():
                    checkbox.setChecked(identifier in profile_ids)
            self._backup_dialog.destroyed.connect(
                lambda _object=None: setattr(self, "_backup_dialog", None)
            )
        self._backup_dialog.show()
        self._backup_dialog.raise_()
        self._backup_dialog.activateWindow()

    def _show_hms_restore(self) -> None:
        if self._hms_restore_service is None:
            return
        if self._restore_dialog is None:
            self._restore_dialog = RestoreWizardDialog(
                self._hms_restore_service,
                self,
            )
            self._restore_dialog.destroyed.connect(
                lambda _object=None: setattr(self, "_restore_dialog", None)
            )
        self._restore_dialog.show()
        self._restore_dialog.raise_()
        self._restore_dialog.activateWindow()

    def _show_user_profiles(self) -> None:
        if self._user_profile_service is None:
            return
        if self._profiles_dialog is None:
            self._profiles_dialog = UserProfilesDialog(
                self._user_profile_service,
                switch_callback=self._switch_user_profile,
                parent=self,
            )
            self._profiles_dialog.backup_requested.connect(self._show_hms_backup)
            self._profiles_dialog.restore_requested.connect(self._show_hms_restore)
            self._profiles_dialog.destroyed.connect(
                lambda _object=None: setattr(self, "_profiles_dialog", None)
            )
        self._profiles_dialog.show()
        self._profiles_dialog.raise_()
        self._profiles_dialog.activateWindow()

    def _switch_user_profile(self, profile_id: str) -> ProfileSwitchReport:
        if self._user_profile_service is None:
            raise ProfileError("User profile service is unavailable")
        report = self._user_profile_service.switch(
            profile_id,
            capture_current=self._capture_active_profile,
            apply_profile=self._apply_user_profile,
            capture_invariants=self._profile_switch_invariants,
        )
        self.statusBar().showMessage(
            ui_text("Profile switched without changing the workspace or project.")
            if report.success
            else ui_text("Profile switch failed; the previous profile was restored."),
            8000,
        )
        self._update_active_profile_status()
        return report

    def _update_active_profile_status(self) -> None:
        if not hasattr(self, "_profile_status"):
            return
        if self._user_profile_service is None:
            self._profile_status.hide()
            return
        try:
            index = self._user_profile_service.load_index()
            profile = self._user_profile_service.load(index.active_profile_id)
        except (OSError, RuntimeError, ValueError, TypeError, ProfileError):
            self._profile_status.hide()
            return
        self._profile_status.setProperty("localizationAuditDomainText", True)
        self._profile_status.setText(
            ui_text("Active profile: {0} · {1}").format(
                profile.display_name,
                profile.locale,
            )
        )
        self._profile_status.show()

    def _capture_active_profile(self, profile: UserProfile) -> UserProfile:
        commands = self._profile_command_registry()
        shortcuts = {
            command_id: action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
            for command_id, action in commands.items()
            if not action.shortcut().isEmpty()
        }
        quick_access = tuple(
            str(action.property("profileCommandId"))
            for action in self._quick_access_toolbar.actions()
            if action.property("profileCommandId")
        )
        ui_state = {
            "geometry_base64": bytes(self.saveGeometry().toBase64()).decode("ascii"),
            "dock_state_base64": bytes(self.saveState(1).toBase64()).decode("ascii"),
            "ribbon_visible": self._ribbon.isVisible(),
            "toolbar_visible": self._quick_access_toolbar.isVisible(),
        }
        return replace(
            profile,
            locale=self._translation_service.language.value,
            ui_state=ui_state,
            shortcuts=shortcuts,
            quick_access=quick_access,
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
            layout_description=profile.layout_description or "HMS",
        )

    def _apply_user_profile(self, profile: UserProfile) -> None:
        commands = self._profile_command_registry()
        shortcut_targets: dict[str, QAction] = {}
        normalized_shortcuts: dict[str, QKeySequence] = {}
        reserved = {"ALT+F4", "CTRL+ALT+DELETE", "CTRL+Q"}
        used: set[str] = set()
        for command_id, shortcut in profile.shortcuts.items():
            action = commands.get(command_id)
            if action is None:
                raise ProfileError(f"Unknown shortcut command ID: {command_id}")
            sequence = QKeySequence(shortcut)
            portable = sequence.toString(QKeySequence.SequenceFormat.PortableText).upper()
            if sequence.isEmpty() or portable in reserved or portable in used:
                raise ProfileError("Shortcut is invalid, reserved or conflicting")
            used.add(portable)
            shortcut_targets[command_id] = action
            normalized_shortcuts[command_id] = sequence
        quick_actions = [commands[item] for item in profile.quick_access if item in commands]
        for command_id, sequence in normalized_shortcuts.items():
            shortcut_targets[command_id].setShortcut(sequence)
        if quick_actions:
            self._quick_access_toolbar.clear()
            for action in quick_actions:
                self._quick_access_toolbar.addAction(action)
        state = profile.ui_state
        geometry = str(state.get("geometry_base64", ""))
        dock_state = str(state.get("dock_state_base64", ""))
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        if dock_state and not self.restoreState(
            QByteArray.fromBase64(dock_state.encode("ascii")), 1
        ):
            raise ProfileError("Profile dock state is incompatible")
        self._quick_access_toolbar.setVisible(bool(state.get("toolbar_visible", True)))
        self._ribbon.setVisible(bool(state.get("ribbon_visible", True)))
        locale = UiLanguage.coerce(profile.locale)
        self._translation_service.set_language(locale)
        if not self._locale_settings.save(locale):
            raise ProfileError("Profile locale could not be persisted")
        clamp_window_to_available_screens(self)

    def _profile_command_registry(self) -> dict[str, QAction]:
        registry = {
            f"project.{key}": action
            for key, action in self.project_controller.actions.items()
        }
        registry.update(
            {f"cad.{key}": action for key, action in self.cad_controller.actions.items()}
        )
        for command_id, action in registry.items():
            action.setProperty("profileCommandId", command_id)
        return registry

    def _profile_switch_invariants(self) -> tuple[object, object]:
        workspace = self.workspace_bar.active_workspace.value
        project = (
            id(self._project_service.current_project),
            id(self._project_service.current_document),
            self._project_service.is_dirty,
            self.cad_controller.active_document_id,
            self.cad_controller.active_source_id,
            tuple(item.selection_id for item in self._active_selection),
            self.cam_workspace.selected_identity,
            self.cam_workspace._active_editor_operation_id,
            id(self.cam_workspace._parallel_task),
            id(self.cam_workspace._z_level_task),
            id(self.cam_workspace._simulation_handle),
            self.cam_workspace._simulation_project_id,
            self.cam_workspace.post_tabs.currentIndex(),
            getattr(self._project_service, "_project_session_id", None),
        )
        return workspace, project

    def _update_storage_notification(self, inspection: object = None) -> None:
        if self._storage_notification is None or self._storage_bootstrap is None:
            return
        current = inspection or self._storage_bootstrap.inspect()
        self._storage_notification.update_inspection(current)

    def _language_changed(self, language: object) -> None:
        """Retranslate presentation only; project and worker state stay intact."""
        selected = UiLanguage.coerce(language)
        active_tab_indices = self._dock_tab_indices()
        apply_widget_font_tree(self, selected)
        # The helper changes the external font family while retaining the
        # manager's scaled size; rebase that family without changing scale.
        self._ui_scale_manager.notify_external_application_font_changed(
            already_scaled=True
        )
        self._apply_ui_scale(self._ui_scale_manager.current_percent)
        self.refresh_localized_layout(active_tab_indices)
        for model in self.findChildren(QAbstractItemModel):
            retranslate = getattr(model, "_retranslate", None)
            if callable(retranslate):
                retranslate(selected)
        self.viewport.retranslate_status()
        self.operation_manager_host.retranslate_ui(selected)
        self.machining_simulation_action.setText(ui_text("Machining simulation"))
        simulation_window = self._machining_simulation_window
        if simulation_window is not None and isValid(simulation_window):
            retranslate = getattr(simulation_window, "retranslate", None)
            if callable(retranslate):
                retranslate()
        if hasattr(self, "unified_post_assembly_panel"):
            self.post_assembly_action.setText(
                ui_text("Post / Program Assembly")
            )
        if hasattr(self, "cam3d_function_panel"):
            self.cam3d_function_action.setText(ui_text("CAM 3D Function UI"))
            self.cam3d_function_action.setToolTip(
                ui_text("Open CAM 3D Function UI")
            )
            self.cam3d_function_dock.setWindowTitle(
                ui_text("CAM 3D Function UI")
            )
            self.cam3d_function_dock.setAccessibleName(
                ui_text("CAM 3D Function UI")
            )
            self.cam3d_function_panel.retranslate_ui(selected)
        self._general_settings_action.setText(ui_text("General settings"))
        self._general_settings_action.setToolTip(
            ui_text("Open general settings...")
        )
        if self._project_service.current_project is not None:
            self._update_project_display(self._project_service.current_project)
        self._update_notification_center_text(
            len(self.project_controller.incoming_requests)
        )
        self._retranslate_output_log()
        if self._language_dialog is not None:
            self._language_dialog.retranslate_ui(selected)
        if self._data_locations_dialog is not None:
            self._data_locations_dialog.retranslate_ui(selected)
        if self._storage_notification is not None:
            self._storage_notification.retranslate_ui(selected)
        if self._backup_dialog is not None:
            self._backup_dialog.retranslate_ui(selected)
        if self._restore_dialog is not None:
            self._restore_dialog.retranslate_ui(selected)
        if self._profiles_dialog is not None:
            self._profiles_dialog.retranslate_ui(selected)
        localize_widget_tree(self)
        if hasattr(self, "cam3d_function_panel"):
            self.cam3d_function_panel.editor_widget.retranslate_ui()
        if self._lathe_review_host:
            self.lathe_workspace.retranslate_ui(selected)

    def refresh_localized_layout(
        self,
        active_tab_indices: dict[int, int] | None = None,
    ) -> None:
        """Reapply locale-aware sizing after generic tree translation."""
        preserved_active_tabs = (
            self._dock_tab_indices()
            if active_tab_indices is None
            else active_tab_indices
        )
        self._ribbon.retranslate_ui()
        self._refresh_compact_dock_titles(preserved_active_tabs)
        # QMainWindow refreshes its private dock tab bars after window-title
        # changes. Reapply presentation and the preserved active tab once that
        # native update completes.
        QTimer.singleShot(
            0,
            lambda: self._refresh_localized_native_chrome(
                preserved_active_tabs
            ),
        )

    def _refresh_localized_native_chrome(
        self,
        active_tab_indices: dict[int, int] | None = None,
    ) -> None:
        """Finalize localized native chrome after it is materialized."""

        if QGuiApplication.platformName().casefold() == "offscreen":
            self._realize_offscreen_dock_groups(force=True)
        for button in self.findChildren(QAbstractButton):
            object_name = button.objectName()
            if (
                object_name.startswith("qt_")
                or object_name in {"ScrollLeftButton", "ScrollRightButton"}
            ):
                localize_widget_tree(button)
        self._ribbon.apply_ui_scale(self._ui_scale_manager.current_percent)
        self._refresh_compact_dock_titles(active_tab_indices)

    def _retranslate_output_log(self) -> None:
        """Translate managed log lines while preserving arbitrary diagnostics."""
        if not hasattr(self, "_output"):
            return
        service = translation_service()
        lines = self._output.toPlainText().splitlines()
        translated: list[str] = []
        managed: dict[
            str,
            tuple[str, dict[str, object], frozenset[str]],
        ] = {}
        for line in lines:
            specification = self._managed_output_lines.get(line)
            if specification is not None:
                key, arguments, localized_arguments = specification
                rendered = self._render_localized_output(
                    key,
                    arguments,
                    localized_arguments,
                )
                translated.append(rendered)
                managed[rendered] = specification
                continue
            canonical = service.canonical_key(line)
            if canonical is not None:
                translated.append(service.translate(canonical))
                continue
            translated.append(line)
        self._managed_output_lines = managed
        value = "\n".join(translated)
        if value != self._output.toPlainText():
            self._output.setPlainText(value)

    def _show_function_editor(self) -> None:
        """Open the selected operation in the one shared CAM popup."""
        self.cam_function_popup.open_current_operation()

    def _open_machining_simulation(self) -> None:
        """Lazy-load the separate R241 workspace only on explicit user action."""

        self.cam_workspace.activate_simulation_workspace()
        window = self._machining_simulation_window
        if window is None or not isValid(window):
            from hms_cadcam.ui.machining_simulation_window import (
                MachiningSimulationWindow,
            )

            window = MachiningSimulationWindow(
                self.cam_workspace.capture_selected_simulation_inputs,
                self,
                precompute_provider=(
                    self.cam_workspace.load_selected_simulation_precompute
                ),
                background_color=self._viewport_background_manager.current_color,
            )
            self._machining_simulation_window = window
        retranslate = getattr(window, "retranslate", None)
        if callable(retranslate):
            retranslate()
        window.show()
        window.raise_()
        window.activateWindow()
        prepare = getattr(window, "prepare_scene", None)
        if callable(prepare):
            prepare()

    def _prepare_cam_popup_for_project_change(self) -> bool:
        """Protect an unapplied CAM draft before closing or replacing a project."""
        if self.function_editor_host.active_session is None:
            return True
        return self.function_editor_host.request_close()

    def _workspace_changed(self, workspace_value: str) -> None:
        """Update shell presentation only; never calculate, post or export."""
        workspace = self.workspace_bar.set_active_workspace(workspace_value)
        if workspace is WorkspaceId.MILL_2D:
            self.operation_manager_dock.show()
            self.operation_manager_dock.raise_()
        elif workspace is WorkspaceId.CAD:
            self.project_dock.show()
            self.project_dock.raise_()
            self.properties_dock.show()
            self.properties_dock.raise_()
        elif workspace is WorkspaceId.SIMULATION:
            self._open_machining_simulation()
            self.secondary_panel_host.select_simulation()
            self.secondary_dock.show()
            self.secondary_dock.raise_()
        elif workspace is WorkspaceId.LATHE and self._lathe_review_host:
            self.lathe_dock.show()
            self.lathe_dock.raise_()
        elif workspace is WorkspaceId.POST:
            self.secondary_panel_host.select_post()
            self.secondary_dock.show()
            self.secondary_dock.raise_()

    def reset_workspace_layout(self) -> None:
        """Reset only window/dock presentation and leave project state untouched."""
        self._layout_store.reset()
        self._apply_default_workspace_layout()
        self.resize(1500, 900)
        clamp_window_to_available_screens(self)

    def _apply_default_workspace_layout(
        self,
        *,
        reposition: bool = True,
    ) -> None:
        docks = (
            self.project_dock,
            self.operation_manager_dock,
            self.properties_dock,
            self.function_editor_dock,
            self.secondary_dock,
            self.output_dock,
            *((self.lathe_dock,) if self._lathe_review_host else ()),
        )
        for dock in docks:
            if dock.isFloating():
                dock.setFloating(False)
        if reposition:
            self.addDockWidget(
                Qt.DockWidgetArea.LeftDockWidgetArea,
                self.project_dock,
            )
            self.addDockWidget(
                Qt.DockWidgetArea.LeftDockWidgetArea,
                self.operation_manager_dock,
            )
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea,
                self.properties_dock,
            )
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea,
                self.function_editor_dock,
            )
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea,
                self.secondary_dock,
            )
            self.addDockWidget(
                Qt.DockWidgetArea.BottomDockWidgetArea,
                self.output_dock,
            )
        # Qt requires visible docks for tabifyDockWidget(). Show each pair
        # while establishing the group, then restore the default visibility.
        self.project_dock.show()
        self.operation_manager_dock.show()
        self.properties_dock.show()
        self.secondary_dock.show()
        if self.operation_manager_dock not in self.tabifiedDockWidgets(
            self.project_dock
        ):
            self.tabifyDockWidget(self.project_dock, self.operation_manager_dock)
        if self.secondary_dock not in self.tabifiedDockWidgets(
            self.properties_dock
        ):
            self.tabifyDockWidget(self.properties_dock, self.secondary_dock)
        self.project_dock.hide()
        self.properties_dock.hide()
        self.secondary_dock.hide()
        self.function_editor_dock.hide()
        if self._lathe_review_host:
            self.lathe_dock.hide()
        self.operation_manager_dock.show()
        self.operation_manager_dock.raise_()
        self.output_dock.show()
        self.resizeDocks(
            [self.operation_manager_dock],
            [OPERATION_MANAGER_DEFAULT_WIDTH],
            Qt.Orientation.Horizontal,
        )
        self.resizeDocks(
            [self.output_dock],
            [DIAGNOSTICS_DEFAULT_HEIGHT],
            Qt.Orientation.Vertical,
        )
        self.workspace_bar.set_active_workspace(WorkspaceId.HOME)
        self._responsive_collapsed_operation_manager = False

    def _restore_workspace_layout(self) -> None:
        snapshot = self._layout_store.restore(self)
        if snapshot is None:
            self._apply_default_workspace_layout(reposition=False)
        else:
            self.resizeDocks(
                [self.operation_manager_dock],
                [snapshot.operation_manager_width],
                Qt.Orientation.Horizontal,
            )
            self.resizeDocks(
                [self.output_dock],
                [snapshot.diagnostics_height],
                Qt.Orientation.Vertical,
            )
            workspace = self.workspace_bar.set_active_workspace(
                snapshot.active_workspace
            )
            if workspace is WorkspaceId.SIMULATION:
                self.secondary_panel_host.select_simulation()
            elif workspace is WorkspaceId.POST:
                self.secondary_panel_host.select_post()
        self.function_editor_dock.hide()
        if self._lathe_review_host:
            if self.workspace_bar.active_workspace is not WorkspaceId.LATHE:
                self.lathe_dock.hide()
        clamp_window_to_available_screens(self)

    def _current_geometry_reference(self):
        """Convert exactly one current viewer selection through the safe adapter."""
        selections = self.cad_controller.active_selection
        source_id = self.cad_controller.active_source_id
        mapping = self.cad_controller.persistent_object_map
        if len(selections) != 1 or source_id is None or mapping is None:
            raise GeometryPickError("Hãy chọn đúng một đối tượng CAD có persistent mapping.")
        selection = selections[0]
        if selection.topology is SelectionMode.FACE:
            return self._planar_face_resolver().bind_selection(selection)
        raise GeometryPickError("Planar Facing requires exactly one FACE selection.")

    def _current_cam3d_selection_source(self) -> Cam3DSelectionSource | None:
        """Return native-free viewport/project facts for the WP2A service."""

        session = self._project_service.current_project
        if session is None:
            return None
        workspace = self._project_service.current_workspace
        read_only = bool(
            workspace is not None
            and workspace.mode is DocumentMode.CAM_PROJECT
            and workspace.read_only
        )
        return Cam3DSelectionSource(
            project_id=session.manifest.project_id,
            project_generation=self._project_service.cam_generation,
            document_id=self.cad_controller.active_document_id,
            source_id=self.cad_controller.active_source_id,
            read_only=read_only,
            selections=self.cad_controller.active_selection,
        )

    def _bind_cam3d_selection_surface(
        self,
        selection: SelectionMetadata,
        role: CamSurfaceRole,
    ) -> CamSurfaceReference:
        """Resolve one FACE through the existing project-owned OCP adapter."""

        return self._parallel_surface_adapter().bind_selection(selection, role)

    def _parallel_surface_adapter(self) -> OcpCam3DSurfaceAdapter:
        """Return the active project-owned CAM 3D BRep adapter."""
        session = self.project_controller.service.current_project
        document_id = self.cad_controller.active_document_id
        source_id = self.cad_controller.active_source_id
        mapping = self.cad_controller.persistent_object_map
        if (
            not isinstance(self._cad_kernel, OcpCadKernel)
            or session is None
            or document_id is None
            or source_id is None
            or mapping is None
        ):
            raise GeometryPickError(
                "No active OCP CAD source is available for Parallel Finishing."
            )
        return OcpCam3DSurfaceAdapter(
            self._cad_kernel,
            document_id,
            source_id,
            session.manifest.project_id,
            mapping,
            source_revision=Revision(0),
        )

    def _current_parallel_surfaces(self) -> tuple[CamSurfaceReference, ...]:
        """Bind all currently selected BRep faces to persistent CAM 3D IDs."""
        selections = self.cad_controller.active_selection
        if not selections or any(
            item.topology is not SelectionMode.FACE for item in selections
        ):
            raise GeometryPickError(
                "Parallel Finishing requires one or more selected BRep FACE objects."
            )
        adapter = self._parallel_surface_adapter()
        return tuple(
            adapter.bind_selection(item, CamSurfaceRole.PART)
            for item in selections
        )

    def _current_parallel_bounds(self) -> tuple[object, ...]:
        """Return OCP-free bounds matching the current Parallel face selection."""
        selections = self.cad_controller.active_selection
        if not selections or any(
            item.topology is not SelectionMode.FACE for item in selections
        ):
            raise GeometryPickError(
                "Parallel Finishing requires one or more selected BRep FACE objects."
            )
        return tuple(item.bounding_box for item in selections)

    def _planar_face_resolver(self) -> OcpPlanarFaceResolver:
        session = self.project_controller.service.current_project
        document_id = self.cad_controller.active_document_id
        source_id = self.cad_controller.active_source_id
        mapping = self.cad_controller.persistent_object_map
        if (not isinstance(self._cad_kernel, OcpCadKernel) or session is None or
                document_id is None or source_id is None or mapping is None):
            raise GeometryPickError("No active OCP CAD source is available for planar FACE resolution.")
        unit = (LengthUnit.INCH if session.manifest.units.value == "inch" else LengthUnit.MM)
        return OcpPlanarFaceResolver(self._cad_kernel, document_id, source_id, mapping, unit)

    def _current_contour_reference(self) -> GeometryReference:
        selections = self.cad_controller.active_selection
        if len(selections) != 1 or selections[0].topology not in {SelectionMode.FACE, SelectionMode.WIRE}:
            raise GeometryPickError(
                "2D Contour/Pocket requires exactly one planar FACE or closed WIRE selection."
            )
        return self._contour_profile_resolver().bind_selection(selections[0])

    def _contour_profile_resolver(self) -> OcpContourProfileResolver:
        session = self.project_controller.service.current_project
        document_id = self.cad_controller.active_document_id
        source_id = self.cad_controller.active_source_id
        mapping = self.cad_controller.persistent_object_map
        if (not isinstance(self._cad_kernel, OcpCadKernel) or session is None or
                document_id is None or source_id is None or mapping is None):
            raise GeometryPickError(
                "No active OCP CAD source is available for Contour/Pocket profile resolution."
            )
        unit = LengthUnit.INCH if session.manifest.units.value == "inch" else LengthUnit.MM
        return OcpContourProfileResolver(self._cad_kernel, document_id, source_id, mapping, unit)

    def _resolve_planar_face_reference(
        self, reference: GeometryReference
    ) -> ResolvedMachiningGeometry:
        return self._planar_face_resolver().resolve(reference)

    def _resolve_contour_profile_reference(
        self, reference: GeometryReference
    ) -> ResolvedContourProfile:
        return self._contour_profile_resolver().resolve(reference)

    def _resolve_pocket_geometry_reference(
        self, reference: GeometryReference
    ) -> ResolvedPocketGeometry:
        session = self.project_controller.service.current_project
        if session is None:
            raise GeometryPickError("No active project is available for Pocket resolution.")
        unit = LengthUnit.INCH if session.manifest.units.value == "inch" else LengthUnit.MM
        resolver = PocketGeometryResolver(self._contour_profile_resolver())
        return resolver.resolve(PocketGeometryInput(reference, unit))

    def _current_drilling_reference(
        self,
        axis: Vector3,
    ) -> HoleReference | HolePattern:
        selections = self.cad_controller.active_selection
        if (
            not selections
            or any(
                selection.topology not in {
                    SelectionMode.VERTEX, SelectionMode.EDGE,
                }
                for selection in selections
            )
        ):
            raise GeometryPickError(
                "Drilling/Tapping requires BREP VERTEX or circular EDGE selections."
            )
        resolver = self._drilling_geometry_resolver()
        references = tuple(
            resolver.bind_selection(selection, axis=axis)
            for selection in selections
        )
        if len(references) == 1:
            return references[0]
        resolved = tuple(resolver.resolve(reference) for reference in references)
        failed = next((
            item for item in resolved
            if item.status is not GeometryResolutionStatus.RESOLVED
            or item.location is None
        ), None)
        if failed is not None:
            raise GeometryPickError(
                failed.diagnostics[0].message
                if failed.diagnostics
                else "Hole selection pattern could not be resolved."
            )
        return HolePattern(
            tuple(item.location for item in resolved if item.location is not None),
            references[0].unit,
        )

    def _drilling_geometry_resolver(self) -> OcpDrillingGeometryResolver:
        session = self.project_controller.service.current_project
        document_id = self.cad_controller.active_document_id
        source_id = self.cad_controller.active_source_id
        mapping = self.cad_controller.persistent_object_map
        if (
            not isinstance(self._cad_kernel, OcpCadKernel)
            or session is None
            or document_id is None
            or source_id is None
            or mapping is None
        ):
            raise GeometryPickError(
                "No active OCP CAD source is available for Drilling resolution."
            )
        unit = (
            LengthUnit.INCH
            if session.manifest.units.value == "inch"
            else LengthUnit.MM
        )
        return OcpDrillingGeometryResolver(
            self._cad_kernel, document_id, source_id, mapping, unit,
        )

    def _resolve_drilling_geometry(
        self,
        geometry: DrillGeometryInput,
        depth: DrillDepthDefinition,
    ) -> ResolvedDrillingGeometry:
        resolver = DrillingGeometryResolver(self._drilling_geometry_resolver())
        return resolver.resolve(geometry, depth)

    def _build_simulation_scene(
        self,
        inputs: SimulationInputSnapshot,
    ) -> tuple[CollisionScene, CollisionBackend]:
        """Resolve native collision geometry only on the active UI owner thread."""
        if not isinstance(self._cad_kernel, OcpCadKernel):
            raise RuntimeError("Open CASCADE simulation backend is unavailable")
        enabled_fixtures = tuple(
            fixture for fixture in inputs.setup.fixtures if fixture.enabled
        )
        if enabled_fixtures:
            document_id = self.cad_controller.active_document_id
            source_id = self.cad_controller.active_source_id
            persistent_map = self.cad_controller.persistent_object_map
            tree = self.cad_controller.active_tree
            if (
                document_id is None
                or source_id is None
                or persistent_map is None
                or tree is None
            ):
                raise RuntimeError("Fixture geometry is not owned by the active CAD document")
            resolver = ActiveOcpFixtureResolver(
                self._cad_kernel,
                document_id,
                source_id,
                persistent_map,
                tree,
                inputs.artifact.unit,
            )
        else:
            resolver = _NoFixtureResolver()
        backend = OcpSimulationCollisionBackend()
        return backend.build_scene(setup=inputs.setup, resolver=resolver), backend

    def _build_cad_toolbar(self) -> None:
        toolbar = QToolBar("CAD Viewer", self)
        toolbar.setObjectName("CadViewToolbar")
        toolbar.setMovable(False)
        self._cad_toolbar = toolbar
        for key in (
            "open_step",
            "open_brep",
            "open_iges",
            "open_stl",
            "fit_all",
            "view_isometric",
            "display_shaded_with_edges",
            "selection_solid",
            "selection_face",
            "selection_wire",
            "selection_edge",
            "selection_vertex",
            "measurement",
        ):
            toolbar.addAction(self.cad_controller.actions[key])
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    def _create_project_dock(self) -> QDockWidget:
        dock = QDockWidget("Geometry / Project", self)
        dock.setObjectName("ProjectManagerDock")
        dock.setProperty("compactTitleSource", "Geometry / Project")
        dock.setProperty(
            "fullTitleSource",
            "Geometry structure / Project Manager",
        )
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        tabs = QTabWidget()
        tabs.setObjectName("ManagerTabs")

        self._project_tree = QTreeWidget()
        self._project_tree.setObjectName("ProjectTree")
        self._project_tree.setHeaderLabels(["Đối tượng", "Trạng thái"])
        self._project_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._project_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._project_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._project_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        root = QTreeWidgetItem(["Chưa mở dự án", "—"])
        root.setDisabled(True)
        self._project_tree.addTopLevelItem(root)
        self._project_tree.itemExpanded.connect(self._populate_topology_children)
        self._project_tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self._project_tree.itemChanged.connect(self._tree_item_changed)
        self._project_tree.customContextMenuRequested.connect(
            self._show_topology_context_menu
        )
        tabs.addTab(self._project_tree, "Dự án")

        manager_sections = (
            ("levels", "Cao độ"),
            ("toolpaths", "Đường chạy dao"),
            ("planes", "Mặt phẳng"),
        )
        for internal_key, title in manager_sections:
            empty_tree = QTreeWidget()
            empty_tree.setObjectName(f"ProjectManager_{internal_key}")
            empty_tree.setProperty("managerSectionKey", internal_key)
            empty_tree.setHeaderHidden(True)
            placeholder = QTreeWidgetItem([f"{title} chưa khả dụng"])
            placeholder.setDisabled(True)
            empty_tree.addTopLevelItem(placeholder)
            tabs.addTab(empty_tree, title)
        dock.setWidget(tabs)
        return dock

    def _schedule_offscreen_dock_group_realization(
        self,
        _visible: bool | None = None,
    ) -> None:
        """Coalesce one dock-group solve for Qt's synthetic offscreen surface."""

        if (
            QGuiApplication.platformName().casefold() != "offscreen"
            or self._offscreen_dock_group_realization_scheduled
            or not self.isVisible()
        ):
            return
        self._offscreen_dock_group_realization_scheduled = True
        QTimer.singleShot(0, self._realize_offscreen_dock_groups)

    def _realize_offscreen_dock_groups(
        self,
        *,
        force: bool = False,
    ) -> None:
        """Materialize each visible semantic group once without locale churn."""

        if not isValid(self):
            return
        self._offscreen_dock_group_realization_scheduled = False
        changed = False
        for primary, secondary in (
            (self.project_dock, self.operation_manager_dock),
            (self.properties_dock, self.secondary_dock),
        ):
            identity = (primary.objectName(), secondary.objectName())
            if (
                (
                    not force
                    and identity in self._offscreen_realized_dock_groups
                )
                or (
                    not primary.isVisible()
                    and not secondary.isVisible()
                )
            ):
                continue
            active = (
                primary
                if not primary.visibleRegion().isEmpty()
                else secondary
            )
            primary.show()
            secondary.show()
            self._offscreen_realized_dock_groups.add(identity)
            self.tabifyDockWidget(primary, secondary)
            active.raise_()
            changed = True
        if changed:
            layout = self.layout()
            if layout is not None:
                layout.activate()
            self._refresh_compact_dock_titles()

    def _dock_tab_indices(self) -> dict[int, int]:
        """Capture native dock-group selection without changing layout."""
        return {
            id(tab_bar): tab_bar.currentIndex()
            for tab_bar in self.findChildren(QTabBar)
            if any(
                isinstance(tab_bar.tabData(index), int)
                for index in range(tab_bar.count())
            )
        }

    def _refresh_compact_dock_titles(
        self,
        active_tab_indices: dict[int, int] | None = None,
    ) -> None:
        """Use compact tabs while exposing each full localized dock label.

        Dock placement is deliberately absent here.  This method can run after
        every locale switch and must only update presentation state.
        """
        service = translation_service()
        definitions = (
            (
                self.project_dock,
                "Geometry / Project",
                "Geometry structure / Project Manager",
            ),
            (
                self.operation_manager_dock,
                "Operations",
                "Operation Manager",
            ),
            (
                self.secondary_dock,
                "Post",
                "Simulation / Post",
            ),
        )
        preserved_active_tabs = (
            self._dock_tab_indices()
            if active_tab_indices is None
            else active_tab_indices
        )
        localized: dict[str, tuple[str, str]] = {}
        for dock, compact_source, full_source in definitions:
            compact = service.translate_key(compact_source)
            full = service.translate_key(full_source)
            localized[compact_source] = (compact, full)
            dock.setWindowTitle(compact)
            dock.setToolTip(full)
            dock.setAccessibleName(full)
            dock.setAccessibleDescription(full)
            dock.setProperty("fullText", full)
            dock.setProperty("textAuditCategory", "dock_tab")

        source_to_compact = {
            source: compact_source
            for _dock, compact_source, full_source in definitions
            for source in (compact_source, full_source)
        }
        layout = self.layout()
        if layout is not None:
            # The offscreen QPA defers its private dock-row solve until the
            # main-window layout is explicitly activated. This updates
            # geometry only; dock placement and object identity stay intact.
            layout.activate()
        visible_group_bottoms = {
            dock.geometry().bottom() + 1
            for dock in (
                self.project_dock,
                self.operation_manager_dock,
                self.properties_dock,
                self.secondary_dock,
            )
            if dock.isVisible() and not dock.visibleRegion().isEmpty()
        }
        seen_native_rows: set[
            tuple[tuple[int, int, int, int], tuple[str, ...]]
        ] = set()
        for tab_bar in self.findChildren(QTabBar):
            # Qt leaves private placeholder tab bars behind for individual
            # docks. Only a native dock-group bar carries the QDockWidget
            # pointer payload (PySide exposes it as an integer). Tagging those
            # bars prevents stale placeholders from being treated as a second
            # semantic row by the localization audit.
            if not any(
                isinstance(tab_bar.tabData(index), int)
                for index in range(tab_bar.count())
            ) or not any(
                abs(tab_bar.geometry().top() - bottom) <= 2
                for bottom in visible_group_bottoms
            ):
                tab_bar.setProperty("hmsDockTabBar", False)
                continue
            native_row = (
                tab_bar.geometry().getRect(),
                tuple(
                    str(tab_bar.tabData(index))
                    for index in range(tab_bar.count())
                ),
            )
            if native_row in seen_native_rows:
                tab_bar.setProperty("hmsDockTabBar", False)
                continue
            seen_native_rows.add(native_row)
            full_labels: list[str] = []
            semantic_ids: list[str] = []
            compact_sources = [""] * tab_bar.count()
            for index in range(tab_bar.count()):
                canonical = service.canonical_key(tab_bar.tabText(index))
                compact_source = source_to_compact.get(str(canonical))
                if compact_source is None:
                    continue
                compact, full = localized[compact_source]
                tab_bar.setTabText(index, compact)
                tab_bar.setTabToolTip(index, full)
                full_labels.append(full)
                semantic_ids.append(str(tab_bar.tabData(index)))
                compact_sources[index] = compact_source
            if full_labels:
                accessible = " · ".join(dict.fromkeys(full_labels))
                tab_bar.setProperty("hmsDockTabBar", True)
                tab_bar.setProperty("dockTabSemanticIds", tuple(semantic_ids))
                tab_bar.setProperty(
                    "dockTabCompactSources",
                    tuple(compact_sources),
                )
                tab_bar.setAccessibleName(accessible)
                tab_bar.setAccessibleDescription(accessible)
                tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
                tab_bar.setUsesScrollButtons(False)
                required_width = sum(
                    max(64, tab_bar.fontMetrics().horizontalAdvance(
                        tab_bar.tabText(index)
                    ) + 24)
                    for index in range(tab_bar.count())
                )
                tab_bar.setMinimumWidth(required_width)
                preserved_index = preserved_active_tabs.get(id(tab_bar))
                if (
                    preserved_index is not None
                    and 0 <= preserved_index < tab_bar.count()
                ):
                    tab_bar.setCurrentIndex(preserved_index)

    def _create_properties_dock(self) -> QDockWidget:
        dock = QDockWidget("Thuộc tính", self)
        dock.setObjectName("PropertiesDock")
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self._properties_table = QTableWidget(0, 2)
        self._properties_table.setHorizontalHeaderLabels(["Thuộc tính", "Giá trị"])
        self._properties_table.verticalHeader().setVisible(False)
        self._properties_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._properties_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._properties_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        dock.setWidget(self._properties_table)
        self._show_document_properties()
        return dock

    def _create_output_dock(self) -> QDockWidget:
        dock = QDockWidget("Đầu ra / Nhật ký", self)
        dock.setObjectName("OutputDock")
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        self._output = QPlainTextEdit()
        self._output.setObjectName("OutputLog")
        self._output.setReadOnly(True)
        self._output.setPlainText(
            ui_text("HMS CAD/CAM is ready.")
            + "\n"
            + ui_text(
                "CAD production viewer is ready; CAM is not integrated."
            )
        )
        dock.setWidget(self._output)
        return dock

    def _build_status_bar(self) -> None:
        status = self.statusBar()
        status.showMessage("Sẵn sàng")
        if self._storage_bootstrap is not None:
            self._storage_notification = StorageNotificationBar(self)
            self._storage_notification.details_requested.connect(
                self._show_data_locations
            )
            self._storage_notification.recheck_requested.connect(
                self._update_storage_notification
            )
            status.addWidget(self._storage_notification, 2)
            self._update_storage_notification()
        self._project_status = QLabel("KHÔNG CÓ DỰ ÁN")
        self._project_status.setObjectName("StatusLabel")
        status.addPermanentWidget(self._project_status, 1)
        self._profile_status = QLabel()
        self._profile_status.setObjectName("StatusLabel")
        status.addPermanentWidget(self._profile_status)
        self._update_active_profile_status()
        self._cad_export_compact_context_widgets = [
            self._project_status,
            self._profile_status,
        ]
        self._cad_export_status = CadExportStatusSurface(
            self.export_controller.cancel_active_export,
            self,
        )
        self._cad_export_status.geometry_requirement_changed.connect(
            self._update_cad_export_status_allocation
        )
        status.addPermanentWidget(self._cad_export_status, 1)
        self.export_controller.operation_state_changed.connect(
            self._cad_export_status.handle_export_event
        )
        self._cad_loading_status = CadLoadingStatusSurface(
            self.cad_controller.cancel_active_import,
            self,
        )
        self._import_status = self._cad_loading_status.status_label
        status.addPermanentWidget(self._cad_loading_status, 1)
        self._cad_export_compact_context_widgets.append(
            self._cad_loading_status
        )
        self.cad_controller.loading_state_changed.connect(
            self._cad_loading_status.handle_loading_event
        )
        self._notification_center_button = QPushButton(
            ui_text("NOTIFICATIONS: 0")
        )
        self._notification_center_button.setObjectName(
            "IncomingGeometryNotificationCenterButton"
        )
        self._notification_center_button.setAccessibleName(
            "Trung tâm thông báo dữ liệu 3D"
        )
        self._notification_center_button.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )
        self._notification_center_button.setEnabled(False)
        self._notification_center_button.clicked.connect(
            self._open_incoming_notification_center
        )
        status.addPermanentWidget(self._notification_center_button)
        self._cad_export_compact_context_widgets.append(
            self._notification_center_button
        )
        for text in (
            "ĐỐI TƯỢNG: 0",
            "X: 0.000",
            "Y: 0.000",
            "Z: 0.000",
            "3D",
            "WCS: Top",
            "METRIC",
        ):
            label = QLabel(text)
            label.setObjectName("StatusLabel")
            status.addPermanentWidget(label)
            self._cad_export_compact_context_widgets.append(label)
        self._cad_export_context_maximum_widths = {
            widget: widget.maximumWidth()
            for widget in self._cad_export_compact_context_widgets
        }
        self._cad_export_context_compacted = False
        self._cad_export_allocation_runs = 0
        self._cad_export_requested_width = 0
        self._cad_export_allocation_timer = QTimer(self)
        self._cad_export_allocation_timer.setSingleShot(True)
        self._cad_export_allocation_timer.timeout.connect(
            self._apply_cad_export_status_allocation
        )

    @Slot(int)
    def _update_cad_export_status_allocation(self, required_width: int) -> None:
        """Schedule one coalesced status allocation after Qt geometry changes."""

        if not hasattr(self, "_cad_export_compact_context_widgets"):
            return
        self._cad_export_requested_width = max(0, int(required_width))
        timer = getattr(self, "_cad_export_allocation_timer", None)
        if timer is not None and not timer.isActive():
            timer.start(0)

    @Slot()
    def _apply_cad_export_status_allocation(self) -> None:
        """Allocate adaptive export geometry without changing semantic visibility."""

        status = self.statusBar()
        surface = self._cad_export_status
        layout = status.layout()
        if layout is None:
            return
        self._cad_export_allocation_runs += 1

        contents = status.contentsRect()
        margins = layout.contentsMargins()
        spacing = max(0, layout.spacing())
        available_left = contents.left() + margins.left()
        available_right = contents.right() - margins.right()
        direct_children = status.findChildren(
            QWidget,
            options=Qt.FindChildOption.FindDirectChildrenOnly,
        )
        for widget in direct_children:
            if (
                isinstance(widget, QSizeGrip)
                and widget.isVisible()
                and widget.geometry().width() > 0
            ):
                available_right = min(
                    available_right,
                    widget.geometry().left() - max(1, spacing),
                )
        available_width = max(0, available_right - available_left + 1)

        if surface.required_width <= 0:
            for widget, intrinsic_maximum in (
                self._cad_export_context_maximum_widths.items()
            ):
                widget.setMaximumWidth(intrinsic_maximum)
            self._cad_export_context_compacted = False
            surface.set_available_width(None)
            layout.invalidate()
            layout.activate()
            return

        compact = (
            surface.required_width > available_width
        )
        intrinsic_widgets = tuple(
            widget
            for widget in self._cad_export_compact_context_widgets
            if not widget.isHidden()
        )
        if compact:
            context_maximum = 0
        elif intrinsic_widgets:
            surface_target = min(
                surface.required_width,
                max(surface.minimum_content_width, surface.wrapped_width),
            )
            spacing_budget = spacing * (len(intrinsic_widgets) + 1)
            context_maximum = max(
                0,
                (
                    available_width
                    - surface_target
                    - spacing_budget
                )
                // len(intrinsic_widgets),
            )
        else:
            context_maximum = 0
        for widget, intrinsic_maximum in (
            self._cad_export_context_maximum_widths.items()
        ):
            widget.setMaximumWidth(
                min(intrinsic_maximum, context_maximum)
                if widget in intrinsic_widgets
                else intrinsic_maximum
            )
        self._cad_export_context_compacted = compact

        surface.set_available_width(available_width)
        layout.invalidate()
        layout.activate()

        surface_left = max(available_left, surface.geometry().left())
        allocated_width = max(
            0,
            min(surface.geometry().right(), available_right)
            - surface_left
            + 1,
        )
        surface.set_available_width(allocated_width)
        layout.invalidate()
        layout.activate()

    def _current_display_state(self) -> ProjectSession | WorkspaceState | None:
        """Return rich project data or typed standalone-document state."""
        return (
            self.project_controller.service.current_project
            or self.project_controller.service.current_workspace
        )

    def _update_project_display(self, session: object) -> None:
        self._tree_sync_guard = True
        self._project_tree.clear()
        self._object_items = {}
        if isinstance(session, WorkspaceState):
            dirty_marker = " *" if session.dirty else ""
            root = QTreeWidgetItem(
                [
                    session.display_name,
                    "Đã sửa" if session.dirty else "Đã lưu",
                ]
            )
            root.addChild(
                QTreeWidgetItem(["Chế độ", session.mode.display_text])
            )
            root.addChild(
                QTreeWidgetItem(
                    [
                        "Tệp nguồn",
                        "—"
                        if session.source_path is None
                        else session.source_path.name,
                    ]
                )
            )
            root.addChild(
                QTreeWidgetItem(
                    [
                        "Tài liệu HMS",
                        "Chưa lưu"
                        if session.physical_path is None
                        else str(session.physical_path),
                    ]
                )
            )
            self._append_cad_document_node(root)
            self._project_tree.addTopLevelItem(root)
            root.setExpanded(True)
            self.setWindowTitle(
                f"HMS CAD/CAM — {session.mode.display_text} — "
                f"{session.display_name}{dirty_marker}"
            )
            physical = (
                "CHƯA LƯU .HMS"
                if session.physical_path is None
                else str(session.physical_path)
            )
            self._project_status.setText(
                f"{session.mode.display_text.upper()}: {physical}"
            )
            self._tree_sync_guard = False
            return
        if not isinstance(session, ProjectSession):
            root = QTreeWidgetItem(["Chưa mở dự án", "—"])
            self._append_cad_document_node(root)
            root.setDisabled(self._active_document_metadata is None)
            self._project_tree.addTopLevelItem(root)
            self.setWindowTitle(ui_text("HMS CAD/CAM — Design"))
            self._project_status.setText(ui_text("NO PROJECT"))
            self._tree_sync_guard = False
            return
        dirty_marker = " *" if session.is_dirty else ""
        root = QTreeWidgetItem(
            [
                session.manifest.project_name,
                ui_text("Edited") if session.is_dirty else ui_text("Saved"),
            ]
        )
        root.addChild(QTreeWidgetItem([ui_text("Units"), session.manifest.units.value]))
        sources = QTreeWidgetItem(
            [ui_text("Source files"), str(len(session.manifest.source_files))]
        )
        for record in session.manifest.source_files:
            source_item = QTreeWidgetItem(
                [record.original_name, record.sha256[:12]]
            )
            if record.internal_filename:
                source_item.addChild(
                    QTreeWidgetItem(
                        [ui_text("Internal copy"), record.internal_filename]
                    )
                )
            if record.working_geometry_path:
                source_item.addChild(
                    QTreeWidgetItem(
                        [ui_text("Working geometry"), record.working_geometry_path]
                    )
                )
            sources.addChild(source_item)
            source_item.setExpanded(True)
        root.addChild(sources)
        self._append_cad_document_node(root)
        self._project_tree.addTopLevelItem(root)
        root.setExpanded(True)
        sources.setExpanded(True)
        self.setWindowTitle(
            f"{ui_text('HMS CAD/CAM — CAM project')} — "
            f"{session.manifest.project_name}{dirty_marker}"
        )
        self._project_status.setProperty("localizationAuditDomainText", True)
        self._project_status.setText(
            f"{ui_text('CAM PROJECT')}: "
            f"{session.manifest.project_name}{dirty_marker}"
        )
        self._project_status.setToolTip(str(session.root_path))
        self._tree_sync_guard = False

    def _handle_project_change(self, session: object) -> None:
        new_project_key = (
            str(session.manifest.project_id)
            if isinstance(session, ProjectSession)
            else (
                str(session.project_id)
                if isinstance(session, WorkspaceState)
                and session.mode is DocumentMode.CAM_PROJECT
                else None
            )
        )
        previous_project_key = getattr(self, "_cam_popup_project_key", None)
        if (
            previous_project_key is not None
            and previous_project_key != new_project_key
        ):
            self.cam_function_popup.invalidate_project()
        self._cam_popup_project_key = new_project_key
        source_binding = None
        if isinstance(session, ProjectSession):
            source_binding = self._find_project_cad_source(session)
        if isinstance(session, WorkspaceState) and (
            session.mode is DocumentMode.CAD_DOCUMENT
        ):
            pass
        elif source_binding is None:
            self.cad_controller.bind_project(None)
        else:
            source_id, source_path = source_binding
            self.cad_controller.bind_project(source_path, source_id=source_id)
        if isinstance(session, ProjectSession):
            self.viewport.bind_simulation_project(
                session.manifest.project_id,
                self._project_service.cam_generation,
            )
        else:
            self.viewport.bind_simulation_project(None, None)
        self._update_project_display(session)
        self.cam_workspace.bind_project(
            session if isinstance(session, ProjectSession) else None
        )
        if self._cam3d_review_host:
            project_session = (
                session if isinstance(session, ProjectSession) else None
            )
            self._bind_cam3d_selection_project(project_session)
        self._post_assembly_adapter.set_session(
            session if isinstance(session, ProjectSession) else None
        )
        self._refresh_post_assembly_panel()
        if self._lathe_review_host:
            self._sync_lathe_context()

    def _incoming_geometry_changed(self, requests: object) -> None:
        if not isinstance(requests, tuple):
            return
        self.incoming_geometry_bar.set_requests(requests)
        if requests:
            self.incoming_geometry_dock.show()
        else:
            self.incoming_geometry_dock.hide()
        total = len(self.project_controller.incoming_requests)
        self._update_notification_center_text(total)
        self._notification_center_button.setEnabled(total > 0)

    def _update_notification_center_text(self, total: int) -> None:
        self._notification_center_button.setText(
            translation_service().format(
                "NOTIFICATIONS: {count}",
                count=total,
            )
        )

    def _open_incoming_notification_center(self) -> None:
        requests = self.project_controller.incoming_requests
        if requests:
            self.project_controller.request_incoming_preview(
                requests[0].request_id
            )

    def _incoming_geometry_preview_ready(self, preview: object) -> None:
        from hms_cadcam.project.geometry_transfer import IncomingGeometryPreview

        if not isinstance(preview, IncomingGeometryPreview):
            return
        self.incoming_geometry_panel.set_preview(preview)
        self.properties_dock.hide()
        self.secondary_dock.hide()
        self.incoming_geometry_panel_dock.show()
        self.incoming_geometry_panel_dock.raise_()
        self.resizeDocks(
            [self.incoming_geometry_panel_dock],
            [max(480, self.height() - 260)],
            Qt.Orientation.Vertical,
        )

    def _incoming_geometry_apply_completed(self, _result: object) -> None:
        self.incoming_geometry_panel_dock.hide()
        self._append_output(
            "Cập nhật dữ liệu 3D hoàn tất; HMS không tự Calculate, "
            "Simulation hoặc Post."
        )

    def _update_cad_document(self, metadata: object) -> None:
        previous_document_id = (
            self._active_document_metadata.document_id
            if self._active_document_metadata is not None else None
        )
        self._active_document_metadata = (
            metadata if isinstance(metadata, CadDocumentMetadata) else None
        )
        self._active_selection = ()
        self._selected_object_ids = ()
        self._active_measurements = ()
        self._update_project_display(self._current_display_state())
        if hasattr(self, "cam_workspace"):
            current_document_id = (
                self._active_document_metadata.document_id
                if self._active_document_metadata is not None else None
            )
            self.cam_workspace.cad_context_changed(
                force_invalidate=(previous_document_id is not None and
                                  current_document_id is not None and
                                  previous_document_id != current_document_id)
            )
        self._show_document_properties()
        if self._cam3d_review_host and hasattr(self, "cam3d_function_panel"):
            self._bind_cam3d_selection_project(self._project_service.current_project)
        if self._lathe_review_host:
            self._sync_lathe_context()

    def _update_topology_tree(self, tree: object) -> None:
        self._active_document_tree = (
            tree if isinstance(tree, CadDocumentTree) else None
        )
        self._update_project_display(self._current_display_state())

    def _update_object_selection(self, document_id: object, items: object) -> None:
        active_document_id = (
            self._active_document_metadata.document_id
            if self._active_document_metadata is not None
            else None
        )
        if document_id != active_document_id or not isinstance(items, tuple):
            return
        if not all(isinstance(item, CadObjectId) for item in items):
            return
        if any(
            self._active_document_tree is None
            or self._active_document_tree.find(item) is None
            for item in items
        ):
            return
        self._selected_object_ids = items
        self._tree_sync_guard = True
        self._project_tree.clearSelection()
        for object_id in items:
            tree_item = self._ensure_object_item(object_id)
            if tree_item is not None:
                tree_item.setSelected(True)
        self._tree_sync_guard = False
        if items and not self._active_selection:
            self._show_object_properties(items[0])

    def _update_object_appearances(self, document_id: object, items: object) -> None:
        active_document_id = (
            self._active_document_metadata.document_id
            if self._active_document_metadata is not None
            else None
        )
        if document_id != active_document_id or not isinstance(items, tuple):
            return
        appearances: dict[CadObjectId, ObjectAppearance] = {}
        for item in items:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], CadObjectId)
                or not isinstance(item[1], ObjectAppearance)
            ):
                return
            appearances[item[0]] = item[1]
        self._object_appearances = appearances
        self._tree_sync_guard = True
        for object_id, tree_item in self._object_items.items():
            appearance = appearances.get(object_id)
            if appearance is not None:
                tree_item.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if appearance.visible
                    else Qt.CheckState.Unchecked,
                )
                node = tree_item.data(0, _OBJECT_NODE_ROLE)
                if isinstance(node, CadObjectNode) and node.xcaf_role is not None:
                    tree_item.setText(
                        1,
                        f"{node.xcaf_role.value.upper()} · {node.product_name} · "
                        f"{'Hiện' if appearance.visible else 'Ẩn'}",
                    )
                else:
                    tree_item.setText(1, "Hiện" if appearance.visible else "Ẩn")
        self._tree_sync_guard = False
        if self._selected_object_ids and not self._active_selection:
            self._show_object_properties(self._selected_object_ids[0])

    def _update_selection(self, document_id: object, items: object) -> None:
        if not isinstance(items, tuple) or not all(
            isinstance(item, SelectionMetadata) for item in items
        ):
            return
        active_document_id = (
            self._active_document_metadata.document_id
            if self._active_document_metadata is not None
            else None
        )
        if document_id != active_document_id:
            return
        if any(item.document_id != active_document_id for item in items):
            return
        self._active_selection = items
        if not items:
            self._show_document_properties()
            return
        item = items[0]
        self._show_selection_properties(item)

    def _update_measurements(self, document_id: object, results: object) -> None:
        if not isinstance(results, tuple) or not all(
            isinstance(result, MeasurementResult) for result in results
        ):
            return
        active_document_id = (
            self._active_document_metadata.document_id
            if self._active_document_metadata is not None
            else None
        )
        if document_id != active_document_id:
            return
        if any(result.document_id != active_document_id for result in results):
            return
        active_selection_ids = {
            item.selection_id for item in self._active_selection
        }
        if self._active_selection:
            if any(
                not result.selection_ids
                or not set(result.selection_ids).issubset(active_selection_ids)
                for result in results
            ):
                return
        elif any(result.selection_ids for result in results):
            return
        self._active_measurements = results
        if self._active_selection:
            self._show_selection_properties(self._active_selection[0])
        else:
            self._show_document_properties()

    def _show_selection_properties(self, item: SelectionMetadata) -> None:
        node = (
            self._active_document_tree.find(item.object_id)
            if self._active_document_tree is not None and item.object_id is not None
            else None
        )
        if node is not None and node.occurrence_id is not None:
            self._show_object_properties(node.object_id)
            return
        rows = [
            ("Loại cấu trúc hình học", item.topology.value.upper()),
            ("Selection ID", item.selection_id),
            ("Document ID", str(item.document_id)),
            ("Hộp bao", _format_bounds(item.bounding_box)),
            ("Số lượng chọn", str(len(self._active_selection))),
        ]
        rows.extend(_measurement_rows(self._active_measurements))
        self._set_properties(tuple(rows))

    def _show_object_properties(self, object_id: CadObjectId) -> None:
        tree = self._active_document_tree
        node = tree.find(object_id) if tree is not None else None
        appearance = self._object_appearances.get(object_id)
        if node is None or appearance is None:
            self._show_document_properties()
            return
        rows = [
            ("Object", node.label),
            ("Object ID", str(node.object_id)),
            ("Loại", node.kind.value.upper()),
            ("Hiển thị", "Có" if appearance.visible else "Không"),
            ("Effective color", appearance.color.to_hex()),
            ("Effective transparency", f"{appearance.transparency:.2f}"),
            ("Hộp bao", _format_bounds(node.bounding_box)),
        ]
        if node.occurrence_id is not None:
            assert node.absolute_transform is not None
            rows.extend(
                (
                    ("Occurrence name", node.label),
                    ("Product name", node.product_name or "-"),
                    ("Role", node.xcaf_role.value.upper() if node.xcaf_role else "-"),
                    (
                        "Absolute translation",
                        _format_translation(node.absolute_transform.translation),
                    ),
                    (
                        "Source appearance",
                        _format_source_appearance(node.source_appearance),
                    ),
                )
            )
        self._set_properties(tuple(rows))

    def _show_document_properties(self) -> None:
        metadata = self._active_document_metadata
        if metadata is None:
            self._set_properties((("CAD document", "None"),))
            return
        rows = [
            ("Document ID", str(metadata.document_id)),
            ("Định dạng", metadata.cad_format.value.upper()),
            ("Loại hình học", metadata.geometry_kind.value.upper()),
            ("Đơn vị", _format_units(metadata)),
        ]
        if metadata.topology_counts is not None:
            counts = metadata.topology_counts
            rows.append(
                (
                    "Khối / Mặt / Cạnh",
                    f"{counts.solids} / {counts.faces} / {counts.edges}",
                )
            )
        if metadata.mesh_statistics is not None:
            statistics = metadata.mesh_statistics
            rows.append(
                (
                    "Vertex / Triangle",
                    f"{statistics.vertices} / {statistics.triangles}",
                )
            )
        rows.append(("Hộp bao", _format_bounds(metadata.bounding_box)))
        rows.extend(_measurement_rows(self._active_measurements))
        self._set_properties(tuple(rows))

    def _set_properties(self, rows: tuple[tuple[str, str], ...]) -> None:
        self._properties_table.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            self._properties_table.setItem(row, 0, QTableWidgetItem(ui_text(name)))
            self._properties_table.setItem(row, 1, QTableWidgetItem(ui_text(value)))

    def _append_cad_document_node(self, root: QTreeWidgetItem) -> None:
        metadata = self._active_document_metadata
        if metadata is None:
            return
        document = QTreeWidgetItem(
            ["Tài liệu CAD", metadata.cad_format.value.upper()]
        )
        tree = self._active_document_tree
        if tree is not None and tree.document_id == metadata.document_id:
            self._configure_object_item(document, tree.root)
        document.addChild(QTreeWidgetItem(["ID tài liệu", str(metadata.document_id)]))
        document.addChild(QTreeWidgetItem(["Hình học", metadata.geometry_kind.value]))
        if metadata.topology_counts is not None:
            counts = metadata.topology_counts
            document.addChild(
                QTreeWidgetItem(
                    [
                        "Cấu trúc hình học",
                        f"S={counts.solids}, F={counts.faces}, E={counts.edges}",
                    ]
                )
            )
        if metadata.mesh_statistics is not None:
            statistics = metadata.mesh_statistics
            document.addChild(
                QTreeWidgetItem(
                    [
                        "Lưới",
                        f"V={statistics.vertices}, T={statistics.triangles}",
                    ]
                )
            )
        document.addChild(
            QTreeWidgetItem(["Hộp bao", _format_bounds(metadata.bounding_box)])
        )
        if tree is not None and tree.document_id == metadata.document_id:
            is_xcaf = any(node.occurrence_id is not None for node in tree.root.children)
            topology = QTreeWidgetItem(
                [
                    "Thực thể lắp ráp" if is_xcaf else "Đối tượng cấu trúc hình học",
                    "Nạp khi cần",
                ]
            )
            topology.setData(0, _TOPOLOGY_GROUP_ROLE, True)
            for node in tree.root.children:
                topology.addChild(self._make_object_item(node))
            document.addChild(topology)
            topology.setExpanded(True)
            document.setExpanded(True)
        root.addChild(document)

    def _make_object_item(self, node: CadObjectNode) -> QTreeWidgetItem:
        appearance = self._object_appearances.get(node.object_id, ObjectAppearance())
        detail = (
            f"{node.xcaf_role.value.upper()} · {node.product_name}"
            if node.xcaf_role is not None
            else ("Hiện" if appearance.visible else "Ẩn")
        )
        item = QTreeWidgetItem(
            [node.label, detail]
        )
        self._configure_object_item(item, node)
        if node.children:
            placeholder = QTreeWidgetItem(["Đang chờ mở…", ""])
            placeholder.setData(0, _PLACEHOLDER_ROLE, True)
            placeholder.setDisabled(True)
            item.addChild(placeholder)
        return item

    def _configure_object_item(
        self,
        item: QTreeWidgetItem,
        node: CadObjectNode,
    ) -> None:
        appearance = self._object_appearances.get(node.object_id, ObjectAppearance())
        item.setData(0, _OBJECT_ID_ROLE, node.object_id)
        item.setData(0, _DOCUMENT_ID_ROLE, node.document_id)
        item.setData(0, _OBJECT_NODE_ROLE, node)
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEnabled
        )
        item.setCheckState(
            0,
            Qt.CheckState.Checked if appearance.visible else Qt.CheckState.Unchecked,
        )
        self._object_items[node.object_id] = item

    def _populate_topology_children(self, item: QTreeWidgetItem) -> None:
        node = item.data(0, _OBJECT_NODE_ROLE)
        if not isinstance(node, CadObjectNode) or not node.children:
            return
        if item.childCount() != 1 or not item.child(0).data(0, _PLACEHOLDER_ROLE):
            return
        previous_guard = self._tree_sync_guard
        self._tree_sync_guard = True
        try:
            item.takeChild(0)
            for child in node.children:
                item.addChild(self._make_object_item(child))
        finally:
            self._tree_sync_guard = previous_guard

    def _tree_selection_changed(self) -> None:
        if self._tree_sync_guard:
            return
        selected = self._project_tree.selectedItems()
        object_ids = tuple(
            item.data(0, _OBJECT_ID_ROLE)
            for item in selected
            if isinstance(item.data(0, _OBJECT_ID_ROLE), CadObjectId)
        )
        document_id = (
            self._active_document_metadata.document_id
            if self._active_document_metadata is not None
            else None
        )
        if document_id is not None:
            self.cad_controller.select_tree_objects(document_id, object_ids)

    def _tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._tree_sync_guard or column != 0:
            return
        object_id = item.data(0, _OBJECT_ID_ROLE)
        document_id = item.data(0, _DOCUMENT_ID_ROLE)
        if not isinstance(object_id, CadObjectId) or not isinstance(
            document_id,
            CadDocumentId,
        ):
            return
        visible = item.checkState(0) is Qt.CheckState.Checked
        self.cad_controller.set_object_visibility(document_id, object_id, visible)

    def _show_topology_context_menu(self, position) -> None:
        item = self._project_tree.itemAt(position)
        if item is None:
            return
        object_id = item.data(0, _OBJECT_ID_ROLE)
        document_id = item.data(0, _DOCUMENT_ID_ROLE)
        if not isinstance(object_id, CadObjectId) or not isinstance(
            document_id,
            CadDocumentId,
        ):
            return
        menu = QMenu(self)
        menu.addAction(
            "Hiện",
            lambda: self.cad_controller.set_object_visibility(
                document_id,
                object_id,
                True,
            ),
        )
        menu.addAction(
            "Ẩn",
            lambda: self.cad_controller.set_object_visibility(
                document_id,
                object_id,
                False,
            ),
        )
        menu.addSeparator()
        menu.addAction(
            "Cô lập",
            lambda: self.cad_controller.isolate_object(document_id, object_id),
        )
        menu.addAction(
            "Bỏ cô lập",
            lambda: self.cad_controller.reset_isolate(document_id),
        )
        menu.addSeparator()
        menu.addAction(
            "Màu…",
            lambda: self._choose_object_color(document_id, object_id),
        )
        menu.addAction(
            "Độ trong suốt…",
            lambda: self._choose_object_transparency(document_id, object_id),
        )
        menu.addAction(
            "Khôi phục hiển thị",
            lambda: self.cad_controller.reset_object_appearance(
                document_id, object_id
            ),
        )
        menu.exec(self._project_tree.viewport().mapToGlobal(position))

    def _choose_object_color(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None:
        current = self._object_appearances.get(object_id, ObjectAppearance()).color
        selected = QColorDialog.getColor(
            QColor.fromRgbF(current.red, current.green, current.blue),
            self,
            "Chọn màu đối tượng",
        )
        if selected.isValid():
            self.cad_controller.set_object_color(
                document_id,
                object_id,
                ObjectColor(selected.redF(), selected.greenF(), selected.blueF()),
            )

    def _choose_object_transparency(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> None:
        current = self._object_appearances.get(
            object_id,
            ObjectAppearance(),
        ).transparency
        value, accepted = QInputDialog.getDouble(
            self,
            "Transparency",
            "Giá trị (0.0–1.0):",
            current,
            0.0,
            1.0,
            2,
        )
        if accepted:
            self.cad_controller.set_object_transparency(
                document_id,
                object_id,
                value,
            )

    def _ensure_object_item(
        self,
        object_id: CadObjectId,
    ) -> QTreeWidgetItem | None:
        existing = self._object_items.get(object_id)
        if existing is not None:
            return existing
        tree = self._active_document_tree
        path = _object_path(tree.root, object_id) if tree is not None else ()
        for node in path:
            parent_item = self._object_items.get(node.object_id)
            if parent_item is not None:
                self._populate_topology_children(parent_item)
        return self._object_items.get(object_id)

    @staticmethod
    def _find_project_cad_source(
        session: ProjectSession,
    ) -> tuple[UUID, Path] | None:
        for record in reversed(session.manifest.source_files):
            candidates: list[Path] = []
            if record.working_geometry_path:
                candidates.append(
                    session.root_path / Path(record.working_geometry_path)
                )
            candidates.append(session.root_path / Path(record.stored_path))
            relative_path = getattr(record, "relative_path", None)
            if relative_path:
                candidates.append(session.root_path / str(relative_path))
            stored_name = getattr(record, "stored_name", None)
            if stored_name:
                candidates.append(session.root_path / "source" / str(stored_name))
            candidates.append(session.root_path / "source" / record.original_name)
            for candidate in candidates:
                if (
                    candidate.suffix.lower()
                    in {
                        ".step",
                        ".stp",
                        ".brep",
                        ".brp",
                        ".iges",
                        ".igs",
                        ".stl",
                    }
                    and candidate.is_file()
                ):
                    return record.source_id, candidate
        return None

    def _sync_lathe_context(self) -> None:
        """Project existing immutable runtime facts into one Lathe UI session."""

        if not self._lathe_review_host:
            return
        session = self._project_service.current_project
        document_id = self.cad_controller.active_document_id
        source_id = self.cad_controller.active_source_id
        if session is None or document_id is None or source_id is None:
            self._lathe_session_controller.update_context(None)
            self.workspace_bar.configure_lathe(
                enabled=False,
                explanation=ui_text(
                    "lathe.presenter.project_context_unavailable"
                ),
            )
            self.lathe_dock.hide()
            return
        try:
            snapshot = self._project_service.cam_snapshot
            generation = self._project_service.cam_generation
        except RuntimeError:
            self._lathe_session_controller.update_context(None)
            self.workspace_bar.configure_lathe(
                enabled=False,
                explanation=ui_text(
                    "lathe.presenter.project_context_unavailable"
                ),
            )
            self.lathe_dock.hide()
            return
        if self._lathe_persistence_host:
            for diagnostic in session.lathe_restore_diagnostics:
                key = (diagnostic.code.value, diagnostic.subject_id)
                if key in self._reported_lathe_diagnostics:
                    continue
                self._reported_lathe_diagnostics.add(key)
                self._append_localized_output(
                    diagnostic.code.value,
                    subject=diagnostic.subject_id,
                )
        active_job = next(
            (
                item
                for item in snapshot.jobs
                if item.job_id == snapshot.active_job_id
            ),
            None,
        )
        active_setup = None if active_job is None else active_job.active_setup
        setup_id = None if active_setup is None else active_setup.setup_id
        stock = (
            active_setup.stock
            if active_setup is not None and isinstance(active_setup.stock, CylinderStock)
            else None
        )
        workspace = self._project_service.current_workspace
        read_only = bool(workspace is not None and workspace.read_only)
        self._lathe_session_controller.update_context(
            LatheUiContext(
                session.manifest.project_id,
                document_id,
                source_id,
                generation,
                setup_id,
                read_only,
                snapshot.tool_definitions,
                snapshot.holder_definitions,
                snapshot.tool_assemblies,
                stock,
            )
        )
        self.workspace_bar.configure_lathe(
            enabled=True,
            explanation=ui_text("lathe.workspace.available"),
        )

    def _current_lathe_selection_context(
        self,
    ) -> LatheSelectionContext | None:
        """Return current OCP-free selection facts for the live Lathe session."""

        if not self._lathe_review_host:
            return None
        context = self._lathe_session_controller.context
        if context is None:
            return None
        document_id = self.cad_controller.active_document_id
        source_id = self.cad_controller.active_source_id
        if document_id != context.document_id or source_id != context.source_id:
            return None
        try:
            generation = self._project_service.cam_generation
        except RuntimeError:
            return None
        return LatheSelectionContext(
            document_id,
            source_id,
            generation,
            self.cad_controller.active_selection,
        )

    def _lathe_selection_changed(
        self, _document_id: object, _items: object
    ) -> None:
        if self._lathe_review_host:
            self.lathe_workspace.refresh_geometry_selection()

    def _lathe_availability_changed(
        self, available: bool, reason: str
    ) -> None:
        if not self._lathe_review_host:
            return
        self.workspace_bar.configure_lathe(
            enabled=available,
            explanation=ui_text(
                "lathe.workspace.available" if available else reason
            ),
        )
        if (
            not available
            and self.workspace_bar.active_workspace is WorkspaceId.LATHE
        ):
            self.workspace_bar.set_active_workspace(WorkspaceId.HOME)
            self.lathe_dock.hide()

    def _update_import_status(self, status: str) -> None:
        severity = "error" if "lỗi" in status.casefold() else "info"
        if hasattr(self, "diagnostics_host"):
            self.diagnostics_host.set_activity(f"CAD: {status}", severity=severity)

    def _append_output(self, text: str) -> None:
        self._output.appendPlainText(text)
        if hasattr(self, "diagnostics_host"):
            folded = text.casefold()
            if "lỗi" in folded or "failed" in folded:
                self.diagnostics_host.set_activity(text, severity="error")
            elif "cảnh báo" in folded or "warning" in folded:
                self.diagnostics_host.set_activity(text, severity="warning")

    def _append_localized_output(
        self,
        key: str,
        *,
        localized_arguments: frozenset[str] = frozenset(),
        **arguments: object,
    ) -> None:
        specification = (key, dict(arguments), localized_arguments)
        rendered = self._render_localized_output(*specification)
        self._managed_output_lines[rendered] = specification
        self._append_output(rendered)

    @staticmethod
    def _render_localized_output(
        key: str,
        arguments: dict[str, object],
        localized_arguments: frozenset[str],
    ) -> str:
        service = translation_service()
        rendered_arguments = {
            name: (
                service.translate(value)
                if name in localized_arguments
                else value
            )
            for name, value in arguments.items()
        }
        return service.format(key, **rendered_arguments)

    def set_drop_overlay_visible(self, visible: bool) -> None:
        """Show/hide the real drop overlay without taking keyboard focus."""
        self._drop_overlay.setGeometry(self.viewport.rect())
        self._drop_overlay.setVisible(visible)
        if visible:
            self._drop_overlay.raise_()

    def dragEnterEvent(  # noqa: N802 - Qt API name
        self, event: QDragEnterEvent
    ) -> None:
        """Accept supported local files and reveal the Vietnamese overlay."""
        paths = self._drop_paths(event)
        if paths and all(_is_supported_open_path(path) for path in paths):
            event.acceptProposedAction()
            self.set_drop_overlay_visible(True)
            return
        self.set_drop_overlay_visible(False)
        event.ignore()

    def dragLeaveEvent(  # noqa: N802 - Qt API name
        self, event: QDragLeaveEvent
    ) -> None:
        """Remove the overlay immediately when the drag leaves."""
        self.set_drop_overlay_visible(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt API name
        """Route one dropped file to exactly the same Open application command."""
        paths = self._drop_paths(event)
        self.set_drop_overlay_visible(False)
        if len(paths) == 1 and _is_supported_open_path(paths[0]):
            if self.cad_controller.open_dropped_path(paths[0]):
                event.acceptProposedAction()
                return
        event.ignore()

    @staticmethod
    def _drop_paths(event: QDragEnterEvent | QDropEvent) -> tuple[Path, ...]:
        urls = event.mimeData().urls()
        paths = tuple(
            Path(url.toLocalFile())
            for url in urls
            if url.isLocalFile() and url.toLocalFile()
        )
        return paths if len(paths) == len(urls) else ()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API name
        """Keep the viewport usable at the compact 1024-1279 px breakpoint."""
        super().resizeEvent(event)
        if hasattr(self, "_drop_overlay"):
            self._drop_overlay.setGeometry(self.viewport.rect())
        if hasattr(self, "_cad_export_compact_context_widgets"):
            self._update_cad_export_status_allocation(
                self._cad_export_status.required_width
            )
        if not hasattr(self, "operation_manager_dock"):
            return
        width = event.size().width()
        if (
            width < 1200
            and self.operation_manager_dock.isVisible()
        ):
            self.operation_manager_dock.hide()
            self._responsive_collapsed_operation_manager = True
        elif width >= 1280 and self._responsive_collapsed_operation_manager:
            self.operation_manager_dock.show()
            self.operation_manager_dock.raise_()
            self._responsive_collapsed_operation_manager = False
        self._apply_post_assembly_compact_layout()
        self._refresh_compact_dock_titles()
        self._schedule_post_assembly_dock_containment()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API name
        """Apply screen-aware minimums after native frame metrics are known."""
        super().showEvent(event)
        self._update_responsive_minimum()
        self._apply_post_assembly_compact_layout()
        self._schedule_offscreen_dock_group_realization()
        QTimer.singleShot(0, self._refresh_localized_native_chrome)

    def _update_responsive_minimum(self) -> None:
        """Keep the client minimum within the current screen work area."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect()
        frame_delta = QSize(
            max(0, self.frameGeometry().width() - self.geometry().width()),
            max(0, self.frameGeometry().height() - self.geometry().height()),
        )
        effective = (
            responsive_minimum_size(
                available, self._requested_minimum_size, frame_delta
            )
            if not available.isNull()
            else QSize(self._requested_minimum_size)
        )
        self._effective_minimum_size = effective
        if effective != self.minimumSize():
            self.setMinimumSize(effective)
        # The offscreen plugin exposes a synthetic 800px work area even when
        # callers request a larger deterministic render surface. It has no
        # native desktop boundary to protect, so retain the requested size;
        # real Windows screens continue to receive the containment pass.
        constrain_to_screen = (
            QGuiApplication.platformName().casefold() != "offscreen"
        )
        if not available.isNull() and constrain_to_screen:
            client_budget = QSize(
                max(1, available.width() - max(0, frame_delta.width())),
                max(1, available.height() - max(0, frame_delta.height())),
            )
            target = QSize(
                min(self.width(), client_budget.width()),
                min(self.height(), client_budget.height()),
            )
            if target != self.size():
                self.resize(target)
            frame = self.frameGeometry()
            if (
                frame.width() <= available.width()
                and frame.height() <= available.height()
            ):
                frame_x = min(
                    max(frame.x(), available.left()),
                    available.right() - frame.width() + 1,
                )
                frame_y = min(
                    max(frame.y(), available.top()),
                    available.bottom() - frame.height() + 1,
                )
                if frame_x != frame.x() or frame_y != frame.y():
                    self.move(frame_x, frame_y)

    def _set_post_assembly_compact_chrome(self, compact: bool) -> None:
        """Temporarily collapse auxiliary toolbars while preserving user state."""

        widgets = (
            self._quick_access_toolbar,
            self._cad_toolbar,
            self.workspace_bar,
        )
        if compact:
            if self._post_assembly_compact_chrome_visibility is None:
                self._post_assembly_compact_chrome_visibility = tuple(
                    widget.isVisible() for widget in widgets
                )
            for widget in widgets:
                widget.hide()
            return
        saved = self._post_assembly_compact_chrome_visibility
        if saved is None:
            return
        self._post_assembly_compact_chrome_visibility = None
        for widget, visible in zip(widgets, saved, strict=True):
            widget.setVisible(visible)

    def _apply_post_assembly_compact_layout(self) -> None:
        """Keep unified table/footer usable within the scaled client budget."""
        if not getattr(self, "_post_assembly_review_host", False):
            return
        dock = getattr(self, "operation_manager_dock", None)
        post_dock = getattr(self, "post_assembly_dock", None)
        output_dock = getattr(self, "output_dock", None)
        scale_factor = max(0.5, self._ui_scale_manager.scale_factor)
        logical_width = self.width() / scale_factor
        logical_height = self.height() / scale_factor
        post_visible = post_dock is not None and post_dock.isVisible()
        compact_chrome = post_visible and logical_height < 620
        self._set_post_assembly_compact_chrome(compact_chrome)
        if hasattr(self, "viewport"):
            self.viewport.setMinimumHeight(
                0
                if compact_chrome
                else self._viewport_baseline_minimum.height()
            )
        if dock is None or not post_visible:
            return
        if logical_width < 1200 and dock.isVisible():
            dock.hide()
            self._responsive_collapsed_operation_manager = True
        elif logical_width >= 1280 and self._responsive_collapsed_operation_manager:
            dock.show()
            dock.raise_()
            self._responsive_collapsed_operation_manager = False
        if (
            logical_height < 620
            and output_dock is not None
            and output_dock.isVisible()
        ):
            output_dock.hide()
            self._responsive_collapsed_output_dock = True
        elif (
            logical_height >= 680
            and self._responsive_collapsed_output_dock
            and output_dock is not None
        ):
            output_dock.show()
            self._responsive_collapsed_output_dock = False

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        """Prevent closing during I/O and protect unsaved project state."""
        if (
            self._prepare_cam_popup_for_project_change()
            and self.project_controller.request_application_close()
        ):
            self._layout_store.save(
                self,
                active_workspace=self.workspace_bar.active_workspace.value,
                operation_manager=self.operation_manager_dock,
                function_editor=self.function_editor_dock,
                diagnostics=self.output_dock,
            )
            if self._user_profile_service is not None:
                try:
                    index = self._user_profile_service.load_index()
                    active = self._user_profile_service.load(index.active_profile_id)
                    self._user_profile_service.save(
                        self._capture_active_profile(active)
                    )
                except (OSError, RuntimeError, ValueError, TypeError, ProfileError):
                    # Project close remains authoritative; profile persistence
                    # failure is reported without changing project semantics.
                    self.statusBar().showMessage(
                        ui_text("Profile switch failed; the previous profile was restored."),
                        8000,
                    )
            if self._cam3d_review_host:
                self._teardown_cam3d_workflow(wait=True)
            simulation_window = self._machining_simulation_window
            if simulation_window is not None and isValid(simulation_window):
                simulation_window.close()
            if self._lathe_review_host:
                self._lathe_session_controller.teardown()
            if self._ai_assist_controller is not None:
                self._ai_assist_controller.shutdown()
            stage13b_owner = getattr(self, "_stage13b_coordinator", None)
            if stage13b_owner is not None:
                try:
                    stage13b_owner.shutdown("APPLICATION_SHUTDOWN")
                except (RuntimeError, TypeError, ValueError):
                    # Stage 13B must never block the established application close.
                    logger.warning("Stage 13B owner shutdown failed", exc_info=True)
            self._cad_export_status.reset_for_shutdown()
            self._cad_loading_status.reset_for_shutdown()
            self.cad_controller.shutdown()
            self.viewport.shutdown()
            event.accept()
        else:
            event.ignore()


def _format_bounds(bounds) -> str:
    values = (
        bounds.x_min,
        bounds.y_min,
        bounds.z_min,
        bounds.x_max,
        bounds.y_max,
        bounds.z_max,
    )
    return ", ".join(f"{value:.3f}" for value in values)


def _is_supported_open_path(path: Path) -> bool:
    return path.is_file() and path.suffix.casefold() in {
        ".hms",
        ".step",
        ".stp",
        ".brep",
        ".brp",
        ".iges",
        ".igs",
        ".stl",
    }


def _format_translation(values: tuple[float, float, float]) -> str:
    return f"X={values[0]:.3f}, Y={values[1]:.3f}, Z={values[2]:.3f}"


def _format_source_appearance(source) -> str:
    if source is None:
        return "Default"
    color = source.surface_color or source.generic_color or source.curve_color
    if color is None:
        return "Default"
    return ObjectColor(color.red, color.green, color.blue).to_hex()


def _format_units(metadata: CadDocumentMetadata) -> str:
    if metadata.cad_format.value == "stl":
        return "Không xác định (STL không lưu đơn vị đáng tin cậy)"
    if metadata.units.value == "unknown":
        return "Đơn vị mô hình (không xác định)"
    return metadata.units.value.upper()


def _measurement_rows(
    results: tuple[MeasurementResult, ...],
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for result in results:
        for value in result.values:
            if isinstance(value, PointCoordinates):
                rows.extend(
                    (
                        ("X", _format_measurement(value.x)),
                        ("Y", _format_measurement(value.y)),
                        ("Z", _format_measurement(value.z)),
                    )
                )
            elif isinstance(value, DistanceMeasurement):
                rows.append(("Khoảng cách", _format_measurement(value.distance)))
            elif isinstance(value, EdgeLengthMeasurement):
                rows.append(("Chiều dài", _format_measurement(value.length)))
            elif isinstance(value, CircularEdgeMeasurement):
                rows.extend(
                    (
                        ("Bán kính", _format_measurement(value.radius)),
                        ("Đường kính", _format_measurement(value.diameter)),
                        (
                            "Loại đường tròn",
                            "Đường tròn" if value.is_full_circle else "Cung tròn",
                        ),
                    )
                )
            elif isinstance(value, AreaMeasurement):
                rows.append(("Diện tích", _format_measurement(value.area, "²")))
            elif isinstance(value, VolumeMeasurement):
                rows.append(("Thể tích", _format_measurement(value.volume, "³")))
            elif isinstance(value, BoundingDimensions):
                rows.extend(
                    (
                        ("Kích thước X", _format_measurement(value.x)),
                        ("Kích thước Y", _format_measurement(value.y)),
                        ("Kích thước Z", _format_measurement(value.z)),
                    )
                )
    return rows


def _format_measurement(value: float, exponent: str = "") -> str:
    return f"{value:.9g} đơn vị mô hình{exponent}"


def _object_path(
    node: CadObjectNode,
    object_id: CadObjectId,
) -> tuple[CadObjectNode, ...]:
    if node.object_id == object_id:
        return (node,)
    for child in node.children:
        child_path = _object_path(child, object_id)
        if child_path:
            return (node,) + child_path
    return ()
