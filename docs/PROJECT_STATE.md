# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1: khung ứng dụng PySide6 đã hoàn thành và ổn định.
- Giai đoạn 2: hệ thống dự án thư mục `.HMS` cơ bản đã hoàn thành.
- Giai đoạn 3: Session Lock, Autosave và Recovery đã hoàn thành.
- Giai đoạn 4: CAD Kernel và CAD Viewer đã hoàn thành phạm vi được duyệt.
- Giai đoạn 5A: import IGES/IGS và STL đã hoàn thành.
- Giai đoạn 5B: Measurement BREP đã hoàn thành và được review ổn định.
- Commit mới nhất: `a95e17e`.
- Toàn bộ kiểm thử: **128 passed**.
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
- Chưa có assembly tree, hide/show, color hoặc transparency.
- Chưa triển khai CAD healing và tessellation sản phẩm.
- Chưa triển khai CAM, toolpath, mô phỏng, Post Processor hoặc Setup Sheet.
