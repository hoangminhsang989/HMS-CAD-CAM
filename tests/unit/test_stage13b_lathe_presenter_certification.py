"""Stage 13B real Lathe FACE Apply-button certification."""
from __future__ import annotations

from _lathe_ui_fixtures import workspace_for
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.lathe_workspace import build_lathe_parameter_update_preview


def test_stage13b_lathe_face_apply_button_dispatches_one_preview_and_one_presenter_call(monkeypatch):
    workspace, presenter, _tool = workspace_for(LatheStrategyId.FACE)
    presenter.create_operation(LatheStrategyId.FACE)
    presenter.refresh()
    editor = workspace.parameter_editor
    editor.editors["spindle_speed_rpm"].setValue(950.0)
    calls = {"workspace": 0, "preview": 0, "presenter": 0}
    original_workspace = workspace._apply_parameters
    original_preview = build_lathe_parameter_update_preview
    original_presenter = presenter.apply_parameter_changes

    def apply():
        calls["workspace"] += 1
        return original_workspace()

    def preview(value):
        calls["preview"] += 1
        return original_preview(value)

    def presenter_apply(*args, **kwargs):
        calls["presenter"] += 1
        return original_presenter(*args, **kwargs)

    monkeypatch.setattr(workspace, "_apply_parameters", apply)
    # Qt was connected to the original bound method; invoke its production slot
    # via click and certify the presenter boundary independently.
    monkeypatch.setattr("hms_cadcam.ui.lathe_workspace.build_lathe_parameter_update_preview", preview)
    monkeypatch.setattr(presenter, "apply_parameter_changes", presenter_apply)
    workspace.parameters_apply_button.click()
    assert calls["preview"] == 1
    assert calls["presenter"] == 1
    assert workspace.parameter_editor.updates() == ()
    workspace.close()


def test_stage13b_lathe_face_cancel_by_close_does_not_dispatch_presenter(monkeypatch):
    workspace, presenter, _tool = workspace_for(LatheStrategyId.FACE)
    presenter.create_operation(LatheStrategyId.FACE)
    presenter.refresh()
    calls = []
    monkeypatch.setattr(presenter, "apply_parameter_changes", lambda *args: calls.append(args))
    workspace.parameter_editor.editors["spindle_speed_rpm"].setValue(875.0)
    workspace.close()
    assert calls == []
