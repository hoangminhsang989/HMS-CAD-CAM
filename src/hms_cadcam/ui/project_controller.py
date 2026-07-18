"""Qt dialogs and actions that delegate all project work to ProjectService."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMainWindow, QMenu, QMessageBox, QStyle

from hms_cadcam.project.exceptions import (
    DatabaseMissingError,
    InvalidProjectNameError,
    ManifestDecodeError,
    ManifestMissingError,
    ProjectAlreadyExistsError,
    ProjectDatabaseError,
    ProjectError,
    ProjectPermissionError,
    SourceFileNotFoundError,
    UnsupportedFormatVersionError,
    UnsupportedProjectFormatError,
)
from hms_cadcam.project.models import ProjectSession, UnitSystem
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.project_worker import ProjectTask

logger = logging.getLogger(__name__)


class ProjectUiController(QObject):
    """Own project actions, dialogs, and background task coordination."""

    project_changed = Signal(object)
    message = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, window: QMainWindow, service: ProjectService) -> None:
        super().__init__(window)
        self._window = window
        self._service = service
        self._thread_pool = QThreadPool.globalInstance()
        self._active_task: ProjectTask | None = None
        self.actions = self._create_actions()
        self._update_action_states()

    @property
    def service(self) -> ProjectService:
        """Expose read-only access to current project state for MainWindow."""
        return self._service

    @property
    def is_busy(self) -> bool:
        """Return whether a project filesystem operation is running."""
        return self._active_task is not None

    def populate_recent_menu(self, menu: QMenu) -> None:
        """Rebuild the recent menu using successfully opened projects."""
        menu.clear()
        entries = self._service.recent_projects()
        if not entries:
            empty = menu.addAction("Không có dự án gần đây")
            empty.setEnabled(False)
            return
        for entry in entries:
            action = menu.addAction(str(entry.path))
            action.triggered.connect(
                lambda checked=False, path=entry.path: self._start_operation(
                    lambda: self._service.open_project(path)
                )
            )

    def request_application_close(self) -> bool:
        """Return whether the window can close without losing project work."""
        if self.is_busy:
            QMessageBox.information(
                self._window,
                "HMS CAD/CAM",
                "Đang thực hiện tác vụ dự án. Vui lòng chờ hoàn tất.",
            )
            return False
        return self._close_current_interactively()

    @Slot()
    def new_project(self) -> None:
        """Collect New Project inputs and run creation in a worker."""
        values = self._request_destination("Tạo dự án HMS mới")
        if values is None:
            return
        parent, name, units, overwrite = values
        self._start_operation(
            lambda: self._service.new_project(parent, name, units, overwrite=overwrite)
        )

    @Slot()
    def import_source(self) -> None:
        """Select a CAD source and copy it without parsing geometry."""
        selected, _ = QFileDialog.getOpenFileName(
            self._window,
            "Chọn file CAD nguồn",
            "",
            "CAD (*.step *.stp *.iges *.igs *.brep *.stl *.dxf);;Tất cả file (*)",
        )
        if not selected:
            return
        source = Path(selected)
        if self._service.has_project:
            self._start_operation(lambda: self._service.import_source(source))
            return
        values = self._request_destination(
            "Lưu file nguồn thành dự án HMS",
            default_parent=source.parent,
            default_name=source.stem,
        )
        if values is None:
            return
        parent, name, units, overwrite = values
        self._start_operation(
            lambda: self._service.create_project_from_source(
                parent,
                name,
                source,
                units,
                overwrite=overwrite,
            )
        )

    @Slot()
    def open_project(self) -> None:
        """Choose and validate a real .HMS directory."""
        selected = QFileDialog.getExistingDirectory(
            self._window,
            "Mở dự án HMS",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self._start_operation(lambda: self._service.open_project(Path(selected)))

    @Slot()
    def save_project(self) -> None:
        """Save the current manifest."""
        self._start_operation(self._service.save)

    @Slot()
    def save_project_as(self) -> None:
        """Collect Save As inputs and create an independent project."""
        current = self._service.current_project
        if current is None:
            return
        values = self._request_destination(
            "Lưu dự án thành",
            default_parent=current.root_path.parent,
            default_name=current.manifest.project_name,
            include_units=False,
        )
        if values is None:
            return
        parent, name, _units, overwrite = values
        self._start_operation(
            lambda: self._service.save_as(parent, name, overwrite=overwrite)
        )

    @Slot()
    def close_project(self) -> None:
        """Close the current project after Save/Discard/Cancel handling."""
        if self._close_current_interactively():
            self.project_changed.emit(None)
            self._update_action_states()

    def _create_actions(self) -> dict[str, QAction]:
        style = self._window.style()
        definitions = {
            "new": ("Dự án mới", QStyle.StandardPixmap.SP_FileIcon, self.new_project, "Ctrl+N"),
            "import": ("Nhập file CAD nguồn", QStyle.StandardPixmap.SP_ArrowDown, self.import_source, ""),
            "open": ("Mở dự án HMS", QStyle.StandardPixmap.SP_DialogOpenButton, self.open_project, "Ctrl+O"),
            "save": ("Lưu", QStyle.StandardPixmap.SP_DialogSaveButton, self.save_project, "Ctrl+S"),
            "save_as": ("Lưu thành", QStyle.StandardPixmap.SP_DialogSaveButton, self.save_project_as, "Ctrl+Shift+S"),
            "close": ("Đóng dự án", QStyle.StandardPixmap.SP_DialogCloseButton, self.close_project, "Ctrl+W"),
        }
        actions: dict[str, QAction] = {}
        for key, (text, icon_name, slot, shortcut) in definitions.items():
            action = QAction(style.standardIcon(icon_name), text, self)
            action.setObjectName(f"Project{key.title().replace('_', '')}Action")
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            actions[key] = action
        return actions

    def _request_destination(
        self,
        title: str,
        default_parent: Path | None = None,
        default_name: str = "Dự án mới",
        include_units: bool = True,
    ) -> tuple[Path, str, UnitSystem, bool] | None:
        selected_parent = QFileDialog.getExistingDirectory(
            self._window,
            title,
            str(default_parent or Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected_parent:
            return None
        name, accepted = QInputDialog.getText(
            self._window,
            title,
            "Tên dự án:",
            text=default_name,
        )
        if not accepted:
            return None
        units = UnitSystem.MILLIMETER
        if include_units:
            unit_text, accepted = QInputDialog.getItem(
                self._window,
                title,
                "Đơn vị:",
                [UnitSystem.MILLIMETER.value, UnitSystem.INCH.value],
                editable=False,
            )
            if not accepted:
                return None
            units = UnitSystem(unit_text)
        try:
            target = self._service.target_path(Path(selected_parent), name)
        except ProjectError as error:
            self._show_error(error)
            return None
        overwrite = False
        if self._service.project_exists(Path(selected_parent), name):
            response = QMessageBox.question(
                self._window,
                "Xác nhận ghi đè",
                f"Dự án đã tồn tại:\n{target}\n\nThay thế dự án này?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return None
            overwrite = True
        return Path(selected_parent), name, units, overwrite

    def _start_operation(self, operation: Callable[[], object]) -> None:
        if self.is_busy:
            return
        task = ProjectTask(operation)
        self._active_task = task
        task.signals.succeeded.connect(self._operation_succeeded)
        task.signals.failed.connect(self._show_error)
        task.signals.finished.connect(self._operation_finished)
        self._set_busy(True)
        self._thread_pool.start(task)

    @Slot(object)
    def _operation_succeeded(self, result: object) -> None:
        if isinstance(result, ProjectSession):
            self.project_changed.emit(result)
            self.message.emit(f"Dự án hiện hành: {result.root_path}")

    @Slot(object)
    def _show_error(self, error: object) -> None:
        logger.error("Tác vụ dự án thất bại", exc_info=(type(error), error, error.__traceback__) if isinstance(error, BaseException) else None)
        messages = {
            InvalidProjectNameError: "Tên dự án không hợp lệ trên Windows.",
            SourceFileNotFoundError: "File nguồn không tồn tại hoặc không đọc được.",
            ProjectAlreadyExistsError: "Dự án đích đã tồn tại.",
            ProjectPermissionError: "Không có quyền đọc hoặc ghi tại đường dẫn đã chọn.",
            ManifestMissingError: "Dự án thiếu file project.hms.json.",
            ManifestDecodeError: "Manifest dự án không phải JSON UTF-8 hợp lệ.",
            UnsupportedProjectFormatError: "Thư mục không thuộc định dạng HMS_PROJECT.",
            UnsupportedFormatVersionError: "Phiên bản dự án chưa được ứng dụng hỗ trợ.",
            DatabaseMissingError: "Dự án thiếu project.db.",
            ProjectDatabaseError: "Database dự án bị lỗi hoặc không thể mở.",
        }
        text = next((message for kind, message in messages.items() if isinstance(error, kind)), "Không thể hoàn tất tác vụ dự án.")
        QMessageBox.critical(self._window, "HMS CAD/CAM", text)

    @Slot()
    def _operation_finished(self) -> None:
        self._active_task = None
        self._set_busy(False)
        self._update_action_states()

    def _set_busy(self, busy: bool) -> None:
        for action in self.actions.values():
            action.setEnabled(not busy)
        self.busy_changed.emit(busy)

    def _update_action_states(self) -> None:
        has_project = self._service.has_project
        self.actions["new"].setEnabled(not self.is_busy)
        self.actions["import"].setEnabled(not self.is_busy)
        self.actions["open"].setEnabled(not self.is_busy)
        for key in ("save", "save_as", "close"):
            self.actions[key].setEnabled(has_project and not self.is_busy)

    def _close_current_interactively(self) -> bool:
        if not self._service.has_project:
            return True
        if self._service.is_dirty:
            response = QMessageBox.warning(
                self._window,
                "Dự án chưa lưu",
                "Dự án có thay đổi chưa lưu.",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if response == QMessageBox.StandardButton.Cancel:
                return False
            if response == QMessageBox.StandardButton.Save:
                try:
                    self._service.save()
                except ProjectError as error:
                    self._show_error(error)
                    return False
            self._service.close_project(discard_changes=True)
        else:
            self._service.close_project()
        return True
