# Kiến trúc UI/UX mục tiêu - Stage 9A.1

## 1. Quyết định kiến trúc

HMS dùng một workspace CAD/CAM riêng, lấy continuity của workflow làm nguyên
tắc nhưng không sao chép giao diện sản phẩm tham khảo:

```text
Contextual ribbon / workspace selector
┌──────────────────┬────────────────────────────┬──────────────────────┐
│ Operation Manager│ Central graphics viewport  │ Function Editor      │
│ (trái)           │ (luôn giữ diện tích chính) │ (phải)               │
├──────────────────┴────────────────────────────┴──────────────────────┤
│ Status / diagnostics / background tasks (dưới, đóng/mở được)        │
└──────────────────────────────────────────────────────────────────────┘
Optional secondary panel: Simulation, Post/NC, Tool Library hoặc Help.
```

Operation Manager tham khảo workflow Mastercam; Function Editor tham khảo cách
chia section của WorkNC. Màu, icon, asset, wording, component và interaction là
thiết kế riêng của HMS.

## 2. Audit giao diện HMS hiện tại

Audit ngày 21-07-2026 chạy `MainWindow` với PySide6 6.11.1, OCP 7.9.3.1.1 và
một dự án `.HMS` tạm có BREP thật. Chín ảnh chụp nằm tại
`reference_private/DERIVED/UI_REFERENCE/HMS_CURRENT/`; contact sheet nằm tại
`reference_private/DERIVED/UI_REFERENCE/CONTACT_SHEETS/hms_current_contact_sheet.png`.

### 2.1 Bằng chứng định lượng

- `CamWorkspace` dùng một `QSplitter` ngang chứa đồng thời operation tree,
  `_CamPropertiesEditor`, Simulation và Post/Program Assembly.
- Workspace khi hiển thị đủ tự giãn tới khoảng **1673 x 3560 px**; editor
  Contour riêng cao khoảng **3530 px**.
- `_CamPropertiesEditor` tạo cùng lúc 12 field Facing, 11 field Contour, 12
  field Pocket, 9 field Drilling, 9 field Tapping, 10 field Reaming và 10 field
  Boring, chưa kể Setup/WCS/resource và combo phụ.
- Không có `QScrollArea`, không có `setTabOrder`, không có accessible name/help
  cho form CAM; chỉ có vài tooltip ở Post và command chưa khả dụng.
- `MainWindow` cho phép tối thiểu 1024 x 680 nhưng CAM content không reflow theo
  kích thước này. Ribbon cố định cao 132 px.
- Contrast text chính đạt tốt; text disabled `#9ca6b1` trên trắng khoảng 2.47:1
  và error `#d9534f` trên nền `#f4f5f7` khoảng 3.63:1, cần kiểm tra lại khi
  redesign.

### 2.2 Vấn đề theo tiêu chí audit

| Tiêu chí | Hiện trạng | Hệ quả |
|---|---|---|
| Mật độ thông tin | Bốn workflow lớn cùng hiện trong một splitter; editor chứa field của mọi strategy. | Viewport bị thu hẹp, người dùng phải đọc nhiều thông tin không liên quan. |
| Trường lặp | Tool/Machine, top/depth, clearance/retract, feed/spindle xuất hiện lặp theo strategy. | Khó biết giá trị thuộc operation nào và giá trị nào kế thừa Setup/Tool. |
| Field không cần | Chọn Contour vẫn thấy Facing, Pocket, Drilling, Tapping, Reaming và Boring. | Tab traversal dài, dễ nhập nhầm, kích thước dọc cực lớn. |
| Chiều rộng panel | CAM dock có size hint lớn; dock tabified làm cả Project Manager nở rộng. | Central viewport không còn là bề mặt chính. |
| Scroll | Form không có scroll container và không có section collapse. | Nội dung bị kéo dài hơn ba màn hình 1080p. |
| Tìm section | Form phẳng, nhãn Việt/Anh trộn lẫn, không có navigator. | Không có mental model Geometry/Tool/Cutting/Levels/Linking. |
| Action | Toolbar liệt kê Create resources cho từng loại dao và mọi Add operation. | Action discovery kém; toolbar quá dài, có dấu `...` và không theo selection. |
| Trạng thái | `MISSING`, `PROFILE MISSING`, fingerprint và status kỹ thuật xuất hiện dày. | Operator khó phân biệt việc phải làm tiếp theo với diagnostics dành cho developer. |
| Enabled/disabled | Nút bị khóa đúng ở nhiều nơi nhưng phần lớn không cho biết điều kiện thiếu. | Người dùng thấy action mờ nhưng không biết cách mở khóa. |
| Draft/Apply | Editor CAM có một nút Apply ở cuối form; Simulation, Post và Assembly dùng workflow draft khác nhau. | Pattern không nhất quán; lỗi xa field và dễ bỏ sót mutation chưa apply. |
| Lỗi/diagnostics | Một error label cuối editor; Post/Simulation/Assembly luôn dành vùng bảng lớn dù rỗng. | Lỗi không inline; khoảng trắng chiếm diện tích và làm action quan trọng trôi xa. |
| Keyboard/tab | Dựa hoàn toàn vào thứ tự tạo widget; không bỏ qua field không áp dụng. | Phím Tab đi qua hàng chục control không liên quan. |
| Resize/DPI | Không có breakpoint hoặc drawer; content phụ thuộc size hint. | 125-150% DPI và 1366 x 768 có nguy cơ cắt/clamp nội dung. |
| Contrast | Text chính tốt; disabled/error nhỏ có contrast thấp hơn mục tiêu 4.5:1. | Trạng thái khó đọc với operator thị lực yếu hoặc màn hình công nghiệp. |
| Terminology | Việt/Anh trộn (`Tên`, `Tool Assembly`, `Contour stepdown`, `Generate Post`). | Học thao tác chậm và khó viết help nhất quán. |

CAM 3D Foundation 8A.1 hiện chỉ có domain/service/persistence và manual smoke
phi GUI; chưa có Function Editor CAM 3D để chụp. Audit không dựng UI giả và
không coi CAM 3D UI là đã hoàn thành.

### 2.3 Điểm đang làm tốt cần giữ

- Main shell đã có menu, ribbon, dock, viewport, output và status bar.
- Operation identity, Program Assembly ordering và selection dùng domain ID,
  không phụ thuộc row index.
- Draft invalid không mutation domain ở các workflow đã có test.
- Simulation/Post/Assembly có trạng thái lifecycle, diagnostics và background
  work; redesign chỉ đổi presentation, không làm yếu các guard này.
- Project/CAD I/O đi qua controller/service, không truy cập SQLite trực tiếp từ UI.

## 3. Bố cục mục tiêu

### 3.1 Contextual ribbon

- Hàng đầu chọn workspace: **Design**, **CAM**, **Simulation**, **NC**.
- Nhóm lệnh đổi theo selection: Setup, Geometry, Toolpath, Simulation hoặc NC.
- Quick access chỉ giữ New/Open/Save, Undo/Redo khi thực sự khả dụng.
- Không lặp cùng command ở menu, toolbar CAD và ribbon nếu không có lý do
  keyboard/accessibility rõ.

### 3.2 Operation Manager bên trái

```text
Project
  Job
    Setup / Machine Group
      Stock
      Tool Library
      Operations
        Operation
          Geometry
          Tool
          Toolpath
          Simulation
          Post
          NC Artifact
      Program Assembly
```

- Identity là `ProjectId`, `CamJobId`, `SetupId`, `CamNodeId`, `OperationId`
  hoặc artifact ID; tuyệt đối không dùng row index.
- Cột mặc định chỉ có tên và semantic status. Dữ liệu kỹ thuật mở trong
  Properties/Diagnostics, không nhồi vào label.
- Status dùng cả text + shape/icon: DRAFT, NEEDS_INPUT, READY, CALCULATING,
  CURRENT, STALE, WARNING, BLOCKED, FAILED.
- Context menu và ribbon action đều nhận cùng command model; tree không chứa
  business logic.

### 3.3 Central viewport

- Luôn là vùng lớn nhất; panel không được ép viewport xuống dưới 480 x 360 ở
  cấu hình tối thiểu.
- Geometry pick, machining zone, preview, toolpath và issue focus thể hiện tại
  đây; editor chỉ giữ summary selection.
- Selection đổi qua domain ID; highlight tạm không được mutation CAD nguồn.

### 3.4 Function Editor bên phải

- Một editor cho đúng object/operation đang chọn, không chứa field của strategy
  khác.
- Cấu trúc bắt buộc theo `UI_FUNCTION_EDITOR_SPEC_9A1.md`.
- Editor có scroll nội bộ; header và footer action vẫn nhìn thấy.
- Chiều rộng mục tiêu 360-460 px; label/control có thể xếp dọc ở dưới 400 px.

### 3.5 Status/diagnostics bên dưới

- Mặc định chỉ là một thanh 1-2 dòng: task, progress, warning/error count.
- Bảng diagnostics mở theo yêu cầu hoặc tự mở khi action thất bại.
- Empty table không chiếm hàng trăm pixel.
- Technical evidence/fingerprint có thể copy nhưng nằm trong Details.

### 3.6 Secondary panel

- Simulation, Post/NC, Tool Library và Help là secondary panel đóng/mở được.
- Chỉ một secondary panel mở mặc định. Mở panel không làm mất selection.
- Simulation/Post/NC vẫn là node trong Operation Manager để giữ workflow, nhưng
  nội dung chi tiết không hiển thị thường trực cạnh editor.

## 4. Responsive và DPI

| Không gian khả dụng | Hành vi |
|---|---|
| >= 1600 px | Manager 280-340 px, viewport co giãn, editor 400-460 px; secondary panel tùy chọn. |
| 1280-1599 px | Manager 250-300 px, editor 360-400 px; diagnostics collapsed; secondary panel dạng tab thay editor. |
| 1024-1279 px | Chỉ một trong Manager/Editor mở rộng; panel còn lại là drawer/tab; viewport vẫn >= 480 px. |
| 125-200% DPI | Không dùng fixed content height; text wrap, control min-height theo style, scroll ở editor/panel chứ không ở toàn cửa sổ. |

Mỗi breakpoint phải được chụp ở 100%, 125% và 150% DPI trong screenshot
regression. Không dùng việc thu nhỏ font để giải quyết overflow.

## 5. State và luồng người dùng

```text
Selection bằng domain ID
  -> tạo draft riêng cho selection
  -> validate inline, không mutation
  -> preview/calculation request immutable
  -> stale/current guard
  -> Apply atomic vào application service
  -> refresh tree/status/artifact theo ID
```

- Đổi selection phải discard hoặc hỏi xử lý draft dirty theo policy đã công bố;
  không phục hồi draft của operation cũ vào operation mới.
- Project switch/Open/Close xóa transient selection, draft và worker callback
  theo generation guard.
- Giá trị kế thừa ghi rõ nguồn: Setup, Stock, Tool, Machine, project unit hoặc
  operation override.
- UI không đọc/ghi trực tiếp `project.db` và không tự Generate/Export.

## 6. Ranh giới trách nhiệm

| Thành phần | Trách nhiệm | Không được làm |
|---|---|---|
| Widget/View | Render state, thu input, phát intent. | Truy cập SQLite, sửa aggregate, chạy CAM nặng. |
| UI presenter/view-model | Project state thành section/field/action state. | Chứa CAD/OCP native handle lâu dài. |
| Application service | Validate command, transaction, generation/stale guard. | Phụ thuộc widget hoặc row index. |
| Worker | Import, tessellate, calculate, simulate, export lớn. | Mutation UI hoặc publish stale result. |
| Domain/infrastructure | Contract hiện có, persistence, CAD adapter. | Bị refactor đồng thời trong migration UI 9A.x. |

## 7. Tiêu chí chấp nhận cho redesign

- Chọn một Contour không hiển thị field Pocket/Drilling/Tapping/... .
- Basic của mỗi operation có khoảng 5-10 input cốt lõi.
- Viewport còn hữu dụng ở 1366 x 768 và 150% DPI.
- Tất cả action disabled có lý do truy cập được bằng text/tooltip/diagnostic.
- Tab order chỉ đi qua field đang hiện và action hợp lệ.
- Invalid draft không mutation; project/selection switch không rò draft.
- Screenshot regression, GUI smoke và test lifecycle cũ đều đạt.
- Không dùng asset, icon hoặc layout pixel-by-pixel của Mastercam/WorkNC.
