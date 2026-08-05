"""WP6 engine orchestration and exit-policy tests."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.ai_sync.engine import (
    CLI_ERROR,
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



def _write_metadata(path: Path, *, current_task: str = "External authority") -> bytes:
    payload = {
        "schema_version": 1,
        "project": "HMS CAD/CAM",
        "stage": "Stage 13C",
        "status": "blocked",
        "current_task": current_task,
        "remaining_work": ["Obtain separate authority"],
        "blockers": ["Immutable reconciliation remains blocked"],
        "blockers_state": "present",
        "next_action": "Review handoff",
        "stage_progress_percent": None,
        "overall_progress_percent": None,
        "provenance": {"authority": "external"},
    }
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(data)
    return data


def _git_control_hashes(root: Path) -> dict[str, str]:
    paths = [root / ".git/HEAD", root / ".git/index", root / ".git/config"]
    paths.extend(path for path in (root / ".git/refs").rglob("*") if path.is_file())
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


@pytest.mark.parametrize("command", ("validate", "show-plan"))
def test_external_metadata_dry_commands_are_bound_and_zero_write(tmp_path: Path, command: str) -> None:
    root = make_engine_repo(tmp_path)
    external = tmp_path / "authority.json"
    data = _write_metadata(external)
    before_tree = tree_hashes(root)
    before_git = _git_control_hashes(root)

    result = execute(command, root, metadata_path=external, dependencies=deps())

    assert result.exit_code == SUCCESS
    assert result.payload["writes_performed"] is False
    assert result.payload["metadata_present"] is True
    assert result.payload["metadata_mode"] == "external_file"
    assert result.payload["metadata_sha256"] == hashlib.sha256(data).hexdigest()
    assert result.payload["project_state"]["stage"] == "Stage 13C"
    assert result.payload["project_state"]["status"] == "blocked"
    assert result.payload["project_state"]["blockers_state"] == "present"
    assert str(external) not in json.dumps(result.payload)
    assert tree_hashes(root) == before_tree
    assert _git_control_hashes(root) == before_git
    assert not (root / ".ai/STATE.json").exists()


def test_external_metadata_sync_is_bound_and_never_publishes_input_path(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    external = tmp_path / "authority.json"
    data = _write_metadata(external)
    digest = hashlib.sha256(data).hexdigest()
    records: list[str] = []

    result = execute(
        "sync", root, metadata_path=external, expected_metadata_sha256=digest,
        dependencies=deps(log_sink=records.append),
    )

    assert result.exit_code == SUCCESS and result.payload["writes_performed"] is True
    assert result.payload["metadata_sha256"] == digest
    assert result.payload["metadata_mode"] == "external_file"
    assert str(external) not in json.dumps(result.payload)
    assert all(str(external) not in line for line in records)
    for artifact in result.payload["publication"]["published_paths"]:
        assert str(external) not in (root / artifact).read_text(encoding="utf-8")


def test_repository_relative_metadata_remains_supported(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    data = _write_metadata(root / "metadata.json")

    result = execute("validate", root, metadata_path=Path("metadata.json"), dependencies=deps())

    assert result.exit_code == SUCCESS
    assert result.payload["metadata_mode"] == "repository_file"
    assert result.payload["metadata_sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("command", ("validate", "show-plan", "sync"))
def test_metadata_file_conflicts_with_inline_metadata_for_all_supported_commands(
    tmp_path: Path,
    command: str,
) -> None:
    root = make_engine_repo(tmp_path)
    external = tmp_path / "authority.json"
    _write_metadata(external)

    result = execute(command, root, metadata_path=external, stage="Stage 13C", dependencies=deps())

    assert result.exit_code == VALIDATION_FAILED
    assert result.payload["writes_performed"] is False


def test_inline_metadata_is_hashed_and_inspect_rejects_metadata_arguments(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    validate = execute("validate", root, stage="Stage 13C", task="Inline authority", dependencies=deps())
    plan = execute("show-plan", root, stage="Stage 13C", task="Inline authority", dependencies=deps())
    rejected = execute("inspect", root, stage="Stage 13C", dependencies=deps())

    assert validate.exit_code == plan.exit_code == SUCCESS
    assert validate.payload["metadata_mode"] == plan.payload["metadata_mode"] == "inline"
    assert validate.payload["metadata_sha256"] == plan.payload["metadata_sha256"]
    assert rejected.exit_code == CLI_ERROR


@pytest.mark.parametrize(
    "kind",
    ("missing", "directory", "malformed_utf8", "bom", "duplicate_key", "oversized"),
)
def test_unsafe_or_invalid_external_metadata_is_rejected_without_path_disclosure(
    tmp_path: Path,
    kind: str,
) -> None:
    root = make_engine_repo(tmp_path)
    external = tmp_path / f"{kind}.json"
    if kind == "directory":
        external.mkdir()
    elif kind == "malformed_utf8":
        external.write_bytes(b"{\xff}")
    elif kind == "bom":
        external.write_bytes(b"\xef\xbb\xbf{}")
    elif kind == "duplicate_key":
        external.write_bytes(b'{"project":"HMS CAD/CAM","project":"HMS CAD/CAM"}')
    elif kind == "oversized":
        external.write_bytes(b" " * (1024 * 1024 + 1))

    result = execute("validate", root, metadata_path=external, dependencies=deps())

    assert result.exit_code == VALIDATION_FAILED
    assert result.payload["writes_performed"] is False
    assert str(external) not in json.dumps(result.payload)
    assert not (root / ".ai/STATE.json").exists()


def test_reparse_and_unsafe_device_metadata_paths_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.ai_sync.engine as module

    root = make_engine_repo(tmp_path)
    external = tmp_path / "authority.json"
    _write_metadata(external)
    monkeypatch.setattr(module, "_has_reparse_ancestor", lambda _path: True)

    reparse = execute("validate", root, metadata_path=external, dependencies=deps())
    monkeypatch.undo()
    device = execute("validate", root, metadata_path=Path(r"\\.\NUL"), dependencies=deps())

    assert reparse.exit_code == device.exit_code == VALIDATION_FAILED
    assert str(external) not in json.dumps(reparse.payload)


def test_expected_metadata_sha_binds_plan_to_sync_and_detects_change(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    external = tmp_path / "authority.json"
    original = _write_metadata(external, current_task="Original authority")
    original_digest = hashlib.sha256(original).hexdigest()

    validate = execute(
        "validate", root, metadata_path=external, expected_metadata_sha256=original_digest, dependencies=deps(),
    )
    plan = execute(
        "show-plan", root, metadata_path=external, expected_metadata_sha256=original_digest, dependencies=deps(),
    )
    _write_metadata(external, current_task="Changed authority")
    sync = execute(
        "sync", root, metadata_path=external, expected_metadata_sha256=original_digest, dependencies=deps(),
    )

    assert validate.exit_code == plan.exit_code == SUCCESS
    assert validate.payload["metadata_sha256"] == plan.payload["metadata_sha256"] == original_digest
    assert sync.exit_code == VALIDATION_FAILED
    assert sync.payload["writes_performed"] is False
    assert not (root / ".ai/STATE.json").exists()


@pytest.mark.parametrize("expected", ("A" * 64, "a" * 63, "a" * 65, "not-a-hash"))
def test_invalid_expected_metadata_sha_fails_closed_before_sync_recovery(tmp_path: Path, expected: str) -> None:
    root = make_engine_repo(tmp_path)
    external = tmp_path / "authority.json"
    _write_metadata(external)
    calls = 0

    def forbidden_recovery(_root: Path) -> str | None:
        nonlocal calls
        calls += 1
        return None

    result = execute(
        "sync", root, metadata_path=external, expected_metadata_sha256=expected,
        dependencies=deps(recovery=forbidden_recovery),
    )

    assert result.exit_code == VALIDATION_FAILED
    assert calls == 0
    assert not (root / ".ai/STATE.json").exists()
