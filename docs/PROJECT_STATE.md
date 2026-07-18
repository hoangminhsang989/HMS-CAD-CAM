# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Trạng thái được cập nhật sau khi hoàn thành Giai đoạn 3B.
- Giai đoạn 1: khung ứng dụng PySide6 đã hoàn thành và được ổn định.
- Giai đoạn 2: hệ thống dự án `.HMS` cơ bản đã hoàn thành và được rà soát.
- Giai đoạn 3A: session lock và cleanup an toàn đã được triển khai.
- Giai đoạn 3B: autosave snapshot nguyên tử và checksum đã được triển khai.
- Bộ kiểm thử hiện tại: **50 passed**.

## Kiến trúc hiện có

- `ProjectService` là API điều phối dự án duy nhất được UI sử dụng.
- `ProjectCreator`, `ProjectLoader` và `ProjectSaver` tách các luồng tạo, mở và lưu.
- `ProjectManifestStore` đọc/ghi manifest JSON UTF-8 theo cách thay thế nguyên tử.
- `ProjectDatabase` khởi tạo, migrate, kiểm tra và backup SQLite.
- `ProjectValidator` kiểm tra tên Windows, định dạng, phiên bản và tham chiếu nguồn.
- `filesystem` quản lý staging, publish, rollback và sao chép source có kiểm tra SHA-256.
- `ProjectSession` giữ project hiện hành và trạng thái thay đổi chưa lưu.
- `ProjectUiController` chỉ gọi service, quản lý hộp thoại và trạng thái thao tác.
- `ProjectTask` chạy một thao tác filesystem ngoài UI thread và trả kết quả bằng signal.
- `AutosaveManager` tạo snapshot bất biến và chỉ cập nhật con trỏ latest sau kiểm tra.
- File CAD nguồn được sao chép vào `source/`; dữ liệu nguồn ban đầu không bị sửa.
- Create, import, open, save, save-as, close và recent projects đã có mã chạy được.

## Giới hạn còn lại

- `session.lock` đã phân loại active/stale/unknown; recovery từ lần đóng bất thường chưa có.
- Chưa có recovery hoặc tích hợp timer/autosave vào UI.
- Chưa xử lý `.replaced` còn sót sau khi tiến trình bị dừng đột ngột.
- Cleanup chỉ xóa staging/temp HMS đủ tuổi, có metadata hợp lệ và PID cục bộ đã chết.
- Snapshot autosave chưa được dùng để phục hồi dữ liệu.
- Dirty state đã có API autosave nhưng chưa được kích hoạt theo timer hoặc sự kiện UI.
- Chưa có CAD kernel, CAD Viewer thực, Open CASCADE hoặc chức năng CAM.
