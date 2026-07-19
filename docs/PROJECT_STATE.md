# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1–2: khung PySide6 và hệ thống dự án thư mục `.HMS` đã hoàn thành.
- Giai đoạn 3–4: Session Lock, Autosave, Recovery, CAD Kernel và CAD Viewer đã hoàn thành.
- Giai đoạn 5A–5D: import CAD, Measurement BREP, topology tree và CAD view state đã hoàn thành.
- Giai đoạn 6A.1–6A.4: XCAF technical spike, domain model, viewer/tree và persistence đã hoàn thành.
- Commit mới nhất: `fd1294b`.
- Toàn bộ pytest: **200 passed**.

## Kiến trúc và dữ liệu dự án

- `ProjectService` là API dự án duy nhất được UI sử dụng.
- Dự án là thư mục `.HMS`; manifest dùng JSON UTF-8 và dữ liệu chính dùng SQLite.
- SQLite schema hiện tại: **v3**.
- File CAD nguồn được giữ nguyên trong `source/`, không bị chỉnh sửa.
- Tác vụ I/O và import CAD chạy ngoài UI thread.
- CAD API công khai chỉ trao đổi ID và model thuần Python, bất biến.
- Object OCP, `TDF_Label`, `TopoDS` và AIS chỉ tồn tại trong adapter nội bộ.
- Persistent state không lưu runtime `CadDocumentId`, `CadObjectId` hoặc object native.

## CAD Kernel, import và Viewer

- Open CASCADE được tích hợp qua `cadquery-ocp-novtk`.
- Đã hỗ trợ import STEP/STP, BREP, IGES/IGS và STL.
- STEP part đơn và STEP assembly XCAF đều được hỗ trợ.
- XCAF assembly hỗ trợ product/occurrence hierarchy lồng nhau và repeated occurrence.
- Mỗi occurrence có local transform và absolute transform theo `parent × local`.
- Source appearance của product, occurrence và subshape được giữ tách biệt với user override.
- Viewer/tree/selection đồng bộ theo occurrence; mỗi repeated occurrence có presentation và state riêng.
- User override về visible, color và transparency thắng source appearance; reset quay về source.
- CAD Viewer hỗ trợ camera, Fit All, hướng nhìn chuẩn và ba display mode.
- Import lỗi, worker cũ hoặc signal đến muộn không thay document hiện tại.

## Tree, persistence và session

- BREP cũ vẫn dùng topology tree giới hạn; STL có một mesh node và một presentation.
- Persistent key XCAF dùng `source_id`, occurrence path có phiên bản và product fingerprint.
- Ambiguous, stale hoặc foreign-source key bị bỏ qua an toàn.
- Save, Open và Save As lưu/khôi phục đúng XCAF occurrence override; Save As giữ `source_id`.
- Autosave và Recovery hỗ trợ XCAF override qua SQLite v3.
- Source appearance không ghi vào database; isolate chỉ tồn tại trong session.
- Apply/rollback appearance thực hiện nguyên tử và giữ source style khi thất bại.
- CAD view state v2 cũ và migration SQLite v2 → v3 vẫn được hỗ trợ.

## Measurement và giới hạn

- Measurement BREP hỗ trợ tọa độ vertex, khoảng cách, edge, circle/arc, diện tích, thể tích và AABB.
- Measurement là read-only, không làm dirty project hoặc kích hoạt autosave.
- Hình học chuẩn hóa chưa được lưu vào `model/`; ứng dụng vẫn import lại nguồn bất biến khi mở dự án.
- Chưa có component editing hoặc thay đổi cấu trúc assembly.
- Chưa có assembly constraints.
- Chưa triển khai CAM, toolpath, mô phỏng, Post Processor hoặc Setup Sheet.
