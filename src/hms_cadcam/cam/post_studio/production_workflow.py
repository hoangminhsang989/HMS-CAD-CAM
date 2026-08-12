"""Owner-operated production activation preparation for Post Studio R239.

The public services in this module implement Phase 1 only.  They can inspect,
fingerprint, package, rehearse, and verify a future activation request, but they
cannot replace a production target.  Target bytes remain authoritative and are
rehash-checked at every trust boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.post_studio.deployment import (
    DeploymentPlan,
    PostDeploymentError,
    TargetReconciliation,
)
from hms_cadcam.cam.post_studio.model import (
    PostMachineBinding,
    _identifier,
    _sha,
    _text,
    _timestamp,
)


PRODUCTION_WORKFLOW_FORMAT_VERSION = 1


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fingerprint(value: ContentFingerprint, label: str) -> ContentFingerprint:
    if not isinstance(value, ContentFingerprint):
        raise CamValidationError(f"{label} fingerprint is invalid")
    return value


def _aware(value: str, label: str) -> datetime:
    normalized = _timestamp(value, label)
    return datetime.fromisoformat(normalized)


class ProductionDecision(StrEnum):
    NOT_DECIDED = "NOT_DECIDED"
    APPROVE_ACTIVATION_WINDOW = "APPROVE_ACTIVATION_WINDOW"
    REJECT_ACTIVATION = "REJECT_ACTIVATION"
    DEFER_ACTIVATION = "DEFER_ACTIVATION"


class ProductionDecisionStatus(StrEnum):
    DRAFT = "DRAFT"
    ATTRIBUTABLE = "ATTRIBUTABLE"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


class ActivationWindowStatus(StrEnum):
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    AUTHORIZED = "AUTHORIZED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


class EvidenceFreshnessStatus(StrEnum):
    FRESH = "FRESH"
    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"


class RollbackReadinessStatus(StrEnum):
    ROLLBACK_READY = "ROLLBACK_READY"
    EXACT_PARENT_UNAVAILABLE = "EXACT_PARENT_UNAVAILABLE"


class Phase2GateStatus(StrEnum):
    CHUA_DUOC_PHEP_KICH_HOAT = "CHUA_DUOC_PHEP_KICH_HOAT"
    SAN_SANG_CHO_PHE_DUYET_KICH_HOAT = "SAN_SANG_CHO_PHE_DUYET_KICH_HOAT"
    CUA_SO_HOP_LE = "CUA_SO_HOP_LE"


@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    target_path: str
    bytes_sha256: str
    file_size: int
    modified_time_ns: int
    filesystem_identity: str
    permissions_summary: str
    expected_parent_sha256: str
    reconciliation_state: TargetReconciliation
    captured_at: str
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        target = Path(_text(self.target_path, "Target snapshot path", 4096))
        if not target.is_absolute():
            raise CamValidationError("Target snapshot path must be absolute")
        object.__setattr__(self, "target_path", str(target))
        object.__setattr__(self, "bytes_sha256", _sha(self.bytes_sha256, "Snapshot SHA-256"))
        object.__setattr__(self, "expected_parent_sha256", _sha(self.expected_parent_sha256, "Expected parent SHA-256"))
        if type(self.file_size) is not int or self.file_size < 0:
            raise CamValidationError("Target snapshot size is invalid")
        if type(self.modified_time_ns) is not int or self.modified_time_ns < 0:
            raise CamValidationError("Target snapshot mtime is invalid")
        object.__setattr__(self, "filesystem_identity", _text(self.filesystem_identity, "Filesystem identity", 512))
        object.__setattr__(self, "permissions_summary", _text(self.permissions_summary, "Permissions summary", 512))
        if not isinstance(self.reconciliation_state, TargetReconciliation):
            raise CamValidationError("Target snapshot reconciliation state is invalid")
        object.__setattr__(self, "captured_at", _timestamp(self.captured_at, "Snapshot timestamp"))
        computed = ContentFingerprint.from_payload(self.payload())
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", computed)
        elif self.fingerprint != computed:
            raise CamInvariantError("Target snapshot fingerprint mismatch")

    def payload(self) -> dict[str, object]:
        return {
            "format": "HMS_POST_TARGET_SNAPSHOT",
            "format_version": PRODUCTION_WORKFLOW_FORMAT_VERSION,
            "target_path": self.target_path,
            "bytes_sha256": self.bytes_sha256,
            "file_size": self.file_size,
            "modified_time_ns": self.modified_time_ns,
            "filesystem_identity": self.filesystem_identity,
            "permissions_summary": self.permissions_summary,
            "expected_parent_sha256": self.expected_parent_sha256,
            "reconciliation_state": self.reconciliation_state.value,
            "captured_at": self.captured_at,
        }

    def is_stale(self) -> bool:
        """Rehash bytes and compare metadata without mutating the target."""

        target = Path(self.target_path)
        try:
            stat = target.stat()
            digest = _hash(target.read_bytes())
        except OSError:
            return True
        return (
            digest != self.bytes_sha256
            or stat.st_size != self.file_size
            or stat.st_mtime_ns != self.modified_time_ns
        )


@dataclass(frozen=True, slots=True)
class FinalPreflight:
    snapshot: TargetSnapshot
    exists: bool
    is_regular_file: bool
    readable: bool
    write_permission_assessment: bool
    target_directory_exists: bool
    target_directory_write_permission_assessment: bool
    atomic_replacement_capability_assessment: str
    conflict_lock_present: bool
    backup_destination_capability_assessment: bool
    rollback_source_available: bool
    status: str
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, TargetSnapshot):
            raise CamValidationError("Final preflight snapshot is invalid")
        for name in (
            "exists", "is_regular_file", "readable", "write_permission_assessment",
            "target_directory_exists", "target_directory_write_permission_assessment",
            "conflict_lock_present", "backup_destination_capability_assessment",
            "rollback_source_available",
        ):
            if type(getattr(self, name)) is not bool:
                raise CamValidationError(f"{name} assessment is invalid")
        object.__setattr__(self, "atomic_replacement_capability_assessment", _text(self.atomic_replacement_capability_assessment, "Atomic capability", 256))
        if self.status not in {"PASS_READ_ONLY", "TARGET_RECONCILIATION_REQUIRED", "PREFLIGHT_BLOCKED"}:
            raise CamValidationError("Final preflight status is invalid")
        computed = ContentFingerprint.from_payload(self.payload())
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", computed)
        elif self.fingerprint != computed:
            raise CamInvariantError("Final preflight fingerprint mismatch")

    def payload(self) -> dict[str, object]:
        return {
            "format": "HMS_POST_FINAL_PREFLIGHT",
            "format_version": PRODUCTION_WORKFLOW_FORMAT_VERSION,
            "target_snapshot_fingerprint": self.snapshot.fingerprint.to_dict(),
            "exists": self.exists,
            "is_regular_file": self.is_regular_file,
            "readable": self.readable,
            "write_permission_assessment": self.write_permission_assessment,
            "target_directory_exists": self.target_directory_exists,
            "target_directory_write_permission_assessment": self.target_directory_write_permission_assessment,
            "atomic_replacement_capability_assessment": self.atomic_replacement_capability_assessment,
            "conflict_lock_present": self.conflict_lock_present,
            "backup_destination_capability_assessment": self.backup_destination_capability_assessment,
            "rollback_source_available": self.rollback_source_available,
            "status": self.status,
            "read_only": True,
        }


@dataclass(frozen=True, slots=True)
class EvidenceFreshness:
    candidate_sha256: str
    validation_fingerprint: ContentFingerprint
    expected_validation_fingerprint: ContentFingerprint
    regression_fingerprint: ContentFingerprint
    expected_regression_fingerprint: ContentFingerprint
    binding_fingerprint: ContentFingerprint
    expected_binding_fingerprint: ContentFingerprint
    policy_fingerprint: ContentFingerprint
    expected_policy_fingerprint: ContentFingerprint
    validation_passed: bool
    regression_passed: bool
    status: EvidenceFreshnessStatus | None = None
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_sha256", _sha(self.candidate_sha256, "Freshness candidate SHA-256"))
        for name in (
            "validation_fingerprint", "expected_validation_fingerprint",
            "regression_fingerprint", "expected_regression_fingerprint",
            "binding_fingerprint", "expected_binding_fingerprint",
            "policy_fingerprint", "expected_policy_fingerprint",
        ):
            _fingerprint(getattr(self, name), name)
        if type(self.validation_passed) is not bool or type(self.regression_passed) is not bool:
            raise CamValidationError("Evidence result state is invalid")
        fresh = (
            self.validation_passed
            and self.regression_passed
            and self.validation_fingerprint == self.expected_validation_fingerprint
            and self.regression_fingerprint == self.expected_regression_fingerprint
            and self.binding_fingerprint == self.expected_binding_fingerprint
            and self.policy_fingerprint == self.expected_policy_fingerprint
        )
        calculated_status = EvidenceFreshnessStatus.FRESH if fresh else EvidenceFreshnessStatus.REVALIDATION_REQUIRED
        if self.status is None:
            object.__setattr__(self, "status", calculated_status)
        elif self.status is not calculated_status:
            raise CamInvariantError("Evidence freshness status contradicts fingerprints")
        computed = ContentFingerprint.from_payload(self.payload())
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", computed)
        elif self.fingerprint != computed:
            raise CamInvariantError("Evidence freshness fingerprint mismatch")

    def payload(self) -> dict[str, object]:
        return {
            "format": "HMS_POST_EVIDENCE_FRESHNESS",
            "format_version": PRODUCTION_WORKFLOW_FORMAT_VERSION,
            "candidate_sha256": self.candidate_sha256,
            "validation_fingerprint": self.validation_fingerprint.to_dict(),
            "expected_validation_fingerprint": self.expected_validation_fingerprint.to_dict(),
            "regression_fingerprint": self.regression_fingerprint.to_dict(),
            "expected_regression_fingerprint": self.expected_regression_fingerprint.to_dict(),
            "binding_fingerprint": self.binding_fingerprint.to_dict(),
            "expected_binding_fingerprint": self.expected_binding_fingerprint.to_dict(),
            "policy_fingerprint": self.policy_fingerprint.to_dict(),
            "expected_policy_fingerprint": self.expected_policy_fingerprint.to_dict(),
            "validation_passed": self.validation_passed,
            "regression_passed": self.regression_passed,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class RollbackPlan:
    rollback_plan_id: str
    deployment_plan_id: str
    target_path: str
    expected_active_sha256: str
    restore_revision_id: str
    restore_sha256: str
    backup_policy: str
    status: RollbackReadinessStatus
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        for name, label in (("rollback_plan_id", "Rollback plan ID"), ("deployment_plan_id", "Deployment plan ID"), ("restore_revision_id", "Restore revision ID")):
            object.__setattr__(self, name, _identifier(getattr(self, name), label))
        target = Path(_text(self.target_path, "Rollback target path", 4096))
        if not target.is_absolute():
            raise CamValidationError("Rollback target path must be absolute")
        object.__setattr__(self, "target_path", str(target))
        object.__setattr__(self, "expected_active_sha256", _sha(self.expected_active_sha256, "Expected active SHA-256"))
        object.__setattr__(self, "restore_sha256", _sha(self.restore_sha256, "Restore SHA-256"))
        object.__setattr__(self, "backup_policy", _text(self.backup_policy, "Backup policy", 512))
        if not isinstance(self.status, RollbackReadinessStatus):
            raise CamValidationError("Rollback readiness status is invalid")
        computed = ContentFingerprint.from_payload(self.payload())
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", computed)
        elif self.fingerprint != computed:
            raise CamInvariantError("Rollback plan fingerprint mismatch")

    def payload(self) -> dict[str, object]:
        return {
            "format": "HMS_POST_ROLLBACK_PLAN",
            "format_version": PRODUCTION_WORKFLOW_FORMAT_VERSION,
            "rollback_plan_id": self.rollback_plan_id,
            "deployment_plan_id": self.deployment_plan_id,
            "target_path": self.target_path,
            "expected_active_sha256": self.expected_active_sha256,
            "restore_revision_id": self.restore_revision_id,
            "restore_sha256": self.restore_sha256,
            "backup_policy": self.backup_policy,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class ProductionActivationDecision:
    decision_id: str
    post_id: str
    candidate_revision_id: str
    candidate_sha256: str
    expected_parent_revision_id: str
    expected_parent_sha256: str
    target_path: str
    target_current_sha256: str
    machine_binding: PostMachineBinding
    validation_fingerprint: ContentFingerprint
    regression_fingerprint: ContentFingerprint
    approval_fingerprint: ContentFingerprint
    deployment_plan_fingerprint: ContentFingerprint
    rollback_plan_fingerprint: ContentFingerprint
    policy_fingerprint: ContentFingerprint
    owner_identity: str
    decision_timestamp_with_timezone: str
    decision: ProductionDecision
    decision_statement_version: str
    status: ProductionDecisionStatus | None = None
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        for name, label in (
            ("decision_id", "Decision ID"), ("post_id", "Post ID"),
            ("candidate_revision_id", "Candidate revision ID"),
            ("expected_parent_revision_id", "Expected parent revision ID"),
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), label))
        for name, label in (
            ("candidate_sha256", "Candidate SHA-256"),
            ("expected_parent_sha256", "Expected parent SHA-256"),
            ("target_current_sha256", "Target current SHA-256"),
        ):
            object.__setattr__(self, name, _sha(getattr(self, name), label))
        target = Path(_text(self.target_path, "Decision target path", 4096))
        if not target.is_absolute():
            raise CamValidationError("Decision target path must be absolute")
        object.__setattr__(self, "target_path", str(target))
        if not isinstance(self.machine_binding, PostMachineBinding):
            raise CamValidationError("Decision machine binding is invalid")
        for name in (
            "validation_fingerprint", "regression_fingerprint", "approval_fingerprint",
            "deployment_plan_fingerprint", "rollback_plan_fingerprint", "policy_fingerprint",
        ):
            _fingerprint(getattr(self, name), name)
        object.__setattr__(self, "owner_identity", _text(self.owner_identity, "Owner identity", 256))
        object.__setattr__(self, "decision_timestamp_with_timezone", _timestamp(self.decision_timestamp_with_timezone, "Decision timestamp"))
        if not isinstance(self.decision, ProductionDecision):
            raise CamValidationError("Production decision is invalid")
        object.__setattr__(self, "decision_statement_version", _text(self.decision_statement_version, "Decision statement version", 128))
        expected_status = {
            ProductionDecision.NOT_DECIDED: ProductionDecisionStatus.DRAFT,
            ProductionDecision.APPROVE_ACTIVATION_WINDOW: ProductionDecisionStatus.ATTRIBUTABLE,
            ProductionDecision.REJECT_ACTIVATION: ProductionDecisionStatus.REJECTED,
            ProductionDecision.DEFER_ACTIVATION: ProductionDecisionStatus.DEFERRED,
        }[self.decision]
        if self.status is None:
            object.__setattr__(self, "status", expected_status)
        elif self.status is not expected_status:
            raise CamInvariantError("Owner decision status contradicts decision")
        computed = ContentFingerprint.from_payload(self.payload())
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", computed)
        elif self.fingerprint != computed:
            raise CamInvariantError("Owner decision fingerprint mismatch")

    def payload(self) -> dict[str, object]:
        return {
            "format": "HMS_POST_PRODUCTION_ACTIVATION_DECISION",
            "format_version": PRODUCTION_WORKFLOW_FORMAT_VERSION,
            "decision_id": self.decision_id,
            "post_id": self.post_id,
            "candidate_revision_id": self.candidate_revision_id,
            "candidate_sha256": self.candidate_sha256,
            "expected_parent_revision_id": self.expected_parent_revision_id,
            "expected_parent_sha256": self.expected_parent_sha256,
            "target_path": self.target_path,
            "target_current_sha256": self.target_current_sha256,
            "machine_binding": self.machine_binding.to_dict(),
            "validation_fingerprint": self.validation_fingerprint.to_dict(),
            "regression_fingerprint": self.regression_fingerprint.to_dict(),
            "approval_fingerprint": self.approval_fingerprint.to_dict(),
            "deployment_plan_fingerprint": self.deployment_plan_fingerprint.to_dict(),
            "rollback_plan_fingerprint": self.rollback_plan_fingerprint.to_dict(),
            "policy_fingerprint": self.policy_fingerprint.to_dict(),
            "owner_identity": self.owner_identity,
            "decision_timestamp_with_timezone": self.decision_timestamp_with_timezone,
            "decision": self.decision.value,
            "decision_statement_version": self.decision_statement_version,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class ActivationWindow:
    window_id: str
    deployment_plan_id: str
    target_snapshot_fingerprint: ContentFingerprint
    candidate_sha256: str
    owner_decision_id: str
    owner_decision_fingerprint: ContentFingerprint
    approved_from: str
    expires_at: str
    actor_identity: str
    status: ActivationWindowStatus
    fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        for name, label in (("window_id", "Window ID"), ("deployment_plan_id", "Deployment plan ID"), ("owner_decision_id", "Owner decision ID")):
            object.__setattr__(self, name, _identifier(getattr(self, name), label))
        _fingerprint(self.target_snapshot_fingerprint, "Target snapshot")
        _fingerprint(self.owner_decision_fingerprint, "Owner decision")
        object.__setattr__(self, "candidate_sha256", _sha(self.candidate_sha256, "Window candidate SHA-256"))
        approved = _aware(self.approved_from, "Activation window start")
        expires = _aware(self.expires_at, "Activation window expiry")
        if expires <= approved:
            raise CamValidationError("Activation window must expire after its start")
        object.__setattr__(self, "approved_from", approved.isoformat())
        object.__setattr__(self, "expires_at", expires.isoformat())
        object.__setattr__(self, "actor_identity", _text(self.actor_identity, "Window actor", 256))
        if not isinstance(self.status, ActivationWindowStatus):
            raise CamValidationError("Activation window status is invalid")
        computed = ContentFingerprint.from_payload(self.payload())
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", computed)
        elif self.fingerprint != computed:
            raise CamInvariantError("Activation window fingerprint mismatch")

    def payload(self) -> dict[str, object]:
        return {
            "format": "HMS_POST_ACTIVATION_WINDOW",
            "format_version": PRODUCTION_WORKFLOW_FORMAT_VERSION,
            "window_id": self.window_id,
            "deployment_plan_id": self.deployment_plan_id,
            "target_snapshot_fingerprint": self.target_snapshot_fingerprint.to_dict(),
            "candidate_sha256": self.candidate_sha256,
            "owner_decision_id": self.owner_decision_id,
            "owner_decision_fingerprint": self.owner_decision_fingerprint.to_dict(),
            "approved_from": self.approved_from,
            "expires_at": self.expires_at,
            "actor_identity": self.actor_identity,
            "status": self.status.value,
        }

    def evaluate(self, *, now: str, snapshot: TargetSnapshot, candidate_sha256: str, decision: ProductionActivationDecision) -> "ActivationWindow":
        if self.status in {ActivationWindowStatus.CONSUMED, ActivationWindowStatus.INVALIDATED}:
            return self
        if (
            snapshot.fingerprint != self.target_snapshot_fingerprint
            or snapshot.is_stale()
            or candidate_sha256 != self.candidate_sha256
            or decision.fingerprint != self.owner_decision_fingerprint
            or decision.decision is not ProductionDecision.APPROVE_ACTIVATION_WINDOW
        ):
            return replace(self, status=ActivationWindowStatus.INVALIDATED, fingerprint=None)
        instant = _aware(now, "Activation window evaluation timestamp")
        if instant < _aware(self.approved_from, "Activation window start"):
            return replace(self, status=ActivationWindowStatus.NOT_AUTHORIZED, fingerprint=None)
        if instant >= _aware(self.expires_at, "Activation window expiry"):
            return replace(self, status=ActivationWindowStatus.EXPIRED, fingerprint=None)
        return replace(self, status=ActivationWindowStatus.AUTHORIZED, fingerprint=None)

    def consume(self) -> "ActivationWindow":
        if self.status is not ActivationWindowStatus.AUTHORIZED:
            raise CamInvariantError("Only an authorized activation window can be consumed")
        return replace(self, status=ActivationWindowStatus.CONSUMED, fingerprint=None)


@dataclass(frozen=True, slots=True)
class ProductionActivationPackage:
    package_path: str
    package_sha256: str
    manifest: dict[str, object]
    manifest_fingerprint: ContentFingerprint
    deployment_state: str = "NOT_ACTIVE_GLOBALLY"


class ProductionWorkflowStore:
    """Append-only structured Phase-1 audit storage under one HMS project."""

    def __init__(self, project_root: Path) -> None:
        if not isinstance(project_root, Path):
            raise TypeError("Project root must be a Path")
        self.root = project_root / "post" / "studio" / "production-activation"

    def write_record(self, record_id: str, name: str, payload: dict[str, object]) -> Path:
        record = _identifier(record_id, "Production workflow record ID")
        filename = _text(name, "Production workflow record name", 128)
        if Path(filename).name != filename or not filename.endswith(".json"):
            raise CamValidationError("Production workflow record name is unsafe")
        data = _canonical(payload)
        target = self.root / record / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != data:
                raise CamInvariantError("Immutable production workflow record conflicts")
            return target
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
            if target.read_bytes() != data:
                raise CamInvariantError("Production workflow record readback mismatch")
        finally:
            temporary.unlink(missing_ok=True)
        return target


class ProductionActivationWorkflow:
    """Phase-1 production preparation; no method can write a target Post."""

    @staticmethod
    def capture_snapshot(target: Path, *, expected_parent_sha256: str, captured_at: str) -> TargetSnapshot:
        if not isinstance(target, Path) or not target.is_absolute():
            raise TypeError("Target must be an absolute Path")
        try:
            stat = target.stat()
            if not target.is_file():
                raise PostDeploymentError("Production target is not a regular file")
            data = target.read_bytes()
        except OSError as error:
            raise PostDeploymentError("Production target cannot be captured read-only") from error
        digest = _hash(data)
        state = TargetReconciliation.TARGET_MATCHES_EXPECTED_PARENT if digest == expected_parent_sha256 else TargetReconciliation.TARGET_RECONCILIATION_REQUIRED
        filesystem_identity = f"device={stat.st_dev};inode={stat.st_ino}"
        permissions = f"read={os.access(target, os.R_OK)};write_assessed={os.access(target, os.W_OK)};mode={stat.st_mode:o}"
        return TargetSnapshot(str(target), digest, stat.st_size, stat.st_mtime_ns, filesystem_identity, permissions, expected_parent_sha256, state, captured_at)

    @staticmethod
    def final_preflight(
        target: Path,
        *,
        expected_parent_sha256: str,
        rollback_source: bytes | None,
        backup_root: Path,
        lock_path: Path,
        captured_at: str,
    ) -> FinalPreflight:
        snapshot = ProductionActivationWorkflow.capture_snapshot(target, expected_parent_sha256=expected_parent_sha256, captured_at=captured_at)
        directory = target.parent
        rollback_available = rollback_source is not None and _hash(rollback_source) == expected_parent_sha256
        reconciled = snapshot.reconciliation_state is TargetReconciliation.TARGET_MATCHES_EXPECTED_PARENT
        status = "PASS_READ_ONLY" if reconciled and rollback_available and not lock_path.exists() else "TARGET_RECONCILIATION_REQUIRED" if not reconciled else "PREFLIGHT_BLOCKED"
        return FinalPreflight(
            snapshot=snapshot,
            exists=target.exists(),
            is_regular_file=target.is_file(),
            readable=os.access(target, os.R_OK),
            write_permission_assessment=os.access(target, os.W_OK),
            target_directory_exists=directory.is_dir(),
            target_directory_write_permission_assessment=os.access(directory, os.W_OK),
            atomic_replacement_capability_assessment="WINDOWS_SAME_DIRECTORY_OS_REPLACE_SUPPORTED_REQUIRES_FUTURE_STANDARD_UAC_RUNAS_REVERIFY",
            conflict_lock_present=lock_path.exists(),
            backup_destination_capability_assessment=backup_root.is_dir() and os.access(backup_root, os.W_OK),
            rollback_source_available=rollback_available,
            status=status,
        )

    @staticmethod
    def rollback_plan(plan: DeploymentPlan, parent_bytes: bytes | None) -> RollbackPlan:
        ready = parent_bytes is not None and _hash(parent_bytes) == plan.expected_current_sha256
        return RollbackPlan(
            f"rollback.{plan.deployment_id}",
            plan.deployment_id,
            plan.target_path,
            plan.candidate_sha256,
            plan.expected_current_revision_id,
            plan.expected_current_sha256,
            "post/studio/deployment/<deployment-id>/backups/<parent-sha>.dat; immutable; exact readback",
            RollbackReadinessStatus.ROLLBACK_READY if ready else RollbackReadinessStatus.EXACT_PARENT_UNAVAILABLE,
        )

    @staticmethod
    def create_window(
        *,
        window_id: str,
        plan: DeploymentPlan,
        snapshot: TargetSnapshot,
        decision: ProductionActivationDecision,
        approved_from: str,
        expires_at: str,
        actor_identity: str,
    ) -> ActivationWindow:
        if decision.decision is not ProductionDecision.APPROVE_ACTIVATION_WINDOW:
            raise CamInvariantError("Activation window requires a fresh attributable owner approval")
        if decision.candidate_sha256 != plan.candidate_sha256 or decision.target_path != plan.target_path:
            raise CamInvariantError("Owner decision does not match deployment plan")
        if decision.deployment_plan_fingerprint != plan.fingerprint:
            raise CamInvariantError("Owner decision deployment fingerprint mismatch")
        if snapshot.is_stale() or snapshot.bytes_sha256 != plan.expected_current_sha256:
            raise CamInvariantError("Activation window requires a current exact-parent target snapshot")
        return ActivationWindow(window_id, plan.deployment_id, snapshot.fingerprint, plan.candidate_sha256,
                                decision.decision_id, decision.fingerprint, approved_from, expires_at,
                                actor_identity, ActivationWindowStatus.NOT_AUTHORIZED)

    @staticmethod
    def phase2_gate(*, freshness: EvidenceFreshness, rollback: RollbackPlan, decision: ProductionActivationDecision, window: ActivationWindow | None) -> Phase2GateStatus:
        if freshness.status is not EvidenceFreshnessStatus.FRESH or rollback.status is not RollbackReadinessStatus.ROLLBACK_READY:
            return Phase2GateStatus.CHUA_DUOC_PHEP_KICH_HOAT
        if decision.decision is ProductionDecision.NOT_DECIDED:
            return Phase2GateStatus.SAN_SANG_CHO_PHE_DUYET_KICH_HOAT
        if window is None or window.status is not ActivationWindowStatus.AUTHORIZED:
            return Phase2GateStatus.CHUA_DUOC_PHEP_KICH_HOAT
        return Phase2GateStatus.CUA_SO_HOP_LE

    @staticmethod
    def build_package(
        target: Path,
        *,
        plan: DeploymentPlan,
        rollback: RollbackPlan,
        snapshot: TargetSnapshot,
        freshness: EvidenceFreshness,
        candidate_bytes: bytes,
        parent_bytes: bytes,
        generated_nc_bytes: bytes,
        validation_evidence: dict[str, object],
        regression_evidence: dict[str, object],
        diff_summary: dict[str, object],
    ) -> ProductionActivationPackage:
        if _hash(candidate_bytes) != plan.candidate_sha256 or _hash(parent_bytes) != plan.expected_current_sha256:
            raise CamInvariantError("Activation package source identity mismatch")
        if freshness.status is not EvidenceFreshnessStatus.FRESH:
            raise CamInvariantError("Activation package requires fresh evidence")
        if rollback.status is not RollbackReadinessStatus.ROLLBACK_READY:
            raise CamInvariantError("Activation package requires exact rollback readiness")
        if snapshot.bytes_sha256 != plan.expected_current_sha256 or snapshot.is_stale():
            raise CamInvariantError("Activation package target snapshot is stale")
        payloads: dict[str, bytes] = {
            "candidate/FANUC-SHL.dat": candidate_bytes,
            "parent/FANUC-SHL.dat": parent_bytes,
            "evidence/generated-nc.fn": generated_nc_bytes,
            "plans/deployment-plan.json": _canonical(plan.payload()),
            "plans/rollback-plan.json": _canonical(rollback.payload()),
            "preflight/target-snapshot.json": _canonical(snapshot.payload()),
            "evidence/freshness.json": _canonical(freshness.payload()),
            "evidence/validation.json": _canonical(validation_evidence),
            "evidence/regression.json": _canonical(regression_evidence),
            "review/exact-diff.json": _canonical(diff_summary),
            "policy/audit-and-recovery.json": _canonical({
                "audit_policy": "ATTRIBUTABLE_IMMUTABLE_STRUCTURED_RECORDS",
                "recovery_policy": "REHASH_TARGET_BYTES_AFTER_RESTART_FAIL_CLOSED",
                "import_activation": False,
                "production_write": False,
                "uac_policy": "STANDARD_WINDOWS_RUNAS_ONLY_FOR_FUTURE_SEPARATE_AUTHORITY",
            }),
        }
        entries = [{"path": name, "size": len(data), "sha256": _hash(data)} for name, data in sorted(payloads.items())]
        manifest: dict[str, object] = {
            "format": "HMS_POST_PRODUCTION_ACTIVATION_PACKAGE",
            "format_version": PRODUCTION_WORKFLOW_FORMAT_VERSION,
            "post_id": plan.post_id,
            "candidate_revision_id": plan.candidate_revision_id,
            "candidate_sha256": plan.candidate_sha256,
            "expected_parent_revision_id": plan.expected_current_revision_id,
            "expected_parent_sha256": plan.expected_current_sha256,
            "target_path": plan.target_path,
            "machine_binding": plan.machine_binding.to_dict(),
            "deployment_plan_fingerprint": plan.fingerprint.to_dict(),
            "rollback_plan_fingerprint": rollback.fingerprint.to_dict(),
            "target_snapshot_fingerprint": snapshot.fingerprint.to_dict(),
            "approval_requirements": "FRESH_PRODUCTION_OWNER_DECISION_AND_EXPLICIT_EXPIRING_WINDOW",
            "deployment_state": "NOT_ACTIVE_GLOBALLY",
            "auto_activate_on_import": False,
            "files": entries,
        }
        manifest_bytes = _canonical(manifest)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                for name, data in sorted(payloads.items()):
                    ProductionActivationWorkflow._zip_write(archive, name, data)
                ProductionActivationWorkflow._zip_write(archive, "package-manifest.json", manifest_bytes)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return ProductionActivationPackage(str(target), _hash(target.read_bytes()), manifest, ContentFingerprint.from_payload(manifest))

    @staticmethod
    def _zip_write(archive: ZipFile, name: str, data: bytes) -> None:
        info = ZipInfo(name)
        info.date_time = (1980, 1, 1, 0, 0, 0)
        info.compress_type = ZIP_DEFLATED
        info.create_system = 0
        archive.writestr(info, data)


class ElevatedActivationVerifier:
    """Independent read-only verifier for a future standard-UAC helper."""

    @staticmethod
    def verify(
        *,
        target: Path,
        candidate_bytes: bytes,
        plan: DeploymentPlan,
        snapshot: TargetSnapshot,
        rollback: RollbackPlan,
        decision: ProductionActivationDecision,
        window: ActivationWindow,
        now: str,
    ) -> dict[str, object]:
        checks = {
            "target_path": str(target.resolve(strict=False)) == str(Path(plan.target_path).resolve(strict=False)),
            "target_current_sha256": target.is_file() and _hash(target.read_bytes()) == plan.expected_current_sha256,
            "snapshot_current": not snapshot.is_stale() and snapshot.bytes_sha256 == plan.expected_current_sha256,
            "candidate_sha256": _hash(candidate_bytes) == plan.candidate_sha256,
            "rollback_ready": rollback.status is RollbackReadinessStatus.ROLLBACK_READY and rollback.restore_sha256 == plan.expected_current_sha256,
            "deployment_plan_fingerprint": decision.deployment_plan_fingerprint == plan.fingerprint,
            "rollback_plan_fingerprint": decision.rollback_plan_fingerprint == rollback.fingerprint,
            "activation_window": window.evaluate(now=now, snapshot=snapshot, candidate_sha256=plan.candidate_sha256, decision=decision).status is ActivationWindowStatus.AUTHORIZED,
        }
        return {"format": "HMS_POST_ELEVATED_HELPER_VERIFICATION", "format_version": PRODUCTION_WORKFLOW_FORMAT_VERSION,
                "checks": checks, "verified": all(checks.values()), "write_performed": False,
                "elevation_policy": "STANDARD_WINDOWS_UAC_RUNAS_ONLY"}


__all__ = [
    "ActivationWindow", "ActivationWindowStatus", "ElevatedActivationVerifier",
    "EvidenceFreshness", "EvidenceFreshnessStatus", "FinalPreflight",
    "Phase2GateStatus", "ProductionActivationDecision", "ProductionActivationPackage",
    "ProductionActivationWorkflow", "ProductionDecision", "ProductionDecisionStatus", "ProductionWorkflowStore",
    "PRODUCTION_WORKFLOW_FORMAT_VERSION", "RollbackPlan", "RollbackReadinessStatus",
    "TargetSnapshot",
]
