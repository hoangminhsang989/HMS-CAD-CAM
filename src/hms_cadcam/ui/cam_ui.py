"""CAM 7B.1 tree, command area and conservative properties editor."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable
from uuid import UUID

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSplitter, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from hms_cadcam.cam.domain import (
    ArtifactStatus, BoxStock, CamJobId, CamNodeId, ContentFingerprint,
    DirtyReason, FacingBoundarySource, FacingCutDirection, FacingParameters,
    FeedRate, FeedUnit,
    GeometryFingerprint, GeometryInputId, GeometryInputRole,
    GeometryReference, GeometryReferenceId,
    GeometryReferenceKind, GeometryRepresentationKind, GeometryResolutionStatus,
    Length, LengthUnit,
    MachineRequirement, Operation, OperationCapability, OperationFamily,
    OperationGeometryInput,
    OperationId, OperationParameterSet, Point3,
    ResolvedMachiningGeometry, Revision, Setup, SetupId, SetupKind, SourceScope, StockKind,
    SpindleSpeed, ToolAssemblyId, ToolAssemblyReference, Vector3, WcsFrame, WorkOffset,
    HMS_GEOMETRY_REFERENCE_SCHEME, HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.project.models import ProjectSession, UnitSystem
from hms_cadcam.project.service import ProjectService
from hms_cadcam.cam.application import basic_mill_resources

_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 20
_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 21
_JOB_ROLE = int(Qt.ItemDataRole.UserRole) + 22
_SETUP_ROLE = int(Qt.ItemDataRole.UserRole) + 23


class CamWorkspace(QWidget):
    """One self-contained CAM management surface; CAD viewport remains central."""

    message = Signal(str)

    def __init__(self, service: ProjectService,
                 source_provider: Callable[[], UUID | None],
                 pick_provider: Callable[[], GeometryReference] | None = None,
                 toolpath_display: Callable[[object], object] | None = None,
                 toolpath_clear: Callable[[], None] | None = None,
                 parent: QWidget | None = None,
                 face_resolver: Callable[[GeometryReference], ResolvedMachiningGeometry] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CamWorkspace")
        self._service = service
        self._source_provider = source_provider
        self._pick_provider = pick_provider
        self._toolpath_display = toolpath_display
        self._toolpath_clear = toolpath_clear
        self._face_resolver = face_resolver
        self._picked_reference: GeometryReference | None = None
        self._picked_reference_resolved = False
        self._generation: int | None = None
        self._guard = False
        self._selected_key: tuple[str, str] | None = None
        self.tree = QTreeWidget()
        self.tree.setObjectName("CamOperationTree")
        self.tree.setHeaderLabels(["CAM Project / Operation", "Trạng thái"])
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemChanged.connect(self._item_changed)
        self.editor = _CamPropertiesEditor(self._apply_properties)
        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(self.editor)
        splitter.setSizes([360, 340])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QToolBar("Lệnh CAM")
        layout.addWidget(self.toolbar)
        layout.addWidget(splitter)
        self.actions = self._actions()
        self.editor.draft_changed.connect(self._update_generate_action)
        for key in ("job", "setup", "resources", "group", "operation", "generate", "visibility",
                    "pick", "clear_pick", "up", "down", "delete"):
            self.toolbar.addAction(self.actions[key])
        self.bind_project(service.current_project)

    def _actions(self) -> dict[str, QAction]:
        definitions = {
            "job": ("Tạo Job", self.create_job), "setup": ("Tạo Setup", self.create_setup),
            "resources": ("Tạo Tool/Machine cơ bản", self.create_basic_resources),
            "group": ("Thêm Group", self.add_group),
            "operation": ("Thêm Facing 2.5D", self.add_operation),
            "generate": ("Generate/Recompute", self.generate_selected),
            "visibility": ("Hiện/ẩn toolpath", self.toggle_toolpath_visibility),
            "pick": ("Bind/Rebind FACE", self.pick_geometry),
            "clear_pick": ("Clear FACE", self.clear_geometry_pick),
            "up": ("Lên", lambda: self.move_selected(-1)),
            "down": ("Xuống", lambda: self.move_selected(1)),
            "delete": ("Xóa", self.delete_selected),
        }
        result = {}
        for key, (label, callback) in definitions.items():
            action = QAction(label, self)
            action.setObjectName(f"Cam{key.title()}Action")
            action.triggered.connect(callback)
            result[key] = action
        return result

    def bind_project(self, session: object) -> None:
        """Clear old identities before rendering a new project snapshot."""
        self._selected_key = None
        self._picked_reference = None
        self._picked_reference_resolved = False
        self.editor.clear()
        if self._toolpath_clear is not None:
            self._toolpath_clear()
        if not isinstance(session, ProjectSession):
            self._generation = None
            self._render(None)
            return
        self._generation = self._service.cam_generation
        self._render(None)

    def refresh(self, preserve: tuple[str, str] | None = None) -> None:
        if self._generation is None or self._generation != self._service.cam_generation:
            return
        self._render(preserve or self._selected_key)

    def _render(self, preserve: tuple[str, str] | None) -> None:
        self._guard = True
        self.tree.clear()
        matches: list[QTreeWidgetItem] = []
        if not self._service.has_project:
            item = QTreeWidgetItem(["Chưa mở dự án", "—"])
            item.setDisabled(True)
            self.tree.addTopLevelItem(item)
        else:
            snapshot = self._service.cam_snapshot
            if not snapshot.jobs:
                item = QTreeWidgetItem(["Chưa có CAM Job", "MISSING"])
                item.setDisabled(True)
                self.tree.addTopLevelItem(item)
            for job in snapshot.jobs:
                job_item = self._item(job.name, "job", str(job.job_id), job.job_id, None,
                                      "ACTIVE" if job.job_id == snapshot.active_job_id else "")
                self.tree.addTopLevelItem(job_item)
                for setup in job.setups:
                    setup_item = self._item(setup.name, "setup", str(setup.setup_id), job.job_id,
                                            setup.setup_id, "ACTIVE" if setup.setup_id == job.active_setup_id else "")
                    job_item.addChild(setup_item)
                    setup_item.addChild(self._item(f"Stock ({setup.stock.kind.value})", "stock", "stock", job.job_id, setup.setup_id, ""))
                    fixtures = self._item("Fixtures", "fixtures", "fixtures", job.job_id, setup.setup_id, str(len(setup.fixtures)))
                    setup_item.addChild(fixtures)
                    for fixture in setup.fixtures:
                        fixtures.addChild(self._item(fixture.name, "fixture", str(fixture.fixture_id), job.job_id, setup.setup_id,
                                                    "DISABLED" if not fixture.enabled else ""))
                    self._append_nodes(setup_item, setup.operation_tree, setup.operation_tree.root_id, job.job_id, setup.setup_id)
                job_item.setExpanded(True)
            if preserve:
                iterator = QTreeWidgetItemIterator(self.tree)
                while iterator.value():
                    item = iterator.value()
                    if (item.data(0, _KIND_ROLE), item.data(0, _ID_ROLE)) == preserve:
                        matches.append(item)
                    iterator += 1
        if matches:
            self.tree.setCurrentItem(matches[0])
        self._guard = False
        if matches:
            self._selected_key = preserve
            self._show_properties(matches[0])
        else:
            self._selected_key = None
            self.editor.clear()

    def _append_nodes(self, parent: QTreeWidgetItem, tree, parent_id, job_id, setup_id) -> None:
        node = tree.get_node(parent_id)
        for child_id in node.child_ids:
            child = tree.get_node(child_id)
            operation = (
                tree.get_operation(child.operation_id)
                if child.operation_id is not None else None
            )
            status = ""
            if operation is not None:
                status = operation.artifact_state.status.value.upper()
                if not operation.enabled:
                    status = f"DISABLED · {status}"
                snapshot = self._service.cam_snapshot
                assembly = next((value for value in snapshot.tool_assemblies
                                 if value.assembly_id == operation.tool_assembly.assembly_id), None)
                tool_status = operation.tool_assembly.assess(assembly)
                if tool_status.value != "valid":
                    status += f" · TOOL {tool_status.value.upper()}"
                if operation.machine_requirement is not None:
                    machine = next((value for value in snapshot.machine_definitions
                                    if value.machine_id == operation.machine_requirement.machine_id), None)
                    if machine is None:
                        status += " · MACHINE MISSING"
                if operation.geometry_inputs:
                    status += " · GEOMETRY UNRESOLVED"
            item = self._item(child.name, child.kind.value, str(child.node_id), job_id, setup_id, status)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            parent.addChild(item)
            if operation is None:
                self._append_nodes(item, tree, child.node_id, job_id, setup_id)

    @staticmethod
    def _item(label: str, kind: str, identity: str, job_id, setup_id, status: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label, status])
        item.setData(0, _KIND_ROLE, kind)
        item.setData(0, _ID_ROLE, identity)
        item.setData(0, _JOB_ROLE, str(job_id) if job_id else None)
        item.setData(0, _SETUP_ROLE, str(setup_id) if setup_id else None)
        return item

    def _selection_changed(self) -> None:
        if self._guard:
            return
        item = self.tree.currentItem()
        if item is None:
            self._selected_key = None
            self.editor.clear()
            return
        self._selected_key = (item.data(0, _KIND_ROLE), item.data(0, _ID_ROLE))
        self._show_properties(item)

    def _show_properties(self, item: QTreeWidgetItem) -> None:
        kind = item.data(0, _KIND_ROLE)
        job = self._find_job(item)
        setup = self._find_setup(item, job)
        if kind == "job" and job:
            self.editor.show_job(job.name)
        elif kind in {"setup", "stock"} and setup:
            self.editor.show_setup(setup)
        elif kind in {"group", "operation"} and setup:
            node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE)))
            operation = (
                setup.operation_tree.get_operation(node.operation_id)
                if node.operation_id is not None else None
            )
            if operation is None:
                self._picked_reference = None
                self.editor.show_node(node.name, None)
            else:
                self._picked_reference = (
                    operation.geometry_inputs[0].reference
                    if len(operation.geometry_inputs) == 1 else None
                )
                self._picked_reference_resolved = self._resolve_picked_reference()
                self.editor.show_node(node.name, operation, self._service.cam_snapshot.tool_assemblies,
                                      self._service.cam_snapshot.machine_definitions)
                self.editor.show_reference(self._picked_reference)
                artifact = (self._service.load_toolpath_artifact(operation.operation_id)
                            if operation.artifact_state.status is ArtifactStatus.VALID else None)
                if artifact is not None and self._toolpath_display is not None:
                    self._toolpath_display(artifact)
                elif self._toolpath_clear is not None:
                    self._toolpath_clear()
        else:
            self._picked_reference = None
            self.editor.clear()
        self._update_generate_action()

    def create_job(self) -> None:
        if not self._service.has_project:
            return
        count = len(self._service.cam_snapshot.jobs) + 1
        snapshot = self._execute(lambda app: app.create_job(f"CAM Job {count}"))
        if snapshot:
            self.refresh(("job", str(snapshot.active_job_id)))

    def create_setup(self) -> None:
        snapshot = self._service.cam_snapshot if self._service.has_project else None
        source_id = self._source_provider()
        if snapshot is None or snapshot.active_job_id is None or source_id is None:
            self._error("Cần một CAM Job và một nguồn CAD trong dự án trước khi tạo Setup.")
            return
        unit = _length_unit(self._service.current_project)
        setup = _default_setup(source_id, unit, len(_active_job(snapshot).setups) + 1)
        changed = self._execute(lambda app: app.add_setup(snapshot.active_job_id, setup))
        if changed:
            self.refresh(("setup", str(setup.setup_id)))

    def add_group(self) -> None:
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, tree, parent_id = context
        node_id = CamNodeId.new()
        changed = self._execute(lambda app: app.update_tree(job_id, setup_id,
            lambda value: value.add_group(parent_id, node_id, "Nhóm mới")))
        if changed:
            self.refresh(("group", str(node_id)))

    def create_basic_resources(self) -> None:
        """Create the minimal project-owned resource bundle without a global library."""
        if not self._service.has_project:
            return
        values = basic_mill_resources(_length_unit(self._service.current_project))
        changed = self._execute(lambda app: app.add_basic_resources(*values))
        if changed is not None:
            self.refresh(self._selected_key)

    def pick_geometry(self) -> None:
        """Explicitly bind the current unambiguous CAD selection."""
        if self._pick_provider is None:
            self._error("Geometry picking adapter chưa sẵn sàng.")
            return
        generation = self._generation
        try:
            reference = self._pick_provider()
        except Exception as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("Phiên chọn hình học đã bị hủy vì dự án đã thay đổi.")
            return
        previous = self._picked_reference
        previous_status = self._picked_reference_resolved
        self._picked_reference = reference
        self._picked_reference_resolved = self._resolve_picked_reference()
        if not self._picked_reference_resolved:
            self._picked_reference = previous
            self._picked_reference_resolved = previous_status
            self._error("Persistent FACE could not be resolved unambiguously.")
            return
        self.editor.show_reference(self._picked_reference)
        self.message.emit("Đã tạo GeometryReference; dùng Rebind để thay thế rõ ràng.")

    def clear_geometry_pick(self) -> None:
        """Clear an explicit binding; never choose a replacement automatically."""
        self._picked_reference = None
        self._picked_reference_resolved = False
        self.editor.show_reference(None)

    def cad_context_changed(self, *, force_invalidate: bool = False) -> None:
        """Re-resolve displayed references after CAD reload without rebinding them."""
        self._picked_reference_resolved = self._resolve_picked_reference()
        if not self._service.has_project:
            self._update_generate_action()
            return
        if self._face_resolver is not None and self._generation is not None:
            for job in self._service.cam_snapshot.jobs:
                for setup in job.setups:
                    for operation in setup.operation_tree.operations:
                        if (operation.artifact_state.status is not ArtifactStatus.VALID or
                                len(operation.geometry_inputs) != 1):
                            continue
                        try:
                            parameters = FacingParameters.from_operation_parameters(operation.parameters)
                        except (RuntimeError, TypeError, ValueError):
                            continue
                        if parameters.boundary_source is not FacingBoundarySource.PLANAR_FACE:
                            continue
                        if force_invalidate:
                            self._execute(lambda app, operation_id=operation.operation_id:
                                app.invalidate_operation(operation_id, DirtyReason.GEOMETRY_CHANGED))
                            continue
                        try:
                            result = self._face_resolver(operation.geometry_inputs[0].reference)
                        except (RuntimeError, TypeError, ValueError):
                            self._execute(lambda app, operation_id=operation.operation_id:
                                app.invalidate_operation(operation_id, DirtyReason.GEOMETRY_CHANGED))
                            continue
                        if getattr(result, "status", None) is not GeometryResolutionStatus.RESOLVED:
                            self._execute(lambda app, operation_id=operation.operation_id:
                                app.invalidate_operation(operation_id, DirtyReason.GEOMETRY_CHANGED))
        self._update_generate_action()

    def _resolve_picked_reference(self) -> bool:
        if self._picked_reference is None:
            return False
        if self._face_resolver is None:
            return True
        try:
            result = self._face_resolver(self._picked_reference)
        except (RuntimeError, TypeError, ValueError):
            return False
        return getattr(result, "status", None) is GeometryResolutionStatus.RESOLVED

    def add_operation(self) -> None:
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, _tree, parent_id = context
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        snapshot = self._service.cam_snapshot
        assembly = snapshot.tool_assemblies[0] if snapshot.tool_assemblies else None
        tool_reference = ToolAssemblyReference.from_assembly(assembly) if assembly else ToolAssemblyReference(
            ToolAssemblyId.new(), Revision(0), ContentFingerprint.from_payload({"missing": True}), _length_unit(self._service.current_project))
        machine = snapshot.machine_definitions[0] if snapshot.machine_definitions else None
        machine_requirement = None if machine is None else MachineRequirement(
            machine.machine_id, machine.revision, ContentFingerprint.from_payload(machine.to_dict()),
            machine.unit, (OperationCapability.MILLING,))
        setup = next(value for job in snapshot.jobs for value in job.setups if value.setup_id == setup_id)
        parameters = _default_facing_parameters(setup)
        operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup_id,
                              tool_reference, (), parameters.to_operation_parameters(), machine_requirement)
        changed = self._execute(lambda app: app.update_tree(job_id, setup_id,
            lambda value: value.add_operation(parent_id, "Facing 2.5D", operation)))
        if changed:
            self.refresh(("operation", str(node_id)))

    def generate_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.data(0, _KIND_ROLE) != "operation" or self._generation is None:
            self._error("Hãy chọn một operation Facing trước khi Generate.")
            return
        setup = self._find_setup(item, self._find_job(item))
        node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE))) if setup else None
        operation = setup.operation_tree.get_operation(node.operation_id) if setup and node else None
        if operation is None or operation.strategy_key != "facing_2_5d":
            self._error("Operation đã chọn không phải Facing 2.5D.")
            return
        draft = self.editor.facing_draft(setup.wcs.origin.unit)
        machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
        if (draft is None or draft.to_operation_parameters() != operation.parameters or
                self.editor.tool.currentData() != str(operation.tool_assembly.assembly_id) or
                self.editor.machine.currentData() != (str(machine_id) if machine_id else None) or
                not operation.enabled):
            self._error("Draft Facing chưa hợp lệ hoặc chưa được Áp dụng.")
            return
        result = self._service.compute_facing(
            operation.operation_id,
            expected_generation=self._generation,
            face_resolver=self._face_resolver,
        )
        if result.accepted and result.artifact is not None:
            if self._toolpath_display is not None:
                self._toolpath_display(result.artifact)
            self.message.emit("Facing 2.5D đã Generate và publish artifact hợp lệ.")
            self.editor.set_error("")
        else:
            self._error(result.diagnostics[0].message if result.diagnostics else "Facing generation thất bại.")
        self.refresh(self._selected_key)

    def toggle_toolpath_visibility(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.data(0, _KIND_ROLE) != "operation":
            return
        setup = self._find_setup(item, self._find_job(item))
        node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE))) if setup else None
        operation = setup.operation_tree.get_operation(node.operation_id) if setup and node else None
        if operation is not None and hasattr(self._toolpath_display, "__self__"):
            viewport = self._toolpath_display.__self__
            visible = not bool(getattr(self, "_toolpath_visible", True))
            viewport.set_toolpath_visibility(operation.operation_id, visible)
            self._toolpath_visible = visible

    def move_selected(self, delta: int) -> None:
        item = self.tree.currentItem()
        context = self._tree_context()
        if item is None or context is None or item.data(0, _KIND_ROLE) not in {"group", "operation"}:
            return
        job_id, setup_id, tree, _ = context
        node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
        node = tree.get_node(node_id)
        siblings = tree.get_node(node.parent_id).child_ids
        new_index = max(0, min(len(siblings) - 1, siblings.index(node_id) + delta))
        changed = self._execute(lambda app: app.update_tree(job_id, setup_id,
            lambda value: value.reorder_node(node_id, new_index)))
        if changed is not None:
            self.refresh((item.data(0, _KIND_ROLE), str(node_id)))

    def delete_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        kind = item.data(0, _KIND_ROLE)
        if kind not in {"job", "setup", "group", "operation"}:
            return
        if QMessageBox.question(self, "Xác nhận xóa", f"Xóa '{item.text(0)}'?") != QMessageBox.StandardButton.Yes:
            return
        job_id = CamJobId.parse(item.data(0, _JOB_ROLE))
        if kind == "job":
            changed = self._execute(lambda app: app.delete_job(job_id))
        elif kind == "setup":
            changed = self._execute(lambda app: app.delete_setup(job_id, SetupId.parse(item.data(0, _SETUP_ROLE))))
        else:
            setup_id = SetupId.parse(item.data(0, _SETUP_ROLE))
            node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
            changed = self._execute(lambda app: app.update_tree(job_id, setup_id, lambda tree: tree.remove_node(node_id)))
        if changed is not None:
            if self._toolpath_clear is not None:
                self._toolpath_clear()
            self.refresh()

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._guard or column != 0:
            return
        kind = item.data(0, _KIND_ROLE)
        old_key = (kind, item.data(0, _ID_ROLE))
        job_id = CamJobId.parse(item.data(0, _JOB_ROLE))
        changed = None
        if kind == "job":
            changed = self._execute(lambda app: app.rename_job(job_id, item.text(0)))
        elif kind == "setup":
            changed = self._execute(lambda app: app.rename_setup(job_id, SetupId.parse(item.data(0, _SETUP_ROLE)), item.text(0)))
        elif kind in {"group", "operation"}:
            setup_id, node_id = SetupId.parse(item.data(0, _SETUP_ROLE)), CamNodeId.parse(item.data(0, _ID_ROLE))
            changed = self._execute(lambda app: app.update_tree(job_id, setup_id, lambda tree: tree.rename_node(node_id, item.text(0))))
        if changed is not None:
            self.refresh(old_key)

    def _apply_properties(self, values: dict[str, object]) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        job = self._find_job(item)
        setup = self._find_setup(item, job)
        kind = item.data(0, _KIND_ROLE)
        try:
            if kind == "job" and job:
                result = self._execute(lambda app: app.rename_job(job.job_id, str(values["name"])))
            elif kind in {"setup", "stock"} and job and setup:
                unit = setup.wcs.origin.unit
                origin = Point3(float(values["x"]), float(values["y"]), float(values["z"]), unit)
                wcs = WcsFrame(origin, setup.wcs.x_axis, setup.wcs.y_axis, setup.wcs.z_axis)
                stock_kind = StockKind(str(values["stock_kind"]))
                if stock_kind is StockKind.BOX:
                    stock = BoxStock(Length(float(values["a"]), unit), Length(float(values["b"]), unit), Length(float(values["c"]), unit), wcs)
                elif stock_kind is StockKind.CYLINDER:
                    from hms_cadcam.cam.domain import CylinderStock
                    stock = CylinderStock(Length(float(values["a"]), unit), Length(float(values["b"]), unit), wcs)
                else:
                    raise ValueError(f"Stock {stock_kind.value} chưa được hỗ trợ trong editor 7B.1")
                changed = replace(setup, name=str(values["name"]), kind=SetupKind(str(values["setup_kind"])),
                                  wcs=wcs, work_offset=WorkOffset(str(values["offset"])), stock=stock)
                result = self._execute(lambda app: app.replace_setup(job.job_id, changed))
            elif kind in {"group", "operation"} and job and setup:
                node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
                node = setup.operation_tree.get_node(node_id)
                current = setup.operation_tree.get_operation(node.operation_id) if node.operation_id else None
                if current is not None and current.strategy_key == "facing_2_5d":
                    unit = setup.wcs.origin.unit
                    feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
                    parameters = FacingParameters(unit, FacingBoundarySource(str(values["boundary_source"])),
                        Length(float(values["top"]), unit), Length(float(values["target"]), unit),
                        Length(float(values["stepdown"]), unit), Length(float(values["stepover"]), unit),
                        Length(float(values["allowance"]), unit), Length(float(values["clearance"]), unit),
                        Length(float(values["retract"]), unit), FeedRate(float(values["feed"]), feed_unit),
                        FeedRate(float(values["plunge"]), feed_unit), SpindleSpeed(float(values["spindle"])),
                        FacingCutDirection(str(values["direction"])), float(values["angle"]),
                        Length(float(values["overtravel"]), unit))
                    snapshot = self._service.cam_snapshot
                    assembly = next(value for value in snapshot.tool_assemblies
                                    if str(value.assembly_id) == values["tool_id"])
                    machine = next(value for value in snapshot.machine_definitions
                                   if str(value.machine_id) == values["machine_id"])
                    requirement = MachineRequirement(machine.machine_id, machine.revision,
                        machine.content_fingerprint, machine.unit, (OperationCapability.MILLING,))
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    geometry_inputs = ()
                    if (parameters.boundary_source is FacingBoundarySource.PLANAR_FACE and
                            self._picked_reference is not None):
                        existing = current.geometry_inputs[0] if len(current.geometry_inputs) == 1 else None
                        input_id = (existing.input_id if existing is not None and
                                    existing.reference.reference_id == self._picked_reference.reference_id
                                    else GeometryInputId.new())
                        geometry_inputs = (OperationGeometryInput(
                            input_id, GeometryInputRole.BOUNDARY, self._picked_reference,
                            True, GeometryReferenceKind.FACE, 0,
                        ),)
                    enabled = bool(values["enabled"])
                    inputs_changed = (parameter_set != current.parameters or
                                      tool_reference != current.tool_assembly or
                                      requirement != current.machine_requirement or
                                      geometry_inputs != current.geometry_inputs)
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if inputs_changed or enabled_changed:
                        reason = (DirtyReason.PARAMETERS_CHANGED if inputs_changed
                                  else DirtyReason.UPSTREAM_CHANGED)
                        changed_operation = replace(current, parameters=parameter_set,
                            tool_assembly=tool_reference, machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled, revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason))
                    tree_mutation = lambda tree: tree.rename_node(node_id, str(values["name"])).replace_operation(changed_operation)
                else:
                    tree_mutation = lambda tree: tree.rename_node(node_id, str(values["name"])).set_enabled(node_id, bool(values["enabled"]))
                result = self._execute(lambda app: app.update_tree(job.job_id, setup.setup_id, tree_mutation))
            else:
                result = None
            if result is not None:
                self.editor.set_error("")
                self.refresh(self._selected_key)
        except (TypeError, ValueError) as error:
            self.editor.set_error(str(error))

    def _tree_context(self):
        item = self.tree.currentItem()
        snapshot = self._service.cam_snapshot if self._service.has_project else None
        if snapshot is None or snapshot.active_job_id is None:
            return None
        job = self._find_job(item) if item else _active_job(snapshot)
        if job is None or job.active_setup is None:
            self._error("Hãy chọn hoặc tạo Setup trước.")
            return None
        setup = self._find_setup(item, job) if item else job.active_setup
        setup = setup or job.active_setup
        parent_id = setup.operation_tree.root_id
        if item and item.data(0, _KIND_ROLE) == "group":
            parent_id = CamNodeId.parse(item.data(0, _ID_ROLE))
        return job.job_id, setup.setup_id, setup.operation_tree, parent_id

    def _find_job(self, item):
        if item is None or not self._service.has_project:
            return None
        value = item.data(0, _JOB_ROLE)
        return next((job for job in self._service.cam_snapshot.jobs if str(job.job_id) == value), None)

    @staticmethod
    def _find_setup(item, job):
        if item is None or job is None:
            return None
        value = item.data(0, _SETUP_ROLE)
        return next((setup for setup in job.setups if str(setup.setup_id) == value), None)

    def _execute(self, command):
        if self._generation is None:
            return None
        try:
            result = self._service.execute_cam_command(
                command,
                expected_generation=self._generation,
            )
            self.message.emit("Đã cập nhật CAM; dự án có thay đổi chưa lưu.")
            return result
        except Exception as error:
            self.refresh(self._selected_key)
            self._error(str(error))
            return None

    def _error(self, text: str) -> None:
        self.editor.set_error(text)
        self.message.emit(f"CAM: {text}")

    def _update_generate_action(self) -> None:
        action = self.actions.get("generate") if hasattr(self, "actions") else None
        item = self.tree.currentItem()
        setup = self._find_setup(item, self._find_job(item)) if item is not None else None
        valid = bool(item is not None and item.data(0, _KIND_ROLE) == "operation" and
                     setup is not None and
                     self._draft_matches_selected_operation(item, setup))
        if action is not None:
            action.setEnabled(valid)

    def _draft_matches_selected_operation(self, item: QTreeWidgetItem, setup: Setup) -> bool:
        try:
            node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE)))
            operation = setup.operation_tree.get_operation(node.operation_id)
            draft = self.editor.facing_draft(setup.wcs.origin.unit)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            return bool(operation.enabled and draft is not None and
                draft.to_operation_parameters() == operation.parameters and
                (draft.boundary_source is FacingBoundarySource.STOCK_BOX or
                 (len(operation.geometry_inputs) == 1 and
                  self._picked_reference == operation.geometry_inputs[0].reference and
                  self._picked_reference_resolved)) and
                self.editor.tool.currentData() == str(operation.tool_assembly.assembly_id) and
                self.editor.machine.currentData() == (str(machine_id) if machine_id else None))
        except (TypeError, ValueError):
            return False


class _CamPropertiesEditor(QWidget):
    draft_changed = Signal()

    def __init__(self, commit: Callable[[dict[str, object]], None]) -> None:
        super().__init__()
        self._commit = commit
        self._fields = {key: QLineEdit() for key in ("name", "offset", "x", "y", "z", "a", "b", "c")}
        self._facing_fields = {key: QLineEdit() for key in (
            "top", "target", "stepdown", "stepover", "allowance", "clearance", "retract",
            "feed", "plunge", "spindle", "angle", "overtravel")}
        self.boundary_source = QComboBox(); self.boundary_source.addItems([item.value for item in FacingBoundarySource])
        self.direction = QComboBox(); self.direction.addItems([item.value for item in FacingCutDirection])
        self.tool = QComboBox(); self.machine = QComboBox()
        self.setup_kind = QComboBox(); self.setup_kind.addItems([item.value for item in SetupKind])
        self.stock_kind = QComboBox(); self.stock_kind.addItems([item.value for item in StockKind])
        self.enabled = QCheckBox("Được bật")
        self.status = QLabel("—")
        self.error = QLabel(); self.error.setStyleSheet("color: #d9534f")
        form = QFormLayout(self)
        for label, key in (("Tên", "name"), ("Work offset", "offset"), ("WCS X", "x"), ("WCS Y", "y"), ("WCS Z", "z")):
            form.addRow(label, self._fields[key])
        form.addRow("Loại Setup", self.setup_kind); form.addRow("Loại Stock", self.stock_kind)
        form.addRow("Kích thước A", self._fields["a"]); form.addRow("Kích thước B", self._fields["b"]); form.addRow("Kích thước C", self._fields["c"])
        form.addRow("Nguồn Facing", self.boundary_source); form.addRow("Tool Assembly", self.tool); form.addRow("Máy", self.machine)
        for label, key in (("Top Z", "top"), ("Target Z", "target"), ("Stepdown", "stepdown"),
                           ("Stepover", "stepover"), ("Allowance", "allowance"), ("Clearance Z", "clearance"),
                           ("Retract Z", "retract"), ("Feed", "feed"), ("Plunge feed", "plunge"),
                           ("Spindle RPM", "spindle"), ("Raster angle", "angle"), ("Overtravel", "overtravel")):
            form.addRow(label, self._facing_fields[key])
        form.addRow("Hướng cắt", self.direction)
        form.addRow("Trạng thái", self.status); form.addRow("", self.enabled); form.addRow("Lỗi", self.error)
        button = QPushButton("Áp dụng"); button.clicked.connect(self._submit); form.addRow(button)
        for field in self._facing_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for combo in (self.boundary_source, self.direction, self.tool, self.machine):
            combo.currentIndexChanged.connect(lambda _index: self.draft_changed.emit())

    def clear(self) -> None:
        for field in self._fields.values(): field.clear()
        for field in self._facing_fields.values(): field.clear()
        self.status.setText("—"); self.error.clear()

    def show_job(self, name: str) -> None:
        self.clear(); self._fields["name"].setText(name); self.status.setText("Editable")

    def show_setup(self, setup: Setup) -> None:
        self.clear(); self._fields["name"].setText(setup.name); self._fields["offset"].setText(setup.work_offset.name)
        for key, value in zip(("x", "y", "z"), (setup.wcs.origin.x, setup.wcs.origin.y, setup.wcs.origin.z), strict=True): self._fields[key].setText(str(value))
        self.setup_kind.setCurrentText(setup.kind.value); self.stock_kind.setCurrentText(setup.stock.kind.value)
        dimensions = setup.stock.to_dict()
        if setup.stock.kind is StockKind.BOX: values = (dimensions["size_x"]["value"], dimensions["size_y"]["value"], dimensions["size_z"]["value"])
        elif setup.stock.kind is StockKind.CYLINDER: values = (dimensions["diameter"]["value"], dimensions["length"]["value"], "")
        else: values = ("", "", "")
        for key, value in zip(("a", "b", "c"), values, strict=True): self._fields[key].setText(str(value))
        if setup.stock.kind in {StockKind.BOX, StockKind.CYLINDER}:
            self.status.setText("MISSING — chưa có artifact")
        else:
            self.status.setText(f"UNSUPPORTED — stock {setup.stock.kind.value}")

    def show_node(self, name: str, operation: Operation | None, assemblies=(), machines=()) -> None:
        self.clear(); self._fields["name"].setText(name); self.enabled.setChecked(True if operation is None else operation.enabled)
        self.status.setText("GROUP" if operation is None else operation.artifact_state.status.value.upper())
        self.tool.clear(); self.machine.clear()
        for value in assemblies: self.tool.addItem(value.name, str(value.assembly_id))
        for value in machines: self.machine.addItem(value.name, str(value.machine_id))
        self.tool.setCurrentIndex(-1); self.machine.setCurrentIndex(-1)
        if operation is not None and operation.strategy_key == "facing_2_5d":
            parameters = FacingParameters.from_operation_parameters(operation.parameters)
            values = {"top": parameters.top_height.value, "target": parameters.target_height.value,
                      "stepdown": parameters.stepdown.value, "stepover": parameters.stepover.value,
                      "allowance": parameters.stock_allowance.value, "clearance": parameters.clearance_height.value,
                      "retract": parameters.retract_height.value, "feed": parameters.feed_rate.value,
                      "plunge": parameters.plunge_feed_rate.value, "spindle": parameters.spindle_speed.value,
                      "angle": parameters.raster_angle_degrees, "overtravel": parameters.overtravel.value}
            for key, value in values.items(): self._facing_fields[key].setText(str(value))
            self.boundary_source.setCurrentText(parameters.boundary_source.value)
            self.direction.setCurrentText(parameters.direction.value)
            self.tool.setCurrentIndex(self.tool.findData(str(operation.tool_assembly.assembly_id)))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(str(operation.machine_requirement.machine_id)))

    def set_error(self, text: str) -> None: self.error.setText(text)

    def show_reference(self, reference: GeometryReference | None) -> None:
        if reference is None:
            self.status.setText("Geometry: chưa liên kết")
        else:
            self.status.setText(
                f"Geometry: {reference.kind.value} · {reference.hint or reference.reference_id}"
            )

    def _submit(self) -> None:
        self._commit({**{key: field.text() for key, field in self._fields.items()},
                      **{key: field.text() for key, field in self._facing_fields.items()},
                      "setup_kind": self.setup_kind.currentText(), "stock_kind": self.stock_kind.currentText(),
                      "enabled": self.enabled.isChecked(), "boundary_source": self.boundary_source.currentText(),
                      "direction": self.direction.currentText(), "tool_id": self.tool.currentData(),
                      "machine_id": self.machine.currentData()})

    def has_valid_facing_draft(self, unit: LengthUnit) -> bool:
        return self.facing_draft(unit) is not None

    def facing_draft(self, unit: LengthUnit) -> FacingParameters | None:
        try:
            feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            value = FacingParameters(unit, FacingBoundarySource(self.boundary_source.currentText()),
                Length(float(self._facing_fields["top"].text()), unit),
                Length(float(self._facing_fields["target"].text()), unit),
                Length(float(self._facing_fields["stepdown"].text()), unit),
                Length(float(self._facing_fields["stepover"].text()), unit),
                Length(float(self._facing_fields["allowance"].text()), unit),
                Length(float(self._facing_fields["clearance"].text()), unit),
                Length(float(self._facing_fields["retract"].text()), unit),
                FeedRate(float(self._facing_fields["feed"].text()), feed_unit),
                FeedRate(float(self._facing_fields["plunge"].text()), feed_unit),
                SpindleSpeed(float(self._facing_fields["spindle"].text())),
                FacingCutDirection(self.direction.currentText()),
                float(self._facing_fields["angle"].text()),
                Length(float(self._facing_fields["overtravel"].text()), unit))
            return value if self.tool.currentData() is not None and self.machine.currentData() is not None else None
        except (TypeError, ValueError):
            return None


def _length_unit(session: ProjectSession | None) -> LengthUnit:
    return LengthUnit.INCH if session and session.manifest.units is UnitSystem.INCH else LengthUnit.MM


def _default_setup(source_id: UUID, unit: LengthUnit, number: int) -> Setup:
    wcs = WcsFrame.identity(unit)
    reference = GeometryReference(GeometryReferenceId.new(), HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, source_id, GeometryReferenceKind.DOCUMENT,
        GeometryRepresentationKind.BREP, GeometryFingerprint.from_payload({"source_id": str(source_id)}), Revision(0))
    return Setup(SetupId.new(), f"Setup {number}", SetupKind.MILL, wcs, WorkOffset("G54", 1),
                 BoxStock(Length(100, unit), Length(100, unit), Length(50, unit), wcs), reference, SourceScope(source_id))


def _default_facing_parameters(setup: Setup) -> FacingParameters:
    if not isinstance(setup.stock, BoxStock):
        raise ValueError("Facing mặc định yêu cầu Stock BOX.")
    unit = setup.wcs.origin.unit
    feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
    top = setup.stock.size_z.value
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    return FacingParameters(unit, FacingBoundarySource.STOCK_BOX, Length(top, unit),
        Length(top - scale, unit), Length(scale, unit), Length(5 * scale, unit), Length(0, unit),
        Length(top + 5 * scale, unit), Length(top + 2 * scale, unit),
        FeedRate(500 * scale, feed_unit), FeedRate(100 * scale, feed_unit), SpindleSpeed(1000),
        FacingCutDirection.BIDIRECTIONAL, 0.0, Length(scale, unit))


def _active_job(snapshot):
    return next(job for job in snapshot.jobs if job.job_id == snapshot.active_job_id)


from PySide6.QtWidgets import QTreeWidgetItemIterator  # noqa: E402
