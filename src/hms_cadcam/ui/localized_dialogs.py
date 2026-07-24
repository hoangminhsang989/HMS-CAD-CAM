"""Vietnamese Qt dialogs whose visible controls do not depend on OS locale."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QIdentityProxyModel, QModelIndex, Qt
from PySide6.QtGui import QAction, QShowEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog as _QtFileDialog,
    QMessageBox as _QtMessageBox,
    QTreeView,
    QWidget,
)


_DIALOG_TEXT = {
    "&Open": "Mở",
    "&Save": "Lưu",
    "&Cancel": "Hủy",
    "Open": "Mở",
    "Save": "Lưu",
    "Cancel": "Hủy",
    "Close": "Đóng",
    "OK": "Đóng",
    "Yes": "Có",
    "No": "Không",
    "Discard": "Không lưu",
    "Look in:": "Thư mục:",
    "File name:": "Tên tệp:",
    "Files of type:": "Loại tệp:",
    "My Computer": "Máy tính",
    "Computer": "Máy tính",
    "Recent": "Gần đây",
    "Desktop": "Màn hình nền",
    "Documents": "Tài liệu",
    "New Folder": "Thư mục mới",
    "Create New Folder": "Tạo thư mục mới",
    "Create a New Folder": "Tạo thư mục mới",
    "Delete": "Xóa",
    "&Delete": "Xóa",
    "Rename": "Đổi tên",
    "&Rename": "Đổi tên",
    "Show Name": "Hiện tên",
    "Show Size": "Hiện kích thước",
    "Show Type": "Hiện loại",
    "Show Date Modified": "Hiện ngày sửa đổi",
    "Back": "Quay lại",
    "Forward": "Tiến",
    "Parent Directory": "Thư mục cha",
    "List View": "Dạng danh sách",
    "Detail View": "Dạng chi tiết",
    "Change to detail view mode": "Chuyển sang dạng chi tiết",
    "Change to list view mode": "Chuyển sang dạng danh sách",
    "Go to the parent directory": "Đi đến thư mục cha",
}

_MESSAGE_BUTTON_TEXT = {
    _QtMessageBox.StandardButton.Ok: "Đóng",
    _QtMessageBox.StandardButton.Save: "Lưu",
    _QtMessageBox.StandardButton.SaveAll: "Lưu tất cả",
    _QtMessageBox.StandardButton.Open: "Mở",
    _QtMessageBox.StandardButton.Yes: "Có",
    _QtMessageBox.StandardButton.YesToAll: "Có cho tất cả",
    _QtMessageBox.StandardButton.No: "Không",
    _QtMessageBox.StandardButton.NoToAll: "Không cho tất cả",
    _QtMessageBox.StandardButton.Abort: "Dừng",
    _QtMessageBox.StandardButton.Retry: "Thử lại",
    _QtMessageBox.StandardButton.Ignore: "Bỏ qua",
    _QtMessageBox.StandardButton.Close: "Đóng",
    _QtMessageBox.StandardButton.Cancel: "Hủy",
    _QtMessageBox.StandardButton.Discard: "Không lưu",
    _QtMessageBox.StandardButton.Help: "Trợ giúp",
    _QtMessageBox.StandardButton.Apply: "Áp dụng",
    _QtMessageBox.StandardButton.Reset: "Đặt lại",
    _QtMessageBox.StandardButton.RestoreDefaults: "Khôi phục mặc định",
}


def _localized_text(text: str) -> str:
    stripped = text.replace("&", "").strip()
    return _DIALOG_TEXT.get(text, _DIALOG_TEXT.get(stripped, text))


class _VietnameseFileSystemProxy(QIdentityProxyModel):
    """Replace only QFileDialog column headers; filesystem data stays intact."""

    _HEADERS = ("Tên", "Kích thước", "Loại", "Ngày sửa đổi")

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if (
            orientation is Qt.Orientation.Horizontal
            and role == int(Qt.ItemDataRole.DisplayRole)
            and 0 <= section < len(self._HEADERS)
        ):
            return self._HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(
        self,
        index: QModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if (
            index.isValid()
            and index.column() == 2
            and role == int(Qt.ItemDataRole.DisplayRole)
        ):
            source_index = self.mapToSource(index)
            source_model = self.sourceModel()
            is_directory = getattr(source_model, "isDir", None)
            if callable(is_directory) and is_directory(source_index):
                return "Thư mục"
            file_info = getattr(source_model, "fileInfo", None)
            if callable(file_info):
                suffix = file_info(source_index).suffix().upper()
                return f"Tệp {suffix}" if suffix else "Tệp"
        if (
            index.isValid()
            and index.column() == 3
            and role == int(Qt.ItemDataRole.DisplayRole)
        ):
            source_index = self.mapToSource(index)
            source_model = self.sourceModel()
            file_info = getattr(source_model, "fileInfo", None)
            if callable(file_info):
                return file_info(source_index).lastModified().toString(
                    "dd/MM/yyyy HH:mm"
                )
        return super().data(index, role)


class LocalizedFileDialog(_QtFileDialog):
    """Non-native QFileDialog with stable Vietnamese presentation text."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.setOption(self.Option.DontUseNativeDialog, True)
        initial_directory = self.directory()
        self._vietnamese_proxy = _VietnameseFileSystemProxy(self)
        self.setProxyModel(self._vietnamese_proxy)
        self.setDirectory(initial_directory)
        self._localize_controls()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        self._localize_controls()
        super().showEvent(event)
        self._localize_controls()

    def _localize_controls(self) -> None:
        accept_text = {
            self.AcceptMode.AcceptOpen: "Mở",
            self.AcceptMode.AcceptSave: "Lưu",
        }[self.acceptMode()]
        if self.fileMode() is self.FileMode.Directory:
            accept_text = "Chọn"
        self.setLabelText(self.DialogLabel.LookIn, "Thư mục:")
        self.setLabelText(self.DialogLabel.FileName, "Tên tệp:")
        self.setLabelText(self.DialogLabel.FileType, "Loại tệp:")
        self.setLabelText(self.DialogLabel.Accept, accept_text)
        self.setLabelText(self.DialogLabel.Reject, "Hủy")
        for button in self.findChildren(QAbstractButton):
            localized = _localized_text(button.text())
            if localized != button.text():
                button.setText(localized)
            accessible_name = _localized_text(button.accessibleName())
            button.setAccessibleName(
                accessible_name
                or localized
                or _localized_text(button.toolTip())
            )
            if button.accessibleDescription():
                button.setAccessibleDescription(
                    _localized_text(button.accessibleDescription())
                )
            if button.toolTip():
                button.setToolTip(_localized_text(button.toolTip()))
        for action in self.findChildren(QAction):
            action.setText(_localized_text(action.text()))
            if action.toolTip():
                action.setToolTip(_localized_text(action.toolTip()))
        for combo in self.findChildren(QComboBox):
            for index in range(combo.count()):
                visible = combo.itemText(index)
                localized = _localized_text(visible)
                if localized != visible:
                    combo.setItemText(index, localized)
            if not combo.accessibleName():
                combo.setAccessibleName("Vị trí thư mục")
        for view in self.findChildren(QAbstractItemView):
            if view.objectName() != "sidebar" or view.model() is None:
                continue
            model = view.model()
            for row in range(model.rowCount()):
                index = model.index(row, 0)
                visible = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "")
                localized = _localized_text(visible)
                if localized != visible:
                    model.setData(
                        index,
                        localized,
                        Qt.ItemDataRole.DisplayRole,
                    )
        tree = self.findChild(QTreeView, "treeView")
        if tree is not None:
            for column, width in enumerate((240, 105, 120, 155)):
                tree.setColumnWidth(column, width)
        if not self.accessibleName():
            self.setAccessibleName(self.windowTitle() or "Chọn tệp")

    @classmethod
    def getOpenFileName(  # noqa: N802
        cls,
        parent: QWidget | None = None,
        caption: str = "",
        directory: str = "",
        filter: str = "",
        selectedFilter: str = "",
        options: _QtFileDialog.Option = _QtFileDialog.Option(0),
    ) -> tuple[str, str]:
        dialog = cls(parent, caption, directory, filter)
        dialog.setOptions(options | cls.Option.DontUseNativeDialog)
        dialog.setAcceptMode(cls.AcceptMode.AcceptOpen)
        dialog.setFileMode(cls.FileMode.ExistingFile)
        if selectedFilter:
            dialog.selectNameFilter(selectedFilter)
        dialog._localize_controls()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return "", dialog.selectedNameFilter()
        files = dialog.selectedFiles()
        return (files[0] if files else ""), dialog.selectedNameFilter()

    @classmethod
    def getSaveFileName(  # noqa: N802
        cls,
        parent: QWidget | None = None,
        caption: str = "",
        directory: str = "",
        filter: str = "",
        selectedFilter: str = "",
        options: _QtFileDialog.Option = _QtFileDialog.Option(0),
    ) -> tuple[str, str]:
        dialog = cls(parent, caption, directory, filter)
        dialog.setOptions(options | cls.Option.DontUseNativeDialog)
        dialog.setAcceptMode(cls.AcceptMode.AcceptSave)
        dialog.setFileMode(cls.FileMode.AnyFile)
        if selectedFilter:
            dialog.selectNameFilter(selectedFilter)
        dialog._localize_controls()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return "", dialog.selectedNameFilter()
        files = dialog.selectedFiles()
        return (files[0] if files else ""), dialog.selectedNameFilter()

    @classmethod
    def getExistingDirectory(  # noqa: N802
        cls,
        parent: QWidget | None = None,
        caption: str = "",
        directory: str = "",
        options: _QtFileDialog.Option = _QtFileDialog.Option.ShowDirsOnly,
    ) -> str:
        initial = directory or str(Path.cwd())
        dialog = cls(parent, caption, initial)
        dialog.setOptions(options | cls.Option.DontUseNativeDialog)
        dialog.setAcceptMode(cls.AcceptMode.AcceptOpen)
        dialog.setFileMode(cls.FileMode.Directory)
        dialog._localize_controls()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        files = dialog.selectedFiles()
        return files[0] if files else ""


class LocalizedMessageBox(_QtMessageBox):
    """QMessageBox whose standard buttons are explicitly Vietnamese."""

    def setStandardButtons(  # noqa: N802
        self,
        buttons: _QtMessageBox.StandardButton,
    ) -> None:
        super().setStandardButtons(buttons)
        self.localize_standard_buttons()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        self.localize_standard_buttons()
        super().showEvent(event)
        self.localize_standard_buttons()

    def localize_standard_buttons(self) -> None:
        """Translate every standard button currently owned by the box."""
        for standard, text in _MESSAGE_BUTTON_TEXT.items():
            button = self.button(standard)
            if button is None:
                continue
            button.setText(text)
            button.setAccessibleName(text)
        if not self.accessibleName():
            self.setAccessibleName(self.windowTitle() or self.text())
        if not self.accessibleDescription():
            self.setAccessibleDescription(self.text())

    @classmethod
    def _show(
        cls,
        icon: _QtMessageBox.Icon,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: _QtMessageBox.StandardButton,
        default_button: _QtMessageBox.StandardButton,
    ) -> _QtMessageBox.StandardButton:
        box = cls(parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        if default_button is not cls.StandardButton.NoButton:
            box.setDefaultButton(default_button)
        box.localize_standard_buttons()
        result = box.exec()
        clicked = box.clickedButton()
        if clicked is not None:
            standard = box.standardButton(clicked)
            if standard is not cls.StandardButton.NoButton:
                return standard
        return cls.StandardButton(result)

    @classmethod
    def information(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.Ok,
        defaultButton: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.NoButton,
    ) -> _QtMessageBox.StandardButton:
        return cls._show(
            cls.Icon.Information, parent, title, text, buttons, defaultButton
        )

    @classmethod
    def warning(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.Ok,
        defaultButton: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.NoButton,
    ) -> _QtMessageBox.StandardButton:
        return cls._show(
            cls.Icon.Warning, parent, title, text, buttons, defaultButton
        )

    @classmethod
    def critical(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.Ok,
        defaultButton: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.NoButton,
    ) -> _QtMessageBox.StandardButton:
        return cls._show(
            cls.Icon.Critical, parent, title, text, buttons, defaultButton
        )

    @classmethod
    def question(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: _QtMessageBox.StandardButton = (
            _QtMessageBox.StandardButton.Yes
            | _QtMessageBox.StandardButton.No
        ),
        defaultButton: _QtMessageBox.StandardButton = _QtMessageBox.StandardButton.NoButton,
    ) -> _QtMessageBox.StandardButton:
        return cls._show(
            cls.Icon.Question, parent, title, text, buttons, defaultButton
        )


QFileDialog = LocalizedFileDialog
QMessageBox = LocalizedMessageBox

__all__ = [
    "LocalizedFileDialog",
    "LocalizedMessageBox",
    "QFileDialog",
    "QMessageBox",
]
