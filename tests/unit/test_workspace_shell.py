"""Stage 9A.2 tests for workspace composition and user-only layout state."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QRect, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget, QToolButton  # noqa: E402

from hms_cadcam.cad.unavailable import UnavailableCadKernel  # noqa: E402
from hms_cadcam.project.service import ProjectService  # noqa: E402
from hms_cadcam.ui.main_window import MainWindow  # noqa: E402
from hms_cadcam.ui.workspace_layout import (  # noqa: E402
    WORKSPACE_LAYOUT_VERSION,
    WorkspaceLayoutStore,
    clamp_geometry,
)
from hms_cadcam.ui.workspace_shell import WorkspaceId  # noqa: E402
from hms_cadcam.viewer.unavailable_backend import (  # noqa: E402
    UnavailableCadViewportBackend,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _layout_store(path: Path) -> WorkspaceLayoutStore:
    return WorkspaceLayoutStore(
        QSettings(str(path), QSettings.Format.IniFormat)
    )


def _window(tmp_path: Path, *, settings_name: str = "workspace.ini") -> MainWindow:
    service = ProjectService.create_default(tmp_path / "config")
    return MainWindow(
        service,
        UnavailableCadKernel("stage9a2 test"),
        UnavailableCadViewportBackend("stage9a2 test"),
        layout_store=_layout_store(tmp_path / settings_name),
    )


def _dispose(window: MainWindow, application: QApplication) -> None:
    window.close()
    application.processEvents()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_shell_has_unique_named_docks_and_reuses_central_viewport(
    tmp_path: Path,
) -> None:
    application = _application()
    window = _window(tmp_path)

    assert window.centralWidget() is window.viewport
    assert window.cam_workspace.tree is window.operation_manager_host.tree
    assert window.function_editor_host.scroll_area.widget() is window.cam_workspace.editor
    assert window.function_editor_host.scroll_area.widgetResizable()
    assert (
        window.dockWidgetArea(window.operation_manager_dock)
        == Qt.DockWidgetArea.LeftDockWidgetArea
    )
    assert (
        window.dockWidgetArea(window.function_editor_dock)
        == Qt.DockWidgetArea.RightDockWidgetArea
    )
    assert (
        window.dockWidgetArea(window.output_dock)
        == Qt.DockWidgetArea.BottomDockWidgetArea
    )
    names = [
        dock.objectName()
        for dock in window.findChildren(QDockWidget)
        if dock.objectName()
    ]
    assert len(names) == len(set(names))
    assert {
        "OperationManagerDock",
        "FunctionEditorDock",
        "OutputDock",
        "SecondaryWorkflowDock",
        "ProjectManagerDock",
        "PropertiesDock",
    }.issubset(names)
    reachable_actions = set(window.operation_manager_host.toolbar.actions())
    for button in window.operation_manager_host.findChildren(QToolButton):
        if button.menu() is not None:
            reachable_actions.update(button.menu().actions())
    assert set(window.cam_workspace.actions.values()).issubset(reachable_actions)
    _dispose(window, application)


def test_workspace_selector_and_panel_toggle_are_explicit(tmp_path: Path) -> None:
    application = _application()
    window = _window(tmp_path)
    window.resize(1600, 900)
    window.show()
    application.processEvents()

    assert window.workspace_bar.active_workspace is WorkspaceId.HOME
    mill_3d = window.workspace_bar.actions_by_workspace[WorkspaceId.MILL_3D]
    lathe = window.workspace_bar.actions_by_workspace[WorkspaceId.LATHE]
    assert not mill_3d.isEnabled()
    assert "nền tảng" in mill_3d.toolTip()
    assert not lathe.isEnabled()
    assert "chưa" in lathe.toolTip()

    diagnostics_action = window.panel_actions["diagnostics"]
    assert diagnostics_action.isChecked()
    diagnostics_action.trigger()
    application.processEvents()
    assert window.output_dock.isHidden()
    assert not diagnostics_action.isChecked()
    diagnostics_action.trigger()
    application.processEvents()
    assert not window.output_dock.isHidden()
    assert diagnostics_action.isChecked()

    window._workspace_changed(WorkspaceId.SIMULATION.value)
    assert window.workspace_bar.active_workspace is WorkspaceId.SIMULATION
    assert not window.secondary_dock.isHidden()
    assert (
        window.secondary_panel_host.tabs.currentWidget()
        is window.secondary_panel_host.simulation_scroll
    )
    window._workspace_changed(WorkspaceId.POST.value)
    assert window.workspace_bar.active_workspace is WorkspaceId.POST
    assert (
        window.secondary_panel_host.tabs.currentWidget()
        is window.secondary_panel_host.post_scroll
    )
    _dispose(window, application)


def test_viewport_priority_at_supported_resolutions(tmp_path: Path) -> None:
    application = _application()
    window = _window(tmp_path)
    window.show()
    for width, height in ((1366, 768), (1600, 900), (1920, 1080)):
        window.resize(width, height)
        application.processEvents()
        assert window.viewport.width() >= 520
        assert window.viewport.height() >= 360
        assert window.function_editor_host.scroll_area.verticalScrollBar() is not None
        assert window.function_editor_dock.width() <= 520
        assert window.operation_manager_dock.width() <= 360

    window.resize(1100, 700)
    application.processEvents()
    assert window.operation_manager_dock.isHidden()
    assert window.viewport.width() >= 520
    window.resize(1366, 768)
    application.processEvents()
    assert not window.operation_manager_dock.isHidden()
    assert window.logicalDpiX() > 0
    _dispose(window, application)


def test_reset_layout_only_changes_ui_state(tmp_path: Path) -> None:
    application = _application()
    window = _window(tmp_path)
    session = window.project_controller.service.new_project(tmp_path, "Layout Data")
    project_id = session.manifest.project_id
    dirty_before = session.is_dirty
    window.operation_manager_dock.hide()
    window.function_editor_dock.hide()
    window.output_dock.hide()
    window.workspace_bar.set_active_workspace(WorkspaceId.POST)

    window.reset_workspace_layout()

    current = window.project_controller.service.current_project
    assert current is not None
    assert current.manifest.project_id == project_id
    assert current.is_dirty is dirty_before
    assert not window.operation_manager_dock.isHidden()
    assert window.function_editor_dock.isHidden()
    assert not window.cam_function_popup.isVisible()
    assert not window.output_dock.isHidden()
    assert window.secondary_dock.isHidden()
    assert window.workspace_bar.active_workspace is WorkspaceId.HOME
    _dispose(window, application)


def test_layout_persistence_restores_visibility_and_workspace(tmp_path: Path) -> None:
    application = _application()
    first = _window(tmp_path, settings_name="persistent.ini")
    first.resize(1600, 900)
    first.show()
    application.processEvents()
    first.output_dock.hide()
    first._workspace_changed(WorkspaceId.POST.value)
    application.processEvents()
    _dispose(first, application)

    reopened = _window(tmp_path, settings_name="persistent.ini")
    assert reopened.workspace_bar.active_workspace is WorkspaceId.POST
    assert reopened.output_dock.isHidden()
    assert not reopened.secondary_dock.isHidden()
    assert (
        reopened.secondary_panel_host.tabs.currentWidget()
        is reopened.secondary_panel_host.post_scroll
    )
    _dispose(reopened, application)


def test_layout_version_mismatch_resets_only_workspace_group(tmp_path: Path) -> None:
    application = _application()
    settings_path = tmp_path / "versioned.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    settings.setValue("unrelated/preference", "keep")
    settings.setValue("workspace_shell_9a2/layout_version", WORKSPACE_LAYOUT_VERSION + 1)
    settings.setValue("workspace_shell_9a2/active_workspace", "post")
    settings.sync()

    window = MainWindow(
        ProjectService.create_default(tmp_path / "version-config"),
        UnavailableCadKernel("stage9a2 test"),
        UnavailableCadViewportBackend("stage9a2 test"),
        layout_store=WorkspaceLayoutStore(settings),
    )

    assert window.workspace_bar.active_workspace is WorkspaceId.HOME
    assert settings.value("unrelated/preference") == "keep"
    assert settings.value("workspace_shell_9a2/layout_version") is None
    _dispose(window, application)


def test_invalid_geometry_is_clamped_to_available_screen() -> None:
    available = QRect(0, 0, 1920, 1040)
    clamped = clamp_geometry(QRect(5000, 4000, 2600, 1600), (available,))
    assert clamped == available

    partial = clamp_geometry(QRect(-1700, -900, 1200, 700), (available,))
    assert partial.left() >= available.left()
    assert partial.top() >= available.top()
    assert partial.right() <= available.right()
    assert partial.bottom() <= available.bottom()
