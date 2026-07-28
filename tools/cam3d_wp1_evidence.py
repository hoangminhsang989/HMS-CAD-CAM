"""Capture deterministic Stage 9A.8 WP1 production-widget evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from uuid import UUID


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--state",
        choices=("legacy", "empty", "read_only", "stale", "error"),
        required=True,
    )
    parser.add_argument("--scale-percent", type=int, choices=(100, 125, 150), default=100)
    parser.add_argument("--screenshot-name", default=None)
    arguments = parser.parse_args()
    if arguments.screenshot_name is not None:
        screenshot_name = Path(arguments.screenshot_name)
        if (
            screenshot_name.name != arguments.screenshot_name
            or screenshot_name.suffix.lower() != ".png"
        ):
            parser.error("--screenshot-name must be one PNG filename")
    return arguments


def main() -> int:
    args = _arguments()
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    os.environ["QT_SCALE_FACTOR"] = str(args.scale_percent / 100)
    repository_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository_root / "src"))

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QDockWidget

    from hms_cadcam.cad.unavailable import UnavailableCadKernel
    from hms_cadcam.project.service import ProjectService
    from hms_cadcam.ui.cam3d_function_state import Cam3DPresentationState
    from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
    from hms_cadcam.ui.i18n import UiLanguage, translation_service
    from hms_cadcam.ui.main_window import MainWindow
    from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend

    app = QApplication.instance() or QApplication([])
    translation_service().set_language(UiLanguage.VI_VN)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config_dir = args.output_dir / "runtime_config" / f"{args.state}_{args.scale_percent}"
    service = ProjectService.create_default(config_dir)
    cam3d_enabled = args.state != "legacy"
    flags = UiFeatureFlags(
        {
            UiFeatureFlag.POST_ASSEMBLY_9A7: False,
            UiFeatureFlag.CAM_3D_9A8: cam3d_enabled,
        }
    )
    window = MainWindow(
        service,
        UnavailableCadKernel("Stage 9A.8 WP1 evidence"),
        UnavailableCadViewportBackend("Stage 9A.8 WP1 evidence"),
        ui_feature_flags=flags,
    )
    window.resize(1500, 900)
    window.show()
    app.processEvents()
    window._open_cam3d_function_ui()
    app.processEvents()

    project_id = UUID("90a80000-0000-4000-8000-000000000001")
    if args.state == "read_only":
        window.cam3d_function_panel.set_state(
            Cam3DPresentationState.for_read_only(project_id, 1)
        )
    elif args.state == "stale":
        window.cam3d_function_panel.set_state(
            Cam3DPresentationState.stale(project_id, 2)
        )
    elif args.state == "error":
        window.cam3d_function_panel.set_state(
            Cam3DPresentationState.error(
                "Không thể xác minh đầu vào hình học CAM 3D.", project_id, 2
            )
        )
    app.processEvents()
    if cam3d_enabled:
        window.resizeDocks(
            [window.cam3d_function_dock],
            [480],
            Qt.Orientation.Horizontal,
        )
        window.cam3d_function_dock.raise_()
        app.processEvents()

    filename = (
        args.screenshot_name
        or f"cam3d_wp1_{args.state}_dpi{args.scale_percent}.png"
    )
    screenshot = args.output_dir / filename
    saved = window.grab().save(str(screenshot), "PNG")
    screen = app.primaryScreen()
    result = {
        "schema": "hms.stage9a8.wp1.native-evidence.v1",
        "state": args.state,
        "scale_percent": args.scale_percent,
        "qt_platform": app.platformName(),
        "device_pixel_ratio": window.devicePixelRatioF(),
        "logical_dpi": None if screen is None else screen.logicalDotsPerInch(),
        "window_size": [window.width(), window.height()],
        "screenshot": str(screenshot.resolve()),
        "saved": bool(saved),
        "feature_enabled": cam3d_enabled,
        "action_present": hasattr(window, "cam3d_function_action"),
        "action_visible": (
            window.cam3d_function_action.isVisible()
            if hasattr(window, "cam3d_function_action")
            else False
        ),
        "dock_present": hasattr(window, "cam3d_function_dock"),
        "dock_area": (
            int(window.dockWidgetArea(window.cam3d_function_dock).value)
            if hasattr(window, "cam3d_function_dock")
            else None
        ),
        "panel_present": hasattr(window, "cam3d_function_panel"),
        "rendered_state": (
            window.cam3d_function_panel.presentation_state.state.value
            if hasattr(window, "cam3d_function_panel")
            else None
        ),
        "placeholder_controls_enabled": (
            sum(
                control.isEnabled()
                for control in window.cam3d_function_panel.placeholder_controls
            )
            if hasattr(window, "cam3d_function_panel")
            else 0
        ),
        "dock_count": len(window.findChildren(QDockWidget)),
        "dock_object_names": [
            dock.objectName() for dock in window.findChildren(QDockWidget)
        ],
        "cam3d_action_count": sum(
            action.objectName() == "Cam3DFunctionOpenAction"
            for action in window.findChildren(QAction)
        ),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    window.close()
    app.processEvents()
    return 0 if saved else 2


if __name__ == "__main__":
    sys.exit(main())