"""Fail-closed OCP adapter for persistent drilling VERTEX/circular EDGE geometry."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from uuid import UUID

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_CurveType
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS, TopoDS_Shape

from hms_cadcam.cad.models import CadDocumentId, CadObjectId, XcafTransform
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cad.persistent_keys import PersistentCadObjectMap
from hms_cadcam.cam.adapters.ocp_contour import (
    _contains_shape,
    _shapes,
)
from hms_cadcam.cam.adapters.ocp_planar_face import (
    _canonical_normal,
    _container_digest,
    _occurrence_path,
    _unit_vector,
)
from hms_cadcam.cam.domain import (
    DiagnosticCode,
    DiagnosticSeverity,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionStatus,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
    HoleLocation,
    HoleReference,
    HoleSourceKind,
    Length,
    LengthUnit,
    OccurrenceTransformProvenance,
    PersistentHoleSelectorV1,
    Point3,
    ResolvedHoleLocation,
    Revision,
    ValidationDiagnostic,
    Vector3,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

_IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)
_TOLERANCE = 1.0e-7
logger = logging.getLogger(__name__)


class DrillingGeometryResolutionError(ValueError):
    """Stable binding failure at the native adapter boundary."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _HoleGeometry:
    position: Point3
    axis: Vector3
    plane_origin: Point3
    diameter: Length | None
    source_kind: HoleSourceKind
    digest: str
    source_normal_reversed: bool = False


class OcpDrillingGeometryResolver:
    """Bind and resolve one BREP VERTEX or one complete circular EDGE."""

    def __init__(
        self,
        kernel: OcpCadKernel,
        document_id: CadDocumentId,
        source_id: UUID,
        persistent_map: PersistentCadObjectMap,
        unit: LengthUnit,
        source_revision: Revision = Revision(0),
    ) -> None:
        if not isinstance(kernel, OcpCadKernel) or not isinstance(document_id, CadDocumentId):
            raise TypeError("OCP drilling resolver requires an active OCP document")
        if not isinstance(source_id, UUID) or not isinstance(
            persistent_map, PersistentCadObjectMap
        ):
            raise TypeError("OCP drilling resolver source context is invalid")
        if not isinstance(unit, LengthUnit) or unit is LengthUnit.UNKNOWN:
            raise ValueError("OCP drilling resolver requires an explicit project unit")
        self._kernel = kernel
        self._document_id = document_id
        self._source_id = source_id
        self._persistent_map = persistent_map
        self._unit = unit
        self._source_revision = source_revision

    def bind_selection(
        self,
        selection: SelectionMetadata,
        *,
        axis: Vector3 | None = None,
    ) -> HoleReference:
        """Bind a runtime pick while persisting no document/object/topology ID."""
        if not isinstance(selection, SelectionMetadata) or selection.topology not in {
            SelectionMode.VERTEX,
            SelectionMode.EDGE,
        }:
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "Select exactly one BREP VERTEX or circular EDGE",
            )
        if selection.document_id != self._document_id or selection.object_id is None:
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_SOURCE_MISMATCH,
                "Drilling selection does not belong to the active CAD document",
            )
        key = self._persistent_map.by_runtime.get(selection.object_id)
        if key is None or key.source_id != self._source_id:
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_GEOMETRY_AMBIGUOUS,
                "Drilling selection has no unique persistent container",
            )
        transform = self._transform_for_object(selection.object_id)
        if selection.topology is SelectionMode.VERTEX:
            if axis is None:
                raise DrillingGeometryResolutionError(
                    DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                    "A VERTEX drilling reference requires an explicit unit axis",
                )
            shape = self._runtime_shape(selection, TopAbs_ShapeEnum.TopAbs_VERTEX)
            geometry = _extract_vertex(TopoDS.Vertex_s(shape), self._unit, axis)
            selector_kind = "vertex"
            reference_kind = GeometryReferenceKind.VERTEX
        else:
            shape = self._runtime_shape(selection, TopAbs_ShapeEnum.TopAbs_EDGE)
            geometry = _extract_circular_edge(TopoDS.Edge_s(shape), self._unit)
            if axis is not None and axis.dot(geometry.axis) < 1.0 - _TOLERANCE:
                raise DrillingGeometryResolutionError(
                    DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                    "Declared drilling axis does not match the circular EDGE",
                )
            selector_kind = "circular_edge"
            reference_kind = GeometryReferenceKind.EDGE
        selector = PersistentHoleSelectorV1(
            _container_digest(key), selector_kind, geometry.digest
        )
        occurrence_path = _occurrence_path(key)
        fingerprint = _reference_fingerprint(
            selector, occurrence_path, transform.values
        )
        reference = GeometryReference(
            GeometryReferenceId.new(),
            HMS_GEOMETRY_REFERENCE_SCHEME,
            HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
            self._source_id,
            reference_kind,
            GeometryRepresentationKind.BREP,
            fingerprint,
            self._source_revision,
            occurrence_path=occurrence_path,
            subshape_selector=str(selector),
            hint=("Drilling VERTEX" if selector_kind == "vertex"
                  else "Drilling circular EDGE"),
            diagnostic_fallback=(
                ("container", selector.container_digest),
                ("geometry", selector.geometry_digest),
                ("source_kind", selector.source_kind),
            ),
        )
        return HoleReference(
            reference,
            geometry.axis,
            geometry.plane_origin,
            self._unit,
        )

    def resolve(self, hole_reference: HoleReference) -> ResolvedHoleLocation:
        """Resolve one reference to normalized public values only."""
        try:
            selector = self._validate_reference(hole_reference)
            reference = hole_reference.reference
            candidates = [
                (key, object_id)
                for key, object_id in self._persistent_map.by_persistent.items()
                if _container_digest(key) == selector.container_digest
                and key.source_id == reference.source_id
                and _occurrence_path(key) == reference.occurrence_path
            ]
            if not candidates:
                return _failure(
                    GeometryResolutionStatus.MISSING,
                    DiagnosticCode.DRILL_GEOMETRY_MISSING,
                    "Persistent drilling container no longer exists",
                )
            if len(candidates) != 1:
                return _failure(
                    GeometryResolutionStatus.AMBIGUOUS,
                    DiagnosticCode.DRILL_GEOMETRY_AMBIGUOUS,
                    "Persistent drilling container matches multiple occurrences",
                )
            _key, object_id = candidates[0]
            container = self._kernel._resolve_presentation_shapes(
                self._document_id
            ).get(object_id)
            if container is None:
                return _failure(
                    GeometryResolutionStatus.MISSING,
                    DiagnosticCode.DRILL_GEOMETRY_MISSING,
                    "Drilling CAD container is missing",
                )
            transform = self._transform_for_object(object_id)
            kind = (
                TopAbs_ShapeEnum.TopAbs_VERTEX
                if selector.source_kind == "vertex"
                else TopAbs_ShapeEnum.TopAbs_EDGE
            )
            matches: list[_HoleGeometry] = []
            for shape in _shapes(container, kind):
                try:
                    geometry = (
                        _extract_vertex(
                            TopoDS.Vertex_s(shape), self._unit, hole_reference.axis
                        )
                        if selector.source_kind == "vertex"
                        else _extract_circular_edge(TopoDS.Edge_s(shape), self._unit)
                    )
                except DrillingGeometryResolutionError:
                    continue
                if geometry.digest == selector.geometry_digest:
                    matches.append(geometry)
            if not matches:
                return _failure(
                    GeometryResolutionStatus.TOPOLOGY_CHANGED,
                    DiagnosticCode.DRILL_GEOMETRY_STALE,
                    "Drilling topology or geometry has changed",
                )
            if len(matches) != 1:
                return _failure(
                    GeometryResolutionStatus.AMBIGUOUS,
                    DiagnosticCode.DRILL_GEOMETRY_AMBIGUOUS,
                    "Persistent drilling selector matches multiple subshapes",
                )
            fingerprint = _reference_fingerprint(
                selector, reference.occurrence_path, transform.values
            )
            if fingerprint != reference.expected_geometry_fingerprint:
                return _failure(
                    GeometryResolutionStatus.TOPOLOGY_CHANGED,
                    DiagnosticCode.DRILL_GEOMETRY_STALE,
                    "Drilling geometry or occurrence transform has changed",
                )
            if reference.expected_source_revision != self._source_revision:
                return _failure(
                    GeometryResolutionStatus.STALE,
                    DiagnosticCode.DRILL_GEOMETRY_STALE,
                    "Drilling CAD source revision is stale",
                )
            geometry = matches[0]
            if (
                geometry.axis.dot(hole_reference.axis) < 1.0 - _TOLERANCE
                or _distance(geometry.plane_origin, hole_reference.plane_origin) > _TOLERANCE
            ):
                return _failure(
                    GeometryResolutionStatus.TOPOLOGY_CHANGED,
                    DiagnosticCode.DRILL_GEOMETRY_STALE,
                    "Bound drilling plane or axis has changed",
                )
            provenance = OccurrenceTransformProvenance(
                reference.occurrence_path,
                transform.values,
                geometry.source_normal_reversed,
            )
            location = HoleLocation(
                geometry.position,
                geometry.axis,
                geometry.plane_origin,
                geometry.diameter,
                self._unit,
                geometry.source_kind,
                hole_reference,
                provenance,
            )
            return ResolvedHoleLocation(GeometryResolutionStatus.RESOLVED, location)
        except DrillingGeometryResolutionError as error:
            status = {
                DiagnosticCode.DRILL_GEOMETRY_MISSING: GeometryResolutionStatus.MISSING,
                DiagnosticCode.DRILL_GEOMETRY_STALE: GeometryResolutionStatus.STALE,
                DiagnosticCode.DRILL_GEOMETRY_AMBIGUOUS: GeometryResolutionStatus.AMBIGUOUS,
                DiagnosticCode.DRILL_SOURCE_MISMATCH: GeometryResolutionStatus.SOURCE_MISMATCH,
            }.get(error.code, GeometryResolutionStatus.INVALID)
            return _failure(status, error.code, str(error))
        except Exception:
            logger.exception("Unexpected OCP drilling geometry resolution failure")
            return _failure(
                GeometryResolutionStatus.INVALID,
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "Drilling geometry could not be resolved safely",
            )

    def _validate_reference(
        self, hole_reference: HoleReference
    ) -> PersistentHoleSelectorV1:
        if not isinstance(hole_reference, HoleReference):
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "HoleReference is missing",
            )
        reference = hole_reference.reference
        if (
            reference.scheme != HMS_GEOMETRY_REFERENCE_SCHEME
            or reference.scheme_version != HMS_GEOMETRY_REFERENCE_SCHEME_VERSION
        ):
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "HoleReference scheme or version is unsupported",
            )
        if reference.source_id != self._source_id:
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_SOURCE_MISMATCH,
                "HoleReference belongs to another CAD source",
            )
        try:
            selector = PersistentHoleSelectorV1.parse(
                reference.subshape_selector or ""
            )
        except ValueError as error:
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "HoleReference selector is unsupported",
            ) from error
        expected = (
            GeometryReferenceKind.VERTEX
            if selector.source_kind == "vertex"
            else GeometryReferenceKind.EDGE
        )
        if (
            reference.kind is not expected
            or reference.geometry_kind is not GeometryRepresentationKind.BREP
        ):
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "HoleReference does not target supported BREP geometry",
            )
        return selector

    def _runtime_shape(
        self,
        selection: SelectionMetadata,
        shape_type: TopAbs_ShapeEnum,
    ) -> TopoDS_Shape:
        prefix = f"{self._document_id}:{selection.topology.value}:"
        if not selection.selection_id.startswith(prefix):
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_GEOMETRY_STALE,
                "Runtime drilling selector belongs to another document",
            )
        try:
            index = int(selection.selection_id[len(prefix):])
        except ValueError as error:
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_GEOMETRY_STALE,
                "Runtime drilling selector is invalid",
            ) from error
        values = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(self._kernel._resolve_shape(self._document_id), shape_type, values)
        if not 1 <= index <= values.Extent():
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_GEOMETRY_STALE,
                "Runtime drilling selector is stale",
            )
        shape = values.FindKey(index)
        container = self._kernel._resolve_presentation_shapes(
            self._document_id
        ).get(selection.object_id)
        if container is None or not _contains_shape(container, shape, shape_type):
            raise DrillingGeometryResolutionError(
                DiagnosticCode.DRILL_SOURCE_MISMATCH,
                "Drilling geometry is outside the selected occurrence",
            )
        return shape

    def _transform_for_object(self, object_id: CadObjectId) -> XcafTransform:
        node = self._kernel.get_document_tree(self._document_id).find(object_id)
        return (
            node.absolute_transform
            if node is not None and node.absolute_transform is not None
            else XcafTransform(_IDENTITY)
        )


def _extract_vertex(vertex, unit: LengthUnit, axis: Vector3) -> _HoleGeometry:
    try:
        normalized_axis = _unit_vector(axis)
    except ValueError as error:
        raise DrillingGeometryResolutionError(
            DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
            "VERTEX drilling axis is invalid",
        ) from error
    point = Point3(*BRep_Tool.Pnt_s(vertex).Coord(), unit)
    digest = _digest({
        "kind": "vertex",
        "position": _quantized_point(point),
    })
    return _HoleGeometry(
        point,
        normalized_axis,
        point,
        None,
        HoleSourceKind.BREP_VERTEX,
        digest,
    )


def _extract_circular_edge(edge, unit: LengthUnit) -> _HoleGeometry:
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    if (
        curve.GetType() != GeomAbs_CurveType.GeomAbs_Circle
        or not curve.IsClosed()
        or not math.isfinite(first)
        or not math.isfinite(last)
        or abs(abs(last - first) - math.tau) > _TOLERANCE
    ):
        raise DrillingGeometryResolutionError(
            DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
            "Drilling v1 requires one complete circular EDGE",
        )
    circle = curve.Circle()
    center = Point3(*circle.Location().Coord(), unit)
    raw_axis = _unit_vector(Vector3(*circle.Axis().Direction().Coord()))
    axis, reversed_axis = _canonical_normal(raw_axis)
    radius = circle.Radius()
    if not math.isfinite(radius) or radius <= _TOLERANCE:
        raise DrillingGeometryResolutionError(
            DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
            "Circular EDGE radius is invalid",
        )
    diameter = Length(2.0 * radius, unit)
    digest = _digest({
        "kind": "circular_edge",
        "center": _quantized_point(center),
        "axis": _quantized_axis(axis),
        "diameter": round(diameter.value / _TOLERANCE),
    })
    return _HoleGeometry(
        center,
        axis,
        center,
        diameter,
        HoleSourceKind.CIRCULAR_EDGE,
        digest,
        reversed_axis,
    )


def _failure(
    status: GeometryResolutionStatus,
    code: DiagnosticCode,
    message: str,
) -> ResolvedHoleLocation:
    return ResolvedHoleLocation(
        status,
        diagnostics=(ValidationDiagnostic(DiagnosticSeverity.ERROR, code, message),),
    )


def _reference_fingerprint(
    selector: PersistentHoleSelectorV1,
    occurrence_path: str | None,
    transform: tuple[float, ...],
) -> GeometryFingerprint:
    return GeometryFingerprint.from_payload({
        "selector": str(selector),
        "occurrence_path": occurrence_path,
        "absolute_transform": transform,
        "selector_version": 1,
    })


def _quantized_point(value: Point3) -> tuple[int, int, int]:
    return tuple(round(item / _TOLERANCE) for item in (value.x, value.y, value.z))


def _quantized_axis(value: Vector3) -> tuple[int, int, int]:
    return tuple(round(item / _TOLERANCE) for item in (value.x, value.y, value.z))


def _distance(first: Point3, second: Point3) -> float:
    return math.sqrt(
        (first.x - second.x) ** 2
        + (first.y - second.y) ** 2
        + (first.z - second.z) ** 2
    )


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()
