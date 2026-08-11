"""Immutable Stage18A Tranche3 offline NC verification contracts.

These contracts describe software review and controlled handoff only.  They do
not model a controller connection, physical dry-run result, or MACHINE READY
state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import re
from typing import Any

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint


OFFLINE_VERIFICATION_FORMAT_VERSION = 1
TRANCHE3_SCOPE_MARKER = "STAGE18A_TRANCHE3_SCOPE_FROZEN"
TRANCHE3_COUNTERFACTUAL_MARKER = "STAGE18A_TRANCHE3_PRODUCT_DELTA_COUNTERFACTUALLY_PROVEN"
NO_CNC_CONTROL_MARKER = "STAGE18A_TRANCHE3_NO_CNC_CONTROL_BOUNDARY_PRESERVED"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,127}")


def _text(value: str, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CamValidationError(f"{name} is invalid")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise CamValidationError(f"{name} contains control characters")
    return value.strip()


def _optional_text(value: str | None, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CamValidationError(f"{name} is invalid")
    return value


def _timestamp(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    normalized = _text(value, name, maximum=64)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise CamValidationError(f"{name} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CamValidationError(f"{name} requires a timezone")
    return normalized


def _fingerprint(value: ContentFingerprint, name: str) -> ContentFingerprint:
    if not isinstance(value, ContentFingerprint):
        raise CamValidationError(f"{name} is invalid")
    return value


class VerificationSessionState(StrEnum):
    DRAFT = "DRAFT"
    ANALYSIS_IN_PROGRESS = "ANALYSIS_IN_PROGRESS"
    PRECHECK_FAILED = "PRECHECK_FAILED"
    READY_FOR_OPERATOR_REVIEW = "READY_FOR_OPERATOR_REVIEW"
    OPERATOR_REVIEWED = "OPERATOR_REVIEWED"
    READY_FOR_EXTERNAL_DRY_RUN_HANDOFF = "READY_FOR_EXTERNAL_DRY_RUN_HANDOFF"
    STALE = "STALE"
    INVALID = "INVALID"


class MotionClass(StrEnum):
    RAPID = "RAPID"
    CUTTING_LINEAR = "CUTTING_LINEAR"
    CUTTING_ARC = "CUTTING_ARC"
    NON_MOTION = "NON_MOTION"
    TOOL_CHANGE = "TOOL_CHANGE"
    SPINDLE_CONTROL = "SPINDLE_CONTROL"
    COOLANT_CONTROL = "COOLANT_CONTROL"
    OFFSET_CONTROL = "OFFSET_CONTROL"
    PROGRAM_CONTROL = "PROGRAM_CONTROL"
    UNRESOLVED = "UNRESOLVED"


class OfflineFindingSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKER = "BLOCKER"


class ReleaseState(StrEnum):
    DRAFT = "DRAFT"
    BLOCKED = "BLOCKED"
    READY_FOR_EXTERNAL_DRY_RUN_HANDOFF = "READY_FOR_EXTERNAL_DRY_RUN_HANDOFF"
    STALE = "STALE"
    INVALID = "INVALID"


class OperatorReviewResult(StrEnum):
    ACCEPT_FOR_EXTERNAL_DRY_RUN = "ACCEPT_FOR_EXTERNAL_DRY_RUN"
    REJECT = "REJECT"


class PackageStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    RELEASED_FOR_EXTERNAL_DRY_RUN = "RELEASED_FOR_EXTERNAL_DRY_RUN"
    STALE = "STALE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class ModalSnapshot:
    motion: str | None = None
    units: str | None = None
    positioning: str | None = None
    plane: str | None = None
    tool: int | None = None
    spindle_on: bool = False
    spindle_rpm: float | None = None
    coolant_on: bool = False
    work_offset: str | None = None
    h_offset: int | None = None
    d_offset: int | None = None
    feed: float | None = None
    compensation: str | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModalSnapshot":
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise CamValidationError("Modal snapshot payload is malformed")
        return cls(**data)


@dataclass(frozen=True, slots=True)
class StaticSafetyFinding:
    finding_id: str
    code: str
    severity: OfflineFindingSeverity
    message: str
    block_line: int | None
    source_validator: str
    authority: str
    remediation: str
    qualification_impact: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", _text(self.finding_id, "Finding ID"))
        if not isinstance(self.code, str) or _CODE.fullmatch(self.code) is None:
            raise CamValidationError("Finding code is invalid")
        if not isinstance(self.severity, OfflineFindingSeverity):
            raise CamValidationError("Finding severity is invalid")
        if self.block_line is not None and (type(self.block_line) is not int or self.block_line <= 0):
            raise CamValidationError("Finding block reference is invalid")
        for name in ("message", "source_validator", "authority", "remediation", "qualification_impact"):
            object.__setattr__(self, name, _text(getattr(self, name), name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id, "code": self.code,
            "severity": self.severity.value, "message": self.message,
            "block_line": self.block_line, "source_validator": self.source_validator,
            "authority": self.authority, "remediation": self.remediation,
            "qualification_impact": self.qualification_impact,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StaticSafetyFinding":
        fields = {
            "finding_id", "code", "severity", "message", "block_line",
            "source_validator", "authority", "remediation", "qualification_impact",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Static finding payload is malformed")
        try:
            severity = OfflineFindingSeverity(data["severity"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Static finding severity is invalid") from error
        return cls(
            data["finding_id"], data["code"], severity, data["message"],
            data["block_line"], data["source_validator"], data["authority"],
            data["remediation"], data["qualification_impact"],
        )


@dataclass(frozen=True, slots=True)
class NCBlockRecord:
    sequence: int
    original_line_number: int
    original_text: str
    normalized_tokens: tuple[str, ...]
    motion_class: MotionClass
    modal_before: ModalSnapshot
    modal_after: ModalSnapshot
    finding_ids: tuple[str, ...] = ()
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise CamValidationError("NC block sequence is invalid")
        if type(self.original_line_number) is not int or self.original_line_number <= 0:
            raise CamValidationError("NC line number is invalid")
        if not isinstance(self.original_text, str):
            raise CamValidationError("NC original text is invalid")
        if not isinstance(self.normalized_tokens, tuple) or any(
            not isinstance(item, str) or not item for item in self.normalized_tokens
        ):
            raise CamValidationError("NC normalized tokens are invalid")
        if not isinstance(self.motion_class, MotionClass):
            raise CamValidationError("NC motion class is invalid")
        if not isinstance(self.modal_before, ModalSnapshot) or not isinstance(self.modal_after, ModalSnapshot):
            raise CamValidationError("NC modal state is invalid")
        if tuple(sorted(set(self.finding_ids))) != self.finding_ids:
            raise CamInvariantError("NC finding IDs must be unique and ordered")
        object.__setattr__(self, "operation_id", _optional_text(self.operation_id, "Operation ID"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence, "original_line_number": self.original_line_number,
            "original_text": self.original_text,
            "normalized_tokens": list(self.normalized_tokens),
            "motion_class": self.motion_class.value,
            "modal_before": self.modal_before.to_dict(), "modal_after": self.modal_after.to_dict(),
            "finding_ids": list(self.finding_ids), "operation_id": self.operation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NCBlockRecord":
        fields = {
            "sequence", "original_line_number", "original_text", "normalized_tokens",
            "motion_class", "modal_before", "modal_after", "finding_ids", "operation_id",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("NC block payload is malformed")
        try:
            motion_class = MotionClass(data["motion_class"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("NC motion class payload is invalid") from error
        return cls(
            data["sequence"], data["original_line_number"], data["original_text"],
            tuple(data["normalized_tokens"]), motion_class,
            ModalSnapshot.from_dict(data["modal_before"]),
            ModalSnapshot.from_dict(data["modal_after"]), tuple(data["finding_ids"]),
            data["operation_id"],
        )


@dataclass(frozen=True, slots=True)
class OfflineNCVerificationSession:
    session_id: str
    project_fingerprint: ContentFingerprint
    program_fingerprint: ContentFingerprint
    nc_artifact_id: str
    nc_sha256: str
    machine_profile_id: str
    machine_profile_fingerprint: ContentFingerprint
    controller_contract: str
    post_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    g54_identity: str
    tool_set_fingerprint: ContentFingerprint
    qualification_contract_version: int
    state: VerificationSessionState
    blocks: tuple[NCBlockRecord, ...]
    findings: tuple[StaticSafetyFinding, ...]
    finalized_at: str | None = None
    session_fingerprint: ContentFingerprint | None = None
    format_version: int = OFFLINE_VERIFICATION_FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.format_version != OFFLINE_VERIFICATION_FORMAT_VERSION:
            raise CamValidationError("Unsupported offline verification session version")
        object.__setattr__(self, "session_id", _text(self.session_id, "Session ID"))
        for name in (
            "project_fingerprint", "program_fingerprint", "machine_profile_fingerprint",
            "post_fingerprint", "setup_fingerprint", "tool_set_fingerprint",
        ):
            _fingerprint(getattr(self, name), name)
        object.__setattr__(self, "nc_artifact_id", _text(self.nc_artifact_id, "NC artifact ID"))
        object.__setattr__(self, "nc_sha256", _sha256(self.nc_sha256, "NC SHA-256"))
        object.__setattr__(self, "machine_profile_id", _text(self.machine_profile_id, "Machine profile ID"))
        object.__setattr__(self, "controller_contract", _text(self.controller_contract, "Controller contract"))
        if self.g54_identity != "G54":
            raise CamValidationError("Only exact G54 is supported by Tranche3")
        if type(self.qualification_contract_version) is not int or self.qualification_contract_version <= 0:
            raise CamValidationError("Qualification contract version is invalid")
        if not isinstance(self.state, VerificationSessionState):
            raise CamValidationError("Verification session state is invalid")
        if not isinstance(self.blocks, tuple) or any(not isinstance(item, NCBlockRecord) for item in self.blocks):
            raise CamValidationError("Verification blocks are invalid")
        if tuple(item.sequence for item in self.blocks) != tuple(range(len(self.blocks))):
            raise CamInvariantError("Verification block sequence is not contiguous")
        if not isinstance(self.findings, tuple) or any(
            not isinstance(item, StaticSafetyFinding) for item in self.findings
        ):
            raise CamValidationError("Verification findings are invalid")
        if len({item.finding_id for item in self.findings}) != len(self.findings):
            raise CamInvariantError("Verification finding IDs must be unique")
        object.__setattr__(self, "finalized_at", _timestamp(self.finalized_at, "Finalization timestamp"))
        if self.state in {VerificationSessionState.DRAFT, VerificationSessionState.ANALYSIS_IN_PROGRESS}:
            if self.finalized_at is not None:
                raise CamInvariantError("An unfinished verification session cannot be finalized")
        elif self.finalized_at is None:
            raise CamInvariantError("A terminal analysis state requires finalization time")
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.session_fingerprint is None:
            object.__setattr__(self, "session_fingerprint", calculated)
        elif self.session_fingerprint != calculated:
            raise CamInvariantError("Verification session fingerprint mismatch")

    @property
    def blocker_count(self) -> int:
        return sum(item.severity is OfflineFindingSeverity.BLOCKER for item in self.findings)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": "HMS_STAGE18A_OFFLINE_NC_VERIFICATION_SESSION",
            "format_version": self.format_version, "session_id": self.session_id,
            "project_fingerprint": self.project_fingerprint.to_dict(),
            "program_fingerprint": self.program_fingerprint.to_dict(),
            "nc_artifact_id": self.nc_artifact_id, "nc_sha256": self.nc_sha256,
            "machine_profile_id": self.machine_profile_id,
            "machine_profile_fingerprint": self.machine_profile_fingerprint.to_dict(),
            "controller_contract": self.controller_contract,
            "post_fingerprint": self.post_fingerprint.to_dict(),
            "setup_fingerprint": self.setup_fingerprint.to_dict(), "g54_identity": self.g54_identity,
            "tool_set_fingerprint": self.tool_set_fingerprint.to_dict(),
            "qualification_contract_version": self.qualification_contract_version,
            "state": self.state.value, "blocks": [item.to_dict() for item in self.blocks],
            "findings": [item.to_dict() for item in self.findings],
            "finalized_at": self.finalized_at,
            "no_cnc_control": NO_CNC_CONTROL_MARKER,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "session_fingerprint": self.session_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OfflineNCVerificationSession":
        fields = {
            "format", "format_version", "session_id", "project_fingerprint",
            "program_fingerprint", "nc_artifact_id", "nc_sha256", "machine_profile_id",
            "machine_profile_fingerprint", "controller_contract", "post_fingerprint",
            "setup_fingerprint", "g54_identity", "tool_set_fingerprint",
            "qualification_contract_version", "state", "blocks", "findings",
            "finalized_at", "no_cnc_control", "session_fingerprint",
        }
        if (
            not isinstance(data, dict) or set(data) != fields
            or data["format"] != "HMS_STAGE18A_OFFLINE_NC_VERIFICATION_SESSION"
            or data["no_cnc_control"] != NO_CNC_CONTROL_MARKER
            or not isinstance(data["blocks"], list) or not isinstance(data["findings"], list)
        ):
            raise CamValidationError("Offline verification session payload is malformed")
        try:
            state = VerificationSessionState(data["state"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Offline verification session state is invalid") from error
        return cls(
            data["session_id"], ContentFingerprint.from_dict(data["project_fingerprint"]),
            ContentFingerprint.from_dict(data["program_fingerprint"]), data["nc_artifact_id"],
            data["nc_sha256"], data["machine_profile_id"],
            ContentFingerprint.from_dict(data["machine_profile_fingerprint"]),
            data["controller_contract"], ContentFingerprint.from_dict(data["post_fingerprint"]),
            ContentFingerprint.from_dict(data["setup_fingerprint"]), data["g54_identity"],
            ContentFingerprint.from_dict(data["tool_set_fingerprint"]),
            data["qualification_contract_version"], state,
            tuple(NCBlockRecord.from_dict(item) for item in data["blocks"]),
            tuple(StaticSafetyFinding.from_dict(item) for item in data["findings"]),
            data["finalized_at"], ContentFingerprint.from_dict(data["session_fingerprint"]),
            data["format_version"],
        )

    def stale(self, reason: str, at: str) -> "OfflineNCVerificationSession":
        """Return a new identity explicitly marking source drift."""

        finding = StaticSafetyFinding(
            f"SESSION_STALE:{len(self.findings) + 1}", "SOURCE_DRIFT",
            OfflineFindingSeverity.BLOCKER, _text(reason, "Stale reason"), None,
            "OfflineNCVerificationSession.stale", "repository/current source",
            "Create a new verification session from current sources.", "HANDOFF_BLOCKED",
        )
        return replace(
            self, state=VerificationSessionState.STALE,
            findings=(*self.findings, finding), finalized_at=_timestamp(at, "Stale timestamp"),
            session_fingerprint=None,
        )


@dataclass(frozen=True, slots=True)
class NCReleaseCandidate:
    release_revision: int
    nc_sha256: str
    verification_session_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    tool_set_fingerprint: ContentFingerprint
    machine_profile_fingerprint: ContentFingerprint
    post_fingerprint: ContentFingerprint
    qualification_report_fingerprint: ContentFingerprint
    qualification_level: str
    created_at: str
    candidate_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        if type(self.release_revision) is not int or self.release_revision <= 0:
            raise CamValidationError("Release revision is invalid")
        object.__setattr__(self, "nc_sha256", _sha256(self.nc_sha256, "Release NC SHA-256"))
        for name in (
            "verification_session_fingerprint", "setup_fingerprint", "tool_set_fingerprint",
            "machine_profile_fingerprint", "post_fingerprint", "qualification_report_fingerprint",
        ):
            _fingerprint(getattr(self, name), name)
        object.__setattr__(self, "qualification_level", _text(self.qualification_level, "Qualification level"))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "Release timestamp"))
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.candidate_fingerprint is None:
            object.__setattr__(self, "candidate_fingerprint", calculated)
        elif self.candidate_fingerprint != calculated:
            raise CamInvariantError("Release candidate fingerprint mismatch")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": "HMS_STAGE18A_NC_RELEASE_CANDIDATE", "format_version": 1,
            "release_revision": self.release_revision, "nc_sha256": self.nc_sha256,
            "verification_session_fingerprint": self.verification_session_fingerprint.to_dict(),
            "setup_fingerprint": self.setup_fingerprint.to_dict(),
            "tool_set_fingerprint": self.tool_set_fingerprint.to_dict(),
            "machine_profile_fingerprint": self.machine_profile_fingerprint.to_dict(),
            "post_fingerprint": self.post_fingerprint.to_dict(),
            "qualification_report_fingerprint": self.qualification_report_fingerprint.to_dict(),
            "qualification_level": self.qualification_level, "created_at": self.created_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "candidate_fingerprint": self.candidate_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NCReleaseCandidate":
        fields = {
            "format", "format_version", "release_revision", "nc_sha256",
            "verification_session_fingerprint", "setup_fingerprint", "tool_set_fingerprint",
            "machine_profile_fingerprint", "post_fingerprint",
            "qualification_report_fingerprint", "qualification_level", "created_at",
            "candidate_fingerprint",
        }
        if (
            not isinstance(data, dict) or set(data) != fields
            or data["format"] != "HMS_STAGE18A_NC_RELEASE_CANDIDATE"
            or data["format_version"] != 1
        ):
            raise CamValidationError("Release candidate payload is malformed")
        return cls(
            data["release_revision"], data["nc_sha256"],
            ContentFingerprint.from_dict(data["verification_session_fingerprint"]),
            ContentFingerprint.from_dict(data["setup_fingerprint"]),
            ContentFingerprint.from_dict(data["tool_set_fingerprint"]),
            ContentFingerprint.from_dict(data["machine_profile_fingerprint"]),
            ContentFingerprint.from_dict(data["post_fingerprint"]),
            ContentFingerprint.from_dict(data["qualification_report_fingerprint"]),
            data["qualification_level"], data["created_at"],
            ContentFingerprint.from_dict(data["candidate_fingerprint"]),
        )


@dataclass(frozen=True, slots=True)
class OperatorReview:
    reviewer_identity: str
    role: str
    reviewed_at: str
    release_candidate_fingerprint: ContentFingerprint
    acknowledged_finding_ids: tuple[str, ...]
    result: OperatorReviewResult
    notes: str

    def __post_init__(self) -> None:
        for name in ("reviewer_identity", "role", "notes"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "reviewed_at", _timestamp(self.reviewed_at, "Review timestamp"))
        _fingerprint(self.release_candidate_fingerprint, "Release candidate fingerprint")
        if tuple(sorted(set(self.acknowledged_finding_ids))) != self.acknowledged_finding_ids:
            raise CamInvariantError("Acknowledged findings must be unique and ordered")
        if not isinstance(self.result, OperatorReviewResult):
            raise CamValidationError("Operator review result is invalid")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer_identity": self.reviewer_identity, "role": self.role,
            "reviewed_at": self.reviewed_at,
            "release_candidate_fingerprint": self.release_candidate_fingerprint.to_dict(),
            "acknowledged_finding_ids": list(self.acknowledged_finding_ids),
            "result": self.result.value, "notes": self.notes,
            "digital_signature": None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperatorReview":
        fields = {
            "reviewer_identity", "role", "reviewed_at", "release_candidate_fingerprint",
            "acknowledged_finding_ids", "result", "notes", "digital_signature",
        }
        if (
            not isinstance(data, dict) or set(data) != fields or data["digital_signature"] is not None
            or not isinstance(data["acknowledged_finding_ids"], list)
        ):
            raise CamValidationError("Operator review payload is malformed")
        try:
            result = OperatorReviewResult(data["result"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Operator review result is invalid") from error
        return cls(
            data["reviewer_identity"], data["role"], data["reviewed_at"],
            ContentFingerprint.from_dict(data["release_candidate_fingerprint"]),
            tuple(data["acknowledged_finding_ids"]), result, data["notes"],
        )


@dataclass(frozen=True, slots=True)
class OperatorAcknowledgement:
    release_candidate_fingerprint: ContentFingerprint
    acknowledged_by: str
    acknowledged_at: str
    software_only_statement: str
    machine_ready_statement: str

    REQUIRED_SOFTWARE_STATEMENT = (
        "Chương trình này mới đạt kiểm tra phần mềm và chưa được nghiệm thu trên máy."
    )
    REQUIRED_MACHINE_READY_STATEMENT = (
        "Việc xuất gói chạy thử không đồng nghĩa MACHINE READY."
    )

    def __post_init__(self) -> None:
        _fingerprint(self.release_candidate_fingerprint, "Acknowledgement release fingerprint")
        object.__setattr__(self, "acknowledged_by", _text(self.acknowledged_by, "Acknowledged by"))
        object.__setattr__(self, "acknowledged_at", _timestamp(self.acknowledged_at, "Acknowledgement timestamp"))
        if self.software_only_statement != self.REQUIRED_SOFTWARE_STATEMENT:
            raise CamInvariantError("Software-only acknowledgement text is not exact")
        if self.machine_ready_statement != self.REQUIRED_MACHINE_READY_STATEMENT:
            raise CamInvariantError("MACHINE READY acknowledgement text is not exact")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_candidate_fingerprint": self.release_candidate_fingerprint.to_dict(),
            "acknowledged_by": self.acknowledged_by, "acknowledged_at": self.acknowledged_at,
            "software_only_statement": self.software_only_statement,
            "machine_ready_statement": self.machine_ready_statement,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperatorAcknowledgement":
        fields = {
            "release_candidate_fingerprint", "acknowledged_by", "acknowledged_at",
            "software_only_statement", "machine_ready_statement",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Operator acknowledgement payload is malformed")
        return cls(
            ContentFingerprint.from_dict(data["release_candidate_fingerprint"]),
            data["acknowledged_by"], data["acknowledged_at"],
            data["software_only_statement"], data["machine_ready_statement"],
        )


@dataclass(frozen=True, slots=True)
class ReleaseAssessment:
    state: ReleaseState
    blocker_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    level2_achieved: bool = False
    level3_achieved: bool = False
    machine_ready: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.state, ReleaseState):
            raise CamValidationError("Release assessment state is invalid")
        if self.level2_achieved or self.level3_achieved or self.machine_ready:
            raise CamInvariantError("Tranche3 cannot promote Level2, Level3, or MACHINE READY")
        if self.state is ReleaseState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF and self.blocker_codes:
            raise CamInvariantError("Ready handoff assessment cannot contain blockers")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value, "blocker_codes": list(self.blocker_codes),
            "warning_codes": list(self.warning_codes), "level2_achieved": False,
            "level3_achieved": False, "machine_ready": False,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReleaseAssessment":
        fields = {
            "state", "blocker_codes", "warning_codes", "level2_achieved",
            "level3_achieved", "machine_ready",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Release assessment payload is malformed")
        try:
            state = ReleaseState(data["state"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Release assessment state is invalid") from error
        return cls(
            state, tuple(data["blocker_codes"]), tuple(data["warning_codes"]),
            data["level2_achieved"], data["level3_achieved"], data["machine_ready"],
        )


@dataclass(frozen=True, slots=True)
class ReleaseComparison:
    nc_sha_changed: bool
    machine_profile_changed: bool
    setup_changed: bool
    tools_changed: bool
    post_changed: bool
    block_count_changed: bool
    motion_blocks_changed: bool
    spindle_feed_changed: bool
    tool_change_sequence_changed: bool
    qualification_findings_changed: bool

    def to_dict(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


__all__ = [
    "MotionClass", "NCBlockRecord", "NCReleaseCandidate", "NO_CNC_CONTROL_MARKER",
    "TRANCHE3_COUNTERFACTUAL_MARKER", "TRANCHE3_SCOPE_MARKER",
    "OfflineFindingSeverity", "OfflineNCVerificationSession", "OperatorAcknowledgement",
    "OperatorReview", "OperatorReviewResult", "PackageStatus", "ReleaseAssessment",
    "ReleaseComparison", "ReleaseState", "StaticSafetyFinding", "VerificationSessionState",
    "ModalSnapshot",
]
