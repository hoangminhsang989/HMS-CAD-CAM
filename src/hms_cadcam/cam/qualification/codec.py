"""Deterministic codecs for Stage18A contracts, reports, and artifacts."""

from __future__ import annotations

import json
from typing import Any

from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.model import (
    QUALIFICATION_CONTRACT_FORMAT,
    QUALIFICATION_CONTRACT_VERSION,
    QUALIFICATION_REPORT_FORMAT,
    QUALIFICATION_REPORT_VERSION,
    QUALIFIED_ARTIFACT_FORMAT,
    ControllerIdentity,
    EvidenceResult,
    JsonValue,
    MachineAxes,
    MachineControllerPolicy,
    MachineIdentity,
    MachineQualificationContract,
    MachineSpindle,
    MachineTable,
    MachineToolSystem,
    PhysicalEvidence,
    QualificationFinding,
    QualificationLevel,
    QualificationReport,
    QualifiedLeaf,
    QualifiedNCArtifact,
    canonical_json_bytes,
)


def _leaves(data: Any, count: int, name: str) -> tuple[QualifiedLeaf, ...]:
    if not isinstance(data, list) or len(data) != count:
        raise CamValidationError(f"{name} qualification leaves are malformed")
    return tuple(QualifiedLeaf.from_dict(item) for item in data)


def contract_from_dict(data: dict[str, Any]) -> MachineQualificationContract:
    """Decode a contract while preserving unknown top-level fields as extensions."""

    if not isinstance(data, dict):
        raise CamValidationError("Machine qualification contract payload is malformed")
    if data.get("format") != QUALIFICATION_CONTRACT_FORMAT:
        raise UnsupportedCamSchemaError("Unsupported machine qualification contract format")
    if data.get("format_version") != QUALIFICATION_CONTRACT_VERSION:
        raise UnsupportedCamSchemaError("Unsupported machine qualification contract version")
    required = {
        "format", "format_version", "profile_id", "display_name", "contract_revision",
        "identity", "controller", "axes", "table", "spindle", "tool_system",
        "policy", "extensions",
    }
    missing = required - set(data)
    if missing:
        raise CamValidationError("Machine qualification contract is incomplete")
    extensions = data["extensions"]
    if not isinstance(extensions, dict):
        raise CamValidationError("Machine qualification extensions are malformed")
    preserved: dict[str, JsonValue] = dict(extensions)
    for key in sorted(set(data) - required):
        preserved[f"forward.{key}"] = data[key]
    identity = _leaves(data["identity"], 4, "identity")
    controller = _leaves(data["controller"], 4, "controller")
    axes = _leaves(data["axes"], 5, "axes")
    table = _leaves(data["table"], 3, "table")
    spindle = _leaves(data["spindle"], 4, "spindle")
    tool = _leaves(data["tool_system"], 6, "tool system")
    policy = _leaves(data["policy"], 6, "policy")
    return MachineQualificationContract(
        data["profile_id"],
        data["display_name"],
        data["contract_revision"],
        MachineIdentity(*identity),
        ControllerIdentity(*controller),
        MachineAxes(*axes),
        MachineTable(*table),
        MachineSpindle(*spindle),
        MachineToolSystem(*tool),
        MachineControllerPolicy(*policy),
        tuple(sorted(preserved.items())),
        data["format_version"],
    )


def physical_evidence_from_dict(data: dict[str, Any]) -> PhysicalEvidence:
    fields = {
        "nc_sha256", "contract_fingerprint", "dry_run", "single_block",
        "air_cut", "machine_acceptance", "authority", "record_reference",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise CamValidationError("Physical evidence payload is malformed")
    try:
        results = tuple(
            EvidenceResult(data[key])
            for key in ("dry_run", "single_block", "air_cut", "machine_acceptance")
        )
    except (TypeError, ValueError) as error:
        raise CamValidationError("Physical evidence result payload is invalid") from error
    return PhysicalEvidence(
        data["nc_sha256"],
        ContentFingerprint.from_dict(data["contract_fingerprint"]),
        *results,
        data["authority"],
        data["record_reference"],
    )


def report_from_dict(data: dict[str, Any]) -> QualificationReport:
    fields = {
        "format", "format_version", "project_id", "program_fingerprint",
        "operation_ids", "tool_binding_fingerprints", "machine_profile_id",
        "machine_contract_fingerprint", "post_profile_id", "post_profile_version",
        "post_profile_fingerprint", "nc_sha256", "qualification_level",
        "machine_ready", "findings", "physical_evidence", "report_fingerprint",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise CamValidationError("Qualification report payload is malformed")
    if data["format"] != QUALIFICATION_REPORT_FORMAT:
        raise UnsupportedCamSchemaError("Unsupported qualification report format")
    if data["format_version"] != QUALIFICATION_REPORT_VERSION:
        raise UnsupportedCamSchemaError("Unsupported qualification report version")
    if not isinstance(data["operation_ids"], list) or not isinstance(
        data["tool_binding_fingerprints"], list
    ) or not isinstance(data["findings"], list):
        raise CamValidationError("Qualification report collection is malformed")
    try:
        level = QualificationLevel(data["qualification_level"])
    except (TypeError, ValueError) as error:
        raise CamValidationError("Qualification level payload is invalid") from error
    if data["machine_ready"] is not (level is QualificationLevel.MACHINE_ACCEPTED):
        raise CamValidationError("Qualification machine-ready payload is inconsistent")
    physical = (
        physical_evidence_from_dict(data["physical_evidence"])
        if data["physical_evidence"] is not None
        else None
    )
    return QualificationReport(
        data["project_id"],
        ContentFingerprint.from_dict(data["program_fingerprint"]),
        tuple(data["operation_ids"]),
        tuple(ContentFingerprint.from_dict(item) for item in data["tool_binding_fingerprints"]),
        data["machine_profile_id"],
        ContentFingerprint.from_dict(data["machine_contract_fingerprint"]),
        data["post_profile_id"],
        data["post_profile_version"],
        ContentFingerprint.from_dict(data["post_profile_fingerprint"]),
        data["nc_sha256"],
        level,
        tuple(QualificationFinding.from_dict(item) for item in data["findings"]),
        physical,
        ContentFingerprint.from_dict(data["report_fingerprint"]),
        data["format_version"],
    )


def artifact_from_dict(data: dict[str, Any]) -> QualifiedNCArtifact:
    fields = {
        "format", "format_version", "artifact_id", "managed_nc_artifact_id",
        "managed_nc_artifact_fingerprint", "managed_nc_relative_path",
        "report_fingerprint", "report", "artifact_fingerprint",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise CamValidationError("Qualified artifact payload is malformed")
    if data["format"] != QUALIFIED_ARTIFACT_FORMAT:
        raise UnsupportedCamSchemaError("Unsupported qualified artifact format")
    if data["format_version"] != QUALIFICATION_REPORT_VERSION:
        raise UnsupportedCamSchemaError("Unsupported qualified artifact version")
    report = report_from_dict(data["report"])
    if ContentFingerprint.from_dict(data["report_fingerprint"]) != report.report_fingerprint:
        raise CamValidationError("Qualified artifact report fingerprint is stale")
    return QualifiedNCArtifact(
        data["artifact_id"],
        data["managed_nc_artifact_id"],
        ContentFingerprint.from_dict(data["managed_nc_artifact_fingerprint"]),
        data["managed_nc_relative_path"],
        report,
        ContentFingerprint.from_dict(data["artifact_fingerprint"]),
        data["format_version"],
    )


def dumps(value: MachineQualificationContract | QualificationReport | QualifiedNCArtifact) -> bytes:
    """Encode one Stage18A value to deterministic UTF-8 JSON bytes."""

    return canonical_json_bytes(value.to_dict())


def loads(payload: bytes) -> MachineQualificationContract | QualificationReport | QualifiedNCArtifact:
    """Decode one complete Stage18A payload without accepting NaN/Infinity."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    try:
        data = json.loads(payload.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CamValidationError("Stage18A JSON payload is invalid") from error
    if not isinstance(data, dict):
        raise CamValidationError("Stage18A JSON root must be an object")
    format_name = data.get("format")
    if format_name == QUALIFICATION_CONTRACT_FORMAT:
        return contract_from_dict(data)
    if format_name == QUALIFICATION_REPORT_FORMAT:
        return report_from_dict(data)
    if format_name == QUALIFIED_ARTIFACT_FORMAT:
        return artifact_from_dict(data)
    raise UnsupportedCamSchemaError("Unsupported Stage18A JSON format")


__all__ = [
    "artifact_from_dict", "contract_from_dict", "dumps", "loads",
    "physical_evidence_from_dict", "report_from_dict",
]
