"""Typed Stage18A machine qualification contracts and immutable results."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hms_cadcam.cam.domain.errors import (
    CamInvariantError,
    CamValidationError,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint


QUALIFICATION_CONTRACT_VERSION = 1
QUALIFICATION_REPORT_VERSION = 1
QUALIFICATION_CONTRACT_FORMAT = "HMS_STAGE18A_MACHINE_QUALIFICATION_CONTRACT"
QUALIFICATION_REPORT_FORMAT = "HMS_STAGE18A_NC_QUALIFICATION_REPORT"
QUALIFIED_ARTIFACT_FORMAT = "HMS_STAGE18A_QUALIFIED_NC_ARTIFACT"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class AuthorityClass(StrEnum):
    OWNER_CONFIRMED = "owner_confirmed"
    CATALOG_CONFIRMED = "catalog_confirmed"
    REPOSITORY_CONFIRMED = "repository_confirmed"
    PHYSICAL_TEST_CONFIRMED = "physical_test_confirmed"
    UNVERIFIED = "unverified"


class QualificationState(StrEnum):
    PROFILE_DEFINED = "profile_defined"
    CONFIRMED = "confirmed"
    UNVERIFIED = "unverified"
    NOT_QUALIFIED = "not_qualified"
    NOT_SUPPORTED = "not_supported"


class QualificationLevel(StrEnum):
    UNQUALIFIED = "unqualified"
    STATICALLY_VALIDATED = "statically_validated"
    DRY_RUN_QUALIFIED = "dry_run_qualified"
    MACHINE_ACCEPTED = "machine_accepted"


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FindingCode(StrEnum):
    PROFILE_MISMATCH = "qualification.profile_mismatch"
    STALE_MACHINE_QUALIFICATION = "qualification.stale_machine"
    SOURCE_STALE = "qualification.source_stale"
    NC_CHECKSUM_MISMATCH = "qualification.nc_checksum_mismatch"
    POST_IDENTITY_MISMATCH = "qualification.post_identity_mismatch"
    X_SPAN_EXCEEDED = "qualification.x_span_exceeded"
    Y_SPAN_EXCEEDED = "qualification.y_span_exceeded"
    Z_SPAN_EXCEEDED = "qualification.z_span_exceeded"
    PHYSICAL_TRAVEL_NOT_FULLY_VERIFIED = "qualification.physical_travel_unverified"
    STOCK_ENVELOPE_MISSING = "qualification.stock_envelope_missing"
    STOCK_EXCEEDS_TABLE = "qualification.stock_exceeds_table"
    TABLE_PLACEMENT_NOT_PHYSICALLY_VERIFIED = "qualification.table_placement_unverified"
    SPINDLE_INVALID = "qualification.spindle_invalid"
    SPINDLE_LIMIT_EXCEEDED = "qualification.spindle_limit_exceeded"
    FEED_INVALID = "qualification.feed_invalid"
    FEED_LIMIT_EXCEEDED = "qualification.feed_limit_exceeded"
    TOOL_INPUT_MISSING = "qualification.tool_input_missing"
    TOOL_FINGERPRINT_STALE = "qualification.tool_fingerprint_stale"
    TOOL_NUMBER_INVALID = "qualification.tool_number_invalid"
    TOOL_NUMBER_CONFLICT = "qualification.tool_number_conflict"
    TOOL_CAPACITY_EXCEEDED = "qualification.tool_capacity_exceeded"
    TOOL_DIAMETER_EXCEEDED = "qualification.tool_diameter_exceeded"
    TOOL_LENGTH_EXCEEDED = "qualification.tool_length_exceeded"
    TOOL_TAPER_MISMATCH = "qualification.tool_taper_mismatch"
    TOOL_NUMBER_MAPPING_VALIDATED = "qualification.tool_number_mapping_validated"
    H_MAPPING_STATICALLY_VALIDATED = "qualification.h_mapping_validated"
    D_MAPPING_STATICALLY_VALIDATED = "qualification.d_mapping_validated"
    H_MAPPING_MISSING = "qualification.h_mapping_missing"
    H_MAPPING_CONFLICT = "qualification.h_mapping_conflict"
    D_MAPPING_MISSING = "qualification.d_mapping_missing"
    D_MAPPING_CONFLICT = "qualification.d_mapping_conflict"
    OFFSET_NAMESPACE_UNVERIFIED = "qualification.offset_namespace_unverified"
    WORK_OFFSET_UNSUPPORTED = "qualification.work_offset_unsupported"
    PHYSICAL_G54_TRANSFORM_UNVERIFIED = "qualification.g54_transform_unverified"
    COOLANT_PHYSICAL_STATE_UNVERIFIED = "qualification.coolant_physical_unverified"
    POST_SEQUENCE_INVALID = "qualification.post_sequence_invalid"
    POST_SEQUENCE_VALID = "qualification.post_sequence_valid"
    PHYSICAL_SAFE_POSITION_UNVERIFIED = "qualification.safe_position_unverified"
    UNVERIFIED_CONTROLLER_SEMANTICS = "qualification.controller_semantics_unverified"
    CANNED_CYCLE_SUBSTITUTION_UNQUALIFIED = "qualification.canned_cycle_unqualified"
    TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED = "qualification.tapping_unqualified"
    PHYSICAL_EVIDENCE_STALE = "qualification.physical_evidence_stale"
    PHYSICAL_EVIDENCE_INCOMPLETE = "qualification.physical_evidence_incomplete"
    GOLDEN_SAMPLE_OWNER_APPROVAL_PENDING = "qualification.golden_owner_pending"


class EvidenceResult(StrEnum):
    NOT_PERFORMED = "not_performed"
    PASS = "pass"
    FAIL = "fail"
    NOT_AUTHORIZED = "not_authorized"


class SampleAuthority(StrEnum):
    ENGINEERING_REGRESSION_SAMPLE = "engineering_regression_sample"
    OWNER_APPROVED_MACHINE_SAMPLE = "owner_approved_machine_sample"


def _text(value: str, name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CamValidationError(f"{name} is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CamValidationError(f"{name} contains control characters")
    return value.strip()


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CamValidationError(f"{name} is invalid")
    return value


def _finite(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"{name} is invalid")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise CamValidationError(f"{name} is invalid")
    return result


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """Return deterministic UTF-8 JSON bytes without timestamps or locale drift."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    reference_id: str
    authority: AuthorityClass
    sha256: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_id", _text(self.reference_id, "Evidence reference"))
        if not isinstance(self.authority, AuthorityClass):
            raise CamValidationError("Evidence authority is invalid")
        if self.sha256 is not None:
            object.__setattr__(self, "sha256", _sha256(self.sha256, "Evidence SHA-256"))
        if self.notes is not None:
            object.__setattr__(self, "notes", _text(self.notes, "Evidence notes", maximum=2048))

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "authority": self.authority.value,
            "sha256": self.sha256,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceReference":
        if not isinstance(data, dict) or set(data) != {
            "reference_id", "authority", "sha256", "notes"
        }:
            raise CamValidationError("Evidence reference payload is malformed")
        try:
            authority = AuthorityClass(data["authority"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Evidence authority payload is invalid") from error
        return cls(data["reference_id"], authority, data["sha256"], data["notes"])


@dataclass(frozen=True, slots=True)
class QualifiedLeaf:
    key: str
    value: JsonValue
    unit: str | None
    sources: tuple[EvidenceReference, ...]
    authority: AuthorityClass
    state: QualificationState
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or _KEY.fullmatch(self.key) is None:
            raise CamValidationError("Qualified leaf key is invalid")
        if self.unit is not None:
            object.__setattr__(self, "unit", _text(self.unit, "Qualified leaf unit", maximum=32))
        if not isinstance(self.sources, tuple) or any(
            not isinstance(item, EvidenceReference) for item in self.sources
        ):
            raise CamValidationError("Qualified leaf sources are invalid")
        if len({item.reference_id for item in self.sources}) != len(self.sources):
            raise CamInvariantError("Qualified leaf sources must be unique")
        if not isinstance(self.authority, AuthorityClass) or not isinstance(
            self.state, QualificationState
        ):
            raise CamValidationError("Qualified leaf authority/state is invalid")
        if self.value is not None and self.authority is AuthorityClass.UNVERIFIED:
            if self.state not in {QualificationState.UNVERIFIED, QualificationState.NOT_QUALIFIED}:
                raise CamInvariantError("Unverified value cannot be confirmed")
        if self.value is None and self.state is QualificationState.CONFIRMED:
            raise CamInvariantError("Missing value cannot be confirmed")
        if self.notes is not None:
            object.__setattr__(self, "notes", _text(self.notes, "Qualified leaf notes", maximum=2048))
        canonical_json_bytes({"value": self.value})

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "unit": self.unit,
            "sources": [item.to_dict() for item in self.sources],
            "authority": self.authority.value,
            "state": self.state.value,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QualifiedLeaf":
        fields = {"key", "value", "unit", "sources", "authority", "state", "notes"}
        if not isinstance(data, dict) or set(data) != fields or not isinstance(data["sources"], list):
            raise CamValidationError("Qualified leaf payload is malformed")
        try:
            authority = AuthorityClass(data["authority"])
            state = QualificationState(data["state"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Qualified leaf enum payload is invalid") from error
        return cls(
            data["key"], data["value"], data["unit"],
            tuple(EvidenceReference.from_dict(item) for item in data["sources"]),
            authority, state, data["notes"],
        )


@dataclass(frozen=True, slots=True)
class MachineIdentity:
    manufacturer: QualifiedLeaf
    model: QualifiedLeaf
    family: QualifiedLeaf
    machine_type: QualifiedLeaf

    def leaves(self) -> tuple[QualifiedLeaf, ...]:
        return (self.manufacturer, self.model, self.family, self.machine_type)


@dataclass(frozen=True, slots=True)
class ControllerIdentity:
    family: QualifiedLeaf
    model: QualifiedLeaf
    software_revision: QualifiedLeaf
    option_set: QualifiedLeaf

    def leaves(self) -> tuple[QualifiedLeaf, ...]:
        return (self.family, self.model, self.software_revision, self.option_set)


@dataclass(frozen=True, slots=True)
class MachineAxes:
    x_travel_span: QualifiedLeaf
    y_travel_span: QualifiedLeaf
    z_travel_span: QualifiedLeaf
    reference_behavior: QualifiedLeaf
    coordinate_endpoints: QualifiedLeaf

    def leaves(self) -> tuple[QualifiedLeaf, ...]:
        return (
            self.x_travel_span, self.y_travel_span, self.z_travel_span,
            self.reference_behavior, self.coordinate_endpoints,
        )


@dataclass(frozen=True, slots=True)
class MachineTable:
    width: QualifiedLeaf
    depth: QualifiedLeaf
    placement_transform: QualifiedLeaf

    def leaves(self) -> tuple[QualifiedLeaf, ...]:
        return (self.width, self.depth, self.placement_transform)


@dataclass(frozen=True, slots=True)
class MachineSpindle:
    maximum_rpm: QualifiedLeaf
    feed_envelope: QualifiedLeaf
    rapid_envelope: QualifiedLeaf
    direction_mapping: QualifiedLeaf

    def leaves(self) -> tuple[QualifiedLeaf, ...]:
        return (
            self.maximum_rpm, self.feed_envelope, self.rapid_envelope,
            self.direction_mapping,
        )


@dataclass(frozen=True, slots=True)
class MachineToolSystem:
    taper: QualifiedLeaf
    atc_capacity: QualifiedLeaf
    maximum_tool_diameter: QualifiedLeaf
    maximum_tool_length: QualifiedLeaf
    selection_behavior: QualifiedLeaf
    offset_namespace: QualifiedLeaf

    def leaves(self) -> tuple[QualifiedLeaf, ...]:
        return (
            self.taper, self.atc_capacity, self.maximum_tool_diameter,
            self.maximum_tool_length, self.selection_behavior,
            self.offset_namespace,
        )


@dataclass(frozen=True, slots=True)
class MachineControllerPolicy:
    work_offsets: QualifiedLeaf
    coolant_mapping: QualifiedLeaf
    drilling_cycles: QualifiedLeaf
    tapping: QualifiedLeaf
    safe_positions: QualifiedLeaf
    program_format: QualifiedLeaf

    def leaves(self) -> tuple[QualifiedLeaf, ...]:
        return (
            self.work_offsets, self.coolant_mapping, self.drilling_cycles,
            self.tapping, self.safe_positions, self.program_format,
        )


@dataclass(frozen=True, slots=True)
class MachineQualificationContract:
    profile_id: str
    display_name: str
    contract_revision: int
    identity: MachineIdentity
    controller: ControllerIdentity
    axes: MachineAxes
    table: MachineTable
    spindle: MachineSpindle
    tool_system: MachineToolSystem
    policy: MachineControllerPolicy
    extensions: tuple[tuple[str, JsonValue], ...] = ()
    format_version: int = QUALIFICATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.format_version != QUALIFICATION_CONTRACT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported qualification contract version")
        if not isinstance(self.profile_id, str) or _KEY.fullmatch(self.profile_id) is None:
            raise CamValidationError("Machine qualification profile ID is invalid")
        object.__setattr__(self, "display_name", _text(self.display_name, "Profile display name"))
        if type(self.contract_revision) is not int or self.contract_revision <= 0:
            raise CamValidationError("Machine qualification contract revision is invalid")
        expected = (
            (self.identity, MachineIdentity), (self.controller, ControllerIdentity),
            (self.axes, MachineAxes), (self.table, MachineTable),
            (self.spindle, MachineSpindle), (self.tool_system, MachineToolSystem),
            (self.policy, MachineControllerPolicy),
        )
        if any(not isinstance(value, typ) for value, typ in expected):
            raise CamValidationError("Machine qualification contract group is invalid")
        keys = [leaf.key for leaf in self.leaves]
        if len(set(keys)) != len(keys):
            raise CamInvariantError("Machine qualification leaf keys must be unique")
        normalized_extensions = tuple(sorted(self.extensions, key=lambda item: item[0]))
        if any(
            not isinstance(item, tuple) or len(item) != 2 or
            not isinstance(item[0], str) or _KEY.fullmatch(item[0]) is None
            for item in normalized_extensions
        ):
            raise CamValidationError("Machine qualification extensions are invalid")
        if len({key for key, _ in normalized_extensions}) != len(normalized_extensions):
            raise CamInvariantError("Machine qualification extensions must be unique")
        canonical_json_bytes({key: value for key, value in normalized_extensions})
        object.__setattr__(self, "extensions", normalized_extensions)

    @property
    def leaves(self) -> tuple[QualifiedLeaf, ...]:
        return (
            *self.identity.leaves(), *self.controller.leaves(), *self.axes.leaves(),
            *self.table.leaves(), *self.spindle.leaves(), *self.tool_system.leaves(),
            *self.policy.leaves(),
        )

    def leaf(self, key: str) -> QualifiedLeaf:
        for leaf in self.leaves:
            if leaf.key == key:
                return leaf
        raise KeyError(key)

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": QUALIFICATION_CONTRACT_FORMAT,
            "format_version": self.format_version,
            "profile_id": self.profile_id,
            "contract_revision": self.contract_revision,
            "identity": [item.to_dict() for item in self.identity.leaves()],
            "controller": [item.to_dict() for item in self.controller.leaves()],
            "axes": [item.to_dict() for item in self.axes.leaves()],
            "table": [item.to_dict() for item in self.table.leaves()],
            "spindle": [item.to_dict() for item in self.spindle.leaves()],
            "tool_system": [item.to_dict() for item in self.tool_system.leaves()],
            "policy": [item.to_dict() for item in self.policy.leaves()],
            "extensions": {key: value for key, value in self.extensions},
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "display_name": self.display_name}


@dataclass(frozen=True, slots=True)
class StockEnvelope:
    x_span_mm: float
    y_span_mm: float
    z_span_mm: float

    def __post_init__(self) -> None:
        for field_name in ("x_span_mm", "y_span_mm", "z_span_mm"):
            object.__setattr__(self, field_name, _finite(getattr(self, field_name), field_name, positive=True))

    def to_dict(self) -> dict[str, float]:
        return {
            "x_span_mm": self.x_span_mm,
            "y_span_mm": self.y_span_mm,
            "z_span_mm": self.z_span_mm,
        }


@dataclass(frozen=True, slots=True)
class ToolQualificationInput:
    tool_assembly_fingerprint: ContentFingerprint
    tool_number: int
    h_offset: int | None
    d_offset: int | None
    diameter_mm: float | None
    overall_length_mm: float | None
    taper: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.tool_assembly_fingerprint, ContentFingerprint):
            raise CamValidationError("Tool qualification fingerprint is invalid")
        if type(self.tool_number) is not int or self.tool_number <= 0:
            raise CamValidationError("Tool qualification number is invalid")
        for name in ("h_offset", "d_offset"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise CamValidationError(f"Tool qualification {name} is invalid")
        for name in ("diameter_mm", "overall_length_mm"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite(value, name, positive=True))
        if self.taper is not None:
            object.__setattr__(self, "taper", _text(self.taper, "Tool taper", maximum=64).upper())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_assembly_fingerprint": self.tool_assembly_fingerprint.to_dict(),
            "tool_number": self.tool_number,
            "h_offset": self.h_offset,
            "d_offset": self.d_offset,
            "diameter_mm": self.diameter_mm,
            "overall_length_mm": self.overall_length_mm,
            "taper": self.taper,
        }


@dataclass(frozen=True, slots=True)
class PhysicalEvidence:
    nc_sha256: str
    contract_fingerprint: ContentFingerprint
    dry_run: EvidenceResult = EvidenceResult.NOT_PERFORMED
    single_block: EvidenceResult = EvidenceResult.NOT_PERFORMED
    air_cut: EvidenceResult = EvidenceResult.NOT_PERFORMED
    machine_acceptance: EvidenceResult = EvidenceResult.NOT_PERFORMED
    authority: str | None = None
    record_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "nc_sha256", _sha256(self.nc_sha256, "Physical evidence NC SHA-256"))
        if not isinstance(self.contract_fingerprint, ContentFingerprint):
            raise CamValidationError("Physical evidence contract fingerprint is invalid")
        for value in (self.dry_run, self.single_block, self.air_cut, self.machine_acceptance):
            if not isinstance(value, EvidenceResult):
                raise CamValidationError("Physical evidence result is invalid")
        if self.authority is not None:
            object.__setattr__(self, "authority", _text(self.authority, "Physical evidence authority"))
        if self.record_reference is not None:
            object.__setattr__(self, "record_reference", _text(self.record_reference, "Physical evidence record"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "nc_sha256": self.nc_sha256,
            "contract_fingerprint": self.contract_fingerprint.to_dict(),
            "dry_run": self.dry_run.value,
            "single_block": self.single_block.value,
            "air_cut": self.air_cut.value,
            "machine_acceptance": self.machine_acceptance.value,
            "authority": self.authority,
            "record_reference": self.record_reference,
        }


@dataclass(frozen=True, slots=True)
class QualificationFinding:
    severity: FindingSeverity
    code: FindingCode
    message_key: str
    evidence: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.severity, FindingSeverity) or not isinstance(self.code, FindingCode):
            raise CamValidationError("Qualification finding enum is invalid")
        if not isinstance(self.message_key, str) or _KEY.fullmatch(self.message_key) is None:
            raise CamValidationError("Qualification message key is invalid")
        normalized = tuple(sorted(self.evidence))
        if any(
            not isinstance(item, tuple) or len(item) != 2 or
            not all(isinstance(value, str) and value for value in item)
            for item in normalized
        ):
            raise CamValidationError("Qualification finding evidence is invalid")
        if len({key for key, _ in normalized}) != len(normalized):
            raise CamInvariantError("Qualification finding evidence keys must be unique")
        object.__setattr__(self, "evidence", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "code": self.code.value,
            "message_key": self.message_key,
            "evidence": [list(item) for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QualificationFinding":
        if not isinstance(data, dict) or set(data) != {"severity", "code", "message_key", "evidence"}:
            raise CamValidationError("Qualification finding payload is malformed")
        if not isinstance(data["evidence"], list):
            raise CamValidationError("Qualification finding evidence payload is malformed")
        try:
            severity = FindingSeverity(data["severity"])
            code = FindingCode(data["code"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Qualification finding enum payload is invalid") from error
        return cls(severity, code, data["message_key"], tuple(tuple(item) for item in data["evidence"]))


@dataclass(frozen=True, slots=True)
class QualificationReport:
    project_id: str
    program_fingerprint: ContentFingerprint
    operation_ids: tuple[str, ...]
    tool_binding_fingerprints: tuple[ContentFingerprint, ...]
    machine_profile_id: str
    machine_contract_fingerprint: ContentFingerprint
    post_profile_id: str
    post_profile_version: int
    post_profile_fingerprint: ContentFingerprint
    nc_sha256: str
    qualification_level: QualificationLevel
    findings: tuple[QualificationFinding, ...]
    physical_evidence: PhysicalEvidence | None
    report_fingerprint: ContentFingerprint | None = None
    format_version: int = QUALIFICATION_REPORT_VERSION

    def __post_init__(self) -> None:
        if self.format_version != QUALIFICATION_REPORT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported qualification report version")
        object.__setattr__(self, "project_id", _text(self.project_id, "Qualification project ID"))
        if not isinstance(self.program_fingerprint, ContentFingerprint):
            raise CamValidationError("Qualification program fingerprint is invalid")
        if not isinstance(self.operation_ids, tuple) or not self.operation_ids:
            raise CamValidationError("Qualification operation identities are invalid")
        operation_ids = tuple(_text(item, "Qualification operation ID") for item in self.operation_ids)
        if len(set(operation_ids)) != len(operation_ids):
            raise CamInvariantError("Qualification operation identities must be unique")
        object.__setattr__(self, "operation_ids", operation_ids)
        if (
            not isinstance(self.tool_binding_fingerprints, tuple)
            or not self.tool_binding_fingerprints
            or any(not isinstance(item, ContentFingerprint) for item in self.tool_binding_fingerprints)
        ):
            raise CamValidationError("Qualification Tool fingerprints are invalid")
        object.__setattr__(self, "machine_profile_id", _text(self.machine_profile_id, "Machine profile ID"))
        if not isinstance(self.machine_contract_fingerprint, ContentFingerprint):
            raise CamValidationError("Machine contract fingerprint is invalid")
        object.__setattr__(self, "post_profile_id", _text(self.post_profile_id, "Post profile ID"))
        if type(self.post_profile_version) is not int or self.post_profile_version <= 0:
            raise CamValidationError("Post profile version is invalid")
        if not isinstance(self.post_profile_fingerprint, ContentFingerprint):
            raise CamValidationError("Post profile fingerprint is invalid")
        object.__setattr__(self, "nc_sha256", _sha256(self.nc_sha256, "Qualification NC SHA-256"))
        if not isinstance(self.qualification_level, QualificationLevel):
            raise CamValidationError("Qualification level is invalid")
        if not isinstance(self.findings, tuple) or any(
            not isinstance(item, QualificationFinding) for item in self.findings
        ):
            raise CamValidationError("Qualification findings are invalid")
        findings = tuple(sorted(self.findings, key=lambda item: (item.severity.value, item.code.value, item.evidence)))
        object.__setattr__(self, "findings", findings)
        if self.physical_evidence is not None and not isinstance(self.physical_evidence, PhysicalEvidence):
            raise CamValidationError("Qualification physical evidence is invalid")
        if self.qualification_level is QualificationLevel.MACHINE_ACCEPTED:
            if self.physical_evidence is None or self.physical_evidence.machine_acceptance is not EvidenceResult.PASS:
                raise CamInvariantError("Machine acceptance requires explicit PASS evidence")
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.report_fingerprint is None:
            object.__setattr__(self, "report_fingerprint", calculated)
        elif self.report_fingerprint != calculated:
            raise CamInvariantError("Qualification report fingerprint verification failed")

    @property
    def machine_ready(self) -> bool:
        return self.qualification_level is QualificationLevel.MACHINE_ACCEPTED

    @property
    def has_errors(self) -> bool:
        return any(item.severity is FindingSeverity.ERROR for item in self.findings)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": QUALIFICATION_REPORT_FORMAT,
            "format_version": self.format_version,
            "project_id": self.project_id,
            "program_fingerprint": self.program_fingerprint.to_dict(),
            "operation_ids": list(self.operation_ids),
            "tool_binding_fingerprints": [item.to_dict() for item in self.tool_binding_fingerprints],
            "machine_profile_id": self.machine_profile_id,
            "machine_contract_fingerprint": self.machine_contract_fingerprint.to_dict(),
            "post_profile_id": self.post_profile_id,
            "post_profile_version": self.post_profile_version,
            "post_profile_fingerprint": self.post_profile_fingerprint.to_dict(),
            "nc_sha256": self.nc_sha256,
            "qualification_level": self.qualification_level.value,
            "machine_ready": self.machine_ready,
            "findings": [item.to_dict() for item in self.findings],
            "physical_evidence": self.physical_evidence.to_dict() if self.physical_evidence else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.identity_payload(),
            "report_fingerprint": self.report_fingerprint.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class QualifiedNCArtifact:
    artifact_id: str
    managed_nc_artifact_id: str
    managed_nc_artifact_fingerprint: ContentFingerprint
    managed_nc_relative_path: str
    report: QualificationReport
    artifact_fingerprint: ContentFingerprint | None = None
    format_version: int = QUALIFICATION_REPORT_VERSION

    def __post_init__(self) -> None:
        if self.format_version != QUALIFICATION_REPORT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported qualified artifact version")
        object.__setattr__(self, "artifact_id", _text(self.artifact_id, "Qualified artifact ID"))
        object.__setattr__(self, "managed_nc_artifact_id", _text(self.managed_nc_artifact_id, "Managed artifact ID"))
        if not isinstance(self.managed_nc_artifact_fingerprint, ContentFingerprint):
            raise CamValidationError("Managed artifact fingerprint is invalid")
        path = _text(self.managed_nc_relative_path, "Managed NC path")
        if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
            raise CamValidationError("Managed NC path must be project-relative")
        object.__setattr__(self, "managed_nc_relative_path", path.replace("\\", "/"))
        if not isinstance(self.report, QualificationReport):
            raise CamValidationError("Qualified artifact report is invalid")
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.artifact_fingerprint is None:
            object.__setattr__(self, "artifact_fingerprint", calculated)
        elif self.artifact_fingerprint != calculated:
            raise CamInvariantError("Qualified artifact fingerprint verification failed")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": QUALIFIED_ARTIFACT_FORMAT,
            "format_version": self.format_version,
            "artifact_id": self.artifact_id,
            "managed_nc_artifact_id": self.managed_nc_artifact_id,
            "managed_nc_artifact_fingerprint": self.managed_nc_artifact_fingerprint.to_dict(),
            "managed_nc_relative_path": self.managed_nc_relative_path,
            "report_fingerprint": self.report.report_fingerprint.to_dict(),
            "report": self.report.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "artifact_fingerprint": self.artifact_fingerprint.to_dict()}


def sha256_bytes(payload: bytes) -> str:
    """Return lower-case SHA-256 for immutable NC or evidence bytes."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "AuthorityClass", "ControllerIdentity", "EvidenceReference", "EvidenceResult",
    "FindingCode", "FindingSeverity", "JsonValue", "MachineAxes",
    "MachineControllerPolicy", "MachineIdentity", "MachineQualificationContract",
    "MachineSpindle", "MachineTable", "MachineToolSystem", "PhysicalEvidence",
    "QualificationFinding", "QualificationLevel", "QualificationReport",
    "QualificationState", "QualifiedLeaf", "QualifiedNCArtifact", "SampleAuthority",
    "StockEnvelope", "ToolQualificationInput", "canonical_json_bytes", "sha256_bytes",
]
