"""Fail-closed CAM geometry picking adapter tests."""

from uuid import uuid4

import pytest

from hms_cadcam.cad.models import BoundingBox, CadDocumentId, CadGeometryKind, CadObjectId
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey, PersistentCadObjectMap, TopologyPath, TopologyPathVersion,
)
from hms_cadcam.ui.cam_geometry_adapter import GeometryPickError, geometry_reference_from_selection
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode


def _selection(object_id: CadObjectId, mode: SelectionMode = SelectionMode.SOLID) -> SelectionMetadata:
    return SelectionMetadata(CadDocumentId("doc"), "solid:stable-1", mode,
                             BoundingBox(0, 0, 0, 1, 1, 1), object_id)


def test_object_pick_uses_persistent_object_identity_and_source() -> None:
    source_id, object_id = uuid4(), CadObjectId("face-runtime")
    key = PersistentCadObjectKey(source_id, CadGeometryKind.BREP, TopologyPathVersion.V1,
                                 TopologyPath("solid:0123456789abcdef0123456789abcdef"))
    mapping = PersistentCadObjectMap({object_id: key}, {key: object_id})
    reference = geometry_reference_from_selection(_selection(object_id), source_id=source_id,
                                                  persistent_map=mapping)
    assert reference.source_id == source_id
    assert reference.subshape_selector.startswith("hms_body_v1:")
    assert "solid:stable-1" not in reference.subshape_selector


def test_runtime_face_selector_is_never_accepted_for_persistence() -> None:
    source_id, object_id = uuid4(), CadObjectId("face-runtime")
    key = PersistentCadObjectKey(source_id, CadGeometryKind.BREP, TopologyPathVersion.V1,
                                 TopologyPath("solid:0123456789abcdef0123456789abcdef"))
    mapping = PersistentCadObjectMap({object_id: key}, {key: object_id})
    with pytest.raises(GeometryPickError, match="runtime selectors cannot be saved"):
        geometry_reference_from_selection(
            _selection(object_id, SelectionMode.FACE), source_id=source_id,
            persistent_map=mapping,
        )


def test_ambiguous_and_source_mismatch_picks_are_rejected() -> None:
    source_id, object_id = uuid4(), CadObjectId("runtime")
    with pytest.raises(GeometryPickError):
        geometry_reference_from_selection(_selection(object_id), source_id=source_id,
                                          persistent_map=PersistentCadObjectMap({}, {}, 1))
    foreign = PersistentCadObjectKey(uuid4(), CadGeometryKind.BREP, TopologyPathVersion.V1,
                                     TopologyPath("solid:0123456789abcdef0123456789abcdef"))
    with pytest.raises(GeometryPickError):
        geometry_reference_from_selection(_selection(object_id), source_id=source_id,
            persistent_map=PersistentCadObjectMap({object_id: foreign}, {foreign: object_id}))
