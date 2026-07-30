"""Production OCP-surface to immutable WP3-B preview tessellation adapter."""

from __future__ import annotations

from collections.abc import Callable

from hms_cadcam.cam.application.cam3d_preview import Cam3DPreviewMesh
from hms_cadcam.cam.application.cam3d_request import (
    Cam3DCalculationOwnershipKey,
    Cam3DCalculationRequestContract,
)
from hms_cadcam.cam.cam3d.mesh import (
    Cam3DCancelledError,
    Cam3DSurfaceMesher,
    build_calculation_mesh,
)
from hms_cadcam.cam.cam3d.models import (
    Cam3DDiagnostic,
    Cam3DDiagnosticCode,
    Cam3DDiagnosticSeverity,
    Cam3DTolerancePolicy,
)
from hms_cadcam.cam.domain.revision import GeometryFingerprint

_ANGULAR_TOLERANCE_RADIANS = 0.2
_CALCULATION_EPSILON_MM = 1.0e-8
_BOUNDARY_TOLERANCE_MM = 0.001
_CONTACT_TOLERANCE_MM = 0.001


class OcpCam3DPreviewTessellator:
    """Compose the project-owned OCP resolver with canonical mesh validation.

    In production ``surface_mesher`` is ``OcpCam3DSurfaceAdapter``.  The
    structural protocol keeps this adapter testable without manufacturing or
    leaking a native ``TopoDS_Shape`` through the application contract.
    """

    def __init__(
        self,
        surface_mesher: Cam3DSurfaceMesher,
        ownership: Cam3DCalculationOwnershipKey,
    ) -> None:
        if not callable(getattr(surface_mesher, "tessellate", None)):
            raise TypeError("OCP CAM 3D preview surface mesher is invalid")
        if not isinstance(ownership, Cam3DCalculationOwnershipKey):
            raise TypeError("OCP CAM 3D preview ownership is invalid")
        self._surface_mesher = surface_mesher
        self._ownership = ownership

    def tessellate(
        self,
        request: Cam3DCalculationRequestContract,
        cancellation: Callable[[], bool],
    ) -> Cam3DPreviewMesh:
        """Resolve every persistent face and return canonical native-free data."""
        if not isinstance(request, Cam3DCalculationRequestContract):
            raise TypeError("OCP CAM 3D preview request is invalid")
        if not callable(cancellation):
            raise TypeError("OCP CAM 3D preview cancellation callback is invalid")
        if request.ownership != self._ownership:
            raise ValueError("OCP CAM 3D preview request belongs to another owner")
        _checkpoint(cancellation)
        tolerance = preview_tolerance_policy(request)
        surfaces = (
            request.inputs.zone.part
            + request.inputs.zone.check
            + request.inputs.zone.fixture
        )
        fragments = []
        for surface in surfaces:
            _checkpoint(cancellation)
            fragments.append(
                self._surface_mesher.tessellate(
                    surface,
                    tolerance,
                    cancellation,
                )
            )
        _checkpoint(cancellation)
        mesh = build_calculation_mesh(
            tuple(fragments),
            tolerance,
            GeometryFingerprint.from_payload(request.inputs.zone.canonical_payload()),
            cancellation=cancellation,
        )
        _checkpoint(cancellation)
        bounds = mesh.bounding_box
        return Cam3DPreviewMesh(
            tuple((float(item.x), float(item.y), float(item.z)) for item in mesh.vertices),
            mesh.triangle_indices,
            tuple(
                (float(item.x), float(item.y), float(item.z))
                for item in mesh.triangle_normals
            ),
            (
                float(bounds.x_min),
                float(bounds.y_min),
                float(bounds.z_min),
                float(bounds.x_max),
                float(bounds.y_max),
                float(bounds.z_max),
            ),
        )


def preview_tolerance_policy(
    request: Cam3DCalculationRequestContract,
) -> Cam3DTolerancePolicy:
    """Map WP3-A's versioned scalar tolerance to the foundation policy v1."""
    if not isinstance(request, Cam3DCalculationRequestContract):
        raise TypeError("CAM 3D preview tolerance request is invalid")
    return Cam3DTolerancePolicy(
        request.inputs.tolerance_mm,
        _ANGULAR_TOLERANCE_RADIANS,
        _CALCULATION_EPSILON_MM,
        _BOUNDARY_TOLERANCE_MM,
        _CONTACT_TOLERANCE_MM,
    )


def _checkpoint(cancellation: Callable[[], bool]) -> None:
    if cancellation():
        raise Cam3DCancelledError(
            Cam3DDiagnostic(
                Cam3DDiagnosticCode.CANCELLED,
                Cam3DDiagnosticSeverity.WARNING,
                "CAM 3D preview tessellation was cancelled",
            )
        )


__all__ = ["OcpCam3DPreviewTessellator", "preview_tolerance_policy"]
