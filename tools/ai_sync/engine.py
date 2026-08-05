"""Read-only inspection pipeline and explicitly bounded sync orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from uuid import uuid4

from .config import AiSyncConfig, ConfigError, load_config
from .git_reader import (
    CommitVerificationStatus,
    GitReaderError,
    NotGitRepositoryError,
    capture_git_snapshot,
    fingerprint_git_snapshot,
    resolve_repository_root,
    verify_commit,
)
from .models import GitSnapshot, PublicationStatus, WorkingTreeKind, validate_utc_datetime
from .publisher import PublicationHooks, PublisherError, publish_outputs, recover_pending_publication
from .renderers import RenderedOutputSet, RendererError, render_output_candidates
from .state_builder import MetadataError, build_project_state, empty_metadata, parse_metadata_bytes
from .test_results import TestEvidenceError, load_test_results


SUCCESS = 0
CLI_ERROR = 2
CONFIG_INVALID = 3
NOT_GIT_REPOSITORY = 4
GIT_READ_FAILED = 5
TEST_EVIDENCE_INVALID = 6
VALIDATION_FAILED = 7
PUBLICATION_FAILED = 8
SAFETY_BOUNDARY_VIOLATION = 9

COMMANDS = ("inspect", "validate", "show-plan", "sync")
_SECRET_RE = re.compile(r"(?i)(token|password|secret|api[_-]?key)=([^&\s]+)")
_URL_USERINFO_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_METADATA_MAX_BYTES = 1024 * 1024
_WINDOWS_REPARSE_POINT = 0x0400


@dataclass(frozen=True, slots=True)
class EngineDependencies:
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    run_id: Callable[[], str] = lambda: str(uuid4())
    log_sink: Callable[[str], None] | None = None
    publisher: Callable[..., Any] = publish_outputs
    recovery: Callable[[Path], str | None] = recover_pending_publication


@dataclass(frozen=True, slots=True)
class EngineResult:
    exit_code: int
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedRun:
    config: AiSyncConfig
    git: GitSnapshot
    outputs: RenderedOutputSet
    generated_at: datetime
    run_id: str
    evidence_present: bool
    evidence_run_count: int
    metadata_sha256: str | None = None
    metadata_mode: str = "none"


@dataclass(frozen=True, slots=True)
class MetadataBinding:
    """Parsed metadata together with a non-path-bearing content binding."""

    metadata: Any
    sha256: str | None
    mode: str


@dataclass(frozen=True, slots=True)
class InspectedRun:
    config: AiSyncConfig
    git: GitSnapshot
    generated_at: datetime
    run_id: str


def _sanitize(value: object) -> object:
    if isinstance(value, str):
        return _SECRET_RE.sub(r"\1=<redacted>", _URL_USERINFO_RE.sub(r"\g<scheme><redacted>@", value))
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if any(part in str(key).casefold() for part in ("secret", "token", "password", "credential")) else _sanitize(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, Path):
        return "<path>"
    return value


def _emit(dependencies: EngineDependencies, *, run_id: str, timestamp: datetime, event: str, severity: str,
          component: str, message: str, details: dict[str, Any] | None = None) -> None:
    if dependencies.log_sink is None:
        return
    record = {
        "run_id": run_id, "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "event": event, "severity": severity, "component": component,
        "message": _sanitize(message), "details": _sanitize(details or {}),
    }
    dependencies.log_sink(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _checkpoint_path(generated_at: datetime) -> str:
    return f".ai/CHECKPOINTS/{generated_at.strftime('%Y-%m-%d_%H%M%S')}.md"


def _has_reparse_ancestor(path: Path) -> bool:
    """Reject a symlink, junction, or other Windows reparse point in ``path``."""

    for candidate in (path, *path.parents):
        try:
            information = candidate.lstat()
        except OSError:
            return True
        attributes = getattr(information, "st_file_attributes", 0)
        if stat.S_ISLNK(information.st_mode) or attributes & _WINDOWS_REPARSE_POINT:
            return True
    return False


def _is_unsafe_external_metadata_spelling(path: Path) -> bool:
    """Reject Windows names that cannot denote an explicit regular authority file."""

    raw = os.fspath(path)
    if raw.startswith(("\\\\?\\", "\\\\.\\", "\\\\", "//")):
        return True
    drive, _ = os.path.splitdrive(raw)
    return ":" in raw[len(drive):]


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _read_metadata_file(config: AiSyncConfig, metadata_path: Path | str) -> MetadataBinding:
    """Read one safe metadata file once and bind the exact parsed bytes by SHA-256."""

    candidate = Path(metadata_path)
    external = candidate.is_absolute()
    if not external:
        candidate = config.repository_root / candidate
    try:
        if external and _is_unsafe_external_metadata_spelling(candidate):
            raise OSError("unsafe metadata spelling")
        if _has_reparse_ancestor(candidate):
            raise OSError("metadata path contains a reparse point")
        resolved = candidate.resolve(strict=True)
        if not external and not _is_within(resolved, config.repository_root):
            raise OSError("metadata path escapes repository")
        if _is_within(resolved, config.repository_root / ".git"):
            raise OSError("metadata path is Git metadata")
        before = resolved.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > _METADATA_MAX_BYTES:
            raise OSError("unsafe metadata file")
        data = resolved.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        after = resolved.stat()
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after:
            raise OSError("metadata changed while reading")
    except (OSError, ValueError) as error:
        raise MetadataError("metadata path is unavailable or unsafe") from error
    return MetadataBinding(
        metadata=parse_metadata_bytes(data, expected_project=config.project.name),
        sha256=digest,
        mode="external_file" if external else "repository_file",
    )


def _validate_expected_metadata_sha256(value: str | None) -> None:
    if value is not None and _SHA256_RE.fullmatch(value) is None:
        raise MetadataError("expected metadata SHA-256 must be 64 lowercase hexadecimal characters")


def _optimistic_fingerprint(snapshot: GitSnapshot) -> str:
    """Exclude only the publisher's private transaction namespace."""

    entries = tuple(
        entry for entry in snapshot.entries
        if entry.path != ".ai/.sync-tmp" and not entry.path.startswith(".ai/.sync-tmp/")
    )
    normalized = replace(
        snapshot, entries=entries,
        is_dirty=any(entry.kind is not WorkingTreeKind.IGNORED for entry in entries),
        fingerprint_sha256="0" * 64,
    )
    return fingerprint_git_snapshot(normalized)


def _metadata(
    config: AiSyncConfig,
    *,
    metadata_path: Path | str | None,
    stage: str | None,
    task: str | None,
) -> MetadataBinding:
    if metadata_path is not None and (stage is not None or task is not None):
        raise MetadataError("metadata file conflicts with inline metadata")
    if metadata_path is not None:
        return _read_metadata_file(config, metadata_path)
    if stage is None and task is None:
        return MetadataBinding(empty_metadata(config.project.name), None, "none")
    payload = {"schema_version": 1, "project": config.project.name}
    if stage is not None:
        payload["stage"] = stage
    if task is not None:
        payload["current_task"] = task
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return MetadataBinding(
        parse_metadata_bytes(data, expected_project=config.project.name),
        hashlib.sha256(data).hexdigest(),
        "inline",
    )


def _resolve_config(repository: Path, config_path: Path | str) -> tuple[Path, AiSyncConfig]:
    root = resolve_repository_root(repository)
    return root, load_config(root, config_path)


def _capture_inspection(
    root: Path,
    config: AiSyncConfig,
    *,
    generated_at: datetime,
    run_id: str,
    expected_head: str | None,
) -> InspectedRun:
    git = capture_git_snapshot(
        root,
        captured_at=generated_at,
        authority_root=root,
        include_remote_urls=config.git.collect_remote,
        remote_names=("origin",),
    )
    if expected_head is not None and git.head_oid != expected_head:
        raise MetadataError("expected HEAD does not match the read-only Git snapshot")
    return InspectedRun(config, git, generated_at, run_id)


def inspect_repository(
    repository: Path,
    *,
    config_path: Path | str = Path(".ai/config.json"),
    expected_head: str | None = None,
    dependencies: EngineDependencies = EngineDependencies(),
) -> InspectedRun:
    """Resolve/configure/capture Git without reading metadata/evidence or rendering."""

    generated_at = dependencies.clock()
    validate_utc_datetime(generated_at, "clock")
    run_id = dependencies.run_id()
    if not isinstance(run_id, str) or not run_id:
        raise MetadataError("injected run ID is invalid")
    root, config = _resolve_config(repository, config_path)
    return _capture_inspection(
        root,
        config,
        generated_at=generated_at,
        run_id=run_id,
        expected_head=expected_head,
    )


def _prepare_from_inspection(
    inspected: InspectedRun,
    *,
    metadata_path: Path | str | None = None,
    stage: str | None = None,
    task: str | None = None,
) -> PreparedRun:
    config = inspected.config
    git = inspected.git
    generated_at = inspected.generated_at
    run_id = inspected.run_id
    evidence = load_test_results(
        config.repository_root,
        config.tests.result_file,
        expected_project=config.project.name,
        required=False,
    )
    metadata_binding = _metadata(config, metadata_path=metadata_path, stage=stage, task=task)
    metadata = metadata_binding.metadata
    verified: set[str] = set()
    if metadata.commit_claim_oid is not None:
        verification = verify_commit(
            config.repository_root,
            metadata.commit_claim_oid,
            authority_root=config.repository_root,
        )
        if verification.status is CommitVerificationStatus.VERIFIED:
            verified.add(metadata.commit_claim_oid)
    state = build_project_state(
        config=config,
        git=git,
        tests=evidence.runs,
        metadata=metadata,
        generated_at=generated_at,
        run_id=run_id,
        version=config.version,
        capabilities=config.capabilities,
        verified_commit_oids=frozenset(verified),
    )
    outputs = render_output_candidates(
        state,
        version=config.version,
        capabilities=config.capabilities,
        checkpoint_path=_checkpoint_path(generated_at),
    )
    return PreparedRun(
        config,
        git,
        outputs,
        generated_at,
        run_id,
        evidence.evidence_present,
        len(evidence.runs), metadata_binding.sha256, metadata_binding.mode,
    )


def prepare_run(
    repository: Path,
    *,
    config_path: Path | str = Path(".ai/config.json"),
    metadata_path: Path | str | None = None,
    stage: str | None = None,
    task: str | None = None,
    expected_head: str | None = None,
    dependencies: EngineDependencies = EngineDependencies(),
) -> PreparedRun:
    inspected = inspect_repository(
        repository,
        config_path=config_path,
        expected_head=expected_head,
        dependencies=dependencies,
    )
    return _prepare_from_inspection(
        inspected,
        metadata_path=metadata_path,
        stage=stage,
        task=task,
    )

def _inspect_payload(command: str, inspected: InspectedRun) -> dict[str, Any]:
    config = inspected.config
    return {
        "ok": True,
        "command": command,
        "run_id": inspected.run_id,
        "generated_at_utc": inspected.generated_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "engine_version": config.version.engine_version,
        "state_schema_version": config.version.state_schema_version,
        "manifest_schema_version": config.version.manifest_schema_version,
        "minimum_reader_version": config.version.minimum_reader_version,
        "capabilities": {
            "supported": list(config.capabilities.supported),
            "required": list(config.capabilities.required),
        },
        "git": {
            "branch": inspected.git.branch,
            "head_oid": inspected.git.head_oid,
            "dirty": inspected.git.is_dirty,
            "fingerprint_sha256": inspected.git.fingerprint_sha256,
            "staged_paths_count": sum(1 for item in inspected.git.entries if item.is_staged),
            "remote_urls": [
                {"name": name, "url": url}
                for name, url in inspected.git.remote_urls
            ],
        },
        "writes_performed": False,
    }


def _base_payload(command: str, prepared: PreparedRun) -> dict[str, Any]:
    state_payload = json.loads(prepared.outputs.by_path()[".ai/STATE.json"].content)
    payload = _inspect_payload(
        command,
        InspectedRun(
            prepared.config,
            prepared.git,
            prepared.generated_at,
            prepared.run_id,
        ),
    )
    payload.update(
        {
            "test_evidence": {
                "present": prepared.evidence_present,
                "run_count": prepared.evidence_run_count,
            },
            "project_state": state_payload["project_state"],
            "intended_outputs": [
                artifact.path
                for artifact in prepared.outputs.artifacts
            ],
        }
    )
    return payload

def execute(
    command: str,
    repository: Path,
    *,
    config_path: Path | str = Path(".ai/config.json"),
    metadata_path: Path | str | None = None,
    stage: str | None = None,
    task: str | None = None,
    expected_head: str | None = None,
    expected_metadata_sha256: str | None = None,
    dependencies: EngineDependencies = EngineDependencies(),
) -> EngineResult:
    """Execute one of four fixed commands and return a structured result."""

    if command not in COMMANDS:
        return EngineResult(CLI_ERROR, {"ok": False, "error": {"code": "CLI_COMMAND_UNSUPPORTED", "message": "unsupported command"}})
    if command == "inspect" and any(
        value is not None for value in (metadata_path, stage, task, expected_metadata_sha256)
    ):
        return EngineResult(CLI_ERROR, {"ok": False, "error": {"code": "CLI_ARGUMENT_UNSUPPORTED", "message": "inspect does not accept metadata arguments"}, "writes_performed": False})
    run_id = "unavailable"
    try:
        _validate_expected_metadata_sha256(expected_metadata_sha256)
        generated_at = dependencies.clock()
        validate_utc_datetime(generated_at, "clock")
        run_id = dependencies.run_id()
        if not isinstance(run_id, str) or not run_id:
            raise MetadataError("injected run ID is invalid")
        root, config = _resolve_config(repository, config_path)
        recovery_outcome: str | None = None
        if command == "sync":
            recovery_outcome = dependencies.recovery(root)
            if recovery_outcome is not None:
                _emit(
                    dependencies,
                    run_id=run_id,
                    timestamp=generated_at,
                    event="publication_recovered",
                    severity="warning",
                    component="publisher",
                    message="Interrupted publication recovery completed",
                    details={"outcome": recovery_outcome},
                )
        inspected = _capture_inspection(
            root,
            config,
            generated_at=generated_at,
            run_id=run_id,
            expected_head=expected_head,
        )
        if command == "inspect":
            _emit(
                dependencies,
                run_id=run_id,
                timestamp=generated_at,
                event="engine_inspected",
                severity="info",
                component="engine",
                message="AI Sync repository inspected",
            )
            return EngineResult(SUCCESS, _inspect_payload(command, inspected))
        prepared = _prepare_from_inspection(
            inspected,
            metadata_path=metadata_path,
            stage=stage,
            task=task,
        )
        _emit(
            dependencies,
            run_id=run_id,
            timestamp=prepared.generated_at,
            event="engine_prepared",
            severity="info",
            component="engine",
            message="AI Sync candidate prepared",
            details={
                "command": command,
                "artifact_count": len(prepared.outputs.artifacts),
            },
        )
        if expected_metadata_sha256 is not None and prepared.metadata_sha256 != expected_metadata_sha256:
            raise MetadataError("expected metadata SHA-256 does not match")
        payload = _base_payload(command, prepared)
        payload.update({
            "metadata_present": prepared.metadata_mode != "none",
            "metadata_sha256": prepared.metadata_sha256,
            "metadata_mode": prepared.metadata_mode,
        })
        if recovery_outcome is not None:
            payload["recovery"] = {
                "outcome": recovery_outcome,
                "continued": True,
            }
        payload["validation"] = {"blocking_issues": 0, "status": "passed"}
        if command == "validate":
            return EngineResult(SUCCESS, payload)
        payload["candidate_artifacts"] = [
            {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
            for item in prepared.outputs.artifacts
        ]
        payload["manifest_self_digest"] = prepared.outputs.manifest_self_digest
        if command == "show-plan":
            return EngineResult(SUCCESS, payload)

        def fingerprint() -> str:
            current = capture_git_snapshot(
                prepared.config.repository_root, captured_at=dependencies.clock(),
                authority_root=prepared.config.repository_root,
                include_remote_urls=prepared.config.git.collect_remote,
                remote_names=("origin",),
            )
            return _optimistic_fingerprint(current)

        result = dependencies.publisher(
            prepared.config.repository_root, prepared.outputs,
            version=prepared.config.version, capabilities=prepared.config.capabilities,
            started_at=prepared.generated_at, completed_at=dependencies.clock(),
            expected_fingerprint=_optimistic_fingerprint(prepared.git),
            hooks=PublicationHooks(fingerprint=fingerprint),
        )
        if result.status is not PublicationStatus.PUBLISHED:
            raise PublisherError("PUBLICATION_INCOMPLETE", "publisher did not complete")
        payload["writes_performed"] = True
        payload["publication"] = {
            "status": result.status.value,
            "published_paths": list(result.published_paths),
            "unchanged_paths": list(result.unchanged_paths),
            "manifest_self_digest": result.manifest_self_digest,
            "manifest_file_sha256": result.manifest_file_sha256,
        }
        return EngineResult(SUCCESS, payload)
    except ConfigError as error:
        return _error(CONFIG_INVALID, error.code, str(error))
    except NotGitRepositoryError as error:
        return _error(NOT_GIT_REPOSITORY, error.code.value, str(error))
    except GitReaderError as error:
        return _error(GIT_READ_FAILED, error.code.value, str(error))
    except TestEvidenceError as error:
        return _error(TEST_EVIDENCE_INVALID, error.code, str(error))
    except (MetadataError, RendererError, TypeError, ValueError) as error:
        return _error(VALIDATION_FAILED, "VALIDATION_FAILED", str(error))
    except PublisherError as error:
        safety = error.recovery_required or error.code.startswith("SAFETY_") or error.code in {
            "PUBLICATION_LOCKED", "PUBLICATION_RECOVERY_REQUIRED", "PUBLICATION_JOURNAL_INVALID",
        }
        return _error(SAFETY_BOUNDARY_VIOLATION if safety else PUBLICATION_FAILED, error.code, str(error))
    except KeyboardInterrupt:
        return _error(SAFETY_BOUNDARY_VIOLATION, "INTERRUPTED", "operation interrupted safely")
    except Exception:
        _emit(dependencies, run_id=run_id, timestamp=datetime.now(UTC), event="engine_internal_error",
              severity="error", component="engine", message="internal engine failure")
        return _error(VALIDATION_FAILED, "ENGINE_INTERNAL_ERROR", "internal engine failure")


def _error(exit_code: int, code: str, message: str) -> EngineResult:
    return EngineResult(exit_code, {"ok": False, "error": {"code": code, "message": str(_sanitize(message))}, "writes_performed": False})
