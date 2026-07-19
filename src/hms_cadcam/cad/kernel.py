"""Product-facing CAD kernel protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from hms_cadcam.cad.models import (
    BoundingBox,
    CadDocumentId,
    CadDocumentTree,
    CadDocumentMetadata,
    CadImportResult,
    CadKernelStatus,
    TopologyCounts,
    XcafAssemblyMetadata,
    XcafOccurrenceId,
    XcafOccurrenceMetadata,
    XcafProductId,
    XcafProductMetadata,
    XcafSourceAppearance,
    XcafTransform,
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

    def import_iges(self, path: str | Path) -> CadImportResult:
        """Import an IGES/IGS document without requiring a solid."""
        ...

    def import_stl(self, path: str | Path) -> CadImportResult:
        """Import an STL triangle mesh without converting triangles to faces."""
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

    def get_document_tree(self, document_id: CadDocumentId) -> CadDocumentTree:
        """Return the bounded topology/display tree for a retained document."""
        ...

    def get_xcaf_assembly_metadata(
        self,
        document_id: CadDocumentId,
    ) -> XcafAssemblyMetadata:
        """Return XCAF product and occurrence indexes for a STEP document."""
        ...

    def get_xcaf_root_occurrences(
        self,
        document_id: CadDocumentId,
    ) -> tuple[XcafOccurrenceMetadata, ...]:
        """Return every root occurrence in stable document-lifetime order."""
        ...

    def get_xcaf_child_occurrences(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> tuple[XcafOccurrenceMetadata, ...]:
        """Return direct children of one occurrence."""
        ...

    def get_xcaf_product_metadata(
        self,
        document_id: CadDocumentId,
        product_id: XcafProductId,
    ) -> XcafProductMetadata:
        """Return one XCAF product definition."""
        ...

    def get_xcaf_occurrence_metadata(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> XcafOccurrenceMetadata:
        """Return one XCAF occurrence."""
        ...

    def get_xcaf_absolute_transform(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> XcafTransform:
        """Return the accumulated transform of one occurrence."""
        ...

    def get_xcaf_source_appearance(
        self,
        document_id: CadDocumentId,
        object_id: XcafOccurrenceId | XcafProductId,
    ) -> XcafSourceAppearance:
        """Return source STEP appearance without user view-state overrides."""
        ...
