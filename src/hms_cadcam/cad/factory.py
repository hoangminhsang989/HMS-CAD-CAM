"""CAD-kernel backend selection with a safe unavailable fallback."""

from __future__ import annotations

import logging
from collections.abc import Callable

from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.unavailable import UnavailableCadKernel

KernelConstructor = Callable[[], CadKernel]


class CadKernelFactory:
    """Create the OCP backend when loadable, otherwise preserve startup."""

    @classmethod
    def create(cls) -> CadKernel:
        """Create the preferred backend without changing process or system paths."""
        try:
            constructor = cls._load_ocp_kernel()
            return constructor()
        except (ImportError, OSError) as error:
            logging.getLogger(__name__).warning(
                "OCP CAD kernel is unavailable; using fallback: %s",
                error,
            )
            return UnavailableCadKernel(error)

    @staticmethod
    def _load_ocp_kernel() -> KernelConstructor:
        from hms_cadcam.cad.ocp import OcpCadKernel

        return OcpCadKernel
