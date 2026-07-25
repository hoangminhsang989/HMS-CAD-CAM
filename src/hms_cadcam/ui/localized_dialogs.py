"""HMS-localized Qt dialogs whose visible controls ignore the OS locale."""

from __future__ import annotations

from collections.abc import Sequence
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
    QLineEdit,
    QMessageBox as _QtMessageBox,
    QSizePolicy,
    QTreeView,
    QWidget,
)

from hms_cadcam.core.paths import ApplicationPathsService

from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.localization import ui_text


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
    "Don’t save": "Không lưu",
    "Save All": "Lưu tất cả",
    "Yes to All": "Có cho tất cả",
    "No to All": "Không cho tất cả",
    "Abort": "Dừng",
    "Retry": "Thử lại",
    "Ignore": "Bỏ qua",
    "Help": "Trợ giúp",
    "Apply": "Áp dụng",
    "Reset": "Đặt lại",
    "Restore Defaults": "Khôi phục mặc định",
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
    "Go back": "Quay lại",
    "Go forward": "Tiến",
    "Files": "Tệp",
    "Sidebar": "Thanh bên",
    "List of places and bookmarks": "Danh sách vị trí và dấu trang",
    "Show hidden files": "Hiện tệp ẩn",
}

_KOREAN_DIALOG_TEXT = {
    "Open": "열기",
    "Save": "저장",
    "Cancel": "취소",
    "Close": "닫기",
    "OK": "확인",
    "Yes": "예",
    "No": "아니요",
    "Discard": "저장 안 함",
    "Don’t save": "저장 안 함",
    "Save All": "모두 저장",
    "Yes to All": "모두 예",
    "No to All": "모두 아니요",
    "Abort": "중단",
    "Retry": "다시 시도",
    "Ignore": "무시",
    "Help": "도움말",
    "Apply": "적용",
    "Reset": "초기화",
    "Restore Defaults": "기본값 복원",
    "Look in:": "폴더:",
    "File name:": "파일 이름:",
    "Files of type:": "파일 형식:",
    "My Computer": "내 컴퓨터",
    "Computer": "컴퓨터",
    "Recent": "최근 항목",
    "Desktop": "바탕 화면",
    "Documents": "문서",
    "New Folder": "새 폴더",
    "Create New Folder": "새 폴더 만들기",
    "Create a New Folder": "새 폴더 만들기",
    "Delete": "삭제",
    "Rename": "이름 바꾸기",
    "Show Name": "이름 표시",
    "Show Size": "크기 표시",
    "Show Type": "유형 표시",
    "Show Date Modified": "수정한 날짜 표시",
    "Back": "뒤로",
    "Forward": "앞으로",
    "Parent Directory": "상위 폴더",
    "List View": "목록 보기",
    "Detail View": "자세히 보기",
    "Change to detail view mode": "자세히 보기로 전환",
    "Change to list view mode": "목록 보기로 전환",
    "Go to the parent directory": "상위 폴더로 이동",
    "Go back": "뒤로",
    "Go forward": "앞으로",
    "Files": "파일",
    "Sidebar": "사이드바",
    "List of places and bookmarks": "위치 및 북마크 목록",
    "Show hidden files": "숨김 파일 표시",
}

_MESSAGE_BUTTON_TEXT = {
    _QtMessageBox.StandardButton.Ok: "OK",
    _QtMessageBox.StandardButton.Save: "Save",
    _QtMessageBox.StandardButton.SaveAll: "Save All",
    _QtMessageBox.StandardButton.Open: "Open",
    _QtMessageBox.StandardButton.Yes: "Yes",
    _QtMessageBox.StandardButton.YesToAll: "Yes to All",
    _QtMessageBox.StandardButton.No: "No",
    _QtMessageBox.StandardButton.NoToAll: "No to All",
    _QtMessageBox.StandardButton.Abort: "Abort",
    _QtMessageBox.StandardButton.Retry: "Retry",
    _QtMessageBox.StandardButton.Ignore: "Ignore",
    _QtMessageBox.StandardButton.Close: "Close",
    _QtMessageBox.StandardButton.Cancel: "Cancel",
    _QtMessageBox.StandardButton.Discard: "Don’t save",
    _QtMessageBox.StandardButton.Help: "Help",
    _QtMessageBox.StandardButton.Apply: "Apply",
    _QtMessageBox.StandardButton.Reset: "Reset",
    _QtMessageBox.StandardButton.RestoreDefaults: "Restore Defaults",
}


def _localized_text(text: str) -> str:
    stripped = text.replace("&", "").strip()
    reverse = {value: key.replace("&", "") for key, value in _DIALOG_TEXT.items()}
    source = (
        text.replace("&", "")
        if text in _DIALOG_TEXT or stripped in _DIALOG_TEXT
        else reverse.get(text, stripped)
    )
    language = translation_service().language
    if language is UiLanguage.VI_VN:
        return _DIALOG_TEXT.get(text, _DIALOG_TEXT.get(source, text))
    if language is UiLanguage.KO_KR:
        return _KOREAN_DIALOG_TEXT.get(source, ui_text(source))
    return source


class _LocalizedFileSystemProxy(QIdentityProxyModel):
    """Replace only QFileDialog column headers; filesystem data stays intact."""

    _HEADERS = ("Name", "Size", "Type", "Date modified")

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
            return ui_text(self._HEADERS[section])
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
                return ui_text("Folder")
            file_info = getattr(source_model, "fileInfo", None)
            if callable(file_info):
                suffix = file_info(source_index).suffix().upper()
                if translation_service().language is UiLanguage.VI_VN:
                    return f"Tệp {suffix}" if suffix else "Tệp"
                if translation_service().language is UiLanguage.KO_KR:
                    return f"{suffix} 파일" if suffix else "파일"
                return f"{suffix} file" if suffix else "File"
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
    """Non-native QFileDialog with stable application-locale presentation."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.setOption(self.Option.DontUseNativeDialog, True)
        initial_directory = self.directory()
        self._localized_proxy = _LocalizedFileSystemProxy(self)
        self.setProxyModel(self._localized_proxy)
        self.setDirectory(initial_directory)
        self._localize_controls()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        self._localize_controls()
        super().showEvent(event)
        self._localize_controls()

    def _localize_controls(self) -> None:
        accept_text = {
            self.AcceptMode.AcceptOpen: ui_text("Open"),
            self.AcceptMode.AcceptSave: ui_text("Save"),
        }[self.acceptMode()]
        if self.fileMode() is self.FileMode.Directory:
            accept_text = ui_text("Select")
        self.setWindowTitle(ui_text(self.windowTitle()))
        self.setLabelText(self.DialogLabel.LookIn, f"{ui_text('Folder')}:")
        self.setLabelText(self.DialogLabel.FileName, f"{ui_text('File name')}:")
        self.setLabelText(self.DialogLabel.FileType, f"{ui_text('File type')}:")
        self.setLabelText(self.DialogLabel.Accept, accept_text)
        self.setLabelText(self.DialogLabel.Reject, ui_text("Cancel"))
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
                combo.setAccessibleName(
                    {
                        UiLanguage.VI_VN: "Vị trí thư mục",
                        UiLanguage.EN_US: "Folder location",
                        UiLanguage.KO_KR: "폴더 위치",
                    }[translation_service().language]
                )
        for line_edit in self.findChildren(QLineEdit):
            if line_edit.objectName() == "fileNameEdit":
                line_edit.setAccessibleName(ui_text("File name"))
                line_edit.setAccessibleDescription(ui_text("File name"))
        for view in self.findChildren(QAbstractItemView):
            if view.accessibleName():
                view.setAccessibleName(
                    _localized_text(view.accessibleName())
                )
            if view.accessibleDescription():
                view.setAccessibleDescription(
                    _localized_text(view.accessibleDescription())
                )
            if view.objectName() == "sidebar":
                view.setHorizontalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
            if view.objectName() != "sidebar" or view.model() is None:
                continue
            model = view.model()
            sidebar_urls = self.sidebarUrls()
            localized_labels: list[str] = []
            for row in range(model.rowCount()):
                index = model.index(row, 0)
                visible = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "")
                source = self._sidebar_semantic_source(
                    row,
                    visible,
                    sidebar_urls,
                )
                localized = (
                    ui_text(source)
                    if source in {"Computer", "User folder"}
                    else _localized_text(source)
                )
                localized_labels.append(localized)
                for role in (
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole,
                    Qt.ItemDataRole.AccessibleDescriptionRole,
                ):
                    model.setData(index, localized, role)
            icon_width = max(24, view.iconSize().width())
            text_width = max(
                (
                    view.fontMetrics().horizontalAdvance(label)
                    for label in localized_labels
                ),
                default=0,
            )
            view.setMinimumWidth(text_width + icon_width + 34)
            view.setSizePolicy(
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Expanding,
            )
            view.setAccessibleName(ui_text("Sidebar"))
            view.setAccessibleDescription(
                ui_text("List of places and bookmarks")
            )
        tree = self.findChild(QTreeView, "treeView")
        if tree is not None:
            for column, width in enumerate((240, 105, 120, 155)):
                tree.setColumnWidth(column, width)
        if not self.accessibleName():
            self.setAccessibleName(self.windowTitle() or ui_text("Select"))

    @staticmethod
    def _sidebar_semantic_source(
        row: int,
        visible: str,
        sidebar_urls: Sequence[object],
    ) -> str:
        """Name built-in places by meaning while preserving their actual URLs."""
        if row >= len(sidebar_urls):
            return visible
        url = sidebar_urls[row]
        local_file_getter = getattr(url, "toLocalFile", None)
        local_file = (
            str(local_file_getter())
            if callable(local_file_getter)
            else ""
        )
        if not local_file:
            return "Computer"
        path = Path(local_file)
        if path.parent.name.casefold() == "users":
            return "User folder"
        return visible

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
        initial = directory or str(
            ApplicationPathsService.production().documents_root
        )
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
    """QMessageBox whose standard buttons follow the HMS locale."""

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
        self.setWindowTitle(ui_text(self.windowTitle()))
        self.setText(ui_text(self.text()))
        self.setInformativeText(ui_text(self.informativeText()))
        for standard, source in _MESSAGE_BUTTON_TEXT.items():
            button = self.button(standard)
            if button is None:
                continue
            text = _localized_text(source)
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
        box.setWindowTitle(ui_text(title))
        box.setText(ui_text(text))
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
