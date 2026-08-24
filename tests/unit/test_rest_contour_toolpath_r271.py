"""R271 Phase-B safety boundary tests."""

from __future__ import annotations

from dataclasses import replace
from copy import copy
import hashlib
import json
import math
from types import SimpleNamespace

import pytest

from hms_cadcam.cam.application.rest_contour_geometry import plan_rest_contour_residual
from hms_cadcam.cam.application.rest_contour_toolpath import (
    RestContourPhaseBExecutionContext,
    RestContourPhaseBCandidate,
    RestContourPhaseBNoRestMaterial,
    RestContourPhaseBPrepared,
    RestContourPhaseBSuccessorProvenance,
    generate_rest_contour_phase_b,
    prepare_rest_contour_phase_b,
    publish_rest_contour_phase_b,
)
from hms_cadcam.cam.domain.rest_contour import RestContourDiagnosticCode, RestContourValidationError
from hms_cadcam.cam.domain import (
    BallEndGeometry, BullNoseGeometry, ContentFingerprint, Length, LengthUnit,
    Point3, Revision, ToolFamily,
)
from hms_cadcam.cam.material_state import (
    CutterEnvelope,
    MaterialState,
    MaterialStatePrecisionPolicy,
    MaterialStateStore,
    calculate_material_state,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.toolpath import LinearMove, MotionClass, RapidMove, compute_material_removal_fingerprint
import hms_cadcam.cam.application.rest_contour_toolpath as rest_contour_toolpath

from test_rest_contour_core_r271 import _inputs, _positive_inputs
from test_rest_contour_foundation_r270 import _inputs as _r270_inputs


def _prepared() -> RestContourPhaseBPrepared:
    inputs = _inputs()
    plan = plan_rest_contour_residual(inputs)
    value = prepare_rest_contour_phase_b(RestContourPhaseBExecutionContext(inputs, plan))
    assert isinstance(value, RestContourPhaseBPrepared)
    return value


def _positive_prepared(*, multiple_depths: bool = False) -> tuple[RestContourPhaseBExecutionContext, RestContourPhaseBPrepared]:
    inputs = _positive_inputs(multiple_depths=multiple_depths)
    plan = plan_rest_contour_residual(inputs)
    context = RestContourPhaseBExecutionContext(inputs, plan)
    value = prepare_rest_contour_phase_b(context)
    assert isinstance(value, RestContourPhaseBPrepared)
    return context, value


def test_no_rest_returns_before_computation_or_persistence() -> None:
    inputs = _inputs(rest=False)
    outcome = plan_rest_contour_residual(inputs)
    value = prepare_rest_contour_phase_b(RestContourPhaseBExecutionContext(inputs, outcome))
    assert isinstance(value, RestContourPhaseBNoRestMaterial)
    # This is a real Ø10 producer over the complete profile and an equal Ø10
    # Rest cutter.  The typed result is geometry-derived, not a diameter guard.
    assert inputs.tool.cutting_geometry.diameter.value == 10.0
    assert inputs.foundation.material.candidate is not None
    assert inputs.foundation.material.candidate.producer_artifact.events


def test_default_seven_plunges_are_rejected_as_unsafe_before_successor() -> None:
    prepared = _prepared()
    with pytest.raises(RestContourValidationError) as error:
        generate_rest_contour_phase_b(prepared)
    assert error.value.code is RestContourDiagnosticCode.ENTRY_UNSAFE


@pytest.mark.parametrize("constructor", (
    lambda value: RestContourPhaseBPrepared(value.plan, value.predecessor_state, value.setup,
        value.base_operation, value.computing_operation, value.input_fingerprint, value.computation_token,
        value.setup_payload_fingerprint),
    lambda value: replace(value),
))
def test_direct_and_replace_prepared_are_unregistered(constructor) -> None:
    with pytest.raises(RestContourValidationError) as error:
        generate_rest_contour_phase_b(constructor(_prepared()))
    assert error.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_phase_b_registrars_are_not_module_capabilities() -> None:
    """Only the closed public prepare/generate boundaries can mint values."""
    assert not callable(getattr(rest_contour_toolpath, "_register_prepared", None))
    assert not callable(getattr(rest_contour_toolpath, "_register_candidate", None))
    assert "registrar" not in str(__import__("inspect").signature(prepare_rest_contour_phase_b))
    assert "registrar" not in str(__import__("inspect").signature(generate_rest_contour_phase_b))


def test_nested_fragment_endpoint_mutation_breaks_deep_prepared_seal_before_generation(monkeypatch) -> None:
    """A stale plan fingerprint may not hide a post-mint fragment splice."""
    _context, prepared = _positive_prepared()
    fragment = prepared.plan.layers[0].region_fragments[0].fragments[0]
    assert 0.0 < fragment.end < 0.9
    object.__setattr__(fragment, "end", 0.9)
    def no_artifact(*_args, **_kwargs):
        raise AssertionError("mutated plan reached artifact construction")
    monkeypatch.setattr(rest_contour_toolpath, "_build_artifact", no_artifact)
    with pytest.raises(RestContourValidationError) as error:
        generate_rest_contour_phase_b(prepared)
    assert error.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_registered_prepared_remains_deterministic_at_entry_boundary() -> None:
    prepared = _prepared()
    for _ in range(2):
        with pytest.raises(RestContourValidationError) as error:
            generate_rest_contour_phase_b(prepared)
        assert error.value.code is RestContourDiagnosticCode.ENTRY_UNSAFE


def test_real_10mm_producer_to_6mm_rest_cutter_generates_only_safe_motion_and_successor(tmp_path) -> None:
    context, prepared = _positive_prepared()
    candidate = generate_rest_contour_phase_b(prepared)
    cuts = tuple(event for event in candidate.artifact.events
                 if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING)
    assert len(cuts) == len(prepared.plan.layers)
    envelope = CutterEnvelope.from_tool(prepared.plan.authority.tool)
    for event in candidate.artifact.events:
        if not isinstance(event, (LinearMove, RapidMove)) or event.motion_class is MotionClass.CUTTING:
            continue
        if event.motion_class is MotionClass.RETRACT:
            continue
        start, end = event.start.position, event.end.position
        if abs(start.x - end.x) <= 1.0e-8 and abs(start.y - end.y) <= 1.0e-8:
            assert not rest_contour_toolpath._meaningful_at(
                prepared.predecessor_state, envelope, end.x, end.y, end.z,
            )
        else:
            assert rest_contour_toolpath._line_is_clear(
                prepared.predecessor_state, envelope, start, end, end.z,
            )
    assert candidate.successor_state.remaining_volume < prepared.predecessor_state.remaining_volume
    assert candidate.successor_state.parent_fingerprint == prepared.predecessor_state.fingerprint
    publication = publish_rest_contour_phase_b(candidate, current_context=context, project_root=tmp_path)
    assert publication.artifact == candidate.artifact
    assert publication.successor_state.content_integrity_fingerprint == candidate.successor_state.content_integrity_fingerprint


def test_persisted_successor_readback_rejects_rechecksummed_provenance_tamper(tmp_path) -> None:
    """A valid content seal cannot replace exact successor provenance authority."""
    context, prepared = _positive_prepared()
    candidate = generate_rest_contour_phase_b(prepared)

    class ProvenanceTamperingStateStore:
        def __init__(self) -> None:
            self.delegate = MaterialStateStore()
            self.write_calls = 0
            self.load_calls = 0

        def write(self, project_root, state):
            self.write_calls += 1
            path = self.delegate.write(project_root, state)
            document = json.loads(path.read_text(encoding="utf-8"))
            document["engine_version"] = "forged-readback-engine"
            document["toolpath_fingerprint"] = document["stock_fingerprint"]
            document["checksum_sha256"] = ""
            unsigned = json.dumps(
                document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            document["checksum_sha256"] = hashlib.sha256(unsigned).hexdigest()
            path.write_bytes(json.dumps(
                document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8"))
            return path

        def load(self, project_root, fingerprint):
            self.load_calls += 1
            return self.delegate.load(project_root, fingerprint)

    store = ProvenanceTamperingStateStore()
    with pytest.raises(RestContourValidationError) as error:
        publish_rest_contour_phase_b(
            candidate,
            current_context=context,
            project_root=tmp_path,
            material_state_store=store,
        )
    assert error.value.code is RestContourDiagnosticCode.PUBLICATION_FAILED
    assert store.write_calls == store.load_calls == 1


def test_multiple_depths_replay_all_cutting_levels() -> None:
    _context, prepared = _positive_prepared()
    candidate = generate_rest_contour_phase_b(prepared)
    levels = {event.end.position.z for event in candidate.artifact.events
              if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING}
    assert levels == {10.0, 2.0}
    assert candidate.successor_state.remaining_volume < prepared.predecessor_state.remaining_volume


def _assert_analytic_non_cutting_law(candidate: RestContourPhaseBCandidate) -> None:
    """Check Phase-B links against the same exact envelope predicate as replay."""
    prepared = candidate.prepared
    envelope = CutterEnvelope.from_tool(prepared.plan.authority.tool)
    for event in candidate.artifact.events:
        if not isinstance(event, (LinearMove, RapidMove)):
            continue
        if event.motion_class is MotionClass.CUTTING:
            assert isinstance(event, LinearMove)
            assert event.engagement
            continue
        start, end = event.start.position, event.end.position
        if event.motion_class is MotionClass.RETRACT:
            # The only legal retraction starts at the terminal point just swept
            # by CUTTING and is monotone +Z; it is never a lateral removal.
            assert abs(start.x - end.x) <= 1.0e-8 and abs(start.y - end.y) <= 1.0e-8
            assert end.z > start.z
            continue
        if abs(start.x - end.x) <= 1.0e-8 and abs(start.y - end.y) <= 1.0e-8:
            assert not rest_contour_toolpath._meaningful_at(
                prepared.predecessor_state, envelope, end.x, end.y, min(start.z, end.z),
            )
        else:
            assert rest_contour_toolpath._line_is_clear(
                prepared.predecessor_state, envelope, start, end, min(start.z, end.z),
            )


def test_two_real_disconnected_gaps_emit_only_union_spans_and_retract_between_components() -> None:
    # Segment 3's .28/.72 producer gap leaves the exact forward Phase-A
    # residual start inside material.  Its .29/.71 fixture proves that a
    # disconnected forward component can succeed without reversal.
    inputs = _positive_inputs(producer_gaps=((1, 0.28, 0.72), (3, 0.29, 0.71)))
    plan = plan_rest_contour_residual(inputs)
    assert hasattr(plan, "layers")
    context = RestContourPhaseBExecutionContext(inputs, plan)
    prepared = prepare_rest_contour_phase_b(context)
    assert isinstance(prepared, RestContourPhaseBPrepared)
    for layer in prepared.plan.layers:
        spans = rest_contour_toolpath._normalized_spans(prepared.plan, layer)
        components = rest_contour_toolpath._span_components(prepared.plan, spans)
        assert [(span.segment_index, round(span.start, 2), round(span.end, 2)) for span in spans] == [
            (1, 0.32, 0.68), (3, 0.33, 0.67),
        ]
        assert len(components) == 2
    candidate = generate_rest_contour_phase_b(prepared)
    cuts = [event for event in candidate.artifact.events
            if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING]
    assert len(cuts) == len(prepared.plan.layers) * 2
    assert all(event.start.position != event.end.position for event in cuts)
    assert sum(1 for event in candidate.artifact.events
               if isinstance(event, (LinearMove, RapidMove)) and event.motion_class is MotionClass.RETRACT) >= 4
    _assert_analytic_non_cutting_law(candidate)


def test_real_two_gap_cut_events_match_exact_phase_a_union_endpoints_at_each_depth() -> None:
    """Phase B may consume the approved union, but may never extend it."""
    inputs = _positive_inputs(
        multiple_depths=True,
        producer_gaps=((1, 0.28, 0.72), (3, 0.29, 0.71)),
    )
    plan = plan_rest_contour_residual(inputs)
    prepared = prepare_rest_contour_phase_b(RestContourPhaseBExecutionContext(inputs, plan))
    assert isinstance(prepared, RestContourPhaseBPrepared)
    candidate = generate_rest_contour_phase_b(prepared)

    expected = {
        layer.tip_z: tuple(
            (
                replace(span.start_point, z=layer.tip_z),
                replace(span.end_point, z=layer.tip_z),
            )
            for component in rest_contour_toolpath._span_components(
                prepared.plan, rest_contour_toolpath._normalized_spans(prepared.plan, layer),
            )
            for span in component
        )
        for layer in prepared.plan.layers
    }
    actual: dict[float, list[tuple[Point3, Point3]]] = {}
    for event in candidate.artifact.events:
        if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING:
            actual.setdefault(event.end.position.z, []).append((event.start.position, event.end.position))

    assert set(actual) == set(expected)
    for depth, expected_spans in expected.items():
        # Phase B consumes exact Phase-A spans in their canonical forward
        # direction.  Directed equality also catches a reversed component.
        assert tuple(actual[depth]) == expected_spans


def test_disconnected_segment3_legacy_gap_is_typed_forward_entry_unsafe() -> None:
    """The former reverse-only segment-3 case must fail before any motion."""
    inputs = _positive_inputs(producer_gaps=((1, 0.28, 0.72), (3, 0.28, 0.72)))
    plan = plan_rest_contour_residual(inputs)
    prepared = prepare_rest_contour_phase_b(RestContourPhaseBExecutionContext(inputs, plan))
    assert isinstance(prepared, RestContourPhaseBPrepared)
    with pytest.raises(RestContourValidationError) as error:
        generate_rest_contour_phase_b(prepared)
    assert error.value.code is RestContourDiagnosticCode.ENTRY_UNSAFE


def test_multi_contributor_span_is_deterministic_without_mapping_sort_and_interpolates_union_bounds() -> None:
    """Regression for R2: contributors are scalar-sorted, never dict-sorted."""
    _context, prepared = _positive_prepared()
    segment = prepared.plan.center_loop.segments[1]
    first_region = ContentFingerprint.from_payload({"region": "z"})
    second_region = ContentFingerprint.from_payload({"region": "a"})
    first = SimpleNamespace(
        start=0.2, end=0.4, fingerprint=ContentFingerprint.from_payload({"fragment": "z"}),
        region_fingerprint=first_region, responsible_cells=((1, 2),),
    )
    second = SimpleNamespace(
        start=0.4, end=0.8, fingerprint=ContentFingerprint.from_payload({"fragment": "a"}),
        region_fingerprint=second_region, responsible_cells=((2, 3),),
    )
    forward = rest_contour_toolpath._span_from_fragments(prepared.plan, 1, [first, second])
    reverse = rest_contour_toolpath._span_from_fragments(prepared.plan, 1, [second, first])

    assert forward == reverse
    assert forward.start_point == Point3(
        segment.start.x + (segment.end.x - segment.start.x) * 0.2,
        segment.start.y + (segment.end.y - segment.start.y) * 0.2,
        segment.start.z + (segment.end.z - segment.start.z) * 0.2,
        segment.start.unit,
    )
    assert forward.end_point == Point3(
        segment.start.x + (segment.end.x - segment.start.x) * 0.8,
        segment.start.y + (segment.end.y - segment.start.y) * 0.8,
        segment.start.z + (segment.end.z - segment.start.z) * 0.8,
        segment.start.unit,
    )


def test_positive_micro_gap_is_never_merged_or_bridged() -> None:
    """A 1e-10 gap remains an entry gap; Phase B must not widen a cut union."""
    _context, prepared = _positive_prepared()
    region = ContentFingerprint.from_payload({"region": "micro-gap"})
    fragments = tuple(
        SimpleNamespace(
            segment_index=1, start=start, end=end,
            fingerprint=ContentFingerprint.from_payload({"fragment": index}),
            region_fingerprint=region, responsible_cells=((index, index),),
        )
        for index, (start, end) in enumerate(((0.2, 0.4), (0.4000000001, 0.8)))
    )
    layer = SimpleNamespace(region_fragments=(SimpleNamespace(fragments=fragments),))
    spans = rest_contour_toolpath._normalized_spans(prepared.plan, layer)

    assert [(span.start, span.end) for span in spans] == [(0.2, 0.4), (0.4000000001, 0.8)]
    assert spans[0].end_point != spans[1].start_point


def test_trusted_calculated_2x2_near_tangent_open_disk_is_meaningful_and_not_clear() -> None:
    """A positive 1e-10 disk penetration must fail analytic link clearance."""
    inputs = _positive_inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    state = calculate_material_state(
        stock=inputs.setup.stock,
        artifact=candidate.producer_artifact,
        tool=_r270_inputs().tool,
        setup_fingerprint=material_state_setup_fingerprint(inputs.setup),
        precision=MaterialStatePrecisionPolicy(grid_target=2),
    ).state
    index = max(range(len(state.top_heights)), key=state.top_heights.__getitem__)
    row, column = divmod(index, state.width)
    center_x = (column + 0.5) * state.cell_size_x
    center_y = (row + 0.5) * state.cell_size_y
    tip_z = state.top_heights[index] - 1.0
    envelope = CutterEnvelope.from_tool(inputs.tool)
    start = Point3(center_x - 10.0, center_y + envelope.radius - 1.0e-10, tip_z, state.unit)
    end = Point3(center_x + 10.0, center_y + envelope.radius - 1.0e-10, tip_z, state.unit)
    tangent_start = Point3(center_x - 10.0, center_y + envelope.radius, tip_z, state.unit)
    tangent_end = Point3(center_x + 10.0, center_y + envelope.radius, tip_z, state.unit)

    assert rest_contour_toolpath._meaningful_at(state, envelope, center_x, center_y, tip_z)
    assert rest_contour_toolpath._line_is_clear(state, envelope, tangent_start, tangent_end, tip_z)
    assert not rest_contour_toolpath._line_is_clear(state, envelope, start, end, tip_z)


def test_sub_tolerance_positive_segment_uses_analytic_interior_not_point_shortcut() -> None:
    """A 2e-9 link has squared length below 1e-8 but crosses a real cell."""
    inputs = _positive_inputs()
    producer = inputs.foundation.material.candidate
    assert producer is not None
    state = calculate_material_state(
        stock=inputs.setup.stock, artifact=producer.producer_artifact,
        tool=_r270_inputs().tool, setup_fingerprint=material_state_setup_fingerprint(inputs.setup),
        precision=MaterialStatePrecisionPolicy(grid_target=2),
    ).state
    index = max(range(len(state.top_heights)), key=state.top_heights.__getitem__)
    row, column = divmod(index, state.width)
    center_x, center_y = ((column + 0.5) * state.cell_size_x,
                          (row + 0.5) * state.cell_size_y)
    tip_z = state.top_heights[index] - 1.0
    envelope = CutterEnvelope(2.0e-10, 0.0, False)
    start = Point3(center_x - 1.0e-9, center_y, tip_z, state.unit)
    end = Point3(center_x + 1.0e-9, center_y, tip_z, state.unit)

    assert (end.x - start.x) ** 2 < 1.0e-8
    assert not rest_contour_toolpath._meaningful_at(state, envelope, start.x, start.y, tip_z)
    assert not rest_contour_toolpath._meaningful_at(state, envelope, end.x, end.y, tip_z)
    assert rest_contour_toolpath._meaningful_at(state, envelope, center_x, center_y, tip_z)
    assert not rest_contour_toolpath._line_is_clear(state, envelope, start, end, tip_z)
    assert rest_contour_toolpath._line_is_clear(
        state, envelope, Point3(center_x - 1.0e-9, center_y + 1.0e-9, tip_z, state.unit),
        Point3(center_x - 1.0e-9, center_y + 1.0e-9, tip_z, state.unit), tip_z,
    )
    assert not rest_contour_toolpath._line_is_clear(
        state, envelope, Point3(center_x, center_y, tip_z, state.unit),
        Point3(center_x, center_y, tip_z, state.unit), tip_z,
    )


def test_subnormal_positive_segment_retains_open_disk_interior_authority() -> None:
    """No squared-radius underflow may turn a real 1e-323 chord into a point."""
    unit = LengthUnit.MM
    minimum = float.fromhex("0x0.0000000000001p-1022")
    state = SimpleNamespace(
        width=1, height=1, cell_size_x=2.0 * minimum, cell_size_y=2.0 * minimum,
        top_heights=(1.0,), precision=SimpleNamespace(residual_threshold=0.0),
    )
    envelope = CutterEnvelope(minimum, 0.0, False)
    start = Point3(0.0, minimum, 0.0, unit)
    end = Point3(2.0 * minimum, minimum, 0.0, unit)
    assert not rest_contour_toolpath._meaningful_at(state, envelope, start.x, start.y, 0.0)
    assert not rest_contour_toolpath._meaningful_at(state, envelope, end.x, end.y, 0.0)
    assert rest_contour_toolpath._meaningful_at(state, envelope, minimum, minimum, 0.0)
    assert not rest_contour_toolpath._line_is_clear(state, envelope, start, end, 0.0)


@pytest.mark.parametrize(("family", "geometry"), (
    (ToolFamily.BALL_END_MILL, BallEndGeometry(Length(4.0, LengthUnit.MM), Length(12.0, LengthUnit.MM))),
    (ToolFamily.BULL_NOSE_END_MILL, BullNoseGeometry(
        Length(4.0, LengthUnit.MM), Length(12.0, LengthUnit.MM), Length(2.0, LengthUnit.MM))),
))
def test_actual_ball_and_bull_near_threshold_small_forbidden_radius_stay_analytic(family, geometry) -> None:
    """Small positive BALL/BULL contact disks cannot collapse into point links."""
    inputs = _positive_inputs()
    producer = inputs.foundation.material.candidate
    assert producer is not None
    cutter = replace(_r270_inputs().tool, family=family, cutting_geometry=geometry)
    state = calculate_material_state(
        stock=inputs.setup.stock, artifact=producer.producer_artifact, tool=cutter,
        setup_fingerprint=material_state_setup_fingerprint(inputs.setup),
        precision=MaterialStatePrecisionPolicy(grid_target=2),
    ).state
    index = max(range(len(state.top_heights)), key=state.top_heights.__getitem__)
    row, column = divmod(index, state.width)
    center_x, center_y = ((column + 0.5) * state.cell_size_x,
                          (row + 0.5) * state.cell_size_y)
    envelope = CutterEnvelope.from_tool(cutter)
    tip_z = state.top_heights[index] - state.precision.residual_threshold - 1.0e-12
    radius = envelope.maximum_removable_radius(
        target_tip_z=tip_z, current_height=state.top_heights[index],
        threshold=state.precision.residual_threshold,
    )
    assert radius is not None and 0.0 < radius < 1.0e-5
    start = Point3(center_x - 2.0 * radius, center_y, tip_z, state.unit)
    end = Point3(center_x + 2.0 * radius, center_y, tip_z, state.unit)
    assert not rest_contour_toolpath._meaningful_at(state, envelope, start.x, start.y, tip_z)
    assert not rest_contour_toolpath._meaningful_at(state, envelope, end.x, end.y, tip_z)
    assert not rest_contour_toolpath._line_is_clear(state, envelope, start, end, tip_z)


def test_phase_a_endpoint_ulp_rounding_tangency_is_clear_without_widening_the_disk() -> None:
    """The real R271 endpoint has a ~8e-16 inverse-proof rounding interval."""
    _context, prepared = _positive_prepared()
    layer = prepared.plan.layers[0]
    span = rest_contour_toolpath._normalized_spans(prepared.plan, layer)[0]
    predecessor_coordinate = span.segment_index + span.end - len(prepared.plan.center_loop.segments)
    route = rest_contour_toolpath._gap_route(
        prepared.plan,
        predecessor_coordinate + ((span.segment_index + span.start) - predecessor_coordinate) / 2.0,
        span.segment_index + span.start,
    )
    envelope = CutterEnvelope.from_tool(prepared.plan.authority.tool)

    # The independent quadratic inverse proof computes the contact boundary
    # just below 1.0 (about 8e-16 in this real fixture).  It is the exact
    # Phase-A tangency at the cut-start, not a positive material intersection.
    assert rest_contour_toolpath._line_is_clear(
        prepared.predecessor_state, envelope, route[-2], route[-1], layer.tip_z,
    )


def test_endpoint_inside_meaningful_open_disk_is_not_erased_by_ulp_interval_contraction() -> None:
    """Reviewer repro: a strict endpoint engagement remains unsafe at t=1."""
    inputs = _positive_inputs()
    producer = inputs.foundation.material.candidate
    assert producer is not None
    state = calculate_material_state(
        stock=inputs.setup.stock, artifact=producer.producer_artifact,
        tool=_r270_inputs().tool, setup_fingerprint=material_state_setup_fingerprint(inputs.setup),
        precision=MaterialStatePrecisionPolicy(grid_target=2),
    ).state
    index = max(range(len(state.top_heights)), key=state.top_heights.__getitem__)
    row, column = divmod(index, state.width)
    envelope = CutterEnvelope(3.0, 0.0, False)
    center_x = (column + 0.5) * state.cell_size_x
    center_y = (row + 0.5) * state.cell_size_y
    tip_z = state.top_heights[index] - 1.0
    start = Point3(center_x - 25.0, center_y, tip_z, state.unit)
    end = Point3(math.nextafter(center_x - envelope.radius, center_x), center_y, tip_z, state.unit)

    assert rest_contour_toolpath._meaningful_at(state, envelope, end.x, end.y, tip_z)
    dx = end.x - start.x
    projection = (center_x - start.x) * dx / (dx * dx)
    half = math.sqrt((envelope.radius * envelope.radius) / (dx * dx))
    assert max(0.0, projection - half) < 1.0
    assert min(1.0, projection + half) == 1.0
    assert not rest_contour_toolpath._line_is_clear(state, envelope, start, end, tip_z)


@pytest.mark.parametrize(("profile_points", "gap", "segment"), (
    (((10, 10), (80, 10), (80, 35), (45, 35), (45, 80), (10, 80)), (1, 0.30, 0.70), 1),
    (((10, 10), (90, 10), (90, 80), (58, 80), (58, 30), (42, 30), (42, 80), (10, 80)), (2, 0.25, 0.75), 2),
))
def test_real_concave_and_narrow_channel_profiles_execute_forward(
    profile_points, gap, segment,
) -> None:
    inputs = _positive_inputs(profile_points=profile_points, producer_gaps=(gap,))
    plan = plan_rest_contour_residual(inputs)
    assert hasattr(plan, "layers")
    assert all({fragment.segment_index for fragment in layer.fragments} == {segment} for layer in plan.layers)
    prepared = prepare_rest_contour_phase_b(RestContourPhaseBExecutionContext(inputs, plan))
    assert isinstance(prepared, RestContourPhaseBPrepared)
    candidate = generate_rest_contour_phase_b(prepared)
    _assert_analytic_non_cutting_law(candidate)
    cuts = tuple(event for event in candidate.artifact.events
                 if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING)
    assert cuts
    assert all(event.motion_class is MotionClass.CUTTING for event in cuts if event.engagement)


def test_same_registered_reservation_is_exactly_deterministic_and_setup_revision_changes_only_full_artifact_identity(tmp_path) -> None:
    base = _r270_inputs()
    inputs = _positive_inputs(base_inputs=base)
    plan = plan_rest_contour_residual(inputs)
    context = RestContourPhaseBExecutionContext(inputs, plan)
    prepared = prepare_rest_contour_phase_b(context)
    assert isinstance(prepared, RestContourPhaseBPrepared)
    first = generate_rest_contour_phase_b(prepared)
    second = generate_rest_contour_phase_b(prepared)
    assert first.artifact == second.artifact
    assert first.successor_state == second.successor_state
    revised_inputs = _positive_inputs(base_inputs=base, setup_revision=1)
    revised_plan = plan_rest_contour_residual(revised_inputs)
    revised_context = RestContourPhaseBExecutionContext(revised_inputs, revised_plan)
    revised_prepared = prepare_rest_contour_phase_b(revised_context)
    assert isinstance(revised_prepared, RestContourPhaseBPrepared)
    revised = generate_rest_contour_phase_b(revised_prepared)
    assert material_state_setup_fingerprint(prepared.setup) == material_state_setup_fingerprint(revised_prepared.setup)
    assert first.artifact.setup_revision != revised.artifact.setup_revision
    assert first.artifact.artifact_fingerprint != revised.artifact.artifact_fingerprint
    assert first.artifact.artifact_id != revised.artifact.artifact_id
    first_publication = publish_rest_contour_phase_b(first, current_context=context, project_root=tmp_path)
    revised_publication = publish_rest_contour_phase_b(revised, current_context=revised_context, project_root=tmp_path)
    assert first_publication.artifact_metadata.relative_path != revised_publication.artifact_metadata.relative_path


def test_candidate_replace_and_successor_splices_fail_before_persistence(tmp_path) -> None:
    context, prepared = _positive_prepared()
    candidate = generate_rest_contour_phase_b(prepared)

    class ArtifactStore:
        def __init__(self) -> None:
            self.calls = 0
        def publish(self, project_root, artifact):
            self.calls += 1
            raise AssertionError("invalid candidate reached artifact store")

    class StateStore:
        def __init__(self) -> None:
            self.calls = 0
        def write(self, project_root, state):
            self.calls += 1
            raise AssertionError("invalid candidate reached state store")

    for altered in (replace(candidate),):
        artifacts, states = ArtifactStore(), StateStore()
        with pytest.raises(RestContourValidationError) as error:
            publish_rest_contour_phase_b(altered, current_context=context, project_root=tmp_path,
                                         artifact_store=artifacts, material_state_store=states)
        assert error.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID
        assert artifacts.calls == states.calls == 0

    object.__setattr__(candidate.successor_state, "engine_version", "forged")
    artifacts, states = ArtifactStore(), StateStore()
    with pytest.raises(RestContourValidationError) as error:
        publish_rest_contour_phase_b(candidate, current_context=context, project_root=tmp_path,
                                     artifact_store=artifacts, material_state_store=states)
    assert error.value.code is RestContourDiagnosticCode.SUCCESSOR_INVALID
    assert artifacts.calls == states.calls == 0


def test_direct_copy_and_coherent_candidate_splices_are_unregistered_before_stores(tmp_path) -> None:
    context, prepared = _positive_prepared()
    candidate = generate_rest_contour_phase_b(prepared)

    class ArtifactStore:
        def __init__(self) -> None:
            self.calls = 0
        def publish(self, project_root, artifact):
            self.calls += 1
            raise AssertionError("unregistered candidate reached artifact store")

    class StateStore:
        def __init__(self) -> None:
            self.calls = 0
        def write(self, project_root, state):
            self.calls += 1
            raise AssertionError("unregistered candidate reached state store")

    direct = RestContourPhaseBCandidate(candidate.prepared, candidate.artifact,
        candidate.successor_state, candidate.successor_provenance)
    for altered in (direct, copy(candidate)):
        artifacts, states = ArtifactStore(), StateStore()
        with pytest.raises(RestContourValidationError) as error:
            publish_rest_contour_phase_b(altered, current_context=context, project_root=tmp_path,
                                         artifact_store=artifacts, material_state_store=states)
        assert error.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID
        assert artifacts.calls == states.calls == 0


def test_prepared_token_and_setup_coherent_splices_never_reach_either_store(tmp_path) -> None:
    context, prepared = _positive_prepared()
    _other_context, other = _positive_prepared()
    candidate = generate_rest_contour_phase_b(prepared)

    class ArtifactStore:
        def __init__(self) -> None:
            self.calls = 0
        def publish(self, project_root, artifact):
            self.calls += 1
            raise AssertionError("untrusted reservation reached artifact store")

    class StateStore:
        def __init__(self) -> None:
            self.calls = 0
        def write(self, project_root, state):
            self.calls += 1
            raise AssertionError("untrusted reservation reached state store")

    token_splice = RestContourPhaseBPrepared(
        prepared.plan, prepared.predecessor_state, prepared.setup, prepared.base_operation,
        other.computing_operation, prepared.input_fingerprint, other.computation_token,
        prepared.setup_payload_fingerprint,
    )
    setup_splice = RestContourPhaseBPrepared(
        prepared.plan, prepared.predecessor_state, other.setup, prepared.base_operation,
        prepared.computing_operation, prepared.input_fingerprint, prepared.computation_token,
        other.setup_payload_fingerprint,
    )
    for altered in (copy(prepared), token_splice, setup_splice):
        with pytest.raises(RestContourValidationError) as error:
            generate_rest_contour_phase_b(altered)
        assert error.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID
        direct = RestContourPhaseBCandidate(altered, candidate.artifact, candidate.successor_state,
                                            candidate.successor_provenance)
        artifacts, states = ArtifactStore(), StateStore()
        with pytest.raises(RestContourValidationError) as error:
            publish_rest_contour_phase_b(direct, current_context=context, project_root=tmp_path,
                                         artifact_store=artifacts, material_state_store=states)
        assert error.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID
        assert artifacts.calls == states.calls == 0


def test_registered_candidate_artifact_provenance_and_successor_splices_reject_before_stores(tmp_path) -> None:
    class ArtifactStore:
        def __init__(self) -> None:
            self.calls = 0
        def publish(self, project_root, artifact):
            self.calls += 1
            raise AssertionError("tampered candidate reached artifact store")

    class StateStore:
        def __init__(self) -> None:
            self.calls = 0
        def write(self, project_root, state):
            self.calls += 1
            raise AssertionError("tampered candidate reached state store")

    def assert_rejected(candidate, context) -> None:
        artifacts, states = ArtifactStore(), StateStore()
        with pytest.raises(RestContourValidationError) as error:
            publish_rest_contour_phase_b(candidate, current_context=context, project_root=tmp_path,
                                         artifact_store=artifacts, material_state_store=states)
        assert error.value.code in {
            RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
            RestContourDiagnosticCode.SUCCESSOR_INVALID,
        }
        assert artifacts.calls == states.calls == 0

    context, prepared = _positive_prepared()
    artifact_splice = generate_rest_contour_phase_b(prepared)
    object.__setattr__(artifact_splice.artifact, "operation_revision", Revision(99))
    assert_rejected(artifact_splice, context)

    context, prepared = _positive_prepared()
    provenance_splice = generate_rest_contour_phase_b(prepared)
    object.__setattr__(provenance_splice, "successor_provenance", RestContourPhaseBSuccessorProvenance(
        provenance_splice.successor_provenance.parent_fingerprint,
        provenance_splice.successor_provenance.parent_content_integrity_fingerprint,
        provenance_splice.successor_provenance.setup_fingerprint,
        compute_material_removal_fingerprint(provenance_splice.artifact),
        provenance_splice.successor_provenance.removed_volume + 1.0,
    ))
    assert_rejected(provenance_splice, context)

    context, prepared = _positive_prepared()
    successor_object_splice = generate_rest_contour_phase_b(prepared)
    object.__setattr__(successor_object_splice, "successor_state", replace(
        successor_object_splice.successor_state,
        remaining_volume=successor_object_splice.successor_state.remaining_volume,
    ))
    assert_rejected(successor_object_splice, context)

    for field, replacement in (
        ("engine_version", "forged-engine"),
        ("fingerprint", None),
    ):
        context, prepared = _positive_prepared()
        successor_splice = generate_rest_contour_phase_b(prepared)
        value = replacement
        if field == "fingerprint":
            value = successor_splice.prepared.predecessor_state.fingerprint
        object.__setattr__(successor_splice.successor_state, field, value)
        assert_rejected(successor_splice, context)
