"""Safe CAD-kernel fallback used when OCP cannot be loaded."""

from __future__ import annotations

from pathlib import Path

from hms_cadcam.cad.exceptions import CadKernelUnavailableError
from hms_cadcam.cad.models import (
    BoundingBox,
    CadDocumentId,
    CadDocumentTree,
    CadDocumentMetadata,
    CadFormat,
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


class UnavailableCadKernel:
    """Preserve application startup while reporting a missing CAD backend."""

    def __init__(self, error: BaseException | str) -> None:
        self._error = str(error) or type(error).__name__

    def is_available(self) -> bool:
        """Return false because no native backend is active."""
        return False

    def get_status(self) -> CadKernelStatus:
        """Return the backend load error for diagnostics and future UI use."""
        return CadKernelStatus(
            available=False,
            backend="unavailable",
            error=self._error,
        )

    def create_box(
        self,
        x_length: float,
        y_length: float,
        z_length: float,
    ) -> CadDocumentId:
        """Reject shape creation with a controlled availability error."""
        del x_length, y_length, z_length
        raise self._unavailable_error()

    def import_step(self, path: str | Path) -> CadImportResult:
        """Return a controlled failed STEP result without loading OCP."""
        return self._failed_import(path, CadFormat.STEP)

    def import_brep(self, path: str | Path) -> CadImportResult:
        """Return a controlled failed BREP result without loading OCP."""
        return self._failed_import(path, CadFormat.BREP)

    def import_iges(self, path: str | Path) -> CadImportResult:
        """Return a controlled failed IGES result without loading OCP."""
        return self._failed_import(path, CadFormat.IGES)

    def import_stl(self, path: str | Path) -> CadImportResult:
        """Return a controlled failed STL result without loading OCP."""
        return self._failed_import(path, CadFormat.STL)

    def release_document(self, document_id: CadDocumentId) -> None:
        """Reject document access when no backend exists."""
        del document_id
        raise self._unavailable_error()

    def get_document_metadata(
        self,
        document_id: CadDocumentId,
    ) -> CadDocumentMetadata:
        """Reject document access when no backend exists."""
        del document_id
        raise self._unavailable_error()

    def get_topology_counts(self, document_id: CadDocumentId) -> TopologyCounts:
        """Reject document access when no backend exists."""
        del document_id
        raise self._unavailable_error()

    def get_bounding_box(self, document_id: CadDocumentId) -> BoundingBox:
        """Reject document access when no backend exists."""
        del document_id
        raise self._unavailable_error()

    def get_document_tree(self, document_id: CadDocumentId) -> CadDocumentTree:
        """Reject document access when no backend exists."""
        del document_id
        raise self._unavailable_error()

    def get_xcaf_assembly_metadata(
        self,
        document_id: CadDocumentId,
    ) -> XcafAssemblyMetadata:
        """Reject XCAF access when no backend exists."""
        del document_id
        raise self._unavailable_error()

    def get_xcaf_root_occurrences(
        self,
        document_id: CadDocumentId,
    ) -> tuple[XcafOccurrenceMetadata, ...]:
        """Reject XCAF access when no backend exists."""
        del document_id
        raise self._unavailable_error()

    def get_xcaf_child_occurrences(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> tuple[XcafOccurrenceMetadata, ...]:
        """Reject XCAF access when no backend exists."""
        del document_id, occurrence_id
        raise self._unavailable_error()

    def get_xcaf_product_metadata(
        self,
        document_id: CadDocumentId,
        product_id: XcafProductId,
    ) -> XcafProductMetadata:
        """Reject XCAF access when no backend exists."""
        del document_id, product_id
        raise self._unavailable_error()

    def get_xcaf_occurrence_metadata(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> XcafOccurrenceMetadata:
        """Reject XCAF access when no backend exists."""
        del document_id, occurrence_id
        raise self._unavailable_error()

    def get_xcaf_absolute_transform(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> XcafTransform:
        """Reject XCAF access when no backend exists."""
        del document_id, occurrence_id
        raise self._unavailable_error()

    def get_xcaf_source_appearance(
        self,
        document_id: CadDocumentId,
        object_id: XcafOccurrenceId | XcafProductId,
    ) -> XcafSourceAppearance:
        """Reject XCAF access when no backend exists."""
        del document_id, object_id
        raise self._unavailable_error()

    def _failed_import(self, path: str | Path, cad_format: CadFormat) -> CadImportResult:
        source_path = Path(path).resolve(strict=False)
        return CadImportResult(
            success=False,
            source_path=source_path,
            detected_format=cad_format,
            document_id=None,
            metadata=None,
            warnings=(),
            errors=(f"CAD kernel is unavailable: {self._error}",),
            elapsed_seconds=0.0,
        )

    def _unavailable_error(self) -> CadKernelUnavailableError:
        return CadKernelUnavailableError(f"CAD kernel is unavailable: {self._error}")
