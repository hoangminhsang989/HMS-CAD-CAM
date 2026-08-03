"""Deterministic, fail-closed registry for the three certified CAM identities."""
from __future__ import annotations

from dataclasses import dataclass

from hms_cadcam.ai_assist.production_draft_bridge import (
    DrillingFamilyEditorDraftBridge,
    FacingEditorDraftBridge,
    LatheParameterEditorDraftBridge,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.function_editor.strategies.common_drilling import (
    DrillingFamilyEditorContext,
)
from hms_cadcam.ui.function_editor.strategies.common_milling import FacingEditorContext
from hms_cadcam.ui.lathe_workspace import LatheParameterEditor


CERTIFIED_OPERATION_IDS = ("facing_2_5d", "drilling_v1", "FACE")


@dataclass(frozen=True, slots=True)
class BridgeResolution:
    bridge: object | None
    status: str
    operation_id: str | None = None


def certified_operation_ids() -> tuple[str, ...]:
    """Return the canonical production strategy identities in stable order."""
    return CERTIFIED_OPERATION_IDS


def resolve_production_bridge(target: object) -> BridgeResolution:
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
        operation_id = target.strategy_id.value
        if target.strategy_id is LatheStrategyId.FACE:
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
