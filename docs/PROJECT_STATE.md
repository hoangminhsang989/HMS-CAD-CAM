# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1: khung ứng dụng PySide6 đã hoàn thành và ổn định.
- Giai đoạn 2: hệ thống dự án thư mục `.HMS` cơ bản đã hoàn thành.
- Giai đoạn 3: Session Lock, Autosave và Recovery đã hoàn thành.
- Giai đoạn 4: CAD Kernel và CAD Viewer sản phẩm đã hoàn thành phạm vi được duyệt.
- Môi trường mục tiêu hiện tại: Windows 10/11 64-bit, Python 3.14.6 và PySide6.
- Commit mới nhất: `4494513`.
- Toàn bộ kiểm thử: **105 passed**.

## Kiến trúc dự án hiện có

- `ProjectService` là API dự án duy nhất được UI sử dụng.
- Dự án là thư mục `.HMS`; manifest dùng JSON UTF-8 và dữ liệu chính dùng SQLite.
- File CAD nguồn chỉ được sao chép vào `source/`, không bị chỉnh sửa.
- Tác vụ I/O và import CAD chạy ngoài UI thread.
- CAD API công khai chỉ trao đổi document ID và metadata thuần Python.
- Object OCP, TopoDS và AIS được giữ bên trong adapter OCP.
- Factory CAD Kernel và Viewer có fallback an toàn khi OCP hoặc DLL không khả dụng.

## CAD Kernel và import

- CAD Kernel `cadquery-ocp-novtk` / Open CASCADE đã được tích hợp.
- `OcpCadKernel` quản lý document và shape nội bộ theo `CadDocumentId`.
- Đã hỗ trợ import STEP/STP và BREP.
- Import lỗi không làm mất document đang hiển thị.
- Import mới thay document cũ và giải phóng document không còn sử dụng.
- Đóng hoặc đổi project sẽ clear viewer và release document hiện tại.
- Kết quả worker cũ hoặc signal đến muộn không được thay document mới.

## CAD Viewer sản phẩm

- `CadViewportWidget` thật đã hoạt động với OCCT Viewer nhúng qua HWND.
- Lifecycle graphic driver, viewer, context, view và AIS presentation được quản lý rõ ràng.
- Camera hỗ trợ rotate, pan, zoom, Fit All và bảy hướng nhìn chuẩn.
- Display mode hỗ trợ Shaded, Wireframe và Shaded with edges.
- Selection hỗ trợ Solid, Face và Edge.
- UI chỉ nhận selection ID, topology và bounding box; không nhận object OCP.
- Resize, clear, đổi document và close đã được kiểm tra an toàn.
- MainWindow, menu, toolbar, ribbon, Project Manager và Properties đã tích hợp CAD Viewer.

## Session, Autosave và Recovery

- Session Lock phân loại `active`, `stale` hoặc `unknown` và cleanup có kiểm soát.
- Autosave snapshot manifest, database, metadata và checksum; không sao chép `source/`.
- Recovery kiểm tra snapshot, dùng backup và rollback an toàn.
- CAD document hiện chưa được lưu vào dữ liệu dự án `.HMS`.

## Giới hạn còn lại

- Chưa hỗ trợ import IGES hoặc STL.
- Chưa triển khai measurement hoặc assembly tree.
- Chưa triển khai CAD healing, tessellation sản phẩm hoặc lưu CAD document vào `.HMS`.
- Chưa triển khai CAM, toolpath, mô phỏng, Post Processor hoặc Setup Sheet.
