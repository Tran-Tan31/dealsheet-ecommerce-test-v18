import os
from sqlalchemy import create_engine, text

DB_URL=os.getenv("DATABASE_URL","sqlite:///dealsheet_v1.db")
if DB_URL.startswith("postgres://"):
    DB_URL="postgresql://"+DB_URL[len("postgres://"):]

engine=create_engine(
    DB_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    future=True
)

def _is_postgres():
    return engine.dialect.name=="postgresql"

def init_db():
    pk_auto="BIGSERIAL PRIMARY KEY" if _is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    stmts=[
    """CREATE TABLE IF NOT EXISTS products (
      sku TEXT PRIMARY KEY, product_name TEXT, cate_code TEXT, cate_name TEXT, brand TEXT, cm TEXT,
      product_id TEXT, model_id TEXT, model_name TEXT, list_price REAL
    )""",
    """CREATE TABLE IF NOT EXISTS cost_fund (
      sku TEXT PRIMARY KEY, cost DOUBLE PRECISION, fund DOUBLE PRECISION, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS commission (
      cate_code TEXT PRIMARY KEY, commission_rate REAL, packing_fee REAL, cm TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS inventory (
      sku TEXT PRIMARY KEY, hcm REAL DEFAULT 0, hn REAL DEFAULT 0, danang REAL DEFAULT 0,
      total_3 REAL DEFAULT 0, total_company REAL DEFAULT 0, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS fee_settings (
      id INTEGER PRIMARY KEY, transaction_rate REAL, voucher_rate REAL, voucher_cap REAL,
      infrastructure_fee REAL, pi_ship REAL
    )""",
    """CREATE TABLE IF NOT EXISTS deals (
      sku TEXT PRIMARY KEY, daily_price REAL, margin REAL, est_gp REAL,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS dealsheet_rows (
      sku TEXT PRIMARY KEY,
      sort_order INTEGER NOT NULL DEFAULT 0,
      added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS ui_preferences (
      id INTEGER PRIMARY KEY,
      visible_columns TEXT,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    f"""CREATE TABLE IF NOT EXISTS price_history (
      id {pk_auto}, sku TEXT, daily_price REAL, margin REAL, est_gp REAL,
      changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    f"""CREATE TABLE IF NOT EXISTS import_log (
      id {pk_auto}, source_name TEXT, rows_read INTEGER, rows_saved INTEGER,
      note TEXT, imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    ]
    with engine.begin() as c:
        for stmt in stmts: c.execute(text(stmt))
        count=c.execute(text("SELECT COUNT(*) FROM fee_settings WHERE id=1")).scalar()
        if not count:
            c.execute(text("""INSERT INTO fee_settings
                (id,transaction_rate,voucher_rate,voucher_cap,infrastructure_fee,pi_ship)
                VALUES(1,.06,.055,50000,3000,2700)"""))
        # V1.7.1: migrate existing PostgreSQL REAL (float32) to float64.
        # REAL silently rounds amounts such as 1530541 back to 1530540.
        if _is_postgres():
            c.execute(text("ALTER TABLE cost_fund ALTER COLUMN cost TYPE DOUBLE PRECISION USING cost::double precision"))
            c.execute(text("ALTER TABLE cost_fund ALTER COLUMN fund TYPE DOUBLE PRECISION USING fund::double precision"))
        # Confirmed project rule.
        c.execute(text("UPDATE fee_settings SET infrastructure_fee=3000,pi_ship=2700 WHERE id=1"))

def settings():
    with engine.begin() as c:
        return dict(c.execute(text("SELECT * FROM fee_settings WHERE id=1")).mappings().one())

def db_status():
    return {
        "dialect":engine.dialect.name,
        "shared":engine.dialect.name=="postgresql",
        "url_mode":"DATABASE_URL" if os.getenv("DATABASE_URL") else "local fallback"
    }
