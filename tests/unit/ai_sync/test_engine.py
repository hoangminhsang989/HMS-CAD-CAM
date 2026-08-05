"""WP6 engine orchestration and exit-policy tests."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.ai_sync.engine import (
    CONFIG_INVALID,
    GIT_READ_FAILED,
    NOT_GIT_REPOSITORY,
    PUBLICATION_FAILED,
    SAFETY_BOUNDARY_VIOLATION,
    SUCCESS,
    TEST_EVIDENCE_INVALID,
    VALIDATION_FAILED,
    EngineDependencies,
    execute,
)
from tools.ai_sync.git_reader import GitCommandError
from tools.ai_sync.publisher import PublisherError


NOW = datetime(2026, 8, 4, 13, 0, 0, tzinfo=UTC)


def make_engine_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    (root / ".ai").mkdir()
    source_config = Path(__file__).parents[3] / ".ai/config.json"
    shutil.copyfile(source_config, root / ".ai/config.json")
    (root / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
    commands = (
        ("git", "init", "-q", "-b", "main"),
        ("git", "add", "README.md", ".ai/config.json"),
        ("git", "-c", "user.name=AI Sync Test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "fixture"),
    )
    for command in commands:
        subprocess.run(command, cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    return root


def deps(*, publisher=None, recovery=None, log_sink=None, clock=None, run_id=None) -> EngineDependencies:
    kwargs = {
        "clock": clock or (lambda: NOW),
        "run_id": run_id or (lambda: "engine-run"),
        "log_sink": log_sink,
    }
    if publisher is not None:
        kwargs["publisher"] = publisher
    if recovery is not None:
        kwargs["recovery"] = recovery
    return EngineDependencies(**kwargs)


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file() and ".git" not in path.parts
    }


@pytest.mark.parametrize("command", ("inspect", "validate", "show-plan"))
def test_read_only_commands_are_zero_write(tmp_path: Path, command: str) -> None:
    root = make_engine_repo(tmp_path); before = tree_hashes(root)
    result = execute(command, root, dependencies=deps())
    assert result.exit_code == SUCCESS and result.payload["writes_performed"] is False
    if command == "inspect":
        assert "intended_outputs" not in result.payload
        assert "test_evidence" not in result.payload
        assert "project_state" not in result.payload
    else:
        assert result.payload["intended_outputs"]
    assert tree_hashes(root) == before
    assert not (root / ".ai/STATE.json").exists()


def test_show_plan_reports_candidate_hashes_and_capabilities(tmp_path: Path) -> None:
    result = execute("show-plan", make_engine_repo(tmp_path), dependencies=deps())
    assert result.exit_code == SUCCESS and len(result.payload["candidate_artifacts"]) == 8
    assert "journaled_publication" in result.payload["capabilities"]["supported"]
    assert len(result.payload["manifest_self_digest"]) == 64
    project_state = result.payload["project_state"]
    assert project_state["stage"] is None and project_state["status"] == "unknown"
    assert project_state["stage_progress_percent"] is None


def test_expected_head_mismatch_is_validation_failure(tmp_path: Path) -> None:
    result = execute("validate", make_engine_repo(tmp_path), expected_head="0" * 40, dependencies=deps())
    assert result.exit_code == VALIDATION_FAILED and result.payload["writes_performed"] is False


def test_missing_config_and_non_git_exit_codes(tmp_path: Path) -> None:
    empty = tmp_path / "empty"; empty.mkdir()
    assert execute("inspect", empty, dependencies=deps()).exit_code == NOT_GIT_REPOSITORY
    root = make_engine_repo(tmp_path)
    (root / ".ai/config.json").unlink()
    assert execute("inspect", root, dependencies=deps()).exit_code == CONFIG_INVALID


def test_invalid_test_evidence_has_dedicated_exit(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    (root / ".ai/TEST_RESULTS.json").write_bytes(b"{bad")
    result = execute("validate", root, dependencies=deps())
    assert result.exit_code == TEST_EVIDENCE_INVALID


def test_inspect_does_not_read_malformed_test_evidence(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    (root / ".ai/TEST_RESULTS.json").write_bytes(b"{malformed")
    result = execute("inspect", root, dependencies=deps())
    assert result.exit_code == SUCCESS
    assert "test_evidence" not in result.payload


def test_inspect_never_builds_state_renders_or_verifies_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.ai_sync.engine as module

    def forbidden(*args, **kwargs):
        raise AssertionError("inspect crossed the review boundary")

    for name in (
        "load_test_results",
        "_metadata",
        "verify_commit",
        "build_project_state",
        "render_output_candidates",
    ):
        monkeypatch.setattr(module, name, forbidden)
    result = execute("inspect", make_engine_repo(tmp_path), dependencies=deps())
    assert result.exit_code == SUCCESS
    assert "candidate_artifacts" not in result.payload


def test_inspect_reports_sanitized_fixed_origin(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    subprocess.run(
        (
            "git",
            "remote",
            "add",
            "origin",
            "https://user:password@example.invalid/org/repo.git",
        ),
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    result = execute("inspect", root, dependencies=deps())
    assert result.exit_code == SUCCESS
    assert result.payload["git"]["remote_urls"] == [
        {"name": "origin", "url": "https://example.invalid/org/repo.git"}
    ]


def test_git_read_failure_has_dedicated_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.ai_sync.engine as module

    def fail(*args, **kwargs):
        raise GitCommandError()

    monkeypatch.setattr(module, "capture_git_snapshot", fail)
    assert execute("inspect", make_engine_repo(tmp_path), dependencies=deps()).exit_code == GIT_READ_FAILED


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (PublisherError("PUBLICATION_FAILED", "failed"), PUBLICATION_FAILED),
        (PublisherError("SAFETY_PATH_ESCAPE", "unsafe"), SAFETY_BOUNDARY_VIOLATION),
        (PublisherError("PUBLICATION_RECOVERY_REQUIRED", "recover", recovery_required=True), SAFETY_BOUNDARY_VIOLATION),
    ),
)
def test_publisher_failures_map_to_stable_exit_codes(tmp_path: Path, error: Exception, expected: int) -> None:
    def fail(*args, **kwargs):
        raise error

    result = execute("sync", make_engine_repo(tmp_path), dependencies=deps(publisher=fail))
    assert result.exit_code == expected and result.payload["writes_performed"] is False


def test_keyboard_interrupt_is_safety_exit(tmp_path: Path) -> None:
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    result = execute("sync", make_engine_repo(tmp_path), dependencies=deps(publisher=interrupt))
    assert result.exit_code == SAFETY_BOUNDARY_VIOLATION


def test_structured_log_is_json_and_secret_redacted(tmp_path: Path) -> None:
    records: list[str] = []
    result = execute("inspect", make_engine_repo(tmp_path), dependencies=deps(log_sink=records.append))
    assert result.exit_code == SUCCESS and len(records) == 1
    assert "engine_inspected" in records[0] and "password=" not in records[0].casefold()


def test_unsupported_engine_command_is_cli_error(tmp_path: Path) -> None:
    result = execute("run-tests", make_engine_repo(tmp_path), dependencies=deps())
    assert result.exit_code == 2
