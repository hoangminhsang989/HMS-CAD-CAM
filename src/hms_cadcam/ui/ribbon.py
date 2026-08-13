"""Compact ribbon used by the main window."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QMargins, QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QWidget,
)

from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.localization import ui_text
from hms_cadcam.ui.ui_tokens import MAIN_MENU_CONTENT_LEFT_PADDING

if TYPE_CHECKING:
    from hms_cadcam.ui.settings.ui_scale import UiScaleManager


_BASE_ICON_SIZE = QSize(22, 22)
_BASE_RIBBON_HEIGHT = 92
_BASE_PAGE_MARGINS = QMargins(3, 2, 3, 1)
_BASE_PAGE_SPACING = 1
_BASE_GROUP_MARGINS = QMargins(3, 2, 3, 2)
_BASE_GROUP_SPACING = 2
_BASE_ACTION_BUTTON_MINIMUM_WIDTH = 44
_BASE_ACTION_BUTTON_PADDING_HORIZONTAL = 12
_BASE_ACTION_BUTTON_PADDING_VERTICAL = 2
_BASE_SEPARATOR_WIDTH = 1
_BASE_TAB_SPACING = 2


@dataclass(frozen=True, slots=True)
class RibbonMetrics:
    """Ribbon/menu geometry derived from the immutable 100% baseline."""

    percent: int
    icon_size: QSize
    ribbon_height: int
    page_margins: QMargins
    page_spacing: int
    group_margins: QMargins
    group_spacing: int
    action_button_minimum_width: int
    action_button_padding_horizontal: int
    action_button_padding_vertical: int
    separator_width: int
    menu_padding_left: int
    tab_spacing: int

    @property
    def group_margin_left(self) -> int:
        return self.group_margins.left()

    @property
    def group_margin_right(self) -> int:
        return self.group_margins.right()

    @property
    def group_margin_top(self) -> int:
        return self.group_margins.top()

    @property
    def group_margin_bottom(self) -> int:
        return self.group_margins.bottom()

    @classmethod
    def from_scale_manager(cls, manager: UiScaleManager) -> "RibbonMetrics":
        """Build one non-cumulative metric snapshot from baseline values."""

        return cls(
            percent=manager.current_percent,
            icon_size=manager.scaled_icon_size(_BASE_ICON_SIZE, minimum=16),
            ribbon_height=manager.scaled_int(_BASE_RIBBON_HEIGHT, minimum=68),
            page_margins=manager.scaled_margins(_BASE_PAGE_MARGINS, minimum=1),
            page_spacing=manager.scaled_int(_BASE_PAGE_SPACING, minimum=1),
            group_margins=manager.scaled_margins(
                _BASE_GROUP_MARGINS, minimum=1
            ),
            group_spacing=manager.scaled_int(_BASE_GROUP_SPACING, minimum=1),
            action_button_minimum_width=manager.scaled_int(
                _BASE_ACTION_BUTTON_MINIMUM_WIDTH, minimum=24
            ),
            action_button_padding_horizontal=manager.scaled_int(
                _BASE_ACTION_BUTTON_PADDING_HORIZONTAL, minimum=4
            ),
            action_button_padding_vertical=manager.scaled_int(
                _BASE_ACTION_BUTTON_PADDING_VERTICAL, minimum=1
            ),
            separator_width=manager.scaled_int(
                _BASE_SEPARATOR_WIDTH, minimum=1
            ),
            menu_padding_left=manager.scaled_int(
                MAIN_MENU_CONTENT_LEFT_PADDING, minimum=1
            ),
            tab_spacing=manager.scaled_int(_BASE_TAB_SPACING, minimum=1),
        )


def ribbon_menu_style_sheet(metrics: RibbonMetrics) -> str:
    """Return the scoped menu stylesheet for one metric snapshot."""

    return (
        "QMenuBar#MainMenuBar { "
        f"padding-left: {metrics.menu_padding_left}px; "
        "}"
    )


def _ribbon_style_sheet(metrics: RibbonMetrics) -> str:
    return (
        "QTabWidget#RibbonTabs QToolButton#RibbonButton { "
        f"padding: {metrics.action_button_padding_vertical // 2}px "
        f"{metrics.action_button_padding_horizontal // 2}px; "
        "}\n"
        "QTabWidget#RibbonTabs QTabBar::tab { "
        f"margin-right: {metrics.tab_spacing}px; "
        "}"
    )


class RibbonWidget(QTabWidget):
    """A lightweight tabbed ribbon with disabled future-stage commands."""

    def __init__(
        self,
        project_actions: Mapping[str, QAction] | None = None,
        cad_actions: Mapping[str, QAction] | None = None,
        parent: QWidget | None = None,
        *,
        workspace_actions: Mapping[str, QAction] | None = None,
        ui_scale_manager: UiScaleManager | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_actions = project_actions or {}
        self._cad_actions = cad_actions or {}
        self._workspace_actions = workspace_actions or {}
        self._ui_scale_manager = ui_scale_manager
        self._action_buttons: list[tuple[QToolButton, QAction]] = []
        self._command_buttons: list[tuple[QToolButton, str, str]] = []
        self._page_layouts: list[QHBoxLayout] = []
        self._group_layouts: list[QHBoxLayout] = []
        self._metrics: RibbonMetrics | None = None
        self.setObjectName("RibbonTabs")
        self.setDocumentMode(True)
        self.setIconSize(_BASE_ICON_SIZE)
        self.setFixedHeight(_BASE_RIBBON_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build_tabs()
        self.retranslate_ui()
        if self._ui_scale_manager is not None:
            self._ui_scale_manager.preview_changed.connect(self.apply_ui_scale)
            self._ui_scale_manager.scale_changed.connect(self.apply_ui_scale)
            self.apply_ui_scale()

    @property
    def metrics(self) -> RibbonMetrics | None:
        """Return the last applied immutable metric snapshot."""

        return self._metrics

    def apply_ui_scale(self, _percent: int | None = None) -> None:
        """Apply logical ribbon metrics from the injected baseline scale."""
        manager = self._ui_scale_manager
        if manager is None:
            return
        metrics = RibbonMetrics.from_scale_manager(manager)
        self._metrics = metrics
        self.setIconSize(metrics.icon_size)
        self.tabBar().setIconSize(metrics.icon_size)
        self.setFixedHeight(metrics.ribbon_height)
        for layout in self._page_layouts:
            layout.setContentsMargins(metrics.page_margins)
            layout.setSpacing(metrics.page_spacing)
        for layout in self._group_layouts:
            layout.setContentsMargins(metrics.group_margins)
            layout.setSpacing(metrics.group_spacing)
        self.setStyleSheet(_ribbon_style_sheet(metrics))
        for button, _action in self._action_buttons:
            self._apply_button_metrics(button)
        for button, _source, _glyph in self._command_buttons:
            self._apply_button_metrics(button)

    def _build_tabs(self) -> None:
        self.addTab(
            self._page(
                (
                    (
                        "Tệp",
                        (
                            self._project_actions.get("new", "Mới"),
                            self._project_actions.get("open", "Mở"),
                            self._project_actions.get("save", "Lưu"),
                            self._project_actions.get(
                                "send_geometry",
                                "Nạp 3D vào CAM",
                            ),
                        ),
                    ),
                    ("Bảng tạm", ("Cut", "Copy", "Paste")),
                    (
                        "CAD",
                        (
                            self._cad_actions.get("open_step", "Mở STEP"),
                            self._cad_actions.get("open_brep", "Mở BREP"),
                            self._cad_actions.get("open_iges", "Mở IGES"),
                            self._cad_actions.get("open_stl", "Mở STL"),
                            self._cad_actions.get("fit_all", "Hiện toàn bộ"),
                        ),
                    ),
                    ("Phân tích", ("Measure", "Properties", "Statistics")),
                    (
                        "Post",
                        tuple(
                            self._workspace_actions[key]
                            for key in ("post_assembly",)
                            if key in self._workspace_actions
                        ),
                    ),
                    (
                        "Settings",
                        tuple(
                            self._workspace_actions[key]
                            for key in ("general_settings",)
                            if key in self._workspace_actions
                        ),
                    ),
                )
            ),
            "Trang chủ",
        )
        self.addTab(self._future_page("Khung dây", ("Điểm", "Đường", "Cung", "Bo góc")), "Khung dây")
        self.addTab(self._future_page("Bề mặt", ("Tạo mặt", "Dịch biên", "Cắt xén", "Nối chuyển tiếp")), "Bề mặt")
        self.addTab(self._future_page("Khối rắn", ("Khối", "Đùn", "Boolean", "Bo tròn")), "Khối rắn")
        self.addTab(self._future_page("Chuẩn bị mô hình", ("Di chuyển", "Đẩy", "Đơn giản hóa", "Sửa lỗi")), "Chuẩn bị mô hình")
        self.addTab(self._future_page("Lưới", ("Tạo lưới", "Sửa lưới", "Giảm lưới", "Kiểm tra")), "Lưới")
        self.addTab(self._future_page("Bản vẽ", ("Kích thước", "Ghi chú", "Mặt cắt", "Lớp")), "Bản vẽ")
        self.addTab(self._future_page("Biến đổi", ("Di chuyển", "Xoay", "Đối xứng", "Tỷ lệ")), "Biến đổi")
        self.addTab(self._future_page("Máy", ("Máy", "Dao", "Thiết lập", "Đường chạy dao")), "Máy")
        self.addTab(
            self._page(
                (
                    (
                        "Hướng nhìn",
                        tuple(
                            self._cad_actions[key]
                            for key in (
                                "view_top",
                                "view_bottom",
                                "view_front",
                                "view_back",
                                "view_left",
                                "view_right",
                                "view_isometric",
                            )
                            if key in self._cad_actions
                        ),
                    ),
                    (
                        "Hiển thị",
                        tuple(
                            self._cad_actions[key]
                            for key in (
                                "display_shaded",
                                "display_wireframe",
                                "display_shaded_with_edges",
                            )
                            if key in self._cad_actions
                        ),
                    ),
                    (
                        "Lựa chọn",
                        tuple(
                            self._cad_actions[key]
                            for key in (
                                "selection_solid",
                                "selection_face",
                                "selection_wire",
                                "selection_edge",
                                "selection_vertex",
                                "measurement",
                            )
                            if key in self._cad_actions
                        ),
                    ),
                )
            ),
            "Hiển thị",
        )

    def _future_page(self, group_name: str, commands: Iterable[str]) -> QWidget:
        return self._page(((group_name, commands),))

    def _page(self, groups: Iterable[tuple[str, Iterable[str | QAction]]]) -> QWidget:
        page = QFrame()
        page.setObjectName("RibbonPage")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(_BASE_PAGE_MARGINS)
        layout.setSpacing(_BASE_PAGE_SPACING)
        self._page_layouts.append(layout)
        for title, commands in groups:
            layout.addWidget(self._group(title, commands))
        layout.addStretch(1)
        return page

    def _group(self, title: str, commands: Iterable[str | QAction]) -> QGroupBox:
        group = QGroupBox(title)
        group.setObjectName("RibbonGroup")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(_BASE_GROUP_MARGINS)
        layout.setSpacing(_BASE_GROUP_SPACING)
        self._group_layouts.append(layout)
        # Keep the compact ribbon markers in the core Windows fonts used by
        # all supported locales; the previous diamond/triangle glyphs are
        # absent from Segoe UI and produced false missing-glyph/tofu hits.
        glyphs = ("●", "○", "■", "•", "●")
        for index, command in enumerate(commands):
            button = QToolButton()
            button.setObjectName("RibbonButton")
            button.setSizePolicy(
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Expanding,
            )
            if isinstance(command, QAction):
                button.setDefaultAction(command)
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                self._action_buttons.append((button, command))
                command.changed.connect(self.retranslate_ui)
            else:
                glyph = glyphs[index % len(glyphs)]
                self._command_buttons.append((button, command, glyph))
                button.setEnabled(False)
            layout.addWidget(button)
        return group

    def retranslate_ui(self, _value: object = None) -> None:
        """Keep full ribbon labels visible after every runtime locale switch."""
        for button, action in self._action_buttons:
            full_text = action.text()
            canonical = translation_service().canonical_key(full_text)
            compact_text = (
                translation_service().translate_key("Send 3D to CAM")
                if canonical == "Send new 3D to CAM project"
                else full_text
            )
            self._apply_button_text(
                button,
                full_text,
                displayed_text=compact_text,
            )
        for button, source, glyph in self._command_buttons:
            full_text = ui_text(source)
            canonical = translation_service().canonical_key(source) or source
            self._apply_button_text(
                button,
                full_text,
                prefix=f"{glyph}\n",
                tooltip=ui_text(f"{canonical} — " + "unavailable"),
            )

    def _apply_button_text(
        self,
        button: QToolButton,
        full_text: str,
        *,
        displayed_text: str | None = None,
        prefix: str = "",
        tooltip: str | None = None,
    ) -> None:
        displayed = displayed_text or full_text
        button.setText(f"{prefix}{displayed}")
        button.setProperty("fullText", full_text)
        button.setProperty(
            "compactText",
            displayed if displayed != full_text else "",
        )
        button.setProperty("textAuditCategory", "ribbon")
        button.setToolTip(tooltip or full_text)
        button.setAccessibleName(full_text)
        button.setAccessibleDescription(full_text)
        widest_line = max(
            button.fontMetrics().horizontalAdvance(line)
            for line in displayed.splitlines()
        )
        metrics = self._metrics
        minimum_width = (
            metrics.action_button_minimum_width
            if metrics is not None
            else _BASE_ACTION_BUTTON_MINIMUM_WIDTH
        )
        horizontal_padding = (
            metrics.action_button_padding_horizontal
            if metrics is not None
            else _BASE_ACTION_BUTTON_PADDING_HORIZONTAL
        )
        button.setMinimumWidth(
            max(minimum_width, widest_line + horizontal_padding)
        )

    def _apply_button_metrics(self, button: QToolButton) -> None:
        """Recompute text-dependent width without scaling runtime geometry."""

        displayed_lines = button.text().splitlines() or ("",)
        widest_line = max(
            button.fontMetrics().horizontalAdvance(line)
            for line in displayed_lines
        )
        metrics = self._metrics
        if metrics is None:
            return
        button.setMinimumWidth(
            max(
                metrics.action_button_minimum_width,
                widest_line + metrics.action_button_padding_horizontal,
            )
        )


__all__ = ["RibbonMetrics", "RibbonWidget", "ribbon_menu_style_sheet"]
