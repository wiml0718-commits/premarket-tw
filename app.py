"""
盤前風向 + 低點選股 — 後端 API
資料源：Yahoo Finance（免費，約 15 分鐘延遲）

端點：
  GET /                盤前風向頁面
  GET /screener        低點選股頁面
  GET /api/predict     盤前預判數據
  GET /api/screener    0050 低點掃描結果（每日快取）

本機測試：
  pip install -r requirements.txt
  uvicorn app:app --reload --port 8000
"""

from datetime import datetime, timezone, date, timedelta
from concurrent.futures import ThreadPoolExecutor
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import yfinance as yf
import requests

BASE = os.path.dirname(os.path.abspath(__file__))

# FinMind：台股月營收年增率（更即時準確）。token 可選，註冊後設環境變數 FINMIND_TOKEN 可提高用量上限。
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "").strip()

app = FastAPI(title="premarket-tw")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ============ 盤前預判 ============
SYMS = {
    "sox": "^SOX", "ndxf": "NQ=F", "spxf": "ES=F", "asia": "^N225",
    "tsm": "TSM", "fx": "USDTWD=X", "tw": "2330.TW",
}


def quote(sym):
    try:
        fi = yf.Ticker(sym).fast_info
        last = getattr(fi, "last_price", None)
        prev = getattr(fi, "previous_close", None)
        if last and prev:
            return float(last), float(prev)
    except Exception:
        pass
    try:
        h = yf.Ticker(sym).history(period="5d")
        if len(h) >= 2:
            return float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
        if len(h) == 1:
            c = float(h["Close"].iloc[-1]); return c, c
    except Exception:
        pass
    return None, None


def pct(sym):
    last, prev = quote(sym)
    if last is None or not prev:
        return None
    return round((last - prev) / prev * 100, 2)


@app.get("/api/predict")
def predict():
    data = {"sox": pct(SYMS["sox"]), "ndxf": pct(SYMS["ndxf"]),
            "spxf": pct(SYMS["spxf"]), "asia": pct(SYMS["asia"])}
    tsm_last, tsm_prev = quote(SYMS["tsm"])
    fx_last, fx_prev = quote(SYMS["fx"])
    tw_last, _ = quote(SYMS["tw"])
    adr_chg = None
    if tsm_last and tsm_prev and fx_last and fx_prev:
        prev_twd = tsm_prev * fx_prev
        if prev_twd:
            adr_chg = round((tsm_last * fx_last / prev_twd - 1) * 100, 2)
    data["adr"] = adr_chg
    data["adr_price"] = round(tsm_last, 2) if tsm_last else None
    data["fx"] = round(fx_last, 3) if fx_last else None
    data["tsmc_tw"] = round(tw_last, 1) if tw_last else None
    data["updated"] = datetime.now(timezone.utc).isoformat()
    return data


# ============ 低點選股（0050） ============
# 種子清單：0050 主要成分股（每季換股，可自行增減）。代號→名稱。
STOCKS = {
    "2330": "台積電", "2454": "聯發科", "2308": "台達電", "2317": "鴻海",
    "3711": "日月光投控", "2383": "台光電", "2303": "聯電", "2327": "國巨",
    "2891": "中信金", "3037": "欣興", "2412": "中華電", "2382": "廣達",
    "2882": "國泰金", "2881": "富邦金", "2886": "兆豐金", "2884": "玉山金",
    "1216": "統一", "2885": "元大金", "2892": "第一金", "2880": "華南金",
    "2890": "永豐金", "5880": "合庫金", "2887": "台新金", "2883": "凱基金",
    "1303": "南亞", "1301": "台塑", "1326": "台化", "3045": "台灣大",
    "4904": "遠傳", "2912": "統一超", "2207": "和泰車", "2603": "長榮",
    "2609": "陽明", "2615": "萬海", "3008": "大立光", "2379": "瑞昱",
    "3034": "聯詠", "2357": "華碩", "2395": "研華", "2345": "智邦",
    "3017": "奇鋐", "2376": "技嘉", "6669": "緯穎", "3231": "緯創",
    "2356": "英業達", "6505": "台塑化", "5871": "中租-KY", "5876": "上海商銀",
    "2002": "中鋼", "1101": "台泥", "3661": "世芯-KY",
}

_cache = {}  # {date: result}

# 週期股標記：這些類股「便宜」常是因為景氣下行，本益比低反而可能是獲利高點（本益比陷阱）
CYCLICAL = {
    "2603": "航運", "2609": "航運", "2615": "航運",
    "2002": "鋼鐵",
    "1301": "塑化", "1303": "塑化", "1326": "塑化", "6505": "塑化",
    "1101": "水泥",
}


def _rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    v = out.dropna()
    return float(v.iloc[-1]) if len(v) else None


def finmind_rev_growth(code):
    """台股月營收年增率（%）：最新月 vs 去年同月。抓不到回 None。"""
    try:
        start = (date.today() - timedelta(days=460)).isoformat()
        headers = {"Authorization": f"Bearer {FINMIND_TOKEN}"} if FINMIND_TOKEN else {}
        params = {"dataset": "TaiwanStockMonthRevenue", "data_id": code, "start_date": start}
        r = requests.get(FINMIND_URL, params=params, headers=headers, timeout=12)
        rows = r.json().get("data", [])
        if not rows:
            return None
        for d in rows:
            d["_k"] = int(d["revenue_year"]) * 100 + int(d["revenue_month"])
        rows.sort(key=lambda d: d["_k"])
        latest = rows[-1]
        yoy_key = (int(latest["revenue_year"]) - 1) * 100 + int(latest["revenue_month"])
        prev = next((d for d in rows if d["_k"] == yoy_key), None)
        if not prev or not prev.get("revenue"):
            return None
        return round((latest["revenue"] / prev["revenue"] - 1) * 100, 1)
    except Exception:
        return None


def _valuation(code):
    """FinMind 估值：本益比 PER、股價淨值比 PBR、殖利率（台股本地資料，覆蓋率高）。"""
    try:
        start = (date.today() - timedelta(days=14)).isoformat()
        headers = {"Authorization": f"Bearer {FINMIND_TOKEN}"} if FINMIND_TOKEN else {}
        params = {"dataset": "TaiwanStockPER", "data_id": code, "start_date": start}
        r = requests.get(FINMIND_URL, params=params, headers=headers, timeout=12)
        rows = r.json().get("data", [])
        if not rows:
            return {"pe": None, "pb": None, "yld": None}
        rows.sort(key=lambda d: d["date"])
        last = rows[-1]
        pe, pb, y = last.get("PER"), last.get("PBR"), last.get("dividend_yield")
        return {"pe": pe if (pe and pe > 0) else None,
                "pb": pb if (pb and pb > 0) else None,
                "yld": y if y else None}
    except Exception:
        return {"pe": None, "pb": None, "yld": None}


def _rank_scores(values, higher_better):
    """跨股百分位評分 0~100（越符合「便宜」越高）。None 保留 None。"""
    valid = [(i, v) for i, v in enumerate(values) if v is not None and v == v and v > 0]
    res = {i: None for i in range(len(values))}
    if not valid:
        return res
    valid.sort(key=lambda x: x[1])  # 由小到大
    n = len(valid)
    for rank, (i, _) in enumerate(valid):
        p = rank / (n - 1) if n > 1 else 0.5  # 0=最小值
        res[i] = (p if higher_better else 1 - p) * 100
    return res


@app.get("/api/screener")
def screener(force: int = 0):
    today = datetime.now().strftime("%Y-%m-%d")
    if not force and today in _cache:
        return _cache[today]

    codes = list(STOCKS.keys())
    tickers = [c + ".TW" for c in codes]

    # 一次批次抓 5 年收盤（快）
    try:
        raw = yf.download(tickers, period="5y", interval="1d",
                          progress=False, group_by="column", threads=True)
        close = raw["Close"] if "Close" in raw else raw
    except Exception as e:
        return {"error": f"price download failed: {e}", "rows": []}

    # 平行抓估值與月營收年增率（皆 FinMind），每日只跑一次
    with ThreadPoolExecutor(max_workers=10) as ex:
        vals = list(ex.map(_valuation, codes))
    with ThreadPoolExecutor(max_workers=8) as ex:
        revs = list(ex.map(finmind_rev_growth, codes))
    revmap = {codes[i]: revs[i] for i in range(len(codes))}

    rows = []
    for code in codes:
        t = code + ".TW"
        try:
            s = close[t].dropna() if t in close else None
        except Exception:
            s = None
        if s is None or len(s) < 60:
            continue
        last = float(s.iloc[-1])
        hi = float(s.max()); lo = float(s.min())
        pos = (last - lo) / (hi - lo) if hi > lo else 0.5   # 0=五年最低
        ma200 = float(s.rolling(200).mean().dropna().iloc[-1]) if len(s) >= 200 else None
        dist_ma = (last / ma200 - 1) if ma200 else None
        rsi = _rsi(s)
        dd = last / hi - 1  # 距高點回檔（負值）
        rows.append({
            "code": code, "name": STOCKS[code], "price": round(last, 2),
            "pos": round(pos * 100, 1), "drawdown": round(dd * 100, 1),
            "rsi": round(rsi, 1) if rsi is not None else None,
            "dist_ma": round(dist_ma * 100, 1) if dist_ma is not None else None,
        })

    # 估值對應
    vmap = {codes[i]: vals[i] for i in range(len(codes))}
    pe_s = _rank_scores([vmap[r["code"]]["pe"] for r in rows], higher_better=False)
    pb_s = _rank_scores([vmap[r["code"]]["pb"] for r in rows], higher_better=False)
    yl_s = _rank_scores([vmap[r["code"]]["yld"] for r in rows], higher_better=True)

    for idx, r in enumerate(rows):
        v = vmap[r["code"]]
        r["pe"] = round(v["pe"], 1) if v["pe"] else None
        r["pb"] = round(v["pb"], 2) if v["pb"] else None
        r["yld"] = round(v["yld"], 2) if v["yld"] else None
        rv_fm = revmap.get(r["code"])
        r["rev"] = rv_fm
        r["rev_src"] = "月" if rv_fm is not None else None
        r["cyclical"] = CYCLICAL.get(r["code"])

        # 子分數（越接近低點/越便宜越高）
        price_score = 100 - r["pos"]                      # 位階越低分越高
        rsi_score = max(0, min(100, (60 - r["rsi"]) / 40 * 100)) if r["rsi"] is not None else None
        ma_score = max(0, min(100, (-r["dist_ma"]) / 20 * 100)) if r["dist_ma"] is not None else None
        tech_parts = [x for x in (rsi_score, ma_score) if x is not None]
        tech_score = sum(tech_parts) / len(tech_parts) if tech_parts else None
        val_parts = [x for x in (pe_s[idx], pb_s[idx], yl_s[idx]) if x is not None]
        val_score = sum(val_parts) / len(val_parts) if val_parts else None

        # 綜合：價格40 技術30 估值30，缺項自動重新分配
        comp_parts = []
        if price_score is not None: comp_parts.append((0.40, price_score))
        if tech_score is not None: comp_parts.append((0.30, tech_score))
        if val_score is not None: comp_parts.append((0.30, val_score))
        wsum = sum(w for w, _ in comp_parts)
        composite = sum(w * v for w, v in comp_parts) / wsum if wsum else 0
        r["score"] = round(composite, 1)

    rows.sort(key=lambda r: r["score"], reverse=True)
    result = {"updated": datetime.now(timezone.utc).isoformat(),
              "count": len(rows), "rows": rows}
    _cache[today] = result
    return result


# ============ 頁面 ============
@app.get("/")
def home():
    return FileResponse(os.path.join(BASE, "index.html"))


@app.get("/screener")
def screener_page():
    return FileResponse(os.path.join(BASE, "screener.html"))
