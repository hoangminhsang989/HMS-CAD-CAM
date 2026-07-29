from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDockWidget

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectedSurface,
    Cam3DSelectionApplicationService,
    Cam3DSelectionIssue,
    Cam3DSelectionProvenance,
    Cam3DSelectionRole,
    Cam3DSelectionState,
    Cam3DSelectionStatus,
)
from hms_cadcam.cam.domain import Revision
from hms_cadcam.project.models import ProjectManifest, ProjectSession, UnitSystem
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam3d_function_panel import Cam3DFunctionPanel
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend
from tests.unit._cam3d_fixtures import surface


@pytest.fixture(scope="module", autouse=True)
def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _session(tmp_path: Path) -> ProjectSession:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return ProjectSession(
        tmp_path / "WP2A.HMS",
        ProjectManifest(
            format="HMS_PROJECT",
            format_version=1,
            application="HMS CAD/CAM",
            application_version="test",
            project_id=uuid4(),
            project_name="WP2A",
            created_at=now,
            modified_at=now,
            units=UnitSystem.MILLIMETER,
            source_files=(),
            active_document=None,
            database="project.db",
        ),
    )


def _item(
    role: Cam3DSelectionRole,
    selector: str,
    project_id,
    source_id,
    generation: int,
) -> Cam3DSelectedSurface:
    return Cam3DSelectedSurface(
        role,
        surface(
            project_id,
            source_id,
            selector,
            role.cam_role,
            revision=Revision(0),
        ),
        Cam3DSelectionProvenance(
            project_id,
            generation,
            CadDocumentId("document-wp2a"),
            source_id,
        ),
        f"CAD surface {selector}",
    )


def _resolved_state(*, read_only: bool = False) -> Cam3DSelectionState:
    project_id = uuid4()
    source_id = uuid4()
    state = Cam3DSelectionState.for_project(
        project_id,
        8,
        read_only=read_only,
    )
    if read_only:
        writable = Cam3DSelectionState.for_project(project_id, 8)
        for role in Cam3DSelectionRole:
            writable = writable.assign(
                role,
                (_item(role, role.value, project_id, source_id, 8),),
            )
        return Cam3DSelectionState(
            project_id,
            8,
            True,
            writable.part,
            writable.check,
            writable.fixture,
        )
    for role in Cam3DSelectionRole:
        state = state.assign(
            role,
            (_item(role, role.value, project_id, source_id, 8),),
        )
    return state


def _flags(enabled: bool) -> UiFeatureFlags:
    return UiFeatureFlags(
        {
            UiFeatureFlag.POST_ASSEMBLY_9A7: False,
            UiFeatureFlag.CAM_3D_9A8: enabled,
        }
    )


def test_panel_builds_three_typed_role_editors_with_accessible_commands() -> None:
    panel = Cam3DFunctionPanel(feature_enabled=True)
    assert tuple(role for role, _editor in panel.role_editors) == tuple(
        Cam3DSelectionRole
    )
    assert len(panel.role_editors) == 3
    for role, editor in panel.role_editors:
        assert editor.objectName() == f"Cam3DSelectionEditor_{role.value}"
        assert editor.summary_label.accessibleName()
        assert editor.summary_label.accessibleDescription()
        assert editor.assign_button.accessibleName()
        assert editor.assign_button.accessibleDescription()
        assert editor.clear_button.accessibleName()
        assert editor.clear_button.accessibleDescription()
        assert not editor.assign_button.isEnabled()
    panel.deleteLater()


def test_panel_renders_empty_partial_resolved_stale_invalid_and_read_only() -> None:
    panel = Cam3DFunctionPanel(feature_enabled=True)
    resolved = _resolved_state()
    partial = resolved.clear_role(Cam3DSelectionRole.FIXTURE)
    panel.set_selection_state(partial)
    assert panel.selection_state.status is Cam3DSelectionStatus.PARTIAL
    assert all(editor.assign_button.isEnabled() for _role, editor in panel.role_editors)

    panel.set_selection_state(resolved)
    assert panel.selection_state.resolved
    assert all("1" in editor.summary_label.text() for _role, editor in panel.role_editors)

    panel.set_selection_state(resolved.mark_stale())
    assert panel.property("cam3dState") == "stale"
    panel.set_selection_state(
        partial.with_issue(Cam3DSelectionIssue.INVALID_GEOMETRY_KIND)
    )
    assert panel.property("cam3dState") == "error"
    assert "invalid_geometry_kind" not in panel.diagnostics_value.text()

    panel.set_selection_state(_resolved_state(read_only=True))
    assert panel.property("cam3dState") == "read_only"
    assert all(
        not editor.assign_button.isEnabled() and not editor.clear_button.isEnabled()
        for _role, editor in panel.role_editors
    )
    panel.deleteLater()


def test_role_signals_are_connected_once_across_repeated_state_rendering() -> None:
    panel = Cam3DFunctionPanel(feature_enabled=True)
    state = _resolved_state().clear_all()
    emitted: list[object] = []
    panel.selection_assign_requested.connect(emitted.append)
    for _index in range(4):
        panel.set_selection_state(state)
    part_editor = dict(panel.role_editors)[Cam3DSelectionRole.PART]
    part_editor.assign_button.click()
    assert emitted == [Cam3DSelectionRole.PART]
    panel.deleteLater()


def test_feature_flag_gates_selection_service_with_wp1_topology(
    tmp_path: Path,
) -> None:
    off_service = ProjectService.create_default(tmp_path / "config_off")
    off_window = MainWindow(
        off_service,
        UnavailableCadKernel("wp2a feature off"),
        UnavailableCadViewportBackend("wp2a feature off"),
        ui_feature_flags=_flags(False),
    )
    assert len(off_window.findChildren(QDockWidget)) == 8
    assert not hasattr(off_window, "cam3d_function_panel")
    assert not hasattr(off_window, "cam3d_function_dock")
    assert not hasattr(off_window, "cam3d_function_action")
    assert not hasattr(off_window, "_cam3d_selection_service")
    off_window.close()

    on_service = ProjectService.create_default(tmp_path / "config_on")
    on_window = MainWindow(
        on_service,
        UnavailableCadKernel("wp2a feature on"),
        UnavailableCadViewportBackend("wp2a feature on"),
        ui_feature_flags=_flags(True),
    )
    cam3d_docks = [
        dock
        for dock in on_window.findChildren(QDockWidget)
        if dock.objectName() == "Cam3DFunctionDock"
    ]
    cam3d_actions = [
        action
        for action in on_window.findChildren(QAction)
        if action.objectName() == "Cam3DFunctionOpenAction"
    ]
    assert len(cam3d_docks) == 1
    assert len(on_window.findChildren(Cam3DFunctionPanel)) == 1
    assert len(cam3d_actions) == 1
    assert isinstance(
        on_window._cam3d_selection_service,
        Cam3DSelectionApplicationService,
    )
    on_window.close()


def test_main_window_service_binding_fails_closed_without_active_cad_source(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path / "projects", "WP2A Binding")
    window = MainWindow(
        service,
        UnavailableCadKernel("wp2a"),
        UnavailableCadViewportBackend("wp2a"),
        ui_feature_flags=_flags(True),
    )
    window._handle_project_change(session)
    before_snapshot = service.cam_snapshot
    before_dirty = session.is_dirty

    part_editor = dict(window.cam3d_function_panel.role_editors)[
        Cam3DSelectionRole.PART
    ]
    part_editor.assign_button.click()
    QApplication.processEvents()

    state = window.cam3d_function_panel.selection_state
    assert state.issue is Cam3DSelectionIssue.SOURCE_UNAVAILABLE
    assert state.part == ()
    assert service.cam_snapshot == before_snapshot
    assert session.is_dirty is before_dirty
    assert "source_unavailable" not in window.cam3d_function_panel.reason_label.text()
    window.close()


def test_project_switch_and_close_reset_selection_binding_in_main_window(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config_lifecycle")
    first = service.new_project(tmp_path / "projects", "WP2A First")
    window = MainWindow(
        service,
        UnavailableCadKernel("wp2a lifecycle"),
        UnavailableCadViewportBackend("wp2a lifecycle"),
        ui_feature_flags=_flags(True),
    )
    window._handle_project_change(first)
    first_generation = window.cam3d_function_panel.selection_state.project_generation
    second = service.new_project(tmp_path / "projects", "WP2A Second")
    window._handle_project_change(second)
    assert window.cam3d_function_panel.selection_state.project_id == (
        second.manifest.project_id
    )
    assert window.cam3d_function_panel.selection_state.project_generation != (
        first_generation
    )
    assert window.cam3d_function_panel.selection_state.part == ()
    window._handle_project_change(None)
    assert window.cam3d_function_panel.selection_state.project_id is None
    window.close()


@pytest.mark.parametrize("language", tuple(UiLanguage))
def test_role_labels_summary_and_accessibility_are_catalog_backed(
    language: UiLanguage,
) -> None:
    service = translation_service()
    previous = service.language
    try:
        service.set_language(language)
        panel = Cam3DFunctionPanel(feature_enabled=True)
        panel.set_selection_state(_resolved_state())
        for role, editor in panel.role_editors:
            assert editor.assign_button.text()
            assert editor.assign_button.text() != Cam3DSelectionIssue.NO_SELECTION.value
            assert editor.summary_label.text()
            assert editor.summary_label.accessibleName()
            assert str(panel.selection_state.project_id) not in editor.summary_label.text()
            assert role.value not in editor.validity_label.text()
        panel.deleteLater()
    finally:
        service.set_language(previous)


def test_wp2a_modules_have_no_database_persistence_or_automatic_calculation() -> None:
    root = Path(__file__).parents[2]
    paths = (
        root / "src/hms_cadcam/cam/application/cam3d_selection.py",
        root / "src/hms_cadcam/ui/cam3d_selection_editor.py",
        root / "src/hms_cadcam/ui/cam3d_function_panel.py",
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    forbidden = (
        "sqlite3",
        "project.db",
        "Cam3DGeometryService",
        "tessellate(",
        "stage_cam3d_config",
        "SimulationRuntimeService",
        "PostRuntimeService",
        "G-code",
    )
    assert all(token not in text for token in forbidden)
