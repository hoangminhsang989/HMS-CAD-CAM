"""Fail-closed adapter from CAD viewer selection to persistent CAM references."""

from __future__ import annotations

from uuid import UUID

from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    PersistentCadObjectMap,
    PersistentXcafOccurrenceKey,
)
from hms_cadcam.cam.domain import (
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
    Revision,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode


class GeometryPickError(ValueError):
    """Selection cannot be mapped without guessing."""


def geometry_reference_from_selection(
    selection: SelectionMetadata,
    *,
    source_id: UUID,
    persistent_map: PersistentCadObjectMap,
    source_revision: Revision = Revision(0),
) -> GeometryReference:
    """Create a reference only for one unambiguous, source-matching CAD object."""
    if selection.topology is not SelectionMode.SOLID:
        raise GeometryPickError(
            "Subshape selection requires a dedicated persistent resolver; runtime selectors cannot be saved."
        )
    if selection.object_id is None:
        raise GeometryPickError("Lựa chọn không thuộc một CAD occurrence bền vững.")
    key = persistent_map.by_runtime.get(selection.object_id)
    if key is None:
        raise GeometryPickError("Lựa chọn mơ hồ hoặc đã lỗi thời.")
    if key.source_id != source_id:
        raise GeometryPickError("Lựa chọn thuộc nguồn CAD khác.")
    occurrence_path = None
    if isinstance(key, PersistentXcafOccurrenceKey):
        occurrence_path = str(key.occurrence_path)
        key_payload = (occurrence_path, str(key.product_identity))
    elif isinstance(key, PersistentCadObjectKey):
        key_payload = (str(key.topology_path), str(key.topology_path_version.value))
    else:  # pragma: no cover - closed union defensive guard
        raise GeometryPickError("Kiểu persistent key không được hỗ trợ.")
    fingerprint = GeometryFingerprint.from_payload(
        {"key": key_payload, "topology": selection.topology.value}
    )
    selector = f"hms_body_v1:{fingerprint.digest}"
    return GeometryReference(
        GeometryReferenceId.new(), HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, source_id, GeometryReferenceKind.BODY,
        GeometryRepresentationKind.BREP, fingerprint, source_revision,
        occurrence_path=occurrence_path, subshape_selector=selector,
        hint=f"{selection.topology.value}: {selector}",
    )
