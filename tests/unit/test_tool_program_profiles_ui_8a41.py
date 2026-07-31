"""Stage 8A.4.1 native Qt Tool-profile management UI tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea

from hms_cadcam.cam.application import basic_drilling_resources, basic_parallel_resources
from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    DRILLING_TOOL_PROFILE_SCHEMA,
    LengthUnit,
    PARALLEL_TOOL_PROFILE_SCHEMA,
    Revision,
    ToolProfileDiffKind,
    ToolProfileSaveMode,
    ToolProfileValue,
    ToolProgramProfile,
    ToolProgramProfileId,
    Z_LEVEL_TOOL_PROFILE_SCHEMA,
    preview_tool_profile_capture,
)
from hms_cadcam.ui.tool_program_profiles import (
    ToolEditorDialog,
    ToolProfileEditorDialog,
    ToolProfileSavePreviewDialog,
    ToolProgramProfilesWidget,
)
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftState,
    FunctionEditorPage,
)
from hms_cadcam.ui.function_editor.strategies.parallel import (
    build_parallel_schema,
)
from tests.unit.test_parallel_finishing_function_editor_8a23 import (
    _context as _parallel_context,
    _valid_values as _parallel_valid_values,
)


_NOW = datetime(2026, 7, 24, 9, 15, tzinfo=UTC)


def _profile(tool, strategy_id: str, values: dict[str, object]):
    schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy_id)
    return ToolProgramProfile(
        ToolProgramProfileId.new(),
        tool.tool_id,
        strategy_id,
        schema.display_name_vi,
        True,
        schema.profile_schema_version,
        schema.normalize_values(values),
        _NOW,
        _NOW,
        tool.revision,
        tool.content_fingerprint,
    )


def test_profiles_area_starts_collapsed_and_is_explicitly_optional(qtbot) -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    widget = ToolProgramProfilesWidget()
    qtbot.addWidget(widget)
    widget.bind_tool(tool)
    widget.show()

    assert not widget.is_expanded
    assert not widget.body.isVisible()
    assert "Không bắt buộc" in widget.toggle.text()
    assert "Chưa cấu hình" in widget.optional_note.text()
    assert widget.tree.topLevelItemCount() == 0

    qtbot.mouseClick(widget.toggle, Qt.MouseButton.LeftButton)
    assert widget.is_expanded
    assert widget.body.isVisible()


def test_profile_list_shows_program_status_count_and_update(qtbot) -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    profile = _profile(
        tool,
        "z_level_finishing_3d",
        {"stepdown_mm": 0.4, "tolerance_mm": 0.01},
    )
    configured = replace(
        tool, program_profiles=(profile,), configuration_revision=Revision(1)
    )
    widget = ToolProgramProfilesWidget()
    qtbot.addWidget(widget)
    widget.bind_tool(configured)
    widget.set_expanded(True)
    widget.show()
    item = widget.tree.topLevelItem(0)

    assert item.text(0) == "Gia công tinh theo cao độ Z"
    assert item.text(1) == "Có tùy chỉnh"
    assert item.text(2) == "2"
    assert "24/07/2026" in item.text(3)
    assert "strategy_id" not in " ".join(item.text(i) for i in range(4))


def test_profile_actions_are_selection_safe_and_emit_stable_identity(qtbot) -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    profile = _profile(tool, "parallel_finishing_3d", {"stepover_mm": 0.6})
    configured = replace(tool, program_profiles=(profile,))
    widget = ToolProgramProfilesWidget()
    qtbot.addWidget(widget)
    widget.bind_tool(configured)
    widget.set_expanded(True)
    widget.show()

    assert widget.action_buttons["add"].isEnabled()
    assert not widget.action_buttons["delete"].isEnabled()
    widget.tree.setCurrentItem(widget.tree.topLevelItem(0))
    assert widget.action_buttons["delete"].isEnabled()
    with qtbot.waitSignal(widget.action_requested) as signal:
        qtbot.mouseClick(
            widget.action_buttons["edit"], Qt.MouseButton.LeftButton
        )
    assert signal.args == ["edit", profile.profile_id]


@pytest.mark.parametrize(
    ("schema", "present", "absent"),
    (
        (
            Z_LEVEL_TOOL_PROFILE_SCHEMA,
            {"stepdown_mm", "approach_retract_policy"},
            {"stepover_mm", "peck_depth_mm"},
        ),
        (
            PARALLEL_TOOL_PROFILE_SCHEMA,
            {"stepover_mm", "direction_angle_degrees"},
            {"stepdown_mm", "peck_depth_mm"},
        ),
        (
            DRILLING_TOOL_PROFILE_SCHEMA,
            {"peck_depth_mm", "dwell_seconds"},
            {"stepdown_mm", "stepover_mm"},
        ),
    ),
)
def test_dialog_only_builds_strategy_declared_fields(
    qtbot, schema, present: set[str], absent: set[str]
) -> None:
    dialog = ToolProfileEditorDialog(schema)
    qtbot.addWidget(dialog)
    dialog.show()

    assert present <= set(dialog._rows)
    assert not (absent & set(dialog._rows))
    scroll = dialog.findChild(QScrollArea, "ToolProfileAdvancedScroll")
    assert scroll is not None
    assert scroll.horizontalScrollBarPolicy() is (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )


def test_profile_dialog_returns_only_enabled_sparse_values(qtbot) -> None:
    dialog = ToolProfileEditorDialog(PARALLEL_TOOL_PROFILE_SCHEMA)
    qtbot.addWidget(dialog)
    stepover = dialog._rows["stepover_mm"]
    angle = dialog._rows["direction_angle_degrees"]
    stepover.enabled.setChecked(True)
    stepover.editor.setValue(0.75)
    angle.enabled.setChecked(False)

    values = dialog.profile_values()

    assert values == {"stepover_mm": 0.75}
    assert "direction_angle_degrees" not in values


def test_save_preview_defaults_to_overrides_and_blocks_invalid_confirmation(qtbot) -> None:
    tool, _holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    def make_preview(mode: ToolProfileSaveMode):
        return preview_tool_profile_capture(
            tool,
            "parallel_finishing_3d",
            "Gia công tinh song song",
            {
                "quality_profile": "high",
                "stepover_mm": "0.5",
                "direction_angle_degrees": "30",
            },
            overridden_field_ids=frozenset({"stepover_mm"}),
            mode=mode,
            registry=DEFAULT_TOOL_PROFILE_REGISTRY,
        )

    preview = make_preview(ToolProfileSaveMode.OVERRIDES_ONLY)
    dialog = ToolProfileSavePreviewDialog(
        preview,
        preview_provider=make_preview,
    )
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.only_overrides.isChecked()
    assert dialog.selected_mode is ToolProfileSaveMode.OVERRIDES_ONLY
    kinds = {item.kind for item in preview.entries}
    assert ToolProfileDiffKind.ADD in kinds
    assert ToolProfileDiffKind.SKIPPED in kinds
    assert dialog.confirm_button.isEnabled()

    dialog.all_effective.click()
    assert dialog.selected_mode is ToolProfileSaveMode.ALL_EFFECTIVE
    quality_row = next(
        index
        for index, entry in enumerate(make_preview(ToolProfileSaveMode.ALL_EFFECTIVE).entries)
        if entry.field_id == "quality_profile"
    )
    assert dialog.table.item(quality_row, 3).text() == "Chất lượng cao"
    dialog.only_overrides.click()

    invalid = replace(
        preview,
        entries=(
            replace(
                preview.entries[0],
                kind=ToolProfileDiffKind.INVALID,
                reason_vi="Không hợp lệ",
            ),
        ),
    )
    invalid_dialog = ToolProfileSavePreviewDialog(invalid)
    qtbot.addWidget(invalid_dialog)
    assert not invalid_dialog.confirm_button.isEnabled()

    with qtbot.waitSignal(dialog.confirmed) as signal:
        qtbot.mouseClick(
            dialog.confirm_button,
            Qt.MouseButton.LeftButton,
        )
    assert signal.args == [ToolProfileSaveMode.OVERRIDES_ONLY]


def test_function_editor_save_action_opens_preview_without_calculate(
    qtbot,
) -> None:
    context, machine = _parallel_context()
    interaction = object()
    calculation_calls: list[object] = []
    page = FunctionEditorPage(
        FunctionEditorDraftState(
            build_parallel_schema(context),
            _parallel_valid_values(context, machine),
        ),
        calculate_callback=lambda values: calculation_calls.append(values),
        tool_profile_interaction_callback=lambda _values, _changed: interaction,
    )
    qtbot.addWidget(page)
    page.show()

    with qtbot.waitSignal(page.child_popup_requested) as signal:
        qtbot.mouseClick(
            page.footer.buttons[FunctionEditorAction.SAVE_TOOL_PROFILE],
            Qt.MouseButton.LeftButton,
        )

    assert signal.args == ["tool_profile_save", interaction]
    assert calculation_calls == []


def test_tool_editor_accessibility_and_optional_section_do_not_complicate_basic(qtbot) -> None:
    drill, _center, holder, _assembly, _center_assembly = (
        basic_drilling_resources(LengthUnit.MM)
    )
    dialog = ToolEditorDialog(
        drill, holder_fingerprint=holder.content_fingerprint
    )
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.accessibleName() == "Trình chỉnh sửa Tool"
    assert dialog.profiles.accessibleName() == "Cấu hình Tool theo chương trình"
    assert dialog.profiles.tree.accessibleName() == (
        "Danh sách cấu hình theo chương trình"
    )
    assert not dialog.profiles.is_expanded
    assert dialog.minimumWidth() <= dialog.width()


@pytest.mark.parametrize(
    ("width", "height", "scale_label"),
    ((620, 520, "100"), (775, 650, "125"), (930, 780, "150")),
)
def test_responsive_bounds_have_no_horizontal_scroll_at_dpi_targets(
    qtbot, width: int, height: int, scale_label: str
) -> None:
    tool, holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    profile = _profile(
        tool,
        "parallel_finishing_3d",
        {"stepover_mm": 0.55, "direction_angle_degrees": 45.0},
    )
    configured = replace(tool, program_profiles=(profile,))
    dialog = ToolEditorDialog(
        configured, holder_fingerprint=holder.content_fingerprint
    )
    qtbot.addWidget(dialog)
    dialog.resize(width, height)
    dialog.profiles.set_expanded(True)
    dialog.show()
    qtbot.wait(10)

    assert dialog.width() <= width
    assert dialog.height() <= height
    assert dialog.profiles.tree.horizontalScrollBarPolicy() is (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    ), scale_label
    assert dialog.profiles.tree.viewport().width() > 0
