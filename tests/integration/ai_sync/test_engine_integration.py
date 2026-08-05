"""WP6 end-to-end engine integration tests in temporary Git repositories."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

from tools.ai_sync.engine import SAFETY_BOUNDARY_VIOLATION, SUCCESS, execute
from tools.ai_sync.models import SUPPORTED_CAPABILITIES
from tools.ai_sync.publisher import (
    LOCK_PATH,
    PublicationCrash,
    PublicationHooks,
    publish_outputs,
    verify_public_snapshot,
)
from tests.unit.ai_sync.test_engine import NOW, deps, make_engine_repo, tree_hashes


def test_sync_end_to_end_only_publishes_exact_outputs(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    before_readme = (root / "README.md").read_bytes()
    result = execute("sync", root, stage="WP6", task="Temporary repository publication", dependencies=deps())
    assert result.exit_code == SUCCESS
    assert result.payload["writes_performed"] is True
    assert len(result.payload["publication"]["published_paths"]) == 8
    assert (root / ".ai/MANIFEST.json").is_file() and (root / ".ai/STATE.json").is_file()
    assert (root / "README.md").read_bytes() == before_readme
    assert not (root / ".ai/.sync-tmp").exists()


def test_dry_commands_preserve_process_cwd_and_tree(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path); before = tree_hashes(root); cwd = Path.cwd()
    for command in ("inspect", "validate", "show-plan"):
        assert execute(command, root, dependencies=deps()).exit_code == SUCCESS
    assert Path.cwd() == cwd and tree_hashes(root) == before

def test_repository_root_is_resolved_before_config_for_root_subdir_and_file(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    subdirectory = root / "nested"
    subdirectory.mkdir()
    file_path = subdirectory / "inside.txt"
    file_path.write_text("inside\n", encoding="utf-8")
    cwd = Path.cwd()
    for candidate in (root, subdirectory, file_path):
        result = execute("inspect", candidate, dependencies=deps())
        assert result.exit_code == SUCCESS
        assert result.payload["git"]["head_oid"]
    assert Path.cwd() == cwd


def test_concurrent_source_change_during_preparation_fails_closed(tmp_path: Path, monkeypatch) -> None:
    import tools.ai_sync.engine as module

    root = make_engine_repo(tmp_path)
    original = module.capture_git_snapshot
    calls = 0

    def capture(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            (root / "README.md").write_text("external concurrent change\n", encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "capture_git_snapshot", capture)
    result = execute("sync", root, dependencies=deps())
    assert result.exit_code == 9
    assert (root / "README.md").read_text(encoding="utf-8") == "external concurrent change\n"
    assert not (root / ".ai/STATE.json").exists()
    assert not (root / ".ai/.sync-tmp").exists()


def _crashing_publisher(fault_point: str):
    def publisher(*args, **kwargs):
        original_hooks = kwargs["hooks"]

        def crash(point: str) -> None:
            if point == fault_point:
                raise PublicationCrash("injected process crash")

        kwargs["hooks"] = PublicationHooks(
            fingerprint=original_hooks.fingerprint,
            fault=crash,
        )
        return publish_outputs(*args, **kwargs)

    return publisher


def _recovery_deps(*, second: int, run_id: str, logs: list[str] | None = None):
    recovered_at = NOW + timedelta(seconds=second)
    return deps(
        clock=lambda: recovered_at,
        run_id=lambda: run_id,
        log_sink=None if logs is None else logs.append,
    )


def test_sync_startup_recovers_pre_manifest_crash_then_continues(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    with pytest.raises(PublicationCrash):
        execute(
            "sync",
            root,
            dependencies=deps(publisher=_crashing_publisher("mutable_replace:.ai/SESSION.json")),
        )
    lock = root.joinpath(*LOCK_PATH.split("/"))
    journal = lock.parent / "journal.json"
    assert lock.is_file() and journal.is_file()
    lock.unlink()  # operator-confirmed stale lock; engine never breaks it
    logs: list[str] = []
    result = execute(
        "sync",
        root,
        dependencies=_recovery_deps(second=1, run_id="recovered-after-rollback", logs=logs),
    )
    assert result.exit_code == SUCCESS
    assert result.payload["recovery"] == {"outcome": "rolled_back", "continued": True}
    assert result.payload["writes_performed"] is True
    assert any('"event":"publication_recovered"' in record for record in logs)
    assert not lock.parent.exists()


def test_sync_startup_recovers_post_manifest_crash_then_continues(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    with pytest.raises(PublicationCrash):
        execute(
            "sync",
            root,
            dependencies=deps(publisher=_crashing_publisher("manifest_verify")),
        )
    lock = root.joinpath(*LOCK_PATH.split("/"))
    journal = lock.parent / "journal.json"
    assert lock.is_file() and journal.is_file() and (root / ".ai/MANIFEST.json").is_file()
    lock.unlink()  # operator-confirmed stale lock; engine never breaks it
    result = execute(
        "sync",
        root,
        dependencies=_recovery_deps(second=1, run_id="recovered-after-roll-forward"),
    )
    assert result.exit_code == SUCCESS
    assert result.payload["recovery"] == {"outcome": "rolled_forward", "continued": True}
    assert result.payload["writes_performed"] is True
    assert not lock.parent.exists()


def test_sync_startup_existing_lock_fails_closed_without_breaking_it(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    lock = root.joinpath(*LOCK_PATH.split("/"))
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"operator-owned-lock\n")
    before = tree_hashes(root)
    result = execute("sync", root, dependencies=deps())
    assert result.exit_code == SAFETY_BOUNDARY_VIOLATION
    assert result.payload["error"]["code"] == "PUBLICATION_LOCKED"
    assert tree_hashes(root) == before
    assert lock.read_bytes() == b"operator-owned-lock\n"


def test_sync_startup_ambiguous_recovery_fails_closed_and_preserves_evidence(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    with pytest.raises(PublicationCrash):
        execute(
            "sync",
            root,
            dependencies=deps(publisher=_crashing_publisher("manifest_verify")),
        )
    lock = root.joinpath(*LOCK_PATH.split("/"))
    lock.unlink()
    state = root / ".ai/STATE.json"
    state.write_bytes(b"external-concurrent-state\n")
    transaction = lock.parent
    before = tree_hashes(root)
    result = execute("sync", root, dependencies=_recovery_deps(second=1, run_id="ambiguous"))
    assert result.exit_code == SAFETY_BOUNDARY_VIOLATION
    assert result.payload["error"]["code"] == "PUBLICATION_RECOVERY_AMBIGUOUS"
    assert tree_hashes(root) == before
    assert state.read_bytes() == b"external-concurrent-state\n"
    assert (transaction / "journal.json").is_file()
    assert (transaction / "engine-run/backups").is_dir()


@pytest.mark.parametrize("command", ("inspect", "validate", "show-plan"))
def test_dry_commands_never_call_recovery_or_mutate_pending_state(
    tmp_path: Path,
    command: str,
) -> None:
    root = make_engine_repo(tmp_path)
    transaction = root / ".ai/.sync-tmp"
    transaction.mkdir(parents=True)
    (transaction / "journal.json").write_bytes(b"pending-recovery-evidence\n")
    before = tree_hashes(root)

    def forbidden_recovery(_root: Path) -> str | None:
        raise AssertionError("dry command called recovery")

    result = execute(command, root, dependencies=deps(recovery=forbidden_recovery))
    assert result.exit_code == SUCCESS
    assert result.payload["writes_performed"] is False
    assert tree_hashes(root) == before

def test_sync_startup_pre_manifest_third_party_content_is_ambiguous(
    tmp_path: Path,
) -> None:
    root = make_engine_repo(tmp_path)
    first = execute("sync", root, dependencies=deps())
    assert first.exit_code == SUCCESS
    old_manifest = (root / ".ai/MANIFEST.json").read_bytes()
    crash_time = NOW + timedelta(seconds=1)
    with pytest.raises(PublicationCrash):
        execute(
            "sync",
            root,
            dependencies=deps(
                publisher=_crashing_publisher("mutable_replace:.ai/SESSION.json"),
                clock=lambda: crash_time,
                run_id=lambda: "r2-pre-manifest-crash",
            ),
        )
    lock = root.joinpath(*LOCK_PATH.split("/"))
    journal = lock.parent / "journal.json"
    backups = lock.parent / "r2-pre-manifest-crash/backups"
    assert lock.is_file() and journal.is_file() and backups.is_dir()
    assert any(path.is_file() for path in backups.rglob("*"))
    lock.unlink()  # operator-confirmed stale lock; engine never breaks it
    third_party = b"third-party-content-must-survive\n"
    state = root / ".ai/STATE.json"
    state.write_bytes(third_party)
    evidence_before = tree_hashes(root)
    result = execute(
        "sync",
        root,
        dependencies=_recovery_deps(second=2, run_id="must-not-publish"),
    )
    assert result.exit_code == SAFETY_BOUNDARY_VIOLATION
    assert result.payload["error"]["code"] == "PUBLICATION_RECOVERY_AMBIGUOUS"
    assert result.payload["writes_performed"] is False
    assert state.read_bytes() == third_party
    assert (root / ".ai/MANIFEST.json").read_bytes() == old_manifest
    assert tree_hashes(root) == evidence_before
    assert journal.is_file() and backups.is_dir()


def _checkpoint_capability_items(text: str, heading: str) -> tuple[str, ...]:
    marker = f"### {heading}"
    lines = text.splitlines()
    start = lines.index(marker) + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("### ") or lines[index].startswith("## ")),
        len(lines),
    )
    return tuple(line[2:] for line in lines[start:end] if line)


def test_temp_repo_sync_supports_required_capability_subset(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path)
    config_path = root / ".ai/config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["ai_sync"]["required_capabilities"] = [
        "git_read_only_snapshot",
        "canonical_state_json",
    ]
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result = execute("sync", root, dependencies=deps())
    assert result.exit_code == SUCCESS
    manifest = verify_public_snapshot(root)
    checkpoint = root.joinpath(*manifest["latest_checkpoint"].split("/"))
    checkpoint_text = checkpoint.read_text(encoding="utf-8")
    expected_required = ("canonical_state_json", "git_read_only_snapshot")
    assert _checkpoint_capability_items(checkpoint_text, "Supported") == SUPPORTED_CAPABILITIES
    assert _checkpoint_capability_items(checkpoint_text, "Required") == expected_required
    assert manifest["capabilities"]["required"] == list(expected_required)
    publication = result.payload["publication"]
    assert publication["manifest_self_digest"] == manifest["publication_manifest_sha256"]
    assert publication["manifest_file_sha256"] == hashlib.sha256(
        (root / ".ai/MANIFEST.json").read_bytes()
    ).hexdigest()
    assert publication["manifest_self_digest"] != publication["manifest_file_sha256"]
    assert not (root / ".ai/.sync-tmp").exists()
