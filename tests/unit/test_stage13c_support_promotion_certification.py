"""Final exact-ID support promotion and documentation equality gates."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _stage13c_turning_runtime_fixtures import TURNING_STRATEGIES, enabled_flags, runtime_for
from hms_cadcam.ai_assist.production_bridge_registry import (
    certified_operation_ids,
    resolve_production_bridge,
    runtime_supported_operation_ids,
    stage13c_certified_operation_ids,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId


STAGE13B = ("facing_2_5d", "drilling_v1", "FACE")
STAGE13C = ("OD_ROUGH", "OD_FINISH", "ID_ROUGH", "ID_FINISH")


def test_authority_tuples_are_separate_and_combined_in_exact_order():
    assert certified_operation_ids() == STAGE13B
    assert stage13c_certified_operation_ids() == STAGE13C
    assert runtime_supported_operation_ids() == STAGE13B + STAGE13C


@pytest.mark.parametrize("strategy_id", TURNING_STRATEGIES, ids=lambda item: item.name)
def test_each_exact_turning_bridge_is_supported_only_with_dependency_flags(strategy_id):
    runtime, workspace = runtime_for(strategy_id)
    bridge = runtime.adapter.context.draft_bridge
    assert resolve_production_bridge(bridge).status == "FEATURE_DISABLED"
    resolution = resolve_production_bridge(bridge, flags=enabled_flags())
    assert resolution.status == "SUPPORTED"
    assert resolution.operation_id == strategy_id.name
    workspace.close()


@pytest.mark.parametrize("strategy_id", (LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD))
def test_threading_remains_unsupported_without_family_wildcard(strategy_id):
    runtime, workspace = runtime_for(LatheStrategyId.OD_ROUGH)
    runtime.adapter.context.draft_bridge.strategy_id = strategy_id
    resolution = resolve_production_bridge(
        runtime.adapter.context.draft_bridge, flags=enabled_flags()
    )
    assert resolution.status == "UNSUPPORTED_OPERATION"
    workspace.close()


def test_stage13c_coverage_matrix_equals_registry_and_preserves_stage13b_history():
    path = Path(__file__).parents[2] / "docs/STAGE13C_TURNING_ADVISOR_COVERAGE_MATRIX.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    supported = tuple(
        row["operation_id"]
        for row in data["entries"]
        if row["support_state"] == "SUPPORTED"
    )
    assert supported == runtime_supported_operation_ids()
    assert tuple(data["stage13b_certified_authority"]) == certified_operation_ids()
    assert tuple(data["stage13c_certified_authority"]) == stage13c_certified_operation_ids()
    assert data["unsupported"] == ["OD_THREAD", "ID_THREAD", "threading"]
