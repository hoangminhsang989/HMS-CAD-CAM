"""Normal Apply/Cancel regressions using existing production construction routes."""
from __future__ import annotations
from hms_cadcam.ui.function_editor.state import FunctionEditorDraftState
from test_facing_function_editors_9a51 import FacingEditorVariant, _schema_and_values
from test_drilling_ui import _workspace
from hms_cadcam.cam.domain import DrillingStrategy
from hms_cadcam.ui.function_editor.strategies import DrillingFamilyEditorKind, drilling_family_applied_values, build_drilling_schema
from hms_cadcam.ui.function_editor.strategies.common_drilling import DrillingFamilyEditorContext

def test_facing_normal_apply_dispatches_once_and_cancel_does_not():
 context,schema,values=_schema_and_values(FacingEditorVariant.STOCK);state=FunctionEditorDraftState(schema,values);calls=[]
 state.edit("spindle_speed","1200")
 assert state.apply(lambda current:(calls.append(dict(current)),True)[1]);assert len(calls)==1
 original=dict(state.values);state.edit("spindle_speed","1300");state.reset_draft();assert dict(state.values)==original
def test_drilling_normal_apply_dispatches_once_and_cancel_does_not(tmp_path):
 service,session,workspace,_viewer,_selected=_workspace(tmp_path);operation=service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0];setup=service.cam_snapshot.jobs[0].setups[0];strategy=DrillingStrategy.from_operation_parameters(operation.parameters);context=DrillingFamilyEditorContext(DrillingFamilyEditorKind.DRILLING,"Drilling",operation,setup,service.cam_snapshot.tool_assemblies,service.cam_snapshot.tool_definitions,service.cam_snapshot.holder_definitions,service.cam_snapshot.machine_definitions,strategy.geometry.source,True);state=FunctionEditorDraftState(build_drilling_schema(context),drilling_family_applied_values(context));calls=[];state.edit("spindle_speed","1200");assert state.apply(lambda current:(calls.append(dict(current)),True)[1]);assert len(calls)==1;persisted=service.cam_snapshot;state.edit("spindle_speed","1300");state.reset_draft();assert service.cam_snapshot==persisted;workspace.deleteLater()
