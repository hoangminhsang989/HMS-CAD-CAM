"""Owner-local signal wiring never accumulates across panel or workspace lifecycles."""
from __future__ import annotations

import pytest

from _stage13c_turning_runtime_fixtures import (
    TURNING_STRATEGIES,
    bind_runtime,
    select_materials,
)


@pytest.mark.parametrize("strategy_id", TURNING_STRATEGIES, ids=lambda item: item.name)
def test_two_independent_owner_lifecycles_each_dispatch_one_action(strategy_id):
    old_session = None
    for _lifecycle in range(2):
        _runtime, workspace, session = bind_runtime(strategy_id)
        select_materials(workspace)
        workspace.advisor_panel.analyze.click()
        assert session.current_result is not None
        workspace.advisor_panel.field_checks["spindle_speed_rpm"].setChecked(True)
        workspace.advisor_panel.apply_selected.click()
        assert (session.analyze_actions, session.selective_apply_actions) == (1, 1)
        if old_session is not None:
            assert (old_session.analyze_actions, old_session.selective_apply_actions) == (1, 1)
        workspace.close()
        old_session = session


def test_repeated_panel_close_show_has_one_live_handler_per_click():
    runtime, workspace, session = bind_runtime(TURNING_STRATEGIES[0])
    select_materials(workspace)
    panel = workspace.advisor_panel
    panel.analyze.click()
    assert session.analyze_actions == 1 and session.current_result is not None
    panel.close()
    assert runtime.is_alive and session.current_result is None
    panel.show()
    panel.analyze.click()
    assert session.analyze_actions == 2 and session.current_result is not None
    workspace.close()
