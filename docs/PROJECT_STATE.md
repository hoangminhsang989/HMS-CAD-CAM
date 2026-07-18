# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Giai đoạn 1: khung ứng dụng PySide6 đã hoàn thành và ổn định.
- Giai đoạn 2: hệ thống dự án thư mục `.HMS` cơ bản đã hoàn thành.
- Giai đoạn 3A: Session Lock và cleanup an toàn đã hoàn thành.
- Giai đoạn 3B: Autosave snapshot đã hoàn thành.
- Giai đoạn 3C: Recovery, rollback và xử lý `.replaced` đã hoàn thành.
- Giai đoạn 3D: Autosave định kỳ qua UI/worker đã hoàn thành.
- Commit mã mới nhất: `15813c5`.
- Toàn bộ kiểm thử: **69 passed**.

## Kiến trúc dự án hiện có

- `ProjectService` là API dự án duy nhất được UI sử dụng.
- Creator, loader, saver, validator, manifest store và database adapter tách biệt.
- Dự án là thư mục `.HMS`; manifest JSON UTF-8 và dữ liệu chính dùng SQLite.
- File CAD nguồn chỉ được sao chép vào `source/`, không bị chỉnh sửa hoặc thay thế.
- `ProjectUiController` điều phối thao tác; `ProjectTask` chạy I/O ngoài UI thread.

## Session Lock

- Mỗi project đang mở ghi `session.lock` có phiên bản, project/session ID, PID,
  hostname, thời điểm tạo và phiên bản ứng dụng.
- Lock được phân loại `active`, `stale` hoặc `unknown`.
- PID chỉ được kiểm tra khi hostname trùng máy hiện tại; lock `unknown` không tự xóa.
- Chuyển project giữ lock cũ đến khi project mới mở thành công; close nhả lock chủ động.
- Cleanup chỉ xóa staging/temp HMS hợp lệ, đủ tuổi và không thuộc phiên còn sống.

## Autosave

- Snapshot chỉ gồm manifest, `project.db`, metadata và checksum; không sao chép `source/`.
- Snapshot được publish nguyên tử; snapshot lỗi không thay thế bản hợp lệ gần nhất.
- Autosave không chạy đồng thời và không làm project chính chuyển sang trạng thái clean.
- UI dùng `QTimer` mặc định 5 phút, chỉ chạy khi project đang mở và dirty.
- Worker autosave chạy nền, hỗ trợ pending và cô lập kết quả theo project/session generation.

## Recovery

- Phát hiện đóng bất thường dựa trên session lock và chọn snapshot hợp lệ có kiểm tra.
- Phục hồi manifest/database dùng backup và rollback an toàn, không thay đổi `source/`.
- `.replaced` chỉ được phục hồi khi nhận diện chắc chắn; trường hợp mơ hồ cần người dùng chọn.
- UI yêu cầu lựa chọn rõ ràng trước recovery và không tự xử lý dữ liệu không chắc chắn.

## Giới hạn còn lại

- Chưa triển khai CAD kernel hoặc tích hợp Open CASCADE.
- Chưa có CAD Viewer thật, tessellation, topology hay thao tác chọn hình học.
- Chưa triển khai thuật toán CAM, toolpath, mô phỏng hoặc Post Processor.
