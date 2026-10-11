# V1.8 phase 1 — Shopee Commission Master

- Adds `Shopee Commission Master` tab with search, category-level commission edits, XLSX import preview/confirmation, and change history.
- Import uses sheet `SP CATE CODE` from the provided Consolidation workbook. Duplicate identical 3-level category paths are collapsed; conflicting rates are rejected.
- Adds a SKU category selector above DealSheet. The chosen full 3-level category is persisted in Supabase; clearing selection restores default `Commission` table rates.
- DealSheet calculates Commission and Margin from the selected category where available.
- Existing Cost/Fund, Daily Price, import pipeline and 3-second refresh are preserved. Supabase Realtime event sync is NOT part of this phase.
- Admin editing uses existing `COST_FUND_EDITOR_PASSWORD` secret.
- Database tables are created automatically at app startup. Make a database backup before deploying.
- Use the `Chi tiết` preset to show `Shopee Subcate` and `Commission %`.

Note: The SKU category picker is a searchable selectbox (one SKU at a time), not an in-grid editable cell. This reduces accidental bulk edits during polling.
