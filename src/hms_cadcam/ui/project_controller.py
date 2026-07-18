"""Qt dialogs and actions that delegate all project work to ProjectService."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot
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
    ProjectLockedError,
    ProjectLockUnknownError,
    ProjectPermissionError,
    RecoveryRequiredError,
    RecoveryRollbackError,
    RecoverySnapshotInvalidError,
    RecoveryTransactionError,
    ReplacedProjectAmbiguousError,
    ReplacedProjectInvalidError,
    ReplacedProjectRecoveryRequiredError,
    SourceFileNotFoundError,
    UnsupportedFormatVersionError,
    UnsupportedProjectFormatError,
)
from hms_cadcam.project.models import ProjectSession, UnitSystem
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.project_worker import ProjectTask

logger = logging.getLogger(__name__)
_AUTOSAVE_INTERVAL_MS = 5 * 60 * 1000


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
        self._pending_operation: Callable[[], object] | None = None
        self._autosave_task: ProjectTask | None = None
        self._autosave_pending = False
        self._autosave_generation = 0
        self._autosave_project_id: UUID | None = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(_AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self.request_autosave)
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

    @property
    def is_autosaving(self) -> bool:
        """Return whether an autosave snapshot is running in a worker."""
        return self._autosave_task is not None

    @property
    def autosave_interval_ms(self) -> int:
        """Return the configured periodic autosave interval."""
        return self._autosave_timer.interval()

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
        if self.is_busy or self.is_autosaving:
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
        if self.is_autosaving:
            return
        if self._close_current_interactively():
            self.project_changed.emit(None)
            self._update_action_states()

    @Slot()
    def request_autosave(self) -> None:
        """Queue a snapshot for the current dirty project when it is safe."""
        session = self._service.current_project
        if session is None or not session.is_dirty:
            return
        if self.is_busy or self.is_autosaving:
            self._autosave_pending = True
            return

        project_id = session.manifest.project_id
        if self._autosave_project_id != project_id:
            self._bind_autosave_session()
        generation = self._autosave_generation
        self._autosave_timer.stop()
        task = ProjectTask(
            lambda: self._service.autosave(expected_project_id=project_id)
        )
        self._autosave_task = task
        task.signals.succeeded.connect(
            lambda result: self._autosave_succeeded(
                result, generation=generation, project_id=project_id
            )
        )
        task.signals.failed.connect(
            lambda error: self._autosave_failed(
                error, generation=generation, project_id=project_id
            )
        )
        task.signals.finished.connect(
            lambda: self._autosave_finished(
                task, generation=generation, project_id=project_id
            )
        )
        self._update_action_states()
        self._thread_pool.start(task)

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
        if self.is_busy or self.is_autosaving:
            return
        self._suspend_autosave_session()
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
        if isinstance(error, RecoveryRequiredError):
            self._request_autosave_recovery(error)
            return
        if isinstance(error, ReplacedProjectRecoveryRequiredError):
            self._request_replaced_recovery(error)
            return
        logger.error("Tác vụ dự án thất bại", exc_info=(type(error), error, error.__traceback__) if isinstance(error, BaseException) else None)
        messages = {
            ProjectLockedError: "Dự án đang được một phiên HMS khác sử dụng.",
            ProjectLockUnknownError: (
                "Không thể xác định chủ sở hữu khóa dự án; "
                "khóa được giữ nguyên để bảo vệ dữ liệu."
            ),
            RecoverySnapshotInvalidError: "Snapshot autosave không hợp lệ nên không thể phục hồi.",
            RecoveryTransactionError: "Phục hồi thất bại; dữ liệu chính ban đầu đã được khôi phục.",
            RecoveryRollbackError: "Phục hồi và rollback đều thất bại; cần kiểm tra thư mục backups.",
            ReplacedProjectAmbiguousError: "Có nhiều thư mục .replaced; HMS không tự chọn ứng viên.",
            ReplacedProjectInvalidError: "Thư mục .replaced không hợp lệ và được giữ nguyên.",
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
        pending = self._pending_operation
        self._pending_operation = None
        if pending is not None:
            self._start_operation(pending)
            return
        self._bind_autosave_session()

    def _autosave_succeeded(
        self,
        result: object,
        *,
        generation: int,
        project_id: UUID,
    ) -> None:
        if result is None or not self._autosave_context_matches(
            generation, project_id
        ):
            return
        self.message.emit("Đã tạo snapshot autosave.")

    def _autosave_failed(
        self,
        error: object,
        *,
        generation: int,
        project_id: UUID,
    ) -> None:
        logger.warning(
            "Autosave dự án thất bại",
            exc_info=(type(error), error, error.__traceback__)
            if isinstance(error, BaseException)
            else None,
        )
        if self._autosave_context_matches(generation, project_id):
            self.message.emit(
                "Autosave thất bại; dự án vẫn được giữ ở trạng thái chưa lưu."
            )

    def _autosave_finished(
        self,
        task: ProjectTask,
        *,
        generation: int,
        project_id: UUID,
    ) -> None:
        if self._autosave_task is not task:
            return
        self._autosave_task = None
        context_matches = self._autosave_context_matches(generation, project_id)
        repeat = context_matches and self._autosave_pending
        self._autosave_pending = False
        self._update_action_states()
        if repeat:
            QTimer.singleShot(0, self.request_autosave)
            return
        self._bind_autosave_session()

    def _autosave_context_matches(self, generation: int, project_id: UUID) -> bool:
        session = self._service.current_project
        return (
            generation == self._autosave_generation
            and project_id == self._autosave_project_id
            and session is not None
            and session.manifest.project_id == project_id
        )

    def _suspend_autosave_session(self) -> None:
        self._autosave_timer.stop()
        self._autosave_generation += 1
        self._autosave_project_id = None
        self._autosave_pending = False

    def _bind_autosave_session(self) -> None:
        session = self._service.current_project
        project_id = None if session is None else session.manifest.project_id
        if project_id != self._autosave_project_id:
            self._autosave_generation += 1
            self._autosave_project_id = project_id
            self._autosave_pending = False
        if session is not None and not self.is_busy and not self.is_autosaving:
            self._autosave_timer.start()
        else:
            self._autosave_timer.stop()

    def _request_autosave_recovery(self, error: RecoveryRequiredError) -> None:
        response = QMessageBox.warning(
            self._window,
            "Phát hiện lần đóng bất thường",
            "Có snapshot autosave hợp lệ từ phiên bị đóng bất thường.\n\n"
            "Chọn Yes để phục hồi, No để mở dữ liệu chính, hoặc Cancel để dừng.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if response == QMessageBox.StandardButton.Yes:
            self._pending_operation = lambda: self._service.recover_project(error.assessment)
        elif response == QMessageBox.StandardButton.No:
            self._pending_operation = lambda: self._service.open_project(
                error.assessment.project_root,
                discard_recovery=True,
            )

    def _request_replaced_recovery(
        self,
        error: ReplacedProjectRecoveryRequiredError,
    ) -> None:
        response = QMessageBox.warning(
            self._window,
            "Phát hiện dự án .replaced",
            "Dự án đích bị thiếu và có đúng một bản .replaced hợp lệ.\n\n"
            "Khôi phục thư mục này và tiếp tục mở dự án?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if response == QMessageBox.StandardButton.Yes:
            self._pending_operation = lambda: self._service.restore_replaced_and_open(
                error.assessment
            )

    def _set_busy(self, busy: bool) -> None:
        for action in self.actions.values():
            action.setEnabled(not busy)
        self.busy_changed.emit(busy)

    def _update_action_states(self) -> None:
        has_project = self._service.has_project
        operation_allowed = not self.is_busy and not self.is_autosaving
        self.actions["new"].setEnabled(operation_allowed)
        self.actions["import"].setEnabled(operation_allowed)
        self.actions["open"].setEnabled(operation_allowed)
        for key in ("save", "save_as", "close"):
            self.actions[key].setEnabled(has_project and operation_allowed)

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
            if not self._try_close_current(discard_changes=True):
                return False
        else:
            if not self._try_close_current(discard_changes=False):
                return False
        return True

    def _try_close_current(self, *, discard_changes: bool) -> bool:
        try:
            self._service.close_project(discard_changes=discard_changes)
        except ProjectError as error:
            self._show_error(error)
            return False
        self._suspend_autosave_session()
        return True
