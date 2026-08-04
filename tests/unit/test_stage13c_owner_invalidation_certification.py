"""Production ownership changes make Stage 13C results and QObjects inert."""
from __future__ import annotations

import pytest

from _stage13c_turning_runtime_fixtures import (
    TURNING_STRATEGIES,
    bind_runtime,
    select_materials,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId


@pytest.mark.parametrize("strategy_id", TURNING_STRATEGIES, ids=lambda item: item.name)
def test_workspace_close_invalidates_result_undo_and_editor_reference(strategy_id):
    runtime, workspace, session = bind_runtime(strategy_id)
    select_materials(workspace)
    workspace.advisor_panel.analyze.click()
    workspace.advisor_panel.field_checks["spindle_speed_rpm"].setChecked(True)
    workspace.advisor_panel.apply_selected.click()
    workspace.close()
    assert not runtime.is_alive
    assert runtime.adapter.context.draft_bridge.editor is None
    assert runtime.undo().status == "STALE_UNDO_REFUSED"
    assert session.current_result is None


def test_actual_strategy_change_and_presenter_unload_invalidate_owner():
    runtime, workspace, _session = bind_runtime(LatheStrategyId.OD_ROUGH)
    presenter = workspace.presenter
    assert presenter is not None
    active = presenter.snapshot.operations[0]
    presenter.change_strategy(active.ownership.operation_id, LatheStrategyId.FACE, active.revision)
    assert not runtime.is_alive
    assert workspace.turning_advisor_session is None or not runtime.is_alive
    workspace.bind_presenter(None)
    workspace.close()


def test_application_shutdown_is_idempotent_and_reaps_broker_worker():
    runtime, workspace, session = bind_runtime(LatheStrategyId.OD_ROUGH, use_worker=True)
    supervisor = runtime.supervisor
    assert supervisor is not None and supervisor.has_worker
    session.shutdown()
    session.shutdown()
    assert not runtime.is_alive and not supervisor.has_worker
    workspace.close()
