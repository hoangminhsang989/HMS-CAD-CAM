"""Canonical owner-frozen FANUC ROBODRILL α-D21MiB qualification profile."""

from __future__ import annotations

from hms_cadcam.cam.qualification.model import (
    AuthorityClass,
    ControllerIdentity,
    EvidenceReference,
    MachineAxes,
    MachineControllerPolicy,
    MachineIdentity,
    MachineQualificationContract,
    MachineSpindle,
    MachineTable,
    MachineToolSystem,
    QualificationState,
    QualifiedLeaf,
)


ROBODRILL_ALPHA_D21MIB_PROFILE_ID = "fanuc_robodrill_alpha_d21mib_31ib_bt30"
ROBODRILL_ALPHA_D21MIB_DISPLAY_NAME = (
    "FANUC ROBODRILL α-D21MiB — FANUC 31i-B — BT30"
)

_OWNER = EvidenceReference(
    "r218.owner.machine.contract",
    AuthorityClass.OWNER_CONFIRMED,
    notes="Frozen owner input in R218",
)
_CATALOG = EvidenceReference(
    "r218.catalog.robodrill.alpha_d21mib.family",
    AuthorityClass.CATALOG_CONFIRMED,
    notes="Model-family record accepted by the owner; source artifact must be preserved in review evidence",
)
_REPOSITORY = EvidenceReference(
    "repository.robodrill_fanuc_21i_worknc_expanded_v1",
    AuthorityClass.REPOSITORY_CONFIRMED,
    notes="Existing production-format Post mapping; not physical machine certification",
)


def _confirmed(
    key: str,
    value: object,
    unit: str | None,
    *sources: EvidenceReference,
    notes: str | None = None,
) -> QualifiedLeaf:
    authority = (
        AuthorityClass.OWNER_CONFIRMED
        if any(item.authority is AuthorityClass.OWNER_CONFIRMED for item in sources)
        else sources[0].authority
    )
    return QualifiedLeaf(
        key,
        value,  # type: ignore[arg-type]
        unit,
        tuple(sources),
        authority,
        QualificationState.CONFIRMED,
        notes,
    )


def _unverified(key: str, *, notes: str) -> QualifiedLeaf:
    return QualifiedLeaf(
        key,
        None,
        None,
        (),
        AuthorityClass.UNVERIFIED,
        QualificationState.UNVERIFIED,
        notes,
    )


def robodrill_alpha_d21mib_contract() -> MachineQualificationContract:
    """Return the deterministic canonical Stage18A Tranche1 machine contract."""

    return MachineQualificationContract(
        profile_id=ROBODRILL_ALPHA_D21MIB_PROFILE_ID,
        display_name=ROBODRILL_ALPHA_D21MIB_DISPLAY_NAME,
        contract_revision=1,
        identity=MachineIdentity(
            _confirmed("identity.manufacturer", "FANUC", None, _OWNER, _CATALOG),
            _confirmed("identity.model", "α-D21MiB", None, _OWNER, _CATALOG),
            _confirmed("identity.family", "ROBODRILL", None, _OWNER, _CATALOG),
            _confirmed(
                "identity.machine_type",
                "3-AXIS CNC DRILLING / MILLING CENTER",
                None,
                _OWNER,
                _CATALOG,
            ),
        ),
        controller=ControllerIdentity(
            _confirmed("controller.family", "FANUC", None, _OWNER),
            _confirmed("controller.model", "31i-B", None, _OWNER),
            _unverified(
                "controller.software_revision",
                notes="Exact software revision was not supplied",
            ),
            _unverified(
                "controller.option_set",
                notes="Installed FANUC option set was not supplied",
            ),
        ),
        axes=MachineAxes(
            _confirmed("axes.x_travel_span", 500.0, "mm", _OWNER, _CATALOG),
            _confirmed("axes.y_travel_span", 400.0, "mm", _OWNER, _CATALOG),
            _confirmed("axes.z_travel_span", 330.0, "mm", _OWNER, _CATALOG),
            _unverified(
                "axes.reference_behavior",
                notes="Exact machine-zero/reference-return and G28 behavior require physical evidence",
            ),
            _unverified(
                "axes.coordinate_endpoints",
                notes="Travel spans must not be interpreted as 0..span machine coordinates",
            ),
        ),
        table=MachineTable(
            _confirmed("table.width", 650.0, "mm", _OWNER, _CATALOG),
            _confirmed("table.depth", 400.0, "mm", _OWNER, _CATALOG),
            _unverified(
                "table.placement_transform",
                notes="Fixture, clamps and workpiece placement are setup-specific",
            ),
        ),
        spindle=MachineSpindle(
            _confirmed("spindle.maximum_rpm", 24000.0, "rpm", _OWNER, _CATALOG),
            _confirmed("spindle.feed_envelope", 30000.0, "mm/min", _CATALOG),
            _confirmed("spindle.rapid_envelope", 48000.0, "mm/min", _CATALOG),
            _confirmed(
                "spindle.direction_mapping",
                {"clockwise": "M03", "counterclockwise": "M04", "stop": "M05"},
                None,
                _REPOSITORY,
                notes="Repository mapping only; physical direction remains unverified",
            ),
        ),
        tool_system=MachineToolSystem(
            _confirmed("tool_system.taper", "BT30", None, _OWNER, _CATALOG),
            _confirmed("tool_system.atc_capacity", 21, "tools", _OWNER, _CATALOG),
            _confirmed("tool_system.maximum_tool_diameter", 80.0, "mm", _CATALOG),
            _confirmed("tool_system.maximum_tool_length", 250.0, "mm", _CATALOG),
            _confirmed(
                "tool_system.selection_behavior",
                "RANDOM_SHORTEST_PATH_CLASS",
                None,
                _CATALOG,
            ),
            _unverified(
                "tool_system.offset_namespace",
                notes="ATC capacity does not authorize T/H/D numeric ranges",
            ),
        ),
        policy=MachineControllerPolicy(
            _confirmed(
                "policy.work_offsets",
                ["G54"],
                None,
                _REPOSITORY,
                notes="G54 static path only; physical transform remains unverified",
            ),
            _confirmed(
                "policy.coolant_mapping",
                {"flood": "M08", "off": "M09"},
                None,
                _REPOSITORY,
                notes="Physical coolant availability remains unverified",
            ),
            _unverified(
                "policy.drilling_cycles",
                notes="Expanded drilling motion is allowed; canned-cycle semantics are unqualified",
            ),
            QualifiedLeaf(
                "policy.tapping",
                None,
                None,
                (_REPOSITORY,),
                AuthorityClass.UNVERIFIED,
                QualificationState.NOT_QUALIFIED,
                "Production validator blocks tapping; exact rigid-tapping semantics are unknown",
            ),
            _unverified(
                "policy.safe_positions",
                notes="G28/G53 and Tool-change safe positions require exact configuration evidence",
            ),
            _confirmed(
                "policy.program_format",
                {
                    "extension": ".fn",
                    "encoding": "utf-8",
                    "newline": "crlf",
                    "program_numbers": "disabled",
                    "block_numbers": "disabled",
                },
                None,
                _REPOSITORY,
            ),
        ),
        extensions=(),
    )


__all__ = [
    "ROBODRILL_ALPHA_D21MIB_DISPLAY_NAME",
    "ROBODRILL_ALPHA_D21MIB_PROFILE_ID",
    "robodrill_alpha_d21mib_contract",
]
