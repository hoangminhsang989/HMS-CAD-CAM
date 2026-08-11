"""Additive Level2 record persistence and deterministic export integration."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.qualification import (
    EvidenceState,
    Tranche2QualificationService,
    Tranche2QualificationStore,
    Tranche2StoreError,
)
from tests.unit._stage18a_tranche2_fixtures import (
    dry_run_attempt,
    level2_record,
    setup_qualification,
)


def project_root(tmp_path):
    root = tmp_path / "R221.HMS"
    root.mkdir()
    return root


def test_record_round_trip_is_deterministic_without_sqlite_migration(tmp_path):
    root = project_root(tmp_path)
    record = level2_record()
    service = Tranche2QualificationService()

    service.save(root, record)
    first = (root / "post" / "qualification" / "level2" / "manifest.json").read_bytes()
    sidecar_first = (
        root / "post" / "qualification" / "level2" / "manifest.json.sha256"
    ).read_bytes()
    restored = service.load(root)
    service.save(root, record)
    second = (root / "post" / "qualification" / "level2" / "manifest.json").read_bytes()
    sidecar_second = (
        root / "post" / "qualification" / "level2" / "manifest.json.sha256"
    ).read_bytes()

    assert restored == (record,)
    assert first == second
    assert sidecar_first == sidecar_second
    assert not (root / "project.db").exists()


def test_fail_then_remediated_attempt_chronology_survives_reload(tmp_path):
    root = project_root(tmp_path)
    setup = setup_qualification()
    failed = dry_run_attempt(setup, result=EvidenceState.FAIL, evidence_id="failed")
    record = level2_record(setup=setup, attempts=(failed,))
    passed = dry_run_attempt(
        setup,
        evidence_id="passed",
        performed_at="2026-08-11T10:20:00+07:00",
        remediation="Corrected clamp position",
    )
    record = record.append_attempt(passed)

    Tranche2QualificationService().save(root, record)
    restored = Tranche2QualificationService().load(root)[0]

    assert [item.evidence_id for item in restored.attempts] == ["failed", "passed"]
    assert [item.result for item in restored.attempts] == [EvidenceState.FAIL, EvidenceState.PASS]


def test_tampered_record_fails_closed_on_manifest_checksum(tmp_path):
    root = project_root(tmp_path)
    record = level2_record()
    store = Tranche2QualificationStore()
    store.save(root, record)
    record_path = next(
        path for path in (root / "post" / "qualification" / "level2").glob("*.json")
        if path.name != "manifest.json"
    )
    record_path.write_bytes(b"tampered")

    with pytest.raises(Tranche2StoreError, match="checksum"):
        store.load(root)


@pytest.mark.parametrize("mutation", ("missing", "corrupt"))
def test_missing_or_corrupt_manifest_sidecar_fails_closed(tmp_path, mutation):
    root = project_root(tmp_path)
    store = Tranche2QualificationStore()
    store.save(root, level2_record())
    sidecar = root / "post" / "qualification" / "level2" / "manifest.json.sha256"
    if mutation == "missing":
        sidecar.unlink()
    else:
        sidecar.write_text("0" * 64 + "  manifest.json\n", encoding="utf-8")

    with pytest.raises(Tranche2StoreError, match="sidecar"):
        store.load(root)


def test_setup_drift_writes_new_snapshot_and_updates_exact_manifest_binding(tmp_path):
    root = project_root(tmp_path)
    service = Tranche2QualificationService()
    record = level2_record()
    service.save(root, record)
    changed_setup = replace(
        record.setup,
        work_offset_transform=replace(
            record.setup.work_offset_transform,
            translation_mm=replace(record.setup.work_offset_transform.translation_mm, x=105.0),
        ),
    )
    changed = replace(record, setup=changed_setup)
    service.save(root, changed)

    restored = service.load(root)
    snapshots = tuple(
        path for path in (root / "post" / "qualification" / "level2").glob("*.json")
        if path.name != "manifest.json"
    )
    assert restored == (changed,)
    assert len(snapshots) == 2


def test_exported_verification_package_and_sidecar_are_deterministic(tmp_path):
    record = level2_record()
    target = tmp_path / "r221-level2-package.json"
    service = Tranche2QualificationService()

    _, first_digest = service.export_package(record, target)
    first = target.read_bytes()
    _, second_digest = service.export_package(record, target)

    assert target.read_bytes() == first
    assert first_digest == second_digest
    assert target.with_suffix(".json.sha256").read_text(encoding="utf-8") == (
        f"{first_digest}  {target.name}\n"
    )
