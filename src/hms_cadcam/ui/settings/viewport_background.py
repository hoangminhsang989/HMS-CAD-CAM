"""Persistent application preference for the shared 3D viewport background."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QObject, QSettings, Signal

from hms_cadcam.viewer.models import DEFAULT_VIEWPORT_BACKGROUND, ObjectColor


VIEWPORT_BACKGROUND_SETTINGS_KEY = "viewer/background_color"
VIEWPORT_BACKGROUND_PRESETS: tuple[ObjectColor, ...] = (
    DEFAULT_VIEWPORT_BACKGROUND,
    ObjectColor(0.06, 0.09, 0.13),
    ObjectColor(0.22, 0.25, 0.29),
    ObjectColor(0.72, 0.75, 0.78),
)


class ViewportBackgroundSettingsBackend(Protocol):
    """Minimal QSettings-compatible persistence boundary."""

    def value(self, key: str, default_value: object = None) -> object: ...

    def setValue(self, key: str, value: object) -> None: ...  # noqa: N802

    def sync(self) -> None: ...


class ViewportBackgroundManager(QObject):
    """Own preview and persisted global 3D background state."""

    preview_changed = Signal(object)
    color_changed = Signal(object)

    def __init__(
        self,
        settings: ViewportBackgroundSettingsBackend | None = None,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings("HMS", "HMS CAD-CAM")
        self._persisted_color = self._load_color()
        self._current_color = self._persisted_color

    @property
    def current_color(self) -> ObjectColor:
        """Return current runtime color, including an uncommitted preview."""

        return self._current_color

    @property
    def persisted_color(self) -> ObjectColor:
        """Return last successfully persisted color."""

        return self._persisted_color

    def set_preview_color(self, color: ObjectColor) -> None:
        """Apply a lightweight runtime preview without writing settings."""

        if not isinstance(color, ObjectColor):
            raise TypeError("Viewport background must be ObjectColor")
        if color == self._current_color:
            return
        self._current_color = color
        self.preview_changed.emit(color)

    def apply_color(self) -> bool:
        """Persist the preview and publish one committed preference change."""

        try:
            self._settings.setValue(
                VIEWPORT_BACKGROUND_SETTINGS_KEY,
                self._current_color.to_hex(),
            )
            self._settings.sync()
        except (OSError, RuntimeError):
            return False
        self._persisted_color = self._current_color
        self.color_changed.emit(self._persisted_color)
        return True

    def cancel_preview(self) -> None:
        """Restore runtime state to the persisted color."""

        self.set_preview_color(self._persisted_color)

    def reset_default(self) -> None:
        """Preview the deterministic HMS default pending Apply or OK."""

        self.set_preview_color(DEFAULT_VIEWPORT_BACKGROUND)

    def _load_color(self) -> ObjectColor:
        raw = self._settings.value(
            VIEWPORT_BACKGROUND_SETTINGS_KEY,
            None,
        )
        if raw is None:
            return DEFAULT_VIEWPORT_BACKGROUND
        try:
            return _color_from_hex(raw)
        except (TypeError, ValueError):
            return DEFAULT_VIEWPORT_BACKGROUND


def _color_from_hex(value: object) -> ObjectColor:
    if not isinstance(value, str):
        raise TypeError("Viewport background setting must be a string")
    normalized = value.strip()
    if len(normalized) != 7 or not normalized.startswith("#"):
        raise ValueError("Viewport background must use #RRGGBB")
    try:
        channels = tuple(
            int(normalized[index : index + 2], 16) / 255.0
            for index in (1, 3, 5)
        )
    except ValueError as error:
        raise ValueError("Viewport background contains invalid RGB") from error
    return ObjectColor(*channels)


__all__ = [
    "DEFAULT_VIEWPORT_BACKGROUND",
    "VIEWPORT_BACKGROUND_PRESETS",
    "VIEWPORT_BACKGROUND_SETTINGS_KEY",
    "ViewportBackgroundManager",
]
