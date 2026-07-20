"""Stage 7B.9.2 Boring presentation and OCP lifecycle tests."""

from __future__ import annotations

from dataclasses import fields, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    BoringCoolantMode,
    BoringRetractPolicy,
    ComputationToken,
    ContentFingerprint,
    DependencyFingerprint,
    FeedRate,
    FeedUnit,
    Length,
    LengthUnit,
    OperationFamily,
    Revision,
    SpindleDirection,
    SpindleSpeed,
    ToolFamily,
)
from hms_cadcam.cam.toolpath import (
    LinearMove,
    MarkerEvent,
    MotionClass,
    RapidMove,
    SpindleState,
    ToolpathArtifact,
)
from hms_cadcam.viewer.ocp import backend as ocp_backend_module
from hms_cadcam.viewer.toolpath import (
    ToolpathPresentation,
    ToolpathPresentationRegistry,
)
from tests.unit.test_boring_strategy import (
    _artifact,
    _inputs,
    _pattern,
    _strategy,
)


def _boring_artifact(
    *,
    hole_count: int = 1,
    dwell_seconds: float = 0.0,
    coolant: BoringCoolantMode = BoringCoolantMode.OFF,
):
    pattern = _pattern(*(
        (float(index * 10), 0.0) for index in range(hole_count)
    ))
    strategy = _strategy(
        pattern=pattern,
        dwell_seconds=dwell_seconds,
        coolant=coolant,
    )
    resources = None
    if coolant is BoringCoolantMode.FLOOD:
        from hms_cadcam.cam.domain import (
            MachineCoolantCapability,
            ToolCoolantCapability,
        )

        resources = {
            "tool_coolant": (ToolCoolantCapability.FLOOD,),
            "machine_coolant": (MachineCoolantCapability.FLOOD,),
        }
    generator, inputs, _resolved = _inputs(
        strategy=strategy,
        resource_changes=resources,
    )
    artifact, _computing, _token = _artifact(generator, inputs)
    return artifact, strategy, inputs


def _unchecked_artifact(artifact, *, events):
    candidate = object.__new__(ToolpathArtifact)
    for field in fields(artifact):
        object.__setattr__(
            candidate,
            field.name,
            tuple(events) if field.name == "events" else getattr(artifact, field.name),
        )
    return candidate


def _with_metadata(artifact, updates, *, first_marker_only: bool = False):
    changed = False
    events = []
    for event in artifact.events:
        if (
            isinstance(event, MarkerEvent)
            and event.semantic_key in {"bore.process_begin", "bore.process_end"}
            and (not first_marker_only or not changed)
        ):
            metadata = dict(event.metadata)
            metadata.update(updates)
            event = replace(event, metadata=tuple(sorted(metadata.items())))
            changed = True
        events.append(event)
    assert changed
    return replace(artifact, events=tuple(events), artifact_fingerprint=None)


def test_boring_presentation_exposes_deterministic_process_and_tool_metadata() -> None:
    artifact, strategy, inputs = _boring_artifact(
        hole_count=2,
        dwell_seconds=0.2,
        coolant=BoringCoolantMode.FLOOD,
    )

    first = ToolpathPresentation.from_artifact(artifact)
    second = ToolpathPresentation.from_artifact(artifact)

    assert first == second
    assert first.strategy_key == "boring_v1"
    assert first.strategy_version == 1
    assert first.operation_family is OperationFamily.DRILLING
    assert first.hole_count == first.pass_count == 2
    assert first.finished_bore_diameter == strategy.finished_bore_diameter
    assert first.pre_bore_diameter == strategy.pre_bore_diameter
    assert first.radial_stock == Length(1, LengthUnit.MM)
    assert first.spindle_speed == SpindleSpeed(600)
    assert first.feed_per_revolution == FeedRate(
        0.1, FeedUnit.MM_PER_REVOLUTION
    )
    assert first.feed_per_minute == FeedRate(60, FeedUnit.MM_PER_MINUTE)
    assert first.top_z == Length(0, LengthUnit.MM)
    assert first.final_depth == Length(-10, LengthUnit.MM)
    assert first.retract_height == Length(3, LengthUnit.MM)
    assert first.clearance_height == Length(8, LengthUnit.MM)
    assert first.dwell_seconds == 0.2
    assert first.spindle_direction is SpindleDirection.CLOCKWISE
    assert first.retract_policy is BoringRetractPolicy.CONTROLLED_FEED
    assert first.coolant_mode is BoringCoolantMode.FLOOD
    assert first.boring_tool_family is ToolFamily.BORING_BAR
    assert first.boring_geometry_version == 1
    assert first.minimum_bore_diameter == Length(15, LengthUnit.MM)
    assert first.maximum_bore_diameter == Length(25, LengthUnit.MM)
    assert first.tool_context_fingerprint == (
        artifact.tool_assembly_fingerprint.digest
    )
    assert inputs.tool.family is ToolFamily.BORING_BAR
    assert first.bounds == artifact.bounds
    assert first.statistics == artifact.statistics


def test_boring_semantics_annotations_and_pass_count_are_deterministic() -> None:
    artifact, _strategy_value, _inputs_value = _boring_artifact(
        hole_count=2,
        dwell_seconds=0.2,
    )
    presentation = ToolpathPresentation.from_artifact(artifact)

    assert tuple(segment.semantic for segment in presentation.segments) == (
        "rapid", "boring_approach", "boring_descent",
        "controlled_retract", "final_retract",
    ) * 2
    assert tuple(annotation.semantic for annotation in presentation.annotations) == (
        "process_begin", "spindle_begin", "dwell",
        "hole_complete", "process_end",
    ) * 2
    assert tuple(annotation.position.z for annotation in presentation.annotations) == (
        3, 3, -10, 3, 8,
    ) * 2
    assert presentation.pass_count == 2
    assert sum(
        segment.semantic == "boring_descent"
        for segment in presentation.segments
    ) == 2
    assert sum(
        segment.semantic == "controlled_retract"
        for segment in presentation.segments
    ) == 2


def test_boring_presentation_is_native_free_and_controller_neutral() -> None:
    artifact, _strategy_value, _inputs_value = _boring_artifact()
    presentation = ToolpathPresentation.from_artifact(artifact)
    payload = repr(presentation).lower()

    assert presentation.__class__.__module__ == "hms_cadcam.viewer.toolpath"
    assert all(
        token not in payload
        for token in ("g85", "g86", "g87", "g89", "m-code", "g-code")
    )
    assert all(
        "ocp" not in value.__class__.__module__.lower()
        and "pyside" not in value.__class__.__module__.lower()
        for field_name in presentation.__dataclass_fields__
        for value in (getattr(presentation, field_name),)
        if value is not None
    )
    assert "cadobject" not in payload and "geometryreference" not in payload


def test_mixed_strategy_provenance_and_wrong_strategy_metadata_fail_closed() -> None:
    artifact, _strategy_value, _inputs_value = _boring_artifact()
    events = list(artifact.events)
    rapid_index = next(
        index for index, event in enumerate(events)
        if event.provenance == "bore.hole.0.rapid"
    )
    events[rapid_index] = replace(
        events[rapid_index], provenance="tap.hole.0.rapid"
    )
    mixed = replace(artifact, events=tuple(events), artifact_fingerprint=None)

    with pytest.raises(ValueError, match="strategy provenance"):
        ToolpathPresentation.from_artifact(mixed)
    for updates in (
        {"strategy_key": "boring_v2"},
        {"strategy_version": "2"},
        {"tool_geometry_version": "2"},
        {"operation_family": OperationFamily.MILLING.value},
    ):
        with pytest.raises(ValueError, match="Boring"):
            ToolpathPresentation.from_artifact(_with_metadata(artifact, updates))


@pytest.mark.parametrize(
    "family",
    (ToolFamily.DRILL, ToolFamily.TAP, ToolFamily.REAMER),
)
def test_wrong_and_mixed_boring_tool_provenance_is_rejected(family) -> None:
    artifact, _strategy_value, _inputs_value = _boring_artifact()
    with pytest.raises(ValueError, match="BORING_BAR"):
        ToolpathPresentation.from_artifact(
            _with_metadata(artifact, {"tool_family": family.value})
        )
    with pytest.raises(ValueError, match="provenance"):
        ToolpathPresentation.from_artifact(_with_metadata(
            artifact,
            {"tool_fingerprint": "0" * 64},
            first_marker_only=True,
        ))


@pytest.mark.parametrize(
    "failure",
    (
        "ordering",
        "rapid_from_depth",
        "rapid_below_retract",
        "unbalanced_process",
        "duplicate_hole_complete",
        "missing_hole_complete",
        "spindle_hung",
        "coolant_hung",
    ),
)
def test_invalid_boring_stream_fails_closed(failure: str) -> None:
    artifact, _strategy_value, _inputs_value = _boring_artifact(
        dwell_seconds=0.2,
        coolant=(
            BoringCoolantMode.FLOOD
            if failure == "coolant_hung"
            else BoringCoolantMode.OFF
        ),
    )
    events = list(artifact.events)
    if failure == "ordering":
        descent_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".descent")
        )
        dwell_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".dwell")
        )
        events[descent_index], events[dwell_index] = (
            events[dwell_index], events[descent_index]
        )
    elif failure == "rapid_from_depth":
        descent = next(
            event for event in events if event.provenance.endswith(".descent")
        )
        retract_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".controlled_retract")
        )
        retract = events[retract_index]
        final_rapid = next(
            event for event in events
            if event.provenance.endswith(".final_retract")
        )
        assert isinstance(descent, LinearMove)
        assert isinstance(retract, LinearMove)
        assert isinstance(final_rapid, RapidMove)
        events[retract_index] = RapidMove(
            event_id=retract.event_id,
            sequence_index=retract.sequence_index,
            source_operation_id=retract.source_operation_id,
            provenance=retract.provenance,
            metadata=retract.metadata,
            start=retract.start,
            end=retract.end,
            motion_class=MotionClass.NON_CUTTING,
            rapid_rate=final_rapid.rapid_rate,
        )
    elif failure == "rapid_below_retract":
        approach_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".approach")
        )
        descent_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".descent")
        )
        approach = events[approach_index]
        descent = events[descent_index]
        low = replace(approach.end, position=replace(approach.end.position, z=2))
        events[approach_index] = replace(approach, end=low)
        events[descent_index] = replace(descent, start=low)
    elif failure == "unbalanced_process":
        events = [
            event for event in events
            if getattr(event, "semantic_key", None) != "bore.process_end"
        ]
    elif failure == "duplicate_hole_complete":
        end_index = next(
            index for index, event in enumerate(events)
            if getattr(event, "semantic_key", None) == "bore.process_end"
        )
        events[end_index] = replace(
            events[end_index], semantic_key="bore.hole_complete"
        )
    elif failure == "missing_hole_complete":
        complete_index = next(
            index for index, event in enumerate(events)
            if getattr(event, "semantic_key", None) == "bore.hole_complete"
        )
        events[complete_index] = replace(
            events[complete_index], semantic_key="bore.invalid_complete"
        )
    elif failure == "spindle_hung":
        end_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".spindle.end")
        )
        events[end_index] = replace(
            events[end_index], state=SpindleState.CLOCKWISE,
            speed=SpindleSpeed(600),
        )
    else:
        end_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".coolant.end")
        )
        begin = next(
            event for event in events
            if event.provenance.endswith(".coolant.begin")
        )
        events[end_index] = replace(events[end_index], state=begin.state)

    with pytest.raises(ValueError, match="Boring"):
        ToolpathPresentation.from_artifact(
            _unchecked_artifact(artifact, events=tuple(events))
        )


def test_registry_enforces_boring_recompute_guards_and_lifecycle() -> None:
    first, _strategy_value, _inputs_value = _boring_artifact()
    replacement, _replacement_strategy, _replacement_inputs = _boring_artifact(
        hole_count=2
    )
    replacement = replace(
        replacement,
        source_operation_id=first.source_operation_id,
        events=tuple(
            replace(event, source_operation_id=first.source_operation_id)
            for event in replacement.events
        ),
        artifact_fingerprint=None,
    )
    registry = ToolpathPresentationRegistry()
    registry.bind_project(12)
    assert registry.display(first, generation=12)
    registry.set_visible(first.source_operation_id, False)
    previous = registry.presentations
    stale = registry.request_display(first.source_operation_id, generation=12)
    current = registry.request_display(first.source_operation_id, generation=12)
    assert stale is not None and current is not None

    rejected = (
        {"generation": 11, "request": current},
        {"generation": 12, "request": stale},
        {"generation": 12, "request": current, "operation_exists": False},
        {"generation": 12, "request": current, "operation_enabled": False},
        {
            "generation": 12,
            "request": current,
            "expected_strategy_key": "reaming_v1",
        },
        {
            "generation": 12,
            "request": current,
            "expected_strategy_version": 2,
        },
        {
            "generation": 12,
            "request": current,
            "expected_operation_family": OperationFamily.MILLING,
        },
        {
            "generation": 12,
            "request": current,
            "expected_operation_family": "invalid_family",
        },
        {
            "generation": 12,
            "request": current,
            "expected_artifact_fingerprint": ContentFingerprint.from_payload(
                {"artifact": "changed"}
            ),
        },
        {
            "generation": 12,
            "request": current,
            "expected_input_fingerprint": DependencyFingerprint.from_payload(
                {"input": "changed"}
            ),
        },
        {
            "generation": 12,
            "request": current,
            "expected_computation_token": ComputationToken(uuid4(), 999),
        },
        {
            "generation": 12,
            "request": current,
            "expected_operation_revision": Revision(
                replacement.operation_revision.value + 1
            ),
        },
    )
    for guard in rejected:
        assert not registry.display(replacement, **guard)
        assert registry.presentations == previous
    assert registry.display(
        replacement,
        generation=12,
        request=current,
        expected_strategy_key="boring_v1",
        expected_strategy_version=1,
        expected_operation_family=OperationFamily.DRILLING,
        expected_artifact_fingerprint=replacement.artifact_fingerprint,
        expected_input_fingerprint=replacement.input_fingerprint,
        expected_computation_token=replacement.computation_token,
        expected_operation_revision=replacement.operation_revision,
    )
    assert registry.presentations[0].artifact_id == replacement.artifact_id
    assert not registry.presentations[0].visible
    registry.select(first.source_operation_id)
    assert registry.presentations[0].highlighted
    registry.remove(first.source_operation_id)
    assert registry.presentations == ()
    registry.bind_project(13)
    assert not registry.display(replacement, generation=12, request=current)
    registry.bind_project(None)
    assert registry.presentations == ()


class _Context:
    def __init__(self) -> None:
        self.displayed: set[object] = set()
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
        clear=lambda: None,
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
        ocp_backend_module, "Quantity_Color", lambda *args: args[:3]
    )
    return backend, context


def test_ocp_boring_show_hide_replace_remove_clear_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    first, _strategy_value, _inputs_value = _boring_artifact(
        dwell_seconds=0.2,
        coolant=BoringCoolantMode.FLOOD,
    )
    other, _other_strategy, _other_inputs = _boring_artifact()
    replacement, _replacement_strategy, _replacement_inputs = _boring_artifact(
        hole_count=2
    )
    replacement = replace(
        replacement,
        source_operation_id=first.source_operation_id,
        events=tuple(
            replace(event, source_operation_id=first.source_operation_id)
            for event in replacement.events
        ),
        artifact_fingerprint=None,
    )

    backend.display_toolpath(first)
    backend.display_toolpath(other)
    other_native = backend._toolpaths[other.source_operation_id]
    semantics = {
        item.semantic
        for item in backend._toolpath_metadata[first.source_operation_id].segments
    }
    assert {
        "rapid", "boring_approach", "boring_descent",
        "controlled_retract", "final_retract",
    }.issubset(semantics)
    backend.set_toolpath_visibility(first.source_operation_id, False)
    backend.display_toolpath(replacement)
    assert not backend._toolpath_metadata[first.source_operation_id].visible
    assert all(
        item not in context.displayed
        for item in backend._toolpaths[first.source_operation_id]
    )
    assert backend._toolpaths[other.source_operation_id] == other_native
    backend.set_toolpath_visibility(first.source_operation_id, True)
    backend.remove_toolpath(first.source_operation_id)
    assert other.source_operation_id in backend._toolpaths
    backend.clear_toolpaths()
    assert backend.get_toolpath_presentations() == ()
    assert context.displayed == set()
    backend.display_toolpath(first)
    backend.clear()
    assert backend._toolpaths == {}
    backend.display_toolpath(first)
    backend.close()
    assert backend._closed and backend.get_toolpath_presentations() == ()


@pytest.mark.parametrize("failure", ("conversion", "display", "remove"))
def test_ocp_boring_replacement_failure_rolls_back_visibility_and_operation(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    first, _strategy_value, _inputs_value = _boring_artifact()
    other, _other_strategy, _other_inputs = _boring_artifact(dwell_seconds=0.2)
    backend.display_toolpath(first)
    backend.display_toolpath(other)
    backend.set_toolpath_visibility(first.source_operation_id, False)
    old_native = backend._toolpaths[first.source_operation_id]
    old_metadata = backend._toolpath_metadata[first.source_operation_id]
    other_native = backend._toolpaths[other.source_operation_id]
    replacement = first
    expected_error = RuntimeError
    if failure == "conversion":
        events = list(first.events)
        descent = next(
            event for event in events if event.provenance.endswith(".descent")
        )
        final_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".final_retract")
        )
        events[final_index] = replace(events[final_index], start=descent.end)
        replacement = _unchecked_artifact(first, events=tuple(events))
        expected_error = ValueError
    elif failure == "display":
        context.fail_display = True
    else:
        context.fail_remove.add(old_native[0])

    with pytest.raises(expected_error):
        backend.display_toolpath(replacement)
    context.fail_display = False

    assert backend._toolpaths[first.source_operation_id] == old_native
    assert backend._toolpath_metadata[first.source_operation_id] == old_metadata
    assert not backend._toolpath_metadata[first.source_operation_id].visible
    assert backend._toolpaths[other.source_operation_id] == other_native
    assert all(item in context.displayed for item in other_native)
    assert all(item not in context.displayed for item in old_native)
