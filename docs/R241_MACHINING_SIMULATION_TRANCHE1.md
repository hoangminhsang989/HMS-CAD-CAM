# R241 — Machining Simulation & Digital Verification Tranche 1

## Product boundary

R241 adds an optional, manually opened `MÔ PHỎNG GIA CÔNG` workspace. The
normal HMS sequence remains `Tạo nguyên công → Tính toolpath → Post → Xuất NC`.
The new `hms_cadcam.simulation` package is dynamically imported only after the
user invokes Simulation. Calculate, Post, and NC Export do not import or invoke
the R241 engine.

Simulation is read-only with respect to source CAD, CAM operations, calculated
toolpaths, NC, Post, machine setup, and physical-machine state. It performs no
CNC connection, upload, MDI, Cycle Start, spindle, axis, or offset action.

## Architecture

- Immutable session identity includes project, part, stock, WCS, operation,
  Tool, Holder, fixture, quality/settings, and engine fingerprints.
- `SimulationEngine` separates the UI from the algorithm. Tranche 1 implements
  `HEIGHTFIELD_3AXIS` on the CPU.
- A bounded LRU cache stores immutable per-operation stock states. Each cache
  key includes the incoming stock state, toolpath, Tool, Holder, stock, quality,
  and engine revision.
- A changed operation invalidates its own state and all downstream material
  states. Earlier independent prefix states remain reusable.
- Camera, visibility, playback speed, and timeline cursor are presentation-only
  dependencies and never request material recomputation.
- The Qt worker uses cooperative cancellation and emits structured progress.
  Partial material state is never published as a full result.
- Existing Simulation 7C collision analysis remains the collision foundation:
  cutter/shank/Holder envelopes, fixture and stock targets, rapid-below-safe
  warnings, and exact OCP narrow phase where geometry ownership is proven.

## Accuracy and honest limitations

The default engine is a bounded regular XY height field. It performs actual
stock-height subtraction for fixed-axis, top-down 3-axis milling and supports
End Mill, Ball End, and Bull Nose/Corner Radius profiles from the HMS Tool
Library. It is suitable for deterministic face, slot, pocket, contour, and
remaining-stock fixtures.

It is not an exact B-Rep boolean engine. It does not represent undercuts,
five-axis orientation changes, complete machine kinematics, micron-level
accuracy, or physical clearance. Resolution is controlled by `NHANH`,
`TIÊU CHUẨN`, and `CHI TIẾT`; these settings never modify the CAM toolpath or
NC. Missing Holder, fixture, or target-model geometry remains
`UNVERIFIED_GEOMETRY` / `GEOMETRY_REFERENCE_UNAVAILABLE`, never PASS.

A Simulation PASS is software evidence only. It does not establish Level2,
Level3, or `MACHINE_READY`, and it does not change the canonical Stage18A
physical-qualification state.

## UI and playback

The independent dark workspace appears before material computation. It shows
operation/tool/Holder/fixture scope, quality, progress, elapsed time, a
remaining-stock heat map, and a linked event timeline. Playback controls are
Play, Pause, Stop, Previous/Next event, Step through the timeline, and speed
presets `0.1x` through `10x` plus `MAX`.

Toolpath colors retain HMS semantics: Cutting yellow, Rapid red, Lead/Link
white, and Lead-out/Retract green. The cyan marker represents current tool
position. Playback speed is visual only and is not machine cycle time.

## Evidence

`tools/run_r241_simulation_evidence.py` writes deterministic external evidence
and a SHA-256 manifest. Heavy derived geometry remains cache/artifact data and
is not added to the project database. The repository tests cover domain and
fingerprints, cache/invalidation, material removal, playback, collision,
cancellation, worker ownership, UI lifecycle, repeated sessions, normal-path
non-interference, Post/Export independence, and relevant CAM regressions.
