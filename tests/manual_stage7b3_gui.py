"""Real Windows GUI verification for Stage 7B.3 planar FACE Facing."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import ArtifactStatus, FacingBoundarySource
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend


def _write_source(path: Path) -> None:
    shape = BRepPrimAPI_MakeBox(60.0, 40.0, 40.0).Shape()
    if not BRepTools.Write_s(shape, str(path)):
        raise RuntimeError("Cannot write planar FACE BREP fixture")


def _window(service: ProjectService) -> tuple[MainWindow, OcpCadViewportBackend]:
    kernel = OcpCadKernel()
    backend = OcpCadViewportBackend(kernel)
    window = MainWindow(service, kernel, backend)
    window.resize(1280, 760)
    window.show()
    return window, backend


def _operation(service: ProjectService):
    return next(operation for job in service.cam_snapshot.jobs for setup in job.setups
                for operation in setup.operation_tree.operations)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory(prefix="hms_stage7b3_")
    root = Path(temporary.name)
    source = root / "planar-face-box.brep"
    _write_source(source)
    service = ProjectService.create_default(root / "config")
    project = service.create_project_from_source(root, "Planar Face Facing", source)
    project_root = project.root_path
    window, backend = _window(service)
    failures: list[BaseException] = []

    def dispose(candidate: MainWindow) -> None:
        candidate.cad_controller.shutdown()
        candidate.viewport.shutdown()
        candidate.hide()

    def finish(error: BaseException | None = None) -> None:
        if error is not None:
            failures.append(error)
        try:
            dispose(window)
        except RuntimeError:
            pass
        application.quit()

    def wait_ready(callback) -> None:
        if window.cad_controller.is_busy:
            QTimer.singleShot(25, lambda: wait_ready(callback))
            return
        try:
            callback()
        except BaseException as error:
            finish(error)

    def verify() -> None:
        workspace = window.cam_workspace
        workspace.create_job()
        workspace.create_setup()
        workspace.create_basic_resources()
        workspace.add_operation()
        mapping = window.cad_controller.persistent_object_map
        document_id = window.cad_controller.active_document_id
        assert mapping is not None and document_id is not None
        selection = SelectionMetadata(
            document_id, f"{document_id}:face:6", SelectionMode.FACE,
            window.cad_controller.active_metadata.bounding_box,
            next(iter(mapping.by_runtime)),
        )
        window.cad_controller._active_selection = (selection,)
        workspace.actions["pick"].trigger()
        assert workspace._picked_reference_resolved
        workspace.editor.boundary_source.setCurrentText(FacingBoundarySource.PLANAR_FACE.value)
        workspace.editor._facing_fields["target"].setText("40")
        workspace.editor._submit()
        operation = _operation(service)
        assert len(operation.geometry_inputs) == 1
        assert "ocp:" not in (operation.geometry_inputs[0].reference.subshape_selector or "")
        workspace.generate_selected()
        operation = _operation(service)
        assert operation.artifact_state.status is ArtifactStatus.VALID
        assert backend._toolpaths.get(operation.operation_id)
        workspace.actions["clear_pick"].trigger()
        assert len(_operation(service).geometry_inputs) == 1
        workspace.editor._submit()
        assert _operation(service).geometry_inputs == ()
        assert not workspace.actions["generate"].isEnabled()
        workspace.actions["pick"].trigger()
        workspace.editor._submit()
        assert len(_operation(service).geometry_inputs) == 1
        workspace.generate_selected()
        assert _operation(service).artifact_state.status is ArtifactStatus.VALID
        service.save()
        service.save_as(root, "Planar Face Facing Copy")
        screenshot = Path(tempfile.gettempdir()) / "hms_stage7b3_planar_face.png"
        assert application.primaryScreen().grabWindow(0).save(str(screenshot))
        logging.info("GUI verified: Bind/Rebind/Clear, persistent FACE, planar Generate, viewer and Save As")
        finish()

    QTimer.singleShot(100, lambda: wait_ready(verify))
    QTimer.singleShot(60_000, lambda: finish(TimeoutError("Stage 7B.3 GUI timeout"))
                      if not failures and window.isVisible() else None)
    application.exec()
    temporary.cleanup()
    if failures:
        raise failures[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
