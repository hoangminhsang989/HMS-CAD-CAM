"""Manual Windows GUI verification for the Stage 5C topology tree."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from OCP.BRep import BRep_Builder
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from OCP.StlAPI import StlAPI_Writer
from OCP.TopoDS import TopoDS_Compound
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hms_cadcam.cad.models import CadFormat, CadObjectKind
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.models import ObjectColor
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend

logger = logging.getLogger(__name__)


def _write_sources(root: Path) -> tuple[Path, Path, Path]:
    multi = root / "multi.brep"
    single = root / "single.brep"
    mesh = root / "mesh.stl"
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, BRepPrimAPI_MakeBox(30.0, 20.0, 10.0).Shape())
    builder.Add(compound, BRepPrimAPI_MakeBox(12.0, 9.0, 7.0).Shape())
    if not BRepTools.Write_s(compound, str(multi)):
        raise RuntimeError("Cannot write multi-solid BREP")
    single_shape = BRepPrimAPI_MakeBox(24.0, 16.0, 8.0).Shape()
    if not BRepTools.Write_s(single_shape, str(single)):
        raise RuntimeError("Cannot write single BREP")
    BRepMesh_IncrementalMesh(single_shape, 0.1)
    writer = StlAPI_Writer()
    writer.ASCIIMode = True
    if not writer.Write(single_shape, str(mesh)):
        raise RuntimeError("Cannot write STL")
    return multi, single, mesh


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    application = QApplication([])
    temporary = tempfile.TemporaryDirectory(prefix="hms_stage5c_")
    root = Path(temporary.name)
    sources = _write_sources(root)
    kernel = OcpCadKernel()
    backend = OcpCadViewportBackend(kernel)
    window = MainWindow(
        ProjectService.create_default(root / "config"),
        kernel,
        backend,
    )
    window.show()
    steps = iter(
        (
            (sources[0], CadFormat.BREP, 2, CadObjectKind.SOLID),
            (sources[1], CadFormat.BREP, 1, CadObjectKind.SOLID),
            (sources[2], CadFormat.STL, 1, CadObjectKind.MESH),
        )
    )
    failure: list[BaseException] = []

    def finish() -> None:
        window.close()
        application.quit()

    def verify(expected_count: int, expected_kind: CadObjectKind) -> None:
        if window.cad_controller.is_busy:
            QTimer.singleShot(20, lambda: verify(expected_count, expected_kind))
            return
        try:
            tree = window.cad_controller.active_tree
            if tree is None:
                raise AssertionError("Topology tree was not created")
            nodes = tree.presentation_nodes
            assert len(nodes) == expected_count
            assert all(node.kind is expected_kind for node in nodes)
            registry = backend._lifecycle.registry
            assert registry is not None
            assert len(registry.presentations) == expected_count
            if expected_count == 2:
                first = nodes[0]
                controller = window.cad_controller
                controller.select_tree_objects(tree.document_id, (first.object_id,))
                assert controller.set_object_visibility(
                    tree.document_id,
                    first.object_id,
                    False,
                )
                assert controller.set_object_visibility(
                    tree.document_id,
                    first.object_id,
                    True,
                )
                assert controller.set_object_color(
                    tree.document_id,
                    first.object_id,
                    ObjectColor(0.2, 0.55, 0.8),
                )
                assert controller.set_object_transparency(
                    tree.document_id,
                    first.object_id,
                    0.35,
                )
                assert controller.isolate_object(tree.document_id, first.object_id)
                assert controller.reset_isolate(tree.document_id)
            logger.info(
                "GUI verified: %s, nodes=%d, presentations=%d",
                expected_kind.value,
                len(nodes),
                len(registry.presentations),
            )
            start_next()
        except BaseException as error:
            failure.append(error)
            logger.exception("Stage 5C GUI verification failed")
            finish()

    def start_next() -> None:
        try:
            path, cad_format, expected_count, expected_kind = next(steps)
        except StopIteration:
            finish()
            return
        window.cad_controller.start_import(path, cad_format)
        QTimer.singleShot(20, lambda: verify(expected_count, expected_kind))

    QTimer.singleShot(100, start_next)
    application.exec()
    temporary.cleanup()
    if failure:
        raise failure[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
