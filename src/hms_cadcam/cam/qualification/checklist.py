"""Reusable ROBODRILL physical checklist and explicit golden-sample approval."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.evidence_model import (
    EvidenceState,
    OwnerAcceptanceRecord,
)
from hms_cadcam.cam.qualification.model import SampleAuthority


@dataclass(frozen=True, slots=True)
class PhysicalChecklistItem:
    key: str
    label: str
    required: bool = True
    state: EvidenceState = EvidenceState.NOT_PERFORMED
    notes: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise CamValidationError("Checklist key is invalid")
        if not isinstance(self.label, str) or not self.label.strip():
            raise CamValidationError("Checklist label is invalid")
        if type(self.required) is not bool:
            raise CamValidationError("Checklist required flag is invalid")
        if self.state not in {
            EvidenceState.NOT_PERFORMED, EvidenceState.PENDING,
            EvidenceState.PASS, EvidenceState.FAIL,
        }:
            raise CamValidationError("Checklist state is invalid")
        if not isinstance(self.notes, str):
            raise CamValidationError("Checklist notes are invalid")

    def with_result(self, state: EvidenceState, notes: str = "") -> "PhysicalChecklistItem":
        return replace(self, state=state, notes=notes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "required": self.required,
            "state": self.state.value, "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PhysicalChecklistItem":
        fields = {"key", "label", "required", "state", "notes"}
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Checklist item payload is malformed")
        try:
            state = EvidenceState(data["state"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Checklist item state is invalid") from error
        return cls(data["key"], data["label"], data["required"], state, data["notes"])


ROBODRILL_CHECKLIST_KEYS = (
    "machine_identity", "tool_setup", "offsets", "work_origin", "stock",
    "fixture", "spindle", "coolant", "safe_retract", "tool_change",
    "first_motion", "drilling", "end_sequence",
)


@dataclass(frozen=True, slots=True)
class RobodrillPhysicalChecklist:
    items: tuple[PhysicalChecklistItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or tuple(item.key for item in self.items) != ROBODRILL_CHECKLIST_KEYS:
            raise CamInvariantError("ROBODRILL checklist keys/order are invalid")
        if any(not isinstance(item, PhysicalChecklistItem) for item in self.items):
            raise CamValidationError("ROBODRILL checklist item is invalid")

    @classmethod
    def default(cls) -> "RobodrillPhysicalChecklist":
        labels = (
            "Machine identity", "Tool setup", "Offsets", "Work origin", "Stock",
            "Fixture", "Spindle", "Coolant", "Safe retract", "Tool change",
            "First motion", "Drilling", "End sequence",
        )
        return cls(tuple(PhysicalChecklistItem(key, label) for key, label in zip(ROBODRILL_CHECKLIST_KEYS, labels)))

    @property
    def complete(self) -> bool:
        return all(
            not item.required or item.state is EvidenceState.PASS for item in self.items
        )

    @property
    def failed(self) -> bool:
        return any(item.state is EvidenceState.FAIL for item in self.items)

    def with_result(self, key: str, state: EvidenceState, notes: str = "") -> "RobodrillPhysicalChecklist":
        if key not in ROBODRILL_CHECKLIST_KEYS:
            raise CamValidationError("Unknown ROBODRILL checklist key")
        return RobodrillPhysicalChecklist(
            tuple(item.with_result(state, notes) if item.key == key else item for item in self.items)
        )

    def to_dict(self) -> dict[str, Any]:
        return {"format": "HMS_STAGE18A_ROBODRILL_PHYSICAL_CHECKLIST", "format_version": 1, "items": [item.to_dict() for item in self.items]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RobodrillPhysicalChecklist":
        if (
            not isinstance(data, dict)
            or set(data) != {"format", "format_version", "items"}
            or data["format"] != "HMS_STAGE18A_ROBODRILL_PHYSICAL_CHECKLIST"
            or data["format_version"] != 1
            or not isinstance(data["items"], list)
        ):
            raise CamValidationError("ROBODRILL checklist payload is malformed")
        return cls(tuple(PhysicalChecklistItem.from_dict(item) for item in data["items"]))


@dataclass(frozen=True, slots=True)
class GoldenSampleApproval:
    sample_id: str
    source_authority: SampleAuthority
    target_authority: SampleAuthority
    owner_acceptance: OwnerAcceptanceRecord
    approved_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.sample_id, str) or not self.sample_id.strip():
            raise CamValidationError("Golden sample ID is invalid")
        if self.source_authority is not SampleAuthority.ENGINEERING_REGRESSION_SAMPLE:
            raise CamInvariantError("Only engineering samples can enter owner approval")
        if self.target_authority is not SampleAuthority.OWNER_APPROVED_MACHINE_SAMPLE:
            raise CamInvariantError("Golden sample target authority is invalid")
        if self.owner_acceptance.result is not EvidenceState.PASS or not self.owner_acceptance.product_owner:
            raise CamInvariantError("Owner approval must be attributable and PASS")
        if not isinstance(self.approved_at, str) or not self.approved_at.strip():
            raise CamValidationError("Golden sample approval timestamp is invalid")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "source_authority": self.source_authority.value,
            "target_authority": self.target_authority.value,
            "owner_acceptance": self.owner_acceptance.to_dict(),
            "approved_at": self.approved_at,
        }


__all__ = [
    "GoldenSampleApproval", "PhysicalChecklistItem", "ROBODRILL_CHECKLIST_KEYS",
    "RobodrillPhysicalChecklist",
]
