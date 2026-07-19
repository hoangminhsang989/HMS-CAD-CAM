"""Facing 2.5D domain, generator, recompute and lifecycle tests."""

from dataclasses import replace
import math
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.application import FacingGenerationError, FacingGenerator, basic_mill_resources
from hms_cadcam.cam.domain import (
    ArtifactStatus, BoxStock, CamInvariantError, CamNodeId, DiagnosticCode, DirtyReason,
    FacingBoundarySource, FacingCutDirection,
    FacingParameters, FeedRate, FeedUnit, GeometryFingerprint, Length, LengthUnit,
    GeometryInputId, GeometryInputRole, GeometryReference, GeometryReferenceId,
    GeometryReferenceKind, GeometryRepresentationKind, GeometryResolutionStatus,
    MachineRequirement, OccurrenceTransformProvenance, Operation, OperationCapability,
    OperationFamily, OperationGeometryInput, OperationId, PlanarFaceBounds,
    PlanarFaceDescriptor, Point3, ResolvedFaceBoundary, FaceBoundaryCurve,
    ResolvedMachiningGeometry, Revision, SpindleSpeed, ToolAssemblyReference, Vector3,
    WcsFrame,
    HMS_GEOMETRY_REFERENCE_SCHEME, HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.cam.toolpath import LinearMove, MotionClass, RapidMove, publish_toolpath
from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.ui.cam_ui import _default_setup
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.viewer.toolpath import ToolpathPresentationRegistry
from hms_cadcam.viewer.ocp import backend as ocp_backend_module


def _parameters(*, angle: float = 0.0, direction: FacingCutDirection = FacingCutDirection.BIDIRECTIONAL,
                target: float = 48.0, stepdown: float = 1.0,
                stepover: float = 5.0, overtravel: float = 1.0) -> FacingParameters:
    unit = LengthUnit.MM
    return FacingParameters(unit, FacingBoundarySource.STOCK_BOX, Length(50, unit), Length(target, unit),
        Length(stepdown, unit), Length(stepover, unit), Length(0, unit), Length(55, unit), Length(52, unit),
        FeedRate(500, FeedUnit.MM_PER_MINUTE), FeedRate(100, FeedUnit.MM_PER_MINUTE),
        SpindleSpeed(1000), direction, angle, Length(overtravel, unit))


def _inputs(parameters: FacingParameters | None = None):
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    node_id, operation_id = CamNodeId.new(), OperationId.new()
    requirement = MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint,
                                     machine.unit, (OperationCapability.MILLING,))
    operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (),
        (parameters or _parameters()).to_operation_parameters(), requirement)
    generator = FacingGenerator()
    inputs = generator.resolve_inputs(operation, setup, assembly=assembly, tool=tool, machine=machine)
    return generator, inputs


def _artifact(parameters: FacingParameters | None = None):
    generator, inputs = _inputs(parameters)
    computing, token = generator.begin(inputs)
    return generator, inputs, computing, token, generator.generate(computing)


def _descriptor(points: tuple[Point3, ...], normal: Vector3 = Vector3(0, 0, 1)):
    source_id = uuid4()
    reference_id = GeometryReferenceId.new()
    closed = (*points, points[0])
    fingerprint = GeometryFingerprint.from_payload({"face": [point.to_dict() for point in points]})
    descriptor = PlanarFaceDescriptor(
        reference_id, source_id, points[0], Vector3(1, 0, 0), Vector3(0, 1, 0), normal,
        ResolvedFaceBoundary(closed, (FaceBoundaryCurve.LINE,) * len(points)), (),
        PlanarFaceBounds(
            Point3(min(point.x for point in points), min(point.y for point in points),
                   min(point.z for point in points), LengthUnit.MM),
            Point3(max(point.x for point in points), max(point.y for point in points),
                   max(point.z for point in points), LengthUnit.MM),
        ), LengthUnit.MM, fingerprint,
        OccurrenceTransformProvenance(None, (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)),
    )
    reference = GeometryReference(
        reference_id, HMS_GEOMETRY_REFERENCE_SCHEME, HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
        source_id, GeometryReferenceKind.FACE, GeometryRepresentationKind.BREP,
        fingerprint, Revision(0), subshape_selector="hms_face_v1:" + "a" * 64 + ":" + "b" * 64,
    )
    return descriptor, OperationGeometryInput(
        GeometryInputId.new(), GeometryInputRole.BOUNDARY, reference,
        True, GeometryReferenceKind.FACE, 0,
    )


def test_parameters_are_versioned_round_trip_and_deterministic() -> None:
    value = _parameters(angle=217.0)
    decoded = FacingParameters.from_operation_parameters(value.to_operation_parameters())
    assert decoded == value
    assert decoded.raster_angle_degrees == 37.0
    assert decoded.fingerprint == value.fingerprint
    assert value.to_operation_parameters().fingerprint == decoded.to_operation_parameters().fingerprint


@pytest.mark.parametrize("field,value", [
    ("stepdown", 0.0), ("stepover", -1.0), ("stock_allowance", -0.1),
    ("raster_angle_degrees", float("nan")), ("raster_angle_degrees", float("inf")),
])
def test_invalid_numeric_parameters_are_rejected(field: str, value: float) -> None:
    parameters = _parameters()
    replacement = value if field == "raster_angle_degrees" else Length(value, LengthUnit.MM)
    with pytest.raises((ValueError, CamInvariantError)):
        replace(parameters, **{field: replacement})


def test_unknown_unit_and_future_strategy_version_are_rejected() -> None:
    with pytest.raises(ValueError):
        replace(_parameters(), unit=LengthUnit.UNKNOWN)
    with pytest.raises(ValueError):
        replace(_parameters(), strategy_version=2)


def test_planar_descriptor_accepts_aligned_face_and_rejects_other_geometry() -> None:
    points = tuple(Point3(x, y, 5, LengthUnit.MM) for x, y in ((0, 0), (5, 0), (5, 4), (0, 4)))
    descriptor, _ = _descriptor(points)
    assert descriptor.outer_boundary.points[:-1] == points
    with pytest.raises(CamInvariantError):
        replace(descriptor, x_axis=Vector3(0, 1, 0))
    stale = ResolvedMachiningGeometry(GeometryResolutionStatus.STALE, message="stale face")
    assert stale.planar_face is None
    with pytest.raises(CamInvariantError):
        ResolvedMachiningGeometry(GeometryResolutionStatus.RESOLVED)


@pytest.mark.parametrize("angle", [0.0, 90.0, 37.0])
def test_raster_angles_cover_boundary_and_have_safe_motion(angle: float) -> None:
    _, _, _, _, artifact = _artifact(_parameters(angle=angle, target=49.0))
    cuts = [event for event in artifact.events if isinstance(event, LinearMove)
            and event.motion_class is MotionClass.CUTTING]
    assert cuts
    assert min(event.end.position.z for event in cuts) == 49.0
    assert artifact.bounds.minimum.x <= 0.0 and artifact.bounds.maximum.x >= 100.0
    assert artifact.bounds.minimum.y <= 0.0 and artifact.bounds.maximum.y >= 100.0
    for event in artifact.events:
        if isinstance(event, RapidMove):
            assert min(event.start.position.z, event.end.position.z) > 50.0


def test_multiple_stepdowns_bidirectional_order_and_overtravel() -> None:
    _, _, _, _, artifact = _artifact(_parameters(target=47.5, stepdown=1.0))
    cuts = [event for event in artifact.events if isinstance(event, LinearMove)
            and event.motion_class is MotionClass.CUTTING]
    depths = sorted({event.start.position.z for event in cuts}, reverse=True)
    assert depths == [49.0, 48.0, 47.5]
    first, second = cuts[0], cuts[1]
    assert first.end.position.x > first.start.position.x
    assert second.end.position.x < second.start.position.x
    assert first.start.position.x == pytest.approx(-6.0)
    assert first.end.position.x == pytest.approx(106.0)


def test_float_rounding_does_not_duplicate_the_final_depth() -> None:
    _, _, _, _, artifact = _artifact(_parameters(target=49.9, stepdown=0.1))
    cuts = [event for event in artifact.events if isinstance(event, LinearMove)
            and event.motion_class is MotionClass.CUTTING]
    assert {event.start.position.z for event in cuts} == {49.9}


def test_angled_raster_keeps_vertex_lanes_for_full_boundary_coverage() -> None:
    angle = 37.0
    _, inputs, _, _, artifact = _artifact(_parameters(
        angle=angle, target=49.0, stepover=10.0, overtravel=0.0))
    cuts = [event for event in artifact.events if isinstance(event, LinearMove)
            and event.motion_class is MotionClass.CUTTING]
    radians = math.radians(angle)
    v_axis = (-math.sin(radians), math.cos(radians))
    lane_positions = [event.start.position.x * v_axis[0] +
                      event.start.position.y * v_axis[1] for event in cuts]
    boundary_positions = [point.x * v_axis[0] + point.y * v_axis[1]
                          for point in inputs.region.boundary]
    assert min(lane_positions) == pytest.approx(min(boundary_positions))
    assert max(lane_positions) == pytest.approx(max(boundary_positions))


def test_climb_and_conventional_keep_consistent_pass_direction() -> None:
    for direction, sign in ((FacingCutDirection.CLIMB, 1), (FacingCutDirection.CONVENTIONAL, -1)):
        _, _, _, _, artifact = _artifact(_parameters(direction=direction, target=49.0))
        cuts = [event for event in artifact.events if isinstance(event, LinearMove)
                and event.motion_class is MotionClass.CUTTING]
        assert all((event.end.position.x - event.start.position.x) * sign > 0 for event in cuts)


def test_event_ids_fingerprint_bounds_and_statistics_are_deterministic() -> None:
    _, _, _, _, first = _artifact()
    _, _, _, _, second = _artifact()
    # Different operation identities intentionally produce different provenance.
    generator, inputs = _inputs()
    computing, _ = generator.begin(inputs)
    same_first = generator.generate(computing)
    generator2 = FacingGenerator()
    replay_inputs = replace(inputs, operation=replace(inputs.operation, artifact_state=inputs.operation.artifact_state))
    replay_computing, _ = generator2.begin(replay_inputs)
    same_second = generator2.generate(replay_computing)
    assert tuple(event.event_id for event in same_first.events) == tuple(event.event_id for event in same_second.events)
    assert same_first.artifact_fingerprint == same_second.artifact_fingerprint
    assert same_first.statistics.total_cutting_length > 0
    assert same_first.bounds.minimum.z == 48.0
    assert first.source_operation_id != second.source_operation_id


def test_stale_input_is_not_published_and_generation_has_no_partial_artifact() -> None:
    generator, inputs, computing, token, candidate = _artifact()
    changed_operation = replace(computing.operation,
        parameters=_parameters(target=47.0).to_operation_parameters())
    changed = generator.resolve_inputs(changed_operation, inputs.setup, assembly=inputs.assembly,
                                       tool=inputs.tool, machine=inputs.machine)
    result = publish_toolpath(computing.operation, candidate, token, changed.input_fingerprint)
    assert not result.accepted
    assert result.operation.artifact_state.status is ArtifactStatus.DIRTY
    with pytest.raises(FacingGenerationError):
        generator.generate(inputs)


def test_unsupported_tool_and_machine_limit_fail_with_specific_diagnostic() -> None:
    generator, inputs = _inputs()
    too_fast = replace(inputs.parameters, feed_rate=FeedRate(6000, FeedUnit.MM_PER_MINUTE))
    operation = replace(inputs.operation, parameters=too_fast.to_operation_parameters())
    with pytest.raises(FacingGenerationError) as captured:
        generator.resolve_inputs(operation, inputs.setup, assembly=inputs.assembly,
                                 tool=inputs.tool, machine=inputs.machine)
    assert captured.value.code.value == "facing.machine_incompatible"


def test_excessive_pass_count_is_rejected_before_generation() -> None:
    parameters = replace(_parameters(), stepover=Length(1.0e-12, LengthUnit.MM))
    generator, inputs = _inputs()
    operation = replace(inputs.operation, parameters=parameters.to_operation_parameters())
    with pytest.raises(FacingGenerationError) as captured:
        generator.resolve_inputs(operation, inputs.setup, assembly=inputs.assembly,
                                 tool=inputs.tool, machine=inputs.machine)
    assert captured.value.code.value == "facing.invalid_parameters"


def test_face_resolution_stale_fails_closed() -> None:
    parameters = replace(_parameters(target=49.0), boundary_source=FacingBoundarySource.PLANAR_FACE)
    generator, inputs = _inputs()
    operation = replace(inputs.operation, parameters=parameters.to_operation_parameters())
    with pytest.raises(FacingGenerationError) as captured:
        generator.resolve_inputs(operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, machine=inputs.machine,
            resolved_face=ResolvedMachiningGeometry(GeometryResolutionStatus.TOPOLOGY_CHANGED,
                                                     message="topology changed"))
    assert captured.value.code.value == "facing.geometry_stale"


def test_planar_face_top_must_match_the_absolute_facing_top() -> None:
    parameters = replace(_parameters(target=49.0), boundary_source=FacingBoundarySource.PLANAR_FACE)
    generator, inputs = _inputs()
    points = tuple(Point3(x, y, 49, LengthUnit.MM)
                   for x, y in ((0, 0), (10, 0), (10, 10), (0, 10)))
    descriptor, geometry_input = _descriptor(points)
    operation = replace(inputs.operation, parameters=parameters.to_operation_parameters(),
                        geometry_inputs=(geometry_input,))
    resolved = generator.resolve_inputs(operation, inputs.setup, assembly=inputs.assembly,
            tool=inputs.tool, machine=inputs.machine,
            resolved_face=ResolvedMachiningGeometry(GeometryResolutionStatus.RESOLVED, descriptor))
    assert resolved.region.boundary[0].z == 49


def test_rotated_setup_wcs_transforms_planar_boundary_exactly_once() -> None:
    parameters = replace(_parameters(target=49.0), boundary_source=FacingBoundarySource.PLANAR_FACE)
    generator, inputs = _inputs()
    rotated_wcs = WcsFrame(
        Point3(10, 20, 0, LengthUnit.MM),
        Vector3(0, 1, 0), Vector3(-1, 0, 0), Vector3(0, 0, 1),
    )
    setup = replace(inputs.setup, wcs=rotated_wcs)
    points = tuple(Point3(x, y, 49, LengthUnit.MM)
                   for x, y in ((10, 20), (30, 20), (30, 35), (10, 35)))
    descriptor, geometry_input = _descriptor(points)
    operation = replace(inputs.operation, parameters=parameters.to_operation_parameters(),
                        geometry_inputs=(geometry_input,))

    resolved = generator.resolve_inputs(
        operation, setup, assembly=inputs.assembly, tool=inputs.tool,
        machine=inputs.machine,
        resolved_face=ResolvedMachiningGeometry(GeometryResolutionStatus.RESOLVED, descriptor),
    )

    assert tuple((point.x, point.y, point.z) for point in resolved.region.boundary) == (
        (0.0, 0.0, 49.0), (0.0, -20.0, 49.0),
        (15.0, -20.0, 49.0), (15.0, 0.0, 49.0),
    )


def test_planar_face_accepts_opposite_normal_and_rejects_inner_loop() -> None:
    parameters = replace(_parameters(target=49.0), boundary_source=FacingBoundarySource.PLANAR_FACE)
    generator, inputs = _inputs()
    points = tuple(Point3(x, y, 49, LengthUnit.MM)
                   for x, y in ((0, 0), (10, 0), (10, 10), (0, 10)))
    descriptor, geometry_input = _descriptor(points)
    operation = replace(inputs.operation, parameters=parameters.to_operation_parameters(),
                        geometry_inputs=(geometry_input,))
    opposite = replace(descriptor, y_axis=Vector3(0, -1, 0), normal=Vector3(0, 0, -1))
    resolved = generator.resolve_inputs(
        operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
        machine=inputs.machine,
        resolved_face=ResolvedMachiningGeometry(GeometryResolutionStatus.RESOLVED, opposite),
    )
    assert resolved.region.normal.z > 0

    inner_points = tuple(Point3(x, y, 49, LengthUnit.MM)
                         for x, y in ((2, 2), (3, 2), (3, 3), (2, 3)))
    inner = ResolvedFaceBoundary((*inner_points, inner_points[0]),
                                 (FaceBoundaryCurve.LINE,) * 4)
    with pytest.raises(FacingGenerationError) as captured:
        generator.resolve_inputs(
            operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
            machine=inputs.machine,
            resolved_face=ResolvedMachiningGeometry(
                GeometryResolutionStatus.RESOLVED,
                replace(descriptor, inner_boundaries=(inner,)),
            ),
        )
    assert captured.value.code is DiagnosticCode.FACING_UNSUPPORTED_INNER_LOOPS


def test_planar_face_above_stock_top_is_rejected() -> None:
    parameters = replace(_parameters(target=49.0), boundary_source=FacingBoundarySource.PLANAR_FACE)
    generator, inputs = _inputs()
    points = tuple(Point3(x, y, 51, LengthUnit.MM)
                   for x, y in ((0, 0), (10, 0), (10, 10), (0, 10)))
    descriptor, geometry_input = _descriptor(points)
    operation = replace(inputs.operation, parameters=parameters.to_operation_parameters(),
                        geometry_inputs=(geometry_input,))
    with pytest.raises(FacingGenerationError) as captured:
        generator.resolve_inputs(
            operation, inputs.setup, assembly=inputs.assembly, tool=inputs.tool,
            machine=inputs.machine,
            resolved_face=ResolvedMachiningGeometry(GeometryResolutionStatus.RESOLVED, descriptor),
        )
    assert captured.value.code is DiagnosticCode.FACING_TARGET_ABOVE_STOCK


def test_project_compute_save_open_and_artifact_round_trip(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Facing Round Trip")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(lambda app: app.add_basic_resources(tool, holder, assembly, machine))
    node_id, operation_id = CamNodeId.new(), OperationId.new()
    requirement = MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint,
                                     machine.unit, (OperationCapability.MILLING,))
    operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (), _parameters(target=49.0).to_operation_parameters(), requirement)
    service.execute_cam_command(lambda app: app.update_tree(job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Facing", operation)))
    result = service.compute_facing(operation_id)
    assert result.accepted and service.load_toolpath_artifact(operation_id) is not None
    service.save()
    root = session.root_path
    service.close_project()
    service.open_project(root)
    restored = next(operation for job in service.cam_snapshot.jobs for setup in job.setups
                    for operation in setup.operation_tree.operations)
    assert FacingParameters.from_operation_parameters(restored.parameters) == _parameters(target=49.0)
    assert restored.artifact_state.status is ArtifactStatus.VALID


def test_planar_reference_compute_round_trip_and_failed_recompute_keeps_valid(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config-planar")
    session = service.new_project(tmp_path, "Planar Round Trip")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(lambda app: app.add_basic_resources(tool, holder, assembly, machine))
    points = tuple(Point3(x, y, 49, LengthUnit.MM)
                   for x, y in ((0, 0), (20, 0), (20, 15), (0, 15)))
    descriptor, geometry_input = _descriptor(points)
    parameters = replace(_parameters(target=49.0), boundary_source=FacingBoundarySource.PLANAR_FACE)
    node_id, operation_id = CamNodeId.new(), OperationId.new()
    requirement = MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint,
                                     machine.unit, (OperationCapability.MILLING,))
    operation = Operation(
        operation_id, node_id, OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (geometry_input,),
        parameters.to_operation_parameters(), requirement,
    )
    service.execute_cam_command(lambda app: app.update_tree(
        job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Planar Facing", operation),
    ))
    result = service.compute_facing(
        operation_id,
        face_resolver=lambda _reference: ResolvedMachiningGeometry(
            GeometryResolutionStatus.RESOLVED, descriptor
        ),
    )
    assert result.accepted and result.operation.artifact_state.status is ArtifactStatus.VALID
    artifact = service.load_toolpath_artifact(operation_id)
    failed = service.compute_facing(
        operation_id,
        face_resolver=lambda _reference: ResolvedMachiningGeometry(
            GeometryResolutionStatus.STALE,
            message="stale",
            diagnostic_code=DiagnosticCode.FACING_FACE_REFERENCE_STALE,
        ),
    )
    assert not failed.accepted
    assert failed.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(operation_id) == artifact
    service.save()
    root = session.root_path
    service.close_project()
    service.open_project(root)
    restored = next(operation for job in service.cam_snapshot.jobs for setup in job.setups
                    for operation in setup.operation_tree.operations)
    assert restored.geometry_inputs == (geometry_input,)
    copied = service.save_as(tmp_path, "Planar Round Trip Copy")
    assert copied.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].geometry_inputs == (geometry_input,)
    copied_job = copied.cam_snapshot.jobs[0]
    copied_setup = copied_job.setups[0]
    copied_operation = copied_setup.operation_tree.operations[0]
    autosave_reference = replace(copied_operation.geometry_inputs[0].reference,
                                 hint="autosave persistent FACE")
    autosave_input = replace(copied_operation.geometry_inputs[0], reference=autosave_reference)
    autosave_operation = replace(
        copied_operation,
        geometry_inputs=(autosave_input,),
        revision=copied_operation.revision.next(),
        artifact_state=copied_operation.artifact_state.mark_dirty(DirtyReason.GEOMETRY_CHANGED),
    )
    service.execute_cam_command(lambda app: app.update_tree(
        copied_job.job_id, copied_setup.setup_id,
        lambda tree: tree.replace_operation(autosave_operation),
    ))
    assert service.autosave() is not None
    opener = ProjectService.create_default(tmp_path / "config-recovery")
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as recovery:
        opener.open_project(copied.root_path)
    recovered = opener.recover_project(recovery.value.assessment)
    recovered_input = recovered.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].geometry_inputs[0]
    assert recovered_input.reference == autosave_reference
    assert service.load_toolpath_artifact(operation_id).artifact_fingerprint == result.artifact.artifact_fingerprint


def test_cad_release_marks_valid_planar_artifact_dirty_when_resolver_is_unavailable(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Planar CAD Release")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(lambda app: app.add_basic_resources(tool, holder, assembly, machine))
    points = tuple(Point3(x, y, 49, LengthUnit.MM)
                   for x, y in ((0, 0), (20, 0), (20, 15), (0, 15)))
    descriptor, geometry_input = _descriptor(points)
    parameters = replace(_parameters(target=49.0), boundary_source=FacingBoundarySource.PLANAR_FACE)
    node_id, operation_id = CamNodeId.new(), OperationId.new()
    requirement = MachineRequirement(machine.machine_id, machine.revision,
        machine.content_fingerprint, machine.unit, (OperationCapability.MILLING,))
    operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (geometry_input,),
        parameters.to_operation_parameters(), requirement)
    service.execute_cam_command(lambda app: app.update_tree(job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Planar Facing", operation)))
    assert service.compute_facing(operation_id, face_resolver=lambda _reference:
        ResolvedMachiningGeometry(GeometryResolutionStatus.RESOLVED, descriptor)).accepted

    def unavailable(_reference):
        raise RuntimeError("CAD document has been released")

    workspace = CamWorkspace(service, lambda: session.manifest.source_files[0].source_id,
                             face_resolver=unavailable)
    workspace.cad_context_changed()
    changed = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert changed.artifact_state.status is ArtifactStatus.DIRTY
    assert DirtyReason.GEOMETRY_CHANGED in changed.artifact_state.dirty_reasons
    workspace.deleteLater()


def test_cad_context_change_after_project_close_is_a_clean_noop(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "CAD Close Lifecycle")
    workspace = CamWorkspace(service, lambda: None)

    service.close_project(discard_changes=True)
    workspace.cad_context_changed()

    assert not service.has_project
    workspace.deleteLater()


def test_facing_ui_invalid_field_does_not_mutate_and_generate_displays(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Facing UI", source)
    displayed = []
    cleared = []
    workspace = CamWorkspace(service, lambda: session.manifest.source_files[0].source_id,
                             toolpath_display=displayed.append, toolpath_clear=lambda: cleared.append(True))
    workspace.create_job(); workspace.create_setup(); workspace.create_basic_resources(); workspace.add_operation()
    assert workspace.actions["generate"].isEnabled()
    before = service.cam_snapshot
    workspace.editor._facing_fields["stepdown"].setText("0")
    assert not workspace.actions["generate"].isEnabled()
    workspace.editor._submit()
    assert service.cam_snapshot == before
    assert workspace.editor.error.text()
    workspace.editor._facing_fields["stepdown"].setText("1")
    workspace.editor._facing_fields["target"].setText("48")
    assert not workspace.actions["generate"].isEnabled()
    workspace.editor._submit()
    assert workspace.actions["generate"].isEnabled()
    workspace.generate_selected()
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert operation.artifact_state.status is ArtifactStatus.VALID
    assert displayed and displayed[-1].source_operation_id == operation.operation_id
    revision = operation.revision
    workspace.editor._fields["name"].setText("Facing renamed")
    workspace.editor._submit()
    renamed = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert renamed.revision == revision
    assert renamed.artifact_state.status is ArtifactStatus.VALID
    workspace.bind_project(session)
    assert cleared
    workspace.deleteLater()


def test_recompute_store_failure_keeps_previous_artifact(tmp_path, monkeypatch) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "Facing Store Failure")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(lambda app: app.add_basic_resources(tool, holder, assembly, machine))
    node_id, operation_id = CamNodeId.new(), OperationId.new()
    requirement = MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint,
                                     machine.unit, (OperationCapability.MILLING,))
    operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (), _parameters(target=49).to_operation_parameters(), requirement)
    service.execute_cam_command(lambda app: app.update_tree(job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Facing", operation)))
    first = service.compute_facing(operation_id)
    assert first.accepted
    old_metadata = service.cam_snapshot.artifacts
    old_artifact = service.load_toolpath_artifact(operation_id)

    def fail_publish(*_args, **_kwargs):
        raise ToolpathArtifactStoreError("injected failure")

    monkeypatch.setattr(service._cam_application._artifact_store, "publish", fail_publish)
    failed = service.compute_facing(operation_id)
    assert not failed.accepted
    assert service.cam_snapshot.artifacts == old_metadata
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert restored.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(operation_id).artifact_fingerprint == old_artifact.artifact_fingerprint


def test_stock_change_invalidates_and_operation_delete_prunes_artifact_metadata(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "Facing Dependencies")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(lambda app: app.add_basic_resources(tool, holder, assembly, machine))
    node_id, operation_id = CamNodeId.new(), OperationId.new()
    requirement = MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint,
                                     machine.unit, (OperationCapability.MILLING,))
    operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (),
        _parameters(target=49).to_operation_parameters(), requirement)
    service.execute_cam_command(lambda app: app.update_tree(job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Facing", operation)))
    assert service.compute_facing(operation_id).accepted
    changed_stock = BoxStock(Length(120, LengthUnit.MM), setup.stock.size_y,
                             setup.stock.size_z, setup.stock.frame)
    service.execute_cam_command(lambda app: app.update_stock(job_id, setup.setup_id, changed_stock))
    changed = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert changed.artifact_state.status is ArtifactStatus.DIRTY
    assert DirtyReason.STOCK_CHANGED in changed.artifact_state.dirty_reasons
    assert service.cam_snapshot.artifacts
    service.execute_cam_command(lambda app: app.update_tree(job_id, setup.setup_id,
        lambda tree: tree.remove_node(node_id)))
    assert service.cam_snapshot.artifacts == ()


def test_toolpath_presentation_registry_rejects_stale_project_and_clears() -> None:
    _, _, _, _, artifact = _artifact(_parameters(target=49))
    registry = ToolpathPresentationRegistry()
    registry.bind_project(7)
    assert not registry.display(artifact, generation=6)
    assert registry.display(artifact, generation=7)
    presentation = registry.presentations[0]
    assert presentation.segments and not presentation.highlighted
    registry.select(artifact.source_operation_id)
    assert registry.presentations[0].highlighted
    registry.set_visible(artifact.source_operation_id, False)
    assert not registry.presentations[0].visible
    registry.bind_project(8)
    assert registry.presentations == ()


def test_ocp_toolpath_display_failure_keeps_previous_presentation(monkeypatch) -> None:
    _, _, _, _, artifact = _artifact(_parameters(target=49))

    class FailingContext:
        def __init__(self) -> None:
            self.removed = []

        def SetColor(self, *_args) -> None:
            return None

        def Display(self, *_args) -> None:
            raise RuntimeError("injected display failure")

        def Remove(self, presentation, _update) -> None:
            self.removed.append(presentation)

        def UpdateCurrentViewer(self) -> None:
            return None

    context = FailingContext()
    backend = object.__new__(ocp_backend_module.OcpCadViewportBackend)
    backend._lifecycle = SimpleNamespace(initialized=True, context=context)
    old_presentation = object()
    backend._toolpaths = {artifact.source_operation_id: (old_presentation,)}
    monkeypatch.setattr(ocp_backend_module, "AIS_Shape", lambda _shape: object())
    monkeypatch.setattr(ocp_backend_module, "Quantity_Color", lambda *_args: object())
    with pytest.raises(RuntimeError, match="injected display failure"):
        backend.display_toolpath(artifact)
    assert backend._toolpaths == {artifact.source_operation_id: (old_presentation,)}
    assert old_presentation not in context.removed
