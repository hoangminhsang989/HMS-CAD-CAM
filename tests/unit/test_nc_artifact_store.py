import json
from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import CamInvariantError
from hms_cadcam.cam.post import (
    ExportOverwritePolicy,
    NCArtifactManifest,
    NCArtifactStatus,
    NCExportDiagnosticCode,
    NCExportService,
)
from hms_cadcam.cam.post.export_store import NCArtifactStoreError
from tests.unit._export_fixtures import production_export_fixture


def _published(tmp_path):
    project = tmp_path / "Store.HMS"
    request, snapshot = production_export_fixture(project)
    service = NCExportService()
    execution = service.export(project, request, snapshot)
    assert execution.accepted and execution.artifact is not None
    return project, request, snapshot, service, execution.artifact


def test_inspect_classifies_missing_and_tampered_without_failing_project(tmp_path) -> None:
    project, request, _, service, entry = _published(tmp_path)
    output = project / entry.output_relative_path
    output.unlink()
    missing = service.store.inspect(project, request.project_id)
    assert missing.entries[0].status is NCArtifactStatus.MISSING
    output.write_bytes(b"tampered")
    tampered = service.store.inspect(project, request.project_id)
    assert tampered.entries[0].status is NCArtifactStatus.TAMPERED


def test_sidecar_missing_and_future_manifest_fail_closed(tmp_path) -> None:
    project, request, _, service, entry = _published(tmp_path)
    (project / entry.metadata_relative_path).unlink()
    with pytest.raises(NCArtifactStoreError) as caught:
        service.store.load(project, request.project_id)
    assert caught.value.code is NCExportDiagnosticCode.SIDECAR_INVALID

    project, request, _, service, _ = _published(tmp_path / "future")
    path = project / "post" / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format_version"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(NCArtifactStoreError) as caught:
        service.store.load(project, request.project_id)
    assert caught.value.code is NCExportDiagnosticCode.MANIFEST_INVALID


def test_manifest_rejects_duplicate_entry_identity(tmp_path) -> None:
    _, request, _, service, entry = _published(tmp_path)
    with pytest.raises(CamInvariantError):
        NCArtifactManifest(request.project_id, (entry, entry))


def test_sidecar_write_failure_rolls_back_output_manifest_and_temp(tmp_path, monkeypatch) -> None:
    project = tmp_path / "Rollback.HMS"
    request, snapshot = production_export_fixture(project)
    service = NCExportService()
    original = service.store._atomic_write_verified
    failed = False

    def fail_sidecar(path, payload, *, expected_sha256=None):
        nonlocal failed
        if path.parent.name == "metadata" and not failed:
            failed = True
            raise OSError("simulated sidecar failure")
        return original(path, payload, expected_sha256=expected_sha256)

    monkeypatch.setattr(service.store, "_atomic_write_verified", fail_sidecar)
    execution = service.export(project, request, snapshot)
    assert not execution.accepted
    assert not (project / "nc" / "runtime_facing.fn").exists()
    assert not (project / "post" / "manifest.json").exists()
    assert not tuple(project.rglob("*.hms-nc-exporting"))


def test_workspace_copy_rewrites_project_identity_and_marks_save_as_stale(tmp_path) -> None:
    project, request, _, service, entry = _published(tmp_path)
    target = tmp_path / "Copy.HMS"
    target.mkdir()
    from uuid import uuid4

    new_project_id = uuid4()
    copied = service.store.copy_workspace(
        project, target, request.project_id, new_project_id
    )
    assert copied.project_id == new_project_id
    assert copied.entries[0].project_id == new_project_id
    assert copied.entries[0].status is NCArtifactStatus.STALE
    assert (target / copied.entries[0].output_relative_path).read_bytes() == (
        project / entry.output_relative_path
    ).read_bytes()
