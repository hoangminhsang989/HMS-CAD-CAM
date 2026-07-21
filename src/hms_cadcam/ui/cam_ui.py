"""CAM 7B.1 tree, command area and conservative properties editor."""

from __future__ import annotations

import math
import logging
from dataclasses import replace
from typing import Callable
from uuid import UUID

from PySide6.QtCore import QEventLoop, QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QApplication, QMessageBox, QPushButton, QSplitter, QTabWidget, QToolBar,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from hms_cadcam.cam.domain import (
    ArtifactStatus, BoringBarGeometry, BoringCoolantMode,
    BoringRetractPolicy, BoringStrategy, BoringValidationError,
    BoxStock, CamJobId, CamNodeId, ContentFingerprint,
    ContourCutDirection, ContourParameters, ContourProfileSource, ContourSide,
    DiagnosticCode, DirtyReason, DrillApproachPolicy, DrillDepthDefinition,
    DrillGeometryInput,
    DrillRetractPolicy, DrillValidationError, DrillingCycle, DrillingStrategy,
    FacingBoundarySource, FacingCutDirection, FacingParameters,
    FeedRate, FeedUnit,
    GeometryFingerprint, GeometryInputId, GeometryInputRole,
    GeometryReference, GeometryReferenceId,
    GeometryReferenceKind, GeometryRepresentationKind, GeometryResolutionStatus,
    HolePattern, HoleReference,
    Length, LengthUnit,
    MachineKind, MachineRequirement, Operation, OperationCapability,
    OperationFamily,
    OperationGeometryInput,
    OperationId, OperationParameterSet, Point3,
    PocketCuttingDirection, PocketDepthDefinition, PocketEntryPolicy,
    PocketGeometryInput, PocketStrategy,
    ReamingCoolantMode, ReamingRetractPolicy, ReamingStrategy,
    ReamingValidationError,
    ResolvedContourProfile, ResolvedMachiningGeometry, Revision, Setup, SetupId, SetupKind, SourceScope, StockKind,
    ResolvedDrillingGeometry, ResolvedPocketGeometry,
    SpindleDirection, SpindleSpeed, TappingHand, TappingStrategy,
    TappingSynchronizationPolicy, TappingValidationError, ToolAssemblyId,
    ToolAssemblyReference,
    ToolFamily,
    Vector3, WcsFrame, WorkOffset,
    HMS_GEOMETRY_REFERENCE_SCHEME, HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.project.models import ProjectSession, UnitSystem
from hms_cadcam.project.exceptions import ProjectError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.cam.application import (
    BoringGenerationError,
    BoringGenerator,
    ReamingGenerationError,
    ReamingGenerator,
    TappingGenerationError,
    TappingGenerator,
    basic_boring_resources,
    basic_drilling_resources,
    basic_mill_resources,
    basic_reaming_resources,
    basic_tapping_resources,
)
from hms_cadcam.viewer.toolpath import ToolpathPresentation
from hms_cadcam.cam.simulation import (
    CollisionBackend, CollisionScene, SimulationCacheStatus,
    SimulationInputSnapshot, SimulationIssueCode, SimulationPreflightError,
    SimulationProgress, SimulationRunHandle, SimulationRunRecord,
    SimulationSamplingPolicy, build_tool_envelope,
)
from hms_cadcam.viewer.simulation import (
    SimulationDisplayContext, SimulationDisplayPolicy,
)
from hms_cadcam.ui.simulation_ui import (
    SimulationIssueSelection, SimulationPanel,
)
from hms_cadcam.ui.post_ui import PostProcessorPanel
from hms_cadcam.ui.program_assembly_ui import ProgramAssemblyPanel

_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 20
_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 21
_JOB_ROLE = int(Qt.ItemDataRole.UserRole) + 22
_SETUP_ROLE = int(Qt.ItemDataRole.UserRole) + 23

logger = logging.getLogger(__name__)


class CamWorkspace(QWidget):
    """One self-contained CAM management surface; CAD viewport remains central."""

    message = Signal(str)

    def __init__(self, service: ProjectService,
                 source_provider: Callable[[], UUID | None],
                 pick_provider: Callable[[], GeometryReference] | None = None,
                 toolpath_display: Callable[[object], object] | None = None,
                 toolpath_clear: Callable[[], None] | None = None,
                 parent: QWidget | None = None,
                 toolpath_remove: Callable[[OperationId], None] | None = None,
                 face_resolver: Callable[[GeometryReference], ResolvedMachiningGeometry] | None = None,
                 contour_pick_provider: Callable[[], GeometryReference] | None = None,
                 profile_resolver: Callable[[GeometryReference], ResolvedContourProfile] | None = None,
                 pocket_resolver: Callable[[GeometryReference], ResolvedPocketGeometry] | None = None,
                 drilling_pick_provider: Callable[
                     [Vector3], HoleReference | HolePattern
                 ] | None = None,
                 drilling_resolver: Callable[
                     [DrillGeometryInput, DrillDepthDefinition], ResolvedDrillingGeometry
                 ] | None = None,
                 simulation_scene_builder: Callable[
                     [SimulationInputSnapshot], tuple[CollisionScene, CollisionBackend]
                 ] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CamWorkspace")
        self._service = service
        self._source_provider = source_provider
        self._pick_provider = pick_provider
        self._toolpath_display = toolpath_display
        self._toolpath_clear = toolpath_clear
        self._toolpath_remove = toolpath_remove
        self._displayed_operation_id: OperationId | None = None
        self._toolpath_visibility: dict[OperationId, bool] = {}
        self._face_resolver = face_resolver
        self._contour_pick_provider = contour_pick_provider
        self._profile_resolver = profile_resolver
        self._pocket_resolver = pocket_resolver
        self._drilling_pick_provider = drilling_pick_provider
        self._drilling_resolver = drilling_resolver
        self._simulation_scene_builder = simulation_scene_builder
        self._simulation_policies: dict[
            OperationId, tuple[SimulationSamplingPolicy, SimulationDisplayPolicy]
        ] = {}
        self._simulation_cache_attempts: set[tuple[OperationId, str]] = set()
        self._simulation_handle: SimulationRunHandle | None = None
        self._simulation_project_id: UUID | None = None
        self._picked_reference: GeometryReference | None = None
        self._picked_hole_reference: HoleReference | None = None
        self._picked_hole_source: HoleReference | HolePattern | None = None
        self._picked_reference_resolved = False
        self._geometry_resolution_error = ""
        self._pocket_drafts: dict[
            OperationId, tuple[dict[str, object], GeometryReference | None]
        ] = {}
        self._drilling_drafts: dict[
            OperationId,
            tuple[dict[str, object], HoleReference | HolePattern | None],
        ] = {}
        self._tapping_drafts: dict[
            OperationId,
            tuple[dict[str, object], HoleReference | HolePattern | None],
        ] = {}
        self._reaming_drafts: dict[
            OperationId,
            tuple[dict[str, object], HoleReference | HolePattern | None],
        ] = {}
        self._boring_drafts: dict[
            OperationId,
            tuple[dict[str, object], HoleReference | HolePattern | None],
        ] = {}
        self._active_editor_operation_id: OperationId | None = None
        self._active_editor_strategy_key: str | None = None
        self._generation: int | None = None
        self._guard = False
        self._selected_key: tuple[str, str] | None = None
        self.tree = QTreeWidget()
        self.tree.setObjectName("CamOperationTree")
        self.tree.setHeaderLabels(["CAM Project / Operation", "Trạng thái"])
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemChanged.connect(self._item_changed)
        self.editor = _CamPropertiesEditor(self._apply_properties)
        self.simulation_panel = SimulationPanel()
        self.post_panel = PostProcessorPanel(service)
        self.program_assembly_panel = ProgramAssemblyPanel(service)
        self.post_tabs = QTabWidget()
        self.post_tabs.setObjectName("CamPostTabs")
        self.post_tabs.addTab(self.post_panel, "Post Processor")
        self.post_tabs.addTab(self.program_assembly_panel, "Program Assembly")
        self.splitter = QSplitter()
        self.splitter.setObjectName("ClassicCamWorkspaceSplitter")
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.editor)
        self.splitter.addWidget(self.simulation_panel)
        self.splitter.addWidget(self.post_tabs)
        self.splitter.setSizes([280, 320, 360, 460])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QToolBar("Lệnh CAM")
        layout.addWidget(self.toolbar)
        layout.addWidget(self.splitter)
        self.actions = self._actions()
        self.editor.draft_changed.connect(self._update_generate_action)
        self.simulation_panel.run_requested.connect(self._run_simulation)
        self.simulation_panel.cancel_requested.connect(self._cancel_simulation)
        self.simulation_panel.clear_requested.connect(self._clear_simulation_result)
        self.simulation_panel.visibility_requested.connect(
            self._set_simulation_visibility
        )
        self.simulation_panel.policy_applied.connect(
            self._apply_simulation_policy
        )
        self.simulation_panel.issue_focus_requested.connect(
            self._focus_simulation_issue
        )
        self.simulation_panel.issue_selection_cleared.connect(
            self._clear_simulation_issue_focus
        )
        self.post_panel.message.connect(self.message.emit)
        self.program_assembly_panel.message.connect(self.message.emit)
        for key in ("job", "setup", "resources", "tapping_resources", "reaming_resources", "boring_resources", "group", "operation", "contour_operation", "pocket_operation", "drilling_operation", "tapping_operation", "reaming_operation", "boring_operation", "generate", "visibility",
                    "pick", "clear_pick", "up", "down", "delete"):
            self.toolbar.addAction(self.actions[key])
        self.bind_project(service.current_project)

    def _actions(self) -> dict[str, QAction]:
        definitions = {
            "job": ("Tạo Job", self.create_job), "setup": ("Tạo Setup", self.create_setup),
            "resources": ("Tạo Tool/Machine cơ bản", self.create_basic_resources),
            "tapping_resources": (
                "Tạo TAP Tool/Machine cơ bản",
                self.create_basic_tapping_resources,
            ),
            "reaming_resources": (
                "Tạo REAMER Tool/Machine cơ bản",
                self.create_basic_reaming_resources,
            ),
            "boring_resources": (
                "Tạo BORING BAR Tool/Machine cơ bản",
                self.create_basic_boring_resources,
            ),
            "group": ("Thêm Group", self.add_group),
            "operation": ("Thêm Facing 2.5D", self.add_operation),
            "contour_operation": ("Thêm 2D Contour", self.add_contour_operation),
            "pocket_operation": ("Thêm Pocket 2.5D", self.add_pocket_operation),
            "drilling_operation": ("Thêm Drilling", self.add_drilling_operation),
            "tapping_operation": ("Thêm Tapping", self.add_tapping_operation),
            "reaming_operation": ("Thêm Reaming", self.add_reaming_operation),
            "boring_operation": ("Thêm Boring", self.add_boring_operation),
            "generate": ("Generate/Recompute", self.generate_selected),
            "visibility": ("Hiện/ẩn toolpath", self.toggle_toolpath_visibility),
            "pick": ("Bind/Rebind geometry", self.pick_geometry),
            "clear_pick": ("Clear geometry", self.clear_geometry_pick),
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
        self._simulation_handle = None
        self._simulation_project_id = None
        self._simulation_policies.clear()
        self._simulation_cache_attempts.clear()
        self.simulation_panel.clear_source()
        self.post_panel.bind_project(session)
        self.program_assembly_panel.bind_project(session)
        self.simulation_panel.set_policy(
            SimulationSamplingPolicy(),
            SimulationDisplayPolicy(),
        )
        self._clear_simulation_issue_focus()
        self._selected_key = None
        self._picked_reference = None
        self._picked_hole_reference = None
        self._picked_hole_source = None
        self._picked_reference_resolved = False
        self._geometry_resolution_error = ""
        self._pocket_drafts.clear()
        self._drilling_drafts.clear()
        self._tapping_drafts.clear()
        self._reaming_drafts.clear()
        self._boring_drafts.clear()
        self._toolpath_visibility.clear()
        self._active_editor_operation_id = None
        self._active_editor_strategy_key = None
        self.editor.clear()
        if self._toolpath_clear is not None:
            self._toolpath_clear()
        self._displayed_operation_id = None
        if not isinstance(session, ProjectSession):
            self._generation = None
            self._render(None)
            return
        self._generation = self._service.cam_generation
        self._simulation_project_id = session.manifest.project_id
        self._render(None)

    def refresh(self, preserve: tuple[str, str] | None = None) -> None:
        if self._generation is None or self._generation != self._service.cam_generation:
            return
        self._render(preserve or self._selected_key)

    def _render(self, preserve: tuple[str, str] | None) -> None:
        self._stash_active_operation_draft()
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
            self._active_editor_operation_id = None
            self.editor.clear()
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
        self.program_assembly_panel.refresh_sources()

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
                is_hole_operation = operation.strategy_key in {
                    "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                }
                if operation.geometry_inputs:
                    status += (
                        " · HOLE BOUND" if is_hole_operation
                        else " · PROFILE BOUND"
                    )
                elif is_hole_operation:
                    try:
                        strategy = _hole_strategy(operation)
                        has_explicit_pattern = (
                            isinstance(strategy.geometry.source, HolePattern)
                            and not _hole_references(strategy.geometry.source)
                        )
                    except (RuntimeError, TypeError, ValueError):
                        has_explicit_pattern = False
                    status += (
                        " · HOLE PATTERN" if has_explicit_pattern
                        else " · HOLE MISSING"
                    )
                elif operation.strategy_key in {"contour_2d", "pocket_2_5d"}:
                    status += " · PROFILE MISSING"
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
            self._stash_active_operation_draft()
            self._active_editor_operation_id = None
            self._active_editor_strategy_key = None
            self._selected_key = None
            self.editor.clear()
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
            return
        self._selected_key = (item.data(0, _KIND_ROLE), item.data(0, _ID_ROLE))
        self._show_properties(item)

    def _show_properties(self, item: QTreeWidgetItem) -> None:
        self._stash_active_operation_draft()
        self._active_editor_operation_id = None
        self._active_editor_strategy_key = None
        self.simulation_panel.clear_source()
        self._clear_simulation_issue_focus()
        kind = item.data(0, _KIND_ROLE)
        job = self._find_job(item)
        setup = self._find_setup(item, job)
        if kind == "job" and job:
            self.editor.show_job(job.name)
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
        elif kind in {"setup", "stock"} and setup:
            self.editor.show_setup(setup)
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
        elif kind in {"group", "operation"} and setup:
            node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE)))
            operation = (
                setup.operation_tree.get_operation(node.operation_id)
                if node.operation_id is not None else None
            )
            if operation is None:
                self._picked_reference = None
                self._picked_hole_reference = None
                self._picked_hole_source = None
                self.editor.show_node(node.name, None)
                self._remove_displayed_toolpath()
                self.post_panel.clear_operation()
                self.program_assembly_panel.clear_selected_operation()
            else:
                self.post_panel.set_operation(
                    operation.operation_id,
                    generation=self._generation,
                    operation_name=node.name,
                )
                self.program_assembly_panel.set_selected_operation(
                    operation.operation_id,
                    operation_name=node.name,
                )
                self._picked_hole_reference = None
                self._picked_hole_source = None
                self._picked_reference = (
                    operation.geometry_inputs[0].reference
                    if len(operation.geometry_inputs) == 1 else None
                )
                if operation.strategy_key in {
                    "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                }:
                    try:
                        hole_strategy = _hole_strategy(operation)
                    except (TypeError, ValueError):
                        hole_strategy = None
                    source = (
                        hole_strategy.geometry.source
                        if hole_strategy is not None else None
                    )
                    if source is not None and _operation_matches_hole_source(
                        operation, source,
                    ):
                        self._set_picked_hole_source(source)
                self._picked_reference_resolved = self._resolve_picked_reference()
                snapshot = self._service.cam_snapshot
                self.editor.show_node(
                    node.name,
                    operation,
                    snapshot.tool_assemblies,
                    snapshot.machine_definitions,
                    snapshot.tool_definitions,
                    snapshot.holder_definitions,
                )
                if operation.strategy_key == "pocket_2_5d":
                    saved = self._pocket_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_pocket_state(saved[0])
                        self._picked_reference = saved[1]
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                elif operation.strategy_key == "drilling_v1":
                    saved = self._drilling_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_drilling_state(saved[0])
                        self._set_picked_hole_source(saved[1])
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                elif operation.strategy_key == "tapping_v1":
                    saved = self._tapping_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_tapping_state(saved[0])
                        self._set_picked_hole_source(saved[1])
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                elif operation.strategy_key == "reaming_v1":
                    saved = self._reaming_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_reaming_state(saved[0])
                        self._set_picked_hole_source(saved[1])
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                elif operation.strategy_key == "boring_v1":
                    saved = self._boring_drafts.get(operation.operation_id)
                    if saved is not None:
                        self.editor.restore_boring_state(saved[0])
                        self._set_picked_hole_source(saved[1])
                        self._picked_reference_resolved = self._resolve_picked_reference()
                    self._active_editor_operation_id = operation.operation_id
                    self._active_editor_strategy_key = operation.strategy_key
                if operation.strategy_key in {
                    "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                }:
                    self.editor.show_hole_source(
                        self._picked_hole_source,
                        self._picked_reference_resolved,
                    )
                else:
                    self.editor.show_reference(
                        self._picked_reference,
                        self._picked_reference_resolved,
                    )
                artifact = (self._service.load_toolpath_artifact(operation.operation_id)
                            if operation.artifact_state.status is ArtifactStatus.VALID else None)
                if artifact is not None and self._toolpath_display is not None:
                    displayed = self._toolpath_display(artifact)
                    if displayed is not False:
                        self.editor.show_toolpath_metadata(
                            ToolpathPresentation.from_artifact(artifact)
                        )
                        previous = self._displayed_operation_id
                        self._displayed_operation_id = operation.operation_id
                        self._toolpath_visibility[operation.operation_id] = True
                        if previous is not None and previous != operation.operation_id:
                            self._remove_toolpath(previous)
                else:
                    self._remove_displayed_toolpath()
                self._bind_simulation_operation(operation, artifact)
        else:
            self._picked_reference = None
            self.editor.clear()
            self._remove_displayed_toolpath()
            self.post_panel.clear_operation()
            self.program_assembly_panel.clear_selected_operation()
        self._update_generate_action()

    def _remove_toolpath(self, operation_id: OperationId) -> None:
        if self._toolpath_remove is not None:
            self._toolpath_remove(operation_id)
        elif self._toolpath_clear is not None:
            self._toolpath_clear()

    def _remove_displayed_toolpath(self) -> None:
        if self._displayed_operation_id is not None:
            self._remove_toolpath(self._displayed_operation_id)
            self._displayed_operation_id = None

    def _bind_simulation_operation(
        self,
        operation: Operation,
        artifact: object | None,
    ) -> None:
        policies = self._simulation_policies.get(
            operation.operation_id,
            (SimulationSamplingPolicy(), SimulationDisplayPolicy()),
        )
        self.simulation_panel.set_policy(*policies)
        if artifact is None:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                "sim.source_missing: ToolpathArtifact chưa COMPLETE/current",
            )
            return
        try:
            inputs = self._service.capture_simulation_inputs(
                operation.operation_id,
                sampling_policy=policies[0],
            )
            build_tool_envelope(
                tool=inputs.tool,
                assembly=inputs.assembly,
                holder=inputs.holder,
            )
            if any(
                fixture.enabled
                and (
                    fixture.geometry_reference.kind
                    not in {GeometryReferenceKind.BODY, GeometryReferenceKind.OCCURRENCE}
                    or fixture.geometry_reference.geometry_kind
                    is not GeometryRepresentationKind.BREP
                )
                for fixture in inputs.setup.fixtures
            ):
                raise SimulationPreflightError(
                    SimulationIssueCode.UNSUPPORTED_GEOMETRY,
                    "Fixture geometry is unsupported",
                )
        except SimulationPreflightError as error:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                f"{error.code.value}: {error}",
            )
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                f"sim.unsupported_geometry: {error}",
            )
            return
        if self._simulation_scene_builder is not None:
            try:
                # Validate native stock/fixture ownership on the owner thread
                # before enabling Run. The scene is rebuilt at execution time
                # so this validation never retains native handles in UI state.
                self._simulation_scene_builder(inputs)
            except SimulationPreflightError as error:
                self.simulation_panel.show_unavailable(
                    operation.operation_id,
                    f"{error.code.value}: {error}",
                )
                return
            except (RuntimeError, TypeError, ValueError) as error:
                self.simulation_panel.show_unavailable(
                    operation.operation_id,
                    f"sim.source_unsupported: {error}",
                )
                return
        can_run = (
            self._simulation_scene_builder is not None
            and operation.enabled
            and operation.artifact_state.status is ArtifactStatus.VALID
        )
        self.simulation_panel.show_source(inputs, can_run=can_run)
        record = self._service.simulation_runs.record(operation.operation_id)
        result = self._service.simulation_runs.result(operation.operation_id)
        if (
            result is not None
            and result.artifact_fingerprint == inputs.request.artifact_fingerprint
            and result.input_fingerprint == inputs.request.input_fingerprint
        ):
            self.simulation_panel.set_run_record(record)
            self.simulation_panel.set_result(
                result,
                self._simulation_presentation(operation.operation_id),
                current=True,
            )
            return
        cache_key = (operation.operation_id, inputs.request.artifact_fingerprint.digest)
        if cache_key in self._simulation_cache_attempts:
            if record is not None:
                self.simulation_panel.set_run_record(record)
            return
        self._simulation_cache_attempts.add(cache_key)
        loaded = self._service.load_cached_simulation_for_source(
            operation.operation_id,
            inputs.request.artifact_fingerprint,
        )
        if loaded.status is not SimulationCacheStatus.VALID or loaded.result is None:
            self.simulation_panel.set_cache_diagnostic(
                loaded.message or loaded.status.value
            )
            return
        cached_result = loaded.result
        cached_policies = (cached_result.sampling_policy, policies[1])
        try:
            cached_inputs = self._service.capture_simulation_inputs(
                operation.operation_id,
                sampling_policy=cached_result.sampling_policy,
            )
        except (RuntimeError, TypeError, ValueError):
            self.simulation_panel.set_cache_diagnostic("cache stale")
            return
        if cached_inputs.request.input_fingerprint != cached_result.input_fingerprint:
            self.simulation_panel.set_cache_diagnostic("cache stale")
            return
        # A cache result retains the original request UUID.  Rebind the
        # freshly captured immutable request to that UUID for the runtime
        # provenance check; request identity/fingerprints remain unchanged.
        cached_inputs = replace(
            cached_inputs,
            request=replace(
                cached_inputs.request,
                request_id=cached_result.request_id,
            ),
        )
        self._simulation_policies[operation.operation_id] = cached_policies
        self.simulation_panel.set_policy(*cached_policies)
        self.simulation_panel.show_source(cached_inputs, can_run=can_run)
        if not self._service.simulation_runs.restore_cached(
            cached_inputs.request,
            cached_result,
            state_callback=self._simulation_state_changed,
        ):
            self.simulation_panel.set_cache_diagnostic("cache stale")
            return
        self._display_simulation_result(cached_inputs, cached_result)

    def _run_simulation(self) -> None:
        operation = self._selected_operation()
        if operation is None or self._simulation_scene_builder is None:
            return
        controller = self._service.simulation_runs
        if (
            self._simulation_handle is not None
            and controller.is_active(self._simulation_handle.request.operation_id)
        ):
            return
        sampling = self.simulation_panel.sampling_policy
        try:
            inputs = self._service.capture_simulation_inputs(
                operation.operation_id,
                sampling_policy=sampling,
            )
            # OCP geometry is resolved here, on the UI/owner thread, and is never
            # passed to a worker or retained by the runtime record.
            scene, backend = self._simulation_scene_builder(inputs)
            handle = controller.start(
                inputs.request,
                state_callback=self._simulation_state_changed,
            )
        except SimulationPreflightError as error:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                f"{error.code.value}: {error}",
            )
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self.simulation_panel.show_unavailable(
                operation.operation_id,
                f"sim.unsupported_geometry: {error}",
            )
            return
        self._simulation_handle = handle
        project_id = self._simulation_project_id
        generation = self._generation
        QTimer.singleShot(
            0,
            lambda: self._execute_simulation(
                handle,
                inputs,
                scene,
                backend,
                project_id,
                generation,
            ),
        )

    def _execute_simulation(
        self,
        handle: SimulationRunHandle,
        inputs: SimulationInputSnapshot,
        scene: CollisionScene,
        backend: CollisionBackend,
        project_id: UUID | None,
        generation: int | None,
    ) -> None:
        controller = self._service.simulation_runs

        def current_request():
            try:
                return self._service.capture_simulation_inputs(
                    handle.request.operation_id,
                    sampling_policy=handle.request.sampling_policy,
                ).request
            except Exception as error:
                raise SimulationPreflightError(
                    SimulationIssueCode.STALE_RESULT,
                    "Simulation inputs changed before publish",
                ) from error

        execution = controller.execute(
            handle,
            snapshot=inputs,
            scene=scene,
            backend=backend,
            current_request=current_request,
            progress_callback=self._simulation_progress_changed,
            state_callback=self._simulation_state_changed,
        )
        if not self._simulation_callback_is_current(
            handle,
            project_id,
            generation,
        ):
            return
        self.simulation_panel.set_run_record(
            controller.record(handle.request.operation_id)
        )
        if not execution.accepted or execution.result is None:
            code = execution.code.value if execution.code is not None else "sim.failed"
            self.message.emit(f"Simulation: {code} · {execution.message or ''}")
            return
        try:
            self._service.persist_simulation_result(execution.result)
        except Exception as error:
            logger.warning("Không thể ghi simulation cache", exc_info=True)
            self.simulation_panel.set_cache_diagnostic(
                f"cache write failure: {error}"
            )
        displayed = self._display_simulation_result(inputs, execution.result)
        if displayed:
            self.message.emit(
                f"Simulation {execution.result.status.value.upper()} đã publish và hiển thị."
            )
        else:
            self.message.emit(
                "Simulation result đã publish nhưng overlay mới không thể hiển thị; overlay cũ được giữ."
            )

    def _simulation_progress_changed(self, progress: SimulationProgress) -> None:
        handle = self._simulation_handle
        if handle is None or handle.request.request_id != progress.request_id:
            return
        panel_inputs = self.simulation_panel.inputs
        if (
            panel_inputs is not None
            and panel_inputs.operation.operation_id == handle.request.operation_id
        ):
            self.simulation_panel.set_progress(progress)
        # Native narrow phase stays on its owner thread. Pump bounded event slices
        # so Cancel/project-switch can invalidate the request cooperatively.
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 5)

    def _simulation_state_changed(self, record: SimulationRunRecord) -> None:
        inputs = self.simulation_panel.inputs
        if inputs is None or inputs.operation.operation_id != record.operation_id:
            return
        self.simulation_panel.set_run_record(record)

    def _cancel_simulation(self) -> None:
        handle = self._simulation_handle
        if handle is None:
            return
        if self._service.simulation_runs.cancel(handle.request.operation_id):
            record = self._service.simulation_runs.record(
                handle.request.operation_id
            )
            self.simulation_panel.set_run_record(record)

    def _apply_simulation_policy(
        self,
        sampling: SimulationSamplingPolicy,
        display: SimulationDisplayPolicy,
    ) -> None:
        operation = self._selected_operation()
        if operation is None:
            return
        self._simulation_policies[operation.operation_id] = (sampling, display)
        self._service.simulation_runs.mark_stale(
            operation.operation_id,
            "Sampling policy changed",
        )
        viewport = self._simulation_viewport()
        if viewport is not None and callable(getattr(viewport, "remove_simulation", None)):
            viewport.remove_simulation(operation.operation_id)
        self.simulation_panel.mark_result_stale(
            "STALE / NON-CURRENT · sampling policy changed"
        )
        artifact = self._service.load_toolpath_artifact(operation.operation_id)
        self._bind_simulation_operation(operation, artifact)

    def _clear_simulation_result(self) -> None:
        operation = self._selected_operation()
        if operation is None:
            return
        self._service.clear_simulation_result(
            operation.operation_id,
            delete_cache=True,
        )
        viewport = self._simulation_viewport()
        if viewport is not None and callable(getattr(viewport, "remove_simulation", None)):
            viewport.remove_simulation(operation.operation_id)
        self.simulation_panel.clear_result_display()

    def _set_simulation_visibility(self, visible: bool) -> None:
        operation = self._selected_operation()
        viewport = self._simulation_viewport()
        if (
            operation is None
            or viewport is None
            or not callable(getattr(viewport, "set_simulation_visibility", None))
        ):
            return
        viewport.set_simulation_visibility(operation.operation_id, visible)
        presentation = self._simulation_presentation(operation.operation_id)
        if presentation is not None:
            self.simulation_panel.source_labels["overlay"].setText(
                f"{presentation.displayed_path_point_count} / "
                f"{presentation.total_path_point_count} points · "
                f"{presentation.displayed_marker_count} / "
                f"{presentation.total_marker_count} markers · "
                f"{'visible' if presentation.visible else 'hidden'}"
            )

    def _focus_simulation_issue(self, selection: object) -> None:
        if not isinstance(selection, SimulationIssueSelection):
            return
        result = self._service.simulation_runs.result(selection.operation_id)
        viewport = self._simulation_viewport()
        if (
            result is None
            or viewport is None
            or selection.marker_id is None
            or self._simulation_project_id is None
            or not callable(getattr(viewport, "focus_simulation_issue", None))
        ):
            return
        viewport.focus_simulation_issue(
            project_id=self._simulation_project_id,
            operation_id=selection.operation_id,
            result_id=result.result_id,
            marker_id=selection.marker_id,
        )

    def _clear_simulation_issue_focus(self) -> None:
        viewport = self._simulation_viewport()
        if viewport is not None and callable(
            getattr(viewport, "clear_simulation_issue_focus", None)
        ):
            viewport.clear_simulation_issue_focus()

    def _display_simulation_result(
        self,
        inputs: SimulationInputSnapshot,
        result,
    ) -> bool:
        viewport = self._simulation_viewport()
        session = self._service.current_project
        if viewport is None or not isinstance(session, ProjectSession):
            self.simulation_panel.set_result(result, None, current=True)
            return False
        generation = self._service.cam_generation
        request = viewport.request_simulation_display(
            inputs.operation.operation_id,
            generation=generation,
        )
        context = SimulationDisplayContext(
            project_id=session.manifest.project_id,
            project_generation=generation,
            operation_id=inputs.operation.operation_id,
            operation_revision=inputs.operation.revision,
            operation_exists=True,
            operation_enabled=inputs.operation.enabled,
            artifact_id=inputs.artifact.artifact_id,
            artifact_fingerprint=inputs.artifact.artifact_fingerprint,
            simulation_input_fingerprint=result.input_fingerprint,
            current_result_id=result.result_id,
            current_result_fingerprint=result.result_fingerprint,
        )
        self._service.simulation_runs.report_rendering(
            result.request_id,
            processed=0,
            total=1,
            callback=self._simulation_progress_changed,
        )
        displayed = viewport.display_simulation(
            result,
            inputs.artifact,
            inputs.setup.wcs,
            context,
            request,
            self.simulation_panel.display_policy,
        )
        self._service.simulation_runs.report_rendering(
            result.request_id,
            processed=1,
            total=1,
            callback=self._simulation_progress_changed,
        )
        presentation = self._simulation_presentation(inputs.operation.operation_id)
        current = (
            self._service.simulation_runs.result(inputs.operation.operation_id)
            is result
        )
        self.simulation_panel.set_result(
            result,
            presentation if displayed else None,
            current=current,
        )
        return displayed

    def _simulation_presentation(self, operation_id: OperationId):
        viewport = self._simulation_viewport()
        if viewport is None:
            return None
        return next(
            (
                value
                for value in viewport.simulation_presentations
                if value.key.operation_id == operation_id
            ),
            None,
        )

    def _simulation_viewport(self):
        return (
            self._toolpath_display.__self__
            if self._toolpath_display is not None
            and hasattr(self._toolpath_display, "__self__")
            else None
        )

    def _simulation_callback_is_current(
        self,
        handle: SimulationRunHandle,
        project_id: UUID | None,
        generation: int | None,
    ) -> bool:
        session = self._service.current_project
        if (
            not isinstance(session, ProjectSession)
            or project_id is None
            or session.manifest.project_id != project_id
            or generation != self._generation
            or generation != self._service.cam_generation
        ):
            return False
        record = self._service.simulation_runs.record(
            handle.request.operation_id
        )
        return record is not None and record.request_id == handle.request.request_id

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
        unit = _length_unit(self._service.current_project)
        mill_values = basic_mill_resources(unit)
        drill, center_drill, holder, drill_assembly, center_assembly = (
            basic_drilling_resources(unit)
        )
        def add_resources(app):
            app.add_basic_resources(*mill_values)
            app.add_tool_definition(drill)
            app.add_tool_definition(center_drill)
            app.add_holder_definition(holder)
            app.add_tool_assembly(drill_assembly)
            return app.add_tool_assembly(center_assembly)

        changed = self._execute(add_resources)
        if changed is not None:
            self.refresh(self._selected_key)

    def create_basic_tapping_resources(self) -> None:
        """Create the project-owned RH/LH TAP bundle explicitly."""
        if not self._service.has_project:
            return
        unit = _length_unit(self._service.current_project)
        (
            right_tap,
            left_tap,
            tapping_holder,
            right_tap_assembly,
            left_tap_assembly,
            tapping_machine,
        ) = basic_tapping_resources(unit)

        def add_resources(app):
            app.add_tool_definition(right_tap)
            app.add_tool_definition(left_tap)
            app.add_holder_definition(tapping_holder)
            app.add_tool_assembly(right_tap_assembly)
            app.add_tool_assembly(left_tap_assembly)
            return app.add_machine_definition(tapping_machine)

        changed = self._execute(add_resources)
        if changed is not None:
            self.refresh(self._selected_key)

    def create_basic_reaming_resources(self) -> None:
        """Create one project-owned REAMER and compatible milling machine."""
        if not self._service.has_project:
            return
        unit = _length_unit(self._service.current_project)
        reamer, holder, assembly, machine = basic_reaming_resources(unit)

        def add_resources(app):
            app.add_tool_definition(reamer)
            app.add_holder_definition(holder)
            app.add_tool_assembly(assembly)
            return app.add_machine_definition(machine)

        changed = self._execute(add_resources)
        if changed is not None:
            self.refresh(self._selected_key)

    def create_basic_boring_resources(self) -> None:
        """Create one project-owned BORING_BAR and compatible milling machine."""
        if not self._service.has_project:
            return
        unit = _length_unit(self._service.current_project)
        tool, holder, assembly, machine = basic_boring_resources(unit)

        def add_resources(app):
            app.add_tool_definition(tool)
            app.add_holder_definition(holder)
            app.add_tool_assembly(assembly)
            return app.add_machine_definition(machine)

        changed = self._execute(add_resources)
        if changed is not None:
            self.refresh(self._selected_key)

    def pick_geometry(self) -> None:
        """Explicitly bind the current unambiguous CAD selection."""
        operation = self._selected_operation()
        is_hole_operation = (
            operation is not None
            and operation.strategy_key in {
                "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
            }
        )
        provider = (
            self._drilling_pick_provider if is_hole_operation
            else self._contour_pick_provider if operation is not None and
            operation.strategy_key in {"contour_2d", "pocket_2_5d"}
            else self._pick_provider
        )
        if provider is None:
            self._error("Geometry picking adapter chưa sẵn sàng.")
            return
        generation = self._generation
        try:
            if is_hole_operation:
                item = self.tree.currentItem()
                setup = self._find_setup(item, self._find_job(item))
                if setup is None:
                    raise ValueError("Hole operation chưa có Setup hợp lệ.")
                hole_source = provider(setup.wcs.z_axis)
                if not isinstance(hole_source, (HoleReference, HolePattern)):
                    raise TypeError("Hole picker returned an invalid source")
                references = _hole_references(hole_source)
                reference = (
                    references[0].reference if len(references) == 1 else None
                )
            else:
                hole_source = None
                reference = provider()
        except Exception as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("Phiên chọn hình học đã bị hủy vì dự án đã thay đổi.")
            return
        previous = self._picked_reference
        previous_hole = self._picked_hole_reference
        previous_hole_source = self._picked_hole_source
        previous_status = self._picked_reference_resolved
        if is_hole_operation:
            self._set_picked_hole_source(hole_source)
        else:
            self._picked_reference = reference
        self._picked_reference_resolved = self._resolve_picked_reference()
        if not self._picked_reference_resolved:
            self._picked_reference = previous
            self._picked_hole_reference = previous_hole
            self._picked_hole_source = previous_hole_source
            self._picked_reference_resolved = previous_status
            self._error(
                self._geometry_resolution_error
                or "Persistent geometry could not be resolved unambiguously."
            )
            return
        if is_hole_operation:
            self.editor.show_hole_source(self._picked_hole_source, True)
        else:
            self.editor.show_reference(self._picked_reference, True)
        self._stash_active_operation_draft()
        self._update_generate_action()
        self.message.emit("Đã tạo GeometryReference; dùng Rebind để thay thế rõ ràng.")

    def clear_geometry_pick(self) -> None:
        """Clear an explicit binding; never choose a replacement automatically."""
        operation = self._selected_operation()
        if (operation is not None
                and operation.strategy_key in {
                    "contour_2d", "drilling_v1", "pocket_2_5d", "tapping_v1",
                    "reaming_v1", "boring_v1",
                }
                and operation.geometry_inputs):
            item = self.tree.currentItem()
            job = self._find_job(item)
            setup = self._find_setup(item, job)
            if item is not None and job is not None and setup is not None:
                changed = replace(operation, geometry_inputs=(), revision=operation.revision.next(),
                                  artifact_state=operation.artifact_state.mark_dirty(
                                      DirtyReason.GEOMETRY_CHANGED))
                updated = self._execute(lambda app: app.update_tree(job.job_id, setup.setup_id,
                    lambda tree: tree.replace_operation(changed)))
                if updated is None:
                    return
                self.refresh(self._selected_key)
        self._picked_reference = None
        self._picked_hole_reference = None
        self._picked_hole_source = None
        self._picked_reference_resolved = False
        self._geometry_resolution_error = ""
        if operation is not None and operation.strategy_key in {
            "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
        }:
            self.editor.show_hole_source(None)
        else:
            self.editor.show_reference(None)
        self._stash_active_operation_draft()
        self._update_generate_action()

    def cad_context_changed(self, *, force_invalidate: bool = False) -> None:
        """Re-resolve displayed references after CAD reload without rebinding them."""
        self._picked_reference_resolved = self._resolve_picked_reference()
        invalidated = False
        if not self._service.has_project:
            self._update_generate_action()
            return
        if force_invalidate:
            self._service.simulation_runs.cancel_all(stale=True)
            viewport = self._simulation_viewport()
            if viewport is not None:
                viewport.clear_simulations()
            self.simulation_panel.mark_result_stale(
                "STALE / NON-CURRENT · CAD source reimported"
            )
        if (
            self._face_resolver is not None
            or self._profile_resolver is not None
            or self._drilling_resolver is not None
        ) and self._generation is not None:
            for job in self._service.cam_snapshot.jobs:
                for setup in job.setups:
                    for operation in setup.operation_tree.operations:
                        if operation.artifact_state.status is not ArtifactStatus.VALID:
                            continue
                        if (
                            operation.strategy_key not in {
                                "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                            }
                            and len(operation.geometry_inputs) != 1
                        ):
                            continue
                        resolver = None
                        if operation.strategy_key == "facing_2_5d":
                            try:
                                parameters = FacingParameters.from_operation_parameters(operation.parameters)
                            except (RuntimeError, TypeError, ValueError):
                                continue
                            if parameters.boundary_source is FacingBoundarySource.PLANAR_FACE:
                                resolver = self._face_resolver
                        elif operation.strategy_key in {"contour_2d", "pocket_2_5d"}:
                            resolver = self._profile_resolver
                        elif operation.strategy_key in {
                            "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                        }:
                            try:
                                hole_strategy = _hole_strategy(operation)
                            except (RuntimeError, TypeError, ValueError):
                                continue
                            resolver = self._drilling_resolver
                        if resolver is None:
                            continue
                        if force_invalidate:
                            invalidated = self._execute(
                                lambda app, operation_id=operation.operation_id:
                                app.invalidate_operation(
                                    operation_id, DirtyReason.GEOMETRY_CHANGED,
                                )
                            ) is not None or invalidated
                            continue
                        try:
                            result = (
                                resolver(hole_strategy.geometry, hole_strategy.depth)
                                if operation.strategy_key in {
                                    "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
                                }
                                else resolver(operation.geometry_inputs[0].reference)
                            )
                        except (RuntimeError, TypeError, ValueError):
                            invalidated = self._execute(
                                lambda app, operation_id=operation.operation_id:
                                app.invalidate_operation(
                                    operation_id, DirtyReason.GEOMETRY_CHANGED,
                                )
                            ) is not None or invalidated
                            continue
                        if getattr(result, "status", None) is not GeometryResolutionStatus.RESOLVED:
                            invalidated = self._execute(
                                lambda app, operation_id=operation.operation_id:
                                app.invalidate_operation(
                                    operation_id, DirtyReason.GEOMETRY_CHANGED,
                                )
                            ) is not None or invalidated
        if invalidated:
            self.refresh(self._selected_key)
        self._update_generate_action()

    def _resolve_picked_reference(self) -> bool:
        self._geometry_resolution_error = ""
        if self._picked_hole_source is not None:
            if self._drilling_resolver is None:
                return True
            operation = self._selected_operation()
            if operation is None or operation.strategy_key not in {
                "boring_v1", "drilling_v1", "tapping_v1", "reaming_v1",
            }:
                return False
            try:
                current = _hole_strategy(operation)
                geometry = DrillGeometryInput(
                    self._picked_hole_source,
                    self._picked_hole_source.unit,
                )
                result = self._drilling_resolver(geometry, current.depth)
            except (RuntimeError, TypeError, ValueError) as error:
                if operation.strategy_key == "boring_v1":
                    self._geometry_resolution_error = (
                        f"bore.geometry_missing: {error}"
                    )
                return False
            status = getattr(result, "status", None)
            if status is GeometryResolutionStatus.RESOLVED:
                return True
            if operation.strategy_key == "boring_v1":
                code = {
                    GeometryResolutionStatus.MISSING: "bore.geometry_missing",
                    GeometryResolutionStatus.STALE: "bore.geometry_stale",
                    GeometryResolutionStatus.TOPOLOGY_CHANGED: "bore.geometry_stale",
                    GeometryResolutionStatus.AMBIGUOUS: "bore.geometry_ambiguous",
                    GeometryResolutionStatus.SOURCE_MISMATCH: "bore.source_mismatch",
                }.get(status, "bore.invalid_parameters")
                diagnostics = getattr(result, "diagnostics", ())
                message = (
                    diagnostics[0].message
                    if diagnostics else "Boring geometry resolve thất bại."
                )
                self._geometry_resolution_error = f"{code}: {message}"
            return False
        if self._picked_reference is None:
            return False
        is_contour = (self._picked_reference.subshape_selector or "").startswith("hms_profile_v1:")
        resolver = self._profile_resolver if is_contour else self._face_resolver
        if resolver is None:
            return True
        try:
            result = resolver(self._picked_reference)
        except (RuntimeError, TypeError, ValueError):
            return False
        return getattr(result, "status", None) is GeometryResolutionStatus.RESOLVED

    def _selected_operation(self) -> Operation | None:
        item = self.tree.currentItem()
        if item is None or item.data(0, _KIND_ROLE) != "operation":
            return None
        setup = self._find_setup(item, self._find_job(item))
        if setup is None:
            return None
        node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE)))
        return setup.operation_tree.get_operation(node.operation_id) if node.operation_id else None

    def _set_picked_hole_source(
        self,
        source: HoleReference | HolePattern | None,
    ) -> None:
        """Set one native-free hole source and its single-reference compatibility view."""
        self._picked_hole_source = source
        self._picked_hole_reference = (
            source if isinstance(source, HoleReference) else None
        )
        references = () if source is None else _hole_references(source)
        self._picked_reference = (
            references[0].reference if len(references) == 1 else None
        )

    def _stash_active_operation_draft(self) -> None:
        """Keep unapplied operation drafts by stable domain identity only."""
        operation_id = self._active_editor_operation_id
        if operation_id is None:
            return
        if self._active_editor_strategy_key == "pocket_2_5d":
            self._pocket_drafts[operation_id] = (
                self.editor.pocket_state(),
                self._picked_reference,
            )
        elif self._active_editor_strategy_key == "drilling_v1":
            self._drilling_drafts[operation_id] = (
                self.editor.drilling_state(),
                self._picked_hole_source,
            )
        elif self._active_editor_strategy_key == "tapping_v1":
            self._tapping_drafts[operation_id] = (
                self.editor.tapping_state(),
                self._picked_hole_source,
            )
        elif self._active_editor_strategy_key == "reaming_v1":
            self._reaming_drafts[operation_id] = (
                self.editor.reaming_state(),
                self._picked_hole_source,
            )
        elif self._active_editor_strategy_key == "boring_v1":
            self._boring_drafts[operation_id] = (
                self.editor.boring_state(),
                self._picked_hole_source,
            )

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

    def add_contour_operation(self) -> None:
        """Add one editable 2D Contour operation without guessing its profile."""
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, _tree, parent_id = context
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        snapshot = self._service.cam_snapshot
        assembly = snapshot.tool_assemblies[0] if snapshot.tool_assemblies else None
        tool_reference = ToolAssemblyReference.from_assembly(assembly) if assembly else ToolAssemblyReference(
            ToolAssemblyId.new(), Revision(0), ContentFingerprint.from_payload({"missing": True}),
            _length_unit(self._service.current_project))
        machine = snapshot.machine_definitions[0] if snapshot.machine_definitions else None
        machine_requirement = None if machine is None else MachineRequirement(
            machine.machine_id, machine.revision, ContentFingerprint.from_payload(machine.to_dict()),
            machine.unit, (OperationCapability.MILLING,))
        setup = next(value for job in snapshot.jobs for value in job.setups if value.setup_id == setup_id)
        parameters = _default_contour_parameters(setup)
        operation = Operation(operation_id, node_id, OperationFamily.MILLING, setup_id,
                              tool_reference, (), parameters.to_operation_parameters(), machine_requirement)
        changed = self._execute(lambda app: app.update_tree(job_id, setup_id,
            lambda value: value.add_operation(parent_id, "2D Contour", operation)))
        if changed:
            self.refresh(("operation", str(node_id)))

    def add_pocket_operation(self) -> None:
        """Add one unbound Pocket operation without guessing its boundary."""
        context = self._tree_context()
        if context is None:
            return
        job_id, setup_id, _tree, parent_id = context
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        snapshot = self._service.cam_snapshot
        assembly = snapshot.tool_assemblies[0] if snapshot.tool_assemblies else None
        tool_reference = ToolAssemblyReference.from_assembly(assembly) if assembly else ToolAssemblyReference(
            ToolAssemblyId.new(), Revision(0), ContentFingerprint.from_payload({"missing": True}),
            _length_unit(self._service.current_project))
        machine = snapshot.machine_definitions[0] if snapshot.machine_definitions else None
        machine_requirement = None if machine is None else MachineRequirement(
            machine.machine_id, machine.revision, ContentFingerprint.from_payload(machine.to_dict()),
            machine.unit, (OperationCapability.MILLING,))
        setup = next(value for job in snapshot.jobs for value in job.setups
                     if value.setup_id == setup_id)
        operation = Operation(
            operation_id, node_id, OperationFamily.MILLING, setup_id,
            tool_reference, (), _default_pocket_parameters(setup), machine_requirement,
        )
        changed = self._execute(lambda app: app.update_tree(
            job_id, setup_id,
            lambda value: value.add_operation(parent_id, "Pocket 2.5D", operation),
        ))
        if changed:
            self.refresh(("operation", str(node_id)))

    def add_drilling_operation(self) -> None:
        """Add one explicitly bound Drilling operation without hole recognition."""
        context = self._tree_context()
        if context is None:
            return
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            self._error("Drilling geometry adapter chưa sẵn sàng.")
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value for job in snapshot.jobs for value in job.setups
            if value.setup_id == setup_id
        )
        generation = self._generation
        try:
            hole_source = self._drilling_pick_provider(setup.wcs.z_axis)
            if not isinstance(hole_source, (HoleReference, HolePattern)):
                raise TypeError("Drilling picker returned an invalid hole source")
            strategy = _default_drilling_strategy(setup, hole_source)
            resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
            if resolved.status is not GeometryResolutionStatus.RESOLVED:
                message = (
                    resolved.diagnostics[0].message
                    if resolved.diagnostics else "Drilling geometry is invalid"
                )
                raise ValueError(message)
            assembly = _find_drilling_assembly(snapshot, ToolFamily.DRILL)
            machine = next((
                value for value in snapshot.machine_definitions
                if OperationCapability.DRILLING in value.capabilities.operations
            ), None)
            if assembly is None or machine is None:
                raise ValueError(
                    "Hãy tạo Tool/Machine cơ bản trước khi thêm Drilling."
                )
        except (RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("Phiên tạo Drilling đã stale vì dự án thay đổi.")
            return
        node_id, operation_id = CamNodeId.new(), OperationId.new()
        requirement = MachineRequirement(
            machine.machine_id, machine.revision, machine.content_fingerprint,
            machine.unit, (OperationCapability.DRILLING,),
        )
        operation = Operation(
            operation_id, node_id, OperationFamily.DRILLING, setup_id,
            ToolAssemblyReference.from_assembly(assembly),
            _hole_geometry_inputs((), hole_source),
            strategy.to_operation_parameters(), requirement,
        )
        changed = self._execute(lambda app: app.update_tree(
            job_id, setup_id,
            lambda value: value.add_operation(parent_id, "Drilling", operation),
        ))
        if changed:
            self.refresh(("operation", str(node_id)))

    def add_tapping_operation(self) -> None:
        """Add one validated Tapping operation bound to an explicit hole source."""
        context = self._tree_context()
        if context is None:
            return
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            self._error("tap.geometry_missing: Tapping geometry adapter chưa sẵn sàng.")
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value for job in snapshot.jobs for value in job.setups
            if value.setup_id == setup_id
        )
        generation = self._generation
        try:
            hole_source = self._drilling_pick_provider(setup.wcs.z_axis)
            if not isinstance(hole_source, (HoleReference, HolePattern)):
                raise ValueError("tap.geometry_missing: Hole source không hợp lệ.")
            strategy = _default_tapping_strategy(setup, hole_source)
            resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
            assembly = _find_drilling_assembly(snapshot, ToolFamily.TAP)
            if assembly is None:
                raise ValueError("tap.tool_missing: Chưa có Tool Assembly TAP.")
            tool = next((
                value for value in snapshot.tool_definitions
                if value.tool_id == assembly.tool_id
            ), None)
            machine = next((
                value for value in snapshot.machine_definitions
                if OperationCapability.TAPPING in value.capabilities.operations
            ), None)
            if machine is None:
                raise ValueError(
                    "tap.machine_incompatible: Chưa có máy hỗ trợ Tapping."
                )
            node_id, operation_id = CamNodeId.new(), OperationId.new()
            requirement = MachineRequirement(
                machine.machine_id,
                machine.revision,
                machine.content_fingerprint,
                machine.unit,
                (OperationCapability.TAPPING,),
            )
            operation = Operation(
                operation_id,
                node_id,
                OperationFamily.DRILLING,
                setup_id,
                ToolAssemblyReference.from_assembly(assembly),
                _hole_geometry_inputs((), hole_source),
                strategy.to_operation_parameters(),
                requirement,
            )
            TappingGenerator().resolve_inputs(
                operation,
                setup,
                assembly=assembly,
                tool=tool,
                machine=machine,
                resolved_geometry=resolved,
            )
        except TappingGenerationError as error:
            self._error(f"{error.code.value}: {error}")
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("tap.stale_result: Phiên tạo Tapping đã stale.")
            return
        changed = self._execute(lambda app: app.update_tree(
            job_id,
            setup_id,
            lambda value: value.add_operation(parent_id, "Tapping", operation),
        ))
        if changed:
            self.refresh(("operation", str(node_id)))

    def add_reaming_operation(self) -> None:
        """Add one validated Reaming operation with an explicit pre-hole value."""
        context = self._tree_context()
        if context is None:
            return
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            self._error("ream.geometry_missing: Reaming geometry adapter chưa sẵn sàng.")
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value for job in snapshot.jobs for value in job.setups
            if value.setup_id == setup_id
        )
        generation = self._generation
        try:
            hole_source = self._drilling_pick_provider(setup.wcs.z_axis)
            if not isinstance(hole_source, (HoleReference, HolePattern)):
                raise ValueError("ream.geometry_missing: Hole source không hợp lệ.")
            strategy = _default_reaming_strategy(setup, hole_source)
            resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
            assembly = _find_drilling_assembly(snapshot, ToolFamily.REAMER)
            if assembly is None:
                raise ValueError("ream.tool_missing: Chưa có Tool Assembly REAMER.")
            tool = next((
                value for value in snapshot.tool_definitions
                if value.tool_id == assembly.tool_id
            ), None)
            machine = next((
                value for value in snapshot.machine_definitions
                if OperationCapability.DRILLING in value.capabilities.operations
                and any(
                    spindle.minimum_speed.value
                    <= strategy.spindle_speed.value
                    <= spindle.maximum_speed.value
                    and strategy.spindle_direction in spindle.directions
                    for spindle in value.spindles
                )
            ), None)
            if machine is None:
                raise ValueError(
                    "ream.machine_incompatible: Chưa có máy phay hỗ trợ drilling."
                )
            node_id, operation_id = CamNodeId.new(), OperationId.new()
            requirement = MachineRequirement(
                machine.machine_id,
                machine.revision,
                machine.content_fingerprint,
                machine.unit,
                (OperationCapability.DRILLING,),
            )
            operation = Operation(
                operation_id,
                node_id,
                OperationFamily.DRILLING,
                setup_id,
                ToolAssemblyReference.from_assembly(assembly),
                _hole_geometry_inputs((), hole_source),
                strategy.to_operation_parameters(),
                requirement,
            )
            ReamingGenerator().resolve_inputs(
                operation,
                setup,
                assembly=assembly,
                tool=tool,
                machine=machine,
                resolved_geometry=resolved,
            )
        except ReamingGenerationError as error:
            self._error(f"{error.code.value}: {error}")
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("ream.stale_result: Phiên tạo Reaming đã stale.")
            return
        changed = self._execute(lambda app: app.update_tree(
            job_id,
            setup_id,
            lambda value: value.add_operation(parent_id, "Reaming", operation),
        ))
        if changed:
            self.refresh(("operation", str(node_id)))

    def add_boring_operation(self) -> None:
        """Add one validated Boring operation with an explicit pre-bore value."""
        context = self._tree_context()
        if context is None:
            return
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            self._error("bore.geometry_missing: Boring geometry adapter chưa sẵn sàng.")
            return
        job_id, setup_id, _tree, parent_id = context
        snapshot = self._service.cam_snapshot
        setup = next(
            value for job in snapshot.jobs for value in job.setups
            if value.setup_id == setup_id
        )
        generation = self._generation
        try:
            hole_source = self._drilling_pick_provider(setup.wcs.z_axis)
            if not isinstance(hole_source, (HoleReference, HolePattern)):
                raise ValueError("bore.geometry_missing: Hole source không hợp lệ.")
            strategy = _default_boring_strategy(setup, hole_source)
            resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
            assembly = _find_drilling_assembly(snapshot, ToolFamily.BORING_BAR)
            if assembly is None:
                raise ValueError("bore.tool_missing: Chưa có Tool Assembly BORING_BAR.")
            tool = next((
                value for value in snapshot.tool_definitions
                if value.tool_id == assembly.tool_id
            ), None)
            holder = next((
                value for value in snapshot.holder_definitions
                if value.holder_id == assembly.holder_id
            ), None)
            machine = next((
                value for value in snapshot.machine_definitions
                if value.kind in {MachineKind.MILL, MachineKind.MILL_TURN}
                and OperationCapability.DRILLING in value.capabilities.operations
                and any(
                    spindle.minimum_speed.value
                    <= strategy.spindle_rpm.value
                    <= spindle.maximum_speed.value
                    and strategy.spindle_direction in spindle.directions
                    for spindle in value.spindles
                )
            ), None)
            if machine is None:
                raise ValueError(
                    "bore.machine_incompatible: Chưa có máy phay hỗ trợ drilling."
                )
            node_id, operation_id = CamNodeId.new(), OperationId.new()
            requirement = MachineRequirement(
                machine.machine_id,
                machine.revision,
                machine.content_fingerprint,
                machine.unit,
                (OperationCapability.DRILLING,),
            )
            operation = Operation(
                operation_id,
                node_id,
                OperationFamily.DRILLING,
                setup_id,
                ToolAssemblyReference.from_assembly(assembly),
                _hole_geometry_inputs((), hole_source),
                strategy.to_operation_parameters(),
                requirement,
            )
            BoringGenerator().resolve_inputs(
                operation,
                setup,
                assembly=assembly,
                tool=tool,
                holder=holder,
                machine=machine,
                resolved_geometry=resolved,
            )
        except BoringGenerationError as error:
            self._error(f"{error.code.value}: {error}")
            return
        except (RuntimeError, TypeError, ValueError) as error:
            self._error(str(error))
            return
        if generation is None or generation != self._service.cam_generation:
            self._error("bore.stale_result: Phiên tạo Boring đã stale.")
            return
        changed = self._execute(lambda app: app.update_tree(
            job_id,
            setup_id,
            lambda value: value.add_operation(parent_id, "Boring", operation),
        ))
        if changed:
            self.refresh(("operation", str(node_id)))

    def generate_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.data(0, _KIND_ROLE) != "operation" or self._generation is None:
            self._error("Hãy chọn một operation CAM trước khi Generate.")
            return
        setup = self._find_setup(item, self._find_job(item))
        node = setup.operation_tree.get_node(CamNodeId.parse(item.data(0, _ID_ROLE))) if setup else None
        operation = setup.operation_tree.get_operation(node.operation_id) if setup and node else None
        if operation is None or operation.strategy_key not in {
            "facing_2_5d", "contour_2d", "drilling_v1", "pocket_2_5d",
            "tapping_v1", "reaming_v1", "boring_v1",
        }:
            self._error("Operation đã chọn không hỗ trợ Generate.")
            return
        if operation.strategy_key == "boring_v1":
            generation = self._generation
            draft = self.editor.boring_draft(
                setup.wcs.origin.unit,
                self._picked_hole_source,
            )
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            if (
                draft is None
                or draft.to_operation_parameters() != operation.parameters
                or self.editor.tool.currentData()
                != str(operation.tool_assembly.assembly_id)
                or self.editor.machine.currentData()
                != (str(machine_id) if machine_id else None)
                or self._picked_hole_source is None
                or not _operation_matches_hole_source(
                    operation, self._picked_hole_source,
                )
                or not self._picked_reference_resolved
                or not operation.enabled
            ):
                self._error(
                    "bore.invalid_parameters: Draft Boring chưa hợp lệ "
                    "hoặc chưa được Áp dụng."
                )
                return
            result = self._service.compute_boring(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._drilling_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (
                generation != self._generation
                or generation != service_generation
                or current is None
                or current.operation_id != operation.operation_id
            ):
                self._error(
                    "bore.stale_result: Kết quả Boring đã stale và không "
                    "được hiển thị."
                )
                return
            if result.accepted and result.artifact is not None:
                self.message.emit(
                    "Boring đã Generate và publish artifact hợp lệ."
                )
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (
                    f"{result.diagnostics[0].code.value}: "
                    f"{result.diagnostics[0].message}"
                    if result.diagnostics
                    else "bore.generation_failed: Boring generation thất bại."
                )
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "reaming_v1":
            generation = self._generation
            draft = self.editor.reaming_draft(
                setup.wcs.origin.unit,
                self._picked_hole_source,
            )
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            if (
                draft is None
                or draft.to_operation_parameters() != operation.parameters
                or self.editor.tool.currentData()
                != str(operation.tool_assembly.assembly_id)
                or self.editor.machine.currentData()
                != (str(machine_id) if machine_id else None)
                or self._picked_hole_source is None
                or not _operation_matches_hole_source(
                    operation, self._picked_hole_source,
                )
                or not self._picked_reference_resolved
                or not operation.enabled
            ):
                self._error(
                    "ream.invalid_parameters: Draft Reaming chưa hợp lệ "
                    "hoặc chưa được Áp dụng."
                )
                return
            result = self._service.compute_reaming(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._drilling_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (
                generation != self._generation
                or generation != service_generation
                or current is None
                or current.operation_id != operation.operation_id
            ):
                self._error(
                    "ream.stale_result: Kết quả Reaming đã stale và không "
                    "được hiển thị."
                )
                return
            if result.accepted and result.artifact is not None:
                self.message.emit(
                    "Reaming đã Generate và publish artifact hợp lệ."
                )
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (
                    f"{result.diagnostics[0].code.value}: "
                    f"{result.diagnostics[0].message}"
                    if result.diagnostics
                    else "ream.generation_failed: Reaming generation thất bại."
                )
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "tapping_v1":
            generation = self._generation
            draft = self.editor.tapping_draft(
                setup.wcs.origin.unit,
                self._picked_hole_source,
            )
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            if (
                draft is None
                or draft.to_operation_parameters() != operation.parameters
                or self.editor.tool.currentData()
                != str(operation.tool_assembly.assembly_id)
                or self.editor.machine.currentData()
                != (str(machine_id) if machine_id else None)
                or self._picked_hole_source is None
                or not _operation_matches_hole_source(
                    operation, self._picked_hole_source,
                )
                or not self._picked_reference_resolved
                or not operation.enabled
            ):
                self._error(
                    "tap.invalid_parameters: Draft Tapping chưa hợp lệ "
                    "hoặc chưa được Áp dụng."
                )
                return
            result = self._service.compute_tapping(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._drilling_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (
                generation != self._generation
                or generation != service_generation
                or current is None
                or current.operation_id != operation.operation_id
            ):
                self._error(
                    "tap.stale_result: Kết quả Tapping đã stale và không "
                    "được hiển thị."
                )
                return
            if result.accepted and result.artifact is not None:
                self.message.emit(
                    "Tapping đã Generate và publish artifact hợp lệ."
                )
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (
                    f"{result.diagnostics[0].code.value}: "
                    f"{result.diagnostics[0].message}"
                    if result.diagnostics
                    else "tap.generation_failed: Tapping generation thất bại."
                )
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "drilling_v1":
            generation = self._generation
            draft = self.editor.drilling_draft(
                setup.wcs.origin.unit, self._picked_hole_source,
            )
            machine_id = (
                operation.machine_requirement.machine_id
                if operation.machine_requirement else None
            )
            if (
                draft is None
                or draft.to_operation_parameters() != operation.parameters
                or self.editor.tool.currentData()
                != str(operation.tool_assembly.assembly_id)
                or self.editor.machine.currentData()
                != (str(machine_id) if machine_id else None)
                or self._picked_hole_source is None
                or not _operation_matches_hole_source(
                    operation, self._picked_hole_source,
                )
                or not self._picked_reference_resolved
                or not operation.enabled
            ):
                self._error("Draft Drilling chưa hợp lệ hoặc chưa được Áp dụng.")
                return
            result = self._service.compute_drilling(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._drilling_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (
                generation != self._generation
                or generation != service_generation
                or current is None
                or current.operation_id != operation.operation_id
            ):
                self._error("Kết quả Drilling đã stale và không được hiển thị.")
                return
            if result.accepted and result.artifact is not None:
                self.message.emit("Drilling đã Generate và publish artifact hợp lệ.")
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (
                    result.diagnostics[0].message
                    if result.diagnostics else "Drilling generation thất bại."
                )
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "pocket_2_5d":
            generation = self._generation
            draft = self.editor.pocket_draft(setup.wcs.origin.unit, self._picked_reference)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            if (draft is None or draft.to_operation_parameters() != operation.parameters or
                    self.editor.tool.currentData() != str(operation.tool_assembly.assembly_id) or
                    self.editor.machine.currentData() != (str(machine_id) if machine_id else None) or
                    len(operation.geometry_inputs) != 1 or
                    self._picked_reference != operation.geometry_inputs[0].reference or
                    not self._picked_reference_resolved or not operation.enabled):
                self._error("Draft Pocket chưa hợp lệ hoặc chưa được Áp dụng.")
                return
            result = self._service.compute_pocket(
                operation.operation_id,
                expected_generation=generation,
                geometry_resolver=self._pocket_resolver,
            )
            current = self._selected_operation()
            try:
                service_generation = self._service.cam_generation
            except (ProjectError, RuntimeError):
                service_generation = None
            if (generation != self._generation or generation != service_generation
                    or current is None or current.operation_id != operation.operation_id):
                self._error("Kết quả Pocket đã stale và không được hiển thị.")
                return
            if result.accepted and result.artifact is not None:
                self.message.emit("Pocket 2.5D đã Generate và publish artifact hợp lệ.")
                self.editor.set_error("")
                failure_message = None
            else:
                failure_message = (result.diagnostics[0].message if result.diagnostics
                                   else "Pocket generation thất bại.")
            self.refresh(self._selected_key)
            if failure_message is not None:
                self._error(failure_message)
            return
        if operation.strategy_key == "contour_2d":
            draft = self.editor.contour_draft(setup.wcs.origin.unit)
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            if (draft is None or draft.to_operation_parameters() != operation.parameters or
                    self.editor.tool.currentData() != str(operation.tool_assembly.assembly_id) or
                    self.editor.machine.currentData() != (str(machine_id) if machine_id else None) or
                    len(operation.geometry_inputs) != 1 or
                    self._picked_reference != operation.geometry_inputs[0].reference or
                    not self._picked_reference_resolved or not operation.enabled):
                self._error("Draft 2D Contour chưa hợp lệ hoặc chưa được Áp dụng.")
                return
            result = self._service.compute_contour(
                operation.operation_id, expected_generation=self._generation,
                profile_resolver=self._profile_resolver,
            )
            if result.accepted and result.artifact is not None:
                if self._toolpath_display is not None:
                    self._toolpath_display(result.artifact)
                self.message.emit("2D Contour đã Generate và publish artifact hợp lệ.")
                self.editor.set_error("")
            else:
                self._error(result.diagnostics[0].message if result.diagnostics else "2D Contour generation thất bại.")
            self.refresh(self._selected_key)
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
            visible = not self._toolpath_visibility.get(operation.operation_id, True)
            viewport.set_toolpath_visibility(operation.operation_id, visible)
            self._toolpath_visibility[operation.operation_id] = visible

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
        removed_operation_id: OperationId | None = None
        if kind == "job":
            changed = self._execute(lambda app: app.delete_job(job_id))
        elif kind == "setup":
            changed = self._execute(lambda app: app.delete_setup(job_id, SetupId.parse(item.data(0, _SETUP_ROLE))))
        else:
            setup_id = SetupId.parse(item.data(0, _SETUP_ROLE))
            node_id = CamNodeId.parse(item.data(0, _ID_ROLE))
            if kind == "operation":
                setup = self._find_setup(item, self._find_job(item))
                node = setup.operation_tree.get_node(node_id) if setup is not None else None
                removed_operation_id = node.operation_id if node is not None else None
            changed = self._execute(lambda app: app.update_tree(job_id, setup_id, lambda tree: tree.remove_node(node_id)))
        if changed is not None:
            if removed_operation_id is not None:
                self._service.clear_simulation_result(
                    removed_operation_id,
                    delete_cache=True,
                )
                self._remove_toolpath(removed_operation_id)
                self._toolpath_visibility.pop(removed_operation_id, None)
                if self._displayed_operation_id == removed_operation_id:
                    self._displayed_operation_id = None
            elif self._toolpath_clear is not None:
                self._toolpath_clear()
                self._displayed_operation_id = None
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
                elif current is not None and current.strategy_key == "contour_2d":
                    unit = setup.wcs.origin.unit
                    parameters = self.editor.contour_draft(unit)
                    if parameters is None:
                        raise ValueError("Draft 2D Contour chưa hợp lệ.")
                    snapshot = self._service.cam_snapshot
                    assembly = next(value for value in snapshot.tool_assemblies
                                    if str(value.assembly_id) == values["tool_id"])
                    machine = next(value for value in snapshot.machine_definitions
                                   if str(value.machine_id) == values["machine_id"])
                    requirement = MachineRequirement(machine.machine_id, machine.revision,
                        machine.content_fingerprint, machine.unit, (OperationCapability.MILLING,))
                    expected_kind = (GeometryReferenceKind.FACE
                                     if parameters.profile_source is ContourProfileSource.PLANAR_FACE_OUTER
                                     else GeometryReferenceKind.SKETCH_OR_PROFILE)
                    if self._picked_reference is None or self._picked_reference.kind is not expected_kind:
                        raise ValueError("Hãy Bind profile đúng loại trước khi Áp dụng 2D Contour.")
                    existing = current.geometry_inputs[0] if len(current.geometry_inputs) == 1 else None
                    input_id = (existing.input_id if existing is not None and
                                existing.reference.reference_id == self._picked_reference.reference_id
                                else GeometryInputId.new())
                    geometry_inputs = (OperationGeometryInput(
                        input_id, GeometryInputRole.PROFILE, self._picked_reference,
                        True, expected_kind, 0,
                    ),)
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    inputs_changed = (parameter_set != current.parameters or
                                      tool_reference != current.tool_assembly or
                                      requirement != current.machine_requirement or
                                      geometry_inputs != current.geometry_inputs)
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if inputs_changed or enabled_changed:
                        reason = DirtyReason.PARAMETERS_CHANGED if inputs_changed else DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(current, parameters=parameter_set,
                            tool_assembly=tool_reference, machine_requirement=requirement,
                            geometry_inputs=geometry_inputs, enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason))
                    tree_mutation = lambda tree: tree.rename_node(node_id, str(values["name"])).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "pocket_2_5d":
                    unit = setup.wcs.origin.unit
                    if self._picked_reference is None:
                        raise ValueError("Pocket thiếu profile. Hãy Bind profile trước khi Áp dụng.")
                    if not self._picked_reference_resolved:
                        raise ValueError("Pocket profile đã stale hoặc không còn hợp lệ.")
                    parameters = self.editor.pocket_draft(unit, self._picked_reference)
                    if parameters is None:
                        raise ValueError("Thông số Pocket chưa hợp lệ.")
                    snapshot = self._service.cam_snapshot
                    assembly = next((value for value in snapshot.tool_assemblies
                                     if str(value.assembly_id) == values["tool_id"]), None)
                    if assembly is None:
                        raise ValueError("Pocket thiếu Tool Assembly hợp lệ.")
                    machine = next((value for value in snapshot.machine_definitions
                                    if str(value.machine_id) == values["machine_id"]), None)
                    if machine is None:
                        raise ValueError("Pocket thiếu máy phay hợp lệ.")
                    requirement = MachineRequirement(
                        machine.machine_id, machine.revision, machine.content_fingerprint,
                        machine.unit, (OperationCapability.MILLING,),
                    )
                    existing = current.geometry_inputs[0] if len(current.geometry_inputs) == 1 else None
                    input_id = (existing.input_id if existing is not None and
                                existing.reference.reference_id == self._picked_reference.reference_id
                                else GeometryInputId.new())
                    geometry_inputs = (OperationGeometryInput(
                        input_id, GeometryInputRole.BOUNDARY, self._picked_reference,
                        True, self._picked_reference.kind, 0,
                    ),)
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((parameter_changed, geometry_changed, tool_changed,
                            machine_changed, enabled_changed)):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "boring_v1":
                    unit = setup.wcs.origin.unit
                    if self._picked_hole_source is None:
                        raise ValueError(
                            "bore.geometry_missing: Boring thiếu hole geometry."
                        )
                    parameters = self.editor.boring_draft(
                        unit,
                        self._picked_hole_source,
                    )
                    if parameters is None:
                        raise ValueError(
                            self.editor.boring_draft_error
                            or "bore.invalid_parameters: Thông số Boring chưa hợp lệ."
                        )
                    if self._drilling_resolver is None:
                        raise ValueError(
                            "bore.geometry_missing: Boring resolver chưa sẵn sàng."
                        )
                    resolved = self._drilling_resolver(
                        parameters.geometry,
                        parameters.depth,
                    )
                    snapshot = self._service.cam_snapshot
                    assembly = next((
                        value for value in snapshot.tool_assemblies
                        if str(value.assembly_id) == values["tool_id"]
                    ), None)
                    if assembly is None:
                        raise ValueError(
                            "bore.tool_missing: Boring thiếu Tool Assembly."
                        )
                    tool = next((
                        value for value in snapshot.tool_definitions
                        if value.tool_id == assembly.tool_id
                    ), None)
                    holder = next((
                        value for value in snapshot.holder_definitions
                        if value.holder_id == assembly.holder_id
                    ), None)
                    machine = next((
                        value for value in snapshot.machine_definitions
                        if str(value.machine_id) == values["machine_id"]
                    ), None)
                    if machine is None:
                        raise ValueError(
                            "bore.machine_incompatible: Boring thiếu Machine."
                        )
                    requirement = MachineRequirement(
                        machine.machine_id,
                        machine.revision,
                        machine.content_fingerprint,
                        machine.unit,
                        (OperationCapability.DRILLING,),
                    )
                    geometry_inputs = _hole_geometry_inputs(
                        current.geometry_inputs,
                        self._picked_hole_source,
                    )
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    validation_operation = replace(
                        current,
                        parameters=parameter_set,
                        tool_assembly=tool_reference,
                        machine_requirement=requirement,
                        geometry_inputs=geometry_inputs,
                        enabled=True,
                    )
                    try:
                        BoringGenerator().resolve_inputs(
                            validation_operation,
                            setup,
                            assembly=assembly,
                            tool=tool,
                            holder=holder,
                            machine=machine,
                            resolved_geometry=resolved,
                        )
                    except BoringGenerationError as error:
                        raise ValueError(
                            f"{error.code.value}: {error}"
                        ) from error
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((
                        parameter_changed,
                        geometry_changed,
                        tool_changed,
                        machine_changed,
                        enabled_changed,
                    )):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "reaming_v1":
                    unit = setup.wcs.origin.unit
                    if self._picked_hole_source is None:
                        raise ValueError(
                            "ream.geometry_missing: Reaming thiếu hole geometry."
                        )
                    parameters = self.editor.reaming_draft(
                        unit,
                        self._picked_hole_source,
                    )
                    if parameters is None:
                        raise ValueError(
                            self.editor.reaming_draft_error
                            or "ream.invalid_parameters: Thông số Reaming chưa hợp lệ."
                        )
                    if self._drilling_resolver is None:
                        raise ValueError(
                            "ream.geometry_missing: Reaming resolver chưa sẵn sàng."
                        )
                    resolved = self._drilling_resolver(
                        parameters.geometry,
                        parameters.depth,
                    )
                    snapshot = self._service.cam_snapshot
                    assembly = next((
                        value for value in snapshot.tool_assemblies
                        if str(value.assembly_id) == values["tool_id"]
                    ), None)
                    if assembly is None:
                        raise ValueError(
                            "ream.tool_missing: Reaming thiếu Tool Assembly."
                        )
                    tool = next((
                        value for value in snapshot.tool_definitions
                        if value.tool_id == assembly.tool_id
                    ), None)
                    machine = next((
                        value for value in snapshot.machine_definitions
                        if str(value.machine_id) == values["machine_id"]
                    ), None)
                    if machine is None:
                        raise ValueError(
                            "ream.machine_incompatible: Reaming thiếu Machine."
                        )
                    requirement = MachineRequirement(
                        machine.machine_id,
                        machine.revision,
                        machine.content_fingerprint,
                        machine.unit,
                        (OperationCapability.DRILLING,),
                    )
                    geometry_inputs = _hole_geometry_inputs(
                        current.geometry_inputs,
                        self._picked_hole_source,
                    )
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    validation_operation = replace(
                        current,
                        parameters=parameter_set,
                        tool_assembly=tool_reference,
                        machine_requirement=requirement,
                        geometry_inputs=geometry_inputs,
                        enabled=True,
                    )
                    try:
                        ReamingGenerator().resolve_inputs(
                            validation_operation,
                            setup,
                            assembly=assembly,
                            tool=tool,
                            machine=machine,
                            resolved_geometry=resolved,
                        )
                    except ReamingGenerationError as error:
                        raise ValueError(
                            f"{error.code.value}: {error}"
                        ) from error
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((
                        parameter_changed,
                        geometry_changed,
                        tool_changed,
                        machine_changed,
                        enabled_changed,
                    )):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "tapping_v1":
                    unit = setup.wcs.origin.unit
                    if self._picked_hole_source is None:
                        raise ValueError(
                            "tap.geometry_missing: Tapping thiếu hole geometry."
                        )
                    parameters = self.editor.tapping_draft(
                        unit,
                        self._picked_hole_source,
                    )
                    if parameters is None:
                        raise ValueError(
                            self.editor.tapping_draft_error
                            or "tap.invalid_parameters: Thông số Tapping chưa hợp lệ."
                        )
                    if self._drilling_resolver is None:
                        raise ValueError(
                            "tap.geometry_missing: Tapping resolver chưa sẵn sàng."
                        )
                    resolved = self._drilling_resolver(
                        parameters.geometry,
                        parameters.depth,
                    )
                    snapshot = self._service.cam_snapshot
                    assembly = next((
                        value for value in snapshot.tool_assemblies
                        if str(value.assembly_id) == values["tool_id"]
                    ), None)
                    if assembly is None:
                        raise ValueError(
                            "tap.tool_missing: Tapping thiếu Tool Assembly."
                        )
                    tool = next((
                        value for value in snapshot.tool_definitions
                        if value.tool_id == assembly.tool_id
                    ), None)
                    machine = next((
                        value for value in snapshot.machine_definitions
                        if str(value.machine_id) == values["machine_id"]
                    ), None)
                    if machine is None:
                        raise ValueError(
                            "tap.machine_incompatible: Tapping thiếu Machine."
                        )
                    requirement = MachineRequirement(
                        machine.machine_id,
                        machine.revision,
                        machine.content_fingerprint,
                        machine.unit,
                        (OperationCapability.TAPPING,),
                    )
                    geometry_inputs = _hole_geometry_inputs(
                        current.geometry_inputs,
                        self._picked_hole_source,
                    )
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    validation_operation = replace(
                        current,
                        parameters=parameter_set,
                        tool_assembly=tool_reference,
                        machine_requirement=requirement,
                        geometry_inputs=geometry_inputs,
                        enabled=True,
                    )
                    try:
                        TappingGenerator().resolve_inputs(
                            validation_operation,
                            setup,
                            assembly=assembly,
                            tool=tool,
                            machine=machine,
                            resolved_geometry=resolved,
                        )
                    except TappingGenerationError as error:
                        raise ValueError(
                            f"{error.code.value}: {error}"
                        ) from error
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((
                        parameter_changed,
                        geometry_changed,
                        tool_changed,
                        machine_changed,
                        enabled_changed,
                    )):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                elif current is not None and current.strategy_key == "drilling_v1":
                    unit = setup.wcs.origin.unit
                    if self._picked_hole_source is None:
                        raise ValueError(
                            "Drilling thiếu hole geometry. Hãy Bind trước khi Áp dụng."
                        )
                    parameters = self.editor.drilling_draft(
                        unit, self._picked_hole_source,
                    )
                    if parameters is None:
                        raise ValueError("Thông số Drilling chưa hợp lệ.")
                    if self._drilling_resolver is None:
                        raise ValueError("Drilling resolver chưa sẵn sàng.")
                    resolved = self._drilling_resolver(
                        parameters.geometry, parameters.depth,
                    )
                    if resolved.status is not GeometryResolutionStatus.RESOLVED:
                        raise ValueError(
                            resolved.diagnostics[0].message
                            if resolved.diagnostics
                            else "Drilling geometry không còn hợp lệ."
                        )
                    snapshot = self._service.cam_snapshot
                    assembly = next((
                        value for value in snapshot.tool_assemblies
                        if str(value.assembly_id) == values["tool_id"]
                    ), None)
                    if assembly is None:
                        raise ValueError("Drilling thiếu Tool Assembly hợp lệ.")
                    tool = next((
                        value for value in snapshot.tool_definitions
                        if value.tool_id == assembly.tool_id
                    ), None)
                    expected_family = (
                        ToolFamily.CENTER_DRILL
                        if parameters.cycle is DrillingCycle.SPOT_DRILL
                        else ToolFamily.DRILL
                    )
                    if tool is None or tool.family is not expected_family:
                        raise ValueError(
                            f"{parameters.cycle.value} yêu cầu tool {expected_family.value}."
                        )
                    machine = next((
                        value for value in snapshot.machine_definitions
                        if str(value.machine_id) == values["machine_id"]
                    ), None)
                    if (
                        machine is None
                        or OperationCapability.DRILLING
                        not in machine.capabilities.operations
                    ):
                        raise ValueError("Drilling thiếu máy có capability DRILLING.")
                    requirement = MachineRequirement(
                        machine.machine_id, machine.revision,
                        machine.content_fingerprint, machine.unit,
                        (OperationCapability.DRILLING,),
                    )
                    geometry_inputs = _hole_geometry_inputs(
                        current.geometry_inputs,
                        self._picked_hole_source,
                    )
                    parameter_set = parameters.to_operation_parameters()
                    tool_reference = ToolAssemblyReference.from_assembly(assembly)
                    enabled = bool(values["enabled"])
                    parameter_changed = parameter_set != current.parameters
                    geometry_changed = geometry_inputs != current.geometry_inputs
                    tool_changed = tool_reference != current.tool_assembly
                    machine_changed = requirement != current.machine_requirement
                    enabled_changed = enabled != current.enabled
                    changed_operation = current
                    if any((parameter_changed, geometry_changed, tool_changed,
                            machine_changed, enabled_changed)):
                        if geometry_changed:
                            reason = DirtyReason.GEOMETRY_CHANGED
                        elif tool_changed:
                            reason = DirtyReason.TOOL_CHANGED
                        elif machine_changed:
                            reason = DirtyReason.MACHINE_CHANGED
                        elif parameter_changed:
                            reason = DirtyReason.PARAMETERS_CHANGED
                        else:
                            reason = DirtyReason.UPSTREAM_CHANGED
                        changed_operation = replace(
                            current,
                            parameters=parameter_set,
                            tool_assembly=tool_reference,
                            machine_requirement=requirement,
                            geometry_inputs=geometry_inputs,
                            enabled=enabled,
                            revision=current.revision.next(),
                            artifact_state=current.artifact_state.mark_dirty(reason),
                        )
                    tree_mutation = lambda tree: tree.rename_node(
                        node_id, str(values["name"])
                    ).replace_operation(changed_operation)
                else:
                    tree_mutation = lambda tree: tree.rename_node(node_id, str(values["name"])).set_enabled(node_id, bool(values["enabled"]))
                result = self._execute(lambda app: app.update_tree(job.job_id, setup.setup_id, tree_mutation))
            else:
                result = None
            if result is not None:
                self.editor.set_error("")
                self.refresh(self._selected_key)
        except (RuntimeError, TypeError, ValueError) as error:
            message = str(error)
            if self._active_editor_strategy_key in {"reaming_v1", "boring_v1"}:
                strategy_key = self._active_editor_strategy_key
                operation_id = self._active_editor_operation_id
                self._active_editor_operation_id = None
                if operation_id is not None:
                    drafts = (
                        self._reaming_drafts
                        if strategy_key == "reaming_v1"
                        else self._boring_drafts
                    )
                    drafts.pop(operation_id, None)
                self.refresh(self._selected_key)
            self.editor.set_error(message)

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
            if self._active_editor_strategy_key in {
                "tapping_v1", "reaming_v1", "boring_v1",
            }:
                strategy_key = self._active_editor_strategy_key
                operation_id = self._active_editor_operation_id
                self._active_editor_operation_id = None
                if operation_id is not None:
                    drafts = (
                        self._tapping_drafts
                        if strategy_key == "tapping_v1"
                        else self._reaming_drafts
                        if strategy_key == "reaming_v1"
                        else self._boring_drafts
                    )
                    drafts.pop(operation_id, None)
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
            machine_id = operation.machine_requirement.machine_id if operation.machine_requirement else None
            if operation.strategy_key == "contour_2d":
                draft = self.editor.contour_draft(setup.wcs.origin.unit)
                return bool(operation.enabled and draft is not None and
                    draft.to_operation_parameters() == operation.parameters and
                    len(operation.geometry_inputs) == 1 and
                    self._picked_reference == operation.geometry_inputs[0].reference and
                    self._picked_reference_resolved and
                    self.editor.tool.currentData() == str(operation.tool_assembly.assembly_id) and
                    self.editor.machine.currentData() == (str(machine_id) if machine_id else None))
            if operation.strategy_key == "pocket_2_5d":
                draft = self.editor.pocket_draft(
                    setup.wcs.origin.unit, self._picked_reference,
                )
                return bool(operation.enabled and draft is not None and
                    draft.to_operation_parameters() == operation.parameters and
                    len(operation.geometry_inputs) == 1 and
                    self._picked_reference == operation.geometry_inputs[0].reference and
                    self._picked_reference_resolved and
                    self.editor.tool.currentData() == str(operation.tool_assembly.assembly_id) and
                    self.editor.machine.currentData() == (str(machine_id) if machine_id else None))
            if operation.strategy_key == "drilling_v1":
                draft = self.editor.drilling_draft(
                    setup.wcs.origin.unit, self._picked_hole_source,
                )
                return bool(
                    operation.enabled
                    and draft is not None
                    and draft.to_operation_parameters() == operation.parameters
                    and self._picked_hole_source is not None
                    and _operation_matches_hole_source(
                        operation, self._picked_hole_source,
                    )
                    and self._picked_reference_resolved
                    and self.editor.tool.currentData()
                    == str(operation.tool_assembly.assembly_id)
                    and self.editor.machine.currentData()
                    == (str(machine_id) if machine_id else None)
                )
            if operation.strategy_key == "tapping_v1":
                draft = self.editor.tapping_draft(
                    setup.wcs.origin.unit,
                    self._picked_hole_source,
                )
                return bool(
                    operation.enabled
                    and draft is not None
                    and draft.to_operation_parameters() == operation.parameters
                    and self._picked_hole_source is not None
                    and _operation_matches_hole_source(
                        operation, self._picked_hole_source,
                    )
                    and self._picked_reference_resolved
                    and self.editor.tool.currentData()
                    == str(operation.tool_assembly.assembly_id)
                    and self.editor.machine.currentData()
                    == (str(machine_id) if machine_id else None)
                )
            if operation.strategy_key == "reaming_v1":
                draft = self.editor.reaming_draft(
                    setup.wcs.origin.unit,
                    self._picked_hole_source,
                )
                return bool(
                    operation.enabled
                    and draft is not None
                    and draft.to_operation_parameters() == operation.parameters
                    and self._picked_hole_source is not None
                    and _operation_matches_hole_source(
                        operation, self._picked_hole_source,
                    )
                    and self._picked_reference_resolved
                    and self.editor.tool.currentData()
                    == str(operation.tool_assembly.assembly_id)
                    and self.editor.machine.currentData()
                    == (str(machine_id) if machine_id else None)
                )
            if operation.strategy_key == "boring_v1":
                draft = self.editor.boring_draft(
                    setup.wcs.origin.unit,
                    self._picked_hole_source,
                )
                return bool(
                    operation.enabled
                    and draft is not None
                    and draft.to_operation_parameters() == operation.parameters
                    and self._picked_hole_source is not None
                    and _operation_matches_hole_source(
                        operation, self._picked_hole_source,
                    )
                    and self._picked_reference_resolved
                    and self.editor.tool.currentData()
                    == str(operation.tool_assembly.assembly_id)
                    and self.editor.machine.currentData()
                    == (str(machine_id) if machine_id else None)
                )
            draft = self.editor.facing_draft(setup.wcs.origin.unit)
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
        self._tapping_draft_error = ""
        self._reaming_draft_error = ""
        self._boring_draft_error = ""
        self._tool_definitions_by_id = {}
        self._holders_by_id = {}
        self._assemblies_by_id = {}
        self._fields = {key: QLineEdit() for key in ("name", "offset", "x", "y", "z", "a", "b", "c")}
        self._facing_fields = {key: QLineEdit() for key in (
            "top", "target", "stepdown", "stepover", "allowance", "clearance", "retract",
            "feed", "plunge", "spindle", "angle", "overtravel")}
        self._contour_fields = {key: QLineEdit() for key in (
            "top", "final", "stepdown", "radial", "axial", "clearance", "retract",
            "feed", "plunge", "spindle", "lead")}
        self._pocket_fields = {key: QLineEdit() for key in (
            "top", "bottom", "stepdown", "stepover", "allowance", "axial", "clearance",
            "retract", "feed", "plunge", "spindle", "tolerance")}
        self._drilling_fields = {key: QLineEdit() for key in (
            "top", "depth", "peck", "clearance", "retract", "feed",
            "spindle", "dwell", "tolerance",
        )}
        self._tapping_fields = {key: QLineEdit() for key in (
            "top", "final", "clearance", "retract", "diameter", "pitch",
            "spindle", "dwell", "tolerance",
        )}
        self._reaming_fields = {key: QLineEdit() for key in (
            "top", "final", "clearance", "retract", "diameter", "pre_hole",
            "spindle", "feed_per_revolution", "dwell", "tolerance",
        )}
        self._boring_fields = {key: QLineEdit() for key in (
            "top", "final", "clearance", "retract", "finished_diameter",
            "pre_bore", "spindle", "feed_per_revolution", "dwell",
            "tolerance",
        )}
        self.boundary_source = QComboBox(); self.boundary_source.addItems([item.value for item in FacingBoundarySource])
        self.direction = QComboBox(); self.direction.addItems([item.value for item in FacingCutDirection])
        self.profile_source = QComboBox(); self.profile_source.addItems([item.value for item in ContourProfileSource])
        self.contour_side = QComboBox(); self.contour_side.addItems([item.value for item in ContourSide])
        self.contour_direction = QComboBox(); self.contour_direction.addItems([item.value for item in ContourCutDirection])
        self.pocket_entry = QComboBox(); self.pocket_entry.addItems([item.value for item in PocketEntryPolicy])
        self.pocket_direction = QComboBox(); self.pocket_direction.addItems([item.value for item in PocketCuttingDirection])
        self.drilling_cycle = QComboBox(); self.drilling_cycle.addItems([item.value for item in DrillingCycle])
        self.drilling_retract = QComboBox(); self.drilling_retract.addItems([item.value for item in DrillRetractPolicy])
        self.tapping_hand = QComboBox(); self.tapping_hand.addItems([item.value for item in TappingHand])
        self.tapping_mode = QComboBox(); self.tapping_mode.addItems([item.value for item in TappingSynchronizationPolicy])
        self.reaming_spindle_direction = QComboBox(); self.reaming_spindle_direction.addItems([item.value for item in SpindleDirection])
        self.reaming_retract_policy = QComboBox(); self.reaming_retract_policy.addItems([item.value for item in ReamingRetractPolicy])
        self.reaming_coolant = QComboBox(); self.reaming_coolant.addItems([item.value for item in ReamingCoolantMode])
        self.reaming_derived = QLabel("—")
        self.reaming_derived.setWordWrap(True)
        self.boring_spindle_direction = QComboBox(); self.boring_spindle_direction.addItems([item.value for item in SpindleDirection])
        self.boring_retract_policy = QComboBox(); self.boring_retract_policy.addItems([item.value for item in BoringRetractPolicy])
        self.boring_coolant = QComboBox(); self.boring_coolant.addItems([item.value for item in BoringCoolantMode])
        self.boring_derived = QLabel("—")
        self.boring_derived.setWordWrap(True)
        self.boring_tool_details = QLabel("—")
        self.boring_tool_details.setWordWrap(True)
        self.finishing_pass = QCheckBox("Finishing pass")
        self.multiple_depth_passes = QCheckBox("Nhiều lớp chiều sâu"); self.multiple_depth_passes.setChecked(True)
        self.tool = QComboBox(); self.machine = QComboBox()
        self.setup_kind = QComboBox(); self.setup_kind.addItems([item.value for item in SetupKind])
        self.stock_kind = QComboBox(); self.stock_kind.addItems([item.value for item in StockKind])
        self.enabled = QCheckBox("Được bật")
        self.status = QLabel("—")
        self.toolpath_metadata = QLabel("—")
        self.toolpath_metadata.setWordWrap(True)
        self.error = QLabel(); self.error.setStyleSheet("color: #9b241b")
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
        form.addRow("Nguồn profile", self.profile_source); form.addRow("Side", self.contour_side)
        for label, key in (("Contour Top Z", "top"), ("Final depth Z", "final"),
                           ("Contour stepdown", "stepdown"), ("Radial allowance", "radial"),
                           ("Axial allowance", "axial"), ("Contour clearance Z", "clearance"),
                           ("Contour retract Z", "retract"), ("Contour feed", "feed"),
                           ("Contour plunge", "plunge"), ("Contour spindle", "spindle"),
                           ("Linear lead length", "lead")):
            form.addRow(label, self._contour_fields[key])
        form.addRow("Contour direction", self.contour_direction)
        form.addRow("", self.multiple_depth_passes); form.addRow("", self.finishing_pass)
        for label, key in (("Pocket Top Z", "top"), ("Pocket Bottom Z", "bottom"),
                           ("Pocket stepdown", "stepdown"), ("Pocket stepover", "stepover"),
                           ("Pocket radial allowance", "allowance"),
                           ("Pocket floor allowance", "axial"),
                           ("Pocket clearance Z", "clearance"),
                           ("Pocket retract Z", "retract"), ("Pocket feed", "feed"),
                           ("Pocket plunge", "plunge"), ("Pocket spindle", "spindle"),
                           ("Pocket tolerance", "tolerance")):
            form.addRow(label, self._pocket_fields[key])
        form.addRow("Pocket entry", self.pocket_entry)
        form.addRow("Pocket direction", self.pocket_direction)
        form.addRow("Drilling cycle", self.drilling_cycle)
        for label, key in (
            ("Drilling Top Z", "top"), ("Drilling depth", "depth"),
            ("Peck depth", "peck"), ("Drilling clearance Z", "clearance"),
            ("Drilling retract Z", "retract"), ("Drilling feed", "feed"),
            ("Drilling spindle", "spindle"), ("Drilling dwell (s)", "dwell"),
            ("Drilling tolerance", "tolerance"),
        ):
            form.addRow(label, self._drilling_fields[key])
        form.addRow("Peck retract", self.drilling_retract)
        form.addRow("Tapping hand", self.tapping_hand)
        form.addRow("Tapping mode", self.tapping_mode)
        for label, key in (
            ("Tapping Top Z", "top"),
            ("Tapping final depth Z", "final"),
            ("Tapping clearance Z", "clearance"),
            ("Tapping retract Z", "retract"),
            ("Tap nominal diameter", "diameter"),
            ("Tap pitch", "pitch"),
            ("Tapping spindle RPM", "spindle"),
            ("Tapping dwell (s, optional)", "dwell"),
            ("Tapping tolerance", "tolerance"),
        ):
            form.addRow(label, self._tapping_fields[key])
        form.addRow("Reaming spindle direction", self.reaming_spindle_direction)
        form.addRow("Reaming retract policy", self.reaming_retract_policy)
        form.addRow("Reaming coolant", self.reaming_coolant)
        for label, key in (
            ("Reaming Top Z", "top"),
            ("Reaming final depth Z", "final"),
            ("Reaming clearance Z", "clearance"),
            ("Reaming retract Z", "retract"),
            ("Finished nominal diameter", "diameter"),
            ("Pre-hole diameter (required)", "pre_hole"),
            ("Reaming spindle RPM", "spindle"),
            ("Feed per revolution", "feed_per_revolution"),
            ("Reaming dwell (s, optional)", "dwell"),
            ("Reaming tolerance", "tolerance"),
        ):
            form.addRow(label, self._reaming_fields[key])
        form.addRow("Derived (read-only)", self.reaming_derived)
        form.addRow("Boring spindle direction", self.boring_spindle_direction)
        form.addRow("Boring retract policy", self.boring_retract_policy)
        form.addRow("Boring coolant", self.boring_coolant)
        for label, key in (
            ("Boring Top Z", "top"),
            ("Boring final depth Z", "final"),
            ("Boring clearance Z", "clearance"),
            ("Boring retract Z", "retract"),
            ("Finished bore diameter", "finished_diameter"),
            ("Pre-bore diameter (required)", "pre_bore"),
            ("Boring spindle RPM", "spindle"),
            ("Boring feed per revolution", "feed_per_revolution"),
            ("Boring dwell (s, optional)", "dwell"),
            ("Boring tolerance", "tolerance"),
        ):
            form.addRow(label, self._boring_fields[key])
        form.addRow("Boring derived (read-only)", self.boring_derived)
        form.addRow("BORING_BAR current", self.boring_tool_details)
        form.addRow("Trạng thái", self.status); form.addRow("Toolpath", self.toolpath_metadata)
        form.addRow("", self.enabled); form.addRow("Lỗi", self.error)
        self.apply_button = QPushButton("Áp dụng")
        self.apply_button.setObjectName("ClassicCamApplyButton")
        self.apply_button.setAccessibleName("Áp dụng bản nháp CAM")
        self.apply_button.clicked.connect(self._submit)
        form.addRow(self.apply_button)
        for field in self._facing_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._contour_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._pocket_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._drilling_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._tapping_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
        for field in self._reaming_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
            field.textChanged.connect(self._update_reaming_preview)
        for field in self._boring_fields.values():
            field.textChanged.connect(lambda _text: self.draft_changed.emit())
            field.textChanged.connect(self._update_boring_preview)
        for combo in (self.boundary_source, self.direction, self.profile_source, self.contour_side,
                      self.contour_direction, self.pocket_entry, self.pocket_direction,
                      self.drilling_cycle, self.drilling_retract,
                      self.tapping_hand, self.tapping_mode,
                      self.reaming_spindle_direction, self.reaming_retract_policy,
                      self.reaming_coolant, self.boring_spindle_direction,
                      self.boring_retract_policy, self.boring_coolant,
                      self.tool, self.machine):
            combo.currentIndexChanged.connect(lambda _index: self.draft_changed.emit())
        self.tool.currentIndexChanged.connect(self._update_boring_tool_details)
        self.finishing_pass.toggled.connect(lambda _checked: self.draft_changed.emit())
        self.multiple_depth_passes.toggled.connect(lambda _checked: self.draft_changed.emit())

    def clear(self) -> None:
        for field in self._fields.values(): field.clear()
        for field in self._facing_fields.values(): field.clear()
        for field in self._contour_fields.values(): field.clear()
        for field in self._pocket_fields.values(): field.clear()
        for field in self._drilling_fields.values(): field.clear()
        for field in self._tapping_fields.values(): field.clear()
        for field in self._reaming_fields.values(): field.clear()
        for field in self._boring_fields.values(): field.clear()
        self.reaming_derived.setText("—")
        self.boring_derived.setText("—")
        self.boring_tool_details.setText("—")
        self.tool.clear(); self.machine.clear()
        self._tool_definitions_by_id = {}
        self._holders_by_id = {}
        self._assemblies_by_id = {}
        self.boring_tool_details.setText("—")
        self.status.setText("—"); self.toolpath_metadata.setText("—"); self.error.clear()

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

    def show_node(
        self,
        name: str,
        operation: Operation | None,
        assemblies=(),
        machines=(),
        tools=(),
        holders=(),
    ) -> None:
        self.clear(); self._fields["name"].setText(name); self.enabled.setChecked(True if operation is None else operation.enabled)
        self.status.setText("GROUP" if operation is None else operation.artifact_state.status.value.upper())
        self.tool.clear(); self.machine.clear()
        self._tool_definitions_by_id = {value.tool_id: value for value in tools}
        self._holders_by_id = {value.holder_id: value for value in holders}
        self._assemblies_by_id = {
            str(value.assembly_id): value for value in assemblies
        }
        visible_assemblies = assemblies
        if operation is not None and operation.strategy_key == "boring_v1":
            boring_tool_ids = {
                value.tool_id for value in tools
                if value.family is ToolFamily.BORING_BAR
            }
            visible_assemblies = tuple(
                value for value in assemblies if value.tool_id in boring_tool_ids
            )
        for value in visible_assemblies: self.tool.addItem(value.name, str(value.assembly_id))
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
        elif operation is not None and operation.strategy_key == "contour_2d":
            parameters = ContourParameters.from_operation_parameters(operation.parameters)
            values = {"top": parameters.top_height.value, "final": parameters.final_depth.value,
                      "stepdown": parameters.stepdown.value, "radial": parameters.radial_stock_allowance.value,
                      "axial": parameters.axial_stock_allowance.value,
                      "clearance": parameters.clearance_height.value, "retract": parameters.retract_height.value,
                      "feed": parameters.cutting_feed_rate.value, "plunge": parameters.plunge_feed_rate.value,
                      "spindle": parameters.spindle_speed.value, "lead": parameters.lead_length.value}
            for key, value in values.items(): self._contour_fields[key].setText(str(value))
            self.profile_source.setCurrentText(parameters.profile_source.value)
            self.contour_side.setCurrentText(parameters.side.value)
            self.contour_direction.setCurrentText(parameters.direction.value)
            self.finishing_pass.setChecked(parameters.finishing_pass)
            self.multiple_depth_passes.setChecked(parameters.multiple_depth_passes)
            self.tool.setCurrentIndex(self.tool.findData(str(operation.tool_assembly.assembly_id)))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(str(operation.machine_requirement.machine_id)))
        elif operation is not None and operation.strategy_key == "pocket_2_5d":
            values = dict(operation.parameters.values)
            mapping = {
                "top": "top_z", "bottom": "bottom_z", "stepdown": "stepdown",
                "stepover": "stepover", "allowance": "radial_stock_allowance",
                "axial": "axial_allowance",
                "clearance": "clearance_height", "retract": "retract_height",
                "feed": "cutting_feed_rate", "plunge": "plunge_feed_rate",
                "spindle": "spindle_speed", "tolerance": "tolerance",
            }
            for field, parameter in mapping.items():
                self._pocket_fields[field].setText(str(values.get(parameter, "")))
            self.pocket_entry.setCurrentText(str(values.get("entry_policy", "")))
            self.pocket_direction.setCurrentText(str(values.get("cutting_direction", "")))
            self.tool.setCurrentIndex(self.tool.findData(str(operation.tool_assembly.assembly_id)))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(str(operation.machine_requirement.machine_id)))
        elif operation is not None and operation.strategy_key == "drilling_v1":
            parameters = DrillingStrategy.from_operation_parameters(operation.parameters)
            values = {
                "top": parameters.top_z.value,
                "depth": parameters.depth.depth.value,
                "peck": (
                    "" if parameters.peck_depth is None
                    else parameters.peck_depth.value
                ),
                "clearance": parameters.clearance_height.value,
                "retract": parameters.retract_height.value,
                "feed": parameters.feed_rate.value,
                "spindle": parameters.spindle_speed.value,
                "dwell": parameters.dwell_seconds,
                "tolerance": parameters.tolerance.value,
            }
            for key, value in values.items():
                self._drilling_fields[key].setText(str(value))
            self.drilling_cycle.setCurrentText(parameters.cycle.value)
            self.drilling_retract.setCurrentText(parameters.retract_policy.value)
            self.tool.setCurrentIndex(
                self.tool.findData(str(operation.tool_assembly.assembly_id))
            )
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(
                    str(operation.machine_requirement.machine_id)
                ))
        elif operation is not None and operation.strategy_key == "tapping_v1":
            parameters = TappingStrategy.from_operation_parameters(
                operation.parameters
            )
            values = {
                "top": parameters.top_z.value,
                "final": parameters.final_depth.value,
                "clearance": parameters.clearance_height.value,
                "retract": parameters.retract_height.value,
                "diameter": parameters.nominal_diameter.value,
                "pitch": parameters.pitch.value,
                "spindle": parameters.spindle_speed.value,
                "dwell": parameters.dwell_seconds,
                "tolerance": parameters.tolerance.value,
            }
            for key, value in values.items():
                self._tapping_fields[key].setText(str(value))
            self.tapping_hand.setCurrentText(parameters.hand.value)
            self.tapping_mode.setCurrentText(
                parameters.synchronization_policy.value
            )
            self.tool.setCurrentIndex(self.tool.findData(
                str(operation.tool_assembly.assembly_id)
            ))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(
                    str(operation.machine_requirement.machine_id)
                ))
        elif operation is not None and operation.strategy_key == "reaming_v1":
            parameters = ReamingStrategy.from_operation_parameters(
                operation.parameters
            )
            values = {
                "top": parameters.top_z.value,
                "final": parameters.final_depth.value,
                "clearance": parameters.clearance_height.value,
                "retract": parameters.retract_height.value,
                "diameter": parameters.nominal_diameter.value,
                "pre_hole": parameters.pre_hole_diameter.value,
                "spindle": parameters.spindle_speed.value,
                "feed_per_revolution": parameters.feed_per_revolution.value,
                "dwell": parameters.dwell_seconds,
                "tolerance": parameters.tolerance.value,
            }
            for key, value in values.items():
                self._reaming_fields[key].setText(str(value))
            self.reaming_spindle_direction.setCurrentText(
                parameters.spindle_direction.value
            )
            self.reaming_retract_policy.setCurrentText(
                parameters.retract_policy.value
            )
            self.reaming_coolant.setCurrentText(parameters.coolant.value)
            self.tool.setCurrentIndex(self.tool.findData(
                str(operation.tool_assembly.assembly_id)
            ))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(
                    str(operation.machine_requirement.machine_id)
                ))
            self._update_reaming_preview()
        elif operation is not None and operation.strategy_key == "boring_v1":
            parameters = BoringStrategy.from_operation_parameters(
                operation.parameters
            )
            values = {
                "top": parameters.top_z.value,
                "final": parameters.final_depth.value,
                "clearance": parameters.clearance_height.value,
                "retract": parameters.retract_height.value,
                "finished_diameter": parameters.finished_bore_diameter.value,
                "pre_bore": parameters.pre_bore_diameter.value,
                "spindle": parameters.spindle_rpm.value,
                "feed_per_revolution": parameters.feed_per_revolution.value,
                "dwell": parameters.dwell_seconds,
                "tolerance": parameters.tolerance.value,
            }
            for key, value in values.items():
                self._boring_fields[key].setText(str(value))
            self.boring_spindle_direction.setCurrentText(
                parameters.spindle_direction.value
            )
            self.boring_retract_policy.setCurrentText(
                parameters.retract_policy.value
            )
            self.boring_coolant.setCurrentText(parameters.coolant.value)
            self.tool.setCurrentIndex(self.tool.findData(
                str(operation.tool_assembly.assembly_id)
            ))
            if operation.machine_requirement:
                self.machine.setCurrentIndex(self.machine.findData(
                    str(operation.machine_requirement.machine_id)
                ))
            self._update_boring_preview()
            self._update_boring_tool_details()

    def set_error(self, text: str) -> None: self.error.setText(text)

    def apply_draft(self) -> None:
        """Apply the current draft through the existing validated commit path."""
        self._submit()

    def show_toolpath_metadata(self, value: ToolpathPresentation) -> None:
        """Show native-free artifact metadata; never expose runtime or NC data."""
        bounds = value.bounds
        minimum, maximum = bounds.minimum, bounds.maximum
        tapping = ""
        if value.strategy_key == "tapping_v1":
            diameter = (
                "?" if value.nominal_diameter is None
                else f"{value.nominal_diameter.value:g}"
            )
            pitch = "?" if value.pitch is None else f"{value.pitch.value:g}"
            rpm = (
                "?" if value.spindle_speed is None
                else f"{value.spindle_speed.value:g}"
            )
            depth = "?" if value.depth is None else f"{value.depth.value:g}"
            tapping = (
                f" · {value.thread_hand.value if value.thread_hand else '?'}"
                f" · {value.tapping_mode.value if value.tapping_mode else '?'}"
                f" · {value.hole_count} hole"
                f" · D{diameter} · P{pitch} · {rpm} RPM · depth {depth}"
            )
        if value.strategy_key == "reaming_v1":
            def number(item) -> str:
                return "?" if item is None else f"{item.value:g}"

            tapping = (
                f" · {value.hole_count} hole"
                f" · D{number(value.nominal_diameter)}"
                f" · pre-hole {number(value.pre_hole_diameter)}"
                f" · stock/side {number(value.stock_per_side)}"
                f" · feed/rev {number(value.feed_per_revolution)}"
                f" · feed/min {number(value.feed_per_minute)}"
                f" · {number(value.spindle_speed)} RPM"
                f" · {value.spindle_direction.value if value.spindle_direction else '?'}"
                f" · {value.retract_policy.value if value.retract_policy else '?'}"
                f" · {value.coolant_mode.value if value.coolant_mode else '?'}"
            )
        if value.strategy_key == "boring_v1":
            def number(item) -> str:
                return "?" if item is None else f"{item.value:g}"

            tapping = (
                f" · {value.hole_count} hole"
                f" · D{number(value.finished_bore_diameter)}"
                f" · pre-bore {number(value.pre_bore_diameter)}"
                f" · radial stock {number(value.radial_stock)}"
                f" · feed/rev {number(value.feed_per_revolution)}"
                f" · feed/min {number(value.feed_per_minute)}"
                f" · {number(value.spindle_speed)} RPM"
                f" · {value.spindle_direction.value if value.spindle_direction else '?'}"
                f" · {value.retract_policy.value if value.retract_policy else '?'}"
                f" · {value.coolant_mode.value if value.coolant_mode else '?'}"
                f" · tool {value.boring_tool_family.value if value.boring_tool_family else '?'}"
                f" · access {number(value.minimum_bore_diameter)}-"
                f"{number(value.maximum_bore_diameter)}"
            )
        self.toolpath_metadata.setText(
            f"{value.operation_id} · {value.strategy_key} · {value.pass_count} pass · "
            f"[{minimum.x:.3f}, {minimum.y:.3f}, {minimum.z:.3f}] → "
            f"[{maximum.x:.3f}, {maximum.y:.3f}, {maximum.z:.3f}] · "
            f"{value.artifact_status.value.upper()}{tapping}"
        )

    def show_hole_source(
        self,
        source: HoleReference | HolePattern | None,
        resolved: bool | None = None,
    ) -> None:
        """Show a persistent single-hole or pattern binding without runtime IDs."""
        if source is None:
            self.status.setText("HOLE MISSING · chưa Bind hole pattern")
            return
        state = "RESOLVED" if resolved else "STALE/INVALID"
        references = _hole_references(source)
        if isinstance(source, HolePattern):
            kinds = sorted({
                location.source_kind.value for location in source.locations
            })
            self.status.setText(
                f"Geometry: {state} · HOLE PATTERN {len(source.locations)} · "
                f"{', '.join(kinds)}"
            )
            return
        reference = references[0].reference
        self.status.setText(
            f"Geometry: {state} · {reference.kind.value} · "
            f"{reference.hint or reference.reference_id}"
        )

    def show_reference(
        self,
        reference: GeometryReference | None,
        resolved: bool | None = None,
        *,
        subject: str = "profile",
    ) -> None:
        if reference is None:
            label = "HOLE" if subject == "hole" else "PROFILE"
            self.status.setText(f"{label} MISSING — chưa Bind {subject}")
        else:
            state = "RESOLVED" if resolved else "STALE/INVALID"
            self.status.setText(
                f"Geometry: {state} · {reference.kind.value} · "
                f"{reference.hint or reference.reference_id}"
            )

    def pocket_state(self) -> dict[str, object]:
        """Capture only transient editor primitives; this state is never persisted."""
        return {
            "name": self._fields["name"].text(),
            "fields": {key: field.text() for key, field in self._pocket_fields.items()},
            "entry": self.pocket_entry.currentText(),
            "direction": self.pocket_direction.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_pocket_state(self, state: dict[str, object]) -> None:
        """Restore a Pocket draft by operation ID after tree selection changes."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._pocket_fields.items():
            field.setText(str(fields.get(key, "")))
        self.pocket_entry.setCurrentText(str(state.get("entry", "")))
        self.pocket_direction.setCurrentText(str(state.get("direction", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))

    def drilling_state(self) -> dict[str, object]:
        """Capture transient Drilling editor primitives without persistence."""
        return {
            "name": self._fields["name"].text(),
            "fields": {
                key: field.text() for key, field in self._drilling_fields.items()
            },
            "cycle": self.drilling_cycle.currentText(),
            "retract": self.drilling_retract.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_drilling_state(self, state: dict[str, object]) -> None:
        """Restore one unapplied Drilling draft by stable operation ID."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._drilling_fields.items():
            field.setText(str(fields.get(key, "")))
        self.drilling_cycle.setCurrentText(str(state.get("cycle", "")))
        self.drilling_retract.setCurrentText(str(state.get("retract", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))

    def tapping_state(self) -> dict[str, object]:
        """Capture one transient Tapping draft keyed outside the editor by operation ID."""
        return {
            "name": self._fields["name"].text(),
            "fields": {
                key: field.text() for key, field in self._tapping_fields.items()
            },
            "hand": self.tapping_hand.currentText(),
            "mode": self.tapping_mode.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_tapping_state(self, state: dict[str, object]) -> None:
        """Restore one unapplied Tapping draft by stable operation ID."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._tapping_fields.items():
            field.setText(str(fields.get(key, "")))
        self.tapping_hand.setCurrentText(str(state.get("hand", "")))
        self.tapping_mode.setCurrentText(str(state.get("mode", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))

    def reaming_state(self) -> dict[str, object]:
        """Capture one transient Reaming draft without derived or runtime state."""
        return {
            "name": self._fields["name"].text(),
            "fields": {
                key: field.text() for key, field in self._reaming_fields.items()
            },
            "spindle_direction": self.reaming_spindle_direction.currentText(),
            "retract_policy": self.reaming_retract_policy.currentText(),
            "coolant": self.reaming_coolant.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_reaming_state(self, state: dict[str, object]) -> None:
        """Restore one unapplied Reaming draft by stable operation ID."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._reaming_fields.items():
            field.setText(str(fields.get(key, "")))
        self.reaming_spindle_direction.setCurrentText(
            str(state.get("spindle_direction", ""))
        )
        self.reaming_retract_policy.setCurrentText(
            str(state.get("retract_policy", ""))
        )
        self.reaming_coolant.setCurrentText(str(state.get("coolant", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))
        self._update_reaming_preview()

    def boring_state(self) -> dict[str, object]:
        """Capture one transient Boring draft without derived/runtime values."""
        return {
            "name": self._fields["name"].text(),
            "fields": {
                key: field.text() for key, field in self._boring_fields.items()
            },
            "spindle_direction": self.boring_spindle_direction.currentText(),
            "retract_policy": self.boring_retract_policy.currentText(),
            "coolant": self.boring_coolant.currentText(),
            "tool_id": self.tool.currentData(),
            "machine_id": self.machine.currentData(),
            "enabled": self.enabled.isChecked(),
        }

    def restore_boring_state(self, state: dict[str, object]) -> None:
        """Restore one unapplied Boring draft by stable operation ID."""
        fields = state.get("fields")
        if not isinstance(fields, dict):
            return
        self._fields["name"].setText(str(state.get("name", "")))
        for key, field in self._boring_fields.items():
            field.setText(str(fields.get(key, "")))
        self.boring_spindle_direction.setCurrentText(
            str(state.get("spindle_direction", ""))
        )
        self.boring_retract_policy.setCurrentText(
            str(state.get("retract_policy", ""))
        )
        self.boring_coolant.setCurrentText(str(state.get("coolant", "")))
        self.tool.setCurrentIndex(self.tool.findData(state.get("tool_id")))
        self.machine.setCurrentIndex(self.machine.findData(state.get("machine_id")))
        self.enabled.setChecked(bool(state.get("enabled", False)))
        self._update_boring_preview()
        self._update_boring_tool_details()

    def _submit(self) -> None:
        self._commit({**{key: field.text() for key, field in self._fields.items()},
                      **{key: field.text() for key, field in self._facing_fields.items()},
                      **{f"contour_{key}": field.text() for key, field in self._contour_fields.items()},
                      **{f"pocket_{key}": field.text() for key, field in self._pocket_fields.items()},
                      **{f"drilling_{key}": field.text() for key, field in self._drilling_fields.items()},
                      **{f"tapping_{key}": field.text() for key, field in self._tapping_fields.items()},
                      **{f"reaming_{key}": field.text() for key, field in self._reaming_fields.items()},
                      **{f"boring_{key}": field.text() for key, field in self._boring_fields.items()},
                      "setup_kind": self.setup_kind.currentText(), "stock_kind": self.stock_kind.currentText(),
                      "enabled": self.enabled.isChecked(), "boundary_source": self.boundary_source.currentText(),
                      "direction": self.direction.currentText(), "tool_id": self.tool.currentData(),
                      "machine_id": self.machine.currentData(), "profile_source": self.profile_source.currentText(),
                      "contour_side": self.contour_side.currentText(),
                      "contour_direction": self.contour_direction.currentText(),
                      "pocket_entry": self.pocket_entry.currentText(),
                      "pocket_direction": self.pocket_direction.currentText(),
                      "drilling_cycle": self.drilling_cycle.currentText(),
                      "drilling_retract": self.drilling_retract.currentText(),
                      "tapping_hand": self.tapping_hand.currentText(),
                      "tapping_mode": self.tapping_mode.currentText(),
                      "reaming_spindle_direction": self.reaming_spindle_direction.currentText(),
                      "reaming_retract_policy": self.reaming_retract_policy.currentText(),
                      "reaming_coolant": self.reaming_coolant.currentText(),
                      "boring_spindle_direction": self.boring_spindle_direction.currentText(),
                      "boring_retract_policy": self.boring_retract_policy.currentText(),
                      "boring_coolant": self.boring_coolant.currentText(),
                      "finishing_pass": self.finishing_pass.isChecked(),
                      "multiple_depth_passes": self.multiple_depth_passes.isChecked()})

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

    def contour_draft(self, unit: LengthUnit) -> ContourParameters | None:
        try:
            feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            value = ContourParameters(
                unit, ContourProfileSource(self.profile_source.currentText()),
                ContourSide(self.contour_side.currentText()),
                Length(float(self._contour_fields["top"].text()), unit),
                Length(float(self._contour_fields["final"].text()), unit),
                Length(float(self._contour_fields["stepdown"].text()), unit),
                Length(float(self._contour_fields["radial"].text()), unit),
                Length(float(self._contour_fields["axial"].text()), unit),
                Length(float(self._contour_fields["clearance"].text()), unit),
                Length(float(self._contour_fields["retract"].text()), unit),
                FeedRate(float(self._contour_fields["feed"].text()), feed_unit),
                FeedRate(float(self._contour_fields["plunge"].text()), feed_unit),
                SpindleSpeed(float(self._contour_fields["spindle"].text())),
                ContourCutDirection(self.contour_direction.currentText()),
                lead_length=Length(float(self._contour_fields["lead"].text()), unit),
                finishing_pass=self.finishing_pass.isChecked(),
                multiple_depth_passes=self.multiple_depth_passes.isChecked(),
            )
            return value if self.tool.currentData() is not None and self.machine.currentData() is not None else None
        except (TypeError, ValueError):
            return None

    def pocket_draft(
        self,
        unit: LengthUnit,
        reference: GeometryReference | None,
    ) -> PocketStrategy | None:
        try:
            if reference is None:
                return None
            feed_unit = (FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM
                         else FeedUnit.INCH_PER_MINUTE)
            return PocketStrategy(
                unit,
                PocketGeometryInput(reference, unit),
                PocketDepthDefinition(
                    unit,
                    Length(float(self._pocket_fields["top"].text()), unit),
                    Length(float(self._pocket_fields["bottom"].text()), unit),
                    Length(float(self._pocket_fields["axial"].text()), unit),
                ),
                Length(float(self._pocket_fields["stepover"].text()), unit),
                Length(float(self._pocket_fields["stepdown"].text()), unit),
                Length(float(self._pocket_fields["allowance"].text()), unit),
                Length(float(self._pocket_fields["clearance"].text()), unit),
                Length(float(self._pocket_fields["retract"].text()), unit),
                FeedRate(float(self._pocket_fields["feed"].text()), feed_unit),
                FeedRate(float(self._pocket_fields["plunge"].text()), feed_unit),
                SpindleSpeed(float(self._pocket_fields["spindle"].text())),
                PocketEntryPolicy(self.pocket_entry.currentText()),
                PocketCuttingDirection(self.pocket_direction.currentText()),
                Length(float(self._pocket_fields["tolerance"].text()), unit),
            )
        except (TypeError, ValueError):
            return None

    def drilling_draft(
        self,
        unit: LengthUnit,
        hole_source: HoleReference | HolePattern | None,
    ) -> DrillingStrategy | None:
        try:
            if hole_source is None or hole_source.unit is not unit:
                return None
            feed_unit = (
                FeedUnit.MM_PER_MINUTE
                if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
            )
            top_z = float(self._drilling_fields["top"].text())
            depth = float(self._drilling_fields["depth"].text())
            cycle = DrillingCycle(self.drilling_cycle.currentText())
            peck = (
                Length(float(self._drilling_fields["peck"].text()), unit)
                if cycle is DrillingCycle.PECK_DRILL else None
            )
            return DrillingStrategy(
                unit=unit,
                geometry=DrillGeometryInput(hole_source, unit),
                depth=DrillDepthDefinition(
                    unit, Length(top_z, unit), Length(top_z - depth, unit),
                ),
                cycle=cycle,
                clearance_height=Length(
                    float(self._drilling_fields["clearance"].text()), unit,
                ),
                retract_height=Length(
                    float(self._drilling_fields["retract"].text()), unit,
                ),
                feed_rate=FeedRate(
                    float(self._drilling_fields["feed"].text()), feed_unit,
                ),
                spindle_speed=SpindleSpeed(
                    float(self._drilling_fields["spindle"].text())
                ),
                dwell_seconds=float(self._drilling_fields["dwell"].text()),
                peck_depth=peck,
                retract_policy=DrillRetractPolicy(
                    self.drilling_retract.currentText()
                ),
                approach_policy=(
                    DrillApproachPolicy.RAPID_CLEARANCE_FEED_RETRACT
                ),
                tolerance=Length(
                    float(self._drilling_fields["tolerance"].text()), unit,
                ),
            )
        except (TypeError, ValueError):
            return None

    def tapping_draft(
        self,
        unit: LengthUnit,
        hole_source: HoleReference | HolePattern | None,
    ) -> TappingStrategy | None:
        """Build an immutable Tapping draft without mutating the project domain."""
        self._tapping_draft_error = ""
        try:
            if hole_source is None or hole_source.unit is not unit:
                return None
            dwell_text = self._tapping_fields["dwell"].text().strip()
            return TappingStrategy(
                unit=unit,
                geometry=DrillGeometryInput(hole_source, unit),
                depth=DrillDepthDefinition(
                    unit,
                    Length(float(self._tapping_fields["top"].text()), unit),
                    Length(float(self._tapping_fields["final"].text()), unit),
                ),
                nominal_diameter=Length(
                    float(self._tapping_fields["diameter"].text()), unit,
                ),
                pitch=Length(
                    float(self._tapping_fields["pitch"].text()), unit,
                ),
                hand=TappingHand(self.tapping_hand.currentText()),
                spindle_speed=SpindleSpeed(
                    float(self._tapping_fields["spindle"].text())
                ),
                clearance_height=Length(
                    float(self._tapping_fields["clearance"].text()), unit,
                ),
                retract_height=Length(
                    float(self._tapping_fields["retract"].text()), unit,
                ),
                synchronization_policy=TappingSynchronizationPolicy(
                    self.tapping_mode.currentText()
                ),
                dwell_seconds=0.0 if not dwell_text else float(dwell_text),
                tolerance=Length(
                    float(self._tapping_fields["tolerance"].text()), unit,
                ),
            )
        except TappingValidationError as error:
            self._tapping_draft_error = f"{error.code.value}: {error}"
            return None
        except DrillValidationError as error:
            code = (
                "tap.depth_invalid"
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else "tap.invalid_parameters"
            )
            self._tapping_draft_error = f"{code}: {error}"
            return None
        except (TypeError, ValueError) as error:
            self._tapping_draft_error = (
                f"tap.invalid_parameters: {error or 'Thông số Tapping không hợp lệ.'}"
            )
            return None

    @property
    def tapping_draft_error(self) -> str:
        """Return the last stable diagnostic produced while parsing a Tapping draft."""
        return self._tapping_draft_error

    def reaming_draft(
        self,
        unit: LengthUnit,
        hole_source: HoleReference | HolePattern | None,
    ) -> ReamingStrategy | None:
        """Build an immutable Reaming draft without mutating project state."""
        self._reaming_draft_error = ""
        try:
            if hole_source is None or hole_source.unit is not unit:
                self._reaming_draft_error = (
                    "ream.geometry_missing: Reaming thiếu hole geometry hợp lệ."
                )
                return None
            pre_hole_text = self._reaming_fields["pre_hole"].text().strip()
            if not pre_hole_text:
                self._reaming_draft_error = (
                    "ream.prehole_missing: Pre-hole diameter là bắt buộc."
                )
                return None
            dwell_text = self._reaming_fields["dwell"].text().strip()
            feed_unit = (
                FeedUnit.MM_PER_REVOLUTION
                if unit is LengthUnit.MM
                else FeedUnit.INCH_PER_REVOLUTION
            )
            return ReamingStrategy(
                unit=unit,
                geometry=DrillGeometryInput(hole_source, unit),
                depth=DrillDepthDefinition(
                    unit,
                    Length(float(self._reaming_fields["top"].text()), unit),
                    Length(float(self._reaming_fields["final"].text()), unit),
                ),
                nominal_diameter=Length(
                    float(self._reaming_fields["diameter"].text()), unit,
                ),
                pre_hole_diameter=Length(float(pre_hole_text), unit),
                spindle_speed=SpindleSpeed(
                    float(self._reaming_fields["spindle"].text())
                ),
                feed_per_revolution=FeedRate(
                    float(self._reaming_fields["feed_per_revolution"].text()),
                    feed_unit,
                ),
                clearance_height=Length(
                    float(self._reaming_fields["clearance"].text()), unit,
                ),
                retract_height=Length(
                    float(self._reaming_fields["retract"].text()), unit,
                ),
                spindle_direction=SpindleDirection(
                    self.reaming_spindle_direction.currentText()
                ),
                retract_policy=ReamingRetractPolicy(
                    self.reaming_retract_policy.currentText()
                ),
                coolant=ReamingCoolantMode(self.reaming_coolant.currentText()),
                dwell_seconds=0.0 if not dwell_text else float(dwell_text),
                tolerance=Length(
                    float(self._reaming_fields["tolerance"].text()), unit,
                ),
            )
        except ReamingValidationError as error:
            self._reaming_draft_error = f"{error.code.value}: {error}"
            return None
        except DrillValidationError as error:
            code = (
                "ream.depth_invalid"
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else "ream.invalid_parameters"
            )
            self._reaming_draft_error = f"{code}: {error}"
            return None
        except (TypeError, ValueError) as error:
            self._reaming_draft_error = (
                f"ream.invalid_parameters: "
                f"{error or 'Thông số Reaming không hợp lệ.'}"
            )
            return None

    @property
    def reaming_draft_error(self) -> str:
        """Return the last stable diagnostic produced while parsing Reaming."""
        return self._reaming_draft_error

    def _update_reaming_preview(self, *_args: object) -> None:
        """Update derived-only values without constructing or mutating an operation."""
        try:
            nominal_text = self._reaming_fields["diameter"].text().strip()
            pre_hole_text = self._reaming_fields["pre_hole"].text().strip()
            if not pre_hole_text:
                self.reaming_derived.setText(
                    "ream.prehole_missing · pre-hole diameter là bắt buộc"
                )
                return
            nominal = float(nominal_text)
            pre_hole = float(pre_hole_text)
            tolerance = float(self._reaming_fields["tolerance"].text())
            if not math.isfinite(nominal) or nominal <= 0.0:
                raise ValueError("ream.invalid_parameters · nominal diameter phải > 0")
            if (
                not math.isfinite(pre_hole)
                or pre_hole <= 0.0
                or pre_hole >= nominal
            ):
                raise ValueError(
                    "ream.prehole_invalid · pre-hole phải > 0 và nhỏ hơn nominal"
                )
            stock = (nominal - pre_hole) / 2.0
            if (
                not math.isfinite(tolerance)
                or tolerance <= 0.0
                or stock <= tolerance
                or stock >= nominal / 2.0 - tolerance
            ):
                raise ValueError(
                    "ream.stock_invalid · stock mỗi phía ngoài giới hạn tolerance"
                )
            rpm = float(self._reaming_fields["spindle"].text())
            feed_per_revolution = float(
                self._reaming_fields["feed_per_revolution"].text()
            )
            top = float(self._reaming_fields["top"].text())
            final = float(self._reaming_fields["final"].text())
            derived = (rpm, feed_per_revolution, top, final)
            if any(not math.isfinite(value) for value in derived):
                raise ValueError("ream.invalid_parameters · derived input không hữu hạn")
            if rpm <= 0.0 or feed_per_revolution <= 0.0:
                raise ValueError("ream.invalid_parameters · RPM/feed mỗi vòng phải > 0")
            self.reaming_derived.setText(
                f"Stock/side: {stock:g} · Feed/min: "
                f"{rpm * feed_per_revolution:g} · Cutting depth: {top - final:g}"
            )
        except ValueError as error:
            self.reaming_derived.setText(str(error) or "ream.invalid_parameters")

    def boring_draft(
        self,
        unit: LengthUnit,
        hole_source: HoleReference | HolePattern | None,
    ) -> BoringStrategy | None:
        """Build an immutable Boring draft without mutating project state."""
        self._boring_draft_error = ""
        try:
            if hole_source is None or hole_source.unit is not unit:
                self._boring_draft_error = (
                    "bore.geometry_missing: Boring thiếu hole geometry hợp lệ."
                )
                return None
            pre_bore_text = self._boring_fields["pre_bore"].text().strip()
            if not pre_bore_text:
                self._boring_draft_error = (
                    "bore.prebore_missing: Pre-bore diameter là bắt buộc."
                )
                return None
            dwell_text = self._boring_fields["dwell"].text().strip()
            feed_unit = (
                FeedUnit.MM_PER_REVOLUTION
                if unit is LengthUnit.MM
                else FeedUnit.INCH_PER_REVOLUTION
            )
            return BoringStrategy(
                unit=unit,
                geometry=DrillGeometryInput(hole_source, unit),
                depth=DrillDepthDefinition(
                    unit,
                    Length(float(self._boring_fields["top"].text()), unit),
                    Length(float(self._boring_fields["final"].text()), unit),
                ),
                finished_bore_diameter=Length(
                    float(self._boring_fields["finished_diameter"].text()),
                    unit,
                ),
                pre_bore_diameter=Length(float(pre_bore_text), unit),
                spindle_rpm=SpindleSpeed(
                    float(self._boring_fields["spindle"].text())
                ),
                feed_per_revolution=FeedRate(
                    float(self._boring_fields["feed_per_revolution"].text()),
                    feed_unit,
                ),
                clearance_height=Length(
                    float(self._boring_fields["clearance"].text()), unit,
                ),
                retract_height=Length(
                    float(self._boring_fields["retract"].text()), unit,
                ),
                spindle_direction=SpindleDirection(
                    self.boring_spindle_direction.currentText()
                ),
                retract_policy=BoringRetractPolicy(
                    self.boring_retract_policy.currentText()
                ),
                coolant=BoringCoolantMode(self.boring_coolant.currentText()),
                dwell_seconds=0.0 if not dwell_text else float(dwell_text),
                tolerance=Length(
                    float(self._boring_fields["tolerance"].text()), unit,
                ),
            )
        except BoringValidationError as error:
            self._boring_draft_error = f"{error.code.value}: {error}"
            return None
        except DrillValidationError as error:
            code = (
                "bore.depth_invalid"
                if error.code in {
                    DiagnosticCode.DRILL_INVALID_DEPTH,
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                }
                else "bore.invalid_parameters"
            )
            self._boring_draft_error = f"{code}: {error}"
            return None
        except (TypeError, ValueError) as error:
            self._boring_draft_error = (
                f"bore.invalid_parameters: "
                f"{error or 'Thông số Boring không hợp lệ.'}"
            )
            return None

    @property
    def boring_draft_error(self) -> str:
        """Return the stable diagnostic produced while parsing Boring."""
        return self._boring_draft_error

    def _update_boring_preview(self, *_args: object) -> None:
        """Update derived Boring values without storing them as source data."""
        try:
            finished_text = self._boring_fields["finished_diameter"].text().strip()
            pre_bore_text = self._boring_fields["pre_bore"].text().strip()
            if not pre_bore_text:
                self.boring_derived.setText(
                    "bore.prebore_missing · pre-bore diameter là bắt buộc"
                )
                return
            finished = float(finished_text)
            pre_bore = float(pre_bore_text)
            tolerance = float(self._boring_fields["tolerance"].text())
            if not math.isfinite(finished) or finished <= 0.0:
                raise ValueError(
                    "bore.invalid_parameters · finished diameter phải > 0"
                )
            if (
                not math.isfinite(pre_bore)
                or pre_bore <= 0.0
                or pre_bore >= finished
            ):
                raise ValueError(
                    "bore.prebore_invalid · pre-bore phải > 0 và nhỏ hơn finished"
                )
            stock = (finished - pre_bore) / 2.0
            if (
                not math.isfinite(tolerance)
                or tolerance <= 0.0
                or stock <= tolerance
                or stock >= finished / 2.0 - tolerance
            ):
                raise ValueError(
                    "bore.stock_invalid · radial stock ngoài giới hạn tolerance"
                )
            rpm = float(self._boring_fields["spindle"].text())
            feed_per_revolution = float(
                self._boring_fields["feed_per_revolution"].text()
            )
            top = float(self._boring_fields["top"].text())
            final = float(self._boring_fields["final"].text())
            derived = (rpm, feed_per_revolution, top, final)
            if any(not math.isfinite(value) for value in derived):
                raise ValueError(
                    "bore.invalid_parameters · derived input không hữu hạn"
                )
            if rpm <= 0.0 or feed_per_revolution <= 0.0:
                raise ValueError(
                    "bore.invalid_parameters · RPM/feed mỗi vòng phải > 0"
                )
            self.boring_derived.setText(
                f"Radial stock: {stock:g} · Feed/min: "
                f"{rpm * feed_per_revolution:g} · Cutting depth: {top - final:g}"
            )
        except ValueError as error:
            self.boring_derived.setText(str(error) or "bore.invalid_parameters")

    def _update_boring_tool_details(self, *_args: object) -> None:
        """Expose the current BORING_BAR access envelope and provenance read-only."""
        assembly = self._assemblies_by_id.get(str(self.tool.currentData()))
        if assembly is None:
            self.boring_tool_details.setText("bore.tool_missing · chưa chọn assembly")
            return
        tool = self._tool_definitions_by_id.get(assembly.tool_id)
        holder = self._holders_by_id.get(assembly.holder_id)
        if (
            tool is None
            or tool.family is not ToolFamily.BORING_BAR
            or not isinstance(tool.cutting_geometry, BoringBarGeometry)
        ):
            self.boring_tool_details.setText(
                "bore.unsupported_tool · chỉ chấp nhận BORING_BAR"
            )
            return
        geometry = tool.cutting_geometry
        current = (
            tool.revision == assembly.expected_tool_revision
            and tool.content_fingerprint == assembly.expected_tool_fingerprint
            and holder is not None
            and holder.revision == assembly.expected_holder_revision
            and holder.content_fingerprint == assembly.expected_holder_fingerprint
        )
        holder_text = "MISSING" if holder is None else holder.name
        holder_revision = "?" if holder is None else str(holder.revision.value)
        holder_fingerprint = (
            "?" if holder is None else holder.content_fingerprint.digest[:12]
        )
        self.boring_tool_details.setText(
            f"{tool.family.value} · min D{geometry.minimum_bore_diameter.value:g} · "
            f"max D{geometry.maximum_bore_diameter.value:g} · "
            f"cut {geometry.cutting_length.value:g} · hand {geometry.hand.value} · "
            f"unit {tool.unit.value} · shank D{tool.shank.diameter.value:g} · "
            f"usable {tool.usable_length.value:g} · stickout {assembly.stickout.value:g} · "
            f"assembly rev {assembly.revision.value}/fp {assembly.content_fingerprint.digest[:12]} · "
            f"tool rev {tool.revision.value}/fp {tool.content_fingerprint.digest[:12]} · "
            f"holder {holder_text} rev {holder_revision}/fp {holder_fingerprint} · "
            f"snapshot {'CURRENT' if current else 'STALE'}"
        )


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


def _default_contour_parameters(setup: Setup) -> ContourParameters:
    if not isinstance(setup.stock, BoxStock):
        raise ValueError("2D Contour mặc định yêu cầu Stock BOX.")
    unit = setup.wcs.origin.unit
    feed_unit = FeedUnit.MM_PER_MINUTE if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
    top = setup.stock.size_z.value
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    return ContourParameters(
        unit, ContourProfileSource.PLANAR_FACE_OUTER, ContourSide.ON,
        Length(top, unit), Length(top - scale, unit), Length(scale, unit),
        Length(0, unit), Length(0, unit), Length(top + 5 * scale, unit),
        Length(top + 2 * scale, unit), FeedRate(500 * scale, feed_unit),
        FeedRate(100 * scale, feed_unit), SpindleSpeed(1000),
        ContourCutDirection.CLIMB, lead_length=Length(scale, unit),
    )


def _default_pocket_parameters(setup: Setup) -> OperationParameterSet:
    """Create an unbound but schema-complete Pocket draft parameter set."""
    if not isinstance(setup.stock, BoxStock):
        raise ValueError("Pocket mặc định yêu cầu Stock BOX.")
    unit = setup.wcs.origin.unit
    top = setup.stock.size_z.value
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    return OperationParameterSet(
        "pocket_2_5d",
        1,
        (
            ("unit", unit.value),
            ("top_z", top),
            ("bottom_z", top - scale),
            ("axial_allowance", 0.0),
            ("stepover", 4.0 * scale),
            ("stepdown", scale),
            ("radial_stock_allowance", 0.0),
            ("clearance_height", top + 5.0 * scale),
            ("retract_height", top + 2.0 * scale),
            ("cutting_feed_rate", 500.0 * scale),
            ("plunge_feed_rate", 100.0 * scale),
            ("spindle_speed", 1000.0),
            ("entry_policy", PocketEntryPolicy.VERTICAL_PLUNGE.value),
            ("cutting_direction", PocketCuttingDirection.CLIMB.value),
            ("tolerance", 1.0e-7 * scale),
        ),
    )


def _default_drilling_strategy(
    setup: Setup,
    hole_source: HoleReference | HolePattern,
) -> DrillingStrategy:
    """Create a conservative bound DRILL draft at the selected drilling plane."""
    unit = setup.wcs.origin.unit
    if hole_source.unit is not unit:
        raise ValueError("Drilling geometry unit does not match Setup WCS.")
    plane_origin = (
        hole_source.plane_origin
        if isinstance(hole_source, HoleReference)
        else hole_source.locations[0].plane_origin
    )
    delta = Vector3(
        plane_origin.x - setup.wcs.origin.x,
        plane_origin.y - setup.wcs.origin.y,
        plane_origin.z - setup.wcs.origin.z,
    )
    top_z = delta.dot(setup.wcs.z_axis)
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    feed_unit = (
        FeedUnit.MM_PER_MINUTE
        if unit is LengthUnit.MM else FeedUnit.INCH_PER_MINUTE
    )
    return DrillingStrategy(
        unit=unit,
        geometry=DrillGeometryInput(hole_source, unit),
        depth=DrillDepthDefinition(
            unit, Length(top_z, unit), Length(top_z - 5.0 * scale, unit),
        ),
        cycle=DrillingCycle.DRILL,
        clearance_height=Length(top_z + 8.0 * scale, unit),
        retract_height=Length(top_z + 3.0 * scale, unit),
        feed_rate=FeedRate(120.0 * scale, feed_unit),
        spindle_speed=SpindleSpeed(1500.0),
        dwell_seconds=0.0,
        retract_policy=DrillRetractPolicy.RETRACT_HEIGHT,
        approach_policy=DrillApproachPolicy.RAPID_CLEARANCE_FEED_RETRACT,
        tolerance=Length(1.0e-7 * scale, unit),
    )


def _default_tapping_strategy(
    setup: Setup,
    hole_source: HoleReference | HolePattern,
) -> TappingStrategy:
    """Create a conservative RH rigid Tapping draft at the selected hole plane."""
    unit = setup.wcs.origin.unit
    if hole_source.unit is not unit:
        raise ValueError("tap.invalid_parameters: Hole source unit không khớp WCS.")
    plane_origin = (
        hole_source.plane_origin
        if isinstance(hole_source, HoleReference)
        else hole_source.locations[0].plane_origin
    )
    delta = Vector3(
        plane_origin.x - setup.wcs.origin.x,
        plane_origin.y - setup.wcs.origin.y,
        plane_origin.z - setup.wcs.origin.z,
    )
    top_z = delta.dot(setup.wcs.z_axis)
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    return TappingStrategy(
        unit=unit,
        geometry=DrillGeometryInput(hole_source, unit),
        depth=DrillDepthDefinition(
            unit,
            Length(top_z, unit),
            Length(top_z - 10.0 * scale, unit),
        ),
        nominal_diameter=Length(8.0 * scale, unit),
        pitch=Length(1.25 * scale, unit),
        hand=TappingHand.RIGHT_HAND_TAP,
        spindle_speed=SpindleSpeed(500.0),
        clearance_height=Length(top_z + 8.0 * scale, unit),
        retract_height=Length(top_z + 3.0 * scale, unit),
        synchronization_policy=TappingSynchronizationPolicy.RIGID,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7 * scale, unit),
    )


def _default_reaming_strategy(
    setup: Setup,
    hole_source: HoleReference | HolePattern,
) -> ReamingStrategy:
    """Create a conservative D8 Reaming draft with explicit D7.8 pre-hole."""
    unit = setup.wcs.origin.unit
    if hole_source.unit is not unit:
        raise ValueError("ream.invalid_parameters: Hole source unit không khớp WCS.")
    plane_origin = (
        hole_source.plane_origin
        if isinstance(hole_source, HoleReference)
        else hole_source.locations[0].plane_origin
    )
    delta = Vector3(
        plane_origin.x - setup.wcs.origin.x,
        plane_origin.y - setup.wcs.origin.y,
        plane_origin.z - setup.wcs.origin.z,
    )
    top_z = delta.dot(setup.wcs.z_axis)
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    feed_unit = (
        FeedUnit.MM_PER_REVOLUTION
        if unit is LengthUnit.MM else FeedUnit.INCH_PER_REVOLUTION
    )
    return ReamingStrategy(
        unit=unit,
        geometry=DrillGeometryInput(hole_source, unit),
        depth=DrillDepthDefinition(
            unit,
            Length(top_z, unit),
            Length(top_z - 10.0 * scale, unit),
        ),
        nominal_diameter=Length(8.0 * scale, unit),
        pre_hole_diameter=Length(7.8 * scale, unit),
        spindle_speed=SpindleSpeed(500.0),
        feed_per_revolution=FeedRate(0.1 * scale, feed_unit),
        clearance_height=Length(top_z + 8.0 * scale, unit),
        retract_height=Length(top_z + 3.0 * scale, unit),
        spindle_direction=SpindleDirection.CLOCKWISE,
        retract_policy=ReamingRetractPolicy.CONTROLLED_FEED,
        coolant=ReamingCoolantMode.OFF,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7 * scale, unit),
    )


def _default_boring_strategy(
    setup: Setup,
    hole_source: HoleReference | HolePattern,
) -> BoringStrategy:
    """Create a conservative D20 Boring draft with explicit D18 pre-bore."""
    unit = setup.wcs.origin.unit
    if hole_source.unit is not unit:
        raise ValueError("bore.invalid_parameters: Hole source unit không khớp WCS.")
    plane_origin = (
        hole_source.plane_origin
        if isinstance(hole_source, HoleReference)
        else hole_source.locations[0].plane_origin
    )
    delta = Vector3(
        plane_origin.x - setup.wcs.origin.x,
        plane_origin.y - setup.wcs.origin.y,
        plane_origin.z - setup.wcs.origin.z,
    )
    top_z = delta.dot(setup.wcs.z_axis)
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    feed_unit = (
        FeedUnit.MM_PER_REVOLUTION
        if unit is LengthUnit.MM else FeedUnit.INCH_PER_REVOLUTION
    )
    return BoringStrategy(
        unit=unit,
        geometry=DrillGeometryInput(hole_source, unit),
        depth=DrillDepthDefinition(
            unit,
            Length(top_z, unit),
            Length(top_z - 10.0 * scale, unit),
        ),
        finished_bore_diameter=Length(20.0 * scale, unit),
        pre_bore_diameter=Length(18.0 * scale, unit),
        spindle_rpm=SpindleSpeed(600.0),
        feed_per_revolution=FeedRate(0.1 * scale, feed_unit),
        clearance_height=Length(top_z + 8.0 * scale, unit),
        retract_height=Length(top_z + 3.0 * scale, unit),
        spindle_direction=SpindleDirection.CLOCKWISE,
        retract_policy=BoringRetractPolicy.CONTROLLED_FEED,
        coolant=BoringCoolantMode.OFF,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7 * scale, unit),
    )


def _hole_strategy(
    operation: Operation,
) -> DrillingStrategy | TappingStrategy | ReamingStrategy | BoringStrategy:
    """Decode one supported hole strategy without changing its binding."""
    if operation.strategy_key == "tapping_v1":
        return TappingStrategy.from_operation_parameters(operation.parameters)
    if operation.strategy_key == "reaming_v1":
        return ReamingStrategy.from_operation_parameters(operation.parameters)
    if operation.strategy_key == "boring_v1":
        return BoringStrategy.from_operation_parameters(operation.parameters)
    if operation.strategy_key == "drilling_v1":
        return DrillingStrategy.from_operation_parameters(operation.parameters)
    raise ValueError("Operation is not a supported hole strategy")


def _hole_references(
    source: HoleReference | HolePattern,
) -> tuple[HoleReference, ...]:
    if isinstance(source, HoleReference):
        return (source,)
    return tuple(
        location.reference
        for location in source.locations
        if location.reference is not None
    )


def _hole_geometry_inputs(
    existing: tuple[OperationGeometryInput, ...],
    source: HoleReference | HolePattern,
) -> tuple[OperationGeometryInput, ...]:
    existing_by_reference = {
        value.reference.reference_id: value for value in existing
    }
    return tuple(
        OperationGeometryInput(
            (
                previous.input_id
                if (previous := existing_by_reference.get(
                    hole.reference.reference_id
                )) is not None
                else GeometryInputId.new()
            ),
            GeometryInputRole.DRIVE_GEOMETRY,
            hole.reference,
            True,
            hole.reference.kind,
            index,
        )
        for index, hole in enumerate(_hole_references(source))
    )


def _operation_matches_hole_source(
    operation: Operation,
    source: HoleReference | HolePattern,
) -> bool:
    expected = tuple(hole.reference for hole in _hole_references(source))
    supplied = tuple(
        item.reference for item in sorted(
            operation.geometry_inputs,
            key=lambda value: value.selection_order,
        )
    )
    return supplied == expected


def _find_drilling_assembly(snapshot, family: ToolFamily):
    tools = {value.tool_id: value for value in snapshot.tool_definitions}
    return next((
        assembly for assembly in snapshot.tool_assemblies
        if tools.get(assembly.tool_id) is not None
        and tools[assembly.tool_id].family is family
    ), None)


def _active_job(snapshot):
    return next(job for job in snapshot.jobs if job.job_id == snapshot.active_job_id)


from PySide6.QtWidgets import QTreeWidgetItemIterator  # noqa: E402
