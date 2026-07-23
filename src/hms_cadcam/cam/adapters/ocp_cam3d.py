"""OCP adapter for stable CAM 3D face binding and calculation tessellation."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from typing import Callable
from uuid import UUID

from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.IMeshTools import IMeshTools_Parameters
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_Orientation, TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import (
    TopTools_FormatVersion_VERSION_3,
    TopTools_IndexedMapOfShape,
)
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape

from hms_cadcam.cad.models import CadDocumentId, CadObjectId
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    PersistentCadObjectMap,
    PersistentObjectKey,
    PersistentXcafOccurrenceKey,
)
from hms_cadcam.cam.cam3d.mesh import (
    Cam3DCancelledError,
    Cam3DMeshError,
    Cam3DResolvedSurfaceMesh,
)
from hms_cadcam.cam.cam3d.models import (
    Cam3DDiagnostic,
    Cam3DDiagnosticCode,
    Cam3DDiagnosticSeverity,
    Cam3DTolerancePolicy,
    CamSurfaceOrientation,
    CamSurfaceReference,
    CamSurfaceRole,
)
from hms_cadcam.cam.cam3d.parallel import ContactResolver
from hms_cadcam.cam.domain import (
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    LengthUnit,
    Point3,
    Revision,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

logger = logging.getLogger(__name__)
_SCHEME = "hms_cam3d_surface"
_SCHEME_VERSION = 1
_SELECTOR = re.compile(r"hms_cam3d_surface_v1:([0-9a-f]{64}):([0-9a-f]{64})")


class OcpCam3DSurfaceAdapter:
    """Resolve persistent BREP faces without exposing native handles."""

    def __init__(
        self,
        kernel: OcpCadKernel,
        document_id: CadDocumentId,
        source_id: UUID,
        project_id: UUID,
        persistent_map: PersistentCadObjectMap,
        *,
        source_revision: Revision = Revision(0),
    ) -> None:
        if not isinstance(kernel, OcpCadKernel) or not isinstance(document_id, CadDocumentId):
            raise TypeError("OCP CAM 3D adapter requires an active OCP document")
        if not isinstance(source_id, UUID) or not isinstance(project_id, UUID):
            raise TypeError("OCP CAM 3D source/project identity is invalid")
        if not isinstance(persistent_map, PersistentCadObjectMap):
            raise TypeError("OCP CAM 3D persistent map is invalid")
        if not isinstance(source_revision, Revision):
            raise TypeError("OCP CAM 3D source revision is invalid")
        self._kernel = kernel
        self._document_id = document_id
        self._source_id = source_id
        self._project_id = project_id
        self._persistent_map = persistent_map
        self._source_revision = source_revision

    def bind_selection(
        self,
        selection: SelectionMetadata,
        role: CamSurfaceRole,
        *,
        orientation: CamSurfaceOrientation = CamSurfaceOrientation.FORWARD,
    ) -> CamSurfaceReference:
        """Convert one runtime FACE selection into stable project geometry."""
        if not isinstance(selection, SelectionMetadata) or selection.topology is not SelectionMode.FACE:
            raise ValueError("CAM 3D requires one BREP FACE selection")
        if not isinstance(role, CamSurfaceRole) or not isinstance(
            orientation, CamSurfaceOrientation
        ):
            raise ValueError("CAM 3D surface role/orientation is invalid")
        if selection.document_id != self._document_id or selection.object_id is None:
            raise ValueError("CAM 3D FACE selection belongs to another document")
        key = self._persistent_map.by_runtime.get(selection.object_id)
        if key is None or key.source_id != self._source_id:
            raise ValueError("CAM 3D FACE has no unique persistent container")
        face = self._runtime_selected_face(selection)
        selector = _selector(key, face)
        occurrence_path = _occurrence_path(key)
        fingerprint = _reference_fingerprint(selector, occurrence_path)
        geometry = GeometryReference(
            GeometryReferenceId.new(),
            _SCHEME,
            _SCHEME_VERSION,
            self._source_id,
            GeometryReferenceKind.FACE,
            GeometryRepresentationKind.BREP,
            fingerprint,
            self._source_revision,
            occurrence_path=occurrence_path,
            subshape_selector=selector,
            hint="CAM 3D BREP FACE",
            diagnostic_fallback=(("container", _container_digest(key)), ("face", _face_digest(face))),
        )
        return CamSurfaceReference(
            self._project_id,
            geometry,
            orientation,
            role,
            body_identity=_container_digest(key),
            face_identity=selector,
        )

    def tessellate(
        self,
        surface: CamSurfaceReference,
        tolerance: Cam3DTolerancePolicy,
        cancellation: Callable[[], bool] | None = None,
    ) -> Cam3DResolvedSurfaceMesh:
        """Resolve, copy and mesh a face using calculation-specific tolerances."""
        try:
            if cancellation is not None and cancellation():
                raise _error(Cam3DDiagnosticCode.CANCELLED, "CAM 3D tessellation was cancelled", surface)
            face = self._resolve(surface)
            copied = TopoDS.Face_s(BRepBuilderAPI_Copy(face).Shape())
            mesher = BRepMesh_IncrementalMesh(
                copied,
                _meshing_parameters(tolerance),
            )
            if not mesher.IsDone():
                raise _error(Cam3DDiagnosticCode.FAILED, "OCP CAM 3D tessellation did not complete", surface)
            location = TopLoc_Location()
            triangulation = BRep_Tool.Triangulation_s(copied, location)
            if triangulation is None or triangulation.NbNodes() <= 0 or triangulation.NbTriangles() <= 0:
                raise _error(Cam3DDiagnosticCode.MESH_EMPTY, "OCP CAM 3D face produced an empty mesh", surface)
            transform = location.Transformation()
            vertices = []
            for index in range(1, triangulation.NbNodes() + 1):
                if index % 2048 == 0 and cancellation is not None and cancellation():
                    raise _error(Cam3DDiagnosticCode.CANCELLED, "CAM 3D tessellation was cancelled", surface)
                point = triangulation.Node(index).Transformed(transform)
                vertices.append(Point3(*point.Coord(), LengthUnit.MM))
            triangles = []
            reverse_native = copied.Orientation() is TopAbs_Orientation.TopAbs_REVERSED
            for index in range(1, triangulation.NbTriangles() + 1):
                if index % 2048 == 0 and cancellation is not None and cancellation():
                    raise _error(Cam3DDiagnosticCode.CANCELLED, "CAM 3D tessellation was cancelled", surface)
                first, second, third = triangulation.Triangle(index).Get()
                triangle = (first - 1, second - 1, third - 1)
                if reverse_native:
                    triangle = (triangle[0], triangle[2], triangle[1])
                triangles.append(triangle)
            return Cam3DResolvedSurfaceMesh(surface, tuple(vertices), tuple(triangles))
        except Cam3DMeshError:
            raise
        except Exception as error:
            logger.exception("Unexpected OCP CAM 3D tessellation failure")
            raise _error(
                Cam3DDiagnosticCode.FAILED,
                "OCP CAM 3D tessellation failed safely",
                surface,
                evidence=(("error_type", type(error).__name__),),
            ) from error

    def contact_resolver(
        self,
        surfaces: tuple[CamSurfaceReference, ...],
    ) -> ContactResolver:
        """Create an isolated BRep contact resolver for a worker calculation."""
        if not isinstance(surfaces, tuple) or not surfaces:
            raise ValueError("Parallel contact resolver requires selected faces")
        from hms_cadcam.cam.adapters.ocp_parallel_contact import (
            OcpParallelContactResolver,
        )

        return OcpParallelContactResolver(
            tuple((surface, self._resolve(surface)) for surface in surfaces)
        )

    def _resolve(self, surface: CamSurfaceReference) -> TopoDS_Face:
        if not isinstance(surface, CamSurfaceReference):
            raise _error(Cam3DDiagnosticCode.INVALID_REQUEST, "CAM 3D surface reference is invalid")
        reference = surface.geometry
        if surface.project_id != self._project_id or reference.source_id != self._source_id:
            raise _error(Cam3DDiagnosticCode.SURFACE_STALE, "CAM 3D surface belongs to another project/source", surface)
        if reference.scheme != _SCHEME or reference.scheme_version != _SCHEME_VERSION:
            raise _error(Cam3DDiagnosticCode.SURFACE_MISSING, "CAM 3D surface selector scheme is unsupported", surface)
        if reference.expected_source_revision != self._source_revision:
            raise _error(Cam3DDiagnosticCode.SURFACE_STALE, "CAM 3D surface source revision is stale", surface)
        selector = _parse_selector(reference.subshape_selector or "")
        candidates = [
            (key, object_id)
            for key, object_id in self._persistent_map.by_persistent.items()
            if key.source_id == self._source_id
            and _container_digest(key) == selector[0]
            and _occurrence_path(key) == reference.occurrence_path
        ]
        if len(candidates) != 1:
            code = Cam3DDiagnosticCode.SURFACE_MISSING if not candidates else Cam3DDiagnosticCode.SURFACE_STALE
            raise _error(code, "CAM 3D surface container is missing or ambiguous", surface)
        container = self._kernel._resolve_presentation_shapes(self._document_id).get(candidates[0][1])
        if container is None:
            raise _error(Cam3DDiagnosticCode.SURFACE_MISSING, "CAM 3D surface container is missing", surface)
        matches = tuple(face for face in _faces(container) if _face_digest(face) == selector[1])
        if len(matches) != 1:
            code = Cam3DDiagnosticCode.SURFACE_MISSING if not matches else Cam3DDiagnosticCode.SURFACE_STALE
            raise _error(code, "CAM 3D face is missing or ambiguous", surface)
        if _reference_fingerprint(reference.subshape_selector or "", reference.occurrence_path) != reference.expected_geometry_fingerprint:
            raise _error(Cam3DDiagnosticCode.SURFACE_STALE, "CAM 3D face fingerprint is stale", surface)
        return matches[0]

    def _runtime_selected_face(self, selection: SelectionMetadata) -> TopoDS_Face:
        prefix = f"{self._document_id}:face:"
        if not selection.selection_id.startswith(prefix):
            raise ValueError("Runtime CAM 3D FACE selector is stale")
        try:
            index = int(selection.selection_id[len(prefix) :])
        except ValueError as error:
            raise ValueError("Runtime CAM 3D FACE selector is invalid") from error
        faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(
            self._kernel._resolve_shape(self._document_id),
            TopAbs_ShapeEnum.TopAbs_FACE,
            faces,
        )
        if not 1 <= index <= faces.Extent():
            raise ValueError("Runtime CAM 3D FACE selector is stale")
        face = TopoDS.Face_s(faces.FindKey(index))
        container = self._kernel._resolve_presentation_shapes(self._document_id).get(selection.object_id)
        if container is None or not _contains_face(container, face):
            raise ValueError("Runtime CAM 3D FACE does not belong to the selected container")
        return face


def _faces(shape: TopoDS_Shape) -> tuple[TopoDS_Face, ...]:
    values = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_FACE, values)
    return tuple(TopoDS.Face_s(values.FindKey(index)) for index in range(1, values.Extent() + 1))


def _meshing_parameters(
    tolerance: Cam3DTolerancePolicy,
) -> IMeshTools_Parameters:
    """Map the complete CAM 3D tolerance policy to deterministic OCP controls."""
    parameters = IMeshTools_Parameters()
    parameters.Deflection = tolerance.chordal_tolerance
    parameters.DeflectionInterior = tolerance.chordal_tolerance
    parameters.Angle = tolerance.angular_tolerance
    parameters.AngleInterior = tolerance.angular_tolerance
    parameters.Relative = False
    parameters.InParallel = False
    parameters.ControlSurfaceDeflection = True
    parameters.ForceFaceDeflection = True
    if tolerance.minimum_triangle_size is not None:
        parameters.MinSize = tolerance.minimum_triangle_size
        parameters.AdjustMinSize = False
    return parameters


def _contains_face(container: TopoDS_Shape, face: TopoDS_Face) -> bool:
    values = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(container, TopAbs_ShapeEnum.TopAbs_FACE, values)
    return values.FindIndex(face) > 0


def _selector(key: PersistentObjectKey, face: TopoDS_Face) -> str:
    return f"hms_cam3d_surface_v1:{_container_digest(key)}:{_face_digest(face)}"


def _parse_selector(value: str) -> tuple[str, str]:
    match = _SELECTOR.fullmatch(value)
    if match is None:
        raise ValueError("Unsupported CAM 3D persistent surface selector")
    return match.group(1), match.group(2)


def _face_digest(face: TopoDS_Face) -> str:
    stream = io.BytesIO()
    BRepTools.Write_s(
        face,
        stream,
        False,
        False,
        TopTools_FormatVersion_VERSION_3,
    )
    return hashlib.sha256(stream.getvalue()).hexdigest()


def _container_digest(key: PersistentObjectKey) -> str:
    if isinstance(key, PersistentCadObjectKey):
        payload = {
            "kind": "brep",
            "path_version": int(key.topology_path_version),
            "path": str(key.topology_path),
        }
    elif isinstance(key, PersistentXcafOccurrenceKey):
        payload = {
            "kind": "xcaf",
            "scheme": key.key_scheme.value,
            "version": int(key.key_version),
            "path": str(key.occurrence_path),
            "product": str(key.product_identity),
            "role": key.occurrence_role.value,
        }
    else:  # pragma: no cover - closed persistent-key union
        raise TypeError("Unsupported persistent CAD container key")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _occurrence_path(key: PersistentObjectKey) -> str | None:
    return str(key.occurrence_path) if isinstance(key, PersistentXcafOccurrenceKey) else None


def _reference_fingerprint(selector: str, occurrence_path: str | None) -> GeometryFingerprint:
    return GeometryFingerprint.from_payload(
        {
            "selector": selector,
            "occurrence_path": occurrence_path,
            "selector_version": _SCHEME_VERSION,
        }
    )


def _error(
    code: Cam3DDiagnosticCode,
    message: str,
    surface: CamSurfaceReference | None = None,
    *,
    evidence: tuple[tuple[str, str], ...] = (),
) -> Cam3DMeshError:
    diagnostic = Cam3DDiagnostic(
            code,
            (
                Cam3DDiagnosticSeverity.WARNING
                if code is Cam3DDiagnosticCode.CANCELLED
                else Cam3DDiagnosticSeverity.ERROR
            ),
            message,
            source_reference_id=(
                surface.geometry.reference_id if surface is not None else None
            ),
            evidence=evidence,
        )
    return (
        Cam3DCancelledError(diagnostic)
        if code is Cam3DDiagnosticCode.CANCELLED
        else Cam3DMeshError(diagnostic)
    )
