"""Smoke tests for the Stage 1 desktop shell."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolBar, QToolButton  # noqa: E402

from hms_cadcam.project.service import ProjectService  # noqa: E402
from hms_cadcam.ui.main_window import MainWindow  # noqa: E402


def test_main_window_can_open_and_close(tmp_path: Path) -> None:
    """Construct the complete workspace, process events and close cleanly."""
    application = QApplication.instance() or QApplication([])
    service = ProjectService.create_default(tmp_path / "config")
    window = MainWindow(service)

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
    assert future_toolbars["QuickAccess"].actions()[0] is window.project_controller.actions["new"]
    assert future_toolbars["QuickAccess"].actions()[1] is window.project_controller.actions["open"]
    assert future_toolbars["QuickAccess"].actions()[2] is window.project_controller.actions["save"]
    assert window.project_controller.actions["new"].isEnabled()
    assert window.project_controller.actions["open"].isEnabled()
    assert not window.project_controller.actions["save"].isEnabled()
    assert all(
        not action.isEnabled()
        for name in ("ViewportTools", "ContextTools")
        for action in future_toolbars[name].actions()
    )
    ribbon_buttons = [
        button
        for button in window.findChildren(QToolButton)
        if button.objectName() == "RibbonButton"
    ]
    assert ribbon_buttons
    assert all(
        not button.isEnabled()
        for button in ribbon_buttons
        if button.defaultAction() is None
    )

    window.show()
    application.processEvents()
    assert window.isVisible()
    assert window.close()
    application.processEvents()
    assert not window.isVisible()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
