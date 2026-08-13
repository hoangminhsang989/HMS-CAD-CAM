"""Canonical Toolpath IR content fingerprint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hms_cadcam.cam.domain.revision import ContentFingerprint

if TYPE_CHECKING:
    from hms_cadcam.cam.toolpath.model import ToolpathArtifact


def toolpath_content_payload(artifact: "ToolpathArtifact") -> dict[str, Any]:
    """Return content identity excluding artifact ID, token UUID and timestamp."""
    from hms_cadcam.cam.toolpath.codec import diagnostic_to_dict, event_to_dict
    return {
        "schema_version": artifact.schema_version,
        "source_operation_id": str(artifact.source_operation_id),
        "operation_revision": artifact.operation_revision.to_dict(),
        "computation_generation": artifact.computation_token.generation,
        "input_fingerprint": artifact.input_fingerprint.to_dict(),
        "coordinate_space": artifact.coordinate_space.value,
        "unit": artifact.unit.value,
        "setup_id": str(artifact.setup_id),
        "setup_revision": artifact.setup_revision.to_dict(),
        "wcs_fingerprint": artifact.wcs_fingerprint.to_dict(),
        "tool_assembly_id": str(artifact.tool_assembly_id),
        "tool_assembly_fingerprint": artifact.tool_assembly_fingerprint.to_dict(),
        "machine_id": str(artifact.machine_id) if artifact.machine_id else None,
        "machine_fingerprint": artifact.machine_fingerprint.to_dict() if artifact.machine_fingerprint else None,
        "initial_pose": artifact.initial_pose.to_dict(),
        "events": [event_to_dict(item) for item in artifact.events],
        "diagnostics": [diagnostic_to_dict(item) for item in artifact.diagnostics],
        "completion_status": artifact.completion_status.value,
    }


def compute_toolpath_fingerprint(artifact: "ToolpathArtifact") -> ContentFingerprint:
    return ContentFingerprint.from_payload(toolpath_content_payload(artifact))


def material_removal_payload(artifact: "ToolpathArtifact") -> dict[str, Any]:
    """Return only geometry that can change the sampled removed material."""
    from hms_cadcam.cam.toolpath.events import ArcMove, LinearMove, MotionClass

    cutting: list[dict[str, Any]] = []
    for event in artifact.events:
        if not isinstance(event, (LinearMove, ArcMove)):
            continue
        if event.motion_class is not MotionClass.CUTTING:
            continue
        payload: dict[str, Any] = {
            "kind": event.kind.value,
            "start": event.start.to_dict(),
            "end": event.end.to_dict(),
            "motion_class": event.motion_class.value,
        }
        if isinstance(event, ArcMove):
            payload.update(
                center=event.center.to_dict(),
                plane_normal=event.plane_normal.to_dict(),
                sweep_radians=event.sweep_radians,
            )
        cutting.append(payload)
    return {
        "format": "HMS_CAM_MATERIAL_REMOVAL_TOOLPATH",
        "format_version": 1,
        "coordinate_space": artifact.coordinate_space.value,
        "unit": artifact.unit.value,
        "setup_id": str(artifact.setup_id),
        "wcs_fingerprint": artifact.wcs_fingerprint.to_dict(),
        "cutting_moves": cutting,
    }


def compute_material_removal_fingerprint(
    artifact: "ToolpathArtifact",
) -> ContentFingerprint:
    """Fingerprint sampled removal geometry independently from feed metadata."""
    return ContentFingerprint.from_payload(material_removal_payload(artifact))
