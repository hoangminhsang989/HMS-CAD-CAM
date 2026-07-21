# HMS CAD/CAM

HMS CAD/CAM là ứng dụng desktop CAD/CAM cho Windows 10/11 64-bit, dùng
PySide6 và Python 3.14.6. Repository hiện đã có hệ thống dự án `.HMS`, CAD
Viewer dựa trên Open CASCADE/OCP, nền tảng CAM 2.5D, Simulation/Collision và
workflow Post Processor FANUC ROBODRILL 21i.

## Phạm vi hiện tại

- Giao diện chính gồm ribbon/menu/toolbar, Project Manager, CAD Viewport,
  Properties, Output/Log và status bar.
- Tạo, mở, lưu, Save As, Autosave/Recovery và đóng dự án dạng thư mục `.HMS`.
- Giữ nguyên file CAD nguồn trong `source/`; dữ liệu dự án và CAD/CAM được quản
  lý qua service, manifest JSON UTF-8 và SQLite schema v4.
- Import, hiển thị và lưu trạng thái CAD/XCAF; Open CASCADE được cô lập sau
  adapter.
- CAM hiện có Facing, Planar Face Facing, Contour, Pocket, Drilling, Tapping,
  Reaming và Boring. Tapping có domain/toolpath/UI nhưng production Post vẫn
  fail-closed.
- Simulation/Collision v1 có PASS/WARN/FAIL, provenance/fingerprint, cache và
  stale guard.
- Post Processor hỗ trợ workflow single-operation và Program Assembly nhiều
  operation theo explicit order. Kết quả `.fn` deterministic có preview,
  checksum, managed artifact và export explicit đến thư mục local/mapped/UNC.

Program Assembly 7D.3.2 không tự sắp xếp hoặc gom operation cùng dao, không tự
Generate/Export, và không gửi chương trình trực tiếp tới CNC. Output ROBODRILL
21i chưa được machine-certified; phải review, dry-run và single-block theo quy
trình tại máy trước khi sản xuất.

## Dự án `.HMS`

`.HMS` là thư mục Windows thật, không phải file nén. Một dự án hợp lệ có tên
kết thúc bằng `.HMS`, chứa `project.hms.json` với `format` bằng `HMS_PROJECT`
và `project.db` đúng version. File CAD nguồn chỉ được đọc và sao chép; ứng dụng
không sửa trực tiếp file gốc của người dùng.

Các thư mục như `cache/` và `temp/` chỉ chứa dữ liệu có thể tái tạo hoặc dữ
liệu tạm. NC managed được lưu trong `nc/` cùng manifest/sidecar dưới `post/`.

## Môi trường phát triển

Không sử dụng thư mục `venv/` cũ. Tạo môi trường riêng `.venv` bằng Python
3.14.6 64-bit:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Không thêm `venv/Lib/site-packages` vào `PYTHONPATH`.

## Chạy ứng dụng

```powershell
.\.venv\Scripts\python.exe main.py
```

Trong CAM workspace, tab `Post Processor` giữ workflow single-operation;
tab `Program Assembly` cung cấp workflow multi-operation của 7D.3.2.

## Chạy kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pytest
```

GUI smoke riêng cho Program Assembly:

```powershell
.\.venv\Scripts\python.exe tests\manual_stage7d32_program_assembly_gui.py
```

Harness GUI không tự mở/chạy file `.fn` và cố ý chuẩn bị các trạng thái
Simulation chặn để người kiểm tra quan sát fail-closed behavior.
