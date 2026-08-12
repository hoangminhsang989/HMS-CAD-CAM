"""Post-activation managed lifecycle reconstruction and portable history.

Target bytes are authoritative.  Records are linked and verified independently;
metadata alone can never project a Post as active.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint


ACTIVE_LIFECYCLE_FORMAT_VERSION = 1


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    """Return a fingerprint digest from either persisted representation."""

    if isinstance(value, dict):
        return str(value.get("digest", ""))
    return str(value or "")


def _record(path: Path, expected_format: str) -> tuple[dict[str, object], str]:
    try:
        data = path.read_bytes()
        value = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CamValidationError(f"Active lifecycle record unavailable: {path.name}") from error
    if not isinstance(value, dict) or value.get("format") != expected_format:
        raise CamValidationError(f"Active lifecycle record malformed: {path.name}")
    return value, _hash(data)


class ManagedActiveStatus(StrEnum):
    ACTIVE_MANAGED_REVISION = "ACTIVE_MANAGED_REVISION"
    POST_DA_BI_THAY_DOI_NGOAI_HMS = "POST_DA_BI_THAY_DOI_NGOAI_HMS"
    TARGET_MISSING = "TARGET_MISSING"
    TARGET_UNREADABLE = "TARGET_UNREADABLE"
    RECORD_RECONCILIATION_REQUIRED = "RECORD_RECONCILIATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class ActiveLifecyclePaths:
    target: Path
    activation_record: Path
    consumed_window_record: Path
    deployment_plan_record: Path
    rollback_plan_record: Path
    owner_decision_record: Path
    backup: Path
    deployment_lock: Path


@dataclass(frozen=True, slots=True)
class ActiveLifecycleProjection:
    post_id: str
    display_name: str
    active_revision_id: str
    active_sha256: str | None
    expected_active_sha256: str
    previous_revision_id: str
    previous_sha256: str
    machine_name: str
    controller_name: str
    tool_interface: str
    activated_at: str
    actor: str
    status: ManagedActiveStatus
    rollback_ready: bool
    drift_detected: bool
    window_consumed: bool
    lock_present: bool
    activation_record_sha256: str
    backup_sha256: str | None
    fingerprint: ContentFingerprint

    @property
    def can_reuse_activation_window(self) -> bool:
        return False

    def payload(self) -> dict[str, object]:
        return {
            "format": "HMS_POST_ACTIVE_LIFECYCLE_PROJECTION",
            "format_version": ACTIVE_LIFECYCLE_FORMAT_VERSION,
            "post_id": self.post_id,
            "display_name": self.display_name,
            "active_revision_id": self.active_revision_id,
            "active_sha256": self.active_sha256,
            "expected_active_sha256": self.expected_active_sha256,
            "previous_revision_id": self.previous_revision_id,
            "previous_sha256": self.previous_sha256,
            "machine_name": self.machine_name,
            "controller_name": self.controller_name,
            "tool_interface": self.tool_interface,
            "activated_at": self.activated_at,
            "actor": self.actor,
            "status": self.status.value,
            "rollback_ready": self.rollback_ready,
            "drift_detected": self.drift_detected,
            "window_consumed": self.window_consumed,
            "lock_present": self.lock_present,
            "activation_record_sha256": self.activation_record_sha256,
            "backup_sha256": self.backup_sha256,
        }


@dataclass(frozen=True, slots=True)
class ImportedActiveHistory:
    post_id: str
    active_revision_id: str
    active_sha256: str
    previous_sha256: str
    deployment_id: str
    backup_sha256: str
    informational_only: bool = True
    auto_activate: bool = False
    requires_local_reconciliation_and_approval: bool = True


class ActiveLifecycleService:
    """Reconstruct active state from current bytes plus linked immutable records."""

    def reconstruct(self, paths: ActiveLifecyclePaths) -> ActiveLifecycleProjection:
        activation, activation_sha = _record(paths.activation_record, "HMS_POST_PRODUCTION_ACTIVATION_RECORD")
        consumed, _consumed_sha = _record(paths.consumed_window_record, "HMS_POST_ACTIVATION_WINDOW")
        deployment, _deployment_sha = _record(paths.deployment_plan_record, "HMS_POST_DEPLOYMENT_PLAN")
        rollback, _rollback_sha = _record(paths.rollback_plan_record, "HMS_POST_ROLLBACK_PLAN")
        decision, _decision_sha = _record(paths.owner_decision_record, "HMS_POST_PRODUCTION_ACTIVATION_DECISION")
        expected_active = str(activation.get("new_sha256", ""))
        expected_parent = str(activation.get("previous_sha256", ""))
        deployment_id = str(activation.get("deployment_id", ""))
        target_path = str(paths.target)
        decision_fingerprint = _digest(decision.get("decision_fingerprint"))
        deployment_fingerprint = _digest(deployment.get("plan_fingerprint"))
        rollback_fingerprint = _digest(rollback.get("rollback_plan_fingerprint"))
        window_fingerprint = _digest(consumed.get("window_fingerprint"))
        record_links_valid = all((
            bool(expected_active and expected_parent and deployment_id),
            activation.get("production_state") in (None, "ACTIVE"),
            activation.get("readback_verified") in (None, True),
            activation.get("window_id") == consumed.get("window_id"),
            consumed.get("status") == "CONSUMED",
            consumed.get("activation_record_sha256") == activation_sha,
            activation.get("window_fingerprint") in (None, window_fingerprint),
            activation.get("deployment_plan_fingerprint") == deployment_fingerprint,
            activation.get("rollback_plan_fingerprint") == rollback_fingerprint,
            activation.get("decision_fingerprint") == decision_fingerprint,
            _digest(consumed.get("owner_decision_fingerprint")) in ("", decision_fingerprint),
            consumed.get("deployment_plan_id") in (None, deployment_id),
            rollback.get("deployment_plan_id") in (None, deployment_id),
            deployment.get("deployment_id") in (None, deployment_id),
            deployment.get("candidate_sha256") == expected_active,
            deployment.get("expected_current_sha256") == expected_parent,
            deployment.get("candidate_revision_id") in (None, activation.get("new_revision_id")),
            deployment.get("expected_current_revision_id") in (None, activation.get("previous_revision_id")),
            rollback.get("restore_sha256") == expected_parent,
            rollback.get("expected_active_sha256") == expected_active,
            rollback.get("restore_revision_id") in (None, activation.get("previous_revision_id")),
            rollback.get("status") == "ROLLBACK_READY",
            decision.get("decision") == "APPROVE_ACTIVATION_WINDOW",
            decision.get("candidate_sha256") in (None, expected_active),
            decision.get("expected_parent_sha256") in (None, expected_parent),
            _digest(decision.get("deployment_plan_fingerprint")) in ("", deployment_fingerprint),
            _digest(decision.get("rollback_plan_fingerprint")) in ("", rollback_fingerprint),
            decision.get("owner_identity") in (None, activation.get("actor")),
            consumed.get("consumed_by") in (None, activation.get("actor")),
            decision.get("target_path") == target_path,
            activation.get("target_path") == target_path,
            deployment.get("target_path") in (None, target_path),
            rollback.get("target_path") in (None, target_path),
            activation.get("backup_path") in (None, str(paths.backup)),
            activation.get("backup_sha256") in (None, expected_parent),
            not paths.deployment_lock.exists(),
        ))
        target_sha: str | None
        try:
            target_sha = _hash(paths.target.read_bytes())
            target_state = ManagedActiveStatus.ACTIVE_MANAGED_REVISION if target_sha == expected_active else ManagedActiveStatus.POST_DA_BI_THAY_DOI_NGOAI_HMS
        except FileNotFoundError:
            target_sha = None; target_state = ManagedActiveStatus.TARGET_MISSING
        except OSError:
            target_sha = None; target_state = ManagedActiveStatus.TARGET_UNREADABLE
        try:
            backup_sha = _hash(paths.backup.read_bytes())
        except OSError:
            backup_sha = None
        rollback_ready = bool(record_links_valid and backup_sha == expected_parent)
        status = target_state if record_links_valid else ManagedActiveStatus.RECORD_RECONCILIATION_REQUIRED
        binding = deployment.get("machine_binding", {})
        if not isinstance(binding, dict):
            binding = {}
        payload = {
            "post_id": activation.get("deployment_id"), "active_revision_id": activation.get("new_revision_id"),
            "active_sha256": target_sha, "expected_active_sha256": expected_active,
            "status": status.value, "rollback_ready": rollback_ready,
            "activation_record_sha256": activation_sha, "backup_sha256": backup_sha,
            "window_consumed": consumed.get("status") == "CONSUMED", "lock_present": paths.deployment_lock.exists(),
        }
        return ActiveLifecycleProjection(
            post_id=str(deployment.get("post_id", "fanuc-shl")),
            display_name=str(binding.get("post_family", "FANUC-SHL")),
            active_revision_id=str(activation.get("new_revision_id", "")), active_sha256=target_sha,
            expected_active_sha256=expected_active, previous_revision_id=str(activation.get("previous_revision_id", "")),
            previous_sha256=expected_parent, machine_name="FANUC ROBODRILL α-D21MiB",
            controller_name="FANUC 31i-B", tool_interface=str(binding.get("tool_interface", "")),
            activated_at=str(activation.get("activated_at", "")), actor=str(activation.get("actor", "")),
            status=status, rollback_ready=rollback_ready,
            drift_detected=status is ManagedActiveStatus.POST_DA_BI_THAY_DOI_NGOAI_HMS,
            window_consumed=consumed.get("status") == "CONSUMED", lock_present=paths.deployment_lock.exists(),
            activation_record_sha256=activation_sha, backup_sha256=backup_sha,
            fingerprint=ContentFingerprint.from_payload(payload),
        )

    def export_active_history(self, target: Path, *, projection: ActiveLifecycleProjection, paths: ActiveLifecyclePaths, original_bytes: bytes, active_bytes: bytes, validation: dict[str, object], regression: dict[str, object]) -> dict[str, object]:
        if projection.status is not ManagedActiveStatus.ACTIVE_MANAGED_REVISION or projection.active_sha256 != _hash(active_bytes):
            raise CamInvariantError("Active history export requires current managed active bytes")
        if projection.previous_sha256 != _hash(original_bytes) or not projection.rollback_ready:
            raise CamInvariantError("Active history export requires exact rollback bytes")
        payloads = {
            "lineage/original.dat": original_bytes, "lineage/active-r233.dat": active_bytes,
            "records/activation.json": paths.activation_record.read_bytes(),
            "records/consumed-window.json": paths.consumed_window_record.read_bytes(),
            "records/deployment-plan.json": paths.deployment_plan_record.read_bytes(),
            "records/rollback-plan.json": paths.rollback_plan_record.read_bytes(),
            "records/owner-decision.json": paths.owner_decision_record.read_bytes(),
            "evidence/validation.json": _canonical(validation), "evidence/regression.json": _canonical(regression),
            "projection/active-state.json": _canonical(projection.payload()),
        }
        entries = [{"path": name, "size": len(data), "sha256": _hash(data)} for name, data in sorted(payloads.items())]
        activation_payload = json.loads(payloads["records/activation.json"].decode("utf-8"))
        manifest = {"format": "HMS_POST_ACTIVE_HISTORY_PACKAGE", "format_version": 1,
                    "post_id": projection.post_id, "active_revision_id": projection.active_revision_id,
                    "active_sha256": projection.active_sha256, "previous_sha256": projection.previous_sha256,
                    "deployment_id": activation_payload.get("deployment_id"),
                    "activation_record_sha256": projection.activation_record_sha256,
                    "backup_sha256": projection.backup_sha256,
                    "rollback_ready": projection.rollback_ready, "machine": projection.machine_name,
                    "controller": projection.controller_name, "tool_interface": projection.tool_interface,
                    "imported_active_state_is_informational": True, "auto_activate_on_import": False,
                    "requires_local_reconciliation_and_approval": True, "files": entries}
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                for name, data in sorted(payloads.items()):
                    self._zip_write(archive, name, data)
                self._zip_write(archive, "package-manifest.json", _canonical(manifest))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"path": str(target), "sha256": _hash(target.read_bytes()), "manifest": manifest,
                "manifest_fingerprint": ContentFingerprint.from_payload(manifest).to_dict()}

    def import_active_history(self, source: Path) -> ImportedActiveHistory:
        """Inspect a portable history package without activating or writing target bytes."""

        try:
            with ZipFile(source, "r") as archive:
                manifest = json.loads(archive.read("package-manifest.json").decode("utf-8"))
                if manifest.get("format") != "HMS_POST_ACTIVE_HISTORY_PACKAGE":
                    raise CamValidationError("Active history package format is invalid")
                if manifest.get("auto_activate_on_import") is not False or manifest.get("imported_active_state_is_informational") is not True or manifest.get("requires_local_reconciliation_and_approval") is not True:
                    raise CamValidationError("Active history package import policy is unsafe")
                files = manifest.get("files")
                if not isinstance(files, list):
                    raise CamValidationError("Active history package inventory is invalid")
                expected_names = {"package-manifest.json"}
                for item in files:
                    if not isinstance(item, dict):
                        raise CamValidationError("Active history package inventory entry is invalid")
                    name = str(item.get("path", ""))
                    path = Path(name)
                    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
                        raise CamValidationError("Active history package path is unsafe")
                    expected_names.add(name)
                    data = archive.read(name)
                    if len(data) != item.get("size") or _hash(data) != item.get("sha256"):
                        raise CamValidationError("Active history package file fingerprint mismatch")
                if set(archive.namelist()) != expected_names or len(archive.namelist()) != len(expected_names):
                    raise CamValidationError("Active history package contains unregistered entries")
        except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, BadZipFile) as error:
            raise CamValidationError("Active history package is unreadable") from error
        return ImportedActiveHistory(
            post_id=str(manifest.get("post_id", "")),
            active_revision_id=str(manifest.get("active_revision_id", "")),
            active_sha256=str(manifest.get("active_sha256", "")),
            previous_sha256=str(manifest.get("previous_sha256", "")),
            deployment_id=str(manifest.get("deployment_id", "")),
            backup_sha256=str(manifest.get("backup_sha256", "")),
        )

    @staticmethod
    def _zip_write(archive: ZipFile, name: str, data: bytes) -> None:
        info = ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = ZIP_DEFLATED; info.create_system = 0
        archive.writestr(info, data)


__all__ = ["ACTIVE_LIFECYCLE_FORMAT_VERSION", "ActiveLifecyclePaths", "ActiveLifecycleProjection", "ActiveLifecycleService", "ImportedActiveHistory", "ManagedActiveStatus"]
