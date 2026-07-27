"""General Settings shell for the C3.1 UI preferences surface."""

from hms_cadcam.ui.settings.general_settings import (
    GeneralSettingsDialog,
    SettingsDialogGeometry,
    settings_dialog_geometry,
)
from hms_cadcam.ui.settings.ui_scale import (
    APPLICATION_FONT_MODE_POINT,
    APPLICATION_FONT_MODE_PIXEL,
    APPLICATION_FONT_MODE_RESOLVED,
    DEFAULT_PERCENT,
    MAX_PERCENT,
    MIN_PERCENT,
    UI_SCALE_PRESETS,
    UI_SCALE_SETTINGS_KEY,
    UiMetrics,
    UiScaleManager,
    validate_percent,
)

__all__ = [
    "APPLICATION_FONT_MODE_POINT",
    "APPLICATION_FONT_MODE_PIXEL",
    "APPLICATION_FONT_MODE_RESOLVED",
    "DEFAULT_PERCENT",
    "GeneralSettingsDialog",
    "SettingsDialogGeometry",
    "settings_dialog_geometry",
    "MAX_PERCENT",
    "MIN_PERCENT",
    "UI_SCALE_PRESETS",
    "UI_SCALE_SETTINGS_KEY",
    "UiMetrics",
    "UiScaleManager",
    "validate_percent",
]
