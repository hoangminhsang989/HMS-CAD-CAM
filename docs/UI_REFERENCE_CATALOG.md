# Catalog tham khảo giao diện - Stage 9A.1

## Phạm vi và nguồn

Catalog này được tạo từ 7 tài liệu có text layer, gồm 5 tài liệu Mastercam và
2 tài liệu WorkNC. Script chỉ tìm text trong các PDF ứng viên rồi render 26
trang có điểm khớp chủ đề; không render tuần tự toàn bộ tài liệu.

- Mastercam: `reference_private/MASTERCAM/INBOX/1.zip` và `2.zip`.
- WorkNC: `reference_private/WORKNC/TRAINING/` và `ONLINE_HELP/`.
- Metadata máy đọc: `reference_private/DERIVED/UI_REFERENCE/reference_catalog.json`
  và `reference_catalog.csv`.
- Contact sheet: `reference_private/DERIVED/UI_REFERENCE/CONTACT_SHEETS/`.
- Ảnh/PDF/ZIP đều nằm trong `reference_private/` và không được commit.

Phân loại đủ 10 nhóm bắt buộc: Main Workspace, Operation Manager, Function
Dialog, Tool Selection, Geometry Selection, Cutting Parameters, Linking,
Advanced Options, Simulation và Post/NC.

## Các trang Mastercam

| # | Tài liệu và trang PDF | Ảnh cục bộ / loại giao diện | Điểm mạnh | Điểm yếu | Ý tưởng áp dụng cho HMS | Không nên sao chép |
|---|---|---|---|---|---|---|
| M01 | *MasterCAM X4 - Lập trình gia công phay*, p.4 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/MAIN_WORKSPACE/mastercam-mastercam-x4-lap-trinh-gia-cong-phay-manager-workflow-p0004.png` - Main Workspace | Một manager giữ ngữ cảnh tạo, sửa, kiểm tra và xuất. | Manager dễ thành nơi chứa quá nhiều action. | Giữ workflow liền mạch; action theo selection đặt ở ribbon/editor HMS. | Bố cục, icon, nhãn và chrome Mastercam X4. |
| M02 | *MasterCAM X4 - Lập trình gia công phay*, p.3 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/OPERATION_MANAGER/mastercam-mastercam-x4-lap-trinh-gia-cong-phay-manager-tree-p0003.png` - Operation Manager | Machine Group và Toolpath Group làm quan hệ công việc dễ đọc. | Lệnh và trạng thái chen dày vào cây. | Cây HMS dùng ID ổn định, node có vai trò và status riêng. | Icon, tên nhóm độc quyền và thứ tự pixel. |
| M03 | *Giáo trình MasterCAM 2017 - WCS và toolpath*, p.25 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/MILL_FUNCTION_DIALOGS/mastercam-giao-trinh-mastercam-2017-wcs-va-toolpath-mill-parameters-p0025.png` - Cutting Parameters | Cut, Depth, Entry và Linking được chia theo nhiệm vụ. | Nhiều trang kỹ thuật khó dò với người mới. | Chuẩn hóa Geometry, Tool, Cutting, Levels và Linking; Basic ngắn. | Tên tab, icon và bố cục dialog. |
| M04 | *Giáo trình MasterCAM 2017 - WCS và toolpath*, p.11 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/SIMULATION_POST/mastercam-giao-trinh-mastercam-2017-wcs-va-toolpath-backplot-p0011.png` - Simulation | Backplot xuất phát trực tiếp từ operation và có tool/holder context. | Backplot và Verify dễ bị hiểu là cùng mức kiểm tra. | Preview/Simulation có CURRENT/STALE và phạm vi kiểm tra rõ. | Icon/chrome Backplot. |
| M05 | *Giáo trình MasterCAM 2017 - WCS và toolpath*, p.30 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/SIMULATION_POST/mastercam-giao-trinh-mastercam-2017-wcs-va-toolpath-post-selected-p0030.png` - Post/NC | Phạm vi Post theo selection/group dễ hiểu. | Có thể ghi file quá sớm nếu không có gate. | Giữ Validate, Generate, Preview, Save Managed, Export thành các bước có điều kiện. | Dialog Post processing và ghi file ngầm. |
| M06 | *Bài giảng MasterCAM 2D*, p.66 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/TOOL_AND_GEOMETRY/mastercam-bai-giang-mastercam-2d-tool-definition-p0066.png` - Tool Selection | Phân biệt tool definition, holder và tool parameters. | Quá nhiều chi tiết hình học dao trong ngữ cảnh operation. | Basic chỉ chọn Tool Assembly; chi tiết mở ở panel tài nguyên riêng. | Hình dao, thư viện và layout hộp thoại. |
| M07 | *Bài giảng MasterCAM 2D*, p.133 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/TOOL_AND_GEOMETRY/mastercam-bai-giang-mastercam-2d-stock-setup-p0133.png` - Geometry Selection | Stock gắn với Machine Group và có nguồn hình học rõ. | Trộn Stock, Program và feed settings trong một dialog. | Stock thuộc Setup; operation chỉ xem summary kế thừa. | Tab Stock Setup và hình minh họa. |
| M08 | *Bài giảng MasterCAM 2D*, p.76 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/SIMULATION_POST/mastercam-bai-giang-mastercam-2d-simulate-verify-post-p0076.png` - Simulation | Backplot, Verify, Post ở gần workflow operation. | Action ngang hàng không thể hiện điều kiện/rủi ro. | Chỉ enable action hợp lệ và luôn kèm semantic status. | Thanh lệnh và icon mô phỏng. |
| M09 | *Phương pháp gia công tiện trong MasterCAM X6*, p.23 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/OPERATION_MANAGER/mastercam-phuong-phap-gia-cong-tien-trong-mastercam-x6-lathe-operation-manager-p0023.png` - Operation Manager | Operation bung thành Parameters, Tool, Geometry và NC. | Cây sâu dễ lặp thông tin/action. | Node con có vai trò ổn định, status summary, không dùng row index. | Tên node, icon và cấu trúc pixel-by-pixel. |
| M10 | *Phương pháp gia công tiện trong MasterCAM X6*, p.33 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/LATHE_FUNCTION_DIALOGS/mastercam-phuong-phap-gia-cong-tien-trong-mastercam-x6-lathe-function-p0033.png` - Function Dialog | Chỉ hiện tham số tiện đặc thù theo strategy. | Dialog con nối tiếp che mất tổng quan operation. | Advanced theo strategy nằm trong cùng editor và có summary/cảnh báo. | Dialog Lathe Rough và hình minh họa. |
| M11 | *Phương pháp gia công tiện trong MasterCAM X6*, p.27 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/SIMULATION_POST/mastercam-phuong-phap-gia-cong-tien-trong-mastercam-x6-lathe-post-p0027.png` - Post/NC | Profile đầu ra và metadata xuất hiện trước NC. | Chọn post có thể thiếu compatibility diagnostics. | Hiển thị profile, compatibility, simulation gate và artifact status chung. | Lựa chọn post và file dialog Mastercam. |
| M12 | *Tổng quan CAM Milling trên MasterCAM*, p.23 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/OPERATION_MANAGER/mastercam-tong-quan-cam-milling-tren-mastercam-machine-toolpath-groups-p0023.png` - Operation Manager | Machine Group tạo ranh giới máy và nhóm operation. | Terminology không ánh xạ trực tiếp domain HMS. | Ánh xạ Job > Setup/Machine Group > Operations theo domain hiện có. | Terminology và UI cây Mastercam. |
| M13 | *Tổng quan CAM Milling trên MasterCAM*, p.58 | `reference_private/DERIVED/UI_REFERENCE/MASTERCAM/TOOL_AND_GEOMETRY/mastercam-tong-quan-cam-milling-tren-mastercam-safety-zone-p0058.png` - Geometry Selection | Safety Zone đặt ở cấp máy/setup. | Stock và vùng an toàn bị phân tán qua nhiều tab. | Kế thừa từ Setup; chỉ override có lý do và nguồn rõ. | Tab/cách nhập Safety Zone của Mastercam. |

## Các trang WorkNC

| # | Tài liệu và trang PDF | Ảnh cục bộ / loại giao diện | Điểm mạnh | Điểm yếu | Ý tưởng áp dụng cho HMS | Không nên sao chép |
|---|---|---|---|---|---|---|
| W01 | *WORKNC 2021 Online Help*, p.538 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/TOOLPATH_PARAMETERS/worknc-worknc-2021-online-help-toolpath-parameters-p0538.png` - Function Dialog | Phân biệt summary, Standard và Specific Parameters. | Hai cột dày không phù hợp màn hình hẹp. | Header summary và section dọc responsive; Advanced collapsed. | Menu, icon và tỷ lệ panel WorkNC. |
| W02 | *WORKNC 2021 Online Help*, p.545 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/TOOLPATH_PARAMETERS/worknc-worknc-2021-online-help-standard-parameter-groups-p0545.png` - Function Dialog | Nhóm theo nhiệm vụ người vận hành. | Một số nhóm dài/chồng lấn trách nhiệm. | Geometry, Tool, Cutting, Levels, Linking, Advanced thống nhất. | Tên mục/icon nguyên trạng. |
| W03 | *WORKNC 2021 Online Help*, p.546 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/MACHINING_ZONE/worknc-worknc-2021-online-help-machining-zone-help-p0546.png` - Geometry Selection | Một dialog cho Window, View, Curve, Plane và Surface. | Nhiều mode cần preview mạnh để tránh chọn sai. | Mode selector rõ, selection summary và focus trong viewport. | Pictogram và layout Machining Zone. |
| W04 | *WORKNC 2021 Online Help*, p.562 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/CUTTER_DETAILS/worknc-worknc-2021-online-help-cutter-details-p0562.png` - Tool Selection | Chọn từ library đồng thời xem kích thước/form. | Dễ lặp định nghĩa dao trong editor operation. | Operation tham chiếu Tool Assembly, chi tiết read-only hoặc linked editor. | Hình dao, dialog và thư viện WorkNC. |
| W05 | *WORKNC 2021 Online Help*, p.586 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/TOLERANCES/worknc-worknc-2021-online-help-tolerances-p0586.png` - Cutting Parameters | Giải thích dependency tolerance, stock, stepover và số điểm. | Precision dễ bị chỉnh mà không hiểu chi phí. | Đưa vào Advanced/Expert với cảnh báo chất lượng và thời gian. | Hình minh họa và default WorkNC. |
| W06 | *WORKNC 2021 Online Help*, p.590 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/CUTTER_MOVEMENTS/worknc-worknc-2021-online-help-safe-movements-p0590.png` - Linking | Approach/retract được nhóm theo chuyển động an toàn. | Tổ hợp mode có thể khó kiểm chứng. | Chỉ hiện policy áp dụng; safe values kế thừa Setup và có đơn vị. | Sơ đồ, icon và tên mode WorkNC. |
| W07 | *WORKNC 2021 Online Help*, p.593 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/CUTTER_MOVEMENTS/worknc-worknc-2021-online-help-lead-in-out-p0593.png` - Linking | Toolpath-dependent field được xác định rõ. | Giữ field mờ vẫn làm tăng mật độ. | Ẩn field không áp dụng, tooltip ngắn, Help chi tiết riêng. | Dialog Cutter Movements và behavior riêng WorkNC. |
| W08 | *WORKNC 2021 Online Help*, p.764 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/TOOLPATH_PARAMETERS/worknc-worknc-2021-online-help-parallel-standard-p0764.png` - Function Dialog | Tách tham số chung và riêng của Parallel Finishing. | Người dùng phải đổi vùng để hiểu toàn bộ strategy. | Dùng chung section framework; field strategy xuất hiện đúng section. | Dialog hoặc thuật toán Parallel Finishing. |
| W09 | *WORKNC 2021 Online Help*, p.765 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/ADDITIONAL_PARAMETERS/worknc-worknc-2021-online-help-parallel-specific-p0765.png` - Advanced Options | Option strategy-specific có vùng/help riêng. | Có nguy cơ lộ quá nhiều option chuyên gia. | Thuật toán, smoothing, filtering ở Expert và có cảnh báo. | Tham số, default và bố cục WorkNC. |
| W10 | *WORKNC 3-Axis Basic Training Guide 2018 R2*, p.127 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/TOOLPATH_PARAMETERS/worknc-worknc-3-axis-basic-training-guide-2018-r2-parallel-overview-p0127.png` - Function Dialog | Strategy được giải thích cùng Zone, Tolerance và Movement. | Nhiều lựa chọn nâng cao xuất hiện sớm. | Summary strategy và 5-10 Basic input; nhóm khác mở theo nhu cầu. | Dialog, icon và bố cục WorkNC. |
| W11 | *WORKNC 3-Axis Basic Training Guide 2018 R2*, p.128 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/TOOLPATH_PARAMETERS/worknc-worknc-3-axis-basic-training-guide-2018-r2-parallel-overview-p0128.png` - Function Dialog | Minh họa quan hệ giữa option và kết quả toolpath. | Dễ biến help thành form quá dài. | Preview theo ngữ cảnh, help mở riêng, không nhồi hướng dẫn vào editor. | Ảnh/toolpath và wording WorkNC. |
| W12 | *WORKNC 3-Axis Basic Training Guide 2018 R2*, p.225 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/MACHINING_ZONE/worknc-worknc-3-axis-basic-training-guide-2018-r2-machining-zone-training-p0225.png` - Geometry Selection | Machining Zone gom View, Curve và Surface thành một khái niệm. | Hiển thị đồng thời mọi loại giới hạn làm dialog phức tạp. | Chọn loại zone trước rồi mới hiện control tương ứng. | Hình minh họa và layout dialog. |
| W13 | *WORKNC 3-Axis Basic Training Guide 2018 R2*, p.245 | `reference_private/DERIVED/UI_REFERENCE/WORKNC/ADDITIONAL_PARAMETERS/worknc-worknc-3-axis-basic-training-guide-2018-r2-multi-edit-p0245.png` - Advanced Options | Multi-edit nhận biết tham số chung/khác nhau. | Có rủi ro ghi đè ngầm mixed state. | Chỉ bulk edit field tương thích, hiển thị mixed và phạm vi mutation. | Ký hiệu, menu và interaction WorkNC. |

## Kết luận sử dụng tham khảo

- Học từ Mastercam về continuity của workflow và cây quản lý operation.
- Học từ WorkNC về section tham số và progressive disclosure.
- HMS giữ domain, terminology, icon, màu sắc và interaction riêng; không tái tạo
  pixel-by-pixel hoặc dùng lại asset của sản phẩm nguồn.
- Ảnh chỉ là bằng chứng thiết kế cục bộ. Source of truth của quyết định HMS là
  các tài liệu 9A.1 trong Git.
