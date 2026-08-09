"""Repository-wide pytest ownership boundaries."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtWidgets import QApplication

from _qt_lifecycle import drain_test_owned_qt_state, top_level_baseline


@pytest.fixture(autouse=True)
def _qt_test_lifecycle_boundary() -> Iterator[None]:
    """Release only top-level Qt objects created by the current test."""

    application = QApplication.instance()
    baseline = top_level_baseline(application)
    yield

    application = QApplication.instance()
    if application is not None:
        drain_test_owned_qt_state(application, baseline)
