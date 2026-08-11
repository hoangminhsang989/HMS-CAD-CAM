"""Application service for the external Stage18A Level2 evidence workflow."""

from __future__ import annotations

from pathlib import Path

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.evidence_model import (
    DryRunQualificationEvidence,
    EvidenceAttachment,
    EvidenceAttachmentRole,
    Level2QualificationRecord,
    Level2Readiness,
    assess_level2_readiness,
)
from hms_cadcam.cam.qualification.model import QualificationReport
from hms_cadcam.cam.qualification.physical_model import PhysicalReadinessResult
from hms_cadcam.cam.qualification.tranche2_store import Tranche2QualificationStore


class Tranche2QualificationService:
    """Coordinate typed entry/import, persistence, and derived promotion state."""

    def __init__(self, store: Tranche2QualificationStore | None = None) -> None:
        self._store = store or Tranche2QualificationStore()

    def import_attachment(
        self,
        path: Path,
        *,
        role: EvidenceAttachmentRole,
        captured_at: str,
        provenance: str,
    ) -> EvidenceAttachment:
        """Hash an external evidence file without communicating with a CNC."""

        return EvidenceAttachment.from_local_file(
            path, role=role, captured_at=captured_at, provenance=provenance
        )

    def append_attempt(
        self,
        record: Level2QualificationRecord,
        attempt: DryRunQualificationEvidence,
    ) -> Level2QualificationRecord:
        """Return a new record while preserving every prior attempt."""

        return record.append_attempt(attempt)

    def assess(
        self,
        *,
        level1_report: QualificationReport,
        record: Level2QualificationRecord,
        physical_readiness: PhysicalReadinessResult,
        current_nc_sha256: str,
        current_machine_profile_fingerprint: ContentFingerprint,
        current_post_fingerprint: ContentFingerprint,
        current_qualification_contract_fingerprint: ContentFingerprint,
    ) -> Level2Readiness:
        """Run the sole programmatic Level2 promotion gate."""

        return assess_level2_readiness(
            level1_report=level1_report,
            record=record,
            physical_readiness=physical_readiness,
            current_nc_sha256=current_nc_sha256,
            current_machine_profile_fingerprint=current_machine_profile_fingerprint,
            current_post_fingerprint=current_post_fingerprint,
            current_qualification_contract_fingerprint=current_qualification_contract_fingerprint,
        )

    def save(self, project_root: Path, record: Level2QualificationRecord) -> Level2QualificationRecord:
        """Persist one immutable additive snapshot below ``post/qualification``."""

        return self._store.save(project_root, record)

    def load(self, project_root: Path) -> tuple[Level2QualificationRecord, ...]:
        return self._store.load(project_root)

    def export_package(
        self,
        record: Level2QualificationRecord,
        target: Path,
    ) -> tuple[Path, str]:
        return self._store.export_package(record, target)


__all__ = ["Tranche2QualificationService"]
