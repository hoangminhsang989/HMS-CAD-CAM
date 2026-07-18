"""Product CAD-kernel architecture tests for Stage 4C."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from hashlib import sha256
from pathlib import Path

import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.IGESControl import IGESControl_Writer
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer
from OCP.StlAPI import StlAPI_Writer
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

from hms_cadcam.cad.exceptions import (
    CadDocumentNotFoundError,
    CadKernelUnavailableError,
)
from hms_cadcam.cad.factory import CadKernelFactory
from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.models import (
    CadFormat,
    CadGeometryKind,
    CadImportResult,
    CadObjectKind,
    CadUnits,
)
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cad.ocp.importer import OcpImportPayload
from hms_cadcam.cad.unavailable import UnavailableCadKernel


def _write_step(path: Path) -> None:
    shape = BRepPrimAPI_MakeBox(40.0, 30.0, 20.0).Shape()
    writer = STEPControl_Writer()
    assert (
        writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
        == IFSelect_ReturnStatus.IFSelect_RetDone
    )
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone


def _write_brep(path: Path) -> None:
    shape = BRepPrimAPI_MakeBox(40.0, 30.0, 20.0).Shape()
    assert BRepTools.Write_s(shape, str(path))


def _write_multi_solid_brep(path: Path) -> None:
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape())
    builder.Add(compound, BRepPrimAPI_MakeBox(6.0, 7.0, 8.0).Shape())
    assert BRepTools.Write_s(compound, str(path))


def _write_iges(path: Path, *, surface: bool = False) -> None:
    if surface:
        shape = BRepBuilderAPI_MakeFace(
            gp_Pln(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)),
            -20.0,
            20.0,
            -15.0,
            15.0,
        ).Shape()
        mode = 0
    else:
        shape = BRepPrimAPI_MakeBox(40.0, 30.0, 20.0).Shape()
        mode = 1
    writer = IGESControl_Writer("MM", mode)
    assert writer.AddShape(shape)
    assert writer.Write(str(path))


def _write_stl(path: Path, *, ascii_mode: bool) -> None:
    shape = BRepPrimAPI_MakeBox(40.0, 30.0, 20.0).Shape()
    BRepMesh_IncrementalMesh(shape, 0.1)
    writer = StlAPI_Writer()
    writer.ASCIIMode = ascii_mode
    assert writer.Write(shape, str(path))


def _assert_no_topods(value: object) -> None:
    assert not isinstance(value, TopoDS_Shape)
    assert not type(value).__module__.startswith("OCP")
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _assert_no_topods(getattr(value, field.name))
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_no_topods(key)
            _assert_no_topods(item)
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            _assert_no_topods(item)


def test_factory_creates_ocp_kernel_when_available() -> None:
    kernel = CadKernelFactory.create()
    assert isinstance(kernel, OcpCadKernel)
    assert isinstance(kernel, CadKernel)
    assert kernel.is_available()
    assert kernel.get_status().available


def test_factory_falls_back_when_ocp_import_fails(monkeypatch) -> None:
    def fail_load():
        raise ImportError("simulated OCP DLL failure")

    monkeypatch.setattr(
        CadKernelFactory,
        "_load_ocp_kernel",
        staticmethod(fail_load),
    )
    kernel = CadKernelFactory.create()
    assert isinstance(kernel, UnavailableCadKernel)
    assert not kernel.is_available()
    assert "simulated OCP DLL failure" in (kernel.get_status().error or "")
    result = kernel.import_step("missing.step")
    assert not result.success
    assert "unavailable" in result.errors[0]
    with pytest.raises(CadKernelUnavailableError):
        kernel.create_box(1.0, 1.0, 1.0)


def test_create_box_returns_valid_id_metadata_counts_and_bounds() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(40.0, 30.0, 20.0)
    metadata = kernel.get_document_metadata(document_id)
    assert metadata.document_id == document_id
    assert metadata.cad_format is CadFormat.GENERATED
    assert metadata.topology_counts.solids == 1
    assert metadata.topology_counts.faces == 6
    assert metadata.topology_counts.edges == 12
    bounds = metadata.bounding_box
    assert (bounds.x_min, bounds.y_min, bounds.z_min) == pytest.approx(
        (0.0, 0.0, 0.0), abs=1.0e-6
    )
    assert (bounds.x_max, bounds.y_max, bounds.z_max) == pytest.approx(
        (40.0, 30.0, 20.0), abs=1.0e-6
    )
    assert kernel.get_topology_counts(document_id) == metadata.topology_counts
    assert kernel.get_bounding_box(document_id) == metadata.bounding_box


def test_single_brep_has_one_managed_shape_without_face_edge_vertex_nodes() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(4.0, 5.0, 6.0)
    tree = kernel.get_document_tree(document_id)
    nodes = tree.root.walk()
    assert len(tree.presentation_nodes) == 1
    assert tree.presentation_nodes[0].kind is CadObjectKind.SOLID
    assert all(
        node.kind.value not in {"face", "edge", "vertex"} for node in nodes
    )
    assert kernel.get_document_tree(document_id) == tree
    _assert_no_topods(tree)


def test_multi_solid_brep_builds_bounded_management_tree(tmp_path: Path) -> None:
    source = tmp_path / "two_solids.brep"
    _write_multi_solid_brep(source)
    kernel = OcpCadKernel()
    result = kernel.import_brep(source)
    assert result.document_id is not None
    tree = kernel.get_document_tree(result.document_id)
    kinds = tuple(node.kind for node in tree.root.walk())
    assert CadObjectKind.COMPOUND in kinds
    assert kinds.count(CadObjectKind.SOLID) == 2
    assert len(tree.presentation_nodes) == 2
    assert len(tree.root.walk()) == 4


def test_stl_tree_contains_exactly_one_mesh_node(tmp_path: Path) -> None:
    source = tmp_path / "one_mesh.stl"
    _write_stl(source, ascii_mode=True)
    kernel = OcpCadKernel()
    result = kernel.import_stl(source)
    assert result.document_id is not None
    tree = kernel.get_document_tree(result.document_id)
    assert len(tree.root.children) == 1
    assert tree.root.children[0].kind is CadObjectKind.MESH
    assert tree.presentation_nodes == (tree.root.children[0],)


def test_step_import_returns_public_result_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "box.step"
    _write_step(source)
    original = source.read_bytes()

    kernel = OcpCadKernel()
    result = kernel.import_step(source)

    assert result.success
    assert result.document_id is not None
    assert result.metadata is not None
    assert result.detected_format is CadFormat.STEP
    assert result.metadata.geometry_kind is CadGeometryKind.BREP
    assert result.metadata.mesh_statistics is None
    assert result.metadata.topology_counts is not None
    assert result.metadata.topology_counts.solids == 1
    assert result.metadata.topology_counts.faces == 6
    assert result.metadata.topology_counts.edges == 12
    assert source.read_bytes() == original
    _assert_no_topods(result)


def test_brep_import_returns_valid_metadata(tmp_path: Path) -> None:
    source = tmp_path / "box.brep"
    _write_brep(source)

    kernel = OcpCadKernel()
    result = kernel.import_brep(source)

    assert result.success
    assert result.document_id is not None
    assert result.metadata is not None
    assert result.detected_format is CadFormat.BREP
    assert result.metadata.geometry_kind is CadGeometryKind.BREP
    assert result.metadata.mesh_statistics is None
    assert result.metadata.topology_counts is not None
    assert result.metadata.bounding_box.x_max == pytest.approx(40.0)
    assert result.metadata.bounding_box.y_max == pytest.approx(30.0)
    assert result.metadata.bounding_box.z_max == pytest.approx(20.0)
    _assert_no_topods(result)


@pytest.mark.parametrize("surface", (False, True), ids=("solid", "surface"))
def test_iges_import_accepts_solid_and_non_solid_and_preserves_source(
    tmp_path: Path,
    surface: bool,
) -> None:
    source = tmp_path / ("surface.igs" if surface else "solid.iges")
    _write_iges(source, surface=surface)
    original_hash = sha256(source.read_bytes()).digest()

    result = OcpCadKernel().import_iges(source)

    assert result.success
    assert result.metadata is not None
    assert result.detected_format is CadFormat.IGES
    assert result.metadata.geometry_kind is CadGeometryKind.BREP
    assert result.metadata.mesh_statistics is None
    assert result.metadata.topology_counts is not None
    if surface:
        assert result.metadata.topology_counts.solids == 0
        assert result.metadata.topology_counts.faces >= 1
    else:
        assert result.metadata.topology_counts.solids >= 1
    assert sha256(source.read_bytes()).digest() == original_hash
    _assert_no_topods(result)


@pytest.mark.parametrize("ascii_mode", (True, False), ids=("ascii", "binary"))
def test_stl_import_is_triangle_mesh_with_unknown_units_and_preserves_source(
    tmp_path: Path,
    ascii_mode: bool,
) -> None:
    source = tmp_path / ("box_ascii.stl" if ascii_mode else "box_binary.stl")
    _write_stl(source, ascii_mode=ascii_mode)
    original_hash = sha256(source.read_bytes()).digest()

    result = OcpCadKernel().import_stl(source)

    assert result.success
    assert result.metadata is not None
    assert result.detected_format is CadFormat.STL
    assert result.metadata.geometry_kind is CadGeometryKind.TRIANGLE_MESH
    assert result.metadata.units is CadUnits.UNKNOWN
    assert result.metadata.topology_counts is None
    assert result.metadata.mesh_statistics is not None
    assert result.metadata.mesh_statistics.vertices == 8
    assert result.metadata.mesh_statistics.triangles == 12
    assert sha256(source.read_bytes()).digest() == original_hash
    _assert_no_topods(result)


@pytest.mark.parametrize(
    ("method_name", "file_name"),
    (
        ("import_step", "broken.step"),
        ("import_brep", "broken.brep"),
        ("import_iges", "broken.iges"),
        ("import_stl", "broken.stl"),
    ),
)
def test_corrupt_file_returns_controlled_failure(
    tmp_path: Path,
    method_name: str,
    file_name: str,
) -> None:
    source = tmp_path / file_name
    source.write_text("not a CAD model", encoding="utf-8")
    kernel = OcpCadKernel()
    result = getattr(kernel, method_name)(source)
    assert not result.success
    assert result.document_id is None
    assert result.metadata is None
    assert result.errors
    _assert_no_topods(result)


def test_missing_file_returns_controlled_failure(tmp_path: Path) -> None:
    result = OcpCadKernel().import_step(tmp_path / "missing.step")
    assert not result.success
    assert "does not exist" in result.errors[0]


@pytest.mark.parametrize(
    ("method_name", "file_name"),
    (("import_iges", "empty.igs"), ("import_stl", "empty.stl")),
)
def test_empty_iges_and_stl_return_controlled_failure(
    tmp_path: Path,
    method_name: str,
    file_name: str,
) -> None:
    source = tmp_path / file_name
    source.write_bytes(b"")
    result = getattr(OcpCadKernel(), method_name)(source)
    assert not result.success
    assert result.document_id is None
    assert "empty" in result.errors[0].lower()


def test_truncated_binary_stl_returns_controlled_failure(tmp_path: Path) -> None:
    source = tmp_path / "truncated_binary.stl"
    header_and_count = bytearray(84)
    header_and_count[80:84] = (1).to_bytes(4, "little")
    source.write_bytes(header_and_count)
    original_hash = sha256(source.read_bytes()).digest()

    result = OcpCadKernel().import_stl(source)

    assert not result.success
    assert result.document_id is None
    assert result.metadata is None
    assert result.errors
    assert sha256(source.read_bytes()).digest() == original_hash


def test_null_shape_is_rejected_as_failed_import(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "placeholder.brep"
    source.write_text("non-empty", encoding="utf-8")
    kernel = OcpCadKernel()
    monkeypatch.setattr(
        kernel._importer,
        "read_brep",
        lambda _path: OcpImportPayload(TopoDS_Shape()),
    )
    result = kernel.import_brep(source)
    assert not result.success
    assert result.document_id is None
    assert "null" in result.errors[0]


def test_release_document_removes_native_reference() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(1.0, 2.0, 3.0)
    kernel.release_document(document_id)
    with pytest.raises(CadDocumentNotFoundError):
        kernel.get_document_metadata(document_id)


def test_two_documents_are_independent_and_ids_are_stable() -> None:
    kernel = OcpCadKernel()
    first = kernel.create_box(1.0, 2.0, 3.0)
    second = kernel.create_box(4.0, 5.0, 6.0)
    assert first != second
    assert kernel.get_document_metadata(first).document_id == first
    assert kernel.get_document_metadata(first).document_id == first
    kernel.release_document(first)
    assert kernel.get_bounding_box(second).x_max == pytest.approx(4.0)


def test_failed_import_does_not_remove_existing_document(tmp_path: Path) -> None:
    kernel = OcpCadKernel()
    existing = kernel.create_box(7.0, 8.0, 9.0)
    result = kernel.import_brep(tmp_path / "missing.brep")
    assert not result.success
    assert kernel.get_document_metadata(existing).document_id == existing


def test_public_import_result_and_models_contain_no_topods(tmp_path: Path) -> None:
    result = CadImportResult(
        success=False,
        source_path=tmp_path / "broken.step",
        detected_format=CadFormat.STEP,
        document_id=None,
        metadata=None,
        warnings=(),
        errors=("broken",),
        elapsed_seconds=0.0,
    )
    _assert_no_topods(result)
