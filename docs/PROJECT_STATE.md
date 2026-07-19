# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1–2: khung PySide6 và hệ thống dự án thư mục `.HMS` đã hoàn thành.
- Giai đoạn 3–4: Session Lock, Autosave, Recovery, CAD Kernel và CAD Viewer đã hoàn thành.
- Giai đoạn 5A–5D: import CAD, Measurement BREP, topology tree và CAD view state đã hoàn thành.
- Giai đoạn 6A.1–6A.4: XCAF technical spike, domain model, viewer/tree và persistence đã hoàn thành.
- Giai đoạn 7A.1–7B.5: CAM Foundation, UI, Facing, Contour 2D và Pocket v1 đã hoàn thành.
- Commit mới nhất: `d8f44d7`.
- Toàn bộ pytest: **534 passed**.

## Kiến trúc và dữ liệu dự án

- `ProjectService` là API dự án duy nhất được UI sử dụng.
- Dự án là thư mục `.HMS`; manifest dùng JSON UTF-8 và dữ liệu chính dùng SQLite.
- SQLite schema hiện tại: **v4**; Pocket v1 không yêu cầu migration.
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

- CAM có ID mạnh, unit tường minh, `GeometryReference` bền vững, Job, Setup, WCS, Stock, tooling và machine model.
- Operation Tree, Dependency DAG và Toolpath IR hỗ trợ dirty propagation, stale-token policy và fingerprint xác định.
- CAM editable state là dữ liệu chính trong SQLite v4; artifact là derived data dưới `toolpaths/`.
- CAM hiện có Facing 2.5D, Planar Face Facing, 2D Contour và Pocket v1.
- Facing hỗ trợ biên Stock BOX và persistent planar FACE; selected face là target plane, top lấy từ Stock BOX.
- OCP planar-face resolver hỗ trợ repeated/nested XCAF occurrence và fail-closed khi reference không còn hợp lệ.
- 2D Contour hỗ trợ persistent outer loop LINE/ARC, offset, depth passes, lead và fail-closed validation.
- Pocket Geometry, Strategy, Viewer, UI và Persistence v1 đã hoàn thành cho một outer loop, không island.
- Bind/Rebind/Clear, recompute giữ artifact VALID cũ khi lỗi và stale result không được publish/display.
- Save, Open, Save As, Autosave và Recovery giữ nguyên persistent `GeometryReference`.

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
- Chưa có component editing, thay đổi cấu trúc assembly hoặc assembly constraints.
- Chưa có Drilling, Simulation, Collision, Post Processor, G-code hoặc Setup Sheet; CAM chưa hỗ trợ island, rest machining hay adaptive clearing.
