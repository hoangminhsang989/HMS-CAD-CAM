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


@dataclass(frozen=True, slots=True)
class CadObjectId:
    """Opaque object identifier stable only within one retained document."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("CAD object ID must not be empty")

    def __str__(self) -> str:
        return self.value


class CadFormat(str, Enum):
    """CAD formats currently supported by the product kernel boundary."""

    GENERATED = "generated"
    STEP = "step"
    BREP = "brep"
    IGES = "iges"
    STL = "stl"


class CadGeometryKind(str, Enum):
    """Kernel-independent representation retained for one CAD document."""

    BREP = "brep"
    TRIANGLE_MESH = "triangle_mesh"


class CadDocumentKind(str, Enum):
    """Semantic document representation retained by the CAD kernel."""

    BREP = "brep"
    TRIANGLE_MESH = "triangle_mesh"
    XCAF_PART = "xcaf_part"
    XCAF_ASSEMBLY = "xcaf_assembly"


class CadObjectKind(str, Enum):
    """Display-management levels exposed by the lightweight topology tree."""

    DOCUMENT = "document"
    COMPOUND = "compound"
    COMPSOLID = "compsolid"
    SOLID = "solid"
    SHELL = "shell"
    SHAPE = "shape"
    MESH = "mesh"


class CadUnits(str, Enum):
    """Unit information known at the current import boundary."""

    UNKNOWN = "unknown"


class XcafNodeRole(str, Enum):
    """Role of one XCAF product and each occurrence referring to it."""

    ASSEMBLY = "assembly"
    PART = "part"


class XcafNameSource(str, Enum):
    """Source selected for a safe public occurrence display name."""

    OCCURRENCE = "occurrence"
    PRODUCT = "product"
    GENERATED = "generated"


@dataclass(frozen=True, slots=True)
class XcafOccurrenceId:
    """Runtime-scoped occurrence identifier valid while a document is retained."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("XCAF occurrence ID must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class XcafProductId:
    """Runtime-scoped product identifier valid while a document is retained."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("XCAF product ID must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class XcafColor:
    """Normalized RGB source color independent from Quantity_Color."""

    red: float
    green: float
    blue: float

    def __post_init__(self) -> None:
        values = (self.red, self.green, self.blue)
        if not all(
            math.isfinite(value) and 0.0 <= value <= 1.0 for value in values
        ):
            raise ValueError("XCAF color channels must be finite values from 0 to 1")


@dataclass(frozen=True, slots=True)
class XcafTransform:
    """Immutable row-major affine 4x4 transform."""

    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.values) != 16 or not all(
            math.isfinite(value) for value in self.values
        ):
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
        return XcafTransform(
            tuple(
                sum(
                    self.values[row * 4 + inner]
                    * local.values[inner * 4 + column]
                    for inner in range(4)
                )
                for row in range(4)
                for column in range(4)
            )
        )

    @property
    def translation(self) -> tuple[float, float, float]:
        """Return translation from the affine matrix."""
        return (self.values[3], self.values[7], self.values[11])


@dataclass(frozen=True, slots=True)
class XcafSourceAppearance:
    """Colors imported from STEP, separate from every user override."""

    generic_color: XcafColor | None = None
    surface_color: XcafColor | None = None
    curve_color: XcafColor | None = None


@dataclass(frozen=True, slots=True)
class XcafSubshapeAppearance:
    """Source appearance for an internal product subshape identifier."""

    subshape_id: str
    source_appearance: XcafSourceAppearance

    def __post_init__(self) -> None:
        if not self.subshape_id:
            raise ValueError("XCAF subshape ID must not be empty")


@dataclass(frozen=True, slots=True)
class XcafProductMetadata:
    """Public OCP-free metadata for one XCAF product definition."""

    document_id: CadDocumentId
    product_id: XcafProductId
    role: XcafNodeRole
    name: str
    source_appearance: XcafSourceAppearance
    subshape_appearances: tuple[XcafSubshapeAppearance, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("XCAF product name must not be empty")


@dataclass(frozen=True, slots=True)
class XcafOccurrenceMetadata:
    """Public OCP-free metadata for one placed product occurrence."""

    document_id: CadDocumentId
    occurrence_id: XcafOccurrenceId
    product_id: XcafProductId
    parent_occurrence_id: XcafOccurrenceId | None
    role: XcafNodeRole
    name: str
    name_source: XcafNameSource
    local_transform: XcafTransform
    absolute_transform: XcafTransform
    source_appearance: XcafSourceAppearance
    child_occurrence_ids: tuple[XcafOccurrenceId, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("XCAF occurrence name must not be empty")


@dataclass(frozen=True, slots=True)
class XcafAssemblyMetadata:
    """Immutable index summary for one retained STEP/XCAF document."""

    document_id: CadDocumentId
    root_occurrence_ids: tuple[XcafOccurrenceId, ...]
    product_ids: tuple[XcafProductId, ...]
    occurrence_ids: tuple[XcafOccurrenceId, ...]

    def __post_init__(self) -> None:
        if not self.root_occurrence_ids:
            raise ValueError("XCAF metadata requires at least one root occurrence")
        if len(self.product_ids) != len(set(self.product_ids)):
            raise ValueError("XCAF product IDs must be unique")
        if len(self.occurrence_ids) != len(set(self.occurrence_ids)):
            raise ValueError("XCAF occurrence IDs must be unique")
        if not set(self.root_occurrence_ids).issubset(self.occurrence_ids):
            raise ValueError("XCAF root occurrence is not indexed")


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
class MeshStatistics:
    """Counts describing a triangle mesh without inventing BREP topology."""

    vertices: int
    triangles: int

    def __post_init__(self) -> None:
        if self.vertices < 0 or self.triangles < 0:
            raise ValueError("Mesh statistics must not be negative")


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
class CadObjectNode:
    """One OCP-free node in a bounded display-management topology tree."""

    document_id: CadDocumentId
    object_id: CadObjectId
    kind: CadObjectKind
    label: str
    bounding_box: BoundingBox
    children: tuple["CadObjectNode", ...] = ()
    has_presentation: bool = False

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("CAD object label must not be empty")
        if any(child.document_id != self.document_id for child in self.children):
            raise ValueError("CAD object children must belong to the same document")
        if self.has_presentation and self.children:
            raise ValueError("Presentation nodes must be leaves")

    def walk(self) -> tuple["CadObjectNode", ...]:
        """Return this node and descendants in stable pre-order."""
        return (self,) + tuple(item for child in self.children for item in child.walk())


@dataclass(frozen=True, slots=True)
class CadDocumentTree:
    """Topology-only tree; it intentionally carries no XCAF assembly semantics."""

    document_id: CadDocumentId
    root: CadObjectNode

    def __post_init__(self) -> None:
        if self.root.document_id != self.document_id:
            raise ValueError("CAD document tree root has a different document ID")
        object_ids = tuple(node.object_id for node in self.root.walk())
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("CAD object IDs must be unique within a document")

    def find(self, object_id: CadObjectId) -> CadObjectNode | None:
        """Resolve one public node without exposing native topology."""
        return next(
            (node for node in self.root.walk() if node.object_id == object_id),
            None,
        )

    @property
    def presentation_nodes(self) -> tuple[CadObjectNode, ...]:
        """Return only nodes backed by AIS presentations."""
        return tuple(node for node in self.root.walk() if node.has_presentation)


@dataclass(frozen=True, slots=True)
class CadDocumentMetadata:
    """Serializable metadata for one kernel-owned CAD document."""

    document_id: CadDocumentId
    cad_format: CadFormat
    bounding_box: BoundingBox
    geometry_kind: CadGeometryKind
    document_kind: CadDocumentKind
    units: CadUnits
    topology_counts: TopologyCounts | None = None
    mesh_statistics: MeshStatistics | None = None
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if self.geometry_kind is CadGeometryKind.BREP:
            if self.topology_counts is None or self.mesh_statistics is not None:
                raise ValueError("BREP metadata requires only topology counts")
            if self.document_kind is CadDocumentKind.TRIANGLE_MESH:
                raise ValueError("BREP metadata cannot be a triangle-mesh document")
        elif self.topology_counts is not None or self.mesh_statistics is None:
            raise ValueError("Triangle-mesh metadata requires only mesh statistics")
        elif self.document_kind is not CadDocumentKind.TRIANGLE_MESH:
            raise ValueError("Triangle-mesh geometry requires triangle-mesh document kind")
        if self.document_kind in {
            CadDocumentKind.XCAF_PART,
            CadDocumentKind.XCAF_ASSEMBLY,
        } and self.cad_format is not CadFormat.STEP:
            raise ValueError("XCAF document kinds are supported only for STEP")


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
