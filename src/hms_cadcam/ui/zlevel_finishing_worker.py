"""Cancelable QRunnable wrapper for Z-Level Finishing calculations."""

from __future__ import annotations

from collections.abc import Callable
import threading

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from hms_cadcam.cam.cam3d.zlevel import ZLevelProgress


class ZLevelFinishingTaskSignals(QObject):
    """Carry native-free progress/results from a pool thread to the UI owner."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)
    finished = Signal()


class ZLevelFinishingTask(QRunnable):
    """Execute one calculation callable with cooperative cancellation."""

    def __init__(
        self,
        operation: Callable[
            [Callable[[], bool], Callable[[ZLevelProgress], None]], object
        ],
    ) -> None:
        super().__init__()
        if not callable(operation):
            raise TypeError("Z-Level worker operation must be callable")
        self._operation = operation
        self._cancelled = threading.Event()
        self._state_lock = threading.Lock()
        self._abandoned = False
        self.signals = ZLevelFinishingTaskSignals()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        """Request cooperative cancellation without blocking the UI."""
        self._cancelled.set()

    def abandon(self) -> None:
        """Cancel and suppress late callbacks after project close."""
        with self._state_lock:
            self._abandoned = True
        self.cancel()

    @Slot()
    def run(self) -> None:
        """Run outside the UI thread and always signal terminal cleanup."""
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

    def _report_progress(self, progress: ZLevelProgress) -> None:
        if not isinstance(progress, ZLevelProgress):
            raise TypeError("Z-Level worker progress payload is invalid")
        if self._may_emit():
            self.signals.progress.emit(progress)

    def _may_emit(self) -> bool:
        with self._state_lock:
            return not self._abandoned
