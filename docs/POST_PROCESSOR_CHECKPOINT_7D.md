# Post Processor integration checkpoint — 7D

## Baseline và kết luận audit

Checkpoint tài liệu này audit source baseline `dbc7dd7`, gồm Post Foundation 7D.1, FANUC ROBODRILL 21i 7D.2.1, export/data-server lifecycle 7D.2.2, Post UI 7D.2.3 và GUI fix `7b06c25`. Chuỗi Post hiện tại đã tích hợp đầy đủ cho **một operation**; checkpoint không thêm tính năng hoặc sửa source code.

```text
ToolpathArtifact
  → Simulation gate
  → NCProgramIR
  → FANUC ROBODRILL 21i PostResult
  → read-only NC preview
  → project-managed .fn + manifest/sidecar/SHA-256
  → local/mapped/UNC filesystem export
```

## Kiến trúc Post hiện tại

- `ToolpathArtifact` đã publish, còn current và có provenance hợp lệ là nguồn chuyển động duy nhất. Post không gọi lại toolpath generator và không sửa dữ liệu CAD/CAM nguồn.
- `PostSourceSnapshot` gom operation, setup/WCS, tool assembly, tool/holder, machine và Simulation result. Preflight kiểm tra identity, revision và fingerprint trước lowering.
- `SimulationGatePolicy` mặc định `REQUIRE_PASS`; `ALLOW_WARN` chấp nhận PASS/WARN; `OPTIONAL` cho phép thiếu kết quả nhưng vẫn chặn FAIL. Simulation stale hoặc malformed không bao giờ được coi là pass.
- Lowering giữ semantics chuyển động và tạo controller-neutral `NCProgramIR` cho đúng một operation và một tool activation.
- `PostRuntimeService` chọn adapter, validate IR/output, tạo checksum SHA-256 và chỉ publish `PostResult` khi input vẫn current. Canonical text của production result là nguồn byte duy nhất cho export.

## Profile FANUC ROBODRILL 21i

Production profile `robodrill_fanuc_21i_worknc_expanded_v1` dùng adapter `fanuc_robodrill_21i_worknc_v1` với các ràng buộc:

- File `.fn`, UTF-8 không BOM, CRLF, output deterministic.
- MILL ba trục XYZ, MM only, absolute, XY plane và G54 only.
- Một `ControllerToolBinding` T/H/D cho một tool section; `ProductionProgramContext` giữ filename, safe Z và process context có fingerprint.
- Safe header/footer, tool change `M06Tn`, length compensation `G43...Hn`, spindle/coolant và arc G02/G03 I/J được validate nghiêm ngặt.
- Tapping production fail-closed; không tự suy diễn canned cycle hoặc synchronous tapping code.

Đây là profile production-format, không phải bằng chứng machine certification.

## Generate, preview, save và export

1. UI chọn một CAM operation và chụp snapshot bất biến.
2. Apply validate atomically profile, filename, safe Z, G54, T/H/D, cutter policy và Simulation gate.
3. Validate/Generate chạy Post với latest-wins stale guard. Generate chỉ tạo `PostResult` trong bộ nhớ, không ghi file.
4. Preview hiển thị exact canonical text ở chế độ read-only, cùng line count, byte count, CRLF/UTF-8 và SHA-256; gutter UI không thuộc payload.
5. Save Managed Artifact ghi nguyên byte vào `nc/<name>.fn`, sidecar vào `post/metadata/` và `post/manifest.json`.
6. Export là hành động explicit, sao chép nguyên byte đã verify đến một filesystem directory local, ổ mạng đã map hoặc UNC. Không có credential management hay direct CNC/DNC transfer.

## Lifecycle và stale guards

- Post publish dùng request token, project generation và full input fingerprint; callback cũ không thể thay kết quả mới.
- Thay draft đã Apply, operation, Toolpath artifact, setup/tool/machine context hoặc Simulation làm Post/managed artifact liên quan stale.
- Export kiểm tra `PostResult` PUBLISHED, provenance, production profile, Simulation gate, checksum và current source cả trước lẫn sau ghi.
- Managed publish dùng atomic temp/write/verify/replace cho output, sidecar và manifest; lỗi giữa transaction được rollback. External copy chỉ chạy sau managed publish và được đọc lại để kiểm tra length/SHA-256.
- Save/Open, Save As, Autosave/Recovery, project switch và Close không tự generate hoặc tự external-export. Missing/tampered/stale artifact được phân loại mà không làm hỏng toàn dự án.
- GUI smoke 7D.2.3 phát hiện output projection chưa refresh sau generate đồng bộ và Post chưa hiện stale khi artifact đổi. Commit `7b06c25` đã sửa hai lỗi và bổ sung regression tests.

## Kết quả kiểm thử checkpoint

- Python 3.14.6, pytest 9.1.1: **980 passed**.
- `python -m pip check`: không có dependency hỏng.
- `python -m compileall src tests`: đạt; package imports đạt.
- SQLite xác nhận giữ nguyên schema v4.
- `git diff --check`: đạt ở baseline.

Lệnh `python` ngoài virtual environment không có pytest; các kiểm tra dự án được chạy lại bằng `.venv\Scripts\python.exe`, đúng Python 3.14.6 của repository.

## Giới hạn và bước tiếp theo

Hiện tại chỉ hỗ trợ single operation, một tool section, MM, G54, XYZ/XY plane. Chưa có multi-operation assembly, production Tapping, automatic tool optimization, direct CNC transfer, FTP/SFTP/HTTP/DNC, 4/5-axis, stock removal, machine kinematics hoặc machine certification.

Vì pipeline single-operation đã có IR, provenance, Simulation gate, deterministic checksum và project-managed export, bước kế tiếp hợp lý là 7D.3 Multi-operation Program Assembly: ghép nhiều operation/tool section trong khi tái sử dụng các contract an toàn hiện có. Checkpoint này chỉ đặt phạm vi, không triển khai 7D.3.

Mọi `.fn` phải được người có trách nhiệm review thủ công và kiểm tra bằng dry-run/single-block theo quy trình tại máy trước khi sử dụng sản xuất. Output hiện tại chưa được machine-certified.
