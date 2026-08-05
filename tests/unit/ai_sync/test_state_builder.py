"""WP4 tests for strict metadata and immutable state construction."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from tools.ai_sync.config import load_config
from tools.ai_sync.models import (
    BlockersState, CapabilitySet, DiffScope, DiffSummary, GitSnapshot, ProjectStatus,
    SUPPORTED_CAPABILITIES, default_version_info,
)
from tools.ai_sync.state_builder import MetadataError, build_project_state, empty_metadata, parse_metadata_bytes


NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _git(root: Path) -> GitSnapshot:
    return GitSnapshot(root, NOW, "main", False, "a" * 40, None, None, None, (), (),
                       DiffSummary(DiffScope.STAGED, 0, 0, 0, 0, ()), DiffSummary(DiffScope.UNSTAGED, 0, 0, 0, 0, ()), False, "b" * 64)


def _build(root: Path, metadata=None, verified=frozenset()):
    config = load_config(Path(__file__).parents[3])
    return build_project_state(config=config, git=_git(root), tests=(), metadata=metadata or empty_metadata(config.project.name),
                               generated_at=NOW, run_id="run-1", version=default_version_info(),
                               capabilities=CapabilitySet(SUPPORTED_CAPABILITIES, SUPPORTED_CAPABILITIES), verified_commit_oids=verified)


def test_missing_metadata_stays_unknown_and_null(tmp_path: Path) -> None:
    state = _build(tmp_path)
    assert state.status is ProjectStatus.UNKNOWN and state.stage is None
    assert state.stage_progress_percent is None and state.blockers_state is BlockersState.UNKNOWN


@pytest.mark.parametrize("value", (-1, 101, True))
def test_progress_boundaries_fail(value: object) -> None:
    payload = {"schema_version": 1, "project": "HMS CAD/CAM", "stage_progress_percent": value}
    with pytest.raises(MetadataError): parse_metadata_bytes(json.dumps(payload).encode(), expected_project="HMS CAD/CAM")


@pytest.mark.parametrize(
    "payload",
    (
        {"blockers_state": "present", "blockers": []},
        {"blockers_state": "verified_none", "blockers": ["x"]},
        {"blockers_state": "bogus"},
    ),
)
def test_blocker_semantics_are_explicit(payload: dict[str, object]) -> None:
    payload.update({"schema_version": 1, "project": "HMS CAD/CAM"})
    with pytest.raises(MetadataError): parse_metadata_bytes(json.dumps(payload).encode(), expected_project="HMS CAD/CAM")


def test_present_blocker_and_commit_claim_are_preserved(tmp_path: Path) -> None:
    oid = "c" * 40
    payload = {"schema_version": 1, "project": "HMS CAD/CAM", "stage": "WP4", "status": "ready_for_review",
               "blockers_state": "present", "blockers": ["review"], "remaining_work": ["approval"],
               "provenance": {"metadata": "user"}, "commit_claim": {"oid": oid}}
    metadata = parse_metadata_bytes(json.dumps(payload).encode(), expected_project="HMS CAD/CAM")
    state = _build(tmp_path, metadata, frozenset({oid}))
    assert state.blockers == ("review",) and state.commit_claim_verified is True
    assert dict(state.provenance)["git"] == "verified_git_snapshot"


def test_metadata_rejects_project_conflict_duplicate_bom_secret_and_absolute_path() -> None:
    with pytest.raises(MetadataError): parse_metadata_bytes(b'{"project":"A","project":"B"}', expected_project="A")
    with pytest.raises(MetadataError): parse_metadata_bytes(b"\xef\xbb\xbf{}", expected_project="A")
    for value in ("token=abc", "C:/private/value"):
        with pytest.raises(MetadataError):
            parse_metadata_bytes(json.dumps({"project": "A", "current_task": value}).encode(), expected_project="A")
