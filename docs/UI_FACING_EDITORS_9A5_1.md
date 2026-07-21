# Stage 9A.5.1 — Production Function Editors cho Facing

## Phạm vi và kết luận audit

Stage này chuyển hai cách dùng của strategy `facing_2_5d` sang Function Editor production:

- Facing theo mặt trên `Stock BOX`;
- Planar Face Facing theo đúng một persistent `FACE`.

Hai editor có schema/presentation key riêng nhưng dùng chung `FacingParameters`, codec, generator, ProjectService và SQLite v4 hiện có. Audit không phát hiện nhu cầu thay public domain contract, codec key, fingerprint hay schema database. Vì vậy Stage 9A.5.1 chỉ thêm presentation binding và integration; Contour cùng các strategy khác tiếp tục dùng Legacy Editor Adapter.

Các ảnh tham khảo đã mở chọn lọc là Cutter Details p0562, Standard Parameter Groups p0545, Tolerances p0586, Safe Movements p0590, Parallel-specific Additional Parameters p0765 và HMS legacy `03_cam_workspace_default_dock.png`. Chúng chỉ định hướng grouping, summary, safe-plane và progressive disclosure; field và semantic luôn lấy từ repository.

## Domain field audit

`mm` dưới đây là đơn vị theo Setup (`mm` hoặc `inch`); feed tương ứng là `mm/min` hoặc `in/min`. Mọi field của `FacingParameters` tham gia `OperationParameterSet.fingerprint`, trừ các presentation-only field được ghi rõ ở bảng sau.

| Domain field | Type / required | Default khi tạo operation | Source | Validation hiện có | Dependency và generator usage | Codec key | Legacy widget | Facing | Planar Face Facing |
|---|---|---|---|---|---|---|---|---|---|
| `unit` | `LengthUnit`, bắt buộc | Setup unit | Setup | không `UNKNOWN`; khớp stock/tool/machine | quyết định toàn bộ quantity/feed conversion | `unit` | suy ra từ project | INTERNAL/DERIVED | INTERNAL/DERIVED |
| `boundary_source` | enum, bắt buộc | `stock_box` | operation type | enum hợp lệ | chọn Stock region hoặc resolved planar FACE | `boundary_source` | `boundary_source` combo | INTERNAL/DERIVED = `stock_box` | INTERNAL/DERIVED = `planar_face` |
| `top_height` | `Length`, bắt buộc | stock top | operation; recommendation từ Stock | hữu hạn; generator yêu cầu bằng stock top | top của các lớp cắt | `top_height` | `_facing_fields["top"]` | LEVELS / Basic | LEVELS / Basic |
| `target_height` | `Length`, bắt buộc | stock top − 1 unit step | operation hoặc explicit FACE selection | `target + allowance < top`; planar khớp plane đã resolve | final cutting plane | `target_height` | `_facing_fields["target"]` | LEVELS / Basic, editable | LEVELS / Basic, read-only từ Geometry |
| `stepdown` | `Length`, bắt buộc | 1 mm-equivalent | operation; recommendation HMS | `> 0` | chia nhiều lớp Z | `stepdown` | `_facing_fields["stepdown"]` | LEVELS / Basic | LEVELS / Basic |
| `stepover` | `Length`, bắt buộc | 5 mm-equivalent | operation; recommendation HMS | `> 0`; generator yêu cầu không lớn hơn đường kính dao | khoảng cách raster lane | `stepover` | `_facing_fields["stepover"]` | CUTTING / Basic | CUTTING / Basic |
| `stock_allowance` | `Length`, bắt buộc | `0` | operation; recommendation HMS | `>= 0`; target cộng allowance vẫn dưới top | nâng final cutting Z | `stock_allowance` | `_facing_fields["allowance"]` | LEVELS / Basic | LEVELS / Basic |
| `clearance_height` | `Length`, bắt buộc | stock top + 5 mm-equivalent | operation; recommendation HMS | `>= retract` | safe plane đầu/cuối | `clearance_height` | `_facing_fields["clearance"]` | LINKING / Advanced | LINKING / Advanced |
| `retract_height` | `Length`, bắt buộc | stock top + 2 mm-equivalent | operation; recommendation HMS | `> top` | retract giữa pass | `retract_height` | `_facing_fields["retract"]` | LINKING / Advanced | LINKING / Advanced |
| `feed_rate` | `FeedRate`, bắt buộc | 500 mm/min-equivalent | operation; không auto đổi theo tool | `> 0`, unit đúng; giới hạn machine | cutting moves | `feed_rate` | `_facing_fields["feed"]` | CUTTING / Basic | CUTTING / Basic |
| `plunge_feed_rate` | `FeedRate`, bắt buộc | 100 mm/min-equivalent | operation; không auto đổi theo tool | `> 0`, unit đúng; giới hạn machine | approach/retract feed | `plunge_feed_rate` | `_facing_fields["plunge"]` | ADVANCED | ADVANCED |
| `spindle_speed` | `SpindleSpeed`, bắt buộc | 1000 RPM | operation; không auto đổi theo tool | `> 0`; nằm trong spindle machine | spindle event | `spindle_speed` | `_facing_fields["spindle"]` | CUTTING / Basic | CUTTING / Basic |
| `direction` | `FacingCutDirection`, bắt buộc | `bidirectional` | operation | enum hợp lệ | climb/conventional/bidirectional pass order | `direction` | `direction` combo | CUTTING / Basic | CUTTING / Basic |
| `raster_angle_degrees` | finite float, bắt buộc | `0` | operation; recommendation HMS | hữu hạn; domain chuẩn hóa modulo 180° | xoay raster axes | `raster_angle_degrees` | `_facing_fields["angle"]` | ADVANCED | ADVANCED |
| `overtravel` | `Length`, bắt buộc trong codec | 1 mm-equivalent | operation | `>= 0` | mở rộng Stock raster; Planar generator cố định extension 0 | `overtravel` | `_facing_fields["overtravel"]` | ADVANCED | NOT APPLICABLE; giá trị codec cũ được bảo toàn |
| `strategy_version` | int, bắt buộc | `1` | domain | chỉ hỗ trợ `1` | chọn algorithm contract | `strategy_version` | không có | INTERNAL | INTERNAL |
| `schema_version` | int, bắt buộc | `1` | domain | chỉ hỗ trợ `1` | codec compatibility | `schema_version` | không có | INTERNAL | INTERNAL |

## Operation/resource binding audit

| Stable field ID | Binding / conversion | Source/default | Validation và mutation | Legacy tương ứng | Classification |
|---|---|---|---|---|---|
| `operation_name` | `node.name` / trimmed text | project operation node | bắt buộc; rename-only không tăng operation revision và không dirty artifact | `_fields["name"]` | BASIC |
| `geometry_summary` | derived presentation string / identity | Stock hoặc Geometry | read-only; Planar có action `select_geometry`; không serialize | legacy status + viewport picker | GEOMETRY |
| `geometry_bounds` | `derived.stock_bounds` / identity | Stock | read-only; chỉ Stock editor | legacy setup/stock summary | GEOMETRY, Facing only |
| `geometry_reference_id` | `operation.geometry_inputs.boundary` / typed ID text | Geometry | read-only; đúng một FACE; giữ `GeometryInputId` khi cùng reference; không chứa OCP object | selected `GeometryReference` | GEOMETRY / Advanced, Planar only |
| `tool_assembly_id` | `operation.tool_assembly` / stable ID | project Tool Library | ID phải còn tồn tại; generator kiểm tra family, unit, diameter; đổi chỉ vào draft đến Apply | `tool` combo | TOOL / Basic |
| `tool_details` | `derived.tool_details` | Tool Definition | read-only name/family/diameter/usable/stickout; không fingerprint riêng | legacy tool details | TOOL / Basic |
| `holder_summary` | `derived.holder_summary` | Tool Definition | read-only; missing/stale fail-closed ở downstream; không fingerprint riêng | legacy holder details | TOOL / Basic |
| `machine_id` | `operation.machine_requirement` / stable ID | project Machine | ID phải tồn tại; tạo lại exact `MachineRequirement`; generator là nguồn compatibility cuối | `machine` combo | ADVANCED |
| `enabled` | `operation.enabled` / strict boolean | operation | đổi riêng tăng revision và thêm `UPSTREAM_CHANGED`; Calculate bị chặn khi tắt | `enabled` checkbox | ADVANCED |

Tool và machine choice hiển thị tên kèm prefix ID, nhưng identity luôn là typed stable ID. Duplicate display name không ảnh hưởng selection.

## Schema và disclosure

Schema `facing_production_9a5_1` dùng strategy presentation key `facing_stock_box_9a5_1`. Schema `planar_face_facing_production_9a5_1` dùng key `planar_face_facing_9a5_1`. Thứ tự section deterministic là:

1. `basic`;
2. `geometry`;
3. `tool`;
4. `cutting`;
5. `levels`;
6. `linking`;
7. `advanced`.

Basic hiển thị tên, machining region, tool, cutting và levels đủ cho công việc thường dùng. `linking` và `advanced` thuộc disclosure Advanced và collapsed mặc định. Không tạo Expert rỗng vì Facing v1 không có tolerance/smoothing/filtering/post-specific parameter. Planar không dựng `overtravel` hay `geometry_bounds`; hidden legacy `overtravel` vẫn được round-trip nguyên giá trị theo domain policy.

Mỗi field khai báo stable ID, order, `binding_key`, conversion, unit, source, recommendation, applicability, validator, help key và reset-to-applied policy. Recommendation chỉ đi vào draft khi người dùng chọn restore defaults; Apply không âm thầm biến nguồn kế thừa thành override mới.

## Draft, selection, preview và lifecycle

- Draft chỉ chứa primitive presentation values và typed geometry ở adapter transient; sửa draft không dirty project.
- Domain/generator validation là nguồn chân lý cuối. Diagnostics được map về field/section, hiển thị inline, badge section, tổng ở header và focus lỗi đầu.
- Planar `Select` dùng picker/resolver hiện có, nhận đúng persistent FACE, kiểm tra project generation, resolve plane rồi cập nhật draft. Cancel/lỗi/stale giữ draft trước đó; raw OCP không đi vào schema hoặc state.
- Preview kiểm tra draft và generation, chỉ lưu summary transient về boundary, direction, top/target, clearance/retract; không Apply, không tạo ToolpathArtifact, không Post/Export.
- Apply dùng một `ProjectService.execute_cam_command`, giữ mutation/revision/dirty-reason của legacy editor và không tự Calculate.
- Calculate chỉ dùng operation đã Apply và current; sau Apply host refresh session trước khi Calculate.
- Khi đổi operation có dirty draft, host yêu cầu Apply/Discard/Cancel. Project switch, operation delete hoặc resource change làm session cũ stale/fail-closed.
- Facing/Planar mở editor mới mặc định. Schema/provider lỗi được log, hiển thị diagnostic rồi về một Legacy Editor Adapter duy nhất. Strategy khác vẫn dùng legacy.

Footer production có đúng thứ tự `Reset Draft`, `Preview`, `Validate`, `Apply`, `Calculate`, `Close`.

## Responsive và accessibility

Editor reflow field/control dưới 400 px, footer chuyển hai hàng dưới 360 px và content chỉ cuộn dọc. Kiểm thử dùng các width 300/360/420/520 px và cửa sổ 1366×768; footer vẫn truy cập được và không có horizontal overflow. Object name, accessible name, label buddy, tooltip/help, keyboard accordion, deterministic tab order và diagnostics bằng icon + text được giữ từ framework 9A.4. Validate mở disclosure/section cần thiết rồi focus field lỗi đầu, nên lỗi không chỉ được biểu diễn bằng màu.

## Chứng minh tương đương và kiểm thử

`tests/unit/test_facing_function_editors_9a51.py` bao phủ hai schema, mapping unsupported/duplicate, source/default/disclosure, round-trip, hidden Planar overtravel, draft/reset/rollback, deleted resources, selection lifecycle, responsive 300/360/420/520 px, fallback, 50-operation switch và ProjectService Save/Open thật.

Regression fixture dựng cùng input qua legacy-equivalent operation và production binding cho cả Stock/Planar, sau đó xác nhận:

- operation values, `to_dict`, parameter codec và fingerprint giống nhau;
- generator input fingerprint, ToolpathArtifact fingerprint và Toolpath IR giống nhau;
- deterministic simulation sampling và Simulation runtime result/fingerprint giống nhau;
- neutral post IR, diagnostics và FANUC ROBODRILL 21i output giống nhau khi machine/profile hợp lệ.

Presentation preferences trong QSettings chỉ gồm disclosure/section/help. Draft, width, focus và expansion không đi vào project database hay fingerprint. Manual evidence và screenshot được tạo bởi `tests/manual_stage9a51_facing_editors.py` trong thư mục Git-ignored `reference_private/DERIVED/UI_STAGE_9A5_1/`.

## Hạn chế có chủ ý

Preview Stage 9A.5.1 là native-free validated overlay summary và giữ highlight FACE từ selection workflow; chưa dựng toolpath hoặc một OCP overlay hình học mới. Không thêm tolerance, smoothing, boundary offset, cutter compensation, cut-order override, tool offset hay feed override vì Facing v1 chưa có các contract đó. Không triển khai CAM stage mới, Post Processor mới hoặc thay đổi CAD kernel.

Bước tiếp theo theo roadmap là Stage 9A.5.2 cho Contour; stage đó không được bắt đầu trong thay đổi này.
