"""Main application window and Stage 1 workspace composition."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QColor
from PySide6.QtWidgets import (
    QDockWidget,
    QColorDialog,
    QHeaderView,
    QLabel,
    QInputDialog,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
)

from hms_cadcam.cad.kernel import CadKernel
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
from hms_cadcam.ui.cad_controller import CadUiController
from hms_cadcam.ui.project_controller import ProjectUiController
from hms_cadcam.ui.ribbon import RibbonWidget
from hms_cadcam.ui.theme import APP_STYLE
from hms_cadcam.viewer.backend import CadViewportBackend
from hms_cadcam.viewer.models import ObjectAppearance, ObjectColor, SelectionMetadata
from hms_cadcam.viewer.widget import CadViewportWidget

_OBJECT_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_DOCUMENT_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_OBJECT_NODE_ROLE = int(Qt.ItemDataRole.UserRole) + 3
_PLACEHOLDER_ROLE = int(Qt.ItemDataRole.UserRole) + 4
_TOPOLOGY_GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 5


class MainWindow(QMainWindow):
    """Compose the HMS CAD/CAM desktop workspace."""

    def __init__(
        self,
        project_service: ProjectService,
        cad_kernel: CadKernel,
        viewport_backend: CadViewportBackend,
    ) -> None:
        super().__init__()
        self.setObjectName("HmsMainWindow")
        self.setWindowTitle("HMS CAD/CAM — Design")
        self.resize(1500, 900)
        self.setMinimumSize(1024, 680)
        self.setDockNestingEnabled(True)
        self.setStyleSheet(APP_STYLE)

        self.viewport = CadViewportWidget(cad_kernel, viewport_backend, self)
        self.project_controller = ProjectUiController(self, project_service)
        self.cad_controller = CadUiController(self, cad_kernel, self.viewport)
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
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.properties_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.output_dock)
        self.resizeDocks(
            [self.project_dock, self.properties_dock],
            [310, 280],
            Qt.Orientation.Horizontal,
        )
        self.resizeDocks([self.output_dock], [145], Qt.Orientation.Vertical)
        self._build_status_bar()
        self.project_controller.project_changed.connect(self._handle_project_change)
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
        self.cad_controller.measurement_context_changed.connect(
            self._update_measurements
        )
        self._handle_project_change(self.project_controller.service.current_project)
        viewport_status = self.viewport.viewport_status
        if not viewport_status.available:
            reason = viewport_status.error or "Không xác định"
            self._append_output(f"CAD Viewer không khả dụng: {reason}")

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("Tệp")
        file_menu.addAction(self.project_controller.actions["new"])
        file_menu.addAction(self.project_controller.actions["import"])
        file_menu.addAction(self.cad_controller.actions["open_step"])
        file_menu.addAction(self.cad_controller.actions["open_brep"])
        file_menu.addAction(self.cad_controller.actions["open_iges"])
        file_menu.addAction(self.cad_controller.actions["open_stl"])
        file_menu.addAction(self.project_controller.actions["open"])
        self._recent_menu = file_menu.addMenu("Dự án gần đây")
        self._recent_menu.aboutToShow.connect(
            lambda: self.project_controller.populate_recent_menu(self._recent_menu)
        )
        file_menu.addSeparator()
        file_menu.addAction(self.project_controller.actions["save"])
        file_menu.addAction(self.project_controller.actions["save_as"])
        file_menu.addAction(self.project_controller.actions["close"])
        file_menu.addSeparator()
        for title in ("Chỉnh sửa", "Hiển thị"):
            menu_bar.addMenu(title)
        cad_viewer_menu = menu_bar.addMenu("CAD")
        for title in ("CAM", "Máy", "Toolpath", "Setup"):
            menu_bar.addMenu(title)
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
        for mode in ("solid", "face", "edge", "vertex"):
            selection_menu.addAction(self.cad_controller.actions[f"selection_{mode}"])

    def _build_quick_access_toolbar(self) -> None:
        toolbar = QToolBar("Truy cập nhanh", self)
        toolbar.setObjectName("QuickAccess")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        for key in ("new", "open", "save"):
            toolbar.addAction(self.project_controller.actions[key])
        for standard_icon, tooltip in (
            (QStyle.StandardPixmap.SP_ArrowBack, "Undo"),
            (QStyle.StandardPixmap.SP_ArrowForward, "Redo"),
        ):
            action = toolbar.addAction(self.style().standardIcon(standard_icon), "")
            action.setToolTip(f"{tooltip} — chưa khả dụng")
            action.setEnabled(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

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
            "selection_edge",
            "selection_vertex",
            "measurement",
        ):
            toolbar.addAction(self.cad_controller.actions[key])
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    def _create_project_dock(self) -> QDockWidget:
        dock = QDockWidget("Topology / Quản lý dự án", self)
        dock.setObjectName("ProjectManagerDock")
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

        for title in ("Levels", "Toolpaths", "Planes"):
            empty_tree = QTreeWidget()
            empty_tree.setHeaderHidden(True)
            placeholder = QTreeWidgetItem([f"{title} chưa khả dụng"])
            placeholder.setDisabled(True)
            empty_tree.addTopLevelItem(placeholder)
            tabs.addTab(empty_tree, title)
        dock.setWidget(tabs)
        return dock

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
            "HMS CAD/CAM đã sẵn sàng.\n"
            "CAD Viewer sản phẩm đã sẵn sàng; chưa tích hợp CAM."
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
        for text in ("ĐỐI TƯỢNG: 0", "X: 0.000", "Y: 0.000", "Z: 0.000", "3D", "WCS: Top", "METRIC"):
            label = QLabel(text)
            label.setObjectName("StatusLabel")
            status.addPermanentWidget(label)

    def _update_project_display(self, session: object) -> None:
        self._tree_sync_guard = True
        self._project_tree.clear()
        self._object_items = {}
        if not isinstance(session, ProjectSession):
            root = QTreeWidgetItem(["Chưa mở dự án", "—"])
            self._append_cad_document_node(root)
            root.setDisabled(self._active_document_metadata is None)
            self._project_tree.addTopLevelItem(root)
            self.setWindowTitle("HMS CAD/CAM — Design")
            self._project_status.setText("KHÔNG CÓ DỰ ÁN")
            self._tree_sync_guard = False
            return
        dirty_marker = " *" if session.is_dirty else ""
        root = QTreeWidgetItem([session.manifest.project_name, "Đã sửa" if session.is_dirty else "Đã lưu"])
        root.addChild(QTreeWidgetItem(["Đơn vị", session.manifest.units.value]))
        sources = QTreeWidgetItem(["File nguồn", str(len(session.manifest.source_files))])
        for record in session.manifest.source_files:
            sources.addChild(QTreeWidgetItem([record.original_name, record.sha256[:12]]))
        root.addChild(sources)
        self._append_cad_document_node(root)
        self._project_tree.addTopLevelItem(root)
        root.setExpanded(True)
        sources.setExpanded(True)
        self.setWindowTitle(f"HMS CAD/CAM — {session.manifest.project_name}{dirty_marker}")
        self._project_status.setText(str(session.root_path))
        self._tree_sync_guard = False

    def _handle_project_change(self, session: object) -> None:
        source_path = None
        if isinstance(session, ProjectSession):
            source_path = self._find_project_cad_source(session)
        self.cad_controller.bind_project(source_path)
        self._update_project_display(session)

    def _update_cad_document(self, metadata: object) -> None:
        self._active_document_metadata = (
            metadata if isinstance(metadata, CadDocumentMetadata) else None
        )
        self._active_selection = ()
        self._selected_object_ids = ()
        self._active_measurements = ()
        self._update_project_display(self.project_controller.service.current_project)
        self._show_document_properties()

    def _update_topology_tree(self, tree: object) -> None:
        self._active_document_tree = (
            tree if isinstance(tree, CadDocumentTree) else None
        )
        self._update_project_display(self.project_controller.service.current_project)

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
        rows = [
            ("Loại topology", item.topology.value.upper()),
            ("Selection ID", item.selection_id),
            ("Document ID", str(item.document_id)),
            ("Bounding box", _format_bounds(item.bounding_box)),
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
        self._set_properties(
            (
                ("Topology object", node.label),
                ("Object ID", str(node.object_id)),
                ("Loại", node.kind.value.upper()),
                ("Hiển thị", "Có" if appearance.visible else "Không"),
                ("Màu", appearance.color.to_hex()),
                ("Transparency", f"{appearance.transparency:.2f}"),
                ("Bounding box", _format_bounds(node.bounding_box)),
                ("Assembly semantics", "Không có — topology tree, chưa có XCAF"),
            )
        )

    def _show_document_properties(self) -> None:
        metadata = self._active_document_metadata
        if metadata is None:
            self._set_properties((("CAD document", "Chưa có"),))
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
                    "Solid / Face / Edge",
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
        rows.append(("Bounding box", _format_bounds(metadata.bounding_box)))
        rows.extend(_measurement_rows(self._active_measurements))
        self._set_properties(tuple(rows))

    def _set_properties(self, rows: tuple[tuple[str, str], ...]) -> None:
        self._properties_table.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            self._properties_table.setItem(row, 0, QTableWidgetItem(name))
            self._properties_table.setItem(row, 1, QTableWidgetItem(value))

    def _append_cad_document_node(self, root: QTreeWidgetItem) -> None:
        metadata = self._active_document_metadata
        if metadata is None:
            return
        document = QTreeWidgetItem(["CAD document", metadata.cad_format.value.upper()])
        tree = self._active_document_tree
        if tree is not None and tree.document_id == metadata.document_id:
            self._configure_object_item(document, tree.root)
        document.addChild(QTreeWidgetItem(["Document ID", str(metadata.document_id)]))
        document.addChild(QTreeWidgetItem(["Geometry", metadata.geometry_kind.value]))
        document.addChild(
            QTreeWidgetItem(
                [
                    "Giới hạn",
                    "Topology only; chưa có XCAF name/instance/product/transform",
                ]
            )
        )
        if metadata.topology_counts is not None:
            counts = metadata.topology_counts
            document.addChild(
                QTreeWidgetItem(
                    [
                        "Topology",
                        f"S={counts.solids}, F={counts.faces}, E={counts.edges}",
                    ]
                )
            )
        if metadata.mesh_statistics is not None:
            statistics = metadata.mesh_statistics
            document.addChild(
                QTreeWidgetItem(
                    [
                        "Mesh",
                        f"V={statistics.vertices}, T={statistics.triangles}",
                    ]
                )
            )
        document.addChild(
            QTreeWidgetItem(["Bounding box", _format_bounds(metadata.bounding_box)])
        )
        if tree is not None and tree.document_id == metadata.document_id:
            topology = QTreeWidgetItem(["Topology objects", "Lazy"])
            topology.setData(0, _TOPOLOGY_GROUP_ROLE, True)
            for node in tree.root.children:
                topology.addChild(self._make_object_item(node))
            document.addChild(topology)
            topology.setExpanded(True)
            document.setExpanded(True)
        root.addChild(document)

    def _make_object_item(self, node: CadObjectNode) -> QTreeWidgetItem:
        appearance = self._object_appearances.get(node.object_id, ObjectAppearance())
        item = QTreeWidgetItem(
            [node.label, "Hiện" if appearance.visible else "Ẩn"]
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
            "Show",
            lambda: self.cad_controller.set_object_visibility(
                document_id,
                object_id,
                True,
            ),
        )
        menu.addAction(
            "Hide",
            lambda: self.cad_controller.set_object_visibility(
                document_id,
                object_id,
                False,
            ),
        )
        menu.addSeparator()
        menu.addAction(
            "Isolate",
            lambda: self.cad_controller.isolate_object(document_id, object_id),
        )
        menu.addAction(
            "Reset Isolate",
            lambda: self.cad_controller.reset_isolate(document_id),
        )
        menu.addSeparator()
        menu.addAction(
            "Color…",
            lambda: self._choose_object_color(document_id, object_id),
        )
        menu.addAction(
            "Transparency…",
            lambda: self._choose_object_transparency(document_id, object_id),
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
    def _find_project_cad_source(session: ProjectSession) -> Path | None:
        for record in reversed(session.manifest.source_files):
            candidates: list[Path] = []
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
                    return candidate
        return None

    def _update_import_status(self, status: str) -> None:
        self._import_status.setText(f"CAD: {status}")

    def _append_output(self, text: str) -> None:
        self._output.appendPlainText(text)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        """Prevent closing during I/O and protect unsaved project state."""
        if self.project_controller.request_application_close():
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
                        ("Bounding X", _format_measurement(value.x)),
                        ("Bounding Y", _format_measurement(value.y)),
                        ("Bounding Z", _format_measurement(value.z)),
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
