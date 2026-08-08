# Stage 15A Final Acceptance and Canonical Closure

## Programme

**HMS CAD/CAM — 3D Export, Format Versioning and Export Profile Foundation**

This record is the concise canonical acceptance record for R165. It documents
the delivered Stage15A contract and retained bounded evidence; it is not a
replacement for the detailed implementation or review reports.

## Delivery

- WP1: **DELIVERED** — versioned native 3D export foundation.
- WP2: **DELIVERED** — persistent export defaults and General Settings 3D
  Export integration.
- WP3: **DELIVERED** — responsive request-owned export lifecycle and status UI.

## Final product capability matrix

| Format | Final classification | Delivered truth |
|---|---|---|
| STEP | `NATIVE_SUPPORTED_NOW` | AP203 / AP214 / AP242 |
| IGES | `NATIVE_SUPPORTED_NOW` | Native OCP writer |
| STL | `NATIVE_SUPPORTED_NOW` | Binary / ASCII; BREP tessellation; existing `TRIANGLE_MESH` re-encoding with tessellation `NOT_APPLICABLE` |
| BREP | `NATIVE_SUPPORTED_NOW` | Versions 1 / 2 / 3 |
| Parasolid | `ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE` | No proprietary backend is present; fail closed |
| ACIS | `ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE` | No proprietary backend is present; fail closed |
| DWG | `NOT_IMPLEMENTED` | No export writer |
| DXF | `DECLARED_UI_ONLY` | No export writer; legacy declaration only |

Explicit product limitations remain: current STEP export is geometry-oriented;
there is no guaranteed XCAF assembly/name/color/tolerance semantic round-trip,
no unit-conversion engine, and `model_units` preserves source coordinates.
Native OCP writers are not claimed interruptible.

## Accepted contracts

- Persistent defaults use shared `QSettings` for exactly STEP, IGES, STL, and
  BREP. Profiles are strict typed versioned JSON; corrupt/future values are
  observable and fail closed, and loading does not auto-rewrite them. The
  `.HMS` persistence contract is unchanged and there is no SQLite migration.
- Publication uses a same-directory temporary output and an explicit commit
  point. The persistent default is `FAIL_IF_EXISTS`; `REPLACE_EXISTING` is a
  request-local choice created only after explicit confirmation (`No` by
  default). Failed or cancelled requests do not publish a final file.
- Selected export resolves the current stable managed topology and fails
  closed for empty, stale, incompatible, mesh, or unsupported selections; it
  never falls back to whole-document export.
- Cancellation is cooperative and request-owned. Cancel-versus-commit has a
  total ordering; accepted cancellation cannot publish, opaque native writers
  are not killed, stale callbacks are suppressed, and busy ownership lasts
  through worker terminal cleanup.
- The status surface is non-modal and adaptive (`heightForWidth()`/`sizeHint()`),
  remains responsive, excludes `QSizeGrip` from its allocation, and compacts
  presentation-only context reversibly while restoring current intrinsic
  visibility. Runtime coverage includes VI/EN/KO, 100/125/150/200%, and
  1500×900, 1366×768, and 1280×720 geometry cases.

## Retained authoritative evidence boundary

These are separate bounded evidence sets and must not be arithmetically
combined into one repository-wide or Stage15A full-suite count:

- WP1 safe-path revalidation: **391 PASS**.
- WP2 final direct review: **399 PASS / exit 0**.
- WP3 final direct review: **421 PASS / exit 0**.
- WP3 production geometry: **216/216 PASS**.
- WP3 status file: **28/28 PASS**.
- WP3 cancellation/WP2 regression: **155/155 PASS**.
- Genuine OCP: **PASS**.
- Worker is distinct from GUI: **PASS**.
- STEP read-back: **PASS**.
- Localization: **untranslated 0**.
- Stage15A collection/full: **0/0** (`no_evidence`; no full suite was run).

R157/R159 geometry findings are recorded as resolved. R160 timeout is an
evidence-loss classification, not a product failure. R162 CP949 failure is an
external review-runner failure, not a candidate product failure. R163 is the
final independent approval and R164 is the GitHub delivery.

## Final verdict

**STAGE15A_COMPLETE**
