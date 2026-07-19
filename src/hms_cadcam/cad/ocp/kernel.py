"""Open CASCADE implementation of the product CAD kernel protocol."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.Poly import Poly_Triangulation
from OCP.TopoDS import TopoDS_Shape

from hms_cadcam.cad.exceptions import CadImportError
from hms_cadcam.cad.models import (
    BoundingBox,
    CadDocumentId,
    CadDocumentMetadata,
    CadDocumentTree,
    CadObjectId,
    CadFormat,
    CadGeometryKind,
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
from hms_cadcam.cad.ocp.importer import (
    OcpImporter,
    OcpImportPayload,
    OcpMeshImportPayload,
)
from hms_cadcam.cad.ocp.lifecycle import OcpDocumentStore

NativeReader = Callable[[Path], OcpImportPayload]
NativeMeshReader = Callable[[Path], OcpMeshImportPayload]


class OcpCadKernel:
    """Own OCP shapes while exposing only stable IDs and public metadata."""

    def __init__(self) -> None:
        self._documents = OcpDocumentStore()
        self._importer = OcpImporter()

    def is_available(self) -> bool:
        """Return true after the OCP modules and DLLs loaded successfully."""
        return True

    def get_status(self) -> CadKernelStatus:
        """Return active backend diagnostics without native objects."""
        try:
            backend_version = version("cadquery-ocp-novtk")
        except PackageNotFoundError:
            backend_version = None
        return CadKernelStatus(
            available=True,
            backend="OCP",
            version=backend_version,
        )

    def create_box(
        self,
        x_length: float,
        y_length: float,
        z_length: float,
    ) -> CadDocumentId:
        """Create a valid box and retain its native shape internally."""
        dimensions = (x_length, y_length, z_length)
        if not all(math.isfinite(value) and value > 0.0 for value in dimensions):
            raise ValueError("Box dimensions must be finite and greater than zero")
        shape = BRepPrimAPI_MakeBox(x_length, y_length, z_length).Shape()
        metadata = self._documents.add_brep(shape, CadFormat.GENERATED)
        return metadata.document_id

    def import_step(self, path: str | Path) -> CadImportResult:
        """Import STEP parts or assemblies into an owned XCAF document."""
        started = perf_counter()
        source_path = Path(path).resolve(strict=False)
        try:
            payload = self._importer.read_step(source_path)
            metadata = self._documents.add_xcaf(payload, source_path)
            return CadImportResult(
                success=True,
                source_path=source_path,
                detected_format=CadFormat.STEP,
                document_id=metadata.document_id,
                metadata=metadata,
                warnings=payload.warnings,
                errors=(),
                elapsed_seconds=perf_counter() - started,
            )
        except (CadImportError, OSError, ValueError) as error:
            return self._failed_import(
                source_path,
                CadFormat.STEP,
                str(error),
                started,
            )
        except Exception as error:
            logging.getLogger(__name__).exception(
                "Unexpected OCP failure while importing STEP/XCAF %s",
                source_path,
            )
            return self._failed_import(
                source_path,
                CadFormat.STEP,
                f"Unexpected OCP import failure: {error}",
                started,
            )

    def import_brep(self, path: str | Path) -> CadImportResult:
        """Import BREP while retaining native data behind a document ID."""
        return self._import(path, CadFormat.BREP, self._importer.read_brep)

    def import_iges(self, path: str | Path) -> CadImportResult:
        """Import IGES wire, surface, shell, solid or compound data."""
        return self._import(path, CadFormat.IGES, self._importer.read_iges)

    def import_stl(self, path: str | Path) -> CadImportResult:
        """Import STL as native triangle mesh rather than fabricated BREP."""
        return self._import_mesh(path, CadFormat.STL, self._importer.read_stl)

    def release_document(self, document_id: CadDocumentId) -> None:
        """Release this kernel's native reference for a document."""
        self._documents.release(document_id)

    def get_document_metadata(
        self,
        document_id: CadDocumentId,
    ) -> CadDocumentMetadata:
        """Return immutable OCP-free document metadata."""
        return self._documents.get_metadata(document_id)

    def get_topology_counts(self, document_id: CadDocumentId) -> TopologyCounts:
        """Return stored topology counts for a document."""
        metadata = self.get_document_metadata(document_id)
        if metadata.geometry_kind is not CadGeometryKind.BREP:
            raise TypeError(f"CAD document has no BREP topology: {document_id}")
        assert metadata.topology_counts is not None
        return metadata.topology_counts

    def get_bounding_box(self, document_id: CadDocumentId) -> BoundingBox:
        """Return stored bounds for a document."""
        return self.get_document_metadata(document_id).bounding_box

    def get_document_tree(self, document_id: CadDocumentId) -> CadDocumentTree:
        """Return the immutable topology-only display tree."""
        return self._documents.get_tree(document_id)

    def get_xcaf_assembly_metadata(
        self,
        document_id: CadDocumentId,
    ) -> XcafAssemblyMetadata:
        """Return the assembly index for one retained STEP/XCAF document."""
        return self._documents.get_xcaf_assembly_metadata(document_id)

    def get_xcaf_root_occurrences(
        self,
        document_id: CadDocumentId,
    ) -> tuple[XcafOccurrenceMetadata, ...]:
        """Return every root product occurrence."""
        return self._documents.get_xcaf_root_occurrences(document_id)

    def get_xcaf_child_occurrences(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> tuple[XcafOccurrenceMetadata, ...]:
        """Return direct child occurrences."""
        return self._documents.get_xcaf_child_occurrences(
            document_id,
            occurrence_id,
        )

    def get_xcaf_product_metadata(
        self,
        document_id: CadDocumentId,
        product_id: XcafProductId,
    ) -> XcafProductMetadata:
        """Return metadata for one product definition."""
        return self._documents.get_xcaf_product_metadata(document_id, product_id)

    def get_xcaf_occurrence_metadata(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> XcafOccurrenceMetadata:
        """Return metadata for one placed occurrence."""
        return self._documents.get_xcaf_occurrence_metadata(
            document_id,
            occurrence_id,
        )

    def get_xcaf_absolute_transform(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> XcafTransform:
        """Return one occurrence's absolute assembly transform."""
        return self._documents.get_xcaf_absolute_transform(
            document_id,
            occurrence_id,
        )

    def get_xcaf_source_appearance(
        self,
        document_id: CadDocumentId,
        object_id: XcafOccurrenceId | XcafProductId,
    ) -> XcafSourceAppearance:
        """Return source STEP appearance, never user override state."""
        return self._documents.get_xcaf_source_appearance(document_id, object_id)

    def _resolve_shape(self, document_id: CadDocumentId) -> TopoDS_Shape:
        """Resolve native data only for trusted internal OCP adapters."""
        return self._documents.resolve_shape(document_id)

    def _resolve_triangulation(
        self,
        document_id: CadDocumentId,
    ) -> Poly_Triangulation:
        """Resolve native mesh only for trusted internal OCP adapters."""
        return self._documents.resolve_triangulation(document_id)

    def _resolve_presentation_shapes(
        self,
        document_id: CadDocumentId,
    ) -> dict[CadObjectId, TopoDS_Shape]:
        """Resolve managed BREP leaves only for trusted viewer adapters."""
        return self._documents.resolve_presentation_shapes(document_id)

    def _resolve_xcaf_occurrence_shape(
        self,
        document_id: CadDocumentId,
        occurrence_id: XcafOccurrenceId,
    ) -> TopoDS_Shape:
        """Resolve native occurrence geometry only for trusted OCP adapters."""
        return self._documents.resolve_xcaf_occurrence_shape(
            document_id,
            occurrence_id,
        )

    def _resolve_xcaf_presentation_sources(self, document_id: CadDocumentId):
        """Resolve native XCAF presentation data only for the OCP viewer."""
        return self._documents.resolve_xcaf_presentation_sources(document_id)

    def _import(
        self,
        source_path: str | Path,
        cad_format: CadFormat,
        reader: NativeReader,
    ) -> CadImportResult:
        started = perf_counter()
        path = Path(source_path).resolve(strict=False)
        try:
            payload = reader(path)
            metadata = self._documents.add_brep(payload.shape, cad_format, path)
            return CadImportResult(
                success=True,
                source_path=path,
                detected_format=cad_format,
                document_id=metadata.document_id,
                metadata=metadata,
                warnings=payload.warnings,
                errors=(),
                elapsed_seconds=perf_counter() - started,
            )
        except (CadImportError, OSError, ValueError) as error:
            return self._failed_import(path, cad_format, str(error), started)
        except Exception as error:
            logging.getLogger(__name__).exception(
                "Unexpected OCP failure while importing %s",
                path,
            )
            return self._failed_import(
                path,
                cad_format,
                f"Unexpected OCP import failure: {error}",
                started,
            )

    def _import_mesh(
        self,
        source_path: str | Path,
        cad_format: CadFormat,
        reader: NativeMeshReader,
    ) -> CadImportResult:
        started = perf_counter()
        path = Path(source_path).resolve(strict=False)
        try:
            payload = reader(path)
            metadata = self._documents.add_mesh(
                payload.triangulation,
                cad_format,
                path,
            )
            return CadImportResult(
                success=True,
                source_path=path,
                detected_format=cad_format,
                document_id=metadata.document_id,
                metadata=metadata,
                warnings=payload.warnings,
                errors=(),
                elapsed_seconds=perf_counter() - started,
            )
        except (CadImportError, OSError, ValueError) as error:
            return self._failed_import(path, cad_format, str(error), started)
        except Exception as error:
            logging.getLogger(__name__).exception(
                "Unexpected OCP failure while importing mesh %s",
                path,
            )
            return self._failed_import(
                path,
                cad_format,
                f"Unexpected OCP mesh import failure: {error}",
                started,
            )

    @staticmethod
    def _failed_import(
        path: Path,
        cad_format: CadFormat,
        error: str,
        started: float,
    ) -> CadImportResult:
        return CadImportResult(
            success=False,
            source_path=path,
            detected_format=cad_format,
            document_id=None,
            metadata=None,
            warnings=(),
            errors=(error,),
            elapsed_seconds=perf_counter() - started,
        )
