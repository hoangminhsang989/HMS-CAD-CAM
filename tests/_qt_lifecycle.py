"""Deterministic ownership and deferred-delete helpers for Qt tests."""

from __future__ import annotations

from dataclasses import dataclass

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, Qt
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True, slots=True)
class QtLifecycleSnapshot:
    """Bounded Qt inventory used by lifecycle regression tests."""

    all_widgets: int
    top_levels: int
    hidden_top_levels: int
    modal_top_levels: int
    app_owned_qobjects: int
    running_app_threads: int


def qt_object_pointer(widget: QWidget) -> int:
    """Return the stable C++ identity for a currently valid Qt widget."""

    return int(shiboken6.getCppPointer(widget)[0])


def top_level_baseline(application: QApplication | None) -> frozenset[int]:
    """Capture top levels that existed before the current test owned anything."""

    if application is None:
        return frozenset()
    return frozenset(
        qt_object_pointer(widget)
        for widget in application.topLevelWidgets()
        if shiboken6.isValid(widget)
    )


def qt_lifecycle_snapshot(application: QApplication) -> QtLifecycleSnapshot:
    """Measure stable, public Qt inventories without retaining their objects."""

    top_levels = tuple(
        widget
        for widget in application.topLevelWidgets()
        if shiboken6.isValid(widget)
    )
    return QtLifecycleSnapshot(
        all_widgets=len(application.allWidgets()),
        top_levels=len(top_levels),
        hidden_top_levels=sum(widget.isHidden() for widget in top_levels),
        modal_top_levels=sum(
            widget.isModal()
            or widget.windowModality() != Qt.WindowModality.NonModal
            for widget in top_levels
        ),
        app_owned_qobjects=len(application.findChildren(QObject)),
        running_app_threads=sum(
            thread.isRunning() for thread in application.findChildren(QThread)
        ),
    )


def drain_test_owned_qt_state(
    application: QApplication,
    baseline: frozenset[int],
    *,
    maximum_passes: int = 8,
) -> QtLifecycleSnapshot:
    """Close and delete top levels created after ``baseline``.

    ``processEvents()`` does not guarantee delivery of deferred-delete events.
    The explicit ``sendPostedEvents`` call is therefore the ownership boundary;
    the bounded loop also catches a dialog created while another dialog closes.
    """

    for _pass in range(maximum_passes):
        owned = [
            widget
            for widget in application.topLevelWidgets()
            if shiboken6.isValid(widget)
            and qt_object_pointer(widget) not in baseline
        ]
        if not owned:
            break

        owned.sort(
            key=lambda widget: (
                not (
                    widget.isModal()
                    or widget.windowModality() != Qt.WindowModality.NonModal
                ),
                qt_object_pointer(widget),
            )
        )
        for widget in owned:
            if not shiboken6.isValid(widget):
                continue
            widget.close()
            if shiboken6.isValid(widget):
                widget.deleteLater()

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    application.processEvents()
    remaining = [
        qt_object_pointer(widget)
        for widget in application.topLevelWidgets()
        if shiboken6.isValid(widget) and qt_object_pointer(widget) not in baseline
    ]
    if remaining:
        raise AssertionError(
            "Qt test teardown did not release owned top-level widgets after "
            f"{maximum_passes} passes: {remaining}"
        )

    snapshot = qt_lifecycle_snapshot(application)
    if snapshot.running_app_threads:
        raise AssertionError(
            "Qt test teardown found running QApplication-owned QThreads: "
            f"{snapshot.running_app_threads}"
        )
    return snapshot
