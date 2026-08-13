"""Deterministic native-free projection builder for Operation Manager 9A.3."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from uuid import UUID

from hms_cadcam.cam.domain import ArtifactStatus
from hms_cadcam.project.models import ProjectSession
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.localization import (
    display_value,
    operation_display_name,
    operation_type_display_name,
    setup_display_name,
    ui_text,
)
from hms_cadcam.ui.operation_manager_status import (
    calculation_status,
    current_status,
    dirty_reason_summary,
    nc_status,
    operation_status,
    parallel_safety_status,
    z_level_safety_status,
    post_status,
    setup_machine_name,
    simulation_status,
    status,
    stock_summary,
    tool_status,
)
from hms_cadcam.ui.operation_manager_types import (
    OperationManagerCapability as Capability,
    OperationManagerDomainIdentity as DomainIdentity,
    OperationManagerEntityKind as EntityKind,
    OperationManagerHeader,
    OperationManagerLegacySelection as LegacySelection,
    OperationManagerNode,
    OperationManagerNodeId as NodeId,
    OperationManagerNodeKind as NodeKind,
    OperationManagerProjection,
    OperationManagerSemanticStatus as SemanticStatus,
    OperationManagerStatus,
    OperationManagerStatusCategory as StatusCategory,
)


_SUPPORTED_STRATEGIES = frozenset(
    {
        "facing_2_5d",
        "contour_2d",
        "pocket_2_5d",
        "rest_pocket_3axis",
        "parallel_finishing_3d",
        "z_level_finishing_3d",
        "drilling_v1",
        "tapping_v1",
        "reaming_v1",
        "boring_v1",
    }
)

# Hidden search aliases preserve established workflows and persisted English
# strategy tokens without exposing them as production UI labels.
_STRATEGY_SEARCH_ALIASES = {
    "facing_2_5d": "Facing 2.5D",
    "contour_2d": "Contour 2D",
    "pocket_2_5d": "Pocket 2.5D",
    "rest_pocket_3axis": "Rest Pocket 3-Axis",
    "parallel_finishing_3d": "Parallel Finishing",
    "z_level_finishing_3d": "Z-Level Finishing",
    "drilling_v1": "Drilling",
    "tapping_v1": "Tapping",
    "reaming_v1": "Reaming",
    "boring_v1": "Boring",
}

_STATUS_SEARCH_ALIASES = {
    SemanticStatus.DRAFT: "NEEDS CALC",
    SemanticStatus.NEEDS_INPUT: "NEEDS INPUT",
    SemanticStatus.CALCULATING: "CALCULATING",
    SemanticStatus.CURRENT: "CURRENT",
    SemanticStatus.STALE: "STALE",
    SemanticStatus.FAILED: "FAILED",
}


class _Collector:
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        self.nodes: list[OperationManagerNode] = []
        self.children: dict[NodeId, list[NodeId]] = {}

    def add(
        self,
        kind: NodeKind,
        identity_kind: EntityKind,
        identity: object,
        parent_id: NodeId | None,
        label: str,
        summary: str,
        statuses: Iterable[OperationManagerStatus],
        *,
        enabled: bool = True,
        order: int = 0,
        counts: Iterable[tuple[str, int]] = (),
        capabilities: Iterable[Capability] = (),
        search_terms: Iterable[object] = (),
        legacy: LegacySelection | None = None,
        default_expanded: bool = False,
        node_identity: object | None = None,
    ) -> NodeId:
        selected_identity = identity if node_identity is None else node_identity
        node_id = NodeId(f"{self.project_id}:{kind.value}:{selected_identity}")
        node = OperationManagerNode(
            node_id=node_id,
            domain_identity=DomainIdentity(identity_kind, str(identity)),
            parent_id=parent_id,
            kind=kind,
            label=label,
            secondary_summary=summary,
            statuses=tuple(statuses),
            enabled=enabled,
            order=order,
            counts=tuple(counts),
            capabilities=tuple(capabilities),
            search_terms=tuple(str(item) for item in search_terms),
            legacy_selection=legacy,
            default_expanded=default_expanded,
        )
        self.nodes.append(node)
        if parent_id is not None:
            self.children.setdefault(parent_id, []).append(node_id)
        return node_id

    def materialize(self) -> tuple[OperationManagerNode, ...]:
        return tuple(
            replace(node, children=tuple(self.children.get(node.node_id, ())))
            for node in self.nodes
        )


class OperationManagerProjectionBuilder:
    """Project existing CAM/application state without mutating or calculating it."""

    def build(
        self,
        service: ProjectService,
        session: ProjectSession | None,
    ) -> OperationManagerProjection:
        if session is None or not service.has_project:
            return _no_project()
        snapshot = service.cam_snapshot
        project_id = session.manifest.project_id
        collector = _Collector(project_id)
        operation_count = sum(
            len(setup.operation_tree.operations)
            for job in snapshot.jobs
            for setup in job.setups
        )
        project_status = status(
            StatusCategory.DOMAIN,
            SemanticStatus.DRAFT if session.is_dirty else SemanticStatus.CURRENT,
            ui_text("Unsaved") if session.is_dirty else ui_text("Saved"),
            ui_text("Project has unsaved changes.")
            if session.is_dirty
            else ui_text("Project matches the saved state."),
        )
        project_node = collector.add(
            NodeKind.PROJECT,
            EntityKind.PROJECT,
            project_id,
            None,
            session.manifest.project_name,
            ui_text("{0} jobs · {1} operations").format(
                len(snapshot.jobs), operation_count
            ),
            (project_status,),
            counts=(("jobs", len(snapshot.jobs)), ("operations", operation_count)),
            default_expanded=True,
        )
        if not snapshot.jobs:
            collector.add(
                NodeKind.EMPTY_STATE,
                EntityKind.PROJECT,
                project_id,
                project_node,
                ui_text("No CAM jobs"),
                ui_text("The CAD project has no CAM data yet."),
                (
                    status(
                        StatusCategory.DOMAIN,
                        SemanticStatus.DRAFT,
                        ui_text("CAD only"),
                        ui_text("Create a CAM job when machining programming is ready."),
                    ),
                ),
                enabled=True,
                node_identity="cad-only",
            )

        post_results = service.post_service.results()
        nc_artifacts = service.nc_export_service.artifacts()
        simulations = {
            result.operation_id: result for result in service.simulation_runs.results()
        }
        for job_order, job in enumerate(snapshot.jobs):
            active = job.job_id == snapshot.active_job_id
            job_node = collector.add(
                NodeKind.JOB,
                EntityKind.JOB,
                job.job_id,
                project_node,
                job.name,
                f"{len(job.setups)} thiết lập",
                (
                    status(
                        StatusCategory.DOMAIN,
                        SemanticStatus.ACTIVE if active else SemanticStatus.READY,
                        "ĐANG HOẠT ĐỘNG" if active else "SẴN SÀNG",
                        "Công việc đang hoạt động."
                        if active
                        else "Công việc khả dụng.",
                    ),
                ),
                order=job_order,
                counts=(("setups", len(job.setups)),),
                capabilities=(Capability.OPEN, Capability.RENAME, Capability.DELETE),
                legacy=LegacySelection("job", str(job.job_id)),
                default_expanded=True,
            )
            for setup_order, setup in enumerate(job.setups):
                self._add_setup(
                    collector,
                    service,
                    session,
                    snapshot,
                    job,
                    setup,
                    job_node,
                    setup_order,
                    post_results,
                    nc_artifacts,
                    simulations,
                )

        nodes = collector.materialize()
        operations = tuple(item for item in nodes if item.kind is NodeKind.OPERATION)
        warning_count = sum(
            any(value.semantic is SemanticStatus.WARNING for value in item.statuses)
            for item in operations
        )
        error_count = sum(
            any(
                value.semantic in {SemanticStatus.BLOCKED, SemanticStatus.FAILED}
                for value in item.statuses
            )
            for item in operations
        )
        active_job = next(
            (item for item in snapshot.jobs if item.job_id == snapshot.active_job_id),
            None,
        )
        active_setup = active_job.active_setup if active_job is not None else None
        header = OperationManagerHeader(
            session.manifest.project_name,
            active_job.name if active_job is not None else "Chưa có công việc",
            (
                setup_display_name(active_setup.name)
                if active_setup is not None
                else "Chưa có thiết lập"
            ),
            setup_machine_name(snapshot, active_setup),
            operation_count,
            warning_count,
            error_count,
        )
        return OperationManagerProjection(project_id, (project_node,), nodes, header)

    def _add_setup(
        self,
        collector,
        service,
        session,
        snapshot,
        job,
        setup,
        job_node,
        setup_order,
        post_results,
        nc_artifacts,
        simulations,
    ) -> None:
        active = setup.setup_id == job.active_setup_id
        semantic = (
            SemanticStatus.DISABLED
            if not setup.enabled
            else SemanticStatus.ACTIVE
            if active
            else SemanticStatus.READY
        )
        setup_node = collector.add(
            NodeKind.SETUP,
            EntityKind.SETUP,
            setup.setup_id,
            job_node,
            setup_display_name(setup.name),
            f"{display_value(setup.kind, 'setup_kind')} · {setup.work_offset.name} · "
            f"{setup_machine_name(snapshot, setup)}",
            (
                status(
                    StatusCategory.DOMAIN,
                    semantic,
                    semantic.value.upper(),
                    "Thiết lập bị vô hiệu hóa."
                    if not setup.enabled
                    else "Thiết lập đang hoạt động."
                    if active
                    else "Thiết lập khả dụng.",
                ),
            ),
            enabled=setup.enabled,
            order=setup_order,
            counts=(("operations", len(setup.operation_tree.operations)),),
            capabilities=(
                Capability.OPEN,
                Capability.ADD_OPERATION,
                Capability.RENAME,
                Capability.DELETE,
            ),
            legacy=LegacySelection("setup", str(setup.setup_id)),
            default_expanded=True,
        )
        collector.add(
            NodeKind.GEOMETRY,
            EntityKind.GEOMETRY_REFERENCE,
            setup.model_reference.reference_id,
            setup_node,
            "Hình học",
            "Mô hình gia công theo nguồn dự án",
            (current_status(StatusCategory.DOMAIN, "Đã liên kết hình học"),),
            enabled=setup.enabled,
            order=0,
            search_terms=(setup.model_reference.source_id,),
        )
        collector.add(
            NodeKind.STOCK,
            EntityKind.STOCK,
            setup.setup_id,
            setup_node,
            "Phôi",
            stock_summary(setup.stock),
            (current_status(StatusCategory.DOMAIN, "Phôi hợp lệ"),),
            enabled=setup.enabled,
            order=1,
            legacy=LegacySelection("setup", str(setup.setup_id)),
        )
        self._add_tools(collector, snapshot, setup, setup_node)
        operations_node = collector.add(
            NodeKind.OPERATIONS,
            EntityKind.CAM_NODE,
            setup.operation_tree.root_id,
            setup_node,
            "Operations",
            f"{len(setup.operation_tree.operations)} nguyên công",
            (
                status(
                    StatusCategory.DOMAIN,
                    SemanticStatus.READY
                    if setup.operation_tree.operations
                    else SemanticStatus.DRAFT,
                    "SẴN SÀNG" if setup.operation_tree.operations else "BẢN NHÁP",
                    "Danh sách theo thứ tự dữ liệu hiện có."
                    if setup.operation_tree.operations
                    else "Thiết lập chưa có nguyên công.",
                ),
            ),
            enabled=setup.enabled,
            order=3,
            counts=(("operations", len(setup.operation_tree.operations)),),
            capabilities=(Capability.ADD_OPERATION,),
            default_expanded=True,
        )
        if not setup.operation_tree.operations:
            collector.add(
                NodeKind.EMPTY_STATE,
                EntityKind.SETUP,
                setup.setup_id,
                operations_node,
                "Chưa có nguyên công",
                "Dùng Thêm nguyên công đầu tiên để bắt đầu.",
                (
                    status(
                        StatusCategory.DOMAIN,
                        SemanticStatus.DRAFT,
                        "TRỐNG",
                        "Thiết lập hợp lệ nhưng chưa có nguyên công.",
                    ),
                ),
                capabilities=(Capability.ADD_OPERATION,),
                node_identity=f"{setup.setup_id}:operations-empty",
            )
        self._add_domain_children(
            collector,
            service,
            session,
            snapshot,
            setup,
            setup.operation_tree.root_id,
            operations_node,
            post_results,
            nc_artifacts,
            simulations,
        )
        if setup.operation_tree.operations:
            collector.add(
                NodeKind.PROGRAM_ASSEMBLY,
                EntityKind.PROGRAM_ASSEMBLY,
                setup.setup_id,
                setup_node,
            "Lắp ráp chương trình",
                f"{len(setup.operation_tree.operations)} nguyên công khả dụng",
                (
                    status(
                        StatusCategory.POST,
                        SemanticStatus.READY,
                        "READY",
                        "Mở Lắp ráp chương trình; không tự tạo hoặc xuất NC.",
                    ),
                ),
                enabled=setup.enabled,
                order=4,
                capabilities=(Capability.POST,),
            )

    @staticmethod
    def _add_tools(collector, snapshot, setup, setup_node) -> None:
        used_ids = {
            operation.tool_assembly.assembly_id
            for operation in setup.operation_tree.operations
        }
        tools = tuple(
            item for item in snapshot.tool_assemblies if item.assembly_id in used_ids
        )
        tools_node = collector.add(
            NodeKind.TOOLS,
            EntityKind.SETUP,
            setup.setup_id,
            setup_node,
            "Tools",
            f"{len(tools)} cụm Tool đang dùng",
            (
                status(
                    StatusCategory.DOMAIN,
                    SemanticStatus.READY,
                    "READY",
                    "Cụm Tool được nguyên công trong thiết lập tham chiếu.",
                ),
            ),
            enabled=setup.enabled,
            order=2,
            counts=(("tools", len(tools)),),
            node_identity=f"{setup.setup_id}:tools",
        )
        for order, tool in enumerate(tools):
            collector.add(
                NodeKind.TOOL,
                EntityKind.TOOL_ASSEMBLY,
                tool.assembly_id,
                tools_node,
                tool.name,
                f"Chiều nhô {tool.stickout.value:g} {tool.unit.value}",
                (current_status(StatusCategory.DOMAIN, "Cụm Tool hiện hành"),),
                order=order,
                search_terms=(tool.tool_id,),
                node_identity=f"{setup.setup_id}:{tool.assembly_id}",
            )

    def _add_domain_children(
        self,
        collector,
        service,
        session,
        snapshot,
        setup,
        domain_parent_id,
        projection_parent_id,
        post_results,
        nc_artifacts,
        simulations,
    ) -> None:
        tree = setup.operation_tree
        for order, child_id in enumerate(tree.get_node(domain_parent_id).child_ids):
            child = tree.get_node(child_id)
            if child.operation_id is not None:
                self._add_operation(
                    collector,
                    service,
                    session,
                    snapshot,
                    setup,
                    child,
                    tree.get_operation(child.operation_id),
                    projection_parent_id,
                    order,
                    post_results,
                    nc_artifacts,
                    simulations,
                )
                continue
            group_node = collector.add(
                NodeKind.GROUP,
                EntityKind.CAM_NODE,
                child.node_id,
                projection_parent_id,
                child.name,
                f"{len(child.child_ids)} mục",
                (
                    status(
                        StatusCategory.DOMAIN,
                        SemanticStatus.READY if child.enabled else SemanticStatus.DISABLED,
                        "SẴN SÀNG" if child.enabled else "ĐÃ TẮT",
                        "Nhóm nguyên công khả dụng."
                        if child.enabled
                        else "Nhóm nguyên công bị vô hiệu hóa.",
                    ),
                ),
                enabled=child.enabled,
                order=order,
                counts=(("children", len(child.child_ids)),),
                capabilities=(
                    Capability.ADD_OPERATION,
                    Capability.RENAME,
                    Capability.DELETE,
                    Capability.MOVE_UP,
                    Capability.MOVE_DOWN,
                ),
                legacy=LegacySelection("group", str(child.node_id)),
            )
            self._add_domain_children(
                collector,
                service,
                session,
                snapshot,
                setup,
                child.node_id,
                group_node,
                post_results,
                nc_artifacts,
                simulations,
            )

    def _add_operation(
        self,
        collector,
        service,
        session,
        snapshot,
        setup,
        cam_node,
        operation,
        parent_node,
        order,
        post_results,
        nc_artifacts,
        simulations,
    ) -> None:
        assembly = next(
            (
                item
                for item in snapshot.tool_assemblies
                if item.assembly_id == operation.tool_assembly.assembly_id
            ),
            None,
        )
        assessed_tool = operation.tool_assembly.assess(assembly)
        calculation = calculation_status(operation, assessed_tool)
        parallel_artifact = (
            service.load_toolpath_artifact(operation.operation_id)
            if operation.strategy_key
            in {"parallel_finishing_3d", "z_level_finishing_3d"}
            else None
        )
        safety = (
            z_level_safety_status(operation, parallel_artifact)
            if operation.strategy_key == "z_level_finishing_3d"
            else parallel_safety_status(operation, parallel_artifact)
        )
        simulation = simulation_status(
            service, operation, simulations.get(operation.operation_id)
        )
        post = post_status(operation, post_results)
        nc, export = nc_status(service, session, operation, nc_artifacts)
        tool_name = assembly.name if assembly is not None else "Chưa có dao"
        operation_type = (
            "Planar Face Facing"
            if operation.strategy_key == "facing_2_5d" and operation.geometry_inputs
            else operation.strategy_key
        )
        strategy_name = operation_type_display_name(operation_type)
        display_name = operation_display_name(
            cam_node.name,
            strategy_key=operation_type,
        )
        capabilities = [
            Capability.OPEN,
            Capability.RENAME,
            Capability.DELETE,
            Capability.DUPLICATE,
            Capability.MOVE_UP,
            Capability.MOVE_DOWN,
            Capability.BIND_GEOMETRY,
            Capability.CLEAR_GEOMETRY,
            Capability.TOGGLE_TOOLPATH,
            Capability.POST,
            Capability.DISABLE if operation.enabled else Capability.ENABLE,
        ]
        if operation.strategy_key in _SUPPORTED_STRATEGIES:
            capabilities.append(Capability.RECALCULATE)
        if operation.artifact_state.status is ArtifactStatus.VALID and (
            safety is None or safety.semantic is SemanticStatus.CURRENT
        ):
            capabilities.append(Capability.SIMULATE)
        legacy = LegacySelection("operation", str(cam_node.node_id))
        if operation.strategy_key == "z_level_finishing_3d":
            # Keep the production row compact and truthful: custom operation
            # names stay primary while the secondary line exposes the minimum
            # Z-Level evidence needed for review.
            secondary_summary = (
                f"Tool cầu {tool_name} · {len(operation.geometry_inputs)} mặt · "
                f"Tính {calculation.text} · "
                f"An toàn {safety.text if safety is not None else 'Chưa kiểm tra'} · "
                f"Mô phỏng {simulation.text} · NC {nc.text}"
            )
        else:
            secondary_summary = (
                f"{strategy_name} · {tool_name} · Đường dao {calculation.text} · "
                f"An toàn {safety.text if safety is not None else 'N/A'} · "
                f"Mô phỏng {simulation.text} · NC {nc.text}"
            )
        operation_node = collector.add(
            NodeKind.OPERATION,
            EntityKind.OPERATION,
            operation.operation_id,
            parent_node,
            display_name,
            secondary_summary,
            (
                operation_status(operation, assessed_tool, calculation),
                calculation,
                *((safety,) if safety is not None else ()),
                simulation,
                post,
                nc,
                export,
            ),
            enabled=operation.enabled,
            order=order,
            counts=(
                ("geometry", len(operation.geometry_inputs)),
                ("diagnostics", len(operation.diagnostics)),
            ),
            capabilities=capabilities,
            search_terms=(
                operation.strategy_key,
                strategy_name,
                tool_name,
                operation.operation_id,
                operation.node_id,
                _STRATEGY_SEARCH_ALIASES.get(operation.strategy_key, ""),
                _STATUS_SEARCH_ALIASES.get(calculation.semantic, ""),
            ),
            legacy=legacy,
            node_identity=cam_node.node_id,
        )
        self._add_operation_details(
            collector,
            snapshot,
            operation,
            operation_node,
            legacy,
            assembly,
            tool_name,
            calculation,
            simulation,
            post,
            nc,
            export,
            post_results,
            nc_artifacts,
            simulations,
        )

    @staticmethod
    def _add_operation_details(
        collector,
        snapshot,
        operation,
        operation_node,
        legacy,
        assembly,
        tool_name,
        calculation,
        simulation,
        post,
        nc,
        export,
        post_results,
        nc_artifacts,
        simulations,
    ) -> None:
        geometry_missing = (
            not operation.geometry_inputs
            and operation.strategy_key
            in {
                "contour_2d",
                "pocket_2_5d",
                "rest_pocket_3axis",
                "parallel_finishing_3d",
                "z_level_finishing_3d",
            }
        )
        geometry_identity = (
            operation.geometry_inputs[0].reference.reference_id
            if operation.geometry_inputs
            else operation.operation_id
        )
        collector.add(
            NodeKind.OPERATION_GEOMETRY,
            EntityKind.GEOMETRY_REFERENCE,
            geometry_identity,
            operation_node,
            "Hình học",
            f"{len(operation.geometry_inputs)} liên kết"
            if operation.geometry_inputs
            else "Kế thừa Thiết lập/mô hình hoặc tham số chiến lược",
            (
                status(
                    StatusCategory.DOMAIN,
                    SemanticStatus.NEEDS_INPUT
                    if geometry_missing
                    else SemanticStatus.CURRENT,
                    "CẦN DỮ LIỆU" if geometry_missing else "HIỆN HÀNH",
                    "Nguyên công cần liên kết hình học."
                    if geometry_missing
                    else "Hình học đến từ nguyên công hoặc Thiết lập/mô hình.",
                ),
            ),
            enabled=operation.enabled,
            order=0,
            capabilities=(
                Capability.OPEN,
                Capability.BIND_GEOMETRY,
                Capability.CLEAR_GEOMETRY,
            ),
            legacy=legacy,
            node_identity=operation.operation_id,
        )
        collector.add(
            NodeKind.OPERATION_TOOL,
            EntityKind.TOOL_ASSEMBLY,
            operation.tool_assembly.assembly_id,
            operation_node,
            "Tool",
            tool_name,
            (tool_status(operation.tool_assembly.assess(assembly)),),
            enabled=operation.enabled,
            order=1,
            search_terms=(tool_name, operation.tool_assembly.assembly_id),
            legacy=legacy,
            node_identity=operation.operation_id,
        )
        artifact = next(
            (
                item
                for item in snapshot.artifacts
                if item.operation_id == operation.operation_id
            ),
            None,
        )
        collector.add(
            NodeKind.TOOLPATH,
            EntityKind.TOOLPATH_ARTIFACT,
            artifact.artifact_id if artifact is not None else operation.operation_id,
            operation_node,
            "Đường chạy dao",
            dirty_reason_summary(operation),
            (calculation,),
            enabled=operation.enabled,
            order=2,
            capabilities=(
                Capability.RECALCULATE,
                Capability.TOGGLE_TOOLPATH,
            ),
            legacy=legacy,
            node_identity=operation.operation_id,
        )
        simulation_result = simulations.get(operation.operation_id)
        collector.add(
            NodeKind.SIMULATION,
            EntityKind.SIMULATION_RESULT,
            simulation_result.result_id
            if simulation_result is not None
            else operation.operation_id,
            operation_node,
            "Mô phỏng",
            simulation.tooltip,
            (simulation,),
            enabled=operation.enabled,
            order=3,
            capabilities=(Capability.SIMULATE, Capability.CLEAR_SIMULATION),
            legacy=legacy,
            node_identity=operation.operation_id,
        )
        post_result = next(
            (
                item
                for item in reversed(post_results)
                if item.operation_id == operation.operation_id
            ),
            None,
        )
        collector.add(
            NodeKind.POST_RESULT,
            EntityKind.POST_RESULT,
            post_result.result_id if post_result is not None else operation.operation_id,
            operation_node,
            "Kết quả Post",
            post.tooltip,
            (post,),
            enabled=operation.enabled,
            order=4,
            capabilities=(Capability.POST, Capability.CLEAR_POST),
            legacy=legacy,
            node_identity=operation.operation_id,
        )
        nc_artifact = next(
            (
                item
                for item in nc_artifacts
                if item.operation_id == operation.operation_id
            ),
            None,
        )
        collector.add(
            NodeKind.NC_ARTIFACT,
            EntityKind.NC_ARTIFACT,
            nc_artifact.artifact_id
            if nc_artifact is not None
            else operation.operation_id,
            operation_node,
            "NC Artifact",
            nc_artifact.output_relative_path
            if nc_artifact is not None
            else "Chưa có artifact NC được quản lý",
            (nc, export),
            enabled=operation.enabled,
            order=5,
            capabilities=(Capability.POST, Capability.CLEAR_NC),
            legacy=legacy,
            node_identity=operation.operation_id,
        )


def _no_project() -> OperationManagerProjection:
    node_id = NodeId("no-project:empty_state")
    node = OperationManagerNode(
        node_id=node_id,
        domain_identity=DomainIdentity(EntityKind.PROJECT, "no-project"),
        parent_id=None,
        kind=NodeKind.EMPTY_STATE,
        label="Chưa mở dự án CAM",
        secondary_summary="Tạo hoặc mở thư mục dự án CAM để bắt đầu.",
        statuses=(
            status(
                StatusCategory.DOMAIN,
                SemanticStatus.MISSING,
                "CHƯA CÓ DỰ ÁN",
                "Quản lý nguyên công chưa có ngữ cảnh dự án.",
            ),
        ),
        enabled=False,
        order=0,
    )
    return OperationManagerProjection(
        None,
        (node_id,),
        (node,),
        OperationManagerHeader(
            "Chưa mở dự án CAM",
            "Chưa có công việc",
            "Chưa có thiết lập",
            "Chưa gán máy",
            0,
            0,
            0,
        ),
    )
