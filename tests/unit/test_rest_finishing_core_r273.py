"""R273 domain and Phase-A geometry tests use real R270-R272 authority bytes."""

from __future__ import annotations

from dataclasses import replace
from copy import copy, deepcopy
import hashlib
import json
import math
import pickle

import pytest

from hms_cadcam.cam.application.rest_finishing_geometry import (
    NoRestFinishingMaterial,
    RestFinishingGeometryInputs,
    RestFinishingRasterPlan,
    derive_rest_finishing_level,
    plan_rest_finishing_geometry,
)
import hms_cadcam.cam.application.rest_finishing_geometry as finishing_geometry
from hms_cadcam.cam.domain import (
    BallEndGeometry,
    BullNoseGeometry,
    CamNodeId,
    ContentFingerprint,
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourProfileSource,
    ContourSegment,
    CylindricalGeometry,
    DependencyEdge,
    DependencyGraph,
    DirtyReason,
    GeometryResolutionStatus,
    GeometryFingerprint,
    GeometryInputId,
    GeometryInputRole,
    Length,
    LengthUnit,
    MachineEvidence,
    OperationId,
    Operation,
    OperationFamily,
    OperationGeometryInput,
    OperationParameterSet,
    Point3,
    ResolvedContourProfile,
    Revision,
    SpindleDirection,
    ToolAssemblyEvidence,
    ToolAssemblyReference,
    ToolDefinitionId,
    ToolFamily,
    WorkEnvelope,
)
from hms_cadcam.cam.domain.rest_finishing import (
    REST_FINISHING_PARAMETER_FORMAT,
    RestFinishingDiagnosticCode,
    RestFinishingParameters,
    RestFinishingProfileSelection,
    RestFinishingValidationError,
)
from hms_cadcam.cam.domain.rest_contour import RestContourValidationError
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, SpindleSpeed
from hms_cadcam.cam.material_state import (
    MaterialStateLoadStatus,
    MaterialStateStore,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.persistence.models import (
    MaterialStateDependency,
    MaterialStateSuccessorPublication,
)
from hms_cadcam.cam.toolpath import (
    compute_material_removal_fingerprint,
    publish_toolpath,
)
from hms_cadcam.cam.application.rest_contour import RestMaterialStateCandidate
from hms_cadcam.cam.application.rest_contour_geometry import plan_rest_contour_residual
from hms_cadcam.cam.application.rest_contour_toolpath import (
    R272ValidatedSuccessorCertificate,
    RestContourPhaseBExecutionContext,
    generate_rest_contour_phase_b,
    mint_r272_validated_successor_certificate,
    prepare_rest_contour_phase_b,
)

from test_rest_contour_foundation_r270 import (
    _inputs as _r270_inputs,
)
from test_rest_contour_core_r271 import _positive_inputs as _r271_positive_inputs


_R272_MINT_BUNDLES: dict[
    R272ValidatedSuccessorCertificate,
    tuple[object, ...],
] = {}


def _parameters(
    *,
    nominal_target_z: float = 2.0,
    allowance: float = 0.0,
    tolerance: float = 0.01,
    stepover: float = 0.5,
    max_stepdown: float = 50.0,
) -> RestFinishingParameters:
    unit = LengthUnit.MM
    return RestFinishingParameters(
        unit,
        ContourProfileSource.PLANAR_FACE_OUTER,
        Length(nominal_target_z, unit),
        Length(allowance, unit),
        Length(tolerance, unit),
        Length(stepover, unit),
        Length(max_stepdown, unit),
        Length(55.0, unit),
        Length(52.0, unit),
        FeedRate(300.0, FeedUnit.MM_PER_MINUTE),
        FeedRate(80.0, FeedUnit.MM_PER_MINUTE),
        SpindleSpeed(1_000.0),
    )


def _inputs(
    *,
    parameters: RestFinishingParameters | None = None,
    complete: bool = False,
    tool_family: ToolFamily | None = None,
    tool_diameter: float = 1.0,
    cutting_length: float = 55.0,
    producer_feed_only: bool = False,
    producer_feed_schema_invalid: bool = False,
    foreign_replay_context: bool = False,
    authoritative_setup_enabled: bool = True,
    consumer_machine_mutator=None,
    cancellation=None,
) -> RestFinishingGeometryInputs:
    r272_inputs = replace(_r271_positive_inputs(), cancellation=cancellation)
    r272_plan = plan_rest_contour_residual(r272_inputs)
    r272_context = RestContourPhaseBExecutionContext(r272_inputs, r272_plan)
    r272_prepared = prepare_rest_contour_phase_b(r272_context)
    r272_candidate = generate_rest_contour_phase_b(r272_prepared)
    publication_result = publish_toolpath(
        r272_prepared.computing_operation,
        r272_candidate.artifact,
        r272_prepared.computation_token,
        r272_prepared.input_fingerprint,
    )
    assert publication_result.accepted and publication_result.operation is not None
    producer_operation = publication_result.operation
    if producer_feed_only or producer_feed_schema_invalid:
        feed_values = tuple(
            (
                name,
                325.0 if name == "cutting_feed_rate" else value,
            )
            for name, value in producer_operation.parameters.values
            if not (
                producer_feed_schema_invalid
                and name in {
                    "cutting_feed_rate",
                    "plunge_feed_rate",
                    "spindle_speed",
                }
            )
        )
        producer_operation = replace(
            producer_operation,
            parameters=replace(
                producer_operation.parameters,
                values=feed_values,
            ),
            revision=producer_operation.revision.next(),
            artifact_state=producer_operation.artifact_state.mark_dirty(
                DirtyReason.PARAMETERS_CHANGED
            ),
        )
    base = _r270_inputs()
    base = replace(
        base,
        setup=r272_inputs.setup,
        profile=replace(base.profile, descriptor=r272_inputs.profile_descriptor),
        tool=r272_inputs.tool,
        assembly=r272_inputs.assembly,
        assembly_evidence=r272_inputs.assembly_evidence,
        machine=r272_inputs.machine,
        machine_requirement=r272_plan.authority.machine_requirement,
        consumer_operation_id=OperationId.new(),
    )
    parameters = parameters or _parameters()
    descriptor = base.profile.descriptor
    unit = base.tool.unit
    point_values = (
        ((9.0, 20.0), (11.0, 20.0), (11.0, 30.0), (9.0, 30.0))
        if complete
        else ((10.0, 20.0), (70.0, 20.0), (70.0, 30.0), (10.0, 30.0))
    )
    points = tuple(Point3(x, y, 0.0, unit) for x, y in point_values)
    loop = ContourLoop(
        tuple(
            ContourSegment(
                ContourCurveKind.LINE,
                points[index],
                points[(index + 1) % len(points)],
            )
            for index in range(len(points))
        ),
        ContourOrientation.COUNTERCLOCKWISE,
    )
    geometry = GeometryFingerprint.from_payload(loop.to_dict())
    descriptor = replace(
        descriptor,
        outer_loop=loop,
        inner_loops=(),
        geometry_fingerprint=geometry,
        reference=replace(
            descriptor.reference,
            expected_geometry_fingerprint=geometry,
        ),
        bounds=replace(
            descriptor.bounds,
            minimum=Point3(
                min(value[0] for value in point_values),
                min(value[1] for value in point_values),
                0.0,
                unit,
            ),
            maximum=Point3(
                max(value[0] for value in point_values),
                max(value[1] for value in point_values),
                0.0,
                unit,
            ),
        ),
    )
    profile = RestFinishingProfileSelection(descriptor)
    producer_parent_state = r272_prepared.predecessor_state
    successor_state = r272_candidate.successor_state
    producer_id = producer_operation.operation_id
    parent_operation_id = r272_inputs.foundation.dependency_edge.source_operation_id
    edge = DependencyEdge.material_state(producer_id, base.consumer_operation_id)
    dependency = MaterialStateDependency(
        base.consumer_operation_id,
        producer_id,
        successor_state.fingerprint,
        compute_material_removal_fingerprint(r272_candidate.artifact),
        successor_state.setup_fingerprint,
        successor_state.stock_fingerprint,
        successor_state.engine_version,
        successor_state.precision.to_dict(),
    )
    candidate = RestMaterialStateCandidate(
        producer_id,
        successor_state,
        dependency,
        edge,
        r272_candidate.artifact,
    )
    parent_edge = DependencyEdge.material_state(
        parent_operation_id,
        producer_id,
    )
    graph = DependencyGraph(
        (parent_operation_id, producer_id, base.consumer_operation_id),
        (parent_edge, edge),
    )
    machine = replace(
        base.machine,
        spindles=tuple(
            replace(
                spindle,
                directions=(SpindleDirection.CLOCKWISE,),
            )
            for spindle in base.machine.spindles
        ),
    )
    if consumer_machine_mutator is not None:
        machine = consumer_machine_mutator(machine)
    machine_requirement = replace(
        base.machine_requirement,
        expected_revision=machine.revision,
        expected_fingerprint=machine.content_fingerprint,
    )
    tool = replace(
        base.tool,
        tool_id=ToolDefinitionId.new(),
        cutting_geometry=CylindricalGeometry(
            Length(tool_diameter, base.tool.unit),
            Length(cutting_length, base.tool.unit),
        ),
        overall_length=Length(max(70.0, cutting_length), base.tool.unit),
        usable_length=Length(max(60.0, cutting_length), base.tool.unit),
    )
    assembly = replace(
        base.assembly,
        tool_id=tool.tool_id,
        expected_tool_revision=tool.revision,
        expected_tool_fingerprint=tool.content_fingerprint,
        stickout=Length(55.0, tool.unit),
    )
    evidence = ToolAssemblyEvidence(
        True,
        tool.revision,
        tool.content_fingerprint,
        tool.unit,
        base.assembly_evidence.holder_exists,
        base.assembly_evidence.holder_revision,
        base.assembly_evidence.holder_fingerprint,
        base.assembly_evidence.holder_unit,
    )
    if tool_family is not None:
        if tool_family is ToolFamily.BALL_END_MILL:
            geometry = BallEndGeometry(Length(10.0, tool.unit), Length(20.0, tool.unit))
        elif tool_family is ToolFamily.BULL_NOSE_END_MILL:
            geometry = BullNoseGeometry(
                Length(10.0, tool.unit),
                Length(20.0, tool.unit),
                Length(1.0, tool.unit),
            )
        else:
            raise AssertionError("unsupported test family")
        tool = replace(
            tool,
            tool_id=ToolDefinitionId.new(),
            family=tool_family,
            cutting_geometry=geometry,
        )
        assembly = replace(
            assembly,
            tool_id=tool.tool_id,
            expected_tool_revision=tool.revision,
            expected_tool_fingerprint=tool.content_fingerprint,
        )
        evidence = ToolAssemblyEvidence(
            True,
            tool.revision,
            tool.content_fingerprint,
            tool.unit,
            base.assembly_evidence.holder_exists,
            base.assembly_evidence.holder_revision,
            base.assembly_evidence.holder_fingerprint,
            base.assembly_evidence.holder_unit,
        )
    tree = r272_inputs.setup.operation_tree.replace_operation(producer_operation)
    consumer = Operation(
        base.consumer_operation_id,
        CamNodeId.new(),
        OperationFamily.MILLING,
        base.setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly),
        (
            OperationGeometryInput(
                GeometryInputId.new(),
                GeometryInputRole.PROFILE,
                profile.descriptor.reference,
                True,
                profile.descriptor.reference.kind,
                0,
            ),
        ),
        parameters.to_operation_parameters(),
        machine_requirement,
    )
    tree = tree.add_operation(tree.root_id, "Rest Finishing", consumer)
    tree = tree.with_dependency_added(edge)
    setup = replace(
        base.setup,
        operation_tree=tree,
        enabled=authoritative_setup_enabled,
    )
    graph = tree.dependency_graph
    state = candidate.state
    artifact = candidate.producer_artifact
    publication = MaterialStateSuccessorPublication.create(
        consumer_operation_id=candidate.producer_operation_id,
        artifact_id=artifact.artifact_id,
        artifact_fingerprint=artifact.artifact_fingerprint,
        input_fingerprint=artifact.input_fingerprint,
        semantic_material_removal_fingerprint=compute_material_removal_fingerprint(artifact),
        parent_state_fingerprint=producer_parent_state.fingerprint,
        parent_state_content_seal=producer_parent_state.content_integrity_fingerprint,
        successor_state_fingerprint=state.fingerprint,
        successor_state_content_seal=state.content_integrity_fingerprint,
        setup_fingerprint=state.setup_fingerprint,
        stock_fingerprint=state.stock_fingerprint,
        engine_version=state.engine_version,
        precision=state.precision.to_dict(),
    )
    producer_operation = setup.operation_tree.get_operation(producer_id)
    parent_operation = setup.operation_tree.get_operation(parent_operation_id)
    producer_dependency = MaterialStateDependency(
        producer_id,
        parent_operation_id,
        producer_parent_state.fingerprint,
        producer_parent_state.toolpath_fingerprint,
        producer_parent_state.setup_fingerprint,
        producer_parent_state.stock_fingerprint,
        producer_parent_state.engine_version,
        producer_parent_state.precision.to_dict(),
        publication,
        finishing_geometry._material_removal_operation_fingerprint(
            parent_operation
        ),
    )

    def resolver(reference):
        if reference == profile.descriptor.reference:
            return ResolvedContourProfile(GeometryResolutionStatus.RESOLVED, profile.descriptor)
        return ResolvedContourProfile(GeometryResolutionStatus.MISSING, None)

    mint_context = r272_context
    if foreign_replay_context:
        foreign_inputs = replace(
            _r271_positive_inputs(profile_points=((15, 15), (65, 15), (65, 65), (15, 65))),
            cancellation=cancellation,
        )
        mint_context = RestContourPhaseBExecutionContext(
            foreign_inputs,
            plan_rest_contour_residual(foreign_inputs),
        )
    certificate = mint_r272_validated_successor_certificate(
        replay_context=mint_context,
        validation_candidate=r272_candidate,
        authoritative_setup=setup,
        authoritative_producer_operation=producer_operation,
        exact_producer_artifact=r272_candidate.artifact,
        trusted_parent_state=producer_parent_state,
        supplied_successor_state=successor_state,
        producer_completion=publication,
        producer_dependency=producer_dependency,
        cancellation=cancellation,
    )
    _R272_MINT_BUNDLES[certificate] = (
        r272_context,
        r272_candidate,
        producer_operation,
        r272_candidate.artifact,
        producer_parent_state,
        successor_state,
        publication,
        producer_dependency,
        cancellation,
    )
    return RestFinishingGeometryInputs(
        setup,
        parameters,
        profile,
        (candidate,),
        publication,
        producer_dependency,
        producer_parent_state,
        certificate,
        graph,
        assembly,
        evidence,
        tool,
        machine,
        machine_requirement,
        MachineEvidence(
            True,
            machine.revision,
            machine.content_fingerprint,
            machine.unit,
            machine.capabilities.operations,
        ),
        base.consumer_operation_id,
        resolver,
        cancellation,
    )


def _with_exact_r273_parameters(
    inputs: RestFinishingGeometryInputs,
    parameters: RestFinishingParameters,
) -> RestFinishingGeometryInputs:
    """Edit only the R273 consumer and remint against that exact Setup snapshot."""
    consumer = inputs.setup.operation_tree.get_operation(inputs.consumer_operation_id)
    updated_consumer = replace(
        consumer,
        parameters=parameters.to_operation_parameters(),
        revision=consumer.revision.next(),
        artifact_state=consumer.artifact_state.mark_dirty(
            DirtyReason.PARAMETERS_CHANGED
        ),
    )
    setup = replace(
        inputs.setup,
        operation_tree=inputs.setup.operation_tree.replace_operation(updated_consumer),
    )
    (
        replay_context,
        validation_candidate,
        producer_operation,
        exact_producer_artifact,
        trusted_parent_state,
        supplied_successor_state,
        producer_completion,
        producer_dependency,
        cancellation,
    ) = _R272_MINT_BUNDLES[inputs.producer_validation_certificate]
    certificate = mint_r272_validated_successor_certificate(
        replay_context=replay_context,
        validation_candidate=validation_candidate,
        authoritative_setup=setup,
        authoritative_producer_operation=producer_operation,
        exact_producer_artifact=exact_producer_artifact,
        trusted_parent_state=trusted_parent_state,
        supplied_successor_state=supplied_successor_state,
        producer_completion=producer_completion,
        producer_dependency=producer_dependency,
        cancellation=cancellation,
    )
    _R272_MINT_BUNDLES[certificate] = _R272_MINT_BUNDLES[
        inputs.producer_validation_certificate
    ]
    return replace(
        inputs,
        setup=setup,
        parameters=parameters,
        dependency_graph=setup.operation_tree.dependency_graph,
        producer_validation_certificate=certificate,
    )


def _assert_code(inputs: RestFinishingGeometryInputs, code: RestFinishingDiagnosticCode) -> None:
    with pytest.raises(RestFinishingValidationError) as captured:
        plan_rest_finishing_geometry(inputs)
    assert captured.value.code is code


def test_manual_schema_roundtrip_allowance_and_auto_fail_closed() -> None:
    parameters = _parameters(nominal_target_z=3.25, allowance=0.75)
    assert parameters.cut_z == 4.0
    assert RestFinishingParameters.from_dict(parameters.to_dict()) == parameters
    assert RestFinishingParameters.from_operation_parameters(
        parameters.to_operation_parameters()
    ) == parameters

    auto = parameters.to_dict() | {"mode": "AUTO"}
    with pytest.raises(RestFinishingValidationError) as captured:
        RestFinishingParameters.from_dict(auto)
    assert captured.value.code is RestFinishingDiagnosticCode.AUTOMATIC_FORBIDDEN

    unknown = parameters.to_dict() | {"invented": 1}
    with pytest.raises(RestFinishingValidationError) as captured:
        RestFinishingParameters.from_dict(unknown)
    assert captured.value.code is RestFinishingDiagnosticCode.INVALID_PARAMETERS


@pytest.mark.parametrize("field", ("stepover", "max_stepdown", "tolerance"))
def test_manual_positive_lengths_reject_zero_and_nonfinite(field: str) -> None:
    payload = _parameters().to_dict()
    payload[field] = 0.0
    with pytest.raises(RestFinishingValidationError):
        RestFinishingParameters.from_dict(payload)
    payload = _parameters().to_dict()
    payload[field] = float("nan")
    with pytest.raises(RestFinishingValidationError):
        RestFinishingParameters.from_dict(payload)


def test_plan_is_deterministic_terminal_inclusive_and_rederived_from_current_state() -> None:
    inputs = _inputs()
    first = plan_rest_finishing_geometry(inputs)
    second = plan_rest_finishing_geometry(inputs)
    assert isinstance(first, RestFinishingRasterPlan)
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.levels[-1] == inputs.parameters.cut_z
    assert first.raster_positions[-1].y > first.raster_positions[0].y
    assert first.raster_positions[-1].intervals
    level = derive_rest_finishing_level(first, first.predecessor_state, first.levels[0])
    assert level is not None
    assert level.state_fingerprint == first.predecessor_state.fingerprint
    assert level.spans
    assert set(level.work_cells) == set().union(
        *(set(component.cells) for component in level.work_components)
    )


def test_complete_material_at_target_returns_typed_no_work_with_no_artifact() -> None:
    inputs = _inputs(parameters=_parameters(nominal_target_z=1.99, allowance=0.0), complete=True)
    result = plan_rest_finishing_geometry(inputs)
    assert isinstance(result, NoRestFinishingMaterial)
    assert result.predecessor_state.content_is_verified
    assert not hasattr(result, "artifact")
    assert not hasattr(result, "successor_state")


def test_below_target_fails_before_no_work() -> None:
    inputs = _inputs(parameters=_parameters(nominal_target_z=2.02, allowance=0.0), complete=True)
    _assert_code(inputs, RestFinishingDiagnosticCode.MATERIAL_BELOW_TARGET)


@pytest.mark.parametrize("family", (ToolFamily.BALL_END_MILL, ToolFamily.BULL_NOSE_END_MILL))
def test_ball_and_bull_are_typed_tool_failures(family: ToolFamily) -> None:
    _assert_code(_inputs(tool_family=family), RestFinishingDiagnosticCode.TOOL_INELIGIBLE)


def test_schema_format_and_public_values_are_stable() -> None:
    payload = _parameters().to_dict()
    assert payload["format"] == REST_FINISHING_PARAMETER_FORMAT
    assert payload["format_version"] == 1
    assert payload["strategy_version"] == 1
    assert payload["schema_version"] == 1


def test_tolerance_equality_is_complete_and_strictly_higher_cells_are_work() -> None:
    inputs = _inputs(
        parameters=_parameters(
            nominal_target_z=1.99,
            allowance=0.0,
            tolerance=0.01,
        )
    )
    plan = plan_rest_finishing_geometry(inputs)
    assert isinstance(plan, RestFinishingRasterPlan)
    level = derive_rest_finishing_level(plan, plan.predecessor_state, plan.levels[-1])
    assert level is not None
    equality_cells = {
        cell
        for cell in plan.target_cells
        if plan.predecessor_state.top_heights[
            cell[0] * plan.predecessor_state.width + cell[1]
        ]
        == inputs.parameters.cut_z + inputs.parameters.tolerance.value
    }
    higher_cells = {
        cell
        for cell in plan.target_cells
        if plan.predecessor_state.top_heights[
            cell[0] * plan.predecessor_state.width + cell[1]
        ]
        > inputs.parameters.cut_z + inputs.parameters.tolerance.value
    }
    assert equality_cells and higher_cells
    assert equality_cells.isdisjoint(level.work_cells)
    assert higher_cells.issubset(level.work_cells)


def test_disconnected_component_order_is_canonical_from_real_state_grid() -> None:
    inputs = _inputs()
    state = inputs.material_candidates[0].state
    first = ((8, 8), (8, 9), (9, 8))
    second = ((30, 30), (30, 31), (31, 30))
    forward = finishing_geometry._components(state, tuple(sorted((*second, *first))))
    reverse = finishing_geometry._components(state, tuple(sorted((*first, *second))))
    assert forward == reverse
    assert tuple(component.cells for component in forward) == (
        tuple(sorted(first)),
        tuple(sorted(second)),
    )


def test_holes_are_rejected_by_the_frozen_profile_selection_contract() -> None:
    inputs = _inputs()
    descriptor = inputs.profile_selection.descriptor
    with pytest.raises(RestFinishingValidationError) as captured:
        RestFinishingProfileSelection(
            replace(descriptor, inner_loops=(descriptor.outer_loop,))
        )
    assert captured.value.code is RestFinishingDiagnosticCode.UNSUPPORTED


def test_exact_line_self_intersection_and_arc_scanline_tangency() -> None:
    unit = LengthUnit.MM
    bow = tuple(
        Point3(x, y, 0.0, unit)
        for x, y in ((0.0, 0.0), (10.0, 10.0), (0.0, 10.0), (10.0, 0.0))
    )
    bow_loop = ContourLoop(
        tuple(
            ContourSegment(
                ContourCurveKind.LINE,
                bow[index],
                bow[(index + 1) % len(bow)],
            )
            for index in range(len(bow))
        ),
        ContourOrientation.COUNTERCLOCKWISE,
    )
    assert finishing_geometry._loop_self_intersects(bow_loop)

    center = Point3(10.0, 10.0, 0.0, unit)
    right = Point3(20.0, 10.0, 0.0, unit)
    left = Point3(0.0, 10.0, 0.0, unit)
    circle = ContourLoop(
        (
            ContourSegment(ContourCurveKind.ARC, right, left, center, math.pi),
            ContourSegment(ContourCurveKind.ARC, left, right, center, math.pi),
        ),
        ContourOrientation.COUNTERCLOCKWISE,
    )
    assert finishing_geometry._horizontal_intervals(circle, 10.0) == ((0.0, 20.0),)
    assert finishing_geometry._horizontal_intervals(circle, 20.0) == ()

    notch_points = tuple(
        Point3(x, y, 0.0, unit)
        for x, y in (
            (0.0, 0.0),
            (10.0, 0.0),
            (10.0, 10.0),
            (6.0, 10.0),
            (4.0, 10.0),
            (0.0, 10.0),
        )
    )
    notch = ContourLoop(
        (
            ContourSegment(ContourCurveKind.LINE, notch_points[0], notch_points[1]),
            ContourSegment(ContourCurveKind.LINE, notch_points[1], notch_points[2]),
            ContourSegment(ContourCurveKind.LINE, notch_points[2], notch_points[3]),
            ContourSegment(
                ContourCurveKind.ARC,
                notch_points[3],
                notch_points[4],
                Point3(5.0, 10.0, 0.0, unit),
                -math.pi,
            ),
            ContourSegment(ContourCurveKind.LINE, notch_points[4], notch_points[5]),
            ContourSegment(ContourCurveKind.LINE, notch_points[5], notch_points[0]),
        ),
        ContourOrientation.COUNTERCLOCKWISE,
    )
    assert not finishing_geometry._loop_self_intersects(notch)
    assert finishing_geometry._horizontal_intervals(notch, 9.0) == ((0.0, 10.0),)
    assert finishing_geometry._point_on_or_inside(notch, 7.5, 9.0)


def test_oversized_flat_tool_fails_whole_operation_as_unreachable() -> None:
    inputs = _inputs(
        parameters=_parameters(stepover=50.0),
        tool_diameter=100.0,
    )
    _assert_code(inputs, RestFinishingDiagnosticCode.UNREACHABLE_FINISHING_MATERIAL)


def test_stale_detached_or_tampered_material_authority_fails_closed() -> None:
    inputs = _inputs()
    candidate = inputs.material_candidates[0]
    detached = replace(candidate.state)
    detached_candidate = replace(candidate, state=detached)
    _assert_code(
        replace(inputs, material_candidates=(detached_candidate,)),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )

    heights = list(candidate.state.top_heights)
    heights[0] += 0.25
    object.__setattr__(candidate.state, "top_heights", tuple(heights))
    _assert_code(inputs, RestFinishingDiagnosticCode.MATERIAL_STATE_STALE)


def test_machine_feed_spindle_and_cutting_length_limits_are_typed() -> None:
    inputs = _inputs()
    excessive = replace(
        inputs.parameters,
        cutting_feed_rate=replace(
            inputs.parameters.cutting_feed_rate,
            value=inputs.machine.capabilities.maximum_feed.value + 1.0,
        ),
    )
    _assert_code(
        _inputs(parameters=excessive),
        RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE,
    )

    short = _inputs(
        parameters=_parameters(max_stepdown=0.5),
        cutting_length=1.0,
    )
    _assert_code(short, RestFinishingDiagnosticCode.TOOL_INELIGIBLE)


def test_machine_work_envelope_and_axis_travel_fail_closed() -> None:
    _assert_code(
        _inputs(
            consumer_machine_mutator=lambda machine: replace(
                machine,
                work_envelope=WorkEnvelope(
                    Length(1.0, machine.unit),
                    Length(1.0, machine.unit),
                    Length(1.0, machine.unit),
                ),
            )
        ),
        RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE,
    )

    _assert_code(
        _inputs(
            consumer_machine_mutator=lambda machine: replace(
                machine,
                axes=tuple(
                    replace(
                        axis,
                        minimum=Length(-1.0, machine.unit),
                        maximum=Length(1.0, machine.unit),
                        home=Length(0.0, machine.unit),
                    )
                    for axis in machine.axes
                ),
            )
        ),
        RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE,
    )

    def translate_machine(machine):
        root = machine.kinematic_chain.nodes[0]
        values = list(root.fixed_transform.values)
        values[3] = 1_000.0
        translated_root = replace(
            root,
            fixed_transform=replace(root.fixed_transform, values=tuple(values)),
        )
        return replace(
            machine,
            kinematic_chain=replace(
                machine.kinematic_chain,
                nodes=(translated_root, *machine.kinematic_chain.nodes[1:]),
            ),
        )

    _assert_code(
        _inputs(consumer_machine_mutator=translate_machine),
        RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE,
    )

    _assert_code(
        _inputs(
            consumer_machine_mutator=lambda machine: replace(
                machine,
                spindles=tuple(
                    replace(
                        spindle,
                        directions=(SpindleDirection.COUNTERCLOCKWISE,),
                    )
                    for spindle in machine.spindles
                ),
            )
        ),
        RestFinishingDiagnosticCode.MACHINE_INCOMPATIBLE,
    )


def test_stepdown_near_cut_z_keeps_exact_safe_terminal_level() -> None:
    parameters = _parameters(
        nominal_target_z=0.0,
        allowance=0.0,
        max_stepdown=1.0,
    )
    hmax = 2.000000005
    levels = finishing_geometry._levels(hmax, parameters)
    formula = tuple(
        max(parameters.cut_z, hmax - index * parameters.max_stepdown.value)
        for index in range(1, 4)
    )
    assert all(
        abs(actual - expected) <= finishing_geometry._EPSILON
        for actual, expected in zip(levels, formula, strict=True)
    )
    assert levels[-1] == parameters.cut_z
    assert all(
        upper - lower <= parameters.max_stepdown.value
        for upper, lower in zip((hmax, *levels[:-1]), levels, strict=True)
    )


def test_decimal_stepdown_never_exceeds_manual_ceiling_by_one_ulp() -> None:
    parameters = _parameters(
        nominal_target_z=0.0,
        allowance=0.0,
        max_stepdown=0.1,
    )
    hmax = 0.31000000000000005
    levels = finishing_geometry._levels(hmax, parameters)
    assert levels[-1] == parameters.cut_z
    assert all(
        upper - lower <= parameters.max_stepdown.value
        for upper, lower in zip((hmax, *levels[:-1]), levels, strict=True)
    )


def test_sub_ulp_stepdown_fails_typed_instead_of_hanging() -> None:
    cut_z = math.nextafter(1.0, -math.inf)
    parameters = _parameters(
        nominal_target_z=cut_z,
        allowance=0.0,
        tolerance=1.0e-18,
        max_stepdown=1.0e-17,
    )
    with pytest.raises(RestFinishingValidationError) as captured:
        finishing_geometry._levels(1.0, parameters)
    assert captured.value.code is RestFinishingDiagnosticCode.INVALID_PARAMETERS


def test_tiny_stepdown_ratio_overflow_fails_typed_before_ceil() -> None:
    parameters = _parameters(
        nominal_target_z=0.0,
        allowance=0.0,
        tolerance=1.0e-18,
        max_stepdown=5.0e-324,
    )
    with pytest.raises(RestFinishingValidationError) as captured:
        finishing_geometry._levels(1.0, parameters)
    assert captured.value.code is RestFinishingDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED


def test_tiny_stepover_ratio_overflow_fails_typed_before_ceil() -> None:
    inputs = _inputs(parameters=_parameters(stepover=5.0e-324))
    _assert_code(
        inputs,
        RestFinishingDiagnosticCode.TOOLPATH_LIMIT_EXCEEDED,
    )


def test_upstream_producer_completion_witness_is_required_and_exact() -> None:
    inputs = _inputs()
    candidate = inputs.material_candidates[0]
    assert candidate.dependency.successor_publication is None
    parent_operation = inputs.setup.operation_tree.get_operation(
        inputs.producer_dependency.producer_operation_id
    )
    assert inputs.producer_dependency.producer_operation_authority_fingerprint == (
        finishing_geometry._material_removal_operation_fingerprint(parent_operation)
    )
    assert inputs.producer_validation_certificate is not None
    _assert_code(replace(inputs, producer_completion=None),
                 RestFinishingDiagnosticCode.INVALID_PARAMETERS)
    state = candidate.state
    artifact = candidate.producer_artifact
    wrong_consumer = MaterialStateSuccessorPublication.create(
        consumer_operation_id=inputs.consumer_operation_id,
        artifact_id=artifact.artifact_id,
        artifact_fingerprint=artifact.artifact_fingerprint,
        input_fingerprint=artifact.input_fingerprint,
        semantic_material_removal_fingerprint=compute_material_removal_fingerprint(artifact),
        parent_state_fingerprint=state.fingerprint,
        parent_state_content_seal=state.content_integrity_fingerprint,
        successor_state_fingerprint=state.fingerprint,
        successor_state_content_seal=state.content_integrity_fingerprint,
        setup_fingerprint=state.setup_fingerprint,
        stock_fingerprint=state.stock_fingerprint,
        engine_version=state.engine_version,
        precision=state.precision.to_dict(),
    )
    _assert_code(
        replace(inputs, producer_completion=wrong_consumer),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )
    assert isinstance(plan_rest_finishing_geometry(inputs), RestFinishingRasterPlan)


def test_r272_certificate_is_opaque_process_local_and_required() -> None:
    inputs = _inputs()
    certificate = inputs.producer_validation_certificate
    assert "token" not in repr(certificate).lower()
    with pytest.raises(TypeError):
        R272ValidatedSuccessorCertificate()
    with pytest.raises(TypeError):
        copy(certificate)
    with pytest.raises(TypeError):
        deepcopy(certificate)
    with pytest.raises(TypeError):
        pickle.dumps(certificate)
    lookalike = object.__new__(R272ValidatedSuccessorCertificate)
    _assert_code(
        replace(inputs, producer_validation_certificate=lookalike),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )


def test_foreign_replay_context_cannot_mint_r272_certificate() -> None:
    with pytest.raises(RestContourValidationError):
        _inputs(foreign_replay_context=True)


def test_disabled_setup_cannot_mint_or_launder_r272_certificate() -> None:
    with pytest.raises(RestContourValidationError):
        _inputs(authoritative_setup_enabled=False)


def test_r272_certificate_rejects_cancellation_capability_substitution() -> None:
    first = lambda: False
    second = lambda: False
    inputs = _inputs(cancellation=first)
    _assert_code(
        replace(inputs, cancellation=second),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )


def test_schema_invalid_feed_only_producer_cannot_mint_certificate() -> None:
    with pytest.raises(RestContourValidationError):
        _inputs(producer_feed_schema_invalid=True)


@pytest.mark.parametrize(
    "field",
    (
        "status",
        "publication_fingerprint",
        "parent_state_fingerprint",
        "parent_state_content_seal",
    ),
)
def test_upstream_producer_completion_tamper_is_stale(field: str) -> None:
    inputs = _inputs()
    completion = inputs.producer_completion
    if field == "status":
        changed = "CORRUPT"
    else:
        changed = ContentFingerprint.from_payload({"tampered_field": field})
    object.__setattr__(completion, field, changed)
    _assert_code(inputs, RestFinishingDiagnosticCode.MATERIAL_STATE_STALE)


@pytest.mark.parametrize(
    "field",
    ("parent_state_fingerprint", "parent_state_content_seal"),
)
def test_coherently_resealed_parent_provenance_is_stale(field: str) -> None:
    inputs = _inputs()
    completion = inputs.producer_completion
    object.__setattr__(
        completion,
        field,
        ContentFingerprint.from_payload({"coherent_forge": field}),
    )
    object.__setattr__(
        completion,
        "publication_fingerprint",
        ContentFingerprint.from_payload(completion._fingerprint_payload()),
    )
    _assert_code(inputs, RestFinishingDiagnosticCode.MATERIAL_STATE_STALE)


def test_changed_cut_depth_and_recomputed_caller_witness_cannot_replace_certificate() -> None:
    inputs = _inputs()
    candidate = inputs.material_candidates[0]
    producer = inputs.setup.operation_tree.get_operation(
        candidate.producer_operation_id
    )
    values = tuple(
        # R272's serialized contour schema names its cutting-depth target
        # ``final_depth``; this is the concrete cut-depth authority field.
        (name, 3.0 if name == "final_depth" else value)
        for name, value in producer.parameters.values
    )
    assert values != producer.parameters.values
    changed = replace(
        producer,
        parameters=replace(producer.parameters, values=values),
    )
    caller_computable_witness = (
        finishing_geometry._material_removal_operation_fingerprint(changed)
    )
    assert caller_computable_witness != (
        finishing_geometry._material_removal_operation_fingerprint(producer)
    )
    setup = replace(
        inputs.setup,
        operation_tree=inputs.setup.operation_tree.replace_operation(changed),
    )
    _assert_code(
        replace(inputs, setup=setup),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )


@pytest.mark.parametrize("authority_name", ("tool", "assembly", "machine"))
def test_post_mint_producer_authority_content_drift_is_stale(
    authority_name: str,
) -> None:
    inputs = _inputs()
    validation_candidate = _R272_MINT_BUNDLES[
        inputs.producer_validation_certificate
    ][1]
    authority = validation_candidate.prepared.plan.authority
    if authority_name == "tool":
        tool = authority.tool
        geometry = tool.cutting_geometry
        object.__setattr__(
            tool,
            "cutting_geometry",
            replace(
                geometry,
                diameter=Length(geometry.diameter.value + 0.125, geometry.diameter.unit),
            ),
        )
    elif authority_name == "assembly":
        assembly = authority.tool_assembly
        object.__setattr__(
            assembly,
            "stickout",
            Length(assembly.stickout.value + 0.125, assembly.stickout.unit),
        )
    else:
        machine = authority.machine
        object.__setattr__(machine, "name", machine.name + " drift")
    _assert_code(inputs, RestFinishingDiagnosticCode.MATERIAL_STATE_STALE)


@pytest.mark.parametrize("authority_name", ("tool", "assembly", "machine"))
def test_post_mint_same_byte_producer_authority_substitution_is_stale(
    authority_name: str,
) -> None:
    inputs = _inputs()
    validation_candidate = _R272_MINT_BUNDLES[
        inputs.producer_validation_certificate
    ][1]
    authority = validation_candidate.prepared.plan.authority
    field = {
        "tool": "tool",
        "assembly": "tool_assembly",
        "machine": "machine",
    }[authority_name]
    current = getattr(authority, field)
    object.__setattr__(authority, field, replace(current))
    _assert_code(inputs, RestFinishingDiagnosticCode.MATERIAL_STATE_STALE)


def test_post_mint_same_byte_cutting_geometry_substitution_is_stale() -> None:
    inputs = _inputs()
    validation_candidate = _R272_MINT_BUNDLES[
        inputs.producer_validation_certificate
    ][1]
    tool = validation_candidate.prepared.plan.authority.tool
    original = tool.cutting_geometry
    replacement = replace(original)
    assert replacement == original and replacement is not original
    object.__setattr__(tool, "cutting_geometry", replacement)
    _assert_code(inputs, RestFinishingDiagnosticCode.MATERIAL_STATE_STALE)


@pytest.mark.parametrize(
    "authority_path",
    (
        "candidate.artifact",
        "candidate.successor",
        "prepared.predecessor",
        "plan.center_loop",
        "authority.parameters",
        "authority.profile_path",
        "tool.shank",
        "assembly.stickout",
        "machine.capabilities",
        "setup.wcs",
        "operation.parameters",
    ),
)
def test_post_mint_nested_same_byte_authority_substitution_is_stale(
    authority_path: str,
) -> None:
    inputs = _inputs()
    candidate = _R272_MINT_BUNDLES[inputs.producer_validation_certificate][1]
    prepared = candidate.prepared
    plan = prepared.plan
    authority = plan.authority
    if authority_path == "candidate.artifact":
        object.__setattr__(candidate, "artifact", replace(candidate.artifact))
    elif authority_path == "candidate.successor":
        object.__setattr__(candidate, "successor_state", replace(candidate.successor_state))
    elif authority_path == "prepared.predecessor":
        object.__setattr__(prepared, "predecessor_state", replace(prepared.predecessor_state))
    elif authority_path == "plan.center_loop":
        object.__setattr__(plan, "center_loop", replace(plan.center_loop))
    elif authority_path == "authority.parameters":
        object.__setattr__(authority, "parameters", replace(authority.parameters))
    elif authority_path == "authority.profile_path":
        object.__setattr__(authority, "profile_path", replace(authority.profile_path))
    elif authority_path == "tool.shank":
        object.__setattr__(authority.tool, "shank", replace(authority.tool.shank))
    elif authority_path == "assembly.stickout":
        object.__setattr__(
            authority.tool_assembly,
            "stickout",
            replace(authority.tool_assembly.stickout),
        )
    elif authority_path == "machine.capabilities":
        object.__setattr__(
            authority.machine,
            "capabilities",
            replace(authority.machine.capabilities),
        )
    elif authority_path == "setup.wcs":
        object.__setattr__(inputs.setup, "wcs", replace(inputs.setup.wcs))
    else:
        operation = inputs.setup.operation_tree.get_operation(
            inputs.material_candidates[0].producer_operation_id
        )
        object.__setattr__(operation, "parameters", replace(operation.parameters))
    _assert_code(inputs, RestFinishingDiagnosticCode.MATERIAL_STATE_STALE)


def test_r272_certificate_accepts_only_proven_feed_only_producer_drift() -> None:
    inputs = _inputs(producer_feed_only=True)
    producer = inputs.setup.operation_tree.get_operation(
        inputs.material_candidates[0].producer_operation_id
    )
    assert producer.artifact_state.status.value == "dirty"
    assert producer.artifact_state.dirty_reasons == (
        DirtyReason.PARAMETERS_CHANGED,
    )
    assert isinstance(plan_rest_finishing_geometry(inputs), RestFinishingRasterPlan)
    changed = replace(
        producer,
        parameters=OperationParameterSet(
            "upstream.material",
            1,
            (("cut_depth", 987.0),),
            1,
        ),
    )
    setup = replace(
        inputs.setup,
        operation_tree=inputs.setup.operation_tree.replace_operation(changed),
    )
    _assert_code(
        replace(inputs, setup=setup),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )


def test_coherently_resealed_persisted_successor_replay_mismatch_is_stale(
    tmp_path,
) -> None:
    inputs = _inputs()
    candidate = inputs.material_candidates[0]
    store = MaterialStateStore()
    path = store.write(tmp_path, candidate.state)
    document = json.loads(path.read_text(encoding="utf-8"))
    heights = list(document["top_heights"])
    index = next(
        index
        for index, value in enumerate(heights)
        if value + 0.25 <= inputs.setup.stock.size_z.value
    )
    heights[index] += 0.25
    document["top_heights"] = heights
    document["remaining_volume"] += (
        0.25 * document["cell_size_x"] * document["cell_size_y"]
    )
    forged = replace(
        candidate.state,
        top_heights=tuple(heights),
        remaining_volume=document["remaining_volume"],
    )
    document["content_integrity_fingerprint"] = (
        forged.content_integrity_fingerprint.to_dict()
    )
    document["checksum_sha256"] = ""
    unsigned = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    document["checksum_sha256"] = hashlib.sha256(unsigned).hexdigest()
    path.write_text(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
        newline="\n",
    )
    loaded = store.load(tmp_path, candidate.state.fingerprint)
    assert loaded.status is MaterialStateLoadStatus.VALID
    assert loaded.state is not None and loaded.state.content_is_verified
    assert loaded.state.top_heights != candidate.state.top_heights
    forged_completion = MaterialStateSuccessorPublication.create(
        consumer_operation_id=inputs.producer_completion.consumer_operation_id,
        artifact_id=inputs.producer_completion.artifact_id,
        artifact_fingerprint=inputs.producer_completion.artifact_fingerprint,
        input_fingerprint=inputs.producer_completion.input_fingerprint,
        semantic_material_removal_fingerprint=(
            inputs.producer_completion.semantic_material_removal_fingerprint
        ),
        parent_state_fingerprint=inputs.producer_completion.parent_state_fingerprint,
        parent_state_content_seal=inputs.producer_completion.parent_state_content_seal,
        successor_state_fingerprint=loaded.state.fingerprint,
        successor_state_content_seal=loaded.state.content_integrity_fingerprint,
        setup_fingerprint=loaded.state.setup_fingerprint,
        stock_fingerprint=loaded.state.stock_fingerprint,
        engine_version=loaded.state.engine_version,
        precision=loaded.state.precision.to_dict(),
    )
    forged_dependency = replace(
        inputs.producer_dependency,
        successor_publication=forged_completion,
    )
    forged_candidate = replace(candidate, state=loaded.state)
    _assert_code(
        replace(
            inputs,
            material_candidates=(forged_candidate,),
            producer_completion=forged_completion,
            producer_dependency=forged_dependency,
        ),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )


def test_producer_parent_state_must_be_current_trusted_authority() -> None:
    inputs = _inputs()
    parent = inputs.producer_parent_state
    heights = list(parent.top_heights)
    heights[0] -= 0.25
    object.__setattr__(parent, "top_heights", tuple(heights))
    _assert_code(inputs, RestFinishingDiagnosticCode.MATERIAL_STATE_STALE)

    current = _inputs()
    foreign = _inputs().producer_parent_state
    _assert_code(
        replace(current, producer_parent_state=foreign),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )


def test_producer_parent_material_edge_must_be_unique() -> None:
    inputs = _inputs()
    parent = inputs.setup.operation_tree.get_operation(
        inputs.producer_dependency.producer_operation_id
    )
    foreign_id = OperationId.new()
    foreign = replace(
        parent,
        operation_id=foreign_id,
        node_id=CamNodeId.new(),
    )
    tree = inputs.setup.operation_tree.add_operation(
        inputs.setup.operation_tree.root_id,
        "Foreign parent",
        foreign,
    )
    tree = tree.with_dependency_added(
        DependencyEdge.material_state(
            foreign_id,
            inputs.material_candidates[0].producer_operation_id,
        )
    )
    _assert_code(
        replace(
            inputs,
            setup=replace(inputs.setup, operation_tree=tree),
            dependency_graph=tree.dependency_graph,
        ),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )


def test_coherently_resealed_producer_semantic_edit_is_stale() -> None:
    inputs = _inputs()
    candidate = inputs.material_candidates[0]
    producer = inputs.setup.operation_tree.get_operation(
        candidate.producer_operation_id
    )
    forged_artifact = replace(
        candidate.producer_artifact,
        operation_revision=producer.revision.next(),
        artifact_fingerprint=None,
    )
    forged_state = replace(
        producer.artifact_state,
        artifact_fingerprint=forged_artifact.artifact_fingerprint,
    )
    forged_producer = replace(
        producer,
        revision=producer.revision.next(),
        parameters=OperationParameterSet(
            "upstream.material",
            1,
            (("cut_depth", 123.0),),
            1,
        ),
        artifact_state=forged_state,
    )
    tree = inputs.setup.operation_tree.replace_operation(forged_producer)
    forged_candidate = replace(
        candidate,
        producer_artifact=forged_artifact,
    )
    _assert_code(
        replace(
            inputs,
            setup=replace(inputs.setup, operation_tree=tree),
            material_candidates=(forged_candidate,),
        ),
        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
    )
