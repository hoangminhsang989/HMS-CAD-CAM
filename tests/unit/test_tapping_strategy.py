"""Stage 7B.7.1 tapping validation, semantic path and publish tests."""

from dataclasses import replace
from uuid import uuid4

import pytest

from hms_cadcam.cam.application import (
    TappingGenerationError,
    TappingGenerator,
    basic_mill_resources,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    CamNodeId,
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
    SpindleDirection,
    SpindleSpeed,
    TapGeometry,
    TappingHand,
    TappingMode,
    TappingStrategy,
    TappingSynchronizationPolicy,
    ToolAssembly,
    ToolAssemblyId,
    ToolAssemblyReference,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    ToolHand,
    ValidationDiagnostic,
    Vector3,
)
from hms_cadcam.cam.toolpath import (
    DwellEvent,
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


def _pattern(
    *points: tuple[float, float],
    diameter: float | None = 8.0,
) -> HolePattern:
    unit = LengthUnit.MM
    return HolePattern(tuple(
        HoleLocation(
            Point3(x, y, 0, unit),
            Vector3(0, 0, 1),
            Point3(x, y, 0, unit),
            None if diameter is None else Length(diameter, unit),
            unit,
        )
        for x, y in points
    ), unit)


def _strategy(
    *,
    pattern: HolePattern | None = None,
    hand: TappingHand = TappingHand.RIGHT_HAND_TAP,
    policy: TappingSynchronizationPolicy = TappingSynchronizationPolicy.RIGID,
    **changes,
) -> TappingStrategy:
    unit = LengthUnit.MM
    values = dict(
        unit=unit,
        geometry=DrillGeometryInput(pattern or _pattern((0, 0)), unit),
        depth=DrillDepthDefinition(unit, Length(0, unit), Length(-10, unit)),
        nominal_diameter=Length(8, unit),
        pitch=Length(1.25, unit),
        hand=hand,
        spindle_speed=SpindleSpeed(500),
        clearance_height=Length(8, unit),
        retract_height=Length(3, unit),
        synchronization_policy=policy,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7, unit),
    )
    values.update(changes)
    return TappingStrategy(**values)


def _resources(
    strategy: TappingStrategy,
    *,
    tool_family: ToolFamily = ToolFamily.TAP,
    tool_diameter: float = 8.0,
    tool_pitch: float | None = None,
    tool_hand: ToolHand | None = None,
    threaded_length: float = 20.0,
    usable_length: float = 30.0,
    stickout: float = 25.0,
    synchronized_feed: bool = True,
    directions: tuple[SpindleDirection, ...] = (
        SpindleDirection.CLOCKWISE,
        SpindleDirection.COUNTERCLOCKWISE,
    ),
    tapping_modes: tuple[TappingMode, ...] = (
        TappingMode.RIGID,
        TappingMode.FLOATING,
    ),
    maximum_feed: float | None = None,
):
    _end_mill, holder, _assembly, machine = basic_mill_resources(LengthUnit.MM)
    selected_hand = tool_hand or (
        ToolHand.RIGHT
        if strategy.hand is TappingHand.RIGHT_HAND_TAP
        else ToolHand.LEFT
    )
    if tool_family is ToolFamily.TAP:
        geometry = TapGeometry(
            Length(tool_diameter, LengthUnit.MM),
            Length(min(threaded_length, usable_length), LengthUnit.MM),
            Length(
                strategy.pitch.value if tool_pitch is None else tool_pitch,
                LengthUnit.MM,
            ),
            selected_hand,
        )
    else:
        from hms_cadcam.cam.domain import Angle, AngleUnit
        geometry = DrillGeometry(
            Length(tool_diameter, LengthUnit.MM),
            Length(threaded_length, LengthUnit.MM),
            Angle(118, AngleUnit.DEGREE),
        )
    tool = ToolDefinition(
        ToolDefinitionId.new(),
        "Tap tool",
        tool_family,
        LengthUnit.MM,
        geometry,
        Length(80, LengthUnit.MM),
        Length(usable_length, LengthUnit.MM),
        ShankGeometry(Length(8, LengthUnit.MM), Length(50, LengthUnit.MM)),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Tap assembly",
        tool,
        Length(stickout, LengthUnit.MM),
        Length(60, LengthUnit.MM),
        holder,
    )
    spindle = replace(
        machine.spindles[0],
        directions=directions,
        synchronized_feed=synchronized_feed,
    )
    capabilities = replace(
        machine.capabilities,
        tapping=True,
        maximum_feed=(
            machine.capabilities.maximum_feed
            if maximum_feed is None
            else FeedRate(maximum_feed, FeedUnit.MM_PER_MINUTE)
        ),
        operations=(OperationCapability.MILLING, OperationCapability.TAPPING),
        tapping_modes=tapping_modes,
    )
    machine = replace(machine, spindles=(spindle,), capabilities=capabilities)
    return tool, holder, assembly, machine


def _inputs(
    *,
    strategy: TappingStrategy | None = None,
    resource_changes: dict | None = None,
):
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
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
            (OperationCapability.TAPPING,),
        ),
    )
    region = DrillingRegion(
        strategy.geometry,
        strategy.geometry.source,
        strategy.depth,
        strategy.unit,
        GeometryFingerprint.from_payload({"pattern": strategy.geometry.to_dict()}),
    )
    resolved = ResolvedDrillingGeometry(GeometryResolutionStatus.RESOLVED, region)
    generator = TappingGenerator()
    inputs = generator.resolve_inputs(
        operation,
        setup,
        assembly=assembly,
        tool=tool,
        machine=machine,
        resolved_geometry=resolved,
    )
    return generator, inputs, holder, resolved


def _artifact(generator: TappingGenerator, inputs):
    computing, token = generator.begin(inputs)
    return generator.generate(computing), computing, token


@pytest.mark.parametrize(
    ("hand", "cutting", "retract"),
    (
        (TappingHand.RIGHT_HAND_TAP, SpindleState.CLOCKWISE,
         SpindleState.COUNTERCLOCKWISE),
        (TappingHand.LEFT_HAND_TAP, SpindleState.COUNTERCLOCKWISE,
         SpindleState.CLOCKWISE),
    ),
)
def test_right_and_left_hand_emit_exact_synchronized_semantics(
    hand, cutting, retract
) -> None:
    strategy = _strategy(hand=hand)
    generator, inputs, _holder, _resolved = _inputs(strategy=strategy)
    artifact, _computing, _token = _artifact(generator, inputs)

    relevant = tuple(
        event for event in artifact.events
        if event.provenance.startswith("tap.hole.0")
    )
    assert tuple(event.provenance.rsplit(".", 1)[-1] for event in relevant) == (
        "rapid", "approach", "begin", "cutting", "descent", "reversal",
        "synchronized_retract", "complete", "end", "final_retract",
    )
    spindle = tuple(event for event in relevant if isinstance(event, SpindleStateEvent))
    assert tuple(event.state for event in spindle) == (cutting, retract)
    moves = tuple(event for event in relevant if isinstance(event, LinearMove))
    assert tuple(event.feed_rate.value for event in moves) == (1.25, 1.25)
    assert all(event.feed_rate.unit is FeedUnit.MM_PER_REVOLUTION for event in moves)
    assert moves[0].end.position.z == strategy.final_depth.value
    assert moves[1].motion_class is MotionClass.RETRACT
    assert artifact.statistics.estimated_duration_seconds > 0.0
    assert not artifact.statistics.duration_is_partial


def test_multi_hole_dwell_safety_completion_and_determinism() -> None:
    strategy = _strategy(
        pattern=_pattern((10, 0), (0, 5), (0, 0)),
        dwell_seconds=0.2,
    )
    generator, inputs, _holder, _resolved = _inputs(strategy=strategy)
    first, *_ = _artifact(generator, inputs)
    second, *_ = _artifact(generator, inputs)

    assert first.artifact_fingerprint == second.artifact_fingerprint
    assert tuple(event.event_id for event in first.events) == tuple(
        event.event_id for event in second.events
    )
    assert sum(isinstance(event, DwellEvent) for event in first.events) == 3
    assert sum(
        isinstance(event, MarkerEvent) and event.semantic_key == "tap.hole_complete"
        for event in first.events
    ) == 3
    assert sum(
        isinstance(event, MarkerEvent)
        and event.semantic_key == "tap.synchronization_begin"
        and dict(event.metadata)["format"] == "hms_tapping_sync_v1"
        for event in first.events
    ) == 3
    assert all(
        event.start.position.z == event.end.position.z
        and event.start.position.z >= strategy.clearance_height.value
        for event in first.events
        if isinstance(event, RapidMove)
        and (event.start.position.x, event.start.position.y)
        != (event.end.position.x, event.end.position.y)
    )
    payload = str(first.to_dict()).lower()
    assert all(token not in payload for token in ("g84", "g74", "m29"))


@pytest.mark.parametrize(
    ("changes", "code"),
    (
        ({"tool_family": ToolFamily.DRILL}, DiagnosticCode.TAP_UNSUPPORTED_TOOL),
        ({"tool_diameter": 7.9}, DiagnosticCode.TAP_DIAMETER_MISMATCH),
        ({"tool_pitch": 1.0}, DiagnosticCode.TAP_PITCH_MISMATCH),
        ({"tool_hand": ToolHand.LEFT}, DiagnosticCode.TAP_HAND_MISMATCH),
        ({"threaded_length": 5.0}, DiagnosticCode.TAP_UNSUPPORTED_TOOL),
        ({"usable_length": 5.0}, DiagnosticCode.TAP_UNSUPPORTED_TOOL),
        ({"stickout": 5.0}, DiagnosticCode.TAP_UNSUPPORTED_TOOL),
    ),
)
def test_tool_validation_is_fail_closed(changes, code) -> None:
    strategy = _strategy()
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, _holder, assembly, machine = _resources(strategy, **changes)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.TAPPING,),
        ),
    )
    region = DrillingRegion(
        strategy.geometry, strategy.geometry.source, strategy.depth, strategy.unit,
        GeometryFingerprint.from_payload({"pattern": 1}),
    )
    with pytest.raises(TappingGenerationError) as failure:
        TappingGenerator().resolve_inputs(
            operation, setup, assembly=assembly, tool=tool, machine=machine,
            resolved_geometry=ResolvedDrillingGeometry(
                GeometryResolutionStatus.RESOLVED, region
            ),
        )
    assert failure.value.code is code


def test_missing_and_stale_tool_are_distinct() -> None:
    generator, inputs, _holder, resolved = _inputs()
    cases = (
        (None, inputs.tool, DiagnosticCode.TAP_TOOL_MISSING),
        (replace(inputs.assembly, revision=Revision(1)), inputs.tool,
         DiagnosticCode.TAP_TOOL_STALE),
        (inputs.assembly, None, DiagnosticCode.TAP_TOOL_MISSING),
    )
    for assembly, tool, code in cases:
        with pytest.raises(TappingGenerationError) as failure:
            generator.resolve_inputs(
                inputs.operation, inputs.setup, assembly=assembly, tool=tool,
                machine=inputs.machine, resolved_geometry=resolved,
            )
        assert failure.value.code is code

    wrong_unit_operation = replace(
        inputs.operation,
        tool_assembly=replace(
            inputs.operation.tool_assembly, unit=LengthUnit.INCH
        ),
    )
    with pytest.raises(TappingGenerationError) as wrong_unit:
        generator.resolve_inputs(
            wrong_unit_operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, machine=inputs.machine, resolved_geometry=resolved,
        )
    assert wrong_unit.value.code is DiagnosticCode.TAP_UNSUPPORTED_TOOL


@pytest.mark.parametrize(
    ("policy", "resource_changes", "code"),
    (
        (TappingSynchronizationPolicy.RIGID, {}, None),
        (TappingSynchronizationPolicy.FLOATING, {}, None),
        (TappingSynchronizationPolicy.RIGID,
         {"synchronized_feed": False}, DiagnosticCode.TAP_SYNC_UNSUPPORTED),
        (TappingSynchronizationPolicy.RIGID,
         {"directions": (SpindleDirection.CLOCKWISE,)},
         DiagnosticCode.TAP_MACHINE_INCOMPATIBLE),
        (TappingSynchronizationPolicy.RIGID,
         {"tapping_modes": (TappingMode.FLOATING,)},
         DiagnosticCode.TAP_SYNC_UNSUPPORTED),
        (TappingSynchronizationPolicy.RIGID,
         {"synchronized_feed": False, "directions": (), "tapping_modes": ()},
         DiagnosticCode.TAP_SYNC_UNSUPPORTED),
        (TappingSynchronizationPolicy.RIGID,
         {"maximum_feed": 500.0}, DiagnosticCode.TAP_MACHINE_INCOMPATIBLE),
    ),
)
def test_machine_rigid_floating_sync_and_direction_policy(
    policy, resource_changes, code
) -> None:
    strategy = _strategy(policy=policy)
    if code is None:
        generator, inputs, *_ = _inputs(
            strategy=strategy, resource_changes=resource_changes
        )
        assert generator.resolve_inputs(
            inputs.operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, machine=inputs.machine,
            resolved_geometry=ResolvedDrillingGeometry(
                GeometryResolutionStatus.RESOLVED, inputs.region
            ),
        )
        return
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, _holder, assembly, machine = _resources(strategy, **resource_changes)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.TAPPING,),
        ),
    )
    region = DrillingRegion(
        strategy.geometry, strategy.geometry.source, strategy.depth, strategy.unit,
        GeometryFingerprint.from_payload({"pattern": 1}),
    )
    with pytest.raises(TappingGenerationError) as failure:
        TappingGenerator().resolve_inputs(
            operation, setup, assembly=assembly, tool=tool, machine=machine,
            resolved_geometry=ResolvedDrillingGeometry(
                GeometryResolutionStatus.RESOLVED, region
            ),
        )
    assert failure.value.code is code


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (GeometryResolutionStatus.MISSING, DiagnosticCode.TAP_GEOMETRY_MISSING),
        (GeometryResolutionStatus.STALE, DiagnosticCode.TAP_GEOMETRY_STALE),
        (GeometryResolutionStatus.AMBIGUOUS, DiagnosticCode.TAP_GEOMETRY_AMBIGUOUS),
        (GeometryResolutionStatus.SOURCE_MISMATCH, DiagnosticCode.TAP_SOURCE_MISMATCH),
    ),
)
def test_geometry_failure_diagnostics_are_mapped(status, code) -> None:
    generator, inputs, _holder, _resolved = _inputs()
    failed = ResolvedDrillingGeometry(
        status,
        diagnostics=(ValidationDiagnostic(
            DiagnosticSeverity.ERROR, DiagnosticCode.DRILL_GEOMETRY_STALE, "failed"
        ),),
    )
    with pytest.raises(TappingGenerationError) as failure:
        generator.resolve_inputs(
            inputs.operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, machine=inputs.machine, resolved_geometry=failed,
        )
    assert failure.value.code is code


def test_vertex_circle_and_repeated_occurrence_references_are_reused() -> None:
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    source_id = setup.source_scope.primary_source_id
    references = []
    locations = []
    for index, (kind, source_kind, occurrence, x) in enumerate((
        (GeometryReferenceKind.VERTEX, HoleSourceKind.BREP_VERTEX, "root/left", 1.0),
        (GeometryReferenceKind.EDGE, HoleSourceKind.CIRCULAR_EDGE, "root/right", 4.0),
    )):
        reference = GeometryReference(
            GeometryReferenceId.new(), "hms_persistent_geometry", 1, source_id,
            kind, GeometryRepresentationKind.BREP,
            GeometryFingerprint.from_payload({"shape": index}), Revision(0),
            occurrence_path=occurrence,
            subshape_selector=f"{kind.value}:{index + 1}",
        )
        hole_reference = HoleReference(
            reference, Vector3(0, 0, 1), Point3(x, 0, 0, LengthUnit.MM),
            LengthUnit.MM,
        )
        references.append(hole_reference)
        locations.append(HoleLocation(
            Point3(x, 0, 0, LengthUnit.MM), Vector3(0, 0, 1),
            Point3(x, 0, 0, LengthUnit.MM),
            Length(8, LengthUnit.MM) if kind is GeometryReferenceKind.EDGE else None,
            LengthUnit.MM, source_kind, hole_reference,
        ))
    pattern = HolePattern(tuple(locations), LengthUnit.MM)
    strategy = _strategy(pattern=pattern)
    tool, _holder, assembly, machine = _resources(strategy)
    operation_inputs = tuple(
        OperationGeometryInput(
            GeometryInputId.new(), GeometryInputRole.DRIVE_GEOMETRY,
            reference.reference, True, reference.reference.kind, index,
        )
        for index, reference in enumerate(references)
    )
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), operation_inputs,
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.TAPPING,),
        ),
    )
    region = DrillingRegion(
        strategy.geometry, pattern, strategy.depth, strategy.unit,
        GeometryFingerprint.from_payload({"occurrences": [
            value.fingerprint.to_dict() for value in references
        ]}),
    )
    inputs = TappingGenerator().resolve_inputs(
        operation, setup, assembly=assembly, tool=tool, machine=machine,
        resolved_geometry=ResolvedDrillingGeometry(
            GeometryResolutionStatus.RESOLVED, region
        ),
    )
    assert len(inputs.holes) == 2
    assert references[0].fingerprint != references[1].fingerprint


def test_current_stale_disabled_and_deleted_publish_contract() -> None:
    generator, inputs, _holder, _resolved = _inputs()
    candidate, computing, token = _artifact(generator, inputs)
    accepted = publish_toolpath(
        computing.operation, candidate, token, inputs.input_fingerprint
    )
    assert accepted.accepted and accepted.artifact == candidate

    newer_state = computing.operation.artifact_state.mark_dirty(
        DirtyReason.PARAMETERS_CHANGED
    )
    newer_state, _new_token = newer_state.begin(inputs.input_fingerprint)
    current = replace(computing.operation, artifact_state=newer_state)
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


def test_geometry_wcs_tool_machine_pitch_hand_rpm_changes_input_fingerprint() -> None:
    generator, inputs, holder, _resolved = _inputs()
    baseline = inputs.input_fingerprint

    for strategy in (
        replace(inputs.strategy, pitch=Length(1.5, LengthUnit.MM)),
        replace(inputs.strategy, hand=TappingHand.LEFT_HAND_TAP),
        replace(inputs.strategy, spindle_speed=SpindleSpeed(600)),
        replace(
            inputs.strategy,
            depth=DrillDepthDefinition(
                LengthUnit.MM, Length(0, LengthUnit.MM),
                Length(-9, LengthUnit.MM),
            ),
        ),
        _strategy(pattern=_pattern((2, 0))),
    ):
        tool, _new_holder, assembly, machine = _resources(strategy)
        operation = replace(
            inputs.operation,
            tool_assembly=ToolAssemblyReference.from_assembly(assembly),
            parameters=strategy.to_operation_parameters(),
            machine_requirement=MachineRequirement(
                machine.machine_id, machine.revision, machine.content_fingerprint,
                machine.unit, (OperationCapability.TAPPING,),
            ),
        )
        region = DrillingRegion(
            strategy.geometry, strategy.geometry.source, strategy.depth, strategy.unit,
            GeometryFingerprint.from_payload({"changed": strategy.to_dict()}),
        )
        changed = generator.resolve_inputs(
            operation, inputs.setup, assembly=assembly, tool=tool, machine=machine,
            resolved_geometry=ResolvedDrillingGeometry(
                GeometryResolutionStatus.RESOLVED, region
            ),
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
        inputs.operation, moved_setup, assembly=inputs.assembly, tool=inputs.tool,
        machine=inputs.machine,
        resolved_geometry=ResolvedDrillingGeometry(
            GeometryResolutionStatus.RESOLVED, inputs.region
        ),
    )
    assert moved.input_fingerprint != baseline

    changed_tool = replace(inputs.tool, revision=Revision(1))
    changed_assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Changed tap", changed_tool,
        inputs.assembly.stickout, inputs.assembly.gauge_length, holder,
    )
    tool_operation = replace(
        inputs.operation,
        tool_assembly=ToolAssemblyReference.from_assembly(changed_assembly),
    )
    tool_inputs = generator.resolve_inputs(
        tool_operation, inputs.setup, assembly=changed_assembly, tool=changed_tool,
        machine=inputs.machine,
        resolved_geometry=ResolvedDrillingGeometry(
            GeometryResolutionStatus.RESOLVED, inputs.region
        ),
    )
    assert tool_inputs.input_fingerprint != baseline

    changed_capabilities = replace(
        inputs.machine.capabilities,
        maximum_feed=FeedRate(4999, FeedUnit.MM_PER_MINUTE),
    )
    changed_machine = replace(inputs.machine, capabilities=changed_capabilities)
    machine_operation = replace(
        inputs.operation,
        machine_requirement=MachineRequirement(
            changed_machine.machine_id, changed_machine.revision,
            changed_machine.content_fingerprint, changed_machine.unit,
            (OperationCapability.TAPPING,),
        ),
    )
    machine_inputs = generator.resolve_inputs(
        machine_operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
        machine=changed_machine,
        resolved_geometry=ResolvedDrillingGeometry(
            GeometryResolutionStatus.RESOLVED, inputs.region
        ),
    )
    assert machine_inputs.input_fingerprint != baseline
    candidate, computing, token = _artifact(generator, inputs)
    stale = publish_toolpath(
        computing.operation,
        candidate,
        token,
        machine_inputs.input_fingerprint,
    )
    assert not stale.accepted and stale.artifact is None


def test_service_save_open_and_failed_recompute_keep_valid_artifact(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Tapping Core")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    strategy = _strategy()
    tool, holder, assembly, machine = _resources(strategy)
    service.execute_cam_command(
        lambda app: app.add_basic_resources(tool, holder, assembly, machine)
    )
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.DRILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (),
        strategy.to_operation_parameters(),
        MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.TAPPING,),
        ),
    )
    service.execute_cam_command(lambda app: app.update_tree(
        job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Tapping", operation),
    ))
    region = DrillingRegion(
        strategy.geometry, strategy.geometry.source, strategy.depth, strategy.unit,
        GeometryFingerprint.from_payload({"pattern": strategy.geometry.to_dict()}),
    )
    resolved = ResolvedDrillingGeometry(GeometryResolutionStatus.RESOLVED, region)
    success = service.compute_tapping(
        operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert success.accepted
    artifact = service.load_toolpath_artifact(operation.operation_id)
    assert artifact is not None

    failure = service.compute_tapping(
        operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: ResolvedDrillingGeometry(
            GeometryResolutionStatus.STALE,
            diagnostics=(ValidationDiagnostic(
                DiagnosticSeverity.ERROR, DiagnosticCode.DRILL_GEOMETRY_STALE,
                "stale",
            ),),
        ),
    )
    assert not failure.accepted
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(operation.operation_id) == artifact

    service.save()
    service.close_project()
    service.open_project(session.root_path)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert TappingStrategy.from_operation_parameters(restored.parameters) == strategy
    assert service.load_toolpath_artifact(operation.operation_id) == artifact
