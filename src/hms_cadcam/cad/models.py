"""OCP- and UI-independent data exchanged with a CAD kernel."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CadDocumentId:
    """Stable opaque identifier for a document owned by one kernel instance."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("CAD document ID must not be empty")

    def __str__(self) -> str:
        return self.value


class CadFormat(str, Enum):
    """CAD formats currently supported by the product kernel boundary."""

    GENERATED = "generated"
    STEP = "step"
    BREP = "brep"


@dataclass(frozen=True, slots=True)
class TopologyCounts:
    """Counts of the topology levels needed by the current product stage."""

    solids: int
    faces: int
    edges: int

    def __post_init__(self) -> None:
        if min(self.solids, self.faces, self.edges) < 0:
            raise ValueError("Topology counts must not be negative")


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned bounds represented without CAD-kernel objects."""

    x_min: float
    y_min: float
    z_min: float
    x_max: float
    y_max: float
    z_max: float

    def __post_init__(self) -> None:
        values = (
            self.x_min,
            self.y_min,
            self.z_min,
            self.x_max,
            self.y_max,
            self.z_max,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Bounding box values must be finite")
        if (
            self.x_min > self.x_max
            or self.y_min > self.y_max
            or self.z_min > self.z_max
        ):
            raise ValueError("Bounding box minimums must not exceed maximums")


@dataclass(frozen=True, slots=True)
class CadDocumentMetadata:
    """Serializable metadata for one kernel-owned CAD document."""

    document_id: CadDocumentId
    cad_format: CadFormat
    topology_counts: TopologyCounts
    bounding_box: BoundingBox
    source_path: Path | None = None


@dataclass(frozen=True, slots=True)
class CadImportResult:
    """Controlled import outcome containing no native CAD objects."""

    success: bool
    source_path: Path
    detected_format: CadFormat
    document_id: CadDocumentId | None
    metadata: CadDocumentMetadata | None
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if self.elapsed_seconds < 0.0:
            raise ValueError("Import elapsed time must not be negative")
        if self.success:
            if self.document_id is None or self.metadata is None or self.errors:
                raise ValueError("Successful CAD import result is inconsistent")
        elif self.document_id is not None or self.metadata is not None:
            raise ValueError("Failed CAD import result must not own a document")


@dataclass(frozen=True, slots=True)
class CadKernelStatus:
    """Availability information suitable for application diagnostics."""

    available: bool
    backend: str
    version: str | None = None
    error: str | None = None
