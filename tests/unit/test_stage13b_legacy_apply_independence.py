"""Traditional Apply remains usable without any Stage 13B runtime object."""
from __future__ import annotations

from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.function_editor.model import FunctionEditorAction
from _lathe_ui_fixtures import workspace_for
from test_drilling_ui import _workspace as drilling_workspace
from test_stage13b_camworkspace_dispatch_certification import _facing_workspace
from hms_cadcam.ui.function_editor.host import FunctionEditorHost


def _host(workspace):
    host = FunctionEditorHost(workspace.editor, workspace.tree, lambda: None,
        production_provider=workspace.production_function_editor_session,
        selection_exists=workspace.selection_exists)
    assert host.active_page is not None
    return host


def test_facing_legacy_apply_works_without_stage13b_runtime(tmp_path, monkeypatch):
    service, _project, workspace = _facing_workspace(tmp_path)
    monkeypatch.setattr("hms_cadcam.ai_assist.model_loader.load_canonical_model", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("model loaded")))
    host = _host(workspace)
    host.active_page._field_changed("stepover", "4.0")
    host.active_page.footer.buttons[FunctionEditorAction.APPLY].click()
    assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    host.close(); workspace.close()


def test_drilling_legacy_apply_works_without_stage13b_runtime(tmp_path, monkeypatch):
    service, _project, workspace, _viewer, _selected = drilling_workspace(tmp_path)
    monkeypatch.setattr("hms_cadcam.ai_assist.model_loader.load_canonical_model", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("model loaded")))
    host = _host(workspace)
    host.active_page._field_changed("feed_rate", "0.08")
    host.active_page.footer.buttons[FunctionEditorAction.APPLY].click()
    assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    host.close(); workspace.close()


def test_lathe_legacy_apply_works_without_stage13b_runtime(monkeypatch):
    workspace, presenter, _tool = workspace_for(LatheStrategyId.FACE)
    presenter.create_operation(LatheStrategyId.FACE); presenter.refresh()
    monkeypatch.setattr("hms_cadcam.ai_assist.model_loader.load_canonical_model", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("model loaded")))
    workspace.parameter_editor.editors["spindle_speed_rpm"].setValue(900.0)
    workspace.parameters_apply_button.click()
    assert presenter.snapshot.operations
    workspace.close()
