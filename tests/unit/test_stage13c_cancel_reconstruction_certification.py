"""Close-without-Apply discards the owner-bound turning advisor draft."""
from __future__ import annotations

import pytest

from _stage13c_turning_runtime_fixtures import (
    TURNING_STRATEGIES,
    bind_runtime,
    runtime_for,
    select_materials,
)


@pytest.mark.parametrize("strategy_id", TURNING_STRATEGIES, ids=lambda item: item.name)
def test_actual_workspace_close_discards_draft_and_reconstructs_persisted_values(
    strategy_id, monkeypatch
):
    runtime, workspace, session = bind_runtime(strategy_id)
    presenter = workspace.presenter
    assert presenter is not None
    persisted_before = dict(presenter.snapshot.operations[0].parameter_values)
    calls = []
    monkeypatch.setattr(
        presenter,
        "apply_parameter_changes",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    select_materials(workspace)
    workspace.advisor_panel.analyze.click()
    assert session.current_result is not None
    workspace.advisor_panel.field_checks["spindle_speed_rpm"].setChecked(True)
    workspace.advisor_panel.apply_selected.click()
    assert runtime.adapter.context.draft_bridge.capture_snapshot()["spindle_speed_rpm"] != persisted_before["spindle_speed_rpm"]
    workspace.close()
    assert calls == []
    assert not runtime.is_alive
    assert runtime.adapter.context.draft_bridge.editor is None
    assert dict(presenter.snapshot.operations[0].parameter_values) == persisted_before

    rebuilt, rebuilt_workspace = runtime_for(strategy_id)
    rebuilt_values = rebuilt.adapter.context.draft_bridge.capture_snapshot()
    assert rebuilt_values["spindle_speed_rpm"] == persisted_before["spindle_speed_rpm"]
    assert rebuilt.undo().status in {"UNDO_NOT_AVAILABLE", "STALE_UNDO_REFUSED"}
    rebuilt_workspace.close()
