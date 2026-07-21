# Stage 9A.5.2 — Production Function Editor cho 2D Contour

## Phạm vi và kết luận audit

Stage 9A.5.2 chuyển duy nhất strategy `contour_2d` sang Unified Function Editor
production. Audit không phát hiện nhu cầu đổi `ContourParameters`, codec key,
`OperationParameterSet`, fingerprint, geometry selector, generator, Toolpath IR,
Simulation, Post/FANUC hoặc SQLite v4. Migration chỉ bổ sung presentation schema,
typed binding và integration với application service hiện có.

Facing và Planar Face Facing tiếp tục dùng production editor 9A.5.1. Pocket và
các operation còn lại tiếp tục dùng một Legacy Editor Adapter. Stage này không
triển khai Pocket, hole family, CAM 3D, Parallel Finishing, Lathe, Post UI hay
strategy/algorithm mới.

Ảnh tham khảo được mở chọn lọc gồm WorkNC Cutter Details p0562, Standard
Parameter Groups p0545, Safe Movements p0590, Lead-in/out p0593, Tolerances
p0586, Additional Parameters p0765, HMS legacy Contour và Mastercam mill
parameters p0025. Chúng chỉ định hướng grouping và progressive disclosure; tên,
field, identity, semantic và asset đều lấy từ HMS.

## Field audit `ContourParameters`

Mọi field bên dưới là bắt buộc trong codec v1. `mm` nghĩa là unit của Setup
(`mm` hoặc `inch`); feed tương ứng là `mm/min` hoặc `in/min`. Mọi field trong
`to_dict()` tham gia `ContourParameters.fingerprint` và
`OperationParameterSet.fingerprint`. Simulation và Post không đọc parameter UI;
chúng nhận ToolpathArtifact đã tạo từ applied operation.

| Domain field | Type / unit | Default khi tạo | Source | Validator / dependency | Codec key; generator usage | Simulation / Post usage | Legacy widget | Production section / disclosure |
|---|---|---|---|---|---|---|---|---|
| `unit` | `LengthUnit` | Setup unit | SETUP | phải known; khớp Setup/tool/machine/feed | `unit`; quantity, offset, artifact unit | gián tiếp qua artifact unit | suy ra project | INTERNAL/DERIVED; unit label |
| `profile_source` | enum FACE outer / closed WIRE | `planar_face_outer` | USER + Geometry | phải khớp `GeometryReferenceKind` và provenance | `profile_source`; chọn expected kind | gián tiếp qua resolved path | `profile_source` combo | GEOMETRY / BASIC |
| `side` | `ON/INSIDE/OUTSIDE` | `on` | USER | enum; offset phải không co sập/tự giao | `side`; offset `tool_radius + radial allowance` và lead side | thay path artifact; Post không tạo compensation mode mới | `contour_side` | CUTTING / BASIC |
| `top_height` | `Length`, Setup WCS Z | Stock top | USER; recommendation STOCK | hữu hạn; `final + axial < top`; retract > top | `top_height`; bắt đầu chia lớp | đổi Z artifact | `_contour_fields["top"]` | LEVELS / BASIC |
| `final_depth` | `Length`, Setup WCS Z | Stock top − 1 mm-equivalent | USER; recommendation STOCK | hữu hạn; cắt theo −Z | `final_depth`; final cutter depth | đổi Z artifact | `_contour_fields["final"]` | LEVELS / BASIC |
| `stepdown` | positive `Length` | 1 mm-equivalent | USER; HMS default | `> 0`; chỉ hiện khi multiple depth bật; giá trị codec cũ được giữ khi ẩn | `stepdown`; chia lớp, không đổi algorithm | đổi pass artifact khi multiple bật | `_contour_fields["stepdown"]` | LEVELS / BASIC, applicable |
| `radial_stock_allowance` | non-negative `Length` | `0` | USER; HMS default | `>= 0`; cộng vào computer offset | `radial_stock_allowance`; offset path | đổi XY artifact | `_contour_fields["radial"]` | CUTTING / BASIC |
| `axial_stock_allowance` | non-negative `Length` | `0` | USER; HMS default | `>= 0`; tham gia depth order | `axial_stock_allowance`; `final_cut_depth` | đổi Z artifact | `_contour_fields["axial"]` | LEVELS / ADVANCED |
| `clearance_height` | `Length`, Setup WCS Z | Stock top + 5 mm-equivalent | USER; recommendation Setup/Stock | `clearance >= retract` | `clearance_height`; start/end và position giữa pass | safe motion artifact | `_contour_fields["clearance"]` | LINKING / BASIC |
| `retract_height` | `Length`, Setup WCS Z | Stock top + 2 mm-equivalent | USER; recommendation Setup/Stock | `retract > top` | `retract_height`; retract mỗi pass | safe motion artifact | `_contour_fields["retract"]` | LINKING / BASIC |
| `cutting_feed_rate` | positive `FeedRate` | 500 mm/min-equivalent | USER; HMS default | `> 0`; đúng unit; không vượt machine | `cutting_feed_rate`; lead/cutting moves | feed artifact; Post lower nguyên semantic | `_contour_fields["feed"]` | CUTTING / BASIC |
| `plunge_feed_rate` | positive `FeedRate` | 100 mm/min-equivalent | USER; HMS default | `> 0`; đúng unit; không vượt machine | `plunge_feed_rate`; approach/retract | feed artifact | `_contour_fields["plunge"]` | LINKING / ADVANCED |
| `spindle_speed` | positive `SpindleSpeed`, RPM | 1000 | USER; HMS default | nằm trong spindle range máy | `spindle_speed`; spindle-on event | spindle artifact; Post lower nguyên semantic | `_contour_fields["spindle"]` | CUTTING / BASIC |
| `direction` | `CLIMB/CONVENTIONAL` | `climb` | USER | enum; kết hợp Side để chọn traversal | `direction`; CW/CCW cutter path | đổi thứ tự/sweep artifact | `contour_direction` | CUTTING / BASIC |
| `start_policy` | `MIN_X_THEN_Y` | `min_x_then_y` | DEFAULT/domain | v1 chỉ hỗ trợ một policy | `start_policy`; canonical midpoint/start contract | đổi provenance/path nếu contract đổi | không có | EXPERT read-only |
| `lead_policy` | `LINEAR` | `linear` | DEFAULT/domain | v1 chỉ hỗ trợ linear | `lead_policy`; lead-in/out tuyến tính | link moves artifact | không có | LINKING read-only |
| `lead_length` | positive `Length` | 1 mm-equivalent | USER; HMS default | `> 0`; lead phải nằm đúng side và không cắt profile | `lead_length`; cùng giá trị cho lead-in/out | link moves artifact | `_contour_fields["lead"]` | LINKING / BASIC |
| `finishing_pass` | `bool` | `false` | USER | boolean; không có finish-only field khác | `finishing_pass`; lặp spring pass ở final depth | thêm một loop artifact | `finishing_pass` | ADVANCED |
| `multiple_depth_passes` | `bool` | `true` | USER | boolean; điều khiển applicability Stepdown | `multiple_depth_passes`; một hoặc nhiều lớp | đổi pass artifact | `multiple_depth_passes` | LEVELS / BASIC |
| `strategy_version` | `int = 1` | `1` | DOMAIN | chỉ nhận `1` | `strategy_version`; strategy contract | provenance/fingerprint | không có | INTERNAL |
| `schema_version` | `int = 1` | `1` | DOMAIN | chỉ nhận `1` | `schema_version`; codec compatibility | provenance/fingerprint | không có | INTERNAL |

## Audit operation, geometry và resource binding

| Stable field ID | Domain binding / identity | Validation và mutation | Source | Section / level |
|---|---|---|---|---|
| `operation_name` | operation-tree node name; identity vẫn là `OperationId`/`CamNodeId` | trim, bắt buộc; rename-only không tăng operation revision | USER | BASIC |
| `geometry_summary` | derived từ đúng một typed `GeometryReference` và resolved descriptor | read-only; Select/Rebind; missing/stale/ambiguous fail-closed | GEOMETRY | GEOMETRY / BASIC |
| `geometry_reference_id` | `operation.geometry_inputs[PROFILE].reference.reference_id` | read-only; không chọn theo display name; không giữ OCP | GEOMETRY | GEOMETRY / ADVANCED |
| `tool_assembly_id` | typed project `ToolAssemblyId` | ID phải tồn tại; generator kiểm tra revision, family, diameter, reach và stickout | USER | TOOL / BASIC |
| `tool_details` | derived Tool Definition name/family/diameter/corner/usable/stickout | read-only; Tool Library là nguồn chân lý | TOOL | TOOL / BASIC |
| `holder_summary` | derived Holder Definition | read-only; missing/stale được nêu rõ | TOOL | TOOL / BASIC |
| `compensation_summary` | derived từ `side` | read-only: HMS computer offset; không có CONTROL/WEAR/D/G41/G42 | DEFAULT | CUTTING / BASIC |
| `machine_id` | typed `MachineRequirement.machine_id` | ID/revision/fingerprint/capability/feed/spindle do domain kiểm tra | USER | ADVANCED |
| `enabled` | `operation.enabled` | thay riêng dùng `UPSTREAM_CHANGED`; disabled không Calculate | USER | ADVANCED |

## Schema và bố cục

Schema `contour_production_9a5_2` dùng typed presentation key
`contour_2d_9a5_2`. Thứ tự deterministic là:

1. `basic` — tên operation;
2. `geometry` — chain/source/typed identity;
3. `tool` — Tool Assembly và detail read-only;
4. `cutting` — Side, Direction, computer-offset summary, allowance, feed/RPM;
5. `levels` — Top/Final, multiple depth và Stepdown;
6. `linking` — Clearance/Retract, linear lead và plunge feed;
7. `advanced` — axial allowance, spring finishing pass, machine, enabled;
8. `expert` — canonical start policy read-only.

Basic disclosure hiện các section workflow cốt lõi. Field ít dùng được gắn
ADVANCED ngay trong section hoặc ở section Advanced collapsed. Expert chỉ có
algorithm policy thực sự tồn tại; không tạo tolerance, filtering, segmentation
hay post-specific input giả.

## Geometry chain, side và direction

- Select Geometry gọi picker/resolver hiện có, nhận planar FACE outer loop hoặc
  closed WIRE và cập nhật draft bằng typed identity.
- Draft/schema không chứa OCP/AIS/TopoDS. Resolved descriptor chỉ được đọc tại
  boundary callback và preview/validation.
- V1 nhận đúng một loop kín, planar, simple, LINE/ARC. Open chain, multiple
  chain/island, spline, stale/deleted/ambiguous/source mismatch đều fail-closed.
- Summary hiển thị số chain, số segment, closed, FACE/WIRE, resolved state và
  geometry orientation. Geometry orientation không bị tự sửa trong operation.
- `side`, `direction`, geometry orientation và traversal direction giữ bốn ý
  nghĩa riêng. Generator hiện có kết hợp Side + Direction để chọn CW/CCW.
- Callback kiểm tra project generation; cancel giữ nguyên draft. Project switch,
  delete operation/resource hoặc topology change làm editor stale/fail-closed.

## Cutter compensation

Contour v1 không có compensation OFF/COMPUTER/CONTROL/WEAR enum, D offset hay
G41/G42 trong domain/Toolpath IR. `ON` giữ centerline theo profile;
`INSIDE/OUTSIDE` tạo computer offset trong HMS bằng `tool_radius + radial
allowance`. UI chỉ hiển thị summary read-only của policy này và không phát minh
D offset. FANUC output tiếp tục dùng Post contract/golden hiện có; migration UI
không đổi NC semantic.

## Lead, depth pass và safe motion

- Lead v1 luôn là linear lead-in và lead-out cùng `lead_length > 0`; không có
  toggle None, arc lead, radius, sweep, separate lead-out hoặc overlap.
- Generator kiểm tra toàn bộ lead nằm đúng side và không cắt profile; lỗi dùng
  `contour.unsafe_lead` và map vào `lead_length`.
- Tắt multiple-depth ẩn Stepdown và loại nó khỏi applicable draft snapshot,
  nhưng giữ exact positive codec value đã Apply vì domain v1 vẫn yêu cầu field.
- Finishing v1 là một spring pass tại final depth, không phải rest machining.
- Clearance, Retract, Top và Final đều là absolute Setup-WCS Z, không phải machine
  coordinate. Domain yêu cầu `clearance >= retract > top > final + axial`.
- Contract hiện tại lưu safe values explicit trong operation; UI chỉ đưa
  recommendation có nguồn Setup/Stock, không giả vờ đây là inherited override.

## Source, default, draft và Apply

Giá trị Tool/Holder/geometry là linked/read-only. Các quantity Contour hiện là
operation-owned USER values; recommended default ghi rõ `HMS Contour v1 ·
Setup/Stock`. Restore Recommended Defaults chỉ đổi applicable draft, không Apply
và không làm project dirty.

Draft dùng presentation primitives tách khỏi operation. Invalid draft không gọi
domain command. Apply dựng đầy đủ `ContourParameters`, typed geometry input,
Tool/Machine reference rồi thực hiện một `ProjectService.execute_cam_command`.
Lỗi giữ applied snapshot và operation cũ. Apply thành công mới cập nhật revision,
dirty reason và project dirty theo đúng legacy semantic; không tự Calculate,
Simulation, Post hoặc Export. Calculate chỉ nhận current applied state.

## Validation, diagnostics và preview

Declarative parse/minimum/applicability chạy trước; `ContourGenerator.resolve_inputs`
là nguồn chân lý cuối. Diagnostic hiện inline, badge section, header count và
Diagnostics panel; Validate mở/focus/scroll field lỗi đầu, không chỉ đổi màu.
Code domain hiện có được giữ nguyên. UI-only stale code dùng namespace
`contour.ui.stale_editor`.

Preview nhận request bất biến có project/operation/generation/fingerprint, resolve
profile và dựng `ContourInputs` bằng generator hiện có. Nó hiển thị chain,
segment, traversal, Side/Direction, pass count, Top/Depth, linear lead và safe
planes. Preview không mutation operation, không tạo/publish ToolpathArtifact,
không Post/Export và không chạm OCP từ worker thread. Đây là validated input
summary, không tuyên bố là một production compensated viewport overlay.

Footer giữ thứ tự `Reset Draft`, `Preview`, `Validate`, `Apply`, `Calculate`,
`Close`. Calculate bị khóa khi draft dirty/invalid/stale, operation disabled,
geometry/tool không current hoặc lifecycle không cho phép.

## Exact equivalence

`tests/unit/test_contour_function_editor_9a52.py` dựng cùng operation trước/sau
binding và xác nhận:

- domain values, `to_dict`, parameter codec và fingerprint giống nhau;
- operation revision/dirty reason/geometry input ID giống legacy semantic;
- generator input fingerprint, ToolpathArtifact fingerprint và Toolpath IR giống;
- Simulation samples, result status/issues/statistics/fingerprint giống;
- neutral Post IR, diagnostics và FANUC ROBODRILL 21i output CRLF giống;
- UI state không tham gia project/operation/toolpath/NC fingerprint.

Fixtures bao phủ OUTSIDE/INSIDE, climb/conventional, single/multiple depth,
finishing pass, radial allowance và linear lead. Open/multiple chain chưa phải
supported operation input của v1 nên được kiểm tra fail-closed ở resolver/domain
tests hiện có, không biến thành equivalence fixture giả.

## Lifecycle, responsive, accessibility và fallback

Manual harness `tests/manual_stage9a52_contour_editor.py` dùng MainWindow,
ProjectService và OCP BREP thật; viewport dùng backend offscreen no-op khi cần.
Harness kiểm tra Select Geometry, Tool, Side/Direction, Depth/Allowance,
depth-pass applicability, lead, invalid/Validate/Reset/Preview/Apply/Calculate,
Facing production, Pocket legacy, Save/Open, project switch, resize và close.
Không workflow nào tự chạy Simulation/Post/Export.

Ảnh Git-ignored nằm tại `reference_private/DERIVED/UI_STAGE_9A5_2/`, gồm Basic,
Geometry, Tool, Cutting, Levels, Linking, Advanced collapsed/expanded, Expert,
inline validation, compensation, lead, source/default, 300 px, 420 px,
1366×768 và Pocket legacy. Editor dùng content scroll nội bộ, footer cố định,
reflow dưới 400 px và không horizontal overflow ở 300 px.

Mọi field/action có stable objectName và accessibleName từ framework; label buddy,
tooltip/help, keyboard accordion, deterministic tab order và diagnostic text cho
screen reader được giữ. Offscreen Qt trên máy test thiếu glyph tiếng Việt nên ảnh
có thể hiện ô vuông; production Windows dùng font hệ thống.

Khi schema/provider lỗi, host log diagnostic rồi mở đúng một Legacy Editor
Adapter; không mở hai editor writable. Operation Manager dùng typed identity,
refresh subtree/status qua event hiện có và child Toolpath/Simulation/Post/NC
tiếp tục mở panel đúng loại.

## Giới hạn có chủ ý

Contour v1 chưa hỗ trợ open chain, multi-chain/island, spline, tab/bridge,
ramp/helix/direct plunge, arc lead, separate lead-out, tolerance/filtering,
controller/wear compensation, D offset hoặc G41/G42. Preview hiện là validated
native-free summary, chưa thêm OCP overlay. Không có SQLite migration; schema vẫn
v4. Bước tiếp theo theo roadmap là Stage 9A.5.3 Pocket, chưa được bắt đầu trong
thay đổi này.
