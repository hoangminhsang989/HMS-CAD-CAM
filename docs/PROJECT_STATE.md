# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Trạng thái được ghi nhận sau commit `3c4bda3`.
- Giai đoạn 1: khung ứng dụng PySide6 đã hoàn thành và được ổn định.
- Giai đoạn 2: hệ thống dự án `.HMS` cơ bản đã hoàn thành và được rà soát.
- Bộ kiểm thử hiện tại: **35 passed**.

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
- File CAD nguồn được sao chép vào `source/`; dữ liệu nguồn ban đầu không bị sửa.
- Create, import, open, save, save-as, close và recent projects đã có mã chạy được.

## Giới hạn còn lại

- Chưa có `session.lock` và chưa ngăn hai phiên cùng ghi một dự án.
- Chưa phát hiện lần đóng bất thường.
- Chưa có autosave, snapshot hoặc luồng recovery.
- Chưa xử lý `.replaced` còn sót sau khi tiến trình bị dừng đột ngột.
- Cleanup hiện chỉ bao phủ staging của giao dịch đang chạy, chưa xử lý phần dư cũ.
- Các thư mục mục tiêu ngoài `source/` chưa được tạo đầy đủ theo vòng đời dự án.
- Dirty state đã có nhưng chưa có cơ chế autosave theo thời gian hoặc sự kiện.
- Chưa có CAD kernel, CAD Viewer thực, Open CASCADE hoặc chức năng CAM.
