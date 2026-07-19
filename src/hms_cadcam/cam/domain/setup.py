"""CAM setup, stock, fixture and source-scope domain models."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from hms_cadcam.cam.domain.errors import (
    CamChildNotFoundError,
    CamInvariantError,
    CamSourceScopeError,
    CamUnitError,
    CamValidationError,
    DuplicateCamIdError,
)
from hms_cadcam.cam.domain.geometry_reference import GeometryReference
from hms_cadcam.cam.domain.ids import FixtureInstanceId, SetupId
from hms_cadcam.cam.domain.operation_tree import OperationTree
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.domain.spatial import AffineTransform, WcsFrame, _strict_payload
from hms_cadcam.cam.domain.units import Length, LengthUnit

_WORK_OFFSET_FORMAT = "HMS_CAM_WORK_OFFSET"
_SOURCE_SCOPE_FORMAT = "HMS_CAM_SOURCE_SCOPE"
_STOCK_FORMAT = "HMS_CAM_STOCK"
_FIXTURE_FORMAT = "HMS_CAM_FIXTURE_INSTANCE"
_SETUP_FORMAT = "HMS_CAM_SETUP"
_VERSION = 1
_SETUP_VERSION = 2
_LOGICAL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")


def _name(value: str, subject: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CamValidationError(f"{subject} name must not be empty")
    normalized = value.strip()
    if len(normalized) > 255:
        raise CamValidationError(f"{subject} name is too long")
    return normalized


def _length_to_dict(value: Length) -> dict[str, float | str]:
    return {"value": value.value, "unit": value.unit.value}


def _length_from_dict(data: dict[str, Any]) -> Length:
    if not isinstance(data, dict) or set(data) != {"value", "unit"}:
        raise CamValidationError("Length payload is malformed")
    try:
        unit = LengthUnit(data["unit"])
    except (TypeError, ValueError) as error:
        raise CamUnitError("Length payload has an invalid unit") from error
    return Length(data["value"], unit)


def _positive_known_length(value: Length, subject: str) -> None:
    if not isinstance(value, Length):
        raise CamValidationError(f"{subject} must be Length")
    if value.unit is LengthUnit.UNKNOWN:
        raise CamUnitError(f"{subject} requires a known length unit")
    if value.value <= 0.0:
        raise CamValidationError(f"{subject} must be greater than zero")


class SetupKind(StrEnum):
    """Broad setup capability without defining machining operations."""

    GENERAL = "general"
    MILL = "mill"
    TURN = "turn"
    MILL_TURN = "mill_turn"


class FixtureRole(StrEnum):
    """Physical role retained for future collision analysis."""

    CLAMP = "clamp"
    VISE = "vise"
    CHUCK = "chuck"
    SUPPORT = "support"
    OTHER = "other"


class StockKind(StrEnum):
    """Supported stock-definition variants."""

    BOX = "box"
    CYLINDER = "cylinder"
    FROM_MODEL = "from_model"
    CUSTOM_GEOMETRY = "custom_geometry"


@dataclass(frozen=True, slots=True)
class WorkOffset:
    """Controller-neutral logical work offset."""

    name: str
    numeric_slot: int | None = None
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _LOGICAL_NAME.fullmatch(self.name):
            raise CamValidationError("Work offset name must be a logical identifier")
        if self.numeric_slot is not None and (
            type(self.numeric_slot) is not int or self.numeric_slot < 0
        ):
            raise CamValidationError("Work offset numeric slot must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        """Serialize this controller-neutral offset."""
        return {
            "format": _WORK_OFFSET_FORMAT,
            "format_version": _VERSION,
            "name": self.name,
            "numeric_slot": self.numeric_slot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkOffset":
        """Deserialize one exact work-offset payload."""
        _strict_payload(
            data,
            format_name=_WORK_OFFSET_FORMAT,
            version=_VERSION,
            fields={"name", "numeric_slot"},
        )
        return cls(data["name"], data["numeric_slot"])


@dataclass(frozen=True, slots=True)
class SourceScope:
    """Explicit project-source scope for one CAM setup."""

    primary_source_id: UUID
    auxiliary_source_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.primary_source_id, UUID):
            raise CamValidationError("Primary source ID must be UUID")
        if not isinstance(self.auxiliary_source_ids, tuple) or any(
            not isinstance(source_id, UUID) for source_id in self.auxiliary_source_ids
        ):
            raise CamValidationError("Auxiliary source IDs must be a UUID tuple")
        if self.primary_source_id in self.auxiliary_source_ids:
            raise CamInvariantError("Primary source cannot also be auxiliary")
        if len(set(self.auxiliary_source_ids)) != len(self.auxiliary_source_ids):
            raise CamInvariantError("Auxiliary source IDs must be unique")
        object.__setattr__(
            self,
            "auxiliary_source_ids",
            tuple(sorted(self.auxiliary_source_ids, key=str)),
        )

    @property
    def allowed_source_ids(self) -> frozenset[UUID]:
        """Return every explicitly declared source."""
        return frozenset((self.primary_source_id, *self.auxiliary_source_ids))

    def allows(self, reference: GeometryReference) -> bool:
        """Return whether a reference belongs to this explicit scope."""
        return reference.source_id in self.allowed_source_ids

    def to_dict(self) -> dict[str, Any]:
        """Serialize this source scope."""
        return {
            "format": _SOURCE_SCOPE_FORMAT,
            "format_version": _VERSION,
            "primary_source_id": str(self.primary_source_id),
            "auxiliary_source_ids": [str(item) for item in self.auxiliary_source_ids],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceScope":
        """Deserialize one exact source scope."""
        _strict_payload(
            data,
            format_name=_SOURCE_SCOPE_FORMAT,
            version=_VERSION,
            fields={"primary_source_id", "auxiliary_source_ids"},
        )
        auxiliary = data["auxiliary_source_ids"]
        if not isinstance(auxiliary, list):
            raise CamValidationError("Auxiliary source IDs must be a list")
        try:
            primary_source_id = UUID(data["primary_source_id"])
            auxiliary_source_ids = tuple(UUID(item) for item in auxiliary)
        except (AttributeError, TypeError, ValueError) as error:
            raise CamValidationError("Source scope UUID payload is invalid") from error
        return cls(primary_source_id, auxiliary_source_ids)


class StockDefinition:
    """Closed stock-variant base with strict serialization dispatch."""

    kind: ClassVar[StockKind]
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    @property
    def geometry_reference(self) -> GeometryReference | None:
        """Return referenced geometry for reference-backed stock variants."""
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this stock variant."""
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StockDefinition":
        """Dispatch a complete payload to exactly one stock variant."""
        if not isinstance(data, dict):
            raise CamValidationError("Stock payload must be an object")
        if data.get("format") != _STOCK_FORMAT:
            from hms_cadcam.cam.domain.errors import UnsupportedCamSchemaError

            raise UnsupportedCamSchemaError("Unsupported stock format")
        if type(data.get("format_version")) is not int or data["format_version"] != _VERSION:
            from hms_cadcam.cam.domain.errors import UnsupportedCamSchemaError

            raise UnsupportedCamSchemaError("Unsupported stock version")
        try:
            stock_kind = StockKind(data["kind"])
        except (KeyError, TypeError, ValueError) as error:
            raise CamValidationError("Stock kind is invalid") from error
        variant: type[StockDefinition] = {
            StockKind.BOX: BoxStock,
            StockKind.CYLINDER: CylinderStock,
            StockKind.FROM_MODEL: ModelStock,
            StockKind.CUSTOM_GEOMETRY: CustomGeometryStock,
        }[stock_kind]
        return variant.from_dict(data)


@dataclass(frozen=True, slots=True)
class BoxStock(StockDefinition):
    """Parametric rectangular stock positioned by a work frame."""

    size_x: Length
    size_y: Length
    size_z: Length
    frame: WcsFrame
    kind: ClassVar[StockKind] = StockKind.BOX

    def __post_init__(self) -> None:
        for value, subject in (
            (self.size_x, "Box size X"),
            (self.size_y, "Box size Y"),
            (self.size_z, "Box size Z"),
        ):
            _positive_known_length(value, subject)
        if not isinstance(self.frame, WcsFrame):
            raise CamValidationError("Box stock frame is invalid")
        units = {self.size_x.unit, self.size_y.unit, self.size_z.unit, self.frame.origin.unit}
        if len(units) != 1:
            raise CamUnitError("Box dimensions and frame must use one unit")

    def to_dict(self) -> dict[str, Any]:
        """Serialize this box stock."""
        return {
            "format": _STOCK_FORMAT,
            "format_version": _VERSION,
            "kind": self.kind.value,
            "size_x": _length_to_dict(self.size_x),
            "size_y": _length_to_dict(self.size_y),
            "size_z": _length_to_dict(self.size_z),
            "frame": self.frame.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BoxStock":
        """Deserialize only the exact box payload."""
        _strict_payload(
            data,
            format_name=_STOCK_FORMAT,
            version=_VERSION,
            fields={"kind", "size_x", "size_y", "size_z", "frame"},
        )
        if data["kind"] != StockKind.BOX.value:
            raise CamValidationError("Box payload has a different stock kind")
        return cls(
            _length_from_dict(data["size_x"]),
            _length_from_dict(data["size_y"]),
            _length_from_dict(data["size_z"]),
            WcsFrame.from_dict(data["frame"]),
        )


@dataclass(frozen=True, slots=True)
class CylinderStock(StockDefinition):
    """Parametric cylindrical stock defined consistently by diameter."""

    diameter: Length
    length: Length
    frame: WcsFrame
    kind: ClassVar[StockKind] = StockKind.CYLINDER

    def __post_init__(self) -> None:
        _positive_known_length(self.diameter, "Cylinder diameter")
        _positive_known_length(self.length, "Cylinder length")
        if not isinstance(self.frame, WcsFrame):
            raise CamValidationError("Cylinder stock frame is invalid")
        if len({self.diameter.unit, self.length.unit, self.frame.origin.unit}) != 1:
            raise CamUnitError("Cylinder dimensions and frame must use one unit")

    def to_dict(self) -> dict[str, Any]:
        """Serialize this cylindrical stock."""
        return {
            "format": _STOCK_FORMAT,
            "format_version": _VERSION,
            "kind": self.kind.value,
            "diameter": _length_to_dict(self.diameter),
            "length": _length_to_dict(self.length),
            "frame": self.frame.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CylinderStock":
        """Deserialize only the exact cylinder payload."""
        _strict_payload(
            data,
            format_name=_STOCK_FORMAT,
            version=_VERSION,
            fields={"kind", "diameter", "length", "frame"},
        )
        if data["kind"] != StockKind.CYLINDER.value:
            raise CamValidationError("Cylinder payload has a different stock kind")
        return cls(
            _length_from_dict(data["diameter"]),
            _length_from_dict(data["length"]),
            WcsFrame.from_dict(data["frame"]),
        )


@dataclass(frozen=True, slots=True)
class ModelStock(StockDefinition):
    """Stock derived from a persistent model reference without resolving it."""

    reference: GeometryReference
    kind: ClassVar[StockKind] = StockKind.FROM_MODEL

    def __post_init__(self) -> None:
        if not isinstance(self.reference, GeometryReference):
            raise CamValidationError("Model stock requires GeometryReference")

    @property
    def geometry_reference(self) -> GeometryReference:
        """Return referenced model geometry."""
        return self.reference

    def to_dict(self) -> dict[str, Any]:
        """Serialize this model-derived stock."""
        return {
            "format": _STOCK_FORMAT,
            "format_version": _VERSION,
            "kind": self.kind.value,
            "reference": self.reference.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelStock":
        """Deserialize only the exact from-model payload."""
        _strict_payload(
            data,
            format_name=_STOCK_FORMAT,
            version=_VERSION,
            fields={"kind", "reference"},
        )
        if data["kind"] != StockKind.FROM_MODEL.value:
            raise CamValidationError("Model payload has a different stock kind")
        return cls(GeometryReference.from_dict(data["reference"]))


@dataclass(frozen=True, slots=True)
class CustomGeometryStock(StockDefinition):
    """Stock backed by explicitly referenced custom geometry."""

    reference: GeometryReference
    kind: ClassVar[StockKind] = StockKind.CUSTOM_GEOMETRY

    def __post_init__(self) -> None:
        if not isinstance(self.reference, GeometryReference):
            raise CamValidationError("Custom stock requires GeometryReference")

    @property
    def geometry_reference(self) -> GeometryReference:
        """Return referenced custom geometry."""
        return self.reference

    def to_dict(self) -> dict[str, Any]:
        """Serialize this custom-geometry stock."""
        return {
            "format": _STOCK_FORMAT,
            "format_version": _VERSION,
            "kind": self.kind.value,
            "reference": self.reference.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CustomGeometryStock":
        """Deserialize only the exact custom-geometry payload."""
        _strict_payload(
            data,
            format_name=_STOCK_FORMAT,
            version=_VERSION,
            fields={"kind", "reference"},
        )
        if data["kind"] != StockKind.CUSTOM_GEOMETRY.value:
            raise CamValidationError("Custom payload has a different stock kind")
        return cls(GeometryReference.from_dict(data["reference"]))


@dataclass(frozen=True, slots=True)
class FixtureInstance:
    """One independently placed instance of referenced fixture geometry."""

    fixture_id: FixtureInstanceId
    name: str
    geometry_reference: GeometryReference
    transform: AffineTransform
    role: FixtureRole = FixtureRole.OTHER
    enabled: bool = True
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, FixtureInstanceId):
            raise CamValidationError("Fixture instance ID is invalid")
        object.__setattr__(self, "name", _name(self.name, "Fixture"))
        if not isinstance(self.geometry_reference, GeometryReference):
            raise CamValidationError("Fixture geometry reference is invalid")
        if not isinstance(self.transform, AffineTransform):
            raise CamValidationError("Fixture transform is invalid")
        if not isinstance(self.role, FixtureRole):
            raise CamValidationError("Fixture role is invalid")
        if type(self.enabled) is not bool:
            raise CamValidationError("Fixture enabled must be boolean")

    def to_dict(self) -> dict[str, Any]:
        """Serialize this fixture instance."""
        return {
            "format": _FIXTURE_FORMAT,
            "format_version": _VERSION,
            "fixture_id": str(self.fixture_id),
            "name": self.name,
            "geometry_reference": self.geometry_reference.to_dict(),
            "transform": self.transform.to_dict(),
            "role": self.role.value,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FixtureInstance":
        """Deserialize one complete fixture instance."""
        _strict_payload(
            data,
            format_name=_FIXTURE_FORMAT,
            version=_VERSION,
            fields={
                "fixture_id",
                "name",
                "geometry_reference",
                "transform",
                "role",
                "enabled",
            },
        )
        try:
            role = FixtureRole(data["role"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Fixture role payload is invalid") from error
        return cls(
            FixtureInstanceId.parse(data["fixture_id"]),
            data["name"],
            GeometryReference.from_dict(data["geometry_reference"]),
            AffineTransform.from_dict(data["transform"]),
            role,
            data["enabled"],
        )


@dataclass(frozen=True, slots=True)
class Setup:
    """Immutable setup snapshot owned and replaced by a CAM job."""

    setup_id: SetupId
    name: str
    kind: SetupKind
    wcs: WcsFrame
    work_offset: WorkOffset
    stock: StockDefinition
    model_reference: GeometryReference
    source_scope: SourceScope
    fixtures: tuple[FixtureInstance, ...] = ()
    enabled: bool = True
    operation_tree: OperationTree | None = None
    revision: Revision = Revision(0)
    SERIALIZATION_VERSION: ClassVar[int] = _SETUP_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.setup_id, SetupId):
            raise CamValidationError("Setup ID is invalid")
        object.__setattr__(self, "name", _name(self.name, "Setup"))
        if not isinstance(self.kind, SetupKind):
            raise CamValidationError("Setup kind is invalid")
        if not isinstance(self.wcs, WcsFrame):
            raise CamValidationError("Setup WCS is invalid")
        if not isinstance(self.work_offset, WorkOffset):
            raise CamValidationError("Setup work offset is invalid")
        if not isinstance(self.stock, StockDefinition):
            raise CamValidationError("Setup stock is invalid")
        if not isinstance(self.model_reference, GeometryReference):
            raise CamValidationError("Setup model reference is invalid")
        if not isinstance(self.source_scope, SourceScope):
            raise CamValidationError("Setup source scope is invalid")
        if self.model_reference.source_id != self.source_scope.primary_source_id:
            raise CamSourceScopeError("Machining model must use the primary source")
        if not isinstance(self.fixtures, tuple) or any(
            not isinstance(item, FixtureInstance) for item in self.fixtures
        ):
            raise CamValidationError("Setup fixtures must be an immutable tuple")
        fixture_ids = tuple(item.fixture_id for item in self.fixtures)
        if len(set(fixture_ids)) != len(fixture_ids):
            raise DuplicateCamIdError("Fixture instance IDs must be unique in a setup")
        if type(self.enabled) is not bool:
            raise CamValidationError("Setup enabled must be boolean")
        selected_tree = self.operation_tree or OperationTree.empty(self.setup_id)
        if not isinstance(selected_tree, OperationTree) or selected_tree.setup_id != self.setup_id:
            raise CamInvariantError("Operation tree must belong to this setup")
        object.__setattr__(self, "operation_tree", selected_tree)
        if not isinstance(self.revision, Revision):
            raise CamValidationError("Setup revision is invalid")
        if any(
            fixture.transform.translation_unit is not self.wcs.origin.unit
            for fixture in self.fixtures
        ):
            raise CamUnitError("Fixture transform unit must match setup WCS unit")
        self._validate_reference_scope()

    def _validate_reference_scope(self) -> None:
        references = [self.model_reference]
        if self.stock.geometry_reference is not None:
            references.append(self.stock.geometry_reference)
        references.extend(item.geometry_reference for item in self.fixtures)
        foreign = [item.source_id for item in references if not self.source_scope.allows(item)]
        if foreign:
            raise CamSourceScopeError(
                f"Geometry source is not declared by setup scope: {foreign[0]}"
            )

    def with_name(self, name: str) -> "Setup":
        """Return a renamed setup after full validation."""
        return replace(self, name=name)

    def with_wcs(self, wcs: WcsFrame) -> "Setup":
        """Return this setup with a validated WCS."""
        return replace(self, wcs=wcs)

    def with_stock(self, stock: StockDefinition) -> "Setup":
        """Return this setup with validated stock and source scope."""
        return replace(self, stock=stock)

    def with_fixture_added(self, fixture: FixtureInstance) -> "Setup":
        """Return this setup with one appended fixture."""
        if not isinstance(fixture, FixtureInstance):
            raise CamValidationError("Fixture is invalid")
        if any(item.fixture_id == fixture.fixture_id for item in self.fixtures):
            raise DuplicateCamIdError(f"Duplicate fixture ID: {fixture.fixture_id}")
        return replace(self, fixtures=(*self.fixtures, fixture))

    def with_fixture_updated(self, fixture: FixtureInstance) -> "Setup":
        """Return this setup with one existing fixture replaced in place."""
        if not isinstance(fixture, FixtureInstance):
            raise CamValidationError("Fixture is invalid")
        index = self._fixture_index(fixture.fixture_id)
        fixtures = list(self.fixtures)
        fixtures[index] = fixture
        return replace(self, fixtures=tuple(fixtures))

    def with_fixture_removed(self, fixture_id: FixtureInstanceId) -> "Setup":
        """Return this setup without one existing fixture."""
        index = self._fixture_index(fixture_id)
        fixtures = self.fixtures[:index] + self.fixtures[index + 1 :]
        return replace(self, fixtures=fixtures)

    def with_operation_tree(self, operation_tree: OperationTree) -> "Setup":
        """Replace the complete validated tree and increment setup revision once."""
        if not isinstance(operation_tree, OperationTree) or operation_tree.setup_id != self.setup_id:
            raise CamInvariantError("Operation tree must belong to this setup")
        if operation_tree == self.operation_tree:
            return self
        return replace(self, operation_tree=operation_tree, revision=self.revision.next())

    def _fixture_index(self, fixture_id: FixtureInstanceId) -> int:
        if not isinstance(fixture_id, FixtureInstanceId):
            raise CamValidationError("Fixture ID is invalid")
        for index, fixture in enumerate(self.fixtures):
            if fixture.fixture_id == fixture_id:
                return index
        raise CamChildNotFoundError(f"Fixture does not exist: {fixture_id}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize this setup while preserving fixture order."""
        return {
            "format": _SETUP_FORMAT,
            "format_version": _SETUP_VERSION,
            "setup_id": str(self.setup_id),
            "name": self.name,
            "kind": self.kind.value,
            "wcs": self.wcs.to_dict(),
            "work_offset": self.work_offset.to_dict(),
            "stock": self.stock.to_dict(),
            "model_reference": self.model_reference.to_dict(),
            "source_scope": self.source_scope.to_dict(),
            "fixtures": [fixture.to_dict() for fixture in self.fixtures],
            "enabled": self.enabled,
            "operation_tree": self.operation_tree.to_dict(),
            "revision": self.revision.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Setup":
        """Deserialize atomically into a fully validated setup."""
        if not isinstance(data, dict) or data.get("format") != _SETUP_FORMAT:
            from hms_cadcam.cam.domain.errors import UnsupportedCamSchemaError
            raise UnsupportedCamSchemaError("Unsupported setup format")
        version = data.get("format_version")
        if type(version) is not int or version not in {1, _SETUP_VERSION}:
            from hms_cadcam.cam.domain.errors import UnsupportedCamSchemaError
            raise UnsupportedCamSchemaError("Unsupported setup version")
        base_fields = {
                "setup_id",
                "name",
                "kind",
                "wcs",
                "work_offset",
                "stock",
                "model_reference",
                "source_scope",
                "fixtures",
                "enabled",
            }
        extra_fields = {"operation_tree", "revision"} if version == _SETUP_VERSION else set()
        if set(data) != {"format", "format_version", *base_fields, *extra_fields}:
            raise CamValidationError("Setup payload is malformed")
        fixtures = data["fixtures"]
        if not isinstance(fixtures, list):
            raise CamValidationError("Setup fixtures payload must be a list")
        try:
            setup_kind = SetupKind(data["kind"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Setup kind payload is invalid") from error
        return cls(
            setup_id=SetupId.parse(data["setup_id"]),
            name=data["name"],
            kind=setup_kind,
            wcs=WcsFrame.from_dict(data["wcs"]),
            work_offset=WorkOffset.from_dict(data["work_offset"]),
            stock=StockDefinition.from_dict(data["stock"]),
            model_reference=GeometryReference.from_dict(data["model_reference"]),
            source_scope=SourceScope.from_dict(data["source_scope"]),
            fixtures=tuple(FixtureInstance.from_dict(item) for item in fixtures),
            enabled=data["enabled"],
            operation_tree=(OperationTree.from_dict(data["operation_tree"]) if version == _SETUP_VERSION else None),
            revision=(Revision.from_dict(data["revision"]) if version == _SETUP_VERSION else Revision(0)),
        )
