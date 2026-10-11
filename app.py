import os
import json
import hmac
import hashlib
import streamlit as st
import pandas as pd

try:
    if "DATABASE_URL" in st.secrets and not os.getenv("DATABASE_URL"):
        os.environ["DATABASE_URL"]=st.secrets["DATABASE_URL"]
except Exception:
    pass

from sqlalchemy import text
from db import engine, init_db, settings, db_status
from importer import import_consolidation
from cost_fund_importer import import_cost_fund

st.set_page_config(page_title="DealSheet Ecommerce", page_icon="📊", layout="wide")

def ensure_v161_schema():
    """Self-migration for V1.6.1 so GitHub deploy does not require manual Supabase SQL."""
    init_db()
    with engine.begin() as con:
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS dealsheet_rows (
                sku TEXT PRIMARY KEY,
                sort_order INTEGER NOT NULL DEFAULT 0,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS ui_preferences (
                id INTEGER PRIMARY KEY,
                visible_columns TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

ensure_v161_schema()
with engine.begin() as con:
    con.execute(text("""CREATE TABLE IF NOT EXISTS cost_fund_history (
        id BIGSERIAL PRIMARY KEY, sku TEXT NOT NULL, field TEXT NOT NULL,
        old_value DOUBLE PRECISION, new_value DOUBLE PRECISION, edited_by TEXT NOT NULL,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""" if engine.dialect.name=="postgresql" else """CREATE TABLE IF NOT EXISTS cost_fund_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL, field TEXT NOT NULL,
        old_value DOUBLE PRECISION, new_value DOUBLE PRECISION, edited_by TEXT NOT NULL,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""))
    if engine.dialect.name=="postgresql":
        con.execute(text("ALTER TABLE cost_fund_history ALTER COLUMN old_value TYPE DOUBLE PRECISION USING old_value::double precision"))
        con.execute(text("ALTER TABLE cost_fund_history ALTER COLUMN new_value TYPE DOUBLE PRECISION USING new_value::double precision"))
    con.execute(text("ALTER TABLE cost_fund ADD COLUMN IF NOT EXISTS manual_override INTEGER NOT NULL DEFAULT 0") if engine.dialect.name=="postgresql" else text("SELECT 1"))
    if engine.dialect.name!="postgresql":
        columns=[r[1] for r in con.execute(text("PRAGMA table_info(cost_fund)")).fetchall()]
        if "manual_override" not in columns:
            con.execute(text("ALTER TABLE cost_fund ADD COLUMN manual_override INTEGER NOT NULL DEFAULT 0"))

# V1.8: Shopee category commission catalog and SKU-level overrides.
with engine.begin() as con:
    con.execute(text("""CREATE TABLE IF NOT EXISTS shopee_categories (
        category_key TEXT PRIMARY KEY, level1 TEXT NOT NULL, level2 TEXT NOT NULL,
        level3 TEXT NOT NULL, commission_rate DOUBLE PRECISION NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""))
    con.execute(text("""CREATE TABLE IF NOT EXISTS shopee_sku_category (
        sku TEXT PRIMARY KEY, category_key TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""))
    con.execute(text("""CREATE TABLE IF NOT EXISTS shopee_commission_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, category_key TEXT NOT NULL,
        old_rate DOUBLE PRECISION, new_rate DOUBLE PRECISION NOT NULL,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""" if engine.dialect.name!="postgresql" else """CREATE TABLE IF NOT EXISTS shopee_commission_history (
        id BIGSERIAL PRIMARY KEY, category_key TEXT NOT NULL,
        old_rate DOUBLE PRECISION, new_rate DOUBLE PRECISION NOT NULL,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""))

def category_key(a,b,c):
    # Full 3-level path prevents collisions such as repeated 'Khác'.
    return json.dumps([str(a).strip(),str(b).strip(),str(c).strip()],ensure_ascii=False)

def category_catalog():
    with engine.begin() as con:
        return pd.read_sql(text("SELECT * FROM shopee_categories ORDER BY level1,level2,level3"),con)

def save_category(con,a,b,c,rate):
    k=category_key(a,b,c)
    old=con.execute(text("SELECT commission_rate FROM shopee_categories WHERE category_key=:k"),{"k":k}).scalar()
    if old is not None and abs(float(old)-float(rate))<1e-10: return False
    con.execute(text("""INSERT INTO shopee_categories(category_key,level1,level2,level3,commission_rate,updated_at)
        VALUES(:k,:a,:b,:c,:r,CURRENT_TIMESTAMP)
        ON CONFLICT(category_key) DO UPDATE SET commission_rate=excluded.commission_rate,updated_at=CURRENT_TIMESTAMP"""),
        {"k":k,"a":a,"b":b,"c":c,"r":rate})
    con.execute(text("INSERT INTO shopee_commission_history(category_key,old_rate,new_rate) VALUES(:k,:o,:r)"),{"k":k,"o":old,"r":rate})
    return True

def parse_shopee_excel(file):
    book=pd.ExcelFile(file)
    if "SP CATE CODE" not in book.sheet_names:
        raise ValueError("Không tìm thấy sheet 'SP CATE CODE'.")
    data=pd.read_excel(book,sheet_name="SP CATE CODE")
    required=["Ngành hàng cấp 1","Ngành hàng cấp 2","Ngành hàng cấp 3","Phí cố định sau khi chiết khấu áp dụng"]
    if not all(x in data.columns for x in required):
        raise ValueError("Thiếu cột ngành hàng hoặc cột phí trong sheet SP CATE CODE.")
    data=data[required].copy()
    data.columns=["level1","level2","level3","rate"]
    data[["level1","level2"]]=data[["level1","level2"]].ffill()
    for c in ["level1","level2","level3"]:
        data[c]=data[c].fillna("").astype(str).str.strip()
    data["rate"]=pd.to_numeric(data["rate"],errors="coerce")
    data=data[(data["level1"]!="")&(data["level2"]!="")&data["rate"].notna()]
    if ((data["rate"]<0)|(data["rate"]>1)).any():
        raise ValueError("Phí phải là tỷ lệ dạng 0.055 cho 5.5%, không phải 5.5.")
    data["category_key"]=data.apply(lambda r:category_key(r.level1,r.level2,r.level3),axis=1)
    conflicts=data.groupby("category_key")["rate"].nunique()
    if (conflicts>1).any():
        raise ValueError("Có ngành hàng cùng đường dẫn nhưng khác mức phí; cần kiểm tra thủ công.")
    # Source workbook has repeated identical paths; collapse only identical-rate duplicates.
    return data.drop_duplicates(subset=["category_key"]).reset_index(drop=True)

ALL_COLUMNS = [
    "sku","product_id","model_id","product_name","brand","cm",
    "cost","fund","cost_after_fund","packing_fee","final_cost","daily_price",
    "shopee_subcate","commission_rate","commission_fee","transaction_fee","voucher_extra","fixed_fee",
    "total_fee","net_revenue","est_gp","margin_pct",
    "hcm","hn","danang","total_3","total_company"
]
LABELS = {
    "sku":"SKU","product_id":"Product ID","model_id":"Model ID","product_name":"Product Name",
    "brand":"Brand","cm":"CM","cost":"Cost","fund":"Fund","cost_after_fund":"Cost - Fund",
    "packing_fee":"Packing Fee","final_cost":"Final Cost","daily_price":"Daily Price ✏️",
    "shopee_subcate":"Shopee Subcate","commission_rate":"Commission %","commission_fee":"Commission Fee",
    "transaction_fee":"Transaction Fee","voucher_extra":"Voucher Extra",
    "fixed_fee":"Pi Ship + Hạ tầng","total_fee":"Total Fee","net_revenue":"Net Revenue",
    "est_gp":"Est GP","margin_pct":"Margin %","hcm":"Kho HCM","hn":"Kho HN",
    "danang":"Kho Đà Nẵng/MTR","total_3":"Tổng tồn 3 kho","total_company":"Tổng tồn công ty"
}
PRESETS = {
    "Gọn":["sku","product_id","model_id","product_name","brand","cm","daily_price","margin_pct","total_3","total_company"],
    "Chi tiết":ALL_COLUMNS,
    "Tồn kho":["sku","product_id","model_id","product_name","brand","hcm","hn","danang","total_3","total_company"],
}

def n(v, default=0.0):
    try:
        if pd.isna(v) or v is None or v=="": return default
        return float(v)
    except Exception:
        return default

def clean_sku(v):
    if pd.isna(v): return ""
    s=str(v).strip()
    return s[:-2] if s.endswith(".0") else s

def calc(row,cfg):
    price=n(row.get("daily_price"),None)
    cost=n(row.get("cost"),None)
    fund=n(row.get("fund"),None)
    pack=n(row.get("packing_fee"),None)
    com=n(row.get("commission_rate"),None)

    cost_after_fund=(cost-fund) if cost is not None and fund is not None else None
    final_cost=(cost_after_fund+pack) if cost_after_fund is not None and pack is not None else None
    commission_fee=(price*com) if price is not None and com is not None else None
    transaction_fee=(price*cfg["transaction_rate"]) if price is not None else None
    voucher_extra=min(price*cfg["voucher_rate"],cfg["voucher_cap"]) if price is not None else None
    fixed_fee=cfg["infrastructure_fee"]+cfg["pi_ship"]

    complete=price is not None and final_cost is not None and commission_fee is not None
    total_fee=(commission_fee+transaction_fee+voucher_extra+fixed_fee) if complete else None
    net=(price-total_fee) if complete else None
    margin=(1-final_cost/net) if complete and net and net>0 else None
    gp=(net-final_cost) if complete else None
    return pd.Series({
        "cost_after_fund":cost_after_fund,"final_cost":final_cost,
        "commission_fee":commission_fee,"transaction_fee":transaction_fee,
        "voucher_extra":voucher_extra,"fixed_fee":fixed_fee,"total_fee":total_fee,
        "net_revenue":net,"est_gp":gp,"margin_pct":margin*100 if margin is not None else None,
        "_margin":margin
    })

def parse_input(raw):
    rows=[]; invalid=[]
    for line in raw.splitlines():
        line=line.strip()
        if not line: continue
        parts=line.replace(","," ").split()
        s=clean_sku(parts[0]); price=None
        if len(parts)>1:
            try: price=float(parts[1].replace(",",""))
            except Exception: invalid.append(line)
        if s: rows.append((s,price))
    return rows,invalid

def add_to_shared_dealsheet(rows):
    if not rows: return 0,0
    unique={}
    for sku,price in rows:
        unique[sku]=price
    skus=list(unique.keys())
    with engine.begin() as con:
        current=con.execute(text("SELECT COALESCE(MAX(sort_order),0) FROM dealsheet_rows")).scalar() or 0
        added=0
        for i,sku in enumerate(skus,1):
            exists=con.execute(text("SELECT 1 FROM dealsheet_rows WHERE sku=:s"),{"s":sku}).scalar()
            if not exists:
                con.execute(text("""INSERT INTO dealsheet_rows(sku,sort_order,added_at,updated_at)
                    VALUES(:s,:o,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""),{"s":sku,"o":current+i})
                added+=1
            price=unique[sku]
            if price is not None:
                _save_price(con,sku,price,write_history=True)
    return added,len(rows)-len(skus)

def shared_skus():
    with engine.begin() as con:
        return [r[0] for r in con.execute(text(
            "SELECT sku FROM dealsheet_rows ORDER BY sort_order, added_at, sku"
        )).fetchall()]

def get_deals(skus):
    if not skus: return pd.DataFrame()
    params={f"s{i}":s for i,s in enumerate(skus)}
    ph=",".join(":"+k for k in params)
    q=f"""SELECT p.sku,p.product_id,p.model_id,p.product_name,p.brand,
    COALESCE(p.cm,c.cm) cm,p.cate_code,p.cate_name,p.list_price,
    cf.cost,cf.fund,c.packing_fee,
    CASE WHEN sc.category_key IS NOT NULL THEN sc.commission_rate ELSE c.commission_rate END commission_rate,
    sc.level3 shopee_subcate,
    i.hcm,i.hn,i.danang,i.total_3,i.total_company,d.daily_price
    FROM products p
    LEFT JOIN cost_fund cf ON cf.sku=p.sku
    LEFT JOIN commission c ON c.cate_code=p.cate_code
    LEFT JOIN shopee_sku_category sk ON sk.sku=p.sku
    LEFT JOIN shopee_categories sc ON sc.category_key=sk.category_key
    LEFT JOIN inventory i ON i.sku=p.sku
    LEFT JOIN deals d ON d.sku=p.sku
    WHERE p.sku IN ({ph})"""
    with engine.begin() as con:
        df=pd.read_sql(text(q),con,params=params)
    rank={s:i for i,s in enumerate(skus)}
    if not df.empty:
        df["_rank"]=df["sku"].astype(str).map(rank)
        df=df.sort_values("_rank").drop(columns="_rank").reset_index(drop=True)
    return df

def _save_price(con,sku,price,write_history=True,row=None):
    old=con.execute(text("SELECT daily_price FROM deals WHERE sku=:s"),{"s":sku}).scalar()
    oldn=None if old is None else float(old)
    newn=None if price is None or pd.isna(price) else float(price)
    if newn is None:
        return False
    if oldn is not None and abs(oldn-newn)<0.000001:
        return False

    margin=None; gp=None
    if row is not None:
        out=calc(row,settings())
        margin=None if pd.isna(out["_margin"]) else float(out["_margin"])
        gp=None if pd.isna(out["est_gp"]) else float(out["est_gp"])

    con.execute(text("""INSERT INTO deals(sku,daily_price,margin,est_gp,updated_at)
        VALUES(:s,:p,:m,:g,CURRENT_TIMESTAMP)
        ON CONFLICT(sku) DO UPDATE SET daily_price=excluded.daily_price,
        margin=excluded.margin,est_gp=excluded.est_gp,updated_at=CURRENT_TIMESTAMP"""),
        {"s":sku,"p":newn,"m":margin,"g":gp})
    if write_history:
        con.execute(text("""INSERT INTO price_history(sku,daily_price,margin,est_gp)
            VALUES(:s,:p,:m,:g)"""),{"s":sku,"p":newn,"m":margin,"g":gp})
    return True

def autosave_prices(original,edited):
    """Persist only Daily Price cells that actually changed.

    No st.rerun() is triggered here.  A small session cache prevents the same
    editor value from being re-processed during unrelated Streamlit reruns.
    """
    if edited.empty:
        return 0

    original_map=dict(zip(original["sku"].astype(str),original["daily_price"]))
    acknowledged=st.session_state.setdefault("_ack_daily_price", {})
    saved=0

    with engine.begin() as con:
        for _,r in edited.iterrows():
            sku=str(r["sku"])
            new=r.get("daily_price")
            old=original_map.get(sku)

            oldn=None if pd.isna(old) else float(old)
            newn=None if pd.isna(new) else float(new)

            # If this exact UI value was already acknowledged, do nothing.
            if newn is not None and acknowledged.get(sku) == newn:
                continue

            changed=(oldn is None and newn is not None) or (
                oldn is not None and newn is not None and abs(oldn-newn)>0.000001
            )
            if changed and _save_price(con,sku,newn,True,r):
                acknowledged[sku]=newn
                saved+=1

    return saved

def load_visible_columns():
    with engine.begin() as con:
        raw=con.execute(text("SELECT visible_columns FROM ui_preferences WHERE id=1")).scalar()
    if not raw: return PRESETS["Gọn"]
    try:
        cols=json.loads(raw)
        return [c for c in ALL_COLUMNS if c in cols] or PRESETS["Gọn"]
    except Exception:
        return PRESETS["Gọn"]

def save_visible_columns(cols):
    payload=json.dumps(cols,ensure_ascii=False)
    with engine.begin() as con:
        con.execute(text("""INSERT INTO ui_preferences(id,visible_columns,updated_at)
            VALUES(1,:v,CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET visible_columns=excluded.visible_columns,
            updated_at=CURRENT_TIMESTAMP"""),{"v":payload})

def clear_shared_dealsheet():
    with engine.begin() as con:
        con.execute(text("DELETE FROM dealsheet_rows"))


# Keep the currently displayed data readable while Streamlit refreshes a fragment.
# The server-side fragment still polls; this is not a websocket-based realtime feed.
st.markdown("""<style>
[data-stale="true"], .stale, [data-testid="stAppViewContainer"] .stale {
    opacity: 1 !important; filter: none !important;
}
</style>""", unsafe_allow_html=True)

st.title("📊 DealSheet Ecommerce")
st.caption("Shared DealSheet: danh sách SKU và Daily Price được lưu trên Supabase, mở máy khác vẫn còn.")
_status=db_status()
if _status["shared"]:
    st.success("🟢 Shared Database: PostgreSQL — dữ liệu DealSheet dùng chung cho team.")
else:
    st.warning("🟡 Local Database: SQLite — chỉ dùng để test.")

# Cost/Fund changes are processed before DealSheet is queried on each rerun.
tab1,tab_cf,tab_sc,tab2,tab3,tab4=st.tabs(["📋 DealSheet","💰 Cost & Fund Master","🛒 Shopee Commission Master","📥 Data Update","⚙️ Fee Settings","🕘 Price History"])

with tab_sc:
    st.subheader("Shopee Commission Master")
    st.caption("Biểu phí theo đủ 3 cấp ngành hàng; cập nhật tại đây sẽ tự áp dụng cho SKU đã liên kết.")
    catalog=category_catalog()
    st.metric("Ngành hàng đang lưu",len(catalog))
    pwd=st.secrets.get("COST_FUND_EDITOR_PASSWORD","")
    if pwd and not st.session_state.get("shopee_editor_authorized",False):
        with st.form("shopee_login"):
            candidate=st.text_input("Mật khẩu quản trị",type="password")
            if st.form_submit_button("Mở quyền chỉnh sửa"):
                if hmac.compare_digest(str(candidate),str(pwd)):
                    st.session_state["shopee_editor_authorized"]=True
                    st.rerun()
                else: st.error("Sai mật khẩu")
    can_edit=bool(pwd and st.session_state.get("shopee_editor_authorized"))
    if not pwd: st.warning("Cần cấu hình COST_FUND_EDITOR_PASSWORD để bật quyền chỉnh sửa.")
    search=st.text_input("Tìm ngành hàng (cấp 1 / 2 / 3)",key="shopee_master_search")
    filtered=catalog
    if search and not catalog.empty:
        mask=catalog[["level1","level2","level3"]].fillna("").astype(str).apply(
            lambda x:x.str.contains(search,case=False,regex=False)).any(axis=1)
        filtered=catalog[mask]
    if not filtered.empty:
        shown=filtered.head(200).copy()
        shown["commission_percent"]=shown["commission_rate"]*100
        st.dataframe(shown[["level1","level2","level3","commission_percent"]],hide_index=True,use_container_width=True,
            column_config={"level1":"Cấp 1","level2":"Cấp 2","level3":"Cấp 3",
            "commission_percent":st.column_config.NumberColumn("Commission %",format="%.2f%%")})
        if len(filtered)>200: st.caption("Đang hiển thị 200 dòng đầu; hãy tìm kiếm để thu hẹp.")
    if can_edit:
        st.markdown("**Chỉnh trực tiếp / thêm ngành hàng**")
        choices={f"{r.level1} → {r.level2} → {r.level3} [{i}]":r for i,r in catalog.reset_index(drop=True).iterrows()}
        option=st.selectbox("Chọn ngành hàng",["+ Thêm mới"]+list(choices),key="shopee_edit_choice")
        initial_rate=0.0 if option=="+ Thêm mới" else float(choices[option].commission_rate)*100
        with st.form("shopee_single_edit"):

            a=st.text_input("Ngành hàng cấp 1 (khi thêm mới)")
            b=st.text_input("Ngành hàng cấp 2 (khi thêm mới)")
            c=st.text_input("Ngành hàng cấp 3 (khi thêm mới; có thể trống)")
            rate_percent=st.number_input("Commission mới (%)",min_value=0.0,max_value=100.0,step=0.1,format="%.2f",value=initial_rate)
            if st.form_submit_button("Lưu Commission"):
                if option=="+ Thêm mới":
                    if not a.strip() or not b.strip(): st.error("Cần nhập cấp 1 và cấp 2.")
                    else:
                        with engine.begin() as con: changed=save_category(con,a.strip(),b.strip(),c.strip(),rate_percent/100)
                        st.success("Đã lưu." if changed else "Không có thay đổi.");st.rerun()
                else:
                    r=choices[option]
                    with engine.begin() as con: changed=save_category(con,r.level1,r.level2,r.level3,rate_percent/100)
                    st.success("Đã lưu." if changed else "Không có thay đổi.");st.rerun()
        st.markdown("**Import Excel hàng loạt**")
        upload=st.file_uploader("File có sheet SP CATE CODE",type=["xlsx"],key="shopee_import")
        if upload:
            try:
                incoming=parse_shopee_excel(upload)
                existing=dict(zip(catalog["category_key"],catalog["commission_rate"]))
                changed=incoming[incoming.apply(lambda r:r.category_key not in existing or abs(float(existing[r.category_key])-float(r.rate))>1e-10,axis=1)]
                st.info(f"{len(incoming):,} ngành hàng hợp lệ; {len(changed):,} dòng mới hoặc đổi phí. Không xóa ngành hàng cũ.")
                preview=changed.head(30).copy();preview["Commission %"]=preview["rate"]*100
                st.dataframe(preview[["level1","level2","level3","Commission %"]],hide_index=True,use_container_width=True)
                if st.button("Xác nhận import Commission",disabled=changed.empty,type="primary"):
                    count=0
                    with engine.begin() as con:
                        for r in changed.itertuples(index=False):
                            count+=int(save_category(con,r.level1,r.level2,r.level3,float(r.rate)))
                    st.success(f"Đã cập nhật {count:,} ngành hàng.");st.rerun()
            except Exception as e: st.error(f"Không thể đọc file: {e}")
    with st.expander("Lịch sử thay đổi Commission"):
        with engine.begin() as con:
            hist=pd.read_sql(text("SELECT category_key,old_rate,new_rate,changed_at FROM shopee_commission_history ORDER BY id DESC LIMIT 100"),con)
        st.dataframe(hist,hide_index=True,use_container_width=True)

with tab_cf:
    st.subheader("Cost & Fund Master — dùng chung Shopee / TikTok")
    st.caption("Tất cả SKU từ Raw Data; chỉ lưu các ô Cost/Fund thực sự thay đổi. Thay đổi được ghi lịch sử.")
    secret=st.secrets.get("COST_FUND_EDITOR_PASSWORD", "")
    if not secret:
        st.warning("Chế độ chỉ xem. Admin cần cấu hình COST_FUND_EDITOR_PASSWORD trong Streamlit Secrets để bật quyền sửa.")
    elif not st.session_state.get("cf_editor"):
        with st.form("cf_login"):
            actor=st.text_input("Tên người chỉnh sửa",key="cf_actor_input")
            password=st.text_input("Mật khẩu Editor",type="password")
            if st.form_submit_button("Mở quyền chỉnh sửa"):
                if actor.strip() and hmac.compare_digest(password,str(secret)):
                    st.session_state["cf_editor"]=actor.strip()
                    st.rerun()
                else:
                    st.error("Thiếu tên người chỉnh hoặc mật khẩu không đúng.")
    else:
        st.success("Editor: " + st.session_state["cf_editor"])
        if st.button("Đăng xuất quyền sửa Cost/Fund"):
            st.session_state.pop("cf_editor",None)
            st.rerun()
    term=st.text_input("Tìm SKU / tên sản phẩm",key="cf_search").strip()
    limit=st.selectbox("Số SKU mỗi trang",[50,100,200,500],index=1,key="cf_limit")
    with engine.begin() as con:
        params={"term":"%"+term.lower()+"%"}
        where="WHERE lower(p.sku) LIKE :term OR lower(COALESCE(p.product_name,'')) LIKE :term" if term else ""
        count=con.execute(text("SELECT COUNT(*) FROM products p "+where),params).scalar()
    pages=max(1,(count+limit-1)//limit)
    page=st.number_input("Trang",min_value=1,max_value=pages,value=1,step=1,key="cf_page")
    params.update({"lim":limit,"off":(page-1)*limit})
    with engine.begin() as con:
        master=pd.read_sql(text("""SELECT p.sku,p.product_name,cf.cost,cf.fund
            FROM products p LEFT JOIN cost_fund cf ON cf.sku=p.sku """+where+
            " ORDER BY p.sku LIMIT :lim OFFSET :off"),con,params=params)
    st.caption(f"{count:,} SKU • trang {page}/{pages}")
    editable=bool(st.session_state.get("cf_editor")) and bool(secret)
    edited=st.data_editor(master,hide_index=True,use_container_width=True,key=f"cf_editor_{term}_{page}_{limit}_{st.session_state.get('cf_editor_rev',0)}",
        disabled=["sku","product_name"] if editable else list(master.columns),
        column_config={"sku":st.column_config.TextColumn("SKU"),
                       "product_name":st.column_config.TextColumn("Product Name"),
                       "cost":st.column_config.NumberColumn("Cost",min_value=0,format="%.0f"),
                       "fund":st.column_config.NumberColumn("Fund",min_value=0,format="%.0f")})
    if editable and not master.empty:
        changes=[]
        old=master.set_index("sku")
        for _,r in edited.iterrows():
            sku=str(r["sku"])
            for field in ("cost","fund"):
                before=old.loc[sku,field]
                after=r[field]
                bv=None if pd.isna(before) else float(before)
                av=None if pd.isna(after) else float(after)
                if bv!=av:
                    changes.append((sku,field,bv,av))
        if changes:
            try:
                with engine.begin() as con:
                    for sku,field,bv,av in changes:
                        con.execute(text("INSERT INTO cost_fund(sku,cost,fund) VALUES(:s,NULL,NULL) ON CONFLICT(sku) DO NOTHING"),{"s":sku})
                        # Check against DB inside transaction, not just the UI value.
                        actual_old=con.execute(text(f"SELECT {field} FROM cost_fund WHERE sku=:s FOR UPDATE") if engine.dialect.name=="postgresql" else text(f"SELECT {field} FROM cost_fund WHERE sku=:s"),{"s":sku}).scalar()
                        actual_old=None if actual_old is None else float(actual_old)
                        if actual_old==av:
                            continue
                        stored=con.execute(text(f"UPDATE cost_fund SET {field}=:v,manual_override=1,updated_at=CURRENT_TIMESTAMP WHERE sku=:s RETURNING {field}"),{"v":av,"s":sku}).scalar()
                        if (stored is None and av is not None) or (stored is not None and av is not None and abs(float(stored)-av)>0.000001):
                            raise ValueError(f"SKU {sku}: database lưu {stored}, khác giá yêu cầu {av}")
                        con.execute(text("""INSERT INTO cost_fund_history(sku,field,old_value,new_value,edited_by)
                            VALUES(:s,:f,:o,:n,:a)"""),{"s":sku,"f":field,"o":actual_old,"n":stored,"a":st.session_state["cf_editor"]})
                st.session_state["cf_editor_rev"]=st.session_state.get("cf_editor_rev",0)+1
                st.toast(f"Đã xác nhận lưu {len(changes)} thay đổi Cost/Fund vào database.",icon="💾")
                st.rerun()
            except Exception as exc:
                st.error(f"Không lưu được Cost/Fund; giao dịch đã hủy: {exc}")
    with st.expander("Lịch sử chỉnh sửa Cost/Fund"):
        with engine.begin() as con:
            hist=pd.read_sql(text("""SELECT sku,field,old_value,new_value,edited_by,changed_at
                FROM cost_fund_history ORDER BY id DESC LIMIT 100"""),con)
        st.dataframe(hist,hide_index=True,use_container_width=True)


# V1.7.5: controls stay mounted; only the results region is refreshed.
# This avoids rebuilding the text area and column selectors every polling cycle.
with tab1:
    left,right=st.columns([3,1])
    with left:
        raw=st.text_area("Thêm SKU vào Shared DealSheet",height=105,
            placeholder="250417630\n250640779\t979000\n250709440")
    with right:
        st.write(""); st.write("")
        if st.button("➕ Add / Refresh SKU",type="primary",use_container_width=True):
            rows,invalid=parse_input(raw)
            added,dupes=add_to_shared_dealsheet(rows)
            if invalid: st.warning(f"{len(invalid)} dòng có Daily Price không hợp lệ.")
            st.success(f"Đã thêm {added} SKU vào Shared DealSheet." + (f" {dupes} dòng trùng được bỏ qua." if dupes else ""))
            st.rerun()

    # Column visibility / presets, persisted in shared DB.
    if "visible_cols" not in st.session_state:
        st.session_state["visible_cols"]=load_visible_columns()

    st.markdown("**Hiển thị cột**")
    p1,p2,p3,p4=st.columns(4)
    preset_clicked=None
    with p1:
        if st.button("Gọn",use_container_width=True): preset_clicked="Gọn"
    with p2:
        if st.button("Chi tiết",use_container_width=True): preset_clicked="Chi tiết"
    with p3:
        if st.button("Tồn kho",use_container_width=True): preset_clicked="Tồn kho"
    with p4:
        if st.button("Hiện tất cả cột",use_container_width=True): preset_clicked="Chi tiết"
    if preset_clicked:
        st.session_state["visible_cols"]=PRESETS[preset_clicked].copy()
        save_visible_columns(st.session_state["visible_cols"])
        st.rerun()

    selected=st.multiselect(
        "Ẩn/hiện từng cột",
        options=ALL_COLUMNS,
        default=st.session_state["visible_cols"],
        format_func=lambda x: LABELS[x],
        key="column_picker"
    )
    # SKU must always remain visible; Daily Price remains available for editing in normal workflows.
    if "sku" not in selected:
        selected=["sku"]+selected
    normalized=[c for c in ALL_COLUMNS if c in selected]
    if normalized!=st.session_state["visible_cols"]:
        st.session_state["visible_cols"]=normalized
        save_visible_columns(normalized)

    with st.expander("🛒 Gán Shopee Subcate cho SKU (tìm kiếm và chọn)"):
        active=shared_skus()
        catalog=category_catalog()
        if not active: st.info("Hãy thêm SKU vào DealSheet trước.")
        elif catalog.empty: st.info("Hãy import biểu phí tại Shopee Commission Master trước.")
        else:
            selected_sku=st.selectbox("SKU",active,key="shopee_assign_sku")
            with engine.begin() as con:
                current=con.execute(text("SELECT category_key FROM shopee_sku_category WHERE sku=:s"),{"s":selected_sku}).scalar()
            choices={f"{r.level3 or r.level2} — {r.level1} → {r.level2} → {r.level3} ({r.commission_rate*100:.2f}%)":r.category_key for r in catalog.itertuples(index=False)}
            labels=["Không chọn — dùng Commission gốc"]+list(choices)
            default=next((i for i,label in enumerate(labels) if choices.get(label)==current),0)
            with st.form("shopee_assign_form"):
                label=st.selectbox("Tìm / chọn ngành hàng",labels,index=default)
                if st.form_submit_button("Lưu ngành hàng cho SKU"):
                    key=choices.get(label)
                    with engine.begin() as con:
                        if key:
                            con.execute(text("""INSERT INTO shopee_sku_category(sku,category_key,updated_at)
                                VALUES(:s,:k,CURRENT_TIMESTAMP) ON CONFLICT(sku) DO UPDATE
                                SET category_key=excluded.category_key,updated_at=CURRENT_TIMESTAMP"""),{"s":selected_sku,"k":key})
                        else: con.execute(text("DELETE FROM shopee_sku_category WHERE sku=:s"),{"s":selected_sku})
                    st.success("Đã lưu lựa chọn ngành hàng.");st.rerun()

    @st.fragment(run_every="3s")
    def render_live_dealsheet():
        skus=shared_skus()
        if not skus:
            st.info("Shared DealSheet đang trống. Paste SKU ở trên rồi bấm Add / Refresh SKU.")
        else:
            df=get_deals(skus)
            found=set(df["sku"].astype(str)) if not df.empty else set()
            missing=[s for s in skus if s not in found]
            if missing:
                st.error("SKU đang có trong Shared DealSheet nhưng chưa có Product Master: "+", ".join(missing[:30])+(" ..." if len(missing)>30 else ""))

            if not df.empty:
                f1,f2,f3,f4=st.columns(4)
                brands=sorted(df["brand"].dropna().astype(str).unique().tolist())
                cms=sorted(df["cm"].dropna().astype(str).unique().tolist())
                with f1: q=st.text_input("Search SKU / Product / Product ID")
                with f2: brand_filter=st.multiselect("Brand",brands)
                with f3: cm_filter=st.multiselect("CM",cms)
                with f4: only_missing=st.checkbox("Chỉ hiện dòng thiếu dữ liệu")

                view=df.copy()
                if q:
                    qq=str(q)
                    mask=(view["sku"].astype(str).str.contains(qq,case=False,na=False) |
                          view["product_name"].astype(str).str.contains(qq,case=False,na=False) |
                          view["product_id"].astype(str).str.contains(qq,case=False,na=False))
                    view=view[mask]
                if brand_filter: view=view[view["brand"].astype(str).isin(brand_filter)]
                if cm_filter: view=view[view["cm"].astype(str).isin(cm_filter)]
                if only_missing:
                    view=view[view[["cost","commission_rate","packing_fee"]].isna().any(axis=1)]

                cfg=settings()
                derived=view.apply(lambda r: calc(r,cfg),axis=1)
                view=pd.concat([view.reset_index(drop=True),derived.reset_index(drop=True)],axis=1)

                missing_cost=int(view["cost"].isna().sum())
                missing_comm=int(view["commission_rate"].isna().sum())
                missing_pack=int(view["packing_fee"].isna().sum())
                if missing_cost or missing_comm or missing_pack:
                    st.warning(f"Thiếu master data: Cost {missing_cost} • Commission {missing_comm} • Packing Fee {missing_pack}. Margin của dòng thiếu sẽ để trống.")

                m1,m2,m3,m4=st.columns(4)
                m1.metric("SKU trong bảng",f"{len(view):,}")
                m2.metric("Có Daily Price",f"{view['daily_price'].notna().sum():,}")
                avg=view["margin_pct"].dropna()
                m3.metric("Margin TB",f"{avg.mean():.2f}%" if len(avg) else "—")
                m4.metric("Tổng tồn 3 kho",f"{view['total_3'].fillna(0).sum():,.0f}")

                visible=[c for c in st.session_state["visible_cols"] if c in view.columns]
                if "sku" not in visible: visible=["sku"]+visible

                original=view.copy()
                # Streamlit retains data_editor widget state under a fixed key.
                # When shared master data changes, rotate the widget identity so
                # the fresh DB values (including Fund -> 0) cannot be masked by
                # an older editor snapshot. Daily Price edits retain the key.
                master_cols=["sku", "cost", "fund", "packing_fee", "commission_rate",
                             "hcm", "hn", "danang", "total_3", "total_company"]
                master_snapshot=original[[c for c in master_cols if c in original.columns]]
                master_signature=hashlib.sha256(
                    master_snapshot.to_json(orient="split", default_handler=str).encode("utf-8")
                ).hexdigest()[:16]
                editor_key=f"shared_deal_editor_{master_signature}"
                edited=st.data_editor(
                    view[visible],
                    key=editor_key,
                    hide_index=True,
                    use_container_width=True,
                    height=min(760,125+35*max(len(view),5)),
                    disabled=[c for c in visible if c!="daily_price"],
                    column_config={
                        "sku":st.column_config.TextColumn("SKU",width="medium"),
                        "product_id":st.column_config.TextColumn("Product ID",width="medium"),
                        "model_id":st.column_config.TextColumn("Model ID",width="medium"),
                        "product_name":st.column_config.TextColumn("Product Name",width="large"),
                        "brand":"Brand","cm":"CM",
                        "cost":st.column_config.NumberColumn("Cost",format="%,.0f"),
                        "fund":st.column_config.NumberColumn("Fund",format="%,.0f"),
                        "cost_after_fund":st.column_config.NumberColumn("Cost - Fund",format="%,.0f"),
                        "packing_fee":st.column_config.NumberColumn("Packing Fee",format="%,.0f"),
                        "final_cost":st.column_config.NumberColumn("Final Cost",format="%,.0f"),
                        "daily_price":st.column_config.NumberColumn("Daily Price ✏️",format="%,.0f",min_value=0),
                        "shopee_subcate":st.column_config.TextColumn("Shopee Subcate"),
                        "commission_rate":st.column_config.NumberColumn("Commission %",format="%.4f"),
                        "commission_fee":st.column_config.NumberColumn("Commission Fee",format="%,.0f"),
                        "transaction_fee":st.column_config.NumberColumn("Transaction Fee",format="%,.0f"),
                        "voucher_extra":st.column_config.NumberColumn("Voucher Extra",format="%,.0f"),
                        "fixed_fee":st.column_config.NumberColumn("Pi Ship + Hạ tầng",format="%,.0f"),
                        "total_fee":st.column_config.NumberColumn("Total Fee",format="%,.0f"),
                        "net_revenue":st.column_config.NumberColumn("Net Revenue",format="%,.0f"),
                        "est_gp":st.column_config.NumberColumn("Est GP",format="%,.0f"),
                        "margin_pct":st.column_config.NumberColumn("Margin %",format="%.2f%%"),
                        "hcm":st.column_config.NumberColumn("Kho HCM",format="%,.0f"),
                        "hn":st.column_config.NumberColumn("Kho HN",format="%,.0f"),
                        "danang":st.column_config.NumberColumn("Kho Đà Nẵng/MTR",format="%,.0f"),
                        "total_3":st.column_config.NumberColumn("Tổng tồn 3 kho",format="%,.0f"),
                        "total_company":st.column_config.NumberColumn("Tổng tồn công ty",format="%,.0f"),
                    }
                )

                # Autosave Daily Price immediately after an edit is committed in the grid.
                if "daily_price" in edited.columns:
                    # Merge hidden source fields back so calculation/history can use full row data.
                    full_edited=original.copy()
                    price_map=dict(zip(edited["sku"].astype(str),edited["daily_price"]))
                    full_edited["daily_price"]=full_edited["sku"].astype(str).map(price_map)
                    saved=autosave_prices(original,full_edited)
                    if saved:
                        st.toast(f"Đã tự lưu {saved} Daily Price vào Shared Database.",icon="💾")

                st.caption("Daily Price tự lưu khi bạn sửa xong ô. Không cần nút Save và không reload toàn bộ bảng. Đóng trình duyệt hoặc đổi máy, Shared DealSheet vẫn còn.")

            with st.expander("Xóa Shared DealSheet"):
                st.warning("Chỉ xóa danh sách SKU đang làm việc. Product Master / Cost / Fund / Commission / Inventory không bị xóa.")
                confirm=st.checkbox("Tôi xác nhận muốn xóa toàn bộ SKU khỏi Shared DealSheet")
                if st.button("Clear Shared DealSheet",disabled=not confirm):
                    clear_shared_dealsheet()
                    st.success("Đã xóa Shared DealSheet.")
                    st.rerun()

    render_live_dealsheet()

with tab2:
    st.subheader("Import / Update dữ liệu")
    st.caption("Update dùng UPSERT: SKU có trong file được cập nhật; SKU cũ không có trong file vẫn được giữ.")

    st.markdown("**1) Workbook Consolidation**")
    uploaded=st.file_uploader("Premium_DealSheet Consolidation.xlsx",type=["xlsx"],key="consolidation")
    if uploaded and st.button("Import Consolidation",type="primary"):
        try:
            with st.spinner("Đang đọc và cập nhật dữ liệu..."):
                result=import_consolidation(uploaded)
            st.success("Import Consolidation hoàn tất.")
            st.json(result)
        except Exception as e:
            st.error(f"Import lỗi: {e}")

    st.divider()
    st.markdown("**2) Cost / Fund — nhập dữ liệu ban đầu (chỉ Admin)**")
    st.info("Cost/Fund nên chỉnh tại tab Cost & Fund Master. File nhập bổ sung không được ghi đè dữ liệu đã chỉnh thủ công.")
    st.caption("File chỉ cần 3 cột: SKU, Cost, Fund. Update không xóa SKU cũ.")
    cf=st.file_uploader("Upload Cost/Fund (.xlsx hoặc .csv)",type=["xlsx","csv"],key="costfund")
    if cf and st.button("Update Cost / Fund",disabled=not bool(st.session_state.get("cf_editor"))):
        try:
            with st.spinner("Đang cập nhật Cost/Fund..."):
                result=import_cost_fund(cf)
            st.success("Đã cập nhật Cost/Fund.")
            st.json(result)
        except Exception as e:
            st.error(f"Cost/Fund import lỗi: {e}")

    st.divider()
    st.markdown("**Lịch sử import gần nhất**")
    try:
        with engine.begin() as con:
            logs=pd.read_sql(text("SELECT * FROM import_log ORDER BY id DESC LIMIT 20"),con)
        st.dataframe(logs,use_container_width=True,hide_index=True)
    except Exception:
        st.info("Chưa có lịch sử import.")

with tab3:
    cfg=settings()
    st.subheader("Shopee Fee Settings")
    with st.form("fees"):
        tr=st.number_input("Transaction Fee (%)",value=cfg["transaction_rate"]*100,step=.1)
        vr=st.number_input("Voucher Extra (%)",value=cfg["voucher_rate"]*100,step=.1)
        cap=st.number_input("Voucher Cap",value=float(cfg["voucher_cap"]),step=1000.0)
        infra=st.number_input("Infrastructure Fee",value=float(cfg["infrastructure_fee"]),step=100.0)
        ship=st.number_input("Pi Ship",value=float(cfg["pi_ship"]),step=100.0)
        st.info(f"Phí cố định: Pi Ship {ship:,.0f} + Hạ tầng {infra:,.0f} = **{ship+infra:,.0f} đ**")
        if st.form_submit_button("Save Settings"):
            with engine.begin() as con:
                con.execute(text("""UPDATE fee_settings SET transaction_rate=:t,voucher_rate=:v,
                    voucher_cap=:c,infrastructure_fee=:i,pi_ship=:p WHERE id=1"""),
                    {"t":tr/100,"v":vr/100,"c":cap,"i":infra,"p":ship})
            st.success("Đã lưu cấu hình phí.")

with tab4:
    st.subheader("Daily Price History")
    hist_sku=st.text_input("Nhập SKU để xem lịch sử")
    if hist_sku:
        with engine.begin() as con:
            hist=pd.read_sql(text("""SELECT sku,daily_price,margin,est_gp,changed_at
                FROM price_history WHERE sku=:s ORDER BY changed_at DESC LIMIT 200"""),
                con,params={"s":clean_sku(hist_sku)})
        if hist.empty:
            st.info("SKU này chưa có lịch sử giá.")
        else:
            hist["margin_pct"]=hist["margin"]*100
            st.dataframe(hist[["changed_at","sku","daily_price","margin_pct","est_gp"]],
                hide_index=True,use_container_width=True,
                column_config={
                    "daily_price":st.column_config.NumberColumn("Daily Price",format="%,.0f"),
                    "margin_pct":st.column_config.NumberColumn("Margin %",format="%.2f%%"),
                    "est_gp":st.column_config.NumberColumn("Est GP",format="%,.0f")
                })
