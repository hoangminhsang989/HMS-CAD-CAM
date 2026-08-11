"""Frozen registry of engineering-only Tranche3 scenarios."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OfflineEngineeringSample:
    sample_id: str
    description: str
    expected_outcome: str
    authority: str = "ENGINEERING_REGRESSION_SAMPLE"


def tranche3_engineering_samples() -> tuple[OfflineEngineeringSample, ...]:
    """Return the exact deterministic R223 sample inventory."""

    values = (
        ("clean_multi_operation_handoff", "Clean multi-operation handoff", "READY_FOR_EXTERNAL_DRY_RUN_HANDOFF"),
        ("stale_nc_revision", "NC bytes changed after review", "NC_HASH_MISMATCH"),
        ("stale_setup", "Setup fingerprint changed", "STALE_SETUP"),
        ("changed_tool", "Tool or Holder fingerprint changed", "STALE_TOOL_FINGERPRINT"),
        ("warning_only_physical_unknowns", "Physical facts remain explicitly unknown", "VISIBLE_WARNING"),
        ("hard_blocker", "Unresolved blocking NC token", "HANDOFF_BLOCKED"),
        ("tapping_blocked", "G84/Tapping request", "TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED"),
        ("canned_cycle_blocked", "Unqualified G81-G89 token", "UNSUPPORTED_CANNED_CYCLE_TOKEN"),
        ("operator_rejected", "Attributable operator rejection", "OPERATOR_REVIEW_REJECTED"),
        ("package_tamper", "Package inventory or checksum changed", "PACKAGE_INVALID"),
    )
    return tuple(OfflineEngineeringSample(*item) for item in values)


__all__ = ["OfflineEngineeringSample", "tranche3_engineering_samples"]
