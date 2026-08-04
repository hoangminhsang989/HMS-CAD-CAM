from __future__ import annotations

from _stage13c_turning_runtime_fixtures import runtime_for


def test_selective_apply_is_draft_only_and_preserves_unselected_fields():
    runtime, workspace = runtime_for()
    result = runtime.analyze()
    before = dict(runtime.adapter.context.draft_bridge.capture_snapshot())
    applied = runtime.selective_apply(result, frozenset({"spindle_speed_rpm"}))
    after = runtime.adapter.context.draft_bridge.capture_snapshot()
    assert applied.status == "APPLIED"
    assert after["feed_mm_per_rev"] == before["feed_mm_per_rev"]
    assert after["spindle_speed_rpm"] == result.final_recommendation["spindle_speed_rpm"]
    workspace.deleteLater()
