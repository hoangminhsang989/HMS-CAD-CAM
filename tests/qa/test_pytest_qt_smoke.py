"""Minimal PySide6/pytest-qt event-loop smoke tests."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLineEdit, QPushButton, QVBoxLayout, QWidget


class _QaSmokeWidget(QWidget):
    submitted = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.input = QLineEdit(self)
        self.button = QPushButton("Gửi", self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.input)
        layout.addWidget(self.button)
        self.button.clicked.connect(self._submit)

    def _submit(self) -> None:
        self.submitted.emit(self.input.text())


@pytest.mark.gui
def test_qtbot_types_clicks_waits_for_signal_and_closes(qtbot) -> None:
    widget = _QaSmokeWidget()
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(widget.isVisible, timeout=1_000)

    qtbot.keyClicks(widget.input, "HMS QA.1")
    with qtbot.waitSignal(widget.submitted, timeout=1_000) as emitted:
        qtbot.mouseClick(widget.button, Qt.MouseButton.LeftButton)

    assert emitted.args == ["HMS QA.1"]
    widget.close()
    qtbot.waitUntil(lambda: not widget.isVisible(), timeout=1_000)
