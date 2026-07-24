# Stage 8A.4.1 — Nền tảng cấu hình Tool theo chương trình

## Trạng thái

Stage 8A.4.1 đã **COMPLETED** sau khi package GUI được người dùng duyệt. Tài
liệu này mô tả contract đã triển khai; Stage 8A.4.2 chưa được bắt đầu.

## Mục tiêu và ranh giới

Mỗi `ToolDefinition` có thể giữ hai lớp cấu hình:

- `ToolCommonDefaults`: các giá trị dùng chung như tốc độ trục chính, lượng
  chạy dao, tưới nguội và lựa chọn chất lượng.
- `ToolProgramProfile`: cấu hình thưa, tùy chọn, gắn với đúng một
  `strategy_id`.

Tool không có profile vẫn hợp lệ, lưu/mở được và tiếp tục dùng chính sách tự
động của strategy. Stage này không triển khai quy trình Chọn chương trình →
Chọn Tool → Tính toán hoàn chỉnh, chương trình mẫu, Import/Export profile,
thuật toán tiếp xúc đa họ Tool, Production Post hoặc chứng nhận machine-ready.

## Kiến trúc

Luồng phụ thuộc giữ đúng phân lớp hiện có:

```text
Function Editor / Tool editor widget
  → ProjectService
  → CamApplicationService
  → ToolDefinition + ToolProgramProfile + ToolProfileResolver
  → CamSqliteRepository
  → project.db schema v4
```

UI không đọc hoặc ghi SQLite. Mọi thay đổi cấu hình đi qua service, dùng
`expected_configuration_revision` để từ chối bản ghi đã stale.

## Data contract

### Cấu hình cơ bản

`ToolCommonDefaults` là typed immutable model. Các trường hiện có:

- `spindle_speed_rpm`;
- `cutting_feed_mm_per_min`;
- `plunge_feed_mm_per_min`;
- `coolant_mode`;
- `quality_profile`;
- `maximum_cutting_depth_mm`;
- `cutting_data_reference`.

Model chỉ chấp nhận primitive hữu hạn và enum đã khai báo. Tham số riêng của
một strategy không được đưa vào common defaults.

### Cấu hình theo chương trình

`ToolProgramProfile` có:

- `profile_id`, `tool_id`, `strategy_id`, `display_name`, `enabled`;
- `profile_schema_version`;
- tuple `ToolProfileValue` thưa, được sắp thứ tự deterministic;
- `created_at`, `updated_at`;
- revision/fingerprint nguồn của Tool và fingerprint Holder tùy chọn;
- revision riêng của profile và validation state;
- fingerprint tính toán không chứa tên hiển thị hoặc timestamp.

Profile chỉ được tạo khi người dùng chủ động thêm, sao chép hoặc xác nhận lưu
từ nguyên công. Không tự sinh profile rỗng khi tạo Tool.

### Registry strategy

Registry hiện có ba schema thật:

| Strategy | Họ Tool được khai báo | Trường phân biệt |
| --- | --- | --- |
| Gia công tinh theo cao độ Z | Ball-end | Bước xuống, tiếp cận/rút dao |
| Gia công tinh song song | Ball-end | Bước ngang, góc chạy, thứ tự cắt |
| Khoan | Drill, Center-drill | Chiều sâu phá phoi, dừng đáy, mức rút |

Mỗi descriptor khai báo kiểu, đơn vị, range/enum, nhãn enum tiếng Việt,
classification ảnh hưởng tính toán/an toàn, liên kết common default, cờ manual
override và trạng thái Basic/Nâng cao. Unknown strategy, unknown field, sai
kiểu, sai range hoặc sai schema version đều fail rõ ràng.

Kiến trúc không hard-code Ball-end. Registry có compatibility theo Tool family;
End-mill, Bull-nose và Profile/Form Tool được giữ trong model nhưng không được
tuyên bố là đã được các thuật toán hiện tại hỗ trợ.

## Resolution và provenance

`ToolProfileResolver` giải quyết từng trường theo đúng thứ tự:

1. Tùy chỉnh thủ công của nguyên công hiện tại.
2. Cấu hình Tool theo chương trình đang bật và tương thích.
3. Cấu hình cơ bản của Tool.
4. Chính sách tự động của strategy.
5. Giá trị an toàn mặc định mà schema cho phép.

Mỗi `EffectiveToolValue` trả về canonical value, display value tiếng Việt,
source enum, source object ID, validation status, automatic/manual mode, lý do
và contribution cho dependency fingerprint.

Profile bị tắt, stale, sai Holder/Tool revision hoặc không tương thích không
được dùng. Resolver chuyển xuống nguồn thấp hơn và ghi trạng thái fallback; nếu
không còn nguồn được schema xác nhận thì kết quả bị chặn.

Adapter Function Editor hiện phủ Z-Level và Parallel. Khi đổi Tool, contract tự
động được resolve lại bằng Tool/Holder đang chọn. Manual override của draft
luôn thắng profile và việc sửa draft không sửa Tool. Khoan có schema production
và lệnh lưu từ Function Editor; stage này không thay đổi thuật toán Khoan.

## Lưu từ nguyên công

Footer của Z-Level, Parallel và Khoan có hành động `Lưu cho Tool`. Hành động:

1. đọc strategy và Tool/Holder đang chọn;
2. chỉ lấy field được schema khai báo;
3. nhận diện field đã manual override hoặc đã đổi trong draft;
4. tạo preview gồm thêm mới, thay đổi, giữ nguyên, bỏ qua, không hợp lệ;
5. mặc định chọn `Chỉ lưu các trường đã tùy chỉnh`;
6. chỉ ghi sau khi người dùng xác nhận;
7. ghi qua `ProjectService`/`CamApplicationService`;
8. không tự Calculate.

Lựa chọn thứ hai lưu toàn bộ giá trị hiệu lực được schema cho phép. Preview có
giá trị enum tiếng Việt và chặn xác nhận khi có entry không hợp lệ.

## Persistence và tương thích ngược

Không bump SQLite. `CamSqliteRepository` đã lưu payload typed của
`ToolDefinition`, vì vậy profile nằm trong payload Tool hiện có:

- Tool không có common defaults/profile/config revision tiếp tục serialize
  đúng payload Tool **v1** cũ.
- Tool có cấu hình serialize thành Tool payload **v2**.
- SQLite vẫn schema **v4**; không có migration hay downgrade.
- Tool/project schema v4 cũ mở lại được và dữ liệu vật lý giữ nguyên.
- round-trip, Save/Open và Autosave dùng cùng typed serializer.

`content_fingerprint` của Tool tiếp tục chỉ mô tả hình học/vật lý nên assembly
và contract cũ không đổi. `configuration_fingerprint` riêng chứa common
defaults và fingerprint profile. Serialization strict; unknown future field
hoặc version không hỗ trợ bị từ chối thay vì âm thầm làm mất dữ liệu.

Sao chép Tool tạo Tool ID và profile ID mới; profile sao chép mặc định bị tắt để
tránh hai cấu hình cùng strategy được chọn ngầm.

## Stale và safety

Profile không chứa READY/SAFE, safety hash, artifact, Toolpath, kết quả
Simulation, G-code hoặc machine-ready state.

Thay đổi giá trị/enabled/reset/delete có làm đổi effective configuration sẽ:

- đổi configuration/dependency fingerprint;
- đánh dấu `DIRTY` chỉ operation dùng Tool và strategy tương ứng;
- stale Simulation/Post runtime của operation bị ảnh hưởng;
- giữ Post fail-closed cho đến khi Calculate và các gate hiện có đạt lại.

Đổi tên hiển thị, timestamp, profile revision không làm đổi calculation
fingerprint. `ProjectService` so sánh operation trước/sau và không stale toàn bộ
Simulation chỉ vì metadata trình bày thay đổi.

## UI và review

`ToolEditorDialog` giữ phần cơ bản ở trước và vùng `Cấu hình theo chương trình ·
Không bắt buộc` có thể thu gọn. Danh sách hiển thị chương trình, trạng thái, số
trường và thời điểm cập nhật; action mutation được phát ra ngoài widget để
service xử lý.

`ToolProfileEditorDialog` sinh field trực tiếp từ schema, Basic trước, Nâng cao
có scroll dọc, không scroll ngang và footer cố định. `ToolProfileSavePreviewDialog`
và `ToolProfileProvenanceWidget` dùng cùng typed schema/resolver.

Review package Git-ignored:

`reference_private/DERIVED/UI_STAGE_8A4_1_TOOL_PROGRAM_PROFILES/`

Package có đúng 16 PNG, 7 JSON và 1 Markdown. Harness dùng production
model/repository/widget, assert model trước mỗi ảnh, kiểm tra 16 SHA-256 riêng,
localization/accessibility, bounds và DPI 100/125/150%. Package đã được người
dùng duyệt với QPA Windows, Segoe UI 9 pt, missing/replacement/tofu bằng
0/0/0 và không crop, overlap hay horizontal scroll.

## Giới hạn còn lại

- Stage 8A.4.2 chưa được bắt đầu.
- Chưa có chương trình mẫu hay wizard ba bước.
- Chưa Import/Export profile.
- Chưa mở rộng contact-point hoặc thuật toán CAM cho nhiều họ Tool.
- Chưa thêm Production Post/G-code mới.
- Không có chứng nhận machine-ready hoặc xác nhận trên máy CNC thật.
