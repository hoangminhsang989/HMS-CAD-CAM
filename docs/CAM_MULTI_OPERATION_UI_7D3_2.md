# Multi-operation Program Assembly UI — 7D.3.2

7D.3.2 bổ sung workflow Program Assembly trong CAM workspace. Panel được mở
trong tab riêng, lấy cảm hứng từ Operation Manager nhưng có giao diện HMS độc
lập. Post Processor tab hiện hữu vẫn là workflow single-operation của 7D.2.3.

## Explicit order và operation list

Panel giữ một danh sách runtime theo `OperationId` typed. Số thứ tự trên bảng
chỉ là projection; nó không bao giờ là identity. Add Selected Operation chỉ
thêm operation hiện tại nếu operation thuộc project, cùng Job/Setup/Machine,
enabled, artifact VALID và source có thể capture. Operation trùng bị từ chối.
Move Up/Move Down đổi hai phần tử một cách atomic, không tự sort theo dao,
không tự group cùng dao và không dựa vào tên hoặc timestamp. Tapping vẫn có
thể hiển thị trong bảng để báo lỗi, nhưng bị chặn ở Validate/Generate và
không sinh G84/G74/M29.

Mỗi dòng hiển thị operation/status, ToolpathArtifact, Simulation, T/H/D, safe Z,
cutter compensation, spindle/RPM, ước lượng số dòng và compatibility code.
ToolAssembly dùng chung được hiển thị là các section độc lập; tool change
không được tối ưu tự động.

## Compatibility, binding và simulation gate

Validate capture snapshot immutable và gọi `ProgramAssemblyService` validation;
nó không format NC, publish result, ghi file hoặc sửa field. Chẩn đoán gắn
operation/section và hiển thị code riêng cho setup, Job, machine, profile,
unit/WCS, artifact missing/stale, disabled/unsupported operation, binding
conflict, safe Z, G41/D và Tapping.

Shared context dùng profile ROBODRILL 21i v1: `.fn`, MM, G54, ABSOLUTE, XY,
CRLF, UTF-8 và filename security contract của 7D.2.2. Global metadata là các
cặp `key=value`; filename không hợp lệ chặn Generate. Per-operation editor có
T/H/D, safe Z, cutter policy và tool comment. Draft invalid không mutation;
Apply atomic. Action `Set T = H = D` chỉ thực hiện khi người dùng yêu cầu.

Simulation summary hiển thị PASS/WARN/OPTIONAL missing/FAIL và stale/malformed.
`REQUIRE_PASS` cần mọi operation current PASS; `ALLOW_WARN` cho PASS/WARN nhưng
FAIL chặn; `OPTIONAL` cho phép missing với warning nhưng FAIL/stale/malformed
vẫn chặn. Panel không tuyên bố “safe tuyệt đối” hoặc “machine certified”.

## Generate, Preview, Save Managed và Export

Generate chỉ chạy sau Validate thành công, có order/context current và không có
request active cùng key. Worker nhận immutable request, không chạm QWidget;
epoch, project generation, operation order và input fingerprint được kiểm tra
khi callback về UI. Callback cũ bị bỏ. Generate chỉ publish
`ProgramAssemblyResult`, không ghi `.fn`; lỗi không tạo partial result và giữ
result cũ nếu result đó vẫn current.

Preview là read-only và lấy đúng `canonical_text` production. Metadata gồm
profile/version, operation/section/tool-change count, line/byte count, CRLF,
encoding, SHA-256, validation status, assembly fingerprint, ordered operation
provenance và `NOT CERTIFIED / REVIEW REQUIRED`. Search, copy checksum, nhảy
section và nhảy diagnostic được hỗ trợ. Mapping section dùng operation ID và
section index của plan; line gutter nếu có chỉ là UI.

Save Managed Artifact và External Export là hai action rõ ràng. Cả hai dùng
`NCExportService.export_assembly()` hiện có; sidecar/manifest giữ assembly
result/fingerprint, ordered operation IDs, section count, source artifact và
binding fingerprints. External target nhận local/mapped/UNC path, overwrite
policy và confirmation summary. External failure giữ managed artifact; không
lưu credentials, map drive, retry vô hạn, mở/chạy file hoặc truyền CNC.
Clear Managed Artifact chỉ xoá payload/sidecar assembly trong project-managed
`nc/` và `post/metadata/`, không xoá external file, Toolpath/Post/Assembly
source.

## Lifecycle và trạng thái

Assembly status: `MISSING`, `DRAFT`, `INVALID`, `VALID`, `GENERATING`, `CURRENT`,
`STALE`, `FAILED`. Managed artifact status: `MISSING`, `CURRENT`, `STALE`,
`TAMPERED`, `FAILED`. External status: `NEVER_EXPORTED`, `EXPORTED`, `OUTDATED`,
`FAILED`. Đổi order, binding, safe Z, G41, simulation, source artifact,
operation disable/delete hoặc shared output context đều làm assembly result
stale và không tự regenerate/export.

Project Open chỉ inspect managed manifest, không Generate và không dirty project.
Save As/Autosave/Recovery dùng lifecycle workspace hiện có; external destination
không được coi là current. Project switch/close invalidate worker callbacks và
clear UI runtime state. Managed artifact hợp lệ không bị xoá. Post UI
single-operation, golden bytes 7D.2.3 và export contract 7D.2.2 không phụ thuộc
assembly list.

Phạm vi này chưa triển khai production Tapping, automatic tool ordering/grouping,
nhiều Setup/WCS, nhiều machine/profile, stock removal, kinematics, 4/5-axis,
direct CNC communication hoặc machine certification. Output phải được review,
dry-run/single-block thủ công trước khi dùng trên máy.
