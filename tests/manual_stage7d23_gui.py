"""Windows/PySide6 smoke checklist for the production Post UI (7D.2.3).

Run this script on a Windows desktop with the repository virtual environment.
It intentionally does not auto-generate or export an NC file; the operator
selects a valid single operation and follows the checklist in the console.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend

logger = logging.getLogger("manual_stage7d23_gui")


def checklist(window: MainWindow) -> tuple[str, ...]:
    panel = window.cam_workspace.post_panel
    return (
        "Select one Facing/Contour/Pocket/Drilling/Reaming/Boring operation",
        "Verify project/setup/ToolpathArtifact/Simulation provenance and fingerprints",
        "Verify T/H/D binding draft and atomic Apply; try invalid values",
        "Try DISABLED and LEGACY_WORKNC_LEFT (G41 only for Contour)",
        "Try REQUIRE_PASS/ALLOW_WARN/OPTIONAL and stale/missing Simulation",
        "Generate; confirm no file is written and preview text is read-only",
        "Check CRLF/UTF-8/byte count/SHA-256 and NOT MACHINE CERTIFIED metadata",
        "Save Managed Artifact, inspect managed status, then explicit Clear",
        "Export to a local or mapped/UNC directory with overwrite confirmation",
        "Switch operation/project, Save/Open, Autosave/Recovery and resize/close",
        "Verify Tapping is fail-closed and emits no G84/G74/M29",
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    app = QApplication(sys.argv)
    service = ProjectService.create_default(ROOT / "temp" / "manual_stage7d23_config")
    kernel = OcpCadKernel()
    window = MainWindow(service, kernel, OcpCadViewportBackend(kernel))
    window.resize(1600, 920)
    window.show()
    logger.info("Post panel object: %s", window.cam_workspace.post_panel.objectName())
    for index, item in enumerate(checklist(window), 1):
        logger.info("%02d. %s", index, item)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
