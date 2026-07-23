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
from hms_cadcam.ui.localization import translate_status


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
    return status(category, OperationManagerSemanticStatus.CURRENT, "HIỆN HÀNH", tooltip)


def tool_status(value: ToolReferenceStatus) -> OperationManagerStatus:
    mapping = {
        ToolReferenceStatus.VALID: (
            OperationManagerSemanticStatus.CURRENT,
            "HIỆN HÀNH",
            "Cụm Tool khớp revision/dấu vân tay hiện hành.",
        ),
        ToolReferenceStatus.MISSING: (
            OperationManagerSemanticStatus.NEEDS_INPUT,
            "THIẾU",
            "Không tìm thấy Cụm Tool được nguyên công tham chiếu.",
        ),
        ToolReferenceStatus.STALE: (
            OperationManagerSemanticStatus.STALE,
            "ĐÃ LỖI THỜI",
            "Cụm Tool đã thay đổi so với tham chiếu nguyên công.",
        ),
        ToolReferenceStatus.INCOMPATIBLE_UNIT: (
            OperationManagerSemanticStatus.BLOCKED,
            "BỊ CHẶN",
            "Đơn vị Cụm Tool không tương thích.",
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
            "ĐANG TÍNH",
            "Đường chạy dao đang được tính bởi quy trình hiện có.",
        )
    if value is ArtifactStatus.VALID:
        return current_status(
            OperationManagerStatusCategory.CALCULATION,
            "Artifact đường chạy dao hiện hành và đã được công bố.",
        )
    if value is ArtifactStatus.DIRTY:
        return status(
            OperationManagerStatusCategory.CALCULATION,
            OperationManagerSemanticStatus.STALE,
            "ĐÃ LỖI THỜI",
            "Dữ liệu đầu vào đã thay đổi; cần tính lại đường chạy dao.",
        )
    if value is ArtifactStatus.FAILED:
        return status(
            OperationManagerStatusCategory.CALCULATION,
            OperationManagerSemanticStatus.FAILED,
            "THẤT BẠI",
            "Lần tính đường chạy dao gần nhất thất bại.",
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
        "CẦN DỮ LIỆU" if missing_input else "CẦN TÍNH",
        "Cần sửa dữ liệu đầu vào trước khi tính đường chạy dao."
        if missing_input
        else "Nguyên công chưa có artifact đường chạy dao.",
    )


def parallel_safety_status(
    operation: Operation,
    artifact: object | None,
) -> OperationManagerStatus | None:
    """Map the Parallel safety contract without implying machine safety."""
    if operation.strategy_key != "parallel_finishing_3d":
        return None
    if not operation.enabled:
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.DISABLED,
            "ĐÃ TẮT",
            "Nguyên công Gia công tinh song song đã bị tắt.",
        )
    if operation.artifact_state.status is ArtifactStatus.COMPUTING:
        return status(
            OperationManagerStatusCategory.CALCULATION,
            OperationManagerSemanticStatus.CALCULATING,
            "ỨNG VIÊN",
            "Đang tạo ứng viên Gia công tinh song song và kiểm tra an toàn.",
        )
    if operation.artifact_state.status is ArtifactStatus.DIRTY:
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.STALE,
            "AN TOÀN ĐÃ LỖI THỜI",
            "Bằng chứng an toàn không còn khớp dữ liệu đầu vào đã áp dụng.",
        )
    if operation.artifact_state.status is ArtifactStatus.FAILED:
        evidence = {
            key: value
            for item in operation.artifact_state.diagnostics
            for key, value in item.context
        }
        value = evidence.get("safety_status", "failed")
        semantic = (
            OperationManagerSemanticStatus.BLOCKED
            if value in {"unsafe", "unknown"}
            else OperationManagerSemanticStatus.FAILED
        )
        return status(
            OperationManagerStatusCategory.DOMAIN,
            semantic,
            translate_status(value),
            "Kiểm tra an toàn Gia công tinh song song không tạo được kết quả SẴN SÀNG.",
        )
    if operation.artifact_state.status is ArtifactStatus.VALID and artifact is not None:
        from hms_cadcam.cam.cam3d.parallel import parallel_artifact_has_safe_contract

        if parallel_artifact_has_safe_contract(artifact):
            return status(
                OperationManagerStatusCategory.DOMAIN,
                OperationManagerSemanticStatus.CURRENT,
                "ĐÃ KIỂM TRA PHẠM VI",
                "Đã xác minh an toàn trong phạm vi công bố; khoảng hở sẵn sàng cho máy chưa được xác minh.",
            )
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.STALE,
            "AN TOÀN KHÔNG HỢP LỆ",
            "Kết quả Gia công tinh song song thiếu hash/phạm vi an toàn hiện hành "
            "của thuật toán v3.",
        )
    return status(
        OperationManagerStatusCategory.DOMAIN,
        OperationManagerSemanticStatus.DRAFT,
        "CHƯA KIỂM TRA",
        "Chưa tính kiểm tra an toàn Gia công tinh song song.",
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
            "ĐÃ TẮT",
            "Nguyên công bị tắt; các kết quả không được coi là đầu ra hiện hành.",
        )
    diagnostics = (*operation.diagnostics, *operation.artifact_state.diagnostics)
    if any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics):
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.BLOCKED,
            "BỊ CHẶN",
            "Nguyên công có chẩn đoán lỗi cần xử lý.",
        )
    if any(item.severity is DiagnosticSeverity.WARNING for item in diagnostics):
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.WARNING,
            "CẢNH BÁO",
            "Nguyên công có chẩn đoán cảnh báo.",
        )
    if tool_reference_status is not ToolReferenceStatus.VALID:
        return status(
            OperationManagerStatusCategory.DOMAIN,
            OperationManagerSemanticStatus.NEEDS_INPUT,
            "CẦN DỮ LIỆU",
            "Cụm Tool chưa hợp lệ.",
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
                "ĐANG CHẠY",
                f"Trạng thái Mô phỏng trong phiên: {record.state.value}.",
            )
        if record.state is SimulationRunState.STALE:
            return status(
                OperationManagerStatusCategory.SIMULATION,
                OperationManagerSemanticStatus.STALE,
                "ĐÃ LỖI THỜI",
                record.diagnostic_message or "Kết quả Mô phỏng đã lỗi thời.",
            )
        if record.state is SimulationRunState.FAILED:
            return status(
                OperationManagerStatusCategory.SIMULATION,
                OperationManagerSemanticStatus.FAILED,
                "THẤT BẠI",
                record.diagnostic_message or "Mô phỏng thất bại.",
            )
    if result is None:
        return status(
            OperationManagerStatusCategory.SIMULATION,
            OperationManagerSemanticStatus.MISSING,
            "CHƯA CHẠY",
            "Chưa có kết quả Mô phỏng cho nguyên công.",
        )
    if (
        operation.artifact_state.status is not ArtifactStatus.VALID
        or operation.artifact_state.artifact_fingerprint != result.artifact_fingerprint
    ):
        return status(
            OperationManagerStatusCategory.SIMULATION,
            OperationManagerSemanticStatus.STALE,
            "ĐÃ LỖI THỜI",
            "Mô phỏng không còn khớp đường chạy dao hiện hành.",
        )
    if result.status is SimulationStatus.PASS:
        return current_status(
            OperationManagerStatusCategory.SIMULATION,
            "Mô phỏng ĐẠT và khớp đường chạy dao hiện hành.",
        )
    if result.status is SimulationStatus.WARN:
        return status(
            OperationManagerStatusCategory.SIMULATION,
            OperationManagerSemanticStatus.WARNING,
            "CẢNH BÁO",
            "Mô phỏng hoàn thành với cảnh báo.",
        )
    return status(
        OperationManagerStatusCategory.SIMULATION,
        OperationManagerSemanticStatus.FAILED,
        "THẤT BẠI",
        "Mô phỏng phát hiện lỗi hoặc va chạm.",
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
            "CHƯA TẠO",
            "Chưa có kết quả Post cho nguyên công.",
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
        translate_status(result.status),
        f"Kết quả Post hiện có trạng thái {result.status.value}.",
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
            "THIẾU",
            "Chưa có kết quả NC được quản lý trong dự án.",
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
            translate_status(artifact.status),
            f"Kết quả NC: {artifact.output_relative_path}.",
        )
    export_result = service.nc_export_service.current(
        session.manifest.project_id, operation.operation_id
    )
    if export_result is None:
        export = status(
            OperationManagerStatusCategory.EXPORT,
            OperationManagerSemanticStatus.MISSING,
            "CHƯA XUẤT",
            "Phiên hiện tại chưa có kết quả Xuất NC.",
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
        translate_status(export_result.status),
            f"Trạng thái Xuất NC trong phiên: {export_result.status.value}.",
        )
    return nc, export


def dirty_reason_summary(operation: Operation) -> str:
    reasons = operation.artifact_state.dirty_reasons
    return (
        ", ".join(item.value.replace("_", " ") for item in reasons)
        if reasons
        else "Artifact đã công bố"
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
