"""Stage 7A.5 controller-neutral Toolpath IR tests."""

import dataclasses
import json
import math
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    ArtifactState, ArtifactStatus, CamInvariantError, CamNodeId, CamValidationError,
    ContentFingerprint, DependencyFingerprint, DirtyReason, FeedRate, FeedUnit, GeometryFingerprint,
    GeometryInputId, GeometryInputRole, GeometryReference, GeometryReferenceId,
    GeometryReferenceKind, GeometryRepresentationKind, LengthUnit, Operation,
    OperationFamily, OperationGeometryInput, OperationId, OperationParameterSet,
    Point3, Revision, SetupId, SpindleSpeed, ToolAssemblyId, ToolAssemblyReference,
    ToolpathArtifactId, ToolpathEventId, UnsupportedCamSchemaError, Vector3,
)
from hms_cadcam.cam.toolpath import (
    ArcMove, CoordinateSpace, CoolantState, FeedMode, LinearMove, MarkerEvent,
    MotionClass, Pose, RapidMove, SpindleState, ToolpathBuilder,
    ToolpathEventKind, artifact_from_dict, artifact_to_dict, event_from_dict,
    event_to_dict, pose_from_dict, pose_to_dict, publish_toolpath,
    validate_event_stream,
)


def _pose(x, y=0.0, z=0.0, axis=None):
    return Pose(Point3(x, y, z, LengthUnit.MM), axis or Vector3(0, 0, 1))


def _fingerprint(name):
    return ContentFingerprint.from_payload({"name": name})


def _builder(*, operation_id=None, operation_revision=Revision(0), state=None,
             input_fingerprint=None, artifact_id=None, created_at=None,
             setup_id=None, tool_assembly_id=None):
    fingerprint = input_fingerprint or DependencyFingerprint.from_payload({"input": 1})
    computing, token = (state or ArtifactState()).begin(fingerprint)
    builder = ToolpathBuilder(
        artifact_id=artifact_id or ToolpathArtifactId.new(),
        operation_id=operation_id or OperationId.new(), operation_revision=operation_revision,
        computation_token=token, input_fingerprint=fingerprint, unit=LengthUnit.MM,
        setup_id=setup_id or SetupId.new(), setup_revision=Revision(3), wcs_fingerprint=_fingerprint("wcs"),
        tool_assembly_id=tool_assembly_id or ToolAssemblyId.new(), tool_assembly_fingerprint=_fingerprint("tool"),
        created_at=created_at,
    )
    return builder, computing, token, fingerprint


def _operation(operation_id=None, revision=Revision(0), artifact_state=None):
    source_id = uuid4()
    reference = GeometryReference(
        GeometryReferenceId.new(), "hms_persistent_geometry", 1, source_id,
        GeometryReferenceKind.FACE, GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"face": 1}), Revision(1), subshape_selector="face:1",
    )
    geometry = OperationGeometryInput(GeometryInputId.new(), GeometryInputRole.DRIVE_GEOMETRY, reference)
    tool = ToolAssemblyReference(ToolAssemblyId.new(), Revision(1), _fingerprint("assembly"), LengthUnit.MM)
    return Operation(operation_id or OperationId.new(), CamNodeId.new(), OperationFamily.MILLING,
        SetupId.new(), tool, (geometry,), OperationParameterSet("mill.test", 1),
        revision=revision, artifact_state=artifact_state or ArtifactState())


def _event_common(operation_id, index, event_id=None, provenance="motion.test"):
    return dict(event_id=event_id or ToolpathEventId.new(), sequence_index=index,
                source_operation_id=operation_id, provenance=provenance)


def test_minimal_artifact_is_immutable_and_coordinate_space_round_trip():
    builder, *_ = _builder()
    builder.set_initial_pose(_pose(0))
    artifact = builder.finalize()

    assert artifact.coordinate_space is CoordinateSpace.SETUP_WCS
    assert artifact.events == () and artifact.bounds.minimum == _pose(0).position
    with pytest.raises(dataclasses.FrozenInstanceError):
        artifact.events = ()
    restored = artifact_from_dict(artifact_to_dict(artifact))
    assert restored == artifact


def test_finalize_twice_and_abort_never_publish_partial():
    builder, *_ = _builder()
    builder.set_initial_pose(_pose(0))
    builder.finalize()
    with pytest.raises(CamInvariantError):
        builder.finalize()

    aborted, *_ = _builder()
    aborted.set_initial_pose(_pose(0))
    aborted.marker("checkpoint")
    aborted.abort()
    with pytest.raises(CamInvariantError):
        aborted.finalize()


def test_deterministic_fingerprint_ignores_artifact_id_token_uuid_and_timestamp():
    operation_id = OperationId.new()
    setup_id = SetupId.new()
    tool_assembly_id = ToolAssemblyId.new()
    fingerprint = DependencyFingerprint.from_payload({"input": "same"})
    artifacts = []
    for timestamp in ("2026-01-01T00:00:00Z", "2027-02-02T00:00:00Z"):
        builder, *_ = _builder(operation_id=operation_id, input_fingerprint=fingerprint, created_at=timestamp,
                               setup_id=setup_id, tool_assembly_id=tool_assembly_id)
        builder.set_initial_pose(_pose(0))
        builder.linear_to(_pose(10), FeedRate(100, FeedUnit.MM_PER_MINUTE))
        artifacts.append(builder.finalize())
    assert artifacts[0].artifact_id != artifacts[1].artifact_id
    assert artifacts[0].computation_token.value != artifacts[1].computation_token.value
    assert artifacts[0].artifact_fingerprint == artifacts[1].artifact_fingerprint
    assert artifacts[0].events[0].event_id == artifacts[1].events[0].event_id


def test_inserted_event_changes_fingerprint_and_sequence_prevents_id_collision():
    operation_id, setup_id, tool_id = OperationId.new(), SetupId.new(), ToolAssemblyId.new()
    fingerprint = DependencyFingerprint.from_payload({"input": "sequence"})
    first, *_ = _builder(operation_id=operation_id, input_fingerprint=fingerprint,
                         setup_id=setup_id, tool_assembly_id=tool_id)
    first.set_initial_pose(_pose(0))
    first.marker("same_marker")
    one = first.finalize()
    second, *_ = _builder(operation_id=operation_id, input_fingerprint=fingerprint,
                          setup_id=setup_id, tool_assembly_id=tool_id)
    second.set_initial_pose(_pose(0))
    second.marker("same_marker")
    second.marker("same_marker")
    two = second.finalize()
    assert one.artifact_fingerprint != two.artifact_fingerprint
    assert two.events[0].event_id != two.events[1].event_id


def test_pose_normalizes_axis_and_rejects_zero_nan_or_infinity():
    assert _pose(0, axis=Vector3(0, 0, 5)).tool_axis == Vector3(0, 0, 1)
    assert pose_from_dict(pose_to_dict(_pose(1, 2, 3))) == _pose(1, 2, 3)
    with pytest.raises(CamValidationError):
        _pose(0, axis=Vector3(0, 0, 0))
    for value in (float("nan"), float("inf")):
        with pytest.raises(CamValidationError):
            _pose(value)


def test_rapid_linear_and_builder_current_pose_sequence():
    builder, *_ = _builder()
    builder.set_initial_pose(_pose(0))
    builder.rapid_to(_pose(5), rapid_rate=FeedRate(500, FeedUnit.MM_PER_MINUTE))
    assert builder.current_pose == _pose(5)
    builder.linear_to(_pose(10), FeedRate(100, FeedUnit.MM_PER_MINUTE))
    artifact = builder.finalize()
    assert tuple(item.kind for item in artifact.events) == (ToolpathEventKind.RAPID, ToolpathEventKind.LINEAR)
    assert tuple(item.sequence_index for item in artifact.events) == (0, 1)


def test_invalid_feed_zero_length_and_missing_initial_pose_are_rejected():
    builder, *_ = _builder()
    with pytest.raises(CamInvariantError):
        builder.finalize()
    builder.set_initial_pose(_pose(0))
    with pytest.raises(CamInvariantError):
        builder.rapid_to(_pose(0))
    with pytest.raises(Exception):
        builder.linear_to(_pose(1), FeedRate(0, FeedUnit.MM_PER_MINUTE))
    builder.marker("zero_length.semantic_marker")
    assert builder.finalize().events[0].kind is ToolpathEventKind.MARKER


def test_discontinuity_and_duplicate_event_id_are_diagnosed():
    operation_id = OperationId.new()
    duplicate = ToolpathEventId.new()
    first = RapidMove(**_event_common(operation_id, 0, duplicate), start=_pose(0), end=_pose(1))
    second = RapidMove(**_event_common(operation_id, 1, duplicate), start=_pose(2), end=_pose(3))
    codes = {item.code.value for item in validate_event_stream(_pose(0), (first, second))}
    assert codes == {"duplicate_event_id", "discontinuity"}


def test_builder_rejects_duplicate_prebuilt_event_id_without_partial_publish():
    operation_id = OperationId.new()
    builder, *_ = _builder(operation_id=operation_id)
    builder.set_initial_pose(_pose(0))
    duplicate = ToolpathEventId.new()
    first = MarkerEvent(**_event_common(operation_id, 0, duplicate, "semantic.marker"), semantic_key="first")
    second = MarkerEvent(**_event_common(operation_id, 1, duplicate, "semantic.marker"), semantic_key="second")
    builder.append_event(first)
    with pytest.raises(CamInvariantError):
        builder.append_event(second)
    artifact = builder.finalize()
    assert artifact.events == (first,)


@pytest.mark.parametrize(("sweep", "end"), ((math.pi / 2, (0, 1)), (-math.pi / 2, (0, -1)), (3 * math.pi / 2, (0, -1))))
def test_arc_signed_sweep_and_large_arc(sweep, end):
    operation_id = OperationId.new()
    arc = ArcMove(**_event_common(operation_id, 0), start=_pose(1, 0), end=_pose(*end),
        center=Point3(0, 0, 0, LengthUnit.MM), plane_normal=Vector3(0, 0, 1),
        sweep_radians=sweep, feed_rate=FeedRate(100, FeedUnit.MM_PER_MINUTE))
    assert math.isclose(arc.length, abs(sweep))


def test_arc_rejects_radius_mismatch_noncoplanar_zero_and_full_circle():
    common = _event_common(OperationId.new(), 0)
    kwargs = dict(start=_pose(1, 0), center=Point3(0, 0, 0, LengthUnit.MM),
        plane_normal=Vector3(0, 0, 1), feed_rate=FeedRate(100, FeedUnit.MM_PER_MINUTE))
    with pytest.raises(CamInvariantError):
        ArcMove(**common, **kwargs, end=_pose(0, 2), sweep_radians=math.pi / 2)
    with pytest.raises(CamInvariantError):
        ArcMove(**common, **kwargs, end=_pose(0, 1, 0.1), sweep_radians=math.pi / 2)
    with pytest.raises(CamInvariantError):
        ArcMove(**common, **kwargs, end=_pose(0, 1), sweep_radians=0)
    with pytest.raises(CamInvariantError):
        ArcMove(**common, **kwargs, end=_pose(1, 0), sweep_radians=math.tau)


def test_arc_bounds_include_all_crossed_quadrants_and_length():
    arc = ArcMove(**_event_common(OperationId.new(), 0), start=_pose(1, 0), end=_pose(0, -1),
        center=Point3(0, 0, 0, LengthUnit.MM), plane_normal=Vector3(0, 0, 1),
        sweep_radians=3 * math.pi / 2, feed_rate=FeedRate(60, FeedUnit.MM_PER_MINUTE))
    assert math.isclose(arc.bounds.minimum.x, -1.0, abs_tol=1e-9)
    assert math.isclose(arc.bounds.minimum.y, -1.0, abs_tol=1e-9)
    assert math.isclose(arc.bounds.maximum.x, 1.0, abs_tol=1e-9)
    assert math.isclose(arc.bounds.maximum.y, 1.0, abs_tol=1e-9)
    assert math.isclose(arc.length, 3 * math.pi / 2, abs_tol=1e-9)


def test_process_state_is_semantic_and_redundant_transition_rejected():
    builder, *_ = _builder()
    builder.set_initial_pose(_pose(0))
    builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE,
        spindle=SpindleState.CLOCKWISE, spindle_speed=SpindleSpeed(5000), coolant=CoolantState.FLOOD)
    with pytest.raises(CamInvariantError):
        builder.set_coolant(CoolantState.FLOOD)
    builder.dwell(2.5)
    artifact = builder.finalize()
    assert {event.kind for event in artifact.events} >= {
        ToolpathEventKind.FEED_MODE, ToolpathEventKind.SPINDLE_STATE,
        ToolpathEventKind.COOLANT_STATE, ToolpathEventKind.DWELL,
    }
    assert "gcode" not in json.dumps(artifact_to_dict(artifact)).lower()


def test_invalid_spindle_and_dwell_payloads_are_rejected():
    builder, *_ = _builder()
    builder.set_initial_pose(_pose(0))
    with pytest.raises(CamInvariantError):
        builder.set_spindle(SpindleState.OFF, SpindleSpeed(1000))
    with pytest.raises(CamValidationError):
        builder.dwell(0)


def test_statistics_lengths_dwell_duration_partial_and_bounds():
    builder, *_ = _builder()
    builder.set_initial_pose(_pose(0, 0))
    builder.rapid_to(_pose(3, 4))  # length 5, no rapid rate
    builder.linear_to(_pose(13, 4), FeedRate(60, FeedUnit.MM_PER_MINUTE), motion_class=MotionClass.CUTTING)
    builder.linear_to(_pose(13, 7), FeedRate(60, FeedUnit.MM_PER_MINUTE), motion_class=MotionClass.LINK)
    builder.linear_to(_pose(13, 9), FeedRate(60, FeedUnit.MM_PER_MINUTE), motion_class=MotionClass.RETRACT)
    builder.dwell(2)
    artifact = builder.finalize()
    stats = artifact.statistics
    assert (stats.total_rapid_length, stats.total_cutting_length, stats.total_link_length, stats.total_retract_length) == (5, 10, 3, 2)
    assert stats.duration_is_partial and math.isclose(stats.estimated_duration_seconds, 17.0)
    assert artifact.bounds.minimum == Point3(0, 0, 0, LengthUnit.MM)
    assert artifact.bounds.maximum == Point3(13, 9, 0, LengthUnit.MM)


def test_per_revolution_duration_requires_known_running_spindle_state():
    partial_builder, *_ = _builder()
    partial_builder.set_initial_pose(_pose(0))
    partial_builder.set_initial_process_state(
        feed_mode=FeedMode.UNITS_PER_REVOLUTION
    )
    partial_builder.linear_to(
        _pose(10), FeedRate(1, FeedUnit.MM_PER_REVOLUTION)
    )
    partial = partial_builder.finalize()
    assert partial.statistics.duration_is_partial
    assert partial.statistics.estimated_duration_seconds == 0.0

    known_builder, *_ = _builder()
    known_builder.set_initial_pose(_pose(0))
    known_builder.set_initial_process_state(
        feed_mode=FeedMode.UNITS_PER_REVOLUTION
    )
    known_builder.set_spindle(SpindleState.CLOCKWISE, SpindleSpeed(600))
    known_builder.linear_to(
        _pose(10), FeedRate(1, FeedUnit.MM_PER_REVOLUTION)
    )
    known = known_builder.finalize()
    assert not known.statistics.duration_is_partial
    assert math.isclose(known.statistics.estimated_duration_seconds, 1.0)
    assert artifact_from_dict(artifact_to_dict(known)) == known


def _candidate_for_operation(operation, computing, token, fingerprint, *, created_at=None):
    builder = ToolpathBuilder(artifact_id=ToolpathArtifactId.new(), operation_id=operation.operation_id,
        operation_revision=operation.revision, computation_token=token, input_fingerprint=fingerprint,
        unit=LengthUnit.MM, setup_id=operation.setup_id, setup_revision=Revision(0),
        wcs_fingerprint=_fingerprint("wcs"), tool_assembly_id=operation.tool_assembly.assembly_id,
        tool_assembly_fingerprint=operation.tool_assembly.expected_fingerprint, created_at=created_at)
    builder.set_initial_pose(_pose(0))
    builder.linear_to(_pose(1), FeedRate(100, FeedUnit.MM_PER_MINUTE))
    return builder.finalize(), dataclasses.replace(operation, artifact_state=computing)


def test_current_token_publish_success_transitions_valid():
    operation = _operation()
    fingerprint = DependencyFingerprint.from_payload({"publish": 1})
    computing, token = operation.artifact_state.begin(fingerprint)
    candidate, operation = _candidate_for_operation(operation, computing, token, fingerprint)
    result = publish_toolpath(operation, candidate, token, fingerprint)
    assert result.accepted and result.artifact == candidate
    assert result.operation.artifact_state.status is ArtifactStatus.VALID


def test_old_token_changed_input_and_deleted_operation_cannot_publish():
    operation = _operation()
    fingerprint = DependencyFingerprint.from_payload({"publish": 1})
    computing, token = operation.artifact_state.begin(fingerprint)
    candidate, operation = _candidate_for_operation(operation, computing, token, fingerprint)
    old = dataclasses.replace(token, generation=token.generation + 1)
    stale = publish_toolpath(operation, candidate, old, fingerprint)
    assert not stale.accepted and stale.operation == operation
    changed = publish_toolpath(operation, candidate, token, DependencyFingerprint.from_payload({"publish": 2}))
    assert not changed.accepted and changed.operation.artifact_state.status is ArtifactStatus.DIRTY
    deleted = publish_toolpath(operation, candidate, token, fingerprint, operation_exists=False)
    assert not deleted.accepted and deleted.artifact is None


def test_old_candidate_does_not_overwrite_newer_computation_state():
    operation = _operation()
    fingerprint = DependencyFingerprint.from_payload({"publish": 1})
    first_state, first_token = operation.artifact_state.begin(fingerprint)
    candidate, _ = _candidate_for_operation(operation, first_state, first_token, fingerprint)
    dirty = first_state.mark_dirty(DirtyReason.UPSTREAM_CHANGED)
    newer, _new_token = dirty.begin(fingerprint)
    current = dataclasses.replace(operation, artifact_state=newer)
    result = publish_toolpath(current, candidate, first_token, fingerprint)
    assert not result.accepted and result.operation == current


def test_codec_round_trip_tamper_future_unknown_kind_and_event_order():
    builder, *_ = _builder()
    builder.set_initial_pose(_pose(0))
    builder.marker("first")
    builder.rapid_to(_pose(1))
    artifact = builder.finalize()
    payload = artifact_to_dict(artifact)
    restored = artifact_from_dict(payload)
    assert restored == artifact
    assert tuple(item.kind for item in restored.events) == (ToolpathEventKind.MARKER, ToolpathEventKind.RAPID)

    tampered = artifact_to_dict(artifact)
    tampered["events"][1]["end"]["position"]["x"] = 2
    with pytest.raises(CamInvariantError):
        artifact_from_dict(tampered)
    fingerprint_tamper = artifact_to_dict(artifact)
    fingerprint_tamper["artifact_fingerprint"] = _fingerprint("forged").to_dict()
    with pytest.raises(CamInvariantError):
        artifact_from_dict(fingerprint_tamper)
    future = artifact_to_dict(artifact)
    future["format_version"] = 2
    with pytest.raises(UnsupportedCamSchemaError):
        artifact_from_dict(future)
    unknown = event_to_dict(artifact.events[0])
    unknown["kind"] = "controller_block"
    with pytest.raises(UnsupportedCamSchemaError):
        event_from_dict(unknown)


def test_malformed_nested_event_and_validation_size_limit_are_atomic():
    builder, *_ = _builder()
    builder.set_initial_pose(_pose(0))
    builder.marker("one")
    artifact = builder.finalize()
    payload = artifact_to_dict(artifact)
    payload["events"][0].pop("semantic_key")
    with pytest.raises(CamValidationError):
        artifact_from_dict(payload)
    with pytest.raises(CamValidationError):
        artifact_from_dict(artifact_to_dict(artifact), max_events=0)


def test_public_model_contains_no_native_or_controller_syntax_types():
    builder, *_ = _builder()
    builder.set_initial_pose(_pose(0))
    builder.linear_to(_pose(1), FeedRate(100, FeedUnit.MM_PER_MINUTE))
    artifact = builder.finalize()

    def walk(value):
        yield value
        if dataclasses.is_dataclass(value):
            for field in dataclasses.fields(value):
                yield from walk(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                yield from walk(item)

    assert all(not type(item).__module__.startswith(("OCP", "PySide6")) for item in walk(artifact))
    payload_text = json.dumps(artifact_to_dict(artifact)).lower()
    assert all(token not in payload_text for token in ('"g0"', '"g1"', '"g2"', '"g3"', '"mcode"'))
