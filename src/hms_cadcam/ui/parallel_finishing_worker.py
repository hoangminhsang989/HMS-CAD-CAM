"""Cancelable QRunnable wrapper for headless Parallel Finishing calculations."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from hms_cadcam.cam.cam3d.parallel import ParallelProgress


class ParallelFinishingTaskSignals(QObject):
    """Carry native-free progress/results from a pool thread to the UI owner."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)
    finished = Signal()


class ParallelFinishingTask(QRunnable):
    """Execute one calculation callable with cooperative cancellation."""

    def __init__(
        self,
        operation: Callable[
            [Callable[[], bool], Callable[[ParallelProgress], None]], object
        ],
    ) -> None:
        super().__init__()
        if not callable(operation):
            raise TypeError("Parallel worker operation must be callable")
        self._operation = operation
        self._cancelled = threading.Event()
        self._state_lock = threading.Lock()
        self._abandoned = False
        self.signals = ParallelFinishingTaskSignals()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        """Request bounded cooperative cancellation without blocking the UI."""
        self._cancelled.set()

    def abandon(self) -> None:
        """Cancel and suppress late progress/result callbacks after project close."""
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

    def _report_progress(self, progress: ParallelProgress) -> None:
        if not isinstance(progress, ParallelProgress):
            raise TypeError("Parallel worker progress payload is invalid")
        if self._may_emit():
            self.signals.progress.emit(progress)

    def _may_emit(self) -> bool:
        with self._state_lock:
            return not self._abandoned
