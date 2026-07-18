# HMS CAD/CAM — Kế hoạch tổng thể để giao việc cho Codex

## I. Tầm nhìn sản phẩm

Tên tạm thời: **HMS CAD/CAM**

Đây là phần mềm CAD/CAM desktop cho Windows, định hướng tương tự quy trình làm việc của Mastercam và WorkNC nhưng sử dụng định dạng dự án riêng.

Mục tiêu:

- Mở và hiển thị file CAD 2D/3D.
- Chuyển đổi các định dạng CAD phổ biến.
- Tạo và chỉnh sửa dữ liệu hình học phục vụ gia công.
- Lập trình phay 2D, phay 3D, khoan và tiện.
- Quản lý máy, dao, holder, phôi và đồ gá.
- Tính toán và mô phỏng đường chạy dao.
- Xuất G-code qua Post Processor.
- Tạo Setup Sheet.
- Đóng gói thành phần mềm cài vào Windows.

---

## II. Ý tưởng cốt lõi của dự án `.HMS`

Mastercam thường lưu công việc thành một file dự án. HMS CAD/CAM sẽ dùng một **thư mục dự án có đuôi `.HMS`**.

Ví dụ:

```text
D:\SAN_PHAM\CHI_TIET_A.HMS\
```

Khi máy có HMS CAD/CAM:

- Phần mềm nhận diện đây là dự án HMS.
- Người dùng mở bằng menu, kéo thả hoặc menu chuột phải.
- Phần mềm đọc manifest, database, mô hình, nguyên công và cache.

Khi máy không có HMS CAD/CAM:

- Nó vẫn là thư mục Windows bình thường.
- Người dùng vẫn có thể mở, sao chép, nén ZIP và xem file NC/Setup Sheet.

### Luồng nhập file 3D

1. Người dùng chọn file STEP, IGES, STL hoặc định dạng hỗ trợ khác.
2. Phần mềm đọc và hiển thị thử.
3. Phần mềm mở hộp thoại chọn nơi lưu dự án.
4. Vị trí mặc định là nơi chứa file CAD gốc.
5. Tên mặc định là tên file nguồn cộng `.HMS`.
6. Phần mềm tạo thư mục dự án.
7. Sao chép file gốc vào `source/`.
8. Chuẩn hóa mô hình vào định dạng nội bộ.
9. Tạo mesh hiển thị.
10. Tạo manifest và database.
11. Tạo thumbnail.
12. Mở phiên làm việc của dự án.

---

## III. Cấu trúc repository mục tiêu

```text
CAD_CAM_PROJECT/
├── main.py
├── pyproject.toml
├── requirements.txt
├── README.md
├── AGENTS.md
├── CHANGELOG.md
├── LICENSE
│
├── src/
│   └── hms_cadcam/
│       ├── __init__.py
│       ├── application.py
│       │
│       ├── core/
│       │   ├── commands.py
│       │   ├── events.py
│       │   ├── exceptions.py
│       │   ├── logging_config.py
│       │   └── paths.py
│       │
│       ├── ui/
│       │   ├── main_window.py
│       │   ├── menus.py
│       │   ├── toolbars.py
│       │   ├── status_bar.py
│       │   ├── dialogs/
│       │   ├── docks/
│       │   └── widgets/
│       │
│       ├── project/
│       │   ├── models.py
│       │   ├── manifest.py
│       │   ├── creator.py
│       │   ├── loader.py
│       │   ├── saver.py
│       │   ├── autosave.py
│       │   ├── recovery.py
│       │   ├── recent_projects.py
│       │   └── migrations/
│       │
│       ├── cad/
│       │   ├── document.py
│       │   ├── topology.py
│       │   ├── properties.py
│       │   ├── importers/
│       │   └── exporters/
│       │
│       ├── viewer/
│       │   ├── viewport.py
│       │   ├── camera.py
│       │   ├── selection.py
│       │   ├── display_modes.py
│       │   ├── measurement.py
│       │   └── clipping.py
│       │
│       ├── cam/
│       │   ├── common/
│       │   ├── milling/
│       │   ├── turning/
│       │   ├── drilling/
│       │   ├── toolpath/
│       │   └── simulation/
│       │
│       ├── tools/
│       ├── machine/
│       ├── post/
│       ├── setup_sheet/
│       └── resources/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── scripts/
│   ├── run_dev.ps1
│   ├── build.ps1
│   └── package.ps1
│
├── installer/
└── docs/
```

Codex phải khảo sát repository thực tế trước. Không được tạo lại toàn bộ cấu trúc nếu dự án hiện tại đã có module tương đương.

---

## IV. Lộ trình triển khai

### Giai đoạn 0 — Khảo sát và chuẩn hóa

Mục tiêu:

- Đọc toàn bộ repository.
- Phân loại mã nguồn, tài liệu, file thử nghiệm và công cụ.
- Phát hiện file trùng, import hỏng và phụ thuộc chưa rõ.
- Đề xuất cấu trúc mới.
- Chưa sửa code cho tới khi người dùng xác nhận.

Đầu ra:

- Báo cáo repository.
- Sơ đồ module.
- Danh sách mã có thể tái sử dụng.
- Danh sách rủi ro.
- Kế hoạch di chuyển an toàn.

---

### Giai đoạn 1 — Khung ứng dụng desktop

Mục tiêu:

- Chạy được bằng `python main.py`.
- Tạo cửa sổ chính PySide6.
- Menu: File, Edit, View, CAD, CAM, Machine, Toolpath, Setup, Help.
- Project Manager bên trái.
- Viewport placeholder ở giữa.
- Properties bên phải.
- Output/Log phía dưới.
- Status Bar.
- Cơ chế logging.
- Cấu hình người dùng cơ bản.

Chưa tích hợp CAD kernel.

Tiêu chí nghiệm thu:

- Ứng dụng khởi động và đóng không lỗi.
- Dock có thể thay đổi kích thước.
- Menu và toolbar không bị treo.
- Có smoke test import.
- Không có logic CAM giả.

---

### Giai đoạn 2 — Hệ thống dự án `.HMS`

Mục tiêu:

- New Project.
- Import CAD rồi Save As Project.
- Open HMS Project.
- Save.
- Save As.
- Close Project.
- Recent Projects.
- Tạo đầy đủ thư mục con.
- Tạo `project.hms.json`.
- Tạo `project.db`.
- Sao chép file nguồn.
- Validation đường dẫn.
- Ghi log.

Manifest tối thiểu:

```json
{
  "format": "HMS_PROJECT",
  "format_version": 1,
  "application": "HMS CAD/CAM",
  "application_version": "0.1.0",
  "project_id": "UUID",
  "project_name": "CHI_TIET_A",
  "created_at": "ISO-8601",
  "modified_at": "ISO-8601",
  "units": "mm",
  "source_files": [],
  "active_document": null,
  "database": "project.db"
}
```

Tiêu chí nghiệm thu:

- Tạo được dự án có dấu tiếng Việt và khoảng trắng.
- Không tạo dự án lồng `.HMS\.HMS`.
- Không ghi đè dự án cũ nếu chưa xác nhận.
- Dự án mở lại giữ đúng metadata.
- Test đường dẫn và manifest thành công.

---

### Giai đoạn 3 — Autosave, lock và phục hồi

Mục tiêu:

- Tạo `session.lock` khi mở dự án.
- Phát hiện dự án bị đóng bất thường.
- Autosave theo thời gian và sự kiện.
- Không lưu khi không có thay đổi.
- Phục hồi từ `autosave/`.
- Backup luân phiên.
- Dọn `temp/` an toàn.
- Cache có thể xóa riêng.

Tiêu chí nghiệm thu:

- Mô phỏng đóng cưỡng bức rồi mở lại thấy đề nghị phục hồi.
- Không làm hỏng project chính.
- Hai phiên không được cùng ghi một dự án mà không cảnh báo.

---

### Giai đoạn 4 — Tích hợp CAD kernel và CAD Viewer

Mục tiêu:

- Kiểm tra và chọn phương án Open CASCADE tương thích môi trường.
- Không gắn UI trực tiếp vào chi tiết thư viện CAD.
- Tạo adapter `CadKernel`.
- Hiển thị mô hình trong viewport.
- Xoay, pan, zoom, fit all.
- Hướng nhìn Top, Bottom, Front, Back, Left, Right, Isometric.
- Shaded, Wireframe, Shaded With Edges.
- Nền và lưới tọa độ.
- Hiển thị trục XYZ.

Tiêu chí nghiệm thu:

- Mở được mô hình thử.
- Viewport không treo khi resize.
- Camera hoạt động ổn định.
- Có fallback thông báo rõ nếu CAD kernel chưa cài.

---

### Giai đoạn 5 — Import và export CAD

Thứ tự ưu tiên:

1. STEP/STP.
2. IGES/IGS.
3. BREP.
4. STL.
5. DXF 2D.
6. Các định dạng khác sau.

Mỗi importer phải trả về một kết quả chuẩn:

```text
ImportResult
- success
- document_id
- source_path
- detected_format
- detected_units
- warnings
- errors
- import_duration
- entity_counts
```

Yêu cầu:

- Giữ file gốc.
- Báo tiến trình.
- Có hủy tác vụ.
- Không khóa UI.
- Phân biệt warning và error.
- Không báo import thành công khi document rỗng.
- Sau import phải lưu mô hình chuẩn hóa và mesh hiển thị.

---

### Giai đoạn 6 — Cây mô hình, lựa chọn và thuộc tính

Mục tiêu:

- Cây document/assembly.
- Chọn body, solid, shell, face, edge và vertex.
- Đồng bộ selection giữa tree và viewport.
- Ẩn/hiện.
- Đổi màu.
- Transparency.
- Bounding box.
- Diện tích, thể tích và trọng tâm.
- Tên đối tượng và metadata.
- Undo/Redo cho thay đổi hiển thị.

---

### Giai đoạn 7 — Đo kiểm CAD

Mục tiêu:

- Khoảng cách điểm–điểm.
- Khoảng cách cạnh/mặt.
- Bán kính.
- Đường kính.
- Góc.
- Tọa độ điểm.
- Chiều dài cạnh.
- Diện tích mặt.
- Bounding dimensions.
- Section/clipping plane.

Kết quả đo phải:

- Hiển thị trong viewport.
- Hiển thị trong Properties.
- Có thể copy.
- Có đơn vị.
- Có độ chính xác cấu hình được.

---

### Giai đoạn 8 — CAD 2D cơ bản

Mục tiêu:

- Sketch/document 2D.
- Point, line, polyline, arc, circle.
- Trim, extend, offset.
- Layer, màu và kiểu nét.
- Snapping.
- Dimension.
- Import/export DXF.
- Chọn contour dùng cho CAM.

Không làm hệ thống parametric phức tạp ngay trong bản đầu.

---

### Giai đoạn 9 — Nền tảng CAM chung

Chỉ bắt đầu sau khi Giai đoạn 2–7 ổn định.

Mục tiêu:

- Job Setup.
- WCS.
- Stock.
- Fixture.
- Tool Library.
- Holder Library.
- Machine Definition.
- Operation Tree.
- Toolpath intermediate representation.
- Dirty/recompute dependency.
- Undo/Redo.
- Lưu đầy đủ vào dự án `.HMS`.

Định dạng toolpath trung gian không được phụ thuộc Fanuc:

```json
{
  "kind": "linear",
  "position": [100.0, 25.0, -5.0],
  "feed": 800.0,
  "spindle": 4500.0,
  "coolant": "flood"
}
```

---

### Giai đoạn 10 — Phay 2D và khoan

Thứ tự:

1. Face Milling.
2. Drill.
3. Peck Drill.
4. Tap.
5. 2D Contour.
6. Pocket.
7. Slot.
8. Chamfer.
9. Helix bore.
10. Entry/exit linking.

Mỗi nguyên công phải có:

- Geometry.
- Tool.
- Feeds and speeds.
- Heights.
- Linking.
- Compensation.
- Coolant.
- Safety validation.
- Generated toolpath.
- Status: clean/dirty/error.

---

### Giai đoạn 11 — Phay 3D

Thứ tự:

- 3D roughing.
- Z-level/waterline.
- Parallel finishing.
- Scallop finishing.
- Rest machining.
- Pencil finishing.
- Boundary control.
- Collision avoidance.

Đây là giai đoạn thuật toán nặng. Có thể cần chuyển phần lõi sang C++ sau khi API Python đã ổn định.

---

### Giai đoạn 12 — Tiện

Thứ tự:

- Turning setup.
- Stock trụ.
- Facing.
- OD/ID roughing.
- Finishing.
- Grooving.
- Threading.
- Drilling theo trục.
- Part-off.
- Tool orientation.
- Lathe simulation.

---

### Giai đoạn 13 — Mô phỏng và kiểm tra

Mục tiêu:

- Tool animation.
- Holder.
- Stock removal.
- Rapid collision.
- Tool/holder/fixture collision.
- Gouge detection.
- Remaining stock.
- Estimated machining time.
- Báo cáo cảnh báo.

Mô phỏng hình ảnh không được coi là kiểm tra va chạm chính xác nếu chỉ dùng mesh thô.

---

### Giai đoạn 14 — Post Processor

Kiến trúc:

```text
Operation
→ Toolpath trung gian
→ Post Processor
→ Controller-specific NC
```

Mục tiêu:

- Post template.
- Machine/controller variables.
- Event hooks.
- Sequence numbers.
- Units.
- Work offsets.
- Tool changes.
- Spindle/coolant.
- Arc modes.
- Canned cycles.
- Header/footer.
- Validation.
- NC preview.
- Post log.

Post đầu tiên nên là Fanuc 3 trục đơn giản, sau đó mới mở rộng.

---

### Giai đoạn 15 — Setup Sheet

Nội dung:

- Tên dự án và chi tiết.
- Revision.
- Máy.
- Controller.
- Phôi.
- Vật liệu.
- WCS.
- Đồ gá.
- Danh sách dao và holder.
- Hình tổng thể.
- Hình từng setup/nguyên công.
- Feeds/speeds.
- Thời gian dự kiến.
- Danh sách chương trình NC.
- Ghi chú người vận hành.

Đầu ra:

- HTML tự chứa.
- PDF.
- Thư mục ảnh.
- Dữ liệu JSON phục vụ template.

---

### Giai đoạn 16 — Đóng gói và bộ cài

Mục tiêu:

- Build bản Windows 64-bit.
- Cài vào `C:\Program Files\HMS CADCAM`.
- Dữ liệu dùng chung trong `C:\ProgramData\HMS CADCAM`.
- Cấu hình người dùng trong `%LOCALAPPDATA%\HMS CADCAM`.
- Shortcut Desktop/Start Menu.
- Uninstaller.
- Menu chuột phải “Open with HMS CAD/CAM” cho thư mục `.HMS`.
- Không xóa dự án người dùng khi uninstall.
- Kiểm tra cài mới trên máy sạch hoặc Windows Sandbox/VM.

---

## V. Quy trình giao việc hằng ngày cho Codex

Mỗi nhiệm vụ chỉ nên có một mục tiêu chính.

### Mẫu giao việc chuẩn

```text
NHIỆM VỤ:
[Mô tả một mục tiêu duy nhất]

PHẠM VI:
- File/module được phép sửa.
- File/module không được sửa.
- Chức năng phải có.
- Chức năng chưa làm.

TIÊU CHÍ NGHIỆM THU:
1. ...
2. ...
3. ...

QUY TRÌNH:
1. Khảo sát mã liên quan.
2. Nêu kế hoạch ngắn trước khi sửa.
3. Thực hiện thay đổi tối thiểu.
4. Chạy test/kiểm tra.
5. Báo cáo file thay đổi và kết quả.
6. Không tự bắt đầu nhiệm vụ khác.
```

---

## VI. Prompt đầu tiên gửi cho Codex

Sao chép nguyên khối sau:

```text
Bạn đang làm việc trực tiếp trong repository CAD_CAM_PROJECT.

Hãy đọc file AGENTS.md ở thư mục gốc và tuân thủ toàn bộ quy tắc trong đó.

BỐI CẢNH SẢN PHẨM:
Chúng ta đang xây dựng HMS CAD/CAM cho Windows 10/11 64-bit.
Phần mềm sẽ đọc và chuyển đổi CAD 2D/3D, quản lý dự án bằng thư mục
có đuôi .HMS, sau này có CAM phay/tiện, mô phỏng, Post Processor,
Setup Sheet và bộ cài Windows.

MÔI TRƯỜNG:
- VS Code
- Python 3.14.6 64-bit
- Giao diện dự kiến: PySide6
- CAD kernel dự kiến: Open CASCADE
- Database: SQLite
- File cấu hình: JSON UTF-8
- Test: pytest

NHIỆM VỤ HIỆN TẠI — CHỈ KHẢO SÁT, CHƯA SỬA CODE:

1. Đọc toàn bộ cây thư mục repository.
2. Đọc các file Python, cấu hình và tài liệu quan trọng.
3. Phân loại:
   - Mã nguồn đang dùng.
   - Mã thử nghiệm.
   - Công cụ hỗ trợ.
   - Tài liệu.
   - File trùng hoặc không còn dùng.
4. Phát hiện:
   - Import hỏng.
   - Phụ thuộc thiếu.
   - File quá lớn.
   - Logic trùng lặp.
   - Đường dẫn gắn cứng.
   - Nguy cơ không tương thích Python 3.14.
5. Đề xuất cấu trúc module cho giai đoạn:
   - Khung PySide6.
   - Dự án .HMS.
   - CAD Viewer.
6. Cho biết mã hiện có nào có thể tái sử dụng.
7. Lập kế hoạch di chuyển mà không làm mất code.
8. Không tạo, xóa hoặc sửa bất kỳ file nào ở bước này.
9. Không bắt đầu CAM.
10. Trình bày báo cáo rõ ràng và chờ tôi xác nhận.
```

---

## VII. Prompt Giai đoạn 1 — Khung giao diện

Chỉ gửi sau khi đã xác nhận báo cáo Giai đoạn 0:

```text
Hãy đọc AGENTS.md và kế hoạch đã thống nhất.

NHIỆM VỤ:
Triển khai Giai đoạn 1 — bộ khung ứng dụng HMS CAD/CAM bằng PySide6.

YÊU CẦU:
1. Chương trình chạy bằng:
   python main.py
2. Tạo Main Window.
3. Menu:
   File, Edit, View, CAD, CAM, Machine, Toolpath, Setup, Help.
4. Toolbar cơ bản.
5. Project Manager dock bên trái.
6. CAD Viewport placeholder ở giữa.
7. Properties dock bên phải.
8. Output/Log dock phía dưới.
9. Status Bar.
10. Logging ra console và file.
11. Xử lý lỗi khởi động.
12. Không tích hợp Open CASCADE.
13. Không triển khai CAM.
14. Không tạo các nút giả tuyên bố tính năng đã hoạt động.
15. Cập nhật README và dependency.
16. Thêm smoke test phù hợp.

QUY TRÌNH:
- Khảo sát các file liên quan.
- Nêu kế hoạch ngắn.
- Thực hiện.
- Chạy kiểm tra import, test và chạy thử khởi động.
- Báo cáo từng file đã tạo/sửa cùng kết quả.
- Không tự làm Giai đoạn 2.
```

---

## VIII. Prompt Giai đoạn 2 — Dự án `.HMS`

```text
Hãy đọc AGENTS.md.

NHIỆM VỤ:
Triển khai hệ thống dự án dạng thư mục `.HMS`.

PHẠM VI:
- Tạo project model, manifest, creator, loader và saver.
- Tích hợp New/Open/Save/Save As/Close vào giao diện.
- Chưa tích hợp CAD kernel.
- Chưa triển khai CAM.

HÀNH VI:
1. Người dùng chọn file CAD nguồn.
2. Hộp thoại lưu mặc định mở tại thư mục chứa file nguồn.
3. Tên gợi ý lấy từ stem của file nguồn và thêm `.HMS`.
4. Tạo đúng một thư mục `TEN_DU_AN.HMS`.
5. Tạo `project.hms.json`, `project.db` và các thư mục con chuẩn.
6. Sao chép file CAD gốc vào `source/`.
7. Không sửa file gốc.
8. Không ghi đè dự án cũ nếu chưa xác nhận.
9. Có validation manifest.
10. Có recent projects.
11. UI không được tự thao tác database.
12. Có unit test và integration test cho tạo/mở/lưu dự án.

TRƯỜNG HỢP PHẢI TEST:
- Tên có dấu tiếng Việt.
- Tên có khoảng trắng.
- File nguồn không tồn tại.
- Thư mục đích không có quyền ghi.
- Dự án đã tồn tại.
- Manifest thiếu hoặc sai version.
- Không tạo `.HMS.HMS`.

Sau khi hoàn thành, chạy test và báo cáo đầy đủ.
Không tự chuyển sang CAD Viewer.
```

---

## IX. Prompt kiểm tra sau mỗi giai đoạn

```text
Hãy đóng vai trò người review độc lập.

Không thêm tính năng mới.

1. Đọc AGENTS.md.
2. Xem toàn bộ diff kể từ checkpoint Git gần nhất.
3. Kiểm tra:
   - Kiến trúc.
   - Lỗi runtime.
   - Import vòng.
   - Threading/UI freeze.
   - Xử lý đường dẫn Windows.
   - Encoding UTF-8.
   - Rò rỉ tài nguyên.
   - Xử lý lỗi.
   - Test còn thiếu.
   - Code trùng lặp.
   - Tính tương thích Python 3.14.
4. Chạy test hiện có.
5. Chỉ sửa lỗi thật sự phát hiện được.
6. Không refactor ngoài phạm vi nếu không cần thiết.
7. Báo cáo:
   - Lỗi đã tìm thấy.
   - Mức nghiêm trọng.
   - File đã sửa.
   - Test đã chạy.
   - Hạn chế còn lại.
```

---

## X. Prompt khi Codex sửa quá nhiều hoặc sai hướng

```text
Dừng triển khai tính năng mới.

Hãy so sánh thay đổi hiện tại với yêu cầu gần nhất và AGENTS.md.

1. Liệt kê mọi file đã sửa ngoài phạm vi.
2. Giải thích vì sao từng file bị sửa.
3. Không xóa hoặc hoàn tác gì ngay.
4. Đề xuất kế hoạch đưa repository về phạm vi đúng.
5. Giữ lại những thay đổi cần thiết.
6. Chờ tôi xác nhận trước khi hoàn tác hoặc xóa code.
```

---

## XI. Cách quản lý checkpoint

Trước mỗi giai đoạn:

```powershell
git status
git add .
git commit -m "checkpoint truoc giai doan X"
```

Sau khi Codex hoàn thành và test đạt:

```powershell
git status
git diff
git add .
git commit -m "hoan thanh giai doan X"
```

Không để Codex thực hiện nhiều giai đoạn trong cùng một commit.

---

## XII. Nguyên tắc quan trọng nhất

- Làm nền tảng đúng trước, tính năng nhiều sau.
- Không làm CAD Viewer và CAM cùng lúc.
- Không để UI phụ thuộc trực tiếp database hoặc CAD kernel.
- Không biến `.HMS` thành file nén ở bản đầu.
- Không dùng STL làm dữ liệu hình học chính xác.
- Không gắn G-code Fanuc trực tiếp vào thuật toán toolpath.
- Không báo “hoàn thành” nếu chỉ có giao diện giả.
- Luôn có test, checkpoint và báo cáo thay đổi.
- Mỗi lần chỉ giao một nhiệm vụ có tiêu chí nghiệm thu rõ ràng.
