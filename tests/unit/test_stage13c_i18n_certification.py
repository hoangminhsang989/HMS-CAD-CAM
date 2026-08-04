"""VI/EN/KO Stage 13C catalog and runtime language-switch certification."""
from __future__ import annotations

from _stage13c_turning_runtime_fixtures import (
    TURNING_STRATEGIES,
    bind_runtime,
    select_materials,
)
from hms_cadcam.ui.i18n import UiLanguage, build_default_catalogs, translation_service


def test_stage13c_catalogs_have_exact_parity_no_duplicates_or_empty_values():
    catalogs = build_default_catalogs()
    keys = {
        key
        for key in catalogs[UiLanguage.VI_VN].entries
        if key.startswith("stage13c.advisor.")
    }
    assert keys
    for language in UiLanguage:
        catalog = catalogs[language]
        assert keys <= set(catalog.entries)
        assert not catalog.duplicate_keys
        assert all(catalog.entries[key].strip() for key in keys)
    assert any("공작물" in catalogs[UiLanguage.KO_KR].entries[key] for key in keys)


def test_runtime_language_switch_preserves_owner_material_draft_result_and_selection():
    service = translation_service()
    original_language = service.language
    runtime, workspace, session = bind_runtime(TURNING_STRATEGIES[0])
    try:
        select_materials(workspace)
        workspace.advisor_panel.analyze.click()
        result = session.current_result
        assert result is not None
        workspace.advisor_panel.field_checks["spindle_speed_rpm"].setChecked(True)
        before = runtime.adapter.context.draft_bridge.capture_snapshot()
        owner = result.snapshot.input_digest if result.snapshot is not None else None
        for language in UiLanguage:
            service.set_language(language)
            workspace.retranslate_ui()
            assert workspace.advisor_panel.selected_workpiece_material() == "ISO_P"
            assert workspace.advisor_panel.selected_tool_material() == "CARBIDE"
            assert runtime.adapter.context.draft_bridge.capture_snapshot() == before
            assert session.current_result is result
            assert session.current_result.snapshot is not None
            assert session.current_result.snapshot.input_digest == owner
            assert workspace.advisor_panel.field_checks["spindle_speed_rpm"].isChecked()
    finally:
        service.set_language(original_language)
        workspace.retranslate_ui()
        workspace.close()
