"""Run the standalone OCP/PySide6 viewer technical spike."""

from __future__ import annotations

import sys

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMainWindow

from viewer import OcpViewerWidget


class SpikeWindow(QMainWindow):
    """Top-level spike window with explicit OCCT teardown ordering."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HMS CAD/CAM — OCP Viewer Spike 4B1")
        self.viewer = OcpViewerWidget(self)
        self.setCentralWidget(self.viewer)
        self.resize(900, 650)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        """Release the embedded OCCT view before closing its parent HWND."""
        self.viewer.close()
        super().closeEvent(event)


def main() -> int:
    """Open the spike window and return the Qt event-loop exit code."""
    application = QApplication(sys.argv)
    window = SpikeWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
