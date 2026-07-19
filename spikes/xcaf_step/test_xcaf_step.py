"""Headless acceptance checks for the isolated XCAF STEP technical spike."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

import pytest
from OCP.IFSelect import IFSelect_ReturnStatus

from spikes.xcaf_step.fixture import XcafFixtureExpectations, write_xcaf_step_fixture
from spikes.xcaf_step.model import (
    XcafColor,
    XcafImportReport,
    XcafNodeRole,
    XcafSourceAppearance,
)
from spikes.xcaf_step.reader import XcafImportError, XcafStepSession, _resolve_name


@pytest.fixture
def imported_fixture(
    tmp_path: Path,
) -> tuple[XcafFixtureExpectations, XcafStepSession, XcafImportReport]:
    source = tmp_path / "nested-assembly.step"
    expected = write_xcaf_step_fixture(source)
    session = XcafStepSession()
    report = session.import_file(source)
    yield expected, session, report
    session.close()


def test_fixture_is_temporary_and_assembly_root_is_read(
    imported_fixture: tuple[XcafFixtureExpectations, XcafStepSession, XcafImportReport],
    tmp_path: Path,
) -> None:
    expected, _session, report = imported_fixture
    assert Path(report.source_path).is_relative_to(tmp_path)
    assert len(report.root_occurrence_ids) == 1
    root = report.occurrence(report.root_occurrence_ids[0])
    assert root.role is XcafNodeRole.ASSEMBLY
    assert root.parent_occurrence_id is None
    assert root.name == expected.root_product_name
    assert root.name_source == "product"
    assert len(root.child_occurrence_ids) == 3


def test_product_definition_occurrence_reference_and_repeated_instance_are_distinct(
    imported_fixture: tuple[XcafFixtureExpectations, XcafStepSession, XcafImportReport],
) -> None:
    expected, _session, report = imported_fixture
    product = next(item for item in report.products if item.name == expected.repeated_product_name)
    repeated = [item for item in report.occurrences if item.product_id == product.product_id]

    assert product.role is XcafNodeRole.PART
    assert len(repeated) == 2
    assert repeated[0].occurrence_id != repeated[1].occurrence_id
    assert repeated[0].parent_occurrence_id == repeated[1].parent_occurrence_id
    assert all(item.role is XcafNodeRole.PART for item in repeated)


def test_nested_assembly_and_absolute_parent_times_local_transform(
    imported_fixture: tuple[XcafFixtureExpectations, XcafStepSession, XcafImportReport],
) -> None:
    expected, _session, report = imported_fixture
    nested = next(
        item for item in report.occurrences if item.name == expected.nested_occurrence_name
    )
    assert nested.role is XcafNodeRole.ASSEMBLY
    assert nested.local_transform.translation == pytest.approx((0.0, 50.0, 0.0))
    assert len(nested.child_occurrence_ids) == 1

    child = report.occurrence(nested.child_occurrence_ids[0])
    assert child.role is XcafNodeRole.PART
    assert child.local_transform.translation == pytest.approx((0.0, 0.0, 20.0))
    assert child.absolute_transform == nested.absolute_transform.compose(child.local_transform)
    assert child.absolute_transform.translation == pytest.approx((0.0, 50.0, 20.0))


def test_repeated_occurrences_have_different_transforms_and_name_fallback(
    imported_fixture: tuple[XcafFixtureExpectations, XcafStepSession, XcafImportReport],
) -> None:
    expected, _session, report = imported_fixture
    product = next(item for item in report.products if item.name == expected.repeated_product_name)
    repeated = sorted(
        (item for item in report.occurrences if item.product_id == product.product_id),
        key=lambda item: item.absolute_transform.translation[0],
    )

    assert repeated[0].absolute_transform.translation == pytest.approx((10.0, 0.0, 0.0))
    assert repeated[1].absolute_transform.translation == pytest.approx((40.0, 0.0, 0.0))
    assert repeated[0].name == expected.first_occurrence_name
    assert repeated[0].name_source == "occurrence"
    assert repeated[1].name == expected.repeated_product_name
    assert repeated[1].name_source == "product"


def test_safe_generated_name_fallback_never_returns_empty() -> None:
    name, source = _resolve_name(
        None,
        "  ",
        XcafNodeRole.PART,
        "xcaf-spike-occurrence:0123456789abcdef",
    )
    assert name == "Part 01234567"
    assert source == "generated"


def test_product_occurrence_and_subshape_source_colors_are_read(
    imported_fixture: tuple[XcafFixtureExpectations, XcafStepSession, XcafImportReport],
) -> None:
    expected, _session, report = imported_fixture
    repeated_product = next(
        item for item in report.products if item.name == expected.repeated_product_name
    )
    nested_part = next(item for item in report.products if item.name == expected.nested_part_name)
    first_occurrence = next(
        item for item in report.occurrences if item.name == expected.first_occurrence_name
    )

    _assert_color(
        repeated_product.source_appearance.surface_color,
        expected.repeated_product_surface_color,
    )
    _assert_color(
        first_occurrence.source_appearance.surface_color,
        expected.first_occurrence_color,
    )
    _assert_color(
        nested_part.source_appearance.surface_color,
        expected.nested_part_surface_color,
    )
    assert repeated_product.subshape_appearances
    _assert_color(
        repeated_product.subshape_appearances[0].source_appearance.surface_color,
        expected.subshape_surface_color,
    )
    assert isinstance(repeated_product.source_appearance, XcafSourceAppearance)
    assert "override" not in {field.name for field in fields(XcafSourceAppearance)}


def test_public_report_contains_no_ocp_or_native_cad_objects(
    imported_fixture: tuple[XcafFixtureExpectations, XcafStepSession, XcafImportReport],
) -> None:
    _expected, _session, report = imported_fixture
    _assert_public_value(report)
    assert all("label" not in field.name for field in fields(type(report)))
    assert all(item.product_id.startswith("xcaf-spike-product:") for item in report.products)
    assert all(
        item.occurrence_id.startswith("xcaf-spike-occurrence:")
        for item in report.occurrences
    )


def test_corrupt_step_is_rejected_without_partial_public_result(tmp_path: Path) -> None:
    source = tmp_path / "broken.step"
    source.write_text("not a STEP model", encoding="utf-8")
    session = XcafStepSession()

    with pytest.raises(XcafImportError, match="ReadFile status"):
        session.import_file(source)

    assert session.last_report is None
    assert not session.has_native_document
    assert session.retained_label_count == 0


def test_transfer_failure_does_not_create_partial_public_result(tmp_path: Path) -> None:
    source = tmp_path / "transfer-failure.step"
    source.write_text("non-empty test input", encoding="utf-8")

    class TransferFailReader:
        def SetColorMode(self, enabled: bool) -> None:
            assert enabled

        def SetNameMode(self, enabled: bool) -> None:
            assert enabled

        def SetSHUOMode(self, enabled: bool) -> None:
            assert enabled

        def ReadFile(self, path: str) -> IFSelect_ReturnStatus:
            assert path.endswith("transfer-failure.step")
            return IFSelect_ReturnStatus.IFSelect_RetDone

        def Transfer(self, _document: object) -> bool:
            return False

    session = XcafStepSession(TransferFailReader)
    with pytest.raises(XcafImportError, match="transfer failed"):
        session.import_file(source)

    assert session.last_report is None
    assert not session.has_native_document
    assert session.retained_label_count == 0


def test_close_releases_native_document_and_labels(
    imported_fixture: tuple[XcafFixtureExpectations, XcafStepSession, XcafImportReport],
) -> None:
    _expected, session, report = imported_fixture
    assert session.has_native_document
    assert session.retained_label_count == len(report.products) + len(report.occurrences)

    session.close()
    session.close()

    assert session.last_report is None
    assert not session.has_native_document
    assert session.retained_label_count == 0


def _assert_color(actual: XcafColor | None, expected: tuple[float, float, float]) -> None:
    assert actual is not None
    assert (actual.red, actual.green, actual.blue) == pytest.approx(expected, abs=1e-6)


def _assert_public_value(value: object) -> None:
    module = type(value).__module__
    assert not module.startswith(("OCP", "PySide6"))
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
