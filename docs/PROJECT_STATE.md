# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1: khung ứng dụng PySide6 đã hoàn thành và ổn định.
- Giai đoạn 2: hệ thống dự án thư mục `.HMS` cơ bản đã hoàn thành.
- Giai đoạn 3: Session Lock, Autosave và Recovery đã hoàn thành.
- Giai đoạn 4: CAD Kernel và CAD Viewer đã hoàn thành phạm vi được duyệt.
- Giai đoạn 5A: import IGES/IGS và STL đã hoàn thành.
- Giai đoạn 5B: Measurement BREP đã hoàn thành và được review ổn định.
- Giai đoạn 5C: topology tree và quản lý hiển thị session-only đã hoàn thành.
- Commit mới nhất trước Giai đoạn 5C: `c840554`.
- Toàn bộ kiểm thử: **137 passed**.
- Môi trường mục tiêu: Windows 10/11 64-bit, Python 3.14.6 và PySide6.

## Kiến trúc và dữ liệu dự án

- `ProjectService` là API dự án duy nhất được UI sử dụng.
- Dự án là thư mục `.HMS`; manifest dùng JSON UTF-8 và dữ liệu chính dùng SQLite.
- File CAD nguồn được giữ nguyên trong `source/`, không bị chỉnh sửa.
- Tác vụ I/O và import CAD chạy ngoài UI thread.
- CAD API công khai chỉ trao đổi ID và model thuần Python, bất biến.
- Object OCP, TopoDS và AIS chỉ tồn tại trong adapter nội bộ.
- Measurement là read-only, không làm dirty project hoặc kích hoạt autosave.

## CAD Kernel, import và Viewer

- Open CASCADE được tích hợp qua `cadquery-ocp-novtk`.
- Đã hỗ trợ import STEP/STP, BREP, IGES/IGS và STL.
- IGES chấp nhận wire, surface, shell, solid hoặc compound hợp lệ.
- STL được giữ dạng triangle mesh, không chuyển thành BREP face giả.
- Metadata phân biệt `BREP` và `TRIANGLE_MESH`.
- CAD Viewer OCCT nhúng qua HWND hỗ trợ camera, Fit All và các hướng nhìn chuẩn.
- Display mode gồm Shaded, Wireframe và Shaded with edges.
- Selection BREP hỗ trợ Solid, Face, Edge và Vertex.
- STL dùng `AIS_Triangulation`; selection topology BREP bị vô hiệu hóa.
- Import lỗi, worker cũ hoặc signal đến muộn không thay document hiện tại.

## Topology tree và quản lý hiển thị

- BREP có cây quản lý giới hạn ở Document, Compound, CompSolid, Solid, Shell và shape đơn lẻ.
- Face, Edge và Vertex không được tạo hàng loạt trong tree; chúng chỉ tồn tại trong selection metadata.
- STL có đúng một mesh node và một presentation, không có triangle/vertex tree.
- Tree và viewport đồng bộ selection hai chiều bằng document ID và object ID có stale guard.
- Hỗ trợ session-only Hide/Show, một isolate state, màu và transparency; không làm dirty project hoặc kích hoạt autosave.
- Đây là topology tree, không phải assembly XCAF: chưa có component name, instance, product hierarchy hoặc assembly transform.

## Measurement BREP

- Vertex: tọa độ X/Y/Z.
- Hai vertex: khoảng cách điểm–điểm bằng Ctrl-pair.
- Edge: chiều dài; circle/arc có bán kính, đường kính và phân loại.
- Face: diện tích; Solid: thể tích.
- Document và selection: axis-aligned bounding box dimensions (AABB) X/Y/Z.
- Kết quả không chứa TopoDS, OCP hoặc AIS object.
- Measurement STL chưa được hỗ trợ.

## Session và giới hạn còn lại

- Session Lock, Autosave và Recovery có kiểm tra, backup và rollback an toàn.
- CAD document hiện chưa được lưu vào dữ liệu dự án `.HMS`.
- Chưa có assembly tree XCAF thực sự hoặc lưu appearance vào dự án `.HMS`.
- Chưa triển khai CAD healing và tessellation sản phẩm.
- Chưa triển khai CAM, toolpath, mô phỏng, Post Processor hoặc Setup Sheet.
