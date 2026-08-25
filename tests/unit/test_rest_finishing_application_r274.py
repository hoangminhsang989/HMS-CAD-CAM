"""R274 durable-boundary contracts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from hms_cadcam.cam.application.rest_finishing_geometry import (
    RestFinishingRasterPlan,
    plan_rest_finishing_geometry,
)
from hms_cadcam.cam.application.rest_finishing_application import (
    RestFinishingApplicationStatus,
    publish_rest_finishing_candidate,
    rest_finishing_application_failure,
    rest_finishing_application_result,
)
from hms_cadcam.cam.application.rest_finishing_lifecycle import (
    RestFinishingLifecycleContext,
    RestFinishingLifecycleStatus,
    generate_rest_finishing_3axis,
    prepare_rest_finishing_3axis,
)
from hms_cadcam.cam.application.rest_finishing_toolpath import (
    generate_rest_finishing_toolpath,
    prepare_rest_finishing_toolpath,
)
from hms_cadcam.cam.application.rest_contour_toolpath import (
    _project_r272_producer_authority_setup,
    mint_r272_validated_successor_certificate,
)
from hms_cadcam.cam.application.service import (
    CamApplicationService,
    _material_removal_operation_fingerprint,
)
from hms_cadcam.cam.domain import CamJob, CamJobId, ToolpathArtifactId
from hms_cadcam.cam.domain.rest_finishing import (
    REST_FINISHING_PARAMETER_SCHEMA_VERSION,
    REST_FINISHING_STRATEGY_KEY,
    REST_FINISHING_STRATEGY_VERSION,
    RestFinishingDiagnosticCode,
    RestFinishingValidationError,
)
from hms_cadcam.cam.material_state import MaterialStateLoadStatus, MaterialStateStore
from hms_cadcam.cam.persistence.models import (
    CamProjectSnapshot,
    MaterialStateSuccessorPublication,
)
from hms_cadcam.cam.persistence.artifact_store import ToolpathArtifactStore
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.toolpath import compute_material_removal_fingerprint
from hms_cadcam.cam.operation_registry import default_rest_contour_operation_registry
from test_rest_contour_core_r271 import _positive_inputs
from test_rest_finishing_core_r273 import _inputs as _r273_inputs


@pytest.fixture(scope="module")
def r273_inputs():
    """Mint one genuine R272 capability over a full downstream Setup."""
    return _r273_inputs()


@pytest.fixture(scope="module")
def r273_candidate(r273_inputs):
    """Generate one sealed R273 candidate for R274 store-boundary tests."""
    plan = plan_rest_finishing_geometry(r273_inputs)
    assert isinstance(plan, RestFinishingRasterPlan)
    prepared = prepare_rest_finishing_toolpath(r273_inputs, plan)
    return generate_rest_finishing_toolpath(prepared)


def test_registry_discovers_and_roundtrips_typed_rest_finishing(r273_inputs) -> None:
    registry = default_rest_contour_operation_registry()
    assert registry.is_registered(
        REST_FINISHING_STRATEGY_KEY,
        REST_FINISHING_STRATEGY_VERSION,
        REST_FINISHING_PARAMETER_SCHEMA_VERSION,
    )
    registered = {
        (item.strategy_key, item.strategy_version, item.parameter_schema_version)
        for item in registry.registered_types
    }
    assert (
        REST_FINISHING_STRATEGY_KEY,
        REST_FINISHING_STRATEGY_VERSION,
        REST_FINISHING_PARAMETER_SCHEMA_VERSION,
    ) in registered
    operation = r273_inputs.setup.operation_tree.get_operation(
        r273_inputs.consumer_operation_id
    )
    assert registry.decode(operation.to_dict()) == operation


def test_application_taxonomy_keeps_cancellation_distinct_from_failure() -> None:
    cancelled = rest_finishing_application_failure(
        RestFinishingDiagnosticCode.CANCELLED,
        "cancelled at the final pre-commit poll",
    )
    failed = rest_finishing_application_failure(
        RestFinishingDiagnosticCode.SUCCESSOR_INVALID,
        "invalid successor",
    )
    assert cancelled.status is RestFinishingApplicationStatus.CANCELLED
    assert failed.status is RestFinishingApplicationStatus.FAILURE


def test_application_no_work_taxonomy_has_zero_durable_output() -> None:
    inputs = _r273_inputs(complete=True)
    context = RestFinishingLifecycleContext(
        inputs.setup,
        inputs.parameters,
        inputs.profile_selection,
        inputs.material_candidates,
        inputs.producer_completion,
        inputs.producer_dependency,
        inputs.producer_parent_state,
        inputs.producer_validation_certificate,
        inputs.dependency_graph,
        inputs.assembly,
        inputs.assembly_evidence,
        inputs.tool,
        inputs.machine,
        inputs.machine_requirement,
        inputs.machine_evidence,
        inputs.consumer_operation_id,
        inputs.profile_resolver,
        inputs.cancellation,
    )
    preparation = prepare_rest_finishing_3axis(context)
    assert preparation.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL
    result = rest_finishing_application_result(
        generate_rest_finishing_3axis(preparation)
    )
    assert result.status is RestFinishingApplicationStatus.NO_WORK
    assert result.candidate is None
    assert result.publication is None


def test_registry_rejects_auto_laundering_on_reopen(r273_inputs) -> None:
    registry = default_rest_contour_operation_registry()
    operation = r273_inputs.setup.operation_tree.get_operation(
        r273_inputs.consumer_operation_id
    )
    payload = operation.to_dict()
    payload["parameters"]["values"].append({"name": "mode", "value": "AUTO"})
    with pytest.raises(RestFinishingValidationError) as captured:
        registry.decode(payload)
    assert captured.value.code is RestFinishingDiagnosticCode.AUTOMATIC_FORBIDDEN


def test_files_first_state_failure_leaves_only_non_authoritative_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    r273_candidate,
) -> None:
    artifact_store = ToolpathArtifactStore()
    state_store = MaterialStateStore()

    def fail_state_write(_project_root, _state):
        raise OSError("injected successor staging failure")

    monkeypatch.setattr(state_store, "write", fail_state_write)
    with pytest.raises(RestFinishingValidationError) as captured:
        publish_rest_finishing_candidate(
            r273_candidate,
            project_root=tmp_path,
            artifact_store=artifact_store,
            material_state_store=state_store,
        )
    assert captured.value.code is RestFinishingDiagnosticCode.SUCCESSOR_INVALID
    assert list((tmp_path / "toolpaths").glob("*.toolpath.json"))
    assert not list(
        (tmp_path / ".hms" / "cam" / "material_state").glob("*.state.json")
    )


def test_cancellation_before_files_creates_no_durable_output(tmp_path: Path) -> None:
    armed = False

    def cancellation() -> bool:
        return armed

    inputs = _r273_inputs(cancellation=cancellation)
    plan = plan_rest_finishing_geometry(inputs)
    assert isinstance(plan, RestFinishingRasterPlan)
    candidate = generate_rest_finishing_toolpath(
        prepare_rest_finishing_toolpath(inputs, plan),
        cancellation=cancellation,
    )
    armed = True
    with pytest.raises(RestFinishingValidationError) as captured:
        publish_rest_finishing_candidate(
            candidate,
            project_root=tmp_path,
            artifact_store=ToolpathArtifactStore(),
            material_state_store=MaterialStateStore(),
            cancellation=cancellation,
        )
    assert captured.value.code is RestFinishingDiagnosticCode.CANCELLED
    assert not (tmp_path / "toolpaths").exists()
    assert not (tmp_path / ".hms" / "cam" / "material_state").exists()


def test_artifact_store_refuses_different_bytes_for_same_immutable_identity(tmp_path: Path) -> None:
    """Collision is fail-closed; a published artifact cannot be overwritten."""
    from hms_cadcam.cam.application.rest_contour_toolpath import (
        RestContourPhaseBExecutionContext, generate_rest_contour_phase_b,
        prepare_rest_contour_phase_b,
    )
    from hms_cadcam.cam.application.rest_contour_geometry import plan_rest_contour_residual
    inputs = _positive_inputs()
    prepared = prepare_rest_contour_phase_b(
        RestContourPhaseBExecutionContext(inputs, plan_rest_contour_residual(inputs))
    )
    artifact = generate_rest_contour_phase_b(prepared).artifact
    store = ToolpathArtifactStore()
    store.publish(tmp_path, artifact)
    changed = artifact.create(
        artifact_id=artifact.artifact_id,
        source_operation_id=artifact.source_operation_id,
        operation_revision=artifact.operation_revision,
        computation_token=artifact.computation_token,
        input_fingerprint=artifact.input_fingerprint,
        coordinate_space=artifact.coordinate_space,
        unit=artifact.unit,
        setup_id=artifact.setup_id,
        setup_revision=artifact.setup_revision,
        wcs_fingerprint=artifact.wcs_fingerprint,
        tool_assembly_id=artifact.tool_assembly_id,
        tool_assembly_fingerprint=artifact.tool_assembly_fingerprint,
        machine_id=artifact.machine_id,
        machine_fingerprint=artifact.machine_fingerprint,
        initial_pose=artifact.initial_pose,
        events=artifact.events[:-1],
        diagnostics=artifact.diagnostics,
        completion_status=artifact.completion_status,
        created_at=artifact.created_at,
    )
    with pytest.raises(ToolpathArtifactStoreError, match="collision"):
        store.publish(tmp_path, changed)


def test_r272_minter_owns_projection_and_consumes_the_full_setup(r273_inputs) -> None:
    """There is one Setup input; callers cannot supply a hand-pruned authority."""
    assert tuple(inspect.signature(mint_r272_validated_successor_certificate).parameters) == (
        "replay_context",
        "validation_candidate",
        "authoritative_setup",
        "authoritative_producer_operation",
        "exact_producer_artifact",
        "trusted_parent_state",
        "supplied_successor_state",
        "producer_completion",
        "producer_dependency",
        "cancellation",
    )
    producer_id = r273_inputs.material_candidates[0].producer_operation_id
    full_operation_ids = {
        operation.operation_id
        for operation in r273_inputs.setup.operation_tree.operations
    }
    assert r273_inputs.consumer_operation_id in full_operation_ids
    projected = _project_r272_producer_authority_setup(
        r273_inputs.setup,
        producer_id,
    )
    projected_operation_ids = {
        operation.operation_id
        for operation in projected.operation_tree.operations
    }
    assert producer_id in projected_operation_ids
    assert r273_inputs.producer_dependency.producer_operation_id in projected_operation_ids
    assert r273_inputs.consumer_operation_id not in projected_operation_ids
    # Geometry planning consumes the certificate against the original full
    # Setup, forcing the require boundary to derive and seal the same projection.
    assert isinstance(plan_rest_finishing_geometry(r273_inputs), RestFinishingRasterPlan)


def test_r272_certificate_rejects_full_setup_producer_and_ancestor_mutation(
    r273_inputs,
) -> None:
    producer_id = r273_inputs.material_candidates[0].producer_operation_id
    ancestor_id = r273_inputs.producer_dependency.producer_operation_id
    for operation_id in (producer_id, ancestor_id):
        operation = r273_inputs.setup.operation_tree.get_operation(operation_id)
        changed = replace(operation, enabled=not operation.enabled)
        changed_tree = r273_inputs.setup.operation_tree.replace_operation(changed)
        changed_setup = replace(r273_inputs.setup, operation_tree=changed_tree)
        with pytest.raises(RestFinishingValidationError) as captured:
            plan_rest_finishing_geometry(
                replace(
                    r273_inputs,
                    setup=changed_setup,
                    dependency_graph=changed_tree.dependency_graph,
                )
            )
        assert captured.value.code in {
            RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
            RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
        }


def test_rest_finishing_invalidation_preserves_input_dependency_only(
    tmp_path: Path,
    r273_inputs,
) -> None:
    candidate = r273_inputs.material_candidates[0]
    producer = r273_inputs.setup.operation_tree.get_operation(
        candidate.producer_operation_id
    )
    producer_metadata = ToolpathArtifactStore().publish(
        tmp_path,
        candidate.producer_artifact,
    )
    output_artifact_id = ToolpathArtifactId.new()
    output_metadata = replace(
        producer_metadata,
        artifact_id=output_artifact_id,
        operation_id=r273_inputs.consumer_operation_id,
        relative_path=f"toolpaths/{output_artifact_id.value.hex}.toolpath.json",
    )
    output = MaterialStateSuccessorPublication.create(
        consumer_operation_id=r273_inputs.consumer_operation_id,
        artifact_id=output_artifact_id,
        artifact_fingerprint=output_metadata.artifact_fingerprint,
        input_fingerprint=output_metadata.input_fingerprint,
        semantic_material_removal_fingerprint=compute_material_removal_fingerprint(
            candidate.producer_artifact
        ),
        parent_state_fingerprint=candidate.dependency.parent_state_fingerprint,
        parent_state_content_seal=candidate.state.content_integrity_fingerprint,
        successor_state_fingerprint=candidate.state.fingerprint,
        successor_state_content_seal=candidate.state.content_integrity_fingerprint,
        setup_fingerprint=candidate.dependency.setup_fingerprint,
        stock_fingerprint=candidate.dependency.stock_fingerprint,
        engine_version=candidate.dependency.engine_version,
        precision=candidate.dependency.precision,
    )
    finishing_dependency = replace(
        candidate.dependency,
        successor_publication=output,
        producer_operation_authority_fingerprint=(
            _material_removal_operation_fingerprint(producer)
        ),
    )
    producer_authority = r273_inputs.producer_dependency
    validation_candidate = r273_inputs.producer_validation_certificate
    # The producer's assembly/tool are distinct from the finishing cutter.
    # Reach them through the genuine R272 candidate retained by the certificate
    # input objects rather than fabricating parallel authority.
    producer_assembly = candidate.producer_artifact.tool_assembly_id
    assemblies = (r273_inputs.assembly,)
    tools = (r273_inputs.tool,)
    if r273_inputs.assembly.assembly_id != producer_assembly:
        from test_rest_finishing_core_r273 import _R272_MINT_BUNDLES

        r272_candidate = _R272_MINT_BUNDLES[validation_candidate][1]
        authority = r272_candidate.prepared.plan.authority
        assemblies = (authority.tool_assembly, r273_inputs.assembly)
        tools = (authority.tool, r273_inputs.tool)
    job = CamJob(CamJobId.new(), "R274 invalidation", setups=(r273_inputs.setup,))
    snapshot = CamProjectSnapshot(
        jobs=(job,),
        active_job_id=job.job_id,
        tool_definitions=tools,
        tool_assemblies=assemblies,
        machine_definitions=(r273_inputs.machine,),
        artifacts=(producer_metadata, output_metadata),
        material_state_dependencies=(producer_authority, finishing_dependency),
    )
    service = CamApplicationService()
    service.load(snapshot)

    def disable_consumer(current: CamProjectSnapshot) -> CamProjectSnapshot:
        setup = current.jobs[0].setups[0]
        consumer = setup.operation_tree.get_operation(r273_inputs.consumer_operation_id)
        changed_tree = setup.operation_tree.replace_operation(
            replace(consumer, enabled=False)
        )
        changed_job = CamJob.from_dict(current.jobs[0].to_dict())
        changed_job.replace_setup(replace(setup, operation_tree=changed_tree))
        return replace(current, jobs=(changed_job,))

    changed = service.apply(disable_consumer)
    retained = next(
        dependency
        for dependency in changed.material_state_dependencies
        if dependency.consumer_operation_id == r273_inputs.consumer_operation_id
    )
    assert retained.producer_operation_id == candidate.producer_operation_id
    assert retained.parent_state_fingerprint == candidate.dependency.parent_state_fingerprint
    assert retained.successor_publication is None
    assert producer_metadata in changed.artifacts
    assert all(
        metadata.operation_id != r273_inputs.consumer_operation_id
        for metadata in changed.artifacts
    )


def test_material_state_store_refuses_different_bytes_for_same_fingerprint(
    tmp_path: Path,
    r273_inputs,
) -> None:
    state = r273_inputs.material_candidates[0].state
    authority_root = tmp_path / "authority"
    forged_root = tmp_path / "forged"
    authority_root.mkdir()
    forged_root.mkdir()
    store = MaterialStateStore()
    store.write(authority_root, state)
    forged_path = store.write(forged_root, state)
    document = json.loads(forged_path.read_text(encoding="utf-8"))
    heights = list(document["top_heights"])
    index = next(
        index
        for index, value in enumerate(heights)
        if value + 0.25 <= r273_inputs.setup.stock.size_z.value
    )
    heights[index] += 0.25
    remaining_volume = document["remaining_volume"] + (
        0.25 * document["cell_size_x"] * document["cell_size_y"]
    )
    forged_state = replace(
        state,
        top_heights=tuple(heights),
        remaining_volume=remaining_volume,
    )
    document["top_heights"] = heights
    document["remaining_volume"] = remaining_volume
    document["content_integrity_fingerprint"] = (
        forged_state.content_integrity_fingerprint.to_dict()
    )
    document["checksum_sha256"] = ""
    unsigned = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    document["checksum_sha256"] = hashlib.sha256(unsigned).hexdigest()
    forged_path.write_bytes(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    loaded = store.load(forged_root, state.fingerprint)
    assert loaded.status is MaterialStateLoadStatus.VALID
    assert loaded.state is not None and loaded.state.content_is_verified
    with pytest.raises(OSError, match="collision"):
        store.write(authority_root, loaded.state)
