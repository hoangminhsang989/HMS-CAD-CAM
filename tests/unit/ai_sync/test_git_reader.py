"""Focused unit tests for the WP2 read-only Git runner and parsers."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import subprocess

import pytest

from tools.ai_sync.git_reader import (
    CommitVerificationStatus,
    GitCommandError,
    GitCommandRejectedError,
    GitCommandResult,
    GitDecodeError,
    GitExecutableMissingError,
    GitRunner,
    GitTimeoutError,
    InconsistentBranchMetadataError,
    InvalidOidError,
    MalformedNumstatError,
    MalformedPorcelainError,
    NotGitRepositoryError,
    RemoteRedactionError,
    capture_git_snapshot,
    fingerprint_git_snapshot,
    parse_numstat_z,
    parse_porcelain_v2_z,
    redact_remote_url,
    resolve_repository_root,
    validate_git_arguments,
    verify_commit,
)
from tools.ai_sync.models import (
    DiffScope,
    DiffStatEntry,
    DiffSummary,
    GitSnapshot,
    WorkingTreeEntry,
    WorkingTreeKind,
)


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
SHA = "c" * 64
NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _z(*records: str) -> bytes:
    return ("\x00".join(records) + "\x00").encode("utf-8")


def _headers(*extra: str, oid: str = HEAD, head: str = "main") -> tuple[str, ...]:
    return (f"# branch.oid {oid}", f"# branch.head {head}", *extra)


def _ordinary(path: str, xy: str = ".M", submodule: str = "N...") -> str:
    return f"1 {xy} {submodule} 100644 100644 100644 {HEAD} {HEAD} {path}"


def _rename(path: str, *, original: str, score: str = "R100", xy: str = "R.") -> tuple[str, str]:
    return (
        f"2 {xy} N... 100644 100644 100644 {HEAD} {HEAD} {score} {path}",
        original,
    )


class SequenceRunner:
    def __init__(self, results: list[GitCommandResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def run(self, arguments: tuple[str, ...], *, cwd: Path) -> GitCommandResult:
        validate_git_arguments(arguments)
        self.calls.append((arguments, Path(cwd)))
        if not self.results:
            raise AssertionError("unexpected Git command")
        return self.results.pop(0)


def _result(arguments: tuple[str, ...], stdout: bytes = b"", returncode: int = 0, stderr: bytes = b"") -> GitCommandResult:
    return GitCommandResult(arguments, returncode, stdout, stderr)


@pytest.mark.parametrize(
    "arguments",
    (
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "--verify", "HEAD"),
        ("status", "--porcelain=v2", "-z", "--branch"),
        ("diff", "--numstat", "-z"),
        ("diff", "--cached", "--numstat", "-z"),
        ("remote", "get-url", "origin"),
        ("cat-file", "-e", f"{HEAD}^{{commit}}"),
    ),
)
def test_exact_read_only_command_allowlist(arguments: tuple[str, ...]) -> None:
    assert validate_git_arguments(arguments) == arguments


@pytest.mark.parametrize(
    "arguments",
    (
        ("add", "."),
        ("commit", "-m", "x"),
        ("push",),
        ("fetch",),
        ("pull",),
        ("merge", "main"),
        ("rebase", "main"),
        ("stash",),
        ("reset", "--hard"),
        ("clean", "-fd"),
        ("checkout", "--", "x"),
        ("switch", "main"),
        ("restore", "x"),
        ("update-index", "--refresh"),
        ("read-tree", "HEAD"),
        ("write-tree",),
        ("config", "user.name", "x"),
        ("tag", "x"),
        ("branch", "new"),
        ("remote", "add", "origin"),
        ("worktree", "add", "x"),
        ("status", "--short"),
    ),
)
def test_every_mutating_or_unapproved_command_is_rejected(arguments: tuple[str, ...]) -> None:
    with pytest.raises(GitCommandRejectedError):
        validate_git_arguments(arguments)


@pytest.mark.parametrize(
    "arguments",
    (("remote", "get-url", "--all"), ("remote", "get-url", "bad name"), ("cat-file", "-e", "--help")),
)
def test_option_injection_is_rejected_before_subprocess(arguments: tuple[str, ...]) -> None:
    with pytest.raises((GitCommandRejectedError, InvalidOidError)):
        validate_git_arguments(arguments)


def test_runner_uses_argument_list_bytes_devnull_and_shell_false(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = GitRunner(2.5).run(("rev-parse", "--show-toplevel"), cwd=tmp_path)

    assert captured["command"] == ("git", "rev-parse", "--show-toplevel")
    assert captured["shell"] is False
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.PIPE and captured["stderr"] is subprocess.PIPE
    assert captured["timeout"] == 2.5
    assert captured["cwd"] == tmp_path.resolve()
    assert result.stdout == b"ok"


def test_runner_timeout_and_missing_executable_are_typed(tmp_path: Path, monkeypatch) -> None:
    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["git"], 1)

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(GitTimeoutError):
        GitRunner().run(("rev-parse", "--show-toplevel"), cwd=tmp_path)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(GitExecutableMissingError):
        GitRunner().run(("rev-parse", "--show-toplevel"), cwd=tmp_path)


def test_resolve_non_repository_and_missing_path_are_typed(tmp_path: Path) -> None:
    runner = SequenceRunner([_result(("rev-parse", "--show-toplevel"), returncode=128)])
    with pytest.raises(NotGitRepositoryError):
        resolve_repository_root(tmp_path, runner=runner)  # type: ignore[arg-type]
    with pytest.raises(Exception) as failure:
        resolve_repository_root(tmp_path / "missing")
    assert getattr(failure.value, "code", None).value == "git_path_invalid"


def test_invalid_utf8_fails_closed() -> None:
    with pytest.raises(GitDecodeError):
        parse_porcelain_v2_z(b"# branch.oid " + b"\xff\x00")
    with pytest.raises(GitDecodeError):
        parse_numstat_z(b"1\t0\t\xff\x00", DiffScope.UNSTAGED)


def test_porcelain_parses_ordinary_staged_unstaged_unicode_whitespace_and_submodule() -> None:
    path = "thư mục/tab\tline\n--gần-option.py"
    result = parse_porcelain_v2_z(
        _z(*_headers(), _ordinary(path, "MM", "S.MU"), "? chưa theo dõi.txt", "! bỏ qua.log")
    )
    assert result.branch == "main" and result.head_oid == HEAD
    assert [entry.path for entry in result.entries] == sorted(
        (path, "chưa theo dõi.txt", "bỏ qua.log"), key=lambda item: (item.casefold(), item)
    )
    ordinary = next(entry for entry in result.entries if entry.kind is WorkingTreeKind.ORDINARY)
    assert ordinary.index_status == "M" and ordinary.worktree_status == "M"
    assert ordinary.submodule_state == "S.MU" and ordinary.is_staged
    assert {entry.kind for entry in result.entries} >= {WorkingTreeKind.UNTRACKED, WorkingTreeKind.IGNORED}


def test_porcelain_canonicalizes_collapsed_directory_markers() -> None:
    result = parse_porcelain_v2_z(_z(*_headers(), "? .ai/", "! cache/"))
    assert [(entry.path, entry.kind) for entry in result.entries] == [
        (".ai", WorkingTreeKind.UNTRACKED),
        ("cache", WorkingTreeKind.IGNORED),
    ]


def test_porcelain_parses_rename_copy_and_original_paths() -> None:
    rename, old = _rename("new name.py", original="old name.py")
    copy, source = _rename("copy.py", original="source.py", score="C075", xy="C.")
    result = parse_porcelain_v2_z(_z(*_headers(), rename, old, copy, source))
    renamed = next(entry for entry in result.entries if entry.kind is WorkingTreeKind.RENAMED)
    copied = next(entry for entry in result.entries if entry.kind is WorkingTreeKind.COPIED)
    assert (renamed.path, renamed.original_path) == ("new name.py", "old name.py")
    assert (copied.path, copied.original_path) == ("copy.py", "source.py")


def test_porcelain_parses_unmerged_record() -> None:
    record = f"u UU N... 100644 100644 100644 100644 {HEAD} {OTHER_HEAD} {HEAD} conflict.txt"
    result = parse_porcelain_v2_z(_z(*_headers(), record))
    assert result.entries[0].kind is WorkingTreeKind.UNMERGED
    assert result.entries[0].index_status == "U" and result.entries[0].worktree_status == "U"


def test_porcelain_parses_detached_unborn_and_upstream() -> None:
    detached = parse_porcelain_v2_z(_z(*_headers(head="(detached)")))
    assert detached.is_detached and detached.branch is None and detached.head_oid == HEAD

    unborn = parse_porcelain_v2_z(_z(*_headers(oid="(initial)", head="new-branch")))
    assert unborn.unborn_branch == "new-branch" and unborn.head_oid is None and not unborn.is_detached

    tracked = parse_porcelain_v2_z(
        _z(*_headers("# branch.upstream origin/main", "# branch.ab +12 -3"))
    )
    assert tracked.upstream == "origin/main" and tracked.ahead == 12 and tracked.behind == 3


@pytest.mark.parametrize(
    "payload",
    (
        b"not-nul-terminated",
        _z("# branch.head main"),
        _z(f"# branch.oid {HEAD}", "# branch.head main", "# branch.ab +1 -0"),
        _z(*_headers(), "1 bad"),
        _z(*_headers(), f"2 R. N... 100644 100644 100644 {HEAD} {HEAD} R100 new.py"),
        _z(*_headers(), "unknown record"),
    ),
)
def test_malformed_porcelain_and_inconsistent_branch_metadata_are_rejected(payload: bytes) -> None:
    with pytest.raises((MalformedPorcelainError, InconsistentBranchMetadataError)):
        parse_porcelain_v2_z(payload)


def test_numstat_text_binary_and_rename_copy_forms() -> None:
    payload = _z("3\t2\ttext.py", "-\t-\tmodel.bin", "1\t0\t", "old.py", "new.py")
    summary = parse_numstat_z(payload, DiffScope.UNSTAGED)
    assert summary.files_changed == 3
    assert summary.binary_files == 1
    assert summary.insertions is None and summary.deletions is None
    binary = next(entry for entry in summary.entries if entry.path == "model.bin")
    renamed = next(entry for entry in summary.entries if entry.path == "new.py")
    assert binary.binary and binary.insertions is None
    assert renamed.original_path == "old.py" and (renamed.insertions, renamed.deletions) == (1, 0)


def test_numstat_aggregate_and_deterministic_ordering() -> None:
    summary = parse_numstat_z(_z("2\t1\tz.py", "3\t4\tA.py"), DiffScope.STAGED)
    assert (summary.insertions, summary.deletions, summary.files_changed) == (5, 5, 2)
    assert [entry.path for entry in summary.entries] == ["A.py", "z.py"]


@pytest.mark.parametrize(
    "payload",
    (b"1\t0\tpath", _z("x\t0\tpath"), _z("-\t0\tpath"), _z("1\t0\t"), _z("1\t0\tA.py", "2\t0\ta.py")),
)
def test_malformed_or_duplicate_numstat_is_rejected(payload: bytes) -> None:
    with pytest.raises(MalformedNumstatError):
        parse_numstat_z(payload, DiffScope.UNSTAGED)


def _summary(scope: DiffScope, *, path: str = "file.py", original: str | None = None, count: int = 1) -> DiffSummary:
    entry = DiffStatEntry(path, original, count, 0, False)
    return DiffSummary(scope, 1, count, 0, 0, (entry,))


def _snapshot(**changes: object) -> GitSnapshot:
    entry = WorkingTreeEntry("file.py", ".", "M", WorkingTreeKind.ORDINARY)
    values: dict[str, object] = {
        "repository_root": Path("C:/repo"),
        "captured_at": NOW,
        "branch": "main",
        "is_detached": False,
        "head_oid": HEAD,
        "upstream": "origin/main",
        "ahead": 0,
        "behind": 0,
        "remote_urls": (("origin", "https://example.invalid/repo.git"),),
        "entries": (entry,),
        "staged_diff": DiffSummary(DiffScope.STAGED, 0, 0, 0, 0, ()),
        "unstaged_diff": _summary(DiffScope.UNSTAGED),
        "is_dirty": True,
        "fingerprint_sha256": SHA,
    }
    values.update(changes)
    return GitSnapshot(**values)


def test_fingerprint_is_deterministic_and_excludes_capture_time_and_root() -> None:
    first = _snapshot()
    second = replace(first, repository_root=Path("D:/clone"), captured_at=datetime(2027, 1, 1, tzinfo=UTC))
    assert fingerprint_git_snapshot(first) == fingerprint_git_snapshot(second)


def test_fingerprint_changes_for_every_authoritative_state_dimension() -> None:
    baseline = _snapshot()
    changed = (
        replace(baseline, head_oid=OTHER_HEAD),
        replace(baseline, branch="other"),
        replace(baseline, ahead=1),
        replace(
            baseline,
            entries=(WorkingTreeEntry("new.py", "?", "?", WorkingTreeKind.UNTRACKED),),
        ),
        replace(baseline, unstaged_diff=_summary(DiffScope.UNSTAGED, count=2)),
        replace(baseline, staged_diff=_summary(DiffScope.STAGED, path="new.py", original="old.py")),
    )
    original_fingerprint = fingerprint_git_snapshot(baseline)
    assert all(fingerprint_git_snapshot(item) != original_fingerprint for item in changed)


@pytest.mark.parametrize(
    "url",
    (
        "https://user:password@example.invalid/repo.git?token=abc&safe=1",
        "ssh://user@example.invalid/repo.git?signature=abc",
        "user@example.invalid:private/repo.git",
        "https://example.invalid/repo.git?x=ghp_abcdefghijklmnopqrstuvwxyz",
    ),
)
def test_remote_redaction_removes_userinfo_password_tokens_and_credential_fragments(url: str) -> None:
    sanitized = redact_remote_url(url)
    lowered = sanitized.casefold()
    assert "password" not in lowered and "user@" not in lowered and "abc" not in sanitized
    assert "ghp_" not in sanitized


@pytest.mark.parametrize("url", ("C:/private/repo.git", "c:\\private\\repo.git", "/home/private/repo.git", "file:///private/repo.git"))
def test_local_remote_paths_are_redacted(url: str) -> None:
    assert "private" not in redact_remote_url(url)


@pytest.mark.parametrize("url", ("https://host:bad/repo", "https://\ninvalid/repo", ""))
def test_unredactable_remote_is_typed_failure(url: str) -> None:
    with pytest.raises(RemoteRedactionError):
        redact_remote_url(url)


def test_capture_nonzero_status_is_typed_git_error(tmp_path: Path) -> None:
    root = str(tmp_path.resolve()).encode("utf-8") + b"\n"
    runner = SequenceRunner(
        [
            _result(("rev-parse", "--show-toplevel"), root),
            _result(("status", "--porcelain=v2", "-z", "--branch"), returncode=2),
        ]
    )
    with pytest.raises(GitCommandError):
        capture_git_snapshot(tmp_path, captured_at=NOW, runner=runner)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("cat_result", "status"),
    (
        (_result(("cat-file", "-e", f"{HEAD}^{{commit}}")), CommitVerificationStatus.VERIFIED),
        (
            _result(
                ("cat-file", "-e", f"{HEAD}^{{commit}}"),
                returncode=128,
                stderr=b"fatal: Not a valid object name",
            ),
            CommitVerificationStatus.NOT_FOUND,
        ),
    ),
)
def test_commit_verification_valid_and_missing(
    tmp_path: Path,
    cat_result: GitCommandResult,
    status: CommitVerificationStatus,
) -> None:
    root = str(tmp_path.resolve()).encode("utf-8") + b"\n"
    runner = SequenceRunner([_result(("rev-parse", "--show-toplevel"), root), cat_result])
    result = verify_commit(tmp_path, HEAD, runner=runner)  # type: ignore[arg-type]
    assert result.status is status and result.verified is (status is CommitVerificationStatus.VERIFIED)


def test_commit_verification_rejects_invalid_oid_and_execution_error(tmp_path: Path) -> None:
    with pytest.raises(InvalidOidError):
        verify_commit(tmp_path, "--help")
    root = str(tmp_path.resolve()).encode("utf-8") + b"\n"
    runner = SequenceRunner(
        [
            _result(("rev-parse", "--show-toplevel"), root),
            _result(("cat-file", "-e", f"{HEAD}^{{commit}}"), returncode=2, stderr=b"fatal: I/O failure"),
        ]
    )
    with pytest.raises(GitCommandError):
        verify_commit(tmp_path, HEAD, runner=runner)  # type: ignore[arg-type]
