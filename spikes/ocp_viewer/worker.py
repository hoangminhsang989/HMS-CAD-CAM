"""Qt worker boundary for non-blocking CAD import in the OCP spike."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from importer import CadImporter
from model import ImportResult


class ImportWorkerSignals(QObject):
    """Signals carrying progress text and OCP-free import results."""

    progress = Signal(str)
    completed = Signal(object)


class ImportWorker(QRunnable):
    """Run only the headless importer; this class has no viewer reference."""

    def __init__(self, importer: CadImporter, source_path: str | Path) -> None:
        super().__init__()
        self._importer = importer
        self._source_path = Path(source_path)
        self._state_lock = threading.Lock()
        self._abandoned = False
        self._result: ImportResult | None = None
        self.signals = ImportWorkerSignals()

    def abandon(self) -> None:
        """Prevent future UI signals and discard a result produced after close."""
        with self._state_lock:
            self._abandoned = True
            result = self._result
            self._result = None
        if result is not None:
            self._importer.discard_result(result)

    def acknowledge_result(self, result: ImportResult) -> None:
        """Release worker ownership after the UI consumes a queued result."""
        with self._state_lock:
            if self._result is result:
                self._result = None

    def _report_progress(self, status: str) -> None:
        with self._state_lock:
            should_emit = not self._abandoned
        if should_emit:
            self.signals.progress.emit(status)

    def run(self) -> None:
        """Execute CAD reading and emit its intermediate result."""
        result = self._importer.import_file(
            self._source_path,
            self._report_progress,
        )
        with self._state_lock:
            should_emit = not self._abandoned
            if should_emit:
                self._result = result
        if should_emit:
            self.signals.completed.emit(result)
        else:
            self._importer.discard_result(result)
