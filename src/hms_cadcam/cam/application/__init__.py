"""Application services for CAM project integration."""

from hms_cadcam.cam.application.service import CamApplicationService, CamSelection, reconcile_artifacts
from hms_cadcam.cam.application.defaults import basic_drilling_resources, basic_mill_resources
from hms_cadcam.cam.application.facing import (
    FacingComputeResult, FacingGenerationError, FacingGenerator, FacingInputs, resolve_box_facing_region,
)
from hms_cadcam.cam.application.contour import (
    ContourComputeResult,
    ContourGenerationError,
    ContourGenerator,
    ContourInputs,
    ContourPath,
    offset_contour,
    resolve_profile_in_setup,
)
from hms_cadcam.cam.application.pocket_geometry import PocketGeometryResolver
from hms_cadcam.cam.application.drilling_geometry import DrillingGeometryResolver
from hms_cadcam.cam.application.drilling import (
    DrillingComputeResult,
    DrillingGenerationError,
    DrillingGenerator,
    DrillingHole,
    DrillingInputs,
    drilling_peck_levels,
)
from hms_cadcam.cam.application.tapping import (
    TappingComputeResult,
    TappingGenerationError,
    TappingGenerator,
    TappingHole,
    TappingInputs,
)
from hms_cadcam.cam.application.pocket import (
    PocketComputeResult,
    PocketGenerationError,
    PocketGenerator,
    PocketInputs,
    build_pocket_offset_loops,
    pocket_depth_levels,
)

__all__ = ["CamApplicationService", "CamSelection", "ContourComputeResult", "ContourGenerationError",
           "ContourGenerator", "ContourInputs", "ContourPath", "FacingComputeResult", "FacingGenerationError",
           "FacingGenerator", "FacingInputs", "basic_drilling_resources", "basic_mill_resources",
           "offset_contour", "reconcile_artifacts",
           "DrillingComputeResult", "DrillingGenerationError", "DrillingGenerator",
           "DrillingGeometryResolver", "DrillingHole", "DrillingInputs",
           "drilling_peck_levels", "TappingComputeResult", "TappingGenerationError",
           "TappingGenerator", "TappingHole", "TappingInputs",
           "PocketComputeResult", "PocketGenerationError", "PocketGenerator", "PocketGeometryResolver",
           "PocketInputs", "build_pocket_offset_loops", "pocket_depth_levels", "resolve_box_facing_region",
           "resolve_profile_in_setup"]
