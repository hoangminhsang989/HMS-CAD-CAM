# Stage 13B operation coverage

The registry and matrix share exactly three certified production identities:

| Production identity | Family | Concrete boundary | Status |
| --- | --- | --- | --- |
| `facing_2_5d` | Milling | Selected existing `CamWorkspace` operation, `FacingEditorContext`, atomic project command | SUPPORTED |
| `drilling_v1` | Drilling | `CamWorkspace.add_drilling_operation`, `DrillingFamilyEditorContext`, atomic project command | SUPPORTED |
| `FACE` | Turning | `LatheWorkspace` / `LatheParameterEditor`, `LatheQtPresenter.apply_parameter_changes` | SUPPORTED |

Each SUPPORTED identity has production construction/session, zero-persistence
advisor Apply, exactly-once normal Apply, actual close/reconstruction,
duplicate-handler, selective draft proof, owner lifecycle and legacy Apply
independence nodes in `STAGE13B_OPERATION_PARAMETER_COVERAGE_MATRIX.json`.

Other milling, drilling/hole and turning strategies remain PARTIAL with an exact
reason in the matrix. Threading remains UNSUPPORTED because no complete
production input, validator and draft setter chain exists. Stage 13B never saves
projects, regenerates toolpaths or exports NC automatically.
