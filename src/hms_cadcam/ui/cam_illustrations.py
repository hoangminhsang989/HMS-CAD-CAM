"""Lightweight, high-DPI CAM function illustrations owned by the HMS UI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.function_editor.model import PresentationValue
from hms_cadcam.ui.ui_tokens import (
    CAM_POPUP_DENSITY,
    CAMPopupMetrics,
)


_MOTION_LEGEND_TEXT = (
    "Nét xanh liền: đường cắt; nét cam đứt: chạy nhanh hoặc liên kết "
    "ngoài cắt; mũi tên xanh: hướng cắt; mũi tên cam: rút dao hoặc tiếp cận."
)


@dataclass(frozen=True, slots=True)
class CAMIllustrationDescriptor:
    """Static, localized illustration metadata for one production editor."""

    key: str
    title: str
    caption: str
    accessible_description: str
    visual_kind: str
    operation_type: str = ""
    preferred_aspect_ratio: tuple[int, int] = (16, 9)
    compact_size: tuple[int, int] = (160, 90)
    expanded_size: tuple[int, int] = (640, 360)
    render_source: str = "hms_qpainter_vector"
    semantic_features: frozenset[str] = frozenset(
        {"tool", "workpiece", "motion_arrow", "machining_region"}
    )
    semantic_flags: tuple[tuple[str, bool], ...] = ()

    def __post_init__(self) -> None:
        if not self.operation_type:
            object.__setattr__(self, "operation_type", self.visual_kind)
        width, height = self.preferred_aspect_ratio
        if width <= 0 or height <= 0:
            raise ValueError("Tỷ lệ minh họa CAM phải dương")


@dataclass(frozen=True, slots=True)
class CAMIllustrationState:
    """Presentation-only state; it never enters a project or calculation request."""

    descriptor: CAMIllustrationDescriptor
    cut_direction: str = "one_way"
    direction_degrees: float = 0.0
    linking: str = "retract"
    quality: str = "balanced"
    manual: bool = False
    semantic_focus: str = "ordering"

    @property
    def ordering_caption(self) -> str:
        """Describe cut ordering independently from the link policy."""
        if self.cut_direction == "zigzag":
            return "Các lượt chạy đổi chiều liên tục để giảm quãng đường không cắt."
        return (
            "Mọi lượt chạy cùng một hướng; Tool quay về đầu lượt trước khi "
            "tiếp tục."
        )

    @property
    def linking_caption(self) -> str:
        """Describe the selected non-cut connection without conflating ordering."""
        if self.linking == "direct":
            return (
                "Tool nối trực tiếp sang lượt kế tiếp sau khi đoạn nối đạt kiểm "
                "tra an toàn."
            )
        return "Tool rút lên, chạy nhanh sang vị trí mới rồi tiếp cận xuống."

    @property
    def caption(self) -> str:
        if self.descriptor.visual_kind == "z_level":
            captions = {
                "inner_hole": "Đường đồng mức theo cao độ giữ nguyên lỗ trong và không cắt xuyên qua lỗ.",
                "disconnected_regions": "Hai vùng rời được tạo đường đồng mức riêng và liên kết bằng chuyển động bảo thủ.",
                "linking": "Liên kết trực tiếp chỉ được dùng khi an toàn đã xác minh; nếu không, Tool rút lên mặt phẳng an toàn.",
                "safety_unknown": "Thiếu Holder hoặc hình học bảo vệ: trạng thái là CHƯA XÁC ĐỊNH, không phải AN TOÀN.",
                "collision": "Vùng va chạm được đánh dấu KHÔNG AN TOÀN và không tạo kết quả SẴN SÀNG.",
                "allowance": "Đường tâm Tool được dịch theo pháp tuyến để giữ lượng dư đã khai báo.",
                "level_range": "Các lớp Z được lập từ cao độ trên xuống cao độ dưới theo bước xuống hiệu lực.",
                "quality": {
                    "fast": (
                        "Hồ sơ Nhanh dùng ít lớp hơn nhưng không làm suy giảm "
                        "hợp đồng an toàn."
                    ),
                    "balanced": "Hồ sơ Cân bằng dùng mật độ lớp Z mặc định.",
                    "high": "Hồ sơ Chất lượng cao dùng nhiều lớp Z và rời rạc dày hơn.",
                }.get(self.quality, "Hồ sơ Cân bằng dùng mật độ lớp Z mặc định."),
            }
            return captions.get(
                self.semantic_focus,
                "Tool cầu gia công các đường đồng mức từ cao độ trên xuống cao độ dưới.",
            )
        if self.descriptor.visual_kind != "parallel":
            return self.descriptor.caption
        if self.semantic_focus == "linking":
            return self.linking_caption
        if self.semantic_focus == "quality":
            quality = {
                "fast": "Nhanh",
                "balanced": "Cân bằng",
                "high": "Chất lượng cao",
            }.get(self.quality, "Cân bằng")
            mode = "Tùy chỉnh" if self.manual else "Tự động"
            return f"Mật độ đường chạy: {quality} · {mode}."
        return self.ordering_caption

    @property
    def accessible_description(self) -> str:
        if self.descriptor.visual_kind == "z_level":
            return f"{self.descriptor.accessible_description} {self.caption}"
        if self.descriptor.visual_kind != "parallel":
            return self.descriptor.accessible_description
        return (
            f"{self.descriptor.accessible_description} "
            f"Hướng chạy dao {self.direction_degrees:g} độ. {self.caption}"
        )

    @property
    def render_state_ids(self) -> tuple[str, ...]:
        """Return deterministic visual IDs without invoking CAM calculation."""
        if self.descriptor.visual_kind == "z_level":
            focus = (
                "overview"
                if self.semantic_focus == "ordering"
                else self.semantic_focus
            )
            return (
                "z_level",
                f"focus_{focus}",
                f"quality_{self.quality}",
                "direct_link" if self.linking == "direct" else "retract_link",
            )
        if self.descriptor.visual_kind != "parallel":
            return (self.descriptor.visual_kind,)
        return (
            f"focus_{self.semantic_focus}",
            (
                self.cut_direction
                if self.semantic_focus != "linking"
                else (
                    "direct_link"
                    if self.linking == "direct"
                    else "retract_link"
                )
            ),
            f"quality_{self.quality}",
        )

    @property
    def semantic_metadata(self) -> tuple[str, ...]:
        """Expose distinct, testable visual meaning for each Parallel state."""
        if self.descriptor.visual_kind == "z_level":
            focus = (
                "overview"
                if self.semantic_focus == "ordering"
                else self.semantic_focus
            )
            metadata = {
                "overview": (
                    "multiple_constant_z_contours",
                    "ball_end_tool",
                    "top_to_bottom_direction",
                ),
                "quality": (
                    f"level_density_{self.quality}",
                    "quality_changes_stepdown",
                    "safety_contract_unchanged",
                ),
                "inner_hole": (
                    "inner_loop_preserved",
                    "no_hole_crossing",
                ),
                "disconnected_regions": (
                    "two_disconnected_regions",
                    "conservative_region_link",
                ),
                "linking": (
                    "direct_link_safe"
                    if self.linking == "direct"
                    else "fallback_retract_rapid_approach",
                ),
                "safety_unknown": (
                    "missing_holder_or_protected_geometry",
                    "unknown_not_safe",
                ),
                "collision": (
                    "collision_zone",
                    "unsafe_not_machine_ready",
                ),
                "allowance": (
                    "nominal_surface",
                    "tool_center_offset",
                    "surface_allowance",
                ),
                "level_range": (
                    "top_level",
                    "bottom_level",
                    "stepdown",
                    "estimated_level_count",
                ),
            }
            return metadata.get(focus, metadata["overview"])
        common = (f"quality_density_{self.quality}",)
        if self.semantic_focus == "linking" and self.linking == "direct":
            return (
                "direct_link_segment",
                "no_z_lift",
                "tool_near_connected_passes",
                *common,
            )
        if self.semantic_focus == "linking":
            return (
                "retract_vertical_up",
                "rapid_horizontal_dashed",
                "approach_vertical_down",
                *common,
            )
        if self.cut_direction == "zigzag":
            return (
                "alternating_cut_arrows",
                "continuous_pass_order",
                *common,
            )
        return (
            "same_direction_cut_arrows",
            "simple_reposition_to_pass_start",
            *common,
        )

    @property
    def render_fingerprint(self) -> str:
        """Return a deterministic renderer fingerprint for regression tests."""
        return "|".join((*self.render_state_ids, *self.semantic_metadata))


class CAMIllustrationRegistry:
    """Resolve every registered production CAM editor to one HMS illustration."""

    def __init__(self) -> None:
        descriptors = (
            CAMIllustrationDescriptor(
                "facing_production_9a5_1",
                "Phay mặt 2.5D",
                "Dao quét các lượt song song để làm phẳng mặt phôi.",
                "Minh họa Tool quét phẳng toàn bộ bề mặt phôi theo các lượt song song.",
                "facing",
            ),
            CAMIllustrationDescriptor(
                "planar_face_facing_production_9a5_1",
                "Phay các mặt phẳng",
                "Chỉ các bề mặt phẳng đã chọn được gia công.",
                "Minh họa Tool quét trên nhiều bề mặt phẳng đã chọn.",
                "planar_faces",
            ),
            CAMIllustrationDescriptor(
                "contour_production_9a5_2",
                "Phay biên dạng 2D",
                "Tool chạy theo biên dạng đã chọn.",
                "Minh họa Tool bám theo đường biên khép kín của chi tiết.",
                "contour",
            ),
            CAMIllustrationDescriptor(
                "pocket_production_9a5_3",
                "Phay hốc 2.5D",
                "Tool bóc vật liệu bên trong vùng hốc.",
                "Minh họa Tool chạy các vòng thu dần bên trong hốc kín.",
                "pocket",
            ),
            CAMIllustrationDescriptor(
                "drilling_production_9a6",
                "Khoan",
                "Tool tiến theo trục của lỗ.",
                "Minh họa mũi khoan quay và tiến xuống theo trục lỗ.",
                "drilling",
            ),
            CAMIllustrationDescriptor(
                "tapping_production_9a6",
                "Taro",
                "Chuyển động quay đồng bộ với bước tiến ren.",
                "Minh họa Tool ta rô quay và tiến xuống theo bước ren.",
                "tapping",
            ),
            CAMIllustrationDescriptor(
                "reaming_production_9a6",
                "Doa lỗ",
                "Tinh chỉnh kích thước và bề mặt của lỗ đã có.",
                "Minh họa dao doa tiến dọc trục trong một lỗ đã khoan.",
                "reaming",
            ),
            CAMIllustrationDescriptor(
                "boring_production_9a6",
                "Khoét lỗ",
                "Tool quay quanh tâm lỗ và tiến xuống theo trục Z.",
                "Minh họa dao tiện lỗ lệch tâm, chuyển động quay quanh tâm lỗ và tiến xuống theo trục Z; không có mũi tên ngang hai chiều.",
                "boring",
                preferred_aspect_ratio=(4, 3),
                compact_size=(132, 99),
                expanded_size=(560, 420),
                semantic_flags=(
                    ("axial_down_arrow", True),
                    ("rotation_about_hole_axis", True),
                    ("horizontal_bidirectional_arrow", False),
                    ("centered_cutaway", True),
                    ("outer_workpiece_intact", True),
                ),
            ),
            CAMIllustrationDescriptor(
                "parallel_finishing_production_8a2_3",
                "Gia công tinh song song",
                "Các lượt chạy song song bám theo bề mặt 3D.",
                "Minh họa Tool cầu chạy các đường song song trên bề mặt ba chiều.",
                "parallel",
            ),
            CAMIllustrationDescriptor(
                "z_level_finishing_production_8a3_3",
                "Gia công tinh theo cao độ Z",
                "Tool cầu gia công các đường đồng mức từ cao độ trên xuống cao độ dưới.",
                "Minh họa Tool cầu, trục W/Z, các đường đồng mức nhiều cao độ, bước xuống và đường tâm Tool.",
                "z_level",
                semantic_features=frozenset(
                    {
                        "tool",
                        "motion_arrow",
                        "machining_region",
                        "ball_end_tool",
                        "workpiece",
                        "multiple_constant_z_contours",
                        "top_to_bottom_arrow",
                        "stepdown",
                        "tool_center_offset",
                    }
                ),
            ),
        )
        self._descriptors = {item.key: item for item in descriptors}

    @property
    def descriptors(self) -> tuple[CAMIllustrationDescriptor, ...]:
        """Return all production descriptors in deterministic registration order."""
        return tuple(self._descriptors.values())

    def resolve(self, editor_id: str) -> CAMIllustrationDescriptor:
        """Resolve a production editor or fail closed on missing artwork."""
        try:
            return self._descriptors[editor_id]
        except KeyError as error:
            raise KeyError(f"Chưa đăng ký minh họa CAM cho {editor_id}") from error


def illustration_state(
    descriptor: CAMIllustrationDescriptor,
    values: Mapping[str, PresentationValue] | None = None,
    *,
    semantic_focus: str | None = None,
) -> CAMIllustrationState:
    """Derive a cheap visual state from presentation primitives only."""
    data = values or {}
    ordering = str(data.get("cut_direction", "one_way")).casefold()
    cut_direction = "zigzag" if "zig" in ordering else "one_way"
    linking_value = str(data.get("linking_mode", "retract")).casefold()
    linking = (
        "direct"
        if linking_value in {"direct", "stay_down", "liên kết trực tiếp"}
        else "retract"
    )
    quality_value = str(data.get("quality_profile", "balanced")).casefold()
    if "direct" in linking_value:
        linking = "direct"
    quality = (
        "fast"
        if quality_value in {"fast", "quick", "nhanh"}
        else "high"
        if quality_value in {"high", "high_quality", "chất lượng cao"}
        else "balanced"
    )
    try:
        direction = float(data.get("effective_direction_angle_degrees", 0.0))
    except (TypeError, ValueError):
        direction = 0.0
    manual = any(
        bool(data.get(key, False))
        for key in (
            "direction_override_enabled",
            "stepover_override_enabled",
            "tolerance_override_enabled",
            "allowance_override_enabled",
            "ordering_override_enabled",
            "top_override_enabled",
            "bottom_override_enabled",
            "stepdown_override_enabled",
            "orientation_override_enabled",
            "boundary_override_enabled",
            "linking_override_enabled",
        )
    )
    focus = str(
        semantic_focus
        if semantic_focus is not None
        else data.get("illustration_focus", "ordering")
    ).casefold()
    if focus not in {
        "ordering",
        "linking",
        "quality",
        "inner_hole",
        "disconnected_regions",
        "safety_unknown",
        "collision",
        "allowance",
        "level_range",
    }:
        focus = "ordering"
    return CAMIllustrationState(
        descriptor,
        cut_direction=cut_direction,
        direction_degrees=direction,
        linking=linking,
        quality=quality,
        manual=manual,
        semantic_focus=focus,
    )


def fit_inside_rect(
    viewport: QRectF,
    aspect_ratio: tuple[int, int],
    *,
    padding: float = 6.0,
) -> QRectF:
    """Center the largest uncropped rectangle with ``aspect_ratio``."""
    inner = viewport.adjusted(padding, padding, -padding, -padding)
    if inner.width() <= 0.0 or inner.height() <= 0.0:
        return QRectF(viewport.center(), viewport.center())
    ratio = aspect_ratio[0] / aspect_ratio[1]
    if inner.width() / inner.height() > ratio:
        height = inner.height()
        width = height * ratio
    else:
        width = inner.width()
        height = width / ratio
    return QRectF(
        inner.center().x() - width / 2.0,
        inner.center().y() - height / 2.0,
        width,
        height,
    )


class IllustrationViewport(QWidget):
    """Device-independent QPainter scene with no bitmap or CAD-kernel dependency."""

    def __init__(
        self, state: CAMIllustrationState, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._state = state
        self.setObjectName("CAMIllustrationCanvas")
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(f"Minh họa {state.descriptor.title}")
        self.setAccessibleDescription(state.accessible_description)

    @property
    def state(self) -> CAMIllustrationState:
        return self._state

    def set_state(self, state: CAMIllustrationState) -> None:
        """Replace presentation state without running OCP or CAM calculation."""
        self._state = state
        self.setAccessibleName(f"Minh họa {state.descriptor.title}")
        self.setAccessibleDescription(state.accessible_description)
        self.update()

    @property
    def render_target_rect(self) -> QRectF:
        """Current centered fit-inside rectangle in logical pixels."""
        return fit_inside_rect(
            QRectF(self.rect()), self._state.descriptor.preferred_aspect_ratio
        )

    @property
    def render_scale_factors(self) -> tuple[float, float]:
        """Expose the uniform scale pair for deterministic aspect tests."""
        target = self.render_target_rect
        design_width = 320.0
        design_height = design_width / (
            self._state.descriptor.preferred_aspect_ratio[0]
            / self._state.descriptor.preferred_aspect_ratio[1]
        )
        return target.width() / design_width, target.height() / design_height

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#f8fbfd"))
        logical = self.render_target_rect
        design_width = 320.0
        design_height = design_width / (
            self._state.descriptor.preferred_aspect_ratio[0]
            / self._state.descriptor.preferred_aspect_ratio[1]
        )
        painter.save()
        painter.translate(logical.left(), logical.top())
        scale = min(
            logical.width() / design_width,
            logical.height() / design_height,
        )
        painter.scale(scale, scale)
        self._draw_scene(painter)
        painter.restore()

    def _draw_scene(self, painter: QPainter) -> None:
        kind = self._state.descriptor.visual_kind
        if kind in {"drilling", "tapping", "reaming", "boring"}:
            self._draw_hole_scene(painter, kind)
        elif kind == "contour":
            self._draw_contour(painter)
        elif kind == "pocket":
            self._draw_pocket(painter)
        elif kind == "parallel":
            self._draw_parallel(painter)
        elif kind == "z_level":
            self._draw_z_level(painter)
        else:
            self._draw_facing(painter, selected_only=kind == "planar_faces")

    @staticmethod
    def _stock(painter: QPainter, rect: QRectF = QRectF(32, 55, 256, 70)) -> None:
        painter.setPen(QPen(QColor("#74899a"), 2))
        painter.setBrush(QColor("#dce7ee"))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QPen(QColor("#b2c3cf"), 1))
        painter.drawLine(rect.left() + 8, rect.top() + 12, rect.right() - 8, rect.top() + 12)

    @staticmethod
    def _tool(painter: QPainter, point: QPointF, *, ball: bool = False) -> None:
        painter.setPen(QPen(QColor("#374d60"), 2))
        painter.setBrush(QColor("#98aab7"))
        painter.drawRoundedRect(QRectF(point.x() - 7, point.y() - 43, 14, 36), 3, 3)
        painter.setBrush(QColor("#d5a43b"))
        if ball:
            painter.drawEllipse(QRectF(point.x() - 8, point.y() - 12, 16, 16))
        else:
            tip = QPolygonF(
                (QPointF(point.x() - 8, point.y() - 8), point, QPointF(point.x() + 8, point.y() - 8))
            )
            painter.drawPolygon(tip)

    @staticmethod
    def _arrow(
        painter: QPainter,
        start: QPointF,
        end: QPointF,
        color: str = "#176aa6",
        *,
        style: Qt.PenStyle = Qt.PenStyle.SolidLine,
        width: float = 3.0,
    ) -> None:
        pen = QPen(QColor(color), width, style)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QColor(color))
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        wing = 8.0
        head = QPolygonF(
            (
                end,
                QPointF(end.x() - wing * math.cos(angle - 0.55), end.y() - wing * math.sin(angle - 0.55)),
                QPointF(end.x() - wing * math.cos(angle + 0.55), end.y() - wing * math.sin(angle + 0.55)),
            )
        )
        painter.drawPolygon(head)

    def _draw_facing(self, painter: QPainter, *, selected_only: bool) -> None:
        self._stock(painter)
        if selected_only:
            painter.setPen(QPen(QColor("#7c91a0"), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor("#edf2f5"))
            painter.drawRect(QRectF(42, 70, 62, 42))
            painter.setPen(QPen(QColor("#178a75"), 2))
            painter.setBrush(QColor("#d8efe9"))
            painter.drawRect(QRectF(112, 70, 92, 42))
            painter.drawRect(QRectF(212, 70, 64, 42))
        path_pen = QPen(QColor("#178a75"), 3)
        painter.setPen(path_pen)
        rows = (70, 84, 98, 112)
        for index, y in enumerate(rows):
            left = 70 if selected_only and index % 2 else 45
            right = 245 if selected_only and index % 2 else 275
            painter.drawLine(left, y, right, y)
        self._tool(painter, QPointF(218, 70))
        self._arrow(painter, QPointF(90, 42), QPointF(230, 42))

    def _draw_contour(self, painter: QPainter) -> None:
        self._stock(painter)
        painter.setPen(QPen(QColor("#178a75"), 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(83, 108)
        path.lineTo(83, 77)
        path.cubicTo(83, 63, 104, 62, 118, 72)
        path.lineTo(203, 72)
        path.cubicTo(230, 72, 244, 87, 239, 108)
        path.closeSubpath()
        painter.drawPath(path)
        self._tool(painter, QPointF(83, 78))
        self._arrow(painter, QPointF(130, 49), QPointF(207, 49))

    def _draw_pocket(self, painter: QPainter) -> None:
        self._stock(painter)
        painter.setPen(QPen(QColor("#6e8494"), 2))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(QRectF(72, 72, 176, 42), 12, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#d8efe9"))
        painter.drawRoundedRect(QRectF(78, 76, 164, 34), 9, 9)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#178a75"), 3))
        for inset in (5, 12, 19):
            painter.drawRoundedRect(QRectF(72 + inset, 72 + inset / 2, 176 - 2 * inset, 42 - inset), 8, 8)
        self._tool(painter, QPointF(160, 92))

    def _draw_hole_scene(self, painter: QPainter, kind: str) -> None:
        self._stock(painter, QRectF(48, 82, 224, 45))
        painter.setPen(QPen(QColor("#617889"), 2))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(130, 91, 60, 25))
        if kind == "boring":
            painter.setPen(QPen(QColor("#617889"), 2))
            painter.setBrush(QColor("#ffffff"))
            painter.drawRect(QRectF(139, 82, 42, 45))
            painter.setPen(QPen(QColor("#d5a43b"), 2))
            painter.drawLine(139, 83, 139, 126)
            painter.drawLine(181, 83, 181, 126)
        elif kind == "reaming":
            painter.setPen(QPen(QColor("#d5a43b"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(133, 93, 54, 21))
        tool_x = 174 if kind == "boring" else 160
        self._tool(painter, QPointF(tool_x, 91), ball=False)
        self._arrow(painter, QPointF(213, 35), QPointF(213, 91), "#b45f2a")
        painter.setPen(QPen(QColor("#176aa6"), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        arc = QRectF(125, 22, 70, 40)
        painter.drawArc(arc, 25 * 16, 280 * 16)
        self._arrow(painter, QPointF(184, 29), QPointF(192, 40))
        if kind == "tapping":
            painter.setPen(QPen(QColor("#178a75"), 2))
            for y in range(67, 95, 6):
                painter.drawLine(151, y, 169, y + 4)
        elif kind == "reaming":
            painter.setPen(QPen(QColor("#178a75"), 2))
            painter.drawLine(151, 58, 151, 93)
            painter.drawLine(169, 58, 169, 93)

    def _draw_parallel(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor("#71889a"), 2))
        painter.setBrush(QColor("#dce7ee"))
        surface = QPainterPath()
        surface.moveTo(28, 108)
        surface.cubicTo(72, 55, 118, 126, 165, 78)
        surface.cubicTo(210, 38, 260, 115, 294, 76)
        surface.lineTo(294, 127)
        surface.lineTo(28, 127)
        surface.closeSubpath()
        painter.drawPath(surface)
        painter.save()
        painter.translate(160, 88)
        painter.rotate(self._state.direction_degrees)
        row_count = {"fast": 4, "balanced": 6, "high": 8}.get(
            self._state.quality, 6
        )
        spacing = 52.0 / max(1, row_count - 1)
        rows = tuple(-26.0 + row * spacing for row in range(row_count))
        if self._state.semantic_focus == "linking":
            link_rows = (-13.0, 17.0)
            if self._state.linking == "direct":
                self._draw_parallel_direct_link(painter, link_rows)
            else:
                self._draw_parallel_retract_link(painter, link_rows)
        elif self._state.cut_direction == "zigzag":
            self._draw_parallel_zigzag(painter, rows)
        else:
            self._draw_parallel_one_way(painter, rows)
        painter.restore()

    def _draw_z_level(self, painter: QPainter) -> None:
        """Render distinct constant-Z, quality, linking and safety states."""
        focus = (
            "overview"
            if self._state.semantic_focus == "ordering"
            else self._state.semantic_focus
        )
        painter.setPen(QPen(QColor("#71889a"), 2))
        painter.setBrush(QColor("#dce7ee"))
        profile = QPainterPath()
        profile.moveTo(38, 126)
        profile.lineTo(38, 104)
        profile.cubicTo(65, 92, 72, 65, 102, 62)
        profile.cubicTo(132, 59, 138, 105, 169, 103)
        profile.cubicTo(205, 100, 205, 45, 242, 43)
        profile.cubicTo(269, 42, 280, 75, 286, 126)
        profile.closeSubpath()
        painter.drawPath(profile)

        if focus == "disconnected_regions":
            painter.setPen(QPen(QColor("#178a75"), 3))
            for y in (72, 86, 101):
                painter.drawLine(54, y, 126, y)
                painter.drawLine(190, y, 270, y)
            self._draw_z_level_link(painter, QPointF(126, 86), QPointF(190, 86))
        elif focus == "inner_hole":
            painter.setPen(QPen(QColor("#178a75"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for inset in (0, 9, 18):
                painter.drawRoundedRect(
                    QRectF(75 + inset, 68 + inset / 2, 170 - inset * 2, 45 - inset),
                    11,
                    11,
                )
            painter.setPen(QPen(QColor("#f8fbfd"), 8))
            painter.drawEllipse(QRectF(143, 78, 35, 23))
            painter.setPen(QPen(QColor("#71889a"), 2))
            painter.drawEllipse(QRectF(143, 78, 35, 23))
        elif focus == "allowance":
            painter.setPen(QPen(QColor("#657d8e"), 2, Qt.PenStyle.DashLine))
            painter.drawPath(profile)
            offset = QPainterPath()
            offset.moveTo(45, 116)
            offset.cubicTo(81, 96, 82, 72, 106, 70)
            offset.cubicTo(136, 67, 143, 111, 171, 110)
            offset.cubicTo(212, 106, 211, 53, 243, 51)
            painter.setPen(QPen(QColor("#178a75"), 3))
            painter.drawPath(offset)
            painter.setPen(QColor("#334957"))
            painter.drawText(QPointF(198, 32), "Lượng dư")
            self._tool(painter, QPointF(242, 50), ball=True)
        else:
            level_count = {
                "fast": 4,
                "balanced": 6,
                "high": 9,
            }.get(self._state.quality, 6)
            levels = tuple(
                53.0 + index * (62.0 / max(1, level_count - 1))
                for index in range(level_count)
            )
            painter.setPen(QPen(QColor("#178a75"), 3))
            for index, y in enumerate(levels):
                left = 49 + min(26, index * 3)
                right = 278 - min(18, index * 2)
                painter.drawLine(left, y, right, y)
            if focus == "linking" and len(levels) >= 2:
                self._draw_z_level_link(
                    painter,
                    QPointF(250, levels[0]),
                    QPointF(250, levels[1]),
                )
            if focus == "safety_unknown":
                painter.setPen(QPen(QColor("#7a8790"), 2, Qt.PenStyle.DashLine))
                painter.setBrush(QColor("#f1f3f4"))
                painter.drawRoundedRect(QRectF(207, 51, 69, 47), 6, 6)
                painter.setPen(QColor("#59666f"))
                painter.drawText(QRectF(207, 51, 69, 47), Qt.AlignmentFlag.AlignCenter, "CHƯA RÕ")
            elif focus == "collision":
                painter.setPen(QPen(QColor("#b52e3e"), 3))
                painter.setBrush(QColor(214, 67, 84, 65))
                painter.drawEllipse(QRectF(205, 55, 55, 42))
                painter.drawLine(211, 61, 253, 91)
                painter.drawLine(253, 61, 211, 91)
                painter.setPen(QColor("#a32031"))
                painter.drawText(QPointF(205, 50), "KHÔNG AN TOÀN")
            elif focus == "level_range":
                painter.setPen(QPen(QColor("#356fbd"), 2))
                painter.drawLine(300, levels[0], 300, levels[-1])
                self._arrow(
                    painter,
                    QPointF(300, levels[0]),
                    QPointF(300, levels[-1]),
                    "#356fbd",
                )
                painter.setPen(QColor("#334957"))
                painter.drawText(QPointF(254, levels[0] - 5), "Cao độ trên")
                painter.drawText(QPointF(248, levels[-1] + 13), "Cao độ dưới")
            self._tool(painter, QPointF(236, levels[0]), ball=True)

        painter.setPen(QPen(QColor("#356fbd"), 2))
        self._arrow(painter, QPointF(20, 35), QPointF(20, 111), "#356fbd")
        painter.setPen(QColor("#244f89"))
        painter.drawText(QPointF(6, 28), "W/Z")

    def _draw_z_level_link(
        self,
        painter: QPainter,
        start: QPointF,
        end: QPointF,
    ) -> None:
        """Render either a checked direct link or retract/rapid/approach."""
        if self._state.linking == "direct":
            self._arrow(
                painter,
                start,
                end,
                "#178a75",
                style=Qt.PenStyle.DashDotLine,
                width=3.0,
            )
            painter.setPen(QColor("#176f60"))
            painter.drawText(QPointF(start.x() - 12, start.y() - 7), "AN TOÀN")
            return
        retract = QPointF(start.x(), start.y() - 25)
        rapid_end = QPointF(end.x(), start.y() - 25)
        self._arrow(painter, start, retract, "#b45f2a", width=3.0)
        self._arrow(
            painter,
            retract,
            rapid_end,
            "#b45f2a",
            style=Qt.PenStyle.DashLine,
            width=2.4,
        )
        self._arrow(painter, rapid_end, end, "#b45f2a", width=3.0)

    def _draw_parallel_one_way(
        self, painter: QPainter, rows: tuple[float, ...]
    ) -> None:
        """Emphasize same-direction cuts with deliberately quiet repositioning."""
        for row, y in enumerate(rows):
            self._arrow(painter, QPointF(-94, y), QPointF(94, y), "#178a75")
            if row >= len(rows) - 1:
                continue
            reposition = QPainterPath(QPointF(94, y))
            reposition.cubicTo(108, y + 3, -108, rows[row + 1] - 3, -94, rows[row + 1])
            painter.setPen(QPen(QColor("#8a9baa"), 1.2, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(reposition)
        self._tool(painter, QPointF(6, rows[0]), ball=True)

    def _draw_parallel_zigzag(
        self, painter: QPainter, rows: tuple[float, ...]
    ) -> None:
        """Render alternating cut arrows and continuous end-to-end ordering."""
        for row, y in enumerate(rows):
            reverse = row % 2 == 1
            start = QPointF(94 if reverse else -94, y)
            end = QPointF(-94 if reverse else 94, y)
            self._arrow(painter, start, end, "#178a75")
            if row < len(rows) - 1:
                painter.setPen(QPen(QColor("#178a75"), 2, Qt.PenStyle.SolidLine))
                painter.drawLine(end, QPointF(end.x(), rows[row + 1]))
        self._tool(painter, QPointF(6, rows[0]), ball=True)

    def _draw_parallel_direct_link(
        self, painter: QPainter, rows: tuple[float, float]
    ) -> None:
        """Render one explicit short, stay-down link between adjacent passes."""
        first_start = QPointF(-86, rows[0])
        first_end = QPointF(86, rows[0])
        # The linking-focused diagram uses the nearest endpoint so the one
        # connector stays short; cut-order direction is shown in its own state.
        second_start = QPointF(86, rows[1])
        second_end = QPointF(-86, rows[1])
        self._arrow(painter, first_start, first_end, "#178a75")
        self._arrow(painter, second_start, second_end, "#178a75")
        connector_end = second_start
        self._arrow(
            painter,
            first_end,
            connector_end,
            "#b45f2a",
            style=Qt.PenStyle.DashDotLine,
            width=3.4,
        )
        font = painter.font()
        font.setPointSizeF(7.5)
        painter.setFont(font)
        painter.setPen(QColor("#8f471e"))
        painter.drawText(QPointF(-42, 49), "Liên kết trực tiếp")
        self._tool(
            painter,
            QPointF(
                (first_end.x() + connector_end.x()) / 2,
                (rows[0] + rows[1]) / 2,
            ),
            ball=True,
        )

    def _draw_parallel_retract_link(
        self, painter: QPainter, rows: tuple[float, float]
    ) -> None:
        """Render retract-up, dashed horizontal rapid and approach-down."""
        self._arrow(painter, QPointF(-86, rows[0]), QPointF(86, rows[0]), "#178a75")
        self._arrow(painter, QPointF(-86, rows[1]), QPointF(86, rows[1]), "#178a75")
        cut_end = QPointF(86, rows[0])
        retract = QPointF(86, rows[0] - 31)
        rapid_end = QPointF(-86, rows[0] - 31)
        approach = QPointF(-86, rows[1])
        self._arrow(painter, cut_end, retract, "#b45f2a", width=3.2)
        self._arrow(
            painter,
            retract,
            rapid_end,
            "#b45f2a",
            style=Qt.PenStyle.DashLine,
            width=2.5,
        )
        self._arrow(painter, rapid_end, approach, "#b45f2a", width=3.2)
        self._tool(painter, cut_end, ball=True)


class CAMIllustrationCanvas(IllustrationViewport):
    """Compatibility name for the shared fit-inside illustration viewport."""


class CAMMotionLegend(QWidget):
    """Small line-style legend shared by expanded and child illustrations."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CAMMotionLegend")
        self.setAccessibleName("Chú giải chuyển động CAM")
        self.setAccessibleDescription(_MOTION_LEGEND_TEXT)
        self.setToolTip(_MOTION_LEGEND_TEXT)
        self.setMinimumHeight(24)
        self.setMaximumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        labels = (
            ("Đường cắt", "#178a75", Qt.PenStyle.SolidLine, False),
            ("Chạy nhanh", "#b45f2a", Qt.PenStyle.DashLine, False),
            ("Hướng cắt", "#178a75", Qt.PenStyle.SolidLine, True),
            ("Rút/Tiếp cận", "#b45f2a", Qt.PenStyle.SolidLine, True),
        )
        cell_width = max(1.0, self.width() / len(labels))
        y = self.height() / 2.0
        for index, (label, color, style, arrow) in enumerate(labels):
            left = index * cell_width + 3.0
            start = QPointF(left, y)
            end = QPointF(left + 22.0, y)
            if arrow:
                IllustrationViewport._arrow(
                    painter, start, end, color, style=style, width=2.0
                )
            else:
                painter.setPen(QPen(QColor(color), 2.0, style))
                painter.drawLine(start, end)
            painter.setPen(QColor("#334957"))
            painter.drawText(
                QRectF(left + 27.0, 0.0, cell_width - 30.0, self.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )


class CAMIllustrationPanel(QWidget):
    """Compact illustration, localized caption and one enlarge affordance."""

    enlarge_requested = Signal(object)
    illustration_updated = Signal(object)
    expanded_changed = Signal(bool)

    def __init__(
        self,
        descriptor: CAMIllustrationDescriptor,
        values: Mapping[str, PresentationValue] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CAMIllustrationPanel")
        self._pending_values: dict[str, PresentationValue] = dict(values or {})
        self._state = illustration_state(descriptor, self._pending_values)
        self._pending_semantic_focus = self._state.semantic_focus
        self._expanded = False
        self._metrics = CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1600, 900))
        self.render_revision = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._commit_pending_state)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(7, 5, 7, 5)
        self._root.setSpacing(4)
        header = QHBoxLayout()
        self.title = QLabel(f"MINH HỌA · {descriptor.title}")
        self.title.setObjectName("CAMIllustrationTitle")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(self.title)
        header.addStretch(1)
        self.expand_button = QToolButton()
        self.expand_button.setText("Mở rộng")
        self.expand_button.setAccessibleName("Mở rộng minh họa CAM")
        self.expand_button.setToolTip("Mở rộng minh họa ngay trong cửa sổ CAM.")
        self.expand_button.clicked.connect(
            lambda: self.set_expanded(not self._expanded)
        )
        header.addWidget(self.expand_button)
        self.enlarge_button = QToolButton()
        self.enlarge_button.setText("Phóng to")
        self.enlarge_button.setAccessibleName(f"Phóng to minh họa {descriptor.title}")
        self.enlarge_button.setToolTip("Mở minh họa lớn trong cửa sổ con của trình chỉnh sửa")
        self.enlarge_button.clicked.connect(
            lambda: self.enlarge_requested.emit(
                {"state": self._state, "focus": self.enlarge_button}
            )
        )
        header.addWidget(self.enlarge_button)
        self._root.addLayout(header)
        self.canvas = CAMIllustrationCanvas(self._state)
        self.viewport = self.canvas
        self._root.addWidget(self.canvas, 1)
        self.legend = CAMMotionLegend()
        self.legend.setVisible(False)
        self._root.addWidget(self.legend)
        self.caption = QLabel(self._state.caption)
        self.caption.setObjectName("CAMIllustrationCaption")
        self.caption.setWordWrap(True)
        self.caption.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.caption.setAccessibleName("Chú thích minh họa CAM")
        self._root.addWidget(self.caption)
        self.setAccessibleName(f"Bảng minh họa {descriptor.title}")
        self.setAccessibleDescription(self._state.accessible_description)
        self.setToolTip(_MOTION_LEGEND_TEXT)
        self.apply_density(self._metrics)

    @property
    def state(self) -> CAMIllustrationState:
        return self._state

    @property
    def debounce_active(self) -> bool:
        return self._timer.isActive()

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool, *, automatic: bool = False) -> None:
        """Switch between compact and in-popup expanded vector presentation."""
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.expand_button.setText(
            "Thu gọn minh họa" if expanded else "Mở rộng"
        )
        self.expand_button.setAccessibleName(
            "Thu gọn minh họa CAM" if expanded else "Mở rộng minh họa CAM"
        )
        self.caption.setWordWrap(expanded)
        self.legend.setVisible(expanded)
        self.apply_density(self._metrics)
        self._refresh_caption()
        if not automatic:
            self.expanded_changed.emit(expanded)

    def apply_density(self, metrics: CAMPopupMetrics) -> None:
        """Apply shared illustration limits in logical pixels."""
        self._metrics = metrics
        self._root.setContentsMargins(
            metrics.content_margin,
            metrics.row_spacing,
            metrics.content_margin,
            metrics.row_spacing,
        )
        self._root.setSpacing(metrics.row_spacing)
        panel_height = (
            metrics.illustration_expanded_height
            if self._expanded
            else metrics.illustration_collapsed_height
        )
        canvas_height = (
            metrics.illustration_canvas_expanded_height
            if self._expanded
            else metrics.illustration_canvas_collapsed_height
        )
        self.setMaximumHeight(panel_height)
        self.caption.setWordWrap(self._expanded)
        self.legend.setVisible(self._expanded)
        if self._expanded:
            canvas_height = max(96, canvas_height - self.legend.maximumHeight())
        self.canvas.setMinimumHeight(canvas_height)
        self.canvas.setMaximumHeight(canvas_height)
        for button in (self.expand_button, self.enlarge_button):
            button.setMinimumHeight(metrics.compact_button_height)
        self._refresh_caption()
        self.updateGeometry()

    def set_values(
        self,
        values: Mapping[str, PresentationValue],
        *,
        semantic_focus: str | None = None,
    ) -> None:
        """Debounce rapid draft edits without moving keyboard focus."""
        self._pending_values = dict(values)
        if semantic_focus is not None:
            self._pending_semantic_focus = semantic_focus
        self._timer.start()

    def flush_pending_update(self) -> None:
        """Commit a pending update deterministically for tests and screenshots."""
        if self._timer.isActive():
            self._timer.stop()
        self._commit_pending_state()

    def _commit_pending_state(self) -> None:
        state = illustration_state(
            self._state.descriptor,
            self._pending_values,
            semantic_focus=self._pending_semantic_focus,
        )
        if state == self._state:
            return
        focus = self.window().focusWidget()
        self._state = state
        self.canvas.set_state(state)
        self._refresh_caption()
        self.setAccessibleDescription(state.accessible_description)
        self.render_revision += 1
        self.illustration_updated.emit(state)
        if focus is not None:
            focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def _refresh_caption(self) -> None:
        full_caption = self._state.caption
        if self._expanded:
            rendered = full_caption
        else:
            rendered = self.caption.fontMetrics().elidedText(
                full_caption,
                Qt.TextElideMode.ElideRight,
                max(80, self.caption.width()),
            )
        self.caption.setText(rendered)
        self.caption.setToolTip(full_caption)
        self.caption.setAccessibleDescription(full_caption)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_caption()


class CAMIllustrationDialog(QDialog):
    """One small child popup for a larger view of the current vector scene."""

    def __init__(
        self, state: CAMIllustrationState, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CAMIllustrationDialog")
        self.setWindowTitle(f"Minh họa · {state.descriptor.title}")
        self.setAccessibleName(f"Minh họa lớn {state.descriptor.title}")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._root = QVBoxLayout(self)
        self.title_label = QLabel(self.windowTitle())
        self.title_label.setObjectName("CAMIllustrationDialogTitle")
        self.title_label.setAccessibleName("Tiêu đề cửa sổ minh họa")
        title_font = self.title_label.font()
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self._root.addWidget(self.title_label)
        self.canvas = CAMIllustrationCanvas(state)
        self._root.addWidget(self.canvas, 1)
        self.legend = CAMMotionLegend()
        self._root.addWidget(self.legend)
        caption = QLabel(state.caption)
        caption.setWordWrap(True)
        caption.setAccessibleName("Chú thích minh họa lớn")
        self._root.addWidget(caption)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = QPushButton("Đóng minh họa")
        self.close_button.setAccessibleName("Đóng minh họa lớn")
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        self._root.addLayout(buttons)
        defaults = CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1600, 900))
        self.apply_density(defaults, QRect(0, 0, 1600, 900))

    def apply_density(self, metrics: CAMPopupMetrics, available: QRect) -> None:
        """Size the enlarged vector child within the active monitor work area."""
        self._root.setContentsMargins(*(metrics.child_margin,) * 4)
        self._root.setSpacing(metrics.row_spacing)
        self.setMaximumSize(available.size())
        width = min(metrics.illustration_dialog_size.width(), available.width())
        height = min(metrics.illustration_dialog_size.height(), available.height())
        self.resize(width, height)
        self.canvas.setMinimumSize(0, 0)


__all__ = [
    "CAMIllustrationCanvas",
    "CAMIllustrationDescriptor",
    "CAMIllustrationDialog",
    "CAMIllustrationPanel",
    "CAMIllustrationRegistry",
    "CAMIllustrationState",
    "CAMMotionLegend",
    "IllustrationViewport",
    "fit_inside_rect",
    "illustration_state",
]
