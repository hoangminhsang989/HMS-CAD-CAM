"""Ownership-checked selective Apply/Undo for concrete production bridges."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping
from hms_cadcam.ai_assist.production_draft_bridge import ProductionEditorDraftBridge

@dataclass(frozen=True,slots=True)
class ApplyOwnership:
    project_id:str; editor_id:str; operation_id:str; bridge_type:str; generation:int; model_id:str; input_digest:str; draft_digest:str
@dataclass(frozen=True,slots=True)
class SelectiveApplyResult:
    status:str; applied:tuple[str,...]=(); rejected:tuple[str,...]=(); warning:tuple[str,...]=()

class SelectiveApplyService:
    """Apply selected values to a draft only and provide one-step stale-safe undo."""
    def __init__(self)->None:self._undo:tuple[ProductionEditorDraftBridge,ApplyOwnership,Mapping[str,object],str]|None=None
    def invalidate(self)->None:
        """Drop owner-bound Undo state without touching the production draft."""
        self._undo=None
    def apply(self,bridge:ProductionEditorDraftBridge,ownership:ApplyOwnership,values:Mapping[str,object],selected:frozenset[str])->SelectiveApplyResult:
        if not self._matches(bridge,ownership): return SelectiveApplyResult("STALE_RESULT_DISCARDED")
        before=bridge.capture_snapshot(); applied=[]; rejected=[]
        for field in sorted(selected):
            if field not in values: continue
            if bridge.validate_proposed_field(field,values[field]):
                if before.get(field)!=values[field]: bridge.set_draft_field(field,values[field]); applied.append(field)
            else: rejected.append(field)
        self._undo=(bridge,ownership,{field:before[field] for field in applied},bridge.current_revision_or_digest()) if applied else None
        return SelectiveApplyResult("APPLIED" if applied else "NOT_APPLIED",tuple(applied),tuple(rejected),tuple(f"INVALID_FIELD:{x}" for x in rejected))
    def undo(self,bridge:ProductionEditorDraftBridge,ownership:ApplyOwnership)->SelectiveApplyResult:
        if self._undo is None:return SelectiveApplyResult("UNDO_NOT_AVAILABLE")
        target,original_owner,values,applied_digest=self._undo
        if target is not bridge or original_owner.project_id!=ownership.project_id or original_owner.editor_id!=ownership.editor_id or original_owner.operation_id!=ownership.operation_id or original_owner.bridge_type!=ownership.bridge_type or bridge.current_revision_or_digest()!=applied_digest or not bridge.is_editor_alive() or not bridge.is_project_alive():return SelectiveApplyResult("STALE_UNDO_REFUSED")
        # Restore the captured pre-Apply values directly through the draft
        # boundary.  Re-validating against the editor's original production
        # state would reject a legitimate reversal as "no parameter changes";
        # the ownership/digest checks above are the safety gate for this
        # already-validated snapshot.
        bridge.restore_snapshot(values)
        self._undo=None;return SelectiveApplyResult("UNDONE",tuple(values))
    @staticmethod
    def _matches(bridge:ProductionEditorDraftBridge,owner:ApplyOwnership)->bool:
        return bridge.is_editor_alive() and bridge.is_project_alive() and bridge.project_identity()==owner.project_id and bridge.editor_identity()==owner.editor_id and bridge.operation_identity()==owner.operation_id and type(bridge).__name__==owner.bridge_type and bridge.current_revision_or_digest()==owner.draft_digest
