"""User UI scale domain and runtime metric helpers for C3.1.

The manager owns only user-interface presentation state. It never touches
projects, SQLite, CAM payloads, or Windows process DPI configuration.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from weakref import WeakKeyDictionary

from PySide6.QtCore import QMargins, QObject, QSettings, QSize, Signal
from PySide6.QtGui import QFont, QFontInfo
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QListWidget,
    QSpinBox,
    QTableView,
    QToolBar,
    QTreeWidget,
    QWidget,
)

MIN_PERCENT = 50
MAX_PERCENT = 200
DEFAULT_PERCENT = 100
UI_SCALE_SETTINGS_KEY = "ui/scale_percent"
UI_SCALE_PRESETS = (50, 75, 90, 100, 110, 125, 150, 175, 200)
APPLICATION_FONT_MODE_POINT = "point"
APPLICATION_FONT_MODE_PIXEL = "pixel"
APPLICATION_FONT_MODE_RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class UiMetrics:
    """Logical presentation metrics derived from one baseline scale."""

    percent: int
    scale_factor: float
    control_height: int
    button_height: int
    row_height: int
    header_height: int
    content_margin: int
    spacing: int
    icon_size: QSize


@dataclass(slots=True)
class _ApplicationFontState:
    baseline: QFont
    last_applied: QFont | None


_APPLICATION_FONT_STATES: WeakKeyDictionary[QApplication, _ApplicationFontState] = (
    WeakKeyDictionary()
)


def validate_percent(value: object) -> int:
    """Return a safe integer percentage, clamped to the public contract."""

    if isinstance(value, bool):
        return DEFAULT_PERCENT
    try:
        if isinstance(value, str) and not value.strip().lstrip("+-").isdigit():
            return DEFAULT_PERCENT
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_PERCENT
    return min(MAX_PERCENT, max(MIN_PERCENT, normalized))


class UiScaleManager(QObject):
    """Injectable user UI-scale state, persistence, and baseline metrics."""

    scale_changed = Signal(int)
    preview_changed = Signal(int)

    def __init__(
        self,
        settings: QSettings | None = None,
        *,
        application: QApplication | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings("HMS", "HMS CAD-CAM")
        self._application = application or QApplication.instance()
        self._persisted_percent = self._load_persisted_percent()
        self._current_percent = self._persisted_percent
        app_font = self._application.font() if self._application is not None else QFont()
        shared_state = (
            _APPLICATION_FONT_STATES.get(self._application)
            if self._application is not None
            else None
        )
        reuse_shared_baseline = (
            shared_state is not None
            and shared_state.last_applied is not None
            and app_font == shared_state.last_applied
        )
        baseline_font = (
            shared_state.baseline if reuse_shared_baseline else app_font
        )
        self._application_font_mode = _font_mode(baseline_font)
        self._application_font_baseline = QFont(baseline_font)
        self._resolved_application_point_size = _resolved_point_size(baseline_font)
        self._last_applied_application_font = (
            QFont(shared_state.last_applied)
            if reuse_shared_baseline and shared_state is not None
            else None
        )
        self._applying_application_font = False
        self._widget_baseline_fonts: WeakKeyDictionary[QWidget, QFont] = (
            WeakKeyDictionary()
        )
        self._widget_baseline_heights: WeakKeyDictionary[QWidget, int] = (
            WeakKeyDictionary()
        )
        self._widget_baseline_icons: WeakKeyDictionary[QWidget, QSize] = (
            WeakKeyDictionary()
        )

    @property
    def current_percent(self) -> int:
        """Return the runtime value, including an uncommitted preview."""

        return self._current_percent

    @property
    def persisted_percent(self) -> int:
        """Return the last successfully persisted value."""

        return self._persisted_percent

    @property
    def scale_factor(self) -> float:
        """Return the logical presentation multiplier for the current value."""

        return self._current_percent / 100.0

    @property
    def settings(self) -> QSettings:
        """Expose the injected settings backend for focused tests."""

        return self._settings

    @property
    def application_font_mode(self) -> str:
        """Return the active QApplication baseline mode: point, pixel, or resolved."""

        return self._application_font_mode

    def application_font_baseline(self) -> QFont:
        """Return a copy of the unscaled QApplication baseline font."""

        return QFont(self._application_font_baseline)

    def capture_application_font_baseline(self, font: QFont | None = None) -> str:
        """Capture an external unscaled application font without applying it."""

        candidate = QFont(font) if font is not None else self._current_application_font()
        self._set_application_font_baseline(candidate)
        return self._application_font_mode

    def notify_external_application_font_changed(
        self,
        font: QFont | None = None,
        *,
        already_scaled: bool = False,
    ) -> bool:
        """Rebase on an external font change while preserving the active scale.

        ``already_scaled`` is used by locale/font helpers that update only the
        family of the manager's currently scaled font. It removes the current
        scale before capturing that font as the new logical baseline.
        """

        if self._application is None or self._applying_application_font:
            return False
        candidate = QFont(font) if font is not None else self._current_application_font()
        if (
            not already_scaled
            and self._last_applied_application_font is not None
            and candidate == self._last_applied_application_font
        ):
            return False
        if already_scaled:
            candidate = _unscale_font(candidate, self.scale_factor)
        self._set_application_font_baseline(candidate)
        self._apply_application_font()
        return True

    def rebase_application_font(
        self,
        font: QFont | None = None,
        *,
        already_scaled: bool = False,
    ) -> bool:
        """Alias for :meth:`notify_external_application_font_changed`."""

        return self.notify_external_application_font_changed(
            font,
            already_scaled=already_scaled,
        )

    def metrics(self) -> UiMetrics:
        """Build baseline-derived metrics for shell and dialog consumers."""

        return UiMetrics(
            percent=self._current_percent,
            scale_factor=self.scale_factor,
            control_height=self.scaled_int(26, minimum=20),
            button_height=self.scaled_int(28, minimum=22),
            row_height=self.scaled_int(26, minimum=18),
            header_height=self.scaled_int(31, minimum=22),
            content_margin=self.scaled_int(8, minimum=4),
            spacing=self.scaled_int(6, minimum=2),
            icon_size=self.scaled_icon_size(QSize(24, 24), minimum=16),
        )

    def set_preview_percent(self, value: object) -> int:
        """Update runtime preview state without writing QSettings."""

        normalized = validate_percent(value)
        if normalized == self._current_percent:
            return normalized
        self._current_percent = normalized
        self.preview_changed.emit(normalized)
        return normalized

    def apply_percent(self, value: object | None = None) -> bool:
        """Persist one value and emit exactly one logical apply signal."""

        normalized = self._current_percent if value is None else validate_percent(value)
        try:
            self._settings.setValue(UI_SCALE_SETTINGS_KEY, normalized)
            self._settings.sync()
            status = getattr(self._settings, "status", None)
            if callable(status) and status() != QSettings.Status.NoError:
                return False
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        self._current_percent = normalized
        self._persisted_percent = normalized
        self.scale_changed.emit(normalized)
        return True

    def cancel_preview(self) -> int:
        """Restore the last persisted value without changing persistence."""

        if self._current_percent != self._persisted_percent:
            self._current_percent = self._persisted_percent
            self.preview_changed.emit(self._current_percent)
        return self._current_percent

    def reset_default(self) -> int:
        """Preview the baseline 100% value."""

        return self.set_preview_percent(DEFAULT_PERCENT)

    def scaled_int(self, base_value: int, *, minimum: int = 0) -> int:
        """Scale one baseline logical integer without cumulative drift."""

        baseline = int(base_value)
        return max(int(minimum), int(round(baseline * self.scale_factor)))

    def scaled_size(
        self,
        base_size: QSize | tuple[int, int],
        *,
        minimum: QSize | tuple[int, int] = QSize(),
    ) -> QSize:
        """Scale a baseline logical QSize from the active percentage."""

        source = _coerce_size(base_size)
        floor = _coerce_size(minimum)
        return QSize(
            max(floor.width(), self.scaled_int(source.width())),
            max(floor.height(), self.scaled_int(source.height())),
        )

    def scaled_margins(
        self,
        margins: QMargins | tuple[int, int, int, int],
        *,
        minimum: int = 0,
    ) -> QMargins:
        """Scale layout margins from baseline logical values."""

        source = _coerce_margins(margins)
        return QMargins(
            self.scaled_int(source.left(), minimum=minimum),
            self.scaled_int(source.top(), minimum=minimum),
            self.scaled_int(source.right(), minimum=minimum),
            self.scaled_int(source.bottom(), minimum=minimum),
        )

    def scaled_font(self, baseline_font: QFont) -> QFont:
        """Return a scaled copy while preserving point/pixel font semantics."""

        return _scale_font(
            baseline_font,
            self.scale_factor,
            resolved_point_size=_resolved_point_size(baseline_font),
        )

    def scaled_icon_size(
        self,
        baseline_size: QSize | tuple[int, int],
        *,
        minimum: int = 1,
    ) -> QSize:
        """Scale a QIcon/QPixmap logical size while preserving aspect ratio."""

        source = _coerce_size(baseline_size)
        return QSize(
            max(minimum, self.scaled_int(source.width())),
            max(minimum, self.scaled_int(source.height())),
        )

    def apply_runtime(self, root: QWidget | None = None) -> None:
        """Apply the current runtime value to the app and an optional widget tree."""

        if root is None:
            self._apply_application_font()
        else:
            self.apply_widget_tree(root)

    def apply_widget_tree(self, root: QWidget) -> None:
        """Scale fonts and safe control metrics from stored baselines."""

        self._apply_application_font()
        widgets: Iterable[QWidget] = (root, *root.findChildren(QWidget))
        for widget in widgets:
            current = widget.font()
            baseline_font = self._widget_baseline_fonts.get(widget)
            if baseline_font is None:
                baseline_font = _unscale_font(current, self.scale_factor)
                self._widget_baseline_fonts[widget] = baseline_font
            scaled = self.scaled_font(baseline_font)
            if _font_has_explicit_size(scaled):
                # Size is manager-owned; family/style may be changed externally
                # by a theme or locale fallback and must remain current.
                target = QFont(current)
                if scaled.pointSizeF() > 0:
                    target.setPointSizeF(scaled.pointSizeF())
                elif scaled.pixelSize() > 0:
                    target.setPixelSize(scaled.pixelSize())
                widget.setFont(target)
            self._apply_control_metric(widget)

    def scale_stylesheet(self, stylesheet: str) -> str:
        """Scale QSS font/padding/control dimensions from one baseline string."""

        pattern = re.compile(
            r"(?P<property>font-size|padding(?:-[a-z]+)?|min-(?:width|height)|spacing)"
            r"(?P<separator>\s*:\s*)(?P<values>[^;]+)(?P<terminator>;)",
            re.IGNORECASE,
        )

        def replace(match: re.Match[str]) -> str:
            property_name = match.group("property")
            values = match.group("values")
            if property_name.lower() == "font-size":
                values = re.sub(
                    r"(-?\d+(?:\.\d+)?)\s*(pt|px)",
                    lambda item: f"{float(item.group(1)) * self.scale_factor:g}{item.group(2)}",
                    values,
                    flags=re.IGNORECASE,
                )
            else:
                values = re.sub(
                    r"(-?\d+(?:\.\d+)?)\s*px",
                    lambda item: f"{max(0.0, float(item.group(1)) * self.scale_factor):g}px",
                    values,
                    flags=re.IGNORECASE,
                )
            return f"{property_name}{match.group('separator')}{values}{match.group('terminator')}"

        return pattern.sub(replace, str(stylesheet))

    def _load_persisted_percent(self) -> int:
        try:
            raw = self._settings.value(UI_SCALE_SETTINGS_KEY, DEFAULT_PERCENT)
        except (OSError, RuntimeError, TypeError, ValueError):
            return DEFAULT_PERCENT
        if isinstance(raw, (list, tuple, dict, set)):
            return DEFAULT_PERCENT
        if isinstance(raw, str) and not raw.strip().lstrip("+-").isdigit():
            return DEFAULT_PERCENT
        return validate_percent(raw)

    def _apply_application_font(self) -> None:
        if self._application is None:
            return
        scaled = _scale_font(
            self._application_font_baseline,
            self.scale_factor,
            resolved_point_size=self._resolved_application_point_size,
        )
        if not _font_has_explicit_size(scaled):
            self._last_applied_application_font = self._current_application_font()
            self._publish_application_font_state()
            return
        self._applying_application_font = True
        try:
            self._application.setFont(scaled)
            self._last_applied_application_font = self._current_application_font()
            self._publish_application_font_state()
        finally:
            self._applying_application_font = False

    def _current_application_font(self) -> QFont:
        if self._application is None:
            return QFont()
        return QFont(self._application.font())

    def _set_application_font_baseline(self, font: QFont) -> None:
        self._application_font_mode = _font_mode(font)
        self._application_font_baseline = QFont(font)
        self._resolved_application_point_size = _resolved_point_size(font)
        self._publish_application_font_state()

    def _publish_application_font_state(self) -> None:
        if self._application is None:
            return
        _APPLICATION_FONT_STATES[self._application] = _ApplicationFontState(
            baseline=QFont(self._application_font_baseline),
            last_applied=(
                QFont(self._last_applied_application_font)
                if self._last_applied_application_font is not None
                else None
            ),
        )

    def _apply_control_metric(self, widget: QWidget) -> None:
        metrics = self.metrics()
        if isinstance(widget, (QAbstractButton, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox)):
            baseline = self._widget_baseline_heights.get(widget)
            if baseline is None:
                baseline = max(0, widget.minimumHeight()) or 26
                self._widget_baseline_heights[widget] = baseline
            widget.setMinimumHeight(max(0, self.scaled_int(baseline, minimum=20)))
        if isinstance(widget, QToolBar):
            baseline = self._widget_baseline_icons.get(widget)
            if baseline is None:
                baseline = widget.iconSize()
                self._widget_baseline_icons[widget] = QSize(baseline)
            widget.setIconSize(self.scaled_icon_size(baseline, minimum=16))
        if isinstance(widget, QTableView):
            header = widget.horizontalHeader()
            vertical = widget.verticalHeader()
            header.setDefaultSectionSize(metrics.header_height)
            vertical.setDefaultSectionSize(metrics.row_height)
            widget.setIconSize(metrics.icon_size)
        elif isinstance(widget, (QTreeWidget, QListWidget)):
            widget.setIconSize(metrics.icon_size)


def _font_mode(font: QFont) -> str:
    if font.pointSizeF() > 0:
        return APPLICATION_FONT_MODE_POINT
    if font.pixelSize() > 0:
        return APPLICATION_FONT_MODE_PIXEL
    return APPLICATION_FONT_MODE_RESOLVED


def _resolved_point_size(font: QFont) -> float | None:
    """Resolve an unspecified QFont through Qt's active style/font database."""

    if font.pointSizeF() > 0:
        return font.pointSizeF()
    if font.pixelSize() > 0:
        return None
    resolved = QFontInfo(font).pointSizeF()
    return resolved if resolved > 0 else None


def _font_has_explicit_size(font: QFont) -> bool:
    return font.pointSizeF() > 0 or font.pixelSize() > 0


def _scale_font(
    baseline_font: QFont,
    factor: float,
    *,
    resolved_point_size: float | None = None,
) -> QFont:
    font = QFont(baseline_font)
    if baseline_font.pointSizeF() > 0:
        font.setPointSizeF(max(1.0, baseline_font.pointSizeF() * factor))
    elif baseline_font.pixelSize() > 0:
        font.setPixelSize(max(1, int(round(baseline_font.pixelSize() * factor))))
    elif resolved_point_size is not None:
        font.setPointSizeF(max(1.0, resolved_point_size * factor))
    return font


def _unscale_font(font: QFont, factor: float) -> QFont:
    baseline = QFont(font)
    if factor <= 0:
        return baseline
    if font.pointSizeF() > 0:
        baseline.setPointSizeF(max(1.0, font.pointSizeF() / factor))
    elif font.pixelSize() > 0:
        baseline.setPixelSize(max(1, int(round(font.pixelSize() / factor))))
    return baseline


def _coerce_size(value: QSize | tuple[int, int]) -> QSize:
    if isinstance(value, QSize):
        return QSize(value)
    if isinstance(value, tuple) and len(value) == 2:
        return QSize(int(value[0]), int(value[1]))
    raise TypeError("size must be QSize or a width/height tuple")


def _coerce_margins(value: QMargins | tuple[int, int, int, int]) -> QMargins:
    if isinstance(value, QMargins):
        return QMargins(value)
    if isinstance(value, tuple) and len(value) == 4:
        return QMargins(*(int(item) for item in value))
    raise TypeError("margins must be QMargins or a four-value tuple")


__all__ = [
    "APPLICATION_FONT_MODE_POINT",
    "APPLICATION_FONT_MODE_PIXEL",
    "APPLICATION_FONT_MODE_RESOLVED",
    "DEFAULT_PERCENT",
    "MAX_PERCENT",
    "MIN_PERCENT",
    "UI_SCALE_PRESETS",
    "UI_SCALE_SETTINGS_KEY",
    "UiMetrics",
    "UiScaleManager",
    "validate_percent",
]
