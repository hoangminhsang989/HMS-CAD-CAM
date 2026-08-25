"""Real R272 project lifecycle coverage for the registered Rest Contour path."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest

from hms_cadcam.cam.application.contour import resolve_profile_in_setup
from hms_cadcam.cam.application.rest_contour_lifecycle import (
    RestContourLifecycle,
    RestContourLifecycleStatus,
)
from hms_cadcam.cam.application.service import _material_removal_operation_fingerprint
from hms_cadcam.cam.automatic_parameters import AutomaticParameterContract, AutomaticParameterMode, CamQualityProfile
from hms_cadcam.cam.domain.contour import ContourProfileSource
from hms_cadcam.cam.domain import (
    CamJob,
    CamJobId,
    CamNodeId,
    CamValidationError,
    ContentFingerprint,
    DirtyReason,
    GeometryInputId,
    GeometryInputRole,
    GeometryResolutionStatus,
    OperationId,
    MachineKind,
    OperationCapability,
    ResolvedContourProfile,
    ToolAssemblyId,
    ToolpathArtifactId,
    Vector3,
)
from hms_cadcam.cam.material_state import MaterialStateStore, calculate_material_state
from hms_cadcam.cam.domain.rest_contour import (
    REST_CONTOUR_STRATEGY_KEY,
    RestContourDiagnosticCode,
    RestContourParameters,
    RestContourProfileSelection,
)
from hms_cadcam.cam.persistence import (
    CamPersistencePayloadError,
    CamProjectSnapshot,
    ToolpathArtifactStore,
)
from hms_cadcam.cam.toolpath import (
    ArcMove,
    LinearMove,
    MotionClass,
    ToolpathArtifact,
    artifact_to_dict,
    compute_material_removal_fingerprint,
)
from hms_cadcam.project.service import ProjectService

# These are R270/R271 real fixture constructors: the producer is a genuine
# ToolpathArtifact and the heightfield is calculated by MaterialState, not a
# detached fake State used only to make an integration assertion pass.
_UNIT_FIXTURES = Path(__file__).parents[1] / "unit"
if str(_UNIT_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_UNIT_FIXTURES))
from test_rest_contour_core_r271 import _inputs as _r271_inputs, _positive_inputs
from test_rest_contour_foundation_r270 import _inputs as _r270_inputs


def _profile_resolver(inputs):
    descriptor = inputs.profile_descriptor

    def resolve(reference):
        return ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED
            if reference == descriptor.reference
            else GeometryResolutionStatus.MISSING,
            descriptor if reference == descriptor.reference else None,
        )

    return resolve


def _replace_setup(snapshot, changed_setup):
    jobs = []
    for job in snapshot.jobs:
        clone = CamJob.from_dict(job.to_dict())
        if any(value.setup_id == changed_setup.setup_id for value in clone.setups):
            clone.replace_setup(changed_setup)
        jobs.append(clone)
    return replace(snapshot, jobs=tuple(jobs))


def _project_with_published_upstream(
    service: ProjectService,
    tmp_path: Path,
    *,
    quality_profile=None,
    manual_overrides=None,
):
    """Install real upstream authority, then create Rest through ProjectService."""
    base = _r270_inputs()
    inputs = _positive_inputs(base_inputs=base)
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    rest_operation = inputs.setup.operation_tree.get_operation(
        candidate.dependency.consumer_operation_id
    )
    # Begin with only the upstream producer.  R272 must add the consumer and
    # typed edge through its official creation service, not inherit them from
    # an R271 fixture aggregate.
    upstream_tree = inputs.setup.operation_tree.remove_node(rest_operation.node_id)
    upstream_setup = replace(inputs.setup, operation_tree=upstream_tree)
    job = CamJob(CamJobId.new(), "R272 Rest lifecycle", setups=(upstream_setup,))
    # R271's isolated fixture intentionally reuses the assembly identity while
    # varying a cutter definition.  A persisted project may not have duplicate
    # assembly IDs, so production creation receives a new but otherwise exact
    # Rest assembly identity.
    rest_assembly = replace(
        inputs.assembly,
        assembly_id=ToolAssemblyId.new(),
        holder_id=None,
        expected_holder_revision=None,
        expected_holder_fingerprint=None,
        expected_holder_unit=None,
    )
    metadata = ToolpathArtifactStore().publish(
        service.current_project.root_path, candidate.producer_artifact
    )
    snapshot = CamProjectSnapshot(
        jobs=(job,),
        active_job_id=job.job_id,
        tool_definitions=(base.tool, inputs.tool),
        tool_assemblies=(base.assembly, rest_assembly),
        machine_definitions=(inputs.machine,),
        artifacts=(metadata,),
    )
    # The positive R271 fixture contains a Rest tool separate from the producer
    # tool.  Add its authoritative definition and assembly without changing
    # the project graph/order semantics.
    producer = upstream_tree.get_operation(candidate.producer_operation_id)
    if producer.tool_assembly.assembly_id != base.assembly.assembly_id:
        raise AssertionError("R271 fixture producer assembly is unexpectedly absent")
    service.stage_cam_snapshot(snapshot)
    service.create_rest_contour_operation(
        job.job_id,
        upstream_setup.setup_id,
        upstream_tree.root_id,
        operation_id=rest_operation.operation_id,
        node_id=rest_operation.node_id,
        name="Rest Contour",
        parameters=inputs.parameters,
        profile=RestContourProfileSelection(inputs.profile_descriptor),
        dependency_operation_id=producer.operation_id,
        tool_assembly_id=rest_assembly.assembly_id,
        machine_requirement=rest_operation.machine_requirement,
        profile_resolver=_profile_resolver(inputs),
        quality_profile=quality_profile,
        manual_overrides=manual_overrides,
    )
    return inputs, job, rest_operation.operation_id


def _project_with_no_rest_upstream(service: ProjectService):
    """Create the official service path for a genuine R271 NO_REST fixture."""
    inputs = _r271_inputs(rest=False)
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    rest_operation = inputs.setup.operation_tree.get_operation(
        candidate.dependency.consumer_operation_id
    )
    upstream_tree = inputs.setup.operation_tree.remove_node(rest_operation.node_id)
    upstream_setup = replace(inputs.setup, operation_tree=upstream_tree)
    job = CamJob(CamJobId.new(), "R272 no-rest lifecycle", setups=(upstream_setup,))
    rest_assembly = replace(
        inputs.assembly,
        assembly_id=ToolAssemblyId.new(),
        holder_id=None,
        expected_holder_revision=None,
        expected_holder_fingerprint=None,
        expected_holder_unit=None,
    )
    metadata = ToolpathArtifactStore().publish(
        service.current_project.root_path, candidate.producer_artifact
    )
    service.stage_cam_snapshot(CamProjectSnapshot(
        jobs=(job,),
        active_job_id=job.job_id,
        tool_definitions=(inputs.tool,),
        tool_assemblies=(inputs.assembly, rest_assembly),
        machine_definitions=(inputs.machine,),
        artifacts=(metadata,),
    ))
    producer = upstream_tree.get_operation(candidate.producer_operation_id)
    service.create_rest_contour_operation(
        job.job_id,
        upstream_setup.setup_id,
        upstream_tree.root_id,
        operation_id=rest_operation.operation_id,
        node_id=rest_operation.node_id,
        name="Rest Contour no-rest",
        parameters=inputs.parameters,
        profile=RestContourProfileSelection(inputs.profile_descriptor),
        dependency_operation_id=producer.operation_id,
        tool_assembly_id=rest_assembly.assembly_id,
        machine_requirement=rest_operation.machine_requirement,
        profile_resolver=_profile_resolver(inputs),
    )
    return inputs, rest_operation.operation_id


def test_project_creation_resolves_and_reopens_auto_with_manual_precedence(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 AUTO Lifecycle")
    _inputs, _job, operation_id = _project_with_published_upstream(
        service,
        tmp_path,
        quality_profile=CamQualityProfile.HIGH,
        manual_overrides={
            "stepdown": 1.25,
            "lead_in_length": 0.75,
            "lead_out_length": 0.5,
        },
    )
    service.save()
    root = session.root_path
    service.close_project()

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    snapshot = reopened.open_project(root).cam_snapshot
    operation = next(
        value for job in snapshot.jobs for setup in job.setups
        for value in setup.operation_tree.operations if value.operation_id == operation_id
    )
    parameters = RestContourParameters.from_operation_parameters(operation.parameters)
    contract = AutomaticParameterContract.from_json(parameters.automatic_parameter_contract)
    assert parameters.stepdown.value == 1.25
    assert parameters.lead_in_length.value == 0.75
    assert parameters.lead_out_length.value == 0.5
    assert contract.quality_profile is CamQualityProfile.HIGH
    assert contract.value("stepdown").mode is AutomaticParameterMode.MANUAL_OVERRIDE


def test_project_create_save_reopen_then_generate_rest_contour(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 Lifecycle")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    root = session.root_path
    service.close_project()

    reopened_service = ProjectService.create_default(tmp_path / "reopen-config")
    reopened = reopened_service.open_project(root)
    operation = next(
        value
        for job in reopened.cam_snapshot.jobs
        for setup in job.setups
        for value in setup.operation_tree.operations
        if value.operation_id == operation_id
    )
    assert operation.strategy_key == REST_CONTOUR_STRATEGY_KEY
    assert len(reopened.cam_snapshot.material_state_dependencies) == 0
    assert any(
        edge.target_operation_id == operation_id and edge.kind.value == "material_state"
        for job in reopened.cam_snapshot.jobs
        for setup in job.setups
        for edge in setup.operation_tree.dependency_graph.edges
    )

    prepared = reopened_service.prepare_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs)
    )
    assert prepared.status is RestContourLifecycleStatus.PREPARED
    assert prepared.operation_id == operation_id
    assert not hasattr(prepared, "prepared")
    result = reopened_service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs)
    )
    assert result.status is RestContourLifecycleStatus.SUCCESS
    reopened_snapshot = reopened_service.cam_snapshot
    dependency = next(
        value for value in reopened_snapshot.material_state_dependencies
        if value.consumer_operation_id == operation_id
    )
    assert dependency.successor_publication is not None
    assert dependency.successor_publication.status == "COMPLETE"
    assert dependency.successor_publication.successor_state_fingerprint == result.publication.successor_state.fingerprint
    assert (root / ".hms" / "cam" / "material_state" / f"{dependency.successor_publication.successor_state_fingerprint.digest}.state.json").is_file()


def test_project_rest_contour_save_failure_restores_snapshot_without_v2_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 Save Rollback")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    before = service.cam_snapshot
    root = service.current_project.root_path

    def fail_save(_session):
        raise OSError("injected SQLite save failure")

    monkeypatch.setattr(service._saver, "save", fail_save)
    with pytest.raises(OSError, match="injected SQLite"):
        service.generate_rest_contour(operation_id, profile_resolver=_profile_resolver(inputs))

    assert service.cam_snapshot == before
    assert not any(
        value.consumer_operation_id == operation_id and value.successor_publication is not None
        for value in service.cam_snapshot.material_state_dependencies
    )
    # Artifact/state bytes written before the injected SQLite failure are
    # unreferenced derived files, never project authority after a fresh reopen.
    service.close_project()
    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    assert not any(
        value.consumer_operation_id == operation_id and value.successor_publication is not None
        for value in reopened.cam_snapshot.material_state_dependencies
    )
    assert not any(value.operation_id == operation_id for value in reopened.cam_snapshot.artifacts)


def test_duplicate_rest_execution_reuses_one_completed_snapshot_record(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 Duplicate")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()

    first = service.generate_rest_contour(operation_id, profile_resolver=_profile_resolver(inputs))
    second = service.generate_rest_contour(operation_id, profile_resolver=_profile_resolver(inputs))

    assert first.status is second.status is RestContourLifecycleStatus.SUCCESS
    records = tuple(
        value for value in service.cam_snapshot.material_state_dependencies
        if value.consumer_operation_id == operation_id
    )
    metadata = tuple(value for value in service.cam_snapshot.artifacts if value.operation_id == operation_id)
    assert len(records) == len(metadata) == 1
    assert records[0].successor_publication is not None
    assert first.publication.artifact.artifact_id == second.publication.artifact.artifact_id
    with pytest.raises(
        CamValidationError,
        match="CAM project snapshot identities must be unique",
    ):
        replace(
            service.cam_snapshot,
            material_state_dependencies=(records[0], records[0]),
        )


@pytest.mark.parametrize("tamper", ("duplicate_payload_row", "producer_column"))
def test_reopen_rejects_material_dependency_raw_row_identity_tamper(
    tmp_path: Path,
    tamper: str,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, f"R272 dependency row identity {tamper}")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    generated = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert generated.status is RestContourLifecycleStatus.SUCCESS
    root = session.root_path
    service.close_project()

    with sqlite3.connect(root / "project.db") as connection:
        row = connection.execute(
            "SELECT consumer_operation_id, producer_operation_id, payload_json "
            "FROM cam_material_state_dependencies WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert row is not None
        valid_consumer, valid_producer, valid_payload = row
        if tamper == "duplicate_payload_row":
            rogue_consumer = OperationId.new()
            while str(rogue_consumer) <= valid_consumer:
                rogue_consumer = OperationId.new()
            rogue_producer = OperationId.new()
            assert str(rogue_consumer) != valid_consumer
            assert str(rogue_producer) != valid_producer
            connection.execute(
                "INSERT INTO cam_material_state_dependencies "
                "(consumer_operation_id, producer_operation_id, payload_json) "
                "VALUES(?,?,?)",
                (str(rogue_consumer), str(rogue_producer), valid_payload),
            )
            ordered_consumers = tuple(
                item[0] for item in connection.execute(
                    "SELECT consumer_operation_id "
                    "FROM cam_material_state_dependencies ORDER BY consumer_operation_id"
                )
            )
            assert ordered_consumers == (valid_consumer, str(rogue_consumer))
        else:
            rogue_producer = OperationId.new()
            while str(rogue_producer) == valid_producer:
                rogue_producer = OperationId.new()
            connection.execute(
                "UPDATE cam_material_state_dependencies SET producer_operation_id=? "
                "WHERE consumer_operation_id=?",
                (str(rogue_producer), valid_consumer),
            )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    with pytest.raises(
        CamPersistencePayloadError,
        match="Material-state dependency row identity does not match its payload",
    ):
        reopened.open_project(root)
    assert reopened.current_project is None


def test_same_process_duplicate_revalidates_current_profile_authority(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 Duplicate Profile Authority")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert first.status is RestContourLifecycleStatus.SUCCESS

    calls = []

    def unavailable_profile(reference):
        calls.append(reference)
        raise RuntimeError("CURRENT_PROFILE_AUTHORITY_UNAVAILABLE")

    second = service.generate_rest_contour(
        operation_id, profile_resolver=unavailable_profile,
    )

    assert calls == [inputs.profile_descriptor.reference]
    assert second.status is RestContourLifecycleStatus.FAILURE
    assert second.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID
    assert first.publication is not None
    assert any(
        value.artifact_id == first.publication.artifact.artifact_id
        for value in service.cam_snapshot.artifacts
    )


def test_semantic_failure_preserves_preexisting_dirty_project_bytes(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 failure transaction")
    _inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    clean = service.save()
    assert clean.cam_snapshot.active_job_id is not None
    service.stage_cam_snapshot(replace(service.cam_snapshot, active_job_id=None))
    assert service.current_project.is_dirty

    def unavailable_profile(_reference):
        raise RuntimeError("CURRENT_PROFILE_AUTHORITY_UNAVAILABLE")

    result = service.generate_rest_contour(
        operation_id,
        profile_resolver=unavailable_profile,
        persist=True,
    )
    assert result.status is RestContourLifecycleStatus.FAILURE
    assert service.current_project.is_dirty
    root = service.current_project.root_path
    service.close_project(discard_changes=True)
    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    assert reopened.open_project(root).cam_snapshot.active_job_id is not None


def test_no_rest_preserves_preexisting_dirty_project_bytes(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 no-rest transaction")
    inputs, operation_id = _project_with_no_rest_upstream(service)
    clean = service.save()
    assert clean.cam_snapshot.active_job_id is not None
    service.stage_cam_snapshot(replace(service.cam_snapshot, active_job_id=None))
    assert service.current_project.is_dirty

    result = service.generate_rest_contour(
        operation_id,
        profile_resolver=_profile_resolver(inputs),
        persist=True,
    )
    assert result.status is RestContourLifecycleStatus.NO_REST_MATERIAL
    assert service.current_project.is_dirty
    root = service.current_project.root_path
    service.close_project(discard_changes=True)
    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    assert reopened.open_project(root).cam_snapshot.active_job_id is not None


@pytest.mark.parametrize(
    "invalid_authority",
    ("missing_machine", "forged_machine", "stale_assembly", "mill_turn", "skew_axes"),
)
def test_official_creation_rejects_unexecutable_aggregate_authority(
    tmp_path: Path,
    invalid_authority: str,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, f"R272 invalid creation {invalid_authority}")
    inputs, job, operation_id = _project_with_published_upstream(service, tmp_path)
    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    operation = setup.operation_tree.get_operation(operation_id)
    edge = next(
        value for value in setup.operation_tree.dependency_graph.edges
        if value.target_operation_id == operation_id
    )
    without_rest = _replace_setup(
        snapshot,
        replace(setup, operation_tree=setup.operation_tree.remove_node(operation.node_id)),
    )
    machine_requirement = operation.machine_requirement
    assert machine_requirement is not None
    if invalid_authority == "missing_machine":
        machine_requirement = None
    elif invalid_authority == "forged_machine":
        machine_requirement = replace(
            machine_requirement,
            expected_fingerprint=inputs.tool.content_fingerprint,
        )
    elif invalid_authority == "stale_assembly":
        without_rest = replace(
            without_rest,
            tool_assemblies=tuple(
                replace(value, expected_tool_fingerprint=inputs.machine.content_fingerprint)
                if value.assembly_id == operation.tool_assembly.assembly_id
                else value
                for value in without_rest.tool_assemblies
            ),
        )
    elif invalid_authority == "mill_turn":
        machine = inputs.machine
        changed_capabilities = replace(
            machine.capabilities,
            turning=True,
            operations=tuple(sorted(
                {*machine.capabilities.operations, OperationCapability.TURNING},
                key=str,
            )),
        )
        changed_machine = replace(
            machine,
            kind=MachineKind.MILL_TURN,
            capabilities=changed_capabilities,
        )
        machine_requirement = replace(
            machine_requirement,
            expected_fingerprint=changed_machine.content_fingerprint,
        )
        without_rest = replace(
            without_rest,
            machine_definitions=(changed_machine,),
        )
    else:
        machine = inputs.machine
        changed_axes = (
            machine.axes[0],
            replace(
                machine.axes[1],
                direction=Vector3(2.0 ** -0.5, 2.0 ** -0.5, 0.0),
            ),
            machine.axes[2],
        )
        changed_machine = replace(machine, axes=changed_axes)
        machine_requirement = replace(
            machine_requirement,
            expected_fingerprint=changed_machine.content_fingerprint,
        )
        without_rest = replace(
            without_rest,
            machine_definitions=(changed_machine,),
        )
    service.stage_cam_snapshot(without_rest)

    with pytest.raises(CamValidationError):
        service.create_rest_contour_operation(
            job.job_id,
            setup.setup_id,
            setup.operation_tree.root_id,
            operation_id=operation.operation_id,
            node_id=operation.node_id,
            name="Rest Contour invalid authority",
            parameters=inputs.parameters,
            profile=RestContourProfileSelection(inputs.profile_descriptor),
            dependency_operation_id=edge.source_operation_id,
            tool_assembly_id=operation.tool_assembly.assembly_id,
            machine_requirement=machine_requirement,
            profile_resolver=_profile_resolver(inputs),
        )

    current_setup = next(
        value for owner in service.cam_snapshot.jobs for value in owner.setups
    )
    assert all(
        value.operation_id != operation_id
        for value in current_setup.operation_tree.operations
    )
    assert all(
        value.target_operation_id != operation_id
        for value in current_setup.operation_tree.dependency_graph.edges
    )


def test_official_creation_rejects_profile_source_reference_kind_mismatch(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 invalid profile source")
    inputs, job, operation_id = _project_with_published_upstream(service, tmp_path)
    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    operation = setup.operation_tree.get_operation(operation_id)
    edge = next(
        value for value in setup.operation_tree.dependency_graph.edges
        if value.target_operation_id == operation_id
    )
    service.stage_cam_snapshot(_replace_setup(
        snapshot,
        replace(setup, operation_tree=setup.operation_tree.remove_node(operation.node_id)),
    ))

    with pytest.raises(CamValidationError, match="profile source"):
        service.create_rest_contour_operation(
            job.job_id,
            setup.setup_id,
            setup.operation_tree.root_id,
            operation_id=operation.operation_id,
            node_id=operation.node_id,
            name="Rest Contour invalid profile source",
            parameters=replace(
                inputs.parameters,
                profile_source=ContourProfileSource.CLOSED_WIRE,
            ),
            profile=RestContourProfileSelection(inputs.profile_descriptor),
            dependency_operation_id=edge.source_operation_id,
            tool_assembly_id=operation.tool_assembly.assembly_id,
            machine_requirement=operation.machine_requirement,
            profile_resolver=_profile_resolver(inputs),
        )

    current_setup = next(
        value for owner in service.cam_snapshot.jobs for value in owner.setups
    )
    assert all(
        value.operation_id != operation_id
        for value in current_setup.operation_tree.operations
    )
    assert all(
        value.target_operation_id != operation_id
        for value in current_setup.operation_tree.dependency_graph.edges
    )


def test_official_creation_rejects_profile_from_foreign_setup_source(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 foreign profile source")
    inputs, job, operation_id = _project_with_published_upstream(service, tmp_path)
    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    operation = setup.operation_tree.get_operation(operation_id)
    edge = next(
        value for value in setup.operation_tree.dependency_graph.edges
        if value.target_operation_id == operation_id
    )
    service.stage_cam_snapshot(_replace_setup(
        snapshot,
        replace(setup, operation_tree=setup.operation_tree.remove_node(operation.node_id)),
    ))
    foreign_descriptor = replace(
        inputs.profile_descriptor,
        reference=replace(inputs.profile_descriptor.reference, source_id=uuid4()),
    )

    def resolve_foreign(reference):
        return ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED,
            foreign_descriptor if reference == foreign_descriptor.reference else None,
        )

    with pytest.raises(CamValidationError, match="profile source scope"):
        service.create_rest_contour_operation(
            job.job_id,
            setup.setup_id,
            setup.operation_tree.root_id,
            operation_id=operation.operation_id,
            node_id=operation.node_id,
            name="Rest Contour foreign profile source",
            parameters=inputs.parameters,
            profile=RestContourProfileSelection(foreign_descriptor),
            dependency_operation_id=edge.source_operation_id,
            tool_assembly_id=operation.tool_assembly.assembly_id,
            machine_requirement=operation.machine_requirement,
            profile_resolver=resolve_foreign,
        )

    current_setup = next(
        value for owner in service.cam_snapshot.jobs for value in owner.setups
    )
    assert all(
        value.operation_id != operation_id
        for value in current_setup.operation_tree.operations
    )
    assert all(
        value.target_operation_id != operation_id
        for value in current_setup.operation_tree.dependency_graph.edges
    )


def test_same_process_runtime_rejects_extra_rest_geometry_input(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 runtime geometry shape")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    operation = setup.operation_tree.get_operation(operation_id)
    profile = operation.geometry_inputs[0]
    extra = replace(
        profile,
        input_id=GeometryInputId.new(),
        role=GeometryInputRole.STOCK,
        selection_order=1,
    )
    changed_operation = replace(
        operation,
        geometry_inputs=(profile, extra),
    )
    changed_tree = setup.operation_tree.replace_operation(changed_operation)
    service.stage_cam_snapshot(_replace_setup(
        snapshot, replace(setup, operation_tree=changed_tree),
    ))

    result = service.generate_rest_contour(
        operation_id,
        profile_resolver=_profile_resolver(inputs),
        persist=False,
    )
    assert result.status is RestContourLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestContourDiagnosticCode.PROFILE_INVALID


def test_persist_true_commits_an_existing_nonpersistent_completion(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 Persist Transition")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()

    first = service.generate_rest_contour(
        operation_id,
        profile_resolver=_profile_resolver(inputs),
        persist=False,
    )
    assert first.status is RestContourLifecycleStatus.SUCCESS
    assert session.is_dirty
    with sqlite3.connect(session.root_path / "project.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()[0] == 0

    second = service.generate_rest_contour(
        operation_id,
        profile_resolver=_profile_resolver(inputs),
        persist=True,
    )
    assert second.status is RestContourLifecycleStatus.SUCCESS
    assert second.publication.artifact.artifact_id == first.publication.artifact.artifact_id
    with sqlite3.connect(session.root_path / "project.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()[0] == 1


def test_referenced_tool_mutation_removes_rest_completion_authority(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 Authority Invalidation")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    result = service.generate_rest_contour(operation_id, profile_resolver=_profile_resolver(inputs))
    assert result.status is RestContourLifecycleStatus.SUCCESS

    snapshot = service.cam_snapshot
    updated_tools = tuple(
        replace(tool, name="Rest tool changed")
        if tool.tool_id == inputs.tool.tool_id else tool
        for tool in snapshot.tool_definitions
    )
    service.stage_cam_snapshot(replace(snapshot, tool_definitions=updated_tools))

    invalidated = service.cam_snapshot
    assert not any(value.operation_id == operation_id for value in invalidated.artifacts)
    assert not any(
        value.consumer_operation_id == operation_id
        for value in invalidated.material_state_dependencies
    )


def test_upstream_tool_semantic_mutation_invalidates_completed_rest_authority(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 Upstream Invalidation")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    result = service.generate_rest_contour(operation_id, profile_resolver=_profile_resolver(inputs))
    assert result.status is RestContourLifecycleStatus.SUCCESS

    snapshot = service.cam_snapshot
    dependency = next(
        value for value in snapshot.material_state_dependencies
        if value.consumer_operation_id == operation_id
    )
    setup = next(value for job in snapshot.jobs for value in job.setups)
    producer = setup.operation_tree.get_operation(dependency.producer_operation_id)
    producer_assembly = next(
        value for value in snapshot.tool_assemblies
        if value.assembly_id == producer.tool_assembly.assembly_id
    )
    changed_tools = tuple(
        replace(value, name="Upstream cutter semantics changed")
        if value.tool_id == producer_assembly.tool_id else value
        for value in snapshot.tool_definitions
    )
    service.stage_cam_snapshot(replace(snapshot, tool_definitions=changed_tools))

    assert not any(value.operation_id == operation_id for value in service.cam_snapshot.artifacts)
    assert not any(
        value.consumer_operation_id == operation_id
        for value in service.cam_snapshot.material_state_dependencies
    )


def test_first_rest_run_rejects_old_artifact_after_producer_cutter_changes(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 Initial Upstream Stale")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    edge = next(
        value for value in setup.operation_tree.dependency_graph.edges
        if value.target_operation_id == operation_id and value.kind.value == "material_state"
    )
    producer = setup.operation_tree.get_operation(edge.source_operation_id)
    producer_assembly = next(
        value for value in snapshot.tool_assemblies
        if value.assembly_id == producer.tool_assembly.assembly_id
    )
    changed_tools = tuple(
        replace(
            value,
            cutting_geometry=replace(
                value.cutting_geometry,
                diameter=replace(
                    value.cutting_geometry.diameter,
                    value=value.cutting_geometry.diameter.value * 1.2,
                ),
            ),
        ) if value.tool_id == producer_assembly.tool_id else value
        for value in snapshot.tool_definitions
    )
    service.stage_cam_snapshot(replace(snapshot, tool_definitions=changed_tools))

    result = service.generate_rest_contour(
        operation_id,
        profile_resolver=_profile_resolver(inputs),
        persist=False,
    )
    assert result.status is RestContourLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_STALE
    assert not any(value.operation_id == operation_id for value in service.cam_snapshot.artifacts)


def test_feed_only_upstream_edit_preserves_completed_rest_after_reopen(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 feed-only upstream")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert first.status is RestContourLifecycleStatus.SUCCESS

    snapshot = service.cam_snapshot
    dependency = next(
        value for value in snapshot.material_state_dependencies
        if value.consumer_operation_id == operation_id
    )
    setup = next(value for job in snapshot.jobs for value in job.setups)
    producer = setup.operation_tree.get_operation(dependency.producer_operation_id)
    feed_values = tuple(
        (key, 325.0 if key == "cutting_feed_rate" else value)
        for key, value in producer.parameters.values
        if key != "cutting_feed_rate"
    ) + (("cutting_feed_rate", 325.0),)
    changed_producer = replace(
        producer,
        parameters=replace(producer.parameters, values=feed_values),
        revision=producer.revision.next(),
        artifact_state=producer.artifact_state.mark_dirty(
            DirtyReason.PARAMETERS_CHANGED,
        ),
    )
    changed_setup = replace(
        setup,
        operation_tree=setup.operation_tree.replace_operation(changed_producer),
    )
    service.stage_cam_snapshot(_replace_setup(snapshot, changed_setup))
    assert any(
        value.consumer_operation_id == operation_id
        for value in service.cam_snapshot.material_state_dependencies
    )
    service.save()
    root = session.root_path
    service.close_project()

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    second = reopened.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert second.status is RestContourLifecycleStatus.SUCCESS
    assert second.publication.artifact.artifact_id == first.publication.artifact.artifact_id


def test_forged_feed_only_shape_cannot_hide_ordinary_producer_semantic_edit(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 forged ordinary producer")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert first.status is RestContourLifecycleStatus.SUCCESS
    dependency = next(
        value for value in service.cam_snapshot.material_state_dependencies
        if value.consumer_operation_id == operation_id
    )
    producer_type = type(next(
        value for job in service.cam_snapshot.jobs for setup in job.setups
        for value in setup.operation_tree.operations
        if value.operation_id == dependency.producer_operation_id
    ))
    root = session.root_path
    service.close_project()

    with sqlite3.connect(root / "project.db") as connection:
        row = connection.execute(
            "SELECT payload_json FROM cam_operations WHERE operation_id=?",
            (str(dependency.producer_operation_id),),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        payload["parameters"]["values"].append({
            "name": "final_depth",
            "value": 3.0,
        })
        payload["revision"]["value"] += 1
        payload["artifact_state"].update(
            status="dirty",
            token=None,
            artifact_fingerprint=None,
            dirty_reasons=["parameters_changed"],
            diagnostics=[],
        )
        payload["diagnostics"] = []
        connection.execute(
            "UPDATE cam_operations SET payload_json=? WHERE operation_id=?",
            (
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ),
                str(dependency.producer_operation_id),
            ),
        )
        dependency_row = connection.execute(
            "SELECT payload_json FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert dependency_row is not None
        dependency_payload = json.loads(dependency_row[0])
        forged_operation = producer_type.from_dict(payload)
        dependency_payload["producer_operation_authority_fingerprint"] = (
            _material_removal_operation_fingerprint(forged_operation).to_dict()
        )
        connection.execute(
            "UPDATE cam_material_state_dependencies SET payload_json=? "
            "WHERE consumer_operation_id=?",
            (
                json.dumps(
                    dependency_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                str(operation_id),
            ),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert replay.status is RestContourLifecycleStatus.FAILURE
    assert replay.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_direct_feed_edited_rest_regenerates_current_artifact(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 direct Rest feed regeneration")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert first.status is RestContourLifecycleStatus.SUCCESS

    snapshot = service.cam_snapshot
    setup = next(value for job in snapshot.jobs for value in job.setups)
    operation = setup.operation_tree.get_operation(operation_id)
    changed_values = tuple(
        (key, value + 25.0 if key == "cutting_feed_rate" else value)
        for key, value in operation.parameters.values
    )
    changed_operation = replace(
        operation,
        parameters=replace(operation.parameters, values=changed_values),
        revision=operation.revision.next(),
        artifact_state=operation.artifact_state.mark_dirty(
            DirtyReason.PARAMETERS_CHANGED,
        ),
    )
    service.stage_cam_snapshot(_replace_setup(
        snapshot,
        replace(
            setup,
            operation_tree=setup.operation_tree.replace_operation(changed_operation),
        ),
    ))
    second = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert second.status is RestContourLifecycleStatus.SUCCESS
    assert second.publication.artifact.artifact_id != first.publication.artifact.artifact_id


def test_unrelated_tool_addition_preserves_and_reuses_completed_rest_authority(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 Unrelated Change")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(operation_id, profile_resolver=_profile_resolver(inputs))
    assert first.status is RestContourLifecycleStatus.SUCCESS

    snapshot = service.cam_snapshot
    unrelated = replace(
        inputs.tool,
        tool_id=type(inputs.tool.tool_id).new(),
        name="Unrelated project tool",
    )
    service.stage_cam_snapshot(replace(
        snapshot, tool_definitions=(*snapshot.tool_definitions, unrelated),
    ))
    assert any(value.operation_id == operation_id for value in service.cam_snapshot.artifacts)
    second = service.generate_rest_contour(operation_id, profile_resolver=_profile_resolver(inputs))
    assert second.status is RestContourLifecycleStatus.SUCCESS
    assert second.publication.artifact.artifact_id == first.publication.artifact.artifact_id


def test_missing_explicit_material_edge_returns_typed_failure(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "R272 Missing Dependency")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    snapshot = service.cam_snapshot
    setup = next(value for job in snapshot.jobs for value in job.setups)
    edge = next(
        value for value in setup.operation_tree.dependency_graph.edges
        if value.target_operation_id == operation_id and value.kind.value == "material_state"
    )
    changed_tree = replace(
        setup.operation_tree,
        dependency_graph=setup.operation_tree.dependency_graph.without_edge(edge),
        revision=setup.operation_tree.revision.next(),
    )
    changed_setup = replace(setup, operation_tree=changed_tree)
    changed_jobs = []
    for job in snapshot.jobs:
        clone = CamJob.from_dict(job.to_dict())
        if any(value.setup_id == changed_setup.setup_id for value in clone.setups):
            clone.replace_setup(changed_setup)
        changed_jobs.append(clone)
    service.stage_cam_snapshot(replace(snapshot, jobs=tuple(changed_jobs)))

    result = service.prepare_rest_contour(operation_id, profile_resolver=_profile_resolver(inputs))
    assert result.status is RestContourLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_MISSING


def test_second_rest_operation_can_consume_reopened_state2_without_list_order_fallback(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 Rest Chain")
    inputs, job, first_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(first_id, profile_resolver=_profile_resolver(inputs))
    assert first.status is RestContourLifecycleStatus.SUCCESS

    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    first_operation = setup.operation_tree.get_operation(first_id)
    second_id = type(first_id).new()
    service.create_rest_contour_operation(
        job.job_id,
        setup.setup_id,
        setup.operation_tree.root_id,
        operation_id=second_id,
        node_id=CamNodeId.new(),
        name="Rest Contour B",
        parameters=inputs.parameters,
        profile=RestContourProfileSelection(inputs.profile_descriptor),
        dependency_operation_id=first_id,
        tool_assembly_id=first_operation.tool_assembly.assembly_id,
        machine_requirement=first_operation.machine_requirement,
        profile_resolver=_profile_resolver(inputs),
    )
    service.save()
    root = session.root_path
    service.close_project()

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    second = reopened.prepare_rest_contour(second_id, profile_resolver=_profile_resolver(inputs))
    # The v2 STATE_2 was accepted as the explicit parent and resolution reached
    # residual planning.  This fixture has no further accountable layer, so the
    # current R271 subset fails closed rather than hard-coding one Rest only.
    assert second.status is RestContourLifecycleStatus.FAILURE
    assert second.diagnostic_code is RestContourDiagnosticCode.RESIDUAL_INVALID


def test_feed_only_rest_a_edit_preserves_state2_for_rest_b_after_reopen(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 feed-only recursive Rest")
    inputs, job, first_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(
        first_id, profile_resolver=_profile_resolver(inputs),
    )
    assert first.status is RestContourLifecycleStatus.SUCCESS

    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    first_operation = setup.operation_tree.get_operation(first_id)
    second_id = OperationId.new()
    service.create_rest_contour_operation(
        job.job_id,
        setup.setup_id,
        setup.operation_tree.root_id,
        operation_id=second_id,
        node_id=CamNodeId.new(),
        name="Rest Contour B after feed edit",
        parameters=inputs.parameters,
        profile=RestContourProfileSelection(inputs.profile_descriptor),
        dependency_operation_id=first_id,
        tool_assembly_id=first_operation.tool_assembly.assembly_id,
        machine_requirement=first_operation.machine_requirement,
        profile_resolver=_profile_resolver(inputs),
    )
    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    first_operation = setup.operation_tree.get_operation(first_id)
    changed_values = tuple(
        (key, value + 25.0 if key == "cutting_feed_rate" else value)
        for key, value in first_operation.parameters.values
    )
    changed_first = replace(
        first_operation,
        parameters=replace(first_operation.parameters, values=changed_values),
        revision=first_operation.revision.next(),
        artifact_state=first_operation.artifact_state.mark_dirty(
            DirtyReason.PARAMETERS_CHANGED,
        ),
    )
    service.stage_cam_snapshot(_replace_setup(
        snapshot,
        replace(
            setup,
            operation_tree=setup.operation_tree.replace_operation(changed_first),
        ),
    ))
    assert any(
        value.consumer_operation_id == first_id
        for value in service.cam_snapshot.material_state_dependencies
    )
    service.save()
    root = session.root_path
    service.close_project()

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    second = reopened.prepare_rest_contour(
        second_id, profile_resolver=_profile_resolver(inputs),
    )
    assert second.status is RestContourLifecycleStatus.FAILURE
    assert second.diagnostic_code is RestContourDiagnosticCode.RESIDUAL_INVALID


def test_recursive_feed_only_rest_replay_preserves_prepare_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 recursive Rest cancellation")
    inputs, job, first_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(
        first_id, profile_resolver=_profile_resolver(inputs),
    )
    assert first.status is RestContourLifecycleStatus.SUCCESS

    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    first_operation = setup.operation_tree.get_operation(first_id)
    second_id = OperationId.new()
    service.create_rest_contour_operation(
        job.job_id,
        setup.setup_id,
        setup.operation_tree.root_id,
        operation_id=second_id,
        node_id=CamNodeId.new(),
        name="Rest Contour B recursive cancellation",
        parameters=inputs.parameters,
        profile=RestContourProfileSelection(inputs.profile_descriptor),
        dependency_operation_id=first_id,
        tool_assembly_id=first_operation.tool_assembly.assembly_id,
        machine_requirement=first_operation.machine_requirement,
        profile_resolver=_profile_resolver(inputs),
    )
    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    first_operation = setup.operation_tree.get_operation(first_id)
    changed_values = tuple(
        (key, value + 25.0 if key == "cutting_feed_rate" else value)
        for key, value in first_operation.parameters.values
    )
    changed_first = replace(
        first_operation,
        parameters=replace(first_operation.parameters, values=changed_values),
        revision=first_operation.revision.next(),
        artifact_state=first_operation.artifact_state.mark_dirty(
            DirtyReason.PARAMETERS_CHANGED,
        ),
    )
    service.stage_cam_snapshot(_replace_setup(
        snapshot,
        replace(
            setup,
            operation_tree=setup.operation_tree.replace_operation(changed_first),
        ),
    ))
    assert any(
        value.consumer_operation_id == first_id
        for value in service.cam_snapshot.material_state_dependencies
    )
    service.save()
    root = session.root_path
    service.close_project()

    armed = False
    recursive_prepare_calls = 0
    cancellation_polls_before_recursive_prepare = 0
    cancellation_polls_inside_recursive_prepare = 0
    original_prepare = RestContourLifecycle.prepare

    def arm_recursive_prepare(self, context):
        nonlocal armed, recursive_prepare_calls
        if context.foundation_inputs.consumer_operation_id == first_id:
            recursive_prepare_calls += 1
            armed = True
        return original_prepare(self, context)

    def cancel_on_first_recursive_prepare_poll() -> bool:
        nonlocal cancellation_polls_before_recursive_prepare
        nonlocal cancellation_polls_inside_recursive_prepare
        if not armed:
            cancellation_polls_before_recursive_prepare += 1
            return False
        cancellation_polls_inside_recursive_prepare += 1
        return True

    monkeypatch.setattr(RestContourLifecycle, "prepare", arm_recursive_prepare)
    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    second = reopened.prepare_rest_contour(
        second_id,
        profile_resolver=_profile_resolver(inputs),
        cancellation=cancel_on_first_recursive_prepare_poll,
    )

    assert cancellation_polls_before_recursive_prepare > 0
    assert recursive_prepare_calls == 1
    assert cancellation_polls_inside_recursive_prepare == 1
    assert second.status is RestContourLifecycleStatus.FAILURE
    assert second.diagnostic_code is RestContourDiagnosticCode.CANCELLED


def test_rest_successor_cannot_fall_back_to_orphan_rest_artifact_without_v2(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 orphan Rest producer")
    inputs, job, first_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    assert service.generate_rest_contour(
        first_id, profile_resolver=_profile_resolver(inputs),
    ).status is RestContourLifecycleStatus.SUCCESS
    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    first_operation = setup.operation_tree.get_operation(first_id)
    second_id = OperationId.new()
    service.create_rest_contour_operation(
        job.job_id,
        setup.setup_id,
        setup.operation_tree.root_id,
        operation_id=second_id,
        node_id=CamNodeId.new(),
        name="Rest Contour B orphan probe",
        parameters=inputs.parameters,
        profile=RestContourProfileSelection(inputs.profile_descriptor),
        dependency_operation_id=first_id,
        tool_assembly_id=first_operation.tool_assembly.assembly_id,
        machine_requirement=first_operation.machine_requirement,
        profile_resolver=_profile_resolver(inputs),
    )
    service.save()
    root = session.root_path
    service.close_project()
    with sqlite3.connect(root / "project.db") as connection:
        connection.execute(
            "DELETE FROM cam_material_state_dependencies WHERE consumer_operation_id=?",
            (str(first_id),),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    result = reopened.prepare_rest_contour(
        second_id, profile_resolver=_profile_resolver(inputs),
    )
    assert result.status is RestContourLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_v2_producer_identity_must_match_authoritative_dag_after_reopen(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 Producer Identity")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(operation_id, profile_resolver=_profile_resolver(inputs))
    assert first.status is RestContourLifecycleStatus.SUCCESS

    snapshot = service.cam_snapshot
    dependency = next(
        value for value in snapshot.material_state_dependencies
        if value.consumer_operation_id == operation_id
    )
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    producer = setup.operation_tree.get_operation(dependency.producer_operation_id)
    producer_metadata = next(
        value for value in snapshot.artifacts if value.operation_id == producer.operation_id
    )
    producer_artifact = ToolpathArtifactStore().load(session.root_path, producer_metadata)
    foreign_id = OperationId.new()
    foreign_operation = replace(
        producer, operation_id=foreign_id, node_id=CamNodeId.new(),
    )
    foreign_events = tuple(
        replace(event, source_operation_id=foreign_id)
        for event in producer_artifact.events
    )
    foreign_artifact = replace(
        producer_artifact,
        artifact_id=ToolpathArtifactId.new(),
        source_operation_id=foreign_id,
        events=foreign_events,
        artifact_fingerprint=None,
    )
    foreign_metadata = ToolpathArtifactStore().publish(session.root_path, foreign_artifact)
    changed_tree = setup.operation_tree.add_operation(
        setup.operation_tree.root_id, "Foreign equal producer", foreign_operation,
    )
    service.stage_cam_snapshot(replace(
        _replace_setup(snapshot, replace(setup, operation_tree=changed_tree)),
        artifacts=(*snapshot.artifacts, foreign_metadata),
    ))
    service.save()
    root = session.root_path
    service.close_project()

    with sqlite3.connect(root / "project.db") as connection:
        row = connection.execute(
            "SELECT payload_json FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        payload["producer_operation_id"] = str(foreign_id)
        connection.execute(
            "UPDATE cam_material_state_dependencies SET producer_operation_id=?, "
            "payload_json=? WHERE consumer_operation_id=?",
            (
                str(foreign_id),
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                str(operation_id),
            ),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    result = reopened.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert result.status is RestContourLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


@pytest.mark.parametrize("forge_feed_only_state", (False, True))
def test_recursive_state2_rejects_stale_rest_a_input_before_rest_b_planning(
    tmp_path: Path,
    forge_feed_only_state: bool,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 Recursive State2")
    inputs, job, first_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    first = service.generate_rest_contour(first_id, profile_resolver=_profile_resolver(inputs))
    assert first.status is RestContourLifecycleStatus.SUCCESS
    snapshot = service.cam_snapshot
    setup = next(value for owner in snapshot.jobs for value in owner.setups)
    first_operation = setup.operation_tree.get_operation(first_id)
    second_id = OperationId.new()
    service.create_rest_contour_operation(
        job.job_id,
        setup.setup_id,
        setup.operation_tree.root_id,
        operation_id=second_id,
        node_id=CamNodeId.new(),
        name="Rest Contour B",
        parameters=inputs.parameters,
        profile=RestContourProfileSelection(inputs.profile_descriptor),
        dependency_operation_id=first_id,
        tool_assembly_id=first_operation.tool_assembly.assembly_id,
        machine_requirement=first_operation.machine_requirement,
        profile_resolver=_profile_resolver(inputs),
    )
    service.save()
    root = session.root_path
    service.close_project()

    with sqlite3.connect(root / "project.db") as connection:
        row = connection.execute(
            "SELECT payload_json FROM cam_operations WHERE operation_id=?",
            (str(first_id),),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        final_depth = next(
            value for value in payload["parameters"]["values"]
            if value["name"] == "final_depth"
        )
        final_depth["value"] = 3.0
        if forge_feed_only_state:
            payload["revision"]["value"] += 1
            payload["artifact_state"].update(
                status="dirty",
                token=None,
                artifact_fingerprint=None,
                dirty_reasons=["parameters_changed"],
                diagnostics=[],
            )
            payload["diagnostics"] = []
        connection.execute(
            "UPDATE cam_operations SET payload_json=? WHERE operation_id=?",
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                str(first_id),
            ),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    result = reopened.prepare_rest_contour(second_id, profile_resolver=_profile_resolver(inputs))
    assert result.status is RestContourLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


@pytest.mark.parametrize(
    ("status", "dirty_reasons"),
    (
        ("dirty", ["parameters_changed"]),
        ("failed", []),
        ("missing", ["artifact_missing"]),
    ),
)
def test_reopen_rejects_completion_when_operation_artifact_state_is_not_valid(
    tmp_path: Path,
    status: str,
    dirty_reasons: list[str],
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, f"R272 invalid artifact state {status}")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    result = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert result.status is RestContourLifecycleStatus.SUCCESS
    root = session.root_path
    service.close_project()

    with sqlite3.connect(root / "project.db") as connection:
        row = connection.execute(
            "SELECT payload_json FROM cam_operations WHERE operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        state = payload["artifact_state"]
        state["status"] = status
        state["token"] = None
        state["artifact_fingerprint"] = None
        state["dirty_reasons"] = dirty_reasons
        connection.execute(
            "UPDATE cam_operations SET payload_json=? WHERE operation_id=?",
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                str(operation_id),
            ),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert replay.status is RestContourLifecycleStatus.FAILURE
    assert replay.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


@pytest.mark.parametrize("diagnostic_owner", ("artifact_state", "operation"))
def test_reopen_rejects_completed_operation_with_injected_diagnostics(
    tmp_path: Path,
    diagnostic_owner: str,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, f"R272 injected {diagnostic_owner} diagnostic")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    assert service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    ).status is RestContourLifecycleStatus.SUCCESS
    root = session.root_path
    service.close_project()
    diagnostic = {
        "format": "HMS_CAM_VALIDATION_DIAGNOSTIC",
        "format_version": 1,
        "severity": "error",
        "code": "artifact_corrupt",
        "message": "injected completed-operation diagnostic",
        "context": [],
    }
    with sqlite3.connect(root / "project.db") as connection:
        row = connection.execute(
            "SELECT payload_json FROM cam_operations WHERE operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        owner = payload["artifact_state"] if diagnostic_owner == "artifact_state" else payload
        owner["diagnostics"] = [diagnostic]
        connection.execute(
            "UPDATE cam_operations SET payload_json=? WHERE operation_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), str(operation_id)),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert replay.status is RestContourLifecycleStatus.FAILURE
    assert replay.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_reopen_rejects_tampered_outer_dependency_toolpath_provenance(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 outer provenance tamper")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    result = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert result.status is RestContourLifecycleStatus.SUCCESS
    assert result.publication is not None
    root = session.root_path
    service.close_project()
    with sqlite3.connect(root / "project.db") as connection:
        row = connection.execute(
            "SELECT payload_json FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        payload["producer_toolpath_fingerprint"] = (
            result.publication.artifact.artifact_fingerprint.to_dict()
        )
        connection.execute(
            "UPDATE cam_material_state_dependencies SET payload_json=? "
            "WHERE consumer_operation_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), str(operation_id)),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert replay.status is RestContourLifecycleStatus.FAILURE
    assert replay.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_reopen_rejects_coherently_resealed_successor_heightfield_and_v2_record(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 coherent successor tamper")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    result = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert result.status is RestContourLifecycleStatus.SUCCESS
    assert result.publication is not None
    successor = result.publication.successor_state
    root = session.root_path
    service.close_project()

    state_path = (
        root / ".hms" / "cam" / "material_state"
        / f"{successor.fingerprint.digest}.state.json"
    )
    state_document = json.loads(state_path.read_text(encoding="utf-8"))
    changed_index = next(
        index for index, value in enumerate(state_document["top_heights"])
        if value > 0.0
    )
    state_document["top_heights"][changed_index] *= 0.5
    state_document["remaining_volume"] = (
        sum(state_document["top_heights"])
        * state_document["cell_size_x"]
        * state_document["cell_size_y"]
    )
    content_payload = {
        "format": "HMS_CAM_MATERIAL_STATE_CONTENT_INTEGRITY",
        "format_version": 1,
        "schema_version": state_document["format_version"],
        "width": state_document["width"],
        "height": state_document["height"],
        "cell_size_x": state_document["cell_size_x"],
        "cell_size_y": state_document["cell_size_y"],
        "top_heights": state_document["top_heights"],
        "initial_volume": state_document["initial_volume"],
        "remaining_volume": state_document["remaining_volume"],
        "unit": state_document["unit"],
    }
    changed_seal = ContentFingerprint.from_payload(content_payload)
    state_document["content_integrity_fingerprint"] = changed_seal.to_dict()
    state_document["checksum_sha256"] = ""
    unsigned = json.dumps(
        state_document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    state_document["checksum_sha256"] = hashlib.sha256(unsigned).hexdigest()
    state_path.write_text(
        json.dumps(
            state_document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    with sqlite3.connect(root / "project.db") as connection:
        row = connection.execute(
            "SELECT payload_json FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert row is not None
        dependency_payload = json.loads(row[0])
        publication_payload = dependency_payload["successor_publication"]
        publication_payload["successor_state_content_seal"] = changed_seal.to_dict()
        fingerprint_payload = dict(publication_payload)
        fingerprint_payload.pop("publication_fingerprint")
        publication_payload["publication_fingerprint"] = (
            ContentFingerprint.from_payload(fingerprint_payload).to_dict()
        )
        connection.execute(
            "UPDATE cam_material_state_dependencies SET payload_json=? "
            "WHERE consumer_operation_id=?",
            (
                json.dumps(
                    dependency_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                str(operation_id),
            ),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert replay.status is RestContourLifecycleStatus.FAILURE
    assert replay.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID


def test_reopen_rejects_coherently_resealed_rest_artifact_output_tamper(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 coherent Rest artifact tamper")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    result = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert result.status is RestContourLifecycleStatus.SUCCESS
    assert result.publication is not None

    original_artifact = result.publication.artifact
    changed_events = list(original_artifact.events)
    changed_index = next(
        index for index, event in enumerate(changed_events)
        if isinstance(event, (LinearMove, ArcMove))
        and event.motion_class in {MotionClass.CUTTING, MotionClass.LINK}
    )
    changed_event = changed_events[changed_index]
    changed_events[changed_index] = replace(
        changed_event,
        feed_rate=replace(
            changed_event.feed_rate,
            value=changed_event.feed_rate.value + 17.0,
        ),
    )
    changed_artifact = ToolpathArtifact.create(
        artifact_id=original_artifact.artifact_id,
        source_operation_id=original_artifact.source_operation_id,
        operation_revision=original_artifact.operation_revision,
        computation_token=original_artifact.computation_token,
        input_fingerprint=original_artifact.input_fingerprint,
        coordinate_space=original_artifact.coordinate_space,
        unit=original_artifact.unit,
        setup_id=original_artifact.setup_id,
        setup_revision=original_artifact.setup_revision,
        wcs_fingerprint=original_artifact.wcs_fingerprint,
        tool_assembly_id=original_artifact.tool_assembly_id,
        tool_assembly_fingerprint=original_artifact.tool_assembly_fingerprint,
        machine_id=original_artifact.machine_id,
        machine_fingerprint=original_artifact.machine_fingerprint,
        initial_pose=original_artifact.initial_pose,
        events=tuple(changed_events),
        diagnostics=original_artifact.diagnostics,
        completion_status=original_artifact.completion_status,
        created_at=original_artifact.created_at,
    )
    assert changed_artifact.artifact_fingerprint != original_artifact.artifact_fingerprint
    changed_material_fingerprint = compute_material_removal_fingerprint(changed_artifact)

    parent = inputs.foundation.material.candidate
    assert parent is not None
    assert parent.state.fingerprint == result.publication.successor_state.parent_fingerprint
    changed_successor = calculate_material_state(
        stock=inputs.setup.stock,
        artifact=changed_artifact,
        tool=inputs.tool,
        setup_fingerprint=result.publication.successor_state.setup_fingerprint,
        parent=parent.state,
        precision=result.publication.successor_state.precision,
    ).state
    # A feed-only event mutation is deliberately invisible to material-removal
    # semantics.  Every v2 material-state check can therefore be coherently
    # resealed while deterministic Rest output must still reject the artifact.
    assert changed_material_fingerprint == compute_material_removal_fingerprint(
        original_artifact
    )
    assert changed_successor.fingerprint == result.publication.successor_state.fingerprint
    assert (
        changed_successor.content_integrity_fingerprint
        == result.publication.successor_state.content_integrity_fingerprint
    )

    root = session.root_path
    service.close_project()
    forged_payload = json.dumps(
        artifact_to_dict(changed_artifact),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    artifact_store = ToolpathArtifactStore()
    original_metadata = result.publication.artifact_metadata
    artifact_store.resolve_metadata_path(root, original_metadata).write_bytes(
        forged_payload
    )
    changed_metadata = replace(
        original_metadata,
        checksum_sha256=hashlib.sha256(forged_payload).hexdigest(),
        artifact_fingerprint=changed_artifact.artifact_fingerprint,
        size_bytes=len(forged_payload),
    )
    MaterialStateStore().write(root, changed_successor)

    with sqlite3.connect(root / "project.db") as connection:
        operation_row = connection.execute(
            "SELECT payload_json FROM cam_operations WHERE operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert operation_row is not None
        operation_payload = json.loads(operation_row[0])
        original_input_fingerprint = operation_payload["artifact_state"]["input_fingerprint"]
        assert original_input_fingerprint == original_artifact.input_fingerprint.to_dict()
        operation_payload["artifact_state"]["artifact_fingerprint"] = (
            changed_artifact.artifact_fingerprint.to_dict()
        )
        assert operation_payload["artifact_state"]["input_fingerprint"] == original_input_fingerprint
        connection.execute(
            "UPDATE cam_operations SET payload_json=? WHERE operation_id=?",
            (
                json.dumps(
                    operation_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                str(operation_id),
            ),
        )
        connection.execute(
            "UPDATE toolpath_artifacts SET checksum_sha256=?, "
            "artifact_fingerprint_json=?, size_bytes=? WHERE operation_id=?",
            (
                changed_metadata.checksum_sha256,
                json.dumps(
                    changed_metadata.artifact_fingerprint.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                changed_metadata.size_bytes,
                str(operation_id),
            ),
        )

        dependency_row = connection.execute(
            "SELECT payload_json FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert dependency_row is not None
        dependency_payload = json.loads(dependency_row[0])
        publication_payload = dependency_payload["successor_publication"]
        publication_payload["artifact_fingerprint"] = (
            changed_artifact.artifact_fingerprint.to_dict()
        )
        publication_payload["semantic_material_removal_fingerprint"] = (
            changed_material_fingerprint.to_dict()
        )
        publication_payload["successor_state_fingerprint"] = (
            changed_successor.fingerprint.to_dict()
        )
        publication_payload["successor_state_content_seal"] = (
            changed_successor.content_integrity_fingerprint.to_dict()
        )
        fingerprint_payload = dict(publication_payload)
        fingerprint_payload.pop("publication_fingerprint")
        publication_payload["publication_fingerprint"] = (
            ContentFingerprint.from_payload(fingerprint_payload).to_dict()
        )
        connection.execute(
            "UPDATE cam_material_state_dependencies SET payload_json=? "
            "WHERE consumer_operation_id=?",
            (
                json.dumps(
                    dependency_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                str(operation_id),
            ),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert replay.status is RestContourLifecycleStatus.FAILURE
    assert replay.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID
    assert replay.message == (
        "Rest Contour v2 completion is invalid: Rest Contour persisted output "
        "differs from deterministic current output"
    )


def test_reopen_rejects_coherently_resealed_rest_signed_zero_event_tamper(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 coherent signed-zero tamper")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    result = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert result.status is RestContourLifecycleStatus.SUCCESS
    assert result.publication is not None

    original_artifact = result.publication.artifact
    changed_events = list(original_artifact.events)
    changed_index = next(
        index for index, event in enumerate(changed_events)
        if isinstance(event, (LinearMove, ArcMove))
        and event.motion_class is MotionClass.CUTTING
        and json.dumps(event.start.tool_axis.x) == "0.0"
    )
    changed_event = changed_events[changed_index]
    changed_events[changed_index] = replace(
        changed_event,
        start=replace(
            changed_event.start,
            tool_axis=replace(changed_event.start.tool_axis, x=-0.0),
        ),
    )
    assert json.dumps(changed_events[changed_index].start.tool_axis.x) == "-0.0"
    changed_artifact = ToolpathArtifact.create(
        artifact_id=original_artifact.artifact_id,
        source_operation_id=original_artifact.source_operation_id,
        operation_revision=original_artifact.operation_revision,
        computation_token=original_artifact.computation_token,
        input_fingerprint=original_artifact.input_fingerprint,
        coordinate_space=original_artifact.coordinate_space,
        unit=original_artifact.unit,
        setup_id=original_artifact.setup_id,
        setup_revision=original_artifact.setup_revision,
        wcs_fingerprint=original_artifact.wcs_fingerprint,
        tool_assembly_id=original_artifact.tool_assembly_id,
        tool_assembly_fingerprint=original_artifact.tool_assembly_fingerprint,
        machine_id=original_artifact.machine_id,
        machine_fingerprint=original_artifact.machine_fingerprint,
        initial_pose=original_artifact.initial_pose,
        events=tuple(changed_events),
        diagnostics=original_artifact.diagnostics,
        completion_status=original_artifact.completion_status,
        created_at=original_artifact.created_at,
    )
    assert changed_artifact.artifact_fingerprint != original_artifact.artifact_fingerprint
    changed_material_fingerprint = compute_material_removal_fingerprint(changed_artifact)
    assert changed_material_fingerprint != compute_material_removal_fingerprint(
        original_artifact
    )

    parent = inputs.foundation.material.candidate
    assert parent is not None
    assert parent.state.fingerprint == result.publication.successor_state.parent_fingerprint
    changed_successor = calculate_material_state(
        stock=inputs.setup.stock,
        artifact=changed_artifact,
        tool=inputs.tool,
        setup_fingerprint=result.publication.successor_state.setup_fingerprint,
        parent=parent.state,
        precision=result.publication.successor_state.precision,
    ).state
    assert changed_successor.fingerprint != result.publication.successor_state.fingerprint
    assert changed_successor.top_heights == result.publication.successor_state.top_heights
    assert (
        changed_successor.content_integrity_fingerprint
        == result.publication.successor_state.content_integrity_fingerprint
    )

    root = session.root_path
    service.close_project()
    forged_payload = json.dumps(
        artifact_to_dict(changed_artifact),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    artifact_store = ToolpathArtifactStore()
    original_metadata = result.publication.artifact_metadata
    artifact_store.resolve_metadata_path(root, original_metadata).write_bytes(
        forged_payload
    )
    changed_metadata = replace(
        original_metadata,
        checksum_sha256=hashlib.sha256(forged_payload).hexdigest(),
        artifact_fingerprint=changed_artifact.artifact_fingerprint,
        size_bytes=len(forged_payload),
    )
    MaterialStateStore().write(root, changed_successor)

    with sqlite3.connect(root / "project.db") as connection:
        operation_row = connection.execute(
            "SELECT payload_json FROM cam_operations WHERE operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert operation_row is not None
        operation_payload = json.loads(operation_row[0])
        original_input_fingerprint = operation_payload["artifact_state"]["input_fingerprint"]
        assert original_input_fingerprint == original_artifact.input_fingerprint.to_dict()
        operation_payload["artifact_state"]["artifact_fingerprint"] = (
            changed_artifact.artifact_fingerprint.to_dict()
        )
        assert operation_payload["artifact_state"]["input_fingerprint"] == original_input_fingerprint
        connection.execute(
            "UPDATE cam_operations SET payload_json=? WHERE operation_id=?",
            (
                json.dumps(
                    operation_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                str(operation_id),
            ),
        )
        connection.execute(
            "UPDATE toolpath_artifacts SET checksum_sha256=?, "
            "artifact_fingerprint_json=?, size_bytes=? WHERE operation_id=?",
            (
                changed_metadata.checksum_sha256,
                json.dumps(
                    changed_metadata.artifact_fingerprint.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                changed_metadata.size_bytes,
                str(operation_id),
            ),
        )

        dependency_row = connection.execute(
            "SELECT payload_json FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert dependency_row is not None
        dependency_payload = json.loads(dependency_row[0])
        publication_payload = dependency_payload["successor_publication"]
        publication_payload["artifact_fingerprint"] = (
            changed_artifact.artifact_fingerprint.to_dict()
        )
        publication_payload["semantic_material_removal_fingerprint"] = (
            changed_material_fingerprint.to_dict()
        )
        publication_payload["successor_state_fingerprint"] = (
            changed_successor.fingerprint.to_dict()
        )
        publication_payload["successor_state_content_seal"] = (
            changed_successor.content_integrity_fingerprint.to_dict()
        )
        fingerprint_payload = dict(publication_payload)
        fingerprint_payload.pop("publication_fingerprint")
        publication_payload["publication_fingerprint"] = (
            ContentFingerprint.from_payload(fingerprint_payload).to_dict()
        )
        connection.execute(
            "UPDATE cam_material_state_dependencies SET payload_json=? "
            "WHERE consumer_operation_id=?",
            (
                json.dumps(
                    dependency_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                str(operation_id),
            ),
        )

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert replay.status is RestContourLifecycleStatus.FAILURE
    assert replay.diagnostic_code is RestContourDiagnosticCode.MATERIAL_STATE_INVALID
    assert replay.message == (
        "Rest Contour v2 completion is invalid: Rest Contour persisted output "
        "differs from deterministic current output"
    )


def test_reopen_successor_reverification_preserves_cancelled_diagnostic(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 cancelled successor replay")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    result = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert result.status is RestContourLifecycleStatus.SUCCESS
    root = session.root_path
    service.close_project()

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_contour(
        operation_id,
        profile_resolver=_profile_resolver(inputs),
        cancellation=lambda: True,
    )
    assert replay.status is RestContourLifecycleStatus.FAILURE
    assert replay.diagnostic_code is RestContourDiagnosticCode.CANCELLED


def test_reopen_current_input_rederive_preserves_late_cancelled_diagnostic(
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R272 late cancelled replay")
    inputs, _job, operation_id = _project_with_published_upstream(service, tmp_path)
    service.save()
    result = service.generate_rest_contour(
        operation_id, profile_resolver=_profile_resolver(inputs),
    )
    assert result.status is RestContourLifecycleStatus.SUCCESS
    root = session.root_path
    service.close_project()

    calls = 0

    def cancel_after_successor_recompute() -> bool:
        nonlocal calls
        calls += 1
        return calls > 107

    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_contour(
        operation_id,
        profile_resolver=_profile_resolver(inputs),
        cancellation=cancel_after_successor_recompute,
    )
    assert calls > 107
    assert replay.status is RestContourLifecycleStatus.FAILURE
    assert replay.diagnostic_code is RestContourDiagnosticCode.CANCELLED
