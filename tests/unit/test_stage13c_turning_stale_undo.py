from __future__ import annotations

from _stage13c_turning_runtime_fixtures import runtime_for


def test_stale_analyze_result_is_discarded_after_draft_change():
    runtime, workspace = runtime_for()
    result = runtime.analyze()
    runtime.adapter.context.draft_bridge.set_draft_field("spindle_speed_rpm", 1100.0)
    assert runtime.selective_apply(result, frozenset({"feed_mm_per_rev"})).status == "STALE_RESULT_DISCARDED"
    workspace.deleteLater()


def test_one_compatible_undo_then_unavailable():
    runtime, workspace = runtime_for()
    result = runtime.analyze()
    assert runtime.selective_apply(result, frozenset({"spindle_speed_rpm"})).status == "APPLIED"
    assert runtime.undo().status == "UNDONE"
    assert runtime.undo().status == "UNDO_NOT_AVAILABLE"
    workspace.deleteLater()


def test_later_draft_mutation_refuses_undo():
    runtime, workspace = runtime_for()
    result = runtime.analyze()
    assert runtime.selective_apply(result, frozenset({"spindle_speed_rpm"})).status == "APPLIED"
    runtime.adapter.context.draft_bridge.set_draft_field("feed_mm_per_rev", 0.3)
    assert runtime.undo().status == "STALE_UNDO_REFUSED"
    workspace.deleteLater()
