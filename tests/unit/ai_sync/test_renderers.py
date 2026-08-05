"""WP4 deterministic STATE/MANIFEST/Markdown renderer tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import pytest

from tools.ai_sync.config import EXACT_OUTPUT_ALLOWLIST, load_config
from tools.ai_sync.models import CapabilitySet, DiffScope, DiffSummary, GitSnapshot, SUPPORTED_CAPABILITIES, default_version_info
from tools.ai_sync.renderers import RenderedOutputSet, RendererError, render_output_candidates, validate_rendered_outputs
from tools.ai_sync.state_builder import build_project_state, parse_metadata_bytes


NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _outputs(tmp_path: Path, checkpoint_path: str = ".ai/CHECKPOINTS/2026-08-04_000000.md"):
    config = load_config(Path(__file__).parents[3])
    git = GitSnapshot(tmp_path.resolve(), NOW, "main", False, "a" * 40, None, None, None, (), (),
                      DiffSummary(DiffScope.STAGED, 0, 0, 0, 0, ()), DiffSummary(DiffScope.UNSTAGED, 0, 0, 0, 0, ()), False, "b" * 64)
    metadata = parse_metadata_bytes(json.dumps({"schema_version": 1, "project": config.project.name, "stage": "WP4",
                                                "status": "work_in_progress", "current_task": "Render `Unicode` Việt",
                                                "blockers_state": "none_reported"}, ensure_ascii=False).encode(), expected_project=config.project.name)
    caps = CapabilitySet(SUPPORTED_CAPABILITIES, SUPPORTED_CAPABILITIES)
    state = build_project_state(config=config, git=git, tests=(), metadata=metadata, generated_at=NOW, run_id="run-1",
                                version=default_version_info(), capabilities=caps)
    return render_output_candidates(state, version=default_version_info(), capabilities=caps,
                                    checkpoint_path=checkpoint_path)


def test_same_inputs_render_byte_identical_outputs(tmp_path: Path) -> None:
    first = _outputs(tmp_path); second = _outputs(tmp_path)
    assert first == second
    assert [(item.path, item.sha256) for item in first.artifacts] == [(item.path, item.sha256) for item in second.artifacts]


def test_exact_allowlist_state_session_metrics_and_same_run(tmp_path: Path) -> None:
    outputs = _outputs(tmp_path); by_path = outputs.by_path()
    expected = {path.replace("<timestamp>", "2026-08-04_000000") for path in EXACT_OUTPUT_ALLOWLIST}
    assert set(by_path) == expected and len(by_path) == 8
    for path in (".ai/STATE.json", ".ai/SESSION.json", ".ai/METRICS.json", ".ai/MANIFEST.json"):
        assert json.loads(by_path[path].content)["run_id"] == "run-1"
    state = json.loads(by_path[".ai/STATE.json"].content)
    assert state["publication"]["status"] == "pending_manifest"
    assert "repository_root" not in by_path[".ai/STATE.json"].content.decode()


def test_manifest_self_digest_and_artifact_hashes(tmp_path: Path) -> None:
    outputs = _outputs(tmp_path); by_path = outputs.by_path(); manifest = json.loads(by_path[".ai/MANIFEST.json"].content)
    digest = manifest.pop("publication_manifest_sha256")
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == digest == outputs.manifest_self_digest
    assert ".ai/MANIFEST.json" not in {item["path"] for item in manifest["artifacts"]}
    for item in manifest["artifacts"]:
        assert by_path[item["path"]].sha256 == item["sha256"]


def test_outputs_are_utf8_lf_final_newline_and_markdown_escaped(tmp_path: Path) -> None:
    outputs = _outputs(tmp_path)
    for artifact in outputs.artifacts:
        text = artifact.content.decode("utf-8")
        assert not artifact.content.startswith(b"\xef\xbb\xbf") and "\r" not in text and text.endswith("\n")
    assert "\\`Unicode\\` Việt" in outputs.by_path()[".ai/NEXT_TASK.md"].content.decode()


def test_validation_rejects_duplicate_or_tampered_output(tmp_path: Path) -> None:
    outputs = _outputs(tmp_path)
    duplicate = RenderedOutputSet(outputs.run_id, outputs.artifacts + (outputs.artifacts[0],), outputs.manifest_self_digest)
    with pytest.raises(RendererError): validate_rendered_outputs(duplicate)
    artifact = outputs.artifacts[0]
    tampered = replace(artifact, content=artifact.content + b"x")
    changed = RenderedOutputSet(outputs.run_id, (tampered, *outputs.artifacts[1:]), outputs.manifest_self_digest)
    with pytest.raises(RendererError): validate_rendered_outputs(changed)


def test_checkpoint_path_and_private_path_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(RendererError):
        _outputs(tmp_path, "../bad.md")
    outputs = _outputs(tmp_path)
    validate_rendered_outputs(outputs, private_repository_path=str(tmp_path.resolve()))

def test_checkpoint_contains_operational_contract_fields(tmp_path: Path) -> None:
    outputs = _outputs(tmp_path)
    checkpoint = next(item for item in outputs.artifacts if item.path.startswith(".ai/CHECKPOINTS/"))
    text = checkpoint.content.decode("utf-8")
    for required in (
        "## Identity", "Checkpoint schema version: 1", "Engine version: 1.1.0",
        "State schema version: 1", "Manifest schema version: 1",
        "Minimum reader version: 1.1.0", "Created by: hms_ai_sync_engine",
        "## Capabilities", "### Supported", "### Required", "## Project",
        "### Provenance", "## Git", "HEAD:", "Dirty:",
        "### Working tree summary", "### Working tree entries", "## Test evidence",
        "State SHA-256:", "Artifact set SHA-256:", "## Remaining work",
        "Blockers state:", "## Commit claim", "OID:", "Verified:", "## Next action",
    ):
        assert required in text
    assert len(checkpoint.content) == 1621
    assert hashlib.sha256(checkpoint.content).hexdigest() == "93899c7f7dc4e4cae2cbd4f0f2f26afe5b942067eb4b05e352561b8f303d0cca"
