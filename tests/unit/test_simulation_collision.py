"""Phase 7C.1 tool-envelope and collision-policy tests."""

import pytest

from hms_cadcam.cam.domain import (
    ContentFingerprint, CylindricalGeometry, DependencyFingerprint, FeedRate,
    FixtureInstanceId, HolderDefinition, HolderDefinitionId, HolderSection, Length,
    LengthUnit, OperationId, Point3, Revision, SetupId, ShankGeometry,
    SimulationRequestId, SimulationResultId, ToolAssembly, ToolAssemblyId,
    ToolDefinition, ToolDefinitionId, ToolFamily, ToolpathArtifactId, Vector3,
)
from hms_cadcam.cam.toolpath import MotionClass, Pose, SpindleState
from hms_cadcam.cam.toolpath.events import CoolantState
from hms_cadcam.cam.toolpath.geometry import Bounds3
from hms_cadcam.cam.simulation import (
    CollisionScene, CollisionTarget, CollisionTargetKind, EnvelopePrimitive,
    EnvelopePrimitiveKind, EnvelopeSupport, InMemoryAabbBackend, SampledSegment,
    SamplingOutput, SimulationIssueCategory, SimulationRequest, SimulationSample,
    SimulationSamplingPolicy, SimulationStatus, ToolEnvelope, build_tool_envelope,
    run_collision_analysis,
)


def _mm(value):
    return Length(value, LengthUnit.MM)


def _fp(name):
    return ContentFingerprint.from_payload({"name": name})


def _request(safe_height=5.0):
    return SimulationRequest(SimulationRequestId.new(), OperationId.new(), Revision(0),
        ToolpathArtifactId.new(), _fp("artifact"), DependencyFingerprint.from_payload({"input": 1}),
        SetupId.new(), Revision(0), _fp("wcs"), _fp("stock"), (), ToolAssemblyId.new(),
        _fp("assembly"), ToolDefinitionId.new(), _fp("tool"), HolderDefinitionId.new(),
        _fp("holder"), None, None, LengthUnit.MM, SimulationSamplingPolicy(), safe_height)


def _tooling():
    tool = ToolDefinition(ToolDefinitionId.new(), "End mill", ToolFamily.END_MILL, LengthUnit.MM,
        CylindricalGeometry(_mm(10), _mm(20)), _mm(100), _mm(30), ShankGeometry(_mm(10), _mm(70)), Revision(2))
    holder = HolderDefinition(HolderDefinitionId.new(), "Holder", LengthUnit.MM,
        (HolderSection(_mm(0), _mm(40), _mm(30), _mm(40)),), _mm(0), Revision(3))
    assembly = ToolAssembly.create(ToolAssemblyId.new(), "Assembly", tool, _mm(40), _mm(80), holder)
    return tool, holder, assembly


def _sampling(motion=MotionClass.NON_CUTTING, spindle=SpindleState.OFF, z=0.0):
    pose = Pose(Point3(0, 0, z, LengthUnit.MM), Vector3(0, 0, 1))
    sample = SimulationSample(0, pose, pose, ())
    segment = SampledSegment(0, "event:1", "rapid", motion, spindle, CoolantState.OFF, (0,))
    return SamplingOutput((sample,), (segment,))


def _envelope():
    cutter = EnvelopePrimitive(EnvelopePrimitiveKind.CYLINDER, 0, 2, 1, 1, LengthUnit.MM, "cutter", EnvelopeSupport.EXACT)
    return ToolEnvelope((cutter,), (), (), LengthUnit.MM, EnvelopeSupport.EXACT)


def _artifact_stub(request):
    class Artifact:
        unit = LengthUnit.MM
    return Artifact()


def test_tool_envelope_includes_cutter_shank_and_holder_without_defaults():
    tool, holder, assembly = _tooling()
    envelope = build_tool_envelope(tool=tool, assembly=assembly, holder=holder)
    assert envelope.cutter and envelope.shank and envelope.holder
    assert envelope.cutter[0].lower_radius == 5
    with pytest.raises(Exception):
        build_tool_envelope(tool=tool, assembly=assembly, holder=None)


def test_cutting_cutter_stock_is_allowed_but_fixture_is_collision():
    request = _request()
    target_bounds = Bounds3(Point3(-2, -2, -2, LengthUnit.MM), Point3(2, 2, 3, LengthUnit.MM))
    stock = CollisionTarget("stock", CollisionTargetKind.STOCK, target_bounds)
    fixture = CollisionTarget("fixture:1", CollisionTargetKind.FIXTURE, target_bounds)
    result = run_collision_analysis(request=request, artifact=_artifact_stub(request), sampling=_sampling(MotionClass.CUTTING),
        envelope=_envelope(), scene=CollisionScene(stock, (fixture,)), backend=InMemoryAabbBackend(), result_id=SimulationResultId.new())
    assert result.status is SimulationStatus.FAIL
    assert {issue.code.value for issue in result.issues} == {"sim.tool_fixture_collision"}


@pytest.mark.parametrize(("spindle", "category"), ((SpindleState.OFF, SimulationIssueCategory.COLLISION), (SpindleState.CLOCKWISE, SimulationIssueCategory.GOUGE)))
def test_noncutting_cutter_stock_uses_process_state(spindle, category):
    request = _request()
    bounds = Bounds3(Point3(-2, -2, -2, LengthUnit.MM), Point3(2, 2, 3, LengthUnit.MM))
    result = run_collision_analysis(request=request, artifact=_artifact_stub(request), sampling=_sampling(spindle=spindle), envelope=_envelope(), scene=CollisionScene(CollisionTarget("stock", CollisionTargetKind.STOCK, bounds)), backend=InMemoryAabbBackend(), result_id=SimulationResultId.new())
    assert any(issue.category is category for issue in result.issues)
    assert result.status is SimulationStatus.FAIL


def test_rapid_below_safe_plane_is_warning_when_scene_is_clear():
    request = _request(safe_height=5)
    far = Bounds3(Point3(100, 100, 100, LengthUnit.MM), Point3(110, 110, 110, LengthUnit.MM))
    result = run_collision_analysis(request=request, artifact=_artifact_stub(request), sampling=_sampling(z=2), envelope=_envelope(), scene=CollisionScene(CollisionTarget("stock", CollisionTargetKind.STOCK, far)), backend=InMemoryAabbBackend(), result_id=SimulationResultId.new())
    assert result.status is SimulationStatus.WARN
    assert result.issues[0].code.value == "sim.rapid_below_safe"


def test_broad_overlap_without_narrow_proof_is_clearance_warning():
    class BroadOnly:
        def broad_overlap(self, target, candidate): return True
        def narrow_intersects(self, target, primitive, pose, tolerance): return None
    request = _request(safe_height=None)
    bounds = Bounds3(Point3(-2, -2, -2, LengthUnit.MM), Point3(2, 2, 3, LengthUnit.MM))
    result = run_collision_analysis(request=request, artifact=_artifact_stub(request), sampling=_sampling(), envelope=_envelope(), scene=CollisionScene(CollisionTarget("stock", CollisionTargetKind.STOCK, bounds)), backend=BroadOnly(), result_id=SimulationResultId.new())
    assert result.status is SimulationStatus.WARN
    assert result.issues[0].category is SimulationIssueCategory.CLEARANCE_WARNING
