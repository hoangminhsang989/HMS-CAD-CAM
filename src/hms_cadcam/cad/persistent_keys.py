"""Stable, versioned keys for persistent CAD topology-tree state."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum, IntEnum
from uuid import UUID

from hms_cadcam.cad.models import (
    CadDocumentTree,
    CadGeometryKind,
    CadObjectId,
    CadObjectNode,
    XcafNodeRole,
)

_PATH_PATTERN = re.compile(r"[a-z_]+:[0-9a-f]{32}(?:/[a-z_]+:[0-9a-f]{32})*")
_PRODUCT_IDENTITY_PATTERN = re.compile(r"product:[0-9a-f]{32}")


class TopologyPathVersion(IntEnum):
    """Algorithms supported for deriving persistent topology paths."""

    V1 = 1


class PersistentKeyScheme(str, Enum):
    """Independent algorithms used by persisted CAD object identities."""

    XCAF_OCCURRENCE = "xcaf_occurrence"


class XcafOccurrenceKeyVersion(IntEnum):
    """Algorithms supported for XCAF occurrence paths."""

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
class XcafOccurrencePath:
    """Deterministic parent-to-child occurrence path without runtime IDs."""

    value: str

    def __post_init__(self) -> None:
        if not _PATH_PATTERN.fullmatch(self.value):
            raise ValueError("Invalid persistent XCAF occurrence path")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class XcafProductIdentity:
    """Conservative product fingerprint used to validate occurrence matches."""

    value: str

    def __post_init__(self) -> None:
        if not _PRODUCT_IDENTITY_PATTERN.fullmatch(self.value):
            raise ValueError("Invalid persistent XCAF product identity")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PersistentXcafOccurrenceKey:
    """Serializable identity for one unambiguous XCAF part occurrence."""

    source_id: UUID
    geometry_kind: CadGeometryKind
    key_scheme: PersistentKeyScheme
    key_version: XcafOccurrenceKeyVersion
    occurrence_path: XcafOccurrencePath
    product_identity: XcafProductIdentity
    occurrence_role: XcafNodeRole

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, UUID):
            raise TypeError("Persistent XCAF source_id must be UUID")
        if self.geometry_kind is not CadGeometryKind.BREP:
            raise ValueError("Persistent XCAF occurrence must use BREP geometry")
        if self.key_scheme is not PersistentKeyScheme.XCAF_OCCURRENCE:
            raise ValueError("Unsupported persistent XCAF key scheme")
        if not isinstance(self.key_version, XcafOccurrenceKeyVersion):
            raise TypeError("Persistent XCAF key version is invalid")
        if not isinstance(self.occurrence_path, XcafOccurrencePath):
            raise TypeError("Persistent XCAF occurrence path is invalid")
        if not isinstance(self.product_identity, XcafProductIdentity):
            raise TypeError("Persistent XCAF product identity is invalid")
        if not isinstance(self.occurrence_role, XcafNodeRole):
            raise TypeError("Persistent XCAF occurrence role is invalid")


PersistentObjectKey = PersistentCadObjectKey | PersistentXcafOccurrenceKey


@dataclass(frozen=True, slots=True)
class PersistentCadObjectMap:
    """Bidirectional runtime mapping plus count of safely skipped nodes."""

    by_runtime: dict[CadObjectId, PersistentObjectKey]
    by_persistent: dict[PersistentObjectKey, CadObjectId]
    ambiguous_nodes: int = 0


def build_persistent_object_map(
    source_id: UUID,
    geometry_kind: CadGeometryKind,
    tree: CadDocumentTree,
) -> PersistentCadObjectMap:
    """Map presentation leaves without depending on document IDs or child order."""
    if any(node.occurrence_id is not None for node in tree.root.walk()):
        return _build_xcaf_occurrence_map(source_id, geometry_kind, tree)
    signatures = {node.object_id: _node_signature(node) for node in tree.root.walk()}
    by_runtime: dict[CadObjectId, PersistentObjectKey] = {}
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


def _build_xcaf_occurrence_map(
    source_id: UUID,
    geometry_kind: CadGeometryKind,
    tree: CadDocumentTree,
) -> PersistentCadObjectMap:
    if geometry_kind is not CadGeometryKind.BREP:
        raise ValueError("XCAF occurrence persistence requires BREP geometry")
    product_signatures = {
        node.object_id: _xcaf_product_signature(node)
        for node in tree.root.walk()
        if node.occurrence_id is not None
    }
    by_runtime: dict[CadObjectId, PersistentObjectKey] = {}
    ambiguous_nodes = 0

    def segment(node: CadObjectNode) -> str:
        assert node.xcaf_role is not None and node.absolute_transform is not None
        payload = {
            "name": node.label.strip(),
            "product": product_signatures[node.object_id],
            "product_name": node.product_name,
            "role": node.xcaf_role.value,
            "transform": [
                _xcaf_canonical_float(value)
                for value in node.absolute_transform.values
            ],
        }
        return _digest(payload)[:32]

    def visit(parent_path: str, children: tuple[CadObjectNode, ...]) -> None:
        nonlocal ambiguous_nodes
        candidates = tuple(child for child in children if child.occurrence_id is not None)
        tokens = [(child.kind.value, segment(child)) for child in candidates]
        counts = {token: tokens.count(token) for token in set(tokens)}
        for child, token in zip(candidates, tokens, strict=True):
            if counts[token] != 1:
                ambiguous_nodes += len(child.walk())
                continue
            path_value = (
                f"{parent_path}/{token[0]}:{token[1]}"
                if parent_path
                else f"{token[0]}:{token[1]}"
            )
            if child.has_presentation:
                assert child.xcaf_role is not None
                key = PersistentXcafOccurrenceKey(
                    source_id=source_id,
                    geometry_kind=geometry_kind,
                    key_scheme=PersistentKeyScheme.XCAF_OCCURRENCE,
                    key_version=XcafOccurrenceKeyVersion.V1,
                    occurrence_path=XcafOccurrencePath(path_value),
                    product_identity=XcafProductIdentity(
                        f"product:{product_signatures[child.object_id][:32]}"
                    ),
                    occurrence_role=child.xcaf_role,
                )
                by_runtime[child.object_id] = key
            visit(path_value, child.children)

    visit("", tree.root.children)
    return PersistentCadObjectMap(
        by_runtime=by_runtime,
        by_persistent={key: object_id for object_id, key in by_runtime.items()},
        ambiguous_nodes=ambiguous_nodes,
    )


def _xcaf_product_signature(node: CadObjectNode) -> str:
    if node.occurrence_id is None or node.xcaf_role is None:
        raise ValueError("XCAF product signature requires occurrence metadata")
    bounds = node.bounding_box
    payload = {
        "children": sorted(
            _xcaf_product_signature(child)
            for child in node.children
            if child.occurrence_id is not None
        ),
        "dimensions": [
            _xcaf_canonical_float(bounds.x_max - bounds.x_min),
            _xcaf_canonical_float(bounds.y_max - bounds.y_min),
            _xcaf_canonical_float(bounds.z_max - bounds.z_min),
        ],
        "product_name": node.product_name,
        "role": node.xcaf_role.value,
    }
    return _digest(payload)


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
    return _digest(payload)


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_float(value: float) -> str:
    return float(value).hex()


def _xcaf_canonical_float(value: float) -> str:
    """Suppress OCCT transfer tolerance noise without using native identity."""
    return f"{round(float(value), 6):.6f}"
