"""Exact immutable contract authority for Stage 13C WP1."""
from pathlib import Path
import pytest

from hms_cadcam.ai_assist.production_bridge_registry import certified_operation_ids
from hms_cadcam.ai_assist.turning_strategy_contracts import (
    DepthOfCutSemantics, TURNING_STRATEGY_CONTRACTS, TurningContractError,
    TurningContractStatus, partition_proposed_fields, turning_strategy_contract,
)
from hms_cadcam.cam.lathe.parameters import lathe_parameter_schema
from hms_cadcam.cam.lathe.types import (
    LatheParameterUnitKind, LatheStrategyId, LatheToolCapability,
)

APPROVED = (
    LatheStrategyId.OD_ROUGH, LatheStrategyId.OD_FINISH,
    LatheStrategyId.ID_ROUGH, LatheStrategyId.ID_FINISH,
)


def test_exact_four_contracts_are_unique_deterministic_and_not_supported() -> None:
    assert tuple(item.strategy_id for item in TURNING_STRATEGY_CONTRACTS) == APPROVED
    assert len({item.strategy_id for item in TURNING_STRATEGY_CONTRACTS}) == 4
    assert all(item.status is TurningContractStatus.CONTRACT_LOCKED and not item.runtime_certified for item in TURNING_STRATEGY_CONTRACTS)


@pytest.mark.parametrize("strategy_id", APPROVED)
def test_contract_descriptors_and_units_are_actual_production_schema(strategy_id: LatheStrategyId) -> None:
    contract = turning_strategy_contract(strategy_id)
    schema = lathe_parameter_schema(strategy_id)
    assert contract.required_descriptors == tuple(item.parameter_id for item in schema.descriptors)
    units = {item.parameter_id: item.unit_kind for item in schema.descriptors}
    assert units["spindle_speed_rpm"] is LatheParameterUnitKind.RPM
    assert units["feed_mm_per_rev"] is LatheParameterUnitKind.MM_PER_REVOLUTION
    assert contract.presenter_apply == "LatheQtPresenter.apply_parameter_changes"
    assert contract.validation_helper == "build_lathe_parameter_update_preview"
    assert contract.draft_setter == "LatheParameterEditorDraftBridge.set_draft_field"


def test_tool_capability_and_rough_only_radial_depth_are_exact() -> None:
    odr, odf, idr, idf = TURNING_STRATEGY_CONTRACTS
    assert (odr.required_tool_capability, odf.required_tool_capability) == (LatheToolCapability.OD_TURNING,) * 2
    assert (idr.required_tool_capability, idf.required_tool_capability) == (LatheToolCapability.ID_TURNING,) * 2
    assert odr.depth_of_cut_semantics is DepthOfCutSemantics.RADIAL
    assert idr.depth_of_cut_semantics is DepthOfCutSemantics.RADIAL
    assert "depth_of_cut_mm" in odr.allowed_advisor_fields
    assert "depth_of_cut_mm" in idr.allowed_advisor_fields
    assert odf.depth_of_cut_semantics is DepthOfCutSemantics.NOT_APPLICABLE
    assert idf.depth_of_cut_semantics is DepthOfCutSemantics.NOT_APPLICABLE
    assert "depth_of_cut_mm" not in odf.allowed_advisor_fields
    assert "depth_of_cut_mm" not in idf.allowed_advisor_fields


def test_unsupported_proposed_field_is_retained_warned_and_not_silenced() -> None:
    result = partition_proposed_fields(LatheStrategyId.OD_FINISH, {"spindle_rpm": 1200.0, "depth_of_cut_mm": 2.0})
    assert result.accepted == (("spindle_rpm", 1200.0),)
    assert result.retained_unsupported == (("depth_of_cut_mm", 2.0),)
    assert result.warnings == ("UNSUPPORTED_PROPOSED_FIELD:depth_of_cut_mm",)


@pytest.mark.parametrize("strategy_id", (LatheStrategyId.FACE, LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD))
def test_face_and_threading_fail_closed_without_stage13b_change(strategy_id: LatheStrategyId) -> None:
    with pytest.raises(TurningContractError, match="UNSUPPORTED_STRATEGY"):
        turning_strategy_contract(strategy_id)
    assert certified_operation_ids() == ("facing_2_5d", "drilling_v1", "FACE")


def test_unknown_strategy_fails_closed_and_module_has_no_runtime_integration() -> None:
    with pytest.raises(TurningContractError, match="UNKNOWN_STRATEGY"):
        turning_strategy_contract("lathe.unknown.v1")  # type: ignore[arg-type]
    source = Path("src/hms_cadcam/ai_assist/turning_strategy_contracts.py").read_text(encoding="utf-8")
    assert "production_bridge_registry" not in source
    assert "CuttingWorker" not in source
    assert "load_model" not in source
