"""R246 toolpath calculation optimization primitives.

The package is deliberately controller-neutral.  It supplies correctness
boundaries for calculation caching, phase instrumentation, dependency-aware
invalidation, checkpoints and bounded scheduling; strategy generators remain
the authority for geometric output.
"""

from hms_cadcam.cam.optimization.cache import (
    CalculationArtifactStore,
    CacheLookup,
    CacheLookupStatus,
    CacheManifest,
)
from hms_cadcam.cam.optimization.fingerprint import (
    CalculationFingerprint,
    CalculationFingerprintInput,
)
from hms_cadcam.cam.optimization.governor import (
    BackgroundDecision,
    ResourceGovernor,
    ResourcePressure,
)
from hms_cadcam.cam.optimization.invalidation import (
    InvalidationDecision,
    InvalidationMatrix,
    InvalidationScope,
)
from hms_cadcam.cam.optimization.scheduler import deterministic_parallel_map
from hms_cadcam.cam.optimization.timing import (
    CalculationTiming,
    PhaseTiming,
    TimingRecorder,
)
from hms_cadcam.cam.optimization.checkpoint import (
    CheckpointRecord,
    CheckpointStore,
    CheckpointState,
)
from hms_cadcam.cam.optimization.phases import (
    contour_geometry_from_dict,
    contour_geometry_to_dict,
    facing_region_from_dict,
    facing_region_to_dict,
    pocket_geometry_from_dict,
    pocket_geometry_to_dict,
)
from hms_cadcam.cam.optimization.semantic import (
    semantic_toolpath_fingerprint,
    semantic_toolpath_payload,
)
from hms_cadcam.cam.optimization.progress import (
    CamCalculationProgress,
    CamPhaseState,
)

__all__ = [
    "BackgroundDecision",
    "CalculationArtifactStore",
    "CalculationFingerprint",
    "CalculationFingerprintInput",
    "CalculationTiming",
    "CacheLookup",
    "CacheLookupStatus",
    "CacheManifest",
    "CamCalculationProgress",
    "CamPhaseState",
    "CheckpointRecord",
    "CheckpointState",
    "CheckpointStore",
    "InvalidationDecision",
    "InvalidationMatrix",
    "InvalidationScope",
    "PhaseTiming",
    "ResourceGovernor",
    "ResourcePressure",
    "TimingRecorder",
    "deterministic_parallel_map",
    "contour_geometry_from_dict",
    "contour_geometry_to_dict",
    "facing_region_from_dict",
    "facing_region_to_dict",
    "pocket_geometry_from_dict",
    "pocket_geometry_to_dict",
    "semantic_toolpath_fingerprint",
    "semantic_toolpath_payload",
]
