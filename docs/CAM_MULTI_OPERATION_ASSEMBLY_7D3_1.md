# Multi-operation Program Assembly — 7D.3.1

7D.3.1 cung cấp nền tảng dựng một chương trình FANUC ROBODRILL 21i `.fn` từ nhiều CAM operation. Đây là service/domain foundation, chưa có UI multi-operation.

## Kiến trúc

```text
ProgramAssemblyRequest
  → immutable operation snapshots
  → existing ToolpathArtifact preflight + simulation gate
  → lower từng operation thành NCProgramIR
  → ProgramOperationSection
  → ProgramAssemblyPlan
  → FANUC global formatter/validator
  → ProgramAssemblyResult
  → NCArtifactStore / 7D.2.2 export
```

Formatter FANUC dùng chung các primitive header, operation section và footer với đường single-operation. Vì vậy output 11 golden single-operation và SHA-256 hiện tại không đổi.

## Contract và compatibility

- V1 chỉ dùng `EXPLICIT_OPERATION_ORDER`; caller order được giữ nguyên, order index phải duy nhất và liên tục.
- Một assembly chỉ có một project, Job, Setup, MachineDefinition, production profile, controller adapter, MM, absolute, XY và G54.
- Shared context chứa filename `.fn`, metadata global đã sanitize, encoding UTF-8 và CRLF. Mỗi section có safe Z, T/H/D, ToolAssembly fingerprint và compensation policy riêng.
- Các operation được hỗ trợ là Facing, Planar Face Facing, Contour, Pocket, Drilling expanded, Reaming expanded và Boring expanded.
- Tapping (right-hand và left-hand), operation disabled/invalid/stale, thiếu simulation bắt buộc, mismatch provenance hoặc safe Z không hợp lệ đều chặn toàn assembly; không sinh partial program.

## Output layout

Program có đúng một `%` mở, một `(SHL-TECH)`, một `FileName`, metadata global và `G90G80G49G40G17`. Mỗi operation là một tool section độc lập:

```text
(OPERATION=...,SECTION=...)
G91G28G0Z0
M06Tn
G90G40G54X0.Y0.
G43Z<safe_z>Hn
optional G41Dn
...
optional G40
M09
M05
G91G28G0Z0
```

Assembly không tự gom operation dùng chung dao, không bỏ `M06`, không tự sắp xếp theo dao và không tối ưu tool change. Footer chỉ xuất một lần: `G28Y0.`, `M30`, `%`. Validator kiểm tra two delimiters, one `M30`, no motion after `M30`, section boundary, T/H/D provenance, modal/safety reset, spindle/coolant/compensation balance, arcs, numeric format, line/program size, comment safety, CRLF, no BOM và deterministic bytes.

## Provenance và simulation gate

Fingerprint bao phủ thứ tự operation/section, ToolpathArtifact, SimulationResult và status, ToolAssembly, ControllerToolBinding, operation context, machine/profile, shared context, assembly policy và checksum bytes. Runtime request token, timestamp và path ngoài project không tham gia identity. `REQUIRE_PASS`, `ALLOW_WARN` và `OPTIONAL` được đánh giá độc lập cho từng operation; một FAIL hoặc stale luôn chặn assembly.

## Managed artifact và export

`ProgramAssemblyResult` dùng `NCExportService.export_assembly()` và cùng `NCArtifactStore`, sidecar, manifest, checksum, overwrite policy, local/mapped/UNC export của 7D.2.2. Manifest mở rộng backward-compatible để ghi assembly result fingerprint, thứ tự operation/section, artifact fingerprints, tool-binding fingerprints và operation-context fingerprints. Không có export tự động và không truyền trực tiếp tới CNC.

## Single-operation compatibility

`PostRequest`, `PostRuntimeService`, codec sidecar/manifest, Dummy 7D.1 và Post UI single-operation vẫn giữ public contract. Đường formatter single-operation gọi primitive dùng chung nhưng giữ byte-for-byte output golden.

## Ngoài phạm vi

Chưa triển khai production Tapping, automatic tool ordering/grouping, nhiều Setup/WCS, nhiều machine/profile, stock removal, machine kinematics, 4/5-axis, FTP/SFTP/HTTP/DNC, direct CNC transfer hoặc machine certification. Output vẫn phải được review thủ công, dry-run/single-block trước khi dùng sản xuất.

## Chuyển sang 7D.3.2

Giai đoạn sau có thể bổ sung UI chọn nhiều operation, hiển thị thứ tự explicit, diagnostic theo section, preview toàn chương trình, action build/save/export và lifecycle stale. UI không được tự đọc/ghi `project.db`; mọi thao tác phải gọi service hiện có.
