"""Coordinate CAD actions, background import and viewport document ownership."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMainWindow

from hms_cadcam.cad.exceptions import CadDocumentNotFoundError
from hms_cadcam.ui.localized_dialogs import QFileDialog
from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.measurement import MeasurementResult, MeasurementService
from hms_cadcam.cad.measurement_factory import MeasurementServiceFactory
from hms_cadcam.cad.models import (
    CadDocumentId,
    CadDocumentKind,
    CadDocumentTree,
    CadDocumentMetadata,
    CadFormat,
    CadGeometryKind,
    CadImportResult,
    CadObjectId,
)
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectMap,
    PersistentXcafOccurrenceKey,
    build_persistent_object_map,
)
from hms_cadcam.project.cad_state import (
    CadViewState,
    ObjectAppearanceOverride,
    PersistentObjectAppearance,
    default_cad_view_state,
)
from hms_cadcam.project.exceptions import ProjectError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.workspace import DocumentOpenOrigin, PreparedDocumentOpen
from hms_cadcam.ui.cad_worker import CadImportTask
from hms_cadcam.ui.cad_loading import (
    CadLoadError,
    CadLoadErrorCode,
    CadLoadEvent,
    CadLoadOrigin,
    CadLoadState,
    CadLoadingCoordinator,
    cad_format_for_path,
    normalize_import_error,
)
from hms_cadcam.viewer.models import (
    DisplayMode,
    ObjectAppearance,
    ObjectColor,
    SelectionMetadata,
    SelectionMode,
    ViewDirection,
)
from hms_cadcam.viewer.widget import CadViewportWidget

logger = logging.getLogger(__name__)
_UNCHANGED = object()


class CadUiController(QObject):
    """Keep native import work outside UI and own the active document ID."""

    document_changed = Signal(object)
    message = Signal(str)
    progress_changed = Signal(str)
    busy_changed = Signal(bool)
    selection_changed = Signal(object)
    selection_context_changed = Signal(object, object)
    measurement_changed = Signal(object)
    measurement_context_changed = Signal(object, object)
    topology_tree_changed = Signal(object)
    object_selection_context_changed = Signal(object, object)
    appearance_context_changed = Signal(object, object)
    project_state_changed = Signal()
    workspace_changed = Signal(object)
    loading_state_changed = Signal(object)

    def __init__(
        self,
        window: QMainWindow,
        kernel: CadKernel,
        viewport: CadViewportWidget,
        measurement_service: MeasurementService | None = None,
        project_service: ProjectService | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._kernel = kernel
        self._viewport = viewport
        self._measurement_service = (
            measurement_service or MeasurementServiceFactory.create(kernel)
        )
        self._project_service = project_service
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._active_task: CadImportTask | None = None
        self._active_task_source_id: UUID | None = None
        self._active_task_prepared: PreparedDocumentOpen | None = None
        self._loading_coordinator = CadLoadingCoordinator(self._publish_loading_event)
        self._last_loading_event: CadLoadEvent | None = None
        self._open_command: Callable[[Path, DocumentOpenOrigin], bool] | None = None
        self._request_generation = 0
        self._active_document_id: CadDocumentId | None = None
        self._active_metadata: CadDocumentMetadata | None = None
        self._closing = False
        self._active_selection: tuple[SelectionMetadata, ...] = ()
        self._active_tree: CadDocumentTree | None = None
        self._selected_object_ids: tuple[CadObjectId, ...] = ()
        self._appearances: dict[CadObjectId, ObjectAppearance] = {}
        self._base_appearances: dict[CadObjectId, ObjectAppearance] = {}
        self._user_overrides: dict[CadObjectId, ObjectAppearanceOverride] = {}
        self._isolate_snapshot: dict[CadObjectId, bool] | None = None
        self._active_source_id: UUID | None = None
        self._persistent_map: PersistentCadObjectMap | None = None
        self._display_mode = DisplayMode.SHADED_WITH_EDGES
        self._view_direction = ViewDirection.ISOMETRIC
        self._vertex_pair: tuple[str, ...] = ()
        self.actions = self._create_actions()
        self._update_action_states()

    @property
    def active_document_id(self) -> CadDocumentId | None:
        return self._active_document_id

    @property
    def active_metadata(self) -> CadDocumentMetadata | None:
        return self._active_metadata

    @property
    def is_busy(self) -> bool:
        return self._active_task is not None

    @property
    def vertex_pair(self) -> tuple[str, ...]:
        """Return the current OCP-free vertex pair used for distance."""
        return self._vertex_pair

    @property
    def active_tree(self) -> CadDocumentTree | None:
        return self._active_tree

    @property
    def active_source_id(self) -> UUID | None:
        """Return the project source bound to the active CAD document."""
        return self._active_source_id

    @property
    def active_selection(self) -> tuple[SelectionMetadata, ...]:
        """Expose immutable native-free viewer selection to application adapters."""
        return self._active_selection

    @property
    def persistent_object_map(self) -> PersistentCadObjectMap | None:
        """Expose the safe runtime-to-persistent mapping, never native geometry."""
        return self._persistent_map

    @property
    def appearances(self) -> tuple[tuple[CadObjectId, ObjectAppearance], ...]:
        return tuple(self._appearances.items())

    @property
    def display_mode(self) -> DisplayMode:
        return self._display_mode

    @property
    def view_direction(self) -> ViewDirection:
        return self._view_direction

    @Slot(object)
    def handle_selection(self, items: object) -> None:
        """Measure valid selection IDs without exposing native shapes to UI."""
        source_document_id = self._active_document_id
        if isinstance(items, tuple) and items:
            first_item = items[0]
            if isinstance(first_item, SelectionMetadata):
                source_document_id = first_item.document_id
        self._handle_selection_event(source_document_id, items)

    @Slot(object, object)
    def handle_selection_event(
        self,
        source_document_id: object,
        items: object,
    ) -> None:
        """Ignore queued selection emitted for a document that is no longer active."""
        if source_document_id is not None and not isinstance(
            source_document_id, CadDocumentId
        ):
            return
        self._handle_selection_event(source_document_id, items)

    def _handle_selection_event(
        self,
        source_document_id: CadDocumentId | None,
        items: object,
    ) -> None:
        if not isinstance(items, tuple) or not all(
            isinstance(item, SelectionMetadata) for item in items
        ):
            return
        document_id = self._active_document_id
        metadata = self._active_metadata
        if source_document_id != document_id:
            return
        if not items:
            self._active_selection = ()
            self._selected_object_ids = ()
            self._reset_vertex_pair()
            self._emit_selection(())
            self._emit_measurements(())
            self.object_selection_context_changed.emit(document_id, ())
            return
        if (
            document_id is None
            or metadata is None
            or metadata.geometry_kind is not CadGeometryKind.BREP
            or any(item.document_id != document_id for item in items)
        ):
            return
        self._active_selection = items
        object_ids = tuple(
            dict.fromkeys(
                item.object_id for item in items if item.object_id is not None
            )
        )
        self._selected_object_ids = object_ids
        self.object_selection_context_changed.emit(document_id, object_ids)
        self._emit_selection(items)
        if all(item.topology is SelectionMode.VERTEX for item in items):
            self._vertex_pair = tuple(item.selection_id for item in items[:2])
        else:
            self._reset_vertex_pair()
        self._measure_current_selection()

    @Slot()
    def choose_step(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self._window,
            "Mở STEP/STP",
            "",
            "STEP (*.step *.stp)",
        )
        if path:
            self.open_path(Path(path))

    @Slot()
    def choose_brep(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self._window,
            "Mở BREP",
            "",
            "BREP (*.brep *.brp)",
        )
        if path:
            self.open_path(Path(path))

    @Slot()
    def choose_iges(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self._window,
            "Mở IGES/IGS",
            "",
            "IGES (*.iges *.igs)",
        )
        if path:
            self.open_path(Path(path))

    @Slot()
    def choose_stl(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self._window,
            "Mở STL",
            "",
            "STL (*.stl)",
        )
        if path:
            self.open_path(Path(path))

    def set_open_command(
        self,
        command: Callable[[Path, DocumentOpenOrigin], bool] | None,
    ) -> None:
        """Use one application Open command for dialogs and drag/drop."""
        self._open_command = command

    def open_path(
        self,
        path: Path,
        *,
        origin: CadLoadOrigin = CadLoadOrigin.OPEN_DIALOG,
    ) -> bool:
        """Route a selected path through the shared command when configured."""
        if self._open_command is not None:
            return bool(self._open_command(path, _document_open_origin(origin)))
        cad_format = cad_format_for_path(path)
        if cad_format is None:
            self._loading_coordinator.reject_unsupported(path)
            return False
        self.start_import(path, cad_format, origin=origin)
        return True

    def open_dropped_path(self, path: Path) -> bool:
        """Use the same Open command as dialogs while retaining drag/drop origin."""

        return self.open_path(path, origin=CadLoadOrigin.DRAG_DROP)

    @Slot(object)
    def open_prepared_document(self, prepared: object) -> None:
        """Start the existing importer for a validated standalone open request."""
        if not isinstance(prepared, PreparedDocumentOpen):
            return
        origin = _cad_load_origin(prepared.origin)
        cad_format = cad_format_for_path(prepared.session.geometry_path)
        if cad_format is None:
            self._loading_coordinator.reject_unsupported(prepared.session.geometry_path)
            self.message.emit("Định dạng hình học trong tài liệu HMS chưa được hỗ trợ.")
            return
        self.start_import(
            prepared.session.geometry_path,
            cad_format,
            source_id=prepared.session.state.identity,
            prepared=prepared,
            origin=origin,
        )

    def start_import(
        self,
        source_path: str | Path,
        cad_format: CadFormat,
        *,
        source_id: UUID | None = None,
        prepared: PreparedDocumentOpen | None = None,
        origin: CadLoadOrigin = CadLoadOrigin.OPEN_DIALOG,
    ) -> None:
        """Start or supersede one background CAD import request."""
        if self._closing:
            return
        source = Path(source_path)
        if not self._kernel.is_available():
            self._loading_coordinator.reject_backend_unavailable(
                source, origin, cad_format,
                owner_identity=str(source_id) if source_id is not None else "transient",
            )
            self._invalidate_active_task()
            self._update_action_states()
            self.progress_changed.emit("Lỗi")
            self.message.emit("Backend CAD hiện không khả dụng.")
            return
        request, _superseded = self._loading_coordinator.begin(
            source, origin, cad_format,
            owner_identity=str(source_id) if source_id is not None else "transient",
        )
        self._invalidate_active_task()
        self._request_generation = request.request_id
        task = CadImportTask(self._kernel, request.request_id, source, cad_format)
        task.signals.progress.connect(self._show_progress)
        task.signals.completed.connect(self._finish_import)
        task.signals.failed.connect(self._import_failed)
        self._active_task = task
        self._active_task_source_id = source_id
        self._active_task_prepared = prepared
        self.progress_changed.emit("Đang đọc")
        self.message.emit(f"Đang nhập CAD: {source}")
        self._update_action_states()
        self.busy_changed.emit(True)
        self._thread_pool.start(task)

    def bind_project(
        self,
        source_path: Path | None,
        *,
        source_id: UUID | None = None,
    ) -> None:
        """Invalidate old work, clear the document and optionally load project CAD."""
        if self._closing:
            return
        self._request_generation += 1
        self._loading_coordinator.abandon_active()
        self._invalidate_active_task()
        self._clear_active_document()
        if source_path is not None:
            cad_format = cad_format_for_path(source_path)
            if cad_format is not None:
                self.start_import(source_path, cad_format, source_id=source_id)

    def shutdown(self) -> None:
        """Detach workers and release the active document before window teardown."""
        if self._closing:
            return
        self._closing = True
        self._request_generation += 1
        self._loading_coordinator.abandon_active()
        self._invalidate_active_task()
        self._thread_pool.clear()
        self._clear_active_document()
        self._update_action_states()

    @Slot(int, str)
    def _show_progress(self, request_id: int, status: str) -> None:
        if not self._closing and self._loading_coordinator.is_active(request_id):
            self.progress_changed.emit(status)

    @Slot(int, object)
    def _finish_import(self, request_id: int, result: object) -> None:
        if not isinstance(result, CadImportResult):
            return
        task = self._active_task
        if task is None or task.request_id != request_id:
            return
        if not task.acknowledge(result):
            return
        if self._closing or not self._loading_coordinator.is_active(request_id):
            self._release_result(result)
            return
        candidate_source_id = self._active_task_source_id
        prepared = self._active_task_prepared
        self._active_task = None
        self._active_task_source_id = None
        self._active_task_prepared = None
        self.busy_changed.emit(False)
        self._update_action_states()
        if not result.success or result.document_id is None or result.metadata is None:
            self._discard_prepared(prepared)
            error = "; ".join(result.errors) or "Không thể đọc file CAD"
            self.progress_changed.emit("Lỗi")
            self.message.emit(f"Lỗi nhập CAD: {error}")
            self._fail_active_request(request_id, error)
            return
        old_document_id = self._active_document_id
        try:
            tree = self._get_document_tree(result.document_id)
        except (ProjectError, TypeError, ValueError):
            logger.exception("Không thể tạo topology tree cho CAD document")
            self._release_result(result)
            self._discard_prepared(prepared)
            self.progress_changed.emit("Lỗi")
            self.message.emit("Lỗi cây cấu trúc hình học; giữ nguyên tài liệu hiện tại.")
            self._fail_active_request(request_id, "topology tree creation failed")
            return
        if not self._viewport.display_document(result.document_id):
            self._release_result(result)
            self._discard_prepared(prepared)
            self.progress_changed.emit("Lỗi")
            self.message.emit("Lỗi hiển thị CAD; giữ nguyên tài liệu hiện tại.")
            self._fail_active_request(request_id, "viewport display failed")
            return
        committed_workspace = None
        if prepared is not None:
            if self._project_service is None:
                self._release_result(result)
                self._discard_prepared(prepared)
                self.progress_changed.emit("Lỗi")
                self.message.emit(
                    "Không có application service để hoàn tất mở tài liệu."
                )
                self._fail_active_request(request_id, "project service unavailable")
                return
            try:
                committed_workspace = self._project_service.commit_document_open(
                    prepared
                )
                self._project_service.record_document_geometry_metadata(
                    _transfer_metadata(result.metadata)
                )
            except ProjectError:
                logger.exception("Không thể commit tài liệu HMS sau import")
                self._release_result(result)
                self._discard_prepared(prepared)
                if old_document_id is not None:
                    self._viewport.display_document(old_document_id)
                self.progress_changed.emit("Lỗi")
                self.message.emit(
                    "Mở tài liệu thất bại; giữ nguyên tài liệu hiện tại."
                )
                self._fail_active_request(request_id, "project document commit failed")
                return
        self._active_document_id = result.document_id
        self._active_metadata = result.metadata
        self._active_tree = tree
        self._base_appearances = (
            {
                node.object_id: _source_object_appearance(node.source_appearance)
                for node in tree.root.walk()
            }
            if tree is not None
            else {}
        )
        self._appearances = dict(self._base_appearances)
        self._user_overrides = {}
        self._isolate_snapshot = None
        self._active_source_id = candidate_source_id
        self._persistent_map = (
            build_persistent_object_map(
                candidate_source_id,
                result.metadata.geometry_kind,
                tree,
            )
            if candidate_source_id is not None and tree is not None
            else None
        )
        if self._persistent_map is not None and self._persistent_map.ambiguous_nodes:
            logger.warning(
                "Bỏ qua %d topology node mơ hồ, không đủ chắc chắn để persistence",
                self._persistent_map.ambiguous_nodes,
            )
        restored = self._load_project_view_state(result.metadata.geometry_kind)
        if not self._apply_persisted_state(restored):
            self.message.emit(
                "Không thể khôi phục trọn vẹn trạng thái hiển thị CAD; "
                "giữ trạng thái trình xem trước khi áp dụng."
            )
        self._selected_object_ids = ()
        self._active_selection = ()
        self._reset_vertex_pair()
        self._emit_selection(())
        self._emit_measurements(())
        if old_document_id is not None and old_document_id != result.document_id:
            self._release_document(old_document_id)
        if not self._loading_coordinator.succeed(request_id):
            return
        self.progress_changed.emit("Hoàn thành")
        self.message.emit(f"Đã hiển thị CAD: {result.source_path}")
        self.document_changed.emit(result.metadata)
        self.topology_tree_changed.emit(tree)
        self._emit_appearances()
        self._measure_active_document()
        self._update_action_states()
        if committed_workspace is not None:
            self.workspace_changed.emit(committed_workspace)

    @Slot(int, object)
    def _import_failed(self, request_id: int, error: object) -> None:
        task = self._active_task
        request = self._loading_coordinator.active_request
        if (
            self._closing or task is None or request is None
            or task.request_id != request_id or request.request_id != request_id
            or not self._loading_coordinator.fail(request, normalize_import_error(error))
        ):
            return
        prepared = self._active_task_prepared
        self._active_task = None
        self._active_task_source_id = None
        self._active_task_prepared = None
        self._discard_prepared(prepared)
        self.busy_changed.emit(False)
        self._update_action_states()
        logger.error(
            "Worker nhập CAD thất bại",
            exc_info=(type(error), error, error.__traceback__)
            if isinstance(error, BaseException)
            else None,
        )
        self.progress_changed.emit("Lỗi")
        self.message.emit("Lỗi nhập CAD: tác vụ nền không thể hoàn thành.")

    def cancel_active_import(self) -> bool:
        """Cancel public ownership once and abandon non-cooperative native work."""
        request = self._loading_coordinator.cancel_active()
        if request is None:
            return False
        self._invalidate_active_task()
        self._update_action_states()
        return True

    def _publish_loading_event(self, event: CadLoadEvent) -> None:
        """Expose request-owned lifecycle state without native geometry."""
        self._last_loading_event = event
        self.loading_state_changed.emit(event)
        if event.state is CadLoadState.LOADING:
            self.progress_changed.emit("Đang đọc")
        elif event.state is CadLoadState.CANCELLED:
            self.progress_changed.emit("Đã hủy")
        elif event.state is CadLoadState.FAILED:
            self.progress_changed.emit("Lỗi")
            if isinstance(event.error, CadLoadError):
                logger.info("CAD loading failure category: %s", event.error.code.value)

    def _fail_active_request(self, request_id: int, cause: str) -> None:
        request = self._loading_coordinator.active_request
        if request is None or request.request_id != request_id:
            return
        self._loading_coordinator.fail(
            request,
            CadLoadError(CadLoadErrorCode.IMPORTER_FAILURE, "Trình nhập CAD không thể hoàn thành yêu cầu.", cause),
        )

    def _create_actions(self) -> dict[str, QAction]:
        definitions = {
            "open_step": ("Mở STEP/STP", self.choose_step),
            "open_brep": ("Mở BREP", self.choose_brep),
            "open_iges": ("Mở IGES/IGS", self.choose_iges),
            "open_stl": ("Mở STL", self.choose_stl),
            "fit_all": ("Hiện toàn bộ", self._viewport.fit_all),
            "measurement": ("Đo BREP", self._measure_current_selection),
        }
        actions: dict[str, QAction] = {}
        for key, (text, slot) in definitions.items():
            action = QAction(text, self)
            action.setObjectName(f"Cad{key.title().replace('_', '')}Action")
            action.triggered.connect(slot)
            actions[key] = action
        for direction in ViewDirection:
            key = f"view_{direction.value}"
            action = QAction(
                {
                    "top": "Trên",
                    "bottom": "Dưới",
                    "front": "Trước",
                    "back": "Sau",
                    "left": "Trái",
                    "right": "Phải",
                    "isometric": "Trục đo",
                }[direction.value],
                self,
            )
            action.setObjectName(f"CadView{direction.value.title()}Action")
            action.triggered.connect(
                lambda _checked=False, value=direction: self.set_view_direction(value)
            )
            actions[key] = action
        display_group = QActionGroup(self)
        display_group.setExclusive(True)
        for mode in DisplayMode:
            key = f"display_{mode.value}"
            action = QAction(_display_label(mode), self, checkable=True)
            action.setChecked(mode is DisplayMode.SHADED_WITH_EDGES)
            action.setObjectName(f"CadDisplay{mode.name.title()}Action")
            action.triggered.connect(
                lambda _checked=False, value=mode: self.set_display_mode(value)
            )
            display_group.addAction(action)
            actions[key] = action
        selection_group = QActionGroup(self)
        selection_group.setExclusive(True)
        for mode in SelectionMode:
            key = f"selection_{mode.value}"
            label = {
                "solid": "khối rắn",
                "face": "bề mặt",
                "wire": "chuỗi",
                "edge": "cạnh",
                "vertex": "đỉnh",
            }[mode.value]
            action = QAction(f"Chọn {label}", self, checkable=True)
            action.setChecked(mode is SelectionMode.SOLID)
            action.setObjectName(f"CadSelection{mode.value.title()}Action")
            action.triggered.connect(
                lambda _checked=False, value=mode: self._set_selection_mode(value)
            )
            selection_group.addAction(action)
            actions[key] = action
        return actions

    def _update_action_states(self) -> None:
        available = self._kernel.is_available() and not self._closing
        for key, action in self.actions.items():
            if key.startswith("open_"):
                action.setEnabled(available and not self.is_busy)
            elif key.startswith("selection_"):
                action.setEnabled(
                    available
                    and self._active_document_id is not None
                    and self._active_metadata is not None
                    and self._active_metadata.geometry_kind is CadGeometryKind.BREP
                )
            elif key == "measurement":
                action.setEnabled(
                    available
                    and self._active_document_id is not None
                    and self._active_metadata is not None
                    and self._active_metadata.geometry_kind is CadGeometryKind.BREP
                )
            else:
                action.setEnabled(available and self._active_document_id is not None)

    def _invalidate_active_task(self) -> None:
        task = self._active_task
        self._active_task = None
        self._active_task_source_id = None
        self._active_task_prepared = None
        if task is None:
            return
        task.abandon()
        for signal, slot in (
            (task.signals.progress, self._show_progress),
            (task.signals.completed, self._finish_import),
            (task.signals.failed, self._import_failed),
        ):
            try:
                signal.disconnect(slot)
            except RuntimeError:
                logger.debug("CAD worker signal was already disconnected")
        self.busy_changed.emit(False)

    def _clear_active_document(self) -> None:
        document_id = self._active_document_id
        self._active_document_id = None
        self._active_metadata = None
        self._active_tree = None
        self._appearances = {}
        self._base_appearances = {}
        self._user_overrides = {}
        self._isolate_snapshot = None
        self._active_source_id = None
        self._persistent_map = None
        self._selected_object_ids = ()
        self._active_selection = ()
        self._reset_vertex_pair()
        self._viewport.clear()
        if document_id is not None:
            self._release_document(document_id)
        self.document_changed.emit(None)
        self.topology_tree_changed.emit(None)
        self.object_selection_context_changed.emit(None, ())
        self.appearance_context_changed.emit(None, ())
        self._emit_measurements(())
        self.progress_changed.emit("Sẵn sàng")
        self._update_action_states()

    def _release_result(self, result: CadImportResult) -> None:
        if result.document_id is not None:
            self._release_document(result.document_id)

    def _discard_prepared(
        self,
        prepared: PreparedDocumentOpen | None,
    ) -> None:
        if prepared is None or self._project_service is None:
            return
        try:
            self._project_service.discard_document_open(prepared)
        except ProjectError:
            logger.warning(
                "Không thể dọn vùng tạm của tài liệu mở thất bại",
                exc_info=True,
            )

    def _release_document(self, document_id: CadDocumentId) -> None:
        try:
            self._kernel.release_document(document_id)
        except CadDocumentNotFoundError:
            logger.debug("CAD document đã được release: %s", document_id)
        except Exception:
            logger.exception("Không thể release CAD document %s", document_id)

    def _measure_current_selection(self) -> None:
        document_id = self._active_document_id
        if document_id is None or not self._active_selection:
            return
        results: list[MeasurementResult] = []
        try:
            results.append(
                self._measurement_service.measure_selection(
                    document_id,
                    self._active_selection[0].selection_id,
                )
            )
            if len(self._vertex_pair) == 2:
                results.append(
                    self._measurement_service.measure_distance(
                        document_id,
                        self._vertex_pair[0],
                        self._vertex_pair[1],
                    )
                )
        except (RuntimeError, TypeError, ValueError):
            logger.exception("Không thể đo selection BREP")
            self.message.emit(
                "Không thể đo lựa chọn BRep; lựa chọn hiện tại được giữ nguyên."
            )
            self._emit_measurements(())
            return
        self._emit_measurements(tuple(results))

    def _set_selection_mode(self, mode: SelectionMode) -> None:
        self._active_selection = ()
        self._selected_object_ids = ()
        self._reset_vertex_pair()
        self._emit_selection(())
        self._emit_measurements(())
        self._viewport.set_selection_mode(mode)

    def set_view_direction(self, direction: ViewDirection) -> bool:
        """Apply and stage one standard view direction after viewer success."""
        if self._active_document_id is None or not isinstance(direction, ViewDirection):
            return False
        previous = self._view_direction
        if direction is previous:
            return True
        if not self._viewport.set_view_direction(direction):
            self._viewport.set_view_direction(previous)
            return False
        self._view_direction = direction
        if not self._stage_current_state():
            self._viewport.set_view_direction(previous)
            self._view_direction = previous
            return False
        return True

    def set_display_mode(self, mode: DisplayMode) -> bool:
        """Apply and stage one display mode after viewer success."""
        if self._active_document_id is None or not isinstance(mode, DisplayMode):
            return False
        previous = self._display_mode
        if mode is previous:
            return True
        if not self._viewport.set_display_mode(mode):
            self._viewport.set_display_mode(previous)
            self.actions[f"display_{previous.value}"].setChecked(True)
            return False
        self._display_mode = mode
        if not self._stage_current_state():
            self._viewport.set_display_mode(previous)
            self._display_mode = previous
            self.actions[f"display_{previous.value}"].setChecked(True)
            return False
        self.actions[f"display_{mode.value}"].setChecked(True)
        return True

    @Slot(object, object)
    def select_tree_objects(self, document_id: object, object_ids: object) -> None:
        """Synchronize a guarded tree selection into the active viewport."""
        if document_id != self._active_document_id or not isinstance(object_ids, tuple):
            return
        if not all(isinstance(item, CadObjectId) for item in object_ids):
            return
        if any(not self._has_object(item) for item in object_ids):
            return
        if not self._viewport.select_objects(document_id, object_ids):
            return
        self._selected_object_ids = object_ids
        self._active_selection = ()
        self._reset_vertex_pair()
        self._emit_selection(())
        self._emit_measurements(())
        self.object_selection_context_changed.emit(document_id, object_ids)

    def set_object_visibility(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        visible: bool,
    ) -> bool:
        """Apply parent-recursive visibility and stage it after viewer success."""
        if not self._valid_object_request(document_id, object_id):
            return False
        previous_appearances = dict(self._appearances)
        previous_overrides = dict(self._user_overrides)
        if not self._viewport.set_object_visibility(document_id, object_id, visible):
            return False
        affected = set(self._descendant_ids(object_id))
        for affected_id in affected:
            current = self._appearances[affected_id]
            self._appearances[affected_id] = ObjectAppearance(
                visible=visible,
                color=current.color,
                transparency=current.transparency,
            )
            self._update_xcaf_override(affected_id, visible=visible)
        self._refresh_container_visibility()
        selected_topology_hidden = any(
            item.object_id in affected for item in self._active_selection
        )
        affected_leaf_ids = set(self._presentation_ids(object_id))
        selected_tree_leaf_ids = {
            leaf_id
            for selected_id in self._selected_object_ids
            for leaf_id in self._presentation_ids(selected_id)
        }
        selected_tree_hidden = bool(
            affected_leaf_ids.intersection(selected_tree_leaf_ids)
        )
        if not visible and (selected_topology_hidden or selected_tree_hidden):
            self._active_selection = ()
            self._selected_object_ids = ()
            self._reset_vertex_pair()
            self._emit_selection(())
            self._emit_measurements(())
            self.object_selection_context_changed.emit(document_id, ())
        if not self._stage_current_state():
            self._restore_runtime_appearances(previous_appearances, affected)
            self._appearances = previous_appearances
            self._user_overrides = previous_overrides
            return False
        self._emit_appearances()
        return True

    def isolate_object(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> bool:
        """Keep one isolate target while preserving the pre-isolate snapshot."""
        if not self._valid_object_request(document_id, object_id):
            return False
        if not self._viewport.isolate_object(document_id, object_id):
            return False
        if self._isolate_snapshot is None:
            self._isolate_snapshot = {
                item_id: appearance.visible
                for item_id, appearance in self._appearances.items()
            }
        else:
            for item_id, visible in self._isolate_snapshot.items():
                current = self._appearances[item_id]
                self._appearances[item_id] = ObjectAppearance(
                    visible=visible,
                    color=current.color,
                    transparency=current.transparency,
                )
        visible_leaf_ids = set(self._presentation_ids(object_id))
        for node_id, current in tuple(self._appearances.items()):
            node = self._active_tree.find(node_id) if self._active_tree else None
            if node is not None and node.has_presentation:
                self._appearances[node_id] = ObjectAppearance(
                    visible=node_id in visible_leaf_ids,
                    color=current.color,
                    transparency=current.transparency,
                )
        self._refresh_container_visibility()
        selected_leaf_ids = {
            leaf_id
            for selected_id in self._selected_object_ids
            for leaf_id in self._presentation_ids(selected_id)
        }
        if selected_leaf_ids and not selected_leaf_ids.issubset(visible_leaf_ids):
            self._selected_object_ids = ()
            self._active_selection = ()
            self._emit_selection(())
            self._emit_measurements(())
            self.object_selection_context_changed.emit(document_id, ())
        self._emit_appearances()
        return True

    def reset_isolate(self, document_id: CadDocumentId) -> bool:
        if document_id != self._active_document_id or self._isolate_snapshot is None:
            return False
        if not self._viewport.reset_isolate(document_id):
            return False
        for item_id, visible in self._isolate_snapshot.items():
            current = self._appearances[item_id]
            self._appearances[item_id] = ObjectAppearance(
                visible=visible,
                color=current.color,
                transparency=current.transparency,
            )
        self._isolate_snapshot = None
        self._emit_appearances()
        return True

    def set_object_color(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        color: ObjectColor,
    ) -> bool:
        if not self._valid_object_request(document_id, object_id):
            return False
        if not isinstance(color, ObjectColor):
            return False
        previous_appearances = dict(self._appearances)
        previous_overrides = dict(self._user_overrides)
        if not self._viewport.set_object_color(document_id, object_id, color):
            return False
        for affected_id in self._descendant_ids(object_id):
            current = self._appearances[affected_id]
            self._appearances[affected_id] = ObjectAppearance(
                visible=current.visible,
                color=color,
                transparency=current.transparency,
            )
            self._update_xcaf_override(affected_id, color=color)
        if not self._stage_current_state():
            self._restore_runtime_appearances(
                previous_appearances,
                set(self._descendant_ids(object_id)),
            )
            self._appearances = previous_appearances
            self._user_overrides = previous_overrides
            return False
        self._emit_appearances()
        return True

    def set_object_transparency(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
        transparency: float,
    ) -> bool:
        if not self._valid_object_request(document_id, object_id):
            return False
        try:
            validated = ObjectAppearance(transparency=transparency).transparency
        except (TypeError, ValueError):
            return False
        previous_appearances = dict(self._appearances)
        previous_overrides = dict(self._user_overrides)
        if not self._viewport.set_object_transparency(
            document_id,
            object_id,
            validated,
        ):
            return False
        for affected_id in self._descendant_ids(object_id):
            current = self._appearances[affected_id]
            self._appearances[affected_id] = ObjectAppearance(
                visible=current.visible,
                color=current.color,
                transparency=validated,
            )
            self._update_xcaf_override(
                affected_id, transparency=validated
            )
        if not self._stage_current_state():
            self._restore_runtime_appearances(
                previous_appearances,
                set(self._descendant_ids(object_id)),
            )
            self._appearances = previous_appearances
            self._user_overrides = previous_overrides
            return False
        self._emit_appearances()
        return True

    def reset_object_appearance(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> bool:
        """Reset user color/transparency overrides to each source baseline."""
        if not self._valid_object_request(document_id, object_id):
            return False
        previous_appearances = dict(self._appearances)
        previous_overrides = dict(self._user_overrides)
        if not self._viewport.reset_object_appearance(document_id, object_id):
            return False
        for affected_id in self._descendant_ids(object_id):
            current = self._appearances[affected_id]
            base = self._base_appearances.get(affected_id, ObjectAppearance())
            self._appearances[affected_id] = ObjectAppearance(
                visible=current.visible,
                color=base.color,
                transparency=base.transparency,
            )
            self._update_xcaf_override(
                affected_id,
                color=None,
                transparency=None,
            )
        if not self._stage_current_state():
            self._restore_runtime_appearances(
                previous_appearances,
                set(self._descendant_ids(object_id)),
            )
            self._appearances = previous_appearances
            self._user_overrides = previous_overrides
            return False
        self._emit_appearances()
        return True

    def _measure_active_document(self) -> None:
        document_id = self._active_document_id
        metadata = self._active_metadata
        if (
            document_id is None
            or metadata is None
            or metadata.geometry_kind is not CadGeometryKind.BREP
        ):
            self._emit_measurements(())
            return
        try:
            result = self._measurement_service.measure_document(document_id)
        except (RuntimeError, TypeError, ValueError):
            logger.exception("Không thể đo bounding dimensions của document BREP")
            return
        self._emit_measurements((result,))

    def _emit_selection(self, items: tuple[SelectionMetadata, ...]) -> None:
        self.selection_changed.emit(items)
        self.selection_context_changed.emit(self._active_document_id, items)

    def _emit_measurements(
        self,
        results: tuple[MeasurementResult, ...],
    ) -> None:
        self.measurement_changed.emit(results)
        self.measurement_context_changed.emit(self._active_document_id, results)

    def _reset_vertex_pair(self) -> None:
        self._vertex_pair = ()

    def _get_document_tree(
        self,
        document_id: CadDocumentId,
    ) -> CadDocumentTree | None:
        getter = getattr(self._kernel, "get_document_tree", None)
        if getter is None:
            return None
        tree = getter(document_id)
        if not isinstance(tree, CadDocumentTree) or tree.document_id != document_id:
            raise TypeError("CAD kernel returned an invalid document tree")
        return tree

    def _has_object(self, object_id: CadObjectId) -> bool:
        return self._active_tree is not None and self._active_tree.find(object_id) is not None

    def _valid_object_request(
        self,
        document_id: CadDocumentId,
        object_id: CadObjectId,
    ) -> bool:
        return document_id == self._active_document_id and self._has_object(object_id)

    def _descendant_ids(self, object_id: CadObjectId) -> tuple[CadObjectId, ...]:
        if self._active_tree is None:
            return ()
        node = self._active_tree.find(object_id)
        return tuple(item.object_id for item in node.walk()) if node else ()

    def _presentation_ids(self, object_id: CadObjectId) -> tuple[CadObjectId, ...]:
        if self._active_tree is None:
            return ()
        node = self._active_tree.find(object_id)
        return (
            tuple(item.object_id for item in node.walk() if item.has_presentation)
            if node
            else ()
        )

    def _refresh_container_visibility(self) -> None:
        if self._active_tree is None:
            return
        for node in reversed(self._active_tree.root.walk()):
            if not node.children:
                continue
            visible = any(
                self._appearances[item.object_id].visible
                for child in node.children
                for item in child.walk()
                if item.has_presentation
            )
            current = self._appearances[node.object_id]
            self._appearances[node.object_id] = ObjectAppearance(
                visible=visible,
                color=current.color,
                transparency=current.transparency,
            )

    def _emit_appearances(self) -> None:
        self.appearance_context_changed.emit(
            self._active_document_id,
            tuple(self._appearances.items()),
        )

    def _load_project_view_state(
        self,
        geometry_kind: CadGeometryKind,
    ) -> CadViewState | None:
        source_id = self._active_source_id
        if source_id is None or self._project_service is None:
            return None
        try:
            state = self._project_service.cad_view_state(source_id)
        except (RuntimeError, TypeError, ValueError):
            logger.exception("Không thể đọc CAD view state từ project service")
            return None
        if any(
            item.key.geometry_kind is not geometry_kind
            for item in state.object_appearances
        ):
            logger.warning(
                "CAD view state chứa geometry_kind không khớp; các row đó sẽ bị bỏ qua"
            )
        return state

    def _apply_persisted_state(self, state: CadViewState | None) -> bool:
        document_id = self._active_document_id
        metadata = self._active_metadata
        tree = self._active_tree
        if document_id is None or metadata is None or tree is None:
            return state is None
        desired_mode = state.display_mode if state is not None else DisplayMode.SHADED_WITH_EDGES
        desired_direction = (
            state.view_direction if state is not None else ViewDirection.ISOMETRIC
        )
        desired = {
            node.object_id: self._base_appearances.get(
                node.object_id, ObjectAppearance()
            )
            for node in tree.root.walk()
        }
        loaded_overrides: dict[CadObjectId, ObjectAppearanceOverride] = {}
        mapping = self._persistent_map
        if state is not None:
            for item in state.object_appearances:
                key = item.key
                if key.source_id != self._active_source_id:
                    logger.warning("Bỏ qua CAD appearance do source_id không khớp")
                    continue
                if key.geometry_kind is not metadata.geometry_kind:
                    logger.warning("Bỏ qua CAD appearance do geometry_kind không khớp")
                    continue
                object_id = mapping.by_persistent.get(key) if mapping is not None else None
                if object_id is None:
                    if hasattr(key, "topology_path"):
                        logger.warning(
                            "Bỏ qua CAD appearance do topology path stale/missing: %s",
                            _persistent_path_text(key),
                        )
                    else:
                        logger.warning(
                            "Bỏ qua CAD appearance do persistent path stale/missing: %s",
                            _persistent_path_text(key),
                        )
                    continue
                if isinstance(item.appearance, ObjectAppearanceOverride):
                    loaded_overrides[object_id] = item.appearance
                    desired[object_id] = item.appearance.apply(
                        self._base_appearances.get(object_id, ObjectAppearance())
                    )
                else:
                    desired[object_id] = item.appearance
        operations: list[tuple[Callable[[], bool], Callable[[], bool]]] = [
            (
                lambda: self._viewport.set_display_mode(desired_mode),
                lambda: self._viewport.set_display_mode(self._display_mode),
            ),
            (
                lambda: self._viewport.set_view_direction(desired_direction),
                lambda: self._viewport.set_view_direction(self._view_direction),
            ),
        ]
        default = ObjectAppearance()
        for object_id, appearance in desired.items():
            if state is None:
                continue
            node = tree.find(object_id)
            if node is None or not node.has_presentation:
                continue
            override = loaded_overrides.get(object_id)
            if override is not None:
                if override.color is not None:
                    operations.append(
                        (
                            lambda oid=object_id, value=override.color: self._viewport.set_object_color(
                                document_id, oid, value
                            ),
                            lambda oid=object_id: self._viewport.reset_object_appearance(
                                document_id, oid
                            ),
                        )
                    )
                if override.transparency is not None:
                    operations.append(
                        (
                            lambda oid=object_id, value=override.transparency: self._viewport.set_object_transparency(
                                document_id, oid, value
                            ),
                            lambda oid=object_id: self._viewport.reset_object_appearance(
                                document_id, oid
                            ),
                        )
                    )
                if override.visible is not None:
                    operations.append(
                        (
                            lambda oid=object_id, value=override.visible: self._viewport.set_object_visibility(
                                document_id, oid, value
                            ),
                            lambda oid=object_id: self._viewport.set_object_visibility(
                                document_id, oid, True
                            ),
                        )
                    )
                continue
            if node.occurrence_id is not None:
                continue
            if appearance == default:
                continue
            if appearance.color != default.color:
                operations.append(
                    (
                        lambda oid=object_id, value=appearance.color: self._viewport.set_object_color(
                            document_id, oid, value
                        ),
                        lambda oid=object_id: self._viewport.set_object_color(
                            document_id, oid, default.color
                        ),
                    )
                )
            if appearance.transparency != default.transparency:
                operations.append(
                    (
                        lambda oid=object_id, value=appearance.transparency: self._viewport.set_object_transparency(
                            document_id, oid, value
                        ),
                        lambda oid=object_id: self._viewport.set_object_transparency(
                            document_id, oid, default.transparency
                        ),
                    )
                )
            if appearance.visible is not default.visible:
                operations.append(
                    (
                        lambda oid=object_id, value=appearance.visible: self._viewport.set_object_visibility(
                            document_id, oid, value
                        ),
                        lambda oid=object_id: self._viewport.set_object_visibility(
                            document_id, oid, default.visible
                        ),
                    )
                )
        applied: list[Callable[[], bool]] = []
        for apply, rollback in operations:
            if not apply():
                if not rollback():
                    logger.error("Rollback thao tác CAD view state hiện tại thất bại")
                for undo in reversed(applied):
                    if not undo():
                        logger.error("Rollback CAD view state không hoàn tất")
                return False
            applied.append(rollback)
        self._display_mode = desired_mode
        self._view_direction = desired_direction
        self._appearances = desired
        self._user_overrides = loaded_overrides
        self._refresh_container_visibility()
        self.actions[f"display_{desired_mode.value}"].setChecked(True)
        return True

    def _stage_current_state(self) -> bool:
        source_id = self._active_source_id
        metadata = self._active_metadata
        mapping = self._persistent_map
        if source_id is None or self._project_service is None:
            return True
        if metadata is None or mapping is None:
            return False
        persisted: list[PersistentObjectAppearance] = []
        for object_id, key in mapping.by_runtime.items():
            if isinstance(key, PersistentXcafOccurrenceKey):
                override = self._user_overrides.get(object_id)
                if override is not None and not override.is_empty:
                    persisted.append(PersistentObjectAppearance(key, override))
                continue
            appearance = self._appearances.get(object_id, ObjectAppearance())
            if self._isolate_snapshot is not None:
                appearance = ObjectAppearance(
                    visible=self._isolate_snapshot.get(object_id, appearance.visible),
                    color=appearance.color,
                    transparency=appearance.transparency,
                )
            if appearance != ObjectAppearance():
                persisted.append(PersistentObjectAppearance(key, appearance))
        try:
            self._project_service.stage_cad_view_state(
                CadViewState(
                    source_id=source_id,
                    display_mode=self._display_mode,
                    view_direction=self._view_direction,
                    object_appearances=tuple(persisted),
                )
            )
        except (ProjectError, TypeError, ValueError):
            logger.exception("Không thể stage CAD view state")
            return False
        self.project_state_changed.emit()
        return True

    def _update_xcaf_override(
        self,
        object_id: CadObjectId,
        *,
        visible: bool | None | object = _UNCHANGED,
        color: ObjectColor | None | object = _UNCHANGED,
        transparency: float | None | object = _UNCHANGED,
    ) -> None:
        mapping = self._persistent_map
        key = mapping.by_runtime.get(object_id) if mapping is not None else None
        if not isinstance(key, PersistentXcafOccurrenceKey):
            return
        current = self._user_overrides.get(object_id, ObjectAppearanceOverride())
        base = self._base_appearances.get(object_id, ObjectAppearance())
        if visible is not _UNCHANGED and visible == base.visible:
            visible = None
        if color is not _UNCHANGED and color == base.color:
            color = None
        if transparency is not _UNCHANGED and transparency == base.transparency:
            transparency = None
        updated = ObjectAppearanceOverride(
            visible=current.visible if visible is _UNCHANGED else visible,
            color=current.color if color is _UNCHANGED else color,
            transparency=(
                current.transparency
                if transparency is _UNCHANGED
                else transparency
            ),
        )
        if updated.is_empty:
            self._user_overrides.pop(object_id, None)
        else:
            self._user_overrides[object_id] = updated

    def _restore_runtime_appearances(
        self,
        previous: dict[CadObjectId, ObjectAppearance],
        affected: set[CadObjectId],
    ) -> None:
        document_id = self._active_document_id
        tree = self._active_tree
        if document_id is None or tree is None:
            return
        for object_id in affected:
            node = tree.find(object_id)
            appearance = previous.get(object_id)
            if node is None or not node.has_presentation or appearance is None:
                continue
            self._viewport.set_object_color(document_id, object_id, appearance.color)
            self._viewport.set_object_transparency(
                document_id, object_id, appearance.transparency
            )
            self._viewport.set_object_visibility(
                document_id, object_id, appearance.visible
            )


def _transfer_metadata(metadata: CadDocumentMetadata) -> dict[str, object]:
    topology = metadata.topology_counts
    mesh = metadata.mesh_statistics
    return {
        "cad_format": metadata.cad_format.value,
        "geometry_kind": metadata.geometry_kind.value,
        "document_kind": metadata.document_kind.value,
        "units": metadata.units.value,
        "topology_counts": (
            {}
            if topology is None
            else {
                "solids": topology.solids,
                "faces": topology.faces,
                "edges": topology.edges,
            }
        ),
        "mesh_statistics": (
            {}
            if mesh is None
            else {
                "vertices": mesh.vertices,
                "triangles": mesh.triangles,
            }
        ),
    }



def _document_open_origin(origin: CadLoadOrigin) -> DocumentOpenOrigin:
    """Convert UI origin once at the project/application boundary."""

    return DocumentOpenOrigin(origin.value)


def _cad_load_origin(origin: DocumentOpenOrigin) -> CadLoadOrigin:
    """Convert immutable prepared-open origin once for WP1 orchestration."""

    return CadLoadOrigin(origin.value)


def _display_label(mode: DisplayMode) -> str:
    return {
        DisplayMode.SHADED: "Tô bóng",
        DisplayMode.WIREFRAME: "Khung dây",
        DisplayMode.SHADED_WITH_EDGES: "Tô bóng kèm cạnh",
    }[mode]


def _source_object_appearance(source) -> ObjectAppearance:
    if source is None:
        return ObjectAppearance()
    color = source.surface_color or source.generic_color
    if color is None:
        return ObjectAppearance()
    return ObjectAppearance(color=ObjectColor(color.red, color.green, color.blue))


def _persistent_path_text(key: object) -> str:
    path = getattr(key, "topology_path", None)
    if path is None:
        path = getattr(key, "occurrence_path", None)
    return str(path) if path is not None else "<unknown>"
