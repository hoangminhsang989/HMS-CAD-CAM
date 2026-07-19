"""Immutable ordered CAM operation tree aggregate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid5

from hms_cadcam.cam.domain.dependency import DependencyEdge, DependencyGraph, DependencyKind
from hms_cadcam.cam.domain.errors import (
    CamChildNotFoundError,
    CamInvariantError,
    CamValidationError,
    DuplicateCamIdError,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.ids import CamNodeId, OperationId, SetupId
from hms_cadcam.cam.domain.operation import Operation
from hms_cadcam.cam.domain.revision import Revision

_NODE_FORMAT = "HMS_CAM_NODE"
_TREE_FORMAT = "HMS_CAM_OPERATION_TREE"
_VERSION = 1
_ROOT_NAMESPACE = UUID("f9a8c3da-491c-47d8-8be5-3b873a904100")


class CamNodeKind(StrEnum):
    GROUP = "group"
    OPERATION = "operation"


def _name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 255:
        raise CamValidationError("CAM node name is invalid")
    return value.strip()


@dataclass(frozen=True, slots=True)
class CamNode:
    node_id: CamNodeId
    kind: CamNodeKind
    name: str
    enabled: bool = True
    parent_id: CamNodeId | None = None
    child_ids: tuple[CamNodeId, ...] = ()
    operation_id: OperationId | None = None
    revision: Revision = Revision(0)
    metadata: tuple[tuple[str, str], ...] = ()
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, CamNodeId) or not isinstance(self.kind, CamNodeKind):
            raise CamValidationError("CAM node identity or kind is invalid")
        object.__setattr__(self, "name", _name(self.name))
        if type(self.enabled) is not bool:
            raise CamValidationError("CAM node enabled must be boolean")
        if self.parent_id is not None and not isinstance(self.parent_id, CamNodeId):
            raise CamValidationError("CAM node parent is invalid")
        if not isinstance(self.child_ids, tuple) or any(not isinstance(item, CamNodeId) for item in self.child_ids):
            raise CamValidationError("CAM node children must be an immutable tuple")
        if len(set(self.child_ids)) != len(self.child_ids):
            raise DuplicateCamIdError("CAM node child IDs must be unique")
        if self.kind is CamNodeKind.OPERATION:
            if self.child_ids or not isinstance(self.operation_id, OperationId):
                raise CamInvariantError("Operation node requires an operation and cannot have children")
        elif self.operation_id is not None:
            raise CamInvariantError("Group node cannot own an operation record")
        if not isinstance(self.revision, Revision):
            raise CamValidationError("CAM node revision is invalid")
        if not isinstance(self.metadata, tuple) or any(not isinstance(item, tuple) or len(item) != 2 or
                not all(isinstance(value, str) and value for value in item) for item in self.metadata):
            raise CamValidationError("CAM node metadata is invalid")
        normalized = tuple(sorted(self.metadata))
        if len({key for key, _ in normalized}) != len(normalized):
            raise CamInvariantError("CAM node metadata keys must be unique")
        object.__setattr__(self, "metadata", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {"format": _NODE_FORMAT, "format_version": _VERSION, "node_id": str(self.node_id),
            "kind": self.kind.value, "name": self.name, "enabled": self.enabled,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "child_ids": [str(item) for item in self.child_ids],
            "operation_id": str(self.operation_id) if self.operation_id else None,
            "revision": self.revision.to_dict(),
            "metadata": [{"key": key, "value": value} for key, value in self.metadata]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CamNode":
        fields = {"format", "format_version", "node_id", "kind", "name", "enabled", "parent_id",
                  "child_ids", "operation_id", "revision", "metadata"}
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("CAM node payload is malformed")
        if data["format"] != _NODE_FORMAT:
            raise UnsupportedCamSchemaError("Unsupported CAM node format")
        if type(data["format_version"]) is not int or data["format_version"] != _VERSION:
            raise UnsupportedCamSchemaError("Unsupported CAM node version")
        if not isinstance(data["child_ids"], list) or not isinstance(data["metadata"], list) or any(
            not isinstance(item, dict) or set(item) != {"key", "value"} for item in data["metadata"]):
            raise CamValidationError("CAM node collections are malformed")
        try:
            return cls(CamNodeId.parse(data["node_id"]), CamNodeKind(data["kind"]), data["name"], data["enabled"],
                CamNodeId.parse(data["parent_id"]) if data["parent_id"] else None,
                tuple(CamNodeId.parse(item) for item in data["child_ids"]),
                OperationId.parse(data["operation_id"]) if data["operation_id"] else None,
                Revision.from_dict(data["revision"]), tuple((item["key"], item["value"]) for item in data["metadata"]))
        except (TypeError, ValueError) as error:
            raise CamValidationError("CAM node payload is invalid") from error


@dataclass(frozen=True, slots=True)
class OperationTree:
    """One setup's tree, operation records and separate dependency DAG."""

    setup_id: SetupId
    root_id: CamNodeId
    nodes: tuple[CamNode, ...]
    operations: tuple[Operation, ...] = ()
    dependency_graph: DependencyGraph = DependencyGraph()
    revision: Revision = Revision(0)
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.setup_id, SetupId) or not isinstance(self.root_id, CamNodeId):
            raise CamValidationError("Operation tree identity is invalid")
        if not isinstance(self.nodes, tuple) or any(not isinstance(item, CamNode) for item in self.nodes):
            raise CamValidationError("Operation tree nodes must be an immutable tuple")
        if not isinstance(self.operations, tuple) or any(not isinstance(item, Operation) for item in self.operations):
            raise CamValidationError("Operation records must be an immutable tuple")
        if not isinstance(self.dependency_graph, DependencyGraph) or not isinstance(self.revision, Revision):
            raise CamValidationError("Operation tree graph or revision is invalid")
        node_map = {item.node_id: item for item in self.nodes}
        if len(node_map) != len(self.nodes):
            raise DuplicateCamIdError("CAM node IDs must be unique in one setup")
        if self.root_id not in node_map:
            raise CamChildNotFoundError("Operation tree root does not exist")
        root = node_map[self.root_id]
        if root.kind is not CamNodeKind.GROUP or root.parent_id is not None:
            raise CamInvariantError("Operation tree root must be a parentless group")
        seen_children: set[CamNodeId] = set()
        for node in self.nodes:
            if node.node_id != self.root_id and node.parent_id not in node_map:
                raise CamChildNotFoundError("CAM node parent does not exist")
            for child_id in node.child_ids:
                if child_id not in node_map or node_map[child_id].parent_id != node.node_id:
                    raise CamInvariantError("CAM parent and child links are inconsistent")
                if child_id in seen_children:
                    raise CamInvariantError("CAM node has multiple parents")
                seen_children.add(child_id)
        if seen_children != set(node_map) - {self.root_id}:
            raise CamInvariantError("Operation tree contains an orphan or disconnected node")
        visited: set[CamNodeId] = set()
        self._walk(self.root_id, node_map, set(), visited)
        if visited != set(node_map):
            raise CamInvariantError("Operation tree contains a disconnected cycle")
        operation_map = {item.operation_id: item for item in self.operations}
        if len(operation_map) != len(self.operations):
            raise DuplicateCamIdError("Operation IDs must be unique in one setup")
        node_operations = {node.operation_id for node in self.nodes if node.operation_id is not None}
        if node_operations != set(operation_map):
            raise CamInvariantError("Operation nodes and records are inconsistent")
        for operation in self.operations:
            if operation.setup_id != self.setup_id or node_map[operation.node_id].operation_id != operation.operation_id:
                raise CamInvariantError("Operation belongs to a different setup or node")
        if set(self.dependency_graph.operation_ids) != set(operation_map):
            raise CamInvariantError("Dependency graph does not match tree operations")

    @classmethod
    def empty(cls, setup_id: SetupId) -> "OperationTree":
        if not isinstance(setup_id, SetupId):
            raise CamValidationError("Setup ID is invalid")
        root_id = CamNodeId(uuid5(_ROOT_NAMESPACE, str(setup_id)))
        return cls(setup_id, root_id, (CamNode(root_id, CamNodeKind.GROUP, "Operations"),))

    @property
    def root(self) -> CamNode:
        return self.get_node(self.root_id)

    @property
    def is_empty(self) -> bool:
        return len(self.nodes) == 1

    def get_node(self, node_id: CamNodeId) -> CamNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise CamChildNotFoundError(f"CAM node does not exist: {node_id}")

    def get_operation(self, operation_id: OperationId) -> Operation:
        for operation in self.operations:
            if operation.operation_id == operation_id:
                return operation
        raise CamChildNotFoundError(f"Operation does not exist: {operation_id}")

    @staticmethod
    def _walk(node_id: CamNodeId, node_map: dict[CamNodeId, CamNode], visiting: set[CamNodeId], visited: set[CamNodeId]) -> None:
        if node_id in visiting:
            raise CamInvariantError("Operation tree contains a cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        visited.add(node_id)
        for child in node_map[node_id].child_ids:
            OperationTree._walk(child, node_map, visiting, visited)
        visiting.remove(node_id)

    def add_group(self, parent_id: CamNodeId, node_id: CamNodeId, name: str,
                  *, enabled: bool = True, metadata: tuple[tuple[str, str], ...] = ()) -> "OperationTree":
        parent = self.get_node(parent_id)
        if parent.kind is not CamNodeKind.GROUP:
            raise CamInvariantError("Only a group can contain a child")
        if any(item.node_id == node_id for item in self.nodes):
            raise DuplicateCamIdError(f"Duplicate CAM node ID: {node_id}")
        node = CamNode(node_id, CamNodeKind.GROUP, name, enabled, parent_id, metadata=metadata)
        return self._replace(nodes=(*self.nodes, node), updates={parent_id: replace(parent, child_ids=(*parent.child_ids, node_id), revision=parent.revision.next())})

    def add_operation(self, parent_id: CamNodeId, name: str, operation: Operation) -> "OperationTree":
        parent = self.get_node(parent_id)
        if parent.kind is not CamNodeKind.GROUP:
            raise CamInvariantError("Only a group can contain an operation")
        if operation.setup_id != self.setup_id:
            raise CamInvariantError("Operation belongs to another setup")
        if any(item.node_id == operation.node_id for item in self.nodes):
            raise DuplicateCamIdError(f"Duplicate CAM node ID: {operation.node_id}")
        if any(item.operation_id == operation.operation_id for item in self.operations):
            raise DuplicateCamIdError(f"Duplicate operation ID: {operation.operation_id}")
        node = CamNode(operation.node_id, CamNodeKind.OPERATION, name, operation.enabled, parent_id, operation_id=operation.operation_id)
        graph = self.dependency_graph.with_operation_added(operation.operation_id)
        return self._replace(nodes=(*self.nodes, node), operations=(*self.operations, operation), graph=graph,
            updates={parent_id: replace(parent, child_ids=(*parent.child_ids, node.node_id), revision=parent.revision.next())})

    def remove_node(self, node_id: CamNodeId) -> "OperationTree":
        """Recursively remove a group subtree and all owned operations."""
        if node_id == self.root_id:
            raise CamInvariantError("Operation tree root cannot be removed")
        node = self.get_node(node_id)
        removed_nodes: set[CamNodeId] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            removed_nodes.add(current)
            stack.extend(self.get_node(current).child_ids)
        removed_operations = frozenset(item.operation_id for item in self.nodes if item.node_id in removed_nodes and item.operation_id is not None)
        parent = self.get_node(node.parent_id)  # type: ignore[arg-type]
        return self._replace(nodes=tuple(item for item in self.nodes if item.node_id not in removed_nodes),
            operations=tuple(item for item in self.operations if item.operation_id not in removed_operations),
            graph=self.dependency_graph.without_operations(removed_operations),
            updates={parent.node_id: replace(parent, child_ids=tuple(item for item in parent.child_ids if item != node_id), revision=parent.revision.next())})

    def rename_node(self, node_id: CamNodeId, name: str) -> "OperationTree":
        node = self.get_node(node_id)
        normalized = _name(name)
        if normalized == node.name:
            return self
        return self._replace(updates={node_id: replace(node, name=normalized, revision=node.revision.next())})

    def set_enabled(self, node_id: CamNodeId, enabled: bool) -> "OperationTree":
        node = self.get_node(node_id)
        if type(enabled) is not bool:
            raise CamValidationError("CAM node enabled must be boolean")
        if enabled == node.enabled:
            return self
        operations = self.operations
        if node.operation_id is not None:
            operations = tuple(item.with_enabled(enabled) if item.operation_id == node.operation_id else item for item in operations)
        return self._replace(operations=operations, updates={node_id: replace(node, enabled=enabled, revision=node.revision.next())})

    def replace_operation(self, operation: Operation) -> "OperationTree":
        """Replace one complete operation snapshot without changing its tree identity."""
        if not isinstance(operation, Operation) or operation.setup_id != self.setup_id:
            raise CamValidationError("Replacement operation is invalid for this setup")
        current = self.get_operation(operation.operation_id)
        if current.node_id != operation.node_id:
            raise CamInvariantError("Replacement operation cannot change its node identity")
        if current == operation:
            return self
        node = self.get_node(operation.node_id)
        operations = tuple(operation if item.operation_id == operation.operation_id else item
                           for item in self.operations)
        return self._replace(operations=operations,
            updates={node.node_id: replace(node, enabled=operation.enabled,
                                           revision=node.revision.next())})

    def move_node(self, node_id: CamNodeId, new_parent_id: CamNodeId, new_index: int | None = None) -> "OperationTree":
        if node_id == self.root_id:
            raise CamInvariantError("Operation tree root cannot be moved")
        node = self.get_node(node_id)
        old_parent = self.get_node(node.parent_id)  # type: ignore[arg-type]
        new_parent = self.get_node(new_parent_id)
        if new_parent.kind is not CamNodeKind.GROUP:
            raise CamInvariantError("New parent must be a group")
        descendants = self._descendants(node_id)
        if new_parent_id == node_id or new_parent_id in descendants:
            raise CamInvariantError("Moving a node would create a cycle")
        target = list(new_parent.child_ids)
        if old_parent.node_id == new_parent.node_id:
            target.remove(node_id)
        index = len(target) if new_index is None else new_index
        if type(index) is not int or not 0 <= index <= len(target):
            raise CamValidationError("CAM node position is out of range")
        target.insert(index, node_id)
        updates = {node_id: replace(node, parent_id=new_parent_id, revision=node.revision.next()),
                   new_parent.node_id: replace(new_parent, child_ids=tuple(target), revision=new_parent.revision.next())}
        if old_parent.node_id != new_parent.node_id:
            updates[old_parent.node_id] = replace(old_parent, child_ids=tuple(item for item in old_parent.child_ids if item != node_id), revision=old_parent.revision.next())
        return self._replace(updates=updates)

    def reorder_node(self, node_id: CamNodeId, new_index: int) -> "OperationTree":
        node = self.get_node(node_id)
        if node_id == self.root_id:
            raise CamInvariantError("Operation tree root cannot be reordered")
        parent = self.get_node(node.parent_id)  # type: ignore[arg-type]
        if type(new_index) is not int or not 0 <= new_index < len(parent.child_ids):
            raise CamValidationError("CAM node position is out of range")
        old = parent.child_ids.index(node_id)
        if old == new_index:
            return self
        children = list(parent.child_ids)
        children.pop(old)
        children.insert(new_index, node_id)
        return self._replace(updates={parent.node_id: replace(parent, child_ids=tuple(children), revision=parent.revision.next())})

    def with_dependency_added(self, edge: DependencyEdge) -> "OperationTree":
        return self._replace(graph=self.dependency_graph.with_edge_added(edge))

    def mark_dependency_changed(self, kind: DependencyKind, source_key: str) -> "OperationTree":
        operations = self.dependency_graph.propagate_dirty(self.operations, kind, source_key)
        if operations == self.operations:
            return self
        return self._replace(operations=operations)

    def _descendants(self, node_id: CamNodeId) -> frozenset[CamNodeId]:
        result: set[CamNodeId] = set()
        stack = list(self.get_node(node_id).child_ids)
        while stack:
            current = stack.pop()
            result.add(current)
            stack.extend(self.get_node(current).child_ids)
        return frozenset(result)

    def _replace(self, *, nodes: tuple[CamNode, ...] | None = None, operations: tuple[Operation, ...] | None = None,
                 graph: DependencyGraph | None = None, updates: dict[CamNodeId, CamNode] | None = None) -> "OperationTree":
        selected = nodes if nodes is not None else self.nodes
        if updates:
            selected = tuple(updates.get(item.node_id, item) for item in selected)
        return OperationTree(self.setup_id, self.root_id, selected,
            operations if operations is not None else self.operations,
            graph if graph is not None else self.dependency_graph, self.revision.next())

    def to_dict(self) -> dict[str, Any]:
        return {"format": _TREE_FORMAT, "format_version": _VERSION, "setup_id": str(self.setup_id),
                "root_id": str(self.root_id), "nodes": [item.to_dict() for item in self.nodes],
                "operations": [item.to_dict() for item in self.operations],
                "dependency_graph": self.dependency_graph.to_dict(), "revision": self.revision.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperationTree":
        fields = {"format", "format_version", "setup_id", "root_id", "nodes", "operations", "dependency_graph", "revision"}
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Operation tree payload is malformed")
        if data["format"] != _TREE_FORMAT:
            raise UnsupportedCamSchemaError("Unsupported operation tree format")
        if type(data["format_version"]) is not int or data["format_version"] != _VERSION:
            raise UnsupportedCamSchemaError("Unsupported operation tree version")
        if not isinstance(data["nodes"], list) or not isinstance(data["operations"], list):
            raise CamValidationError("Operation tree children must be lists")
        return cls(SetupId.parse(data["setup_id"]), CamNodeId.parse(data["root_id"]),
            tuple(CamNode.from_dict(item) for item in data["nodes"]),
            tuple(Operation.from_dict(item) for item in data["operations"]),
            DependencyGraph.from_dict(data["dependency_graph"]), Revision.from_dict(data["revision"]))
