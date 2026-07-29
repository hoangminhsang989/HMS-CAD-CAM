"""Typed Part/Check/Fixture editor widgets for Stage 9A.8 WP2A."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectionRole,
    Cam3DSelectionState,
    Cam3DSelectionStatus,
    Cam3DSelectionValidity,
)
from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.localization import ui_text


class Cam3DSelectionRoleEditor(QWidget):
    """Render and edit one role without retaining viewport/native objects."""

    assign_requested = Signal(object)
    clear_requested = Signal(object)

    def __init__(
        self,
        role: Cam3DSelectionRole,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(role, Cam3DSelectionRole):
            raise TypeError("role must be Cam3DSelectionRole")
        self.role = role
        self._state = Cam3DSelectionState.closed()
        self.setObjectName(f"Cam3DSelectionEditor_{role.value}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(5)
        self.summary_label = QLabel(self)
        self.summary_label.setObjectName(f"Cam3DSelectionSummary_{role.value}")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)
        self.validity_label = QLabel(self)
        self.validity_label.setObjectName(f"Cam3DSelectionValidity_{role.value}")
        self.validity_label.setWordWrap(True)
        root.addWidget(self.validity_label)

        commands = QHBoxLayout()
        self.assign_button = QPushButton(self)
        self.assign_button.setObjectName(f"Cam3DAssignSelection_{role.value}")
        self.assign_button.clicked.connect(self._request_assign)
        commands.addWidget(self.assign_button)
        self.clear_button = QPushButton(self)
        self.clear_button.setObjectName(f"Cam3DClearSelection_{role.value}")
        self.clear_button.clicked.connect(self._request_clear)
        commands.addWidget(self.clear_button)
        root.addLayout(commands)

        self.retranslate_ui()
        self.set_state(self._state)

    @property
    def selection_state(self) -> Cam3DSelectionState:
        return self._state

    def set_state(self, state: Cam3DSelectionState) -> None:
        if not isinstance(state, Cam3DSelectionState):
            raise TypeError("state must be Cam3DSelectionState")
        self._state = state
        items = state.items_for(self.role)
        count = len(items)
        if count:
            summary = translation_service().format(
                "Selected surfaces: {count}",
                count=count,
            )
        else:
            summary = ui_text("No surfaces selected")
        self.summary_label.setText(summary)
        self.summary_label.setAccessibleName(
            translation_service().format(
                "{role} selection summary",
                role=ui_text(self.role.label_key),
            )
        )
        self.summary_label.setAccessibleDescription(summary)

        if any(item.validity is Cam3DSelectionValidity.STALE for item in items):
            validity = ui_text("Surface selection is stale")
        elif any(item.validity is Cam3DSelectionValidity.INVALID for item in items):
            validity = ui_text("Surface selection is invalid")
        elif items:
            validity = ui_text("Selection is valid")
        else:
            validity = ui_text(state.status.label_key)
        self.validity_label.setText(validity)
        self.validity_label.setAccessibleName(
            translation_service().format(
                "{role} selection status",
                role=ui_text(self.role.label_key),
            )
        )
        self.validity_label.setAccessibleDescription(validity)

        can_mutate = state.can_mutate
        self.assign_button.setEnabled(can_mutate)
        self.clear_button.setEnabled(can_mutate and bool(items))

    def retranslate_ui(self) -> None:
        role_label = ui_text(self.role.label_key)
        self.assign_button.setText(ui_text("Assign current selection"))
        self.assign_button.setToolTip(
            translation_service().format(
                "Assign current BRep faces to {role}",
                role=role_label,
            )
        )
        self.assign_button.setAccessibleName(
            translation_service().format(
                "Assign {role} surfaces",
                role=role_label,
            )
        )
        self.assign_button.setAccessibleDescription(self.assign_button.toolTip())
        self.clear_button.setText(ui_text("Clear role"))
        self.clear_button.setToolTip(
            translation_service().format(
                "Clear {role} surfaces",
                role=role_label,
            )
        )
        self.clear_button.setAccessibleName(self.clear_button.toolTip())
        self.clear_button.setAccessibleDescription(
            ui_text("Remove only this role without changing project data")
        )
        self.set_state(self._state)

    def _request_assign(self) -> None:
        if self._state.can_mutate:
            self.assign_requested.emit(self.role)

    def _request_clear(self) -> None:
        if self._state.can_mutate and self._state.items_for(self.role):
            self.clear_requested.emit(self.role)


__all__ = ["Cam3DSelectionRoleEditor"]
