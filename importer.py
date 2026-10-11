import io
import pandas as pd
from sqlalchemy import text
from db import engine


def _num(v, default=None):
    try:
        if pd.isna(v) or v == "":
            return default
        if isinstance(v, str):
            s=v.strip().replace(",", "")
            if not s or s.startswith("#"):
                return default
            return float(s)
        return float(v)
    except Exception:
        return default


def _rate(v):
    if pd.isna(v) or v == "":
        return None
    try:
        if isinstance(v, str):
            s=v.strip().replace(",", "")
            if s.endswith("%"):
                return float(s[:-1]) / 100
            x=float(s)
        else:
            x=float(v)
        return x / 100 if x > 1 else x
    except Exception:
        return None


def _sku(v):
    if pd.isna(v):
        return ""
    s=str(v).strip()
    if s.endswith(".0"):
        s=s[:-2]
    return s


def _valid_sku(v):
    s=_sku(v)
    return s if s.isdigit() else ""


def _chunks(rows, size=4000):
    for i in range(0, len(rows), size):
        yield rows[i:i+size]


def import_consolidation(uploaded):
    """Import workbook using the confirmed physical column positions.

    Confirmed mapping in Premium_DealSheet Consolidation:
      Raw Data: A SKU, B Product Name, G Brand, S Weighted COGS, Z Cat Code
      Commission: B PV Cat ID, D Packing Fee, K Shopee Mall Commission (current), T CM
      Raw Tồn: A SKU, AM HCM, AN HN, AO MTR/Đà Nẵng.
      Total company is calculated from all inventory locations C:AS; the workbook's
      cached AT "Total PV" values are inconsistent with the location totals.

    Total CostFund is intentionally NOT imported here because the uploaded workbook
    contains broken external-reference values (#REF!). Cost is refreshed from Raw Data;
    Fund is preserved in the persistent cost_fund table and is updated only by the
    dedicated Cost/Fund uploader.
    """
    data=io.BytesIO(uploaded.getvalue())
    xl=pd.ExcelFile(data, engine="openpyxl")
    required={"SHOPEE PREMIUM","Raw Seller Center","Raw Data","Raw Tồn","Commission"}
    missing=required-set(xl.sheet_names)
    if missing:
        raise ValueError("Thiếu sheet: "+", ".join(sorted(missing)))

    # Raw Data: exact Excel columns, independent of header wording/position.
    raw=pd.read_excel(xl,"Raw Data",header=None,usecols="A,B,G,S,Z",dtype=object)
    raw.columns=["sku","product_name","brand","base_cost","cate_code"]
    raw["sku"]=raw["sku"].map(_valid_sku)
    raw=raw[raw["sku"]!=""].drop_duplicates("sku",keep="last")
    raw["cate_code"]=raw["cate_code"].map(_sku)
    raw["base_cost"]=pd.to_numeric(raw["base_cost"],errors="coerce")

    # Seller Center: retain metadata; detect duplicate SKU source rows.
    seller=pd.read_excel(xl,"Raw Seller Center",header=1,dtype=object).rename(columns={
        "SKU PV":"sku","Mã Sản phẩm":"product_id","Mã Phân loại":"model_id",
        "Tên phân loại":"model_name","Giá":"list_price"})
    if "sku" not in seller.columns:
        raise ValueError("Raw Seller Center: không tìm thấy cột SKU PV")
    seller["sku"]=seller["sku"].map(_valid_sku)
    seller=seller[seller["sku"]!=""]
    duplicate_rows=int(seller.duplicated("sku",keep=False).sum())
    seller=seller.drop_duplicates("sku",keep="last")
    for c in ["product_id","model_id","model_name","list_price"]:
        if c not in seller.columns: seller[c]=None

    # Commission: exact columns B/D/K/T.
    com=pd.read_excel(xl,"Commission",header=None,usecols="B,D,K,T",dtype=object)
    com.columns=["cate_code","packing_fee","commission_rate","cm"]
    com["cate_code"]=com["cate_code"].map(_sku)
    # PV Cat IDs are text hierarchy codes such as NH14-01-06-01, not numeric IDs.
    # Keep real NH* category codes and discard title/header rows.
    com=com[com["cate_code"].str.match(r"^NH\d{2}(?:-\d{2}){0,3}$",na=False)]
    com["packing_fee"]=com["packing_fee"].map(lambda x:_num(x,None))
    com["commission_rate"]=com["commission_rate"].map(_rate)
    com["cm"]=com["cm"].map(lambda x: None if pd.isna(x) else str(x).strip() or None)
    com=com.drop_duplicates("cate_code",keep="last")

    # Inventory.
    # AM/AN/AO are the three warehouse totals shown in the source.
    # For "Tổng tồn công ty", calculate the total from all physical inventory
    # location columns C:AS.  The workbook's cached AT "Total PV" is not reliable
    # (e.g. it can be 1 while AM+AN+AO is 264), so it must not be used.
    inv_all=pd.read_excel(xl,"Raw Tồn",header=None,usecols="A,C:AS",dtype=object)
    inv_all["sku"]=inv_all.iloc[:,0].map(_valid_sku)
    inv_all=inv_all[inv_all["sku"]!=""].drop_duplicates("sku",keep="last")

    # In A,C:AS dataframe: original AM/AN/AO map to positions 37/38/39 after SKU.
    # Use original Excel positions by reading these exact columns separately for clarity.
    inv3=pd.read_excel(xl,"Raw Tồn",header=None,usecols="A,AM,AN,AO",dtype=object)
    inv3.columns=["sku","hcm","hn","danang"]
    inv3["sku"]=inv3["sku"].map(_valid_sku)
    inv3=inv3[inv3["sku"]!=""].drop_duplicates("sku",keep="last")
    for c in ["hcm","hn","danang"]:
        inv3[c]=pd.to_numeric(inv3[c],errors="coerce")
    inv3["total_3"]=inv3[["hcm","hn","danang"]].fillna(0).sum(axis=1)

    location_values=inv_all.iloc[:,1:].apply(pd.to_numeric,errors="coerce")
    inv_all["total_company"]=location_values.fillna(0).sum(axis=1)
    inv=inv3.merge(inv_all[["sku","total_company"]],on="sku",how="left")

    # Existing DealSheet daily prices only. Cost/Fund is NOT taken from this sheet.
    prem=pd.read_excel(xl,"SHOPEE PREMIUM",header=2,dtype=object).rename(columns={
        "PV SKU":"sku","Daily Price ":"daily_price","Daily Price":"daily_price"})
    if "sku" in prem.columns:
        prem["sku"]=prem["sku"].map(_valid_sku)
        prem=prem[prem["sku"]!=""].drop_duplicates("sku",keep="last")
    else:
        prem=pd.DataFrame(columns=["sku","daily_price"])
    if "daily_price" not in prem.columns: prem["daily_price"]=None

    merged=raw.merge(seller[["sku","product_id","model_id","model_name","list_price"]],on="sku",how="left")

    products=[]
    costs=[]
    prices=[]
    for _,r in merged.iterrows():
        products.append({
            "sku":r.sku,
            "product_name":None if pd.isna(r.product_name) else str(r.product_name),
            "cate_code":None if not r.cate_code else str(r.cate_code),
            "cate_name":None,
            "brand":None if pd.isna(r.brand) else str(r.brand),
            "cm":None,
            "product_id":None if pd.isna(r.product_id) else _sku(r.product_id),
            "model_id":None if pd.isna(r.model_id) else _sku(r.model_id),
            "model_name":None if pd.isna(r.model_name) else str(r.model_name),
            "list_price":_num(r.list_price,None)
        })
        if not pd.isna(r.base_cost):
            costs.append({"sku":r.sku,"cost":float(r.base_cost)})

    for _,r in prem.iterrows():
        dp=_num(r.daily_price,None)
        if dp is not None and dp>0:
            prices.append({"sku":r.sku,"daily_price":dp})

    commissions=com.to_dict("records")
    inventories=[]
    for _,r in inv.iterrows():
        inventories.append({
            "sku":r.sku,
            "hcm":_num(r.hcm,None),"hn":_num(r.hn,None),"danang":_num(r.danang,None),
            "total_3":_num(r.total_3,0),"total_company":_num(r.total_company,None)
        })

    sql_products="""INSERT INTO products(sku,product_name,cate_code,cate_name,brand,cm,product_id,model_id,model_name,list_price)
      VALUES(:sku,:product_name,:cate_code,:cate_name,:brand,:cm,:product_id,:model_id,:model_name,:list_price)
      ON CONFLICT(sku) DO UPDATE SET product_name=excluded.product_name,cate_code=excluded.cate_code,
      brand=excluded.brand,product_id=excluded.product_id,model_id=excluded.model_id,
      model_name=excluded.model_name,list_price=excluded.list_price"""
    # Important: preserve Fund. Consolidation only refreshes Cost from Raw Data.
    sql_cost="""INSERT INTO cost_fund(sku,cost,fund,updated_at) VALUES(:sku,:cost,NULL,CURRENT_TIMESTAMP)
      ON CONFLICT(sku) DO UPDATE SET cost=excluded.cost,updated_at=CURRENT_TIMESTAMP WHERE cost_fund.manual_override=0"""
    sql_com="""INSERT INTO commission(cate_code,commission_rate,packing_fee,cm)
      VALUES(:cate_code,:commission_rate,:packing_fee,:cm)
      ON CONFLICT(cate_code) DO UPDATE SET commission_rate=excluded.commission_rate,
      packing_fee=excluded.packing_fee,cm=excluded.cm"""
    sql_inv="""INSERT INTO inventory(sku,hcm,hn,danang,total_3,total_company,updated_at)
      VALUES(:sku,:hcm,:hn,:danang,:total_3,:total_company,CURRENT_TIMESTAMP)
      ON CONFLICT(sku) DO UPDATE SET hcm=excluded.hcm,hn=excluded.hn,danang=excluded.danang,
      total_3=excluded.total_3,total_company=excluded.total_company,updated_at=CURRENT_TIMESTAMP"""
    # Consolidation only seeds Daily Price for SKUs not yet present in deals.
    # Existing prices (including manually edited ones) are never overwritten.
    sql_price="""INSERT INTO deals(sku,daily_price) VALUES(:sku,:daily_price)
      ON CONFLICT(sku) DO NOTHING"""

    with engine.begin() as con:
        for sql,rows in [(sql_products,products),(sql_cost,costs),(sql_com,commissions),(sql_inv,inventories),(sql_price,prices)]:
            for chunk in _chunks(rows):
                if chunk: con.execute(text(sql),chunk)
        con.execute(text("""INSERT INTO import_log(source_name,rows_read,rows_saved,note)
          VALUES(:n,:r,:s,:note)"""),{
            "n":uploaded.name,"r":len(raw),"s":len(products),
            "note":f"V1.6.3 mapping; Commission NH category codes fixed; company inventory calculated from C:AS; seller duplicate rows={duplicate_rows}; Fund preserved; existing Daily Price protected"
        })

    return {
        "Products":len(products),
        "Cost from Raw Data":len(costs),
        "Fund":"Preserved / not imported",
        "Commission":len(commissions),
        "Inventory":len(inventories),
        "Daily Price source rows (existing prices preserved)":len(prices),
        "Seller duplicate rows":duplicate_rows
    }
