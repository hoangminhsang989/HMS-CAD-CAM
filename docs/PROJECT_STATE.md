# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1–2: khung PySide6 và hệ thống dự án thư mục `.HMS` đã hoàn thành.
- Giai đoạn 3–4: Session Lock, Autosave, Recovery, CAD Kernel và CAD Viewer đã hoàn thành.
- Giai đoạn 5A–5D: import CAD, Measurement BREP, topology tree và CAD view state đã hoàn thành.
- Giai đoạn 6A.1–6A.4: XCAF technical spike, domain model, viewer/tree và persistence đã hoàn thành.
- Giai đoạn 7A.1–7B.8: CAM Foundation, UI, Facing, Contour 2D, Pocket v1, Drilling v1, Tapping v1 và Reaming v1 đã hoàn thành.
- Review Reaming đã sửa lỗi Viewer provenance trộn strategy khác; commit review `1db864a`, worktree sạch.
- Toàn bộ pytest: **697 passed**.

## Kiến trúc và dữ liệu dự án

- `ProjectService` là API dự án duy nhất được UI sử dụng.
- Dự án là thư mục `.HMS`; manifest dùng JSON UTF-8 và dữ liệu chính dùng SQLite.
- SQLite schema hiện tại: **v4**; Pocket, Drilling, Tapping và Reaming v1 không yêu cầu migration.
- File CAD nguồn được giữ nguyên trong `source/`, không bị chỉnh sửa.
- Tác vụ I/O và import CAD chạy ngoài UI thread.
- CAD API chỉ trao đổi ID/model thuần Python; object OCP, `TDF_Label`, `TopoDS`, AIS và runtime ID chỉ tồn tại trong adapter nội bộ.

## CAD Kernel, import và Viewer

- Open CASCADE được tích hợp qua `cadquery-ocp-novtk`.
- Đã hỗ trợ import STEP/STP, BREP, IGES/IGS và STL.
- STEP part đơn và STEP assembly XCAF hỗ trợ hierarchy lồng nhau, repeated occurrence và transform `parent × local`.
- Viewer/tree/selection và appearance state tách riêng theo occurrence; user override thắng source và có thể reset.
- CAD Viewer hỗ trợ camera, Fit All, hướng nhìn chuẩn và ba display mode.
- Import lỗi, worker cũ hoặc signal đến muộn không thay document hiện tại.

## CAM Foundation

- CAM có ID mạnh, unit tường minh, `GeometryReference` bền vững, Job, Setup, WCS, Stock, tooling và machine model.
- Operation Tree, Dependency DAG và Toolpath IR hỗ trợ dirty propagation, stale-token policy và fingerprint xác định.
- CAM editable state là dữ liệu chính trong SQLite v4; artifact là derived data dưới `toolpaths/`.
- CAM hiện có Facing 2.5D, Planar Face Facing, 2D Contour, Pocket v1, Drilling v1, Tapping v1 và Reaming v1.
- Facing hỗ trợ biên Stock BOX và persistent planar FACE; selected face là target plane, top lấy từ Stock BOX.
- OCP planar-face resolver hỗ trợ repeated/nested XCAF occurrence và fail-closed khi reference không còn hợp lệ.
- 2D Contour hỗ trợ persistent outer loop LINE/ARC; Pocket v1 đã hoàn thành Geometry, Strategy, Viewer, UI và Persistence cho một outer loop, không island.
- Drilling v1 hỗ trợ explicit point pattern, persistent BREP VERTEX, full circular EDGE, SPOT_DRILL, DRILL, PECK_DRILL, Viewer/Recompute và persistence.
- Tapping v1 hỗ trợ RH/LH, RIGID/FLOATING, persistent hole geometry, deterministic multi-hole, CAM UI, Viewer/Recompute và persistence đầy đủ.
- Commit Reaming: `1d523fb` — Reaming Domain/Strategy Foundation; `fad5d33` — Reaming Viewer/Recompute; `a994d4b` — Reaming UI/Persistence; `1db864a` — Review và ổn định Reaming v1.
- Reaming hỗ trợ explicit point, BREP vertex, full circular edge, repeated XCAF occurrence identity, multi-hole xác định, `pre_hole_diameter` bắt buộc và stock allowance dẫn xuất.
- Feed-per-revolution là nguồn chính; IR có controlled feed retract, optional dwell, spindle/coolant semantic; chỉ nhận REAMER và machine hợp lệ.
- Reaming có Viewer/Recompute, CAM UI, Bind/Rebind/Clear, Apply/Generate/Recompute, Show/Hide, Save/Open, Save As và Autosave/Recovery.
- Viewer kiểm tra provenance fail-closed, kể cả khi trộn strategy; GUI Windows/PySide6/OCP thật đã đạt.

## Tree, persistence và session

- Persistent key XCAF dùng `source_id`, occurrence path có phiên bản và product fingerprint; Save/Open/Save As giữ đúng occurrence override và `source_id`.
- Autosave và Recovery hỗ trợ XCAF override và CAM editable state qua SQLite v4.
- Apply/rollback appearance thực hiện nguyên tử và giữ source style khi thất bại.
- CAD view state v2 cũ và migration SQLite v2 → v3 vẫn được hỗ trợ.

## Measurement và giới hạn

- Measurement BREP hỗ trợ tọa độ vertex, khoảng cách, edge, circle/arc, diện tích, thể tích và AABB; luôn read-only, không làm dirty project.
- Hình học chuẩn hóa chưa được lưu vào `model/`; ứng dụng vẫn import lại nguồn bất biến khi mở dự án.
- Chưa có component editing, thay đổi cấu trúc assembly hoặc assembly constraints.
- Chưa có Boring, Simulation, Collision, Post Processor hoặc G-code.
