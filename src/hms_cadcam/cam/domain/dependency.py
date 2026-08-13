"""Typed operation dependency DAG and deterministic dirty propagation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, ClassVar

from hms_cadcam.cam.domain.errors import (
    CamChildNotFoundError,
    CamInvariantError,
    CamValidationError,
    DuplicateCamIdError,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.ids import OperationId
from hms_cadcam.cam.domain.operation import DirtyReason, Operation

_FORMAT = "HMS_CAM_DEPENDENCY_GRAPH"
_VERSION = 1


class DependencyKind(StrEnum):
    GEOMETRY = "geometry"
    WCS = "wcs"
    STOCK = "stock"
    FIXTURE = "fixture"
    TOOL = "tool"
    MACHINE = "machine"
    PARAMETERS = "parameters"
    OPERATION_OUTPUT = "operation_output"
    MATERIAL_STATE = "material_state"


_DIRTY_REASON = {
    DependencyKind.GEOMETRY: DirtyReason.GEOMETRY_CHANGED,
    DependencyKind.WCS: DirtyReason.WCS_CHANGED,
    DependencyKind.STOCK: DirtyReason.STOCK_CHANGED,
    DependencyKind.FIXTURE: DirtyReason.FIXTURE_CHANGED,
    DependencyKind.TOOL: DirtyReason.TOOL_CHANGED,
    DependencyKind.MACHINE: DirtyReason.MACHINE_CHANGED,
    DependencyKind.PARAMETERS: DirtyReason.PARAMETERS_CHANGED,
    DependencyKind.OPERATION_OUTPUT: DirtyReason.UPSTREAM_CHANGED,
    DependencyKind.MATERIAL_STATE: DirtyReason.UPSTREAM_CHANGED,
}


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    """One dependency source targeting an artifact-producing operation."""

    kind: DependencyKind
    source_key: str
    target_operation_id: OperationId

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DependencyKind):
            raise CamValidationError("Dependency kind is invalid")
        if not isinstance(self.source_key, str) or not self.source_key.strip() or len(self.source_key) > 512:
            raise CamValidationError("Dependency source key is invalid")
        if not isinstance(self.target_operation_id, OperationId):
            raise CamValidationError("Dependency target operation is invalid")

    @classmethod
    def operation_output(cls, source: OperationId, target: OperationId) -> "DependencyEdge":
        if not isinstance(source, OperationId):
            raise CamValidationError("Dependency source operation is invalid")
        return cls(DependencyKind.OPERATION_OUTPUT, str(source), target)

    @classmethod
    def material_state(cls, source: OperationId, target: OperationId) -> "DependencyEdge":
        if not isinstance(source, OperationId):
            raise CamValidationError("Material-state source operation is invalid")
        return cls(DependencyKind.MATERIAL_STATE, str(source), target)

    @property
    def source_operation_id(self) -> OperationId | None:
        if self.kind not in {DependencyKind.OPERATION_OUTPUT, DependencyKind.MATERIAL_STATE}:
            return None
        return OperationId.parse(self.source_key)

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "source_key": self.source_key,
                "target_operation_id": str(self.target_operation_id)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DependencyEdge":
        if not isinstance(data, dict) or set(data) != {"kind", "source_key", "target_operation_id"}:
            raise CamValidationError("Dependency edge payload is malformed")
        try:
            return cls(DependencyKind(data["kind"]), data["source_key"], OperationId.parse(data["target_operation_id"]))
        except (TypeError, ValueError) as error:
            raise CamValidationError("Dependency edge payload is invalid") from error


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """Immutable DAG; only operation-output edges participate in cycle ordering."""

    operation_ids: tuple[OperationId, ...] = ()
    edges: tuple[DependencyEdge, ...] = ()
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.operation_ids, tuple) or any(not isinstance(item, OperationId) for item in self.operation_ids):
            raise CamValidationError("Dependency graph operation IDs must be a tuple")
        if len(set(self.operation_ids)) != len(self.operation_ids):
            raise DuplicateCamIdError("Dependency graph operation IDs must be unique")
        if not isinstance(self.edges, tuple) or any(not isinstance(item, DependencyEdge) for item in self.edges):
            raise CamValidationError("Dependency graph edges must be a tuple")
        if len(set(self.edges)) != len(self.edges):
            raise DuplicateCamIdError("Duplicate dependency edge")
        known = set(self.operation_ids)
        for edge in self.edges:
            if edge.target_operation_id not in known:
                raise CamChildNotFoundError("Dependency target operation does not exist")
            source = edge.source_operation_id
            if source is not None and source not in known:
                raise CamChildNotFoundError("Dependency source operation does not exist")
        self._topological_order()

    def with_operation_added(self, operation_id: OperationId) -> "DependencyGraph":
        if operation_id in self.operation_ids:
            raise DuplicateCamIdError(f"Duplicate dependency operation: {operation_id}")
        return replace(self, operation_ids=(*self.operation_ids, operation_id))

    def without_operations(self, removed: frozenset[OperationId]) -> "DependencyGraph":
        return DependencyGraph(tuple(item for item in self.operation_ids if item not in removed),
            tuple(edge for edge in self.edges if edge.target_operation_id not in removed and edge.source_operation_id not in removed))

    def with_edge_added(self, edge: DependencyEdge) -> "DependencyGraph":
        if edge in self.edges:
            raise DuplicateCamIdError("Duplicate dependency edge")
        return replace(self, edges=(*self.edges, edge))

    def without_edge(self, edge: DependencyEdge) -> "DependencyGraph":
        if edge not in self.edges:
            raise CamChildNotFoundError("Dependency edge does not exist")
        return replace(self, edges=tuple(item for item in self.edges if item != edge))

    @property
    def topological_order(self) -> tuple[OperationId, ...]:
        return self._topological_order()

    def _topological_order(self) -> tuple[OperationId, ...]:
        incoming = {item: 0 for item in self.operation_ids}
        outgoing: dict[OperationId, list[OperationId]] = {item: [] for item in self.operation_ids}
        for edge in self.edges:
            source = edge.source_operation_id
            if source is not None:
                incoming[edge.target_operation_id] += 1
                outgoing[source].append(edge.target_operation_id)
        ready = sorted((item for item, count in incoming.items() if count == 0), key=str)
        result: list[OperationId] = []
        while ready:
            current = ready.pop(0)
            result.append(current)
            for target in sorted(outgoing[current], key=str):
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
                    ready.sort(key=str)
        if len(result) != len(self.operation_ids):
            raise CamInvariantError("Dependency graph contains a cycle")
        return tuple(result)

    def affected_operations(self, kind: DependencyKind, source_key: str) -> tuple[OperationId, ...]:
        direct = {edge.target_operation_id for edge in self.edges if edge.kind is kind and edge.source_key == source_key}
        affected = set(direct)
        queue = sorted(direct, key=str)
        while queue:
            source = queue.pop(0)
            for edge in self.edges:
                if (edge.kind in {DependencyKind.OPERATION_OUTPUT, DependencyKind.MATERIAL_STATE}
                        and edge.source_operation_id == source
                        and edge.target_operation_id not in affected):
                    affected.add(edge.target_operation_id)
                    queue.append(edge.target_operation_id)
        return tuple(item for item in self.topological_order if item in affected)

    def propagate_dirty(self, operations: tuple[Operation, ...], kind: DependencyKind,
                        source_key: str) -> tuple[Operation, ...]:
        by_id = {item.operation_id: item for item in operations}
        if set(by_id) != set(self.operation_ids):
            raise CamInvariantError("Operation records do not match dependency graph")
        directly = {edge.target_operation_id for edge in self.edges if edge.kind is kind and edge.source_key == source_key}
        affected = set(self.affected_operations(kind, source_key))
        result = []
        for operation in operations:
            if operation.operation_id not in affected:
                result.append(operation)
                continue
            reason = _DIRTY_REASON[kind] if operation.operation_id in directly else DirtyReason.UPSTREAM_CHANGED
            result.append(replace(operation, artifact_state=operation.artifact_state.mark_dirty(reason)))
        return tuple(result)

    def to_dict(self) -> dict[str, Any]:
        return {"format": _FORMAT, "format_version": _VERSION,
                "operation_ids": [str(item) for item in self.operation_ids],
                "edges": [item.to_dict() for item in self.edges]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DependencyGraph":
        if not isinstance(data, dict) or set(data) != {"format", "format_version", "operation_ids", "edges"}:
            raise CamValidationError("Dependency graph payload is malformed")
        if data["format"] != _FORMAT:
            raise UnsupportedCamSchemaError("Unsupported dependency graph format")
        if type(data["format_version"]) is not int or data["format_version"] != _VERSION:
            raise UnsupportedCamSchemaError("Unsupported dependency graph version")
        if not isinstance(data["operation_ids"], list) or not isinstance(data["edges"], list):
            raise CamValidationError("Dependency graph children must be lists")
        return cls(tuple(OperationId.parse(item) for item in data["operation_ids"]),
                   tuple(DependencyEdge.from_dict(item) for item in data["edges"]))
