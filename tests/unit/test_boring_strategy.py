"""Stage 7B.9.1 Boring validation, semantic path and publish tests."""

from dataclasses import replace
from uuid import uuid4

import pytest

from hms_cadcam.cam.application import (
    BoringGenerationError,
    BoringGenerator,
    basic_mill_resources,
)
from hms_cadcam.cam.domain import (
    Angle,
    AngleUnit,
    ArtifactStatus,
    BoringBarGeometry,
    BoringCoolantMode,
    BoringStrategy,
    BoxStock,
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
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
    HoleLocation,
    HolePattern,
    HoleReference,
    HoleSourceKind,
    Length,
    LengthUnit,
    MachineCoolantCapability,
    MachineKind,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationGeometryInput,
    OperationId,
    Point3,
    ResolvedDrillingGeometry,
    Revision,
    Setup,
    SetupId,
    SetupKind,
    ShankGeometry,
    SourceScope,
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
    WcsFrame,
    WorkOffset,
)
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.toolpath import (
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


def _setup(source_id=None, unit: LengthUnit = LengthUnit.MM) -> Setup:
    source_id = source_id or uuid4()
    wcs = WcsFrame.identity(unit)
    reference = GeometryReference(
        GeometryReferenceId.new(),
        HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
        source_id,
        GeometryReferenceKind.DOCUMENT,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"source_id": str(source_id)}),
        Revision(0),
    )
    return Setup(
        SetupId.new(),
        "Boring Setup",
        SetupKind.MILL,
        wcs,
        WorkOffset("G54", 1),
        BoxStock(
            Length(100, unit), Length(100, unit), Length(50, unit), wcs
        ),
        reference,
        SourceScope(source_id),
    )


def _pattern(*points: tuple[float, float]) -> HolePattern:
    unit = LengthUnit.MM
    return HolePattern(tuple(
        HoleLocation(
            Point3(x, y, 0, unit),
            Vector3(0, 0, 1),
            Point3(x, y, 0, unit),
            None,
            unit,
        )
        for x, y in points
    ), unit)


def _strategy(
    *,
    pattern: HolePattern | None = None,
    **changes,
) -> BoringStrategy:
    unit = LengthUnit.MM
    values = dict(
        unit=unit,
        geometry=DrillGeometryInput(pattern or _pattern((0, 0)), unit),
        depth=DrillDepthDefinition(unit, Length(0, unit), Length(-10, unit)),
        finished_bore_diameter=Length(20, unit),
        pre_bore_diameter=Length(18, unit),
        spindle_rpm=SpindleSpeed(600),
        feed_per_revolution=FeedRate(0.1, FeedUnit.MM_PER_REVOLUTION),
        clearance_height=Length(8, unit),
        retract_height=Length(3, unit),
        spindle_direction=SpindleDirection.CLOCKWISE,
        coolant=BoringCoolantMode.OFF,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7, unit),
    )
    values.update(changes)
    return BoringStrategy(**values)


def _tool_geometry(
    family: ToolFamily,
    *,
    minimum_bore: float,
    maximum_bore: float,
    cutting_length: float,
    hand: ToolHand,
):
    unit = LengthUnit.MM
    if family is ToolFamily.BORING_BAR:
        return BoringBarGeometry(
            Length(minimum_bore, unit),
            Length(maximum_bore, unit),
            Length(cutting_length, unit),
            hand,
        )
    if family is ToolFamily.DRILL:
        return DrillGeometry(
            Length(12, unit),
            Length(cutting_length, unit),
            Angle(118, AngleUnit.DEGREE),
        )
    if family is ToolFamily.TAP:
        return TapGeometry(
            Length(12, unit),
            Length(cutting_length, unit),
            Length(1.25, unit),
            hand,
        )
    if family is ToolFamily.REAMER:
        return CylindricalGeometry(
            Length(20, unit), Length(cutting_length, unit)
        )
    raise AssertionError("unsupported test tool family")


def _resources(
    strategy: BoringStrategy,
    *,
    family: ToolFamily = ToolFamily.BORING_BAR,
    minimum_bore: float = 15.0,
    maximum_bore: float = 25.0,
    cutting_length: float = 20.0,
    usable_length: float = 30.0,
    stickout: float = 25.0,
    shank_diameter: float = 12.0,
    hand: ToolHand = ToolHand.RIGHT,
    directions: tuple[SpindleDirection, ...] = (SpindleDirection.CLOCKWISE,),
    maximum_feed: float = 5000.0,
    tool_coolant: tuple[ToolCoolantCapability, ...] = (),
    machine_coolant: tuple[MachineCoolantCapability, ...] = (),
):
    _end_mill, holder, _assembly, machine = basic_mill_resources(LengthUnit.MM)
    geometry = _tool_geometry(
        family,
        minimum_bore=minimum_bore,
        maximum_bore=maximum_bore,
        cutting_length=min(cutting_length, usable_length),
        hand=hand,
    )
    tool = ToolDefinition(
        ToolDefinitionId.new(),
        "Boring bar",
        family,
        LengthUnit.MM,
        geometry,
        Length(80, LengthUnit.MM),
        Length(usable_length, LengthUnit.MM),
        ShankGeometry(
            Length(shank_diameter, LengthUnit.MM),
            Length(50, LengthUnit.MM),
        ),
        coolant_capabilities=tool_coolant,
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Boring assembly",
        tool,
        Length(stickout, LengthUnit.MM),
        Length(60, LengthUnit.MM),
        holder,
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


def _resolved(strategy: BoringStrategy) -> ResolvedDrillingGeometry:
    return ResolvedDrillingGeometry(
        GeometryResolutionStatus.RESOLVED,
        DrillingRegion(
            strategy.geometry,
            strategy.geometry.source,
            strategy.depth,
            strategy.unit,
            GeometryFingerprint.from_payload({
                "geometry": strategy.geometry.to_dict()
            }),
        ),
    )


def _inputs(
    *,
    strategy: BoringStrategy | None = None,
    resource_changes: dict | None = None,
):
    setup = _setup()
    strategy = strategy or _strategy()
    tool, holder, assembly, machine = _resources(
        strategy, **(resource_changes or {})
    )
    operation = Operation(
        OperationId.new(),
        CamNodeId.new(),
        OperationFamily.DRILLING,
        setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly),
        (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id,
            machine.revision,
            machine.content_fingerprint,
            machine.unit,
            (OperationCapability.DRILLING,),
        ),
    )
    resolved = _resolved(strategy)
    generator = BoringGenerator()
    inputs = generator.resolve_inputs(
        operation,
        setup,
        assembly=assembly,
        tool=tool,
        holder=holder,
        machine=machine,
        resolved_geometry=resolved,
    )
    return generator, inputs, resolved


def _artifact(generator: BoringGenerator, inputs):
    computing, token = generator.begin(inputs)
    return generator.generate(computing), computing, token


def test_boring_semantic_sequence_controlled_retract_and_no_gcode() -> None:
    strategy = _strategy(dwell_seconds=0.2)
    generator, inputs, _resolved_geometry = _inputs(strategy=strategy)
    artifact, _computing, _token = _artifact(generator, inputs)

    relevant = tuple(
        event for event in artifact.events
        if event.provenance.startswith("bore.hole.0")
    )
    assert tuple(event.provenance for event in relevant) == (
        "bore.hole.0.rapid",
        "bore.hole.0.approach",
        "bore.hole.0.process.begin",
        "bore.hole.0.spindle.begin",
        "bore.hole.0.descent",
        "bore.hole.0.dwell",
        "bore.hole.0.controlled_retract",
        "bore.hole.0.complete",
        "bore.hole.0.final_retract",
        "bore.hole.0.spindle.end",
        "bore.hole.0.process.end",
    )
    moves = tuple(event for event in relevant if isinstance(event, LinearMove))
    assert tuple(event.motion_class for event in moves) == (
        MotionClass.CUTTING,
        MotionClass.RETRACT,
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
    assert all(
        token not in payload
        for token in ("g85", "g86", "g87", "g89", "m-code", "g-code")
    )


def test_boring_multi_hole_order_safety_and_artifact_are_deterministic() -> None:
    strategy = _strategy(pattern=_pattern((10, 0), (0, 5), (0, 0)))
    generator, inputs, _resolved_geometry = _inputs(strategy=strategy)
    first, *_ = _artifact(generator, inputs)
    second, *_ = _artifact(generator, inputs)

    assert tuple(hole.position.x for hole in inputs.holes) == (0.0, 0.0, 10.0)
    assert first.artifact_fingerprint == second.artifact_fingerprint
    assert tuple(event.event_id for event in first.events) == tuple(
        event.event_id for event in second.events
    )
    for key in (
        "bore.process_begin", "bore.hole_complete", "bore.process_end"
    ):
        assert sum(
            isinstance(event, MarkerEvent) and event.semantic_key == key
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
        ({"family": ToolFamily.DRILL}, DiagnosticCode.BORE_UNSUPPORTED_TOOL),
        ({"family": ToolFamily.TAP}, DiagnosticCode.BORE_UNSUPPORTED_TOOL),
        ({"family": ToolFamily.REAMER}, DiagnosticCode.BORE_UNSUPPORTED_TOOL),
        ({"minimum_bore": 18.0}, DiagnosticCode.BORE_TOOL_ACCESS_INVALID),
        ({"maximum_bore": 19.0}, DiagnosticCode.BORE_TOOL_ACCESS_INVALID),
        ({"cutting_length": 5.0}, DiagnosticCode.BORE_UNSUPPORTED_TOOL),
        ({"usable_length": 5.0}, DiagnosticCode.BORE_UNSUPPORTED_TOOL),
        ({"stickout": 10.0}, DiagnosticCode.BORE_CLEARANCE_INVALID),
        ({"shank_diameter": 18.0}, DiagnosticCode.BORE_CLEARANCE_INVALID),
        ({"hand": ToolHand.LEFT}, DiagnosticCode.BORE_UNSUPPORTED_TOOL),
    ),
)
def test_boring_tool_access_length_hand_and_clearance_validation(
    resource_changes, code
) -> None:
    strategy = _strategy()
    setup = _setup()
    tool, holder, assembly, machine = _resources(strategy, **resource_changes)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly), (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    with pytest.raises(BoringGenerationError) as failure:
        BoringGenerator().resolve_inputs(
            operation, setup, assembly=assembly, tool=tool, holder=holder,
            machine=machine, resolved_geometry=_resolved(strategy),
        )
    assert failure.value.code is code


def test_boring_missing_and_stale_assembly_tool_holder_fail_closed() -> None:
    generator, inputs, resolved = _inputs()
    cases = (
        (None, inputs.tool, inputs.holder, DiagnosticCode.BORE_TOOL_MISSING),
        (
            replace(inputs.assembly, revision=Revision(1)),
            inputs.tool,
            inputs.holder,
            DiagnosticCode.BORE_TOOL_STALE,
        ),
        (inputs.assembly, None, inputs.holder, DiagnosticCode.BORE_TOOL_MISSING),
        (inputs.assembly, inputs.tool, None, DiagnosticCode.BORE_TOOL_MISSING),
        (
            inputs.assembly,
            replace(inputs.tool, revision=Revision(1)),
            inputs.holder,
            DiagnosticCode.BORE_TOOL_STALE,
        ),
        (
            inputs.assembly,
            inputs.tool,
            replace(inputs.holder, revision=Revision(1)),
            DiagnosticCode.BORE_TOOL_STALE,
        ),
    )
    for assembly, tool, holder, code in cases:
        with pytest.raises(BoringGenerationError) as failure:
            generator.resolve_inputs(
                inputs.operation, inputs.setup, assembly=assembly, tool=tool,
                holder=holder, machine=inputs.machine,
                resolved_geometry=resolved,
            )
        assert failure.value.code is code

    wrong_unit = replace(
        inputs.operation,
        tool_assembly=replace(
            inputs.operation.tool_assembly, unit=LengthUnit.INCH
        ),
    )
    with pytest.raises(BoringGenerationError) as unit_failure:
        generator.resolve_inputs(
            wrong_unit, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, holder=inputs.holder, machine=inputs.machine,
            resolved_geometry=resolved,
        )
    assert unit_failure.value.code is DiagnosticCode.BORE_UNSUPPORTED_TOOL


@pytest.mark.parametrize(
    "machine_change",
    (
        "capability",
        "direction",
        "rpm",
        "feed",
    ),
)
def test_boring_machine_validation_is_fail_closed(machine_change) -> None:
    strategy = _strategy()
    generator, inputs, resolved = _inputs(strategy=strategy)
    machine = inputs.machine
    if machine_change == "capability":
        machine = replace(
            machine,
            capabilities=replace(
                machine.capabilities,
                operations=(OperationCapability.MILLING,),
            ),
        )
    elif machine_change == "direction":
        machine = replace(
            machine,
            spindles=(replace(
                machine.spindles[0],
                directions=(SpindleDirection.COUNTERCLOCKWISE,),
            ),),
        )
    elif machine_change == "rpm":
        strategy = replace(strategy, spindle_rpm=SpindleSpeed(100_000))
    else:
        machine = replace(
            machine,
            capabilities=replace(
                machine.capabilities,
                maximum_feed=FeedRate(50, FeedUnit.MM_PER_MINUTE),
            ),
        )
    operation = replace(
        inputs.operation,
        parameters=strategy.to_operation_parameters(),
        machine_requirement=MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    with pytest.raises(BoringGenerationError) as failure:
        generator.resolve_inputs(
            operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
            holder=inputs.holder, machine=machine,
            resolved_geometry=_resolved(strategy),
        )
    assert failure.value.code is DiagnosticCode.BORE_MACHINE_INCOMPATIBLE


def test_boring_coolant_and_no_tapping_synchronization_requirement() -> None:
    generator, inputs, resolved = _inputs()
    assert not inputs.machine.spindles[0].synchronized_feed
    assert generator.resolve_inputs(
        inputs.operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
        holder=inputs.holder, machine=inputs.machine,
        resolved_geometry=resolved,
    )

    coolant = _strategy(coolant=BoringCoolantMode.THROUGH_TOOL)
    setup = _setup()
    tool, holder, assembly, machine = _resources(coolant)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly), (),
        coolant.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    with pytest.raises(BoringGenerationError) as tool_failure:
        generator.resolve_inputs(
            operation, setup, assembly=assembly, tool=tool, holder=holder,
            machine=machine, resolved_geometry=_resolved(coolant),
        )
    assert tool_failure.value.code is DiagnosticCode.BORE_UNSUPPORTED_TOOL

    tool, holder, assembly, machine = _resources(
        coolant,
        tool_coolant=(ToolCoolantCapability.THROUGH_TOOL,),
    )
    operation = replace(
        operation,
        tool_assembly=ToolAssemblyReference.from_assembly(assembly),
        machine_requirement=MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    with pytest.raises(BoringGenerationError) as machine_failure:
        generator.resolve_inputs(
            operation, setup, assembly=assembly, tool=tool, holder=holder,
            machine=machine, resolved_geometry=_resolved(coolant),
        )
    assert machine_failure.value.code is DiagnosticCode.BORE_MACHINE_INCOMPATIBLE


def test_boring_accepts_mill_turn_and_rejects_missing_machine() -> None:
    generator, inputs, resolved = _inputs()
    mill_turn = replace(
        inputs.machine,
        kind=MachineKind.MILL_TURN,
        capabilities=replace(inputs.machine.capabilities, turning=True),
    )
    operation = replace(
        inputs.operation,
        machine_requirement=MachineRequirement(
            mill_turn.machine_id, mill_turn.revision,
            mill_turn.content_fingerprint, mill_turn.unit,
            (OperationCapability.DRILLING,),
        ),
    )
    assert generator.resolve_inputs(
        operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
        holder=inputs.holder, machine=mill_turn, resolved_geometry=resolved,
    )
    with pytest.raises(BoringGenerationError) as missing:
        generator.resolve_inputs(
            inputs.operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, holder=inputs.holder, machine=None,
            resolved_geometry=resolved,
        )
    assert missing.value.code is DiagnosticCode.BORE_MACHINE_INCOMPATIBLE


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (GeometryResolutionStatus.MISSING, DiagnosticCode.BORE_GEOMETRY_MISSING),
        (GeometryResolutionStatus.STALE, DiagnosticCode.BORE_GEOMETRY_STALE),
        (
            GeometryResolutionStatus.TOPOLOGY_CHANGED,
            DiagnosticCode.BORE_GEOMETRY_STALE,
        ),
        (
            GeometryResolutionStatus.AMBIGUOUS,
            DiagnosticCode.BORE_GEOMETRY_AMBIGUOUS,
        ),
        (
            GeometryResolutionStatus.SOURCE_MISMATCH,
            DiagnosticCode.BORE_SOURCE_MISMATCH,
        ),
    ),
)
def test_boring_geometry_failures_are_mapped_without_rebind(status, code) -> None:
    generator, inputs, _resolved_geometry = _inputs()
    failed = ResolvedDrillingGeometry(
        status,
        diagnostics=(ValidationDiagnostic(
            DiagnosticSeverity.ERROR,
            DiagnosticCode.DRILL_GEOMETRY_STALE,
            "failed",
        ),),
    )
    with pytest.raises(BoringGenerationError) as failure:
        generator.resolve_inputs(
            inputs.operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, holder=inputs.holder, machine=inputs.machine,
            resolved_geometry=failed,
        )
    assert failure.value.code is code


def test_circular_edge_confirms_finished_diameter_not_prebore() -> None:
    setup = _setup()
    reference = GeometryReference(
        GeometryReferenceId.new(),
        HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
        setup.source_scope.primary_source_id,
        GeometryReferenceKind.EDGE,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"circle": 1}),
        Revision(0),
        occurrence_path="root/part:1",
        subshape_selector="circular_edge:1",
    )
    hole_reference = HoleReference(
        reference,
        Vector3(0, 0, 1),
        Point3(0, 0, 0, LengthUnit.MM),
        LengthUnit.MM,
    )
    location = HoleLocation(
        Point3(0, 0, 0, LengthUnit.MM),
        Vector3(0, 0, 1),
        Point3(0, 0, 0, LengthUnit.MM),
        Length(20, LengthUnit.MM),
        LengthUnit.MM,
        HoleSourceKind.CIRCULAR_EDGE,
        hole_reference,
    )
    pattern = HolePattern((location,), LengthUnit.MM)
    strategy = _strategy(
        pattern=pattern,
        pre_bore_diameter=Length(17.5, LengthUnit.MM),
    )
    tool, holder, assembly, machine = _resources(strategy)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly),
        (OperationGeometryInput(
            GeometryInputId.new(), GeometryInputRole.DRIVE_GEOMETRY,
            reference, True, GeometryReferenceKind.EDGE, 0,
        ),),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        ),
    )
    resolved = _resolved(strategy)
    assert BoringGenerator().resolve_inputs(
        operation, setup, assembly=assembly, tool=tool, holder=holder,
        machine=machine, resolved_geometry=resolved,
    )

    mismatch_location = replace(location, diameter=Length(19.9, LengthUnit.MM))
    mismatch_pattern = HolePattern((mismatch_location,), LengthUnit.MM)
    mismatch_strategy = _strategy(pattern=mismatch_pattern)
    mismatch_operation = replace(
        operation, parameters=mismatch_strategy.to_operation_parameters()
    )
    with pytest.raises(BoringGenerationError) as failure:
        BoringGenerator().resolve_inputs(
            mismatch_operation, setup, assembly=assembly, tool=tool,
            holder=holder, machine=machine,
            resolved_geometry=_resolved(mismatch_strategy),
        )
    assert failure.value.code is DiagnosticCode.BORE_DIAMETER_MISMATCH


def test_boring_current_stale_disabled_and_deleted_publish_contract() -> None:
    generator, inputs, _resolved_geometry = _inputs()
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


def test_boring_input_fingerprint_covers_all_true_dependencies() -> None:
    generator, inputs, _resolved_geometry = _inputs()
    baseline = inputs.input_fingerprint
    for strategy in (
        replace(
            inputs.strategy,
            pre_bore_diameter=Length(17.5, LengthUnit.MM),
        ),
        replace(
            inputs.strategy,
            feed_per_revolution=FeedRate(
                0.12, FeedUnit.MM_PER_REVOLUTION
            ),
        ),
        _strategy(pattern=_pattern((2, 0))),
    ):
        changed = generator.resolve_inputs(
            replace(
                inputs.operation,
                parameters=strategy.to_operation_parameters(),
            ),
            inputs.setup,
            assembly=inputs.assembly,
            tool=inputs.tool,
            holder=inputs.holder,
            machine=inputs.machine,
            resolved_geometry=_resolved(strategy),
        )
        assert changed.input_fingerprint != baseline

    moved_setup = replace(
        inputs.setup,
        wcs=replace(
            inputs.setup.wcs,
            origin=Point3(1, 0, 0, LengthUnit.MM),
        ),
    )
    moved = generator.resolve_inputs(
        inputs.operation, moved_setup, assembly=inputs.assembly,
        tool=inputs.tool, holder=inputs.holder, machine=inputs.machine,
        resolved_geometry=_resolved(inputs.strategy),
    )
    assert moved.input_fingerprint != baseline

    changed_tool = replace(inputs.tool, manufacturer="changed")
    tool_assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Changed tool assembly", changed_tool,
        inputs.assembly.stickout, inputs.assembly.gauge_length, inputs.holder,
    )
    tool_inputs = generator.resolve_inputs(
        replace(
            inputs.operation,
            tool_assembly=ToolAssemblyReference.from_assembly(tool_assembly),
        ),
        inputs.setup,
        assembly=tool_assembly,
        tool=changed_tool,
        holder=inputs.holder,
        machine=inputs.machine,
        resolved_geometry=_resolved(inputs.strategy),
    )
    assert tool_inputs.input_fingerprint != baseline

    changed_holder = replace(inputs.holder, manufacturer="changed")
    changed_assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Changed holder assembly", inputs.tool,
        inputs.assembly.stickout, inputs.assembly.gauge_length, changed_holder,
    )
    holder_inputs = generator.resolve_inputs(
        replace(
            inputs.operation,
            tool_assembly=ToolAssemblyReference.from_assembly(changed_assembly),
        ),
        inputs.setup,
        assembly=changed_assembly,
        tool=inputs.tool,
        holder=changed_holder,
        machine=inputs.machine,
        resolved_geometry=_resolved(inputs.strategy),
    )
    assert holder_inputs.input_fingerprint != baseline

    changed_machine = replace(inputs.machine, model="changed")
    machine_inputs = generator.resolve_inputs(
        replace(
            inputs.operation,
            machine_requirement=MachineRequirement(
                changed_machine.machine_id, changed_machine.revision,
                changed_machine.content_fingerprint, changed_machine.unit,
                (OperationCapability.DRILLING,),
            ),
        ),
        inputs.setup,
        assembly=inputs.assembly,
        tool=inputs.tool,
        holder=inputs.holder,
        machine=changed_machine,
        resolved_geometry=_resolved(inputs.strategy),
    )
    assert machine_inputs.input_fingerprint != baseline


def test_service_save_open_failure_and_project_generation_contract(
    tmp_path, monkeypatch
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Boring Core")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    _generator, inputs, resolved = _inputs()
    service.execute_cam_command(lambda app: app.add_setup(job_id, inputs.setup))
    service.execute_cam_command(lambda app: app.add_basic_resources(
        inputs.tool, inputs.holder, inputs.assembly, inputs.machine,
    ))
    service.execute_cam_command(lambda app: app.update_tree(
        job_id, inputs.setup.setup_id,
        lambda tree: tree.add_operation(
            tree.root_id, "Boring", inputs.operation
        ),
    ))
    success = service.compute_boring(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert success.accepted and success.artifact is not None
    old_artifact = service.load_toolpath_artifact(inputs.operation.operation_id)

    def fail_generate(_self, _inputs):
        raise BoringGenerationError(
            DiagnosticCode.BORE_GENERATION_FAILED, "generation failed"
        )

    with monkeypatch.context() as context:
        context.setattr(BoringGenerator, "generate", fail_generate)
        generation_failure = service.compute_boring(
            inputs.operation.operation_id,
            geometry_resolver=lambda _geometry, _depth: resolved,
        )
    assert not generation_failure.accepted
    assert generation_failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact

    def fail_publish(*_args, **_kwargs):
        raise ToolpathArtifactStoreError("store failed")

    with monkeypatch.context() as context:
        context.setattr(
            service._cam_application._artifact_store,
            "publish",
            fail_publish,
        )
        failure = service.compute_boring(
            inputs.operation.operation_id,
            geometry_resolver=lambda _geometry, _depth: resolved,
        )
    assert not failure.accepted
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact

    service.save()
    old_generation = service.cam_generation
    service.close_project()
    service.open_project(session.root_path)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert BoringStrategy.from_operation_parameters(restored.parameters) == inputs.strategy
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact
    with pytest.raises(RuntimeError):
        service.compute_boring(
            inputs.operation.operation_id,
            expected_generation=old_generation,
            geometry_resolver=lambda _geometry, _depth: resolved,
        )
