# CAM Application và UI Foundation 7B.1

## Phạm vi đã triển khai

`CamApplicationService` tiếp tục sở hữu một `CamProjectSnapshot` native-free dưới
`RLock`. Facade mới cung cấp mutation theo domain ID cho Job, Setup, WCS, WorkOffset,
Stock, Fixture, operation tree và project-owned Tool/Holder/Assembly/Machine. Job
được clone trước mutation; domain validation thất bại không thay snapshot hiện hành.
Selection CAM dùng ID bất biến và project generation để bỏ callback đến muộn.

`ProjectService.execute_cam_command()` là lifecycle gateway duy nhất: mutation thành
công được đưa vào `ProjectSession.cam_snapshot` và làm dirty; Save, Save As, Autosave,
Recovery, Open và Close vẫn dùng transaction/lifecycle v4 hiện có. UI không mở SQLite
và không có đường Save CAM riêng.

## Workspace và editor

MainWindow giữ nguyên CAD viewport ở trung tâm và bổ sung CAM Workspace dạng dock có
command area, cây CAM và properties editor. Cây lưu domain ID trong Qt UserRole,
render lại theo snapshot, giữ selection theo ID, có guard chống selection loop và
project generation chống signal cũ. Cây hiển thị Job, Setup, Stock, Fixtures, Group,
Operation cùng MISSING/DIRTY/COMPUTING/VALID/FAILED, disabled và cảnh báo thiếu/stale
tool, machine hoặc geometry.

Người dùng có thể tạo/đổi tên/xóa/sắp xếp Job, Setup, Group và generic placeholder
Operation; chỉnh Setup kind, WCS origin, WorkOffset và BOX/CYLINDER stock. Editor chỉ
commit bằng nút **Áp dụng**; giá trị không hợp lệ được domain từ chối, hiển thị lỗi và
tree được dựng lại từ snapshot trước mutation. Bundle tối thiểu tạo end mill, holder,
tool assembly và máy MILL project-owned; operation mới dùng snapshot này khi có.

## Geometry picking

Adapter `cam_geometry_adapter` chỉ nhận metadata viewer native-free và persistent CAD
map. Mapping thiếu/mơ hồ, nguồn khác hoặc selector rỗng bị từ chối. XCAF repeated
occurrence giữ occurrence path riêng. Bind, rebind và clear đều là lệnh explicit;
không có heuristic tìm face gần giống và reference chưa được thuật toán sử dụng.

## Giới hạn có chủ ý

Generic operation luôn bắt đầu ở `MISSING`; UI không giả lập artifact `VALID` và không
chạy background compute. Chưa có Pocket, Contour, Facing, Drilling/Turning algorithm,
toolpath generation, simulation, collision, stock removal, IK, machine animation,
Post Processor hoặc G-code. Editor tooling/machine 7B.1 chỉ tạo bundle cơ bản, chưa là
thư viện toàn cục hay trình biên tập hình học nâng cao.
