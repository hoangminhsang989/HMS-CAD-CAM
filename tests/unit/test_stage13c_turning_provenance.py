"""Production-object provenance checks for Stage 13C WP1."""
from dataclasses import replace
import pytest

from hms_cadcam.ai_assist.turning_strategy_contracts import (
    DepthOfCutSemantics, TurningContractError, WorkpieceDiameterSource,
    resolve_turning_provenance,
)
from hms_cadcam.cam.lathe.capabilities import LatheToolCapabilityResolution
from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate, build_lathe_v1_defaults
from hms_cadcam.cam.lathe.types import (
    LatheParameterUnitKind, LatheStrategyId, LatheToolCapability,
)
from tests.unit._lathe_fixtures import capability_resolution
from tests.unit._lathe_toolpath_fixtures import stock_snapshot


@pytest.mark.parametrize(("strategy_id", "stock", "expected", "source", "capability", "depth"), (
    (LatheStrategyId.OD_ROUGH, stock_snapshot(outer_diameter_mm=100.0), 100.0, WorkpieceDiameterSource.STOCK_OUTER_DIAMETER, LatheToolCapability.OD_TURNING, DepthOfCutSemantics.RADIAL),
    (LatheStrategyId.OD_FINISH, stock_snapshot(outer_diameter_mm=100.0), 40.0, WorkpieceDiameterSource.TARGET_DIAMETER, LatheToolCapability.OD_TURNING, DepthOfCutSemantics.NOT_APPLICABLE),
    (LatheStrategyId.ID_ROUGH, stock_snapshot(outer_diameter_mm=100.0, inner_diameter_mm=10.0), 10.0, WorkpieceDiameterSource.STOCK_INNER_DIAMETER, LatheToolCapability.ID_TURNING, DepthOfCutSemantics.RADIAL),
    (LatheStrategyId.ID_FINISH, stock_snapshot(outer_diameter_mm=100.0, inner_diameter_mm=10.0), 20.0, WorkpieceDiameterSource.TARGET_DIAMETER, LatheToolCapability.ID_TURNING, DepthOfCutSemantics.NOT_APPLICABLE),
))
def test_exact_diameter_tool_feed_and_depth_provenance(strategy_id, stock, expected, source, capability, depth) -> None:
    result = resolve_turning_provenance(strategy_id, build_lathe_v1_defaults(strategy_id), stock, capability_resolution(capability))
    assert result.diameter_mm == expected
    assert result.diameter_source is source
    assert result.feed_unit is LatheParameterUnitKind.MM_PER_REVOLUTION
    assert result.depth_of_cut_semantics is depth


@pytest.mark.parametrize("strategy_id", (LatheStrategyId.ID_ROUGH, LatheStrategyId.ID_FINISH))
def test_internal_strategies_reject_missing_or_impossible_bore(strategy_id: LatheStrategyId) -> None:
    state = build_lathe_v1_defaults(strategy_id)
    tool = capability_resolution(LatheToolCapability.ID_TURNING)
    with pytest.raises(TurningContractError, match="MISSING_INTERNAL_BORE"):
        resolve_turning_provenance(strategy_id, state, stock_snapshot(), tool)
    impossible = state.with_updates((LatheParameterUpdate("target_diameter_mm", 5.0),))
    with pytest.raises(TurningContractError, match="IMPOSSIBLE_INTERNAL_DIAMETER"):
        resolve_turning_provenance(strategy_id, impossible, stock_snapshot(inner_diameter_mm=10.0), tool)


def test_cross_strategy_missing_stock_and_incompatible_tool_fail_closed() -> None:
    state = build_lathe_v1_defaults(LatheStrategyId.OD_ROUGH)
    correct = capability_resolution(LatheToolCapability.OD_TURNING)
    with pytest.raises(TurningContractError, match="CROSS_STRATEGY_PARAMETER_STATE"):
        resolve_turning_provenance(LatheStrategyId.OD_FINISH, state, stock_snapshot(), correct)
    with pytest.raises(TurningContractError, match="MISSING_STOCK"):
        resolve_turning_provenance(LatheStrategyId.OD_ROUGH, state, None, correct)  # type: ignore[arg-type]
    wrong = capability_resolution(LatheToolCapability.ID_TURNING)
    with pytest.raises(TurningContractError, match="INCOMPATIBLE_TOOL"):
        resolve_turning_provenance(LatheStrategyId.OD_ROUGH, state, stock_snapshot(), wrong)
    with pytest.raises(TurningContractError, match="INCOMPATIBLE_TOOL"):
        resolve_turning_provenance(LatheStrategyId.OD_ROUGH, state, stock_snapshot(), replace(correct, current=False))


def test_missing_tool_resolution_rejects_without_name_or_geometry_fallback() -> None:
    state = build_lathe_v1_defaults(LatheStrategyId.OD_ROUGH)
    reference = capability_resolution(LatheToolCapability.OD_TURNING).reference
    missing = LatheToolCapabilityResolution.missing(reference)
    with pytest.raises(TurningContractError, match="INCOMPATIBLE_TOOL"):
        resolve_turning_provenance(LatheStrategyId.OD_ROUGH, state, stock_snapshot(), missing)
