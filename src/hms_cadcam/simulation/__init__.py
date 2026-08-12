"""Optional machining simulation and digital-verification subsystem.

This package is intentionally absent from the normal CAM, Post, and NC-export
dependency graph.  Import it only after the user explicitly opens the
machining-simulation workspace.
"""

from .contracts import (
    CollisionKind,
    EngineKind,
    GougeStatus,
    OperationCoverage,
    QualityMode,
    ResultState,
    SimulationEvidence,
    SimulationSession,
    StageTiming,
)
from .heightfield import (
    HeightField3AxisEngine,
    HeightFieldResult,
    MaterialRemovalError,
    RemainingStock,
    SimulationEngine,
)
from .invalidation import InvalidationPlan, SimulationDependencyGraph
from .job import IncrementalJobSimulator, JobSimulationResult
from .playback import PlaybackController, PlaybackEvent, PlaybackState, Timeline
from .verification import (
    SurfaceComparison,
    build_evidence,
    compare_target_surface,
    session_from_input,
)

__all__ = [
    "CollisionKind",
    "EngineKind",
    "GougeStatus",
    "HeightField3AxisEngine",
    "HeightFieldResult",
    "InvalidationPlan",
    "IncrementalJobSimulator",
    "JobSimulationResult",
    "MaterialRemovalError",
    "OperationCoverage",
    "PlaybackController",
    "PlaybackEvent",
    "PlaybackState",
    "QualityMode",
    "RemainingStock",
    "ResultState",
    "SimulationDependencyGraph",
    "SimulationEngine",
    "SimulationEvidence",
    "SimulationSession",
    "StageTiming",
    "SurfaceComparison",
    "Timeline",
    "build_evidence",
    "compare_target_surface",
    "session_from_input",
]
