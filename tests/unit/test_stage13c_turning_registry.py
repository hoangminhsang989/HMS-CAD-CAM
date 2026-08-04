from __future__ import annotations

from _stage13c_turning_runtime_fixtures import enabled_flags, runtime_for
from hms_cadcam.ai_assist.production_bridge_registry import certified_operation_ids, resolve_production_bridge, turning_operation_ids


def test_stage13b_certified_ids_and_wp2_ids_are_separate():
    assert certified_operation_ids() == ("facing_2_5d", "drilling_v1", "FACE")
    assert turning_operation_ids() == ("OD_ROUGH", "OD_FINISH", "ID_ROUGH", "ID_FINISH")


def test_turning_registry_is_flag_gated_and_exact_id_supported():
    runtime, workspace = runtime_for()
    bridge = runtime.adapter.context.draft_bridge
    off = resolve_production_bridge(bridge)
    on = resolve_production_bridge(bridge, flags=enabled_flags())
    assert off.status == "FEATURE_DISABLED" and off.bridge is None
    assert on.status == "SUPPORTED"
    workspace.deleteLater()
