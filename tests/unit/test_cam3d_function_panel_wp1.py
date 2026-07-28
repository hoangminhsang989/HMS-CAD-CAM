from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDockWidget

from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.project.models import ProjectManifest, ProjectSession, UnitSystem
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam3d_function_panel import Cam3DFunctionPanel
from hms_cadcam.ui.cam3d_function_state import Cam3DPresentationState, Cam3DUiState
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend


LEGACY_DOCK_OBJECT_NAMES = (
    "ProjectManagerDock",
    "PropertiesDock",
    "OutputDock",
    "FunctionEditorDock",
    "SecondaryWorkflowDock",
    "IncomingGeometryNotificationDock",
    "IncomingGeometryChangeDock",
    "OperationManagerDock",
)


@pytest.fixture(scope="module", autouse=True)
def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _session(tmp_path: Path) -> ProjectSession:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    manifest = ProjectManifest(
        format="HMS_PROJECT",
        format_version=1,
        application="HMS CAD/CAM",
        application_version="test",
        project_id=uuid4(),
        project_name="CAM 3D WP1",
        created_at=now,
        modified_at=now,
        units=UnitSystem.MILLIMETER,
        source_files=(),
        active_document=None,
        database="project.db",
    )
    return ProjectSession(tmp_path / "CAM_3D_WP1.HMS", manifest)


def _flags(cam3d: bool) -> UiFeatureFlags:
    return UiFeatureFlags(
        {
            UiFeatureFlag.POST_ASSEMBLY_9A7: False,
            UiFeatureFlag.CAM_3D_9A8: cam3d,
        }
    )


def _window(tmp_path: Path, *, cam3d: bool) -> MainWindow:
    return _window_with_flags(tmp_path, _flags(cam3d))


def _window_with_flags(
    tmp_path: Path,
    flags: UiFeatureFlags,
) -> MainWindow:
    return MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel("cam3d wp1 test"),
        UnavailableCadViewportBackend("cam3d wp1 test"),
        ui_feature_flags=flags,
    )


def _dock_object_names(window: MainWindow) -> tuple[str, ...]:
    return tuple(
        dock.objectName() for dock in window.findChildren(QDockWidget)
    )


def _cam3d_action_count(window: MainWindow) -> int:
    return sum(
        action.objectName() == "Cam3DFunctionOpenAction"
        for action in window.findChildren(QAction)
    )


def test_panel_has_required_responsive_sections_and_disabled_controls() -> None:
    panel = Cam3DFunctionPanel(feature_enabled=True)
    assert panel.objectName() == "Cam3DFunctionPanel"
    assert tuple(panel.section_keys()) == (
        "machining_zone",
        "part",
        "check",
        "fixtures",
        "tool",
        "tolerance",
        "allowance",
        "safe_motion",
        "calculation_status",
        "diagnostics",
    )
    assert panel.scroll_area.widgetResizable()
    assert len(panel.placeholder_controls) == 10
    assert all(not control.isEnabled() for control in panel.placeholder_controls)
    assert all(
        control.objectName()
        and control.accessibleName()
        and control.accessibleDescription()
        for control in panel.placeholder_controls
    )
    panel.deleteLater()


def test_panel_renders_feature_disabled_empty_read_only_stale_and_error(
    tmp_path: Path,
) -> None:
    panel = Cam3DFunctionPanel(feature_enabled=False)
    assert panel.presentation_state.state is Cam3DUiState.FEATURE_DISABLED
    assert not panel.scroll_area.isEnabled()
    panel.set_feature_enabled(True)
    assert panel.presentation_state.state is Cam3DUiState.EMPTY
    session = _session(tmp_path)
    panel.bind_project(session, generation=4, read_only=True)
    assert panel.presentation_state.state is Cam3DUiState.READ_ONLY
    panel.set_state(Cam3DPresentationState.stale(session.manifest.project_id, 5))
    assert panel.property("cam3dState") == "stale"
    panel.set_state(
        Cam3DPresentationState.error(
            "geometry validation failed", session.manifest.project_id, 5
        )
    )
    assert panel.diagnostics_value.text() == "geometry validation failed"
    assert all(not control.isEnabled() for control in panel.placeholder_controls)
    panel.deleteLater()


def test_panel_project_rebind_drops_mutable_session_and_duplicate_signal(
    tmp_path: Path,
) -> None:
    panel = Cam3DFunctionPanel(feature_enabled=True)
    session = _session(tmp_path)
    emissions: list[object] = []
    panel.state_changed.connect(emissions.append)
    panel.bind_project(session, generation=3)
    panel.bind_project(session, generation=3)
    assert len(emissions) == 1
    assert panel.presentation_state.project_id == session.manifest.project_id
    assert not hasattr(panel, "_session")
    panel.bind_project(None, generation=None)
    assert panel.presentation_state.project_id is None
    assert panel.presentation_state.project_generation is None
    panel.deleteLater()


def test_main_window_feature_disabled_preserves_legacy_cam_route(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, cam3d=False)
    assert _dock_object_names(window) == LEGACY_DOCK_OBJECT_NAMES
    assert len(window.findChildren(QDockWidget)) == 8
    assert not hasattr(window, "cam3d_function_panel")
    assert not hasattr(window, "cam3d_function_dock")
    assert not hasattr(window, "cam3d_function_action")
    assert window.findChild(QDockWidget, "Cam3DFunctionDock") is None
    assert window.findChild(QAction, "Cam3DFunctionOpenAction") is None
    window._show_cam_workspace()
    app.processEvents()
    assert window.workspace_bar.active_workspace.value == "mill_2d"
    window.close()


def test_main_window_feature_enabled_opens_one_idempotent_shell(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, cam3d=True)
    assert window.cam3d_function_action.isVisible()
    assert window.cam3d_function_action.property("commandId") == "cam.cam3d.open"
    assert len(window.findChildren(QDockWidget)) == 9
    assert _dock_object_names(window) == (
        *LEGACY_DOCK_OBJECT_NAMES[:5],
        "Cam3DFunctionDock",
        *LEGACY_DOCK_OBJECT_NAMES[5:],
    )
    assert len(window.findChildren(Cam3DFunctionPanel)) == 1
    assert _cam3d_action_count(window) == 1
    window.cam3d_function_action.trigger()
    window.cam3d_function_action.trigger()
    app.processEvents()
    assert not window.cam3d_function_dock.isHidden()
    assert len(window.findChildren(QDockWidget)) == 9
    assert len(window.findChildren(Cam3DFunctionPanel)) == 1
    assert _cam3d_action_count(window) == 1
    assert window.cam3d_function_panel.presentation_state.state is Cam3DUiState.EMPTY
    window.close()


def test_main_window_missing_cam3d_flag_fails_closed_to_legacy_topology(
    tmp_path: Path,
) -> None:
    window = _window_with_flags(
        tmp_path,
        UiFeatureFlags({UiFeatureFlag.POST_ASSEMBLY_9A7: False}),
    )
    assert _dock_object_names(window) == LEGACY_DOCK_OBJECT_NAMES
    assert window.findChild(QDockWidget, "Cam3DFunctionDock") is None
    assert window.findChild(QAction, "Cam3DFunctionOpenAction") is None
    window.close()


def test_main_window_project_lifecycle_rebinds_and_clears_shell(
    tmp_path: Path,
) -> None:
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config_lifecycle")
    session = service.create_project_from_source(
        tmp_path / "projects", "CAM 3D Lifecycle", source
    )
    window = MainWindow(
        service,
        UnavailableCadKernel("cam3d wp1 lifecycle"),
        UnavailableCadViewportBackend("cam3d wp1 lifecycle"),
        ui_feature_flags=_flags(True),
    )
    window._handle_project_change(session)
    bound = window.cam3d_function_panel.presentation_state
    assert bound.state is Cam3DUiState.READY
    assert bound.project_id == session.manifest.project_id
    window._handle_project_change(None)
    cleared = window.cam3d_function_panel.presentation_state
    assert cleared.state is Cam3DUiState.EMPTY
    assert cleared.project_id is None
    assert len(window.findChildren(QDockWidget)) == 9
    assert len(window.findChildren(Cam3DFunctionPanel)) == 1
    assert _cam3d_action_count(window) == 1
    window.close()


def test_feature_off_project_switch_and_reopen_keep_legacy_topology(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config_reopen")
    session = service.create_project_from_source(
        tmp_path / "projects", "CAM 3D Feature Off", source
    )
    first = MainWindow(
        service,
        UnavailableCadKernel("cam3d wp1 feature off"),
        UnavailableCadViewportBackend("cam3d wp1 feature off"),
        ui_feature_flags=_flags(False),
    )
    first._handle_project_change(session)
    first._handle_project_change(None)
    assert _dock_object_names(first) == LEGACY_DOCK_OBJECT_NAMES
    assert _cam3d_action_count(first) == 0
    first.close()
    first.deleteLater()
    app.processEvents()

    reopened = MainWindow(
        service,
        UnavailableCadKernel("cam3d wp1 reopen"),
        UnavailableCadViewportBackend("cam3d wp1 reopen"),
        ui_feature_flags=_flags(False),
    )
    assert _dock_object_names(reopened) == LEGACY_DOCK_OBJECT_NAMES
    assert reopened.findChild(QDockWidget, "Cam3DFunctionDock") is None
    assert _cam3d_action_count(reopened) == 0
    reopened.close()