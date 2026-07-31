"""Optional feature-topology adapter for the Lathe Program Preview action."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QPushButton, QWidget

from hms_cadcam.cam.lathe.lathe_post import LatheProgramService
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


__all__ = ["install_lathe_program_preview"]
