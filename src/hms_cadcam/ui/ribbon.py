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


class RibbonWidget(QTabWidget):
    """A lightweight tabbed ribbon with disabled future-stage commands."""

    def __init__(
        self,
        project_actions: Mapping[str, QAction] | None = None,
        cad_actions: Mapping[str, QAction] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_actions = project_actions or {}
        self._cad_actions = cad_actions or {}
        self.setObjectName("RibbonTabs")
        self.setDocumentMode(True)
        self.setIconSize(QSize(24, 24))
        self.setFixedHeight(132)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build_tabs()

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
                        ),
                    ),
                    ("Clipboard", ("Cắt", "Sao chép", "Dán")),
                    (
                        "CAD",
                        (
                            self._cad_actions.get("open_step", "Mở STEP"),
                            self._cad_actions.get("open_brep", "Mở BREP"),
                            self._cad_actions.get("fit_all", "Fit All"),
                        ),
                    ),
                    ("Phân tích", ("Đo", "Thuộc tính", "Thống kê")),
                )
            ),
            "Trang chủ",
        )
        self.addTab(self._future_page("Wireframe", ("Điểm", "Đường", "Cung", "Bo góc")), "Wireframe")
        self.addTab(self._future_page("Surfaces", ("Tạo mặt", "Offset", "Trim", "Blend")), "Surfaces")
        self.addTab(self._future_page("Solids", ("Khối", "Extrude", "Boolean", "Fillet")), "Solids")
        self.addTab(self._future_page("Model Prep", ("Move", "Push", "Simplify", "Heal")), "Model Prep")
        self.addTab(self._future_page("Mesh", ("Tạo mesh", "Sửa mesh", "Giảm lưới", "Kiểm tra")), "Mesh")
        self.addTab(self._future_page("Drafting", ("Kích thước", "Ghi chú", "Hatch", "Layer")), "Drafting")
        self.addTab(self._future_page("Transform", ("Di chuyển", "Xoay", "Đối xứng", "Scale")), "Transform")
        self.addTab(self._future_page("Machine", ("Máy", "Dao", "Thiết lập", "Toolpath")), "Machine")
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
                                "selection_edge",
                            )
                            if key in self._cad_actions
                        ),
                    ),
                )
            ),
            "View",
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
        glyphs = ("◇", "□", "△", "○", "◎")
        for index, command in enumerate(commands):
            button = QToolButton()
            button.setObjectName("RibbonButton")
            if isinstance(command, QAction):
                button.setDefaultAction(command)
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            else:
                button.setText(f"{glyphs[index % len(glyphs)]}\n{command}")
                button.setToolTip(f"{command} — chưa khả dụng")
                button.setEnabled(False)
            layout.addWidget(button)
        return group
