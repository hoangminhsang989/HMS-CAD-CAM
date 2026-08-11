# Stage18A Tranche2 — Setup transform and external dry-run qualification

Frozen scope: `STAGE18A_TRANCHE2_SETUP_TRANSFORM_AND_DRY_RUN_QUALIFICATION`  
Baseline: `661ba163d7b99272ce50252352daf5f3e7358bee`  
Baseline tree: `e6a6d509c78ffa78ba1668c056f8fdda1d2a0b28`

## Delivery boundary

This tranche implements software and deterministic evidence workflow needed to
move an exact Level1 statically qualified NC artifact toward externally
performed Level2 dry-run qualification.

The implementation does not:

- communicate with a CNC;
- upload or run a program;
- change controller parameters;
- infer machine-coordinate endpoints from the X/Y/Z travel spans;
- fabricate fixture or Holder geometry;
- treat a simulation result as physical confirmation;
- promote Level3 or set `MACHINE_READY=true`.

Without genuine matching external evidence, the actual programme state remains:

- qualification: `LEVEL1_STATICALLY_VALIDATED`;
- physical qualification: `NOT_PERFORMED`;
- Level2: `NOT_ACHIEVED`;
- Level3: `NOT_ACHIEVED`;
- `MACHINE_READY=false`.

## Typed setup authority

`MachineSetupQualification` binds:

- machine profile ID and fingerprint;
- exact NC artifact ID and SHA-256;
- Post fingerprint;
- canonical G54 transform;
- part zero and machine-coordinate reference;
- stock dimensions, origin, orientation, source, and authority;
- fixture identity, envelope, location, orientation, source, authority, and
  verification state;
- ordered Tool/Holder bindings, gauge length, stick-out, assembly length,
  usable axial length, requested depth, and diameter envelope;
- setup timestamp, provenance, authority, and qualification state;
- optional current collision/clearance evidence from an existing simulation or
  physical source.

Unknown transform/location/orientation components are explicit `None` values.
Zero translation is never inferred. G55–G59 are rejected by the Tranche2
contract.

## Physical-readiness chain

The validator evaluates:

`PROGRAM POINT → G54 TRANSFORM → MACHINE COORDINATE → AUTHORITATIVE ENDPOINTS`

When exact endpoints are absent or unverified, it returns
`PHYSICAL_TRAVEL_VALIDATION_UNAVAILABLE`. It never substitutes the Level1 spans
500/400/330 mm as absolute endpoints.

Stock footprint placement is evaluated against the known table envelope only
when stock origin/orientation authority exists. Missing fixture placement stays
`FIXTURE_PLACEMENT_UNVERIFIED`. Holder/fixture clearance is accepted only when
existing evidence is bound to the current setup, Tool set, and fixture
fingerprints; otherwise it remains
`HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED`.

Tool reach distinguishes:

- `TOOL_REACH_STATICALLY_VALIDATED`;
- `TOOL_REACH_PHYSICALLY_CONFIRMED`;
- `TOOL_REACH_INSUFFICIENT`;
- `TOOL_REACH_EVIDENCE_INCOMPLETE`.

## External evidence and policy

`DryRunQualificationEvidence` binds the exact machine/controller identity, NC
SHA-256, machine profile, setup, ordered Tool set, Post, qualification contract,
G54 identity, date/time, operator/authority, run mode, result, observations,
blockers, attachments, and attributable acceptance record.

Run modes are:

- `CONTROLLER_GRAPHICS`;
- `DRY_RUN`;
- `SINGLE_BLOCK`;
- `AIR_CUT`.

`PhysicalAcceptancePolicy` is owner-controlled. Every decision defaults to
unknown, so policy remains fail-closed until an identified authority confirms
all dimensions and selects at least one external run mode. The software does
not guess shop procedure or require all modes by generic assumption.

Every local attachment records filename, size, SHA-256, role, capture timestamp,
provenance, and deterministic reference. A missing file becomes `INVALID`; byte
drift becomes `STALE`.

The exact evidence states are:

- `NOT_PERFORMED`;
- `PENDING`;
- `PASS`;
- `FAIL`;
- `STALE`;
- `INVALID`.

A FAIL remains in immutable chronology. A later PASS requires a new evidence
ID, later timestamp, and explicit remediation.

## Promotion gate

The only promotion path is derived by `assess_level2_readiness`:

`LEVEL1_STATICALLY_VALIDATED`

→ `READY_FOR_EXTERNAL_LEVEL2_EVIDENCE`

→ `LEVEL2_EVIDENCE_PENDING`

→ `DRY_RUN_QUALIFIED`

Failure and drift paths are:

→ `LEVEL2_EVIDENCE_FAILED`

→ `LEVEL2_EVIDENCE_STALE`

The gate rechecks current NC bytes, machine profile, setup, Tool set, Post,
qualification contract, required external modes, attachments, sign-offs, and
physical blockers. `Level2Readiness` permanently rejects
`machine_ready=True`. There is no Level1 → Level3 transition.

## Checklist and golden samples

`RobodrillPhysicalChecklist.default()` includes machine identity, Tool setup,
offsets, work origin, stock, fixture, spindle, coolant, safe retract, Tool
change, first motion, drilling, and end sequence. Tapping is deliberately absent
from this qualified workflow.

Engineering regression samples remain
`ENGINEERING_REGRESSION_SAMPLE`. `GoldenSampleApproval` permits conversion to
`OWNER_APPROVED_MACHINE_SAMPLE` only with an attributable owner PASS record; no
automatic promotion exists.

## Additive persistence

Tranche2 snapshots are deterministic UTF-8 JSON below:

`post/qualification/level2/`

The manifest binds record/setup/policy fingerprints and exact NC/profile
identity. Updates create immutable content-addressed snapshots and point the
manifest at the current record. SQLite schema remains 5; Stage17A and Tranche1
records are not destructively modified.

`Tranche2QualificationService.export_package` writes deterministic JSON plus a
SHA-256 sidecar. It embeds metadata/references only, not large binary evidence.

## Vietnamese-first wizard

`PhysicalQualificationWizard` contains eight compact pages:

1. Bước 1 — Máy
2. Bước 2 — NC
3. Bước 3 — Gá đặt
4. Bước 4 — Dao & Holder
5. Bước 5 — Đồ gá
6. Bước 6 — Kiểm tra hành trình
7. Bước 7 — Dry-run
8. Bước 8 — Kết quả

Buttons are `Quay lại`, `Tiếp tục`, `Lưu`, and `Xuất gói kiểm tra`. Runtime
VI/EN/KO switching preserves typed state. Result language distinguishes static
validation, readiness, pending, PASS, FAIL, stale evidence, and
`Chưa nghiệm thu trên máy`. It never displays “Machine Ready” before Level3.

## Product markers

- `STAGE18A_TRANCHE2_SCOPE_FROZEN`
- `STAGE18A_SETUP_QUALIFICATION_IMPLEMENTED`
- `STAGE18A_WORK_OFFSET_TRANSFORM_MODEL_IMPLEMENTED`
- `STAGE18A_PHYSICAL_READINESS_MODEL_IMPLEMENTED`
- `STAGE18A_FIXTURE_EVIDENCE_MODEL_IMPLEMENTED`
- `STAGE18A_TOOL_HOLDER_REACH_QUALIFICATION_IMPLEMENTED`
- `STAGE18A_DRY_RUN_EVIDENCE_WORKFLOW_IMPLEMENTED`
- `STAGE18A_LEVEL2_PROMOTION_GATE_IMPLEMENTED`
- `STAGE18A_EVIDENCE_STALENESS_PROTECTION_IMPLEMENTED`
- `STAGE18A_VI_FIRST_PHYSICAL_QUALIFICATION_WIZARD_IMPLEMENTED`
- `STAGE18A_TRANCHE2_PRODUCT_DELTA_COUNTERFACTUALLY_PROVEN`

The implementation can produce `READY_FOR_EXTERNAL_LEVEL2_EVIDENCE` when all
authoritative setup prerequisites exist. R221 ships no real-machine evidence,
so it does not claim `DRY_RUN_QUALIFIED`, `MACHINE_ACCEPTED`, or machine-ready
output.
