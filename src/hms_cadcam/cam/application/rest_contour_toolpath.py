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
from hms_cadcam.cam.application.rest_machining_safety import (
    cutter_engages_material_at,
    horizontal_segment_is_clear,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus, BoxStock, ContentFingerprint, DependencyFingerprint, DirtyReason,
    Operation, Point3, Setup, ToolpathArtifactId, Vector3,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.operation import ComputationToken
from hms_cadcam.cam.domain.rest_contour import (
    RestContourDiagnosticCode,
    RestContourParameters,
    RestContourValidationError,
)
from hms_cadcam.cam.material_state import (
    MATERIAL_STATE_ENGINE_VERSION, CutterEnvelope, MaterialState, MaterialStateLoadStatus, MaterialStatePrecisionPolicy,
    MaterialStateStatus, MaterialStateStore, calculate_material_state,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.material_state.core import MaterialStateVerificationOrigin
from hms_cadcam.cam.persistence.artifact_store import ToolpathArtifactStore
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import (
    MaterialStateDependency,
    MaterialStateSuccessorPublication,
    ToolpathArtifactMetadata,
)
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


class R272ValidatedSuccessorCertificate:
    """Opaque process-local capability minted only by the R272 validator."""

    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("R272 successor certificates can only be minted by validation")

    def __repr__(self) -> str:
        return "<R272ValidatedSuccessorCertificate opaque>"

    def __copy__(self):
        raise TypeError("R272 successor certificates cannot be copied")

    def __deepcopy__(self, memo):
        del memo
        raise TypeError("R272 successor certificates cannot be copied")

    def __reduce__(self):
        raise TypeError("R272 successor certificates cannot be serialized")

    def __reduce_ex__(self, protocol):
        del protocol
        raise TypeError("R272 successor certificates cannot be serialized")


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


def _exact_authority_identity_graph(*values: object) -> tuple[object, ...]:
    """Snapshot value plus identity for every non-scalar authority node."""
    seen: dict[int, int] = {}

    def encode(value: object) -> object:
        if type(value) is float:
            return ("float", value.hex())
        if value is None or type(value) in {bool, int, str, bytes}:
            return (type(value).__qualname__, value)
        if isinstance(value, Enum):
            return (type(value).__module__, type(value).__qualname__, value.value)
        if isinstance(value, UUID):
            return ("uuid", str(value))
        identifier = id(value)
        if identifier in seen:
            return ("ref", seen[identifier])
        ordinal = len(seen)
        seen[identifier] = ordinal
        identity = (type(value).__module__, type(value).__qualname__, identifier)
        if is_dataclass(value) and not isinstance(value, type):
            return (
                "dataclass",
                identity,
                tuple((item.name, encode(getattr(value, item.name))) for item in fields(value)),
            )
        if isinstance(value, tuple):
            return ("tuple", identity, tuple(encode(item) for item in value))
        if isinstance(value, list):
            return ("list", identity, tuple(encode(item) for item in value))
        if isinstance(value, dict):
            return (
                "dict",
                identity,
                tuple(
                    sorted(
                        ((encode(key), encode(item)) for key, item in value.items()),
                        key=repr,
                    )
                ),
            )
        if isinstance(value, (set, frozenset)):
            return ("set", identity, tuple(sorted((encode(item) for item in value), key=repr)))
        return ("object", identity)

    return tuple(encode(value) for value in values)


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
    return cutter_engages_material_at(state, envelope, x, y, tip_z)


def _line_is_clear(state: MaterialState, envelope: CutterEnvelope, start: Point3, end: Point3, tip_z: float) -> bool:
    """Exact cell-disk interval proof for a horizontal link; never sample points."""
    return horizontal_segment_is_clear(state, envelope, start, end, tip_z)


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


def _r272_material_removal_operation_fingerprint(operation: Operation) -> ContentFingerprint:
    """Seal every operation fact except the three frozen feed-only controls."""

    if not isinstance(operation, Operation):
        raise TypeError("R272 producer operation authority is invalid")
    payload = operation.to_dict()
    payload.pop("revision")
    payload.pop("artifact_state")
    payload.pop("diagnostics")
    payload["parameters"] = dict(payload["parameters"])
    payload["parameters"]["values"] = [
        value
        for value in payload["parameters"]["values"]
        if value["name"] not in {
            "cutting_feed_rate",
            "plunge_feed_rate",
            "spindle_speed",
        }
    ]
    return ContentFingerprint.from_payload({
        "format": "HMS_CAM_MATERIAL_REMOVAL_OPERATION_AUTHORITY",
        "format_version": 1,
        "operation": payload,
    })


def _r272_state_authority_payload(state: MaterialState) -> dict[str, object]:
    """Return every durable machining-authority field of one state."""

    if not isinstance(state, MaterialState):
        raise TypeError("R272 successor state authority is invalid")
    return state.to_dict()


def _r272_feed_only_material_artifact(
    operation: Operation,
    artifact: ToolpathArtifact,
) -> bool:
    state = operation.artifact_state
    return (
        operation.strategy_key == "rest_contour_3axis"
        and state.status is ArtifactStatus.DIRTY
        and state.token is None
        and state.dirty_reasons == (DirtyReason.PARAMETERS_CHANGED,)
        and state.input_fingerprint == artifact.input_fingerprint
        and state.artifact_fingerprint is None
        and state.generation == artifact.computation_token.generation
        and artifact.source_operation_id == operation.operation_id
        and artifact.operation_revision.value < operation.revision.value
    )


def _install_r272_validated_successor_boundary():
    """Install the non-persistable R272 validation capability registry."""

    import secrets
    import threading
    import weakref

    lock = threading.RLock()
    records: dict[int, dict[str, object]] = {}

    def remove(identifier: int, reference) -> None:
        with lock:
            record = records.get(identifier)
            if record is not None and record["certificate"] is reference:
                records.pop(identifier, None)

    def validated_chain_current(record: dict[str, object]) -> bool:
        """Recheck exact live R272 producer authority retained by a certificate."""
        try:
            candidate = record["validation_candidate"]
            prepared = record["prepared"]
            plan = record["plan"]
            authority = record["plan_authority"]
            tool = record["producer_tool"]
            cutting_geometry = record["producer_cutting_geometry"]
            assembly = record["producer_assembly"]
            machine = record["producer_machine"]
            return (
                isinstance(candidate, RestContourPhaseBCandidate)
                and candidate.prepared is prepared
                and candidate.candidate_chain_fingerprint
                    == record["candidate_seal"]
                and _candidate_chain_fingerprint(candidate)
                    == record["candidate_seal"]
                and isinstance(prepared, RestContourPhaseBPrepared)
                and prepared.plan is plan
                and _prepared_fingerprint(prepared) == record["prepared_seal"]
                and isinstance(plan, RestContourResidualPlan)
                and plan.authority is authority
                and _plan_authority_seal(plan) == record["plan_seal"]
                and authority.tool is tool
                and authority.tool_assembly is assembly
                and authority.machine is machine
                and tool.cutting_geometry is cutting_geometry
                and tool.content_fingerprint == record["producer_tool_seal"]
                and ContentFingerprint.from_payload(assembly.to_dict())
                    == record["producer_assembly_seal"]
                and machine.content_fingerprint == record["producer_machine_seal"]
            )
        except (AttributeError, KeyError, TypeError, ValueError, CamValidationError):
            return False

    def mint(
        *,
        replay_context: RestContourPhaseBExecutionContext,
        validation_candidate: RestContourPhaseBCandidate,
        authoritative_setup: Setup,
        authoritative_producer_operation: Operation,
        exact_producer_artifact: ToolpathArtifact,
        trusted_parent_state: MaterialState,
        supplied_successor_state: MaterialState,
        producer_completion: MaterialStateSuccessorPublication,
        producer_dependency: MaterialStateDependency,
        cancellation: Callable[[], bool] | None,
    ) -> R272ValidatedSuccessorCertificate:
        if (not isinstance(replay_context, RestContourPhaseBExecutionContext)
                or not isinstance(validation_candidate, RestContourPhaseBCandidate)
                or not isinstance(authoritative_setup, Setup)
                or not isinstance(authoritative_producer_operation, Operation)
                or not isinstance(exact_producer_artifact, ToolpathArtifact)
                or not isinstance(trusted_parent_state, MaterialState)
                or not isinstance(supplied_successor_state, MaterialState)
                or not isinstance(producer_completion, MaterialStateSuccessorPublication)
                or not isinstance(producer_dependency, MaterialStateDependency)
                or (cancellation is not None and not callable(cancellation))):
            raise TypeError("R272 successor validation inputs are invalid")
        if replay_context.phase_a_inputs.cancellation is not cancellation:
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
                  "R272 validation cancellation authority differs from replay authority")
        _cancelled(cancellation)
        # This is the critical independent anchor: only a candidate registered
        # by the closed R272 Phase-B boundary can reach certificate minting.
        _validate_candidate(validation_candidate, rebuild=True)
        prepared = validation_candidate.prepared
        _fresh_prewrite(replay_context, prepared)
        plan = prepared.plan
        producer = authoritative_setup.operation_tree.get_operation(
            authoritative_producer_operation.operation_id
        )
        producer_state = authoritative_producer_operation.artifact_state
        exact_operation_state = (
            producer_state.status is ArtifactStatus.VALID
            and producer_state.token is None
            and not producer_state.dirty_reasons
            and producer_state.input_fingerprint == exact_producer_artifact.input_fingerprint
            and producer_state.artifact_fingerprint
                == exact_producer_artifact.artifact_fingerprint
            and producer_state.generation
                == exact_producer_artifact.computation_token.generation
            and exact_producer_artifact.operation_revision
                == authoritative_producer_operation.revision
        )
        feed_only_operation_state = _r272_feed_only_material_artifact(
            authoritative_producer_operation,
            exact_producer_artifact,
        )
        try:
            current_parameters = RestContourParameters.from_operation_parameters(
                authoritative_producer_operation.parameters
            )
            prepared_parameters = RestContourParameters.from_operation_parameters(
                prepared.base_operation.parameters
            )
            parameters_are_removal_equivalent = (
                current_parameters.to_operation_parameters().values
                == prepared_parameters.to_operation_parameters().values
            )
            if feed_only_operation_state:
                parameters_are_removal_equivalent = (
                    _r272_material_removal_operation_fingerprint(
                        authoritative_producer_operation
                    ) == _r272_material_removal_operation_fingerprint(
                        prepared.base_operation
                    )
                )
        except (TypeError, ValueError, CamValidationError):
            parameters_are_removal_equivalent = False
        if (producer is not authoritative_producer_operation
                or not authoritative_producer_operation.enabled
                or not authoritative_setup.enabled
                or not (exact_operation_state or feed_only_operation_state)
                or not parameters_are_removal_equivalent
                or prepared.setup.setup_id != authoritative_setup.setup_id
                or prepared.setup.revision != authoritative_setup.revision
                or prepared.setup.stock != authoritative_setup.stock
                or prepared.setup.wcs != authoritative_setup.wcs
                or prepared.setup.work_offset != authoritative_setup.work_offset
                or plan.authority.consumer_operation_id
                   != authoritative_producer_operation.operation_id
                or _r272_material_removal_operation_fingerprint(
                    authoritative_producer_operation
                ) != _r272_material_removal_operation_fingerprint(prepared.base_operation)
                or exact_producer_artifact != validation_candidate.artifact
                or trusted_parent_state is not prepared.predecessor_state
                or not trusted_parent_state.content_is_verified
                or exact_producer_artifact.source_operation_id
                   != authoritative_producer_operation.operation_id
                or exact_producer_artifact.setup_id != authoritative_setup.setup_id
                or exact_producer_artifact.wcs_fingerprint
                   != ContentFingerprint.from_payload(authoritative_setup.wcs.to_dict())
                or exact_producer_artifact.tool_assembly_id
                   != plan.authority.tool_assembly.assembly_id
                or exact_producer_artifact.tool_assembly_fingerprint
                   != ContentFingerprint.from_payload(plan.authority.tool_assembly.to_dict())
                or exact_producer_artifact.machine_id != plan.authority.machine.machine_id
                or exact_producer_artifact.machine_fingerprint
                   != plan.authority.machine.content_fingerprint
                or compute_material_removal_fingerprint(exact_producer_artifact)
                   != compute_material_removal_fingerprint(validation_candidate.artifact)):
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_STALE,
                  "R272 producer operation, artifact, tool, machine or Setup authority changed")
        try:
            completion_payload = producer_completion.to_dict()
            completion_payload["status"] = producer_completion.status
            completion_valid = (
                MaterialStateSuccessorPublication.from_dict(completion_payload)
                == producer_completion
            )
            dependency_valid = (
                MaterialStateDependency.from_dict(producer_dependency.to_dict())
                == producer_dependency
            )
        except (AttributeError, TypeError, ValueError, CamValidationError):
            completion_valid = False
            dependency_valid = False
        parent_edges = tuple(
            edge
            for edge in authoritative_setup.operation_tree.dependency_graph.edges
            if edge.kind.value == "material_state"
            and edge.target_operation_id == authoritative_producer_operation.operation_id
        )
        expected_parent_edge = (
            producer_dependency.producer_operation_id,
            authoritative_producer_operation.operation_id,
        )
        upstream = authoritative_setup.operation_tree.get_operation(
            producer_dependency.producer_operation_id
        ) if dependency_valid else None
        provenance_checks = {
            "completion_self": completion_valid,
            "dependency_self": dependency_valid,
            "unique_parent_edge": len(parent_edges) == 1,
            "parent_edge": len(parent_edges) == 1 and (
                parent_edges[0].source_operation_id,
                parent_edges[0].target_operation_id,
            ) == expected_parent_edge,
            "upstream_current": upstream is not None and upstream.enabled,
            "dependency_consumer": producer_dependency.consumer_operation_id
                == authoritative_producer_operation.operation_id,
            "upstream_operation": upstream is not None and (
                producer_dependency.producer_operation_authority_fingerprint
                == _r272_material_removal_operation_fingerprint(upstream)
            ),
            "parent_fingerprint": producer_dependency.parent_state_fingerprint
                == trusted_parent_state.fingerprint,
            "parent_toolpath": producer_dependency.producer_toolpath_fingerprint
                == trusted_parent_state.toolpath_fingerprint,
            "parent_setup": producer_dependency.setup_fingerprint
                == trusted_parent_state.setup_fingerprint,
            "parent_stock": producer_dependency.stock_fingerprint
                == trusted_parent_state.stock_fingerprint,
            "parent_engine": producer_dependency.engine_version
                == trusted_parent_state.engine_version,
            "parent_precision": producer_dependency.precision
                == trusted_parent_state.precision.to_dict(),
            "publication_link": producer_dependency.successor_publication
                == producer_completion,
            "completion_consumer": producer_completion.consumer_operation_id
                == authoritative_producer_operation.operation_id,
            "completion_artifact_id": producer_completion.artifact_id
                == exact_producer_artifact.artifact_id,
            "completion_artifact": producer_completion.artifact_fingerprint
                == exact_producer_artifact.artifact_fingerprint,
            "completion_input": producer_completion.input_fingerprint
                == exact_producer_artifact.input_fingerprint,
            "completion_removal": producer_completion.semantic_material_removal_fingerprint
                == compute_material_removal_fingerprint(exact_producer_artifact),
            "completion_parent": producer_completion.parent_state_fingerprint
                == trusted_parent_state.fingerprint,
            "completion_parent_seal": producer_completion.parent_state_content_seal
                == trusted_parent_state.content_integrity_fingerprint,
        }
        failed_provenance = tuple(
            name for name, valid in provenance_checks.items() if not valid
        )
        if failed_provenance:
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_STALE,
                  "R272 completion, dependency or predecessor authority changed: "
                  + ", ".join(failed_provenance))
        latched = False

        def replay_cancellation() -> bool:
            nonlocal latched
            if cancellation is not None and cancellation():
                latched = True
            return latched

        _cancelled(cancellation)
        try:
            replayed = calculate_material_state(
                stock=authoritative_setup.stock,
                artifact=exact_producer_artifact,
                tool=plan.authority.tool,
                parent=trusted_parent_state,
                setup_fingerprint=material_state_setup_fingerprint(authoritative_setup),
                precision=trusted_parent_state.precision,
                cancellation=replay_cancellation if cancellation is not None else None,
            ).state
        except CamValidationError as error:
            if latched:
                _fail(RestContourDiagnosticCode.CANCELLED,
                      "R272 successor authority replay was cancelled")
            _fail(RestContourDiagnosticCode.SUCCESSOR_INVALID,
                  f"R272 successor authority replay failed: {error}")
        _cancelled(cancellation)
        if (_r272_state_authority_payload(replayed)
                != _r272_state_authority_payload(supplied_successor_state)
                or _r272_state_authority_payload(replayed)
                   != _r272_state_authority_payload(validation_candidate.successor_state)
                or producer_completion.successor_state_fingerprint
                   != supplied_successor_state.fingerprint
                or producer_completion.successor_state_content_seal
                   != supplied_successor_state.content_integrity_fingerprint
                or producer_completion.setup_fingerprint
                   != supplied_successor_state.setup_fingerprint
                or producer_completion.stock_fingerprint
                   != supplied_successor_state.stock_fingerprint
                or producer_completion.engine_version != supplied_successor_state.engine_version
                or producer_completion.precision != supplied_successor_state.precision.to_dict()):
            _fail(RestContourDiagnosticCode.SUCCESSOR_INVALID,
                  "R272 supplied successor differs from independent authoritative replay")
        binding = ContentFingerprint.from_payload({
            "format": "HMS_CAM_R272_VALIDATED_SUCCESSOR_BINDING",
            "format_version": 1,
            "producer_operation": str(authoritative_producer_operation.operation_id),
            "producer_removal": _r272_material_removal_operation_fingerprint(
                authoritative_producer_operation
            ).to_dict(),
            "producer_tool": plan.authority.tool.content_fingerprint.to_dict(),
            "producer_assembly": ContentFingerprint.from_payload(
                plan.authority.tool_assembly.to_dict()
            ).to_dict(),
            "machine": plan.authority.machine.content_fingerprint.to_dict(),
            "setup": material_state_setup_fingerprint(authoritative_setup).to_dict(),
            "wcs": ContentFingerprint.from_payload(authoritative_setup.wcs.to_dict()).to_dict(),
            "parent": trusted_parent_state.fingerprint.to_dict(),
            "parent_seal": trusted_parent_state.content_integrity_fingerprint.to_dict(),
            "artifact": exact_producer_artifact.artifact_fingerprint.to_dict(),
            "semantic_removal": compute_material_removal_fingerprint(
                exact_producer_artifact
            ).to_dict(),
            "successor": supplied_successor_state.fingerprint.to_dict(),
            "successor_seal": supplied_successor_state.content_integrity_fingerprint.to_dict(),
            "engine": supplied_successor_state.engine_version,
            "precision": supplied_successor_state.precision.to_dict(),
            "completion": producer_completion.publication_fingerprint.to_dict(),
            "dependency": ContentFingerprint.from_payload(
                producer_dependency.to_dict()
            ).to_dict(),
        })
        certificate = object.__new__(R272ValidatedSuccessorCertificate)
        identifier = id(certificate)
        reference = weakref.ref(
            certificate,
            lambda value: remove(identifier, value),
        )
        with lock:
            records[identifier] = {
                "certificate": reference,
                "token": secrets.token_bytes(32),
                "binding": binding,
                "validation_candidate": validation_candidate,
                "replay_context": replay_context,
                "candidate_seal": _candidate_chain_fingerprint(validation_candidate),
                "prepared": prepared,
                "prepared_seal": _prepared_fingerprint(prepared),
                "plan": plan,
                "plan_seal": _plan_authority_seal(plan),
                "plan_authority": plan.authority,
                "producer_tool": plan.authority.tool,
                "producer_cutting_geometry": plan.authority.tool.cutting_geometry,
                "producer_tool_seal": plan.authority.tool.content_fingerprint,
                "producer_assembly": plan.authority.tool_assembly,
                "producer_assembly_seal": ContentFingerprint.from_payload(
                    plan.authority.tool_assembly.to_dict()
                ),
                "producer_machine": plan.authority.machine,
                "producer_machine_seal": plan.authority.machine.content_fingerprint,
                "setup": authoritative_setup,
                "setup_seal": ContentFingerprint.from_payload(authoritative_setup.to_dict()),
                "operation": authoritative_producer_operation,
                "operation_seal": ContentFingerprint.from_payload(
                    authoritative_producer_operation.to_dict()
                ),
                "artifact": exact_producer_artifact,
                "artifact_seal": ContentFingerprint.from_payload(
                    artifact_to_dict(exact_producer_artifact)
                ),
                "parent": trusted_parent_state,
                "parent_seal": trusted_parent_state.content_integrity_fingerprint,
                "successor": supplied_successor_state,
                "successor_seal": supplied_successor_state.content_integrity_fingerprint,
                "completion": producer_completion,
                "completion_seal": producer_completion.publication_fingerprint,
                "dependency": producer_dependency,
                "dependency_seal": ContentFingerprint.from_payload(
                    producer_dependency.to_dict()
                ),
                "cancellation": cancellation,
                "authority_identity_graph": _exact_authority_identity_graph(
                    replay_context,
                    validation_candidate,
                    authoritative_setup,
                    authoritative_producer_operation,
                    exact_producer_artifact,
                    trusted_parent_state,
                    supplied_successor_state,
                    producer_completion,
                    producer_dependency,
                    cancellation,
                ),
            }
        return certificate

    def require(
        certificate: R272ValidatedSuccessorCertificate,
        *,
        setup: Setup,
        producer_operation: Operation,
        artifact: ToolpathArtifact,
        parent_state: MaterialState,
        successor_state: MaterialState,
        completion: MaterialStateSuccessorPublication,
        dependency: MaterialStateDependency,
        cancellation: Callable[[], bool] | None,
    ) -> ContentFingerprint:
        if (not isinstance(certificate, R272ValidatedSuccessorCertificate)
                or (cancellation is not None and not callable(cancellation))):
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_STALE,
                  "R272 successor validation certificate is absent or invalid")
        with lock:
            record = records.get(id(certificate))
            valid = (
                record is not None
                and record["certificate"]() is certificate
                and isinstance(record["token"], bytes)
                and len(record["token"]) == 32
                and record["authority_identity_graph"]
                    == _exact_authority_identity_graph(
                        record["replay_context"],
                        record["validation_candidate"],
                        setup,
                        producer_operation,
                        artifact,
                        parent_state,
                        successor_state,
                        completion,
                        dependency,
                        cancellation,
                    )
                and validated_chain_current(record)
                and record["setup"] is setup
                and record["operation"] is producer_operation
                and record["artifact"] is artifact
                and record["parent"] is parent_state
                and record["successor"] is successor_state
                and record["completion"] is completion
                and record["dependency"] is dependency
                and record["cancellation"] is cancellation
                and record["setup_seal"] == ContentFingerprint.from_payload(setup.to_dict())
                and record["operation_seal"]
                    == ContentFingerprint.from_payload(producer_operation.to_dict())
                and record["artifact_seal"]
                    == ContentFingerprint.from_payload(artifact_to_dict(artifact))
                and record["parent_seal"] == parent_state.content_integrity_fingerprint
                and parent_state.content_is_verified
                and record["successor_seal"] == successor_state.content_integrity_fingerprint
                and successor_state.content_is_verified
                and record["completion_seal"] == completion.publication_fingerprint
                and record["dependency_seal"]
                    == ContentFingerprint.from_payload(dependency.to_dict())
            )
            binding = record["binding"] if valid else None
        if binding is None:
            _fail(RestContourDiagnosticCode.MATERIAL_STATE_STALE,
                  "R272 successor validation certificate is stale or foreign")
        _cancelled(cancellation)
        return binding

    return mint, require


(
    mint_r272_validated_successor_certificate,
    require_r272_validated_successor_certificate,
) = _install_r272_validated_successor_boundary()
del _install_r272_validated_successor_boundary


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
