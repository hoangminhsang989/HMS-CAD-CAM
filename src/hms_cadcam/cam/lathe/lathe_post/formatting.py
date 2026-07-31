"""Deterministic numeric and comment formatting for basic NC output."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import re
import unicodedata


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("numeric value must not be bool or non-numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("numeric value must be finite")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("numeric value is invalid") from error


def format_number(value: object, decimals: int, *, suppress_leading_zero: bool = True, trim_trailing_zero: bool = True) -> str:
    if type(decimals) is not int or decimals < 0 or decimals > 9:
        raise ValueError("decimal precision is invalid")
    decimal = _decimal(value)
    quantum = Decimal(1).scaleb(-decimals)
    rounded = decimal.quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = Decimal(0)
    text = format(rounded, "f")
    if trim_trailing_zero and "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"-0", "-0.0", ""}:
        text = "0"
    if suppress_leading_zero and text.startswith("0."):
        text = text[1:]
    elif suppress_leading_zero and text.startswith("-0."):
        text = "-" + text[2:]
    return text


def round_rpm(value: object) -> int:
    decimal = _decimal(value)
    rounded = decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise ValueError("RPM must round to a positive integer")
    return int(rounded)


def sanitize_comment(value: object, *, uppercase: bool = True) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text).replace("(", "[").replace(")", "]")
    text = re.sub(r"\s+", " ", text).strip()
    return text.upper() if uppercase else text


def sanitize_filename_stem(value: object) -> str:
    text = sanitize_comment(value, uppercase=False)
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return text or "program"


__all__ = ["format_number", "round_rpm", "sanitize_comment", "sanitize_filename_stem"]
