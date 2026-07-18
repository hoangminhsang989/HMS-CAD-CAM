"""Select a measurement service without making OCP mandatory at startup."""

from __future__ import annotations

import logging

from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.measurement import MeasurementResult, MeasurementService
from hms_cadcam.cad.models import CadDocumentId


class MeasurementServiceFactory:
    """Create the compatible read-only measurement adapter for one kernel."""

    @staticmethod
    def create(kernel: CadKernel) -> MeasurementService:
        try:
            from hms_cadcam.cad.ocp.kernel import OcpCadKernel
            from hms_cadcam.cad.ocp.measurement import OcpMeasurementService

            if isinstance(kernel, OcpCadKernel):
                return OcpMeasurementService(kernel)
        except (ImportError, OSError) as error:
            logging.getLogger(__name__).warning(
                "OCP measurement service is unavailable; using fallback: %s",
                error,
            )
        return UnavailableMeasurementService()


class UnavailableMeasurementService:
    """Controlled fallback for non-OCP kernels and unavailable native code."""

    def measure_selection(
        self,
        document_id: CadDocumentId,
        selection_id: str,
    ) -> MeasurementResult:
        del document_id, selection_id
        raise RuntimeError("BREP measurement service is unavailable")

    def measure_distance(
        self,
        document_id: CadDocumentId,
        first_selection_id: str,
        second_selection_id: str,
    ) -> MeasurementResult:
        del document_id, first_selection_id, second_selection_id
        raise RuntimeError("BREP measurement service is unavailable")

    def measure_document(self, document_id: CadDocumentId) -> MeasurementResult:
        del document_id
        raise RuntimeError("BREP measurement service is unavailable")
