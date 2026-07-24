# HMS CAD/CAM

HMS CAD/CAM là ứng dụng desktop CAD/CAM cho Windows 10/11 64-bit, dùng
PySide6 và Python 3.14.6. Repository hiện có hai chế độ tài liệu, CAD
Viewer dựa trên Open CASCADE/OCP, nền tảng CAM 2.5D, Simulation/Collision và
workflow Post Processor FANUC ROBODRILL 21i.

Stage 8A.4.2 về kiến trúc hai chế độ tài liệu đã hoàn thành; chưa bắt đầu stage
kế tiếp hoặc stage đa ngôn ngữ.

## Phạm vi hiện tại

- Giao diện chính gồm ribbon/menu/toolbar, Project Manager, CAD Viewport,
  Properties, Output/Log và status bar.
- Mở CAD đơn lẻ và lưu thành một file container `.HMS`; dự án CAM mới làm việc
  trực tiếp trong một thư mục workspace riêng.
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

## Hai chế độ tài liệu

`CAD_DOCUMENT` là tài liệu CAD/3D đơn lẻ. Người dùng lưu một file `.HMS`;
container có manifest, metadata, hình học, trạng thái hiển thị và checksum.
Tên file được giữ Unicode/dấu cách nếu hợp lệ trên Windows. File này không phải
container dự án CAM.

`CAM_PROJECT` là thư mục workspace không có hậu tố `.HMS`. Dự án mới có
`manifest.json`, `project.db`, `source/`, `working-geometry/`, `autosave/`,
`backups/`, `temp/`, `replaced/` và inbox `incoming-geometry/`. Tên hiển thị
tiếng Việt được giữ trong manifest; tên thư mục vật lý chỉ dùng ASCII
chữ/số/dấu `-`.

Loader vẫn tương thích các dự án thư mục `.HMS` cũ có `project.hms.json`.
Không tự đóng gói dự án CAM thành file `.HMS`. File CAD nguồn bên ngoài chỉ
được đọc và sao chép; ứng dụng không sửa trực tiếp dữ liệu gốc của người dùng.

Các thư mục như `cache/` và `temp/` chỉ chứa dữ liệu có thể tái tạo hoặc dữ
liệu tạm. Hình học làm việc CAM không nén nằm trong `working-geometry/`; mesh
hiển thị không thay thế exact geometry. NC managed được lưu trong `nc/` cùng
manifest/sidecar dưới `post/`.

Từ một tài liệu `.HMS` đã lưu, lệnh `Nạp 3D mới cho dự án CAM` sao chép exact
geometry vào inbox atomic của project được chọn. Project đang mở phát hiện bằng
watcher + polling; project đóng phát hiện khi mở lại. Người dùng xem preview và
chọn rõ Add/Replace/Update. Không có auto-apply, auto-Calculate, auto-Simulation
hoặc auto-Post; lỗi apply rollback và giữ mô hình cũ. Geometry transfer không
phải chứng nhận an toàn hoặc machine-ready và không sao chép claim READY/SAFE.

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
