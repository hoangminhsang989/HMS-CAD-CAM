"""Pocket 7B.5.3 presentation and stale-display lifecycle regressions."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ComputationToken,
    ContentFingerprint,
    DependencyFingerprint,
    FeedRate,
    FeedUnit,
    LengthUnit,
    OperationId,
    Point3,
    Revision,
    SetupId,
    ToolAssemblyId,
    ToolpathArtifactId,
    Vector3,
)
from hms_cadcam.cam.toolpath import FeedMode, MotionClass, Pose, ToolpathBuilder
from hms_cadcam.viewer.ocp import backend as ocp_backend_module
from hms_cadcam.viewer.toolpath import (
    ToolpathPresentation,
    ToolpathPresentationRegistry,
)


def _pocket_artifact():
    unit = LengthUnit.MM
    operation_id = OperationId.new()
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(),
        operation_id=operation_id,
        operation_revision=Revision(0),
        computation_token=ComputationToken(uuid4(), 1),
        input_fingerprint=DependencyFingerprint.from_payload({"pocket": "viewer"}),
        unit=unit,
        setup_id=SetupId.new(),
        setup_revision=Revision(0),
        wcs_fingerprint=ContentFingerprint.from_payload({"wcs": 0}),
        tool_assembly_id=ToolAssemblyId.new(),
        tool_assembly_fingerprint=ContentFingerprint.from_payload({"tool": 1}),
    )
    axis = Vector3(0, 0, 1)
    builder.set_initial_pose(Pose(Point3(0, 0, 5, unit), axis))
    builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
    builder.rapid_to(Pose(Point3(5, 5, 5, unit), axis),
                     provenance="pocket.depth.0.loop.0.position")
    builder.linear_to(Pose(Point3(5, 5, -1, unit), axis),
                      FeedRate(100, FeedUnit.MM_PER_MINUTE),
                      motion_class=MotionClass.LINK,
                      provenance="pocket.depth.0.loop.0.plunge")
    builder.linear_to(Pose(Point3(15, 5, -1, unit), axis),
                      FeedRate(500, FeedUnit.MM_PER_MINUTE),
                      provenance="pocket.depth.0.loop.0.segment.0.cut")
    builder.linear_to(Pose(Point3(5, 5, -1, unit), axis),
                      FeedRate(500, FeedUnit.MM_PER_MINUTE),
                      provenance="pocket.depth.0.loop.0.segment.1.cut")
    builder.linear_to(Pose(Point3(5, 5, 2, unit), axis),
                      FeedRate(100, FeedUnit.MM_PER_MINUTE),
                      motion_class=MotionClass.RETRACT,
                      provenance="pocket.depth.0.loop.0.retract")
    return builder.finalize()


def test_pocket_presentation_exposes_distinct_motion_and_metadata() -> None:
    artifact = _pocket_artifact()
    registry = ToolpathPresentationRegistry()
    registry.bind_project(11)

    assert registry.display(artifact, generation=11)
    presentation = registry.presentations[0]
    assert {segment.semantic for segment in presentation.segments} == {
        "rapid", "plunge", "pocket_cutting", "retract",
    }
    assert presentation.operation_id == artifact.source_operation_id
    assert presentation.artifact_id == artifact.artifact_id
    assert presentation.strategy_key == "pocket_2_5d"
    assert presentation.pass_count == 1
    assert presentation.bounds == artifact.bounds
    assert presentation.artifact_status is ArtifactStatus.VALID

    registry.set_visible(artifact.source_operation_id, False)
    assert not registry.presentations[0].visible
    registry.set_visible(artifact.source_operation_id, True)
    assert registry.presentations[0].visible


def test_replace_is_atomic_and_stale_callback_cannot_change_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _pocket_artifact()
    registry = ToolpathPresentationRegistry()
    registry.bind_project(3)
    assert registry.display(artifact, generation=3)
    original = registry.presentations[0]

    stale = registry.request_display(artifact.source_operation_id, generation=3)
    current = registry.request_display(artifact.source_operation_id, generation=3)
    assert stale is not None and current is not None
    assert not registry.display(artifact, generation=3, request=stale)
    assert registry.presentations[0] is original

    def fail_conversion(_cls, _artifact):
        raise RuntimeError("injected presentation failure")

    monkeypatch.setattr(ToolpathPresentation, "from_artifact", classmethod(fail_conversion))
    with pytest.raises(RuntimeError, match="injected presentation failure"):
        registry.display(artifact, generation=3, request=current)
    assert registry.presentations[0] is original


def test_clear_remove_and_project_rebind_invalidate_pending_callbacks() -> None:
    first, second = _pocket_artifact(), _pocket_artifact()
    registry = ToolpathPresentationRegistry()
    registry.bind_project(4)
    assert registry.display(first, generation=4)
    assert registry.display(second, generation=4)
    pending = registry.request_display(first.source_operation_id, generation=4)
    assert pending is not None

    registry.remove(first.source_operation_id)
    assert tuple(item.operation_id for item in registry.presentations) == (
        second.source_operation_id,
    )
    assert not registry.display(first, generation=4, request=pending)
    registry.clear()
    assert registry.presentations == ()
    registry.bind_project(5)
    assert not registry.display(second, generation=4)


def test_ocp_registry_keeps_other_operation_and_removes_exact_identity(monkeypatch) -> None:
    first, second = _pocket_artifact(), _pocket_artifact()

    class Context:
        def __init__(self) -> None:
            self.removed = []

        def SetColor(self, *_args) -> None:
            return None

        def Display(self, *_args) -> None:
            return None

        def Erase(self, *_args) -> None:
            return None

        def Remove(self, presentation, _update) -> None:
            self.removed.append(presentation)

        def UpdateCurrentViewer(self) -> None:
            return None

    class Builder:
        def MakeCompound(self, _compound) -> None:
            return None

        def Add(self, _compound, _edge) -> None:
            return None

    context = Context()
    backend = object.__new__(ocp_backend_module.OcpCadViewportBackend)
    backend._lifecycle = SimpleNamespace(initialized=True, context=context)
    backend._toolpaths = {}
    monkeypatch.setattr(ocp_backend_module, "TopoDS_Compound", object)
    monkeypatch.setattr(ocp_backend_module, "BRep_Builder", Builder)
    monkeypatch.setattr(
        ocp_backend_module,
        "BRepBuilderAPI_MakeEdge",
        lambda *_args: SimpleNamespace(Edge=lambda: object()),
    )
    monkeypatch.setattr(ocp_backend_module, "AIS_Shape", lambda _shape: object())
    monkeypatch.setattr(ocp_backend_module, "Quantity_Color", lambda *_args: object())

    backend.display_toolpath(first)
    first_presentations = backend._toolpaths[first.source_operation_id]
    backend.display_toolpath(second)
    assert set(backend._toolpaths) == {
        first.source_operation_id,
        second.source_operation_id,
    }
    assert {item.operation_id for item in backend.get_toolpath_presentations()} == {
        first.source_operation_id,
        second.source_operation_id,
    }
    backend.set_toolpath_visibility(second.source_operation_id, False)
    assert not next(item for item in backend.get_toolpath_presentations()
                    if item.operation_id == second.source_operation_id).visible
    backend.set_toolpath_visibility(second.source_operation_id, True)
    assert next(item for item in backend.get_toolpath_presentations()
                if item.operation_id == second.source_operation_id).visible
    backend.remove_toolpath(first.source_operation_id)
    assert set(backend._toolpaths) == {second.source_operation_id}
    assert tuple(item.operation_id for item in backend.get_toolpath_presentations()) == (
        second.source_operation_id,
    )
    assert all(value in context.removed for value in first_presentations)
