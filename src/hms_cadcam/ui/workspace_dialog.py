"""Production widgets for CAM workspace creation and drag/drop feedback."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.project.path_policy import (
    normalize_cam_project_name,
    validate_parent_path,
)
from hms_cadcam.project.exceptions import UnsafeWorkspacePathError
from hms_cadcam.ui.localized_dialogs import QFileDialog


class CamProjectDialog(QDialog):
    """Collect and validate the explicit parent/name CAM workspace contract."""

    validation_changed = Signal(bool, str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Tạo dự án CAM",
        default_name: str = "",
        default_parent: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CamProjectDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(620, 390)
        self.resize(680, 420)
        self.setAccessibleName(title)
        self.setAccessibleDescription(
            "Nhập tên dự án, chọn thư mục cha và xác nhận đường dẫn vật lý."
        )

        title_label = QLabel(title)
        title_label.setObjectName("CamProjectDialogTitle")
        title_label.setStyleSheet("font-size: 18px; font-weight: 600;")

        self.project_name_edit = QLineEdit(default_name)
        self.project_name_edit.setObjectName("CamProjectNameEdit")
        self.project_name_edit.setAccessibleName("Tên dự án")
        self.project_name_edit.setAccessibleDescription(
            "Tên hiển thị tiếng Việt được giữ nguyên trong manifest."
        )

        self.parent_path_edit = QLineEdit(
            "" if default_parent is None else str(default_parent)
        )
        self.parent_path_edit.setObjectName("CamProjectParentEdit")
        self.parent_path_edit.setReadOnly(True)
        self.parent_path_edit.setAccessibleName("Thư mục cha")
        self.parent_path_edit.setAccessibleDescription(
            "Đường dẫn cha phải không có dấu cách hoặc ký tự không an toàn."
        )
        browse_button = QPushButton("Chọn…")
        browse_button.setObjectName("CamProjectBrowseButton")
        browse_button.setAccessibleName("Chọn thư mục cha")
        browse_button.clicked.connect(self._browse_parent)
        parent_row = QWidget()
        parent_layout = QHBoxLayout(parent_row)
        parent_layout.setContentsMargins(0, 0, 0, 0)
        parent_layout.addWidget(self.parent_path_edit, 1)
        parent_layout.addWidget(browse_button)

        self.physical_name_label = QLabel("—")
        self.physical_name_label.setObjectName("CamProjectPhysicalName")
        self.physical_name_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.physical_name_label.setAccessibleName("Tên thư mục sẽ tạo")

        self.full_path_label = QLabel("—")
        self.full_path_label.setObjectName("CamProjectFullPath")
        self.full_path_label.setWordWrap(True)
        self.full_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.full_path_label.setAccessibleName("Đường dẫn đầy đủ")

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("Tên dự án:", self.project_name_edit)
        form.addRow("Thư mục cha:", parent_row)
        form.addRow("Tên thư mục sẽ tạo:", self.physical_name_label)
        form.addRow("Đường dẫn đầy đủ:", self.full_path_label)

        self.validation_label = QLabel("Hãy nhập tên dự án và chọn thư mục cha.")
        self.validation_label.setObjectName("CamProjectValidation")
        self.validation_label.setWordWrap(True)
        self.validation_label.setAccessibleName("Trạng thái kiểm tra")
        self.validation_label.setAccessibleDescription(
            "Thông báo lý do đường dẫn hợp lệ hoặc bị chặn."
        )

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        create_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        create_button.setText("Tạo dự án")
        create_button.setObjectName("CamProjectCreateButton")
        create_button.setAccessibleName("Tạo dự án CAM")
        cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        cancel_button.setText("Hủy")
        cancel_button.setAccessibleName("Hủy tạo dự án CAM")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)
        layout.addWidget(title_label)
        layout.addLayout(form)
        layout.addWidget(self.validation_label)
        layout.addStretch(1)
        layout.addWidget(self.buttons)

        self.project_name_edit.textChanged.connect(self._validate)
        self.parent_path_edit.textChanged.connect(self._validate)
        self._validate()

    @property
    def project_name(self) -> str:
        """Return the unchanged display name entered by the user."""
        return self.project_name_edit.text().strip()

    @property
    def parent_directory(self) -> Path | None:
        """Return the explicit parent path, if one has been selected."""
        text = self.parent_path_edit.text().strip()
        return None if not text else Path(text)

    @property
    def physical_name(self) -> str | None:
        """Return the current deterministic physical-name preview."""
        try:
            return normalize_cam_project_name(self.project_name)
        except UnsafeWorkspacePathError:
            return None

    def set_parent_directory(self, path: Path) -> None:
        """Set an explicit parent path for production use or review harnesses."""
        self.parent_path_edit.setText(str(path))

    def _browse_parent(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Chọn thư mục cha cho dự án CAM",
            self.parent_path_edit.text(),
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.parent_path_edit.setText(selected)

    def _validate(self) -> None:
        valid = False
        reason = "Hãy nhập tên dự án và chọn thư mục cha."
        physical = self.physical_name
        parent = self.parent_directory
        if physical is None:
            self.physical_name_label.setText("—")
            self.full_path_label.setText("—")
            reason = "Tên dự án không tạo được tên thư mục vật lý an toàn."
        elif parent is None:
            self.physical_name_label.setText(physical)
            self.full_path_label.setText("—")
        else:
            self.physical_name_label.setText(physical)
            self.full_path_label.setText(str(parent / physical))
            assessment = validate_parent_path(parent, physical)
            valid = assessment.valid
            reason = assessment.reason
        self.validation_label.setText(reason)
        self.validation_label.setProperty("valid", valid)
        self.validation_label.setStyleSheet(
            "color: #1f7a3d;" if valid else "color: #a33a2b;"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)
        self.validation_changed.emit(valid, reason)


class DropOpenOverlay(QFrame):
    """Non-interactive Vietnamese drop target shown only during file drag."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("DropOpenOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("Thả tệp để mở trong HMS")
        self.setAccessibleDescription(
            "Vùng thả chỉ xuất hiện khi kéo một tệp CAD hoặc HMS được hỗ trợ."
        )
        self.setStyleSheet(
            "#DropOpenOverlay {"
            "background: rgba(24, 55, 86, 205);"
            "border: 3px dashed #8ec5ff;"
            "border-radius: 14px;"
            "}"
            "#DropOpenOverlay QLabel {"
            "color: white; font-size: 22px; font-weight: 600;"
            "}"
        )
        label = QLabel("Thả tệp để mở trong HMS", self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)
        self.hide()
