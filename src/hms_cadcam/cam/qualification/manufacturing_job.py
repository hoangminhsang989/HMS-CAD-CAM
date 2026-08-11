"""Typed offline manufacturing-job release governance for Stage18A Tranche4.

The aggregate deliberately stops at controlled external dry-run handoff.  It
does not represent controller connectivity, physical acceptance, Level2,
Level3, or MACHINE READY.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Iterable

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint

TRANCHE4_FORMAT_VERSION = 1
TRANCHE4_SCOPE_MARKER = "STAGE18A_TRANCHE4_SCOPE_FROZEN"
TRANCHE4_NO_CNC_MARKER = "STAGE18A_TRANCHE4_NO_CNC_CONTROL_BOUNDARY_PRESERVED"
TRANCHE4_COUNTERFACTUAL_MARKER = "STAGE18A_TRANCHE4_PRODUCT_DELTA_COUNTERFACTUALLY_PROVEN"
TRANCHE4_IMPLEMENTATION_MARKERS = (
    "STAGE18A_MANUFACTURING_JOB_MODEL_IMPLEMENTED",
    "STAGE18A_JOB_TOOL_RECONCILIATION_IMPLEMENTED",
    "STAGE18A_JOB_SETUP_CONSISTENCY_IMPLEMENTED",
    "STAGE18A_JOB_RELEASE_POLICY_IMPLEMENTED",
    "STAGE18A_IMMUTABLE_JOB_RELEASE_IMPLEMENTED",
    "STAGE18A_RELEASE_SUPERSEDE_HISTORY_IMPLEMENTED",
    "STAGE18A_JOB_LEVEL_STRUCTURED_DIFF_IMPLEMENTED",
    "STAGE18A_MANUFACTURING_JOB_HANDOFF_PACKAGE_IMPLEMENTED",
)
TRANCHE4_ADVERSARIAL_CASES = (
    "mixed machine profiles", "stale NC release", "changed NC same filename", "changed setup",
    "changed Tool", "conflicting Tool number", "changed Holder", "changed Post", "changed part revision",
    "stale operator review", "missing program", "package file deleted", "unexpected package file",
    "manifest mutation", "superseded release reused", "manual release-state edit", "manual Level2 flag",
    "machine_ready injection", "Tapping constituent program", "unqualified canned cycle", "detached Level2 evidence",
    "release review bound to old job fingerprint", "comment-only NC byte revision", "duplicate job release revision",
)
PACKAGE_FORMAT = "HMS_STAGE18A_MANUFACTURING_JOB_HANDOFF"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _text(value: str, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CamValidationError(f"{name} is invalid")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise CamValidationError(f"{name} contains control characters")
    return value.strip()


def _sha(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CamValidationError(f"{name} is invalid")
    return value


def _fp(value: ContentFingerprint, name: str) -> ContentFingerprint:
    if not isinstance(value, ContentFingerprint):
        raise CamValidationError(f"{name} is invalid")
    return value


def _sorted_unique(values: Iterable[str], name: str) -> tuple[str, ...]:
    result = tuple(sorted({_text(value, name) for value in values}))
    return result


class ManufacturingJobState(StrEnum):
    DRAFT = "DRAFT"
    IN_VERIFICATION = "IN_VERIFICATION"
    BLOCKED = "BLOCKED"
    READY_FOR_RELEASE_REVIEW = "READY_FOR_RELEASE_REVIEW"
    RELEASE_APPROVED = "RELEASE_APPROVED"
    RELEASED_FOR_EXTERNAL_DRY_RUN = "RELEASED_FOR_EXTERNAL_DRY_RUN"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    INVALID = "INVALID"


class JobQualificationState(StrEnum):
    CURRENT = "CURRENT"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class ReleaseDecision(StrEnum):
    APPROVE_FOR_EXTERNAL_DRY_RUN_HANDOFF = "APPROVE_FOR_EXTERNAL_DRY_RUN_HANDOFF"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class JobToolBinding:
    tool_number: int
    fingerprint: ContentFingerprint
    identity: str
    tool_type: str
    diameter_mm: float | None
    length_mm: float | None
    holder: str | None
    h_offset: int | None
    d_offset: int | None
    program_ids: tuple[str, ...]
    setup_ids: tuple[str, ...]
    state: JobQualificationState = JobQualificationState.CURRENT

    def __post_init__(self) -> None:
        if type(self.tool_number) is not int or self.tool_number <= 0:
            raise CamValidationError("Tool number is invalid")
        _fp(self.fingerprint, "Tool fingerprint")
        object.__setattr__(self, "identity", _text(self.identity, "Tool identity"))
        object.__setattr__(self, "tool_type", _text(self.tool_type, "Tool type"))
        for name in ("diameter_mm", "length_mm"):
            value = getattr(self, name)
            if value is not None and (type(value) not in (int, float) or value <= 0):
                raise CamValidationError(f"{name} is invalid")
        if self.h_offset is not None and (type(self.h_offset) is not int or self.h_offset < 0):
            raise CamValidationError("H offset is invalid")
        if self.d_offset is not None and (type(self.d_offset) is not int or self.d_offset < 0):
            raise CamValidationError("D offset is invalid")
        for name in ("holder",):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))
        object.__setattr__(self, "program_ids", _sorted_unique(self.program_ids, "Program ID"))
        object.__setattr__(self, "setup_ids", _sorted_unique(self.setup_ids, "Setup ID"))
        if not isinstance(self.state, JobQualificationState):
            raise CamValidationError("Tool qualification state is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {"tool_number": self.tool_number, "fingerprint": self.fingerprint.to_dict(),
                "identity": self.identity, "tool_type": self.tool_type,
                "diameter_mm": self.diameter_mm, "length_mm": self.length_mm,
                "holder": self.holder, "h_offset": self.h_offset, "d_offset": self.d_offset,
                "program_ids": list(self.program_ids), "setup_ids": list(self.setup_ids),
                "state": self.state.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobToolBinding":
        return cls(data["tool_number"], ContentFingerprint.from_dict(data["fingerprint"]),
                   data["identity"], data["tool_type"], data["diameter_mm"], data["length_mm"],
                   data["holder"], data["h_offset"], data["d_offset"], tuple(data["program_ids"]),
                   tuple(data["setup_ids"]), JobQualificationState(data.get("state", "CURRENT")))


@dataclass(frozen=True, slots=True)
class JobSetupBinding:
    setup_id: str
    fingerprint: ContentFingerprint
    g54_identity: str
    stock_fingerprint: ContentFingerprint | None
    fixture_fingerprint: ContentFingerprint | None
    machine_profile_id: str
    tool_numbers: tuple[int, ...]
    program_ids: tuple[str, ...]
    qualification_state: JobQualificationState
    physical_unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "setup_id", _text(self.setup_id, "Setup ID"))
        _fp(self.fingerprint, "Setup fingerprint")
        if self.g54_identity != "G54":
            raise CamValidationError("Only exact G54 is supported by the current contract")
        for name in ("stock_fingerprint", "fixture_fingerprint"):
            value = getattr(self, name)
            if value is not None:
                _fp(value, name)
        object.__setattr__(self, "machine_profile_id", _text(self.machine_profile_id, "Machine profile ID"))
        if not isinstance(self.tool_numbers, tuple) or any(type(v) is not int or v <= 0 for v in self.tool_numbers):
            raise CamValidationError("Setup Tool numbers are invalid")
        if tuple(sorted(set(self.tool_numbers))) != self.tool_numbers:
            raise CamInvariantError("Setup Tool numbers must be ordered and unique")
        object.__setattr__(self, "program_ids", _sorted_unique(self.program_ids, "Program ID"))
        object.__setattr__(self, "physical_unknowns", _sorted_unique(self.physical_unknowns, "Physical unknown"))
        if not isinstance(self.qualification_state, JobQualificationState):
            raise CamValidationError("Setup qualification state is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {"setup_id": self.setup_id, "fingerprint": self.fingerprint.to_dict(),
                "g54_identity": self.g54_identity,
                "stock_fingerprint": self.stock_fingerprint.to_dict() if self.stock_fingerprint else None,
                "fixture_fingerprint": self.fixture_fingerprint.to_dict() if self.fixture_fingerprint else None,
                "machine_profile_id": self.machine_profile_id, "tool_numbers": list(self.tool_numbers),
                "program_ids": list(self.program_ids), "qualification_state": self.qualification_state.value,
                "physical_unknowns": list(self.physical_unknowns)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobSetupBinding":
        return cls(data["setup_id"], ContentFingerprint.from_dict(data["fingerprint"]), data["g54_identity"],
                   ContentFingerprint.from_dict(data["stock_fingerprint"]) if data.get("stock_fingerprint") else None,
                   ContentFingerprint.from_dict(data["fixture_fingerprint"]) if data.get("fixture_fingerprint") else None,
                   data["machine_profile_id"], tuple(data["tool_numbers"]), tuple(data["program_ids"]),
                   JobQualificationState(data["qualification_state"]), tuple(data.get("physical_unknowns", ())))


@dataclass(frozen=True, slots=True)
class JobProgramBinding:
    program_id: str
    nc_release_fingerprint: ContentFingerprint
    nc_sha256: str
    setup_id: str
    g54_identity: str
    machine_profile_id: str
    machine_profile_fingerprint: ContentFingerprint
    controller_contract: str
    post_fingerprint: ContentFingerprint
    tool_numbers: tuple[int, ...]
    qualification_state: JobQualificationState
    release_revision: int
    qualification_blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "program_id", _text(self.program_id, "Program ID"))
        _fp(self.nc_release_fingerprint, "NC release fingerprint")
        object.__setattr__(self, "nc_sha256", _sha(self.nc_sha256, "NC SHA-256"))
        object.__setattr__(self, "setup_id", _text(self.setup_id, "Setup ID"))
        if self.g54_identity != "G54":
            raise CamValidationError("Program G54 identity is invalid")
        object.__setattr__(self, "machine_profile_id", _text(self.machine_profile_id, "Machine profile ID"))
        _fp(self.machine_profile_fingerprint, "Machine profile fingerprint")
        object.__setattr__(self, "controller_contract", _text(self.controller_contract, "Controller contract"))
        _fp(self.post_fingerprint, "Post fingerprint")
        if tuple(sorted(set(self.tool_numbers))) != self.tool_numbers or any(type(v) is not int or v <= 0 for v in self.tool_numbers):
            raise CamValidationError("Program Tool numbers are invalid")
        if not isinstance(self.qualification_state, JobQualificationState):
            raise CamValidationError("Program qualification state is invalid")
        if type(self.release_revision) is not int or self.release_revision <= 0:
            raise CamValidationError("Program release revision is invalid")
        object.__setattr__(self, "qualification_blockers", _sorted_unique(self.qualification_blockers, "Qualification blocker"))

    def to_dict(self) -> dict[str, Any]:
        return {"program_id": self.program_id, "nc_release_fingerprint": self.nc_release_fingerprint.to_dict(),
                "nc_sha256": self.nc_sha256, "setup_id": self.setup_id, "g54_identity": self.g54_identity,
                "machine_profile_id": self.machine_profile_id,
                "machine_profile_fingerprint": self.machine_profile_fingerprint.to_dict(),
                "controller_contract": self.controller_contract,
                "post_fingerprint": self.post_fingerprint.to_dict(), "tool_numbers": list(self.tool_numbers),
                "qualification_state": self.qualification_state.value, "release_revision": self.release_revision,
                "qualification_blockers": list(self.qualification_blockers)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobProgramBinding":
        return cls(data["program_id"], ContentFingerprint.from_dict(data["nc_release_fingerprint"]), data["nc_sha256"],
                   data["setup_id"], data["g54_identity"], data["machine_profile_id"],
                   ContentFingerprint.from_dict(data["machine_profile_fingerprint"]),
                   data["controller_contract"], ContentFingerprint.from_dict(data["post_fingerprint"]),
                   tuple(data["tool_numbers"]), JobQualificationState(data["qualification_state"]),
                   data["release_revision"], tuple(data.get("qualification_blockers", ())))


@dataclass(frozen=True, slots=True)
class JobReleasePolicy:
    require_current_programs: bool = True
    require_zero_blockers: bool = True
    require_setup_readiness: bool = True
    require_tool_reconciliation: bool = True
    require_operator_review: bool = True
    require_handoff_package: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {name: bool(getattr(self, name)) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobReleasePolicy":
        return cls(**{name: bool(data.get(name, True)) for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class ManufacturingJob:
    project_id: str
    job_id: str
    part_id: str
    part_revision: str
    project_fingerprint: ContentFingerprint
    machine_profile_id: str
    machine_profile_fingerprint: ContentFingerprint
    controller_contract: str
    programs: tuple[JobProgramBinding, ...]
    setups: tuple[JobSetupBinding, ...]
    tools: tuple[JobToolBinding, ...]
    release_policy: JobReleasePolicy = JobReleasePolicy()
    state: ManufacturingJobState = ManufacturingJobState.DRAFT
    provenance: tuple[tuple[str, str], ...] = ()
    job_fingerprint: ContentFingerprint | None = None
    format_version: int = TRANCHE4_FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.format_version != TRANCHE4_FORMAT_VERSION:
            raise CamValidationError("Unsupported manufacturing job format version")
        for name in ("project_id", "job_id", "part_id", "part_revision", "machine_profile_id", "controller_contract"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("project_fingerprint", "machine_profile_fingerprint"):
            _fp(getattr(self, name), name)
        if not self.programs or any(not isinstance(v, JobProgramBinding) for v in self.programs):
            raise CamValidationError("Manufacturing job requires typed programs")
        if len({v.program_id for v in self.programs}) != len(self.programs):
            raise CamInvariantError("Program IDs must be unique")
        if not self.setups or any(not isinstance(v, JobSetupBinding) for v in self.setups):
            raise CamValidationError("Manufacturing job requires typed setups")
        if len({v.setup_id for v in self.setups}) != len(self.setups):
            raise CamInvariantError("Setup IDs must be unique")
        if any(v.machine_profile_id != self.machine_profile_id for v in self.programs + self.setups):
            raise CamInvariantError("Manufacturing job cannot mix machine profiles")
        if any(v.controller_contract != self.controller_contract for v in self.programs):
            raise CamInvariantError("Manufacturing job cannot mix controller contracts")
        if len({v.nc_release_fingerprint for v in self.programs}) != len(self.programs):
            raise CamInvariantError("Program NC release identities must be unique")
        if any(not isinstance(v, JobToolBinding) for v in self.tools):
            raise CamValidationError("Manufacturing job tools are invalid")
        if len({v.tool_number for v in self.tools}) != len(self.tools):
            raise CamInvariantError("Tool numbers must be unique in canonical job state")
        if not isinstance(self.release_policy, JobReleasePolicy) or not isinstance(self.state, ManufacturingJobState):
            raise CamValidationError("Manufacturing job policy/state is invalid")
        normalized = tuple(sorted(((_text(k, "Provenance key"), _text(v, "Provenance value")) for k, v in self.provenance)))
        if len({k for k, _ in normalized}) != len(normalized):
            raise CamInvariantError("Job provenance keys must be unique")
        object.__setattr__(self, "provenance", normalized)
        calculated = ContentFingerprint.from_payload(self.fingerprint_payload())
        if self.job_fingerprint is None:
            object.__setattr__(self, "job_fingerprint", calculated)
        elif self.job_fingerprint != calculated:
            raise CamInvariantError("Manufacturing job fingerprint mismatch")

    def identity_payload(self) -> dict[str, Any]:
        return {"format": "HMS_STAGE18A_MANUFACTURING_JOB", "format_version": self.format_version,
                "project_id": self.project_id, "job_id": self.job_id, "part_id": self.part_id,
                "part_revision": self.part_revision, "project_fingerprint": self.project_fingerprint.to_dict(),
                "machine_profile_id": self.machine_profile_id,
                "machine_profile_fingerprint": self.machine_profile_fingerprint.to_dict(),
                "controller_contract": self.controller_contract, "programs": [v.to_dict() for v in self.programs],
                "setups": [v.to_dict() for v in self.setups], "tools": [v.to_dict() for v in self.tools],
                "release_policy": self.release_policy.to_dict(), "state": self.state.value,
                "provenance": [list(item) for item in self.provenance], "no_cnc_control": TRANCHE4_NO_CNC_MARKER}

    def fingerprint_payload(self) -> dict[str, Any]:
        """Return source identity; lifecycle state is intentionally excluded."""
        payload = self.identity_payload()
        payload.pop("state", None)
        return payload

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "job_fingerprint": self.job_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ManufacturingJob":
        if not isinstance(data, dict) or data.get("format") != "HMS_STAGE18A_MANUFACTURING_JOB":
            raise CamValidationError("Manufacturing job payload is malformed")
        if data.get("no_cnc_control") != TRANCHE4_NO_CNC_MARKER:
            raise CamValidationError("Manufacturing job CNC boundary marker is missing")
        return cls(data["project_id"], data["job_id"], data["part_id"], data["part_revision"],
                   ContentFingerprint.from_dict(data["project_fingerprint"]), data["machine_profile_id"],
                   ContentFingerprint.from_dict(data["machine_profile_fingerprint"]), data["controller_contract"],
                   tuple(JobProgramBinding.from_dict(v) for v in data["programs"]),
                   tuple(JobSetupBinding.from_dict(v) for v in data["setups"]),
                   tuple(JobToolBinding.from_dict(v) for v in data["tools"]),
                   JobReleasePolicy.from_dict(data["release_policy"]), ManufacturingJobState(data["state"]),
                   tuple(tuple(v) for v in data.get("provenance", ())),
                   ContentFingerprint.from_dict(data["job_fingerprint"]), data["format_version"])

    def with_state(self, state: ManufacturingJobState) -> "ManufacturingJob":
        return replace(self, state=state, job_fingerprint=None)


@dataclass(frozen=True, slots=True)
class ToolReconciliationIssue:
    code: str
    tool_number: int | None
    message: str
    severity: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "tool_number": self.tool_number, "message": self.message, "severity": self.severity}


@dataclass(frozen=True, slots=True)
class JobToolReconciliationReport:
    job_fingerprint: ContentFingerprint
    issues: tuple[ToolReconciliationIssue, ...]
    tool_count: int
    report_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        _fp(self.job_fingerprint, "Tool report job fingerprint")
        object.__setattr__(self, "issues", tuple(sorted(self.issues, key=lambda i: (i.tool_number or 0, i.code))))
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.report_fingerprint is None:
            object.__setattr__(self, "report_fingerprint", calculated)
        elif self.report_fingerprint != calculated:
            raise CamInvariantError("Tool reconciliation fingerprint mismatch")

    @property
    def passed(self) -> bool:
        return not self.issues

    def identity_payload(self) -> dict[str, Any]:
        return {"format": "HMS_STAGE18A_JOB_TOOL_RECONCILIATION", "job_fingerprint": self.job_fingerprint.to_dict(),
                "issues": [v.to_dict() for v in self.issues], "tool_count": self.tool_count}

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "report_fingerprint": self.report_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobToolReconciliationReport":
        issues = tuple(ToolReconciliationIssue(item["code"], item.get("tool_number"), item["message"], item["severity"])
                       for item in data["issues"])
        return cls(ContentFingerprint.from_dict(data["job_fingerprint"]), issues, data["tool_count"],
                   ContentFingerprint.from_dict(data["report_fingerprint"]))


@dataclass(frozen=True, slots=True)
class JobReleaseReview:
    reviewer: str
    role: str
    reviewed_at: str
    job_fingerprint: ContentFingerprint
    program_release_fingerprints: tuple[ContentFingerprint, ...]
    acknowledged_findings: tuple[str, ...]
    decision: ReleaseDecision
    notes: str
    review_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reviewer", _text(self.reviewer, "Reviewer"))
        object.__setattr__(self, "role", _text(self.role, "Reviewer role"))
        object.__setattr__(self, "reviewed_at", _text(self.reviewed_at, "Review timestamp", maximum=64))
        _fp(self.job_fingerprint, "Review job fingerprint")
        if not self.program_release_fingerprints or any(not isinstance(v, ContentFingerprint) for v in self.program_release_fingerprints):
            raise CamValidationError("Review program release fingerprints are invalid")
        object.__setattr__(self, "acknowledged_findings", _sorted_unique(self.acknowledged_findings, "Finding"))
        if not isinstance(self.decision, ReleaseDecision):
            raise CamValidationError("Release decision is invalid")
        object.__setattr__(self, "notes", _text(self.notes, "Review notes"))
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.review_fingerprint is None:
            object.__setattr__(self, "review_fingerprint", calculated)
        elif self.review_fingerprint != calculated:
            raise CamInvariantError("Review fingerprint mismatch")

    def identity_payload(self) -> dict[str, Any]:
        return {"format": "HMS_STAGE18A_JOB_RELEASE_REVIEW", "reviewer": self.reviewer, "role": self.role,
                "reviewed_at": self.reviewed_at, "job_fingerprint": self.job_fingerprint.to_dict(),
                "program_release_fingerprints": [v.to_dict() for v in self.program_release_fingerprints],
                "acknowledged_findings": list(self.acknowledged_findings), "decision": self.decision.value,
                "notes": self.notes}

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "review_fingerprint": self.review_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobReleaseReview":
        return cls(data["reviewer"], data["role"], data["reviewed_at"],
                   ContentFingerprint.from_dict(data["job_fingerprint"]),
                   tuple(ContentFingerprint.from_dict(v) for v in data["program_release_fingerprints"]),
                   tuple(data["acknowledged_findings"]), ReleaseDecision(data["decision"]), data["notes"],
                   ContentFingerprint.from_dict(data["review_fingerprint"]))


@dataclass(frozen=True, slots=True)
class ManufacturingJobRelease:
    release_id: str
    job: ManufacturingJob
    tool_report: JobToolReconciliationReport
    review: JobReleaseReview
    released_at: str
    package_fingerprint: ContentFingerprint
    state: ManufacturingJobState = ManufacturingJobState.RELEASED_FOR_EXTERNAL_DRY_RUN
    supersedes_release_id: str | None = None
    superseded_by_release_id: str | None = None
    superseded_reason: str | None = None
    superseded_at: str | None = None
    release_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "release_id", _text(self.release_id, "Release ID"))
        if not isinstance(self.job, ManufacturingJob) or not isinstance(self.tool_report, JobToolReconciliationReport):
            raise CamValidationError("Release aggregate is invalid")
        if self.review.job_fingerprint != self.job.job_fingerprint:
            raise CamInvariantError("Review is bound to a different job fingerprint")
        _fp(self.package_fingerprint, "Package fingerprint")
        object.__setattr__(self, "released_at", _text(self.released_at, "Release timestamp", maximum=64))
        if self.state not in {ManufacturingJobState.RELEASED_FOR_EXTERNAL_DRY_RUN, ManufacturingJobState.SUPERSEDED}:
            raise CamValidationError("Release state is invalid")
        if self.state is ManufacturingJobState.SUPERSEDED:
            if self.superseded_by_release_id is None or self.superseded_reason is None or self.superseded_at is None:
                raise CamInvariantError("Superseded release requires replacement, reason, and timestamp")
        for name in ("supersedes_release_id", "superseded_by_release_id", "superseded_reason", "superseded_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name, maximum=4096 if name.endswith("reason") else 64))
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.release_fingerprint is None:
            object.__setattr__(self, "release_fingerprint", calculated)
        elif self.release_fingerprint != calculated:
            raise CamInvariantError("Manufacturing release fingerprint mismatch")

    def identity_payload(self) -> dict[str, Any]:
        return {"format": "HMS_STAGE18A_MANUFACTURING_JOB_RELEASE", "release_id": self.release_id,
                "job": self.job.to_dict(), "tool_report": self.tool_report.to_dict(),
                "review": self.review.to_dict(), "released_at": self.released_at,
                "package_fingerprint": self.package_fingerprint.to_dict(), "state": self.state.value,
                "supersedes_release_id": self.supersedes_release_id,
                "superseded_by_release_id": self.superseded_by_release_id,
                "superseded_reason": self.superseded_reason, "superseded_at": self.superseded_at,
                "no_cnc_control": TRANCHE4_NO_CNC_MARKER}

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "release_fingerprint": self.release_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ManufacturingJobRelease":
        if data.get("no_cnc_control") != TRANCHE4_NO_CNC_MARKER:
            raise CamValidationError("Manufacturing release CNC boundary marker is missing")
        return cls(data["release_id"], ManufacturingJob.from_dict(data["job"]),
                   JobToolReconciliationReport.from_dict(data["tool_report"]),
                   JobReleaseReview.from_dict(data["review"]), data["released_at"],
                   ContentFingerprint.from_dict(data["package_fingerprint"]),
                   ManufacturingJobState(data["state"]), data.get("supersedes_release_id"),
                   data.get("superseded_by_release_id"), data.get("superseded_reason"), data.get("superseded_at"),
                   ContentFingerprint.from_dict(data["release_fingerprint"]))


@dataclass(frozen=True, slots=True)
class JobReleaseDiff:
    programs_added: tuple[str, ...]
    programs_removed: tuple[str, ...]
    nc_revision_changes: tuple[str, ...]
    setup_changes: tuple[str, ...]
    tool_changes: tuple[int, ...]
    machine_changed: bool
    post_changes: tuple[str, ...]
    findings_changes: tuple[str, ...]
    release_policy_changed: bool

    @property
    def changed(self) -> bool:
        return any((self.programs_added, self.programs_removed, self.nc_revision_changes, self.setup_changes,
                    self.tool_changes, self.machine_changed, self.post_changes, self.findings_changes,
                    self.release_policy_changed))

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) if not isinstance(getattr(self, name), tuple) else list(getattr(self, name))
                for name in self.__dataclass_fields__}


__all__ = [
    "JobProgramBinding", "JobReleaseDiff", "JobReleasePolicy", "JobReleaseReview", "JobQualificationState",
    "JobSetupBinding", "JobToolBinding", "JobToolReconciliationReport", "JobReleaseReview", "ManufacturingJob",
    "ManufacturingJobRelease", "ManufacturingJobState", "ReleaseDecision", "ToolReconciliationIssue",
    "TRANCHE4_COUNTERFACTUAL_MARKER", "TRANCHE4_FORMAT_VERSION", "TRANCHE4_NO_CNC_MARKER",
    "TRANCHE4_SCOPE_MARKER", "TRANCHE4_IMPLEMENTATION_MARKERS", "TRANCHE4_ADVERSARIAL_CASES",
]
