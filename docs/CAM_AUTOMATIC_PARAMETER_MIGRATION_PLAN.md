# Kế hoạch mở rộng UX tham số CAM tự động

## Phạm vi hiện tại

Chỉ Parallel Finishing đã dùng shared contract. Tài liệu này là kế hoạch cho các
operation khác; không tuyên bố các operation đó đã được chuyển đổi.

## Điều kiện trước khi migrate một strategy

1. Domain model và generator của strategy phải ổn định, có validation và input
   fingerprint đầy đủ.
2. Phải xác định được evidence OCP-free cần cho policy; tác vụ native/nặng không
   được chạy trong UI thread.
3. Phải liệt kê rõ tham số có thể auto, tham số bắt buộc manual và capability chưa
   hỗ trợ.
4. Nếu persistence không thể dùng `OperationParameterSet` schema hiện có, phải
   dừng để thiết kế migration/version riêng; không chèn payload không tương thích.

## Trình tự migrate

1. Viết policy thuần, deterministic, nhận immutable evidence và trả
   `AutomaticParameterContract`.
2. Mỗi parameter khai báo dependency fingerprint tối thiểu: geometry, tool,
   holder, machine/Setup, stock/project tolerance và quality profile tùy ý nghĩa.
3. Thêm ba profile Nhanh/Cân bằng/Chất lượng cao với giới hạn domain rõ ràng.
4. Giữ Basic ở mức chọn hình học, chọn dao, profile và auto summary. Đưa override
   vào Advanced bằng applicability rule; không tạo editor riêng.
5. Map effective values trở lại parameter key hiện hữu trước generator. Không để
   generator phụ thuộc metadata trình bày.
6. Lưu contract trong payload hiện hữu nếu schema cho phép; chứng minh round-trip,
   Save/Open, Duplicate và backward compatibility.
7. Chứng minh thay đổi dependency/mode/policy làm artifact stale hoặc DIRTY theo
   lifecycle hiện hữu.
8. Chạy runtime audit tiếng Việt, keyboard/resize/DPI review và bộ test đầy đủ.

## Thứ tự đề xuất

- Facing/Planar Facing: auto stepover/overtravel/tolerance từ stock, dao và bounds.
- Contour/Pocket: auto stepdown/stepover/linking từ profile, dao, stock và topology
  sau khi resolver hình học đã cung cấp evidence ổn định.
- Drilling family: auto cycle/retract/peck từ hole evidence, tool và machine
  capability; không tự chọn cycle máy chưa hỗ trợ.
- Reaming/Boring/Tapping: chỉ auto các giá trị có công thức và capability được xác
  minh; fit, thread và kích thước chức năng vẫn cần intent rõ của người dùng.

## Phân nhóm audit editor hiện có

### Có thể tự động ngay bằng contract hiện có

- Facing/Planar Facing: stepover, overtravel và tolerance khi stock, bounds và dao
  đã resolve.
- Parallel: đã triển khai trong Stage 8A.2.3.

### Cần thêm domain contract

- Drilling family: cycle/peck/retract cần machine capability và material/cutting
  data có provenance; không dùng feed/spindle giả.
- Reaming/Boring/Tapping: fit, pitch/hand, finished diameter và cycle semantics
  phải tách rõ intent với giá trị dẫn xuất.

### Cần thêm geometry analysis

- Contour: hướng loop, lead placement và linking cần topology/collision evidence.
- Pocket: rest region, island connectivity và linking cost cần resolver ổn định.
- Facing trên face phức tạp: principal extent/stock envelope phải có evidence
  OCP-free trước khi policy chạy.

### Không được tự động vì là ý định công nghệ

- vật liệu khi project chưa khai báo;
- allowance đặc biệt, vùng cấm gia công, clamp/fixture location;
- production feed/spindle/coolant khi thiếu dữ liệu cắt có nguồn;
- chọn machine/Post, machine travel và production clearance chưa xác minh.

## Test bắt buộc cho mỗi strategy

- deterministic cùng input;
- profile monotonic và guardrail;
- manual valid/invalid, toggle manual → auto và giá trị stale ẩn;
- geometry/tool/holder/Setup/profile/policy invalidation;
- effective fingerprint khác khi mode khác dù số hiệu lực bằng nhau;
- payload round-trip, Save/Open, Duplicate và dữ liệu cũ không metadata;
- Apply không tự Calculate; safety/generator fail-closed không bị thay thế;
- Basic field/section count, Advanced override, keyboard, resize, DPI và tiếng Việt.

## Không làm trong migration UX

Không tăng algorithm/strategy/schema version, không thêm linking/cycle chưa hỗ trợ,
không nới safety validator và không biến auto policy thành CAM algorithm mới. Mọi
thay đổi thuộc các nhóm này phải là stage kiến trúc độc lập có migration riêng.
