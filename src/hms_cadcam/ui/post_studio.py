"""Compact Vietnamese-first Post Processor Studio panel."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from hms_cadcam.cam.post_studio import PostLifecycleStatus, PostStudioService
from hms_cadcam.ui.i18n import translation_service


@dataclass(frozen=True, slots=True)
class PostStudioViewState:
    selected_revision_id: str | None = None
    operation_state: str = "NOT_RUN"
    elapsed_seconds: float = 0.0


class PostProcessorStudioPanel(QWidget):
    """Read/write studio presentation; all committed revisions remain immutable."""

    state_changed = Signal(object)
    message = Signal(str)
    prepare_activation_requested = Signal(str)

    def __init__(self, service: PostStudioService | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service or PostStudioService()
        self._state = PostStudioViewState()
        self.setObjectName("PostProcessorStudioPanel")
        self._build()
        translation_service().language_changed.connect(self._retranslate)

    @property
    def state(self) -> PostStudioViewState:
        return self._state

    def set_service(self, service: PostStudioService) -> None:
        self._service = service
        self.refresh()

    def refresh(self) -> None:
        selected = self._state.selected_revision_id
        self.library.clear()
        for definition in self._service.definitions():
            for revision in self._service.revisions_for(definition.post_id):
                self.library.addItem(f"{definition.display_name}  /  {revision.revision_id}")
                self.library.item(self.library.count() - 1).setData(32, revision.revision_id)
        if selected:
            for index in range(self.library.count()):
                if self.library.item(index).data(32) == selected:
                    self.library.setCurrentRow(index); break
        self._show_selected()

    def _build(self) -> None:
        layout = QVBoxLayout(self); layout.setContentsMargins(6, 6, 6, 6); layout.setSpacing(5)
        heading = QHBoxLayout(); self.title = QLabel(); self.title.setObjectName("PostStudioTitle")
        self.status = QLabel("NOT_RUN"); self.status.setObjectName("PostStudioStatus")
        heading.addWidget(self.title); heading.addStretch(1); heading.addWidget(self.status); layout.addLayout(heading)
        actions = QHBoxLayout(); self.new_button = QPushButton(); self.import_button = QPushButton(); self.clone_button = QPushButton(); self.validate_button = QPushButton(); self.diff_button = QPushButton(); self.approve_button = QPushButton(); self.prepare_activation_button = QPushButton("Chuẩn bị kích hoạt"); self.activate_button = QPushButton(); self.rollback_button = QPushButton()
        for button in (self.new_button, self.import_button, self.clone_button, self.validate_button, self.diff_button, self.approve_button, self.prepare_activation_button, self.activate_button, self.rollback_button): actions.addWidget(button)
        actions.addStretch(1); layout.addLayout(actions)
        splitter = QSplitter(); self.library = QListWidget(); self.library.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection); self.library.currentRowChanged.connect(lambda _row: self._show_selected())
        center = QWidget(); center_layout = QVBoxLayout(center); center_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(); self.search.setPlaceholderText("Tìm Post / phiên bản")
        self.source_editor = QPlainTextEdit(); self.source_editor.setReadOnly(True); self.source_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap); self.source_editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.diff = QPlainTextEdit(); self.diff.setReadOnly(True); self.diff.setMaximumBlockCount(20000); self.diff.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        center_layout.addWidget(self.search); center_layout.addWidget(self.source_editor, 3); center_layout.addWidget(self.diff, 2)
        self.properties = QTableWidget(0, 2); self.properties.setHorizontalHeaderLabels(["Thuộc tính", "Giá trị"]); self.properties.verticalHeader().hide(); self.properties.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        splitter.addWidget(self.library); splitter.addWidget(center); splitter.addWidget(self.properties); splitter.setSizes([240, 550, 260]); layout.addWidget(splitter, 1)
        self.new_button.clicked.connect(lambda: self.message.emit("Tạo Post yêu cầu siêu dữ liệu và nguồn hợp lệ."))
        self.import_button.clicked.connect(lambda: self.message.emit("Nhập Post chỉ tạo phiên bản bất biến trong vùng làm việc cô lập."))
        self.validate_button.clicked.connect(self._validate_selected); self.diff_button.clicked.connect(self._show_selected)
        self.prepare_activation_button.clicked.connect(self._request_activation_preparation)
        self.activate_button.setEnabled(False)
        self.activate_button.setToolTip("Giai đoạn 2 chưa được phép kích hoạt trong R239.")
        self.rollback_button.clicked.connect(lambda: self.message.emit("Rollback yêu cầu một activation record đã được xác minh."))
        self._retranslate()

    def _retranslate(self, *_unused: object) -> None:
        tr = translation_service().translate_key
        self.title.setText(tr("post_studio.title")); self.new_button.setText(tr("post_studio.new")); self.import_button.setText(tr("post_studio.import")); self.clone_button.setText(tr("post_studio.clone")); self.validate_button.setText(tr("post_studio.validate")); self.diff_button.setText(tr("post_studio.diff")); self.approve_button.setText(tr("post_studio.approve")); self.activate_button.setText(tr("post_studio.activate")); self.rollback_button.setText(tr("post_studio.rollback"))

    def _show_selected(self) -> None:
        item = self.library.currentItem()
        revision_id = item.data(32) if item else None
        self._state = PostStudioViewState(revision_id, self._state.operation_state, self._state.elapsed_seconds)
        self.properties.setRowCount(0); self.source_editor.clear(); self.diff.clear()
        if not revision_id:
            self.state_changed.emit(self._state); return
        revision = self._service.revision(revision_id); source = self._service.source_bytes(revision_id)
        self.source_editor.setPlainText(source.decode(revision.source_encoding, errors="replace"))
        diff = self._service.source_diff(revision_id); self.diff.setPlainText("\n".join(diff.text_lines) or "(Không có parent diff)")
        entries = (("Phiên bản", revision.revision_id), ("SHA-256", revision.source_sha256), ("Trạng thái", self._service.lifecycle_status(revision_id).value), ("Mã hóa", revision.source_encoding), ("Kết thúc dòng", revision.line_ending), ("Triển khai", "NOT_ACTIVE_GLOBALLY"), ("Giai đoạn 1", "CHUẨN BỊ KÍCH HOẠT — Sẵn sàng chờ phê duyệt"), ("Giai đoạn 2", "CHƯA ĐƯỢC PHÉP KÍCH HOẠT"), ("Rollback", "Chỉ Post managed có activation record đã xác minh"), ("Thay đổi ngoài HMS", "Kiểm tra lại trước mọi thao tác; không ghi đè bytes ngoài HMS"))
        for key, value in entries:
            row = self.properties.rowCount(); self.properties.insertRow(row); self.properties.setItem(row, 0, QTableWidgetItem(key)); self.properties.setItem(row, 1, QTableWidgetItem(value))
        self.state_changed.emit(self._state)

    def _validate_selected(self) -> None:
        revision_id = self._state.selected_revision_id
        if not revision_id:
            return
        result = self._service.validate(revision_id, validated_at="2026-08-12T12:00:00+07:00")
        self._state = PostStudioViewState(revision_id, result.state.value, 0.0); self.status.setText(result.state.value); self._show_selected()

    def _request_activation_preparation(self) -> None:
        revision_id = self._state.selected_revision_id
        if not revision_id:
            self.message.emit("Hãy chọn một phiên bản Post đã được duyệt trước khi chuẩn bị kích hoạt.")
            return
        self.status.setText("Đang chuẩn bị kích hoạt...")
        self.prepare_activation_requested.emit(revision_id)

    def set_production_progress(self, state: str) -> None:
        """Project background-service progress without running work in the widget."""

        labels = {
            "PREFLIGHT": "Đang kiểm tra Post hiện tại...",
            "HASHING": "Đang xác minh SHA...",
            "ROLLBACK": "Đang kiểm tra rollback...",
            "SANDBOX": "Đang chạy thử kích hoạt trong sandbox...",
            "PACKAGE": "Đang tạo gói kích hoạt...",
            "READY": "Sẵn sàng chờ phê duyệt kích hoạt",
            "FAILED": "Chuẩn bị kích hoạt thất bại",
        }
        self.status.setText(labels.get(state, "Đang kiểm tra..."))


__all__ = ["PostProcessorStudioPanel", "PostStudioViewState"]
