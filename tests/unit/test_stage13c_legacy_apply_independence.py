"""Traditional Lathe Apply has no Stage 13C runtime dependency."""
from __future__ import annotations

import pytest

from _lathe_ui_fixtures import workspace_for
from _stage13c_turning_runtime_fixtures import TURNING_STRATEGIES
from hms_cadcam.cam.lathe.types import LatheStrategyId


@pytest.mark.parametrize(
    "strategy_id",
    (LatheStrategyId.FACE, *TURNING_STRATEGIES),
    ids=lambda item: item.name,
)
def test_lathe_legacy_apply_uses_one_presenter_call_and_zero_ai_runtime(
    strategy_id, monkeypatch
):
    workspace, presenter, _tool = workspace_for(strategy_id)
    presenter.create_operation(strategy_id)
    presenter.refresh()
    calls = {"presenter": 0, "adapter": 0, "model": 0, "worker": 0}
    original = presenter.apply_parameter_changes
    monkeypatch.setattr(
        presenter,
        "apply_parameter_changes",
        lambda *args, **kwargs: (
            calls.__setitem__("presenter", calls["presenter"] + 1),
            original(*args, **kwargs),
        )[1],
    )
    monkeypatch.setattr(
        "hms_cadcam.ai_assist.model_loader.load_canonical_model",
        lambda *_args, **_kwargs: calls.__setitem__("model", calls["model"] + 1),
    )
    workspace.parameter_editor.editors["spindle_speed_rpm"].setValue(901.0)
    workspace.parameters_apply_button.click()
    assert calls == {"presenter": 1, "adapter": 0, "model": 0, "worker": 0}
    assert workspace.turning_advisor_session is None
    workspace.close()
