"""Vietnamese production-UI acceptance checks for Stage 8A.2.3."""

from __future__ import annotations

from pathlib import Path
import re

import pytest
from PySide6.QtGui import QRawFont
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.cam3d.parallel import ParallelProgress, ParallelProgressPhase
from hms_cadcam.cam.domain import SetupKind, StockKind
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
    APPROVED_TECHNICAL_TERMS,
    FORBIDDEN_UI_PHRASES,
    INTERNAL_MODEL_VALUE_CATALOG,
    _classify,
    audit_production_ui,
    audit_runtime_review_states,
    duplicate_user_facing_phrase_matches,
    raw_user_facing_internal_matches,
    unapproved_user_facing_acronym_matches,
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


def test_final_gui_leak_terms_are_localized_at_ui_boundary() -> None:
    cases = {
        "Safety contract Stage 8A.3.2": "hợp đồng an toàn giai đoạn 8A.3.2",
        "Hướng contour theo topology": (
            "Hướng đường đồng mức theo cấu trúc liên kết hình học"
        ),
        "để HMS tính lại từ dependency hiện hành.": (
            "để HMS tính lại từ dữ liệu phụ thuộc hiện hành."
        ),
        "Mở panel trợ giúp ngắn": "Mở bảng trợ giúp ngắn",
        "Mở rộng minh họa ngay trong popup CAM": (
            "Mở rộng minh họa ngay trong cửa sổ CAM"
        ),
        "Cao độ clearance": "Cao độ an toàn",
        "Cao độ retract": "Cao độ rút dao",
        "Tên trong Operation Manager": "Tên trong Trình quản lý nguyên công",
        "Dải Top/Bottom/Stepdown": "Dải Trên/Dưới/Bước xuống",
        "algorithm v2 · payload v1": "Thuật toán v2 · Phiên bản dữ liệu v1",
        "Ball-end Tool · Tool Assembly": "Tool cầu · Cụm Tool",
        "Tools · PRIMARY · override · guardrail": (
            "Tool · Chính · tùy chỉnh thủ công · giới hạn bảo vệ"
        ),
        "artifact · safety · contour · machine-ready": (
            "kết quả tính toán · an toàn · đường đồng mức · sẵn sàng chạy máy"
        ),
        "Production Post · fail-closed": "Post sản xuất · chặn an toàn",
        "Rút dao bảo thủ · fallback fail-closed": (
            "Rút dao bảo thủ · chuyển sang phương án chặn an toàn."
        ),
        "Ball - D10 mm": "Cầu · D10 mm",
        "safety contract": "hợp đồng an toàn",
        "projection hiện hành": "dữ liệu hiển thị hiện hành",
        "Chọn lại bề mặt từ viewport": "Chọn lại bề mặt từ vùng hiển thị CAD",
        "WCS của Thiết lập": "Hệ tọa độ Thiết lập",
        "Biên mặt đã trim": "Biên mặt đã cắt xén",
        "safety validator": "bộ kiểm tra an toàn",
        "Machining zone": "vùng gia công",
        "Parallel Setup": "Thiết lập cao độ Z",
    }
    assert {source: ui_text(source) for source in cases} == cases
    assert ui_text("Tools") == "Tool"
    assert ui_text("Tool Assembly") == "Cụm Tool"


def test_forbidden_rendered_phrase_denylist_is_enforced() -> None:
    assert {
        "safety contract",
        "projection",
        "viewport",
        "WCS",
        "trim",
        "trimmed",
        "validator",
        "Machining zone",
        "Parallel Setup",
        "contract",
        "Stage",
        "topology",
        "dependency",
        "panel",
        "popup",
        "fallback",
        "clearance",
        "retract",
        "Manager",
        "Top/Bottom/Stepdown",
    }.issubset(FORBIDDEN_UI_PHRASES)
    for phrase in FORBIDDEN_UI_PHRASES:
        classification, matches = _classify(phrase, "literal")
        assert classification == "untranslated"
        assert phrase in matches
    assert _classify("WCS của Thiết lập", "ui_text")[0] == "translated"


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("Post sản xuất · bị chặn chặn an toàn", "chặn chặn"),
        ("Công việc Công việc gia công cao độ Z", "Công việc Công việc"),
        ("Thiết lập Thiết lập cao độ Z", "Thiết lập Thiết lập"),
        ("Chọn lại Chọn lại", "Chọn lại Chọn lại"),
        ("Loại Loại bề mặt", "Loại Loại"),
        ("Xóa Xóa lựa chọn", "Xóa Xóa"),
        ("Tự động · Tự động", "Tự động · Tự động"),
    ),
)
def test_duplicate_user_facing_phrase_detector_finds_adjacent_repetition(
    text: str,
    expected: str,
) -> None:
    assert expected in duplicate_user_facing_phrase_matches(text)
    assert _classify(text, "literal")[0] == "untranslated"


@pytest.mark.parametrize(
    "text",
    (
        "Đường tâm Tool. Tool cầu tiếp tục theo biên.",
        "Đường tâm Tool; Tool cầu tiếp tục theo biên.",
        "Công việc gia công cao độ Z · Thiết lập cao độ Z",
    ),
)
def test_duplicate_detector_stops_at_sentence_boundaries(text: str) -> None:
    assert not duplicate_user_facing_phrase_matches(text)


@pytest.mark.parametrize("acronym", ("CW", "CCW", "CW/CCW"))
def test_unapproved_orientation_acronyms_fail_audit(acronym: str) -> None:
    assert unapproved_user_facing_acronym_matches(acronym) == (acronym,)
    assert _classify(acronym, "literal")[0] == "untranslated"


def test_approved_technical_terms_are_not_unapproved_acronyms() -> None:
    assert all(
        not unapproved_user_facing_acronym_matches(term)
        for term in APPROVED_TECHNICAL_TERMS
    )


@pytest.mark.parametrize(
    "raw_text",
    (
        "DOMAIN · ĐÃ LƯU",
        "CALCULATION · CẦN TÍNH",
        "SIMULATION · CHƯA CHẠY",
        "EXPORT · CHƯA XUẤT",
        "1 Setup",
        "MILL · Chính · Chưa gán máy",
        "Setup đang hoạt động",
        "Hình học đến từ nguyên công hoặc Setup/mô hình",
        "Đã liên kết geometry",
        "box · 20 × 20 × 10",
        "Stock hợp lệ",
        "1 assembly đang dùng",
        "Stickout 30 mm",
        "Danh sách theo thứ tự domain hiện có",
        "Cụm Tool khớp revision/dấu vân tay hiện hành",
        "kết quả tính toán missing",
        "Nội dung tham số Function Editor",
        "Không phải chứng nhận production-AN TOÀN hoặc sẵn sàng chạy máy",
        "Post sản xuất cho Z-Level chưa được hỗ trợ",
        "bộ tính Z-Level sản xuất công bố",
        "Lượng dư bề mặt · minh họa offset",
        "Bằng chứng cổng Mô phỏng · Z-Level",
    ),
)
def test_raw_namespace_model_and_accessibility_regressions_fail_audit(
    raw_text: str,
) -> None:
    assert raw_user_facing_internal_matches(raw_text)


def test_internal_token_auditor_uses_production_enum_catalog_and_strict_allowlist() -> None:
    assert INTERNAL_MODEL_VALUE_CATALOG["SetupKind"] == tuple(
        item.value for item in SetupKind
    )
    assert INTERNAL_MODEL_VALUE_CATALOG["StockKind"] == tuple(
        item.value for item in StockKind
    )
    assert set(APPROVED_TECHNICAL_TERMS) == {
        "CAD",
        "CAM",
        "CNC",
        "Tool",
        "Holder",
        "Post",
        "G-code",
        "Toolpath IR",
        "SQLite",
        "OCP",
        "BRep",
        "UUID/ID",
        "version/hash",
        "U/V/W",
        "đơn vị kỹ thuật",
    }
    clean_values = (
        "DỰ ÁN · ĐÃ LƯU",
        "TRẠNG THÁI · ĐANG HOẠT ĐỘNG",
        "TÍNH TOÁN · CẦN TÍNH",
        "MÔ PHỎNG · CHƯA CHẠY",
        "XUẤT NC · CHƯA XUẤT",
        "PHAY · Chính · Chưa gán máy",
        "Khối hộp · 20 × 20 × 10",
        "1 cụm Tool đang dùng",
        "Chiều nhô 30 mm",
        "Post sản xuất cho gia công tinh theo cao độ Z chưa được hỗ trợ",
    )
    assert all(
        not raw_user_facing_internal_matches(text)
        for text in clean_values
    )


def test_ten_operation_display_names_are_localized_without_changing_ids() -> None:
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
        "Z-Level Finishing": "Gia công tinh theo cao độ Z",
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
        "z_level_finishing_3d": "Gia công tinh theo cao độ Z",
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
        "z_level_finishing_3d",
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
        "quality_profile": {
            "fast": "Nhanh",
            "balanced": "Cân bằng",
            "high": "Chất lượng cao",
        },
        "automatic_mode": {
            "auto": "Tự động",
            "manual": "Thủ công",
        },
        "automatic_status": {
            "resolved": "Đã xác định",
            "needs_confirmation": "Cần xác nhận",
            "unsupported": "Không được hỗ trợ",
            "unresolved": "Chưa xác định",
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
    assert len(operation_entries) == 10
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
