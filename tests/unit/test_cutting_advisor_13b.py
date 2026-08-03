"""Focused fail-closed tests for the Stage 13B numerical foundation."""
from __future__ import annotations
import hashlib, json, os
from pathlib import Path
import pytest
from hms_cadcam.ai_assist.adapters import DrillingAdapter, MillingAdapter, TurningAdapter
from hms_cadcam.ai_assist.cutting_advisor import CuttingRequest, OperationFamily, RecommendationProfile, recommend
from hms_cadcam.ai_assist.model_loader import ModelLoadError, load_canonical_model
from hms_cadcam.ai_assist.stage13b_settings import AdvisorSettingsService
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.i18n import UiLanguage, build_default_catalogs
from hms_cadcam.ai_assist.production_draft_bridge import DrillingFamilyEditorDraftBridge, FacingEditorDraftBridge, FunctionEditorDraftBridge, LatheParameterEditorDraftBridge
from hms_cadcam.ui.function_editor.reference import build_contour_reference_schema
from hms_cadcam.ui.function_editor.state import FunctionEditorDraftState

ROOT=Path(__file__).parents[2]/"src/hms_cadcam/ai_assist/models"
MANIFEST=ROOT/"cutting_parameters_v1.manifest.json"
def model(): return load_canonical_model(MANIFEST)
def request(family=OperationFamily.MILLING, **values):
 d=dict(correlation_id="c",family=family,material_group="ISO_P",tool_material="CARBIDE",diameter_mm=10.0,flute_count=4,requested_axial_depth_mm=8.0,requested_radial_engagement_mm=5.0,requested_peck_depth_mm=8.0,requested_depth_of_cut_mm=8.0);d.update(values);return CuttingRequest(**d)
def write_pair(tmp_path, data, manifest=None):
 raw=json.dumps(data,separators=(",",":"),allow_nan=True).encode();(tmp_path/"m.json").write_bytes(raw); manifest=manifest or {"manifest_schema_version":1,"model_id":"cutting-parameters-v1","model_version":"1","relative_model_path":"m.json","byte_size":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"maximum_bytes":65536,"units":"metric","domain_version":"stage13b-v1","worker_protocol":"stage13b-cutting-advisor-v1","role":"immutable_offline_numerical_model"};(tmp_path/"m.manifest.json").write_text(json.dumps(manifest),encoding="utf-8");return tmp_path/"m.manifest.json"

def test_canonical_load_identity(): assert (model().model_id,model().byte_size)==("cutting-parameters-v1",1093)
@pytest.mark.parametrize("family",list(OperationFamily))
def test_all_families_produce_nonnegative_values(family): assert all(v>=0 for v in recommend(model(),request(family,flute_count=4 if family is OperationFamily.MILLING else None)).values.values())
@pytest.mark.parametrize("profile",list(RecommendationProfile))
def test_profiles_are_deterministic(profile): assert recommend(model(),request(profile=profile)).input_digest==recommend(model(),request(profile=profile)).input_digest
def test_productivity_order():
 values=[recommend(model(),request(profile=p)).values["spindle_rpm"] for p in RecommendationProfile];assert values==sorted(values)
def test_milling_outputs(): assert {"spindle_rpm","linear_feed_mm_min","feed_per_tooth_mm","plunge_feed_mm_min","axial_stepdown_mm","radial_stepover_mm"} <= recommend(model(),request()).values.keys()
def test_drilling_outputs(): assert {"spindle_rpm","linear_feed_mm_min","feed_per_revolution_mm","peck_depth_mm"} <= recommend(model(),request(OperationFamily.DRILLING,flute_count=None)).values.keys()
def test_turning_outputs(): assert {"spindle_rpm","linear_feed_mm_min","feed_per_revolution_mm","depth_of_cut_mm"} <= recommend(model(),request(OperationFamily.TURNING,flute_count=None)).values.keys()
def test_machine_clamps_recorded():
 clamps=recommend(model(),request(machine_max_rpm=100,machine_max_feed_mm_min=10)).clamps
 assert "SPINDLE_CLAMPED_TO_MACHINE_MAX" in clamps and "FEED_CLAMPED_TO_MACHINE_MAX" in clamps
@pytest.mark.parametrize("bad",[0,-1,float("inf"),float("nan")])
def test_bad_diameter_rejected(bad):
 with pytest.raises(ValueError): request(diameter_mm=bad)
@pytest.mark.parametrize("key",["relative_model_path","byte_size","sha256","maximum_bytes","units"])
def test_manifest_missing_required_data_rejected(tmp_path,key):
 d=json.loads((ROOT/"cutting_parameters_v1.json").read_text());raw=json.dumps(d,separators=(",",":"));manifest=json.loads(MANIFEST.read_text());manifest.pop(key);p=write_pair(tmp_path,d,manifest)
 with pytest.raises(ModelLoadError): load_canonical_model(p)
@pytest.mark.parametrize("path",["../m.json","C:/m.json",""])
def test_bad_paths_rejected(tmp_path,path):
 d=json.loads((ROOT/"cutting_parameters_v1.json").read_text());manifest=json.loads(MANIFEST.read_text());manifest["relative_model_path"]=path;p=write_pair(tmp_path,d,manifest)
 with pytest.raises(ModelLoadError): load_canonical_model(p)
@pytest.mark.parametrize("mutation",["size","checksum","version"])
def test_manifest_identity_failures(tmp_path,mutation):
 d=json.loads((ROOT/"cutting_parameters_v1.json").read_text());manifest=json.loads(MANIFEST.read_text());manifest["relative_model_path"]="m.json";manifest["byte_size"]=len(json.dumps(d,separators=(",",":")));manifest["sha256"]=hashlib.sha256(json.dumps(d,separators=(",",":" )).encode()).hexdigest()
 if mutation=="size":manifest["byte_size"]+=1
 if mutation=="checksum":manifest["sha256"]="0"*64
 if mutation=="version":manifest["manifest_schema_version"]=2
 with pytest.raises(ModelLoadError):load_canonical_model(write_pair(tmp_path,d,manifest))
@pytest.mark.parametrize("field",["cutting_speed","feed_per_tooth","plunge_factor"])
def test_ranges_are_validated(tmp_path,field):
 d=json.loads((ROOT/"cutting_parameters_v1.json").read_text());d["families"]["milling"][field]["minimum"]=999
 with pytest.raises(ModelLoadError) as e:load_canonical_model(write_pair(tmp_path,d))
 assert str(e.value)=="MODEL_RANGE_INVALID"
def test_nonfinite_rejected(tmp_path):
 d=json.loads((ROOT/"cutting_parameters_v1.json").read_text());d["materials"]["ISO_P"]=float("nan")
 with pytest.raises(ModelLoadError):load_canonical_model(write_pair(tmp_path,d))
def test_duplicate_key_rejected(tmp_path):
 p=tmp_path/"m.manifest.json";p.write_text('{"manifest_schema_version":1,"manifest_schema_version":1}',encoding="utf-8")
 with pytest.raises(ModelLoadError) as e:load_canonical_model(p)
 assert str(e.value)=="MODEL_DUPLICATE_KEY"

class Draft:
 def __init__(self):self.operation_id="op";self.revision=1;self.values={"spindle_rpm":1.0,"linear_feed_mm_min":2.0};self.calls=[]
 def snapshot_metric(self):return self.values
 def validate_field(self,n,v):self.calls.append(n);return n!="linear_feed_mm_min"
 def set_draft_field(self,n,v):self.values[n]=v
@pytest.mark.parametrize("adapter",[MillingAdapter,DrillingAdapter,TurningAdapter])
def test_adapter_apply_and_stale(adapter):
 d=Draft();a=adapter(d,"project","editor");owner=a.build_request();result=a.selective_apply(owner,{"spindle_rpm":12,"linear_feed_mm_min":13},{"spindle_rpm","linear_feed_mm_min"});assert result.applied==("spindle_rpm",) and d.values["linear_feed_mm_min"]==2
 d.revision+=1;assert a.selective_apply(owner,{"spindle_rpm":15},{"spindle_rpm"}).status=="STALE_RESULT_DISCARDED"
def test_settings_defaults_are_off():
 class B:
  def value(self,*x):return x[-1]
 s=AdvisorSettingsService(B()).load();assert not s.enabled and s.profile is RecommendationProfile.BALANCED
def test_stage13b_flag_depends_on_stage13a_capability():
 flags=UiFeatureFlags({UiFeatureFlag.OFFLINE_CAM_AI_ASSIST_13A:False,UiFeatureFlag.OFFLINE_CAM_AI_PARAMETER_ADVISOR_13B:True})
 assert not flags.is_enabled(UiFeatureFlag.OFFLINE_CAM_AI_PARAMETER_ADVISOR_13B)
def test_stage13b_i18n_catalogs_have_parity_for_panel_keys():
 catalogs=build_default_catalogs();keys={key for key in catalogs[UiLanguage.EN_US].entries if key.startswith("stage13b.advisor.")}
 assert keys and all(keys <= set(catalogs[language].entries) for language in UiLanguage)
def test_function_editor_draft_bridge_validates_without_apply_or_persistence():
 state=FunctionEditorDraftState(build_contour_reference_schema(),project_key="project",operation_key="operation")
 bridge=FunctionEditorDraftBridge(state,"editor","operation","project",{"diameter_mm":10.0,"flute_count":4})
 before=dict(state.values);assert bridge.validate_proposed_field("feed_rate",500.0);assert dict(state.values)==before
 bridge.set_draft_field("feed_rate",500.0);assert state.values["feed_rate"]==500.0 and state.last_apply_result is None
 bridge.restore_snapshot(before);assert dict(state.values)==before
def test_function_editor_draft_bridge_preview_restores_digest_dirty_and_apply_identity():
 state=FunctionEditorDraftState(build_contour_reference_schema(),project_key="project",operation_key="operation")
 bridge=FunctionEditorDraftBridge(state,"editor","operation","project",{})
 before=(dict(state.values),bridge.current_revision_or_digest(),state.is_dirty,state.last_apply_result)
 assert bridge.validate_proposed_field("feed_rate",500.0)
 after=(dict(state.values),bridge.current_revision_or_digest(),state.is_dirty,state.last_apply_result)
 assert after==before
def test_concrete_facing_context_bridge_reads_production_tool_and_edits_only_draft():
 from test_facing_function_editors_9a51 import FacingEditorVariant, _schema_and_values
 context,schema,values=_schema_and_values(FacingEditorVariant.STOCK)
 state=FunctionEditorDraftState(schema,values,project_key="project",operation_key=str(context.operation.operation_id))
 bridge=FacingEditorDraftBridge.from_context(context,state,project_id="project")
 before=dict(state.values);assert bridge.operation_identity()==str(context.operation.operation_id)
 assert bridge.read_advisor_inputs()["diameter_mm"]>0 and bridge.validate_proposed_field("spindle_speed",1200.0)
 bridge.set_draft_field("spindle_speed",1200.0)
 assert state.values["spindle_speed"]==1200.0 and state.values["feed_rate"]==before["feed_rate"] and state.last_apply_result is None
def test_facing_bridge_reuses_pure_production_update_builder_for_rejection():
 from test_facing_function_editors_9a51 import FacingEditorVariant, _schema_and_values
 context,schema,values=_schema_and_values(FacingEditorVariant.STOCK);state=FunctionEditorDraftState(schema,values)
 bridge=FacingEditorDraftBridge.from_context(context,state,project_id="project")
 before=(dict(state.values),bridge.current_revision_or_digest(),state.last_apply_result)
 assert not bridge.validate_proposed_field("spindle_speed","not-a-number")
 assert (dict(state.values),bridge.current_revision_or_digest(),state.last_apply_result)==before
def test_concrete_drilling_context_bridge_uses_runtime_compatible_context(tmp_path):
 from test_drilling_ui import _workspace
 from hms_cadcam.cam.domain import DrillingStrategy
 from hms_cadcam.ui.function_editor.strategies import DrillingFamilyEditorKind, drilling_family_applied_values, build_drilling_schema
 from hms_cadcam.ui.function_editor.strategies.common_drilling import DrillingFamilyEditorContext, DrillingFamilyEditorDraftContext
 service,session,workspace,_viewer,_selected=_workspace(tmp_path)
 operation=service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]; setup=service.cam_snapshot.jobs[0].setups[0]; strategy=DrillingStrategy.from_operation_parameters(operation.parameters)
 context=DrillingFamilyEditorContext(DrillingFamilyEditorKind.DRILLING,"Drilling",operation,setup,service.cam_snapshot.tool_assemblies,service.cam_snapshot.tool_definitions,service.cam_snapshot.holder_definitions,service.cam_snapshot.machine_definitions,strategy.geometry.source,True)
 schema=build_drilling_schema(context);values=drilling_family_applied_values(context);state=FunctionEditorDraftState(schema,values);draft=DrillingFamilyEditorDraftContext(context.hole_source)
 bridge=DrillingFamilyEditorDraftBridge.from_context(context,state,project_id=str(session.manifest.project_id),production_draft=draft)
 before=dict(state.values);assert bridge.validate_proposed_field("spindle_speed",1200.0);bridge.set_draft_field("spindle_speed",1200.0);assert state.values["spindle_speed"]==1200.0 and state.values["feed_rate"]==before["feed_rate"];workspace.deleteLater()
def test_drilling_bridge_reuses_pure_production_update_builder_for_rejection(tmp_path):
 from test_drilling_ui import _workspace
 from hms_cadcam.cam.domain import DrillingStrategy
 from hms_cadcam.ui.function_editor.strategies import DrillingFamilyEditorKind, drilling_family_applied_values, build_drilling_schema
 from hms_cadcam.ui.function_editor.strategies.common_drilling import DrillingFamilyEditorContext, DrillingFamilyEditorDraftContext
 service,session,workspace,_viewer,_selected=_workspace(tmp_path);operation=service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0];setup=service.cam_snapshot.jobs[0].setups[0];strategy=DrillingStrategy.from_operation_parameters(operation.parameters)
 context=DrillingFamilyEditorContext(DrillingFamilyEditorKind.DRILLING,"Drilling",operation,setup,service.cam_snapshot.tool_assemblies,service.cam_snapshot.tool_definitions,service.cam_snapshot.holder_definitions,service.cam_snapshot.machine_definitions,strategy.geometry.source,True);state=FunctionEditorDraftState(build_drilling_schema(context),drilling_family_applied_values(context));bridge=DrillingFamilyEditorDraftBridge.from_context(context,state,project_id=str(session.manifest.project_id),production_draft=DrillingFamilyEditorDraftContext(context.hole_source))
 before=dict(state.values);assert not bridge.validate_proposed_field("spindle_speed","broken");assert dict(state.values)==before;workspace.deleteLater()
def test_concrete_lathe_parameter_editor_bridge_uses_real_controls():
 from _lathe_ui_fixtures import workspace_for
 workspace,presenter,_reference=workspace_for(); presenter.create_operation(__import__("hms_cadcam.cam.lathe.types",fromlist=["LatheStrategyId"]).LatheStrategyId.FACE); presenter.refresh()
 editor=workspace.parameter_editor;assert editor.descriptors
 bridge=LatheParameterEditorDraftBridge(editor,"lathe-editor","operation","project");before=bridge.capture_snapshot();field="spindle_speed_rpm"
 inputs=bridge.read_advisor_inputs();assert inputs["workpiece_diameter_mm"]==before["outer_diameter_mm"] and "spindle_speed_rpm" in inputs and "feed_mm_per_rev" in inputs
 assert bridge.validate_proposed_field(field,before[field]+100);bridge.invalidate();assert not bridge.is_editor_alive();workspace.deleteLater()
def test_lathe_face_diameter_mapping_has_no_target_fallback():
 from _lathe_ui_fixtures import workspace_for
 workspace,presenter,_reference=workspace_for(); presenter.create_operation(__import__("hms_cadcam.cam.lathe.types",fromlist=["LatheStrategyId"]).LatheStrategyId.FACE); presenter.refresh();editor=workspace.parameter_editor;bridge=LatheParameterEditorDraftBridge(editor,"lathe-editor","operation","project");
 original=bridge.capture_snapshot(); editor._editors["outer_diameter_mm"].setValue(0.0)
 with pytest.raises(ValueError): bridge.read_advisor_inputs()
 bridge.restore_snapshot(original);workspace.deleteLater()
