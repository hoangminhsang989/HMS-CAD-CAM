"""Stage 9A.3 Operation Manager view, interaction, and user-only state."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from PySide6.QtCore import (
    QElapsedTimer,
    QModelIndex,
    QPoint,
    QSettings,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.project.exceptions import ProjectError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.operation_manager_actions import OperationManagerActions
from hms_cadcam.ui.operation_manager_delegate import OperationManagerDelegate
from hms_cadcam.ui.operation_manager_model import OperationManagerModel
from hms_cadcam.ui.operation_manager_types import (
    OperationManagerFilter,
    OperationManagerLegacySelection,
    OperationManagerNode,
    OperationManagerNodeId,
    OperationManagerNodeKind,
)
from hms_cadcam.ui.operation_manager_projection import OperationManagerProjectionBuilder
from hms_cadcam.ui.localization import localize_widget_tree


logger = logging.getLogger(__name__)
OPERATION_MANAGER_STATE_VERSION = 1
_SETTINGS_GROUP = "operation_manager_9a3"


@dataclass(frozen=True, slots=True)
class OperationManagerUserState:
    expanded: tuple[OperationManagerNodeId, ...]
    selected: OperationManagerNodeId | None
    has_expansion_state: bool


class OperationManagerStateStore:
    """Persist expansion/selection in user settings, never inside .HMS data."""

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings

    def load(self, project_id: UUID) -> OperationManagerUserState:
        group = self._group(project_id)
        self._settings.beginGroup(group)
        try:
            version = int(self._settings.value("version", 0))
            if version not in {0, OPERATION_MANAGER_STATE_VERSION}:
                self._settings.remove("")
                return OperationManagerUserState((), None, False)
            has_expansion = self._settings.contains("expanded")
            raw_expanded = self._settings.value("expanded", [])
            if isinstance(raw_expanded, str):
                raw_expanded = [raw_expanded]
            expanded = tuple(
                OperationManagerNodeId(str(item))
                for item in raw_expanded
                if str(item)
            )
            selected_value = str(self._settings.value("selected", ""))
            selected = (
                OperationManagerNodeId(selected_value) if selected_value else None
            )
            return OperationManagerUserState(expanded, selected, has_expansion)
        finally:
            self._settings.endGroup()

    def save(
        self,
        project_id: UUID,
        expanded: tuple[OperationManagerNodeId, ...],
        selected: OperationManagerNodeId | None,
    ) -> None:
        self._settings.beginGroup(self._group(project_id))
        try:
            self._settings.setValue("version", OPERATION_MANAGER_STATE_VERSION)
            self._settings.setValue("expanded", [str(item) for item in expanded])
            self._settings.setValue("selected", str(selected) if selected else "")
        finally:
            self._settings.endGroup()
        self._settings.sync()

    @staticmethod
    def _group(project_id: UUID) -> str:
        return f"{_SETTINGS_GROUP}/{project_id}"


class OperationManagerView(QTreeView):
    """Keyboard-accessible single-selection view."""

    default_requested = Signal()
    delete_requested = Signal()
    context_requested = Signal()
    viewport_width_changed = Signal(int)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.viewport_width_changed.emit(self.viewport().width())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.default_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.delete_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Menu or (
            event.key() == Qt.Key.Key_F10
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.context_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class OperationManagerPanel(QWidget):
    """Production Operation Manager composed over the classic CAM coordinator."""

    collapse_requested = Signal()
    simulation_requested = Signal()
    post_requested = Signal()
    editor_requested = Signal()

    def __init__(
        self,
        workspace,
        service: ProjectService,
        settings: QSettings,
        source_actions: Mapping[str, QAction],
        project_actions: Mapping[str, QAction] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("OperationManagerHost")
        self.setAccessibleName("Quản lý nguyên công")
        self._workspace = workspace
        self._service = service
        self._source_actions = source_actions
        self._project_actions = project_actions or {}
        self._builder = OperationManagerProjectionBuilder()
        self._state_store = OperationManagerStateStore(settings)
        self._state_guard = False
        self._selection_guard = False
        self._refresh_scheduled = False
        self._downstream_states: dict[str, object] = {}
        self.last_refresh_ms = 0

        # Backward-compatibility bridge.  The coordinator tree remains alive and
        # testable, but the QAbstractItemModel view below is the production UI.
        self.tree = workspace.tree
        self.tree.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(self._project_summary())
        root.addWidget(self._search_row())
        root.addWidget(self._toolbar())

        self.model = OperationManagerModel(self)
        self.view = OperationManagerView(self)
        self.view.setObjectName("OperationManagerTree")
        self.view.setAccessibleName("Cây dự án, công việc, thiết lập và nguyên công CAM")
        self.view.setAccessibleDescription(
            "Cây chọn một mục, dùng ID miền ổn định; phím nhập mở mục, "
            "phím xóa yêu cầu xác nhận."
        )
        self.view.setModel(self.model)
        self.view.setItemDelegate(OperationManagerDelegate(self.view))
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setDragEnabled(False)
        self.view.setAcceptDrops(False)
        self.view.setDropIndicatorShown(False)
        self.view.setAlternatingRowColors(False)
        self.view.setRootIsDecorated(True)
        self.view.setItemsExpandable(True)
        self.view.setIndentation(10)
        self.view.setAllColumnsShowFocus(True)
        self.view.setUniformRowHeights(False)
        self.view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.view.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(1, 70)
        root.addWidget(self.view, 1)
        self.state_frame = self._empty_state_frame()
        root.addWidget(self.state_frame)

        self.commands = OperationManagerActions(
            workspace, source_actions, self.current_node, self
        )
        self._install_toolbar_commands()
        self.commands.simulation_requested.connect(self.simulation_requested)
        self.commands.post_requested.connect(self.post_requested)
        self.commands.editor_requested.connect(self.editor_requested)
        self.view.selectionModel().currentChanged.connect(self._current_changed)
        self.view.expanded.connect(lambda _index: self._save_state())
        self.view.collapsed.connect(lambda _index: self._save_state())
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        self.view.doubleClicked.connect(lambda _index: self.commands.trigger_default())
        self.view.default_requested.connect(self.commands.trigger_default)
        self.view.delete_requested.connect(self._delete_from_keyboard)
        self.view.context_requested.connect(self._show_keyboard_context_menu)
        self.view.viewport_width_changed.connect(self._apply_tree_width_policy)
        self.search.textChanged.connect(self._filter_changed)
        self.filter.currentIndexChanged.connect(self._filter_changed)
        workspace.projection_changed.connect(self.refresh)
        workspace.selection_identity_changed.connect(self._sync_legacy_selection)
        workspace.post_panel.state_changed.connect(
            lambda state: self._downstream_state_changed("post", state)
        )
        workspace.program_assembly_panel.state_changed.connect(
            lambda state: self._downstream_state_changed("program", state)
        )
        localize_widget_tree(self)
        self.refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        """Preserve the operation name before secondary text at narrow widths."""
        super().resizeEvent(event)
        self._apply_tree_width_policy(self.view.viewport().width())

    def _apply_tree_width_policy(self, available_width: int) -> None:
        """Allocate logical width to the primary operation name first."""
        narrow = available_width < 290
        self.view.setIndentation(6 if narrow else 10)
        status_width = max(
            60,
            min(
                70,
                self.view.fontMetrics().horizontalAdvance("CẦN TÍNH") + 18,
            ),
        )
        self.view.header().resizeSection(
            1, min(status_width, 64) if narrow else status_width
        )

    def current_node(self) -> OperationManagerNode | None:
        return self.model.node_for_index(self.view.currentIndex())

    def refresh(self) -> None:
        """Rebuild from one immutable snapshot and preserve identity-based state."""
        timer = QElapsedTimer()
        timer.start()
        old_projection = self.model.projection
        old_node = self.current_node()
        old_node_id = old_node.node_id if old_node is not None else None
        old_project_id = old_projection.project_id
        if old_project_id is not None:
            self._save_state()
        session = self._service.current_project
        try:
            projection = self._builder.build(self._service, session)
        except (ProjectError, RuntimeError, TypeError, ValueError):
            logger.exception("Không thể dựng Operation Manager projection")
            projection = self._builder.build(self._service, None)
        self._state_guard = True
        try:
            self.model.set_projection(projection)
            self._update_header(projection.header)
            state = (
                self._state_store.load(projection.project_id)
                if projection.project_id is not None
                else OperationManagerUserState((), None, False)
            )
            expanded = (
                state.expanded
                if state.has_expansion_state
                else tuple(
                    item.node_id for item in projection.nodes if item.default_expanded
                )
            )
            self._restore_expansion(expanded)
            candidate = self._node_id_for_legacy(self._workspace.selected_identity)
            if (
                candidate is None
                and projection.project_id == old_project_id
                and old_node_id is not None
            ):
                candidate = projection.nearest_existing(old_projection, old_node_id)
            elif state.selected is not None:
                candidate = candidate or state.selected
            self._select_node_id(candidate)
        finally:
            self._state_guard = False
        self._update_empty_state()
        self.commands.update_state()
        self.last_refresh_ms = timer.elapsed()

    def _downstream_state_changed(self, source: str, state: object) -> None:
        """Coalesce real Post/NC lifecycle changes into one projection refresh."""
        previous = self._downstream_states.get(source)
        if previous == state:
            return
        self._downstream_states[source] = state
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True
        QTimer.singleShot(0, self._run_scheduled_refresh)

    def _run_scheduled_refresh(self) -> None:
        self._refresh_scheduled = False
        self.refresh()

    def select_legacy_identity(self, kind: str, value: str) -> bool:
        """Select the primary projected node matching an external CAM selection."""
        candidates = (
            item
            for item in self.model.projection.nodes
            if item.legacy_selection
            == OperationManagerLegacySelection(kind, value)
        )
        primary = next(
            (item for item in candidates if item.kind is OperationManagerNodeKind.OPERATION),
            None,
        )
        if primary is None:
            primary = next(
                (
                    item
                    for item in self.model.projection.nodes
                    if item.legacy_selection
                    == OperationManagerLegacySelection(kind, value)
                ),
                None,
            )
        return self._select_node_id(primary.node_id if primary is not None else None)

    def _node_id_for_legacy(
        self, selection: tuple[str, str] | None
    ) -> OperationManagerNodeId | None:
        if selection is None:
            return None
        kind, value = selection
        expected = OperationManagerLegacySelection(kind, value)
        matches = tuple(
            item
            for item in self.model.projection.nodes
            if item.legacy_selection == expected
        )
        primary = next(
            (item for item in matches if item.kind is OperationManagerNodeKind.OPERATION),
            matches[0] if matches else None,
        )
        return primary.node_id if primary is not None else None

    def _header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("PanelHeader")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 5, 5, 5)
        title = QLabel("Quản lý nguyên công")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)
        layout.addStretch(1)
        self.collapse_button = QToolButton()
        self.collapse_button.setText("×")
        self.collapse_button.setAccessibleName("Thu gọn Quản lý nguyên công")
        self.collapse_button.setToolTip("Thu gọn Quản lý nguyên công")
        self.collapse_button.setAutoRaise(True)
        self.collapse_button.clicked.connect(self.collapse_requested)
        layout.addWidget(self.collapse_button)
        return frame

    def _project_summary(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("OperationManagerSummary")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(1)
        self.project_label = QLabel("Chưa mở dự án")
        self.project_label.setObjectName("OperationManagerProject")
        self.project_label.setWordWrap(True)
        self.project_label.setAccessibleName("Dự án hiện hành")
        self.context_label = QLabel("Công việc — · Thiết lập — · Máy —")
        self.context_label.setObjectName("PanelSummary")
        self.context_label.setWordWrap(True)
        self.context_label.setAccessibleName("Công việc, thiết lập và máy hiện hành")
        self.counts_label = QLabel("0 nguyên công · 0 cảnh báo · 0 lỗi")
        self.counts_label.setObjectName("OperationManagerCounts")
        self.counts_label.setAccessibleName("Tổng hợp trạng thái nguyên công")
        layout.addWidget(self.project_label)
        layout.addWidget(self.context_label)
        layout.addWidget(self.counts_label)
        return frame

    def _search_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(4)
        self.search = QLineEdit()
        self.search.setObjectName("OperationSearch")
        self.search.setAccessibleName("Tìm trong Quản lý nguyên công")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Tên, chiến lược, dao, trạng thái hoặc ID…")
        self.filter = QComboBox()
        self.filter.setObjectName("OperationStatusFilter")
        self.filter.setAccessibleName("Lọc trạng thái Quản lý nguyên công")
        labels = {
            OperationManagerFilter.ALL: "Tất cả",
            OperationManagerFilter.ENABLED: "Đang bật",
            OperationManagerFilter.DISABLED: "Đã tắt",
            OperationManagerFilter.NEEDS_CALCULATION: "Cần tính",
            OperationManagerFilter.STALE: "Đã lỗi thời",
            OperationManagerFilter.WARNINGS: "Cảnh báo",
            OperationManagerFilter.ERRORS: "Lỗi",
        }
        for value, label in labels.items():
            self.filter.addItem(label, value)
        self.filter.setMaximumWidth(105)
        layout.addWidget(self.search, 1)
        layout.addWidget(self.filter)
        return widget

    def _toolbar(self) -> QToolBar:
        self.toolbar = QToolBar("Lệnh Quản lý nguyên công")
        self.toolbar.setObjectName("OperationManagerTools")
        self.toolbar.setAccessibleName("Lệnh theo vùng chọn của Quản lý nguyên công")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.add_button = QToolButton(self.toolbar)
        self.add_button.setObjectName("OperationAddMenuButton")
        self.add_button.setText("+ Thêm nguyên công")
        self.add_button.setAccessibleName("Thêm nguyên công")
        self.add_button.setToolTip("Chọn chiến lược nguyên công để thêm")
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_button.setMenu(self._add_menu())
        self.toolbar.addWidget(self.add_button)
        return self.toolbar

    def _install_toolbar_commands(self) -> None:
        for action, text, accessible in (
            (self.commands.recalculate, "Tính", "Tính lại toolpath"),
            (self.commands.simulate, "Mô phỏng", "Chạy mô phỏng"),
            (self.commands.post, "Post", "Mở Post hoặc Lắp ráp chương trình"),
            (self.commands.delete, "Xóa", "Xóa vùng chọn có xác nhận"),
        ):
            action.setObjectName(f"OperationManager{accessible.replace(' ', '')}Action")
            button = QToolButton(self.toolbar)
            button.setDefaultAction(action)
            button.setText(text)
            button.setAccessibleName(accessible)
            self.toolbar.addWidget(button)
        self.more_button = QToolButton(self.toolbar)
        self.more_button.setObjectName("OperationMoreMenuButton")
        self.more_button.setText("•••")
        self.more_button.setAccessibleName("Thêm thao tác Quản lý nguyên công")
        self.more_button.setToolTip("Tạo tài nguyên và thao tác ít dùng")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setMenu(self._more_menu())
        self.toolbar.addWidget(self.more_button)

    def _add_menu(self) -> QMenu:
        menu = QMenu("Thêm nguyên công", self)
        menu.addSection("CAM 2D / Phay")
        for key in (
            "operation",
            "contour_operation",
            "pocket_operation",
        ):
            action = self._source_actions.get(key)
            if action is not None:
                menu.addAction(action)
        menu.addSection("CAM 3D")
        action = self._source_actions.get("parallel_operation")
        if action is not None:
            menu.addAction(action)
        menu.addSection("Gia công lỗ")
        for key in (
            "drilling_operation",
            "tapping_operation",
            "reaming_operation",
            "boring_operation",
        ):
            action = self._source_actions.get(key)
            if action is not None:
                menu.addAction(action)
        localize_widget_tree(menu)
        return menu

    def _more_menu(self) -> QMenu:
        menu = QMenu("Thêm thao tác", self)
        for key in (
            "job",
            "setup",
            "resources",
            "parallel_resources",
            "tapping_resources",
            "reaming_resources",
            "boring_resources",
            "group",
        ):
            action = self._source_actions.get(key)
            if action is not None:
                menu.addAction(action)
        menu.addSeparator()
        for action in (
            self.commands.open,
            self.commands.rename,
            self.commands.enable,
            self.commands.disable,
            self.commands.bind_geometry,
            self.commands.clear_geometry,
            self.commands.toggle_toolpath,
            self.commands.move_up,
            self.commands.move_down,
            self.commands.duplicate,
            self.commands.clear_toolpath,
            self.commands.clear_simulation,
            self.commands.clear_post,
            self.commands.clear_nc,
        ):
            menu.addAction(action)
        # All classic actions remain reachable during the presentation migration.
        menu.addSeparator()
        for key in (
            "generate",
            "visibility",
            "pick",
            "clear_pick",
            "up",
            "down",
            "delete",
        ):
            action = self._source_actions.get(key)
            if action is not None:
                menu.addAction(action)
        localize_widget_tree(menu)
        return menu

    def _empty_state_frame(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("OperationManagerEmptyState")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(4)
        self.state_title = QLabel()
        self.state_title.setObjectName("OperationManagerStateTitle")
        self.state_title.setWordWrap(True)
        self.state_message = QLabel()
        self.state_message.setObjectName("PanelSummary")
        self.state_message.setWordWrap(True)
        self.state_actions = QHBoxLayout()
        layout.addWidget(self.state_title)
        layout.addWidget(self.state_message)
        layout.addLayout(self.state_actions)
        return frame

    def _update_header(self, header) -> None:
        self.project_label.setText(header.project_name)
        self.context_label.setText(
            f"Công việc {header.active_job} · Thiết lập {header.active_setup} · {header.machine}"
        )
        self.counts_label.setText(
            f"{header.operation_count} nguyên công · "
            f"{header.warning_count} cảnh báo · {header.error_count} lỗi"
        )

    def _filter_changed(self) -> None:
        self._state_guard = True
        try:
            self.model.set_query(self.search.text())
            value = self.filter.currentData()
            self.model.set_status_filter(
                value if isinstance(value, OperationManagerFilter) else OperationManagerFilter.ALL
            )
            self._expand_visible_context()
        finally:
            self._state_guard = False
        self._update_empty_state()

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._selection_guard:
            return
        node = self.model.node_for_index(current)
        self._selection_guard = True
        try:
            if node is not None and node.legacy_selection is not None:
                self._workspace.select_identity(
                    node.legacy_selection.kind, node.legacy_selection.value
                )
            elif node is not None:
                self._workspace.clear_selection()
        finally:
            self._selection_guard = False
        self.commands.update_state()
        self._save_state()

    def _sync_legacy_selection(self, selection: object) -> None:
        if self._selection_guard or not isinstance(selection, tuple) or len(selection) != 2:
            return
        kind, value = selection
        if not isinstance(kind, str) or not isinstance(value, str):
            return
        self._selection_guard = True
        try:
            self.select_legacy_identity(kind, value)
        finally:
            self._selection_guard = False

    def _select_node_id(self, node_id: OperationManagerNodeId | None) -> bool:
        index = self.model.index_for_node_id(node_id) if node_id is not None else QModelIndex()
        if not index.isValid():
            index = self.model.first_index()
        if not index.isValid():
            self.view.clearSelection()
            self.view.setCurrentIndex(QModelIndex())
            return False
        self._expand_ancestors(index)
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
        return True

    def _restore_expansion(
        self, expanded: tuple[OperationManagerNodeId, ...]
    ) -> None:
        expanded_set = set(expanded)
        for node in self.model.projection.nodes:
            index = self.model.index_for_node_id(node.node_id)
            if index.isValid():
                self.view.setExpanded(index, node.node_id in expanded_set)

    def _expand_visible_context(self) -> None:
        if (
            not self.search.text().strip()
            and self.filter.currentData() == OperationManagerFilter.ALL
        ):
            state = (
                self._state_store.load(self.model.projection.project_id)
                if self.model.projection.project_id is not None
                else OperationManagerUserState((), None, False)
            )
            expanded = state.expanded if state.has_expansion_state else tuple(
                item.node_id
                for item in self.model.projection.nodes
                if item.default_expanded
            )
            self._restore_expansion(expanded)
            return
        for node in self.model.projection.nodes:
            index = self.model.index_for_node_id(node.node_id)
            if index.isValid() and self.model.rowCount(index) > 0:
                self.view.setExpanded(index, True)

    def _expand_ancestors(self, index: QModelIndex) -> None:
        parent = index.parent()
        while parent.isValid():
            self.view.setExpanded(parent, True)
            parent = parent.parent()

    def _expanded_ids(self) -> tuple[OperationManagerNodeId, ...]:
        values = []
        for node in self.model.projection.nodes:
            index = self.model.index_for_node_id(node.node_id)
            if index.isValid() and self.view.isExpanded(index):
                values.append(node.node_id)
        return tuple(values)

    def _save_state(self) -> None:
        project_id = self.model.projection.project_id
        if self._state_guard or project_id is None:
            return
        node = self.current_node()
        self._state_store.save(
            project_id,
            self._expanded_ids(),
            node.node_id if node is not None else None,
        )

    def _show_context_menu(self, point: QPoint) -> None:
        index = self.view.indexAt(point)
        if index.isValid():
            self.view.setCurrentIndex(index)
        menu = self.commands.context_menu(self.view)
        if not menu.isEmpty():
            menu.exec(self.view.viewport().mapToGlobal(point))

    def _show_keyboard_context_menu(self) -> None:
        index = self.view.currentIndex()
        point = self.view.visualRect(index).center() if index.isValid() else QPoint(8, 8)
        menu = self.commands.context_menu(self.view)
        if not menu.isEmpty():
            self._active_context_menu = menu
            menu.popup(self.view.viewport().mapToGlobal(point))

    def _delete_from_keyboard(self) -> None:
        self.commands.update_state()
        if self.commands.delete.isEnabled():
            self.commands.delete.trigger()

    def _update_empty_state(self) -> None:
        self._clear_state_actions()
        projection = self.model.projection
        if projection.project_id is None:
            self.state_title.setText("Chưa mở dự án")
            self.state_message.setText("Mở hoặc tạo dự án .HMS để bắt đầu.")
            self._add_project_action("new", "Tạo dự án")
            self._add_project_action("open", "Mở dự án")
            self.state_frame.show()
            return
        if self.model.rowCount() == 0:
            self.state_title.setText("Không có kết quả phù hợp")
            self.state_message.setText("Đổi từ khóa hoặc bộ lọc trạng thái.")
            button = QPushButton("Xóa bộ lọc")
            button.clicked.connect(self._clear_filters)
            self.state_actions.addWidget(button)
            self.state_frame.show()
            return
        operation_count = projection.header.operation_count
        has_jobs = any(
            item.kind is OperationManagerNodeKind.JOB for item in projection.nodes
        )
        has_setups = any(
            item.kind is OperationManagerNodeKind.SETUP for item in projection.nodes
        )
        if not has_jobs:
            self.state_title.setText("Dự án hiện chỉ có CAD")
            self.state_message.setText("Tạo công việc CAM khi sẵn sàng lập trình gia công.")
            action = self._source_actions.get("job")
            self._add_action_button(action, "Tạo CAM Job")
            self.state_frame.show()
        elif has_setups and operation_count == 0:
            self.state_title.setText("Thiết lập chưa có nguyên công")
            self.state_message.setText(
                "Thêm nguyên công đầu tiên bằng chiến lược hiện có."
            )
            self._add_action_button(
                self.commands.add_operation, "Thêm nguyên công đầu tiên"
            )
            self.state_frame.show()
        else:
            self.state_frame.hide()

    def _clear_state_actions(self) -> None:
        while self.state_actions.count():
            item = self.state_actions.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_project_action(self, key: str, text: str) -> None:
        self._add_action_button(self._project_actions.get(key), text)

    def _add_action_button(self, action: QAction | None, text: str) -> None:
        if action is None:
            return
        button = QPushButton(text)
        button.setEnabled(action.isEnabled())
        button.setToolTip(action.toolTip())
        button.clicked.connect(action.trigger)
        self.state_actions.addWidget(button)

    def _clear_filters(self) -> None:
        self.search.clear()
        self.filter.setCurrentIndex(0)
