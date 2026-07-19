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
    CadDocumentKind,
    CadDocumentMetadata,
    CadDocumentTree,
    CadObjectId,
    CadFormat,
    CadGeometryKind,
    CadUnits,
    MeshStatistics,
    XcafAssemblyMetadata,
    XcafNodeRole,
    XcafOccurrenceId,
    XcafOccurrenceMetadata,
    XcafProductId,
    XcafProductMetadata,
    XcafSourceAppearance,
    XcafTransform,
)
from hms_cadcam.cad.ocp.topology import (
    get_bounding_box,
    build_brep_document_tree,
    build_mesh_document_tree,
    get_mesh_bounding_box,
    get_topology_counts,
)
from hms_cadcam.cad.ocp.xcaf import (
    OcpXcafDocumentData,
    OcpXcafImportPayload,
    OcpXcafPresentationSource,
    build_xcaf_document_data,
    build_xcaf_document_tree,
    resolve_occurrence_shape,
    resolve_presentation_sources,
)


@dataclass(slots=True)
class _BrepDocumentRecord:
    shape: TopoDS_Shape
    metadata: CadDocumentMetadata
    tree: CadDocumentTree
    presentation_shapes: dict[CadObjectId, TopoDS_Shape]


@dataclass(slots=True)
class _MeshDocumentRecord:
    triangulation: Poly_Triangulation
    metadata: CadDocumentMetadata
    tree: CadDocumentTree


@dataclass(slots=True)
class _XcafDocumentRecord:
    payload: OcpXcafImportPayload
    data: OcpXcafDocumentData
    metadata: CadDocumentMetadata
    tree: CadDocumentTree
    presentation_shapes: dict[CadObjectId, TopoDS_Shape]


_DocumentRecord = _BrepDocumentRecord | _MeshDocumentRecord | _XcafDocumentRecord


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
            document_kind=CadDocumentKind.BREP,
            units=CadUnits.UNKNOWN,
            topology_counts=topology_counts,
            source_path=source_path,
        )
        tree, presentation_shapes = build_brep_document_tree(document_id, shape)
        with self._lock:
            self._records[document_id] = _BrepDocumentRecord(
                shape,
                metadata,
                tree,
                presentation_shapes,
            )
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
            document_kind=CadDocumentKind.TRIANGLE_MESH,
            units=CadUnits.UNKNOWN,
            mesh_statistics=MeshStatistics(vertices, triangles),
            source_path=source_path,
        )
        with self._lock:
            self._records[document_id] = _MeshDocumentRecord(
                triangulation,
                metadata,
                build_mesh_document_tree(document_id, metadata.bounding_box),
            )
        return metadata

    def add_xcaf(
        self,
        payload: OcpXcafImportPayload,
        source_path: Path,
    ) -> CadDocumentMetadata:
        """Atomically register one STEPCAF document and its assembly indexes."""
        if payload.shape.IsNull():
            raise ValueError("Cannot register a null XCAF aggregate shape")
        document_id = CadDocumentId(f"ocp:{uuid4().hex}")
        data = build_xcaf_document_data(document_id, payload)
        topology_counts = get_topology_counts(payload.shape)
        if not any(
            (
                topology_counts.solids,
                topology_counts.faces,
                topology_counts.edges,
            )
        ):
            raise ValueError("Cannot register an empty XCAF BREP document")
        metadata = CadDocumentMetadata(
            document_id=document_id,
            cad_format=CadFormat.STEP,
            bounding_box=get_bounding_box(payload.shape),
            geometry_kind=CadGeometryKind.BREP,
            document_kind=data.document_kind,
            units=CadUnits.UNKNOWN,
            topology_counts=topology_counts,
            source_path=source_path,
        )
        tree = build_xcaf_document_tree(
            document_id, payload, data, metadata.bounding_box
        )
        presentation_shapes = {
            data.object_ids_by_occurrence[occurrence_id]: resolve_occurrence_shape(
                payload, data, occurrence_id
            )
            for occurrence_id, occurrence in data.occurrences.items()
            if occurrence.role is XcafNodeRole.PART
        }
        record = _XcafDocumentRecord(
            payload,
            data,
            metadata,
            tree,
            presentation_shapes,
        )
        with self._lock:
            self._records[document_id] = record
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
        if not isinstance(record, (_BrepDocumentRecord, _XcafDocumentRecord)):
            raise TypeError(f"CAD document is not BREP: {document_id}")
        return record.shape if isinstance(record, _BrepDocumentRecord) else record.payload.shape

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

    def get_tree(self, document_id: CadDocumentId) -> CadDocumentTree:
        """Return the stored immutable topology tree."""
        with self._lock:
            record = self._records.get(document_id)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
        return record.tree

    def resolve_presentation_shapes(
        self,
        document_id: CadDocumentId,
    ) -> dict[CadObjectId, TopoDS_Shape]:
        """Resolve BREP leaf shapes for trusted viewer adapters only."""
        with self._lock:
            record = self._records.get(document_id)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
        if not isinstance(record, (_BrepDocumentRecord, _XcafDocumentRecord)):
            raise TypeError(f"CAD document is not BREP: {document_id}")
        return dict(record.presentation_shapes)

    def get_xcaf_assembly_metadata(
        self,
        document_id: CadDocumentId,
    ) -> XcafAssemblyMetadata:
        """Return the immutable assembly index for a STEPCAF document."""
        return self._get_xcaf_record(document_id).data.assembly_metadata

    def get_xcaf_root_occurrences(
        self,
        document_id: CadDocumentId,
    ) -> tuple[XcafOccurrenceMetadata, ...]:
        """Return every root occurrence in stable transfer order."""
        record = self._get_xcaf_record(document_id)
        return tuple(
            record.data.occurrences[occurrence_id]
            for occurrence_id in record.data.assembly_metadata.root_occurrence_ids
        )

    def get_xcaf_child_occurrences(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> tuple[XcafOccurrenceMetadata, ...]:
        """Return direct children for one occurrence."""
        record = self._get_xcaf_record(document_id)
        occurrence = self._get_occurrence(record, occurrence_id)
        return tuple(
            record.data.occurrences[child_id]
            for child_id in occurrence.child_occurrence_ids
        )

    def get_xcaf_product_metadata(
        self,
        document_id: CadDocumentId,
        product_id: XcafProductId,
    ) -> XcafProductMetadata:
        """Resolve one public product definition."""
        record = self._get_xcaf_record(document_id)
        try:
            return record.data.products[product_id]
        except KeyError as error:
            raise KeyError(f"XCAF product not found: {product_id}") from error

    def get_xcaf_occurrence_metadata(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> XcafOccurrenceMetadata:
        """Resolve one public occurrence."""
        record = self._get_xcaf_record(document_id)
        return self._get_occurrence(record, occurrence_id)

    def get_xcaf_absolute_transform(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> XcafTransform:
        """Return one occurrence's accumulated parent-to-child transform."""
        return self.get_xcaf_occurrence_metadata(
            document_id,
            occurrence_id,
        ).absolute_transform

    def get_xcaf_source_appearance(
        self,
        document_id: CadDocumentId,
        object_id: XcafOccurrenceId | XcafProductId,
    ) -> XcafSourceAppearance:
        """Return STEP source appearance without involving user view state."""
        record = self._get_xcaf_record(document_id)
        if isinstance(object_id, XcafOccurrenceId):
            return self._get_occurrence(record, object_id).source_appearance
        if isinstance(object_id, XcafProductId):
            return self.get_xcaf_product_metadata(
                document_id,
                object_id,
            ).source_appearance
        raise TypeError("XCAF source appearance requires occurrence or product ID")

    def resolve_xcaf_occurrence_shape(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> TopoDS_Shape:
        """Resolve a native absolutely placed occurrence for trusted adapters."""
        record = self._get_xcaf_record(document_id)
        return resolve_occurrence_shape(record.payload, record.data, occurrence_id)

    def resolve_xcaf_presentation_sources(
        self,
        document_id: CadDocumentId,
    ) -> dict[CadObjectId, OcpXcafPresentationSource]:
        """Resolve native XCAF labels/locations for trusted viewer adapters."""
        record = self._get_xcaf_record(document_id)
        return resolve_presentation_sources(record.payload, record.data)

    def release(self, document_id: CadDocumentId) -> None:
        """Remove the record so its native shape can be released."""
        with self._lock:
            record = self._records.pop(document_id, None)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
        if isinstance(record, _XcafDocumentRecord):
            record.data.release()

    def _get_xcaf_record(self, document_id: CadDocumentId) -> _XcafDocumentRecord:
        with self._lock:
            record = self._records.get(document_id)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
        if not isinstance(record, _XcafDocumentRecord):
            raise TypeError(f"CAD document is not XCAF: {document_id}")
        return record

    @staticmethod
    def _get_occurrence(
        record: _XcafDocumentRecord,
        occurrence_id: XcafOccurrenceId,
    ) -> XcafOccurrenceMetadata:
        try:
            return record.data.occurrences[occurrence_id]
        except KeyError as error:
            raise KeyError(f"XCAF occurrence not found: {occurrence_id}") from error
