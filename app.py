# -*- coding: utf-8 -*-
"""MOMO × PChome 比價系統 (Streamlit)
執行方式: streamlit run app.py
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import pandas as pd
import plotly.express as px
import streamlit as st
from curl_cffi import requests

st.set_page_config(page_title="MOMO × PChome 比價系統", page_icon="🛒", layout="wide")

# ============================== 常數 ==============================
MOMO_API = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
MOMO_HOME = "https://www.momoshop.com.tw/"
MOMO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}
PCHOME_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
PCHOME_HEADERS = {"Accept": "application/json, text/plain, */*", "Accept-Language": "zh-TW,zh;q=0.9"}

COLORS = {"MOMO": "#E5006E", "PChome": "#F5A300"}
COLUMNS = ["平台", "品名", "價格", "評分", "評論數", "購買人數", "評論摘要", "圖片", "連結"]

# API 欄位名稱可能隨網站改版而變，這裡以「候選欄位清單」容錯，取第一個有值的
RATING_KEYS = ["ratingValue", "rating", "score", "goodsRating", "avgScore", "reviewScore", "starRating"]
REVIEW_COUNT_KEYS = ["reviewCount", "ratingCount", "commentCount", "reviewNum", "evaluateCount",
                     "goodsReviewCount", "commentNum", "reviews"]
SOLD_KEYS = ["soldCount", "saleCount", "soldQty", "salesCount", "totalSale", "totalSaleCount",
             "buyCount", "sellCount", "purchaseCount", "saleQty", "goodsSaleCount", "soldNum"]
SNIPPET_KEYS = ["reviewContent", "commentContent", "latestReview", "topReview", "reviewText", "comment"]


# ============================== 工具函式 ==============================
def fix_url(u) -> str:
    u = str(u or "")
    if u.startswith("//"):
        return "https:" + u
    return u


def pick(d: dict, keys: list):
    """依序從候選欄位取出第一個非空值。"""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def parse_count(v):
    """把 '1,234'、'已售 1.2萬+'、'3k' 之類的文字轉為整數；無法解析回傳 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*([萬万kK千]?)", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    mult = {"萬": 1e4, "万": 1e4, "k": 1e3, "K": 1e3, "千": 1e3}.get(unit, 1)
    return int(num * mult)


def parse_float(v):
    if v is None:
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        m = re.search(r"\d+(?:\.\d+)?", str(v))
        return float(m.group()) if m else None


def parse_price(v):
    s = re.sub(r"[^\d]", "", str(v or ""))
    return int(s) if s else None


def to_snippet(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("content") or v.get("text") or ""
    return str(v).strip()[:120]


def normalize(row: dict, platform: str, name, price, img, link, raw: dict) -> dict:
    return {
        "平台": platform,
        "品名": str(name or "").strip(),
        "價格": parse_price(price),
        "評分": parse_float(pick(raw, RATING_KEYS)),
        "評論數": parse_count(pick(raw, REVIEW_COUNT_KEYS)),
        "購買人數": parse_count(pick(raw, SOLD_KEYS)),
        "評論摘要": to_snippet(pick(raw, SNIPPET_KEYS)),
        "圖片": img,
        "連結": link,
    }


# ============================== 爬蟲 ==============================
def momo_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword, "searchType": "1", "cateLevel": "-1",
            "curPage": str(page), "priceS": "0", "priceE": "9999999",
            "showType": "chessboardType", "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "", "cateLv": "-1", "keyword": keyword, "curPage": str(page),
                "historyDoPush": "false", "timestamp": int(time.time() * 1000),
            },
            "flag": 2018, "serviceCode": "MT01", "addressSearchData": {},
        },
    }


def scrape_momo(keyword: str, pages: int, delay: float):
    rows, errors, sample = [], [], {}
    session = requests.Session(impersonate="chrome")
    try:
        session.get(MOMO_HOME, headers=MOMO_HEADERS, timeout=20)
    except Exception as e:
        errors.append(f"[MOMO] 首頁連線失敗：{e}")
    for page in range(1, pages + 1):
        try:
            resp = session.post(MOMO_API, headers=MOMO_HEADERS,
                                json=momo_payload(keyword, page), timeout=20)
            if resp.status_code != 200:
                errors.append(f"[MOMO] 第 {page} 頁狀態碼 {resp.status_code}")
                continue
            goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", []) or []
        except Exception as e:
            errors.append(f"[MOMO] 第 {page} 頁失敗：{e}")
            continue
        if not goods:
            break
        for p in goods:
            sample = sample or p
            code = p.get("goodsCode", "")
            rows.append(normalize(
                {}, "MOMO", p.get("goodsName"), p.get("goodsPrice"),
                fix_url(p.get("imgUrl") or p.get("goodsImageUrl")),
                f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={code}" if code else "",
                p))
        if page < pages:
            time.sleep(delay)
    return rows, errors, sample


def scrape_pchome(keyword: str, pages: int, delay: float):
    rows, errors, sample = [], [], {}
    session = requests.Session(impersonate="chrome")
    for page in range(1, pages + 1):
        try:
            resp = session.get(PCHOME_API, headers=PCHOME_HEADERS, timeout=20,
                               params={"q": keyword, "page": page, "sort": "sale/dc"})
            if resp.status_code != 200:
                errors.append(f"[PChome] 第 {page} 頁狀態碼 {resp.status_code}")
                continue
            prods = resp.json().get("prods", []) or []
        except Exception as e:
            errors.append(f"[PChome] 第 {page} 頁失敗：{e}")
            continue
        if not prods:
            break
        for p in prods:
            sample = sample or p
            pid = p.get("Id", "")
            pic = p.get("picB") or p.get("picS") or ""
            img = "https://cs-a.ecimg.tw" + pic if pic.startswith("/") else fix_url(pic)
            rows.append(normalize(
                {}, "PChome", p.get("name"), p.get("price"), img,
                f"https://24h.pchome.com.tw/prod/{pid}" if pid else "", p))
        if page < pages:
            time.sleep(delay)
    return rows, errors, sample


SCRAPERS = {"MOMO": scrape_momo, "PChome": scrape_pchome}


@st.cache_data(ttl=600, show_spinner=False)
def fetch_platform(platform: str, keyword: str, pages: int, delay: float):
    rows, errors, sample = SCRAPERS[platform](keyword, pages, delay)
    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.dropna(subset=["價格"]).reset_index(drop=True)
    if not df.empty:
        df["價格"] = df["價格"].astype(int)
    return df, errors, sample


def fetch_all(platforms, keyword, pages, delay):
    """兩個平台同時爬取，縮短等待時間。"""
    with ThreadPoolExecutor(max_workers=len(platforms)) as ex:
        futs = {p: ex.submit(fetch_platform, p, keyword, pages, delay) for p in platforms}
        return {p: f.result() for p, f in futs.items()}


# ============================== 資料處理 ==============================
SORT_OPTIONS = {
    "原始順序（平台預設）": None,
    "價格由低到高": ("價格", True),
    "價格由高到低": ("價格", False),
    "評分由高到低": ("評分", False),
    "評論數由多到少": ("評論數", False),
    "購買人數由多到少": ("購買人數", False),
    "品名 A→Z": ("品名", True),
}


def apply_sort(df: pd.DataFrame, option: str) -> pd.DataFrame:
    rule = SORT_OPTIONS[option]
    if rule is None or df.empty:
        return df.reset_index(drop=True)
    col, asc = rule
    return df.sort_values(col, ascending=asc, na_position="last", kind="stable").reset_index(drop=True)


def add_price_range(df: pd.DataFrame) -> pd.DataFrame:
    bins = [0, 300, 500, 1000, 1500, 2000, 3000, float("inf")]
    labels = ["0-300", "301-500", "501-1000", "1001-1500", "1501-2000", "2001-3000", "3000以上"]
    df = df.copy()
    df["價格區間"] = pd.cut(df["價格"], bins=bins, labels=labels, include_lowest=True).astype(str)
    return df


def clean_name(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s.lower())


def match_products(a: pd.DataFrame, b: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """以品名相似度，把 MOMO 與 PChome 的相近商品配成一對。"""
    if a.empty or b.empty:
        return pd.DataFrame()
    b_names = [clean_name(n) for n in b["品名"]]
    out = []
    for _, ra in a.iterrows():
        na = clean_name(ra["品名"])
        best, best_i = 0.0, -1
        for i, nb in enumerate(b_names):
            if abs(len(na) - len(nb)) > max(len(na), len(nb)) * 0.6:
                continue
            r = SequenceMatcher(None, na, nb).ratio()
            if r > best:
                best, best_i = r, i
        if best >= threshold:
            rb = b.iloc[best_i]
            diff = int(ra["價格"]) - int(rb["價格"])
            out.append({
                "相似度": round(best, 2),
                "MOMO 品名": ra["品名"], "MOMO 價格": int(ra["價格"]),
                "PChome 品名": rb["品名"], "PChome 價格": int(rb["價格"]),
                "價差(MOMO-PChome)": diff,
                "較便宜": "PChome" if diff > 0 else ("MOMO" if diff < 0 else "相同"),
                "MOMO 連結": ra["連結"], "PChome 連結": rb["連結"],
            })
    res = pd.DataFrame(out)
    return res.sort_values("相似度", ascending=False).reset_index(drop=True) if not res.empty else res


def fmt_int(v) -> str:
    return "—" if pd.isna(v) else f"{int(v):,}"


# ============================== 顯示元件 ==============================
def show_metrics(df: pd.DataFrame, title: str):
    st.markdown(f"##### {title}")
    c = st.columns(5)
    c[0].metric("商品數", len(df))
    c[1].metric("平均價格", f"${df['價格'].mean():,.0f}")
    c[2].metric("最高價格", f"${df['價格'].max():,}")
    c[3].metric("最低價格", f"${df['價格'].min():,}")
    c[4].metric("中位數", f"${df['價格'].median():,.0f}")


def show_cards(df: pd.DataFrame, cols_per_row: int = 4):
    for i in range(0, len(df), cols_per_row):
        cols = st.columns(cols_per_row)
        for col, (_, r) in zip(cols, df.iloc[i:i + cols_per_row].iterrows()):
            with col, st.container(border=True):
                if r["圖片"]:
                    st.image(r["圖片"], use_container_width=True)
                st.caption(r["平台"])
                st.markdown(f"**{r['品名']}**")
                st.markdown(f"### ${r['價格']:,}")
                star = "—" if pd.isna(r["評分"]) else f"⭐ {r['評分']:.1f}"
                st.write(f"{star}　💬 {fmt_int(r['評論數'])} 則評論")
                st.write(f"🛍️ 已購買 {fmt_int(r['購買人數'])} 人")
                if r["評論摘要"]:
                    st.caption(f"「{r['評論摘要']}」")
                if r["連結"]:
                    st.link_button("前往商品頁", r["連結"])


def show_chart(view: str, df: pd.DataFrame, kw: str):
    if view == "圓餅圖":
        d = add_price_range(df)
        cnt = d.groupby(["平台", "價格區間"]).size().reset_index(name="商品數")
        plats = list(cnt["平台"].unique())
        cols = st.columns(len(plats))
        for col, p in zip(cols, plats):
            fig = px.pie(cnt[cnt["平台"] == p], names="價格區間", values="商品數",
                         title=f"{p}「{kw}」價格區間分布")
            fig.update_traces(textinfo="label+percent+value")
            col.plotly_chart(fig, use_container_width=True)
    elif view == "折線圖":
        d = df.copy()
        d["編號"] = d.groupby("平台").cumcount() + 1
        fig = px.line(d, x="編號", y="價格", color="平台", markers=True, hover_name="品名",
                      color_discrete_map=COLORS, title=f"「{kw}」售價（依目前排序）")
        for p, g in d.groupby("平台"):
            fig.add_hline(y=g["價格"].mean(), line_dash="dash", line_color=COLORS[p],
                          annotation_text=f"{p} 平均 {g['價格'].mean():,.0f}")
        fig.update_layout(height=600, xaxis_title="商品編號", yaxis_title="價格（元）")
        st.plotly_chart(fig, use_container_width=True)
    elif view == "旭日圖":
        d = add_price_range(df)
        d["商品"] = d["品名"].str.slice(0, 18) + "(" + d.index.astype(str) + ")"
        d["商品數"] = 1
        fig = px.sunburst(d, path=["平台", "價格區間", "商品"], values="商品數",
                          color="平台", color_discrete_map=COLORS,
                          title=f"「{kw}」平台 → 價格區間 → 商品")
        fig.update_layout(height=700)
        st.plotly_chart(fig, use_container_width=True)
    else:  # 價格分布 (箱型圖)
        fig = px.box(df, x="平台", y="價格", color="平台", points="all", hover_name="品名",
                     color_discrete_map=COLORS, title=f"「{kw}」兩平台價格分布")
        fig.update_layout(height=600)
        st.plotly_chart(fig, use_container_width=True)


# ============================== 介面 ==============================
st.title("🛒 MOMO × PChome 比價系統")

with st.sidebar:
    st.header("查詢條件")
    keyword = st.text_input("商品關鍵字", value="行動電源")
    pages = st.number_input("爬取頁數（每個平台）", min_value=1, max_value=10, value=2, step=1)
    delay = st.slider("每頁間隔秒數", 0.5, 5.0, 1.0, 0.5)
    sort_by = st.selectbox("排序方式", list(SORT_OPTIONS.keys()))
    st.divider()
    st.header("顯示設定")
    platforms = st.multiselect("比價平台", ["MOMO", "PChome"], default=["MOMO", "PChome"])
    view = st.radio("呈現方式", ["條列式（含圖片／評論／購買人數）", "圓餅圖", "折線圖", "旭日圖", "價格分布"])
    max_items = st.slider("每平台最多顯示筆數", 10, 200, 40, step=10)
    min_price, max_price = st.number_input("最低價格", 0, value=0, step=100), \
        st.number_input("最高價格（0 = 不限）", 0, value=0, step=100)
    threshold = st.slider("同商品配對相似度門檻", 0.3, 0.9, 0.55, 0.05)
    go = st.button("🔍 查詢", type="primary", use_container_width=True)

if go:
    st.session_state["query"] = (keyword.strip(), int(pages), float(delay), tuple(platforms))

if "query" not in st.session_state:
    st.info("請在左側輸入商品關鍵字、頁數與排序方式，然後按「查詢」。")
    st.stop()

kw, pg, dl, plats = st.session_state["query"]
if not kw:
    st.warning("請輸入商品關鍵字")
    st.stop()
if not plats:
    st.warning("請至少選擇一個平台")
    st.stop()

with st.spinner(f"正在查詢「{kw}」：{' / '.join(plats)}，各 {pg} 頁..."):
    results = fetch_all(plats, kw, pg, dl)

frames, samples = {}, {}
for p, (d, errs, sample) in results.items():
    for e in errs:
        st.warning(e)
    samples[p] = sample
    if not d.empty:
        frames[p] = d

if not frames:
    st.error("沒有取得任何商品，可能被網站阻擋或關鍵字無結果。")
    st.stop()

# 價格篩選 → 排序（各平台分別處理）
sorted_frames = {}
for p, d in frames.items():
    if min_price:
        d = d[d["價格"] >= min_price]
    if max_price:
        d = d[d["價格"] <= max_price]
    sorted_frames[p] = apply_sort(d, sort_by)
sorted_frames = {p: d for p, d in sorted_frames.items() if not d.empty}
if not sorted_frames:
    st.warning("價格篩選後沒有商品，請放寬條件。")
    st.stop()

shown = {p: d.head(max_items) for p, d in sorted_frames.items()}
combined = pd.concat(shown.values(), ignore_index=True)

tab_list, tab_cmp, tab_data = st.tabs(["📊 商品與圖表", "⚖️ 兩平台比價", "📄 原始資料"])

# ---- 分頁 1：商品與圖表 ----
with tab_list:
    for p, d in sorted_frames.items():
        show_metrics(d, f"{p}")
    st.divider()
    if view.startswith("條列式"):
        if len(shown) > 1:
            sub = st.tabs(list(shown.keys()))
            for t, (p, d) in zip(sub, shown.items()):
                with t:
                    show_cards(d)
        else:
            show_cards(next(iter(shown.values())))
    else:
        show_chart(view, combined, kw)

# ---- 分頁 2：比價 ----
with tab_cmp:
    if len(sorted_frames) < 2:
        st.info("請在左側同時勾選 MOMO 與 PChome 才能比價。")
    else:
        m, pc = sorted_frames["MOMO"], sorted_frames["PChome"]
        summary = pd.DataFrame({
            "平台": ["MOMO", "PChome"],
            "商品數": [len(m), len(pc)],
            "平均價": [m["價格"].mean(), pc["價格"].mean()],
            "中位數": [m["價格"].median(), pc["價格"].median()],
            "最低價": [m["價格"].min(), pc["價格"].min()],
            "最高價": [m["價格"].max(), pc["價格"].max()],
            "平均評分": [m["評分"].mean(), pc["評分"].mean()],
            "總購買人數": [m["購買人數"].sum(), pc["購買人數"].sum()],
        })
        cheaper = "MOMO" if m["價格"].mean() < pc["價格"].mean() else "PChome"
        st.success(f"整體來看，「{kw}」平均價格 **{cheaper}** 較低"
                   f"（差距約 ${abs(m['價格'].mean() - pc['價格'].mean()):,.0f}）。")
        st.dataframe(summary.round(1), use_container_width=True, hide_index=True)

        fig = px.box(combined, x="平台", y="價格", color="平台", points="all", hover_name="品名",
                     color_discrete_map=COLORS, title="價格分布比較")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("同款商品配對（依品名相似度）")
        st.caption("配對僅依品名文字相似度推算，請點連結確認規格是否相同。")
        pairs = match_products(shown["MOMO"], shown["PChome"], threshold)
        if pairs.empty:
            st.info("找不到相似商品，可調低左側「相似度門檻」。")
        else:
            st.dataframe(
                pairs, use_container_width=True, hide_index=True,
                column_config={"MOMO 連結": st.column_config.LinkColumn("MOMO 連結"),
                               "PChome 連結": st.column_config.LinkColumn("PChome 連結")})

# ---- 分頁 3：原始資料 ----
with tab_data:
    full = pd.concat(sorted_frames.values(), ignore_index=True)
    st.dataframe(
        full, use_container_width=True,
        column_config={"圖片": st.column_config.ImageColumn("圖片"),
                       "連結": st.column_config.LinkColumn("連結")})
    st.download_button(
        "下載 CSV", full.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{time.strftime('%Y%m%d')}_比價_{kw}.csv", mime="text/csv")

    with st.expander("🔧 除錯：各平台 API 原始欄位（評分／評論／購買人數欄位對不上時看這裡）"):
        for p, s in samples.items():
            st.write(f"**{p}** 欄位：", sorted(s.keys()) if s else "（無資料）")
            if s:
                st.json(s, expanded=False)
        miss = {p: [c for c in ["評分", "評論數", "購買人數"] if d[c].isna().all()]
                for p, d in sorted_frames.items()}
        for p, cols in miss.items():
            if cols:
                st.warning(f"{p} 的「{'、'.join(cols)}」全部為空：API 可能未提供，或欄位名稱不在候選清單內。")
