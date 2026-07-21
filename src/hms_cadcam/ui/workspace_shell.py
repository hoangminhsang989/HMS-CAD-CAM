"""Contextual workspace selection for the Stage 9A.2 main shell."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QToolBar, QWidget


class WorkspaceId(StrEnum):
    """Stable identifiers saved in user settings, never in project data."""

    HOME = "home"
    CAD = "cad"
    MILL_2D = "mill_2d"
    MILL_3D = "mill_3d"
    LATHE = "lathe"
    SIMULATION = "simulation"
    POST = "post"


class WorkspaceBar(QToolBar):
    """Compact top-level environment selector with honest capability states."""

    workspace_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Môi trường làm việc", parent)
        self.setObjectName("WorkspaceBar")
        self.setAccessibleName("Chọn môi trường làm việc HMS")
        self.setMovable(False)
        self.setFloatable(False)
        self._group = QActionGroup(self)
        self._group.setExclusive(True)
        self.actions_by_workspace: dict[WorkspaceId, QAction] = {}
        definitions = (
            (WorkspaceId.HOME, "HOME", True, "Tổng quan dự án và lệnh thường dùng"),
            (WorkspaceId.CAD, "CAD", True, "Thiết kế, nhập và kiểm tra CAD"),
            (WorkspaceId.MILL_2D, "MILL 2D", True, "Lập trình CAM 2D/2.5D hiện có"),
            (
                WorkspaceId.MILL_3D,
                "MILL 3D",
                False,
                "CAM 3D mới ở mức Foundation; giao diện production chưa triển khai",
            ),
            (
                WorkspaceId.LATHE,
                "LATHE",
                False,
                "Tiện chưa được triển khai trong HMS",
            ),
            (
                WorkspaceId.SIMULATION,
                "SIMULATION",
                True,
                "Mở panel mô phỏng hiện có",
            ),
            (WorkspaceId.POST, "POST", True, "Mở panel Post và Program Assembly"),
        )
        for workspace, label, enabled, explanation in definitions:
            action = QAction(label, self)
            action.setObjectName(f"Workspace{_object_suffix(workspace)}Action")
            action.setCheckable(True)
            action.setEnabled(enabled)
            action.setToolTip(explanation)
            action.setStatusTip(explanation)
            action.setData(workspace.value)
            self._group.addAction(action)
            self.addAction(action)
            self.actions_by_workspace[workspace] = action
        self._group.triggered.connect(self._workspace_triggered)
        self.set_active_workspace(WorkspaceId.HOME)

    @property
    def active_workspace(self) -> WorkspaceId:
        """Return the checked workspace, falling back to HOME."""
        checked = self._group.checkedAction()
        if checked is None:
            return WorkspaceId.HOME
        try:
            return WorkspaceId(str(checked.data()))
        except ValueError:
            return WorkspaceId.HOME

    def set_active_workspace(self, workspace: WorkspaceId | str) -> WorkspaceId:
        """Select an enabled workspace without manufacturing unavailable behavior."""
        try:
            selected = WorkspaceId(workspace)
        except ValueError:
            selected = WorkspaceId.HOME
        action = self.actions_by_workspace[selected]
        if not action.isEnabled():
            selected = WorkspaceId.HOME
            action = self.actions_by_workspace[selected]
        action.setChecked(True)
        return selected

    def _workspace_triggered(self, action: QAction) -> None:
        self.workspace_changed.emit(str(action.data()))


def _object_suffix(workspace: WorkspaceId) -> str:
    return "".join(part.title() for part in workspace.value.split("_"))
