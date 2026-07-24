"""Qt dialogs and actions that delegate all project work to ProjectService."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import (
    QFileSystemWatcher,
    QObject,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMainWindow, QMenu, QStyle

from hms_cadcam.project.exceptions import (
    DatabaseMissingError,
    GeometryTransferApplyError,
    GeometryTransferDuplicateError,
    GeometryTransferIntegrityError,
    GeometryTransferRecoveryError,
    GeometryTransferTargetError,
    InvalidProjectNameError,
    InvalidHmsFilenameError,
    HmsContainerError,
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
    UnsafeWorkspacePathError,
)
from hms_cadcam.project.constants import (
    INCOMING_GEOMETRY_DIRECTORY,
    INCOMING_GEOMETRY_PENDING_DIRECTORY,
)
from hms_cadcam.project.geometry_transfer import (
    GeometryApplyChoice,
    GeometryApplyResult,
    GeometryTransferRequest,
    GeometryTransferStatus,
    IncomingGeometryPreview,
)
from hms_cadcam.project.models import ProjectSession, UnitSystem
from hms_cadcam.project.path_policy import ensure_hms_suffix
from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.workspace import (
    DocumentMode,
    PreparedDocumentOpen,
    WorkspaceState,
)
from hms_cadcam.ui.project_worker import ProjectTask
from hms_cadcam.ui.geometry_transfer_ui import CamProjectTargetDialog
from hms_cadcam.ui.localized_dialogs import QFileDialog, QMessageBox
from hms_cadcam.ui.workspace_dialog import CamProjectDialog

logger = logging.getLogger(__name__)
_AUTOSAVE_INTERVAL_MS = 5 * 60 * 1000


class ProjectUiController(QObject):
    """Own project actions, dialogs, and background task coordination."""

    project_changed = Signal(object)
    message = Signal(str)
    busy_changed = Signal(bool)
    document_open_requested = Signal(object)
    incoming_geometry_changed = Signal(object)
    incoming_geometry_preview_ready = Signal(object)
    geometry_transfer_sent = Signal(object)
    geometry_apply_completed = Signal(object)

    def __init__(self, window: QMainWindow, service: ProjectService) -> None:
        super().__init__(window)
        self._window = window
        self._service = service
        self._thread_pool = QThreadPool.globalInstance()
        self._active_task: ProjectTask | None = None
        self._pending_operation: Callable[[], object] | None = None
        self._project_change_guard: Callable[[], bool] | None = None
        self._autosave_task: ProjectTask | None = None
        self._autosave_pending = False
        self._autosave_generation = 0
        self._autosave_project_id: UUID | None = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(_AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self.request_autosave)
        self._incoming_scan_task: ProjectTask | None = None
        self._incoming_scan_generation = 0
        self._incoming_scan_pending = False
        self._monitored_project_id: UUID | None = None
        self._deferred_this_session: set[UUID] = set()
        self._incoming_requests: tuple[GeometryTransferRequest, ...] = ()
        self._inbox_watcher = QFileSystemWatcher(self)
        self._inbox_watcher.directoryChanged.connect(
            lambda _path: self.request_incoming_scan()
        )
        self._inbox_timer = QTimer(self)
        self._inbox_timer.setInterval(2500)
        self._inbox_timer.timeout.connect(self.request_incoming_scan)
        self.actions = self._create_actions()
        self.project_changed.connect(self._bind_inbox_monitor)
        self._bind_inbox_monitor()
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

    @property
    def incoming_requests(self) -> tuple[GeometryTransferRequest, ...]:
        """Return validated pending/deferred requests for the notification center."""
        return self._incoming_requests

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
                lambda checked=False, path=entry.path: self._open_recent(path)
            )

    def set_project_change_guard(self, guard: Callable[[], bool] | None) -> None:
        """Install a UI-only guard for transient drafts outside project payloads."""
        self._project_change_guard = guard

    def _can_change_project(self) -> bool:
        return self._project_change_guard is None or self._project_change_guard()

    def _open_recent(self, path: Path) -> None:
        if self._can_change_project():
            self._start_operation(lambda: self._service.open_project(path))

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
        """Collect the explicit parent/name CAM workspace contract."""
        if not self._can_change_project() or not self._prepare_workspace_replacement():
            return
        dialog = CamProjectDialog(self._window, title="Tạo dự án CAM")
        if dialog.exec() != CamProjectDialog.DialogCode.Accepted:
            return
        parent = dialog.parent_directory
        if parent is None:
            return
        self._start_operation(
            lambda: self._service.create_cam_workspace(
                parent,
                dialog.project_name,
                UnitSystem.MILLIMETER,
            )
        )

    @Slot()
    def new_project_from_document(self) -> None:
        """Create a CAM project transactionally from the current CAD document."""
        workspace = self._service.current_workspace
        if (
            workspace is None
            or workspace.mode is not DocumentMode.CAD_DOCUMENT
            or not self._can_change_project()
        ):
            return
        dialog = CamProjectDialog(
            self._window,
            title="Tạo dự án CAM từ tài liệu hiện tại",
            default_name=workspace.display_name,
            default_parent=workspace.suggested_save_directory,
        )
        if dialog.exec() != CamProjectDialog.DialogCode.Accepted:
            return
        parent = dialog.parent_directory
        if parent is None:
            return
        self._start_operation(
            lambda: self._service.create_cam_workspace_from_document(
                parent,
                dialog.project_name,
                UnitSystem.MILLIMETER,
            )
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
        """Choose and validate a legacy or folder-based CAM project."""
        selected = QFileDialog.getExistingDirectory(
            self._window,
            "Mở dự án CAM",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            if (
                not self._can_change_project()
                or not self._prepare_workspace_replacement()
            ):
                return
            self._start_operation(lambda: self._service.open_project(Path(selected)))

    @Slot()
    def open_document(self) -> None:
        """Open a supported source or standalone HMS file through one command."""
        selected, _ = QFileDialog.getOpenFileName(
            self._window,
            "Mở trong HMS",
            "",
            (
                "Tài liệu HMS và CAD "
                "(*.HMS *.hms *.step *.stp *.brep *.brp *.iges *.igs *.stl);;"
                "Tất cả file (*)"
            ),
        )
        if selected:
            self.request_open_path(Path(selected))

    def request_open_paths(self, paths: tuple[Path, ...]) -> bool:
        """Apply the explicit single-file policy used by drag/drop."""
        if len(paths) != 1:
            QMessageBox.information(
                self._window,
                "Mở trong HMS",
                "Mỗi lần chỉ mở một tệp; HMS không tự trộn nhiều mô hình.",
            )
            return False
        return self.request_open_path(paths[0])

    def request_open_path(self, path: Path) -> bool:
        """Route dialog/drop to the same lifecycle and application command."""
        if self.is_busy or self.is_autosaving or not self._can_change_project():
            return False
        if not self._prepare_workspace_replacement():
            return False
        self._start_operation(lambda: self._service.prepare_document_open(path))
        return True

    def document_open_succeeded(self, state: object) -> None:
        """Publish a committed document state after the CAD importer succeeds."""
        if not isinstance(state, WorkspaceState):
            return
        self.project_changed.emit(state)
        self.message.emit(
            f"{state.mode.display_text} hiện hành: {state.display_name}"
        )
        self._bind_autosave_session()
        self._update_action_states()

    @Slot()
    def save_project(self) -> None:
        """Route Save according to the typed workspace mode."""
        workspace = self._service.current_workspace
        if workspace is None:
            return
        if workspace.mode is DocumentMode.CAD_DOCUMENT:
            if workspace.physical_path is None:
                self.save_project_as()
            else:
                self._start_operation(self._service.save_document)
            return
        self._start_operation(self._service.save)

    @Slot()
    def save_project_as(self) -> None:
        """Save As a standalone HMS document; CAM Save remains in project root."""
        workspace = self._service.current_workspace
        if workspace is None or workspace.mode is not DocumentMode.CAD_DOCUMENT:
            return
        suggestion = self._service.suggested_document_path()
        selected, _ = QFileDialog.getSaveFileName(
            self._window,
            "Lưu thành tài liệu HMS",
            str(suggestion),
            "Tài liệu HMS (*.HMS)",
        )
        if not selected:
            return
        target = Path(selected)
        target = target.with_name(ensure_hms_suffix(target.name))
        self._start_operation(
            lambda: self._service.save_document(target)
        )

    @Slot()
    def send_geometry_to_cam(self) -> None:
        """Publish the saved standalone document into a validated CAM inbox."""
        workspace = self._service.current_workspace
        document = self._service.current_document
        if (
            workspace is None
            or workspace.mode is not DocumentMode.CAD_DOCUMENT
            or document is None
        ):
            return
        if (
            workspace.physical_path is None
            or workspace.physical_path.suffix.casefold() != ".hms"
            or workspace.dirty
        ):
            response = QMessageBox.warning(
                self._window,
                "Nạp 3D mới cho dự án CAM",
                "Hãy lưu tài liệu HMS trước khi nạp 3D sang dự án CAM.",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if response is not QMessageBox.StandardButton.Save:
                return
            if not self._save_current_for_lifecycle():
                return
            workspace = self._service.current_workspace
            document = self._service.current_document
        if (
            workspace is None
            or document is None
            or workspace.physical_path is None
            or workspace.dirty
            or not document.geometry_path.is_file()
        ):
            return
        dialog = CamProjectTargetDialog(self._service, self._window)
        if dialog.exec() != CamProjectTargetDialog.DialogCode.Accepted:
            return
        target = dialog.project_root
        if target is None:
            return
        self.message.emit("Đang chuẩn bị dữ liệu 3D.")
        self._start_operation(
            lambda: self._service.send_document_geometry(target)
        )

    def request_incoming_preview(self, request_id: UUID) -> None:
        """Load a validated preview in the worker used for project I/O."""
        self._start_operation(
            lambda: self._service.incoming_geometry_preview(request_id)
        )

    def defer_incoming_geometry(self, request_id: UUID) -> None:
        self._start_operation(
            lambda: self._service.defer_incoming_geometry(request_id)
        )

    def reject_incoming_geometry(self, request_id: UUID) -> None:
        response = QMessageBox.question(
            self._window,
            "Bỏ qua dữ liệu 3D",
            "Bỏ qua yêu cầu này? Dữ liệu được giữ lại để kiểm tra, không bị xóa.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response is QMessageBox.StandardButton.Yes:
            self._start_operation(
                lambda: self._service.reject_incoming_geometry(request_id)
            )

    def apply_incoming_geometry(
        self,
        request_id: UUID,
        choice: GeometryApplyChoice,
        target_source_id: UUID | None,
    ) -> None:
        if not isinstance(choice, GeometryApplyChoice):
            return
        self._start_operation(
            lambda: self._service.apply_incoming_geometry(
                request_id,
                choice,
                target_source_id=target_source_id,
            )
        )

    @Slot()
    def request_incoming_scan(self) -> None:
        """Poll the filesystem inbox without blocking the UI thread."""
        workspace = self._service.current_workspace
        if (
            workspace is None
            or workspace.mode is not DocumentMode.CAM_PROJECT
            or self.is_busy
            or self.is_autosaving
        ):
            return
        if self._incoming_scan_task is not None:
            self._incoming_scan_pending = True
            return
        generation = self._incoming_scan_generation
        project_id = workspace.identity
        task = ProjectTask(self._service.scan_incoming_geometry)
        self._incoming_scan_task = task
        task.signals.succeeded.connect(
            lambda result: self._incoming_scan_succeeded(
                result,
                generation=generation,
                project_id=project_id,
            )
        )
        task.signals.failed.connect(self._incoming_scan_failed)
        task.signals.finished.connect(
            lambda: self._incoming_scan_finished(task)
        )
        self._thread_pool.start(task)

    @Slot()
    def close_project(self) -> None:
        """Close the current workspace after Save/Discard/Cancel handling."""
        if self.is_autosaving:
            return
        if not self._can_change_project():
            return
        if self._close_current_interactively():
            self.project_changed.emit(None)
            self._update_action_states()

    @Slot()
    def request_autosave(self) -> None:
        """Queue a snapshot for the current dirty project when it is safe."""
        workspace = self._service.current_workspace
        if workspace is None or not workspace.dirty:
            return
        if self.is_busy or self.is_autosaving:
            self._autosave_pending = True
            return

        project_id = workspace.identity
        if self._autosave_project_id != project_id:
            self._bind_autosave_session()
        generation = self._autosave_generation
        self._autosave_timer.stop()
        task = ProjectTask(
            lambda: self._service.autosave_workspace(
                expected_identity=project_id
            )
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
            "new": ("Tạo dự án CAM", QStyle.StandardPixmap.SP_FileIcon, self.new_project, "Ctrl+N"),
            "new_from_document": ("Tạo dự án CAM từ tài liệu hiện tại", QStyle.StandardPixmap.SP_FileDialogNewFolder, self.new_project_from_document, ""),
            "import": ("Nhập tệp CAD nguồn", QStyle.StandardPixmap.SP_ArrowDown, self.import_source, ""),
            "open": ("Mở", QStyle.StandardPixmap.SP_DialogOpenButton, self.open_document, "Ctrl+O"),
            "open_project": ("Mở dự án CAM", QStyle.StandardPixmap.SP_DirOpenIcon, self.open_project, ""),
            "save": ("Lưu", QStyle.StandardPixmap.SP_DialogSaveButton, self.save_project, "Ctrl+S"),
            "save_as": ("Lưu thành tài liệu HMS", QStyle.StandardPixmap.SP_DialogSaveButton, self.save_project_as, "Ctrl+Shift+S"),
            "send_geometry": ("Nạp 3D mới cho dự án CAM", QStyle.StandardPixmap.SP_ArrowForward, self.send_geometry_to_cam, ""),
            "close": ("Đóng tài liệu/không gian làm việc", QStyle.StandardPixmap.SP_DialogCloseButton, self.close_project, "Ctrl+W"),
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
        elif isinstance(result, WorkspaceState):
            self.project_changed.emit(result)
            self.message.emit(
                f"{result.mode.display_text} hiện hành: {result.display_name}"
            )
        elif isinstance(result, PreparedDocumentOpen):
            self.document_open_requested.emit(result)
        elif isinstance(result, IncomingGeometryPreview):
            self.incoming_geometry_preview_ready.emit(result)
        elif isinstance(result, GeometryApplyResult):
            session = self._service.current_project
            if session is not None:
                self.project_changed.emit(session)
            self.geometry_apply_completed.emit(result)
            self.message.emit(
                "Đã cập nhật dữ liệu 3D; các kết quả phụ thuộc được đánh dấu stale."
            )
            QTimer.singleShot(0, self.request_incoming_scan)
        elif isinstance(result, GeometryTransferRequest):
            if self._service.current_document is not None:
                self.geometry_transfer_sent.emit(result)
                self.message.emit("Đã nạp dữ liệu vào vùng chờ.")
            else:
                if result.status is GeometryTransferStatus.DEFERRED:
                    self._deferred_this_session.add(result.request_id)
                    self.message.emit("Đã để sau yêu cầu dữ liệu 3D.")
                elif result.status is GeometryTransferStatus.REJECTED:
                    self.message.emit("Đã bỏ qua yêu cầu dữ liệu 3D.")
                QTimer.singleShot(0, self.request_incoming_scan)

    @Slot(object)
    def _show_error(self, error: object) -> None:
        if isinstance(error, RecoveryRequiredError):
            self._request_autosave_recovery(error)
            return
        if isinstance(error, ReplacedProjectRecoveryRequiredError):
            self._request_replaced_recovery(error)
            return
        if isinstance(error, GeometryTransferDuplicateError):
            QMessageBox.information(
                self._window,
                "Dữ liệu 3D này đã được gửi",
                "Dữ liệu 3D này đã được gửi tới dự án và đang chờ xử lý.",
            )
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
            RecoveryRollbackError: (
                "Phục hồi và hoàn tác đều thất bại; cần kiểm tra thư mục "
                "bản sao lưu."
            ),
            ReplacedProjectAmbiguousError: "Có nhiều thư mục .replaced; HMS không tự chọn ứng viên.",
            ReplacedProjectInvalidError: "Thư mục .replaced không hợp lệ và được giữ nguyên.",
            InvalidProjectNameError: "Tên dự án không hợp lệ trên Windows.",
            InvalidHmsFilenameError: "Tên file HMS không hợp lệ trên Windows.",
            UnsafeWorkspacePathError: "Đường dẫn không hợp lệ cho dự án CAM.",
            HmsContainerError: "Tài liệu HMS bị hỏng hoặc không thể lưu an toàn.",
            SourceFileNotFoundError: "File nguồn không tồn tại hoặc không đọc được.",
            ProjectAlreadyExistsError: "Dự án đích đã tồn tại.",
            ProjectPermissionError: "Không có quyền đọc hoặc ghi tại đường dẫn đã chọn.",
            ManifestMissingError: "Dự án thiếu file project.hms.json.",
            ManifestDecodeError: "Manifest dự án không phải JSON UTF-8 hợp lệ.",
            UnsupportedProjectFormatError: "Thư mục không thuộc định dạng HMS_PROJECT.",
            UnsupportedFormatVersionError: "Phiên bản dự án chưa được ứng dụng hỗ trợ.",
            DatabaseMissingError: "Dự án thiếu project.db.",
            ProjectDatabaseError: "Database dự án bị lỗi hoặc không thể mở.",
            GeometryTransferTargetError: "Dự án CAM không hợp lệ.",
            GeometryTransferIntegrityError: (
                "Dữ liệu 3D không còn nguyên vẹn hoặc không đủ điều kiện."
            ),
            GeometryTransferApplyError: (
                "Cập nhật thất bại, mô hình cũ được giữ nguyên."
            ),
            GeometryTransferRecoveryError: (
                "Phục hồi cập nhật 3D thất bại; dự án được giữ fail-closed."
            ),
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
        QTimer.singleShot(0, self.request_incoming_scan)

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
        self.message.emit("Đã tạo ảnh chụp tự động lưu.")

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
        workspace = self._service.current_workspace
        return (
            generation == self._autosave_generation
            and project_id == self._autosave_project_id
            and workspace is not None
            and workspace.identity == project_id
        )

    def _suspend_autosave_session(self) -> None:
        self._autosave_timer.stop()
        self._autosave_generation += 1
        self._autosave_project_id = None
        self._autosave_pending = False

    def _bind_autosave_session(self) -> None:
        workspace = self._service.current_workspace
        project_id = None if workspace is None else workspace.identity
        if project_id != self._autosave_project_id:
            self._autosave_generation += 1
            self._autosave_project_id = project_id
            self._autosave_pending = False
        if workspace is not None and not self.is_busy and not self.is_autosaving:
            self._autosave_timer.start()
        else:
            self._autosave_timer.stop()

    def _request_autosave_recovery(self, error: RecoveryRequiredError) -> None:
        response = QMessageBox.warning(
            self._window,
            "Phát hiện lần đóng bất thường",
            "Có ảnh chụp tự động lưu hợp lệ từ phiên bị đóng bất thường.\n\n"
            "Chọn Có để phục hồi, Không để mở dữ liệu chính, hoặc Hủy để dừng.",
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
        workspace = self._service.current_workspace
        has_workspace = workspace is not None
        is_document = (
            workspace is not None
            and workspace.mode is DocumentMode.CAD_DOCUMENT
        )
        operation_allowed = not self.is_busy and not self.is_autosaving
        self.actions["new"].setEnabled(operation_allowed)
        self.actions["open"].setEnabled(operation_allowed)
        self.actions["open_project"].setEnabled(operation_allowed)
        self.actions["new_from_document"].setEnabled(
            is_document and operation_allowed
        )
        self.actions["import"].setEnabled(
            self._service.has_project and operation_allowed
        )
        self.actions["save"].setEnabled(has_workspace and operation_allowed)
        self.actions["save_as"].setEnabled(is_document and operation_allowed)
        document = self._service.current_document
        self.actions["send_geometry"].setEnabled(
            is_document
            and workspace is not None
            and workspace.physical_path is not None
            and workspace.physical_path.suffix.casefold() == ".hms"
            and not workspace.dirty
            and document is not None
            and document.geometry_path.is_file()
            and operation_allowed
        )
        self.actions["close"].setEnabled(has_workspace and operation_allowed)

    @Slot(object)
    def _bind_inbox_monitor(self, _result: object = None) -> None:
        workspace = self._service.current_workspace
        project_id = (
            workspace.identity
            if workspace is not None
            and workspace.mode is DocumentMode.CAM_PROJECT
            else None
        )
        if project_id != self._monitored_project_id:
            self._incoming_scan_generation += 1
            self._monitored_project_id = project_id
            self._deferred_this_session.clear()
            self._incoming_requests = ()
        watched = self._inbox_watcher.directories()
        if watched:
            self._inbox_watcher.removePaths(watched)
        if project_id is None or workspace is None:
            self._inbox_timer.stop()
            self._incoming_requests = ()
            self.incoming_geometry_changed.emit(())
            return
        pending = (
            workspace.physical_path
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_PENDING_DIRECTORY
        )
        if pending.is_dir():
            self._inbox_watcher.addPath(str(pending))
        self._inbox_timer.start()
        QTimer.singleShot(0, self.request_incoming_scan)

    def _incoming_scan_succeeded(
        self,
        result: object,
        *,
        generation: int,
        project_id: UUID,
    ) -> None:
        workspace = self._service.current_workspace
        if (
            generation != self._incoming_scan_generation
            or workspace is None
            or workspace.mode is not DocumentMode.CAM_PROJECT
            or workspace.identity != project_id
            or not isinstance(result, tuple)
            or any(
                not isinstance(item, GeometryTransferRequest)
                for item in result
            )
        ):
            return
        visible = tuple(
            item
            for item in result
            if item.request_id not in self._deferred_this_session
        )
        self._incoming_requests = result
        self.incoming_geometry_changed.emit(visible)

    @staticmethod
    def _incoming_scan_failed(error: object) -> None:
        logger.warning(
            "Quét vùng chờ dữ liệu 3D thất bại",
            exc_info=(type(error), error, error.__traceback__)
            if isinstance(error, BaseException)
            else None,
        )

    def _incoming_scan_finished(self, task: ProjectTask) -> None:
        if self._incoming_scan_task is not task:
            return
        self._incoming_scan_task = None
        repeat = self._incoming_scan_pending
        self._incoming_scan_pending = False
        if repeat:
            QTimer.singleShot(0, self.request_incoming_scan)

    def _close_current_interactively(self) -> bool:
        workspace = self._service.current_workspace
        if workspace is None:
            return True
        if self._service.is_dirty:
            response = QMessageBox.warning(
                self._window,
                "Tài liệu chưa lưu",
                f"{workspace.mode.display_text} có thay đổi chưa lưu.",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if response == QMessageBox.StandardButton.Cancel:
                return False
            if response == QMessageBox.StandardButton.Save:
                if not self._save_current_for_lifecycle():
                    return False
            if not self._try_close_current(discard_changes=True):
                return False
        else:
            if not self._try_close_current(discard_changes=False):
                return False
        return True

    def _try_close_current(self, *, discard_changes: bool) -> bool:
        try:
            self._service.close_workspace(discard_changes=discard_changes)
        except ProjectError as error:
            self._show_error(error)
            return False
        self._suspend_autosave_session()
        return True

    def _prepare_workspace_replacement(self) -> bool:
        """Obtain Save/Discard/Cancel consent without closing before I/O succeeds."""
        workspace = self._service.current_workspace
        if workspace is None or not workspace.dirty:
            return True
        response = QMessageBox.warning(
            self._window,
            "Tài liệu chưa lưu",
            f"{workspace.mode.display_text} có thay đổi chưa lưu.",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if response == QMessageBox.StandardButton.Cancel:
            return False
        if response == QMessageBox.StandardButton.Save:
            return self._save_current_for_lifecycle()
        return True

    def _save_current_for_lifecycle(self) -> bool:
        workspace = self._service.current_workspace
        if workspace is None:
            return True
        try:
            if workspace.mode is DocumentMode.CAM_PROJECT:
                self._service.save()
                return True
            target = workspace.physical_path
            if target is None:
                suggestion = self._service.suggested_document_path()
                selected, _ = QFileDialog.getSaveFileName(
                    self._window,
                    "Lưu thành tài liệu HMS",
                    str(suggestion),
                    "Tài liệu HMS (*.HMS)",
                )
                if not selected:
                    return False
                target = Path(selected)
                target = target.with_name(ensure_hms_suffix(target.name))
            self._service.save_document(target)
            return True
        except ProjectError as error:
            self._show_error(error)
            return False
