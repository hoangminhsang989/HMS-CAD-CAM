"""Evidence-contract checks for the Stage 8A.4.1 review package."""

from __future__ import annotations

import hashlib
import json

import pytest
from PySide6.QtGui import QImage

from tools.create_tool_program_profiles_review_package import (
    FONT_POLICY,
    FONT_PROBE_PHRASES,
    JSON_NAMES,
    OUTPUT,
    PNG_NAMES,
    REPOSITORY_ROOT,
    _detect_tofu_candidates,
    _require_valid_font_probe,
    create_package,
)


def _read_json(name: str) -> dict[str, object]:
    return json.loads((OUTPUT / name).read_text(encoding="utf-8"))


def test_review_harness_builds_exact_unique_production_evidence(qapp) -> None:
    create_package()

    expected = {*PNG_NAMES, *JSON_NAMES, "REVIEW_INDEX.md"}
    actual = {path.name for path in OUTPUT.iterdir() if path.is_file()}
    assert actual == expected
    assert len(actual) == 24

    summary = _read_json("summary.json")
    assert summary["stage"] == "8A.4.1"
    assert summary["status"] == "IN PROGRESS"
    assert summary["package_file_count"] == 24
    assert summary["production_model_service_widget_only"] is True
    assert summary["model_state_asserted_before_each_image"] is True
    assert summary["font_policy"] == FONT_POLICY
    assert summary["font_override_applied"] is False
    assert summary["qpa_platform"] == "windows"
    assert summary["application_font_family"] == "Segoe UI"
    assert summary["resolved_font_family"] == "Segoe UI"
    assert summary["application_font_point_size"] == 9.0
    assert summary["font_probe_passed"] is True
    assert summary["vietnamese_glyph_probe_passed"] is True
    assert summary["replacement_character_count"] == 0
    assert summary["tofu_detection_count"] == 0
    assert summary["all_png_text_rendering_valid"] is True
    image_records = [
        *summary["main_image_records"],
        *summary["dpi_image_records"],
    ]
    assert len(image_records) == 16
    assert all(record["text_rendering"]["passed"] for record in image_records)
    assert all(
        record["text_rendering"]["replacement_glyph_count"] == 0
        and record["text_rendering"]["tofu_candidate_count"] == 0
        and record["text_rendering"]["missing_glyph_count"] == 0
        for record in image_records
    )
    recorded_hashes = summary["png_sha256"]
    assert isinstance(recorded_hashes, dict)
    actual_hashes = {
        name: hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest()
        for name in PNG_NAMES
    }
    assert recorded_hashes == actual_hashes
    assert len(set(actual_hashes.values())) == 16
    for name in PNG_NAMES:
        image = QImage(str(OUTPUT / name))
        assert not image.isNull()
        assert image.width() > 0 and image.height() > 0

    source_hashes = summary["source_sha256"]
    assert isinstance(source_hashes, dict)
    for relative, expected_hash in source_hashes.items():
        source = REPOSITORY_ROOT / relative
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_hash

    schemas = _read_json("profile_schema_report.json")
    assert schemas["registry_size"] == 3
    assert schemas["strategy_specific"] is True
    schema_fields = {
        item["strategy_id"]: {
            field["field_id"] for field in item["fields"]
        }
        for item in schemas["schemas"]
    }
    assert "stepdown_mm" in schema_fields["z_level_finishing_3d"]
    assert "stepover_mm" in schema_fields["parallel_finishing_3d"]
    assert "peck_depth_mm" in schema_fields["drilling_v1"]

    resolution = _read_json("resolution_precedence_report.json")
    assert resolution["precedence"] == [
        "Nguyên công hiện tại",
        "Cấu hình Tool theo chương trình",
        "Cấu hình cơ bản của Tool",
        "Chính sách tự động",
        "Giá trị an toàn mặc định",
    ]
    assert resolution["blocked"] is False
    assert all(item["dependency_contribution"] for item in resolution["values"])

    persistence = _read_json("persistence_report.json")
    assert persistence["sqlite_schema_version"] == 4
    assert persistence["sqlite_schema_bumped"] is False
    assert persistence["configured_tool_round_trip"] is True
    assert persistence["legacy_tool_round_trip"] is True
    assert persistence["deterministic_serialization"] is True

    stale = _read_json("stale_dependency_report.json")
    assert stale["calculation_value_changes_fingerprint"] is True
    assert stale["display_name_changes_fingerprint"] is False
    assert stale["updated_at_participates_in_fingerprint"] is False
    assert not any(stale["forbidden_safety_payload_tokens"].values())

    accessibility = _read_json("localization_accessibility_audit.json")
    assert accessibility["forbidden_leak_count"] == 0
    assert accessibility["missing_accessible_name_count"] == 0
    assert all(accessibility["required_phrases"].values())
    assert accessibility["rendered_glyph_validation"]["passed"] is True
    assert accessibility["tested_sample_phrases"] == list(
        FONT_PROBE_PHRASES
    )
    assert accessibility["missing_glyph_count"] == 0
    assert accessibility["replacement_glyph_count"] == 0
    assert accessibility["tofu_candidate_count"] == 0
    assert accessibility["affected_widget_count"] == 0
    assert accessibility["affected_screenshot_count"] == 0

    responsive = _read_json("responsive_bounds_report.json")
    assert responsive["horizontal_scroll_count"] == 0
    assert responsive["clipping_count"] == 0
    assert responsive["overlap_count"] == 0
    assert responsive["truncated_text_count"] == 0
    assert responsive["missing_glyph_count"] == 0
    assert responsive["child_dialog_depth"] == 1
    assert [item["scale"] for item in responsive["measurements"]] == [
        1.0,
        1.25,
        1.5,
    ]
    assert all(
        item["bounds_valid"]
        and item["footer_visible"]
        and item["horizontal_scrollbar_range"] == 0
        and item["clipping_count"] == 0
        and item["overlap_count"] == 0
        and item["truncated_text_count"] == 0
        and item["missing_glyph_count"] == 0
        and item["content_viewport_bounds"]
        and item["footer_bounds"]
        for item in responsive["measurements"]
    )
    assert [
        item["device_pixel_ratio"] for item in responsive["measurements"]
    ] == [1.0, 1.25, 1.5]

    review_index = (OUTPUT / "REVIEW_INDEX.md").read_text(encoding="utf-8")
    assert all(f"`{name}`" in review_index for name in PNG_NAMES)
    assert all(f"`{name}`" in review_index for name in JSON_NAMES)
    assert review_index.count("font/glyph: PASS") == 16


def test_tofu_detector_reports_reused_square_glyph_signature() -> None:
    result = _detect_tofu_candidates(
        {"C": "square", "ấ": "square", "Z": "square", "·": "dot"}
    )

    assert result["candidate_count"] == 3
    assert result["candidate_characters"] == ["C", "Z", "ấ"]


def test_font_probe_failure_is_fail_closed() -> None:
    failed_probe = {
        "qpa_platform": "windows",
        "font_probe_passed": False,
        "missing_glyph_count": 0,
        "replacement_glyph_count": 0,
        "tofu_candidate_count": 4,
    }

    with pytest.raises(RuntimeError, match="Vietnamese rendered glyph probe"):
        _require_valid_font_probe(failed_probe)


def test_non_production_qpa_is_fail_closed() -> None:
    invalid_probe = {
        "qpa_platform": "offscreen",
        "font_probe_passed": True,
        "missing_glyph_count": 0,
        "replacement_glyph_count": 0,
        "tofu_candidate_count": 0,
    }

    with pytest.raises(RuntimeError, match="production Windows QPA"):
        _require_valid_font_probe(invalid_probe)
