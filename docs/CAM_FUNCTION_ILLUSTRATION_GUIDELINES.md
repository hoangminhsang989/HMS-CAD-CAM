# Hướng dẫn minh họa chức năng CAM

## Mục tiêu và bản quyền

Minh họa giúp người dùng hiểu Tool, vùng gia công, hướng chạy dao và chuyển động
chính ngay trong popup. Đây không phải icon và không mở lại Stage 9A.I1. Tất cả
hình được HMS tự vẽ bằng `QPainter`/vector; không sao chép WorkNC, Mastercam hay
asset thương mại.

## Quy tắc trình bày

- Dùng vector/logical coordinates, antialiasing và palette HMS; không dùng
  bitmap chứa đoạn văn dài.
- Rõ ở vùng nhỏ, không buộc horizontal scrollbar và không che footer ở
  1366×768.
- Caption, tooltip, accessible name và accessible description phải bằng tiếng
  Việt và tự đủ nghĩa, không dựa riêng vào màu.
- Mặc định là trạng thái thu gọn cao 110–125 logical px ở 1366×768, tăng tối đa
  140–150 px trên màn hình lớn; canvas và caption không đẩy footer khỏi popup.
  Caption compact elide một dòng và giữ tooltip đầy đủ.
- `Mở rộng`/`Thu gọn minh họa` đổi presentation ngay trong popup; khi popup thấp hơn 620
  logical px, host tự thu gọn để footer không biến mất. Expanded vẫn bị giới hạn
  theo work area và không kéo popup vượt maximum height.
- Nút `Phóng to` mở child popup resize được trong ứng dụng, không mở viewer ngoài
  và không tự full-screen. Child dùng title `Minh họa · <Tên chức năng>` và nút
  `Đóng minh họa`; Esc đóng child rồi trả focus về `Phóng to`.
- Collapsed/expanded là presentation-only UI state; scene, pixmap và renderer không
  được đưa vào project payload.

## Registry production

Mỗi descriptor khai báo operation type, preferred aspect ratio, compact/expanded
logical size, caption, accessible description, vector render source và semantic
features. `IllustrationViewport` lấy tỷ lệ này làm nguồn duy nhất; popup không
hard-code tỷ lệ riêng.

`CAMIllustrationRegistry` phải resolve đủ chín editor:

1. Phay mặt 2.5D — quét phẳng toàn bộ bề mặt.
2. Phay các mặt phẳng — quét các mặt phẳng đã chọn.
3. Phay biên dạng 2D — Tool bám theo biên dạng.
4. Phay hốc 2.5D — Tool bóc vật liệu bên trong hốc.
5. Khoan — tiến theo trục lỗ.
6. Taro — quay đồng bộ với bước tiến ren.
7. Doa lỗ — tinh chỉnh lỗ đã có.
8. Khoét lỗ — dao lệch tâm quay quanh tâm lỗ và tiến xuống theo trục Z; tuyệt đối
   không dùng mũi tên ngang hai chiều.
9. Parallel Finishing — các lượt song song trên bề mặt 3D.

Thiếu descriptor là lỗi fail-closed trong test registration, không dùng hình
placeholder mơ hồ.

## Minh họa động Parallel

`CAMIllustrationState` chỉ nhận presentation primitives và cập nhật bằng timer
debounce 90 ms. Nó không import OCP, không tessellate và không chạy full
calculation. Scene phải phân biệt:

- Một chiều và Zíc zắc.
- Góc/hướng chạy dao và hướng bước ngang.
- Liên kết trực tiếp và rút dao–chạy nhanh–tiếp cận.
- Hồ sơ Nhanh, Cân bằng, Chất lượng cao.
- Tự động và Tùy chỉnh.

Ordering và linking có renderer riêng, không dùng một render path rồi chỉ đổi
caption:

- Một chiều: mọi mũi tên cắt cùng hướng; reposition chỉ là nét nhẹ.
- Zíc zắc: mũi tên đổi chiều và nối liên tục ở cuối lượt.
- Liên kết trực tiếp: đúng một đoạn nối ngắn kiểu riêng, có nhãn và Tool gần hai
  pass; không có Z lift.
- Rút dao bảo thủ: mũi tên rút lên, đoạn chạy nhanh ngang nét đứt và mũi tên tiếp
  cận xuống.

Caption tương ứng ghi rõ thứ tự cắt hoặc policy liên kết. State có semantic
metadata và fingerprint riêng cho `same_direction_cut_arrows`,
`alternating_cut_arrows`, `direct_link_segment` và bộ ba
`retract_vertical_up`/`rapid_horizontal_dashed`/`approach_vertical_down`.
Mật độ pass thay đổi theo quality. Việc cập nhật không được làm mất focus của
field đang nhập.

Expanded và child hiển thị legend chung: xanh liền là đường cắt, cam đứt là
chạy nhanh/liên kết ngoài cắt, mũi tên xanh là hướng cắt, mũi tên cam là rút/
tiếp cận. Compact giữ cùng thông tin trong tooltip/accessibility. Ý nghĩa không
phụ thuộc riêng vào màu vì cut/rapid dùng line style khác nhau. Boring có metadata
bắt buộc cho mũi tên Z đi xuống, quay quanh trục lỗ, cutaway giữa mặt trước, giữ
nguyên khối ngoài và không có mũi tên ngang hai chiều.

## Giữ tỷ lệ và fit-inside

Viewport tính target rect bằng phép fit-inside có padding, căn giữa và dùng một
hệ số scale đồng nhất cho X/Y. Phần dư giữ nền trung tính; không crop geometry,
không render trực tiếp scene vào toàn widget rect và không dùng
`IgnoreAspectRatio`. Compact, expanded và child zoom dùng cùng preferred aspect
ratio. QPainter làm việc bằng logical coordinates, vì vậy DPR 1.0/1.25/1.5/2.0
không cần nhân kích thước lần hai và vẫn giữ đúng tỷ lệ.

## High DPI và kiểm thử

Canvas phải render được ở logical size nhỏ, wide, tall và child zoom, giữ accessible description,
không chứa asset bị thiếu và không phụ thuộc device pixel ratio cố định. Test
phải kiểm tra đủ registry, render của từng loại, semantics Boring, state động
Parallel, debounce, collapse/expand, caption đầy đủ và việc không gọi
OCP/calculation. Qt tự chuyển logical pixel/point font ở DPI 100/125/150; không
render bitmap lớn rồi thu nhỏ và không nhân device scale lần hai.
