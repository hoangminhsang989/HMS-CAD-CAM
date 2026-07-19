"""Fail-closed OCP adapter for persistent 2D Contour FACE/WIRE profiles."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from uuid import UUID

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
from OCP.TopAbs import TopAbs_Orientation, TopAbs_ShapeEnum
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape, TopoDS_Wire

from hms_cadcam.cad.models import CadDocumentId, CadObjectId, XcafTransform
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    PersistentCadObjectMap,
    PersistentObjectKey,
    PersistentXcafOccurrenceKey,
)
from hms_cadcam.cam.domain import (
    ContourBounds,
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourProfileDescriptor,
    ContourProfileSource,
    ContourSegment,
    DiagnosticCode,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionStatus,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
    LengthUnit,
    OccurrenceTransformProvenance,
    PersistentProfileSelectorV1,
    Point3,
    ProfileProvenance,
    ResolvedContourProfile,
    Revision,
    Vector3,
)
from hms_cadcam.cam.adapters.ocp_planar_face import (
    _canonical_normal,
    _canonical_x_axis,
    _container_digest,
    _inverse_transform_point,
    _occurrence_path,
    _unit_vector,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

_IDENTITY = (1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0)
_TOLERANCE = 1.0e-7
logger = logging.getLogger(__name__)


class ContourProfileResolutionError(ValueError):
    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _ProfileGeometry:
    origin: Point3
    x_axis: Vector3
    y_axis: Vector3
    normal: Vector3
    loop: ContourLoop
    bounds: ContourBounds
    digest: str
    normal_reversed: bool


class OcpContourProfileResolver:
    """Bind and resolve a planar FACE outer wire or explicitly selected closed WIRE."""

    def __init__(self, kernel: OcpCadKernel, document_id: CadDocumentId,
                 source_id: UUID, persistent_map: PersistentCadObjectMap,
                 unit: LengthUnit, source_revision: Revision = Revision(0)) -> None:
        if not isinstance(kernel, OcpCadKernel) or not isinstance(document_id, CadDocumentId):
            raise TypeError("OCP Contour resolver requires an active OCP document")
        if not isinstance(source_id, UUID) or not isinstance(persistent_map, PersistentCadObjectMap):
            raise TypeError("OCP Contour resolver source context is invalid")
        if not isinstance(unit, LengthUnit) or unit is LengthUnit.UNKNOWN:
            raise ValueError("OCP Contour resolver requires an explicit project unit")
        self._kernel = kernel
        self._document_id = document_id
        self._source_id = source_id
        self._persistent_map = persistent_map
        self._unit = unit
        self._source_revision = source_revision

    def bind_selection(self, selection: SelectionMetadata) -> GeometryReference:
        """Create one persistent profile reference without storing runtime topology IDs."""
        if not isinstance(selection, SelectionMetadata) or selection.topology not in {SelectionMode.FACE, SelectionMode.WIRE}:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_PROFILE_MISSING,
                                                "Hãy chọn đúng một planar FACE hoặc closed WIRE.")
        if selection.document_id != self._document_id or selection.object_id is None:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                                                "Profile selection không thuộc CAD document hiện hành.")
        key = self._persistent_map.by_runtime.get(selection.object_id)
        if key is None or key.source_id != self._source_id:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_PROFILE_AMBIGUOUS,
                                                "Profile selection không có persistent container duy nhất.")
        transform = self._transform_for_object(selection.object_id)
        if selection.topology is SelectionMode.FACE:
            face = self._runtime_shape(selection, TopAbs_ShapeEnum.TopAbs_FACE)
            geometry = _extract_face_profile(TopoDS.Face_s(face), self._unit, transform)
            source = "face"
            reference_kind = GeometryReferenceKind.FACE
            profile_source = ContourProfileSource.PLANAR_FACE_OUTER
        else:
            wire = self._runtime_shape(selection, TopAbs_ShapeEnum.TopAbs_WIRE)
            geometry = _extract_wire_profile(TopoDS.Wire_s(wire), self._unit, transform)
            source = "wire"
            reference_kind = GeometryReferenceKind.SKETCH_OR_PROFILE
            profile_source = ContourProfileSource.CLOSED_WIRE
        selector = PersistentProfileSelectorV1(_container_digest(key), source, geometry.digest)
        occurrence_path = _occurrence_path(key)
        fingerprint = _reference_fingerprint(selector, occurrence_path, transform.values)
        return GeometryReference(
            GeometryReferenceId.new(), HMS_GEOMETRY_REFERENCE_SCHEME,
            HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, self._source_id, reference_kind,
            GeometryRepresentationKind.BREP, fingerprint, self._source_revision,
            occurrence_path=occurrence_path, subshape_selector=str(selector),
            hint=("Planar FACE outer profile" if profile_source is ContourProfileSource.PLANAR_FACE_OUTER
                  else "Closed WIRE profile"),
            diagnostic_fallback=(("container", selector.container_digest),
                                 ("profile", selector.profile_digest),
                                 ("source_kind", selector.source_kind)),
        )

    def resolve(self, reference: GeometryReference) -> ResolvedContourProfile:
        """Resolve a persistent reference to a verified OCP-free exact LINE/ARC descriptor."""
        try:
            selector = self._validate_reference(reference)
            candidates = [(key, object_id) for key, object_id in self._persistent_map.by_persistent.items()
                          if _container_digest(key) == selector.container_digest and
                          key.source_id == reference.source_id and
                          _occurrence_path(key) == reference.occurrence_path]
            if not candidates:
                return _failure(GeometryResolutionStatus.MISSING, DiagnosticCode.CONTOUR_PROFILE_MISSING,
                                "Không tìm thấy persistent occurrence/container của profile.")
            if len(candidates) != 1:
                return _failure(GeometryResolutionStatus.AMBIGUOUS, DiagnosticCode.CONTOUR_PROFILE_AMBIGUOUS,
                                "Persistent profile container khớp nhiều occurrence.")
            _key, object_id = candidates[0]
            container = self._kernel._resolve_presentation_shapes(self._document_id).get(object_id)
            if container is None:
                return _failure(GeometryResolutionStatus.MISSING, DiagnosticCode.CONTOUR_PROFILE_MISSING,
                                "CAD container của profile không còn tồn tại.")
            transform = self._transform_for_object(object_id)
            matches: list[_ProfileGeometry] = []
            shapes = _shapes(container, TopAbs_ShapeEnum.TopAbs_FACE if selector.source_kind == "face"
                             else TopAbs_ShapeEnum.TopAbs_WIRE)
            for shape in shapes:
                try:
                    geometry = (_extract_face_profile(TopoDS.Face_s(shape), self._unit, transform)
                                if selector.source_kind == "face" else
                                _extract_wire_profile(TopoDS.Wire_s(shape), self._unit, transform))
                except ContourProfileResolutionError:
                    continue
                if geometry.digest == selector.profile_digest:
                    matches.append(geometry)
            if not matches:
                return _failure(GeometryResolutionStatus.TOPOLOGY_CHANGED, DiagnosticCode.CONTOUR_TOPOLOGY_CHANGED,
                                "Topology hoặc geometry của profile đã thay đổi.")
            if len(matches) != 1:
                return _failure(GeometryResolutionStatus.AMBIGUOUS, DiagnosticCode.CONTOUR_PROFILE_AMBIGUOUS,
                                "Persistent selector khớp nhiều profile.")
            fingerprint = _reference_fingerprint(selector, reference.occurrence_path, transform.values)
            if fingerprint != reference.expected_geometry_fingerprint:
                return _failure(GeometryResolutionStatus.TOPOLOGY_CHANGED, DiagnosticCode.CONTOUR_TOPOLOGY_CHANGED,
                                "Topology hoặc occurrence transform của profile đã thay đổi.")
            if reference.expected_source_revision != self._source_revision:
                return _failure(GeometryResolutionStatus.STALE, DiagnosticCode.CONTOUR_PROFILE_STALE,
                                "Revision CAD source của profile đã stale.")
            geometry = matches[0]
            source_kind = (ContourProfileSource.PLANAR_FACE_OUTER if selector.source_kind == "face"
                           else ContourProfileSource.CLOSED_WIRE)
            occurrence = OccurrenceTransformProvenance(reference.occurrence_path, transform.values,
                                                       geometry.normal_reversed)
            provenance = ProfileProvenance(source_kind, occurrence)
            descriptor = ContourProfileDescriptor(reference, geometry.origin, geometry.x_axis,
                geometry.y_axis, geometry.normal, geometry.loop, (), geometry.bounds, self._unit,
                fingerprint, provenance)
            return ResolvedContourProfile(GeometryResolutionStatus.RESOLVED, descriptor)
        except ContourProfileResolutionError as error:
            status = {
                DiagnosticCode.CONTOUR_PROFILE_MISSING: GeometryResolutionStatus.MISSING,
                DiagnosticCode.CONTOUR_PROFILE_STALE: GeometryResolutionStatus.STALE,
                DiagnosticCode.CONTOUR_PROFILE_AMBIGUOUS: GeometryResolutionStatus.AMBIGUOUS,
                DiagnosticCode.CONTOUR_SOURCE_MISMATCH: GeometryResolutionStatus.SOURCE_MISMATCH,
                DiagnosticCode.CONTOUR_TOPOLOGY_CHANGED: GeometryResolutionStatus.TOPOLOGY_CHANGED,
            }.get(error.code, GeometryResolutionStatus.INVALID)
            return _failure(status, error.code, str(error))
        except Exception:
            logger.exception("Unexpected OCP Contour profile resolution failure")
            return _failure(GeometryResolutionStatus.INVALID, DiagnosticCode.CONTOUR_PROFILE_MISSING,
                            "Không thể resolve 2D Contour profile an toàn.")

    def _validate_reference(self, reference: GeometryReference) -> PersistentProfileSelectorV1:
        if not isinstance(reference, GeometryReference):
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_PROFILE_MISSING,
                                                "GeometryReference profile bị thiếu.")
        if reference.scheme != HMS_GEOMETRY_REFERENCE_SCHEME or reference.scheme_version != HMS_GEOMETRY_REFERENCE_SCHEME_VERSION:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_PROFILE_MISSING,
                                                "GeometryReference profile dùng scheme/version không hỗ trợ.")
        if reference.source_id != self._source_id:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                                                "GeometryReference profile thuộc CAD source khác.")
        selector = PersistentProfileSelectorV1.parse(reference.subshape_selector or "")
        expected = (GeometryReferenceKind.FACE if selector.source_kind == "face"
                    else GeometryReferenceKind.SKETCH_OR_PROFILE)
        if reference.kind is not expected or reference.geometry_kind is not GeometryRepresentationKind.BREP:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                                                "GeometryReference không trỏ tới BREP profile phù hợp.")
        return selector

    def _runtime_shape(self, selection: SelectionMetadata, shape_type: TopAbs_ShapeEnum) -> TopoDS_Shape:
        prefix = f"{self._document_id}:{selection.topology.value}:"
        if not selection.selection_id.startswith(prefix):
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_PROFILE_STALE,
                                                "Runtime profile selector không thuộc document hiện hành.")
        try:
            index = int(selection.selection_id[len(prefix):])
        except ValueError as error:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_PROFILE_STALE,
                                                "Runtime profile selector không hợp lệ.") from error
        values = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(self._kernel._resolve_shape(self._document_id), shape_type, values)
        if not 1 <= index <= values.Extent():
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_PROFILE_STALE,
                                                "Runtime profile selector đã stale.")
        shape = values.FindKey(index)
        container = self._kernel._resolve_presentation_shapes(self._document_id).get(selection.object_id)
        if container is None or not _contains_shape(container, shape, shape_type):
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                                                "Profile không thuộc occurrence/container đã chọn.")
        return shape

    def _transform_for_object(self, object_id: CadObjectId) -> XcafTransform:
        node = self._kernel.get_document_tree(self._document_id).find(object_id)
        return node.absolute_transform if node is not None and node.absolute_transform is not None else XcafTransform(_IDENTITY)


def _failure(status: GeometryResolutionStatus, code: DiagnosticCode, message: str) -> ResolvedContourProfile:
    return ResolvedContourProfile(status, message=message, diagnostic_code=code)


def _shapes(container: TopoDS_Shape, kind: TopAbs_ShapeEnum) -> tuple[TopoDS_Shape, ...]:
    values = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(container, kind, values)
    return tuple(values.FindKey(index) for index in range(1, values.Extent() + 1))


def _contains_shape(container: TopoDS_Shape, shape: TopoDS_Shape, kind: TopAbs_ShapeEnum) -> bool:
    values = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(container, kind, values)
    return values.FindIndex(shape) > 0


def _extract_face_profile(face: TopoDS_Face, unit: LengthUnit, transform: XcafTransform) -> _ProfileGeometry:
    surface = BRepAdaptor_Surface(face, True)
    if surface.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
        raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_NON_PLANAR_PROFILE,
                                            "FACE profile không phẳng.")
    wires = _shapes(face, TopAbs_ShapeEnum.TopAbs_WIRE)
    outer = BRepTools.OuterWire_s(face)
    if outer.IsNull():
        raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_OPEN_PROFILE,
                                            "FACE không có outer wire kín.")
    if sum(1 for wire in wires if not wire.IsSame(outer)):
        raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_UNSUPPORTED_INNER_LOOPS,
                                            "2D Contour v1 không hỗ trợ inner loop/island.")
    return _extract_profile(outer, face, unit, transform)


def _extract_wire_profile(wire: TopoDS_Wire, unit: LengthUnit, transform: XcafTransform) -> _ProfileGeometry:
    if not wire.Closed():
        raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_OPEN_PROFILE,
                                            "WIRE profile chưa đóng.")
    builder = BRepBuilderAPI_MakeFace(wire, True)
    if not builder.IsDone():
        raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_NON_PLANAR_PROFILE,
                                            "WIRE profile không tạo được planar support.")
    face = builder.Face()
    surface = BRepAdaptor_Surface(face, True)
    if surface.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
        raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_NON_PLANAR_PROFILE,
                                            "WIRE profile không phẳng.")
    return _extract_profile(wire, face, unit, transform)


def _extract_profile(wire: TopoDS_Wire, face: TopoDS_Face, unit: LengthUnit,
                     transform: XcafTransform) -> _ProfileGeometry:
    surface = BRepAdaptor_Surface(face, True)
    plane = surface.Plane()
    origin = Point3(*plane.Location().Coord(), unit)
    raw_normal = _unit_vector(Vector3(*plane.Axis().Direction().Coord()))
    normal, reversed_normal = _canonical_normal(raw_normal)
    x_axis = _canonical_x_axis(normal)
    y_axis = _unit_vector(normal.cross(x_axis))
    segments: list[ContourSegment] = []
    explorer = BRepTools_WireExplorer(wire, face)
    while explorer.More():
        edge = TopoDS.Edge_s(explorer.Current())
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        if not math.isfinite(first) or not math.isfinite(last) or last <= first:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_OPEN_PROFILE,
                                                "Profile edge có parameter range không hợp lệ.")
        reversed_edge = explorer.Orientation() == TopAbs_Orientation.TopAbs_REVERSED
        kind = curve.GetType()
        if kind == GeomAbs_CurveType.GeomAbs_Line:
            start, end = Point3(*curve.Value(first).Coord(), unit), Point3(*curve.Value(last).Coord(), unit)
            if reversed_edge:
                start, end = end, start
            segments.append(ContourSegment(ContourCurveKind.LINE, start, end))
        elif kind == GeomAbs_CurveType.GeomAbs_Circle:
            circle = curve.Circle()
            center = Point3(*circle.Location().Coord(), unit)
            axis = _unit_vector(Vector3(*circle.Axis().Direction().Coord()))
            sweep = (last - first) * (1.0 if axis.dot(normal) >= 0.0 else -1.0)
            start, end = Point3(*curve.Value(first).Coord(), unit), Point3(*curve.Value(last).Coord(), unit)
            if reversed_edge:
                start, end, sweep = end, start, -sweep
            if abs(sweep) >= math.tau - _TOLERANCE:
                middle = Point3(*curve.Value((first + last) / 2.0).Coord(), unit)
                if reversed_edge:
                    segments.extend((ContourSegment(ContourCurveKind.ARC, start, middle, center, sweep / 2.0),
                                     ContourSegment(ContourCurveKind.ARC, middle, end, center, sweep / 2.0)))
                else:
                    segments.extend((ContourSegment(ContourCurveKind.ARC, start, middle, center, sweep / 2.0),
                                     ContourSegment(ContourCurveKind.ARC, middle, end, center, sweep / 2.0)))
            else:
                segments.append(ContourSegment(ContourCurveKind.ARC, start, end, center, sweep))
        else:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_UNSUPPORTED_CURVE,
                                                "2D Contour v1 chỉ hỗ trợ LINE và circular ARC.")
        explorer.Next()
    if len(segments) < 2:
        raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_OPEN_PROFILE,
                                            "Profile không có đủ segment.")
    for current, following in zip(segments, (*segments[1:], segments[0]), strict=True):
        if _distance(current.end, following.start) > _TOLERANCE:
            raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_OPEN_PROFILE,
                                                "Profile edge chain bị hở hoặc không liên tục.")
    points = _sample(tuple(segments))
    plane_points = tuple(_plane_xy(point, origin, x_axis, y_axis) for point in points)
    if _self_intersects(plane_points):
        raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_SELF_INTERSECTION,
                                            "Profile bị tự giao.")
    area = _area(plane_points)
    if abs(area) <= _TOLERANCE:
        raise ContourProfileResolutionError(DiagnosticCode.CONTOUR_SELF_INTERSECTION,
                                            "Profile có diện tích bằng zero.")
    orientation = ContourOrientation.COUNTERCLOCKWISE if area > 0.0 else ContourOrientation.CLOCKWISE
    loop = ContourLoop(tuple(segments), orientation)
    if loop.orientation is ContourOrientation.CLOCKWISE:
        loop = loop.reversed()
    loop = _rotate_loop(loop)
    points = _sample(loop.segments)
    minimum = Point3(min(point.x for point in points), min(point.y for point in points),
                     min(point.z for point in points), unit)
    maximum = Point3(max(point.x for point in points), max(point.y for point in points),
                     max(point.z for point in points), unit)
    local_segments = tuple(_local_segment(segment, transform) for segment in loop.segments)
    digest = _digest({"format": "hms_contour_profile_geometry_v1",
                      "segments": [_segment_payload(segment) for segment in local_segments]})
    return _ProfileGeometry(origin, x_axis, y_axis, normal, loop,
                            ContourBounds(minimum, maximum), digest, reversed_normal)


def _rotate_loop(loop: ContourLoop) -> ContourLoop:
    index = min(range(len(loop.segments)), key=lambda value: _quantized(loop.segments[value].start))
    return ContourLoop((*loop.segments[index:], *loop.segments[:index]), loop.orientation)


def _local_segment(segment: ContourSegment, transform: XcafTransform) -> ContourSegment:
    return ContourSegment(segment.kind, _inverse_transform_point(segment.start, transform),
                          _inverse_transform_point(segment.end, transform),
                          None if segment.center is None else _inverse_transform_point(segment.center, transform),
                          segment.sweep_radians)


def _segment_payload(segment: ContourSegment) -> dict[str, object]:
    return {"kind": segment.kind.value, "start": _quantized(segment.start), "end": _quantized(segment.end),
            "center": None if segment.center is None else _quantized(segment.center),
            "sweep": None if segment.sweep_radians is None else round(segment.sweep_radians, 12)}


def _sample(segments: tuple[ContourSegment, ...]) -> tuple[Point3, ...]:
    values: list[Point3] = []
    for segment in segments:
        if not values:
            values.append(segment.start)
        if segment.kind is ContourCurveKind.LINE:
            values.append(segment.end)
        else:
            assert segment.center is not None and segment.sweep_radians is not None and segment.radius is not None
            start = math.atan2(segment.start.y - segment.center.y, segment.start.x - segment.center.x)
            count = max(2, math.ceil(abs(segment.sweep_radians) / math.radians(5.0)))
            values.extend(Point3(segment.center.x + segment.radius * math.cos(start + segment.sweep_radians * index / count),
                                 segment.center.y + segment.radius * math.sin(start + segment.sweep_radians * index / count),
                                 segment.start.z, segment.unit) for index in range(1, count + 1))
    values[-1] = values[0]
    return tuple(values)


def _plane_xy(point: Point3, origin: Point3, x_axis: Vector3, y_axis: Vector3) -> tuple[float, float]:
    delta = Vector3(point.x - origin.x, point.y - origin.y, point.z - origin.z)
    return delta.dot(x_axis), delta.dot(y_axis)


def _area(points: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * sum(first[0] * second[1] - second[0] * first[1]
                     for first, second in zip(points, points[1:]))


def _self_intersects(points: tuple[tuple[float, float], ...]) -> bool:
    count = len(points) - 1
    for first_index in range(count):
        for second_index in range(first_index + 1, count):
            if second_index in {first_index, first_index + 1} or (first_index == 0 and second_index == count - 1):
                continue
            if _intersects(points[first_index], points[first_index + 1],
                           points[second_index], points[second_index + 1]):
                return True
    return False


def _intersects(a1, a2, b1, b2) -> bool:
    cross = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    values = cross(a1, a2, b1), cross(a1, a2, b2), cross(b1, b2, a1), cross(b1, b2, a2)
    if values[0] * values[1] < -_TOLERANCE and values[2] * values[3] < -_TOLERANCE:
        return True
    on_segment = lambda p, q, r: (min(p[0], q[0]) - _TOLERANCE <= r[0] <= max(p[0], q[0]) + _TOLERANCE and
                                  min(p[1], q[1]) - _TOLERANCE <= r[1] <= max(p[1], q[1]) + _TOLERANCE)
    return any(abs(value) <= _TOLERANCE and on_segment(first, second, point)
               for value, first, second, point in (
                   (values[0], a1, a2, b1), (values[1], a1, a2, b2),
                   (values[2], b1, b2, a1), (values[3], b1, b2, a2)))


def _reference_fingerprint(selector: PersistentProfileSelectorV1, occurrence_path: str | None,
                           transform: tuple[float, ...]) -> GeometryFingerprint:
    return GeometryFingerprint.from_payload({"selector": str(selector), "occurrence_path": occurrence_path,
                                             "absolute_transform": transform, "selector_version": 1})


def _quantized(point: Point3) -> tuple[int, int, int]:
    return tuple(round(value / _TOLERANCE) for value in (point.x, point.y, point.z))


def _distance(first: Point3, second: Point3) -> float:
    return math.sqrt((first.x - second.x) ** 2 + (first.y - second.y) ** 2 + (first.z - second.z) ** 2)


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()
