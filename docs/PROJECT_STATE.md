# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1–2: khung PySide6 và hệ thống dự án thư mục `.HMS` đã hoàn thành.
- Giai đoạn 3–4: Session Lock, Autosave, Recovery, CAD Kernel và CAD Viewer đã hoàn thành.
- Giai đoạn 5A–5D: import CAD, Measurement BREP, topology tree và CAD view state đã hoàn thành.
- Giai đoạn 6A.1–6A.4: XCAF technical spike, domain model, viewer/tree và persistence đã hoàn thành.
- Giai đoạn 7A.1–7B.9: CAM Foundation, UI, Facing, Contour 2D, Pocket, Drilling, Tapping, Reaming và Boring v1 đã hoàn thành.
- Simulation/Collision v1 đã hoàn thành và review ổn định; review commit: `6fdbd45`.

## Lịch sử Simulation/Collision

- `64e049b` — Simulation/Collision Foundation 7C.1.
- `bfb1ea5` — Simulation Viewer Integration 7C.2.
- `c4588ce` — Simulation UI + External Cache 7C.3.
- `6fdbd45` — Review và ổn định Simulation/Collision v1.

## Simulation/Collision v1

- Request/result/issue/statistics immutable, versioned; codec nghiêm ngặt và deterministic.
- Sampling LINE/ARC; transform WCS/world; fixed tool-axis policy.
- Envelope cutter/shank/holder; stock BOX; fixture BODY/OCCURRENCE.
- Pipeline broad/narrow; kết quả PASS/WARN/FAIL; runtime publish nguyên tử.
- Guard stale/cancel/project; semantic path overlay, collision/gouge/warning markers.
- Decimation deterministic; issue metadata/focus; Simulation panel và progress.
- Cooperative cancellation; Show/Hide/Clear overlay; external cache; Save/Open, Save As, Autosave/Recovery.

## Review đã sửa

- Fixture unit mismatch; approximate narrow evidence; computation token release.
- Cache link/junction restoration; orphan cache cleanup khi xóa operation cha.
- Zero exposed shank khi stickout đúng bằng cutter end.

## CAM hiện có

- Facing; Planar Face Facing; 2D Contour; Pocket v1; Drilling v1; Tapping v1; Reaming v1; Boring v1; Simulation/Collision v1.

## Giới hạn hiện tại

- PASS chỉ có nghĩa không phát hiện vấn đề trong phạm vi/resolution v1.
- Chưa có stock removal, animation, machine kinematics/IK, continuous exact collision guarantee, Post Processor hoặc G-code.

## Kiến trúc, dữ liệu và kiểm tra

- `ProjectService` là API dự án duy nhất được UI sử dụng; manifest JSON UTF-8 và dữ liệu chính dùng SQLite v4.
- Dự án là thư mục `.HMS`; file CAD nguồn giữ nguyên trong `source/`; cache có thể xóa và tái tạo.
- Tác vụ I/O/import CAD chạy ngoài UI thread; OCP chỉ ở adapter nội bộ; CAD Viewer và XCAF persistence đã ổn định.
- Toàn bộ pytest: **887 passed**; CAM/CAD/XCAF regression mục tiêu: **544 passed**.
- GUI Windows/PySide6/OCP thật: đạt; worktree sạch tại review commit `6fdbd45`.
