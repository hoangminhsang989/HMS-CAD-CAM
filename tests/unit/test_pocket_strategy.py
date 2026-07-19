"""Stage 7B.5.2 Pocket strategy, offset clearing, generation and publish tests."""

from dataclasses import replace
import math
from uuid import uuid4

import pytest

from hms_cadcam.cam.application import (
    PocketGenerationError,
    PocketGenerator,
    basic_mill_resources,
    build_pocket_offset_loops,
    pocket_depth_levels,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    BoxStock,
    CamNodeId,
    ContourBounds,
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourProfileSource,
    ContourSegment,
    DiagnosticCode,
    DiagnosticSeverity,
    DirtyReason,
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
    Length,
    LengthUnit,
    MachineRequirement,
    OccurrenceTransformProvenance,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationGeometryInput,
    OperationId,
    PocketBoundary,
    PocketCuttingDirection,
    PocketDepthDefinition,
    PocketEntryPolicy,
    PocketGeometryInput,
    PocketRegion,
    PocketStrategy,
    PocketValidationError,
    Point3,
    ProfileProvenance,
    ResolvedPocketGeometry,
    Revision,
    SpindleSpeed,
    ToolAssemblyReference,
    ValidationDiagnostic,
    Vector3,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.cam.toolpath import ArcMove, LinearMove, MotionClass, RapidMove, publish_toolpath
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import _default_setup
from hms_cadcam.viewer.toolpath import ToolpathPresentationRegistry

IDENTITY = (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0)


def _rectangle(width: float = 40.0, height: float = 30.0) -> ContourLoop:
    unit = LengthUnit.MM
    points = tuple(Point3(x, y, 0, unit) for x, y in (
        (0, 0), (width, 0), (width, height), (0, height),
    ))
    return ContourLoop(tuple(ContourSegment(ContourCurveKind.LINE, points[index],
        points[(index + 1) % len(points)]) for index in range(len(points))),
        ContourOrientation.COUNTERCLOCKWISE)


def _arc_boundary() -> ContourLoop:
    unit = LengthUnit.MM
    return ContourLoop((
        ContourSegment(ContourCurveKind.LINE, Point3(-15, 0, 0, unit), Point3(15, 0, 0, unit)),
        ContourSegment(ContourCurveKind.ARC, Point3(15, 0, 0, unit), Point3(-15, 0, 0, unit),
                       Point3(0, 0, 0, unit), math.pi),
    ), ContourOrientation.COUNTERCLOCKWISE)


def _reference(source_id) -> GeometryReference:
    selector = f"hms_profile_v1:{'a' * 64}:face:{'b' * 64}"
    fingerprint = GeometryFingerprint.from_payload({"selector": selector})
    return GeometryReference(
        GeometryReferenceId.new(), HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, source_id, GeometryReferenceKind.FACE,
        GeometryRepresentationKind.BREP, fingerprint, Revision(0),
        subshape_selector=selector,
    )


def _region(loop: ContourLoop, reference: GeometryReference) -> PocketRegion:
    unit = LengthUnit.MM
    points = tuple(point for segment in loop.segments for point in (segment.start, segment.end))
    bounds = ContourBounds(
        Point3(min(point.x for point in points), min(point.y for point in points),
               min(point.z for point in points), unit),
        Point3(max(point.x for point in points), max(point.y for point in points),
               max(point.z for point in points), unit),
    )
    geometry = GeometryFingerprint.from_payload({"loop": loop.to_dict()})
    provenance = ProfileProvenance(
        ContourProfileSource.PLANAR_FACE_OUTER,
        OccurrenceTransformProvenance(reference.occurrence_path, IDENTITY),
    )
    return PocketRegion(reference, PocketBoundary(loop, unit), loop.segments[0].start,
                        Vector3(1, 0, 0), Vector3(0, 1, 0), Vector3(0, 0, 1),
                        bounds, unit, geometry, provenance)


def _strategy(reference: GeometryReference, **changes) -> PocketStrategy:
    unit = LengthUnit.MM
    values = dict(
        unit=unit,
        geometry=PocketGeometryInput(reference, unit),
        depth=PocketDepthDefinition(unit, Length(0, unit), Length(-3, unit), Length(0, unit)),
        stepover=Length(4, unit),
        stepdown=Length(1, unit),
        radial_stock_allowance=Length(0, unit),
        clearance_height=Length(5, unit),
        retract_height=Length(2, unit),
        cutting_feed_rate=FeedRate(500, FeedUnit.MM_PER_MINUTE),
        plunge_feed_rate=FeedRate(100, FeedUnit.MM_PER_MINUTE),
        spindle_speed=SpindleSpeed(1000),
        entry_policy=PocketEntryPolicy.VERTICAL_PLUNGE,
        cutting_direction=PocketCuttingDirection.CLIMB,
        tolerance=Length(1.0e-7, unit),
    )
    values.update(changes)
    return PocketStrategy(**values)


def _inputs(loop: ContourLoop | None = None, strategy_changes=None):
    loop = loop or _rectangle()
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    reference = _reference(setup.source_scope.primary_source_id)
    strategy = _strategy(reference, **(strategy_changes or {}))
    geometry_input = OperationGeometryInput(
        GeometryInputId.new(), GeometryInputRole.BOUNDARY, reference,
        True, GeometryReferenceKind.FACE, 0,
    )
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (geometry_input,),
        strategy.to_operation_parameters(),
        MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint,
                           machine.unit, (OperationCapability.MILLING,)),
    )
    resolved = ResolvedPocketGeometry(GeometryResolutionStatus.RESOLVED,
                                      _region(loop, reference))
    generator = PocketGenerator()
    inputs = generator.resolve_inputs(operation, setup, assembly=assembly, tool=tool,
                                      machine=machine, resolved_geometry=resolved)
    return generator, inputs, holder, resolved


def test_strategy_versioned_round_trip_and_invalid_update_is_atomic() -> None:
    reference = _reference(uuid4())
    strategy = _strategy(reference)
    assert PocketStrategy.from_dict(strategy.to_dict()) == strategy
    assert PocketStrategy.from_operation_parameters(
        strategy.to_operation_parameters(), reference) == strategy
    before = strategy
    with pytest.raises(PocketValidationError) as stepover:
        replace(strategy, stepover=Length(0, LengthUnit.MM))
    assert stepover.value.code is DiagnosticCode.POCKET_INVALID_STEPOVER
    assert strategy == before
    with pytest.raises(PocketValidationError) as stepdown:
        replace(strategy, stepdown=Length(0, LengthUnit.MM))
    assert stepdown.value.code is DiagnosticCode.POCKET_INVALID_STEPDOWN


def test_rectangle_offsets_repeat_inward_deterministically() -> None:
    loops = build_pocket_offset_loops(_rectangle(), 5, 4, 1.0e-7)
    replay = build_pocket_offset_loops(_rectangle(), 5, 4, 1.0e-7)
    assert len(loops) == 3
    assert loops == replay
    bounds = [(
        min(segment.start.x for segment in loop.segments),
        max(segment.start.x for segment in loop.segments),
        min(segment.start.y for segment in loop.segments),
        max(segment.start.y for segment in loop.segments),
    ) for loop in loops]
    assert bounds == pytest.approx([(5, 35, 5, 25), (9, 31, 9, 21), (13, 27, 13, 17)])


def test_offset_collapse_and_invalid_geometry_fail_closed() -> None:
    with pytest.raises(PocketGenerationError) as collapse:
        build_pocket_offset_loops(_rectangle(8, 8), 5, 2, 1.0e-7)
    assert collapse.value.code is DiagnosticCode.POCKET_OFFSET_COLLAPSED
    with pytest.raises(PocketGenerationError) as invalid:
        build_pocket_offset_loops(_rectangle(), float("nan"), 2, 1.0e-7)
    assert invalid.value.code is DiagnosticCode.POCKET_OFFSET_FAILED
    unit = LengthUnit.MM
    points = tuple(Point3(x, y, 0, unit) for x, y in ((0, 0), (10, 10), (0, 10), (10, 0)))
    bowtie = ContourLoop(tuple(ContourSegment(ContourCurveKind.LINE, points[index],
        points[(index + 1) % len(points)]) for index in range(len(points))),
        ContourOrientation.COUNTERCLOCKWISE)
    with pytest.raises(PocketGenerationError):
        build_pocket_offset_loops(bowtie, 1, 2, 1.0e-7)


def test_offset_collapse_does_not_leave_an_uncut_center_strip() -> None:
    with pytest.raises(PocketGenerationError) as collapse:
        _inputs(
            _rectangle(40, 22),
            {"stepover": Length(9, LengthUnit.MM)},
        )

    assert collapse.value.code is DiagnosticCode.POCKET_OFFSET_COLLAPSED


def test_rotated_and_arc_boundaries_generate_without_bounding_box_fallback() -> None:
    rotated = _rectangle()
    angle = math.radians(31)
    transform = lambda point: Point3(
        point.x * math.cos(angle) - point.y * math.sin(angle),
        point.x * math.sin(angle) + point.y * math.cos(angle),
        point.z,
        point.unit,
    )
    rotated = ContourLoop(tuple(ContourSegment(segment.kind, transform(segment.start),
        transform(segment.end)) for segment in rotated.segments), rotated.orientation)
    rotated_generator, rotated_inputs, _, _ = _inputs(rotated)
    computing, _ = rotated_generator.begin(rotated_inputs)
    rotated_artifact = rotated_generator.generate(computing)
    assert rotated_artifact.statistics.total_cutting_length > 0

    arc_generator, arc_inputs, _, _ = _inputs(_arc_boundary(), {"stepover": Length(2, LengthUnit.MM)})
    arc_computing, _ = arc_generator.begin(arc_inputs)
    arc_artifact = arc_generator.generate(arc_computing)
    assert any(isinstance(event, ArcMove) and event.motion_class is MotionClass.CUTTING
               for event in arc_artifact.events)


def test_depth_layers_single_multiple_and_exact_final_depth() -> None:
    assert pocket_depth_levels(0, -3, 5, 1.0e-8) == (-3,)
    assert pocket_depth_levels(0, -2.5, 1, 1.0e-8) == (-1, -2, -2.5)
    levels = pocket_depth_levels(0, -0.3, 0.1, 1.0e-8)
    assert levels[-1] == -0.3
    assert len(levels) == len(set(levels)) == 3
    with pytest.raises(PocketGenerationError) as invalid:
        pocket_depth_levels(0, -1, 0, 1.0e-8)
    assert invalid.value.code is DiagnosticCode.POCKET_INVALID_STEPDOWN


def test_cutting_direction_controls_loop_orientation_and_fingerprint() -> None:
    _, climb, _, _ = _inputs()
    _, conventional, _, _ = _inputs(strategy_changes={
        "cutting_direction": PocketCuttingDirection.CONVENTIONAL,
    })
    assert all(loop.orientation is ContourOrientation.COUNTERCLOCKWISE
               for loop in climb.offset_loops)
    assert all(loop.orientation is ContourOrientation.CLOCKWISE
               for loop in conventional.offset_loops)
    assert climb.strategy.fingerprint != conventional.strategy.fingerprint


def test_stock_content_participates_in_pocket_recompute_fingerprint() -> None:
    generator, inputs, _, resolved = _inputs()
    stock = inputs.setup.stock
    assert isinstance(stock, BoxStock)
    changed_setup = inputs.setup.with_stock(BoxStock(
        Length(stock.size_x.value + 5, stock.size_x.unit),
        stock.size_y,
        stock.size_z,
        stock.frame,
    ))
    changed = generator.resolve_inputs(
        inputs.operation,
        changed_setup,
        assembly=inputs.assembly,
        tool=inputs.tool,
        machine=inputs.machine,
        resolved_geometry=resolved,
    )
    assert changed.input_fingerprint != inputs.input_fingerprint


def test_generation_is_deterministic_retracts_safely_and_stays_inside_rectangle() -> None:
    generator, inputs, _, _ = _inputs()
    computing, token = generator.begin(inputs)
    artifact = generator.generate(computing)
    replay_computing, _ = generator.begin(inputs)
    replay = generator.generate(replay_computing)
    assert artifact.artifact_fingerprint == replay.artifact_fingerprint
    assert tuple(event.event_id for event in artifact.events) == tuple(event.event_id for event in replay.events)
    cuts = tuple(event for event in artifact.events
                 if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING)
    assert cuts
    assert {event.start.position.z for event in cuts} == {-1.0, -2.0, -3.0}
    assert all(0 <= point <= limit for event in cuts
               for point, limit in ((event.start.position.x, 40), (event.end.position.x, 40),
                                    (event.start.position.y, 30), (event.end.position.y, 30)))
    assert all(min(event.start.position.z, event.end.position.z) >= 2
               for event in artifact.events if isinstance(event, RapidMove))
    assert any(isinstance(event, LinearMove) and "plunge" in event.provenance
               for event in artifact.events)

    changed = replace(computing.operation,
                      artifact_state=computing.operation.artifact_state.mark_dirty(
                          DirtyReason.PARAMETERS_CHANGED))
    newer, _ = changed.artifact_state.begin(inputs.input_fingerprint)
    stale = publish_toolpath(replace(changed, artifact_state=newer), artifact, token,
                             inputs.input_fingerprint)
    assert not stale.accepted and stale.artifact is None

    disabled = publish_toolpath(replace(computing.operation, enabled=False), artifact, token,
                                inputs.input_fingerprint)
    assert not disabled.accepted and disabled.artifact is None

    registry = ToolpathPresentationRegistry()
    registry.bind_project(1)
    assert registry.display(artifact, generation=1)
    before = registry.presentations
    if stale.accepted and stale.artifact is not None:
        registry.display(stale.artifact, generation=1)
    assert registry.presentations == before


def test_invalid_tool_and_stepover_fail_before_token() -> None:
    generator, inputs, _, resolved = _inputs()
    with pytest.raises(PocketGenerationError) as missing:
        generator.resolve_inputs(inputs.operation, inputs.setup, assembly=None, tool=None,
                                 machine=inputs.machine, resolved_geometry=resolved)
    assert missing.value.code is DiagnosticCode.POCKET_TOOL_MISSING
    stale_assembly = replace(inputs.assembly, revision=inputs.assembly.revision.next())
    with pytest.raises(PocketGenerationError) as stale:
        generator.resolve_inputs(inputs.operation, inputs.setup, assembly=stale_assembly,
                                 tool=inputs.tool, machine=inputs.machine,
                                 resolved_geometry=resolved)
    assert stale.value.code is DiagnosticCode.POCKET_TOOL_STALE
    too_wide = replace(inputs.strategy, stepover=Length(10, LengthUnit.MM))
    operation = replace(inputs.operation, parameters=too_wide.to_operation_parameters())
    with pytest.raises(PocketGenerationError) as stepover:
        generator.resolve_inputs(operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
                                 machine=inputs.machine, resolved_geometry=resolved)
    assert stepover.value.code is DiagnosticCode.POCKET_INVALID_STEPOVER


def test_foreign_source_boundary_fails_before_token() -> None:
    generator, inputs, _, _ = _inputs()
    foreign_reference = _reference(uuid4())
    foreign_strategy = replace(
        inputs.strategy,
        geometry=PocketGeometryInput(foreign_reference, inputs.strategy.unit),
    )
    operation = replace(
        inputs.operation,
        geometry_inputs=(replace(
            inputs.operation.geometry_inputs[0],
            reference=foreign_reference,
        ),),
        parameters=foreign_strategy.to_operation_parameters(),
    )
    resolved = ResolvedPocketGeometry(
        GeometryResolutionStatus.RESOLVED,
        _region(_rectangle(), foreign_reference),
    )

    with pytest.raises(PocketGenerationError) as mismatch:
        generator.resolve_inputs(
            operation,
            inputs.setup,
            assembly=inputs.assembly,
            tool=inputs.tool,
            machine=inputs.machine,
            resolved_geometry=resolved,
        )

    assert mismatch.value.code is DiagnosticCode.POCKET_PROFILE_INVALID


def test_additional_geometry_input_is_not_silently_ignored() -> None:
    generator, inputs, _, resolved = _inputs()
    boundary = inputs.operation.geometry_inputs[0]
    extra = OperationGeometryInput(
        GeometryInputId.new(),
        GeometryInputRole.PROFILE,
        boundary.reference,
        True,
        boundary.reference.kind,
        1,
    )
    operation = replace(inputs.operation, geometry_inputs=(boundary, extra))

    with pytest.raises(PocketGenerationError) as unsupported:
        generator.resolve_inputs(
            operation,
            inputs.setup,
            assembly=inputs.assembly,
            tool=inputs.tool,
            machine=inputs.machine,
            resolved_geometry=resolved,
        )

    assert unsupported.value.code is DiagnosticCode.POCKET_PROFILE_INVALID


def test_recompute_publish_keeps_previous_valid_artifact_on_resolution_failure(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Pocket Core")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(lambda app: app.add_basic_resources(tool, holder, assembly, machine))
    reference = _reference(setup.source_scope.primary_source_id)
    geometry_input = OperationGeometryInput(
        GeometryInputId.new(), GeometryInputRole.BOUNDARY, reference,
        True, GeometryReferenceKind.FACE, 0,
    )
    strategy = _strategy(reference)
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (geometry_input,),
        strategy.to_operation_parameters(),
        MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint,
                           machine.unit, (OperationCapability.MILLING,)),
    )
    service.execute_cam_command(lambda app: app.update_tree(job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Pocket", operation)))
    resolved = ResolvedPocketGeometry(GeometryResolutionStatus.RESOLVED,
                                      _region(_rectangle(), reference))
    result = service.compute_pocket(operation.operation_id,
                                    geometry_resolver=lambda _reference: resolved)
    assert result.accepted and result.operation.artifact_state.status is ArtifactStatus.VALID
    artifact = service.load_toolpath_artifact(operation.operation_id)
    assert artifact is not None
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)
    assert registry.display(artifact, generation=service.cam_generation)
    shown_before_error = registry.presentations
    stale_geometry = ResolvedPocketGeometry(
        GeometryResolutionStatus.STALE,
        diagnostics=(ValidationDiagnostic(DiagnosticSeverity.ERROR,
                                          DiagnosticCode.POCKET_PROFILE_STALE,
                                          "stale"),),
    )
    failed = service.compute_pocket(operation.operation_id,
                                    geometry_resolver=lambda _reference: stale_geometry)
    assert not failed.accepted and failed.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(operation.operation_id) == artifact
    assert registry.presentations == shown_before_error
    service.save()
    root = session.root_path
    service.close_project()
    service.open_project(root)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert PocketStrategy.from_operation_parameters(
        restored.parameters, restored.geometry_inputs[0].reference) == strategy
    assert restored.artifact_state.status is ArtifactStatus.VALID
