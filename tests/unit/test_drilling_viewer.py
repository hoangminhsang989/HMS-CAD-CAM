"""Stage 7B.6.3 drilling presentation and recompute-viewer lifecycle tests."""

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


def _drilling_artifact(
    cycle: str,
    *,
    hole_count: int = 1,
    operation_id: OperationId | None = None,
    generation: int = 1,
):
    unit = LengthUnit.MM
    operation_id = operation_id or OperationId.new()
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(),
        operation_id=operation_id,
        operation_revision=Revision(0),
        computation_token=ComputationToken(uuid4(), generation),
        input_fingerprint=DependencyFingerprint.from_payload({
            "cycle": cycle,
            "generation": generation,
            "holes": hole_count,
        }),
        unit=unit,
        setup_id=SetupId.new(),
        setup_revision=Revision(0),
        wcs_fingerprint=ContentFingerprint.from_payload({"wcs": 0}),
        tool_assembly_id=ToolAssemblyId.new(),
        tool_assembly_fingerprint=ContentFingerprint.from_payload({"tool": cycle}),
    )
    axis = Vector3(0, 0, 1)
    feed = FeedRate(120, FeedUnit.MM_PER_MINUTE)
    rapid = FeedRate(1500, FeedUnit.MM_PER_MINUTE)
    builder.set_initial_pose(Pose(Point3(0, 0, 8, unit), axis))
    builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
    depths = (-2.0, -4.0, -5.0) if cycle == "peck_drill" else (-5.0,)
    for hole_index in range(hole_count):
        x = float(hole_index * 10)
        clearance = Pose(Point3(x, 0, 8, unit), axis)
        if builder.current_pose != clearance:
            builder.rapid_to(
                clearance,
                rapid_rate=rapid,
                provenance=f"drill.hole.{hole_index}.rapid",
            )
        builder.linear_to(
            Pose(Point3(x, 0, 3, unit), axis),
            feed,
            motion_class=MotionClass.LINK,
            provenance=f"drill.hole.{hole_index}.approach",
        )
        previous_depth = 0.0
        for peck_index, target_depth in enumerate(depths):
            if peck_index:
                builder.linear_to(
                    Pose(Point3(x, 0, previous_depth, unit), axis),
                    feed,
                    motion_class=MotionClass.LINK,
                    provenance=(
                        f"drill.hole.{hole_index}.peck.{peck_index}.resume"
                    ),
                )
            builder.linear_to(
                Pose(Point3(x, 0, target_depth, unit), axis),
                feed,
                motion_class=MotionClass.CUTTING,
                provenance=f"drill.hole.{hole_index}.peck.{peck_index}.plunge",
            )
            if cycle == "spot_drill" and peck_index == len(depths) - 1:
                builder.dwell(
                    0.25,
                    provenance=f"drill.hole.{hole_index}.dwell",
                )
            builder.linear_to(
                Pose(Point3(x, 0, 3, unit), axis),
                feed,
                motion_class=MotionClass.RETRACT,
                provenance=f"drill.hole.{hole_index}.peck.{peck_index}.retract",
            )
            previous_depth = target_depth
        builder.rapid_to(
            clearance,
            rapid_rate=rapid,
            provenance=f"drill.hole.{hole_index}.clearance",
        )
        builder.marker(
            "drill.hole_complete",
            provenance=f"drill.hole.{hole_index}.complete",
        )
    return builder.finalize()


@pytest.mark.parametrize(
    ("cycle", "expected_semantics", "dwell_count"),
    (
        ("spot_drill", {"rapid", "approach", "plunge", "retract"}, 1),
        ("drill", {"rapid", "approach", "plunge", "retract"}, 0),
        (
            "peck_drill",
            {"rapid", "approach", "peck_resume", "plunge", "retract"},
            0,
        ),
    ),
)
def test_drilling_presentation_exposes_cycle_semantics_and_annotations(
    cycle: str,
    expected_semantics: set[str],
    dwell_count: int,
) -> None:
    artifact = _drilling_artifact(cycle)
    presentation = ToolpathPresentation.from_artifact(artifact)

    assert presentation.strategy_key == "drilling_v1"
    assert presentation.pass_count == 1
    assert {segment.semantic for segment in presentation.segments} == expected_semantics
    dwell = tuple(
        item for item in presentation.annotations if item.semantic == "dwell"
    )
    complete = tuple(
        item for item in presentation.annotations
        if item.semantic == "hole_complete"
    )
    assert len(dwell) == dwell_count
    assert all(item.position.z == -5 and item.duration_seconds == 0.25 for item in dwell)
    assert len(complete) == 1
    assert complete[0].position == Point3(0, 0, 8, LengthUnit.MM)
    assert complete[0].duration_seconds is None
    assert presentation.artifact_status is ArtifactStatus.VALID


def test_multi_hole_pass_count_uses_completion_markers_not_peck_plunges() -> None:
    presentation = ToolpathPresentation.from_artifact(
        _drilling_artifact("peck_drill", hole_count=3)
    )

    assert presentation.pass_count == 3
    assert sum(
        segment.semantic == "plunge" for segment in presentation.segments
    ) == 9
    complete = tuple(
        item for item in presentation.annotations
        if item.semantic == "hole_complete"
    )
    assert tuple(item.position.x for item in complete) == (0.0, 10.0, 20.0)


def test_stale_callback_and_project_generation_cannot_replace_other_operations() -> None:
    operation_id = OperationId.new()
    first = _drilling_artifact("drill", operation_id=operation_id, generation=1)
    replacement = _drilling_artifact(
        "peck_drill", operation_id=operation_id, generation=2
    )
    other = _drilling_artifact("spot_drill")
    registry = ToolpathPresentationRegistry()
    registry.bind_project(7)
    assert registry.display(first, generation=7)
    assert registry.display(other, generation=7)
    before = {item.operation_id: item for item in registry.presentations}

    stale = registry.request_display(operation_id, generation=7)
    current = registry.request_display(operation_id, generation=7)
    assert stale is not None and current is not None
    assert not registry.display(replacement, generation=7, request=stale)
    assert {item.operation_id: item for item in registry.presentations} == before
    assert registry.display(replacement, generation=7, request=current)
    changed = {item.operation_id: item for item in registry.presentations}
    assert changed[operation_id].artifact_id == replacement.artifact_id
    assert changed[other.source_operation_id] is before[other.source_operation_id]

    registry.set_visible(other.source_operation_id, False)
    assert not next(
        item for item in registry.presentations
        if item.operation_id == other.source_operation_id
    ).visible
    registry.remove(operation_id)
    assert tuple(item.operation_id for item in registry.presentations) == (
        other.source_operation_id,
    )
    registry.bind_project(8)
    assert registry.presentations == ()
    assert not registry.display(other, generation=7)


class _Context:
    def __init__(self) -> None:
        self.displayed: set[object] = set()
        self.removed: list[object] = []
        self.fail_display = False
        self.fail_remove: set[object] = set()

    def SetColor(self, *_args) -> None:
        return None

    def Display(self, presentation, *_args) -> None:
        if self.fail_display:
            raise RuntimeError("injected display failure")
        self.displayed.add(presentation)

    def Erase(self, presentation, *_args) -> None:
        self.displayed.discard(presentation)

    def Remove(self, presentation, *_args) -> None:
        if presentation in self.fail_remove:
            self.fail_remove.remove(presentation)
            raise RuntimeError("injected remove failure")
        self.displayed.discard(presentation)
        self.removed.append(presentation)

    def UpdateCurrentViewer(self) -> None:
        return None


class _Builder:
    def MakeCompound(self, _compound) -> None:
        return None

    def Add(self, _compound, _shape) -> None:
        return None


def _ocp_backend(monkeypatch: pytest.MonkeyPatch):
    context = _Context()
    backend = object.__new__(ocp_backend_module.OcpCadViewportBackend)
    backend._lifecycle = SimpleNamespace(initialized=True, context=context)
    backend._toolpaths = {}
    backend._toolpath_metadata = {}
    monkeypatch.setattr(ocp_backend_module, "TopoDS_Compound", object)
    monkeypatch.setattr(ocp_backend_module, "BRep_Builder", _Builder)
    monkeypatch.setattr(
        ocp_backend_module,
        "BRepBuilderAPI_MakeEdge",
        lambda *_args: SimpleNamespace(Edge=lambda: object()),
    )
    monkeypatch.setattr(
        ocp_backend_module,
        "BRepBuilderAPI_MakeVertex",
        lambda *_args: SimpleNamespace(Vertex=lambda: object()),
    )
    monkeypatch.setattr(ocp_backend_module, "AIS_Shape", lambda _shape: object())
    monkeypatch.setattr(ocp_backend_module, "Quantity_Color", lambda *_args: object())
    return backend, context


def test_ocp_candidate_display_failure_keeps_previous_drilling_presentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    operation_id = OperationId.new()
    first = _drilling_artifact("drill", operation_id=operation_id, generation=1)
    replacement = _drilling_artifact(
        "peck_drill", operation_id=operation_id, generation=2
    )
    other = _drilling_artifact("spot_drill")
    backend.display_toolpath(first)
    backend.display_toolpath(other)
    old_presentations = backend._toolpaths[operation_id]
    old_metadata = backend._toolpath_metadata[operation_id]
    other_presentations = backend._toolpaths[other.source_operation_id]

    context.fail_display = True
    with pytest.raises(RuntimeError, match="injected display failure"):
        backend.display_toolpath(replacement)
    context.fail_display = False

    assert backend._toolpaths[operation_id] == old_presentations
    assert backend._toolpath_metadata[operation_id] == old_metadata
    assert backend._toolpaths[other.source_operation_id] == other_presentations
    assert all(item in context.displayed for item in (*old_presentations, *other_presentations))


def test_ocp_remove_failure_rolls_back_candidate_and_preserves_other_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    operation_id = OperationId.new()
    first = _drilling_artifact("drill", operation_id=operation_id, generation=1)
    replacement = _drilling_artifact(
        "peck_drill", operation_id=operation_id, generation=2
    )
    other = _drilling_artifact("spot_drill")
    backend.display_toolpath(first)
    backend.display_toolpath(other)
    old_presentations = backend._toolpaths[operation_id]
    old_metadata = backend._toolpath_metadata[operation_id]
    other_presentations = backend._toolpaths[other.source_operation_id]
    context.fail_remove.add(old_presentations[0])

    with pytest.raises(RuntimeError, match="injected remove failure"):
        backend.display_toolpath(replacement)

    assert backend._toolpaths[operation_id] == old_presentations
    assert backend._toolpath_metadata[operation_id] == old_metadata
    assert backend._toolpaths[other.source_operation_id] == other_presentations
    assert all(item in context.displayed for item in (*old_presentations, *other_presentations))
