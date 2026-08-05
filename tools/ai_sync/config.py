"""Strict, fail-closed configuration loading for AI Sync Engine WP1."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .models import (
    CapabilitySet,
    SUPPORTED_CAPABILITIES,
    VersionInfo,
    default_version_info,
    normalize_relative_posix_path,
    require_positive_int,
)


CONFIG_SCHEMA_VERSION = 1
CONFIG_PROFILE = "schema_1_v1_1_compatibility"
UTF8_BOM = b"\xef\xbb\xbf"

EXACT_OUTPUT_ALLOWLIST = (
    ".ai/STATE.json",
    ".ai/MANIFEST.json",
    ".ai/CURRENT_STATUS.md",
    ".ai/NEXT_TASK.md",
    ".ai/SESSION.json",
    ".ai/METRICS.json",
    ".ai/HANDOFF/TO_CHATGPT.md",
    ".ai/CHECKPOINTS/<timestamp>.md",
)

_PATH_DEFAULTS = {
    "root": ".ai",
    "state": ".ai/STATE.json",
    "manifest": ".ai/MANIFEST.json",
    "current_status": ".ai/CURRENT_STATUS.md",
    "next_task": ".ai/NEXT_TASK.md",
    "session": ".ai/SESSION.json",
    "metrics": ".ai/METRICS.json",
    "handoff_to_chatgpt": ".ai/HANDOFF/TO_CHATGPT.md",
    "handoff_to_codex": ".ai/HANDOFF/TO_CODEX.md",
    "checkpoints": ".ai/CHECKPOINTS",
}


class ConfigError(ValueError):
    """A stable, sanitized configuration failure."""

    def __init__(self, code: str, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    name: str
    repository_root: str
    default_branch: str


@dataclass(frozen=True, slots=True)
class AiSyncPaths:
    root: str
    state: str
    manifest: str
    current_status: str
    next_task: str
    session: str
    metrics: str
    handoff_to_chatgpt: str
    handoff_to_codex: str
    checkpoints: str

    @property
    def final_output_allowlist(self) -> tuple[str, ...]:
        return EXACT_OUTPUT_ALLOWLIST


@dataclass(frozen=True, slots=True)
class GitConfig:
    collect_branch: bool
    collect_head: bool
    collect_remote: bool
    collect_working_tree: bool
    collect_diff_stat: bool
    allow_stage: bool
    allow_commit: bool
    allow_push: bool


@dataclass(frozen=True, slots=True)
class TestsConfig:
    result_file: str
    accept_manual_results: bool
    run_tests_automatically: bool


@dataclass(frozen=True, slots=True)
class CheckpointConfig:
    create_automatically: bool
    timestamp_format: str
    overwrite_existing: bool


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    preserve_uncommitted_work: bool
    reject_unrelated_staging: bool
    never_modify_source_files: bool
    never_invent_test_results: bool
    never_invent_progress: bool


@dataclass(frozen=True, slots=True)
class AiSyncConfig:
    schema_version: int
    profile: str
    repository_root: Path
    project: ProjectConfig
    paths: AiSyncPaths
    git: GitConfig
    tests: TestsConfig
    checkpoint: CheckpointConfig
    safety: SafetyConfig
    version: VersionInfo
    capabilities: CapabilitySet
    inferred_fields: tuple[str, ...]

    @property
    def final_output_allowlist(self) -> tuple[str, ...]:
        return self.paths.final_output_allowlist


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(
                "CONFIG_DUPLICATE_KEY",
                "Configuration contains a duplicate JSON key",
                field=key,
            )
        result[key] = value
    return result


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError("CONFIG_TYPE_INVALID", f"{field} must be a JSON object", field=field)
    return value


def _expect_keys(
    value: dict[str, Any],
    *,
    field: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = sorted(required - value.keys())
    if missing:
        raise ConfigError(
            "CONFIG_REQUIRED_FIELD_MISSING",
            f"{field} is missing required field {missing[0]}",
            field=f"{field}.{missing[0]}",
        )
    unknown = sorted(value.keys() - required - optional)
    if unknown:
        raise ConfigError(
            "CONFIG_UNKNOWN_FIELD",
            f"{field} contains an unsupported field",
            field=f"{field}.{unknown[0]}",
        )


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError("CONFIG_TYPE_INVALID", f"{field} must be boolean", field=field)
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError("CONFIG_TYPE_INVALID", f"{field} must be a non-empty string", field=field)
    return value


def _path(value: object, field: str, repository_root: Path, *, allow_dot: bool = False) -> str:
    try:
        normalized = normalize_relative_posix_path(value, field, allow_dot=allow_dot)
    except (TypeError, ValueError) as error:
        raise ConfigError("CONFIG_PATH_INVALID", f"{field} is not a safe relative path", field=field) from error
    candidate = repository_root if normalized == "." else repository_root.joinpath(*normalized.split("/"))
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(repository_root)
    except (OSError, ValueError) as error:
        raise ConfigError("CONFIG_PATH_ESCAPE", f"{field} escapes the repository root", field=field) from error
    return normalized


def _locked_false(value: object, field: str) -> bool:
    parsed = _bool(value, field)
    if parsed:
        raise ConfigError("CONFIG_SAFETY_LOCK_VIOLATION", f"{field} must remain false", field=field)
    return parsed


def _locked_true(value: object, field: str) -> bool:
    parsed = _bool(value, field)
    if not parsed:
        raise ConfigError("CONFIG_SAFETY_LOCK_VIOLATION", f"{field} must remain true", field=field)
    return parsed


def _parse_version(value: object | None, inferred: list[str]) -> VersionInfo:
    if value is None:
        inferred.append("compatibility")
        return default_version_info()
    data = _mapping(value, "compatibility")
    required = {
        "engine_version",
        "state_schema_version",
        "manifest_schema_version",
        "minimum_reader_version",
        "created_by",
    }
    _expect_keys(data, field="compatibility", required=required)
    try:
        version = VersionInfo(
            engine_version=data["engine_version"],
            state_schema_version=data["state_schema_version"],
            manifest_schema_version=data["manifest_schema_version"],
            minimum_reader_version=data["minimum_reader_version"],
            created_by=data["created_by"],
        )
    except (TypeError, ValueError) as error:
        raise ConfigError("CONFIG_VERSION_INVALID", "compatibility metadata is invalid", field="compatibility") from error
    if version != default_version_info():
        raise ConfigError(
            "CONFIG_VERSION_UNSUPPORTED",
            "compatibility metadata is not supported by this engine profile",
            field="compatibility",
        )
    return version


def _parse_capabilities(value: object | None, inferred: list[str]) -> CapabilitySet:
    if value is None:
        inferred.append("ai_sync.required_capabilities")
        required = SUPPORTED_CAPABILITIES
    else:
        if not isinstance(value, list):
            raise ConfigError(
                "CONFIG_TYPE_INVALID",
                "ai_sync.required_capabilities must be a JSON array",
                field="ai_sync.required_capabilities",
            )
        required = tuple(value)
    try:
        return CapabilitySet(supported=SUPPORTED_CAPABILITIES, required=required)
    except (TypeError, ValueError) as error:
        code = "CONFIG_FORBIDDEN_CAPABILITY" if "forbidden" in str(error) else "CONFIG_CAPABILITY_UNSUPPORTED"
        raise ConfigError(code, "Configuration requests an unsupported capability", field="ai_sync.required_capabilities") from error


def parse_config_bytes(data: bytes, repository_root: Path) -> AiSyncConfig:
    """Parse strict UTF-8 JSON using the documented schema-1 V1.1 profile."""

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if data.startswith(UTF8_BOM):
        raise ConfigError("CONFIG_BOM_FORBIDDEN", "Configuration must be UTF-8 without BOM")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ConfigError("CONFIG_UTF8_INVALID", "Configuration is not valid UTF-8") from error
    try:
        decoded = json.loads(text, object_pairs_hook=_duplicate_key_guard)
    except ConfigError:
        raise
    except json.JSONDecodeError as error:
        raise ConfigError("CONFIG_JSON_INVALID", "Configuration JSON is malformed") from error

    try:
        canonical_root = Path(repository_root).resolve(strict=True)
    except OSError as error:
        raise ConfigError("CONFIG_REPOSITORY_ROOT_INVALID", "Repository root is unavailable") from error
    if not canonical_root.is_dir():
        raise ConfigError("CONFIG_REPOSITORY_ROOT_INVALID", "Repository root must be a directory")

    root = _mapping(decoded, "config")
    required_top = {"schema_version", "project", "ai_sync", "git", "tests", "checkpoint", "safety"}
    _expect_keys(root, field="config", required=required_top, optional={"compatibility"})
    try:
        schema_version = require_positive_int(root["schema_version"], "schema_version")
    except (TypeError, ValueError) as error:
        raise ConfigError("CONFIG_SCHEMA_INVALID", "schema_version must be a positive integer", field="schema_version") from error
    if schema_version != CONFIG_SCHEMA_VERSION:
        raise ConfigError("CONFIG_SCHEMA_UNSUPPORTED", "Configuration schema is not supported", field="schema_version")

    project_data = _mapping(root["project"], "project")
    _expect_keys(project_data, field="project", required={"name", "repository_root", "default_branch"})
    project_root = _path(project_data["repository_root"], "project.repository_root", canonical_root, allow_dot=True)
    if project_root != ".":
        raise ConfigError(
            "CONFIG_REPOSITORY_ROOT_MISMATCH",
            "project.repository_root must identify the canonical repository root",
            field="project.repository_root",
        )
    project = ProjectConfig(
        name=_string(project_data["name"], "project.name"),
        repository_root=project_root,
        default_branch=_string(project_data["default_branch"], "project.default_branch"),
    )

    inferred: list[str] = []
    ai_data = _mapping(root["ai_sync"], "ai_sync")
    ai_required = {
        "root",
        "current_status",
        "next_task",
        "session",
        "metrics",
        "handoff_to_chatgpt",
        "handoff_to_codex",
        "checkpoints",
    }
    _expect_keys(
        ai_data,
        field="ai_sync",
        required=ai_required,
        optional={"state", "manifest", "required_capabilities"},
    )
    path_values: dict[str, str] = {}
    for name, expected in _PATH_DEFAULTS.items():
        if name in {"state", "manifest"} and name not in ai_data:
            inferred.append(f"ai_sync.{name}")
            raw = expected
        else:
            raw = ai_data[name]
        normalized = _path(raw, f"ai_sync.{name}", canonical_root)
        if normalized != expected:
            raise ConfigError(
                "CONFIG_OUTPUT_CONTRACT_MISMATCH",
                f"ai_sync.{name} does not match the V1.1 contract",
                field=f"ai_sync.{name}",
            )
        path_values[name] = normalized
    paths = AiSyncPaths(**path_values)
    capabilities = _parse_capabilities(ai_data.get("required_capabilities"), inferred)
    version = _parse_version(root.get("compatibility"), inferred)

    git_data = _mapping(root["git"], "git")
    git_keys = {
        "collect_branch",
        "collect_head",
        "collect_remote",
        "collect_working_tree",
        "collect_diff_stat",
        "allow_stage",
        "allow_commit",
        "allow_push",
    }
    _expect_keys(git_data, field="git", required=git_keys)
    git = GitConfig(
        collect_branch=_bool(git_data["collect_branch"], "git.collect_branch"),
        collect_head=_bool(git_data["collect_head"], "git.collect_head"),
        collect_remote=_bool(git_data["collect_remote"], "git.collect_remote"),
        collect_working_tree=_bool(git_data["collect_working_tree"], "git.collect_working_tree"),
        collect_diff_stat=_bool(git_data["collect_diff_stat"], "git.collect_diff_stat"),
        allow_stage=_locked_false(git_data["allow_stage"], "git.allow_stage"),
        allow_commit=_locked_false(git_data["allow_commit"], "git.allow_commit"),
        allow_push=_locked_false(git_data["allow_push"], "git.allow_push"),
    )

    tests_data = _mapping(root["tests"], "tests")
    _expect_keys(tests_data, field="tests", required={"result_file", "accept_manual_results", "run_tests_automatically"})
    tests = TestsConfig(
        result_file=_path(tests_data["result_file"], "tests.result_file", canonical_root),
        accept_manual_results=_bool(tests_data["accept_manual_results"], "tests.accept_manual_results"),
        run_tests_automatically=_locked_false(
            tests_data["run_tests_automatically"],
            "tests.run_tests_automatically",
        ),
    )

    checkpoint_data = _mapping(root["checkpoint"], "checkpoint")
    _expect_keys(
        checkpoint_data,
        field="checkpoint",
        required={"create_automatically", "timestamp_format", "overwrite_existing"},
    )
    checkpoint = CheckpointConfig(
        create_automatically=_bool(checkpoint_data["create_automatically"], "checkpoint.create_automatically"),
        timestamp_format=_string(checkpoint_data["timestamp_format"], "checkpoint.timestamp_format"),
        overwrite_existing=_locked_false(
            checkpoint_data["overwrite_existing"],
            "checkpoint.overwrite_existing",
        ),
    )

    safety_data = _mapping(root["safety"], "safety")
    safety_keys = {
        "preserve_uncommitted_work",
        "reject_unrelated_staging",
        "never_modify_source_files",
        "never_invent_test_results",
        "never_invent_progress",
    }
    _expect_keys(safety_data, field="safety", required=safety_keys)
    safety = SafetyConfig(
        preserve_uncommitted_work=_locked_true(
            safety_data["preserve_uncommitted_work"],
            "safety.preserve_uncommitted_work",
        ),
        reject_unrelated_staging=_locked_true(
            safety_data["reject_unrelated_staging"],
            "safety.reject_unrelated_staging",
        ),
        never_modify_source_files=_locked_true(
            safety_data["never_modify_source_files"],
            "safety.never_modify_source_files",
        ),
        never_invent_test_results=_locked_true(
            safety_data["never_invent_test_results"],
            "safety.never_invent_test_results",
        ),
        never_invent_progress=_locked_true(
            safety_data["never_invent_progress"],
            "safety.never_invent_progress",
        ),
    )

    return AiSyncConfig(
        schema_version=schema_version,
        profile=CONFIG_PROFILE,
        repository_root=canonical_root,
        project=project,
        paths=paths,
        git=git,
        tests=tests,
        checkpoint=checkpoint,
        safety=safety,
        version=version,
        capabilities=capabilities,
        inferred_fields=tuple(sorted(inferred)),
    )


def load_config(repository_root: Path, config_path: Path | str = Path(".ai/config.json")) -> AiSyncConfig:
    """Read and parse a repository-contained config without modifying it."""

    try:
        canonical_root = Path(repository_root).resolve(strict=True)
    except OSError as error:
        raise ConfigError("CONFIG_REPOSITORY_ROOT_INVALID", "Repository root is unavailable") from error
    candidate = Path(config_path)
    if not candidate.is_absolute():
        candidate = canonical_root / candidate
    try:
        resolved_config = candidate.resolve(strict=True)
        resolved_config.relative_to(canonical_root)
    except (OSError, ValueError) as error:
        raise ConfigError("CONFIG_PATH_ESCAPE", "Config path is outside the repository") from error
    try:
        data = resolved_config.read_bytes()
    except OSError as error:
        raise ConfigError("CONFIG_READ_FAILED", "Configuration could not be read") from error
    return parse_config_bytes(data, canonical_root)
