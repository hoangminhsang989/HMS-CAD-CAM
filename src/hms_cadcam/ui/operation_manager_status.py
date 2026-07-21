"""Lifecycle-to-semantic-status mapping for Operation Manager projections."""

from __future__ import annotations

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    DiagnosticSeverity,
    Operation,
    Setup,
    StockDefinition,
    ToolReferenceStatus,
)
from hms_cadcam.cam.persistence.models import CamProjectSnapshot
from hms_cadcam.cam.post import (
    NCArtifactManifestEntry,
    NCArtifactStatus,
    NCExportStatus,
    PostResult,
    PostResultStatus,
)
from hms_cadcam.cam.simulation import (
    SimulationResult,
    SimulationRunState,
    SimulationStatus,
)
from hms_cadcam.project.models import ProjectSession
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.operation_manager_types import (
    OperationManagerSemanticStatus,
    OperationManagerStatus,
    OperationManagerStatusCategory,
)


def status(
    category: OperationManagerStatusCategory,
    semantic: OperationManagerSemanticStatus,
    text: str,
    tooltip: str,
) -> OperationManagerStatus:
    return OperationManagerStatus(category, semantic, text, tooltip)


def current_status(
    category: OperationManagerStatusCategory, tooltip: str
) -> OperationManagerStatus:
    return status(category, OperationManagerSemanticStatus.CURRENT, "CURRENT", tooltip)


def tool_status(value: ToolReferenceStatus) -> OperationManagerStatus:
    mapping = {
        ToolReferenceStatus.VALID: (
            OperationManagerSemanticStatus.CURRENT,
            "CURRENT",
            "Tool Assembly khớp revision/fingerprint hiện hành.",
        ),
        ToolReferenceStatus.MISSING: (
            OperationManagerSemanticStatus.NEEDS_INPUT,
            "MISSING",
            "Không tìm thấy Tool Assembly được operation tham chiếu.",
        ),
        ToolReferenceStatus.STALE: (
            OperationManagerSemanticStatus.STALE,
            "STALE",
            "Tool Assembly đã thay đổi so với tham chiếu operation.",
        ),
        ToolReferenceStatus.INCOMPATIBLE_UNIT: (
            OperationManagerSemanticStatus.BLOCKED,
            "BLOCKED",
            "Đơn vị Tool Assembly không tương thích.",
        ),
    }
    semantic, text, tooltip = mapping[value]
    return status(OperationManagerStatusCategory.DOMAIN, semantic, text, tooltip)


def calculation_status(
    operation: Operation,
    tool_reference_status: ToolReferenceStatus,
) -> OperationManagerStatus:
    value = operation.artifact_state.status
    if value is ArtifactStatus.COMPUTING:
        return status(
            OperationManagerStatusCategory.CALCULATION,
            OperationManagerSemanticStatus.CALCULATING,
            "CALCULATING",
            "Toolpath đang được tính bởi workflow hiện có.",
        )
    if value is ArtifactStatus.VALID:
        return current_status(
            OperationManagerStatusCategory.CALCULATION,
            "Toolpath artifact hiện hành và đã publish.",
        )
    if value is ArtifactStatus.DIRTY:
        return status(
            OperationManagerStatusCategory.CALCULATION,
            OperationManagerSemanticStatus.STALE,
            "STALE",
            "Input đã thay đổi; cần tính lại toolpath.",
        )
    if value is ArtifactStatus.FAILED:
        return status(
            OperationManagerStatusCategory.CALCULATION,
            OperationManagerSemanticStatus.FAILED,
            "FAILED",
            "Lần tính toolpath gần nhất thất bại.",
        )
    missing_input = tool_reference_status is not ToolReferenceStatus.VALID
    semantic = (
        OperationManagerSemanticStatus.NEEDS_INPUT
        if missing_input
        else OperationManagerSemanticStatus.DRAFT
    )
    return status(
        OperationManagerStatusCategory.CALCULATION,
        semantic,
        "NEEDS INPUT" if missing_input else "NEEDS CALC",
        "Cần sửa input trước khi tính toolpath."
        if missing_input
        else "Operation chưa có toolpath artifact.",
    )


def operation_status(
    operation: Operation,
    tool_reference_status: ToolReferenceStatus,
    calculation: OperationManagerStatus,
) -> OperationManagerStatus:
    if not operation.enabled:
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.DISABLED,
            "DISABLED",
            "Operation bị vô hiệu hóa; các artifact không được coi là đầu ra hiện hành.",
        )
    diagnostics = (*operation.diagnostics, *operation.artifact_state.diagnostics)
    if any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics):
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.BLOCKED,
            "BLOCKED",
            "Operation có diagnostic lỗi cần xử lý.",
        )
    if any(item.severity is DiagnosticSeverity.WARNING for item in diagnostics):
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.WARNING,
            "WARNING",
            "Operation có diagnostic cảnh báo.",
        )
    if tool_reference_status is not ToolReferenceStatus.VALID:
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.NEEDS_INPUT,
            "NEEDS INPUT",
            "Tool Assembly chưa hợp lệ.",
        )
    return status(
        OperationManagerStatusCategory.DOMAIN,
        calculation.semantic,
        calculation.text,
        calculation.tooltip,
    )


def simulation_status(
    service: ProjectService,
    operation: Operation,
    result: SimulationResult | None,
) -> OperationManagerStatus:
    record = service.simulation_runs.record(operation.operation_id)
    if record is not None:
        if record.state in {
            SimulationRunState.VALIDATING,
            SimulationRunState.RUNNING,
            SimulationRunState.CANCELLING,
        }:
            return status(
                OperationManagerStatusCategory.SIMULATION,
                OperationManagerSemanticStatus.CALCULATING,
                "RUNNING",
                f"Simulation runtime: {record.state.value}.",
            )
        if record.state is SimulationRunState.STALE:
            return status(
                OperationManagerStatusCategory.SIMULATION,
                OperationManagerSemanticStatus.STALE,
                "STALE",
                record.diagnostic_message or "Simulation result đã stale.",
            )
        if record.state is SimulationRunState.FAILED:
            return status(
                OperationManagerStatusCategory.SIMULATION,
                OperationManagerSemanticStatus.FAILED,
                "FAILED",
                record.diagnostic_message or "Simulation thất bại.",
            )
    if result is None:
        return status(
            OperationManagerStatusCategory.SIMULATION,
            OperationManagerSemanticStatus.MISSING,
            "NOT RUN",
            "Chưa có kết quả Simulation cho operation.",
        )
    if (
        operation.artifact_state.status is not ArtifactStatus.VALID
        or operation.artifact_state.artifact_fingerprint != result.artifact_fingerprint
    ):
        return status(
            OperationManagerStatusCategory.SIMULATION,
            OperationManagerSemanticStatus.STALE,
            "STALE",
            "Simulation không còn khớp toolpath hiện hành.",
        )
    if result.status is SimulationStatus.PASS:
        return current_status(
            OperationManagerStatusCategory.SIMULATION,
            "Simulation PASS và khớp toolpath hiện hành.",
        )
    if result.status is SimulationStatus.WARN:
        return status(
            OperationManagerStatusCategory.SIMULATION,
            OperationManagerSemanticStatus.WARNING,
            "WARNING",
            "Simulation hoàn thành với cảnh báo.",
        )
    return status(
        OperationManagerStatusCategory.SIMULATION,
        OperationManagerSemanticStatus.FAILED,
        "FAILED",
        "Simulation phát hiện lỗi hoặc va chạm.",
    )


def post_status(
    operation: Operation,
    results: tuple[PostResult, ...],
) -> OperationManagerStatus:
    result = next(
        (item for item in reversed(results) if item.operation_id == operation.operation_id),
        None,
    )
    if result is None:
        return status(
            OperationManagerStatusCategory.POST,
            OperationManagerSemanticStatus.MISSING,
            "NOT GENERATED",
            "Chưa có Post Result cho operation.",
        )
    mapping = {
        PostResultStatus.PUBLISHED: OperationManagerSemanticStatus.CURRENT,
        PostResultStatus.STALE: OperationManagerSemanticStatus.STALE,
        PostResultStatus.BLOCKED: OperationManagerSemanticStatus.BLOCKED,
        PostResultStatus.FAILED: OperationManagerSemanticStatus.FAILED,
        PostResultStatus.CANCELLED: OperationManagerSemanticStatus.DRAFT,
    }
    semantic = mapping[result.status]
    return status(
        OperationManagerStatusCategory.POST,
        semantic,
        result.status.value.upper(),
        f"Post Result hiện có trạng thái {result.status.value}.",
    )


def nc_status(
    service: ProjectService,
    session: ProjectSession,
    operation: Operation,
    artifacts: tuple[NCArtifactManifestEntry, ...],
) -> tuple[OperationManagerStatus, OperationManagerStatus]:
    artifact = next(
        (item for item in artifacts if item.operation_id == operation.operation_id),
        None,
    )
    if artifact is None:
        nc = status(
            OperationManagerStatusCategory.NC,
            OperationManagerSemanticStatus.MISSING,
            "MISSING",
            "Chưa có NC Artifact được quản lý trong project.",
        )
    else:
        mapping = {
            NCArtifactStatus.CURRENT: OperationManagerSemanticStatus.CURRENT,
            NCArtifactStatus.STALE: OperationManagerSemanticStatus.STALE,
            NCArtifactStatus.MISSING: OperationManagerSemanticStatus.MISSING,
            NCArtifactStatus.TAMPERED: OperationManagerSemanticStatus.FAILED,
        }
        nc = status(
            OperationManagerStatusCategory.NC,
            mapping[artifact.status],
            artifact.status.value.upper(),
            f"NC Artifact: {artifact.output_relative_path}.",
        )
    export_result = service.nc_export_service.current(
        session.manifest.project_id, operation.operation_id
    )
    if export_result is None:
        export = status(
            OperationManagerStatusCategory.EXPORT,
            OperationManagerSemanticStatus.MISSING,
            "NOT EXPORTED",
            "Phiên hiện tại chưa có kết quả Export NC.",
        )
    else:
        mapping = {
            NCExportStatus.PUBLISHED: OperationManagerSemanticStatus.CURRENT,
            NCExportStatus.PUBLISHED_EXTERNAL: OperationManagerSemanticStatus.CURRENT,
            NCExportStatus.STALE: OperationManagerSemanticStatus.STALE,
            NCExportStatus.FAILED: OperationManagerSemanticStatus.FAILED,
            NCExportStatus.EXTERNAL_FAILED: OperationManagerSemanticStatus.FAILED,
            NCExportStatus.CANCELLED: OperationManagerSemanticStatus.DRAFT,
        }
        export = status(
            OperationManagerStatusCategory.EXPORT,
            mapping[export_result.status],
            export_result.status.value.upper(),
            f"NC Export runtime: {export_result.status.value}.",
        )
    return nc, export


def dirty_reason_summary(operation: Operation) -> str:
    reasons = operation.artifact_state.dirty_reasons
    return (
        ", ".join(item.value.replace("_", " ") for item in reasons)
        if reasons
        else "Artifact đã publish"
    )


def stock_summary(stock: StockDefinition) -> str:
    values = []
    for name in ("size_x", "size_y", "size_z", "diameter", "length"):
        value = getattr(stock, name, None)
        if value is not None:
            values.append(f"{value.value:g}")
    suffix = f" · {' × '.join(values)}" if values else ""
    return f"{stock.kind.value}{suffix}"


def setup_machine_name(
    snapshot: CamProjectSnapshot, setup: Setup | None
) -> str:
    if setup is None:
        return "Chưa gán máy"
    machine_ids = {
        operation.machine_requirement.machine_id
        for operation in setup.operation_tree.operations
        if operation.machine_requirement is not None
    }
    names = tuple(
        item.name for item in snapshot.machine_definitions if item.machine_id in machine_ids
    )
    if not names:
        return "Chưa gán máy"
    return names[0] if len(names) == 1 else f"{len(names)} máy"
