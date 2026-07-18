"""Run the standalone OCP/PySide6 viewer technical spike."""

from __future__ import annotations

import sys

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QToolBar,
)

from importer import CadImporter
from model import (
    DisplayMode,
    ImportResult,
    InteractionMode,
    SelectionKind,
    SelectionSummary,
    ViewOrientation,
)
from viewer import OcpViewerWidget
from worker import ImportWorker


class SpikeWindow(QMainWindow):
    """Top-level spike window with explicit OCCT teardown ordering."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HMS CAD/CAM — OCP Viewer Spike 4B3")
        self._importer = CadImporter()
        self._thread_pool = QThreadPool.globalInstance()
        self._import_worker: ImportWorker | None = None
        self.viewer = OcpViewerWidget(self)
        self.setCentralWidget(self.viewer)
        self.resize(1100, 720)
        self._selection_label = QLabel("Selection: 0")
        self._import_label = QLabel("Import: sẵn sàng")
        self.statusBar().addPermanentWidget(self._selection_label, 1)
        self.statusBar().addPermanentWidget(self._import_label, 2)
        self.viewer.selection_changed.connect(self._show_selection)
        self._build_camera_toolbar()
        self._build_view_toolbar()
        self._build_display_toolbar()
        self._build_selection_toolbar()
        self._build_import_toolbar()

    def _build_import_toolbar(self) -> None:
        toolbar = QToolBar("Import", self)
        toolbar.setObjectName("ImportToolbar")
        self._import_action = toolbar.addAction("Open STEP/BREP")
        self._import_action.triggered.connect(self._choose_import_file)
        self.addToolBar(toolbar)

    def _choose_import_file(self) -> None:
        source_path, _filter = QFileDialog.getOpenFileName(
            self,
            "Mở STEP hoặc BREP",
            "",
            "CAD files (*.step *.stp *.brep *.brp)",
        )
        if source_path:
            self.import_path(source_path)

    def import_path(self, source_path: str) -> None:
        """Start a background import without giving the worker a viewer."""
        if self._import_worker is not None:
            self._import_label.setText("Import: đang bận")
            return
        worker = ImportWorker(self._importer, source_path)
        worker.signals.progress.connect(self._show_import_progress)
        worker.signals.completed.connect(self._finish_import)
        self._import_worker = worker
        self._import_action.setEnabled(False)
        self._thread_pool.start(worker)

    def _show_import_progress(self, status: str) -> None:
        self._import_label.setText(f"Import: {status}")

    def _finish_import(self, result: ImportResult) -> None:
        self._import_worker = None
        self._import_action.setEnabled(True)
        if not result.success or result.shape_id is None:
            message = "; ".join(result.errors) or "Lỗi không xác định"
            self._import_label.setText(f"Import: lỗi — {message}")
            return
        self._importer.present_shape(result.shape_id, self.viewer.replace_shape)
        counts = result.topology_counts
        self._import_label.setText(
            f"Import: hoàn thành — {result.detected_format.upper()} | "
            f"solid={counts['solid']}, face={counts['face']}, edge={counts['edge']} | "
            f"bounds={tuple(round(value, 3) for value in result.bounding_box or ())}"
        )

    def _build_camera_toolbar(self) -> None:
        toolbar = QToolBar("Camera", self)
        toolbar.setObjectName("CameraToolbar")
        toolbar.addAction("Fit All", self.viewer.fit_all)
        toolbar.addAction("Reset Iso", self.viewer.reset_isometric)
        group = QActionGroup(self)
        group.setExclusive(True)
        for label, mode in (
            ("Select", InteractionMode.SELECT),
            ("Rotate", InteractionMode.ROTATE),
            ("Pan", InteractionMode.PAN),
        ):
            action = QAction(label, self, checkable=True)
            action.setChecked(mode is InteractionMode.SELECT)
            action.triggered.connect(
                lambda _checked=False, selected=mode: (
                    self.viewer.set_interaction_mode(selected)
                )
            )
            group.addAction(action)
            toolbar.addAction(action)
        self.addToolBar(toolbar)

    def _build_view_toolbar(self) -> None:
        toolbar = QToolBar("Views", self)
        toolbar.setObjectName("ViewToolbar")
        for orientation in ViewOrientation:
            action = toolbar.addAction(orientation.value.title())
            action.triggered.connect(
                lambda _checked=False, selected=orientation: (
                    self.viewer.set_view_orientation(selected)
                )
            )
        self.addToolBar(toolbar)

    def _build_display_toolbar(self) -> None:
        toolbar = QToolBar("Display", self)
        toolbar.setObjectName("DisplayToolbar")
        group = QActionGroup(self)
        group.setExclusive(True)
        for label, mode in (
            ("Shaded", DisplayMode.SHADED),
            ("Wireframe", DisplayMode.WIREFRAME),
            ("Shaded + Edges", DisplayMode.SHADED_WITH_EDGES),
        ):
            action = QAction(label, self, checkable=True)
            action.setChecked(mode is DisplayMode.SHADED_WITH_EDGES)
            action.triggered.connect(
                lambda _checked=False, selected=mode: self.viewer.set_display_mode(selected)
            )
            group.addAction(action)
            toolbar.addAction(action)
        self.addToolBar(toolbar)

    def _build_selection_toolbar(self) -> None:
        toolbar = QToolBar("Selection", self)
        toolbar.setObjectName("SelectionToolbar")
        group = QActionGroup(self)
        group.setExclusive(True)
        for kind in SelectionKind:
            action = QAction(kind.value.title(), self, checkable=True)
            action.setChecked(kind is SelectionKind.SOLID)
            action.triggered.connect(
                lambda _checked=False, selected=kind: self.viewer.set_selection_kind(selected)
            )
            group.addAction(action)
            toolbar.addAction(action)
        self.addToolBar(toolbar)

    def _show_selection(self, summary: SelectionSummary) -> None:
        if not summary.items:
            self._selection_label.setText("Selection: 0")
            return
        item = summary.items[0]
        rounded = tuple(round(value, 3) for value in item.bounds)
        self._selection_label.setText(
            f"Selection: {summary.count} | {item.topology.upper()} | "
            f"{item.shape_id} | bounds={rounded}"
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        """Release the embedded OCCT view before closing its parent HWND."""
        self.viewer.close()
        super().closeEvent(event)


def main() -> int:
    """Open the spike window and return the Qt event-loop exit code."""
    application = QApplication(sys.argv)
    window = SpikeWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
