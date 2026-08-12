"""Fail-closed binary Post deployment and recovery for isolated targets.

This module deliberately has no default production target.  A caller must pass
an explicit path and the real WorkNC FANUC-SHL path is refused for mutation.
All writes are byte oriented and every state transition is persisted as a
durable, immutable JSON audit record beneath a project supplied audit root.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.post_studio.model import PostMachineBinding, _identifier, _sha, _text, _timestamp


DEPLOYMENT_FORMAT_VERSION = 1
PRODUCTION_FANUC_SHL_PATH = Path(r"C:\ProgramData\WORKNC\2021.0\pospro\FANUC-SHL.dat")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class TargetReconciliation(StrEnum):
    TARGET_MATCHES_EXPECTED_PARENT = "TARGET_MATCHES_EXPECTED_PARENT"
    TARGET_ALREADY_CANDIDATE = "TARGET_ALREADY_CANDIDATE"
    TARGET_UNKNOWN_REVISION = "TARGET_UNKNOWN_REVISION"
    TARGET_MISSING = "TARGET_MISSING"
    TARGET_UNREADABLE = "TARGET_UNREADABLE"
    TARGET_HASH_CHANGED_SINCE_PLAN = "TARGET_HASH_CHANGED_SINCE_PLAN"
    TARGET_RECONCILIATION_REQUIRED = "TARGET_RECONCILIATION_REQUIRED"


class DeploymentTransactionState(StrEnum):
    PLANNED = "PLANNED"
    PRECHECK_PASS = "PRECHECK_PASS"
    BACKUP_VERIFIED = "BACKUP_VERIFIED"
    REPLACEMENT_STARTED = "REPLACEMENT_STARTED"
    REPLACEMENT_VERIFIED = "REPLACEMENT_VERIFIED"
    ACTIVE_COMMITTED = "ACTIVE_COMMITTED"
    FAILED_PRECHECK = "FAILED_PRECHECK"
    FAILED_BACKUP = "FAILED_BACKUP"
    FAILED_REPLACEMENT = "FAILED_REPLACEMENT"
    FAILED_READBACK = "FAILED_READBACK"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    ROLLED_BACK = "ROLLED_BACK"


class DeploymentFault(StrEnum):
    AFTER_BACKUP = "AFTER_BACKUP"
    BEFORE_REPLACE = "BEFORE_REPLACE"
    AFTER_REPLACE_BEFORE_READBACK = "AFTER_REPLACE_BEFORE_READBACK"
    AFTER_READBACK_BEFORE_METADATA_COMMIT = "AFTER_READBACK_BEFORE_METADATA_COMMIT"


class PostDeploymentError(CamInvariantError):
    """Raised when safe deployment cannot establish an unambiguous result."""


@dataclass(frozen=True, slots=True)
class AttributableApproval:
    approver_identity: str
    approved_at: str
    candidate_revision_id: str
    candidate_sha256: str
    machine_binding: PostMachineBinding
    validation_fingerprint: ContentFingerprint
    regression_fingerprint: ContentFingerprint
    decision: str
    statement_version: str
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "approver_identity", _text(self.approver_identity, "Approver identity", 256))
        object.__setattr__(self, "approved_at", _timestamp(self.approved_at, "Approval timestamp"))
        object.__setattr__(self, "candidate_revision_id", _identifier(self.candidate_revision_id, "Candidate revision ID"))
        object.__setattr__(self, "candidate_sha256", _sha(self.candidate_sha256, "Candidate SHA-256"))
        if not isinstance(self.machine_binding, PostMachineBinding):
            raise CamValidationError("Approval machine binding is invalid")
        if not isinstance(self.validation_fingerprint, ContentFingerprint) or not isinstance(self.regression_fingerprint, ContentFingerprint):
            raise CamValidationError("Approval evidence fingerprint is invalid")
        if self.decision != "APPROVE":
            raise CamValidationError("Approval decision must be APPROVE")
        object.__setattr__(self, "statement_version", _text(self.statement_version, "Approval statement version", 128))
        computed = ContentFingerprint.from_payload(self.payload())
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", computed)
        elif self.fingerprint != computed:
            raise CamInvariantError("Approval fingerprint mismatch")

    def payload(self) -> dict[str, object]:
        return {"format": "HMS_POST_ATTRIBUTABLE_APPROVAL", "format_version": DEPLOYMENT_FORMAT_VERSION,
                "approver_identity": self.approver_identity, "approved_at": self.approved_at,
                "candidate_revision_id": self.candidate_revision_id, "candidate_sha256": self.candidate_sha256,
                "machine_binding": self.machine_binding.to_dict(), "validation_fingerprint": self.validation_fingerprint.to_dict(),
                "regression_fingerprint": self.regression_fingerprint.to_dict(), "decision": self.decision,
                "statement_version": self.statement_version}


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    deployment_id: str
    post_id: str
    candidate_revision_id: str
    candidate_sha256: str
    expected_current_revision_id: str
    expected_current_sha256: str
    target_path: str
    machine_binding: PostMachineBinding
    approval: AttributableApproval
    validation_fingerprint: ContentFingerprint
    regression_fingerprint: ContentFingerprint
    policy_fingerprint: ContentFingerprint
    created_at: str
    created_by: str
    status: DeploymentTransactionState = DeploymentTransactionState.PLANNED
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        for field, label in (("deployment_id", "Deployment ID"), ("post_id", "Post ID"), ("candidate_revision_id", "Candidate revision ID"), ("expected_current_revision_id", "Expected current revision ID")):
            object.__setattr__(self, field, _identifier(getattr(self, field), label))
        for field, label in (("candidate_sha256", "Candidate SHA-256"), ("expected_current_sha256", "Expected current SHA-256")):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        target = Path(_text(self.target_path, "Target path", 4096))
        if not target.is_absolute():
            raise CamValidationError("Target path must be absolute")
        object.__setattr__(self, "target_path", str(target))
        if not isinstance(self.machine_binding, PostMachineBinding) or not isinstance(self.approval, AttributableApproval):
            raise CamValidationError("Deployment binding or approval is invalid")
        if self.approval.candidate_revision_id != self.candidate_revision_id or self.approval.candidate_sha256 != self.candidate_sha256:
            raise CamInvariantError("Approval belongs to a different candidate")
        if self.approval.machine_binding != self.machine_binding:
            raise CamInvariantError("Approval binding differs from deployment binding")
        if self.approval.validation_fingerprint != self.validation_fingerprint or self.approval.regression_fingerprint != self.regression_fingerprint:
            raise CamInvariantError("Approval evidence differs from deployment plan")
        if not all(isinstance(item, ContentFingerprint) for item in (self.validation_fingerprint, self.regression_fingerprint, self.policy_fingerprint)):
            raise CamValidationError("Deployment fingerprints are invalid")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "Deployment creation timestamp"))
        object.__setattr__(self, "created_by", _text(self.created_by, "Deployment creator", 256))
        if not isinstance(self.status, DeploymentTransactionState):
            raise CamValidationError("Deployment status is invalid")
        computed = ContentFingerprint.from_payload(self.payload())
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", computed)
        elif self.fingerprint != computed:
            raise CamInvariantError("Deployment plan fingerprint mismatch")

    def payload(self) -> dict[str, object]:
        return {"format": "HMS_POST_DEPLOYMENT_PLAN", "format_version": DEPLOYMENT_FORMAT_VERSION,
                "deployment_id": self.deployment_id, "post_id": self.post_id, "candidate_revision_id": self.candidate_revision_id,
                "candidate_sha256": self.candidate_sha256, "expected_current_revision_id": self.expected_current_revision_id,
                "expected_current_sha256": self.expected_current_sha256, "target_path": self.target_path,
                "machine_binding": self.machine_binding.to_dict(), "approval_fingerprint": self.approval.fingerprint.to_dict(),
                "validation_fingerprint": self.validation_fingerprint.to_dict(), "regression_fingerprint": self.regression_fingerprint.to_dict(),
                "policy_fingerprint": self.policy_fingerprint.to_dict(), "created_at": self.created_at, "created_by": self.created_by,
                "status": self.status.value}


@dataclass(frozen=True, slots=True)
class ProductionActivationReadiness:
    """Decision packet for a future human owner; it has no activation capability."""

    plan: DeploymentPlan
    observed_target_sha256: str
    readiness_state: str = "READY_FOR_OWNER_ACTIVATION_DECISION"
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, DeploymentPlan):
            raise CamValidationError("Readiness deployment plan is invalid")
        object.__setattr__(self, "observed_target_sha256", _sha(self.observed_target_sha256, "Observed target SHA-256"))
        if self.readiness_state != "READY_FOR_OWNER_ACTIVATION_DECISION":
            raise CamValidationError("Production readiness must remain owner-decision only")
        computed = ContentFingerprint.from_payload(self.payload())
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", computed)
        elif self.fingerprint != computed:
            raise CamInvariantError("Production readiness fingerprint mismatch")

    def payload(self) -> dict[str, object]:
        return {"format": "HMS_POST_PRODUCTION_ACTIVATION_READINESS", "format_version": DEPLOYMENT_FORMAT_VERSION,
                "target_path": self.plan.target_path, "current_target_sha256": self.observed_target_sha256,
                "expected_parent_sha256": self.plan.expected_current_sha256, "candidate_sha256": self.plan.candidate_sha256,
                "candidate_revision_id": self.plan.candidate_revision_id, "machine_binding": self.plan.machine_binding.to_dict(),
                "validation_fingerprint": self.plan.validation_fingerprint.to_dict(), "regression_fingerprint": self.plan.regression_fingerprint.to_dict(),
                "approval_requirements": self.plan.approval.payload(), "backup_location_policy": "PROJECT_POST_STUDIO_DEPLOYMENT_IMMUTABLE_BACKUPS",
                "rollback_revision_id": self.plan.expected_current_revision_id, "deployment_plan_fingerprint": self.plan.fingerprint.to_dict(),
                "readiness_state": self.readiness_state}


@dataclass(frozen=True, slots=True)
class TargetInspection:
    state: TargetReconciliation
    target_sha256: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class ActivationRecord:
    deployment_id: str
    previous_revision_id: str
    previous_sha256: str
    new_revision_id: str
    new_sha256: str
    target_path: str
    backup_path: str
    backup_sha256: str
    plan_fingerprint: ContentFingerprint
    approval_fingerprint: ContentFingerprint
    activated_at: str
    actor: str
    verification_result: str = "READBACK_MATCHED"

    def payload(self) -> dict[str, object]:
        return {"format": "HMS_POST_ACTIVATION_RECORD", "format_version": DEPLOYMENT_FORMAT_VERSION,
                "deployment_id": self.deployment_id, "previous_revision_id": self.previous_revision_id,
                "previous_sha256": self.previous_sha256, "new_revision_id": self.new_revision_id,
                "new_sha256": self.new_sha256, "target_path": self.target_path, "backup_path": self.backup_path,
                "backup_sha256": self.backup_sha256, "plan_fingerprint": self.plan_fingerprint.to_dict(),
                "approval_fingerprint": self.approval_fingerprint.to_dict(), "activated_at": self.activated_at,
                "actor": self.actor, "verification_result": self.verification_result}


class PostDeploymentStore:
    """Append-only deployment evidence rooted in the HMS project, not cache/temp."""

    def __init__(self, root: Path) -> None:
        self.root = root / "post" / "studio" / "deployment"

    def write(self, deployment_id: str, name: str, payload: object) -> Path:
        directory = self.root / deployment_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        data = _canonical(payload)
        if target.exists():
            if target.read_bytes() != data:
                raise PostDeploymentError("Immutable deployment audit record conflicts")
            return target
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
            if target.read_bytes() != data:
                raise PostDeploymentError("Deployment audit write verification failed")
        finally:
            temporary.unlink(missing_ok=True)
        return target


class PostDeploymentEngine:
    """Single-target deployment executor with exact backup/readback verification."""

    def __init__(self, store: PostDeploymentStore, *, fault: DeploymentFault | None = None) -> None:
        self._store, self._fault = store, fault

    @staticmethod
    def production_read_only_preflight(plan: DeploymentPlan) -> TargetInspection:
        """Hash a production target without creating plans, locks, or writes."""

        return PostDeploymentEngine.inspect_target(Path(plan.target_path), plan)

    @staticmethod
    def readiness(plan: DeploymentPlan, inspection: TargetInspection) -> ProductionActivationReadiness:
        """Build a future-owner packet only after exact parent reconciliation."""

        if inspection.state is not TargetReconciliation.TARGET_MATCHES_EXPECTED_PARENT or inspection.target_sha256 is None:
            raise PostDeploymentError("Production readiness requires exact expected-parent reconciliation")
        return ProductionActivationReadiness(plan, inspection.target_sha256)

    @staticmethod
    def inspect_target(target: Path, plan: DeploymentPlan, *, active_sha256: str | None = None) -> TargetInspection:
        try:
            data = target.read_bytes()
        except FileNotFoundError:
            return TargetInspection(TargetReconciliation.TARGET_MISSING, None, "Target does not exist")
        except OSError as error:
            return TargetInspection(TargetReconciliation.TARGET_UNREADABLE, None, f"Target cannot be read: {error.__class__.__name__}")
        digest = _hash(data)
        if digest == plan.expected_current_sha256:
            return TargetInspection(TargetReconciliation.TARGET_MATCHES_EXPECTED_PARENT, digest, "Target matches expected parent")
        if digest == plan.candidate_sha256:
            return TargetInspection(TargetReconciliation.TARGET_ALREADY_CANDIDATE, digest, "Target already contains candidate")
        if active_sha256 is not None and digest == active_sha256:
            return TargetInspection(TargetReconciliation.TARGET_HASH_CHANGED_SINCE_PLAN, digest, "Target is managed active but plan is stale")
        return TargetInspection(TargetReconciliation.TARGET_UNKNOWN_REVISION, digest, "Target bytes are not registered for this plan")

    def deploy(self, plan: DeploymentPlan, candidate: bytes, *, actor: str) -> ActivationRecord:
        target = Path(plan.target_path)
        self._reject_production_target(target)
        if _hash(candidate) != plan.candidate_sha256:
            raise PostDeploymentError("Candidate bytes do not match deployment plan")
        lock = self._acquire_lock(plan, actor)
        try:
            self._write_state(plan, DeploymentTransactionState.PLANNED)
            inspection = self.inspect_target(target, plan)
            self._write_event(plan, "precheck", {"state": inspection.state.value, "sha256": inspection.target_sha256, "detail": inspection.detail})
            if inspection.state is not TargetReconciliation.TARGET_MATCHES_EXPECTED_PARENT:
                self._write_state(plan, DeploymentTransactionState.FAILED_PRECHECK)
                raise PostDeploymentError(f"Deployment precheck rejected: {inspection.state.value}")
            self._write_state(plan, DeploymentTransactionState.PRECHECK_PASS)
            backup = self._backup(target, plan, target.read_bytes())
            self._write_state(plan, DeploymentTransactionState.BACKUP_VERIFIED)
            self._inject(DeploymentFault.AFTER_BACKUP)
            try:
                self._inject(DeploymentFault.BEFORE_REPLACE)
                self._write_state(plan, DeploymentTransactionState.REPLACEMENT_STARTED)
                self._replace_bytes(target, candidate)
                self._inject(DeploymentFault.AFTER_REPLACE_BEFORE_READBACK)
                if _hash(target.read_bytes()) != plan.candidate_sha256:
                    self._write_state(plan, DeploymentTransactionState.FAILED_READBACK)
                    raise PostDeploymentError("Post replacement readback SHA mismatch")
                self._write_state(plan, DeploymentTransactionState.REPLACEMENT_VERIFIED)
                record = ActivationRecord(plan.deployment_id, plan.expected_current_revision_id, plan.expected_current_sha256,
                                          plan.candidate_revision_id, plan.candidate_sha256, str(target), str(backup), _hash(backup.read_bytes()),
                                          plan.fingerprint, plan.approval.fingerprint, _utc_now(), _text(actor, "Activation actor", 256))
                self._inject(DeploymentFault.AFTER_READBACK_BEFORE_METADATA_COMMIT)
                self._store.write(plan.deployment_id, "activation-record.json", record.payload())
                self._write_state(plan, DeploymentTransactionState.ACTIVE_COMMITTED)
                return record
            except PostDeploymentError:
                raise
            except OSError as error:
                self._write_state(plan, DeploymentTransactionState.FAILED_REPLACEMENT)
                raise PostDeploymentError("Atomic Post replacement failed") from error
        finally:
            lock.unlink(missing_ok=True)

    def rollback(self, record: ActivationRecord, *, actor: str) -> None:
        target, backup = Path(record.target_path), Path(record.backup_path)
        self._reject_production_target(target)
        if self._safe_hash(target) != record.new_sha256:
            raise PostDeploymentError("Rollback requires exact managed active target bytes")
        if self._safe_hash(backup) != record.backup_sha256 or record.backup_sha256 != record.previous_sha256:
            raise PostDeploymentError("Rollback backup is not exact known parent bytes")
        self._replace_bytes(target, backup.read_bytes())
        if self._safe_hash(target) != record.previous_sha256:
            raise PostDeploymentError("Rollback readback SHA mismatch")
        self._store.write(record.deployment_id, "rollback-record.json", {"format": "HMS_POST_ROLLBACK_RECORD", "format_version": DEPLOYMENT_FORMAT_VERSION,
                          "deployment_id": record.deployment_id, "target_path": record.target_path, "restored_sha256": record.previous_sha256,
                          "rolled_back_at": _utc_now(), "actor": _text(actor, "Rollback actor", 256), "state": DeploymentTransactionState.ROLLED_BACK.value})
        self._write_state_for(record.deployment_id, DeploymentTransactionState.ROLLED_BACK)

    def recover(self, plan: DeploymentPlan) -> TargetInspection:
        target = Path(plan.target_path)
        inspection = self.inspect_target(target, plan)
        state = DeploymentTransactionState.ACTIVE_COMMITTED if inspection.state is TargetReconciliation.TARGET_ALREADY_CANDIDATE else DeploymentTransactionState.RECOVERY_REQUIRED
        self._write_state(plan, state)
        self._write_event(plan, "recovery", {"state": inspection.state.value, "sha256": inspection.target_sha256})
        return inspection

    def _backup(self, target: Path, plan: DeploymentPlan, data: bytes) -> Path:
        backup_dir = self._store.root / plan.deployment_id / "backups"
        backup = backup_dir / f"{plan.expected_current_sha256}.dat"
        backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._write_new_binary(backup, data)
            if _hash(backup.read_bytes()) != plan.expected_current_sha256:
                raise PostDeploymentError("Backup SHA mismatch")
        except (OSError, PostDeploymentError) as error:
            self._write_state(plan, DeploymentTransactionState.FAILED_BACKUP)
            raise PostDeploymentError("Pre-deployment backup failed") from error
        self._write_event(plan, "backup", {"path": str(backup), "backup_sha256": _hash(data), "source_target_path": str(target), "target_sha256": _hash(data), "timestamp": _utc_now()})
        return backup

    def _acquire_lock(self, plan: DeploymentPlan, actor: str) -> Path:
        """Acquire a non-stealable deployment lock for post/target/binding scope."""

        lock_key = _hash(_canonical({"post_id": plan.post_id, "target_path": plan.target_path,
                                     "machine_id": plan.machine_binding.machine_id,
                                     "controller_id": plan.machine_binding.controller_id}))
        locks = self._store.root / "locks"; locks.mkdir(parents=True, exist_ok=True)
        lock = locks / f"{lock_key}.lock"
        payload = _canonical({"format": "HMS_POST_DEPLOYMENT_LOCK", "format_version": DEPLOYMENT_FORMAT_VERSION,
                              "deployment_id": plan.deployment_id, "actor": _text(actor, "Lock actor", 256),
                              "pid": os.getpid(), "created_at": _utc_now(),
                              "stale_lock_policy": "REQUIRES_EXPLICIT_OPERATOR_RECONCILIATION_NO_SILENT_TAKEOVER"})
        try:
            with lock.open("xb") as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError as error:
            raise PostDeploymentError("Concurrent or stale deployment lock requires explicit reconciliation") from error
        return lock

    @staticmethod
    def _write_new_binary(path: Path, data: bytes) -> None:
        if path.exists():
            if path.read_bytes() != data:
                raise PostDeploymentError("Immutable backup path conflicts")
            return
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace_bytes(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f".{target.name}.{uuid4().hex}.stage")
        try:
            with staged.open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if staged.read_bytes() != data:
                raise PostDeploymentError("Staged Post bytes failed verification")
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)

    @staticmethod
    def _safe_hash(path: Path) -> str | None:
        try:
            return _hash(path.read_bytes())
        except OSError:
            return None

    @staticmethod
    def _reject_production_target(target: Path) -> None:
        try:
            forbidden = PRODUCTION_FANUC_SHL_PATH.resolve(strict=False)
            actual = target.resolve(strict=False)
        except OSError as error:
            raise PostDeploymentError("Target cannot be resolved safely") from error
        if actual == forbidden:
            raise PostDeploymentError("R238 forbids mutation of the real global FANUC-SHL target")

    def _inject(self, boundary: DeploymentFault) -> None:
        if self._fault is boundary:
            raise PostDeploymentError(f"Injected deployment fault: {boundary.value}")

    def _write_state(self, plan: DeploymentPlan, state: DeploymentTransactionState) -> None:
        self._write_state_for(plan.deployment_id, state)

    def _write_state_for(self, deployment_id: str, state: DeploymentTransactionState) -> None:
        self._store.write(deployment_id, f"state-{state.value.lower()}.json", {"format": "HMS_POST_DEPLOYMENT_STATE", "format_version": DEPLOYMENT_FORMAT_VERSION, "deployment_id": deployment_id, "state": state.value, "recorded_at": _utc_now()})

    def _write_event(self, plan: DeploymentPlan, name: str, payload: dict[str, object]) -> None:
        self._store.write(plan.deployment_id, f"{name}.json", {"format": "HMS_POST_DEPLOYMENT_AUDIT", "format_version": DEPLOYMENT_FORMAT_VERSION, "deployment_id": plan.deployment_id, **payload})


__all__ = ["ActivationRecord", "AttributableApproval", "DEPLOYMENT_FORMAT_VERSION", "DeploymentFault", "DeploymentPlan", "DeploymentTransactionState", "PostDeploymentEngine", "PostDeploymentError", "PostDeploymentStore", "PRODUCTION_FANUC_SHL_PATH", "ProductionActivationReadiness", "TargetInspection", "TargetReconciliation"]
