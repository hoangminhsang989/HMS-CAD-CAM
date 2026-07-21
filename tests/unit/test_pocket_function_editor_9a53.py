"""Stage 9A.5.3 production Function Editor contracts for Pocket 2.5D."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from hms_cadcam.cam.application import PocketGenerator, basic_mill_resources
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    CamNodeId,
    DiagnosticCode,
    DiagnosticSeverity,
    DirtyReason,
    GeometryInputId,
    GeometryInputRole,
    GeometryResolutionStatus,
    Length,
    LengthUnit,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationGeometryInput,
    OperationId,
    PocketCuttingDirection,
    PocketEntryPolicy,
    PocketStrategy,
    ResolvedPocketGeometry,
    ToolAssemblyReference,
    ValidationDiagnostic,
    WorkOffset,
)
from hms_cadcam.cam.post import (
    FanucRobodrill21iAdapter,
    PostRequest,
    SimulationGateMode,
    SimulationGatePolicy,
    lower_toolpath,
    robodrill_21i_definition,
)
from hms_cadcam.cam.post.lowering import PostSourceSnapshot
from hms_cadcam.cam.simulation import (
    CollisionScene,
    CollisionTarget,
    CollisionTargetKind,
    InMemoryAabbBackend,
    SimulationRuntimeService,
    SimulationSamplingPolicy,
    build_simulation_request,
    sample_toolpath,
)
from hms_cadcam.cam.toolpath import artifact_to_dict, publish_toolpath
from hms_cadcam.cam.toolpath.geometry import Bounds3
from hms_cadcam.project.constants import DATABASE_SCHEMA_VERSION
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftState,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorPage,
    FunctionEditorSection,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.function_editor.strategies import (
    PocketEditorContext,
    PocketEditorDraftContext,
    build_pocket_schema,
    pocket_applied_values,
    prepare_pocket_update,
    validate_pocket_schema_contract,
)
from tests.unit._fanuc_fixtures import fixture_context
from tests.unit.test_fanuc_robodrill_21i_runtime import _robodrill_machine
from tests.unit.test_pocket_strategy import _rectangle, _reference, _region, _strategy
from tests.unit.test_pocket_ui import _workspace


def _context(
    *,
    strategy: PocketStrategy | None = None,
    machine=None,
    bound: bool = True,
) -> tuple[PocketEditorContext, object, object]:
    from hms_cadcam.ui.cam_ui import _default_setup

    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    setup = replace(setup, work_offset=WorkOffset("PRIMARY", 1))
    reference = _reference(setup.source_scope.primary_source_id)
    selected_strategy = strategy or _strategy(reference)
    geometry_input = OperationGeometryInput(
        GeometryInputId.new(),
        GeometryInputRole.BOUNDARY,
        reference,
        True,
        reference.kind,
        0,
    )
    tool, holder, assembly, default_machine = basic_mill_resources(LengthUnit.MM)
    selected_machine = machine or default_machine
    operation = Operation(
        OperationId.new(),
        CamNodeId.new(),
        OperationFamily.MILLING,
        setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly),
        (geometry_input,) if bound else (),
        selected_strategy.to_operation_parameters(),
        MachineRequirement(
            selected_machine.machine_id,
            selected_machine.revision,
            selected_machine.content_fingerprint,
            selected_machine.unit,
            (OperationCapability.MILLING,),
        ),
    )
    loop = _rectangle()
    region = _region(loop, reference)
    context = PocketEditorContext(
        "Pocket 2.5D",
        operation,
        setup,
        (assembly,),
        (tool,),
        (holder,),
        (selected_machine,),
        reference if bound else None,
        bound,
        len(loop.segments) if bound else None,
        loop.orientation.value if bound else "",
        0 if bound else None,
    )
    return context, region, holder


def _schema_values() -> tuple[PocketEditorContext, FunctionEditorSchema, dict[str, object]]:
    context, _region_value, _holder = _context()
    schema = build_pocket_schema(context)
    return context, schema, dict(pocket_applied_values(context))


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dispose(widget: QWidget, application: QApplication) -> None:
    widget.close()
    widget.deleteLater()
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_schema_has_stable_complete_deterministic_mapping() -> None:
    context, schema, values = _schema_values()
    assert schema.editor_id == "pocket_production_9a5_3"
    assert str(schema.strategy) == "pocket_2_5d_9a5_3"
    assert tuple(section.section_id for section in schema.ordered_sections) == (
        "basic",
        "geometry",
        "tool",
        "cutting",
        "levels",
        "entry",
        "linking",
        "advanced",
        "expert",
    )
    assert tuple(field.field_id for field in schema.fields) == tuple(
        field.field_id for field in build_pocket_schema(context).fields
    )
    assert set(values) == {field.field_id for field in schema.fields}
    assert all(field.binding_key for field in schema.fields)
    assert schema.field("tolerance").disclosure_level is ParameterDisclosureLevel.EXPERT
    assert schema.field("plunge_feed_rate").disclosure_level is ParameterDisclosureLevel.ADVANCED
    assert schema.field("final_depth_summary").source.value == "derived"
    assert values["machining_pattern"] == "offset_inward"
    assert values["island_summary"].startswith("0 island")


def test_basic_is_minimal_and_advanced_expert_are_progressive() -> None:
    _context_value, schema, values = _schema_values()
    basic_sections = schema.visible_sections(values, ParameterDisclosureLevel.BASIC)
    assert {section.section_id for section in basic_sections} == {
        "basic", "geometry", "tool", "cutting", "levels", "entry"
    }
    basic_editable = {
        field.field_id
        for section in basic_sections
        for field in section.fields
        if field.disclosure_level is ParameterDisclosureLevel.BASIC
        and field.kind is not FunctionEditorFieldKind.READ_ONLY
    }
    assert basic_editable == {
        "operation_name",
        "tool_assembly_id",
        "cutting_direction",
        "stepover",
        "cutting_feed_rate",
        "spindle_speed",
        "top_z",
        "bottom_z",
        "stepdown",
        "entry_policy",
    }
    advanced = schema.visible_sections(values, ParameterDisclosureLevel.ADVANCED)
    assert "linking" in {section.section_id for section in advanced}
    assert "advanced" in {section.section_id for section in advanced}
    assert "expert" not in {section.section_id for section in advanced}


def test_sources_defaults_enums_units_and_actual_capabilities_are_explicit() -> None:
    _context_value, schema, values = _schema_values()
    assert schema.field("geometry_summary").source.value == "geometry"
    assert schema.field("tool_details").source.value == "tool"
    assert schema.field("stepover").unit == "mm"
    assert schema.field("cutting_feed_rate").unit == "mm/min"
    assert "Setup/Stock" in schema.field("top_z").default_label
    assert set(schema.field("cutting_direction").choices) == {"climb", "conventional"}
    assert schema.field("entry_policy").choices == ("vertical_plunge",)
    assert not {
        "zigzag_angle", "helix_radius", "ramp_angle", "finish_pass",
        "stay_down", "d_offset", "island_order",
    } & set(values)


def test_duplicate_and_unsupported_field_contracts_fail_closed() -> None:
    _context_value, schema, _values = _schema_values()
    invented = FunctionEditorField(
        "invented_helix",
        "Invented",
        FunctionEditorFieldKind.TEXT,
        "bad",
        binding_key="parameters.invented_helix",
    )
    unsupported = replace(
        schema,
        sections=schema.sections
        + (FunctionEditorSection("unsupported", "UNSUPPORTED", (invented,), order=999),),
    )
    with pytest.raises(ValueError, match="unsupported"):
        validate_pocket_schema_contract(unsupported)
    duplicate = replace(
        schema.sections[0],
        fields=schema.sections[0].fields + (schema.sections[0].fields[0],),
    )
    with pytest.raises(ValueError, match="Duplicate field ID"):
        replace(schema, sections=(duplicate, *schema.sections[1:]))


def test_unbound_operation_can_open_but_requires_typed_geometry_before_apply() -> None:
    context, _region_value, _holder = _context(bound=False)
    schema = build_pocket_schema(context)
    values = dict(pocket_applied_values(context))
    assert values["geometry_reference_id"] == ""
    assert "Chưa chọn region" in str(values["geometry_summary"])
    with pytest.raises(ValueError, match="geometry_reference_id"):
        prepare_pocket_update(context, PocketEditorDraftContext(None), values)
    assert schema.field("geometry_summary").action_id == "select_geometry"


def test_unchanged_round_trip_preserves_domain_codec_and_fingerprints() -> None:
    context, _region_value, _holder = _context()
    reference = context.geometry_reference
    assert reference is not None
    before = PocketStrategy.from_operation_parameters(context.operation.parameters, reference)
    update = prepare_pocket_update(
        context,
        PocketEditorDraftContext(reference),
        pocket_applied_values(context),
    )
    assert update.operation == context.operation
    assert update.strategy == before
    assert update.strategy.to_dict() == before.to_dict()
    assert update.strategy.to_operation_parameters() == context.operation.parameters
    assert update.strategy.fingerprint == before.fingerprint
    assert update.operation.geometry_inputs == context.operation.geometry_inputs


@pytest.mark.parametrize(
    ("field_id", "presentation_value", "attribute", "domain_value"),
    (
        ("cutting_direction", "conventional", "cutting_direction", PocketCuttingDirection.CONVENTIONAL),
        ("stepover", "2.0", "stepover", Length(2.0, LengthUnit.MM)),
        ("stepdown", "0.5", "stepdown", Length(0.5, LengthUnit.MM)),
        ("radial_stock_allowance", "0.2", "radial_stock_allowance", Length(0.2, LengthUnit.MM)),
        ("axial_allowance", "0.1", "depth", None),
    ),
)
def test_ui_binding_matches_legacy_parameter_and_dirty_semantics(
    field_id: str,
    presentation_value: object,
    attribute: str,
    domain_value: object,
) -> None:
    context, _region_value, _holder = _context()
    values = dict(pocket_applied_values(context))
    values[field_id] = presentation_value
    update = prepare_pocket_update(
        context, PocketEditorDraftContext(context.geometry_reference), values
    )
    if field_id == "axial_allowance":
        assert update.strategy.depth.allowance == Length(0.1, LengthUnit.MM)
    else:
        assert getattr(update.strategy, attribute) == domain_value
    assert update.operation.revision == context.operation.revision.next()
    assert DirtyReason.PARAMETERS_CHANGED in update.operation.artifact_state.dirty_reasons
    assert update.operation.parameters == update.strategy.to_operation_parameters()


def test_geometry_tool_machine_duplicate_and_island_states_fail_closed() -> None:
    context, _region_value, _holder = _context()
    values = pocket_applied_values(context)
    with pytest.raises(ValueError, match="Tool Assembly"):
        prepare_pocket_update(
            replace(context, tool_assemblies=()),
            PocketEditorDraftContext(context.geometry_reference),
            values,
        )
    with pytest.raises(ValueError, match="máy phay"):
        prepare_pocket_update(
            replace(context, machine_definitions=()),
            PocketEditorDraftContext(context.geometry_reference),
            values,
        )
    boundary = context.operation.geometry_inputs[0]
    duplicate = replace(
        context.operation,
        geometry_inputs=(
            boundary,
            replace(boundary, input_id=GeometryInputId.new(), selection_order=1),
        ),
    )
    with pytest.raises(ValueError, match="duplicate/additional"):
        prepare_pocket_update(
            replace(context, operation=duplicate),
            PocketEditorDraftContext(context.geometry_reference),
            values,
        )
    island_context = replace(
        context,
        geometry_resolved=False,
        geometry_island_count=2,
        geometry_diagnostic="island unsupported v1",
    )
    island_values = pocket_applied_values(island_context)
    assert "2 island" in str(island_values["island_summary"])
    assert "UNSUPPORTED" in str(island_values["geometry_summary"])


def test_draft_invalid_reset_defaults_apply_and_rollback_do_not_mutate_domain() -> None:
    context, schema, values = _schema_values()
    state = FunctionEditorDraftState(schema, values)
    before = context.operation
    state.edit("stepdown", "0")
    assert state.validate()
    assert context.operation == before
    assert not state.apply(lambda _values: pytest.fail("invalid draft called Apply"))
    state.reset_field("stepdown")
    state.edit("stepover", "2")
    assert state.is_dirty
    state.restore_recommended_defaults()
    assert state.is_dirty
    assert context.operation == before
    assert not state.apply(
        lambda _values: (_ for _ in ()).throw(RuntimeError("rollback"))
    )
    assert context.operation == before
    state.reset_draft()
    assert not state.is_dirty


@pytest.mark.parametrize(
    "changes",
    (
        {},
        {"cutting_direction": PocketCuttingDirection.CONVENTIONAL},
        {"stepover": Length(2.0, LengthUnit.MM)},
        {"stepdown": Length(0.5, LengthUnit.MM)},
        {"radial_stock_allowance": Length(0.2, LengthUnit.MM)},
    ),
)
def test_exact_toolpath_ir_is_unchanged_for_supported_pocket_scenarios(
    changes: dict[str, object],
) -> None:
    base_context, _base_region, _holder = _context()
    reference = base_context.geometry_reference
    assert reference is not None
    context, region, _holder = _context(strategy=_strategy(reference, **changes))
    # Rebind the strategy to the context's reference created by _context.
    context_reference = context.geometry_reference
    assert context_reference is not None
    strategy = _strategy(context_reference, **changes)
    context = replace(
        context,
        operation=replace(context.operation, parameters=strategy.to_operation_parameters()),
    )
    update = prepare_pocket_update(
        context,
        PocketEditorDraftContext(context_reference),
        pocket_applied_values(context),
    )
    resolved = ResolvedPocketGeometry(GeometryResolutionStatus.RESOLVED, region)
    generator = PocketGenerator()
    legacy_inputs = generator.resolve_inputs(
        context.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=update.machine,
        resolved_geometry=resolved,
    )
    migrated_inputs = generator.resolve_inputs(
        update.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=update.machine,
        resolved_geometry=resolved,
    )
    assert migrated_inputs.input_fingerprint == legacy_inputs.input_fingerprint
    legacy_computing, _legacy_token = generator.begin(legacy_inputs)
    migrated_computing, _migrated_token = generator.begin(migrated_inputs)
    legacy_artifact = generator.generate(legacy_computing)
    migrated_artifact = generator.generate(migrated_computing)
    legacy_payload = artifact_to_dict(legacy_artifact)
    migrated_payload = artifact_to_dict(migrated_artifact)
    legacy_payload.pop("computation_token")
    migrated_payload.pop("computation_token")
    assert migrated_payload == legacy_payload
    assert migrated_artifact.artifact_fingerprint == legacy_artifact.artifact_fingerprint


def test_simulation_and_fanuc_output_are_exactly_equivalent() -> None:
    machine = _robodrill_machine()
    context, region, holder = _context(machine=machine)
    update = prepare_pocket_update(
        context,
        PocketEditorDraftContext(context.geometry_reference),
        pocket_applied_values(context),
    )
    resolved = ResolvedPocketGeometry(GeometryResolutionStatus.RESOLVED, region)
    generator = PocketGenerator()
    legacy_inputs = generator.resolve_inputs(
        context.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=machine,
        resolved_geometry=resolved,
    )
    migrated_inputs = generator.resolve_inputs(
        update.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=machine,
        resolved_geometry=resolved,
    )
    legacy_computing, legacy_token = generator.begin(legacy_inputs)
    migrated_computing, migrated_token = generator.begin(migrated_inputs)
    legacy_artifact = generator.generate(legacy_computing)
    migrated_artifact = generator.generate(migrated_computing)
    policy = SimulationSamplingPolicy(max_linear_step=10.0)
    assert sample_toolpath(
        artifact=legacy_artifact, wcs=context.setup.wcs, policy=policy
    ) == sample_toolpath(
        artifact=migrated_artifact, wcs=context.setup.wcs, policy=policy
    )
    legacy_published = publish_toolpath(
        legacy_computing.operation,
        legacy_artifact,
        legacy_token,
        legacy_inputs.input_fingerprint,
    )
    migrated_published = publish_toolpath(
        migrated_computing.operation,
        migrated_artifact,
        migrated_token,
        migrated_inputs.input_fingerprint,
    )
    legacy_request = build_simulation_request(
        operation=legacy_published.operation,
        artifact=legacy_artifact,
        setup=context.setup,
        tool=update.tool,
        assembly=update.assembly,
        holder=holder,
        machine=machine,
        sampling_policy=policy,
        safe_height=5.0,
    )
    migrated_request = build_simulation_request(
        operation=migrated_published.operation,
        artifact=migrated_artifact,
        setup=context.setup,
        tool=update.tool,
        assembly=update.assembly,
        holder=holder,
        machine=machine,
        sampling_policy=policy,
        safe_height=5.0,
    )
    point_type = context.setup.wcs.origin.__class__
    scene = CollisionScene(
        CollisionTarget(
            "far-stock",
            CollisionTargetKind.STOCK,
            Bounds3(
                point_type(1000.0, 1000.0, 1000.0, LengthUnit.MM),
                point_type(1100.0, 1100.0, 1100.0, LengthUnit.MM),
            ),
        )
    )
    runtime = SimulationRuntimeService()
    legacy_simulation = runtime.run(
        request=legacy_request,
        artifact=legacy_artifact,
        setup=context.setup,
        tool=update.tool,
        assembly=update.assembly,
        holder=holder,
        scene=scene,
        backend=InMemoryAabbBackend(),
    )
    migrated_simulation = runtime.run(
        request=migrated_request,
        artifact=migrated_artifact,
        setup=context.setup,
        tool=update.tool,
        assembly=update.assembly,
        holder=holder,
        scene=scene,
        backend=InMemoryAabbBackend(),
    )
    assert legacy_simulation.result is not None and migrated_simulation.result is not None
    assert migrated_simulation.result.status == legacy_simulation.result.status
    assert migrated_simulation.result.issues == legacy_simulation.result.issues
    assert migrated_simulation.result.statistics == legacy_simulation.result.statistics
    assert migrated_simulation.result.result_fingerprint == legacy_simulation.result.result_fingerprint

    project_id = uuid4()
    legacy_source = PostSourceSnapshot(
        project_id,
        legacy_published.operation,
        legacy_artifact,
        context.setup,
        update.assembly,
        update.tool,
        holder,
        machine,
    )
    migrated_source = replace(
        legacy_source,
        operation=migrated_published.operation,
        artifact=migrated_artifact,
    )
    definition = robodrill_21i_definition()
    program_context = replace(
        fixture_context(legacy_source, file_name="pocket_equivalence.fn", cutter=False),
        safe_z=Length(5.0, LengthUnit.MM),
    )
    gate = SimulationGatePolicy(SimulationGateMode.OPTIONAL)
    legacy_post_request = PostRequest(
        project_id,
        legacy_source.operation.operation_id,
        legacy_artifact.artifact_id,
        definition,
        simulation_gate_policy=gate,
        program_context=program_context,
    )
    migrated_post_request = PostRequest(
        project_id,
        migrated_source.operation.operation_id,
        migrated_artifact.artifact_id,
        definition,
        simulation_gate_policy=gate,
        program_context=program_context,
    )
    legacy_program = lower_toolpath(legacy_post_request, legacy_source)
    migrated_program = lower_toolpath(migrated_post_request, migrated_source)
    assert migrated_program.records == legacy_program.records
    assert migrated_program.diagnostics == legacy_program.diagnostics
    adapter = FanucRobodrill21iAdapter(definition)
    legacy_output = adapter.format_program(legacy_program, definition)
    migrated_output = adapter.format_program(migrated_program, definition)
    assert migrated_output == legacy_output
    assert "\r\n" in legacy_output
    assert adapter.validate_output(migrated_output, migrated_program, definition) == ()


@pytest.mark.parametrize("width", (300, 360, 420, 520))
def test_production_page_is_responsive_and_never_calculates_on_edit(width: int) -> None:
    application = _application()
    _context_value, schema, values = _schema_values()
    calculated: list[object] = []
    state = FunctionEditorDraftState(schema, values, generation=1)
    page = FunctionEditorPage(
        state,
        apply_callback=lambda _values: True,
        preview_callback=lambda _request: "Preview approximate",
        calculate_callback=calculated.append,
    )
    page.resize(width, 700)
    page.show()
    application.processEvents()
    page._field_changed("stepover", "2")
    assert calculated == []
    assert not page.footer.buttons[FunctionEditorAction.CALCULATE].isEnabled()
    assert page.scroll_area.horizontalScrollBar().maximum() == 0
    assert page.footer.isVisible()
    _dispose(page, application)


def test_real_workspace_select_validate_preview_apply_calculate_and_lifecycle(
    tmp_path,
) -> None:
    application = _application()
    service, _project, workspace, viewer, _selected = _workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None
    assert session.schema.editor_id == "pocket_production_9a5_3"
    before = service.cam_snapshot
    values = session.applied_mapping()
    assert session.field_action_callback is not None
    selected = session.field_action_callback("select_geometry", values)
    assert selected is not None
    values.update(selected)
    values["stepdown"] = "0"
    diagnostics = session.validation_callback(values)
    assert diagnostics and diagnostics[0].field_id == "stepdown"
    assert service.cam_snapshot == before
    values["stepdown"] = "0.5"
    preview_request = FunctionEditorDraftState(
        session.schema,
        values,
        project_key=session.project_key,
        operation_key=session.operation_key,
        generation=session.generation,
    ).preview_request()
    assert "Preview approximate" in str(session.preview_callback(preview_request))
    assert service.cam_snapshot == before
    assert session.apply_callback(values)
    applied = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert applied.artifact_state.status is ArtifactStatus.DIRTY
    assert viewer.displayed == []
    refreshed = workspace.production_function_editor_session()
    assert refreshed is not None
    assert refreshed.calculate_callback(refreshed.applied_mapping())
    calculated = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert calculated.artifact_state.status is ArtifactStatus.VALID
    assert viewer.displayed
    service.save()
    root = service.current_project.root_path
    service.close_project()
    service.open_project(root)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    reference = restored.geometry_inputs[0].reference
    assert PocketStrategy.from_operation_parameters(
        restored.parameters, reference
    ).stepdown.value == 0.5
    assert DATABASE_SCHEMA_VERSION == 4
    workspace.deleteLater()
    application.processEvents()


def test_selection_cancel_island_rejection_and_project_switch_are_stale_safe(
    tmp_path,
) -> None:
    application = _application()
    service, _project, workspace, _viewer, _selected = _workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None and session.field_action_callback is not None
    before = session.applied_mapping()
    workspace._contour_pick_provider = lambda: (_ for _ in ()).throw(
        ValueError("selection cancelled")
    )
    with pytest.raises(ValueError, match="cancelled"):
        session.field_action_callback("select_geometry", before)
    assert session.applied_mapping() == before
    stale = ResolvedPocketGeometry(
        GeometryResolutionStatus.INVALID,
        diagnostics=(
            ValidationDiagnostic(
                DiagnosticSeverity.ERROR,
                DiagnosticCode.POCKET_PROFILE_INVALID,
                "Pocket v1 does not support islands or inner loops",
            ),
        ),
    )
    workspace._contour_pick_provider = lambda: _selected["reference"]
    workspace._pocket_resolver = lambda _reference: stale
    with pytest.raises(ValueError, match="islands"):
        session.field_action_callback("select_geometry", before)
    service.new_project(tmp_path, "Other Project")
    diagnostics = session.validation_callback(before)
    assert diagnostics and diagnostics[0].code == "pocket.ui.stale_editor"
    workspace.deleteLater()
    application.processEvents()
