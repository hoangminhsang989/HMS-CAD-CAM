"""R272 application boundary coverage without bypassing R270/R271 authority."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from hms_cadcam.cam.application.rest_contour import (
    RestContourFoundationInputs,
    resolve_rest_contour_application_parameters,
)
from hms_cadcam.cam.application.rest_contour_lifecycle import (
    RestContourLifecycle,
    RestContourLifecycleContext,
    RestContourLifecycleStatus,
)
from hms_cadcam.cam.domain import CamJob, CamJobId, GeometryResolutionStatus, MachineEvidence, ResolvedContourProfile
from hms_cadcam.cam.domain.rest_contour import (
    RestContourDiagnosticCode,
    RestContourProfileSelection,
)
from hms_cadcam.cam.automatic_parameters import AutomaticParameterMode, CamQualityProfile
from hms_cadcam.cam.persistence import CamPersistencePayloadError, CamProjectSnapshot, CamSqliteRepository
from hms_cadcam.project.database import ProjectDatabase

from test_rest_contour_core_r271 import _inputs, _positive_inputs


def test_application_auto_resolves_effective_values_and_manual_precedence() -> None:
    inputs = _positive_inputs()
    resolved = resolve_rest_contour_application_parameters(
        inputs.parameters,
        RestContourProfileSelection(inputs.profile_descriptor),
        inputs.tool,
        inputs.assembly,
        inputs.setup,
        lambda reference: ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED,
            inputs.profile_descriptor,
        ) if reference == inputs.profile_descriptor.reference else ResolvedContourProfile(
            GeometryResolutionStatus.MISSING,
        ),
        quality_profile=CamQualityProfile.HIGH,
        manual_overrides={
            "stepdown": 1.25,
            "lead_in_length": 0.75,
            "lead_out_length": 0.5,
        },
    )
    contract = __import__(
        "hms_cadcam.cam.automatic_parameters", fromlist=["AutomaticParameterContract"]
    ).AutomaticParameterContract.from_json(resolved.automatic_parameter_contract)
    assert resolved.stepdown.value == 1.25
    assert resolved.lead_in_length.value == 0.75
    assert resolved.lead_out_length.value == 0.5
    assert contract.quality_profile is CamQualityProfile.HIGH
    assert contract.value("stepdown").mode is AutomaticParameterMode.MANUAL_OVERRIDE
    assert contract.value("entry_segment_index").mode is AutomaticParameterMode.AUTO


def test_application_package_does_not_export_lifecycle_mint_or_direct_generate() -> None:
    import hms_cadcam.cam.application as application

    for name in (
        "RestContourLifecycleContext",
        "RestContourLifecyclePreparation",
        "RestContourLifecyclePrepared",
        "RestContourPublicationCallback",
        "prepare_rest_contour",
        "generate_rest_contour_toolpath",
    ):
        assert not hasattr(application, name)


def _context(geometry_inputs) -> RestContourLifecycleContext:
    """Adapt real R271 fixture authority through the application boundary."""
    material = geometry_inputs.foundation.material.candidate
    assert material is not None
    consumer_id = material.dependency.consumer_operation_id
    consumer = geometry_inputs.setup.operation_tree.get_operation(consumer_id)
    descriptor = geometry_inputs.profile_descriptor

    def resolve(reference):
        return ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED if reference == descriptor.reference else GeometryResolutionStatus.MISSING,
            descriptor if reference == descriptor.reference else None,
        )

    foundation_inputs = RestContourFoundationInputs(
        setup=geometry_inputs.setup,
        parameters=geometry_inputs.parameters,
        profile=RestContourProfileSelection(descriptor),
        material_candidates=(material,),
        dependency_graph=geometry_inputs.setup.operation_tree.dependency_graph,
        assembly=geometry_inputs.assembly,
        assembly_evidence=geometry_inputs.assembly_evidence,
        tool=geometry_inputs.tool,
        machine=geometry_inputs.machine,
        machine_requirement=consumer.machine_requirement,
        consumer_operation_id=consumer_id,
    )
    machine = geometry_inputs.machine
    return RestContourLifecycleContext(
        foundation_inputs=foundation_inputs,
        machine_evidence=MachineEvidence(
            True,
            machine.revision,
            machine.content_fingerprint,
            machine.unit,
            machine.capabilities.operations,
        ),
        profile_resolver=resolve,
    )


def test_application_prepare_generate_publishes_real_successor_state(tmp_path: Path) -> None:
    lifecycle = RestContourLifecycle()
    preparation = lifecycle.prepare(_context(_positive_inputs()))

    assert preparation.status is RestContourLifecycleStatus.PREPARED
    result = lifecycle.generate(preparation, project_root=tmp_path)

    assert result.status is RestContourLifecycleStatus.SUCCESS
    assert result.candidate is not None
    assert result.publication is not None
    assert result.successor_publication is not None
    assert result.publication.successor_state.parent_fingerprint == result.candidate.prepared.predecessor_state.fingerprint
    assert result.publication.artifact == result.candidate.artifact
    assert result.successor_publication.successor_state_fingerprint == result.publication.successor_state.fingerprint


def test_application_no_rest_is_typed_and_creates_no_durable_output(tmp_path: Path) -> None:
    lifecycle = RestContourLifecycle()
    preparation = lifecycle.prepare(_context(_inputs(rest=False)))

    assert preparation.status is RestContourLifecycleStatus.NO_REST_MATERIAL
    result = lifecycle.generate(preparation, project_root=tmp_path / "no-rest")

    assert result.status is RestContourLifecycleStatus.NO_REST_MATERIAL
    assert result.candidate is None
    assert result.publication is None
    assert not (tmp_path / "no-rest").exists()


def test_application_unsafe_entry_is_typed_failure_before_durable_publication(tmp_path: Path) -> None:
    lifecycle = RestContourLifecycle()
    preparation = lifecycle.prepare(_context(_inputs()))

    assert preparation.status is RestContourLifecycleStatus.PREPARED
    result = lifecycle.generate(preparation, project_root=tmp_path / "unsafe")

    assert result.status is RestContourLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestContourDiagnosticCode.ENTRY_UNSAFE
    assert not (tmp_path / "unsafe").exists()


def test_application_publisher_failure_cannot_return_success_or_durable_garbage(tmp_path: Path) -> None:
    lifecycle = RestContourLifecycle()
    preparation = lifecycle.prepare(_context(_positive_inputs()))
    root = tmp_path / "publish-failure"

    def fail_before_write(_candidate, _context, _project_root):
        raise OSError("injected persistence failure")

    result = lifecycle.generate(preparation, project_root=root, publisher=fail_before_write)

    assert result.status is RestContourLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestContourDiagnosticCode.PUBLICATION_FAILED
    assert result.candidate is None
    assert result.publication is None
    assert not root.exists()


def _persisted_rest_operation_database(path: Path) -> tuple[CamSqliteRepository, str]:
    """Persist a genuine R271 Rest operation through the R272 SQLite boundary."""
    inputs = _positive_inputs()
    setup = inputs.setup
    job = CamJob(CamJobId.new(), "R272 persisted Rest", setups=(setup,), active_setup_id=setup.setup_id)
    snapshot = CamProjectSnapshot(jobs=(job,), active_job_id=job.job_id)
    ProjectDatabase().initialize(path)
    repository = CamSqliteRepository()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        repository.replace_all(connection, snapshot)
    consumer_id = inputs.foundation.material.candidate.dependency.consumer_operation_id
    return repository, str(consumer_id)


@pytest.mark.parametrize(
    "tamper",
    (
        "profile_required", "expected_kind", "schema", "parameters",
        "machine_requirement", "profile_source", "extra_geometry",
    ),
)
def test_sqlite_reopen_rejects_tampered_known_rest_operation_contract(tmp_path: Path, tamper: str) -> None:
    """Known Rest records use the typed registry decoder instead of generic reopen."""
    database_path = tmp_path / f"{tamper}.db"
    repository, operation_id = _persisted_rest_operation_database(database_path)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        row = connection.execute(
            "SELECT payload_json FROM cam_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        profile = payload["geometry_inputs"][0]
        if tamper == "profile_required":
            profile["required"] = False
        elif tamper == "expected_kind":
            expected = profile["expected_kind"]
            profile["expected_kind"] = "face" if expected != "face" else "edge"
        elif tamper == "schema":
            payload["parameters"]["schema_version"] = 2
        elif tamper == "parameters":
            values = payload["parameters"]["values"]
            stepdown = next(item for item in values if item["name"] == "stepdown")
            stepdown["value"] = 0.0
        elif tamper == "machine_requirement":
            payload["machine_requirement"] = None
        elif tamper == "profile_source":
            values = payload["parameters"]["values"]
            source = next(item for item in values if item["name"] == "profile_source")
            source["value"] = "closed_wire"
        else:
            extra = dict(profile)
            extra["input_id"] = str(uuid4())
            extra["role"] = "stock"
            extra["selection_order"] = 1
            payload["geometry_inputs"].append(extra)
        connection.execute(
            "UPDATE cam_operations SET payload_json=? WHERE operation_id=?",
            (json.dumps(payload, separators=(",", ":"), sort_keys=True), operation_id),
        )

    with pytest.raises(CamPersistencePayloadError):
        repository.load(database_path)
