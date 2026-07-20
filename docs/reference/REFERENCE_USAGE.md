# Quy tắc sử dụng tài liệu tham khảo

`reference_private/` là khu vực riêng để người phát triển lưu tài liệu tham
khảo cục bộ. Tài liệu trong đó không phải là source of truth của mã nguồn và
không được tự động biến thành yêu cầu sản phẩm.

## Khi làm việc với Codex

- Không nạp toàn bộ tài liệu cho Codex trong mỗi task.
- Chỉ đọc tài liệu liên quan trực tiếp tới tính năng đang triển khai.
- Không quét toàn bộ `reference_private/` nếu task không yêu cầu.
- Khi cần dùng tài liệu, phải chỉ rõ đường dẫn hoặc nhóm chủ đề cần đọc, ví dụ
  `WORKNC/CAM_3D` hoặc `MASTERCAM/MILL_2D`.
- Không tự động biến tài liệu tham khảo thành yêu cầu sửa code; yêu cầu thay
  đổi phải được xác nhận trong phạm vi task.

## Bản quyền và phạm vi sử dụng

- Không sao chép nguyên văn giao diện, thuật toán hoặc nội dung có bản quyền.
- Dùng tài liệu để hiểu workflow, thuật ngữ, hành vi và yêu cầu người dùng.
- Tóm tắt hoặc ghi chú nội bộ phải nêu nguồn và phạm vi tham khảo khi cần.
- Tài liệu lớn không được commit vào Git. `.gitignore` bỏ qua toàn bộ
  `reference_private/`.

## Quy trình phân loại

1. Đặt tài liệu mới ban đầu vào thư mục `INBOX/` tương ứng.
2. Không giải nén, đổi tên hoặc di chuyển tài liệu chỉ vì một task không yêu
   cầu việc đó.
3. Khi có task phân loại, xác định sản phẩm, chủ đề, phiên bản, ngôn ngữ và
   trạng thái đọc trong [REFERENCE_INDEX.md](REFERENCE_INDEX.md).
4. Chỉ sau khi xác định được chủ đề mới chuyển tài liệu sang thư mục chuyên
   môn như `TRAINING/`, `MILL_2D/` hoặc `CAM_REFERENCE/`.

Các thư mục riêng chỉ phục vụ tham khảo cục bộ; không tải, giải nén hoặc sửa
nội dung tài liệu tự động trong quá trình tạo cấu trúc này.
