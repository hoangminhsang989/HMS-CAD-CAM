"""Smoke tests for the Stage 1 desktop shell."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hms_cadcam.ui.main_window import MainWindow  # noqa: E402


def test_main_window_can_open_and_close() -> None:
    """Construct the complete workspace, process events and close cleanly."""
    application = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert window.centralWidget() is window.viewport
    assert window.dockWidgetArea(window.project_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert window.dockWidgetArea(window.properties_dock) == Qt.DockWidgetArea.RightDockWidgetArea
    assert window.dockWidgetArea(window.output_dock) == Qt.DockWidgetArea.BottomDockWidgetArea
    assert window.project_dock.features() & window.project_dock.DockWidgetFeature.DockWidgetMovable

    window.show()
    application.processEvents()
    assert window.isVisible()
    assert window.close()
    application.processEvents()
    assert not window.isVisible()
