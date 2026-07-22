# Nhiệm vụ hiện tại — Stage 9A.6

## Trạng thái ưu tiên

- Functional UI baseline: `24d2a42` — hoàn thành đánh giá đồng bộ giao diện
  phay 2D Stage 9A.5.4.
- **Stage 9A.I1 — HMS Isometric CAD/CAM Icon Pack: DEFERRED.**
- **Stage 9A.6 — Drilling Family Production Function Editors — COMPLETED.**

## Lý do tạm gác Stage 9A.I1

- Bộ icon chưa đạt chuẩn hình học và độ nét cần thiết.
- Icon chưa phải ưu tiên trong giai đoạn hoàn thiện giao diện chức năng chính.
- Production UI tiếp tục dùng icon hoặc placeholder hiện có.
- Stage 9A.I1 sẽ được đánh giá lại sau khi giao diện chức năng chính hoàn thiện.

## Phạm vi Stage 9A.6 đã hoàn tất

- Drilling.
- Tapping.
- Reaming.
- Boring.
- Tích hợp trong Unified Function Editor.
- Tối giản chế độ Basic.
- Thu gọn Advanced/Expert theo progressive disclosure.
- Giữ tương đương domain, toolpath và post hiện có.

## Kết quả hoàn thành

- Shared Drilling Family editor foundation dùng chung cho Drilling, Tapping,
  Reaming và Boring đã hoàn tất.
- Unified Function Editor đã có progressive disclosure và lifecycle
  Preview/Apply/Calculate đúng applied-state contract.
- Operation Manager đã tích hợp editor và Duplicate lifecycle với identity,
  geometry input và artifact state mới.
- Save/Open round-trip và exact-equivalence đã được kiểm tra trên cả bốn editor.
- Native Windows GUI review đã được người dùng duyệt; font harness đã được sửa để
  chặn ảnh thiếu glyph, panel 460 px và footer polish đã đạt.
- SQLite giữ nguyên schema v4; dependency và icon không đổi.
- Stage 9A.I1 icon pack tiếp tục deferred.
- Full regression đạt 1284 passed, 2 deselected.

## Ràng buộc chuyển giai đoạn

- Không thay đổi domain, codec, Toolpath IR, Simulation, Post hoặc SQLite schema.
- Stage 9A.5.4 và các production editor Facing, Planar Facing, Contour, Pocket
  tiếp tục là baseline giao diện cần bảo toàn.
- Stage 9A.I1 icon pack tiếp tục deferred; không sửa icon trong Stage 9A.6.
- Không tự động bắt đầu stage tiếp theo sau khi hoàn tất Stage 9A.6.
