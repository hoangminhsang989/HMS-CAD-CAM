# Chính sách UX tham số CAM tự động

## Mục tiêu

HMS CAM dùng nguyên tắc **tự động trước, chỉ nhập tay khi cần**. Basic không yêu
cầu người dùng nhập các giá trị kỹ thuật có thể suy ra an toàn từ hình học, dao,
Setup và mục tiêu chất lượng. Mọi giá trị tự động phải hiện giá trị hiệu lực,
nguồn, lý do và trạng thái; không được đoán âm thầm.

## Contract dùng chung

Contract nằm tại `hms_cadcam.cam.automatic_parameters` và không phụ thuộc Qt hay
OCP. Mỗi `AutomaticParameterValue` có:

- `mode`: `auto` hoặc `manual`;
- `resolved_value` và `override_value` được giữ tách biệt;
- `source`, `reason`, `policy_version`;
- `dependency_fingerprint`;
- `status`: `resolved`, `needs_confirmation`, `unsupported`, `unresolved`;
- kết quả validation giữ nguyên dữ liệu nhập sai để người dùng sửa.

`effective_fingerprint` bao gồm giá trị hiệu lực, mode, policy version, dependency
fingerprint, status và validation. Vì vậy đổi hình học, dao, Setup, profile,
manual mode hoặc policy đều tạo identity mới.

## Parallel Finishing

Policy hiện thực đầu tiên là `parallel.finishing.automatic`, version 1. Không đổi
Parallel algorithm v3, strategy v1 hay SQLite schema v4.

- Hướng chạy dao: dùng chiều chính dài hơn của hộp bao vùng/mặt đã chọn sau khi
  chiếu vào trục U/V của Setup. Nếu chưa có evidence, HMS hiện rõ “Cần xác nhận”
  và dùng Setup X; không mô tả đây là kết quả phân tích hình học.
- Bước ngang: tính từ đường kính/bán kính dao cầu, hệ số chất lượng có version và
  giới hạn 20.000 lượt cắt. Giá trị không vượt đường kính dao; policy không cam
  kết Ra hoặc chiều cao nhấp nhô chính xác.
- Dung sai: ưu tiên dung sai đã khai báo, sau đó siết theo đường kính dao và
  profile. Dung sai bề mặt không bị trộn với ngưỡng phát hiện nội bộ.
- Lượng dư: giữ lượng dư vùng gia công nếu đã khai báo; nếu không, policy gia công
  tinh dùng 0 mm.
- Thứ tự cắt: ưu tiên zíc zắc khi có hình học để giảm chuyển động không cắt. Mọi
  liên kết vẫn dùng rút dao bảo thủ và phải qua bộ kiểm tra an toàn hiện hữu.
- Liên kết: chỉ công khai `retract_between_segments`; mode chưa hỗ trợ không xuất
  hiện trong UI.
- Holder: summary cho biết holder đã nhận diện hay phạm vi chưa được xác minh.
  Contract không thay thế kiểm tra holder/cutter/shank hiện hữu.

## Hồ sơ chất lượng

- **Nhanh**: bước ngang lớn hơn, dung sai rộng hơn trong giới hạn khai báo.
- **Cân bằng**: mặc định cho nguyên công mới/chưa có metadata tự động.
- **Chất lượng cao**: bước ngang nhỏ hơn và dung sai chặt hơn.

Profile chỉ thay đổi policy tham số. Nó không tự tính toolpath và không thay đổi
thuật toán hay safety validator.

## Basic và Advanced

Basic của Parallel chỉ có bốn nhóm: Hình học, Dao, Chất lượng và Tham số tự động.
Các giá trị hiệu lực luôn kèm nguồn/lý do. Advanced có checkbox “Tùy chỉnh thủ
công” riêng cho hướng, bước ngang, dung sai, lượng dư và thứ tự cắt. Trường nhập
chỉ applicable khi checkbox bật.

Giá trị manual sai được giữ nguyên và chặn Apply. Khi tắt override, trường stale
ẩn không tham gia validation; giá trị tự động mới được dùng, còn giá trị manual
cũ vẫn nằm trong contract để có thể bật lại.

## Persistence và invalidation

Contract JSON được lưu dưới key `automatic_parameter_contract` trong
`OperationParameterSet` primitive hiện có. Đây là sử dụng mở rộng payload tổng
quát đã được schema v1 hỗ trợ, không phải thay đổi serialization schema.

Các giá trị số hiệu lực vẫn nằm ở các key Parallel v1 hiện có để generator không
cần hiểu metadata UX. Decoder Parallel cho phép key metadata này và bỏ qua khi
dựng `ParallelFinishingParameters`.

Input fingerprint của Parallel đã bao gồm toàn bộ `operation.parameters`, nên
contract, mode, effective value, policy và dependencies đều tham gia artifact
compatibility. Apply một thay đổi hiệu lực tăng revision và đánh dấu artifact
DIRTY; Calculate vẫn dùng lifecycle/safety hiện hữu.

## Ranh giới an toàn

Automatic policy không gọi generator, không công bố READY/SAFE, không bỏ qua
validation và không tự sửa giá trị manual sai. Quyết định cuối cùng vẫn qua
`prepare_parallel_update`, Parallel generator, safety report v3, Simulation gate
và Post gate hiện hữu.
