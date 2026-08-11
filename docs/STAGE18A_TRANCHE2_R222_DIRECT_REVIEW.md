# R222 Stage18A Tranche2 final direct review

Starting candidate: `0775e65b45219f37c90c5b04bab56a57046ed23a`

Starting tree: `ae6edaf5893c5a386195f769886d2770ff9cfed5`

## Qualification boundary

R222 reviews and may deliver only the software capability
`READY_FOR_EXTERNAL_LEVEL2_EVIDENCE`. No real external machine evidence is
supplied by this work package. Therefore actual Level2 and Level3 remain
`NOT_ACHIEVED`, `MACHINE_READY=false`, and owner-approved machine samples remain
zero.

## Direct-review findings and remediation

1. A PASS record could contain no attachment. PASS now requires at least one
   current attachment; missing, renamed, or changed bytes cannot qualify.
2. Attachment uniqueness did not reject duplicate semantic roles. Roles are now
   unique per attempt and contradictory operator records are rejected.
3. Evidence did not bind the owner acceptance policy. Exact policy fingerprint,
   machine profile identity, controller identity, NC, setup, Tool set, Post,
   qualification contract, and G54 are now checked together.
4. Attempt chronology accepted equal timestamps. Attempt timestamps are now
   strictly increasing; FAIL remains immutable and a later PASS after FAIL still
   requires explicit remediation.
5. Historical stale evidence blocked later current remediation forever. Only the
   latest attempt for each required mode determines unresolved stale state;
   history remains serialized and auditable.
6. Unknown Holder data could reuse a clearance PASS. Unknown Holder identity or
   envelope now returns `HOLDER_CLEARANCE_UNVERIFIED` even when an old clearance
   record exists.
7. Stock table footprint used only Z rotation. Placement now transforms all
   eight stock-envelope corners through full XYZ orientation.
8. Level2 project manifests had record hashes but no manifest sidecar. Save now
   writes `manifest.json.sha256`; load fails closed for missing, orphaned, or
   mismatched sidecars.
9. Golden sample approval did not bind current sample/NC bytes. Approval now
   binds sample fingerprint plus NC SHA-256 and exposes strict deserialization
   and currentness checks.
10. The ROBODRILL checklist represented offsets and work origin but lacked an
    explicit G54 item. G54 is now a permanent required checklist key; Tapping
    remains excluded.
11. The wizard displayed workflow state in its physical-travel field. It now
    consumes the typed `PhysicalReadinessResult` and displays exact travel and
    clearance states.

Permanent adversarial tests cover attachment absence/role/rename, operator
contradiction, wrong machine/controller, policy drift, strict chronology,
historical stale remediation, unknown Holder clearance, full XYZ footprint,
manual Level2/Level3 flags, manifest sidecars, golden sample stale reuse, UI
states, long strings/dark palette/resize, 24-cycle save/reopen, and forbidden CNC
control imports/callables.

## No-CNC-control boundary

Tranche2 modules import no network, serial, Modbus, OPC, or CNC transport. They
define no upload, cycle-start, spindle-start, controller-write, parameter-write,
or CNC-connect callable. The workflow hashes external files and stores evidence
metadata only.

## Approval identity

Direct-review approval attaches only to the final remediated commit/tree stated
in the R222 evidence package. This document does not itself approve integration
or claim physical qualification.
