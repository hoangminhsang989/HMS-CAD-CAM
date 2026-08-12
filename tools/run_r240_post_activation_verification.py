"""Build deterministic R240 post-activation evidence without CNC interaction."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from hms_cadcam.cam.post_studio import ActiveLifecyclePaths, ActiveLifecycleService, ManagedActiveStatus
from hms_cadcam.cam.qualification.offline_analyzer import AnalysisPolicy, analyze_nc_bytes
from hms_cadcam.cam.qualification.validation import validate_fanuc_modal_sequence


ACTIVE_SHA = "1160411dea6a5f104085747b4deac151fbd6b103b5930f39b11e8be358b67039"
PARENT_SHA = "d0aa7518d669283be8aad6e92ffdec4dae8785abb7fdb2895cac0ab46cb51da3"
NC_SHA = "1bb0690a9f95e197dd26ead70d4447855ff0a3e1ee6119a85bc96d05847a9f67"
ORIGINAL_NC_SHA = "8ea6a6c432d74581e36d69cd22a43d287cd22345ec2ba402185d5a575af80774"
EVIDENCE_BASE = Path(r"E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE")
R233 = EVIDENCE_BASE / "R233_FANUC_SHL_COMPLETE_CONTEXT_AND_ISOLATED_G40_REMEDIATION"
R239_PHASE2 = EVIDENCE_BASE / "R239_PHASE2_PRODUCTION_ACTIVATION_SANG_HOANG_MINH_20260812"
R239_OWNER = EVIDENCE_BASE / "R239_OWNER_ACTIVATION_WINDOW_SANG_HOANG_MINH_20260812" / "owner-window.HMS" / "post" / "studio" / "production-activation" / "owner-window.r239.fanuc-shl"
REPRO = EVIDENCE_BASE / "R240_ACTIVE_POST_NC_REPRODUCTION"
OUTPUT = EVIDENCE_BASE / "R240_POST_STUDIO_V1_POST_ACTIVATION_VERIFICATION_AND_FORMAL_CLOSURE"
TARGET = Path(r"C:\ProgramData\WORKNC\2021.0\pospro\FANUC-SHL.dat")
BACKUP = Path(r"C:\ProgramData\HMS-CADCAM\PostStudio\production-backups\fanuc-shl\deploy.r239.fanuc-shl.owner-window\fanuc-shl__fanuc-shl.original__d0aa7518d669283be8aad6e92ffdec4dae8785abb7fdb2895cac0ab46cb51da3__deploy.r239.fanuc-shl.owner-window.dat")
LOCK = Path(r"C:\ProgramData\HMS-CADCAM\PostStudio\locks\fanuc-shl-production.lock")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError("R240 closure evidence root already exists")
    OUTPUT.mkdir(parents=True)
    paths = ActiveLifecyclePaths(
        TARGET, R239_PHASE2 / "08_ACTIVATION_RECORD.json", R239_PHASE2 / "09_WINDOW_CONSUMED.json",
        R239_OWNER / "deployment-plan.json", R239_OWNER / "rollback-plan.json", R239_OWNER / "owner-decision.json",
        BACKUP, LOCK,
    )
    service = ActiveLifecycleService()
    target_before = TARGET.read_bytes()
    parent = BACKUP.read_bytes()
    projection = service.reconstruct(paths)
    if not (projection.status is ManagedActiveStatus.ACTIVE_MANAGED_REVISION and projection.active_sha256 == ACTIVE_SHA and projection.backup_sha256 == PARENT_SHA and projection.rollback_ready and projection.window_consumed and not projection.lock_present):
        raise RuntimeError("Real active lifecycle reconciliation failed")
    write_json(OUTPUT / "01_ACTIVE_LIFECYCLE_RECONCILIATION.json", projection.payload())

    fresh_nc_path = REPRO / "ACTIVE_WORKZONE_COPY" / "workzone" / "SHEET" / "260601---BL-CUM-DAN-DONG--25X226_5-L1_01.fn"
    known_nc_path = R233 / "generated_nc" / "260601---BL-CUM-DAN-DONG--25X226_5-L1_R233_G40.fn"
    original_nc_path = R233 / "c2" / "z" / "SHEET" / "260601---BL-CUM-DAN-DONG--25X226_5-L1_01.fn"
    fresh_nc, known_nc, original_nc = fresh_nc_path.read_bytes(), known_nc_path.read_bytes(), original_nc_path.read_bytes()
    if sha(fresh_nc) != NC_SHA or fresh_nc != known_nc or sha(original_nc) != ORIGINAL_NC_SHA:
        raise RuntimeError("Three-way NC identities are not authoritative")
    original_lines = original_nc.decode("ascii").replace("\r\n", "\n").splitlines()
    fresh_lines = fresh_nc.decode("ascii").replace("\r\n", "\n").splitlines()
    additions = [line for line in fresh_lines if fresh_lines.count(line) > original_lines.count(line)]
    if additions != ["G40"] or fresh_lines[-7:] != ["G40", "M09", "M05", "G91G28G0Z0", "G28Y0.", "M30", "%"]:
        raise RuntimeError("Original-to-active NC delta is not the intended G40 correction")
    write_json(OUTPUT / "02_THREE_WAY_NC_PROOF.json", {
        "format": "HMS_R240_THREE_WAY_NC_PROOF", "format_version": 1,
        "original": {"path": str(original_nc_path), "size": len(original_nc), "sha256": sha(original_nc)},
        "r233": {"path": str(known_nc_path), "size": len(known_nc), "sha256": sha(known_nc)},
        "active_generated": {"path": str(fresh_nc_path), "size": len(fresh_nc), "sha256": sha(fresh_nc)},
        "original_to_r233": {"result": "EXPECTED_G40_REMEDIATION_ONLY", "added": additions, "position": "before M09/M05/G91G28G0Z0"},
        "r233_to_active": "BYTE_IDENTICAL", "marker": "ACTIVE_POST_REPRODUCES_R233_NC_EXACTLY",
    })

    analysis = analyze_nc_bytes(fresh_nc, AnalysisPolicy(expected_tool_numbers=(1,)))
    modal = validate_fanuc_modal_sequence(fresh_nc.decode("ascii"))
    blocker_count = analysis.risk_summary["blockers"]
    sequence_valid = any(item.code == "POST_SEQUENCE_VALID" for item in analysis.findings)
    if blocker_count != 0 or analysis.risk_summary["unresolved_blocks"] != 0 or not sequence_valid:
        raise RuntimeError("Fresh active NC static validation failed")
    write_json(OUTPUT / "03_FRESH_STATIC_SEMANTIC_VALIDATION.json", {
        "format": "HMS_R240_FRESH_STATIC_SEMANTIC_VALIDATION", "format_version": 1,
        "nc_sha256": analysis.nc_sha256, "risk_summary": analysis.risk_summary,
        "post_sequence": "POST_SEQUENCE_VALID", "candidate_induced_blocker_count": blocker_count,
        "findings": [{"code": item.code, "severity": item.severity.value, "line": item.block_line} for item in analysis.findings],
        "modal_findings": [{"code": item.code.value, "severity": item.severity.value} for item in modal],
        "physical_warnings_preserved": True, "level2": "NOT_ACHIEVED", "level3": "NOT_ACHIEVED", "machine_ready": False,
    })

    sandbox = OUTPUT / "rollback-sandbox"
    sandbox.mkdir()
    sandbox_target = sandbox / "FANUC-SHL.dat"
    sandbox_target.write_bytes(target_before)
    start_sha = sha(sandbox_target.read_bytes())
    sandbox_target.write_bytes(parent)
    rollback_sha = sha(sandbox_target.read_bytes())
    sandbox_target.write_bytes(target_before)
    reactivate_sha = sha(sandbox_target.read_bytes())
    if (start_sha, rollback_sha, reactivate_sha) != (ACTIVE_SHA, PARENT_SHA, ACTIVE_SHA):
        raise RuntimeError("Isolated rollback rehearsal failed")
    write_json(OUTPUT / "04_ISOLATED_ROLLBACK_REHEARSAL.json", {
        "format": "HMS_R240_ISOLATED_ROLLBACK_REHEARSAL", "format_version": 1,
        "sandbox_target": str(sandbox_target), "start_sha256": start_sha,
        "rollback_sha256": rollback_sha, "reactivated_sha256": reactivate_sha,
        "real_target_touched": False, "result": "PASS_ACTIVE_TO_PARENT_TO_ACTIVE_SANDBOX_ONLY",
    })

    validation = json.loads((OUTPUT / "03_FRESH_STATIC_SEMANTIC_VALIDATION.json").read_text(encoding="utf-8"))
    regression = json.loads((R233 / "05_STATIC_AND_REGRESSION.json").read_text(encoding="utf-8"))["regression"]
    package = service.export_active_history(OUTPUT / "fanuc-shl-r233-active-history.zip", projection=projection, paths=paths, original_bytes=parent, active_bytes=target_before, validation=validation, regression=regression)
    imported = service.import_active_history(Path(str(package["path"])))
    if imported.auto_activate or not imported.informational_only or TARGET.read_bytes() != target_before:
        raise RuntimeError("Active history import altered production state")
    write_json(OUTPUT / "05_PACKAGE_IMPORT_VERIFICATION.json", {
        "format": "HMS_R240_PACKAGE_IMPORT_VERIFICATION", "format_version": 1,
        "package_sha256": package["sha256"], "manifest": package["manifest"],
        "imported": {"post_id": imported.post_id, "active_revision_id": imported.active_revision_id,
                     "active_sha256": imported.active_sha256, "previous_sha256": imported.previous_sha256,
                     "deployment_id": imported.deployment_id, "backup_sha256": imported.backup_sha256,
                     "informational_only": imported.informational_only, "auto_activate": imported.auto_activate,
                     "requires_local_reconciliation_and_approval": imported.requires_local_reconciliation_and_approval},
        "production_target_unchanged_by_import": True,
    })

    if TARGET.read_bytes() != target_before or sha(TARGET.read_bytes()) != ACTIVE_SHA:
        raise RuntimeError("Production Post changed during R240 verification")
    shutil.copy2(REPRO / "INVOCATION.json", OUTPUT / "00_WORKNC_INVOCATION.json")
    write_json(OUTPUT / "06_TERMINAL.json", {
        "format": "HMS_R240_POST_ACTIVATION_TERMINAL", "format_version": 1,
        "verdict": "PASS_R240_POST_STUDIO_V1_POST_ACTIVATION_VERIFICATION_AND_FORMAL_CLOSURE",
        "active_state": projection.status.value, "active_sha256": ACTIVE_SHA, "backup_sha256": PARENT_SHA,
        "rollback": "ROLLBACK_READY", "window": "CONSUMED", "fresh_nc_sha256": NC_SHA,
        "nc_comparison": "BYTE_IDENTICAL", "validation": "POST_SEQUENCE_VALID", "candidate_induced_blockers": 0,
        "production_post_unchanged": True, "cnc_action": False,
        "closure": "POST_PROCESSOR_STUDIO_V1_COMPLETE",
        "next_stage": "MACHINING_SIMULATION_AND_DIGITAL_VERIFICATION",
    })
    files = []
    for path in sorted(item for item in OUTPUT.rglob("*") if item.is_file() and item.name != "PACKAGE_MANIFEST.json"):
        data = path.read_bytes()
        files.append({"path": path.relative_to(OUTPUT).as_posix(), "size": len(data), "sha256": sha(data)})
    write_json(OUTPUT / "PACKAGE_MANIFEST.json", {
        "format": "HMS_R240_POST_ACTIVATION_CLOSURE_EVIDENCE", "format_version": 1, "files": files,
        "active_target": {"path": str(TARGET), "size": len(target_before), "sha256": ACTIVE_SHA},
        "backup": {"path": str(BACKUP), "size": len(parent), "sha256": PARENT_SHA},
        "self_excluded": True,
    })
    print("PASS_R240_POST_STUDIO_V1_POST_ACTIVATION_VERIFICATION_AND_FORMAL_CLOSURE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
