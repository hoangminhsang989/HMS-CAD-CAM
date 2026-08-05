"""Journaled MANIFEST-last publication for AI Sync Engine V1.1.

The publisher changes only the eight public output paths rendered by WP4 and
its private transaction area under ``.ai/.sync-tmp``.  It deliberately has no
Git, test-execution, network, or source-tree mutation capability.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any

from .checkpoint import CheckpointError, create_checkpoint_exclusive
from .config import EXACT_OUTPUT_ALLOWLIST
from .models import (
    ENGINE_VERSION,
    MANIFEST_SCHEMA_VERSION,
    MINIMUM_READER_VERSION,
    STATE_SCHEMA_VERSION,
    SUPPORTED_CAPABILITIES,
    CapabilitySet,
    PublicationResult,
    PublicationStatus,
    VersionInfo,
    validate_utc_datetime,
)
from .renderers import RenderedArtifact, RenderedOutputSet, validate_rendered_outputs


MUTABLE_PUBLICATION_ORDER = (
    ".ai/STATE.json",
    ".ai/CURRENT_STATUS.md",
    ".ai/NEXT_TASK.md",
    ".ai/SESSION.json",
    ".ai/METRICS.json",
    ".ai/HANDOFF/TO_CHATGPT.md",
)
MANIFEST_PATH = ".ai/MANIFEST.json"
TRANSACTION_ROOT = ".ai/.sync-tmp"
LOCK_PATH = f"{TRANSACTION_ROOT}/LOCK"
JOURNAL_PATH = f"{TRANSACTION_ROOT}/journal.json"


class PublisherError(RuntimeError):
    """Stable publication failure with an exit-policy-compatible code."""

    def __init__(self, code: str, message: str, *, recovery_required: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.recovery_required = recovery_required


class PublicationCrash(BaseException):
    """Fault-injection-only abrupt interruption that bypasses rollback."""


@dataclass(frozen=True, slots=True)
class PublicationHooks:
    """Injectable verification and fault boundaries used by integration tests."""

    fingerprint: Callable[[], str] | None = None
    fault: Callable[[str], None] | None = None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink() or _is_reparse(path):
        raise PublisherError("SAFETY_UNSAFE_TARGET", "publication target is not a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _safe_target(root: Path, relative: str, *, allow_internal: bool = False) -> Path:
    allowed = relative.startswith(f"{TRANSACTION_ROOT}/") if allow_internal else (
        relative in EXACT_OUTPUT_ALLOWLIST[:-1] or relative.startswith(".ai/CHECKPOINTS/")
    )
    if not allowed or "\\" in relative or relative.startswith("/") or any(
        part in {"", ".", ".."} or ":" in part for part in relative.split("/")
    ):
        raise PublisherError("SAFETY_PATH_NOT_ALLOWED", "publication path violates the exact allowlist")
    target = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/")[:-1]:
        current = current / part
        if current.exists() and (current.is_symlink() or _is_reparse(current) or not current.is_dir()):
            raise PublisherError("SAFETY_REPARSE_POINT", "publication path contains an unsafe component")
    try:
        target.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as error:
        raise PublisherError("SAFETY_PATH_ESCAPE", "publication path escapes repository root") from error
    if target.exists() and (target.is_symlink() or _is_reparse(target) or not target.is_file()):
        raise PublisherError("SAFETY_UNSAFE_TARGET", "publication target is unsafe")
    return target


def _mkdir_safe(root: Path, directory: Path) -> None:
    relative_parts = directory.relative_to(root).parts
    current = root
    for part in relative_parts:
        current = current / part
        if current.exists():
            if current.is_symlink() or _is_reparse(current) or not current.is_dir():
                raise PublisherError("SAFETY_REPARSE_POINT", "transaction directory is unsafe")
        else:
            current.mkdir()


def _write_durable(path: Path, content: bytes, *, fault: Callable[[str], None] | None, point: str) -> None:
    if fault is not None:
        fault("temp_create")
    with path.open("xb") as stream:
        if fault is not None:
            fault("temp_write")
        stream.write(content)
        stream.flush()
        if fault is not None:
            fault("file_flush")
        os.fsync(stream.fileno())
        if fault is not None:
            fault("fsync")
    if fault is not None:
        fault(point)


def _write_json_durable(path: Path, payload: dict[str, Any], *, fault: Callable[[str], None] | None, point: str) -> None:
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    _write_durable(path, content, fault=fault, point=point)


def _replace(source: Path, target: Path) -> None:
    if source.drive.casefold() != target.drive.casefold():
        raise PublisherError("SAFETY_CROSS_VOLUME", "atomic replacement requires one filesystem volume")
    os.replace(source, target)


def _artifact_map(outputs: RenderedOutputSet) -> dict[str, RenderedArtifact]:
    result = outputs.by_path()
    checkpoint_paths = [path for path in result if path.startswith(".ai/CHECKPOINTS/")]
    expected = set(MUTABLE_PUBLICATION_ORDER) | {MANIFEST_PATH} | set(checkpoint_paths)
    if len(checkpoint_paths) != 1 or len(outputs.artifacts) != 8 or set(result) != expected:
        raise PublisherError("PUBLICATION_ALLOWLIST_INVALID", "candidate set violates the exact output contract")
    return result


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublisherError(
                "PUBLIC_SNAPSHOT_INVALID",
                "public JSON contains a duplicate key",
            )
        result[key] = value
    return result


def _expect_keys(value: object, expected: set[str], component: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"{component} schema is invalid",
        )
    return value


def _read_public_bytes(root: Path, relative: str) -> bytes:
    target = _safe_target(root, relative)
    try:
        data = target.read_bytes()
        text = data.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as error:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "public artifact is unreadable",
        ) from error
    if (
        data.startswith(bytes((239, 187, 191)))
        or chr(13) in text
        or not data.endswith(bytes((10,)))
    ):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "public artifact encoding contract is invalid",
        )
    return data


def _parse_public_json(data: bytes, component: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
        )
    except PublisherError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"{component} JSON is invalid",
        ) from error
    if not isinstance(value, dict):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"{component} must be a JSON object",
        )
    return value


def _require_utc_timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z",
        value,
    ) is None:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"{field} timestamp is invalid",
        )
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"{field} timestamp is invalid",
        ) from error
    return value


def _markdown_identity(data: bytes, title: str, run_id: str, generated_at: str) -> str:
    text = data.decode("utf-8", errors="strict")
    lines = text.splitlines()
    marker = chr(96)
    expected = [
        f"# {title}",
        "",
        f"Run ID: {marker}{run_id}{marker}",
        f"Generated: {marker}{generated_at}{marker}",
    ]
    if lines[:4] != expected:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"{title} identity is inconsistent",
        )
    return text


def _checkpoint_value(text: str, label: str) -> str:
    prefix = f"- {label}: "
    values = [
        line[len(prefix):]
        for line in text.splitlines()
        if line.startswith(prefix)
    ]
    if len(values) != 1:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"checkpoint field {label} is missing or duplicated",
        )
    return values[0]


def _checkpoint_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    lines = text.splitlines()
    indices = [index for index, line in enumerate(lines) if line == marker]
    if len(indices) != 1:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"checkpoint section {heading} is missing or duplicated",
        )
    start = indices[0] + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return chr(10).join(lines[start:end])


def _checkpoint_subsection_items(text: str, heading: str) -> tuple[str, ...]:
    marker = f"### {heading}"
    lines = text.splitlines()
    indices = [index for index, line in enumerate(lines) if line == marker]
    if len(indices) != 1:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"checkpoint subsection {heading} is missing or duplicated",
        )
    start = indices[0] + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("### ")),
        len(lines),
    )
    content = [line for line in lines[start:end] if line]
    if any(not line.startswith("- ") or len(line) == 2 for line in content):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"checkpoint subsection {heading} is malformed",
        )
    items = tuple(line[2:] for line in content)
    if len(items) != len(set(items)):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            f"checkpoint subsection {heading} contains duplicates",
        )
    return items


def _checkpoint_escape(value: object) -> str:
    if value is None:
        return "unknown"
    return (
        str(value)
        .replace(chr(92), chr(92) + chr(92))
        .replace(chr(96), chr(92) + chr(96))
        .replace(chr(13), " ")
        .replace(chr(10), " ")
    )

def verify_public_snapshot(repository_root: Path) -> dict[str, Any]:
    """Strictly parse and cross-check all eight public snapshot artifacts."""

    root = Path(repository_root).resolve(strict=True)
    manifest_bytes = _read_public_bytes(root, MANIFEST_PATH)
    manifest = _parse_public_json(manifest_bytes, "MANIFEST")
    manifest_keys = {
        "manifest_schema_version",
        "engine_version",
        "state_schema_version",
        "minimum_reader_version",
        "created_by",
        "generated_at_utc",
        "run_id",
        "branch",
        "head_oid",
        "dirty",
        "latest_checkpoint",
        "publication_manifest_sha256",
        "published_paths",
        "artifacts",
        "capabilities",
        "reader_compatibility",
        "publication_status",
    }
    _expect_keys(manifest, manifest_keys, "MANIFEST")
    if manifest["publication_status"] != "complete":
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "public MANIFEST status is invalid",
        )
    if (
        manifest["engine_version"] != ENGINE_VERSION
        or manifest["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION
        or manifest["state_schema_version"] != STATE_SCHEMA_VERSION
        or manifest["minimum_reader_version"] != MINIMUM_READER_VERSION
    ):
        raise PublisherError(
            "PUBLIC_MANIFEST_INCOMPATIBLE",
            "public MANIFEST version is incompatible",
        )
    run_id = manifest["run_id"]
    if not isinstance(run_id, str) or not run_id:
        raise PublisherError("PUBLIC_SNAPSHOT_INVALID", "MANIFEST run ID is invalid")
    generated_at = _require_utc_timestamp(
        manifest["generated_at_utc"],
        "MANIFEST generated_at_utc",
    )
    capability_data = _expect_keys(
        manifest["capabilities"],
        {"supported", "required"},
        "MANIFEST capabilities",
    )
    supported = capability_data["supported"]
    required_capabilities = capability_data["required"]
    if (
        not isinstance(supported, list)
        or not isinstance(required_capabilities, list)
        or any(not isinstance(item, str) for item in (*supported, *required_capabilities))
        or tuple(supported) != SUPPORTED_CAPABILITIES
        or tuple(required_capabilities) != tuple(sorted(set(required_capabilities)))
        or not set(required_capabilities).issubset(SUPPORTED_CAPABILITIES)
    ):
        raise PublisherError(
            "PUBLIC_MANIFEST_INCOMPATIBLE",
            "public MANIFEST requires unsupported capabilities",
        )
    reader = _expect_keys(
        manifest["reader_compatibility"],
        {
            "state_schema_versions",
            "manifest_schema_versions",
            "minimum_reader_version",
            "optional_extensions",
        },
        "reader_compatibility",
    )
    if reader != {
        "state_schema_versions": [STATE_SCHEMA_VERSION],
        "manifest_schema_versions": [MANIFEST_SCHEMA_VERSION],
        "minimum_reader_version": MINIMUM_READER_VERSION,
        "optional_extensions": "ignore_only_when_not_required",
    }:
        raise PublisherError(
            "PUBLIC_MANIFEST_INCOMPATIBLE",
            "reader compatibility contract is invalid",
        )
    digest = manifest["publication_manifest_sha256"]
    unsigned = dict(manifest)
    unsigned.pop("publication_manifest_sha256")
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not isinstance(digest, str) or _sha256(canonical) != digest:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "public MANIFEST self-digest is invalid",
        )
    checkpoint = manifest["latest_checkpoint"]
    if not isinstance(checkpoint, str) or re.fullmatch(
        r"[.]ai/CHECKPOINTS/[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}[.]md",
        checkpoint,
    ) is None:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "public MANIFEST checkpoint path is invalid",
        )
    generated_datetime = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    if checkpoint != f".ai/CHECKPOINTS/{generated_datetime.strftime('%Y-%m-%d_%H%M%S')}.md":
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "checkpoint path does not match generation time",
        )
    expected_paths = [
        path.replace("<timestamp>", checkpoint.split("/")[-1][:-3])
        if "<timestamp>" in path
        else path
        for path in EXACT_OUTPUT_ALLOWLIST
    ]
    if manifest["published_paths"] != expected_paths:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "public MANIFEST path order or set is invalid",
        )
    expected_roles = {
        ".ai/STATE.json": "canonical_state",
        ".ai/CURRENT_STATUS.md": "derived_markdown",
        ".ai/NEXT_TASK.md": "derived_markdown",
        ".ai/SESSION.json": "derived_json",
        ".ai/METRICS.json": "derived_json",
        ".ai/HANDOFF/TO_CHATGPT.md": "derived_markdown",
        checkpoint: "checkpoint",
    }
    records = manifest["artifacts"]
    if not isinstance(records, list) or len(records) != len(expected_roles):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "public MANIFEST artifact set is invalid",
        )
    record_by_path: dict[str, dict[str, Any]] = {}
    artifact_bytes: dict[str, bytes] = {}
    for record_value in records:
        record = _expect_keys(
            record_value,
            {"path", "role", "sha256", "size_bytes", "required"},
            "MANIFEST artifact",
        )
        relative = record["path"]
        if (
            not isinstance(relative, str)
            or relative in record_by_path
            or relative not in expected_roles
            or record["role"] != expected_roles[relative]
            or record["required"] is not True
        ):
            raise PublisherError(
                "PUBLIC_SNAPSHOT_INVALID",
                "public MANIFEST artifact contract is invalid",
            )
        data = _read_public_bytes(root, relative)
        if (
            not isinstance(record["sha256"], str)
            or _sha256(data) != record["sha256"]
            or isinstance(record["size_bytes"], bool)
            or not isinstance(record["size_bytes"], int)
            or len(data) != record["size_bytes"]
        ):
            raise PublisherError(
                "PUBLIC_SNAPSHOT_INVALID",
                "public artifact hash or size is invalid",
            )
        record_by_path[relative] = record
        artifact_bytes[relative] = data
    if set(record_by_path) != set(expected_roles):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "public MANIFEST references are incomplete",
        )

    state = _parse_public_json(artifact_bytes[".ai/STATE.json"], "STATE")
    _expect_keys(
        state,
        {
            "created_by",
            "engine_version",
            "state_schema_version",
            "generated_at_utc",
            "run_id",
            "capabilities",
            "project_state",
            "git",
            "test_evidence_summary",
            "publication",
        },
        "STATE",
    )
    project_state = _expect_keys(
        state["project_state"],
        {
            "project",
            "stage",
            "status",
            "current_task",
            "remaining_work",
            "blockers",
            "blockers_state",
            "next_action",
            "stage_progress_percent",
            "overall_progress_percent",
            "provenance",
            "commit_claim_oid",
            "commit_claim_verified",
        },
        "STATE project_state",
    )
    git_state = _expect_keys(
        state["git"],
        {
            "captured_at",
            "branch",
            "unborn_branch",
            "detached",
            "head_oid",
            "upstream",
            "ahead",
            "behind",
            "dirty",
            "fingerprint_sha256",
            "remote_urls",
            "entries",
            "staged_diff",
            "unstaged_diff",
        },
        "STATE git",
    )
    publication = _expect_keys(
        state["publication"],
        {"status", "manifest_path", "latest_checkpoint"},
        "STATE publication",
    )
    if (
        state["created_by"] != manifest["created_by"]
        or state["engine_version"] != manifest["engine_version"]
        or state["state_schema_version"] != manifest["state_schema_version"]
        or state["generated_at_utc"] != generated_at
        or state["run_id"] != run_id
        or state["capabilities"] != capability_data
        or git_state["branch"] != manifest["branch"]
        or git_state["head_oid"] != manifest["head_oid"]
        or git_state["dirty"] != manifest["dirty"]
        or publication != {
            "status": "pending_manifest",
            "manifest_path": MANIFEST_PATH,
            "latest_checkpoint": checkpoint,
        }
    ):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "STATE and MANIFEST are inconsistent",
        )
    project = project_state["project"]
    if not isinstance(project, str) or not project:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "STATE project is invalid",
        )
    tests = state["test_evidence_summary"]
    if not isinstance(tests, list):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "STATE test evidence summary is invalid",
        )

    test_keys = {
        "run_id",
        "command",
        "exit_code",
        "started_at",
        "completed_at",
        "duration_seconds",
        "counts",
        "status",
        "evidence_source",
        "verification",
        "log_path",
        "log_sha256",
        "verification_issues",
    }
    for evidence in tests:
        _expect_keys(evidence, test_keys, "STATE test evidence")
        if (
            not isinstance(evidence["run_id"], str)
            or not isinstance(evidence["command"], list)
            or not all(isinstance(item, str) and item for item in evidence["command"])
            or not isinstance(evidence["counts"], dict)
            or not isinstance(evidence["verification_issues"], list)
        ):
            raise PublisherError(
                "PUBLIC_SNAPSHOT_INVALID",
                "STATE test evidence contract is invalid",
            )
        _require_utc_timestamp(evidence["started_at"], "test started_at")
        _require_utc_timestamp(evidence["completed_at"], "test completed_at")
    session = _parse_public_json(artifact_bytes[".ai/SESSION.json"], "SESSION")
    _expect_keys(
        session,
        {
            "schema_version",
            "engine_version",
            "state_schema_version",
            "created_by",
            "run_id",
            "generated_at_utc",
            "project",
            "stage",
            "status",
            "head_oid",
        },
        "SESSION",
    )
    if session != {
        "schema_version": 1,
        "engine_version": state["engine_version"],
        "state_schema_version": state["state_schema_version"],
        "created_by": state["created_by"],
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "project": project,
        "stage": project_state["stage"],
        "status": project_state["status"],
        "head_oid": git_state["head_oid"],
    }:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "SESSION and STATE are inconsistent",
        )
    metrics = _parse_public_json(artifact_bytes[".ai/METRICS.json"], "METRICS")
    _expect_keys(
        metrics,
        {
            "schema_version",
            "engine_version",
            "state_schema_version",
            "created_by",
            "run_id",
            "generated_at_utc",
            "project",
            "stage_progress_percent",
            "overall_progress_percent",
            "test_run_count",
        },
        "METRICS",
    )
    if metrics != {
        "schema_version": 1,
        "engine_version": state["engine_version"],
        "state_schema_version": state["state_schema_version"],
        "created_by": state["created_by"],
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "project": project,
        "stage_progress_percent": project_state["stage_progress_percent"],
        "overall_progress_percent": project_state["overall_progress_percent"],
        "test_run_count": len(tests),
    }:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "METRICS and STATE are inconsistent",
        )

    current_text = _markdown_identity(
        artifact_bytes[".ai/CURRENT_STATUS.md"],
        "CURRENT STATUS",
        run_id,
        generated_at,
    )
    next_text = _markdown_identity(
        artifact_bytes[".ai/NEXT_TASK.md"],
        "NEXT TASK",
        run_id,
        generated_at,
    )
    handoff_text = _markdown_identity(
        artifact_bytes[".ai/HANDOFF/TO_CHATGPT.md"],
        "HANDOFF TO CHATGPT WEB",
        run_id,
        generated_at,
    )
    for text in (current_text, next_text, handoff_text):
        if project not in text:
            raise PublisherError(
                "PUBLIC_SNAPSHOT_INVALID",
                "derived Markdown project is inconsistent",
            )

    checkpoint_text = _markdown_identity(
        artifact_bytes[checkpoint],
        "AI SYNC CHECKPOINT",
        run_id,
        generated_at,
    )
    expected_checkpoint_values = {
        "Identity": {
            "Checkpoint schema version": "1",
            "Engine version": state["engine_version"],
            "State schema version": str(state["state_schema_version"]),
            "Manifest schema version": str(manifest["manifest_schema_version"]),
            "Minimum reader version": manifest["minimum_reader_version"],
            "Created by": state["created_by"],
            "Run ID": run_id,
            "Timestamp UTC": generated_at,
        },
        "Project": {
            "Project": project,
            "Stage": "unknown" if project_state["stage"] is None else str(project_state["stage"]),
            "Status": str(project_state["status"]),
        },
        "Git": {
            "Branch": "unknown" if git_state["branch"] is None else str(git_state["branch"]),
            "HEAD": "unknown" if git_state["head_oid"] is None else str(git_state["head_oid"]),
            "Dirty": str(git_state["dirty"]).lower(),
        },
        "State binding": {
            "State SHA-256": record_by_path[".ai/STATE.json"]["sha256"],
        },
        "Commit claim": {
            "OID": "unknown" if project_state["commit_claim_oid"] is None else str(project_state["commit_claim_oid"]),
            "Verified": "unknown" if project_state["commit_claim_verified"] is None else str(project_state["commit_claim_verified"]).lower(),
        },
    }
    checkpoint_sections = {
        heading: _checkpoint_section(checkpoint_text, heading)
        for heading in expected_checkpoint_values
    }
    for heading, values in expected_checkpoint_values.items():
        for label, expected in values.items():
            if _checkpoint_value(checkpoint_sections[heading], label) != expected:
                raise PublisherError(
                    "PUBLIC_SNAPSHOT_INVALID",
                    f"checkpoint {label} is inconsistent",
                )
    entries = git_state["entries"]
    if not isinstance(entries, list):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "STATE working tree entries are invalid",
        )
    entry_keys = {
        "path",
        "original_path",
        "index_status",
        "worktree_status",
        "kind",
        "submodule_state",
    }
    for entry in entries:
        _expect_keys(entry, entry_keys, "STATE working tree entry")
    working_summary = {
        "Staged entries": sum(
            1
            for entry in entries
            if entry["index_status"] not in {".", "?", "!"}
        ),
        "Unstaged entries": sum(
            1
            for entry in entries
            if entry["worktree_status"] not in {".", "?", "!"}
        ),
        "Untracked entries": sum(
            1 for entry in entries if entry["kind"] == "untracked"
        ),
        "Renamed/copied entries": sum(
            1 for entry in entries if entry["kind"] in {"renamed", "copied"}
        ),
        "Deleted entries": sum(
            1
            for entry in entries
            if "D" in {entry["index_status"], entry["worktree_status"]}
        ),
        "Staged diff files": git_state["staged_diff"]["files_changed"],
        "Unstaged diff files": git_state["unstaged_diff"]["files_changed"],
    }
    for label, expected in working_summary.items():
        if _checkpoint_value(checkpoint_sections["Git"], label) != str(expected):
            raise PublisherError(
                "PUBLIC_SNAPSHOT_INVALID",
                "checkpoint working tree summary is inconsistent",
            )
    mutable_hashes = {
        relative: record_by_path[relative]["sha256"]
        for relative in MUTABLE_PUBLICATION_ORDER
    }
    artifact_set_sha256 = _sha256(
        json.dumps(
            mutable_hashes,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if _checkpoint_value(checkpoint_sections["State binding"], "Artifact set SHA-256") != artifact_set_sha256:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "checkpoint artifact-set hash is inconsistent",
        )
    for key, value in project_state["provenance"].items():
        if f"- {key}: {_checkpoint_escape(value)}" not in checkpoint_text:
            raise PublisherError(
                "PUBLIC_SNAPSHOT_INVALID",
                "checkpoint provenance is incomplete",
            )
    for index, evidence in enumerate(tests, start=1):
        counts_text = json.dumps(
            evidence["counts"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        command_text = json.dumps(
            evidence["command"],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        run_lines = [
            f"### Run {index}",
            "",
            f"- Run ID: {_checkpoint_escape(evidence['run_id'])}",
            f"- Command argv: {_checkpoint_escape(command_text)}",
            f"- Exit code: {_checkpoint_escape(evidence['exit_code'])}",
            f"- Started at: {evidence['started_at']}",
            f"- Completed at: {evidence['completed_at']}",
            f"- Duration seconds: {evidence['duration_seconds']}",
            f"- Counts: {_checkpoint_escape(counts_text)}",
            f"- Status: {evidence['status']}",
            f"- Evidence source: {evidence['evidence_source']}",
            f"- Verification: {evidence['verification']}",
            f"- Log path: {_checkpoint_escape(evidence['log_path'])}",
            f"- Log SHA-256: {_checkpoint_escape(evidence['log_sha256'])}",
            f"- Verification issues: {_checkpoint_escape(json.dumps(evidence['verification_issues'], ensure_ascii=False, separators=(',', ':')))}",
        ]
        if (chr(10).join(run_lines)) not in checkpoint_text:
            raise PublisherError(
                "PUBLIC_SNAPSHOT_INVALID",
                "checkpoint test evidence is inconsistent",
            )
    capability_section = _checkpoint_section(checkpoint_text, "Capabilities")
    if [
        line for line in capability_section.splitlines()
        if line.startswith("### ")
    ] != ["### Supported", "### Required"]:
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "checkpoint capability subsection structure is invalid",
        )
    checkpoint_supported = _checkpoint_subsection_items(
        capability_section,
        "Supported",
    )
    checkpoint_required = _checkpoint_subsection_items(
        capability_section,
        "Required",
    )
    if (
        checkpoint_supported != tuple(supported)
        or checkpoint_required != tuple(required_capabilities)
        or not set(checkpoint_required).issubset(checkpoint_supported)
    ):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "checkpoint capability sections are inconsistent",
        )
    if (
        digest in checkpoint_text
        or _sha256(manifest_bytes) in checkpoint_text
    ):
        raise PublisherError(
            "PUBLIC_SNAPSHOT_INVALID",
            "checkpoint must not contain a final MANIFEST hash",
        )
    return manifest

def _acquire_lock(root: Path, run_id: str, started_at: datetime) -> Path:
    transaction = _safe_target(root, LOCK_PATH, allow_internal=True).parent
    _mkdir_safe(root, transaction)
    lock = transaction / "LOCK"
    payload = (json.dumps({"run_id": run_id, "pid": os.getpid(), "created_at_utc": started_at.isoformat()}, sort_keys=True) + "\n").encode("utf-8")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise PublisherError("PUBLICATION_LOCKED", "repository publication lock already exists") from error
    return lock


def _journal_records(artifacts: dict[str, RenderedArtifact], old_hashes: dict[str, str | None]) -> list[dict[str, Any]]:
    return [
        {"path": path, "candidate_sha256": artifacts[path].sha256, "old_sha256": old_hashes[path]}
        for path in [*MUTABLE_PUBLICATION_ORDER, next(p for p in artifacts if p.startswith(".ai/CHECKPOINTS/")), MANIFEST_PATH]
    ]


def _verify_candidate(path: Path, artifact: RenderedArtifact) -> None:
    if _file_hash(path) != artifact.sha256 or path.stat().st_size != artifact.size_bytes:
        raise PublisherError("PUBLICATION_HASH_MISMATCH", "published artifact hash verification failed")


def _rollback(
    root: Path,
    records: list[dict[str, Any]],
    backup_root: Path,
    changed: list[str],
) -> tuple[str, ...]:
    rolled_back: list[str] = []
    for relative in reversed(changed):
        record = next(item for item in records if item["path"] == relative)
        target = _safe_target(root, relative)
        current = _file_hash(target)
        if current != record["candidate_sha256"]:
            raise PublisherError(
                "SAFETY_CONCURRENT_WRITE",
                "rollback refused to overwrite concurrent content",
                recovery_required=True,
            )
        old_hash = record["old_sha256"]
        backup = backup_root.joinpath(*relative.split("/"))
        if old_hash is None:
            target.unlink()
        else:
            if _file_hash(backup) != old_hash:
                raise PublisherError("PUBLICATION_BACKUP_INVALID", "rollback backup hash mismatch", recovery_required=True)
            os.replace(backup, target)
            if _file_hash(target) != old_hash:
                raise PublisherError("PUBLICATION_ROLLBACK_FAILED", "rollback hash verification failed", recovery_required=True)
        rolled_back.append(relative)
    return tuple(rolled_back)


def _cleanup_transaction(transaction: Path, lock: Path, *, fault: Callable[[str], None] | None = None) -> None:
    if fault is not None:
        fault("cleanup")
    if lock.exists():
        lock.unlink()
    for child in list(transaction.iterdir()) if transaction.exists() else []:
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        elif child.exists():
            child.unlink()
    if transaction.exists():
        transaction.rmdir()


def publish_outputs(
    repository_root: Path,
    outputs: RenderedOutputSet,
    *,
    version: VersionInfo,
    capabilities: CapabilitySet,
    started_at: datetime,
    completed_at: datetime,
    expected_fingerprint: str | None = None,
    hooks: PublicationHooks = PublicationHooks(),
) -> PublicationResult:
    """Publish a validated candidate set using a recoverable MANIFEST-last transaction."""

    validate_utc_datetime(started_at, "started_at")
    validate_utc_datetime(completed_at, "completed_at")
    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as error:
        raise PublisherError("SAFETY_REPOSITORY_INVALID", "repository root is unavailable") from error
    if not root.is_dir():
        raise PublisherError("SAFETY_REPOSITORY_INVALID", "repository root is not a directory")
    validate_rendered_outputs(outputs, private_repository_path=str(root))
    artifacts = _artifact_map(outputs)
    if expected_fingerprint is not None:
        if hooks.fingerprint is None or hooks.fingerprint() != expected_fingerprint:
            raise PublisherError("SAFETY_FINGERPRINT_CHANGED", "repository fingerprint changed before publication")
    existing_journal = _safe_target(root, JOURNAL_PATH, allow_internal=True)
    if existing_journal.exists():
        raise PublisherError("PUBLICATION_RECOVERY_REQUIRED", "an unresolved publication journal exists", recovery_required=True)
    lock = _acquire_lock(root, outputs.run_id, started_at)
    transaction = lock.parent
    run_root = transaction / outputs.run_id
    staging = run_root / "staging"
    backups = run_root / "backups"
    journal = transaction / "journal.json"
    changed: list[str] = []
    unchanged: list[str] = []
    manifest_committed = False
    records: list[dict[str, Any]] = []
    try:
        _mkdir_safe(root, staging)
        _mkdir_safe(root, backups)
        for relative, artifact in artifacts.items():
            staged = staging.joinpath(*relative.split("/"))
            _mkdir_safe(root, staged.parent)
            _write_durable(staged, artifact.content, fault=hooks.fault, point=f"staged:{relative}")
            _verify_candidate(staged, artifact)
        if hooks.fault is not None:
            hooks.fault("manifest_plan")
        old_hashes = {relative: _file_hash(_safe_target(root, relative)) for relative in artifacts}
        records = _journal_records(artifacts, old_hashes)
        for relative, old_hash in old_hashes.items():
            if old_hash is None:
                continue
            source = _safe_target(root, relative)
            backup = backups.joinpath(*relative.split("/"))
            _mkdir_safe(root, backup.parent)
            shutil.copyfile(source, backup)
            if _file_hash(backup) != old_hash:
                raise PublisherError("PUBLICATION_BACKUP_INVALID", "backup hash verification failed")
            if hooks.fault is not None:
                hooks.fault(f"backup:{relative}")
        journal_payload = {"schema_version": 1, "run_id": outputs.run_id, "status": "prepared", "records": records}
        _write_json_durable(journal, journal_payload, fault=hooks.fault, point="journal_prepared")
        if expected_fingerprint is not None and hooks.fingerprint is not None and hooks.fingerprint() != expected_fingerprint:
            raise PublisherError("SAFETY_FINGERPRINT_CHANGED", "repository fingerprint changed during preparation")

        for relative in MUTABLE_PUBLICATION_ORDER:
            artifact = artifacts[relative]
            target = _safe_target(root, relative)
            _mkdir_safe(root, target.parent)
            if _file_hash(target) != old_hashes[relative]:
                raise PublisherError("SAFETY_CONCURRENT_WRITE", "publication target changed concurrently")
            if old_hashes[relative] == artifact.sha256:
                unchanged.append(relative)
                continue
            staged = staging.joinpath(*relative.split("/"))
            _replace(staged, target)
            changed.append(relative)
            if hooks.fault is not None:
                hooks.fault(f"mutable_replace:{relative}")
            _verify_candidate(target, artifact)
            if hooks.fault is not None:
                hooks.fault(f"hash_verify:{relative}")

        checkpoint_relative = next(path for path in artifacts if path.startswith(".ai/CHECKPOINTS/"))
        checkpoint_target = _safe_target(root, checkpoint_relative)
        _mkdir_safe(root, checkpoint_target.parent)
        if checkpoint_target.exists():
            raise PublisherError("CHECKPOINT_COLLISION", "checkpoint already exists")
        try:
            create_checkpoint_exclusive(checkpoint_target, artifacts[checkpoint_relative].content, fault_hook=hooks.fault)
            changed.append(checkpoint_relative)
        except Exception:
            if checkpoint_target.exists() and _file_hash(checkpoint_target) == artifacts[checkpoint_relative].sha256:
                changed.append(checkpoint_relative)
            raise
        _verify_candidate(checkpoint_target, artifacts[checkpoint_relative])

        for relative, artifact in artifacts.items():
            if relative == MANIFEST_PATH:
                continue
            _verify_candidate(_safe_target(root, relative), artifact)
        if hooks.fault is not None:
            hooks.fault("final_artifacts_verify")

        manifest_target = _safe_target(root, MANIFEST_PATH)
        _mkdir_safe(root, manifest_target.parent)
        if _file_hash(manifest_target) != old_hashes[MANIFEST_PATH]:
            raise PublisherError("SAFETY_CONCURRENT_WRITE", "MANIFEST changed concurrently")
        staged_manifest = staging.joinpath(*MANIFEST_PATH.split("/"))
        _replace(staged_manifest, manifest_target)
        changed.append(MANIFEST_PATH)
        manifest_committed = True
        if hooks.fault is not None:
            hooks.fault("manifest_replace")
        _verify_candidate(manifest_target, artifacts[MANIFEST_PATH])
        verify_public_snapshot(root)
        if hooks.fault is not None:
            hooks.fault("manifest_verify")

        journal_payload["status"] = "committed"
        committed_temp = transaction / "journal.committed.tmp"
        _write_json_durable(committed_temp, journal_payload, fault=hooks.fault, point="journal_committed")
        os.replace(committed_temp, journal)
        _cleanup_transaction(transaction, lock, fault=hooks.fault)
        return PublicationResult(
            version, capabilities, outputs.run_id, PublicationStatus.PUBLISHED,
            started_at, completed_at, tuple(changed), tuple(unchanged), (), MANIFEST_PATH,
            outputs.manifest_self_digest, artifacts[MANIFEST_PATH].sha256, None,
        )
    except PublicationCrash:
        raise
    except Exception as error:
        if manifest_committed:
            raise PublisherError(
                "PUBLICATION_RECOVERY_REQUIRED",
                "valid MANIFEST may be public; recovery verification is required",
                recovery_required=True,
            ) from error
        rolled_back = _rollback(root, records, backups, changed) if records and changed else ()
        if MANIFEST_PATH not in changed:
            try:
                _cleanup_transaction(transaction, lock)
            except OSError as cleanup_error:
                error.add_note(f"transaction cleanup failed: {type(cleanup_error).__name__}")
        if isinstance(error, PublisherError):
            raise error
        if isinstance(error, CheckpointError):
            raise PublisherError("CHECKPOINT_CREATE_FAILED", "checkpoint publication failed") from error
        raise PublisherError("PUBLICATION_FAILED", "atomic publication failed") from error


def recover_pending_publication(repository_root: Path) -> str | None:
    """Recover one journal only when no repository publication lock exists."""

    root = Path(repository_root).resolve(strict=True)
    lock = _safe_target(root, LOCK_PATH, allow_internal=True)
    if lock.exists():
        raise PublisherError(
            "PUBLICATION_LOCKED",
            "repository publication lock exists; operator review is required",
            recovery_required=True,
        )
    journal = _safe_target(root, JOURNAL_PATH, allow_internal=True)
    if not journal.exists():
        return None
    return recover_publication(root)

def recover_publication(repository_root: Path) -> str:
    """Recover an interrupted transaction after an operator has cleared a stale lock.

    Returns ``rolled_forward`` when a valid candidate MANIFEST and every referenced
    artifact are public, otherwise restores the exact old snapshot and returns
    ``rolled_back``.  Ambiguous or concurrently changed content fails closed.
    """

    root = Path(repository_root).resolve(strict=True)
    lock = root.joinpath(*LOCK_PATH.split("/"))
    if lock.exists():
        raise PublisherError("PUBLICATION_LOCKED", "stale lock must be reviewed and cleared before recovery")
    journal = root.joinpath(*JOURNAL_PATH.split("/"))
    if not journal.is_file() or journal.is_symlink() or _is_reparse(journal):
        raise PublisherError("PUBLICATION_JOURNAL_MISSING", "no safe recovery journal exists")
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
        run_id = payload["run_id"]
        records = payload["records"]
        if payload["schema_version"] != 1 or payload["status"] not in {"prepared", "committed"}:
            raise ValueError
        if not isinstance(run_id, str) or not run_id or not isinstance(records, list):
            raise ValueError
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise PublisherError("PUBLICATION_JOURNAL_INVALID", "recovery journal is invalid", recovery_required=True) from error
    by_path = {item["path"]: item for item in records}
    manifest = by_path.get(MANIFEST_PATH)
    if manifest is None:
        raise PublisherError("PUBLICATION_JOURNAL_INVALID", "recovery journal lacks MANIFEST", recovery_required=True)
    transaction = journal.parent
    backups = transaction / run_id / "backups"
    manifest_current = _file_hash(_safe_target(root, MANIFEST_PATH))
    if manifest_current == manifest["candidate_sha256"]:
        try:
            verify_public_snapshot(root)
        except PublisherError as error:
            raise PublisherError(
                "PUBLICATION_RECOVERY_AMBIGUOUS",
                "candidate MANIFEST is public but snapshot verification failed",
                recovery_required=True,
            ) from error
        for item in records:
            if _file_hash(_safe_target(root, item["path"])) != item["candidate_sha256"]:
                raise PublisherError("PUBLICATION_RECOVERY_AMBIGUOUS", "public snapshot is incomplete", recovery_required=True)
        _cleanup_transaction(transaction, lock)
        return "rolled_forward"
    candidate_paths: list[str] = []
    for item in records:
        current = _file_hash(_safe_target(root, item["path"]))
        if current == item["old_sha256"]:
            continue
        if current == item["candidate_sha256"]:
            candidate_paths.append(item["path"])
            continue
        raise PublisherError(
            "PUBLICATION_RECOVERY_AMBIGUOUS",
            "publication target differs from both candidate and old state",
            recovery_required=True,
        )
    if MANIFEST_PATH in candidate_paths:
        raise PublisherError("PUBLICATION_RECOVERY_AMBIGUOUS", "MANIFEST recovery state is ambiguous", recovery_required=True)
    _rollback(root, records, backups, candidate_paths)
    for item in records:
        if _file_hash(_safe_target(root, item["path"])) != item["old_sha256"]:
            raise PublisherError(
                "PUBLICATION_RECOVERY_AMBIGUOUS",
                "rollback did not restore the complete old state",
                recovery_required=True,
            )
    if manifest["old_sha256"] is not None:
        try:
            verify_public_snapshot(root)
        except PublisherError as error:
            raise PublisherError(
                "PUBLICATION_RECOVERY_AMBIGUOUS",
                "restored old public snapshot failed verification",
                recovery_required=True,
            ) from error
    _cleanup_transaction(transaction, lock)
    return "rolled_back"
