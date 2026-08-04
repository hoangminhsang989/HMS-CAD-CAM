"""Fail-closed Stage 13C WP1 contracts for four production Lathe strategies.

This module records proven production metadata only. It does not register new
Stage 13B bridges, load a model, start a worker, or mutate an editor/project.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Mapping

from hms_cadcam.cam.lathe.capabilities import LatheToolCapabilityResolution
from hms_cadcam.cam.lathe.parameters import LatheParameterState, lathe_parameter_schema
from hms_cadcam.cam.lathe.toolpath.stock import LatheStockSnapshotV1
from hms_cadcam.cam.lathe.types import (
    LatheParameterUnitKind, LatheStrategyId, LatheToolCapability,
)


class TurningContractStatus(StrEnum):
    CONTRACT_LOCKED = "CONTRACT_LOCKED"


class WorkpieceDiameterSource(StrEnum):
    STOCK_OUTER_DIAMETER = "LatheStockSnapshotV1.outer_diameter_mm"
    STOCK_INNER_DIAMETER = "LatheStockSnapshotV1.inner_diameter_mm"
    TARGET_DIAMETER = "LatheParameterState.target_diameter_mm"


class DepthOfCutSemantics(StrEnum):
    RADIAL = "RADIAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class AdvisorFieldMapping:
    advisor_field: str
    production_descriptor: str

    def __post_init__(self) -> None:
        if not self.advisor_field or not self.production_descriptor:
            raise ValueError("Advisor field mappings require non-empty IDs")


@dataclass(frozen=True, slots=True)
class TurningStrategyContract:
    strategy_id: LatheStrategyId
    status: TurningContractStatus
    operation_type: str
    editor_type: str
    presenter_apply: str
    required_descriptors: tuple[str, ...]
    diameter_source: WorkpieceDiameterSource
    required_tool_capability: LatheToolCapability
    field_mappings: tuple[AdvisorFieldMapping, ...]
    depth_of_cut_semantics: DepthOfCutSemantics
    validation_helper: str
    draft_setter: str
    runtime_certified: bool = False

    def __post_init__(self) -> None:
        if self.strategy_id not in _APPROVED_STRATEGIES:
            raise ValueError("Stage 13C contract strategy is outside approved scope")
        if self.status is not TurningContractStatus.CONTRACT_LOCKED:
            raise ValueError("Stage 13C WP1 contracts must remain CONTRACT_LOCKED")
        if self.runtime_certified:
            raise ValueError("Stage 13C WP1 cannot certify runtime support")
        if len(set(self.required_descriptors)) != len(self.required_descriptors):
            raise ValueError("Required descriptors must be unique")
        outputs = tuple(item.advisor_field for item in self.field_mappings)
        targets = tuple(item.production_descriptor for item in self.field_mappings)
        if len(set(outputs)) != len(outputs) or len(set(targets)) != len(targets):
            raise ValueError("Advisor field mappings must be one-to-one")
        if set(targets) - set(self.required_descriptors):
            raise ValueError("Mapped fields must be production descriptors")
        has_depth = "depth_of_cut_mm" in outputs
        if has_depth != (self.depth_of_cut_semantics is DepthOfCutSemantics.RADIAL):
            raise ValueError("Depth mapping and semantics are inconsistent")

    @property
    def allowed_advisor_fields(self) -> frozenset[str]:
        return frozenset(item.advisor_field for item in self.field_mappings)


@dataclass(frozen=True, slots=True)
class TurningProvenanceResolution:
    strategy_id: LatheStrategyId
    diameter_mm: float
    diameter_source: WorkpieceDiameterSource
    tool_resolution: LatheToolCapabilityResolution
    feed_unit: LatheParameterUnitKind
    depth_of_cut_semantics: DepthOfCutSemantics


@dataclass(frozen=True, slots=True)
class ProposedFieldPartition:
    accepted: tuple[tuple[str, object], ...]
    retained_unsupported: tuple[tuple[str, object], ...]
    warnings: tuple[str, ...]


class TurningContractError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_APPROVED_STRATEGIES = (
    LatheStrategyId.OD_ROUGH,
    LatheStrategyId.OD_FINISH,
    LatheStrategyId.ID_ROUGH,
    LatheStrategyId.ID_FINISH,
)
_SPINDLE = AdvisorFieldMapping("spindle_rpm", "spindle_speed_rpm")
_FEED = AdvisorFieldMapping("feed_per_revolution_mm", "feed_mm_per_rev")
_DEPTH = AdvisorFieldMapping("depth_of_cut_mm", "max_depth_of_cut_mm")


def _contract(
    strategy_id: LatheStrategyId,
    diameter_source: WorkpieceDiameterSource,
    tool_capability: LatheToolCapability,
    *,
    roughing: bool,
) -> TurningStrategyContract:
    descriptors = tuple(
        item.parameter_id for item in lathe_parameter_schema(strategy_id).descriptors
    )
    return TurningStrategyContract(
        strategy_id=strategy_id,
        status=TurningContractStatus.CONTRACT_LOCKED,
        operation_type="LatheOperationState/LatheOperationSnapshot",
        editor_type="LatheParameterEditor",
        presenter_apply="LatheQtPresenter.apply_parameter_changes",
        required_descriptors=descriptors,
        diameter_source=diameter_source,
        required_tool_capability=tool_capability,
        field_mappings=(_SPINDLE, _FEED, _DEPTH) if roughing else (_SPINDLE, _FEED),
        depth_of_cut_semantics=(
            DepthOfCutSemantics.RADIAL
            if roughing
            else DepthOfCutSemantics.NOT_APPLICABLE
        ),
        validation_helper="build_lathe_parameter_update_preview",
        draft_setter="LatheParameterEditorDraftBridge.set_draft_field",
    )


TURNING_STRATEGY_CONTRACTS: tuple[TurningStrategyContract, ...] = (
    _contract(
        LatheStrategyId.OD_ROUGH,
        WorkpieceDiameterSource.STOCK_OUTER_DIAMETER,
        LatheToolCapability.OD_TURNING,
        roughing=True,
    ),
    _contract(
        LatheStrategyId.OD_FINISH,
        WorkpieceDiameterSource.TARGET_DIAMETER,
        LatheToolCapability.OD_TURNING,
        roughing=False,
    ),
    _contract(
        LatheStrategyId.ID_ROUGH,
        WorkpieceDiameterSource.STOCK_INNER_DIAMETER,
        LatheToolCapability.ID_TURNING,
        roughing=True,
    ),
    _contract(
        LatheStrategyId.ID_FINISH,
        WorkpieceDiameterSource.TARGET_DIAMETER,
        LatheToolCapability.ID_TURNING,
        roughing=False,
    ),
)
_BY_STRATEGY: Mapping[LatheStrategyId, TurningStrategyContract] = MappingProxyType(
    {item.strategy_id: item for item in TURNING_STRATEGY_CONTRACTS}
)
if tuple(_BY_STRATEGY) != _APPROVED_STRATEGIES:
    raise RuntimeError("Stage 13C contracts must contain the exact approved order")


def turning_strategy_contract(strategy_id: LatheStrategyId) -> TurningStrategyContract:
    """Return one approved contract; unknown, FACE and threading fail closed."""
    if not isinstance(strategy_id, LatheStrategyId):
        raise TurningContractError("UNKNOWN_STRATEGY")
    try:
        return _BY_STRATEGY[strategy_id]
    except KeyError as error:
        raise TurningContractError("UNSUPPORTED_STRATEGY") from error


def resolve_turning_provenance(
    strategy_id: LatheStrategyId,
    parameter_state: LatheParameterState,
    stock: LatheStockSnapshotV1,
    tool_resolution: LatheToolCapabilityResolution,
) -> TurningProvenanceResolution:
    """Resolve production diameter/tool evidence without a generic fallback."""
    contract = turning_strategy_contract(strategy_id)
    if not isinstance(parameter_state, LatheParameterState):
        raise TurningContractError("MISSING_PARAMETER_STATE")
    if parameter_state.strategy_id is not strategy_id:
        raise TurningContractError("CROSS_STRATEGY_PARAMETER_STATE")
    if not isinstance(stock, LatheStockSnapshotV1):
        raise TurningContractError("MISSING_STOCK")
    if not isinstance(tool_resolution, LatheToolCapabilityResolution):
        raise TurningContractError("MISSING_TOOL_RESOLUTION")
    if (
        not tool_resolution.exists
        or not tool_resolution.current
        or contract.required_tool_capability not in tool_resolution.capabilities
    ):
        raise TurningContractError("INCOMPATIBLE_TOOL")

    target = _positive_parameter(parameter_state, "target_diameter_mm")
    if target >= stock.outer_diameter_mm:
        raise TurningContractError("TARGET_OUTSIDE_STOCK")
    if strategy_id in {LatheStrategyId.ID_ROUGH, LatheStrategyId.ID_FINISH}:
        if stock.inner_diameter_mm <= 0.0:
            raise TurningContractError("MISSING_INTERNAL_BORE")
        if target <= stock.inner_diameter_mm:
            raise TurningContractError("IMPOSSIBLE_INTERNAL_DIAMETER")

    if contract.diameter_source is WorkpieceDiameterSource.STOCK_OUTER_DIAMETER:
        diameter = stock.outer_diameter_mm
    elif contract.diameter_source is WorkpieceDiameterSource.STOCK_INNER_DIAMETER:
        diameter = stock.inner_diameter_mm
    else:
        diameter = target
    if not isfinite(diameter) or diameter <= 0.0:
        raise TurningContractError("MISSING_WORKPIECE_DIAMETER")
    return TurningProvenanceResolution(
        strategy_id,
        diameter,
        contract.diameter_source,
        tool_resolution,
        LatheParameterUnitKind.MM_PER_REVOLUTION,
        contract.depth_of_cut_semantics,
    )


def partition_proposed_fields(
    strategy_id: LatheStrategyId, proposed: Mapping[str, object]
) -> ProposedFieldPartition:
    """Retain and warn on unsupported outputs; never apply them."""
    contract = turning_strategy_contract(strategy_id)
    if not isinstance(proposed, Mapping):
        raise TypeError("proposed fields must be a mapping")
    accepted = tuple(
        (key, proposed[key])
        for key in sorted(proposed)
        if key in contract.allowed_advisor_fields
    )
    unsupported = tuple(
        (key, proposed[key])
        for key in sorted(proposed)
        if key not in contract.allowed_advisor_fields
    )
    return ProposedFieldPartition(
        accepted,
        unsupported,
        tuple(f"UNSUPPORTED_PROPOSED_FIELD:{key}" for key, _value in unsupported),
    )


def _positive_parameter(state: LatheParameterState, parameter_id: str) -> float:
    try:
        value = float(state.value(parameter_id))
    except (KeyError, TypeError, ValueError) as error:
        raise TurningContractError("MISSING_WORKPIECE_DIAMETER") from error
    if not isfinite(value) or value <= 0.0:
        raise TurningContractError("MISSING_WORKPIECE_DIAMETER")
    return value


__all__ = [
    "AdvisorFieldMapping", "DepthOfCutSemantics", "ProposedFieldPartition",
    "TURNING_STRATEGY_CONTRACTS", "TurningContractError",
    "TurningContractStatus", "TurningProvenanceResolution",
    "TurningStrategyContract", "WorkpieceDiameterSource",
    "partition_proposed_fields", "resolve_turning_provenance",
    "turning_strategy_contract",
]
