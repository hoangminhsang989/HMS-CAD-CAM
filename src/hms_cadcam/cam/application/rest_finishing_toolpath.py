"""Sealed in-memory Phase B for the R273 Rest Finishing raster plan.

The module deliberately has no store, registry, project-service, or durable
publication boundary.  A successful call returns one process-minted candidate
whose Toolpath IR and successor MaterialState have both been independently
replayed from the authoritative predecessor.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum
from typing import Callable
from uuid import UUID, uuid5

from hms_cadcam.cam.application.rest_finishing_geometry import (
    NoRestFinishingMaterial,
    RestFinishingGeometryInputs,
    RestFinishingLevelPlan,
    RestFinishingRasterPlan,
    _validate_machine_motion_bounds,
    derive_rest_finishing_level,
    plan_rest_finishing_geometry,
)
from hms_cadcam.cam.application.rest_machining_safety import (
    cutter_engages_material_at,
    horizontal_segment_is_clear,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ContentFingerprint,
    DependencyFingerprint,
    Operation,
    Point3,
    ToolpathArtifactId,
    Vector3,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.operation import ComputationToken
from hms_cadcam.cam.domain.rest_finishing import (
    REST_FINISHING_STRATEGY_KEY,
    RestFinishingDiagnosticCode,
    RestFinishingValidationError,
)
from hms_cadcam.cam.material_state import (
    MATERIAL_STATE_ENGINE_VERSION,
    CutterEnvelope,
    MaterialState,
    MaterialStatePrecisionPolicy,
    MaterialStateStatus,
    calculate_material_state,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.material_state.core import MaterialStateVerificationOrigin
from hms_cadcam.cam.toolpath import (
    FeedMode,
    MotionClass,
    Pose,
    SpindleState,
    ToolpathArtifact,
    ToolpathBuilder,
    compute_material_removal_fingerprint,
    compute_toolpath_fingerprint,
)
from hms_cadcam.cam.toolpath.codec import artifact_to_dict


_ALGORITHM_VERSION = 1
_ARTIFACT_NAMESPACE = UUID("00d3d61e-c188-4d60-9bdd-d3d264d0fc04")
_PROVISIONAL_ARTIFACT_ID = ToolpathArtifactId(
    UUID("00000000-0000-0000-0000-000000000001")
)
_MAX_EVENTS = 100_000


def _fail(code: RestFinishingDiagnosticCode, message: str) -> None:
    raise RestFinishingValidationError(code, message)


def _cancelled(cancellation: Callable[[], bool] | None, phase: str) -> None:
    if cancellation is not None and cancellation():
        _fail(RestFinishingDiagnosticCode.CANCELLED, f"Rest Finishing cancelled during {phase}")


def _removed_volume_is_positive(value: float) -> bool:
    """Validate an L^3 delta without comparing it to an L tolerance."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0.0


def _pose(x: float, y: float, z: float, unit) -> Pose:
    return Pose(Point3(x, y, z, unit), Vector3(0.0, 0.0, 1.0))


def _canonical(value: object) -> object:
    """Recursively bind frozen dataclasses instead of trusting cached hashes."""
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Rest Finishing authority contains a non-finite float")
        return {"__float__": value.hex()}
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, Enum):
        return {
            "__enum__": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _canonical(value.value),
        }
    if isinstance(value, UUID):
        return {"__uuid__": str(value)}
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        pairs = [(_canonical(key), _canonical(item)) for key, item in value.items()]
        return {"__dict__": sorted(pairs, key=lambda item: repr(item[0]))}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__dataclass__": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": [
                [item.name, _canonical(getattr(value, item.name))]
                for item in fields(value)
                if item.name not in {
                    "_factory_seal",
                    "_trust_token",
                    "cancellation",
                    "profile_resolver",
                    "producer_validation_certificate",
                }
            ],
        }
    raise TypeError(f"Unsupported Rest Finishing authority type: {type(value)!r}")


def _exact_authority_identity_graph(value: object) -> object:
    """Bind the complete nested identity and value graph of process authority."""
    seen: dict[int, int] = {}

    def encode(current: object) -> object:
        if type(current) is float:
            return ("float", current.hex())
        if current is None or type(current) in {bool, int, str, bytes}:
            return (type(current).__qualname__, current)
        if isinstance(current, Enum):
            return (type(current).__module__, type(current).__qualname__, current.value)
        if isinstance(current, UUID):
            return ("uuid", str(current))
        identifier = id(current)
        if identifier in seen:
            return ("ref", seen[identifier])
        ordinal = len(seen)
        seen[identifier] = ordinal
        identity = (type(current).__module__, type(current).__qualname__, identifier)
        if is_dataclass(current) and not isinstance(current, type):
            return (
                "dataclass",
                identity,
                tuple(
                    (item.name, encode(getattr(current, item.name)))
                    for item in fields(current)
                ),
            )
        if isinstance(current, tuple):
            return ("tuple", identity, tuple(encode(item) for item in current))
        if isinstance(current, list):
            return ("list", identity, tuple(encode(item) for item in current))
        if isinstance(current, dict):
            return (
                "dict",
                identity,
                tuple(
                    sorted(
                        ((encode(key), encode(item)) for key, item in current.items()),
                        key=repr,
                    )
                ),
            )
        if isinstance(current, (set, frozenset)):
            return (
                "set",
                identity,
                tuple(sorted((encode(item) for item in current), key=repr)),
            )
        return ("object", identity)

    return encode(value)


def _deep_seal(value: object, format_name: str) -> ContentFingerprint:
    return ContentFingerprint.from_payload(
        {"format": format_name, "format_version": 1, "value": _canonical(value)}
    )


@dataclass(frozen=True, slots=True)
class RestFinishingPrepared:
    """One process-local reservation for the exact immutable Phase-A plan."""

    inputs: RestFinishingGeometryInputs
    plan: RestFinishingRasterPlan
    predecessor_state: MaterialState
    base_operation: Operation
    computing_operation: Operation
    input_fingerprint: DependencyFingerprint
    computation_token: ComputationToken
    prepared_fingerprint: ContentFingerprint = field(init=False)
    _factory_seal: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "prepared_fingerprint", _prepared_fingerprint(self))
        object.__setattr__(self, "_factory_seal", object())


@dataclass(frozen=True, slots=True)
class RestFinishingSuccessorProvenance:
    parent_fingerprint: ContentFingerprint
    parent_content_integrity_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    full_toolpath_artifact_fingerprint: ContentFingerprint
    semantic_material_removal_fingerprint: ContentFingerprint
    successor_fingerprint: ContentFingerprint
    successor_content_integrity_fingerprint: ContentFingerprint
    removed_volume: float


@dataclass(frozen=True, slots=True)
class RestFinishingCandidate:
    """Validated R273 output; it is intentionally not a publication object."""

    prepared: RestFinishingPrepared
    artifact: ToolpathArtifact
    successor_state: MaterialState
    successor_provenance: RestFinishingSuccessorProvenance
    level_plans: tuple[RestFinishingLevelPlan, ...]
    candidate_fingerprint: ContentFingerprint = field(init=False)
    _factory_seal: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_fingerprint", _candidate_fingerprint(self))
        object.__setattr__(self, "_factory_seal", object())

    @property
    def full_toolpath_artifact_fingerprint(self) -> ContentFingerprint:
        return self.successor_provenance.full_toolpath_artifact_fingerprint

    @property
    def semantic_material_removal_fingerprint(self) -> ContentFingerprint:
        return self.successor_provenance.semantic_material_removal_fingerprint


def _input_fingerprint(
    inputs: RestFinishingGeometryInputs,
    plan: RestFinishingRasterPlan,
) -> DependencyFingerprint:
    return DependencyFingerprint.from_payload(
        {
            "format": "HMS_CAM_REST_FINISHING_PHASE_B_INPUT",
            "format_version": _ALGORITHM_VERSION,
            "plan": _deep_seal(plan, "HMS_CAM_REST_FINISHING_PLAN_DEEP_SEAL").to_dict(),
            "predecessor": plan.predecessor_state.fingerprint.to_dict(),
            "predecessor_content": plan.predecessor_state.content_integrity_fingerprint.to_dict(),
            "setup": ContentFingerprint.from_payload(inputs.setup.to_dict()).to_dict(),
            "parameters": inputs.parameters.fingerprint.to_dict(),
            "tool": inputs.tool.content_fingerprint.to_dict(),
            "assembly": ContentFingerprint.from_payload(inputs.assembly.to_dict()).to_dict(),
            "machine": inputs.machine.content_fingerprint.to_dict(),
            "algorithm_version": _ALGORITHM_VERSION,
        }
    )


def _prepared_fingerprint(value: RestFinishingPrepared) -> ContentFingerprint:
    return ContentFingerprint.from_payload(
        {
            "format": "HMS_CAM_REST_FINISHING_PREPARED",
            "format_version": 1,
            "inputs": _deep_seal(value.inputs, "HMS_CAM_REST_FINISHING_INPUTS_DEEP_SEAL").to_dict(),
            "plan": _deep_seal(value.plan, "HMS_CAM_REST_FINISHING_PLAN_DEEP_SEAL").to_dict(),
            "predecessor": value.predecessor_state.fingerprint.to_dict(),
            "predecessor_content": value.predecessor_state.content_integrity_fingerprint.to_dict(),
            "base_operation": value.base_operation.to_dict(),
            "computing_operation": value.computing_operation.to_dict(),
            "input": value.input_fingerprint.to_dict(),
            "token": {
                "value": str(value.computation_token.value),
                "generation": value.computation_token.generation,
            },
        }
    )


def _consumer_operation(inputs: RestFinishingGeometryInputs) -> Operation:
    operations = {
        operation.operation_id: operation
        for operation in inputs.setup.operation_tree.operations
    }
    operation = operations.get(inputs.consumer_operation_id)
    if (
        operation is None
        or not operation.enabled
        or operation.setup_id != inputs.setup.setup_id
        or operation.strategy_key != REST_FINISHING_STRATEGY_KEY
        or operation.parameters != inputs.parameters.to_operation_parameters()
    ):
        _fail(
            RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
            "Rest Finishing consumer operation is not current",
        )
    return operation


def _prepare_unsealed(
    inputs: RestFinishingGeometryInputs,
    plan: RestFinishingRasterPlan,
) -> RestFinishingPrepared:
    if not isinstance(inputs, RestFinishingGeometryInputs) or not isinstance(plan, RestFinishingRasterPlan):
        raise TypeError("Rest Finishing preparation inputs are invalid")
    _cancelled(inputs.cancellation, "preparation")
    current = plan_rest_finishing_geometry(inputs)
    if (
        not isinstance(current, RestFinishingRasterPlan)
        or _deep_seal(current, "HMS_CAM_REST_FINISHING_PLAN_DEEP_SEAL")
        != _deep_seal(plan, "HMS_CAM_REST_FINISHING_PLAN_DEEP_SEAL")
    ):
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_STALE, "Rest Finishing plan is no longer current")
    predecessor = plan.predecessor_state
    operation = _consumer_operation(inputs)
    fingerprint = _input_fingerprint(inputs, plan)
    try:
        computing_state, token = operation.artifact_state.begin(fingerprint)
    except CamValidationError as error:
        _fail(
            RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
            f"Rest Finishing computation cannot begin: {error}",
        )
    prepared = RestFinishingPrepared(
        inputs,
        plan,
        predecessor,
        operation,
        replace(operation, artifact_state=computing_state),
        fingerprint,
        token,
    )
    _validate_prepared(prepared, replan=False)
    return prepared


def _validate_prepared(value: RestFinishingPrepared, *, replan: bool) -> None:
    if not isinstance(value, RestFinishingPrepared):
        raise TypeError("Rest Finishing prepared value is invalid")
    state = value.computing_operation.artifact_state
    expected_input = _input_fingerprint(value.inputs, value.plan)
    expected_computing = replace(value.base_operation, artifact_state=state)
    predecessor = value.predecessor_state
    if (
        value.plan.predecessor_state is not predecessor
        or not predecessor.content_is_verified
        or predecessor.fingerprint != value.plan.predecessor_state.fingerprint
        or value.input_fingerprint != expected_input
        or value.prepared_fingerprint != _prepared_fingerprint(value)
        or expected_computing != value.computing_operation
        or state.status is not ArtifactStatus.COMPUTING
        or state.token != value.computation_token
        or state.input_fingerprint != value.input_fingerprint
        or value.computation_token.generation != value.base_operation.artifact_state.generation + 1
    ):
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_STALE, "Rest Finishing prepared seal is stale")
    if replan:
        current = plan_rest_finishing_geometry(value.inputs)
        if (
            not isinstance(current, RestFinishingRasterPlan)
            or _deep_seal(current, "HMS_CAM_REST_FINISHING_PLAN_DEEP_SEAL")
            != _deep_seal(value.plan, "HMS_CAM_REST_FINISHING_PLAN_DEEP_SEAL")
        ):
            _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_STALE, "Rest Finishing authority drifted")


def _artifact_without_id(artifact: ToolpathArtifact) -> dict[str, object]:
    payload = artifact_to_dict(artifact)
    payload.pop("artifact_id")
    return payload


def _artifact_id(artifact: ToolpathArtifact) -> ToolpathArtifactId:
    digest = ContentFingerprint.from_payload(_artifact_without_id(artifact)).digest
    return ToolpathArtifactId(uuid5(_ARTIFACT_NAMESPACE, digest))


def _build_artifact(
    prepared: RestFinishingPrepared,
    levels: tuple[tuple[RestFinishingLevelPlan, MaterialState], ...],
    *,
    cancellation: Callable[[], bool] | None,
) -> ToolpathArtifact:
    if not levels or any(not level.spans for level, _state in levels):
        _fail(RestFinishingDiagnosticCode.UNREACHABLE_FINISHING_MATERIAL, "Rest Finishing has no reachable raster")
    span_count = sum(len(level.spans) for level, _state in levels)
    if span_count * 5 + 8 > _MAX_EVENTS:
        _fail(RestFinishingDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Finishing event limit exceeded")
    parameters = prepared.inputs.parameters
    first_span = levels[0][0].spans[0]
    first_x = first_span.end_x if first_span.reverse else first_span.start_x
    builder = ToolpathBuilder(
        artifact_id=_PROVISIONAL_ARTIFACT_ID,
        operation_id=prepared.computing_operation.operation_id,
        operation_revision=prepared.computing_operation.revision,
        computation_token=prepared.computation_token,
        input_fingerprint=prepared.input_fingerprint,
        unit=parameters.unit,
        setup_id=prepared.inputs.setup.setup_id,
        setup_revision=prepared.inputs.setup.revision,
        wcs_fingerprint=ContentFingerprint.from_payload(prepared.inputs.setup.wcs.to_dict()),
        tool_assembly_id=prepared.inputs.assembly.assembly_id,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(prepared.inputs.assembly.to_dict()),
        machine_id=prepared.inputs.machine.machine_id,
        machine_fingerprint=prepared.inputs.machine.content_fingerprint,
        created_at=None,
    )
    envelope = CutterEnvelope.from_tool(prepared.inputs.tool)
    try:
        builder.set_initial_pose(
            _pose(first_x, first_span.y, parameters.clearance_height.value, parameters.unit)
        )
        builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
        builder.set_spindle(
            SpindleState.CLOCKWISE,
            parameters.spindle_speed,
            provenance="rest_finishing.spindle.on",
        )
        ordinal = 0
        for level, state in levels:
            for span in level.spans:
                _cancelled(cancellation, "toolpath generation")
                start_x = span.end_x if span.reverse else span.start_x
                end_x = span.start_x if span.reverse else span.end_x
                start = Point3(start_x, span.y, level.tip_z, parameters.unit)
                end = Point3(end_x, span.y, level.tip_z, parameters.unit)
                if start_x == end_x:
                    _fail(RestFinishingDiagnosticCode.GEOMETRY_INVALID, "Zero-length raster cannot mint CUTTING")
                if cutter_engages_material_at(state, envelope, start.x, start.y, level.tip_z):
                    _fail(RestFinishingDiagnosticCode.ENTRY_UNSAFE, "Rest Finishing raster entry is material-engaging")
                current = builder.current_pose
                assert current is not None
                clearance = _pose(start.x, start.y, parameters.clearance_height.value, parameters.unit)
                if not horizontal_segment_is_clear(
                    state,
                    envelope,
                    current.position,
                    clearance.position,
                    parameters.clearance_height.value,
                ):
                    _fail(RestFinishingDiagnosticCode.LINK_UNSAFE, "Rest Finishing clearance link is unsafe")
                if current != clearance:
                    builder.rapid_to(
                        clearance,
                        motion_class=MotionClass.LINK,
                        provenance=f"rest_finishing.raster.{ordinal}.clearance_link",
                    )
                builder.linear_to(
                    _pose(start.x, start.y, level.tip_z, parameters.unit),
                    parameters.plunge_feed_rate,
                    motion_class=MotionClass.LINK,
                    provenance=f"rest_finishing.raster.{ordinal}.entry",
                )
                builder.linear_to(
                    _pose(end.x, end.y, level.tip_z, parameters.unit),
                    parameters.cutting_feed_rate,
                    motion_class=MotionClass.CUTTING,
                    engagement=(
                        ("phase", "rest_finishing"),
                        ("level", level.fingerprint.digest),
                        ("span", span.fingerprint.digest),
                    ),
                    provenance=f"rest_finishing.raster.{ordinal}.cut",
                )
                builder.linear_to(
                    _pose(end.x, end.y, parameters.retract_height.value, parameters.unit),
                    parameters.plunge_feed_rate,
                    motion_class=MotionClass.RETRACT,
                    provenance=f"rest_finishing.raster.{ordinal}.retract",
                )
                builder.rapid_to(
                    _pose(end.x, end.y, parameters.clearance_height.value, parameters.unit),
                    motion_class=MotionClass.RETRACT,
                    provenance=f"rest_finishing.raster.{ordinal}.clearance",
                )
                ordinal += 1
        builder.set_spindle(SpindleState.OFF, provenance="rest_finishing.spindle.off")
        provisional = builder.finalize()
    except RestFinishingValidationError:
        builder.abort()
        raise
    except CamValidationError as error:
        builder.abort()
        _fail(RestFinishingDiagnosticCode.GEOMETRY_INVALID, f"Rest Finishing motion is invalid: {error}")
    artifact = replace(provisional, artifact_id=_artifact_id(provisional))
    if artifact.artifact_id != _artifact_id(artifact):
        _fail(RestFinishingDiagnosticCode.SUCCESSOR_INVALID, "Rest Finishing artifact self-address is invalid")
    _validate_machine_motion_bounds(
        prepared.inputs,
        artifact.bounds.minimum,
        artifact.bounds.maximum,
    )
    return artifact


def _replay(
    prepared: RestFinishingPrepared,
    artifact: ToolpathArtifact,
    cancellation: Callable[[], bool] | None,
    phase: str,
) -> MaterialState:
    latched = False

    def callback() -> bool:
        nonlocal latched
        if cancellation is not None and cancellation():
            latched = True
        return latched

    try:
        state = calculate_material_state(
            stock=prepared.inputs.setup.stock,
            artifact=artifact,
            tool=prepared.inputs.tool,
            parent=prepared.predecessor_state,
            setup_fingerprint=material_state_setup_fingerprint(prepared.inputs.setup),
            precision=MaterialStatePrecisionPolicy(),
            cancellation=callback,
        ).state
    except CamValidationError as error:
        if latched:
            _fail(RestFinishingDiagnosticCode.CANCELLED, f"Rest Finishing cancelled during {phase}")
        _fail(RestFinishingDiagnosticCode.SUCCESSOR_INVALID, f"Rest Finishing replay failed: {error}")
    _cancelled(cancellation, phase)
    return state


def _candidate_fingerprint(candidate: RestFinishingCandidate) -> ContentFingerprint:
    return ContentFingerprint.from_payload(
        {
            "format": "HMS_CAM_REST_FINISHING_CANDIDATE",
            "format_version": 1,
            "prepared": candidate.prepared.prepared_fingerprint.to_dict(),
            "artifact": compute_toolpath_fingerprint(candidate.artifact).to_dict(),
            "semantic": compute_material_removal_fingerprint(candidate.artifact).to_dict(),
            "successor": candidate.successor_state.fingerprint.to_dict(),
            "successor_content": candidate.successor_state.content_integrity_fingerprint.to_dict(),
            "provenance": _canonical(candidate.successor_provenance),
            "levels": [level.fingerprint.to_dict() for level in candidate.level_plans],
        }
    )


def _validate_candidate(
    value: RestFinishingCandidate,
    *,
    rebuild: bool,
    cancellation: Callable[[], bool] | None,
) -> None:
    if not isinstance(value, RestFinishingCandidate):
        raise TypeError("Rest Finishing candidate is invalid")
    _validate_prepared(value.prepared, replan=False)
    artifact = value.artifact
    successor = value.successor_state
    proof = value.successor_provenance
    predecessor = value.prepared.predecessor_state
    setup = value.prepared.inputs.setup
    full = compute_toolpath_fingerprint(artifact)
    semantic = compute_material_removal_fingerprint(artifact)
    if rebuild:
        rebuilt = _build_artifact(
            value.prepared,
            tuple((level, predecessor) for level in value.level_plans),
            cancellation=cancellation,
        )
        # Safety-state identity is validated by the generation replay chain;
        # artifact bytes themselves depend only on the immutable level plans.
        if rebuilt != artifact:
            _fail(RestFinishingDiagnosticCode.SUCCESSOR_INVALID, "Rest Finishing artifact differs from its sealed plan")
    if (
        artifact.source_operation_id != value.prepared.computing_operation.operation_id
        or artifact.operation_revision != value.prepared.computing_operation.revision
        or artifact.computation_token != value.prepared.computation_token
        or artifact.input_fingerprint != value.prepared.input_fingerprint
        or artifact.artifact_id != _artifact_id(artifact)
        or successor.parent_fingerprint != predecessor.fingerprint
        or successor.setup_fingerprint != material_state_setup_fingerprint(setup)
        or successor.engine_version != MATERIAL_STATE_ENGINE_VERSION
        or successor.precision != MaterialStatePrecisionPolicy()
        or successor.status is not MaterialStateStatus.COMPLETE
        or successor.verification_origin is not MaterialStateVerificationOrigin.TRUSTED_CALCULATED
        or not successor.content_is_verified
        or successor.toolpath_fingerprint != semantic
        or proof.parent_fingerprint != predecessor.fingerprint
        or proof.parent_content_integrity_fingerprint != predecessor.content_integrity_fingerprint
        or proof.full_toolpath_artifact_fingerprint != full
        or proof.semantic_material_removal_fingerprint != semantic
        or proof.successor_fingerprint != successor.fingerprint
        or proof.successor_content_integrity_fingerprint != successor.content_integrity_fingerprint
        or value.candidate_fingerprint != _candidate_fingerprint(value)
    ):
        _fail(RestFinishingDiagnosticCode.SUCCESSOR_INVALID, "Rest Finishing candidate seal is invalid")
    replay = _replay(value.prepared, artifact, cancellation, "candidate validation")
    if replay != successor:
        _fail(RestFinishingDiagnosticCode.SUCCESSOR_INVALID, "Rest Finishing successor differs from independent replay")


def _generate_unsealed(
    prepared: RestFinishingPrepared,
    *,
    cancellation: Callable[[], bool] | None,
) -> RestFinishingCandidate:
    _validate_prepared(prepared, replan=True)
    callback = cancellation if cancellation is not None else prepared.inputs.cancellation
    _cancelled(callback, "generation")
    current = prepared.predecessor_state
    level_authority: list[tuple[RestFinishingLevelPlan, MaterialState]] = []
    for tip_z in prepared.plan.levels:
        _cancelled(callback, "level planning")
        level = derive_rest_finishing_level(prepared.plan, current, tip_z, cancellation=callback)
        if level is None:
            continue
        engagement_epsilon = max(math.ulp(tip_z), current.precision.tolerance)
        if any(
            current.top_heights[row * current.width + column] - tip_z
            > prepared.inputs.parameters.max_stepdown.value + engagement_epsilon
            for row, column in prepared.plan.target_cells
            if current.top_heights[row * current.width + column] > tip_z + engagement_epsilon
        ):
            _fail(
                RestFinishingDiagnosticCode.STEPDOWN_EXCEEDED,
                "Rest Finishing axial engagement exceeds the authorized max stepdown",
            )
        level_authority.append((level, current))
        prefix = _build_artifact(prepared, tuple(level_authority), cancellation=callback)
        current = _replay(prepared, prefix, callback, "intermediate replay")
    if not level_authority:
        _fail(RestFinishingDiagnosticCode.UNREACHABLE_FINISHING_MATERIAL, "Rest Finishing work produced no raster")
    artifact = _build_artifact(prepared, tuple(level_authority), cancellation=callback)
    successor = _replay(prepared, artifact, callback, "final successor construction")
    _cancelled(callback, "final completeness validation")
    remaining = derive_rest_finishing_level(
        prepared.plan,
        successor,
        prepared.inputs.parameters.cut_z,
        cancellation=callback,
    )
    if remaining is not None:
        _fail(
            RestFinishingDiagnosticCode.UNREACHABLE_FINISHING_MATERIAL,
            "Rest Finishing replay leaves required material",
        )
    removed = prepared.predecessor_state.remaining_volume - successor.remaining_volume
    # Final cell completeness above is the dimensional removal proof. Volume
    # (L^3) must not be compared with the operation's length tolerance (L).
    if not _removed_volume_is_positive(removed):
        _fail(RestFinishingDiagnosticCode.SUCCESSOR_INVALID, "Rest Finishing successor removed no material")
    full = compute_toolpath_fingerprint(artifact)
    semantic = compute_material_removal_fingerprint(artifact)
    proof = RestFinishingSuccessorProvenance(
        prepared.predecessor_state.fingerprint,
        prepared.predecessor_state.content_integrity_fingerprint,
        material_state_setup_fingerprint(prepared.inputs.setup),
        full,
        semantic,
        successor.fingerprint,
        successor.content_integrity_fingerprint,
        removed,
    )
    candidate = RestFinishingCandidate(
        prepared,
        artifact,
        successor,
        proof,
        tuple(level for level, _state in level_authority),
    )
    _validate_candidate(candidate, rebuild=False, cancellation=callback)
    _cancelled(callback, "candidate sealing")
    return candidate


def _install_boundary():
    prepared_records: dict[
        int,
        tuple[RestFinishingPrepared, object, ContentFingerprint, object],
    ] = {}
    candidate_records: dict[
        int,
        tuple[
            RestFinishingCandidate,
            object,
            ContentFingerprint,
            RestFinishingPrepared,
            object,
        ],
    ] = {}
    lock = threading.RLock()

    def prepare(inputs: RestFinishingGeometryInputs, plan: RestFinishingRasterPlan) -> RestFinishingPrepared:
        value = _prepare_unsealed(inputs, plan)
        with lock:
            prepared_records[id(value)] = (
                value,
                value._factory_seal,
                value.prepared_fingerprint,
                _exact_authority_identity_graph(value),
            )
        return value

    def require_prepared(value: RestFinishingPrepared) -> None:
        with lock:
            record = prepared_records.get(id(value))
        if (
            record is None
            or record[0] is not value
            or record[1] is not value._factory_seal
            or record[2] != value.prepared_fingerprint
            or record[3] != _exact_authority_identity_graph(value)
        ):
            _fail(
                RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
                "Rest Finishing preparation was not minted by this process",
            )
        try:
            _validate_prepared(value, replan=False)
        except RestFinishingValidationError:
            raise
        except (AttributeError, TypeError, ValueError, CamValidationError) as error:
            _fail(
                RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
                f"Rest Finishing prepared authority is invalid: {error}",
            )

    def generate(
        value: RestFinishingPrepared,
        *,
        cancellation: Callable[[], bool] | None = None,
    ) -> RestFinishingCandidate:
        require_prepared(value)
        candidate = _generate_unsealed(value, cancellation=cancellation)
        with lock:
            candidate_records[id(candidate)] = (
                candidate,
                candidate._factory_seal,
                candidate.candidate_fingerprint,
                candidate.prepared,
                _exact_authority_identity_graph(candidate),
            )
        return candidate

    def require_candidate(
        value: RestFinishingCandidate,
        *,
        cancellation: Callable[[], bool] | None = None,
    ) -> None:
        with lock:
            record = candidate_records.get(id(value))
        if (
            record is None
            or record[0] is not value
            or record[1] is not value._factory_seal
            or record[2] != value.candidate_fingerprint
            or record[3] is not value.prepared
            or record[4] != _exact_authority_identity_graph(value)
        ):
            _fail(
                RestFinishingDiagnosticCode.SUCCESSOR_INVALID,
                "Rest Finishing candidate was not minted by this process",
            )
        try:
            require_prepared(value.prepared)
            callback = cancellation if cancellation is not None else value.prepared.inputs.cancellation
            _validate_candidate(value, rebuild=False, cancellation=callback)
            _cancelled(callback, "final candidate validation")
        except RestFinishingValidationError:
            raise
        except (AttributeError, TypeError, ValueError, CamValidationError) as error:
            _fail(
                RestFinishingDiagnosticCode.SUCCESSOR_INVALID,
                f"Rest Finishing candidate authority is invalid: {error}",
            )

    return prepare, generate, require_prepared, require_candidate


(
    prepare_rest_finishing_toolpath,
    generate_rest_finishing_toolpath,
    require_rest_finishing_prepared,
    require_rest_finishing_candidate,
) = _install_boundary()
del _install_boundary


__all__ = [
    "RestFinishingCandidate",
    "RestFinishingPrepared",
    "RestFinishingSuccessorProvenance",
]
