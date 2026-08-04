"""Stage 13C WP2 production snapshot and runtime bridge.

The bridge is deliberately draft-only.  It consumes the immutable WP1
contracts and the existing V1 advisor, but never calls the normal presenter
Apply route or persists project state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from hms_cadcam.ai_assist.cutting_advisor import (
    CuttingRecommendation,
    CuttingRequest,
    OperationFamily,
    RecommendationProfile,
    RecommendationStatus,
    recommend,
)
from hms_cadcam.ai_assist.model_loader import CuttingModel, ModelLoadError, load_canonical_model
from hms_cadcam.ai_assist.cutting_supervisor import CuttingWorkerSupervisor
from hms_cadcam.ai_assist.lifecycle import AiAssistBroker, AiRuntimeState
from hms_cadcam.ai_assist.production_draft_bridge import (
    LatheParameterEditorDraftBridge,
)
from hms_cadcam.ai_assist.selective_apply import (
    ApplyOwnership,
    SelectiveApplyResult,
    SelectiveApplyService,
)
from hms_cadcam.ai_assist.turning_strategy_contracts import (
    ProposedFieldPartition,
    TurningContractError,
    partition_proposed_fields,
    resolve_turning_provenance,
    turning_strategy_contract,
)
from hms_cadcam.cam.lathe.capabilities import LatheToolCapabilityResolution
from hms_cadcam.cam.lathe.parameters import LatheParameterState
from hms_cadcam.cam.lathe.toolpath.stock import LatheStockSnapshotV1
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags


_MATERIALS = frozenset({"ISO_P", "ISO_M", "ISO_K", "ISO_N", "ISO_S", "ISO_H"})
_TOOL_MATERIALS = frozenset({"HSS", "CARBIDE"})


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: object) -> object:
    return getattr(value, "value", value)


@dataclass(slots=True)
class TurningProductionContext:
    """Live production references used to re-snapshot ownership safely."""

    project_id: str
    editor_id: str
    operation_id: str
    parameter_state: LatheParameterState
    stock: LatheStockSnapshotV1
    tool_resolution: LatheToolCapabilityResolution
    material_token: str | None
    tool_material: str | None
    draft_bridge: LatheParameterEditorDraftBridge

    def __post_init__(self) -> None:
        for value in (self.project_id, self.editor_id, self.operation_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Turning production ownership identity is invalid")
        if not isinstance(self.parameter_state, LatheParameterState):
            raise TypeError("Turning production parameter state is invalid")
        if not isinstance(self.stock, LatheStockSnapshotV1):
            raise TypeError("Turning production stock snapshot is invalid")
        if not isinstance(self.tool_resolution, LatheToolCapabilityResolution):
            raise TypeError("Turning production tool resolution is invalid")
        if not isinstance(self.draft_bridge, LatheParameterEditorDraftBridge):
            raise TypeError("Turning production draft bridge is invalid")


@dataclass(frozen=True, slots=True)
class TurningProductionSnapshot:
    """Immutable, ordinary-data snapshot.  It retains no QObject/editor."""

    project_id: str
    editor_id: str
    operation_id: str
    strategy_id: str
    parameter_state_digest: str
    stock_digest: str
    tool_resolution_digest: str
    draft_digest: str
    input_digest: str
    active_diameter_mm: float | None
    material_token: str | None
    tool_material: str | None
    compatible_tool_capability: str | None
    spindle_speed_rpm: float | None
    feed_mm_per_rev: float | None
    depth_of_cut_mm: float | None
    descriptor_allowlist: tuple[str, ...]
    units: Mapping[str, str]
    warnings: tuple[str, ...]
    provenance: Mapping[str, str]

    def __post_init__(self) -> None:
        for value in (self.project_id, self.editor_id, self.operation_id, self.strategy_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Turning snapshot identities must be non-blank")
        if self.active_diameter_mm is not None and self.active_diameter_mm <= 0:
            raise ValueError("Turning snapshot diameter must be positive")
        object.__setattr__(self, "units", MappingProxyType(dict(self.units)))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible copy without exposing production objects."""

        return {
            "project_id": self.project_id,
            "editor_id": self.editor_id,
            "operation_id": self.operation_id,
            "strategy_id": self.strategy_id,
            "parameter_state_digest": self.parameter_state_digest,
            "stock_digest": self.stock_digest,
            "tool_resolution_digest": self.tool_resolution_digest,
            "draft_digest": self.draft_digest,
            "input_digest": self.input_digest,
            "active_diameter_mm": self.active_diameter_mm,
            "material_token": self.material_token,
            "tool_material": self.tool_material,
            "compatible_tool_capability": self.compatible_tool_capability,
            "spindle_speed_rpm": self.spindle_speed_rpm,
            "feed_mm_per_rev": self.feed_mm_per_rev,
            "depth_of_cut_mm": self.depth_of_cut_mm,
            "descriptor_allowlist": list(self.descriptor_allowlist),
            "units": dict(self.units),
            "warnings": list(self.warnings),
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class TurningAnalyzeResult:
    """Structured explicit-Analyze result; never mutates a draft."""

    status: str
    snapshot: TurningProductionSnapshot | None = None
    raw_recommendation: Mapping[str, float] = MappingProxyType({})
    final_recommendation: Mapping[str, float] = MappingProxyType({})
    safe_ranges: Mapping[str, tuple[float, float]] = MappingProxyType({})
    confidence: float = 0.0
    clamps: tuple[str, ...] = ()
    provenance: Mapping[str, str] = MappingProxyType({})
    warnings: tuple[str, ...] = ()
    retained_unsupported: tuple[tuple[str, object], ...] = ()
    stale_ownership_token: str | None = None
    model_id: str | None = None


class TurningProductionAdapter:
    """Extract exact WP1 provenance from actual production state."""

    def __init__(self, context: TurningProductionContext) -> None:
        if not isinstance(context, TurningProductionContext):
            raise TypeError("Turning production context is invalid")
        self.context = context

    def snapshot(self) -> TurningProductionSnapshot:
        state = self.context.parameter_state
        contract = turning_strategy_contract(state.strategy_id)
        provenance = resolve_turning_provenance(
            state.strategy_id, state, self.context.stock, self.context.tool_resolution
        )
        values = {key: _json_value(value) for key, value in state.mapping.items()}
        parameter_digest = _digest({"strategy_id": state.strategy_id.name, "values": values})
        stock_digest = _digest(self.context.stock.canonical_payload())
        resolution = self.context.tool_resolution
        tool_payload = {
            "reference": {
                "tool_id": str(resolution.reference.tool_id),
                "profile_id": None if resolution.reference.profile_id is None else str(resolution.reference.profile_id),
                "assembly_id": str(resolution.reference.assembly_id),
            },
            "exists": resolution.exists,
            "current": resolution.current,
            "capabilities": sorted(item.value for item in resolution.capabilities),
            "tool_revision": None if resolution.tool_revision is None else resolution.tool_revision.value,
            "profile_revision": None if resolution.profile_revision is None else resolution.profile_revision.value,
            "assembly_revision": None if resolution.assembly_revision is None else resolution.assembly_revision.value,
        }
        tool_digest = _digest(tool_payload)
        material = self.context.material_token
        tool_material = self.context.tool_material
        warnings: list[str] = []
        if material is None:
            warnings.append("MISSING_WORKPIECE_MATERIAL")
        elif material not in _MATERIALS:
            warnings.append("INVALID_WORKPIECE_MATERIAL")
        if tool_material is None:
            warnings.append("MISSING_TOOL_MATERIAL")
        elif tool_material not in _TOOL_MATERIALS:
            warnings.append("INVALID_TOOL_MATERIAL")
        draft_digest = self.context.draft_bridge.current_revision_or_digest()
        input_digest = _digest({
            "project_id": self.context.project_id,
            "editor_id": self.context.editor_id,
            "operation_id": self.context.operation_id,
            "strategy_id": state.strategy_id.name,
            "parameter_state_digest": parameter_digest,
            "stock_digest": stock_digest,
            "tool_resolution_digest": tool_digest,
            "material_token": material,
            "tool_material": tool_material,
        })
        depth = values.get("max_depth_of_cut_mm") if "depth_of_cut_mm" in contract.allowed_advisor_fields else None
        return TurningProductionSnapshot(
            self.context.project_id,
            self.context.editor_id,
            self.context.operation_id,
            state.strategy_id.name,
            parameter_digest,
            stock_digest,
            tool_digest,
            draft_digest,
            input_digest,
            provenance.diameter_mm,
            material,
            tool_material,
            contract.required_tool_capability.value,
            float(values.get("spindle_speed_rpm")) if values.get("spindle_speed_rpm") is not None else None,
            float(values.get("feed_mm_per_rev")) if values.get("feed_mm_per_rev") is not None else None,
            None if depth is None else float(depth),
            tuple(item.production_descriptor for item in contract.field_mappings),
            {item.production_descriptor: ("rpm" if item.production_descriptor == "spindle_speed_rpm" else "mm/rev" if item.production_descriptor == "feed_mm_per_rev" else "mm") for item in contract.field_mappings},
            tuple(warnings),
            {
                "diameter": contract.diameter_source.value,
                "tool": "LatheToolCapabilityResolution.capabilities",
                "feed": "LatheParameterState.feed_mm_per_rev",
                "spindle": "LatheParameterState.spindle_speed_rpm",
                "depth": "LatheParameterState.max_depth_of_cut_mm" if depth is not None else "not_exposed",
            },
        )

    def build_request(self, snapshot: TurningProductionSnapshot) -> CuttingRequest:
        if snapshot.material_token not in _MATERIALS:
            raise TurningContractError("MISSING_WORKPIECE_MATERIAL")
        if snapshot.tool_material not in _TOOL_MATERIALS:
            raise TurningContractError("MISSING_TOOL_MATERIAL")
        if snapshot.active_diameter_mm is None:
            raise TurningContractError("MISSING_WORKPIECE_DIAMETER")
        return CuttingRequest(
            correlation_id=snapshot.input_digest,
            family=OperationFamily.TURNING,
            material_group=snapshot.material_token,
            tool_material=snapshot.tool_material,
            diameter_mm=snapshot.active_diameter_mm,
            profile=RecommendationProfile.BALANCED,
            requested_depth_of_cut_mm=snapshot.depth_of_cut_mm,
        )


class TurningRuntimeBridge:
    """Flag-gated Analyze and draft-only Apply/Undo runtime boundary."""

    def __init__(
        self,
        adapter: TurningProductionAdapter,
        flags: UiFeatureFlags,
        *,
        model: CuttingModel | None = None,
        broker: AiAssistBroker | None = None,
        supervisor: CuttingWorkerSupervisor | None = None,
    ) -> None:
        self.adapter = adapter
        if not isinstance(flags, UiFeatureFlags):
            raise TypeError("Turning runtime flags are invalid")
        self.flags = flags
        self.model = model
        if (broker is None) is not (supervisor is None):
            raise ValueError("Turning runtime broker and supervisor must be paired")
        self.broker = broker
        self.supervisor = supervisor
        self._apply_service = SelectiveApplyService()
        self._last_owner: ApplyOwnership | None = None
        self._last_snapshot: TurningProductionSnapshot | None = None
        self._alive = True

    @property
    def is_alive(self) -> bool:
        return self._alive

    def material_tokens(self) -> tuple[str, ...]:
        """Return exact material tokens from the canonical TURNING V1 model."""

        model = self._model_for_analyze()
        return tuple(str(token) for token in model.data["materials"])

    def update_materials(
        self, workpiece_material: str | None, tool_material: str | None
    ) -> None:
        """Update session-only selectors and invalidate result/Undo ownership."""

        if workpiece_material is not None and workpiece_material not in _MATERIALS:
            raise ValueError("INVALID_WORKPIECE_MATERIAL")
        if tool_material is not None and tool_material not in _TOOL_MATERIALS:
            raise ValueError("INVALID_TOOL_MATERIAL")
        self.adapter.context.material_token = workpiece_material
        self.adapter.context.tool_material = tool_material
        self.invalidate_result()

    def invalidate_result(self) -> None:
        self._last_owner = None
        self._last_snapshot = None
        self._apply_service.invalidate()

    def invalidate_owner(self, reason: str = "OWNER_INVALIDATED") -> None:
        """Make queued callbacks and all future mutations inert."""

        if not self._alive:
            return
        self._alive = False
        self.invalidate_result()
        if self.broker is not None:
            if reason == "APPLICATION_SHUTDOWN":
                self.broker.shutdown()
            else:
                self.broker.cancel_task()
        self.adapter.context.draft_bridge.invalidate()

    def _model_for_analyze(self) -> CuttingModel:
        if self.model is not None:
            return self.model
        manifest = Path(__file__).with_name("models") / "cutting_parameters_v1.manifest.json"
        self.model = load_canonical_model(manifest)
        return self.model

    def analyze(self) -> TurningAnalyzeResult:
        if not self._alive:
            return TurningAnalyzeResult("OWNER_INVALIDATED")
        if not self.flags.is_enabled(UiFeatureFlag.OFFLINE_CAM_AI_TURNING_COVERAGE_13C):
            return TurningAnalyzeResult("FEATURE_DISABLED")
        try:
            snapshot = self.adapter.snapshot()
            request = self.adapter.build_request(snapshot)
        except TurningContractError as error:
            return TurningAnalyzeResult(error.code)
        if snapshot.warnings:
            return TurningAnalyzeResult(
                "MISSING_PRODUCTION_INPUT", snapshot=snapshot, warnings=snapshot.warnings,
                stale_ownership_token=snapshot.input_digest,
            )
        try:
            model = self._model_for_analyze()
        except (ModelLoadError, OSError):
            return TurningAnalyzeResult("MODEL_UNAVAILABLE", snapshot=snapshot)
        raw: Mapping[str, float]
        proposed: Mapping[str, float]
        clamps: tuple[str, ...]
        confidence: float
        if self.broker is not None and self.supervisor is not None:
            if (
                self.broker.status.state is not AiRuntimeState.RUNNING
                or not self.supervisor.has_worker
            ):
                return TurningAnalyzeResult("WORKER_UNAVAILABLE", snapshot=snapshot)
            try:
                message = self.supervisor.recommend(
                    snapshot.input_digest,
                    {
                        "family": request.family.value,
                        "material_group": request.material_group,
                        "tool_material": request.tool_material,
                        "diameter_mm": request.diameter_mm,
                        "flute_count": request.flute_count,
                        "profile": request.profile.value,
                        "rigidity": request.rigidity,
                        "machine_max_rpm": request.machine_max_rpm,
                        "machine_max_feed_mm_min": request.machine_max_feed_mm_min,
                        "requested_axial_depth_mm": request.requested_axial_depth_mm,
                        "requested_radial_engagement_mm": request.requested_radial_engagement_mm,
                        "requested_peck_depth_mm": request.requested_peck_depth_mm,
                        "requested_depth_of_cut_mm": request.requested_depth_of_cut_mm,
                    },
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                return TurningAnalyzeResult("WORKER_ERROR", snapshot=snapshot)
            if message.kind != "RECOMMEND_RESULT" or not isinstance(message.payload, dict):
                return TurningAnalyzeResult(
                    "WORKER_ERROR", snapshot=snapshot, warnings=(str(message.payload),)
                )
            try:
                raw = MappingProxyType(
                    {str(key): float(value) for key, value in message.payload["raw"].items()}
                )
                proposed = MappingProxyType(
                    {str(key): float(value) for key, value in message.payload["values"].items()}
                )
                clamps = tuple(str(item) for item in message.payload["clamps"])
                confidence = float(message.payload["confidence"])
            except (KeyError, TypeError, ValueError):
                return TurningAnalyzeResult("WORKER_ERROR", snapshot=snapshot)
        else:
            recommendation = recommend(model, request)
            raw = recommendation.raw
            proposed = recommendation.values
            clamps = recommendation.clamps
            confidence = recommendation.confidence
        partition = partition_proposed_fields(LatheStrategyId[snapshot.strategy_id], proposed)
        mapped = self._map_fields(partition)
        ranges = self._safe_ranges(partition)
        return TurningAnalyzeResult(
            RecommendationStatus.READY.value,
            snapshot,
            raw,
            mapped,
            ranges,
            confidence,
            clamps,
            snapshot.provenance,
            snapshot.warnings + partition.warnings,
            partition.retained_unsupported,
            snapshot.input_digest,
            model.model_id,
        )

    @staticmethod
    def _map_fields(partition: ProposedFieldPartition) -> Mapping[str, float]:
        mapping = {"spindle_rpm": "spindle_speed_rpm", "feed_per_revolution_mm": "feed_mm_per_rev", "depth_of_cut_mm": "max_depth_of_cut_mm"}
        return MappingProxyType({mapping[key]: float(value) for key, value in partition.accepted if key in mapping})

    @staticmethod
    def _safe_ranges(partition: ProposedFieldPartition) -> Mapping[str, tuple[float, float]]:
        mapping = {"spindle_rpm": "spindle_speed_rpm", "feed_per_revolution_mm": "feed_mm_per_rev", "depth_of_cut_mm": "max_depth_of_cut_mm"}
        return MappingProxyType({mapping[key]: (0.0, float(value)) for key, value in partition.accepted if key in mapping})

    def selective_apply(self, result: TurningAnalyzeResult, selected: frozenset[str]) -> SelectiveApplyResult:
        if not self._alive:
            return SelectiveApplyResult("STALE_RESULT_DISCARDED")
        if result.status != RecommendationStatus.READY.value or result.snapshot is None:
            return SelectiveApplyResult("STALE_RESULT_DISCARDED")
        try:
            current = self.adapter.snapshot()
        except (TurningContractError, ValueError):
            return SelectiveApplyResult("STALE_RESULT_DISCARDED")
        if current.input_digest != result.snapshot.input_digest or current.draft_digest != result.snapshot.draft_digest:
            return SelectiveApplyResult("STALE_RESULT_DISCARDED")
        owner = ApplyOwnership(
            current.project_id, current.editor_id, current.operation_id,
            type(self.adapter.context.draft_bridge).__name__, 0,
            self.model.model_id if self.model is not None else "cutting-parameters-v1",
            current.input_digest, current.draft_digest,
        )
        applied = self._apply_service.apply(
            self.adapter.context.draft_bridge, owner, result.final_recommendation, selected
        )
        if applied.status == "APPLIED":
            self._last_owner = owner
            self._last_snapshot = result.snapshot
        return applied

    def undo(self) -> SelectiveApplyResult:
        if not self._alive:
            return SelectiveApplyResult("STALE_UNDO_REFUSED")
        if self._last_owner is None or self._last_snapshot is None:
            return SelectiveApplyResult("UNDO_NOT_AVAILABLE")
        try:
            current = self.adapter.snapshot()
        except (TurningContractError, ValueError):
            return SelectiveApplyResult("STALE_UNDO_REFUSED")
        baseline = self._last_snapshot
        if (
            current.project_id != baseline.project_id
            or current.editor_id != baseline.editor_id
            or current.operation_id != baseline.operation_id
            or current.strategy_id != baseline.strategy_id
            or current.parameter_state_digest != baseline.parameter_state_digest
            or current.stock_digest != baseline.stock_digest
            or current.tool_resolution_digest != baseline.tool_resolution_digest
            or current.material_token != baseline.material_token
            or current.tool_material != baseline.tool_material
        ):
            return SelectiveApplyResult("STALE_UNDO_REFUSED")
        result = self._apply_service.undo(self.adapter.context.draft_bridge, self._last_owner)
        if result.status == "UNDONE":
            self._last_owner = None
            self._last_snapshot = None
        return result


__all__ = [
    "TurningAnalyzeResult",
    "TurningProductionContext",
    "TurningProductionAdapter",
    "TurningProductionSnapshot",
    "TurningRuntimeBridge",
]
