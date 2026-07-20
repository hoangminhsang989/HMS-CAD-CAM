"""Real Windows/PySide6/OCP smoke test for Simulation Viewer Stage 7C.2.

The fixture is built synchronously from the 7C.1 contracts.  This intentionally
does not expose a run/progress/cancel control: those belong to a later stage.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import logging
import tempfile
from pathlib import Path

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.simulation import SimulationIssueCode
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend

logger = logging.getLogger(__name__)


def _load_fixture():
    path = Path(__file__).parent / "unit" / "test_simulation_viewer.py"
    spec = importlib.util.spec_from_file_location("hms_stage7c2_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load 7C.2 fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._fixture


def _source(path: Path) -> None:
    if not BRepTools.Write_s(BRepPrimAPI_MakeBox(80, 60, 30).Shape(), str(path)):
        raise RuntimeError("Cannot write Simulation BREP fixture")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory(prefix="hms_stage7c2_")
    root = Path(temporary.name)
    source = root / "simulation-box.brep"
    _source(source)
    service = ProjectService.create_default(root / "config")
    session = service.create_project_from_source(root, "Simulation Viewer", source)
    kernel = OcpCadKernel()
    backend = OcpCadViewportBackend(kernel)
    window = MainWindow(service, kernel, backend)
    window.resize(1280, 760)
    window.show()
    fixture = _load_fixture()
    failures: list[BaseException] = []

    def finish(error: BaseException | None = None) -> None:
        if error is not None:
            failures.append(error)
            logger.error("Stage 7C.2 GUI failed: %s", error)
        try:
            window.cad_controller.shutdown()
            window.viewport.shutdown()
            window.hide()
        except RuntimeError:
            pass
        application.quit()

    def verify() -> None:
        generation = service.cam_generation
        project_id = session.manifest.project_id
        operation, artifact, wcs, _, _, pass_result, context = fixture()
        context = dataclasses.replace(
            context,
            project_id=project_id,
            project_generation=generation,
        )
        assert window.viewport.bind_simulation_project(project_id, generation)
        assert window.viewport.display_simulation(
            pass_result, artifact, wcs, context
        )
        assert window.viewport.simulation_presentations[0].status.value == "pass"
        assert window.viewport.simulation_presentations[0].path_segments

        warning = fixture(
            (SimulationIssueCode.RAPID_BELOW_SAFE,),
            operation_id=operation.operation_id,
            project_id=project_id,
        )
        _, warning_artifact, warning_wcs, _, _, warning_result, warning_context = warning
        warning_context = dataclasses.replace(
            warning_context,
            project_generation=generation,
        )
        assert window.viewport.display_simulation(
            warning_result, warning_artifact, warning_wcs, warning_context
        )
        marker = window.viewport.simulation_presentations[0].markers[0]
        assert window.viewport.lookup_simulation_issue(
            project_id=project_id,
            operation_id=operation.operation_id,
            result_id=warning_result.result_id,
            marker_id=marker.marker_id,
        ) == marker

        fail = fixture(
            (SimulationIssueCode.TOOL_FIXTURE_COLLISION,),
            operation_id=operation.operation_id,
            project_id=project_id,
        )
        _, fail_artifact, fail_wcs, _, _, fail_result, fail_context = fail
        fail_context = dataclasses.replace(fail_context, project_generation=generation)
        assert window.viewport.set_simulation_visibility(operation.operation_id, False)
        assert window.viewport.display_simulation(
            fail_result, fail_artifact, fail_wcs, fail_context
        )
        assert not window.viewport.simulation_presentations[0].visible
        assert window.viewport.set_simulation_visibility(operation.operation_id, True)

        assert window.viewport.display_toolpath(fail_artifact)
        assert window.viewport.set_toolpath_visibility(operation.operation_id, False)
        assert window.viewport.simulation_presentations[0].visible

        copied = service.save_as(root, "Simulation Viewer Copy")
        assert window.viewport.bind_simulation_project(
            copied.manifest.project_id,
            service.cam_generation,
        )
        assert window.viewport.simulation_presentations == ()
        window.cad_controller.bind_project(None)
        assert window.viewport.simulation_presentations == ()
        window.resize(1024, 680)
        window.resize(1400, 820)
        logger.info(
            "GUI verified: PASS/WARN/FAIL, semantic path, marker lookup, show/hide, "
            "source visibility independence, replacement, project switch, CAD clear, resize"
        )
        finish()

    QTimer.singleShot(250, lambda: _guarded(verify, finish))
    QTimer.singleShot(90_000, lambda: finish(TimeoutError("Stage 7C.2 GUI timeout")) if not failures else None)
    application.exec()
    temporary.cleanup()
    if failures:
        raise failures[0]
    return 0


def _guarded(callback, fail) -> None:
    try:
        callback()
    except BaseException as error:
        fail(error)


if __name__ == "__main__":
    raise SystemExit(main())
