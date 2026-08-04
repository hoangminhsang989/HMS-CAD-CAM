"""Normal Apply remains the one authoritative production persistence boundary."""
from __future__ import annotations

import pytest

import hms_cadcam.ui.lathe_workspace as lathe_module
from _stage13c_turning_runtime_fixtures import (
    TURNING_STRATEGIES,
    bind_runtime,
    select_materials,
)


@pytest.mark.parametrize("strategy_id", TURNING_STRATEGIES, ids=lambda item: item.name)
def test_real_apply_button_dispatches_preview_presenter_facade_service_exactly_once(
    strategy_id, monkeypatch
):
    runtime, workspace, session = bind_runtime(strategy_id)
    presenter = workspace.presenter
    assert presenter is not None
    panel = workspace.advisor_panel
    select_materials(workspace)
    panel.analyze.click()
    result = session.current_result
    assert result is not None
    selected_value = result.final_recommendation["spindle_speed_rpm"]
    panel.field_checks["spindle_speed_rpm"].setChecked(True)

    calls = {"signal": 0, "preview": 0, "presenter": 0, "facade": 0, "service": 0}
    original_preview = lathe_module.build_lathe_parameter_update_preview
    original_presenter = presenter.apply_parameter_changes
    original_facade = presenter.facade.apply_parameter_changes
    original_execute = presenter.facade.service.execute
    workspace.parameters_apply_button.clicked.connect(
        lambda: calls.__setitem__("signal", calls["signal"] + 1)
    )
    monkeypatch.setattr(
        lathe_module,
        "build_lathe_parameter_update_preview",
        lambda editor: (
            calls.__setitem__("preview", calls["preview"] + 1),
            original_preview(editor),
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
    monkeypatch.setattr(
        presenter.facade,
        "apply_parameter_changes",
        lambda *args, **kwargs: (
            calls.__setitem__("facade", calls["facade"] + 1),
            original_facade(*args, **kwargs),
        )[1],
    )
    monkeypatch.setattr(
        presenter.facade.service,
        "execute",
        lambda *args, **kwargs: (
            calls.__setitem__("service", calls["service"] + 1),
            original_execute(*args, **kwargs),
        )[1],
    )

    panel.apply_selected.click()
    assert calls == {"signal": 0, "preview": 0, "presenter": 0, "facade": 0, "service": 0}
    monkeypatch.setattr(
        runtime,
        "_model_for_analyze",
        lambda: (_ for _ in ()).throw(AssertionError("normal Apply loaded the model")),
    )
    workspace.parameters_apply_button.click()
    assert calls == {"signal": 1, "preview": 1, "presenter": 1, "facade": 1, "service": 1}
    persisted = presenter.snapshot.operations[0]
    assert persisted.strategy_id is strategy_id
    assert dict(persisted.parameter_values)["spindle_speed_rpm"] == selected_value
    assert workspace.parameter_editor.updates() == ()
    workspace.close()
