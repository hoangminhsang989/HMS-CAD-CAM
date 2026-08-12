"""R241 strict optional-module and normal-workflow non-interference proofs."""

import subprocess
import sys

from hms_cadcam.cam.post import (
    NCExportService,
    PostRequest,
    PostRuntimeService,
    SimulationGateMode,
    SimulationGatePolicy,
    canonical_definition,
)
from tests.unit._export_fixtures import production_export_fixture
from tests.unit._post_fixtures import source_snapshot


def test_normal_application_import_does_not_import_r241_simulation_package() -> None:
    code = (
        "import sys; import hms_cadcam.application; "
        "assert not any(n == 'hms_cadcam.simulation' or "
        "n.startswith('hms_cadcam.simulation.') for n in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_post_and_export_do_not_import_or_invoke_r241_simulation(tmp_path) -> None:
    before = frozenset(sys.modules)
    source = source_snapshot()
    request = PostRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        canonical_definition(),
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
    )
    assert PostRuntimeService().post(request, source).accepted
    export_root = tmp_path / "isolation.HMS"
    export_request, snapshot = production_export_fixture(export_root)
    assert NCExportService().export(export_root, export_request, snapshot).accepted
    imported = frozenset(sys.modules) - before
    assert not any(
        name == "hms_cadcam.simulation" or name.startswith("hms_cadcam.simulation.")
        for name in imported
    )
