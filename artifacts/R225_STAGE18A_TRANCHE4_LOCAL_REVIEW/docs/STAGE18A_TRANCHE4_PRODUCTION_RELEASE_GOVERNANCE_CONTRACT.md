# Stage18A Tranche4 — Production Release Governance

Frozen scope: `STAGE18A_TRANCHE4_PRODUCTION_RELEASE_GOVERNANCE_AND_JOB_HANDOFF`.

The increment aggregates already-qualified offline NC release candidates into a
typed `ManufacturingJob`. A job binds exact project/part revision, one exact
machine profile/controller contract, one or more setups and programs, Tool
fingerprints, qualification states, release policy, review, immutable release
identity and deterministic handoff package.

## Included

- cross-program Tool/H/D/holder reconciliation with deterministic blockers;
- setup/G54/machine/part revision consistency and stale detection;
- owner-configurable fail-closed release policy;
- attributable review bound to the exact job and program release fingerprints;
- immutable release records, supersede history and structured release diff;
- package inventory with NC SHA-256, setup/verification reports, checklists,
  Level2 evidence intake template and tamper detection;
- additive persistence under `post/qualification/tranche4` while SQLite schema
  remains `5`;
- Vietnamese-first job-level Release Center.

## Explicit exclusions

No controller discovery, FOCAS, CNC upload, MDI, spindle/cycle control, offset
mutation, network machine control, physical dry-run, air-cut, single-block,
Level2/Level3 PASS, MACHINE READY, tapping qualification, second-machine
qualification, or invented fixture/stock dimensions.

All releases stop at `RELEASED_FOR_EXTERNAL_DRY_RUN`; physical state remains
`LEVEL2_NOT_ACHIEVED`, `LEVEL3_NOT_ACHIEVED`, `MACHINE_READY_FALSE`.

Required boundary marker:
`STAGE18A_TRANCHE4_NO_CNC_CONTROL_BOUNDARY_PRESERVED`.
