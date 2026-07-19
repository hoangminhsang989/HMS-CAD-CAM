"""Stage 7B.6.2 drilling strategy, semantic path and publish tests."""

from dataclasses import replace
from uuid import uuid4

import pytest

from hms_cadcam.cam.application import (
    DrillingGenerationError,
    DrillingGenerator,
    basic_mill_resources,
    drilling_peck_levels,
)
from hms_cadcam.cam.domain import (
    Angle,
    AngleUnit,
    ArtifactStatus,
    CamNodeId,
    DiagnosticCode,
    DiagnosticSeverity,
    DirtyReason,
    DrillApproachPolicy,
    DrillDepthDefinition,
    DrillGeometry,
    DrillGeometryInput,
    DrillRetractPolicy,
    DrillValidationError,
    DrillingCycle,
    DrillingRegion,
    DrillingStrategy,
    FeedRate,
    FeedUnit,
    GeometryFingerprint,
    GeometryInputId,
    GeometryInputRole,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionStatus,
    HoleLocation,
    HolePattern,
    HoleReference,
    HoleSourceKind,
    Length,
    LengthUnit,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationGeometryInput,
    OperationId,
    Point3,
    ResolvedDrillingGeometry,
    Revision,
    ShankGeometry,
    SpindleSpeed,
    ToolAssembly,
    ToolAssemblyId,
    ToolAssemblyReference,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    ValidationDiagnostic,
    Vector3,
)
from hms_cadcam.cam.toolpath import (
    DwellEvent,
    LinearMove,
    MarkerEvent,
    MotionClass,
    RapidMove,
    publish_toolpath,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import _default_setup


def _pattern(*points: tuple[float, float], unit: LengthUnit = LengthUnit.MM) -> HolePattern:
    return HolePattern(tuple(
        HoleLocation(
            Point3(x, y, 0, unit), Vector3(0, 0, 1), Point3(x, y, 0, unit),
            Length(6, unit), unit,
        )
        for x, y in points
    ), unit)


def _strategy(
    cycle: DrillingCycle = DrillingCycle.DRILL,
    *,
    pattern: HolePattern | None = None,
    **changes,
) -> DrillingStrategy:
    unit = LengthUnit.MM
    values = dict(
        unit=unit,
        geometry=DrillGeometryInput(pattern or _pattern((0, 0)), unit),
        depth=DrillDepthDefinition(unit, Length(0, unit), Length(-5, unit)),
        cycle=cycle,
        clearance_height=Length(8, unit),
        retract_height=Length(3, unit),
        feed_rate=FeedRate(120, FeedUnit.MM_PER_MINUTE),
        spindle_speed=SpindleSpeed(1500),
        dwell_seconds=0.0,
        peck_depth=Length(2, unit) if cycle is DrillingCycle.PECK_DRILL else None,
        retract_policy=DrillRetractPolicy.RETRACT_HEIGHT,
        approach_policy=DrillApproachPolicy.RAPID_CLEARANCE_FEED_RETRACT,
        tolerance=Length(1.0e-7, unit),
    )
    values.update(changes)
    return DrillingStrategy(**values)


def _resources(cycle: DrillingCycle):
    _end_mill, holder, _assembly, machine = basic_mill_resources(LengthUnit.MM)
    family = (
        ToolFamily.CENTER_DRILL
        if cycle is DrillingCycle.SPOT_DRILL else ToolFamily.DRILL
    )
    tool = ToolDefinition(
        ToolDefinitionId.new(), family.value, family, LengthUnit.MM,
        DrillGeometry(Length(6, LengthUnit.MM), Length(30, LengthUnit.MM),
                      Angle(118, AngleUnit.DEGREE)),
        Length(100, LengthUnit.MM), Length(40, LengthUnit.MM),
        ShankGeometry(Length(6, LengthUnit.MM), Length(60, LengthUnit.MM)),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Drill assembly", tool,
        Length(35, LengthUnit.MM), Length(80, LengthUnit.MM), holder,
    )
    capabilities = replace(
        machine.capabilities,
        operations=(OperationCapability.DRILLING, OperationCapability.MILLING),
    )
    machine = replace(machine, capabilities=capabilities)
    return tool, holder, assembly, machine


def _inputs(
    cycle: DrillingCycle = DrillingCycle.DRILL,
    *,
    pattern: HolePattern | None = None,
    strategy_changes: dict | None = None,
):
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    strategy = _strategy(cycle, pattern=pattern, **(strategy_changes or {}))
    tool, holder, assembly, machine = _resources(cycle)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    region = DrillingRegion(
        strategy.geometry, strategy.geometry.source, strategy.depth, strategy.unit,
        GeometryFingerprint.from_payload({"pattern": strategy.geometry.to_dict()}),
    )
    resolved = ResolvedDrillingGeometry(GeometryResolutionStatus.RESOLVED, region)
    generator = DrillingGenerator()
    inputs = generator.resolve_inputs(
        operation, setup, assembly=assembly, tool=tool, machine=machine,
        resolved_geometry=resolved,
    )
    return generator, inputs, holder, resolved


def _artifact(generator: DrillingGenerator, inputs):
    computing, token = generator.begin(inputs)
    return generator.generate(computing), computing, token


def test_strategy_versioned_round_trip_and_invalid_draft_is_atomic() -> None:
    strategy = _strategy(DrillingCycle.PECK_DRILL)
    assert DrillingStrategy.from_dict(strategy.to_dict()) == strategy
    assert DrillingStrategy.from_operation_parameters(
        strategy.to_operation_parameters()
    ) == strategy
    before = strategy
    with pytest.raises(DrillValidationError) as invalid:
        replace(strategy, peck_depth=Length(0, LengthUnit.MM))
    assert invalid.value.code is DiagnosticCode.DRILL_INVALID_PECK
    assert strategy == before


def test_peck_levels_have_exact_single_final_depth() -> None:
    assert drilling_peck_levels(0, -5, 2, 1.0e-7) == (-2.0, -4.0, -5.0)
    levels = drilling_peck_levels(0, -0.3, 0.1, 1.0e-7)
    assert levels[-1] == -0.3 and levels.count(-0.3) == 1
    with pytest.raises(DrillingGenerationError) as invalid:
        drilling_peck_levels(0, -5, 0, 1.0e-7)
    assert invalid.value.code is DiagnosticCode.DRILL_INVALID_PECK


def test_invalid_depth_and_unknown_unit_fail_closed() -> None:
    with pytest.raises(DrillValidationError) as depth:
        DrillDepthDefinition(
            LengthUnit.MM, Length(0, LengthUnit.MM), Length(0, LengthUnit.MM)
        )
    assert depth.value.code is DiagnosticCode.DRILL_INVALID_DEPTH
    with pytest.raises(DrillValidationError) as unit:
        DrillingStrategy(
            unit=LengthUnit.UNKNOWN,
            geometry=DrillGeometryInput(_pattern((0, 0)), LengthUnit.MM),
            depth=DrillDepthDefinition(
                LengthUnit.MM, Length(0, LengthUnit.MM), Length(-5, LengthUnit.MM)
            ),
            cycle=DrillingCycle.DRILL,
            clearance_height=Length(8, LengthUnit.MM),
            retract_height=Length(3, LengthUnit.MM),
            feed_rate=FeedRate(120, FeedUnit.MM_PER_MINUTE),
            spindle_speed=SpindleSpeed(1500),
        )
    assert unit.value.code is DiagnosticCode.DRILL_UNIT_MISSING


@pytest.mark.parametrize(
    ("cycle", "dwell", "expected_plunges"),
    ((DrillingCycle.SPOT_DRILL, 0.25, 1),
     (DrillingCycle.DRILL, 0.0, 1),
     (DrillingCycle.PECK_DRILL, 0.0, 3)),
)
def test_spot_drill_and_peck_emit_controller_neutral_semantics(
    cycle: DrillingCycle, dwell: float, expected_plunges: int
) -> None:
    generator, inputs, _holder, _resolved = _inputs(
        cycle, strategy_changes={"dwell_seconds": dwell}
    )
    artifact, _computing, _token = _artifact(generator, inputs)
    plunges = tuple(
        event for event in artifact.events
        if isinstance(event, LinearMove) and event.provenance.endswith(".plunge")
    )
    assert len(plunges) == expected_plunges
    assert plunges[-1].end.position.z == -5
    assert any(event.provenance.endswith(".approach") for event in artifact.events)
    assert any(
        isinstance(event, LinearMove) and event.motion_class is MotionClass.RETRACT
        for event in artifact.events
    )
    complete = tuple(
        event for event in artifact.events
        if isinstance(event, MarkerEvent) and event.semantic_key == "drill.hole_complete"
    )
    assert len(complete) == 1
    assert any(isinstance(event, DwellEvent) for event in artifact.events) is (dwell > 0)
    assert all(
        event.start.position.z >= inputs.strategy.retract_height.value
        and event.end.position.z >= inputs.strategy.retract_height.value
        for event in artifact.events if isinstance(event, RapidMove)
    )


def test_multiple_holes_are_canonical_and_artifact_is_deterministic() -> None:
    pattern = _pattern((10, 0), (0, 5), (0, 0))
    generator, inputs, _holder, _resolved = _inputs(pattern=pattern)
    assert tuple((hole.position.x, hole.position.y) for hole in inputs.holes) == (
        (0, 0), (0, 5), (10, 0)
    )
    first, *_ = _artifact(generator, inputs)
    second, *_ = _artifact(generator, inputs)
    assert first.artifact_fingerprint == second.artifact_fingerprint
    assert sum(
        isinstance(event, MarkerEvent) and event.semantic_key == "drill.hole_complete"
        for event in first.events
    ) == 3


def test_persistent_vertex_reference_must_match_operation_and_setup_scope() -> None:
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    reference = GeometryReference(
        GeometryReferenceId.new(), "hms_persistent_geometry", 1,
        setup.source_scope.primary_source_id, GeometryReferenceKind.VERTEX,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"vertex": 1}), Revision(0),
        subshape_selector="vertex:1",
    )
    hole_reference = HoleReference(
        reference, Vector3(0, 0, 1), Point3(4, 7, 0, LengthUnit.MM),
        LengthUnit.MM,
    )
    geometry = DrillGeometryInput(hole_reference, LengthUnit.MM)
    strategy = _strategy(geometry=geometry)
    tool, _holder, assembly, machine = _resources(strategy.cycle)
    operation_input = OperationGeometryInput(
        GeometryInputId.new(), GeometryInputRole.DRIVE_GEOMETRY, reference,
        True, GeometryReferenceKind.VERTEX, 0,
    )
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (operation_input,),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    location = HoleLocation(
        Point3(4, 7, 0, LengthUnit.MM), Vector3(0, 0, 1),
        Point3(4, 7, 0, LengthUnit.MM), None, LengthUnit.MM,
        HoleSourceKind.BREP_VERTEX, hole_reference,
    )
    pattern = HolePattern((location,), LengthUnit.MM)
    region = DrillingRegion(
        geometry, pattern, strategy.depth, strategy.unit,
        GeometryFingerprint.from_payload({"vertex": reference.to_dict()}),
    )
    resolved = ResolvedDrillingGeometry(GeometryResolutionStatus.RESOLVED, region)
    generator = DrillingGenerator()
    inputs = generator.resolve_inputs(
        operation, setup, assembly=assembly, tool=tool, machine=machine,
        resolved_geometry=resolved,
    )
    assert inputs.holes[0].position == Point3(4, 7, 0, LengthUnit.MM)
    with pytest.raises(DrillingGenerationError) as missing:
        generator.resolve_inputs(
            replace(operation, geometry_inputs=()), setup, assembly=assembly,
            tool=tool, machine=machine, resolved_geometry=resolved,
        )
    assert missing.value.code is DiagnosticCode.DRILL_GEOMETRY_MISSING


def test_tool_missing_stale_wrong_type_and_unit_fail_closed() -> None:
    generator, inputs, _holder, resolved = _inputs()
    cases = (
        (None, inputs.tool, DiagnosticCode.DRILL_TOOL_MISSING),
        (replace(inputs.assembly, revision=Revision(1)), inputs.tool,
         DiagnosticCode.DRILL_TOOL_STALE),
        (inputs.assembly, None, DiagnosticCode.DRILL_TOOL_MISSING),
    )
    for assembly, tool, code in cases:
        with pytest.raises(DrillingGenerationError) as failure:
            generator.resolve_inputs(
                inputs.operation, inputs.setup, assembly=assembly, tool=tool,
                machine=inputs.machine, resolved_geometry=resolved,
            )
        assert failure.value.code is code

    end_mill, _holder, end_assembly, _machine = basic_mill_resources(LengthUnit.MM)
    wrong_operation = replace(
        inputs.operation,
        tool_assembly=ToolAssemblyReference.from_assembly(end_assembly),
    )
    with pytest.raises(DrillingGenerationError) as wrong_type:
        generator.resolve_inputs(
            wrong_operation, inputs.setup, assembly=end_assembly, tool=end_mill,
            machine=inputs.machine, resolved_geometry=resolved,
        )
    assert wrong_type.value.code is DiagnosticCode.DRILL_TOOL_INVALID

    wrong_unit_reference = replace(
        inputs.operation.tool_assembly, unit=LengthUnit.INCH
    )
    with pytest.raises(DrillingGenerationError) as wrong_unit:
        generator.resolve_inputs(
            replace(inputs.operation, tool_assembly=wrong_unit_reference), inputs.setup,
            assembly=inputs.assembly, tool=inputs.tool, machine=inputs.machine,
            resolved_geometry=resolved,
        )
    assert wrong_unit.value.code is DiagnosticCode.DRILL_TOOL_INVALID

    no_drilling = replace(
        inputs.machine,
        capabilities=replace(
            inputs.machine.capabilities,
            operations=(OperationCapability.MILLING,),
        ),
    )
    no_drilling_operation = replace(
        inputs.operation,
        machine_requirement=MachineRequirement(
            no_drilling.machine_id, no_drilling.revision,
            no_drilling.content_fingerprint, no_drilling.unit,
            (OperationCapability.DRILLING,),
        ),
    )
    with pytest.raises(DrillingGenerationError) as capability:
        generator.resolve_inputs(
            no_drilling_operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, machine=no_drilling, resolved_geometry=resolved,
        )
    assert capability.value.code is DiagnosticCode.DRILL_INVALID_PARAMETER


def test_strategy_and_wcs_changes_change_input_fingerprint() -> None:
    generator, inputs, _holder, resolved = _inputs()
    changed_strategy = replace(
        inputs.strategy,
        feed_rate=FeedRate(121, FeedUnit.MM_PER_MINUTE),
    )
    changed_operation = replace(
        inputs.operation, parameters=changed_strategy.to_operation_parameters()
    )
    changed_parameters = generator.resolve_inputs(
        changed_operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
        machine=inputs.machine, resolved_geometry=resolved,
    )
    assert changed_parameters.input_fingerprint != inputs.input_fingerprint

    moved_wcs = replace(
        inputs.setup,
        wcs=replace(inputs.setup.wcs, origin=Point3(1, 0, 0, LengthUnit.MM)),
    )
    changed_wcs = generator.resolve_inputs(
        inputs.operation, moved_wcs, assembly=inputs.assembly, tool=inputs.tool,
        machine=inputs.machine, resolved_geometry=resolved,
    )
    assert changed_wcs.input_fingerprint != inputs.input_fingerprint


def test_stale_token_does_not_publish_candidate() -> None:
    generator, inputs, _holder, _resolved = _inputs()
    candidate, computing, token = _artifact(generator, inputs)
    newer_state = computing.operation.artifact_state.mark_dirty(
        DirtyReason.PARAMETERS_CHANGED
    )
    newer_state, _new_token = newer_state.begin(inputs.input_fingerprint)
    current = replace(computing.operation, artifact_state=newer_state)
    result = publish_toolpath(
        current, candidate, token, inputs.input_fingerprint
    )
    assert not result.accepted and result.artifact is None
    assert result.operation == current


def test_recompute_failure_keeps_previous_valid_artifact(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Drilling Core")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    strategy = _strategy()
    tool, holder, assembly, machine = _resources(strategy.cycle)
    service.execute_cam_command(
        lambda app: app.add_basic_resources(tool, holder, assembly, machine)
    )
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    service.execute_cam_command(lambda app: app.update_tree(
        job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Drilling", operation),
    ))
    region = DrillingRegion(
        strategy.geometry, strategy.geometry.source, strategy.depth, strategy.unit,
        GeometryFingerprint.from_payload({"pattern": strategy.geometry.to_dict()}),
    )
    resolved = ResolvedDrillingGeometry(GeometryResolutionStatus.RESOLVED, region)
    success = service.compute_drilling(
        operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert success.accepted and success.operation.artifact_state.status is ArtifactStatus.VALID
    artifact = service.load_toolpath_artifact(operation.operation_id)
    stale = ResolvedDrillingGeometry(
        GeometryResolutionStatus.STALE,
        diagnostics=(ValidationDiagnostic(
            DiagnosticSeverity.ERROR, DiagnosticCode.DRILL_GEOMETRY_STALE, "stale"
        ),),
    )
    failure = service.compute_drilling(
        operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: stale,
    )
    assert not failure.accepted
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(operation.operation_id) == artifact
    service.save()
    service.close_project()
    service.open_project(session.root_path)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert DrillingStrategy.from_operation_parameters(restored.parameters) == strategy
    assert service.load_toolpath_artifact(operation.operation_id) == artifact
