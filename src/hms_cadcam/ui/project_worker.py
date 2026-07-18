"""Single-operation Qt worker for potentially large project filesystem tasks."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class ProjectTaskSignals(QObject):
    """Signals emitted by a ProjectTask back to the UI thread."""

    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()


class ProjectTask(QRunnable):
    """Run one project service callable outside the UI thread."""

    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = ProjectTaskSignals()

    @Slot()
    def run(self) -> None:
        """Execute the operation and preserve its exception for the controller."""
        try:
            self.signals.succeeded.emit(self.operation())
        except Exception as error:
            self.signals.failed.emit(error)
        finally:
            self.signals.finished.emit()
