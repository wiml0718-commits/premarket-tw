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
import re
import time
import threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import yfinance as yf
import requests

BASE = os.path.dirname(os.path.abspath(__file__))

# FinMind：台股月營收年增率（更即時準確）。token 可選，註冊後設環境變數 FINMIND_TOKEN 可提高用量上限。
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "").strip()

# 證交所開放資料（全上市一次抓，免費免 token）
TWSE_DAYALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_BWIBBU = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
MARKET_THRESH = {"pos": 25, "rsi": 40, "drawdown": -30}  # 全上市初篩門檻（AND）

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


# ============ 低點選股（分類版） ============
# 種子清單，皆可自行增減。代號→名稱。分類成分股會變動，這是觀察用的代表清單。
CATEGORIES = {
    "t50": {"name": "0050 權值股", "stocks": {
        "2330": "台積電", "2454": "聯發科", "2308": "台達電", "2317": "鴻海",
        "3711": "日月光投控", "2383": "台光電", "2303": "聯電", "2327": "國巨",
        "2891": "中信金", "3037": "欣興", "2412": "中華電", "2382": "廣達",
        "2882": "國泰金", "2881": "富邦金", "2886": "兆豐金", "2884": "玉山金",
        "1216": "統一", "2885": "元大金", "2892": "第一金", "2880": "華南金",
        "2890": "永豐金", "5880": "合庫金", "2887": "台新新光金", "2883": "凱基金",
        "1303": "南亞", "1301": "台塑", "1326": "台化", "3045": "台灣大",
        "4904": "遠傳", "2912": "統一超", "2207": "和泰車", "2603": "長榮",
        "2609": "陽明", "2615": "萬海", "3008": "大立光", "2379": "瑞昱",
        "3034": "聯詠", "2357": "華碩", "2395": "研華", "2345": "智邦",
        "3017": "奇鋐", "2376": "技嘉", "6669": "緯穎", "3231": "緯創",
        "2356": "英業達", "6505": "台塑化", "5871": "中租-KY", "5876": "上海商銀",
        "2002": "中鋼", "1101": "台泥", "3661": "世芯-KY",
    }},
    "m100": {"name": "中型100", "stocks": {
        "2049": "上銀", "1605": "華新", "2105": "正新", "9910": "豐泰",
        "9904": "寶成", "2542": "興富發", "2548": "華固", "1402": "遠東新",
        "1102": "亞泥", "2027": "大成鋼", "6415": "矽力-KY", "8046": "南電",
        "2368": "金像電", "3035": "智原", "3443": "創意", "2474": "可成",
        "2360": "致茂", "2059": "川湖", "1590": "亞德客-KY", "6285": "啟碁",
        "8454": "富邦媒", "9921": "巨大", "9914": "美利達", "1722": "台肥",
        "2353": "宏碁", "2324": "仁寶", "2347": "聯強", "6176": "瑞儀",
        "3702": "大聯大", "2371": "大同",
    }},
    "fin": {"name": "金融股", "stocks": {
        "2880": "華南金", "2881": "富邦金", "2882": "國泰金", "2883": "凱基金",
        "2884": "玉山金", "2885": "元大金", "2886": "兆豐金", "2887": "台新新光金",
        "2889": "國票金", "2890": "永豐金", "2891": "中信金", "2892": "第一金",
        "5880": "合庫金", "2801": "彰銀", "2809": "京城銀", "2812": "台中銀",
        "2823": "中壽", "2834": "臺企銀", "2845": "遠東銀", "5876": "上海商銀",
        "5871": "中租-KY",
    }},
    "ai": {"name": "AI 供應鏈", "stocks": {
        "2330": "台積電", "2454": "聯發科", "2317": "鴻海", "4938": "和碩",
        "3231": "緯創", "2382": "廣達", "2376": "技嘉", "6669": "緯穎",
        "3017": "奇鋐", "3324": "雙鴻", "3653": "健策", "2368": "金像電",
        "3037": "欣興", "8046": "南電", "3034": "聯詠", "2379": "瑞昱",
        "3035": "智原", "3443": "創意", "3661": "世芯-KY", "2345": "智邦",
        "2308": "台達電", "3711": "日月光投控", "6196": "帆宣", "1590": "亞德客-KY",
        "4966": "譜瑞-KY", "5274": "信驊", "3529": "力旺", "5269": "祥碩", "2458": "義隆",
    }},
    "trad": {"name": "傳產原物料", "stocks": {
        "2603": "長榮", "2609": "陽明", "2615": "萬海", "2002": "中鋼",
        "2027": "大成鋼", "9958": "世紀鋼", "1301": "台塑", "1303": "南亞",
        "1326": "台化", "6505": "台塑化", "1101": "台泥", "1102": "亞泥",
        "1605": "華新", "2105": "正新", "1402": "遠東新", "1722": "台肥",
        "1216": "統一", "1227": "佳格", "9904": "寶成", "9910": "豐泰",
    }},
    "bio": {"name": "生技醫療", "stocks": {
        "6446": "藥華藥", "1795": "美時", "6472": "保瑞", "1707": "葡萄王", "8436": "大江",
    }},
    "power": {"name": "重電綠能", "stocks": {
        "1519": "華城", "1503": "士電", "1513": "中興電", "1514": "亞力", "1504": "東元",
    }},
    "travel": {"name": "航空觀光餐飲", "stocks": {
        "2618": "長榮航", "2610": "華航", "2727": "王品", "2729": "瓦城", "2731": "雄獅",
    }},
    "etf": {"name": "ETF", "etf": True, "stocks": {
        "0050": "元大台灣50", "006208": "富邦台50", "0056": "元大高股息",
        "00878": "國泰永續高股息", "00919": "群益台灣精選高息", "00929": "復華台灣科技優息",
        "00713": "元大台灣高息低波", "00940": "元大台灣價值高息", "00939": "統一台灣高息動能",
        "0051": "元大中型100", "00692": "富邦公司治理", "00850": "元大臺灣ESG永續",
        "00646": "元大S&P500", "00662": "富邦NASDAQ", "00757": "統一FANG+", "00733": "富邦臺灣中小",
    }},
}

_cache = {}  # {"date:cat" 或 "date:all:mode": result}

# 全部觀察池：六類去重合併，但排除 ETF（基金無估值/營收）。代號→名稱
ALL_POOL = {}
for _c in CATEGORIES.values():
    if _c.get("etf"):
        continue
    for _code, _name in _c["stocks"].items():
        ALL_POOL.setdefault(_code, _name)

# 「全部」模式的篩選門檻（AND）
THRESH = {
    "strict": {"pos": 25, "rsi": 40, "drawdown": -30},
    "loose":  {"pos": 40, "rsi": 50, "drawdown": -20},
}

# 週期股標記：這些類股「便宜」常是因為景氣下行，本益比低反而可能是獲利高點（本益比陷阱）
CYCLICAL = {
    "2603": "航運", "2609": "航運", "2615": "航運",
    "2002": "鋼鐵", "2027": "鋼鐵", "9958": "鋼鐵",
    "1301": "塑化", "1303": "塑化", "1326": "塑化", "6505": "塑化",
    "1101": "水泥", "1102": "水泥",
}


def _rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    v = out.dropna()
    return float(v.iloc[-1]) if len(v) else None


_fm_lock = threading.Lock()  # 串行化 FinMind 呼叫，避免同一秒塞太多撞分鐘限制


def finmind_get(params, tries=4):
    """呼叫 FinMind，遇限流（402/429）自動退避重試。回傳 data list。"""
    headers = {"Authorization": f"Bearer {FINMIND_TOKEN}"} if FINMIND_TOKEN else {}
    for i in range(tries):
        try:
            with _fm_lock:
                r = requests.get(FINMIND_URL, params=params, headers=headers, timeout=15)
                time.sleep(0.4)  # 每次呼叫間隔，控制在分鐘上限內
            if r.status_code in (402, 429):  # 限流
                time.sleep(2 ** i + 1)        # 退避：3、4、6、10 秒
                continue
            j = r.json()
            if isinstance(j, dict) and j.get("status") in (402, 429):
                time.sleep(2 ** i + 1)
                continue
            return j.get("data", []) if isinstance(j, dict) else []
        except Exception:
            time.sleep(1.5)
    return []


def finmind_rev_growth(code):
    """台股月營收年增率（%）：最新月 vs 去年同月。抓不到回 None。"""
    try:
        start = (date.today() - timedelta(days=460)).isoformat()
        params = {"dataset": "TaiwanStockMonthRevenue", "data_id": code, "start_date": start}
        rows = finmind_get(params)
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


def finmind_inst_streak(code):
    """外資近期連續買超天數（從最近一個交易日往回數，遇賣超即停）。抓不到回 None。"""
    try:
        start = (date.today() - timedelta(days=25)).isoformat()
        rows = finmind_get({"dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                            "data_id": code, "start_date": start})
        if not rows:
            return None
        day = {}
        for r in rows:
            nm = str(r.get("name", ""))
            if nm == "Foreign_Investor" or "Foreign_Investor" in nm:
                net = (r.get("buy", 0) or 0) - (r.get("sell", 0) or 0)
                day[r.get("date")] = day.get(r.get("date"), 0) + net
        if not day:  # 欄位不同時退而用全體法人合計
            for r in rows:
                net = (r.get("buy", 0) or 0) - (r.get("sell", 0) or 0)
                day[r.get("date")] = day.get(r.get("date"), 0) + net
        streak = 0
        for d in sorted(day.keys(), reverse=True):
            if day[d] > 0:
                streak += 1
            else:
                break
        return streak
    except Exception:
        return None


def finmind_div_years(code):
    """近年連續配發現金股利的年數（從最近往回數，斷一年即停）。抓不到回 None。"""
    try:
        start = (date.today() - timedelta(days=365 * 9)).isoformat()
        rows = finmind_get({"dataset": "TaiwanStockDividend",
                            "data_id": code, "start_date": start})
        if not rows:
            return None
        # 以「除息日(西元年)」歸類，同一年有多筆（多次配息）只要任一筆現金>0 即算該年有配
        years = {}
        for r in rows:
            ds = str(r.get("CashExDividendTradingDate") or r.get("date") or "")
            y = ds[:4]
            if not y.isdigit():
                continue
            cash = 0
            for k in ("CashEarningsDistribution", "CashStatutorySurplus", "CashCapitalReserve"):
                cash += (r.get(k, 0) or 0)
            if cash > 0:
                years[int(y)] = True
        if not years:
            return 0
        ys = sorted(years.keys(), reverse=True)
        streak, prev = 0, None
        for y in ys:
            if prev is None or prev - y == 1:
                streak += 1
                prev = y
            else:
                break
        return streak
    except Exception:
        return None


def _enrich_one(code):
    return {"inst": finmind_inst_streak(code), "divy": finmind_div_years(code)}


def enrich_rows(rows):
    """為清單補上『外資連續買超天數』與『連續配息年數』。"""
    if not rows:
        return
    codes = [r["code"] for r in rows]
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(_enrich_one, codes))
    for r, e in zip(rows, res):
        r["inst"] = e["inst"]
        r["divy"] = e["divy"]


def _valuation(code):
    """FinMind 估值：本益比 PER、股價淨值比 PBR、殖利率（台股本地資料，覆蓋率高）。"""
    try:
        start = (date.today() - timedelta(days=14)).isoformat()
        params = {"dataset": "TaiwanStockPER", "data_id": code, "start_date": start}
        rows = finmind_get(params)
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


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def twse_universe(top_n=200):
    """證交所：成交值前 top_n 大的上市普通股 [(code, name), ...]。"""
    data = None
    for _ in range(3):  # 資料量大，加重試與較長逾時
        try:
            r = requests.get(TWSE_DAYALL, timeout=40)
            if r.status_code == 200:
                data = r.json()
                break
        except Exception:
            time.sleep(2)
    if not data:
        return []
    rows = []
    for d in data:
        code = str(d.get("Code", "")).strip()
        # 只要普通股：純四位數字 1000~9999。排除權證(含英文如 00400A)、ETF(00xx)、特別股等
        if not re.fullmatch(r"[1-9]\d{3}", code):
            continue
        val = _num(d.get("TradeValue")) or 0
        if val <= 0:
            continue
        rows.append((code, str(d.get("Name", "")).strip(), val))
    rows.sort(key=lambda x: x[2], reverse=True)
    return [(c, n) for c, n, _ in rows[:top_n]]


def twse_valuation_all():
    """證交所：一次抓全上市本益比/淨值比/殖利率 {code: {pe,pb,yld}}。"""
    out = {}
    try:
        for d in requests.get(TWSE_BWIBBU, timeout=25).json():
            c = str(d.get("Code", ""))
            pe, pb, y = _num(d.get("PEratio")), _num(d.get("PBratio")), _num(d.get("DividendYield"))
            out[c] = {"pe": pe if (pe and pe > 0) else None,
                      "pb": pb if (pb and pb > 0) else None,
                      "yld": y if y else None}
    except Exception:
        pass
    return out


@app.get("/api/categories")
def categories():
    return [{"key": k, "name": v["name"], "count": len(v["stocks"])}
            for k, v in CATEGORIES.items()]


@app.get("/api/market")
def market(force: int = 0, n: int = 200):
    """全上市低點掃描：先用價格/技術初篩，通過的才補估值與營收。"""
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"{today}:market"
    if not force and key in _cache:
        return _cache[key]

    uni = twse_universe(n)
    if not uni:
        return {"error": "無法取得上市清單", "rows": []}
    names = {c: nm for c, nm in uni}
    codes = [c for c, _ in uni]
    tickers = [c + ".TW" for c in codes]

    try:
        raw = yf.download(tickers, period="3y", interval="1d",
                          progress=False, group_by="column", threads=True)
        close = raw["Close"] if "Close" in raw else raw
    except Exception as e:
        return {"error": f"price download failed: {e}", "rows": []}

    # 第一層：價格/技術初篩（AND 門檻）
    prelim = []
    for code in codes:
        t = code + ".TW"
        try:
            s = close[t].dropna() if t in close else None
        except Exception:
            s = None
        if s is None or len(s) < 60:
            continue
        last = float(s.iloc[-1]); hi = float(s.max()); lo = float(s.min())
        pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50
        ma200 = float(s.rolling(200).mean().dropna().iloc[-1]) if len(s) >= 200 else None
        dist_ma = (last / ma200 - 1) * 100 if ma200 else None
        rsi = _rsi(s)
        dd = (last / hi - 1) * 100
        if pos >= MARKET_THRESH["pos"]:
            continue
        if rsi is None or rsi >= MARKET_THRESH["rsi"]:
            continue
        if dd >= MARKET_THRESH["drawdown"]:
            continue
        prelim.append({"code": code, "name": names.get(code, code), "price": round(last, 2),
                       "pos": round(pos, 1), "drawdown": round(dd, 1),
                       "rsi": round(rsi, 1),
                       "dist_ma": round(dist_ma, 1) if dist_ma is not None else None})

    # 第二層：估值（證交所全市場一次）＋ 營收（僅通過者）
    val_all = twse_valuation_all()
    surv = [r["code"] for r in prelim]
    revmap = {}
    if surv:
        with ThreadPoolExecutor(max_workers=6) as ex:
            revs = list(ex.map(finmind_rev_growth, surv))
        revmap = {surv[i]: revs[i] for i in range(len(surv))}

    pe_s = _rank_scores([val_all.get(r["code"], {}).get("pe") for r in prelim], False)
    pb_s = _rank_scores([val_all.get(r["code"], {}).get("pb") for r in prelim], False)
    yl_s = _rank_scores([val_all.get(r["code"], {}).get("yld") for r in prelim], True)

    rows = []
    for idx, r in enumerate(prelim):
        v = val_all.get(r["code"], {})
        r["pe"] = round(v["pe"], 1) if v.get("pe") else None
        r["pb"] = round(v["pb"], 2) if v.get("pb") else None
        r["yld"] = round(v["yld"], 2) if v.get("yld") else None
        rv = revmap.get(r["code"])
        r["rev"] = rv
        r["rev_src"] = "月" if rv is not None else None
        r["cyclical"] = CYCLICAL.get(r["code"])

        price_score = 100 - r["pos"]
        rsi_score = max(0, min(100, (60 - r["rsi"]) / 40 * 100)) if r["rsi"] is not None else None
        ma_score = max(0, min(100, (-r["dist_ma"]) / 20 * 100)) if r["dist_ma"] is not None else None
        tp = [x for x in (rsi_score, ma_score) if x is not None]
        tech = sum(tp) / len(tp) if tp else None
        vp = [x for x in (pe_s[idx], pb_s[idx], yl_s[idx]) if x is not None]
        val = sum(vp) / len(vp) if vp else None
        cp = []
        if price_score is not None: cp.append((0.40, price_score))
        if tech is not None: cp.append((0.30, tech))
        if val is not None: cp.append((0.30, val))
        ws = sum(w for w, _ in cp)
        r["score"] = round(sum(w * x for w, x in cp) / ws, 1) if ws else 0
        rows.append(r)

    rows.sort(key=lambda r: r["score"], reverse=True)
    result = {"updated": datetime.now(timezone.utc).isoformat(),
              "cat": "market", "cat_name": "全上市低點", "is_etf": False,
              "scanned": len(codes), "passed": len(rows),
              "count": len(rows), "rows": rows}
    _cache[key] = result
    return result


@app.get("/api/all")
def scan_all(force: int = 0, mode: str = "strict"):
    """全部觀察池低點掃描：先價格/技術初篩（嚴格/放寬），通過的才補估值與營收。"""
    if mode not in THRESH:
        mode = "strict"
    th = THRESH[mode]
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"{today}:all:{mode}"
    if not force and key in _cache:
        return _cache[key]

    codes = list(ALL_POOL.keys())
    tickers = [c + ".TW" for c in codes]
    try:
        raw = yf.download(tickers, period="3y", interval="1d",
                          progress=False, group_by="column", threads=True)
        close = raw["Close"] if "Close" in raw else raw
    except Exception as e:
        return {"error": f"price download failed: {e}", "rows": []}

    # 第一層：價格/技術初篩（AND 門檻）
    prelim = []
    for code in codes:
        t = code + ".TW"
        try:
            s = close[t].dropna() if t in close else None
        except Exception:
            s = None
        if s is None or len(s) < 60:
            continue
        last = float(s.iloc[-1]); hi = float(s.max()); lo = float(s.min())
        pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50
        ma200 = float(s.rolling(200).mean().dropna().iloc[-1]) if len(s) >= 200 else None
        dist_ma = (last / ma200 - 1) * 100 if ma200 else None
        rsi = _rsi(s)
        dd = (last / hi - 1) * 100
        if pos >= th["pos"]:
            continue
        if rsi is None or rsi >= th["rsi"]:
            continue
        if dd >= th["drawdown"]:
            continue
        prelim.append({"code": code, "name": ALL_POOL[code], "price": round(last, 2),
                       "pos": round(pos, 1), "drawdown": round(dd, 1),
                       "rsi": round(rsi, 1),
                       "dist_ma": round(dist_ma, 1) if dist_ma is not None else None})

    # 第二層：估值＋營收（皆 FinMind，僅通過者）
    surv = [r["code"] for r in prelim]
    vals, revmap = {}, {}
    if surv:
        with ThreadPoolExecutor(max_workers=8) as ex:
            vlist = list(ex.map(_valuation, surv))
        with ThreadPoolExecutor(max_workers=8) as ex:
            rlist = list(ex.map(finmind_rev_growth, surv))
        vals = {surv[i]: vlist[i] for i in range(len(surv))}
        revmap = {surv[i]: rlist[i] for i in range(len(surv))}

    pe_s = _rank_scores([vals.get(r["code"], {}).get("pe") for r in prelim], False)
    pb_s = _rank_scores([vals.get(r["code"], {}).get("pb") for r in prelim], False)
    yl_s = _rank_scores([vals.get(r["code"], {}).get("yld") for r in prelim], True)

    rows = []
    for idx, r in enumerate(prelim):
        v = vals.get(r["code"], {})
        r["pe"] = round(v["pe"], 1) if v.get("pe") else None
        r["pb"] = round(v["pb"], 2) if v.get("pb") else None
        r["yld"] = round(v["yld"], 2) if v.get("yld") else None
        rv = revmap.get(r["code"])
        r["rev"] = rv
        r["rev_src"] = "月" if rv is not None else None
        r["cyclical"] = CYCLICAL.get(r["code"])

        price_score = 100 - r["pos"]
        rsi_score = max(0, min(100, (60 - r["rsi"]) / 40 * 100)) if r["rsi"] is not None else None
        ma_score = max(0, min(100, (-r["dist_ma"]) / 20 * 100)) if r["dist_ma"] is not None else None
        tp = [x for x in (rsi_score, ma_score) if x is not None]
        tech = sum(tp) / len(tp) if tp else None
        vp = [x for x in (pe_s[idx], pb_s[idx], yl_s[idx]) if x is not None]
        val = sum(vp) / len(vp) if vp else None
        cp = []
        if price_score is not None: cp.append((0.40, price_score))
        if tech is not None: cp.append((0.30, tech))
        if val is not None: cp.append((0.30, val))
        ws = sum(w for w, _ in cp)
        r["score"] = round(sum(w * x for w, x in cp) / ws, 1) if ws else 0
        rows.append(r)

    enrich_rows(rows)
    rows.sort(key=lambda r: r["score"], reverse=True)
    result = {"updated": datetime.now(timezone.utc).isoformat(),
              "cat": "all", "cat_name": "全部低點", "is_etf": False, "mode": mode,
              "scanned": len(codes), "passed": len(rows),
              "count": len(rows), "rows": rows}
    _cache[key] = result
    return result


@app.get("/api/screener")
def screener(cat: str = "t50", force: int = 0):
    if cat not in CATEGORIES:
        cat = "t50"
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"{today}:{cat}"
    if not force and key in _cache:
        return _cache[key]

    stocks = CATEGORIES[cat]["stocks"]
    is_etf = CATEGORIES[cat].get("etf", False)
    codes = list(stocks.keys())
    tickers = [c + ".TW" for c in codes]

    # 一次批次抓 5 年收盤（快）
    try:
        raw = yf.download(tickers, period="5y", interval="1d",
                          progress=False, group_by="column", threads=True)
        close = raw["Close"] if "Close" in raw else raw
    except Exception as e:
        return {"error": f"price download failed: {e}", "rows": []}

    # 平行抓估值與月營收年增率（皆 FinMind）。ETF 是基金、無估值/營收，直接略過、省額度
    if is_etf:
        vals = [{"pe": None, "pb": None, "yld": None} for _ in codes]
        revs = [None for _ in codes]
    else:
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
            "code": code, "name": stocks[code], "price": round(last, 2),
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

    if not is_etf:
        enrich_rows(rows)
    rows.sort(key=lambda r: r["score"], reverse=True)
    result = {"updated": datetime.now(timezone.utc).isoformat(),
              "cat": cat, "cat_name": CATEGORIES[cat]["name"], "is_etf": is_etf,
              "count": len(rows), "rows": rows}
    _cache[key] = result
    return result


# ============ 頁面 ============
@app.get("/")
def home():
    return FileResponse(os.path.join(BASE, "index.html"))


@app.get("/screener")
def screener_page():
    return FileResponse(os.path.join(BASE, "screener.html"))
