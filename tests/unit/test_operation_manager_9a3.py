"""Stage 9A.3 Operation Manager projection, model, action and lifecycle tests."""

from __future__ import annotations

import os
from dataclasses import fields, replace
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractItemModel, QCoreApplication, QEvent, QRect, QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleOptionViewItem  # noqa: E402

from hms_cadcam.cam.domain import (  # noqa: E402
    ArtifactState,
    ArtifactStatus,
    CamNodeId,
    OperationId,
    ToolReferenceStatus,
)
from hms_cadcam.cam.post import (  # noqa: E402
    NCArtifactStatus,
    NCExportStatus,
    PostResultStatus,
)
from hms_cadcam.cam.simulation import SimulationStatus  # noqa: E402
from hms_cadcam.cam.simulation.runtime import SimulationRunState  # noqa: E402
from hms_cadcam.project.service import ProjectService  # noqa: E402
from hms_cadcam.ui.cam_ui import CamWorkspace  # noqa: E402
from hms_cadcam.ui.operation_manager import (  # noqa: E402
    OperationManagerPanel,
    operation_manager_context_summary,
)
from hms_cadcam.ui.operation_manager_delegate import (  # noqa: E402
    OperationManagerDelegate,
    compact_operation_summary,
)
from hms_cadcam.ui.operation_manager_model import OperationManagerModel  # noqa: E402
from hms_cadcam.ui.operation_manager_projection import (  # noqa: E402
    OperationManagerProjectionBuilder,
)
from hms_cadcam.ui.operation_manager_status import (  # noqa: E402
    calculation_status,
    nc_status,
    post_status,
    simulation_status,
)
from hms_cadcam.ui.operation_manager_types import (  # noqa: E402
    OperationManagerFilter,
    OperationManagerHeader,
    OperationManagerNode,
    OperationManagerNodeKind,
    OperationManagerSemanticStatus,
    OperationManagerStatus,
    OperationManagerStatusCategory,
    count_operation_nodes,
)
from tools.audit_vietnamese_ui import (  # noqa: E402
    duplicate_user_facing_phrase_matches,
    raw_user_facing_internal_matches,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _environment(tmp_path, *, operation_count: int = 1):
    application = _application()
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "Manager 9A3")
    workspace = CamWorkspace(service, uuid4)
    settings = QSettings(str(tmp_path / "operation_manager.ini"), QSettings.Format.IniFormat)
    panel = OperationManagerPanel(
        workspace,
        service,
        settings,
        workspace.actions,
    )
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_resources()
    workspace.add_operation()
    if operation_count > 1:
        _clone_operations(service, operation_count)
        workspace.refresh()
    application.processEvents()
    return application, service, workspace, panel, settings


def _dispose(service, workspace, panel, application) -> None:
    panel.close()
    workspace.close()
    panel.deleteLater()
    workspace.deleteLater()
    if service.has_project:
        service.close_project(discard_changes=True)
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _node(panel, kind: OperationManagerNodeKind) -> OperationManagerNode:
    return next(item for item in panel.model.projection.nodes if item.kind is kind)


def _operation_nodes(panel) -> tuple[OperationManagerNode, ...]:
    return tuple(
        item
        for item in panel.model.projection.nodes
        if item.kind is OperationManagerNodeKind.OPERATION
    )


def _clone_operations(service: ProjectService, count: int) -> None:
    snapshot = service.cam_snapshot
    job = snapshot.jobs[0]
    setup = job.setups[0]
    base = setup.operation_tree.operations[0]

    def command(app):
        def mutate(tree):
            changed = tree
            for index in range(1, count):
                operation = replace(
                    base,
                    operation_id=OperationId.new(),
                    node_id=CamNodeId.new(),
                    revision=base.revision,
                    artifact_state=ArtifactState(),
                )
                changed = changed.add_operation(
                    changed.root_id, f"Facing {index + 1:03d}", operation
                )
            return changed

        return app.update_tree(job.job_id, setup.setup_id, mutate)

    service.execute_cam_command(command)


@pytest.mark.parametrize(
    ("job_name", "setup_name"),
    (
        ("Công việc 1", "Thiết lập 1"),
        ("Công việc gia công cao độ Z", "Thiết lập cao độ Z"),
        ("Khuôn lõi khách hàng", "Gá lần hai"),
        ("Công việc đặc biệt", "Gá mặt sau"),
        ("Lõi trên", "Thiết lập tinh"),
        ("Lõi dưới", "Gá phụ"),
    ),
)
def test_context_summary_preserves_job_and_setup_names_without_prefixes(
    job_name: str,
    setup_name: str,
) -> None:
    header = OperationManagerHeader(
        "Mẫu gia công cao độ Z",
        job_name,
        setup_name,
        "Chưa gán máy",
        1,
        0,
        0,
    )

    summary = operation_manager_context_summary(header)

    assert summary == f"{job_name} · {setup_name} · Chưa gán máy"
    assert not duplicate_user_facing_phrase_matches(summary)


def test_projection_has_complete_hierarchy_stable_ids_and_no_qt_state(tmp_path) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        first = panel.model.projection
        second = OperationManagerProjectionBuilder().build(
            service, service.current_project
        )
        assert tuple(item.node_id for item in first.nodes) == tuple(
            item.node_id for item in second.nodes
        )
        assert len({item.node_id for item in first.nodes}) == len(first.nodes)
        kinds = {item.kind for item in first.nodes}
        assert {
            OperationManagerNodeKind.PROJECT,
            OperationManagerNodeKind.JOB,
            OperationManagerNodeKind.SETUP,
            OperationManagerNodeKind.GEOMETRY,
            OperationManagerNodeKind.STOCK,
            OperationManagerNodeKind.TOOLS,
            OperationManagerNodeKind.TOOL,
            OperationManagerNodeKind.OPERATIONS,
            OperationManagerNodeKind.OPERATION,
            OperationManagerNodeKind.OPERATION_GEOMETRY,
            OperationManagerNodeKind.OPERATION_TOOL,
            OperationManagerNodeKind.TOOLPATH,
            OperationManagerNodeKind.SIMULATION,
            OperationManagerNodeKind.POST_RESULT,
            OperationManagerNodeKind.NC_ARTIFACT,
            OperationManagerNodeKind.PROGRAM_ASSEMBLY,
        }.issubset(kinds)
        by_id = {item.node_id: item for item in first.nodes}
        for item in first.nodes:
            assert item.domain_identity.value
            assert item.order >= 0
            assert all(child in by_id for child in item.children)
            assert not any(
                token in field.name
                for field in fields(item)
                for token in ("qmodelindex", "qobject", "callback", "widget", "ocp")
            )
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        assert tuple(by_id[item].kind for item in operation.children) == (
            OperationManagerNodeKind.OPERATION_GEOMETRY,
            OperationManagerNodeKind.OPERATION_TOOL,
            OperationManagerNodeKind.TOOLPATH,
            OperationManagerNodeKind.SIMULATION,
            OperationManagerNodeKind.POST_RESULT,
            OperationManagerNodeKind.NC_ARTIFACT,
        )
    finally:
        _dispose(service, workspace, panel, application)


def test_no_project_and_cad_only_have_honest_empty_states(tmp_path) -> None:
    application = _application()
    service = ProjectService.create_default(tmp_path / "config")
    workspace = CamWorkspace(service, uuid4)
    settings = QSettings(
        str(tmp_path / "operation_manager.ini"), QSettings.Format.IniFormat
    )
    panel = OperationManagerPanel(workspace, service, settings, workspace.actions)
    try:
        assert panel.model.projection.project_id is None
        assert panel.current_node().kind is OperationManagerNodeKind.EMPTY_STATE
        assert panel.state_title.text() == "Chưa mở dự án"

        session = service.new_project(tmp_path, "CAD Only")
        workspace.bind_project(session)
        application.processEvents()
        assert panel.model.projection.project_id == session.manifest.project_id
        assert not any(
            item.kind is OperationManagerNodeKind.JOB
            for item in panel.model.projection.nodes
        )
        assert panel.state_title.text() == "Dự án hiện chỉ có CAD"
        assert any(
            item.kind is OperationManagerNodeKind.EMPTY_STATE
                and item.status.text == "CHỈ CÓ CAD"
            for item in panel.model.projection.nodes
        )
    finally:
        _dispose(service, workspace, panel, application)


def test_duplicate_display_names_keep_distinct_typed_identity(tmp_path) -> None:
    application, service, workspace, panel, _settings = _environment(
        tmp_path, operation_count=2
    )
    try:
        snapshot = service.cam_snapshot
        job, setup = snapshot.jobs[0], snapshot.jobs[0].setups[0]
        operation_ids = tuple(
            item.node_id for item in setup.operation_tree.operations
        )
        service.execute_cam_command(
            lambda app: app.update_tree(
                job.job_id,
                setup.setup_id,
                lambda tree: tree.rename_node(
                    operation_ids[0], "Tên trùng"
                ).rename_node(operation_ids[1], "Tên trùng"),
            )
        )
        workspace.refresh()
        operations = _operation_nodes(panel)
        assert tuple(item.label for item in operations) == ("Tên trùng", "Tên trùng")
        assert len({item.node_id for item in operations}) == 2
        assert len({item.domain_identity for item in operations}) == 2
    finally:
        _dispose(service, workspace, panel, application)


def test_project_switch_drops_old_selection_and_identity(tmp_path) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        old_project_id = panel.model.projection.project_id
        old_operation = _node(panel, OperationManagerNodeKind.OPERATION)
        panel.view.setCurrentIndex(
            panel.model.index_for_node_id(old_operation.node_id)
        )
        service.close_project(discard_changes=True)
        session = service.new_project(tmp_path, "Second Project")
        workspace.bind_project(session)
        application.processEvents()
        assert panel.model.projection.project_id != old_project_id
        assert panel.model.projection.project_id == session.manifest.project_id
        assert panel.model.projection.node(old_operation.node_id) is None
        assert panel.current_node().node_id != old_operation.node_id
    finally:
        _dispose(service, workspace, panel, application)


def test_status_categories_filters_and_operation_summary_are_honest(tmp_path) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        assert {item.category for item in operation.statuses} == {
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerStatusCategory.CALCULATION,
            OperationManagerStatusCategory.SIMULATION,
            OperationManagerStatusCategory.POST,
            OperationManagerStatusCategory.NC,
            OperationManagerStatusCategory.EXPORT,
        }
        assert "Phay mặt 2.5D" in operation.secondary_summary
        assert "Đường dao CẦN TÍNH" in operation.secondary_summary
        assert "Mô phỏng CHƯA CHẠY" in operation.secondary_summary
        assert "NC THIẾU" in operation.secondary_summary

        workspace.set_selected_enabled(False)
        application.processEvents()
        disabled = _node(panel, OperationManagerNodeKind.OPERATION)
        assert disabled.status.semantic is OperationManagerSemanticStatus.DISABLED
        panel.model.set_status_filter(OperationManagerFilter.DISABLED)
        assert panel.model.index_for_node_id(disabled.node_id).isValid()

        workspace.set_selected_enabled(True)
        snapshot = service.cam_snapshot
        job, setup = snapshot.jobs[0], snapshot.jobs[0].setups[0]
        current = setup.operation_tree.operations[0]
        dirty = replace(
            current,
            artifact_state=ArtifactState(status=ArtifactStatus.DIRTY),
        )
        service.execute_cam_command(
            lambda app: app.update_tree(
                job.job_id,
                setup.setup_id,
                lambda tree: tree.replace_operation(dirty),
            )
        )
        workspace.refresh()
        panel.model.set_status_filter(OperationManagerFilter.STALE)
        stale = _node(panel, OperationManagerNodeKind.OPERATION)
        assert stale.is_stale
        assert panel.model.index_for_node_id(stale.node_id).isValid()
    finally:
        _dispose(service, workspace, panel, application)


def test_operation_rows_use_localized_two_line_names_tooltips_badges_and_dpi(
    tmp_path,
) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        panel.resize(300, 680)
        panel.show()
        application.processEvents()
        assert {
            key: workspace.actions[key].text()
            for key in (
                "operation",
                "contour_operation",
                "pocket_operation",
                "parallel_operation",
                "drilling_operation",
                "tapping_operation",
                "reaming_operation",
                "boring_operation",
            )
        } == {
            "operation": "Thêm Phay mặt 2.5D",
            "contour_operation": "Thêm Phay biên dạng 2D",
            "pocket_operation": "Thêm Phay hốc 2.5D",
            "parallel_operation": "Thêm Gia công tinh song song",
            "drilling_operation": "Thêm Khoan",
            "tapping_operation": "Thêm Taro",
            "reaming_operation": "Thêm Doa lỗ",
            "boring_operation": "Thêm Khoét lỗ",
        }
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        index = panel.model.index_for_node_id(operation.node_id)
        tool_name = service.cam_snapshot.tool_assemblies[0].name
        assert operation.label == "Phay mặt 2.5D"
        assert compact_operation_summary(operation) == (
            f"Phay mặt 2.5D · {tool_name}"
        )
        tooltip = str(panel.model.data(index, Qt.ItemDataRole.ToolTipRole))
        accessible = str(
            panel.model.data(index, Qt.ItemDataRole.AccessibleTextRole)
        )
        assert "Phay mặt 2.5D" in tooltip
        assert tool_name in tooltip
        assert "Trạng thái" in accessible
        status_index = index.siblingAtColumn(1)
        assert panel.model.data(status_index, Qt.ItemDataRole.DisplayRole)
        assert panel.view.header().sectionSize(1) == 70
        assert panel.view.indentation() == 10
        assert panel.view.horizontalScrollBar().maximum() == 0

        delegate = panel.view.itemDelegate()
        assert isinstance(delegate, OperationManagerDelegate)
        base_option = QStyleOptionViewItem()
        base_option.rect = QRect(0, 0, 150, 40)
        base_option.font = panel.view.font()
        base_height = delegate.sizeHint(base_option, index).height()
        assert 38 <= base_height <= 44
        short = base_option.fontMetrics.elidedText(
            "Khoan", Qt.TextElideMode.ElideRight, 80
        )
        long_name = base_option.fontMetrics.elidedText(
            "Gia công tinh song song", Qt.TextElideMode.ElideRight, 120
        )
        assert short == "Khoan"
        assert len(long_name.rstrip("…’")) >= 7
        for scale in (1.25, 1.5):
            option = QStyleOptionViewItem(base_option)
            option.font.setPointSizeF(panel.view.font().pointSizeF() * scale)
            assert delegate.sizeHint(option, index).height() >= base_height

        snapshot = service.cam_snapshot
        job, setup = snapshot.jobs[0], snapshot.jobs[0].setups[0]
        operation_node_id = setup.operation_tree.operations[0].node_id
        service.execute_cam_command(
            lambda app: app.update_tree(
                job.job_id,
                setup.setup_id,
                lambda tree: tree.rename_node(
                    operation_node_id,
                    "Tinh mặt khuôn rất dài theo yêu cầu khách hàng",
                ),
            )
        )
        workspace.refresh()
        custom = _node(panel, OperationManagerNodeKind.OPERATION)
        custom_index = panel.model.index_for_node_id(custom.node_id)
        custom_tooltip = str(
            panel.model.data(custom_index, Qt.ItemDataRole.ToolTipRole)
        )
        assert custom.label == "Tinh mặt khuôn rất dài theo yêu cầu khách hàng"
        assert "Phay mặt 2.5D" in custom.secondary_summary
        assert custom.label in custom_tooltip
    finally:
        _dispose(service, workspace, panel, application)


def test_project_tree_model_tooltip_and_accessibility_hide_internal_tokens(
    tmp_path,
) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        project = _node(panel, OperationManagerNodeKind.PROJECT)
        job = _node(panel, OperationManagerNodeKind.JOB)
        setup = _node(panel, OperationManagerNodeKind.SETUP)
        geometry = _node(panel, OperationManagerNodeKind.GEOMETRY)
        stock = _node(panel, OperationManagerNodeKind.STOCK)
        tools = _node(panel, OperationManagerNodeKind.TOOLS)
        tool = _node(panel, OperationManagerNodeKind.TOOL)
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        toolpath = _node(panel, OperationManagerNodeKind.TOOLPATH)

        assert job.secondary_summary == "1 thiết lập"
        assert setup.label == "Thiết lập 1"
        assert setup.secondary_summary.startswith("PHAY ·")
        assert geometry.status.tooltip == "Đã liên kết hình học"
        assert stock.label == "Phôi"
        assert stock.secondary_summary.startswith("Khối hộp ·")
        assert stock.status.tooltip == "Phôi hợp lệ"
        assert tools.secondary_summary == "1 cụm Tool đang dùng"
        assert tool.secondary_summary.startswith("Chiều nhô ")
        assert toolpath.secondary_summary == "Thiếu kết quả tính toán"

        model_texts: list[str] = []
        for node in panel.model.projection.nodes:
            index = panel.model.index_for_node_id(node.node_id)
            if not index.isValid():
                continue
            for column in range(panel.model.columnCount(index.parent())):
                current = index.siblingAtColumn(column)
                for role in (
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole,
                    Qt.ItemDataRole.AccessibleDescriptionRole,
                ):
                    value = panel.model.data(current, role)
                    if value:
                        model_texts.append(str(value))

        project_tooltip = str(
            panel.model.data(
                panel.model.index_for_node_id(project.node_id),
                Qt.ItemDataRole.ToolTipRole,
            )
        )
        setup_tooltip = str(
            panel.model.data(
                panel.model.index_for_node_id(setup.node_id),
                Qt.ItemDataRole.ToolTipRole,
            )
        )
        operation_tooltip = str(
            panel.model.data(
                panel.model.index_for_node_id(operation.node_id),
                Qt.ItemDataRole.ToolTipRole,
            )
        )
        assert "DỰ ÁN ·" in project_tooltip
        assert "TRẠNG THÁI ·" in setup_tooltip
        assert "TÍNH TOÁN ·" in operation_tooltip
        assert "MÔ PHỎNG ·" in operation_tooltip
        assert "XUẤT NC ·" in operation_tooltip
        assert all(
            not raw_user_facing_internal_matches(text)
            for text in model_texts
        )
    finally:
        _dispose(service, workspace, panel, application)


def test_narrow_operation_manager_preserves_primary_name_before_metadata(
    tmp_path,
) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        panel.view.setFixedWidth(260)
        panel.resize(424, 600)
        panel.show()
        for _ in range(3):
            application.processEvents()
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        index = panel.model.index_for_node_id(operation.node_id)
        first_column = panel.view.header().sectionSize(0)
        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()
        readable_width = first_column - depth * panel.view.indentation() - 25
        assert panel.view.indentation() == 6
        assert panel.view.header().sectionSize(1) <= 64
        assert readable_width >= panel.view.fontMetrics().horizontalAdvance(
            "Gia công"
        )
        assert panel.view.horizontalScrollBar().maximum() == 0
        assert operation.label in str(
            panel.model.data(index, Qt.ItemDataRole.ToolTipRole)
        )
    finally:
        _dispose(service, workspace, panel, application)


def test_enabled_needs_calculation_warning_and_error_filters(tmp_path) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        project = _node(panel, OperationManagerNodeKind.PROJECT)
        for selected_filter in (
            OperationManagerFilter.ENABLED,
            OperationManagerFilter.NEEDS_CALCULATION,
        ):
            panel.model.set_status_filter(selected_filter)
            assert panel.model.index_for_node_id(operation.node_id).isValid()
            assert panel.model.index_for_node_id(project.node_id).isValid()

        for selected_filter, semantic in (
            (
                OperationManagerFilter.WARNINGS,
                OperationManagerSemanticStatus.WARNING,
            ),
            (
                OperationManagerFilter.ERRORS,
                OperationManagerSemanticStatus.FAILED,
            ),
        ):
            status = OperationManagerStatus(
                OperationManagerStatusCategory.DOMAIN,
                semantic,
                semantic.value.upper(),
                "Status fixture Stage 9A.3",
            )
            changed = replace(operation, statuses=(status,))
            projection = replace(
                panel.model.projection,
                nodes=tuple(
                    changed if item.node_id == operation.node_id else item
                    for item in panel.model.projection.nodes
                ),
            )
            panel.model.set_projection(projection)
            panel.model.set_status_filter(selected_filter)
            assert panel.model.index_for_node_id(operation.node_id).isValid()
            assert panel.model.index_for_node_id(project.node_id).isValid()

        panel.model.set_status_filter(OperationManagerFilter.ALL)
        assert panel.model.visible_node_count() == len(panel.model.projection.nodes)
    finally:
        _dispose(service, workspace, panel, application)


@pytest.mark.parametrize(
    ("artifact_status", "tool_status", "semantic", "text"),
    [
        (
            ArtifactStatus.MISSING,
            ToolReferenceStatus.VALID,
            OperationManagerSemanticStatus.DRAFT,
            "CẦN TÍNH",
        ),
        (
            ArtifactStatus.MISSING,
            ToolReferenceStatus.MISSING,
            OperationManagerSemanticStatus.NEEDS_INPUT,
            "CẦN DỮ LIỆU",
        ),
        (
            ArtifactStatus.COMPUTING,
            ToolReferenceStatus.VALID,
            OperationManagerSemanticStatus.CALCULATING,
            "ĐANG TÍNH",
        ),
        (
            ArtifactStatus.VALID,
            ToolReferenceStatus.VALID,
            OperationManagerSemanticStatus.CURRENT,
            "HIỆN HÀNH",
        ),
        (
            ArtifactStatus.DIRTY,
            ToolReferenceStatus.VALID,
            OperationManagerSemanticStatus.STALE,
            "ĐÃ LỖI THỜI",
        ),
        (
            ArtifactStatus.FAILED,
            ToolReferenceStatus.VALID,
            OperationManagerSemanticStatus.FAILED,
            "THẤT BẠI",
        ),
    ],
)
def test_calculation_status_matrix(
    artifact_status, tool_status, semantic, text
) -> None:
    operation = SimpleNamespace(
        artifact_state=SimpleNamespace(status=artifact_status)
    )
    mapped = calculation_status(operation, tool_status)
    assert (mapped.semantic, mapped.text) == (semantic, text)


@pytest.mark.parametrize(
    ("result_status", "semantic", "text"),
    [
        (
            SimulationStatus.PASS,
            OperationManagerSemanticStatus.CURRENT,
            "HIỆN HÀNH",
        ),
        (
            SimulationStatus.WARN,
            OperationManagerSemanticStatus.WARNING,
            "CẢNH BÁO",
        ),
        (
            SimulationStatus.FAIL,
            OperationManagerSemanticStatus.FAILED,
            "THẤT BẠI",
        ),
    ],
)
def test_simulation_result_status_matrix(result_status, semantic, text) -> None:
    operation_id = OperationId.new()
    operation = SimpleNamespace(
        operation_id=operation_id,
        artifact_state=SimpleNamespace(
            status=ArtifactStatus.VALID,
            artifact_fingerprint="current-fingerprint",
        ),
    )
    service = SimpleNamespace(
        simulation_runs=SimpleNamespace(record=lambda _operation_id: None)
    )
    result = SimpleNamespace(
        artifact_fingerprint="current-fingerprint",
        status=result_status,
    )
    mapped = simulation_status(service, operation, result)
    assert (mapped.semantic, mapped.text) == (semantic, text)


@pytest.mark.parametrize(
    ("run_state", "semantic", "text"),
    [
        (
            SimulationRunState.RUNNING,
            OperationManagerSemanticStatus.CALCULATING,
            "ĐANG CHẠY",
        ),
        (
            SimulationRunState.STALE,
            OperationManagerSemanticStatus.STALE,
            "ĐÃ LỖI THỜI",
        ),
        (
            SimulationRunState.FAILED,
            OperationManagerSemanticStatus.FAILED,
            "THẤT BẠI",
        ),
    ],
)
def test_simulation_runtime_status_matrix(run_state, semantic, text) -> None:
    operation = SimpleNamespace(operation_id=OperationId.new())
    record = SimpleNamespace(
        state=run_state,
        diagnostic_message="runtime diagnostic",
    )
    service = SimpleNamespace(
        simulation_runs=SimpleNamespace(record=lambda _operation_id: record)
    )
    mapped = simulation_status(service, operation, None)
    assert (mapped.semantic, mapped.text) == (semantic, text)


@pytest.mark.parametrize(
    ("result_status", "semantic"),
    [
        (None, OperationManagerSemanticStatus.MISSING),
        (PostResultStatus.PUBLISHED, OperationManagerSemanticStatus.CURRENT),
        (PostResultStatus.STALE, OperationManagerSemanticStatus.STALE),
        (PostResultStatus.BLOCKED, OperationManagerSemanticStatus.BLOCKED),
        (PostResultStatus.FAILED, OperationManagerSemanticStatus.FAILED),
        (PostResultStatus.CANCELLED, OperationManagerSemanticStatus.DRAFT),
    ],
)
def test_post_status_matrix(result_status, semantic) -> None:
    operation = SimpleNamespace(operation_id=OperationId.new())
    results = (
        ()
        if result_status is None
        else (SimpleNamespace(operation_id=operation.operation_id, status=result_status),)
    )
    mapped = post_status(operation, results)
    assert mapped.semantic is semantic


@pytest.mark.parametrize(
    ("artifact_status", "semantic"),
    [
        (None, OperationManagerSemanticStatus.MISSING),
        (NCArtifactStatus.CURRENT, OperationManagerSemanticStatus.CURRENT),
        (NCArtifactStatus.STALE, OperationManagerSemanticStatus.STALE),
        (NCArtifactStatus.MISSING, OperationManagerSemanticStatus.MISSING),
        (NCArtifactStatus.TAMPERED, OperationManagerSemanticStatus.FAILED),
    ],
)
def test_nc_artifact_status_matrix(artifact_status, semantic) -> None:
    operation = SimpleNamespace(operation_id=OperationId.new())
    artifacts = (
        ()
        if artifact_status is None
        else (
            SimpleNamespace(
                operation_id=operation.operation_id,
                status=artifact_status,
                output_relative_path="nc/result.nc",
            ),
        )
    )
    service = SimpleNamespace(
        nc_export_service=SimpleNamespace(current=lambda *_args: None)
    )
    session = SimpleNamespace(manifest=SimpleNamespace(project_id=uuid4()))
    mapped, _export = nc_status(service, session, operation, artifacts)
    assert mapped.semantic is semantic


@pytest.mark.parametrize(
    ("export_status", "semantic"),
    [
        (None, OperationManagerSemanticStatus.MISSING),
        (NCExportStatus.PUBLISHED, OperationManagerSemanticStatus.CURRENT),
        (NCExportStatus.PUBLISHED_EXTERNAL, OperationManagerSemanticStatus.CURRENT),
        (NCExportStatus.STALE, OperationManagerSemanticStatus.STALE),
        (NCExportStatus.FAILED, OperationManagerSemanticStatus.FAILED),
        (NCExportStatus.EXTERNAL_FAILED, OperationManagerSemanticStatus.FAILED),
        (NCExportStatus.CANCELLED, OperationManagerSemanticStatus.DRAFT),
    ],
)
def test_external_export_status_matrix(export_status, semantic) -> None:
    operation = SimpleNamespace(operation_id=OperationId.new())
    export_result = (
        None if export_status is None else SimpleNamespace(status=export_status)
    )
    service = SimpleNamespace(
        nc_export_service=SimpleNamespace(
            current=lambda *_args: export_result
        )
    )
    session = SimpleNamespace(manifest=SimpleNamespace(project_id=uuid4()))
    _nc, mapped = nc_status(service, session, operation, ())
    assert mapped.semantic is semantic


def test_search_matches_strategy_tool_status_and_typed_id_with_parent_context(
    tmp_path,
) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        for query in (
            "Facing 2.5D",
            operation.search_terms[2],
            "needs calc",
            operation.domain_identity.value,
        ):
            panel.search.setText(query)
            application.processEvents()
            assert panel.model.rowCount() == 1
            assert panel.model.index_for_node_id(operation.node_id).isValid()
            assert panel.model.index_for_node_id(
                _node(panel, OperationManagerNodeKind.PROJECT).node_id
            ).isValid()
        panel.search.setText("khong-co-ket-qua-9a3")
        application.processEvents()
        assert panel.model.rowCount() == 0
        assert not panel.state_frame.isHidden()
        assert "Không có" in panel.state_title.text()
    finally:
        _dispose(service, workspace, panel, application)


def test_child_selection_synchronizes_operation_and_default_opens_correct_panel(
    tmp_path,
) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    simulation_requests: list[bool] = []
    post_requests: list[bool] = []
    panel.simulation_requested.connect(lambda: simulation_requests.append(True))
    panel.post_requested.connect(lambda: post_requests.append(True))
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        simulation = _node(panel, OperationManagerNodeKind.SIMULATION)
        panel.view.setCurrentIndex(panel.model.index_for_node_id(simulation.node_id))
        application.processEvents()
        assert workspace.selected_identity == (
            operation.legacy_selection.kind,
            operation.legacy_selection.value,
        )
        panel.commands.trigger_default()
        assert simulation_requests == [True]
        post = _node(panel, OperationManagerNodeKind.POST_RESULT)
        panel.view.setCurrentIndex(panel.model.index_for_node_id(post.node_id))
        panel.commands.trigger_default()
        assert post_requests == [True]
        assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[
            0
        ].artifact_state.status is ArtifactStatus.MISSING
    finally:
        _dispose(service, workspace, panel, application)


def test_single_click_only_selects_but_double_click_and_enter_request_editor(
    tmp_path,
) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    requests: list[bool] = []
    panel.editor_requested.connect(lambda: requests.append(True))
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        index = panel.model.index_for_node_id(operation.node_id)
        panel.view.setCurrentIndex(index)
        application.processEvents()
        assert requests == []

        panel.view.doubleClicked.emit(index)
        assert requests == [True]
        panel.view.setFocus()
        QTest.keyClick(panel.view, Qt.Key.Key_Return)
        assert requests == [True, True]
    finally:
        _dispose(service, workspace, panel, application)


def test_successful_function_creation_announces_selected_operation(tmp_path) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    created: list[str] = []
    workspace.operation_created.connect(created.append)
    try:
        before = len(_operation_nodes(panel))
        workspace.actions["operation"].trigger()
        application.processEvents()

        assert len(_operation_nodes(panel)) == before + 1
        assert created == [workspace.selected_identity[1]]
    finally:
        _dispose(service, workspace, panel, application)


def test_expansion_and_selection_are_user_only_and_restore_by_identity(tmp_path) -> None:
    application, service, workspace, panel, settings = _environment(tmp_path)
    second = None
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        index = panel.model.index_for_node_id(operation.node_id)
        panel.view.setExpanded(index, True)
        panel.view.setCurrentIndex(index)
        panel._save_state()
        dirty_before = service.is_dirty
        second = OperationManagerPanel(
            workspace, service, settings, workspace.actions
        )
        application.processEvents()
        restored = second.model.index_for_node_id(operation.node_id)
        assert restored.isValid()
        assert second.view.isExpanded(restored)
        assert second.current_node().node_id == operation.node_id
        assert service.is_dirty is dirty_before
    finally:
        if second is not None:
            second.close()
            second.deleteLater()
        _dispose(service, workspace, panel, application)


def test_delete_requires_confirmation_and_selection_falls_back(monkeypatch, tmp_path) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        panel.view.setCurrentIndex(panel.model.index_for_node_id(operation.node_id))
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
        )
        panel.commands.delete.trigger()
        assert len(_operation_nodes(panel)) == 1
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        panel.commands.delete.trigger()
        application.processEvents()
        assert not _operation_nodes(panel)
        assert panel.current_node() is not None
        assert panel.current_node().kind is not OperationManagerNodeKind.OPERATION
    finally:
        _dispose(service, workspace, panel, application)


def test_model_has_no_row_widgets_drag_drop_and_actions_explain_disabled_state(
    tmp_path,
) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        assert isinstance(panel.model, QAbstractItemModel)
        assert isinstance(panel.model, OperationManagerModel)
        assert not panel.view.dragEnabled()
        assert not panel.view.acceptDrops()
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        index = panel.model.index_for_node_id(operation.node_id)
        assert panel.view.indexWidget(index) is None
        panel.view.setCurrentIndex(index)
        panel.commands.update_state()
        assert not panel.commands.simulate.isEnabled()
        assert "HIỆN HÀNH" in panel.commands.simulate.toolTip()
        assert panel.commands.duplicate.isEnabled()
        assert panel.commands.duplicate.toolTip() == "Nhân bản"
        assert not panel.commands.clear_toolpath.isEnabled()
        assert "lệnh" in panel.commands.clear_toolpath.toolTip()
    finally:
        _dispose(service, workspace, panel, application)


def test_duplicate_action_routes_through_operation_manager(tmp_path) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        panel.view.setCurrentIndex(panel.model.index_for_node_id(operation.node_id))
        panel.commands.update_state()

        panel.commands.duplicate.trigger()
        application.processEvents()

        operations = _operation_nodes(panel)
        assert len(operations) == 2
        assert len({item.domain_identity.value for item in operations}) == 2
        assert any(item.label.endswith("Bản sao") for item in operations)
    finally:
        _dispose(service, workspace, panel, application)


def test_context_menus_are_node_scoped_and_opening_them_has_no_side_effect(
    tmp_path,
) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        before = service.cam_snapshot
        post_before = service.post_service.results()
        nc_before = service.nc_export_service.artifacts()
        panel.view.setCurrentIndex(panel.model.index_for_node_id(operation.node_id))
        operation_menu = panel.commands.context_menu(panel.view)
        operation_labels = {
            action.text() for action in operation_menu.actions() if not action.isSeparator()
        }
        assert {
            "Mở",
            "Tính lại",
            "Mô phỏng",
                "Tạo Post",
                "Thêm vào Lắp ráp chương trình",
            "Xóa",
        }.issubset(operation_labels)
        assert "Xóa kết quả đường chạy dao" not in operation_labels

        toolpath = _node(panel, OperationManagerNodeKind.TOOLPATH)
        panel.view.setCurrentIndex(panel.model.index_for_node_id(toolpath.node_id))
        toolpath_menu = panel.commands.context_menu(panel.view)
        toolpath_labels = tuple(
            action.text()
            for action in toolpath_menu.actions()
            if not action.isSeparator()
        )
        assert toolpath_labels == (
            "Hiện/ẩn đường chạy dao",
            "Tính lại",
            "Xóa kết quả đường chạy dao",
        )
        assert service.cam_snapshot == before
        assert service.post_service.results() == post_before
        assert service.nc_export_service.artifacts() == nc_before
    finally:
        _dispose(service, workspace, panel, application)


def test_keyboard_enter_delete_and_context_menu_are_reachable(monkeypatch, tmp_path) -> None:
    application, service, workspace, panel, _settings = _environment(tmp_path)
    default_requests: list[bool] = []
    context_requests: list[bool] = []
    panel.view.default_requested.connect(lambda: default_requests.append(True))
    panel.view.context_requested.connect(lambda: context_requests.append(True))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    try:
        operation = _node(panel, OperationManagerNodeKind.OPERATION)
        panel.view.setCurrentIndex(panel.model.index_for_node_id(operation.node_id))
        panel.view.setFocus()
        QTest.keyClick(panel.view, Qt.Key.Key_Return)
        QTest.keyClick(
            panel.view,
            Qt.Key.Key_F10,
            Qt.KeyboardModifier.ShiftModifier,
        )
        QTest.keyClick(panel.view, Qt.Key.Key_Delete)
        application.processEvents()
        assert default_requests == [True]
        assert context_requests == [True]
        assert len(_operation_nodes(panel)) == 1
        assert panel.view.accessibleName()
        assert panel.search.accessibleName()
        assert panel.filter.accessibleName()
    finally:
        active = getattr(panel, "_active_context_menu", None)
        if active is not None:
            active.close()
        _dispose(service, workspace, panel, application)


@pytest.mark.parametrize("operation_count", [10, 50, 200])
def test_projection_refresh_is_responsive_without_domain_mutation(
    tmp_path, operation_count
) -> None:
    application, service, workspace, panel, _settings = _environment(
        tmp_path, operation_count=operation_count
    )
    try:
        before = service.cam_snapshot
        started = perf_counter()
        projection = OperationManagerProjectionBuilder().build(
            service, service.current_project
        )
        elapsed = perf_counter() - started
        assert count_operation_nodes(projection.nodes) == operation_count
        assert elapsed < 2.0
        assert service.cam_snapshot == before
        panel.model.set_projection(projection)
        assert panel.model.visible_node_count() >= operation_count
        assert all(
            panel.view.indexWidget(panel.model.index_for_node_id(item.node_id)) is None
            for item in projection.nodes[:25]
        )
    finally:
        _dispose(service, workspace, panel, application)
