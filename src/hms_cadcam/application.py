"""Application startup and top-level error handling."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from hms_cadcam.core.logging_config import configure_logging
from hms_cadcam.core.paths import AppPaths
from hms_cadcam.project.service import ProjectService


def run(argv: Sequence[str] | None = None) -> int:
    """Start HMS CAD/CAM and return its process exit code."""
    logger = logging.getLogger(__name__)
    try:
        paths = AppPaths.for_current_user()
        configure_logging(paths.log_dir)
    except OSError:
        if not logging.getLogger().handlers:
            logging.basicConfig(level=logging.ERROR)
        logger.exception("Không thể khởi tạo thư mục hoặc file nhật ký")
        return 1

    arguments = list(argv) if argv is not None else sys.argv

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from hms_cadcam.ui.main_window import MainWindow

        application = QApplication(arguments)
        application.setApplicationName("HMS CAD/CAM")
        application.setApplicationDisplayName("HMS CAD/CAM")
        application.setOrganizationName("HMS")

        project_service = ProjectService.create_default(paths.config_dir)
        window = MainWindow(project_service)
        window.show()
        logger.info("Ứng dụng HMS CAD/CAM đã khởi động")
        exit_code = application.exec()
        logger.info("Ứng dụng HMS CAD/CAM đã đóng với mã %s", exit_code)
        return exit_code
    except ImportError:
        logger.exception("Không thể nạp PySide6 hoặc module giao diện")
        return 1
    except Exception as error:
        logger.exception("Lỗi nghiêm trọng khi khởi động ứng dụng")
        application = QApplication.instance() if "QApplication" in locals() else None
        if application is not None:
            QMessageBox.critical(
                None,
                "HMS CAD/CAM",
                f"Không thể khởi động ứng dụng.\n\n{error}",
            )
        return 1
