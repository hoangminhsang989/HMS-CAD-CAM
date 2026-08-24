"""R271 Phase A residual geometry stays bound to real R270 authority."""

from __future__ import annotations

from copy import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hms_cadcam.cam.application.contour import (
    ContourPath,
    canonical_contour_start,
    offset_contour,
    resolve_profile_in_setup,
)
from hms_cadcam.cam.application.rest_contour import RestMaterialStateCandidate
from hms_cadcam.cam.application.rest_contour_geometry import (
    NoRestContourMaterial,
    RestContourGeometryInputs,
    RestContourResidualPlan,
    plan_rest_contour_residual,
)
from hms_cadcam.cam.domain import (
    ContourLoop,
    ContourOrientation,
    ContourSegment,
    ContourBounds,
    ContourSide,
    ComputationToken,
    ContentFingerprint,
    CylindricalGeometry,
    DependencyEdge,
    DependencyFingerprint,
    DependencyGraph,
    DirtyReason,
    GeometryFingerprint,
    FeedRate,
    FeedUnit,
    Length,
    MachineEvidence,
    OperationId,
    Point3,
    Revision,
    ToolDefinitionId,
    ToolpathArtifactId,
    Vector3,
)
from hms_cadcam.cam.domain.rest_contour import (
    RestContourDiagnosticCode,
    RestContourValidationError,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.material_state import (
    MaterialState,
    MaterialStateStore,
    calculate_material_state,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.material_state.core import MaterialStateVerificationOrigin
import hms_cadcam.cam.material_state.core as material_state_core
from hms_cadcam.cam.persistence.models import MaterialStateDependency
from hms_cadcam.cam.toolpath import Pose, ToolpathBuilder, compute_material_removal_fingerprint
from hms_cadcam.cam.automatic_rest_contour import (
    RestContourAutomaticContext,
    resolve_rest_contour_automatic_contract,
)

# These fixtures construct real upstream ToolpathArtifact, MaterialState,
# MaterialStateDependency and candidate evidence. R271 never accepts a detached
# fabricated MaterialState merely to make an empty geometry test convenient.
from test_rest_contour_foundation_r270 import (
    _candidate,
    _foundation,
    _inputs as _r270_inputs,
    _setup_with_operation_aggregate,
)


def _inputs(*, rest: bool = True, radial_allowance: float = 0.0) -> RestContourGeometryInputs:
    base = _r270_inputs()
    path = resolve_profile_in_setup(base.profile.descriptor, base.setup)
    preliminary = replace(
        base.parameters,
        side=base.parameters.side.INSIDE,
        radial_stock_allowance=Length(radial_allowance, base.parameters.unit),
        automatic_parameter_contract=None,
    )
    geometry = base.tool.cutting_geometry
    contract = resolve_rest_contour_automatic_contract(RestContourAutomaticContext(
        preliminary.unit, base.tool.family, geometry.diameter.value,
        getattr(geometry, "corner_radius", None) and geometry.corner_radius.value,
        geometry.axial_cutting_length.value, base.assembly.stickout.value,
        preliminary.top_height.value - (preliminary.final_depth.value + preliminary.axial_stock_allowance.value),
        preliminary.tolerance.value, preliminary.side, True, path.loop,
        base.profile.descriptor.outer_loop, base.profile.descriptor.geometry_fingerprint.digest,
        base.tool.content_fingerprint.digest,
    ))
    parameters = replace(
        preliminary,
        stepdown=Length(float(contract.value("stepdown").effective_value), preliminary.unit),
        lead_in_length=Length(float(contract.value("lead_in_length").effective_value), preliminary.unit),
        lead_out_length=Length(float(contract.value("lead_out_length").effective_value), preliminary.unit),
        automatic_parameter_contract=contract.to_json(),
    )
    candidate, _graph = _candidate(
        base.setup, base.consumer_operation_id, base.tool, base.assembly, base.machine, rest=rest,
    )
    setup = _setup_with_operation_aggregate(
        base.setup, base.assembly, base.consumer_operation_id, parameters,
        base.profile, _graph, base.machine_requirement, (candidate,),
    )
    foundation_inputs = replace(base, setup=setup, parameters=parameters,
                                material_candidates=(candidate,), dependency_graph=_graph)
    foundation = _foundation(foundation_inputs).resolve(foundation_inputs)
    return RestContourGeometryInputs(
        foundation, base.profile.descriptor, setup.stock, setup,
        candidate.state.setup_fingerprint, parameters, base.tool, base.assembly,
        base.assembly_evidence, base.machine,
        MachineEvidence(True, base.machine.revision, base.machine.content_fingerprint,
                        base.machine.unit, base.machine.capabilities.operations),
    )


def _positive_inputs(
    *,
    multiple_depths: bool = False,
    profile_points: tuple[tuple[float, float], ...] = ((10, 10), (70, 10), (70, 70), (10, 70)),
    producer_gaps: tuple[tuple[int, float, float], ...] = ((1, 0.28, 0.72),),
    rest_diameter: float = 6.0,
    setup_revision: int = 0,
    base_inputs: object | None = None,
) -> RestContourGeometryInputs:
    """Build real R270 evidence from a Ø10 producer and a configurable Rest cutter.

    ``producer_gaps`` are deliberately expressed in the final consumer center
    loop coordinates.  The upstream artifact is still a genuine
    ``ToolpathArtifact`` and its heightfield is always recalculated; this lets
    R271 exercise disconnected, concave and notch residuals without detached
    MaterialState test doubles.
    """
    base = _r270_inputs() if base_inputs is None else base_inputs
    if setup_revision:
        base = replace(base, setup=replace(base.setup, revision=Revision(setup_revision)))
    unit = base.tool.unit
    points = tuple(Point3(x, y, 0, unit) for x, y in profile_points)
    loop = ContourLoop(tuple(ContourSegment(base.profile.descriptor.outer_loop.segments[0].kind,
        points[index], points[(index + 1) % len(points)]) for index in range(len(points))),
        ContourOrientation.COUNTERCLOCKWISE)
    geometry = GeometryFingerprint.from_payload(loop.to_dict())
    descriptor = replace(base.profile.descriptor, outer_loop=loop, inner_loops=(), geometry_fingerprint=geometry,
        reference=replace(base.profile.descriptor.reference, expected_geometry_fingerprint=geometry),
        bounds=ContourBounds(
            Point3(min(point.x for point in points), min(point.y for point in points), 0, unit),
            Point3(max(point.x for point in points), max(point.y for point in points), 0, unit),
        ))
    profile = replace(base.profile, descriptor=descriptor)
    rest_tool = replace(base.tool, tool_id=ToolDefinitionId.new(), name="Dao Rest 6",
        cutting_geometry=CylindricalGeometry(Length(rest_diameter, unit), Length(20, unit)))
    rest_assembly = replace(base.assembly, tool_id=rest_tool.tool_id,
        expected_tool_revision=rest_tool.revision, expected_tool_fingerprint=rest_tool.content_fingerprint)
    path = resolve_profile_in_setup(descriptor, base.setup)
    preliminary = replace(base.parameters, side=base.parameters.side.INSIDE,
        top_height=Length(20, unit), final_depth=Length(2, unit),
        retract_height=Length(52, unit), clearance_height=Length(55, unit),
        automatic_parameter_contract=None)
    contract = resolve_rest_contour_automatic_contract(RestContourAutomaticContext(
        unit, rest_tool.family, rest_diameter, None, 20.0, rest_assembly.stickout.value,
        preliminary.top_height.value - preliminary.final_depth.value, preliminary.tolerance.value,
        preliminary.side, True, path.loop, descriptor.outer_loop, geometry.digest,
        rest_tool.content_fingerprint.digest,
    ))
    parameters = replace(preliminary, stepdown=Length(float(contract.value("stepdown").effective_value), unit),
        lead_in_length=Length(float(contract.value("lead_in_length").effective_value), unit),
        lead_out_length=Length(float(contract.value("lead_out_length").effective_value), unit),
        automatic_parameter_contract=contract.to_json())
    producer = OperationId.new()
    input_fingerprint = DependencyFingerprint.from_payload({"producer": str(producer), "purpose": "r271-positive"})
    builder = ToolpathBuilder(artifact_id=ToolpathArtifactId.new(), operation_id=producer,
        operation_revision=Revision(0), computation_token=ComputationToken(__import__("uuid").uuid4(), 1),
        input_fingerprint=input_fingerprint, unit=unit, setup_id=base.setup.setup_id,
        setup_revision=base.setup.revision, wcs_fingerprint=ContentFingerprint.from_payload(base.setup.wcs.to_dict()),
        tool_assembly_id=base.assembly.assembly_id, tool_assembly_fingerprint=base.assembly.content_fingerprint,
        machine_id=base.machine.machine_id, machine_fingerprint=base.machine.content_fingerprint)
    center_loop = canonical_contour_start(offset_contour(
        resolve_profile_in_setup(descriptor, base.setup).loop,
        ContourSide.INSIDE,
        rest_diameter / 2.0,
    ))
    gaps_by_segment: dict[int, tuple[tuple[float, float], ...]] = {}
    for segment_index, start, end in producer_gaps:
        gaps_by_segment[segment_index] = tuple(sorted((*gaps_by_segment.get(segment_index, ()), (start, end))))

    def point_on_segment(segment, parameter: float) -> Point3:
        return Point3(
            segment.start.x + (segment.end.x - segment.start.x) * parameter,
            segment.start.y + (segment.end.y - segment.start.y) * parameter,
            2.0,
            unit,
        )

    builder.set_initial_pose(Pose(point_on_segment(center_loop.segments[0], 0.0), Vector3(0, 0, 1)))
    for segment_index, segment in enumerate(center_loop.segments):
        cursor = 0.0
        for gap_start, gap_end in gaps_by_segment.get(segment_index, ()):
            if cursor < gap_start:
                builder.linear_to(Pose(point_on_segment(segment, gap_start), Vector3(0, 0, 1)),
                                  FeedRate(100, FeedUnit.MM_PER_MINUTE))
            if gap_end > gap_start:
                builder.rapid_to(Pose(replace(point_on_segment(segment, gap_start), z=12.0), Vector3(0, 0, 1)))
                builder.rapid_to(Pose(replace(point_on_segment(segment, gap_end), z=12.0), Vector3(0, 0, 1)))
                builder.rapid_to(Pose(point_on_segment(segment, gap_end), Vector3(0, 0, 1)))
            cursor = gap_end
        if cursor < 1.0:
            builder.linear_to(Pose(point_on_segment(segment, 1.0), Vector3(0, 0, 1)),
                              FeedRate(100, FeedUnit.MM_PER_MINUTE))
    artifact = builder.finalize()
    state = calculate_material_state(stock=base.setup.stock, artifact=artifact, tool=base.tool,
        setup_fingerprint=material_state_setup_fingerprint(base.setup)).state
    edge = DependencyEdge.material_state(producer, base.consumer_operation_id)
    dependency = MaterialStateDependency(base.consumer_operation_id, producer, state.fingerprint,
        compute_material_removal_fingerprint(artifact), state.setup_fingerprint, state.stock_fingerprint,
        state.engine_version, state.precision.to_dict())
    candidate = RestMaterialStateCandidate(producer, state, dependency, edge, artifact)
    graph = DependencyGraph((producer, base.consumer_operation_id), (edge,))
    setup = _setup_with_operation_aggregate(base.setup, rest_assembly, base.consumer_operation_id, parameters,
        profile, graph, base.machine_requirement, (candidate,), producer_assembly=base.assembly,
        producer_machine_requirement=base.machine_requirement)
    evidence = replace(base.assembly_evidence, tool_fingerprint=rest_tool.content_fingerprint)
    foundation_inputs = replace(base, setup=setup, parameters=parameters, profile=profile, tool=rest_tool,
        assembly=rest_assembly, assembly_evidence=evidence, material_candidates=(candidate,), dependency_graph=graph)
    foundation = _foundation(foundation_inputs).resolve(foundation_inputs)
    return RestContourGeometryInputs(foundation, descriptor, setup.stock, setup, state.setup_fingerprint,
        parameters, rest_tool, rest_assembly, evidence, base.machine,
        MachineEvidence(True, base.machine.revision, base.machine.content_fingerprint, unit,
                        base.machine.capabilities.operations))


def _assert_code(inputs: RestContourGeometryInputs, code: RestContourDiagnosticCode) -> None:
    with pytest.raises(RestContourValidationError) as captured:
        plan_rest_contour_residual(inputs)
    assert captured.value.code is code


def test_real_r270_authority_generates_self_contained_plan_with_exact_fragments() -> None:
    inputs = _inputs()
    first = plan_rest_contour_residual(inputs)
    second = plan_rest_contour_residual(inputs)
    assert isinstance(first, RestContourResidualPlan)
    assert first.fingerprint == second.fingerprint
    assert first.center_loop.closed
    assert first.profile_path_fingerprint == inputs.foundation.profile.source_fingerprint  # type: ignore[union-attr]
    assert first.authority.parameters == inputs.parameters
    assert first.authority.profile_path == inputs.foundation.profile
    assert first.authority.tool == inputs.tool
    assert first.authority.machine == inputs.machine
    assert all(layer.region_fragments for layer in first.layers)
    for layer in first.layers:
        assert set(layer.eligible_cells) == set().union(*(set(bundle.cells) for bundle in layer.region_fragments))
        for fragment in layer.fragments:
            source = first.center_loop.segments[fragment.segment_index]
            assert fragment.segment_start == source.start
            assert fragment.segment_end == source.end
            assert fragment.responsible_cells


def test_no_rest_is_typed_only_from_real_no_rest_foundation_candidate() -> None:
    result = plan_rest_contour_residual(_inputs(rest=False))
    assert isinstance(result, NoRestContourMaterial)


def test_detached_inside_stock_path_cannot_replace_foundation_profile() -> None:
    inputs = _inputs()
    path = inputs.foundation.profile
    assert path is not None
    shifted = tuple(replace(segment, start=replace(segment.start, x=segment.start.x + 5.0),
                            end=replace(segment.end, x=segment.end.x + 5.0))
                    for segment in path.loop.segments)
    forged = replace(inputs.foundation, profile=ContourPath(ContourLoop(shifted, ContourOrientation.COUNTERCLOCKWISE),
                                                             ContentFingerprint.from_payload({"forged": "inside-stock"})))
    _assert_code(replace(inputs, foundation=forged), RestContourDiagnosticCode.PROFILE_INVALID)


@pytest.mark.parametrize("rest", (True, False))
def test_wholly_outside_stock_profile_rejects_before_empty_outcome(rest: bool) -> None:
    inputs = _inputs(rest=rest)
    descriptor = inputs.profile_descriptor
    outside_loop = ContourLoop(tuple(
        replace(segment, start=replace(segment.start, x=segment.start.x + 200.0),
                end=replace(segment.end, x=segment.end.x + 200.0))
        for segment in descriptor.outer_loop.segments
    ), descriptor.outer_loop.orientation)
    geometry = GeometryFingerprint.from_payload(outside_loop.to_dict())
    outside_descriptor = replace(
        descriptor, outer_loop=outside_loop, geometry_fingerprint=geometry,
        reference=replace(descriptor.reference, expected_geometry_fingerprint=geometry),
    )
    outside_path = resolve_profile_in_setup(outside_descriptor, inputs.setup)
    foundation = replace(inputs.foundation, profile=outside_path)
    _assert_code(replace(inputs, foundation=foundation, profile_descriptor=outside_descriptor),
                 RestContourDiagnosticCode.PATH_OUTSIDE_AUTHORITY)


@pytest.mark.parametrize("field, change", (
    ("engine_version", lambda state: "other-engine"),
    ("cell_size_x", lambda state: state.cell_size_x / 2.0),
    ("cell_size_y", lambda state: state.cell_size_y * 2.0),
))
def test_current_material_engine_and_grid_authority_reject_before_no_rest(field: str, change) -> None:
    inputs = _inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    object.__setattr__(candidate.state, field, change(candidate.state))
    _assert_code(inputs, RestContourDiagnosticCode.MATERIAL_STATE_INVALID)


def test_zero_radial_allowance_permits_tangent_cutter_containment() -> None:
    result = plan_rest_contour_residual(_inputs(radial_allowance=0.0))
    assert isinstance(result, RestContourResidualPlan)


def test_equal_diameter_with_real_producer_gap_is_residual_geometry_not_a_diameter_shortcut() -> None:
    """Ø10-to-Ø10 still has rest authority where the real producer skipped."""
    inputs = _positive_inputs(rest_diameter=10.0, producer_gaps=((1, 0.28, 0.72),))
    result = plan_rest_contour_residual(inputs)
    assert isinstance(result, RestContourResidualPlan)
    assert result.layers
    assert all(layer.fragments for layer in result.layers)


def test_radial_allowance_is_identity_bearing() -> None:
    zero = plan_rest_contour_residual(_inputs(radial_allowance=0.0))
    allowed = plan_rest_contour_residual(_inputs(radial_allowance=0.1))
    assert isinstance(zero, RestContourResidualPlan)
    assert isinstance(allowed, RestContourResidualPlan)
    assert zero.center_loop != allowed.center_loop
    assert zero.fingerprint != allowed.fingerprint


def test_forged_heightfield_or_false_no_rest_is_never_accepted() -> None:
    inputs = _inputs(rest=False)
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    forged = list(candidate.state.top_heights)
    forged[0] = forged[0] + 1.0
    object.__setattr__(candidate.state, "top_heights", tuple(forged))
    _assert_code(inputs, RestContourDiagnosticCode.MATERIAL_STATE_INVALID)


def test_direct_constructor_or_replace_is_untrusted_and_cannot_self_seal() -> None:
    inputs = _inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    detached = replace(candidate.state, remaining_volume=candidate.state.remaining_volume)
    assert detached.content_integrity_fingerprint == candidate.state.content_integrity_fingerprint
    assert detached.verification_origin is MaterialStateVerificationOrigin.UNVERIFIED
    _assert_code(replace(inputs, foundation=replace(
        inputs.foundation,
        material=replace(inputs.foundation.material, candidate=replace(candidate, state=detached)),
    )), RestContourDiagnosticCode.MATERIAL_STATE_INVALID)


def test_trust_registry_rejects_copy_token_splice_and_mutation_for_calculated_and_persisted_state(tmp_path) -> None:
    """Trust belongs to one exact minted object, never its public bytes alone."""
    inputs = _inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    calculated = candidate.state
    assert calculated.content_is_verified
    assert calculated.verification_origin is MaterialStateVerificationOrigin.TRUSTED_CALCULATED
    assert not hasattr(MaterialState, "_trust_calculated")
    assert not hasattr(MaterialState, "_trust_persisted")
    assert not hasattr(MaterialState, "_promote_verified")

    detached = replace(calculated)
    copied = copy(calculated)
    object.__setattr__(detached, "_trust_token", calculated._trust_token)
    for forged in (detached, copied):
        assert not forged.content_is_verified
        assert forged.verification_origin is MaterialStateVerificationOrigin.UNVERIFIED
        with pytest.raises(CamValidationError):
            calculate_material_state(stock=inputs.setup.stock, artifact=candidate.producer_artifact,
                                     tool=inputs.tool, parent=forged,
                                     setup_fingerprint=material_state_setup_fingerprint(inputs.setup))
        with pytest.raises(CamValidationError):
            MaterialStateStore().write(tmp_path, forged)

    original_engine = calculated.engine_version
    object.__setattr__(calculated, "engine_version", "forged")
    assert not calculated.content_is_verified
    object.__setattr__(calculated, "engine_version", original_engine)
    assert calculated.content_is_verified

    store = MaterialStateStore()
    store.write(tmp_path, calculated)
    persisted = store.load(tmp_path, calculated.fingerprint).state
    assert persisted is not None and persisted.content_is_verified
    assert persisted.verification_origin is MaterialStateVerificationOrigin.TRUSTED_PERSISTED
    persisted_copy = copy(persisted)
    object.__setattr__(persisted_copy, "_trust_token", persisted._trust_token)
    assert not persisted_copy.content_is_verified
    object.__setattr__(persisted, "remaining_volume", persisted.remaining_volume + 0.01)
    assert not persisted.content_is_verified


def test_material_state_trust_ingresses_capture_real_class_before_module_monkeypatch(tmp_path, monkeypatch) -> None:
    """Neither calculated nor persisted ingress may promote a patched-class forgery."""
    inputs = _inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    calculated = candidate.state
    forged = replace(calculated)
    assert not forged.content_is_verified
    store = MaterialStateStore()
    document = store.write(tmp_path, calculated).read_bytes()

    monkeypatch.setattr(material_state_core, "MaterialState", lambda *args: forged)
    recalculated = material_state_core.calculate_material_state(
        stock=inputs.setup.stock, artifact=candidate.producer_artifact, tool=inputs.tool,
        setup_fingerprint=material_state_setup_fingerprint(inputs.setup),
    ).state
    decoded = material_state_core.material_state_from_persisted_bytes(document, calculated.fingerprint)

    assert recalculated is not forged and recalculated.content_is_verified
    assert decoded is not forged and decoded.content_is_verified
    with pytest.raises(CamValidationError):
        store.write(tmp_path, forged)


def _rewrite_checksum(document: dict[str, object]) -> bytes:
    document["checksum_sha256"] = ""
    unsigned = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    document["checksum_sha256"] = hashlib.sha256(unsigned).hexdigest()
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_legacy_checksum_state(tmp_path, state):
    store = MaterialStateStore()
    target = store.write(tmp_path, state)
    loaded = store.load(tmp_path, state.fingerprint)
    assert loaded.state is not None
    assert loaded.state.verification_origin is MaterialStateVerificationOrigin.TRUSTED_PERSISTED
    target.write_bytes(_rewrite_checksum({key: value for key, value in loaded.state.to_dict().items()
                                          if key != "content_integrity_fingerprint"}))
    legacy = store.load(tmp_path, state.fingerprint)
    assert legacy.state is not None
    assert legacy.state.verification_origin is MaterialStateVerificationOrigin.VERIFIED_LEGACY_CHECKSUM
    return store, target, loaded.state, legacy.state


def test_persisted_and_legacy_checksum_origins_are_verified_but_forged_seal_is_corrupt(tmp_path) -> None:
    inputs = _inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    store, target, loaded_state, legacy_state = _load_legacy_checksum_state(tmp_path, candidate.state)
    target = store.write(tmp_path, loaded_state)
    document = json.loads(target.read_text(encoding="utf-8"))
    document["top_heights"][0] = document["top_heights"][0] + 1.0
    target.write_bytes(_rewrite_checksum(document))
    assert store.load(tmp_path, candidate.state.fingerprint).status.value == "CORRUPT"
    assert loaded_state.content_integrity_fingerprint == legacy_state.content_integrity_fingerprint


def test_safe_persisted_decoder_reestablishes_trust_in_a_fresh_subprocess(tmp_path) -> None:
    """A new interpreter derives trust from bytes, never an inherited token."""
    inputs = _inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    state = candidate.state
    MaterialStateStore().write(tmp_path, state)
    script = (
        "from pathlib import Path; import sys; "
        "from hms_cadcam.cam.domain import ContentFingerprint; "
        "from hms_cadcam.cam.material_state import MaterialStateStore; "
        "value=MaterialStateStore().load(Path(sys.argv[1]), "
        "ContentFingerprint('sha256', 1, sys.argv[2])).state; "
        "print(value.verification_origin.value); print(value.content_is_verified)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), state.fingerprint.digest],
        cwd=Path.cwd(), capture_output=True, text=True, timeout=30, check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")
             + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ["trusted_persisted", "True"]


@pytest.mark.parametrize("rest", (True, False))
def test_legacy_checksum_state_remains_readable_but_phase_a_rejects_it(tmp_path, rest: bool) -> None:
    inputs = _inputs(rest=rest)
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    store, _target, _persisted, legacy = _load_legacy_checksum_state(tmp_path, candidate.state)
    assert store.load(tmp_path, candidate.state.fingerprint).state is not None
    foundation = replace(inputs.foundation, material=replace(
        inputs.foundation.material, candidate=replace(candidate, state=legacy),
    ))
    _assert_code(replace(inputs, foundation=foundation), RestContourDiagnosticCode.MATERIAL_STATE_INVALID)


def test_material_content_seal_is_identity_bearing_without_changing_semantic_state_identity() -> None:
    inputs = _inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    changed = replace(candidate.state, top_heights=tuple(
        value + (0.1 if index == 0 else 0.0) for index, value in enumerate(candidate.state.top_heights)
    ))
    assert changed.fingerprint == candidate.state.fingerprint
    assert changed.content_integrity_fingerprint != candidate.state.content_integrity_fingerprint


def _with_tree(inputs: RestContourGeometryInputs, tree) -> RestContourGeometryInputs:
    return replace(inputs, setup=replace(inputs.setup, operation_tree=tree))


def test_detached_foundation_result_is_rejected_against_current_aggregate_edge() -> None:
    inputs = _inputs()
    assert inputs.foundation.dependency_edge is not None
    _assert_code(replace(inputs, foundation=replace(inputs.foundation, dependency_edge=None)),
                 RestContourDiagnosticCode.MATERIAL_STATE_INVALID)


def test_missing_disabled_or_dirty_producer_never_reaches_geometry() -> None:
    inputs = _inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    tree = inputs.setup.operation_tree
    producer = tree.get_operation(candidate.producer_operation_id)
    _assert_code(_with_tree(inputs, tree.remove_node(producer.node_id)),
                 RestContourDiagnosticCode.MATERIAL_STATE_INVALID)
    _assert_code(_with_tree(inputs, tree.set_enabled(producer.node_id, False)),
                 RestContourDiagnosticCode.MATERIAL_STATE_INVALID)
    dirty = replace(producer, artifact_state=producer.artifact_state.mark_dirty(DirtyReason.UPSTREAM_CHANGED))
    _assert_code(_with_tree(inputs, tree.replace_operation(dirty)),
                 RestContourDiagnosticCode.MATERIAL_STATE_INVALID)


def test_missing_current_dependency_edge_is_rejected() -> None:
    inputs = _inputs()
    tree = inputs.setup.operation_tree
    _assert_code(_with_tree(inputs, replace(
        tree, dependency_graph=DependencyGraph(tree.dependency_graph.operation_ids, ()),
    )), RestContourDiagnosticCode.MATERIAL_STATE_INVALID)


def test_consumer_parameter_tool_and_machine_replacements_are_not_authority() -> None:
    inputs = _inputs()
    _assert_code(replace(inputs, parameters=replace(
        inputs.parameters, radial_stock_allowance=Length(0.1, inputs.parameters.unit),
    )), RestContourDiagnosticCode.INVALID_PARAMETERS)
    _assert_code(replace(inputs, tool=replace(inputs.tool, tool_id=ToolDefinitionId.new())),
                 RestContourDiagnosticCode.TOOL_INELIGIBLE)
    _assert_code(replace(inputs, machine=replace(inputs.machine, name="foreign same-capability mill")),
                 RestContourDiagnosticCode.MACHINE_INCOMPATIBLE)


def test_cancellation_is_typed_before_any_empty_result() -> None:
    _assert_code(replace(_inputs(), cancellation=lambda: True), RestContourDiagnosticCode.CANCELLED)
