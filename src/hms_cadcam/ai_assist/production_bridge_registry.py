"""Deterministic, fail-closed registry for certified and WP2 bridge identities."""
from __future__ import annotations

from dataclasses import dataclass

from hms_cadcam.ai_assist.production_draft_bridge import (
    DrillingFamilyEditorDraftBridge,
    FacingEditorDraftBridge,
    LatheParameterEditorDraftBridge,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.function_editor.strategies.common_drilling import (
    DrillingFamilyEditorContext,
)
from hms_cadcam.ui.function_editor.strategies.common_milling import FacingEditorContext
from hms_cadcam.ui.lathe_workspace import LatheParameterEditor


CERTIFIED_OPERATION_IDS = ("facing_2_5d", "drilling_v1", "FACE")
TURNING_OPERATION_IDS = ("OD_ROUGH", "OD_FINISH", "ID_ROUGH", "ID_FINISH")


@dataclass(frozen=True, slots=True)
class BridgeResolution:
    bridge: object | None
    status: str
    operation_id: str | None = None


def certified_operation_ids() -> tuple[str, ...]:
    """Return the canonical production strategy identities in stable order."""
    return CERTIFIED_OPERATION_IDS


def turning_operation_ids() -> tuple[str, ...]:
    """Return the exact Stage 13C turning identities in stable order."""

    return TURNING_OPERATION_IDS


def stage13c_certified_operation_ids() -> tuple[str, ...]:
    """Return only the exact turning identities certified by Stage 13C WP3."""

    return TURNING_OPERATION_IDS


def runtime_supported_operation_ids() -> tuple[str, ...]:
    """Combine current authorities without rewriting the Stage 13B tuple."""

    return CERTIFIED_OPERATION_IDS + TURNING_OPERATION_IDS


def resolve_production_bridge(
    target: object, *, flags: UiFeatureFlags | None = None
) -> BridgeResolution:
    """Resolve only a concrete bridge for an exact certified production identity."""
    if isinstance(target, FacingEditorDraftBridge):
        context = target.context
        operation_id = getattr(getattr(context, "operation", None), "strategy_key", None)
        if operation_id == "facing_2_5d":
            return BridgeResolution(target, "SUPPORTED", operation_id)
        return BridgeResolution(None, "UNSUPPORTED_OPERATION", operation_id)
    if isinstance(target, DrillingFamilyEditorDraftBridge):
        context = target.context
        operation_id = getattr(getattr(context, "operation", None), "strategy_key", None)
        if operation_id == "drilling_v1":
            return BridgeResolution(target, "SUPPORTED", operation_id)
        return BridgeResolution(None, "UNSUPPORTED_OPERATION", operation_id)
    if isinstance(target, LatheParameterEditorDraftBridge):
        operation_id = target.strategy_id.name
        if target.strategy_id is LatheStrategyId.FACE:
            return BridgeResolution(target, "SUPPORTED", "FACE")
        if target.strategy_id in {
            LatheStrategyId.OD_ROUGH,
            LatheStrategyId.OD_FINISH,
            LatheStrategyId.ID_ROUGH,
            LatheStrategyId.ID_FINISH,
        }:
            if flags is None or not flags.is_enabled(
                UiFeatureFlag.OFFLINE_CAM_AI_TURNING_COVERAGE_13C
            ):
                return BridgeResolution(None, "FEATURE_DISABLED", operation_id)
            return BridgeResolution(target, "SUPPORTED", operation_id)
        return BridgeResolution(None, "UNSUPPORTED_OPERATION", operation_id)
    if isinstance(target, FacingEditorContext):
        return BridgeResolution(None, "REQUIRES_DRAFT_BRIDGE", "facing_2_5d")
    if isinstance(target, DrillingFamilyEditorContext):
        operation_id = getattr(getattr(target, "operation", None), "strategy_key", None)
        return BridgeResolution(None, "REQUIRES_DRAFT_BRIDGE", operation_id)
    if isinstance(target, LatheParameterEditor):
        return BridgeResolution(None, "REQUIRES_DRAFT_BRIDGE", None)
    return BridgeResolution(None, "UNSUPPORTED_EDITOR")
