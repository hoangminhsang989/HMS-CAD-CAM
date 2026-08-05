"""Immutable domain models and primitive validators for AI Sync Engine V1.1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PureWindowsPath
import re
from typing import Any


ENGINE_VERSION = "1.1.0"
STATE_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
MINIMUM_READER_VERSION = "1.1.0"
CREATED_BY = "hms_ai_sync_engine"

SUPPORTED_CAPABILITIES = (
    "canonical_state_json",
    "dry_run",
    "git_read_only_snapshot",
    "immutable_checkpoint",
    "journaled_publication",
    "markdown_render",
    "rollback_recovery",
    "source_protection",
    "structured_logging",
    "test_evidence_read",
)

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _require_string(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a non-empty string")
    return value


def require_non_negative_int(value: object, field: str) -> int:
    """Return a non-negative integer while rejecting bool-as-int."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def require_positive_int(value: object, field: str) -> int:
    result = require_non_negative_int(value, field)
    if result == 0:
        raise ValueError(f"{field} must be positive")
    return result


def validate_utc_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must use UTC")
    return value


def validate_sha256(value: object, field: str = "sha256") -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be 64 lowercase hexadecimal characters")
    return value


def validate_semver(value: object, field: str = "version") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a semantic-version string")
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"{field} must use Semantic Versioning")
    prerelease = match.group(4)
    if prerelease:
        for identifier in prerelease.split("."):
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise ValueError(f"{field} has a leading-zero prerelease identifier")
    return value


def normalize_relative_posix_path(value: object, field: str = "path", *, allow_dot: bool = False) -> str:
    """Normalize a serialized relative path without consulting the filesystem."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{field} must be a non-empty relative path")
    windows_path = PureWindowsPath(value)
    if windows_path.drive or value.startswith(("/", "\\")):
        raise ValueError(f"{field} must not be absolute, drive-relative, or UNC")
    normalized = value.replace("\\", "/")
    if normalized == "." and allow_dot:
        return normalized
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field} contains an unsafe path component")
    if any(":" in part for part in parts):
        raise ValueError(f"{field} must not contain a drive or alternate stream")
    return "/".join(parts)


def validate_percentage(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric or null")
    result = float(value)
    if not 0.0 <= result <= 100.0:
        raise ValueError(f"{field} must be between 0 and 100")
    return result


def _string_tuple(values: object, field: str, *, sorted_unique: bool = False) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise TypeError(f"{field} must be a collection of strings")
    result = tuple(values)
    if any(not isinstance(item, str) or not item for item in result):
        raise TypeError(f"{field} must contain non-empty strings")
    if sorted_unique:
        return tuple(sorted(set(result)))
    return result


def _optional_count(value: object, field: str) -> int | None:
    if value is None:
        return None
    return require_non_negative_int(value, field)


def _validate_git_oid(value: object, field: str = "head_oid") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _GIT_OID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a full lowercase Git object id")
    return value


class WorkingTreeKind(StrEnum):
    ORDINARY = "ordinary"
    RENAMED = "renamed"
    COPIED = "copied"
    UNMERGED = "unmerged"
    UNTRACKED = "untracked"
    IGNORED = "ignored"


class DiffScope(StrEnum):
    UNSTAGED = "unstaged"
    STAGED = "staged"


class TestStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ERROR = "error"
    UNKNOWN = "unknown"


class EvidenceSource(StrEnum):
    RUNNER_JSON = "runner_json"
    MANUAL = "manual"
    IMPORTED_LOG = "imported_log"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class ProjectStatus(StrEnum):
    NOT_STARTED = "not_started"
    WORK_IN_PROGRESS = "work_in_progress"
    BLOCKED = "blocked"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


class BlockersState(StrEnum):
    UNKNOWN = "unknown"
    NONE_REPORTED = "none_reported"
    VERIFIED_NONE = "verified_none"
    PRESENT = "present"


class SyncCommand(StrEnum):
    INSPECT = "inspect"
    SYNC = "sync"
    VALIDATE = "validate"
    SHOW_PLAN = "show_plan"


class PublicationStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    PUBLISHED = "published"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    RECOVERY_REQUIRED = "recovery_required"


class ArtifactRole(StrEnum):
    CANONICAL_STATE = "canonical_state"
    DERIVED_JSON = "derived_json"
    DERIVED_MARKDOWN = "derived_markdown"
    CHECKPOINT = "checkpoint"


class ValidationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


@dataclass(frozen=True, slots=True)
class WorkingTreeEntry:
    path: str
    index_status: str
    worktree_status: str
    kind: WorkingTreeKind
    original_path: str | None = None
    submodule_state: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_relative_posix_path(self.path))
        for name in ("index_status", "worktree_status"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 1:
                raise ValueError(f"{name} must be a one-character porcelain status")
        if not isinstance(self.kind, WorkingTreeKind):
            raise TypeError("kind must be WorkingTreeKind")
        if self.original_path is not None:
            object.__setattr__(
                self,
                "original_path",
                normalize_relative_posix_path(self.original_path, "original_path"),
            )
        if self.kind in {WorkingTreeKind.RENAMED, WorkingTreeKind.COPIED} and self.original_path is None:
            raise ValueError("renamed/copied entries require original_path")
        if self.kind not in {WorkingTreeKind.RENAMED, WorkingTreeKind.COPIED} and self.original_path is not None:
            raise ValueError("original_path is only valid for renamed/copied entries")

    @property
    def is_staged(self) -> bool:
        return self.index_status not in {".", "?", "!"}


@dataclass(frozen=True, slots=True)
class DiffStatEntry:
    path: str
    original_path: str | None
    insertions: int | None
    deletions: int | None
    binary: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_relative_posix_path(self.path))
        if self.original_path is not None:
            object.__setattr__(
                self,
                "original_path",
                normalize_relative_posix_path(self.original_path, "original_path"),
            )
        if not isinstance(self.binary, bool):
            raise TypeError("binary must be bool")
        if self.binary:
            if self.insertions is not None or self.deletions is not None:
                raise ValueError("binary entries must have null insertion/deletion counts")
        else:
            require_non_negative_int(self.insertions, "insertions")
            require_non_negative_int(self.deletions, "deletions")


@dataclass(frozen=True, slots=True)
class DiffSummary:
    scope: DiffScope
    files_changed: int
    insertions: int | None
    deletions: int | None
    binary_files: int
    entries: tuple[DiffStatEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, DiffScope):
            raise TypeError("scope must be DiffScope")
        for name in ("files_changed", "binary_files"):
            require_non_negative_int(getattr(self, name), name)
        for name in ("insertions", "deletions"):
            _optional_count(getattr(self, name), name)
        if not isinstance(self.entries, (tuple, list)) or any(
            not isinstance(item, DiffStatEntry) for item in self.entries
        ):
            raise TypeError("entries must contain DiffStatEntry")
        normalized = tuple(self.entries)
        if len({item.path.casefold() for item in normalized}) != len(normalized):
            raise ValueError("entries must not contain duplicate paths")
        object.__setattr__(
            self,
            "entries",
            tuple(sorted(normalized, key=lambda item: (item.path.casefold(), item.path))),
        )


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    repository_root: Path
    captured_at: datetime
    branch: str | None
    is_detached: bool
    head_oid: str | None
    upstream: str | None
    ahead: int | None
    behind: int | None
    remote_urls: tuple[tuple[str, str], ...]
    entries: tuple[WorkingTreeEntry, ...]
    staged_diff: DiffSummary
    unstaged_diff: DiffSummary
    is_dirty: bool
    fingerprint_sha256: str
    unborn_branch: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.repository_root, Path):
            raise TypeError("repository_root must be pathlib.Path")
        validate_utc_datetime(self.captured_at, "captured_at")
        _require_string(self.branch, "branch", nullable=True)
        _require_string(self.unborn_branch, "unborn_branch", nullable=True)
        if not isinstance(self.is_detached, bool) or not isinstance(self.is_dirty, bool):
            raise TypeError("Git state flags must be bool")
        if self.unborn_branch is not None:
            if self.branch is not None or self.is_detached or self.head_oid is not None:
                raise ValueError("unborn branch metadata is inconsistent")
        _validate_git_oid(self.head_oid)
        _require_string(self.upstream, "upstream", nullable=True)
        _optional_count(self.ahead, "ahead")
        _optional_count(self.behind, "behind")
        if self.upstream is None and (self.ahead is not None or self.behind is not None):
            raise ValueError("ahead/behind require an upstream")
        if any(not isinstance(item, WorkingTreeEntry) for item in self.entries):
            raise TypeError("entries must contain WorkingTreeEntry")
        ordered = tuple(sorted(self.entries, key=lambda item: (item.path.casefold(), item.path)))
        if len({item.path.casefold() for item in ordered}) != len(ordered):
            raise ValueError("entries contain duplicate canonical paths")
        object.__setattr__(self, "entries", ordered)
        if self.staged_diff.scope is not DiffScope.STAGED or self.unstaged_diff.scope is not DiffScope.UNSTAGED:
            raise ValueError("diff summaries have incorrect scope")
        validate_sha256(self.fingerprint_sha256, "fingerprint_sha256")


@dataclass(frozen=True, slots=True)
class TestEvidence:
    run_id: str
    command: tuple[str, ...]
    exit_code: int | None
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    passed: int | None
    failed: int | None
    skipped: int | None
    deselected: int | None
    xfailed: int | None
    xpassed: int | None
    warnings: int | None
    status: TestStatus
    evidence_source: EvidenceSource
    log_path: str | None
    log_sha256: str | None
    verification: VerificationStatus
    verification_issues: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_string(self.run_id, "run_id")
        command = _string_tuple(self.command, "command")
        if not command:
            raise ValueError("command must not be empty")
        object.__setattr__(self, "command", command)
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise TypeError("exit_code must be an integer or null")
        validate_utc_datetime(self.started_at, "started_at")
        validate_utc_datetime(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if isinstance(self.duration_seconds, bool) or not isinstance(self.duration_seconds, (int, float)):
            raise TypeError("duration_seconds must be numeric")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be non-negative")
        for name in ("passed", "failed", "skipped", "deselected", "xfailed", "xpassed", "warnings"):
            _optional_count(getattr(self, name), name)
        if not isinstance(self.status, TestStatus) or not isinstance(self.evidence_source, EvidenceSource):
            raise TypeError("invalid test evidence enum")
        if not isinstance(self.verification, VerificationStatus):
            raise TypeError("verification must be VerificationStatus")
        if self.log_path is not None:
            object.__setattr__(self, "log_path", normalize_relative_posix_path(self.log_path, "log_path"))
        if self.log_sha256 is not None:
            validate_sha256(self.log_sha256, "log_sha256")
        object.__setattr__(self, "verification_issues", _string_tuple(self.verification_issues, "verification_issues"))
        if self.status is TestStatus.PASSED and self.verification is VerificationStatus.VERIFIED:
            if self.exit_code != 0 or (self.failed not in {None, 0}):
                raise ValueError("verified passed evidence requires exit_code 0 and no failures")


@dataclass(frozen=True, slots=True)
class ProjectState:
    state_schema_version: int
    run_id: str
    project_name: str
    generated_at: datetime
    stage: str | None
    status: ProjectStatus
    current_task: str | None
    git: GitSnapshot
    tests: tuple[TestEvidence, ...]
    remaining_work: tuple[str, ...]
    blockers: tuple[str, ...]
    next_action: str | None
    stage_progress_percent: float | None
    overall_progress_percent: float | None
    provenance: tuple[tuple[str, str], ...]
    blockers_state: BlockersState = BlockersState.UNKNOWN
    commit_claim_oid: str | None = None
    commit_claim_verified: bool | None = None

    def __post_init__(self) -> None:
        require_positive_int(self.state_schema_version, "state_schema_version")
        _require_string(self.run_id, "run_id")
        _require_string(self.project_name, "project_name")
        validate_utc_datetime(self.generated_at, "generated_at")
        _require_string(self.stage, "stage", nullable=True)
        _require_string(self.current_task, "current_task", nullable=True)
        _require_string(self.next_action, "next_action", nullable=True)
        if not isinstance(self.status, ProjectStatus) or not isinstance(self.git, GitSnapshot):
            raise TypeError("ProjectState has invalid typed fields")
        if not isinstance(self.blockers_state, BlockersState):
            raise TypeError("blockers_state must be BlockersState")
        if any(not isinstance(item, TestEvidence) for item in self.tests):
            raise TypeError("tests must contain TestEvidence")
        object.__setattr__(self, "remaining_work", _string_tuple(self.remaining_work, "remaining_work"))
        object.__setattr__(self, "blockers", _string_tuple(self.blockers, "blockers"))
        if self.blockers_state is BlockersState.PRESENT and not self.blockers:
            raise ValueError("present blockers_state requires blockers")
        if self.blockers_state is not BlockersState.PRESENT and self.blockers:
            raise ValueError("blockers require present blockers_state")
        _validate_git_oid(self.commit_claim_oid, "commit_claim_oid")
        if self.commit_claim_verified is not None and not isinstance(self.commit_claim_verified, bool):
            raise TypeError("commit_claim_verified must be bool or null")
        if self.commit_claim_oid is None and self.commit_claim_verified is not None:
            raise ValueError("commit verification requires a commit claim")
        object.__setattr__(self, "stage_progress_percent", validate_percentage(self.stage_progress_percent, "stage_progress_percent"))
        object.__setattr__(self, "overall_progress_percent", validate_percentage(self.overall_progress_percent, "overall_progress_percent"))
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.provenance):
            raise TypeError("provenance must contain string pairs")
        object.__setattr__(self, "provenance", tuple(sorted(self.provenance)))


@dataclass(frozen=True, slots=True)
class SyncRequest:
    repository: Path
    command: SyncCommand
    config_path: Path
    metadata_path: Path | None
    inline_stage: str | None
    inline_task: str | None
    dry_run: bool
    expected_head: str | None
    run_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.repository, Path) or not isinstance(self.config_path, Path):
            raise TypeError("repository and config_path must be pathlib.Path")
        if self.metadata_path is not None and not isinstance(self.metadata_path, Path):
            raise TypeError("metadata_path must be pathlib.Path or null")
        if not isinstance(self.command, SyncCommand) or not isinstance(self.dry_run, bool):
            raise TypeError("invalid sync request fields")
        _require_string(self.inline_stage, "inline_stage", nullable=True)
        _require_string(self.inline_task, "inline_task", nullable=True)
        _validate_git_oid(self.expected_head, "expected_head")
        _require_string(self.run_id, "run_id")


@dataclass(frozen=True, slots=True)
class VersionInfo:
    engine_version: str
    state_schema_version: int
    manifest_schema_version: int
    minimum_reader_version: str
    created_by: str

    def __post_init__(self) -> None:
        validate_semver(self.engine_version, "engine_version")
        require_positive_int(self.state_schema_version, "state_schema_version")
        require_positive_int(self.manifest_schema_version, "manifest_schema_version")
        validate_semver(self.minimum_reader_version, "minimum_reader_version")
        if not isinstance(self.created_by, str) or re.fullmatch(r"[a-z][a-z0-9_]*", self.created_by) is None:
            raise ValueError("created_by must be a stable lowercase identifier")


def default_version_info() -> VersionInfo:
    return VersionInfo(
        engine_version=ENGINE_VERSION,
        state_schema_version=STATE_SCHEMA_VERSION,
        manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        minimum_reader_version=MINIMUM_READER_VERSION,
        created_by=CREATED_BY,
    )


def _looks_forbidden_capability(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    tokens = set(normalized.split("_"))
    if tokens & {"stage", "commit", "push"}:
        return True
    return "run" in tokens and bool(tokens & {"test", "tests", "pytest"})


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    supported: tuple[str, ...]
    required: tuple[str, ...]

    def __post_init__(self) -> None:
        supported = _string_tuple(self.supported, "supported", sorted_unique=True)
        required = _string_tuple(self.required, "required", sorted_unique=True)
        for value in (*supported, *required):
            if _looks_forbidden_capability(value):
                raise ValueError("forbidden mutating or test-execution capability")
            if _CAPABILITY_RE.fullmatch(value) is None or value not in SUPPORTED_CAPABILITIES:
                raise ValueError("unsupported capability")
        if not set(required).issubset(supported):
            raise ValueError("required capabilities must be a subset of supported")
        object.__setattr__(self, "supported", supported)
        object.__setattr__(self, "required", required)


@dataclass(frozen=True, slots=True)
class StateDocument:
    version: VersionInfo
    run_id: str
    generated_at_utc: datetime
    capabilities: CapabilitySet
    project_state: ProjectState
    test_evidence_summary: tuple[tuple[str, Any], ...]
    publication: tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        _require_string(self.run_id, "run_id")
        validate_utc_datetime(self.generated_at_utc, "generated_at_utc")
        if not isinstance(self.version, VersionInfo) or not isinstance(self.capabilities, CapabilitySet):
            raise TypeError("StateDocument version/capabilities are invalid")
        if not isinstance(self.project_state, ProjectState):
            raise TypeError("project_state must be ProjectState")
        if self.run_id != self.project_state.run_id:
            raise ValueError("StateDocument run_id must match ProjectState")
        if self.version.state_schema_version != self.project_state.state_schema_version:
            raise ValueError("state schema versions must match")


@dataclass(frozen=True, slots=True)
class OutputArtifact:
    path: str
    role: ArtifactRole
    sha256: str
    size_bytes: int
    content_type: str
    required: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_relative_posix_path(self.path))
        if not isinstance(self.role, ArtifactRole):
            raise TypeError("role must be ArtifactRole")
        validate_sha256(self.sha256)
        require_non_negative_int(self.size_bytes, "size_bytes")
        _require_string(self.content_type, "content_type")
        if not isinstance(self.required, bool):
            raise TypeError("required must be bool")


@dataclass(frozen=True, slots=True)
class PublicManifest:
    version: VersionInfo
    generated_at_utc: datetime
    run_id: str
    branch: str | None
    head_oid: str | None
    dirty: bool
    latest_checkpoint: str
    publication_manifest_sha256: str
    published_paths: tuple[str, ...]
    artifacts: tuple[OutputArtifact, ...]
    capabilities: CapabilitySet
    reader_compatibility: tuple[tuple[str, Any], ...]
    publication_status: str

    def __post_init__(self) -> None:
        if not isinstance(self.version, VersionInfo) or not isinstance(self.capabilities, CapabilitySet):
            raise TypeError("PublicManifest version/capabilities are invalid")
        validate_utc_datetime(self.generated_at_utc, "generated_at_utc")
        _require_string(self.run_id, "run_id")
        _require_string(self.branch, "branch", nullable=True)
        _validate_git_oid(self.head_oid)
        if not isinstance(self.dirty, bool):
            raise TypeError("dirty must be bool")
        checkpoint = normalize_relative_posix_path(self.latest_checkpoint, "latest_checkpoint")
        object.__setattr__(self, "latest_checkpoint", checkpoint)
        validate_sha256(self.publication_manifest_sha256, "publication_manifest_sha256")
        paths = tuple(sorted({normalize_relative_posix_path(item, "published_paths") for item in self.published_paths}))
        if len(paths) != len(self.published_paths):
            raise ValueError("published_paths must be unique")
        object.__setattr__(self, "published_paths", paths)
        if checkpoint not in paths or ".ai/MANIFEST.json" not in paths:
            raise ValueError("manifest must reference itself and the latest checkpoint")
        artifact_paths = tuple(item.path for item in self.artifacts)
        if len(set(artifact_paths)) != len(artifact_paths):
            raise ValueError("artifacts must have unique paths")
        if ".ai/MANIFEST.json" in artifact_paths or not set(artifact_paths).issubset(paths):
            raise ValueError("artifact paths violate public manifest rules")
        if self.publication_status != "complete":
            raise ValueError("public manifest status must be complete")

    @property
    def engine_version(self) -> str:
        return self.version.engine_version

    @property
    def state_schema_version(self) -> int:
        return self.version.state_schema_version

    @property
    def manifest_schema_version(self) -> int:
        return self.version.manifest_schema_version


@dataclass(frozen=True, slots=True)
class PublicationResult:
    version: VersionInfo
    capabilities: CapabilitySet
    run_id: str
    status: PublicationStatus
    started_at: datetime | None
    completed_at: datetime | None
    published_paths: tuple[str, ...]
    unchanged_paths: tuple[str, ...]
    rolled_back_paths: tuple[str, ...]
    manifest_path: str | None
    manifest_self_digest: str | None
    manifest_file_sha256: str | None
    error_code: str | None

    def __post_init__(self) -> None:
        _require_string(self.run_id, "run_id")
        if not isinstance(self.version, VersionInfo) or not isinstance(self.capabilities, CapabilitySet):
            raise TypeError("PublicationResult version/capabilities are invalid")
        if not isinstance(self.status, PublicationStatus):
            raise TypeError("status must be PublicationStatus")
        if self.started_at is not None:
            validate_utc_datetime(self.started_at, "started_at")
        if self.completed_at is not None:
            validate_utc_datetime(self.completed_at, "completed_at")
            if self.started_at is None or self.completed_at < self.started_at:
                raise ValueError("completed_at requires a valid started_at")
        for name in ("published_paths", "unchanged_paths", "rolled_back_paths"):
            values = tuple(sorted({normalize_relative_posix_path(item, name) for item in getattr(self, name)}))
            object.__setattr__(self, name, values)
        if self.manifest_path is not None:
            object.__setattr__(self, "manifest_path", normalize_relative_posix_path(self.manifest_path, "manifest_path"))
        if self.manifest_self_digest is not None:
            validate_sha256(self.manifest_self_digest, "manifest_self_digest")
        if self.manifest_file_sha256 is not None:
            validate_sha256(self.manifest_file_sha256, "manifest_file_sha256")
        _require_string(self.error_code, "error_code", nullable=True)


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    checkpoint_schema_version: int
    version: VersionInfo
    capabilities: CapabilitySet
    checkpoint_id: str
    created_at: datetime
    run_id: str
    branch: str | None
    head_oid: str | None
    is_dirty: bool
    working_tree_summary: tuple[str, ...]
    test_evidence: tuple[TestEvidence, ...]
    remaining_work: tuple[str, ...]
    blockers: tuple[str, ...]
    next_action: str | None
    state_sha256: str
    artifact_set_sha256: str
    commit_claim_verified: bool

    def __post_init__(self) -> None:
        require_positive_int(self.checkpoint_schema_version, "checkpoint_schema_version")
        if not isinstance(self.version, VersionInfo) or not isinstance(self.capabilities, CapabilitySet):
            raise TypeError("CheckpointRecord version/capabilities are invalid")
        _require_string(self.checkpoint_id, "checkpoint_id")
        validate_utc_datetime(self.created_at, "created_at")
        _require_string(self.run_id, "run_id")
        _require_string(self.branch, "branch", nullable=True)
        _validate_git_oid(self.head_oid)
        if not isinstance(self.is_dirty, bool) or not isinstance(self.commit_claim_verified, bool):
            raise TypeError("checkpoint flags must be bool")
        for name in ("working_tree_summary", "remaining_work", "blockers"):
            object.__setattr__(self, name, _string_tuple(getattr(self, name), name))
        if any(not isinstance(item, TestEvidence) for item in self.test_evidence):
            raise TypeError("test_evidence must contain TestEvidence")
        _require_string(self.next_action, "next_action", nullable=True)
        validate_sha256(self.state_sha256, "state_sha256")
        validate_sha256(self.artifact_set_sha256, "artifact_set_sha256")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    severity: ValidationSeverity
    component: str
    field: str | None
    path: str | None
    message: str
    details: tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or _ERROR_CODE_RE.fullmatch(self.code) is None:
            raise ValueError("code must be stable uppercase snake case")
        if not isinstance(self.severity, ValidationSeverity):
            raise TypeError("severity must be ValidationSeverity")
        _require_string(self.component, "component")
        _require_string(self.field, "field", nullable=True)
        _require_string(self.path, "path", nullable=True)
        _require_string(self.message, "message")
        if any(not isinstance(key, str) for key, _value in self.details):
            raise TypeError("details keys must be strings")
        object.__setattr__(self, "details", tuple(sorted(self.details, key=lambda item: item[0])))

    def sort_key(self) -> tuple[object, ...]:
        rank = {
            ValidationSeverity.FATAL: 0,
            ValidationSeverity.ERROR: 1,
            ValidationSeverity.WARNING: 2,
            ValidationSeverity.INFO: 3,
        }
        return (rank[self.severity], self.code, self.component, self.field or "", self.path or "", self.message)


@dataclass(frozen=True, slots=True)
class SyncResult:
    version: VersionInfo
    capabilities: CapabilitySet
    run_id: str
    success: bool
    exit_code: int
    state: ProjectState | None
    state_document: StateDocument | None
    issues: tuple[ValidationIssue, ...]
    publication: PublicationResult
    planned_paths: tuple[str, ...]
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.version, VersionInfo) or not isinstance(self.capabilities, CapabilitySet):
            raise TypeError("SyncResult version/capabilities are invalid")
        _require_string(self.run_id, "run_id")
        if not isinstance(self.success, bool):
            raise TypeError("success must be bool")
        require_non_negative_int(self.exit_code, "exit_code")
        if self.state is not None and self.state.run_id != self.run_id:
            raise ValueError("state run_id must match SyncResult")
        if self.state_document is not None and self.state_document.run_id != self.run_id:
            raise ValueError("state document run_id must match SyncResult")
        if any(not isinstance(item, ValidationIssue) for item in self.issues):
            raise TypeError("issues must contain ValidationIssue")
        object.__setattr__(self, "issues", tuple(sorted(self.issues, key=ValidationIssue.sort_key)))
        object.__setattr__(
            self,
            "planned_paths",
            tuple(sorted({normalize_relative_posix_path(item, "planned_paths") for item in self.planned_paths})),
        )
        _require_string(self.message, "message")
