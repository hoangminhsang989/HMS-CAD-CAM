# Nhiệm vụ tiếp theo — Giai đoạn 7D.3

## Multi-operation Program Assembly

7D.3 dự kiến mở rộng pipeline Post hiện có từ một operation sang một chương trình `.fn` gồm nhiều operation, nhưng chưa triển khai trong checkpoint trạng thái này.

## Phạm vi dự kiến

- Ghép nhiều operation thành một chương trình `.fn`.
- Hỗ trợ nhiều tool section và quy tắc tool ordering rõ ràng.
- Dùng chung header/footer ở cấp chương trình.
- Phát tool-change sequence an toàn giữa các section.
- Giữ provenance riêng cho từng operation và Toolpath artifact nguồn.
- Áp dụng simulation/post gate cho toàn chương trình.
- Giữ output và checksum deterministic.
- Tái sử dụng pipeline project-managed artifact/export hiện có.

## Chưa thuộc 7D.3

- Production Tapping.
- Automatic tool optimization.
- Direct CNC communication hoặc FTP/SFTP/HTTP/DNC.
- 4/5-axis.
- Stock removal.
- Machine certification.

Tài liệu này chỉ xác định phạm vi của nhiệm vụ tiếp theo; không xác nhận 7D.3 đã bắt đầu hoặc hoàn thành.
