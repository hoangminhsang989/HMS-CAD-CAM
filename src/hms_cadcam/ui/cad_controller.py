"""Coordinate CAD actions, background import and viewport document ownership."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QFileDialog, QMainWindow

from hms_cadcam.cad.exceptions import CadDocumentNotFoundError
from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.models import (
    CadDocumentId,
    CadDocumentMetadata,
    CadFormat,
    CadImportResult,
)
from hms_cadcam.ui.cad_worker import CadImportTask
from hms_cadcam.viewer.models import DisplayMode, SelectionMode, ViewDirection
from hms_cadcam.viewer.widget import CadViewportWidget

logger = logging.getLogger(__name__)


class CadUiController(QObject):
    """Keep native import work outside UI and own the active document ID."""

    document_changed = Signal(object)
    message = Signal(str)
    progress_changed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(
        self,
        window: QMainWindow,
        kernel: CadKernel,
        viewport: CadViewportWidget,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._kernel = kernel
        self._viewport = viewport
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._active_task: CadImportTask | None = None
        self._request_generation = 0
        self._active_document_id: CadDocumentId | None = None
        self._active_metadata: CadDocumentMetadata | None = None
        self._closing = False
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

    @Slot()
    def choose_step(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self._window,
            "Mở STEP/STP",
            "",
            "STEP (*.step *.stp)",
        )
        if path:
            self.start_import(Path(path), CadFormat.STEP)

    @Slot()
    def choose_brep(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self._window,
            "Mở BREP",
            "",
            "BREP (*.brep *.brp)",
        )
        if path:
            self.start_import(Path(path), CadFormat.BREP)

    def start_import(self, source_path: str | Path, cad_format: CadFormat) -> None:
        """Start or supersede one background CAD import request."""
        if self._closing or not self._kernel.is_available():
            return
        self._invalidate_active_task()
        self._request_generation += 1
        request_id = self._request_generation
        task = CadImportTask(
            self._kernel,
            request_id,
            source_path,
            cad_format,
        )
        task.signals.progress.connect(self._show_progress)
        task.signals.completed.connect(self._finish_import)
        task.signals.failed.connect(self._import_failed)
        self._active_task = task
        self.progress_changed.emit("Đang đọc")
        self.message.emit(f"Đang nhập CAD: {Path(source_path)}")
        self._update_action_states()
        self.busy_changed.emit(True)
        self._thread_pool.start(task)

    def bind_project(self, source_path: Path | None) -> None:
        """Invalidate old work, clear the document and optionally load project CAD."""
        if self._closing:
            return
        self._request_generation += 1
        self._invalidate_active_task()
        self._clear_active_document()
        if source_path is not None:
            cad_format = _format_for_path(source_path)
            if cad_format is not None:
                self.start_import(source_path, cad_format)

    def shutdown(self) -> None:
        """Detach workers and release the active document before window teardown."""
        if self._closing:
            return
        self._closing = True
        self._request_generation += 1
        self._invalidate_active_task()
        self._thread_pool.clear()
        self._clear_active_document()
        self._update_action_states()

    @Slot(int, str)
    def _show_progress(self, request_id: int, status: str) -> None:
        if not self._closing and request_id == self._request_generation:
            self.progress_changed.emit(status)

    @Slot(int, object)
    def _finish_import(self, request_id: int, result: object) -> None:
        if not isinstance(result, CadImportResult):
            return
        task = self._active_task
        if task is not None and task.request_id == request_id:
            task.acknowledge(result)
        if self._closing or request_id != self._request_generation:
            self._release_result(result)
            return
        self._active_task = None
        self.busy_changed.emit(False)
        self._update_action_states()
        if not result.success or result.document_id is None or result.metadata is None:
            error = "; ".join(result.errors) or "Không thể đọc file CAD"
            self.progress_changed.emit("Lỗi")
            self.message.emit(f"Lỗi nhập CAD: {error}")
            return
        old_document_id = self._active_document_id
        if not self._viewport.display_document(result.document_id):
            self._release_result(result)
            self.progress_changed.emit("Lỗi")
            self.message.emit("Lỗi hiển thị CAD; giữ nguyên document hiện tại.")
            return
        self._active_document_id = result.document_id
        self._active_metadata = result.metadata
        if old_document_id is not None and old_document_id != result.document_id:
            self._release_document(old_document_id)
        self.progress_changed.emit("Hoàn thành")
        self.message.emit(f"Đã hiển thị CAD: {result.source_path}")
        self.document_changed.emit(result.metadata)
        self._update_action_states()

    @Slot(int, object)
    def _import_failed(self, request_id: int, error: object) -> None:
        if self._closing or request_id != self._request_generation:
            return
        self._active_task = None
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

    def _create_actions(self) -> dict[str, QAction]:
        definitions = {
            "open_step": ("Mở STEP/STP", self.choose_step),
            "open_brep": ("Mở BREP", self.choose_brep),
            "fit_all": ("Fit All", self._viewport.fit_all),
        }
        actions: dict[str, QAction] = {}
        for key, (text, slot) in definitions.items():
            action = QAction(text, self)
            action.setObjectName(f"Cad{key.title().replace('_', '')}Action")
            action.triggered.connect(slot)
            actions[key] = action
        for direction in ViewDirection:
            key = f"view_{direction.value}"
            action = QAction(direction.value.title(), self)
            action.setObjectName(f"CadView{direction.value.title()}Action")
            action.triggered.connect(
                lambda _checked=False, value=direction: (
                    self._viewport.set_view_direction(value)
                )
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
                lambda _checked=False, value=mode: self._viewport.set_display_mode(value)
            )
            display_group.addAction(action)
            actions[key] = action
        selection_group = QActionGroup(self)
        selection_group.setExclusive(True)
        for mode in SelectionMode:
            key = f"selection_{mode.value}"
            action = QAction(f"Chọn {mode.value.title()}", self, checkable=True)
            action.setChecked(mode is SelectionMode.SOLID)
            action.setObjectName(f"CadSelection{mode.value.title()}Action")
            action.triggered.connect(
                lambda _checked=False, value=mode: (
                    self._viewport.set_selection_mode(value)
                )
            )
            selection_group.addAction(action)
            actions[key] = action
        return actions

    def _update_action_states(self) -> None:
        available = self._kernel.is_available() and not self._closing
        for key, action in self.actions.items():
            if key.startswith("open_"):
                action.setEnabled(available and not self.is_busy)
            else:
                action.setEnabled(available and self._active_document_id is not None)

    def _invalidate_active_task(self) -> None:
        task = self._active_task
        self._active_task = None
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
        self._viewport.clear()
        if document_id is not None:
            self._release_document(document_id)
        self.document_changed.emit(None)
        self.progress_changed.emit("Sẵn sàng")
        self._update_action_states()

    def _release_result(self, result: CadImportResult) -> None:
        if result.document_id is not None:
            self._release_document(result.document_id)

    def _release_document(self, document_id: CadDocumentId) -> None:
        try:
            self._kernel.release_document(document_id)
        except CadDocumentNotFoundError:
            logger.debug("CAD document đã được release: %s", document_id)
        except Exception:
            logger.exception("Không thể release CAD document %s", document_id)


def _format_for_path(path: Path) -> CadFormat | None:
    suffix = path.suffix.lower()
    if suffix in {".step", ".stp"}:
        return CadFormat.STEP
    if suffix in {".brep", ".brp"}:
        return CadFormat.BREP
    return None


def _display_label(mode: DisplayMode) -> str:
    return {
        DisplayMode.SHADED: "Shaded",
        DisplayMode.WIREFRAME: "Wireframe",
        DisplayMode.SHADED_WITH_EDGES: "Shaded with edges",
    }[mode]
