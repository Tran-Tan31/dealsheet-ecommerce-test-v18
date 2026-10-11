
import io
import pandas as pd
from sqlalchemy import text
from db import engine

SKU_NAMES = ["SKU","PV SKU","sku","Sku"]
COST_NAMES = ["Cost","COST","Unit Cost","Adjust Unit Cost","cost"]
FUND_NAMES = ["Fund","FUND","Aujust Fund","Adjust Fund","fund"]

def _pick(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None

def _sku(v):
    if pd.isna(v): return ""
    s=str(v).strip()
    return s[:-2] if s.endswith(".0") else s

def import_cost_fund(uploaded):
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data), dtype=object)
    else:
        df = pd.read_excel(io.BytesIO(data), dtype=object)

    sku_col=_pick(df.columns,SKU_NAMES)
    cost_col=_pick(df.columns,COST_NAMES)
    fund_col=_pick(df.columns,FUND_NAMES)
    if not sku_col or not cost_col or not fund_col:
        raise ValueError(
            "File Cost/Fund cần có 3 cột: SKU, Cost, Fund. "
            f"Cột hiện có: {', '.join(map(str,df.columns[:30]))}"
        )

    out=df[[sku_col,cost_col,fund_col]].copy()
    out.columns=["sku","cost","fund"]
    out["sku"]=out["sku"].map(_sku)
    out=out[out["sku"]!=""]
    dupes=int(out.duplicated("sku",keep=False).sum())
    out=out.drop_duplicates("sku",keep="last")
    out["cost"]=pd.to_numeric(out["cost"],errors="coerce")
    out["fund"]=pd.to_numeric(out["fund"],errors="coerce").fillna(0)

    bad_cost=int(out["cost"].isna().sum())
    valid=out[out["cost"].notna()].copy()
    rows=valid.to_dict("records")

    with engine.begin() as con:
        if rows:
            con.execute(text("""
                INSERT INTO cost_fund(sku,cost,fund,updated_at)
                VALUES(:sku,:cost,:fund,CURRENT_TIMESTAMP)
                ON CONFLICT(sku) DO UPDATE SET
                    cost=excluded.cost,
                    fund=excluded.fund,
                    updated_at=CURRENT_TIMESTAMP
                WHERE cost_fund.manual_override=0
            """),rows)
        con.execute(text("""
            INSERT INTO import_log(source_name,rows_read,rows_saved,note)
            VALUES(:n,:r,:s,:note)
        """),{
            "n":uploaded.name,
            "r":len(df),
            "s":len(valid),
            "note":f"Cost/Fund persistent update; duplicate rows={dupes}; invalid cost={bad_cost}"
        })
    return {
        "Rows read":len(df),
        "Unique SKU saved":len(valid),
        "Duplicate SKU rows":dupes,
        "Rows skipped - invalid Cost":bad_cost
    }
