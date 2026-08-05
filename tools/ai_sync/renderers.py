"""Deterministic in-memory STATE/MANIFEST/Markdown candidate rendering for WP4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any

from .config import EXACT_OUTPUT_ALLOWLIST
from .models import ArtifactRole, CapabilitySet, ProjectState, TestEvidence, VersionInfo


class RendererError(ValueError):
    """A deterministic candidate rendering or validation failure."""


@dataclass(frozen=True, slots=True)
class RenderedArtifact:
    path: str
    content: bytes
    sha256: str
    size_bytes: int
    role: ArtifactRole


@dataclass(frozen=True, slots=True)
class RenderedOutputSet:
    run_id: str
    artifacts: tuple[RenderedArtifact, ...]
    manifest_self_digest: str

    def by_path(self) -> dict[str, RenderedArtifact]:
        return {artifact.path: artifact for artifact in self.artifacts}


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_payload_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _test_payload(evidence: TestEvidence) -> dict[str, Any]:
    return {
        "run_id": evidence.run_id,
        "command": list(evidence.command),
        "exit_code": evidence.exit_code,
        "started_at": _timestamp(evidence.started_at),
        "completed_at": _timestamp(evidence.completed_at),
        "duration_seconds": evidence.duration_seconds,
        "counts": {name: getattr(evidence, name) for name in ("passed", "failed", "skipped", "deselected", "xfailed", "xpassed", "warnings")},
        "status": evidence.status.value,
        "evidence_source": evidence.evidence_source.value,
        "verification": evidence.verification.value,
        "log_path": evidence.log_path,
        "log_sha256": evidence.log_sha256,
        "verification_issues": list(evidence.verification_issues),
    }


def _git_payload(state: ProjectState) -> dict[str, Any]:
    git = state.git
    return {
        "captured_at": _timestamp(git.captured_at),
        "branch": git.branch,
        "unborn_branch": git.unborn_branch,
        "detached": git.is_detached,
        "head_oid": git.head_oid,
        "upstream": git.upstream,
        "ahead": git.ahead,
        "behind": git.behind,
        "dirty": git.is_dirty,
        "fingerprint_sha256": git.fingerprint_sha256,
        "remote_urls": [{"name": name, "url": url} for name, url in git.remote_urls],
        "entries": [
            {"path": item.path, "original_path": item.original_path, "index_status": item.index_status,
             "worktree_status": item.worktree_status, "kind": item.kind.value, "submodule_state": item.submodule_state}
            for item in git.entries
        ],
        "staged_diff": _diff_payload(git.staged_diff),
        "unstaged_diff": _diff_payload(git.unstaged_diff),
    }


def _diff_payload(summary) -> dict[str, Any]:
    return {
        "files_changed": summary.files_changed, "insertions": summary.insertions,
        "deletions": summary.deletions, "binary_files": summary.binary_files,
        "entries": [{"path": item.path, "original_path": item.original_path, "insertions": item.insertions,
                     "deletions": item.deletions, "binary": item.binary} for item in summary.entries],
    }


def _state_payload(state: ProjectState, version: VersionInfo, capabilities: CapabilitySet, checkpoint_path: str) -> dict[str, Any]:
    return {
        "created_by": version.created_by,
        "engine_version": version.engine_version,
        "state_schema_version": version.state_schema_version,
        "generated_at_utc": _timestamp(state.generated_at),
        "run_id": state.run_id,
        "capabilities": {"supported": list(capabilities.supported), "required": list(capabilities.required)},
        "project_state": {
            "project": state.project_name, "stage": state.stage, "status": state.status.value,
            "current_task": state.current_task, "remaining_work": list(state.remaining_work),
            "blockers": list(state.blockers), "blockers_state": state.blockers_state.value,
            "next_action": state.next_action, "stage_progress_percent": state.stage_progress_percent,
            "overall_progress_percent": state.overall_progress_percent,
            "provenance": dict(state.provenance), "commit_claim_oid": state.commit_claim_oid,
            "commit_claim_verified": state.commit_claim_verified,
        },
        "git": _git_payload(state),
        "test_evidence_summary": [_test_payload(item) for item in state.tests],
        "publication": {"status": "pending_manifest", "manifest_path": ".ai/MANIFEST.json", "latest_checkpoint": checkpoint_path},
    }


def _md(value: str | None) -> str:
    if value is None:
        return "unknown"
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("\r", " ").replace("\n", " ")


def _markdown(title: str, state: ProjectState, body: list[str]) -> bytes:
    lines = [f"# {title}", "", f"Run ID: `{state.run_id}`", f"Generated: `{_timestamp(state.generated_at)}`", "", *body]
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _artifact(path: str, content: bytes, role: ArtifactRole) -> RenderedArtifact:
    return RenderedArtifact(path, content, hashlib.sha256(content).hexdigest(), len(content), role)


def _checkpoint_bytes(
    state: ProjectState,
    *,
    version: VersionInfo,
    capabilities: CapabilitySet,
    state_sha256: str,
    artifact_set_sha256: str,
) -> bytes:
    staged_count = sum(1 for item in state.git.entries if item.is_staged)
    unstaged_count = sum(
        1
        for item in state.git.entries
        if item.worktree_status not in {".", "?", "!"}
    )
    untracked_count = sum(
        1 for item in state.git.entries if item.kind.value == "untracked"
    )
    renamed_count = sum(
        1 for item in state.git.entries if item.kind.value in {"renamed", "copied"}
    )
    deleted_count = sum(
        1
        for item in state.git.entries
        if "D" in {item.index_status, item.worktree_status}
    )
    capability_lines = [
        "### Supported",
        "",
        *[f"- {item}" for item in capabilities.supported],
        "",
        "### Required",
        "",
        *[f"- {item}" for item in capabilities.required],
    ]
    provenance_lines = [
        f"- {key}: {_md(value)}"
        for key, value in state.provenance
    ] or ["- unknown"]
    entry_lines = [
        (
            f"- Path={_md(item.path)}; Kind={item.kind.value}; "
            f"Index={item.index_status}; Worktree={item.worktree_status}; "
            f"Original={_md(item.original_path)}"
        )
        for item in state.git.entries
    ] or ["- none"]
    test_lines: list[str] = []
    for index, evidence in enumerate(state.tests, start=1):
        counts = {
            name: getattr(evidence, name)
            for name in (
                "passed",
                "failed",
                "skipped",
                "deselected",
                "xfailed",
                "xpassed",
                "warnings",
            )
        }
        test_lines.extend(
            [
                f"### Run {index}",
                "",
                f"- Run ID: {_md(evidence.run_id)}",
                f"- Command argv: {_md(json.dumps(list(evidence.command), ensure_ascii=False, separators=(',', ':')))}",
                f"- Exit code: {_md(None if evidence.exit_code is None else str(evidence.exit_code))}",
                f"- Started at: {_timestamp(evidence.started_at)}",
                f"- Completed at: {_timestamp(evidence.completed_at)}",
                f"- Duration seconds: {evidence.duration_seconds}",
                f"- Counts: {_md(json.dumps(counts, ensure_ascii=False, sort_keys=True, separators=(',', ':')))}",
                f"- Status: {evidence.status.value}",
                f"- Evidence source: {evidence.evidence_source.value}",
                f"- Verification: {evidence.verification.value}",
                f"- Log path: {_md(evidence.log_path)}",
                f"- Log SHA-256: {_md(evidence.log_sha256)}",
                f"- Verification issues: {_md(json.dumps(list(evidence.verification_issues), ensure_ascii=False, separators=(',', ':')))}",
                "",
            ]
        )
    if not test_lines:
        test_lines = ["- none", ""]
    body = [
        "## Identity",
        "",
        "- Checkpoint schema version: 1",
        f"- Engine version: {version.engine_version}",
        f"- State schema version: {version.state_schema_version}",
        f"- Manifest schema version: {version.manifest_schema_version}",
        f"- Minimum reader version: {version.minimum_reader_version}",
        f"- Created by: {version.created_by}",
        f"- Run ID: {state.run_id}",
        f"- Timestamp UTC: {_timestamp(state.generated_at)}",
        "",
        "## Capabilities",
        "",
        *capability_lines,
        "",
        "## Project",
        "",
        f"- Project: {_md(state.project_name)}",
        f"- Stage: {_md(state.stage)}",
        f"- Status: {state.status.value}",
        "",
        "### Provenance",
        "",
        *provenance_lines,
        "",
        "## Git",
        "",
        f"- Branch: {_md(state.git.branch)}",
        f"- HEAD: {_md(state.git.head_oid)}",
        f"- Dirty: {str(state.git.is_dirty).lower()}",
        "",
        "### Working tree summary",
        "",
        f"- Staged entries: {staged_count}",
        f"- Unstaged entries: {unstaged_count}",
        f"- Untracked entries: {untracked_count}",
        f"- Renamed/copied entries: {renamed_count}",
        f"- Deleted entries: {deleted_count}",
        f"- Staged diff files: {state.git.staged_diff.files_changed}",
        f"- Unstaged diff files: {state.git.unstaged_diff.files_changed}",
        "",
        "### Working tree entries",
        "",
        *entry_lines,
        "",
        "## Test evidence",
        "",
        *test_lines,
        "## Remaining work",
        "",
        *([f"- {_md(item)}" for item in state.remaining_work] or ["- unknown"]),
        "",
        "## Blockers",
        "",
        f"- Blockers state: {state.blockers_state.value}",
        *([f"- {_md(item)}" for item in state.blockers] or ["- unknown"]),
        "",
        "## Next action",
        "",
        _md(state.next_action),
        "",
        "## State binding",
        "",
        f"- State SHA-256: {state_sha256}",
        f"- Artifact set SHA-256: {artifact_set_sha256}",
        "",
        "## Commit claim",
        "",
        f"- OID: {_md(state.commit_claim_oid)}",
        f"- Verified: {_md(None if state.commit_claim_verified is None else str(state.commit_claim_verified).lower())}",
    ]
    return _markdown("AI SYNC CHECKPOINT", state, body)

def render_output_candidates(
    state: ProjectState,
    *,
    version: VersionInfo,
    capabilities: CapabilitySet,
    checkpoint_path: str,
) -> RenderedOutputSet:
    if not re.fullmatch(r"\.ai/CHECKPOINTS/\d{4}-\d{2}-\d{2}_\d{6}\.md", checkpoint_path):
        raise RendererError("checkpoint path is invalid")
    state_bytes = _json_payload_bytes(_state_payload(state, version, capabilities, checkpoint_path))
    session = _json_payload_bytes({"schema_version": 1, "engine_version": version.engine_version, "state_schema_version": version.state_schema_version,
                                   "created_by": version.created_by, "run_id": state.run_id, "generated_at_utc": _timestamp(state.generated_at),
                                   "project": state.project_name, "stage": state.stage, "status": state.status.value, "head_oid": state.git.head_oid})
    metrics = _json_payload_bytes({"schema_version": 1, "engine_version": version.engine_version, "state_schema_version": version.state_schema_version,
                                   "created_by": version.created_by, "run_id": state.run_id, "generated_at_utc": _timestamp(state.generated_at),
                                   "project": state.project_name, "stage_progress_percent": state.stage_progress_percent, "overall_progress_percent": state.overall_progress_percent,
                                   "test_run_count": len(state.tests)})
    current = _markdown("CURRENT STATUS", state, ["## Project", "", _md(state.project_name), "", "## Status", "", _md(state.status.value),
                                                        "", "## Git", "", f"- Branch: `{_md(state.git.branch)}`", f"- HEAD: `{_md(state.git.head_oid)}`", f"- Dirty: `{str(state.git.is_dirty).lower()}`"])
    next_task = _markdown("NEXT TASK", state, ["## Project", "", _md(state.project_name), "", "## Current task", "", _md(state.current_task), "", "## Next action", "", _md(state.next_action),
                                                       "", "## Remaining work", "", *([f"- {_md(item)}" for item in state.remaining_work] or ["- unknown"])])
    handoff = _markdown("HANDOFF TO CHATGPT WEB", state, ["## Project", "", _md(state.project_name), "", "## Status", "", _md(state.status.value), "", "## Test evidence", "",
                                                                    f"- Recorded runs: {len(state.tests)}", "", "## Blockers", "",
                                                                    f"- State: `{state.blockers_state.value}`", *[f"- {_md(item)}" for item in state.blockers]])
    mutable = [
        _artifact(".ai/STATE.json", state_bytes, ArtifactRole.CANONICAL_STATE),
        _artifact(".ai/CURRENT_STATUS.md", current, ArtifactRole.DERIVED_MARKDOWN),
        _artifact(".ai/NEXT_TASK.md", next_task, ArtifactRole.DERIVED_MARKDOWN),
        _artifact(".ai/SESSION.json", session, ArtifactRole.DERIVED_JSON),
        _artifact(".ai/METRICS.json", metrics, ArtifactRole.DERIVED_JSON),
        _artifact(".ai/HANDOFF/TO_CHATGPT.md", handoff, ArtifactRole.DERIVED_MARKDOWN),
    ]
    artifact_set = hashlib.sha256(_canonical_bytes({item.path: item.sha256 for item in mutable})).hexdigest()
    checkpoint = _checkpoint_bytes(
        state,
        version=version,
        capabilities=capabilities,
        state_sha256=mutable[0].sha256,
        artifact_set_sha256=artifact_set,
    )
    non_manifest = [*mutable, _artifact(checkpoint_path, checkpoint, ArtifactRole.CHECKPOINT)]
    artifact_records = [{"path": item.path, "role": item.role.value, "sha256": item.sha256, "size_bytes": item.size_bytes, "required": True} for item in non_manifest]
    published_paths = [path.replace("<timestamp>", checkpoint_path.split("/")[-1][:-3]) if "<timestamp>" in path else path for path in EXACT_OUTPUT_ALLOWLIST]
    manifest_base = {
        "manifest_schema_version": version.manifest_schema_version, "engine_version": version.engine_version,
        "state_schema_version": version.state_schema_version, "minimum_reader_version": version.minimum_reader_version,
        "created_by": version.created_by, "generated_at_utc": _timestamp(state.generated_at), "run_id": state.run_id,
        "branch": state.git.branch, "head_oid": state.git.head_oid, "dirty": state.git.is_dirty,
        "latest_checkpoint": checkpoint_path, "published_paths": published_paths, "artifacts": artifact_records,
        "capabilities": {"supported": list(capabilities.supported), "required": list(capabilities.required)},
        "reader_compatibility": {"state_schema_versions": [version.state_schema_version], "manifest_schema_versions": [version.manifest_schema_version],
                                 "minimum_reader_version": version.minimum_reader_version, "optional_extensions": "ignore_only_when_not_required"},
        "publication_status": "complete",
    }
    self_digest = hashlib.sha256(_canonical_bytes(manifest_base)).hexdigest()
    manifest = dict(manifest_base); manifest["publication_manifest_sha256"] = self_digest
    artifacts = tuple(sorted([*non_manifest, _artifact(".ai/MANIFEST.json", _json_payload_bytes(manifest), ArtifactRole.DERIVED_JSON)], key=lambda item: item.path))
    result = RenderedOutputSet(state.run_id, artifacts, self_digest)
    validate_rendered_outputs(result, private_repository_path=str(state.git.repository_root))
    return result


def validate_rendered_outputs(outputs: RenderedOutputSet, *, private_repository_path: str | None = None) -> None:
    by_path = outputs.by_path()
    expected = set(EXACT_OUTPUT_ALLOWLIST[:-1]) | {next(path for path in by_path if path.startswith(".ai/CHECKPOINTS/"))}
    if set(by_path) != expected or len(by_path) != 8 or len(outputs.artifacts) != 8:
        raise RendererError("rendered output set violates exact allowlist")
    for artifact in outputs.artifacts:
        if hashlib.sha256(artifact.content).hexdigest() != artifact.sha256 or artifact.size_bytes != len(artifact.content):
            raise RendererError("artifact hash/size mismatch")
        text = artifact.content.decode("utf-8", errors="strict")
        if artifact.content.startswith(b"\xef\xbb\xbf") or "\r" in text or not text.endswith("\n"):
            raise RendererError("artifact encoding/newline contract failed")
        if private_repository_path and private_repository_path in text:
            raise RendererError("private repository path leaked")
        if re.search(r"(?i)(token|password|secret)=([^<\s][^\s]*)", text):
            raise RendererError("secret-like value leaked")
        if artifact.path.endswith(".json"):
            json.loads(text)
        elif not text.startswith("# "):
            raise RendererError("Markdown heading is missing")
    manifest = json.loads(by_path[".ai/MANIFEST.json"].content)
    digest = manifest.pop("publication_manifest_sha256")
    if hashlib.sha256(_canonical_bytes(manifest)).hexdigest() != digest or digest != outputs.manifest_self_digest:
        raise RendererError("MANIFEST self digest mismatch")
    if manifest["run_id"] != outputs.run_id or manifest["publication_status"] != "complete":
        raise RendererError("MANIFEST run/status mismatch")
    for record in manifest["artifacts"]:
        artifact = by_path.get(record["path"])
        if artifact is None or artifact.sha256 != record["sha256"] or artifact.size_bytes != record["size_bytes"]:
            raise RendererError("MANIFEST artifact reference mismatch")
