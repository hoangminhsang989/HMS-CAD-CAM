"""Stage 7B.8.2 Reaming presentation and OCP lifecycle tests."""

from __future__ import annotations

from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from hms_cadcam.cam.domain import (
    ContentFingerprint,
    FeedRate,
    FeedUnit,
    Length,
    LengthUnit,
    OperationId,
    ReamingCoolantMode,
    ReamingRetractPolicy,
    SpindleDirection,
    SpindleSpeed,
)
from hms_cadcam.cam.toolpath import LinearMove, RapidMove, ToolpathArtifact
from hms_cadcam.viewer.ocp import backend as ocp_backend_module
from hms_cadcam.viewer.toolpath import (
    ToolpathPresentation,
    ToolpathPresentationRegistry,
)
from tests.unit.test_reaming_strategy import _artifact, _inputs, _pattern, _strategy


def _reaming_artifact(
    *,
    hole_count: int = 1,
    dwell_seconds: float = 0.0,
    coolant: ReamingCoolantMode = ReamingCoolantMode.OFF,
):
    pattern = _pattern(*((float(index * 10), 0.0) for index in range(hole_count)))
    strategy = _strategy(
        pattern=pattern,
        dwell_seconds=dwell_seconds,
        coolant=coolant,
    )
    resource_changes = None
    if coolant is ReamingCoolantMode.FLOOD:
        from hms_cadcam.cam.domain import (
            MachineCoolantCapability,
            ToolCoolantCapability,
        )

        resource_changes = {
            "tool_coolant": (ToolCoolantCapability.FLOOD,),
            "machine_coolant": (MachineCoolantCapability.FLOOD,),
        }
    generator, inputs, _holder, _resolved = _inputs(
        strategy=strategy,
        resource_changes=resource_changes,
    )
    artifact, _computing, _token = _artifact(generator, inputs)
    return artifact, strategy


def _unchecked_artifact(artifact, *, events):
    candidate = object.__new__(ToolpathArtifact)
    for field in fields(artifact):
        field_name = field.name
        object.__setattr__(
            candidate,
            field_name,
            events if field_name == "events" else getattr(artifact, field_name),
        )
    return candidate


def test_reaming_presentation_exposes_complete_process_metadata() -> None:
    artifact, strategy = _reaming_artifact(
        hole_count=2,
        dwell_seconds=0.2,
        coolant=ReamingCoolantMode.FLOOD,
    )
    presentation = ToolpathPresentation.from_artifact(artifact)

    assert presentation.strategy_key == "reaming_v1"
    assert presentation.strategy_version == 1
    assert presentation.hole_count == presentation.pass_count == 2
    assert presentation.nominal_diameter == strategy.nominal_diameter
    assert presentation.pre_hole_diameter == strategy.pre_hole_diameter
    assert presentation.stock_per_side == strategy.stock_per_side
    assert presentation.spindle_speed == SpindleSpeed(500)
    assert presentation.feed_per_revolution == FeedRate(
        0.1, FeedUnit.MM_PER_REVOLUTION
    )
    assert presentation.feed_per_minute == FeedRate(
        50, FeedUnit.MM_PER_MINUTE
    )
    assert presentation.top_z == Length(0, LengthUnit.MM)
    assert presentation.final_depth == Length(-10, LengthUnit.MM)
    assert presentation.retract_height == Length(3, LengthUnit.MM)
    assert presentation.clearance_height == Length(8, LengthUnit.MM)
    assert presentation.dwell_seconds == 0.2
    assert presentation.spindle_direction is SpindleDirection.CLOCKWISE
    assert presentation.retract_policy is ReamingRetractPolicy.CONTROLLED_FEED
    assert presentation.coolant_mode is ReamingCoolantMode.FLOOD
    assert tuple(
        annotation.semantic for annotation in presentation.annotations
    ).count("coolant_begin") == 2
    assert presentation.bounds == artifact.bounds
    assert presentation.statistics == artifact.statistics


def test_reaming_semantics_annotations_and_pass_count_are_deterministic() -> None:
    artifact, _strategy_value = _reaming_artifact(
        hole_count=2,
        dwell_seconds=0.2,
    )
    first = ToolpathPresentation.from_artifact(artifact)
    second = ToolpathPresentation.from_artifact(artifact)

    assert first == second
    assert tuple(segment.semantic for segment in first.segments) == (
        "rapid",
        "reaming_approach",
        "reaming_descent",
        "controlled_retract",
        "final_retract",
    ) * 2
    assert tuple(annotation.semantic for annotation in first.annotations) == (
        "process_begin",
        "spindle_begin",
        "dwell",
        "hole_complete",
        "process_end",
    ) * 2
    assert tuple(annotation.position.z for annotation in first.annotations) == (
        3,
        3,
        -10,
        3,
        8,
    ) * 2
    assert first.pass_count == 2
    assert sum(
        segment.semantic == "reaming_descent" for segment in first.segments
    ) == 2


def test_reaming_presentation_is_native_free_and_controller_neutral() -> None:
    artifact, _strategy_value = _reaming_artifact()
    presentation = ToolpathPresentation.from_artifact(artifact)
    payload = repr(presentation).lower()

    assert presentation.__class__.__module__ == "hms_cadcam.viewer.toolpath"
    assert all(token not in payload for token in ("g85", "g86", "g-code"))
    assert all(
        "ocp" not in value.__class__.__module__.lower()
        and "pyside" not in value.__class__.__module__.lower()
        for field_name in presentation.__dataclass_fields__
        for value in (getattr(presentation, field_name),)
        if value is not None
    )


@pytest.mark.parametrize(
    "failure",
    (
        "ordering",
        "rapid_from_depth",
        "missing_hole_complete",
        "canonical_hole_order",
    ),
)
def test_invalid_reaming_stream_fails_closed(failure: str) -> None:
    artifact, _strategy_value = _reaming_artifact(
        hole_count=2 if failure == "canonical_hole_order" else 1,
        dwell_seconds=0.2,
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
            events[dwell_index],
            events[descent_index],
        )
    elif failure == "rapid_from_depth":
        descent = next(
            event for event in events if event.provenance.endswith(".descent")
        )
        final_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".final_retract")
        )
        final_rapid = events[final_index]
        assert isinstance(descent, LinearMove)
        assert isinstance(final_rapid, RapidMove)
        events[final_index] = replace(final_rapid, start=descent.end)
    elif failure == "missing_hole_complete":
        events = [
            event for event in events
            if not (
                event.provenance.endswith(".complete")
                and getattr(event, "semantic_key", None) == "ream.hole_complete"
            )
        ]
    else:
        state_events = [
            event for event in events
            if not event.provenance.startswith("ream.")
        ]
        second_hole = [
            event for event in events
            if event.provenance.startswith("ream.hole.1.")
        ]
        first_hole = [
            event for event in events
            if event.provenance.startswith("ream.hole.0.")
        ]
        events = [*state_events, *second_hole, *first_hole]

    with pytest.raises(ValueError, match="Reaming"):
        ToolpathPresentation.from_artifact(
            _unchecked_artifact(artifact, events=tuple(events))
        )


@pytest.mark.parametrize(
    "dependency",
    (
        "geometry",
        "canonical_hole_order",
        "wcs",
        "nominal_diameter",
        "pre_hole_diameter",
        "rpm_feed",
        "depth_clearance_retract",
        "dwell",
        "spindle_coolant_retract_policy",
        "tool_assembly",
        "tool_definition",
        "machine",
        "operation_revision",
    ),
)
def test_recompute_guards_reject_every_changed_input(dependency: str) -> None:
    artifact, _strategy_value = _reaming_artifact()
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
        expected_strategy_key="reaming_v1",
        expected_artifact_fingerprint=ContentFingerprint.from_payload({
            dependency: "changed",
        }),
    )
    assert registry.presentations == previous


def test_registry_requires_current_enabled_existing_reaming_result() -> None:
    artifact, _strategy_value = _reaming_artifact()
    registry = ToolpathPresentationRegistry()
    registry.bind_project(30)
    assert registry.display(artifact, generation=30)
    registry.set_visible(artifact.source_operation_id, False)
    previous = registry.presentations
    stale = registry.request_display(artifact.source_operation_id, generation=30)
    current = registry.request_display(artifact.source_operation_id, generation=30)
    assert stale is not None and current is not None

    assert not registry.display(artifact, generation=29)
    assert not registry.display(artifact, generation=30, request=stale)
    assert not registry.display(
        artifact, generation=30, request=current, operation_exists=False
    )
    assert not registry.display(
        artifact, generation=30, request=current, operation_enabled=False
    )
    assert not registry.display(
        artifact,
        generation=30,
        request=current,
        expected_strategy_key="tapping_v1",
    )
    assert registry.presentations == previous
    assert registry.display(
        artifact,
        generation=30,
        request=current,
        expected_strategy_key="reaming_v1",
        expected_artifact_fingerprint=artifact.artifact_fingerprint,
    )
    assert not registry.presentations[0].visible
    registry.bind_project(31)
    assert registry.presentations == ()
    registry.bind_project(None)
    assert not registry.display(artifact, generation=30, request=current)


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


def test_ocp_groups_colors_visibility_replace_remove_clear_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    first, _strategy_value = _reaming_artifact(dwell_seconds=0.2)
    replacement, _replacement_strategy = _reaming_artifact(hole_count=2)
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
    assert len(backend._toolpaths[first.source_operation_id]) == 10
    assert len(set(context.colors.values())) == 10
    backend.set_toolpath_visibility(first.source_operation_id, False)
    backend.display_toolpath(replacement)
    assert not backend._toolpath_metadata[first.source_operation_id].visible
    assert all(
        item not in context.displayed
        for item in backend._toolpaths[first.source_operation_id]
    )
    backend.set_toolpath_visibility(first.source_operation_id, True)
    assert all(
        item in context.displayed
        for item in backend._toolpaths[first.source_operation_id]
    )
    backend.remove_toolpath(first.source_operation_id)
    assert backend.get_toolpath_presentations() == ()
    assert context.displayed == set()
    backend.display_toolpath(first)
    backend.clear_toolpaths()
    assert backend._toolpaths == {}
    assert context.displayed == set()
    backend.display_toolpath(first)
    backend.clear()
    assert backend._toolpaths == {}
    assert context.displayed == set()
    backend.display_toolpath(first)
    backend.close()
    assert backend._closed
    assert backend.get_toolpath_presentations() == ()
    assert context.displayed == set()


@pytest.mark.parametrize("failure", ("conversion", "display", "remove"))
def test_ocp_replacement_failure_rolls_back_only_target_operation(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    backend, context = _ocp_backend(monkeypatch)
    first, _strategy_value = _reaming_artifact()
    other, _other_strategy = _reaming_artifact(dwell_seconds=0.2)
    backend.display_toolpath(first)
    backend.display_toolpath(other)
    old_presentations = backend._toolpaths[first.source_operation_id]
    old_metadata = backend._toolpath_metadata[first.source_operation_id]
    other_presentations = backend._toolpaths[other.source_operation_id]
    replacement = first
    expected_error = RuntimeError
    if failure == "conversion":
        events = list(first.events)
        final_index = next(
            index for index, event in enumerate(events)
            if event.provenance.endswith(".final_retract")
        )
        descent = next(
            event for event in events if event.provenance.endswith(".descent")
        )
        events[final_index] = replace(events[final_index], start=descent.end)
        replacement = _unchecked_artifact(first, events=tuple(events))
        expected_error = ValueError
    elif failure == "display":
        context.fail_display = True
    else:
        context.fail_remove.add(old_presentations[0])

    with pytest.raises(expected_error):
        backend.display_toolpath(replacement)
    context.fail_display = False

    assert backend._toolpaths[first.source_operation_id] == old_presentations
    assert backend._toolpath_metadata[first.source_operation_id] == old_metadata
    assert backend._toolpaths[other.source_operation_id] == other_presentations
    assert all(
        item in context.displayed
        for item in (*old_presentations, *other_presentations)
    )
