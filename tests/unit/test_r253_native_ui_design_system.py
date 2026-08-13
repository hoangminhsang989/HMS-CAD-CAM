"""R253 native design-system and certified-boundary contracts."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from hms_cadcam.ui.design_system import (
    NATIVE_CAD_STYLE,
    PALETTE,
    TOOLPATH_SEMANTIC_COLORS,
)
from hms_cadcam.ui.ribbon import RibbonMetrics
from hms_cadcam.ui.settings.ui_scale import UiScaleManager
from hms_cadcam.ui.settings.viewport_background import DEFAULT_VIEWPORT_BACKGROUND


def test_owner_theme_is_light_workspace_navy_chrome_compact_and_state_complete() -> None:
    assert PALETTE.window != "#000000"
    assert PALETTE.chrome != "#000000"
    assert PALETTE.window.lower() == "#f8fafc"
    assert PALETTE.chrome.lower() == "#0b2030"
    assert PALETTE.panel.lower() == "#f7f9fb"
    assert PALETTE.editor.lower() == "#ffffff"
    assert PALETTE.text.lower() == "#24313d"
    assert "QMainWindow#HmsMainWindow" in NATIVE_CAD_STYLE
    assert "QToolBar#WorkspaceBar" in NATIVE_CAD_STYLE
    assert "QToolBar#RibbonContainer" in NATIVE_CAD_STYLE
    assert "QDockWidget::title" in NATIVE_CAD_STYLE
    assert "QDialog#GeneralSettingsDialog" in NATIVE_CAD_STYLE
    assert "QDialog#ToolLibraryDialog" in NATIVE_CAD_STYLE
    assert "QWidget#MachiningSimulationRoot" in NATIVE_CAD_STYLE
    for state in (":hover", ":pressed", ":checked", ":disabled", ":focus"):
        assert state in NATIVE_CAD_STYLE


def test_r253_theme_does_not_embed_web_runtime_or_viewport_background() -> None:
    lowered = NATIVE_CAD_STYLE.lower()
    assert "qwebengine" not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "cadviewportwidget" not in lowered
    assert DEFAULT_VIEWPORT_BACKGROUND.to_hex().lower() not in lowered


def test_r253_toolpath_semantic_colors_are_not_theme_accents() -> None:
    assert dict(TOOLPATH_SEMANTIC_COLORS) == {
        "rapid": "#ff3636",
        "cutting": "#ffd22e",
        "link": "#ffffff",
        "retract": "#32d06b",
    }
    assert TOOLPATH_SEMANTIC_COLORS["cutting"] != PALETTE.gold
    assert TOOLPATH_SEMANTIC_COLORS["rapid"] != PALETTE.danger


def test_r253_ribbon_metrics_are_compact_at_baseline(
    tmp_path: Path,
    qapp,
) -> None:
    del qapp
    manager = UiScaleManager(
        QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    )
    metrics = RibbonMetrics.from_scale_manager(manager)
    assert metrics.ribbon_height == 92
    assert metrics.icon_size.width() == 22
    assert metrics.action_button_minimum_width == 44


def test_r253_reference_remains_input_only() -> None:
    source = Path(__file__).parents[2] / "src" / "hms_cadcam" / "ui"
    production_text = "\n".join(
        path.read_text(encoding="utf-8") for path in source.rglob("*.py")
    ).lower()
    assert "hms_cadcam_ui_review_worknc_v11.html" not in production_text
    assert "qwebengineview" not in production_text
