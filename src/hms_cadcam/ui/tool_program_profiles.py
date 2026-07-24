"""Vietnamese Tool editor widgets for optional program-specific profiles."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Callable, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    ContentFingerprint,
    ToolDefinition,
    ToolProfileDiffKind,
    ToolProfileFieldDescriptor,
    ToolProfileFieldType,
    ToolProfileListState,
    ToolProfileResolution,
    ToolProfileSaveMode,
    ToolProfileSavePreview,
    ToolProfileSchemaRegistry,
    ToolProgramProfile,
    ToolProgramProfileId,
    ToolStrategyProfileSchema,
    assess_tool_program_profile,
)


_STATUS_TEXT = {
    ToolProfileListState.NOT_CONFIGURED: "Chưa cấu hình",
    ToolProfileListState.CONFIGURED: "Đã cấu hình",
    ToolProfileListState.CUSTOMIZED: "Có tùy chỉnh",
    ToolProfileListState.INCOMPATIBLE: "Không tương thích",
    ToolProfileListState.NEEDS_REVIEW: "Cần xem lại",
    ToolProfileListState.DISABLED: "Đang tắt",
}
_DIFF_TEXT = {
    ToolProfileDiffKind.ADD: "Thêm mới",
    ToolProfileDiffKind.CHANGE: "Thay đổi",
    ToolProfileDiffKind.UNCHANGED: "Giữ nguyên",
    ToolProfileDiffKind.SKIPPED: "Bỏ qua",
    ToolProfileDiffKind.INVALID: "Không hợp lệ",
}
_ACTION_LABELS = (
    ("add", "Thêm cấu hình"),
    ("edit", "Chỉnh sửa"),
    ("copy", "Sao chép"),
    ("toggle", "Bật/Tắt"),
    ("reset", "Đặt lại"),
    ("delete", "Xóa"),
)
_SOURCE_TEXT = {
    "operation_override": "Nguyên công hiện tại",
    "tool_program_profile": "Cấu hình Tool theo chương trình",
    "tool_common_default": "Cấu hình cơ bản của Tool",
    "automatic_policy": "Chính sách tự động",
    "safe_default": "Giá trị an toàn mặc định",
    "program_template": "Chương trình mẫu",
}


def _family_text(tool: ToolDefinition) -> str:
    return {
        "ball_end_mill": "Dao cầu",
        "end_mill": "Dao phay ngón",
        "bull_nose_end_mill": "Dao bo góc",
        "custom": "Dao định hình",
        "drill": "Mũi khoan",
        "center_drill": "Mũi khoan tâm",
    }.get(tool.family.value, "Họ Tool kỹ thuật")


def _updated_text(value: datetime) -> str:
    return value.astimezone().strftime("%d/%m/%Y %H:%M")


class ToolProgramProfilesWidget(QWidget):
    """Collapsible optional-profile list; all mutations leave through signals."""

    action_requested = Signal(str, object)
    expanded_changed = Signal(bool)

    def __init__(
        self,
        registry: ToolProfileSchemaRegistry = DEFAULT_TOOL_PROFILE_REGISTRY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolProgramProfilesWidget")
        self.setAccessibleName("Cấu hình Tool theo chương trình")
        self.setAccessibleDescription(
            "Danh sách cấu hình tùy chọn; Tool vẫn hợp lệ khi danh sách trống."
        )
        self._registry = registry
        self._tool: ToolDefinition | None = None
        self._holder_fingerprint: ContentFingerprint | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(5)

        self.toggle = QToolButton()
        self.toggle.setObjectName("ToolProfilesCollapseToggle")
        self.toggle.setText("Cấu hình theo chương trình · Không bắt buộc")
        self.toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setAccessibleName(
            "Mở hoặc thu gọn cấu hình Tool theo chương trình"
        )
        self.toggle.toggled.connect(self.set_expanded)
        root.addWidget(self.toggle)

        self.body = QFrame()
        self.body.setObjectName("ToolProfilesBody")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(8, 4, 8, 8)
        body_layout.setSpacing(5)
        self.optional_note = QLabel(
            "Không bắt buộc. Khi chưa cấu hình, chương trình tiếp tục dùng "
            "chính sách tự động."
        )
        self.optional_note.setWordWrap(True)
        self.optional_note.setObjectName("ToolProfilesOptionalNote")
        self.optional_note.setAccessibleName("Trạng thái cấu hình tùy chọn")
        body_layout.addWidget(self.optional_note)

        self.tree = QTreeWidget()
        self.tree.setObjectName("ToolProfilesList")
        self.tree.setAccessibleName("Danh sách cấu hình theo chương trình")
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            ("Chương trình", "Trạng thái", "Số trường", "Cập nhật")
        )
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tree.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in (1, 2, 3):
            self.tree.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.tree.itemSelectionChanged.connect(self._update_actions)
        body_layout.addWidget(self.tree, 1)

        actions = QGridLayout()
        actions.setHorizontalSpacing(4)
        actions.setVerticalSpacing(4)
        self.action_buttons: dict[str, QPushButton] = {}
        for index, (action, label) in enumerate(_ACTION_LABELS):
            button = QPushButton(label)
            button.setObjectName(f"ToolProfileAction_{action}")
            button.setAccessibleName(f"{label} cấu hình Tool")
            button.clicked.connect(
                lambda _checked=False, key=action: self._request(key)
            )
            actions.addWidget(button, index // 3, index % 3)
            self.action_buttons[action] = button
        body_layout.addLayout(actions)

        self.save_current_button = QPushButton(
            "Lưu thiết lập này cho Tool và chương trình hiện tại"
        )
        self.save_current_button.setObjectName("ToolProfileSaveCurrentOperation")
        self.save_current_button.setAccessibleName(
            "Lưu thiết lập nguyên công hiện tại thành cấu hình Tool"
        )
        self.save_current_button.setAccessibleDescription(
            "Mở bản xem trước thay đổi; mặc định chỉ lưu các trường đã tùy chỉnh."
        )
        self.save_current_button.clicked.connect(
            lambda: self._request("save_current")
        )
        body_layout.addWidget(self.save_current_button)
        root.addWidget(self.body)
        self.body.setVisible(False)
        self._update_actions()

    @property
    def is_expanded(self) -> bool:
        return self.toggle.isChecked()

    @property
    def selected_profile_id(self) -> ToolProgramProfileId | None:
        item = self.tree.currentItem()
        value = (
            item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        )
        return value if isinstance(value, ToolProgramProfileId) else None

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.blockSignals(True)
        self.toggle.setChecked(expanded)
        self.toggle.blockSignals(False)
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.body.setVisible(expanded)
        self.expanded_changed.emit(expanded)

    def bind_tool(
        self,
        tool: ToolDefinition,
        *,
        holder_fingerprint: ContentFingerprint | None = None,
    ) -> None:
        """Render current immutable state; no profile is synthesized."""
        if not isinstance(tool, ToolDefinition):
            raise TypeError("Tool editor requires ToolDefinition")
        self._tool = tool
        self._holder_fingerprint = holder_fingerprint
        selected = self.selected_profile_id
        self.tree.clear()
        for profile in tool.program_profiles:
            schema = self._registry.schema(profile.strategy_id)
            compatibility = assess_tool_program_profile(
                profile,
                tool,
                self._registry,
                holder_fingerprint=holder_fingerprint,
            )
            item = QTreeWidgetItem(
                (
                    schema.display_name_vi,
                    _STATUS_TEXT[compatibility.state],
                    str(len(profile.values)),
                    _updated_text(profile.updated_at),
                )
            )
            item.setData(0, Qt.ItemDataRole.UserRole, profile.profile_id)
            item.setToolTip(1, compatibility.reason_vi)
            self.tree.addTopLevelItem(item)
            if selected == profile.profile_id:
                self.tree.setCurrentItem(item)
        if self.tree.topLevelItemCount() == 0:
            self.optional_note.setText(
                "Chưa cấu hình · Không bắt buộc. Tool vẫn dùng được và chương "
                "trình tiếp tục dùng chính sách tự động."
            )
        else:
            self.optional_note.setText(
                "Cấu hình theo chương trình là tùy chọn và chỉ lưu các trường "
                "được chương trình khai báo."
            )
        self._update_actions()

    def _request(self, action: str) -> None:
        profile_id = self.selected_profile_id
        self.action_requested.emit(action, profile_id)

    def _update_actions(self) -> None:
        selected = self.selected_profile_id is not None
        for action in ("edit", "copy", "toggle", "reset", "delete"):
            self.action_buttons[action].setEnabled(selected)
        self.action_buttons["add"].setEnabled(self._tool is not None)
        self.save_current_button.setEnabled(self._tool is not None)


class _ProfileFieldRow(QWidget):
    """One sparse opt-in field with a finite typed editor."""

    def __init__(
        self,
        descriptor: ToolProfileFieldDescriptor,
        value: object,
        active: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.descriptor = descriptor
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.enabled = QCheckBox("Dùng")
        self.enabled.setChecked(active)
        self.enabled.setAccessibleName(f"Dùng trường {descriptor.display_name_vi}")
        layout.addWidget(self.enabled)
        if descriptor.field_type is ToolProfileFieldType.NUMBER:
            editor = QDoubleSpinBox()
            editor.setDecimals(6)
            editor.setRange(
                descriptor.minimum
                if descriptor.minimum is not None
                else -1.0e12,
                descriptor.maximum
                if descriptor.maximum is not None
                else 1.0e12,
            )
            editor.setValue(float(value) if active else max(0.0, editor.minimum()))
            editor.setSuffix(f" {descriptor.unit}" if descriptor.unit else "")
            editor.setKeyboardTracking(False)
        elif descriptor.field_type is ToolProfileFieldType.ENUM:
            editor = QComboBox()
            for option in descriptor.enum_values:
                editor.addItem(descriptor.display_value(option), option)
            if active:
                index = editor.findData(value)
                editor.setCurrentIndex(max(0, index))
        else:
            editor = QCheckBox("Bật")
            editor.setChecked(bool(value) if active else False)
        editor.setAccessibleName(descriptor.display_name_vi)
        editor.setAccessibleDescription(
            f"Trường tùy chọn do chương trình khai báo"
            + (f", đơn vị {descriptor.unit}" if descriptor.unit else "")
        )
        editor.setEnabled(active)
        self.enabled.toggled.connect(editor.setEnabled)
        self.editor = editor
        layout.addWidget(editor, 1)

    def canonical_value(self) -> object:
        if not self.enabled.isChecked():
            raise KeyError(self.descriptor.field_id)
        if isinstance(self.editor, QDoubleSpinBox):
            raw: object = self.editor.value()
        elif isinstance(self.editor, QComboBox):
            raw = self.editor.currentData()
        else:
            raw = cast(QCheckBox, self.editor).isChecked()
        return self.descriptor.normalize(raw)


class ToolProfileEditorDialog(QDialog):
    """Compact dynamic profile editor with Basic before scrollable Advanced."""

    def __init__(
        self,
        schema: ToolStrategyProfileSchema,
        profile: ToolProgramProfile | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolProfileEditorDialog")
        self.setWindowTitle(
            "Thêm cấu hình theo chương trình"
            if profile is None
            else "Chỉnh sửa cấu hình theo chương trình"
        )
        self.setAccessibleName(self.windowTitle())
        self.setAccessibleDescription(
            "Chỉ hiển thị các trường do chương trình hiện tại khai báo."
        )
        self.setModal(True)
        self._schema = schema
        self._rows: dict[str, _ProfileFieldRow] = {}
        values = profile.sparse_mapping if profile is not None else {}
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(7)

        title = QLabel(schema.display_name_vi)
        title.setObjectName("ToolProfileStrategyTitle")
        title.setWordWrap(True)
        root.addWidget(title)
        note = QLabel(
            "Không bắt buộc · chỉ các trường được bật mới được lưu."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        form = QFormLayout()
        self.name_edit = QLineEdit(
            profile.display_name if profile is not None else schema.display_name_vi
        )
        self.name_edit.setAccessibleName("Tên cấu hình")
        form.addRow("Tên cấu hình", self.name_edit)
        self.enabled_check = QCheckBox("Bật cấu hình này")
        self.enabled_check.setChecked(profile.enabled if profile is not None else True)
        form.addRow("Trạng thái", self.enabled_check)
        root.addLayout(form)

        basic = QGroupBox("Cơ bản")
        basic_layout = QFormLayout(basic)
        advanced = QWidget()
        advanced_layout = QFormLayout(advanced)
        for descriptor in schema.fields:
            row = _ProfileFieldRow(
                descriptor,
                values.get(descriptor.field_id),
                descriptor.field_id in values,
            )
            self._rows[descriptor.field_id] = row
            target = advanced_layout if descriptor.advanced else basic_layout
            target.addRow(descriptor.display_name_vi, row)
        root.addWidget(basic)

        self.advanced_group = QGroupBox("Nâng cao")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        advanced_group_layout = QVBoxLayout(self.advanced_group)
        scroll = QScrollArea()
        scroll.setObjectName("ToolProfileAdvancedScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setWidget(advanced)
        advanced_group_layout.addWidget(scroll)
        self.advanced_group.setVisible(advanced_layout.rowCount() > 0)
        root.addWidget(self.advanced_group, 1)

        self.error_label = QLabel()
        self.error_label.setObjectName("ToolProfileValidationMessage")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)
        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Hủy")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.save_button = QPushButton("Lưu cấu hình")
        self.save_button.setObjectName("PrimaryPanelAction")
        self.save_button.clicked.connect(self._validate_and_accept)
        footer.addWidget(self.save_button)
        root.addLayout(footer)
        self.setMinimumSize(480, 520)
        self.resize(540, 640)

    @property
    def display_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def profile_enabled(self) -> bool:
        return self.enabled_check.isChecked()

    def profile_values(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for field_id, row in self._rows.items():
            if not row.enabled.isChecked():
                continue
            values[field_id] = row.canonical_value()
        self._schema.normalize_values(values)
        return values

    def _validate_and_accept(self) -> None:
        try:
            if not self.display_name:
                raise ValueError("Tên cấu hình là bắt buộc.")
            self.profile_values()
        except (KeyError, TypeError, ValueError) as error:
            self.error_label.setText(str(error))
            self.error_label.setVisible(True)
            return
        self.error_label.setVisible(False)
        self.accept()


class ToolProfileSavePreviewDialog(QDialog):
    """Fixed-footer preview for save-from-operation confirmation."""

    confirmed = Signal(object)

    def __init__(
        self,
        preview: ToolProfileSavePreview,
        parent: QWidget | None = None,
        *,
        preview_provider: Callable[
            [ToolProfileSaveMode],
            ToolProfileSavePreview,
        ]
        | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolProfileSavePreviewDialog")
        self.setWindowTitle("Xem trước thay đổi cấu hình Tool")
        self.setAccessibleName("Xem trước lưu cấu hình Tool từ nguyên công")
        self.setAccessibleDescription(
            "Hiển thị trường thêm mới, thay đổi, giữ nguyên, bỏ qua và không hợp lệ."
        )
        self.setModal(True)
        self._preview = preview
        self._preview_provider = preview_provider
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(7)
        title = QLabel(
            "Lưu thiết lập này cho Tool và chương trình hiện tại"
        )
        title.setObjectName("ToolProfilePreviewTitle")
        title.setWordWrap(True)
        root.addWidget(title)
        note = QLabel(
            "Cấu hình Tool không phải chứng nhận an toàn và không lưu dữ liệu "
            "tính toán, "
            "kết quả mô phỏng hoặc G-code."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.only_overrides = QRadioButton(
            "Chỉ lưu các trường đã tùy chỉnh"
        )
        self.only_overrides.setChecked(
            preview.mode is ToolProfileSaveMode.OVERRIDES_ONLY
        )
        self.only_overrides.setAccessibleDescription("Lựa chọn mặc định an toàn")
        self.all_effective = QRadioButton(
            "Lưu toàn bộ giá trị hiệu lực được phép"
        )
        self.all_effective.setChecked(
            preview.mode is ToolProfileSaveMode.ALL_EFFECTIVE
        )
        root.addWidget(self.only_overrides)
        root.addWidget(self.all_effective)

        self.table = QTableWidget(len(preview.entries), 4)
        self.table.setObjectName("ToolProfileDiffTable")
        self.table.setAccessibleName("Bảng xem trước thay đổi cấu hình Tool")
        self.table.setHorizontalHeaderLabels(
            ("Trường", "Phân loại", "Đang lưu", "Sẽ lưu")
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        root.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Hủy")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.confirm_button = QPushButton("Xác nhận lưu")
        self.confirm_button.setObjectName("PrimaryPanelAction")
        self.confirm_button.clicked.connect(self._confirm)
        footer.addWidget(self.confirm_button)
        root.addLayout(footer)
        self.only_overrides.toggled.connect(self._mode_changed)
        self.all_effective.toggled.connect(self._mode_changed)
        self._render_preview(preview)
        self.setMinimumSize(560, 460)
        self.resize(660, 560)

    @property
    def selected_mode(self) -> ToolProfileSaveMode:
        return (
            ToolProfileSaveMode.OVERRIDES_ONLY
            if self.only_overrides.isChecked()
            else ToolProfileSaveMode.ALL_EFFECTIVE
        )

    def _confirm(self) -> None:
        self.confirmed.emit(self.selected_mode)
        self.accept()

    def _mode_changed(self, checked: bool) -> None:
        if not checked or self._preview_provider is None:
            return
        preview = self._preview_provider(self.selected_mode)
        if (
            not isinstance(preview, ToolProfileSavePreview)
            or preview.mode is not self.selected_mode
        ):
            raise ValueError("Bản xem trước cấu hình Tool không hợp lệ.")
        self._render_preview(preview)

    def _render_preview(self, preview: ToolProfileSavePreview) -> None:
        self._preview = preview
        schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(preview.strategy_id)
        descriptors = {item.field_id: item for item in schema.fields}
        self.table.setRowCount(len(preview.entries))
        self.table.clearContents()
        for row, entry in enumerate(preview.entries):
            descriptor = descriptors[entry.field_id]
            values = (
                entry.display_name_vi,
                _DIFF_TEXT[entry.kind],
                (
                    "—"
                    if entry.previous_value is None
                    else descriptor.display_value(entry.previous_value)
                ),
                (
                    "—"
                    if entry.candidate_value is None
                    else descriptor.display_value(entry.candidate_value)
                ),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(entry.reason_vi)
                self.table.setItem(row, column, item)
        self.confirm_button.setEnabled(
            not any(
                item.kind is ToolProfileDiffKind.INVALID
                for item in preview.entries
            )
        )


class ToolProfileProvenanceWidget(QWidget):
    """Read-only effective-value table used by Tool/Function Editor surfaces."""

    def __init__(
        self,
        resolution: ToolProfileResolution,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolProfileProvenanceWidget")
        self.setAccessibleName("Nguồn giá trị cấu hình Tool")
        self.setAccessibleDescription(
            "Giá trị hiệu lực, nguồn tiếng Việt và trạng thái kiểm tra."
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        title = QLabel("Nguồn giá trị")
        title.setObjectName("ToolProfileProvenanceTitle")
        root.addWidget(title)
        note = QLabel(resolution.profile_compatibility.reason_vi)
        note.setWordWrap(True)
        root.addWidget(note)
        self.table = QTableWidget(len(resolution.values), 4)
        self.table.setObjectName("ToolProfileProvenanceTable")
        self.table.setAccessibleName("Bảng nguồn giá trị hiệu lực")
        self.table.setHorizontalHeaderLabels(
            ("Tham số", "Giá trị hiệu lực", "Nguồn", "Trạng thái")
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(resolution.strategy_id)
        fields = {item.field_id: item for item in schema.fields}
        for row, effective in enumerate(resolution.values):
            values = (
                fields[effective.field_id].display_name_vi,
                effective.display_value,
                _SOURCE_TEXT[effective.source.value],
                {
                    "valid": "Hợp lệ",
                    "fallback": "Dùng nguồn thấp hơn",
                    "blocked": "Bị chặn",
                }[effective.validation_status.value],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(effective.reason_vi)
                self.table.setItem(row, column, item)
        root.addWidget(self.table, 1)


class ToolEditorDialog(QDialog):
    """Simple Tool editor shell with optional profiles kept out of the basic form."""

    profile_action_requested = Signal(str, object)

    def __init__(
        self,
        tool: ToolDefinition,
        *,
        holder_fingerprint: ContentFingerprint | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolEditorDialog")
        self.setWindowTitle("Chỉnh sửa Tool")
        self.setAccessibleName("Trình chỉnh sửa Tool")
        self.setAccessibleDescription(
            "Cấu hình cơ bản ở trước; cấu hình theo chương trình là tùy chọn."
        )
        self._tool = tool
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(7)
        basic = QGroupBox("Cấu hình cơ bản")
        basic_form = QFormLayout(basic)
        name = QLineEdit(tool.name)
        name.setReadOnly(True)
        name.setAccessibleName("Tên Tool")
        basic_form.addRow("Tên Tool", name)
        family = QLabel(_family_text(tool))
        family.setAccessibleName("Họ Tool")
        basic_form.addRow("Họ Tool", family)
        common = QLabel(
            "Đã cấu hình"
            if not tool.common_defaults.is_empty
            else "Chưa cấu hình · Không bắt buộc"
        )
        common.setAccessibleName("Trạng thái cấu hình cơ bản")
        basic_form.addRow("Dữ liệu cắt dùng chung", common)
        root.addWidget(basic)

        self.profiles = ToolProgramProfilesWidget(parent=self)
        self.profiles.bind_tool(
            tool, holder_fingerprint=holder_fingerprint
        )
        self.profiles.action_requested.connect(
            self.profile_action_requested.emit
        )
        root.addWidget(self.profiles, 1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton("Đóng")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        root.addLayout(footer)
        self.setMinimumSize(620, 420)
        self.resize(760, 620)

    def refresh_tool(
        self,
        tool: ToolDefinition,
        *,
        holder_fingerprint: ContentFingerprint | None = None,
    ) -> None:
        self._tool = tool
        self.profiles.bind_tool(
            tool, holder_fingerprint=holder_fingerprint
        )


__all__ = [
    "ToolEditorDialog",
    "ToolProfileEditorDialog",
    "ToolProfileProvenanceWidget",
    "ToolProfileSavePreviewDialog",
    "ToolProgramProfilesWidget",
]
