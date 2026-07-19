# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1–2: khung PySide6 và hệ thống dự án thư mục `.HMS` đã hoàn thành.
- Giai đoạn 3–4: Session Lock, Autosave, Recovery, CAD Kernel và CAD Viewer đã hoàn thành.
- Giai đoạn 5A–5D: import CAD, Measurement BREP, topology tree và CAD view state đã hoàn thành.
- Giai đoạn 6A.1–6A.4: XCAF technical spike, domain model, viewer/tree và persistence đã hoàn thành.
- Giai đoạn 7A.1–7A.7: CAM Foundation, persistence và hardening đã hoàn thành.
- Commit mới nhất: `0de9d02`.
- Toàn bộ pytest: **424 passed**.

## Kiến trúc và dữ liệu dự án

- `ProjectService` là API dự án duy nhất được UI sử dụng.
- Dự án là thư mục `.HMS`; manifest dùng JSON UTF-8 và dữ liệu chính dùng SQLite.
- SQLite schema hiện tại: **v4**.
- File CAD nguồn được giữ nguyên trong `source/`, không bị chỉnh sửa.
- Tác vụ I/O và import CAD chạy ngoài UI thread.
- CAD API công khai chỉ trao đổi ID và model thuần Python, bất biến.
- Object OCP, `TDF_Label`, `TopoDS` và AIS chỉ tồn tại trong adapter nội bộ.
- Persistent state không lưu runtime `CadDocumentId`, `CadObjectId` hoặc object native.

## CAD Kernel, import và Viewer

- Open CASCADE được tích hợp qua `cadquery-ocp-novtk`.
- Đã hỗ trợ import STEP/STP, BREP, IGES/IGS và STL.
- STEP part đơn và STEP assembly XCAF hỗ trợ hierarchy lồng nhau, repeated occurrence và transform `parent × local`.
- Viewer/tree/selection và appearance state tách riêng theo occurrence; user override thắng source và có thể reset.
- CAD Viewer hỗ trợ camera, Fit All, hướng nhìn chuẩn và ba display mode.
- Import lỗi, worker cũ hoặc signal đến muộn không thay document hiện tại.

## CAM Foundation

- Strongly typed CAM IDs, units tường minh và `GeometryReference` bền vững đã hoàn thành.
- `CamJob`, Setup, WCS, Stock và Fixture có invariant, revision và mutation nguyên tử.
- Tooling, holder, tool assembly và machine model độc lập controller đã hoàn thành.
- Operation Tree bất biến và Dependency DAG hỗ trợ topo order, dirty propagation và stale-token policy.
- Toolpath IR controller-neutral hỗ trợ motion/event, arc, bounds, statistics và fingerprint xác định.
- CAM editable state là dữ liệu chính; tooling/machine snapshot và aggregate được lưu trong SQLite v4.
- Save, Open, Save As, Autosave và Recovery đã hỗ trợ đầy đủ CAM editable state.
- Toolpath artifact là derived data dưới `toolpaths/`; thiếu hoặc hỏng chỉ làm operation chuyển `DIRTY`/`MISSING`, không làm mất aggregate.

## Tree, persistence và session

- Persistent key XCAF dùng `source_id`, occurrence path có phiên bản và product fingerprint.
- Save, Open và Save As lưu/khôi phục đúng XCAF occurrence override; Save As giữ `source_id`.
- Autosave và Recovery hỗ trợ XCAF override và CAM editable state qua SQLite v4.
- Apply/rollback appearance thực hiện nguyên tử và giữ source style khi thất bại.
- CAD view state v2 cũ và migration SQLite v2 → v3 vẫn được hỗ trợ.

## Measurement và giới hạn

- Measurement BREP hỗ trợ tọa độ vertex, khoảng cách, edge, circle/arc, diện tích, thể tích và AABB.
- Measurement là read-only, không làm dirty project hoặc kích hoạt autosave.
- Hình học chuẩn hóa chưa được lưu vào `model/`; ứng dụng vẫn import lại nguồn bất biến khi mở dự án.
- Chưa có component editing hoặc thay đổi cấu trúc assembly.
- Chưa có assembly constraints.
- Chưa có UI CAM, thuật toán gia công, simulation, collision, Post Processor hoặc G-code.
- Chưa triển khai Setup Sheet.
