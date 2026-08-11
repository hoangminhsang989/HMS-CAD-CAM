"""Managed NC provenance and additive project persistence integration tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.post import (
    NCAssemblyExportRequest,
    NCAssemblyExportSourceSnapshot,
    NCExportService,
    ProgramAssemblyService,
)
from hms_cadcam.cam.qualification import (
    MachineQualificationService,
    QualificationArtifactStore,
    QualificationStoreError,
    dumps,
    loads,
    qualify_static_nc,
)
from tests.unit._stage18a_qualification_fixtures import qualification_input
from tests.unit.test_fanuc_robodrill_21i_runtime import _runtime_source
from tests.unit.test_program_assembly import _request


def _managed_project(tmp_path):
    root = tmp_path / "Stage18A.HMS"
    root.mkdir()
    source = _runtime_source()
    request = _request([source])
    execution = ProgramAssemblyService().assemble(request)
    assert execution.result is not None
    export = NCExportService().export_assembly(
        root,
        NCAssemblyExportRequest(source.project_id, execution.result.result_id, "ASSEMBLY.fn"),
        NCAssemblyExportSourceSnapshot(1, request, execution.result),
    )
    assert export.accepted and export.artifact is not None
    return root, execution.result, export.artifact


def test_qualification_report_and_contract_round_trip_deterministically():
    value = qualification_input()
    report = qualify_static_nc(value)

    restored_report = loads(dumps(report))
    restored_contract = loads(dumps(value.machine_contract))

    assert restored_report == report
    assert restored_contract == value.machine_contract
    assert dumps(restored_report) == dumps(report)


def test_managed_qualification_survives_reload_without_sqlite_migration(tmp_path):
    root, result, managed = _managed_project(tmp_path)
    value = qualification_input(result)
    service = MachineQualificationService()

    artifact = service.publish(root, managed, value)
    manifest_first = (root / "post" / "qualification" / "manifest.json").read_bytes()
    restored = service.load(root)
    service.publish(root, managed, value)
    manifest_second = (root / "post" / "qualification" / "manifest.json").read_bytes()

    assert restored == (artifact,)
    assert artifact.report.nc_sha256 == managed.sha256
    assert artifact.managed_nc_artifact_fingerprint == managed.artifact_fingerprint
    assert not artifact.report.machine_ready
    assert manifest_first == manifest_second
    assert not (root / "project.db").exists()


def test_tampered_managed_nc_invalidates_qualification_on_reload(tmp_path):
    root, result, managed = _managed_project(tmp_path)
    service = MachineQualificationService()
    service.publish(root, managed, qualification_input(result))
    (root / managed.output_relative_path).write_bytes(b"tampered")

    with pytest.raises(QualificationStoreError):
        service.load(root)


def test_profile_program_or_managed_artifact_drift_is_stale(tmp_path):
    root, result, managed = _managed_project(tmp_path)
    value = qualification_input(result)
    artifact = MachineQualificationService().publish(root, managed, value)
    store = QualificationArtifactStore()

    assert store.is_current(
        artifact,
        contract_fingerprint=value.machine_contract.fingerprint,
        program_fingerprint=result.result_fingerprint,
        managed_artifact_fingerprint=managed.artifact_fingerprint,
    )
    assert not store.is_current(
        artifact,
        contract_fingerprint=ContentFingerprint.from_payload({"changed": "profile"}),
        program_fingerprint=result.result_fingerprint,
        managed_artifact_fingerprint=managed.artifact_fingerprint,
    )
    assert not store.is_current(
        artifact,
        contract_fingerprint=value.machine_contract.fingerprint,
        program_fingerprint=ContentFingerprint.from_payload({"changed": "program"}),
        managed_artifact_fingerprint=managed.artifact_fingerprint,
    )
    assert not store.is_current(
        artifact,
        contract_fingerprint=value.machine_contract.fingerprint,
        program_fingerprint=result.result_fingerprint,
        managed_artifact_fingerprint=ContentFingerprint.from_payload({"changed": "managed"}),
    )


def test_qualification_cannot_bind_stale_or_mismatched_managed_artifact(tmp_path):
    root, result, managed = _managed_project(tmp_path)
    report = qualify_static_nc(qualification_input(result))

    with pytest.raises(QualificationStoreError):
        QualificationArtifactStore().save(
            root,
            replace(managed, sha256="0" * 64, artifact_fingerprint=None),
            report,
        )
