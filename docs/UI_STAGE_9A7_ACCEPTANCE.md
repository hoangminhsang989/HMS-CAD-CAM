# Stage 9A.7 — Acceptance và review contract

## Baseline/lifecycle

Stage 9A.7 is **FINALIZED / COMPLETE**. Final immutable R4 is
`reference_private/DERIVED/STAGE_9A7_WP2_UNIFIED_PANEL_REVIEW_R4.zip`,
SHA-256
`1f1eb3a99911fe3c193703297fc786a0153bb550f1d3b1bc1cb7535629a1ebe6`,
**109344 bytes**, **27 entries**. CRC, manifest/per-entry hashes,
candidate-final binary comparison, portability rehearsal and no-overwrite gate
all PASS.

Canonical source is the explicit revision range from WP1 base
`9465d294f60ae31b810983c71588be9945a71368` to maintenance target
`bbb07b9ba436293faa9d286927186d5e885016ea`, tree
`ca84b5f5f6735f10a57e5b0f735c4a87c9e365da`. Original WP2 and maintenance
parent are `ff250d67c70abfe80224befee4a17cccb4e4d3fb`; canonical patch SHA-256 is
`e08c2376245230d5543d2cec06e5af4705b83dbd7cc977d6ed4baf7413647210`
over **16 paths**. The maintenance delta is test-harness-only.

Exact-source QA PASS: focused **315 passed, 1 skipped**; regression **147
passed, 1 skipped**; full offscreen **2185 passed, 1 skipped, 2 deselected**;
native geometry **1 passed**; native smoke **17 passed**; `pip check`,
`compileall` and `diff-check` PASS. The single offscreen geometry skip is
limited to the native-Windows-QPA production geometry contract.

Main offscreen-fix commit
`33c9330fe16ec5c371470072c9ef7b94e7dda3c0` supplies supplementary integrated
compatibility evidence: targeted **18 passed**, native **398 passed**, and two
full offscreen runs each at **2262 passed, 6 skipped, 2 deselected**. It is not
the canonical R4 source and does not replace exact-source QA.

The maintenance branch/worktree is not merged into `main` and is retained to
preserve canonical R4 provenance:
**RETAIN_UNTIL_STAGE_9A7_CLOSURE_COMMIT_VERIFIED**. The Git-ignored final ZIP
is not claimed as a tracked main artifact. Current project state is
**WAITING_FOR_NEXT_APPROVED_STAGE**.

## Acceptance gates áp dụng cho WP1

WP1 phải chứng minh:

1. Bốn component projection là typed, immutable và độc lập với SQLite/domain
   mutation.
2. External projection total, deterministic, mutually exclusive; precedence
   là NOT_SELECTED → EXPORTING → current FAILED → EXPORTED_CURRENT →
   EXPORTED_STALE → READY.
3. Target absence/presence, current/stale checksum/provenance, target-intent
   drift và historical/current failure đều có test; `READY` không phải action
   authority.
4. Event-effect recompute không có transition API. Accepted result và callback
   audit là hai typed object độc lập; stale callback không đổi component state,
   headline, source fingerprints, accepted result, publish, artifact write,
   UI, selection hay project.
5. Readiness source identity không phụ thuộc generated payload byte/SHA; valid
   no-result workflow phải reachable `READY_TO_GENERATE + IDLE`.
6. Generation attempt/cancel chỉ nhận current matching identity gồm attempt ID,
   worker epoch, project generation, request và operation-order fingerprint.
   Previous result/managed artifact được giữ và không auto-retry.
7. Confirmation rejection tạo không attempt/worker/cancelled state và chỉ
   tăng rejection counter.
8. 19 headline IDs reachable, không alias managed với external và không dùng
   `EXPORT_REQUIRED`; target-specific prerequisite map fail-closed.
9. Feature flag chỉ nhận exact `UiFeatureFlag` key và exact `bool` value; không
   coercion string/int/`None`/truthy object. Development/test false, review true,
   production false trong WP1; không persistence/migration.
10. Simulation policy/status, operation artifact, workflow intent, managed và
   terminal state là typed enum; invalid boundary value bị reject.
11. `WARN + ALLOW_WARN` đạt readiness nếu không có blocker; MISSING/FAIL/STALE/
    INVALID theo truth table fail-closed và không tự Simulation/Generate.
12. Current attempt bắt buộc complete năm field. Accepted result đã qua boundary
    là nguồn duy nhất cho generated state; stale callback có received bằng
    discarded và mọi side-effect counter bằng 0. Cancel replacement attempt giữ
    previous accepted result/managed artifact và Preview evidence.
13. External dispatch core chỉ có `external_dispatch_attempt_id`; compatibility
   alias mâu thuẫn bị reject, active/failed không đồng thời đúng.
14. Accepted result phải nhất quán với attempt provenance ở project generation,
    request và operation order; previous result preservation không đổi quy tắc.
15. Managed/external current không dùng explicit shortcut hay `None` wildcard;
    byte/SHA/source/request/order/project/Post/machine và target path (external)
    phải đầy đủ, khớp. Managed explicit negative state và external
    `explicit_stale` luôn fail-closed; managed explicit CURRENT bị reject và
    external core không có `explicit_current`.
16. External dispatch source identity phải khóa managed artifact content (ID,
    SHA, source checksum, bytes và provenance); artifact ID đơn độc không đủ.
17. Compound source equality chỉ pass khi toàn bộ required identities tồn tại
    và bằng nhau; mismatch tạo typed block evidence yêu cầu Save Managed lại.
18. Managed MISSING chỉ tạo `SAVE_MANAGED_REQUIRED` khi runtime result current;
    IDLE/stale/active/terminal giữ đúng generation headline.
19. Diagnostic tuple/item, operation ID, generation/epoch/counter và identity/
    path đều được validate tại typed boundary; invalid evidence bị reject sớm.
20. `dirty_state` và external confirmation rejection được project thành audit
    output; dead core input field count bằng 0.
21. `presentation_readiness_blocked` chỉ là presentation evidence; WP1 không có
    `disabled` action authority. Action guard matrix thuộc WP2.

## R4 coverage và evidence

Đặc tả đầy đủ có 107 test cases, 28 acceptance gates và package review cuối
46 file (30 PNG, 15 JSON, 1 Markdown). WP1 chỉ thực hiện nhóm test projection,
identity, external precedence, headline reachability và feature foundation;
action guard, UI host, localization, DPI, compound write/rollback và GUI
evidence thuộc WP2–WP6.

Các lệnh QA bắt buộc cuối WP1:

```text
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall src tests tools
.\.venv\Scripts\python.exe -m pytest --basetemp=<pytest-basetemp>
git diff --check
git ls-files --deleted
```

Full suite không được giảm so với WP1 R3 baseline `2108 passed, 2 deselected` do
failure/error; test mới phải làm số pass tăng. Không dùng xdist, pyautogui,
coordinate click, benchmark hard gate hoặc coverage hard gate.

Kết quả Review R7: focused **280 passed**, regression **166 passed**; exact
Stage 8A.4.1 package test **3/3 passed** trong sandbox và shared DERIVED không
đổi. Full lặp lại **2150 passed, 2 deselected** trên hai basetemp độc lập;
failure/error = 0. Optional output-root injection chỉ phục vụ test worker,
production default và permission không đổi. Legacy artifact vẫn tồn tại do
owner/ACL cũ và rename cleanup thất bại; không reset ACL rộng, không dùng
Administrator. Contract state-projection R6 được giữ nguyên. `pip check`,
`compileall src tests tools`, `git diff --check` PASS; deleted tracked = 0.

## Dữ liệu và an toàn

SQLite schema v4, project `.HMS`, Post/Assembly schema, NC artifact format,
CAM algorithm và source file CAD không đổi. UI không đọc/ghi `project.db`;
không có migration hoặc dữ liệu flag trong profile/backup. External dispatch
được ghi nhận là synchronous theo call graph hiện tại và không có cancel sau
dispatch.

Không thuộc acceptance WP1: unified production panel, operation table/action
footer, Preview/diagnostics UI, localization catalog/DPI screenshots,
compound external workflow, clone/duplicate, Post algorithm, installer,
machine certification và việc mở lại Stage 9A.I1.

## WP2 Unified panel (implementation boundary)

WP2 adds one presentation entry, `cam.post_assembly.open`, hosted by a single
`PostAssemblyDock`/`UnifiedPostAssemblyPanel`. `UiFeatureFlag.POST_ASSEMBLY_9A7`
remains false for production/development and is true only for the review harness.
The false path opens the existing legacy Post/Program Assembly host.

The panel consumes `PostAssemblyProjectionAdapter` and a stable-ID
`PostAssemblyOperationTableModel` with explicit order, selection preservation and
VI/EN/KO headers. Add/Remove/Move Up/Move Down/Clear are presentation-scoped
assembly-list actions; they do not delete source operations or toolpaths.
Preview, Diagnostics, Generate, Save Managed and Export External are typed
fail-closed placeholders and remain disabled in WP2. No SQLite/schema/project
migration is introduced.
