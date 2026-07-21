"""Stage 9A.5.1 production Function Editor contracts for both Facing variants."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem, QWidget

from hms_cadcam.cam.application import FacingGenerator, basic_mill_resources
from hms_cadcam.cam.domain import (
    CamNodeId,
    DirtyReason,
    FacingBoundarySource,
    FacingCutDirection,
    FacingParameters,
    FeedRate,
    FeedUnit,
    GeometryFingerprint,
    GeometryInputId,
    GeometryInputRole,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
    Length,
    LengthUnit,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationGeometryInput,
    OperationId,
    Point3,
    ResolvedMachiningGeometry,
    Revision,
    SpindleSpeed,
    ToolAssemblyReference,
    GeometryResolutionStatus,
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
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.ui.cam_ui import _default_setup
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftState,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorHost,
    FunctionEditorPage,
    FunctionEditorProductionSession,
    FunctionEditorSection,
    FunctionEditorStateStore,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.function_editor.strategies.common_milling import (
    FacingEditorContext,
    FacingEditorDraftContext,
    FacingEditorVariant,
    facing_applied_values,
    prepare_facing_update,
    validate_facing_schema_contract,
)
from hms_cadcam.ui.function_editor.strategies.facing import build_facing_schema
from hms_cadcam.ui.function_editor.strategies.planar_face_facing import (
    build_planar_face_facing_schema,
)
from tests.unit.test_cam_facing import _descriptor
from tests.unit._fanuc_fixtures import fixture_context
from tests.unit.test_fanuc_robodrill_21i_runtime import _robodrill_machine


def _parameters(boundary: FacingBoundarySource) -> FacingParameters:
    unit = LengthUnit.MM
    return FacingParameters(
        unit,
        boundary,
        Length(50.0, unit),
        Length(48.0, unit),
        Length(1.0, unit),
        Length(5.0, unit),
        Length(0.0, unit),
        Length(55.0, unit),
        Length(52.0, unit),
        FeedRate(500.0, FeedUnit.MM_PER_MINUTE),
        FeedRate(100.0, FeedUnit.MM_PER_MINUTE),
        SpindleSpeed(1000.0),
        FacingCutDirection.BIDIRECTIONAL,
        0.0,
        Length(1.0, unit),
    )


def _face_reference(*, hint: str = "Top face") -> GeometryReference:
    source_id = uuid4()
    return GeometryReference(
        GeometryReferenceId.new(),
        HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
        source_id,
        GeometryReferenceKind.FACE,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"source": str(source_id), "face": 1}),
        Revision(0),
        subshape_selector="hms_face_v1:" + "a" * 64 + ":" + "b" * 64,
        hint=hint,
    )


def _context(
    variant: FacingEditorVariant = FacingEditorVariant.STOCK,
) -> FacingEditorContext:
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    reference = _face_reference() if variant is FacingEditorVariant.PLANAR_FACE else None
    geometry_inputs = ()
    if reference is not None:
        geometry_inputs = (
            OperationGeometryInput(
                GeometryInputId.new(),
                GeometryInputRole.BOUNDARY,
                reference,
                True,
                GeometryReferenceKind.FACE,
                0,
            ),
        )
    requirement = MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        (OperationCapability.MILLING,),
    )
    operation = Operation(
        OperationId.new(),
        CamNodeId.new(),
        OperationFamily.MILLING,
        setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly),
        geometry_inputs,
        _parameters(variant.boundary_source).to_operation_parameters(),
        requirement,
    )
    return FacingEditorContext(
        "Facing",
        operation,
        setup,
        (assembly,),
        (tool,),
        (holder,),
        (machine,),
        reference,
        reference is not None,
    )


def _schema_and_values(
    variant: FacingEditorVariant,
) -> tuple[FacingEditorContext, FunctionEditorSchema, dict[str, object]]:
    context = _context(variant)
    schema = (
        build_facing_schema(context)
        if variant is FacingEditorVariant.STOCK
        else build_planar_face_facing_schema(context)
    )
    return context, schema, dict(facing_applied_values(context, variant))


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dispose(widget: QWidget, application: QApplication) -> None:
    widget.close()
    widget.deleteLater()
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _session(
    context: FacingEditorContext,
    *,
    project_key: str = "project",
    generation: int = 1,
    calculated: list[str] | None = None,
) -> FunctionEditorProductionSession:
    variant = (
        FacingEditorVariant.STOCK
        if FacingParameters.from_operation_parameters(
            context.operation.parameters
        ).boundary_source
        is FacingBoundarySource.STOCK_BOX
        else FacingEditorVariant.PLANAR_FACE
    )
    schema = (
        build_facing_schema(context)
        if variant is FacingEditorVariant.STOCK
        else build_planar_face_facing_schema(context)
    )
    values = facing_applied_values(context, variant)
    return FunctionEditorProductionSession(
        ("operation", str(context.operation.node_id)),
        schema,
        tuple((field.field_id, values[field.field_id]) for field in schema.fields),
        project_key,
        str(context.operation.operation_id),
        generation,
        lambda _values: True,
        lambda _values: (),
        lambda _request: "Preview CURRENT",
        lambda _values: calculated.append(str(context.operation.operation_id))
        if calculated is not None
        else True,
        lambda action_id, _values: (
            {"geometry_summary": "Selected", "geometry_reference_id": "selected"}
            if action_id == "select_geometry"
            else None
        ),
    )


@pytest.mark.parametrize("variant", tuple(FacingEditorVariant))
def test_schema_is_typed_stable_and_deterministic(variant: FacingEditorVariant) -> None:
    context, schema, values = _schema_and_values(variant)
    rebuilt = (
        build_facing_schema(context)
        if variant is FacingEditorVariant.STOCK
        else build_planar_face_facing_schema(context)
    )
    assert schema == rebuilt
    assert tuple(section.section_id for section in schema.ordered_sections) == (
        "basic",
        "geometry",
        "tool",
        "cutting",
        "levels",
        "linking",
        "advanced",
    )
    assert {field.field_id for field in schema.fields} == set(values)
    assert len({field.field_id for field in schema.fields}) == len(schema.fields)
    assert all(field.binding_key and field.help_key for field in schema.fields)
    assert schema.footer.actions == (
        FunctionEditorAction.RESET_DRAFT,
        FunctionEditorAction.PREVIEW,
        FunctionEditorAction.VALIDATE,
        FunctionEditorAction.APPLY,
        FunctionEditorAction.CALCULATE,
        FunctionEditorAction.CLOSE,
    )
    assert all(
        section.disclosure_level is not ParameterDisclosureLevel.EXPERT
        for section in schema.sections
    )
    assert not schema.section("linking").default_expanded
    assert not schema.section("advanced").default_expanded


def test_variants_have_separate_geometry_contracts_and_no_false_planar_field() -> None:
    _stock_context, stock, _stock_values = _schema_and_values(FacingEditorVariant.STOCK)
    _face_context, planar, _face_values = _schema_and_values(FacingEditorVariant.PLANAR_FACE)
    stock_ids = {field.field_id for field in stock.fields}
    planar_ids = {field.field_id for field in planar.fields}
    assert stock.editor_id != planar.editor_id
    assert stock.strategy != planar.strategy
    assert {"geometry_bounds", "overtravel"} <= stock_ids
    assert "geometry_reference_id" not in stock_ids
    assert "geometry_reference_id" in planar_ids
    assert "geometry_bounds" not in planar_ids
    assert "overtravel" not in planar_ids
    assert planar.field("geometry_summary").action_id == "select_geometry"
    assert stock.field("geometry_summary").action_id == ""


@pytest.mark.parametrize("variant", tuple(FacingEditorVariant))
def test_sources_defaults_and_basic_disclosure_are_explicit(
    variant: FacingEditorVariant,
) -> None:
    _context_value, schema, values = _schema_and_values(variant)
    basic = schema.visible_sections(values, ParameterDisclosureLevel.BASIC)
    assert tuple(section.section_id for section in basic) == (
        "basic",
        "geometry",
        "tool",
        "cutting",
        "levels",
    )
    assert schema.field("stepover").default == "5.0"
    assert schema.field("tool_details").source.value == "tool"
    expected_source = "stock" if variant is FacingEditorVariant.STOCK else "geometry"
    assert schema.field("geometry_summary").source.value == expected_source
    assert schema.field("target_height").source.value == (
        "user" if variant is FacingEditorVariant.STOCK else "geometry"
    )


def test_duplicate_and_unsupported_field_mappings_fail_closed() -> None:
    context = _context()
    schema = build_facing_schema(context)
    duplicate = FunctionEditorSection(
        "duplicate",
        "DUPLICATE",
        (FunctionEditorField("operation_name", "Duplicate"),),
    )
    with pytest.raises(ValueError, match="Duplicate field ID"):
        replace(schema, sections=(*schema.sections, duplicate))
    unsupported = FunctionEditorSection(
        "unsupported",
        "UNSUPPORTED",
        (
            FunctionEditorField(
                "invented_value",
                "Invented",
                binding_key="parameters.invented_value",
            ),
        ),
    )
    with pytest.raises(ValueError, match="unsupported"):
        validate_facing_schema_contract(
            replace(schema, sections=(*schema.sections, unsupported)),
            FacingEditorVariant.STOCK,
        )


@pytest.mark.parametrize("variant", tuple(FacingEditorVariant))
def test_unchanged_round_trip_preserves_exact_operation_and_codec(
    variant: FacingEditorVariant,
) -> None:
    context, _schema, values = _schema_and_values(variant)
    update = prepare_facing_update(
        context,
        FacingEditorDraftContext(context.geometry_reference),
        variant,
        values,
    )
    assert update.operation is context.operation
    assert update.parameters.to_operation_parameters() == context.operation.parameters
    assert update.parameters.fingerprint == _parameters(variant.boundary_source).fingerprint
    assert update.operation.to_dict() == context.operation.to_dict()


def test_planar_round_trip_preserves_hidden_overtravel_and_geometry_input_id() -> None:
    context, _schema, values = _schema_and_values(FacingEditorVariant.PLANAR_FACE)
    original = FacingParameters.from_operation_parameters(context.operation.parameters)
    update = prepare_facing_update(
        context,
        FacingEditorDraftContext(context.geometry_reference),
        FacingEditorVariant.PLANAR_FACE,
        values,
    )
    assert update.parameters.overtravel == original.overtravel
    assert update.operation.geometry_inputs[0].input_id == context.operation.geometry_inputs[0].input_id


def test_name_only_change_does_not_mutate_operation_or_artifact_state() -> None:
    context, _schema, values = _schema_and_values(FacingEditorVariant.STOCK)
    values["operation_name"] = "Facing renamed"
    update = prepare_facing_update(
        context, FacingEditorDraftContext(None), FacingEditorVariant.STOCK, values
    )
    assert update.operation_name == "Facing renamed"
    assert update.operation is context.operation


@pytest.mark.parametrize(
    ("field_id", "value"),
    (("stepover", "4.0"), ("enabled", False)),
)
def test_apply_candidate_matches_legacy_revision_and_dirty_semantics(
    field_id: str, value: object
) -> None:
    context, _schema, values = _schema_and_values(FacingEditorVariant.STOCK)
    values[field_id] = value
    update = prepare_facing_update(
        context, FacingEditorDraftContext(None), FacingEditorVariant.STOCK, values
    )
    assert update.operation.revision == context.operation.revision.next()
    assert update.operation.artifact_state.dirty_reasons[-1] is (
        DirtyReason.PARAMETERS_CHANGED
        if field_id == "stepover"
        else DirtyReason.UPSTREAM_CHANGED
    )


@pytest.mark.parametrize("variant", tuple(FacingEditorVariant))
def test_draft_reset_validate_apply_and_rollback_are_non_mutating(
    variant: FacingEditorVariant,
) -> None:
    context, schema, values = _schema_and_values(variant)
    original = context.operation.to_dict()
    state = FunctionEditorDraftState(schema, values, validation_callback=lambda _values: ())
    state.edit("stepover", "0")
    assert state.is_dirty
    assert state.validate()[0].field_id == "stepover"
    assert context.operation.to_dict() == original
    state.reset_field("stepover")
    state.edit("feed_rate", "600")
    state.edit("spindle_speed", "1200")
    state.reset_section("cutting")
    assert not state.is_dirty
    state.edit("stepover", "4")
    assert not state.apply(lambda _snapshot: False)
    assert state.is_dirty and context.operation.to_dict() == original
    state.reset_draft()
    assert not state.is_dirty
    state.edit("stepover", "4")
    applied: list[dict[str, object]] = []
    assert state.apply(lambda snapshot: applied.append(dict(snapshot)) or True)
    assert applied[0]["stepover"] == "4"
    assert not state.is_dirty and context.operation.to_dict() == original


def test_prepare_rejects_deleted_tool_machine_and_geometry() -> None:
    context, _schema, values = _schema_and_values(FacingEditorVariant.PLANAR_FACE)
    missing_tool = replace(context, tool_assemblies=())
    with pytest.raises(ValueError, match="Tool Assembly"):
        prepare_facing_update(
            missing_tool,
            FacingEditorDraftContext(context.geometry_reference),
            FacingEditorVariant.PLANAR_FACE,
            values,
        )
    missing_machine = replace(context, machine_definitions=())
    with pytest.raises(ValueError, match="Máy"):
        prepare_facing_update(
            missing_machine,
            FacingEditorDraftContext(context.geometry_reference),
            FacingEditorVariant.PLANAR_FACE,
            values,
        )
    values["geometry_reference_id"] = str(GeometryReferenceId.new())
    with pytest.raises(ValueError, match="persistent FACE"):
        prepare_facing_update(
            context,
            FacingEditorDraftContext(context.geometry_reference),
            FacingEditorVariant.PLANAR_FACE,
            values,
        )


def test_disabled_operation_can_apply_but_cannot_calculate() -> None:
    _context_value, schema, values = _schema_and_values(FacingEditorVariant.STOCK)
    state = FunctionEditorDraftState(schema, values, validation_callback=lambda _values: ())
    state.edit("enabled", False)
    assert state.apply(lambda _snapshot: True)
    assert state.applied_values["enabled"] is False
    assert not state.can_calculate
    with pytest.raises(RuntimeError, match="applied state"):
        state.calculation_snapshot()


def test_multi_field_geometry_merge_rejects_unsupported_mapping_atomically() -> None:
    _context_value, schema, values = _schema_and_values(
        FacingEditorVariant.PLANAR_FACE
    )
    state = FunctionEditorDraftState(schema, values)
    before = dict(state.values)
    with pytest.raises(KeyError, match="unsupported_geometry_value"):
        state.edit_many(
            {
                "target_height": "47.0",
                "unsupported_geometry_value": "must fail",
            }
        )
    assert dict(state.values) == before


@pytest.mark.parametrize("variant", tuple(FacingEditorVariant))
def test_new_editor_generates_same_toolpath_ir_and_fingerprint_as_legacy_input(
    variant: FacingEditorVariant,
) -> None:
    context, _schema, values = _schema_and_values(variant)
    machine = _robodrill_machine()
    requirement = MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        (OperationCapability.MILLING,),
    )
    context = replace(
        context,
        operation=replace(context.operation, machine_requirement=requirement),
        setup=replace(context.setup, work_offset=WorkOffset("PRIMARY", 1)),
        machine_definitions=(machine,),
    )
    values = dict(facing_applied_values(context, variant))
    update = prepare_facing_update(
        context,
        FacingEditorDraftContext(context.geometry_reference),
        variant,
        values,
    )
    resolved_face = None
    if variant is FacingEditorVariant.PLANAR_FACE:
        points = tuple(
            Point3(x, y, 48.0, LengthUnit.MM)
            for x, y in ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
        )
        descriptor, geometry_input = _descriptor(points)
        operation = replace(context.operation, geometry_inputs=(geometry_input,))
        context = replace(
            context,
            operation=operation,
            geometry_reference=geometry_input.reference,
            geometry_resolved=True,
        )
        values = dict(facing_applied_values(context, variant))
        update = prepare_facing_update(
            context,
            FacingEditorDraftContext(context.geometry_reference),
            variant,
            values,
        )
        resolved_face = ResolvedMachiningGeometry(
            GeometryResolutionStatus.RESOLVED, descriptor
        )
    generator = FacingGenerator()
    legacy_inputs = generator.resolve_inputs(
        context.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=update.machine,
        resolved_face=resolved_face,
    )
    migrated_inputs = generator.resolve_inputs(
        update.operation,
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=update.machine,
        resolved_face=resolved_face,
    )
    assert migrated_inputs.input_fingerprint == legacy_inputs.input_fingerprint
    legacy_computing, legacy_token = generator.begin(legacy_inputs)
    migrated_computing, migrated_token = generator.begin(migrated_inputs)
    legacy_artifact = generator.generate(legacy_computing)
    migrated_artifact = generator.generate(migrated_computing)
    assert migrated_artifact.artifact_fingerprint == legacy_artifact.artifact_fingerprint
    legacy_payload = artifact_to_dict(legacy_artifact)
    migrated_payload = artifact_to_dict(migrated_artifact)
    legacy_payload.pop("computation_token")
    migrated_payload.pop("computation_token")
    assert migrated_payload == legacy_payload

    sampling_policy = SimulationSamplingPolicy(max_linear_step=10.0)
    assert sample_toolpath(
        artifact=legacy_artifact,
        wcs=context.setup.wcs,
        policy=sampling_policy,
    ) == sample_toolpath(
        artifact=migrated_artifact,
        wcs=context.setup.wcs,
        policy=sampling_policy,
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
    assert legacy_published.accepted and migrated_published.accepted
    holder = context.holder_definitions[0]
    legacy_simulation_request = build_simulation_request(
        operation=legacy_published.operation,
        artifact=legacy_artifact,
        setup=context.setup,
        tool=update.tool,
        assembly=update.assembly,
        holder=holder,
        machine=machine,
        sampling_policy=sampling_policy,
        safe_height=55.0,
    )
    migrated_simulation_request = build_simulation_request(
        operation=migrated_published.operation,
        artifact=migrated_artifact,
        setup=context.setup,
        tool=update.tool,
        assembly=update.assembly,
        holder=holder,
        machine=machine,
        sampling_policy=sampling_policy,
        safe_height=55.0,
    )
    far_bounds = Bounds3(
        Point3(1000.0, 1000.0, 1000.0, LengthUnit.MM),
        Point3(1100.0, 1100.0, 1100.0, LengthUnit.MM),
    )
    scene = CollisionScene(
        CollisionTarget("far-stock", CollisionTargetKind.STOCK, far_bounds)
    )
    legacy_simulation = SimulationRuntimeService().run(
        request=legacy_simulation_request,
        artifact=legacy_artifact,
        setup=context.setup,
        tool=update.tool,
        assembly=update.assembly,
        holder=holder,
        scene=scene,
        backend=InMemoryAabbBackend(),
    )
    migrated_simulation = SimulationRuntimeService().run(
        request=migrated_simulation_request,
        artifact=migrated_artifact,
        setup=context.setup,
        tool=update.tool,
        assembly=update.assembly,
        holder=holder,
        scene=scene,
        backend=InMemoryAabbBackend(),
    )
    assert legacy_simulation.accepted and migrated_simulation.accepted
    assert legacy_simulation.result is not None
    assert migrated_simulation.result is not None
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
        fixture_context(legacy_source, file_name="facing_equivalence.fn"),
        safe_z=Length(55.0, LengthUnit.MM),
    )
    gate = SimulationGatePolicy(SimulationGateMode.OPTIONAL)
    legacy_request = PostRequest(
        project_id,
        legacy_source.operation.operation_id,
        legacy_artifact.artifact_id,
        definition,
        simulation_gate_policy=gate,
        program_context=program_context,
    )
    migrated_request = PostRequest(
        project_id,
        migrated_source.operation.operation_id,
        migrated_artifact.artifact_id,
        definition,
        simulation_gate_policy=gate,
        program_context=program_context,
    )
    legacy_program = lower_toolpath(legacy_request, legacy_source)
    migrated_program = lower_toolpath(migrated_request, migrated_source)
    assert migrated_program.records == legacy_program.records
    assert migrated_program.diagnostics == legacy_program.diagnostics
    adapter = FanucRobodrill21iAdapter(definition)
    legacy_output = adapter.format_program(legacy_program, definition)
    migrated_output = adapter.format_program(migrated_program, definition)
    assert migrated_output == legacy_output
    assert adapter.validate_output(legacy_output, legacy_program, definition) == ()
    assert adapter.validate_output(migrated_output, migrated_program, definition) == ()


def test_session_rejects_missing_or_duplicate_applied_field_mapping() -> None:
    context, schema, values = _schema_and_values(FacingEditorVariant.STOCK)
    callbacks = {
        "apply_callback": lambda _values: True,
        "validation_callback": lambda _values: (),
        "preview_callback": lambda _request: None,
        "calculate_callback": lambda _values: None,
    }
    with pytest.raises(ValueError, match="do not match"):
        FunctionEditorProductionSession(
            ("operation", str(context.operation.node_id)),
            schema,
            tuple(values.items())[:-1],
            "project",
            str(context.operation.operation_id),
            1,
            **callbacks,
        )
    duplicate = (*tuple(values.items()), tuple(values.items())[0])
    with pytest.raises(ValueError, match="unique"):
        FunctionEditorProductionSession(
            ("operation", str(context.operation.node_id)),
            schema,
            duplicate,
            "project",
            str(context.operation.operation_id),
            1,
            **callbacks,
        )


def test_qsettings_store_keeps_only_presentation_preferences(tmp_path) -> None:
    _context_value, schema, values = _schema_and_values(FacingEditorVariant.STOCK)
    settings = QSettings(str(tmp_path / "function-editor.ini"), QSettings.Format.IniFormat)
    store = FunctionEditorStateStore(settings)
    state = FunctionEditorDraftState(schema, values)
    state.edit("stepover", "3.25")
    store.save(schema, store.load(schema))
    settings.sync()
    persisted = (tmp_path / "function-editor.ini").read_text(encoding="utf-8")
    assert "3.25" not in persisted
    assert str(state.operation_key) not in persisted


def test_same_display_names_are_disambiguated_by_typed_ids() -> None:
    context = _context()
    assembly = context.tool_assemblies[0]
    other = replace(assembly, assembly_id=type(assembly.assembly_id).new())
    schema = build_facing_schema(replace(context, tool_assemblies=(assembly, other)))
    field = schema.field("tool_assembly_id")
    assert len(field.choices) == 2
    assert field.choices[0] != field.choices[1]
    assert all(str(choice) in dict(field.choice_labels) for choice in field.choices)


@pytest.mark.parametrize("variant", tuple(FacingEditorVariant))
def test_production_page_preview_apply_calculate_policy_and_responsive_widths(
    variant: FacingEditorVariant,
) -> None:
    application = _application()
    context, schema, values = _schema_and_values(variant)
    applied: list[dict[str, object]] = []
    previews: list[object] = []
    calculations: list[dict[str, object]] = []
    page = FunctionEditorPage(
        FunctionEditorDraftState(schema, values, generation=1),
        apply_callback=lambda snapshot: applied.append(dict(snapshot)) or True,
        preview_callback=lambda request: previews.append(request) or "Preview CURRENT",
        calculate_callback=lambda snapshot: calculations.append(dict(snapshot)),
        field_action_callback=lambda _action, _values: None,
    )
    page.show()
    for width in (300, 360, 420, 520):
        page.resize(width, 680)
        application.processEvents()
        assert page.scroll_area.horizontalScrollBar().maximum() == 0
        assert page.footer.isVisible()
        assert page._compact is (width < 400)
    page._field_changed("stepover", "4.0")
    page.footer.buttons[FunctionEditorAction.PREVIEW].click()
    assert len(previews) == 1
    assert applied == [] and calculations == []
    assert page.state.is_dirty
    assert page.preview_status.text() == "Preview CURRENT"
    assert not page.footer.buttons[FunctionEditorAction.CALCULATE].isEnabled()
    page.footer.buttons[FunctionEditorAction.APPLY].click()
    assert len(applied) == 1 and calculations == []
    assert not page.state.is_dirty
    page.footer.buttons[FunctionEditorAction.CALCULATE].click()
    assert calculations[0]["stepover"] == "4.0"
    _dispose(page, application)


def test_planar_select_action_updates_draft_primitives_only() -> None:
    application = _application()
    context, schema, values = _schema_and_values(FacingEditorVariant.PLANAR_FACE)
    original = context.operation.to_dict()
    actions: list[str] = []

    def select(action_id: str, _values) -> dict[str, str]:
        actions.append(action_id)
        return {
            "geometry_summary": "Replacement FACE · RESOLVED",
            "geometry_reference_id": "replacement-reference-id",
            "target_height": "47.0",
        }

    page = FunctionEditorPage(
        FunctionEditorDraftState(schema, values, generation=1),
        field_action_callback=select,
    )
    page.show()
    application.processEvents()
    field = page._field_widgets["geometry_summary"]
    assert field.action_button.isVisible()
    field.action_button.click()
    application.processEvents()
    assert actions == ["select_geometry"]
    assert page.state.values["target_height"] == "47.0"
    assert page.state.is_dirty
    assert context.operation.to_dict() == original
    _dispose(page, application)


def test_host_opens_production_and_cancel_restores_dirty_operation_selection() -> None:
    application = _application()
    first_context = _context(FacingEditorVariant.STOCK)
    second_context = replace(
        _context(FacingEditorVariant.STOCK), operation_name="Facing second"
    )
    first_session = _session(first_context)
    second_session = _session(second_context)
    tree = QTreeWidget()
    first_item = QTreeWidgetItem(["Facing first", "DIRTY"])
    second_item = QTreeWidgetItem(["Facing second", "DIRTY"])
    tree.addTopLevelItems((first_item, second_item))
    tree.setCurrentItem(first_item)
    decisions = ["cancel"]
    sessions = {first_item: first_session, second_item: second_session}

    def restore(kind: str, identity: str) -> bool:
        assert kind == "operation"
        item = first_item if identity == first_session.selection_key[1] else second_item
        tree.setCurrentItem(item)
        return True

    host = FunctionEditorHost(
        QWidget(),
        tree,
        lambda: None,
        production_provider=lambda: sessions[tree.currentItem()],
        selection_restore=restore,
        selection_exists=lambda _key: True,
        switch_confirmation=lambda _state: decisions[-1],
    )
    host.show()
    application.processEvents()
    assert host.current_mode == "framework"
    assert host.active_page is not None
    assert host.active_page.schema.editor_id == "facing_production_9a5_1"
    host.active_page._field_changed("stepover", "4")
    tree.setCurrentItem(second_item)
    application.processEvents()
    assert tree.currentItem() is first_item
    assert host.active_page.state.is_dirty
    decisions.append("discard")
    tree.setCurrentItem(second_item)
    application.processEvents()
    assert host.active_page is not None
    assert host.active_page.state.operation_key == str(second_context.operation.operation_id)
    assert host.stack.count() == 2
    _dispose(host, application)


def test_host_schema_failure_falls_back_to_legacy_with_diagnostic() -> None:
    application = _application()
    tree = QTreeWidget()
    tree.addTopLevelItem(QTreeWidgetItem(["Facing", "DIRTY"]))
    tree.setCurrentItem(tree.topLevelItem(0))
    diagnostics: list[str] = []

    def broken_provider() -> FunctionEditorProductionSession:
        raise ValueError("broken facing schema")

    host = FunctionEditorHost(
        QWidget(),
        tree,
        lambda: None,
        production_provider=broken_provider,
        fallback_callback=diagnostics.append,
    )
    host.show()
    application.processEvents()
    assert host.current_mode == "legacy"
    assert host.mode_label.text() == "FALLBACK"
    assert diagnostics == ["broken facing schema"]
    assert "broken facing schema" in host.legacy_adapter.state_summary.text()
    _dispose(host, application)


def test_switching_fifty_production_operations_keeps_one_page_and_never_calculates() -> None:
    application = _application()
    tree = QTreeWidget()
    sessions: dict[QTreeWidgetItem, FunctionEditorProductionSession] = {}
    calculated: list[str] = []
    for index in range(50):
        item = QTreeWidgetItem([f"Facing duplicate {index % 2}", "DIRTY"])
        tree.addTopLevelItem(item)
        sessions[item] = _session(
            replace(_context(), operation_name=f"Facing {index}"),
            calculated=calculated,
        )
    tree.setCurrentItem(tree.topLevelItem(0))
    host = FunctionEditorHost(
        QWidget(),
        tree,
        lambda: None,
        production_provider=lambda: sessions[tree.currentItem()],
        selection_exists=lambda _key: True,
        switch_confirmation=lambda _state: "discard",
    )
    host.show()
    for index in range(50):
        tree.setCurrentItem(tree.topLevelItem(index))
        application.processEvents()
        assert host.stack.count() == 2
    assert host.active_page is not None
    assert calculated == []
    assert host.active_page.state.operation_key == sessions[
        tree.topLevelItem(49)
    ].operation_key
    _dispose(host, application)


def test_real_workspace_draft_apply_calculate_save_open_lifecycle(tmp_path) -> None:
    application = _application()
    source = tmp_path / "facing-editor.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    project = service.create_project_from_source(tmp_path, "Facing Editor", source)
    workspace = CamWorkspace(
        service, lambda: project.manifest.source_files[0].source_id
    )
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_resources()
    workspace.add_operation()
    service.save()
    assert not service.is_dirty
    assert service._database.current_schema_version(project.root_path / "project.db") == 4
    assert DATABASE_SCHEMA_VERSION == 4

    session = workspace.production_function_editor_session()
    assert session is not None
    assert session.schema.editor_id == "facing_production_9a5_1"
    disabled = session.applied_mapping()
    disabled["enabled"] = False
    assert session.validation_callback(disabled) == ()
    state = FunctionEditorDraftState(
        session.schema,
        session.applied_mapping(),
        project_key=session.project_key,
        operation_key=session.operation_key,
        generation=session.generation,
        validation_callback=session.validation_callback,
    )
    state.edit("stepover", "4.0")
    assert not service.is_dirty
    assert not service.cam_snapshot.artifacts
    assert state.apply(session.apply_callback)
    application.processEvents()
    assert service.is_dirty
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert FacingParameters.from_operation_parameters(operation.parameters).stepover.value == 4.0
    assert not service.cam_snapshot.artifacts

    refreshed = workspace.production_function_editor_session()
    assert refreshed is not None
    refreshed.calculate_callback(refreshed.applied_mapping())
    application.processEvents()
    assert service.cam_snapshot.artifacts
    root = project.root_path
    service.save()
    assert not service.is_dirty
    service.close_project()
    service.open_project(root)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert FacingParameters.from_operation_parameters(restored.parameters).stepover.value == 4.0
    assert not service.is_dirty
    service.close_project()
    _dispose(workspace, application)
