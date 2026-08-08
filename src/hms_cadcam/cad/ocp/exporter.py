"""Open CASCADE writers behind the typed CAD export service boundary."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESControl import IGESControl_Controller, IGESControl_Writer
from OCP.Interface import Interface_Static
from OCP.Message import Message_ProgressRange
from OCP.OSD import OSD_Path
from OCP.RWStl import RWStl
from OCP.STEPControl import (
    STEPControl_Controller,
    STEPControl_StepModelType,
    STEPControl_Writer,
)
from OCP.StlAPI import StlAPI_Writer
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_FormatVersion, TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape

from hms_cadcam.cad.export_models import (
    ExportEntityKind,
    ExportFormatId,
    ExportSelectionRef,
    StlEncoding,
)
from hms_cadcam.cad.export_service import (
    BackendWriteMetadata,
    CadExportDocumentError,
    CadExportProfileError,
    CadExportSelectionError,
    ExportRequest,
)
from hms_cadcam.cad.models import CadGeometryKind
from hms_cadcam.cad.ocp.kernel import OcpCadKernel


_WRITER_LOCK = RLock()
_TOPOLOGY = {
    ExportEntityKind.SOLID: TopAbs_ShapeEnum.TopAbs_SOLID,
    ExportEntityKind.FACE: TopAbs_ShapeEnum.TopAbs_FACE,
    ExportEntityKind.WIRE: TopAbs_ShapeEnum.TopAbs_WIRE,
    ExportEntityKind.EDGE: TopAbs_ShapeEnum.TopAbs_EDGE,
}
_STEP_STANDARDS = {
    "AP203": "AP203",
    "AP214": "AP214IS",
    "AP242": "AP242DIS",
}
_BREP_VERSIONS = {
    "1": TopTools_FormatVersion.TopTools_FormatVersion_VERSION_1,
    "2": TopTools_FormatVersion.TopTools_FormatVersion_VERSION_2,
    "3": TopTools_FormatVersion.TopTools_FormatVersion_VERSION_3,
}


class OcpCadExportBackend:
    """Resolve kernel-owned native data and invoke only real OCP writers."""

    def __init__(self, kernel: object) -> None:
        if not isinstance(kernel, OcpCadKernel):
            raise TypeError("OCP CAD export requires OcpCadKernel")
        self._kernel = kernel

    @property
    def supported_formats(self) -> frozenset[ExportFormatId]:
        return frozenset(
            {
                ExportFormatId.STEP,
                ExportFormatId.IGES,
                ExportFormatId.STL,
                ExportFormatId.BREP,
            }
        )

    @property
    def unavailable_reason(self) -> None:
        return None

    def write(
        self,
        request: ExportRequest,
        temporary_path: Path,
    ) -> BackendWriteMetadata:
        try:
            metadata = self._kernel.get_document_metadata(request.document_id)
        except KeyError as error:
            raise CadExportDocumentError("CAD document is stale or no longer open") from error
        if request.selections:
            if metadata.geometry_kind is not CadGeometryKind.BREP:
                raise CadExportSelectionError(
                    "Selected-object export is unavailable for mesh documents"
                )
            shape = self._selection_shape(request.selections)
            entity_count = len(request.selections)
            triangulation = None
        elif metadata.geometry_kind is CadGeometryKind.BREP:
            shape = self._kernel._resolve_shape(request.document_id)
            entity_count = 1
            triangulation = None
        else:
            shape = None
            entity_count = 1
            triangulation = self._kernel._resolve_triangulation(request.document_id)

        with _WRITER_LOCK:
            if request.profile.format_id is ExportFormatId.STEP:
                if shape is None:
                    raise ValueError("STEP export requires BREP geometry")
                self._write_step(shape, temporary_path, request.profile.standard)
                backend = "OCP STEPControl_Writer"
            elif request.profile.format_id is ExportFormatId.IGES:
                if shape is None:
                    raise ValueError("IGES export requires BREP geometry")
                self._write_iges(shape, temporary_path)
                backend = "OCP IGESControl_Writer"
            elif request.profile.format_id is ExportFormatId.BREP:
                if shape is None:
                    raise ValueError("BREP export requires BREP geometry")
                self._write_brep(shape, temporary_path, request.profile.standard)
                backend = "OCP BRepTools"
            elif request.profile.format_id is ExportFormatId.STL:
                if triangulation is not None:
                    if (
                        request.profile.mesh_options is not None
                        or request.profile.tolerance is not None
                    ):
                        raise CadExportProfileError(
                            "STL tessellation settings are not applicable to an "
                            "existing triangle mesh"
                        )
                    self._write_mesh_stl(
                        triangulation,
                        temporary_path,
                        request.profile.stl_encoding,
                    )
                    backend = "OCP RWStl"
                else:
                    assert shape is not None
                    if request.profile.mesh_options is None:
                        raise CadExportProfileError(
                            "BREP-to-STL export requires active tessellation settings"
                        )
                    self._write_brep_stl(shape, temporary_path, request)
                    backend = "OCP StlAPI_Writer"
            else:
                raise ValueError("No native OCP writer for the requested format")
        return BackendWriteMetadata(backend, entity_count)

    def _selection_shape(
        self,
        selections: tuple[ExportSelectionRef, ...],
    ) -> TopoDS_Shape:
        resolved = tuple(self._resolve_selection(item) for item in selections)
        if len(resolved) == 1:
            return resolved[0]
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        for shape in resolved:
            builder.Add(compound, shape)
        return compound

    def _resolve_selection(self, selection: ExportSelectionRef) -> TopoDS_Shape:
        topology = _TOPOLOGY.get(selection.entity_kind)
        if topology is None:
            raise CadExportSelectionError("Selection geometry kind is not exportable")
        prefix = (
            f"{selection.document_id}:{selection.entity_kind.value}:"
        )
        if not selection.selection_id.startswith(prefix):
            raise CadExportSelectionError(
                "Selection identity is stale or has the wrong topology"
            )
        try:
            index = int(selection.selection_id[len(prefix) :])
        except ValueError as error:
            raise CadExportSelectionError(
                "Selection identity has an invalid topology index"
            ) from error
        if index <= 0:
            raise CadExportSelectionError("Selection topology index must be positive")
        base = self._kernel._resolve_shape(selection.document_id)
        if selection.object_id is not None:
            shapes = self._kernel._resolve_presentation_shapes(selection.document_id)
            if selection.object_id not in shapes:
                raise CadExportSelectionError("Selected object no longer exists")
        indexed = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(base, topology, indexed)
        if index > indexed.Extent():
            raise CadExportSelectionError("Selection topology index is stale")
        shape = indexed.FindKey(index)
        if shape.IsNull():
            raise CadExportSelectionError("Selection resolved to a null shape")
        return shape

    @staticmethod
    def _write_step(shape: TopoDS_Shape, path: Path, standard: str | None) -> None:
        STEPControl_Controller.Init_s()
        schema = _STEP_STANDARDS.get(standard or "")
        if schema is None:
            raise ValueError("STEP profile standard is invalid")
        previous = Interface_Static.CVal_s("write.step.schema")
        if not Interface_Static.SetCVal_s("write.step.schema", schema):
            raise RuntimeError("OCP rejected the STEP schema")
        try:
            writer = STEPControl_Writer()
            transfer = writer.Transfer(
                shape,
                STEPControl_StepModelType.STEPControl_AsIs,
            )
            if transfer != IFSelect_ReturnStatus.IFSelect_RetDone:
                raise RuntimeError(f"STEP transfer failed: {transfer.name}")
            status = writer.Write(str(path))
            if status != IFSelect_ReturnStatus.IFSelect_RetDone:
                raise RuntimeError(f"STEP write failed: {status.name}")
        finally:
            Interface_Static.SetCVal_s("write.step.schema", previous)

    @staticmethod
    def _write_iges(shape: TopoDS_Shape, path: Path) -> None:
        IGESControl_Controller.Init_s()
        writer = IGESControl_Writer()
        if not writer.AddShape(shape):
            raise RuntimeError("IGES writer rejected the shape")
        writer.ComputeModel()
        if not writer.Write(str(path)):
            raise RuntimeError("IGES writer failed")

    @staticmethod
    def _write_brep(shape: TopoDS_Shape, path: Path, standard: str | None) -> None:
        version = _BREP_VERSIONS.get(standard or "")
        if version is None:
            raise ValueError("BREP profile version is invalid")
        if not BRepTools.Write_s(
            shape,
            str(path),
            False,
            False,
            version,
            Message_ProgressRange(),
        ):
            raise RuntimeError("BREP writer failed")

    @staticmethod
    def _write_brep_stl(
        shape: TopoDS_Shape,
        path: Path,
        request: ExportRequest,
    ) -> None:
        options = request.profile.mesh_options
        encoding = request.profile.stl_encoding
        if options is None or encoding is None:
            raise ValueError("STL writer options are missing")
        exported_shape = BRepBuilderAPI_Copy(shape, True, False).Shape()
        mesh = BRepMesh_IncrementalMesh(
            exported_shape,
            options.linear_deflection,
            options.relative,
            options.angular_deflection,
            True,
        )
        if not mesh.IsDone():
            raise RuntimeError("STL tessellation failed")
        writer = StlAPI_Writer()
        writer.ASCIIMode = encoding is StlEncoding.ASCII
        if not writer.Write(exported_shape, str(path)):
            raise RuntimeError("STL writer failed")

    @staticmethod
    def _write_mesh_stl(triangulation, path: Path, encoding: StlEncoding | None) -> None:
        if encoding is None:
            raise ValueError("STL encoding is missing")
        osd_path = OSD_Path(str(path))
        if encoding is StlEncoding.ASCII:
            written = RWStl.WriteAscii_s(triangulation, osd_path)
        else:
            written = RWStl.WriteBinary_s(triangulation, osd_path)
        if not written:
            raise RuntimeError("STL mesh writer failed")
