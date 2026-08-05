"""Integration certification for WP2 using isolated temporary Git repositories."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from tools.ai_sync.git_reader import (
    CommitVerificationStatus,
    capture_git_snapshot,
    verify_commit,
)
from tools.ai_sync.models import WorkingTreeKind


NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _git(repository: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "AI Sync Test",
            "GIT_AUTHOR_EMAIL": "ai-sync@example.invalid",
            "GIT_COMMITTER_NAME": "AI Sync Test",
            "GIT_COMMITTER_EMAIL": "ai-sync@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    result = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        env=environment,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"fixture Git command failed: {arguments!r}, code={result.returncode}, "
            f"stderr={result.stderr.decode('utf-8', errors='replace')!r}"
        )
    return result


def _init_repository(path: Path, *, commit: bool = True) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    if commit:
        (path / "tracked.txt").write_text("initial\n", encoding="utf-8", newline="\n")
        _git(path, "add", "tracked.txt")
        _git(path, "commit", "-m", "initial")
    return path


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _refs_identity(git_directory: Path) -> tuple[tuple[str, str], ...]:
    refs_root = git_directory / "refs"
    return tuple(
        sorted(
            (
                path.relative_to(git_directory).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in refs_root.rglob("*")
            if path.is_file()
        )
    )


def _identity(repository: Path) -> tuple[object, ...]:
    git_directory = repository / ".git"
    head = _git(repository, "rev-parse", "--verify", "HEAD", check=False)
    status = _git(repository, "status", "--porcelain=v2", "-z", "--branch")
    return (
        head.returncode,
        head.stdout,
        _sha(git_directory / "HEAD"),
        _sha(git_directory / "index"),
        _sha(git_directory / "config"),
        _sha(git_directory / "packed-refs"),
        _refs_identity(git_directory),
        status.stdout,
    )


def _capture_without_mutation(repository: Path, **kwargs):
    before = _identity(repository)
    process_cwd = Path.cwd()
    snapshot = capture_git_snapshot(repository, captured_at=NOW, **kwargs)
    after = _identity(repository)
    assert after == before
    assert Path.cwd() == process_cwd
    return snapshot


def test_clean_repo_and_file_candidate_are_read_only(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "clean")
    before = _identity(repository)
    process_cwd = Path.cwd()
    snapshot = capture_git_snapshot(repository / "tracked.txt", captured_at=NOW)
    assert _identity(repository) == before
    assert Path.cwd() == process_cwd
    assert snapshot.repository_root == repository.resolve()
    assert snapshot.branch == "main" and snapshot.head_oid is not None
    assert not snapshot.is_dirty and snapshot.entries == ()
    assert snapshot.remote_urls == ()
    assert snapshot.fingerprint_sha256 != "0" * 64


@pytest.mark.parametrize("case", ("modified", "staged", "untracked", "deleted", "renamed"))
def test_working_tree_states_and_numstat_are_captured_without_mutation(tmp_path: Path, case: str) -> None:
    repository = _init_repository(tmp_path / case)
    tracked = repository / "tracked.txt"
    if case == "modified":
        tracked.write_text("changed\n", encoding="utf-8", newline="\n")
    elif case == "staged":
        tracked.write_text("staged\n", encoding="utf-8", newline="\n")
        _git(repository, "add", "tracked.txt")
    elif case == "untracked":
        (repository / "tệp mới.txt").write_text("new\n", encoding="utf-8", newline="\n")
    elif case == "deleted":
        tracked.unlink()
    else:
        _git(repository, "mv", "tracked.txt", "renamed name.txt")

    snapshot = _capture_without_mutation(repository)
    assert snapshot.is_dirty
    if case == "modified":
        assert snapshot.entries[0].worktree_status == "M"
        assert snapshot.unstaged_diff.files_changed == 1
    elif case == "staged":
        assert snapshot.entries[0].index_status == "M"
        assert snapshot.staged_diff.files_changed == 1
    elif case == "untracked":
        assert snapshot.entries[0].kind is WorkingTreeKind.UNTRACKED
        assert snapshot.entries[0].path == "tệp mới.txt"
    elif case == "deleted":
        assert snapshot.entries[0].worktree_status == "D"
        assert snapshot.unstaged_diff.files_changed == 1
    else:
        entry = snapshot.entries[0]
        assert entry.kind is WorkingTreeKind.RENAMED
        assert (entry.original_path, entry.path) == ("tracked.txt", "renamed name.txt")
        assert snapshot.staged_diff.entries[0].original_path == "tracked.txt"


def test_detached_head_is_captured_without_mutation(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "detached")
    _git(repository, "checkout", "--detach")
    snapshot = _capture_without_mutation(repository)
    assert snapshot.is_detached
    assert snapshot.branch is None and snapshot.unborn_branch is None
    assert snapshot.head_oid is not None


def test_unborn_branch_is_captured_without_requiring_head(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "unborn", commit=False)
    snapshot = _capture_without_mutation(repository)
    assert snapshot.unborn_branch == "main"
    assert snapshot.branch is None and snapshot.head_oid is None
    assert not snapshot.is_detached and not snapshot.is_dirty


def test_local_upstream_and_sanitized_remote_are_captured_without_mutation(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")
    repository = _init_repository(tmp_path / "upstream")
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "-u", "origin", "main")

    snapshot = _capture_without_mutation(
        repository,
        include_remote_urls=True,
        remote_names=("origin",),
    )
    assert snapshot.upstream == "origin/main"
    assert snapshot.ahead == 0 and snapshot.behind == 0
    assert snapshot.remote_urls == (("origin", "<redacted-local-path>"),)


def test_commit_verification_is_read_only_for_present_and_missing_oid(tmp_path: Path) -> None:
    repository = _init_repository(tmp_path / "verify")
    head = _git(repository, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    before = _identity(repository)
    present = verify_commit(repository, head)
    missing = verify_commit(repository, "f" * 40)
    after = _identity(repository)
    assert present.status is CommitVerificationStatus.VERIFIED
    assert missing.status is CommitVerificationStatus.NOT_FOUND
    assert after == before


@pytest.mark.parametrize(
    ("url", "expected"),
    (
        ("https://user:password@example.invalid/org/repo.git?token=abc&safe=1", "https://example.invalid/org/repo.git?<redacted>&safe=1"),
        ("ssh://user@example.invalid/org/repo.git", "ssh://example.invalid/org/repo.git"),
        ("git@example.invalid:org/repo.git", "example.invalid:org/repo.git"),
    ),
)
def test_fixed_origin_collection_sanitizes_supported_url_forms(
    tmp_path: Path,
    url: str,
    expected: str,
) -> None:
    repository = _init_repository(tmp_path / "remote-form")
    _git(repository, "remote", "add", "origin", url)
    snapshot = _capture_without_mutation(repository, include_remote_urls=True)
    assert snapshot.remote_urls == (("origin", expected),)


def test_missing_origin_is_no_remote_and_collection_can_be_disabled(tmp_path: Path) -> None:
    no_remote = _init_repository(tmp_path / "no-remote")
    assert _capture_without_mutation(no_remote, include_remote_urls=True).remote_urls == ()

    with_remote = _init_repository(tmp_path / "collection-disabled")
    _git(with_remote, "remote", "add", "origin", "https://example.invalid/org/repo.git")
    assert _capture_without_mutation(with_remote, include_remote_urls=False).remote_urls == ()
