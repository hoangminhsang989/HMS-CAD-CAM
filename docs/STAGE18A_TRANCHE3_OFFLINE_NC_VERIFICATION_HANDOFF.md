# Stage18A Tranche3 — NC Verification & Dry-Run Handoff

This candidate consumes exact R222 final state `fe8dade90a0ef7d58d51a316088ff19ce08968c7` (tree `3bb3b47e76057109353bbca80696213a2ce5229c`) in an isolated worktree. R222 history is not rewritten.

## Frozen scope

`STAGE18A_TRANCHE3_OFFLINE_NC_VERIFICATION_AND_CONTROLLED_DRY_RUN_HANDOFF`

The product capability is `READY_FOR_CONTROLLED_EXTERNAL_DRY_RUN_HANDOFF`. This is an offline software review and package-preparation state. It is not physical qualification:

- Stage18A: `ACTIVE`
- Tranche1: `FULLY_DELIVERED_LEVEL1`
- Tranche2: `FULLY_DELIVERED_SOFTWARE`
- Tranche3 local implementation: `COMPLETE`
- Actual Level2: `LEVEL2_NOT_ACHIEVED`
- Actual Level3: `LEVEL3_NOT_ACHIEVED`
- `MACHINE_READY_FALSE`

The exact Vietnamese boundary is persisted in every operator acknowledgement:

> Chương trình này mới đạt kiểm tra phần mềm và chưa được nghiệm thu trên máy.

> Việc xuất gói chạy thử không đồng nghĩa MACHINE READY.

No module in this tranche opens a socket, discovers a CNC, uploads NC, sends G-code, starts cycle/spindle, changes offsets/controller parameters, reads PMC, invokes FOCAS, or communicates with a physical controller. Required marker: `STAGE18A_TRANCHE3_NO_CNC_CONTROL_BOUNDARY_PRESERVED`.

## Architecture audit

| Surface | Classification | Rationale |
| --- | --- | --- |
| Stage18A machine contract/profile and static qualification | `REUSE` | Exact FANUC ROBODRILL α-D21MiB / 31i-B / BT30 contract and Level1 report remain authoritative. |
| Modal validator and canned-cycle boundary | `HARDEN` | Existing `validate_fanuc_modal_sequence` is reused; Tranche3 adds line-indexed typed findings and preserves lowercase/adjoining detection. |
| Deterministic Program Assembly/Post output | `REUSE` | Analyzer consumes canonical emitted NC bytes and never regenerates or edits them. |
| Managed NC/artifact provenance | `REUSE` | Candidate binds the exact NC SHA and upstream fingerprints. |
| Tranche2 setup/physical-readiness model | `REUSE` | `MachineSetupQualification`, Tool/Holder bindings and `PhysicalReadinessResult` are linked directly. |
| Collision/simulation evidence | `REUSE` | Existing evidence is referenced; unknown physical clearance remains visible. No CAM toolpath is recalculated. |
| Offline block analyzer/execution trace | `EXTEND` | New immutable R223 domain contracts provide deterministic blocks, modal state, motion classes and logical transitions. |
| Release candidate/revision comparison | `EXTEND` | New immutable identity binds NC, session, machine, setup, Tool set, Post and Level1 report. |
| Handoff package/manifest | `EXTEND` | Existing deterministic artifact conventions are extended with exact inventory and sidecar verification. |
| Project persistence | `EXTEND` | Additive files under `post/qualification/tranche3`; SQLite schema remains `5`. |
| NC/Post preview UI and I18N | `EXTEND` | `NCReleaseCenter` is a projection-only panel with trace filters and VI/EN/KO runtime switching. |
| New parallel Post/parser/simulator/persistence framework | `NOT_APPLICABLE` | No duplicate framework is introduced. |

## Verification and release rules

`OfflineNCVerificationSession` is immutable after finalization and carries exact project/program/NC/machine/controller/Post/setup/G54/Tool/contract bindings. Any drift creates `STALE` and a new session. Unknown syntax is `UNRESOLVED`, not safe. Findings are typed `INFO`, `WARNING`, or `BLOCKER`, with stable code, block reference, validator, authority, remediation and qualification impact.

`NCReleaseCandidate` uses monotonically explicit revisions. Structured comparison covers source bindings, block/motion count, spindle/feed, Tool-change sequence and findings; optional line diff classifies motion, Tool, spindle, coolant, offset, modal, program structure and comments. Comment-only bytes still change NC SHA.

The controlled release gate requires current exact sources, Level1 static validity, completed Tranche2 setup readiness, no blocker, finalized session, accepted attributable operator review and exact boundary acknowledgement. `RELEASED_FOR_EXTERNAL_DRY_RUN` never maps to `DRY_RUN_QUALIFIED`.

## Required markers

`STAGE18A_TRANCHE3_SCOPE_FROZEN`

`STAGE18A_OFFLINE_NC_VERIFICATION_SESSION_IMPLEMENTED`

`STAGE18A_NC_EXECUTION_TRACE_IMPLEMENTED`

`STAGE18A_STATIC_FINDING_MODEL_IMPLEMENTED`

`STAGE18A_NC_REVISION_LOCKING_IMPLEMENTED`

`STAGE18A_STRUCTURED_REVISION_COMPARISON_IMPLEMENTED`

`STAGE18A_CONTROLLED_RELEASE_GATE_IMPLEMENTED`

`STAGE18A_OPERATOR_REVIEW_IMPLEMENTED`

`STAGE18A_SETUP_SHEET_IMPLEMENTED`

`STAGE18A_DRY_RUN_HANDOFF_PACKAGE_IMPLEMENTED`

`STAGE18A_HANDOFF_STALENESS_PROTECTION_IMPLEMENTED`

`READY_FOR_CONTROLLED_EXTERNAL_DRY_RUN_HANDOFF`

`LEVEL2_NOT_ACHIEVED`

`LEVEL3_NOT_ACHIEVED`

`MACHINE_READY_FALSE`

`STAGE18A_TRANCHE3_NO_CNC_CONTROL_BOUNDARY_PRESERVED`

`STAGE18A_TRANCHE3_PRODUCT_DELTA_COUNTERFACTUALLY_PROVEN`

