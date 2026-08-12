"""Qt worker bridge created only inside the optional simulation workspace."""

from __future__ import annotations

from threading import Event
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class SimulationWorkerSignals(QObject):
    progress = Signal(str, int, int)
    succeeded = Signal(object)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()


class SimulationWorker(QRunnable):
    """Cooperative, single-owner task which never accesses UI objects."""

    def __init__(self, computation: Callable[[Callable[[], bool], Callable[[str, int, int], None]], object]) -> None:
        super().__init__()
        self.signals = SimulationWorkerSignals()
        self._computation = computation
        self._cancel = Event()
        self.setAutoDelete(True)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self._computation(
                lambda: self._cancel.is_set(),
                lambda stage, value, total: self.signals.progress.emit(stage, value, total),
            )
            if self._cancel.is_set():
                self.signals.cancelled.emit()
            else:
                self.signals.succeeded.emit(result)
        except RuntimeError as error:
            if self._cancel.is_set():
                self.signals.cancelled.emit()
            else:
                self.signals.failed.emit(str(error))
        finally:
            self.signals.finished.emit()
