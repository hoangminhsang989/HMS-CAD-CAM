"""Reusable Stage 13B panel extended by the Stage 13C owner-local workflow."""
from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.i18n import translation_service


_FIELD_KEYS = {
    "spindle_speed_rpm": "stage13c.advisor.field.spindle",
    "feed_mm_per_rev": "stage13c.advisor.field.feed",
    "max_depth_of_cut_mm": "stage13c.advisor.field.depth",
}


class CuttingAdvisorPanel(QWidget):
    """One advisor panel shared by Stage 13B and Stage 13C workflows."""

    ownership_invalidated = Signal(str)

    def __init__(
        self,
        translate: Callable[[str], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translate = translate or translation_service().translate_key
        self.setObjectName("CuttingAdvisorPanel")

        layout = QVBoxLayout(self)
        self.status = QLabel(self)
        self.status.setObjectName("CuttingAdvisorStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        form = QFormLayout()
        self.strategy_caption = QLabel(self)
        self.strategy_value = QLabel(self)
        self.strategy_value.setObjectName("Stage13CAdvisorStrategy")
        form.addRow(self.strategy_caption, self.strategy_value)

        self.workpiece_material_caption = QLabel(self)
        self.workpiece_material = QComboBox(self)
        self.workpiece_material.setObjectName("Stage13CWorkpieceMaterial")
        form.addRow(self.workpiece_material_caption, self.workpiece_material)

        self.tool_material_caption = QLabel(self)
        self.tool_material = QComboBox(self)
        self.tool_material.setObjectName("Stage13CToolMaterial")
        form.addRow(self.tool_material_caption, self.tool_material)

        self.diameter_caption = QLabel(self)
        self.diameter_value = QLabel(self)
        self.diameter_value.setObjectName("Stage13CActiveDiameter")
        form.addRow(self.diameter_caption, self.diameter_value)

        self.current_caption = QLabel(self)
        self.current_value = QLabel(self)
        self.current_value.setObjectName("Stage13CCurrentParameters")
        self.current_value.setWordWrap(True)
        form.addRow(self.current_caption, self.current_value)
        layout.addLayout(form)

        self.warning_value = QLabel(self)
        self.warning_value.setObjectName("Stage13CAdvisorWarnings")
        self.warning_value.setWordWrap(True)
        layout.addWidget(self.warning_value)

        self.field_checks: dict[str, QCheckBox] = {}
        for field_id in _FIELD_KEYS:
            check = QCheckBox(self)
            check.setObjectName(f"Stage13CRecommendation_{field_id}")
            check.hide()
            self.field_checks[field_id] = check
            layout.addWidget(check)

        buttons = QHBoxLayout()
        self.analyze = QPushButton(self)
        self.cancel = QPushButton(self)
        self.apply_selected = QPushButton(self)
        self.reset_selection = QPushButton(self)
        self.undo = QPushButton(self)
        self.close_button = QPushButton(self)
        for button in (
            self.analyze,
            self.cancel,
            self.apply_selected,
            self.reset_selection,
            self.undo,
            self.close_button,
        ):
            buttons.addWidget(button)
        self.close_button.clicked.connect(self.close)
        layout.addLayout(buttons)

        self.configure_material_tokens(())
        self.reset_result()
        self.retranslate_ui()

    def configure_material_tokens(self, tokens: Iterable[str]) -> None:
        """Populate exact model tokens while keeping session selection stable."""

        material = self.workpiece_material.currentData()
        tool = self.tool_material.currentData()
        self.workpiece_material.clear()
        self.workpiece_material.addItem("", None)
        for token in tokens:
            self.workpiece_material.addItem(str(token), str(token))
        self.tool_material.clear()
        self.tool_material.addItem("", None)
        self.tool_material.addItem("HSS", "HSS")
        self.tool_material.addItem("CARBIDE", "CARBIDE")
        self._select_token(self.workpiece_material, material)
        self._select_token(self.tool_material, tool)
        self._retranslate_material_items()

    @staticmethod
    def _select_token(combo: QComboBox, token: object) -> None:
        index = combo.findData(token)
        combo.setCurrentIndex(max(0, index))

    def selected_workpiece_material(self) -> str | None:
        value = self.workpiece_material.currentData()
        return value if isinstance(value, str) and value else None

    def selected_tool_material(self) -> str | None:
        value = self.tool_material.currentData()
        return value if isinstance(value, str) and value else None

    def selected_fields(self) -> frozenset[str]:
        return frozenset(
            field_id for field_id, check in self.field_checks.items() if check.isChecked()
        )

    def set_owner_state(
        self,
        *,
        strategy_id: str,
        diameter_mm: float,
        diameter_provenance: str,
        spindle_rpm: float | None,
        feed_mm_per_rev: float | None,
        depth_of_cut_mm: float | None,
    ) -> None:
        self.strategy_value.setText(strategy_id)
        self.diameter_value.setText(f"{diameter_mm:g} mm — {diameter_provenance}")
        current = [
            f"RPM={spindle_rpm:g}" if spindle_rpm is not None else "RPM=—",
            f"F={feed_mm_per_rev:g} mm/rev" if feed_mm_per_rev is not None else "F=—",
        ]
        if depth_of_cut_mm is not None:
            current.append(f"DOC={depth_of_cut_mm:g} mm")
        self.current_value.setText("; ".join(current))

    def set_recommendation(self, result: object) -> None:
        values = getattr(result, "final_recommendation", {})
        warnings = tuple(getattr(result, "warnings", ()))
        unsupported = tuple(getattr(result, "retained_unsupported", ()))
        for field_id, check in self.field_checks.items():
            visible = field_id in values
            check.setVisible(visible)
            check.setChecked(False)
            if visible:
                check.setText(
                    f"{self._translate(_FIELD_KEYS[field_id])}: {float(values[field_id]):g}"
                )
        warning_items = [*warnings]
        warning_items.extend(f"UNSUPPORTED_PROPOSED_FIELD:{key}" for key, _value in unsupported)
        self.warning_value.setText("\n".join(dict.fromkeys(warning_items)))
        self.apply_selected.setEnabled(bool(values))
        self.undo.setEnabled(False)

    def reset_result(self) -> None:
        for check in self.field_checks.values():
            check.setChecked(False)
            check.hide()
        self.warning_value.clear()
        self.apply_selected.setEnabled(False)
        self.undo.setEnabled(False)

    def reset_field_selection(self) -> None:
        for check in self.field_checks.values():
            check.setChecked(False)

    def retranslate_ui(self) -> None:
        self.strategy_caption.setText(self._translate("stage13c.advisor.strategy"))
        self.workpiece_material_caption.setText(
            self._translate("stage13c.advisor.workpiece_material")
        )
        self.tool_material_caption.setText(self._translate("stage13c.advisor.tool_material"))
        self.diameter_caption.setText(self._translate("stage13c.advisor.active_diameter"))
        self.current_caption.setText(self._translate("stage13c.advisor.current_values"))
        self.analyze.setText(self._translate("stage13b.advisor.analyze"))
        self.cancel.setText(self._translate("stage13b.advisor.cancel"))
        self.apply_selected.setText(self._translate("stage13b.advisor.apply_selected"))
        self.reset_selection.setText(self._translate("stage13b.advisor.reset_selection"))
        self.undo.setText(self._translate("stage13b.advisor.undo"))
        self.close_button.setText(self._translate("stage13b.advisor.close"))
        self._retranslate_material_items()
        for field_id, check in self.field_checks.items():
            if check.isVisible() and ":" in check.text():
                value = check.text().split(":", 1)[1]
                check.setText(f"{self._translate(_FIELD_KEYS[field_id])}:{value}")

    def _retranslate_material_items(self) -> None:
        if self.workpiece_material.count():
            self.workpiece_material.setItemText(
                0, self._translate("stage13c.advisor.not_selected")
            )
        if self.tool_material.count():
            self.tool_material.setItemText(0, self._translate("stage13c.advisor.not_selected"))
            self.tool_material.setItemText(1, self._translate("stage13c.advisor.hss"))
            self.tool_material.setItemText(2, self._translate("stage13c.advisor.carbide"))
        for index in range(1, self.workpiece_material.count()):
            token = str(self.workpiece_material.itemData(index))
            self.workpiece_material.setItemText(
                index, self._translate(f"stage13c.advisor.material.{token.casefold()}")
            )

    def set_state(self, state_key: str) -> None:
        self.status.setText(self._translate(f"stage13c.advisor.state.{state_key}"))

    def bind_owner_invalidator(self, invalidator: Callable[[], None]) -> None:
        """Compatibility hook retained for Stage 13B owner tests."""

        self.ownership_invalidated.connect(lambda _reason: invalidator())

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.ownership_invalidated.emit("PANEL_CLOSED")
        super().closeEvent(event)


__all__ = ["CuttingAdvisorPanel"]
