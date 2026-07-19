"""Stage 7B.7.2 tapping presentation and viewer lifecycle tests."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    ComputationToken,
    ContentFingerprint,
    DependencyFingerprint,
    FeedRate,
    FeedUnit,
    Length,
    LengthUnit,
    OperationId,
    Point3,
    Revision,
    SetupId,
    SpindleSpeed,
    TappingHand,
    TappingSynchronizationPolicy,
    ToolAssemblyId,
    ToolpathArtifactId,
    Vector3,
)
from hms_cadcam.cam.toolpath import (
    FeedMode,
    MotionClass,
    Pose,
    SpindleState,
    ToolpathBuilder,
)
from hms_cadcam.viewer.ocp import backend as ocp_backend_module
from hms_cadcam.viewer.toolpath import (
    ToolpathPresentation,
    ToolpathPresentationRegistry,
)


def _tapping_artifact(
    *,
    hand: TappingHand = TappingHand.RIGHT_HAND_TAP,
    policy: TappingSynchronizationPolicy = TappingSynchronizationPolicy.RIGID,
    hole_count: int = 1,
    dwell_seconds: float = 0.2,
    operation_id: OperationId | None = None,
    generation: int = 1,
    metadata_format: str = "hms_tapping_sync_v1",
):
    unit = LengthUnit.MM
    operation_id = operation_id or OperationId.new()
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(),
        operation_id=operation_id,
        operation_revision=Revision(generation - 1),
        computation_token=ComputationToken(uuid4(), generation),
        input_fingerprint=DependencyFingerprint.from_payload({
            "generation": generation,
            "hand": hand.value,
            "holes": hole_count,
            "policy": policy.value,
        }),
        unit=unit,
        setup_id=SetupId.new(),
        setup_revision=Revision(0),
        wcs_fingerprint=ContentFingerprint.from_payload({"wcs": generation}),
        tool_assembly_id=ToolAssemblyId.new(),
        tool_assembly_fingerprint=ContentFingerprint.from_payload({
            "tap": hand.value,
        }),
    )
    axis = Vector3(0, 0, 1)
    feed = FeedRate(1.25, FeedUnit.MM_PER_REVOLUTION)
    rapid = FeedRate(1500, FeedUnit.MM_PER_MINUTE)
    speed = SpindleSpeed(500)
    cutting = (
        SpindleState.CLOCKWISE
        if hand is TappingHand.RIGHT_HAND_TAP
        else SpindleState.COUNTERCLOCKWISE
    )
    retract = (
        SpindleState.COUNTERCLOCKWISE
        if hand is TappingHand.RIGHT_HAND_TAP
        else SpindleState.CLOCKWISE
    )
    metadata = (
        ("format", metadata_format),
        ("hand", hand.value),
        ("metadata_version", "1"),
        ("nominal_diameter", "8"),
        ("pitch", "1.25"),
        ("pitch_unit", unit.value),
        ("policy", policy.value),
        ("rpm", "500"),
        ("thread_depth", "10"),
    )
    builder.set_initial_pose(Pose(Point3(0, 0, 12, unit), axis))
    builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_REVOLUTION)
    for hole_index in range(hole_count):
        x = float(hole_index * 10)
        builder.rapid_to(
            Pose(Point3(x, 0, 8, unit), axis),
            rapid_rate=rapid,
            provenance=f"tap.hole.{hole_index}.rapid",
        )
        builder.rapid_to(
            Pose(Point3(x, 0, 3, unit), axis),
            motion_class=MotionClass.LINK,
            rapid_rate=rapid,
            provenance=f"tap.hole.{hole_index}.approach",
        )
        builder.marker(
            "tap.synchronization_begin",
            metadata=metadata,
            provenance=f"tap.hole.{hole_index}.synchronization.begin",
        )
        builder.set_spindle(
            cutting,
            speed,
            provenance=f"tap.hole.{hole_index}.spindle.cutting",
        )
        builder.linear_to(
            Pose(Point3(x, 0, -10, unit), axis),
            feed,
            provenance=f"tap.hole.{hole_index}.descent",
        )
        if dwell_seconds:
            builder.dwell(
                dwell_seconds,
                provenance=f"tap.hole.{hole_index}.dwell",
            )
        builder.set_spindle(
            retract,
            speed,
            provenance=f"tap.hole.{hole_index}.spindle.reversal",
        )
        builder.linear_to(
            Pose(Point3(x, 0, 3, unit), axis),
            feed,
            motion_class=MotionClass.RETRACT,
            provenance=f"tap.hole.{hole_index}.synchronized_retract",
        )
        builder.marker(
            "tap.hole_complete",
            provenance=f"tap.hole.{hole_index}.complete",
        )
        builder.marker(
            "tap.synchronization_end",
            metadata=metadata,
            provenance=f"tap.hole.{hole_index}.synchronization.end",
        )
        builder.rapid_to(
            Pose(Point3(x, 0, 8, unit), axis),
            rapid_rate=rapid,
            provenance=f"tap.hole.{hole_index}.final_retract",
        )
    builder.set_spindle(SpindleState.OFF, provenance="tap.spindle.off")
    return builder.finalize()


@pytest.mark.parametrize(
    ("hand", "policy"),
    (
        (
            TappingHand.RIGHT_HAND_TAP,
            TappingSynchronizationPolicy.RIGID,
        ),
        (
            TappingHand.LEFT_HAND_TAP,
            TappingSynchronizationPolicy.FLOATING,
        ),
    ),
)
def test_tapping_presentation_exposes_process_metadata(hand, policy) -> None:
    artifact = _tapping_artifact(hand=hand, policy=policy, hole_count=2)
    presentation = ToolpathPresentation.from_artifact(artifact)

    assert presentation.strategy_key == "tapping_v1"
    assert presentation.thread_hand is hand
    assert presentation.tapping_mode is policy
    assert presentation.hole_count == presentation.pass_count == 2
    assert presentation.nominal_diameter == Length(8, LengthUnit.MM)
    assert presentation.pitch == Length(1.25, LengthUnit.MM)
    assert presentation.spindle_speed == SpindleSpeed(500)
    assert presentation.depth == Length(10, LengthUnit.MM)
    assert presentation.statistics == artifact.statistics
    assert presentation.bounds == artifact.bounds


def test_tapping_semantics_annotations_and_pass_count_are_deterministic() -> None:
    artifact = _tapping_artifact(hole_count=2)
    first = ToolpathPresentation.from_artifact(artifact)
    second = ToolpathPresentation.from_artifact(artifact)

    assert first == second
    assert tuple(segment.semantic for segment in first.segments) == (
        "rapid",
        "approach",
        "synchronized_descent",
        "synchronized_retract",
        "final_retract",
    ) * 2
    assert tuple(annotation.semantic for annotation in first.annotations) == (
        "synchronization_begin",
        "dwell",
        "spindle_reversal",
        "hole_complete",
        "synchronization_end",
    ) * 2
    assert tuple(annotation.position.z for annotation in first.annotations) == (
        3,
        -10,
        -10,
        3,
        3,
    ) * 2
    assert first.pass_count == 2


def test_tapping_presentation_is_native_free_and_controller_neutral() -> None:
    presentation = ToolpathPresentation.from_artifact(_tapping_artifact())
    payload = repr(presentation).lower()

    assert presentation.__class__.__module__ == "hms_cadcam.viewer.toolpath"
    assert all(token not in payload for token in ("g84", "g74", "m29"))
    assert all(
        "ocp" not in value.__class__.__module__.lower()
        and "pyside" not in value.__class__.__module__.lower()
        for field_name in presentation.__dataclass_fields__
        for value in (getattr(presentation, field_name),)
        if value is not None
    )


def test_registry_recompute_guards_and_operation_lifecycle() -> None:
    operation_id = OperationId.new()
    first = _tapping_artifact(operation_id=operation_id, generation=1)
    replacement = _tapping_artifact(
        operation_id=operation_id,
        generation=2,
        hand=TappingHand.LEFT_HAND_TAP,
    )
    other = _tapping_artifact(operation_id=OperationId.new())
    registry = ToolpathPresentationRegistry()
    registry.bind_project(20)
    assert registry.display(first, generation=20)
    assert registry.display(other, generation=20)
    registry.set_visible(operation_id, False)
    old = next(
        item for item in registry.presentations
        if item.operation_id == operation_id
    )

    stale = registry.request_display(operation_id, generation=20)
    current = registry.request_display(operation_id, generation=20)
    assert stale is not None and current is not None
    assert not registry.display(replacement, generation=20, request=stale)
    assert next(
        item for item in registry.presentations
        if item.operation_id == operation_id
    ) == old
    assert registry.display(
        replacement,
        generation=20,
        request=current,
        expected_strategy_key="tapping_v1",
        expected_artifact_fingerprint=replacement.artifact_fingerprint,
    )
    changed = {
        item.operation_id: item for item in registry.presentations
    }
    assert changed[operation_id].artifact_id == replacement.artifact_id
    assert not changed[operation_id].visible
    assert changed[other.source_operation_id].artifact_id == other.artifact_id

    rejected = _tapping_artifact(operation_id=operation_id, generation=3)
    before = changed[operation_id]
    assert not registry.display(
        rejected,
        generation=20,
        operation_exists=False,
    )
    assert not registry.display(
        rejected,
        generation=20,
        operation_enabled=False,
    )
    assert next(
        item for item in registry.presentations
        if item.operation_id == operation_id
    ) == before
    registry.remove(operation_id)
    assert tuple(item.operation_id for item in registry.presentations) == (
        other.source_operation_id,
    )
    registry.bind_project(21)
    assert registry.presentations == ()
    assert not registry.display(other, generation=20)


@pytest.mark.parametrize("dependency", ("parameters", "tool", "machine", "wcs"))
def test_changed_dependency_rejects_obsolete_tapping_artifact(dependency) -> None:
    artifact = _tapping_artifact()
    registry = ToolpathPresentationRegistry()
    registry.bind_project(8)
    assert registry.display(artifact, generation=8)
    previous = registry.presentations
    request = registry.request_display(artifact.source_operation_id, generation=8)
    assert request is not None

    assert not registry.display(
        artifact,
        generation=8,
        request=request,
        expected_artifact_fingerprint=ContentFingerprint.from_payload({
            dependency: "changed",
        }),
    )
    assert registry.presentations == previous


class _Context:
    def __init__(self) -> None:
        self.displayed: set[object] = set()
        self.removed: list[object] = []
        self.colors: dict[object, object] = {}
        self.fail_display = False
        self.fail_remove: set[object] = set()

    def SetColor(self, presentation, color, *_args) -> None:
        self.colors[presentation] = color

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
    backend._lifecycle = SimpleNamespace(
        initialized=True,
        context=context,
        close=lambda: None,
    )
    backend._toolpaths = {}
    backend._toolpath_metadata = {}
    backend._closed = False
    backend._selection = None
    backend._input = None
    backend._selection_callback = lambda _items: None
    backend._document_id = None
    backend._tree = None
    backend._selected_object_ids = ()
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
    monkeypatch.setattr(
        ocp_backend_module,
        "Quantity_Color",
        lambda *args: args[:3],
    )
    return backend, context


def test_ocp_renders_tapping_groups_and_preserves_visibility_on_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    operation_id = OperationId.new()
    first = _tapping_artifact(operation_id=operation_id)
    replacement = _tapping_artifact(
        operation_id=operation_id,
        generation=2,
        hand=TappingHand.LEFT_HAND_TAP,
    )

    backend.display_toolpath(first)
    assert len(backend._toolpaths[operation_id]) == 10
    assert len(context.colors) == 10
    backend.set_toolpath_visibility(operation_id, False)
    assert all(
        item not in context.displayed
        for item in backend._toolpaths[operation_id]
    )
    backend.set_toolpath_visibility(operation_id, True)
    assert all(
        item in context.displayed
        for item in backend._toolpaths[operation_id]
    )
    backend.set_toolpath_visibility(operation_id, False)
    backend.display_toolpath(replacement)

    assert backend._toolpath_metadata[operation_id].artifact_id == (
        replacement.artifact_id
    )
    assert not backend._toolpath_metadata[operation_id].visible
    assert all(
        item not in context.displayed
        for item in backend._toolpaths[operation_id]
    )


@pytest.mark.parametrize("failure", ("conversion", "display"))
def test_ocp_candidate_failure_keeps_previous_tapping_presentation(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    operation_id = OperationId.new()
    first = _tapping_artifact(operation_id=operation_id)
    replacement = _tapping_artifact(operation_id=operation_id, generation=2)
    backend.display_toolpath(first)
    old_presentations = backend._toolpaths[operation_id]
    old_metadata = backend._toolpath_metadata[operation_id]

    if failure == "conversion":
        replacement = _tapping_artifact(
            operation_id=operation_id,
            generation=2,
            metadata_format="invalid",
        )
    else:
        context.fail_display = True
    with pytest.raises(ValueError if failure == "conversion" else RuntimeError):
        backend.display_toolpath(replacement)
    context.fail_display = False

    assert backend._toolpaths[operation_id] == old_presentations
    assert backend._toolpath_metadata[operation_id] == old_metadata
    assert all(item in context.displayed for item in old_presentations)


def test_ocp_remove_failure_rolls_back_only_target_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    operation_id = OperationId.new()
    first = _tapping_artifact(operation_id=operation_id)
    replacement = _tapping_artifact(operation_id=operation_id, generation=2)
    other = _tapping_artifact(operation_id=OperationId.new())
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
    assert all(
        item in context.displayed
        for item in (*old_presentations, *other_presentations)
    )


def test_ocp_remove_and_clear_cleanup_are_operation_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    first = _tapping_artifact()
    second = _tapping_artifact()
    backend.display_toolpath(first)
    backend.display_toolpath(second)
    second_presentations = backend._toolpaths[second.source_operation_id]

    backend.remove_toolpath(first.source_operation_id)
    assert first.source_operation_id not in backend._toolpaths
    assert second.source_operation_id in backend._toolpaths
    assert all(item in context.displayed for item in second_presentations)

    backend.clear_toolpaths()
    assert backend._toolpaths == {}
    assert backend.get_toolpath_presentations() == ()
    assert context.displayed == set()

    backend.display_toolpath(first)
    backend.close()
    assert backend._closed
    assert backend._toolpaths == {}
    assert backend.get_toolpath_presentations() == ()
    assert context.displayed == set()
