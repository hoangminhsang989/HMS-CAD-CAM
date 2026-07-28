"""Stage 8A.4.1 typed Tool profiles, resolver, service and persistence."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import sqlite3

import pytest

from hms_cadcam.cam.application import (
    CamApplicationService,
    basic_drilling_resources,
    basic_mill_resources,
    basic_parallel_resources,
)
from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    DEFAULT_TOOL_PROFILE_RESOLVER,
    DRILLING_TOOL_PROFILE_SCHEMA,
    PARALLEL_TOOL_PROFILE_SCHEMA,
    Z_LEVEL_TOOL_PROFILE_SCHEMA,
    CamValidationError,
    ContentFingerprint,
    EffectiveValueValidation,
    LengthUnit,
    Revision,
    ToolCommonDefaults,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    ToolProfileListState,
    ToolProfileSaveMode,
    ToolProfileValidationState,
    ToolProfileValue,
    ToolProfileValueSource,
    ToolProgramProfile,
    ToolProgramProfileId,
    assess_tool_program_profile,
    build_profile_from_preview,
    preview_tool_profile_capture,
)
from hms_cadcam.cam.persistence import CamProjectSnapshot, CamSqliteRepository
from hms_cadcam.project.database import ProjectDatabase


_NOW = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)


def _profile(
    tool: ToolDefinition,
    strategy_id: str,
    values: dict[str, object],
    *,
    enabled: bool = True,
    holder_fingerprint: ContentFingerprint | None = None,
) -> ToolProgramProfile:
    schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy_id)
    return ToolProgramProfile(
        ToolProgramProfileId.new(),
        tool.tool_id,
        strategy_id,
        schema.display_name_vi,
        enabled,
        schema.profile_schema_version,
        schema.normalize_values(values),
        _NOW,
        _NOW,
        tool.revision,
        tool.content_fingerprint,
        source_holder_fingerprint=holder_fingerprint,
    )


def _automatic_zlevel() -> dict[str, object]:
    return {
        "quality_profile": "balanced",
        "stepdown_mm": 0.8,
        "tolerance_mm": 0.02,
        "surface_allowance_mm": 0.0,
        "linking_mode": "retract_clearance",
        "approach_retract_policy": "retract_then_rapid",
    }


def test_tool_without_profiles_remains_v1_and_round_trips_unchanged() -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    payload = tool.to_dict()
    fingerprint = tool.content_fingerprint

    restored = ToolDefinition.from_dict(payload)

    assert payload["format_version"] == 1
    assert restored == tool
    assert restored.program_profiles == ()
    assert restored.common_defaults.is_empty
    assert restored.content_fingerprint == fingerprint


def test_common_defaults_and_sparse_profile_round_trip_as_tool_v2() -> None:
    tool, holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    profile = _profile(
        tool,
        "parallel_finishing_3d",
        {"stepover_mm": 0.65, "direction_angle_degrees": 30.0},
        holder_fingerprint=holder.content_fingerprint,
    )
    configured = replace(
        tool,
        common_defaults=ToolCommonDefaults(
            spindle_speed_rpm=8000,
            cutting_feed_mm_per_min=1200,
            quality_profile="high",
        ),
        program_profiles=(profile,),
        configuration_revision=Revision(1),
    )

    restored = ToolDefinition.from_dict(configured.to_dict())

    assert configured.to_dict()["format_version"] == 2
    assert restored == configured
    assert restored.program_profiles[0].sparse_mapping == {
        "direction_angle_degrees": 30.0,
        "stepover_mm": 0.65,
    }
    assert restored.content_fingerprint == tool.content_fingerprint
    assert restored.configuration_fingerprint != tool.configuration_fingerprint


def test_three_real_strategy_schemas_are_distinct_and_typed() -> None:
    zlevel = {item.field_id for item in Z_LEVEL_TOOL_PROFILE_SCHEMA.fields}
    parallel = {item.field_id for item in PARALLEL_TOOL_PROFILE_SCHEMA.fields}
    drilling = {item.field_id for item in DRILLING_TOOL_PROFILE_SCHEMA.fields}

    assert {"stepdown_mm", "approach_retract_policy"} <= zlevel
    assert {"stepover_mm", "direction_angle_degrees"} <= parallel
    assert {"peck_depth_mm", "dwell_seconds"} <= drilling
    assert "stepdown_mm" not in parallel | drilling
    assert "peck_depth_mm" not in zlevel | parallel
    assert len(DEFAULT_TOOL_PROFILE_REGISTRY.schemas) == 3


@pytest.mark.parametrize(
    ("values", "message"),
    (
        ({"unknown_field": 1.0}, "không được hỗ trợ"),
        ({"stepdown_mm": 0.0}, "giới hạn"),
        ({"linking_mode": "unsafe_direct"}, "danh sách"),
    ),
)
def test_schema_rejects_unknown_or_invalid_sparse_values(
    values: dict[str, object], message: str
) -> None:
    with pytest.raises(CamValidationError, match=message):
        Z_LEVEL_TOOL_PROFILE_SCHEMA.normalize_values(values)


def test_unknown_strategy_fails_clearly_during_tool_validation() -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    profile = ToolProgramProfile(
        ToolProgramProfileId.new(),
        tool.tool_id,
        "future_strategy",
        "Tương lai",
        True,
        1,
        (),
        _NOW,
        _NOW,
        tool.revision,
        tool.content_fingerprint,
    )

    with pytest.raises(CamValidationError, match="chưa được đăng ký"):
        replace(tool, program_profiles=(profile,))


def test_resolver_precedence_and_vietnamese_provenance() -> None:
    tool, holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    profile = _profile(
        tool,
        "z_level_finishing_3d",
        {"stepdown_mm": 0.4, "tolerance_mm": 0.01},
        holder_fingerprint=holder.content_fingerprint,
    )
    configured = replace(
        tool,
        common_defaults=ToolCommonDefaults(quality_profile="high"),
        program_profiles=(profile,),
        configuration_revision=Revision(1),
    )

    result = DEFAULT_TOOL_PROFILE_RESOLVER.resolve(
        configured,
        "z_level_finishing_3d",
        operation_overrides={"tolerance_mm": 0.005},
        automatic_values=_automatic_zlevel(),
        operation_id="operation:test",
        automatic_policy_id="z_level_automatic_v1",
        holder_fingerprint=holder.content_fingerprint,
    )

    assert result.value("tolerance_mm").source is (
        ToolProfileValueSource.OPERATION_OVERRIDE
    )
    assert result.value("stepdown_mm").source is (
        ToolProfileValueSource.TOOL_PROGRAM_PROFILE
    )
    assert result.value("quality_profile").source is (
        ToolProfileValueSource.TOOL_COMMON_DEFAULT
    )
    assert result.value("surface_allowance_mm").source is (
        ToolProfileValueSource.AUTOMATIC_POLICY
    )
    assert "Cấu hình Tool theo chương trình" in result.value(
        "stepdown_mm"
    ).reason_vi
    assert not result.blocked
    assert result.dependency_fingerprint == result.dependency_fingerprint


def test_disabled_profile_is_optional_and_automatic_policy_still_resolves() -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    disabled = _profile(
        tool,
        "z_level_finishing_3d",
        {"stepdown_mm": 0.2},
        enabled=False,
    )
    configured = replace(tool, program_profiles=(disabled,))

    result = DEFAULT_TOOL_PROFILE_RESOLVER.resolve(
        configured,
        "z_level_finishing_3d",
        automatic_values=_automatic_zlevel(),
    )

    assert result.profile_compatibility.state is ToolProfileListState.DISABLED
    assert result.value("stepdown_mm").canonical_value == 0.8
    assert result.value("stepdown_mm").source is (
        ToolProfileValueSource.AUTOMATIC_POLICY
    )


def test_tool_or_holder_change_marks_profile_for_review_and_falls_back() -> None:
    tool, holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    profile = _profile(
        tool,
        "z_level_finishing_3d",
        {"stepdown_mm": 0.25},
        holder_fingerprint=holder.content_fingerprint,
    )
    configured = replace(tool, program_profiles=(profile,))
    changed_tool = replace(configured, revision=Revision(1))

    stale_tool = assess_tool_program_profile(
        profile,
        changed_tool,
        DEFAULT_TOOL_PROFILE_REGISTRY,
        holder_fingerprint=holder.content_fingerprint,
    )
    stale_holder = assess_tool_program_profile(
        profile,
        configured,
        DEFAULT_TOOL_PROFILE_REGISTRY,
        holder_fingerprint=ContentFingerprint.from_payload({"holder": "changed"}),
    )
    result = DEFAULT_TOOL_PROFILE_RESOLVER.resolve(
        changed_tool,
        "z_level_finishing_3d",
        automatic_values=_automatic_zlevel(),
        holder_fingerprint=holder.content_fingerprint,
    )

    assert stale_tool.state is ToolProfileListState.NEEDS_REVIEW
    assert stale_holder.state is ToolProfileListState.NEEDS_REVIEW
    assert result.value("stepdown_mm").source is (
        ToolProfileValueSource.AUTOMATIC_POLICY
    )
    assert result.value("stepdown_mm").validation_status is (
        EffectiveValueValidation.FALLBACK
    )


def test_unsupported_tool_family_never_applies_ball_end_profile() -> None:
    ball, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    end_mill, _holder2, _assembly2, _machine2 = basic_mill_resources(
        LengthUnit.MM
    )
    copied = replace(
        _profile(ball, "parallel_finishing_3d", {"stepover_mm": 0.5}),
        tool_id=end_mill.tool_id,
        source_tool_revision=end_mill.revision,
        source_tool_fingerprint=end_mill.content_fingerprint,
    )
    configured = replace(end_mill, program_profiles=(copied,))

    status = assess_tool_program_profile(
        copied, configured, DEFAULT_TOOL_PROFILE_REGISTRY
    )

    assert configured.family is ToolFamily.END_MILL
    assert status.state is ToolProfileListState.INCOMPATIBLE
    assert not status.usable
    assert "Họ Tool" in status.reason_vi


def test_safe_default_is_used_only_when_schema_declares_it() -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)

    result = DEFAULT_TOOL_PROFILE_RESOLVER.resolve(
        tool,
        "z_level_finishing_3d",
        automatic_values={
            "stepdown_mm": 0.5,
            "tolerance_mm": 0.02,
            "surface_allowance_mm": 0.0,
            "linking_mode": "retract_clearance",
            "approach_retract_policy": "retract_then_rapid",
        },
    )

    assert result.value("quality_profile").source is (
        ToolProfileValueSource.SAFE_DEFAULT
    )
    assert result.value("quality_profile").canonical_value == "balanced"


def test_capture_preview_defaults_to_only_explicit_overrides() -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)

    preview = preview_tool_profile_capture(
        tool,
        "parallel_finishing_3d",
        "Gia công tinh song song",
        {
            "quality_profile": "high",
            "stepover_mm": 0.6,
            "direction_angle_degrees": 45.0,
        },
        overridden_field_ids=frozenset({"stepover_mm"}),
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )
    profile = build_profile_from_preview(tool, preview, now=_NOW)

    assert preview.mode is ToolProfileSaveMode.OVERRIDES_ONLY
    assert profile.sparse_mapping == {"stepover_mm": 0.6}
    assert profile.validation_state is ToolProfileValidationState.CONFIGURED


def test_drilling_profile_capture_normalizes_inch_length_to_mm() -> None:
    tool, _center, _holder, _assembly, _center_assembly = (
        basic_drilling_resources(LengthUnit.INCH)
    )

    preview = preview_tool_profile_capture(
        tool,
        "drilling_v1",
        "Khoan",
        {"peck_depth": "0.1"},
        overridden_field_ids=frozenset({"peck_depth_mm"}),
        source_unit=LengthUnit.INCH,
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )
    profile = build_profile_from_preview(tool, preview, now=_NOW)

    assert profile.sparse_mapping == {"peck_depth_mm": pytest.approx(2.54)}


def test_application_service_save_delete_toggle_and_duplicate() -> None:
    tool, holder, assembly, machine = basic_parallel_resources(LengthUnit.MM)
    app = CamApplicationService()
    app.add_basic_resources(tool, holder, assembly, machine)
    preview = preview_tool_profile_capture(
        tool,
        "parallel_finishing_3d",
        "Gia công tinh song song",
        {"stepover_mm": 0.7},
        overridden_field_ids=frozenset({"stepover_mm"}),
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )

    saved = app.save_tool_program_profile(
        preview,
        expected_configuration_revision=Revision(0),
        holder_fingerprint=holder.content_fingerprint,
    )
    configured = saved.tool_definitions[0]
    profile = configured.program_profiles[0]
    assert configured.configuration_revision == Revision(1)
    assert profile.sparse_mapping == {"stepover_mm": 0.7}

    disabled = app.set_tool_program_profile_enabled(
        tool.tool_id,
        profile.profile_id,
        False,
        expected_configuration_revision=Revision(1),
    )
    assert not disabled.tool_definitions[0].program_profiles[0].enabled

    duplicate = app.duplicate_tool_definition(tool.tool_id)
    assert duplicate.tool_id != tool.tool_id
    assert duplicate.program_profiles[0].profile_id != profile.profile_id
    assert not duplicate.program_profiles[0].enabled

    deleted = app.delete_tool_program_profile(
        tool.tool_id,
        profile.profile_id,
        expected_configuration_revision=Revision(2),
    )
    assert deleted.tool_definitions[0].program_profiles == ()


def test_profile_persistence_round_trip_keeps_sqlite_schema_v4(tmp_path) -> None:
    tool, _center_drill, holder, _assembly, _center_assembly = (
        basic_drilling_resources(LengthUnit.MM)
    )
    profile = _profile(
        tool,
        "drilling_v1",
        {
            "peck_depth_mm": 2.5,
            "dwell_seconds": 0.2,
            "retract_policy": "retract_height",
        },
        holder_fingerprint=holder.content_fingerprint,
    )
    configured = replace(
        tool,
        program_profiles=(profile,),
        configuration_revision=Revision(1),
    )
    database_path = tmp_path / "project.db"
    ProjectDatabase().initialize(database_path)
    repository = CamSqliteRepository()
    with closing(sqlite3.connect(database_path)) as connection, connection:
        repository.replace_all(
            connection, CamProjectSnapshot(tool_definitions=(configured,))
        )

    restored = repository.load(database_path)

    assert restored.tool_definitions == (configured,)
    assert ProjectDatabase().current_schema_version(database_path) == 4


@dataclass(frozen=True, slots=True)
class _InvalidContext:
    tool_id: ToolDefinitionId
    family: object
    revision: Revision
    content_fingerprint: ContentFingerprint
    common_defaults: ToolCommonDefaults
    program_profiles: tuple[ToolProgramProfile, ...]


def test_invalid_profile_value_is_not_used_and_resolution_fails_closed() -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    invalid = _profile(
        tool, "z_level_finishing_3d", {"stepdown_mm": 0.25}
    )
    invalid = replace(
        invalid, values=(ToolProfileValue("stepdown_mm", -1.0),)
    )
    context = _InvalidContext(
        tool.tool_id,
        tool.family,
        tool.revision,
        tool.content_fingerprint,
        ToolCommonDefaults(),
        (invalid,),
    )

    result = DEFAULT_TOOL_PROFILE_RESOLVER.resolve(
        context,
        "z_level_finishing_3d",
        automatic_values=_automatic_zlevel(),
    )

    assert result.profile_compatibility.state is ToolProfileListState.INCOMPATIBLE
    assert result.value("stepdown_mm").canonical_value == 0.8
    assert result.value("stepdown_mm").validation_status is (
        EffectiveValueValidation.FALLBACK
    )
