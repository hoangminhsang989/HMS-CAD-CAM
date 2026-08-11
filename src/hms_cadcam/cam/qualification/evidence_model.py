"""External dry-run evidence, acceptance policy, and Level2 promotion gate."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import (
    ContentFingerprint,
    DependencyFingerprint,
    GeometryFingerprint,
)
from hms_cadcam.cam.qualification.model import QualificationLevel, QualificationReport
from hms_cadcam.cam.qualification.physical_model import (
    MachineSetupQualification,
    PhysicalReadinessResult,
)


LEVEL2_RECORD_FORMAT = "HMS_STAGE18A_LEVEL2_QUALIFICATION_RECORD"
LEVEL2_RECORD_VERSION = 1


class EvidenceState(StrEnum):
    NOT_PERFORMED = "NOT_PERFORMED"
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"
    STALE = "STALE"
    INVALID = "INVALID"


class DryRunMode(StrEnum):
    CONTROLLER_GRAPHICS = "CONTROLLER_GRAPHICS"
    DRY_RUN = "DRY_RUN"
    SINGLE_BLOCK = "SINGLE_BLOCK"
    AIR_CUT = "AIR_CUT"


class EvidenceAttachmentRole(StrEnum):
    PHOTO = "PHOTO"
    CONTROLLER_SCREENSHOT = "CONTROLLER_SCREENSHOT"
    SIGNED_CHECKLIST = "SIGNED_CHECKLIST"
    NOTES = "NOTES"
    NC_COMPARISON = "NC_COMPARISON"
    MACHINE_LOG_EXPORT = "MACHINE_LOG_EXPORT"


class Level2WorkflowState(StrEnum):
    LEVEL1_STATICALLY_VALIDATED = "LEVEL1_STATICALLY_VALIDATED"
    READY_FOR_EXTERNAL_LEVEL2_EVIDENCE = "READY_FOR_EXTERNAL_LEVEL2_EVIDENCE"
    LEVEL2_EVIDENCE_PENDING = "LEVEL2_EVIDENCE_PENDING"
    DRY_RUN_QUALIFIED = "DRY_RUN_QUALIFIED"
    LEVEL2_EVIDENCE_FAILED = "LEVEL2_EVIDENCE_FAILED"
    LEVEL2_EVIDENCE_STALE = "LEVEL2_EVIDENCE_STALE"


def _text(value: str, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CamValidationError(f"{name} is invalid")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise CamValidationError(f"{name} contains control characters")
    return value.strip()


def _optional_text(value: str | None, name: str, *, maximum: int = 4096) -> str | None:
    return None if value is None else _text(value, name, maximum=maximum)


def _timestamp(value: str, name: str) -> str:
    normalized = _text(value, name, maximum=64)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise CamValidationError(f"{name} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CamValidationError(f"{name} requires a timezone")
    return normalized


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise CamValidationError(f"{name} is invalid")
    return value


def _fingerprint(data: Any, name: str) -> ContentFingerprint:
    try:
        kind = data.get("kind") if isinstance(data, dict) else None
        fingerprint_type = {
            ContentFingerprint.KIND: ContentFingerprint,
            DependencyFingerprint.KIND: DependencyFingerprint,
            GeometryFingerprint.KIND: GeometryFingerprint,
        }.get(kind)
        if fingerprint_type is None:
            raise CamValidationError("Unsupported fingerprint kind")
        return fingerprint_type.from_dict(data)
    except (TypeError, CamValidationError) as error:
        raise CamValidationError(f"{name} is invalid") from error


@dataclass(frozen=True, slots=True)
class EvidenceAttachment:
    filename: str
    byte_length: int
    sha256: str
    role: EvidenceAttachmentRole
    captured_at: str
    provenance: str
    reference: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "filename", _text(self.filename, "Evidence filename", maximum=255))
        if Path(self.filename).name != self.filename:
            raise CamValidationError("Evidence filename must not contain a path")
        if type(self.byte_length) is not int or self.byte_length < 0:
            raise CamValidationError("Evidence byte length is invalid")
        object.__setattr__(self, "sha256", _sha256(self.sha256, "Evidence SHA-256"))
        if not isinstance(self.role, EvidenceAttachmentRole):
            raise CamValidationError("Evidence attachment role is invalid")
        object.__setattr__(self, "captured_at", _timestamp(self.captured_at, "Evidence timestamp"))
        object.__setattr__(self, "provenance", _text(self.provenance, "Evidence provenance"))
        object.__setattr__(self, "reference", _text(self.reference, "Evidence reference", maximum=2048))

    @classmethod
    def from_local_file(
        cls,
        path: Path,
        *,
        role: EvidenceAttachmentRole,
        captured_at: str,
        provenance: str,
    ) -> "EvidenceAttachment":
        """Hash one existing local attachment without copying or embedding it."""

        if not isinstance(path, Path):
            raise TypeError("path must be pathlib.Path")
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_file():
                raise OSError("Evidence reference is not a file")
            payload = resolved.read_bytes()
        except OSError as error:
            raise CamValidationError("Evidence file is unavailable") from error
        return cls(
            resolved.name, len(payload), hashlib.sha256(payload).hexdigest(), role,
            captured_at, provenance, str(resolved),
        )

    def current_state(self) -> EvidenceState:
        """Re-hash local bytes; missing or changed references are never PASS."""

        path = Path(self.reference)
        if path.name != self.filename:
            return EvidenceState.STALE
        try:
            if not path.is_file():
                return EvidenceState.INVALID
            payload = path.read_bytes()
        except OSError:
            return EvidenceState.INVALID
        if len(payload) != self.byte_length or hashlib.sha256(payload).hexdigest() != self.sha256:
            return EvidenceState.STALE
        return EvidenceState.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename, "byte_length": self.byte_length,
            "sha256": self.sha256, "role": self.role.value,
            "captured_at": self.captured_at, "provenance": self.provenance,
            "reference": self.reference,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceAttachment":
        fields = {
            "filename", "byte_length", "sha256", "role", "captured_at",
            "provenance", "reference",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Evidence attachment payload is malformed")
        try:
            role = EvidenceAttachmentRole(data["role"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Evidence attachment role is invalid") from error
        return cls(
            data["filename"], data["byte_length"], data["sha256"], role,
            data["captured_at"], data["provenance"], data["reference"],
        )


@dataclass(frozen=True, slots=True)
class OwnerAcceptanceRecord:
    operator: str
    verifier: str | None
    product_owner: str | None
    result: EvidenceState
    accepted_at: str
    notes: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operator", _text(self.operator, "Operator"))
        object.__setattr__(self, "verifier", _optional_text(self.verifier, "Verifier"))
        object.__setattr__(self, "product_owner", _optional_text(self.product_owner, "Product owner"))
        if self.result not in {EvidenceState.PASS, EvidenceState.FAIL, EvidenceState.PENDING}:
            raise CamValidationError("Acceptance result is invalid")
        object.__setattr__(self, "accepted_at", _timestamp(self.accepted_at, "Acceptance timestamp"))
        object.__setattr__(self, "notes", _text(self.notes, "Acceptance notes"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator": self.operator, "verifier": self.verifier,
            "product_owner": self.product_owner, "result": self.result.value,
            "accepted_at": self.accepted_at, "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OwnerAcceptanceRecord":
        fields = {"operator", "verifier", "product_owner", "result", "accepted_at", "notes"}
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Acceptance record payload is malformed")
        try:
            result = EvidenceState(data["result"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Acceptance record result is invalid") from error
        return cls(
            data["operator"], data["verifier"], data["product_owner"], result,
            data["accepted_at"], data["notes"],
        )


@dataclass(frozen=True, slots=True)
class PhysicalAcceptancePolicy:
    """Owner-defined policy; ``None`` means undecided and therefore blocks."""

    policy_id: str
    policy_revision: int
    controller_graphics_required: bool | None = None
    dry_run_required: bool | None = None
    single_block_required: bool | None = None
    air_cut_required: bool | None = None
    operator_signoff_required: bool | None = None
    verifier_signoff_required: bool | None = None
    owner_signoff_required: bool | None = None
    owner_authority: str | None = None
    confirmed_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _text(self.policy_id, "Policy ID"))
        if type(self.policy_revision) is not int or self.policy_revision <= 0:
            raise CamValidationError("Policy revision is invalid")
        for name in (
            "controller_graphics_required", "dry_run_required", "single_block_required",
            "air_cut_required", "operator_signoff_required", "verifier_signoff_required",
            "owner_signoff_required",
        ):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise CamValidationError(f"{name} is invalid")
        object.__setattr__(self, "owner_authority", _optional_text(self.owner_authority, "Policy owner"))
        if self.confirmed_at is not None:
            object.__setattr__(self, "confirmed_at", _timestamp(self.confirmed_at, "Policy timestamp"))
        if (self.owner_authority is None) != (self.confirmed_at is None):
            raise CamInvariantError("Policy authority and timestamp must be present together")

    @property
    def confirmed(self) -> bool:
        decisions = (
            self.controller_graphics_required, self.dry_run_required,
            self.single_block_required, self.air_cut_required,
            self.operator_signoff_required, self.verifier_signoff_required,
            self.owner_signoff_required,
        )
        return (
            self.owner_authority is not None
            and all(value is not None for value in decisions)
            and any(decisions[:4])
        )

    @property
    def required_modes(self) -> tuple[DryRunMode, ...]:
        if not self.confirmed:
            return ()
        pairs = (
            (self.controller_graphics_required, DryRunMode.CONTROLLER_GRAPHICS),
            (self.dry_run_required, DryRunMode.DRY_RUN),
            (self.single_block_required, DryRunMode.SINGLE_BLOCK),
            (self.air_cut_required, DryRunMode.AIR_CUT),
        )
        return tuple(mode for required, mode in pairs if required)

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id, "policy_revision": self.policy_revision,
            "controller_graphics_required": self.controller_graphics_required,
            "dry_run_required": self.dry_run_required,
            "single_block_required": self.single_block_required,
            "air_cut_required": self.air_cut_required,
            "operator_signoff_required": self.operator_signoff_required,
            "verifier_signoff_required": self.verifier_signoff_required,
            "owner_signoff_required": self.owner_signoff_required,
            "owner_authority": self.owner_authority, "confirmed_at": self.confirmed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PhysicalAcceptancePolicy":
        fields = {
            "policy_id", "policy_revision", "controller_graphics_required",
            "dry_run_required", "single_block_required", "air_cut_required",
            "operator_signoff_required", "verifier_signoff_required",
            "owner_signoff_required", "owner_authority", "confirmed_at",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Physical acceptance policy payload is malformed")
        return cls(**data)


@dataclass(frozen=True, slots=True)
class DryRunQualificationEvidence:
    evidence_id: str
    machine_identity: str
    controller_identity: str
    nc_sha256: str
    machine_profile_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    tool_set_fingerprint: ContentFingerprint
    post_fingerprint: ContentFingerprint
    qualification_contract_fingerprint: ContentFingerprint
    acceptance_policy_fingerprint: ContentFingerprint
    work_offset: str
    performed_at: str
    operator: str
    authority: str
    run_mode: DryRunMode
    result: EvidenceState
    observations: str
    blockers: tuple[str, ...]
    attachments: tuple[EvidenceAttachment, ...]
    acceptance: OwnerAcceptanceRecord
    remediation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _text(self.evidence_id, "Evidence ID"))
        object.__setattr__(self, "machine_identity", _text(self.machine_identity, "Machine identity"))
        object.__setattr__(self, "controller_identity", _text(self.controller_identity, "Controller identity"))
        object.__setattr__(self, "nc_sha256", _sha256(self.nc_sha256, "Evidence NC SHA-256"))
        for name in (
            "machine_profile_fingerprint", "setup_fingerprint", "tool_set_fingerprint",
            "post_fingerprint", "qualification_contract_fingerprint",
            "acceptance_policy_fingerprint",
        ):
            if not isinstance(getattr(self, name), ContentFingerprint):
                raise CamValidationError(f"Evidence {name} is invalid")
        object.__setattr__(self, "work_offset", _text(self.work_offset, "Evidence work offset", maximum=8).upper())
        if self.work_offset != "G54":
            raise CamValidationError("Only G54 evidence is accepted")
        object.__setattr__(self, "performed_at", _timestamp(self.performed_at, "Evidence timestamp"))
        object.__setattr__(self, "operator", _text(self.operator, "Evidence operator"))
        object.__setattr__(self, "authority", _text(self.authority, "Evidence authority"))
        if not isinstance(self.run_mode, DryRunMode):
            raise CamValidationError("Dry-run mode is invalid")
        if self.result not in {EvidenceState.PENDING, EvidenceState.PASS, EvidenceState.FAIL}:
            raise CamValidationError("Dry-run result is invalid")
        object.__setattr__(self, "observations", _text(self.observations, "Evidence observations"))
        if not isinstance(self.blockers, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.blockers
        ):
            raise CamValidationError("Evidence blockers are invalid")
        object.__setattr__(self, "blockers", tuple(_text(item, "Evidence blocker") for item in self.blockers))
        if not isinstance(self.attachments, tuple) or any(
            not isinstance(item, EvidenceAttachment) for item in self.attachments
        ):
            raise CamValidationError("Evidence attachments are invalid")
        if len({(item.reference, item.sha256) for item in self.attachments}) != len(self.attachments):
            raise CamInvariantError("Evidence attachments must be unique")
        if len({item.role for item in self.attachments}) != len(self.attachments):
            raise CamInvariantError("Evidence attachment roles must be unique")
        if not isinstance(self.acceptance, OwnerAcceptanceRecord):
            raise CamValidationError("Evidence acceptance is invalid")
        object.__setattr__(self, "remediation", _optional_text(self.remediation, "Evidence remediation"))
        if self.result is EvidenceState.PASS and self.blockers:
            raise CamInvariantError("PASS evidence cannot retain blockers")
        if self.result is EvidenceState.PASS and not self.attachments:
            raise CamInvariantError("PASS evidence requires at least one attachment")
        if self.acceptance.result is not self.result:
            raise CamInvariantError("Evidence and acceptance results must agree")
        if self.acceptance.operator != self.operator:
            raise CamInvariantError("Evidence and acceptance operators must agree")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def attachment_state(self) -> EvidenceState:
        states = tuple(item.current_state() for item in self.attachments)
        if EvidenceState.INVALID in states:
            return EvidenceState.INVALID
        if EvidenceState.STALE in states:
            return EvidenceState.STALE
        return EvidenceState.PASS

    def is_current(
        self,
        *,
        setup: MachineSetupQualification,
        controller_identity: str,
        qualification_contract_fingerprint: ContentFingerprint,
        acceptance_policy_fingerprint: ContentFingerprint,
    ) -> bool:
        return (
            self.nc_sha256 == setup.nc_sha256
            and self.machine_identity == setup.machine_profile_id
            and self.controller_identity == controller_identity
            and self.machine_profile_fingerprint == setup.machine_profile_fingerprint
            and self.setup_fingerprint == setup.fingerprint
            and self.tool_set_fingerprint == setup.tool_set_fingerprint
            and self.post_fingerprint == setup.post_fingerprint
            and self.qualification_contract_fingerprint == qualification_contract_fingerprint
            and self.acceptance_policy_fingerprint == acceptance_policy_fingerprint
            and self.work_offset == setup.work_offset_transform.work_offset
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id, "machine_identity": self.machine_identity,
            "controller_identity": self.controller_identity, "nc_sha256": self.nc_sha256,
            "machine_profile_fingerprint": self.machine_profile_fingerprint.to_dict(),
            "setup_fingerprint": self.setup_fingerprint.to_dict(),
            "tool_set_fingerprint": self.tool_set_fingerprint.to_dict(),
            "post_fingerprint": self.post_fingerprint.to_dict(),
            "qualification_contract_fingerprint": self.qualification_contract_fingerprint.to_dict(),
            "acceptance_policy_fingerprint": self.acceptance_policy_fingerprint.to_dict(),
            "work_offset": self.work_offset, "performed_at": self.performed_at,
            "operator": self.operator, "authority": self.authority,
            "run_mode": self.run_mode.value, "result": self.result.value,
            "observations": self.observations, "blockers": list(self.blockers),
            "attachments": [item.to_dict() for item in self.attachments],
            "acceptance": self.acceptance.to_dict(), "remediation": self.remediation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DryRunQualificationEvidence":
        fields = {
            "evidence_id", "machine_identity", "controller_identity", "nc_sha256",
            "machine_profile_fingerprint", "setup_fingerprint", "tool_set_fingerprint",
            "post_fingerprint", "qualification_contract_fingerprint",
            "acceptance_policy_fingerprint", "work_offset",
            "performed_at", "operator", "authority", "run_mode", "result",
            "observations", "blockers", "attachments", "acceptance", "remediation",
        }
        if (
            not isinstance(data, dict) or set(data) != fields
            or not isinstance(data["blockers"], list)
            or not isinstance(data["attachments"], list)
        ):
            raise CamValidationError("Dry-run evidence payload is malformed")
        try:
            mode = DryRunMode(data["run_mode"])
            result = EvidenceState(data["result"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Dry-run evidence enum is invalid") from error
        return cls(
            data["evidence_id"], data["machine_identity"], data["controller_identity"],
            data["nc_sha256"], _fingerprint(data["machine_profile_fingerprint"], "Machine fingerprint"),
            _fingerprint(data["setup_fingerprint"], "Setup fingerprint"),
            _fingerprint(data["tool_set_fingerprint"], "Tool set fingerprint"),
            _fingerprint(data["post_fingerprint"], "Post fingerprint"),
            _fingerprint(data["qualification_contract_fingerprint"], "Qualification contract fingerprint"),
            _fingerprint(data["acceptance_policy_fingerprint"], "Acceptance policy fingerprint"),
            data["work_offset"], data["performed_at"], data["operator"], data["authority"],
            mode, result, data["observations"], tuple(data["blockers"]),
            tuple(EvidenceAttachment.from_dict(item) for item in data["attachments"]),
            OwnerAcceptanceRecord.from_dict(data["acceptance"]), data["remediation"],
        )


@dataclass(frozen=True, slots=True)
class Level2Readiness:
    workflow_state: Level2WorkflowState
    ready: tuple[str, ...]
    missing: tuple[str, ...]
    blockers: tuple[str, ...]
    stale_reasons: tuple[str, ...]
    required_modes: tuple[DryRunMode, ...]
    satisfied_modes: tuple[DryRunMode, ...]
    machine_ready: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_state, Level2WorkflowState):
            raise CamValidationError("Level2 workflow state is invalid")
        if self.machine_ready is not False:
            raise CamInvariantError("Tranche2 can never set MACHINE_READY true")
        if self.workflow_state is Level2WorkflowState.DRY_RUN_QUALIFIED and (
            self.missing or self.blockers or self.stale_reasons
        ):
            raise CamInvariantError("Dry-run qualification cannot retain unresolved gates")

    @property
    def level2_achieved(self) -> bool:
        return self.workflow_state is Level2WorkflowState.DRY_RUN_QUALIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_state": self.workflow_state.value, "ready": list(self.ready),
            "missing": list(self.missing), "blockers": list(self.blockers),
            "stale_reasons": list(self.stale_reasons),
            "required_modes": [item.value for item in self.required_modes],
            "satisfied_modes": [item.value for item in self.satisfied_modes],
            "level2_achieved": self.level2_achieved, "machine_ready": self.machine_ready,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Level2Readiness":
        fields = {
            "workflow_state", "ready", "missing", "blockers", "stale_reasons",
            "required_modes", "satisfied_modes", "level2_achieved", "machine_ready",
        }
        if (
            not isinstance(data, dict)
            or set(data) != fields
            or any(
                not isinstance(data[name], list)
                for name in (
                    "ready", "missing", "blockers", "stale_reasons",
                    "required_modes", "satisfied_modes",
                )
            )
        ):
            raise CamValidationError("Level2 readiness payload is malformed")
        try:
            state = Level2WorkflowState(data["workflow_state"])
            required = tuple(DryRunMode(item) for item in data["required_modes"])
            satisfied = tuple(DryRunMode(item) for item in data["satisfied_modes"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Level2 readiness enum payload is invalid") from error
        expected_achieved = state is Level2WorkflowState.DRY_RUN_QUALIFIED
        if data["level2_achieved"] is not expected_achieved:
            raise CamInvariantError("Level2 achieved flag is derived and cannot be mutated")
        return cls(
            state,
            tuple(data["ready"]),
            tuple(data["missing"]),
            tuple(data["blockers"]),
            tuple(data["stale_reasons"]),
            required,
            satisfied,
            data["machine_ready"],
        )


@dataclass(frozen=True, slots=True)
class Level2QualificationRecord:
    record_id: str
    setup: MachineSetupQualification
    policy: PhysicalAcceptancePolicy
    attempts: tuple[DryRunQualificationEvidence, ...]
    created_at: str
    format_version: int = LEVEL2_RECORD_VERSION

    def __post_init__(self) -> None:
        if self.format_version != LEVEL2_RECORD_VERSION:
            raise CamValidationError("Unsupported Level2 record version")
        object.__setattr__(self, "record_id", _text(self.record_id, "Level2 record ID"))
        if not isinstance(self.setup, MachineSetupQualification):
            raise CamValidationError("Level2 setup is invalid")
        if not isinstance(self.policy, PhysicalAcceptancePolicy):
            raise CamValidationError("Level2 policy is invalid")
        if not isinstance(self.attempts, tuple) or any(
            not isinstance(item, DryRunQualificationEvidence) for item in self.attempts
        ):
            raise CamValidationError("Level2 attempts are invalid")
        if len({item.evidence_id for item in self.attempts}) != len(self.attempts):
            raise CamInvariantError("Evidence attempt IDs must be unique")
        timestamps = [datetime.fromisoformat(item.performed_at) for item in self.attempts]
        if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
            raise CamInvariantError("Evidence attempt timestamps must be strictly increasing")
        for index, attempt in enumerate(self.attempts):
            earlier_failed = any(
                previous.run_mode is attempt.run_mode and previous.result is EvidenceState.FAIL
                for previous in self.attempts[:index]
            )
            if earlier_failed and attempt.result is EvidenceState.PASS and attempt.remediation is None:
                raise CamInvariantError("A PASS after FAIL requires explicit remediation")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "Record timestamp"))

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def append_attempt(self, attempt: DryRunQualificationEvidence) -> "Level2QualificationRecord":
        """Append a new immutable attempt; prior FAIL records remain in chronology."""

        if not isinstance(attempt, DryRunQualificationEvidence):
            raise TypeError("attempt must be DryRunQualificationEvidence")
        return Level2QualificationRecord(
            self.record_id, self.setup, self.policy, (*self.attempts, attempt),
            self.created_at, self.format_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": LEVEL2_RECORD_FORMAT, "format_version": self.format_version,
            "record_id": self.record_id, "setup": self.setup.to_dict(),
            "policy": self.policy.to_dict(),
            "attempts": [item.to_dict() for item in self.attempts],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Level2QualificationRecord":
        fields = {"format", "format_version", "record_id", "setup", "policy", "attempts", "created_at"}
        if (
            not isinstance(data, dict) or set(data) != fields
            or data["format"] != LEVEL2_RECORD_FORMAT
            or not isinstance(data["attempts"], list)
        ):
            raise CamValidationError("Level2 qualification record payload is malformed")
        return cls(
            data["record_id"], MachineSetupQualification.from_dict(data["setup"]),
            PhysicalAcceptancePolicy.from_dict(data["policy"]),
            tuple(DryRunQualificationEvidence.from_dict(item) for item in data["attempts"]),
            data["created_at"], data["format_version"],
        )


def _signoff_missing(
    policy: PhysicalAcceptancePolicy,
    acceptance: OwnerAcceptanceRecord,
) -> tuple[str, ...]:
    missing: list[str] = []
    if policy.operator_signoff_required and not acceptance.operator:
        missing.append("OPERATOR_SIGNOFF_MISSING")
    if policy.verifier_signoff_required and acceptance.verifier is None:
        missing.append("VERIFIER_SIGNOFF_MISSING")
    if policy.owner_signoff_required and acceptance.product_owner is None:
        missing.append("OWNER_SIGNOFF_MISSING")
    return tuple(missing)


def assess_level2_readiness(
    *,
    level1_report: QualificationReport,
    record: Level2QualificationRecord,
    physical_readiness: PhysicalReadinessResult,
    current_nc_sha256: str,
    current_machine_profile_fingerprint: ContentFingerprint,
    current_post_fingerprint: ContentFingerprint,
    current_qualification_contract_fingerprint: ContentFingerprint,
    current_controller_identity: str,
) -> Level2Readiness:
    """Derive Level2 state; callers cannot manually mutate promotion flags."""

    if not isinstance(level1_report, QualificationReport):
        raise TypeError("level1_report must be QualificationReport")
    if level1_report.qualification_level is not QualificationLevel.STATICALLY_VALIDATED:
        return Level2Readiness(
            Level2WorkflowState.LEVEL1_STATICALLY_VALIDATED, (), (),
            ("LEVEL1_STATIC_QUALIFICATION_REQUIRED",), (), (), (),
        )
    setup = record.setup
    ready = ["STATIC_NC", "MACHINE_PROFILE", "TOOLS"]
    missing = list(physical_readiness.missing)
    blockers = list(physical_readiness.blockers)
    stale: list[str] = []
    if current_nc_sha256 != setup.nc_sha256 or level1_report.nc_sha256 != setup.nc_sha256:
        stale.append("NC_SHA_CHANGED")
    if current_machine_profile_fingerprint != setup.machine_profile_fingerprint:
        stale.append("MACHINE_PROFILE_CHANGED")
    if current_post_fingerprint != setup.post_fingerprint:
        stale.append("POST_CHANGED")
    if level1_report.machine_contract_fingerprint != current_qualification_contract_fingerprint:
        stale.append("QUALIFICATION_CONTRACT_CHANGED")
    if setup.work_offset_transform.authoritative:
        ready.append("G54_SETUP_TRANSFORM")
    else:
        missing.append("G54_PHYSICAL_TRANSFORM")
    if setup.fixture is not None and setup.fixture.placement_verified:
        ready.append("FIXTURE")
    if record.policy.confirmed:
        ready.append("EVIDENCE_POLICY")
    else:
        missing.append("OWNER_DEFINED_EVIDENCE_POLICY")
    required = record.policy.required_modes
    latest: dict[DryRunMode, DryRunQualificationEvidence] = {}
    latest_issue: dict[DryRunMode, str] = {}
    for attempt in record.attempts:
        if not attempt.is_current(
            setup=setup,
            controller_identity=current_controller_identity,
            qualification_contract_fingerprint=current_qualification_contract_fingerprint,
            acceptance_policy_fingerprint=record.policy.fingerprint,
        ):
            latest.pop(attempt.run_mode, None)
            latest_issue[attempt.run_mode] = (
                f"{attempt.evidence_id}:PHYSICAL_EVIDENCE_STALE"
            )
            continue
        attachment_state = attempt.attachment_state()
        if attachment_state is EvidenceState.INVALID:
            latest.pop(attempt.run_mode, None)
            latest_issue[attempt.run_mode] = f"{attempt.evidence_id}:ATTACHMENT_INVALID"
            continue
        if attachment_state is EvidenceState.STALE:
            latest.pop(attempt.run_mode, None)
            latest_issue[attempt.run_mode] = (
                f"{attempt.evidence_id}:ATTACHMENT_BYTES_CHANGED"
            )
            continue
        latest[attempt.run_mode] = attempt
        latest_issue.pop(attempt.run_mode, None)
    stale.extend(latest_issue[mode] for mode in required if mode in latest_issue)
    satisfied: list[DryRunMode] = []
    for mode in required:
        attempt = latest.get(mode)
        if attempt is None:
            missing.append(f"{mode.value}_EVIDENCE")
            continue
        if attempt.result is EvidenceState.FAIL:
            blockers.append(f"{mode.value}_FAILED")
            continue
        if attempt.result is EvidenceState.PENDING:
            missing.append(f"{mode.value}_PENDING")
            continue
        signoff = _signoff_missing(record.policy, attempt.acceptance)
        if signoff:
            missing.extend(signoff)
            continue
        satisfied.append(mode)
    normalized_missing = tuple(sorted(set(missing)))
    normalized_blockers = tuple(sorted(set(blockers)))
    normalized_stale = tuple(sorted(set(stale)))
    latest_failed = any(
        latest.get(mode) is not None and latest[mode].result is EvidenceState.FAIL
        for mode in required
    )
    if normalized_stale:
        state = Level2WorkflowState.LEVEL2_EVIDENCE_STALE
    elif normalized_blockers or latest_failed:
        state = Level2WorkflowState.LEVEL2_EVIDENCE_FAILED
    elif record.policy.confirmed and not normalized_missing and set(satisfied) == set(required):
        state = Level2WorkflowState.DRY_RUN_QUALIFIED
    elif record.attempts:
        state = Level2WorkflowState.LEVEL2_EVIDENCE_PENDING
    elif not physical_readiness.blockers and not physical_readiness.missing and record.policy.confirmed:
        state = Level2WorkflowState.READY_FOR_EXTERNAL_LEVEL2_EVIDENCE
    else:
        state = Level2WorkflowState.LEVEL1_STATICALLY_VALIDATED
    return Level2Readiness(
        state, tuple(sorted(set(ready))), normalized_missing, normalized_blockers,
        normalized_stale, required, tuple(satisfied), False,
    )


def level2_status_vi(readiness: Level2Readiness) -> str:
    """Vietnamese-first truthful status; no Tranche2 state says Machine Ready."""

    labels = {
        Level2WorkflowState.LEVEL1_STATICALLY_VALIDATED: "Đạt kiểm tra tĩnh",
        Level2WorkflowState.READY_FOR_EXTERNAL_LEVEL2_EVIDENCE: "Sẵn sàng kiểm tra trên máy",
        Level2WorkflowState.LEVEL2_EVIDENCE_PENDING: "Chờ dry-run",
        Level2WorkflowState.DRY_RUN_QUALIFIED: "Dry-run đạt",
        Level2WorkflowState.LEVEL2_EVIDENCE_FAILED: "Dry-run không đạt",
        Level2WorkflowState.LEVEL2_EVIDENCE_STALE: "Bằng chứng đã lỗi thời",
    }
    return labels[readiness.workflow_state]


__all__ = [
    "DryRunMode", "DryRunQualificationEvidence", "EvidenceAttachment",
    "EvidenceAttachmentRole", "EvidenceState", "LEVEL2_RECORD_FORMAT",
    "LEVEL2_RECORD_VERSION", "Level2QualificationRecord", "Level2Readiness",
    "Level2WorkflowState", "OwnerAcceptanceRecord", "PhysicalAcceptancePolicy",
    "assess_level2_readiness", "level2_status_vi",
]
