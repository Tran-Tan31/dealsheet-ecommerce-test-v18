# Deploy V1.4 lên Streamlit Community Cloud + Supabase/PostgreSQL

## 1. Tạo database
Tạo một project PostgreSQL/Supabase. Trong phần database connection, lấy connection string dành cho app/server.
Không đưa password vào code hoặc GitHub.

## 2. DATABASE_URL
Dạng:
postgresql://USER:PASSWORD@HOST:PORT/postgres

Nếu nhà cung cấp cho chuỗi `postgres://...`, app sẽ tự đổi sang `postgresql://...`.

## 3. GitHub
Đưa thư mục `dealsheet_v1` lên một private GitHub repository.
Không upload file `.streamlit/secrets.toml` có password thật.
File `.streamlit/secrets.toml.example` chỉ là mẫu.

## 4. Streamlit Cloud
- New app
- Chọn repository
- Main file: app.py
- App Settings -> Secrets
- Thêm:
  DATABASE_URL = "postgresql://..."

## 5. Chạy
App tự tạo bảng database lần đầu.
Khi góc đầu trang hiện:
`Shared Database: PostgreSQL`
thì 1-4 người đang dùng chung một database.

## 6. Import dữ liệu
Vào Data Update:
1. Import Consolidation.
2. Upload Cost/Fund nguồn thật nếu có.
3. Kiểm tra Fee Settings:
   Transaction 6%
   Voucher 5.5%, cap 50,000
   Infrastructure 3,000
   Pi Ship 2,700

## 7. Lưu ý concurrent users
V1.4 dùng database chung, nên người A lưu Daily Price thì người B Refresh SKU sẽ thấy giá mới.
Đây chưa phải Google-Sheets realtime cell collaboration; hai người sửa cùng đúng một SKU tại cùng thời điểm thì lần Save sau cùng sẽ thắng.
