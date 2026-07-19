"""Fail-closed OCP adapter for persistent planar FACE references."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from uuid import UUID

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
from OCP.TopAbs import TopAbs_Orientation, TopAbs_ShapeEnum
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS, TopoDS_Edge, TopoDS_Face, TopoDS_Shape, TopoDS_Wire

from hms_cadcam.cad.models import CadDocumentId, CadObjectId, XcafTransform
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    PersistentCadObjectMap,
    PersistentObjectKey,
    PersistentXcafOccurrenceKey,
)
from hms_cadcam.cam.domain import (
    DiagnosticCode,
    FaceBoundaryCurve,
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
    PersistentFaceSelectorV1,
    PlanarFaceBounds,
    PlanarFaceDescriptor,
    Point3,
    ResolvedFaceBoundary,
    ResolvedMachiningGeometry,
    Revision,
    Vector3,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

_IDENTITY = (1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0)
_GEOMETRY_TOLERANCE = 1.0e-7
_ANGULAR_TOLERANCE = 1.0e-9
_ARC_CHORD_TOLERANCE = 1.0e-3
_MAX_ARC_SEGMENTS = 4096
logger = logging.getLogger(__name__)


class PlanarFaceResolutionError(ValueError):
    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _FaceGeometry:
    plane_origin: Point3
    x_axis: Vector3
    y_axis: Vector3
    normal: Vector3
    boundary: ResolvedFaceBoundary
    bounds: PlanarFaceBounds
    face_digest: str
    source_normal_reversed: bool


class OcpPlanarFaceResolver:
    """Resolve one active CAD document without leaking native handles."""

    def __init__(self, kernel: OcpCadKernel, document_id: CadDocumentId,
                 source_id: UUID, persistent_map: PersistentCadObjectMap,
                 unit: LengthUnit, source_revision: Revision = Revision(0)) -> None:
        if not isinstance(kernel, OcpCadKernel) or not isinstance(document_id, CadDocumentId):
            raise TypeError("OCP planar-face resolver requires an active OCP document")
        if not isinstance(source_id, UUID) or not isinstance(persistent_map, PersistentCadObjectMap):
            raise TypeError("OCP planar-face resolver source context is invalid")
        if not isinstance(unit, LengthUnit) or unit is LengthUnit.UNKNOWN:
            raise ValueError("Planar-face resolver requires an explicitly declared project unit")
        self._kernel = kernel
        self._document_id = document_id
        self._source_id = source_id
        self._persistent_map = persistent_map
        self._unit = unit
        self._source_revision = source_revision

    def bind_selection(self, selection: SelectionMetadata) -> GeometryReference:
        """Convert one runtime FACE selection into a persistent CAM selector."""
        if not isinstance(selection, SelectionMetadata) or selection.topology is not SelectionMode.FACE:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_UNSUPPORTED_FACE_SHAPE,
                                            "Hãy chọn đúng một FACE BREP.")
        if selection.document_id != self._document_id or selection.object_id is None:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_FACE_SOURCE_MISMATCH,
                                            "FACE selection không thuộc CAD document hiện hành.")
        key = self._persistent_map.by_runtime.get(selection.object_id)
        if key is None or key.source_id != self._source_id:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_FACE_REFERENCE_AMBIGUOUS,
                                            "FACE selection không có persistent container duy nhất.")
        face = self._runtime_selected_face(selection)
        transform = self._transform_for_object(selection.object_id)
        geometry = _extract_face_geometry(face, self._unit, transform)
        selector = PersistentFaceSelectorV1(_container_digest(key), geometry.face_digest)
        reference_id = GeometryReferenceId.new()
        occurrence_path = str(key.occurrence_path) if isinstance(key, PersistentXcafOccurrenceKey) else None
        fingerprint = _reference_fingerprint(selector, occurrence_path)
        return GeometryReference(reference_id, HMS_GEOMETRY_REFERENCE_SCHEME,
            HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, self._source_id,
            GeometryReferenceKind.FACE, GeometryRepresentationKind.BREP,
            fingerprint, self._source_revision, occurrence_path=occurrence_path,
            subshape_selector=str(selector), hint="Planar FACE target",
            diagnostic_fallback=(("container", selector.container_digest),
                                 ("face", selector.face_digest)))

    def resolve(self, reference: GeometryReference) -> ResolvedMachiningGeometry:
        """Resolve a persistent FACE reference to a verified native-free descriptor."""
        try:
            self._validate_reference(reference)
            selector = PersistentFaceSelectorV1.parse(reference.subshape_selector or "")
            candidates = [(key, object_id) for key, object_id in self._persistent_map.by_persistent.items()
                          if _container_digest(key) == selector.container_digest and
                          key.source_id == reference.source_id and
                          _occurrence_path(key) == reference.occurrence_path]
            if not candidates:
                return _failure(GeometryResolutionStatus.MISSING,
                    DiagnosticCode.FACING_FACE_REFERENCE_MISSING,
                    "Không tìm thấy persistent occurrence/container của FACE.")
            if len(candidates) != 1:
                return _failure(GeometryResolutionStatus.AMBIGUOUS,
                    DiagnosticCode.FACING_FACE_REFERENCE_AMBIGUOUS,
                    "Persistent FACE container khớp nhiều occurrence.")
            _key, object_id = candidates[0]
            container = self._kernel._resolve_presentation_shapes(self._document_id).get(object_id)
            if container is None:
                return _failure(GeometryResolutionStatus.MISSING,
                    DiagnosticCode.FACING_FACE_REFERENCE_MISSING,
                    "CAD container của FACE không còn tồn tại.")
            transform = self._transform_for_object(object_id)
            matches: list[tuple[TopoDS_Face, _FaceGeometry]] = []
            for face in _faces(container):
                try:
                    geometry = _extract_face_geometry(face, self._unit, transform)
                except PlanarFaceResolutionError:
                    continue
                if geometry.face_digest == selector.face_digest:
                    matches.append((face, geometry))
            if not matches:
                return _failure(GeometryResolutionStatus.TOPOLOGY_CHANGED,
                    DiagnosticCode.FACING_FACE_TOPOLOGY_CHANGED,
                    "Topology hoặc boundary của FACE đã thay đổi.")
            if len(matches) != 1:
                return _failure(GeometryResolutionStatus.AMBIGUOUS,
                    DiagnosticCode.FACING_FACE_REFERENCE_AMBIGUOUS,
                    "Subshape selector khớp nhiều FACE.")
            _face, geometry = matches[0]
            fingerprint = _reference_fingerprint(selector, reference.occurrence_path)
            if fingerprint != reference.expected_geometry_fingerprint:
                return _failure(GeometryResolutionStatus.TOPOLOGY_CHANGED,
                    DiagnosticCode.FACING_FACE_TOPOLOGY_CHANGED,
                    "Fingerprint FACE không còn khớp reference.")
            if reference.expected_source_revision != self._source_revision:
                return _failure(GeometryResolutionStatus.STALE,
                    DiagnosticCode.FACING_FACE_REFERENCE_STALE,
                    "Revision CAD source của FACE đã stale.")
            provenance = OccurrenceTransformProvenance(reference.occurrence_path,
                transform.values, geometry.source_normal_reversed)
            descriptor = PlanarFaceDescriptor(reference.reference_id, reference.source_id,
                geometry.plane_origin, geometry.x_axis, geometry.y_axis, geometry.normal,
                geometry.boundary, (), geometry.bounds, self._unit, fingerprint, provenance)
            return ResolvedMachiningGeometry(GeometryResolutionStatus.RESOLVED, descriptor)
        except PlanarFaceResolutionError as error:
            status = {
                DiagnosticCode.FACING_FACE_REFERENCE_MISSING: GeometryResolutionStatus.MISSING,
                DiagnosticCode.FACING_FACE_REFERENCE_STALE: GeometryResolutionStatus.STALE,
                DiagnosticCode.FACING_FACE_REFERENCE_AMBIGUOUS: GeometryResolutionStatus.AMBIGUOUS,
                DiagnosticCode.FACING_FACE_SOURCE_MISMATCH: GeometryResolutionStatus.SOURCE_MISMATCH,
                DiagnosticCode.FACING_FACE_TOPOLOGY_CHANGED: GeometryResolutionStatus.TOPOLOGY_CHANGED,
            }.get(error.code, GeometryResolutionStatus.INVALID)
            return _failure(status, error.code, str(error))
        except Exception:
            logger.exception("Unexpected OCP planar FACE resolution failure")
            return _failure(GeometryResolutionStatus.INVALID,
                DiagnosticCode.FACING_GEOMETRY_RESOLUTION_FAILED,
                "Không thể resolve planar FACE an toàn.")

    def _validate_reference(self, reference: GeometryReference) -> None:
        if not isinstance(reference, GeometryReference):
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_FACE_REFERENCE_MISSING,
                                            "GeometryReference FACE bị thiếu.")
        if reference.scheme != HMS_GEOMETRY_REFERENCE_SCHEME:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_GEOMETRY_RESOLUTION_FAILED,
                                            "GeometryReference dùng scheme không hỗ trợ.")
        if reference.scheme_version != HMS_GEOMETRY_REFERENCE_SCHEME_VERSION:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_GEOMETRY_RESOLUTION_FAILED,
                                            "GeometryReference dùng version không hỗ trợ.")
        if reference.source_id != self._source_id:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_FACE_SOURCE_MISMATCH,
                                            "GeometryReference thuộc CAD source khác.")
        if reference.kind is not GeometryReferenceKind.FACE or reference.geometry_kind is not GeometryRepresentationKind.BREP:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_UNSUPPORTED_FACE_SHAPE,
                                            "GeometryReference không trỏ tới BREP FACE.")

    def _runtime_selected_face(self, selection: SelectionMetadata) -> TopoDS_Face:
        prefix = f"{self._document_id}:face:"
        if not selection.selection_id.startswith(prefix):
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_FACE_REFERENCE_STALE,
                                            "Runtime FACE selector không thuộc document hiện hành.")
        try:
            index = int(selection.selection_id[len(prefix):])
        except ValueError as error:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_FACE_REFERENCE_STALE,
                                            "Runtime FACE selector không hợp lệ.") from error
        faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(self._kernel._resolve_shape(self._document_id),
                           TopAbs_ShapeEnum.TopAbs_FACE, faces)
        if not 1 <= index <= faces.Extent():
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_FACE_REFERENCE_STALE,
                                            "Runtime FACE selector đã stale.")
        face = TopoDS.Face_s(faces.FindKey(index))
        container = self._kernel._resolve_presentation_shapes(self._document_id).get(selection.object_id)
        if container is None or not _contains_face(container, face):
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_FACE_SOURCE_MISMATCH,
                                            "FACE không thuộc occurrence/container đã chọn.")
        return face

    def _transform_for_object(self, object_id: CadObjectId) -> XcafTransform:
        node = self._kernel.get_document_tree(self._document_id).find(object_id)
        return node.absolute_transform if node is not None and node.absolute_transform is not None else XcafTransform(_IDENTITY)


def _failure(status: GeometryResolutionStatus, code: DiagnosticCode,
             message: str) -> ResolvedMachiningGeometry:
    return ResolvedMachiningGeometry(status, message=message, diagnostic_code=code)


def _faces(shape: TopoDS_Shape) -> tuple[TopoDS_Face, ...]:
    values = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_FACE, values)
    return tuple(TopoDS.Face_s(values.FindKey(index)) for index in range(1, values.Extent() + 1))


def _contains_face(container: TopoDS_Shape, face: TopoDS_Face) -> bool:
    values = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(container, TopAbs_ShapeEnum.TopAbs_FACE, values)
    return values.FindIndex(face) > 0


def _extract_face_geometry(face: TopoDS_Face, unit: LengthUnit,
                           transform: XcafTransform) -> _FaceGeometry:
    surface = BRepAdaptor_Surface(face, True)
    if surface.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
        raise PlanarFaceResolutionError(DiagnosticCode.FACING_NON_PLANAR_FACE,
                                        "FACE đã chọn không phẳng.")
    wires = []
    explorer = TopExp_Explorer(face, TopAbs_ShapeEnum.TopAbs_WIRE)
    while explorer.More():
        wires.append(TopoDS.Wire_s(explorer.Current()))
        explorer.Next()
    outer = BRepTools.OuterWire_s(face)
    if outer.IsNull() or not wires:
        raise PlanarFaceResolutionError(DiagnosticCode.FACING_INVALID_FACE_BOUNDARY,
                                        "FACE không có outer wire kín.")
    if sum(1 for wire in wires if not wire.IsSame(outer)):
        raise PlanarFaceResolutionError(DiagnosticCode.FACING_UNSUPPORTED_INNER_LOOPS,
                                        "Facing planar v1 không hỗ trợ inner loop/hole.")
    plane = surface.Plane()
    origin_xyz = plane.Location().Coord()
    raw_normal = _unit_vector(Vector3(*plane.Axis().Direction().Coord()))
    if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
        raw_normal = Vector3(-raw_normal.x, -raw_normal.y, -raw_normal.z)
    normal, reversed_normal = _canonical_normal(raw_normal)
    x_axis = _canonical_x_axis(normal)
    y_axis = _unit_vector(normal.cross(x_axis))
    origin = Point3(*origin_xyz, unit)
    points, curves = _wire_polyline(outer, face, unit)
    points = _canonical_loop(points, origin, x_axis, y_axis)
    _validate_simple_polygon(points, origin, x_axis, y_axis)
    boundary = ResolvedFaceBoundary(points, tuple(sorted(curves, key=lambda item: item.value)))
    minimum = Point3(min(point.x for point in points), min(point.y for point in points),
                     min(point.z for point in points), unit)
    maximum = Point3(max(point.x for point in points), max(point.y for point in points),
                     max(point.z for point in points), unit)
    local_points = tuple(_inverse_transform_point(point, transform) for point in points)
    face_digest = _face_digest(local_points, curves)
    return _FaceGeometry(origin, x_axis, y_axis, normal, boundary,
                         PlanarFaceBounds(minimum, maximum), face_digest, reversed_normal)


def _wire_polyline(wire: TopoDS_Wire, face: TopoDS_Face,
                   unit: LengthUnit) -> tuple[tuple[Point3, ...], tuple[FaceBoundaryCurve, ...]]:
    points: list[Point3] = []
    curves: list[FaceBoundaryCurve] = []
    explorer = BRepTools_WireExplorer(wire, face)
    while explorer.More():
        edge = TopoDS.Edge_s(explorer.Current())
        sampled, kind = _sample_edge(edge, unit)
        if explorer.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
            sampled = tuple(reversed(sampled))
        if points:
            if _distance(points[-1], sampled[0]) > _GEOMETRY_TOLERANCE:
                raise PlanarFaceResolutionError(DiagnosticCode.FACING_INVALID_FACE_BOUNDARY,
                                                "Outer wire có edge không liên tục.")
            points.extend(sampled[1:])
        else:
            points.extend(sampled)
        curves.append(kind)
        explorer.Next()
    if len(points) < 4 or _distance(points[0], points[-1]) > _GEOMETRY_TOLERANCE:
        raise PlanarFaceResolutionError(DiagnosticCode.FACING_INVALID_FACE_BOUNDARY,
                                        "Outer wire bị hở.")
    points[-1] = points[0]
    return tuple(points), tuple(curves)


def _sample_edge(edge: TopoDS_Edge, unit: LengthUnit) -> tuple[tuple[Point3, ...], FaceBoundaryCurve]:
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    if not math.isfinite(first) or not math.isfinite(last) or last <= first:
        raise PlanarFaceResolutionError(DiagnosticCode.FACING_INVALID_FACE_BOUNDARY,
                                        "FACE edge có parameter range không hợp lệ.")
    kind = curve.GetType()
    if kind == GeomAbs_CurveType.GeomAbs_Line:
        parameters = (first, last)
        source_kind = FaceBoundaryCurve.LINE
    elif kind == GeomAbs_CurveType.GeomAbs_Circle:
        radius = curve.Circle().Radius()
        if radius <= _GEOMETRY_TOLERANCE:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_INVALID_FACE_BOUNDARY,
                                            "FACE arc có bán kính không hợp lệ.")
        cosine = max(-1.0, min(1.0, 1.0 - _ARC_CHORD_TOLERANCE / radius))
        maximum_step = max(math.radians(1.0), 2.0 * math.acos(cosine))
        segments = max(2, math.ceil((last - first) / maximum_step))
        if segments > _MAX_ARC_SEGMENTS:
            raise PlanarFaceResolutionError(DiagnosticCode.FACING_UNSUPPORTED_FACE_SHAPE,
                                            "FACE arc vượt giới hạn tessellation v1.")
        parameters = tuple(first + (last - first) * index / segments
                           for index in range(segments + 1))
        source_kind = FaceBoundaryCurve.ARC
    else:
        raise PlanarFaceResolutionError(DiagnosticCode.FACING_UNSUPPORTED_FACE_SHAPE,
                                        "Facing planar v1 chỉ hỗ trợ LINE/ARC boundary.")
    return (tuple(Point3(*curve.Value(value).Coord(), unit) for value in parameters), source_kind)


def _canonical_loop(points: tuple[Point3, ...], origin: Point3,
                    x_axis: Vector3, y_axis: Vector3) -> tuple[Point3, ...]:
    opened = list(points[:-1])
    coordinates = [_plane_xy(point, origin, x_axis, y_axis) for point in opened]
    area = sum(first[0] * second[1] - second[0] * first[1]
               for first, second in zip(coordinates, coordinates[1:] + coordinates[:1], strict=True))
    if abs(area) <= _GEOMETRY_TOLERANCE * _GEOMETRY_TOLERANCE:
        raise PlanarFaceResolutionError(DiagnosticCode.FACING_INVALID_FACE_BOUNDARY,
                                        "FACE outer boundary có diện tích bằng zero.")
    if area < 0.0:
        opened.reverse()
        coordinates.reverse()
    start = min(range(len(opened)), key=lambda index: _quantized_xyz(opened[index]))
    opened = opened[start:] + opened[:start]
    return tuple((*opened, opened[0]))


def _validate_simple_polygon(points: tuple[Point3, ...], origin: Point3,
                             x_axis: Vector3, y_axis: Vector3) -> None:
    values = [_plane_xy(point, origin, x_axis, y_axis) for point in points[:-1]]
    count = len(values)
    for first_index in range(count):
        a1, a2 = values[first_index], values[(first_index + 1) % count]
        for second_index in range(first_index + 1, count):
            if second_index in {first_index, (first_index + 1) % count} or (
                first_index == 0 and second_index == count - 1
            ):
                continue
            b1, b2 = values[second_index], values[(second_index + 1) % count]
            if _segments_intersect(a1, a2, b1, b2):
                raise PlanarFaceResolutionError(DiagnosticCode.FACING_INVALID_FACE_BOUNDARY,
                                                "FACE outer boundary tự giao.")


def _segments_intersect(a1, a2, b1, b2) -> bool:
    def cross(first, second, third):
        return ((second[0] - first[0]) * (third[1] - first[1]) -
                (second[1] - first[1]) * (third[0] - first[0]))
    values = (cross(a1, a2, b1), cross(a1, a2, b2),
              cross(b1, b2, a1), cross(b1, b2, a2))
    if values[0] * values[1] < -_GEOMETRY_TOLERANCE and values[2] * values[3] < -_GEOMETRY_TOLERANCE:
        return True

    def on_segment(first, second, value) -> bool:
        return (min(first[0], second[0]) - _GEOMETRY_TOLERANCE <= value[0] <=
                max(first[0], second[0]) + _GEOMETRY_TOLERANCE and
                min(first[1], second[1]) - _GEOMETRY_TOLERANCE <= value[1] <=
                max(first[1], second[1]) + _GEOMETRY_TOLERANCE)

    return any((abs(cross_value) <= _GEOMETRY_TOLERANCE and on_segment(first, second, value))
               for cross_value, first, second, value in (
                   (values[0], a1, a2, b1), (values[1], a1, a2, b2),
                   (values[2], b1, b2, a1), (values[3], b1, b2, a2)))


def _canonical_normal(value: Vector3) -> tuple[Vector3, bool]:
    normalized = _unit_vector(value)
    components = (normalized.x, normalized.y, normalized.z)
    first = next(component for component in components if abs(component) > _ANGULAR_TOLERANCE)
    if first < 0.0:
        return Vector3(-normalized.x, -normalized.y, -normalized.z), True
    return normalized, False


def _canonical_x_axis(normal: Vector3) -> Vector3:
    seed = Vector3(1.0, 0.0, 0.0) if abs(normal.x) < 0.9 else Vector3(0.0, 1.0, 0.0)
    projection = Vector3(seed.x - normal.x * seed.dot(normal),
                         seed.y - normal.y * seed.dot(normal),
                         seed.z - normal.z * seed.dot(normal))
    return _unit_vector(projection)


def _unit_vector(value: Vector3) -> Vector3:
    magnitude = value.magnitude
    if magnitude <= _ANGULAR_TOLERANCE:
        raise PlanarFaceResolutionError(DiagnosticCode.FACING_GEOMETRY_RESOLUTION_FAILED,
                                        "Không thể tạo basis cho planar FACE.")
    return Vector3(value.x / magnitude, value.y / magnitude, value.z / magnitude)


def _plane_xy(point: Point3, origin: Point3, x_axis: Vector3, y_axis: Vector3) -> tuple[float, float]:
    delta = Vector3(point.x - origin.x, point.y - origin.y, point.z - origin.z)
    return delta.dot(x_axis), delta.dot(y_axis)


def _distance(first: Point3, second: Point3) -> float:
    return math.sqrt((first.x - second.x) ** 2 + (first.y - second.y) ** 2 +
                     (first.z - second.z) ** 2)


def _quantized_xyz(point: Point3) -> tuple[int, int, int]:
    return tuple(round(value / _GEOMETRY_TOLERANCE) for value in (point.x, point.y, point.z))


def _inverse_transform_point(point: Point3, transform: XcafTransform) -> Point3:
    values = transform.values
    translated = (point.x - values[3], point.y - values[7], point.z - values[11])
    return Point3(
        values[0] * translated[0] + values[4] * translated[1] + values[8] * translated[2],
        values[1] * translated[0] + values[5] * translated[1] + values[9] * translated[2],
        values[2] * translated[0] + values[6] * translated[1] + values[10] * translated[2],
        point.unit,
    )


def _face_digest(points: tuple[Point3, ...], curves: tuple[FaceBoundaryCurve, ...]) -> str:
    opened = tuple(_quantized_xyz(point) for point in points[:-1])
    rotations = []
    for values in (opened, tuple(reversed(opened))):
        rotations.extend(values[index:] + values[:index] for index in range(len(values)))
    canonical = min(rotations)
    return _digest({"format": "hms_face_geometry_v1", "points": canonical,
                    "curves": sorted(curve.value for curve in curves)})


def _container_digest(key: PersistentObjectKey) -> str:
    if isinstance(key, PersistentCadObjectKey):
        payload = {"kind": "brep", "path_version": int(key.topology_path_version),
                   "path": str(key.topology_path)}
    elif isinstance(key, PersistentXcafOccurrenceKey):
        payload = {"kind": "xcaf", "scheme": key.key_scheme.value,
                   "version": int(key.key_version), "path": str(key.occurrence_path),
                   "product": str(key.product_identity), "role": key.occurrence_role.value}
    else:  # pragma: no cover
        raise TypeError("Unsupported persistent CAD container key")
    return _digest(payload)


def _occurrence_path(key: PersistentObjectKey) -> str | None:
    return str(key.occurrence_path) if isinstance(key, PersistentXcafOccurrenceKey) else None


def _reference_fingerprint(selector: PersistentFaceSelectorV1,
                           occurrence_path: str | None) -> GeometryFingerprint:
    return GeometryFingerprint.from_payload({"selector": str(selector),
        "occurrence_path": occurrence_path, "selector_version": 1})


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()
