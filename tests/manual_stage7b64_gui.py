"""Real Windows GUI verification for Stage 7B.6.4 Drilling UI."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from hms_cadcam.cam.domain import ArtifactStatus, DrillingCycle, DrillingStrategy
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend


def _write_source(path: Path) -> None:
    if not BRepTools.Write_s(BRepPrimAPI_MakeBox(60, 40, 20).Shape(), str(path)):
        raise RuntimeError("Cannot write Drilling BREP fixture")


def _find_item(item: QTreeWidgetItem, text: str) -> QTreeWidgetItem | None:
    if item.text(0) == text:
        return item
    for index in range(item.childCount()):
        found = _find_item(item.child(index), text)
        if found is not None:
            return found
    return None


def _operation(service: ProjectService):
    return next(
        operation
        for job in service.cam_snapshot.jobs
        for setup in job.setups
        for operation in setup.operation_tree.operations
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory(prefix="hms_stage7b64_")
    root = Path(temporary.name)
    source = root / "drilling-box.brep"
    _write_source(source)
    service = ProjectService.create_default(root / "config")
    service.create_project_from_source(root, "Drilling UI", source)
    kernel = OcpCadKernel()
    backend = OcpCadViewportBackend(kernel)
    window = MainWindow(service, kernel, backend)
    window.resize(1280, 760)
    window.show()
    failures: list[BaseException] = []

    def finish(error: BaseException | None = None) -> None:
        if error is not None:
            failures.append(error)
        try:
            window.cad_controller.shutdown()
            window.viewport.shutdown()
            window.hide()
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

    def select_vertex(index: int) -> None:
        mapping = window.cad_controller.persistent_object_map
        document_id = window.cad_controller.active_document_id
        metadata = window.cad_controller.active_metadata
        assert mapping is not None and document_id is not None and metadata is not None
        window.cad_controller._active_selection = (SelectionMetadata(
            document_id,
            f"{document_id}:vertex:{index}",
            SelectionMode.VERTEX,
            metadata.bounding_box,
            next(iter(mapping.by_runtime)),
        ),)

    def verify() -> None:
        workspace = window.cam_workspace
        workspace.create_job()
        workspace.create_setup()
        workspace.create_basic_resources()
        select_vertex(1)
        workspace.add_drilling_operation()
        operation = _operation(service)
        assert operation.strategy_key == "drilling_v1"
        assert len(operation.geometry_inputs) == 1

        workspace.editor.drilling_cycle.setCurrentText(DrillingCycle.PECK_DRILL.value)
        workspace.editor._drilling_fields["peck"].setText("1")
        workspace.editor._drilling_fields["dwell"].setText("0.2")
        workspace.editor._submit()
        strategy = DrillingStrategy.from_operation_parameters(_operation(service).parameters)
        assert strategy.cycle is DrillingCycle.PECK_DRILL
        workspace.generate_selected()
        operation = _operation(service)
        assert operation.artifact_state.status is ArtifactStatus.VALID
        assert backend._toolpaths.get(operation.operation_id)

        select_vertex(3)
        workspace.pick_geometry()
        workspace.editor._submit()
        assert _operation(service).geometry_inputs[0].reference != operation.geometry_inputs[0].reference
        workspace.clear_geometry_pick()
        assert _operation(service).geometry_inputs == ()
        workspace.pick_geometry()
        workspace.editor._submit()
        workspace.generate_selected()
        assert _operation(service).artifact_state.status is ArtifactStatus.VALID, (
            workspace.editor.error.text()
        )

        service.save()
        copied = service.save_as(root, "Drilling UI Copy")
        workspace.bind_project(copied)
        drilling_item = _find_item(workspace.tree.topLevelItem(0), "Drilling")
        assert drilling_item is not None
        workspace.tree.setCurrentItem(drilling_item)
        restored = _operation(service)
        assert restored.artifact_state.status is ArtifactStatus.VALID
        assert backend._toolpaths.get(restored.operation_id)
        screenshot = Path(tempfile.gettempdir()) / "hms_stage7b64_drilling_ui.png"
        assert application.primaryScreen().grabWindow(0).save(str(screenshot))
        logging.info("GUI verified: Drilling edit/bind/clear/generate/viewer/Save As")
        finish()

    QTimer.singleShot(100, lambda: wait_ready(verify))
    QTimer.singleShot(
        60_000,
        lambda: finish(TimeoutError("Stage 7B.6.4 GUI timeout"))
        if not failures and window.isVisible()
        else None,
    )
    application.exec()
    temporary.cleanup()
    if failures:
        raise failures[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
