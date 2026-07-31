# Stage 12.2 — Lathe Toolpath Preview V2

Status: owner-approved authoritative additive specification

## 1. Scope

Stage 12.2 extends the Stage 12.1 offline Lathe preview pipeline with exactly six executable strategies: lathe.face.v1, lathe.id_rough.v1, lathe.id_finish.v1, lathe.od_groove.v1, lathe.id_groove.v1 and lathe.part_off.v1.

Together with Stage 12.1 OD_ROUGH, OD_FINISH and AXIAL_DRILL, the executable partition is exactly 9/11. OD_THREAD and ID_THREAD are the exact two unsupported strategies. They fail closed with UNSUPPORTED_STRATEGY where a typed request reaches the registry and with diagnostic thread_toolpath_not_implemented_v2 at the request boundary. There is no fallback or empty successful path.

Stage 12.2 does not implement thread synchronization, thread phase or infeed, Post, G-code, controller commands, cutter compensation, stock removal, simulation, collision certification, persistence, database or .HMS schema changes. Success means an offline nominal centerline preview only.

## 2. Coordinate, numerical and motion contract

The Stage 12.1 immutable XZ contract is unchanged. X is diameter in millimetres and Z is the setup-local axial coordinate. The viewport adapter alone maps (X, Z) to (X / 2, 0, Z). No Y coordinate, unit conversion, machine-axis inversion or localized numeric parsing is introduced.

The exact tolerance remains 1e-9 mm. Strict numeric inputs reject bool, NaN and infinity. Ordered motions have contiguous deterministic sequence indices. Zero-length lines and duplicate consecutive motions are omitted before immutable construction. Bounds and cutting/rapid lengths derive from the final immutable tuple.

Motion classes remain RAPID, CUTTING, LEAD_IN and LEAD_OUT. The typed DWELL remains AXIAL_DRILL-only.

## 3. Normalized stock and bore

LatheStockSnapshotV1 continues to carry outer_diameter_mm, inner_diameter_mm, front_z_mm and back_z_mm plus exact stock/source/generation identity. Stock direction is sign(back_z_mm - front_z_mm).

External cutting lies between target diameter and stock OD. Internal operations require inner_diameter_mm > 0, enlarge that explicit bore, remain below stock OD and use safe X max(0, bore - 2 * clearance). CylinderStock still maps to a solid ID-zero snapshot. Stage 12.2 never infers a bore. An ID request without one returns missing_internal_bore.

No generated X is negative. Only solid-stock PART_OFF with target zero may end on the spindle centerline.

## 4. FACE

Strategy lathe.face.v1 uses algorithm lathe.face.toolpath.v2.

Inputs are stock plus face_z_mm, outer_diameter_mm, inner_diameter_mm, max_depth_of_cut_mm, finish_allowance_mm, clearance_mm, retract_mm and feed_mm_per_rev. Outer is positive and no greater than stock OD; inner is non-negative, below outer and no lower than an explicit bore. DOC is positive, allowance non-negative, and face/effective planes stay inside stock.

The effective plane is face_z_mm - stock_direction * finish_allowance_mm. Deterministic planes advance from stock front by at most axial DOC and include the exact effective plane. Each slice rapids at external clearance, approaches its Z, leads to outer diameter, cuts radially to inner diameter, leads out and returns safely. Diagnostic: nominal_facing_centerline_preview.

No free-form profile, nose compensation, CSS, stock mutation or collision check is implemented.

## 5. ID_ROUGH

Strategy lathe.id_rough.v1 uses algorithm lathe.id_rough.toolpath.v2.

The explicit bore is positive. Target is greater than bore and below stock OD. Radial DOC becomes diameter increment 2 * max_depth_of_cut_mm. Rough target is target_diameter_mm - 2 * radial_stock_to_leave_mm and stays at or above the bore. Direction is sign(end_z_mm - start_z_mm); effective end is end_z_mm - direction * axial_stock_to_leave_mm and remains beyond start.

Pass diameters advance deterministically from bore and include exact rough target. Each pass rapids at internal safe diameter, rapids axially to start, leads outward, cuts to effective end, leads inward to a non-negative retract and returns safely. Cancellation is checked between passes and construction steps. Diagnostic: nominal_internal_centerline_preview.

No arbitrary internal profile, undercut, boring-bar collision or insert compensation is implemented.

## 6. ID_FINISH

Strategy lathe.id_finish.v1 uses algorithm lathe.id_finish.toolpath.v2.

Bore and target rules match ID_ROUGH. finish_passes is at least one, spring_passes is non-negative, and total passes equal their sum. Every pass uses the exact target and supports either Z direction with the same safe internal approach/retract and cancellation rules. Diagnostic: nominal_internal_centerline_preview. No nose compensation is implied.

## 7. OD_GROOVE and ID_GROOVE

OD_GROOVE uses lathe.od_groove.toolpath.v2; ID_GROOVE uses lathe.id_groove.toolpath.v2.

Effective width is groove_width_mm - 2 * side_allowance_mm and is positive. Exact boundaries are center minus/plus half effective width and remain inside stock. Deterministic positions include both boundaries, contain no duplicates, have spacing no greater than max_step_mm and are ordered from stock front toward stock back. A tolerance-collapsed range uses its midpoint once.

OD plunges rapid at stock OD + 2 * clearance, lead to stock OD, cut inward to exact target, lead out and return. ID requires an explicit bore, rapids at internal safe diameter, leads to bore, cuts outward to target, leads inward without crossing centerline and returns. Diagnostics are nominal_multi_plunge_groove_preview and nominal_internal_multi_plunge_groove_preview.

No tool-width compensation, wall-finishing sweep, bore inference or stock simulation is implemented.

## 8. PART_OFF

Strategy lathe.part_off.v1 uses algorithm lathe.part_off.toolpath.v2.

Target is non-negative and below stock OD. For hollow stock it cannot be below the existing bore. max_step_mm is radial depth, so diameter decrement is 2 * max_step_mm and the final stage reaches exact target. Cutoff and front-side approach cutoff_z_mm - stock_direction * side_clearance_mm stay inside stock.

Each stage rapids at external safe diameter, rapids to the approach plane, leads radially to stock OD, leads axially to the exact cutoff, cuts radially to stage diameter, leads out and returns. Target zero is allowed only for solid stock, ends at X zero and never crosses it. Diagnostic: nominal_part_off_centerline_preview.

The preview does not assert physical separation and has no blade-width compensation.

## 9. Request, fingerprint and cache

LatheToolpathRequestV1 remains the only request type. The builder still requires live exact ownership, matching revision, READY/enabled operation, compatible foundation geometry, exact Tool capability, live stock, mutable/open session and supported strategy. Capabilities FACE_TURNING, ID_TURNING, OD_GROOVING, ID_GROOVING and PARTING come from the Stage 12 registry.

Fingerprint/cache identity includes ownership/revision, parameters, geometry, Tool/Profile/Assembly evidence, stock/source/generation, global algorithm version and exact strategy algorithm version. Language, theme, UI scale, job ID, sequence, timestamp and actor handles remain excluded. The bounded memory-only FIFO cache and common acceptance/publication path are unchanged.

## 10. Worker, viewport and UI

Stage 12.2 reuses the single coordinator/executor, cooperative cancellation, latest-wins ownership isolation, callback-outside-lock behavior, close/transition cancellation and late-result rejection.

Successful paths use the existing X/2 publication adapter, fixed XZ plane and colors: rapid red, cutting yellow, lead-in white, lead-out green. OCP retains one atomic grouped actor system with rollback, exact-owner clear and source/selection preservation.

Existing explicit Preview and Cancel actions are reused; edits never auto-submit. Thread operations remain editable and show localized V2 unsupported status. Success remains an offline nominal preview, never machine-ready.

LATHE_TOOLPATH_12_1 remains the only flag. Feature-off creates no coordinator, bridge, sink or actions. Feature-on creates exactly one of each and one Preview/Cancel pair. VI/EN/KO remain in parity; language changes do not alter request, fingerprint, cache or result.

## 11. Diagnostics

New stable codes are thread_toolpath_not_implemented_v2, missing_internal_bore, nominal_facing_centerline_preview, nominal_internal_centerline_preview, nominal_multi_plunge_groove_preview, nominal_internal_multi_plunge_groove_preview and nominal_part_off_centerline_preview. Existing validation, ownership, cancellation, generation and publication diagnostics remain deterministic.

## 12. Acceptance matrix

| Area | Required result |
| --- | --- |
| Registry | exact ordered 9 executable and 2 unsupported; no duplicate, fallback or thread generator |
| FACE | one/multiple slices, both directions, exact allowance plane, envelope/safety/cancel |
| ID | explicit bore, exact DOC/allowances/targets/passes, both directions/safety/cancel |
| Grooves | exact effective boundaries, bounded spacing, no duplicate, external/internal safety/cancel |
| PART_OFF | staged decrement, exact target/centerline, hollow-stock and approach guards/cancel |
| Motion | immutable contiguous finite events, no zero/duplicate motion, deterministic bounds/lengths |
| Request/cache | foundation compatibility, ownership/revision/lifecycle, deterministic versioned identity |
| Worker | one executor, latest-wins/isolation, no callback under lock or leak |
| Viewport | X/2, four colors, atomic replace/clear, source/selection preserved, near-zero valid |
| UI/I18N | explicit actions, honest states, thread fail-closed, singleton/off topology, VI/EN/KO |
| Regression | Stage 12.1, Stage 12, 9A.9, viewer/OCP, tooling and lifecycle |
| Exclusions | no thread algorithm, Post/G-code, simulation, persistence, package or push |

## 13. Completion

Stage 12.2 is COMPLETE only after all six generators and exact 9/2 registry pass focused pytest -W error, numerical/static/resource/visual gates, one fresh-clone full pytest -W error, exact staging/commit and post-commit verification.

OD_THREAD, ID_THREAD, Lathe Post, G-code, simulation and persistence remain NOT_STARTED.
