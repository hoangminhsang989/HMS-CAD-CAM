"""Tests for CAM WCS, work offsets and strict stock variants."""

import dataclasses
import json
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    BoxStock,
    CamUnitError,
    CamValidationError,
    CustomGeometryStock,
    CylinderStock,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    Length,
    LengthUnit,
    ModelStock,
    Point3,
    Revision,
    StockDefinition,
    UnsupportedCamSchemaError,
    Vector3,
    WcsFrame,
    WorkOffset,
)


def _reference(source_id=None, selector: str = "body:main") -> GeometryReference:
    return GeometryReference(
        reference_id=GeometryReferenceId.new(),
        scheme="hms_persistent_geometry",
        scheme_version=1,
        source_id=source_id or uuid4(),
        kind=GeometryReferenceKind.BODY,
        geometry_kind=GeometryRepresentationKind.BREP,
        subshape_selector=selector,
        expected_geometry_fingerprint=GeometryFingerprint.from_payload(
            {"selector": selector}
        ),
        expected_source_revision=Revision(1),
    )


def _frame(unit: LengthUnit = LengthUnit.MM) -> WcsFrame:
    return WcsFrame.identity(unit)


def test_right_handed_wcs_round_trip_is_deterministic() -> None:
    frame = WcsFrame(
        Point3(1.0, 2.0, 3.0, LengthUnit.MM),
        Vector3(1.0, 0.0, 0.0),
        Vector3(0.0, 1.0, 0.0),
        Vector3(0.0, 0.0, 1.0),
    )

    restored = WcsFrame.from_dict(frame.to_dict())

    assert restored == frame
    assert json.dumps(restored.to_dict(), sort_keys=True) == json.dumps(
        frame.to_dict(), sort_keys=True
    )


@pytest.mark.parametrize(
    "axes",
    (
        (
            Vector3(0.0, 0.0, 0.0),
            Vector3(0.0, 1.0, 0.0),
            Vector3(0.0, 0.0, 1.0),
        ),
        (
            Vector3(1.0, 0.0, 0.0),
            Vector3(1.0, 1.0, 0.0),
            Vector3(0.0, 0.0, 1.0),
        ),
        (
            Vector3(1.0, 0.0, 0.0),
            Vector3(0.0, 1.0, 0.0),
            Vector3(0.0, 0.0, -1.0),
        ),
    ),
)
def test_degenerate_non_orthogonal_and_left_handed_wcs_are_rejected(axes) -> None:
    with pytest.raises(CamValidationError):
        WcsFrame(Point3(0.0, 0.0, 0.0, LengthUnit.MM), *axes)


def test_wcs_unknown_unit_is_rejected_without_inference() -> None:
    with pytest.raises(CamUnitError):
        WcsFrame.identity(LengthUnit.UNKNOWN)


def test_work_offset_is_controller_neutral_and_round_trips() -> None:
    offset = WorkOffset("PRIMARY_SETUP", numeric_slot=7)

    assert WorkOffset.from_dict(offset.to_dict()) == offset
    assert "G54" not in json.dumps(offset.to_dict(), sort_keys=True)


def test_box_stock_round_trip_preserves_frame_and_dimensions() -> None:
    stock = BoxStock(
        Length(100.0, LengthUnit.MM),
        Length(80.0, LengthUnit.MM),
        Length(25.0, LengthUnit.MM),
        _frame(),
    )

    assert StockDefinition.from_dict(stock.to_dict()) == stock


def test_cylinder_stock_round_trip_uses_diameter_convention() -> None:
    stock = CylinderStock(
        diameter=Length(50.0, LengthUnit.MM),
        length=Length(120.0, LengthUnit.MM),
        frame=_frame(),
    )

    payload = stock.to_dict()

    assert "diameter" in payload and "radius" not in payload
    assert StockDefinition.from_dict(payload) == stock


def test_reference_backed_stock_preserves_geometry_reference() -> None:
    reference = _reference()

    for stock in (ModelStock(reference), CustomGeometryStock(reference)):
        restored = StockDefinition.from_dict(stock.to_dict())
        assert restored == stock
        assert restored.geometry_reference == reference


def test_stock_payload_cannot_mix_variant_fields() -> None:
    payload = BoxStock(
        Length(1.0, LengthUnit.MM),
        Length(2.0, LengthUnit.MM),
        Length(3.0, LengthUnit.MM),
        _frame(),
    ).to_dict()
    payload["diameter"] = {"value": 4.0, "unit": "mm"}

    with pytest.raises(CamValidationError):
        StockDefinition.from_dict(payload)


@pytest.mark.parametrize(
    "length",
    (
        Length(1.0, LengthUnit.UNKNOWN),
        Length(0.0, LengthUnit.MM),
        Length(-1.0, LengthUnit.MM),
    ),
)
def test_box_stock_rejects_unknown_zero_or_negative_dimensions(length) -> None:
    with pytest.raises((CamUnitError, CamValidationError)):
        BoxStock(
            length,
            Length(2.0, LengthUnit.MM),
            Length(3.0, LengthUnit.MM),
            _frame(),
        )


def test_stock_rejects_non_finite_dimension_at_quantity_boundary() -> None:
    with pytest.raises(CamUnitError):
        Length(float("nan"), LengthUnit.MM)


def test_wcs_future_version_is_rejected() -> None:
    payload = _frame().to_dict()
    payload["format_version"] = 2

    with pytest.raises(UnsupportedCamSchemaError):
        WcsFrame.from_dict(payload)


def test_wcs_is_frozen_and_contains_no_native_object() -> None:
    frame = _frame()

    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.origin = Point3(1.0, 0.0, 0.0, LengthUnit.MM)
    assert not type(frame).__module__.startswith(("OCP", "PySide6"))
