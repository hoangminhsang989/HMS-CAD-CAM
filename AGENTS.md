# AGENTS.md — Quy tắc phát triển HMS CAD/CAM

## 1. Mục tiêu dự án

Xây dựng phần mềm **HMS CAD/CAM** chạy trên Windows 10/11 64-bit.

Mục tiêu dài hạn:

- Đọc, hiển thị và chuyển đổi dữ liệu CAD 2D/3D.
- Quản lý dự án bằng thư mục có đuôi `.HMS`.
- Lập trình CAM phay, khoan và tiện.
- Tạo, lưu, chỉnh sửa và mô phỏng đường chạy dao.
- Xuất G-code thông qua Post Processor.
- Tạo Setup Sheet.
- Đóng gói thành bộ cài Windows.

Không được tuyên bố rằng một tính năng đã hoàn thành nếu chưa có mã chạy được và chưa được kiểm tra.

---

## 2. Môi trường mục tiêu

- Hệ điều hành: Windows 10/11 64-bit.
- IDE: Visual Studio Code.
- Python hiện tại: Python 3.14.6 64-bit.
- Ngôn ngữ giao diện chính: tiếng Việt.
- GUI: ưu tiên PySide6.
- CAD kernel dự kiến: Open CASCADE.
- Cơ sở dữ liệu: SQLite.
- Cấu hình và manifest: JSON UTF-8.
- Kiểm thử: pytest.
- Quản lý phiên bản: Git.

Trước khi thêm thư viện, phải kiểm tra thư viện đó có hỗ trợ Python 3.14 trên Windows hay không. Nếu chưa hỗ trợ, không tự ý cài bản không tương thích; phải báo cáo và đề xuất phương án thay thế.

---

## 3. Quy tắc làm việc bắt buộc

1. Luôn khảo sát cấu trúc repository và mã hiện có trước khi sửa.
2. Không xóa file hoặc đoạn mã cũ khi chưa giải thích rõ lý do.
3. Không viết toàn bộ ứng dụng trong một file.
4. Không tạo “mã giả” rồi báo là đã hoàn thành.
5. Không tạo chức năng nằm ngoài phạm vi nhiệm vụ hiện tại.
6. Mỗi module chỉ nên có một nhóm trách nhiệm rõ ràng.
7. Dùng `pathlib.Path` cho đường dẫn.
8. Không gắn cứng đường dẫn theo máy người phát triển.
9. Tất cả file văn bản phải dùng UTF-8.
10. Có type hints cho public functions và public methods.
11. Có logging thay vì dùng `print()` cho lỗi vận hành.
12. Có xử lý ngoại lệ ở ranh giới I/O, import CAD, lưu dự án và database.
13. Không dùng `except Exception: pass`.
14. Mọi thay đổi schema dự án phải có `format_version`.
15. Không sửa trực tiếp dữ liệu nguồn của người dùng.
16. File CAD gốc phải được giữ nguyên trong thư mục `source`.
17. Cache phải có thể xóa và tái tạo.
18. Dữ liệu chính không được lưu trong `temp` hoặc `cache`.
19. Không đóng băng giao diện khi đọc file lớn; tác vụ nặng phải chạy nền.
20. Không triển khai CAM trước khi hệ thống dự án và CAD Viewer ổn định.

---

## 4. Quy trình cho mỗi nhiệm vụ

Codex phải thực hiện theo thứ tự:

1. Đọc yêu cầu.
2. Khảo sát file liên quan.
3. Nêu kế hoạch ngắn.
4. Chỉ sửa các file cần thiết.
5. Chạy formatter/linter/test hoặc kiểm tra import phù hợp.
6. Chạy thử luồng chức năng liên quan nếu có thể.
7. Báo cáo:
   - File đã tạo.
   - File đã sửa.
   - Tính năng đã hoàn thành.
   - Lệnh kiểm tra đã chạy.
   - Kết quả kiểm tra.
   - Hạn chế hoặc việc chưa làm.
8. Không tự động bắt đầu giai đoạn tiếp theo.

Nếu yêu cầu có rủi ro ảnh hưởng kiến trúc hoặc dữ liệu dự án, phải dừng ở bước lập kế hoạch và chờ xác nhận.

---

## 5. Quy tắc Git

Trước nhiệm vụ lớn:

```powershell
git status
git add .
git commit -m "checkpoint truoc thay doi"
```

Sau khi hoàn thành và kiểm tra thành công:

```powershell
git status
git add .
git commit -m "mo ta thay doi"
```

Không dùng các lệnh phá hủy như sau nếu chưa được yêu cầu rõ ràng:

```text
git reset --hard
git clean -fd
git checkout -- .
```

---

## 6. Định dạng dự án `.HMS`

`.HMS` là một **thư mục Windows thật**, không phải file nén.

Ví dụ:

```text
D:\CONG_VIEC\TRUC_MAY.HMS\
```

Một thư mục được nhận diện là dự án HMS khi:

- Tên thư mục kết thúc bằng `.HMS`.
- Có file `project.hms.json`.
- Manifest có `"format": "HMS_PROJECT"`.

Cấu trúc mục tiêu:

```text
TEN_DU_AN.HMS/
├── project.hms.json
├── project.db
├── source/
├── model/
├── mesh/
├── cad/
├── cam/
├── toolpaths/
├── stock/
├── fixtures/
├── tools/
├── machines/
├── post/
├── nc/
├── setup/
├── cache/
├── temp/
├── autosave/
├── backups/
└── logs/
```

Phân loại dữ liệu:

- `source/`: file gốc, không chỉnh sửa.
- `model/`: dữ liệu hình học chuẩn hóa.
- `mesh/`: dữ liệu hiển thị và mô phỏng.
- `cad/`: layer, màu, transform, metadata.
- `cam/`: nguyên công và tham số CAM.
- `toolpaths/`: đường chạy dao đã tính.
- `cache/`: dữ liệu có thể tái tạo.
- `temp/`: dữ liệu tạm của phiên làm việc.
- `autosave/`: phục hồi sau lỗi/mất điện.
- `backups/`: bản sao phiên bản trước.
- `nc/`: chương trình G-code đầu ra.
- `setup/`: Setup Sheet và hình minh họa.

---

## 7. Nguyên tắc kiến trúc

Các lớp chính không được phụ thuộc trực tiếp lẫn nhau một cách vòng tròn.

Phân lớp dự kiến:

```text
UI
↓
Application Services
↓
Domain Model
↓
Infrastructure Adapters
↓
CAD Kernel / Database / File System
```

Các khối chức năng:

- `hms_app`: khởi động ứng dụng và điều phối.
- `hms_ui`: cửa sổ, menu, dock, dialog.
- `hms_project`: tạo, mở, lưu, đóng và migrate dự án.
- `hms_cad`: tài liệu CAD, import/export và topology.
- `hms_viewer`: viewport, camera, selection và rendering.
- `hms_cam`: nguyên công và thuật toán CAM.
- `hms_tools`: dao và holder.
- `hms_machine`: máy, trục và controller.
- `hms_post`: chuyển dữ liệu toolpath trung gian thành NC.
- `hms_setup`: tạo Setup Sheet.
- `hms_core`: kiểu dữ liệu, event, command và tiện ích dùng chung.
- `tests`: kiểm thử tự động.

UI không được tự đọc/ghi trực tiếp `project.db`. UI phải gọi service.

---

## 8. Quy tắc giao diện

Giao diện chính dự kiến gồm:

- Menu trên cùng.
- Thanh công cụ.
- Project Manager bên trái.
- Viewport CAD ở giữa.
- Properties bên phải.
- Output/Log phía dưới.
- Status Bar.

Không đặt logic nghiệp vụ phức tạp trong widget hoặc event handler.

Các tác vụ sau phải chạy ngoài UI thread:

- Import file CAD lớn.
- Tessellation.
- Tính toolpath.
- Mô phỏng.
- Xuất file lớn.
- Tạo Setup Sheet nhiều ảnh.

---

## 9. Tiêu chí hoàn thành một tính năng

Một tính năng chỉ được coi là hoàn thành khi:

- Có mã thực thi.
- Có xử lý lỗi cơ bản.
- Có kiểm tra tự động hoặc kịch bản kiểm tra rõ ràng.
- Chạy được trong môi trường mục tiêu.
- Không phá vỡ tính năng đang có.
- Có cập nhật tài liệu nếu hành vi người dùng thay đổi.
- Có báo cáo giới hạn còn lại.

---

## 10. Phạm vi hiện tại

Ưu tiên hiện tại:

1. Chuẩn hóa repository.
2. Khung ứng dụng PySide6.
3. Hệ thống dự án `.HMS`.
4. CAD Viewer.
5. Import/export CAD.
6. Đo kiểm và chọn đối tượng.
7. Sau đó mới đến CAM.

Không bắt đầu phay, tiện, Post Processor hoặc Setup Sheet khi chưa có chỉ thị riêng.
