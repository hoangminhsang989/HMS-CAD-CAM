"""Concrete selective Apply/Undo ownership tests."""
from __future__ import annotations
from hms_cadcam.ai_assist.production_draft_bridge import FacingEditorDraftBridge
from hms_cadcam.ai_assist.selective_apply import ApplyOwnership, SelectiveApplyService
from hms_cadcam.ui.function_editor.reference import build_contour_reference_schema
from hms_cadcam.ui.function_editor.state import FunctionEditorDraftState
from test_facing_function_editors_9a51 import FacingEditorVariant, _schema_and_values

def test_facing_selective_apply_and_undo_do_not_call_final_apply():
 context,schema,values=_schema_and_values(FacingEditorVariant.STOCK);state=FunctionEditorDraftState(schema,values);bridge=FacingEditorDraftBridge.from_context(context,state,project_id="project");owner=ApplyOwnership("project",bridge.editor_identity(),bridge.operation_identity(),type(bridge).__name__,1,"cutting-parameters-v1","input",bridge.current_revision_or_digest());service=SelectiveApplyService();before=state.values["feed_rate"];result=service.apply(bridge,owner,{"spindle_speed":1200.0,"feed_rate":600.0},frozenset({"spindle_speed"}));assert result.status=="APPLIED" and state.values["feed_rate"]==before and state.last_apply_result is None
 owner=ApplyOwnership(owner.project_id,owner.editor_id,owner.operation_id,owner.bridge_type,owner.generation,owner.model_id,owner.input_digest,bridge.current_revision_or_digest());assert service.undo(bridge,owner).status=="UNDONE" and state.values["spindle_speed"]==values["spindle_speed"]
def test_stale_and_second_undo_are_refused():
 schema=build_contour_reference_schema();state=FunctionEditorDraftState(schema);assert state.values
