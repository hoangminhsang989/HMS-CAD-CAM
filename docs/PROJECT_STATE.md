# CANONICAL CURRENT PROGRAMME SUMMARY — R201 STAGE17A TRANCHE 1 DELIVERY

- Stage16A: **CLOSED**.
- Stage17A: **ACTIVE; TRANCHE 1 COMPLETE AND PRODUCTION-DELIVERED**.
- Canonical increment: `STAGE17A_CAM_AUTOMATIC_PARAMETERS_AND_OPERATION_INTELLIGENCE`.
- Program container: `MEGA_WP3_AUTOMATIC_CAM_SETUP_MODERNIZATION`.
- Scope/spec: `docs/STAGE17A_CAM_AUTOMATIC_PARAMETERS_OPERATION_INTELLIGENCE_V1.md`.
- R193 implementation PASS and R194 final direct review PASS apply to candidate
  `0f1836777c2c8553474503955abba381e6a1c46e` / tree
  `b99e7ec24e4b152975043be25f6c222479e1b06e`.
- R195–R200 preserved the product candidate while correcting the legacy Windows
  file-object ACL/replacement defect. The final filesystem proof is DELETE
  `18/18`, add-only create `3/3`, Git replacement `12/12`, with content/blob
  integrity preserved.
- R201 proved `GIT_INDEX_STAT_CACHE_FALSE_DIRTY_STATE` for the exact two state
  docs, reconciled their stat metadata without changing index blobs or raw
  bytes, and completed the one-shot fast-forward integration.
- Push A delivered the exact production candidate to `origin/main`; the live
  remote was independently verified at
  `0f1836777c2c8553474503955abba381e6a1c46e`.
- Exact-tree certification is inherited from R193/R194. Fresh post-integration
  evidence: focused **72/72 PASS**; bounded **396 passed + 1 directly reproduced
  inherited baseline failure**; localization
  **1874 total / 0 untranslated / 239 allowlisted**; static compile/import,
  catalog, `pip check`, and committed-delta `git diff --check` PASS.
- Candidate-induced and indeterminate failures remain `0`;
  `NEW_FAILURE_DELTA_INTEGRATION=0`.
- Production/test/catalog/schema/dependency delta from this state-closure phase
  is `0`.
- The five protected local identities and eight pre-sync R191 `.ai` outputs
  remain preserved.
- State-only Push B and AI Sync V1.1 are pending at this commit point.
- Stage17A remains **OPEN** beyond Tranche 1; the next tranche requires a
  canonically defined operation-family policy or an owner definition packet.

The historical Stage16/R191 material below is retained for provenance and does
not override this R201 canonical summary.

# Trạng thái dự án HMS CAD/CAM

## CANONICAL CURRENT PROGRAMME SUMMARY — R189 LOCAL CLOSURE

- **Stage13C: 100% COMPLETE**
- **Stage14A: 100% COMPLETE**
- **Stage15A: 100% COMPLETE**
- **Stage16A: IN_PROGRESS**
- **Stage16A Mega-WP1: 100% DELIVERED**.
- **Stage16A Mega-WP2: 100_PERCENT_IMPLEMENTED_REVIEWED_AND_INTEGRATED**.
- Mega-WP2 production scope is the Tool Library management and safe-reuse
  implementation integrated by R188E. SQLite schema remains **5**; `.HMS`
  migration, dependency delta, and CAM algorithm fork remain `0`.
- R187 final review verdict:
  `APPROVE_STAGE16A_MEGA_WP2_TOOL_LIBRARY_FOR_INTEGRATION`.
- R188E integration verdict:
  `PASS_STAGE16A_R188E_TRANSACTIONAL_FAST_FORWARD_INTEGRATION`.
- Integrated HEAD: `f67262c31a7a5611daf392ce8ce3b26ff9fb233a`.
- Integrated tree: `8067361cd01d29240d845643ef79f5515c257959`.
- Post-integration verification: focused **248/248 PASS**, bounded **645/645
  PASS**, compile/import **7/7 PASS**, and
  `NEW_FAILURE_DELTA_INTEGRATION = 0`.
- R187 full broad certification is inherited by exact commit/tree identity;
  candidate-induced failures, indeterminate results, and provenance mismatches
  are all `0`.
- R188E preserved the seven protected local dirty identities exactly:
  `PROTECTED_DIRTY_IDENTITIES_PRESERVED_7_OF_7`.
- R189 local closure:
  `PASS_STAGE16A_R189_MEGA_WP2_POST_INTEGRATION_STATE_RECONCILIATION_AND_LOCAL_DELIVERY`.
- Production remote delivery is complete after Push A:
  `MEGA_WP2_PRODUCTION_REMOTE_DELIVERY_COMPLETE`; remote
  `refs/heads/main` was independently verified at
  `f67262c31a7a5611daf392ce8ce3b26ff9fb233a`.
- R190 state commit records the completed Mega-WP2 production remote delivery.
- Final remote state closure remains pending Push B:
  `MEGA_WP2_REMOTE_STATE_CLOSURE_PENDING_PUSH_B`.
- Current task: `STAGE16A_MEGA_WP2_REMOTE_DELIVERY_CLOSURE`.
- Push B has not run yet; **AI Sync NOT run**, **Stage17 NOT started**, and
  **Mega-WP3 NOT started**.

The historical stage sections below, including local-only Stage 9A.7 material,
are retained for provenance and do not override this canonical Stage16A
summary.

## HISTORICAL LOCAL PROJECT RECORD

## Stage 9A.7 — Post/Program Assembly UI Cleanup

- Stage 9A.7 WP2 Review R3 is **COMPLETED_WAITING_REVIEW** on branch `main`, baseline HEAD
  `7dd26867f27e67baef0ca2e7dc04d95663e8d27a`; specification R4 remains approved.
- WP1 Review R7 is approved; total projection and accepted-result preservation remain unchanged.
- WP2 Review R3 blocker remediation is complete and waiting for review; no stage/commit/push.
- WP3-WP6 are not started.
- Managed/external `CURRENT` không nhận explicit shortcut; mọi byte length,
  SHA-256, source/request/order/project/Post/machine/target-path identity bắt
  buộc tồn tại và khớp. Missing identity fail-closed; headline managed MISSING
  không còn tuyên bố `MANAGED_CURRENT`.
- Feature flags chỉ nhận exact bool; external attempt core chỉ có canonical
  dispatch ID; diagnostic/operation/counter/identity evidence được validate
  sớm. Managed explicit negative state và external `explicit_stale` fail-closed;
  output presentation không có `disabled` action authority.
- Callback counter accounting bị khóa theo received/published/discarded/write/
  mutation; external active/failed identity phải complete và
  `ExternalDispatchSourceIdentity` khóa nội dung managed bằng SHA/checksum/
  bytes/provenance. `dirty_state` và confirmation rejection là audit output;
  dead core input field count = 0.
- R6 coi `current_request_fingerprint` là authority duy nhất; generation attempt
  phải exact-match project/request/order source hiện tại; stale active reject,
  stale terminal ignore. Callback publish phải có accepted result cùng attempt và
  provenance; generation artifact-write/selection-mutation = 0; automatic
  downstream count derive từ evidence.
- R7 thêm optional output root cho review worker và dùng `tmp_path` trong test
  Stage 8A.4.1; production default/behavior, permission và WP1 semantics không
  đổi. Exact test đạt 3/3, shared DERIVED manifest bất biến. Legacy artifact
  cleanup thất bại do owner/ACL cũ; không Administrator hoặc reset ACL rộng.
- QA R7 đạt **280 passed** focused và **166 passed** regression. Full lặp lại
  **2150 passed, 2 deselected**, failure/error = 0; `pip check`, `compileall src
  tests tools` và `git diff --check` PASS; chưa stage, commit hoặc push.
- Projector chỉ nhận immutable evidence và không đọc/ghi SQLite, không gọi
  Calculate/Simulation/Generate/Save Managed/Export/project Save. External
  publication trong source là synchronous; không có external worker/cancel API.
- SQLite schema **v4**, `.HMS` contract, Post/Program Assembly schema,
  artifact formats và CAM algorithms không đổi; không migration. Stage 9A.I1
  icon pack vẫn **PAUSED/DEFERRED**, không được mở lại.
- Chưa commit, chưa push; các thay đổi WP1 được giữ để review trước lifecycle
  Git cuối.

## Stage 8A.4.4 — Kiến trúc cài đặt và dữ liệu

- Stage 8A.4.4 đã **COMPLETED**; provenance package lịch sử từng dùng baseline
  `3b70b5c`, còn baseline hiện tại sau phê duyệt R4 là
  `7dd26867f27e67baef0ca2e7dc04d95663e8d27a`; package GUI R3 đã
  được người dùng duyệt; câu “chưa có stage kế tiếp” chỉ đúng tại checkpoint
  lịch sử đó, còn Stage 9A.7 hiện đang `IN PROGRESS`.
- Install root production là `HMS-CADCAM install root` và read-only khi runtime chạy.
  Machine shared root là `ProgramData machine-wide root` với đúng `Tool-Library`,
  `Program-Templates`, `Posts`, `Machines`, `Materials`, `Config`, `Schemas`,
  `Backups`.
- Roaming AppData giữ Config/UI-State/Profiles; Local AppData giữ Cache/Logs/Temp/Crash.
  `.HMS` và CAM project vẫn ở vị trí người dùng chọn, không tự chuyển scope.
- Resolver dùng Windows Known Folders và typed injection cho test/review, không
  dùng CWD hay environment override production. Bootstrap tạo phần được phép,
  ghi `storage-layout.json` atomic và rollback chỉ thư mục mới/rỗng khi lỗi.
- Security chặn traversal, root escape, UNC mặc định, reparse/symlink/junction,
  reserved name, trailing dot/space, invalid character, case/file collision và
  target không hỗ trợ atomic rename.
- Config precedence, lock theo resource, checksum/read-after-write, backup
  retention và migration non-destructive đã có nền tảng production/test.
- `.BAKUPHMS` đã có typed manifest/checksum, 14 category, selective creation,
  validation fail-closed, preview/conflict/permission, atomic restore/rollback
  và tách scope USER_ROAMING/MACHINE_SHARED; không chứa project/executable/secret.
- User profile UI đã có index/component checksum, UUID directory, CRUD/default,
  backup/import-as-copy và runtime switch VI/EN/KO với invariant bảo vệ
  workspace, project, selection, dirty state, CAM worker, Simulation/Post.
- UI `Vị trí dữ liệu` và startup diagnostic không modal hỗ trợ VI/EN/KO,
  accessibility và DPI 100/125/150; không có nút đổi root production.
- QA cuối đạt **122 passed** focused, **194 passed** regression liên quan và
  **1870 passed, 2 deselected** toàn dự án; package Git-ignored đúng 57 file
  (40 PNG, 16 JSON, 1 Markdown), PNG/source mismatch và visual evidence missing
  đều bằng 0.
- SQLite vẫn schema v4; project/container, Tool v1/v2, CAM calculation và
  Simulation/Post fail-closed không đổi.

## Stage 8A.4.3 — Hệ thống đa ngôn ngữ

- Stage 8A.4.3 đã **COMPLETED** trên baseline `4f7e8d7` sau khi người dùng duyệt
  package GUI cuối; không có stage kế tiếp đang thực hiện.
- Phạm vi là locale typed `VI_VN`, `EN_US`, `KO_KR`, catalog tập trung,
  persistence preference người dùng, đổi ngôn ngữ runtime, accessibility,
  glossary, audit và review native Windows.
- Tiếng Việt là mặc định và fallback ưu tiên, độc lập locale Windows. Preference
  không được ghi vào `.HMS`, manifest, `project.db` hoặc CAM payload.
- Property/model/delegate, viewport/status/diagnostic, notification, dialog và
  accessibility đã retranslate sạch qua chuỗi
  VI→EN→KO→VI→EN→KO→VI; workspace, selection, dirty state, worker, geometry,
  dock/tab identity và project state được giữ nguyên.
- Rendered audit bao phủ QAction, menu/ribbon, widget, model/header/cell,
  delegate, log/status/diagnostic, QFileDialog sidebar, QTabBar/dock tab,
  font metrics, tooltip/accessibility và notification formatter; package local
  đúng 28 file có source fingerprint và QA metadata, đã được người dùng duyệt.
- QA cuối đạt **58 passed** focused Stage mới, **140 passed** regression liên
  quan, **198 passed** nhóm Stage và **1748 passed, 2 deselected** toàn repository;
  `pip check`, `compileall src tests tools` và `git diff --check` đạt.
- SQLite giữ schema **v4**; Tool payload v2 và compatibility Tool v1 không đổi.
- Chưa triển khai ProgramData/install layout, importer đa định dạng đầy đủ,
  Export 3D/version settings, CAM workflow ba bước, Tool đa họ, màu đường chạy
  dao, Program Templates hoặc Production Post mới.

## Stage 8A.4.2 — Kiến trúc hai chế độ tài liệu

- Stage 8A.4.2 đã **COMPLETED** trên baseline `88aff78`; không stage kế tiếp
  hoặc stage đa ngôn ngữ nào được bắt đầu.
- `ProjectService` định tuyến typed state cho `CAD_DOCUMENT` và `CAM_PROJECT`.
- CAD đơn lẻ dùng một file container `.HMS` deterministic/checksummed; tên file
  giữ Unicode và dấu cách hợp lệ trên Windows.
- Dự án CAM mới dùng thư mục vật lý ASCII/hyphen, `manifest.json`, SQLite v4,
  `source/`, `working-geometry/`, `autosave/`, `backups/`, `temp/` và
  `replaced/`, cùng inbox atomic `incoming-geometry/{staging,pending,applied,
  rejected,failed}`.
- Loader tiếp tục nhận dự án thư mục `.HMS` cũ, `project.hms.json` và
  `.replaced`; không migrate phá dữ liệu.
- Open dialog và kéo-thả dùng chung application command/importer nền; mode chỉ
  chuyển sau khi import/validation thành công.
- Tạo dự án từ tài liệu hiện tại dùng staging/publish/rollback; file `.HMS` hoặc
  source ban đầu không bị xóa.
- Lệnh `Nạp 3D mới cho dự án CAM` xác minh root/Project ID/SQLite/lock/quyền
  ghi, không sửa project.db ở bước gửi và giữ `.HMS` độc lập.
- Scanner chạy sau recovery/open bằng worker, kết hợp watcher + polling; UI có
  notification không modal, notification center và preview Add/Replace/Update
  không có mặc định nguy hiểm.
- Apply dùng claim/checksum/backup/evidence/rollback; chỉ source dependency liên
  quan stale, không copy READY/SAFE và không tự Calculate/Simulation/Post.
- Review package Git-ignored đã được người dùng duyệt, có đúng 43 file (30 PNG
  hash riêng, 12 JSON và 1 Markdown).
- QA khóa cuối: **136 passed** focused Stage, **113 passed** regression liên
  quan và **1690 passed, 2 deselected** toàn repository; `pip check`,
  `compileall`, audit GUI tiếng Việt với toàn bộ nhóm lỗi bằng 0 và
  `git diff --check` đạt.
- SQLite giữ schema **v4** và loader tương thích ngược project thư mục `.HMS`
  legacy. File `.HMS` đơn lẻ không phải container dự án CAM.
- Geometry transfer chỉ quản lý exact geometry/provenance/stale dependency;
  không phải chứng nhận an toàn, không tự Calculate/Simulation/Post và không
  xác nhận machine-ready clearance.
- Ngoài phạm vi: hệ thống đa ngôn ngữ, ProgramData/install layout, importer đa
  định dạng đầy đủ, Export 3D/version settings, three-step CAM workflow, Tool
  đa họ, màu toolpath, Program Templates và Production Post mới.

## Stage 8A.4.1 — Nền tảng cấu hình Tool theo chương trình

- Stage 8A.4.1 đã **COMPLETED** trên baseline `af3bbf3`; package GUI đã được
  người dùng duyệt và chưa bắt đầu Stage 8A.4.2.
- Phạm vi hoàn thành chỉ gồm common defaults, profile tùy chọn theo strategy,
  schema/validation, persistence, resolver/provenance, stale rules, Tool editor,
  tests, tài liệu và review package.
- `ToolDefinition` giữ payload v1 nguyên trạng khi chưa có cấu hình và dùng
  payload v2 khi có common defaults/profile; physical fingerprint không đổi,
  configuration fingerprint được tách riêng.
- Registry đã có schema typed riêng cho Z-Level, Parallel và Khoan. Resolver
  dùng precedence operation override → Tool profile → common defaults →
  automatic policy → safe fallback, kèm provenance/dependency contribution.
- Function Editor Z-Level/Parallel đọc profile theo Tool đang chọn, manual
  override luôn thắng; Z-Level/Parallel/Khoan có preview xác nhận lưu cho Tool
  và không tự Calculate.
- Profile thay đổi calculation semantics chỉ stale operation cùng Tool/strategy;
  metadata trình bày không stale Simulation. Safety/Simulation/Post gate cũ
  không được lưu trong profile và tiếp tục fail-closed.
- Review package Git-ignored có đúng 24 file (16 PNG có hash riêng, 7 JSON,
  1 Markdown). Cả ba DPI dùng QPA Windows và font production Segoe UI; probe
  coverage/pixel cho missing, replacement và tofu đều bằng 0; người dùng đã
  duyệt package và focused QA cuối đạt 41 passed.
- Regression tập trung đạt 307 passed; full QA đạt **1601 passed,
  2 deselected**. `pip check` và `compileall src tests tools` đều đạt.
- Không triển khai quy trình ba bước hoàn chỉnh, chương trình mẫu,
  Import/Export profile, thay đổi thuật toán CAM/Tool đa họ, Production Post
  hoặc chứng nhận machine-ready trong stage này.
- Compatibility theo Tool family chỉ là kiến trúc fail-closed; cấu hình Tool
  không phải chứng nhận an toàn và không tạo claim G-code production.
- SQLite giữ schema **v4**, không migration; Tool/project v4 cũ, Save/Open và
  Autosave đã được kiểm tra tương thích ngược.

## Checkpoint Stage 8A.3.3 — Z-Level Production Function Editor

- Stage 8A.3.3 đã **COMPLETED**; package GUI native Windows cuối đã được người
  dùng duyệt và chưa bắt đầu stage tiếp theo.
- Production editor đã tích hợp tham số tự động/tùy chỉnh thủ công, Operation
  Manager, lifecycle worker, persistence, accessibility, minh họa và
  responsive/DPI.
- Z-Level giữ nguyên algorithm **v2**, payload **v1** và SQLite schema **v4**;
  dependency và icon không đổi.
- Simulation chỉ mở với artifact hiện hành **READY + SAFE v2**. Production Post
  vẫn fail-closed; machine-ready clearance chưa được xác minh hoặc chứng nhận.
- Review package có 27 ảnh kỹ thuật, 1 montage, 9 JSON và 1 Markdown; 28/28 PNG
  có hash riêng. Localization audit 115.103 record và mọi leak/duplicate/acronym
  count đều bằng 0.
- QA khóa cuối đạt **302 passed** focused và **1559 passed, 2 deselected** toàn
  repository; `pip check`, `compileall src tests tools` và `git diff --check`
  đều đạt.

## Checkpoint Multi-operation Program Assembly

- Baseline source 7D.3.2: `4d8deab` (`hoan thanh Multi operation assembly UI
  giai doan 7D3.2`), kế thừa đầy đủ 7D.3.1 tại `8555747`.
- Worktree sạch trước audit ngày 21-07-2026; `git diff --check` đạt.
- Python dự án: 3.14.6 trong `.venv`; package compile/import đạt.
- SQLite giữ nguyên schema **v4** (`DATABASE_SCHEMA_VERSION = 4`).
- Toàn bộ pytest: **999 passed**; `pip check` và `compileall src tests` đều đạt.
- GUI smoke 7D.3.2 chạy thành công ở chế độ offscreen, tự đóng và không
  Generate/Export NC.

## Nền tảng ứng dụng, dự án, CAD và XCAF

- Giai đoạn 1–4: khung PySide6, dự án thư mục `.HMS` legacy, Session Lock,
  Autosave, Recovery, CAD Kernel và CAD Viewer đã hoàn thành; Stage 8A.4.2 giữ
  loader tương thích và bổ sung workspace CAM dạng thư mục không hậu tố.
- Giai đoạn 5A–5D: import CAD, Measurement BREP, topology tree và CAD view state
  đã hoàn thành.
- Giai đoạn 6A.1–6A.4: XCAF technical spike, domain model, viewer/tree và
  persistence đã hoàn thành.
- `ProjectService` là API dự án được UI sử dụng; manifest dùng JSON UTF-8, dữ
  liệu chính dùng SQLite v4, CAD gốc được giữ nguyên trong `source/`, cache có
  thể xóa và tái tạo.
- OCP/Open CASCADE được cô lập sau adapter; các tác vụ import/I/O nặng không
  chạy trực tiếp trong UI thread.

## CAM và Simulation/Collision

- Các operation hiện có: Facing, Planar Face Facing, Contour, Pocket, Drilling,
  Tapping, Reaming và Boring.
- Tapping đã có domain/toolpath/UI/viewer nhưng production Post vẫn fail-closed.
- Simulation/Collision v1 gồm foundation 7C.1, Viewer 7C.2 và UI/cache 7C.3;
  kết quả có PASS/WARN/FAIL, provenance/fingerprint, stale/cancel guard và
  project lifecycle.
- PASS chỉ có nghĩa không phát hiện vấn đề trong phạm vi và resolution v1;
  không phải chứng nhận an toàn máy.

## Chuỗi Post Processor đã tích hợp

- 7D.1: `ToolpathArtifact` single-operation được preflight qua Simulation gate,
  lower thành `NCProgramIR`, rồi adapter tạo `PostResult` deterministic trong
  bộ nhớ.
- 7D.2.1: production profile FANUC ROBODRILL 21i `.fn`, MM, G54, XYZ/XY,
  CRLF/UTF-8 và validation fail-closed.
- 7D.2.2: managed artifact, manifest/sidecar/SHA-256 và external export đến thư
  mục local, ổ mạng đã map hoặc UNC.
- 7D.2.3: Apply/Validate/Generate, exact read-only preview, Save Managed Artifact
  và explicit filesystem export cho single operation.
- 7D.3.1: `ProgramAssemblyService` ghép nhiều immutable operation snapshot theo
  explicit order, tạo nhiều independent tool section với một global
  header/footer và deterministic checksum/provenance.
- 7D.3.2: tab `Program Assembly` trong CAM workspace cung cấp danh sách operation,
  context/binding editor, compatibility/Simulation diagnostics, background
  Generate, preview/navigation, managed save và explicit external export.

Luồng multi-operation hiện tại:

```text
Ordered ToolpathArtifact snapshots
  → per-operation Simulation gate + production context
  → NCProgramIR sections
  → ProgramAssemblyResult / canonical .fn / SHA-256
  → read-only preview
  → project-managed artifact + manifest/sidecar
  → explicit local/mapped/UNC filesystem export
```

Workflow single-operation 7D.2.3, golden output và export contract cũ vẫn được
giữ tương thích.

## Lifecycle và giới hạn

- Đổi order, T/H/D, safe Z, cutter policy, shared context, Simulation result,
  source Toolpath hoặc project generation làm result cũ stale; UI không tự
  regenerate hoặc export.
- V1 chỉ hỗ trợ một Job/Setup/Machine/profile, MM, G54, ba trục XYZ và mặt
  phẳng XY trong một assembly.
- Mỗi operation luôn là một section độc lập; chưa tự group cùng dao hoặc tối ưu
  tool change.
- Chưa có production Tapping, stock removal, machine kinematics, 4/5-axis,
  direct CNC transfer, FTP/SFTP/HTTP/DNC hoặc machine certification.
- Output ROBODRILL 21i phải được manual review và dry-run/single-block trước khi
  sử dụng sản xuất.

## Tài liệu liên quan

- `docs/CAM_MULTI_OPERATION_ASSEMBLY_7D3_1.md`: contract/service 7D.3.1.
- `docs/CAM_MULTI_OPERATION_UI_7D3_2.md`: workflow UI/lifecycle 7D.3.2.
- `docs/references/` và `docs/reference/`: chỉ mục/quy tắc tài liệu tham khảo;
  file riêng trong `reference_private/` không phải source of truth.
