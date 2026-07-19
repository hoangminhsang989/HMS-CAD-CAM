"""OCP-free records crossing the boundary of the isolated XCAF spike."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class XcafNodeRole(str, Enum):
    """Product role reconstructed from the XCAF assembly graph."""

    ASSEMBLY = "assembly"
    PART = "part"


@dataclass(frozen=True, slots=True)
class XcafColor:
    """Normalized source RGB color with no Quantity_Color dependency."""

    red: float
    green: float
    blue: float

    def __post_init__(self) -> None:
        values = (self.red, self.green, self.blue)
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values):
            raise ValueError("XCAF color channels must be finite values from 0 to 1")


@dataclass(frozen=True, slots=True)
class XcafTransform:
    """Immutable row-major affine 4x4 transform."""

    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.values) != 16 or not all(math.isfinite(value) for value in self.values):
            raise ValueError("XCAF transform must contain 16 finite values")
        if self.values[12:] != (0.0, 0.0, 0.0, 1.0):
            raise ValueError("XCAF transform must be affine")

    @classmethod
    def identity(cls) -> "XcafTransform":
        """Return an identity transform."""
        return cls(
            (
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
            )
        )

    def compose(self, local: "XcafTransform") -> "XcafTransform":
        """Return ``self x local`` for parent-to-child accumulation."""
        if not isinstance(local, XcafTransform):
            raise TypeError("local must be XcafTransform")
        result = tuple(
            sum(self.values[row * 4 + inner] * local.values[inner * 4 + column] for inner in range(4))
            for row in range(4)
            for column in range(4)
        )
        return XcafTransform(result)

    @property
    def translation(self) -> tuple[float, float, float]:
        """Return translation from the affine matrix."""
        return (self.values[3], self.values[7], self.values[11])


@dataclass(frozen=True, slots=True)
class XcafSourceAppearance:
    """Colors originating in STEP/XCAF, never a user view-state override."""

    generic_color: XcafColor | None = None
    surface_color: XcafColor | None = None
    curve_color: XcafColor | None = None


@dataclass(frozen=True, slots=True)
class XcafSubshapeAppearance:
    """Source appearance for one internally identified product subshape."""

    subshape_id: str
    source_appearance: XcafSourceAppearance

    def __post_init__(self) -> None:
        if not self.subshape_id:
            raise ValueError("XCAF subshape ID must not be empty")


@dataclass(frozen=True, slots=True)
class XcafProductRecord:
    """One product definition, shared by one or more occurrences."""

    product_id: str
    role: XcafNodeRole
    name: str
    source_appearance: XcafSourceAppearance
    subshape_appearances: tuple[XcafSubshapeAppearance, ...] = ()

    def __post_init__(self) -> None:
        if not self.product_id or not self.name.strip():
            raise ValueError("XCAF product ID and name must not be empty")


@dataclass(frozen=True, slots=True)
class XcafOccurrenceRecord:
    """One placed occurrence referencing a product definition."""

    occurrence_id: str
    product_id: str
    parent_occurrence_id: str | None
    role: XcafNodeRole
    name: str
    name_source: str
    local_transform: XcafTransform
    absolute_transform: XcafTransform
    source_appearance: XcafSourceAppearance
    child_occurrence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.occurrence_id or not self.product_id or not self.name.strip():
            raise ValueError("XCAF occurrence identity and name must not be empty")
        if self.name_source not in {"occurrence", "product", "generated"}:
            raise ValueError("Unsupported XCAF occurrence name source")


@dataclass(frozen=True, slots=True)
class XcafImportReport:
    """All-or-nothing public result of one STEP/XCAF transfer."""

    source_path: str
    root_occurrence_ids: tuple[str, ...]
    products: tuple[XcafProductRecord, ...]
    occurrences: tuple[XcafOccurrenceRecord, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        product_ids = tuple(item.product_id for item in self.products)
        occurrence_ids = tuple(item.occurrence_id for item in self.occurrences)
        if not self.source_path or not self.root_occurrence_ids:
            raise ValueError("XCAF import report requires a source and assembly roots")
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("XCAF product IDs must be unique")
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("XCAF occurrence IDs must be unique")
        known_products = set(product_ids)
        known_occurrences = set(occurrence_ids)
        if not set(self.root_occurrence_ids).issubset(known_occurrences):
            raise ValueError("XCAF report contains an unknown root occurrence")
        for occurrence in self.occurrences:
            if occurrence.product_id not in known_products:
                raise ValueError("XCAF occurrence references an unknown product")
            if occurrence.parent_occurrence_id is not None and occurrence.parent_occurrence_id not in known_occurrences:
                raise ValueError("XCAF occurrence references an unknown parent")
            if not set(occurrence.child_occurrence_ids).issubset(known_occurrences):
                raise ValueError("XCAF occurrence references an unknown child")

    def product(self, product_id: str) -> XcafProductRecord:
        """Resolve a product by its spike-local ID."""
        return next(item for item in self.products if item.product_id == product_id)

    def occurrence(self, occurrence_id: str) -> XcafOccurrenceRecord:
        """Resolve an occurrence by its spike-local ID."""
        return next(item for item in self.occurrences if item.occurrence_id == occurrence_id)
