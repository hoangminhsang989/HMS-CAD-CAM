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
    if selection.object_id is None:
        raise GeometryPickError("Lựa chọn không thuộc một CAD occurrence bền vững.")
    key = persistent_map.by_runtime.get(selection.object_id)
    if key is None:
        raise GeometryPickError("Lựa chọn mơ hồ hoặc đã lỗi thời.")
    if key.source_id != source_id:
        raise GeometryPickError("Lựa chọn thuộc nguồn CAD khác.")
    kinds = {
        SelectionMode.SOLID: GeometryReferenceKind.BODY,
        SelectionMode.FACE: GeometryReferenceKind.FACE,
        SelectionMode.EDGE: GeometryReferenceKind.EDGE,
        SelectionMode.VERTEX: GeometryReferenceKind.VERTEX,
    }
    occurrence_path = None
    if isinstance(key, PersistentXcafOccurrenceKey):
        occurrence_path = str(key.occurrence_path)
        key_payload = (occurrence_path, str(key.product_identity))
    elif isinstance(key, PersistentCadObjectKey):
        key_payload = (str(key.topology_path), str(key.topology_path_version.value))
    else:  # pragma: no cover - closed union defensive guard
        raise GeometryPickError("Kiểu persistent key không được hỗ trợ.")
    selector = selection.selection_id.strip()
    if not selector:
        raise GeometryPickError("CAD viewer không cung cấp selector bền vững.")
    fingerprint = GeometryFingerprint.from_payload(
        {"key": key_payload, "selector": selector, "topology": selection.topology.value}
    )
    return GeometryReference(
        GeometryReferenceId.new(), HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, source_id, kinds[selection.topology],
        GeometryRepresentationKind.BREP, fingerprint, source_revision,
        occurrence_path=occurrence_path, subshape_selector=selector,
        hint=f"{selection.topology.value}: {selector}",
    )
