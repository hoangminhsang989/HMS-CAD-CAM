# HMS CAD/CAM

HMS CAD/CAM là ứng dụng desktop CAD/CAM dành cho Windows 10/11 64-bit. Phiên bản hiện tại mới triển khai khung giao diện PySide6 theo phong cách phần mềm CAD chuyên nghiệp; vùng CAD Viewport chỉ là placeholder và chưa có CAD kernel.

## Phạm vi hiện tại

- Ribbon, menu, toolbar và status bar.
- Project Manager, Properties và Output/Log dạng dock có thể thay đổi kích thước.
- CAD Viewport placeholder có lưới và trục tọa độ minh họa.
- Logging ra console và `%LOCALAPPDATA%\HMS CADCAM\logs\hms_cadcam.log`.
- Chưa có hệ thống dự án `.HMS`, Open CASCADE hoặc chức năng CAM.

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
