"""R240 active lifecycle reconstruction, drift, backup and export tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.post_studio import ActiveLifecyclePaths, ActiveLifecycleService, ManagedActiveStatus


PARENT = b"original post"
ACTIVE = b"active R233 G40 post"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _fixture(tmp_path: Path) -> ActiveLifecyclePaths:
    root = tmp_path / "records"; target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(ACTIVE)
    activation = {"format": "HMS_POST_PRODUCTION_ACTIVATION_RECORD", "target_path": str(target), "deployment_id": "deploy.r240", "window_id": "window.r240", "deployment_plan_fingerprint": "plan-fp", "rollback_plan_fingerprint": "rollback-fp", "decision_fingerprint": "decision-fp", "new_revision_id": "fanuc-shl.r233-g40", "new_sha256": _sha(ACTIVE), "previous_revision_id": "fanuc-shl.original", "previous_sha256": _sha(PARENT), "activated_at": "2026-08-12T14:02:28+07:00", "actor": "Sáng Hoàng Minh"}
    activation_path = root / "activation.json"; _write(activation_path, activation)
    activation_sha = _sha(activation_path.read_bytes())
    consumed = {"format": "HMS_POST_ACTIVATION_WINDOW", "window_id": "window.r240", "status": "CONSUMED", "activation_record_sha256": activation_sha}
    deployment = {"format": "HMS_POST_DEPLOYMENT_PLAN", "candidate_sha256": _sha(ACTIVE), "expected_current_sha256": _sha(PARENT), "plan_fingerprint": {"digest": "plan-fp"}, "machine_binding": {"tool_interface": "BT30"}}
    rollback = {"format": "HMS_POST_ROLLBACK_PLAN", "restore_sha256": _sha(PARENT), "expected_active_sha256": _sha(ACTIVE), "status": "ROLLBACK_READY", "rollback_plan_fingerprint": {"digest": "rollback-fp"}}
    decision = {"format": "HMS_POST_PRODUCTION_ACTIVATION_DECISION", "decision": "APPROVE_ACTIVATION_WINDOW", "target_path": str(target), "decision_fingerprint": {"digest": "decision-fp"}}
    for name, value in (("consumed.json", consumed), ("deployment.json", deployment), ("rollback.json", rollback), ("decision.json", decision)):
        _write(root / name, value)
    backup = root / "backup.dat"; backup.write_bytes(PARENT)
    return ActiveLifecyclePaths(target, activation_path, root / "consumed.json", root / "deployment.json", root / "rollback.json", root / "decision.json", backup, root / "deployment.lock")


def test_restart_reconstructs_managed_active_state_from_bytes_and_records(tmp_path: Path) -> None:
    paths = _fixture(tmp_path); projection = ActiveLifecycleService().reconstruct(paths)
    assert projection.status is ManagedActiveStatus.ACTIVE_MANAGED_REVISION
    assert projection.active_revision_id == "fanuc-shl.r233-g40"
    assert projection.active_sha256 == _sha(ACTIVE)
    assert projection.rollback_ready and projection.window_consumed
    assert not projection.can_reuse_activation_window


def test_external_drift_is_projected_from_target_bytes_not_metadata(tmp_path: Path) -> None:
    paths = _fixture(tmp_path); paths.target.write_bytes(b"external edit")
    projection = ActiveLifecycleService().reconstruct(paths)
    assert projection.status is ManagedActiveStatus.POST_DA_BI_THAY_DOI_NGOAI_HMS
    assert projection.drift_detected and not projection.rollback_ready is False


def test_missing_or_wrong_backup_blocks_rollback_readiness(tmp_path: Path) -> None:
    paths = _fixture(tmp_path); paths.backup.write_bytes(b"wrong")
    projection = ActiveLifecycleService().reconstruct(paths)
    assert projection.status is ManagedActiveStatus.ACTIVE_MANAGED_REVISION
    assert not projection.rollback_ready


def test_tampered_record_link_requires_reconciliation(tmp_path: Path) -> None:
    paths = _fixture(tmp_path); consumed = json.loads(paths.consumed_window_record.read_text(encoding="utf-8")); consumed["activation_record_sha256"] = "0" * 64; _write(paths.consumed_window_record, consumed)
    assert ActiveLifecycleService().reconstruct(paths).status is ManagedActiveStatus.RECORD_RECONCILIATION_REQUIRED


def test_tampered_cross_record_deployment_identity_requires_reconciliation(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    rollback = json.loads(paths.rollback_plan_record.read_text(encoding="utf-8"))
    rollback["deployment_plan_id"] = "deploy.foreign"
    _write(paths.rollback_plan_record, rollback)
    assert ActiveLifecycleService().reconstruct(paths).status is ManagedActiveStatus.RECORD_RECONCILIATION_REQUIRED


def test_real_r239_records_reconstruct_current_production_state() -> None:
    phase2 = Path(r"E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE\R239_PHASE2_PRODUCTION_ACTIVATION_SANG_HOANG_MINH_20260812")
    owner = Path(r"E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE\R239_OWNER_ACTIVATION_WINDOW_SANG_HOANG_MINH_20260812\owner-window.HMS\post\studio\production-activation\owner-window.r239.fanuc-shl")
    target = Path(r"C:\ProgramData\WORKNC\2021.0\pospro\FANUC-SHL.dat")
    backup = Path(r"C:\ProgramData\HMS-CADCAM\PostStudio\production-backups\fanuc-shl\deploy.r239.fanuc-shl.owner-window\fanuc-shl__fanuc-shl.original__d0aa7518d669283be8aad6e92ffdec4dae8785abb7fdb2895cac0ab46cb51da3__deploy.r239.fanuc-shl.owner-window.dat")
    required = (phase2 / "08_ACTIVATION_RECORD.json", phase2 / "09_WINDOW_CONSUMED.json", owner / "deployment-plan.json", owner / "rollback-plan.json", owner / "owner-decision.json", target, backup)
    if not all(path.is_file() for path in required):
        pytest.skip("R239 production activation records are unavailable")
    paths = ActiveLifecyclePaths(target, required[0], required[1], required[2], required[3], required[4], backup, Path(r"C:\ProgramData\HMS-CADCAM\PostStudio\locks\fanuc-shl-production.lock"))
    projection = ActiveLifecycleService().reconstruct(paths)
    assert projection.status is ManagedActiveStatus.ACTIVE_MANAGED_REVISION
    assert projection.active_sha256 == "1160411dea6a5f104085747b4deac151fbd6b103b5930f39b11e8be358b67039"
    assert projection.backup_sha256 == "d0aa7518d669283be8aad6e92ffdec4dae8785abb7fdb2895cac0ab46cb51da3"
    assert projection.rollback_ready and projection.window_consumed and not projection.lock_present


def test_active_history_package_is_deterministic_and_import_never_activates(tmp_path: Path) -> None:
    paths = _fixture(tmp_path); service = ActiveLifecycleService(); projection = service.reconstruct(paths)
    kwargs = dict(projection=projection, paths=paths, original_bytes=PARENT, active_bytes=ACTIVE, validation={"result": "PASS"}, regression={"result": "PASS", "unexpected": 0})
    one = service.export_active_history(tmp_path / "one.zip", **kwargs); two = service.export_active_history(tmp_path / "two.zip", **kwargs)
    assert one["sha256"] == two["sha256"]
    with ZipFile(one["path"]) as archive:
        manifest = json.loads(archive.read("package-manifest.json"))
    assert manifest["auto_activate_on_import"] is False
    assert manifest["imported_active_state_is_informational"] is True
    assert manifest["requires_local_reconciliation_and_approval"] is True
    before = paths.target.read_bytes()
    imported = service.import_active_history(Path(one["path"]))
    assert imported.informational_only and not imported.auto_activate
    assert imported.requires_local_reconciliation_and_approval
    assert imported.active_sha256 == _sha(ACTIVE)
    assert paths.target.read_bytes() == before


def test_active_history_import_rejects_unregistered_archive_entry(tmp_path: Path) -> None:
    paths = _fixture(tmp_path); service = ActiveLifecycleService(); projection = service.reconstruct(paths)
    package = service.export_active_history(tmp_path / "history.zip", projection=projection, paths=paths, original_bytes=PARENT, active_bytes=ACTIVE, validation={"result": "PASS"}, regression={"result": "PASS"})
    with ZipFile(package["path"], "a") as archive:
        archive.writestr("unregistered.txt", b"not in manifest")
    with pytest.raises(CamValidationError, match="unregistered"):
        service.import_active_history(Path(package["path"]))
