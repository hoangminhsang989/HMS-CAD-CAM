# HMS CAD/CAM

HMS CAD/CAM là ứng dụng desktop CAD/CAM dành cho Windows 10/11 64-bit. Phiên bản hiện tại mới triển khai khung giao diện PySide6 theo phong cách phần mềm CAD chuyên nghiệp; vùng CAD Viewport chỉ là placeholder và chưa có CAD kernel.

## Phạm vi hiện tại

- Ribbon, menu, toolbar và status bar.
- Project Manager, Properties và Output/Log dạng dock có thể thay đổi kích thước.
- CAD Viewport placeholder có lưới và trục tọa độ minh họa.
- Logging ra console và `%LOCALAPPDATA%\HMS CADCAM\logs\hms_cadcam.log`.
- Tạo, mở, lưu, Save As và đóng dự án dạng thư mục `.HMS`.
- Sao chép nguyên vẹn file CAD nguồn vào `source/`; chưa phân tích hình học.
- Chưa có Open CASCADE, CAD kernel, autosave hoặc chức năng CAM.

## Dự án `.HMS`

`.HMS` là thư mục Windows thật. Cấu trúc Giai đoạn 2 chỉ gồm dữ liệu đang được sử dụng:

```text
TEN_DU_AN.HMS/
├── project.hms.json
├── project.db
└── source/
```

Một thư mục hợp lệ phải kết thúc bằng `.HMS`, có manifest với `format` bằng `HMS_PROJECT` và database SQLite đúng version. File nguồn của người dùng chỉ được đọc và sao chép; ứng dụng không chỉnh sửa file gốc.

## Môi trường phát triển

Không sử dụng thư mục `venv/` cũ. Tạo môi trường riêng `.venv` bằng Python 3.14.5 64-bit:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Không thêm `venv/Lib/site-packages` vào `PYTHONPATH`.

## Chạy ứng dụng

```powershell
.\.venv\Scripts\python.exe main.py
```

## Chạy kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pytest
```
