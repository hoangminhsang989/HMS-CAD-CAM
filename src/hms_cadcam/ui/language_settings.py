"""Language preference dialog backed by the central translation services."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.i18n import (
    LocaleSettingsService,
    TranslationService,
    UiLanguage,
    apply_widget_font_tree,
    language_display_name,
)
from hms_cadcam.ui.localization import localize_widget_tree, ui_text


class LanguageSettingsDialog(QDialog):
    """Select and apply one typed interface language without restarting."""

    language_applied = Signal(object)

    def __init__(
        self,
        service: TranslationService,
        settings: LocaleSettingsService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._settings = settings
        self.setObjectName("LanguageSettingsDialog")
        self.setModal(False)
        self.setMinimumSize(520, 330)
        self.resize(590, 370)

        self.title_label = QLabel()
        self.title_label.setObjectName("LanguageSettingsTitle")
        self.title_label.setStyleSheet("font-size: 19px; font-weight: 600;")

        self.description_label = QLabel()
        self.description_label.setObjectName("LanguageSettingsDescription")
        self.description_label.setWordWrap(True)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)

        self.language_label = QLabel()
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("InterfaceLanguageCombo")
        self.language_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        for language in UiLanguage:
            self.language_combo.addItem("", language)
        selected_index = self.language_combo.findData(service.language)
        self.language_combo.setCurrentIndex(max(0, selected_index))
        self.language_combo.currentIndexChanged.connect(self._selection_changed)

        self.current_label = QLabel()
        self.current_label.setObjectName("CurrentLanguageLabel")
        self.current_label.setWordWrap(True)

        self.default_label = QLabel()
        self.default_label.setObjectName("DefaultLanguageDescription")
        self.default_label.setWordWrap(True)
        self.default_label.setStyleSheet("color: #5b6570;")

        self.status_label = QLabel()
        self.status_label.setObjectName("LanguageSettingsStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #a33a2b;")
        self.status_label.hide()

        self.apply_button = QPushButton()
        self.apply_button.setObjectName("ApplyLanguageButton")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self.apply_selection)
        self.close_button = QPushButton()
        self.close_button.setObjectName("CloseLanguageSettingsButton")
        self.close_button.clicked.connect(self.close)

        language_row = QHBoxLayout()
        language_row.addWidget(self.language_label)
        language_row.addWidget(self.language_combo, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(13)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        layout.addWidget(separator)
        layout.addLayout(language_row)
        layout.addWidget(self.current_label)
        layout.addWidget(self.default_label)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addLayout(button_row)

        self._service.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui(service.language)

    @property
    def selected_language(self) -> UiLanguage:
        value = self.language_combo.currentData()
        return UiLanguage.coerce(value)

    def apply_selection(self) -> None:
        """Persist and apply the selected locale without touching project state."""
        selected = self.selected_language
        if not self._settings.save(selected):
            self.status_label.setText(
                ui_text("Language preference could not be saved.")
            )
            self.status_label.show()
            return
        self.status_label.hide()
        self._service.set_language(selected)
        self.language_applied.emit(selected)
        self.retranslate_ui(selected)

    def retranslate_ui(self, _language: object = None) -> None:
        """Update text, accessibility and choices while preserving selection."""
        selected = self.selected_language
        apply_widget_font_tree(self, self._service.language)
        self.setWindowTitle(ui_text("Language settings"))
        self.setAccessibleName(ui_text("Language settings"))
        self.setAccessibleDescription(
            ui_text("Choose the language used by HMS CAD/CAM.")
        )
        self.title_label.setText(ui_text("Language settings"))
        self.description_label.setText(
            ui_text("The language changes immediately without modifying the project.")
        )
        self.language_label.setText(f"{ui_text('Interface language')}:")
        self.language_label.setAccessibleName(ui_text("Interface language"))
        self.language_combo.setAccessibleName(ui_text("Interface language"))
        self.language_combo.setAccessibleDescription(
            ui_text("Choose the language used by HMS CAD/CAM.")
        )
        for index in range(self.language_combo.count()):
            language = UiLanguage.coerce(self.language_combo.itemData(index))
            self.language_combo.setItemText(
                index,
                language_display_name(language, service=self._service),
            )
        selected_index = self.language_combo.findData(selected)
        self.language_combo.setCurrentIndex(max(0, selected_index))
        self.current_label.setText(
            f"{ui_text('Current language')}: "
            f"{language_display_name(self._service.language, service=self._service)}"
        )
        self.current_label.setAccessibleName(ui_text("Current language"))
        self.default_label.setText(
            f"{ui_text('Default language')}: "
            f"{ui_text('HMS uses Vietnamese for new or invalid settings.')}"
        )
        self.default_label.setAccessibleName(ui_text("Default language"))
        self.apply_button.setText(ui_text("Apply"))
        self.apply_button.setAccessibleName(ui_text("Apply"))
        self.apply_button.setToolTip(
            ui_text("The language changes immediately without modifying the project.")
        )
        self.close_button.setText(ui_text("Close"))
        self.close_button.setAccessibleName(ui_text("Close"))
        localize_widget_tree(self)
        self._selection_changed()

    def _selection_changed(self) -> None:
        self.apply_button.setEnabled(
            self.selected_language is not self._service.language
        )

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


__all__ = ["LanguageSettingsDialog"]
