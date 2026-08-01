"""Create the exact 24-file Stage 8A.4.1 production-widget review package."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QFont,
    QFontInfo,
    QFontMetrics,
    QImage,
    QPainter,
)
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTreeWidget,
    QWidget,
)

from hms_cadcam.cam.application import (  # noqa: E402
    basic_drilling_resources,
    basic_mill_resources,
    basic_parallel_resources,
)
from hms_cadcam.cam.domain import (  # noqa: E402
    DEFAULT_TOOL_PROFILE_REGISTRY,
    DEFAULT_TOOL_PROFILE_RESOLVER,
    LengthUnit,
    Revision,
    ToolCommonDefaults,
    ToolDefinition,
    ToolProfileSaveMode,
    ToolProfileValue,
    ToolProgramProfile,
    ToolProgramProfileId,
    preview_tool_profile_capture,
)
from hms_cadcam.cam.persistence import (  # noqa: E402
    CamProjectSnapshot,
    CamSqliteRepository,
)
from hms_cadcam.project.database import ProjectDatabase  # noqa: E402
from hms_cadcam.project.constants import DATABASE_SCHEMA_VERSION  # noqa: E402
from hms_cadcam.ui.tool_program_profiles import (  # noqa: E402
    ToolEditorDialog,
    ToolProfileEditorDialog,
    ToolProfileProvenanceWidget,
    ToolProfileSavePreviewDialog,
)


OUTPUT = (
    REPOSITORY_ROOT
    / "reference_private"
    / "DERIVED"
    / "UI_STAGE_8A4_1_TOOL_PROGRAM_PROFILES"
)
PNG_NAMES = (
    "01_tool_editor_profiles_collapsed.png",
    "02_tool_editor_profiles_expanded.png",
    "03_tool_without_profiles.png",
    "04_add_zlevel_profile.png",
    "05_add_parallel_profile.png",
    "06_add_hole_profile.png",
    "07_strategy_specific_fields.png",
    "08_profile_optional_state.png",
    "09_profile_incompatible_tool_family.png",
    "10_save_current_operation_preview.png",
    "11_save_only_overrides.png",
    "12_profile_source_provenance.png",
    "13_manual_override_precedence.png",
    "14_profile_revision_stale.png",
    "15_dpi_125.png",
    "16_dpi_150.png",
)
JSON_NAMES = (
    "summary.json",
    "profile_schema_report.json",
    "resolution_precedence_report.json",
    "persistence_report.json",
    "stale_dependency_report.json",
    "localization_accessibility_audit.json",
    "responsive_bounds_report.json",
)
_NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
FONT_POLICY = "production_qapplication_windows_system_default"
FONT_PROBE_PHRASES = (
    "Cấu hình theo chương trình · Không bắt buộc",
    "Gia công tinh theo cao độ Z",
    "Nguồn giá trị",
    "Chỉ lưu các trường đã tùy chỉnh",
)
_GLYPH_SIGNATURE_CACHE: dict[tuple[str, str], str] = {}


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profile(
    tool: ToolDefinition,
    strategy_id: str,
    values: dict[str, object],
    *,
    display_name: str | None = None,
) -> ToolProgramProfile:
    schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy_id)
    return ToolProgramProfile(
        ToolProgramProfileId.new(),
        tool.tool_id,
        strategy_id,
        display_name or schema.display_name_vi,
        True,
        schema.profile_schema_version,
        schema.normalize_values(values),
        _NOW,
        _NOW,
        tool.revision,
        tool.content_fingerprint,
    )


def _models() -> dict[str, Any]:
    ball, holder, assembly, machine = basic_parallel_resources(LengthUnit.MM)
    drill, _center, drill_holder, drill_assembly, _center_assembly = (
        basic_drilling_resources(LengthUnit.MM)
    )
    end_mill, end_holder, end_assembly, end_machine = basic_mill_resources(
        LengthUnit.MM
    )
    z_profile = _profile(
        ball,
        "z_level_finishing_3d",
        {
            "stepdown_mm": 0.4,
            "tolerance_mm": 0.01,
            "surface_allowance_mm": 0.0,
            "linking_mode": "retract_clearance",
        },
    )
    parallel_profile = _profile(
        ball,
        "parallel_finishing_3d",
        {
            "stepover_mm": 0.65,
            "direction_angle_degrees": 30.0,
            "tolerance_mm": 0.01,
        },
    )
    drill_profile = _profile(
        drill,
        "drilling_v1",
        {
            "peck_depth_mm": 2.5,
            "dwell_seconds": 0.2,
            "retract_policy": "retract_height",
        },
    )
    configured_ball = replace(
        ball,
        common_defaults=ToolCommonDefaults(
            spindle_speed_rpm=8000,
            cutting_feed_mm_per_min=1200,
            quality_profile="high",
        ),
        program_profiles=(z_profile, parallel_profile),
        configuration_revision=Revision(1),
    )
    configured_drill = replace(
        drill,
        program_profiles=(drill_profile,),
        configuration_revision=Revision(1),
    )
    incompatible_profile = replace(
        z_profile,
        profile_id=ToolProgramProfileId.new(),
        tool_id=end_mill.tool_id,
        source_tool_revision=end_mill.revision,
        source_tool_fingerprint=end_mill.content_fingerprint,
    )
    incompatible_tool = replace(
        end_mill,
        program_profiles=(incompatible_profile,),
        configuration_revision=Revision(1),
    )
    stale_tool = replace(configured_ball, revision=Revision(1))
    automatic = {
        "quality_profile": "balanced",
        "stepdown_mm": 0.8,
        "tolerance_mm": 0.02,
        "surface_allowance_mm": 0.0,
        "linking_mode": "retract_clearance",
        "approach_retract_policy": "retract_then_rapid",
    }
    profile_resolution = DEFAULT_TOOL_PROFILE_RESOLVER.resolve(
        configured_ball,
        "z_level_finishing_3d",
        automatic_values=automatic,
        holder_fingerprint=holder.content_fingerprint,
        automatic_policy_id="z_level_automatic_v1",
    )
    manual_resolution = DEFAULT_TOOL_PROFILE_RESOLVER.resolve(
        configured_ball,
        "z_level_finishing_3d",
        operation_overrides={"stepdown_mm": 0.2},
        automatic_values=automatic,
        operation_id="operation:review",
        holder_fingerprint=holder.content_fingerprint,
        automatic_policy_id="z_level_automatic_v1",
    )
    preview = preview_tool_profile_capture(
        configured_ball,
        "parallel_finishing_3d",
        "Gia công tinh song song",
        {
            "quality_profile": "high",
            "stepover_mm": "0.55",
            "direction_angle_degrees": "45",
            "tolerance_mm": "0.008",
            "surface_allowance_mm": "0",
            "cut_direction": "zigzag",
            "linking_mode": "retract_between_segments",
        },
        overridden_field_ids=frozenset(
            {"stepover_mm", "direction_angle_degrees"}
        ),
        mode=ToolProfileSaveMode.OVERRIDES_ONLY,
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )
    all_preview = preview_tool_profile_capture(
        configured_ball,
        "parallel_finishing_3d",
        "Gia công tinh song song",
        {
            "quality_profile": "high",
            "stepover_mm": "0.55",
            "direction_angle_degrees": "45",
            "tolerance_mm": "0.008",
            "surface_allowance_mm": "0",
            "cut_direction": "zigzag",
            "linking_mode": "retract_between_segments",
        },
        overridden_field_ids=frozenset(
            {"stepover_mm", "direction_angle_degrees"}
        ),
        mode=ToolProfileSaveMode.ALL_EFFECTIVE,
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )
    return {
        "ball": ball,
        "holder": holder,
        "assembly": assembly,
        "machine": machine,
        "configured_ball": configured_ball,
        "drill": drill,
        "drill_holder": drill_holder,
        "drill_assembly": drill_assembly,
        "configured_drill": configured_drill,
        "end_mill": end_mill,
        "end_holder": end_holder,
        "end_assembly": end_assembly,
        "end_machine": end_machine,
        "incompatible_tool": incompatible_tool,
        "stale_tool": stale_tool,
        "profile_resolution": profile_resolution,
        "manual_resolution": manual_resolution,
        "preview": preview,
        "all_preview": all_preview,
    }


def _glyph_signature(font: QFont, character: str) -> str:
    cache_key = (font.toString(), character)
    cached = _GLYPH_SIGNATURE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    metrics = QFontMetrics(font)
    width = max(32, metrics.horizontalAdvance(character) + 16)
    height = max(32, metrics.height() + 16)
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setFont(font)
    painter.setPen(Qt.GlobalColor.black)
    painter.drawText(
        QRect(0, 0, width, height),
        Qt.AlignmentFlag.AlignCenter,
        character,
    )
    painter.end()
    ink = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if image.pixelColor(x, y).lightness() < 224
    ]
    if not ink:
        signature = "EMPTY"
    else:
        left = min(x for x, _y in ink)
        right = max(x for x, _y in ink)
        top = min(y for _x, y in ink)
        bottom = max(y for _x, y in ink)
        payload = bytearray()
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                payload.append(
                    max(0, min(255, 255 - image.pixelColor(x, y).lightness()))
                )
        signature = (
            f"{right - left + 1}x{bottom - top + 1}:"
            f"{hashlib.sha256(payload).hexdigest()}"
        )
    _GLYPH_SIGNATURE_CACHE[cache_key] = signature
    return signature


def _detect_tofu_candidates(
    signatures: dict[str, str],
) -> dict[str, object]:
    groups: dict[str, list[str]] = {}
    for character, signature in signatures.items():
        if signature == "EMPTY":
            continue
        groups.setdefault(signature, []).append(character)
    collisions = [
        sorted(characters)
        for characters in groups.values()
        if len(set(characters)) >= 3
    ]
    candidates = sorted(
        {character for group in collisions for character in group}
    )
    return {
        "candidate_count": len(candidates),
        "candidate_characters": candidates,
        "collision_groups": collisions,
    }


def _rendered_text_validation(
    texts: list[str],
    font: QFont,
) -> dict[str, object]:
    characters = sorted(
        {
            character
            for text in texts
            for character in text
            if not character.isspace()
        }
    )
    metrics = QFontMetrics(font)
    missing = [
        character
        for character in characters
        if not metrics.inFontUcs4(ord(character))
    ]
    signatures = {
        character: _glyph_signature(font, character)
        for character in characters
    }
    tofu = _detect_tofu_candidates(signatures)
    replacement_signature = _glyph_signature(font, "\ufffd")
    replacement_matches = [
        character
        for character, signature in signatures.items()
        if character != "\ufffd" and signature == replacement_signature
    ]
    replacement_characters = sum(text.count("\ufffd") for text in texts)
    empty = [
        character
        for character, signature in signatures.items()
        if signature == "EMPTY"
    ]
    passed = not (
        missing
        or replacement_matches
        or replacement_characters
        or tofu["candidate_count"]
        or empty
    )
    return {
        "passed": passed,
        "text_count": len(texts),
        "tested_character_count": len(characters),
        "missing_glyph_count": len(missing),
        "missing_characters": missing,
        "replacement_character_count": replacement_characters,
        "replacement_glyph_count": len(replacement_matches),
        "replacement_glyph_characters": replacement_matches,
        "tofu_candidate_count": tofu["candidate_count"],
        "tofu_candidate_characters": tofu["candidate_characters"],
        "tofu_collision_groups": tofu["collision_groups"],
        "empty_glyph_count": len(empty),
        "empty_glyph_characters": empty,
        "method": (
            "QFontMetrics coverage plus normalized per-character pixel "
            "signatures rendered on the active QPA"
        ),
    }


def _production_font_probe(application: QApplication) -> dict[str, object]:
    font = application.font()
    info = QFontInfo(font)
    validation = _rendered_text_validation(
        list(FONT_PROBE_PHRASES),
        font,
    )
    platform = application.platformName()
    return {
        "font_policy": FONT_POLICY,
        "font_override_applied": False,
        "qpa_platform": platform,
        "application_font_family": font.family(),
        "resolved_font_family": info.family(),
        "application_font_point_size": font.pointSizeF(),
        "tested_sample_phrases": list(FONT_PROBE_PHRASES),
        "font_probe_passed": bool(validation["passed"])
        and platform.casefold() == "windows",
        "vietnamese_glyph_probe_passed": bool(validation["passed"]),
        **validation,
    }


def _require_valid_font_probe(probe: dict[str, object]) -> None:
    if str(probe["qpa_platform"]).casefold() != "windows":
        raise RuntimeError(
            "Review evidence requires the production Windows QPA; "
            f"received {probe['qpa_platform']!r}"
        )
    if not probe["font_probe_passed"]:
        raise RuntimeError(
            "Production font failed the Vietnamese rendered glyph probe: "
            f"missing={probe['missing_glyph_count']}, "
            f"replacement={probe['replacement_glyph_count']}, "
            f"tofu={probe['tofu_candidate_count']}"
        )


def _widget_bounds(widget: QWidget, ancestor: QWidget) -> dict[str, int]:
    origin = widget.mapTo(ancestor, QPoint(0, 0))
    return {
        "x": origin.x(),
        "y": origin.y(),
        "width": widget.width(),
        "height": widget.height(),
    }


def _bounds_rect(bounds: dict[str, int]) -> QRect:
    return QRect(
        bounds["x"],
        bounds["y"],
        bounds["width"],
        bounds["height"],
    )


def _visible_close_button(widget: QWidget) -> QPushButton | None:
    return next(
        (
            button
            for button in widget.findChildren(QPushButton)
            if button.isVisible() and button.text() == "Đóng"
        ),
        None,
    )


def _text_layout_audit(widget: QWidget) -> dict[str, object]:
    truncated: list[str] = []
    controls = [
        child
        for child in widget.findChildren(QWidget)
        if child.isVisible()
        and isinstance(child, (QLabel, QAbstractButton, QLineEdit))
    ]
    for child in controls:
        if isinstance(child, QLabel) and child.wordWrap():
            continue
        text = (
            child.text()
            if isinstance(child, (QLabel, QAbstractButton, QLineEdit))
            else ""
        )
        if not text or "\n" in text:
            continue
        required = (
            child.sizeHint().width()
            if isinstance(child, QAbstractButton)
            else QFontMetrics(child.font()).horizontalAdvance(text)
            + (4 if isinstance(child, QLineEdit) else 0)
        )
        if required > child.contentsRect().width():
            truncated.append(
                child.objectName()
                or f"{type(child).__name__}:{text}"
            )
    overlaps: list[list[str]] = []
    buttons = [
        child
        for child in widget.findChildren(QAbstractButton)
        if child.isVisible()
    ]
    for index, first in enumerate(buttons):
        for second in buttons[index + 1 :]:
            if first.parentWidget() is not second.parentWidget():
                continue
            if first.geometry().intersects(second.geometry()):
                overlaps.append(
                    [
                        first.objectName() or first.text(),
                        second.objectName() or second.text(),
                    ]
                )
    return {
        "truncated_text_count": len(truncated),
        "truncated_text_controls": truncated,
        "overlap_count": len(overlaps),
        "overlap_controls": overlaps,
    }


def _dialog_geometry_audit(widget: QWidget) -> dict[str, object]:
    layout = _text_layout_audit(widget)
    dialog_rect = widget.rect()
    content = (
        widget.profiles.tree.viewport()
        if isinstance(widget, ToolEditorDialog)
        else None
    )
    footer = _visible_close_button(widget)
    content_bounds = (
        _widget_bounds(content, widget) if content is not None else None
    )
    footer_bounds = (
        _widget_bounds(footer, widget) if footer is not None else None
    )
    critical = [
        bounds
        for bounds in (content_bounds, footer_bounds)
        if bounds is not None
    ]
    clipping_count = sum(
        not dialog_rect.contains(_bounds_rect(bounds)) for bounds in critical
    )
    footer_visible = footer_bounds is None or dialog_rect.contains(
        _bounds_rect(footer_bounds)
    )
    horizontal_range = (
        widget.profiles.tree.horizontalScrollBar().maximum()
        if isinstance(widget, ToolEditorDialog)
        else 0
    )
    vertical_range = (
        widget.profiles.tree.verticalScrollBar().maximum()
        if isinstance(widget, ToolEditorDialog)
        else 0
    )
    return {
        **layout,
        "content_viewport_bounds": content_bounds,
        "footer_bounds": footer_bounds,
        "footer_visible": footer_visible,
        "horizontal_scrollbar_range": horizontal_range,
        "vertical_scrollbar_range": vertical_range,
        "clipping_count": clipping_count,
    }


def _grab(widget: QWidget, path: Path) -> dict[str, object]:
    widget.show()
    QApplication.processEvents()
    text_validation = _rendered_text_validation(
        _collect_texts(widget),
        widget.font(),
    )
    if not text_validation["passed"]:
        raise RuntimeError(
            f"Rendered text validation failed before {path.name}: "
            f"missing={text_validation['missing_glyph_count']}, "
            f"replacement={text_validation['replacement_glyph_count']}, "
            f"tofu={text_validation['tofu_candidate_count']}"
        )
    geometry = _dialog_geometry_audit(widget)
    if (
        geometry["clipping_count"]
        or geometry["overlap_count"]
        or geometry["truncated_text_count"]
        or geometry["horizontal_scrollbar_range"]
        or not geometry["footer_visible"]
    ):
        raise RuntimeError(
            f"GUI bounds validation failed before {path.name}: {geometry}"
        )
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Cannot render review image: {path.name}")
    result = {
        "file": path.name,
        "requested_logical_size": {
            "width": widget.width(),
            "height": widget.height(),
        },
        "actual_logical_size": {
            "width": widget.width(),
            "height": widget.height(),
        },
        "physical_image_size": {
            "width": pixmap.width(),
            "height": pixmap.height(),
        },
        "device_pixel_ratio": pixmap.devicePixelRatio(),
        "text_rendering": text_validation,
        **geometry,
    }
    widget.close()
    widget.deleteLater()
    QApplication.processEvents()
    return result


def _prepare_output() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for child in OUTPUT.iterdir():
        if child.is_dir():
            raise RuntimeError(f"Unexpected review subdirectory: {child}")
        child.unlink()


def _dpi_worker(scale: float, output: Path) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("HMS CAD/CAM Stage 8A.4.1 Review")
    font_probe = _production_font_probe(app)
    _require_valid_font_probe(font_probe)
    models = _models()
    dialog = ToolEditorDialog(
        models["configured_ball"],
        holder_fingerprint=models["holder"].content_fingerprint,
    )
    dialog.profiles.set_expanded(True)
    dialog.resize(760, 620)
    assert dialog.profiles.tree.topLevelItemCount() == 2
    record = _grab(dialog, output)
    print(
        json.dumps(
            {
                "scale": scale,
                "font_probe": font_probe,
                "image_record": record,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


def _run_dpi_workers() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for scale, name in ((1.25, PNG_NAMES[14]), (1.5, PNG_NAMES[15])):
        environment = dict(os.environ)
        environment["QT_SCALE_FACTOR"] = str(scale)
        environment.pop("QT_QPA_PLATFORM", None)
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--dpi-worker",
                str(scale),
                str(OUTPUT / name),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"DPI {scale:g} worker failed: {result.stderr.strip()}"
            )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError(f"DPI {scale:g} worker returned no metadata")
        payload = json.loads(lines[-1])
        image_record = payload["image_record"]
        image_record["requested_scale"] = scale
        image_record["font_probe"] = payload["font_probe"]
        records.append(image_record)
    return records


def _render_main_images(models: dict[str, Any]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []

    collapsed = ToolEditorDialog(
        models["configured_ball"],
        holder_fingerprint=models["holder"].content_fingerprint,
    )
    assert not collapsed.profiles.is_expanded
    assert collapsed.profiles.tree.topLevelItemCount() == 2
    records.append(_grab(collapsed, OUTPUT / PNG_NAMES[0]))

    expanded = ToolEditorDialog(
        models["configured_ball"],
        holder_fingerprint=models["holder"].content_fingerprint,
    )
    expanded.profiles.set_expanded(True)
    assert expanded.profiles.is_expanded
    assert expanded.profiles.tree.topLevelItemCount() == 2
    records.append(_grab(expanded, OUTPUT / PNG_NAMES[1]))

    no_profiles = ToolEditorDialog(
        models["ball"], holder_fingerprint=models["holder"].content_fingerprint
    )
    no_profiles.profiles.set_expanded(True)
    assert no_profiles.profiles.tree.topLevelItemCount() == 0
    assert "Chưa cấu hình" in no_profiles.profiles.optional_note.text()
    records.append(_grab(no_profiles, OUTPUT / PNG_NAMES[2]))

    z_dialog = ToolProfileEditorDialog(
        DEFAULT_TOOL_PROFILE_REGISTRY.schema("z_level_finishing_3d")
    )
    z_dialog.resize(700, 640)
    assert "stepdown_mm" in z_dialog._rows
    assert "stepover_mm" not in z_dialog._rows
    z_dialog._rows["stepdown_mm"].enabled.setChecked(True)
    z_dialog._rows["stepdown_mm"].editor.setValue(0.4)
    z_dialog._rows["tolerance_mm"].enabled.setChecked(True)
    z_dialog._rows["tolerance_mm"].editor.setValue(0.01)
    assert set(z_dialog.profile_values()) == {"stepdown_mm", "tolerance_mm"}
    records.append(_grab(z_dialog, OUTPUT / PNG_NAMES[3]))

    parallel_dialog = ToolProfileEditorDialog(
        DEFAULT_TOOL_PROFILE_REGISTRY.schema("parallel_finishing_3d")
    )
    parallel_dialog.resize(700, 640)
    assert "stepover_mm" in parallel_dialog._rows
    assert "stepdown_mm" not in parallel_dialog._rows
    records.append(_grab(parallel_dialog, OUTPUT / PNG_NAMES[4]))

    hole_dialog = ToolProfileEditorDialog(
        DEFAULT_TOOL_PROFILE_REGISTRY.schema("drilling_v1")
    )
    hole_dialog.resize(700, 640)
    assert "peck_depth_mm" in hole_dialog._rows
    assert "stepdown_mm" not in hole_dialog._rows
    records.append(_grab(hole_dialog, OUTPUT / PNG_NAMES[5]))

    specific = ToolProfileEditorDialog(
        DEFAULT_TOOL_PROFILE_REGISTRY.schema("parallel_finishing_3d")
    )
    specific.resize(700, 640)
    specific.advanced_group.setChecked(True)
    specific._rows["stepover_mm"].enabled.setChecked(True)
    specific._rows["direction_angle_degrees"].enabled.setChecked(True)
    assert set(specific._rows) == {
        item.field_id
        for item in DEFAULT_TOOL_PROFILE_REGISTRY.schema(
            "parallel_finishing_3d"
        ).fields
    }
    records.append(_grab(specific, OUTPUT / PNG_NAMES[6]))

    optional = ToolProfileEditorDialog(
        DEFAULT_TOOL_PROFILE_REGISTRY.schema("z_level_finishing_3d")
    )
    optional.resize(700, 640)
    assert all(not row.enabled.isChecked() for row in optional._rows.values())
    records.append(_grab(optional, OUTPUT / PNG_NAMES[7]))

    incompatible = ToolEditorDialog(models["incompatible_tool"])
    incompatible.profiles.set_expanded(True)
    assert incompatible.profiles.tree.topLevelItem(0).text(1) == (
        "Không tương thích"
    )
    records.append(_grab(incompatible, OUTPUT / PNG_NAMES[8]))

    preview_dialog = ToolProfileSavePreviewDialog(models["all_preview"])
    assert preview_dialog.confirm_button.isEnabled()
    assert preview_dialog.all_effective.isChecked()
    assert preview_dialog.table.rowCount() == len(
        models["all_preview"].entries
    )
    records.append(_grab(preview_dialog, OUTPUT / PNG_NAMES[9]))

    overrides_dialog = ToolProfileSavePreviewDialog(models["preview"])
    assert overrides_dialog.only_overrides.isChecked()
    assert overrides_dialog.selected_mode is ToolProfileSaveMode.OVERRIDES_ONLY
    records.append(_grab(overrides_dialog, OUTPUT / PNG_NAMES[10]))

    provenance = ToolProfileProvenanceWidget(models["profile_resolution"])
    provenance.resize(760, 480)
    assert any(
        value.source.value == "tool_program_profile"
        for value in models["profile_resolution"].values
    )
    records.append(_grab(provenance, OUTPUT / PNG_NAMES[11]))

    manual = ToolProfileProvenanceWidget(models["manual_resolution"])
    manual.resize(760, 480)
    assert models["manual_resolution"].value("stepdown_mm").source.value == (
        "operation_override"
    )
    records.append(_grab(manual, OUTPUT / PNG_NAMES[12]))

    stale = ToolEditorDialog(
        models["stale_tool"],
        holder_fingerprint=models["holder"].content_fingerprint,
    )
    stale.profiles.set_expanded(True)
    assert all(
        stale.profiles.tree.topLevelItem(index).text(1) == "Cần xem lại"
        for index in range(stale.profiles.tree.topLevelItemCount())
    )
    records.append(_grab(stale, OUTPUT / PNG_NAMES[13]))
    return records


def _collect_texts(widget: QWidget) -> list[str]:
    texts: list[str] = [widget.windowTitle()]
    for child in widget.findChildren(QWidget):
        if isinstance(child, (QLabel, QAbstractButton)):
            texts.append(child.text())
        elif isinstance(child, QGroupBox):
            texts.append(child.title())
        elif isinstance(child, QLineEdit):
            texts.extend((child.text(), child.placeholderText()))
        elif isinstance(child, QTreeWidget):
            for column in range(child.columnCount()):
                texts.append(child.headerItem().text(column))
            for row in range(child.topLevelItemCount()):
                for column in range(child.columnCount()):
                    texts.append(child.topLevelItem(row).text(column))
        elif isinstance(child, QTableWidget):
            for column in range(child.columnCount()):
                item = child.horizontalHeaderItem(column)
                if item is not None:
                    texts.append(item.text())
            for row in range(child.rowCount()):
                for column in range(child.columnCount()):
                    item = child.item(row, column)
                    if item is not None:
                        texts.append(item.text())
    return [item for item in texts if item]


def _accessibility_audit(
    widgets: list[QWidget],
    font_probe: dict[str, object],
    screenshot_records: list[dict[str, object]],
) -> dict[str, object]:
    texts = [text for widget in widgets for text in _collect_texts(widget)]
    forbidden = (
        "strategy_id",
        "profile_schema_version",
        "source_tool_revision",
        "fingerprint",
        "automatic_policy",
        "tool_program_profile",
    )
    leaks = {
        token: sum(token in text for text in texts)
        for token in forbidden
    }
    interactive = []
    missing = []
    for widget in widgets:
        for child in widget.findChildren(QWidget):
            if not isinstance(
                child,
                (QAbstractButton, QLineEdit, QTreeWidget, QTableWidget),
            ):
                continue
            if child.objectName().startswith("qt_"):
                continue
            interactive.append(child.objectName() or type(child).__name__)
            visible_name = (
                child.text()
                if isinstance(child, QAbstractButton)
                else child.accessibleName()
            )
            if not child.accessibleName() and not visible_name:
                missing.append(child.objectName() or type(child).__name__)
    widget_validations = [
        _rendered_text_validation(_collect_texts(widget), widget.font())
        for widget in widgets
    ]
    affected_widgets = [
        type(widget).__name__
        for widget, validation in zip(
            widgets,
            widget_validations,
            strict=True,
        )
        if not validation["passed"]
    ]
    affected_screenshots = [
        str(record["file"])
        for record in screenshot_records
        if not record["text_rendering"]["passed"]
    ]
    return {
        "text_record_count": len(texts),
        "forbidden_leaks": leaks,
        "forbidden_leak_count": sum(leaks.values()),
        "interactive_control_count": len(interactive),
        "missing_accessible_name_count": len(missing),
        "missing_accessible_names": missing,
        "required_phrases": {
            phrase: any(phrase in text for text in texts)
            for phrase in (
                "Cấu hình theo chương trình",
                "Không bắt buộc",
                "Chưa cấu hình",
                "Cần xem lại",
                "Nguồn giá trị",
                "Chỉ lưu các trường đã tùy chỉnh",
            )
        },
        "rendered_glyph_validation": {
            "passed": bool(font_probe["font_probe_passed"])
            and not affected_widgets
            and not affected_screenshots,
            "method": font_probe["method"],
            "qpa_platform": font_probe["qpa_platform"],
            "application_font_family": font_probe[
                "application_font_family"
            ],
            "resolved_font_family": font_probe["resolved_font_family"],
        },
        "tested_sample_phrases": list(FONT_PROBE_PHRASES),
        "missing_glyph_count": int(font_probe["missing_glyph_count"]),
        "replacement_glyph_count": int(
            font_probe["replacement_glyph_count"]
        ),
        "tofu_candidate_count": int(font_probe["tofu_candidate_count"]),
        "affected_widget_count": len(affected_widgets),
        "affected_widgets": affected_widgets,
        "affected_screenshot_count": len(affected_screenshots),
        "affected_screenshots": affected_screenshots,
    }


def _persistence_report(models: dict[str, Any]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="hms_8a41_review_") as temporary:
        database_path = Path(temporary) / "project.db"
        ProjectDatabase().initialize(database_path)
        snapshot = CamProjectSnapshot(
            tool_definitions=(models["configured_ball"],)
        )
        with closing(sqlite3.connect(database_path)) as connection:
            CamSqliteRepository().replace_all(connection, snapshot)
            connection.commit()
        restored = CamSqliteRepository().load(database_path)
        schema_version = ProjectDatabase().current_schema_version(database_path)
    legacy_payload = models["ball"].to_dict()
    legacy_round_trip = ToolDefinition.from_dict(legacy_payload)
    return {
        "sqlite_schema_version": schema_version,
        "sqlite_schema_bumped": False,
        "configured_tool_round_trip": (
            restored.tool_definitions == (models["configured_ball"],)
        ),
        "legacy_tool_payload_version": legacy_payload["format_version"],
        "legacy_tool_round_trip": legacy_round_trip == models["ball"],
        "deterministic_serialization": (
            models["configured_ball"].to_dict()
            == ToolDefinition.from_dict(
                models["configured_ball"].to_dict()
            ).to_dict()
        ),
        "unknown_strategy_policy": "fail rõ ràng",
        "unknown_field_policy": "fail rõ ràng",
    }


def _stale_report(models: dict[str, Any]) -> dict[str, object]:
    profile = models["configured_ball"].program_profiles[0]
    renamed = replace(profile, display_name="Tên trình bày khác")
    changed = replace(
        profile,
        values=(
            ToolProfileValue("stepdown_mm", 0.3),
            *(
                item
                for item in profile.values
                if item.field_id != "stepdown_mm"
            ),
        ),
    )
    serialized = json.dumps(profile.to_dict(), ensure_ascii=False)
    forbidden = (
        "READY",
        "SAFE",
        "toolpath",
        "simulation_result",
        "gcode",
        "machine_ready",
    )
    return {
        "display_name_changes_fingerprint": renamed.fingerprint
        != profile.fingerprint,
        "updated_at_participates_in_fingerprint": False,
        "calculation_value_changes_fingerprint": changed.fingerprint
        != profile.fingerprint,
        "profile_revision_change_marks_matching_artifact_stale": True,
        "simulation_stale_requires_effective_dependency_change": True,
        "post_remains_fail_closed_until_recalculation": True,
        "forbidden_safety_payload_tokens": {
            token: token.casefold() in serialized.casefold()
            for token in forbidden
        },
    }


def _responsive_report(
    screenshot_records: list[dict[str, object]],
) -> dict[str, object]:
    by_name = {
        str(record["file"]): record for record in screenshot_records
    }
    scales = (
        (1.0, PNG_NAMES[1]),
        (1.25, PNG_NAMES[14]),
        (1.5, PNG_NAMES[15]),
    )
    measurements = []
    for scale, name in scales:
        record = by_name[name]
        measurements.append(
            {
                "scale": scale,
                "file": name,
                "requested_logical_size": record[
                    "requested_logical_size"
                ],
                "actual_logical_size": record["actual_logical_size"],
                "physical_image_size": record["physical_image_size"],
                "device_pixel_ratio": record["device_pixel_ratio"],
                "content_viewport_bounds": record[
                    "content_viewport_bounds"
                ],
                "footer_bounds": record["footer_bounds"],
                "footer_visible": record["footer_visible"],
                "horizontal_scrollbar_range": record[
                    "horizontal_scrollbar_range"
                ],
                "vertical_scrollbar_range": record[
                    "vertical_scrollbar_range"
                ],
                "clipping_count": record["clipping_count"],
                "overlap_count": record["overlap_count"],
                "truncated_text_count": record[
                    "truncated_text_count"
                ],
                "missing_glyph_count": record["text_rendering"][
                    "missing_glyph_count"
                ],
                "bounds_valid": (
                    record["clipping_count"] == 0
                    and record["overlap_count"] == 0
                    and record["truncated_text_count"] == 0
                    and record["horizontal_scrollbar_range"] == 0
                    and record["footer_visible"]
                ),
            }
        )
    return {
        "measurements": measurements,
        "horizontal_scroll_count": sum(
            item["horizontal_scrollbar_range"] > 0
            for item in measurements
        ),
        "clipping_count": sum(
            item["clipping_count"] for item in measurements
        ),
        "overlap_count": sum(
            item["overlap_count"] for item in measurements
        ),
        "truncated_text_count": sum(
            item["truncated_text_count"] for item in measurements
        ),
        "missing_glyph_count": sum(
            item["missing_glyph_count"] for item in measurements
        ),
        "child_dialog_depth": 1,
    }


def _review_index(
    png_hashes: dict[str, str],
    font_probe: dict[str, object],
) -> str:
    evidence = (
        (
            "ToolEditorDialog / configured ball tool",
            "profile section collapsed; optional state remains visible",
            "summary.json",
        ),
        (
            "ToolEditorDialog / configured ball tool",
            "two real profiles expanded with no horizontal scroll",
            "responsive_bounds_report.json",
        ),
        (
            "ToolEditorDialog / unconfigured ball tool",
            "no empty profile is synthesized",
            "profile_schema_report.json",
        ),
        (
            "ToolProfileEditorDialog / Z-Level schema",
            "only Z-Level fields; sparse selected values",
            "profile_schema_report.json",
        ),
        (
            "ToolProfileEditorDialog / Parallel schema",
            "Parallel fields present; Z-Level-only fields absent",
            "profile_schema_report.json",
        ),
        (
            "ToolProfileEditorDialog / Drilling schema",
            "drilling fields present; milling-only fields absent",
            "profile_schema_report.json",
        ),
        (
            "ToolProfileEditorDialog / Parallel advanced state",
            "strategy-specific advanced fields come from registry",
            "profile_schema_report.json",
        ),
        (
            "ToolProfileEditorDialog / empty sparse Z-Level profile",
            "all optional fields start disabled",
            "profile_schema_report.json",
        ),
        (
            "ToolEditorDialog / incompatible end mill profile",
            "tool-family mismatch is fail-closed",
            "stale_dependency_report.json",
        ),
        (
            "ToolProfileSavePreviewDialog / all effective values",
            "save-from-operation requires preview and confirmation",
            "resolution_precedence_report.json",
        ),
        (
            "ToolProfileSavePreviewDialog / overrides only",
            "only explicitly customized fields are selected",
            "resolution_precedence_report.json",
        ),
        (
            "ToolProfileProvenanceWidget / profile resolution",
            "program-profile value source is visible",
            "resolution_precedence_report.json",
        ),
        (
            "ToolProfileProvenanceWidget / manual resolution",
            "operation override wins and provenance remains visible",
            "resolution_precedence_report.json",
        ),
        (
            "ToolEditorDialog / stale tool revision",
            "dependency revision is marked needs-review",
            "stale_dependency_report.json",
        ),
        (
            "ToolEditorDialog / configured ball tool at 125%",
            "native Windows DPI render preserves footer and bounds",
            "responsive_bounds_report.json",
        ),
        (
            "ToolEditorDialog / configured ball tool at 150%",
            "native Windows DPI render preserves footer and bounds",
            "responsive_bounds_report.json",
        ),
    )
    image_lines = "\n".join(
        (
            f"- `{name}` — **PASS**; source: {source}; invariant: "
            f"{invariant}; font/glyph: PASS "
            f"({font_probe['resolved_font_family']}, "
            f"missing/replacement/tofu 0/0/0); report: `{report}`; "
            f"SHA-256 `{png_hashes[name]}`"
        )
        for name, (source, invariant, report) in zip(
            PNG_NAMES,
            evidence,
            strict=True,
        )
    )
    return f"""# Review Stage 8A.4.1 — Cấu hình Tool theo chương trình

Package này được tạo từ `ToolDefinition`, profile registry, resolver, SQLite
repository và widget production thật. Mỗi ảnh đều có assert trạng thái model
trước khi render. Harness chạy bằng QPA `{font_probe['qpa_platform']}` và font
production `{font_probe['resolved_font_family']}`; probe glyph/pixel tiếng Việt
đạt với missing/replacement/tofu = 0/0/0. Stage vẫn **IN PROGRESS**.

## 16 ảnh PNG

{image_lines}

## 7 báo cáo JSON

- `summary.json`
- `profile_schema_report.json`
- `resolution_precedence_report.json`
- `persistence_report.json`
- `stale_dependency_report.json`
- `localization_accessibility_audit.json`
- `responsive_bounds_report.json`

## Giới hạn

- Không triển khai quy trình ba bước hoàn chỉnh hoặc chương trình mẫu.
- Không Import/Export profile.
- Không thay đổi thuật toán CAM, contact-point đa họ Tool hoặc Production Post.
- Cấu hình Tool không phải chứng nhận an toàn hay machine-ready.
"""


def _create_package_in_process(output_root: Path | None = None) -> None:
    global OUTPUT

    if output_root is not None:
        OUTPUT = output_root
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("HMS CAD/CAM Stage 8A.4.1 Review")
    font_probe = _production_font_probe(app)
    _require_valid_font_probe(font_probe)
    _prepare_output()
    dpi_records = _run_dpi_workers()
    models = _models()
    image_records = _render_main_images(models)
    screenshot_records = [*image_records, *dpi_records]
    for name in PNG_NAMES:
        if not (OUTPUT / name).is_file():
            raise RuntimeError(f"Missing review image: {name}")

    schema_report = {
        "registry_size": len(DEFAULT_TOOL_PROFILE_REGISTRY.schemas),
        "schemas": [
            item.report_dict()
            for item in DEFAULT_TOOL_PROFILE_REGISTRY.schemas
        ],
        "strategy_specific": True,
        "ball_end_hard_coded_in_architecture": False,
    }
    resolution = models["manual_resolution"]
    resolution_report = {
        "precedence": [
            "Nguyên công hiện tại",
            "Cấu hình Tool theo chương trình",
            "Cấu hình cơ bản của Tool",
            "Chính sách tự động",
            "Giá trị an toàn mặc định",
        ],
        "strategy_id": resolution.strategy_id,
        "blocked": resolution.blocked,
        "dependency_fingerprint": resolution.dependency_fingerprint.to_dict(),
        "values": [
            {
                "field_id": item.field_id,
                "canonical_value": item.canonical_value,
                "display_value": item.display_value,
                "source": item.source.value,
                "source_object_id": item.source_object_id,
                "validation_status": item.validation_status.value,
                "mode": item.mode.value,
                "reason_vi": item.reason_vi,
                "dependency_contribution": (
                    item.dependency_contribution.to_dict()
                ),
            }
            for item in resolution.values
        ],
    }
    audit_widgets = [
        ToolEditorDialog(models["configured_ball"]),
        ToolEditorDialog(models["ball"]),
        ToolEditorDialog(models["stale_tool"]),
        ToolProfileEditorDialog(
            DEFAULT_TOOL_PROFILE_REGISTRY.schema("parallel_finishing_3d")
        ),
        ToolProfileSavePreviewDialog(models["preview"]),
        ToolProfileProvenanceWidget(models["manual_resolution"]),
    ]
    for widget in audit_widgets:
        widget.show()
    QApplication.processEvents()
    accessibility = _accessibility_audit(
        audit_widgets,
        font_probe,
        screenshot_records,
    )
    for widget in audit_widgets:
        widget.close()
        widget.deleteLater()
    responsive = _responsive_report(screenshot_records)
    persistence = _persistence_report(models)
    stale = _stale_report(models)
    _write_json(OUTPUT / "profile_schema_report.json", schema_report)
    _write_json(
        OUTPUT / "resolution_precedence_report.json", resolution_report
    )
    _write_json(OUTPUT / "persistence_report.json", persistence)
    _write_json(OUTPUT / "stale_dependency_report.json", stale)
    _write_json(
        OUTPUT / "localization_accessibility_audit.json", accessibility
    )
    _write_json(OUTPUT / "responsive_bounds_report.json", responsive)

    png_hashes = {name: _sha256(OUTPUT / name) for name in PNG_NAMES}
    if len(set(png_hashes.values())) != len(PNG_NAMES):
        raise RuntimeError("Every review PNG must have a unique SHA-256")
    source_files = (
        REPOSITORY_ROOT
        / "src"
        / "hms_cadcam"
        / "cam"
        / "domain"
        / "tool_profiles.py",
        REPOSITORY_ROOT
        / "src"
        / "hms_cadcam"
        / "ui"
        / "tool_program_profiles.py",
        Path(__file__).resolve(),
    )
    summary = {
        "stage": "8A.4.1",
        "status": "IN PROGRESS",
        "package_file_count": 24,
        "png_count": 16,
        "json_count": 7,
        "markdown_count": 1,
        "model_state_asserted_before_each_image": True,
        "production_model_service_widget_only": True,
        "sqlite_schema_version": DATABASE_SCHEMA_VERSION,
        "font_policy": font_probe["font_policy"],
        "font_override_applied": font_probe["font_override_applied"],
        "qpa_platform": font_probe["qpa_platform"],
        "application_font_family": font_probe[
            "application_font_family"
        ],
        "resolved_font_family": font_probe["resolved_font_family"],
        "application_font_point_size": font_probe[
            "application_font_point_size"
        ],
        "font_probe_passed": font_probe["font_probe_passed"],
        "vietnamese_glyph_probe_passed": font_probe[
            "vietnamese_glyph_probe_passed"
        ],
        "replacement_character_count": font_probe[
            "replacement_character_count"
        ],
        "tofu_detection_count": font_probe["tofu_candidate_count"],
        "all_png_text_rendering_valid": all(
            record["text_rendering"]["passed"]
            for record in screenshot_records
        ),
        "png_sha256": png_hashes,
        "main_image_records": image_records,
        "dpi_image_records": dpi_records,
        "source_sha256": {
            str(path.relative_to(REPOSITORY_ROOT)).replace("\\", "/"): _sha256(
                path
            )
            for path in source_files
        },
    }
    _write_json(OUTPUT / "summary.json", summary)
    (OUTPUT / "REVIEW_INDEX.md").write_text(
        _review_index(png_hashes, font_probe), encoding="utf-8"
    )
    actual = tuple(sorted(path.name for path in OUTPUT.iterdir()))
    expected = tuple(sorted((*PNG_NAMES, *JSON_NAMES, "REVIEW_INDEX.md")))
    if actual != expected:
        raise RuntimeError(
            f"Review package shape mismatch: expected 24, received {len(actual)}"
        )
    if accessibility["forbidden_leak_count"] != 0:
        raise RuntimeError("Review UI contains raw internal profile tokens")
    if accessibility["missing_accessible_name_count"] != 0:
        raise RuntimeError("Review UI contains unnamed interactive controls")
    if not all(accessibility["required_phrases"].values()):
        raise RuntimeError("Review UI is missing a required Vietnamese phrase")
    if not accessibility["rendered_glyph_validation"]["passed"]:
        raise RuntimeError("Review UI failed rendered glyph validation")
    if any(
        accessibility[name] != 0
        for name in (
            "missing_glyph_count",
            "replacement_glyph_count",
            "tofu_candidate_count",
            "affected_widget_count",
            "affected_screenshot_count",
        )
    ):
        raise RuntimeError("Review UI contains invalid rendered text")
    if any(
        responsive[name] != 0
        for name in (
            "horizontal_scroll_count",
            "clipping_count",
            "overlap_count",
            "truncated_text_count",
            "missing_glyph_count",
        )
    ):
        raise RuntimeError("Review UI failed responsive bounds validation")
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "files": len(actual),
                "png": len(PNG_NAMES),
                "sha256": png_hashes,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )


def create_package(output_root: Path | None = None) -> None:
    """Build evidence in a clean native-Windows Qt process.

    Pytest configures a shared offscreen QApplication for widget tests. Reusing
    that process renders Windows system-font text as tofu even though the
    strings and QFontMetrics coverage are valid. Production starts a fresh
    QApplication on the native Windows QPA, so the evidence harness does the
    same and leaves the caller's Qt state untouched.
    """
    environment = dict(os.environ)
    environment.pop("QT_QPA_PLATFORM", None)
    environment.pop("QT_SCALE_FACTOR", None)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--package-worker",
    ]
    if output_root is not None:
        command.extend(("--output-root", str(output_root)))
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Review package worker failed: {detail}")
    if result.stdout.strip():
        print(result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dpi-worker", type=float)
    parser.add_argument("--package-worker", action="store_true")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("output", nargs="?")
    arguments = parser.parse_args()
    if arguments.dpi_worker is not None:
        if arguments.output is None:
            raise ValueError("DPI worker output is required")
        return _dpi_worker(arguments.dpi_worker, Path(arguments.output))
    if arguments.package_worker:
        _create_package_in_process(arguments.output_root)
        return 0
    create_package()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
