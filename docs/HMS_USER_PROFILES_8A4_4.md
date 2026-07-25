# HMS user profiles — Stage 8A.4.4

## Mô hình và vị trí

Profile HMS là cài đặt giao diện của nhiều người dùng chung một Windows account;
không phải tài khoản, đăng nhập, xác thực hay phân quyền. Dữ liệu nằm dưới
`%APPDATA%\HMS-CADCAM\Profiles\`, không nằm trong ProgramData, project hay `.HMS`.

`profiles.json` schema v1 giữ active/default ID, index và checksum. Mỗi physical
directory dùng UUID canonical, còn `display_name` giữ Unicode. Một profile có
`profile.json`, `ui-state.json`, `shortcuts.json`, `quick-access.json`,
`preferences.json` và `recent-files.json`; mỗi document có schema/profile ID và
checksum. Mọi publication dùng atomic writer và read-after-write validation.

Khi chưa có profile, HMS tạo `Mặc định` theo locale hiện hành và nhập layout UI
đã restore từ Stage 8A.4.3. Luôn phải còn ít nhất một profile; active/default ID
phải tồn tại. Không xóa profile cuối hoặc active profile khi chưa chỉ định profile
thay thế.

## Quản lý và backup

`Cài đặt → Giao diện → Profile người dùng` hỗ trợ chọn, tạo, sao chép, đổi tên,
đặt mặc định, xóa, xuất và nhập qua `.BAKUPHMS`. Đổi tên chỉ đổi display name;
sao chép sinh UUID mới. Dialog hiển thị locale, updated time, active/default,
số shortcut tùy chỉnh và mô tả layout.

Backup cho chọn một hoặc nhiều profile và component UI/settings/shortcuts/Quick
Access/recent files. Profile ID được giữ nếu không xung đột; import-as-copy sinh
ID mới. Profile vừa restore không tự thành active.

## Chuyển profile runtime

Service validate target, capture và lưu profile hiện tại, chụp invariant, apply
locale/shortcut/Quick Access/geometry/dock/ribbon/toolbar, clamp cửa sổ, validate
lại rồi mới cập nhật active ID. Locale dùng `VI_VN`, `EN_US` hoặc `KO_KR` và UI
retranslate qua catalog hiện hành.

Shortcut lưu stable command ID, được kiểm command tồn tại, sequence hợp lệ,
reserved key và conflict trước apply. Quick Access cũng chỉ lưu command ID; ID
không còn tồn tại bị bỏ qua, không render raw ID và không lưu callback/code.

Invariant bảo vệ active workspace, document/project identity, dirty state, CAD
source/selection, CAM selection/editor operation, Parallel/Z-Level worker,
Simulation handle/project, Post tab và project session lock. Chuyển profile không
Save project, Calculate, Simulation hay Post. Apply lỗi sẽ reapply profile trước,
giữ active ID và báo lỗi không modal.

## Giới hạn chủ ý

Không có profile authentication, Windows account management, password/secret,
cloud sync hay authorization. Recent files là tùy chọn riêng tư và mặc định không
backup. Profile không thay thế project preferences hoặc machine-wide policy.
