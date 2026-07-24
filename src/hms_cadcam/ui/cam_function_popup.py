"""Singleton modeless popup shell for all production CAM Function Editors."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import logging

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QPoint,
    QRect,
    QSettings,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.cam_illustrations import (
    CAMIllustrationDialog,
    CAMIllustrationState,
)
from hms_cadcam.ui.function_editor.host import FunctionEditorHost
from hms_cadcam.ui.function_editor.production import ToolProfileSaveInteraction
from hms_cadcam.ui.localization import localize_widget_tree, operation_display_name
from hms_cadcam.ui.tool_program_profiles import ToolProfileSavePreviewDialog
from hms_cadcam.ui.ui_tokens import (
    CAM_POPUP_DENSITY,
    CAMPopupMetrics,
    cam_popup_style,
)


logger = logging.getLogger(__name__)

_SETTINGS_GROUP = "cam_function_popup_v2"
_LEGACY_SETTINGS_GROUP = "cam_function_popup_v1"


def clamp_popup_geometry(
    geometry: QRect,
    available_screens: Iterable[QRect],
    *,
    native_font_point_size: float = 9.0,
) -> QRect:
    """Clamp normal popup geometry and responsive size to a current work area."""
    screens = tuple(item for item in available_screens if item.isValid())
    if not screens:
        return QRect(geometry)
    screen = next(
        (
            item
            for item in screens
            if item.intersected(geometry).width() >= 80
            and item.intersected(geometry).height() >= 60
        ),
        screens[0],
    )
    metrics = CAM_POPUP_DENSITY.metrics_for(
        screen, native_font_point_size=native_font_point_size
    )
    width = min(
        max(geometry.width(), metrics.minimum_width), metrics.maximum_width
    )
    height = min(
        max(geometry.height(), metrics.minimum_height), metrics.maximum_height
    )
    x = min(max(geometry.x(), screen.left()), screen.right() - width + 1)
    y = min(max(geometry.y(), screen.top()), screen.bottom() - height + 1)
    return QRect(x, y, width, height)


class CAMToolSelectorDialog(QDialog):
    """Compact child popup for choosing one project-owned Tool Assembly."""

    tool_selected = Signal(object)

    def __init__(
        self,
        choices: tuple[tuple[object, str], ...],
        current_value: object,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CAMToolSelectorDialog")
        self.setWindowTitle("Chọn Tool")
        self.setAccessibleName("Bộ chọn Tool cho nguyên công CAM")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._root = QVBoxLayout(self)
        instruction = QLabel(
            "Chọn một cụm Tool thuộc dự án. Thay đổi chỉ cập nhật bản nháp."
        )
        instruction.setWordWrap(True)
        self._root.addWidget(instruction)
        self.search = QLineEdit()
        self.search.setObjectName("CAMToolSelectorSearch")
        self.search.setPlaceholderText("Lọc Tool…")
        self.search.setAccessibleName("Lọc danh sách Tool")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_choices)
        self._root.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("CAMToolSelectorList")
        self.list.setAccessibleName("Danh sách cụm Tool")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for value, label in choices:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.list.addItem(item)
            if value == current_value:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        self._root.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Tiếp tục chỉnh sửa")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        choose = QPushButton("Chọn Tool")
        choose.setObjectName("PrimaryPanelAction")
        choose.clicked.connect(self._accept_selection)
        buttons.addWidget(choose)
        self._root.addLayout(buttons)

        defaults = CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1600, 900))
        self.apply_density(defaults, QRect(0, 0, 1600, 900))

    def apply_density(self, metrics: CAMPopupMetrics, available: QRect) -> None:
        """Apply popup-owned child metrics without affecting application fonts."""
        self._root.setContentsMargins(*(metrics.child_margin,) * 4)
        self._root.setSpacing(metrics.row_spacing)
        width = min(metrics.tool_selector_size.width(), available.width())
        height = min(metrics.tool_selector_size.height(), available.height())
        self.setMaximumSize(available.size())
        self.resize(width, height)
        self.list.setSpacing(0)

    def _filter_choices(self, value: str) -> None:
        needle = value.strip().casefold()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _accept_selection(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.tool_selected.emit(item.data(Qt.ItemDataRole.UserRole))
        self.accept()


class CAMFunctionPopupHost(QDialog):
    """One reusable top-level popup; child dialogs are limited to one level."""

    operation_opened = Signal(str)
    operation_closed = Signal()
    child_popup_changed = Signal(object)

    def __init__(
        self,
        editor_host: FunctionEditorHost,
        settings: QSettings,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CAMFunctionPopupHost")
        self.setWindowTitle("Chỉnh sửa nguyên công CAM")
        self.setAccessibleName("Cửa sổ chỉnh sửa chức năng CAM")
        self.setAccessibleDescription(
            "Cửa sổ CAM chính duy nhất; vẫn cho phép chọn hình học trong khung nhìn."
        )
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self._settings = settings
        self.editor_host = editor_host
        self._child_dialog: QDialog | None = None
        self._child_key: str | None = None
        self._child_focus: QWidget | None = None
        self._closing_from_window = False
        self._allow_close = False
        self._geometry_restored = False
        self._screen_signal_connected = False
        self._metrics = self._metrics_for_available(self._available_geometry())
        self._apply_density(self._metrics)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(editor_host, 1)
        editor_host.collapse_requested.connect(self._editor_requested_close)
        editor_host.editor_replaced.connect(self._editor_replaced)
        editor_host.child_popup_requested.connect(self._open_child_request)
        localize_widget_tree(self)

    @property
    def active_operation_key(self) -> str | None:
        session = self.editor_host.active_session
        return session.operation_key if session is not None else None

    @property
    def child_dialog(self) -> QDialog | None:
        return self._child_dialog

    @property
    def density_metrics(self) -> CAMPopupMetrics:
        """Expose the active shared policy for diagnostics and focused tests."""
        return self._metrics

    def apply_available_work_area(self, available: QRect) -> None:
        """Refresh policy metrics after a monitor/work-area change."""
        if not available.isValid():
            return
        self._apply_density(self._metrics_for_available(available))
        self.set_compact_outer_geometry(
            clamp_popup_geometry(
                self.frameGeometry(),
                (available,),
                native_font_point_size=self._native_font_point_size(),
            )
        )

    def set_compact_outer_geometry(self, geometry: QRect) -> None:
        """Apply a frame-inclusive geometry using Qt's client-size API."""
        frame_width, frame_height = self._frame_extents()
        client_width = max(1, geometry.width() - frame_width)
        client_height = max(1, geometry.height() - frame_height)
        self.setGeometry(
            geometry.x(), geometry.y(), client_width, client_height
        )

    def open_current_operation(self) -> bool:
        """Open/rebind to the selected operation while respecting its dirty draft."""
        opened = self.editor_host.open_current_selection()
        if not opened:
            return False
        page = self.editor_host.active_page
        if page is not None:
            self.setWindowTitle(
                f"Chỉnh sửa CAM · {operation_display_name(page.schema.summary.title)}"
            )
        self._restore_or_place_geometry()
        self.show()
        self.raise_()
        self.activateWindow()
        self.operation_opened.emit(self.active_operation_key or "")
        return True

    def focus_existing(self) -> None:
        """Raise the same popup instance without rebinding or losing its draft."""
        self.show()
        self.raise_()
        self.activateWindow()

    def adopt_child_dialog(
        self,
        key: str,
        dialog: QDialog,
        focus_widget: QWidget | None = None,
    ) -> QDialog:
        """Own exactly one child popup and prevent a third dialog level."""
        current = self._child_dialog
        if current is not None:
            if self._child_key == key and current.isVisible():
                current.raise_()
                current.activateWindow()
                return current
            current.close()
            current.deleteLater()
        dialog.setParent(self, Qt.WindowType.Dialog)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setProperty("camChildPopup", True)
        dialog.installEventFilter(self)
        dialog.finished.connect(lambda _result, owned=dialog: self._child_finished(owned))
        self._child_dialog = dialog
        self._child_key = key
        self._child_focus = focus_widget or self.focusWidget()
        self.child_popup_changed.emit(dialog)
        self._place_child_dialog(dialog)
        dialog.open()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def close_child_popup(self) -> None:
        current = self._child_dialog
        if current is None:
            return
        current.close()

    def invalidate_project(self) -> None:
        """Drop stale Qt bindings after a project closes or is replaced."""
        self.close_child_popup()
        self.editor_host.invalidate_current_session()
        self._allow_close = True
        self.close()
        self._allow_close = False

    def _open_child_request(self, kind: str, payload: object) -> None:
        illustration_state = (
            payload
            if isinstance(payload, CAMIllustrationState)
            else payload.get("state")
            if isinstance(payload, dict)
            else None
        )
        illustration_focus = (
            payload.get("focus") if isinstance(payload, dict) else self.focusWidget()
        )
        if kind == "illustration" and isinstance(
            illustration_state, CAMIllustrationState
        ):
            self.adopt_child_dialog(
                "illustration",
                CAMIllustrationDialog(illustration_state, self),
                illustration_focus
                if isinstance(illustration_focus, QWidget)
                else self.focusWidget(),
            )
            return
        if kind == "tool_profile_save" and isinstance(
            payload, ToolProfileSaveInteraction
        ):
            dialog = ToolProfileSavePreviewDialog(
                payload.preview,
                self,
                preview_provider=payload.preview_callback,
            )

            def confirm(mode: object) -> None:
                try:
                    payload.confirm_callback(mode)  # type: ignore[arg-type]
                except (KeyError, RuntimeError, TypeError, ValueError) as error:
                    logger.error("Không thể lưu cấu hình Tool: %s", error)
                    return
                QTimer.singleShot(0, self.editor_host.refresh_current)

            dialog.confirmed.connect(confirm)
            self.adopt_child_dialog(
                "tool_profile_save", dialog, self.focusWidget()
            )
            return
        if kind != "tool_selector" or not isinstance(payload, dict):
            logger.warning("Yêu cầu cửa sổ con CAM không được hỗ trợ: %s", kind)
            return
        raw_choices = payload.get("choices", ())
        callback = payload.get("accept")
        choices = tuple(
            (item[0], str(item[1]))
            for item in raw_choices
            if isinstance(item, tuple) and len(item) == 2
        )
        if not callable(callback) or not choices:
            return
        dialog = CAMToolSelectorDialog(choices, payload.get("current"), self)
        dialog.tool_selected.connect(callback)
        self.adopt_child_dialog("tool_selector", dialog, payload.get("focus"))

    def _editor_replaced(self, editor_id: str) -> None:
        self.close_child_popup()
        page = self.editor_host.active_page
        if page is not None:
            self.setWindowTitle(
                f"Chỉnh sửa CAM · {operation_display_name(page.schema.summary.title)}"
            )
        if editor_id != "legacy":
            self.operation_opened.emit(self.active_operation_key or "")

    def _editor_requested_close(self) -> None:
        if self._closing_from_window:
            return
        self._allow_close = True
        try:
            self.close()
        finally:
            self._allow_close = False

    def _child_finished(self, dialog: QDialog) -> None:
        if dialog is not self._child_dialog:
            return
        self._child_dialog = None
        self._child_key = None
        focus = self._child_focus
        self._child_focus = None
        self.child_popup_changed.emit(None)
        if focus is not None and focus.isVisible():
            self.raise_()
            self.activateWindow()
            QTimer.singleShot(0, self, lambda: self._restore_focus(focus))

    def _place_child_dialog(self, dialog: QDialog) -> None:
        """Center the one child on this popup and keep it on the same monitor."""
        popup_frame = self.frameGeometry()
        screen = QGuiApplication.screenAt(popup_frame.center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else popup_frame
        metrics = self._metrics_for_available(available)
        apply_density = getattr(dialog, "apply_density", None)
        if callable(apply_density):
            apply_density(metrics, available)
        dialog.setMaximumSize(available.size())
        width = min(max(dialog.width(), dialog.minimumWidth()), available.width())
        height = min(max(dialog.height(), dialog.minimumHeight()), available.height())
        x = popup_frame.center().x() - width // 2
        y = popup_frame.center().y() - height // 2
        x = min(max(x, available.left()), available.right() - width + 1)
        y = min(max(y, available.top()), available.bottom() - height + 1)
        # A QDialog remains a top-level window even with an owner; geometry is
        # therefore expressed in global screen coordinates.
        dialog.setGeometry(x, y, width, height)

    @staticmethod
    def _restore_focus(widget: QWidget) -> None:
        try:
            if widget.isVisible():
                widget.setFocus(Qt.FocusReason.OtherFocusReason)
        except RuntimeError:
            return

    def _restore_or_place_geometry(self) -> None:
        if not self._geometry_restored:
            restored: QRect | None = None
            self._settings.beginGroup(_SETTINGS_GROUP)
            try:
                saved_rect = self._settings.value("rect")
            finally:
                self._settings.endGroup()
            if isinstance(saved_rect, QRect) and saved_rect.isValid():
                restored = QRect(saved_rect)
            else:
                self._settings.beginGroup(_LEGACY_SETTINGS_GROUP)
                try:
                    saved = self._settings.value("geometry")
                finally:
                    self._settings.endGroup()
                if isinstance(saved, (QByteArray, bytes)):
                    self.restoreGeometry(QByteArray(saved))
                    restored = self.normalGeometry()
            if restored is not None and restored.isValid():
                self.setWindowState(Qt.WindowState.WindowNoState)
                self.set_compact_outer_geometry(restored)
            elif self.parentWidget() is not None:
                parent = self.parentWidget().frameGeometry()
                available = self._available_geometry(parent.center())
                metrics = self._metrics_for_available(available)
                self._apply_density(metrics)
                width = metrics.popup_width
                height = self._preferred_initial_height(metrics)
                self.set_compact_outer_geometry(QRect(
                    parent.right() - width - metrics.content_margin,
                    parent.center().y() - height // 2,
                    width,
                    height,
                ))
            self._geometry_restored = True
        screens = tuple(screen.availableGeometry() for screen in QGuiApplication.screens())
        clamped = clamp_popup_geometry(
            self.frameGeometry(),
            screens,
            native_font_point_size=self._native_font_point_size(),
        )
        available = self._available_geometry(clamped.center())
        self._apply_density(self._metrics_for_available(available))
        self.set_compact_outer_geometry(clamped)

    def _save_geometry(self) -> None:
        self._settings.beginGroup(_SETTINGS_GROUP)
        try:
            self._settings.setValue("rect", self.frameGeometry())
        finally:
            self._settings.endGroup()
        self._settings.sync()

    def _native_font_point_size(self) -> float:
        point_size = self.font().pointSizeF()
        if point_size <= 0:
            point_size = QApplication.font().pointSizeF()
        return point_size if point_size > 0 else 9.0

    def _available_geometry(self, point: QPoint | None = None) -> QRect:
        if point is not None:
            screen = QGuiApplication.screenAt(point)
        else:
            parent = self.parentWidget()
            screen = parent.screen() if parent is not None else self.screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else QRect(0, 0, 1366, 768)

    def _metrics_for_available(self, available: QRect) -> CAMPopupMetrics:
        screen = QGuiApplication.screenAt(available.center())
        display_scale = screen.devicePixelRatio() if screen is not None else 1.0
        return CAM_POPUP_DENSITY.metrics_for(
            available,
            native_font_point_size=self._native_font_point_size(),
            display_scale_factor=display_scale,
        )

    def _apply_density(self, metrics: CAMPopupMetrics) -> None:
        self._metrics = metrics
        frame_width, frame_height = self._frame_extents()
        self.setMinimumSize(
            max(1, metrics.minimum_width - frame_width),
            max(1, metrics.minimum_height - frame_height),
        )
        self.setMaximumSize(
            max(1, metrics.maximum_width - frame_width),
            max(1, metrics.maximum_height - frame_height),
        )
        self.setStyleSheet(cam_popup_style(metrics))
        apply_density = getattr(self.editor_host, "apply_popup_density", None)
        if callable(apply_density):
            apply_density(metrics)

    def _frame_extents(self) -> tuple[int, int]:
        frame = self.frameGeometry()
        client = self.geometry()
        return (
            max(0, frame.width() - client.width()),
            max(0, frame.height() - client.height()),
        )

    def _preferred_initial_height(self, metrics: CAMPopupMetrics) -> int:
        page = self.editor_host.active_page
        preferred = getattr(page, "preferred_popup_height", None)
        if callable(preferred):
            return max(
                metrics.minimum_height,
                min(metrics.popup_height, int(preferred(metrics))),
            )
        return metrics.popup_height

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self._child_dialog
            and event.type() is QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
        ):
            self.close_child_popup()
            return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._allow_close:
            self.close_child_popup()
            self._save_geometry()
            event.accept()
            self.operation_closed.emit()
            return
        self._closing_from_window = True
        try:
            accepted = self.editor_host.request_close()
        finally:
            self._closing_from_window = False
        if not accepted:
            event.ignore()
            return
        self.close_child_popup()
        self._save_geometry()
        event.accept()
        self.operation_closed.emit()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._screen_signal_connected:
            handle.screenChanged.connect(self._screen_changed)
            self._screen_signal_connected = True
        QTimer.singleShot(
            0,
            self,
            lambda: self.apply_available_work_area(self._available_geometry()),
        )

    def _screen_changed(self, screen: object) -> None:
        available_getter = getattr(screen, "availableGeometry", None)
        if not callable(available_getter):
            return
        available = available_getter()
        if isinstance(available, QRect) and available.isValid():
            QTimer.singleShot(
                0, self, lambda: self.apply_available_work_area(available)
            )


__all__ = [
    "CAMFunctionPopupHost",
    "CAMToolSelectorDialog",
    "clamp_popup_geometry",
]
