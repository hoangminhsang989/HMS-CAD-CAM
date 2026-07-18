"""Own native document references for the lifetime of one OCP kernel."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from OCP.Poly import Poly_Triangulation
from OCP.TopoDS import TopoDS_Shape

from hms_cadcam.cad.exceptions import CadDocumentNotFoundError
from hms_cadcam.cad.models import (
    CadDocumentId,
    CadDocumentMetadata,
    CadFormat,
    CadGeometryKind,
    CadUnits,
    MeshStatistics,
)
from hms_cadcam.cad.ocp.topology import (
    get_bounding_box,
    get_mesh_bounding_box,
    get_topology_counts,
)


@dataclass(slots=True)
class _BrepDocumentRecord:
    shape: TopoDS_Shape
    metadata: CadDocumentMetadata


@dataclass(slots=True)
class _MeshDocumentRecord:
    triangulation: Poly_Triangulation
    metadata: CadDocumentMetadata


_DocumentRecord = _BrepDocumentRecord | _MeshDocumentRecord


class OcpDocumentStore:
    """Keep all TopoDS references behind opaque product document IDs."""

    def __init__(self) -> None:
        self._records: dict[CadDocumentId, _DocumentRecord] = {}
        self._lock = threading.RLock()

    def add_brep(
        self,
        shape: TopoDS_Shape,
        cad_format: CadFormat,
        source_path: Path | None = None,
    ) -> CadDocumentMetadata:
        """Register one validated shape and return only public metadata."""
        if shape.IsNull():
            raise ValueError("Cannot register a null CAD shape")
        document_id = CadDocumentId(f"ocp:{uuid4().hex}")
        topology_counts = get_topology_counts(shape)
        if not any(
            (
                topology_counts.solids,
                topology_counts.faces,
                topology_counts.edges,
            )
        ):
            raise ValueError("Cannot register an empty BREP shape")
        metadata = CadDocumentMetadata(
            document_id=document_id,
            cad_format=cad_format,
            bounding_box=get_bounding_box(shape),
            geometry_kind=CadGeometryKind.BREP,
            units=CadUnits.UNKNOWN,
            topology_counts=topology_counts,
            source_path=source_path,
        )
        with self._lock:
            self._records[document_id] = _BrepDocumentRecord(shape, metadata)
        return metadata

    def add_mesh(
        self,
        triangulation: Poly_Triangulation,
        cad_format: CadFormat,
        source_path: Path | None = None,
    ) -> CadDocumentMetadata:
        """Register a triangle mesh without converting it into BREP topology."""
        vertices = triangulation.NbNodes()
        triangles = triangulation.NbTriangles()
        if vertices <= 0 or triangles <= 0:
            raise ValueError("Cannot register an empty triangle mesh")
        document_id = CadDocumentId(f"ocp:{uuid4().hex}")
        metadata = CadDocumentMetadata(
            document_id=document_id,
            cad_format=cad_format,
            bounding_box=get_mesh_bounding_box(triangulation),
            geometry_kind=CadGeometryKind.TRIANGLE_MESH,
            units=CadUnits.UNKNOWN,
            mesh_statistics=MeshStatistics(vertices, triangles),
            source_path=source_path,
        )
        with self._lock:
            self._records[document_id] = _MeshDocumentRecord(
                triangulation,
                metadata,
            )
        return metadata

    def get_metadata(self, document_id: CadDocumentId) -> CadDocumentMetadata:
        """Return metadata for an owned document or raise a controlled error."""
        with self._lock:
            record = self._records.get(document_id)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
        return record.metadata

    def resolve_shape(self, document_id: CadDocumentId) -> TopoDS_Shape:
        """Resolve a native shape for trusted internal OCP adapters only."""
        with self._lock:
            record = self._records.get(document_id)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
        if not isinstance(record, _BrepDocumentRecord):
            raise TypeError(f"CAD document is not BREP: {document_id}")
        return record.shape

    def resolve_triangulation(
        self,
        document_id: CadDocumentId,
    ) -> Poly_Triangulation:
        """Resolve mesh data for trusted internal OCP adapters only."""
        with self._lock:
            record = self._records.get(document_id)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
        if not isinstance(record, _MeshDocumentRecord):
            raise TypeError(f"CAD document is not a triangle mesh: {document_id}")
        return record.triangulation

    def release(self, document_id: CadDocumentId) -> None:
        """Remove the record so its native shape can be released."""
        with self._lock:
            record = self._records.pop(document_id, None)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
