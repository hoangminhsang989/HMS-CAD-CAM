"""Read-only Git runner, parsers, and snapshot capture for AI Sync WP2."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import (
    DiffScope,
    DiffStatEntry,
    DiffSummary,
    GitSnapshot,
    WorkingTreeEntry,
    WorkingTreeKind,
    validate_utc_datetime,
)


DEFAULT_GIT_TIMEOUT_SECONDS: Final[float] = 10.0
_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_BRANCH_AB_RE = re.compile(r"^\+(\d+) -(\d+)$")
_CREDENTIAL_FRAGMENT_RE = re.compile(
    r"(?i)(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"(?:token|password|secret|credential|api[_-]?key)=([^&\s]+))"
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "credential",
        "key",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
    }
)


class GitErrorCode(StrEnum):
    EXECUTABLE_MISSING = "git_executable_missing"
    NOT_REPOSITORY = "not_git_repository"
    TIMEOUT = "git_timeout"
    COMMAND_REJECTED = "git_command_rejected"
    NONZERO_EXIT = "git_nonzero_exit"
    PORCELAIN_MALFORMED = "git_porcelain_malformed"
    NUMSTAT_MALFORMED = "git_numstat_malformed"
    UTF8_INVALID = "git_utf8_invalid"
    OID_INVALID = "git_oid_invalid"
    REMOTE_REDACTION_FAILED = "git_remote_redaction_failed"
    BRANCH_METADATA_INCONSISTENT = "git_branch_metadata_inconsistent"
    PATH_INVALID = "git_path_invalid"


class GitReaderError(RuntimeError):
    """Base class carrying a stable code and sanitized message."""

    def __init__(self, code: GitErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class GitExecutableMissingError(GitReaderError):
    def __init__(self) -> None:
        super().__init__(GitErrorCode.EXECUTABLE_MISSING, "Git executable is unavailable")


class NotGitRepositoryError(GitReaderError):
    def __init__(self) -> None:
        super().__init__(GitErrorCode.NOT_REPOSITORY, "Path is not inside an accessible Git repository")


class GitTimeoutError(GitReaderError):
    def __init__(self) -> None:
        super().__init__(GitErrorCode.TIMEOUT, "Read-only Git command timed out")


class GitCommandRejectedError(GitReaderError):
    def __init__(self) -> None:
        super().__init__(GitErrorCode.COMMAND_REJECTED, "Git command is outside the read-only allowlist")


class GitCommandError(GitReaderError):
    def __init__(self) -> None:
        super().__init__(GitErrorCode.NONZERO_EXIT, "Read-only Git command returned a nonzero status")


class MalformedPorcelainError(GitReaderError):
    def __init__(self, message: str = "Git porcelain-v2 payload is malformed") -> None:
        super().__init__(GitErrorCode.PORCELAIN_MALFORMED, message)


class MalformedNumstatError(GitReaderError):
    def __init__(self, message: str = "Git numstat payload is malformed") -> None:
        super().__init__(GitErrorCode.NUMSTAT_MALFORMED, message)


class GitDecodeError(GitReaderError):
    def __init__(self) -> None:
        super().__init__(GitErrorCode.UTF8_INVALID, "Git output is not valid UTF-8")


class InvalidOidError(GitReaderError):
    def __init__(self) -> None:
        super().__init__(GitErrorCode.OID_INVALID, "Git object id is invalid")


class RemoteRedactionError(GitReaderError):
    def __init__(self) -> None:
        super().__init__(GitErrorCode.REMOTE_REDACTION_FAILED, "Remote URL could not be sanitized")


class InconsistentBranchMetadataError(GitReaderError):
    def __init__(self, message: str = "Git branch metadata is inconsistent") -> None:
        super().__init__(GitErrorCode.BRANCH_METADATA_INCONSISTENT, message)


class GitPathError(GitReaderError):
    def __init__(self) -> None:
        super().__init__(GitErrorCode.PATH_INVALID, "Git repository path is invalid or outside authority")


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    arguments: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.arguments, tuple) or any(
            not isinstance(item, str) or not item for item in self.arguments
        ):
            raise TypeError("arguments must be a non-empty string tuple")
        if isinstance(self.returncode, bool) or not isinstance(self.returncode, int):
            raise TypeError("returncode must be an integer")
        if not isinstance(self.stdout, bytes) or not isinstance(self.stderr, bytes):
            raise TypeError("Git command output must be bytes")


@dataclass(frozen=True, slots=True)
class StatusParseResult:
    branch: str | None
    is_detached: bool
    unborn_branch: str | None
    head_oid: str | None
    upstream: str | None
    ahead: int | None
    behind: int | None
    entries: tuple[WorkingTreeEntry, ...]


class CommitVerificationStatus(StrEnum):
    VERIFIED = "verified"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class CommitVerificationResult:
    oid: str
    status: CommitVerificationStatus

    @property
    def verified(self) -> bool:
        return self.status is CommitVerificationStatus.VERIFIED


def validate_git_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    """Reject every command not matching an exact V1.1 read-only form."""

    if not isinstance(arguments, tuple) or not arguments or any(
        not isinstance(item, str) or not item or "\x00" in item for item in arguments
    ):
        raise GitCommandRejectedError()
    exact = {
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "--verify", "HEAD"),
        ("status", "--porcelain=v2", "-z", "--branch"),
        ("diff", "--numstat", "-z"),
        ("diff", "--cached", "--numstat", "-z"),
    }
    if arguments in exact:
        return arguments
    if len(arguments) == 3 and arguments[:2] == ("remote", "get-url"):
        remote = arguments[2]
        if remote.startswith("-") or _REMOTE_NAME_RE.fullmatch(remote) is None:
            raise GitCommandRejectedError()
        return arguments
    if len(arguments) == 3 and arguments[:2] == ("cat-file", "-e"):
        expression = arguments[2]
        suffix = "^{commit}"
        if not expression.endswith(suffix):
            raise GitCommandRejectedError()
        oid = expression[: -len(suffix)]
        if _OID_RE.fullmatch(oid) is None:
            raise InvalidOidError()
        return arguments
    raise GitCommandRejectedError()


class GitRunner:
    """Execute only exact read-only Git commands without an interactive stdin."""

    __slots__ = ("timeout_seconds",)

    def __init__(self, timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be numeric")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = float(timeout_seconds)

    def run(self, arguments: tuple[str, ...], *, cwd: Path) -> GitCommandResult:
        checked_arguments = validate_git_arguments(arguments)
        try:
            canonical_cwd = Path(cwd).resolve(strict=True)
        except OSError as error:
            raise GitPathError() from error
        if not canonical_cwd.is_dir():
            raise GitPathError()
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
                "LC_ALL": "C.UTF-8",
            }
        )
        try:
            completed = subprocess.run(
                ("git", *checked_arguments),
                cwd=canonical_cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
                env=environment,
            )
        except FileNotFoundError as error:
            raise GitExecutableMissingError() from error
        except subprocess.TimeoutExpired as error:
            raise GitTimeoutError() from error
        return GitCommandResult(
            arguments=checked_arguments,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _require_success(result: GitCommandResult) -> GitCommandResult:
    if result.returncode != 0:
        raise GitCommandError()
    return result


def _decode_utf8(payload: bytes) -> str:
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise GitDecodeError() from error


def _nul_records(payload: bytes, *, porcelain: bool) -> list[str]:
    if not payload:
        return []
    if not payload.endswith(b"\x00"):
        if porcelain:
            raise MalformedPorcelainError("Git porcelain-v2 payload is not NUL-terminated")
        raise MalformedNumstatError("Git numstat payload is not NUL-terminated")
    return _decode_utf8(payload[:-1]).split("\x00")


def _validate_xy_and_submodule(xy: str, submodule: str) -> None:
    if len(xy) != 2 or len(submodule) != 4:
        raise MalformedPorcelainError()
    allowed = frozenset(".MADRCUT?!")
    if any(character not in allowed for character in xy):
        raise MalformedPorcelainError()


def _entry(
    *,
    path: str,
    xy: str,
    submodule: str | None,
    kind: WorkingTreeKind,
    original_path: str | None = None,
) -> WorkingTreeEntry:
    try:
        return WorkingTreeEntry(
            path=path,
            index_status=xy[0],
            worktree_status=xy[1],
            kind=kind,
            original_path=original_path,
            submodule_state=submodule,
        )
    except (TypeError, ValueError) as error:
        raise MalformedPorcelainError() from error


def parse_porcelain_v2_z(payload: bytes) -> StatusParseResult:
    """Parse NUL-delimited porcelain-v2 status without line-based path parsing."""

    records = _nul_records(payload, porcelain=True)
    headers: dict[str, str] = {}
    entries: list[WorkingTreeEntry] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if record.startswith("# "):
            body = record[2:]
            key, separator, value = body.partition(" ")
            if not separator or key in headers:
                raise MalformedPorcelainError()
            headers[key] = value
            continue
        if record.startswith("1 "):
            parts = record.split(" ", 8)
            if len(parts) != 9:
                raise MalformedPorcelainError()
            xy, submodule, path = parts[1], parts[2], parts[8]
            _validate_xy_and_submodule(xy, submodule)
            entries.append(
                _entry(path=path, xy=xy, submodule=submodule, kind=WorkingTreeKind.ORDINARY)
            )
            continue
        if record.startswith("2 "):
            parts = record.split(" ", 9)
            if len(parts) != 10 or index >= len(records):
                raise MalformedPorcelainError()
            xy, submodule, score, path = parts[1], parts[2], parts[8], parts[9]
            _validate_xy_and_submodule(xy, submodule)
            if len(score) < 2 or score[0] not in {"R", "C"} or not score[1:].isdigit():
                raise MalformedPorcelainError()
            original_path = records[index]
            index += 1
            kind = WorkingTreeKind.RENAMED if score[0] == "R" else WorkingTreeKind.COPIED
            entries.append(
                _entry(
                    path=path,
                    xy=xy,
                    submodule=submodule,
                    kind=kind,
                    original_path=original_path,
                )
            )
            continue
        if record.startswith("u "):
            parts = record.split(" ", 10)
            if len(parts) != 11:
                raise MalformedPorcelainError()
            xy, submodule, path = parts[1], parts[2], parts[10]
            _validate_xy_and_submodule(xy, submodule)
            entries.append(
                _entry(path=path, xy=xy, submodule=submodule, kind=WorkingTreeKind.UNMERGED)
            )
            continue
        if record.startswith("? "):
            # Git may collapse an untracked directory to a porcelain path with
            # one trailing slash unless ``--untracked-files=all`` is requested.
            # Preserve its identity as a canonical repository-relative path.
            path = record[2:].removesuffix("/")
            entries.append(
                _entry(path=path, xy="??", submodule=None, kind=WorkingTreeKind.UNTRACKED)
            )
            continue
        if record.startswith("! "):
            path = record[2:].removesuffix("/")
            entries.append(
                _entry(path=path, xy="!!", submodule=None, kind=WorkingTreeKind.IGNORED)
            )
            continue
        raise MalformedPorcelainError()

    if "branch.oid" not in headers or "branch.head" not in headers:
        raise InconsistentBranchMetadataError("Required branch metadata is missing")
    oid_value = headers["branch.oid"]
    head_value = headers["branch.head"]
    is_unborn = oid_value == "(initial)"
    is_detached = head_value == "(detached)"
    if is_unborn and is_detached:
        raise InconsistentBranchMetadataError()
    if is_unborn:
        if not head_value or head_value.startswith("("):
            raise InconsistentBranchMetadataError()
        branch = None
        unborn_branch = head_value
        head_oid = None
    elif is_detached:
        branch = None
        unborn_branch = None
        if _OID_RE.fullmatch(oid_value) is None:
            raise InconsistentBranchMetadataError()
        head_oid = oid_value
    else:
        if not head_value or head_value.startswith("(") or _OID_RE.fullmatch(oid_value) is None:
            raise InconsistentBranchMetadataError()
        branch = head_value
        unborn_branch = None
        head_oid = oid_value

    upstream = headers.get("branch.upstream")
    ab_value = headers.get("branch.ab")
    if upstream is None:
        if ab_value is not None:
            raise InconsistentBranchMetadataError()
        ahead = behind = None
    else:
        if is_detached or is_unborn or ab_value is None:
            raise InconsistentBranchMetadataError()
        match = _BRANCH_AB_RE.fullmatch(ab_value)
        if match is None:
            raise InconsistentBranchMetadataError()
        ahead, behind = int(match.group(1)), int(match.group(2))

    ordered = tuple(sorted(entries, key=lambda item: (item.path.casefold(), item.path)))
    if len({item.path.casefold() for item in ordered}) != len(ordered):
        raise MalformedPorcelainError("Git porcelain contains duplicate canonical paths")
    return StatusParseResult(
        branch=branch,
        is_detached=is_detached,
        unborn_branch=unborn_branch,
        head_oid=head_oid,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        entries=ordered,
    )


def _parse_numstat_count(value: str) -> int:
    if not value.isdigit():
        raise MalformedNumstatError()
    return int(value)


def parse_numstat_z(payload: bytes, scope: DiffScope) -> DiffSummary:
    """Parse text, binary, and NUL-framed rename/copy numstat entries."""

    if not isinstance(scope, DiffScope):
        raise TypeError("scope must be DiffScope")
    records = _nul_records(payload, porcelain=False)
    entries: list[DiffStatEntry] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        parts = record.split("\t", 2)
        if len(parts) != 3:
            raise MalformedNumstatError()
        inserted_raw, deleted_raw, path = parts
        original_path: str | None = None
        if not path:
            if index + 1 >= len(records):
                raise MalformedNumstatError("Rename/copy numstat record is incomplete")
            original_path = records[index]
            path = records[index + 1]
            index += 2
            if not original_path or not path:
                raise MalformedNumstatError()
        binary = inserted_raw == "-" and deleted_raw == "-"
        if binary:
            insertions = deletions = None
        else:
            if inserted_raw == "-" or deleted_raw == "-":
                raise MalformedNumstatError()
            insertions = _parse_numstat_count(inserted_raw)
            deletions = _parse_numstat_count(deleted_raw)
        try:
            entries.append(
                DiffStatEntry(
                    path=path,
                    original_path=original_path,
                    insertions=insertions,
                    deletions=deletions,
                    binary=binary,
                )
            )
        except (TypeError, ValueError) as error:
            raise MalformedNumstatError() from error
    ordered = tuple(sorted(entries, key=lambda item: (item.path.casefold(), item.path)))
    if len({entry.path.casefold() for entry in ordered}) != len(ordered):
        raise MalformedNumstatError("Git numstat contains duplicate canonical paths")
    binary_files = sum(entry.binary for entry in ordered)
    insertions_total = None if binary_files else sum(entry.insertions or 0 for entry in ordered)
    deletions_total = None if binary_files else sum(entry.deletions or 0 for entry in ordered)
    return DiffSummary(
        scope=scope,
        files_changed=len(ordered),
        insertions=insertions_total,
        deletions=deletions_total,
        binary_files=binary_files,
        entries=ordered,
    )


def redact_remote_url(value: str) -> str:
    """Remove local paths, userinfo, tokens, and sensitive query values."""

    if not isinstance(value, str) or not value or any(ord(character) < 32 for character in value):
        raise RemoteRedactionError()
    if value.startswith(("/", "\\")) or PureWindowsPath(value).drive:
        return "<redacted-local-path>"
    try:
        if "://" in value:
            parsed = urlsplit(value)
            if not parsed.scheme:
                raise RemoteRedactionError()
            if parsed.scheme.casefold() == "file":
                return "file://<redacted-local-path>"
            hostname = parsed.hostname
            if not hostname:
                raise RemoteRedactionError()
            netloc = hostname
            if ":" in hostname and not hostname.startswith("["):
                netloc = f"[{hostname}]"
            if parsed.port is not None:
                netloc = f"{netloc}:{parsed.port}"
            query = urlencode(
                [
                    (key, "<redacted>" if key.casefold() in _SENSITIVE_QUERY_KEYS else item)
                    for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                ],
                doseq=True,
            )
            fragment = parsed.fragment
            if any(key in fragment.casefold() for key in _SENSITIVE_QUERY_KEYS):
                fragment = "<redacted>"
            sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))
        elif "@" in value and ":" in value.rsplit("@", 1)[-1]:
            sanitized = value.rsplit("@", 1)[-1]
        else:
            sanitized = value
    except (TypeError, ValueError) as error:
        raise RemoteRedactionError() from error
    return _CREDENTIAL_FRAGMENT_RE.sub("<redacted>", sanitized)


def resolve_repository_root(
    candidate: Path,
    *,
    runner: GitRunner | None = None,
    authority_root: Path | None = None,
) -> Path:
    """Resolve a file/directory to its canonical Git root without changing process cwd."""

    try:
        canonical_candidate = Path(candidate).resolve(strict=True)
    except OSError as error:
        raise GitPathError() from error
    candidate_directory = canonical_candidate.parent if canonical_candidate.is_file() else canonical_candidate
    if not candidate_directory.is_dir():
        raise GitPathError()
    canonical_authority: Path | None = None
    if authority_root is not None:
        try:
            canonical_authority = Path(authority_root).resolve(strict=True)
            candidate_directory.relative_to(canonical_authority)
        except (OSError, ValueError) as error:
            raise GitPathError() from error
    active_runner = runner or GitRunner()
    result = active_runner.run(("rev-parse", "--show-toplevel"), cwd=candidate_directory)
    if result.returncode != 0:
        raise NotGitRepositoryError()
    root_text = _decode_utf8(result.stdout).strip("\r\n")
    if not root_text or "\x00" in root_text:
        raise NotGitRepositoryError()
    try:
        root = Path(root_text).resolve(strict=True)
        candidate_directory.relative_to(root)
        if canonical_authority is not None:
            root.relative_to(canonical_authority)
    except (OSError, ValueError) as error:
        raise GitPathError() from error
    if not root.is_dir():
        raise GitPathError()
    return root


def _remote_pairs(
    root: Path,
    *,
    runner: GitRunner,
    include_remote_urls: bool,
    remote_names: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    if not include_remote_urls:
        return ()
    if not isinstance(remote_names, tuple):
        raise TypeError("remote_names must be a tuple")
    pairs: list[tuple[str, str]] = []
    for name in sorted(set(remote_names)):
        result = runner.run(("remote", "get-url", name), cwd=root)
        if result.returncode != 0:
            stderr = _decode_utf8(result.stderr).strip("\r\n")
            if name == "origin" and result.returncode in {2, 128} and stderr == "error: No such remote 'origin'":
                continue
            raise GitCommandError()
        url = _decode_utf8(result.stdout).strip("\r\n")
        pairs.append((name, redact_remote_url(url)))
    return tuple(pairs)


def fingerprint_git_snapshot(snapshot: GitSnapshot) -> str:
    """Hash canonical state while excluding root, capture time, and the prior hash."""

    payload = {
        "branch": snapshot.branch,
        "unborn_branch": snapshot.unborn_branch,
        "is_detached": snapshot.is_detached,
        "head_oid": snapshot.head_oid,
        "upstream": snapshot.upstream,
        "ahead": snapshot.ahead,
        "behind": snapshot.behind,
        "remote_urls": list(snapshot.remote_urls),
        "entries": [
            {
                "path": entry.path,
                "original_path": entry.original_path,
                "index_status": entry.index_status,
                "worktree_status": entry.worktree_status,
                "kind": entry.kind.value,
                "submodule_state": entry.submodule_state,
            }
            for entry in snapshot.entries
        ],
        "staged_diff": _diff_fingerprint_payload(snapshot.staged_diff),
        "unstaged_diff": _diff_fingerprint_payload(snapshot.unstaged_diff),
        "is_dirty": snapshot.is_dirty,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _diff_fingerprint_payload(summary: DiffSummary) -> dict[str, object]:
    return {
        "scope": summary.scope.value,
        "files_changed": summary.files_changed,
        "insertions": summary.insertions,
        "deletions": summary.deletions,
        "binary_files": summary.binary_files,
        "entries": [
            {
                "path": entry.path,
                "original_path": entry.original_path,
                "insertions": entry.insertions,
                "deletions": entry.deletions,
                "binary": entry.binary,
            }
            for entry in summary.entries
        ],
    }


def capture_git_snapshot(
    repository: Path,
    *,
    captured_at: datetime,
    runner: GitRunner | None = None,
    authority_root: Path | None = None,
    include_remote_urls: bool = False,
    remote_names: tuple[str, ...] = ("origin",),
) -> GitSnapshot:
    """Capture one normalized read-only Git snapshot from exact allowlisted commands."""

    validate_utc_datetime(captured_at, "captured_at")
    active_runner = runner or GitRunner()
    root = resolve_repository_root(
        repository,
        runner=active_runner,
        authority_root=authority_root,
    )
    status_result = _require_success(
        active_runner.run(("status", "--porcelain=v2", "-z", "--branch"), cwd=root)
    )
    status = parse_porcelain_v2_z(status_result.stdout)
    if status.head_oid is not None:
        head_result = _require_success(
            active_runner.run(("rev-parse", "--verify", "HEAD"), cwd=root)
        )
        verified_head = _decode_utf8(head_result.stdout).strip("\r\n")
        if _OID_RE.fullmatch(verified_head) is None or verified_head != status.head_oid:
            raise InconsistentBranchMetadataError("HEAD changed or disagrees with porcelain metadata")
    staged = parse_numstat_z(
        _require_success(
            active_runner.run(("diff", "--cached", "--numstat", "-z"), cwd=root)
        ).stdout,
        DiffScope.STAGED,
    )
    unstaged = parse_numstat_z(
        _require_success(active_runner.run(("diff", "--numstat", "-z"), cwd=root)).stdout,
        DiffScope.UNSTAGED,
    )
    remotes = _remote_pairs(
        root,
        runner=active_runner,
        include_remote_urls=include_remote_urls,
        remote_names=remote_names,
    )
    snapshot = GitSnapshot(
        repository_root=root,
        captured_at=captured_at,
        branch=status.branch,
        is_detached=status.is_detached,
        head_oid=status.head_oid,
        upstream=status.upstream,
        ahead=status.ahead,
        behind=status.behind,
        remote_urls=remotes,
        entries=status.entries,
        staged_diff=staged,
        unstaged_diff=unstaged,
        is_dirty=any(entry.kind is not WorkingTreeKind.IGNORED for entry in status.entries),
        fingerprint_sha256="0" * 64,
        unborn_branch=status.unborn_branch,
    )
    return replace(snapshot, fingerprint_sha256=fingerprint_git_snapshot(snapshot))


def verify_commit(
    repository: Path,
    oid: str,
    *,
    runner: GitRunner | None = None,
    authority_root: Path | None = None,
) -> CommitVerificationResult:
    """Verify a full OID as a commit without treating timeouts/errors as absence."""

    if not isinstance(oid, str) or _OID_RE.fullmatch(oid) is None:
        raise InvalidOidError()
    active_runner = runner or GitRunner()
    root = resolve_repository_root(
        repository,
        runner=active_runner,
        authority_root=authority_root,
    )
    result = active_runner.run(("cat-file", "-e", f"{oid}^{{commit}}"), cwd=root)
    if result.returncode == 0:
        return CommitVerificationResult(oid, CommitVerificationStatus.VERIFIED)
    stderr = _decode_utf8(result.stderr).casefold()
    missing_markers = (
        "not a valid object name",
        "not a valid object",
        "bad object",
        "invalid object",
        "could not get object info",
    )
    if result.returncode in {1, 128} and any(marker in stderr for marker in missing_markers):
        return CommitVerificationResult(oid, CommitVerificationStatus.NOT_FOUND)
    raise GitCommandError()
