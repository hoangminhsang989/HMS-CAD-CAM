# Nhiệm vụ hiện tại — Giai đoạn 5B

## Phạm vi

- Measurement chỉ cho BREP: tọa độ vertex, khoảng cách hai vertex, chiều dài
  cạnh, bán kính/đường kính cạnh tròn, diện tích face, thể tích solid và bounding
  dimensions theo các trục X/Y/Z.
- Kết quả đo là dữ liệu thuần Python, read-only và không làm thay đổi dự án.

## Kiểm thử bắt buộc

- Kiểm tra số đo box, cylinder, circular arc và tolerance tập trung.
- Kiểm tra Ctrl-pair vertex, reset lifecycle, lỗi an toàn và public API không có OCP.
- Chạy test measurement, CAD/viewer/integration, toàn bộ pytest và kiểm tra package.

## Ngoài phạm vi

- Chưa hỗ trợ measurement STL.
- Chưa triển khai assembly tree hoặc CAM.
