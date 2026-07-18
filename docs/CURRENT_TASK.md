# Nhiệm vụ hiện tại — Giai đoạn 3

Trạng thái: **3A hoàn thành; 3B và 3C chưa bắt đầu**.

## Phạm vi

Xây dựng cơ chế bảo vệ phiên làm việc và phục hồi dự án `.HMS` mà không thay đổi
file CAD nguồn.

### Session lock

- Tạo `session.lock` khi một dự án được kích hoạt thành công.
- Gỡ lock khi đóng dự án hoặc ứng dụng theo luồng bình thường.
- Phát hiện lock còn hoạt động, lock cũ do đóng bất thường và lock không xác định.
- Không cho hai phiên cùng ghi một dự án mà không có cảnh báo và xử lý rõ ràng.
- Chuyển project phải giữ nguyên session hiện tại nếu mở hoặc khóa project mới thất bại.

### Autosave

- Chỉ autosave khi session có thay đổi chưa lưu.
- Hỗ trợ kích hoạt có kiểm soát theo thời gian và sự kiện.
- Ghi snapshot nguyên tử vào `autosave/`; lỗi autosave không được làm hỏng dữ liệu chính.
- Snapshot phải có metadata phiên bản và đủ dữ liệu để kiểm tra trước khi phục hồi.

### Recovery

- Nhận diện đóng bất thường bằng lock cũ và trạng thái autosave hợp lệ.
- Kiểm tra snapshot trước khi đề nghị hoặc thực hiện phục hồi.
- Phục hồi theo giao dịch và giữ bản backup của dữ liệu chính bị thay thế.
- Snapshot lỗi hoặc không đầy đủ phải bị từ chối bằng lỗi có kiểm soát.

### `.replaced` và staging

- Phát hiện thư mục `.replaced` còn sót từ giao dịch overwrite bị gián đoạn.
- Chỉ tự phục hồi khi ứng viên duy nhất, nhận diện được và hợp lệ.
- Không tự xóa hoặc chọn thay người dùng khi có nhiều ứng viên mơ hồ.
- Chỉ dọn staging/temp đúng mẫu, thuộc HMS và không còn được phiên hoạt động sử dụng.
- Không xóa `source/`, dữ liệu chính hoặc thư mục không nhận diện được.

## Tiêu chí kiểm thử

- Lock được tạo/gỡ đúng; lock đang hoạt động chặn phiên ghi thứ hai.
- Lock cũ kích hoạt phát hiện đóng bất thường và luồng recovery.
- Autosave bỏ qua session sạch, tạo snapshot cho session dirty và ghi nguyên tử.
- Recovery thành công không làm hỏng project chính; snapshot lỗi bị từ chối.
- `.replaced` hợp lệ được phục hồi, trường hợp mơ hồ được giữ nguyên và báo lỗi.
- Staging/temp cũ được dọn theo allowlist; dữ liệu ngoài phạm vi không bị xóa.
- Mọi lỗi I/O được rollback hoặc báo bằng ngoại lệ dự án có kiểm soát.
- Toàn bộ 35 test hiện tại vẫn vượt qua cùng các test mới của Giai đoạn 3.

## Ngoài phạm vi

- Không triển khai CAD kernel, Open CASCADE, CAD Viewer thực hoặc CAM.
