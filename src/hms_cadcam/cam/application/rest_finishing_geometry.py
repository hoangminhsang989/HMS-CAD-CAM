"""Authoritative constant-Z planar geometry planning for Rest Finishing.

Phase A validates the complete current aggregate and produces immutable raster
evidence only.  Phase B must call :func:`derive_rest_finishing_level` with the
MaterialState obtained by replaying the cumulative Toolpath IR before every
level; a mask derived from an earlier state is never reusable authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, TypeAlias

from hms_cadcam.cam.application.contour import (
    _TOLERANCE as _EPSILON,
    ContourGenerationError,
    ContourPath,
    canonical_contour_start,
    offset_contour,
    resolve_profile_in_setup,
)
from hms_cadcam.cam.application.rest_contour import RestMaterialStateCandidate
from hms_cadcam.cam.application.rest_contour_toolpath import (
    R272ValidatedSuccessorCertificate,
    require_r272_validated_successor_certificate,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    BoxStock,
    ContentFingerprint,
    ContourCurveKind,
    ContourLoop,
    ContourProfileSource,
    ContourSide,
    CylindricalGeometry,
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    DirtyReason,
    GeometryInputRole,
    GeometryReference,
    GeometryReferenceKind,
    GeometryResolutionStatus,
    KinematicSide,
    MachineAxisType,
    MachineDefinition,
    MachineEvidence,
    MachineKind,
    MachineRequirement,
    OperationCapability,
    OperationFamily,
    OperationId,
    Operation,
    Point3,
    ResolvedContourProfile,
    Revision,
    Setup,
    SpindleDirection,
    ToolAssembly,
    ToolAssemblyEvidence,
    ToolAssemblyReference,
    ToolAssemblyStatus,
    ToolDefinition,
    ToolFamily,
    Vector3,
    WCS_ORTHONORMAL_TOLERANCE,
    assess_machine_compatibility,
    assess_tool_assembly,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.rest_contour import (
    RestContourDiagnosticCode,
    RestContourValidationError,
)
from hms_cadcam.cam.domain.rest_finishing import (
    REST_FINISHING_STRATEGY_KEY,
    RestFinishingDiagnosticCode,
    RestFinishingParameters,
    RestFinishingProfileSelection,
    RestFinishingValidationError,
)
from hms_cadcam.cam.material_state import (
    MATERIAL_STATE_ENGINE_VERSION,
    CutterEnvelope,
    MaterialState,
    MaterialStatePrecisionPolicy,
    MaterialStateStatus,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.material_state.core import MaterialStateVerificationOrigin
from hms_cadcam.cam.persistence.models import (
    MaterialStateDependency,
    MaterialStateSuccessorPublication,
)
from hms_cadcam.cam.toolpath import ToolpathCompletionStatus, compute_material_removal_fingerprint


# Reuse the repository's established exact-contour representation tolerance;
# R273 must not introduce a second geometric epsilon. Material work/complete/
# overcut classification continues to use the explicit operation tolerance.
_MAX_CELLS = 65_536
_MAX_RASTERS = 2_048
_MAX_LEVELS = 256
_MAX_SPANS = 16_384
_MAX_CHECKS = 4_000_000
_CANCEL_CADENCE = 256
_IDENTITY_AFFINE_VALUES = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def _fail(code: RestFinishingDiagnosticCode, message: str) -> None:
    raise RestFinishingValidationError(code, message)


def _cancelled(callback: Callable[[], bool] | None) -> None:
    if callback is not None and callback():
        _fail(RestFinishingDiagnosticCode.CANCELLED, "Rest Finishing geometry planning was cancelled")


def _fingerprint_payload(candidate: RestMaterialStateCandidate) -> dict[str, object]:
    return {
        "producer": str(candidate.producer_operation_id),
        "state": candidate.state.fingerprint.to_dict(),
        "state_seal": candidate.state.content_integrity_fingerprint.to_dict(),
        "dependency": candidate.dependency.to_dict(),
        "edge": candidate.edge.to_dict(),
        "artifact": candidate.producer_artifact.artifact_fingerprint.to_dict(),
    }


@dataclass(frozen=True, slots=True)
class RestFinishingGeometryInputs:
    setup: Setup
    parameters: RestFinishingParameters
    profile_selection: RestFinishingProfileSelection
    material_candidates: tuple[RestMaterialStateCandidate, ...]
    producer_completion: MaterialStateSuccessorPublication
    producer_dependency: MaterialStateDependency
    producer_parent_state: MaterialState
    producer_validation_certificate: R272ValidatedSuccessorCertificate
    dependency_graph: DependencyGraph
    assembly: ToolAssembly
    assembly_evidence: ToolAssemblyEvidence
    tool: ToolDefinition
    machine: MachineDefinition
    machine_requirement: MachineRequirement
    machine_evidence: MachineEvidence
    consumer_operation_id: OperationId
    profile_resolver: Callable[[GeometryReference], ResolvedContourProfile]
    cancellation: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class RestFinishingGeometryAuthority:
    consumer_operation_id: OperationId
    consumer_operation_revision: Revision
    parameters: RestFinishingParameters
    profile_selection: RestFinishingProfileSelection
    profile_path: ContourPath
    material_candidate: RestMaterialStateCandidate
    producer_completion: MaterialStateSuccessorPublication
    producer_dependency: MaterialStateDependency
    producer_parent_state: MaterialState
    producer_validation_fingerprint: ContentFingerprint
    setup: Setup
    assembly: ToolAssembly
    tool: ToolDefinition
    machine: MachineDefinition
    machine_requirement: MachineRequirement
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_FINISHING_GEOMETRY_AUTHORITY",
            "format_version": 1,
            "consumer": str(self.consumer_operation_id),
            "consumer_revision": self.consumer_operation_revision.to_dict(),
            "parameters": self.parameters.to_dict(),
            "profile_selection": self.profile_selection.fingerprint.to_dict(),
            "profile_path": self.profile_path.source_fingerprint.to_dict(),
            "material": _fingerprint_payload(self.material_candidate),
            "producer_completion": self.producer_completion.to_dict(),
            "producer_dependency": self.producer_dependency.to_dict(),
            "producer_parent_state": self.producer_parent_state.fingerprint.to_dict(),
            "producer_parent_state_seal": self.producer_parent_state.content_integrity_fingerprint.to_dict(),
            "producer_validation_authority": (
                self.producer_validation_fingerprint.to_dict()
            ),
            "setup": ContentFingerprint.from_payload(self.setup.to_dict()).to_dict(),
            "assembly": self.assembly.content_fingerprint.to_dict(),
            "tool": self.tool.content_fingerprint.to_dict(),
            "machine": self.machine.content_fingerprint.to_dict(),
            "machine_requirement": {
                "id": str(self.machine_requirement.machine_id),
                "revision": self.machine_requirement.expected_revision.to_dict(),
                "fingerprint": self.machine_requirement.expected_fingerprint.to_dict(),
                "unit": self.machine_requirement.unit.value,
                "capabilities": [value.value for value in self.machine_requirement.required_capabilities],
            },
        }))


@dataclass(frozen=True, slots=True)
class RestFinishingWorkComponent:
    cells: tuple[tuple[int, int], ...]
    min_y: float
    min_x: float
    max_y: float
    max_x: float
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if not self.cells or tuple(sorted(self.cells)) != self.cells or len(set(self.cells)) != len(self.cells):
            raise ValueError("Rest Finishing component cells are invalid")
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_FINISHING_WORK_COMPONENT", "format_version": 1,
            "cells": [list(value) for value in self.cells],
            "bounds": [self.min_y, self.min_x, self.max_y, self.max_x],
        }))


@dataclass(frozen=True, slots=True)
class RestFinishingRasterPosition:
    index: int
    y: float
    intervals: tuple[tuple[float, float], ...]
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0 or not math.isfinite(self.y):
            raise ValueError("Rest Finishing raster position is invalid")
        if any(not (math.isfinite(start) and math.isfinite(end) and start < end) for start, end in self.intervals):
            raise ValueError("Rest Finishing raster intervals are invalid")
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_FINISHING_RASTER_POSITION", "format_version": 1,
            "index": self.index, "y": self.y, "intervals": [list(value) for value in self.intervals],
        }))


@dataclass(frozen=True, slots=True)
class RestFinishingRasterSpan:
    component_index: int
    raster_index: int
    y: float
    start_x: float
    end_x: float
    reverse: bool
    responsible_cells: tuple[tuple[int, int], ...]
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if (type(self.component_index) is not int or self.component_index < 0
                or type(self.raster_index) is not int or self.raster_index < 0
                or not all(math.isfinite(value) for value in (self.y, self.start_x, self.end_x))
                or self.start_x >= self.end_x or type(self.reverse) is not bool
                or not self.responsible_cells or tuple(sorted(self.responsible_cells)) != self.responsible_cells):
            raise ValueError("Rest Finishing raster span is invalid")
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_FINISHING_RASTER_SPAN", "format_version": 1,
            "component": self.component_index, "raster": self.raster_index, "y": self.y,
            "start_x": self.start_x, "end_x": self.end_x, "reverse": self.reverse,
            "cells": [list(value) for value in self.responsible_cells],
        }))


@dataclass(frozen=True, slots=True)
class RestFinishingLevelPlan:
    tip_z: float
    state_fingerprint: ContentFingerprint
    state_content_seal: ContentFingerprint
    work_cells: tuple[tuple[int, int], ...]
    work_components: tuple[RestFinishingWorkComponent, ...]
    spans: tuple[RestFinishingRasterSpan, ...]
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.tip_z) or not self.work_cells or not self.work_components or not self.spans:
            raise ValueError("Rest Finishing level plan is empty or invalid")
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_FINISHING_LEVEL_PLAN", "format_version": 1,
            "tip_z": self.tip_z, "state": self.state_fingerprint.to_dict(),
            "state_seal": self.state_content_seal.to_dict(),
            "work_cells": [list(value) for value in self.work_cells],
            "components": [value.fingerprint.to_dict() for value in self.work_components],
            "spans": [value.fingerprint.to_dict() for value in self.spans],
        }))


@dataclass(frozen=True, slots=True)
class RestFinishingRasterPlan:
    authority: RestFinishingGeometryAuthority
    predecessor_state: MaterialState
    material_candidate: RestMaterialStateCandidate
    target_path: ContourPath
    cutter_center_loop: ContourLoop
    target_cells: tuple[tuple[int, int], ...]
    initial_work_components: tuple[RestFinishingWorkComponent, ...]
    levels: tuple[float, ...]
    raster_positions: tuple[RestFinishingRasterPosition, ...]
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if not self.target_cells or not self.initial_work_components or not self.levels or not self.raster_positions:
            raise ValueError("Rest Finishing raster plan is invalid")
        if any(first <= second for first, second in zip(self.levels, self.levels[1:])):
            raise ValueError("Rest Finishing levels must be strictly descending")
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_FINISHING_RASTER_PLAN", "format_version": 1,
            "authority": self.authority.fingerprint.to_dict(),
            "predecessor": self.predecessor_state.fingerprint.to_dict(),
            "predecessor_seal": self.predecessor_state.content_integrity_fingerprint.to_dict(),
            "target_path": self.target_path.source_fingerprint.to_dict(),
            "center_loop": self.cutter_center_loop.to_dict(),
            "target_cells": [list(value) for value in self.target_cells],
            "components": [value.fingerprint.to_dict() for value in self.initial_work_components],
            "levels": list(self.levels),
            "rasters": [value.fingerprint.to_dict() for value in self.raster_positions],
        }))


@dataclass(frozen=True, slots=True)
class NoRestFinishingMaterial:
    authority: RestFinishingGeometryAuthority
    predecessor_state: MaterialState
    target_path: ContourPath
    target_cells: tuple[tuple[int, int], ...]
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_NO_REST_FINISHING_MATERIAL", "format_version": 1,
            "authority": self.authority.fingerprint.to_dict(),
            "state": self.predecessor_state.fingerprint.to_dict(),
            "state_seal": self.predecessor_state.content_integrity_fingerprint.to_dict(),
            "target_path": self.target_path.source_fingerprint.to_dict(),
            "target_cells": [list(value) for value in self.target_cells],
        }))


RestFinishingGeometryResult: TypeAlias = RestFinishingRasterPlan | NoRestFinishingMaterial


def _material_removal_operation_fingerprint(operation: Operation) -> ContentFingerprint:
    """Apply the established R272 removal-authority projection."""
    if not isinstance(operation, Operation):
        raise TypeError("Material-removal operation authority is invalid")
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


def _feed_only_material_artifact(operation: Operation, artifact) -> bool:
    """Recognize the established R272 feed-only retained removal artifact."""

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


def _validate_state(state: MaterialState, setup: Setup, parameters: RestFinishingParameters) -> None:
    stock = setup.stock
    assert isinstance(stock, BoxStock)
    expected_stock = ContentFingerprint.from_payload(stock.to_dict())
    expected_setup = material_state_setup_fingerprint(setup)
    volume = sum(state.top_heights) * state.cell_size_x * state.cell_size_y
    initial = stock.size_x.value * stock.size_y.value * stock.size_z.value
    volume_tolerance = max(_EPSILON, state.precision.tolerance, abs(initial) * 1.0e-10)
    if (state.status is not MaterialStateStatus.COMPLETE
            or state.engine_version != MATERIAL_STATE_ENGINE_VERSION
            or state.precision != MaterialStatePrecisionPolicy()
            or state.unit is not parameters.unit
            or state.stock_fingerprint != expected_stock or state.setup_fingerprint != expected_setup
            or state.content_integrity_fingerprint != state.computed_content_integrity_fingerprint()
            or state.verification_origin not in {MaterialStateVerificationOrigin.TRUSTED_CALCULATED,
                                                 MaterialStateVerificationOrigin.TRUSTED_PERSISTED}
            or not state.content_is_verified or not 1 <= state.width * state.height <= _MAX_CELLS
            or not math.isclose(state.width * state.cell_size_x, stock.size_x.value,
                                rel_tol=0.0, abs_tol=_EPSILON)
            or not math.isclose(state.height * state.cell_size_y, stock.size_y.value,
                                rel_tol=0.0, abs_tol=_EPSILON)
            or any(not math.isfinite(value) or value < 0.0 or value > stock.size_z.value + _EPSILON
                   for value in state.top_heights)
            or not math.isclose(state.initial_volume, initial, rel_tol=0.0, abs_tol=volume_tolerance)
            or not math.isclose(state.remaining_volume, volume, rel_tol=0.0, abs_tol=volume_tolerance)):
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
              "Rest Finishing state does not match current engine, stock, setup or content seal")


def _select_candidate(
    inputs: RestFinishingGeometryInputs,
) -> tuple[RestMaterialStateCandidate, Operation, Operation, ContentFingerprint]:
    if inputs.dependency_graph != inputs.setup.operation_tree.dependency_graph:
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID, "Detached dependency graph is not authority")
    operations = {value.operation_id: value for value in inputs.setup.operation_tree.operations}
    consumer = operations.get(inputs.consumer_operation_id)
    if (consumer is None or not consumer.enabled or consumer.diagnostics
            or consumer.setup_id != inputs.setup.setup_id
            or consumer.family is not OperationFamily.MILLING
            or consumer.strategy_key != REST_FINISHING_STRATEGY_KEY):
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_MISSING,
              "Rest Finishing consumer is absent from the current Setup")
    edges = tuple(edge for edge in inputs.dependency_graph.edges
                  if edge.kind is DependencyKind.MATERIAL_STATE
                  and edge.target_operation_id == inputs.consumer_operation_id)
    if not edges:
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_MISSING, "Material-state edge is missing")
    if len(edges) != 1:
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_AMBIGUOUS, "Material-state edge is ambiguous")
    edge = edges[0]
    matches = tuple(value for value in inputs.material_candidates if value.edge == edge)
    if not matches:
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_MISSING, "Material-state candidate is missing")
    if len(matches) != 1:
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_AMBIGUOUS, "Material-state candidate is ambiguous")
    candidate = matches[0]
    producer = operations.get(candidate.producer_operation_id)
    if producer is None or edge.source_operation_id != producer.operation_id or not producer.enabled:
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID, "Material-state producer is not current")
    artifact = candidate.producer_artifact
    state = producer.artifact_state
    dependency = candidate.dependency
    completion = inputs.producer_completion
    producer_dependency = inputs.producer_dependency
    producer_parent = inputs.producer_parent_state
    completion_is_self_valid = False
    if isinstance(completion, MaterialStateSuccessorPublication):
        try:
            completion_payload = completion.to_dict()
            # to_dict() emits the canonical COMPLETE payload.  Retain the
            # actual in-memory status so mutation cannot be normalized away.
            completion_payload["status"] = completion.status
            completion_is_self_valid = (
                MaterialStateSuccessorPublication.from_dict(completion_payload)
                == completion
            )
        except (AttributeError, TypeError, ValueError, CamValidationError):
            completion_is_self_valid = False
    producer_dependency_is_self_valid = False
    if isinstance(producer_dependency, MaterialStateDependency):
        try:
            producer_dependency_is_self_valid = (
                MaterialStateDependency.from_dict(producer_dependency.to_dict())
                == producer_dependency
            )
        except (AttributeError, TypeError, ValueError, CamValidationError):
            producer_dependency_is_self_valid = False
    producer_machine = producer.machine_requirement
    machine_matches = (
        artifact.machine_id is None and artifact.machine_fingerprint is None
        if producer_machine is None else
        artifact.machine_id == producer_machine.machine_id
        and artifact.machine_fingerprint == producer_machine.expected_fingerprint
        and artifact.unit is producer_machine.unit
    )
    parent_operation = (
        operations.get(producer_dependency.producer_operation_id)
        if producer_dependency_is_self_valid
        else None
    )
    parent_edge = DependencyEdge.material_state(
        producer_dependency.producer_operation_id,
        producer.operation_id,
    ) if producer_dependency_is_self_valid else None
    incoming_parent_edges = tuple(
        value
        for value in inputs.dependency_graph.edges
        if value.kind is DependencyKind.MATERIAL_STATE
        and value.target_operation_id == producer.operation_id
    )
    exact_operation_state = (
        state.status is ArtifactStatus.VALID
        and not state.dirty_reasons
        and state.artifact_fingerprint == artifact.artifact_fingerprint
        and artifact.operation_revision == producer.revision
    )
    feed_only_operation_state = _feed_only_material_artifact(producer, artifact)
    if (not completion_is_self_valid
            or not producer_dependency_is_self_valid
            or dependency.consumer_operation_id != consumer.operation_id
            or dependency.producer_operation_id != producer.operation_id
            or not isinstance(producer_parent, MaterialState)
            or not producer_parent.content_is_verified
            or producer_dependency.consumer_operation_id != producer.operation_id
            or producer_dependency.successor_publication != completion
            or parent_operation is None
            or not parent_operation.enabled
            or producer_dependency.producer_operation_authority_fingerprint
               != _material_removal_operation_fingerprint(parent_operation)
            or producer_dependency.parent_state_fingerprint != producer_parent.fingerprint
            or producer_dependency.producer_toolpath_fingerprint
               != producer_parent.toolpath_fingerprint
            or producer_dependency.setup_fingerprint != producer_parent.setup_fingerprint
            or producer_dependency.stock_fingerprint != producer_parent.stock_fingerprint
            or producer_dependency.engine_version != producer_parent.engine_version
            or producer_dependency.precision != producer_parent.precision.to_dict()
            or producer_parent.setup_fingerprint != candidate.state.setup_fingerprint
            or producer_parent.stock_fingerprint != candidate.state.stock_fingerprint
            or producer_parent.engine_version != candidate.state.engine_version
            or producer_parent.precision != candidate.state.precision
            or producer_parent.unit is not candidate.state.unit
            or incoming_parent_edges != (parent_edge,)
            or completion.consumer_operation_id != producer.operation_id
            or completion.artifact_id != artifact.artifact_id
            or completion.artifact_fingerprint != artifact.artifact_fingerprint
            or completion.input_fingerprint != artifact.input_fingerprint
            or completion.semantic_material_removal_fingerprint
               != compute_material_removal_fingerprint(artifact)
            or completion.successor_state_fingerprint != candidate.state.fingerprint
            or completion.successor_state_content_seal
               != candidate.state.content_integrity_fingerprint
            or completion.setup_fingerprint != candidate.state.setup_fingerprint
            or completion.stock_fingerprint != candidate.state.stock_fingerprint
            or completion.engine_version != candidate.state.engine_version
            or completion.precision != candidate.state.precision.to_dict()
            or completion.parent_state_fingerprint != producer_parent.fingerprint
            or completion.parent_state_content_seal
               != producer_parent.content_integrity_fingerprint
            or candidate.state.parent_fingerprint != producer_parent.fingerprint
            or not (exact_operation_state or feed_only_operation_state)
            or state.input_fingerprint != artifact.input_fingerprint
            or state.generation != artifact.computation_token.generation
            or artifact.source_operation_id != producer.operation_id
            or (
                artifact.operation_revision != producer.revision
                and not feed_only_operation_state
            )
            or artifact.setup_id != inputs.setup.setup_id
            or artifact.wcs_fingerprint != ContentFingerprint.from_payload(inputs.setup.wcs.to_dict())
            or artifact.tool_assembly_id != producer.tool_assembly.assembly_id
            or artifact.tool_assembly_fingerprint.algorithm != producer.tool_assembly.expected_fingerprint.algorithm
            or artifact.tool_assembly_fingerprint.algorithm_version != producer.tool_assembly.expected_fingerprint.algorithm_version
            or artifact.tool_assembly_fingerprint.digest != producer.tool_assembly.expected_fingerprint.digest
            or artifact.completion_status is not ToolpathCompletionStatus.COMPLETE
            or not machine_matches
            or compute_material_removal_fingerprint(artifact) != candidate.state.toolpath_fingerprint):
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
              "Material-state producer artifact is stale or foreign")
    try:
        validation_fingerprint = require_r272_validated_successor_certificate(
            inputs.producer_validation_certificate,
            setup=inputs.setup,
            producer_operation=producer,
            artifact=artifact,
            parent_state=producer_parent,
            successor_state=candidate.state,
            completion=completion,
            dependency=producer_dependency,
            cancellation=inputs.cancellation,
        )
    except RestContourValidationError as error:
        if error.code is RestContourDiagnosticCode.CANCELLED:
            _fail(RestFinishingDiagnosticCode.CANCELLED, str(error))
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
              f"R272 producer validation certificate rejected: {error}")
    return candidate, consumer, producer, validation_fingerprint


def _validate_machine(inputs: RestFinishingGeometryInputs) -> None:
    machine, requirement, evidence = inputs.machine, inputs.machine_requirement, inputs.machine_evidence
    axes = machine.axes
    if (requirement.machine_id != machine.machine_id
            or requirement.expected_revision != machine.revision
            or requirement.expected_fingerprint != machine.content_fingerprint
            or requirement.unit is not machine.unit or machine.unit is not inputs.parameters.unit
            or evidence.revision != machine.revision or evidence.fingerprint != machine.content_fingerprint
            or evidence.unit is not machine.unit
            or assess_machine_compatibility(requirement, evidence).value != "compatible"
            or machine.kind is not MachineKind.MILL or not machine.capabilities.milling
            or OperationCapability.MILLING not in requirement.required_capabilities
            or len(axes) != 3 or any(axis.axis_type is not MachineAxisType.LINEAR for axis in axes)
            or len({axis.name for axis in axes}) != 3
            or set(node.axis_name for node in machine.kinematic_chain.nodes if node.axis_name is not None)
               != {axis.name for axis in axes}
            or any(tuple(node.axis_name for node in machine.kinematic_chain.nodes
                         if node.axis_name is not None).count(axis.name) != 1 for axis in axes)
            or any(node.axis_name is not None and node.side is not KinematicSide.TOOL
                   for node in machine.kinematic_chain.nodes)
            # The current machine domain explicitly has no inverse-kinematic
            # behavior or Setup-to-machine placement binding.  R273 therefore
            # cannot prove travel through non-identity fixed transforms and
            # must reject them instead of treating Setup-WCS points as machine
            # coordinates.
            or any(node.fixed_transform.values != _IDENTITY_AFFINE_VALUES
                   for node in machine.kinematic_chain.nodes)
            or abs(axes[0].direction.dot(axes[1].direction)) > WCS_ORTHONORMAL_TOLERANCE
            or abs(axes[0].direction.dot(axes[2].direction)) > WCS_ORTHONORMAL_TOLERANCE
            or abs(axes[1].direction.dot(axes[2].direction)) > WCS_ORTHONORMAL_TOLERANCE
            or abs(axes[0].direction.cross(axes[1].direction).dot(axes[2].direction))
               <= WCS_ORTHONORMAL_TOLERANCE
            or inputs.parameters.cutting_feed_rate.value > machine.capabilities.maximum_feed.value
            or inputs.parameters.plunge_feed_rate.value > machine.capabilities.maximum_feed.value
            or not any(
                value.minimum_speed.value
                <= inputs.parameters.spindle_speed.value
                <= value.maximum_speed.value
                and SpindleDirection.CLOCKWISE in value.directions
                for value in machine.spindles
            )):
        _fail(RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE,
              "Rest Finishing machine identity, axes, feed or spindle authority is invalid")

    stock = inputs.setup.stock
    assert isinstance(stock, BoxStock)
    _validate_machine_motion_bounds(
        inputs,
        Point3(
            0.0,
            0.0,
            min(0.0, inputs.parameters.nominal_target_z.value, inputs.parameters.cut_z),
            inputs.parameters.unit,
        ),
        Point3(
            stock.size_x.value,
            stock.size_y.value,
            max(
                stock.size_z.value,
                inputs.parameters.retract_height.value,
                inputs.parameters.clearance_height.value,
            ),
            inputs.parameters.unit,
        ),
    )


def _validate_machine_motion_bounds(
    inputs: RestFinishingGeometryInputs,
    minimum: Point3,
    maximum: Point3,
) -> None:
    """Fail closed unless one Setup-WCS box fits machine travel and envelope."""
    if (
        minimum.unit is not inputs.machine.unit
        or maximum.unit is not inputs.machine.unit
        or minimum.x > maximum.x
        or minimum.y > maximum.y
        or minimum.z > maximum.z
    ):
        _fail(
            RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE,
            "Rest Finishing motion bounds are invalid for the selected machine",
        )

    frame = inputs.setup.wcs
    corners: list[Point3] = []
    for x in (minimum.x, maximum.x):
        for y in (minimum.y, maximum.y):
            for z in (minimum.z, maximum.z):
                corners.append(Point3(
                    frame.origin.x + frame.x_axis.x * x + frame.y_axis.x * y + frame.z_axis.x * z,
                    frame.origin.y + frame.x_axis.y * x + frame.y_axis.y * y + frame.z_axis.y * z,
                    frame.origin.z + frame.x_axis.z * x + frame.y_axis.z * y + frame.z_axis.z * z,
                    frame.origin.unit,
                ))

    envelope = inputs.machine.work_envelope
    extents = (
        max(point.x for point in corners) - min(point.x for point in corners),
        max(point.y for point in corners) - min(point.y for point in corners),
        max(point.z for point in corners) - min(point.z for point in corners),
    )
    if any(
        extent > limit.value + _EPSILON
        for extent, limit in zip(
            extents,
            (envelope.size_x, envelope.size_y, envelope.size_z),
            strict=True,
        )
    ):
        _fail(
            RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE,
            "Rest Finishing Setup or motion exceeds the machine work envelope",
        )

    for axis in inputs.machine.axes:
        projections = tuple(
            axis.direction.dot(Vector3(point.x, point.y, point.z))
            for point in corners
        )
        # Without an authoritative work-offset binding the absolute machine
        # position is not known at R273.  Prove only the represented capacity:
        # the required span must fit the axis travel.  Physical placement and
        # true machine-coordinate travel remain outside this core revision.
        required_span = max(projections) - min(projections)
        available_span = axis.maximum.value - axis.minimum.value
        if required_span > available_span + _EPSILON:
            _fail(
                RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE,
                f"Rest Finishing Setup or motion span exceeds axis {axis.name} travel",
            )


def _validate_authority(inputs: RestFinishingGeometryInputs) -> tuple[RestFinishingGeometryAuthority, MaterialState, ContourPath, CutterEnvelope]:
    if not isinstance(inputs, RestFinishingGeometryInputs):
        raise TypeError("Rest Finishing geometry inputs are invalid")
    if (not isinstance(inputs.setup, Setup) or not inputs.setup.enabled
            or not isinstance(inputs.setup.stock, BoxStock)
            or inputs.setup.stock.frame != inputs.setup.wcs):
        _fail(RestFinishingDiagnosticCode.GEOMETRY_INVALID,
              "Rest Finishing requires an enabled Setup with exact Box Stock in Setup WCS")
    if (not isinstance(inputs.parameters, RestFinishingParameters)
            or not isinstance(inputs.profile_selection, RestFinishingProfileSelection)
            or not isinstance(inputs.dependency_graph, DependencyGraph)
            or not isinstance(inputs.material_candidates, tuple)
            or not isinstance(inputs.producer_completion, MaterialStateSuccessorPublication)
            or not isinstance(inputs.assembly, ToolAssembly)
            or not isinstance(inputs.assembly_evidence, ToolAssemblyEvidence)
            or not isinstance(inputs.tool, ToolDefinition)
            or not isinstance(inputs.machine, MachineDefinition)
            or not isinstance(inputs.machine_requirement, MachineRequirement)
            or not isinstance(inputs.machine_evidence, MachineEvidence)
            or not isinstance(inputs.consumer_operation_id, OperationId)
            or not callable(inputs.profile_resolver)
            or (inputs.cancellation is not None and not callable(inputs.cancellation))):
        _fail(RestFinishingDiagnosticCode.INVALID_PARAMETERS, "Rest Finishing authority input types are invalid")
    _cancelled(inputs.cancellation)
    candidate, consumer, _, producer_validation_fingerprint = _select_candidate(inputs)
    _validate_state(candidate.state, inputs.setup, inputs.parameters)
    dependency = candidate.dependency
    if (dependency.parent_state_fingerprint != candidate.state.fingerprint
            or dependency.stock_fingerprint != candidate.state.stock_fingerprint
            or dependency.setup_fingerprint != candidate.state.setup_fingerprint
            or dependency.engine_version != MATERIAL_STATE_ENGINE_VERSION
            or dependency.precision != MaterialStatePrecisionPolicy().to_dict()):
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID, "Material-state dependency is stale")
    if RestFinishingParameters.from_operation_parameters(consumer.parameters) != inputs.parameters:
        _fail(RestFinishingDiagnosticCode.INVALID_PARAMETERS, "Parameters differ from current operation")
    profile_inputs = tuple(value for value in consumer.geometry_inputs if value.role is GeometryInputRole.PROFILE)
    descriptor = inputs.profile_selection.descriptor
    if (len(consumer.geometry_inputs) != 1 or len(profile_inputs) != 1
            or not profile_inputs[0].required or profile_inputs[0].reference != descriptor.reference):
        _fail(RestFinishingDiagnosticCode.PROFILE_INVALID, "Profile differs from current operation")
    inputs.profile_selection.validate_for(inputs.parameters)
    expected_kind = (GeometryReferenceKind.FACE
                     if inputs.parameters.profile_source is ContourProfileSource.PLANAR_FACE_OUTER
                     else GeometryReferenceKind.SKETCH_OR_PROFILE)
    current = inputs.profile_resolver(descriptor.reference)
    if (not isinstance(current, ResolvedContourProfile)
            or current.status is not GeometryResolutionStatus.RESOLVED or current.profile != descriptor
            or descriptor.reference.source_id != inputs.setup.source_scope.primary_source_id
            or descriptor.reference.kind is not expected_kind
            or descriptor.reference.expected_geometry_fingerprint != descriptor.geometry_fingerprint
            or descriptor.inner_loops):
        _fail(RestFinishingDiagnosticCode.PROFILE_INVALID, "Profile authority is stale, foreign or unsupported")
    try:
        path = resolve_profile_in_setup(descriptor, inputs.setup)
    except ContourGenerationError as error:
        raise RestFinishingValidationError(RestFinishingDiagnosticCode.GEOMETRY_INVALID, str(error)) from error
    if _loop_self_intersects(path.loop):
        _fail(RestFinishingDiagnosticCode.GEOMETRY_INVALID,
              "Rest Finishing target profile self-intersects")
    if any(abs(point.z - path.loop.segments[0].start.z) > _EPSILON
           for segment in path.loop.segments for point in (segment.start, segment.end)):
        _fail(RestFinishingDiagnosticCode.GEOMETRY_INVALID, "Target profile is not planar")
    stock = inputs.setup.stock
    assert isinstance(stock, BoxStock)
    if (inputs.parameters.unit is not inputs.tool.unit or inputs.parameters.unit is not stock.size_x.unit
            or not 0.0 <= inputs.parameters.nominal_target_z.value <= stock.size_z.value
            or inputs.parameters.cut_z > stock.size_z.value):
        _fail(RestFinishingDiagnosticCode.GEOMETRY_INVALID, "Target, stock or tool units are outside authority")
    if any(point.x < -_EPSILON or point.x > stock.size_x.value + _EPSILON
           or point.y < -_EPSILON or point.y > stock.size_y.value + _EPSILON
           for segment in path.loop.segments for point in (segment.start, segment.end)):
        _fail(RestFinishingDiagnosticCode.PATH_OUTSIDE_AUTHORITY, "Target profile is outside stock")
    if (consumer.machine_requirement != inputs.machine_requirement
            or consumer.tool_assembly != ToolAssemblyReference.from_assembly(inputs.assembly)
            or assess_tool_assembly(inputs.assembly, inputs.assembly_evidence) is not ToolAssemblyStatus.VALID
            or inputs.assembly.tool_id != inputs.tool.tool_id
            or inputs.assembly.expected_tool_revision != inputs.tool.revision
            or inputs.assembly.expected_tool_fingerprint != inputs.tool.content_fingerprint
            or inputs.assembly_evidence.tool_revision != inputs.tool.revision
            or inputs.assembly_evidence.tool_fingerprint != inputs.tool.content_fingerprint
            or inputs.tool.family is not ToolFamily.END_MILL
            or not isinstance(inputs.tool.cutting_geometry, CylindricalGeometry)):
        _fail(RestFinishingDiagnosticCode.TOOL_INELIGIBLE, "Rest Finishing requires a current flat End Mill assembly")
    geometry = inputs.tool.cutting_geometry
    if (inputs.parameters.stepover.value > geometry.diameter.value
            or inputs.parameters.max_stepdown.value > geometry.axial_cutting_length.value
            or inputs.parameters.max_stepdown.value > inputs.assembly.stickout.value):
        _fail(RestFinishingDiagnosticCode.TOOL_INELIGIBLE, "Stepover, stepdown or reach exceeds cutter authority")
    _validate_machine(inputs)
    try:
        envelope = CutterEnvelope.from_tool(inputs.tool)
    except CamValidationError as error:
        raise RestFinishingValidationError(RestFinishingDiagnosticCode.TOOL_INELIGIBLE, str(error)) from error
    authority = RestFinishingGeometryAuthority(
        consumer.operation_id, consumer.revision, inputs.parameters, inputs.profile_selection,
        path, candidate, inputs.producer_completion,
        inputs.producer_dependency,
        inputs.producer_parent_state,
        producer_validation_fingerprint,
        inputs.setup,
        inputs.assembly, inputs.tool, inputs.machine,
        inputs.machine_requirement,
    )
    return authority, candidate.state, path, envelope


def _angle_on_arc(segment, angle: float) -> bool:
    assert segment.center is not None and segment.sweep_radians is not None
    start = math.atan2(segment.start.y - segment.center.y, segment.start.x - segment.center.x)
    if segment.sweep_radians > 0.0:
        return (angle - start) % math.tau <= segment.sweep_radians + _EPSILON
    return (start - angle) % math.tau <= -segment.sweep_radians + _EPSILON


def _point_on_segment(segment, x: float, y: float) -> bool:
    if segment.kind is ContourCurveKind.LINE:
        dx, dy = segment.end.x - segment.start.x, segment.end.y - segment.start.y
        cross = dx * (y - segment.start.y) - dy * (x - segment.start.x)
        scale = max(1.0, math.hypot(dx, dy))
        return (abs(cross) <= _EPSILON * scale
                and min(segment.start.x, segment.end.x) - _EPSILON <= x <= max(segment.start.x, segment.end.x) + _EPSILON
                and min(segment.start.y, segment.end.y) - _EPSILON <= y <= max(segment.start.y, segment.end.y) + _EPSILON)
    assert segment.center is not None and segment.radius is not None
    radius = math.hypot(x - segment.center.x, y - segment.center.y)
    return (abs(radius - segment.radius) <= _EPSILON
            and _angle_on_arc(segment, math.atan2(y - segment.center.y, x - segment.center.x)))


def _segment_intersections(first, second) -> tuple[tuple[float, float], ...]:
    values: list[tuple[float, float]] = []
    if first.kind is ContourCurveKind.LINE and second.kind is ContourCurveKind.LINE:
        px, py = first.start.x, first.start.y
        rx, ry = first.end.x - px, first.end.y - py
        qx, qy = second.start.x, second.start.y
        sx, sy = second.end.x - qx, second.end.y - qy
        denominator = rx * sy - ry * sx
        if abs(denominator) <= _EPSILON:
            for point in (first.start, first.end, second.start, second.end):
                if _point_on_segment(first, point.x, point.y) and _point_on_segment(second, point.x, point.y):
                    values.append((point.x, point.y))
        else:
            t = ((qx - px) * sy - (qy - py) * sx) / denominator
            u = ((qx - px) * ry - (qy - py) * rx) / denominator
            if -_EPSILON <= t <= 1.0 + _EPSILON and -_EPSILON <= u <= 1.0 + _EPSILON:
                values.append((px + t * rx, py + t * ry))
    elif first.kind is ContourCurveKind.LINE or second.kind is ContourCurveKind.LINE:
        line, arc = (first, second) if first.kind is ContourCurveKind.LINE else (second, first)
        assert arc.center is not None and arc.radius is not None
        dx, dy = line.end.x - line.start.x, line.end.y - line.start.y
        fx, fy = line.start.x - arc.center.x, line.start.y - arc.center.y
        a = dx * dx + dy * dy
        b = 2.0 * (fx * dx + fy * dy)
        c = fx * fx + fy * fy - arc.radius * arc.radius
        discriminant = b * b - 4.0 * a * c
        if discriminant >= -_EPSILON:
            root = math.sqrt(max(0.0, discriminant))
            for t in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)):
                if -_EPSILON <= t <= 1.0 + _EPSILON:
                    x, y = line.start.x + t * dx, line.start.y + t * dy
                    if _point_on_segment(arc, x, y):
                        values.append((x, y))
    else:
        assert first.center is not None and first.radius is not None
        assert second.center is not None and second.radius is not None
        dx, dy = second.center.x - first.center.x, second.center.y - first.center.y
        distance = math.hypot(dx, dy)
        if (_EPSILON < distance <= first.radius + second.radius + _EPSILON
                and distance >= abs(first.radius - second.radius) - _EPSILON):
            along = (first.radius ** 2 - second.radius ** 2 + distance ** 2) / (2.0 * distance)
            height = math.sqrt(max(0.0, first.radius ** 2 - along ** 2))
            base_x = first.center.x + along * dx / distance
            base_y = first.center.y + along * dy / distance
            for x, y in ((base_x - dy * height / distance, base_y + dx * height / distance),
                         (base_x + dy * height / distance, base_y - dx * height / distance)):
                if _point_on_segment(first, x, y) and _point_on_segment(second, x, y):
                    values.append((x, y))
    result: list[tuple[float, float]] = []
    for point in sorted(values):
        if not result or math.hypot(point[0] - result[-1][0], point[1] - result[-1][1]) > _EPSILON:
            result.append(point)
    return tuple(result)


def _loop_self_intersects(loop: ContourLoop) -> bool:
    count = len(loop.segments)
    for first_index, first in enumerate(loop.segments):
        for second_index in range(first_index + 1, count):
            if second_index == first_index + 1 or (first_index == 0 and second_index == count - 1):
                continue
            if _segment_intersections(first, loop.segments[second_index]):
                return True
    return False


def _scanline_boundary_x(loop: ContourLoop, y: float) -> tuple[float, ...]:
    """Return unique exact boundary candidates, retaining tangent locations."""
    intersections: list[float] = []
    for segment in loop.segments:
        if segment.kind is ContourCurveKind.LINE:
            y1, y2 = segment.start.y, segment.end.y
            if abs(y2 - y1) <= _EPSILON:
                if abs(y - y1) <= _EPSILON:
                    intersections.extend((segment.start.x, segment.end.x))
                continue
            if min(y1, y2) - _EPSILON <= y <= max(y1, y2) + _EPSILON:
                ratio = (y - y1) / (y2 - y1)
                if -_EPSILON <= ratio <= 1.0 + _EPSILON:
                    ratio = min(1.0, max(0.0, ratio))
                    intersections.append(
                        segment.start.x + ratio * (segment.end.x - segment.start.x)
                    )
            continue
        assert segment.center is not None and segment.radius is not None
        offset = y - segment.center.y
        if abs(offset) > segment.radius + _EPSILON:
            continue
        delta = math.sqrt(max(0.0, segment.radius * segment.radius - offset * offset))
        for x in (segment.center.x - delta, segment.center.x + delta):
            angle = math.atan2(offset, x - segment.center.x)
            if _angle_on_arc(segment, angle):
                intersections.append(x)
    ordered: list[float] = []
    for value in sorted(intersections):
        if not ordered or abs(value - ordered[-1]) > _EPSILON:
            ordered.append(value)
    return tuple(ordered)


def _ray_inside_at_y(loop: ContourLoop, x: float, y: float) -> bool:
    """Odd/even +X ray test at a non-boundary probe ordinate."""
    crossings = 0
    for segment in loop.segments:
        if segment.kind is ContourCurveKind.LINE:
            y1, y2 = segment.start.y, segment.end.y
            # Standard half-open vertex law counts a shared vertex once.
            if (y1 > y) == (y2 > y):
                continue
            intersection = segment.start.x + (
                (y - y1) * (segment.end.x - segment.start.x) / (y2 - y1)
            )
            if intersection > x:
                crossings += 1
            continue
        assert segment.center is not None and segment.radius is not None
        offset = y - segment.center.y
        if abs(offset) > segment.radius:
            continue
        delta = math.sqrt(max(0.0, segment.radius * segment.radius - offset * offset))
        # A true interior tangency contributes twice at the same X and thus
        # leaves parity unchanged. Endpoint coincidences are avoided by the
        # one-ULP probe ordinates used by the caller.
        for intersection in (
            segment.center.x - delta,
            segment.center.x + delta,
        ):
            angle = math.atan2(offset, intersection - segment.center.x)
            if intersection > x and _angle_on_arc(segment, angle):
                crossings += 1
    return crossings % 2 == 1


def _horizontal_intervals(loop: ContourLoop, y: float) -> tuple[tuple[float, float], ...]:
    """Classify exact LINE/ARC scanline cells without tangent parity loss."""
    boundaries = _scanline_boundary_x(loop, y)
    intervals: list[tuple[float, float]] = []
    above = math.nextafter(y, math.inf)
    below = math.nextafter(y, -math.inf)
    for start, end in zip(boundaries, boundaries[1:]):
        if end - start <= _EPSILON:
            continue
        midpoint = (start + end) / 2.0
        on_boundary = any(
            _point_on_segment(segment, midpoint, y)
            for segment in loop.segments
        )
        if (
            on_boundary
            or _ray_inside_at_y(loop, midpoint, above)
            or _ray_inside_at_y(loop, midpoint, below)
        ):
            if intervals and start <= intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], end)
            else:
                intervals.append((start, end))
    return tuple(intervals)


def _loop_y_bounds(loop: ContourLoop) -> tuple[float, float]:
    values = [point.y for segment in loop.segments for point in (segment.start, segment.end)]
    for segment in loop.segments:
        if segment.kind is ContourCurveKind.ARC:
            assert segment.center is not None and segment.radius is not None
            for angle in (math.pi / 2.0, 3.0 * math.pi / 2.0):
                if _angle_on_arc(segment, angle):
                    values.append(segment.center.y + segment.radius * math.sin(angle))
    return min(values), max(values)


def _point_on_or_inside(loop: ContourLoop, x: float, y: float) -> bool:
    if any(_point_on_segment(segment, x, y) for segment in loop.segments):
        return True
    intervals = _horizontal_intervals(loop, y)
    return any(start - _EPSILON <= x <= end + _EPSILON for start, end in intervals)


def _cells_inside(
    state: MaterialState,
    loop: ContourLoop,
    cancellation: Callable[[], bool] | None = None,
) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    checks = 0
    for row in range(state.height):
        _cancelled(cancellation)
        y = (row + 0.5) * state.cell_size_y
        intervals = _horizontal_intervals(loop, y)
        for column in range(state.width):
            checks += 1
            if checks % _CANCEL_CADENCE == 0:
                _cancelled(cancellation)
            x = (column + 0.5) * state.cell_size_x
            if (any(_point_on_segment(segment, x, y) for segment in loop.segments)
                    or any(start - _EPSILON <= x <= end + _EPSILON for start, end in intervals)):
                result.append((row, column))
    return tuple(result)


def _components(state: MaterialState, cells: tuple[tuple[int, int], ...]) -> tuple[RestFinishingWorkComponent, ...]:
    remaining = set(cells)
    result: list[RestFinishingWorkComponent] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        queue = [seed]
        while queue:
            row, column = queue.pop()
            for neighbor in ((row - 1, column), (row, column - 1), (row, column + 1), (row + 1, column)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        ordered = tuple(sorted(component))
        xs = tuple((column + 0.5) * state.cell_size_x for _, column in ordered)
        ys = tuple((row + 0.5) * state.cell_size_y for row, _ in ordered)
        result.append(RestFinishingWorkComponent(ordered, min(ys), min(xs), max(ys), max(xs)))
    return tuple(sorted(result, key=lambda value: (
        value.min_y, value.min_x, value.max_y, value.max_x, value.fingerprint.digest,
    )))


def _levels(hmax: float, parameters: RestFinishingParameters) -> tuple[float, ...]:
    cut_z = parameters.cut_z
    stepdown = parameters.max_stepdown.value
    depth = hmax - cut_z
    ratio = depth / stepdown
    if not math.isfinite(ratio) or ratio > _MAX_LEVELS:
        _fail(RestFinishingDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Finishing level limit exceeded")
    count = max(1, math.ceil(ratio))
    values: list[float] = []
    previous = hmax

    def append_safe(formula_level: float) -> None:
        nonlocal previous
        # Binary subtraction can otherwise make previous - formula_level one
        # ULP greater than the explicit maximum stepdown.  Clamp upward only
        # as far as required to retain the hard process ceiling.
        level = max(cut_z, formula_level, previous - stepdown)
        while previous - level > stepdown:
            level = math.nextafter(level, math.inf)
        if level >= previous:
            _fail(
                RestFinishingDiagnosticCode.INVALID_PARAMETERS,
                "Rest Finishing maximum stepdown is below the representable length resolution",
            )
        values.append(level)
        previous = level

    for index in range(1, count + 1):
        append_safe(max(cut_z, hmax - index * stepdown))
    while previous > cut_z:
        if len(values) >= _MAX_LEVELS:
            _fail(RestFinishingDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Finishing level limit exceeded")
        append_safe(cut_z)
    return tuple(values)


def _raster_positions(
    loop: ContourLoop,
    stepover: float,
    cancellation: Callable[[], bool] | None = None,
) -> tuple[RestFinishingRasterPosition, ...]:
    minimum, maximum = _loop_y_bounds(loop)
    ratio = (maximum - minimum) / stepover
    if not math.isfinite(ratio) or ratio > _MAX_RASTERS - 1:
        _fail(RestFinishingDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Finishing raster limit exceeded")
    count = max(1, math.ceil(ratio))
    values: list[float] = []
    for index in range(count + 1):
        _cancelled(cancellation)
        value = minimum + index * stepover
        if value >= maximum - _EPSILON:
            continue
        if values and value <= values[-1]:
            _fail(
                RestFinishingDiagnosticCode.INVALID_PARAMETERS,
                "Rest Finishing stepover is below the representable length resolution",
            )
        values.append(value)
    values.append(maximum)
    positions = tuple(RestFinishingRasterPosition(index, y, _horizontal_intervals(loop, y))
                      for index, y in enumerate(values))
    if not any(value.intervals for value in positions):
        _fail(RestFinishingDiagnosticCode.UNREACHABLE_FINISHING_MATERIAL,
              "Cutter-center raster domain has no positive interval")
    return positions


def _intersections(first: tuple[float, float], second: tuple[float, float]) -> tuple[float, float] | None:
    start, end = max(first[0], second[0]), min(first[1], second[1])
    return (start, end) if end - start > _EPSILON else None


def derive_rest_finishing_level(
    plan: RestFinishingRasterPlan,
    state: MaterialState,
    tip_z: float,
    cancellation: Callable[[], bool] | None = None,
) -> RestFinishingLevelPlan | None:
    """Re-derive one level solely from the current cumulative replay state."""
    if not isinstance(plan, RestFinishingRasterPlan) or not isinstance(state, MaterialState):
        raise TypeError("Rest Finishing level inputs are invalid")
    if not math.isfinite(tip_z) or tip_z < plan.authority.parameters.cut_z - _EPSILON:
        _fail(RestFinishingDiagnosticCode.GEOMETRY_INVALID, "Rest Finishing level Z is invalid")
    _validate_state(state, plan.authority.setup, plan.authority.parameters)
    if (state.parent_fingerprint != plan.predecessor_state.fingerprint
            and state.fingerprint != plan.predecessor_state.fingerprint):
        _fail(RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
              "Replay state is not derived from the authoritative predecessor")
    _cancelled(cancellation)
    parameters = plan.authority.parameters
    terminal = math.isclose(
        tip_z,
        parameters.cut_z,
        rel_tol=0.0,
        abs_tol=max(_EPSILON, state.precision.tolerance),
    )
    threshold = (
        parameters.tolerance.value
        if terminal
        else max(_EPSILON, state.precision.tolerance)
    )
    work = tuple(
        cell
        for cell in plan.target_cells
        if state.top_heights[cell[0] * state.width + cell[1]] > tip_z + threshold
    )
    if not work:
        return None
    components = _components(state, work)
    radius = plan.authority.tool.cutting_geometry.diameter.value / 2.0
    spans: list[RestFinishingRasterSpan] = []
    checks = 0
    covered: set[tuple[int, int]] = set()
    for component_index, component in enumerate(components):
        component_raster_ordinal = 0
        for raster in plan.raster_positions:
            cell_intervals: list[tuple[float, float, tuple[int, int]]] = []
            for cell in component.cells:
                checks += 1
                if checks > _MAX_CHECKS:
                    _fail(RestFinishingDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Finishing coverage-check limit exceeded")
                if checks % _CANCEL_CADENCE == 0:
                    _cancelled(cancellation)
                row, column = cell
                dy = abs((row + 0.5) * state.cell_size_y - raster.y)
                if dy >= radius:
                    continue
                half = math.sqrt(max(0.0, radius * radius - dy * dy))
                center_x = (column + 0.5) * state.cell_size_x
                for allowed in raster.intervals:
                    interval = _intersections((center_x - half, center_x + half), allowed)
                    if interval is not None:
                        cell_intervals.append((interval[0], interval[1], cell))
            if not cell_intervals:
                continue
            reverse = component_raster_ordinal % 2 == 1
            component_raster_ordinal += 1
            # Contributor changes inside one continuous interval are evidence
            # changes, not new ENTRY authority. Union exact overlaps/touches,
            # while retaining every strictly positive gap as a separate span.
            groups: list[tuple[float, float, set[tuple[int, int]]]] = []
            for start, end, cell in sorted(
                cell_intervals,
                key=lambda value: (value[0], value[1], value[2]),
            ):
                if groups and start <= groups[-1][1]:
                    prior_start, prior_end, responsible = groups[-1]
                    responsible.add(cell)
                    groups[-1] = (prior_start, max(prior_end, end), responsible)
                else:
                    groups.append((start, end, {cell}))
            for group_index, (start, end, cells) in enumerate(groups):
                if end - start <= _EPSILON:
                    continue
                # Quadratic cutter tangency and the independently reconstructed
                # point predicate can differ by a handful of ULPs. Extend only
                # outward, at most eight representable values, and clamp inside
                # the exact permitted interval and strictly before any positive
                # neighboring gap. This is representation normalization, not a
                # geometric tolerance or cutter-radius shrink.
                midpoint = (start + end) / 2.0
                permitted = next(
                    value for value in raster.intervals
                    if value[0] <= midpoint <= value[1]
                )
                normalized_start, normalized_end = start, end
                for _ in range(8):
                    normalized_start = math.nextafter(normalized_start, -math.inf)
                    normalized_end = math.nextafter(normalized_end, math.inf)
                normalized_start = max(normalized_start, permitted[0])
                normalized_end = min(normalized_end, permitted[1])
                if group_index:
                    normalized_start = max(
                        normalized_start,
                        math.nextafter(groups[group_index - 1][1], math.inf),
                    )
                if group_index + 1 < len(groups):
                    normalized_end = min(
                        normalized_end,
                        math.nextafter(groups[group_index + 1][0], -math.inf),
                    )
                if normalized_end - normalized_start <= _EPSILON:
                    continue
                responsible = tuple(sorted(cells))
                spans.append(RestFinishingRasterSpan(
                    component_index, raster.index, raster.y,
                    normalized_start, normalized_end,
                    reverse, responsible,
                ))
                covered.update(responsible)
    if len(spans) > _MAX_SPANS:
        _fail(RestFinishingDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Finishing span limit exceeded")
    if set(work) != covered:
        _fail(RestFinishingDiagnosticCode.UNREACHABLE_FINISHING_MATERIAL,
              "Selected cutter/raster cannot cover every finishing cell")
    return RestFinishingLevelPlan(
        float(tip_z), state.fingerprint, state.content_integrity_fingerprint,
        work, components, tuple(spans),
    )


def plan_rest_finishing_geometry(inputs: RestFinishingGeometryInputs) -> RestFinishingGeometryResult:
    """Validate current authority and freeze a deterministic Phase-A raster."""
    authority, state, path, envelope = _validate_authority(inputs)
    _cancelled(inputs.cancellation)
    target_cells = _cells_inside(state, path.loop, inputs.cancellation)
    _cancelled(inputs.cancellation)
    if not target_cells:
        _fail(RestFinishingDiagnosticCode.GEOMETRY_INVALID, "Target profile contains no authoritative material cells")
    parameters = inputs.parameters
    below = tuple(cell for cell in target_cells
                  if state.top_heights[cell[0] * state.width + cell[1]]
                  < parameters.nominal_target_z.value - parameters.tolerance.value)
    if below:
        _fail(RestFinishingDiagnosticCode.MATERIAL_BELOW_TARGET,
              "Current material is below the nominal target tolerance")
    try:
        center_loop = canonical_contour_start(offset_contour(path.loop, ContourSide.INSIDE, envelope.radius))
    except ContourGenerationError as error:
        raise RestFinishingValidationError(
            RestFinishingDiagnosticCode.UNREACHABLE_FINISHING_MATERIAL,
            f"Cutter-center domain collapsed: {error}",
        ) from error
    rasters = _raster_positions(
        center_loop,
        parameters.stepover.value,
        inputs.cancellation,
    )
    _cancelled(inputs.cancellation)
    work = tuple(cell for cell in target_cells
                 if state.top_heights[cell[0] * state.width + cell[1]]
                 > parameters.cut_z + parameters.tolerance.value)
    if not work:
        return NoRestFinishingMaterial(authority, state, path, target_cells)
    hmax = max(state.top_heights[row * state.width + column] for row, column in work)
    required_depth = hmax - parameters.cut_z
    if (required_depth > inputs.tool.cutting_geometry.axial_cutting_length.value + _EPSILON
            or required_depth > inputs.assembly.stickout.value + _EPSILON):
        _fail(RestFinishingDiagnosticCode.TOOL_INELIGIBLE,
              "Cutter cutting length or assembly reach cannot reach the finishing target")
    components = _components(state, work)
    levels = _levels(hmax, parameters)
    plan = RestFinishingRasterPlan(
        authority, state, authority.material_candidate, path, center_loop,
        target_cells, components, levels, rasters,
    )
    # Prove terminal coverage now; Phase B must still rederive it from replay.
    derive_rest_finishing_level(plan, state, parameters.cut_z, inputs.cancellation)
    return plan


__all__ = [
    "NoRestFinishingMaterial",
    "RestFinishingGeometryAuthority",
    "RestFinishingGeometryInputs",
    "RestFinishingGeometryResult",
    "RestFinishingLevelPlan",
    "RestFinishingRasterPlan",
    "RestFinishingRasterPosition",
    "RestFinishingRasterSpan",
    "RestFinishingWorkComponent",
    "derive_rest_finishing_level",
    "plan_rest_finishing_geometry",
]
