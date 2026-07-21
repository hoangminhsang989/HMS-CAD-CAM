"""Optional pywinauto smoke against a dedicated HMS QA window.

This script never attaches to another production application. It launches a
small PySide6 child process from this file, selects controls semantically with
the UIA backend, verifies focus/dialog/lifecycle behaviour, and closes it.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil

logger = logging.getLogger("hms.qa.windows_native")


def _interactive_desktop_available() -> bool:
    if sys.platform != "win32":
        return False
    handle = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0100)
    if not handle:
        return False
    ctypes.windll.user32.CloseDesktop(handle)
    return True


def _run_host(window_title: str) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    application = QApplication([])
    application.setApplicationName("HMS CAD/CAM QA Native")
    window = QMainWindow()
    window.setWindowTitle(window_title)
    # Qt exposes the accessible name as the UIA window title, so keep it
    # identical to the unique title used by the controller.
    window.setAccessibleName(window_title)
    window.resize(520, 280)

    central = QWidget(window)
    layout = QVBoxLayout(central)
    qa_input = QLineEdit(central)
    qa_input.setObjectName("qaInput")
    qa_input.setAccessibleName("Dữ liệu QA")
    qa_input.setPlaceholderText("Dữ liệu QA")
    submit = QPushButton("Kiểm tra", central)
    submit.setObjectName("qaSubmit")
    submit.setAccessibleName("Kiểm tra")
    open_dialog = QPushButton("Mở hộp thoại QA", central)
    open_dialog.setObjectName("qaDialog")
    open_dialog.setAccessibleName("Mở hộp thoại QA")
    result = QLabel("Chưa kiểm tra", central)
    result.setObjectName("qaResult")
    for widget in (qa_input, submit, open_dialog, result):
        layout.addWidget(widget)
    window.setCentralWidget(central)

    file_menu = window.menuBar().addMenu("Tệp")
    file_menu.setAccessibleName("Tệp")
    close_action = file_menu.addAction("Đóng cửa sổ QA")
    close_action.triggered.connect(window.close)
    submit.clicked.connect(lambda: result.setText(f"Đã nhận: {qa_input.text()}"))

    def show_qa_dialog() -> None:
        dialog = QDialog(window)
        dialog.setWindowTitle("HMS QA Dialog")
        dialog.setAccessibleName("HMS QA Dialog")
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(QLabel("UIA đã mở đúng hộp thoại của cửa sổ QA."))
        ok = QPushButton("OK", dialog)
        ok.clicked.connect(dialog.accept)
        dialog_layout.addWidget(ok)
        window._qa_dialog = dialog
        dialog.show()

    open_dialog.clicked.connect(show_qa_dialog)

    QTimer.singleShot(30_000, window.close)
    window.show()
    window.raise_()
    window.activateWindow()
    return application.exec()


def _wait_for_process_exit(
    process: subprocess.Popen[bytes],
    timeout: float,
    *tracked_pids: int,
) -> None:
    deadline = time.monotonic() + timeout
    pids = {process.pid, *tracked_pids}
    while time.monotonic() < deadline:
        if not any(psutil.pid_exists(pid) for pid in pids):
            return
        time.sleep(0.1)
    raise RuntimeError(f"Process QA còn sống sau khi đóng: {sorted(pids)}")


def _terminate_process_tree(pid: int, *extra_pids: int) -> None:
    processes: list[psutil.Process] = []
    for candidate in (pid, *extra_pids):
        try:
            root = psutil.Process(candidate)
        except psutil.NoSuchProcess:
            continue
        processes.extend([root, *root.children(recursive=True)])
    unique = {item.pid: item for item in processes}
    processes = list(unique.values())
    for child in reversed(processes):
        child.terminate()
    _, alive = psutil.wait_procs(processes, timeout=3)
    for process in alive:
        process.kill()


def run_windows_native_smoke() -> None:
    """Launch and automate only the dedicated HMS QA child window."""
    if not _interactive_desktop_available():
        raise RuntimeError("Không tìm thấy Windows interactive desktop session")

    from pywinauto import Desktop, timings

    token = uuid.uuid4().hex[:8]
    window_title = f"HMS CAD/CAM QA Native {token}"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--host", window_title],
        creationflags=creation_flags,
    )
    host_pid = process.pid
    try:
        desktop = Desktop(backend="uia")
        window = desktop.window(title=window_title)
        logger.info("UIA: chờ cửa sổ %s", window_title)
        window.wait("visible enabled ready", timeout=15)
        host_pid = window.element_info.process_id
        known_pids = {process.pid}
        try:
            known_pids.update(item.pid for item in psutil.Process(process.pid).children(recursive=True))
        except psutil.NoSuchProcess as error:
            raise RuntimeError("Process host QA đã kết thúc trước khi UIA kết nối") from error
        if host_pid not in known_pids:
            raise RuntimeError("UIA trả về cửa sổ không thuộc process QA vừa tạo")

        menu = window.child_window(title="Tệp", control_type="MenuItem")
        logger.info("UIA: kiểm tra menu")
        if not menu.exists(timeout=5):
            raise RuntimeError("Không phát hiện menu Tệp bằng UIA")

        qa_input = window.child_window(control_type="Edit")
        logger.info("UIA: nhập dữ liệu và kiểm tra focus")
        qa_input.set_focus()
        qa_input.set_edit_text("HMS QA.1")
        if not qa_input.has_keyboard_focus():
            raise RuntimeError("QLineEdit QA không nhận focus")

        window.child_window(title="Kiểm tra", control_type="Button").invoke()
        logger.info("UIA: chờ kết quả")
        result = window.child_window(control_type="Text")
        result.wait("exists visible", timeout=5)
        expected_result = "Đã nhận: HMS QA.1"
        timings.wait_until(
            5,
            0.1,
            lambda: result.window_text() == expected_result,
        )
        if result.window_text() != expected_result:
            raise RuntimeError(f"Kết quả QA không đúng: {result.window_text()!r}")

        window.child_window(
            title="Mở hộp thoại QA",
            control_type="Button",
        ).invoke()
        logger.info("UIA: chờ hộp thoại")
        dialog = window.child_window(title="HMS QA Dialog", control_type="Window")
        dialog.wait("visible enabled ready", timeout=5)
        dialog.child_window(title="OK", control_type="Button").invoke()
        dialog.wait_not("visible", timeout=5)

        window.close()
        _wait_for_process_exit(process, 10, host_pid)
        logger.info("Windows-native UIA smoke đạt cho PID %s", host_pid)
    except (subprocess.TimeoutExpired, timings.TimeoutError) as error:
        raise RuntimeError("Windows-native UIA smoke hết thời gian chờ") from error
    finally:
        _terminate_process_tree(process.pid, host_pid)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", metavar="WINDOW_TITLE")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if arguments.host:
        return _run_host(arguments.host)
    try:
        run_windows_native_smoke()
    except (OSError, RuntimeError) as error:
        logger.exception("Windows-native smoke thất bại: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
