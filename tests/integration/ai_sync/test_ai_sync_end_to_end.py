"""WP7 full V1.1 pipeline and consumer verification in a temporary Git repo."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.ai_sync.engine import SUCCESS, execute
from tools.ai_sync.publisher import verify_public_snapshot
from tests.unit.ai_sync.test_engine import deps, make_engine_repo


def _git_metadata(root: Path) -> dict[str, str]:
    paths = [root / ".git/HEAD", root / ".git/index", root / ".git/config"]
    paths.extend(path for path in (root / ".git/refs").rglob("*") if path.is_file())
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _write_verified_evidence(root: Path) -> None:
    log = root / "evidence.log"; log.write_bytes(b"2 passed in 0.01s\n")
    run = {
        "run_id": "certification-1",
        "command": {"argv": ["python", "-m", "pytest", "tests/unit/ai_sync"]},
        "exit_code": 0,
        "started_at": "2026-08-04T12:59:58Z",
        "completed_at": "2026-08-04T13:00:00Z",
        "duration_seconds": 2.0,
        "counts": {"passed": 2, "failed": 0, "skipped": 0, "deselected": 0, "xfailed": 0, "xpassed": 0, "warnings": 0},
        "status": "passed",
        "evidence_source": "runner_json",
        "verification": "verified",
        "verification_notes": [],
        "log_path": "evidence.log",
        "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
    }
    document = {
        "schema_version": 1,
        "project": "HMS CAD/CAM",
        "generated_at": "2026-08-04T13:00:00Z",
        "runs": [run],
    }
    (root / ".ai/TEST_RESULTS.json").write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8", newline="\n",
    )


def test_full_pipeline_is_deterministic_publishes_and_is_consumer_verifiable(tmp_path: Path) -> None:
    root = make_engine_repo(tmp_path); _write_verified_evidence(root)
    git_before = _git_metadata(root); source_before = (root / "README.md").read_bytes()

    first_plan = execute("show-plan", root, dependencies=deps())
    second_plan = execute("show-plan", root, dependencies=deps())
    assert first_plan.exit_code == second_plan.exit_code == SUCCESS
    assert first_plan.payload["candidate_artifacts"] == second_plan.payload["candidate_artifacts"]
    assert first_plan.payload["manifest_self_digest"] == second_plan.payload["manifest_self_digest"]
    assert first_plan.payload["test_evidence"] == {"present": True, "run_count": 1}

    result = execute("sync", root, stage="WP7", task="Certification", dependencies=deps())
    assert result.exit_code == SUCCESS and result.payload["writes_performed"] is True
    manifest = verify_public_snapshot(root)
    state = json.loads((root / ".ai/STATE.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == state["run_id"] == "engine-run"
    assert state["test_evidence_summary"][0]["verification"] == "verified"
    assert set(result.payload["publication"]["published_paths"]) == set(manifest["published_paths"])
    assert _git_metadata(root) == git_before
    assert (root / "README.md").read_bytes() == source_before
    assert not (root / ".ai/.sync-tmp").exists()
