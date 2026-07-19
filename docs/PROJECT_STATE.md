# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1–2: khung PySide6 và hệ thống dự án thư mục `.HMS` đã hoàn thành.
- Giai đoạn 3–4: Session Lock, Autosave, Recovery, CAD Kernel và CAD Viewer đã hoàn thành.
- Giai đoạn 5A–5B: import IGES/IGS, STL và Measurement BREP đã hoàn thành.
- Giai đoạn 5C: topology tree và quản lý hiển thị session-only đã hoàn thành.
- Giai đoạn 5D: trạng thái hiển thị CAD đã được lưu trong dự án `.HMS`.
- Commit mới nhất: `57c8e3e`.
- Toàn bộ pytest: **170 passed**.

## Kiến trúc và dữ liệu dự án

- `ProjectService` là API dự án duy nhất được UI sử dụng.
- Dự án là thư mục `.HMS`; manifest dùng JSON UTF-8 và dữ liệu chính dùng SQLite.
- SQLite schema hiện tại: **v2**.
- File CAD nguồn được giữ nguyên trong `source/`, không bị chỉnh sửa.
- Tác vụ I/O và import CAD chạy ngoài UI thread.
- CAD API công khai chỉ trao đổi ID và model thuần Python, bất biến.
- Object OCP, TopoDS và AIS chỉ tồn tại trong adapter nội bộ.
- Measurement là read-only, không làm dirty project hoặc kích hoạt autosave.

## CAD Kernel, import và Viewer

- Open CASCADE được tích hợp qua `cadquery-ocp-novtk`.
- Đã hỗ trợ import STEP/STP, BREP, IGES/IGS và STL.
- STL được giữ dạng `TRIANGLE_MESH`, không chuyển thành BREP face giả; BREP có metadata riêng.
- CAD Viewer OCCT nhúng qua HWND hỗ trợ camera, Fit All, hướng nhìn chuẩn và ba display mode.
- Selection BREP hỗ trợ Solid, Face, Edge và Vertex.
- STL dùng `AIS_Triangulation`; selection topology BREP bị vô hiệu hóa.
- Import lỗi, worker cũ hoặc signal đến muộn không thay document hiện tại.

## Topology tree và quản lý hiển thị

- BREP có cây quản lý giới hạn ở Document, Compound, CompSolid, Solid, Shell và shape đơn lẻ.
- Face, Edge và Vertex không được tạo hàng loạt trong tree; chúng chỉ tồn tại trong selection metadata.
- STL có đúng một mesh node và một presentation, không có triangle/vertex tree.
- Topology Tree và viewport đã đồng bộ selection hai chiều bằng document ID và object ID có stale guard.
- CAD view state lưu được visible, color, transparency, display mode và view direction.
- State được lưu và khôi phục qua Save, Save As, Autosave và Recovery.
- Persistent key dùng topology path có phiên bản; không dùng runtime ID hoặc object OCP/TopoDS/AIS.
- Isolate vẫn chỉ tồn tại trong session; Save trong isolate dùng visibility trước isolate.
- Đây là topology tree, không phải assembly XCAF: chưa có component name, instance, product hierarchy hoặc assembly transform.

## Measurement BREP

- Vertex: tọa độ X/Y/Z.
- Hai vertex: khoảng cách điểm–điểm bằng Ctrl-pair.
- Edge: chiều dài; circle/arc có bán kính, đường kính và phân loại.
- Face: diện tích; Solid: thể tích.
- Document và selection: axis-aligned bounding box dimensions (AABB) X/Y/Z.
- Kết quả không chứa TopoDS, OCP hoặc AIS object; Measurement STL chưa được hỗ trợ.

## Session và giới hạn còn lại

- Session Lock, Autosave và Recovery có kiểm tra, backup và rollback an toàn.
- Hình học CAD chuẩn hóa chưa được lưu vào `model/`; ứng dụng vẫn import lại bản nguồn bất biến khi mở dự án.
- Chưa có assembly tree XCAF thực sự hoặc appearance theo Face/Edge/Vertex.
- Chưa triển khai CAM, toolpath, mô phỏng, Post Processor hoặc Setup Sheet.
