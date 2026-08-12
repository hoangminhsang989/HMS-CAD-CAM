"""R239 Phase-1 production activation workflow and fail-closed gates."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.post_studio import (
    ActivationWindowStatus,
    AttributableApproval,
    DeploymentPlan,
    ElevatedActivationVerifier,
    EvidenceFreshness,
    EvidenceFreshnessStatus,
    Phase2GateStatus,
    PostMachineBinding,
    ProductionActivationDecision,
    ProductionActivationWorkflow,
    ProductionWorkflowStore,
    ProductionDecision,
    ProductionDecisionStatus,
    RollbackReadinessStatus,
    TargetReconciliation,
)


AT = "2026-08-12T13:00:00+07:00"
START = "2026-08-12T14:00:00+07:00"
END = "2026-08-12T15:00:00+07:00"
PARENT = b"FANUC-SHL\r\n#56\r\nA\r\nB\r\nC\r\n"
CANDIDATE = b"FANUC-SHL\r\n#56\r\nA\r\nB\r\nC\r\nG40\r\n"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _context(target: Path):
    binding = PostMachineBinding("fanuc_robodrill_alpha_d21mib", "fanuc_31i_b", "BT30", "FANUC-SHL", ContentFingerprint.from_payload({"machine": "D21MiB", "controller": "31i-B"}))
    validation = ContentFingerprint.from_payload({"validator": "r233", "version": 1, "result": "PASS"})
    regression = ContentFingerprint.from_payload({"corpus": "r233-production-nc", "result": "PASS", "unexpected": 0})
    policy = ContentFingerprint.from_payload({"policy": "r239-phase1", "production_write": False})
    approval = AttributableApproval("r238.lineage.owner", AT, "fanuc-shl.r233-g40", _sha(CANDIDATE), binding, validation, regression, "APPROVE", "R238-LINEAGE-1")
    plan = DeploymentPlan("deploy.r239.fanuc-shl", "fanuc-shl", "fanuc-shl.r233-g40", _sha(CANDIDATE), "fanuc-shl.original", _sha(PARENT), str(target), binding, approval, validation, regression, policy, AT, "r239.workflow")
    binding_fp = ContentFingerprint.from_payload(binding.to_dict())
    freshness = EvidenceFreshness(_sha(CANDIDATE), validation, validation, regression, regression, binding_fp, binding_fp, policy, policy, True, True)
    rollback = ProductionActivationWorkflow.rollback_plan(plan, PARENT)
    return binding, validation, regression, policy, plan, freshness, rollback


def _decision(plan, rollback, decision: ProductionDecision = ProductionDecision.APPROVE_ACTIVATION_WINDOW):
    return ProductionActivationDecision(
        "decision.r239.owner", plan.post_id, plan.candidate_revision_id, plan.candidate_sha256,
        plan.expected_current_revision_id, plan.expected_current_sha256, plan.target_path,
        plan.expected_current_sha256, plan.machine_binding, plan.validation_fingerprint,
        plan.regression_fingerprint, plan.approval.fingerprint, plan.fingerprint,
        rollback.fingerprint, plan.policy_fingerprint, "owner.production", AT, decision,
        "R239-PRODUCTION-DECISION-1",
    )


def test_target_snapshot_is_deterministic_and_becomes_stale(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(PARENT)
    first = ProductionActivationWorkflow.capture_snapshot(target, expected_parent_sha256=_sha(PARENT), captured_at=AT)
    second = ProductionActivationWorkflow.capture_snapshot(target, expected_parent_sha256=_sha(PARENT), captured_at=AT)
    assert first == second
    assert first.reconciliation_state is TargetReconciliation.TARGET_MATCHES_EXPECTED_PARENT
    assert not first.is_stale()
    target.write_bytes(b"external drift")
    assert first.is_stale()


def test_final_preflight_is_read_only_and_reports_capabilities(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(PARENT); before = target.read_bytes()
    backup = tmp_path / "managed-backups"; backup.mkdir()
    result = ProductionActivationWorkflow.final_preflight(target, expected_parent_sha256=_sha(PARENT), rollback_source=PARENT, backup_root=backup, lock_path=tmp_path / "none.lock", captured_at=AT)
    assert result.status == "PASS_READ_ONLY"
    assert result.rollback_source_available
    assert "RUNAS" in result.atomic_replacement_capability_assessment
    assert target.read_bytes() == before


def test_freshness_requires_exact_validation_regression_binding_and_policy() -> None:
    fp = ContentFingerprint.from_payload({"same": 1}); stale = ContentFingerprint.from_payload({"stale": 1})
    fresh = EvidenceFreshness(_sha(CANDIDATE), fp, fp, fp, fp, fp, fp, fp, fp, True, True)
    assert fresh.status is EvidenceFreshnessStatus.FRESH
    assert EvidenceFreshness(_sha(CANDIDATE), stale, fp, fp, fp, fp, fp, fp, fp, True, True).status is EvidenceFreshnessStatus.REVALIDATION_REQUIRED
    assert EvidenceFreshness(_sha(CANDIDATE), fp, fp, stale, fp, fp, fp, fp, fp, True, True).status is EvidenceFreshnessStatus.REVALIDATION_REQUIRED


def test_owner_decision_is_attributable_fingerprinted_and_has_no_default_approval(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; binding, validation, regression, policy, plan, freshness, rollback = _context(target)
    undecided = _decision(plan, rollback, ProductionDecision.NOT_DECIDED)
    approved = _decision(plan, rollback)
    assert undecided.status is ProductionDecisionStatus.DRAFT
    assert approved.status is ProductionDecisionStatus.ATTRIBUTABLE
    assert undecided.fingerprint != approved.fingerprint
    with pytest.raises(CamValidationError, match="timezone"):
        ProductionActivationDecision("decision.bad.time", plan.post_id, plan.candidate_revision_id, plan.candidate_sha256, plan.expected_current_revision_id, plan.expected_current_sha256, plan.target_path, plan.expected_current_sha256, binding, validation, regression, plan.approval.fingerprint, plan.fingerprint, rollback.fingerprint, policy, "owner", "2026-08-12T13:00:00", ProductionDecision.DEFER_ACTIVATION, "R239-1")


def test_decision_mismatch_cannot_create_window(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(PARENT)
    _binding, _validation, _regression, _policy, plan, _freshness, rollback = _context(target)
    snapshot = ProductionActivationWorkflow.capture_snapshot(target, expected_parent_sha256=plan.expected_current_sha256, captured_at=AT)
    deferred = _decision(plan, rollback, ProductionDecision.DEFER_ACTIVATION)
    with pytest.raises(CamInvariantError, match="fresh attributable"):
        ProductionActivationWorkflow.create_window(window_id="window.r239", plan=plan, snapshot=snapshot, decision=deferred, approved_from=START, expires_at=END, actor_identity="owner.production")


def test_activation_window_expiry_invalidation_and_consumption(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(PARENT)
    _binding, _validation, _regression, _policy, plan, _freshness, rollback = _context(target)
    snapshot = ProductionActivationWorkflow.capture_snapshot(target, expected_parent_sha256=plan.expected_current_sha256, captured_at=AT)
    decision = _decision(plan, rollback)
    window = ProductionActivationWorkflow.create_window(window_id="window.r239", plan=plan, snapshot=snapshot, decision=decision, approved_from=START, expires_at=END, actor_identity="owner.production")
    assert window.status is ActivationWindowStatus.NOT_AUTHORIZED
    authorized = window.evaluate(now="2026-08-12T14:30:00+07:00", snapshot=snapshot, candidate_sha256=plan.candidate_sha256, decision=decision)
    assert authorized.status is ActivationWindowStatus.AUTHORIZED
    assert window.evaluate(now=END, snapshot=snapshot, candidate_sha256=plan.candidate_sha256, decision=decision).status is ActivationWindowStatus.EXPIRED
    consumed = authorized.consume(); assert consumed.status is ActivationWindowStatus.CONSUMED
    target.write_bytes(b"drift")
    assert window.evaluate(now="2026-08-12T14:30:00+07:00", snapshot=snapshot, candidate_sha256=plan.candidate_sha256, decision=decision).status is ActivationWindowStatus.INVALIDATED


def test_phase1_vs_phase2_gate_and_rollback_readiness(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(PARENT)
    _binding, _validation, _regression, _policy, plan, freshness, rollback = _context(target)
    undecided = _decision(plan, rollback, ProductionDecision.NOT_DECIDED)
    assert rollback.status is RollbackReadinessStatus.ROLLBACK_READY
    assert ProductionActivationWorkflow.phase2_gate(freshness=freshness, rollback=rollback, decision=undecided, window=None) is Phase2GateStatus.SAN_SANG_CHO_PHE_DUYET_KICH_HOAT
    missing = ProductionActivationWorkflow.rollback_plan(plan, None)
    assert missing.status is RollbackReadinessStatus.EXACT_PARENT_UNAVAILABLE
    assert ProductionActivationWorkflow.phase2_gate(freshness=freshness, rollback=missing, decision=undecided, window=None) is Phase2GateStatus.CHUA_DUOC_PHEP_KICH_HOAT


def test_activation_package_is_deterministic_and_never_auto_activates(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(PARENT)
    _binding, validation, regression, _policy, plan, freshness, rollback = _context(target)
    snapshot = ProductionActivationWorkflow.capture_snapshot(target, expected_parent_sha256=plan.expected_current_sha256, captured_at=AT)
    kwargs = dict(plan=plan, rollback=rollback, snapshot=snapshot, freshness=freshness, candidate_bytes=CANDIDATE, parent_bytes=PARENT, generated_nc_bytes=b"G41\r\nG40\r\nM09\r\n", validation_evidence={"fingerprint": validation.to_dict(), "result": "PASS"}, regression_evidence={"fingerprint": regression.to_dict(), "result": "PASS", "unexpected_changes": 0}, diff_summary={"section": 56, "block_count_before": 3, "block_count_after": 4, "added": ["G40"], "deployment_state": "NOT_ACTIVE_GLOBALLY"})
    one = ProductionActivationWorkflow.build_package(tmp_path / "one.zip", **kwargs)
    two = ProductionActivationWorkflow.build_package(tmp_path / "two.zip", **kwargs)
    assert one.package_sha256 == two.package_sha256
    assert one.manifest_fingerprint == two.manifest_fingerprint
    assert one.manifest["auto_activate_on_import"] is False
    assert one.deployment_state == "NOT_ACTIVE_GLOBALLY"


def test_elevated_helper_independently_rehashes_and_rejects_toctou(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(PARENT)
    _binding, _validation, _regression, _policy, plan, _freshness, rollback = _context(target)
    snapshot = ProductionActivationWorkflow.capture_snapshot(target, expected_parent_sha256=plan.expected_current_sha256, captured_at=AT)
    decision = _decision(plan, rollback)
    window = ProductionActivationWorkflow.create_window(window_id="window.r239", plan=plan, snapshot=snapshot, decision=decision, approved_from=START, expires_at=END, actor_identity="owner.production")
    result = ElevatedActivationVerifier.verify(target=target, candidate_bytes=CANDIDATE, plan=plan, snapshot=snapshot, rollback=rollback, decision=decision, window=window, now="2026-08-12T14:30:00+07:00")
    assert result["verified"] and result["write_performed"] is False
    target.write_bytes(b"TOCTOU external edit")
    rejected = ElevatedActivationVerifier.verify(target=target, candidate_bytes=CANDIDATE, plan=plan, snapshot=snapshot, rollback=rollback, decision=decision, window=window, now="2026-08-12T14:30:00+07:00")
    assert not rejected["verified"]
    assert not rejected["checks"]["target_current_sha256"]


def test_phase1_audit_store_is_append_only(tmp_path: Path) -> None:
    store = ProductionWorkflowStore(tmp_path / "project")
    payload = {"format": "HMS_POST_TARGET_SNAPSHOT", "format_version": 1, "state": "READ_ONLY"}
    first = store.write_record("audit.r239", "target-snapshot.json", payload)
    assert first.read_bytes()
    assert store.write_record("audit.r239", "target-snapshot.json", payload) == first
    with pytest.raises(CamInvariantError, match="conflicts"):
        store.write_record("audit.r239", "target-snapshot.json", {**payload, "state": "CHANGED"})
