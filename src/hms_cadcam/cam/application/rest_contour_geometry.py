"""Fail-closed residual geometry planning for Rest Contour Phase A.

This module deliberately produces geometric evidence only. It does not emit
motion, publish a MaterialState, or wire an application service. Its input is
the immutable R270 foundation result: a detached path or heightfield is never
an authority for the Phase A plan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, TypeAlias

from hms_cadcam.cam.application.contour import (
    ContourGenerationError,
    ContourPath,
    canonical_contour_start,
    offset_contour,
    resolve_profile_in_setup,
)
from hms_cadcam.cam.application.rest_contour import (
    RestContourFoundationResult,
    RestMaterialResolutionStatus,
)
from hms_cadcam.cam.application.rest_region import RestRegion, extract_cell_mask_regions
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    BoxStock,
    ContentFingerprint,
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourProfileDescriptor,
    ContourSide,
    GeometryInputRole,
    MachineDefinition,
    MachineEvidence,
    MachineRequirement,
    OperationFamily,
    OperationId,
    Point3,
    Revision,
    Setup,
    ToolAssembly,
    ToolAssemblyEvidence,
    ToolAssemblyReference,
    ToolAssemblyStatus,
    ToolDefinition,
    ToolFamily,
    assess_machine_compatibility,
    assess_tool_assembly,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.rest_contour import (
    RestContourDiagnosticCode,
    RestContourParameters,
    RestContourValidationError,
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
from hms_cadcam.cam.toolpath import ToolpathCompletionStatus, compute_material_removal_fingerprint


_TOLERANCE = 1.0e-8
_MAX_CELLS = 65_536
_MAX_LINES = 512
_MAX_DEPTHS = 128
_MAX_CHECKS = 2_000_000
_MAX_FRAGMENTS = 8_192
_CANCEL_CADENCE = 256


class RestContourResidualOutcome(StrEnum):
    """The two successful outcomes of the bounded Phase A planner."""

    PLANNED = "planned"
    NO_REST_MATERIAL = "no_rest_material"


def _point_at(start: Point3, end: Point3, parameter: float) -> Point3:
    return Point3(
        start.x + (end.x - start.x) * parameter,
        start.y + (end.y - start.y) * parameter,
        start.z + (end.z - start.z) * parameter,
        start.unit,
    )


def _same_point(first: Point3, second: Point3) -> bool:
    return (
        first.unit is second.unit
        and abs(first.x - second.x) <= _TOLERANCE
        and abs(first.y - second.y) <= _TOLERANCE
        and abs(first.z - second.z) <= _TOLERANCE
    )


@dataclass(frozen=True, slots=True)
class RestContourFragment:
    """One interval with its exact source segment, endpoints and cell evidence."""

    segment_index: int
    start: float
    end: float
    segment_start: Point3
    segment_end: Point3
    start_point: Point3
    end_point: Point3
    responsible_cells: tuple[tuple[int, int], ...]
    region_fingerprint: ContentFingerprint
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if type(self.segment_index) is not int or self.segment_index < 0:
            raise ValueError("Rest Contour fragment segment index is invalid")
        if any(type(value) not in (int, float) or not math.isfinite(float(value))
               for value in (self.start, self.end)):
            raise ValueError("Rest Contour fragment interval is invalid")
        if not 0.0 <= self.start < self.end <= 1.0:
            raise ValueError("Rest Contour fragment interval must be normalized and non-empty")
        if not all(isinstance(value, Point3) for value in (
            self.segment_start, self.segment_end, self.start_point, self.end_point,
        )):
            raise ValueError("Rest Contour fragment endpoints are invalid")
        if (not _same_point(_point_at(self.segment_start, self.segment_end, self.start), self.start_point)
                or not _same_point(_point_at(self.segment_start, self.segment_end, self.end), self.end_point)):
            raise ValueError("Rest Contour fragment endpoints do not match its source interval")
        if (tuple(sorted(self.responsible_cells)) != self.responsible_cells
                or not self.responsible_cells
                or len(set(self.responsible_cells)) != len(self.responsible_cells)
                or any(type(row) is not int or type(column) is not int
                       for row, column in self.responsible_cells)):
            raise ValueError("Rest Contour fragment cell evidence is invalid")
        if not isinstance(self.region_fingerprint, ContentFingerprint):
            raise ValueError("Rest Contour fragment region authority is invalid")
        object.__setattr__(self, "start", float(self.start))
        object.__setattr__(self, "end", float(self.end))
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "segment_index": self.segment_index,
            "start": self.start,
            "end": self.end,
            "segment_start": self.segment_start.to_dict(),
            "segment_end": self.segment_end.to_dict(),
            "start_point": self.start_point.to_dict(),
            "end_point": self.end_point.to_dict(),
            "responsible_cells": [list(value) for value in self.responsible_cells],
            "region": self.region_fingerprint.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RestContourRegionFragments:
    """One unambiguous residual component and the fragments supported by it."""

    region: RestRegion
    cells: tuple[tuple[int, int], ...]
    fragments: tuple[RestContourFragment, ...]
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.region, RestRegion) or self.region.fingerprint is None:
            raise ValueError("Rest Contour region is invalid")
        if self.region.holes:
            raise ValueError("Rest Contour Phase A does not support residual holes")
        if (tuple(sorted(self.cells)) != self.cells or not self.cells
                or len(set(self.cells)) != len(self.cells)):
            raise ValueError("Rest Contour region cells are invalid")
        if (tuple(sorted(self.fragments, key=lambda value: (
                value.segment_index, value.start, value.end, value.fingerprint.digest))) != self.fragments
                or not self.fragments):
            raise ValueError("Rest Contour region fragments are not deterministic")
        evidence: set[tuple[int, int]] = set()
        for fragment in self.fragments:
            if fragment.region_fingerprint != self.region.fingerprint:
                raise ValueError("Rest Contour fragment is associated with a different region")
            if not set(fragment.responsible_cells).issubset(self.cells):
                raise ValueError("Rest Contour fragment cells are outside its region")
            evidence.update(fragment.responsible_cells)
        if evidence != set(self.cells):
            raise ValueError("Rest Contour region has unassociated cell evidence")
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_CONTOUR_REGION_FRAGMENTS",
            "format_version": 1,
            "region": self.region.fingerprint.to_dict(),
            "cells": [list(value) for value in self.cells],
            "fragments": [value.fingerprint.to_dict() for value in self.fragments],
        }))


@dataclass(frozen=True, slots=True)
class RestContourDepthLayer:
    """All residual evidence at one tip Z, with explicit component ownership."""

    tip_z: float
    eligible_cells: tuple[tuple[int, int], ...]
    region_fragments: tuple[RestContourRegionFragments, ...]
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if type(self.tip_z) not in (int, float) or not math.isfinite(float(self.tip_z)):
            raise ValueError("Rest Contour layer tip depth is invalid")
        if (tuple(sorted(self.eligible_cells)) != self.eligible_cells or not self.eligible_cells
                or len(set(self.eligible_cells)) != len(self.eligible_cells)):
            raise ValueError("Rest Contour layer cells must be deterministic and non-empty")
        if (tuple(sorted(self.region_fragments, key=lambda value: value.fingerprint.digest))
                != self.region_fragments or not self.region_fragments):
            raise ValueError("Rest Contour layer regions are not deterministic")
        if set().union(*(set(value.cells) for value in self.region_fragments)) != set(self.eligible_cells):
            raise ValueError("Rest Contour layer regions do not account for all cells")
        object.__setattr__(self, "tip_z", float(self.tip_z))
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_CONTOUR_DEPTH_LAYER", "format_version": 2,
            "tip_z": self.tip_z,
            "eligible_cells": [list(value) for value in self.eligible_cells],
            "regions": [value.fingerprint.to_dict() for value in self.region_fragments],
        }))

    @property
    def regions(self) -> tuple[RestRegion, ...]:
        return tuple(value.region for value in self.region_fragments)

    @property
    def fragments(self) -> tuple[RestContourFragment, ...]:
        return tuple(fragment for value in self.region_fragments for fragment in value.fragments)


@dataclass(frozen=True, slots=True)
class RestContourResidualPlan:
    """Immutable Phase A handoff, self-contained for a future motion phase."""

    foundation_fingerprint: ContentFingerprint
    parent_state_fingerprint: ContentFingerprint
    profile_path_fingerprint: ContentFingerprint
    profile_geometry_fingerprint: ContentFingerprint
    center_loop: ContourLoop
    stock_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    cutter_envelope_fingerprint: ContentFingerprint
    authority: "RestContourGeometryAuthority"
    layers: tuple[RestContourDepthLayer, ...]
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.center_loop, ContourLoop) or not self.center_loop.closed
                or not self.layers):
            raise ValueError("Rest Contour plan center loop or layers are invalid")
        if any(first.tip_z <= second.tip_z + _TOLERANCE
               for first, second in zip(self.layers, self.layers[1:])):
            raise ValueError("Rest Contour plan depths must be strictly descending")
        for layer in self.layers:
            for fragment in layer.fragments:
                if fragment.segment_index >= len(self.center_loop.segments):
                    raise ValueError("Rest Contour fragment segment is outside the center loop")
                source = self.center_loop.segments[fragment.segment_index]
                if (not _same_point(fragment.segment_start, source.start)
                        or not _same_point(fragment.segment_end, source.end)):
                    raise ValueError("Rest Contour fragment source segment differs from plan center loop")
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_CONTOUR_RESIDUAL_PLAN", "format_version": 2,
            "foundation": self.foundation_fingerprint.to_dict(),
            "parent_state": self.parent_state_fingerprint.to_dict(),
            "profile_path": self.profile_path_fingerprint.to_dict(),
            "profile_geometry": self.profile_geometry_fingerprint.to_dict(),
            "center_loop": self.center_loop.to_dict(),
            "stock": self.stock_fingerprint.to_dict(), "setup": self.setup_fingerprint.to_dict(),
            "cutter_envelope": self.cutter_envelope_fingerprint.to_dict(),
            "authority": self.authority.fingerprint.to_dict(),
            "layers": [layer.fingerprint.to_dict() for layer in self.layers],
        }))


@dataclass(frozen=True, slots=True)
class NoRestContourMaterial:
    """Typed valid outcome after the foundation and current state re-validation."""

    authority: "RestContourGeometryAuthority"
    foundation_fingerprint: ContentFingerprint
    parent_state_fingerprint: ContentFingerprint
    profile_path_fingerprint: ContentFingerprint
    profile_geometry_fingerprint: ContentFingerprint
    fingerprint: ContentFingerprint = field(init=False)
    outcome: RestContourResidualOutcome = RestContourResidualOutcome.NO_REST_MATERIAL

    def __post_init__(self) -> None:
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_CONTOUR_NO_MATERIAL", "format_version": 2,
            "authority": self.authority.fingerprint.to_dict(),
            "foundation": self.foundation_fingerprint.to_dict(),
            "parent_state": self.parent_state_fingerprint.to_dict(),
            "profile_path": self.profile_path_fingerprint.to_dict(),
            "profile_geometry": self.profile_geometry_fingerprint.to_dict(),
        }))


@dataclass(frozen=True, slots=True)
class RestContourGeometryInputs:
    """Phase A inputs, bound to one exact R270 foundation outcome.

    ``profile_descriptor`` is retained solely for a deterministic re-resolution
    check against ``foundation.profile``. It cannot substitute a detached
    profile because the exact resulting ``ContourPath`` must compare equal.
    """

    foundation: RestContourFoundationResult
    profile_descriptor: ContourProfileDescriptor
    stock: BoxStock
    setup: Setup
    setup_fingerprint: ContentFingerprint
    parameters: RestContourParameters
    tool: ToolDefinition
    assembly: ToolAssembly
    assembly_evidence: ToolAssemblyEvidence
    machine: MachineDefinition
    machine_evidence: MachineEvidence
    cancellation: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class RestContourGeometryAuthority:
    """Exact immutable R270/current-aggregate authority retained for Phase B."""

    foundation_fingerprint: ContentFingerprint
    consumer_operation_id: OperationId
    consumer_operation_revision: Revision
    parameters: RestContourParameters
    profile_descriptor: ContourProfileDescriptor
    profile_path: ContourPath
    tool_assembly: ToolAssembly
    tool: ToolDefinition
    machine: MachineDefinition
    machine_requirement: MachineRequirement
    parent_state_fingerprint: ContentFingerprint
    parent_state_content_integrity_fingerprint: ContentFingerprint
    parent_state_verification_origin: MaterialStateVerificationOrigin
    fingerprint: ContentFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.consumer_operation_id, OperationId) or not isinstance(self.consumer_operation_revision, Revision):
            raise ValueError("Rest Contour consumer authority is invalid")
        if not isinstance(self.parameters, RestContourParameters) or not isinstance(self.profile_descriptor, ContourProfileDescriptor):
            raise ValueError("Rest Contour parameter or profile authority is invalid")
        if not isinstance(self.profile_path, ContourPath) or not isinstance(self.tool_assembly, ToolAssembly):
            raise ValueError("Rest Contour path or tool assembly authority is invalid")
        if not isinstance(self.tool, ToolDefinition) or not isinstance(self.machine, MachineDefinition):
            raise ValueError("Rest Contour tool or machine authority is invalid")
        if not isinstance(self.machine_requirement, MachineRequirement):
            raise ValueError("Rest Contour machine requirement authority is invalid")
        if self.parent_state_verification_origin not in {
            MaterialStateVerificationOrigin.TRUSTED_CALCULATED,
            MaterialStateVerificationOrigin.TRUSTED_PERSISTED,
        }:
            raise ValueError("Rest Contour parent-state integrity authority is invalid")
        object.__setattr__(self, "fingerprint", ContentFingerprint.from_payload({
            "format": "HMS_CAM_REST_CONTOUR_PHASE_A_AUTHORITY", "format_version": 1,
            "foundation": self.foundation_fingerprint.to_dict(),
            "consumer_operation_id": str(self.consumer_operation_id),
            "consumer_operation_revision": self.consumer_operation_revision.to_dict(),
            "parameters": self.parameters.to_dict(),
            "profile_descriptor": {
                "reference": self.profile_descriptor.reference.to_dict(),
                "outer_loop": self.profile_descriptor.outer_loop.to_dict(),
                "inner_loops": [value.to_dict() for value in self.profile_descriptor.inner_loops],
                "geometry": self.profile_descriptor.geometry_fingerprint.to_dict(),
                "unit": self.profile_descriptor.unit.value,
                "provenance": {
                    "source_kind": self.profile_descriptor.provenance.source_kind.value,
                    "occurrence_path": self.profile_descriptor.provenance.occurrence_transform.occurrence_path,
                    "transform": self.profile_descriptor.provenance.occurrence_transform.absolute_transform,
                },
            },
            "profile_path": {"loop": self.profile_path.loop.to_dict(), "source": self.profile_path.source_fingerprint.to_dict()},
            "tool_assembly": self.tool_assembly.to_dict(), "tool": self.tool.to_dict(),
            "machine": self.machine.to_dict(), "machine_requirement": {
                "id": str(self.machine_requirement.machine_id),
                "revision": self.machine_requirement.expected_revision.to_dict(),
                "fingerprint": self.machine_requirement.expected_fingerprint.to_dict(),
                "unit": self.machine_requirement.unit.value,
                "capabilities": [value.value for value in self.machine_requirement.required_capabilities],
            },
            "parent_state": self.parent_state_fingerprint.to_dict(),
            "parent_state_content": self.parent_state_content_integrity_fingerprint.to_dict(),
            # Verification origin is an audit/eligibility property.  A state
            # promoted from calculator bytes to persistence bytes must retain
            # the same semantic Rest Contour authority.
        }))


RestContourGeometryResult: TypeAlias = RestContourResidualPlan | NoRestContourMaterial


def _fail(code: RestContourDiagnosticCode, message: str) -> None:
    raise RestContourValidationError(code, message)


def _cancelled(cancellation: Callable[[], bool] | None) -> None:
    if cancellation is not None and cancellation():
        _fail(RestContourDiagnosticCode.CANCELLED, "Rest Contour residual planning was cancelled")


def _finite_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _validate_current_state(state: MaterialState, inputs: RestContourGeometryInputs) -> None:
    stock = inputs.setup.stock
    assert isinstance(stock, BoxStock)
    expected_stock = ContentFingerprint.from_payload(stock.to_dict())
    expected_setup = material_state_setup_fingerprint(inputs.setup)
    expected_initial_volume = stock.size_x.value * stock.size_y.value * stock.size_z.value
    expected_remaining_volume = sum(state.top_heights) * state.cell_size_x * state.cell_size_y
    volume_tolerance = max(_TOLERANCE, state.precision.tolerance,
                           abs(expected_initial_volume) * 1.0e-10)
    if (
        state.status is not MaterialStateStatus.COMPLETE
        or state.engine_version != MATERIAL_STATE_ENGINE_VERSION
        or state.precision != MaterialStatePrecisionPolicy()
        or state.unit is not inputs.parameters.unit
        or state.stock_fingerprint != expected_stock
        or state.setup_fingerprint != expected_setup
        or inputs.setup_fingerprint != expected_setup
        or not isinstance(state.content_integrity_fingerprint, ContentFingerprint)
        or state.content_integrity_fingerprint != state.computed_content_integrity_fingerprint()
        or state.verification_origin not in {
            MaterialStateVerificationOrigin.TRUSTED_CALCULATED,
            MaterialStateVerificationOrigin.TRUSTED_PERSISTED,
        }
        or not state.content_is_verified
        or not 1 <= state.width * state.height <= _MAX_CELLS
        or not _finite_number(state.cell_size_x)
        or not _finite_number(state.cell_size_y)
        or state.cell_size_x <= 0.0
        or state.cell_size_y <= 0.0
        or not math.isclose(state.width * state.cell_size_x, stock.size_x.value, rel_tol=0.0, abs_tol=_TOLERANCE)
        or not math.isclose(state.height * state.cell_size_y, stock.size_y.value, rel_tol=0.0, abs_tol=_TOLERANCE)
        or any(height > stock.size_z.value + _TOLERANCE for height in state.top_heights)
        or not math.isclose(state.initial_volume, expected_initial_volume, rel_tol=0.0, abs_tol=volume_tolerance)
        or not math.isclose(state.remaining_volume, expected_remaining_volume, rel_tol=0.0, abs_tol=volume_tolerance)
    ):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
              "Rest Contour foundation state does not match current engine, grid, stock or setup")


def _validate_aggregate_authority(
    inputs: RestContourGeometryInputs,
    path: ContourPath,
    state: MaterialState,
    foundation_fingerprint: ContentFingerprint,
) -> RestContourGeometryAuthority:
    """Re-bind the R270 result to the *current* persisted Setup aggregate."""
    resolution = inputs.foundation.material
    assert resolution.candidate is not None
    candidate = resolution.candidate
    dependency = candidate.dependency
    tree = inputs.setup.operation_tree
    graph = tree.dependency_graph
    operations = {operation.operation_id: operation for operation in tree.operations}
    producer = operations.get(candidate.producer_operation_id)
    consumer = operations.get(dependency.consumer_operation_id)
    if (
        producer is None or consumer is None
        or not producer.enabled or not consumer.enabled
        or producer.setup_id != inputs.setup.setup_id or consumer.setup_id != inputs.setup.setup_id
        or consumer.family is not OperationFamily.MILLING
        or consumer.strategy_key != "rest_contour_3axis"
        or candidate.edge not in graph.edges
        or candidate.edge.source_operation_id != candidate.producer_operation_id
        or candidate.edge.target_operation_id != consumer.operation_id
        or dependency.producer_operation_id != producer.operation_id
        or dependency.consumer_operation_id != consumer.operation_id
        or (resolution.status is RestMaterialResolutionStatus.RESOLVED
            and (inputs.foundation.dependency_edge != candidate.edge
                 or inputs.foundation.material_dependency != dependency))
        or (resolution.status is RestMaterialResolutionStatus.NO_REST_MATERIAL
            and (inputs.foundation.dependency_edge is not None
                 or inputs.foundation.material_dependency is not None))
    ):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
              "Rest Contour foundation dependency is absent from the current setup aggregate")
    artifact = candidate.producer_artifact
    producer_state = producer.artifact_state
    producer_machine = producer.machine_requirement
    machine_matches = (
        artifact.machine_id is None and artifact.machine_fingerprint is None
        if producer_machine is None
        else (
            artifact.machine_id == producer_machine.machine_id
            and artifact.machine_fingerprint == producer_machine.expected_fingerprint
            and artifact.unit is producer_machine.unit
        )
    )
    if (
        producer_state.status is not ArtifactStatus.VALID
        or producer_state.dirty_reasons
        or producer_state.artifact_fingerprint != artifact.artifact_fingerprint
        or producer_state.input_fingerprint != artifact.input_fingerprint
        or producer_state.generation != artifact.computation_token.generation
        or artifact.source_operation_id != producer.operation_id
        or artifact.operation_revision != producer.revision
        or artifact.setup_id != inputs.setup.setup_id
        or artifact.unit is not inputs.parameters.unit
        or artifact.wcs_fingerprint != ContentFingerprint.from_payload(inputs.setup.wcs.to_dict())
        or artifact.tool_assembly_id != producer.tool_assembly.assembly_id
        or artifact.tool_assembly_fingerprint.digest != producer.tool_assembly.expected_fingerprint.digest
        or artifact.tool_assembly_fingerprint.algorithm != producer.tool_assembly.expected_fingerprint.algorithm
        or artifact.tool_assembly_fingerprint.algorithm_version != producer.tool_assembly.expected_fingerprint.algorithm_version
        or artifact.completion_status is not ToolpathCompletionStatus.COMPLETE
        or not machine_matches
        or compute_material_removal_fingerprint(artifact) != state.toolpath_fingerprint
    ):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
              "Rest Contour producer artifact is not current in the setup aggregate")
    if RestContourParameters.from_operation_parameters(consumer.parameters) != inputs.parameters:
        _fail(RestContourDiagnosticCode.INVALID_PARAMETERS,
              "Rest Contour parameters differ from the persisted consumer operation")
    profiles = tuple(value for value in consumer.geometry_inputs if value.role is GeometryInputRole.PROFILE)
    if len(profiles) != 1 or profiles[0].reference != inputs.profile_descriptor.reference or not profiles[0].required:
        _fail(RestContourDiagnosticCode.PROFILE_INVALID,
              "Rest Contour profile differs from persisted consumer authority")
    if (
        consumer.tool_assembly != ToolAssemblyReference.from_assembly(inputs.assembly)
        or assess_tool_assembly(inputs.assembly, inputs.assembly_evidence) is not ToolAssemblyStatus.VALID
        or inputs.assembly.tool_id != inputs.tool.tool_id
        or inputs.assembly.expected_tool_revision != inputs.tool.revision
        or inputs.assembly.expected_tool_fingerprint != inputs.tool.content_fingerprint
        or inputs.assembly.expected_tool_unit is not inputs.tool.unit
        or not inputs.assembly_evidence.tool_exists
        or inputs.assembly_evidence.tool_revision != inputs.tool.revision
        or inputs.assembly_evidence.tool_fingerprint != inputs.tool.content_fingerprint
        or inputs.assembly_evidence.tool_unit is not inputs.tool.unit
    ):
        _fail(RestContourDiagnosticCode.TOOL_INELIGIBLE,
              "Rest Contour tool differs from persisted consumer assembly authority")
    requirement = consumer.machine_requirement
    if (
        requirement is None
        or inputs.machine.machine_id != requirement.machine_id
        or inputs.machine.revision != requirement.expected_revision
        or inputs.machine.content_fingerprint != requirement.expected_fingerprint
        or inputs.machine.unit is not requirement.unit
        or not inputs.machine_evidence.exists
        or inputs.machine_evidence.revision != inputs.machine.revision
        or inputs.machine_evidence.fingerprint != inputs.machine.content_fingerprint
        or inputs.machine_evidence.unit is not inputs.machine.unit
        or assess_machine_compatibility(requirement, inputs.machine_evidence).value != "compatible"
    ):
        _fail(RestContourDiagnosticCode.MACHINE_INCOMPATIBLE,
              "Rest Contour machine differs from persisted consumer authority")
    return RestContourGeometryAuthority(
        foundation_fingerprint, consumer.operation_id, consumer.revision, inputs.parameters,
        inputs.profile_descriptor, path, inputs.assembly, inputs.tool, inputs.machine,
        requirement, state.fingerprint, state.content_integrity_fingerprint,
        state.verification_origin,
    )


def _validate_inputs(inputs: RestContourGeometryInputs) -> tuple[CutterEnvelope, ContourPath, MaterialState, ContentFingerprint, RestContourGeometryAuthority]:
    if not isinstance(inputs, RestContourGeometryInputs):
        raise TypeError("Rest Contour geometry inputs are invalid")
    if not isinstance(inputs.foundation, RestContourFoundationResult):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour foundation result is invalid")
    if not isinstance(inputs.profile_descriptor, ContourProfileDescriptor):
        _fail(RestContourDiagnosticCode.PROFILE_INVALID, "Rest Contour profile descriptor is invalid")
    if not isinstance(inputs.stock, BoxStock) or not isinstance(inputs.setup, Setup):
        _fail(RestContourDiagnosticCode.RESIDUAL_UNSUPPORTED, "Rest Contour requires a Box Stock Setup")
    # There is exactly one stock authority: the setup.  Validate this before
    # any foundation/current-state branch, including the legitimate NO_REST
    # outcome, so a detached BoxStock cannot manufacture a success.
    if (not isinstance(inputs.setup.stock, BoxStock)
            or inputs.stock != inputs.setup.stock
            or inputs.setup.stock.frame != inputs.setup.wcs):
        _fail(RestContourDiagnosticCode.RESIDUAL_UNSUPPORTED,
              "Rest Contour requires the exact Setup Box Stock in the Setup WCS")
    if (not isinstance(inputs.parameters, RestContourParameters) or not isinstance(inputs.tool, ToolDefinition)
            or not isinstance(inputs.assembly, ToolAssembly) or not isinstance(inputs.assembly_evidence, ToolAssemblyEvidence)
            or not isinstance(inputs.machine, MachineDefinition) or not isinstance(inputs.machine_evidence, MachineEvidence)):
        _fail(RestContourDiagnosticCode.INVALID_PARAMETERS, "Rest Contour parameters or tool are invalid")
    if inputs.cancellation is not None and not callable(inputs.cancellation):
        _fail(RestContourDiagnosticCode.RESIDUAL_INVALID, "Rest Contour cancellation callback is invalid")
    _cancelled(inputs.cancellation)

    foundation = inputs.foundation
    resolution = foundation.material
    if resolution.status not in {
        RestMaterialResolutionStatus.RESOLVED,
        RestMaterialResolutionStatus.NO_REST_MATERIAL,
    } or resolution.candidate is None:
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour Phase A needs a resolved R270 material candidate")
    if foundation.profile is None:
        _fail(RestContourDiagnosticCode.PROFILE_INVALID, "Rest Contour foundation has no authoritative profile path")
    if (foundation.fingerprint is None) != (resolution.status is RestMaterialResolutionStatus.NO_REST_MATERIAL):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour foundation outcome fingerprint is inconsistent")
    state = resolution.candidate.state
    _validate_current_state(state, inputs)
    dependency = resolution.candidate.dependency
    if (
        dependency.parent_state_fingerprint != state.fingerprint
        or dependency.stock_fingerprint != state.stock_fingerprint
        or dependency.setup_fingerprint != state.setup_fingerprint
        or dependency.engine_version != MATERIAL_STATE_ENGINE_VERSION
        or dependency.precision != MaterialStatePrecisionPolicy().to_dict()
    ):
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour foundation material provenance is stale")
    if resolution.status is RestMaterialResolutionStatus.RESOLVED and not state.has_rest_material:
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Resolved Rest Contour foundation has no rest material")
    if resolution.status is RestMaterialResolutionStatus.NO_REST_MATERIAL and state.has_rest_material:
        _fail(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "No-rest foundation still contains material")

    try:
        supplied_path = resolve_profile_in_setup(inputs.profile_descriptor, inputs.setup)
    except ContourGenerationError as error:
        raise RestContourValidationError(RestContourDiagnosticCode.PROFILE_INVALID, str(error)) from error
    if supplied_path != foundation.profile:
        _fail(RestContourDiagnosticCode.PROFILE_INVALID, "Supplied profile does not resolve to the exact R270 path")
    if inputs.profile_descriptor.geometry_fingerprint != inputs.profile_descriptor.reference.expected_geometry_fingerprint:
        _fail(RestContourDiagnosticCode.PROFILE_INVALID, "Rest Contour profile geometry authority is invalid")
    stock = inputs.setup.stock
    assert isinstance(stock, BoxStock)
    if (state.unit is not inputs.parameters.unit or stock.size_x.unit is not inputs.parameters.unit
            or stock.size_y.unit is not inputs.parameters.unit or inputs.tool.unit is not inputs.parameters.unit):
        _fail(RestContourDiagnosticCode.RESIDUAL_INVALID, "Rest Contour units differ")
    if inputs.parameters.side is not ContourSide.INSIDE:
        _fail(RestContourDiagnosticCode.RESIDUAL_UNSUPPORTED, "Phase A supports INSIDE Rest Contour only")
    target = inputs.parameters.final_depth.value + inputs.parameters.axial_stock_allowance.value
    if not (0.0 <= target < inputs.parameters.top_height.value <= stock.size_z.value):
        _fail(RestContourDiagnosticCode.RESIDUAL_INVALID, "Rest Contour stock-local depth authority is invalid")
    if inputs.tool.family not in {ToolFamily.END_MILL, ToolFamily.BALL_END_MILL, ToolFamily.BULL_NOSE_END_MILL}:
        _fail(RestContourDiagnosticCode.TOOL_INELIGIBLE, "Rest Contour cutter family is unsupported")
    axial_length = inputs.tool.cutting_geometry.axial_cutting_length
    if axial_length.unit is not inputs.parameters.unit or axial_length.value + _TOLERANCE < inputs.parameters.top_height.value - target:
        _fail(RestContourDiagnosticCode.TOOL_INELIGIBLE, "Rest Contour cutter lacks axial capacity")
    loop = foundation.profile.loop
    if (not loop.closed or loop.orientation is not ContourOrientation.COUNTERCLOCKWISE
            or len(loop.segments) > _MAX_LINES
            or any(segment.kind is not ContourCurveKind.LINE for segment in loop.segments)):
        _fail(RestContourDiagnosticCode.RESIDUAL_UNSUPPORTED, "Phase A requires one simple CCW LINE-only profile")
    if any(segment.start.unit is not inputs.parameters.unit or abs(segment.start.z) > _TOLERANCE
           or abs(segment.end.z) > _TOLERANCE for segment in loop.segments):
        _fail(RestContourDiagnosticCode.PATH_OUTSIDE_AUTHORITY, "Profile is not in stock-local XY authority")
    if any(
        point.x < -_TOLERANCE or point.x > stock.size_x.value + _TOLERANCE
        or point.y < -_TOLERANCE or point.y > stock.size_y.value + _TOLERANCE
        for segment in loop.segments for point in (segment.start, segment.end)
    ):
        _fail(RestContourDiagnosticCode.PATH_OUTSIDE_AUTHORITY,
              "Profile is outside stock authority before Rest Contour planning")
    if any(math.hypot(segment.end.x - segment.start.x, segment.end.y - segment.start.y) <= _TOLERANCE
           for segment in loop.segments):
        _fail(RestContourDiagnosticCode.RESIDUAL_INVALID, "Profile line is degenerate")
    try:
        envelope = CutterEnvelope.from_tool(inputs.tool)
    except CamValidationError as error:
        raise RestContourValidationError(RestContourDiagnosticCode.TOOL_INELIGIBLE, "Rest Contour cutter is unsupported") from error
    foundation_fingerprint = foundation.fingerprint or ContentFingerprint.from_payload({
        "format": "HMS_CAM_REST_CONTOUR_NO_REST_FOUNDATION", "format_version": 1,
        "path": foundation.profile.source_fingerprint.to_dict(),
        "state": state.fingerprint.to_dict(),
        "dependency": dependency.to_dict(),
    })
    authority = _validate_aggregate_authority(inputs, foundation.profile, state, foundation_fingerprint)
    return envelope, foundation.profile, state, foundation_fingerprint, authority


def _depths(parameters: RestContourParameters) -> tuple[float, ...]:
    target = parameters.final_depth.value + parameters.axial_stock_allowance.value
    count = max(1, math.ceil((parameters.top_height.value - target) / parameters.stepdown.value))
    if count > _MAX_DEPTHS:
        _fail(RestContourDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Contour depth limit exceeded")
    levels: list[float] = []
    for index in range(1, count + 1):
        value = max(target, parameters.top_height.value - parameters.stepdown.value * index)
        if not levels or value < levels[-1] - _TOLERANCE:
            levels.append(value)
    if not levels or abs(levels[-1] - target) > _TOLERANCE:
        levels.append(target)
    else:
        levels[-1] = target
    return tuple(levels)


def _distance_to_segment(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= _TOLERANCE:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    value = min(1.0, max(0.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared))
    return math.hypot(point[0] - (start[0] + value * dx), point[1] - (start[1] + value * dy))


def _point_in_or_on_polygon(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
    if any(_distance_to_segment(point, first, second) <= _TOLERANCE
           for first, second in zip(polygon, (*polygon[1:], polygon[0]), strict=True)):
        return True
    inside = False
    x, y = point
    for first, second in zip(polygon, (*polygon[1:], polygon[0]), strict=True):
        if (first[1] > y) != (second[1] > y):
            crossing = (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1]) + first[0]
            if x < crossing:
                inside = not inside
    return inside


def _signed_polygon_clearance(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> float:
    distance = min(_distance_to_segment(point, first, second)
                   for first, second in zip(polygon, (*polygon[1:], polygon[0]), strict=True))
    return distance if _point_in_or_on_polygon(point, polygon) else -distance


def _signed_stock_clearance(point: tuple[float, float], stock: BoxStock) -> float:
    x, y = point
    inside = -_TOLERANCE <= x <= stock.size_x.value + _TOLERANCE and -_TOLERANCE <= y <= stock.size_y.value + _TOLERANCE
    distance = min(x, y, stock.size_x.value - x, stock.size_y.value - y)
    return distance if inside else -abs(distance)


def _validate_center_loop(center_loop: ContourLoop, polygon: tuple[tuple[float, float], ...], stock: BoxStock,
                          envelope: CutterEnvelope) -> None:
    """Require signed cutter containment; a zero-allowance tangent is legal."""
    for segment in center_loop.segments:
        samples = (
            (segment.start.x, segment.start.y),
            ((segment.start.x + segment.end.x) / 2.0, (segment.start.y + segment.end.y) / 2.0),
            (segment.end.x, segment.end.y),
        )
        for point in samples:
            if _signed_polygon_clearance(point, polygon) < envelope.radius - _TOLERANCE:
                _fail(RestContourDiagnosticCode.PATH_OUTSIDE_AUTHORITY,
                      "Nominal center loop places the cutter outside profile authority")
            if _signed_stock_clearance(point, stock) < envelope.radius - _TOLERANCE:
                _fail(RestContourDiagnosticCode.PATH_OUTSIDE_AUTHORITY,
                      "Nominal center loop places the cutter outside stock authority")


def _merged_intervals(intervals: list[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    if not intervals:
        return ()
    result: list[tuple[float, float]] = [min(intervals)]
    for start, end in sorted(intervals)[1:]:
        prior_start, prior_end = result[-1]
        if start <= prior_end + _TOLERANCE:
            result[-1] = (prior_start, max(prior_end, end))
        else:
            result.append((start, end))
    return tuple(result)


def _components(cells: set[tuple[int, int]]) -> tuple[tuple[tuple[int, int], ...], ...]:
    remaining = set(cells)
    values: list[tuple[tuple[int, int], ...]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        queue = [seed]
        while queue:
            row, column = queue.pop()
            for neighbor in ((row - 1, column), (row + 1, column), (row, column - 1), (row, column + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        values.append(tuple(sorted(component)))
    return tuple(sorted(values))


def _layer(*, state: MaterialState, center_loop: ContourLoop, tip_z: float, envelope: CutterEnvelope,
           cancellation: Callable[[], bool] | None, checks: list[int]) -> RestContourDepthLayer | None:
    eligible: set[tuple[int, int]] = set()
    evidence: dict[int, list[tuple[float, float, tuple[int, int]]]] = {}
    tangential_contact = False
    for segment_index, segment in enumerate(center_loop.segments):
        start_x, start_y, end_x, end_y = segment.start.x, segment.start.y, segment.end.x, segment.end.y
        dx, dy = end_x - start_x, end_y - start_y
        length = math.hypot(dx, dy)
        if length <= _TOLERANCE:
            _fail(RestContourDiagnosticCode.RESIDUAL_INVALID, "Center loop contains a degenerate line")
        min_column = max(0, math.floor((min(start_x, end_x) - envelope.radius) / state.cell_size_x))
        max_column = min(state.width - 1, math.floor((max(start_x, end_x) + envelope.radius) / state.cell_size_x))
        min_row = max(0, math.floor((min(start_y, end_y) - envelope.radius) / state.cell_size_y))
        max_row = min(state.height - 1, math.floor((max(start_y, end_y) + envelope.radius) / state.cell_size_y))
        for row in range(min_row, max_row + 1):
            center_y = (row + 0.5) * state.cell_size_y
            for column in range(min_column, max_column + 1):
                checks[0] += 1
                if checks[0] > _MAX_CHECKS:
                    _fail(RestContourDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Contour geometry-check limit exceeded")
                if checks[0] % _CANCEL_CADENCE == 0:
                    _cancelled(cancellation)
                maximum = envelope.maximum_removable_radius(
                    target_tip_z=tip_z,
                    current_height=state.top_heights[row * state.width + column],
                    threshold=state.precision.residual_threshold,
                )
                if maximum is None:
                    continue
                center_x = (column + 0.5) * state.cell_size_x
                projection = ((center_x - start_x) * dx + (center_y - start_y) * dy) / (length * length)
                perpendicular = abs((center_x - start_x) * dy - (center_y - start_y) * dx) / length
                if perpendicular > maximum + _TOLERANCE:
                    continue
                half = math.sqrt(max(0.0, maximum * maximum - perpendicular * perpendicular)) / length
                start, end = max(0.0, projection - half), min(1.0, projection + half)
                if end - start <= _TOLERANCE:
                    tangential_contact = True
                    continue
                cell = (row, column)
                eligible.add(cell)
                evidence.setdefault(segment_index, []).append((start, end, cell))
    if not eligible:
        if tangential_contact:
            _fail(RestContourDiagnosticCode.RESIDUAL_UNSUPPORTED,
                  "Residual contact is tangential or below Phase A tolerance")
        return None
    bundles: list[RestContourRegionFragments] = []
    for component in _components(eligible):
        try:
            regions = extract_cell_mask_regions(state, component)
        except CamValidationError as error:
            raise RestContourValidationError(RestContourDiagnosticCode.RESIDUAL_INVALID,
                                              "Residual component cannot be represented safely") from error
        if len(regions) != 1 or regions[0].holes:
            _fail(RestContourDiagnosticCode.RESIDUAL_UNSUPPORTED,
                  "Residual holes or ambiguous components are unsupported in Phase A")
        region = regions[0]
        assert region.fingerprint is not None
        fragments: list[RestContourFragment] = []
        component_set = set(component)
        for segment_index, values in evidence.items():
            selected = [(start, end, cell) for start, end, cell in values if cell in component_set]
            for start, end in _merged_intervals([(first, second) for first, second, _ in selected]):
                cells = tuple(sorted({cell for first, second, cell in selected
                                      if first < end - _TOLERANCE and second > start + _TOLERANCE}))
                if not cells:
                    _fail(RestContourDiagnosticCode.RESIDUAL_INVALID, "Residual fragment lost its cell evidence")
                source = center_loop.segments[segment_index]
                fragments.append(RestContourFragment(
                    segment_index, start, end, source.start, source.end,
                    _point_at(source.start, source.end, start), _point_at(source.start, source.end, end),
                    cells, region.fingerprint,
                ))
        ordered = tuple(sorted(fragments, key=lambda value: (
            value.segment_index, value.start, value.end, value.fingerprint.digest)))
        if len(ordered) > _MAX_FRAGMENTS:
            _fail(RestContourDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Contour fragment limit exceeded")
        bundles.append(RestContourRegionFragments(region, component, ordered))
    return RestContourDepthLayer(tip_z, tuple(sorted(eligible)),
        tuple(sorted(bundles, key=lambda value: value.fingerprint.digest)))


def plan_rest_contour_residual(inputs: RestContourGeometryInputs) -> RestContourGeometryResult:
    """Produce bounded Phase A evidence from exactly one resolved R270 foundation."""
    envelope, path, state, foundation_fingerprint, authority = _validate_inputs(inputs)
    _cancelled(inputs.cancellation)
    if inputs.foundation.material.status is RestMaterialResolutionStatus.NO_REST_MATERIAL:
        return NoRestContourMaterial(
            authority, foundation_fingerprint, state.fingerprint, path.source_fingerprint,
            inputs.profile_descriptor.geometry_fingerprint,
        )
    try:
        center_loop = canonical_contour_start(offset_contour(
            path.loop, ContourSide.INSIDE,
            envelope.radius + inputs.parameters.radial_stock_allowance.value,
        ))
    except ContourGenerationError as error:
        raise RestContourValidationError(RestContourDiagnosticCode.RESIDUAL_UNSUPPORTED, str(error)) from error
    if len(center_loop.segments) > _MAX_LINES:
        _fail(RestContourDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED, "Rest Contour line limit exceeded")
    polygon = tuple((segment.start.x, segment.start.y) for segment in path.loop.segments)
    _validate_center_loop(center_loop, polygon, inputs.setup.stock, envelope)
    checks = [0]
    layers = tuple(
        layer for depth in _depths(inputs.parameters)
        if (layer := _layer(state=state, center_loop=center_loop, tip_z=depth,
                            envelope=envelope, cancellation=inputs.cancellation, checks=checks)) is not None
    )
    if not layers:
        _fail(RestContourDiagnosticCode.RESIDUAL_INVALID, "Resolved material produced no accountable residual layer")
    return RestContourResidualPlan(
        foundation_fingerprint, state.fingerprint, path.source_fingerprint,
        inputs.profile_descriptor.geometry_fingerprint, center_loop,
        state.stock_fingerprint, inputs.setup_fingerprint, envelope.fingerprint,
        authority, layers,
    )


__all__ = [
    "NoRestContourMaterial", "RestContourDepthLayer", "RestContourFragment",
    "RestContourGeometryInputs", "RestContourGeometryResult", "RestContourRegionFragments",
    "RestContourResidualOutcome", "RestContourResidualPlan", "RestContourGeometryAuthority",
    "plan_rest_contour_residual",
]
