"""Own native document references for the lifetime of one OCP kernel."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from OCP.TopoDS import TopoDS_Shape

from hms_cadcam.cad.exceptions import CadDocumentNotFoundError
from hms_cadcam.cad.models import CadDocumentId, CadDocumentMetadata, CadFormat
from hms_cadcam.cad.ocp.topology import get_bounding_box, get_topology_counts


@dataclass(slots=True)
class _DocumentRecord:
    shape: TopoDS_Shape
    metadata: CadDocumentMetadata


class OcpDocumentStore:
    """Keep all TopoDS references behind opaque product document IDs."""

    def __init__(self) -> None:
        self._records: dict[CadDocumentId, _DocumentRecord] = {}
        self._lock = threading.RLock()

    def add(
        self,
        shape: TopoDS_Shape,
        cad_format: CadFormat,
        source_path: Path | None = None,
    ) -> CadDocumentMetadata:
        """Register one validated shape and return only public metadata."""
        if shape.IsNull():
            raise ValueError("Cannot register a null CAD shape")
        document_id = CadDocumentId(f"ocp:{uuid4().hex}")
        metadata = CadDocumentMetadata(
            document_id=document_id,
            cad_format=cad_format,
            topology_counts=get_topology_counts(shape),
            bounding_box=get_bounding_box(shape),
            source_path=source_path,
        )
        with self._lock:
            self._records[document_id] = _DocumentRecord(shape, metadata)
        return metadata

    def get_metadata(self, document_id: CadDocumentId) -> CadDocumentMetadata:
        """Return metadata for an owned document or raise a controlled error."""
        with self._lock:
            record = self._records.get(document_id)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
        return record.metadata

    def release(self, document_id: CadDocumentId) -> None:
        """Remove the record so its native shape can be released."""
        with self._lock:
            record = self._records.pop(document_id, None)
        if record is None:
            raise CadDocumentNotFoundError(f"CAD document not found: {document_id}")
