"""Offline smoke coverage for the Stage QA.1 development toolchain."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import sys
from pathlib import Path

import psutil
import pytest
from packaging.version import Version
from pytestqt.qt_compat import qt_api

from hms_cadcam.cam.domain import CamJobId
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.operation_manager_projection import OperationManagerProjectionBuilder


_QA_IMPORTS = {
    "pytest-qt": "pytestqt",
    "pytest-cov": "pytest_cov",
    "pytest-timeout": "pytest_timeout",
    "psutil": "psutil",
    "pytest-xdist": "xdist",
    "pytest-benchmark": "pytest_benchmark",
}

_PLUGIN_MODULES = {
    "pytestqt.plugin",
    "pytest_cov.plugin",
    "pytest_timeout",
    "xdist.plugin",
    "pytest_benchmark.plugin",
}


def test_required_qa_packages_import_and_report_versions() -> None:
    for distribution, module_name in _QA_IMPORTS.items():
        importlib.import_module(module_name)
        assert Version(importlib.metadata.version(distribution)).release


def test_pytest_uses_pyside6_without_loading_pyqt(pytestconfig) -> None:
    assert pytestconfig.getini("qt_api") == "pyside6"
    assert qt_api.pytest_qt_api == "pyside6"
    assert "PySide6" in qt_api.get_versions()
    assert "PyQt5" not in sys.modules
    assert "PyQt6" not in sys.modules


def test_required_pytest_plugins_are_registered(pytestconfig) -> None:
    modules = {
        getattr(plugin, "__name__", plugin.__class__.__module__)
        for plugin in pytestconfig.pluginmanager.get_plugins()
    }
    assert _PLUGIN_MODULES.issubset(modules)


def test_psutil_can_inspect_current_process() -> None:
    process = psutil.Process(os.getpid())

    assert process.pid == os.getpid()
    assert process.is_running()
    assert isinstance(process.children(recursive=True), list)
    assert process.memory_info().rss > 0


@pytest.mark.windows_native
@pytest.mark.skipif(sys.platform != "win32", reason="pywinauto chỉ hỗ trợ Windows")
def test_pywinauto_exposes_uia_and_win32_backends() -> None:
    from pywinauto import Desktop

    assert Desktop(backend="uia").backend.name == "uia"
    assert Desktop(backend="win32").backend.name == "win32"


@pytest.mark.benchmark
def test_cam_identity_codec_benchmark_smoke(benchmark) -> None:
    encoded = str(CamJobId.new())

    decoded = benchmark(CamJobId.parse, encoded)

    assert str(decoded) == encoded


@pytest.mark.benchmark
def test_operation_manager_projection_benchmark_smoke(
    benchmark,
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Operation Manager Benchmark")
    builder = OperationManagerProjectionBuilder()

    projection = benchmark(builder.build, service, session)

    assert projection.project_id == session.manifest.project_id
    service.close_project()
