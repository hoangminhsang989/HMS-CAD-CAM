"""R241 optional machining-simulation workspace, imported on user demand."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.simulation.runtime import SimulationInputSnapshot
from hms_cadcam.simulation import (
    HeightField3AxisEngine,
    PlaybackController,
    PlaybackState,
    QualityMode,
    Timeline,
)
from hms_cadcam.simulation.worker import SimulationWorker
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.localization import ui_text

InputProvider = Callable[[], SimulationInputSnapshot]
PrecomputeProvider = Callable[[SimulationInputSnapshot], object | None]

_TEXT = {
    UiLanguage.VI_VN: {
        "title": "MÔ PHỎNG GIA CÔNG",
        "prepare": "Đang chuẩn bị mô phỏng...",
        "ready_path": "Đường chạy dao đã sẵn sàng",
        "material": "Đang tính bóc vật liệu...",
        "ready": "Đã sẵn sàng xem",
        "cancelled": "Đã hủy",
        "run": "TÍNH MÔ PHỎNG",
        "cancel": "DỪNG TÍNH TOÁN",
        "operations": "Nguyên công / Xác minh",
        "properties": "Thuộc tính mô phỏng",
        "timeline": "Dòng thời gian",
        "remaining": "Hiện phôi còn lại",
        "quality": "Chất lượng",
        "result": "Kết quả bóc vật liệu",
        "unverified": "Hình học Holder/fixture thiếu phải giữ trạng thái CHƯA XÁC MINH.",
    },
    UiLanguage.EN_US: {
        "title": "MACHINING SIMULATION",
        "prepare": "Preparing simulation...",
        "ready_path": "Toolpath ready",
        "material": "Calculating material removal...",
        "ready": "Ready to inspect",
        "cancelled": "Cancelled",
        "run": "CALCULATE SIMULATION",
        "cancel": "STOP CALCULATION",
        "operations": "Operations / Verification",
        "properties": "Simulation properties",
        "timeline": "Timeline",
        "remaining": "Show remaining stock",
        "quality": "Quality",
        "result": "Material-removal result",
        "unverified": "Missing Holder/fixture geometry remains UNVERIFIED.",
    },
    UiLanguage.KO_KR: {
        "title": "가공 시뮬레이션",
        "prepare": "시뮬레이션 준비 중...",
        "ready_path": "툴패스 준비 완료",
        "material": "소재 제거 계산 중...",
        "ready": "검토 준비 완료",
        "cancelled": "취소됨",
        "run": "시뮬레이션 계산",
        "cancel": "계산 중지",
        "operations": "작업 / 검증",
        "properties": "시뮬레이션 속성",
        "timeline": "타임라인",
        "remaining": "잔여 소재 표시",
        "quality": "품질",
        "result": "소재 제거 결과",
        "unverified": "Holder/fixture 형상이 없으면 미검증 상태로 유지됩니다.",
    },
}


class _SimulationCanvas(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("MachiningSimulationViewport")
        self.setMinimumSize(640, 420)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("")
        self.setStyleSheet("background:#111820;border:1px solid #34414d;color:#8fa3b5")
        self._base: QPixmap | None = None
        self._scale = (1.0, 1.0)
        self._height = 500

    def show_result(self, inputs: SimulationInputSnapshot, result: object) -> None:
        width, height = 800, 500
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor("#111820"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        remaining = result.remaining_stock
        cell_w = width / remaining.width
        cell_h = height / remaining.height
        span = max(1.0e-9, remaining.maximum_height - remaining.minimum_height)
        for row in range(remaining.height):
            for column in range(remaining.width):
                value = remaining.top_heights[row * remaining.width + column]
                ratio = (value - remaining.minimum_height) / span
                color = QColor.fromRgbF(0.15 + 0.15 * ratio, 0.35 + 0.45 * ratio, 0.75 - 0.35 * ratio)
                painter.fillRect(int(column * cell_w), int((remaining.height - row - 1) * cell_h), max(1, int(cell_w + 1)), max(1, int(cell_h + 1)), color)
        stock = inputs.setup.stock
        scale_x, scale_y = width / stock.size_x.value, height / stock.size_y.value
        for event in inputs.artifact.events:
            start = getattr(event, "start", None)
            end = getattr(event, "end", None)
            if start is None or end is None:
                continue
            color = {
                "rapid": "#ff3636",
                "cutting": "#ffd22e",
                "link": "#ffffff",
                "retract": "#32d06b",
            }.get(
                "rapid" if event.kind.value == "rapid" else event.motion_class.value,
                "#ffd22e",
            )
            painter.setPen(QPen(QColor(color), 1.5))
            painter.drawLine(
                int(start.position.x * scale_x), height - int(start.position.y * scale_y),
                int(end.position.x * scale_x), height - int(end.position.y * scale_y),
            )
        painter.end()
        self._base = QPixmap(pixmap)
        self._scale = (scale_x, scale_y)
        self._height = height
        self.setPixmap(pixmap)

    def show_tool_pose(self, inputs: SimulationInputSnapshot, event_index: int) -> None:
        if self._base is None:
            return
        pixmap = QPixmap(self._base)
        painter = QPainter(pixmap)
        event = inputs.artifact.events[event_index] if event_index >= 0 else None
        pose = getattr(event, "end", inputs.artifact.initial_pose)
        x = int(pose.position.x * self._scale[0])
        y = self._height - int(pose.position.y * self._scale[1])
        painter.setPen(QPen(QColor("#00e5ff"), 3.0))
        painter.drawEllipse(x - 6, y - 6, 12, 12)
        painter.drawLine(x, y - 24, x, y + 4)
        painter.end()
        self.setPixmap(pixmap)


class MachiningSimulationWindow(QMainWindow):
    """High-density optional workspace with progressive, cancellable compute."""

    def __init__(
        self,
        input_provider: InputProvider,
        parent: QWidget | None = None,
        *,
        precompute_provider: PrecomputeProvider | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MachiningSimulationR241Window")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(1440, 900)
        self._input_provider = input_provider
        self._precompute_provider = precompute_provider
        self._worker: SimulationWorker | None = None
        self._inputs: SimulationInputSnapshot | None = None
        self._playback: PlaybackController | None = None
        self._started = 0.0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(100)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        self._playback_timer = QTimer(self)
        self._playback_timer.timeout.connect(self._playback_tick)
        self._build_ui()
        self.retranslate()

    @property
    def worker_active(self) -> bool:
        return self._worker is not None

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setStyleSheet(
            "QWidget{background:#202831;color:#e5ebef} QGroupBox{border:1px solid #3b4a58;margin-top:8px} "
            "QPushButton{background:#354452;padding:6px;border:1px solid #526474} QPushButton:disabled{color:#71808c}"
        )
        layout = QVBoxLayout(root)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QGroupBox()
        self.left_group = left
        left_layout = QVBoxLayout(left)
        self.operation_tree = QTreeWidget()
        self.operation_tree.setHeaderLabels(["Operation", "State"])
        left_layout.addWidget(self.operation_tree)
        self.canvas = _SimulationCanvas()
        right = QGroupBox()
        self.right_group = right
        form = QFormLayout(right)
        self.quality = QComboBox()
        self.quality.addItem("NHANH", QualityMode.FAST)
        self.quality.addItem("TIÊU CHUẨN", QualityMode.STANDARD)
        self.quality.addItem("CHI TIẾT", QualityMode.DETAILED)
        self.quality.setCurrentIndex(1)
        self.remaining_visible = QCheckBox()
        self.remaining_visible.setChecked(True)
        self.result_label = QLabel("—")
        self.result_label.setWordWrap(True)
        self.scope_label = QLabel()
        self.scope_label.setWordWrap(True)
        form.addRow("Mode", self.quality)
        form.addRow(self.remaining_visible)
        form.addRow("Result", self.result_label)
        form.addRow(self.scope_label)
        splitter.addWidget(left)
        splitter.addWidget(self.canvas)
        splitter.addWidget(right)
        splitter.setSizes([260, 900, 320])
        layout.addWidget(splitter, 1)
        self.timeline_group = QGroupBox()
        timeline_layout = QVBoxLayout(self.timeline_group)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        buttons = QHBoxLayout()
        self.play = QPushButton("Play")
        self.pause = QPushButton("Pause")
        self.stop = QPushButton("Stop")
        self.previous = QPushButton("Previous event")
        self.next = QPushButton()
        self.speed = QComboBox()
        for value in ("0.1x", "0.25x", "0.5x", "1x", "2x", "5x", "10x", "MAX"):
            self.speed.addItem(value)
        self.speed.setCurrentText("1x")
        for widget in (self.play, self.pause, self.stop, self.previous, self.next, self.speed):
            buttons.addWidget(widget)
        timeline_layout.addWidget(self.timeline)
        timeline_layout.addLayout(buttons)
        layout.addWidget(self.timeline_group)
        progress_row = QHBoxLayout()
        self.status = QLabel()
        self.elapsed = QLabel("0.000 s")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.run_button = QPushButton()
        self.cancel_button = QPushButton()
        self.cancel_button.setEnabled(False)
        for widget in (self.status, self.progress, self.elapsed, self.run_button, self.cancel_button):
            progress_row.addWidget(widget)
        layout.addLayout(progress_row)
        self.setCentralWidget(root)
        self.run_button.clicked.connect(self.calculate)
        self.cancel_button.clicked.connect(self.cancel)
        self.remaining_visible.toggled.connect(self.canvas.setVisible)
        self.play.clicked.connect(self._play)
        self.pause.clicked.connect(self._pause)
        self.stop.clicked.connect(self._stop)
        self.previous.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        self.speed.currentTextChanged.connect(self._speed_changed)

    def retranslate(self) -> None:
        text = _TEXT[translation_service().language]
        self.canvas.setText(ui_text("3D Simulation Viewport"))
        self.next.setText(ui_text("Next event"))
        self.setWindowTitle(text["title"])
        self.left_group.setTitle(text["operations"])
        self.right_group.setTitle(text["properties"])
        self.timeline_group.setTitle(text["timeline"])
        self.remaining_visible.setText(text["remaining"])
        self.run_button.setText(text["run"])
        self.cancel_button.setText(text["cancel"])
        self.scope_label.setText(text["unverified"])
        if not self.worker_active:
            self.status.setText(text["prepare"])

    def prepare_scene(self) -> None:
        """Show the window first, then load only lightweight toolpath state."""
        QTimer.singleShot(0, self._prepare_source)

    def _prepare_source(self) -> None:
        text = _TEXT[translation_service().language]
        try:
            self._inputs = self._input_provider()
        except (RuntimeError, TypeError, ValueError) as error:
            self.status.setText(str(error))
            self.run_button.setEnabled(False)
            return
        self.operation_tree.clear()
        root = QTreeWidgetItem([str(self._inputs.operation.operation_id), "TOOLPATH READY"])
        root.addChild(QTreeWidgetItem(["Tool", self._inputs.tool.name]))
        root.addChild(QTreeWidgetItem(["Holder", "UNVERIFIED" if self._inputs.holder is None else self._inputs.holder.name]))
        root.addChild(QTreeWidgetItem(["Fixtures", str(len(self._inputs.setup.fixtures))]))
        self.operation_tree.addTopLevelItem(root)
        root.setExpanded(True)
        self._playback = PlaybackController(Timeline.from_artifacts((self._inputs.artifact,)))
        self.timeline.setRange(0, max(0, len(self._playback.timeline.events) - 1))
        self.status.setText(text["ready_path"])
        if self._precompute_provider is not None:
            try:
                precomputed = self._precompute_provider(self._inputs)
            except (OSError, RuntimeError, TypeError, ValueError):
                precomputed = None
            if precomputed is not None:
                self._succeeded(precomputed)

    def calculate(self) -> None:
        if self._worker is not None:
            return
        if self._inputs is None:
            self._prepare_source()
        if self._inputs is None:
            return
        inputs = self._inputs
        quality = QualityMode(self.quality.currentData())
        engine = HeightField3AxisEngine()
        self._started = perf_counter()
        self.progress.setValue(0)
        self.status.setText(_TEXT[translation_service().language]["material"])
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._elapsed_timer.start()

        def compute(cancelled, progress):
            return engine.simulate(
                stock=inputs.setup.stock,
                artifact=inputs.artifact,
                tool=inputs.tool,
                quality=quality,
                cancellation=cancelled,
                progress=lambda done, total: progress("material_removal", done, total),
            )

        worker = SimulationWorker(compute)
        self._worker = worker
        worker.signals.progress.connect(self._progress_changed)
        worker.signals.succeeded.connect(self._succeeded)
        worker.signals.cancelled.connect(self._cancelled)
        worker.signals.failed.connect(self._failed)
        worker.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(worker)

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_button.setEnabled(False)

    def _progress_changed(self, _stage: str, value: int, total: int) -> None:
        self.progress.setValue(0 if total <= 0 else round(value * 100 / total))

    def _succeeded(self, result: object) -> None:
        assert self._inputs is not None
        self.canvas.show_result(self._inputs, result)
        stock = result.remaining_stock
        self.result_label.setText(
            f"Removed {stock.removed_volume:.3f} {stock.unit.value}³ · "
            f"Remaining {stock.remaining_volume:.3f} {stock.unit.value}³ · "
            f"HEIGHTFIELD_3AXIS {result.quality.value.upper()}"
        )
        self.status.setText(_TEXT[translation_service().language]["ready"])
        self.progress.setValue(100)

    def _cancelled(self) -> None:
        self.status.setText(_TEXT[translation_service().language]["cancelled"])

    def _failed(self, message: str) -> None:
        self.status.setText(message)

    def _finished(self) -> None:
        self._elapsed_timer.stop()
        self._update_elapsed()
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _update_elapsed(self) -> None:
        if self._started:
            self.elapsed.setText(f"{perf_counter() - self._started:.3f} s")

    def _play(self) -> None:
        if self._playback is not None:
            self._playback.play()
            self._update_playback_timer()

    def _pause(self) -> None:
        if self._playback is not None:
            self._playback.pause()
            self._playback_timer.stop()

    def _stop(self) -> None:
        if self._playback is not None:
            self._playback.stop()
            self._playback_timer.stop()
            self.timeline.setValue(0)

    def _step(self, amount: int) -> None:
        if self._playback is not None:
            event = self._playback.step(amount)
            if event is not None:
                self.timeline.setValue(event.index)
                if self._inputs is not None:
                    self.canvas.show_tool_pose(self._inputs, event.toolpath_event_index)

    def _speed_changed(self, value: str) -> None:
        if self._playback is None:
            return
        speed = float("inf") if value == "MAX" else float(value.rstrip("x"))
        self._playback.set_speed(speed)
        if self._playback.state is PlaybackState.PLAYING:
            self._update_playback_timer()

    def _update_playback_timer(self) -> None:
        if self._playback is None:
            return
        interval = 0 if self._playback.speed == float("inf") else max(
            16, round(250 / self._playback.speed)
        )
        self._playback_timer.start(interval)

    def _playback_tick(self) -> None:
        if self._playback is None or self._playback.state is not PlaybackState.PLAYING:
            self._playback_timer.stop()
            return
        if self._playback.cursor >= len(self._playback.timeline.events) - 1:
            self._pause()
            return
        self._step(1)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.cancel()
        self._playback_timer.stop()
        event.accept()
