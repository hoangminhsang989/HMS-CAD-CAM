"""Choose an OCP viewport backend or a controlled unavailable fallback."""

from __future__ import annotations

import logging
from collections.abc import Callable

from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.viewer.backend import CadViewportBackend
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend

BackendConstructor = Callable[[CadKernel], CadViewportBackend]


class CadViewportBackendFactory:
    """Create a renderer compatible with the selected product CAD kernel."""

    @classmethod
    def create(cls, kernel: CadKernel) -> CadViewportBackend:
        status = kernel.get_status()
        if not status.available:
            return UnavailableCadViewportBackend(
                status.error or "CAD kernel is unavailable"
            )
        try:
            constructor = cls._load_ocp_backend()
            return constructor(kernel)
        except (ImportError, OSError, TypeError) as error:
            logging.getLogger(__name__).warning(
                "OCP viewport backend is unavailable; using fallback: %s",
                error,
            )
            return UnavailableCadViewportBackend(error)

    @staticmethod
    def _load_ocp_backend() -> BackendConstructor:
        from hms_cadcam.viewer.ocp import OcpCadViewportBackend

        return OcpCadViewportBackend
