"""Application services for CAM project integration."""

from hms_cadcam.cam.application.service import CamApplicationService, CamSelection, reconcile_artifacts
from hms_cadcam.cam.application.defaults import (
    basic_boring_resources,
    basic_drilling_resources,
    basic_mill_resources,
    basic_parallel_resources,
    basic_reaming_resources,
    basic_tapping_resources,
)
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    AutomaticParameterValue,
    AutomaticValidationResult,
    CamQualityProfile,
    cam_quality_factor,
)
from hms_cadcam.cam.automatic_drilling import (
    DRILLING_AUTOMATIC_KEYS,
    DRILLING_AUTOMATIC_POLICY_KEY,
    DRILLING_AUTOMATIC_POLICY_VERSION,
    DRILLING_AUTOMATIC_USER_KEYS,
    DrillingAutomaticContext,
    DrillingPatternAnalysis,
    analyze_drilling_pattern,
    merge_drilling_automatic_intent,
    resolve_drilling_automatic_contract,
    validate_drilling_automatic_contract,
)
from hms_cadcam.cam.automatic_contour import (
    CONTOUR_AUTOMATIC_KEYS,
    CONTOUR_AUTOMATIC_POLICY_KEY,
    CONTOUR_AUTOMATIC_POLICY_VERSION,
    CONTOUR_AUTOMATIC_USER_KEYS,
    ContourAutomaticContext,
    ContourAutomaticLeadForm,
    ContourAutomaticLeadPlacement,
    contour_automatic_lead_points,
    reorder_contour_entry,
    resolve_contour_automatic_contract,
)
from hms_cadcam.cam.automatic_facing import (
    FACING_AUTOMATIC_KEYS,
    FACING_AUTOMATIC_POLICY_KEY,
    FACING_AUTOMATIC_POLICY_VERSION,
    FacingAutomaticContext,
    FacingAutomaticVariant,
    resolve_facing_automatic_contract,
)
from hms_cadcam.cam.automatic_pocket import (
    POCKET_AUTOMATIC_KEYS,
    POCKET_AUTOMATIC_POLICY_KEY,
    POCKET_AUTOMATIC_POLICY_VERSION,
    POCKET_AUTOMATIC_SUPPORTED_TOOL_FAMILIES,
    POCKET_AUTOMATIC_USER_KEYS,
    PocketAutomaticContext,
    PocketAutomaticEntryPlacement,
    pocket_automatic_entry_loops,
    pocket_geometric_stepover_target,
    resolve_pocket_automatic_contract,
)
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
    prepare_contour_machining_geometry,
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
from hms_cadcam.cam.application.reaming import (
    ReamingComputeResult,
    ReamingGenerationError,
    ReamingGenerator,
    ReamingHole,
    ReamingInputs,
)
from hms_cadcam.cam.application.boring import (
    BoringComputeResult,
    BoringGenerationError,
    BoringGenerator,
    BoringHole,
    BoringInputs,
)
from hms_cadcam.cam.application.pocket import (
    PocketComputeResult,
    PocketGenerationError,
    PocketGenerator,
    PocketInputs,
    build_pocket_offset_loops,
    pocket_lead_independent_fingerprint,
    pocket_depth_levels,
    prepare_pocket_machining_geometry,
)
from hms_cadcam.cam.application.rest_pocket import (MaterialStateResolution,
    MaterialStateResolutionStatus, RestPocketGenerator, RestPocketInputs,
    material_state_status_vi, resolve_material_state)
from hms_cadcam.cam.application.rest_region import RestRegion, extract_rest_regions, validate_rest_region
from hms_cadcam.cam.application.rest_contour import (
    RestContourFoundation,
    RestContourFoundationInputs,
    RestContourFoundationResult,
    RestMaterialResolution,
    RestMaterialResolutionStatus,
    RestMaterialStateCandidate,
    resolve_rest_material_state,
)

__all__ = ["AUTOMATIC_PARAMETER_CONTRACT_KEY", "AutomaticParameterContract",
           "DRILLING_AUTOMATIC_KEYS", "DRILLING_AUTOMATIC_POLICY_KEY",
           "DRILLING_AUTOMATIC_POLICY_VERSION", "DRILLING_AUTOMATIC_USER_KEYS",
           "DrillingAutomaticContext", "DrillingPatternAnalysis",
           "analyze_drilling_pattern", "merge_drilling_automatic_intent",
           "resolve_drilling_automatic_contract", "validate_drilling_automatic_contract",
           "AutomaticParameterMode", "AutomaticParameterStatus", "AutomaticParameterValue",
           "AutomaticValidationResult", "CamQualityProfile",
           "cam_quality_factor",
           "CONTOUR_AUTOMATIC_KEYS", "CONTOUR_AUTOMATIC_POLICY_KEY",
           "CONTOUR_AUTOMATIC_POLICY_VERSION", "CONTOUR_AUTOMATIC_USER_KEYS",
           "ContourAutomaticContext", "ContourAutomaticLeadForm",
           "ContourAutomaticLeadPlacement", "contour_automatic_lead_points",
           "reorder_contour_entry", "resolve_contour_automatic_contract",
           "FACING_AUTOMATIC_KEYS", "FACING_AUTOMATIC_POLICY_KEY",
           "FACING_AUTOMATIC_POLICY_VERSION", "FacingAutomaticContext",
           "FacingAutomaticVariant", "resolve_facing_automatic_contract",
           "POCKET_AUTOMATIC_KEYS", "POCKET_AUTOMATIC_POLICY_KEY",
           "POCKET_AUTOMATIC_POLICY_VERSION",
           "POCKET_AUTOMATIC_SUPPORTED_TOOL_FAMILIES",
           "POCKET_AUTOMATIC_USER_KEYS", "PocketAutomaticContext",
           "PocketAutomaticEntryPlacement", "pocket_automatic_entry_loops",
           "pocket_geometric_stepover_target", "resolve_pocket_automatic_contract",
           "CamApplicationService", "CamSelection", "ContourComputeResult", "ContourGenerationError",
           "ContourGenerator", "ContourInputs", "ContourPath", "FacingComputeResult", "FacingGenerationError",
           "FacingGenerator", "FacingInputs", "basic_boring_resources", "basic_drilling_resources", "basic_mill_resources", "basic_parallel_resources",
           "basic_reaming_resources",
           "basic_tapping_resources",
           "offset_contour", "prepare_contour_machining_geometry", "reconcile_artifacts",
           "DrillingComputeResult", "DrillingGenerationError", "DrillingGenerator",
           "DrillingGeometryResolver", "DrillingHole", "DrillingInputs",
           "drilling_peck_levels", "TappingComputeResult", "TappingGenerationError",
           "TappingGenerator", "TappingHole", "TappingInputs",
           "ReamingComputeResult", "ReamingGenerationError", "ReamingGenerator",
           "ReamingHole", "ReamingInputs",
           "BoringComputeResult", "BoringGenerationError", "BoringGenerator",
           "BoringHole", "BoringInputs",
           "PocketComputeResult", "PocketGenerationError", "PocketGenerator", "PocketGeometryResolver",
           "PocketInputs", "build_pocket_offset_loops", "pocket_depth_levels",
           "pocket_lead_independent_fingerprint",
           "prepare_pocket_machining_geometry", "MaterialStateResolution", "MaterialStateResolutionStatus", "RestPocketGenerator", "RestPocketInputs", "material_state_status_vi", "resolve_material_state", "RestRegion", "extract_rest_regions", "validate_rest_region", "resolve_box_facing_region",
           "RestContourFoundation", "RestContourFoundationInputs", "RestContourFoundationResult",
           "RestMaterialResolution", "RestMaterialResolutionStatus", "RestMaterialStateCandidate",
           "resolve_rest_material_state",
           "resolve_profile_in_setup"]
