"""Main application window and Stage 1 workspace composition."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QAbstractItemModel, QTimer, Qt
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QDockWidget,
    QColorDialog,
    QHeaderView,
    QLabel,
    QInputDialog,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabBar,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from hms_cadcam.cad.kernel import CadKernel
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
from hms_cadcam.cam.domain import (
    DrillDepthDefinition, DrillGeometryInput, GeometryReference,
    GeometryResolutionStatus, HolePattern, HoleReference,
    LengthUnit, PocketGeometryInput, ResolvedContourProfile,
    ResolvedDrillingGeometry, ResolvedMachiningGeometry, ResolvedPocketGeometry,
    Vector3,
    Revision,
)
from hms_cadcam.cam.cam3d import CamSurfaceReference, CamSurfaceRole
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
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.ui.cam_geometry_adapter import (
    GeometryPickError,
)
from hms_cadcam.ui.project_controller import ProjectUiController
from hms_cadcam.ui.geometry_transfer_ui import (
    IncomingGeometryNotificationBar,
    IncomingGeometryPanel,
)
from hms_cadcam.ui.cam_function_popup import CAMFunctionPopupHost
from hms_cadcam.ui.ribbon import RibbonWidget
from hms_cadcam.ui.theme import APP_STYLE
from hms_cadcam.ui.ui_tokens import (
    DIAGNOSTICS_DEFAULT_HEIGHT,
    DIAGNOSTICS_MAX_HEIGHT,
    FUNCTION_EDITOR_MAX_WIDTH,
    FUNCTION_EDITOR_MIN_WIDTH,
    MAIN_MENU_CONTENT_LEFT_PADDING,
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
from hms_cadcam.viewer.backend import CadViewportBackend
from hms_cadcam.viewer.models import ObjectAppearance, ObjectColor, SelectionMetadata, SelectionMode
from hms_cadcam.viewer.widget import CadViewportWidget

_OBJECT_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_DOCUMENT_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_OBJECT_NODE_ROLE = int(Qt.ItemDataRole.UserRole) + 3
_PLACEHOLDER_ROLE = int(Qt.ItemDataRole.UserRole) + 4
_TOPOLOGY_GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 5


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
    ) -> None:
        super().__init__()
        self.setObjectName("HmsMainWindow")
        self.setWindowTitle("HMS CAD/CAM — Thiết kế")
        self.resize(1500, 900)
        self.setMinimumSize(1024, 680)
        self.setDockNestingEnabled(True)
        self.setStyleSheet(APP_STYLE + WORKSPACE_STYLE)
        self._cad_kernel = cad_kernel
        self._project_service = project_service
        self._layout_store = layout_store or WorkspaceLayoutStore.for_config_directory(
            project_service.config_dir
        )
        self._translation_service = translation_service()
        self._locale_settings = LocaleSettingsService(self._layout_store.settings)
        self._translation_service.set_language(self._locale_settings.load())
        apply_application_font(self._translation_service.language)
        self._translation_service.language_changed.connect(
            self._language_changed
        )
        self._language_dialog: LanguageSettingsDialog | None = None
        self._responsive_collapsed_operation_manager = False
        self._managed_output_lines: dict[
            str,
            tuple[str, dict[str, object], frozenset[str]],
        ] = {}

        self.viewport = CadViewportWidget(cad_kernel, viewport_backend, self)
        self.viewport.set_status_text_resolver(ui_text)
        self.setAcceptDrops(True)
        self._drop_overlay = DropOpenOverlay(self.viewport)
        self.project_controller = ProjectUiController(self, project_service)
        self.cad_controller = CadUiController(
            self,
            cad_kernel,
            self.viewport,
            project_service=project_service,
        )
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
        )
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        ribbon_toolbar = QToolBar("Ribbon", self)
        ribbon_toolbar.setObjectName("RibbonContainer")
        ribbon_toolbar.setMovable(False)
        ribbon_toolbar.setFloatable(False)
        ribbon_toolbar.addWidget(self._ribbon)
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
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)
        self.addDockWidget(
            Qt.DockWidgetArea.LeftDockWidgetArea, self.operation_manager_dock
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.properties_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.secondary_dock)
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
        self.function_editor_dock.hide()
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
        self._build_status_bar()
        self.project_controller.project_changed.connect(self._handle_project_change)
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
        self.cad_controller.progress_changed.connect(self._update_import_status)
        self.cad_controller.document_changed.connect(self._update_cad_document)
        self.cad_controller.topology_tree_changed.connect(self._update_topology_tree)
        self.viewport.selection_context_changed.connect(
            self.cad_controller.handle_selection_event
        )
        self.cad_controller.selection_context_changed.connect(self._update_selection)
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
        localize_widget_tree(self)
        self.refresh_localized_layout()
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
            f"QMenuBar {{ padding-left: {MAIN_MENU_CONTENT_LEFT_PADDING}px; }}"
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
        for key in ("new", "open", "save"):
            toolbar.addAction(self.project_controller.actions[key])
        for standard_icon, tooltip in (
            (QStyle.StandardPixmap.SP_ArrowBack, "Hoàn tác"),
            (QStyle.StandardPixmap.SP_ArrowForward, "Làm lại"),
        ):
            action = toolbar.addAction(self.style().standardIcon(standard_icon), "")
            action.setToolTip(f"{tooltip} — chưa khả dụng")
            action.setEnabled(False)
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

    def _show_cam_workspace(self) -> None:
        """Switch to MILL 2D without replacing the CAD/OCP viewport."""
        self.workspace_bar.set_active_workspace(WorkspaceId.MILL_2D)
        self.operation_manager_dock.show()
        self.operation_manager_dock.raise_()

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

    def _language_changed(self, language: object) -> None:
        """Retranslate presentation only; project and worker state stay intact."""
        selected = UiLanguage.coerce(language)
        active_tab_indices = self._dock_tab_indices()
        apply_widget_font_tree(self, selected)
        localize_widget_tree(self)
        self.refresh_localized_layout(active_tab_indices)
        for model in self.findChildren(QAbstractItemModel):
            retranslate = getattr(model, "_retranslate", None)
            if callable(retranslate):
                retranslate(selected)
        self.viewport.retranslate_status()
        self._update_notification_center_text(
            len(self.project_controller.incoming_requests)
        )
        self._retranslate_output_log()
        if self._language_dialog is not None:
            self._language_dialog.retranslate_ui(selected)

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
            lambda: self._refresh_compact_dock_titles(
                preserved_active_tabs
            ),
        )

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
            self.secondary_panel_host.select_simulation()
            self.secondary_dock.show()
            self.secondary_dock.raise_()
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
        self._project_status = QLabel("KHÔNG CÓ DỰ ÁN")
        self._project_status.setObjectName("StatusLabel")
        status.addPermanentWidget(self._project_status, 1)
        self._import_status = QLabel("CAD: Sẵn sàng")
        self._import_status.setObjectName("StatusLabel")
        status.addPermanentWidget(self._import_status, 1)
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
            self.setWindowTitle("HMS CAD/CAM — Thiết kế")
            self._project_status.setText("KHÔNG CÓ DỰ ÁN")
            self._tree_sync_guard = False
            return
        dirty_marker = " *" if session.is_dirty else ""
        root = QTreeWidgetItem([session.manifest.project_name, "Đã sửa" if session.is_dirty else "Đã lưu"])
        root.addChild(QTreeWidgetItem(["Đơn vị", session.manifest.units.value]))
        sources = QTreeWidgetItem(["Tệp nguồn", str(len(session.manifest.source_files))])
        for record in session.manifest.source_files:
            source_item = QTreeWidgetItem(
                [record.original_name, record.sha256[:12]]
            )
            if record.internal_filename:
                source_item.addChild(
                    QTreeWidgetItem(
                        ["Bản sao nội bộ", record.internal_filename]
                    )
                )
            if record.working_geometry_path:
                source_item.addChild(
                    QTreeWidgetItem(
                        ["Hình học làm việc", record.working_geometry_path]
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
            f"HMS CAD/CAM — Dự án CAM — "
            f"{session.manifest.project_name}{dirty_marker}"
        )
        self._project_status.setText(f"DỰ ÁN CAM: {session.root_path}")
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

    def _update_import_status(self, status: str) -> None:
        self._import_status.setText(f"CAD: {ui_text(status)}")
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
        if paths and all(_is_supported_open_path(path) for path in paths):
            if self.project_controller.request_open_paths(paths):
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
        self._refresh_compact_dock_titles()

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
