# Stage 15A — 3D Export, Format Versioning and Export Profile Foundation

## Capability matrix

| Format | Audit classification | Native writer | Extensions | Current scope |
|---|---|---|---|---|
| STEP | `NATIVE_SUPPORTED_NOW` | OCP `STEPControl_Writer` | `.step`, `.stp` | BREP document and selected solid/face/wire/edge; AP203/AP214/AP242 |
| IGES | `NATIVE_SUPPORTED_NOW` | OCP `IGESControl_Writer` | `.iges`, `.igs` | BREP document and selected solid/face/wire/edge |
| STL | `NATIVE_SUPPORTED_NOW` | OCP `StlAPI_Writer` / `RWStl` | `.stl` | BREP document or selected solid/face uses binary/ASCII plus active tessellation settings; a whole existing triangle mesh is binary/ASCII re-encoding with tessellation explicitly not applicable |
| BREP | `NATIVE_SUPPORTED_NOW` | OCP `BRepTools` | `.brep`, `.brp` | BREP document and selected solid/face/wire/edge; format versions 1/2/3 |
| Parasolid | `ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE` | none | `.x_t`, `.x_b` | Fail-closed; proprietary SDK is not present |
| ACIS | `ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE` | none | `.sat`, `.sab` | Fail-closed; proprietary SDK is not present |
| DWG | `NOT_IMPLEMENTED` | none | `.dwg` | Fail-closed; no export adapter |
| DXF | `DECLARED_UI_ONLY` | none | `.dxf` | The legacy source picker declared DXF, but no export writer exists |

The registry in `hms_cadcam.cad.export_models` is the single source of truth for
format IDs, extension routing, labels, standards, geometry kinds, backend names,
availability, and unavailable reasons. No unavailable route creates a placeholder
file or substitutes another format.

## Product behavior

- **Save As** keeps `.HMS` document persistence unchanged. A registered 3D
  extension is routed to the CAD export controller and never becomes HMS
  persistence. Unknown extensions fail before filesystem publication.
- **3D Export** uses a compact Basic-first profile dialog. Advanced controls are
  displayed only for STL because only the STL writer consumes them.
- Profiles are typed, versioned deterministic JSON contracts with strict
  `from_dict` / `from_json` inverse decoding. `format_version = 1` is the export
  **profile schema version**, not the STEP standard or BREP file-format version.
  Overwrite policy is serialized in the same contract. Profiles remain session
  state only; no SQLite or `.HMS` schema changed.
- Native writes run through the existing request-owned Qt worker pattern. Project
  lifecycle actions are blocked while the worker owns the active CAD document.
- The service writes to a unique, validated temporary file in the destination
  directory. It verifies non-empty output and computes size/SHA-256 from those
  validated **temporary bytes before publication**. Publication is the explicit
  commit point. On supported Windows systems, `FAIL_IF_EXISTS` publishes with
  same-directory `os.rename(temp, final)` after validating Windows no-replace
  behavior; a destination that already exists or appears concurrently is never
  replaced. `REPLACE_EXISTING` publishes the validated temporary file with
  `os.replace(temp, final)`. Unsupported platforms fail closed for
  `FAIL_IF_EXISTS` instead of substituting a weaker primitive. No final-path
  metadata operation occurs after the commit point that could turn a successful
  publication into an ordinary write failure. Writer or pre-commit publication
  failures clean the temporary path and preserve an existing or concurrently
  created final file.
- Selected-object export resolves the stable topology index against the current
  kernel-owned document and validates the selected managed object still exists.
  Empty, vertex, stale, mesh-selection, and format-incompatible selections fail
  explicitly; they never fall back to exporting the whole document.

## Evidence boundary and limitations

Real tests create deterministic box geometry and validate every native writer by
non-empty output plus supported reader read-back. The evidence proves readable
geometry and reasonable topology/mesh metadata; it does not claim exact semantic,
assembly-name, color, or tolerance round-trip equivalence. Current STEP export is
geometry-oriented and does not promise XCAF product metadata preservation.

BREP-to-STL export copies the source shape and tessellates that copy using the
profile's linear deflection, angular deflection, and relative flag before writing.
Existing-mesh-to-STL export preserves the already imported triangulation and only
changes binary/ASCII encoding. Its tessellation values are `NOT_APPLICABLE`; the
UI hides those controls for mesh context, and the backend rejects a mesh request
that supplies active tessellation settings rather than silently ignoring them.

The existing `ProjectTask` worker has no cancellation contract, so Stage15A WP1
does not expose a fake cancel control. Unit conversion and compatibility overrides
also remain unavailable: the safe `model_units` policy preserves current model
coordinates without inventing unsupported writer options.
