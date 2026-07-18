"""Qt worker boundary for non-blocking CAD import in the OCP spike."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from importer import CadImporter


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
        self.signals = ImportWorkerSignals()

    def run(self) -> None:
        """Execute CAD reading and emit its intermediate result."""
        result = self._importer.import_file(
            self._source_path,
            self.signals.progress.emit,
        )
        self.signals.completed.emit(result)
