"""Automated real-Windows GUI verification for Facing 2.5D Stage 7B.2."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import ArtifactStatus, FacingParameters
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend

logger = logging.getLogger(__name__)


def _source(path: Path) -> None:
    if not BRepTools.Write_s(BRepPrimAPI_MakeBox(100.0, 100.0, 50.0).Shape(), str(path)):
        raise RuntimeError("Cannot write Facing BREP fixture")


def _window(service: ProjectService) -> tuple[MainWindow, OcpCadViewportBackend]:
    kernel = OcpCadKernel()
    backend = OcpCadViewportBackend(kernel)
    window = MainWindow(service, kernel, backend)
    window.show()
    window.resize(1280, 760)
    return window, backend


def _dispose(window: MainWindow) -> None:
    window.cad_controller.shutdown()
    window.viewport.shutdown()
    window.hide()


def _operation(service: ProjectService):
    return next(operation for job in service.cam_snapshot.jobs for setup in job.setups
                for operation in setup.operation_tree.operations)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory(prefix="hms_stage7b2_")
    root = Path(temporary.name)
    source = root / "facing-box.brep"
    _source(source)
    service = ProjectService.create_default(root / "config")
    session = service.create_project_from_source(root, "Facing Original", source)
    original_root = session.root_path
    window, backend = _window(service)
    windows = [window]
    failures: list[BaseException] = []
    state = {"step": "import BREP"}

    def finish() -> None:
        for candidate in windows:
            try:
                if candidate.isVisible():
                    _dispose(candidate)
            except RuntimeError:
                pass
        application.quit()

    def fail(error: BaseException) -> None:
        failures.append(error)
        logger.error("Stage 7B.2 GUI failed at %s: %s", state["step"], error)
        finish()

    def wait_ready(candidate: MainWindow, callback) -> None:
        if candidate.cad_controller.is_busy:
            QTimer.singleShot(25, lambda: wait_ready(candidate, callback))
            return
        try:
            callback()
        except BaseException as error:
            fail(error)

    def initial() -> None:
        nonlocal service, window, backend
        state["step"] = "create Setup/resources/Facing and Generate"
        assert window.cad_controller.active_tree is not None
        workspace = window.cam_workspace
        window.cam_dock.show(); window.cam_dock.raise_()
        workspace.create_job(); workspace.create_setup(); workspace.create_basic_resources(); workspace.add_operation()
        workspace.generate_selected()
        operation = _operation(service)
        assert operation.artifact_state.status is ArtifactStatus.VALID
        assert backend._toolpaths.get(operation.operation_id)
        workspace.editor._facing_fields["target"].setText("48")
        workspace.editor._submit(); workspace.generate_selected()
        assert FacingParameters.from_operation_parameters(_operation(service).parameters).target_height.value == 48
        assert service.load_toolpath_artifact(operation.operation_id) is not None
        service.save()
        screenshot = Path(tempfile.gettempdir()) / "hms_stage7b2_facing.png"
        assert application.primaryScreen().grabWindow(0).save(str(screenshot))
        _dispose(window); service.close_project()

        service = ProjectService.create_default(root / "reopen-config")
        service.open_project(original_root)
        window, backend = _window(service)
        windows.append(window)
        state["step"] = "Save/Open and viewer restore"
        QTimer.singleShot(25, lambda: wait_ready(window, reopen))

    def reopen() -> None:
        nonlocal service, window, backend
        operation = _operation(service)
        assert operation.artifact_state.status is ArtifactStatus.VALID
        artifact = service.load_toolpath_artifact(operation.operation_id)
        assert artifact is not None and window.viewport.display_toolpath(artifact)
        assert backend._toolpaths.get(operation.operation_id)
        copied = service.save_as(root, "Facing Copy")
        assert service.load_toolpath_artifact(operation.operation_id) is not None
        workspace = window.cam_workspace
        workspace.bind_project(copied)
        workspace.refresh(("operation", str(operation.node_id)))
        workspace.editor._facing_fields["target"].setText("47")
        workspace.editor._submit()
        autosave = service.autosave()
        assert autosave is not None and copied.root_path == service.current_project.root_path
        copy_root = copied.root_path
        _dispose(window)

        opener = ProjectService.create_default(root / "recovery-config")
        opener._session_locks._pid_checker = lambda _pid: False
        try:
            opener.open_project(copy_root)
        except RecoveryRequiredError as error:
            opener.recover_project(error.assessment)
        else:
            raise AssertionError("Autosave recovery was not requested")
        service = opener
        window, backend = _window(service)
        windows.append(window)
        state["step"] = "Autosave/Recovery/Recompute"
        QTimer.singleShot(25, lambda: wait_ready(window, recovered))

    def recovered() -> None:
        operation = _operation(service)
        parameters = FacingParameters.from_operation_parameters(operation.parameters)
        assert parameters.target_height.value == 47
        workspace = window.cam_workspace
        workspace.refresh(("operation", str(operation.node_id)))
        workspace.generate_selected()
        assert _operation(service).artifact_state.status is ArtifactStatus.VALID
        assert backend._toolpaths.get(operation.operation_id)
        window.cam_dock.hide(); window.cam_dock.show(); window.resize(1100, 700)
        service.save()
        logger.info("GUI verified: BREP, Setup/BOX/resources, Facing Generate/Recompute, viewer, Save/Open/Save As/Autosave/Recovery")
        finish()

    QTimer.singleShot(100, lambda: wait_ready(window, initial))
    QTimer.singleShot(90_000, lambda: fail(TimeoutError(f"timeout at {state['step']}")) if not failures else None)
    application.exec()
    temporary.cleanup()
    if failures:
        raise failures[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
