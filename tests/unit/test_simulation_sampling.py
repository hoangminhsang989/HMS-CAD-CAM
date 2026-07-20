"""Phase 7C.1 deterministic sampling and coordinate tests."""

import math

import pytest

from hms_cadcam.cam.domain import (
    AffineTransform, ArtifactState, ContentFingerprint, DependencyFingerprint, FeedRate,
    FeedUnit, LengthUnit, OperationId, Point3, Revision, SetupId, ToolAssemblyId,
    ToolpathArtifactId, Vector3, WcsFrame,
)
from hms_cadcam.cam.simulation import (
    SimulationIssueCode, SimulationSamplingError, SimulationSamplingPolicy,
    apply_affine_point, pose_to_world, sample_toolpath, wcs_to_world_axis,
)
from hms_cadcam.cam.toolpath import Pose, ToolpathBuilder


def _pose(x, y=0.0, z=0.0):
    return Pose(Point3(x, y, z, LengthUnit.MM), Vector3(0, 0, 1))


def _builder(initial=_pose(0)):
    inputs = DependencyFingerprint.from_payload({"input": 1})
    computing, token = ArtifactState().begin(inputs)
    builder = ToolpathBuilder(artifact_id=ToolpathArtifactId.new(), operation_id=OperationId.new(),
        operation_revision=Revision(0), computation_token=token, input_fingerprint=inputs,
        unit=LengthUnit.MM, setup_id=SetupId.new(), setup_revision=Revision(0),
        wcs_fingerprint=ContentFingerprint.from_payload({"wcs": 1}),
        tool_assembly_id=ToolAssemblyId.new(), tool_assembly_fingerprint=ContentFingerprint.from_payload({"tool": 1}))
    builder.set_initial_pose(initial)
    return builder


def test_line_sampling_uses_ceil_endpoints_and_deduplicates_junction_provenance():
    builder = _builder()
    builder.linear_to(_pose(2.1), FeedRate(100, FeedUnit.MM_PER_MINUTE))
    builder.linear_to(_pose(3.0), FeedRate(100, FeedUnit.MM_PER_MINUTE))
    output = sample_toolpath(artifact=builder.finalize(), wcs=WcsFrame.identity(LengthUnit.MM),
        policy=SimulationSamplingPolicy(max_linear_step=1.0))
    assert [sample.setup_pose.position.x for sample in output.samples] == pytest.approx([0, .7, 1.4, 2.1, 3.0])
    assert output.segments[0].sample_indices[-1] == output.segments[1].sample_indices[0]
    junction = output.samples[output.segments[0].sample_indices[-1]]
    assert {item.event_index for item in junction.provenance} == {0, 1}


def test_arc_sampling_honors_direction_chord_and_maximum_angle():
    builder = _builder(_pose(1, 0))
    builder.arc_to(_pose(0, 1), center=Point3(0, 0, 0, LengthUnit.MM),
        plane_normal=Vector3(0, 0, 1), sweep_radians=math.pi / 2,
        feed_rate=FeedRate(100, FeedUnit.MM_PER_MINUTE))
    output = sample_toolpath(artifact=builder.finalize(), wcs=WcsFrame.identity(LengthUnit.MM),
        policy=SimulationSamplingPolicy(chord_tolerance=.05, max_arc_angle=math.pi / 6))
    assert len(output.samples) >= 4
    assert output.samples[-1].setup_pose.position == _pose(0, 1).position
    assert all(sample.setup_pose.position.x >= -1e-9 and sample.setup_pose.position.y >= -1e-9 for sample in output.samples)


def test_dwell_marker_and_process_events_do_not_create_motion_samples():
    builder = _builder()
    builder.marker("simulation.marker")
    builder.dwell(1.0)
    output = sample_toolpath(artifact=builder.finalize(), wcs=WcsFrame.identity(LengthUnit.MM), policy=SimulationSamplingPolicy())
    assert len(output.samples) == 1 and output.segments == ()


def test_sample_limit_and_cancellation_fail_closed():
    builder = _builder(); builder.linear_to(_pose(10), FeedRate(100, FeedUnit.MM_PER_MINUTE))
    artifact = builder.finalize()
    with pytest.raises(SimulationSamplingError) as limit:
        sample_toolpath(artifact=artifact, wcs=WcsFrame.identity(LengthUnit.MM), policy=SimulationSamplingPolicy(max_linear_step=1, maximum_samples=5))
    assert limit.value.code is SimulationIssueCode.SAMPLE_LIMIT
    with pytest.raises(SimulationSamplingError) as cancelled:
        sample_toolpath(artifact=artifact, wcs=WcsFrame.identity(LengthUnit.MM), policy=SimulationSamplingPolicy(cancellation_check_interval=1), cancellation=lambda: True)
    assert cancelled.value.code is SimulationIssueCode.CANCELLED


def test_wcs_identity_translation_rotation_and_axis_excludes_origin():
    rotated = WcsFrame(Point3(10, 20, 30, LengthUnit.MM), Vector3(0, 1, 0), Vector3(-1, 0, 0), Vector3(0, 0, 1))
    world = pose_to_world(_pose(2, 3, 4), rotated)
    assert world.position == Point3(7, 22, 34, LengthUnit.MM)
    assert wcs_to_world_axis(Vector3(1, 0, 0), rotated) == Vector3(0, 1, 0)


def test_fixture_affine_is_applied_once_without_wcs_double_transform():
    transform = AffineTransform((1, 0, 0, 5, 0, 1, 0, 6, 0, 0, 1, 7, 0, 0, 0, 1), LengthUnit.MM)
    original = Point3(1, 2, 3, LengthUnit.MM)
    placed = apply_affine_point(original, transform)
    assert placed == Point3(6, 8, 10, LengthUnit.MM)
