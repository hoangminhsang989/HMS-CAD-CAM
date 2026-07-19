"""Product-domain STEP/XCAF model and document-store tests for Stage 6A.2."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

from hms_cadcam.cad.exceptions import CadDocumentNotFoundError
from hms_cadcam.cad.models import (
    CadObjectKind,
    CadDocumentKind,
    XcafColor,
    XcafNameSource,
    XcafNodeRole,
    XcafOccurrenceId,
    XcafProductId,
    XcafSourceAppearance,
)
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cad.ocp.topology import get_bounding_box
from spikes.xcaf_step.fixture import XcafFixtureExpectations, write_xcaf_step_fixture


@pytest.fixture
def imported_assembly(
    tmp_path: Path,
) -> tuple[XcafFixtureExpectations, OcpCadKernel, object]:
    source = tmp_path / "product-assembly.step"
    expected = write_xcaf_step_fixture(source)
    kernel = OcpCadKernel()
    result = kernel.import_step(source)
    assert result.success and result.document_id is not None
    return expected, kernel, result.document_id


def test_step_assembly_document_kind_and_root_occurrence(
    imported_assembly: tuple[XcafFixtureExpectations, OcpCadKernel, object],
) -> None:
    expected, kernel, document_id = imported_assembly
    metadata = kernel.get_document_metadata(document_id)
    assembly = kernel.get_xcaf_assembly_metadata(document_id)
    roots = kernel.get_xcaf_root_occurrences(document_id)

    assert metadata.document_kind is CadDocumentKind.XCAF_ASSEMBLY
    assert assembly.document_id == document_id
    assert len(roots) == 1
    assert roots[0].role is XcafNodeRole.ASSEMBLY
    assert roots[0].name == expected.root_product_name
    assert roots[0].name_source is XcafNameSource.PRODUCT
    assert assembly.root_occurrence_ids == (roots[0].occurrence_id,)


def test_plain_step_part_remains_supported_as_xcaf_part(tmp_path: Path) -> None:
    source = tmp_path / "plain-part.step"
    _write_plain_step(source)
    kernel = OcpCadKernel()

    result = kernel.import_step(source)

    assert result.success and result.document_id is not None
    assert result.metadata is not None
    assert result.metadata.document_kind is CadDocumentKind.XCAF_PART
    roots = kernel.get_xcaf_root_occurrences(result.document_id)
    assert len(roots) == 1
    assert roots[0].role is XcafNodeRole.PART
    assert roots[0].name.strip()
    assert roots[0].child_occurrence_ids == ()
    assert kernel.get_document_tree(result.document_id).presentation_nodes


def test_nested_assembly_repeated_product_and_transforms(
    imported_assembly: tuple[XcafFixtureExpectations, OcpCadKernel, object],
) -> None:
    expected, kernel, document_id = imported_assembly
    root = kernel.get_xcaf_root_occurrences(document_id)[0]
    children = kernel.get_xcaf_child_occurrences(document_id, root.occurrence_id)
    repeated_product = next(
        product_id
        for product_id in kernel.get_xcaf_assembly_metadata(document_id).product_ids
        if kernel.get_xcaf_product_metadata(document_id, product_id).name
        == expected.repeated_product_name
    )
    repeated = sorted(
        (item for item in children if item.product_id == repeated_product),
        key=lambda item: item.absolute_transform.translation[0],
    )

    assert len(repeated) == 2
    assert repeated[0].occurrence_id != repeated[1].occurrence_id
    assert repeated[0].absolute_transform.translation == pytest.approx((10, 0, 0))
    assert repeated[1].absolute_transform.translation == pytest.approx((40, 0, 0))
    assert repeated[0].name == expected.first_occurrence_name
    assert repeated[0].name_source is XcafNameSource.OCCURRENCE
    assert repeated[1].name == expected.repeated_product_name
    assert repeated[1].name_source is XcafNameSource.PRODUCT

    nested = next(item for item in children if item.role is XcafNodeRole.ASSEMBLY)
    nested_child = kernel.get_xcaf_child_occurrences(
        document_id,
        nested.occurrence_id,
    )[0]
    assert nested.absolute_transform.translation == pytest.approx((0, 50, 0))
    assert nested_child.local_transform.translation == pytest.approx((0, 0, 20))
    assert nested_child.absolute_transform == nested.absolute_transform.compose(
        nested_child.local_transform
    )
    assert kernel.get_xcaf_absolute_transform(
        document_id,
        nested_child.occurrence_id,
    ).translation == pytest.approx((0, 50, 20))


def test_internal_occurrence_shape_resolver_uses_absolute_transform(
    imported_assembly: tuple[XcafFixtureExpectations, OcpCadKernel, object],
) -> None:
    _expected, kernel, document_id = imported_assembly
    root = kernel.get_xcaf_root_occurrences(document_id)[0]
    nested = next(
        item
        for item in kernel.get_xcaf_child_occurrences(document_id, root.occurrence_id)
        if item.role is XcafNodeRole.ASSEMBLY
    )
    leaf = kernel.get_xcaf_child_occurrences(document_id, nested.occurrence_id)[0]

    shape = kernel._resolve_xcaf_occurrence_shape(document_id, leaf.occurrence_id)
    bounds = get_bounding_box(shape)

    assert (bounds.x_min, bounds.y_min, bounds.z_min) == pytest.approx(
        (0, 50, 20),
        abs=1e-6,
    )
    assert (bounds.x_max, bounds.y_max, bounds.z_max) == pytest.approx(
        (4, 55, 27),
        abs=1e-6,
    )


def test_source_appearance_product_occurrence_and_subshape(
    imported_assembly: tuple[XcafFixtureExpectations, OcpCadKernel, object],
) -> None:
    expected, kernel, document_id = imported_assembly
    assembly = kernel.get_xcaf_assembly_metadata(document_id)
    product = next(
        kernel.get_xcaf_product_metadata(document_id, product_id)
        for product_id in assembly.product_ids
        if kernel.get_xcaf_product_metadata(document_id, product_id).name
        == expected.repeated_product_name
    )
    occurrence = next(
        kernel.get_xcaf_occurrence_metadata(document_id, occurrence_id)
        for occurrence_id in assembly.occurrence_ids
        if kernel.get_xcaf_occurrence_metadata(document_id, occurrence_id).name
        == expected.first_occurrence_name
    )

    _assert_color(
        kernel.get_xcaf_source_appearance(
            document_id,
            product.product_id,
        ).surface_color,
        expected.repeated_product_surface_color,
    )
    _assert_color(
        kernel.get_xcaf_source_appearance(
            document_id,
            occurrence.occurrence_id,
        ).surface_color,
        expected.first_occurrence_color,
    )
    assert product.subshape_appearances
    _assert_color(
        product.subshape_appearances[0].source_appearance.surface_color,
        expected.subshape_surface_color,
    )
    assert "override" not in {field.name for field in fields(XcafSourceAppearance)}


def test_xcaf_tree_uses_distinct_occurrence_objects_and_nested_hierarchy(
    imported_assembly: tuple[XcafFixtureExpectations, OcpCadKernel, object],
) -> None:
    expected, kernel, document_id = imported_assembly
    tree = kernel.get_document_tree(document_id)
    assert len(tree.root.children) == 1
    root = tree.root.children[0]
    assert root.kind is CadObjectKind.ASSEMBLY
    assert root.label == expected.root_product_name
    assert not root.has_presentation
    repeated = [
        node for node in root.children if node.product_name == expected.repeated_product_name
    ]
    assert len(repeated) == 2
    assert repeated[0].object_id != repeated[1].object_id
    assert repeated[0].occurrence_id != repeated[1].occurrence_id
    assert all(node.kind is CadObjectKind.PART for node in repeated)
    assert all(node.has_presentation for node in repeated)
    assert sorted(
        node.absolute_transform.translation[0] for node in repeated
    ) == pytest.approx([10.0, 40.0])
    nested = next(node for node in root.children if node.kind is CadObjectKind.ASSEMBLY)
    assert len(nested.children) == 1
    assert nested.children[0].absolute_transform.translation == pytest.approx(
        (0.0, 50.0, 20.0)
    )
    assert len(tree.presentation_nodes) == 3
    _assert_public_value(tree)


def test_xcaf_presentation_shapes_are_absolute_and_source_appearance_is_effective(
    imported_assembly: tuple[XcafFixtureExpectations, OcpCadKernel, object],
) -> None:
    expected, kernel, document_id = imported_assembly
    tree = kernel.get_document_tree(document_id)
    repeated = sorted(
        (
            node
            for node in tree.presentation_nodes
            if node.product_name == expected.repeated_product_name
        ),
        key=lambda node: node.absolute_transform.translation[0],
    )
    native_shapes = kernel._resolve_presentation_shapes(document_id)
    first_bounds = get_bounding_box(native_shapes[repeated[0].object_id])
    second_bounds = get_bounding_box(native_shapes[repeated[1].object_id])
    assert first_bounds.x_min == pytest.approx(10.0)
    assert second_bounds.x_min == pytest.approx(40.0)
    _assert_color(
        repeated[0].source_appearance.surface_color,
        expected.first_occurrence_color,
    )
    _assert_color(
        repeated[1].source_appearance.surface_color,
        expected.repeated_product_surface_color,
    )


def test_public_xcaf_models_are_ocp_free_and_ids_are_runtime_scoped(
    imported_assembly: tuple[XcafFixtureExpectations, OcpCadKernel, object],
) -> None:
    _expected, kernel, document_id = imported_assembly
    assembly = kernel.get_xcaf_assembly_metadata(document_id)
    values = (
        assembly,
        kernel.get_xcaf_root_occurrences(document_id),
        tuple(
            kernel.get_xcaf_product_metadata(document_id, product_id)
            for product_id in assembly.product_ids
        ),
        tuple(
            kernel.get_xcaf_occurrence_metadata(document_id, occurrence_id)
            for occurrence_id in assembly.occurrence_ids
        ),
    )
    _assert_public_value(values)
    assert all(isinstance(item, XcafProductId) for item in assembly.product_ids)
    assert all(isinstance(item, XcafOccurrenceId) for item in assembly.occurrence_ids)
    assert all(str(document_id) in item.value for item in assembly.product_ids)
    assert all(str(document_id) in item.value for item in assembly.occurrence_ids)
    assert all("0:1:" not in item.value for item in assembly.product_ids)
    assert all("0:1:" not in item.value for item in assembly.occurrence_ids)


def test_release_removes_entire_xcaf_record(
    imported_assembly: tuple[XcafFixtureExpectations, OcpCadKernel, object],
) -> None:
    _expected, kernel, document_id = imported_assembly
    occurrence_id = kernel.get_xcaf_root_occurrences(document_id)[0].occurrence_id

    kernel.release_document(document_id)

    assert document_id not in kernel._documents._records
    with pytest.raises(CadDocumentNotFoundError):
        kernel.get_xcaf_assembly_metadata(document_id)
    with pytest.raises(CadDocumentNotFoundError):
        kernel.get_xcaf_occurrence_metadata(document_id, occurrence_id)


def test_two_xcaf_documents_are_independent(tmp_path: Path) -> None:
    first_source = tmp_path / "first.step"
    second_source = tmp_path / "second.step"
    write_xcaf_step_fixture(first_source)
    write_xcaf_step_fixture(second_source)
    kernel = OcpCadKernel()
    first = kernel.import_step(first_source)
    second = kernel.import_step(second_source)
    assert first.document_id is not None and second.document_id is not None

    first_metadata = kernel.get_xcaf_assembly_metadata(first.document_id)
    second_metadata = kernel.get_xcaf_assembly_metadata(second.document_id)

    assert first.document_id != second.document_id
    assert set(first_metadata.product_ids).isdisjoint(second_metadata.product_ids)
    assert set(first_metadata.occurrence_ids).isdisjoint(second_metadata.occurrence_ids)
    kernel.release_document(first.document_id)
    assert kernel.get_xcaf_root_occurrences(second.document_id)


def test_failed_step_import_does_not_affect_existing_xcaf_document(
    imported_assembly: tuple[XcafFixtureExpectations, OcpCadKernel, object],
    tmp_path: Path,
) -> None:
    _expected, kernel, document_id = imported_assembly
    original = kernel.get_xcaf_assembly_metadata(document_id)
    broken = tmp_path / "broken.step"
    broken.write_text("not a STEP model", encoding="utf-8")

    result = kernel.import_step(broken)

    assert not result.success
    assert result.document_id is None
    assert kernel.get_xcaf_assembly_metadata(document_id) == original
    assert len(kernel._documents._records) == 1


def _write_plain_step(path: Path) -> None:
    writer = STEPControl_Writer()
    assert (
        writer.Transfer(
            BRepPrimAPI_MakeBox(4.0, 5.0, 6.0).Shape(),
            STEPControl_StepModelType.STEPControl_AsIs,
        )
        == IFSelect_ReturnStatus.IFSelect_RetDone
    )
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone


def _assert_color(actual: XcafColor | None, expected: tuple[float, float, float]) -> None:
    assert actual is not None
    assert (actual.red, actual.green, actual.blue) == pytest.approx(expected, abs=1e-6)


def _assert_public_value(value: object) -> None:
    assert not type(value).__module__.startswith(("OCP", "PySide6"))
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _assert_public_value(getattr(value, field.name))
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_public_value(key)
            _assert_public_value(item)
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            _assert_public_value(item)
    elif isinstance(value, Enum):
        _assert_public_value(value.value)
