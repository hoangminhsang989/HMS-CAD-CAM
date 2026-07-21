# Main Workspace Shell - Stage 9A.2

## Phạm vi đã triển khai

Stage 9A.2 thay composition của cửa sổ chính nhưng giữ nguyên CAD/CAM domain,
Toolpath IR, SQLite schema và các application service. `MainWindow` tiếp tục là
`QMainWindow`; `CadViewportWidget` hiện có tiếp tục là central widget duy nhất,
không tạo lại OCP context và không đổi selection contract.

Bố cục production:

```text
Menu + Quick Access/CAD toolbar
Workspace bar: HOME | CAD | MILL 2D | MILL 3D | LATHE | SIMULATION | POST
Contextual ribbon hiện có
┌──────────────────┬──────────────────────────┬─────────────────────┐
│ Operation Manager│ OCP graphics viewport    │ Function Editor     │
│ dock trái        │ central widget ưu tiên   │ dock phải           │
├──────────────────┴──────────────────────────┴─────────────────────┤
│ Diagnostics & Activity dock dưới, đóng/mở được                   │
└──────────────────────────────────────────────────────────────────┘
Secondary dock: Simulation hoặc Post / Program Assembly.
```

## Viewport priority và responsive

- Viewport giữ minimum `520 x 360` logical pixel.
- Operation Manager dùng dải `240-360 px`; Function Editor dùng dải
  `360-520 px`; secondary panel dùng dải `360-620 px`.
- Editor và secondary workflow scroll nội bộ. Header summary và footer Apply / Close
  của Function Editor không nằm trong vùng scroll.
- Ở dưới `1200 px`, khi cả hai primary panel cùng mở, Operation Manager tự thu
  gọn. Từ `1280 px` trở lên, panel tự thu gọn được phục hồi.
- Qt logical pixel, layout co giãn và scroll được dùng thay cho fixed content
  height; không thu nhỏ font để xử lý DPI.
- GUI smoke ghi nhận viewport tối thiểu đạt yêu cầu ở `1366 x 768`,
  `1600 x 900` và `1920 x 1080`. Geometry restore ngoài màn hình được clamp về
  available screen.

## Panel host

### Operation Manager

`OperationManagerHost` reparent `CamOperationTree` hiện có, nên domain ID trong
item role, selection và project-generation guard không đổi. Host bổ sung header,
search/filter presentation-only và toolbar nhỏ; menu `+ Tạo` chứa toàn bộ action
tạo Job, Setup, resource và operation hiện có. Stage này chưa tạo Machine Group
tree mới và chưa đổi tree contract; phần đó thuộc 9A.3.

### Function Editor

`FunctionEditorHost` reparent `_CamPropertiesEditor` hiện có vào `QScrollArea`,
thêm selection/status summary và footer sticky. Footer vẫn gọi đúng `_submit()`
hiện có nên parse, validation, draft và atomic Apply không đổi. Stage này chưa
phân loại/migrate field; Unified Function Editor framework thuộc 9A.4.

### Diagnostics và secondary workflow

`DiagnosticsHost` tái sử dụng `OutputLog`, có activity/severity summary và scroll
riêng. `SecondaryPanelHost` tái sử dụng nguyên `SimulationPanel`,
`PostProcessorPanel` và `ProgramAssemblyPanel`; mở panel hoặc đổi tab không tự
Run, Generate, Save hay Export.

Project/Topology và CAD Properties cũ vẫn tồn tại dưới dạng dock tùy chọn để giữ
CAD import, topology selection, measurement và appearance workflow.

## Workspace selector

| Workspace | Stage 9A.2 |
|---|---|
| HOME | Bật; trạng thái shell/tổng quan |
| CAD | Bật; đưa Project/Topology và CAD Properties lên trước |
| MILL 2D | Bật; đưa Operation Manager và Function Editor lên trước |
| MILL 3D | Disabled; tooltip ghi rõ mới ở mức CAM 3D Foundation |
| LATHE | Disabled; tooltip ghi rõ chưa triển khai |
| SIMULATION | Bật; chỉ mở/chọn Simulation panel hiện có |
| POST | Bật; chỉ mở/chọn Post / Program Assembly panel hiện có |

Workspace action không tạo chức năng giả và không gọi Calculate/Post/Export.

## Panel visibility và reset

Menu `Hiển thị` luôn có action checked đồng bộ với dock visibility cho:

- Operation Manager;
- Function Editor;
- Diagnostics & Activity;
- Simulation / Post;
- Project / Topology;
- Thuộc tính CAD.

`Reset Workspace Layout` chỉ đặt lại vị trí, kích thước và visibility của UI,
đặt active workspace về HOME và xóa riêng nhóm settings Stage 9A.2. Action không
chạm project session, không làm project dirty và không xóa preference khác.

## Layout persistence

`WorkspaceLayoutStore` lưu user-runtime state vào `workspace_ui.ini` trong
config directory của HMS, không lưu trong `.HMS`:

- window geometry;
- Qt dock state/positions/visibility;
- Operation Manager width;
- Function Editor width;
- Diagnostics height;
- active workspace.

Nhóm settings có `layout_version = 1`. Version không tương thích chỉ xóa nhóm
`workspace_shell_9a2`, sau đó áp dụng default layout; project fingerprint,
Save As và dirty semantics không liên quan.

## Accessibility và keyboard

- Dock, host, search, workspace bar, collapse và footer action có `objectName`,
  tooltip/accessibility text ổn định.
- Disabled MILL 3D/LATHE có lý do đọc được qua tooltip/status tip.
- Focus state có border tương phản; Function Editor dùng thứ tự widget hiện có
  trong scroll và footer nằm sau content.
- Không thêm shortcut mới nên không xung đột shortcut project/CAD hiện có.
- Panel bị đóng luôn khôi phục được qua menu `Hiển thị`.

## Backward compatibility

Không đổi binding của New/Open/Save/Save As, Autosave/Recovery, CAD/XCAF import,
OCP viewer, operation selection/editor, toolpath calculation, Simulation,
Post, Program Assembly hoặc close/callback guard. `CamWorkspace` vẫn là
coordinator và vẫn public qua `MainWindow.cam_workspace`; alias `cam_dock` tiếp
tục đưa người gọi cũ tới Operation Manager dock.

## Screenshot regression và giới hạn

Manual harness: `tests/manual_stage9a2_workspace_shell.py`. Output runtime bị
Git ignore tại `reference_private/DERIVED/UI_STAGE_9A2/`, gồm ba resolution,
hai primary panel collapsed, Diagnostics expanded, CAM operation selected và
Post selected. Nhánh offscreen dùng OCP kernel để import/topology nhưng dùng
no-op presentation backend vì Qt offscreen không có native Windows surface;
chạy không đặt `QT_QPA_PLATFORM` sẽ dùng OCP viewport production.

Chưa làm trong Stage 9A.2: Operation Manager hierarchy mới (9A.3), Unified
Function Editor/field migration (9A.4+), CAM 3D Function UI, Parallel Finishing,
Lathe hoặc CAM strategy mới.
