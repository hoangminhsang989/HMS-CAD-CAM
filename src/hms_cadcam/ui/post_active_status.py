"""Compact Vietnamese active Post lifecycle projection."""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLabel, QWidget

from hms_cadcam.cam.post_studio import ActiveLifecycleProjection, ManagedActiveStatus
from hms_cadcam.ui.i18n import translation_service


class PostActiveStatusPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PostActiveStatusPanel")
        self._state: ActiveLifecycleProjection | None = None
        self._layout = QFormLayout(self)
        self.fields: dict[str, QLabel] = {}
        self.captions: dict[str, QLabel] = {}
        for key in ("post", "revision", "state", "machine", "controller", "sha", "backup", "rollback", "drift", "activated_at"):
            caption = QLabel(); field = QLabel("—")
            self.captions[key] = caption; self.fields[key] = field
            self._layout.addRow(caption, field)
        translation_service().language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self, *_unused: object) -> None:
        tr = translation_service().translate_key
        for key, caption in self.captions.items():
            caption.setText(tr(f"post_studio.active.{key}"))
        if self._state is not None:
            self.project(self._state)

    def project(self, state: ActiveLifecycleProjection) -> None:
        self._state = state
        active = state.status is ManagedActiveStatus.ACTIVE_MANAGED_REVISION
        self.fields["post"].setText(state.display_name)
        tr = translation_service().translate_key
        self.fields["revision"].setText(tr("post_studio.active.r233_revision") if state.active_revision_id == "fanuc-shl.r233-g40" else state.active_revision_id)
        self.fields["state"].setText(tr("post_studio.active.active") if active else tr("post_studio.active.reconcile"))
        self.fields["machine"].setText(state.machine_name); self.fields["controller"].setText(state.controller_name)
        self.fields["sha"].setText(state.active_sha256 or tr("post_studio.active.unreadable"))
        ready = tr("post_studio.active.ready") if state.rollback_ready else tr("post_studio.active.not_ready")
        self.fields["backup"].setText(ready); self.fields["rollback"].setText(ready)
        if state.drift_detected:
            drift = tr("post_studio.active.external_drift")
        elif active:
            drift = tr("post_studio.active.no_drift")
        else:
            drift = tr("post_studio.active.reconcile")
        self.fields["drift"].setText(drift)
        self.fields["activated_at"].setText(state.activated_at)


__all__ = ["PostActiveStatusPanel"]
