"""Safe in-process services for the Post Processor Studio lifecycle."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.offline_analyzer import AnalysisPolicy, analyze_nc_bytes
from hms_cadcam.cam.qualification.offline_model import OfflineFindingSeverity
from hms_cadcam.cam.post_studio.model import (
    PostActivationPlan, PostApproval, PostDefinition, PostDeploymentState,
    PostLifecycleStatus, PostRegressionResult, PostRevision, PostSourceFormat,
    PostTestState, PostValidationResult, sha256_bytes,
)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class SourceTextInfo:
    encoding: str
    line_ending: str
    text: str | None


@dataclass(frozen=True, slots=True)
class PostSourceDiff:
    parent_revision_id: str | None
    revision_id: str
    text_lines: tuple[str, ...]
    added_lines: tuple[str, ...]
    removed_lines: tuple[str, ...]
    semantic_changes: tuple[str, ...]
    diff_fingerprint: ContentFingerprint


@dataclass(frozen=True, slots=True)
class NCDiff:
    baseline_sha256: str
    candidate_sha256: str
    expected_changes: tuple[str, ...]
    unexpected_changes: tuple[str, ...]
    diff_fingerprint: ContentFingerprint


def inspect_source_bytes(payload: bytes) -> SourceTextInfo:
    """Detect common legacy text encodings without rewriting the original bytes."""

    if not isinstance(payload, bytes):
        raise TypeError("Post source must be bytes")
    encoding = "binary"
    text: str | None = None
    for candidate in ("utf-8-sig", "utf-16", "cp1258", "cp1252"):
        try:
            decoded = payload.decode(candidate)
        except UnicodeDecodeError:
            continue
        if "\x00" not in decoded:
            encoding, text = candidate, decoded
            break
    if text is None:
        return SourceTextInfo(encoding, "NONE", None)
    if "\r\n" in text and "\n" not in text.replace("\r\n", ""):
        ending = "CRLF"
    elif "\r\n" in text and "\n" in text.replace("\r\n", ""):
        ending = "MIXED"
    elif "\n" in text:
        ending = "LF"
    else:
        ending = "NONE"
    return SourceTextInfo(encoding, ending, text)


def _known_semantics(lines: Iterable[str]) -> tuple[str, ...]:
    result: set[str] = set()
    for line in lines:
        upper = line.upper().replace(" ", "")
        for code, label in (("G40", "G40_CANCELLATION"), ("G41", "G41_LEFT_COMPENSATION"),
                            ("G42", "G42_RIGHT_COMPENSATION"), ("G28", "G28_SAFE_RETURN"),
                            ("G53", "G53_MACHINE_RETURN"), ("G54", "G54_WORK_OFFSET")):
            if code in upper:
                result.add(label)
    return tuple(sorted(result))


def diff_source(parent: PostRevision | None, parent_bytes: bytes | None, revision: PostRevision, revision_bytes: bytes) -> PostSourceDiff:
    before = inspect_source_bytes(parent_bytes).text.splitlines() if parent_bytes is not None and inspect_source_bytes(parent_bytes).text is not None else []
    after_info = inspect_source_bytes(revision_bytes)
    after = after_info.text.splitlines() if after_info.text is not None else []
    rendered = tuple(difflib.unified_diff(before, after, fromfile=parent.revision_id if parent else "/dev/null", tofile=revision.revision_id, lineterm=""))
    added = tuple(line[1:] for line in rendered if line.startswith("+") and not line.startswith("+++"))
    removed = tuple(line[1:] for line in rendered if line.startswith("-") and not line.startswith("---"))
    semantics = tuple(sorted(set(_known_semantics(added)) | {f"REMOVED_{v}" for v in _known_semantics(removed)}))
    payload = {"parent_revision_id": parent.revision_id if parent else None, "revision_id": revision.revision_id,
               "text_lines": list(rendered), "added_lines": list(added), "removed_lines": list(removed),
               "semantic_changes": list(semantics)}
    return PostSourceDiff(parent.revision_id if parent else None, revision.revision_id, rendered, added, removed, semantics, ContentFingerprint.from_payload(payload))


def diff_nc(baseline: bytes, candidate: bytes, *, expected_added_tokens: tuple[str, ...] = ("G40",)) -> NCDiff:
    before = baseline.decode("ascii", errors="replace").splitlines()
    after = candidate.decode("ascii", errors="replace").splitlines()
    rendered = tuple(difflib.ndiff(before, after))
    expected, unexpected = [], []
    for line in rendered:
        if not line.startswith(('+ ', '- ')):
            continue
        change = line[2:].strip()
        (expected if any(token in change.upper() for token in expected_added_tokens) else unexpected).append(line)
    payload = {"baseline_sha256": sha256_bytes(baseline), "candidate_sha256": sha256_bytes(candidate),
               "expected_changes": expected, "unexpected_changes": unexpected}
    return NCDiff(payload["baseline_sha256"], payload["candidate_sha256"], tuple(expected), tuple(unexpected), ContentFingerprint.from_payload(payload))


class PostStudioService:
    """An isolated immutable Post library. It has no global filesystem write API."""

    def __init__(self) -> None:
        self._definitions: dict[str, PostDefinition] = {}
        self._revisions: dict[str, PostRevision] = {}
        self._sources: dict[str, bytes] = {}
        self._validations: dict[str, PostValidationResult] = {}
        self._regressions: dict[str, PostRegressionResult] = {}
        self._approvals: dict[str, PostApproval] = {}

    def register_definition(self, definition: PostDefinition) -> PostDefinition:
        existing = self._definitions.get(definition.post_id)
        if existing is not None and existing != definition:
            raise CamInvariantError("Post definition ID already exists")
        self._definitions[definition.post_id] = definition
        return definition

    def import_source(self, definition: PostDefinition, source: bytes, *, revision_id: str,
                      created_at: str, created_by: str, notes: str) -> PostRevision:
        self.register_definition(definition)
        if any(item.post_id == definition.post_id for item in self._revisions.values()):
            raise CamInvariantError("Initial import requires a new Post definition")
        return self._commit(definition.post_id, source, revision_id=revision_id, parent_revision_id=None,
                            status=PostLifecycleStatus.VALIDATED, created_at=created_at,
                            created_by=created_by, notes=notes)

    def create_candidate(self, parent_revision_id: str, source: bytes, *, revision_id: str,
                         created_at: str, created_by: str, notes: str) -> PostRevision:
        parent = self.revision(parent_revision_id)
        if sha256_bytes(source) == parent.source_sha256:
            raise CamInvariantError("Candidate source must differ from its parent")
        return self._commit(parent.post_id, source, revision_id=revision_id, parent_revision_id=parent.revision_id,
                            status=PostLifecycleStatus.CANDIDATE, created_at=created_at,
                            created_by=created_by, notes=notes)

    def _commit(self, post_id: str, source: bytes, *, revision_id: str, parent_revision_id: str | None,
                status: PostLifecycleStatus, created_at: str, created_by: str, notes: str) -> PostRevision:
        if revision_id in self._revisions:
            raise CamInvariantError("Post revision IDs are immutable")
        info = inspect_source_bytes(source)
        revision = PostRevision(revision_id, post_id, parent_revision_id, sha256_bytes(source), len(source),
                                info.encoding, info.line_ending, created_at, created_by, notes, status)
        self._revisions[revision_id], self._sources[revision_id] = revision, bytes(source)
        return revision

    def revision(self, revision_id: str) -> PostRevision:
        try:
            return self._revisions[revision_id]
        except KeyError as error:
            raise CamValidationError("Unknown Post revision") from error

    def source_bytes(self, revision_id: str) -> bytes:
        self.revision(revision_id)
        return self._sources[revision_id]

    def revisions_for(self, post_id: str) -> tuple[PostRevision, ...]:
        return tuple(item for item in self._revisions.values() if item.post_id == post_id)

    def definitions(self) -> tuple[PostDefinition, ...]:
        return tuple(self._definitions.values())

    def source_diff(self, revision_id: str) -> PostSourceDiff:
        revision = self.revision(revision_id)
        parent = self._revisions.get(revision.parent_revision_id) if revision.parent_revision_id else None
        return diff_source(parent, self._sources.get(parent.revision_id) if parent else None, revision, self.source_bytes(revision_id))

    def validate(self, revision_id: str, *, validated_at: str, validator_id: str = "hms.stage18a.fanuc.static",
                 validator_version: str = "1") -> PostValidationResult:
        revision = self.revision(revision_id)
        # Post files cannot be falsely treated as NC files. A small source policy
        # catches empty/control-byte source; generated NC remains validated by its own analyzer.
        payload = self.source_bytes(revision_id)
        codes: list[str] = []
        state = PostTestState.PASS
        if not payload:
            codes.append("SOURCE_EMPTY"); state = PostTestState.FAIL
        if b"\x00" in payload:
            codes.append("SOURCE_BINARY_OR_UNSUPPORTED_ENCODING"); state = PostTestState.WARNING
        result = PostValidationResult(revision_id, revision.source_sha256, validator_id, validator_version,
                                      state, tuple(sorted(codes)), validated_at)
        self._validations[revision_id] = result
        return result

    def validate_generated_nc(self, revision_id: str, nc_bytes: bytes, *, validated_at: str) -> PostValidationResult:
        revision = self.revision(revision_id)
        analysis = analyze_nc_bytes(nc_bytes, AnalysisPolicy())
        codes = tuple(sorted({item.code for item in analysis.findings}))
        state = PostTestState.FAIL if any(item.severity is OfflineFindingSeverity.BLOCKER for item in analysis.findings) else PostTestState.WARNING if codes else PostTestState.PASS
        result = PostValidationResult(revision_id, revision.source_sha256, "hms.stage18a.nc.semantic", "1", state, codes, validated_at)
        self._validations[revision_id] = result
        return result

    def record_regression(self, revision_id: str, *, corpus_id: str, baseline_nc: bytes, candidate_nc: bytes,
                          expected_added_tokens: tuple[str, ...] = ("G40",), completed_at: str) -> PostRegressionResult:
        revision = self.revision(revision_id)
        comparison = diff_nc(baseline_nc, candidate_nc, expected_added_tokens=expected_added_tokens)
        state = PostTestState.PASS if not comparison.unexpected_changes else PostTestState.FAIL
        result = PostRegressionResult(revision_id, corpus_id, revision.source_sha256, comparison.baseline_sha256,
                                      comparison.candidate_sha256, len(comparison.expected_changes),
                                      len(comparison.unexpected_changes), state, completed_at)
        self._regressions[revision_id] = result
        return result

    def approve(self, revision_id: str, *, approval_identity: str, approved_at: str) -> PostApproval:
        revision = self.revision(revision_id)
        validation, regression = self._validations.get(revision_id), self._regressions.get(revision_id)
        if validation is None or validation.state not in {PostTestState.PASS, PostTestState.WARNING}:
            raise CamInvariantError("Post approval requires current validation evidence")
        if regression is None or regression.state is not PostTestState.PASS:
            raise CamInvariantError("Post approval requires passing current regression evidence")
        approval = PostApproval(revision_id, approval_identity, approved_at, "APPROVE", validation.validation_fingerprint, regression.regression_fingerprint)
        self._approvals[revision_id] = approval
        return approval

    def activation_plan(self, revision_id: str, *, expected_parent_sha256: str, target_reference: str) -> PostActivationPlan:
        revision = self.revision(revision_id)
        if revision_id not in self._approvals:
            raise CamInvariantError("Activation plan requires attributable approval")
        return PostActivationPlan(revision_id, expected_parent_sha256, revision.source_sha256, target_reference,
                                  PostDeploymentState.NOT_ACTIVE_GLOBALLY)

    def lifecycle_status(self, revision_id: str) -> PostLifecycleStatus:
        """Project current lifecycle from immutable revision plus current evidence."""

        revision = self.revision(revision_id)
        if revision_id in self._approvals:
            return PostLifecycleStatus.APPROVED
        regression = self._regressions.get(revision_id)
        validation = self._validations.get(revision_id)
        if regression is not None and regression.state is PostTestState.FAIL:
            return PostLifecycleStatus.REJECTED
        if validation is not None and validation.state is PostTestState.FAIL:
            return PostLifecycleStatus.REJECTED
        if validation is not None and regression is not None:
            return PostLifecycleStatus.REVIEW_REQUIRED
        return revision.status


__all__ = ["NCDiff", "PostSourceDiff", "PostStudioService", "SourceTextInfo", "diff_nc", "diff_source", "inspect_source_bytes", "utc_timestamp"]
