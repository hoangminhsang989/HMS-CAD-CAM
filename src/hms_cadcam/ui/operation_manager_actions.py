"""Selection-scoped action routing for the Stage 9A.3 Operation Manager."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from hms_cadcam.ui.operation_manager_types import (
    OperationManagerCapability,
    OperationManagerNode,
    OperationManagerNodeKind,
)


class OperationManagerActions(QObject):
    """Resolve identity at trigger time and reuse existing CAM UI commands."""

    simulation_requested = Signal()
    post_requested = Signal()
    editor_requested = Signal()

    def __init__(
        self,
        workspace,
        source_actions: Mapping[str, QAction],
        current_node: Callable[[], OperationManagerNode | None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._workspace = workspace
        self._source = source_actions
        self._current_node = current_node
        self.add_operation = self._action("Thêm operation", self._add_default)
        self.recalculate = self._action("Tính lại", self._recalculate)
        self.simulate = self._action("Mô phỏng", self._simulate)
        self.post = self._action("Post", self._post)
        self.generate_post = self._action("Generate Post", self._generate_post)
        self.add_to_program = self._action(
            "Thêm vào Program Assembly", self._add_to_program
        )
        self.open_simulation = self._action("Mở Simulation", self._open_simulation)
        self.open_post = self._action("Mở Post Preview", self._post)
        self.export_nc = self._action("Export NC", self._export_nc)
        self.show_export_details = self._action(
            "Hiện chi tiết NC/Export", self._show_export_details
        )
        self.delete = self._action("Xóa", self._delete)
        self.open = self._action("Chỉnh sửa", self._open)
        self.rename = self._action("Đổi tên", self._rename)
        self.enable = self._action("Bật", lambda: self._set_enabled(True))
        self.disable = self._action("Tắt", lambda: self._set_enabled(False))
        self.move_up = self._action("Di chuyển lên", lambda: self._trigger("up"))
        self.move_down = self._action("Di chuyển xuống", lambda: self._trigger("down"))
        self.bind_geometry = self._action(
            "Bind/Rebind geometry", lambda: self._trigger("pick")
        )
        self.clear_geometry = self._action(
            "Clear geometry", lambda: self._trigger("clear_pick")
        )
        self.toggle_toolpath = self._action(
            "Hiện/ẩn toolpath", lambda: self._trigger("visibility")
        )
        self.clear_simulation = self._action(
            "Xóa kết quả Simulation", self._clear_simulation
        )
        self.clear_post = self._action("Xóa Post Result", self._clear_post)
        self.clear_nc = self._action("Xóa NC Artifact", self._clear_nc)
        self.clear_toolpath = self._action("Xóa Toolpath Artifact", lambda: None)
        _disable(
            self.clear_toolpath,
            "Chưa có command xóa Toolpath Artifact an toàn trong application service.",
        )
        self.duplicate = self._action("Nhân bản", self._duplicate)
        self.all_actions = (
            self.add_operation,
            self.recalculate,
            self.simulate,
            self.post,
            self.generate_post,
            self.add_to_program,
            self.open_simulation,
            self.open_post,
            self.export_nc,
            self.show_export_details,
            self.delete,
            self.open,
            self.rename,
            self.enable,
            self.disable,
            self.move_up,
            self.move_down,
            self.bind_geometry,
            self.clear_geometry,
            self.toggle_toolpath,
            self.clear_simulation,
            self.clear_post,
            self.clear_nc,
            self.clear_toolpath,
            self.duplicate,
        )
        self.update_state()

    def update_state(self) -> None:
        """Refresh availability and readable disabled reasons for current identity."""
        node = self._current_node()
        capabilities = set(node.capabilities) if node is not None else set()
        self._set_capability(
            self.add_operation,
            OperationManagerCapability.ADD_OPERATION,
            capabilities,
            "Chọn Setup, Operations hoặc Group để thêm operation.",
        )
        source_generate = self._source.get("generate")
        can_recalculate = (
            OperationManagerCapability.RECALCULATE in capabilities
            and source_generate is not None
            and source_generate.isEnabled()
        )
        _set_enabled(
            self.recalculate,
            can_recalculate,
            "Draft operation phải hợp lệ, đã áp dụng và có đủ geometry/tool/machine.",
        )
        can_simulate = (
            OperationManagerCapability.SIMULATE in capabilities
            and self._workspace.simulation_panel.run_button.isEnabled()
        )
        _set_enabled(
            self.simulate,
            can_simulate,
            "Cần toolpath CURRENT và Simulation preflight hợp lệ.",
        )
        self._set_capability(
            self.post,
            OperationManagerCapability.POST,
            capabilities,
            "Chọn operation, Post Result, NC Artifact hoặc Program Assembly.",
        )
        kind = node.kind if node is not None else None
        can_generate_post = (
            OperationManagerCapability.POST in capabilities
            and kind
            in {
                OperationManagerNodeKind.OPERATION,
                OperationManagerNodeKind.POST_RESULT,
            }
            and self._workspace.post_panel.generate_button.isEnabled()
        )
        _set_enabled(
            self.generate_post,
            can_generate_post,
            "Post source/gate phải hợp lệ và không có tác vụ Post đang chạy.",
        )
        _set_enabled(
            self.add_to_program,
            kind is OperationManagerNodeKind.OPERATION
            and self._workspace.program_assembly_panel.add_button.isEnabled(),
            "Operation phải hợp lệ và chưa có trong Program Assembly hiện tại.",
        )
        _set_enabled(
            self.open_simulation,
            kind is OperationManagerNodeKind.SIMULATION,
            "Chọn node Simulation.",
        )
        _set_enabled(
            self.open_post,
            kind
            in {
                OperationManagerNodeKind.POST_RESULT,
                OperationManagerNodeKind.NC_ARTIFACT,
                OperationManagerNodeKind.PROGRAM_ASSEMBLY,
            },
            "Chọn Post Result, NC Artifact hoặc Program Assembly.",
        )
        _set_enabled(
            self.export_nc,
            kind is OperationManagerNodeKind.NC_ARTIFACT
            and self._workspace.post_panel.export_button.isEnabled(),
            "Cần NC hiện hành và cấu hình Export hợp lệ.",
        )
        _set_enabled(
            self.show_export_details,
            kind is OperationManagerNodeKind.NC_ARTIFACT
            and self._workspace.post_panel.export_details_button.isEnabled(),
            "Chưa có kết quả Export NC để xem.",
        )
        self._set_capability(
            self.delete,
            OperationManagerCapability.DELETE,
            capabilities,
            "Node này không có command xóa trong domain hiện tại.",
        )
        self._set_capability(
            self.duplicate,
            OperationManagerCapability.DUPLICATE,
            capabilities,
            "Chỉ operation có identity domain mới được nhân bản.",
        )
        self._set_capability(
            self.rename,
            OperationManagerCapability.RENAME,
            capabilities,
            "Node trình chiếu không hỗ trợ đổi tên.",
        )
        self._set_capability(
            self.enable,
            OperationManagerCapability.ENABLE,
            capabilities,
            "Node đang bật hoặc không hỗ trợ thay đổi enabled.",
        )
        self._set_capability(
            self.disable,
            OperationManagerCapability.DISABLE,
            capabilities,
            "Node đang tắt hoặc không hỗ trợ thay đổi enabled.",
        )
        self._set_capability(
            self.move_up,
            OperationManagerCapability.MOVE_UP,
            capabilities,
            "Chỉ Group/Operation có thứ tự domain mới được di chuyển.",
        )
        self._set_capability(
            self.move_down,
            OperationManagerCapability.MOVE_DOWN,
            capabilities,
            "Chỉ Group/Operation có thứ tự domain mới được di chuyển.",
        )
        self._set_capability(
            self.bind_geometry,
            OperationManagerCapability.BIND_GEOMETRY,
            capabilities,
            "Chọn operation hoặc Geometry của operation.",
        )
        self._set_capability(
            self.clear_geometry,
            OperationManagerCapability.CLEAR_GEOMETRY,
            capabilities,
            "Chọn operation hoặc Geometry của operation.",
        )
        self._set_capability(
            self.toggle_toolpath,
            OperationManagerCapability.TOGGLE_TOOLPATH,
            capabilities,
            "Chọn operation có presentation toolpath.",
        )
        _set_enabled(
            self.clear_simulation,
            OperationManagerCapability.CLEAR_SIMULATION in capabilities
            and self._workspace.simulation_panel.clear_button.isEnabled(),
            "Chưa có kết quả Simulation để xóa.",
        )
        _set_enabled(
            self.clear_post,
            OperationManagerCapability.CLEAR_POST in capabilities
            and self._workspace.post_panel.clear_post_button.isEnabled(),
            "Chưa có Post Result runtime để xóa.",
        )
        _set_enabled(
            self.clear_nc,
            OperationManagerCapability.CLEAR_NC in capabilities
            and self._workspace.post_panel.clear_managed_button.isEnabled(),
            "Chưa có NC Artifact được quản lý để xóa.",
        )
        self.open.setEnabled(node is not None)

    def context_menu(self, parent: QWidget) -> QMenu:
        """Build a kind-specific menu without retaining a QModelIndex."""
        self.update_state()
        node = self._current_node()
        menu = QMenu(parent)
        if node is None:
            menu.addAction(self._source.get("job")) if self._source.get("job") else None
            return menu
        kind = node.kind
        if kind is OperationManagerNodeKind.OPERATION:
            menu.addAction(self.open)
            menu.addAction(self.recalculate)
            menu.addAction(self.simulate)
            menu.addAction(self.generate_post)
            menu.addAction(self.add_to_program)
            menu.addSeparator()
            menu.addAction(self.enable)
            menu.addAction(self.disable)
            menu.addAction(self.duplicate)
            menu.addAction(self.delete)
        elif kind is OperationManagerNodeKind.OPERATION_GEOMETRY:
            menu.addAction(self.open)
            menu.addAction(self.bind_geometry)
            menu.addAction(self.clear_geometry)
        elif kind is OperationManagerNodeKind.OPERATION_TOOL:
            menu.addAction(self.open)
        elif kind is OperationManagerNodeKind.TOOLPATH:
            menu.addAction(self.toggle_toolpath)
            menu.addAction(self.recalculate)
            menu.addAction(self.clear_toolpath)
        elif kind is OperationManagerNodeKind.SIMULATION:
            menu.addAction(self.open_simulation)
            menu.addAction(self.simulate)
            menu.addAction(self.clear_simulation)
        elif kind is OperationManagerNodeKind.POST_RESULT:
            menu.addAction(self.open_post)
            menu.addAction(self.generate_post)
            menu.addAction(self.clear_post)
        elif kind is OperationManagerNodeKind.NC_ARTIFACT:
            menu.addAction(self.show_export_details)
            menu.addAction(self.export_nc)
            menu.addAction(self.clear_nc)
        elif kind is OperationManagerNodeKind.PROGRAM_ASSEMBLY:
            menu.addAction(self.open_post)
        elif kind in {
            OperationManagerNodeKind.SETUP,
            OperationManagerNodeKind.OPERATIONS,
            OperationManagerNodeKind.GROUP,
            OperationManagerNodeKind.EMPTY_STATE,
        }:
            menu.addAction(self.add_operation)
            if kind is OperationManagerNodeKind.GROUP:
                menu.addAction(self.rename)
                menu.addAction(self.move_up)
                menu.addAction(self.move_down)
                menu.addAction(self.delete)
        elif kind is OperationManagerNodeKind.JOB:
            setup = self._source.get("setup")
            if setup is not None:
                menu.addAction(setup)
            menu.addAction(self.rename)
            menu.addAction(self.delete)
        elif kind is OperationManagerNodeKind.PROJECT:
            job = self._source.get("job")
            if job is not None:
                menu.addAction(job)
        else:
            menu.addAction(self.open)
        return menu

    def trigger_default(self) -> None:
        node = self._current_node()
        if node is None:
            return
        if node.kind is OperationManagerNodeKind.SIMULATION:
            self._open_simulation()
        elif node.kind in {
            OperationManagerNodeKind.POST_RESULT,
            OperationManagerNodeKind.NC_ARTIFACT,
        }:
            self._post()
        elif node.kind is OperationManagerNodeKind.PROGRAM_ASSEMBLY:
            self._open_program_assembly()
        else:
            self._open()

    def _action(self, text: str, callback) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(callback)
        return action

    def _ensure_selection(self) -> OperationManagerNode | None:
        node = self._current_node()
        if node is not None and node.legacy_selection is not None:
            self._workspace.select_identity(
                node.legacy_selection.kind, node.legacy_selection.value
            )
        return node

    def _trigger(self, key: str) -> None:
        self._ensure_selection()
        action = self._source.get(key)
        if action is not None and action.isEnabled():
            action.trigger()

    def _add_default(self) -> None:
        self._ensure_selection()
        action = self._source.get("operation")
        if action is not None:
            action.trigger()

    def _recalculate(self) -> None:
        self._trigger("generate")

    def _simulate(self) -> None:
        self._ensure_selection()
        self.simulation_requested.emit()
        button = self._workspace.simulation_panel.run_button
        if button.isEnabled():
            button.click()

    def _post(self) -> None:
        self._ensure_selection()
        self._workspace.post_tabs.setCurrentWidget(self._workspace.post_panel)
        self.post_requested.emit()

    def _generate_post(self) -> None:
        self._post()
        button = self._workspace.post_panel.generate_button
        if button.isEnabled():
            button.click()

    def _open_simulation(self) -> None:
        self._ensure_selection()
        self.simulation_requested.emit()

    def _open_program_assembly(self) -> None:
        self._ensure_selection()
        self._workspace.post_tabs.setCurrentWidget(
            self._workspace.program_assembly_panel
        )
        self.post_requested.emit()

    def _add_to_program(self) -> None:
        self._open_program_assembly()
        button = self._workspace.program_assembly_panel.add_button
        if button.isEnabled():
            button.click()

    def _export_nc(self) -> None:
        self._post()
        button = self._workspace.post_panel.export_button
        if button.isEnabled():
            button.click()

    def _show_export_details(self) -> None:
        self._post()
        button = self._workspace.post_panel.export_details_button
        if button.isEnabled():
            button.click()

    def _delete(self) -> None:
        self._trigger("delete")

    def _duplicate(self) -> None:
        self._ensure_selection()
        self._workspace.duplicate_selected_operation()

    def _open(self) -> None:
        node = self._ensure_selection()
        if node is not None:
            self.editor_requested.emit()

    def _rename(self) -> None:
        self._ensure_selection()
        item = self._workspace.tree.currentItem()
        if item is not None:
            self._workspace.tree.editItem(item, 0)

    def _set_enabled(self, enabled: bool) -> None:
        self._ensure_selection()
        self._workspace.set_selected_enabled(enabled)

    def _clear_simulation(self) -> None:
        self._ensure_selection()
        button = self._workspace.simulation_panel.clear_button
        if button.isEnabled():
            button.click()

    def _clear_post(self) -> None:
        self._ensure_selection()
        button = self._workspace.post_panel.clear_post_button
        if button.isEnabled():
            button.click()

    def _clear_nc(self) -> None:
        self._ensure_selection()
        button = self._workspace.post_panel.clear_managed_button
        if button.isEnabled():
            button.click()

    @staticmethod
    def _set_capability(action, capability, capabilities, reason) -> None:
        _set_enabled(action, capability in capabilities, reason)


def _set_enabled(action: QAction, enabled: bool, reason: str) -> None:
    action.setEnabled(enabled)
    action.setToolTip("" if enabled else reason)
    action.setStatusTip("" if enabled else reason)
    action.setWhatsThis("" if enabled else f"Không khả dụng: {reason}")


def _disable(action: QAction, reason: str) -> None:
    _set_enabled(action, False, reason)
