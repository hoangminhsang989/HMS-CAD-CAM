# XCAF STEP technical spike — 6A.1

Spike này tạo fixture STEP assembly bằng OCCT/XCAF trong thư mục tạm và đọc lại
bằng `STEPCAFControl_Reader`. Mọi `TDocStd_Document`, `TDF_Label` và `TopoDS`
được giữ nội bộ trong `XcafStepSession`; kết quả công khai chỉ là model Python
bất biến trong `model.py`.

Spike không tích hợp với domain CAD, UI chính, project database hoặc persistence.
ID trong report chỉ có ý nghĩa trong spike và không phải persistent key sản phẩm.

Chạy kiểm tra headless:

```powershell
.\.venv\Scripts\python.exe -m pytest -q spikes/xcaf_step
```
