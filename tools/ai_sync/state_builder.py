"""Strict metadata and immutable ProjectState construction for WP4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import PureWindowsPath
import re
from typing import Any

from .config import AiSyncConfig
from .models import (
    BlockersState,
    CapabilitySet,
    GitSnapshot,
    ProjectState,
    ProjectStatus,
    TestEvidence,
    VersionInfo,
    validate_percentage,
    validate_utc_datetime,
)


METADATA_SCHEMA_VERSION = 1
_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SECRET_RE = re.compile(r"(?i)(token|password|secret|credential|api[_-]?key)(?:=|:)")


class MetadataError(ValueError):
    """A strict metadata parsing or state-construction failure."""


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    schema_version: int
    project: str
    stage: str | None
    status: ProjectStatus
    current_task: str | None
    remaining_work: tuple[str, ...]
    blockers: tuple[str, ...]
    blockers_state: BlockersState
    next_action: str | None
    stage_progress_percent: float | None
    overall_progress_percent: float | None
    provenance: tuple[tuple[str, str], ...]
    commit_claim_oid: str | None


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MetadataError("Metadata contains duplicate JSON key")
        result[key] = value
    return result


def _safe_text(value: object, field: str, *, nullable: bool = True) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise MetadataError(f"{field} must be a non-empty string or null")
    if _SECRET_RE.search(value) or PureWindowsPath(value).drive or value.startswith(("/", "\\")):
        raise MetadataError(f"{field} contains unsafe private data")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise MetadataError(f"{field} must be an array")
    result = tuple(_safe_text(item, field, nullable=False) for item in value)
    return tuple(item for item in result if item is not None)


def empty_metadata(project: str) -> ProjectMetadata:
    return ProjectMetadata(
        METADATA_SCHEMA_VERSION, project, None, ProjectStatus.UNKNOWN, None, (), (),
        BlockersState.UNKNOWN, None, None, None, (), None,
    )


def parse_metadata_bytes(data: bytes, *, expected_project: str) -> ProjectMetadata:
    if data.startswith(b"\xef\xbb\xbf"):
        raise MetadataError("Metadata must be UTF-8 without BOM")
    try:
        decoded = json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=_pairs)
    except MetadataError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MetadataError("Metadata JSON is invalid") from error
    if not isinstance(decoded, dict):
        raise MetadataError("Metadata must be an object")
    allowed = {
        "schema_version", "project", "stage", "status", "current_task", "remaining_work",
        "blockers", "blockers_state", "next_action", "stage_progress_percent",
        "overall_progress_percent", "provenance", "commit_claim",
    }
    if decoded.keys() - allowed:
        raise MetadataError("Metadata contains unsupported field")
    schema = decoded.get("schema_version", METADATA_SCHEMA_VERSION)
    if isinstance(schema, bool) or schema != METADATA_SCHEMA_VERSION:
        raise MetadataError("Metadata schema is unsupported")
    project = decoded.get("project", expected_project)
    if project != expected_project:
        raise MetadataError("Metadata project does not match config")
    try:
        status = ProjectStatus(decoded.get("status", "unknown"))
        blockers_state = BlockersState(decoded.get("blockers_state", "unknown"))
    except (TypeError, ValueError) as error:
        raise MetadataError("Metadata status is invalid") from error
    blockers = _strings(decoded.get("blockers"), "blockers")
    if blockers_state is BlockersState.PRESENT and not blockers:
        raise MetadataError("present blockers_state requires blockers")
    if blockers_state is not BlockersState.PRESENT and blockers:
        raise MetadataError("blockers require present blockers_state")
    provenance_raw = decoded.get("provenance", {})
    if not isinstance(provenance_raw, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in provenance_raw.items()
    ):
        raise MetadataError("provenance must be a string mapping")
    commit_raw = decoded.get("commit_claim")
    commit_oid: str | None = None
    if commit_raw is not None:
        if not isinstance(commit_raw, dict) or set(commit_raw) != {"oid"}:
            raise MetadataError("commit_claim must contain only oid")
        commit_oid = commit_raw["oid"]
        if not isinstance(commit_oid, str) or _OID_RE.fullmatch(commit_oid) is None:
            raise MetadataError("commit claim OID is invalid")
    try:
        stage_progress = validate_percentage(decoded.get("stage_progress_percent"), "stage_progress_percent")
        overall_progress = validate_percentage(decoded.get("overall_progress_percent"), "overall_progress_percent")
    except (TypeError, ValueError) as error:
        raise MetadataError("Metadata progress is invalid") from error
    return ProjectMetadata(
        schema_version=METADATA_SCHEMA_VERSION,
        project=project,
        stage=_safe_text(decoded.get("stage"), "stage"),
        status=status,
        current_task=_safe_text(decoded.get("current_task"), "current_task"),
        remaining_work=_strings(decoded.get("remaining_work"), "remaining_work"),
        blockers=blockers,
        blockers_state=blockers_state,
        next_action=_safe_text(decoded.get("next_action"), "next_action"),
        stage_progress_percent=stage_progress,
        overall_progress_percent=overall_progress,
        provenance=tuple(sorted(provenance_raw.items())),
        commit_claim_oid=commit_oid,
    )


def build_project_state(
    *,
    config: AiSyncConfig,
    git: GitSnapshot,
    tests: tuple[TestEvidence, ...],
    metadata: ProjectMetadata,
    generated_at: datetime,
    run_id: str,
    version: VersionInfo,
    capabilities: CapabilitySet,
    verified_commit_oids: frozenset[str] = frozenset(),
) -> ProjectState:
    validate_utc_datetime(generated_at, "generated_at")
    if not isinstance(run_id, str) or not run_id:
        raise MetadataError("run_id is invalid")
    if metadata.project != config.project.name:
        raise MetadataError("Metadata project does not match config")
    if version.state_schema_version != config.version.state_schema_version:
        raise MetadataError("State schema conflicts with config")
    if not set(capabilities.required).issubset(capabilities.supported):
        raise MetadataError("Required capability is unsupported")
    commit_verified = None
    if metadata.commit_claim_oid is not None:
        commit_verified = metadata.commit_claim_oid in verified_commit_oids
    provenance = dict(metadata.provenance)
    provenance.update({"git": "verified_git_snapshot", "tests": "parsed_test_evidence" if tests else "no_evidence"})
    return ProjectState(
        state_schema_version=version.state_schema_version,
        run_id=run_id,
        project_name=config.project.name,
        generated_at=generated_at,
        stage=metadata.stage,
        status=metadata.status,
        current_task=metadata.current_task,
        git=git,
        tests=tuple(sorted(tests, key=lambda item: (item.started_at, item.run_id))),
        remaining_work=metadata.remaining_work,
        blockers=metadata.blockers,
        next_action=metadata.next_action,
        stage_progress_percent=metadata.stage_progress_percent,
        overall_progress_percent=metadata.overall_progress_percent,
        provenance=tuple(sorted(provenance.items())),
        blockers_state=metadata.blockers_state,
        commit_claim_oid=metadata.commit_claim_oid,
        commit_claim_verified=commit_verified,
    )
