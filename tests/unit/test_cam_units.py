"""Tests for explicit CAM quantity units."""

import math

import pytest

from hms_cadcam.cam.domain import (
    Angle,
    AngleUnit,
    FeedRate,
    FeedUnit,
    Length,
    LengthUnit,
    SpindleSpeed,
)
from hms_cadcam.cam.domain.errors import CamUnitError


def test_length_conversion_mm_and_inch_is_explicit_and_reversible() -> None:
    inch = Length(1.0, LengthUnit.INCH)

    assert inch.to(LengthUnit.MM) == Length(25.4, LengthUnit.MM)
    assert inch.to(LengthUnit.MM).to(LengthUnit.INCH).value == pytest.approx(1.0)


def test_unknown_length_unit_is_never_inferred() -> None:
    unknown = Length(10.0, LengthUnit.UNKNOWN)

    with pytest.raises(CamUnitError):
        unknown.to(LengthUnit.MM)
    with pytest.raises(CamUnitError):
        Length(10.0, LengthUnit.MM).to(LengthUnit.UNKNOWN)


def test_angle_and_feed_conversions_preserve_semantics() -> None:
    assert Angle(math.pi, AngleUnit.RADIAN).to(AngleUnit.DEGREE).value == pytest.approx(
        180.0
    )
    assert FeedRate(1.0, FeedUnit.INCH_PER_MINUTE).to(
        FeedUnit.MM_PER_MINUTE
    ) == FeedRate(25.4, FeedUnit.MM_PER_MINUTE)


@pytest.mark.parametrize("invalid", (float("nan"), float("inf"), -float("inf")))
def test_non_finite_quantities_are_rejected(invalid: float) -> None:
    constructors = (
        lambda: Length(invalid, LengthUnit.MM),
        lambda: Angle(invalid, AngleUnit.DEGREE),
        lambda: FeedRate(invalid, FeedUnit.MM_PER_MINUTE),
        lambda: SpindleSpeed(invalid),
    )

    for constructor in constructors:
        with pytest.raises(CamUnitError):
            constructor()


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: FeedRate(0.0, FeedUnit.MM_PER_MINUTE),
        lambda: FeedRate(-1.0, FeedUnit.MM_PER_MINUTE),
        lambda: SpindleSpeed(0.0),
        lambda: SpindleSpeed(-1.0),
    ),
)
def test_rates_must_be_strictly_positive(constructor) -> None:
    with pytest.raises(CamUnitError):
        constructor()
