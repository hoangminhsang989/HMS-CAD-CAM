# Stage17A Tranche3 — Pocket 2D Auto Setup

## Canonical freeze

- Product tranche: STAGE17A_TRANCHE3_POCKET_2D_CLOSED_REGION_AUTOMATIC_SETUP_AND_ENTRY_INTELLIGENCE.
- Short name: Pocket 2D Auto Setup.
- Baseline: 229a56fb93ad88e89cd1287bdcd5b280c65b9e08.
- Delivery boundary: large local implementation and local review package only. No integration, push, or production AI Sync.
- Shared architecture: additive automatic_parameter_contract with AUTO, MANUAL_OVERRIDE, legacy MANUAL, and NOT_APPLICABLE.

## POCKET_EXISTING_PRODUCT_CONTRACT_MATRIX

| Surface | Current production contract | R205 classification |
| --- | --- | --- |
| Geometry | One planar persistent BREP FACE/profile reference resolving to one closed CCW LINE/ARC outer loop | ALREADY_SUPPORTED |
| Islands | Inner loops are rejected by PocketGeometryResolver | UNSUPPORTED |
| Depth sequence | Positive stepdown; exact final floor without cumulative drift | ALREADY_SUPPORTED |
| Stepover | Positive numeric value strictly below tool diameter | AUTO_MIGRATION_TARGET |
| Stepdown | Positive numeric value with full-depth tool/stickout validation | AUTO_MIGRATION_TARGET |
| Tool family | Generator accepts valid END_MILL only | ALREADY_SUPPORTED and AUTO gate |
| Entry location | Canonical first point of each generated offset loop | AUTO_MIGRATION_TARGET |
| Entry form | VERTICAL_PLUNGE only; no center-cutting capability metadata | INTENTIONALLY_MANUAL / AUTO unavailable |
| Linking | Retract/position/plunge for every loop | ALREADY_SUPPORTED; preserved |
| Feed/spindle | Explicit user values validated against machine | INTENTIONALLY_MANUAL |
| Direction | Explicit climb/conventional intent | INTENTIONALLY_MANUAL |
| Allowances/depth endpoints | Explicit final geometry intent | INTENTIONALLY_MANUAL |
| Persistence | Strategy/operation payload V1, project/profile/template reuse existing parameter set | additive AUTO migration target; no SQLite migration |
| Editor | Basic/Advanced Pocket Function Editor with manual stepover/stepdown | AUTO_MIGRATION_TARGET |

## Eligibility and cutter policy

AUTO requires a successfully resolved planar outer loop, positive depth span, valid current END_MILL, positive diameter and axial cutting length, positive assembly stickout, a current geometry fingerprint, and at least one cutter-center offset loop produced by the production Pocket offset algorithm. Open, stale, self-intersecting, non-planar, ambiguous, island-bearing, inaccessible, degenerate, NaN/Inf, and unsupported-cutter cases fail closed.

AUTO_POLICY_SUPPORTED_TOOL_FAMILIES is exactly the intersection with the generator: END_MILL.

## Automatic ownership

- stepdown: derived from depth span, explicit axial cutting length, stickout, and shared quality profile; bounded by every validated physical limit.
- stepover: geometric coverage derived from diameter and shared quality factor; strictly below effective diameter and accepted only when the production offset algorithm proves a reachable region.
- entry_segment_index and entry coordinates: deterministic hidden placement ranked on the first reachable cutter-center loop by local boundary clearance, non-degenerate segment length, stable coordinates, and index. The generator revalidates and applies the placement before emitting toolpath.
- entry_form: NOT_APPLICABLE; the only generator form is vertical plunge and Tool metadata does not prove center-cutting capability.
- linking: existing retract/safe-position/plunge behavior remains unchanged because no complete stay-down path validation exists.

Feed, plunge feed, spindle, Tool selection, direction, allowances, depth endpoints, final region, material, controller, machine, fixture, and island intent remain user-owned.

## Safety and compatibility

Derived values are finite and positive. Stepdown never exceeds depth span, axial cutting length, or stickout. Stepover is positive and strictly below diameter. Region accessibility uses the same production offset-loop builder used by generation. Legacy Pocket numerics load as manual overrides; AUTO intent and manual overrides persist additively; malformed metadata fails closed; temporary evidence loss preserves stored AUTO mode. No SQLite schema migration is authorized or required.

## R205 local implementation and certification

- Status: `PASS_R205_STAGE17A_TRANCHE3_POCKET_AUTO_SETUP_LARGE_LOCAL_IMPLEMENTATION`.
- Implementation commit: `143958966760b43fef7c43b105b9c949bfc6b821`.
- Focused: 360 passed.
- Bounded: 1346 passed from 69 deterministic test files.
- Lifecycle: 2 passed; 24 Pocket editor VI/EN/KO cycles; top-level, hidden,
  modal and running-QThread delta `0/0/0/0`.
- Full: 4142 passed, 8 inherited/external failed, 8 skipped, 2 deselected.
  Candidate-induced and indeterminate failures are 0;
  `NEW_FAILURE_DELTA_R205=0`.
- The inherited environment-only `pip check` conflict is `flet 0.24.0` versus
  installed `packaging 26.2`; canonical tracked requirements were not changed.
- Evidence root:
  `E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE\R205_STAGE17A_TRANCHE3_POCKET_AUTO_20260810`.
- Delivery boundary: no R205 integration, push, force operation or production
  AI Sync. The next action is an independent final direct review and integration
  authority.

## R206 final direct review and remote delivery

- Direct review and exact-baseline counterfactual passed; candidate delta is
  proven and `NEW_FAILURE_DELTA_R206_REVIEW=0`.
- Transition was `M/A/D/R/T = 13/4/0/0/0`; protected overlap and false-dirty
  replacement paths were `0`; modify-existing DELETE capability was `14/14`;
  exact legacy ACL remediation was `4/4`; add-only capability was `3/3` with
  residue `0`.
- One-shot fast-forward and Push A delivered
  `9211761144552f3a89c96a6967679d8219c7518c` / tree
  `b1271575420fb7697e47b431cd59f4704b0352ba`.
- Fresh post-integration direct review passed `360/360`; fresh bounded review
  passed `1346/1346`; candidate-induced and indeterminate failures are `0/0`.
- Required marker: `STAGE17A_TRANCHE3_FULLY_DELIVERED`. AI Sync V1.1 and Push B
  remain pending at this state-closure commit point.
