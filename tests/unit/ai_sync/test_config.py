"""WP1 tests for the strict schema-1 compatibility config profile."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.ai_sync.config import (
    CONFIG_PROFILE,
    EXACT_OUTPUT_ALLOWLIST,
    ConfigError,
    load_config,
    parse_config_bytes,
)
from tools.ai_sync.models import SUPPORTED_CAPABILITIES


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "project": {"name": "HMS CAD/CAM", "repository_root": ".", "default_branch": "main"},
        "ai_sync": {
            "root": ".ai",
            "current_status": ".ai/CURRENT_STATUS.md",
            "next_task": ".ai/NEXT_TASK.md",
            "session": ".ai/SESSION.json",
            "metrics": ".ai/METRICS.json",
            "handoff_to_chatgpt": ".ai/HANDOFF/TO_CHATGPT.md",
            "handoff_to_codex": ".ai/HANDOFF/TO_CODEX.md",
            "checkpoints": ".ai/CHECKPOINTS",
        },
        "git": {
            "collect_branch": True,
            "collect_head": True,
            "collect_remote": True,
            "collect_working_tree": True,
            "collect_diff_stat": True,
            "allow_stage": False,
            "allow_commit": False,
            "allow_push": False,
        },
        "tests": {
            "result_file": ".ai/TEST_RESULTS.json",
            "accept_manual_results": True,
            "run_tests_automatically": False,
        },
        "checkpoint": {
            "create_automatically": True,
            "timestamp_format": "%Y-%m-%d_%H%M%S",
            "overwrite_existing": False,
        },
        "safety": {
            "preserve_uncommitted_work": True,
            "reject_unrelated_staging": True,
            "never_modify_source_files": True,
            "never_invent_test_results": True,
            "never_invent_progress": True,
        },
    }


def _bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_current_repository_config_uses_explicit_safe_compatibility_profile() -> None:
    repository = Path(__file__).parents[3]
    config = load_config(repository)

    assert config.profile == CONFIG_PROFILE
    assert config.inferred_fields == (
        "ai_sync.manifest",
        "ai_sync.required_capabilities",
        "ai_sync.state",
        "compatibility",
    )
    assert config.paths.state == ".ai/STATE.json"
    assert config.paths.manifest == ".ai/MANIFEST.json"
    assert config.capabilities.required == SUPPORTED_CAPABILITIES
    assert config.final_output_allowlist == EXACT_OUTPUT_ALLOWLIST
    assert not config.git.allow_stage and not config.git.allow_commit and not config.git.allow_push
    assert not config.tests.run_tests_automatically


def test_exact_allowlist_contains_only_eight_v1_1_patterns(tmp_path: Path) -> None:
    config = parse_config_bytes(_bytes(_payload()), tmp_path)
    assert config.final_output_allowlist == (
        ".ai/STATE.json",
        ".ai/MANIFEST.json",
        ".ai/CURRENT_STATUS.md",
        ".ai/NEXT_TASK.md",
        ".ai/SESSION.json",
        ".ai/METRICS.json",
        ".ai/HANDOFF/TO_CHATGPT.md",
        ".ai/CHECKPOINTS/<timestamp>.md",
    )


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    data = _bytes(_payload()).decode("utf-8").replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1)
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(data.encode("utf-8"), tmp_path)
    assert failure.value.code == "CONFIG_DUPLICATE_KEY"


@pytest.mark.parametrize(
    ("data", "code"),
    ((b"{", "CONFIG_JSON_INVALID"), (b"\xff", "CONFIG_UTF8_INVALID"), (b"\xef\xbb\xbf{}", "CONFIG_BOM_FORBIDDEN")),
)
def test_malformed_json_utf8_and_bom_are_rejected(tmp_path: Path, data: bytes, code: str) -> None:
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(data, tmp_path)
    assert failure.value.code == code


@pytest.mark.parametrize("schema", (2, 0, True, "1"))
def test_unsupported_or_malformed_schema_is_rejected(tmp_path: Path, schema: object) -> None:
    payload = _payload()
    payload["schema_version"] = schema
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(payload), tmp_path)
    assert failure.value.code in {"CONFIG_SCHEMA_INVALID", "CONFIG_SCHEMA_UNSUPPORTED"}


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    payload = _payload()
    del payload["safety"]
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(payload), tmp_path)
    assert failure.value.code == "CONFIG_REQUIRED_FIELD_MISSING"


def test_unknown_security_sensitive_field_is_rejected(tmp_path: Path) -> None:
    payload = _payload()
    safety = payload["safety"]
    assert isinstance(safety, dict)
    safety["allow_source_write"] = True
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(payload), tmp_path)
    assert failure.value.code == "CONFIG_UNKNOWN_FIELD"
    assert failure.value.field == "safety.allow_source_write"


@pytest.mark.parametrize("field", ("allow_stage", "allow_commit", "allow_push"))
def test_git_mutation_flags_are_locked_false(tmp_path: Path, field: str) -> None:
    payload = _payload()
    git = payload["git"]
    assert isinstance(git, dict)
    git[field] = True
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(payload), tmp_path)
    assert failure.value.code == "CONFIG_SAFETY_LOCK_VIOLATION"


def test_automatic_test_execution_is_locked_false(tmp_path: Path) -> None:
    payload = _payload()
    tests = payload["tests"]
    assert isinstance(tests, dict)
    tests["run_tests_automatically"] = True
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(payload), tmp_path)
    assert failure.value.code == "CONFIG_SAFETY_LOCK_VIOLATION"


@pytest.mark.parametrize(
    "unsafe",
    (
        "../repo-evil/STATE.json",
        "C:/private/STATE.json",
        "c:/private/STATE.json",
        "C:private\\STATE.json",
        "c:private\\STATE.json",
        "\\\\server\\share\\STATE.json",
    ),
)
def test_path_escape_drive_unc_and_prefix_confusion_are_rejected(tmp_path: Path, unsafe: str) -> None:
    payload = _payload()
    ai_sync = payload["ai_sync"]
    assert isinstance(ai_sync, dict)
    ai_sync["current_status"] = unsafe
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(payload), tmp_path)
    assert failure.value.code in {"CONFIG_PATH_INVALID", "CONFIG_PATH_ESCAPE", "CONFIG_OUTPUT_CONTRACT_MISMATCH"}


def test_windows_separators_and_unicode_are_safe_and_normalized(tmp_path: Path) -> None:
    payload = _payload()
    project = payload["project"]
    ai_sync = payload["ai_sync"]
    assert isinstance(project, dict) and isinstance(ai_sync, dict)
    project["name"] = "HMS Dữ liệu Việt"
    ai_sync["current_status"] = ".ai\\CURRENT_STATUS.md"
    config = parse_config_bytes(_bytes(payload), tmp_path)
    assert config.project.name == "HMS Dữ liệu Việt"
    assert config.paths.current_status == ".ai/CURRENT_STATUS.md"


@pytest.mark.parametrize("capability", ("stage", "git_commit", "push", "run-tests", "pytest_run"))
def test_forbidden_capabilities_are_rejected(tmp_path: Path, capability: str) -> None:
    payload = _payload()
    ai_sync = payload["ai_sync"]
    assert isinstance(ai_sync, dict)
    ai_sync["required_capabilities"] = [capability]
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(payload), tmp_path)
    assert failure.value.code == "CONFIG_FORBIDDEN_CAPABILITY"


def test_unsupported_capability_is_rejected(tmp_path: Path) -> None:
    payload = _payload()
    ai_sync = payload["ai_sync"]
    assert isinstance(ai_sync, dict)
    ai_sync["required_capabilities"] = ["future_unknown"]
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(payload), tmp_path)
    assert failure.value.code == "CONFIG_CAPABILITY_UNSUPPORTED"


def test_explicit_v1_1_fields_remove_compatibility_inferences(tmp_path: Path) -> None:
    payload = _payload()
    ai_sync = payload["ai_sync"]
    assert isinstance(ai_sync, dict)
    ai_sync.update(
        {
            "state": ".ai/STATE.json",
            "manifest": ".ai/MANIFEST.json",
            "required_capabilities": list(SUPPORTED_CAPABILITIES),
        }
    )
    payload["compatibility"] = {
        "engine_version": "1.1.0",
        "state_schema_version": 1,
        "manifest_schema_version": 1,
        "minimum_reader_version": "1.1.0",
        "created_by": "hms_ai_sync_engine",
    }
    config = parse_config_bytes(_bytes(payload), tmp_path)
    assert config.inferred_fields == ()

@pytest.mark.parametrize(
    ("field", "value"),
    (("engine_version", "1.2.0"), ("state_schema_version", 2), ("manifest_schema_version", 2)),
)
def test_explicit_but_unsupported_compatibility_version_is_rejected(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload = _payload()
    payload["compatibility"] = {
        "engine_version": "1.1.0",
        "state_schema_version": 1,
        "manifest_schema_version": 1,
        "minimum_reader_version": "1.1.0",
        "created_by": "hms_ai_sync_engine",
    }
    compatibility = payload["compatibility"]
    assert isinstance(compatibility, dict)
    compatibility[field] = value
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(payload), tmp_path)
    assert failure.value.code == "CONFIG_VERSION_UNSUPPORTED"


def test_existing_symlink_escape_is_rejected_when_platform_can_create_it(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / ".ai"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {type(error).__name__}")
    with pytest.raises(ConfigError) as failure:
        parse_config_bytes(_bytes(_payload()), tmp_path)
    assert failure.value.code == "CONFIG_PATH_ESCAPE"


def test_config_loader_rejects_config_path_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(_bytes(_payload()))
    with pytest.raises(ConfigError) as failure:
        load_config(repository, outside)
    assert failure.value.code == "CONFIG_PATH_ESCAPE"
