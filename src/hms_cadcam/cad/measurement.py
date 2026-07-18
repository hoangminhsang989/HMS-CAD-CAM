"""Public, immutable measurement models and service protocol."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, TypeAlias, runtime_checkable

from hms_cadcam.cad.models import CadDocumentId


class MeasurementKind(str, Enum):
    """Kinds of read-only BREP measurements exposed by the product."""

    POINT_COORDINATES = "point_coordinates"
    DISTANCE = "distance"
    EDGE_LENGTH = "edge_length"
    CIRCULAR_EDGE = "circular_edge"
    AREA = "area"
    VOLUME = "volume"
    BOUNDING_DIMENSIONS = "bounding_dimensions"


def _require_finite(*values: float) -> None:
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Measurement values must be finite")


def _require_non_negative(*values: float) -> None:
    _require_finite(*values)
    if min(values) < 0.0:
        raise ValueError("Measurement values must not be negative")


@dataclass(frozen=True, slots=True)
class PointCoordinates:
    x: float
    y: float
    z: float
    kind: MeasurementKind = field(
        default=MeasurementKind.POINT_COORDINATES, init=False
    )

    def __post_init__(self) -> None:
        _require_finite(self.x, self.y, self.z)


@dataclass(frozen=True, slots=True)
class DistanceMeasurement:
    distance: float
    kind: MeasurementKind = field(default=MeasurementKind.DISTANCE, init=False)

    def __post_init__(self) -> None:
        _require_non_negative(self.distance)


@dataclass(frozen=True, slots=True)
class EdgeLengthMeasurement:
    length: float
    kind: MeasurementKind = field(default=MeasurementKind.EDGE_LENGTH, init=False)

    def __post_init__(self) -> None:
        _require_non_negative(self.length)


@dataclass(frozen=True, slots=True)
class CircularEdgeMeasurement:
    radius: float
    diameter: float
    is_full_circle: bool
    kind: MeasurementKind = field(default=MeasurementKind.CIRCULAR_EDGE, init=False)

    def __post_init__(self) -> None:
        _require_non_negative(self.radius, self.diameter)


@dataclass(frozen=True, slots=True)
class AreaMeasurement:
    area: float
    kind: MeasurementKind = field(default=MeasurementKind.AREA, init=False)

    def __post_init__(self) -> None:
        _require_non_negative(self.area)


@dataclass(frozen=True, slots=True)
class VolumeMeasurement:
    volume: float
    kind: MeasurementKind = field(default=MeasurementKind.VOLUME, init=False)

    def __post_init__(self) -> None:
        _require_non_negative(self.volume)


@dataclass(frozen=True, slots=True)
class BoundingDimensions:
    x: float
    y: float
    z: float
    kind: MeasurementKind = field(
        default=MeasurementKind.BOUNDING_DIMENSIONS, init=False
    )

    def __post_init__(self) -> None:
        _require_non_negative(self.x, self.y, self.z)


MeasurementValue: TypeAlias = (
    PointCoordinates
    | DistanceMeasurement
    | EdgeLengthMeasurement
    | CircularEdgeMeasurement
    | AreaMeasurement
    | VolumeMeasurement
    | BoundingDimensions
)


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    """Measurements associated with one document and zero or more selections."""

    document_id: CadDocumentId
    selection_ids: tuple[str, ...]
    values: tuple[MeasurementValue, ...]

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("Measurement result must contain at least one value")
        if any(not selection_id for selection_id in self.selection_ids):
            raise ValueError("Measurement selection IDs must not be empty")


@runtime_checkable
class MeasurementService(Protocol):
    """Read-only BREP measurement boundary consumed by application UI."""

    def measure_selection(
        self,
        document_id: CadDocumentId,
        selection_id: str,
    ) -> MeasurementResult: ...

    def measure_distance(
        self,
        document_id: CadDocumentId,
        first_selection_id: str,
        second_selection_id: str,
    ) -> MeasurementResult: ...

    def measure_document(self, document_id: CadDocumentId) -> MeasurementResult: ...
