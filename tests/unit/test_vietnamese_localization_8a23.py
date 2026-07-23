"""Vietnamese production-UI acceptance checks for Stage 8A.2.3."""

from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtGui import QRawFont
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.cam3d.parallel import ParallelProgress, ParallelProgressPhase
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftState,
    FunctionEditorPage,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.strategies.parallel import build_parallel_schema
from hms_cadcam.ui.function_editor.strategies.parallel import parallel_applied_values
from hms_cadcam.ui.localization import (
    DISPLAY_VALUE_MAPPINGS,
    OPERATION_DISPLAY_NAMES,
    TECHNICAL_TERMS,
    UI_TRANSLATIONS,
    display_value,
    display_value_list,
    operation_display_name,
    operation_type_display_name,
    ui_text,
)
from tests.unit.test_parallel_finishing_function_editor_8a23 import (
    _context,
    _valid_values,
)
from tools.audit_vietnamese_ui import (
    audit_production_ui,
    audit_runtime_review_states,
    write_reports,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_central_catalog_contains_required_hms_terminology() -> None:
    required = {
        "Parallel Finishing": "Gia công tinh song song",
        "Geometry": "Hình học",
        "Machining Faces": "Bề mặt gia công",
        "Selected Faces": "Bề mặt đã chọn",
        "Stepover": "Bước ngang",
        "Tolerance": "Dung sai",
        "Surface Allowance": "Lượng dư bề mặt",
        "Safety Diagnostics": "Chẩn đoán an toàn",
        "Operation Manager": "Quản lý nguyên công",
        "Calculate": "Tính toán",
        "Cancel Calculation": "Hủy tính toán",
    }
    assert all(UI_TRANSLATIONS.get(source) == target for source, target in required.items())
    assert {"CAD", "CAM", "Tool", "Holder", "Post", "G-code"}.issubset(
        TECHNICAL_TERMS
    )


def test_nine_operation_display_names_are_localized_without_changing_ids() -> None:
    expected = {
        "Facing 2.5D": "Phay mặt 2.5D",
        "Planar Face Facing": "Phay các mặt phẳng",
        "2D Contour": "Phay biên dạng 2D",
        "Pocket 2.5D": "Phay hốc 2.5D",
        "Drilling": "Khoan",
        "Tapping": "Taro",
        "Reaming": "Doa lỗ",
        "Boring": "Khoét lỗ",
        "Parallel Finishing": "Gia công tinh song song",
    }
    assert dict(OPERATION_DISPLAY_NAMES) == expected
    assert {
        source: operation_display_name(source) for source in expected
    } == expected
    assert operation_display_name(
        "Tinh mặt khuôn A", strategy_key="parallel_finishing_3d"
    ) == "Tinh mặt khuôn A"
    assert operation_display_name("", strategy_key="contour_2d") == (
        "Phay biên dạng 2D"
    )
    stable_ids = {
        "facing_2_5d": "Phay mặt 2.5D",
        "contour_2d": "Phay biên dạng 2D",
        "pocket_2_5d": "Phay hốc 2.5D",
        "parallel_finishing_3d": "Gia công tinh song song",
        "drilling_v1": "Khoan",
        "tapping_v1": "Taro",
        "reaming_v1": "Doa lỗ",
        "boring_v1": "Khoét lỗ",
    }
    assert {
        strategy: operation_type_display_name(strategy)
        for strategy in stable_ids
    } == stable_ids
    assert tuple(stable_ids) == (
        "facing_2_5d",
        "contour_2d",
        "pocket_2_5d",
        "parallel_finishing_3d",
        "drilling_v1",
        "tapping_v1",
        "reaming_v1",
        "boring_v1",
    )


def test_production_ui_audit_is_zero_and_reports_are_deterministic(
    tmp_path: Path,
) -> None:
    result = audit_production_ui()
    assert result.total >= 900
    assert result.untranslated == 0
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_reports(result, first)
    write_reports(result, second)
    names = {
        "untranslated_strings.json",
        "allowed_technical_terms.json",
        "translated_strings_summary.json",
        "UI_VIETNAMESE_AUDIT.md",
        "runtime_rendered_strings.json",
        "runtime_untranslated_strings.json",
        "display_value_mappings.json",
    }
    assert {item.name for item in first.iterdir()} == names
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_display_value_mapper_covers_required_dynamic_categories() -> None:
    expected = {
        "geometry_resolution": {
            "RESOLVED": "Đã xác định",
            "UNRESOLVED": "Chưa xác định",
            "MISSING": "Bị thiếu",
            "STALE": "Cần cập nhật",
            "INVALID": "Không hợp lệ",
        },
        "setup_role": {
            "PRIMARY": "Chính",
            "SECONDARY": "Phụ",
            "ACTIVE": "Đang sử dụng",
            "INACTIVE": "Không hoạt động",
        },
        "safety_scope": {
            "declared_assembly_holder_verified": "Cụm Tool đã xác minh Holder",
            "declared_assembly_holder_absent": "Cụm Tool chưa khai báo Holder",
        },
        "safety_component": {
            "cutter": "Dao cắt",
            "shank": "Cán dao",
            "holder": "Holder",
            "tool_assembly": "Cụm Tool",
            "rapid": "Chạy nhanh",
            "link": "Liên kết",
            "approach": "Tiếp cận",
            "retract": "Rút dao",
        },
        "geometry_source": {
            "check_surface": "Bề mặt kiểm tra",
            "geometry_reference": "Tham chiếu hình học",
            "selected_face": "Bề mặt đã chọn",
            "protected_face": "Bề mặt được bảo vệ",
        },
    }
    for category, values in expected.items():
        assert all(
            display_value(source, category) == target
            for source, target in values.items()
        )
        assert set(values).issubset(DISPLAY_VALUE_MAPPINGS[category])
    assert display_value("verified", "holder_state") == "Holder đã được xác minh"
    assert display_value_list("cutter, shank", "safety_component") == (
        "Dao cắt và Cán dao"
    )
    assert display_value("domain_value", "unknown_category") == "domain_value"


def test_parallel_dynamic_summaries_use_display_values_not_internal_codes() -> None:
    context, _machine = _context()
    values = parallel_applied_values(context)
    assert values["geometry_summary"] == "1 bề mặt gia công · Đã xác định"
    assert values["selected_body_setup_summary"].endswith(" · Chính")
    assert values["holder_state"] == (
        "Chưa khai báo Holder · Đã kiểm tra Dao cắt và Cán dao · "
        "Holder chưa được xác minh"
    )
    assert values["holder_scope"].startswith("Cụm Tool chưa khai báo Holder")
    assert "not declared" not in values["holder_state"].casefold()
    assert "cutter/shank" not in values["holder_state"].casefold()
    assert not any(
        raw in str(values[field])
        for raw in ("RESOLVED", "PRIMARY", "declared_assembly_holder_absent")
        for field in ("geometry_summary", "selected_body_setup_summary", "holder_scope")
    )


def test_runtime_rendered_audit_covers_dynamic_names_and_preserves_only_technical_ids() -> None:
    result = audit_runtime_review_states()
    assert result.state_count >= 21
    assert result.total >= 1_000
    assert result.untranslated == 0
    assert any(
        "parallel.safety.protected_face_collision" in item.text
        for item in result.entries
    )
    operation_entries = tuple(
        item
        for item in result.entries
        if item.source == "operation_display_name"
    )
    assert len(operation_entries) == 9
    assert {item.text for item in operation_entries} == set(
        OPERATION_DISPLAY_NAMES.values()
    )
    assert all(item.classification == "translated" for item in operation_entries)
    assert all("dynamic_operation_name" in item.categories for item in operation_entries)
    uuid_pattern = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    )
    assert any(uuid_pattern.search(item.text) for item in result.entries)
    for item in result.entries:
        if "declared_assembly_holder_" in item.text:
            assert item.text.startswith("Mã phạm vi nội bộ:")


def test_parallel_editor_sections_footer_validation_and_progress_are_vietnamese() -> None:
    application = _application()
    context, machine = _context()
    schema = build_parallel_schema(context)
    page = FunctionEditorPage(
        FunctionEditorDraftState(
            schema,
            _valid_values(context, machine),
        )
    )
    try:
        assert page.summary.title.text() == "Gia công tinh song song"
        assert page._section_widgets["geometry"].title_label.text() == "HÌNH HỌC"
        assert (
            page._section_widgets["automatic_summary"].title_label.text()
            == "TÓM TẮT TÍNH TOÁN TỰ ĐỘNG"
        )
        index = page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        page.disclosure_selector.setCurrentIndex(index)
        page._field_changed("stepover_override_enabled", True)
        assert page._field_widgets["stepover_mm"].label.text() == "Bước ngang *"
        assert "Tool" in page._field_widgets["tool_assembly_id"].label.text()
        assert "Holder" in page._field_widgets["holder_state"].label.text()
        assert page.footer.buttons[FunctionEditorAction.VALIDATE].text() == "Kiểm tra"
        assert page.footer.buttons[FunctionEditorAction.APPLY].text() == "Áp dụng"
        assert page.footer.buttons[FunctionEditorAction.CALCULATE].text() == "Tính toán"

        page._field_widgets["stepover_mm"].set_value("0")
        page._field_changed("stepover_mm", "0")
        page.validate_draft()
        assert "Bước ngang phải lớn hơn 0." in page._field_widgets[
            "stepover_mm"
        ].diagnostic_label.text()

        page.set_calculation_active(True)
        page.update_calculation_progress(
            ParallelProgress(
                context.operation.operation_id,
                ParallelProgressPhase.SAFETY_VALIDATION,
                7,
                10,
            )
        )
        application.processEvents()
        assert page.calculation_progress.phase.text() == "Giai đoạn: Kiểm tra an toàn"
        assert page.calculation_progress.percentage.text() == "Tổng thể: 70%"
        assert page.calculation_progress.cancel_button.text() == "Hủy tính toán"
    finally:
        page.close()
        page.deleteLater()
        application.processEvents()


def test_diagnostic_messages_translate_but_internal_codes_do_not() -> None:
    code = "parallel.safety.holder_collision"
    message = "Holder collision on motion 12; clearance -0.5 mm."
    translated = ui_text(message)
    assert translated == "Holder va chạm tại chuyển động 12; khoảng hở -0.5 mm."
    assert code == "parallel.safety.holder_collision"
    typed_id = "operation:b9510e14-21b7-429e-85fa-b60545a94794"
    assert typed_id in ui_text(f"{typed_id} · VALID")
    assert ui_text("Tool") == "Tool"
    assert ui_text("Holder") == "Holder"


def test_application_font_supports_vietnamese_without_replacement_glyph() -> None:
    application = _application()
    raw_font = QRawFont.fromFont(application.font())
    sample = "ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậếềểễệ"
    assert raw_font.isValid()
    assert all(raw_font.supportsCharacter(ord(character)) for character in sample)
    assert "�" not in "Gia công tinh song song · Chẩn đoán an toàn"
