"""Open CASCADE implementation of read-only BREP measurement."""

from __future__ import annotations

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_CurveType
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS, TopoDS_Shape

from hms_cadcam.cad.measurement import (
    AreaMeasurement,
    BoundingDimensions,
    CircularEdgeMeasurement,
    DistanceMeasurement,
    EdgeLengthMeasurement,
    MeasurementResult,
    MeasurementValue,
    PointCoordinates,
    VolumeMeasurement,
)
from hms_cadcam.cad.models import CadDocumentId, CadGeometryKind
from hms_cadcam.cad.ocp.kernel import OcpCadKernel

_SELECTION_TYPES = {
    "vertex": TopAbs_ShapeEnum.TopAbs_VERTEX,
    "edge": TopAbs_ShapeEnum.TopAbs_EDGE,
    "face": TopAbs_ShapeEnum.TopAbs_FACE,
    "solid": TopAbs_ShapeEnum.TopAbs_SOLID,
}


class OcpMeasurementService:
    """Measure kernel-owned BREP shapes without exposing native objects."""

    def __init__(self, kernel: OcpCadKernel) -> None:
        self._kernel = kernel

    def measure_selection(
        self,
        document_id: CadDocumentId,
        selection_id: str,
    ) -> MeasurementResult:
        root = self._resolve_brep(document_id)
        topology, shape = self._resolve_selection(root, document_id, selection_id)
        values: list[MeasurementValue] = [self._bounding_dimensions(shape)]
        if topology == "vertex":
            point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(shape))
            values.insert(0, PointCoordinates(point.X(), point.Y(), point.Z()))
        elif topology == "edge":
            properties = GProp_GProps()
            BRepGProp.LinearProperties_s(shape, properties)
            values.insert(0, EdgeLengthMeasurement(abs(properties.Mass())))
            curve = BRepAdaptor_Curve(TopoDS.Edge_s(shape))
            if curve.GetType() == GeomAbs_CurveType.GeomAbs_Circle:
                radius = curve.Circle().Radius()
                values.insert(
                    1,
                    CircularEdgeMeasurement(radius, 2.0 * radius, curve.IsClosed()),
                )
        elif topology == "face":
            properties = GProp_GProps()
            BRepGProp.SurfaceProperties_s(shape, properties)
            values.insert(0, AreaMeasurement(abs(properties.Mass())))
        elif topology == "solid":
            properties = GProp_GProps()
            BRepGProp.VolumeProperties_s(shape, properties)
            values.insert(0, VolumeMeasurement(abs(properties.Mass())))
        return MeasurementResult(document_id, (selection_id,), tuple(values))

    def measure_distance(
        self,
        document_id: CadDocumentId,
        first_selection_id: str,
        second_selection_id: str,
    ) -> MeasurementResult:
        root = self._resolve_brep(document_id)
        first_type, first = self._resolve_selection(
            root, document_id, first_selection_id
        )
        second_type, second = self._resolve_selection(
            root, document_id, second_selection_id
        )
        if first_type != "vertex" or second_type != "vertex":
            raise ValueError("Point-to-point distance requires two BREP vertices")
        first_point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(first))
        second_point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(second))
        return MeasurementResult(
            document_id,
            (first_selection_id, second_selection_id),
            (DistanceMeasurement(first_point.Distance(second_point)),),
        )

    def measure_document(self, document_id: CadDocumentId) -> MeasurementResult:
        shape = self._resolve_brep(document_id)
        return MeasurementResult(
            document_id,
            (),
            (self._bounding_dimensions(shape),),
        )

    def _resolve_brep(self, document_id: CadDocumentId) -> TopoDS_Shape:
        metadata = self._kernel.get_document_metadata(document_id)
        if metadata.geometry_kind is not CadGeometryKind.BREP:
            raise TypeError("Measurement is only available for BREP documents")
        return self._kernel._resolve_shape(document_id)

    @staticmethod
    def _resolve_selection(
        root: TopoDS_Shape,
        document_id: CadDocumentId,
        selection_id: str,
    ) -> tuple[str, TopoDS_Shape]:
        try:
            selection_document, topology, raw_index = selection_id.rsplit(":", 2)
            index = int(raw_index)
            shape_type = _SELECTION_TYPES[topology]
        except (KeyError, ValueError) as error:
            raise ValueError(f"Invalid BREP selection ID: {selection_id}") from error
        if selection_document != str(document_id):
            raise ValueError("Selection does not belong to the requested document")
        shapes = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(root, shape_type, shapes)
        if index <= 0 or index > shapes.Extent():
            raise ValueError(f"BREP selection index is out of range: {index}")
        return topology, shapes.FindKey(index)

    @staticmethod
    def _bounding_dimensions(shape: TopoDS_Shape) -> BoundingDimensions:
        bounds = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bounds, False, False)
        x_min, y_min, z_min, x_max, y_max, z_max = bounds.Get()
        return BoundingDimensions(
            x_max - x_min,
            y_max - y_min,
            z_max - z_min,
        )
