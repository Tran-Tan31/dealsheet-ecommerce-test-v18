# DealSheet V1.8 Test — triển khai riêng

- Repository: `Tran-Tan31/dealsheet-ecommerce-test-v18` (KHÔNG upload vào Production).
- Streamlit Community Cloud: tạo app riêng, branch `main`, main file `app.py`.
- Secrets của app TEST: `DATABASE_URL` phải trỏ đến Supabase project `dealsheet-test-v18`, không được dùng URL Production. Trong Streamlit Secrets dùng cú pháp `DATABASE_URL = "postgresql://..."` và `COST_FUND_EDITOR_PASSWORD = "..."`. Không commit secrets vào GitHub.
- Database TEST: chạy file SQL schema đã tạo trước đó; hoặc app có logic tạo bảng khi khởi động. Chỉ triển khai khi xác nhận kết nối là project TEST.
- Trong tab Shopee Commission Master, nhập file Excel gốc có sheet `SP CATE CODE`, xem preview rồi xác nhận import.
- Thêm vài SKU thử nghiệm vào DealSheet (cần import dữ liệu sản phẩm thử nghiệm trước), gán ngành hàng và kiểm tra Margin. Không nhập toàn bộ dữ liệu Production khi chưa thử nghiệm.
- Đây là V1.8 Commission phase 1; Cost/Fund vẫn polling 3 giây, chưa chuyển sang Realtime.
