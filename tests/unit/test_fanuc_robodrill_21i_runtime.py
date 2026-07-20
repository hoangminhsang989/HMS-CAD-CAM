from dataclasses import replace

from hms_cadcam.cam.domain import (
    AffineTransform,
    ArtifactStatus,
    FeedRate,
    FeedUnit,
    KinematicChain,
    KinematicMount,
    KinematicNode,
    KinematicSide,
    Length,
    LengthUnit,
    MachineAxis,
    MachineAxisType,
    MachineCapabilities,
    MachineCoolantCapability,
    MachineDefinition,
    MachineDefinitionId,
    MachineKind,
    OperationCapability,
    Point3,
    SpindleCapability,
    SpindleDirection,
    SpindleSpeed,
    ToolpathArtifactId,
    Vector3,
    WorkEnvelope,
)
from hms_cadcam.cam.post import (
    PostRequest,
    PostResultStatus,
    PostRuntimeService,
    SimulationGateMode,
    SimulationGatePolicy,
    robodrill_21i_definition,
)
from hms_cadcam.cam.post.codec import dumps, loads
from hms_cadcam.cam.toolpath import FeedMode, Pose, SpindleState, ToolpathBuilder
from tests.unit._fanuc_fixtures import fixture_context
from tests.unit._post_fixtures import source_snapshot


def _robodrill_machine() -> MachineDefinition:
    unit = LengthUnit.MM
    axes = tuple(
        MachineAxis(
            name,
            f"linear_{name.casefold()}",
            MachineAxisType.LINEAR,
            direction,
            Length(-500.0, unit),
            Length(500.0, unit),
            Length(0.0, unit),
        )
        for name, direction in (
            ("X", Vector3(1.0, 0.0, 0.0)),
            ("Y", Vector3(0.0, 1.0, 0.0)),
            ("Z", Vector3(0.0, 0.0, 1.0)),
        )
    )
    transform = AffineTransform.identity(unit)
    kinematics = KinematicChain(
        (
            KinematicNode("base", None, None, KinematicSide.FIXED, KinematicMount.NONE, transform),
            KinematicNode("axis_x", "base", "X", KinematicSide.WORKPIECE, KinematicMount.NONE, transform),
            KinematicNode("axis_y", "axis_x", "Y", KinematicSide.WORKPIECE, KinematicMount.NONE, transform),
            KinematicNode("axis_z", "axis_y", "Z", KinematicSide.TOOL, KinematicMount.SPINDLE, transform),
        )
    )
    return MachineDefinition(
        machine_id=MachineDefinitionId.new(),
        name="FANUC ROBODRILL 21i test contract",
        kind=MachineKind.MILL,
        unit=unit,
        axes=axes,
        spindles=(
            SpindleCapability(
                "main",
                SpindleSpeed(100.0),
                SpindleSpeed(10000.0),
                directions=(SpindleDirection.CLOCKWISE, SpindleDirection.COUNTERCLOCKWISE),
            ),
        ),
        capabilities=MachineCapabilities(
            milling=True,
            turning=False,
            live_tooling=False,
            probing=False,
            tapping=False,
            threading=False,
            spindle_count=1,
            maximum_feed=FeedRate(5000.0, FeedUnit.MM_PER_MINUTE),
            maximum_rapid=FeedRate(10000.0, FeedUnit.MM_PER_MINUTE),
            tool_capacity=21,
            coolant=(MachineCoolantCapability.FLOOD,),
            operations=(OperationCapability.MILLING,),
        ),
        kinematic_chain=kinematics,
        work_envelope=WorkEnvelope(Length(500.0, unit), Length(400.0, unit), Length(300.0, unit)),
        manufacturer="FANUC",
        model="ROBODRILL 21i",
    )


def _runtime_source():
    source = source_snapshot(with_motion=False)
    machine = _robodrill_machine()
    input_fingerprint = source.artifact.input_fingerprint
    dirty_state = source.operation.artifact_state.transition(ArtifactStatus.DIRTY)
    computing_state, token = dirty_state.begin(input_fingerprint)
    operation = replace(source.operation, artifact_state=computing_state)
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(),
        operation_id=operation.operation_id,
        operation_revision=operation.revision,
        computation_token=token,
        input_fingerprint=input_fingerprint,
        unit=LengthUnit.MM,
        setup_id=source.setup.setup_id,
        setup_revision=source.setup.revision,
        wcs_fingerprint=source.artifact.wcs_fingerprint,
        tool_assembly_id=source.assembly.assembly_id,
        tool_assembly_fingerprint=source.assembly.content_fingerprint,
        machine_id=machine.machine_id,
        machine_fingerprint=machine.content_fingerprint,
    )
    pose = lambda x, y, z: Pose(Point3(x, y, z, LengthUnit.MM), Vector3(0.0, 0.0, 1.0))
    builder.set_initial_pose(pose(0.0, 0.0, 10.0))
    builder.set_feed_mode(FeedMode.UNITS_PER_MINUTE)
    builder.set_spindle(SpindleState.CLOCKWISE, SpindleSpeed(4000.0))
    builder.rapid_to(pose(10.0, 0.0, 10.0), rapid_rate=FeedRate(1000.0, FeedUnit.MM_PER_MINUTE))
    builder.linear_to(pose(10.0, 0.0, 0.0), FeedRate(100.0, FeedUnit.MM_PER_MINUTE))
    builder.rapid_to(pose(10.0, 0.0, 10.0), rapid_rate=FeedRate(1000.0, FeedUnit.MM_PER_MINUTE))
    builder.set_spindle(SpindleState.OFF)
    artifact = builder.finalize()
    published_state, accepted = computing_state.publish(
        token,
        input_fingerprint,
        artifact.artifact_fingerprint,
        enabled=True,
    )
    assert accepted
    operation = replace(operation, artifact_state=published_state)
    return replace(source, operation=operation, artifact=artifact, machine=machine)


def test_runtime_auto_resolves_fanuc_adapter_and_publishes_production_provenance():
    source = _runtime_source()
    definition = robodrill_21i_definition()
    context = fixture_context(source, file_name="runtime_facing.fn")
    request = PostRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        definition,
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
        program_context=context,
    )

    execution = PostRuntimeService().post(request, source)

    assert execution.accepted
    assert execution.status is PostResultStatus.PUBLISHED
    assert execution.result is not None
    assert execution.result.canonical_text.endswith("\r\n")
    assert execution.result.production_profile_id == definition.production_profile.profile_id
    assert execution.result.production_profile_fingerprint == definition.production_profile.fingerprint
    assert execution.result.tool_binding_fingerprint == context.tool_binding.fingerprint
    assert execution.result.program_context_fingerprint == context.fingerprint
    assert execution.result.validated_unit is LengthUnit.MM
    assert execution.result.validated_feed_modes == (FeedMode.UNITS_PER_MINUTE,)
    restored = loads(dumps(execution.result))
    assert restored.result_fingerprint == execution.result.result_fingerprint
    assert restored.production_profile_fingerprint == definition.production_profile.fingerprint
