"""Stable, versioned keys for persistent CAD topology-tree state."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

from hms_cadcam.cad.models import (
    CadDocumentTree,
    CadGeometryKind,
    CadObjectId,
    CadObjectNode,
)

_PATH_PATTERN = re.compile(r"[a-z_]+:[0-9a-f]{32}(?:/[a-z_]+:[0-9a-f]{32})*")


class TopologyPathVersion(IntEnum):
    """Algorithms supported for deriving persistent topology paths."""

    V1 = 1


@dataclass(frozen=True, slots=True)
class TopologyPath:
    """A deterministic path that contains no runtime CAD identifiers."""

    value: str

    def __post_init__(self) -> None:
        if not _PATH_PATTERN.fullmatch(self.value):
            raise ValueError("Invalid persistent topology path")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PersistentCadObjectKey:
    """Serializable identity for one unambiguous persistent CAD tree node."""

    source_id: UUID
    geometry_kind: CadGeometryKind
    topology_path_version: TopologyPathVersion
    topology_path: TopologyPath

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, UUID):
            raise TypeError("Persistent CAD source_id must be UUID")
        if not isinstance(self.geometry_kind, CadGeometryKind):
            raise TypeError("Persistent CAD geometry_kind is invalid")
        if not isinstance(self.topology_path_version, TopologyPathVersion):
            raise TypeError("Persistent CAD topology path version is invalid")
        if not isinstance(self.topology_path, TopologyPath):
            raise TypeError("Persistent CAD topology path is invalid")


@dataclass(frozen=True, slots=True)
class PersistentCadObjectMap:
    """Bidirectional runtime mapping plus count of safely skipped nodes."""

    by_runtime: dict[CadObjectId, PersistentCadObjectKey]
    by_persistent: dict[PersistentCadObjectKey, CadObjectId]
    ambiguous_nodes: int = 0


def build_persistent_object_map(
    source_id: UUID,
    geometry_kind: CadGeometryKind,
    tree: CadDocumentTree,
) -> PersistentCadObjectMap:
    """Map presentation leaves without depending on document IDs or child order."""
    signatures = {node.object_id: _node_signature(node) for node in tree.root.walk()}
    by_runtime: dict[CadObjectId, PersistentCadObjectKey] = {}
    ambiguous_nodes = 0

    def visit(parent_path: str, children: tuple[CadObjectNode, ...]) -> None:
        nonlocal ambiguous_nodes
        counts: dict[tuple[str, str], int] = {}
        for child in children:
            token = (child.kind.value, signatures[child.object_id])
            counts[token] = counts.get(token, 0) + 1
        for child in children:
            signature = signatures[child.object_id]
            token = (child.kind.value, signature)
            if counts[token] != 1:
                ambiguous_nodes += len(child.walk())
                continue
            segment = f"{child.kind.value}:{signature[:32]}"
            path_value = f"{parent_path}/{segment}" if parent_path else segment
            if child.has_presentation:
                key = PersistentCadObjectKey(
                    source_id=source_id,
                    geometry_kind=geometry_kind,
                    topology_path_version=TopologyPathVersion.V1,
                    topology_path=TopologyPath(path_value),
                )
                by_runtime[child.object_id] = key
            visit(path_value, child.children)

    visit("", tree.root.children)
    return PersistentCadObjectMap(
        by_runtime=by_runtime,
        by_persistent={key: object_id for object_id, key in by_runtime.items()},
        ambiguous_nodes=ambiguous_nodes,
    )


def _node_signature(node: CadObjectNode) -> str:
    child_signatures = sorted(_node_signature(child) for child in node.children)
    bounds = node.bounding_box
    payload = {
        "bounds": [
            _canonical_float(value)
            for value in (
                bounds.x_min,
                bounds.y_min,
                bounds.z_min,
                bounds.x_max,
                bounds.y_max,
                bounds.z_max,
            )
        ],
        "children": child_signatures,
        "kind": node.kind.value,
        "presentation": node.has_presentation,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_float(value: float) -> str:
    return float(value).hex()
