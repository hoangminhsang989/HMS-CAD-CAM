"""Application startup and top-level error handling."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from hms_cadcam.cad.factory import CadKernelFactory
from hms_cadcam.core.logging_config import configure_logging
from hms_cadcam.core.paths import AppPathKind, ApplicationPathsService
from hms_cadcam.core.hms_backup import HmsBackupService, HmsRestoreService
from hms_cadcam.core.storage_layout import StorageBootstrapService
from hms_cadcam.core.user_profiles import UserProfileService
from hms_cadcam.project.service import ProjectService
from hms_cadcam.viewer.factory import CadViewportBackendFactory


def run(argv: Sequence[str] | None = None) -> int:
    """Start HMS CAD/CAM and return its process exit code."""
    logger = logging.getLogger(__name__)
    try:
        paths = ApplicationPathsService.production()
        configure_logging(paths.path(AppPathKind.LOGS))
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

        project_service = ProjectService.create_default(
            paths.path(AppPathKind.USER_CONFIG),
            document_runtime_root=(
                paths.path(AppPathKind.TEMP) / "Document-Runtime"
            ),
            default_document_directory=paths.documents_root,
        )
        cad_kernel = CadKernelFactory.create()
        viewport_backend = CadViewportBackendFactory.create(cad_kernel)
        storage_bootstrap = StorageBootstrapService(paths)
        user_profiles = UserProfileService(paths)
        backup_service = HmsBackupService(paths, profile_service=user_profiles)
        restore_service = HmsRestoreService(
            paths,
            backup_service=backup_service,
            profile_service=user_profiles,
        )
        window = MainWindow(
            project_service,
            cad_kernel,
            viewport_backend,
            application_paths=paths,
            storage_bootstrap=storage_bootstrap,
            user_profile_service=user_profiles,
            hms_backup_service=backup_service,
            hms_restore_service=restore_service,
        )
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
