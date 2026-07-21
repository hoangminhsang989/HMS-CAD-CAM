# Stage 9A.5.3 — Production Function Editor cho Pocket 2.5D

## Phạm vi và kết luận audit

Stage 9A.5.3 chuyển operation `pocket_2_5d` từ Legacy Editor Adapter sang
Unified Function Editor production. Migration chỉ thêm presentation schema,
binding, lifecycle callback và integration; Pocket domain v1, codec,
`OperationParameterSet`, fingerprint, `PocketGenerator`, Toolpath IR,
Simulation, Post/FANUC và SQLite v4 không thay đổi.

Pocket v1 hiện có một outer loop kín LINE/ARC, không có inner loop/island, chỉ
có inward offset clearing, `VERTICAL_PLUNGE`, `CLIMB`/`CONVENTIONAL`, chia lớp
theo stepdown, radial stock allowance và floor/axial allowance. UI không tạo
pattern, entry, linking, finish pass hoặc island control không tồn tại. Nếu
resolver trả inner loop, geometry bị chẩn đoán `UNSUPPORTED` và fail-closed.

Các ảnh tham khảo được mở chọn lọc là WorkNC Machining Zone p0546, Cutter
Details p0562, Standard Parameter Groups p0545, Tolerances p0586, Safe
Movements p0590, Additional Parameters p0765, Mastercam mill parameters p0025
và HMS Legacy Pocket. Chúng chỉ định hướng grouping, source summary và
progressive disclosure; field, identity, default và semantic luôn lấy từ HMS.

## Field audit Pocket v1

`mm` và `mm/min` theo `Setup WCS` (`in`/`in/min` khi project dùng inch). Tất cả
quantity đều được parse finite trước khi gọi domain. `OperationParameterSet`
v1 dùng đúng codec keys sau; không thêm key cho UI.

| Domain field | Type / unit | Default hiện có | Source | Validator/generator usage | Codec key / fingerprint | UI section / disclosure | Legacy widget |
|---|---|---|---|---|---|---|---|
| `unit` | `LengthUnit` | Setup WCS | SETUP | Pocket strategy yêu cầu known unit và feed unit tương ứng | `unit`; tham gia parameter fingerprint | INTERNAL/DERIVED; unit cạnh field | suy ra từ project |
| `geometry.reference` | typed `GeometryReference` | chưa bind khi tạo operation | GEOMETRY | resolver yêu cầu BREP FACE hoặc profile; source scope, revision, topology và closed LINE/ARC | `geometry` payload; fingerprint của operation geometry | GEOMETRY / Basic summary, identity Advanced | `_picked_reference`, status |
| `top_z` | absolute Setup-WCS Z / `Length` | Stock BOX top | STOCK/DEFAULT | phải trùng plane resolved trong generator | `top_z` | LEVELS / Basic | `_pocket_fields["top"]` |
| `bottom_z` | absolute Setup-WCS Z / `Length` | top − 1 unit | STOCK/DEFAULT | thấp hơn top; cùng axial allowance để tạo final cutter Z | `bottom_z` | LEVELS / Basic | `_pocket_fields["bottom"]` |
| `axial_allowance` | non-negative `Length` | `0` | DEFAULT | `bottom_z + allowance < top_z`; giữ floor stock | `axial_allowance` | LEVELS / Basic | `_pocket_fields["axial"]` |
| `stepover` | positive `Length` | `4` mm-equivalent | DEFAULT (HMS Pocket v1) | finite, > 0, nhỏ hơn diameter; khoảng cách offset loop | `stepover` | CUTTING / Basic | `_pocket_fields["stepover"]` |
| `stepdown` | positive `Length` | `1` mm-equivalent | DEFAULT (HMS Pocket v1) | finite, > 0; `pocket_depth_levels` không đổi | `stepdown` | LEVELS / Basic | `_pocket_fields["stepdown"]` |
| `radial_stock_allowance` | non-negative `Length` | `0` | DEFAULT | cộng vào bán kính offset ban đầu; không trộn với tolerance | `radial_stock_allowance` | CUTTING / Basic | `_pocket_fields["allowance"]` |
| `clearance_height` | absolute Setup-WCS Z / `Length` | top + 5 | SETUP/DEFAULT recommendation | `clearance >= retract` và rapid safe trong generator | `clearance_height` | LINKING / Advanced | `_pocket_fields["clearance"]` |
| `retract_height` | absolute Setup-WCS Z / `Length` | top + 2 | SETUP/DEFAULT recommendation | `retract > top` | `retract_height` | LINKING / Advanced | `_pocket_fields["retract"]` |
| `cutting_feed_rate` | `FeedRate` | 500 unit-equivalent | DEFAULT | > 0, đúng unit, không vượt machine | `cutting_feed_rate` | CUTTING / Advanced | `_pocket_fields["feed"]` |
| `plunge_feed_rate` | `FeedRate` | 100 unit-equivalent | DEFAULT | > 0, đúng unit, không vượt machine | `plunge_feed_rate` | ENTRY / Advanced | `_pocket_fields["plunge"]` |
| `spindle_speed` | `SpindleSpeed` RPM | `1000` | DEFAULT | trong spindle range của machine | `spindle_speed` | CUTTING / Advanced | `_pocket_fields["spindle"]` |
| `entry_policy` | enum | `vertical_plunge` | DOMAIN/DEFAULT | PocketGenerator chỉ chấp nhận `VERTICAL_PLUNGE` | `entry_policy` | ENTRY / Basic choice một option | `pocket_entry` |
| `cutting_direction` | enum | `climb` | DOMAIN/DEFAULT | đảo traversal loop; không đảo source orientation | `cutting_direction` | CUTTING / Advanced choice | `pocket_direction` |
| `tolerance` | positive `Length` | `1e-7` unit-equivalent trong default operation | EXPERT/DEFAULT | offset collapse/progress và depth-level tolerance | `tolerance` | EXPERT / Expert | `_pocket_fields["tolerance"]` |
| `strategy_version` | `int = 1` | `1` | DOMAIN | Pocket v1 only | implicit `OperationParameterSet` | INTERNAL | không có |
| `schema_version` | `int = 1` | `1` | DOMAIN | codec compatibility | implicit `OperationParameterSet` | INTERNAL | không có |

Các resource binding không phải parameter mới:

| Stable field ID | Domain binding / source | Usage và disclosure |
|---|---|---|
| `operation_name` | operation-tree node name; identity vẫn `OperationId`/`CamNodeId` | rename-only, Basic |
| `geometry_summary` | derived từ resolver và typed reference | Select/Rebind, Basic; không serialize |
| `geometry_reference_id` | `OperationGeometryInput` role `BOUNDARY` | read-only identity, Advanced; không chọn theo display name |
| `island_summary` | derived `inner_loops` count từ profile resolver | read-only; `0 island` hoặc `UNSUPPORTED`, Geometry |
| `tool_assembly_id` | typed project `ToolAssemblyId` | Tool Library là nguồn chân lý, Basic choice |
| `tool_details` / `holder_summary` | derived Tool/Holder Definition | read-only Tool summary |
| `machining_pattern` | derived algorithm policy `offset_inward` | read-only; không phải enum giả |
| `machine_id` | `MachineRequirement` typed snapshot | Advanced choice; generator kiểm tra capability |
| `enabled` | `operation.enabled` | Advanced; disabled operation không Calculate |
| `final_depth_summary` | `bottom_z + axial_allowance` | read-only DERIVED; không có control thứ hai |
| `level_count` | `pocket_depth_levels` trên applied values | read-only DERIVED; không tham gia codec |

Không có field Pocket domain cho wall/floor riêng: wall dùng
`radial_stock_allowance`, floor dùng `axial_allowance`. Không có percentage
stepover mode; UI dùng đúng distance hiện tại. Không có T/H/D, holder geometry,
Safe Z machine coordinate, WCS hoặc Stock dimensions input trong operation
editor; các giá trị đó thuộc Tool/Setup/Machine và chỉ được tóm tắt khi contract
hiện có cho phép.

## Production schema và disclosure

Schema ID là `pocket_production_9a5_3`, strategy presentation key là
`pocket_2_5d_9a5_3`. Section order xác định:

```text
BASIC → GEOMETRY → TOOL → CUTTING → LEVELS → ENTRY → LINKING → ADVANCED → EXPERT
```

Mỗi field có stable ID, binding key, order, unit, source/default, disclosure
level, typed validator, help key và reset behavior. Schema/state không chứa
QWidget/QObject, QModelIndex, callback, raw OCP/AIS/TopoDS hoặc expression
Python. `validate_pocket_schema_contract` reject field thiếu, duplicate hoặc
field invented.

Basic mặc định đủ cho operation thông thường: Geometry region, Tool Assembly,
offset pattern summary, stepover, wall/floor allowance, Top/Bottom, Stepdown và
Entry Method. Feed/spindle, direction, plunge detail, safe motion, machine và
enabled mở ở Advanced; `tolerance` là Expert duy nhất thực sự tồn tại.

Header hiển thị operation name, `Pocket 2.5D · Offset`, direction, stepover,
Top/Depth, Tool summary, Geometry summary và operation artifact status. Khi
draft thay đổi, framework hiển thị dirty badge; error/warning count và toolpath
status vẫn do state/Operation Manager hiện có tổng hợp.

## Geometry, region và island

- `Select` dùng `_contour_pick_provider` và `PocketGeometryResolver` hiện có;
  không viết lại CAD selection kernel.
- Identity là `GeometryReferenceId` typed. Duplicate display name không ảnh
  hưởng lựa chọn.
- Pocket v1 yêu cầu đúng một `BOUNDARY` input, BREP FACE hoặc profile reference,
  closed canonical outer loop LINE/ARC. Open, stale, deleted, source mismatch,
  wrong unit, wrong orientation, duplicate/additional geometry và inner loop
  đều fail-closed.
- Không tự đảo orientation, tự sửa boundary, tự nhận island theo màu/layer hay
  giữ raw OCP trong draft/schema.
- Preview dùng resolved inputs và chỉ trả validated summary: region, số offset
  loop, số level, direction, depth, entry và safe values. Đây là preview
  approximate, không tạo `ToolpathArtifact`, không Calculate và không phải bằng
  chứng collision-safe.
- `Cancel`/exception không mutate draft. Project switch hoặc operation delete
  làm callback cũ stale qua generation/selection guard.

## Tool, cutting, levels, entry và linking

Tool section chỉ chọn Tool Assembly project-owned và hiển thị diameter, corner
radius, usable length, stickout, holder. Đổi tool chỉ nằm trong draft; không tự
Apply, đổi stepover/stepdown hay Calculate. Tool family/diameter/revision/stickout
và machine capability vẫn do `PocketGenerator.resolve_inputs` quyết định cuối.

Cutting chỉ hiển thị pattern đang tồn tại (`offset_inward`) và direction thật
(`climb`, `conventional`). Stepover là distance absolute, không clamp, không
percentage mode. Radial allowance là wall allowance duy nhất của domain.

Levels giữ absolute Setup-WCS Z; `final_depth_summary` là derived và pass count
read-only. Không thay thuật toán chia level và không biến hidden presentation
value thành input khác.

Entry chỉ có `vertical_plunge`; ramp, helix, pre-drill, existing entry point và
mọi field method-specific khác không được dựng. Plunge feed nằm Advanced.

Linking chỉ có Clearance Z và Retract Z explicit theo operation contract; không
đoán Safe Z, không dùng machine coordinate và không giả vờ inherited value là
override của Setup. Domain chặn `retract <= top` hoặc `clearance < retract`.

## Source/default, draft/apply và validation

Recommended defaults lấy đúng `_default_pocket_parameters` hiện có (Stock BOX,
unit scale và HMS Pocket v1). Restore Recommended Defaults chỉ thay draft;
không đổi geometry/tool, không Apply và không Calculate. Source hiển thị gọn
`TOOL`, `GEOMETRY`, `SETUP/Stock recommendation`, `DEFAULT` hoặc `DERIVED`.

Draft tách khỏi applied operation. Validation declarative kiểm tra required,
finite, minimum và quan hệ Top/Bottom/Retract; domain validator/generator là
nguồn chân lý cuối cho tool compatibility, stepover diameter, machine limit,
profile, island, entry, offset collapse, level và safe motion. Invalid draft
không mutation domain/project dirty/artifact. Apply gửi một snapshot immutable,
atomic qua `ProjectService.execute_cam_command`; lỗi giữ operation/applied
snapshot cũ. Apply thành công mới cập nhật revision/dirty reason theo Legacy
Pocket semantic:

```text
geometry → GEOMETRY_CHANGED
tool     → TOOL_CHANGED
machine  → MACHINE_CHANGED
parameter→ PARAMETERS_CHANGED
enabled  → UPSTREAM_CHANGED
```

Apply không tự Calculate, Simulate, Post hoặc Export. Calculate chỉ dùng applied
state current/valid và gọi `ProjectService.compute_pocket` rõ ràng. Footer giữ
chuẩn `Reset Draft | Preview | Validate | Apply | Calculate | Close`.

Diagnostics map code domain về field/section/header; focus lỗi đầu và có icon +
text. Không có UI validator cạnh tranh với generator. Lỗi unsupported island,
stale geometry, invalid/open boundary, duplicate input, invalid tool/levels,
stepdown/stepover/allowance, entry hoặc safe motion đều fail-closed theo domain
khả năng hiện tại.

## Exact equivalence

`tests/unit/test_pocket_function_editor_9a53.py` dựng cùng input trước/sau
binding và xác nhận:

- domain `PocketStrategy`, `to_dict`, `OperationParameterSet` và fingerprint;
- operation geometry/tool/machine binding và dirty semantics;
- Pocket generator input fingerprint, `ToolpathArtifact` payload/fingerprint và
  Toolpath IR;
- deterministic Simulation samples, status, issues, statistics và fingerprint;
- neutral Post IR, diagnostics và FANUC ROBODRILL 21i output CRLF.

Không golden output nào bị cập nhật. Presentation state (section expansion,
width, draft, focus, summary text) không tham gia project/operation/toolpath/NC
fingerprint.

## Operation Manager, fallback và lifecycle

Chọn Pocket trong Operation Manager mở production editor mặc định; Apply refresh
summary/status, Calculate cập nhật artifact status, và child Toolpath/Simulation/
Post/NC vẫn mở panel cũ. Facing, Planar Face Facing và Contour tiếp tục
production editor. Drilling/Tapping/Reaming/Boring tiếp tục một Legacy Editor
Adapter. Nếu Pocket schema/provider lỗi, host log diagnostic và fallback về đúng
một Legacy editor; không mở hai writable editor.

New/Open/Save/Save As/Autosave/Recovery tiếp tục đi qua ProjectService; applied
values được lưu, draft không serialize, SQLite vẫn v4. Project switch, geometry
deletion, tool change, operation delete và stale callback không được phép mutate
operation cũ.

## Responsive và accessibility

Manual harness kiểm tra dock 300/360/420/520 px và cửa sổ 1366×768. Content
scroll nội bộ, footer luôn truy cập được và horizontal scrollbar bằng 0 ở 300 px.
Framework giữ stable `objectName`/`accessibleName`, label buddy, tooltip/help,
keyboard accordion, tab order, inline error text và focus tới field lỗi đầu.
Offscreen Qt trên máy kiểm thử không có glyph tiếng Việt nên screenshot có thể
hiện ô vuông; Windows production dùng font hệ thống.

## Manual smoke và ảnh

Chạy:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe tests/manual_stage9a53_pocket_editor.py `
  --output-dir reference_private/DERIVED/UI_STAGE_9A5_3
```

Harness thực hiện New/Open/Save, chọn region, đổi pattern/direction,
stepover/stepdown, Top/Bottom, allowance, Entry, invalid/Validate/Reset,
Preview, Apply, Calculate rõ ràng, chuyển Facing/Contour, mở Legacy Drilling,
project switch, resize và đóng sạch. Không tự Simulation/Post/Export. Ảnh nằm
trong `reference_private/DERIVED/UI_STAGE_9A5_3/` và không được stage.

## Giới hạn và bước tiếp theo

Pocket editor chưa tạo island support, ramp/helix/pre-drill, zigzag/spiral/
one-way/follow-boundary, finish pass, rest machining, stock removal mới,
tolerance ngoài field hiện có, collision kernel, CAM 3D, direct CNC hoặc Post
UI. Các giới hạn này là contract Pocket v1, không phải thiếu control do migration.

Bước tiếp theo theo roadmap là migration Drilling family UI riêng; không bắt đầu
trong Stage 9A.5.3.
