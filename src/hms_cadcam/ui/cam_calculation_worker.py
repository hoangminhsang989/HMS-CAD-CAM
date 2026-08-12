"""Cancelable QRunnable for native-free 2D CAM production calculations."""

from __future__ import annotations

from collections.abc import Callable
import threading

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from hms_cadcam.cam.optimization import CamCalculationProgress


class CamCalculationTaskSignals(QObject):
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)
    finished = Signal()


class CamCalculationTask(QRunnable):
    """Execute one cooperative CAM calculation outside the UI thread."""

    def __init__(self, operation: Callable[[Callable[[], bool], Callable[[CamCalculationProgress], None]], object]) -> None:
        super().__init__()
        if not callable(operation):
            raise TypeError("CAM calculation worker operation must be callable")
        self._operation = operation
        self._cancelled = threading.Event()
        self._state_lock = threading.Lock()
        self._abandoned = False
        self.signals = CamCalculationTaskSignals()

    def cancel(self) -> None:
        self._cancelled.set()

    def abandon(self) -> None:
        with self._state_lock:
            self._abandoned = True
        self.cancel()

    @Slot()
    def run(self) -> None:
        try:
            result = self._operation(self._cancelled.is_set, self._report_progress)
        except Exception as error:
            if self._may_emit():
                self.signals.failed.emit(error)
        else:
            if self._may_emit():
                self.signals.completed.emit(result)
        finally:
            self.signals.finished.emit()

    def _report_progress(self, value: CamCalculationProgress) -> None:
        if not isinstance(value, CamCalculationProgress):
            raise TypeError("CAM calculation progress payload is invalid")
        if self._may_emit():
            self.signals.progress.emit(value)

    def _may_emit(self) -> bool:
        with self._state_lock:
            return not self._abandoned
