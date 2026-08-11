# STAGE18A post-Tranche3 remaining software scope matrix

Baseline: R224 `3f73da82aaa2574bf9df4c965e27a81d3d367977` (tree
`80c376ecc181a47c9d3490642a326520d3dabcb8`). This audit is software-only;
physical qualification remains `LEVEL2_NOT_ACHIEVED`, `LEVEL3_NOT_ACHIEVED`,
and `MACHINE_READY_FALSE`.

| Gap | Classification | Evidence / boundary |
| --- | --- | --- |
| NC release governance across multiple programs/jobs | TRANCHE4_CANDIDATE | Tranche3 governs one immutable NC release; no job aggregate exists. |
| Manufacturing job/batch release | TRANCHE4_CANDIDATE | No typed job, release policy, review, or supersede graph exists. |
| Reusable setup qualification profiles | ALREADY_COMPLETE | Tranche2 `MachineSetupQualification` and additive stores are authoritative. |
| Reusable machine setup templates | FUTURE_TRANCHE | Requires owner-defined template semantics beyond exact qualified setup. |
| Controller/Post semantic qualification | REQUIRES_PHYSICAL_EVIDENCE | H/D, G28/G53, canned-cycle and tapping semantics remain frozen boundaries. |
| Golden NC/reference program management | FUTURE_TRANCHE | Existing static samples are engineering fixtures, not a production golden registry. |
| Returned Level2 evidence intake/reconciliation | REQUIRES_PHYSICAL_EVIDENCE | Tranche4 may bind future evidence; it cannot create PASS. |
| Offline simulation/verification gaps | FUTURE_TRANCHE | Existing Tranche3 static/offline verification is sufficient for job governance. |
| Multi-setup/multi-program release | TRANCHE4_CANDIDATE | Required to make manufacturing handoff useful and is machine-offline. |
| Tool/setup revision governance | TRANCHE4_CANDIDATE | Existing fingerprints can be reconciled across a job; no aggregate report exists. |
| Second milling-machine profile (D21MiA) | FUTURE_TRANCHE | Evidence is identity/static partial only; never add to qualified set silently. |
| Operator/manufacturing handoff UX | TRANCHE4_CANDIDATE | Existing NC Release Center is single-program; job-level Vietnamese-first center is missing. |
| Audit/history/reporting | TRANCHE4_CANDIDATE | Existing artifact history is per release; job review/supersede/diff history is missing. |
| Stage18A closure criteria | FUTURE_TRANCHE | Closure still depends on independent physical Level2/Level3 evidence. |

## Candidate comparison

| Candidate | Product value | Readiness | Authority | Reuse | Physical dependency | Workflow value | Regression risk |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Production release governance and manufacturing handoff | HIGH | HIGH | HIGH | HIGH | NONE | HIGH | MEDIUM |
| Golden NC registry | MEDIUM | MEDIUM | MEDIUM | MEDIUM | NONE | MEDIUM | MEDIUM |
| Reusable setup/machine templates | MEDIUM | LOW | LOW | MEDIUM | PARTIAL | MEDIUM | HIGH |
| Controller semantic qualification | HIGH | LOW | LOW | HIGH | BLOCKING | MEDIUM | HIGH |

## Frozen selection

`STAGE18A_TRANCHE4_PRODUCTION_RELEASE_GOVERNANCE_AND_JOB_HANDOFF`

The slice remains offline, preserves `STAGE18A_TRANCHE4_NO_CNC_CONTROL_BOUNDARY_PRESERVED`,
and never promotes Level2, Level3, or MACHINE READY.
