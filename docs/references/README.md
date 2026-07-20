# Tài liệu tham khảo CAD/CAM

Thư mục này là nơi tổ chức các ghi chú và chỉ mục tham khảo cho các quy trình
CAD/CAM liên quan đến HMS CAD/CAM. Hiện tại có ba nhánh tài liệu:

- [`worknc/`](worknc/): WorkNC.
- [`mastercam/`](mastercam/): Mastercam.
- [`nx/`](nx/): Siemens NX.

Đây chỉ là kho tài liệu tham khảo; không đặt mã nguồn ứng dụng, dữ liệu dự án
`.HMS` hoặc file CAD/CAM đầu vào của người dùng ở đây.

## Cấu trúc mỗi sản phẩm

Các nhánh sản phẩm dùng cùng một cấu trúc để dễ tìm kiếm và đối chiếu:

| Thư mục | Mục đích |
| --- | --- |
| `manuals/` | Chỉ mục, trích dẫn và ghi chú theo manual/phiên bản. |
| `workflows/` | Ghi chú quy trình thao tác và thuật ngữ. |
| `post-processors/` | Ghi chú về post, controller và quy ước xuất NC. |
| `examples/` | Ví dụ nhỏ đã được làm sạch, không chứa dữ liệu khách hàng. |
| `notes/` | Ghi chú nội bộ, so sánh và quyết định thiết kế. |
| `assets/` | Ảnh chụp, sơ đồ hoặc tệp minh họa chỉ dùng cục bộ. |
| `external/` | Tài liệu tải về hoặc tệp nhị phân chỉ dùng cục bộ; không commit. |

Các thư mục được giữ trong Git bằng `.gitkeep` cho đến khi có tài liệu phù
hợp. Có thể thêm Markdown, hình minh họa hoặc chỉ mục vào các thư mục công
khai; tệp tải về lớn và định dạng phân phối của nhà cung cấp đã được bỏ qua
trong `.gitignore`.

## Quy tắc bổ sung tài liệu

1. Ghi rõ sản phẩm, phiên bản, tiêu đề, nguồn, ngày truy cập và quyền sử dụng.
2. Không đưa thông tin đăng nhập, dữ liệu khách hàng, đường dẫn máy cá nhân
   hoặc bản sao manual bị hạn chế phân phối vào Git.
3. Dùng [mẫu metadata nguồn](SOURCE_TEMPLATE.md) cho mỗi tài liệu bên ngoài
   được lập chỉ mục.
4. Đặt tên file bằng chữ thường, dùng dấu gạch ngang hoặc gạch dưới; tránh
   dấu cách và ký tự phụ thuộc Windows.

## Quy ước phiên bản

Khi ghi chú phụ thuộc vào một bản phát hành cụ thể, đặt phiên bản trong tên
file hoặc tiêu đề, ví dụ `worknc-2025-terminology.md`. Không sửa tài liệu
nguồn; tạo ghi chú mới khi cần so sánh giữa các phiên bản.
