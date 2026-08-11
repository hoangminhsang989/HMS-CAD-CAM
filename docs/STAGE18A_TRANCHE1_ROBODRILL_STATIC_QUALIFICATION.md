# Stage18A Tranche1 — ROBODRILL static machine qualification

## Frozen owner contract

Stage: `STAGE18A_MACHINE_READY_MILLING_PRODUCTION_AND_CONTROLLER_VALIDATION`  
Tranche: `STAGE18A_TRANCHE1_FANUC_3AXIS_MACHINE_QUALIFICATION_AND_NC_DELIVERY`  
Machine profile: `FANUC_ROBODRILL_ALPHA_D21MIB_31IB_BT30`

The exact target is FANUC ROBODRILL α-D21MiB, FANUC 31i-B, BT30, three
linear axes. Owner-confirmed travels are X 500 mm, Y 400 mm and Z 330 mm;
the table working area is 650 × 400 mm; configured spindle maximum is 24,000
rpm; and the ATC has 21 positions. Catalog-confirmed envelopes are 48 m/min
rapid, 30,000 mm/min cutting feed, 80 mm maximum Tool diameter and 250 mm
maximum Tool length.

Travel values are spans, not absolute machine-coordinate endpoints. The
profile does not invent axis minima/maxima, physical G54 transform, G28/G53
semantics, H/D namespaces, safe Tool-change position, canned cycles, rigid
tapping, fixture/holder clearance or physical acceptance evidence.

## Qualification levels

1. `UNQUALIFIED`: a contract or required static input is missing/invalid.
2. `STATICALLY_VALIDATED`: every bounded software/static check passes;
   `machine_ready=false` and physical warnings remain visible.
3. `DRY_RUN_QUALIFIED`: requires external dry-run, single-block and air-cut
   evidence bound to exact NC and contract fingerprints.
4. `MACHINE_ACCEPTED`: requires Level 2 plus recorded machine acceptance.
   Only this level sets `machine_ready=true`.

R218 may demonstrate Level 1 only. No R218 artifact claims Level 2 or Level 3.

## R218 existing Post and machine-ready architecture matrix

| Existing behavior | Decision | Stage18A use |
|---|---|---|
| Controller-neutral Post/Program IR | `REUSE` | Source and Post identity |
| FANUC ROBODRILL production-format adapter | `HARDEN` | Existing deterministic NC plus exact qualification |
| Explicit-order multi-operation assembly | `REUSE` | Qualified assembly source and Tool transitions |
| Tool T/H/D binding/fingerprint | `HARDEN` | Internal consistency and exact Tool snapshot checks |
| G54-only WorkNC mapping | `HARDEN` | Static G54 path; physical transform remains unverified |
| M03/M04/M05 and M08/M09 mappings | `HARDEN` | Repository-confirmed mapping, physical behavior unverified |
| Expanded Standard/Spot/Peck motion | `REUSE` | Qualifiable without canned-cycle substitution |
| FANUC canned-cycle emission | `OUT_OF_SCOPE` | Exact 31i-B semantics are not frozen |
| Production Tapping | `OUT_OF_SCOPE` | Existing fail-closed blocker is preserved |
| Managed NC artifact/read-back/SHA | `EXTEND` | Separate qualification record references exact artifact |
| SQLite project schema | `REUSE` | No migration; qualification JSON is additive below `post/` |
| Stage9A.7 unified presentation shell | `DEPRECATE_LATER` | Not reopened; Stage18A uses the production assembly UI |
| Direct CNC/DNC transfer | `OUT_OF_SCOPE` | Explicitly forbidden |

## Static validation policy

- Derive required X/Y/Z spans from emitted Program IR coordinates and reject a
  span above 500/400/330 mm. Without absolute endpoints/setup transform, emit
  `PHYSICAL_TRAVEL_NOT_FULLY_VERIFIED`.
- Reject stock spans above 650 × 400 mm. A fitting stock with unknown placement
  emits `TABLE_PLACEMENT_NOT_PHYSICALLY_VERIFIED`.
- Reject non-finite/negative RPM or feed, RPM above 24,000 and feed above
  30,000 mm/min.
- Enforce at most 21 unique assigned Tools, diameter ≤ 80 mm and authoritative
  length ≤ 250 mm. BT30 compatibility is checked only when taper metadata is
  supplied. Magazine capacity never becomes a guessed T/H/D numeric range.
- Reject conflicting stations/H/D mappings, stale Tool fingerprints and
  missing H/D references. Unknown controller namespaces remain
  `OFFSET_NAMESPACE_UNVERIFIED`.
- Accept only deterministic G54 for static qualification. G55–G59 and extended
  offsets remain unverified and blocked.
- Validate known modal/sequence state while reporting unknown physical
  reference/safe-position semantics explicitly.
- Do not replace expanded drilling motion with G81/G82/G83.
- Keep Tapping `TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED`.

## Additive persistence and managed provenance

Qualification records are deterministic UTF-8 JSON stored below
`post/qualification/`. Each record binds project/program identity, ordered
operations, Tool bindings, machine contract/profile fingerprint, Post identity,
NC SHA-256, findings, qualification level and external evidence. Contract or
source drift produces `STALE_MACHINE_QUALIFICATION`. Existing SQLite and
Stage17A automatic-parameter persistence are unchanged.

## Physical boundary

Static validation is not setup or physical safety certification. Fixture,
workholding, Tool reach, holder clearance, physical offsets, dry-run and
machine acceptance remain external owner responsibilities and must be recorded
before promotion beyond Level 1.
