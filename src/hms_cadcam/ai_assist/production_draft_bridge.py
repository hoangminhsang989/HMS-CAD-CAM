"""Draft-only bridge for the existing Function Editor production state."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Protocol

from hms_cadcam.ui.function_editor.model import PresentationValue
from hms_cadcam.ui.function_editor.state import FunctionEditorDraftState
from hms_cadcam.ui.function_editor.strategies.common_milling import FacingEditorContext, FacingEditorDraftContext, FacingEditorVariant, prepare_facing_update
from hms_cadcam.ui.function_editor.strategies.common_drilling import DrillingFamilyEditorContext, DrillingFamilyEditorDraftContext, prepare_drilling_family_update
from hms_cadcam.ui.lathe_workspace import LatheParameterEditor, build_lathe_parameter_update_preview
from hms_cadcam.cam.lathe.types import LatheStrategyId


class ProductionEditorDraftBridge(Protocol):
    """Safe editor-draft boundary; deliberately excludes persistence operations."""
    def editor_identity(self) -> str: ...
    def operation_identity(self) -> str: ...
    def project_identity(self) -> str: ...
    def capture_snapshot(self) -> Mapping[str, PresentationValue]: ...
    def current_revision_or_digest(self) -> str: ...
    def read_advisor_inputs(self) -> Mapping[str, PresentationValue]: ...
    def validate_proposed_field(self, field: str, value: PresentationValue) -> bool: ...
    def set_draft_field(self, field: str, value: PresentationValue) -> None: ...
    def restore_snapshot(self, snapshot: Mapping[str, PresentationValue]) -> None: ...
    def is_editor_alive(self) -> bool: ...
    def is_project_alive(self) -> bool: ...
    def invalidate(self) -> None: ...
    def supported_fields(self) -> frozenset[str]: ...


@dataclass(slots=True)
class FunctionEditorDraftBridge:
    """Thin wrapper over a real ``FunctionEditorDraftState``.

    Validation temporarily uses the production draft state's normal validation
    callback and always restores the live draft.  No apply callback is invoked.
    """
    state: FunctionEditorDraftState
    editor_id: str
    operation_id: str
    project_id: str
    advisor_inputs: Mapping[str, PresentationValue]
    _alive: bool = True
    _project_alive: bool = True

    def editor_identity(self) -> str: return self.editor_id
    def operation_identity(self) -> str: return self.operation_id
    def project_identity(self) -> str: return self.project_id
    def capture_snapshot(self) -> Mapping[str, PresentationValue]: return self.state.values
    def current_revision_or_digest(self) -> str:
        payload=json.dumps(dict(self.state.values),sort_keys=True,separators=(",",":"),default=str)
        return sha256(payload.encode("utf-8")).hexdigest()
    def read_advisor_inputs(self) -> Mapping[str, PresentationValue]: return dict(self.advisor_inputs)
    def supported_fields(self) -> frozenset[str]: return frozenset(self.state.applicable_field_ids())
    def is_editor_alive(self) -> bool: return self._alive
    def is_project_alive(self) -> bool: return self._project_alive
    def invalidate(self) -> None: self._alive=False
    def project_closed(self) -> None: self._project_alive=False; self._alive=False
    def validate_proposed_field(self, field: str, value: PresentationValue) -> bool:
        if not self._alive or not self._project_alive or field not in self.supported_fields(): return False
        before=self.capture_snapshot()
        try:
            self.state.edit(field,value)
            return not any(item.field_id == field and item.severity.value == "error" for item in self.state.validate())
        finally:
            self.restore_snapshot(before)
    def set_draft_field(self, field: str, value: PresentationValue) -> None:
        if not self.validate_proposed_field(field,value): raise ValueError("PRODUCTION_DRAFT_VALIDATION_FAILED")
        self.state.edit(field,value)
    def restore_snapshot(self, snapshot: Mapping[str, PresentationValue]) -> None:
        self.state.edit_many(snapshot)


@dataclass(slots=True)
class FacingEditorDraftBridge(FunctionEditorDraftBridge):
    """Concrete bridge for the existing ``facing_2_5d`` production context."""
    context: FacingEditorContext | None = None
    production_draft: FacingEditorDraftContext | None = None
    variant: FacingEditorVariant = FacingEditorVariant.STOCK

    @classmethod
    def from_context(cls, context: FacingEditorContext, state: FunctionEditorDraftState, *, project_id: str, production_draft: FacingEditorDraftContext | None = None, variant: FacingEditorVariant = FacingEditorVariant.STOCK) -> "FacingEditorDraftBridge":
        assembly=next((item for item in context.tool_assemblies if item.assembly_id==context.operation.tool_assembly.assembly_id),None)
        tool=next((item for item in context.tool_definitions if assembly is not None and item.tool_id==assembly.tool_id),None)
        diameter=getattr(getattr(tool,"cutting_geometry",None),"diameter",None)
        if diameter is None: raise ValueError("FACING_TOOL_DIAMETER_UNAVAILABLE")
        inputs={"diameter_mm":float(diameter.value),"flute_count":getattr(getattr(tool,"cutting_geometry",None),"flute_count",None)}
        return cls(state,"facing-production",str(context.operation.operation_id),project_id,inputs,context=context,production_draft=production_draft or FacingEditorDraftContext(None),variant=variant)

    def validate_proposed_field(self, field: str, value: PresentationValue) -> bool:
        if self.context is None or self.production_draft is None: return False
        before=self.capture_snapshot(); pending=self.production_draft.pending_input_id
        try:
            candidate=dict(before); candidate[field]=value
            prepare_facing_update(self.context,self.production_draft,self.variant,candidate)
            return True
        except (KeyError,TypeError,ValueError): return False
        finally:
            self.production_draft.pending_input_id=pending
            self.restore_snapshot(before)


@dataclass(slots=True)
class DrillingFamilyEditorDraftBridge(FunctionEditorDraftBridge):
    """Concrete bridge for one existing drilling-family production context."""
    context: DrillingFamilyEditorContext | None = None
    production_draft: DrillingFamilyEditorDraftContext | None = None

    @classmethod
    def from_context(cls, context: DrillingFamilyEditorContext, state: FunctionEditorDraftState, *, project_id: str, production_draft: DrillingFamilyEditorDraftContext) -> "DrillingFamilyEditorDraftBridge":
        tool=next((item for item in context.tool_definitions if any(a.tool_id==item.tool_id for a in context.tool_assemblies)),None)
        diameter=getattr(getattr(tool,"cutting_geometry",None),"diameter",None)
        inputs={"diameter_mm":float(diameter.value)} if diameter is not None else {}
        return cls(state,"drilling-family-production",str(context.operation.operation_id),project_id,inputs,context=context,production_draft=production_draft)

    def validate_proposed_field(self, field: str, value: PresentationValue) -> bool:
        if self.context is None or self.production_draft is None: return False
        before=self.capture_snapshot(); pending=dict(self.production_draft.pending_input_ids or {})
        try:
            candidate=dict(before); candidate[field]=value
            prepare_drilling_family_update(self.context,self.production_draft,candidate)
            return True
        except (KeyError,TypeError,ValueError): return False
        finally:
            self.production_draft.pending_input_ids=pending
            self.restore_snapshot(before)


@dataclass(slots=True)
class LatheParameterEditorDraftBridge:
    """Concrete draft-only wrapper over the production LatheParameterEditor."""
    editor: LatheParameterEditor | None
    editor_id: str
    operation_id: str
    project_id: str
    strategy_id: LatheStrategyId = LatheStrategyId.FACE
    _alive: bool = True
    def editor_identity(self)->str: return self.editor_id
    def operation_identity(self)->str: return self.operation_id
    def project_identity(self)->str: return self.project_id
    def is_editor_alive(self)->bool: return self._alive
    def is_project_alive(self)->bool: return self._alive
    def invalidate(self)->None:
        self._alive=False
        self.editor=None
    def supported_fields(self)->frozenset[str]:
        return frozenset() if self.editor is None else frozenset(self.editor.editors)
    def capture_snapshot(self)->Mapping[str, PresentationValue]:
        if not self._alive or self.editor is None:
            raise RuntimeError("LATHE_EDITOR_INVALIDATED")
        return {key:self._read_widget(key) for key in self.editor.editors}
    def current_revision_or_digest(self)->str:
        payload=json.dumps(self.capture_snapshot(),sort_keys=True,default=str,separators=(",",":"));return sha256(payload.encode()).hexdigest()
    def read_advisor_inputs(self)->Mapping[str, PresentationValue]:
        values=dict(self.capture_snapshot())
        if self.strategy_id is LatheStrategyId.FACE:
            diameter=values.get("outer_diameter_mm")
        elif self.strategy_id in {LatheStrategyId.OD_ROUGH,LatheStrategyId.OD_FINISH,LatheStrategyId.ID_ROUGH,LatheStrategyId.ID_FINISH}:
            diameter=values.get("target_diameter_mm")
        else:
            diameter=None
        try:
            workpiece=float(diameter)  # FACE uses outer diameter as the machining/workpiece diameter.
        except (TypeError,ValueError): raise ValueError("LATHE_WORKPIECE_DIAMETER_UNAVAILABLE")
        if not workpiece > 0: raise ValueError("LATHE_WORKPIECE_DIAMETER_UNAVAILABLE")
        values["workpiece_diameter_mm"]=workpiece
        return values
    def _read_widget(self,key:str)->PresentationValue:
        if self.editor is None: raise RuntimeError("LATHE_EDITOR_INVALIDATED")
        descriptor=next(item for item in self.editor.descriptors if item.parameter_id==key)
        return self.editor._editor_value(descriptor)
    def _write_widget(self,key:str,value:PresentationValue)->None:
        if self.editor is None: raise RuntimeError("LATHE_EDITOR_INVALIDATED")
        descriptor=next(item for item in self.editor.descriptors if item.parameter_id==key)
        self.editor._set_editor_value(descriptor,value)
    def validate_proposed_field(self,field:str,value:PresentationValue)->bool:
        if not self._alive or field not in self.supported_fields():return False
        before=self.capture_snapshot()
        try:
            if self.editor is None:return False
            self._write_widget(field,value);build_lathe_parameter_update_preview(self.editor);return True
        except (KeyError,TypeError,ValueError,RuntimeError):return False
        finally:self.restore_snapshot(before)
    def set_draft_field(self,field:str,value:PresentationValue)->None:
        if not self.validate_proposed_field(field,value):raise ValueError("LATHE_DRAFT_VALIDATION_FAILED")
        self._write_widget(field,value)
    def restore_snapshot(self,snapshot:Mapping[str,PresentationValue])->None:
        if not self._alive or self.editor is None:
            raise RuntimeError("LATHE_EDITOR_INVALIDATED")
        for key,value in snapshot.items():self._write_widget(key,value)


__all__=["DrillingFamilyEditorDraftBridge","FacingEditorDraftBridge","FunctionEditorDraftBridge","LatheParameterEditorDraftBridge","ProductionEditorDraftBridge"]
