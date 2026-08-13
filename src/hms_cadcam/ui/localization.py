"""Vietnamese presentation catalog for production UI text.

The catalog deliberately lives at the UI boundary.  Domain enums, diagnostic
codes, fingerprints, payloads and persisted values remain unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import MappingProxyType
import re

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QComboBox

from hms_cadcam.ui.i18n import UiLanguage, translation_service

_LOCALIZATION_SOURCE_ROLE = int(Qt.ItemDataRole.UserRole) + 1000


TECHNICAL_TERMS = (
    "CAD",
    "CAM",
    "CNC",
    "Tool",
    "Holder",
    "Post",
    "G-code",
    "Toolpath IR",
    "SQLite",
    "OCP",
    "BRep",
    "STEP",
    "STP",
    "IGES",
    "IGS",
    "STL",
    "UUID",
    "ID",
    "HMS",
    "G54",
    "XYZ",
    "CRLF",
    "UTF-8",
    "SHA-256",
    "UNC",
    "MM",
    "RPM",
)


OPERATION_DISPLAY_NAMES = MappingProxyType(
    {
        "Z-Level Finishing": "Gia công tinh theo cao độ Z",
        "Facing 2.5D": "Phay mặt 2.5D",
        "Planar Face Facing": "Phay các mặt phẳng",
        "2D Contour": "Phay biên dạng 2D",
        "Pocket 2.5D": "Phay hốc 2.5D",
        "Drilling": "Khoan",
        "Tapping": "Taro",
        "Reaming": "Doa lỗ",
        "Boring": "Khoét lỗ",
        "Parallel Finishing": "Gia công tinh song song",
    }
)


_OPERATION_STRATEGY_DISPLAY_NAMES = MappingProxyType(
    {
        "z_level_finishing_3d": "Gia công tinh theo cao độ Z",
        "z_level_finishing_3d_8a3_3": "Gia công tinh theo cao độ Z",
        "facing_2_5d": OPERATION_DISPLAY_NAMES["Facing 2.5D"],
        "facing_stock_box_9a5_1": OPERATION_DISPLAY_NAMES["Facing 2.5D"],
        "planar_face_facing_9a5_1": OPERATION_DISPLAY_NAMES["Planar Face Facing"],
        "contour_2d": OPERATION_DISPLAY_NAMES["2D Contour"],
        "contour_2d_9a5_2": OPERATION_DISPLAY_NAMES["2D Contour"],
        "pocket_2_5d": OPERATION_DISPLAY_NAMES["Pocket 2.5D"],
        "pocket_2_5d_9a5_3": OPERATION_DISPLAY_NAMES["Pocket 2.5D"],
        "rest_pocket_3axis": "Phay hốc phần dư 3 trục",
        "rest_pocket_3axis_r266": "Phay hốc phần dư 3 trục",
        "drilling_v1": OPERATION_DISPLAY_NAMES["Drilling"],
        "drilling_v1_9a6": OPERATION_DISPLAY_NAMES["Drilling"],
        "tapping_v1": OPERATION_DISPLAY_NAMES["Tapping"],
        "tapping_v1_9a6": OPERATION_DISPLAY_NAMES["Tapping"],
        "reaming_v1": OPERATION_DISPLAY_NAMES["Reaming"],
        "reaming_v1_9a6": OPERATION_DISPLAY_NAMES["Reaming"],
        "boring_v1": OPERATION_DISPLAY_NAMES["Boring"],
        "boring_v1_9a6": OPERATION_DISPLAY_NAMES["Boring"],
        "parallel_finishing_3d": OPERATION_DISPLAY_NAMES["Parallel Finishing"],
        "parallel_finishing_3d_8a2_3": OPERATION_DISPLAY_NAMES[
            "Parallel Finishing"
        ],
    }
)


def operation_type_display_name(value: object) -> str:
    """Return a localized operation type without changing its stable value."""
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    localized = OPERATION_DISPLAY_NAMES.get(
        text, _OPERATION_STRATEGY_DISPLAY_NAMES.get(text, text)
    )
    return ui_text(localized)


def operation_display_name(
    name: object,
    *,
    strategy_key: object | None = None,
) -> str:
    """Localize a known default name while preserving every custom user name."""
    text = str(name).strip()
    localized = OPERATION_DISPLAY_NAMES.get(text)
    if localized is not None:
        return ui_text(localized)
    if text:
        return text
    return operation_type_display_name(strategy_key or "")


def setup_display_name(value: object) -> str:
    """Localize only the generated Setup name while preserving custom names."""
    text = str(value).strip()
    generated = re.fullmatch(r"Setup\s+(\d+)", text, re.IGNORECASE)
    if generated is None:
        return text
    label = {
        UiLanguage.VI_VN: "Thiết lập",
        UiLanguage.EN_US: "Setup",
        UiLanguage.KO_KR: "Setup",
    }[translation_service().language]
    return f"{label} {generated.group(1)}"


DISPLAY_VALUE_MAPPINGS = MappingProxyType(
    {
        "operation_display_name": OPERATION_DISPLAY_NAMES,
        "geometry_resolution": MappingProxyType(
            {
                "RESOLVED": "Đã xác định",
                "UNRESOLVED": "Chưa xác định",
                "MISSING": "Bị thiếu",
                "STALE": "Cần cập nhật",
                "INVALID": "Không hợp lệ",
            }
        ),
        "setup_role": MappingProxyType(
            {
                "PRIMARY": "Chính",
                "SECONDARY": "Phụ",
                "ACTIVE": "Đang sử dụng",
                "INACTIVE": "Không hoạt động",
            }
        ),
        "setup_kind": MappingProxyType(
            {
                "general": "CHUNG",
                "mill": "PHAY",
                "turn": "TIỆN",
                "mill_turn": "PHAY/TIỆN",
            }
        ),
        "stock_kind": MappingProxyType(
            {
                "box": "Khối hộp",
                "cylinder": "Khối trụ",
                "from_model": "Từ mô hình",
                "custom_geometry": "Hình học tùy chỉnh",
            }
        ),
        "dirty_reason": MappingProxyType(
            {
                "geometry_changed": "Hình học đã thay đổi",
                "wcs_changed": "Hệ tọa độ đã thay đổi",
                "stock_changed": "Phôi đã thay đổi",
                "fixture_changed": "Đồ gá đã thay đổi",
                "tool_changed": "Tool đã thay đổi",
                "machine_changed": "Máy đã thay đổi",
                "parameters_changed": "Tham số đã thay đổi",
                "upstream_changed": "Dữ liệu đầu vào đã thay đổi",
                "artifact_missing": "Thiếu kết quả tính toán",
            }
        ),
        "holder_state": MappingProxyType(
            {
                "declared_present": "Đã khai báo Holder",
                "declared_absent": "Chưa khai báo Holder",
                "missing": "Thiếu dữ liệu Holder",
                "invalid": "Holder không hợp lệ",
                "verified": "Holder đã được xác minh",
                "unverified": "Holder chưa được xác minh",
                # Runtime report aliases retained by the immutable domain model.
                "geometry_faithful": "Holder đã được xác minh",
                "reference_invalid": "Holder không hợp lệ",
            }
        ),
        "safety_component": MappingProxyType(
            {
                "cutter": "Dao cắt",
                "shank": "Cán dao",
                "holder": "Holder",
                "tool_assembly": "Cụm Tool",
                "rapid": "Chạy nhanh",
                "link": "Liên kết",
                "approach": "Tiếp cận",
                "retract": "Rút dao",
            }
        ),
        "safety_scope": MappingProxyType(
            {
                "declared_assembly_holder_verified": "Cụm Tool đã xác minh Holder",
                "declared_assembly_holder_absent": "Cụm Tool chưa khai báo Holder",
                "declared_assembly_holder_unavailable": "Không thể xác minh Holder của cụm Tool",
                "incomplete_tool_assembly": "Không thể xác minh Holder của cụm Tool",
            }
        ),
        "geometry_source": MappingProxyType(
            {
                "check_surface": "Bề mặt kiểm tra",
                "geometry_reference": "Tham chiếu hình học",
                "selected_face": "Bề mặt đã chọn",
                "protected_face": "Bề mặt được bảo vệ",
                # Sources used by the Parallel safety report.
                "machining_face": "Bề mặt gia công",
                "protected_part": "Chi tiết được bảo vệ",
                "fixture": "Đồ gá",
                "unknown": "Chưa xác định",
            }
        ),
        "cut_direction": MappingProxyType(
            {
                "one_way": "Một chiều",
                "zigzag": "Zích zắc",
            }
        ),
        # Stable automatic-policy values are mapped only at the presentation
        # boundary.  The persisted enum values remain English and unchanged.
        "quality_profile": MappingProxyType(
            {
                "fast": "Nhanh",
                "balanced": "Cân bằng",
                "high": "Chất lượng cao",
            }
        ),
        "automatic_mode": MappingProxyType(
            {"auto": "Tự động", "manual": "Thủ công"}
        ),
        "automatic_status": MappingProxyType(
            {
                "resolved": "Đã xác định",
                "needs_confirmation": "Cần xác nhận",
                "unsupported": "Không được hỗ trợ",
                "unresolved": "Chưa xác định",
            }
        ),
        "z_level_orientation": MappingProxyType(
            {
                "automatic": "Tự động",
                "u": "Theo trục U",
                "v": "Theo trục V",
            }
        ),
        "z_level_machining_frame": MappingProxyType(
            {"setup_wcs": "Hệ tọa độ Thiết lập"}
        ),
        "z_level_boundary_policy": MappingProxyType(
            {"trimmed_face": "Biên mặt đã cắt xén"}
        ),
        "z_level_contour_ordering": MappingProxyType(
            {
                "top_down_nearest_safe": "Từ cao xuống thấp · gần và an toàn",
                "top_down_lexicographic": "Từ cao xuống thấp · theo thứ tự",
            }
        ),
        "z_level_linking_mode": MappingProxyType(
            {
                "retract_clearance": "Rút dao về mặt phẳng an toàn",
                "conservative_direct": "Liên kết trực tiếp có kiểm tra",
            }
        ),
        "z_level_safety_scope": MappingProxyType(
            {
                "declared_geometry_and_tool_assembly": (
                    "Hình học và cụm Tool đã khai báo"
                )
            }
        ),
        "z_level_protected_geometry_scope": MappingProxyType(
            {
                "part_boundary_only": "Chỉ biên chi tiết",
                "declared_protected_geometry": "Hình học bảo vệ đã khai báo",
            }
        ),
        "z_level_approach_retract_policy": MappingProxyType(
            {"retract_then_rapid": "Rút dao rồi chạy nhanh"}
        ),
        "z_level_safety_sampling_policy": MappingProxyType(
            {
                "standard": "Tiêu chuẩn",
                "dense": "Dày",
                "very_dense": "Rất dày",
            }
        ),
    }
)


def display_value(value: object, category: str) -> str:
    """Map one internal value at the UI boundary without changing its source."""
    raw = getattr(value, "value", value)
    text = str(raw)
    mapping = DISPLAY_VALUE_MAPPINGS.get(category)
    return text if mapping is None else ui_text(mapping.get(text, text))


def display_value_list(values: object, category: str) -> str:
    """Render a deterministic Vietnamese list of internal presentation values."""
    if isinstance(values, str):
        source = tuple(
            item.strip()
            for item in re.split(r"[,/]", values)
            if item.strip() and item.strip().casefold() != "none"
        )
    else:
        try:
            source = tuple(values)  # type: ignore[arg-type]
        except TypeError:
            source = (values,)
    if category == "safety_component":
        order = {
            key: index
            for index, key in enumerate(DISPLAY_VALUE_MAPPINGS[category])
        }
        source = tuple(
            sorted(
                source,
                key=lambda item: order.get(
                    str(getattr(item, "value", item)), len(order)
                ),
            )
        )
    rendered = tuple(display_value(item, category) for item in source)
    if not rendered:
        return {
            UiLanguage.VI_VN: "Không có",
            UiLanguage.EN_US: "None",
            UiLanguage.KO_KR: "없음",
        }[translation_service().language]
    if len(rendered) == 1:
        return rendered[0]
    conjunction = {
        UiLanguage.VI_VN: " và ",
        UiLanguage.EN_US: " and ",
        UiLanguage.KO_KR: ", ",
    }[translation_service().language]
    return ", ".join(rendered[:-1]) + conjunction + rendered[-1]


UI_TRANSLATIONS = MappingProxyType(
    {
        # Operation display names. Stable strategy IDs and persisted defaults
        # remain English; only presentation strings pass through this catalog.
        **OPERATION_DISPLAY_NAMES,
        # Stage 9A.8 WP1 CAM 3D shell.
        "CAM 3D Function UI": "Giao diện chức năng CAM 3D",
        "Open CAM 3D Function UI": "Mở giao diện chức năng CAM 3D",
        "Part": "Chi tiết",
        "Safe motion": "Chuyển động an toàn",
        "Feature disabled": "Tính năng đang tắt",
        "No data": "Chưa có dữ liệu",
        "WP1 shell only": "Chỉ là khung giao diện WP1",
        "Status": "Trạng thái",
        "Revision": "Phiên bản",
        "Compare revision": "So sánh phiên bản",
        "Blocked": "Bị chặn",
        "Reviewer role": "Vai trò người rà soát",
        "Review notes": "Ghi chú rà soát",
        "Acknowledge all displayed findings": "Xác nhận đã rà soát mọi phát hiện hiển thị",
        "Accept for external dry-run": "Chấp nhận để chuẩn bị chạy thử bên ngoài",
        "Reject": "Từ chối",
        # Workspace shell and shared panels.
        "HMS CAD/CAM — Design": "HMS CAD/CAM — Thiết kế",
        "Operation Manager": "Quản lý nguyên công",
        "Function Editor": "Trình chỉnh sửa chức năng",
        "Diagnostics & Activity": "Chẩn đoán & Hoạt động",
        "Simulation / Post": "Mô phỏng / Post",
        "Reset Workspace Layout": "Khôi phục bố cục làm việc",
        "Show": "Hiện",
        "Hide": "Ẩn",
        "Isolate": "Cô lập",
        "Reset Isolate": "Bỏ cô lập",
        "Color…": "Màu…",
        "Transparency…": "Độ trong suốt…",
        "Reset Appearance": "Khôi phục hiển thị",
        "CAD document": "Tài liệu CAD",
        "Document ID": "ID tài liệu",
        "Fingerprint": "Dấu nhận dạng hình học",
        "Levels": "Cao độ",
        "Toolpaths": "Đường chạy dao",
        "Planes": "Mặt phẳng",
        "Save": "Lưu",
        "Discard": "Không lưu",
        "Cancel": "Hủy",
        "Save Managed Artifact": "Lưu kết quả được quản lý",
        "Selection ID": "ID lựa chọn",
        "Geometry": "Hình học",
        "Bounding box": "Hộp bao",
        "Bounding X": "Kích thước X",
        "Bounding Y": "Kích thước Y",
        "Bounding Z": "Kích thước Z",
        "Assembly occurrences": "Thực thể lắp ráp",
        "Topology objects": "Đối tượng cấu trúc hình học",
        "Lazy": "Nạp khi cần",
        "Mesh": "Lưới",
        "Top": "Trên",
        # Function Editor shell, disclosure and actions.
        "LEGACY": "TRÌNH CŨ",
        "LEGACY EDITOR": "TRÌNH CHỈNH SỬA CŨ",
        "REFERENCE": "THAM CHIẾU",
        "FRAMEWORK": "KHUNG MỚI",
        "FALLBACK": "DỰ PHÒNG",
        "REFERENCE DEMO": "BẢN MẪU THAM CHIẾU",
        "DIAGNOSTICS": "CHẨN ĐOÁN",
        "HELP": "TRỢ GIÚP",
        "Basic": "Cơ bản",
        "Advanced": "Nâng cao",
        "Expert": "Chuyên gia",
        "Collapse All": "Thu gọn tất cả",
        "Expand Relevant": "Mở phần liên quan",
        "Defaults": "Giá trị mặc định",
        "Reset Draft": "Đặt lại bản nháp",
        "Preview": "Xem trước",
        "Validate": "Kiểm tra hợp lệ",
        "Calculate": "Tính toán",
        "Apply": "Áp dụng",
        "Close": "Đóng",
        "No changes": "Không có thay đổi",
        "Modified": "Đã sửa",
        "Invalid": "Không hợp lệ",
        "Applying": "Đang áp dụng",
        "Applied": "Đã áp dụng",
        "Stale": "Đã lỗi thời",
        "Preview CURRENT": "Bản xem trước HIỆN HÀNH",
        "Preview STALE — đã bỏ kết quả cũ": "Bản xem trước ĐÃ LỖI THỜI — đã bỏ kết quả cũ",
        # Common schema sections.
        "BASIC": "CƠ BẢN",
        "GEOMETRY": "HÌNH HỌC",
        "TOOL": "TOOL",
        "CUTTING": "THÔNG SỐ CẮT",
        "LEVELS": "CAO ĐỘ",
        "LINKING": "LIÊN KẾT",
        "ADVANCED": "NÂNG CAO",
        "EXPERT": "CHUYÊN GIA",
        "ENTRY": "VÀO DAO",
        "OPERATION": "NGUYÊN CÔNG",
        "DIRECTION": "HƯỚNG GIA CÔNG",
        "CUT PARAMETERS": "THÔNG SỐ CẮT",
        "LEVELS / LINKING": "CAO ĐỘ / LIÊN KẾT",
        "LEVELS / DEPTH": "CAO ĐỘ / CHIỀU SÂU",
        "CLEARANCE / RETRACT": "AN TOÀN / RÚT DAO",
        "CYCLE / PROCESS": "CHU TRÌNH / QUY TRÌNH",
        "CUTTING PARAMETERS": "THÔNG SỐ CẮT",
        "MACHINE / POST CAPABILITY": "KHẢ NĂNG HỖ TRỢ CỦA MÁY / POST",
        "CAPABILITY AND SAFETY": "KHẢ NĂNG HỖ TRỢ VÀ AN TOÀN",
        "SUMMARY": "TÓM TẮT",
        # Stage17A automatic Facing/Planar parameter presentation.
        "Chế độ tự động tính lại từ bằng chứng; tùy chỉnh giữ ý định người dùng.": (
            "Chế độ tự động tính lại từ bằng chứng; tùy chỉnh giữ ý định người dùng."
        ),
        "Điều chỉnh tỷ lệ bước ngang/bước xuống trong giới hạn hình học thực.": (
            "Điều chỉnh tỷ lệ bước ngang/bước xuống trong giới hạn hình học thực."
        ),
        "Tóm tắt chế độ tự động, giá trị tùy chỉnh và tham số thiếu bằng chứng.": (
            "Tóm tắt chế độ tự động, giá trị tùy chỉnh và tham số thiếu bằng chứng."
        ),
        "Bước ngang tự động": "Bước ngang tự động",
        "Bước xuống tự động": "Bước xuống tự động",
        "Vượt biên tự động": "Vượt biên tự động",
        "THAM SỐ TỰ ĐỘNG": "THAM SỐ TỰ ĐỘNG",
        "Trạng thái tham số tự động": "Trạng thái tham số tự động",
        "Hồ sơ chất lượng": "Hồ sơ chất lượng",
        "Chế độ Bước ngang": "Chế độ Bước ngang",
        "Chế độ Bước xuống": "Chế độ Bước xuống",
        "Chế độ Vượt biên": "Chế độ Vượt biên",
        "Tự động": "Tự động",
        "Tùy chỉnh": "Tùy chỉnh",
        "AUTO": "AUTO",
        # Schema field labels and choice labels.
        "Operation name": "Tên nguyên công",
        "Operation type": "Loại nguyên công",
        "Enabled": "Được bật",
        "Machining Faces": "Bề mặt gia công",
        "Selected Faces": "Bề mặt đã chọn",
        "Selected faces": "Bề mặt đã chọn",
        "Body / Setup": "Thân / Thiết lập",
        "Reselect": "Chọn lại",
        "Remove": "Loại bỏ",
        "Selection": "Vùng chọn",
        "Clear": "Xóa",
        "Select": "Chọn",
        "Select / Add": "Chọn/Thêm",
        "Persistent face IDs": "ID bề mặt cố định",
        "Ball-end tool": "Tool cầu",
        "Ball-end tool required.": "Cần Tool cầu.",
        "Ball-end Tool": "Tool cầu",
        "Ball-end": "Tool cầu",
        "Ball - D10 mm": "Cầu · D10 mm",
        "Tools": "Tool",
        "Tool Assembly": "Cụm Tool",
        "PRIMARY": "Chính",
        "override": "tùy chỉnh thủ công",
        "guardrail": "giới hạn bảo vệ",
        "artifact": "kết quả tính toán",
        "safety": "an toàn",
        "contour": "đường đồng mức",
        "machine-ready": "sẵn sàng chạy máy",
        "Production Post": "Post sản xuất",
        "fail-closed": "chặn an toàn",
        "Rút dao bảo thủ · fallback fail-closed": (
            "Rút dao bảo thủ · chuyển sang phương án chặn an toàn."
        ),
        "algorithm v2": "Thuật toán v2",
        "payload v1": "Phiên bản dữ liệu v1",
        "algorithm v1": "Thuật toán v1",
        "algorithm v3": "Thuật toán v3",
        "payload v3": "Phiên bản dữ liệu v3",
        "safety contract": "hợp đồng an toàn",
        "projection hiện hành": "dữ liệu hiển thị hiện hành",
        "viewport": "vùng hiển thị CAD",
        "WCS": "hệ tọa độ",
        "WCS của Thiết lập": "Hệ tọa độ Thiết lập",
        "Machining zone": "vùng gia công",
        "Machining zone missing": "Thiếu vùng gia công",
        "Parallel Setup": "Thiết lập cao độ Z",
        "safety scope": "phạm vi an toàn",
        "safety sampling": "lấy mẫu an toàn",
        "machine-ready clearance": "khoảng hở sẵn sàng chạy máy",
        "Direct link": "Liên kết trực tiếp",
        "direct link": "liên kết trực tiếp",
        "UNKNOWN": "CHƯA XÁC ĐỊNH",
        "UNSAFE": "KHÔNG AN TOÀN",
        "SAFE": "AN TOÀN",
        "READY": "SẴN SÀNG",
        "Tool details": "Chi tiết Tool",
        "Tool / Shank": "Tool / Cán dao",
        "Holder State": "Trạng thái Holder",
        "Holder scope": "Phạm vi Holder",
        "Machining Direction": "Hướng chạy dao",
        "Direction angle": "Góc chạy dao",
        "Direction preview": "Xem trước hướng",
        "Workplane / Setup": "Mặt phẳng làm việc / Thiết lập",
        "Stepover": "Bước ngang",
        "Tolerance": "Dung sai",
        "Surface Allowance": "Lượng dư bề mặt",
        "Cut Ordering": "Thứ tự cắt",
        "One-way": "Một chiều",
        "Zigzag": "Zíc zắc",
        "Clearance": "Mặt phẳng an toàn",
        "Retract": "Mặt phẳng rút dao",
        "Link clearance": "Khoảng hở liên kết",
        "Linking mode": "Chế độ liên kết",
        "Linking policy": "Chính sách liên kết",
        "Retract between segments": "Rút dao giữa các đoạn",
        "Feed rate": "Lượng chạy dao",
        "Maximum segment length": "Chiều dài đoạn tối đa",
        "Contact tolerance": "Dung sai tiếp xúc",
        "Internal detection threshold": "Ngưỡng phát hiện nội bộ",
        "Validation limits": "Giới hạn kiểm tra",
        "Machine / Setup": "Máy / Thiết lập",
        "Supported": "Được hỗ trợ",
        "Not supported / verified": "Chưa được hỗ trợ / xác minh",
        "Calculation Status": "Trạng thái tính toán",
        "Safety Status": "Trạng thái an toàn",
        "Safety Algorithm": "Thuật toán an toàn",
        "Safety Scope": "Phạm vi an toàn",
        "Checked Components": "Thành phần đã kiểm tra",
        "Unverified Components": "Thành phần chưa xác minh",
        "Machine-ready Clearance": "Khoảng hở sẵn sàng cho máy",
        "Safety counts": "Số liệu an toàn",
        "Safety report": "Báo cáo an toàn",
        "Safety diagnostics": "Chẩn đoán an toàn",
        "Open Details": "Mở chi tiết",
        "Open": "Mở",
        "Operation summary": "Tóm tắt nguyên công",
        "Geometry identity": "ID hình học",
        "Tool Assembly": "Cụm Tool",
        "Profile / chain": "Biên dạng / chuỗi",
        "Pocket region": "Vùng hốc",
        "Islands": "Đảo",
        "Machining pattern": "Kiểu chạy dao",
        "Wall Stock Allowance": "Lượng dư thành",
        "Floor Stock Allowance": "Lượng dư đáy",
        "Stock Allowance": "Lượng dư Phôi",
        "Stepdown": "Bước xuống dao",
        "Depth": "Chiều sâu",
        "Final cutter Z": "Z cuối của dao",
        "Entry Method": "Phương pháp vào dao",
        "Vertical plunge": "Cắm dao thẳng đứng",
        "Plunge feed": "Lượng chạy dao cắm",
        "Algorithm tolerance": "Dung sai thuật toán",
        "Lead Length": "Chiều dài vào/ra dao",
        "Lead-In Length": "Chiều dài Lead-In",
        "Spring finishing pass": "Lượt tinh lặp lại",
        "Canonical start policy": "Chính sách điểm bắt đầu chuẩn",
        "Hole geometry": "Hình học lỗ",
        "Persistent source": "Nguồn cố định",
        "Capability": "Khả năng hỗ trợ",
        # Classic CAM property editor.
        "Work offset": "Gốc làm việc",
        "Nguồn Facing": "Nguồn phay mặt",
        "Top Z": "Z trên",
        "Target Z": "Z mục tiêu",
        "Allowance": "Lượng dư",
        "Clearance Z": "Z an toàn",
        "Retract Z": "Z rút dao",
        "Feed": "Lượng chạy dao",
        "Plunge feed": "Lượng chạy dao cắm",
        "Spindle RPM": "Tốc độ trục chính RPM",
        "Raster angle": "Góc quét",
        "Overtravel": "Vượt biên",
        "Side": "Phía gia công",
        "Contour Top Z": "Z trên biên dạng",
        "Final depth Z": "Z chiều sâu cuối",
        "Contour stepdown": "Bước xuống dao biên dạng",
        "Radial allowance": "Lượng dư hướng kính",
        "Axial allowance": "Lượng dư hướng trục",
        "Contour clearance Z": "Z an toàn biên dạng",
        "Contour retract Z": "Z rút dao biên dạng",
        "Contour feed": "Lượng chạy dao biên dạng",
        "Contour plunge": "Lượng chạy dao cắm biên dạng",
        "Contour spindle": "Tốc độ trục chính biên dạng",
        "Linear lead length": "Chiều dài vào/ra dao thẳng",
        "Contour direction": "Hướng biên dạng",
        "Pocket Top Z": "Z trên của hốc",
        "Pocket Bottom Z": "Z đáy hốc",
        "Pocket stepdown": "Bước xuống dao hốc",
        "Pocket stepover": "Bước ngang hốc",
        "Pocket radial allowance": "Lượng dư hướng kính hốc",
        "Pocket floor allowance": "Lượng dư đáy hốc",
        "Pocket clearance Z": "Z an toàn hốc",
        "Pocket retract Z": "Z rút dao hốc",
        "Pocket feed": "Lượng chạy dao hốc",
        "Pocket plunge": "Lượng chạy dao cắm hốc",
        "Pocket spindle": "Tốc độ trục chính hốc",
        "Pocket tolerance": "Dung sai hốc",
        "Pocket entry": "Cách vào dao hốc",
        "Pocket direction": "Hướng chạy dao hốc",
        "Drilling cycle": "Chu trình khoan",
        "Drilling Top Z": "Z trên khi khoan",
        "Drilling depth": "Chiều sâu khoan",
        "Peck depth": "Chiều sâu nhấp",
        "Drilling clearance Z": "Z an toàn khi khoan",
        "Drilling retract Z": "Z rút dao khi khoan",
        "Drilling feed": "Lượng chạy dao khoan",
        "Drilling spindle": "Tốc độ trục chính khoan",
        "Drilling dwell (s)": "Dừng đáy khi khoan (s)",
        "Drilling tolerance": "Dung sai khoan",
        "Peck retract": "Rút dao khi khoan nhấp",
        "Tapping hand": "Chiều ren ta rô",
        "Tapping mode": "Chế độ ta rô",
        "Tapping Top Z": "Z trên khi ta rô",
        "Tapping final depth Z": "Z chiều sâu cuối khi ta rô",
        "Tapping clearance Z": "Z an toàn khi ta rô",
        "Tapping retract Z": "Z rút dao khi ta rô",
        "Tap nominal diameter": "Đường kính danh nghĩa ta rô",
        "Tap pitch": "Bước ren ta rô",
        "Tapping spindle RPM": "Tốc độ trục chính ta rô RPM",
        "Tapping dwell (s, optional)": "Dừng đáy khi ta rô (s, tùy chọn)",
        "Tapping tolerance": "Dung sai ta rô",
        "Reaming spindle direction": "Chiều trục chính khi doa",
        "Reaming retract policy": "Chính sách rút dao khi doa",
        "Reaming coolant": "Tưới nguội khi doa",
        "Reaming Top Z": "Z trên khi doa",
        "Reaming final depth Z": "Z chiều sâu cuối khi doa",
        "Reaming clearance Z": "Z an toàn khi doa",
        "Reaming retract Z": "Z rút dao khi doa",
        "Finished nominal diameter": "Đường kính danh nghĩa hoàn thiện",
        "Pre-hole diameter (required)": "Đường kính lỗ trước (bắt buộc)",
        "Reaming spindle RPM": "Tốc độ trục chính doa RPM",
        "Feed per revolution": "Lượng chạy dao mỗi vòng",
        "Reaming dwell (s, optional)": "Dừng đáy khi doa (s, tùy chọn)",
        "Reaming tolerance": "Dung sai doa",
        "Derived (read-only)": "Giá trị suy ra (chỉ đọc)",
        "Boring spindle direction": "Chiều trục chính khi tiện lỗ",
        "Boring retract policy": "Chính sách rút dao khi tiện lỗ",
        "Boring coolant": "Tưới nguội khi tiện lỗ",
        "Boring Top Z": "Z trên khi tiện lỗ",
        "Boring final depth Z": "Z chiều sâu cuối khi tiện lỗ",
        "Boring clearance Z": "Z an toàn khi tiện lỗ",
        "Boring retract Z": "Z rút dao khi tiện lỗ",
        "Finished bore diameter": "Đường kính lỗ hoàn thiện",
        "Pre-bore diameter (required)": "Đường kính lỗ trước (bắt buộc)",
        "Boring spindle RPM": "Tốc độ trục chính tiện lỗ RPM",
        "Boring feed per revolution": "Lượng chạy dao tiện lỗ mỗi vòng",
        "Boring dwell (s, optional)": "Dừng đáy khi tiện lỗ (s, tùy chọn)",
        "Boring tolerance": "Dung sai tiện lỗ",
        "Boring derived (read-only)": "Giá trị tiện lỗ suy ra (chỉ đọc)",
        "BORING_BAR current": "BORING_BAR hiện hành",
        "Toolpath": "Đường chạy dao",
        "Finishing pass": "Lượt tinh",
        "Editable": "Có thể chỉnh sửa",
        # Sources and generic read-only values.
        "Machine": "Máy",
        "Profile": "Biên dạng",
        "Project": "Dự án",
        "Setup": "Thiết lập",
        "Stock": "Phôi",
        "profile": "biên dạng",
        "hole": "lỗ",
        "PROFILE MISSING": "THIẾU BIÊN DẠNG",
        "PROFILE BOUND": "ĐÃ LIÊN KẾT BIÊN DẠNG",
        "HOLE MISSING": "THIẾU LỖ",
        "HOLE PATTERN": "MẪU LỖ",
        "HMS Default": "Mặc định HMS",
        "Derived": "Suy ra",
        "none": "không có",
        "Not calculated": "Chưa tính toán",
        "Not verified": "Chưa xác minh",
        "Missing tool": "Thiếu Tool",
        "Missing/unsupported tool": "Thiếu Tool hoặc Tool không được hỗ trợ",
        "Missing machine": "Thiếu máy",
        "Replace the draft face selection from the viewport": "Thay vùng chọn bề mặt trong bản nháp bằng vùng chọn từ vùng hiển thị",
        "Remove currently selected viewport faces from the draft": "Loại các bề mặt đang chọn trong vùng hiển thị khỏi bản nháp",
        "Clear the draft face selection": "Xóa vùng chọn bề mặt trong bản nháp",
        "Setup X + angle": "Trục X của thiết lập + góc",
        "Retract between segments · horizontal rapid at clearance": "Rút dao giữa các đoạn · chạy nhanh ngang tại mặt phẳng an toàn",
        "Derived safety minimum · not a certified machining clearance": "Mức an toàn tối thiểu được suy ra · không phải khoảng hở gia công đã chứng nhận",
        "v3 required": "yêu cầu v3",
        "No safety findings": "Không có phát hiện an toàn",
        "Available · READY + SAFE v3": "Khả dụng · SẴN SÀNG + AN TOÀN v3",
        "Blocked · requires current READY + SAFE algorithm v3 artifact": "Bị chặn · cần kết quả hiện hành SẴN SÀNG + AN TOÀN của thuật toán v3",
        "Blocked · Parallel production Post capability is not available": "Bị chặn · Post sản xuất chưa hỗ trợ Gia công tinh song song",
        "Blocked · artifact/safety capability is insufficient": "Bị chặn · kết quả hoặc khả năng an toàn chưa đủ",
        "Safe — verified within declared scope": "An toàn — đã xác minh trong phạm vi công bố",
        "Unsafe": "Không an toàn",
        "Unknown": "Chưa xác định",
        "Cancelled": "Đã hủy",
        "cancelled": "ĐÃ HỦY",
        "Failed": "Thất bại",
        "Candidate": "Ứng viên",
        "Holder verified": "Holder đã xác minh",
        "Holder not declared · holder unverified": "Chưa khai báo Holder · Holder chưa được xác minh",
        "Holder verification unavailable": "Không thể xác minh Holder",
        "Holder verified · cutter/shank/holder checked": "Holder đã xác minh · đã kiểm tra dao/cán dao/Holder",
        # Parallel help, validation and diagnostics.
        "Parallel Finishing": "Gia công tinh song song",
        "Parallel Finishing · algorithm v3 · payload v1": "Gia công tinh song song · thuật toán v3 · dữ liệu v1",
        "Parallel Finishing operation identity.": "Thông tin nhận diện nguyên công Gia công tinh song song.",
        "U pass direction · V stepover direction · W tool axis.": "U là hướng lượt cắt · V là hướng bước ngang · W là trục Tool.",
        "Foundation ball-center path parameters.": "Thông số nền tảng của đường tâm Tool cầu.",
        "Surface/chordal tolerance; contact tolerance is shown separately.": "Dung sai bề mặt/dây cung; dung sai tiếp xúc được hiển thị riêng.",
        "Conservative retract-only linking.": "Liên kết bảo thủ, chỉ dùng chuyển động rút dao.",
        "Algorithm-used values and read-only safety limits.": "Giá trị thuật toán sử dụng và giới hạn an toàn chỉ đọc.",
        "Internal numerical/safety threshold; not a safe machining clearance.": "Ngưỡng số học/an toàn nội bộ; không phải khoảng hở gia công an toàn.",
        "Applied/draft summary without a production-safety claim.": "Tóm tắt trạng thái đã áp dụng/bản nháp, không khẳng định an toàn sản xuất.",
        "Ball-end · fixed 3-axis · selected trimmed BRep faces · one-way/zigzag · Toolpath IR · Simulation · safety validation": "Dao cầu · ba trục cố định · bề mặt BRep đã cắt xén và chọn · một chiều/zíc zắc · Toolpath IR · Mô phỏng · kiểm tra an toàn",
        "Flat/bull end, 5-axis, holder avoidance, rest machining, adaptive cusp, machine-ready clearance and production Post are unavailable": "Dao đầu phẳng/bo, 5 trục, tránh Holder, gia công phần dư, bước nhấp nhô thích ứng, khoảng hở sẵn sàng cho máy và Post sản xuất chưa khả dụng",
        "20,000 passes · 25,000 points/curve · 100,000 points/result": "20.000 lượt · 25.000 điểm/đường cong · 100.000 điểm/kết quả",
        "Stepover must be > 0.": "Bước ngang phải lớn hơn 0.",
        "Tolerance must be > 0.": "Dung sai phải lớn hơn 0.",
        "Surface allowance cannot be negative.": "Lượng dư bề mặt không được âm.",
        "Clearance must be above Retract.": "Mặt phẳng an toàn phải cao hơn mặt phẳng rút dao.",
        "Link clearance cannot be negative.": "Khoảng hở liên kết không được âm.",
        "Feed rate must be > 0.": "Lượng chạy dao phải lớn hơn 0.",
        "Maximum segment length must be > 0.": "Chiều dài đoạn tối đa phải lớn hơn 0.",
        "Holder not declared; cutter/shank are checked and holder remains unverified.": "Chưa khai báo Holder; đã kiểm tra dao/cán dao và Holder vẫn chưa được xác minh.",
        "Holder verification unavailable; Calculate cannot publish READY.": "Không thể xác minh Holder; Tính toán không thể công bố trạng thái SẴN SÀNG.",
        "Machine-ready clearance is not verified.": "Khoảng hở sẵn sàng cho máy chưa được xác minh.",
        "Production Post is not available for Parallel Finishing.": "Post production chưa hỗ trợ Gia công tinh song song.",
        "Parallel draft is invalid.": "Bản nháp Gia công tinh song song không hợp lệ.",
        "Estimated pass count exceeds guardrail.": "Số lượt cắt ước tính vượt giới hạn bảo vệ.",
        "Face selection is stale; select faces again.": "Vùng chọn bề mặt đã lỗi thời; hãy chọn lại bề mặt.",
        "Select at least one machining face.": "Hãy chọn ít nhất một bề mặt gia công.",
        "Selected faces use different revisions.": "Các bề mặt đã chọn dùng bản sửa đổi khác nhau.",
        "Tool Assembly is missing.": "Thiếu Cụm Tool.",
        "Tool Definition is missing.": "Thiếu định nghĩa Tool.",
        "UNSUPPORTED_TOOL_GEOMETRY — select a ball-end tool.": "HÌNH HỌC TOOL KHÔNG ĐƯỢC HỖ TRỢ — hãy chọn Tool cầu.",
        "Holder verification unavailable or stale.": "Thông tin xác minh Holder không khả dụng hoặc đã lỗi thời.",
        "A compatible milling machine is required.": "Cần một máy phay tương thích.",
        "Tolerance must be greater than zero.": "Dung sai phải lớn hơn 0.",
        # Parallel progress and safety-detail dialog.
        "Phase: Validation": "Giai đoạn: Kiểm tra hợp lệ",
        "Overall: 0%": "Tổng thể: 0%",
        "Cancel Calculation": "Hủy tính toán",
        "Items: 0 / 0": "Hạng mục: 0 / 0",
        "Items: 0 / 0 · Preparing immutable applied snapshot": "Hạng mục: 0 / 0 · Đang chuẩn bị ảnh chụp trạng thái đã áp dụng bất biến",
        "Parallel Safety Details": "Chi tiết an toàn Gia công tinh song song",
        "Code": "Mã",
        "Severity": "Mức độ",
        "Pass": "Lượt cắt",
        "Segment": "Đoạn",
        "Motion": "Chuyển động",
        "Component": "Thành phần",
        "Closest distance": "Khoảng cách gần nhất",
        "Penetration": "Độ xuyên",
        "Occurrence count": "Số lần xuất hiện",
        "Message": "Thông báo",
        "Copy selected cell": "Sao chép ô đã chọn",
        # Operation Manager.
        "CAM Project / Operation": "Dự án CAM / Nguyên công",
        "Tên, strategy, dao, status hoặc ID…": "Tên, chiến lược, dao, trạng thái hoặc ID…",
        "+ Thêm": "+ Thêm nguyên công",
        "Thêm operation": "Thêm nguyên công",
        "Thêm thao tác": "Thêm thao tác",
        "Setup chưa có operation": "Thiết lập chưa có nguyên công",
        "Thêm operation đầu tiên bằng strategy hiện có.": "Thêm nguyên công đầu tiên bằng chiến lược hiện có.",
        # Simulation.
        "Simulation 7C.3": "Mô phỏng 7C.3",
        "Simulation": "Mô phỏng",
        "Sampling policy": "Chính sách lấy mẫu",
        "Run Simulation": "Chạy mô phỏng",
        "Cancel": "Hủy",
        "Hide Overlay": "Ẩn lớp phủ",
        "Show Overlay": "Hiện lớp phủ",
        "Clear Result": "Xóa kết quả",
        "Open issue details": "Mở chi tiết vấn đề",
        "Simulation issues": "Vấn đề mô phỏng",
        "Filter": "Bộ lọc",
        "Clear selection": "Bỏ vùng chọn",
        "Copy technical details": "Sao chép chi tiết kỹ thuật",
        "Category": "Phân loại",
        "Operation / Result": "Nguyên công / Kết quả",
        "Event": "Sự kiện",
        "Sample": "Mẫu",
        "Entities": "Thực thể",
        "Message / evidence": "Thông báo / bằng chứng",
        "Operation": "Nguyên công",
        "Artifact state": "Trạng thái kết quả",
        "Fixtures": "Đồ gá",
        "Tool / Holder / Machine": "Tool / Holder / Máy",
        "Sampling": "Lấy mẫu",
        "Run state": "Trạng thái chạy",
        "PASS / WARN / FAIL": "ĐẠT / CẢNH BÁO / KHÔNG ĐẠT",
        "Issues": "Vấn đề",
        "Samples": "Mẫu",
        "Elapsed": "Thời gian",
        "Overlay": "Lớp phủ",
        "Result": "Kết quả",
        "Max linear step (project unit)": "Bước thẳng tối đa (đơn vị dự án)",
        "Chord tolerance (project unit)": "Dung sai dây cung (đơn vị dự án)",
        "Max arc angle (degree)": "Góc cung tối đa (độ)",
        "Geometric tolerance (project unit)": "Dung sai hình học (đơn vị dự án)",
        "Maximum samples (≤ 1,000,000)": "Số mẫu tối đa (≤ 1.000.000)",
        "Maximum issues (≤ 10,000)": "Số vấn đề tối đa (≤ 10.000)",
        "Display point cap (≤ 1,000,000)": "Giới hạn điểm hiển thị (≤ 1.000.000)",
        "Display marker cap (≤ 10,000)": "Giới hạn dấu hiển thị (≤ 10.000)",
        "No current result": "Không có kết quả hiện hành",
        "not rendered": "chưa kết xuất",
        # Post and Program Assembly.
        "Post Processor · Production Workflow 7D.2.3": "Bộ xử lý Post · Quy trình sản xuất 7D.2.3",
        "Job / Setup": "Công việc / Thiết lập",
        "Tool / Holder": "Tool / Holder",
        "Operation / provenance": "Nguyên công / nguồn gốc",
        "Production profile": "Cấu hình sản xuất",
        "Profile immutable; canonical production output is separate from dummy output": "Cấu hình bất biến; đầu ra sản xuất chuẩn tách biệt với đầu ra giả lập",
        "Contract": "Hợp đồng",
        "Controller Tool Binding": "Liên kết Tool với bộ điều khiển",
        "T station": "Trạm T",
        "H length offset": "Bù chiều dài H",
        "D diameter offset": "Bù đường kính D",
        "Comment": "Ghi chú",
        "Fingerprint": "Dấu nhận dạng hình học",
        "Program context": "Ngữ cảnh chương trình",
        "Filename": "Tên tệp",
        "File/comment metadata": "Siêu dữ liệu tệp/ghi chú",
        "Safe Z (MM)": "Z an toàn (MM)",
        "Work offset": "Gốc làm việc",
        "Cutter compensation": "Bù bán kính dao",
        "Simulation gate": "Cổng mô phỏng",
        "Overwrite": "Ghi đè",
        "Local / mapped / UNC filesystem": "Hệ thống tệp cục bộ / ổ ánh xạ / UNC",
        "Local / mapped / UNC": "Cục bộ / ổ ánh xạ / UNC",
        "Data-server directory": "Thư mục máy chủ dữ liệu",
        "External target type": "Loại đích ngoài",
        "External local/mapped/UNC directory (optional)": "Thư mục ngoài cục bộ/ổ ánh xạ/UNC (tùy chọn)",
        "External target": "Đích ngoài",
        "Browse…": "Duyệt…",
        "Apply draft": "Áp dụng bản nháp",
        "Reset draft": "Đặt lại bản nháp",
        "Generate Post": "Tạo Post",
        "Save Managed Artifact": "Lưu kết quả được quản lý",
        "Export": "Xuất",
        "Show Export Details": "Hiện chi tiết xuất",
        "Clear Post Result": "Xóa kết quả Post",
        "Clear Managed Artifact": "Xóa kết quả được quản lý",
        "Preview metadata: —": "Siêu dữ liệu xem trước: —",
        "Diagnostics": "Chẩn đoán",
        "Safety Diagnostics": "Chẩn đoán an toàn",
        "Record": "Bản ghi",
        "Evidence": "Bằng chứng",
        "Source": "Nguồn",
        "Program Assembly · Production Workflow 7D.3.2": "Lắp ráp chương trình · Quy trình sản xuất 7D.3.2",
        "Profile / WCS": "Cấu hình / hệ tọa độ",
        "Simulation gates": "Cổng mô phỏng",
        "Artifacts": "Các kết quả",
        "Project / shared production context": "Dự án / ngữ cảnh sản xuất dùng chung",
        "Shared program context": "Ngữ cảnh chương trình dùng chung",
        "External destination": "Đích ngoài",
        "Create destination if missing": "Tạo đích nếu chưa có",
        "Apply Context": "Áp dụng ngữ cảnh",
        "Output filename": "Tên tệp đầu ra",
        "Global comment metadata": "Siêu dữ liệu ghi chú chung",
        "Overwrite policy": "Chính sách ghi đè",
        "Target type": "Loại đích",
        "Destination": "Đích",
        "Explicit ordered operation list": "Danh sách nguyên công có thứ tự tường minh",
        "Add Selected Operation": "Thêm nguyên công đã chọn",
        "Remove Operation": "Loại bỏ nguyên công",
        "Move Up": "Di chuyển lên",
        "Move Down": "Di chuyển xuống",
        "Clear List": "Xóa danh sách",
        "Selected operation section context": "Ngữ cảnh phần nguyên công đã chọn",
        "Apply Operation": "Áp dụng nguyên công",
        "Reset Operation": "Đặt lại nguyên công",
        "Tool comment": "Ghi chú Tool",
        "Validate Assembly": "Kiểm tra lắp ráp",
        "Generate Assembly": "Tạo chương trình lắp ráp",
        "Show Diagnostics": "Hiện chẩn đoán",
        "Clear Assembly Result": "Xóa kết quả lắp ráp",
        "Search exact preview": "Tìm chính xác trong bản xem trước",
        "Find Next": "Tìm tiếp",
        "Copy Checksum": "Sao chép tổng kiểm",
        "Jump to Section": "Chuyển đến phần",
        "Order": "Thứ tự",
        "Strategy": "Chiến lược",
        "Operation status": "Trạng thái nguyên công",
        "Safe Z": "Z an toàn",
        "Compensation": "Bù dao",
        "Spindle/RPM": "Trục chính/RPM",
        "Est. lines": "Số dòng ước tính",
        "Compatibility": "Khả năng tương thích",
        "Section": "Phần",
        "Set T = H = D": "Đặt T = H = D",
        "(none)": "(không có)",
        "(missing)": "(thiếu)",
        "LEGACY_WORKNC_LEFT (G41)": "WORKNC CŨ - TRÁI (G41)",
        "FROM_PROGRAM_IR_ONLY": "CHỈ TỪ IR CHƯƠNG TRÌNH",
        # Display-only enum labels and severity/status values.
        "ALL": "TẤT CẢ",
        "ERROR": "LỖI",
        "WARNING": "CẢNH BÁO",
        "INFO": "THÔNG TIN",
        "MISSING": "THIẾU",
        "CURRENT": "HIỆN HÀNH",
        "READY": "SẴN SÀNG",
        "VALID": "HỢP LỆ",
        "DIRTY": "ĐÃ SỬA",
        "COMPUTING": "ĐANG TÍNH",
        "FAILED": "THẤT BẠI",
        "CANCELLED": "ĐÃ HỦY",
        "STALE": "ĐÃ LỖI THỜI",
        "INVALID": "KHÔNG HỢP LỆ",
        "ENABLED": "ĐÃ BẬT",
        "DISABLED": "ĐÃ TẮT",
        "IDLE": "ĐANG CHỜ",
        "SAFE": "AN TOÀN",
        "UNSAFE": "KHÔNG AN TOÀN",
        "UNKNOWN": "CHƯA XÁC ĐỊNH",
        "ACTIVE": "ĐANG HOẠT ĐỘNG",
        "BLOCKED": "BỊ CHẶN",
        "DRAFT": "BẢN NHÁP",
        "NEEDS INPUT": "CẦN DỮ LIỆU",
        "NEEDS CALC": "CẦN TÍNH",
        "NOT RUN": "CHƯA CHẠY",
        "NOT GENERATED": "CHƯA TẠO",
        "NOT EXPORTED": "CHƯA XUẤT",
        "PASS": "ĐẠT",
        "WARN": "CẢNH BÁO",
        "FAIL": "KHÔNG ĐẠT",
        "MALFORMED": "SAI DẠNG",
        "PUBLISHED": "ĐÃ CÔNG BỐ",
        "EXPORTED": "ĐÃ XUẤT",
        "NEVER_EXPORTED": "CHƯA TỪNG XUẤT",
        "OUTDATED": "ĐÃ CŨ",
        "VALIDATING": "ĐANG KIỂM TRA",
        "GENERATING": "ĐANG TẠO",
        "WRITING": "ĐANG GHI",
        "VERIFYING": "ĐANG XÁC MINH",
        "COMPLETED": "HOÀN TẤT",
        "RUNNING": "ĐANG CHẠY",
        "CANCELLING": "ĐANG HỦY",
        "UNSUPPORTED": "KHÔNG ĐƯỢC HỖ TRỢ",
        "GROUP": "NHÓM",
        "REQUIRE_PASS": "YÊU CẦU ĐẠT",
        "ALLOW_WARN": "CHO PHÉP CẢNH BÁO",
        "OPTIONAL": "TÙY CHỌN",
        "FAIL_IF_EXISTS": "KHÔNG GHI ĐÈ NẾU ĐÃ TỒN TẠI",
        "REPLACE_IF_SAME_ARTIFACT": "THAY NẾU CÙNG KẾT QUẢ",
        "REPLACE_EXPLICIT": "GHI ĐÈ TƯỜNG MINH",
        # Legacy-editor enum presentation (itemData keeps these raw values).
        "stock_box": "Phôi dạng hộp",
        "planar_face": "Bề mặt phẳng",
        "climb": "Phay thuận",
        "conventional": "Phay nghịch",
        "bidirectional": "Hai chiều",
        "planar_face_outer": "Biên ngoài của bề mặt phẳng",
        "closed_wire": "Chuỗi kín",
        "on": "Trên biên dạng",
        "inside": "Bên trong",
        "outside": "Bên ngoài",
        "vertical_plunge": "Cắm dao thẳng đứng",
        "spot_drill": "Khoan tâm",
        "drill": "Khoan",
        "peck_drill": "Khoan nhấp",
        "retract_height": "Mặt phẳng rút dao",
        "clearance_height": "Mặt phẳng an toàn",
        "right_hand_tap": "Ta rô ren phải",
        "left_hand_tap": "Ta rô ren trái",
        "rigid": "Đồng bộ cứng",
        "floating": "Đầu bù nổi",
        "clockwise": "Theo chiều kim đồng hồ",
        "counterclockwise": "Ngược chiều kim đồng hồ",
        "controlled_feed": "Rút dao có lượng chạy dao",
        "off": "Tắt",
        "flood": "Tưới nguội tràn",
        "mist": "Tưới nguội sương",
        "through_tool": "Tưới nguội xuyên Tool",
        "general": "Tổng quát",
        "mill": "Phay",
        "turn": "Tiện",
        "mill_turn": "Phay tiện",
        "box": "Hộp",
        "cylinder": "Trụ",
        "from_model": "Từ mô hình",
        "custom_geometry": "Hình học tùy chỉnh",
        # Legacy Function Editor descriptions are translated at the UI boundary.
        "Chọn quan hệ của tâm dao với profile.": "Chọn quan hệ của tâm dao với biên dạng.",
        "Basic giữ các quyết định người vận hành thường xuyên dùng.": "Cơ bản giữ các quyết định người vận hành thường xuyên dùng.",
        "1 Chain · planar_face_outer · RESOLVED": "1 chuỗi · biên ngoài mặt phẳng · ĐÃ ĐỒNG BỘ",
        "Summary hình học; selection chi tiết thực hiện trong viewport.": "Tóm tắt hình học; việc chọn chi tiết thực hiện trong vùng hiển thị.",
        "Geometry reference được hiển thị bằng summary, không lộ raw key.": "Tham chiếu hình học được hiển thị bằng bản tóm tắt, không lộ khóa nội bộ.",
        "Geometry chứa selection summary và preview/focus hook.": "Hình học chứa bản tóm tắt lựa chọn và liên kết xem trước/tập trung.",
        "Tool Assembly đến từ Tool Library của project.": "Cụm Tool đến từ thư viện Tool của dự án.",
        "Feed 500 mm/min · 4500 RPM": "Lượng chạy dao 500 mm/min · 4500 RPM",
        "Tốc độ trục chính; kiểm tra capability máy khi production binding.": "Tốc độ trục chính; kiểm tra khả năng máy khi liên kết sản xuất.",
        "Cutting gom feed, spindle và lượng cắt theo workflow.": "Cắt gọt gồm lượng chạy dao, trục chính và lượng cắt theo quy trình.",
        "Levels dùng semantic Top/Depth và nguồn Stock/Geometry.": "Cao độ dùng ý nghĩa Đỉnh/Chiều sâu và nguồn Phôi/Hình học.",
        "Linking": "Liên kết đường chạy dao",
        "Safe Z 20 · Linear lead": "Z an toàn 20 · Dẫn dao tuyến tính",
        "Linking hiển thị safe motion kế thừa và field phụ thuộc mode.": "Liên kết đường chạy dao hiển thị chuyển động an toàn kế thừa và trường phụ thuộc chế độ.",
        "Radial allowance không được âm.": "Lượng dư hướng kính không được âm.",
        "Advanced chứa override ít dùng và collapsed mặc định.": "Nâng cao chứa các giá trị ghi đè ít dùng và mặc định thu gọn.",
        "Tolerance nhỏ hơn thường tăng số điểm và thời gian tính.": "Dung sai nhỏ hơn thường tăng số điểm và thời gian tính.",
        "Expert là prototype presentation-only. Thay đổi precision có thể ảnh hưởng chất lượng và thời gian; Stage 9A.4 không gửi vào engine.": "Chuyên sâu là bản mẫu chỉ dành cho trình bày. Thay đổi độ chính xác có thể ảnh hưởng chất lượng và thời gian; Giai đoạn 9A.4 không gửi vào bộ xử lý.",
        "Contour 2D": "Biên dạng 2D",
        "Spindle, feed và coolant theo contract hiện có.": "Trục chính, lượng chạy dao và tưới nguội theo hợp đồng hiện có.",
        "Controller-neutral; cycle code chỉ được quyết định ở Post.": "Độc lập bộ điều khiển; mã chu trình chỉ được quyết định ở Post.",
        "Tolerance hình học v1; không thêm tham số chuyên sâu giả.": "Dung sai hình học v1; không thêm tham số chuyên sâu giả.",
        "Chọn Tool Assembly project-owned; thay đổi chỉ nằm trong draft.": "Chọn cụm Tool thuộc dự án; thay đổi chỉ nằm trong bản nháp.",
        "Tool Assembly và chi tiết read-only từ Tool Library.": "Cụm Tool và chi tiết chỉ đọc từ thư viện Tool.",
        "Stepover không được lớn hơn đường kính dao.": "Bước ngang không được lớn hơn đường kính dao.",
        "Climb, conventional hoặc bidirectional theo contract Facing v1.": "Phay thuận, phay nghịch hoặc hai chiều theo hợp đồng Phay mặt v1.",
        "Facing v1 yêu cầu Top Z bằng mặt trên Stock BOX.": "Phay mặt v1 yêu cầu Z đỉnh bằng mặt trên của Phôi dạng hộp.",
        "Top, target, allowance và phân lớp theo Setup WCS.": "Đỉnh, đích, lượng dư và phân lớp theo hệ tọa độ Thiết lập.",
        "Override ít dùng thuộc contract Facing v1.": "Giá trị ghi đè ít dùng thuộc hợp đồng Phay mặt v1.",
        "Contour v1 nhận đúng một loop kín LINE/ARC; không tự đảo geometry.": "Biên dạng v1 nhận đúng một vòng kín LINE/ARC; không tự đảo hình học.",
        "Select/Rebind tạo GeometryReference typed; preview không giữ OCP object.": "Chọn/Liên kết lại tạo tham chiếu hình học có kiểu; phần xem trước không giữ đối tượng OCP.",
        "Nguồn profile": "Nguồn cấu hình",
        "Nguồn phải khớp loại GeometryReference đã Select.": "Nguồn phải khớp loại tham chiếu hình học đã chọn.",
        "Chọn project-owned Tool Assembly; hình học dao là read-only.": "Chọn cụm Tool thuộc dự án; hình học dao là chỉ đọc.",
        "Tool Library là nguồn chân lý cho diameter, corner radius, holder và stickout.": "Thư viện Tool là nguồn chân lý cho đường kính, bán kính góc, Holder và độ nhô dao.",
        "Phía contour": "Phía biên dạng",
        "Không đồng nhất field này với orientation hoặc cutting direction.": "Không đồng nhất trường này với hướng hình học hoặc hướng cắt.",
        "Generator quyết định traversal từ Side + Direction; không sửa geometry.": "Bộ tạo quyết định thứ tự chạy từ Phía + Hướng; không sửa hình học.",
        "Contour v1 offset trong HMS; không có CONTROL/WEAR, D offset hoặc G41/G42.": "Biên dạng v1 bù trong HMS; không có CONTROL/WEAR, bù D hoặc G41/G42.",
        "Side, cutting direction, allowance và tốc độ công nghệ.": "Phía, hướng cắt, lượng dư và tốc độ công nghệ.",
        "Tọa độ tuyệt đối trong Setup WCS; không phải machine coordinate.": "Tọa độ tuyệt đối trong hệ tọa độ Thiết lập; không phải tọa độ máy.",
        "Chiều cắt đi theo -Z; axial allowance được cộng vào final cutter depth.": "Chiều cắt đi theo -Z; lượng dư dọc trục được cộng vào chiều sâu dao cuối.",
        "Contour v1 luôn dùng linear lead-in và linear lead-out cùng chiều dài.": "Biên dạng v1 luôn dùng dẫn dao vào và ra tuyến tính cùng chiều dài.",
        "Clearance, retract và linear lead v1; mọi Z đều thuộc Setup WCS.": "Khoảng an toàn, rút dao và dẫn dao tuyến tính v1; mọi Z đều thuộc hệ tọa độ Thiết lập.",
        "Lặp lại loop tại lớp cuối; không phải rest machining.": "Lặp lại vòng tại lớp cuối; không phải gia công phần còn lại.",
        "Machine requirement hiện có; domain kiểm tra capability/feed/spindle.": "Yêu cầu máy hiện có; miền kiểm tra khả năng/lượng chạy dao/trục chính.",
        "Tùy chọn ít dùng nhưng có trong Contour v1.": "Tùy chọn ít dùng nhưng có trong Biên dạng v1.",
        "Algorithm policy read-only; Contour v1 không có tolerance/filter/post override.": "Chính sách thuật toán chỉ đọc; Biên dạng v1 không có ghi đè dung sai/bộ lọc/Post.",
        "Các quyết định Pocket cốt lõi nằm trong Geometry, Tool, Cutting, Levels và Entry.": "Các quyết định Hốc cốt lõi nằm trong Hình học, Tool, Cắt gọt, Cao độ và Vào dao.",
        "Pocket v1 nhận đúng một outer loop kín LINE/ARC.": "Hốc v1 nhận đúng một vòng ngoài kín LINE/ARC.",
        "Select/Rebind dùng GeometryReference typed; không giữ OCP object.": "Chọn/Liên kết lại dùng tham chiếu hình học có kiểu; không giữ đối tượng OCP.",
        "Pocket v1 fail-closed khi profile có inner loop; UI không tự suy ra island.": "Hốc v1 chặn an toàn khi biên dạng có vòng trong; giao diện không tự suy ra đảo.",
        "Tool Library là nguồn chân lý; Pocket v1 chỉ hỗ trợ END_MILL hợp lệ.": "Thư viện Tool là nguồn chân lý; Hốc v1 chỉ hỗ trợ END_MILL hợp lệ.",
        "Generator Pocket v1 chỉ có deterministic inward offset loops.": "Bộ tạo Hốc v1 chỉ có các vòng bù vào trong xác định.",
        "Đổi traversal của offset loops; không tự đảo geometry nguồn.": "Đổi thứ tự chạy của các vòng bù; không tự đảo hình học nguồn.",
        "Offset pattern, direction, stepover, wall allowance và tốc độ cắt.": "Mẫu bù, hướng, bước ngang, lượng dư thành và tốc độ cắt.",
        "Nominal bottom trong Setup WCS; floor allowance nâng final cutter Z.": "Đáy danh nghĩa trong hệ tọa độ Thiết lập; lượng dư đáy nâng Z dao cuối.",
        "Derived = Bottom Z + floor allowance; không phải input thứ hai.": "Giá trị dẫn xuất = Z đáy + lượng dư đáy; không phải đầu vào thứ hai.",
        "Số lớp đã Apply": "Số lớp đã áp dụng",
        "Derived theo thuật toán pocket_depth_levels hiện có.": "Được dẫn xuất theo thuật toán pocket_depth_levels hiện có.",
        "Absolute Setup-WCS Z, chia lớp và hai allowance độc lập của domain v1.": "Z tuyệt đối của hệ tọa độ Thiết lập, chia lớp và hai lượng dư độc lập của miền v1.",
        "Pocket v1 chỉ hỗ trợ vertical plunge; không có ramp/helix/pre-drill.": "Hốc v1 chỉ hỗ trợ cắm dao thẳng; không có vào dao dốc/xoắn/khoan trước.",
        "Generator plunge thẳng tại start của từng offset loop.": "Bộ tạo cắm dao thẳng tại điểm đầu của từng vòng bù.",
        "Domain yêu cầu Clearance >= Retract > Top.": "Miền yêu cầu Khoảng an toàn >= Rút dao > Đỉnh.",
        "Safe motion v1 chỉ có explicit Clearance và Retract trong Setup WCS.": "Chuyển động an toàn v1 chỉ có Khoảng an toàn và Rút dao tường minh trong hệ tọa độ Thiết lập.",
        "Precision của offset/depth algorithm; giá trị nhỏ có thể tăng chi phí tính.": "Độ chính xác của thuật toán bù/chiều sâu; giá trị nhỏ có thể tăng chi phí tính.",
        "Precision duy nhất thực sự tồn tại trong Pocket v1.": "Độ chính xác duy nhất thực sự tồn tại trong Hốc v1.",
        # Parallel validation and safety diagnostics (codes remain unchanged).
        "Parallel candidate is not a complete Toolpath IR artifact.": "Ứng viên Gia công tinh song song không phải là kết quả Toolpath IR hoàn chỉnh.",
        "Declared machining/protected geometry is missing from the safety mesh.": "Hình học gia công/bảo vệ đã khai báo bị thiếu trong lưới an toàn.",
        "Protected triangle limit exceeded before broad phase.": "Đã vượt giới hạn tam giác bảo vệ trước bước kiểm tra va chạm sơ bộ.",
        "Swept validation subdivision limit exceeded.": "Đã vượt giới hạn phân đoạn kiểm tra toàn bộ chuyển động.",
        "Total broad/narrow safety check limit exceeded.": "Đã vượt tổng giới hạn kiểm tra an toàn sơ bộ/chi tiết.",
        "Collision candidate limit exceeded.": "Đã vượt giới hạn ứng viên va chạm.",
        "Narrow-phase safety check limit exceeded.": "Đã vượt giới hạn kiểm tra an toàn chi tiết.",
        "Unique safety finding limit exceeded.": "Đã vượt giới hạn phát hiện an toàn duy nhất.",
        "Parallel safety validation was cancelled before publish.": "Kiểm tra an toàn Gia công tinh song song đã bị hủy trước khi công bố.",
        "Tool assembly geometry is incomplete.": "Hình học cụm Tool chưa hoàn chỉnh.",
        "Parallel safety holder snapshot is missing or mismatched": "Bản dữ liệu Holder dùng để kiểm tra an toàn bị thiếu hoặc không khớp",
        "Parallel safety validation failed at an internal boundary.": "Kiểm tra an toàn Gia công tinh song song thất bại tại ranh giới nội bộ.",
        "Parallel path crosses a sharp normal discontinuity.": "Đường chạy song song đi qua vị trí gián đoạn pháp tuyến sắc.",
        "Concave local radius is not accessible to the selected ball-end tool.": "Tool cầu đã chọn không tiếp cận được bán kính lõm cục bộ.",
        "Clearance and retract planes are required for safety validation.": "Cần khai báo mặt phẳng an toàn và rút dao để kiểm tra an toàn.",
        "Parallel calculation became stale before generation started.": "Tính toán Gia công tinh song song đã lỗi thời trước khi bắt đầu tạo đường chạy.",
        "Parallel result became stale before artifact publish.": "Kết quả Gia công tinh song song đã lỗi thời trước khi công bố.",
        "Safety applies to the declared fixed-axis geometry scene; this is not a universal production certificate.": "An toàn chỉ áp dụng cho cảnh hình học trục cố định đã khai báo; đây không phải chứng nhận sản xuất phổ quát.",
        "Tool-center normals use mesh facets, not original BRep differentials.": "Pháp tuyến tâm Tool dùng mặt lưới, không dùng vi phân BRep gốc.",
        "Parallel toolpath file could not be published atomically.": "Không thể công bố toàn vẹn tệp đường chạy dao song song.",
        "Parallel draft is invalid.": "Bản nháp Gia công tinh song song không hợp lệ.",
        "Holder not declared; cutter/shank are checked and holder remains unverified.": "Chưa khai báo Holder; dao cắt/cán dao được kiểm tra và Holder vẫn chưa được xác minh.",
        "Holder verification unavailable; Calculate cannot publish READY.": "Không thể xác minh Holder; Tính toán không thể công bố trạng thái SẴN SÀNG.",
        "Machine-ready clearance is not verified.": "Khoảng an toàn để điều khiển máy chưa được xác minh.",
        "Production Post is not available for Parallel Finishing.": "Post sản xuất chưa khả dụng cho Gia công tinh song song.",
        "parallel.size_limit: Estimated pass count exceeds guardrail.": "parallel.size_limit: Số lượt cắt ước tính vượt giới hạn bảo vệ.",
    }
)


PROGRESS_PHASE_TRANSLATIONS = MappingProxyType(
    {
        "validation": "Kiểm tra hợp lệ",
        "input_resolution": "Đọc dữ liệu đầu vào",
        "frame_bounds": "Xác định giới hạn khung",
        "path_generation": "Tạo đường chạy dao",
        "pass_generation": "Tạo lượt cắt",
        "intersection": "Tính giao tuyến",
        "discretization": "Rời rạc hóa",
        "ordering_linking": "Sắp thứ tự và liên kết",
        "ir_build": "Tạo Toolpath IR",
        "safety_validation": "Kiểm tra an toàn",
        "artifact_publication": "Công bố kết quả",
        "finalization": "Hoàn thiện",
        "complete": "Hoàn tất",
        "cancelled": "Đã hủy",
        "failed": "Thất bại",
        "preparing": "Chuẩn bị",
        "generating": "Đang tạo",
        "generating_sections": "Đang tạo các phần",
        "assembling": "Đang lắp ráp chương trình",
        "validating_output": "Đang kiểm tra đầu ra",
        "current": "Hiện hành",
        "stale": "Đã lỗi thời",
        "missing": "Thiếu",
        "validating": "Đang kiểm tra",
        "resolving": "Đang đọc dữ liệu",
        "sampling": "Đang lấy mẫu",
        "broad_phase": "Kiểm tra va chạm sơ bộ",
        "narrow_phase": "Kiểm tra va chạm chi tiết",
        "building_result": "Đang tạo kết quả",
        "publishing": "Đang công bố",
        "rendering_overlay": "Đang kết xuất lớp phủ",
        "saving": "Đang lưu",
        "exporting": "Đang xuất",
    }
)


_PREFIX_TRANSLATIONS = (
    ("Phase: ", "Giai đoạn: "),
    ("Overall: ", "Tổng thể: "),
    ("Items: ", "Hạng mục: "),
    ("Safety Status: ", "Trạng thái an toàn: "),
    ("Scope: ", "Phạm vi: "),
    ("Machine-ready clearance: ", "Khoảng hở sẵn sàng cho máy: "),
    ("Preview metadata: ", "Siêu dữ liệu xem trước: "),
)


_PHRASE_TRANSLATIONS = (
    ("Operation Manager", "Trình quản lý nguyên công"),
    ("Safe — verified within declared scope", "An toàn — đã xác minh trong phạm vi công bố"),
    ("Not calculated", "Chưa tính toán"),
    ("Candidate", "Ứng viên"),
    ("Cancelled", "Đã hủy"),
    ("Failed", "Thất bại"),
    ("points/curve", "điểm/đường cong"),
    ("points/result", "điểm/kết quả"),
    ("machine-ready clearance not verified", "khoảng hở sẵn sàng cho máy chưa được xác minh"),
    ("NOT CERTIFIED / REVIEW REQUIRED", "CHƯA CHỨNG NHẬN / CẦN RÀ SOÁT"),
    ("STALE / NON-CURRENT", "ĐÃ LỖI THỜI / KHÔNG HIỆN HÀNH"),
    ("No current result", "Không có kết quả hiện hành"),
    ("no fingerprint", "không có dấu vân tay"),
    ("machining face(s)", "bề mặt gia công"),
    ("selected face(s)", "bề mặt đã chọn"),
    ("face(s)", "bề mặt"),
    ("fixed three-axis", "ba trục cố định"),
    ("pass direction", "hướng lượt cắt"),
    ("stepover direction", "hướng bước ngang"),
    ("tool axis", "trục Tool"),
    ("U base", "U cơ sở"),
    ("W=Setup Z", "W=Z thiết lập"),
    ("one-way", "một chiều"),
    ("zigzag", "zíc zắc"),
    ("angle ", "góc "),
    ("stepover ", "bước ngang "),
    ("tol ", "dung sai "),
    ("allowance ", "lượng dư "),
    ("passes", "lượt"),
    ("checked", "đã kiểm tra"),
    ("unverified", "chưa xác minh"),
    ("required", "bắt buộc"),
    ("operations", "nguyên công"),
    ("operation", "nguyên công"),
    ("sections", "phần"),
    ("section", "phần"),
    ("tool changes", "lần đổi Tool"),
    ("lines", "dòng"),
    ("bytes", "byte"),
    ("points", "điểm"),
    ("segments", "đoạn"),
    ("markers", "dấu"),
    ("issues", "vấn đề"),
    ("errors", "lỗi"),
    ("warnings", "cảnh báo"),
    ("total", "tổng"),
    ("validation PASSED", "kiểm tra ĐẠT"),
)


_USER_FACING_TERM_TRANSLATIONS = (
    # Keep safety-stage wording as a single phrase so the generic ``safety``
    # replacement below cannot leave the English suffix visible.
    (
        r"\bSafety\s+contract\s+Stage\s+8A\.3\.2\b",
        "hợp đồng an toàn giai đoạn 8A.3.2",
    ),
    (r"\bsafety\s+contract\b", "hợp đồng an toàn"),
    (r"\bStage\s+8A\.3\.2\b", "giai đoạn 8A.3.2"),
    (r"\bsafety\s+validator\b", "bộ kiểm tra an toàn"),
    (r"\bMachining\s+zone\b", "vùng gia công"),
    (r"\bviewport\b", "vùng hiển thị CAD"),
    (r"\bWCS\b", "hệ tọa độ"),
    (r"\btopology\b", "cấu trúc liên kết hình học"),
    (r"\bdependency\b", "dữ liệu phụ thuộc"),
    (r"\bpanel\b", "bảng"),
    (r"\bpopup\b", "cửa sổ"),
    (r"\bfallback\b", "phương án thay thế"),
    (r"\bclearance\b", "an toàn"),
    (r"\bretract\b", "rút dao"),
    (r"\bManager\b", "Trình quản lý"),
    (r"\bTop/Bottom/Stepdown\b", "Trên/Dưới/Bước xuống"),
    (r"\btrimmed\b", "đã cắt xén"),
    (r"\btrim\b", "cắt xén"),
    (r"\bvalidator\b", "bộ kiểm tra"),
    (r"\bprojection\b", "dữ liệu hiển thị"),
    (r"\btoolpath\b", "đường chạy dao"),
    (r"\bstale\b", "đã lỗi thời"),
    (r"\bBall-end\s+Tool\b", "Tool cầu"),
    (r"\bBall-end\s+tool\b", "Tool cầu"),
    (r"\bBall\s*-\s*D(?=\d)", "Cầu · D"),
    (r"\bBall\s*·\s*D(?=\d)", "Cầu · D"),
    (r"\bBall\b", "Cầu"),
    (r"\bTools\b", "Tool"),
    (r"\bTool\s+Assembly\b", "Cụm Tool"),
    (r"\bPRIMARY\b", "Chính"),
    (r"\boverride\b", "tùy chỉnh thủ công"),
    (r"\bguardrail\b", "giới hạn bảo vệ"),
    (r"\bartifact\b", "kết quả tính toán"),
    (r"\bsafety\b", "an toàn"),
    (r"\bcontour\b", "đường đồng mức"),
    (r"\bmachine-ready\b", "sẵn sàng chạy máy"),
    (r"\bProduction\s+Post\b", "Post sản xuất"),
    (r"\bfail-closed\b", "chặn an toàn"),
    (r"\balgorithm\s+v([123])\b", r"Thuật toán v\1"),
    (r"\bpayload\s+v([123])\b", r"Phiên bản dữ liệu v\1"),
    (r"\bdirect\s+link\b", "liên kết trực tiếp"),
    (r"\bUNKNOWN\b", "CHƯA XÁC ĐỊNH"),
    (r"\bUNSAFE\b", "KHÔNG AN TOÀN"),
    (r"\bSAFE\b", "AN TOÀN"),
)


def translate_progress_phase(value: object) -> str:
    """Return a localized phase label without mutating its enum value."""
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return ui_text(PROGRESS_PHASE_TRANSLATIONS.get(text.casefold(), text))


def translate_status(value: object) -> str:
    """Translate a presentation status while retaining IDs and diagnostics codes."""
    raw = getattr(value, "value", value)
    text = str(raw)
    direct = UI_TRANSLATIONS.get(text)
    if direct is not None:
        return ui_text(direct)
    parts = re.split(r"(\s*[·/]\s*)", text)
    return "".join(
        ui_text(UI_TRANSLATIONS.get(part.strip(), part))
        if index % 2 == 0
        else part
        for index, part in enumerate(parts)
    )


def operation_manager_status_category_display_name(
    value: object,
    *,
    project_context: bool = False,
) -> str:
    """Map an internal Operation Manager namespace at its presentation boundary."""
    raw = getattr(value, "value", value)
    text = str(raw).strip().casefold()
    if text == "domain":
        return ui_text("Project" if project_context else "Status").upper()
    localized = {
        "calculation": "TÍNH TOÁN",
        "simulation": "MÔ PHỎNG",
        "post": "Post",
        "nc": "NC",
        "export": "XUẤT NC",
    }.get(text, str(raw))
    return ui_text(localized)


def _vietnamese_text(value: object) -> str:
    """Translate one known system presentation string into Vietnamese.

    Unknown text is returned byte-for-byte so project names, operation names,
    paths, UUIDs and other user/domain data are never guessed or rewritten.
    """
    text = str(value)
    if not text:
        return text
    direct = UI_TRANSLATIONS.get(text)
    if direct is not None:
        return direct
    if ":" in text:
        prefix, remainder = text.split(":", 1)
        if prefix.startswith(
            ("parallel.", "z_level.", "field.", "simulation.", "post.")
        ):
            return f"{prefix}: {_vietnamese_text(remainder.strip())}"
    match = re.fullmatch(
        r"Unexpected cutter contact outside the declared contact zone on motion (\d+)\.",
        text,
    )
    if match:
        return (
            "Dao cắt tiếp xúc ngoài vùng tiếp xúc đã khai báo tại chuyển động "
            f"{match.group(1)}."
        )
    match = re.fullmatch(
        r"Cutter gouge on pass ([^,]+), segment ([^;]+); penetration ([^ ]+) mm\.",
        text,
    )
    if match:
        return (
            f"Dao cắt lẹm tại lượt {match.group(1)}, đoạn {match.group(2)}; "
            f"độ xuyên {match.group(3)} mm."
        )
    match = re.fullmatch(
        r"(Cutter|Shank|Holder) collision on motion ([^;]+); clearance ([^ ]+) mm\.",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        component = {
            "cutter": "Dao cắt",
            "shank": "Cán dao",
            "holder": "Holder",
        }[match.group(1).casefold()]
        return (
            f"{component} va chạm tại chuyển động {match.group(2)}; "
            f"khoảng hở {match.group(3)} mm."
        )
    match = re.fullmatch(
        r"Retract/clearance plane must exceed protected bounds \(([^)]+) mm\)\.",
        text,
    )
    if match:
        return (
            "Mặt phẳng rút dao/an toàn phải cao hơn giới hạn được bảo vệ "
            f"({match.group(1)} mm)."
        )
    for source, target in _PREFIX_TRANSLATIONS:
        if text.startswith(source):
            return target + _vietnamese_text(text[len(source) :])
    phase_key = text.replace(" ", "_").casefold()
    if phase_key in PROGRESS_PHASE_TRANSLATIONS:
        return PROGRESS_PHASE_TRANSLATIONS[phase_key]
    protected_tokens: list[str] = []

    def protect_internal_token(match: re.Match[str]) -> str:
        protected_tokens.append(match.group(0))
        return f"\ufff0{len(protected_tokens) - 1}\ufff1"

    translated = re.sub(
        r"\b[a-z][a-z0-9_.-]*:[A-Za-z0-9_.-]+\b",
        protect_internal_token,
        text,
    )
    # Diagnostic/strategy IDs are also emitted as dotted tokens without a
    # colon (for example ``parallel.safety.holder_collision``).  Keep those
    # stable while translating surrounding user-facing prose.
    translated = re.sub(
        r"\b(?:[a-z][a-z0-9_-]*\.)+[a-z][a-z0-9_.-]*\b",
        protect_internal_token,
        translated,
    )
    for source, target in _PHRASE_TRANSLATIONS:
        translated = re.sub(re.escape(source), target, translated, flags=re.IGNORECASE)
    for pattern, target in _USER_FACING_TERM_TRANSLATIONS:
        translated = re.sub(pattern, target, translated, flags=re.IGNORECASE)
    for source, target in UI_TRANSLATIONS.items():
        if source.isupper() and len(source) >= 4:
            translated = re.sub(
                rf"(?<![\w.]){re.escape(source)}(?![\w.])",
                target,
                translated,
            )
    for index, token in enumerate(protected_tokens):
        translated = translated.replace(f"\ufff0{index}\ufff1", token)
    return translated


def ui_text(value: object) -> str:
    """Translate managed production text using the active typed locale.

    Exact catalog entries use the central resolver.  The established
    Vietnamese phrase adapter remains the default/fallback presentation
    boundary for dynamic diagnostics, while unknown user/domain data is
    preserved byte-for-byte.
    """
    text = str(value)
    if not text:
        return text
    service = translation_service()
    symbol_match = re.fullmatch(r"([●○■•]\n)(.+)", text, re.DOTALL)
    if symbol_match is not None:
        return symbol_match.group(1) + ui_text(symbol_match.group(2))
    if text.startswith("CAD VIEWER KHÔNG KHẢ DỤNG\n"):
        return (
            service.translate_key("CAD VIEWER UNAVAILABLE")
            + "\n"
            + text.split("\n", 1)[1]
        )
    if text.startswith("CAD: "):
        return "CAD: " + ui_text(text[5:])
    if any(text in catalog.entries for catalog in service.catalogs.values()):
        # Exact semantic source keys outrank reverse-value aliases.  Without
        # this, English "Back" can collide with Vietnamese "Sau" (After).
        return service.translate_key(text)
    canonical = service.canonical_key(text)
    if canonical is not None:
        return service.translate(text)
    if service.language is UiLanguage.VI_VN:
        return _vietnamese_text(text)
    # Dynamic source diagnostics are generally English.  If the Vietnamese
    # adapter produces an exact managed value, resolve that value into the
    # active locale.  Otherwise preserve the source text rather than guessing
    # at user-entered names, paths, IDs, or metadata.
    vietnamese = _vietnamese_text(text)
    vietnamese_key = service.canonical_key(vietnamese)
    if vietnamese_key is not None:
        return service.translate(vietnamese)
    return text


def iter_catalog_entries() -> Iterator[tuple[str, str]]:
    """Yield deterministic catalog entries for audit/report tooling."""
    yield from sorted(UI_TRANSLATIONS.items())


class LocalizedComboBox(QComboBox):
    """Show localized choices while preserving source values and aliases."""

    _SOURCE_TEXT_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def addItem(self, text: str, userData: object = None) -> None:  # noqa: N802
        super().addItem(ui_text(text), userData)
        self.setItemData(self.count() - 1, text, self._SOURCE_TEXT_ROLE)

    def addItems(self, texts: list[str]) -> None:  # noqa: N802
        for item in texts:
            self.addItem(item, item)

    def setCurrentText(self, text: str) -> None:  # noqa: N802
        visible_index = self.findText(text)
        if visible_index >= 0:
            self.setCurrentIndex(visible_index)
            return
        expected = str(text)
        for index in range(self.count()):
            source = self.itemData(index, self._SOURCE_TEXT_ROLE)
            value = self.itemData(index)
            value_token = getattr(value, "value", value)
            if expected in {str(source), str(value_token), str(value_token).upper()}:
                self.setCurrentIndex(index)
                return
        super().setCurrentText(text)


def localize_widget_tree(root: object) -> None:
    """Translate or retranslate known texts on a built Qt widget subtree.

    Imports are local so the pure catalog remains usable by static audit tools.
    Canonical source keys are retained on objects/items so VI → EN → KO → VI
    does not accumulate translations or alter user/domain values.
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QAbstractButton,
        QAbstractItemView,
        QComboBox,
        QGroupBox,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QStatusBar,
        QTableWidget,
        QTabWidget,
        QTabBar,
        QTreeWidget,
        QWidget,
    )

    service = translation_service()
    source_role = _LOCALIZATION_SOURCE_ROLE

    def translated_source(text: object) -> tuple[str, str]:
        current = str(text)
        canonical = service.canonical_key(current)
        if canonical is not None:
            return canonical, service.translate(canonical)
        localized = ui_text(current)
        canonical = service.canonical_key(localized)
        if canonical is not None:
            return canonical, service.translate(canonical)
        return current, localized

    def is_rendering_of_source(source: object, current: object) -> bool:
        source_text = str(source)
        current_text = str(current)
        if source_text == current_text:
            return True
        return any(
            catalog.entries.get(source_text) == current_text
            for catalog in service.catalogs.values()
        )

    def ensure_accessibility(item: object) -> None:
        """Give visible interactive controls a translated accessible label."""
        if not isinstance(item, QWidget):
            return
        if not isinstance(
            item,
            (QAbstractButton, QComboBox, QLineEdit, QAbstractItemView),
        ):
            return
        accessible_name = item.accessibleName().strip()
        if not accessible_name:
            text_getter = (
                item.currentText
                if isinstance(item, QComboBox)
                else getattr(item, "text", None)
            )
            text = str(text_getter() if callable(text_getter) else "").strip()
            accessible_name = (
                item.toolTip().strip()
                or item.statusTip().strip()
                or text
                or (ui_text("View") if isinstance(item, QAbstractItemView) else "")
            )
            if accessible_name:
                item.setAccessibleName(accessible_name)
        if not item.accessibleDescription().strip():
            description = item.toolTip().strip() or accessible_name
            if description:
                item.setAccessibleDescription(description)

    def retranslate_property(
        item: object,
        getter_name: str,
        setter_name: str,
        property_suffix: str,
    ) -> None:
        getter = getattr(item, getter_name, None)
        setter = getattr(item, setter_name, None)
        property_getter = getattr(item, "property", None)
        property_setter = getattr(item, "setProperty", None)
        if not (
            callable(getter)
            and callable(setter)
            and callable(property_getter)
            and callable(property_setter)
        ):
            return
        property_name = f"_hms_i18n_source_{property_suffix}"
        source = property_getter(property_name)
        current = getter()
        if source is None or not is_rendering_of_source(source, current):
            if not current:
                return
            source, _translated = translated_source(current)
            property_setter(property_name, source)
        setter(service.translate(source) if service.canonical_key(source) else ui_text(source))

    def retranslate_item(item: object, column: int) -> None:
        data = getattr(item, "data", None)
        set_data = getattr(item, "setData", None)
        text_getter = getattr(item, "text", None)
        text_setter = getattr(item, "setText", None)
        if not (
            callable(data)
            and callable(set_data)
            and callable(text_getter)
            and callable(text_setter)
        ):
            return
        source = data(column, source_role)
        current = text_getter(column)
        if source is None or not is_rendering_of_source(source, current):
            source, _translated = translated_source(current)
            set_data(column, source_role, source)
        text_setter(
            column,
            service.translate(source)
            if service.canonical_key(source)
            else ui_text(source),
        )

    objects = [root]
    if isinstance(root, QWidget):
        objects.extend(root.findChildren(QWidget))
        objects.extend(root.findChildren(QAction))
    for item in objects:
        if isinstance(item, QLabel):
            retranslate_property(item, "text", "setText", "text")
        elif isinstance(item, QAbstractButton):
            retranslate_property(item, "text", "setText", "text")
        if isinstance(item, QGroupBox):
            retranslate_property(item, "title", "setTitle", "title")
        if isinstance(item, QMenu):
            retranslate_property(item, "title", "setTitle", "title")
        if isinstance(item, QTabWidget):
            for index in range(item.count()):
                page = item.widget(index)
                property_name = "_hms_i18n_source_tab_text"
                source = page.property(property_name) if page is not None else None
                current = item.tabText(index)
                if source is None or not is_rendering_of_source(source, current):
                    source, _translated = translated_source(current)
                    if page is not None:
                        page.setProperty(property_name, source)
                item.setTabText(
                    index,
                    service.translate(source)
                    if service.canonical_key(source)
                    else ui_text(source),
                )
        if isinstance(item, QTabBar):
            if (
                isinstance(item.parentWidget(), QMainWindow)
                and any(
                    isinstance(item.tabData(index), int)
                    for index in range(item.count())
                )
            ):
                # Native QMainWindow dock tabs store internal dock pointers in
                # tabData. Overwriting them with translation source strings
                # corrupts Qt's grouping and creates duplicate placeholder
                # bars. MainWindow owns their compact localized presentation.
                continue
            for index in range(item.count()):
                source = item.tabData(index)
                current = item.tabText(index)
                if source is None or not is_rendering_of_source(source, current):
                    source, _translated = translated_source(current)
                    item.setTabData(index, source)
                item.setTabText(
                    index,
                    service.translate(source)
                    if service.canonical_key(source)
                    else ui_text(source),
                )
        if isinstance(item, QWidget) and item.windowTitle():
            retranslate_property(
                item,
                "windowTitle",
                "setWindowTitle",
                "window_title",
            )
        if isinstance(item, QLineEdit) and item.placeholderText():
            retranslate_property(
                item,
                "placeholderText",
                "setPlaceholderText",
                "placeholder",
            )
        if isinstance(item, QComboBox):
            for index in range(item.count()):
                source = item.itemData(index, LocalizedComboBox._SOURCE_TEXT_ROLE)
                current = item.itemText(index)
                if source is None or not is_rendering_of_source(source, current):
                    source, _translated = translated_source(current)
                    item.setItemData(
                        index,
                        source,
                        LocalizedComboBox._SOURCE_TEXT_ROLE,
                    )
                item.setItemText(
                    index,
                    service.translate(source)
                    if service.canonical_key(source)
                    else ui_text(source),
                )
        if isinstance(item, QTreeWidget):
            blocker = QSignalBlocker(item)
            try:
                header = item.headerItem()
                if header is not None:
                    for column in range(header.columnCount()):
                        retranslate_item(header, column)
                pending = [
                    item.topLevelItem(index) for index in range(item.topLevelItemCount())
                ]
                while pending:
                    tree_item = pending.pop()
                    if tree_item is None:
                        continue
                    try:
                        for column in range(tree_item.columnCount()):
                            retranslate_item(tree_item, column)
                        pending.extend(
                            tree_item.child(index)
                            for index in range(tree_item.childCount())
                        )
                    except RuntimeError:
                        # A refresh can invalidate a transient item before Qt
                        # has dispatched its queued signal; it is no longer visible.
                        continue
            finally:
                del blocker
        if isinstance(item, QTableWidget):
            blocker = QSignalBlocker(item)
            try:
                for column in range(item.columnCount()):
                    header = item.horizontalHeaderItem(column)
                    if header is not None:
                        source = header.data(source_role)
                        current = header.text()
                        if source is None or not is_rendering_of_source(source, current):
                            source, _translated = translated_source(current)
                            header.setData(source_role, source)
                        header.setText(
                            service.translate(source)
                            if service.canonical_key(source)
                            else ui_text(source)
                        )
                for row in range(item.rowCount()):
                    for column in range(item.columnCount()):
                        cell = item.item(row, column)
                        if cell is not None:
                            source = cell.data(source_role)
                            current = cell.text()
                            if source is None or not is_rendering_of_source(source, current):
                                source, _translated = translated_source(current)
                                cell.setData(source_role, source)
                            cell.setText(
                                service.translate(source)
                                if service.canonical_key(source)
                                else ui_text(source)
                            )
            finally:
                del blocker
        if isinstance(item, QAction):
            retranslate_property(item, "text", "setText", "text")
        if isinstance(item, QStatusBar) and item.currentMessage():
            retranslate_property(
                item,
                "currentMessage",
                "showMessage",
                "status_message",
            )
        for getter_name, setter_name in (
            ("toolTip", "setToolTip"),
            ("statusTip", "setStatusTip"),
            ("accessibleName", "setAccessibleName"),
            ("accessibleDescription", "setAccessibleDescription"),
        ):
            retranslate_property(
                item,
                getter_name,
                setter_name,
                getter_name,
            )
        ensure_accessibility(item)


__all__ = [
    "DISPLAY_VALUE_MAPPINGS",
    "OPERATION_DISPLAY_NAMES",
    "PROGRESS_PHASE_TRANSLATIONS",
    "TECHNICAL_TERMS",
    "UI_TRANSLATIONS",
    "LocalizedComboBox",
    "display_value",
    "display_value_list",
    "iter_catalog_entries",
    "localize_widget_tree",
    "operation_display_name",
    "operation_manager_status_category_display_name",
    "operation_type_display_name",
    "setup_display_name",
    "translate_progress_phase",
    "translate_status",
    "ui_text",
]
