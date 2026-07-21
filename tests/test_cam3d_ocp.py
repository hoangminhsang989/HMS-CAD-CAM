"""OCP integration tests for independent CAM 3D calculation tessellation."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from uuid import uuid4

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.BRepTools import BRepTools

from hms_cadcam.cad.models import CadGeometryKind
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cad.persistent_keys import build_persistent_object_map
from hms_cadcam.cam.adapters import OcpCam3DSurfaceAdapter
from hms_cadcam.cam.cam3d import (
    Cam3DCancelledError,
    Cam3DMeshError,
    CamSurfaceRole,
    build_calculation_mesh,
)
from hms_cadcam.cam.domain import Revision
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

from tests.unit._cam3d_fixtures import tolerance


def _adapter(kernel: OcpCadKernel, document_id, *, revision: Revision = Revision(1)):
    source_id, project_id = uuid4(), uuid4()
    tree = kernel.get_document_tree(document_id)
    mapping = build_persistent_object_map(source_id, CadGeometryKind.BREP, tree)
    adapter = OcpCam3DSurfaceAdapter(
        kernel,
        document_id,
        source_id,
        project_id,
        mapping,
        source_revision=revision,
    )
    object_id = tree.presentation_nodes[0].object_id
    return adapter, project_id, object_id


def _selection(kernel, document_id, object_id, face_index: int) -> SelectionMetadata:
    return SelectionMetadata(
        document_id,
        f"{document_id}:face:{face_index}",
        SelectionMode.FACE,
        kernel.get_bounding_box(document_id),
        object_id,
    )


def test_box_face_binding_and_tessellation_are_stable() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(20, 10, 5)
    try:
        adapter, _project_id, object_id = _adapter(kernel, document_id)
        selection = _selection(kernel, document_id, object_id, 1)
        first = adapter.bind_selection(selection, CamSurfaceRole.PART)
        fragment = adapter.tessellate(first, tolerance())
        second = adapter.bind_selection(selection, CamSurfaceRole.PART)
        assert fragment.vertices
        assert fragment.triangles
        assert first.face_identity == second.face_identity
        assert first.geometry.expected_geometry_fingerprint == second.geometry.expected_geometry_fingerprint
    finally:
        kernel.release_document(document_id)


def test_multiple_box_surfaces_build_one_source_mapped_mesh() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(20, 10, 5)
    try:
        adapter, _project_id, object_id = _adapter(kernel, document_id)
        surfaces = tuple(
            adapter.bind_selection(
                _selection(kernel, document_id, object_id, index),
                CamSurfaceRole.PART,
            )
            for index in (1, 2, 3)
        )
        fragments = tuple(adapter.tessellate(item, tolerance()) for item in surfaces)
        source_fingerprint = surfaces[0].geometry.expected_geometry_fingerprint.from_payload(
            {"box": [20, 10, 5]}
        )
        mesh = build_calculation_mesh(fragments, tolerance(), source_fingerprint)
        assert mesh.statistics.surface_count == 3
        assert set(mesh.triangle_sources) == {
            item.geometry.reference_id for item in surfaces
        }
        assert mesh.bounding_box.x_min >= 0.0
        assert mesh.bounding_box.z_max <= 5.0
    finally:
        kernel.release_document(document_id)


def test_curved_cylinder_face_tessellates_with_multiple_normals(tmp_path: Path) -> None:
    source = tmp_path / "cylinder.brep"
    assert BRepTools.Write_s(BRepPrimAPI_MakeCylinder(5, 12).Shape(), str(source))
    kernel = OcpCadKernel()
    imported = kernel.import_brep(source)
    assert imported.success and imported.document_id is not None
    document_id = imported.document_id
    try:
        adapter, _project_id, object_id = _adapter(kernel, document_id)
        curved = adapter.bind_selection(
            _selection(kernel, document_id, object_id, 1), CamSurfaceRole.PART
        )
        fragment = adapter.tessellate(curved, tolerance(0.05))
        mesh = build_calculation_mesh(
            (fragment,),
            tolerance(0.05),
            curved.geometry.expected_geometry_fingerprint,
        )
        normals = {
            (round(item.x, 3), round(item.y, 3), round(item.z, 3))
            for item in mesh.triangle_normals
        }
        assert len(normals) > 2
    finally:
        kernel.release_document(document_id)


def test_stale_deleted_or_foreign_surface_fails_closed() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(10, 10, 10)
    try:
        adapter, _project_id, object_id = _adapter(kernel, document_id)
        surface = adapter.bind_selection(
            _selection(kernel, document_id, object_id, 1), CamSurfaceRole.PART
        )
        stale_geometry = dataclasses.replace(
            surface.geometry, expected_source_revision=Revision(2)
        )
        with pytest.raises(Cam3DMeshError) as captured:
            adapter.tessellate(dataclasses.replace(surface, geometry=stale_geometry), tolerance())
        assert captured.value.diagnostic.code.value == "cam3d.surface_stale"
        with pytest.raises(Cam3DMeshError):
            adapter.tessellate(dataclasses.replace(surface, project_id=uuid4()), tolerance())
        missing_geometry = dataclasses.replace(
            surface.geometry,
            subshape_selector=(surface.geometry.subshape_selector or "")[:-1] + "0",
        )
        with pytest.raises(Cam3DMeshError):
            adapter.tessellate(dataclasses.replace(surface, geometry=missing_geometry), tolerance())
    finally:
        kernel.release_document(document_id)


def test_ocp_tessellation_cancellation() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(10, 10, 10)
    try:
        adapter, _project_id, object_id = _adapter(kernel, document_id)
        surface = adapter.bind_selection(
            _selection(kernel, document_id, object_id, 1), CamSurfaceRole.PART
        )
        with pytest.raises(Cam3DCancelledError):
            adapter.tessellate(surface, tolerance(), cancellation=lambda: True)
    finally:
        kernel.release_document(document_id)
