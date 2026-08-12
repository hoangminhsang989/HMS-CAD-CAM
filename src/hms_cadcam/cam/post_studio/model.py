"""Immutable production Post Studio contracts.

This module deliberately models Post source lifecycle separately from NC export
and machine qualification.  In particular, activating a revision is only a
*plan* here; filesystem deployment belongs to a separately authorized adapter.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint


POST_STUDIO_FORMAT_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")


def sha256_bytes(payload: bytes) -> str:
    if not isinstance(payload, bytes):
        raise TypeError("Post source must be bytes")
    return hashlib.sha256(payload).hexdigest()


def _text(value: str, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CamValidationError(f"{name} is invalid")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise CamValidationError(f"{name} contains control characters")
    return value.strip()


def _identifier(value: str, name: str) -> str:
    normalized = _text(value, name, 128)
    if _KEY.fullmatch(normalized) is None:
        raise CamValidationError(f"{name} is invalid")
    return normalized


def _sha(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CamValidationError(f"{name} is invalid")
    return value


def _timestamp(value: str, name: str) -> str:
    normalized = _text(value, name, 64)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise CamValidationError(f"{name} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CamValidationError(f"{name} requires a timezone")
    return normalized


def _fp(value: ContentFingerprint, name: str) -> ContentFingerprint:
    if not isinstance(value, ContentFingerprint):
        raise CamValidationError(f"{name} is invalid")
    return value


class PostLifecycleStatus(StrEnum):
    DRAFT = "DRAFT"
    CANDIDATE = "CANDIDATE"
    TESTING = "TESTING"
    VALIDATED = "VALIDATED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"
    REJECTED = "REJECTED"


class PostSourceFormat(StrEnum):
    WORKNC_DAT = "WORKNC_DAT"
    HMS_TYPED = "HMS_TYPED"
    IMPORTED_TEXT = "IMPORTED_TEXT"


class PostTestState(StrEnum):
    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    STALE = "STALE"


class PostDeploymentState(StrEnum):
    NOT_ACTIVE_GLOBALLY = "NOT_ACTIVE_GLOBALLY"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"
    UNKNOWN_TARGET = "UNKNOWN_TARGET"


@dataclass(frozen=True, slots=True)
class PostMachineBinding:
    machine_id: str
    controller_id: str
    tool_interface: str
    post_family: str
    machine_profile_fingerprint: ContentFingerprint

    def __post_init__(self) -> None:
        object.__setattr__(self, "machine_id", _identifier(self.machine_id, "Machine ID"))
        object.__setattr__(self, "controller_id", _identifier(self.controller_id, "Controller ID"))
        object.__setattr__(self, "tool_interface", _text(self.tool_interface, "Tool interface", 64))
        object.__setattr__(self, "post_family", _text(self.post_family, "Post family", 128))
        _fp(self.machine_profile_fingerprint, "Machine fingerprint")

    def to_dict(self) -> dict[str, Any]:
        return {"machine_id": self.machine_id, "controller_id": self.controller_id,
                "tool_interface": self.tool_interface, "post_family": self.post_family,
                "machine_profile_fingerprint": self.machine_profile_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostMachineBinding":
        if not isinstance(data, dict) or set(data) != {"machine_id", "controller_id", "tool_interface", "post_family", "machine_profile_fingerprint"}:
            raise CamValidationError("Post machine binding payload is malformed")
        return cls(data["machine_id"], data["controller_id"], data["tool_interface"], data["post_family"], ContentFingerprint.from_dict(data["machine_profile_fingerprint"]))


@dataclass(frozen=True, slots=True)
class PostDefinition:
    post_id: str
    display_name: str
    source_format: PostSourceFormat
    binding: PostMachineBinding
    created_at: str
    created_by: str
    definition_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "post_id", _identifier(self.post_id, "Post ID"))
        object.__setattr__(self, "display_name", _text(self.display_name, "Post display name", 256))
        if not isinstance(self.source_format, PostSourceFormat):
            raise CamValidationError("Post source format is invalid")
        if not isinstance(self.binding, PostMachineBinding):
            raise CamValidationError("Post binding is invalid")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "Post creation timestamp"))
        object.__setattr__(self, "created_by", _text(self.created_by, "Post creator", 256))
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.definition_fingerprint is None:
            object.__setattr__(self, "definition_fingerprint", calculated)
        elif self.definition_fingerprint != calculated:
            raise CamInvariantError("Post definition fingerprint mismatch")

    def identity_payload(self) -> dict[str, Any]:
        return {"format": "HMS_POST_STUDIO_DEFINITION", "format_version": POST_STUDIO_FORMAT_VERSION,
                "post_id": self.post_id, "display_name": self.display_name,
                "source_format": self.source_format.value, "binding": self.binding.to_dict(),
                "created_at": self.created_at, "created_by": self.created_by}

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "definition_fingerprint": self.definition_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostDefinition":
        fields = {"format", "format_version", "post_id", "display_name", "source_format", "binding", "created_at", "created_by", "definition_fingerprint"}
        if not isinstance(data, dict) or set(data) != fields or data["format"] != "HMS_POST_STUDIO_DEFINITION" or data["format_version"] != POST_STUDIO_FORMAT_VERSION:
            raise CamValidationError("Post definition payload is malformed")
        return cls(data["post_id"], data["display_name"], PostSourceFormat(data["source_format"]), PostMachineBinding.from_dict(data["binding"]), data["created_at"], data["created_by"], ContentFingerprint.from_dict(data["definition_fingerprint"]))


@dataclass(frozen=True, slots=True)
class PostRevision:
    revision_id: str
    post_id: str
    parent_revision_id: str | None
    source_sha256: str
    source_size: int
    source_encoding: str
    line_ending: str
    created_at: str
    created_by: str
    notes: str
    status: PostLifecycleStatus
    revision_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision_id", _identifier(self.revision_id, "Revision ID"))
        object.__setattr__(self, "post_id", _identifier(self.post_id, "Post ID"))
        if self.parent_revision_id is not None:
            object.__setattr__(self, "parent_revision_id", _identifier(self.parent_revision_id, "Parent revision ID"))
            if self.parent_revision_id == self.revision_id:
                raise CamInvariantError("Post revision cannot parent itself")
        object.__setattr__(self, "source_sha256", _sha(self.source_sha256, "Post source SHA-256"))
        if type(self.source_size) is not int or self.source_size < 0:
            raise CamValidationError("Post source size is invalid")
        object.__setattr__(self, "source_encoding", _text(self.source_encoding, "Post encoding", 64).casefold())
        if self.line_ending not in {"LF", "CRLF", "MIXED", "NONE"}:
            raise CamValidationError("Post line ending is invalid")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "Revision timestamp"))
        object.__setattr__(self, "created_by", _text(self.created_by, "Revision author", 256))
        object.__setattr__(self, "notes", _text(self.notes, "Revision notes", 4096))
        if self.status not in {PostLifecycleStatus.DRAFT, PostLifecycleStatus.CANDIDATE, PostLifecycleStatus.TESTING,
                               PostLifecycleStatus.VALIDATED, PostLifecycleStatus.REVIEW_REQUIRED,
                               PostLifecycleStatus.APPROVED, PostLifecycleStatus.ACTIVE, PostLifecycleStatus.SUPERSEDED,
                               PostLifecycleStatus.ROLLED_BACK, PostLifecycleStatus.REJECTED}:
            raise CamValidationError("Post revision status is invalid")
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.revision_fingerprint is None:
            object.__setattr__(self, "revision_fingerprint", calculated)
        elif self.revision_fingerprint != calculated:
            raise CamInvariantError("Post revision fingerprint mismatch")

    def identity_payload(self) -> dict[str, Any]:
        return {"format": "HMS_POST_STUDIO_REVISION", "format_version": POST_STUDIO_FORMAT_VERSION,
                "revision_id": self.revision_id, "post_id": self.post_id,
                "parent_revision_id": self.parent_revision_id, "source_sha256": self.source_sha256,
                "source_size": self.source_size, "source_encoding": self.source_encoding,
                "line_ending": self.line_ending, "created_at": self.created_at,
                "created_by": self.created_by, "notes": self.notes, "status": self.status.value}

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "revision_fingerprint": self.revision_fingerprint.to_dict()}

    @classmethod
    def new_id(cls) -> str:
        return f"rev.{uuid4().hex}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostRevision":
        fields = {"format", "format_version", "revision_id", "post_id", "parent_revision_id", "source_sha256", "source_size", "source_encoding", "line_ending", "created_at", "created_by", "notes", "status", "revision_fingerprint"}
        if not isinstance(data, dict) or set(data) != fields or data["format"] != "HMS_POST_STUDIO_REVISION" or data["format_version"] != POST_STUDIO_FORMAT_VERSION:
            raise CamValidationError("Post revision payload is malformed")
        return cls(data["revision_id"], data["post_id"], data["parent_revision_id"], data["source_sha256"], data["source_size"], data["source_encoding"], data["line_ending"], data["created_at"], data["created_by"], data["notes"], PostLifecycleStatus(data["status"]), ContentFingerprint.from_dict(data["revision_fingerprint"]))


@dataclass(frozen=True, slots=True)
class PostValidationResult:
    revision_id: str
    candidate_sha256: str
    validator_id: str
    validator_version: str
    state: PostTestState
    finding_codes: tuple[str, ...]
    validated_at: str
    validation_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision_id", _identifier(self.revision_id, "Revision ID"))
        object.__setattr__(self, "candidate_sha256", _sha(self.candidate_sha256, "Candidate SHA-256"))
        object.__setattr__(self, "validator_id", _identifier(self.validator_id, "Validator ID"))
        object.__setattr__(self, "validator_version", _text(self.validator_version, "Validator version", 64))
        if self.state not in {PostTestState.PASS, PostTestState.WARNING, PostTestState.FAIL, PostTestState.STALE}:
            raise CamValidationError("Validation state is invalid")
        if not isinstance(self.finding_codes, tuple) or tuple(sorted(set(self.finding_codes))) != self.finding_codes:
            raise CamInvariantError("Validation finding codes must be unique and ordered")
        if any(_KEY.fullmatch(item.casefold().replace("_", ".")) is None for item in self.finding_codes):
            raise CamValidationError("Validation finding code is invalid")
        object.__setattr__(self, "validated_at", _timestamp(self.validated_at, "Validation timestamp"))
        calculated = ContentFingerprint.from_payload(self.to_payload())
        if self.validation_fingerprint is None:
            object.__setattr__(self, "validation_fingerprint", calculated)
        elif self.validation_fingerprint != calculated:
            raise CamInvariantError("Post validation fingerprint mismatch")

    def to_payload(self) -> dict[str, Any]:
        return {"format": "HMS_POST_STUDIO_VALIDATION", "format_version": POST_STUDIO_FORMAT_VERSION,
                "revision_id": self.revision_id, "candidate_sha256": self.candidate_sha256,
                "validator_id": self.validator_id, "validator_version": self.validator_version,
                "state": self.state.value, "finding_codes": list(self.finding_codes), "validated_at": self.validated_at}

    def to_dict(self) -> dict[str, Any]:
        return {**self.to_payload(), "validation_fingerprint": self.validation_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostValidationResult":
        fields = {"format", "format_version", "revision_id", "candidate_sha256", "validator_id", "validator_version", "state", "finding_codes", "validated_at", "validation_fingerprint"}
        if not isinstance(data, dict) or set(data) != fields or data["format"] != "HMS_POST_STUDIO_VALIDATION" or data["format_version"] != POST_STUDIO_FORMAT_VERSION or not isinstance(data["finding_codes"], list):
            raise CamValidationError("Post validation payload is malformed")
        return cls(data["revision_id"], data["candidate_sha256"], data["validator_id"], data["validator_version"], PostTestState(data["state"]), tuple(data["finding_codes"]), data["validated_at"], ContentFingerprint.from_dict(data["validation_fingerprint"]))


@dataclass(frozen=True, slots=True)
class PostRegressionResult:
    revision_id: str
    corpus_id: str
    candidate_sha256: str
    baseline_nc_sha256: str
    candidate_nc_sha256: str
    expected_change_count: int
    unexpected_change_count: int
    state: PostTestState
    completed_at: str
    regression_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision_id", _identifier(self.revision_id, "Revision ID"))
        object.__setattr__(self, "corpus_id", _identifier(self.corpus_id, "Regression corpus ID"))
        for name in ("candidate_sha256", "baseline_nc_sha256", "candidate_nc_sha256"):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        for name in ("expected_change_count", "unexpected_change_count"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise CamValidationError(f"{name} is invalid")
        if self.state not in {PostTestState.PASS, PostTestState.WARNING, PostTestState.FAIL, PostTestState.STALE}:
            raise CamValidationError("Regression state is invalid")
        if self.state is PostTestState.PASS and self.unexpected_change_count:
            raise CamInvariantError("Passing regression cannot have unexpected changes")
        object.__setattr__(self, "completed_at", _timestamp(self.completed_at, "Regression timestamp"))
        calculated = ContentFingerprint.from_payload(self.to_payload())
        if self.regression_fingerprint is None:
            object.__setattr__(self, "regression_fingerprint", calculated)
        elif self.regression_fingerprint != calculated:
            raise CamInvariantError("Post regression fingerprint mismatch")

    def to_payload(self) -> dict[str, Any]:
        return {"format": "HMS_POST_STUDIO_REGRESSION", "format_version": POST_STUDIO_FORMAT_VERSION,
                "revision_id": self.revision_id, "corpus_id": self.corpus_id,
                "candidate_sha256": self.candidate_sha256, "baseline_nc_sha256": self.baseline_nc_sha256,
                "candidate_nc_sha256": self.candidate_nc_sha256, "expected_change_count": self.expected_change_count,
                "unexpected_change_count": self.unexpected_change_count, "state": self.state.value,
                "completed_at": self.completed_at}

    def to_dict(self) -> dict[str, Any]:
        return {**self.to_payload(), "regression_fingerprint": self.regression_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostRegressionResult":
        fields = {"format", "format_version", "revision_id", "corpus_id", "candidate_sha256", "baseline_nc_sha256", "candidate_nc_sha256", "expected_change_count", "unexpected_change_count", "state", "completed_at", "regression_fingerprint"}
        if not isinstance(data, dict) or set(data) != fields or data["format"] != "HMS_POST_STUDIO_REGRESSION" or data["format_version"] != POST_STUDIO_FORMAT_VERSION:
            raise CamValidationError("Post regression payload is malformed")
        return cls(data["revision_id"], data["corpus_id"], data["candidate_sha256"], data["baseline_nc_sha256"], data["candidate_nc_sha256"], data["expected_change_count"], data["unexpected_change_count"], PostTestState(data["state"]), data["completed_at"], ContentFingerprint.from_dict(data["regression_fingerprint"]))


@dataclass(frozen=True, slots=True)
class PostApproval:
    revision_id: str
    approval_identity: str
    approved_at: str
    decision: str
    validation_fingerprint: ContentFingerprint
    regression_fingerprint: ContentFingerprint
    approval_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision_id", _identifier(self.revision_id, "Revision ID"))
        object.__setattr__(self, "approval_identity", _text(self.approval_identity, "Approval identity", 256))
        object.__setattr__(self, "approved_at", _timestamp(self.approved_at, "Approval timestamp"))
        if self.decision != "APPROVE":
            raise CamValidationError("Approval decision is invalid")
        _fp(self.validation_fingerprint, "Validation fingerprint"); _fp(self.regression_fingerprint, "Regression fingerprint")
        calculated = ContentFingerprint.from_payload(self.to_payload())
        if self.approval_fingerprint is None:
            object.__setattr__(self, "approval_fingerprint", calculated)
        elif self.approval_fingerprint != calculated:
            raise CamInvariantError("Post approval fingerprint mismatch")

    def to_payload(self) -> dict[str, Any]:
        return {"format": "HMS_POST_STUDIO_APPROVAL", "format_version": POST_STUDIO_FORMAT_VERSION,
                "revision_id": self.revision_id, "approval_identity": self.approval_identity,
                "approved_at": self.approved_at, "decision": self.decision,
                "validation_fingerprint": self.validation_fingerprint.to_dict(),
                "regression_fingerprint": self.regression_fingerprint.to_dict()}

    def to_dict(self) -> dict[str, Any]:
        return {**self.to_payload(), "approval_fingerprint": self.approval_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostApproval":
        fields = {"format", "format_version", "revision_id", "approval_identity", "approved_at", "decision", "validation_fingerprint", "regression_fingerprint", "approval_fingerprint"}
        if not isinstance(data, dict) or set(data) != fields or data["format"] != "HMS_POST_STUDIO_APPROVAL" or data["format_version"] != POST_STUDIO_FORMAT_VERSION:
            raise CamValidationError("Post approval payload is malformed")
        return cls(data["revision_id"], data["approval_identity"], data["approved_at"], data["decision"], ContentFingerprint.from_dict(data["validation_fingerprint"]), ContentFingerprint.from_dict(data["regression_fingerprint"]), ContentFingerprint.from_dict(data["approval_fingerprint"]))


@dataclass(frozen=True, slots=True)
class PostActivationPlan:
    """A verified deployment plan; it deliberately does not write target bytes."""

    revision_id: str
    expected_parent_sha256: str
    approved_candidate_sha256: str
    target_reference: str
    deployment_state: PostDeploymentState = PostDeploymentState.NOT_ACTIVE_GLOBALLY

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision_id", _identifier(self.revision_id, "Revision ID"))
        object.__setattr__(self, "expected_parent_sha256", _sha(self.expected_parent_sha256, "Expected parent SHA-256"))
        object.__setattr__(self, "approved_candidate_sha256", _sha(self.approved_candidate_sha256, "Approved candidate SHA-256"))
        object.__setattr__(self, "target_reference", _text(self.target_reference, "Deployment target reference", 2048))
        if self.deployment_state is not PostDeploymentState.NOT_ACTIVE_GLOBALLY:
            raise CamInvariantError("Studio Tranche1 activation plans must remain non-active")

    def to_dict(self) -> dict[str, Any]:
        return {"format": "HMS_POST_STUDIO_ACTIVATION_PLAN", "format_version": POST_STUDIO_FORMAT_VERSION,
                "revision_id": self.revision_id, "expected_parent_sha256": self.expected_parent_sha256,
                "approved_candidate_sha256": self.approved_candidate_sha256,
                "target_reference": self.target_reference, "deployment_state": self.deployment_state.value,
                "no_global_write": True}


__all__ = [name for name in globals() if name.startswith("Post") or name in {"POST_STUDIO_FORMAT_VERSION", "sha256_bytes"}]
