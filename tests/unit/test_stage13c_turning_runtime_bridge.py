from __future__ import annotations

import pytest

from _stage13c_turning_runtime_fixtures import runtime_for
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags


@pytest.mark.parametrize("strategy_id", [LatheStrategyId.OD_ROUGH, LatheStrategyId.OD_FINISH, LatheStrategyId.ID_ROUGH, LatheStrategyId.ID_FINISH])
def test_explicit_analyze_maps_only_allowlisted_fields(strategy_id):
    runtime, workspace = runtime_for(strategy_id)
    before = runtime.adapter.context.draft_bridge.capture_snapshot()
    result = runtime.analyze()
    assert result.status == "READY"
    assert {"spindle_speed_rpm", "feed_mm_per_rev"} <= result.final_recommendation.keys()
    assert "target_diameter_mm" not in result.final_recommendation
    assert result.retained_unsupported
    assert runtime.adapter.context.draft_bridge.capture_snapshot() == before
    workspace.deleteLater()


def test_flag_off_exposes_no_analyze_route():
    runtime, workspace = runtime_for()
    runtime.flags = UiFeatureFlags(
        {
            UiFeatureFlag.OFFLINE_CAM_AI_ASSIST_13A: True,
            UiFeatureFlag.OFFLINE_CAM_AI_PARAMETER_ADVISOR_13B: True,
            UiFeatureFlag.OFFLINE_CAM_AI_TURNING_COVERAGE_13C: False,
        }
    )
    assert runtime.analyze().status == "FEATURE_DISABLED"
    workspace.deleteLater()
