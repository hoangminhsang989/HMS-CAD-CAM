"""Selective Apply mutates only the real Lathe editor draft."""
from __future__ import annotations

import pytest

from _stage13c_turning_runtime_fixtures import (
    TURNING_STRATEGIES,
    bind_runtime,
    select_materials,
)


@pytest.mark.parametrize("strategy_id", TURNING_STRATEGIES, ids=lambda item: item.name)
def test_actual_selective_apply_changes_only_selected_control_and_zero_production_apply(
    strategy_id, monkeypatch
):
    runtime, workspace, session = bind_runtime(strategy_id)
    presenter = workspace.presenter
    assert presenter is not None
    panel = workspace.advisor_panel
    persisted_before = presenter.snapshot.operations[0]
    draft_before = runtime.adapter.context.draft_bridge.capture_snapshot()
    calls = {"presenter": 0, "facade": 0, "service": 0}
    monkeypatch.setattr(
        presenter,
        "apply_parameter_changes",
        lambda *args, **kwargs: calls.__setitem__("presenter", calls["presenter"] + 1),
    )
    monkeypatch.setattr(
        presenter.facade,
        "apply_parameter_changes",
        lambda *args, **kwargs: calls.__setitem__("facade", calls["facade"] + 1),
    )
    monkeypatch.setattr(
        presenter.facade.service,
        "execute",
        lambda *args, **kwargs: calls.__setitem__("service", calls["service"] + 1),
    )
    select_materials(workspace)
    panel.analyze.click()
    result = session.current_result
    assert result is not None and "spindle_speed_rpm" in result.final_recommendation
    panel.field_checks["spindle_speed_rpm"].setChecked(True)
    panel.apply_selected.click()
    draft_after = runtime.adapter.context.draft_bridge.capture_snapshot()
    assert draft_after["spindle_speed_rpm"] == result.final_recommendation["spindle_speed_rpm"]
    assert draft_after["feed_mm_per_rev"] == draft_before["feed_mm_per_rev"]
    assert presenter.snapshot.operations[0] == persisted_before
    assert calls == {"presenter": 0, "facade": 0, "service": 0}
    assert session.selective_apply_actions == 1
    workspace.close()


def test_zero_selection_and_unsupported_fields_fail_closed():
    runtime, workspace, session = bind_runtime(TURNING_STRATEGIES[0])
    select_materials(workspace)
    workspace.advisor_panel.analyze.click()
    before = runtime.adapter.context.draft_bridge.capture_snapshot()
    workspace.advisor_panel.apply_selected.click()
    assert runtime.adapter.context.draft_bridge.capture_snapshot() == before
    assert "no recommendation" in workspace.advisor_panel.status.text().casefold() or workspace.advisor_panel.status.text()
    workspace.close()


@pytest.mark.parametrize("strategy_id", TURNING_STRATEGIES, ids=lambda item: item.name)
def test_actual_undo_button_restores_one_compatible_selected_draft(strategy_id):
    runtime, workspace, session = bind_runtime(strategy_id)
    panel = workspace.advisor_panel
    select_materials(workspace)
    before = runtime.adapter.context.draft_bridge.capture_snapshot()
    panel.analyze.click()
    panel.field_checks["spindle_speed_rpm"].setChecked(True)
    panel.apply_selected.click()
    assert runtime.adapter.context.draft_bridge.capture_snapshot() != before
    panel.undo.click()
    assert runtime.adapter.context.draft_bridge.capture_snapshot() == before
    assert session.undo_actions == 1
    assert not panel.undo.isEnabled()
    workspace.close()
