"""Compact ribbon used by the main window."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from PySide6.QtCore import QSize, Qt
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


class RibbonWidget(QTabWidget):
    """A lightweight tabbed ribbon with disabled future-stage commands."""

    def __init__(
        self,
        project_actions: Mapping[str, QAction] | None = None,
        cad_actions: Mapping[str, QAction] | None = None,
        parent: QWidget | None = None,
        *,
        workspace_actions: Mapping[str, QAction] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_actions = project_actions or {}
        self._cad_actions = cad_actions or {}
        self._workspace_actions = workspace_actions or {}
        self._action_buttons: list[tuple[QToolButton, QAction]] = []
        self._command_buttons: list[tuple[QToolButton, str, str]] = []
        self.setObjectName("RibbonTabs")
        self.setDocumentMode(True)
        self.setIconSize(QSize(24, 24))
        self.setFixedHeight(112)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build_tabs()
        self.retranslate_ui()

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
        layout.setContentsMargins(4, 3, 4, 0)
        layout.setSpacing(0)
        for title, commands in groups:
            layout.addWidget(self._group(title, commands))
        layout.addStretch(1)
        return page

    def _group(self, title: str, commands: Iterable[str | QAction]) -> QGroupBox:
        group = QGroupBox(title)
        group.setObjectName("RibbonGroup")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
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
        button.setMinimumWidth(max(48, widest_line + 16))
