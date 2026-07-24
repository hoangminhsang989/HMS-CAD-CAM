# Trạng thái dự án HMS CAD/CAM

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

- Giai đoạn 1–4: khung PySide6, dự án thư mục `.HMS`, Session Lock, Autosave,
  Recovery, CAD Kernel và CAD Viewer đã hoàn thành.
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
