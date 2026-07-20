"""Infrastructure adapters implementing CAM application ports."""

from hms_cadcam.cam.adapters.ocp_planar_face import OcpPlanarFaceResolver
from hms_cadcam.cam.adapters.ocp_contour import OcpContourProfileResolver
from hms_cadcam.cam.adapters.ocp_drilling import OcpDrillingGeometryResolver
from hms_cadcam.cam.adapters.ocp_simulation import (
    FixtureGeometryResolver,
    OcpSimulationCollisionBackend,
    ResolvedFixtureGeometry,
)

__all__ = [
    "FixtureGeometryResolver",
    "OcpContourProfileResolver",
    "OcpDrillingGeometryResolver",
    "OcpPlanarFaceResolver",
    "OcpSimulationCollisionBackend",
    "ResolvedFixtureGeometry",
]
