"""R270 Rest Contour core-only evidence contract.

Registry/service/UI/Post/toolpath generation are deliberately deferred.
"""

from __future__ import annotations

import ast
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from hms_cadcam.cam.application import basic_mill_resources
from hms_cadcam.cam.application.contour import resolve_profile_in_setup
from hms_cadcam.cam.application.rest_contour import (
    RestContourFoundation,
    RestContourFoundationInputs,
    RestMaterialResolutionStatus,
    RestMaterialStateCandidate,
    resolve_rest_material_state,
)
from hms_cadcam.cam.automatic_rest_contour import (
    REST_CONTOUR_AUTOMATIC_KEYS,
    REST_CONTOUR_AUTOMATIC_POLICY_KEY,
    RestContourAutomaticContext,
    resolve_rest_contour_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import AutomaticParameterContract
from hms_cadcam.cam.domain import (
    AffineTransform,
    ArtifactState,
    ArtifactStatus,
    BoxStock,
    CamNodeId,
    CamJob,
    CamJobId,
    ContentFingerprint,
    ContourBounds,
    ContourCurveKind,
    ContourCutDirection,
    ContourLoop,
    ContourOrientation,
    ContourProfileDescriptor,
    ContourProfileSource,
    ComputationToken,
    DependencyFingerprint,
    ContourSegment,
    ContourSide,
    DependencyEdge,
    DependencyGraph,
    CylinderStock,
    GeometryFingerprint,
    GeometryInputId,
    GeometryInputRole,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
    Length,
    LengthUnit,
    KinematicChain,
    KinematicMount,
    KinematicNode,
    KinematicSide,
    MachineAxis,
    MachineAxisType,
    MachineRequirement,
    OccurrenceTransformProvenance,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationId,
    OperationGeometryInput,
    OperationParameterSet,
    OperationTree,
    Point3,
    ProfileProvenance,
    Revision,
    Setup,
    SetupId,
    SetupKind,
    SourceScope,
    SpindleSpeed,
    ToolAssemblyEvidence,
    ToolAssemblyReference,
    ToolpathArtifactId,
    Vector3,
    WcsFrame,
    WorkOffset,
)
from hms_cadcam.cam.domain.rest_contour import (
    REST_CONTOUR_STRATEGY_KEY,
    RestContourDiagnosticCode,
    RestContourLinkingPolicy,
    RestContourParameters,
    RestContourProfileSelection,
    RestContourValidationError,
)
from hms_cadcam.cam.domain.errors import CamInvariantError, CamUnitError
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit
from hms_cadcam.cam.material_state import (
    calculate_material_state,
    MaterialState,
    MaterialStatePrecisionPolicy,
    MaterialStateStatus,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.toolpath import Pose, ToolpathBuilder, compute_material_removal_fingerprint
from hms_cadcam.cam.domain import GeometryResolutionStatus, ResolvedContourProfile
from hms_cadcam.cam.persistence.models import MaterialStateDependency
from hms_cadcam.cam.persistence import CamProjectSnapshot, CamSqliteRepository
from hms_cadcam.project.database import ProjectDatabase


def _setup() -> Setup:
    unit = LengthUnit.MM
    source = uuid4()
    wcs = WcsFrame.identity(unit)
    reference = GeometryReference(
        GeometryReferenceId.new(), HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, source,
        GeometryReferenceKind.DOCUMENT, GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"setup": str(source)}), Revision(0),
    )
    return Setup(SetupId.new(), "R270", SetupKind.MILL, wcs, WorkOffset("G54", 1),
        BoxStock(Length(100, unit), Length(100, unit), Length(50, unit), wcs),
        reference, SourceScope(source))


def _profile(setup: Setup, *, inner: bool = False, kind: GeometryReferenceKind = GeometryReferenceKind.FACE) -> RestContourProfileSelection:
    unit = LengthUnit.MM
    points = (Point3(0, 0, 0, unit), Point3(20, 0, 0, unit), Point3(20, 20, 0, unit), Point3(0, 20, 0, unit))
    loop = ContourLoop(tuple(ContourSegment(ContourCurveKind.LINE, points[index], points[(index + 1) % 4]) for index in range(4)), ContourOrientation.COUNTERCLOCKWISE)
    fingerprint = GeometryFingerprint.from_payload(loop.to_dict())
    reference = GeometryReference(GeometryReferenceId.new(), HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, setup.source_scope.primary_source_id, kind,
        GeometryRepresentationKind.BREP, fingerprint, Revision(0),
        subshape_selector="hms_profile_v1:" + "a" * 64 + ":face:" + "b" * 64)
    descriptor = ContourProfileDescriptor(reference, points[0], Vector3(1, 0, 0), Vector3(0, 1, 0), Vector3(0, 0, 1),
        loop, (loop,) if inner else (), ContourBounds(points[0], Point3(20, 20, 0, unit)), unit, fingerprint,
        ProfileProvenance(ContourProfileSource.PLANAR_FACE_OUTER, OccurrenceTransformProvenance(None, (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))))
    return RestContourProfileSelection(descriptor)


def _base_parameters(**changes: object) -> RestContourParameters:
    unit = LengthUnit.MM
    values: dict[str, object] = {
        "unit": unit, "profile_source": ContourProfileSource.PLANAR_FACE_OUTER, "side": ContourSide.ON,
        "top_height": Length(10, unit), "final_depth": Length(2, unit), "stepdown": Length(1, unit),
        "radial_stock_allowance": Length(0, unit), "axial_stock_allowance": Length(0, unit),
        "clearance_height": Length(15, unit), "retract_height": Length(12, unit),
        "cutting_feed_rate": FeedRate(300, FeedUnit.MM_PER_MINUTE), "plunge_feed_rate": FeedRate(80, FeedUnit.MM_PER_MINUTE),
        "spindle_speed": SpindleSpeed(1000), "direction": ContourCutDirection.CLIMB,
        "tolerance": Length(0.01, unit), "lead_in_length": Length(1, unit), "lead_out_length": Length(1, unit),
        "linking_policy": RestContourLinkingPolicy.RETRACT_CLEARANCE,
    }
    values.update(changes)
    return RestContourParameters(**values)  # type: ignore[arg-type]


def _automatic_parameters(setup: Setup, profile: RestContourProfileSelection, tool, assembly) -> RestContourParameters:
    preliminary = _base_parameters()
    path = resolve_profile_in_setup(profile.descriptor, setup)
    geometry = tool.cutting_geometry
    contract = resolve_rest_contour_automatic_contract(RestContourAutomaticContext(
        preliminary.unit, tool.family, geometry.diameter.value, getattr(geometry, "corner_radius", None) and geometry.corner_radius.value,
        geometry.axial_cutting_length.value, assembly.stickout.value, 8.0, preliminary.tolerance.value,
        preliminary.side, True, path.loop, profile.descriptor.outer_loop,
        profile.descriptor.geometry_fingerprint.digest, tool.content_fingerprint.digest,
    ))
    return _base_parameters(
        stepdown=Length(float(contract.value("stepdown").effective_value), LengthUnit.MM),
        lead_in_length=Length(float(contract.value("lead_in_length").effective_value), LengthUnit.MM),
        lead_out_length=Length(float(contract.value("lead_out_length").effective_value), LengthUnit.MM),
        automatic_parameter_contract=contract.to_json(),
    )


def _fixed_three_axis_machine(machine):
    """Upgrade the generic one-axis fixture to R268's fixed 3-axis contract."""
    unit = machine.unit
    axes = (
        MachineAxis("axis_x", "longitudinal_motion", MachineAxisType.LINEAR, Vector3(1, 0, 0), Length(-500, unit), Length(500, unit), Length(0, unit)),
        MachineAxis("axis_y", "transverse_motion", MachineAxisType.LINEAR, Vector3(0, 1, 0), Length(-500, unit), Length(500, unit), Length(0, unit)),
        MachineAxis("axis_z", "vertical_motion", MachineAxisType.LINEAR, Vector3(0, 0, 1), Length(-500, unit), Length(500, unit), Length(0, unit)),
    )
    chain = KinematicChain((
        KinematicNode("base", None, None, KinematicSide.FIXED, KinematicMount.NONE, AffineTransform.identity(unit)),
        KinematicNode("slide_x", "base", "axis_x", KinematicSide.TOOL, KinematicMount.TOOL, AffineTransform.identity(unit)),
        KinematicNode("slide_y", "slide_x", "axis_y", KinematicSide.TOOL, KinematicMount.TOOL, AffineTransform.identity(unit)),
        KinematicNode("slide_z", "slide_y", "axis_z", KinematicSide.TOOL, KinematicMount.TOOL, AffineTransform.identity(unit)),
    ))
    return replace(machine, axes=axes, kinematic_chain=chain)


def _candidate(setup: Setup, consumer: OperationId, tool, assembly, machine, *, rest: bool = True,
               status: MaterialStateStatus = MaterialStateStatus.COMPLETE, feed: float = 100.0,
               producer: OperationId | None = None) -> tuple[RestMaterialStateCandidate, DependencyGraph]:
    producer = producer or OperationId.new()
    setup_fp = material_state_setup_fingerprint(setup)
    input_fingerprint = DependencyFingerprint.from_payload({"producer": str(producer), "purpose": "r270"})
    artifact = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(), operation_id=producer, operation_revision=Revision(0),
        computation_token=ComputationToken(uuid4(), 1), input_fingerprint=input_fingerprint,
        unit=setup.wcs.origin.unit, setup_id=setup.setup_id, setup_revision=setup.revision,
        wcs_fingerprint=ContentFingerprint.from_payload(setup.wcs.to_dict()),
        tool_assembly_id=assembly.assembly_id, tool_assembly_fingerprint=assembly.content_fingerprint,
        machine_id=machine.machine_id, machine_fingerprint=machine.content_fingerprint,
    )
    artifact.set_initial_pose(Pose(Point3(2, 2, 12, setup.wcs.origin.unit), Vector3(0, 0, 1)))
    if rest:
        artifact.linear_to(Pose(Point3(18, 2, 2, setup.wcs.origin.unit), Vector3(0, 0, 1)), FeedRate(feed, FeedUnit.MM_PER_MINUTE))
    else:
        # A real raster artifact drives calculated stock to zero; it is not a
        # fabricated MaterialState used merely to exercise NO_REST_MATERIAL.
        for index, y in enumerate(range(0, 101, 5)):
            first, second = ((0, 100) if index % 2 == 0 else (100, 0))
            artifact.rapid_to(Pose(Point3(first, y, 0, setup.wcs.origin.unit), Vector3(0, 0, 1)))
            artifact.linear_to(Pose(Point3(second, y, 0, setup.wcs.origin.unit), Vector3(0, 0, 1)), FeedRate(feed, FeedUnit.MM_PER_MINUTE))
    artifact = artifact.finalize()
    state = calculate_material_state(stock=setup.stock, artifact=artifact, tool=tool, setup_fingerprint=setup_fp).state
    if status is not MaterialStateStatus.COMPLETE:
        state = replace(state, status=status)
    dependency = MaterialStateDependency(consumer, producer, state.fingerprint, compute_material_removal_fingerprint(artifact), setup_fp, state.stock_fingerprint,
        state.engine_version, state.precision.to_dict())
    edge = DependencyEdge.material_state(producer, consumer)
    return RestMaterialStateCandidate(producer, state, dependency, edge, artifact), DependencyGraph((producer, consumer), (edge,))


def _setup_with_operation_aggregate(
    setup: Setup,
    assembly,
    consumer: OperationId,
    parameters: RestContourParameters,
    profile: RestContourProfileSelection,
    graph: DependencyGraph,
    machine_requirement: MachineRequirement | None = None,
    candidates: tuple[RestMaterialStateCandidate, ...] = (),
    producer_assembly=None,
    producer_machine_requirement: MachineRequirement | None = None,
) -> Setup:
    """Bind test material evidence to real persisted Setup operations."""
    producer_assembly = producer_assembly or assembly
    producer_machine_requirement = producer_machine_requirement or machine_requirement
    tree = OperationTree.empty(setup.setup_id)
    for operation_id in graph.operation_ids:
        is_consumer = operation_id == consumer
        operation = Operation(
            operation_id,
            CamNodeId.new(),
            OperationFamily.MILLING,
            setup.setup_id,
            ToolAssemblyReference.from_assembly(assembly if is_consumer else producer_assembly),
            (
                (
                    OperationGeometryInput(
                        GeometryInputId.new(),
                        GeometryInputRole.PROFILE,
                        profile.descriptor.reference,
                        True,
                        profile.descriptor.reference.kind,
                        0,
                    ),
                )
                if is_consumer else ()
            ),
            (
                parameters.to_operation_parameters()
                if is_consumer
                else OperationParameterSet("upstream.material", 1, (), 1)
            ),
            machine_requirement if is_consumer else producer_machine_requirement,
            artifact_state=(
                ArtifactState()
                if is_consumer
                else ArtifactState(
                    ArtifactStatus.VALID,
                    1,
                    input_fingerprint=next((candidate.producer_artifact.input_fingerprint for candidate in candidates if candidate.producer_operation_id == operation_id), DependencyFingerprint.from_payload({"upstream": str(operation_id)})),
                    artifact_fingerprint=next((candidate.producer_artifact.artifact_fingerprint for candidate in candidates if candidate.producer_operation_id == operation_id), ContentFingerprint.from_payload({"upstream": str(operation_id)})),
                    dirty_reasons=(),
                )
            ),
        )
        tree = tree.add_operation(
            tree.root_id,
            "Rest Contour" if is_consumer else "Producer",
            operation,
        )
    for edge in graph.edges:
        tree = tree.with_dependency_added(edge)
    return replace(setup, operation_tree=tree)


def _replace_aggregate_operation(setup: Setup, operation_id: OperationId, **changes: object) -> Setup:
    current = setup.operation_tree.get_operation(operation_id)
    tree = setup.operation_tree.replace_operation(replace(current, **changes))
    return replace(setup, operation_tree=tree)


def _with_persisted_parameters(
    inputs: RestContourFoundationInputs,
    parameters: RestContourParameters,
) -> RestContourFoundationInputs:
    setup = _replace_aggregate_operation(
        inputs.setup,
        inputs.consumer_operation_id,
        parameters=parameters.to_operation_parameters(),
    )
    return replace(inputs, setup=setup, parameters=parameters)


def _inputs(**changes: object) -> RestContourFoundationInputs:
    setup = _setup()
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    machine = _fixed_three_axis_machine(machine)
    profile = _profile(setup)
    consumer = OperationId.new()
    candidate, graph = _candidate(setup, consumer, tool, assembly, machine)
    evidence = ToolAssemblyEvidence(True, tool.revision, tool.content_fingerprint, tool.unit, True, holder.revision, holder.content_fingerprint, holder.unit)
    requirement = MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint, machine.unit, (OperationCapability.MILLING,))
    parameters = _automatic_parameters(setup, profile, tool, assembly)
    setup = _setup_with_operation_aggregate(setup, assembly, consumer, parameters, profile, graph, requirement, (candidate,))
    values: dict[str, object] = {"setup": setup, "parameters": parameters, "profile": profile,
        "material_candidates": (candidate,), "dependency_graph": graph, "assembly": assembly, "assembly_evidence": evidence,
        "tool": tool, "machine": machine, "machine_requirement": requirement, "consumer_operation_id": consumer}
    values.update(changes)
    return RestContourFoundationInputs(**values)  # type: ignore[arg-type]


def _foundation(inputs: RestContourFoundationInputs) -> RestContourFoundation:
    descriptor = inputs.profile.descriptor
    def resolve(reference: GeometryReference) -> ResolvedContourProfile:
        return ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED if reference == descriptor.reference else GeometryResolutionStatus.MISSING,
            descriptor if reference == descriptor.reference else None,
        )
    return RestContourFoundation(resolve)


def test_real_artifact_full_identity_can_change_without_changing_material_semantics() -> None:
    inputs = _inputs()
    first, _ = _candidate(inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly, inputs.machine, feed=100.0)
    second, _ = _candidate(inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly, inputs.machine, feed=250.0)
    assert first.producer_artifact.artifact_fingerprint != second.producer_artifact.artifact_fingerprint
    assert compute_material_removal_fingerprint(first.producer_artifact) == compute_material_removal_fingerprint(second.producer_artifact)


def test_profile_resolver_is_called_once_for_the_exact_persisted_reference() -> None:
    inputs = _inputs()
    seen: list[GeometryReference] = []
    def resolve(reference: GeometryReference) -> ResolvedContourProfile:
        seen.append(reference)
        return ResolvedContourProfile(GeometryResolutionStatus.RESOLVED, inputs.profile.descriptor)
    assert RestContourFoundation(resolve).resolve(inputs).profile is not None
    assert seen == [inputs.profile.descriptor.reference]


def test_signed_depths_finite_categories_and_roundtrip() -> None:
    parameters = _base_parameters(top_height=Length(-2, LengthUnit.MM), final_depth=Length(-10, LengthUnit.MM),
        retract_height=Length(0, LengthUnit.MM), clearance_height=Length(1, LengthUnit.MM))
    assert RestContourParameters.from_dict(parameters.to_dict()) == parameters
    for change in ({"stepdown": Length(0, LengthUnit.MM)}, {"radial_stock_allowance": Length(-1, LengthUnit.MM)}):
        with pytest.raises(RestContourValidationError):
            _base_parameters(**change)
    with pytest.raises(CamUnitError):
        Length(float("inf"), LengthUnit.MM)


def test_persisted_automatic_contract_is_exact_and_adapter_uses_real_contour_policy() -> None:
    inputs = _inputs()
    contract = inputs.parameters.automatic_parameter_contract
    assert contract is not None
    parsed = __import__("hms_cadcam.cam.automatic_parameters", fromlist=["AutomaticParameterContract"]).AutomaticParameterContract.from_json(contract)
    assert parsed.policy_key == REST_CONTOUR_AUTOMATIC_POLICY_KEY
    assert {value.key for value in parsed.values} == set(REST_CONTOUR_AUTOMATIC_KEYS)
    assert _foundation(inputs).resolve(inputs).automatic_contract == parsed


def _wrong_policy(data: dict[str, object]) -> dict[str, object]:
    contract = AutomaticParameterContract.from_json(data["automatic_parameter_contract"])  # type: ignore[arg-type]
    return data | {"automatic_parameter_contract": AutomaticParameterContract("wrong.policy", contract.policy_version, contract.quality_profile, contract.values).to_json()}


@pytest.mark.parametrize("mutator", (lambda data: data | {"automatic_parameter_contract": "not-json"}, _wrong_policy))
def test_malformed_or_stale_automatic_payload_fails_closed(mutator) -> None:
    inputs = _inputs()
    payload = inputs.parameters.to_dict()
    with pytest.raises(RestContourValidationError) as captured:
        RestContourParameters.from_dict(mutator(payload))
    assert captured.value.code is RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED


def test_automatic_context_mutation_and_effective_value_mismatch_fail_closed() -> None:
    inputs = _inputs()
    mismatch = replace(inputs.parameters, stepdown=Length(inputs.parameters.stepdown.value / 2, LengthUnit.MM))
    with pytest.raises(RestContourValidationError, match="effective value"):
        _foundation(inputs).resolve(_with_persisted_parameters(inputs, mismatch))
    changed_tool = replace(inputs.tool, revision=Revision(1))
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, tool=changed_tool))
    assert captured.value.code in {RestContourDiagnosticCode.TOOL_INELIGIBLE, RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED}


def test_recomputed_auto_with_changed_same_id_tool_is_tool_ineligible() -> None:
    """A recomputed AUTO contract cannot make an assembly snapshot current."""
    inputs = _inputs()
    changed_tool = replace(
        inputs.tool,
        cutting_geometry=replace(
            inputs.tool.cutting_geometry,
            diameter=Length(inputs.tool.cutting_geometry.diameter.value * 0.8, LengthUnit.MM),
        ),
    )
    recomputed_parameters = _automatic_parameters(
        inputs.setup, inputs.profile, changed_tool, inputs.assembly,
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(
            _with_persisted_parameters(inputs, recomputed_parameters),
            tool=changed_tool,
        ))
    assert captured.value.code is RestContourDiagnosticCode.TOOL_INELIGIBLE


def test_inch_tool_assembly_cannot_enter_mm_rest_contour_authority() -> None:
    inputs = _inputs()
    inch_tool, inch_holder, inch_assembly, _inch_machine = basic_mill_resources(LengthUnit.INCH)
    setup = _replace_aggregate_operation(
        inputs.setup,
        inputs.consumer_operation_id,
        tool_assembly=ToolAssemblyReference.from_assembly(inch_assembly),
    )
    evidence = ToolAssemblyEvidence(
        True,
        inch_tool.revision,
        inch_tool.content_fingerprint,
        inch_tool.unit,
        True,
        inch_holder.revision,
        inch_holder.content_fingerprint,
        inch_holder.unit,
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(
            inputs,
            setup=setup,
            tool=inch_tool,
            assembly=inch_assembly,
            assembly_evidence=evidence,
        ))
    assert captured.value.code is RestContourDiagnosticCode.TOOL_INELIGIBLE


@pytest.mark.parametrize("authority", ("parameters", "tool_assembly", "machine_requirement"))
def test_supplied_inputs_must_match_persisted_consumer_authority(authority: str) -> None:
    inputs = _inputs()
    consumer = inputs.setup.operation_tree.get_operation(inputs.consumer_operation_id)
    if authority == "parameters":
        persisted_parameters = replace(
            inputs.parameters,
            radial_stock_allowance=Length(0.2, LengthUnit.MM),
        ).to_operation_parameters()
        changed = replace(consumer, parameters=persisted_parameters)
        expected = RestContourDiagnosticCode.INVALID_PARAMETERS
    elif authority == "tool_assembly":
        changed = replace(
            consumer,
            tool_assembly=replace(
                consumer.tool_assembly,
                expected_fingerprint=ContentFingerprint.from_payload({"detached": "assembly"}),
            ),
        )
        expected = RestContourDiagnosticCode.TOOL_INELIGIBLE
    else:
        assert consumer.machine_requirement is not None
        changed = replace(
            consumer,
            machine_requirement=replace(
                consumer.machine_requirement,
                expected_fingerprint=ContentFingerprint.from_payload({"detached": "machine"}),
            ),
        )
        expected = RestContourDiagnosticCode.MACHINE_INCOMPATIBLE
    setup = replace(
        inputs.setup,
        operation_tree=inputs.setup.operation_tree.replace_operation(changed),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, setup=setup))
    assert captured.value.code is expected


def test_roundtripped_turning_family_rest_contour_consumer_is_rejected() -> None:
    inputs = _inputs()
    consumer = inputs.setup.operation_tree.get_operation(inputs.consumer_operation_id)
    roundtripped = Operation.from_dict(
        replace(consumer, family=OperationFamily.TURNING).to_dict(),
    )
    assert roundtripped.family is OperationFamily.TURNING
    setup = replace(
        inputs.setup,
        operation_tree=inputs.setup.operation_tree.replace_operation(roundtripped),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, setup=setup))
    assert captured.value.code is RestContourDiagnosticCode.INVALID_PARAMETERS


def test_consumer_disabled_via_public_api_is_rejected() -> None:
    inputs = _inputs()
    consumer = inputs.setup.operation_tree.get_operation(inputs.consumer_operation_id)
    disabled = consumer.with_enabled(False)
    assert disabled.enabled is False
    setup = replace(
        inputs.setup,
        operation_tree=inputs.setup.operation_tree.replace_operation(disabled),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, setup=setup))
    assert captured.value.code is RestContourDiagnosticCode.INVALID_PARAMETERS


@pytest.mark.parametrize(
    "profile_defect",
    ("zero", "multiple", "wrong_role", "wrong_reference", "wrong_source", "wrong_fingerprint"),
)
def test_persisted_consumer_profile_authority_fails_closed(profile_defect: str) -> None:
    inputs = _inputs()
    consumer = inputs.setup.operation_tree.get_operation(inputs.consumer_operation_id)
    original = consumer.geometry_inputs[0]
    if profile_defect == "zero":
        geometry_inputs = ()
    elif profile_defect == "multiple":
        geometry_inputs = (
            original,
            replace(original, input_id=GeometryInputId.new(), selection_order=1),
        )
    elif profile_defect == "wrong_role":
        geometry_inputs = (replace(original, role=GeometryInputRole.BOUNDARY),)
    elif profile_defect == "wrong_reference":
        geometry_inputs = (
            replace(
                original,
                reference=replace(original.reference, reference_id=GeometryReferenceId.new()),
            ),
        )
    elif profile_defect == "wrong_source":
        geometry_inputs = (
            replace(original, reference=replace(original.reference, source_id=uuid4())),
        )
    else:
        geometry_inputs = (
            replace(
                original,
                reference=replace(
                    original.reference,
                    expected_geometry_fingerprint=GeometryFingerprint.from_payload({"stale": "profile"}),
                ),
            ),
        )
    changed = Operation.from_dict(replace(consumer, geometry_inputs=geometry_inputs).to_dict())
    setup = replace(
        inputs.setup,
        operation_tree=inputs.setup.operation_tree.replace_operation(changed),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, setup=setup))
    assert captured.value.code is RestContourDiagnosticCode.PROFILE_INVALID


@pytest.mark.parametrize("key", ("entry_segment_index", "lead_form", "lead_in_length"))
def test_forged_auto_effective_values_fail_against_fresh_contour_contract(key: str) -> None:
    inputs = _inputs()
    persisted = AutomaticParameterContract.from_json(inputs.parameters.automatic_parameter_contract or "")
    actual = persisted.value(key).resolved_value
    forged: object
    if key == "entry_segment_index":
        forged = (int(actual) + 1) % len(resolve_profile_in_setup(inputs.profile.descriptor, inputs.setup).loop.segments)
    elif key == "lead_form":
        forged = "normal_linear" if actual == "tangent_linear" else "tangent_linear"
    else:
        forged = float(actual) / 2.0
    forged_contract = AutomaticParameterContract(
        persisted.policy_key,
        persisted.policy_version,
        persisted.quality_profile,
        tuple(replace(value, resolved_value=forged) if value.key == key else value for value in persisted.values),
    )
    parameters = replace(inputs.parameters, automatic_parameter_contract=forged_contract.to_json())
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(_with_persisted_parameters(inputs, parameters))
    assert captured.value.code is RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED


def test_infeasible_manual_lead_is_revalidated_against_real_contour_geometry() -> None:
    inputs = _inputs()
    path = resolve_profile_in_setup(inputs.profile.descriptor, inputs.setup)
    geometry = inputs.tool.cutting_geometry
    contract = resolve_rest_contour_automatic_contract(
        RestContourAutomaticContext(
            inputs.parameters.unit, inputs.tool.family, geometry.diameter.value,
            getattr(geometry, "corner_radius", None) and geometry.corner_radius.value,
            geometry.axial_cutting_length.value, inputs.assembly.stickout.value, 8.0,
            inputs.parameters.tolerance.value, inputs.parameters.side, True, path.loop,
            inputs.profile.descriptor.outer_loop,
            inputs.profile.descriptor.geometry_fingerprint.digest,
            inputs.tool.content_fingerprint.digest,
        ),
        manual_overrides={
            "stepdown": inputs.parameters.stepdown.value,
            "entry_segment_index": 0,
            "lead_form": "normal_linear",
            "lead_in_length": 1000.0,
            "lead_out_length": 1000.0,
        },
    )
    parameters = replace(
        inputs.parameters,
        lead_in_length=Length(1000, LengthUnit.MM),
        lead_out_length=Length(1000, LengthUnit.MM),
        automatic_parameter_contract=contract.to_json(),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(_with_persisted_parameters(inputs, parameters))
    assert captured.value.code is RestContourDiagnosticCode.AUTOMATIC_UNRESOLVED


def test_ball_end_auto_is_unsupported_but_all_explicit_manual_values_are_valid() -> None:
    from hms_cadcam.cam.application.defaults import basic_parallel_resources
    setup = _setup(); tool, holder, assembly, machine = basic_parallel_resources(LengthUnit.MM); machine = _fixed_three_axis_machine(machine); profile = _profile(setup)
    preliminary = _base_parameters(); path = resolve_profile_in_setup(profile.descriptor, setup)
    contract = resolve_rest_contour_automatic_contract(RestContourAutomaticContext(LengthUnit.MM, tool.family, tool.cutting_geometry.diameter.value,
        None, tool.cutting_geometry.axial_cutting_length.value, assembly.stickout.value, 8.0, 0.01, ContourSide.ON, True,
        path.loop, profile.descriptor.outer_loop, profile.descriptor.geometry_fingerprint.digest, tool.content_fingerprint.digest),
        manual_overrides={"stepdown": 1.0, "entry_segment_index": 0, "lead_form": "normal_linear", "lead_in_length": 1.0, "lead_out_length": 1.0})
    consumer = OperationId.new(); candidate, graph = _candidate(setup, consumer, tool, assembly, machine)
    parameters = _base_parameters(automatic_parameter_contract=contract.to_json())
    requirement = MachineRequirement(machine.machine_id, machine.revision, machine.content_fingerprint, machine.unit, (OperationCapability.MILLING,))
    setup = _setup_with_operation_aggregate(setup, assembly, consumer, parameters, profile, graph, requirement, (candidate,))
    inputs = RestContourFoundationInputs(setup, parameters, profile, (candidate,), graph, assembly,
        ToolAssemblyEvidence(True, tool.revision, tool.content_fingerprint, tool.unit, True, holder.revision, holder.content_fingerprint, holder.unit), tool, machine,
        requirement, consumer)
    assert _foundation(inputs).resolve(inputs).automatic_contract is not None


def test_typed_material_dependency_and_generic_dag_roundtrip() -> None:
    inputs = _inputs(); candidate = inputs.material_candidates[0]
    assert DependencyGraph.from_dict(inputs.dependency_graph.to_dict()) == inputs.dependency_graph
    assert MaterialStateDependency.from_dict(candidate.dependency.to_dict()) == candidate.dependency
    assert candidate.edge.source_operation_id == candidate.producer_operation_id


def test_r260_material_dependency_generic_repository_roundtrip_reopens_and_resolves_rest_contour(tmp_path) -> None:
    inputs = _inputs()
    candidate = inputs.material_candidates[0]
    # R260 persists the state consumed by the Rest operation, rather than the
    # input state from which that consumed state was calculated.
    assert candidate.state.parent_fingerprint != candidate.state.fingerprint
    assert candidate.dependency.parent_state_fingerprint == candidate.state.fingerprint
    producer = Operation(
        candidate.producer_operation_id, CamNodeId.new(), OperationFamily.MILLING,
        inputs.setup.setup_id, ToolAssemblyReference.from_assembly(inputs.assembly), (),
        OperationParameterSet("upstream.material", 1, (), 1),
        inputs.machine_requirement,
        artifact_state=ArtifactState(
            ArtifactStatus.VALID,
            1,
            input_fingerprint=candidate.producer_artifact.input_fingerprint,
            artifact_fingerprint=candidate.producer_artifact.artifact_fingerprint,
            dirty_reasons=(),
        ),
    )
    consumer = Operation(
        inputs.consumer_operation_id, CamNodeId.new(), OperationFamily.MILLING,
        inputs.setup.setup_id, ToolAssemblyReference.from_assembly(inputs.assembly),
        (OperationGeometryInput(
            GeometryInputId.new(), GeometryInputRole.PROFILE,
            inputs.profile.descriptor.reference, True,
            inputs.profile.descriptor.reference.kind, 0,
        ),),
        inputs.parameters.to_operation_parameters(),
        inputs.machine_requirement,
    )
    tree = OperationTree.empty(inputs.setup.setup_id)
    tree = tree.add_operation(tree.root_id, "Producer", producer)
    tree = tree.add_operation(tree.root_id, "Rest Contour", consumer)
    tree = tree.with_dependency_added(candidate.edge)
    setup = replace(inputs.setup, operation_tree=tree)
    job = CamJob(CamJobId.new(), "R270 persistence", setups=(setup,), active_setup_id=setup.setup_id)
    snapshot = CamProjectSnapshot(
        (job,), job.job_id, (inputs.tool,), (), (inputs.assembly,), (inputs.machine,), (),
        (candidate.dependency,),
    )
    database_path = tmp_path / "project.db"
    ProjectDatabase().initialize(database_path)
    repository = CamSqliteRepository()
    with sqlite3.connect(database_path) as connection, connection:
        repository.replace_all(connection, snapshot)
    loaded = CamSqliteRepository().load(database_path)
    reopened = CamSqliteRepository().load(database_path)
    assert loaded == reopened
    assert loaded.material_state_dependencies == (candidate.dependency,)
    assert loaded.material_state_dependencies[0].parent_state_fingerprint == candidate.state.fingerprint
    loaded_consumer = next(
        item for item in loaded.jobs[0].setups[0].operation_tree.operations
        if item.operation_id == inputs.consumer_operation_id
    )
    loaded_parameters = RestContourParameters.from_operation_parameters(loaded_consumer.parameters)
    assert loaded_parameters == inputs.parameters
    assert len(loaded_consumer.geometry_inputs) == 1
    loaded_profile = RestContourProfileSelection(replace(
        inputs.profile.descriptor,
        reference=loaded_consumer.geometry_inputs[0].reference,
    ))
    loaded_setup = loaded.jobs[0].setups[0]
    loaded_edge = next(
        edge for edge in loaded_setup.operation_tree.dependency_graph.edges
        if edge.target_operation_id == inputs.consumer_operation_id
    )
    loaded_candidate = RestMaterialStateCandidate(
        candidate.producer_operation_id,
        candidate.state,
        loaded.material_state_dependencies[0],
        loaded_edge,
        candidate.producer_artifact,
    )
    reopened_result = _foundation(inputs).resolve(replace(
        inputs,
        setup=loaded_setup,
        parameters=loaded_parameters,
        profile=loaded_profile,
        material_candidates=(loaded_candidate,),
        dependency_graph=loaded_setup.operation_tree.dependency_graph,
    ))
    assert reopened_result.material.status is RestMaterialResolutionStatus.RESOLVED


@pytest.mark.parametrize("mutation, status", (
    ("none", RestMaterialResolutionStatus.MISSING), ("building", RestMaterialResolutionStatus.STALE),
    ("broken", RestMaterialResolutionStatus.INCONSISTENT), ("ambiguous", RestMaterialResolutionStatus.AMBIGUOUS),
))
def test_material_resolution_mutation_matrix(mutation: str, status: RestMaterialResolutionStatus) -> None:
    inputs = _inputs(); candidate = inputs.material_candidates[0]
    if mutation == "none":
        values, graph = (), inputs.dependency_graph
    elif mutation == "building":
        state = replace(candidate.state, status=MaterialStateStatus.BUILDING)
        values, graph = (replace(candidate, state=state),), inputs.dependency_graph
    elif mutation == "broken":
        values, graph = (candidate,), DependencyGraph((candidate.producer_operation_id, inputs.consumer_operation_id), ())
    else:
        other, _ = _candidate(inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly, inputs.machine)
        graph = DependencyGraph((candidate.producer_operation_id, other.producer_operation_id, inputs.consumer_operation_id), (candidate.edge, other.edge))
        values = (candidate, other)
    aggregate = _setup_with_operation_aggregate(
        inputs.setup, inputs.assembly, inputs.consumer_operation_id, inputs.parameters,
        inputs.profile, graph, candidates=values,
    )
    assert resolve_rest_material_state(
        values,
        setup_fingerprint=material_state_setup_fingerprint(aggregate),
        setup=aggregate,
        consumer_operation_id=inputs.consumer_operation_id,
    ).status is status


def test_material_graph_selects_only_its_unique_terminal_independent_of_candidate_order() -> None:
    inputs = _inputs()
    selected = inputs.material_candidates[0]
    unrelated_consumer = OperationId.new()
    unrelated, _ = _candidate(inputs.setup, unrelated_consumer, inputs.tool, inputs.assembly, inputs.machine)
    graph = DependencyGraph(
        (selected.producer_operation_id, unrelated.producer_operation_id, inputs.consumer_operation_id, unrelated_consumer),
        (selected.edge, unrelated.edge),
    )
    resolution = resolve_rest_material_state(
        (unrelated, selected),
        setup_fingerprint=material_state_setup_fingerprint(inputs.setup),
        setup=_setup_with_operation_aggregate(
            inputs.setup, inputs.assembly, inputs.consumer_operation_id, inputs.parameters,
            inputs.profile, graph, candidates=(unrelated, selected),
        ),
        consumer_operation_id=inputs.consumer_operation_id,
    )
    assert resolution.status is RestMaterialResolutionStatus.RESOLVED
    assert resolution.candidate == selected


def test_competing_material_terminal_edges_fail_closed() -> None:
    inputs = _inputs()
    selected = inputs.material_candidates[0]
    other, _ = _candidate(inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly, inputs.machine)
    graph = DependencyGraph(
        (selected.producer_operation_id, other.producer_operation_id, inputs.consumer_operation_id),
        (selected.edge, other.edge),
    )
    resolution = resolve_rest_material_state(
        (selected, other),
        setup_fingerprint=material_state_setup_fingerprint(inputs.setup),
        setup=_setup_with_operation_aggregate(
            inputs.setup, inputs.assembly, inputs.consumer_operation_id, inputs.parameters,
            inputs.profile, graph, candidates=(selected, other),
        ),
        consumer_operation_id=inputs.consumer_operation_id,
    )
    assert resolution.status is RestMaterialResolutionStatus.AMBIGUOUS


def test_typed_material_graph_cycle_is_rejected_before_resolution() -> None:
    inputs = _inputs()
    candidate = inputs.material_candidates[0]
    with pytest.raises(CamInvariantError):
        DependencyGraph(
            (candidate.producer_operation_id, inputs.consumer_operation_id),
            (
                candidate.edge,
                DependencyEdge.material_state(inputs.consumer_operation_id, candidate.producer_operation_id),
            ),
        )


def test_no_rest_material_is_typed_success_without_dependency_or_fingerprint() -> None:
    inputs = _inputs(); candidate, graph = _candidate(inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly, inputs.machine, rest=False)
    setup = _setup_with_operation_aggregate(
        inputs.setup, inputs.assembly, inputs.consumer_operation_id, inputs.parameters,
        inputs.profile, graph,
        inputs.machine_requirement, (candidate,),
    )
    result = _foundation(inputs).resolve(replace(
        inputs, setup=setup, material_candidates=(candidate,), dependency_graph=graph,
    ))
    assert result.material.status is RestMaterialResolutionStatus.NO_REST_MATERIAL
    assert result.dependency_edge is result.material_dependency is result.fingerprint is None


def test_no_rest_material_rejects_mutually_consistent_stale_stock_provenance() -> None:
    inputs = _inputs()
    candidate, graph = _candidate(inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly, inputs.machine, rest=False)
    stale_stock = ContentFingerprint.from_payload({"stock": "previous-revision"})
    stale_candidate = RestMaterialStateCandidate(
        candidate.producer_operation_id,
        replace(candidate.state, stock_fingerprint=stale_stock),
        replace(candidate.dependency, stock_fingerprint=stale_stock),
        candidate.edge,
        candidate.producer_artifact,
    )
    setup = _setup_with_operation_aggregate(
        inputs.setup,
        inputs.assembly,
        inputs.consumer_operation_id,
        inputs.parameters,
        inputs.profile,
        graph,
        inputs.machine_requirement, (candidate,),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(
            inputs,
            setup=setup,
            material_candidates=(stale_candidate,),
            dependency_graph=graph,
        ))
    assert captured.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


@pytest.mark.parametrize("artifact_defect", ("missing", "wrong_fingerprint"))
def test_material_dependency_requires_valid_exact_producer_artifact(artifact_defect: str) -> None:
    inputs = _inputs()
    candidate = inputs.material_candidates[0]
    producer = inputs.setup.operation_tree.get_operation(candidate.producer_operation_id)
    artifact_state = (
        ArtifactState()
        if artifact_defect == "missing"
        else ArtifactState(
            ArtifactStatus.VALID,
            1,
            artifact_fingerprint=ContentFingerprint.from_payload({"wrong": "toolpath"}),
            dirty_reasons=(),
        )
    )
    setup = replace(
        inputs.setup,
        operation_tree=inputs.setup.operation_tree.replace_operation(
            replace(producer, artifact_state=artifact_state),
        ),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, setup=setup))
    assert captured.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_no_rest_material_still_requires_valid_exact_producer_artifact() -> None:
    inputs = _inputs()
    candidate, graph = _candidate(inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly, inputs.machine, rest=False)
    setup = _setup_with_operation_aggregate(
        inputs.setup,
        inputs.assembly,
        inputs.consumer_operation_id,
        inputs.parameters,
        inputs.profile,
        graph,
        inputs.machine_requirement, (candidate,),
    )
    producer = setup.operation_tree.get_operation(candidate.producer_operation_id)
    setup = replace(
        setup,
        operation_tree=setup.operation_tree.replace_operation(
            replace(producer, artifact_state=ArtifactState()),
        ),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(
            inputs,
            setup=setup,
            material_candidates=(candidate,),
            dependency_graph=graph,
        ))
    assert captured.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_detached_dependency_graph_with_empty_setup_tree_fails_closed() -> None:
    inputs = _inputs()
    empty_setup = replace(
        inputs.setup,
        operation_tree=OperationTree.empty(inputs.setup.setup_id),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, setup=empty_setup))
    assert captured.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


@pytest.mark.parametrize("scope_mutation", ("turn", "general", "cylinder_stock"))
def test_r268_milling_box_stock_scope_is_enforced(scope_mutation: str) -> None:
    inputs = _inputs()
    if scope_mutation == "turn":
        setup = replace(inputs.setup, kind=SetupKind.TURN)
    elif scope_mutation == "general":
        setup = replace(inputs.setup, kind=SetupKind.GENERAL)
    else:
        setup = replace(
            inputs.setup,
            stock=CylinderStock(Length(100, LengthUnit.MM), Length(50, LengthUnit.MM), inputs.setup.wcs),
        )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, setup=setup))
    assert captured.value.code is RestContourDiagnosticCode.INVALID_PARAMETERS


@pytest.mark.parametrize("field", ("profile", "assembly", "machine"))
def test_profile_tool_and_machine_mutation_matrix(field: str) -> None:
    inputs = _inputs()
    if field == "profile":
        changed = replace(inputs, profile=_profile(inputs.setup, inner=True))
    elif field == "assembly":
        changed = replace(inputs, assembly=replace(inputs.assembly, stickout=Length(1, LengthUnit.MM)))
    else:
        changed = replace(inputs, machine_requirement=replace(inputs.machine_requirement, expected_fingerprint=ContentFingerprint.from_payload({"stale": True})))
    with pytest.raises(RestContourValidationError):
        _foundation(inputs).resolve(changed)


def test_machine_feed_spindle_kind_and_tool_capacity_fail_closed() -> None:
    inputs = _inputs()
    high_feed = replace(inputs.parameters, cutting_feed_rate=FeedRate(99_999, FeedUnit.MM_PER_MINUTE))
    with pytest.raises(RestContourValidationError):
        _foundation(inputs).resolve(_with_persisted_parameters(inputs, high_feed))
    low_spindle = replace(inputs.parameters, spindle_speed=SpindleSpeed(10))
    with pytest.raises(RestContourValidationError):
        _foundation(inputs).resolve(_with_persisted_parameters(inputs, low_spindle))
    excessive = replace(inputs.parameters, final_depth=Length(-30, LengthUnit.MM))
    with pytest.raises(RestContourValidationError):
        _foundation(inputs).resolve(_with_persisted_parameters(inputs, excessive))
    _tool, _holder, _assembly, one_axis_machine = basic_mill_resources(LengthUnit.MM)
    one_axis_requirement = MachineRequirement(
        one_axis_machine.machine_id,
        one_axis_machine.revision,
        one_axis_machine.content_fingerprint,
        one_axis_machine.unit,
        (OperationCapability.MILLING,),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(
            inputs, machine=one_axis_machine, machine_requirement=one_axis_requirement,
        ))
    assert captured.value.code is RestContourDiagnosticCode.MACHINE_INCOMPATIBLE


@pytest.mark.parametrize("mutation", ("foreign_source", "reference_kind", "provenance", "geometry_identity", "inner_loop"))
def test_profile_scope_kind_provenance_identity_and_inner_loop_fail_closed(mutation: str) -> None:
    inputs = _inputs(); descriptor = inputs.profile.descriptor
    if mutation == "foreign_source":
        changed = replace(descriptor, reference=replace(descriptor.reference, source_id=uuid4()))
    elif mutation == "reference_kind":
        changed = replace(descriptor, reference=replace(descriptor.reference, kind=GeometryReferenceKind.SKETCH_OR_PROFILE))
    elif mutation == "provenance":
        changed = replace(descriptor, provenance=replace(descriptor.provenance, source_kind=ContourProfileSource.CLOSED_WIRE))
    elif mutation == "geometry_identity":
        changed = replace(descriptor, reference=replace(descriptor.reference, expected_geometry_fingerprint=GeometryFingerprint.from_payload({"wrong": True})))
    else:
        changed = replace(descriptor, inner_loops=(descriptor.outer_loop,))
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, profile=RestContourProfileSelection(changed)))
    assert captured.value.code is RestContourDiagnosticCode.PROFILE_INVALID


@pytest.mark.parametrize("field", ("parent_state_fingerprint", "producer_toolpath_fingerprint", "setup_fingerprint", "stock_fingerprint", "engine_version"))
def test_material_state_full_provenance_mutation_matrix(field: str) -> None:
    inputs = _inputs(); candidate = inputs.material_candidates[0]
    value = ContentFingerprint.from_payload({"wrong": field}) if field != "engine_version" else "wrong-engine"
    dependency = replace(candidate.dependency, **{field: value})
    with pytest.raises(RestContourValidationError) as captured:
        RestMaterialStateCandidate(candidate.producer_operation_id, candidate.state, dependency, candidate.edge, candidate.producer_artifact)
    assert captured.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_current_stock_fingerprint_is_rechecked_after_persisted_provenance_roundtrip() -> None:
    inputs = _inputs()
    candidate = inputs.material_candidates[0]
    stale_stock = ContentFingerprint.from_payload({"stock": "stale"})
    stale = replace(
        candidate,
        state=replace(candidate.state, stock_fingerprint=stale_stock),
        dependency=replace(candidate.dependency, stock_fingerprint=stale_stock),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(inputs, material_candidates=(stale,)))
    assert captured.value.code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


@pytest.mark.parametrize("field, value", (("profile_fingerprint", "other-profile"), ("tool_fingerprint", "other-tool")))
def test_automatic_adapter_dependency_tracks_real_contour_evidence(field: str, value: str) -> None:
    inputs = _inputs(); path = resolve_profile_in_setup(inputs.profile.descriptor, inputs.setup); geometry = inputs.tool.cutting_geometry
    context = RestContourAutomaticContext(LengthUnit.MM, inputs.tool.family, geometry.diameter.value,
        getattr(geometry, "corner_radius", None) and geometry.corner_radius.value, geometry.axial_cutting_length.value,
        inputs.assembly.stickout.value, 8.0, inputs.parameters.tolerance.value, inputs.parameters.side, True,
        path.loop, inputs.profile.descriptor.outer_loop, inputs.profile.descriptor.geometry_fingerprint.digest,
        inputs.tool.content_fingerprint.digest)
    first = resolve_rest_contour_automatic_contract(context)
    second = resolve_rest_contour_automatic_contract(replace(context, **{field: value}))
    assert first.value("stepdown").dependency_fingerprint != second.value("stepdown").dependency_fingerprint


def test_foundation_fingerprint_covers_locked_semantic_inputs() -> None:
    inputs = _inputs(); first = _foundation(inputs).resolve(inputs).fingerprint
    changed = replace(inputs.parameters, radial_stock_allowance=Length(0.1, LengthUnit.MM))
    # New parameters need matching auto evidence only when their automatic fields change.
    second = _foundation(inputs).resolve(_with_persisted_parameters(inputs, changed)).fingerprint
    assert first != second


def test_producer_artifact_uses_producer_not_rest_consumer_tool_and_machine_authority() -> None:
    """A valid upstream may use a different persisted assembly and machine."""
    setup = _setup()
    consumer_tool, consumer_holder, consumer_assembly, consumer_machine = basic_mill_resources(LengthUnit.MM)
    producer_tool, _producer_holder, producer_assembly, producer_machine = basic_mill_resources(LengthUnit.MM)
    consumer_machine = _fixed_three_axis_machine(consumer_machine)
    producer_machine = _fixed_three_axis_machine(producer_machine)
    profile = _profile(setup)
    consumer = OperationId.new()
    candidate, graph = _candidate(
        setup, consumer, producer_tool, producer_assembly, producer_machine,
    )
    consumer_requirement = MachineRequirement(
        consumer_machine.machine_id, consumer_machine.revision,
        consumer_machine.content_fingerprint, consumer_machine.unit,
        (OperationCapability.MILLING,),
    )
    producer_requirement = MachineRequirement(
        producer_machine.machine_id, producer_machine.revision,
        producer_machine.content_fingerprint, producer_machine.unit,
        (OperationCapability.MILLING,),
    )
    parameters = _automatic_parameters(setup, profile, consumer_tool, consumer_assembly)
    aggregate = _setup_with_operation_aggregate(
        setup, consumer_assembly, consumer, parameters, profile, graph,
        consumer_requirement, (candidate,),
        producer_assembly=producer_assembly,
        producer_machine_requirement=producer_requirement,
    )
    evidence = ToolAssemblyEvidence(
        True, consumer_tool.revision, consumer_tool.content_fingerprint, consumer_tool.unit,
        True, consumer_holder.revision, consumer_holder.content_fingerprint, consumer_holder.unit,
    )
    inputs = RestContourFoundationInputs(
        aggregate, parameters, profile, (candidate,), graph, consumer_assembly,
        evidence, consumer_tool, consumer_machine, consumer_requirement, consumer,
    )
    assert _foundation(inputs).resolve(inputs).material.candidate == candidate


def test_setup_revision_advance_does_not_stale_semantically_current_producer_artifact() -> None:
    inputs = _inputs()
    original_setup_fingerprint = material_state_setup_fingerprint(inputs.setup)
    consumer = inputs.setup.operation_tree.get_operation(inputs.consumer_operation_id)
    advanced_tree = inputs.setup.operation_tree.replace_operation(
        replace(consumer, revision=consumer.revision.next()),
    )
    advanced_setup = inputs.setup.with_operation_tree(advanced_tree)
    assert advanced_setup.revision != inputs.setup.revision
    assert material_state_setup_fingerprint(advanced_setup) == original_setup_fingerprint
    assert _foundation(inputs).resolve(replace(inputs, setup=advanced_setup)).material.status is RestMaterialResolutionStatus.RESOLVED


def test_unrelated_stale_material_chain_does_not_poison_current_consumer_terminal() -> None:
    inputs = _inputs()
    selected = inputs.material_candidates[0]
    other_consumer = OperationId.new()
    unrelated, _ = _candidate(
        inputs.setup, other_consumer, inputs.tool, inputs.assembly, inputs.machine,
        status=MaterialStateStatus.BUILDING,
    )
    graph = DependencyGraph(
        (
            selected.producer_operation_id, unrelated.producer_operation_id,
            inputs.consumer_operation_id, other_consumer,
        ),
        (selected.edge, unrelated.edge),
    )
    aggregate = _setup_with_operation_aggregate(
        inputs.setup, inputs.assembly, inputs.consumer_operation_id,
        inputs.parameters, inputs.profile, graph, inputs.machine_requirement,
        (selected, unrelated),
    )
    result = _foundation(inputs).resolve(replace(
        inputs, setup=aggregate, material_candidates=(selected, unrelated), dependency_graph=graph,
    ))
    assert result.material.status is RestMaterialResolutionStatus.RESOLVED
    assert result.material.candidate == selected


def test_stale_selected_material_chain_still_fails_closed() -> None:
    inputs = _inputs()
    stale, graph = _candidate(
        inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly,
        inputs.machine, status=MaterialStateStatus.BUILDING,
    )
    aggregate = _setup_with_operation_aggregate(
        inputs.setup, inputs.assembly, inputs.consumer_operation_id,
        inputs.parameters, inputs.profile, graph, inputs.machine_requirement, (stale,),
    )
    with pytest.raises(RestContourValidationError) as captured:
        _foundation(inputs).resolve(replace(
            inputs, setup=aggregate, material_candidates=(stale,), dependency_graph=graph,
        ))
    assert captured.value.code is RestContourDiagnosticCode.MATERIAL_STATE_STALE


def test_feed_only_producer_artifact_update_passes_foundation_without_changing_semantic_fingerprint() -> None:
    inputs = _inputs()
    producer = inputs.material_candidates[0].producer_operation_id
    first, graph = _candidate(
        inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly,
        inputs.machine, feed=100.0, producer=producer,
    )
    second, _ = _candidate(
        inputs.setup, inputs.consumer_operation_id, inputs.tool, inputs.assembly,
        inputs.machine, feed=250.0, producer=producer,
    )
    assert first.producer_artifact.artifact_fingerprint != second.producer_artifact.artifact_fingerprint
    assert compute_material_removal_fingerprint(first.producer_artifact) == compute_material_removal_fingerprint(second.producer_artifact)
    first_setup = _setup_with_operation_aggregate(
        inputs.setup, inputs.assembly, inputs.consumer_operation_id,
        inputs.parameters, inputs.profile, graph, inputs.machine_requirement, (first,),
    )
    second_setup = _setup_with_operation_aggregate(
        inputs.setup, inputs.assembly, inputs.consumer_operation_id,
        inputs.parameters, inputs.profile, graph, inputs.machine_requirement, (second,),
    )
    first_result = _foundation(inputs).resolve(replace(
        inputs, setup=first_setup, material_candidates=(first,), dependency_graph=graph,
    ))
    second_result = _foundation(inputs).resolve(replace(
        inputs, setup=second_setup, material_candidates=(second,), dependency_graph=graph,
    ))
    assert first_result.fingerprint == second_result.fingerprint


def test_generic_operation_roundtrip_and_core_boundary_is_truthful() -> None:
    inputs = _inputs()
    operation = Operation(OperationId.new(), CamNodeId.new(), OperationFamily.MILLING, inputs.setup.setup_id,
        ToolAssemblyReference.from_assembly(inputs.assembly), (), OperationParameterSet(REST_CONTOUR_STRATEGY_KEY, 1, (), 1))
    assert Operation.from_dict(operation.to_dict()) == operation
    root = Path(__file__).parents[2] / "src" / "hms_cadcam" / "cam"
    for name in ("application/rest_contour.py", "domain/rest_contour.py", "automatic_rest_contour.py"):
        source = (root / name).read_text(encoding="utf-8")
        imports = tuple(node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom))
        assert not any(module.startswith(("hms_cadcam.ui", "hms_cadcam.post", "hms_cadcam.project", "hms_cadcam.simulation")) for module in imports)
        assert "ToolpathBuilder" not in source and "Motion(" not in source
    app_source = (root / "application/rest_contour.py").read_text(encoding="utf-8")
    assert "hms_cadcam.cam.application.contour" in app_source
