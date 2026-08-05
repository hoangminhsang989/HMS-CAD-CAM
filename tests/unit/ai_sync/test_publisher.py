"""WP5 publisher unit safety and transaction tests."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import pytest

from tools.ai_sync.config import load_config
from tools.ai_sync.models import (
    CapabilitySet,
    DiffScope,
    DiffSummary,
    GitSnapshot,
    PublicationStatus,
    SUPPORTED_CAPABILITIES,
    default_version_info,
)
from tools.ai_sync.publisher import (
    LOCK_PATH,
    PublicationHooks,
    PublisherError,
    publish_outputs,
    verify_public_snapshot,
)
from tools.ai_sync.renderers import render_output_candidates
from tools.ai_sync.state_builder import build_project_state, empty_metadata


NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)
VERSION = default_version_info()
CAPABILITIES = CapabilitySet(SUPPORTED_CAPABILITIES, SUPPORTED_CAPABILITIES)
SUBSET_CAPABILITIES = CapabilitySet(
    SUPPORTED_CAPABILITIES,
    ("canonical_state_json", "git_read_only_snapshot"),
)


def make_outputs(
    root: Path,
    *,
    run_id: str = "run-1",
    second: int = 0,
    capabilities: CapabilitySet = CAPABILITIES,
):
    config = load_config(Path(__file__).parents[3])
    captured = NOW.replace(second=second)
    git = GitSnapshot(
        root.resolve(), captured, "main", False, "a" * 40, None, None, None, (), (),
        DiffSummary(DiffScope.STAGED, 0, 0, 0, 0, ()),
        DiffSummary(DiffScope.UNSTAGED, 0, 0, 0, 0, ()), False, "b" * 64,
    )
    state = build_project_state(
        config=config, git=git, tests=(), metadata=empty_metadata(config.project.name),
        generated_at=captured, run_id=run_id, version=VERSION, capabilities=capabilities,
    )
    checkpoint = f".ai/CHECKPOINTS/2026-08-04_1200{second:02d}.md"
    return render_output_candidates(state, version=VERSION, capabilities=capabilities, checkpoint_path=checkpoint)


def do_publish(
    root: Path,
    outputs,
    *,
    hooks: PublicationHooks = PublicationHooks(),
    capabilities: CapabilitySet = CAPABILITIES,
):
    return publish_outputs(
        root, outputs, version=VERSION, capabilities=capabilities,
        started_at=NOW, completed_at=NOW, hooks=hooks,
    )


def public_bytes(root: Path, outputs) -> dict[str, bytes]:
    return {path: root.joinpath(*path.split("/")).read_bytes() for path in outputs.by_path()}


def test_first_publication_writes_exact_eight_and_manifest_last_contract(tmp_path: Path) -> None:
    outputs = make_outputs(tmp_path)
    result = do_publish(tmp_path, outputs)
    assert result.status is PublicationStatus.PUBLISHED
    assert result.manifest_self_digest == outputs.manifest_self_digest
    assert result.manifest_file_sha256 == outputs.by_path()[".ai/MANIFEST.json"].sha256
    assert result.manifest_self_digest != result.manifest_file_sha256
    assert set(result.published_paths) == set(outputs.by_path())
    assert public_bytes(tmp_path, outputs) == {path: item.content for path, item in outputs.by_path().items()}
    assert json.loads((tmp_path / ".ai/MANIFEST.json").read_text(encoding="utf-8"))["publication_status"] == "complete"
    assert not (tmp_path / ".ai/.sync-tmp").exists()


def test_lock_collision_is_fail_closed_and_not_broken(tmp_path: Path) -> None:
    lock = tmp_path.joinpath(*LOCK_PATH.split("/"))
    lock.parent.mkdir(parents=True)
    lock.write_text("owned\n", encoding="utf-8")
    with pytest.raises(PublisherError, match="lock") as caught:
        do_publish(tmp_path, make_outputs(tmp_path))
    assert caught.value.code == "PUBLICATION_LOCKED" and lock.read_text(encoding="utf-8") == "owned\n"


def test_checkpoint_collision_rolls_back_mutable_outputs(tmp_path: Path) -> None:
    outputs = make_outputs(tmp_path)
    checkpoint = next(path for path in outputs.by_path() if path.startswith(".ai/CHECKPOINTS/"))
    target = tmp_path.joinpath(*checkpoint.split("/")); target.parent.mkdir(parents=True)
    target.write_bytes(b"# existing\n")
    with pytest.raises(PublisherError) as caught:
        do_publish(tmp_path, outputs)
    assert caught.value.code == "CHECKPOINT_COLLISION"
    assert target.read_bytes() == b"# existing\n"
    assert not (tmp_path / ".ai/STATE.json").exists()
    assert not (tmp_path / ".ai/.sync-tmp").exists()


def test_optimistic_fingerprint_mismatch_is_zero_write(tmp_path: Path) -> None:
    outputs = make_outputs(tmp_path)
    with pytest.raises(PublisherError) as caught:
        publish_outputs(
            tmp_path, outputs, version=VERSION, capabilities=CAPABILITIES,
            started_at=NOW, completed_at=NOW, expected_fingerprint="expected",
            hooks=PublicationHooks(fingerprint=lambda: "changed"),
        )
    assert caught.value.code == "SAFETY_FINGERPRINT_CHANGED"
    assert not (tmp_path / ".ai").exists()


def test_pre_manifest_fault_rolls_back_exact_old_snapshot(tmp_path: Path) -> None:
    first = make_outputs(tmp_path, run_id="run-1", second=0)
    do_publish(tmp_path, first)
    before = public_bytes(tmp_path, first)
    second = make_outputs(tmp_path, run_id="run-2", second=1)

    def fault(point: str) -> None:
        if point == "mutable_replace:.ai/SESSION.json":
            raise RuntimeError("injected")

    with pytest.raises(PublisherError):
        do_publish(tmp_path, second, hooks=PublicationHooks(fault=fault))
    assert public_bytes(tmp_path, first) == before
    assert not (tmp_path / ".ai/.sync-tmp").exists()


def test_publisher_does_not_accept_unsafe_ai_link(tmp_path: Path) -> None:
    outside = tmp_path / "outside"; outside.mkdir()
    try:
        (tmp_path / ".ai").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {type(error).__name__}")
    with pytest.raises(PublisherError) as caught:
        do_publish(tmp_path, make_outputs(tmp_path))
    assert caught.value.code == "SAFETY_REPARSE_POINT"
    assert list(outside.iterdir()) == []

def _rewrite_public_artifact(root: Path, relative: str, data: bytes) -> None:
    target = root.joinpath(*relative.split("/"))
    target.write_bytes(data)
    manifest_path = root / ".ai/MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["artifacts"]:
        if record["path"] == relative:
            record["sha256"] = hashlib.sha256(data).hexdigest()
            record["size_bytes"] = len(data)
            break
    else:
        raise AssertionError("artifact record missing")
    unsigned = dict(manifest)
    unsigned.pop("publication_manifest_sha256")
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest["publication_manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    manifest_path.write_bytes(
        (
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + chr(10)
        ).encode("utf-8")
    )


def test_verifier_rejects_cross_run_state_after_hashes_and_self_digest_are_rebound(
    tmp_path: Path,
) -> None:
    outputs = make_outputs(tmp_path)
    do_publish(tmp_path, outputs)
    path = tmp_path / ".ai/STATE.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["run_id"] = "cross-run"
    data = (
        json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + chr(10)
    ).encode("utf-8")
    _rewrite_public_artifact(tmp_path, ".ai/STATE.json", data)
    with pytest.raises(PublisherError) as caught:
        verify_public_snapshot(tmp_path)
    assert caught.value.code == "PUBLIC_SNAPSHOT_INVALID"


@pytest.mark.parametrize(
    ("relative", "field", "value"),
    (
        (".ai/SESSION.json", "project", "cross-project"),
        (".ai/METRICS.json", "run_id", "cross-run"),
    ),
)
def test_verifier_rejects_rebound_derived_json_cross_snapshot_values(
    tmp_path: Path,
    relative: str,
    field: str,
    value: str,
) -> None:
    outputs = make_outputs(tmp_path)
    do_publish(tmp_path, outputs)
    path = tmp_path.joinpath(*relative.split("/"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    data = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + chr(10)
    ).encode("utf-8")
    _rewrite_public_artifact(tmp_path, relative, data)
    with pytest.raises(PublisherError) as caught:
        verify_public_snapshot(tmp_path)
    assert caught.value.code == "PUBLIC_SNAPSHOT_INVALID"


def test_public_snapshot_verifier_rejects_tampering(tmp_path: Path) -> None:
    outputs = make_outputs(tmp_path)
    do_publish(tmp_path, outputs)
    assert verify_public_snapshot(tmp_path)["run_id"] == outputs.run_id
    (tmp_path / ".ai/STATE.json").write_bytes(b"tampered\n")
    with pytest.raises(PublisherError) as caught:
        verify_public_snapshot(tmp_path)
    assert caught.value.code == "PUBLIC_SNAPSHOT_INVALID"


def test_public_snapshot_verifier_rejects_incompatible_schema(tmp_path: Path) -> None:
    import hashlib

    outputs = make_outputs(tmp_path)
    do_publish(tmp_path, outputs)
    path = tmp_path / ".ai/MANIFEST.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["manifest_schema_version"] = 2
    unsigned = dict(payload)
    unsigned.pop("publication_manifest_sha256")
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["publication_manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(PublisherError) as caught:
        verify_public_snapshot(tmp_path)
    assert caught.value.code == "PUBLIC_MANIFEST_INCOMPATIBLE"


def test_preexisting_journal_is_preserved_for_recovery(tmp_path: Path) -> None:
    journal = tmp_path / ".ai/.sync-tmp/journal.json"
    journal.parent.mkdir(parents=True)
    journal.write_bytes(b"operator evidence\n")
    with pytest.raises(PublisherError) as caught:
        do_publish(tmp_path, make_outputs(tmp_path))
    assert caught.value.recovery_required
    assert journal.read_bytes() == b"operator evidence\n"
    assert not (tmp_path / ".ai/.sync-tmp/LOCK").exists()

def _replace_checkpoint_capability_sections(
    text: str,
    *,
    supported: list[str],
    required: list[str],
) -> str:
    original_supported = (
        "### Supported\n\n"
        + "\n".join(f"- {item}" for item in SUPPORTED_CAPABILITIES)
    )
    original_required = (
        "### Required\n\n"
        + "\n".join(f"- {item}" for item in SUBSET_CAPABILITIES.required)
    )
    assert text.count(original_supported) == 1
    assert text.count(original_required) == 1
    replacement_supported = (
        "### Supported\n\n"
        + "\n".join(f"- {item}" for item in supported)
    )
    replacement_required = (
        "### Required\n\n"
        + "\n".join(f"- {item}" for item in required)
    )
    return text.replace(
        original_supported,
        replacement_supported,
    ).replace(
        original_required,
        replacement_required,
    )


@pytest.mark.parametrize(
    "case",
    (
        "required_missing",
        "required_extra",
        "required_duplicate",
        "required_not_supported",
        "capability_wrong_section",
    ),
)
def test_verifier_rejects_rebound_checkpoint_capability_section_tampering(
    tmp_path: Path,
    case: str,
) -> None:
    outputs = make_outputs(tmp_path, capabilities=SUBSET_CAPABILITIES)
    do_publish(tmp_path, outputs, capabilities=SUBSET_CAPABILITIES)
    checkpoint = next(
        path for path in outputs.by_path()
        if path.startswith(".ai/CHECKPOINTS/")
    )
    path = tmp_path.joinpath(*checkpoint.split("/"))
    text = path.read_text(encoding="utf-8")
    supported = list(SUPPORTED_CAPABILITIES)
    required = list(SUBSET_CAPABILITIES.required)
    if case == "required_missing":
        required.remove("canonical_state_json")
    elif case == "required_extra":
        required.append("dry_run")
    elif case == "required_duplicate":
        required.append("canonical_state_json")
    elif case == "required_not_supported":
        supported.remove("canonical_state_json")
    elif case == "capability_wrong_section":
        supported.remove("dry_run")
        required.append("dry_run")
    else:
        raise AssertionError(case)
    tampered = _replace_checkpoint_capability_sections(
        text,
        supported=supported,
        required=required,
    ).encode("utf-8")
    _rewrite_public_artifact(tmp_path, checkpoint, tampered)
    with pytest.raises(PublisherError) as caught:
        verify_public_snapshot(tmp_path)
    assert caught.value.code == "PUBLIC_SNAPSHOT_INVALID"
