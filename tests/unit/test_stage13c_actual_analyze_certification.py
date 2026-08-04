"""Actual LatheWorkspace Analyze-button certification through the real worker."""
from __future__ import annotations

import pytest

from _stage13c_turning_runtime_fixtures import (
    TURNING_STRATEGIES,
    bind_runtime,
    select_materials,
)


@pytest.mark.parametrize("strategy_id", TURNING_STRATEGIES, ids=lambda item: item.name)
def test_actual_analyze_is_one_worker_request_and_zero_draft_or_apply(strategy_id, monkeypatch):
    runtime, workspace, session = bind_runtime(strategy_id, use_worker=True)
    panel = workspace.advisor_panel
    presenter = workspace.presenter
    assert presenter is not None and runtime.supervisor is not None
    before = runtime.adapter.context.draft_bridge.capture_snapshot()
    calls = {"worker": 0, "presenter": 0}
    original_worker = runtime.supervisor.recommend
    original_presenter = presenter.apply_parameter_changes
    monkeypatch.setattr(
        runtime.supervisor,
        "recommend",
        lambda *args, **kwargs: (
            calls.__setitem__("worker", calls["worker"] + 1),
            original_worker(*args, **kwargs),
        )[1],
    )
    monkeypatch.setattr(
        presenter,
        "apply_parameter_changes",
        lambda *args, **kwargs: (
            calls.__setitem__("presenter", calls["presenter"] + 1),
            original_presenter(*args, **kwargs),
        )[1],
    )
    select_materials(workspace)
    panel.analyze.click()
    result = session.current_result
    assert result is not None and result.status == "READY"
    assert result.snapshot is not None
    assert result.snapshot.strategy_id == strategy_id.name
    assert result.snapshot.material_token == "ISO_P"
    assert result.snapshot.tool_material == "CARBIDE"
    assert result.raw_recommendation and result.final_recommendation
    assert result.safe_ranges and result.confidence > 0
    assert result.stale_ownership_token == result.snapshot.input_digest
    assert calls == {"worker": 1, "presenter": 0}
    assert session.analyze_actions == 1
    assert runtime.adapter.context.draft_bridge.capture_snapshot() == before
    workspace.close()
    assert not runtime.is_alive
    assert not runtime.supervisor.has_worker
