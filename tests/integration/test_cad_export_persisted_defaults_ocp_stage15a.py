"""Real OCP certification driven by restarted persistent WP2 defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from hms_cadcam.cad.export_models import (
    ExportFormatId,
    ExportProfile,
    StlEncoding,
    StlMeshOptions,
)
from hms_cadcam.cad.export_service import CadExportService, ExportRequest
from hms_cadcam.cad.ocp import exporter as exporter_module
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.ui.settings.export_defaults import (
    ExportDefaultsSettingsService,
    factory_export_profiles,
)


pytestmark = [pytest.mark.ocp, pytest.mark.filesystem]


def _read_back(kernel: OcpCadKernel, format_id: ExportFormatId, path: Path):
    result = {
        ExportFormatId.STEP: kernel.import_step,
        ExportFormatId.IGES: kernel.import_iges,
        ExportFormatId.STL: kernel.import_stl,
        ExportFormatId.BREP: kernel.import_brep,
    }[format_id](path)
    assert result.success, result.errors
    assert result.document_id is not None
    return result


def test_restarted_persisted_profiles_drive_five_real_writer_readbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_path = tmp_path / "persisted-export.ini"
    instance_a = ExportDefaultsSettingsService(
        QSettings(str(settings_path), QSettings.Format.IniFormat)
    )
    profiles = factory_export_profiles()
    profiles[ExportFormatId.STEP] = ExportProfile(
        ExportFormatId.STEP,
        standard="AP242",
    )
    profiles[ExportFormatId.BREP] = ExportProfile(
        ExportFormatId.BREP,
        standard="3",
    )
    options = StlMeshOptions(0.037, 0.23, True)
    profiles[ExportFormatId.STL] = ExportProfile(
        ExportFormatId.STL,
        tolerance=0.037,
        stl_encoding=StlEncoding.ASCII,
        mesh_options=options,
    )
    instance_a.apply(profiles)
    del instance_a

    instance_b = ExportDefaultsSettingsService(
        QSettings(str(settings_path), QSettings.Format.IniFormat)
    )
    restarted = instance_b.load()
    assert restarted.issues == ()
    restored = restarted.profiles

    step_schemas: list[str] = []
    real_interface_static = exporter_module.Interface_Static

    def recording_schema(name: str, value: str):
        if name == "write.step.schema" and value == "AP242DIS":
            step_schemas.append(value)
        return real_interface_static.SetCVal_s(name, value)

    class RecordingInterfaceStatic:
        CVal_s = staticmethod(real_interface_static.CVal_s)
        SetCVal_s = staticmethod(recording_schema)

    brep_versions: list[object] = []
    real_brep_tools = exporter_module.BRepTools

    def recording_brep_write(*args):
        brep_versions.append(args[4])
        return real_brep_tools.Write_s(*args)

    class RecordingBRepTools:
        Write_s = staticmethod(recording_brep_write)

    tessellation: list[tuple[float, bool, float, bool]] = []
    real_mesher = exporter_module.BRepMesh_IncrementalMesh

    def recording_mesher(shape, linear, relative, angular, parallel):
        tessellation.append((linear, relative, angular, parallel))
        return real_mesher(shape, linear, relative, angular, parallel)

    monkeypatch.setattr(exporter_module, "Interface_Static", RecordingInterfaceStatic)
    monkeypatch.setattr(exporter_module, "BRepTools", RecordingBRepTools)
    monkeypatch.setattr(exporter_module, "BRepMesh_IncrementalMesh", recording_mesher)

    kernel = OcpCadKernel()
    service = CadExportService.create_for_kernel(kernel)
    document_id = kernel.create_box(11.0, 7.0, 4.0)
    imported_ids = []
    for format_id, suffix in (
        (ExportFormatId.STEP, ".step"),
        (ExportFormatId.IGES, ".iges"),
        (ExportFormatId.BREP, ".brep"),
        (ExportFormatId.STL, ".stl"),
    ):
        target = tmp_path / f"persisted-brep-{format_id.value}{suffix}"
        result = service.export(
            ExportRequest(document_id, target, restored[format_id])
        )
        assert result.success, result.failure
        imported = _read_back(kernel, format_id, target)
        imported_ids.append(imported.document_id)
        if format_id is ExportFormatId.STL:
            assert target.read_bytes().lstrip().startswith(b"solid")

    assert step_schemas == ["AP242DIS"]
    assert brep_versions == [
        exporter_module.TopTools_FormatVersion.TopTools_FormatVersion_VERSION_3
    ]
    assert tessellation == [(0.037, True, 0.23, True)]

    binary_source = tmp_path / "mesh-source-binary.stl"
    assert service.export(
        ExportRequest(
            document_id,
            binary_source,
            ExportProfile.default_for(ExportFormatId.STL),
        )
    ).success
    source_mesh = kernel.import_stl(binary_source)
    assert source_mesh.success and source_mesh.document_id is not None
    assert source_mesh.metadata is not None
    assert source_mesh.metadata.mesh_statistics is not None
    effective_mesh = ExportProfile(
        ExportFormatId.STL,
        stl_encoding=restored[ExportFormatId.STL].stl_encoding,
    )
    mesh_target = tmp_path / "persisted-existing-mesh.stl"
    mesh_result = service.export(
        ExportRequest(source_mesh.document_id, mesh_target, effective_mesh)
    )
    assert mesh_result.success, mesh_result.failure
    assert mesh_target.read_bytes().lstrip().startswith(b"solid")
    mesh_readback = _read_back(kernel, ExportFormatId.STL, mesh_target)
    imported_ids.append(mesh_readback.document_id)
    assert mesh_readback.metadata is not None
    assert mesh_readback.metadata.mesh_statistics is not None
    assert (
        mesh_readback.metadata.mesh_statistics.triangles
        == source_mesh.metadata.mesh_statistics.triangles
    )
    assert instance_b.load().profiles[ExportFormatId.STL] == restored[ExportFormatId.STL]
    assert instance_b.load().profiles[ExportFormatId.STL].mesh_options == options

    for imported_id in imported_ids:
        kernel.release_document(imported_id)
    kernel.release_document(source_mesh.document_id)
    kernel.release_document(document_id)
