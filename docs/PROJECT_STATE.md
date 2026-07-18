# Trạng thái dự án HMS CAD/CAM

## Mốc hiện tại

- Trạng thái được cập nhật sau khi hoàn thành phạm vi recovery Giai đoạn 3C.
- Giai đoạn 1: khung ứng dụng PySide6 đã hoàn thành và được ổn định.
- Giai đoạn 2: hệ thống dự án `.HMS` cơ bản đã hoàn thành và được rà soát.
- Giai đoạn 3A: session lock và cleanup an toàn đã được triển khai.
- Giai đoạn 3B: autosave snapshot nguyên tử và checksum đã được triển khai.
- Giai đoạn 3C: recovery, rollback, `.replaced` và lựa chọn UI đã được triển khai.
- Bộ kiểm thử hiện tại: **61 passed**.

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
- `RecoveryManager` phát hiện stale session, backup và phục hồi manifest/database.
- File CAD nguồn được sao chép vào `source/`; dữ liệu nguồn ban đầu không bị sửa.
- Create, import, open, save, save-as, close và recent projects đã có mã chạy được.

## Giới hạn còn lại

- Recovery chỉ được đề nghị khi stale lock và snapshot khớp project/session.
- Autosave chưa được kích hoạt tự động bằng timer hoặc sự kiện UI.
- `.replaced` mơ hồ hoặc tồn tại cạnh target hợp lệ được giữ nguyên để người dùng xử lý.
- Cleanup chỉ xóa staging/temp HMS đủ tuổi, có metadata hợp lệ và PID cục bộ đã chết.
- Recovery chỉ thay manifest/database; không chỉnh sửa hoặc thay thế `source/`.
- Chưa có CAD kernel, CAD Viewer thực, Open CASCADE hoặc chức năng CAM.
