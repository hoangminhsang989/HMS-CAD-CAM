# HMS Isometric CAD/CAM Icon Pack — Stage 9A.I1

## Mục tiêu

Stage 9A.I1 tạo một bộ nền icon vector kỹ thuật HMS để review trực quan trước
khi có quyết định thay icon trong Ribbon. Bộ này không thay resource production,
không sửa Workspace Bar, Operation Manager, Function Editor, menu, CAD/CAM
domain, codec, schema hay trạng thái dirty của project.

## Palette HMS

Các mặt hình học dùng xanh kỹ thuật tiết chế: `#82C0D0`, `#6FB2C4`, `#5199B0`,
`#438CA4`, `#356F85`, `#2E6277`. Cạnh và nét construction dùng `#1E4A5D` và
`#4F7588`; highlight cạnh trên dùng `#D9F1F5`. Kim loại dùng
`#F2F5F7`, `#D3DADE`, `#AEB8BF`, `#8D9AA3`, `#596771`. Màu vàng cam
`#E89924` (viền `#9B5B16`) chỉ dành cho vùng tác động và mũi tên.

Không có nền trong icon, không dùng glow, bóng sân khấu, vật liệu bóng kính hay
độ bão hòa kiểu game. Nền checkerboard chỉ xuất hiện ở contact sheet riêng tư
để kiểm tra alpha.

## Quy tắc phối cảnh và hình học

- Cả sáu icon dùng cùng góc nhìn isometric trực giao, cùng hướng sáng từ trên.
- Vật thể chiếm vùng trung tâm nhưng giữ khoảng trống quanh mép viewBox 64×64.
- Mặt trên sáng hơn, mặt trước trung gian, mặt bên phải tối hơn; cạnh trên có
  một highlight mảnh và nhất quán.
- Nét cạnh khoảng 1.7 đơn vị ở SVG gốc, không viền đen dày và không bo lớn.
- Mũi tên là polygon/path phẳng màu cam, không dùng ký tự Unicode và không che
  feature chính.
- Chi tiết được giới hạn để còn nhận dạng ở 24 px; Threaded Shaft dùng năm gờ
  ren chính thay vì vẽ nhiễu nhiều vòng.

## Quy ước group SVG

Mỗi SVG độc lập, có `width="64"`, `height="64"`, `viewBox="0 0 64 64"` và
không cần runtime generator để hiển thị. Các ID được dùng theo vai trò:

| Group | Vai trò |
|---|---|
| `main-solid` | Các mặt chính của vật thể |
| `top-face`, `front-face`, `side-face` | Phân loại mặt khi icon dùng khối rõ ràng |
| `feature-highlight` | Rãnh, vùng lồi/lõm hoặc cạnh cần nhấn |
| `action-arrow` | Hướng thao tác màu cam |
| `tool` | Dao hoặc dụng cụ kim loại |
| `mirror-plane` | Mặt phẳng đối xứng bán trong suốt và nét đứt |
| `construction-detail` | Đường tâm, nét đứt hoặc chi tiết phụ |

SVG không chứa `<text>`, `<image>`, font, base64, JavaScript, CSS ngoài, tài
nguyên ngoài, filter glow hoặc phần tử nền phủ toàn canvas.

## Sáu icon

1. **Threaded Shaft** — trục nghiêng với vai đầu và năm gờ ren chính.
2. **Slot Cut** — khối isometric, rãnh màu tác động và dao phay đứng phía trên.
3. **Mirror Body** — hai khối đối xứng qua mặt phẳng mirror nét đứt.
4. **Offset Surface** — hai sheet cong song song, không mô phỏng thành solid.
5. **Scale Up** — khối gốc, khối đích lớn hơn dạng outline và mũi tên phóng to.
6. **Join Bodies** — hai thân có đầu lồi/lõm hướng vào nhau để minh họa phép nối.

## Quy trình build PNG

`tools/build_isometric_cadcam_icons.py` lấy SVG làm nguồn, dùng
`PySide6.QtSvg.QSvgRenderer`, `QImage.Format_ARGB32`, `QPainter` antialiasing
và fill transparent để tạo PNG ở 24, 32, 48 và 64 px:

```text
python tools/build_isometric_cadcam_icons.py
python tools/build_isometric_cadcam_icons.py --check
python tools/build_isometric_cadcam_icons.py --sizes 24 32 48 64
```

Script tự xác định repository từ vị trí file nên không phụ thuộc working
directory. Build có thể chạy lặp an toàn; file chỉ được thay khi bytes thay đổi.
`--check` kiểm tra SVG, renderer, kích thước, PNG color type RGBA, alpha ở góc,
crop và byte render deterministic; lỗi trả exit code khác 0.

## Transparency và contact sheet

Mỗi thư mục `png/<size>/` có đủ sáu PNG, nền hoàn toàn trong suốt và vùng hữu
hình không chạm mép. Build tạo các sheet review vào
`reference_private/DERIVED/UI_ICON_PACK_9AI1/`:

- `contact_sheet_64.png`
- `contact_sheet_32.png`
- `contact_sheet_24.png`
- `comparison_all_sizes.png`

Sheet dùng checkerboard nhạt và nhãn ở ngoài icon. Đây là artifact dẫn xuất,
không được stage hoặc Git track; `.gitignore` của repository đã loại
`reference_private/`.

## Visual QA

Sau build, mở bốn contact sheet và kiểm tra đặc biệt: ren của Threaded Shaft,
dao đang tạo Slot Cut, hai khối và mặt phẳng của Mirror Body, hai sheet cong
song song của Offset Surface, quan hệ khối gốc/khối lớn của Scale Up, và đầu
lồi/lõm khớp nhau của Join Bodies. Kiểm tra ở 24 px rằng rãnh không nhập thành
một mảng, nét đứt và arrowhead còn đọc được, không có nền, glow, emoji hay cảm
giác đồ chơi 3D.

## Accessibility

Icon không dùng màu làm tín hiệu duy nhất: hình học, mặt phẳng, nét construction
và hướng mũi tên mang ý nghĩa chính. Khi tích hợp sau này, Ribbon phải bổ sung
accessible name/tooltip bằng văn bản tiếng Việt và trạng thái enabled/disabled;
nhãn review trong contact sheet không phải metadata production.

## Giới hạn và kế hoạch review

Đây là icon pack foundation, không phải cam kết tích hợp UI. Chưa có resource
path production mới, chưa thay icon cũ và chưa có kiểm thử màn hình native DPI
125/150%. Trước khi tích hợp Ribbon cần review với người dùng CAD/CAM, xác nhận
nhận dạng ở 24 px trên Windows 10/11, thống nhất tên/tooltip và quyết định
resource packaging. Giai đoạn tích hợp Ribbon là công việc riêng sau khi bộ này
được duyệt.
