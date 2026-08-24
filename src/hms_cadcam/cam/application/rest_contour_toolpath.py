"""Fail-closed Phase B execution for the R271 Rest Contour residual plan.

The module deliberately has no aggregate/service wiring. Its two boundaries
are an immutable prepared reservation and a fully sealed in-memory candidate;
durable stores are only reached after both are reconstructed and revalidated.
"""

from __future__ import annotations

import math
from dataclasses import fields, is_dataclass, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid5

from hms_cadcam.cam.application.rest_contour_geometry import (
    NoRestContourMaterial, RestContourGeometryInputs, RestContourGeometryResult,
    RestContourResidualPlan, plan_rest_contour_residual,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus, BoxStock, ContentFingerprint, DependencyFingerprint,
    Operation, Point3, Setup, ToolpathArtifactId, Vector3,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.operation import ComputationToken
from hms_cadcam.cam.domain.rest_contour import (
    RestContourDiagnosticCode, RestContourValidationError,
)
from hms_cadcam.cam.material_state import (
    MATERIAL_STATE_ENGINE_VERSION, CutterEnvelope, MaterialState, MaterialStateLoadStatus, MaterialStatePrecisionPolicy,
    MaterialStateStatus, MaterialStateStore, calculate_material_state,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.material_state.core import MaterialStateVerificationOrigin
from hms_cadcam.cam.persistence.artifact_store import ToolpathArtifactStore
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import ToolpathArtifactMetadata
from hms_cadcam.cam.toolpath import (
    FeedMode, MotionClass, Pose, SpindleState, ToolpathArtifact,
    ToolpathBuilder, compute_material_removal_fingerprint, publish_toolpath,
)
from hms_cadcam.cam.toolpath.codec import artifact_to_dict


_ALGORITHM_VERSION = 1
_ARTIFACT_NAMESPACE = UUID("8eccecf3-8b05-4a0d-b86b-307a34148bd2")
_PROVISIONAL_ARTIFACT_ID = ToolpathArtifactId(UUID("00000000-0000-0000-0000-000000000001"))
_MAX_EVENTS = 100_000
_TOLERANCE = 1.0e-8

@dataclass(frozen=True, slots=True)
class RestContourPhaseBExecutionContext:
    phase_a_inputs: RestContourGeometryInputs
    plan: RestContourResidualPlan | NoRestContourMaterial


@dataclass(frozen=True, slots=True)
class RestContourPhaseBNoRestMaterial:
    outcome: NoRestContourMaterial


@dataclass(frozen=True, slots=True)
class RestContourPhaseBPrepared:
    """Sealed reservation. All fields are re-derived before consumption."""

    plan: RestContourResidualPlan
    predecessor_state: MaterialState
    setup: Setup
    base_operation: Operation
    computing_operation: Operation
    input_fingerprint: DependencyFingerprint
    computation_token: ComputationToken
    setup_payload_fingerprint: ContentFingerprint
    prepared_fingerprint: ContentFingerprint = field(init=False)
    _factory_seal: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "prepared_fingerprint", _prepared_fingerprint(self))
        object.__setattr__(self, "_factory_seal", object())


@dataclass(frozen=True, slots=True)
class RestContourPhaseBSuccessorProvenance:
    parent_fingerprint: ContentFingerprint
    parent_content_integrity_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    toolpath_fingerprint: ContentFingerprint
    removed_volume: float


@dataclass(frozen=True, slots=True)
class RestContourPhaseBCandidate:
    prepared: RestContourPhaseBPrepared
    artifact: ToolpathArtifact
    successor_state: MaterialState
    successor_provenance: RestContourPhaseBSuccessorProvenance
    candidate_chain_fingerprint: ContentFingerprint = field(init=False)
    _factory_seal: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_chain_fingerprint", _candidate_chain_fingerprint(self))
        object.__setattr__(self, "_factory_seal", object())


@dataclass(frozen=True, slots=True)
class RestContourPhaseBPublication:
    operation: Operation
    artifact_metadata: ToolpathArtifactMetadata
    artifact: ToolpathArtifact
    successor_state: MaterialState


@dataclass(frozen=True, slots=True)
class _CutSpan:
    """Internal, normalized Phase-A cutting authority for one LINE segment."""

    segment_index: int
    start: float
    end: float
    start_point: Point3
    end_point: Point3
    fragment_fingerprint: ContentFingerprint
    region_fingerprint: ContentFingerprint
    cells: tuple[tuple[int, int], ...]


def _fail(code: RestContourDiagnosticCode, message: str) -> None:
    raise RestContourValidationError(code, message)


def _cancelled(cancellation: Callable[[], bool] | None) -> None:
    if cancellation is not None and cancellation():
        _fail(RestContourDiagnosticCode.CANCELLED, "Rest Contour Phase B was cancelled")


def _same_point(first: Point3, second: Point3) -> bool:
    return (first.unit is second.unit and abs(first.x - second.x) <= _TOLERANCE
            and abs(first.y - second.y) <= _TOLERANCE and abs(first.z - second.z) <= _TOLERANCE)


def _pose(point: Point3, z: float) -> Pose:
    return Pose(Point3(point.x, point.y, z, point.unit), Vector3(0.0, 0.0, 1.0))


def _phase_a_result(context: RestContourPhaseBExecutionContext) -> RestContourGeometryResult:
    if not isinstance(context, RestContourPhaseBExecutionContext):
        raise TypeError("Rest Contour Phase B context is invalid")
    result = plan_rest_contour_residual(context.phase_a_inputs)
    supplied = context.plan
    if isinstance(supplied, NoRestContourMaterial):
        if not isinstance(result, NoRestContourMaterial) or result.fingerprint != supplied.fingerprint:
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour no-rest outcome is no longer current")
        return result
    if (not isinstance(supplied, RestContourResidualPlan) or not isinstance(result, RestContourResidualPlan)
            or result.fingerprint != supplied.fingerprint
            or result.authority.fingerprint != supplied.authority.fingerprint):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour Phase A plan or authority has drifted")
    return result


def _setup_stock_is_exact(setup: Setup, expected_stock: BoxStock | None = None) -> bool:
    return (isinstance(setup, Setup) and isinstance(setup.stock, BoxStock)
            and setup.stock.frame == setup.wcs
            and (expected_stock is None or setup.stock == expected_stock))


def _predecessor(plan: RestContourResidualPlan, inputs: RestContourGeometryInputs) -> MaterialState:
    candidate = inputs.foundation.material.candidate
    if candidate is None:
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour Phase B has no material predecessor")
    state = candidate.state
    expected_stock = ContentFingerprint.from_payload(inputs.setup.stock.to_dict())
    expected_setup = material_state_setup_fingerprint(inputs.setup)
    if (not _setup_stock_is_exact(inputs.setup, inputs.stock)
            or state.fingerprint != plan.authority.parent_state_fingerprint
            or state.content_integrity_fingerprint != plan.authority.parent_state_content_integrity_fingerprint
            or state.stock_fingerprint != expected_stock or state.setup_fingerprint != expected_setup
            or state.engine_version != MATERIAL_STATE_ENGINE_VERSION
            or state.precision != MaterialStatePrecisionPolicy()
            or state.verification_origin not in {MaterialStateVerificationOrigin.TRUSTED_CALCULATED,
                                                 MaterialStateVerificationOrigin.TRUSTED_PERSISTED}
            or not state.content_is_verified):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
              "Rest Contour predecessor semantic identity or content seal is untrusted")
    return state


def _operation(plan: RestContourResidualPlan, inputs: RestContourGeometryInputs) -> Operation:
    operations = {item.operation_id: item for item in inputs.setup.operation_tree.operations}
    operation = operations.get(plan.authority.consumer_operation_id)
    if (operation is None or operation.operation_id != plan.authority.consumer_operation_id
            or operation.revision != plan.authority.consumer_operation_revision
            or operation.setup_id != inputs.setup.setup_id or not operation.enabled):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour consumer operation is no longer current")
    return operation


def _input_fingerprint(plan: RestContourResidualPlan, predecessor: MaterialState,
                       setup: Setup) -> DependencyFingerprint:
    return DependencyFingerprint.from_payload({
        "format": "HMS_CAM_REST_CONTOUR_PHASE_B_INPUT", "format_version": _ALGORITHM_VERSION,
        "phase_a_plan": plan.fingerprint.to_dict(), "phase_a_authority": plan.authority.fingerprint.to_dict(),
        "predecessor": predecessor.fingerprint.to_dict(),
        "predecessor_content": predecessor.content_integrity_fingerprint.to_dict(),
        "consumer_operation_id": str(plan.authority.consumer_operation_id),
        "consumer_operation_revision": plan.authority.consumer_operation_revision.to_dict(),
        "parameters": plan.authority.parameters.fingerprint.to_dict(),
        "profile": plan.authority.profile_path.source_fingerprint.to_dict(),
        "semantic_setup": material_state_setup_fingerprint(setup).to_dict(),
        "stock": ContentFingerprint.from_payload(setup.stock.to_dict()).to_dict(),
        "wcs": ContentFingerprint.from_payload(setup.wcs.to_dict()).to_dict(),
        "tool": plan.authority.tool.content_fingerprint.to_dict(),
        "assembly": ContentFingerprint.from_payload(plan.authority.tool_assembly.to_dict()).to_dict(),
        "machine": plan.authority.machine.content_fingerprint.to_dict(), "algorithm_version": _ALGORITHM_VERSION,
    })


def _prepared_payload(value: RestContourPhaseBPrepared) -> dict[str, object]:
    return {
        "format": "HMS_CAM_REST_CONTOUR_PHASE_B_PREPARED", "format_version": 1,
        # ``RestContourResidualPlan.fingerprint`` is a construction-time
        # cache.  The lifecycle seal must bind the complete current plan graph
        # as frozen dataclasses can still be altered through object.__setattr__.
        "plan": _plan_authority_seal(value.plan).to_dict(),
        "plan_cached_fingerprint": value.plan.fingerprint.to_dict(),
        "plan_parent": value.plan.parent_state_fingerprint.to_dict(),
        "plan_authority": value.plan.authority.fingerprint.to_dict(),
        "predecessor": value.predecessor_state.fingerprint.to_dict(),
        "predecessor_seal": value.predecessor_state.content_integrity_fingerprint.to_dict(),
        "setup": value.setup.to_dict(), "setup_payload": value.setup_payload_fingerprint.to_dict(),
        "base_operation": value.base_operation.to_dict(),
        "computing_operation": value.computing_operation.to_dict(),
        "input": value.input_fingerprint.to_dict(),
        "token": {"value": str(value.computation_token.value), "generation": value.computation_token.generation},
    }


def _prepared_fingerprint(value: RestContourPhaseBPrepared) -> ContentFingerprint:
    return ContentFingerprint.from_payload(_prepared_payload(value))


def _canonical_plan_value(value: object) -> object:
    """Encode every plan-authority field without trusting cached ``to_dict`` values."""
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        # The domain rejects non-finite geometry; repr nevertheless preserves
        # signed zero and is deterministic for the remaining IEEE values.
        if not math.isfinite(value):
            raise ValueError("Rest Contour plan contains non-finite authority")
        return {"__float__": value.hex()}
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, Enum):
        return {"__enum__": f"{type(value).__module__}.{type(value).__qualname__}",
                "value": _canonical_plan_value(value.value)}
    if isinstance(value, UUID):
        return {"__uuid__": str(value)}
    if isinstance(value, tuple):
        return [ _canonical_plan_value(item) for item in value ]
    if isinstance(value, list):
        return [ _canonical_plan_value(item) for item in value ]
    if isinstance(value, dict):
        items = [(_canonical_plan_value(key), _canonical_plan_value(item)) for key, item in value.items()]
        return {"__dict__": sorted(items, key=lambda item: repr(item[0]))}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__dataclass__": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": [[item.name, _canonical_plan_value(getattr(value, item.name))]
                       for item in fields(value)],
        }
    raise TypeError(f"Rest Contour plan contains unsupported authority type: {type(value)!r}")


def _plan_authority_seal(plan: RestContourResidualPlan) -> ContentFingerprint:
    """Return a recursive exact-byte seal for the self-contained Phase-A plan."""
    if not isinstance(plan, RestContourResidualPlan):
        raise TypeError("Rest Contour Phase A plan is invalid")
    return ContentFingerprint.from_payload({
        "format": "HMS_CAM_REST_CONTOUR_PHASE_A_PLAN_DEEP_SEAL", "format_version": 1,
        "plan": _canonical_plan_value(plan),
    })


def _validate_prepared_integrity(value: RestContourPhaseBPrepared) -> None:
    if not isinstance(value, RestContourPhaseBPrepared):
        raise TypeError("Rest Contour Phase B prepared execution is invalid")
    plan, predecessor, setup, base, computing = (value.plan, value.predecessor_state, value.setup,
                                                   value.base_operation, value.computing_operation)
    expected_input = _input_fingerprint(plan, predecessor, setup)
    expected_stock = ContentFingerprint.from_payload(setup.stock.to_dict()) if isinstance(setup, Setup) else None
    expected_setup = material_state_setup_fingerprint(setup) if isinstance(setup, Setup) else None
    expected_computing = replace(base, artifact_state=computing.artifact_state) if isinstance(base, Operation) and isinstance(computing, Operation) else None
    state = computing.artifact_state if isinstance(computing, Operation) else None
    if (not isinstance(plan, RestContourResidualPlan) or not isinstance(predecessor, MaterialState)
            or not _setup_stock_is_exact(setup) or not isinstance(base, Operation) or not isinstance(computing, Operation)
            or value.input_fingerprint != expected_input or value.prepared_fingerprint != _prepared_fingerprint(value)
            or value.setup_payload_fingerprint != ContentFingerprint.from_payload(setup.to_dict())
            or plan.parent_state_fingerprint != predecessor.fingerprint
            or plan.authority.parent_state_fingerprint != predecessor.fingerprint
            or plan.authority.parent_state_content_integrity_fingerprint != predecessor.content_integrity_fingerprint
            or plan.stock_fingerprint != expected_stock or plan.setup_fingerprint != expected_setup
            or predecessor.stock_fingerprint != expected_stock or predecessor.setup_fingerprint != expected_setup
            or expected_computing != computing or state is None or state.status is not ArtifactStatus.COMPUTING
            or state.token != value.computation_token or state.input_fingerprint != value.input_fingerprint
            or state.generation != base.artifact_state.generation + 1
            or value.computation_token.generation != state.generation):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour Phase B prepared seal is stale")


def _prepare_rest_contour_phase_b_unsealed(context: RestContourPhaseBExecutionContext) -> RestContourPhaseBPrepared | RestContourPhaseBNoRestMaterial:
    result = _phase_a_result(context)
    if isinstance(result, NoRestContourMaterial):
        return RestContourPhaseBNoRestMaterial(result)
    _cancelled(context.phase_a_inputs.cancellation)
    predecessor = _predecessor(result, context.phase_a_inputs)
    base = _operation(result, context.phase_a_inputs)
    fingerprint = _input_fingerprint(result, predecessor, context.phase_a_inputs.setup)
    try:
        computing_state, token = base.artifact_state.begin(fingerprint)
    except CamValidationError as error:
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, f"Rest Contour computation cannot begin: {error}")
    prepared = RestContourPhaseBPrepared(result, predecessor, context.phase_a_inputs.setup, base,
        replace(base, artifact_state=computing_state), fingerprint, token,
        ContentFingerprint.from_payload(context.phase_a_inputs.setup.to_dict()))
    _validate_prepared_integrity(prepared)
    return prepared


def _artifact_payload_without_id(artifact: ToolpathArtifact) -> dict[str, object]:
    payload = artifact_to_dict(artifact)
    payload.pop("artifact_id")
    return payload


def _derived_artifact_id(provisional: ToolpathArtifact) -> ToolpathArtifactId:
    # Retain token UUID, generation, setup revision, full fingerprint and
    # created_at. Only the self-addressing ID is removed from canonical bytes.
    return ToolpathArtifactId(uuid5(_ARTIFACT_NAMESPACE,
                                    ContentFingerprint.from_payload(_artifact_payload_without_id(provisional)).digest))


def _point_on_loop(plan: RestContourResidualPlan, coordinate: float) -> Point3:
    count = len(plan.center_loop.segments)
    whole = math.floor(coordinate)
    index = int(whole) % count
    parameter = coordinate - whole
    if parameter <= _TOLERANCE and coordinate > 0.0:
        index = (index - 1) % count
        parameter = 1.0
    segment = plan.center_loop.segments[index]
    return Point3(segment.start.x + (segment.end.x - segment.start.x) * parameter,
                  segment.start.y + (segment.end.y - segment.start.y) * parameter,
                  segment.start.z + (segment.end.z - segment.start.z) * parameter,
                  segment.start.unit)


def _normalized_spans(plan: RestContourResidualPlan, layer) -> tuple[_CutSpan, ...]:
    """Normalize only exact Phase-A overlaps/touches, retaining source evidence.

    A positive interval between two Phase-A fragments is an entry gap.  It is
    therefore not a numerical nuisance that Phase B may erase with a broad
    tolerance: doing so would authorise cutting material which Phase A did not
    approve.  The planner already emits finite canonical floats, so equality
    (including signed-zero equality) is the only merge predicate here.
    """
    grouped: dict[int, list[object]] = {}
    for bundle in layer.region_fragments:
        for fragment in bundle.fragments:
            grouped.setdefault(fragment.segment_index, []).append(fragment)
    spans: list[_CutSpan] = []
    for index in sorted(grouped):
        fragments = sorted(grouped[index], key=lambda item: (item.start, item.end, item.fingerprint.digest))
        current = [fragments[0]]
        for fragment in fragments[1:]:
            if fragment.start <= max(value.end for value in current):
                current.append(fragment)
            else:
                spans.append(_span_from_fragments(plan, index, current))
                current = [fragment]
        spans.append(_span_from_fragments(plan, index, current))
    return tuple(sorted(spans, key=lambda item: (item.segment_index, item.start, item.end)))


def _fingerprint_scalars(values: list[ContentFingerprint]) -> tuple[ContentFingerprint, ...]:
    """Return deterministic, deduplicated opaque fingerprints.

    Never sort ``to_dict`` payloads: Python dictionaries are deliberately not
    orderable and doing so made a legitimate multi-region residual crash before
    it could be proven safe.  The concrete fingerprint identity is the scalar
    (algorithm, version, digest) tuple.
    """
    unique = {
        (value.algorithm, value.algorithm_version, value.digest): value
        for value in values
    }
    return tuple(unique[key] for key in sorted(unique))


def _point_on_segment(segment, parameter: float) -> Point3:
    """Interpolate one canonical centre-loop LINE at its exact interval bound."""
    return Point3(
        segment.start.x + (segment.end.x - segment.start.x) * parameter,
        segment.start.y + (segment.end.y - segment.start.y) * parameter,
        segment.start.z + (segment.end.z - segment.start.z) * parameter,
        segment.start.unit,
    )


def _span_from_fragments(plan: RestContourResidualPlan, index: int, fragments: list[object]) -> _CutSpan:
    start, end = min(item.start for item in fragments), max(item.end for item in fragments)
    segment = plan.center_loop.segments[index]
    fragment_fingerprints = _fingerprint_scalars([item.fingerprint for item in fragments])
    region_fingerprints = _fingerprint_scalars([item.region_fingerprint for item in fragments])
    return _CutSpan(index, start, end, _point_on_segment(segment, start), _point_on_segment(segment, end),
                    ContentFingerprint.from_payload({"fragments": [item.to_dict() for item in fragment_fingerprints]}),
                    ContentFingerprint.from_payload({"regions": [item.to_dict() for item in region_fingerprints]}),
                    tuple(sorted({cell for item in fragments for cell in item.responsible_cells})))


def _span_components(plan: RestContourResidualPlan, spans: tuple[_CutSpan, ...]) -> tuple[tuple[_CutSpan, ...], ...]:
    """Join spans only across a genuinely touching forward cyclic seam."""
    if not spans:
        return ()
    count = len(plan.center_loop.segments)
    ordered = list(spans)
    components: list[list[_CutSpan]] = [[ordered[0]]]
    for span in ordered[1:]:
        prior = components[-1][-1]
        touching = (prior.segment_index + 1 == span.segment_index and prior.end == 1.0
                    and span.start == 0.0)
        if touching:
            components[-1].append(span)
        else:
            components.append([span])
    if (len(components) > 1 and components[-1][-1].segment_index == count - 1
            and components[-1][-1].end == 1.0
            and components[0][0].segment_index == 0 and components[0][0].start == 0.0):
        components[0] = components.pop() + components[0]
    return tuple(tuple(component) for component in components)


def _meaningful_at(state: MaterialState, envelope: CutterEnvelope, x: float, y: float, tip_z: float) -> bool:
    for row in range(state.height):
        center_y = (row + 0.5) * state.cell_size_y
        for column in range(state.width):
            radius = math.hypot((column + 0.5) * state.cell_size_x - x, center_y - y)
            # The forbidden cutter footprint is an open disk.  Contact at its
            # boundary is not an intersection, but even a 1e-10 penetration
            # must remain forbidden; never shrink this disk by a tolerance.
            if radius >= envelope.radius:
                continue
            if state.top_heights[row * state.width + column] > tip_z + envelope.surface_offset(radius) + state.precision.residual_threshold:
                return True
    return False


def _line_is_clear(state: MaterialState, envelope: CutterEnvelope, start: Point3, end: Point3, tip_z: float) -> bool:
    """Exact cell-disk interval proof for a horizontal link; never sample points."""
    # The endpoint predicate has a stricter authority than the later
    # representation-only interval normalization.  A line whose exact endpoint
    # is inside a meaningful open cutter footprint is material-engaging even if
    # the independently rounded quadratic interval has collapsed at t=0 or
    # t=1.  Check it before any ULP contraction; exact tangency remains legal
    # because _meaningful_at uses the same strict open-disk CutterEnvelope law.
    if (_meaningful_at(state, envelope, start.x, start.y, tip_z)
            or _meaningful_at(state, envelope, end.x, end.y, tip_z)):
        return False
    dx, dy = end.x - start.x, end.y - start.y
    length = math.hypot(dx, dy)
    # A point proof is valid only for an exactly coincident move.  Positive
    # sub-micrometre links must retain their analytic segment authority: their
    # squared length can underflow a tolerance even while crossing material.
    if length == 0.0:
        return not _meaningful_at(state, envelope, start.x, start.y, tip_z)
    direction_x, direction_y = dx / length, dy / length
    for row in range(state.height):
        center_y = (row + 0.5) * state.cell_size_y
        for column in range(state.width):
            maximum = envelope.maximum_removable_radius(target_tip_z=tip_z,
                current_height=state.top_heights[row * state.width + column], threshold=state.precision.residual_threshold)
            if maximum is None:
                continue
            center_x = (column + 0.5) * state.cell_size_x
            offset_x, offset_y = center_x - start.x, center_y - start.y
            projection = offset_x * direction_x + offset_y * direction_y
            perpendicular = offset_x * direction_y - offset_y * direction_x
            # Open-disk interval proof.  Do not subtract a tolerance from the
            # radius or interval: a positive near-tangent penetration is still
            # a material-engaging link and must fail closed.
            # Keep this ratio-safe: squaring a legal subnormal cutter radius
            # turns both terms into zero and incorrectly proves a positive
            # segment clear. ``hypot`` above retained the chord scale.
            if abs(perpendicular) >= maximum:
                continue
            ratio = perpendicular / maximum
            half_distance = maximum * math.sqrt((1.0 - ratio) * (1.0 + ratio))
            lower = max(0.0, (projection - half_distance) / length)
            upper = min(1.0, (projection + half_distance) / length)
            # ``lower``/``upper`` arise from separate floating-point paths.
            # Normalize at a bounded 8-ULP representation envelope so an
            # exact Phase-A tangency cannot become a fake ~1e-15 interval
            # (the planner and this inverse proof each multiply/divide along
            # the segment).  This is intentionally not a geometric tolerance
            # and never subtracts from the cutter radius: a positive 1e-10
            # penetration remains forbidden by the same open-disk predicate.
            for _ in range(8):
                lower = math.nextafter(lower, math.inf)
                upper = math.nextafter(upper, -math.inf)
            if lower < upper:
                return False
    return True


def _gap_route(plan: RestContourResidualPlan, start: float, end: float) -> tuple[Point3, ...]:
    count = len(plan.center_loop.segments)
    while end <= start + _TOLERANCE:
        end += count
    points = [_point_on_loop(plan, start)]
    boundary = math.floor(start) + 1
    while boundary < end - _TOLERANCE:
        points.append(_point_on_loop(plan, float(boundary)))
        boundary += 1
    points.append(_point_on_loop(plan, end))
    return tuple(points)


def _build_artifact(prepared: RestContourPhaseBPrepared, *, cancellation: Callable[[], bool] | None = None) -> ToolpathArtifact:
    """Build only analytically proven LINK/RETRACT moves and approved CUTTING spans."""
    _require_prepared(prepared)
    _cancelled(cancellation)
    plan, parameters, state = prepared.plan, prepared.plan.authority.parameters, prepared.predecessor_state
    envelope = CutterEnvelope.from_tool(plan.authority.tool)
    layers = tuple((layer, _span_components(plan, _normalized_spans(plan, layer))) for layer in plan.layers)
    if not layers or any(not components for _layer, components in layers):
        _fail(RestContourDiagnosticCode.RESIDUAL_INVALID, "Rest Contour Phase B plan has no approved fragments")
    if sum(len(component) for _layer, components in layers for component in components) * 6 + 8 > _MAX_EVENTS:
        _fail(RestContourDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Contour Phase B event limit exceeded")
    first = layers[0][1][0][0]
    builder = ToolpathBuilder(
        artifact_id=_PROVISIONAL_ARTIFACT_ID, operation_id=prepared.computing_operation.operation_id,
        operation_revision=prepared.computing_operation.revision, computation_token=prepared.computation_token,
        input_fingerprint=prepared.input_fingerprint, unit=parameters.unit, setup_id=prepared.setup.setup_id,
        setup_revision=prepared.setup.revision,
        wcs_fingerprint=ContentFingerprint.from_payload(prepared.setup.wcs.to_dict()),
        tool_assembly_id=plan.authority.tool_assembly.assembly_id,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(plan.authority.tool_assembly.to_dict()),
        machine_id=plan.authority.machine.machine_id, machine_fingerprint=plan.authority.machine.content_fingerprint,
        created_at=None,
    )
    try:
        builder.set_initial_pose(_pose(first.start_point, parameters.clearance_height.value))
        builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
        builder.set_spindle(SpindleState.CLOCKWISE, parameters.spindle_speed, provenance="rest_contour.spindle.on")
        component_index = 0
        for layer, components in layers:
            depth = layer.tip_z
            flattened = tuple(span for component in components for span in component)
            for component in components:
                _cancelled(cancellation)
                first_span, last_span = component[0], component[-1]
                start_coordinate = first_span.segment_index + first_span.start
                start_is_clear = not _meaningful_at(
                    state, envelope, first_span.start_point.x, first_span.start_point.y, depth,
                )
                # The Phase-A component is an ordered, forward-only authority.
                # Its exact first clipped endpoint must be a legal entry before
                # *any* motion is emitted.  A clear terminal does not authorize
                # reversing the component: that would change the approved CUT
                # direction and make a material-engaging canonical start appear
                # safe.
                if not start_is_clear:
                    _fail(RestContourDiagnosticCode.ENTRY_UNSAFE,
                          "Rest Contour residual component forward start is material-engaging")
                prior_candidates = [span.segment_index + span.end for span in flattened
                                    if span.segment_index + span.end < start_coordinate - _TOLERANCE]
                if not prior_candidates:
                    prior_candidates = [span.segment_index + span.end - len(plan.center_loop.segments)
                                        for span in flattened]
                prior_end = max(prior_candidates)
                gap = start_coordinate - prior_end
                anchor_coordinate = prior_end + gap / 2.0
                # Re-interpolation is safe for a clearance route interior, but
                # not for the plan-owned cut start.  Retain Phase A's exact
                # float endpoint so the safe-entry LINK ends at the same bytes
                # the first CUT begins from.
                route = _gap_route(plan, anchor_coordinate, start_coordinate)[:-1] + (first_span.start_point,)
                if gap <= _TOLERANCE:
                    _fail(RestContourDiagnosticCode.ENTRY_UNSAFE, "Rest Contour residual union has no positive entry gap")
                anchor = route[0]
                if _meaningful_at(state, envelope, anchor.x, anchor.y, depth):
                    _fail(RestContourDiagnosticCode.ENTRY_UNSAFE, "Rest Contour vertical entry is material-engaging")
                if any(not _line_is_clear(state, envelope, left, right, depth)
                       for left, right in zip(route, route[1:])):
                    _fail(RestContourDiagnosticCode.ENTRY_UNSAFE, "Rest Contour at-depth entry link is material-engaging")
                current = builder.current_pose
                assert current is not None
                clearance_anchor = _pose(anchor, parameters.clearance_height.value)
                if not _line_is_clear(state, envelope, current.position, clearance_anchor.position,
                                      parameters.clearance_height.value):
                    _fail(RestContourDiagnosticCode.ENTRY_UNSAFE, "Rest Contour clearance traverse is material-engaging")
                if not _same_point(current.position, clearance_anchor.position):
                    builder.rapid_to(clearance_anchor, motion_class=MotionClass.LINK,
                                     provenance=f"rest_contour.component.{component_index}.clearance_traverse")
                builder.linear_to(_pose(anchor, depth), parameters.plunge_feed_rate,
                                  motion_class=MotionClass.LINK,
                                  provenance=f"rest_contour.component.{component_index}.safe_entry")
                for route_index, point in enumerate(route[1:]):
                    builder.linear_to(_pose(point, depth), parameters.plunge_feed_rate,
                                      motion_class=MotionClass.LINK,
                                      provenance=f"rest_contour.component.{component_index}.entry_link.{route_index}")
                for span_index, span in enumerate(component):
                    builder.linear_to(_pose(span.end_point, depth), parameters.cutting_feed_rate,
                                      motion_class=MotionClass.CUTTING,
                                      engagement=(("phase", "rest_contour"), ("span", span.fragment_fingerprint.digest)),
                                      provenance=f"rest_contour.component.{component_index}.cut.{span_index}")
                # A terminal can be materially engaged: it follows approved
                # CUTTING and is the only legal origin for the vertical retract.
                terminal = component[-1].end_point
                builder.linear_to(_pose(terminal, parameters.retract_height.value), parameters.plunge_feed_rate,
                                  motion_class=MotionClass.RETRACT,
                                  provenance=f"rest_contour.component.{component_index}.retract")
                builder.rapid_to(_pose(terminal, parameters.clearance_height.value), motion_class=MotionClass.RETRACT,
                                 provenance=f"rest_contour.component.{component_index}.clearance")
                component_index += 1
        builder.set_spindle(SpindleState.OFF, provenance="rest_contour.spindle.off")
        provisional = builder.finalize()
    except RestContourValidationError:
        builder.abort(); raise
    except CamValidationError as error:
        builder.abort(); _fail(RestContourDiagnosticCode.RESIDUAL_INVALID, f"Rest Contour Phase B motion is invalid: {error}")
    artifact = replace(provisional, artifact_id=_derived_artifact_id(provisional))
    if artifact.artifact_id != _derived_artifact_id(artifact):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour artifact self-address is invalid")
    return artifact


def _candidate_chain_fingerprint(candidate: RestContourPhaseBCandidate) -> ContentFingerprint:
    return ContentFingerprint.from_payload({
        "format": "HMS_CAM_REST_CONTOUR_PHASE_B_CANDIDATE_CHAIN", "format_version": 1,
        "prepared": candidate.prepared.prepared_fingerprint.to_dict(),
        "artifact": ContentFingerprint.from_payload(_artifact_payload_without_id(candidate.artifact)).to_dict(),
        "predecessor": candidate.prepared.predecessor_state.fingerprint.to_dict(),
        "predecessor_seal": candidate.prepared.predecessor_state.content_integrity_fingerprint.to_dict(),
        "successor": candidate.successor_state.fingerprint.to_dict(),
        "successor_seal": candidate.successor_state.content_integrity_fingerprint.to_dict(),
        "provenance": {
            "parent": candidate.successor_provenance.parent_fingerprint.to_dict(),
            "parent_seal": candidate.successor_provenance.parent_content_integrity_fingerprint.to_dict(),
            "setup": candidate.successor_provenance.setup_fingerprint.to_dict(),
            "toolpath": candidate.successor_provenance.toolpath_fingerprint.to_dict(),
            "removed_volume": candidate.successor_provenance.removed_volume,
        },
    })


def _validate_successor(candidate: RestContourPhaseBCandidate) -> None:
    prepared, artifact, successor, proof = (candidate.prepared, candidate.artifact,
                                             candidate.successor_state, candidate.successor_provenance)
    predecessor, setup = prepared.predecessor_state, prepared.setup
    expected_stock = ContentFingerprint.from_payload(setup.stock.to_dict())
    expected_setup = material_state_setup_fingerprint(setup)
    tolerance = max(_TOLERANCE, predecessor.precision.tolerance)
    expected_initial = setup.stock.size_x.value * setup.stock.size_y.value * setup.stock.size_z.value
    aspect = setup.stock.size_x.value / setup.stock.size_y.value
    width = max(2, round(predecessor.precision.grid_target * math.sqrt(aspect)))
    height = max(2, round(predecessor.precision.grid_target / math.sqrt(aspect)))
    removed = predecessor.remaining_volume - successor.remaining_volume
    if (not isinstance(proof, RestContourPhaseBSuccessorProvenance)
            or successor.parent_fingerprint != predecessor.fingerprint
            or successor.stock_fingerprint != expected_stock or successor.setup_fingerprint != expected_setup
            or successor.unit is not artifact.unit or successor.engine_version != MATERIAL_STATE_ENGINE_VERSION
            or successor.precision != MaterialStatePrecisionPolicy() or successor.precision != predecessor.precision
            or successor.width != width or successor.height != height
            or successor.cell_size_x != setup.stock.size_x.value / width
            or successor.cell_size_y != setup.stock.size_y.value / height
            or abs(successor.initial_volume - expected_initial) > tolerance
            or successor.status is not MaterialStateStatus.COMPLETE
            or successor.verification_origin is not MaterialStateVerificationOrigin.TRUSTED_CALCULATED
            or not successor.content_is_verified
            or successor.toolpath_fingerprint != compute_material_removal_fingerprint(artifact)
            or successor.remaining_volume > predecessor.remaining_volume + tolerance
            or any(after > before + tolerance for before, after in zip(predecessor.top_heights, successor.top_heights, strict=True))
            or not math.isfinite(removed) or removed <= tolerance
            or proof.parent_fingerprint != predecessor.fingerprint
            or proof.parent_content_integrity_fingerprint != predecessor.content_integrity_fingerprint
            or proof.setup_fingerprint != expected_setup
            or proof.toolpath_fingerprint != successor.toolpath_fingerprint
            or not math.isfinite(proof.removed_volume) or abs(proof.removed_volume - removed) > tolerance):
        _fail(RestContourDiagnosticCode.SUCCESSOR_INVALID, "Rest Contour successor or provenance seal is invalid")
    try:
        replay = calculate_material_state(stock=setup.stock, artifact=artifact, tool=prepared.plan.authority.tool,
            parent=predecessor, setup_fingerprint=expected_setup, precision=MaterialStatePrecisionPolicy()).state
    except CamValidationError as error:
        _fail(RestContourDiagnosticCode.SUCCESSOR_INVALID,
              f"Rest Contour successor authoritative replay failed: {error}")
    if replay != successor:
        _fail(RestContourDiagnosticCode.SUCCESSOR_INVALID,
              "Rest Contour successor does not exactly match authoritative replay")


def _validate_candidate_integrity(candidate: RestContourPhaseBCandidate, *, rebuild: bool) -> None:
    if not isinstance(candidate, RestContourPhaseBCandidate):
        raise TypeError("Rest Contour Phase B candidate is invalid")
    _validate_prepared_integrity(candidate.prepared)
    if rebuild and candidate.artifact != _build_artifact(candidate.prepared):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour artifact differs from the prepared motion")
    artifact = candidate.artifact
    if (artifact.source_operation_id != candidate.prepared.computing_operation.operation_id
            or artifact.operation_revision != candidate.prepared.computing_operation.revision
            or artifact.computation_token != candidate.prepared.computation_token
            or artifact.input_fingerprint != candidate.prepared.input_fingerprint
            or artifact.setup_id != candidate.prepared.setup.setup_id
            or artifact.setup_revision != candidate.prepared.setup.revision
            or artifact.wcs_fingerprint != ContentFingerprint.from_payload(candidate.prepared.setup.wcs.to_dict())
            or artifact.artifact_id != _derived_artifact_id(artifact)):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour artifact provenance seal is invalid")
    _validate_successor(candidate)
    if candidate.candidate_chain_fingerprint != _candidate_chain_fingerprint(candidate):
        _fail(RestContourDiagnosticCode.SUCCESSOR_INVALID, "Rest Contour candidate chain seal is invalid")


def _generate_rest_contour_phase_b_unsealed(prepared: RestContourPhaseBPrepared,
                                            *, cancellation: Callable[[], bool] | None = None) -> RestContourPhaseBCandidate:
    _validate_prepared_integrity(prepared)
    callback = cancellation
    _cancelled(callback)
    artifact = _build_artifact(prepared, cancellation=callback)
    latched = False
    def calculator_cancellation() -> bool:
        nonlocal latched
        if callback is not None and callback():
            latched = True
        return latched
    _cancelled(callback)
    try:
        removal = calculate_material_state(stock=prepared.setup.stock, artifact=artifact,
            tool=prepared.plan.authority.tool, parent=prepared.predecessor_state,
            setup_fingerprint=material_state_setup_fingerprint(prepared.setup), cancellation=calculator_cancellation)
    except CamValidationError as error:
        if latched:
            _fail(RestContourDiagnosticCode.CANCELLED, "Rest Contour Phase B was cancelled during successor calculation")
        _fail(RestContourDiagnosticCode.SUCCESSOR_INVALID, f"Rest Contour successor calculation failed: {error}")
    _cancelled(callback)
    successor = removal.state
    incremental = prepared.predecessor_state.remaining_volume - successor.remaining_volume
    candidate = RestContourPhaseBCandidate(prepared, artifact, successor,
        RestContourPhaseBSuccessorProvenance(prepared.predecessor_state.fingerprint,
            prepared.predecessor_state.content_integrity_fingerprint,
            material_state_setup_fingerprint(prepared.setup), successor.toolpath_fingerprint, incremental))
    _cancelled(callback)
    return candidate


def _fresh_prewrite(context: RestContourPhaseBExecutionContext, prepared: RestContourPhaseBPrepared) -> None:
    # Recheck the non-serializable reservation, including the recursive plan
    # seal, before deriving fresh Phase-A authority or touching either store.
    _require_prepared(prepared)
    result = _phase_a_result(context)
    if not isinstance(result, RestContourResidualPlan):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour Phase B work became a no-rest outcome")
    predecessor = _predecessor(result, context.phase_a_inputs)
    base = _operation(result, context.phase_a_inputs)
    current_setup = context.phase_a_inputs.setup
    if (result.fingerprint != prepared.plan.fingerprint
            or _plan_authority_seal(result) != _plan_authority_seal(prepared.plan)
            or predecessor != prepared.predecessor_state
            or base != prepared.base_operation
            or prepared.setup != current_setup
            or prepared.setup_payload_fingerprint != ContentFingerprint.from_payload(current_setup.to_dict())
            or prepared.setup.revision != current_setup.revision
            or prepared.setup.operation_tree.to_dict() != current_setup.operation_tree.to_dict()
            or _input_fingerprint(result, predecessor, current_setup) != prepared.input_fingerprint):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour Phase B authority drifted before persistence")


def _install_phase_b_seal_boundary(prepare_unsealed, generate_unsealed, validate_prepared_integrity,
                                   validate_candidate_integrity):
    """Close lifecycle-only registrars over their identity registry.

    The registrars themselves intentionally never escape this closure.  A
    dataclass constructor, ``copy``/``replace``, or a coherent field splice can
    therefore construct inspectable values but cannot mint a reservation or a
    candidate eligible for generation/publication.
    """
    import threading

    lock = threading.RLock()
    prepared_records: dict[int, tuple[RestContourPhaseBPrepared, object, ContentFingerprint, ContentFingerprint]] = {}
    candidate_records: dict[int, tuple[RestContourPhaseBCandidate, RestContourPhaseBPrepared, object, ContentFingerprint]] = {}

    def prepared_current(value: RestContourPhaseBPrepared) -> bool:
        try:
            record = prepared_records.get(id(value))
            return (record is not None and record[0] is value and record[1] is value._factory_seal
                    and record[2] == value.prepared_fingerprint
                    and record[3] == _plan_authority_seal(value.plan)
                    and value.prepared_fingerprint == _prepared_fingerprint(value))
        except (AttributeError, TypeError, ValueError):
            return False

    def require_prepared(value: RestContourPhaseBPrepared) -> None:
        if not isinstance(value, RestContourPhaseBPrepared):
            raise TypeError("Rest Contour Phase B prepared execution is invalid")
        with lock:
            registered = prepared_current(value)
        if not registered:
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                  "Rest Contour Phase B prepared reservation was not minted by this process")
        try:
            validate_prepared_integrity(value)
        except RestContourValidationError:
            raise
        except (AttributeError, TypeError, ValueError) as error:
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                  f"Rest Contour Phase B prepared authority is invalid: {error}")

    def candidate_current(value: RestContourPhaseBCandidate) -> bool:
        try:
            record = candidate_records.get(id(value))
            return (record is not None and record[0] is value and record[1] is value.prepared
                    and record[2] is value._factory_seal
                    and record[3] == value.candidate_chain_fingerprint
                    and value.candidate_chain_fingerprint == _candidate_chain_fingerprint(value))
        except (AttributeError, TypeError, ValueError):
            return False

    def validate_candidate(value: RestContourPhaseBCandidate, *, rebuild: bool) -> None:
        if not isinstance(value, RestContourPhaseBCandidate):
            raise TypeError("Rest Contour Phase B candidate is invalid")
        with lock:
            registered = candidate_current(value)
        if not registered:
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                  "Rest Contour Phase B candidate was not minted by this process")
        require_prepared(value.prepared)
        try:
            validate_candidate_integrity(value, rebuild=rebuild)
        except RestContourValidationError:
            raise
        except (AttributeError, TypeError, ValueError) as error:
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                  f"Rest Contour Phase B candidate authority is invalid: {error}")

    def prepare(context: RestContourPhaseBExecutionContext) -> RestContourPhaseBPrepared | RestContourPhaseBNoRestMaterial:
        value = prepare_unsealed(context)
        if isinstance(value, RestContourPhaseBNoRestMaterial):
            return value
        with lock:
            prepared_records[id(value)] = (
                value, value._factory_seal, value.prepared_fingerprint, _plan_authority_seal(value.plan),
            )
        require_prepared(value)
        return value

    def generate(prepared: RestContourPhaseBPrepared,
                 *, cancellation: Callable[[], bool] | None = None) -> RestContourPhaseBCandidate:
        require_prepared(prepared)
        value = generate_unsealed(prepared, cancellation=cancellation)
        with lock:
            candidate_records[id(value)] = (
                value, prepared, value._factory_seal, value.candidate_chain_fingerprint,
            )
        validate_candidate(value, rebuild=False)
        return value

    return prepare, generate, validate_candidate, require_prepared


prepare_rest_contour_phase_b, generate_rest_contour_phase_b, _validate_candidate, _require_prepared = _install_phase_b_seal_boundary(
    _prepare_rest_contour_phase_b_unsealed,
    _generate_rest_contour_phase_b_unsealed,
    _validate_prepared_integrity,
    _validate_candidate_integrity,
)
del _install_phase_b_seal_boundary
del _prepare_rest_contour_phase_b_unsealed, _generate_rest_contour_phase_b_unsealed
del _validate_candidate_integrity


def publish_rest_contour_phase_b(candidate: RestContourPhaseBCandidate, *, current_context: RestContourPhaseBExecutionContext,
                                 project_root: Path, artifact_store: ToolpathArtifactStore | None = None,
                                 material_state_store: MaterialStateStore | None = None) -> RestContourPhaseBPublication:
    if not isinstance(project_root, Path):
        raise TypeError("Rest Contour Phase B publication inputs are invalid")
    # All failure-prone checks occur before the in-memory transition/store calls.
    _fresh_prewrite(current_context, candidate.prepared)
    _validate_candidate(candidate, rebuild=True)
    _cancelled(current_context.phase_a_inputs.cancellation)
    memory = publish_toolpath(candidate.prepared.computing_operation, candidate.artifact,
                              candidate.prepared.computation_token, candidate.prepared.input_fingerprint)
    if not memory.accepted or memory.artifact is None:
        _fail(RestContourDiagnosticCode.PUBLICATION_FAILED,
              f"Rest Contour in-memory publication was rejected: {memory.reason or 'unknown'}")
    artifacts, states = artifact_store or ToolpathArtifactStore(), material_state_store or MaterialStateStore()
    try:
        metadata = artifacts.publish(project_root, candidate.artifact)
        artifact_readback = artifacts.load(project_root, metadata)
        if artifact_readback != candidate.artifact:
            _fail(RestContourDiagnosticCode.PUBLICATION_FAILED, "Rest Contour artifact readback does not match the candidate")
        states.write(project_root, candidate.successor_state)
        state_readback = states.load(project_root, candidate.successor_state.fingerprint)
    except (OSError, CamValidationError, ToolpathArtifactStoreError) as error:
        _fail(RestContourDiagnosticCode.PUBLICATION_FAILED, f"Rest Contour durable publication failed: {error}")
    state = state_readback.state
    if (state_readback.status is not MaterialStateLoadStatus.VALID or state is None
            or state.verification_origin is not MaterialStateVerificationOrigin.TRUSTED_PERSISTED
            or state != candidate.successor_state
            or state.fingerprint != candidate.successor_state.fingerprint
            or state.content_integrity_fingerprint != candidate.successor_state.content_integrity_fingerprint
            or state.parent_fingerprint != candidate.prepared.predecessor_state.fingerprint):
        _fail(RestContourDiagnosticCode.PUBLICATION_FAILED, "Rest Contour successor state readback is invalid")
    return RestContourPhaseBPublication(memory.operation, metadata, artifact_readback, state)
