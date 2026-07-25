"""Production Qt widgets for Stage 8A.4.2 geometry transfer workflows."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.project.geometry_transfer import (
    CamProjectTargetInspection,
    GeometryApplyChoice,
    GeometryTransferRequest,
    IncomingGeometryPreview,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.localized_dialogs import QFileDialog
from hms_cadcam.ui.i18n import (
    format_geometry_update_message,
    translation_service,
)
from hms_cadcam.ui.localization import localize_widget_tree, ui_text


def _short_identity(value: UUID | str) -> str:
    text = str(value)
    return f"{text[:8]}…{text[-4:]}"


def _localized_unknown(value: object) -> str:
    text = str(value)
    return "Không xác định" if text.strip().casefold() == "unknown" else text


class CamProjectTargetDialog(QDialog):
    """Select and validate one real CAM project root before publishing."""

    def __init__(
        self,
        service: ProjectService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._inspection: CamProjectTargetInspection | None = None
        self.setObjectName("GeometryTransferTargetDialog")
        self.setWindowTitle("Chọn dự án CAM")
        self.setModal(True)
        self.resize(680, 330)

        self.project_path_edit = QLineEdit()
        self.project_path_edit.setObjectName("GeometryTargetPathEdit")
        self.project_path_edit.setAccessibleName("Thư mục dự án CAM")
        self.project_path_edit.setPlaceholderText(
            "Chọn thư mục gốc của dự án CAM"
        )
        browse = QPushButton("Duyệt…")
        browse.setObjectName("GeometryTargetBrowseButton")
        browse.clicked.connect(self._browse)
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.addWidget(self.project_path_edit, 1)
        path_layout.addWidget(browse)

        self.project_name_label = QLabel("—")
        self.project_id_label = QLabel("—")
        self.workspace_version_label = QLabel("—")
        self.full_path_label = QLabel("—")
        self.full_path_label.setWordWrap(True)
        self.validation_label = QLabel("Hãy chọn thư mục dự án CAM.")
        self.validation_label.setObjectName("GeometryTargetValidationLabel")
        self.validation_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Thư mục dự án CAM:", path_row)
        form.addRow("Tên dự án phát hiện được:", self.project_name_label)
        form.addRow("Mã dự án:", self.project_id_label)
        form.addRow(
            "Phiên bản không gian làm việc:",
            self.workspace_version_label,
        )
        form.addRow("Đường dẫn đầy đủ:", self.full_path_label)
        form.addRow("Trạng thái hợp lệ:", self.validation_label)

        self.send_button = QPushButton("Nạp 3D")
        self.send_button.setObjectName("GeometryTransferSendButton")
        self.send_button.setDefault(True)
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self.accept)
        cancel = QPushButton("Hủy")
        cancel.setObjectName("GeometryTransferCancelButton")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.send_button)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(buttons)
        self.project_path_edit.editingFinished.connect(self.validate_target)

    @property
    def project_root(self) -> Path | None:
        if self._inspection is None or not self._inspection.valid:
            return None
        return self._inspection.root_path

    @property
    def inspection(self) -> CamProjectTargetInspection | None:
        return self._inspection

    def set_project_root(self, path: Path) -> None:
        """Set a path and render the same validation used by the command."""
        self.project_path_edit.setText(str(path))
        self.validate_target()

    def validate_target(self) -> None:
        text = self.project_path_edit.text().strip()
        if not text:
            self._inspection = None
            self._render_inspection(None)
            return
        self._inspection = self._service.inspect_geometry_transfer_target(
            Path(text)
        )
        self._render_inspection(self._inspection)

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Chọn dự án CAM",
            self.project_path_edit.text(),
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.set_project_root(Path(selected))

    def _render_inspection(
        self,
        inspection: CamProjectTargetInspection | None,
    ) -> None:
        if inspection is None:
            self.project_name_label.setText("—")
            self.project_id_label.setText("—")
            self.workspace_version_label.setText("—")
            self.full_path_label.setText("—")
            self.validation_label.setText("Hãy chọn thư mục dự án CAM.")
            self.validation_label.setProperty("valid", False)
            self.send_button.setEnabled(False)
            return
        self.project_name_label.setText(inspection.project_name or "—")
        self.project_id_label.setText(
            "—" if inspection.project_id is None else str(inspection.project_id)
        )
        self.workspace_version_label.setText(
            "—"
            if inspection.workspace_version is None
            else str(inspection.workspace_version)
        )
        self.full_path_label.setText(str(inspection.root_path))
        self.validation_label.setText(
            f"{inspection.status_text}. {inspection.reason}"
            if inspection.valid
            else f"Dự án CAM không hợp lệ: {inspection.reason}"
        )
        self.validation_label.setProperty("valid", inspection.valid)
        self.validation_label.style().unpolish(self.validation_label)
        self.validation_label.style().polish(self.validation_label)
        self.send_button.setEnabled(inspection.valid)


class IncomingGeometryNotificationBar(QFrame):
    """Non-modal, non-focus-stealing notification surface."""

    view_requested = Signal(object)
    apply_requested = Signal(object)
    defer_requested = Signal(object)
    reject_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("IncomingGeometryNotificationBar")
        self.setAccessibleName("Thông báo dữ liệu 3D mới")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._requests: tuple[GeometryTransferRequest, ...] = ()
        self.message_label = QLabel("Không có dữ liệu 3D mới.")
        self.message_label.setObjectName("IncomingGeometryMessage")
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.view_button = self._button("Xem thay đổi", "View")
        self.apply_button = self._button("Cập nhật", "Apply")
        self.defer_button = self._button("Để sau", "Defer")
        self.reject_button = self._button("Bỏ qua", "Reject")
        self.view_button.clicked.connect(
            lambda: self._emit_current(self.view_requested)
        )
        self.apply_button.clicked.connect(
            lambda: self._emit_current(self.apply_requested)
        )
        self.defer_button.clicked.connect(
            lambda: self._emit_current(self.defer_requested)
        )
        self.reject_button.clicked.connect(
            lambda: self._emit_current(self.reject_requested)
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self.message_label, 1)
        for button in (
            self.view_button,
            self.apply_button,
            self.defer_button,
            self.reject_button,
        ):
            layout.addWidget(button)
        self.set_requests(())
        localize_widget_tree(self)
        translation_service().language_changed.connect(self.retranslate_ui)

    @property
    def current_request(self) -> GeometryTransferRequest | None:
        return None if not self._requests else self._requests[0]

    def set_requests(
        self,
        requests: tuple[GeometryTransferRequest, ...],
    ) -> None:
        self._requests = requests
        count = len(requests)
        if requests:
            request = requests[0]
            self.message_label.setText(
                format_geometry_update_message(
                    count,
                    request.source_display_name,
                )
            )
            self.message_label.setProperty(
                "localeMessageSource",
                request.source_display_name,
            )
        else:
            self.message_label.setText(format_geometry_update_message(0, ""))
            self.message_label.setProperty("localeMessageSource", "")
        self.message_label.setProperty("localeMessageCount", count)
        for button in (
            self.view_button,
            self.apply_button,
            self.defer_button,
            self.reject_button,
        ):
            button.setEnabled(bool(requests))

    def retranslate_ui(self, _language: object = None) -> None:
        """Refresh presentation without changing or acknowledging requests."""
        localize_widget_tree(self)
        self.set_requests(self._requests)
        localize_widget_tree(self)

    def _button(self, text: str, suffix: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(f"IncomingGeometry{suffix}Button")
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _emit_current(self, signal: Signal) -> None:
        request = self.current_request
        if request is not None:
            signal.emit(request.request_id)


class IncomingGeometryPanel(QWidget):
    """Non-modal preview and explicit apply-choice panel."""

    apply_requested = Signal(object, object, object)
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("IncomingGeometryChangePanel")
        self.setAccessibleName("Xem thay đổi dữ liệu 3D")
        self._preview: IncomingGeometryPreview | None = None
        self.title_label = QLabel("Xem thay đổi dữ liệu 3D")
        self.title_label.setObjectName("IncomingGeometryPanelTitle")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.details_label = QLabel("Chưa chọn yêu cầu.")
        self.details_label.setObjectName("IncomingGeometryDetails")
        self.details_label.setWordWrap(True)
        self.details_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.current_assets_label = QLabel("Chưa có dữ liệu.")
        self.current_assets_label.setWordWrap(True)
        self.new_geometry_label = QLabel("Chưa có dữ liệu.")
        self.new_geometry_label.setWordWrap(True)
        self.match_label = QLabel("Chưa có dữ liệu.")
        self.match_label.setWordWrap(True)
        self.impact_label = QLabel("Chưa có dữ liệu.")
        self.impact_label.setWordWrap(True)
        self.warning_label = QLabel(
            "Không tự tính toán, mô phỏng hoặc xử lý hậu kỳ."
        )
        self.warning_label.setObjectName("IncomingGeometrySafetyWarning")
        self.warning_label.setWordWrap(True)

        self.choice_combo = QComboBox()
        self.choice_combo.setObjectName("IncomingGeometryApplyChoice")
        self.choice_combo.setAccessibleName("Cách cập nhật hình học")
        self.choice_combo.addItem("Chọn cách cập nhật…", None)
        self.choice_combo.addItem(
            "Thêm làm mô hình mới",
            GeometryApplyChoice.ADD_NEW,
        )
        self.choice_combo.addItem(
            "Thay thế mô hình hiện tại",
            GeometryApplyChoice.REPLACE_EXISTING,
        )
        self.choice_combo.addItem(
            "Cập nhật phiên bản mô hình tương ứng",
            GeometryApplyChoice.UPDATE_MATCHING,
        )
        self.target_combo = QComboBox()
        self.target_combo.setObjectName("IncomingGeometryTargetAsset")
        self.target_combo.setAccessibleName("Mô hình hiện tại cần thay thế")
        self.target_combo.addItem("Chọn mô hình đích…", None)
        self.apply_button = QPushButton("Cập nhật")
        self.apply_button.setObjectName("IncomingGeometryConfirmApplyButton")
        self.apply_button.setEnabled(False)
        cancel = QPushButton("Hủy")
        cancel.setObjectName("IncomingGeometryPanelCancelButton")
        self.choice_combo.currentIndexChanged.connect(self._update_apply_state)
        self.target_combo.currentIndexChanged.connect(self._update_apply_state)
        self.apply_button.clicked.connect(self._emit_apply)
        cancel.clicked.connect(self.cancel_requested)

        form = QFormLayout()
        form.addRow("Tài liệu nguồn:", self.details_label)
        form.addRow("Hình học hiện tại:", self.current_assets_label)
        form.addRow("Hình học mới:", self.new_geometry_label)
        form.addRow("Mô hình có khả năng tương ứng:", self.match_label)
        form.addRow("Nguyên công dự kiến cần cập nhật:", self.impact_label)
        form.addRow("Cảnh báo mô phỏng/hậu kỳ:", self.warning_label)
        form.addRow("Cách cập nhật:", self.choice_combo)
        form.addRow("Mô hình đích:", self.target_combo)
        body = QWidget()
        body.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(scroll, 1)
        layout.addLayout(buttons)

    @property
    def preview(self) -> IncomingGeometryPreview | None:
        return self._preview

    def set_preview(self, preview: IncomingGeometryPreview) -> None:
        """Render only localized display values from a validated preview."""
        self._preview = preview
        request = preview.request
        self.details_label.setText(
            "\n".join(
                (
                    request.source_display_name,
                    str(request.source_hms_path),
                    request.created_at_utc.astimezone().strftime(
                        "%d/%m/%Y %H:%M:%S"
                    ),
                    f"ID tài liệu: {_short_identity(request.source_document_id)}",
                    (
                        "Dấu nhận dạng hình học: "
                        f"{request.source_geometry_fingerprint[:12]}…"
                    ),
                )
            )
        )
        self.current_assets_label.setText(
            "Không có mô hình."
            if not preview.current_assets
            else "\n".join(
                (
                    f"{asset.display_name} — v{asset.geometry_version} — "
                    f"{_localized_unknown(asset.units)} — "
                    f"{_short_identity(asset.source_id)}"
                )
                for asset in preview.current_assets
            )
        )
        self.new_geometry_label.setText(
            (
                f"{request.geometry_representation.display_text}; "
                f"đơn vị {_localized_unknown(request.geometry_units)}; "
                f"{request.solid_count} khối, {request.face_count} mặt, "
                f"{request.edge_count} cạnh"
            )
        )
        self.match_label.setText(preview.update_matching_reason)
        self.impact_label.setText(
            "Không có nguyên công liên quan."
            if not preview.affected_operation_ids
            else "\n".join(preview.affected_operation_ids)
        )
        self.warning_label.setText(preview.simulation_post_warning)
        self.target_combo.clear()
        self.target_combo.addItem("Chọn mô hình đích…", None)
        for asset in preview.current_assets:
            self.target_combo.addItem(
                f"{asset.display_name} — v{asset.geometry_version}",
                asset.source_id,
            )
        update_index = self.choice_combo.findData(
            GeometryApplyChoice.UPDATE_MATCHING
        )
        model = self.choice_combo.model()
        item = model.item(update_index)
        if item is not None:
            item.setEnabled(preview.update_matching_allowed)
        self.choice_combo.setCurrentIndex(0)
        self.target_combo.setCurrentIndex(0)
        self._update_apply_state()

    def _update_apply_state(self) -> None:
        preview = self._preview
        choice = self._selected_choice()
        target = self._selected_target_id()
        replace_selected = choice is GeometryApplyChoice.REPLACE_EXISTING
        self.target_combo.setEnabled(replace_selected)
        allowed = (
            preview is not None
            and isinstance(choice, GeometryApplyChoice)
            and preview.request.geometry_representation.exact_for_cam
            and (not replace_selected or isinstance(target, UUID))
            and (
                choice is not GeometryApplyChoice.UPDATE_MATCHING
                or preview.update_matching_allowed
            )
        )
        self.apply_button.setEnabled(allowed)

    def _emit_apply(self) -> None:
        if self._preview is None or not self.apply_button.isEnabled():
            return
        choice = self._selected_choice()
        if choice is None:
            return
        self.apply_requested.emit(
            self._preview.request.request_id,
            choice,
            self._selected_target_id(),
        )

    def _selected_choice(self) -> GeometryApplyChoice | None:
        value = self.choice_combo.currentData()
        if value is None:
            return None
        try:
            return GeometryApplyChoice(value)
        except (TypeError, ValueError):
            return None

    def _selected_target_id(self) -> UUID | None:
        value = self.target_combo.currentData()
        if value is None:
            return None
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None
