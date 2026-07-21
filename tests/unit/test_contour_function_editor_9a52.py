"""Stage 9A.5.2 production Function Editor contracts for 2D Contour."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from hms_cadcam.cam.application import ContourGenerator, basic_mill_resources
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    CamNodeId,
    ContourCutDirection,
    ContourParameters,
    ContourProfileSource,
    ContourSide,
    DirtyReason,
    FeedRate,
    FeedUnit,
    GeometryResolutionStatus,
    Length,
    LengthUnit,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationId,
    ResolvedContourProfile,
    SpindleSpeed,
    ToolAssemblyReference,
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
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace, _default_setup
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
    ContourEditorContext,
    ContourEditorDraftContext,
    build_contour_schema,
    contour_applied_values,
    prepare_contour_update,
    validate_contour_schema_contract,
)
from tests.unit._fanuc_fixtures import fixture_context
from tests.unit.test_cam_contour import _descriptor
from tests.unit.test_fanuc_robodrill_21i_runtime import _robodrill_machine


def _parameters(**changes: object) -> ContourParameters:
    unit = LengthUnit.MM
    values: dict[str, object] = {
        "unit": unit,
        "profile_source": ContourProfileSource.PLANAR_FACE_OUTER,
        "side": ContourSide.OUTSIDE,
        "top_height": Length(50.0, unit),
        "final_depth": Length(47.0, unit),
        "stepdown": Length(1.0, unit),
        "radial_stock_allowance": Length(0.2, unit),
        "axial_stock_allowance": Length(0.1, unit),
        "clearance_height": Length(55.0, unit),
        "retract_height": Length(52.0, unit),
        "cutting_feed_rate": FeedRate(500.0, FeedUnit.MM_PER_MINUTE),
        "plunge_feed_rate": FeedRate(100.0, FeedUnit.MM_PER_MINUTE),
        "spindle_speed": SpindleSpeed(1000.0),
        "direction": ContourCutDirection.CLIMB,
        "lead_length": Length(1.0, unit),
        "finishing_pass": False,
        "multiple_depth_passes": True,
    }
    values.update(changes)
    return ContourParameters(**values)  # type: ignore[arg-type]


def _context(
    *, parameters: ContourParameters | None = None, machine=None
) -> tuple[ContourEditorContext, object]:
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    setup = replace(setup, work_offset=WorkOffset("PRIMARY", 1))
    tool, holder, assembly, default_machine = basic_mill_resources(LengthUnit.MM)
    selected_machine = machine or default_machine
    descriptor, geometry_input = _descriptor(
        source_id=setup.source_scope.primary_source_id
    )
    requirement = MachineRequirement(
        selected_machine.machine_id,
        selected_machine.revision,
        selected_machine.content_fingerprint,
        selected_machine.unit,
        (OperationCapability.MILLING,),
    )
    operation = Operation(
        OperationId.new(),
        CamNodeId.new(),
        OperationFamily.MILLING,
        setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly),
        (geometry_input,),
        (parameters or _parameters()).to_operation_parameters(),
        requirement,
    )
    return (
        ContourEditorContext(
            "Contour 2D",
            operation,
            setup,
            (assembly,),
            (tool,),
            (holder,),
            (selected_machine,),
            geometry_input.reference,
            True,
            len(descriptor.outer_loop.segments),
            descriptor.outer_loop.orientation.value,
        ),
        descriptor,
    )


def _schema_values() -> tuple[ContourEditorContext, FunctionEditorSchema, dict[str, object]]:
    context, _descriptor_value = _context()
    schema = build_contour_schema(context)
    return context, schema, dict(contour_applied_values(context))


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dispose(widget: QWidget, application: QApplication) -> None:
    widget.close()
    widget.deleteLater()
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_schema_has_stable_complete_deterministic_mapping() -> None:
    context, schema, values = _schema_values()
    assert schema.editor_id == "contour_production_9a5_2"
    assert str(schema.strategy) == "contour_2d_9a5_2"
    assert tuple(section.section_id for section in schema.ordered_sections) == (
        "basic",
        "geometry",
        "tool",
        "cutting",
        "levels",
        "linking",
        "advanced",
        "expert",
    )
    assert tuple(field.field_id for field in schema.fields) == tuple(
        field.field_id for field in build_contour_schema(context).fields
    )
    assert set(values) == {field.field_id for field in schema.fields}
    assert all(field.binding_key for field in schema.fields)
    assert schema.field("start_policy").disclosure_level is ParameterDisclosureLevel.EXPERT
    assert schema.field("finishing_pass").disclosure_level is ParameterDisclosureLevel.ADVANCED
    assert "G41/G42" in str(values["compensation_summary"])
    assert not {"d_offset", "tolerance", "arc_lead", "wear_compensation"} & set(values)


def test_disclosure_and_stepdown_applicability_are_domain_driven() -> None:
    _context_value, schema, values = _schema_values()
    basic_sections = schema.visible_sections(values, ParameterDisclosureLevel.BASIC)
    assert {section.section_id for section in basic_sections} == {
        "basic", "geometry", "tool", "cutting", "levels", "linking"
    }
    assert schema.field("stepdown").is_applicable(values)
    values["multiple_depth_passes"] = False
    assert not schema.field("stepdown").is_applicable(values)
    advanced = schema.visible_sections(values, ParameterDisclosureLevel.ADVANCED)
    assert "advanced" in {section.section_id for section in advanced}
    assert "expert" not in {section.section_id for section in advanced}


def test_sources_defaults_enums_units_and_identity_are_explicit() -> None:
    _context_value, schema, values = _schema_values()
    assert schema.field("geometry_summary").source.value == "geometry"
    assert schema.field("tool_details").source.value == "tool"
    assert schema.field("top_height").unit == "mm"
    assert schema.field("cutting_feed_rate").unit == "mm/min"
    assert schema.field("top_height").default is not None
    assert "Setup/Stock" in schema.field("top_height").default_label
    assert set(schema.field("side").choices) == {"on", "inside", "outside"}
    assert set(schema.field("direction").choices) == {"climb", "conventional"}
    assert values["geometry_reference_id"] != values["geometry_summary"]


def test_duplicate_and_unsupported_field_contracts_fail_closed() -> None:
    _context_value, schema, _values = _schema_values()
    field = FunctionEditorField(
        "invented_compensation",
        "Invented",
        FunctionEditorFieldKind.TEXT,
        "bad",
        binding_key="parameters.invented_compensation",
    )
    unsupported = replace(
        schema,
        sections=schema.sections
        + (FunctionEditorSection("unsupported", "UNSUPPORTED", (field,), order=999),),
    )
    with pytest.raises(ValueError, match="unsupported"):
        validate_contour_schema_contract(unsupported)
    duplicate_section = replace(
        schema.sections[0], fields=schema.sections[0].fields + (schema.sections[0].fields[0],)
    )
    with pytest.raises(ValueError, match="Duplicate field ID"):
        replace(schema, sections=(duplicate_section, *schema.sections[1:]))


def test_unchanged_round_trip_preserves_domain_codec_and_fingerprints() -> None:
    context, _descriptor_value = _context()
    before = ContourParameters.from_operation_parameters(context.operation.parameters)
    values = contour_applied_values(context)
    update = prepare_contour_update(
        context, ContourEditorDraftContext(context.geometry_reference), values
    )
    assert update.operation == context.operation
    assert update.parameters == before
    assert update.parameters.to_dict() == before.to_dict()
    assert update.parameters.to_operation_parameters() == context.operation.parameters
    assert update.parameters.fingerprint == before.fingerprint
    assert update.operation.geometry_inputs == context.operation.geometry_inputs


@pytest.mark.parametrize(
    ("field_id", "presentation_value", "parameter_name", "domain_value"),
    (
        ("side", "inside", "side", ContourSide.INSIDE),
        ("direction", "conventional", "direction", ContourCutDirection.CONVENTIONAL),
        ("radial_stock_allowance", "0.5", "radial_stock_allowance", Length(0.5, LengthUnit.MM)),
        ("axial_stock_allowance", "0.25", "axial_stock_allowance", Length(0.25, LengthUnit.MM)),
        ("lead_length", "2.0", "lead_length", Length(2.0, LengthUnit.MM)),
        ("finishing_pass", True, "finishing_pass", True),
        ("multiple_depth_passes", False, "multiple_depth_passes", False),
    ),
)
def test_ui_binding_matches_legacy_parameter_and_dirty_semantics(
    field_id: str,
    presentation_value: object,
    parameter_name: str,
    domain_value: object,
) -> None:
    context, _descriptor_value = _context()
    values = dict(contour_applied_values(context))
    values[field_id] = presentation_value
    update = prepare_contour_update(
        context, ContourEditorDraftContext(context.geometry_reference), values
    )
    assert getattr(update.parameters, parameter_name) == domain_value
    assert update.operation.revision == context.operation.revision.next()
    assert DirtyReason.PARAMETERS_CHANGED in update.operation.artifact_state.dirty_reasons
    assert update.operation.parameters == update.parameters.to_operation_parameters()


def test_hidden_stepdown_preserves_existing_codec_value() -> None:
    context, _descriptor_value = _context()
    values = dict(contour_applied_values(context))
    values["multiple_depth_passes"] = False
    values.pop("stepdown")
    update = prepare_contour_update(
        context, ContourEditorDraftContext(context.geometry_reference), values
    )
    current = ContourParameters.from_operation_parameters(context.operation.parameters)
    assert update.parameters.multiple_depth_passes is False
    assert update.parameters.stepdown == current.stepdown


def test_draft_invalid_reset_apply_and_rollback_do_not_mutate_domain() -> None:
    context, schema, values = _schema_values()
    state = FunctionEditorDraftState(schema, values)
    before_operation = context.operation
    state.edit("stepdown", "0")
    assert state.validate()
    assert context.operation == before_operation
    assert not state.apply(lambda _values: pytest.fail("invalid draft called Apply"))
    state.reset_field("stepdown")
    state.edit("lead_length", "2")
    assert state.is_dirty
    assert not state.apply(lambda _values: (_ for _ in ()).throw(RuntimeError("rollback")))
    assert context.operation == before_operation
    assert state.applied_values["lead_length"] == "1.0"
    state.reset_draft()
    assert not state.is_dirty


def test_deleted_resources_and_geometry_mismatch_fail_closed() -> None:
    context, _descriptor_value = _context()
    values = contour_applied_values(context)
    with pytest.raises(ValueError, match="Tool Assembly"):
        prepare_contour_update(
            replace(context, tool_assemblies=()),
            ContourEditorDraftContext(context.geometry_reference),
            values,
        )
    with pytest.raises(ValueError, match="Máy"):
        prepare_contour_update(
            replace(context, machine_definitions=()),
            ContourEditorDraftContext(context.geometry_reference),
            values,
        )
    changed_source = dict(values)
    changed_source["profile_source"] = ContourProfileSource.CLOSED_WIRE.value
    with pytest.raises(ValueError, match="không khớp"):
        prepare_contour_update(
            context,
            ContourEditorDraftContext(context.geometry_reference),
            changed_source,
        )


@pytest.mark.parametrize(
    "parameters",
    (
        _parameters(side=ContourSide.OUTSIDE, direction=ContourCutDirection.CLIMB),
        _parameters(side=ContourSide.INSIDE, direction=ContourCutDirection.CONVENTIONAL),
        _parameters(multiple_depth_passes=False),
        _parameters(finishing_pass=True),
        _parameters(radial_stock_allowance=Length(0.5, LengthUnit.MM)),
        _parameters(lead_length=Length(2.0, LengthUnit.MM)),
    ),
)
def test_exact_toolpath_ir_is_unchanged_for_supported_contour_scenarios(
    parameters: ContourParameters,
) -> None:
    context, descriptor = _context(parameters=parameters)
    update = prepare_contour_update(
        context,
        ContourEditorDraftContext(context.geometry_reference),
        contour_applied_values(context),
    )
    resolved = ResolvedContourProfile(GeometryResolutionStatus.RESOLVED, descriptor)
    generator = ContourGenerator()
    legacy_inputs = generator.resolve_inputs(
        context.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=update.machine,
        resolved_profile=resolved,
    )
    migrated_inputs = generator.resolve_inputs(
        update.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=update.machine,
        resolved_profile=resolved,
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
    context, descriptor = _context(machine=machine)
    update = prepare_contour_update(
        context,
        ContourEditorDraftContext(context.geometry_reference),
        contour_applied_values(context),
    )
    resolved = ResolvedContourProfile(GeometryResolutionStatus.RESOLVED, descriptor)
    generator = ContourGenerator()
    legacy_inputs = generator.resolve_inputs(
        context.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=machine,
        resolved_profile=resolved,
    )
    migrated_inputs = generator.resolve_inputs(
        update.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=machine,
        resolved_profile=resolved,
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
    holder = context.holder_definitions[0]
    legacy_request = build_simulation_request(
        operation=legacy_published.operation,
        artifact=legacy_artifact,
        setup=context.setup,
        tool=update.tool,
        assembly=update.assembly,
        holder=holder,
        machine=machine,
        sampling_policy=policy,
        safe_height=55.0,
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
        safe_height=55.0,
    )
    far_bounds = Bounds3(
        context.setup.wcs.origin.__class__(1000.0, 1000.0, 1000.0, LengthUnit.MM),
        context.setup.wcs.origin.__class__(1100.0, 1100.0, 1100.0, LengthUnit.MM),
    )
    scene = CollisionScene(
        CollisionTarget("far-stock", CollisionTargetKind.STOCK, far_bounds)
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
    assert (
        migrated_simulation.result.result_fingerprint
        == legacy_simulation.result.result_fingerprint
    )

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
        fixture_context(
            legacy_source, file_name="contour_equivalence.fn", cutter=True
        ),
        safe_z=Length(55.0, LengthUnit.MM),
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
    context, schema, values = _schema_values()
    calculated: list[object] = []
    state = FunctionEditorDraftState(schema, values, generation=1)
    page = FunctionEditorPage(
        state,
        apply_callback=lambda _values: True,
        preview_callback=lambda _request: "Preview CURRENT",
        calculate_callback=calculated.append,
    )
    page.resize(width, 700)
    page.show()
    application.processEvents()
    page._field_changed("lead_length", "2")
    assert calculated == []
    assert not page.footer.buttons[FunctionEditorAction.CALCULATE].isEnabled()
    assert page.scroll_area.horizontalScrollBar().maximum() == 0
    assert page.footer.isVisible()
    _dispose(page, application)


def _workspace(tmp_path) -> tuple[ProjectService, CamWorkspace, object]:
    source = tmp_path / "contour-source.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Contour 9A52", source)
    descriptor, _geometry_input = _descriptor(
        source_id=session.manifest.source_files[0].source_id
    )
    displayed: list[object] = []
    workspace = CamWorkspace(
        service,
        lambda: session.manifest.source_files[0].source_id,
        toolpath_display=displayed.append,
        contour_pick_provider=lambda: descriptor.reference,
        profile_resolver=lambda _reference: ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED, descriptor
        ),
    )
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_resources()
    workspace.add_contour_operation()
    return service, workspace, displayed


def test_real_workspace_selection_validate_preview_apply_calculate_and_lifecycle(
    tmp_path,
) -> None:
    application = _application()
    service, workspace, displayed = _workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None
    assert session.schema.editor_id == "contour_production_9a5_2"
    before = service.cam_snapshot
    values = session.applied_mapping()
    assert session.field_action_callback is not None
    selected = session.field_action_callback("select_geometry", values)
    assert selected is not None
    values.update(selected)
    values["stepdown"] = "0"
    assert session.validation_callback(values)
    assert service.cam_snapshot == before
    values["stepdown"] = "0.5"
    preview_request = FunctionEditorDraftState(
        session.schema,
        values,
        project_key=session.project_key,
        operation_key=session.operation_key,
        generation=session.generation,
    ).preview_request()
    assert "Preview CURRENT" in str(session.preview_callback(preview_request))
    assert service.cam_snapshot == before
    assert session.apply_callback(values)
    applied = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert applied.artifact_state.status is ArtifactStatus.DIRTY
    assert displayed == []
    refreshed = workspace.production_function_editor_session()
    assert refreshed is not None
    assert refreshed.calculate_callback(refreshed.applied_mapping())
    calculated = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert calculated.artifact_state.status is ArtifactStatus.VALID
    assert displayed
    service.save()
    root = service.current_project.root_path
    service.close_project()
    service.open_project(root)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert ContourParameters.from_operation_parameters(restored.parameters).stepdown.value == 0.5
    assert DATABASE_SCHEMA_VERSION == 4
    workspace.deleteLater()
    application.processEvents()


def test_selection_cancel_and_project_switch_are_stale_safe(tmp_path) -> None:
    application = _application()
    service, workspace, _displayed = _workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None and session.field_action_callback is not None
    before = session.applied_mapping()
    workspace._contour_pick_provider = lambda: (_ for _ in ()).throw(
        ValueError("selection cancelled")
    )
    with pytest.raises(ValueError, match="cancelled"):
        session.field_action_callback("select_geometry", before)
    assert session.applied_mapping() == before
    service.new_project(tmp_path, "Other Project")
    diagnostics = session.validation_callback(before)
    assert diagnostics and diagnostics[0].code == "contour.ui.stale_editor"
    workspace.deleteLater()
    application.processEvents()
