from __future__ import annotations

from dataclasses import fields
import json

import pytest

from _stage13c_turning_runtime_fixtures import runtime_for
from hms_cadcam.cam.lathe.types import LatheStrategyId


@pytest.mark.parametrize(
    "strategy_id,capability,diameter",
    [
        (LatheStrategyId.OD_ROUGH, "OD_TURNING", 100.0),
        (LatheStrategyId.OD_FINISH, "OD_TURNING", 40.0),
        (LatheStrategyId.ID_ROUGH, "ID_TURNING", 10.0),
        (LatheStrategyId.ID_FINISH, "ID_TURNING", 20.0),
    ],
)
def test_adapter_reads_actual_production_state(strategy_id, capability, diameter):
    runtime, workspace = runtime_for(strategy_id)
    snapshot = runtime.adapter.snapshot()
    assert snapshot.strategy_id == strategy_id.name
    assert snapshot.compatible_tool_capability == capability
    assert snapshot.active_diameter_mm == diameter
    assert snapshot.parameter_state_digest
    assert snapshot.stock_digest and snapshot.tool_resolution_digest
    assert all("QObject" not in type(getattr(snapshot, item.name)).__name__ for item in fields(snapshot))
    json.dumps(snapshot.to_dict())
    workspace.deleteLater()


def test_missing_material_is_explicit_and_fail_closed():
    runtime, workspace = runtime_for(material_token=None)
    snapshot = runtime.adapter.snapshot()
    assert "MISSING_WORKPIECE_MATERIAL" in snapshot.warnings
    assert runtime.analyze().status == "MISSING_WORKPIECE_MATERIAL"
    workspace.deleteLater()
