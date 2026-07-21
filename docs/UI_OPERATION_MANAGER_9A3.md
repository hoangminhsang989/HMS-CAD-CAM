# Operation Manager - Stage 9A.3

## Phạm vi đã triển khai

Stage 9A.3 thay cây CAM dùng trong giao diện production bằng một Operation
Manager có model phân cấp, nhưng không đổi CAD/CAM domain, schema dự án,
Toolpath IR, thuật toán, Post Processor hay Program Assembly. `CamWorkspace`
vẫn là coordinator và các action hiện có vẫn là ranh giới ghi dữ liệu.

Stage này chỉ trình bày trạng thái thật. Mở node hoặc panel không tự Calculate,
Run Simulation, Generate Post, Save hay Export. Các thao tác đó vẫn cần lệnh rõ
ràng của người dùng và tiếp tục dùng application service hiện có.

## Cấu trúc cây

Projection được dựng từ một snapshot bất biến theo thứ tự:

```text
Project
└── Job
    └── Setup
        ├── Geometry
        ├── Stock
        ├── Tools
        │   └── Tool
        ├── Operations
        │   ├── Group
        │   └── Operation
        │       ├── Geometry
        │       ├── Tool
        │       ├── Toolpath
        │       ├── Simulation
        │       ├── Post Result
        │       └── NC Artifact
        └── Program Assembly
```

Chỉ node có dữ liệu domain tương ứng mới được tạo. Dự án CAD-only không bị gắn
Job/Setup giả; trạng thái rỗng đưa người dùng tới action tạo CAM Job. Tool được
chiếu theo phạm vi Setup. Thứ tự operation/group lấy từ operation tree hiện có,
không được sắp xếp lại theo tên hay trạng thái.

## Contract của node và model

`OperationManagerNode` là dataclass thuần Python, không giữ `QObject`, widget,
`QModelIndex`, callback, OCP handle hoặc mutable UI state. Mỗi node có:

- `node_id` ổn định, typed và tái tạo được từ project/domain identity;
- `kind`, `domain_identity`, `parent_id`, `children` và `order`;
- nhãn chính, tóm tắt phụ, từ khóa tìm kiếm và trạng thái semantic;
- capability và legacy selection dùng để nối action hiện có.

`OperationManagerModel` kế thừa `QAbstractItemModel`, có hai cột tên/trạng thái
và không dùng row widget. Drag/drop và chỉnh sửa inline bị tắt vì domain command
cho reorder/rename chưa tồn tại. Projection builder không ghi project và không
giữ UI state; rollback có thể thay `OperationManagerHost` về cây coordinator cũ
mà không cần migrate dữ liệu.

## Trạng thái và tóm tắt operation

Mỗi operation hợp nhất sáu nhóm trạng thái thật:

| Nhóm | Nguồn | Ví dụ hiển thị |
|---|---|---|
| Domain | `operation.enabled` | ENABLED / DISABLED |
| Calculation | `ArtifactStatus` | MISSING / DIRTY / CURRENT / FAILED |
| Simulation | simulation registry | NOT RUN / CURRENT / STALE / FAILED |
| Post | post registry | NOT GENERATED / CURRENT / STALE / FAILED |
| NC | managed NC registry | MISSING / CURRENT / STALE / FAILED |
| Export | external export record | NEVER EXPORTED / EXPORTED / STALE |

Hình tròn có dấu kiểm biểu thị current/ready, tam giác biểu thị stale/warning,
dấu nhân biểu thị failed và hình vuông biểu thị missing/disabled. Màu chỉ là
kênh phụ: pill luôn có chữ, tooltip mô tả nguồn và accessible text đọc được.

Dòng operation dùng hai cấp thông tin: tên ở dòng đầu; strategy, tool và trạng
thái Toolpath/Simulation/NC cô đọng ở dòng hai. Không suy diễn `CURRENT` khi
registry hoặc fingerprint chưa chứng minh được trạng thái đó.

## Search, filter và trạng thái rỗng

Search không phân biệt hoa thường và tra theo tên, strategy, tool, status cùng
typed domain ID. Kết quả luôn giữ toàn bộ chuỗi node cha để không mất ngữ cảnh.
Filter hỗ trợ Tất cả, Cần tính, Lỗi, Cũ và Tắt; search và filter có thể kết hợp.

Các trạng thái không có project, CAD-only, Setup chưa có operation và không có
kết quả lọc đều có thông báo cùng action phục hồi phù hợp. Header hiển thị tên
project, số Job/Setup/Operation và tổng quan trạng thái từ chính projection.

## Selection, action và vòng đời

Selection được nối bằng typed domain identity sang `CamWorkspace`, Properties,
Simulation, Post và Program Assembly. Node con của operation chọn đúng operation
chủ; Enter mở editor/panel phù hợp nhưng không chạy tác vụ. Delete luôn xác nhận,
và sau khi xóa chọn node hợp lệ gần nhất theo identity thay vì giữ index cũ.

Toolbar và menu chuột phải được giới hạn theo loại node. Action chưa có domain
command an toàn, như Duplicate, ở trạng thái disabled kèm lý do. Các action hợp
lệ luôn tra lại node hiện tại khi trigger, không cache `QModelIndex`. Thay đổi
Post/NC được gom trong event loop rồi rebuild projection để tránh refresh lặp.
Menu operation có Calculate, Simulation, Generate Post và Add to Program
Assembly rõ ràng; menu Toolpath/Simulation/Post/NC chỉ giữ lệnh đúng loại node.
Generate/Add/Export là thao tác explicit và gọi control/service hiện có. Clear
Toolpath hiển thị disabled vì application service chưa có command xóa an toàn.

Keyboard hỗ trợ Enter, Delete, phím Menu và Shift+F10. Focus, accessible name,
accessible description và disabled reason được khai báo cho control chính.

## UI state và hiệu năng

Expansion/selection được lưu theo project ID trong nhóm QSettings
`operation_manager_9a3`, có `version = 1`. Dữ liệu này chỉ là preference người
dùng, không ghi vào `.HMS`, không thay fingerprint và không làm project dirty.
Project switch khôi phục riêng từng cây; node mất do xóa được bỏ an toàn.

Benchmark projection 7 lần trên môi trường phát triển:

| Operations | Tổng node | Median | Max |
|---:|---:|---:|---:|
| 10 | 79 | 3.54 ms | 5.09 ms |
| 50 | 359 | 16.72 ms | 18.23 ms |
| 200 | 1,409 | 74.34 ms | 100.65 ms |

Operation Manager dùng dải rộng `260-340 px`; smoke ở `1366 x 768` giữ viewport
trung tâm sử dụng được. Model không tạo widget theo dòng và rebuild tuyến tính
theo số node trong snapshot.

## Kiểm thử, ảnh regression và giới hạn

Kiểm thử tự động ở `tests/unit/test_operation_manager_9a3.py` bao phủ hierarchy,
ID ổn định, sáu nhóm status, summary, search/filter, selection, action, keyboard,
persistence, delete fallback, không drag/drop/row-widget và tải 10/50/200
operation. Manual harness là `tests/manual_stage9a3_operation_manager.py`.

16 ảnh runtime được Git ignore tại
`reference_private/DERIVED/UI_STAGE_9A3/`: no-project, CAD-only, empty Setup,
7 operation/mixed status, operation expanded, search, stale/disabled/warning/error
filter, context menu, 1366x768, 1920x1080, panel collapsed/restored và project
switch. Qt offscreen trên máy kiểm thử không có phông tiếng Việt nên glyph trong
ảnh thành ô vuông; kích thước, màu, shape và trạng thái vẫn được kiểm tra, còn
production Windows dùng font hệ thống bình thường.

Chưa làm trong 9A.3: Unified Function Editor 9A.4, reorder bằng drag/drop,
Duplicate operation, CAM strategy/algorithm mới hoặc thay đổi project schema.
