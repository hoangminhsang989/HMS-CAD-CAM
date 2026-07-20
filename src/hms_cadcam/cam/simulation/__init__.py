"""Public headless simulation/collision foundation API."""

from .codec import dumps, loads_request, loads_result, request_from_dict, result_from_dict
from .collision import CollisionBackend, CollisionEvidence, CollisionScene, CollisionTarget, CollisionTargetKind, InMemoryAabbBackend, run_collision_analysis
from .coordinates import apply_affine_point, apply_affine_vector, pose_to_world, transform_bounds, wcs_to_world_axis, wcs_to_world_point
from .envelope import EnvelopePrimitive, EnvelopePrimitiveKind, EnvelopeSupport, ToolEnvelope, UnsupportedToolGeometryError, build_tool_envelope
from .model import SIMULATION_ALGORITHM_VERSION, SIMULATION_FORMAT, SIMULATION_VERSION, SimulationIssue, SimulationIssueCategory, SimulationIssueCode, SimulationRequest, SimulationResult, SimulationSamplingPolicy, SimulationStatistics, SimulationStatus
from .sampling import SampledSegment, SampleProvenance, SamplingOutput, SimulationSample, SimulationSamplingError, sample_toolpath
from .service import SimulationComputationToken, SimulationExecution, SimulationPreflightError, SimulationRuntimeService, build_simulation_request

__all__ = [
    "SIMULATION_ALGORITHM_VERSION", "SIMULATION_FORMAT", "SIMULATION_VERSION", "CollisionBackend", "CollisionEvidence", "CollisionScene", "CollisionTarget", "CollisionTargetKind", "EnvelopePrimitive", "EnvelopePrimitiveKind", "EnvelopeSupport", "InMemoryAabbBackend", "SampleProvenance", "SampledSegment", "SamplingOutput", "SimulationComputationToken", "SimulationExecution", "SimulationIssue", "SimulationIssueCategory", "SimulationIssueCode", "SimulationPreflightError", "SimulationRequest", "SimulationResult", "SimulationRuntimeService", "SimulationSample", "SimulationSamplingError", "SimulationSamplingPolicy", "SimulationStatistics", "SimulationStatus", "ToolEnvelope", "UnsupportedToolGeometryError", "apply_affine_point", "apply_affine_vector", "build_simulation_request", "build_tool_envelope", "dumps", "loads_request", "loads_result", "pose_to_world", "request_from_dict", "result_from_dict", "run_collision_analysis", "sample_toolpath", "transform_bounds", "wcs_to_world_axis", "wcs_to_world_point",
]
