"""Background CAD import task with stale-result ownership handling."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.models import CadFormat, CadImportResult


class CadImportTaskSignals(QObject):
    """Carry only request IDs, progress text and OCP-free import results."""

    progress = Signal(int, str)
    completed = Signal(int, object)
    failed = Signal(int, object)


class CadImportTask(QRunnable):
    """Import one file without accessing AIS, QWidget or other UI state."""

    def __init__(
        self,
        kernel: CadKernel,
        request_id: int,
        source_path: str | Path,
        cad_format: CadFormat,
    ) -> None:
        super().__init__()
        self._kernel = kernel
        self.request_id = request_id
        self._source_path = Path(source_path)
        self._cad_format = cad_format
        self._state_lock = threading.Lock()
        self._abandoned = False
        self._result: CadImportResult | None = None
        self.signals = CadImportTaskSignals()

    def abandon(self) -> None:
        """Suppress future UI signals and release any unconsumed document."""
        with self._state_lock:
            self._abandoned = True
            result = self._result
            self._result = None
        self._release_result(result)

    def acknowledge(self, result: CadImportResult) -> None:
        """Transfer result ownership from this worker to its UI controller."""
        with self._state_lock:
            if self._result is result:
                self._result = None

    @Slot()
    def run(self) -> None:
        """Execute the native reader and publish only an OCP-free result."""
        self._report_progress("Đang đọc")
        self._report_progress("Đang chuyển đổi")
        try:
            if self._cad_format is CadFormat.STEP:
                result = self._kernel.import_step(self._source_path)
            else:
                result = self._kernel.import_brep(self._source_path)
        except Exception as error:
            with self._state_lock:
                should_emit_error = not self._abandoned
            if should_emit_error:
                self.signals.failed.emit(self.request_id, error)
            return
        with self._state_lock:
            should_emit = not self._abandoned
            if should_emit:
                self._result = result
        if should_emit:
            self.signals.completed.emit(self.request_id, result)
        else:
            self._release_result(result)

    def _report_progress(self, status: str) -> None:
        with self._state_lock:
            should_emit = not self._abandoned
        if should_emit:
            self.signals.progress.emit(self.request_id, status)

    def _release_result(self, result: CadImportResult | None) -> None:
        if result is None or result.document_id is None:
            return
        try:
            self._kernel.release_document(result.document_id)
        except Exception:
            logging.getLogger(__name__).exception(
                "Không thể release CAD document của worker đã hủy"
            )
