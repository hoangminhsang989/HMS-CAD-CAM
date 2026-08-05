"""WP5 journal/recovery and fault-boundary integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.ai_sync.publisher import (
    LOCK_PATH,
    PublicationCrash,
    PublicationHooks,
    PublisherError,
    publish_outputs,
    recover_publication,
    verify_public_snapshot,
)
from tests.unit.ai_sync.test_publisher import CAPABILITIES, NOW, VERSION, do_publish, make_outputs, public_bytes


@pytest.mark.parametrize(
    "fault_point",
    (
        "temp_create", "temp_write", "file_flush", "fsync", "manifest_plan", "journal_prepared",
        "mutable_replace:.ai/STATE.json", "mutable_replace:.ai/CURRENT_STATUS.md",
        "mutable_replace:.ai/NEXT_TASK.md", "mutable_replace:.ai/SESSION.json",
        "mutable_replace:.ai/METRICS.json", "mutable_replace:.ai/HANDOFF/TO_CHATGPT.md",
        "hash_verify:.ai/STATE.json", "checkpoint_exclusive_create", "checkpoint_flush",
        "final_artifacts_verify",
    ),
)
def test_each_pre_manifest_fault_boundary_leaves_no_public_snapshot(tmp_path: Path, fault_point: str) -> None:
    outputs = make_outputs(tmp_path)

    def fault(point: str) -> None:
        if point == fault_point:
            raise RuntimeError("injected")

    with pytest.raises(PublisherError):
        do_publish(tmp_path, outputs, hooks=PublicationHooks(fault=fault))
    for relative in outputs.by_path():
        assert not tmp_path.joinpath(*relative.split("/")).exists()
    assert not (tmp_path / ".ai/.sync-tmp").exists()


def test_crash_before_manifest_recovers_by_exact_rollback(tmp_path: Path) -> None:
    outputs = make_outputs(tmp_path)

    def crash(point: str) -> None:
        if point == "mutable_replace:.ai/SESSION.json":
            raise PublicationCrash("crash")

    with pytest.raises(PublicationCrash):
        do_publish(tmp_path, outputs, hooks=PublicationHooks(fault=crash))
    assert (tmp_path / ".ai/STATE.json").read_bytes() == outputs.by_path()[".ai/STATE.json"].content
    lock = tmp_path.joinpath(*LOCK_PATH.split("/")); assert lock.exists()
    lock.unlink()  # operator-confirmed stale lock; publisher never breaks it itself
    assert recover_publication(tmp_path) == "rolled_back"
    for relative in outputs.by_path():
        assert not tmp_path.joinpath(*relative.split("/")).exists()


@pytest.mark.parametrize("fault_point", ("manifest_replace", "manifest_verify", "journal_committed", "cleanup"))
def test_fault_after_valid_manifest_recovers_by_roll_forward(tmp_path: Path, fault_point: str) -> None:
    outputs = make_outputs(tmp_path)

    def fault(point: str) -> None:
        if point == fault_point:
            raise RuntimeError("injected")

    with pytest.raises(PublisherError) as caught:
        do_publish(tmp_path, outputs, hooks=PublicationHooks(fault=fault))
    assert caught.value.recovery_required
    lock = tmp_path.joinpath(*LOCK_PATH.split("/")); assert lock.exists(); lock.unlink()
    assert recover_publication(tmp_path) == "rolled_forward"
    assert public_bytes(tmp_path, outputs) == {path: item.content for path, item in outputs.by_path().items()}


def test_concurrent_write_during_rollback_is_preserved_and_fail_closed(tmp_path: Path) -> None:
    outputs = make_outputs(tmp_path)
    target = tmp_path / ".ai/STATE.json"

    def fault(point: str) -> None:
        if point == "mutable_replace:.ai/STATE.json":
            target.write_bytes(b"external\n")
            raise RuntimeError("injected concurrent writer")

    with pytest.raises(PublisherError) as caught:
        do_publish(tmp_path, outputs, hooks=PublicationHooks(fault=fault))
    assert caught.value.code == "SAFETY_CONCURRENT_WRITE"
    assert caught.value.recovery_required and target.read_bytes() == b"external\n"
    assert (tmp_path / ".ai/.sync-tmp/journal.json").exists()


def test_update_publication_preserves_old_manifest_on_rollback(tmp_path: Path) -> None:
    first = make_outputs(tmp_path, run_id="run-1", second=0); do_publish(tmp_path, first)
    before = public_bytes(tmp_path, first)
    second = make_outputs(tmp_path, run_id="run-2", second=1)

    def fault(point: str) -> None:
        if point == "final_artifacts_verify":
            raise RuntimeError("injected")

    with pytest.raises(PublisherError):
        do_publish(tmp_path, second, hooks=PublicationHooks(fault=fault))
    assert public_bytes(tmp_path, first) == before


def test_backup_fault_is_zero_public_change(tmp_path: Path) -> None:
    first = make_outputs(tmp_path, run_id="run-1", second=0); do_publish(tmp_path, first)
    before = public_bytes(tmp_path, first)
    second = make_outputs(tmp_path, run_id="run-2", second=1)

    def fault(point: str) -> None:
        if point == "backup:.ai/STATE.json":
            raise RuntimeError("injected backup failure")

    with pytest.raises(PublisherError):
        do_publish(tmp_path, second, hooks=PublicationHooks(fault=fault))
    assert public_bytes(tmp_path, first) == before
    assert not (tmp_path / ".ai/.sync-tmp").exists()


def test_same_volume_guard_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = make_outputs(tmp_path)
    import tools.ai_sync.publisher as module
    original = module._replace

    def cross_volume(source: Path, target: Path) -> None:
        raise PublisherError("SAFETY_CROSS_VOLUME", "atomic replacement requires one filesystem volume")

    monkeypatch.setattr(module, "_replace", cross_volume)
    with pytest.raises(PublisherError) as caught:
        publish_outputs(tmp_path, outputs, version=VERSION, capabilities=CAPABILITIES, started_at=NOW, completed_at=NOW)
    assert caught.value.code == "SAFETY_CROSS_VOLUME"
    monkeypatch.setattr(module, "_replace", original)

def test_recovery_keeps_old_hashes_rolls_back_candidates_and_verifies_old_snapshot(
    tmp_path: Path,
) -> None:
    first = make_outputs(tmp_path, run_id="old-run", second=0)
    do_publish(tmp_path, first)
    old_public = public_bytes(tmp_path, first)
    second = make_outputs(tmp_path, run_id="candidate-run", second=1)

    def crash(point: str) -> None:
        if point == "mutable_replace:.ai/SESSION.json":
            raise PublicationCrash("crash")

    with pytest.raises(PublicationCrash):
        do_publish(tmp_path, second, hooks=PublicationHooks(fault=crash))
    assert (tmp_path / ".ai/STATE.json").read_bytes() == second.by_path()[".ai/STATE.json"].content
    assert (tmp_path / ".ai/METRICS.json").read_bytes() == old_public[".ai/METRICS.json"]
    lock = tmp_path.joinpath(*LOCK_PATH.split("/"))
    lock.unlink()
    assert recover_publication(tmp_path) == "rolled_back"
    assert public_bytes(tmp_path, first) == old_public
    assert verify_public_snapshot(tmp_path)["run_id"] == "old-run"
    assert not lock.parent.exists()


def test_recovery_old_absent_third_party_is_ambiguous_and_preserves_evidence(
    tmp_path: Path,
) -> None:
    outputs = make_outputs(tmp_path)

    def crash(point: str) -> None:
        if point == "mutable_replace:.ai/SESSION.json":
            raise PublicationCrash("crash")

    with pytest.raises(PublicationCrash):
        do_publish(tmp_path, outputs, hooks=PublicationHooks(fault=crash))
    lock = tmp_path.joinpath(*LOCK_PATH.split("/"))
    lock.unlink()
    target = tmp_path / ".ai/STATE.json"
    third_party = b"third-party-old-absent\n"
    target.write_bytes(third_party)
    journal = lock.parent / "journal.json"
    run_root = lock.parent / "run-1"
    before = {
        path.relative_to(lock.parent).as_posix(): path.read_bytes()
        for path in run_root.rglob("*")
        if path.is_file()
    }
    journal_before = journal.read_bytes()
    with pytest.raises(PublisherError) as caught:
        recover_publication(tmp_path)
    assert caught.value.code == "PUBLICATION_RECOVERY_AMBIGUOUS"
    assert caught.value.recovery_required
    assert target.read_bytes() == third_party
    assert journal.read_bytes() == journal_before
    assert {
        path.relative_to(lock.parent).as_posix(): path.read_bytes()
        for path in run_root.rglob("*")
        if path.is_file()
    } == before
    assert not (tmp_path / ".ai/MANIFEST.json").exists()


def test_recovery_verifies_every_old_hash_before_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = make_outputs(tmp_path)

    def crash(point: str) -> None:
        if point == "mutable_replace:.ai/SESSION.json":
            raise PublicationCrash("crash")

    with pytest.raises(PublicationCrash):
        do_publish(tmp_path, outputs, hooks=PublicationHooks(fault=crash))
    lock = tmp_path.joinpath(*LOCK_PATH.split("/"))
    lock.unlink()
    journal = lock.parent / "journal.json"
    journal_before = journal.read_bytes()
    import tools.ai_sync.publisher as module

    monkeypatch.setattr(module, "_rollback", lambda *args, **kwargs: ())
    with pytest.raises(PublisherError) as caught:
        recover_publication(tmp_path)
    assert caught.value.code == "PUBLICATION_RECOVERY_AMBIGUOUS"
    assert caught.value.recovery_required
    assert journal.read_bytes() == journal_before
    assert lock.parent.is_dir()
    assert (tmp_path / ".ai/STATE.json").is_file()
