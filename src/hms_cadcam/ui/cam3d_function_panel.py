"""Stage 9A.8 WP1 shell with the WP2A typed selection editor.

The widget remains presentation-only: no CAD kernel, persistence, or worker
service is imported here.  Selection commands leave through typed signals and
immutable application state is rendered without retaining viewport objects.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.project.models import ProjectSession
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectionIssue,
    Cam3DSelectionRole,
    Cam3DSelectionState,
    Cam3DSelectionStatus,
)
from hms_cadcam.ui.cam3d_function_state import (
    Cam3DPresentationState,
    Cam3DUiCommandPolicy,
)
from hms_cadcam.ui.cam3d_selection_editor import Cam3DSelectionRoleEditor
from hms_cadcam.ui.localization import ui_text


_SECTION_SOURCES: tuple[tuple[str, str], ...] = (
    ("machining_zone", "Machining zone"),
    ("part", "Part"),
    ("check", "Check"),
    ("fixtures", "Fixtures"),
    ("tool", "Tool Assembly"),
    ("tolerance", "Tolerance"),
    ("allowance", "Surface Allowance"),
    ("safe_motion", "Safe motion"),
    ("calculation_status", "Calculation Status"),
    ("diagnostics", "DIAGNOSTICS"),
)


class Cam3DFunctionPanel(QWidget):
    """Responsive, state-driven shell for the CAM 3D function UI."""

    state_changed = Signal(object)
    selection_assign_requested = Signal(object)
    selection_clear_requested = Signal(object)

    def __init__(
        self,
        *,
        feature_enabled: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if type(feature_enabled) is not bool:
            raise TypeError("feature_enabled must be bool")
        self.setObjectName("Cam3DFunctionPanel")
        self.setMinimumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._feature_enabled = feature_enabled
        self._state = (
            Cam3DPresentationState.empty()
            if feature_enabled
            else Cam3DPresentationState.feature_disabled()
        )
        self._selection_state = Cam3DSelectionState.closed()
        self._section_titles: dict[str, QGroupBox] = {}
        self._role_editors: dict[Cam3DSelectionRole, Cam3DSelectionRoleEditor] = {}
        self._placeholder_controls: list[QWidget] = []
        self._build_ui()
        self.retranslate_ui()
        self._render_state()

    @property
    def presentation_state(self) -> Cam3DPresentationState:
        """Return the immutable state currently rendered by the shell."""

        return self._state

    @property
    def feature_enabled(self) -> bool:
        """Return whether the review-only entry is enabled in this process."""

        return self._feature_enabled

    @property
    def placeholder_controls(self) -> tuple[QWidget, ...]:
        """Expose the non-operational WP1 controls for focused UI verification."""

        return tuple(self._placeholder_controls)

    @property
    def selection_state(self) -> Cam3DSelectionState:
        """Return the immutable WP2A selection aggregate currently rendered."""

        return self._selection_state

    @property
    def role_editors(
        self,
    ) -> tuple[tuple[Cam3DSelectionRole, Cam3DSelectionRoleEditor], ...]:
        """Expose stable typed role editors for focused UI verification."""

        return tuple(self._role_editors.items())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        header = QFrame(self)
        header.setObjectName("Cam3DFunctionHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(10, 8, 10, 8)
        self.title_label = QLabel(header)
        self.title_label.setObjectName("Cam3DFunctionTitle")
        self.title_label.setProperty("role", "heading")
        header_layout.addWidget(self.title_label)

        state_row = QHBoxLayout()
        self.state_caption = QLabel(header)
        self.state_caption.setObjectName("Cam3DStateCaption")
        self.state_value = QLabel(header)
        self.state_value.setObjectName("Cam3DStateValue")
        self.state_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        state_row.addWidget(self.state_caption)
        state_row.addWidget(self.state_value, 1)
        header_layout.addLayout(state_row)
        self.reason_label = QLabel(header)
        self.reason_label.setObjectName("Cam3DStateReason")
        self.reason_label.setWordWrap(True)
        header_layout.addWidget(self.reason_label)
        root.addWidget(header)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("Cam3DFunctionScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(self.scroll_area)
        content.setObjectName("Cam3DFunctionContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(7)
        for key, source in _SECTION_SOURCES:
            group = QGroupBox(content)
            group.setObjectName(f"Cam3DSection_{key}")
            group.setProperty("titleSource", source)
            layout = QVBoxLayout(group)
            layout.setContentsMargins(9, 12, 9, 9)
            if key == "diagnostics":
                control = QLabel(group)
                control.setObjectName("Cam3DDiagnosticsValue")
                control.setWordWrap(True)
                control.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                self.diagnostics_value = control
            elif key in {"part", "check", "fixtures"}:
                role = {
                    "part": Cam3DSelectionRole.PART,
                    "check": Cam3DSelectionRole.CHECK,
                    "fixtures": Cam3DSelectionRole.FIXTURE,
                }[key]
                control = QPushButton(group)
                control.setObjectName(f"Cam3DPlaceholder_{key}")
                control.setProperty("textSource", "Select")
                control.hide()
                editor = Cam3DSelectionRoleEditor(role, group)
                editor.assign_requested.connect(self.selection_assign_requested.emit)
                editor.clear_requested.connect(self.selection_clear_requested.emit)
                self._role_editors[role] = editor
                layout.addWidget(editor)
            elif key in {"machining_zone", "tool"}:
                control = QPushButton(group)
                control.setObjectName(f"Cam3DPlaceholder_{key}")
                control.setProperty("textSource", "Select")
            else:
                control = QLineEdit(group)
                control.setObjectName(f"Cam3DPlaceholder_{key}")
                control.setProperty("placeholderSource", "No data")
                control.setReadOnly(True)
            control.setEnabled(False)
            self._placeholder_controls.append(control)
            layout.addWidget(control)
            self._section_titles[key] = group
            content_layout.addWidget(group)
        content_layout.addStretch(1)
        self.scroll_area.setWidget(content)
        root.addWidget(self.scroll_area, 1)

        self.scope_note = QLabel(self)
        self.scope_note.setObjectName("Cam3DWP1ScopeNote")
        self.scope_note.setWordWrap(True)
        root.addWidget(self.scope_note)

    def set_feature_enabled(self, enabled: bool) -> None:
        """Apply a process-local feature decision and reset presentation state."""

        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        self._feature_enabled = enabled
        self.set_state(
            Cam3DPresentationState.empty()
            if enabled
            else Cam3DPresentationState.feature_disabled()
        )

    def bind_project(
        self,
        session: ProjectSession | None,
        *,
        generation: int | None,
        read_only: bool = False,
    ) -> None:
        """Bind only typed project identity; never retain a mutable session."""

        if not self._feature_enabled:
            self.set_state(Cam3DPresentationState.feature_disabled())
            return
        if session is None:
            self.set_state(Cam3DPresentationState.empty())
            return
        if not isinstance(session, ProjectSession):
            raise TypeError("session must be ProjectSession or None")
        if type(generation) is not int or generation < 0:
            raise ValueError("generation must be a non-negative int for a project")
        if type(read_only) is not bool:
            raise TypeError("read_only must be bool")
        project_id = session.manifest.project_id
        next_state = (
            Cam3DPresentationState.for_read_only(project_id, generation)
            if read_only
            else Cam3DPresentationState.ready(project_id, generation)
        )
        self.set_state(next_state)

    def set_state(self, state: Cam3DPresentationState) -> None:
        """Render one immutable state and emit only when the value changes."""

        if not isinstance(state, Cam3DPresentationState):
            raise TypeError("state must be Cam3DPresentationState")
        if state == self._state:
            self._render_state()
            return
        self._state = state
        self._render_state()
        self.state_changed.emit(state)

    def set_selection_state(self, state: Cam3DSelectionState) -> None:
        """Project one immutable WP2A aggregate into the existing shell state."""

        if not isinstance(state, Cam3DSelectionState):
            raise TypeError("state must be Cam3DSelectionState")
        self._selection_state = state
        if not self._feature_enabled:
            presentation = Cam3DPresentationState.feature_disabled()
        elif state.project_id is None:
            presentation = Cam3DPresentationState.empty()
        elif state.read_only:
            presentation = Cam3DPresentationState.for_read_only(
                state.project_id,
                state.project_generation,
            )
        elif state.status is Cam3DSelectionStatus.STALE:
            presentation = Cam3DPresentationState.stale(
                state.project_id,
                state.project_generation,
            )
        elif state.status is Cam3DSelectionStatus.INVALID:
            presentation = Cam3DPresentationState.error(
                "",
                state.project_id,
                state.project_generation,
            )
        else:
            presentation = Cam3DPresentationState.ready(
                state.project_id,
                state.project_generation,
            )
        self.set_state(presentation)

    def retranslate_ui(self, _language: object = None) -> None:
        """Retranslate visible shell text without changing state or identity."""

        self.title_label.setText(ui_text("CAM 3D Function UI"))
        self.title_label.setAccessibleName(ui_text("CAM 3D Function UI"))
        self.state_caption.setText(f"{ui_text('Status')}:")
        scope_description = ui_text(
            "Select Part, Check and Fixture surfaces; calculation remains unavailable"
        )
        self.scroll_area.setAccessibleName(ui_text("CAM 3D Function UI"))
        self.scroll_area.setAccessibleDescription(scope_description)
        for (key, source), control in zip(
            _SECTION_SOURCES,
            self._placeholder_controls,
            strict=True,
        ):
            section_title = ui_text(source)
            group = self._section_titles[key]
            group.setTitle(section_title)
            group.setAccessibleName(section_title)
            control.setAccessibleName(section_title)
            control.setAccessibleDescription(scope_description)
            text_source = control.property("textSource")
            if isinstance(control, QPushButton) and isinstance(text_source, str):
                control.setText(ui_text(text_source))
            placeholder_source = control.property("placeholderSource")
            if isinstance(control, QLineEdit) and isinstance(placeholder_source, str):
                control.setPlaceholderText(ui_text(placeholder_source))
        for editor in self._role_editors.values():
            editor.retranslate_ui()
        self.scope_note.setText(
            ui_text("Select Part, Check and Fixture surfaces; calculation remains unavailable")
        )
        self.scope_note.setAccessibleDescription(scope_description)
        self._render_state()

    def _render_state(self) -> None:
        state = self._state
        self.setProperty("cam3dState", state.state.value)
        self.setProperty("commandPolicy", state.command_policy.value)
        self.state_value.setText(ui_text(state.label_key))
        self.state_value.setAccessibleName(
            f"{ui_text('Status')}: {ui_text(state.label_key)}"
        )
        selection = self._selection_state
        selection_message = (
            ui_text(selection.issue.label_key)
            if selection.issue is not Cam3DSelectionIssue.NONE
            else ui_text(selection.status.label_key)
        )
        reason_text = (
            selection_message
            if selection.project_id is not None
            else ui_text(state.reason_key)
        )
        self.reason_label.setText(reason_text)
        self.reason_label.setAccessibleDescription(reason_text)
        diagnostics = state.message.strip()
        if not diagnostics and selection.issue is not Cam3DSelectionIssue.NONE:
            diagnostics = selection_message
        if not diagnostics:
            diagnostics = f"{ui_text('DIAGNOSTICS')}: {state.diagnostic_count}"
        self.diagnostics_value.setText(diagnostics)
        self.scroll_area.setEnabled(
            state.command_policy is not Cam3DUiCommandPolicy.HIDDEN
        )
        for control in self._placeholder_controls:
            control.setEnabled(False)
        for editor in self._role_editors.values():
            editor.set_state(selection)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def section_keys(self) -> Iterable[str]:
        """Return stable section identifiers in visual order."""

        return tuple(key for key, _source in _SECTION_SOURCES)


__all__ = ["Cam3DFunctionPanel"]