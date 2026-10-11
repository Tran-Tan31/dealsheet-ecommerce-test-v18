# V1.7.5 – Reduced-flicker sync

- The SKU input, column presets and column picker are no longer inside the 3-second polling fragment.
- Only DealSheet results, filters, metrics and grid update on the polling cycle.
- Adds a style override to reduce the grey/dim stale effect during fragment refresh.
- Cost/Fund edits remain persisted in Supabase; DealSheet still polls every 3 seconds.
- This is **not** fully background push/WebSocket synchronization. Streamlit may still briefly redraw the table.

Deploy: replace files from `dealsheet_v1/` into the GitHub repository root. No new Secrets or SQL migrations required.
