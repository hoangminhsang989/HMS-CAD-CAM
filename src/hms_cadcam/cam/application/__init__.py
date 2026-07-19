"""Application services for CAM project integration."""

from hms_cadcam.cam.application.service import CamApplicationService, CamSelection, reconcile_artifacts
from hms_cadcam.cam.application.defaults import basic_mill_resources
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

__all__ = ["CamApplicationService", "CamSelection", "ContourComputeResult", "ContourGenerationError",
           "ContourGenerator", "ContourInputs", "ContourPath", "FacingComputeResult", "FacingGenerationError",
           "FacingGenerator", "FacingInputs", "basic_mill_resources", "offset_contour", "reconcile_artifacts",
           "resolve_box_facing_region", "resolve_profile_in_setup"]
