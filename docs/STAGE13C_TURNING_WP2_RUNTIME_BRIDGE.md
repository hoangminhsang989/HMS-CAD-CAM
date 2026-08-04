# Stage 13C WP2 — Production Runtime Bridge

## Status

WP2 integrates the four exact turning strategy IDs behind
`OFFLINE_CAM_AI_TURNING_COVERAGE_13C`. Registry status is
`RUNTIME_BRIDGE_INTEGRATED_NOT_CERTIFIED`; no strategy is promoted to
`SUPPORTED`.

## Production route

`TurningProductionAdapter` reads `LatheParameterState`,
`LatheStockSnapshotV1`, `LatheToolCapabilityResolution` and the existing
`LatheParameterEditorDraftBridge`. It emits an immutable ordinary-data
`TurningProductionSnapshot` containing ownership identities, parameter/stock/
tool/draft digests, diameter provenance, typed tool capability, units,
descriptor allowlist, warnings and per-value provenance. No QObject, database
object or mutable editor reference is retained in that snapshot.

The current repository has no authoritative workpiece-material token in the
Lathe production state. Analyze therefore fails closed with
`MISSING_WORKPIECE_MATERIAL` until an explicit material token is supplied. No
project name, operation name, tool name, UI text, file name or machine name is
used as a fallback. Tool material is likewise explicit and must be `HSS` or
`CARBIDE`.

## Analyze and mapping

Analyze is explicit and CPU-only. It reuses the existing TURNING V1 model and
preserves raw recommendation, final values, confidence, clamps, provenance,
warnings and unsupported outputs. Only these production fields can be mapped:

- `spindle_rpm` → `spindle_speed_rpm`;
- `feed_per_revolution_mm` → `feed_mm_per_rev`;
- `depth_of_cut_mm` → `max_depth_of_cut_mm` for roughing only.

Target diameter, stock/bore diameter, allowances, threading pitch, tool
geometry/identity, material and unknown advisor fields are never mapped.
Unsupported fields remain in `retained_unsupported` with structured warnings.

## Draft-only Apply and Undo

Selective Apply requires an explicit selected-field set and production
descriptor validation. It mutates draft controls only. It does not call
`LatheQtPresenter.apply_parameter_changes`, project commands, persistence,
tree replacement, save, simulation, post or NC export. Ownership checks compare
project, editor, operation, strategy, parameter-state, stock, tool, material
and draft digests. Mismatch returns `STALE_RESULT_DISCARDED` without partial
mutation.

One compatible draft-only Undo restores only the selected pre-Apply values.
After a later draft/ownership mutation it returns `STALE_UNDO_REFUSED`; after
successful use a second Undo returns `UNDO_NOT_AVAILABLE`. Final UI Undo
workflow certification remains a later stage.

## Deferred gates

WP3/WP4 still own final `SUPPORTED` promotion, normal Apply exactly-once,
Cancel/reconstruction, duplicate signal-handler, project close/unload and
complete lifecycle certification. This document records runtime bridge
integration only.

## Subsequent WP3 authority

R38 WP3 completed the deferred production UI, material-selector, normal Apply,
Cancel/reconstruction, duplicate-handler and owner-lifecycle gates. The four
exact IDs are now `SUPPORTED`; this does not alter the historical WP2 status
recorded above. WP4 delivery/final full authority has not started.
