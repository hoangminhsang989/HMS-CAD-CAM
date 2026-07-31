"""Optional feature-topology adapter for the Lathe Program Preview action."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QPushButton, QWidget

from hms_cadcam.cam.lathe.lathe_post import LatheProgramService, LatheBasicNcService
from hms_cadcam.ui.basic_nc_preview import BasicNcPreviewController
from hms_cadcam.ui.lathe_program_preview import LatheProgramPreviewController


def install_lathe_program_preview(
    workspace: QWidget,
    enabled: bool,
    service: LatheProgramService,
    *,
    parent: QWidget | None = None,
    assemble_callback: Callable[[], object] | None = None,
) -> tuple[LatheProgramPreviewController, QPushButton] | None:
    """Install one explicit action and one owned controller when enabled."""

    if not isinstance(workspace, QWidget) or type(enabled) is not bool:
        raise TypeError("workspace and enabled flag are invalid")
    if not enabled:
        return None
    controller = LatheProgramPreviewController(service, assemble_callback, parent or workspace)
    action = QPushButton("Program Preview", parent or workspace)
    action.setObjectName("LatheProgramPreviewAction")
    action.setAccessibleName("Program Preview")
    action.setToolTip("Xem chương trình trung gian — PREVIEW ONLY — NOT MACHINE-READY")
    action.clicked.connect(lambda: controller.open(workspace))
    return controller, action


def install_lathe_basic_nc_preview(
    workspace: QWidget,
    enabled: bool,
    service: LatheBasicNcService,
    *,
    parent: QWidget | None = None,
    program_provider: Callable[[], object | None] | None = None,
) -> tuple[BasicNcPreviewController, QPushButton] | None:
    """Install the explicit Stage 12.4B Generate action when the feature is on."""

    if not isinstance(workspace, QWidget) or type(enabled) is not bool or not isinstance(service, LatheBasicNcService):
        raise TypeError("workspace, enabled, and service are invalid")
    if not enabled:
        return None
    controller = BasicNcPreviewController(service, program_provider, parent or workspace)
    action = QPushButton("Generate Basic NC Preview", parent or workspace)
    action.setObjectName("LatheBasicNcPreviewAction")
    action.setAccessibleName("Generate Basic NC Preview")
    action.setToolTip("Generate explicit unverified .NC preview; not machine-ready")
    action.clicked.connect(controller.generate)
    action.clicked.connect(lambda: controller.open(workspace))
    return controller, action


__all__ = ["install_lathe_basic_nc_preview", "install_lathe_program_preview"]
