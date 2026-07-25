"""Qt item model over the native-free Operation Manager projection."""

from __future__ import annotations

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt

from hms_cadcam.ui.operation_manager_types import (
    OperationManagerEntityKind,
    OperationManagerFilter,
    OperationManagerNode,
    OperationManagerNodeId,
    OperationManagerProjection,
    node_matches_filter,
)
from hms_cadcam.ui.localization import (
    operation_manager_status_category_display_name,
    translate_status,
    ui_text,
)


NODE_ROLE = int(Qt.ItemDataRole.UserRole) + 101
NODE_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 102
DOMAIN_IDENTITY_ROLE = int(Qt.ItemDataRole.UserRole) + 103
STATUS_ROLE = int(Qt.ItemDataRole.UserRole) + 104


class OperationManagerModel(QAbstractItemModel):
    """Read-only tree model with ancestor-preserving search and status filters."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._projection = OperationManagerProjectionBuilderEmpty.build()
        self._nodes: dict[OperationManagerNodeId, OperationManagerNode] = {}
        self._query = ""
        self._filter = OperationManagerFilter.ALL
        self._visible_children: dict[
            OperationManagerNodeId | None, tuple[OperationManagerNodeId, ...]
        ] = {None: ()}

    @property
    def projection(self) -> OperationManagerProjection:
        return self._projection

    @property
    def status_filter(self) -> OperationManagerFilter:
        return self._filter

    @property
    def query(self) -> str:
        return self._query

    def set_projection(self, projection: OperationManagerProjection) -> None:
        """Atomically replace the immutable source projection."""
        if not isinstance(projection, OperationManagerProjection):
            raise TypeError("Operation Manager projection is invalid")
        self.beginResetModel()
        self._projection = projection
        self._nodes = {node.node_id: node for node in projection.nodes}
        self._rebuild_visibility()
        self.endResetModel()

    def set_query(self, text: str) -> None:
        normalized = " ".join(text.strip().casefold().split())
        if normalized == self._query:
            return
        self.beginResetModel()
        self._query = normalized
        self._rebuild_visibility()
        self.endResetModel()

    def set_status_filter(self, value: OperationManagerFilter) -> None:
        if not isinstance(value, OperationManagerFilter):
            raise TypeError("Operation Manager filter is invalid")
        if value is self._filter:
            return
        self.beginResetModel()
        self._filter = value
        self._rebuild_visibility()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid() and parent.column() != 0:
            return 0
        parent_id = self._node_id(parent) if parent.isValid() else None
        return len(self._visible_children.get(parent_id, ()))

    def columnCount(self, _parent: QModelIndex = QModelIndex()) -> int:
        return 2

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex = QModelIndex(),
    ) -> QModelIndex:
        if row < 0 or column < 0 or column >= 2:
            return QModelIndex()
        parent_id = self._node_id(parent) if parent.isValid() else None
        children = self._visible_children.get(parent_id, ())
        if row >= len(children):
            return QModelIndex()
        node = self._nodes.get(children[row])
        return self.createIndex(row, column, node) if node is not None else QModelIndex()

    def parent(self, child: QModelIndex) -> QModelIndex:
        node = self.node_for_index(child)
        if node is None or node.parent_id is None:
            return QModelIndex()
        parent_node = self._nodes.get(node.parent_id)
        if parent_node is None:
            return QModelIndex()
        grandparent_id = parent_node.parent_id
        siblings = self._visible_children.get(grandparent_id, ())
        try:
            row = siblings.index(parent_node.node_id)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, 0, parent_node)

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        node = self.node_for_index(index)
        if node is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                ui_text(node.label)
                if index.column() == 0
                else translate_status(node.status.text)
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{ui_text(node.label)}\n{ui_text(node.secondary_summary)}\n"
                f"{_status_details(node)}"
            )
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return (
                f"{ui_text(node.label)}. {ui_text(node.secondary_summary)}. "
                f"{ui_text('Status')} {translate_status(node.status.text)}."
            )
        if role == Qt.ItemDataRole.AccessibleDescriptionRole:
            return _status_details(node)
        if role == NODE_ROLE:
            return node
        if role == NODE_ID_ROLE:
            return node.node_id
        if role == DOMAIN_IDENTITY_ROLE:
            return node.domain_identity
        if role == STATUS_ROLE:
            return node.status
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ui_text("Name") if section == 0 else ui_text("Status")
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def node_for_index(self, index: QModelIndex) -> OperationManagerNode | None:
        if not index.isValid():
            return None
        pointer = index.internalPointer()
        return pointer if isinstance(pointer, OperationManagerNode) else None

    def index_for_node_id(
        self, node_id: OperationManagerNodeId, column: int = 0
    ) -> QModelIndex:
        node = self._nodes.get(node_id)
        if node is None:
            return QModelIndex()
        siblings = self._visible_children.get(node.parent_id, ())
        try:
            row = siblings.index(node_id)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, column, node)

    def first_index(self) -> QModelIndex:
        return self.index(0, 0)

    def visible_node_count(self) -> int:
        return sum(len(values) for values in self._visible_children.values())

    def _retranslate(self, _language: object = None) -> None:
        """Notify views that presentation roles changed without resetting state."""
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 1)

        def emit_parent(parent: QModelIndex = QModelIndex()) -> None:
            rows = self.rowCount(parent)
            if rows <= 0:
                return
            self.dataChanged.emit(
                self.index(0, 0, parent),
                self.index(rows - 1, 1, parent),
                [
                    int(Qt.ItemDataRole.DisplayRole),
                    int(Qt.ItemDataRole.ToolTipRole),
                    int(Qt.ItemDataRole.AccessibleTextRole),
                    int(Qt.ItemDataRole.AccessibleDescriptionRole),
                ],
            )
            for row in range(rows):
                emit_parent(self.index(row, 0, parent))

        emit_parent()

    def _node_id(self, index: QModelIndex) -> OperationManagerNodeId | None:
        node = self.node_for_index(index)
        return node.node_id if node is not None else None

    def _rebuild_visibility(self) -> None:
        visible: dict[
            OperationManagerNodeId | None, list[OperationManagerNodeId]
        ] = {}

        def include(node_id: OperationManagerNodeId, ancestor_match: bool = False) -> bool:
            node = self._nodes[node_id]
            direct = self._direct_match(node)
            child_visible = False
            for child_id in node.children:
                if include(child_id, ancestor_match or direct):
                    visible.setdefault(node.node_id, []).append(child_id)
                    child_visible = True
            return ancestor_match or direct or child_visible

        for root_id in self._projection.roots:
            if root_id in self._nodes and include(root_id):
                visible.setdefault(None, []).append(root_id)
        self._visible_children = {
            parent_id: tuple(children) for parent_id, children in visible.items()
        }
        self._visible_children.setdefault(None, ())

    def _direct_match(self, node: OperationManagerNode) -> bool:
        query_match = not self._query or all(
            token in node.searchable_text for token in self._query.split()
        )
        return query_match and node_matches_filter(node, self._filter)


def _status_details(node: OperationManagerNode) -> str:
    """Return localized multi-line detail for tooltip and accessibility."""
    project_context = (
        node.domain_identity.kind is OperationManagerEntityKind.PROJECT
    )
    lines: list[str] = []
    for item in node.statuses:
        namespace = operation_manager_status_category_display_name(
            item.category,
            project_context=project_context,
        )
        lines.append(
            f"{namespace} · {translate_status(item.text)}: {ui_text(item.tooltip)}"
        )
    return "\n".join(lines)


class OperationManagerProjectionBuilderEmpty:
    """Avoid a mutable or optional projection inside the Qt model."""

    @staticmethod
    def build() -> OperationManagerProjection:
        from hms_cadcam.ui.operation_manager_types import (
            OperationManagerHeader,
        )

        return OperationManagerProjection(
            None,
            (),
            (),
            OperationManagerHeader("", "", "", "", 0, 0, 0),
        )
