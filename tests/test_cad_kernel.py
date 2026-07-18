"""Product CAD-kernel architecture tests for Stage 4C."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest
from OCP.BRepTools import BRepTools
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer
from OCP.TopoDS import TopoDS_Shape

from hms_cadcam.cad.exceptions import (
    CadDocumentNotFoundError,
    CadKernelUnavailableError,
)
from hms_cadcam.cad.factory import CadKernelFactory
from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.models import CadFormat, CadImportResult
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


def _assert_no_topods(value: object) -> None:
    assert not isinstance(value, TopoDS_Shape)
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
    assert result.metadata.bounding_box.x_max == pytest.approx(40.0)
    assert result.metadata.bounding_box.y_max == pytest.approx(30.0)
    assert result.metadata.bounding_box.z_max == pytest.approx(20.0)
    _assert_no_topods(result)


@pytest.mark.parametrize(
    ("method_name", "file_name"),
    (("import_step", "broken.step"), ("import_brep", "broken.brep")),
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
