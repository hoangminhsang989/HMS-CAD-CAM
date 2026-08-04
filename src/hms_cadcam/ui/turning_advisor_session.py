"""Owner-local Stage 13C UI workflow over the existing advisor panel."""
from __future__ import annotations

from PySide6.QtCore import QObject

from hms_cadcam.ai_assist.turning_production_adapter import (
    TurningAnalyzeResult,
    TurningRuntimeBridge,
)
from hms_cadcam.cam.lathe.parameters import LatheParameterState
from hms_cadcam.cam.lathe.presenter import LatheOperationSnapshot
from hms_cadcam.ui.cutting_advisor_panel import CuttingAdvisorPanel


class TurningAdvisorUiSession(QObject):
    """Wire one panel to one exact production editor/operation owner."""

    def __init__(
        self,
        panel: CuttingAdvisorPanel,
        runtime: TurningRuntimeBridge,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(panel, CuttingAdvisorPanel):
            raise TypeError("Stage 13C advisor panel is invalid")
        if not isinstance(runtime, TurningRuntimeBridge):
            raise TypeError("Stage 13C runtime bridge is invalid")
        self.panel = panel
        self.runtime = runtime
        self._result: TurningAnalyzeResult | None = None
        self._connected = False
        self.analyze_actions = 0
        self.selective_apply_actions = 0
        self.undo_actions = 0

        runtime.update_materials(None, None)
        panel.configure_material_tokens(runtime.material_tokens())
        self._connect_once()
        self.refresh_owner()
        panel.set_state("material_required")
        panel.show()

    @property
    def current_result(self) -> TurningAnalyzeResult | None:
        return self._result

    def _connect_once(self) -> None:
        if self._connected:
            return
        self.panel.workpiece_material.currentIndexChanged.connect(
            self._material_changed
        )
        self.panel.tool_material.currentIndexChanged.connect(self._material_changed)
        self.panel.analyze.clicked.connect(self.analyze)
        self.panel.cancel.clicked.connect(self.cancel)
        self.panel.apply_selected.clicked.connect(self.selective_apply)
        self.panel.reset_selection.clicked.connect(self.panel.reset_field_selection)
        self.panel.undo.clicked.connect(self.undo)
        self.panel.ownership_invalidated.connect(self._panel_invalidated)
        self._connected = True

    def _disconnect(self) -> None:
        if not self._connected:
            return
        connections = (
            (self.panel.workpiece_material.currentIndexChanged, self._material_changed),
            (self.panel.tool_material.currentIndexChanged, self._material_changed),
            (self.panel.analyze.clicked, self.analyze),
            (self.panel.cancel.clicked, self.cancel),
            (self.panel.apply_selected.clicked, self.selective_apply),
            (self.panel.reset_selection.clicked, self.panel.reset_field_selection),
            (self.panel.undo.clicked, self.undo),
            (self.panel.ownership_invalidated, self._panel_invalidated),
        )
        for signal, slot in connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                continue
        self._connected = False

    def _material_changed(self, _index: int = -1) -> None:
        if not self.runtime.is_alive:
            return
        self.runtime.update_materials(
            self.panel.selected_workpiece_material(),
            self.panel.selected_tool_material(),
        )
        self._result = None
        self.panel.reset_result()
        complete = (
            self.panel.selected_workpiece_material() is not None
            and self.panel.selected_tool_material() is not None
        )
        self.panel.set_state("ready_to_analyze" if complete else "material_required")

    def analyze(self) -> TurningAnalyzeResult:
        self.analyze_actions += 1
        result = self.runtime.analyze()
        self._result = result if result.status == "READY" else None
        if result.status == "READY":
            self.panel.set_recommendation(result)
            self.panel.set_state("ready")
        elif result.status in {
            "MISSING_WORKPIECE_MATERIAL",
            "MISSING_TOOL_MATERIAL",
            "MISSING_PRODUCTION_INPUT",
        }:
            self.panel.reset_result()
            self.panel.warning_value.setText(result.status)
            self.panel.set_state("material_required")
        else:
            self.panel.reset_result()
            self.panel.warning_value.setText(result.status)
            self.panel.set_state("unavailable")
        self.refresh_owner()
        return result

    def selective_apply(self) -> str:
        self.selective_apply_actions += 1
        if self._result is None:
            self.panel.set_state("stale")
            return "STALE_RESULT_DISCARDED"
        selected = self.panel.selected_fields()
        if not selected:
            self.panel.set_state("no_selection")
            return "NOT_APPLIED"
        outcome = self.runtime.selective_apply(self._result, selected)
        self.panel.set_state(
            "draft_applied" if outcome.status == "APPLIED" else "stale"
        )
        self.panel.undo.setEnabled(outcome.status == "APPLIED")
        self.refresh_owner()
        return outcome.status

    def undo(self) -> str:
        self.undo_actions += 1
        outcome = self.runtime.undo()
        self.panel.set_state(
            "undo_complete"
            if outcome.status == "UNDONE"
            else "undo_refused"
        )
        self.panel.undo.setEnabled(outcome.status != "UNDONE")
        self.refresh_owner()
        return outcome.status

    def cancel(self) -> None:
        self.runtime.invalidate_result()
        self._result = None
        self.panel.reset_result()
        self.panel.set_state("cancelled")

    def _panel_invalidated(self, _reason: str) -> None:
        self.cancel()

    def sync_operation(self, operation: LatheOperationSnapshot | None) -> bool:
        context = self.runtime.adapter.context
        if (
            operation is None
            or str(operation.ownership.operation_id) != context.operation_id
            or operation.strategy_id is not context.parameter_state.strategy_id
        ):
            self.invalidate_owner("OPERATION_OWNER_CHANGED")
            return False
        next_state = LatheParameterState.build(
            operation.strategy_id, dict(operation.parameter_values)
        )
        if next_state != context.parameter_state:
            context.parameter_state = next_state
            self.runtime.invalidate_result()
            self._result = None
            self.panel.reset_result()
        self.refresh_owner()
        return True

    def refresh_owner(self) -> None:
        if not self.runtime.is_alive:
            return
        snapshot = self.runtime.adapter.snapshot()
        draft = self.runtime.adapter.context.draft_bridge.capture_snapshot()
        self.panel.set_owner_state(
            strategy_id=snapshot.strategy_id,
            diameter_mm=float(snapshot.active_diameter_mm),
            diameter_provenance=snapshot.provenance["diameter"],
            spindle_rpm=self._optional_float(draft.get("spindle_speed_rpm")),
            feed_mm_per_rev=self._optional_float(draft.get("feed_mm_per_rev")),
            depth_of_cut_mm=self._optional_float(draft.get("max_depth_of_cut_mm")),
        )

    @staticmethod
    def _optional_float(value: object) -> float | None:
        return None if value is None else float(value)

    def invalidate_owner(self, _reason: str = "OWNER_INVALIDATED") -> None:
        self._result = None
        self.runtime.invalidate_owner(_reason)
        self._disconnect()
        self.panel.reset_result()
        self.panel.set_state("owner_invalidated")
        self.panel.hide()

    def shutdown(self) -> None:
        self.invalidate_owner("APPLICATION_SHUTDOWN")


__all__ = ["TurningAdvisorUiSession"]
