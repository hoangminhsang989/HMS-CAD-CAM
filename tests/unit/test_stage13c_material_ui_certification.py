"""Stage 13C explicit, session-only production material controls."""
from __future__ import annotations

import pytest

from _stage13c_turning_runtime_fixtures import (
    TURNING_STRATEGIES,
    bind_runtime,
    select_materials,
)


@pytest.mark.parametrize("strategy_id", TURNING_STRATEGIES, ids=lambda item: item.name)
def test_exact_material_tokens_default_empty_and_change_invalidates_result(strategy_id):
    runtime, workspace, session = bind_runtime(strategy_id)
    panel = workspace.advisor_panel
    assert panel.selected_workpiece_material() is None
    assert panel.selected_tool_material() is None
    assert tuple(panel.workpiece_material.itemData(i) for i in range(1, 7)) == (
        "ISO_P", "ISO_M", "ISO_K", "ISO_N", "ISO_S", "ISO_H"
    )
    assert tuple(panel.tool_material.itemData(i) for i in range(1, 3)) == (
        "HSS", "CARBIDE"
    )
    select_materials(workspace)
    panel.analyze.click()
    assert session.current_result is not None
    panel.workpiece_material.setCurrentIndex(panel.workpiece_material.findData("ISO_M"))
    assert session.current_result is None
    assert runtime.adapter.context.material_token == "ISO_M"
    assert runtime.adapter.context.tool_material == "CARBIDE"
    workspace.close()


def test_selector_changes_do_not_reload_model_or_start_worker(monkeypatch):
    runtime, workspace, _session = bind_runtime(TURNING_STRATEGIES[0])
    calls = {"model": 0, "worker": 0}
    monkeypatch.setattr(
        runtime,
        "_model_for_analyze",
        lambda: calls.__setitem__("model", calls["model"] + 1),
    )
    select_materials(workspace)
    assert calls == {"model": 0, "worker": 0}
    workspace.close()


def test_missing_material_fails_closed_before_worker_request(monkeypatch):
    runtime, workspace, session = bind_runtime(TURNING_STRATEGIES[0])
    calls = []
    monkeypatch.setattr(runtime, "_model_for_analyze", lambda: calls.append("model"))
    workspace.advisor_panel.analyze.click()
    assert session.current_result is None
    assert runtime.adapter.context.material_token is None
    assert runtime.adapter.context.tool_material is None
    assert calls == []
    workspace.close()
