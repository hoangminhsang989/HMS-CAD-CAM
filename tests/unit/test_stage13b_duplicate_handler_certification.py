"""Repeated-owner regression checks for the three certified production routes."""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent

from _lathe_ui_fixtures import workspace_for
from hms_cadcam.cam.domain import FacingParameters
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.function_editor.host import FunctionEditorHost
from hms_cadcam.ui.function_editor.model import FunctionEditorAction
from hms_cadcam.ui.lathe_workspace import build_lathe_parameter_update_preview
from hms_cadcam.ui.cam_ui import CamWorkspace
from test_drilling_ui import _workspace as drilling_workspace
from test_stage13b_camworkspace_dispatch_certification import _facing_workspace


def _dispose(host: FunctionEditorHost) -> None:
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _host(workspace) -> FunctionEditorHost:
    host = FunctionEditorHost(
        workspace.editor, workspace.tree, lambda: None,
        production_provider=workspace.production_function_editor_session,
        selection_exists=workspace.selection_exists,
    )
    assert host.active_page is not None
    return host


def test_stage13b_facing_reconstructed_hosts_do_not_duplicate_atomic_apply(tmp_path, monkeypatch):
    service, project, initial = _facing_workspace(tmp_path)
    node_id = str(service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].node_id)
    counts = {"apply": 0, "command": 0}
    apply = initial._apply_facing_production
    command = service.execute_cam_command
    monkeypatch.setattr(service, "execute_cam_command", lambda *a, **kw: (counts.__setitem__("command", counts["command"] + 1), command(*a, **kw))[1])
    for value, expected in ((4.0, 1), (3.5, 2)):
        workspace = CamWorkspace(service, lambda: project.manifest.source_files[0].source_id)
        workspace.refresh(("operation", node_id))
        original_apply = workspace._apply_facing_production
        monkeypatch.setattr(workspace, "_apply_facing_production", lambda *a, **kw: (counts.__setitem__("apply", counts["apply"] + 1), original_apply(*a, **kw))[1])
        host = _host(workspace)
        host.active_page._field_changed("stepover", str(value))
        host.active_page.footer.buttons[FunctionEditorAction.APPLY].click()
        assert counts == {"apply": expected, "command": expected}
        _dispose(host)
        workspace.close()
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert FacingParameters.from_operation_parameters(operation.parameters).stepover.value == 3.5
    assert counts == {"apply": 2, "command": 2}
    initial.close()


def test_stage13b_drilling_reconstructed_hosts_do_not_duplicate_atomic_apply(tmp_path, monkeypatch):
    service, project, initial, _viewer, _selected = drilling_workspace(tmp_path)
    node_id = str(service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].node_id)
    counts = {"apply": 0, "command": 0}
    apply = initial._apply_drilling_family_production
    command = service.execute_cam_command
    monkeypatch.setattr(service, "execute_cam_command", lambda *a, **kw: (counts.__setitem__("command", counts["command"] + 1), command(*a, **kw))[1])
    for value, expected in ((0.08, 1), (0.07, 2)):
        workspace = CamWorkspace(
            service, lambda: project.manifest.source_files[0].source_id,
            drilling_pick_provider=initial._drilling_pick_provider,
            drilling_resolver=initial._drilling_resolver,
        )
        workspace.refresh(("operation", node_id))
        original_apply = workspace._apply_drilling_family_production
        monkeypatch.setattr(workspace, "_apply_drilling_family_production", lambda *a, **kw: (counts.__setitem__("apply", counts["apply"] + 1), original_apply(*a, **kw))[1])
        host = _host(workspace)
        host.active_page._field_changed("feed_rate", str(value))
        host.active_page.footer.buttons[FunctionEditorAction.APPLY].click()
        assert counts == {"apply": expected, "command": expected}
        _dispose(host)
        workspace.close()
    initial.close()


def test_stage13b_lathe_face_reconstructed_workspaces_do_not_duplicate_apply_signal(monkeypatch):
    counts = {"signal": 0, "preview": 0, "presenter": 0}
    import hms_cadcam.ui.lathe_workspace as lathe_module
    original_preview = lathe_module.build_lathe_parameter_update_preview
    for speed, expected in ((925.0, 1), (875.0, 2)):
        workspace, presenter, _tool = workspace_for(LatheStrategyId.FACE)
        presenter.create_operation(LatheStrategyId.FACE)
        presenter.refresh()
        original_presenter = presenter.apply_parameter_changes
        workspace.parameters_apply_button.clicked.connect(lambda: counts.__setitem__("signal", counts["signal"] + 1))
        monkeypatch.setattr(lathe_module, "build_lathe_parameter_update_preview", lambda editor: (counts.__setitem__("preview", counts["preview"] + 1), original_preview(editor))[1])
        monkeypatch.setattr(presenter, "apply_parameter_changes", lambda *a, **kw: (counts.__setitem__("presenter", counts["presenter"] + 1), original_presenter(*a, **kw))[1])
        workspace.parameter_editor.editors["spindle_speed_rpm"].setValue(speed)
        workspace.parameters_apply_button.click()
        assert counts == {"signal": expected, "preview": expected, "presenter": expected}
        workspace.close()
