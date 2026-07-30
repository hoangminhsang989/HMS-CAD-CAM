"""Typed UI adapter from accepted CAM 3D results to the CAD viewport."""

from __future__ import annotations

import logging

from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DPreviewCompletionState,
    Cam3DPreviewResult,
)
from hms_cadcam.cam.application.cam3d_request import Cam3DCalculationOwnershipKey
from hms_cadcam.viewer.cam3d import (
    Cam3DPreviewActorIdentity,
    Cam3DPreviewMeshData,
    Cam3DPreviewOwnership,
    Cam3DPreviewPublication,
    Cam3DPreviewPublicationResult,
    Cam3DPreviewPublicationSource,
)
from hms_cadcam.viewer.widget import CadViewportWidget

logger = logging.getLogger(__name__)


def cam3d_preview_ownership(
    ownership: Cam3DCalculationOwnershipKey,
) -> Cam3DPreviewOwnership:
    """Copy application ownership into the pure viewer contract."""

    if not isinstance(ownership, Cam3DCalculationOwnershipKey):
        raise TypeError("CAM 3D viewport ownership is invalid")
    return Cam3DPreviewOwnership(
        ownership.project_id,
        ownership.document_id,
        ownership.source_id,
        ownership.setup_id,
    )


def cam3d_preview_publication_from_result(
    result: Cam3DPreviewResult,
) -> Cam3DPreviewPublication:
    """Map only an accepted successful native-free result to viewer data."""

    if not isinstance(result, Cam3DPreviewResult):
        raise TypeError("CAM 3D viewport result is invalid")
    if (
        result.state is not Cam3DPreviewCompletionState.SUCCEEDED
        or result.mesh is None
    ):
        raise ValueError("Only a successful CAM 3D preview can be published")
    identity = result.identity
    actor_identity = Cam3DPreviewActorIdentity(
        cam3d_preview_ownership(identity.ownership),
        identity.project_generation,
        str(identity.job_id),
        identity.fingerprint.digest,
        result.cache_key.digest,
        Cam3DPreviewPublicationSource(result.source.value),
    )
    mesh = result.mesh
    return Cam3DPreviewPublication(
        actor_identity,
        Cam3DPreviewMeshData(
            mesh.vertices,
            mesh.triangles,
            mesh.triangle_normals,
            mesh.bounds,
        ),
    )


class Cam3DViewportPreviewSink:
    """Publish accepted meshes without reflection or fabricated success."""

    def __init__(self, viewport: CadViewportWidget) -> None:
        if not isinstance(viewport, CadViewportWidget):
            raise TypeError("CAM 3D viewport sink requires CadViewportWidget")
        self._viewport = viewport

    def publish(self, result: Cam3DPreviewResult) -> bool:
        """Return true only when the real backend owns the requested actor."""

        try:
            publication = cam3d_preview_publication_from_result(result)
        except (TypeError, ValueError):
            return False
        outcome = self._viewport.publish_cam3d_preview(publication)
        if not isinstance(outcome, Cam3DPreviewPublicationResult):
            logger.error("CAM 3D viewport returned an invalid publication outcome")
            return False
        if not outcome.succeeded:
            logger.warning(
                "CAM 3D viewport publication rejected: %s",
                outcome.code.value,
            )
        return outcome.succeeded

    def clear(self, ownership: Cam3DCalculationOwnershipKey) -> None:
        """Clear only the exact ownership; repeated clear remains a no-op."""

        try:
            viewer_ownership = cam3d_preview_ownership(ownership)
        except TypeError:
            return
        outcome = self._viewport.clear_cam3d_preview(viewer_ownership)
        if not outcome.succeeded:
            logger.warning(
                "CAM 3D viewport clear rejected: %s",
                outcome.code.value,
            )


__all__ = [
    "Cam3DViewportPreviewSink",
    "cam3d_preview_ownership",
    "cam3d_preview_publication_from_result",
]