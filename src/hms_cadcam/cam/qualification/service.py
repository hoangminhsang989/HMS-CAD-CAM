"""Application orchestration and Vietnamese-first reporting for Stage18A."""

from __future__ import annotations

from pathlib import Path

from hms_cadcam.cam.post.export_model import NCArtifactManifestEntry
from hms_cadcam.cam.qualification.model import (
    FindingSeverity,
    MachineQualificationContract,
    QualificationLevel,
    QualificationReport,
    QualifiedNCArtifact,
)
from hms_cadcam.cam.qualification.store import QualificationArtifactStore
from hms_cadcam.cam.qualification.validation import (
    StaticQualificationInput,
    qualify_static_nc,
)


_LEVEL_VI = {
    QualificationLevel.UNQUALIFIED: "Chưa xác nhận",
    QualificationLevel.STATICALLY_VALIDATED: "Đạt kiểm tra tĩnh",
    QualificationLevel.DRY_RUN_QUALIFIED: "Đã dry-run",
    QualificationLevel.MACHINE_ACCEPTED: "Đã nghiệm thu trên máy",
}


class MachineQualificationService:
    """Qualify existing assembly bytes, then optionally bind a managed artifact."""

    def __init__(self, store: QualificationArtifactStore | None = None) -> None:
        self._store = store or QualificationArtifactStore()

    def qualify(self, value: StaticQualificationInput) -> QualificationReport:
        """Run pure deterministic static/physical-evidence qualification."""

        return qualify_static_nc(value)

    def publish(
        self,
        project_root: Path,
        managed: NCArtifactManifestEntry,
        value: StaticQualificationInput,
    ) -> QualifiedNCArtifact:
        """Persist provenance only after the exact managed NC is verified."""

        report = self.qualify(value)
        return self._store.save(project_root, managed, report)

    def load(self, project_root: Path) -> tuple[QualifiedNCArtifact, ...]:
        """Reload and verify all additive project qualification records."""

        return self._store.load(project_root)


def qualification_status_vi(report: QualificationReport | None) -> str:
    """Return a truthful concise Vietnamese status without generic Ready."""

    if report is None:
        return "Chưa định danh máy"
    return _LEVEL_VI[report.qualification_level]


def render_qualification_report_vi(
    report: QualificationReport,
    contract: MachineQualificationContract,
) -> str:
    """Render a compact human review report; G/M tokens remain untranslated."""

    errors = tuple(item for item in report.findings if item.severity is FindingSeverity.ERROR)
    warnings = tuple(item for item in report.findings if item.severity is FindingSeverity.WARNING)
    operations = ", ".join(report.operation_ids)
    finding_lines = [
        f"- [{item.severity.value.upper()}] {item.code.value}"
        + (" — " + ", ".join(f"{key}={value}" for key, value in item.evidence) if item.evidence else "")
        for item in report.findings
    ]
    return "\n".join(
        (
            "BÁO CÁO XÁC NHẬN NC",
            f"Máy: {contract.display_name}",
            "Controller: FANUC 31i-B",
            f"Mức xác nhận: {_LEVEL_VI[report.qualification_level]}",
            f"MACHINE_READY: {'true' if report.machine_ready else 'false'}",
            f"Nguồn project: {report.project_id}",
            f"Program fingerprint: {report.program_fingerprint.digest}",
            f"NC SHA-256: {report.nc_sha256}",
            "Post mapping: repository-confirmed ROBODRILL 21i mapping; not physical FANUC 31i-B certification",
            f"Operations: {operations}",
            "Work offset: G54 — physical transform UNVERIFIED",
            "Spindle envelope: 24000 rpm",
            "Feed envelope: 30000 mm/min",
            "Axis spans: X500 / Y400 / Z330 mm",
            "Table envelope: 650 × 400 mm",
            "Offsets: internal mapping checked; controller namespace UNVERIFIED",
            "Drilling: expanded-motion qualification only; canned cycles UNVERIFIED",
            "Tapping: TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED",
            f"Blocking errors: {len(errors)}",
            f"Unverified/warnings: {len(warnings)}",
            "Physical acceptance: NOT_PERFORMED"
            if report.physical_evidence is None
            else f"Physical acceptance: {report.physical_evidence.machine_acceptance.value}",
            "Findings:",
            *finding_lines,
        )
    )


__all__ = [
    "MachineQualificationService",
    "qualification_status_vi",
    "render_qualification_report_vi",
]
