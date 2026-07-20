# Trạng thái dự án HMS CAD/CAM

## Baseline checkpoint Post Processor

- `HEAD` đầu vào đã audit: `dbc7dd7` (`tao he thong thu muc tai lieu tham khao`); không có thay đổi source code sau baseline này trong checkpoint.
- Worktree sạch trước audit; sau commit checkpoint phải tiếp tục sạch.
- Python dự án: 3.14.6 trong `.venv`; package imports đạt.
- SQLite giữ nguyên schema **v4** (`DATABASE_SCHEMA_VERSION = 4`).
- Toàn bộ pytest ngày 20-07-2026: **980 passed**; `pip check`, `compileall src tests` và `git diff --check` đều đạt.

## Nền tảng ứng dụng, CAD và XCAF

- Giai đoạn 1–4: khung PySide6, dự án thư mục `.HMS`, Session Lock, Autosave, Recovery, CAD Kernel và CAD Viewer đã hoàn thành.
- Giai đoạn 5A–5D: import CAD, Measurement BREP, topology tree và CAD view state đã hoàn thành.
- Giai đoạn 6A.1–6A.4: XCAF technical spike, domain model, viewer/tree và persistence đã hoàn thành.
- `ProjectService` là API dự án được UI sử dụng; manifest dùng JSON UTF-8, dữ liệu chính dùng SQLite v4, CAD gốc được giữ nguyên trong `source/`, cache có thể xóa và tái tạo.
- OCP/Open CASCADE được cô lập sau adapter; các tác vụ import/I/O nặng không chạy trực tiếp trong UI thread.

## CAM và Simulation/Collision hiện có

- Các operation hiện có: Facing, Planar Face Facing, Contour, Pocket, Drilling, Tapping domain/toolpath, Reaming và Boring.
- Tapping đã có domain/toolpath/UI/viewer nhưng production Post hiện vẫn fail-closed.
- Simulation/Collision v1 gồm foundation 7C.1, Viewer 7C.2, UI + external cache 7C.3; kết quả có PASS/WARN/FAIL, provenance/fingerprint, stale/cancel guard và project lifecycle.
- Review ổn định Simulation/Collision v1 tại `6fdbd45`; PASS chỉ có nghĩa không phát hiện vấn đề trong phạm vi và resolution v1.

## Chuỗi Post Processor đã tích hợp

- `0b2038e` — Post Foundation 7D.1: `ToolpathArtifact` single-operation được preflight qua Simulation gate, lower thành `NCProgramIR`, rồi adapter tạo `PostResult` deterministic trong bộ nhớ.
- `1b3d1ff` — FANUC ROBODRILL 21i 7D.2.1: production profile/adapter `.fn`, MM, G54, XYZ/XY plane, CRLF/UTF-8 và validation fail-closed.
- `bfac949` — Export/data-server lifecycle 7D.2.2: lưu `.fn` do project quản lý cùng manifest, sidecar và SHA-256; export nguyên byte đến thư mục local, ổ mạng đã map hoặc UNC.
- `bd90166` — Post UI 7D.2.3: Apply/Validate/Generate, preview chính xác ở chế độ read-only, Save Managed Artifact và explicit filesystem export.
- GUI smoke sau 7D.2.3 phát hiện metadata output chưa refresh sau generate đồng bộ và trạng thái Post chưa hiện stale khi Toolpath artifact đổi. Hai lỗi đã được sửa, có regression tests, tại `7b06c25`.
- Luồng hiện tại: `ToolpathArtifact → Simulation gate → NCProgramIR → FANUC ROBODRILL 21i PostResult → read-only NC preview → project-managed .fn + manifest/sidecar/checksum → local/mapped/UNC filesystem export`.

## Giới hạn hiện tại

- Chỉ một operation và một tool section; chưa có multi-operation assembly.
- Chỉ MM, G54, ba trục XYZ và mặt phẳng XY.
- Tapping production fail-closed.
- Không direct CNC transfer; không FTP/SFTP/HTTP/DNC.
- Không stock removal, machine kinematics, 4/5-axis hoặc machine certification.
- Output ROBODRILL 21i **chưa được machine-certified**; luôn cần manual review và dry-run/single-block trước sản xuất.

## Tài liệu tham khảo

- `docs/references/` là cấu trúc ghi chú/chỉ mục công khai theo WorkNC, Mastercam và NX; file nhị phân bên ngoài được giữ cục bộ.
- `docs/reference/` chứa quy tắc sử dụng và chỉ mục cho `reference_private/`.
- `reference_private/` bị Git ignore, không phải source of truth và chỉ được đọc chọn lọc theo task; checkpoint này chỉ xác nhận bốn file đã được người dùng copy, không quét hoặc đọc toàn bộ.
