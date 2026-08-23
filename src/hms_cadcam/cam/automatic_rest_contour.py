"""Rest Contour AUTO is a narrow adapter over the proven Contour policy.

It deliberately owns no geometric heuristics. The 2D Contour policy remains
the sole source for loop-aware lead placement and axial-capacity stepdown.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from hms_cadcam.cam.automatic_contour import (
    CONTOUR_AUTOMATIC_KEYS,
    CONTOUR_AUTOMATIC_POLICY_KEY,
    CONTOUR_AUTOMATIC_POLICY_VERSION,
    ContourAutomaticContext,
    resolve_contour_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    CamQualityProfile,
)

# R270 has its own strategy key, but persists the exact established Contour
# contract so algorithm and dependency provenance cannot drift into a second
# implementation.
REST_CONTOUR_AUTOMATIC_POLICY_KEY = CONTOUR_AUTOMATIC_POLICY_KEY
REST_CONTOUR_AUTOMATIC_POLICY_VERSION = CONTOUR_AUTOMATIC_POLICY_VERSION
REST_CONTOUR_AUTOMATIC_KEYS = CONTOUR_AUTOMATIC_KEYS
RestContourAutomaticContext = ContourAutomaticContext


def _valid_manual_value(key: str, value: object) -> None:
    if key == "entry_segment_index":
        if type(value) is not int or value < 0:
            raise ValueError("Rest Contour entry override is invalid")
        return
    if key == "lead_form":
        if value not in {"tangent_linear", "normal_linear"}:
            raise ValueError("Rest Contour lead-form override is invalid")
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("Rest Contour numeric override is invalid")
    if key == "stepdown" and value <= 0:
        raise ValueError("Rest Contour stepdown override is invalid")


def resolve_rest_contour_automatic_contract(
    context: ContourAutomaticContext,
    quality_profile: CamQualityProfile = CamQualityProfile.BALANCED,
    manual_overrides: Mapping[str, object] | None = None,
) -> AutomaticParameterContract:
    """Resolve existing Contour AUTO, optionally preserving explicit overrides.

    Ball-end AUTO consequently remains unsupported. An owner can still give
    required values manually; those retain exact Contour-policy provenance.
    """
    if not isinstance(context, ContourAutomaticContext):
        raise TypeError("Rest Contour automatic context is invalid")
    overrides = {} if manual_overrides is None else dict(manual_overrides)
    if set(overrides) - set(REST_CONTOUR_AUTOMATIC_KEYS):
        raise ValueError("Rest Contour automatic override key is invalid")
    for key, value in overrides.items():
        _valid_manual_value(key, value)
    contract = resolve_contour_automatic_contract(
        context, quality_profile=quality_profile
    )
    if not overrides:
        return contract
    values = tuple(
        replace(
            item,
            mode=AutomaticParameterMode.MANUAL_OVERRIDE,
            override_value=overrides[item.key],
            status=AutomaticParameterStatus.RESOLVED,
            reason="Explicit Rest Contour manual override.",
        )
        if item.key in overrides
        else item
        for item in contract.values
    )
    return AutomaticParameterContract(
        contract.policy_key,
        contract.policy_version,
        contract.quality_profile,
        values,
        contract.contract_version,
    )


__all__ = [
    "REST_CONTOUR_AUTOMATIC_KEYS",
    "REST_CONTOUR_AUTOMATIC_POLICY_KEY",
    "REST_CONTOUR_AUTOMATIC_POLICY_VERSION",
    "RestContourAutomaticContext",
    "resolve_rest_contour_automatic_contract",
]
