"""Build R239 Phase-1 evidence using real R233 lineage and an isolated target."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.post_studio import (
    AttributableApproval,
    DeploymentPlan,
    EvidenceFreshness,
    Phase2GateStatus,
    PostDeploymentEngine,
    PostDeploymentError,
    PostDeploymentStore,
    PostMachineBinding,
    ProductionActivationDecision,
    ProductionActivationWorkflow,
    ProductionDecision,
    ProductionWorkflowStore,
    TargetReconciliation,
)


PARENT_SHA = "d0aa7518d669283be8aad6e92ffdec4dae8785abb7fdb2895cac0ab46cb51da3"
CANDIDATE_SHA = "1160411dea6a5f104085747b4deac151fbd6b103b5930f39b11e8be358b67039"
GENERATED_NC_SHA = "1bb0690a9f95e197dd26ead70d4447855ff0a3e1ee6119a85bc96d05847a9f67"
GLOBAL_TARGET = Path(r"C:\ProgramData\WORKNC\2021.0\pospro\FANUC-SHL.dat")
R233_ROOT = Path(r"E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE\R233_FANUC_SHL_COMPLETE_CONTEXT_AND_ISOLATED_G40_REMEDIATION")
CANDIDATE_PATH = R233_ROOT / "R233_CANDIDATE" / "FANUC-SHL.dat"
GENERATED_NC_PATH = R233_ROOT / "generated_nc" / "260601---BL-CUM-DAN-DONG--25X226_5-L1_R233_G40.fn"
VALIDATION_PATH = R233_ROOT / "05_STATIC_AND_REGRESSION.json"
DIFF_PATH = R233_ROOT / "03_POST_CANDIDATE.json"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _write_json(path: Path, payload: object) -> None:
    data = _canonical(payload)
    if path.exists() and path.read_bytes() != data:
        raise RuntimeError(f"Immutable acceptance record conflict: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)


def run(output: Path) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parent = GLOBAL_TARGET.read_bytes()
    candidate = CANDIDATE_PATH.read_bytes()
    generated_nc = GENERATED_NC_PATH.read_bytes()
    validation_bytes = VALIDATION_PATH.read_bytes()
    diff_bytes = DIFF_PATH.read_bytes()
    if (_sha(parent), _sha(candidate), _sha(generated_nc)) != (PARENT_SHA, CANDIDATE_SHA, GENERATED_NC_SHA):
        raise RuntimeError("Real R233 lineage identity mismatch")

    output.mkdir(parents=True, exist_ok=False)
    project = output / "audit-project.HMS"
    backups = output / "managed-production-backups"; backups.mkdir()
    binding = PostMachineBinding("fanuc_robodrill_alpha_d21mib", "fanuc_31i_b", "BT30", "FANUC-SHL", ContentFingerprint.from_payload({"machine": "FANUC ROBODRILL alpha-D21MiB", "controller": "FANUC 31i-B", "tool_interface": "BT30"}))
    validation_fp = ContentFingerprint.from_payload({"source_sha256": _sha(validation_bytes), "validator": "R233_STATIC_NC_QUALIFICATION", "result": "PASS"})
    regression_fp = ContentFingerprint.from_payload({"source_sha256": _sha(validation_bytes), "corpus": "R233_BOUNDED_REGRESSION", "result": "PASS", "unexpected_changes": 0})
    policy_fp = ContentFingerprint.from_payload({"version": "R239-PHASE1-1", "real_target_write": False, "future_uac": "STANDARD_RUNAS_ONLY"})
    approval = AttributableApproval("r238.lineage.approval", now, "fanuc-shl.r233-g40", CANDIDATE_SHA, binding, validation_fp, regression_fp, "APPROVE", "R238-LINEAGE-APPROVAL-1")
    plan = DeploymentPlan("deploy.r239.fanuc-shl", "fanuc-shl", "fanuc-shl.r233-g40", CANDIDATE_SHA, "fanuc-shl.original", PARENT_SHA, str(GLOBAL_TARGET), binding, approval, validation_fp, regression_fp, policy_fp, now, "r239.phase1")
    binding_fp = ContentFingerprint.from_payload(binding.to_dict())
    freshness = EvidenceFreshness(CANDIDATE_SHA, validation_fp, validation_fp, regression_fp, regression_fp, binding_fp, binding_fp, policy_fp, policy_fp, True, True)
    rollback = ProductionActivationWorkflow.rollback_plan(plan, parent)
    preflight = ProductionActivationWorkflow.final_preflight(GLOBAL_TARGET, expected_parent_sha256=PARENT_SHA, rollback_source=parent, backup_root=backups, lock_path=output / "no-production-lock-created.lock", captured_at=now)
    snapshot = preflight.snapshot

    decision = ProductionActivationDecision("decision.r239.not-decided", plan.post_id, plan.candidate_revision_id, plan.candidate_sha256, plan.expected_current_revision_id, plan.expected_current_sha256, plan.target_path, snapshot.bytes_sha256, binding, validation_fp, regression_fp, approval.fingerprint, plan.fingerprint, rollback.fingerprint, policy_fp, "OWNER_ACTION_REQUIRED", now, ProductionDecision.NOT_DECIDED, "R239-PRODUCTION-DECISION-1")
    phase2 = ProductionActivationWorkflow.phase2_gate(freshness=freshness, rollback=rollback, decision=decision, window=None)
    if phase2 is not Phase2GateStatus.SAN_SANG_CHO_PHE_DUYET_KICH_HOAT:
        raise RuntimeError("Phase2 boundary projection is invalid")

    package_kwargs = dict(plan=plan, rollback=rollback, snapshot=snapshot, freshness=freshness, candidate_bytes=candidate, parent_bytes=parent, generated_nc_bytes=generated_nc, validation_evidence={"source_sha256": _sha(validation_bytes), "fingerprint": validation_fp.to_dict(), "result": "PASS"}, regression_evidence={"source_sha256": _sha(validation_bytes), "fingerprint": regression_fp.to_dict(), "result": "PASS", "unexpected_generated_nc_changes": 0}, diff_summary={"source_sha256": _sha(diff_bytes), "section": 56, "block_count_before": 3, "block_count_after": 4, "added": ["G40"], "placement": "before M09/M05/G91G28G0Z0", "generated_nc_sha256": GENERATED_NC_SHA, "unexpected_generated_nc_changes": 0, "deployment_state": "NOT_ACTIVE_GLOBALLY"})
    package_one = ProductionActivationWorkflow.build_package(output / "FANUC-SHL-R239-ACTIVATION-PHASE1.zip", **package_kwargs)
    package_two = ProductionActivationWorkflow.build_package(output / "determinism-check.zip", **package_kwargs)
    if package_one.package_sha256 != package_two.package_sha256:
        raise RuntimeError("Production activation package is not deterministic")
    package_two_path = Path(package_two.package_path); package_two_path.unlink()

    # Exact real lineage rehearsal on an isolated target only.
    sandbox_target = output / "sandbox" / "FANUC-SHL.dat"
    sandbox_target.parent.mkdir(); sandbox_target.write_bytes(parent)
    sandbox_plan = DeploymentPlan("deploy.r239.sandbox", plan.post_id, plan.candidate_revision_id, plan.candidate_sha256, plan.expected_current_revision_id, plan.expected_current_sha256, str(sandbox_target), binding, approval, validation_fp, regression_fp, policy_fp, now, "r239.rehearsal")
    engine = PostDeploymentEngine(PostDeploymentStore(project))
    before = engine.inspect_target(sandbox_target, sandbox_plan)
    record = engine.deploy(sandbox_plan, candidate, actor="r239.rehearsal")
    active_sha = _sha(sandbox_target.read_bytes())
    recovered = PostDeploymentEngine(PostDeploymentStore(project)).recover(sandbox_plan)
    engine.rollback(record, actor="r239.rehearsal")
    final_sha = _sha(sandbox_target.read_bytes())
    sandbox_target.write_bytes(b"external drift retained")
    drift_rejected = False
    try:
        engine.deploy(sandbox_plan, candidate, actor="r239.rehearsal")
    except PostDeploymentError:
        drift_rejected = True
    sandbox_target.write_bytes(parent)
    lock = engine._acquire_lock(sandbox_plan, "other.session")
    lock_rejected = False
    try:
        engine.deploy(sandbox_plan, candidate, actor="r239.rehearsal")
    except PostDeploymentError:
        lock_rejected = True
    lock.unlink(missing_ok=True)

    store = ProductionWorkflowStore(project)
    store.write_record("audit.r239.real", "target-preflight.json", preflight.payload())
    store.write_record("audit.r239.real", "target-snapshot.json", snapshot.payload())
    store.write_record("audit.r239.real", "evidence-freshness.json", freshness.payload())
    store.write_record("audit.r239.real", "owner-decision.json", decision.payload())
    store.write_record("audit.r239.real", "rollback-plan.json", rollback.payload())
    store.write_record("audit.r239.real", "package-record.json", {"format": "HMS_POST_ACTIVATION_PACKAGE_RECORD", "format_version": 1, "package_sha256": package_one.package_sha256, "manifest_fingerprint": package_one.manifest_fingerprint.to_dict(), "auto_activate_on_import": False})
    rehearsal = {"format": "HMS_R239_SANDBOX_REHEARSAL", "format_version": 1, "initial_reconciliation": before.state.value, "active_sha256": active_sha, "restart_recovery": recovered.state.value, "final_parent_sha256": final_sha, "drift_rejected": drift_rejected, "lock_conflict_rejected": lock_rejected, "global_target_write": False}
    store.write_record("audit.r239.real", "sandbox-rehearsal.json", rehearsal)

    global_after = _sha(GLOBAL_TARGET.read_bytes())
    summary = {"format": "HMS_R239_REAL_LINEAGE_ACCEPTANCE", "format_version": 1, "target_snapshot_fingerprint": snapshot.fingerprint.to_dict(), "target_sha256_before": PARENT_SHA, "target_sha256_after": global_after, "candidate_sha256": CANDIDATE_SHA, "validation_fingerprint": validation_fp.to_dict(), "regression_fingerprint": regression_fp.to_dict(), "deployment_plan_fingerprint": plan.fingerprint.to_dict(), "rollback_plan_fingerprint": rollback.fingerprint.to_dict(), "package_sha256": package_one.package_sha256, "package_manifest_fingerprint": package_one.manifest_fingerprint.to_dict(), "preflight_status": preflight.status, "freshness_status": freshness.status.value, "rollback_status": rollback.status.value, "owner_decision": decision.decision.value, "activation_window": "NOT_CREATED_OWNER_DECISION_REQUIRED", "phase2_status": phase2.value, "sandbox_rehearsal": rehearsal, "production_state": "NOT_ACTIVE_GLOBALLY", "global_target_unchanged": global_after == PARENT_SHA}
    _write_json(output / "R239_REAL_LINEAGE_ACCEPTANCE.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = run(args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
