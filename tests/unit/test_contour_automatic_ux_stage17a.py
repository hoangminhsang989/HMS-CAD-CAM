"""Stage17A Tranche2 Contour AUTO editor, persistence and generator tests."""

from dataclasses import replace
import json
from pathlib import Path
from time import perf_counter
import pytest

from hms_cadcam.cam.application import ContourGenerator
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    CylindricalGeometry,
    GeometryResolutionStatus,
    Length,
    LengthUnit,
    OperationParameterSet,
    ResolvedContourProfile,
    ToolAssembly,
)
from hms_cadcam.ui.function_editor import (
    FunctionEditorDraftState,
    FunctionEditorPage,
    FunctionEditorValueSource,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from _qt_lifecycle import (
    drain_test_owned_qt_state,
    qt_lifecycle_snapshot,
    top_level_baseline,
)
from hms_cadcam.ui.function_editor.strategies import (
    ContourEditorDraftContext,
    build_contour_schema,
    contour_applied_values,
    contour_draft_transform,
    prepare_contour_update,
)
from tests.unit.test_contour_function_editor_9a52 import (
    _application,
    _context,
    _workspace,
)


def _draft(context):
    return ContourEditorDraftContext(
        context.geometry_reference,
        geometry_profile=context.geometry_profile,
    )


def _auto_values(context, draft):
    values = dict(contour_applied_values(context))
    values.update(
        {
            "stepdown_mode": AutomaticParameterMode.AUTO.value,
            "lead_in_mode": AutomaticParameterMode.AUTO.value,
            "lead_out_mode": AutomaticParameterMode.AUTO.value,
        }
    )
    values.update(contour_draft_transform(context, draft, values))
    return values


def _contract(operation) -> AutomaticParameterContract:
    raw = dict(operation.parameters.values)[AUTOMATIC_PARAMETER_CONTRACT_KEY]
    assert isinstance(raw, str)
    return AutomaticParameterContract.from_json(raw)


def test_basic_advanced_auto_indicators_and_provenance_are_explicit() -> None:
    context, _descriptor = _context()
    schema = build_contour_schema(context)
    values = dict(contour_applied_values(context))

    basic = {
        section.section_id
        for section in schema.visible_sections(
            values, ParameterDisclosureLevel.BASIC
        )
    }
    assert "automatic_parameters" in basic
    assert schema.field("automatic_summary").action_id == (
        "use_contour_automatic_parameters"
    )
    assert schema.field("stepdown").disclosure_level is (
        ParameterDisclosureLevel.ADVANCED
    )
    assert schema.field("lead_in_length").disclosure_level is (
        ParameterDisclosureLevel.ADVANCED
    )
    assert schema.field("lead_out_length").disclosure_level is (
        ParameterDisclosureLevel.ADVANCED
    )
    assert "tùy chỉnh" in str(values["automatic_summary"])
    assert "normal_linear" in str(values["automatic_lead_provenance"])
    assert "segment" in str(values["automatic_entry_placement"])


def test_switch_to_auto_persists_policy_and_generator_revalidates_leads() -> None:
    context, descriptor = _context()
    draft = _draft(context)
    values = _auto_values(context, draft)
    assert values["stepdown"] != "1.0"
    assert values["automatic_summary"].startswith("3 tự động")

    update = prepare_contour_update(context, draft, values)
    contract = _contract(update.operation)
    assert all(
        contract.value(key).mode is AutomaticParameterMode.AUTO
        for key in ("stepdown", "lead_in_length", "lead_out_length")
    )
    assert contract.value("entry_segment_index").effective_value is not None
    assert contract.value("lead_form").effective_value == "normal_linear"
    assert update.parameters.stepdown.value == pytest.approx(
        float(contract.value("stepdown").effective_value)
    )
    assert update.parameters.lead_length.value == pytest.approx(
        float(contract.value("lead_in_length").effective_value)
    )

    generator = ContourGenerator()
    inputs = generator.resolve_inputs(
        update.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=update.machine,
        resolved_profile=ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED, descriptor
        ),
    )
    assert inputs.lead_in_point is not None
    assert inputs.lead_out_point is not None
    computing, _token = generator.begin(inputs)
    artifact = generator.generate(computing)
    assert any("lead_in" in event.provenance for event in artifact.events)
    assert any("lead_out" in event.provenance for event in artifact.events)


def test_manual_override_survives_tool_change_and_reset_returns_to_auto() -> None:
    context, _descriptor = _context()
    draft = _draft(context)
    auto_update = prepare_contour_update(context, draft, _auto_values(context, draft))
    current = replace(context, operation=auto_update.operation)
    values = dict(contour_applied_values(current))
    values["stepdown_mode"] = AutomaticParameterMode.MANUAL_OVERRIDE.value
    values["stepdown"] = "0.75"
    values.update(contour_draft_transform(current, draft, values))
    manual_update = prepare_contour_update(current, draft, values)
    assert _contract(manual_update.operation).value("stepdown").override_value == 0.75

    old_tool = current.tool_definitions[0]
    old_geometry = old_tool.cutting_geometry
    assert isinstance(old_geometry, CylindricalGeometry)
    new_tool = replace(
        old_tool,
        cutting_geometry=CylindricalGeometry(
            Length(old_geometry.diameter.value * 0.6, LengthUnit.MM),
            old_geometry.flute_length,
        ),
    )
    old_assembly = current.tool_assemblies[0]
    new_assembly = ToolAssembly.create(
        old_assembly.assembly_id,
        old_assembly.name,
        new_tool,
        old_assembly.stickout,
        old_assembly.gauge_length,
        current.holder_definitions[0],
    )
    changed = replace(
        current,
        operation=manual_update.operation,
        tool_definitions=(new_tool,),
        tool_assemblies=(new_assembly,),
    )
    changed_values = dict(contour_applied_values(changed))
    before_lead = float(changed_values["lead_in_length"])
    changed_values.update(contour_draft_transform(changed, draft, changed_values))
    assert changed_values["stepdown"] == "0.75"
    assert changed_values["stepdown_mode"] == (
        AutomaticParameterMode.MANUAL_OVERRIDE.value
    )
    assert float(changed_values["lead_in_length"]) == before_lead

    changed_values["stepdown_mode"] = AutomaticParameterMode.AUTO.value
    changed_values.update(contour_draft_transform(changed, draft, changed_values))
    assert float(changed_values["stepdown"]) != 0.75


def test_depth_geometry_quality_and_modes_recompute_only_auto_values() -> None:
    context, _descriptor = _context()
    draft = _draft(context)
    values = _auto_values(context, draft)
    initial_stepdown = float(values["stepdown"])
    initial_dependency = contour_draft_transform(context, draft, values)

    values["final_depth"] = "48.5"
    values.update(contour_draft_transform(context, draft, values))
    assert float(values["stepdown"]) < initial_stepdown

    values["quality_profile"] = "high"
    values["final_depth"] = "47.0"
    values.update(contour_draft_transform(context, draft, values))
    assert values != initial_dependency

    values["lead_out_mode"] = AutomaticParameterMode.MANUAL_OVERRIDE.value
    values["lead_out_length"] = "0.8"
    values.update(contour_draft_transform(context, draft, values))
    values["side"] = "inside"
    values.update(contour_draft_transform(context, draft, values))
    assert values["lead_out_length"] == "0.8"
    assert values["lead_out_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value


def test_reopened_schema_marks_auto_numeric_values_as_derived() -> None:
    context, _descriptor = _context()
    draft = _draft(context)
    update = prepare_contour_update(context, draft, _auto_values(context, draft))
    reopened = replace(context, operation=update.operation)
    schema = build_contour_schema(reopened)
    assert schema.field("stepdown").source is FunctionEditorValueSource.DERIVED
    assert schema.field("lead_in_length").source is FunctionEditorValueSource.DERIVED
    assert schema.field("lead_out_length").source is FunctionEditorValueSource.DERIVED


def test_malformed_contract_and_invalid_mode_fail_safely() -> None:
    context, _descriptor = _context()
    draft = _draft(context)
    update = prepare_contour_update(context, draft, _auto_values(context, draft))
    malformed = '{"contract_version":1}'
    base = update.operation.parameters
    parameters = OperationParameterSet(
        base.strategy_key,
        base.strategy_version,
        tuple(
            (key, malformed if key == AUTOMATIC_PARAMETER_CONTRACT_KEY else value)
            for key, value in base.values
        ),
        base.schema_version,
    )
    with pytest.raises(ValueError, match="bị hỏng"):
        contour_applied_values(replace(context, operation=replace(update.operation, parameters=parameters)))

    values = dict(contour_applied_values(context))
    values["stepdown_mode"] = "invented"
    with pytest.raises(ValueError, match="không hợp lệ"):
        contour_draft_transform(context, draft, values)


def test_missing_additive_contract_fields_load_as_legacy_manual_intent() -> None:
    context, descriptor = _context()
    draft = _draft(context)
    update = prepare_contour_update(context, draft, _auto_values(context, draft))
    full = _contract(update.operation)
    partial = replace(full, values=(full.value("stepdown"),))
    base = update.operation.parameters
    parameters = OperationParameterSet(
        base.strategy_key,
        base.strategy_version,
        tuple(
            (
                key,
                partial.to_json()
                if key == AUTOMATIC_PARAMETER_CONTRACT_KEY
                else value,
            )
            for key, value in base.values
        ),
        base.schema_version,
    )
    reopened = replace(
        context,
        operation=replace(update.operation, parameters=parameters),
    )
    values = dict(contour_applied_values(reopened))
    assert values["stepdown_mode"] == AutomaticParameterMode.AUTO.value
    assert values["lead_in_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value
    assert values["lead_out_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value
    assert values["lead_in_length"] == str(update.parameters.lead_length.value)
    assert values["lead_out_length"] == str(update.parameters.lead_length.value)
    inputs = ContourGenerator().resolve_inputs(
        reopened.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=update.machine,
        resolved_profile=ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED, descriptor
        ),
    )
    assert inputs.lead_in_point == inputs.lead_out_point


def test_project_auto_contract_round_trip_keeps_policy_not_transient_manual(tmp_path) -> None:
    application = _application()
    service, workspace, _displayed = _workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None
    values = session.applied_mapping()
    assert session.field_action_callback is not None
    selected = session.field_action_callback("select_geometry", values)
    assert selected is not None
    values.update(selected)
    changed = session.field_action_callback(
        "use_contour_automatic_parameters", values
    )
    assert changed is not None
    values.update(changed)
    assert session.apply_callback(values)
    applied = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    before = _contract(applied)
    assert before.value("stepdown").mode is AutomaticParameterMode.AUTO
    service.save()
    root = service.current_project.root_path
    service.close_project()
    service.open_project(root)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    after = _contract(restored)
    assert after.to_json() == before.to_json()
    assert after.value("stepdown").override_value is None
    assert restored.artifact_state.status is ArtifactStatus.DIRTY
    workspace.deleteLater()
    application.processEvents()


def test_unavailable_auto_is_not_offered_but_manual_legacy_value_remains() -> None:
    context, _descriptor = _context()
    missing = replace(context, tool_definitions=())
    schema = build_contour_schema(missing)
    values = dict(contour_applied_values(missing))
    assert schema.field("stepdown_mode").choices == (
        AutomaticParameterMode.MANUAL_OVERRIDE.value,
    )
    assert values["stepdown"] == "1.0"
    assert values["stepdown_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value
    state = FunctionEditorDraftState(
        schema,
        values,
        draft_transform_callback=lambda candidate: contour_draft_transform(
            missing, _draft(missing), candidate
        ),
    )
    with pytest.raises(ValueError, match="chưa đủ evidence"):
        state.edit("stepdown_mode", AutomaticParameterMode.AUTO.value)


def test_persisted_auto_intent_survives_temporarily_missing_evidence() -> None:
    context, _descriptor = _context()
    draft = _draft(context)
    update = prepare_contour_update(context, draft, _auto_values(context, draft))
    missing = replace(
        context,
        operation=update.operation,
        tool_definitions=(),
        geometry_profile=None,
    )
    missing_values = dict(contour_applied_values(missing))
    assert missing_values["stepdown_mode"] == AutomaticParameterMode.AUTO.value
    assert missing_values["lead_in_mode"] == AutomaticParameterMode.AUTO.value
    assert "không khả dụng" in str(missing_values["automatic_stepdown"])
    missing_schema = build_contour_schema(missing)
    assert AutomaticParameterMode.AUTO.value in missing_schema.field(
        "stepdown_mode"
    ).choices

    restored = replace(context, operation=update.operation)
    restored_values = dict(contour_applied_values(restored))
    assert restored_values["stepdown_mode"] == AutomaticParameterMode.AUTO.value
    assert "không khả dụng" not in str(restored_values["automatic_stepdown"])


def test_contour_auto_catalog_keys_have_vi_en_ko_parity_and_utf8() -> None:
    root = Path("src/hms_cadcam/ui/catalogs")
    catalogs = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in (
            root / "vi_VN.json",
            root / "en_US.json",
            root / "ko_KR.json",
        )
    }
    keys = {
        "Contour 2D Auto Setup",
        "Dùng tự động khả dụng",
        "Stepdown tự động",
        "Lead-in tự động",
        "Lead-out tự động",
        "Nguồn gốc lead",
        "Vị trí vào/ra",
        "Chế độ stepdown",
        "Chế độ lead-in",
        "Chế độ lead-out",
        "Chiều dài lead-in",
        "Chiều dài lead-out",
        "Tùy chỉnh · AUTO không khả dụng",
        "Tự động không khả dụng",
        "Tự động · hiện không khả dụng",
    }
    for catalog in catalogs.values():
        assert keys <= set(catalog)
        assert all(isinstance(catalog[key], str) and catalog[key] for key in keys)
    assert catalogs["en_US.json"]["Dùng tự động khả dụng"] == (
        "Use available automatic values"
    )
    assert catalogs["ko_KR.json"]["Lead-in tự động"] == "자동 리드인"


def test_contour_auto_editor_cycles_have_zero_qt_leak_and_no_tail_slowdown(
    qapp,
    record_testsuite_property,
) -> None:
    context, _descriptor = _context()
    baseline_pointers = top_level_baseline(qapp)
    stable = qt_lifecycle_snapshot(qapp)
    durations: list[float] = []
    service = translation_service()

    for cycle in range(24):
        started = perf_counter()
        with service.using(tuple(UiLanguage)[cycle % len(UiLanguage)]):
            schema = build_contour_schema(context)
            state = FunctionEditorDraftState(
                schema,
                contour_applied_values(context),
                draft_transform_callback=lambda values: contour_draft_transform(
                    context, _draft(context), values
                ),
            )
            page = FunctionEditorPage(state)
            page.resize(300 + (cycle % 4) * 80, 620 + (cycle % 3) * 60)
            page.show()
            qapp.processEvents()
            state.edit(
                "quality_profile",
                ("fast", "balanced", "high")[cycle % 3],
            )
            qapp.processEvents()
            page.close()
            page.deleteLater()
            drain_test_owned_qt_state(qapp, baseline_pointers)
            del page
        durations.append(perf_counter() - started)
        current = qt_lifecycle_snapshot(qapp)
        assert current.top_levels <= stable.top_levels
        assert current.modal_top_levels <= stable.modal_top_levels
        assert current.running_app_threads == 0

    head_max = max(durations[:6])
    tail_max = max(durations[-6:])
    assert tail_max <= max(1.0, head_max * 5.0)
    final = qt_lifecycle_snapshot(qapp)
    assert final.top_levels <= stable.top_levels
    assert final.hidden_top_levels <= stable.hidden_top_levels
    assert final.modal_top_levels <= stable.modal_top_levels
    assert final.running_app_threads == 0
    record_testsuite_property("r202_contour_cycles", str(len(durations)))
    record_testsuite_property(
        "r202_top_level_delta", str(final.top_levels - stable.top_levels)
    )
    record_testsuite_property(
        "r202_hidden_top_level_delta",
        str(final.hidden_top_levels - stable.hidden_top_levels),
    )
    record_testsuite_property(
        "r202_modal_delta", str(final.modal_top_levels - stable.modal_top_levels)
    )
    record_testsuite_property("r202_running_qthreads", str(final.running_app_threads))
    record_testsuite_property("r202_head_max_seconds", f"{head_max:.6f}")
    record_testsuite_property("r202_tail_max_seconds", f"{tail_max:.6f}")
