# Stage17A Tranche4 — Drilling 2D Hole-Pattern Automatic Geometry Setup

Status: `STAGE17A_TRANCHE4_DRILLING_PRODUCT_CONTRACT_FROZEN`

Baseline: `9959c7ccde84d3543eb585896095e82f7fdfdf99` / tree
`2f3fb32540e113db97d0bd243a228b143f06b55b`.

## Frozen scope

R208 adds evidence-backed automatic geometry setup to the existing
controller-neutral `drilling_v1` operation for exactly `spot_drill`, `drill` and
`peck_drill`. It reuses `AutomaticParameterContract`; it does not choose a cycle,
feed, spindle, dwell, coolant, material, controller cycle or machine clearance.

## DRILLING_EXISTING_PRODUCT_CONTRACT_MATRIX

| Existing field/evidence | Current repository authority | R208 ownership |
| --- | --- | --- |
| Hole source and persistent references | `DrillGeometryInput`, `HoleReference`, `HolePattern` | `GEOMETRY_DERIVED_AUTO_CANDIDATE` |
| Resolved centre coordinates | `HoleLocation.position` with finite `Point3` | `GEOMETRY_DERIVED_AUTO_CANDIDATE` |
| Common drilling axis | `HoleLocation.axis`; generator requires setup +Z | `GEOMETRY_DERIVED_AUTO_CANDIDATE` |
| Machining/reference plane | `HoleLocation.plane_origin`; location must lie on plane | `GEOMETRY_DERIVED_AUTO_CANDIDATE` |
| Pattern identity/order | source and location fingerprints; current generator preserves resolver order | `GEOMETRY_DERIVED_AUTO_CANDIDATE` for hidden deterministic evidence only |
| Hole diameter | optional `HoleLocation.diameter`; generator checks exact Tool diameter | `TOOL_CAPABILITY_DEPENDENT` |
| Top/reference Z | explicit `DrillDepthDefinition.top_z`; pattern plane can verify it | `GEOMETRY_DERIVED_AUTO_CANDIDATE` only for a resolved common +Z plane |
| Final/target depth | explicit operation numeric; no current hole feature start/end extent | conditional `GEOMETRY_DERIVED_AUTO_CANDIDATE`; current production fallback is `USER_PROCESS_INTENT` |
| Clearance height | explicit operation numeric; no stock/fixture safe-plane source | `USER_PROCESS_INTENT` |
| Retract height | explicit operation numeric; no stock/fixture safe-plane source | `USER_PROCESS_INTENT` |
| Cycle (`spot_drill`, `drill`, `peck_drill`) | explicit `DrillingCycle` | `USER_PROCESS_INTENT` |
| Peck amount | explicit positive numeric, required only for Peck | `USER_PROCESS_INTENT` |
| Dwell | explicit non-negative numeric | `USER_PROCESS_INTENT` |
| Feed and spindle | explicit positive values plus machine bounds | `USER_PROCESS_INTENT` |
| Coolant | Drilling v1 does not persist it | `UNSUPPORTED` |
| Retract/approach policy | existing controller-neutral process choices | `USER_PROCESS_INTENT` |
| Controller canned cycle | selected only by Post | `CONTROLLER_SPECIFIC` |
| Tool selection | explicit Tool Assembly reference | `USER_PROCESS_INTENT` |
| Tool family/diameter/flute/stickout | current Tool and assembly snapshots | `TOOL_CAPABILITY_DEPENDENT` |
| Spot included angle | `CENTER_DRILL` uses `DrillGeometry.point_angle` | `TOOL_CAPABILITY_DEPENDENT` |
| Spot target diameter | no distinct production field | `UNSUPPORTED` until explicit evidence exists |
| Tolerance | explicit positive operation value | `USER_PROCESS_INTENT` and validation evidence |

## Eligibility and pattern intelligence

AUTO pattern eligibility requires a non-empty tuple of resolved `HoleLocation`
values, one known unit, finite unique centres, a common setup-compatible +Z axis,
a common plane, a stable geometry fingerprint and a current compatible Tool.
The pure policy normalizes centres deterministically, records hole count, common
axis, plane coordinate, XY bounding box and minimum centre spacing, and creates a
dependency fingerprint from geometry and Tool identities. Duplicate centres,
mixed axes/planes, stale geometry, missing Tool or unsupported Tool family fail
closed without a fabricated numeric value.

## Depth, plane, Spot and Peck boundaries

- `top_z` may be AUTO only from the resolved common hole plane.
- `final_depth` may be AUTO only from explicit per-hole feature start/end evidence
  with one compatible common span. Existing `DrillDepthDefinition` alone is manual
  intent and is never re-labelled AUTO.
- Clearance and retract remain manual because the current operation has no
  authoritative fixture/holder safe-plane contract.
- Spot final depth may be derived only from an explicit target spot diameter and
  current `CENTER_DRILL` point angle, bounded by flute length and stickout. The
  current editor does not expose that target evidence, so production reports this
  conditional field unavailable rather than inferring it from hole diameter.
- Peck amount always remains manual. Existing validation rejects zero, negative,
  non-finite and depth-sized values; R208 adds no material-less percentage rule.

## Persistence and generator boundary

The additive `automatic_parameter_contract` entry is stored in the existing
`OperationParameterSet`; the strategy/schema versions and SQLite schema do not
change. Legacy numeric operations load with manual intent. AUTO and manual
override modes round-trip. The generator decodes the additive entry, recomputes
current pattern/Tool evidence and rejects stale persisted AUTO dependencies before
toolpath emission.

## UX and acceptance

Basic shows one compact Auto Setup summary and pattern/top state. Advanced exposes
modes, provenance, unavailable reasons and the explicit manual Peck/safe-plane
boundary. Existing two-column disclosure, VI/EN/KO catalogs and lifecycle remain.
Completion requires focused, bounded and full regression with candidate-induced
and indeterminate failures zero, a deterministic local package, and no protected
main, remote or production AI Sync mutation.
