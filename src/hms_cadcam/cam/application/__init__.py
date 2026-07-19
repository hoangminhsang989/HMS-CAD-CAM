"""Application services for CAM project integration."""

from hms_cadcam.cam.application.service import CamApplicationService, CamSelection, reconcile_artifacts
from hms_cadcam.cam.application.defaults import basic_mill_resources
from hms_cadcam.cam.application.facing import (
    FacingComputeResult, FacingGenerationError, FacingGenerator, FacingInputs, resolve_box_facing_region,
)

__all__ = ["CamApplicationService", "CamSelection", "FacingComputeResult", "FacingGenerationError", "FacingGenerator",
           "FacingInputs", "basic_mill_resources", "reconcile_artifacts", "resolve_box_facing_region"]
