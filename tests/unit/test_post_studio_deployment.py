"""R238 exact-byte activation, recovery and rollback tests on isolated targets."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.post_studio import (
    AttributableApproval, DeploymentFault, DeploymentPlan, DeploymentTransactionState,
    PostDeploymentEngine, PostDeploymentError, PostDeploymentStore, PostMachineBinding,
    PRODUCTION_FANUC_SHL_PATH, TargetReconciliation,
)


AT = "2026-08-12T12:00:00+07:00"
PARENT = b"FANUC-SHL\r\nG41D1\r\nM09\r\n"
CANDIDATE = b"FANUC-SHL\r\nG41D1\r\nG40\r\nM09\r\n"


def _plan(target: Path, *, deployment_id: str = "deploy.r238.test") -> DeploymentPlan:
    binding = PostMachineBinding("fanuc_robodrill_alpha_d21mib", "fanuc_31i_b", "BT30", "FANUC-SHL", ContentFingerprint.from_payload({"machine": "D21MiB"}))
    validation = ContentFingerprint.from_payload({"validation": "r233"})
    regression = ContentFingerprint.from_payload({"regression": "r233"})
    approval = AttributableApproval("owner.r238", AT, "fanuc-shl.r233-g40", hashlib.sha256(CANDIDATE).hexdigest(), binding, validation, regression, "APPROVE", "R238-APPROVAL-1")
    return DeploymentPlan(deployment_id, "fanuc-shl", "fanuc-shl.r233-g40", hashlib.sha256(CANDIDATE).hexdigest(), "fanuc-shl.original", hashlib.sha256(PARENT).hexdigest(), str(target), binding, approval, validation, regression, ContentFingerprint.from_payload({"policy": "isolated-only"}), AT, "r238.test")


def test_isolated_activation_backup_and_exact_rollback(tmp_path: Path) -> None:
    target = tmp_path / "isolated" / "FANUC-SHL.dat"; target.parent.mkdir(); target.write_bytes(PARENT)
    plan = _plan(target); engine = PostDeploymentEngine(PostDeploymentStore(tmp_path / "project"))
    assert engine.inspect_target(target, plan).state is TargetReconciliation.TARGET_MATCHES_EXPECTED_PARENT
    record = engine.deploy(plan, CANDIDATE, actor="r238.test")
    assert target.read_bytes() == CANDIDATE
    assert Path(record.backup_path).read_bytes() == PARENT
    assert record.new_sha256 == hashlib.sha256(CANDIDATE).hexdigest()
    engine.rollback(record, actor="r238.test")
    assert target.read_bytes() == PARENT


def test_unknown_drift_is_rejected_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(b"external bytes")
    plan = _plan(target); engine = PostDeploymentEngine(PostDeploymentStore(tmp_path / "project"))
    assert engine.inspect_target(target, plan).state is TargetReconciliation.TARGET_UNKNOWN_REVISION
    with pytest.raises(PostDeploymentError, match="TARGET_UNKNOWN_REVISION"):
        engine.deploy(plan, CANDIDATE, actor="r238.test")
    assert target.read_bytes() == b"external bytes"


def test_reconciliation_distinguishes_missing_unreadable_candidate_and_readiness(tmp_path: Path) -> None:
    missing = tmp_path / "missing.dat"; plan = _plan(missing); engine = PostDeploymentEngine(PostDeploymentStore(tmp_path / "project"))
    assert engine.inspect_target(missing, plan).state is TargetReconciliation.TARGET_MISSING
    directory_target = tmp_path / "directory-target"; directory_target.mkdir()
    assert engine.inspect_target(directory_target, _plan(directory_target, deployment_id="deploy.r238.unreadable")).state is TargetReconciliation.TARGET_UNREADABLE
    candidate_target = tmp_path / "candidate.dat"; candidate_target.write_bytes(CANDIDATE)
    assert engine.inspect_target(candidate_target, _plan(candidate_target, deployment_id="deploy.r238.candidate")).state is TargetReconciliation.TARGET_ALREADY_CANDIDATE
    parent_target = tmp_path / "parent.dat"; parent_target.write_bytes(PARENT)
    parent_plan = _plan(parent_target, deployment_id="deploy.r238.readiness")
    readiness = engine.readiness(parent_plan, engine.production_read_only_preflight(parent_plan))
    assert readiness.readiness_state == "READY_FOR_OWNER_ACTIVATION_DECISION"
    assert readiness.observed_target_sha256 == hashlib.sha256(PARENT).hexdigest()


@pytest.mark.parametrize("fault", list(DeploymentFault))
def test_fault_injection_is_fail_closed_and_recovery_rehashes(tmp_path: Path, fault: DeploymentFault) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(PARENT)
    plan = _plan(target, deployment_id=f"deploy.r238.{fault.value.lower()}")
    engine = PostDeploymentEngine(PostDeploymentStore(tmp_path / "project"), fault=fault)
    with pytest.raises(PostDeploymentError):
        engine.deploy(plan, CANDIDATE, actor="r238.test")
    recovered = PostDeploymentEngine(PostDeploymentStore(tmp_path / "project")).recover(plan)
    assert recovered.state in {TargetReconciliation.TARGET_MATCHES_EXPECTED_PARENT, TargetReconciliation.TARGET_ALREADY_CANDIDATE}


def test_production_global_target_is_hard_refused_for_mutation(tmp_path: Path) -> None:
    plan = _plan(PRODUCTION_FANUC_SHL_PATH)
    with pytest.raises(PostDeploymentError, match="forbids mutation"):
        PostDeploymentEngine(PostDeploymentStore(tmp_path / "project")).deploy(plan, CANDIDATE, actor="r238.test")


def test_plan_rejects_approval_for_other_candidate(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"
    plan = _plan(target)
    with pytest.raises(Exception):
        DeploymentPlan(plan.deployment_id, plan.post_id, plan.candidate_revision_id, "0" * 64, plan.expected_current_revision_id, plan.expected_current_sha256, plan.target_path, plan.machine_binding, plan.approval, plan.validation_fingerprint, plan.regression_fingerprint, plan.policy_fingerprint, plan.created_at, plan.created_by)


def test_concurrent_or_stale_lock_is_never_silently_taken_over(tmp_path: Path) -> None:
    target = tmp_path / "FANUC-SHL.dat"; target.write_bytes(PARENT)
    plan = _plan(target); store = PostDeploymentStore(tmp_path / "project"); engine = PostDeploymentEngine(store)
    lock = engine._acquire_lock(plan, "other.owner")
    with pytest.raises(PostDeploymentError, match="Concurrent or stale"):
        engine.deploy(plan, CANDIDATE, actor="r238.test")
    assert lock.exists()
