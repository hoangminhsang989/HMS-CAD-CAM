"""Native BREP and triangle-mesh readers used only by the OCP backend."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESControl import IGESControl_Reader
from OCP.Poly import Poly_Triangulation
from OCP.RWStl import RWStl
from OCP.STEPControl import STEPControl_Reader
from OCP.TopoDS import TopoDS_Shape

from hms_cadcam.cad.exceptions import CadImportError


@dataclass(frozen=True, slots=True)
class OcpImportPayload:
    """Native reader result that never leaves the private OCP implementation."""

    shape: TopoDS_Shape
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OcpMeshImportPayload:
    """Native mesh reader result kept behind the private OCP boundary."""

    triangulation: Poly_Triangulation
    warnings: tuple[str, ...] = ()


class OcpImporter:
    """Validate and translate supported CAD files without mutating their source."""

    def read_step(self, path: Path) -> OcpImportPayload:
        """Read and transfer all roots from a STEP/STP file."""
        self._validate_source(path, "STEP")
        reader = STEPControl_Reader()
        status = reader.ReadFile(str(path))
        if status != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise CadImportError(
                f"Cannot read STEP file; ReadFile status={status.name}."
            )
        roots = reader.NbRootsForTransfer()
        if roots <= 0:
            raise CadImportError("STEP file has no transferable roots")
        transferred = reader.TransferRoots()
        if transferred <= 0:
            raise CadImportError("STEP reader did not transfer any roots")
        shape = reader.OneShape()
        if shape.IsNull():
            raise CadImportError("STEP reader returned a null shape")
        warnings = ()
        if transferred < roots:
            warnings = (f"Transferred {transferred}/{roots} STEP roots",)
        return OcpImportPayload(shape=shape, warnings=warnings)

    def read_brep(self, path: Path) -> OcpImportPayload:
        """Read one native Open CASCADE BREP file."""
        self._validate_source(path, "BREP")
        shape = TopoDS_Shape()
        builder = BRep_Builder()
        if not BRepTools.Read_s(shape, str(path), builder):
            raise CadImportError("Cannot read BREP file")
        if shape.IsNull():
            raise CadImportError("BREP reader returned a null shape")
        return OcpImportPayload(shape=shape)

    def read_iges(self, path: Path) -> OcpImportPayload:
        """Read every transferable IGES root without requiring solid topology."""
        self._validate_source(path, "IGES")
        reader = IGESControl_Reader()
        status = reader.ReadFile(str(path))
        if status != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise CadImportError(
                f"Cannot read IGES file; ReadFile status={status.name}."
            )
        roots = reader.NbRootsForTransfer()
        if roots <= 0:
            raise CadImportError("IGES file has no transferable roots")
        transferred = reader.TransferRoots()
        if transferred <= 0:
            raise CadImportError("IGES reader did not transfer any roots")
        shape = reader.OneShape()
        if shape.IsNull():
            raise CadImportError("IGES reader returned a null shape")
        warnings = ()
        if transferred < roots:
            warnings = (f"Transferred {transferred}/{roots} IGES roots",)
        return OcpImportPayload(shape=shape, warnings=warnings)

    def read_stl(self, path: Path) -> OcpMeshImportPayload:
        """Read STL directly as triangulation, never as per-triangle BREP faces."""
        self._validate_source(path, "STL")
        triangulation = RWStl.ReadFile_s(str(path))
        if triangulation is None:
            raise CadImportError("Cannot read STL triangulation")
        if triangulation.NbNodes() <= 0 or triangulation.NbTriangles() <= 0:
            raise CadImportError("STL triangulation is empty")
        if not triangulation.HasNormals():
            triangulation.ComputeNormals()
        return OcpMeshImportPayload(triangulation=triangulation)

    @staticmethod
    def _validate_source(path: Path, format_name: str) -> None:
        if not path.is_file():
            raise CadImportError(f"CAD source file does not exist: {path}")
        if path.stat().st_size == 0:
            raise CadImportError(f"{format_name} source file is empty: {path}")
