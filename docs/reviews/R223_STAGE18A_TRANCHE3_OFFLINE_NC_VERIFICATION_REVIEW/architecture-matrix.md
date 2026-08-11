# R223 architecture reuse matrix

| Existing boundary | R223 action | Evidence |
| --- | --- | --- |
| `cam.qualification.model/profile/validation` | REUSE + HARDEN | Existing contract, exact machine profile, modal and R219 canned-cycle validator. |
| `cam.post.assembly_model` and managed NC export | REUSE | Analyzer consumes canonical text and SHA without regeneration. |
| `physical_model` and Tranche2 store/service | REUSE | Setup, G54, Tool/Holder, clearance and Level2 handoff bindings. |
| `cam.persistence`/artifact conventions | EXTEND | Additive `post/qualification/tranche3`, manifest and sidecar; SQLite stays 5. |
| existing NC/Post preview and I18N | EXTEND | `NCReleaseCenter`, VI_VN/EN_US/KO_KR parity and runtime switching. |
| parallel parser/Post/simulator/persistence | NOT_APPLICABLE | No duplicate framework. |

