"""Stage 7B.4 pure-domain 2D Contour tests."""

from dataclasses import replace
import math
from uuid import uuid4

import pytest

from hms_cadcam.cam.application import ContourGenerationError, ContourGenerator, basic_mill_resources, offset_contour
from hms_cadcam.cam.application.contour import _canonical_start, _safe_lead_point, _sample_loop
from hms_cadcam.cam.domain import (
    ArtifactStatus, CamNodeId, ContourBounds, ContourCurveKind, ContourCutDirection, ContourLoop,
    ContourOrientation, ContourParameters, ContourProfileDescriptor, ContourProfileSource, DirtyReason,
    ContourSegment, ContourSide, FeedRate, FeedUnit, GeometryFingerprint, GeometryInputId,
    GeometryInputRole, GeometryReference, GeometryReferenceId, GeometryReferenceKind,
    GeometryRepresentationKind, GeometryResolutionStatus, Length, LengthUnit, MachineRequirement, OccurrenceTransformProvenance,
    Operation, OperationCapability, OperationFamily, OperationGeometryInput, OperationId, Point3,
    ProfileProvenance, ResolvedContourProfile, Revision, SpindleSpeed, ToolAssemblyReference,
    Vector3, HMS_GEOMETRY_REFERENCE_SCHEME, HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.cam.toolpath import ArcMove, LinearMove, MotionClass, RapidMove
from hms_cadcam.project.service import ProjectService
from hms_cadcam.cam.persistence import CamSqliteRepository
from hms_cadcam.ui.cam_ui import CamWorkspace, _default_setup
from hms_cadcam.viewer.toolpath import ToolpathPresentation
from PySide6.QtWidgets import QApplication

IDENTITY = (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0)


def _parameters(**changes) -> ContourParameters:
    unit = LengthUnit.MM
    values = dict(
        unit=unit, profile_source=ContourProfileSource.PLANAR_FACE_OUTER,
        side=ContourSide.ON, top_height=Length(50, unit), final_depth=Length(48, unit),
        stepdown=Length(1, unit), radial_stock_allowance=Length(0, unit),
        axial_stock_allowance=Length(0, unit), clearance_height=Length(55, unit),
        retract_height=Length(52, unit), cutting_feed_rate=FeedRate(500, FeedUnit.MM_PER_MINUTE),
        plunge_feed_rate=FeedRate(100, FeedUnit.MM_PER_MINUTE), spindle_speed=SpindleSpeed(1000),
        direction=ContourCutDirection.CLIMB, lead_length=Length(1, unit),
    )
    values.update(changes)
    return ContourParameters(**values)


def _rectangle_loop(size: float = 20.0, z: float = 0.0) -> ContourLoop:
    unit = LengthUnit.MM
    points = (Point3(0, 0, z, unit), Point3(size, 0, z, unit),
              Point3(size, size, z, unit), Point3(0, size, z, unit))
    return ContourLoop(tuple(ContourSegment(ContourCurveKind.LINE, points[index], points[(index + 1) % 4])
                             for index in range(4)), ContourOrientation.COUNTERCLOCKWISE)


def _descriptor(loop: ContourLoop | None = None, source_id=None):
    loop = loop or _rectangle_loop()
    source_id, reference_id = source_id or uuid4(), GeometryReferenceId.new()
    geometry = GeometryFingerprint.from_payload(loop.to_dict())
    reference = GeometryReference(
        reference_id, HMS_GEOMETRY_REFERENCE_SCHEME, HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
        source_id, GeometryReferenceKind.FACE, GeometryRepresentationKind.BREP, geometry,
        Revision(0), subshape_selector="hms_profile_v1:" + "a" * 64 + ":face:" + "b" * 64,
    )
    points = tuple(segment.start for segment in loop.segments)
    descriptor = ContourProfileDescriptor(
        reference, points[0], Vector3(1, 0, 0), Vector3(0, 1, 0), Vector3(0, 0, 1),
        loop, (), ContourBounds(Point3(min(p.x for p in points), min(p.y for p in points), 0, LengthUnit.MM),
                                Point3(max(p.x for p in points), max(p.y for p in points), 0, LengthUnit.MM)),
        LengthUnit.MM, geometry,
        ProfileProvenance(ContourProfileSource.PLANAR_FACE_OUTER,
                          OccurrenceTransformProvenance(None, IDENTITY)),
    )
    geometry_input = OperationGeometryInput(GeometryInputId.new(), GeometryInputRole.PROFILE,
        reference, True, GeometryReferenceKind.FACE, 0)
    return descriptor, geometry_input


def _inputs(parameters: ContourParameters | None = None, loop: ContourLoop | None = None):
    parameters = parameters or _parameters()
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    descriptor, geometry_input = _descriptor(loop, setup.source_scope.primary_source_id)
    operation = Operation(OperationId.new(), CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (geometry_input,), parameters.to_operation_parameters(),
        MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint,
                           machine.unit, (OperationCapability.MILLING,)))
    generator = ContourGenerator()
    inputs = generator.resolve_inputs(operation, setup, assembly=assembly, tool=tool, machine=machine,
                                      resolved_profile=ResolvedContourProfile(
                                          status=GeometryResolutionStatus.RESOLVED, profile=descriptor))
    return generator, inputs


def test_parameters_round_trip_depth_and_invalid_values() -> None:
    value = _parameters(axial_stock_allowance=Length(0.25, LengthUnit.MM))
    assert ContourParameters.from_operation_parameters(value.to_operation_parameters()) == value
    assert value.final_cut_depth == pytest.approx(48.25)
    with pytest.raises(ValueError):
        _parameters(stepdown=Length(0, LengthUnit.MM))
    with pytest.raises(ValueError):
        _parameters(final_depth=Length(50, LengthUnit.MM))


def test_rectangle_on_inside_outside_offsets_are_exact() -> None:
    loop = _rectangle_loop(20)
    on = offset_contour(loop, ContourSide.ON, 3)
    inside = offset_contour(loop, ContourSide.INSIDE, 3)
    outside = offset_contour(loop, ContourSide.OUTSIDE, 3)
    assert min(segment.start.x for segment in on.segments) == pytest.approx(0)
    assert min(segment.start.x for segment in inside.segments) == pytest.approx(3)
    assert max(segment.start.x for segment in inside.segments) == pytest.approx(17)
    assert min(segment.start.x for segment in outside.segments) == pytest.approx(-3)
    assert max(segment.start.x for segment in outside.segments) == pytest.approx(23)


def test_offset_collapse_is_rejected() -> None:
    with pytest.raises(ContourGenerationError) as captured:
        offset_contour(_rectangle_loop(4), ContourSide.INSIDE, 3)
    from hms_cadcam.cam.domain import DiagnosticCode
    assert captured.value.code in {DiagnosticCode.CONTOUR_OFFSET_COLLAPSED,
                                   DiagnosticCode.CONTOUR_OFFSET_FAILED}


def test_self_intersection_and_unsafe_lead_fail_closed() -> None:
    unit = LengthUnit.MM
    points = tuple(Point3(x, y, 0, unit) for x, y in ((0, 0), (10, 10), (0, 10), (10, 0)))
    bowtie = ContourLoop(tuple(ContourSegment(ContourCurveKind.LINE, points[index],
        points[(index + 1) % 4]) for index in range(4)), ContourOrientation.CLOCKWISE)
    from hms_cadcam.cam.domain import DiagnosticCode
    with pytest.raises(ContourGenerationError) as intersection:
        _inputs(loop=bowtie)
    assert intersection.value.code is DiagnosticCode.CONTOUR_SELF_INTERSECTION
    with pytest.raises(ContourGenerationError) as lead:
        _inputs(_parameters(side=ContourSide.INSIDE, lead_length=Length(20, unit)))
    assert lead.value.code is DiagnosticCode.CONTOUR_UNSAFE_LEAD


def test_lead_crossing_narrow_concavity_fails_closed() -> None:
    unit = LengthUnit.MM
    points = tuple(Point3(x, y, 0, unit) for x, y in (
        (0, 0), (120, 0), (120, 100), (41, 100),
        (41, 40), (39, 40), (39, 100), (0, 100),
    ))
    loop = ContourLoop(tuple(ContourSegment(ContourCurveKind.LINE, points[index],
        points[(index + 1) % len(points)]) for index in range(len(points))),
        ContourOrientation.COUNTERCLOCKWISE)
    canonical = _canonical_start(loop)

    from hms_cadcam.cam.domain import DiagnosticCode
    with pytest.raises(ContourGenerationError) as lead:
        _safe_lead_point(canonical, _sample_loop(canonical), ContourSide.INSIDE, 100)
    assert lead.value.code is DiagnosticCode.CONTOUR_UNSAFE_LEAD


def test_line_arc_join_and_concave_profile_offset_follow_v1_policy() -> None:
    unit = LengthUnit.MM
    line_arc = ContourLoop((
        ContourSegment(ContourCurveKind.LINE, Point3(-5, 0, 0, unit), Point3(5, 0, 0, unit)),
        ContourSegment(ContourCurveKind.ARC, Point3(5, 0, 0, unit), Point3(-5, 0, 0, unit),
                       Point3(0, 0, 0, unit), math.pi),
    ), ContourOrientation.COUNTERCLOCKWISE)
    offset = offset_contour(line_arc, ContourSide.INSIDE, 1)
    arcs = [segment for segment in offset.segments if segment.kind is ContourCurveKind.ARC]
    assert arcs and all(segment.radius == pytest.approx(4) for segment in arcs)
    for current, following in zip(offset.segments, (*offset.segments[1:], offset.segments[0]), strict=True):
        assert current.end == following.start
    generator, inputs = _inputs(_parameters(), line_arc)
    computing, _ = generator.begin(inputs)
    assert any(isinstance(event, ArcMove) for event in generator.generate(computing).events)

    points = tuple(Point3(x, y, 0, unit) for x, y in
                   ((0, 0), (8, 0), (8, 3), (3, 3), (3, 8), (0, 8)))
    concave = ContourLoop(tuple(ContourSegment(ContourCurveKind.LINE, points[index],
        points[(index + 1) % len(points)]) for index in range(len(points))),
        ContourOrientation.COUNTERCLOCKWISE)
    candidate = offset_contour(concave, ContourSide.INSIDE, 0.5)
    assert candidate.orientation is ContourOrientation.COUNTERCLOCKWISE


def test_generator_multiple_depths_safe_lead_and_deterministic_artifact() -> None:
    generator, inputs = _inputs()
    computing, _token = generator.begin(inputs)
    artifact = generator.generate(computing)
    cutting = tuple(event for event in artifact.events
                    if isinstance(event, (LinearMove, ArcMove)) and event.motion_class is MotionClass.CUTTING)
    assert len(cutting) == 12  # six split/rectangle segments at two depths
    assert {event.end.position.z for event in cutting} == {48.0, 49.0}
    assert any("lead_in" in event.provenance for event in artifact.events)
    assert any("lead_out" in event.provenance for event in artifact.events)
    assert artifact.events[-2].provenance == "contour.final.clearance"
    assert all(not isinstance(event, RapidMove) or
               min(event.start.position.z, event.end.position.z) >= 52.0
               for event in artifact.events)
    second_inputs = replace(inputs, operation=replace(inputs.operation, artifact_state=inputs.operation.artifact_state))
    second_computing, _ = generator.begin(second_inputs)
    second = generator.generate(second_computing)
    assert [event.event_id for event in artifact.events] == [event.event_id for event in second.events]
    assert artifact.artifact_fingerprint == second.artifact_fingerprint
    presentation = ToolpathPresentation.from_artifact(artifact)
    assert {segment.semantic for segment in presentation.segments} >= {
        "rapid", "plunge_link", "lead_in", "cutting", "lead_out", "retract"
    }


def test_single_depth_and_optional_finishing_pass_are_explicit() -> None:
    parameters = _parameters(multiple_depth_passes=False, finishing_pass=True)
    generator, inputs = _inputs(parameters)
    computing, _ = generator.begin(inputs)
    artifact = generator.generate(computing)
    cutting = tuple(event for event in artifact.events
                    if isinstance(event, (LinearMove, ArcMove)) and event.motion_class is MotionClass.CUTTING)
    assert {event.end.position.z for event in cutting} == {parameters.final_cut_depth}
    assert len(cutting) == 12  # one cutting loop plus one explicit spring/finishing loop


def test_climb_and_conventional_reverse_contour_direction() -> None:
    climb_generator, climb_inputs = _inputs(_parameters(side=ContourSide.OUTSIDE,
                                                        direction=ContourCutDirection.CLIMB))
    conventional_generator, conventional_inputs = _inputs(_parameters(side=ContourSide.OUTSIDE,
                                                                       direction=ContourCutDirection.CONVENTIONAL))
    climb, _ = climb_generator.begin(climb_inputs)
    conventional, _ = conventional_generator.begin(conventional_inputs)
    climb_artifact = climb_generator.generate(climb)
    conventional_artifact = conventional_generator.generate(conventional)
    climb_first = next(event for event in climb_artifact.events
                       if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING)
    conventional_first = next(event for event in conventional_artifact.events
                              if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING)
    assert (climb_first.end.position.x, climb_first.end.position.y) != (
        conventional_first.end.position.x, conventional_first.end.position.y)


def test_project_recompute_persistence_and_failed_recompute_keeps_valid(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Contour Round Trip")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(lambda app: app.add_basic_resources(tool, holder, assembly, machine))
    descriptor, geometry_input = _descriptor(source_id=setup.source_scope.primary_source_id)
    operation_id, node_id = OperationId.new(), CamNodeId.new()
    operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (geometry_input,), _parameters().to_operation_parameters(),
        MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint,
                           machine.unit, (OperationCapability.MILLING,)))
    service.execute_cam_command(lambda app: app.update_tree(job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "2D Contour", operation)))
    resolver = lambda _reference: ResolvedContourProfile(GeometryResolutionStatus.RESOLVED, descriptor)
    result = service.compute_contour(operation_id, profile_resolver=resolver)
    assert result.accepted and result.operation.artifact_state.status is ArtifactStatus.VALID
    artifact = service.load_toolpath_artifact(operation_id)
    from hms_cadcam.cam.domain import DiagnosticCode
    failed = service.compute_contour(operation_id, profile_resolver=lambda _reference:
        ResolvedContourProfile(GeometryResolutionStatus.STALE, message="stale",
                               diagnostic_code=DiagnosticCode.CONTOUR_PROFILE_STALE))
    assert not failed.accepted and failed.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(operation_id) == artifact
    service.save(); root = session.root_path; service.close_project(); service.open_project(root)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert ContourParameters.from_operation_parameters(restored.parameters) == _parameters()
    assert restored.geometry_inputs == (geometry_input,)
    copied = service.save_as(tmp_path, "Contour Round Trip Copy")
    assert copied.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].operation_id == operation_id
    copied_job = copied.cam_snapshot.jobs[0]; copied_setup = copied_job.setups[0]
    copied_operation = copied_setup.operation_tree.operations[0]
    edited_parameters = _parameters(lead_length=Length(2, LengthUnit.MM))
    edited = replace(copied_operation, parameters=edited_parameters.to_operation_parameters(),
                     revision=copied_operation.revision.next(),
                     artifact_state=copied_operation.artifact_state.mark_dirty(DirtyReason.PARAMETERS_CHANGED))
    service.execute_cam_command(lambda app: app.update_tree(copied_job.job_id, copied_setup.setup_id,
        lambda tree: tree.replace_operation(edited)))
    autosave = service.autosave()
    assert autosave is not None
    autosaved = CamSqliteRepository().load(autosave.path / "project.db")
    autosaved_operation = autosaved.jobs[0].setups[0].operation_tree.operations[0]
    assert ContourParameters.from_operation_parameters(autosaved_operation.parameters) == edited_parameters


def test_contour_ui_bind_invalid_draft_apply_generate_and_visibility(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config-ui")
    session = service.create_project_from_source(tmp_path, "Contour UI", source)
    descriptor, _geometry_input = _descriptor(source_id=session.manifest.source_files[0].source_id)
    displayed = []
    workspace = CamWorkspace(
        service, lambda: session.manifest.source_files[0].source_id,
        toolpath_display=displayed.append,
        contour_pick_provider=lambda: descriptor.reference,
        profile_resolver=lambda _reference: ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED, descriptor),
    )
    workspace.create_job(); workspace.create_setup(); workspace.create_basic_resources()
    workspace.add_contour_operation(); workspace.pick_geometry()
    before = service.cam_snapshot
    workspace.editor._contour_fields["stepdown"].setText("0")
    workspace.editor._submit()
    assert service.cam_snapshot == before and workspace.editor.error.text()
    workspace.editor._contour_fields["stepdown"].setText("1")
    workspace.editor._submit()
    assert workspace.actions["generate"].isEnabled()
    workspace.generate_selected()
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert operation.artifact_state.status is ArtifactStatus.VALID
    assert displayed and displayed[-1].source_operation_id == operation.operation_id
    old_reference = operation.geometry_inputs[0].reference
    workspace._contour_pick_provider = lambda: (_ for _ in ()).throw(ValueError("pick cancelled"))
    workspace.pick_geometry()
    assert workspace._picked_reference == old_reference
    workspace.clear_geometry_pick()
    cleared = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert cleared.geometry_inputs == () and cleared.artifact_state.status.value == "dirty"
    workspace.deleteLater()
