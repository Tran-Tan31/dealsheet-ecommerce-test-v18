# DealSheet Ecommerce V1.6

Các thay đổi chính:
- Shared DealSheet persistent: danh sách SKU lưu trong PostgreSQL/Supabase.
- Đóng trình duyệt, logout hoặc đổi máy: bảng SKU vẫn còn.
- Daily Price autosave khi chỉnh xong ô; không còn nút Save Daily Price.
- Price History chỉ ghi khi Daily Price thực sự thay đổi.
- Thêm Product ID và Model ID ngay sau SKU.
- Một bảng DealSheet duy nhất, gồm cả fee / GP / Margin / inventory.
- Ẩn/hiện cột tùy ý.
- Preset: Gọn, Chi tiết, Tồn kho, Hiện tất cả cột.
- Cấu hình cột được lưu trong database nên mở máy khác vẫn giữ.
- Clear Shared DealSheet cần xác nhận và chỉ xóa danh sách SKU đang làm việc.
- Pi Ship 2,700 + Infrastructure 3,000 = 5,700.
- Thiếu Cost/Commission/Packing Fee thì Margin để trống, không hiện 100% giả.

## V1.6.1 hotfix
- App tự kiểm tra/tạo `dealsheet_rows` và `ui_preferences` khi khởi động.
- Không cần chạy SQL thủ công trên Supabase.
- Sửa lỗi `sqlalchemy.exc.ProgrammingError / UndefinedTable: ui_preferences`.

## V1.6.2 — Smooth Autosave
- Bỏ `st.rerun()` sau khi sửa Daily Price.
- Daily Price vẫn ghi trực tiếp vào PostgreSQL/Supabase.
- Chỉ xử lý SKU có giá thực sự thay đổi.
- Thêm session acknowledgement để tránh ghi lặp cùng một giá ở các rerun không liên quan.
- Price History vẫn chỉ ghi khi giá thực sự thay đổi.
- Không thay đổi schema Supabase và không cần sửa Streamlit Secrets.

## V1.6.3 — Master Mapping Fix
- Commission join fixed: PV Cat ID is text (`NHxx-xx-xx-xx`), not numeric.
- Packing Fee = Commission column D.
- Shopee Commission = Commission column K (current rate in supplied workbook).
- CM = Commission column T.
- Inventory HCM/HN/MTR = Raw Tồn AM/AN/AO.
- `Tổng tồn công ty` is calculated from all physical inventory location columns C:AS because cached `Total PV` in AT is inconsistent with the source totals.
- Shared DealSheet / Daily Price / autosave remain unchanged.

## V1.6.4 — Daily Price Protection
Consolidation import only inserts a starting Daily Price when the SKU is absent from `deals`. Existing Daily Price, manual edits and Price History remain untouched.
