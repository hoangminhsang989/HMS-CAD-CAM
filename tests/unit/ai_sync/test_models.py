"""WP1 tests for immutable AI Sync domain models."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.ai_sync import __version__
from tools.ai_sync.models import (
    ArtifactRole,
    CapabilitySet,
    CheckpointRecord,
    DiffScope,
    DiffSummary,
    GitSnapshot,
    OutputArtifact,
    ProjectState,
    ProjectStatus,
    PublicManifest,
    PublicationResult,
    StateDocument,
    SUPPORTED_CAPABILITIES,
    SyncRequest,
    SyncResult,
    TestEvidence as EvidenceModel,
    ValidationIssue,
    VersionInfo,
    WorkingTreeEntry,
    WorkingTreeKind,
    default_version_info,
    normalize_relative_posix_path,
    require_non_negative_int,
    validate_semver,
    validate_sha256,
    validate_utc_datetime,
)


NOW = datetime(2026, 8, 4, tzinfo=UTC)
SHA = "a" * 64
HEAD = "b" * 40


def _diff(scope: DiffScope) -> DiffSummary:
    return DiffSummary(scope, 0, 0, 0, 0, ())


def _git() -> GitSnapshot:
    return GitSnapshot(
        repository_root=Path("C:/repo"),
        captured_at=NOW,
        branch="main",
        is_detached=False,
        head_oid=HEAD,
        upstream=None,
        ahead=None,
        behind=None,
        remote_urls=(),
        entries=(),
        staged_diff=_diff(DiffScope.STAGED),
        unstaged_diff=_diff(DiffScope.UNSTAGED),
        is_dirty=False,
        fingerprint_sha256=SHA,
    )


def _state(**changes: object) -> ProjectState:
    values: dict[str, object] = {
        "state_schema_version": 1,
        "run_id": "run-1",
        "project_name": "HMS CAD/CAM",
        "generated_at": NOW,
        "stage": None,
        "status": ProjectStatus.UNKNOWN,
        "current_task": None,
        "git": _git(),
        "tests": (),
        "remaining_work": (),
        "blockers": (),
        "next_action": None,
        "stage_progress_percent": None,
        "overall_progress_percent": None,
        "provenance": (),
    }
    values.update(changes)
    return ProjectState(**values)


def test_package_version_and_import_have_no_filesystem_side_effect(tmp_path: Path, monkeypatch) -> None:
    before = tuple(tmp_path.iterdir())
    monkeypatch.chdir(tmp_path)
    import tools.ai_sync as package

    assert package.__version__ == "1.1.0" == __version__
    assert tuple(tmp_path.iterdir()) == before
    assert not hasattr(package, "GitReader")
    assert not hasattr(package, "Publisher")
    init_source = Path(package.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in init_source


@pytest.mark.parametrize("value", ("1.1.0", "0.0.1-alpha.1", "2.0.0+build.7"))
def test_semver_accepts_valid_values(value: str) -> None:
    assert validate_semver(value) == value


@pytest.mark.parametrize("value", ("1.1", "01.1.0", "1.0.0-01", True, 1))
def test_semver_rejects_invalid_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_semver(value)


@pytest.mark.parametrize("value", (0, -1, True, 1.5))
def test_schema_integer_rejects_zero_negative_bool_and_float(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        VersionInfo("1.1.0", value, 1, "1.1.0", "hms_ai_sync_engine")


def test_version_info_is_frozen_and_typed() -> None:
    version = default_version_info()
    assert version == VersionInfo("1.1.0", 1, 1, "1.1.0", "hms_ai_sync_engine")
    with pytest.raises(FrozenInstanceError):
        version.engine_version = "2.0.0"  # type: ignore[misc]


def test_timestamp_requires_utc_aware_value() -> None:
    assert validate_utc_datetime(NOW, "at") is NOW
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        validate_utc_datetime(datetime(2026, 8, 4), "at")


def test_non_utc_offset_is_rejected() -> None:
    non_utc = datetime(2026, 8, 4, tzinfo=UTC).astimezone(
        __import__("datetime").timezone(timedelta(hours=7))
    )
    with pytest.raises(ValueError, match="use UTC"):
        validate_utc_datetime(non_utc, "at")


@pytest.mark.parametrize("value", ("A" * 64, "a" * 63, "g" * 64, True))
def test_sha256_requires_lowercase_64_hex(value: object) -> None:
    with pytest.raises(ValueError):
        validate_sha256(value)
    assert validate_sha256(SHA) == SHA


@pytest.mark.parametrize(
    "value",
    ("/absolute", "C:/absolute", "C:relative", "\\\\server\\share", "a/../b", "a//b", "a:b"),
)
def test_serialized_path_rejects_absolute_drive_unc_parent_and_ads(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_relative_posix_path(value)


def test_windows_separator_and_unicode_path_normalize_to_posix() -> None:
    assert normalize_relative_posix_path(".ai\\dữ_liệu\\trạng_thái.json") == ".ai/dữ_liệu/trạng_thái.json"


def test_capabilities_are_sorted_deduplicated_and_required_subset() -> None:
    capabilities = CapabilitySet(
        supported=("source_protection", "dry_run", "source_protection"),
        required=("dry_run", "dry_run"),
    )
    assert capabilities.supported == ("dry_run", "source_protection")
    assert capabilities.required == ("dry_run",)
    with pytest.raises(ValueError, match="subset"):
        CapabilitySet(supported=("dry_run",), required=("source_protection",))


@pytest.mark.parametrize("capability", ("stage", "git-stage", "commit", "git_commit", "push", "run-tests", "pytest_run"))
def test_forbidden_capability_aliases_fail_closed(capability: str) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        CapabilitySet(supported=(capability,), required=())


def test_capability_contract_is_exactly_ten_stable_identifiers() -> None:
    assert len(SUPPORTED_CAPABILITIES) == 10
    assert tuple(sorted(SUPPORTED_CAPABILITIES)) == SUPPORTED_CAPABILITIES


def test_working_tree_path_and_rename_contract() -> None:
    entry = WorkingTreeEntry("src\\mới.py", ".", "M", WorkingTreeKind.ORDINARY)
    assert entry.path == "src/mới.py"
    assert not entry.is_staged
    with pytest.raises(ValueError, match="require original_path"):
        WorkingTreeEntry("new.py", "R", ".", WorkingTreeKind.RENAMED)


@pytest.mark.parametrize("value", (-1, True, 1.5))
def test_counters_reject_negative_and_bool(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        require_non_negative_int(value, "count")
    with pytest.raises((TypeError, ValueError)):
        DiffSummary(DiffScope.STAGED, value, 0, 0, 0, ())


@pytest.mark.parametrize("value", (-0.1, 100.1, True, "50"))
def test_project_percentages_reject_out_of_range_or_wrong_type(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _state(stage_progress_percent=value)


def test_project_percentage_boundaries_and_null_are_valid() -> None:
    assert _state(stage_progress_percent=0).stage_progress_percent == 0.0
    assert _state(stage_progress_percent=100).stage_progress_percent == 100.0
    assert _state(stage_progress_percent=None).stage_progress_percent is None


def test_output_artifact_validates_sha_path_size_and_bool() -> None:
    artifact = OutputArtifact(".ai\\STATE.json", ArtifactRole.CANONICAL_STATE, SHA, 12, "application/json", True)
    assert artifact.path == ".ai/STATE.json"
    with pytest.raises((TypeError, ValueError)):
        OutputArtifact(".ai/STATE.json", ArtifactRole.CANONICAL_STATE, "bad", 12, "application/json", True)
    with pytest.raises((TypeError, ValueError)):
        OutputArtifact(".ai/STATE.json", ArtifactRole.CANONICAL_STATE, SHA, True, "application/json", True)


def test_all_required_wp1_models_are_real_slotted_dataclasses() -> None:
    classes = (
        WorkingTreeEntry,
        DiffSummary,
        GitSnapshot,
        EvidenceModel,
        ProjectState,
        SyncRequest,
        VersionInfo,
        CapabilitySet,
        StateDocument,
        OutputArtifact,
        PublicManifest,
        PublicationResult,
        CheckpointRecord,
        ValidationIssue,
        SyncResult,
    )
    assert all(is_dataclass(model) and hasattr(model, "__slots__") for model in classes)
