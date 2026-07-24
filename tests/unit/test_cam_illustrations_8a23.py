"""Focused tests for the HMS-owned CAM illustration system."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QCoreApplication, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hms_cadcam.ui.cam_illustrations import (  # noqa: E402
    CAMIllustrationPanel,
    CAMIllustrationRegistry,
    CAMIllustrationDialog,
    CAMMotionLegend,
    IllustrationViewport,
    fit_inside_rect,
    illustration_state,
)
from hms_cadcam.ui.ui_tokens import CAM_POPUP_DENSITY  # noqa: E402
from PySide6.QtCore import QRect  # noqa: E402


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dispose(widget, application: QApplication) -> None:
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def test_registry_has_one_vector_illustration_for_every_production_editor() -> None:
    registry = CAMIllustrationRegistry()
    expected = {
        "facing_production_9a5_1",
        "planar_face_facing_production_9a5_1",
        "contour_production_9a5_2",
        "pocket_production_9a5_3",
        "drilling_production_9a6",
        "tapping_production_9a6",
        "reaming_production_9a6",
        "boring_production_9a6",
        "parallel_finishing_production_8a2_3",
        "z_level_finishing_production_8a3_3",
    }

    assert {item.key for item in registry.descriptors} == expected
    assert len(registry.descriptors) == 10
    assert tuple(item.title for item in registry.descriptors) == (
        "Phay mặt 2.5D",
        "Phay các mặt phẳng",
        "Phay biên dạng 2D",
        "Phay hốc 2.5D",
        "Khoan",
        "Taro",
        "Doa lỗ",
        "Khoét lỗ",
        "Gia công tinh song song",
        "Gia công tinh theo cao độ Z",
    )
    assert all(item.caption and item.accessible_description for item in registry.descriptors)
    assert all(item.operation_type for item in registry.descriptors)
    assert all(item.preferred_aspect_ratio for item in registry.descriptors)
    assert all(item.compact_size and item.expanded_size for item in registry.descriptors)
    assert all(item.render_source == "hms_qpainter_vector" for item in registry.descriptors)
    assert all(
        {"tool", "workpiece", "motion_arrow", "machining_region"}
        <= item.semantic_features
        for item in registry.descriptors
    )
    assert "trục Z" in registry.resolve("boring_production_9a6").caption
    assert "ngang hai chiều" in registry.resolve(
        "boring_production_9a6"
    ).accessible_description


def test_every_registered_illustration_renders_at_high_dpi_independent_size() -> None:
    application = _application()
    panels = []
    try:
        for descriptor in CAMIllustrationRegistry().descriptors:
            panel = CAMIllustrationPanel(descriptor)
            panels.append(panel)
            panel.resize(520, 240)
            panel.show()
            application.processEvents()
            image = panel.grab().toImage()
            assert not image.isNull()
            assert image.width() == 520
            assert panel.canvas.accessibleDescription()
            assert panel.caption.text()
    finally:
        for panel in panels:
            _dispose(panel, application)


def test_parallel_dynamic_states_have_distinct_captions_metadata_and_fingerprints() -> None:
    descriptor = CAMIllustrationRegistry().resolve(
        "parallel_finishing_production_8a2_3"
    )
    one_way = illustration_state(
        descriptor,
        {
            "cut_direction": "one_way",
            "linking_mode": "retract",
            "quality_profile": "fast",
            "effective_direction_angle_degrees": "25",
        },
    )
    zigzag = illustration_state(
        descriptor,
        {
            "cut_direction": "zigzag",
            "quality_profile": "high",
            "effective_direction_angle_degrees": "90",
            "ordering_override_enabled": True,
        },
    )
    direct = illustration_state(
        descriptor,
        {"cut_direction": "one_way", "linking_mode": "direct"},
        semantic_focus="linking",
    )
    retract = illustration_state(
        descriptor,
        {"cut_direction": "one_way", "linking_mode": "retract"},
        semantic_focus="linking",
    )

    assert one_way.caption == (
        "Mọi lượt chạy cùng một hướng; Tool quay về đầu lượt trước khi tiếp tục."
    )
    assert zigzag.caption == (
        "Các lượt chạy đổi chiều liên tục để giảm quãng đường không cắt."
    )
    assert direct.caption == (
        "Tool nối trực tiếp sang lượt kế tiếp sau khi đoạn nối đạt kiểm tra an toàn."
    )
    assert retract.caption == (
        "Tool rút lên, chạy nhanh sang vị trí mới rồi tiếp cận xuống."
    )
    assert zigzag.direction_degrees == 90.0
    assert one_way.render_state_ids == (
        "focus_ordering",
        "one_way",
        "quality_fast",
    )
    assert zigzag.render_state_ids == (
        "focus_ordering",
        "zigzag",
        "quality_high",
    )
    assert "same_direction_cut_arrows" in one_way.semantic_metadata
    assert "alternating_cut_arrows" in zigzag.semantic_metadata
    assert "direct_link_segment" in direct.semantic_metadata
    assert "no_z_lift" in direct.semantic_metadata
    assert "retract_vertical_up" in retract.semantic_metadata
    assert "rapid_horizontal_dashed" in retract.semantic_metadata
    assert "approach_vertical_down" in retract.semantic_metadata
    assert len(
        {
            one_way.render_fingerprint,
            zigzag.render_fingerprint,
            direct.render_fingerprint,
            retract.render_fingerprint,
        }
    ) == 4


def test_parallel_four_semantic_states_render_to_different_pixels() -> None:
    application = _application()
    descriptor = CAMIllustrationRegistry().resolve(
        "parallel_finishing_production_8a2_3"
    )
    states = (
        illustration_state(descriptor, {"cut_direction": "one_way"}),
        illustration_state(descriptor, {"cut_direction": "zigzag"}),
        illustration_state(
            descriptor,
            {"linking_mode": "direct"},
            semantic_focus="linking",
        ),
        illustration_state(
            descriptor,
            {"linking_mode": "retract"},
            semantic_focus="linking",
        ),
    )
    viewports = [IllustrationViewport(state) for state in states]
    try:
        payloads = []
        for viewport in viewports:
            viewport.resize(640, 360)
            viewport.show()
            application.processEvents()
            image = viewport.grab().toImage()
            payloads.append(bytes(image.constBits()))
        assert len(set(payloads)) == 4
    finally:
        for viewport in viewports:
            _dispose(viewport, application)


def test_fit_inside_rect_preserves_ratio_centers_and_never_crops() -> None:
    ratio = (16, 9)
    for viewport in (QRectF(0, 0, 900, 180), QRectF(0, 0, 180, 900)):
        target = fit_inside_rect(viewport, ratio, padding=8)
        assert abs(target.width() / target.height() - 16 / 9) < 1.0e-9
        assert abs(target.center().x() - viewport.center().x()) < 1.0e-9
        assert abs(target.center().y() - viewport.center().y()) < 1.0e-9
        assert viewport.contains(target)


def test_viewport_uses_uniform_scale_at_wide_tall_and_high_dpi_sizes() -> None:
    application = _application()
    descriptor = CAMIllustrationRegistry().resolve(
        "parallel_finishing_production_8a2_3"
    )
    viewport = IllustrationViewport(illustration_state(descriptor))
    try:
        for size in ((800, 160), (180, 700), (640, 360)):
            viewport.resize(*size)
            viewport.show()
            application.processEvents()
            scale_x, scale_y = viewport.render_scale_factors
            assert abs(scale_x - scale_y) < 1.0e-9
            assert QRectF(viewport.rect()).contains(viewport.render_target_rect)
            for dpr in (1.0, 1.25, 1.5, 2.0):
                physical_width = viewport.render_target_rect.width() * dpr
                physical_height = viewport.render_target_rect.height() * dpr
                assert abs(physical_width / physical_height - 16 / 9) < 1.0e-9
    finally:
        _dispose(viewport, application)


def test_compact_expanded_and_child_zoom_share_registry_aspect_ratio() -> None:
    application = _application()
    descriptor = CAMIllustrationRegistry().resolve("boring_production_9a6")
    panel = CAMIllustrationPanel(descriptor)
    dialog = CAMIllustrationDialog(illustration_state(descriptor))
    try:
        panel.resize(520, 125)
        panel.show()
        dialog.resize(700, 500)
        dialog.show()
        application.processEvents()
        compact_ratio = (
            panel.canvas.render_target_rect.width()
            / panel.canvas.render_target_rect.height()
        )
        panel.set_expanded(True)
        panel.resize(520, 300)
        application.processEvents()
        expanded_ratio = (
            panel.canvas.render_target_rect.width()
            / panel.canvas.render_target_rect.height()
        )
        child_ratio = (
            dialog.canvas.render_target_rect.width()
            / dialog.canvas.render_target_rect.height()
        )
        assert compact_ratio == expanded_ratio == child_ratio == 4 / 3
        assert panel.caption.parent() is panel
        assert panel.caption is not panel.canvas
        assert dialog.windowTitle() == "Minh họa · Khoét lỗ"
        assert dialog.title_label.text() == "Minh họa · Khoét lỗ"
        assert dialog.close_button.text() == "Đóng minh họa"
        assert dialog.legend.isVisible()
    finally:
        _dispose(dialog, application)
        _dispose(panel, application)


def test_boring_semantics_are_axial_centered_and_keep_outer_stock_intact() -> None:
    descriptor = CAMIllustrationRegistry().resolve("boring_production_9a6")
    flags = dict(descriptor.semantic_flags)
    assert flags == {
        "axial_down_arrow": True,
        "rotation_about_hole_axis": True,
        "horizontal_bidirectional_arrow": False,
        "centered_cutaway": True,
        "outer_workpiece_intact": True,
    }


def test_parallel_panel_debounces_without_running_a_calculation_or_losing_focus() -> None:
    application = _application()
    descriptor = CAMIllustrationRegistry().resolve(
        "parallel_finishing_production_8a2_3"
    )
    panel = CAMIllustrationPanel(descriptor)
    panel.show()
    panel.enlarge_button.setFocus()
    for angle in range(25):
        panel.set_values(
            {
                "cut_direction": "zigzag",
                "effective_direction_angle_degrees": str(angle),
            }
        )
    assert panel.debounce_active
    assert panel.render_revision == 0

    panel.flush_pending_update()
    application.processEvents()

    assert panel.render_revision == 1
    assert panel.state.direction_degrees == 24.0
    assert panel.enlarge_button.hasFocus()
    _dispose(panel, application)


def test_illustration_defaults_compact_and_expands_without_losing_caption() -> None:
    application = _application()
    descriptor = CAMIllustrationRegistry().resolve(
        "parallel_finishing_production_8a2_3"
    )
    panel = CAMIllustrationPanel(descriptor)
    metrics = CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1366, 768))
    try:
        panel.apply_density(metrics)
        panel.resize(metrics.popup_width, metrics.illustration_collapsed_height)
        panel.show()
        application.processEvents()
        assert not panel.is_expanded
        assert panel.maximumHeight() == metrics.illustration_collapsed_height
        assert panel.caption.toolTip() == panel.state.caption
        assert panel.expand_button.text() == "Mở rộng"

        panel.expand_button.click()
        application.processEvents()
        assert panel.is_expanded
        assert panel.maximumHeight() == metrics.illustration_expanded_height
        assert panel.caption.text() == panel.state.caption
        assert panel.expand_button.text() == "Thu gọn minh họa"
        assert panel.legend.isVisible()
        assert isinstance(panel.legend, CAMMotionLegend)
        assert "nét cam đứt" in panel.legend.accessibleDescription().casefold()
    finally:
        _dispose(panel, application)
