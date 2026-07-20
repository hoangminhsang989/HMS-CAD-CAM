"""Stage 7B.8.1 Reaming validation, semantic path and publish tests."""

from dataclasses import replace
from uuid import uuid4

import pytest

from hms_cadcam.cam.application import (
    ReamingGenerationError,
    ReamingGenerator,
    basic_mill_resources,
)
from hms_cadcam.cam.domain import (
    Angle,
    AngleUnit,
    ArtifactStatus,
    CamNodeId,
    CylindricalGeometry,
    DiagnosticCode,
    DiagnosticSeverity,
    DirtyReason,
    DrillDepthDefinition,
    DrillGeometry,
    DrillGeometryInput,
    DrillingRegion,
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
    MachineCoolantCapability,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationGeometryInput,
    OperationId,
    Point3,
    ReamingCoolantMode,
    ReamingStrategy,
    ResolvedDrillingGeometry,
    Revision,
    ShankGeometry,
    SpindleDirection,
    SpindleSpeed,
    TapGeometry,
    ToolAssembly,
    ToolAssemblyId,
    ToolAssemblyReference,
    ToolCoolantCapability,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    ToolHand,
    ValidationDiagnostic,
    Vector3,
)
from hms_cadcam.cam.toolpath import (
    CoolantState,
    CoolantStateEvent,
    DwellEvent,
    FeedMode,
    FeedModeEvent,
    LinearMove,
    MarkerEvent,
    MotionClass,
    RapidMove,
    SpindleState,
    SpindleStateEvent,
    publish_toolpath,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import _default_setup


def _pattern(*points: tuple[float, float]) -> HolePattern:
    unit = LengthUnit.MM
    return HolePattern(tuple(
        HoleLocation(
            Point3(x, y, 0, unit), Vector3(0, 0, 1),
            Point3(x, y, 0, unit), None, unit,
        )
        for x, y in points
    ), unit)


def _strategy(
    *, pattern: HolePattern | None = None, **changes
) -> ReamingStrategy:
    unit = LengthUnit.MM
    values = dict(
        unit=unit,
        geometry=DrillGeometryInput(pattern or _pattern((0, 0)), unit),
        depth=DrillDepthDefinition(unit, Length(0, unit), Length(-10, unit)),
        nominal_diameter=Length(8, unit),
        pre_hole_diameter=Length(7.8, unit),
        spindle_speed=SpindleSpeed(500),
        feed_per_revolution=FeedRate(0.1, FeedUnit.MM_PER_REVOLUTION),
        clearance_height=Length(8, unit),
        retract_height=Length(3, unit),
        spindle_direction=SpindleDirection.CLOCKWISE,
        coolant=ReamingCoolantMode.OFF,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7, unit),
    )
    values.update(changes)
    return ReamingStrategy(**values)


def _tool_geometry(family: ToolFamily, diameter: float, cutting_length: float):
    unit = LengthUnit.MM
    if family is ToolFamily.REAMER:
        return CylindricalGeometry(
            Length(diameter, unit), Length(cutting_length, unit)
        )
    if family is ToolFamily.DRILL:
        return DrillGeometry(
            Length(diameter, unit), Length(cutting_length, unit),
            Angle(118, AngleUnit.DEGREE),
        )
    if family is ToolFamily.TAP:
        return TapGeometry(
            Length(diameter, unit), Length(cutting_length, unit),
            Length(1.25, unit), ToolHand.RIGHT,
        )
    raise AssertionError("unsupported test tool family")


def _resources(
    strategy: ReamingStrategy,
    *,
    family: ToolFamily = ToolFamily.REAMER,
    tool_diameter: float = 8.0,
    cutting_length: float = 20.0,
    usable_length: float = 30.0,
    stickout: float = 25.0,
    directions: tuple[SpindleDirection, ...] = (SpindleDirection.CLOCKWISE,),
    maximum_feed: float = 5000.0,
    tool_coolant: tuple[ToolCoolantCapability, ...] = (),
    machine_coolant: tuple[MachineCoolantCapability, ...] = (),
):
    _end_mill, holder, _assembly, machine = basic_mill_resources(LengthUnit.MM)
    tool = ToolDefinition(
        ToolDefinitionId.new(),
        "Reamer",
        family,
        LengthUnit.MM,
        _tool_geometry(family, tool_diameter, min(cutting_length, usable_length)),
        Length(80, LengthUnit.MM),
        Length(usable_length, LengthUnit.MM),
        ShankGeometry(Length(8, LengthUnit.MM), Length(50, LengthUnit.MM)),
        coolant_capabilities=tool_coolant,
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Reaming assembly", tool,
        Length(stickout, LengthUnit.MM), Length(60, LengthUnit.MM), holder,
    )
    spindle = replace(
        machine.spindles[0], directions=directions, synchronized_feed=False
    )
    capabilities = replace(
        machine.capabilities,
        maximum_feed=FeedRate(maximum_feed, FeedUnit.MM_PER_MINUTE),
        coolant=machine_coolant,
        operations=(OperationCapability.DRILLING, OperationCapability.MILLING),
    )
    machine = replace(machine, spindles=(spindle,), capabilities=capabilities)
    return tool, holder, assembly, machine


def _resolved(strategy: ReamingStrategy) -> ResolvedDrillingGeometry:
    return ResolvedDrillingGeometry(
        GeometryResolutionStatus.RESOLVED,
        DrillingRegion(
            strategy.geometry,
            strategy.geometry.source,
            strategy.depth,
            strategy.unit,
            GeometryFingerprint.from_payload({"geometry": strategy.geometry.to_dict()}),
        ),
    )


def _inputs(
    *,
    strategy: ReamingStrategy | None = None,
    resource_changes: dict | None = None,
):
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    strategy = strategy or _strategy()
    tool, holder, assembly, machine = _resources(
        strategy, **(resource_changes or {})
    )
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly), (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    resolved = _resolved(strategy)
    generator = ReamingGenerator()
    inputs = generator.resolve_inputs(
        operation, setup, assembly=assembly, tool=tool, machine=machine,
        resolved_geometry=resolved,
    )
    return generator, inputs, holder, resolved


def _artifact(generator: ReamingGenerator, inputs):
    computing, token = generator.begin(inputs)
    return generator.generate(computing), computing, token


def test_semantic_sequence_feed_retract_dwell_lifecycle_and_no_gcode() -> None:
    strategy = _strategy(dwell_seconds=0.2)
    generator, inputs, _holder, _region = _inputs(strategy=strategy)
    artifact, _computing, _token = _artifact(generator, inputs)

    relevant = tuple(
        event for event in artifact.events
        if event.provenance.startswith("ream.hole.0")
    )
    assert tuple(event.provenance for event in relevant) == (
        "ream.hole.0.rapid",
        "ream.hole.0.approach",
        "ream.hole.0.process.begin",
        "ream.hole.0.spindle.begin",
        "ream.hole.0.descent",
        "ream.hole.0.dwell",
        "ream.hole.0.controlled_retract",
        "ream.hole.0.complete",
        "ream.hole.0.final_retract",
        "ream.hole.0.spindle.end",
        "ream.hole.0.process.end",
    )
    moves = tuple(event for event in relevant if isinstance(event, LinearMove))
    assert tuple(event.motion_class for event in moves) == (
        MotionClass.CUTTING, MotionClass.RETRACT,
    )
    assert all(
        event.feed_rate.unit is FeedUnit.MM_PER_REVOLUTION for event in moves
    )
    assert moves[0].end.position.z == strategy.final_depth.value
    assert moves[1].end.position.z == strategy.retract_height.value
    assert sum(isinstance(event, DwellEvent) for event in relevant) == 1
    assert tuple(
        event.mode for event in artifact.events if isinstance(event, FeedModeEvent)
    ) == (FeedMode.UNITS_PER_REVOLUTION,)
    spindle = tuple(
        event.state for event in relevant if isinstance(event, SpindleStateEvent)
    )
    assert spindle == (SpindleState.CLOCKWISE, SpindleState.OFF)
    assert artifact.statistics.estimated_duration_seconds > 0
    assert not artifact.statistics.duration_is_partial
    payload = str(artifact.to_dict()).lower()
    assert all(token not in payload for token in ("g85", "g86", "m-code", "g-code"))


def test_multi_hole_order_safety_completion_and_artifact_are_deterministic() -> None:
    strategy = _strategy(pattern=_pattern((10, 0), (0, 5), (0, 0)))
    generator, inputs, _holder, _resolved_geometry = _inputs(strategy=strategy)
    first, *_ = _artifact(generator, inputs)
    second, *_ = _artifact(generator, inputs)

    assert tuple(hole.position.x for hole in inputs.holes) == (0.0, 0.0, 10.0)
    assert first.artifact_fingerprint == second.artifact_fingerprint
    assert tuple(event.event_id for event in first.events) == tuple(
        event.event_id for event in second.events
    )
    assert sum(
        isinstance(event, MarkerEvent)
        and event.semantic_key == "ream.hole_complete"
        for event in first.events
    ) == 3
    assert sum(
        isinstance(event, MarkerEvent)
        and event.semantic_key == "ream.process_begin"
        for event in first.events
    ) == 3
    assert sum(
        isinstance(event, MarkerEvent)
        and event.semantic_key == "ream.process_end"
        for event in first.events
    ) == 3
    assert all(
        event.start.position.z != strategy.final_depth.value
        for event in first.events if isinstance(event, RapidMove)
    )
    assert all(
        event.start.position.z == event.end.position.z
        and event.start.position.z >= strategy.clearance_height.value
        for event in first.events
        if isinstance(event, RapidMove)
        and (event.start.position.x, event.start.position.y)
        != (event.end.position.x, event.end.position.y)
    )


@pytest.mark.parametrize(
    ("resource_changes", "code"),
    (
        ({"family": ToolFamily.DRILL}, DiagnosticCode.REAM_UNSUPPORTED_TOOL),
        ({"family": ToolFamily.TAP}, DiagnosticCode.REAM_UNSUPPORTED_TOOL),
        ({"tool_diameter": 7.9}, DiagnosticCode.REAM_DIAMETER_MISMATCH),
        ({"cutting_length": 5.0}, DiagnosticCode.REAM_UNSUPPORTED_TOOL),
        ({"usable_length": 5.0}, DiagnosticCode.REAM_UNSUPPORTED_TOOL),
        ({"stickout": 5.0}, DiagnosticCode.REAM_UNSUPPORTED_TOOL),
    ),
)
def test_tool_family_diameter_and_length_validation(resource_changes, code) -> None:
    strategy = _strategy()
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, _holder, assembly, machine = _resources(strategy, **resource_changes)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly), (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    with pytest.raises(ReamingGenerationError) as failure:
        ReamingGenerator().resolve_inputs(
            operation, setup, assembly=assembly, tool=tool, machine=machine,
            resolved_geometry=_resolved(strategy),
        )
    assert failure.value.code is code


def test_missing_stale_tool_snapshot_and_unit_are_distinct() -> None:
    generator, inputs, _holder, resolved = _inputs()
    cases = (
        (None, inputs.tool, DiagnosticCode.REAM_TOOL_MISSING),
        (replace(inputs.assembly, revision=Revision(1)), inputs.tool,
         DiagnosticCode.REAM_TOOL_STALE),
        (inputs.assembly, None, DiagnosticCode.REAM_TOOL_MISSING),
        (inputs.assembly, replace(inputs.tool, revision=Revision(1)),
         DiagnosticCode.REAM_TOOL_STALE),
    )
    for assembly, tool, code in cases:
        with pytest.raises(ReamingGenerationError) as failure:
            generator.resolve_inputs(
                inputs.operation, inputs.setup, assembly=assembly, tool=tool,
                machine=inputs.machine, resolved_geometry=resolved,
            )
        assert failure.value.code is code

    wrong_unit = replace(
        inputs.operation,
        tool_assembly=replace(
            inputs.operation.tool_assembly, unit=LengthUnit.INCH
        ),
    )
    with pytest.raises(ReamingGenerationError) as failure:
        generator.resolve_inputs(
            wrong_unit, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
            machine=inputs.machine, resolved_geometry=resolved,
        )
    assert failure.value.code is DiagnosticCode.REAM_UNSUPPORTED_TOOL


@pytest.mark.parametrize(
    "resource_changes",
    (
        {"directions": (SpindleDirection.COUNTERCLOCKWISE,)},
        {"maximum_feed": 40.0},
    ),
)
def test_machine_direction_and_derived_feed_are_fail_closed(resource_changes) -> None:
    strategy = _strategy()
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, _holder, assembly, machine = _resources(strategy, **resource_changes)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly), (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    with pytest.raises(ReamingGenerationError) as failure:
        ReamingGenerator().resolve_inputs(
            operation, setup, assembly=assembly, tool=tool, machine=machine,
            resolved_geometry=_resolved(strategy),
        )
    assert failure.value.code is DiagnosticCode.REAM_MACHINE_INCOMPATIBLE


def test_machine_rpm_coolant_and_no_tapping_sync_requirement() -> None:
    generator, inputs, _holder, resolved = _inputs()
    assert not inputs.machine.spindles[0].synchronized_feed
    assert generator.resolve_inputs(
        inputs.operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
        machine=inputs.machine, resolved_geometry=resolved,
    )

    high_rpm = replace(inputs.strategy, spindle_speed=SpindleSpeed(100_000))
    high_operation = replace(
        inputs.operation, parameters=high_rpm.to_operation_parameters()
    )
    with pytest.raises(ReamingGenerationError) as rpm:
        generator.resolve_inputs(
            high_operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, machine=inputs.machine,
            resolved_geometry=_resolved(high_rpm),
        )
    assert rpm.value.code is DiagnosticCode.REAM_MACHINE_INCOMPATIBLE

    coolant = _strategy(coolant=ReamingCoolantMode.FLOOD)
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, _holder, assembly, machine = _resources(coolant)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly), (),
        coolant.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    with pytest.raises(ReamingGenerationError) as unsupported:
        generator.resolve_inputs(
            operation, setup, assembly=assembly, tool=tool, machine=machine,
            resolved_geometry=_resolved(coolant),
        )
    assert unsupported.value.code is DiagnosticCode.REAM_UNSUPPORTED_TOOL

    tool, _holder, assembly, machine = _resources(
        coolant, tool_coolant=(ToolCoolantCapability.FLOOD,)
    )
    operation = replace(
        operation,
        tool_assembly=ToolAssemblyReference.from_assembly(assembly),
        machine_requirement=MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    with pytest.raises(ReamingGenerationError) as machine_coolant:
        generator.resolve_inputs(
            operation, setup, assembly=assembly, tool=tool, machine=machine,
            resolved_geometry=_resolved(coolant),
        )
    assert machine_coolant.value.code is DiagnosticCode.REAM_MACHINE_INCOMPATIBLE

    tool, _holder, assembly, machine = _resources(
        coolant,
        tool_coolant=(ToolCoolantCapability.FLOOD,),
        machine_coolant=(MachineCoolantCapability.FLOOD,),
    )
    operation = replace(
        operation,
        tool_assembly=ToolAssemblyReference.from_assembly(assembly),
        machine_requirement=MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    coolant_inputs = generator.resolve_inputs(
        operation, setup, assembly=assembly, tool=tool, machine=machine,
        resolved_geometry=_resolved(coolant),
    )
    artifact, *_ = _artifact(generator, coolant_inputs)
    states = tuple(
        event.state for event in artifact.events
        if isinstance(event, CoolantStateEvent)
    )
    assert states == (CoolantState.OFF, CoolantState.FLOOD, CoolantState.OFF)


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (GeometryResolutionStatus.MISSING, DiagnosticCode.REAM_GEOMETRY_MISSING),
        (GeometryResolutionStatus.STALE, DiagnosticCode.REAM_GEOMETRY_STALE),
        (GeometryResolutionStatus.TOPOLOGY_CHANGED,
         DiagnosticCode.REAM_GEOMETRY_STALE),
        (GeometryResolutionStatus.AMBIGUOUS,
         DiagnosticCode.REAM_GEOMETRY_AMBIGUOUS),
        (GeometryResolutionStatus.SOURCE_MISMATCH,
         DiagnosticCode.REAM_SOURCE_MISMATCH),
    ),
)
def test_geometry_failures_are_mapped_without_rebind(status, code) -> None:
    generator, inputs, _holder, _resolved_geometry = _inputs()
    failed = ResolvedDrillingGeometry(
        status,
        diagnostics=(ValidationDiagnostic(
            DiagnosticSeverity.ERROR, DiagnosticCode.DRILL_GEOMETRY_STALE,
            "failed",
        ),),
    )
    with pytest.raises(ReamingGenerationError) as failure:
        generator.resolve_inputs(
            inputs.operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, machine=inputs.machine,
            resolved_geometry=failed,
        )
    assert failure.value.code is code


def test_circular_edge_validates_finished_diameter_not_declared_prehole() -> None:
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    source_id = setup.source_scope.primary_source_id
    reference = GeometryReference(
        GeometryReferenceId.new(), "hms_persistent_geometry", 1, source_id,
        GeometryReferenceKind.EDGE, GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"circle": 1}), Revision(0),
        occurrence_path="root/part:1", subshape_selector="circular_edge:1",
    )
    hole_reference = HoleReference(
        reference, Vector3(0, 0, 1), Point3(0, 0, 0, LengthUnit.MM),
        LengthUnit.MM,
    )
    location = HoleLocation(
        Point3(0, 0, 0, LengthUnit.MM), Vector3(0, 0, 1),
        Point3(0, 0, 0, LengthUnit.MM), Length(8, LengthUnit.MM),
        LengthUnit.MM, HoleSourceKind.CIRCULAR_EDGE, hole_reference,
    )
    pattern = HolePattern((location,), LengthUnit.MM)
    strategy = _strategy(pattern=pattern, pre_hole_diameter=Length(7.7, LengthUnit.MM))
    tool, _holder, assembly, machine = _resources(strategy)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly),
        (OperationGeometryInput(
            GeometryInputId.new(), GeometryInputRole.DRIVE_GEOMETRY,
            reference, True, GeometryReferenceKind.EDGE, 0,
        ),), strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    region = DrillingRegion(
        strategy.geometry, pattern, strategy.depth, strategy.unit,
        GeometryFingerprint.from_payload({"circle": 1}),
    )
    resolved = ResolvedDrillingGeometry(GeometryResolutionStatus.RESOLVED, region)
    assert ReamingGenerator().resolve_inputs(
        operation, setup, assembly=assembly, tool=tool, machine=machine,
        resolved_geometry=resolved,
    )

    mismatched = replace(location, diameter=Length(7.9, LengthUnit.MM))
    mismatched_pattern = HolePattern((mismatched,), LengthUnit.MM)
    mismatched_strategy = _strategy(pattern=mismatched_pattern)
    mismatched_operation = replace(
        operation, parameters=mismatched_strategy.to_operation_parameters()
    )
    mismatched_region = DrillingRegion(
        mismatched_strategy.geometry, mismatched_pattern,
        mismatched_strategy.depth, mismatched_strategy.unit,
        GeometryFingerprint.from_payload({"circle": 2}),
    )
    with pytest.raises(ReamingGenerationError) as failure:
        ReamingGenerator().resolve_inputs(
            mismatched_operation, setup, assembly=assembly, tool=tool,
            machine=machine,
            resolved_geometry=ResolvedDrillingGeometry(
                GeometryResolutionStatus.RESOLVED, mismatched_region
            ),
        )
    assert failure.value.code is DiagnosticCode.REAM_DIAMETER_MISMATCH


def test_vertex_references_preserve_repeated_occurrence_identity() -> None:
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    source_id = setup.source_scope.primary_source_id
    references = []
    locations = []
    for index, (occurrence, x) in enumerate((
        ("root/part:1", 1.0), ("root/part:2", 4.0),
    )):
        reference = GeometryReference(
            GeometryReferenceId.new(), "hms_persistent_geometry", 1, source_id,
            GeometryReferenceKind.VERTEX, GeometryRepresentationKind.BREP,
            GeometryFingerprint.from_payload({"vertex": index}), Revision(0),
            occurrence_path=occurrence,
            subshape_selector=f"vertex:{index + 1}",
        )
        hole_reference = HoleReference(
            reference, Vector3(0, 0, 1),
            Point3(x, 0, 0, LengthUnit.MM), LengthUnit.MM,
        )
        references.append(hole_reference)
        locations.append(HoleLocation(
            Point3(x, 0, 0, LengthUnit.MM), Vector3(0, 0, 1),
            Point3(x, 0, 0, LengthUnit.MM), None, LengthUnit.MM,
            HoleSourceKind.BREP_VERTEX, hole_reference,
        ))
    pattern = HolePattern(tuple(locations), LengthUnit.MM)
    strategy = _strategy(pattern=pattern)
    tool, _holder, assembly, machine = _resources(strategy)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly),
        tuple(OperationGeometryInput(
            GeometryInputId.new(), GeometryInputRole.DRIVE_GEOMETRY,
            reference.reference, True, GeometryReferenceKind.VERTEX, index,
        ) for index, reference in enumerate(references)),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    region = DrillingRegion(
        strategy.geometry, pattern, strategy.depth, strategy.unit,
        GeometryFingerprint.from_payload({
            "occurrences": [item.fingerprint.to_dict() for item in references]
        }),
    )
    inputs = ReamingGenerator().resolve_inputs(
        operation, setup, assembly=assembly, tool=tool, machine=machine,
        resolved_geometry=ResolvedDrillingGeometry(
            GeometryResolutionStatus.RESOLVED, region
        ),
    )
    assert len(inputs.holes) == 2
    assert references[0].fingerprint != references[1].fingerprint


def test_current_stale_disabled_and_deleted_publish_contract() -> None:
    generator, inputs, _holder, _resolved_geometry = _inputs()
    candidate, computing, token = _artifact(generator, inputs)
    accepted = publish_toolpath(
        computing.operation, candidate, token, inputs.input_fingerprint
    )
    assert accepted.accepted and accepted.artifact == candidate

    newer = computing.operation.artifact_state.mark_dirty(
        DirtyReason.PARAMETERS_CHANGED
    )
    newer, _ = newer.begin(inputs.input_fingerprint)
    current = replace(computing.operation, artifact_state=newer)
    assert not publish_toolpath(
        current, candidate, token, inputs.input_fingerprint
    ).accepted
    assert not publish_toolpath(
        replace(computing.operation, enabled=False), candidate, token,
        inputs.input_fingerprint,
    ).accepted
    assert not publish_toolpath(
        computing.operation, candidate, token, inputs.input_fingerprint,
        operation_exists=False,
    ).accepted


def test_geometry_wcs_parameters_prehole_tool_and_machine_change_fingerprint() -> None:
    generator, inputs, _holder, _resolved_geometry = _inputs()
    baseline = inputs.input_fingerprint
    for strategy in (
        replace(inputs.strategy, pre_hole_diameter=Length(7.7, LengthUnit.MM)),
        replace(inputs.strategy, feed_per_revolution=FeedRate(
            0.12, FeedUnit.MM_PER_REVOLUTION
        )),
        _strategy(pattern=_pattern((2, 0))),
    ):
        operation = replace(
            inputs.operation, parameters=strategy.to_operation_parameters()
        )
        changed = generator.resolve_inputs(
            operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
            machine=inputs.machine, resolved_geometry=_resolved(strategy),
        )
        assert changed.input_fingerprint != baseline

    moved_setup = replace(
        inputs.setup,
        wcs=replace(inputs.setup.wcs, origin=Point3(1, 0, 0, LengthUnit.MM)),
    )
    moved = generator.resolve_inputs(
        inputs.operation, moved_setup, assembly=inputs.assembly, tool=inputs.tool,
        machine=inputs.machine, resolved_geometry=_resolved(inputs.strategy),
    )
    assert moved.input_fingerprint != baseline

    changed_tool = replace(inputs.tool, manufacturer="changed")
    changed_assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Changed", changed_tool,
        inputs.assembly.stickout, inputs.assembly.gauge_length,
    )
    tool_operation = replace(
        inputs.operation,
        tool_assembly=ToolAssemblyReference.from_assembly(changed_assembly),
    )
    tool_inputs = generator.resolve_inputs(
        tool_operation, inputs.setup, assembly=changed_assembly,
        tool=changed_tool, machine=inputs.machine,
        resolved_geometry=_resolved(inputs.strategy),
    )
    assert tool_inputs.input_fingerprint != baseline

    changed_machine = replace(inputs.machine, model="changed")
    machine_operation = replace(
        inputs.operation,
        machine_requirement=MachineRequirement(
            changed_machine.machine_id, changed_machine.revision,
            changed_machine.content_fingerprint, changed_machine.unit,
            (OperationCapability.DRILLING,),
        ),
    )
    machine_inputs = generator.resolve_inputs(
        machine_operation, inputs.setup, assembly=inputs.assembly,
        tool=inputs.tool, machine=changed_machine,
        resolved_geometry=_resolved(inputs.strategy),
    )
    assert machine_inputs.input_fingerprint != baseline


def test_service_save_open_and_failed_recompute_keep_valid_artifact(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Reaming Core")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    generator, inputs, holder, resolved = _inputs()
    service.execute_cam_command(lambda app: app.add_setup(job_id, inputs.setup))
    service.execute_cam_command(lambda app: app.add_basic_resources(
        inputs.tool, holder, inputs.assembly, inputs.machine,
    ))
    service.execute_cam_command(lambda app: app.update_tree(
        job_id, inputs.setup.setup_id,
        lambda tree: tree.add_operation(
            tree.root_id, "Reaming", inputs.operation
        ),
    ))
    success = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert success.accepted and success.artifact is not None
    old_artifact = service.load_toolpath_artifact(inputs.operation.operation_id)

    stale = ResolvedDrillingGeometry(
        GeometryResolutionStatus.STALE,
        diagnostics=(ValidationDiagnostic(
            DiagnosticSeverity.ERROR, DiagnosticCode.DRILL_GEOMETRY_STALE,
            "stale",
        ),),
    )
    failure = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: stale,
    )
    assert not failure.accepted
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact

    service.save()
    service.close_project()
    service.open_project(session.root_path)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert ReamingStrategy.from_operation_parameters(restored.parameters) == inputs.strategy
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact
