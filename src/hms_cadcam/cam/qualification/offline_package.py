"""Deterministic controlled dry-run handoff package creation and validation."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.model import (
    MachineQualificationContract,
    canonical_json_bytes,
    sha256_bytes,
)
from hms_cadcam.cam.qualification.offline_model import (
    NCReleaseCandidate,
    OfflineNCVerificationSession,
    OperatorAcknowledgement,
    OperatorReview,
    PackageStatus,
    ReleaseAssessment,
    ReleaseState,
)
from hms_cadcam.cam.qualification.offline_reports import (
    operation_summary,
    render_setup_sheet_vi,
    render_tool_list_csv,
    verification_report_payload,
)
from hms_cadcam.cam.qualification.physical_model import MachineSetupQualification


LOGGER = logging.getLogger(__name__)
PACKAGE_FORMAT = "DRY_RUN_HANDOFF_PACKAGE"
MANIFEST_NAME = "package-manifest.json"
MANIFEST_SIDECAR = "package-manifest.json.sha256"


class HandoffPackageError(RuntimeError):
    """Raised when package bytes or inventory cannot be trusted."""


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.r223.tmp")
    if temporary.exists():
        raise HandoffPackageError(f"Stale temporary file exists for {path.name}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != payload:
            raise HandoffPackageError(f"Read-back mismatch for {path.name}")
        os.replace(temporary, path)
    except OSError as error:
        raise HandoffPackageError(f"Could not write {path.name}") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not remove R223 temporary file", exc_info=True)


def _package_identity(
    session: OfflineNCVerificationSession,
    candidate: NCReleaseCandidate,
    review: OperatorReview,
    acknowledgement: OperatorAcknowledgement,
    assessment: ReleaseAssessment,
) -> ContentFingerprint:
    return ContentFingerprint.from_payload(
        {
            "format": PACKAGE_FORMAT,
            "session": session.session_fingerprint.to_dict(),
            "candidate": candidate.candidate_fingerprint.to_dict(),
            "review": review.fingerprint.to_dict(),
            "acknowledgement": acknowledgement.fingerprint.to_dict(),
            "assessment": assessment.to_dict(),
        }
    )


def _machine_summary(contract: MachineQualificationContract, session: OfflineNCVerificationSession) -> dict[str, Any]:
    return {
        "machine_profile_id": contract.profile_id,
        "display_name": contract.display_name,
        "controller": "FANUC 31i-B",
        "machine_profile_fingerprint": contract.fingerprint.to_dict(),
        "controller_contract": session.controller_contract,
        "physical_machine_endpoint_limits": "UNVERIFIED",
        "machine_ready": False,
    }


def _checklist(package_id: ContentFingerprint, candidate: NCReleaseCandidate) -> str:
    return "\n".join(
        (
            "# CHECKLIST CHẠY THỬ NGOÀI — KHÔNG ĐIỀU KHIỂN CNC",
            "",
            f"- Package ID: {package_id.digest}",
            f"- NC SHA-256: {candidate.nc_sha256}",
            "- [ ] Đối chiếu đúng máy ROBODRILL α-D21MiB / FANUC 31i-B / BT30",
            "- [ ] Đối chiếu đúng NC SHA-256",
            "- [ ] Xác minh G54, phôi, đồ gá, Tool và Holder",
            "- [ ] Xác minh hành trình vật lý và vị trí thay Tool",
            "- [ ] Chạy graphics/single-block/dry-run theo thẩm quyền bên ngoài",
            "- [ ] Ghi bằng chứng Level2 với đúng Package ID và NC SHA",
            "",
            "Chương trình này mới đạt kiểm tra phần mềm và chưa được nghiệm thu trên máy.",
            "Việc xuất gói chạy thử không đồng nghĩa MACHINE READY.",
            "MACHINE_READY: KHÔNG",
            "",
        )
    )


class DryRunHandoffPackageBuilder:
    """Build and independently validate one closed deterministic inventory."""

    def build(
        self,
        target: Path,
        *,
        project_name: str,
        program_name: str,
        nc_filename: str,
        nc_bytes: bytes,
        contract: MachineQualificationContract,
        setup: MachineSetupQualification,
        session: OfflineNCVerificationSession,
        candidate: NCReleaseCandidate,
        review: OperatorReview,
        acknowledgement: OperatorAcknowledgement,
        assessment: ReleaseAssessment,
    ) -> tuple[Path, str]:
        if not isinstance(target, Path):
            raise TypeError("target must be pathlib.Path")
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise HandoffPackageError("Package target must be absent or an empty directory")
        if assessment.state is not ReleaseState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF:
            raise HandoffPackageError("Release assessment does not authorize handoff packaging")
        if sha256_bytes(nc_bytes) != candidate.nc_sha256:
            raise HandoffPackageError("NC bytes do not match release candidate")
        if review.release_candidate_fingerprint != candidate.candidate_fingerprint:
            raise HandoffPackageError("Operator review is stale")
        if acknowledgement.release_candidate_fingerprint != candidate.candidate_fingerprint:
            raise HandoffPackageError("Operator acknowledgement is stale")
        if Path(nc_filename).name != nc_filename or Path(nc_filename).suffix.casefold() not in {".nc", ".fn"}:
            raise HandoffPackageError("NC filename is invalid")
        target.mkdir(parents=True, exist_ok=True)
        package_id = _package_identity(session, candidate, review, acknowledgement, assessment)
        release_identity = {
            "format": "HMS_STAGE18A_TRANCHE3_RELEASE_IDENTITY", "format_version": 1,
            "package_id": package_id.to_dict(), "candidate": candidate.to_dict(),
            "operator_review": review.to_dict(), "operator_acknowledgement": acknowledgement.to_dict(),
            "release_assessment": assessment.to_dict(), "level2_achieved": False,
            "level3_achieved": False, "machine_ready": False,
        }
        level2_template = {
            "format": "HMS_STAGE18A_LEVEL2_EVIDENCE_INTAKE_TEMPLATE", "format_version": 1,
            "release_candidate_fingerprint": candidate.candidate_fingerprint.to_dict(),
            "handoff_package_id": package_id.to_dict(), "nc_sha256": candidate.nc_sha256,
            "setup_fingerprint": candidate.setup_fingerprint.to_dict(),
            "machine_profile_fingerprint": candidate.machine_profile_fingerprint.to_dict(),
            "physical_evidence": None, "level2_achieved": False, "machine_ready": False,
        }
        contents: dict[str, tuple[str, bytes]] = {
            nc_filename: ("EXACT_NC", nc_bytes),
            f"{nc_filename}.sha256": ("NC_SHA256", f"{candidate.nc_sha256}  {nc_filename}\n".encode("utf-8")),
            "machine-profile-summary.json": ("MACHINE_PROFILE", canonical_json_bytes(_machine_summary(contract, session))),
            "setup-sheet.vi.md": ("SETUP_SHEET", render_setup_sheet_vi(
                project_name=project_name, program_name=program_name, candidate=candidate,
                session=session, setup=setup, contract=contract,
            ).encode("utf-8")),
            "tool-list.csv": ("TOOL_LIST", render_tool_list_csv(setup, session).encode("utf-8")),
            "operation-summary.json": ("OPERATION_SUMMARY", canonical_json_bytes({
                "format": "HMS_STAGE18A_OPERATION_SUMMARY", "format_version": 1,
                "operations": operation_summary(session), "machining_time": "Chưa xác minh",
            })),
            "static-verification-report.json": ("STATIC_VERIFICATION", canonical_json_bytes(verification_report_payload(session))),
            "finding-manifest.json": ("FINDINGS", canonical_json_bytes({
                "format": "HMS_STAGE18A_STATIC_FINDING_MANIFEST", "format_version": 1,
                "findings": [item.to_dict() for item in session.findings],
            })),
            "release-identity.json": ("RELEASE_IDENTITY", canonical_json_bytes(release_identity)),
            "dry-run-checklist.md": ("DRY_RUN_CHECKLIST", _checklist(package_id, candidate).encode("utf-8")),
            "level2-evidence-intake-template.json": ("LEVEL2_EVIDENCE_INTAKE", canonical_json_bytes(level2_template)),
        }
        for relative, (_role, payload) in sorted(contents.items()):
            _atomic_write(target / relative, payload)
        entries = [
            {"path": path, "role": role, "size": len(payload), "sha256": sha256_bytes(payload)}
            for path, (role, payload) in sorted(contents.items())
        ]
        manifest = {
            "format": PACKAGE_FORMAT, "format_version": 1,
            "package_id": package_id.to_dict(),
            "status": PackageStatus.RELEASED_FOR_EXTERNAL_DRY_RUN.value,
            "inventory_policy": "EXACT_NO_UNEXPECTED_FILES",
            "entries": entries,
            "level2_achieved": False, "level3_achieved": False, "machine_ready": False,
        }
        manifest_bytes = canonical_json_bytes(manifest)
        manifest_sha = sha256_bytes(manifest_bytes)
        _atomic_write(target / MANIFEST_NAME, manifest_bytes)
        _atomic_write(target / MANIFEST_SIDECAR, f"{manifest_sha}  {MANIFEST_NAME}\n".encode("utf-8"))
        self.validate(target)
        return target, manifest_sha

    def validate(self, root: Path) -> dict[str, Any]:
        if not isinstance(root, Path) or not root.is_dir():
            raise HandoffPackageError("Package root is invalid")
        manifest_path = root / MANIFEST_NAME
        sidecar_path = root / MANIFEST_SIDECAR
        try:
            manifest_bytes = manifest_path.read_bytes()
            expected_sidecar = f"{sha256_bytes(manifest_bytes)}  {MANIFEST_NAME}\n".encode("utf-8")
            if sidecar_path.read_bytes() != expected_sidecar:
                raise HandoffPackageError("Package manifest sidecar mismatch")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HandoffPackageError("Package manifest is unreadable") from error
        required = {
            "format", "format_version", "package_id", "status", "inventory_policy",
            "entries", "level2_achieved", "level3_achieved", "machine_ready",
        }
        if (
            not isinstance(manifest, dict) or set(manifest) != required
            or manifest["format"] != PACKAGE_FORMAT or manifest["format_version"] != 1
            or manifest["status"] != PackageStatus.RELEASED_FOR_EXTERNAL_DRY_RUN.value
            or manifest["level2_achieved"] or manifest["level3_achieved"] or manifest["machine_ready"]
            or not isinstance(manifest["entries"], list)
        ):
            raise HandoffPackageError("Package manifest is malformed")
        expected = {MANIFEST_NAME, MANIFEST_SIDECAR}
        seen: set[str] = set()
        for entry in manifest["entries"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "role", "size", "sha256"}:
                raise HandoffPackageError("Package manifest entry is malformed")
            relative = entry["path"]
            if not isinstance(relative, str) or Path(relative).name != relative or relative in seen:
                raise HandoffPackageError("Package inventory path is invalid")
            seen.add(relative)
            expected.add(relative)
            path = root / relative
            try:
                if path.is_symlink() or not path.is_file():
                    raise HandoffPackageError(f"Package file is missing: {relative}")
                payload = path.read_bytes()
            except OSError as error:
                raise HandoffPackageError(f"Package file is unreadable: {relative}") from error
            if len(payload) != entry["size"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise HandoffPackageError(f"Package file checksum mismatch: {relative}")
        actual = {item.name for item in root.iterdir()}
        if actual != expected:
            raise HandoffPackageError("Package has missing or unexpected files")
        return manifest


def package_stale_reasons(
    candidate: NCReleaseCandidate,
    *,
    nc_sha256: str,
    setup_fingerprint: ContentFingerprint,
    tool_set_fingerprint: ContentFingerprint,
    machine_profile_fingerprint: ContentFingerprint,
    post_fingerprint: ContentFingerprint,
) -> tuple[str, ...]:
    checks = (
        (candidate.nc_sha256, nc_sha256, "NC_CHANGED"),
        (candidate.setup_fingerprint, setup_fingerprint, "SETUP_CHANGED"),
        (candidate.tool_set_fingerprint, tool_set_fingerprint, "TOOL_SET_CHANGED"),
        (candidate.machine_profile_fingerprint, machine_profile_fingerprint, "MACHINE_CHANGED"),
        (candidate.post_fingerprint, post_fingerprint, "POST_CHANGED"),
    )
    return tuple(code for frozen, current, code in checks if frozen != current)


__all__ = [
    "DryRunHandoffPackageBuilder", "HandoffPackageError", "MANIFEST_NAME",
    "MANIFEST_SIDECAR", "PACKAGE_FORMAT", "package_stale_reasons",
]
