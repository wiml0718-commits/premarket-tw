"""
盤前風向 — 後端 API
用 Yahoo Finance（免費，約 15 分鐘延遲）抓齊台股開盤預判所需的數據。
前端會呼叫 GET /api/predict 拿到 JSON。

本機測試：
    pip install -r requirements.txt
    uvicorn app:app --reload --port 8000
    瀏覽器開 http://localhost:8000/api/predict
"""

from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf

app = FastAPI(title="premarket-tw")

# 允許手機網頁（不同網域）來讀，避免 CORS 被擋
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 要抓的代號
SYMS = {
    "sox":  "^SOX",       # 費城半導體
    "ndxf": "NQ=F",       # 那斯達克100 期貨
    "spxf": "ES=F",       # S&P500 期貨
    "asia": "^N225",      # 日經（亞股早盤代表）
    "tsm":  "TSM",        # 台積電 ADR
    "fx":   "USDTWD=X",   # 美元/台幣
    "tw":   "2330.TW",    # 台積電（台股，取前收）
}


def quote(sym):
    """回傳 (最新價, 前收價)，抓不到回 (None, None)。"""
    # 先試 fast_info（快又穩）
    try:
        fi = yf.Ticker(sym).fast_info
        last = getattr(fi, "last_price", None)
        prev = getattr(fi, "previous_close", None)
        if last and prev:
            return float(last), float(prev)
    except Exception:
        pass
    # 退而求其次：用日線歷史
    try:
        h = yf.Ticker(sym).history(period="5d")
        if len(h) >= 2:
            return float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
        if len(h) == 1:
            c = float(h["Close"].iloc[-1])
            return c, c
    except Exception:
        pass
    return None, None


def pct(sym):
    """漲跌百分比，抓不到回 None。"""
    last, prev = quote(sym)
    if last is None or not prev:
        return None
    return round((last - prev) / prev * 100, 2)


@app.get("/api/predict")
def predict():
    # 盤前 / 早盤百分比
    data = {
        "sox":  pct(SYMS["sox"]),
        "ndxf": pct(SYMS["ndxf"]),
        "spxf": pct(SYMS["spxf"]),
        "asia": pct(SYMS["asia"]),
    }

    # ADR 溢價所需的三個原始值（換算交給前端，方便你看得到、能微調）
    tsm_last, _ = quote(SYMS["tsm"])
    fx_last, _  = quote(SYMS["fx"])
    tw_last, _  = quote(SYMS["tw"])

    data["adr_price"] = round(tsm_last, 2) if tsm_last else None
    data["fx"]        = round(fx_last, 3) if fx_last else None
    data["tsmc_tw"]   = round(tw_last, 1) if tw_last else None
    data["updated"]   = datetime.now(timezone.utc).isoformat()
    return data


@app.get("/")
def root():
    return {"ok": True, "endpoint": "/api/predict"}
