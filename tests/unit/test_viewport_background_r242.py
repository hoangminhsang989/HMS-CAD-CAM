"""R242 global 3D viewport background preference and settings UI tests."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from hms_cadcam.ui.settings.general_settings import GeneralSettingsDialog
from hms_cadcam.ui.settings.ui_scale import UiScaleManager
from hms_cadcam.ui.settings.viewport_background import (
    DEFAULT_VIEWPORT_BACKGROUND,
    VIEWPORT_BACKGROUND_SETTINGS_KEY,
    ViewportBackgroundManager,
)
from hms_cadcam.viewer.models import ObjectColor


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _settings(path: Path) -> QSettings:
    return QSettings(str(path), QSettings.Format.IniFormat)


def test_background_preference_round_trip_and_corrupt_fallback(tmp_path: Path) -> None:
    settings_path = tmp_path / "viewport.ini"
    first = ViewportBackgroundManager(_settings(settings_path))
    selected = ObjectColor(0.2, 0.3, 0.4)
    first.set_preview_color(selected)
    assert first.apply_color()

    reopened = ViewportBackgroundManager(_settings(settings_path))
    assert reopened.current_color.to_hex() == selected.to_hex()

    corrupt = _settings(settings_path)
    corrupt.setValue(VIEWPORT_BACKGROUND_SETTINGS_KEY, "not-a-color")
    corrupt.sync()
    assert ViewportBackgroundManager(corrupt).current_color == DEFAULT_VIEWPORT_BACKGROUND


def test_background_preview_cancel_apply_and_reset_default(tmp_path: Path) -> None:
    manager = ViewportBackgroundManager(_settings(tmp_path / "preview.ini"))
    changes: list[ObjectColor] = []
    manager.preview_changed.connect(changes.append)
    selected = ObjectColor(0.25, 0.35, 0.45)

    manager.set_preview_color(selected)
    manager.cancel_preview()
    manager.set_preview_color(selected)
    assert manager.apply_color()
    manager.reset_default()

    assert changes == [selected, DEFAULT_VIEWPORT_BACKGROUND, selected, DEFAULT_VIEWPORT_BACKGROUND]
    assert manager.persisted_color.to_hex() == selected.to_hex()
    assert manager.current_color == DEFAULT_VIEWPORT_BACKGROUND


def test_general_settings_viewport_page_immediate_preview_apply_ok_cancel(
    tmp_path: Path,
) -> None:
    _application()
    settings = _settings(tmp_path / "dialog.ini")
    background = ViewportBackgroundManager(settings)
    dialog = GeneralSettingsDialog(
        UiScaleManager(settings),
        viewport_background_manager=background,
    )
    row = next(
        index
        for index, item in enumerate(dialog._category_items)
        if item.data(Qt.ItemDataRole.UserRole) == "CAD/Viewer"
    )
    dialog.category_list.setCurrentRow(row)
    selected = ObjectColor(0.17, 0.27, 0.37)

    dialog._set_background_preview(selected)

    assert background.current_color == selected
    assert background.persisted_color == DEFAULT_VIEWPORT_BACKGROUND
    assert dialog.background_preview.toolTip() == selected.to_hex()
    assert dialog.apply_button.isEnabled()
    assert dialog._apply()
    assert background.persisted_color.to_hex() == selected.to_hex()

    dialog._reset_default()
    assert background.current_color == DEFAULT_VIEWPORT_BACKGROUND
    dialog._cancel()
    assert background.current_color.to_hex() == selected.to_hex()
