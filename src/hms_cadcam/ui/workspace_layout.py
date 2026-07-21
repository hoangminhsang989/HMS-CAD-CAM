"""Versioned user-only layout persistence for the HMS workspace shell."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QByteArray, QRect, QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDockWidget, QMainWindow

from hms_cadcam.ui.ui_tokens import (
    DIAGNOSTICS_DEFAULT_HEIGHT,
    FUNCTION_EDITOR_DEFAULT_WIDTH,
    OPERATION_MANAGER_DEFAULT_WIDTH,
)


WORKSPACE_LAYOUT_VERSION = 1
_SETTINGS_GROUP = "workspace_shell_9a2"


@dataclass(frozen=True, slots=True)
class WorkspaceLayoutSnapshot:
    """Small scalar part of a saved layout; Qt owns the dock-state payload."""

    active_workspace: str
    operation_manager_width: int
    function_editor_width: int
    diagnostics_height: int


class WorkspaceLayoutStore:
    """Persist UI state outside projects without affecting project dirty state."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings("HMS", "HMS CAD-CAM")

    @classmethod
    def for_config_directory(cls, config_dir: Path) -> "WorkspaceLayoutStore":
        """Create an INI-backed store beside other user-runtime settings."""
        config_dir.mkdir(parents=True, exist_ok=True)
        settings = QSettings(
            str(config_dir / "workspace_ui.ini"), QSettings.Format.IniFormat
        )
        return cls(settings)

    @property
    def settings(self) -> QSettings:
        """Return the backing settings object for focused UI tests."""
        return self._settings

    def save(
        self,
        window: QMainWindow,
        *,
        active_workspace: str,
        operation_manager: QDockWidget,
        function_editor: QDockWidget,
        diagnostics: QDockWidget,
    ) -> None:
        """Save geometry, dock state, visibility and active workspace."""
        self._settings.beginGroup(_SETTINGS_GROUP)
        try:
            self._settings.setValue("layout_version", WORKSPACE_LAYOUT_VERSION)
            self._settings.setValue("geometry", window.saveGeometry())
            self._settings.setValue(
                "dock_state", window.saveState(WORKSPACE_LAYOUT_VERSION)
            )
            self._settings.setValue("active_workspace", active_workspace)
            self._settings.setValue(
                "operation_manager_width", operation_manager.width()
            )
            self._settings.setValue("function_editor_width", function_editor.width())
            self._settings.setValue("diagnostics_height", diagnostics.height())
        finally:
            self._settings.endGroup()
        self._settings.sync()

    def restore(self, window: QMainWindow) -> WorkspaceLayoutSnapshot | None:
        """Restore compatible Qt state and return the saved scalar metrics."""
        self._settings.beginGroup(_SETTINGS_GROUP)
        try:
            version = int(self._settings.value("layout_version", 0))
            if version != WORKSPACE_LAYOUT_VERSION:
                self._settings.remove("")
                return None
            geometry = _byte_array(self._settings.value("geometry"))
            state = _byte_array(self._settings.value("dock_state"))
            if not geometry.isEmpty():
                window.restoreGeometry(geometry)
            if not state.isEmpty():
                window.restoreState(state, WORKSPACE_LAYOUT_VERSION)
            return WorkspaceLayoutSnapshot(
                active_workspace=str(
                    self._settings.value("active_workspace", "home")
                ),
                operation_manager_width=_bounded_int(
                    self._settings.value(
                        "operation_manager_width", OPERATION_MANAGER_DEFAULT_WIDTH
                    ),
                    180,
                    800,
                    OPERATION_MANAGER_DEFAULT_WIDTH,
                ),
                function_editor_width=_bounded_int(
                    self._settings.value(
                        "function_editor_width", FUNCTION_EDITOR_DEFAULT_WIDTH
                    ),
                    240,
                    900,
                    FUNCTION_EDITOR_DEFAULT_WIDTH,
                ),
                diagnostics_height=_bounded_int(
                    self._settings.value(
                        "diagnostics_height", DIAGNOSTICS_DEFAULT_HEIGHT
                    ),
                    60,
                    500,
                    DIAGNOSTICS_DEFAULT_HEIGHT,
                ),
            )
        finally:
            self._settings.endGroup()

    def reset(self) -> None:
        """Remove only Stage 9A.2 layout keys, leaving every other setting intact."""
        self._settings.beginGroup(_SETTINGS_GROUP)
        try:
            self._settings.remove("")
        finally:
            self._settings.endGroup()
        self._settings.sync()


def clamp_window_to_available_screens(window: QMainWindow) -> QRect:
    """Clamp restored geometry to a visible screen and return the applied rect."""
    screens = tuple(screen.availableGeometry() for screen in QGuiApplication.screens())
    target = clamp_geometry(window.geometry(), screens)
    if target != window.geometry():
        window.setGeometry(target)
    return target


def clamp_geometry(geometry: QRect, available_screens: Iterable[QRect]) -> QRect:
    """Return a usable logical-pixel geometry for the available screens."""
    screens = tuple(rect for rect in available_screens if rect.isValid())
    if not screens:
        return QRect(geometry)
    visible = next(
        (
            screen
            for screen in screens
            if screen.intersected(geometry).width() >= 80
            and screen.intersected(geometry).height() >= 60
        ),
        None,
    )
    if visible is None:
        visible = screens[0]
    width = min(max(geometry.width(), 960), visible.width())
    height = min(max(geometry.height(), 640), visible.height())
    maximum_x = visible.right() - width + 1
    maximum_y = visible.bottom() - height + 1
    x = min(max(geometry.x(), visible.left()), maximum_x)
    y = min(max(geometry.y(), visible.top()), maximum_y)
    return QRect(x, y, width, height)


def _byte_array(value: object) -> QByteArray:
    if isinstance(value, QByteArray):
        return value
    if isinstance(value, bytes):
        return QByteArray(value)
    return QByteArray()


def _bounded_int(value: object, minimum: int, maximum: int, default: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(candidate, minimum), maximum)
