"""Real OCP writer/read-back validation for every native Stage15A format."""

from __future__ import annotations

from pathlib import Path
from threading import get_ident

import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Trsf, gp_Vec
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Compound
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow

from hms_cadcam.cad.export_models import (
    ExportEntityKind,
    ExportFormatId,
    ExportProfile,
    ExportSelectionRef,
    StlEncoding,
    StlMeshOptions,
)
from hms_cadcam.cad.export_service import (
    CadExportService,
    ExportErrorCode,
    ExportRequest,
)
from hms_cadcam.cad.models import CadFormat, CadGeometryKind
from hms_cadcam.cad.ocp import exporter as exporter_module
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cad_export import CadExportUiController
from hms_cadcam.ui.cad_export_status import (
    CadExportStatusSurface,
    ExportOperationState,
)


pytestmark = [pytest.mark.ocp, pytest.mark.filesystem]


_EXTENSIONS = {
    ExportFormatId.STEP: ".step",
    ExportFormatId.IGES: ".iges",
    ExportFormatId.STL: ".stl",
    ExportFormatId.BREP: ".brep",
}


def _read_back(kernel: OcpCadKernel, format_id: ExportFormatId, path: Path):
    reader = {
        ExportFormatId.STEP: kernel.import_step,
        ExportFormatId.IGES: kernel.import_iges,
        ExportFormatId.STL: kernel.import_stl,
        ExportFormatId.BREP: kernel.import_brep,
    }[format_id]
    result = reader(path)
    assert result.success, result.errors
    assert result.metadata is not None
    if format_id is ExportFormatId.STL:
        assert result.metadata.mesh_statistics is not None
        assert result.metadata.mesh_statistics.triangles > 0
    else:
        assert result.metadata.topology_counts is not None
        assert (
            result.metadata.topology_counts.solids
            + result.metadata.topology_counts.faces
            + result.metadata.topology_counts.edges
        ) > 0
    return result


@pytest.mark.parametrize("format_id", tuple(_EXTENSIONS))
def test_every_native_format_writes_nonempty_and_reads_back(
    tmp_path: Path, format_id: ExportFormatId
) -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(12.0, 8.0, 5.0)
    target = tmp_path / f"box{_EXTENSIONS[format_id]}"
    result = CadExportService.create_for_kernel(kernel).export(
        ExportRequest(document_id, target, ExportProfile.default_for(format_id))
    )
    assert result.success, result.failure
    assert target.stat().st_size == result.bytes_written > 0
    imported = _read_back(kernel, format_id, target)
    assert not tuple(tmp_path.glob("*.hms-exporting"))
    kernel.release_document(imported.document_id)
    kernel.release_document(document_id)


@pytest.mark.parametrize(
    ("format_id", "standard"),
    [
        (ExportFormatId.STEP, "AP203"),
        (ExportFormatId.STEP, "AP214"),
        (ExportFormatId.STEP, "AP242"),
        (ExportFormatId.BREP, "1"),
        (ExportFormatId.BREP, "2"),
        (ExportFormatId.BREP, "3"),
    ],
)
def test_versioned_writer_profiles_are_consumed_by_real_backend(
    tmp_path: Path, format_id: ExportFormatId, standard: str
) -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(4.0, 3.0, 2.0)
    target = tmp_path / f"version-{standard}{_EXTENSIONS[format_id]}"
    result = CadExportService.create_for_kernel(kernel).export(
        ExportRequest(document_id, target, ExportProfile(format_id, standard=standard))
    )
    assert result.success, result.failure
    imported = _read_back(kernel, format_id, target)
    kernel.release_document(imported.document_id)
    kernel.release_document(document_id)


@pytest.mark.parametrize(
    ("kind", "format_id"),
    [
        (ExportEntityKind.SOLID, ExportFormatId.STEP),
        (ExportEntityKind.FACE, ExportFormatId.IGES),
        (ExportEntityKind.WIRE, ExportFormatId.BREP),
        (ExportEntityKind.EDGE, ExportFormatId.BREP),
        (ExportEntityKind.FACE, ExportFormatId.STL),
    ],
)
def test_selected_brep_topology_exports_only_resolved_selection(
    tmp_path: Path,
    kind: ExportEntityKind,
    format_id: ExportFormatId,
) -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(9.0, 7.0, 3.0)
    object_id = kernel.get_document_tree(document_id).presentation_nodes[0].object_id
    selection = ExportSelectionRef(
        document_id,
        f"{document_id}:{kind.value}:1",
        kind,
        object_id,
    )
    target = tmp_path / f"selected-{kind.value}{_EXTENSIONS[format_id]}"
    result = CadExportService.create_for_kernel(kernel).export(
        ExportRequest(
            document_id,
            target,
            ExportProfile.default_for(format_id),
            (selection,),
        )
    )
    assert result.success, result.failure
    assert result.entity_count == 1
    imported = _read_back(kernel, format_id, target)
    kernel.release_document(imported.document_id)
    kernel.release_document(document_id)


def test_stale_selected_identity_is_typed_and_creates_no_file(tmp_path: Path) -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(2.0, 2.0, 2.0)
    target = tmp_path / "stale.step"
    selection = ExportSelectionRef(
        document_id,
        f"{document_id}:face:999999",
        ExportEntityKind.FACE,
    )
    result = CadExportService.create_for_kernel(kernel).export(
        ExportRequest(
            document_id,
            target,
            ExportProfile.default_for(ExportFormatId.STEP),
            (selection,),
        )
    )
    assert result.failure.code is ExportErrorCode.INVALID_SELECTION
    assert not target.exists()
    assert not tuple(tmp_path.glob("*.hms-exporting"))
    kernel.release_document(document_id)


@pytest.mark.parametrize("encoding", tuple(StlEncoding))
def test_imported_mesh_reexports_through_rwstl_without_brep_fabrication(
    tmp_path: Path, encoding: StlEncoding
) -> None:
    kernel = OcpCadKernel()
    service = CadExportService.create_for_kernel(kernel)
    box_id = kernel.create_box(5.0, 4.0, 3.0)
    source = tmp_path / "source.stl"
    assert service.export(
        ExportRequest(
            box_id,
            source,
            ExportProfile.default_for(ExportFormatId.STL),
        )
    ).success
    mesh = kernel.import_stl(source)
    assert mesh.success and mesh.document_id is not None
    profile = ExportProfile(
        ExportFormatId.STL,
        stl_encoding=encoding,
    )
    target = tmp_path / f"mesh-{encoding.value}.stl"
    exported = service.export(ExportRequest(mesh.document_id, target, profile))
    assert exported.success, exported.failure
    read_back = _read_back(kernel, ExportFormatId.STL, target)
    assert mesh.metadata is not None
    assert mesh.metadata.mesh_statistics is not None
    assert read_back.metadata is not None
    assert read_back.metadata.mesh_statistics is not None
    assert (
        read_back.metadata.mesh_statistics.triangles
        == mesh.metadata.mesh_statistics.triangles
    )
    kernel.release_document(read_back.document_id)
    kernel.release_document(mesh.document_id)
    kernel.release_document(box_id)


def test_existing_mesh_rejects_active_tessellation_profile_without_output(
    tmp_path: Path,
) -> None:
    kernel = OcpCadKernel()
    service = CadExportService.create_for_kernel(kernel)
    box_id = kernel.create_box(5.0, 4.0, 3.0)
    source = tmp_path / "mesh-source.stl"
    assert service.export(
        ExportRequest(
            box_id,
            source,
            ExportProfile.default_for(ExportFormatId.STL),
        )
    ).success
    mesh = kernel.import_stl(source)
    assert mesh.success and mesh.document_id is not None
    target = tmp_path / "must-not-silently-ignore.stl"
    result = service.export(
        ExportRequest(
            mesh.document_id,
            target,
            ExportProfile.default_for(ExportFormatId.STL),
        )
    )
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.INVALID_PROFILE
    assert "not applicable" in result.failure.message
    assert not target.exists()
    assert not tuple(tmp_path.glob("*.hms-exporting"))
    kernel.release_document(mesh.document_id)
    kernel.release_document(box_id)


def test_brep_stl_passes_all_tessellation_values_to_real_mesher_and_reads_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[float, bool, float, bool]] = []
    real_mesher = exporter_module.BRepMesh_IncrementalMesh

    def recording_mesher(shape, linear, relative, angular, parallel):
        observed.append((linear, relative, angular, parallel))
        return real_mesher(shape, linear, relative, angular, parallel)

    monkeypatch.setattr(
        exporter_module,
        "BRepMesh_IncrementalMesh",
        recording_mesher,
    )
    kernel = OcpCadKernel()
    document_id = kernel.create_box(7.0, 6.0, 5.0)
    options = StlMeshOptions(0.037, 0.23, True)
    profile = ExportProfile(
        ExportFormatId.STL,
        tolerance=options.linear_deflection,
        stl_encoding=StlEncoding.ASCII,
        mesh_options=options,
    )
    target = tmp_path / "brep-options-ascii.stl"
    result = CadExportService.create_for_kernel(kernel).export(
        ExportRequest(document_id, target, profile)
    )
    assert result.success, result.failure
    assert observed == [(0.037, True, 0.23, True)]
    imported = _read_back(kernel, ExportFormatId.STL, target)
    kernel.release_document(imported.document_id)
    kernel.release_document(document_id)


def test_moderate_real_step_export_runs_off_gui_thread_with_live_heartbeat(
    qtbot, tmp_path: Path
) -> None:
    kernel = OcpCadKernel()
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for row in range(5):
        for column in range(5):
            shape = BRepPrimAPI_MakeBox(8.0, 6.0, 4.0).Shape()
            transform = gp_Trsf()
            transform.SetTranslation(gp_Vec(column * 12.0, row * 10.0, 0.0))
            shape.Location(TopLoc_Location(transform))
            builder.Add(compound, shape)
    document_id = kernel._documents.add_brep(
        compound, CadFormat.GENERATED
    ).document_id

    native_service = CadExportService.create_for_kernel(kernel)
    native_backend = native_service._backend
    writer_thread_ids: list[int] = []

    class _RecordingRealBackend:
        supported_formats = native_backend.supported_formats
        unavailable_reason = native_backend.unavailable_reason

        def write(self, request: ExportRequest, temporary_path: Path):
            writer_thread_ids.append(get_ident())
            return native_backend.write(request, temporary_path)

    source = tmp_path / "moderate-source.step"
    source.write_bytes(b"workspace identity only")
    project_service = ProjectService.create_default(tmp_path / "config")
    project_service.commit_document_open(project_service.prepare_document_open(source))
    window = QMainWindow()
    qtbot.addWidget(window)
    controller = CadExportUiController(
        window,
        CadExportService(_RecordingRealBackend()),
        project_service,
        lambda: document_id,
        lambda: CadGeometryKind.BREP,
        lambda: (),
        lambda: True,
    )
    surface = CadExportStatusSurface(controller.cancel_active_export, window)
    controller.operation_state_changed.connect(surface.handle_export_event)
    heartbeat = [0]
    timer = QTimer()
    timer.setInterval(0)
    timer.timeout.connect(lambda: heartbeat.__setitem__(0, heartbeat[0] + 1))
    timer.start()
    gui_thread_id = get_ident()
    target = tmp_path / "moderate-real.step"
    try:
        assert controller._start_export(
            target,
            ExportProfile.default_for(ExportFormatId.STEP),
            selected=False,
        )
        qtbot.waitUntil(lambda: controller._active_task is None, timeout=15000)
        assert writer_thread_ids and writer_thread_ids[0] != gui_thread_id
        assert heartbeat[0] > 0
        assert surface.last_event is not None
        assert surface.last_event.state is ExportOperationState.SUCCEEDED
        assert target.stat().st_size > 0
        imported = _read_back(kernel, ExportFormatId.STEP, target)
        assert not tuple(tmp_path.glob("*.hms-exporting"))
        kernel.release_document(imported.document_id)
    finally:
        timer.stop()
        kernel.release_document(document_id)
