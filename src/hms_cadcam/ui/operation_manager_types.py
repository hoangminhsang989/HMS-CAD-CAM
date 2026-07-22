"""Native-free value types shared by Operation Manager presentation modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable
from uuid import UUID


class OperationManagerNodeKind(StrEnum):
    PROJECT = "project"
    JOB = "job"
    SETUP = "setup"
    MACHINE_GROUP = "machine_group"
    GEOMETRY = "geometry"
    STOCK = "stock"
    TOOLS = "tools"
    TOOL = "tool"
    OPERATIONS = "operations"
    GROUP = "group"
    OPERATION = "operation"
    OPERATION_GEOMETRY = "operation_geometry"
    OPERATION_TOOL = "operation_tool"
    TOOLPATH = "toolpath"
    SIMULATION = "simulation"
    POST_RESULT = "post_result"
    NC_ARTIFACT = "nc_artifact"
    PROGRAM_ASSEMBLY = "program_assembly"
    EMPTY_STATE = "empty_state"


class OperationManagerEntityKind(StrEnum):
    PROJECT = "project"
    JOB = "job"
    SETUP = "setup"
    CAM_NODE = "cam_node"
    OPERATION = "operation"
    GEOMETRY_REFERENCE = "geometry_reference"
    STOCK = "stock"
    TOOL_ASSEMBLY = "tool_assembly"
    TOOLPATH_ARTIFACT = "toolpath_artifact"
    SIMULATION_RESULT = "simulation_result"
    POST_RESULT = "post_result"
    NC_ARTIFACT = "nc_artifact"
    PROGRAM_ASSEMBLY = "program_assembly"


class OperationManagerStatusCategory(StrEnum):
    DOMAIN = "domain"
    CALCULATION = "calculation"
    SIMULATION = "simulation"
    POST = "post"
    NC = "nc"
    EXPORT = "export"


class OperationManagerSemanticStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_INPUT = "needs_input"
    READY = "ready"
    CALCULATING = "calculating"
    CURRENT = "current"
    STALE = "stale"
    WARNING = "warning"
    BLOCKED = "blocked"
    FAILED = "failed"
    DISABLED = "disabled"
    ACTIVE = "active"
    MISSING = "missing"


class OperationManagerFilter(StrEnum):
    ALL = "all"
    ENABLED = "enabled"
    DISABLED = "disabled"
    NEEDS_CALCULATION = "needs_calculation"
    STALE = "stale"
    WARNINGS = "warnings"
    ERRORS = "errors"


class OperationManagerCapability(StrEnum):
    OPEN = "open"
    ADD_OPERATION = "add_operation"
    RECALCULATE = "recalculate"
    SIMULATE = "simulate"
    POST = "post"
    DELETE = "delete"
    DUPLICATE = "duplicate"
    RENAME = "rename"
    ENABLE = "enable"
    DISABLE = "disable"
    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    BIND_GEOMETRY = "bind_geometry"
    CLEAR_GEOMETRY = "clear_geometry"
    TOGGLE_TOOLPATH = "toggle_toolpath"
    CLEAR_SIMULATION = "clear_simulation"
    CLEAR_POST = "clear_post"
    CLEAR_NC = "clear_nc"


@dataclass(frozen=True, slots=True)
class OperationManagerNodeId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("Operation Manager node ID must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class OperationManagerDomainIdentity:
    kind: OperationManagerEntityKind
    value: str


@dataclass(frozen=True, slots=True)
class OperationManagerLegacySelection:
    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class OperationManagerStatus:
    category: OperationManagerStatusCategory
    semantic: OperationManagerSemanticStatus
    text: str
    tooltip: str

    @property
    def is_stale(self) -> bool:
        return self.semantic is OperationManagerSemanticStatus.STALE

    @property
    def is_current(self) -> bool:
        return self.semantic is OperationManagerSemanticStatus.CURRENT


@dataclass(frozen=True, slots=True)
class OperationManagerNode:
    node_id: OperationManagerNodeId
    domain_identity: OperationManagerDomainIdentity
    parent_id: OperationManagerNodeId | None
    kind: OperationManagerNodeKind
    label: str
    secondary_summary: str
    statuses: tuple[OperationManagerStatus, ...]
    enabled: bool
    order: int
    counts: tuple[tuple[str, int], ...] = ()
    capabilities: tuple[OperationManagerCapability, ...] = ()
    children: tuple[OperationManagerNodeId, ...] = ()
    search_terms: tuple[str, ...] = ()
    legacy_selection: OperationManagerLegacySelection | None = None
    default_expanded: bool = False

    @property
    def status(self) -> OperationManagerStatus:
        return self.statuses[0]

    @property
    def is_stale(self) -> bool:
        return any(item.is_stale for item in self.statuses)

    @property
    def is_current(self) -> bool:
        return any(item.is_current for item in self.statuses)

    @property
    def searchable_text(self) -> str:
        values = (
            self.label,
            self.secondary_summary,
            self.kind.value,
            self.domain_identity.kind.value,
            self.domain_identity.value,
            *(item.semantic.value for item in self.statuses),
            *(item.text for item in self.statuses),
            *self.search_terms,
        )
        return " ".join(values).casefold()


@dataclass(frozen=True, slots=True)
class OperationManagerHeader:
    project_name: str
    active_job: str
    active_setup: str
    machine: str
    operation_count: int
    warning_count: int
    error_count: int


@dataclass(frozen=True, slots=True)
class OperationManagerProjection:
    project_id: UUID | None
    roots: tuple[OperationManagerNodeId, ...]
    nodes: tuple[OperationManagerNode, ...]
    header: OperationManagerHeader

    def node(self, node_id: OperationManagerNodeId) -> OperationManagerNode | None:
        return next((item for item in self.nodes if item.node_id == node_id), None)

    def nearest_existing(
        self,
        old_projection: "OperationManagerProjection",
        node_id: OperationManagerNodeId,
    ) -> OperationManagerNodeId | None:
        current = old_projection.node(node_id)
        available = {item.node_id for item in self.nodes}
        while current is not None:
            if current.node_id in available:
                return current.node_id
            current = (
                old_projection.node(current.parent_id)
                if current.parent_id is not None
                else None
            )
        return self.roots[0] if self.roots else None


def node_matches_filter(
    node: OperationManagerNode, selected_filter: OperationManagerFilter
) -> bool:
    """Return direct filter match; the model keeps matching ancestor context."""
    if selected_filter is OperationManagerFilter.ALL:
        return True
    if selected_filter is OperationManagerFilter.ENABLED:
        return node.enabled and node.kind is not OperationManagerNodeKind.EMPTY_STATE
    if selected_filter is OperationManagerFilter.DISABLED:
        return not node.enabled or any(
            item.semantic is OperationManagerSemanticStatus.DISABLED
            for item in node.statuses
        )
    if selected_filter is OperationManagerFilter.NEEDS_CALCULATION:
        return any(
            item.category is OperationManagerStatusCategory.CALCULATION
            and item.semantic
            in {
                OperationManagerSemanticStatus.NEEDS_INPUT,
                OperationManagerSemanticStatus.DRAFT,
                OperationManagerSemanticStatus.STALE,
            }
            for item in node.statuses
        )
    if selected_filter is OperationManagerFilter.STALE:
        return node.is_stale
    if selected_filter is OperationManagerFilter.WARNINGS:
        return any(
            item.semantic is OperationManagerSemanticStatus.WARNING
            for item in node.statuses
        )
    return any(
        item.semantic
        in {
            OperationManagerSemanticStatus.BLOCKED,
            OperationManagerSemanticStatus.FAILED,
        }
        for item in node.statuses
    )


def count_operation_nodes(nodes: Iterable[OperationManagerNode]) -> int:
    return sum(item.kind is OperationManagerNodeKind.OPERATION for item in nodes)
