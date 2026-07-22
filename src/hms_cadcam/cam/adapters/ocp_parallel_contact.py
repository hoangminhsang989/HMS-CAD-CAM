"""Original-BRep contact projection and surface-normal resolution for CAM 3D."""

from __future__ import annotations

import logging

from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepTools import BRepTools
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.GeomLProp import GeomLProp_SLProps
from OCP.TopAbs import TopAbs_Orientation, TopAbs_State
from OCP.TopoDS import TopoDS, TopoDS_Face
from OCP.gp import gp_Pnt, gp_Pnt2d

from hms_cadcam.cam.cam3d.models import CamSurfaceOrientation, CamSurfaceReference
from hms_cadcam.cam.cam3d.parallel import (
    ParallelFinishingError,
    ParallelResolvedContact,
)
from hms_cadcam.cam.domain import (
    DiagnosticCode,
    GeometryReferenceId,
    LengthUnit,
    Point3,
    Vector3,
)

logger = logging.getLogger(__name__)


class OcpParallelContactResolver:
    """Resolve mesh candidates back to trimmed source faces and exact normals."""

    def __init__(
        self,
        bindings: tuple[tuple[CamSurfaceReference, TopoDS_Face], ...],
    ) -> None:
        if not isinstance(bindings, tuple) or not bindings:
            raise TypeError("OCP Parallel contact bindings must not be empty")
        values: dict[
            GeometryReferenceId,
            tuple[TopoDS_Face, CamSurfaceOrientation],
        ] = {}
        for surface, face in bindings:
            if not isinstance(surface, CamSurfaceReference) or not isinstance(
                face, TopoDS_Face
            ):
                raise TypeError("OCP Parallel contact binding is invalid")
            source_id = surface.geometry.reference_id
            if source_id in values:
                raise ValueError("OCP Parallel contact source is duplicated")
            copied = TopoDS.Face_s(BRepBuilderAPI_Copy(face).Shape())
            values[source_id] = copied, surface.orientation
        self._bindings = values

    def __call__(
        self,
        source_surface_id: GeometryReferenceId,
        candidate: Point3,
        maximum_deviation_mm: float,
    ) -> ParallelResolvedContact:
        """Project one mesh candidate and evaluate the oriented BRep differential."""
        if not isinstance(source_surface_id, GeometryReferenceId):
            raise TypeError("OCP Parallel source ID is invalid")
        if not isinstance(candidate, Point3) or candidate.unit is not LengthUnit.MM:
            raise TypeError("OCP Parallel contact candidate is invalid")
        if (
            isinstance(maximum_deviation_mm, bool)
            or not isinstance(maximum_deviation_mm, (int, float))
            or maximum_deviation_mm <= 0.0
        ):
            raise ValueError("OCP Parallel maximum deviation is invalid")
        binding = self._bindings.get(source_surface_id)
        if binding is None:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_SOURCE_NORMAL_UNAVAILABLE,
                "Selected source face is unavailable for BRep normal resolution.",
            )
        face, selection_orientation = binding
        try:
            surface = BRep_Tool.Surface_s(face)
            u_min, u_max, v_min, v_max = BRepTools.UVBounds_s(face)
            numerical_tolerance = max(1.0e-10, maximum_deviation_mm * 1.0e-3)
            projector = GeomAPI_ProjectPointOnSurf(
                gp_Pnt(candidate.x, candidate.y, candidate.z),
                surface,
                u_min,
                u_max,
                v_min,
                v_max,
                numerical_tolerance,
            )
            if projector.NbPoints() <= 0:
                raise ParallelFinishingError(
                    DiagnosticCode.PARALLEL_SOURCE_NORMAL_UNAVAILABLE,
                    "Contact point could not be projected to its source BRep face.",
                )
            u_value, v_value = projector.LowerDistanceParameters()
            classifier = BRepClass_FaceClassifier(
                face,
                gp_Pnt2d(u_value, v_value),
                maximum_deviation_mm,
            )
            if classifier.State() not in {
                TopAbs_State.TopAbs_IN,
                TopAbs_State.TopAbs_ON,
            }:
                raise ParallelFinishingError(
                    DiagnosticCode.PARALLEL_SOURCE_NORMAL_UNAVAILABLE,
                    "Projected contact lies outside the trimmed source face.",
                )
            properties = GeomLProp_SLProps(
                surface,
                u_value,
                v_value,
                1,
                numerical_tolerance,
            )
            if not properties.IsNormalDefined():
                raise ParallelFinishingError(
                    DiagnosticCode.PARALLEL_SOURCE_NORMAL_UNAVAILABLE,
                    "Source BRep differential normal is undefined.",
                )
            native = properties.Normal()
            reverse = (
                face.Orientation() is TopAbs_Orientation.TopAbs_REVERSED
            ) != (selection_orientation is CamSurfaceOrientation.REVERSED)
            scale = -1.0 if reverse else 1.0
            normal = Vector3(
                native.X() * scale,
                native.Y() * scale,
                native.Z() * scale,
            )
            magnitude = normal.magnitude
            if magnitude <= numerical_tolerance:
                raise ParallelFinishingError(
                    DiagnosticCode.PARALLEL_SOURCE_NORMAL_UNAVAILABLE,
                    "Source BRep differential normal is degenerate.",
                )
            normal = Vector3(
                normal.x / magnitude,
                normal.y / magnitude,
                normal.z / magnitude,
            )
            projected = projector.NearestPoint()
            return ParallelResolvedContact(
                source_surface_id,
                Point3(*projected.Coord(), LengthUnit.MM),
                normal,
                projector.LowerDistance(),
            )
        except ParallelFinishingError:
            raise
        except Exception as error:
            logger.exception(
                "Unexpected OCP source-normal resolution failure for %s",
                source_surface_id,
            )
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_SOURCE_NORMAL_UNAVAILABLE,
                "Original source-surface normal resolution failed safely.",
            ) from error
