"""Stage18A exact-machine contract, provenance, and codec tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import CamInvariantError, CamValidationError
from hms_cadcam.cam.qualification import (
    AuthorityClass,
    QualificationState,
    dumps,
    loads,
    robodrill_alpha_d21mib_contract,
)
from hms_cadcam.cam.qualification.codec import contract_from_dict


def test_canonical_profile_freezes_exact_owner_and_catalog_values():
    contract = robodrill_alpha_d21mib_contract()

    assert contract.profile_id == "fanuc_robodrill_alpha_d21mib_31ib_bt30"
    assert contract.display_name == "FANUC ROBODRILL α-D21MiB — FANUC 31i-B — BT30"
    assert contract.leaf("identity.model").value == "α-D21MiB"
    assert contract.leaf("controller.model").value == "31i-B"
    assert contract.leaf("axes.x_travel_span").value == 500.0
    assert contract.leaf("axes.y_travel_span").value == 400.0
    assert contract.leaf("axes.z_travel_span").value == 330.0
    assert contract.leaf("table.width").value == 650.0
    assert contract.leaf("table.depth").value == 400.0
    assert contract.leaf("spindle.maximum_rpm").value == 24000.0
    assert contract.leaf("tool_system.taper").value == "BT30"
    assert contract.leaf("tool_system.atc_capacity").value == 21
    assert len(contract.leaves) == 32


def test_owner_and_catalog_provenance_are_both_preserved():
    contract = robodrill_alpha_d21mib_contract()
    x_span = contract.leaf("axes.x_travel_span")

    assert x_span.authority is AuthorityClass.OWNER_CONFIRMED
    assert {item.authority for item in x_span.sources} == {
        AuthorityClass.OWNER_CONFIRMED,
        AuthorityClass.CATALOG_CONFIRMED,
    }
    assert contract.leaf("axes.coordinate_endpoints").state is QualificationState.UNVERIFIED
    assert contract.leaf("policy.tapping").state is QualificationState.NOT_QUALIFIED


def test_contract_bytes_and_fingerprint_are_deterministic_round_trip():
    first = robodrill_alpha_d21mib_contract()
    second = robodrill_alpha_d21mib_contract()
    restored = loads(dumps(first))

    assert first.fingerprint == second.fingerprint
    assert dumps(first) == dumps(second)
    assert restored == first
    assert restored.fingerprint == first.fingerprint


def test_unknown_forward_field_survives_under_deterministic_extensions():
    payload = robodrill_alpha_d21mib_contract().to_dict()
    payload["future_option"] = {"value": "preserve-me"}

    restored = contract_from_dict(payload)

    assert dict(restored.extensions)["forward.future_option"] == {
        "value": "preserve-me"
    }
    assert loads(dumps(restored)) == restored


def test_profile_fingerprint_changes_for_critical_exact_identity():
    contract = robodrill_alpha_d21mib_contract()
    changed_model = replace(
        contract.identity.model,
        value="α-D21LiB",
    )
    changed = replace(
        contract,
        identity=replace(contract.identity, model=changed_model),
    )

    assert changed.fingerprint != contract.fingerprint


def test_unverified_or_missing_leaf_cannot_claim_confirmed():
    contract = robodrill_alpha_d21mib_contract()

    with pytest.raises(CamInvariantError):
        replace(
            contract.axes.coordinate_endpoints,
            state=QualificationState.CONFIRMED,
        )
    with pytest.raises(CamInvariantError):
        replace(
            contract.identity.model,
            authority=AuthorityClass.UNVERIFIED,
        )
    with pytest.raises(CamValidationError):
        replace(contract, contract_revision=0)
