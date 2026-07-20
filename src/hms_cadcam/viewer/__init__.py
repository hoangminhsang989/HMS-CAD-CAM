"""Product CAD viewport abstractions and Qt widget."""

from hms_cadcam.viewer.backend import CadViewportBackend
from hms_cadcam.viewer.factory import CadViewportBackendFactory
from hms_cadcam.viewer.models import (
    DisplayMode,
    KeyboardModifier,
    MouseButton,
    SelectionMetadata,
    SelectionMode,
    ViewDirection,
    ViewportStatus,
)
from hms_cadcam.viewer.simulation import (
    SimulationDisplayContext,
    SimulationDisplayPolicy,
    SimulationDisplayRequest,
    SimulationIssueEvidenceSummary,
    SimulationIssueMarker,
    SimulationMarkerKind,
    SimulationPathSegment,
    SimulationPathSemantic,
    SimulationPresentation,
    SimulationPresentationKey,
    SimulationPresentationRegistry,
)

__all__ = [
    "CadViewportBackend",
    "CadViewportBackendFactory",
    "DisplayMode",
    "KeyboardModifier",
    "MouseButton",
    "SelectionMetadata",
    "SelectionMode",
    "SimulationDisplayContext",
    "SimulationDisplayPolicy",
    "SimulationDisplayRequest",
    "SimulationIssueEvidenceSummary",
    "SimulationIssueMarker",
    "SimulationMarkerKind",
    "SimulationPathSegment",
    "SimulationPathSemantic",
    "SimulationPresentation",
    "SimulationPresentationKey",
    "SimulationPresentationRegistry",
    "ViewDirection",
    "ViewportStatus",
]
