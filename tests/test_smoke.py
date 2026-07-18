"""Smoke tests for the Stage 1 desktop shell."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolBar, QToolButton  # noqa: E402

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
    assert {
        window.project_dock.objectName(),
        window.properties_dock.objectName(),
        window.output_dock.objectName(),
    } == {"ProjectManagerDock", "PropertiesDock", "OutputDock"}

    future_toolbars = {
        toolbar.objectName(): toolbar
        for toolbar in window.findChildren(QToolBar)
        if toolbar.objectName() in {"QuickAccess", "ViewportTools", "ContextTools"}
    }
    assert set(future_toolbars) == {"QuickAccess", "ViewportTools", "ContextTools"}
    assert all(
        not action.isEnabled()
        for toolbar in future_toolbars.values()
        for action in toolbar.actions()
    )
    ribbon_buttons = [
        button
        for button in window.findChildren(QToolButton)
        if button.objectName() == "RibbonButton"
    ]
    assert ribbon_buttons
    assert all(not button.isEnabled() for button in ribbon_buttons)

    window.show()
    application.processEvents()
    assert window.isVisible()
    assert window.close()
    application.processEvents()
    assert not window.isVisible()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
