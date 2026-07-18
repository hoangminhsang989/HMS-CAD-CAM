"""Open CASCADE implementation of the product CAD kernel protocol."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.TopoDS import TopoDS_Shape

from hms_cadcam.cad.exceptions import CadImportError
from hms_cadcam.cad.models import (
    BoundingBox,
    CadDocumentId,
    CadDocumentMetadata,
    CadFormat,
    CadImportResult,
    CadKernelStatus,
    TopologyCounts,
)
from hms_cadcam.cad.ocp.importer import OcpImporter, OcpImportPayload
from hms_cadcam.cad.ocp.lifecycle import OcpDocumentStore

NativeReader = Callable[[Path], OcpImportPayload]


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
        metadata = self._documents.add(shape, CadFormat.GENERATED)
        return metadata.document_id

    def import_step(self, path: str | Path) -> CadImportResult:
        """Import STEP while retaining native data behind a document ID."""
        return self._import(path, CadFormat.STEP, self._importer.read_step)

    def import_brep(self, path: str | Path) -> CadImportResult:
        """Import BREP while retaining native data behind a document ID."""
        return self._import(path, CadFormat.BREP, self._importer.read_brep)

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
        return self.get_document_metadata(document_id).topology_counts

    def get_bounding_box(self, document_id: CadDocumentId) -> BoundingBox:
        """Return stored bounds for a document."""
        return self.get_document_metadata(document_id).bounding_box

    def _resolve_shape(self, document_id: CadDocumentId) -> TopoDS_Shape:
        """Resolve native data only for trusted internal OCP adapters."""
        return self._documents.resolve_shape(document_id)

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
            metadata = self._documents.add(payload.shape, cad_format, path)
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
