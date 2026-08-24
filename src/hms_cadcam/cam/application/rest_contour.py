"""Evidence-only Rest Contour foundation; it never calculates a toolpath.

Operation registration, project persistence and UI wiring deliberately remain
deferred. This resolver accepts already-persisted DAG/material evidence and
performs no graph or database mutation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Callable, Iterable, Mapping

from hms_cadcam.cam.application.contour import (
    ContourGenerationError,
    ContourPath,
    resolve_profile_in_setup,
)
from hms_cadcam.cam.automatic_contour import (
    ContourAutomaticLeadForm,
    ContourAutomaticLeadPlacement,
    contour_automatic_lead_points,
)
from hms_cadcam.cam.automatic_rest_contour import (
    REST_CONTOUR_AUTOMATIC_KEYS,
    REST_CONTOUR_AUTOMATIC_POLICY_KEY,
    REST_CONTOUR_AUTOMATIC_POLICY_VERSION,
    RestContourAutomaticContext,
    resolve_rest_contour_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    BoxStock,
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    GeometryInputRole,
    KinematicSide,
    MachineAxisType,
    MachineDefinition,
    MachineKind,
    MachineRequirement,
    Length,
    OperationCapability,
    OperationFamily,
    OperationId,
    Setup,
    SetupKind,
    ToolAssembly,
    ToolAssemblyEvidence,
    ToolAssemblyReference,
    ToolAssemblyStatus,
    ToolDefinition,
    ToolFamily,
    WCS_ORTHONORMAL_TOLERANCE,
    assess_machine_compatibility,
    assess_tool_assembly,
)
from hms_cadcam.cam.domain.contour import ContourProfileSource
from hms_cadcam.cam.domain.geometry_reference import GeometryReference, GeometryReferenceKind, GeometryResolutionStatus
from hms_cadcam.cam.domain.contour import ContourProfileDescriptor, ResolvedContourProfile
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.domain.rest_contour import (
    REST_CONTOUR_STRATEGY_KEY,
    RestContourDiagnosticCode,
    RestContourParameters,
    RestContourProfileSelection,
    RestContourValidationError,
)
from hms_cadcam.cam.material_state import (
    MATERIAL_STATE_ENGINE_VERSION,
    MaterialState,
    MaterialStatePrecisionPolicy,
    MaterialStateStatus,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.persistence.models import MaterialStateDependency
from hms_cadcam.cam.toolpath import ToolpathArtifact, ToolpathCompletionStatus, compute_material_removal_fingerprint


class RestMaterialResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    NO_REST_MATERIAL = "NO_REST_MATERIAL"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    STALE = "STALE"
    INCONSISTENT = "INCONSISTENT"


@dataclass(frozen=True, slots=True)
class RestMaterialStateCandidate:
    """One complete persisted state and its existing typed dependency evidence."""

    producer_operation_id: OperationId
    state: MaterialState
    dependency: MaterialStateDependency
    edge: DependencyEdge
    producer_artifact: ToolpathArtifact

    def __post_init__(self) -> None:
        if not isinstance(self.producer_operation_id, OperationId) or not isinstance(self.state, MaterialState):
            raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Material-state candidate is invalid")
        if not isinstance(self.dependency, MaterialStateDependency) or not isinstance(self.edge, DependencyEdge) or not isinstance(self.producer_artifact, ToolpathArtifact):
            raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Material-state dependency evidence is invalid")
        if (
            self.dependency.producer_operation_id != self.producer_operation_id
            or self.edge.kind is not DependencyKind.MATERIAL_STATE
            or self.edge.source_operation_id != self.producer_operation_id
            or self.edge.target_operation_id != self.dependency.consumer_operation_id
            # R260 records the consumed upstream state here.  It is not the
            # consumed state's own parent input (which belongs one generation
            # further upstream).
            or self.state.fingerprint != self.dependency.parent_state_fingerprint
            or self.state.toolpath_fingerprint != self.dependency.producer_toolpath_fingerprint
            or self.state.setup_fingerprint != self.dependency.setup_fingerprint
            or self.state.stock_fingerprint != self.dependency.stock_fingerprint
            or self.state.engine_version != self.dependency.engine_version
            or self.state.precision.to_dict() != self.dependency.precision
            or self.producer_artifact.source_operation_id != self.producer_operation_id
            or compute_material_removal_fingerprint(self.producer_artifact) != self.state.toolpath_fingerprint
        ):
            raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Material-state provenance is not exact")


@dataclass(frozen=True, slots=True)
class RestMaterialResolution:
    status: RestMaterialResolutionStatus
    candidate: RestMaterialStateCandidate | None = None
    message: str = ""

    def __post_init__(self) -> None:
        carries_candidate = self.status in {
            RestMaterialResolutionStatus.RESOLVED,
            RestMaterialResolutionStatus.NO_REST_MATERIAL,
        }
        if not isinstance(self.status, RestMaterialResolutionStatus) or carries_candidate != (self.candidate is not None):
            raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Material-state resolution is inconsistent")


def resolve_rest_material_state(
    candidates: Iterable[RestMaterialStateCandidate],
    *,
    setup_fingerprint: ContentFingerprint,
    setup: Setup,
    consumer_operation_id: OperationId,
) -> RestMaterialResolution:
    """Select one terminal material state from the persisted Setup aggregate.

    A caller-supplied dependency graph is deliberately not accepted here.  The
    operation tree is the aggregate authority for both operation identity and
    material-state edge ordering.
    """
    if not isinstance(setup, Setup) or not isinstance(setup_fingerprint, ContentFingerprint) or not isinstance(consumer_operation_id, OperationId):
        raise TypeError("Rest Contour material-state resolution inputs are invalid")
    if not setup.enabled:
        raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Disabled setup cannot resolve Rest Contour material state")
    values = tuple(candidates)
    if any(not isinstance(item, RestMaterialStateCandidate) for item in values):
        raise TypeError("Rest Contour material-state candidate is invalid")
    tree = setup.operation_tree
    operation_map = {operation.operation_id: operation for operation in tree.operations}
    consumer = operation_map.get(consumer_operation_id)
    if consumer is None or consumer.strategy_key != REST_CONTOUR_STRATEGY_KEY:
        raise RestContourValidationError(
            RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
            "Rest Contour consumer is absent from the persisted setup operation tree",
        )
    graph = tree.dependency_graph
    if not values:
        return RestMaterialResolution(RestMaterialResolutionStatus.MISSING, message="No material-state evidence")
    # The graph, rather than caller ordering or a prefiltered candidate list,
    # names the sole producer allowed to feed this consumer.  DependencyGraph
    # validates graph membership and cycles at construction time.
    terminal_edges = tuple(
        edge
        for edge in graph.edges
        if edge.kind is DependencyKind.MATERIAL_STATE
        and edge.target_operation_id == consumer_operation_id
    )
    directed_candidates = tuple(
        item for item in values
        if item.edge.target_operation_id == consumer_operation_id
    )
    if len(terminal_edges) != 1:
        if not terminal_edges and not directed_candidates:
            return RestMaterialResolution(RestMaterialResolutionStatus.MISSING, message="No material-state terminal edge")
        status = (
            RestMaterialResolutionStatus.INCONSISTENT
            if not terminal_edges
            else RestMaterialResolutionStatus.AMBIGUOUS
        )
        return RestMaterialResolution(status, message="Material-state terminal chain is not unique")
    terminal_edge = terminal_edges[0]
    terminal = tuple(item for item in values if item.edge == terminal_edge)
    if not terminal:
        return RestMaterialResolution(RestMaterialResolutionStatus.MISSING, message="No material state for terminal edge")
    if len(terminal) != 1:
        return RestMaterialResolution(RestMaterialResolutionStatus.AMBIGUOUS, message="Multiple material states for terminal edge")
    selected = terminal[0]
    if selected.producer_operation_id != terminal_edge.source_operation_id:
        return RestMaterialResolution(RestMaterialResolutionStatus.INCONSISTENT, message="Material-state terminal producer is invalid")
    producer = operation_map.get(selected.producer_operation_id)
    if (
        producer is None
        or selected.edge.source_operation_id != selected.producer_operation_id
        or not producer.enabled
        or producer.artifact_state.status is not ArtifactStatus.VALID
        or producer.artifact_state.dirty_reasons
        or producer.artifact_state.artifact_fingerprint != selected.producer_artifact.artifact_fingerprint
        or producer.artifact_state.input_fingerprint != selected.producer_artifact.input_fingerprint
        or producer.artifact_state.generation != selected.producer_artifact.computation_token.generation
    ):
        raise RestContourValidationError(
            RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
            "Material-state evidence is not bound to the selected persisted setup operation",
        )
    if selected.state.setup_fingerprint != setup_fingerprint:
        return RestMaterialResolution(RestMaterialResolutionStatus.MISSING, message="No same-setup material state")
    if selected.state.status is not MaterialStateStatus.COMPLETE:
        return RestMaterialResolution(RestMaterialResolutionStatus.STALE, message="Material state is not complete")
    if not selected.state.has_rest_material:
        return RestMaterialResolution(RestMaterialResolutionStatus.NO_REST_MATERIAL, selected, "No remaining rest material")
    return RestMaterialResolution(RestMaterialResolutionStatus.RESOLVED, selected)


@dataclass(frozen=True, slots=True)
class RestContourFoundationInputs:
    setup: Setup
    parameters: RestContourParameters
    profile: RestContourProfileSelection
    material_candidates: tuple[RestMaterialStateCandidate, ...]
    dependency_graph: DependencyGraph
    assembly: ToolAssembly
    assembly_evidence: ToolAssemblyEvidence
    tool: ToolDefinition
    machine: MachineDefinition
    machine_requirement: MachineRequirement
    consumer_operation_id: OperationId


@dataclass(frozen=True, slots=True)
class RestContourFoundationResult:
    material: RestMaterialResolution
    profile: ContourPath | None
    dependency_edge: DependencyEdge | None
    material_dependency: MaterialStateDependency | None
    automatic_contract: AutomaticParameterContract | None
    fingerprint: DependencyFingerprint | None


def _material_error(resolution: RestMaterialResolution) -> RestContourDiagnosticCode:
    return {
        RestMaterialResolutionStatus.MISSING: RestContourDiagnosticCode.MATERIAL_STATE_MISSING,
        RestMaterialResolutionStatus.STALE: RestContourDiagnosticCode.MATERIAL_STATE_STALE,
        RestMaterialResolutionStatus.AMBIGUOUS: RestContourDiagnosticCode.MATERIAL_STATE_AMBIGUOUS,
        RestMaterialResolutionStatus.INCONSISTENT: RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
    }[resolution.status]


def _validate_profile_authority(
    parameters: RestContourParameters,
    descriptor: ContourProfileDescriptor,
    setup: Setup,
) -> None:
    """Require the same current Setup/profile authority at create and prepare."""
    expected_kind = (
        GeometryReferenceKind.FACE
        if parameters.profile_source is ContourProfileSource.PLANAR_FACE_OUTER
        else GeometryReferenceKind.SKETCH_OR_PROFILE
    )
    if (
        descriptor.reference.source_id != setup.source_scope.primary_source_id
        or descriptor.reference.kind is not expected_kind
        or descriptor.provenance.source_kind is not parameters.profile_source
        or descriptor.reference.expected_geometry_fingerprint
        != descriptor.geometry_fingerprint
        or descriptor.inner_loops
    ):
        raise RestContourValidationError(
            RestContourDiagnosticCode.PROFILE_INVALID,
            "Rest Contour profile source scope, kind, identity or inner loops are invalid",
        )


def _profile(parameters: RestContourParameters, selection: RestContourProfileSelection, setup: Setup,
             resolver: Callable[[GeometryReference], ResolvedContourProfile]) -> tuple[ContourPath, ContourProfileDescriptor]:
    supplied = selection.descriptor
    resolved = resolver(supplied.reference)
    if (not isinstance(resolved, ResolvedContourProfile)
            or resolved.status is not GeometryResolutionStatus.RESOLVED
            or resolved.profile is None
            or resolved.profile.reference != supplied.reference
            or resolved.profile != supplied):
        raise RestContourValidationError(RestContourDiagnosticCode.PROFILE_INVALID, "Rest Contour persisted profile cannot be resolved exactly")
    descriptor = resolved.profile
    _validate_profile_authority(parameters, descriptor, setup)
    try:
        return resolve_profile_in_setup(descriptor, setup), descriptor
    except ContourGenerationError as error:
        raise RestContourValidationError(RestContourDiagnosticCode.PROFILE_INVALID, str(error)) from error


def validate_rest_contour_machine_authority(
    parameters: RestContourParameters,
    machine: MachineDefinition,
    requirement: MachineRequirement,
) -> None:
    """Validate the complete fixed three-axis MILL contract at app boundaries."""
    # MachineEvidence is intentionally constructed from the current immutable definition.
    from hms_cadcam.cam.domain import MachineEvidence
    current = MachineEvidence(True, machine.revision, machine.content_fingerprint, machine.unit, machine.capabilities.operations)
    if (
        requirement.machine_id != machine.machine_id
        or requirement.expected_revision != machine.revision
        or requirement.expected_fingerprint != machine.content_fingerprint
        or requirement.unit is not machine.unit
        or machine.unit is not parameters.unit
        # R268 is a fixed-WCS, 3-axis milling contract.  The current machine
        # model exposes axis type and kinematic side rather than a dedicated
        # ``fixed_3_axis`` flag, so accept only linear tool-side motion on a
        # MILL definition; rotary, workpiece-side and mill-turn semantics are
        # outside this tranche.
        or machine.kind is not MachineKind.MILL
        or not machine.capabilities.milling
        or OperationCapability.MILLING not in machine.capabilities.operations
        or OperationCapability.MILLING not in requirement.required_capabilities
        or len(machine.axes) != 3
        or any(axis.axis_type is not MachineAxisType.LINEAR for axis in machine.axes)
        or len({axis.name for axis in machine.axes}) != 3
        or tuple(node.axis_name for node in machine.kinematic_chain.nodes if node.axis_name is not None).count(machine.axes[0].name) != 1
        or tuple(node.axis_name for node in machine.kinematic_chain.nodes if node.axis_name is not None).count(machine.axes[1].name) != 1
        or tuple(node.axis_name for node in machine.kinematic_chain.nodes if node.axis_name is not None).count(machine.axes[2].name) != 1
        or set(node.axis_name for node in machine.kinematic_chain.nodes if node.axis_name is not None) != {axis.name for axis in machine.axes}
        or any(node.axis_name is not None and node.side is not KinematicSide.TOOL for node in machine.kinematic_chain.nodes)
        or abs(machine.axes[0].direction.dot(machine.axes[1].direction)) > WCS_ORTHONORMAL_TOLERANCE
        or abs(machine.axes[0].direction.dot(machine.axes[2].direction)) > WCS_ORTHONORMAL_TOLERANCE
        or abs(machine.axes[1].direction.dot(machine.axes[2].direction)) > WCS_ORTHONORMAL_TOLERANCE
        or abs(machine.axes[0].direction.cross(machine.axes[1].direction).dot(machine.axes[2].direction)) <= WCS_ORTHONORMAL_TOLERANCE
        or assess_machine_compatibility(requirement, current).value != "compatible"
        or parameters.cutting_feed_rate.value > machine.capabilities.maximum_feed.value
        or parameters.plunge_feed_rate.value > machine.capabilities.maximum_feed.value
        or not any(spindle.minimum_speed.value <= parameters.spindle_speed.value <= spindle.maximum_speed.value for spindle in machine.spindles)
    ):
        raise RestContourValidationError(RestContourDiagnosticCode.MACHINE_INCOMPATIBLE, "Rest Contour machine identity, capability, feed or spindle limit is invalid")


def _automatic(parameters: RestContourParameters, profile: ContourPath, selection: RestContourProfileSelection, tool: ToolDefinition, assembly: ToolAssembly) -> AutomaticParameterContract:
    if parameters.automatic_parameter_contract is None:
        raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour requires persisted automatic evidence")
    geometry = tool.cutting_geometry
    diameter = getattr(geometry, "diameter", None)
    corner_radius = getattr(geometry, "corner_radius", None)
    if diameter is None:
        raise RestContourValidationError(RestContourDiagnosticCode.TOOL_INELIGIBLE, "Rest Contour cutter geometry has no diameter")
    depth_span = parameters.top_height.value - (parameters.final_depth.value + parameters.axial_stock_allowance.value)
    context = RestContourAutomaticContext(
        parameters.unit, tool.family, diameter.value,
        None if corner_radius is None else corner_radius.value,
        geometry.axial_cutting_length.value, assembly.stickout.value, depth_span,
        parameters.tolerance.value, parameters.side, True, profile.loop,
        selection.descriptor.outer_loop, selection.descriptor.geometry_fingerprint.digest,
        tool.content_fingerprint.digest,
    )
    try:
        persisted = AutomaticParameterContract.from_json(parameters.automatic_parameter_contract)
        expected = resolve_rest_contour_automatic_contract(context, persisted.quality_profile)
    except (TypeError, ValueError) as error:
        raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour automatic evidence is malformed") from error
    if (
        persisted.policy_key != REST_CONTOUR_AUTOMATIC_POLICY_KEY
        or persisted.policy_version != REST_CONTOUR_AUTOMATIC_POLICY_VERSION
        or tuple(value.key for value in persisted.values) != tuple(sorted(REST_CONTOUR_AUTOMATIC_KEYS))
        or any(value.status is not AutomaticParameterStatus.RESOLVED or not value.validation.valid for value in persisted.values)
    ):
        raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour automatic evidence is stale or unresolved")
    for value in persisted.values:
        fresh = expected.value(value.key)
        if value.mode is AutomaticParameterMode.AUTO:
            if value != fresh:
                raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour automatic value or provenance differs from current Contour policy")
        elif value.mode in {AutomaticParameterMode.MANUAL, AutomaticParameterMode.MANUAL_OVERRIDE}:
            # Manual intent is allowed only over freshly-derived provenance.  Its
            # effective value is checked below against Rest parameters and exact
            # existing Contour geometry; it must not turn a stale policy result
            # into authority.
            if (
                value.source != fresh.source
                or value.policy_version != fresh.policy_version
                or value.dependency_fingerprint != fresh.dependency_fingerprint
                or value.inputs != fresh.inputs
                or value.resolved_value != fresh.resolved_value
                or value.lower_bound != fresh.lower_bound
                or value.upper_bound != fresh.upper_bound
                or value.clamped != fresh.clamped
                or value.validation != fresh.validation
            ):
                raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour manual override provenance is stale")
        else:
            raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour automatic mode is unresolved")
    expected_values = {
        "stepdown": parameters.stepdown.value,
        "lead_in_length": parameters.lead_in_length.value,
        "lead_out_length": parameters.lead_out_length.value,
    }
    for key, parameter_value in expected_values.items():
        item = persisted.value(key)
        value = item.effective_value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isclose(float(value), parameter_value, rel_tol=0.0, abs_tol=1.0e-12):
            raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour automatic effective value differs from parameters")
        fresh_upper = expected.value(key).upper_bound
        if (
            item.mode in {AutomaticParameterMode.MANUAL, AutomaticParameterMode.MANUAL_OVERRIDE}
            and isinstance(fresh_upper, (int, float))
            and not isinstance(fresh_upper, bool)
            and float(value) > float(fresh_upper) + 1.0e-12
        ):
            raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour manual lead or stepdown exceeds current Contour feasibility bound")
    entry = persisted.value("entry_segment_index").effective_value
    lead_form = persisted.value("lead_form").effective_value
    if (
        type(entry) is not int
        or not 0 <= entry < len(profile.loop.segments)
        or lead_form not in {"tangent_linear", "normal_linear"}
    ):
        raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour automatic entry evidence is invalid")
    try:
        contour_automatic_lead_points(
            profile.loop,
            selection.descriptor.outer_loop,
            parameters.side,
            ContourAutomaticLeadPlacement(
                entry,
                ContourAutomaticLeadForm(lead_form),
                float(persisted.value("lead_in_length").effective_value),
                float(persisted.value("lead_out_length").effective_value),
                0.0,
                0.0,
                False,
                False,
            ),
        )
    except (TypeError, ValueError) as error:
        raise RestContourValidationError(RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED, "Rest Contour lead placement is not geometrically feasible") from error
    return persisted


def resolve_rest_contour_application_parameters(
    parameters: RestContourParameters,
    profile: RestContourProfileSelection,
    tool: ToolDefinition,
    assembly: ToolAssembly,
    setup: Setup,
    profile_resolver: Callable[[GeometryReference], ResolvedContourProfile],
    *,
    quality_profile: CamQualityProfile = CamQualityProfile.BALANCED,
    manual_overrides: Mapping[str, object] | None = None,
) -> RestContourParameters:
    """Resolve and persist Rest Contour AUTO from authoritative live inputs.

    This is the application creation boundary: callers provide intent, while
    the effective stepdown/lead values and their provenance are always derived
    from the current profile, cutter and assembly.  Explicit manual overrides
    retain the shared Contour policy provenance and take precedence only for
    the named values.
    """
    if not isinstance(parameters, RestContourParameters):
        raise TypeError("Rest Contour parameters are invalid")
    if not isinstance(profile, RestContourProfileSelection):
        raise TypeError("Rest Contour profile is invalid")
    if not isinstance(tool, ToolDefinition) or not isinstance(assembly, ToolAssembly):
        raise TypeError("Rest Contour tooling is invalid")
    if not isinstance(setup, Setup):
        raise TypeError("Rest Contour setup is invalid")
    if not callable(profile_resolver):
        raise TypeError("Rest Contour profile resolver is invalid")
    current = profile_resolver(profile.descriptor.reference)
    if (
        not isinstance(current, ResolvedContourProfile)
        or current.status is not GeometryResolutionStatus.RESOLVED
        or current.profile != profile.descriptor
    ):
        raise RestContourValidationError(
            RestContourDiagnosticCode.PROFILE_INVALID,
            "Rest Contour creation profile is stale or unresolved",
        )
    _validate_profile_authority(parameters, profile.descriptor, setup)
    try:
        resolved_path = resolve_profile_in_setup(profile.descriptor, setup)
    except ContourGenerationError as error:
        raise RestContourValidationError(
            RestContourDiagnosticCode.PROFILE_INVALID,
            "Rest Contour profile cannot be resolved for automatic parameters",
        ) from error
    geometry = tool.cutting_geometry
    diameter = getattr(geometry, "diameter", None)
    corner_radius = getattr(geometry, "corner_radius", None)
    if diameter is None:
        raise RestContourValidationError(
            RestContourDiagnosticCode.TOOL_INELIGIBLE,
            "Rest Contour cutter geometry has no diameter",
        )
    depth_span = parameters.top_height.value - (
        parameters.final_depth.value + parameters.axial_stock_allowance.value
    )
    context = RestContourAutomaticContext(
        parameters.unit,
        tool.family,
        diameter.value,
        None if corner_radius is None else corner_radius.value,
        geometry.axial_cutting_length.value,
        assembly.stickout.value,
        depth_span,
        parameters.tolerance.value,
        parameters.side,
        True,
        resolved_path.loop,
        profile.descriptor.outer_loop,
        profile.descriptor.geometry_fingerprint.digest,
        tool.content_fingerprint.digest,
    )
    try:
        contract = resolve_rest_contour_automatic_contract(
            context,
            quality_profile=quality_profile,
            manual_overrides=manual_overrides,
        )
        stepdown = contract.value("stepdown").effective_value
        lead_in = contract.value("lead_in_length").effective_value
        lead_out = contract.value("lead_out_length").effective_value
    except (TypeError, ValueError) as error:
        raise RestContourValidationError(
            RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED,
            "Rest Contour automatic intent cannot be resolved",
        ) from error
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (stepdown, lead_in, lead_out)):
        raise RestContourValidationError(
            RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED,
            "Rest Contour automatic effective values are invalid",
        )
    return replace(
        parameters,
        stepdown=Length(float(stepdown), parameters.unit),
        lead_in_length=Length(float(lead_in), parameters.unit),
        lead_out_length=Length(float(lead_out), parameters.unit),
        automatic_parameter_contract=contract.to_json(),
    )


def _current_material_state_is_valid(
    candidate: RestMaterialStateCandidate,
    setup: Setup,
    unit,
) -> bool:
    """Bind persisted residue to the current stock and R260 core policy."""
    current_stock = ContentFingerprint.from_payload(setup.stock.to_dict())
    state = candidate.state
    return (
        state.stock_fingerprint == current_stock
        and candidate.dependency.stock_fingerprint == current_stock
        and state.unit is unit
        and state.engine_version == MATERIAL_STATE_ENGINE_VERSION
        and state.precision == MaterialStatePrecisionPolicy()
        and candidate.dependency.engine_version == MATERIAL_STATE_ENGINE_VERSION
        and candidate.dependency.precision == MaterialStatePrecisionPolicy().to_dict()
    )


def _producer_artifact_is_current(
    candidate: RestMaterialStateCandidate,
    producer,
    setup: Setup,
    unit,
) -> bool:
    """Bind residue provenance to the actual published upstream artifact."""
    artifact = candidate.producer_artifact
    state = producer.artifact_state
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
    return (
        producer.enabled
        and state.status is ArtifactStatus.VALID
        and not state.dirty_reasons
        and state.artifact_fingerprint == artifact.artifact_fingerprint
        and state.input_fingerprint == artifact.input_fingerprint
        and state.generation == artifact.computation_token.generation
        and artifact.source_operation_id == producer.operation_id
        and artifact.operation_revision == producer.revision
        and artifact.setup_id == setup.setup_id
        and artifact.unit is unit
        and artifact.wcs_fingerprint == ContentFingerprint.from_payload(setup.wcs.to_dict())
        and artifact.tool_assembly_id == producer.tool_assembly.assembly_id
        # Toolpath provenance uses the assembly's dependency-fingerprint
        # subtype while Operation stores the same canonical assembly bytes as
        # a content fingerprint.  Bind their algorithm/version/digest rather
        # than Python subclass identity.
        and artifact.tool_assembly_fingerprint.algorithm == producer.tool_assembly.expected_fingerprint.algorithm
        and artifact.tool_assembly_fingerprint.algorithm_version == producer.tool_assembly.expected_fingerprint.algorithm_version
        and artifact.tool_assembly_fingerprint.digest == producer.tool_assembly.expected_fingerprint.digest
        and artifact.unit is producer.tool_assembly.unit
        and machine_matches
        and artifact.completion_status is ToolpathCompletionStatus.COMPLETE
    )


class RestContourFoundation:
    """Validate the core-only R270 tranche; no toolpath is created here."""

    def __init__(self, profile_resolver: Callable[[GeometryReference], ResolvedContourProfile]) -> None:
        if not callable(profile_resolver):
            raise TypeError("Rest Contour profile resolver is invalid")
        self._profile_resolver = profile_resolver

    def resolve(self, inputs: RestContourFoundationInputs) -> RestContourFoundationResult:
        if not isinstance(inputs, RestContourFoundationInputs):
            raise TypeError("Rest Contour foundation inputs are invalid")
        if (
            not inputs.setup.enabled
            or inputs.parameters.unit is not inputs.setup.wcs.origin.unit
            or inputs.setup.kind is not SetupKind.MILL
            or not isinstance(inputs.setup.stock, BoxStock)
            or inputs.setup.stock.size_x.unit is not inputs.parameters.unit
            or inputs.setup.stock.size_y.unit is not inputs.parameters.unit
            or inputs.setup.stock.size_z.unit is not inputs.parameters.unit
            or inputs.setup.stock.frame.origin.unit is not inputs.parameters.unit
        ):
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Rest Contour unit differs from setup")
        unit = inputs.parameters.unit
        if (
            inputs.tool.unit is not unit
            or inputs.assembly.unit is not unit
            or inputs.assembly.expected_tool_unit is not unit
            or inputs.assembly_evidence.tool_unit is not unit
            or (
                inputs.assembly.holder_id is not None
                and (
                    inputs.assembly.expected_holder_unit is not unit
                    or inputs.assembly_evidence.holder_unit is not unit
                )
            )
        ):
            raise RestContourValidationError(RestContourDiagnosticCode.TOOL_INELIGIBLE, "Rest Contour tool authority uses a different unit")
        if any(candidate.state.unit is not unit for candidate in inputs.material_candidates):
            raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour material state uses a different unit")
        if inputs.machine.unit is not unit or inputs.machine_requirement.unit is not unit:
            raise RestContourValidationError(RestContourDiagnosticCode.MACHINE_INCOMPATIBLE, "Rest Contour machine authority uses a different unit")

        operation_map = {
            operation.operation_id: operation
            for operation in inputs.setup.operation_tree.operations
        }
        persisted_consumer = operation_map.get(inputs.consumer_operation_id)
        if persisted_consumer is None or persisted_consumer.strategy_key != REST_CONTOUR_STRATEGY_KEY:
            raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Rest Contour consumer is absent from the setup aggregate")
        if persisted_consumer.family is not OperationFamily.MILLING or not persisted_consumer.enabled:
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Persisted Rest Contour consumer lifecycle is not enabled milling")
        if RestContourParameters.from_operation_parameters(persisted_consumer.parameters) != inputs.parameters:
            raise RestContourValidationError(RestContourDiagnosticCode.INVALID_PARAMETERS, "Supplied Rest Contour parameters differ from persisted consumer authority")
        persisted_profiles = tuple(
            geometry_input
            for geometry_input in persisted_consumer.geometry_inputs
            if geometry_input.role is GeometryInputRole.PROFILE
        )
        expected_profile_kind = (
            GeometryReferenceKind.FACE
            if inputs.parameters.profile_source is ContourProfileSource.PLANAR_FACE_OUTER
            else GeometryReferenceKind.SKETCH_OR_PROFILE
        )
        if len(persisted_profiles) != 1:
            raise RestContourValidationError(RestContourDiagnosticCode.PROFILE_INVALID, "Rest Contour requires exactly one persisted profile input")
        persisted_profile = persisted_profiles[0]
        descriptor_reference = inputs.profile.descriptor.reference
        if (
            not persisted_profile.required
            or persisted_profile.expected_kind is not expected_profile_kind
            or persisted_profile.reference.kind is not expected_profile_kind
            or persisted_profile.reference.reference_id != descriptor_reference.reference_id
            or persisted_profile.reference.source_id != descriptor_reference.source_id
            or persisted_profile.reference.expected_geometry_fingerprint
            != descriptor_reference.expected_geometry_fingerprint
            or persisted_profile.reference != descriptor_reference
        ):
            raise RestContourValidationError(RestContourDiagnosticCode.PROFILE_INVALID, "Supplied Rest Contour profile differs from persisted consumer authority")
        if persisted_consumer.tool_assembly != ToolAssemblyReference.from_assembly(inputs.assembly):
            raise RestContourValidationError(RestContourDiagnosticCode.TOOL_INELIGIBLE, "Supplied tool assembly differs from persisted consumer authority")
        if persisted_consumer.machine_requirement != inputs.machine_requirement:
            raise RestContourValidationError(RestContourDiagnosticCode.MACHINE_INCOMPATIBLE, "Supplied machine requirement differs from persisted consumer authority")
        if (
            assess_tool_assembly(inputs.assembly, inputs.assembly_evidence) is not ToolAssemblyStatus.VALID
            or inputs.assembly.tool_id != inputs.tool.tool_id
            or inputs.tool.revision != inputs.assembly.expected_tool_revision
            or inputs.tool.content_fingerprint != inputs.assembly.expected_tool_fingerprint
            or inputs.tool.unit is not inputs.assembly.expected_tool_unit
            or not inputs.assembly_evidence.tool_exists
            or inputs.tool.revision != inputs.assembly_evidence.tool_revision
            or inputs.tool.content_fingerprint != inputs.assembly_evidence.tool_fingerprint
            or inputs.tool.unit is not inputs.assembly_evidence.tool_unit
        ):
            raise RestContourValidationError(RestContourDiagnosticCode.TOOL_INELIGIBLE, "Tool assembly is missing, stale or inconsistent")
        if inputs.tool.family not in {ToolFamily.END_MILL, ToolFamily.BALL_END_MILL, ToolFamily.BULL_NOSE_END_MILL}:
            raise RestContourValidationError(RestContourDiagnosticCode.TOOL_INELIGIBLE, "Rest Contour tool family is ineligible")
        # Establish machine/unit authority before comparing any untagged scalar
        # geometry or capacity values below.
        validate_rest_contour_machine_authority(
            inputs.parameters, inputs.machine, inputs.machine_requirement,
        )
        geometry = inputs.tool.cutting_geometry
        diameter = getattr(geometry, "diameter", None)
        depth_span = inputs.parameters.top_height.value - (inputs.parameters.final_depth.value + inputs.parameters.axial_stock_allowance.value)
        if (
            diameter is None or diameter.value <= 0.0 or geometry.axial_cutting_length.value <= 0.0
            or inputs.assembly.stickout.value <= 0.0 or depth_span <= 0.0
            or depth_span > min(geometry.axial_cutting_length.value, inputs.assembly.stickout.value)
            or inputs.parameters.stepdown.value > min(depth_span, geometry.axial_cutting_length.value, inputs.assembly.stickout.value)
        ):
            raise RestContourValidationError(RestContourDiagnosticCode.TOOL_INELIGIBLE, "Rest Contour cutting geometry or axial capacity is invalid")
        profile, authoritative_descriptor = _profile(inputs.parameters, inputs.profile, inputs.setup, self._profile_resolver)
        authoritative_selection = RestContourProfileSelection(authoritative_descriptor)
        automatic = _automatic(inputs.parameters, profile, authoritative_selection, inputs.tool, inputs.assembly)
        # Keep the public input shape temporarily compatible, but reject any
        # detached graph that does not exactly restate the aggregate graph.
        # Resolution below reads only ``setup.operation_tree.dependency_graph``.
        if inputs.dependency_graph != inputs.setup.operation_tree.dependency_graph:
            raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Detached dependency graph differs from persisted setup aggregate")
        material = resolve_rest_material_state(
            inputs.material_candidates,
            setup_fingerprint=material_state_setup_fingerprint(inputs.setup),
            setup=inputs.setup,
            consumer_operation_id=inputs.consumer_operation_id,
        )
        if material.status in {
            RestMaterialResolutionStatus.RESOLVED,
            RestMaterialResolutionStatus.NO_REST_MATERIAL,
        }:
            assert material.candidate is not None
            if not _current_material_state_is_valid(material.candidate, inputs.setup, inputs.parameters.unit):
                raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Material state does not match current stock, unit, engine or precision")
            producer = operation_map.get(material.candidate.producer_operation_id)
            if producer is None or not _producer_artifact_is_current(
                material.candidate, producer, inputs.setup, inputs.parameters.unit,
            ):
                raise RestContourValidationError(RestContourDiagnosticCode.MATERIAL_STATE_INVALID, "Published producer artifact is stale or mismatched")
        if material.status is RestMaterialResolutionStatus.NO_REST_MATERIAL:
            return RestContourFoundationResult(material, profile, None, None, automatic, None)
        if material.status is not RestMaterialResolutionStatus.RESOLVED:
            raise RestContourValidationError(_material_error(material), material.message or "Material state is unavailable")
        assert material.candidate is not None
        candidate = material.candidate
        fingerprint = DependencyFingerprint.from_payload({
            "strategy": REST_CONTOUR_STRATEGY_KEY,
            "algorithm_version": 1,
            "parameters": inputs.parameters.to_dict(),
            "automatic": automatic.effective_fingerprint.to_dict(),
            "profile_reference": authoritative_descriptor.reference.to_dict(),
            "profile_geometry": authoritative_descriptor.geometry_fingerprint.to_dict(),
            "profile_path": profile.source_fingerprint.to_dict(),
            "setup_wcs": inputs.setup.wcs.to_dict(),
            "stock": inputs.setup.stock.to_dict(),
            "material_state": candidate.state.to_dict(),
            "material_dependency": candidate.dependency.to_dict(),
            "dependency_edge": candidate.edge.to_dict(),
            "tool": inputs.tool.to_dict(), "assembly": inputs.assembly.to_dict(),
            "machine": inputs.machine.to_dict(), "machine_requirement": {
                "id": str(inputs.machine_requirement.machine_id),
                "revision": inputs.machine_requirement.expected_revision.to_dict(),
                "fingerprint": inputs.machine_requirement.expected_fingerprint.to_dict(),
            },
        })
        return RestContourFoundationResult(material, profile, candidate.edge, candidate.dependency, automatic, fingerprint)
