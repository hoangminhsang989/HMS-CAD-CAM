"""Product-facing CAD kernel protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from hms_cadcam.cad.models import (
    BoundingBox,
    CadDocumentId,
    CadDocumentMetadata,
    CadImportResult,
    CadKernelStatus,
    TopologyCounts,
)


@runtime_checkable
class CadKernel(Protocol):
    """Backend-neutral API used by future application services and UI."""

    def is_available(self) -> bool:
        """Return whether this kernel can execute CAD operations."""
        ...

    def get_status(self) -> CadKernelStatus:
        """Return backend availability and diagnostic information."""
        ...

    def create_box(
        self,
        x_length: float,
        y_length: float,
        z_length: float,
    ) -> CadDocumentId:
        """Create and retain a box document."""
        ...

    def import_step(self, path: str | Path) -> CadImportResult:
        """Import a STEP/STP document without exposing its native shape."""
        ...

    def import_brep(self, path: str | Path) -> CadImportResult:
        """Import a BREP document without exposing its native shape."""
        ...

    def release_document(self, document_id: CadDocumentId) -> None:
        """Release all native references held for a document."""
        ...

    def get_document_metadata(
        self,
        document_id: CadDocumentId,
    ) -> CadDocumentMetadata:
        """Return immutable metadata for a retained document."""
        ...

    def get_topology_counts(self, document_id: CadDocumentId) -> TopologyCounts:
        """Return topology counts for a retained document."""
        ...

    def get_bounding_box(self, document_id: CadDocumentId) -> BoundingBox:
        """Return axis-aligned bounds for a retained document."""
        ...
