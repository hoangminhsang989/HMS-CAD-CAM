"""Owner-thread fixture resolver for Simulation 7C.3 OCP execution."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from hms_cadcam.cad.models import CadDocumentId, CadDocumentTree, CadObjectId
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    PersistentCadObjectMap,
    PersistentXcafOccurrenceKey,
)
from hms_cadcam.cam.adapters.ocp_simulation import ResolvedFixtureGeometry
from hms_cadcam.cam.domain import (
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    LengthUnit,
    Point3,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.toolpath.geometry import Bounds3


@dataclass(frozen=True, slots=True)
class ActiveOcpFixtureResolver:
    """Resolve only shapes retained by the active OCP document owner."""

    kernel: OcpCadKernel
    document_id: CadDocumentId
    source_id: UUID
    persistent_map: PersistentCadObjectMap
    tree: CadDocumentTree
    unit: LengthUnit

    def resolve_fixture(
        self,
        reference: GeometryReference,
    ) -> ResolvedFixtureGeometry:
        if (
            reference.source_id != self.source_id
            or reference.geometry_kind is not GeometryRepresentationKind.BREP
            or reference.kind
            not in {GeometryReferenceKind.BODY, GeometryReferenceKind.OCCURRENCE}
        ):
            raise CamValidationError("Fixture reference does not belong to the active BREP source")
        matches = tuple(
            (key, object_id)
            for key, object_id in self.persistent_map.by_persistent.items()
            if self._matches_reference(key, reference)
        )
        if len(matches) != 1:
            raise CamValidationError("Fixture reference is stale or ambiguous")
        key, object_id = matches[0]
        node = self.tree.find(object_id)
        if node is None or not node.has_presentation:
            raise CamValidationError("Fixture presentation geometry is unavailable")
        shape = self._resolve_owned_shape(key, object_id, node.occurrence_id)
        bounds = node.bounding_box
        return ResolvedFixtureGeometry(
            shape=shape,
            bounds=Bounds3(
                Point3(bounds.x_min, bounds.y_min, bounds.z_min, self.unit),
                Point3(bounds.x_max, bounds.y_max, bounds.z_max, self.unit),
            ),
            geometry_fingerprint=reference.expected_geometry_fingerprint,
            source_id=reference.source_id,
            occurrence_path=reference.occurrence_path,
            source_revision=reference.expected_source_revision,
            match_count=1,
            ownership_verified=True,
        )

    @staticmethod
    def _matches_reference(
        key: PersistentCadObjectKey | PersistentXcafOccurrenceKey,
        reference: GeometryReference,
    ) -> bool:
        if key.source_id != reference.source_id:
            return False
        if isinstance(key, PersistentXcafOccurrenceKey):
            if reference.occurrence_path != str(key.occurrence_path):
                return False
            key_payload = (str(key.occurrence_path), str(key.product_identity))
        else:
            if reference.occurrence_path is not None:
                return False
            key_payload = (str(key.topology_path), str(key.topology_path_version.value))
        fingerprint = GeometryFingerprint.from_payload(
            {"key": key_payload, "topology": "solid"}
        )
        return (
            fingerprint == reference.expected_geometry_fingerprint
            and reference.subshape_selector == f"hms_body_v1:{fingerprint.digest}"
        )

    def _resolve_owned_shape(
        self,
        key: PersistentCadObjectKey | PersistentXcafOccurrenceKey,
        object_id: CadObjectId,
        occurrence_id,
    ):
        if isinstance(key, PersistentXcafOccurrenceKey):
            if occurrence_id is None:
                raise CamValidationError("Fixture occurrence identity is unavailable")
            return self.kernel._resolve_xcaf_occurrence_shape(  # noqa: SLF001
                self.document_id,
                occurrence_id,
            )
        shapes = self.kernel._resolve_presentation_shapes(self.document_id)  # noqa: SLF001
        shape = shapes.get(object_id)
        if shape is None:
            raise CamValidationError("Fixture body is no longer owned by the active document")
        return shape
