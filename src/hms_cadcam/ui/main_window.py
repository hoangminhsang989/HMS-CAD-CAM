"""Main application window and Stage 1 workspace composition."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QDockWidget,
    QHeaderView,
    QLabel,
    QMainWindow,
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
from hms_cadcam.cad.models import CadDocumentMetadata
from hms_cadcam.project.models import ProjectSession
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cad_controller import CadUiController
from hms_cadcam.ui.project_controller import ProjectUiController
from hms_cadcam.ui.ribbon import RibbonWidget
from hms_cadcam.ui.theme import APP_STYLE
from hms_cadcam.viewer.backend import CadViewportBackend
from hms_cadcam.viewer.models import SelectionMetadata
from hms_cadcam.viewer.widget import CadViewportWidget


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
        self._active_selection: tuple[SelectionMetadata, ...] = ()
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
        self.viewport.selection_changed.connect(self._update_selection)
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
        for mode in ("solid", "face", "edge"):
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
        ):
            toolbar.addAction(self.cad_controller.actions[key])
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    def _create_project_dock(self) -> QDockWidget:
        dock = QDockWidget("Toolpaths / Quản lý dự án", self)
        dock.setObjectName("ProjectManagerDock")
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        tabs = QTabWidget()
        tabs.setObjectName("ManagerTabs")

        self._project_tree = QTreeWidget()
        self._project_tree.setObjectName("ProjectTree")
        self._project_tree.setHeaderLabels(["Đối tượng", "Trạng thái"])
        self._project_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._project_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        root = QTreeWidgetItem(["Chưa mở dự án", "—"])
        root.setDisabled(True)
        self._project_tree.addTopLevelItem(root)
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
        self._project_tree.clear()
        if not isinstance(session, ProjectSession):
            root = QTreeWidgetItem(["Chưa mở dự án", "—"])
            root.setDisabled(True)
            self._append_cad_document_node(root)
            self._project_tree.addTopLevelItem(root)
            self.setWindowTitle("HMS CAD/CAM — Design")
            self._project_status.setText("KHÔNG CÓ DỰ ÁN")
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
        self._update_project_display(self.project_controller.service.current_project)
        self._show_document_properties()

    def _update_selection(self, items: object) -> None:
        if not isinstance(items, tuple) or not all(
            isinstance(item, SelectionMetadata) for item in items
        ):
            return
        self._active_selection = items
        if not items:
            self._show_document_properties()
            return
        item = items[0]
        self._set_properties(
            (
                ("Loại topology", item.topology.value.upper()),
                ("Selection ID", item.selection_id),
                ("Document ID", str(item.document_id)),
                ("Bounding box", _format_bounds(item.bounding_box)),
                ("Số lượng chọn", str(len(items))),
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
        document.addChild(QTreeWidgetItem(["Document ID", str(metadata.document_id)]))
        document.addChild(QTreeWidgetItem(["Geometry", metadata.geometry_kind.value]))
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
        root.addChild(document)

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
    return metadata.units.value.upper()
