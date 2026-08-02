"""Vietnamese-first PySide6 adapter for Lathe 2D XZ simulation results."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.lathe.simulation.models import SafetySeverity, SimulationFrame, SimulationResult
from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.localization import ui_text


class LatheSimulationCanvas(QWidget):
    """Bounded 2D painter; it owns no simulation or project data."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LatheSimulationCanvas")
        self.setMinimumSize(480, 300)
        self._result: SimulationResult | None = None
        self._frame_index = 0
        self.layers = {
            "initial": True, "remaining": True, "tool": True, "holder": True,
            "rapid": True, "cutting": True, "removed": True,
        }

    def set_result(self, result: SimulationResult | None) -> None:
        if result is not None and not isinstance(result, SimulationResult):
            raise TypeError("Lathe simulation canvas result is invalid")
        self._result = result
        self._frame_index = 0
        self.update()

    def set_frame_index(self, index: int) -> None:
        if self._result is None or not self._result.frames:
            self._frame_index = 0
        else:
            self._frame_index = min(max(0, int(index)), len(self._result.frames) - 1)
        self.update()

    def set_layer(self, layer: str, visible: bool) -> None:
        if layer not in self.layers or type(visible) is not bool:
            raise ValueError("Unknown simulation display layer")
        self.layers[layer] = visible
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#20242b"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#9aa4b2"), 1))
        painter.drawLine(24, self.height() // 2, self.width() - 16, self.height() // 2)
        painter.drawText(8, self.height() // 2 - 6, "X=0")
        painter.drawText(self.width() - 30, self.height() // 2 - 6, "Z")
        result = self._result
        if result is None:
            return
        stock = result.final_stock
        z_min, z_max = stock.z_min_mm, stock.z_max_mm
        max_r = max(item.outer_radius_mm for item in result.initial_stock.stations) or 1.0

        def point(z_mm: float, radius_mm: float) -> QPointF:
            x = 36.0 + (z_mm - z_min) / max(1.0e-9, z_max - z_min) * (self.width() - 60.0)
            y = self.height() * 0.5 - radius_mm / max_r * (self.height() * 0.38)
            return QPointF(x, y)

        def outline(stations: tuple[object, ...], color: str, width: int) -> None:
            upper = [point(item.z_mm, item.outer_radius_mm) for item in stations]
            lower = [point(item.z_mm, -item.outer_radius_mm) for item in reversed(stations)]
            painter.setPen(QPen(QColor(color), width))
            painter.drawPolyline(QPolygonF((*upper, *lower, upper[0])))

        if self.layers["initial"]:
            outline(result.initial_stock.stations, "#7f8c8d", 1)
        if self.layers["remaining"]:
            outline(stock.stations, "#55c2ff", 2)
        if result.frames:
            frame = result.frames[self._frame_index]
            if self.layers["rapid"] or self.layers["cutting"]:
                for prior, current in zip(result.frames[: self._frame_index], result.frames[1 : self._frame_index + 1]):
                    rapid = current.motion_kind.value == "RAPID"
                    if (rapid and not self.layers["rapid"]) or (not rapid and not self.layers["cutting"]):
                        continue
                    painter.setPen(QPen(QColor("#e74c3c" if rapid else "#f1c40f"), 1))
                    painter.drawLine(point(prior.tool_position.z_mm, prior.tool_position.radius_mm), point(current.tool_position.z_mm, current.tool_position.radius_mm))
            tool_point = point(frame.tool_position.z_mm, frame.tool_position.radius_mm)
            if self.layers["tool"]:
                painter.setBrush(QColor("#f1c40f"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(tool_point, 6, 6)
            if any(item.severity in {SafetySeverity.COLLISION, SafetySeverity.BLOCKING_ERROR} for item in frame.events):
                painter.setPen(QPen(QColor("#ff2d55"), 3))
                painter.drawLine(tool_point + QPointF(-8, -8), tool_point + QPointF(8, 8))
                painter.drawLine(tool_point + QPointF(-8, 8), tool_point + QPointF(8, -8))


class LatheSimulationWindow(QWidget):
    """Non-modal seekable playback window over an immutable result."""

    SPEEDS = ("0.25×", "0.5×", "1×", "2×", "4×")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("LatheSimulationWindow12_6A")
        self.setMinimumSize(760, 560)
        self.resize(980, 700)
        self._result: SimulationResult | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.step_forward)
        self._build_ui()
        translation_service().language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.control_buttons: dict[str, QPushButton] = {}
        for key, handler in (
            ("run", self.play), ("pause", self.pause), ("resume", self.play),
            ("stop", self.stop), ("reset", self.reset), ("first", self.first),
            ("back", self.step_back), ("forward", self.step_forward), ("last", self.last),
        ):
            button = QPushButton(self)
            button.setObjectName(f"LatheSimulation_{key}")
            button.clicked.connect(handler)
            controls.addWidget(button)
            self.control_buttons[key] = button
        root.addLayout(controls)
        self.canvas = LatheSimulationCanvas(self)
        root.addWidget(self.canvas, 1)
        timeline = QHBoxLayout()
        self.timeline = QSlider(Qt.Orientation.Horizontal, self)
        self.timeline.setObjectName("LatheSimulationTimeline")
        self.timeline.valueChanged.connect(self.seek)
        self.speed = QComboBox(self)
        self.speed.setObjectName("LatheSimulationSpeed")
        self.speed.addItems(self.SPEEDS)
        self.speed.setCurrentText("1×")
        self.speed.currentTextChanged.connect(self._speed_changed)
        self.operation_filter = QComboBox(self)
        self.operation_filter.setObjectName("LatheSimulationOperationFilter")
        self.event_filter = QComboBox(self)
        self.event_filter.setObjectName("LatheSimulationEventFilter")
        timeline.addWidget(self.timeline, 1)
        timeline.addWidget(self.speed)
        timeline.addWidget(self.operation_filter)
        timeline.addWidget(self.event_filter)
        root.addLayout(timeline)
        layer_grid = QGridLayout()
        self.layer_checks: dict[str, QCheckBox] = {}
        for index, layer in enumerate(("initial", "remaining", "tool", "holder", "rapid", "cutting", "removed")):
            check = QCheckBox(self)
            check.setChecked(True)
            check.setObjectName(f"LatheSimulationLayer_{layer}")
            check.toggled.connect(lambda checked, name=layer: self.canvas.set_layer(name, checked))
            layer_grid.addWidget(check, index // 4, index % 4)
            self.layer_checks[layer] = check
        root.addLayout(layer_grid)
        status_layout = QFormLayout()
        self.status_values: dict[str, QLabel] = {}
        for key in ("state", "strategy", "operation", "frame", "progress", "position", "motion", "stock", "volume", "collision", "warning", "approximation"):
            label = QLabel("—", self)
            label.setObjectName(f"LatheSimulationStatus_{key}")
            status_layout.addRow(QLabel(self), label)
            self.status_values[key] = label
        self.status_layout = status_layout
        root.addLayout(status_layout)

    def set_result(self, result: SimulationResult) -> None:
        if not isinstance(result, SimulationResult):
            raise TypeError("Lathe simulation UI result is invalid")
        self._result = result
        self.canvas.set_result(result)
        self.timeline.setRange(0, max(0, len(result.frames) - 1))
        self.timeline.setValue(0)
        self.operation_filter.clear()
        self.operation_filter.addItem(ui_text("lathe.simulation.filter.all"), None)
        for operation_id in dict.fromkeys(item.operation_id for item in result.frames):
            self.operation_filter.addItem(operation_id, operation_id)
        self._update_status()

    def play(self) -> None:
        if self._result is not None and self._result.frames:
            self._timer.start(self._interval_ms())

    def pause(self) -> None:
        self._timer.stop()

    def stop(self) -> None:
        self._timer.stop()
        self.first()

    def reset(self) -> None:
        self.stop()

    def first(self) -> None:
        self.timeline.setValue(0)

    def last(self) -> None:
        self.timeline.setValue(self.timeline.maximum())

    def step_back(self) -> None:
        self.timeline.setValue(max(0, self.timeline.value() - 1))

    def step_forward(self) -> None:
        if self.timeline.value() >= self.timeline.maximum():
            self._timer.stop()
            return
        self.timeline.setValue(self.timeline.value() + 1)

    def seek(self, index: int) -> None:
        self.canvas.set_frame_index(index)
        self._update_status()

    def _interval_ms(self) -> int:
        multiplier = float(self.speed.currentText().replace("×", ""))
        return max(16, round(100 / multiplier))

    def _speed_changed(self, _value: str) -> None:
        if self._timer.isActive():
            self._timer.start(self._interval_ms())

    def _update_status(self) -> None:
        result = self._result
        if result is None or not result.frames:
            self.status_values["state"].setText(result.state.value if result else "—")
            return
        frame = result.frames[self.timeline.value()]
        values = {
            "state": result.state.value,
            "strategy": frame.strategy_id.value,
            "operation": frame.operation_id,
            "frame": f"{frame.sequence + 1}/{len(result.frames)}",
            "progress": f"{frame.progress * 100:.1f}%",
            "position": f"X={frame.tool_position.radius_mm * 2:.3f} mm; Z={frame.tool_position.z_mm:.3f} mm",
            "motion": frame.motion_kind.value,
            "stock": f"rev {frame.stock_revision}",
            "volume": f"≈ {result.removed.estimated_volume_mm3:.3f} mm³",
            "collision": str(result.collision_count),
            "warning": str(result.warning_count),
            "approximation": result.removed.approximation,
        }
        for key, value in values.items():
            self.status_values[key].setText(value)

    def retranslate_ui(self, _language: object = None) -> None:
        self.setWindowTitle(ui_text("lathe.simulation.title"))
        for key, button in self.control_buttons.items():
            button.setText(ui_text(f"lathe.simulation.control.{key}"))
        for layer, check in self.layer_checks.items():
            check.setText(ui_text(f"lathe.simulation.layer.{layer}"))
        self.event_filter.clear()
        self.event_filter.addItems((ui_text("lathe.simulation.filter.all"), ui_text("lathe.simulation.filter.collisions"), ui_text("lathe.simulation.filter.warnings")))
        for row, key in enumerate(self.status_values):
            item = self.status_layout.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is not None and item.widget() is not None:
                item.widget().setText(ui_text(f"lathe.simulation.status.{key}"))

    def closeEvent(self, event: object) -> None:
        self._timer.stop()
        super().closeEvent(event)


class LatheSimulationWindowManager:
    """Own one idempotent non-modal view and fail closed behind the feature flag."""

    def __init__(self, parent: QWidget, *, enabled: bool) -> None:
        if not isinstance(parent, QWidget) or type(enabled) is not bool:
            raise TypeError("Lathe simulation manager inputs are invalid")
        self._parent = parent
        self._enabled = enabled
        self._window: LatheSimulationWindow | None = None

    @property
    def window(self) -> LatheSimulationWindow | None:
        return self._window

    def open(self, result: SimulationResult | None = None) -> LatheSimulationWindow | None:
        if not self._enabled:
            return None
        if self._window is None:
            self._window = LatheSimulationWindow(self._parent)
            self._window.destroyed.connect(self._window_destroyed)
        if result is not None:
            self._window.set_result(result)
        self._window.show()
        self._window.raise_()
        return self._window

    def _window_destroyed(self, _object: object = None) -> None:
        self._window = None


__all__ = ["LatheSimulationCanvas", "LatheSimulationWindow", "LatheSimulationWindowManager"]
