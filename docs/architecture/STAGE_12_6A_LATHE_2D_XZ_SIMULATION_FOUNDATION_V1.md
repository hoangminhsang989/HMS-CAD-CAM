# Stage 12.6A — Lathe 2D XZ Simulation Foundation V1

## Coordinate contract

The source of truth is `LatheXZPoint`: canonical HMS Lathe X values are
**diameters in millimetres**. The simulation converts exactly once at its
planner boundary to a non-negative radial coordinate, `radius_mm =
x_diameter_mm / 2`. Negative X, NaN and infinity fail closed. Z remains the
setup-local millimetre coordinate. The canonical cylinder adapter places the
front face at Z = 0 and the back face at Z = `-length`; algorithms must still
honour snapshots whose front/back order is reversed. Numeric tolerance is
1e-9 mm. Linear motions are sampled at a bounded explicit resolution; dwell is
one non-removing frame.

## Architecture and ownership

`cam.lathe.simulation` is an immutable, PySide6-free domain/application
boundary. The planner consumes successful canonical `LatheToolpathResult`
objects, never rendered NC or controller text. It preserves exact operation,
strategy and segment identities. The engine owns a bounded piecewise-linear
axisymmetric radial profile, deterministic stock revisions, safety events and
SHA-256 result fingerprints. The service owns validation, plan construction,
cancellation/progress and display decimation. `ui.lathe_simulation` only paints
immutable results and controls timeline playback.

The project continues to use schema V5 and canonical `project.db`. Frames and
stock snapshots are derived and are not persisted. No safe existing V5
extension owns Stage 12.6A settings, so V1 settings remain runtime-only. The
feature flag is in-memory, defaults off, depends on the Lathe workspace and
canonical toolpath feature, and does not migrate or delete authored data.

## Geometry and safety policy

Tool nose/insert dimensions may be adapted from `TurningInsertGeometry`.
Orientation is not present in the current Tool definition, so it must be
supplied explicitly. Holder sections provide a conservative maximum envelope.
Missing tool geometry is blocking; missing holder geometry is an explicit
warning and never becomes a zero-sized holder. The holder never removes
material. Rapid tool contact and holder-stock contact are collision events and
stop by default. Invalid geometry, caps and unsupported semantics return an
explicit rejected/incomplete result.

OD/facing/groove/part-off/thread samples monotonically reduce outer radius;
ID/bore/thread samples monotonically increase inner radius; axial drilling
opens the inner radius. Rapid and dwell do not remove stock. The result reports
piecewise meridian area and an estimated revolved volume; neither is claimed
as exact physical cutting dynamics.

All exact V1 strategies are planned without sentinels: FACE, OD_ROUGH,
OD_FINISH, ID_ROUGH, ID_FINISH, OD_GROOVE, ID_GROOVE, PART_OFF, OD_THREAD,
ID_THREAD and AXIAL_DRILL. Thread motion is animated under the explicit
`THREAD_PROFILE_APPROXIMATION_V1` policy; pitch form, flank angle, controller
cycle, machine kinematics and NC verification are outside Stage 12.6A.
