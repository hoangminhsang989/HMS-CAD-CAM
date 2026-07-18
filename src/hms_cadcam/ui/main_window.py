"""Main application window and Stage 1 workspace composition."""

from __future__ import annotations

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

from hms_cadcam.project.models import ProjectSession
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.project_controller import ProjectUiController
from hms_cadcam.ui.ribbon import RibbonWidget
from hms_cadcam.ui.theme import APP_STYLE
from hms_cadcam.ui.viewport_placeholder import CadViewportPlaceholder


class MainWindow(QMainWindow):
    """Compose the HMS CAD/CAM desktop workspace."""

    def __init__(self, project_service: ProjectService) -> None:
        super().__init__()
        self.setObjectName("HmsMainWindow")
        self.setWindowTitle("HMS CAD/CAM — Design")
        self.resize(1500, 900)
        self.setMinimumSize(1024, 680)
        self.setDockNestingEnabled(True)
        self.setStyleSheet(APP_STYLE)

        self.project_controller = ProjectUiController(self, project_service)
        self._build_menu_bar()
        self._build_quick_access_toolbar()
        self._ribbon = RibbonWidget(self.project_controller.actions, self)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        ribbon_toolbar = QToolBar("Ribbon", self)
        ribbon_toolbar.setObjectName("RibbonContainer")
        ribbon_toolbar.setMovable(False)
        ribbon_toolbar.setFloatable(False)
        ribbon_toolbar.addWidget(self._ribbon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, ribbon_toolbar)

        self.viewport = CadViewportPlaceholder(self)
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
        self.project_controller.project_changed.connect(self._update_project_display)
        self.project_controller.message.connect(self._append_output)
        self._update_project_display(self.project_controller.service.current_project)

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("Tệp")
        file_menu.addAction(self.project_controller.actions["new"])
        file_menu.addAction(self.project_controller.actions["import"])
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
        for title in ("Chỉnh sửa", "Hiển thị", "CAD", "CAM", "Máy", "Toolpath", "Setup"):
            menu_bar.addMenu(title)
        help_menu = menu_bar.addMenu("Trợ giúp")

        exit_action = QAction("Thoát", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        about_action = QAction("Giới thiệu HMS CAD/CAM", self)
        about_action.setEnabled(False)
        help_menu.addAction(about_action)

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
        table = QTableWidget(4, 2)
        table.setHorizontalHeaderLabels(["Thuộc tính", "Giá trị"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, (name, value) in enumerate(
            (("Lựa chọn", "Không có"), ("Mặt phẳng", "Top"), ("Đơn vị", "Metric"), ("Chế độ", "3D"))
        ):
            table.setItem(row, 0, QTableWidgetItem(name))
            table.setItem(row, 1, QTableWidgetItem(value))
        dock.setWidget(table)
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
            "CAD Viewport hiện là placeholder; chưa tích hợp CAD kernel hoặc CAM."
        )
        dock.setWidget(self._output)
        return dock

    def _build_status_bar(self) -> None:
        status = self.statusBar()
        status.showMessage("Sẵn sàng")
        self._project_status = QLabel("KHÔNG CÓ DỰ ÁN")
        self._project_status.setObjectName("StatusLabel")
        status.addPermanentWidget(self._project_status, 1)
        for text in ("ĐỐI TƯỢNG: 0", "X: 0.000", "Y: 0.000", "Z: 0.000", "3D", "WCS: Top", "METRIC"):
            label = QLabel(text)
            label.setObjectName("StatusLabel")
            status.addPermanentWidget(label)

    def _update_project_display(self, session: object) -> None:
        self._project_tree.clear()
        if not isinstance(session, ProjectSession):
            root = QTreeWidgetItem(["Chưa mở dự án", "—"])
            root.setDisabled(True)
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
        self._project_tree.addTopLevelItem(root)
        root.setExpanded(True)
        sources.setExpanded(True)
        self.setWindowTitle(f"HMS CAD/CAM — {session.manifest.project_name}{dirty_marker}")
        self._project_status.setText(str(session.root_path))

    def _append_output(self, text: str) -> None:
        self._output.appendPlainText(text)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        """Prevent closing during I/O and protect unsaved project state."""
        if self.project_controller.request_application_close():
            event.accept()
        else:
            event.ignore()
