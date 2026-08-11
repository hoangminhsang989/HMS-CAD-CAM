"""R223 deterministic package inventory and additive persistence integration."""

import json

import pytest

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.qualification import (
    DryRunHandoffPackageBuilder,
    HandoffPackageError,
    OfflineReleaseRecord,
    OfflineReleaseStore,
    OfflineReleaseStoreError,
    PackageStatus,
    current_sources,
    dumps_release_record,
    loads_release_record,
    package_stale_reasons,
)
from tests.unit._stage18a_tranche2_fixtures import acceptance_policy
from tests.unit._stage18a_tranche3_fixtures import BASE_INPUT, BASE_REPORT, release_context


def _build(target):
    _service, payload, setup, _ready, session, candidate, review, ack, assessment = release_context()
    path, digest = DryRunHandoffPackageBuilder().build(
        target, project_name="R223 engineering", program_name="PROGRAM",
        nc_filename="PROGRAM.nc", nc_bytes=payload, contract=BASE_INPUT.machine_contract,
        setup=setup, level1_report=BASE_REPORT, physical_readiness=_ready,
        current_sources=current_sources(payload, setup, BASE_INPUT.machine_contract),
        level2_policy_fingerprint=acceptance_policy().fingerprint,
        session=session, candidate=candidate, review=review,
        acknowledgement=ack, assessment=assessment,
    )
    return path, digest, session, candidate, review, ack, assessment


def test_package_rebuild_is_deterministic_and_operator_readable(tmp_path):
    first, first_sha, *_ = _build(tmp_path / "first")
    second, second_sha, *_ = _build(tmp_path / "second")

    assert first_sha == second_sha
    assert {item.name for item in first.iterdir()} == {item.name for item in second.iterdir()}
    assert all((first / item.name).read_bytes() == (second / item.name).read_bytes() for item in first.iterdir())
    setup_sheet = (first / "setup-sheet.vi.md").read_text(encoding="utf-8")
    assert "MACHINE_READY: KHÔNG" in setup_sheet
    assert "Chưa đủ dữ liệu để xác minh hành trình tuyệt đối trên máy" in setup_sheet
    intake = json.loads((first / "level2-evidence-intake-template.json").read_text(encoding="utf-8"))
    assert intake["release_candidate_fingerprint"]
    assert intake["handoff_package_id"]
    assert not intake["level2_achieved"]


@pytest.mark.parametrize("tamper", ("edit", "remove", "unexpected"))
def test_package_tamper_missing_and_unexpected_files_fail_closed(tmp_path, tamper):
    root, _digest, *_ = _build(tmp_path / "package")
    if tamper == "edit":
        (root / "PROGRAM.nc").write_bytes((root / "PROGRAM.nc").read_bytes() + b"\n")
    elif tamper == "remove":
        (root / "tool-list.csv").unlink()
    else:
        (root / "unexpected.txt").write_text("unexpected", encoding="utf-8")

    with pytest.raises(HandoffPackageError):
        DryRunHandoffPackageBuilder().validate(root)


def test_release_record_round_trip_and_schema5_additive_store(tmp_path):
    root, _digest, session, candidate, review, ack, assessment = _build(tmp_path / "package")
    manifest = json.loads((root / "package-manifest.json").read_text(encoding="utf-8"))
    package_id = ContentFingerprint.from_dict(manifest["package_id"])
    record = OfflineReleaseRecord(
        "r223-release-1", session, candidate, review, ack, assessment,
        package_id, PackageStatus.RELEASED_FOR_EXTERNAL_DRY_RUN, (),
    )
    assert loads_release_record(dumps_release_record(record)) == record

    project = tmp_path / "Persisted.HMS"
    project.mkdir()
    store = OfflineReleaseStore()
    store.save(project, record)
    assert OfflineReleaseStore().load(project) == (record,)
    persisted_manifest = json.loads(
        (project / "post" / "qualification" / "tranche3" / "manifest.json").read_text(encoding="utf-8")
    )
    assert persisted_manifest["sqlite_schema"] == 5


def test_tranche3_persistence_rejects_corrupt_manifest_sidecar_after_reopen(tmp_path):
    root, _digest, session, candidate, review, ack, assessment = _build(tmp_path / "package")
    manifest = json.loads((root / "package-manifest.json").read_text(encoding="utf-8"))
    package_id = ContentFingerprint.from_dict(manifest["package_id"])
    record = OfflineReleaseRecord(
        "r224-release", session, candidate, review, ack, assessment,
        package_id, PackageStatus.RELEASED_FOR_EXTERNAL_DRY_RUN, (),
    )
    project = tmp_path / "Reopen.HMS"
    project.mkdir()
    OfflineReleaseStore().save(project, record)
    sidecar = project / "post" / "qualification" / "tranche3" / "manifest.json.sha256"
    sidecar.write_text("0" * 64 + "  manifest.json\n", encoding="utf-8")
    with pytest.raises(OfflineReleaseStoreError, match="sidecar mismatch"):
        OfflineReleaseStore().load(project)


def test_package_staleness_detects_nc_setup_tool_machine_and_post():
    _service, _payload, _setup, _ready, _session, candidate, *_ = release_context()
    changed = ContentFingerprint.from_payload({"changed": True})
    reasons = package_stale_reasons(
        candidate, nc_sha256="0" * 64, setup_fingerprint=changed,
        tool_set_fingerprint=changed, machine_profile_fingerprint=changed,
        post_fingerprint=changed,
    )
    assert reasons == (
        "NC_CHANGED", "SETUP_CHANGED", "TOOL_SET_CHANGED", "MACHINE_CHANGED", "POST_CHANGED",
    )
