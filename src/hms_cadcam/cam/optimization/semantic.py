"""Canonical machining-semantic identity for Toolpath IR artifacts."""

from __future__ import annotations

from typing import Any

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.toolpath import AnyToolpathEvent, ToolpathArtifact
from hms_cadcam.cam.toolpath.codec import diagnostic_to_dict, event_to_dict


def _semantic_event_payload(event: AnyToolpathEvent) -> dict[str, Any]:
    """Return one event without its per-calculation identity."""
    payload = event_to_dict(event)
    payload.pop("event_id")
    return payload


def semantic_toolpath_payload(artifact: ToolpathArtifact) -> dict[str, Any]:
    """Return semantic fields, excluding run IDs, generation and timestamps."""
    if not isinstance(artifact, ToolpathArtifact):
        raise TypeError("Semantic toolpath input is invalid")
    return {
        "format": "HMS_R250_SEMANTIC_TOOLPATH",
        "format_version": 1,
        "source_operation_id": str(artifact.source_operation_id),
        "operation_revision": artifact.operation_revision.to_dict(),
        "coordinate_space": artifact.coordinate_space.value,
        "unit": artifact.unit.value,
        "setup_id": str(artifact.setup_id),
        "wcs_fingerprint": artifact.wcs_fingerprint.to_dict(),
        "tool_assembly_id": str(artifact.tool_assembly_id),
        "tool_assembly_fingerprint": artifact.tool_assembly_fingerprint.to_dict(),
        "machine_id": str(artifact.machine_id) if artifact.machine_id else None,
        "machine_fingerprint": (
            artifact.machine_fingerprint.to_dict() if artifact.machine_fingerprint else None
        ),
        "initial_pose": artifact.initial_pose.to_dict(),
        "events": [_semantic_event_payload(item) for item in artifact.events],
        "diagnostics": [diagnostic_to_dict(item) for item in artifact.diagnostics],
        "completion_status": artifact.completion_status.value,
    }


def semantic_toolpath_fingerprint(artifact: ToolpathArtifact) -> ContentFingerprint:
    """Hash the canonical machining-semantic representation."""
    return ContentFingerprint.from_payload(semantic_toolpath_payload(artifact))
