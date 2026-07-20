"""Non-GUI smoke for the 7D.3.1 program-assembly foundation.

The script only creates temporary managed/export files and never opens or runs
the generated NC program.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.unit._fanuc_fixtures import fixture_context
from tests.unit.test_program_assembly import _request, _source_variant

from hms_cadcam.cam.post import (
    CutterCompensationPolicy,
    NCArtifactStore,
    NCAssemblyExportRequest,
    NCAssemblyExportSourceSnapshot,
    NCExportService,
    ExportTarget,
    ExportOverwritePolicy,
    ProgramAssemblyOperationInput,
    ProgramAssemblyService,
    ProgramAssemblyStatus,
)


logger = logging.getLogger("hms.manual.stage7d31")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    first = __import__(
        "tests.unit.test_fanuc_robodrill_21i_runtime",
        fromlist=["_runtime_source"],
    )._runtime_source()
    second = _source_variant(first, "pocket_2_5d")
    third = _source_variant(first, "contour_2d")
    request = _request([first, second, third])
    bindings: list[ProgramAssemblyOperationInput] = []
    for index, item in enumerate(request.operations, start=1):
        binding = replace(item.tool_binding, tool_station=index, length_offset=index)
        context = replace(item.program_context, tool_binding=binding)
        bindings.append(
            replace(
                item,
                tool_binding=binding,
                program_context=context,
                cutter_compensation_policy=CutterCompensationPolicy.DISABLED,
            )
        )
    request = replace(request, operations=tuple(bindings))
    service = ProgramAssemblyService()
    execution = service.assemble(request)
    if not execution.accepted or execution.result is None:
        logger.error("Assembly failed: %s", execution.diagnostics)
        return 1
    result = execution.result
    if result.canonical_text.count("(SHL-TECH)") != 1 or result.canonical_text.count("M30") != 1:
        raise RuntimeError("Global header/footer contract failed")
    if result.canonical_text.count("M06T") != 3:
        raise RuntimeError("Expected one M06 per explicit operation")
    if tuple(section.order_index for section in result.plan.sections) != (0, 1, 2):
        raise RuntimeError("Explicit section order was not preserved")
    logger.info("Assembly checksum=%s bytes=%d", result.output_checksum, len(result.canonical_text.encode()))

    with tempfile.TemporaryDirectory(prefix="hms_stage7d31_", dir=Path.cwd()) as temp:
        root = Path(temp)
        export_request = NCAssemblyExportRequest(first.project_id, result.result_id, "ASSEMBLY.fn")
        snapshot = NCAssemblyExportSourceSnapshot(1, request, result)
        managed = NCExportService().export_assembly(root, export_request, snapshot)
        if not managed.accepted or managed.artifact is None:
            raise RuntimeError(f"Managed assembly export failed: {managed.diagnostics}")
        managed_bytes = (root / managed.artifact.output_relative_path).read_bytes()
        if managed_bytes != result.canonical_text.encode("utf-8"):
            raise RuntimeError("Managed bytes differ from canonical bytes")
        local = root / "local"
        local_request = replace(
            export_request,
            target=ExportTarget.FILESYSTEM_DIRECTORY,
            target_directory=local,
            create_target_directory=True,
            overwrite_policy=ExportOverwritePolicy.REPLACE_EXPLICIT,
        )
        external = NCExportService().export_assembly(
            root, local_request, snapshot
        )
        if not external.accepted:
            raise RuntimeError(f"Local assembly export failed: {external.diagnostics}")
        logger.info("Managed artifact and local export verified")
        if NCArtifactStore().load(root, first.project_id).entries[0].assembly_result_id != result.result_id:
            raise RuntimeError("Assembly manifest provenance missing")

    changed = replace(
        request,
        operations=(replace(request.operations[0], display_metadata=(("name", "changed"),)), *request.operations[1:]),
    )
    if service.current(changed) is not None:
        raise RuntimeError("Changed source operation was not classified stale")
    tapping = replace(
        request,
        operations=(
            replace(
                request.operations[0],
                source_snapshot=replace(
                    request.operations[0].source_snapshot,
                    operation=replace(
                        request.operations[0].source_snapshot.operation,
                        parameters=replace(
                            request.operations[0].source_snapshot.operation.parameters,
                            strategy_key="tapping_v1",
                        ),
                    ),
                ),
            ),
            *request.operations[1:],
        ),
    )
    tapping_execution = service.assemble(tapping)
    if tapping_execution.status is not ProgramAssemblyStatus.BLOCKED:
        raise RuntimeError("Tapping assembly was not rejected")
    logger.info("Stale guard and Tapping rejection verified; .fn was not opened or run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
