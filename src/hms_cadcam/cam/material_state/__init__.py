"""Shared deterministic material-state core for CAM and Simulation.

The core is deliberately UI-free.  It models software-estimated remaining
stock and can be consumed by either foreground CAM or background Simulation.
"""

from .core import (
    MATERIAL_STATE_ENGINE_VERSION,
    CutterEnvelope,
    MaterialRemovalResult,
    MaterialState,
    MaterialStateFingerprint,
    MaterialStatePrecisionPolicy,
    MaterialStateStatus,
    NoRestMaterial,
    calculate_material_state,
    material_state_setup_fingerprint,
)
from .persistence import MaterialStateLoad, MaterialStateLoadStatus, MaterialStateStore

__all__ = [
    "MATERIAL_STATE_ENGINE_VERSION",
    "CutterEnvelope",
    "MaterialRemovalResult",
    "MaterialState",
    "MaterialStateFingerprint",
    "MaterialStatePrecisionPolicy",
    "MaterialStateStatus",
    "MaterialStateLoad",
    "MaterialStateLoadStatus",
    "MaterialStateStore",
    "NoRestMaterial",
    "calculate_material_state",
    "material_state_setup_fingerprint",
]
