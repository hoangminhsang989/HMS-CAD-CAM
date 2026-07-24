"""Safe presentation-only Contour reference schema for Stage 9A.4."""

from __future__ import annotations

from hms_cadcam.ui.function_editor.model import (
    ApplicabilityOperator,
    FunctionEditorAction,
    FunctionEditorApplicability,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorFooter,
    FunctionEditorSection,
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
    FunctionEditorValidationKind,
    FunctionEditorValidationRule,
    FunctionEditorValueSource,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema


def _positive(code: str, label: str) -> FunctionEditorValidationRule:
    return FunctionEditorValidationRule(
        kind=FunctionEditorValidationKind.MINIMUM,
        operand=0.001,
        code=code,
        message=f"{label} phải lớn hơn 0.",
    )


def build_contour_reference_schema() -> FunctionEditorSchema:
    """Build a non-production demo that exercises every framework component."""
    basic = FunctionEditorSection(
        section_id="basic",
        title="Basic",
        summary="Tên, phía dao và ý định cắt cốt lõi",
        order=0,
        fields=(
            FunctionEditorField(
                field_id="operation_name",
                label="Tên operation",
                value="Contour 2D — Demo",
                default="Contour 2D — Demo",
                default_label="HMS reference",
                required=True,
                tooltip="Tên presentation của operation; demo không ghi project.",
                help_text="Tên giúp nhận biết operation trong Operation Manager.",
                order=0,
            ),
            FunctionEditorField(
                field_id="cut_side",
                label="Phía dao",
                kind=FunctionEditorFieldKind.CHOICE,
                value="outside",
                default="outside",
                choices=("outside", "inside", "on"),
                required=True,
                tooltip="Chọn quan hệ của tâm dao với profile.",
                help_text="Phía dao chỉ là dữ liệu tham chiếu trong Giai đoạn 9A.4.",
                order=1,
            ),
        ),
        help_text="Basic giữ các quyết định người vận hành thường xuyên dùng.",
    )
    geometry = FunctionEditorSection(
        section_id="geometry",
        title="Geometry",
        summary="1 Chain · planar_face_outer · RESOLVED",
        order=1,
        fields=(
            FunctionEditorField(
                field_id="geometry_summary",
                label="Profile",
                kind=FunctionEditorFieldKind.READ_ONLY,
                value="1 Chain · Outer profile · RESOLVED",
                source=FunctionEditorValueSource.GEOMETRY,
                required=True,
                tooltip="Summary hình học; selection chi tiết thực hiện trong viewport.",
                help_text="Geometry reference được hiển thị bằng summary, không lộ raw key.",
            ),
        ),
        help_text="Geometry chứa selection summary và preview/focus hook.",
    )
    tool = FunctionEditorSection(
        section_id="tool",
        title="Tool",
        summary="T3 · Dao phay ngón đầu phẳng Ø10",
        order=2,
        fields=(
            FunctionEditorField(
                field_id="tool_summary",
                label="Tool Assembly",
                kind=FunctionEditorFieldKind.READ_ONLY,
                value="T3 · Dao phay ngón đầu phẳng Ø10 · Holder H1",
                source=FunctionEditorValueSource.TOOL,
                required=True,
                tooltip="Tool Assembly đến từ Tool Library của project.",
                help_text="Operation tham chiếu Tool Assembly, không nhân bản hình học dao.",
            ),
        ),
        help_text="Tool hiển thị dao/Holder và nguồn; sửa thư viện ở bảng riêng.",
    )
    cutting = FunctionEditorSection(
        section_id="cutting",
        title="Cutting",
        summary="Feed 500 mm/min · 4500 RPM",
        order=3,
        fields=(
            FunctionEditorField(
                field_id="feed_rate",
                label="Feed",
                kind=FunctionEditorFieldKind.NUMBER,
                value="500",
                default="500",
                default_label="Tool profile",
                unit="mm/min",
                source=FunctionEditorValueSource.TOOL,
                required=True,
                validators=(_positive("contour.feed_positive", "Feed"),),
                tooltip="Tốc độ tiến dao cắt; nguồn khuyến nghị từ Tool.",
                order=0,
            ),
            FunctionEditorField(
                field_id="spindle_rpm",
                label="Spindle",
                kind=FunctionEditorFieldKind.NUMBER,
                value="4500",
                default="4500",
                default_label="Tool profile",
                unit="RPM",
                source=FunctionEditorValueSource.TOOL,
                required=True,
                validators=(_positive("contour.spindle_positive", "Spindle"),),
                tooltip="Tốc độ trục chính; kiểm tra capability máy khi production binding.",
                order=1,
            ),
            FunctionEditorField(
                field_id="stepdown",
                label="Stepdown",
                kind=FunctionEditorFieldKind.NUMBER,
                value="2.0",
                default="2.0",
                default_label="HMS reference v1",
                unit="mm",
                source=FunctionEditorValueSource.DEFAULT,
                required=True,
                validators=(_positive("contour.stepdown_positive", "Stepdown"),),
                tooltip="Chiều sâu mỗi lớp cắt.",
                order=2,
            ),
        ),
        help_text="Cutting gom feed, spindle và lượng cắt theo workflow.",
    )
    levels = FunctionEditorSection(
        section_id="levels",
        title="Levels",
        summary="Top 0 · Depth -12",
        order=4,
        fields=(
            FunctionEditorField(
                field_id="top_z",
                label="Top",
                kind=FunctionEditorFieldKind.NUMBER,
                value="0.0",
                default="0.0",
                default_label="Stock top",
                unit="mm",
                source=FunctionEditorValueSource.STOCK,
                required=True,
                tooltip="Mặt bắt đầu, có nguồn từ phôi.",
                order=0,
            ),
            FunctionEditorField(
                field_id="final_depth",
                label="Final depth",
                kind=FunctionEditorFieldKind.NUMBER,
                value="-12.0",
                default="-12.0",
                default_label="Geometry reference",
                unit="mm",
                source=FunctionEditorValueSource.GEOMETRY,
                required=True,
                validators=(
                    FunctionEditorValidationRule(
                        kind=FunctionEditorValidationKind.LESS_THAN_FIELD,
                        operand="top_z",
                        code="contour.depth_order",
                        message="Final depth phải thấp hơn Top.",
                    ),
                ),
                tooltip="Chiều sâu cuối phải thấp hơn Top.",
                order=1,
            ),
        ),
        help_text="Levels dùng semantic Top/Depth và nguồn Stock/Geometry.",
    )
    linking = FunctionEditorSection(
        section_id="linking",
        title="Linking",
        summary="Safe Z 20 · Linear lead",
        order=5,
        fields=(
            FunctionEditorField(
                field_id="safe_z",
                label="Safe Z",
                kind=FunctionEditorFieldKind.READ_ONLY,
                value="20.0",
                unit="mm",
                source=FunctionEditorValueSource.SETUP,
                tooltip="Giá trị kế thừa thiết lập; bản minh họa không cho ghi đè.",
                order=0,
            ),
            FunctionEditorField(
                field_id="use_lead",
                label="Dùng lead-in/out",
                kind=FunctionEditorFieldKind.CHECKBOX,
                value=True,
                default=True,
                default_label="Recommended",
                tooltip="Bật để hiện tham số lead có áp dụng.",
                order=1,
            ),
            FunctionEditorField(
                field_id="lead_length",
                label="Chiều dài lead",
                kind=FunctionEditorFieldKind.NUMBER,
                value="1.0",
                default="1.0",
                default_label="HMS reference v1",
                unit="mm",
                source=FunctionEditorValueSource.DEFAULT,
                applicable_when=FunctionEditorApplicability(
                    "use_lead", ApplicabilityOperator.TRUTHY
                ),
                validators=(_positive("contour.lead_positive", "Chiều dài lead"),),
                tooltip="Ẩn hoàn toàn khi lead-in/out không áp dụng.",
                order=2,
            ),
        ),
        help_text="Linking hiển thị safe motion kế thừa và field phụ thuộc mode.",
    )
    advanced = FunctionEditorSection(
        section_id="advanced",
        title="Advanced",
        summary="3 thiết lập tùy chọn",
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        default_expanded=False,
        order=6,
        fields=(
            FunctionEditorField(
                field_id="finishing_pass",
                label="Finishing pass",
                kind=FunctionEditorFieldKind.CHECKBOX,
                value=False,
                default=False,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                tooltip="Tùy chọn tham chiếu; không nối bộ tạo CAM trong 9A.4.",
                order=0,
            ),
            FunctionEditorField(
                field_id="radial_allowance",
                label="Radial allowance",
                kind=FunctionEditorFieldKind.NUMBER,
                value="0.0",
                default="0.0",
                unit="mm",
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                validators=(
                    FunctionEditorValidationRule(
                        kind=FunctionEditorValidationKind.MINIMUM,
                        operand=0.0,
                        code="contour.allowance_nonnegative",
                        message="Radial allowance không được âm.",
                    ),
                ),
                order=1,
            ),
            FunctionEditorField(
                field_id="plunge_feed",
                label="Plunge feed",
                kind=FunctionEditorFieldKind.NUMBER,
                value="100",
                default="100",
                unit="mm/min",
                source=FunctionEditorValueSource.TOOL,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                validators=(_positive("contour.plunge_positive", "Plunge feed"),),
                order=2,
            ),
        ),
        help_text="Advanced chứa override ít dùng và collapsed mặc định.",
    )
    expert = FunctionEditorSection(
        section_id="expert",
        title="Expert",
        summary="Thiết lập độ chính xác · thay đổi có thể tăng thời gian tính",
        disclosure_level=ParameterDisclosureLevel.EXPERT,
        default_expanded=False,
        order=7,
        fields=(
            FunctionEditorField(
                field_id="tolerance",
                label="Tolerance",
                kind=FunctionEditorFieldKind.NUMBER,
                value="0.01",
                default="0.01",
                default_label="HMS reference v1",
                unit="mm",
                source=FunctionEditorValueSource.DEFAULT,
                disclosure_level=ParameterDisclosureLevel.EXPERT,
                validators=(_positive("contour.tolerance_positive", "Tolerance"),),
                tooltip="Trường độ chính xác chỉ tham chiếu; có đánh đổi chất lượng/thời gian.",
                help_text="Tolerance nhỏ hơn thường tăng số điểm và thời gian tính.",
                order=0,
            ),
            FunctionEditorField(
                field_id="arc_filtering",
                label="Arc filtering",
                kind=FunctionEditorFieldKind.CHECKBOX,
                value=False,
                default=False,
                disclosure_level=ParameterDisclosureLevel.EXPERT,
                tooltip="Chỉ tham chiếu; không nối Post hoặc Toolpath IR trong 9A.4.",
                order=1,
            ),
        ),
        help_text=(
            "Chuyên sâu là bản mẫu chỉ dành cho trình bày. Thay đổi độ chính xác "
            "có thể ảnh hưởng chất lượng và thời gian; Giai đoạn 9A.4 không gửi "
            "vào bộ xử lý."
        ),
    )
    return FunctionEditorSchema(
        editor_id="contour_reference_9a4",
        strategy=FunctionEditorStrategyKey("contour_reference_9a4"),
        summary=FunctionEditorSummary(
            title="Contour 2D",
            strategy="Contour · UI reference only",
            tool="T3 Ø10",
            geometry="1 Chain · Depth -12",
            operation_status="REFERENCE",
            reference_only=True,
        ),
        sections=(basic, geometry, tool, cutting, levels, linking, advanced, expert),
        footer=FunctionEditorFooter(
            actions=(
                FunctionEditorAction.RESET_DRAFT,
                FunctionEditorAction.PREVIEW,
                FunctionEditorAction.VALIDATE,
                FunctionEditorAction.APPLY,
                FunctionEditorAction.CLOSE,
            ),
            preview_supported=True,
            calculate_supported=False,
            apply_supported=True,
        ),
    )
