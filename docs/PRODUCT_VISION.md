# Định hướng sản phẩm HMS CAD/CAM

HMS CAD/CAM được xây dựng như một nền tảng thống nhất cho quy trình CAD/CAM.
HMS không phải là bản sao trực tiếp của WorkNC, Mastercam hoặc Siemens NX;
các sản phẩm này chỉ là nguồn tham khảo để hiểu workflow, thuật ngữ và kỳ
vọng sử dụng trong ngành.

## Định hướng tham khảo theo khu vực

- Giao diện chính ở giai đoạn hiện tại tham khảo cách tổ chức thao tác của
  Mastercam.
- CAM 2D và Turning tham khảo workflow Mastercam.
- CAM 3D tham khảo chiến lược và workflow WorkNC.
- CAD/3D nâng cao, surface, solid và assembly tham khảo NX.
- Toolpath IR, Setup, Tool Library, Simulation và Post Processor phải được
  thiết kế trung lập, không gắn với một sản phẩm thương mại cụ thể.

Các tham khảo trên chỉ định hướng việc nghiên cứu và mô tả yêu cầu. Chúng
không tự động trở thành yêu cầu sửa code, cũng không thay thế quyết định kiến
trúc của HMS.

## Nguyên tắc độc lập

HMS không phụ thuộc vào định dạng nội bộ độc quyền của WorkNC, Mastercam hoặc
NX. Dữ liệu miền, persistence và các giao diện trao đổi của HMS phải có mô
hình riêng, có version rõ ràng và có thể kiểm thử độc lập.

Về sau HMS sẽ tiếp tục tinh chỉnh thành giao diện và workflow riêng, phù hợp
với mô hình dự án `.HMS`, khả năng mở rộng của hệ thống và nhu cầu người dùng.
