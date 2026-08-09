"""Focused production Tool Library application/query contract tests."""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter

import pytest

from hms_cadcam.cam.application import basic_parallel_resources
from hms_cadcam.cam.application.service import CamApplicationService
from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    LengthUnit,
    Revision,
    ToolCommonDefaults,
    ToolDefinitionId,
    ToolFamily,
    ToolProfileSaveMode,
    preview_tool_profile_capture,
)
from hms_cadcam.cam.tool_library import (
    ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA,
    ToolDefinitionDraft,
    ToolLibrarySort,
    tool_library_records,
)


def _draft(
    name: str = "Tool A",
    family: ToolFamily = ToolFamily.BALL_END_MILL,
    *,
    create_assembly: bool = True,
    principal_size: float = 10.0,
) -> ToolDefinitionDraft:
    detail_size = {
        ToolFamily.BULL_NOSE_END_MILL: 1.0,
        ToolFamily.CHAMFER_MILL: 1.0,
        ToolFamily.TAP: 1.25,
        ToolFamily.BORING_BAR: 12.0,
        ToolFamily.TURNING_INSERT: 1.0,
    }.get(family)
    detail_angle = (
        90.0
        if family is ToolFamily.CHAMFER_MILL
        else 118.0
        if family in {ToolFamily.DRILL, ToolFamily.CENTER_DRILL}
        else None
    )
    return ToolDefinitionDraft(
        name,
        family,
        LengthUnit.MM,
        principal_size,
        20.0,
        80.0,
        40.0,
        principal_size,
        50.0,
        detail_size=detail_size,
        detail_angle_degrees=detail_angle,
        detail_text="Custom envelope" if family is ToolFamily.CUSTOM else None,
        create_assembly=create_assembly,
        assembly_name=f"{name} Assembly",
        stickout=35.0,
        gauge_length=70.0,
    )


@pytest.mark.parametrize("family", tuple(ToolFamily))
def test_draft_builds_every_real_tool_family(family: ToolFamily) -> None:
    draft = _draft(family=family)

    tool = draft.build_tool(ToolDefinitionId.new())

    assert tool.family is family
    assert tool.cutting_geometry.axial_cutting_length.value == pytest.approx(20.0)
    assert tool.revision == Revision(0)
    assert tool.configuration_revision == Revision(0)


@pytest.mark.parametrize("create_assembly", (False, True))
def test_service_generates_stable_id_and_optional_assembly(
    create_assembly: bool,
) -> None:
    app = CamApplicationService()

    snapshot = app.create_managed_tool(
        _draft(create_assembly=create_assembly)
    )

    assert len(snapshot.tool_definitions) == 1
    assert snapshot.tool_definitions[0].tool_id.value.int != 0
    assert len(snapshot.tool_assemblies) == int(create_assembly)
    if create_assembly:
        assert snapshot.tool_assemblies[0].tool_id == snapshot.tool_definitions[0].tool_id


def test_edit_preserves_identity_and_advances_both_revisions() -> None:
    app = CamApplicationService()
    created = app.create_managed_tool(_draft())
    original = created.tool_definitions[0]
    assembly = created.tool_assemblies[0]

    changed = app.update_managed_tool(
        original.tool_id,
        _draft("Tool A edited", ToolFamily.DRILL),
        expected_revision=original.revision,
        expected_configuration_revision=original.configuration_revision,
    )

    tool = changed.tool_definitions[0]
    refreshed = changed.tool_assemblies[0]
    assert tool.tool_id == original.tool_id
    assert tool.revision == original.revision.next()
    assert tool.configuration_revision == original.configuration_revision.next()
    assert refreshed.assembly_id == assembly.assembly_id
    assert refreshed.revision == assembly.revision.next()
    assert refreshed.expected_tool_revision == tool.revision
    assert refreshed.expected_tool_fingerprint == tool.content_fingerprint


@pytest.mark.parametrize(
    ("expected_revision", "expected_configuration"),
    ((Revision(99), Revision(0)), (Revision(0), Revision(99))),
)
def test_stale_editor_fails_closed_without_mutation(
    expected_revision: Revision, expected_configuration: Revision
) -> None:
    app = CamApplicationService()
    before = app.create_managed_tool(_draft())
    tool = before.tool_definitions[0]

    with pytest.raises(ValueError, match="stale"):
        app.update_managed_tool(
            tool.tool_id,
            _draft("stale"),
            expected_revision=expected_revision,
            expected_configuration_revision=expected_configuration,
        )

    assert app.snapshot == before


def test_edit_cannot_change_persisted_unit() -> None:
    app = CamApplicationService()
    created = app.create_managed_tool(_draft())
    tool = created.tool_definitions[0]
    inch = replace(_draft(), unit=LengthUnit.INCH)

    with pytest.raises(ValueError, match="unit"):
        app.update_managed_tool(
            tool.tool_id,
            inch,
            expected_revision=tool.revision,
            expected_configuration_revision=tool.configuration_revision,
        )

    assert app.snapshot == created


def test_duplicate_has_new_tool_and_assembly_identity_and_is_independent() -> None:
    app = CamApplicationService()
    created = app.create_managed_tool(_draft())
    original = created.tool_definitions[0]
    original_assembly = created.tool_assemblies[0]

    duplicate = app.duplicate_tool_definition(original.tool_id)
    snapshot = app.snapshot
    duplicate_assembly = next(
        item for item in snapshot.tool_assemblies if item.tool_id == duplicate.tool_id
    )

    assert duplicate.tool_id != original.tool_id
    assert duplicate_assembly.assembly_id != original_assembly.assembly_id
    assert duplicate.common_defaults == original.common_defaults
    assert duplicate.cutting_geometry == original.cutting_geometry
    app.update_tool_common_defaults(
        duplicate.tool_id,
        ToolCommonDefaults(spindle_speed_rpm=3200.0),
        expected_configuration_revision=duplicate.configuration_revision,
    )
    live_original = next(
        item for item in app.snapshot.tool_definitions if item.tool_id == original.tool_id
    )
    assert live_original.common_defaults == original.common_defaults


def test_referenced_tool_delete_is_blocked_without_cascade() -> None:
    app = CamApplicationService()
    before = app.create_managed_tool(_draft())
    tool = before.tool_definitions[0]

    with pytest.raises(ValueError, match="referenced"):
        app.remove_managed_tool(
            tool.tool_id,
            expected_revision=tool.revision,
            expected_configuration_revision=tool.configuration_revision,
        )

    assert app.snapshot == before


def test_unreferenced_tool_delete_is_safe_and_keeps_other_identities() -> None:
    app = CamApplicationService()
    first = app.create_managed_tool(_draft("Referenced"))
    stable = first.tool_definitions[0]
    second = app.create_managed_tool(_draft("Bare", create_assembly=False))
    bare = next(item for item in second.tool_definitions if item.name == "Bare")

    changed = app.remove_managed_tool(
        bare.tool_id,
        expected_revision=bare.revision,
        expected_configuration_revision=bare.configuration_revision,
    )

    assert tuple(item.tool_id for item in changed.tool_definitions) == (stable.tool_id,)
    assert changed.tool_assemblies == first.tool_assemblies


@pytest.mark.parametrize(
    "query_kind",
    ("name", "tool_id", "family", "size", "assembly", "holder"),
)
def test_search_uses_real_tool_data(query_kind: str) -> None:
    app = CamApplicationService()
    _other_tool, holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    app.add_holder_definition(holder)
    draft = replace(_draft("Search Target", principal_size=13.5), holder_id=holder.holder_id)
    snapshot = app.create_managed_tool(draft)
    tool = snapshot.tool_definitions[-1]
    assembly = snapshot.tool_assemblies[-1]
    query = {
        "name": "search target",
        "tool_id": str(tool.tool_id),
        "family": "ball_end_mill",
        "size": "13.5",
        "assembly": str(assembly.assembly_id),
        "holder": holder.name,
    }[query_kind]

    records = tool_library_records(snapshot, query=query)

    assert tuple(item.tool.tool_id for item in records) == (tool.tool_id,)


@pytest.mark.parametrize(
    "family",
    (ToolFamily.BALL_END_MILL, ToolFamily.DRILL, ToolFamily.END_MILL),
)
def test_family_filter_uses_real_enum(family: ToolFamily) -> None:
    app = CamApplicationService()
    for value in (ToolFamily.BALL_END_MILL, ToolFamily.DRILL, ToolFamily.END_MILL):
        app.create_managed_tool(_draft(value.value, value, create_assembly=False))

    records = tool_library_records(app.snapshot, family=family)

    assert len(records) == 1
    assert records[0].tool.family is family


@pytest.mark.parametrize(
    ("strategy_id", "expected_family"),
    (
        ("parallel_finishing_3d", ToolFamily.BALL_END_MILL),
        ("z_level_finishing_3d", ToolFamily.BALL_END_MILL),
        ("drilling_v1", ToolFamily.DRILL),
    ),
)
def test_compatibility_filter_reuses_profile_registry(
    strategy_id: str, expected_family: ToolFamily
) -> None:
    app = CamApplicationService()
    app.create_managed_tool(_draft("Ball", ToolFamily.BALL_END_MILL, create_assembly=False))
    app.create_managed_tool(_draft("Drill", ToolFamily.DRILL, create_assembly=False))

    records = tool_library_records(app.snapshot, compatible_strategy_id=strategy_id)

    assert records
    assert {item.tool.family for item in records} == {expected_family}


@pytest.mark.parametrize("sort", tuple(ToolLibrarySort))
def test_sort_is_deterministic_and_identity_independent(sort: ToolLibrarySort) -> None:
    app = CamApplicationService()
    app.create_managed_tool(_draft("Zulu", create_assembly=False, principal_size=12.0))
    app.create_managed_tool(_draft("Alpha", ToolFamily.DRILL, create_assembly=False, principal_size=5.0))
    snapshot = app.snapshot

    first = tool_library_records(snapshot, sort=sort)
    second = tool_library_records(snapshot, sort=sort)

    assert first == second
    assert {item.tool.tool_id for item in first} == {
        item.tool_id for item in snapshot.tool_definitions
    }


def test_common_defaults_update_advances_only_configuration_revision() -> None:
    app = CamApplicationService()
    created = app.create_managed_tool(_draft())
    tool = created.tool_definitions[0]
    defaults = ToolCommonDefaults(
        spindle_speed_rpm=4500.0,
        cutting_feed_mm_per_min=800.0,
        quality_profile="high",
    )

    changed = app.update_tool_common_defaults(
        tool.tool_id,
        defaults,
        expected_configuration_revision=tool.configuration_revision,
    )
    current = changed.tool_definitions[0]

    assert current.tool_id == tool.tool_id
    assert current.revision == tool.revision
    assert current.configuration_revision == tool.configuration_revision.next()
    assert current.common_defaults == defaults


@pytest.mark.parametrize(
    ("strategy_id", "family", "values"),
    (
        ("parallel_finishing_3d", ToolFamily.BALL_END_MILL, {"stepover_mm": 0.5}),
        ("z_level_finishing_3d", ToolFamily.BALL_END_MILL, {"stepdown_mm": 0.6}),
        ("drilling_v1", ToolFamily.DRILL, {"peck_depth_mm": 2.0}),
    ),
)
def test_strategy_profiles_use_authoritative_schema_and_revision(
    strategy_id: str, family: ToolFamily, values: dict[str, object]
) -> None:
    app = CamApplicationService()
    created = app.create_managed_tool(_draft("Profile Tool", family))
    tool = created.tool_definitions[0]
    preview = preview_tool_profile_capture(
        tool,
        strategy_id,
        f"{strategy_id} profile",
        values,
        overridden_field_ids=frozenset(values),
        mode=ToolProfileSaveMode.OVERRIDES_ONLY,
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )

    changed = app.save_tool_program_profile(
        preview,
        expected_configuration_revision=tool.configuration_revision,
    )
    current = changed.tool_definitions[0]

    assert current.configuration_revision == tool.configuration_revision.next()
    assert current.program_profiles[0].strategy_id == strategy_id
    assert current.program_profiles[0].sparse_mapping == values


def test_profile_duplicate_is_deep_and_disabled() -> None:
    app = CamApplicationService()
    created = app.create_managed_tool(_draft())
    tool = created.tool_definitions[0]
    preview = preview_tool_profile_capture(
        tool,
        "parallel_finishing_3d",
        "Parallel",
        {"stepover_mm": 0.5},
        overridden_field_ids=frozenset({"stepover_mm"}),
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )
    saved = app.save_tool_program_profile(
        preview, expected_configuration_revision=Revision(0)
    )
    current = saved.tool_definitions[0]
    profile = current.program_profiles[0]

    duplicated = app.duplicate_tool_program_profile_entry(
        current.tool_id,
        profile.profile_id,
        expected_configuration_revision=current.configuration_revision,
    )
    profiles = duplicated.tool_definitions[0].program_profiles

    assert len(profiles) == 2
    original_profile = next(item for item in profiles if item.profile_id == profile.profile_id)
    copied_profile = next(item for item in profiles if item.profile_id != profile.profile_id)
    assert original_profile.sparse_mapping == copied_profile.sparse_mapping
    assert original_profile.enabled
    assert not copied_profile.enabled


def test_archive_capability_is_truthfully_unavailable_without_schema_change() -> None:
    assert ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA == (
        "ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA"
    )


@pytest.mark.parametrize("tool_count", (10, 50, 200))
def test_load_search_filter_responsiveness_is_immediate(tool_count: int) -> None:
    app = CamApplicationService()
    for index in range(tool_count):
        family = ToolFamily.BALL_END_MILL if index % 2 == 0 else ToolFamily.DRILL
        app.create_managed_tool(
            _draft(
                f"Performance Tool {index:03d}",
                family,
                create_assembly=False,
                principal_size=1.0 + index,
            )
        )
    snapshot = app.snapshot

    started = perf_counter()
    loaded = tool_library_records(snapshot)
    searched = tool_library_records(snapshot, query="Performance Tool 0")
    filtered = tool_library_records(
        snapshot,
        family=ToolFamily.BALL_END_MILL,
        sort=ToolLibrarySort.PRINCIPAL_SIZE,
    )
    duration = perf_counter() - started

    assert len(loaded) == tool_count
    assert searched
    assert len(filtered) == (tool_count + 1) // 2
    assert duration < 0.25
