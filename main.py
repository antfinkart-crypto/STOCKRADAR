from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime

app = FastAPI(title="AntFinServ Positional Radar & Quant Portfolio Terminal")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_FILE = "users_db.json"
PORTFOLIO_FILE = "portfolio_db.json"
HISTORICAL_PORTFOLIO_FILE = "portfolios_history.json"
LOGS_FILE = "activity_logs.json"

DEFAULT_DATA = {
    "pins": {
        "942040": {
            "role": "admin",
            "name": "Rana Sahib (Admin)",
            "mobile": "Master Desk",
            "email": "admin@antfinserv.com",
            "active": True
        }
    }
}

# --- 5-TIER TOP 750 NSE INDEX UNIVERSE ---
INDEX_UNIVERSES = {
    "midcap_150": [
        "DIXON.NS", "POLYCAB.NS", "PERSISTENT.NS", "COFORGE.NS", "CUMMINSIND.NS",
        "SUPRAJIT.NS", "AIAENG.NS", "FEDERALBNK.NS", "MPHASIS.NS", "ASHOKLEY.NS",
        "SUZLON.NS", "AUROPHARMA.NS", "BHARATFORG.NS", "MAXHEALTH.NS", "LUPIN.NS"
    ],
    "smallcap_250": [
        "CGCL.NS", "SANDHAR.NS", "GANDHAR.NS", "KAYNES.NS", "BSE.NS",
        "CDSL.NS", "HAPPSTMNDS.NS", "ALLDIGI.NS", "ELECON.NS", "TEJASNET.NS",
        "JBCHEPHARM.NS", "CASTROLIND.NS", "CENTURYTEX.NS", "PPLPHARMA.NS", "BLS.NS"
    ],
    "microcap_250": [
        "SOLARINDS.NS", "WOCKPHARMA.NS", "GENUSPOWER.NS", "AURIONPRO.NS", "SANGHVIMOV.NS",
        "DREDGECORP.NS", "NELCO.NS", "APOLLOPIPE.NS", "PITTIENG.NS", "RICOAUTO.NS",
        "MARKSANS.NS", "TIIL.NS", "RATEGAIN.NS", "GOKEX.NS", "HGINFRA.NS"
    ],
    "next_50": [
        "HAL.NS", "BEL.NS", "TRENT.NS", "SIEMENS.NS", "DLF.NS",
        "VBL.NS", "CHOLAFIN.NS", "PFC.NS", "RECLTD.NS", "BANKBARODA.NS"
    ],
    "nifty_50": [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "BHARTIARTL.NS", "ICICIBANK.NS",
        "INFY.NS", "LT.NS", "SBIN.NS", "ITC.NS", "HINDUNILVR.NS"
    ]
}

def load_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump(DEFAULT_DATA, f, indent=2)
        return DEFAULT_DATA
    try:
        with open(DB_FILE, "r") as f:
            data = json.load(f)
            if "942040" not in data.get("pins", {}):
                data["pins"]["942040"] = DEFAULT_DATA["pins"]["942040"]
            return data
    except Exception:
        return DEFAULT_DATA

def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return []
    try:
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_portfolio(p):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, indent=2)

def load_portfolio_history():
    if not os.path.exists(HISTORICAL_PORTFOLIO_FILE):
        return []
    try:
        with open(HISTORICAL_PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_portfolio_history(records):
    with open(HISTORICAL_PORTFOLIO_FILE, "w") as f:
        json.dump(records, f, indent=2)

def verify_pin(x_app_pin: str = Header(None)):
    if not x_app_pin:
        raise HTTPException(status_code=401, detail="PIN_REQUIRED")
    db = load_db()
    user = db.get("pins", {}).get(str(x_app_pin))
    if not user or not user.get("active", False):
        raise HTTPException(status_code=403, detail="INVALID_PIN")
    return user

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.get("/manifest.json")
def serve_manifest():
    return FileResponse("manifest.json")

# --- LIVE TICKER ENGINE ---
TICKER_CACHE = {"timestamp": 0, "data": {}}
N50_SEEDS = [
    {"symbol": "RELIANCE", "ticker": "RELIANCE.NS", "base": 2985.0},
    {"symbol": "HDFCBANK", "ticker": "HDFCBANK.NS", "base": 1648.5},
    {"symbol": "ICICIBANK", "ticker": "ICICIBANK.NS", "base": 1230.0},
    {"symbol": "INFY", "ticker": "INFY.NS", "base": 1890.0},
    {"symbol": "TCS", "ticker": "TCS.NS", "base": 4210.0},
    {"symbol": "LT", "ticker": "LT.NS", "base": 3615.0},
    {"symbol": "BHARTIARTL", "ticker": "BHARTIARTL.NS", "base": 1560.0},
    {"symbol": "SBIN", "ticker": "SBIN.NS", "base": 815.0}
]

N500_SEEDS = [
    {"symbol": "TRENT", "ticker": "TRENT.NS", "base": 6940.0},
    {"symbol": "DIXON", "ticker": "DIXON.NS", "base": 12850.0},
    {"symbol": "POLYCAB", "ticker": "POLYCAB.NS", "base": 6780.0},
    {"symbol": "HAL", "ticker": "HAL.NS", "base": 4720.0},
    {"symbol": "CGCL", "ticker": "CGCL.NS", "base": 266.0},
    {"symbol": "SANDHAR", "ticker": "SANDHAR.NS", "base": 618.0},
    {"symbol": "GANDHAR", "ticker": "GANDHAR.NS", "base": 272.0},
    {"symbol": "ALLDIGI", "ticker": "ALLDIGI.NS", "base": 795.0}
]

@app.get("/api/market-ticker")
def get_market_ticker():
    global TICKER_CACHE
    now = time.time()
    if now - TICKER_CACHE["timestamp"] < 300 and TICKER_CACHE["data"]:
        return TICKER_CACHE["data"]

    def build_feed(seeds):
        res = []
        for s in seeds:
            try:
                t = yf.Ticker(s["ticker"])
                h = t.history(period="5d")
                if h is not None and not h.empty and len(h) >= 2:
                    c_now = float(h['Close'].iloc[-1])
                    c_prev = float(h['Close'].iloc[-2])
                    chg = round(((c_now - c_prev) / c_prev) * 100, 2)
                    res.append({"symbol": s["symbol"], "price": round(c_now, 1), "chg": chg})
                else:
                    res.append({"symbol": s["symbol"], "price": s["base"], "chg": 0.45})
            except Exception:
                res.append({"symbol": s["symbol"], "price": s["base"], "chg": 0.45})
        return res

    feed = {
        "n50_gainers": sorted(build_feed(N50_SEEDS), key=lambda x: x["chg"], reverse=True)[:3],
        "n50_losers": sorted(build_feed(N50_SEEDS), key=lambda x: x["chg"])[:3],
        "n500_gainers": sorted(build_feed(N500_SEEDS), key=lambda x: x["chg"], reverse=True)[:4],
        "n500_losers": sorted(build_feed(N500_SEEDS), key=lambda x: x["chg"])[:4]
    }
    TICKER_CACHE["timestamp"] = now
    TICKER_CACHE["data"] = feed
    return feed

@app.get("/api/search-companies")
def search_companies(q: str):
    query = q.strip()
    if len(query) < 1:
        return []
    results = []
    alias_table = {
        "CGCL": {"name": "Capri Global Capital Ltd", "symbol": "CGCL.NS", "display": "CGCL"},
        "SANDHAR": {"name": "Sandhar Technologies Ltd", "symbol": "SANDHAR.NS", "display": "SANDHAR"},
        "GANDHAR": {"name": "Gandhar Oil Refinery India", "symbol": "GANDHAR.NS", "display": "GANDHAR"},
        "TCS": {"name": "Tata Consultancy Services", "symbol": "TCS.NS", "display": "TCS"},
        "TATATECH": {"name": "Tata Technologies Limited", "symbol": "TATATECH.NS", "display": "TATATECH"},
        "SEPC": {"name": "SEPC Limited", "symbol": "SEPC.NS", "display": "SEPC"},
        "PATINTLOG": {"name": "Patel Integrated Logistics", "symbol": "PATINTLOG.NS", "display": "PATINTLOG"},
        "VINCOFE": {"name": "Vintage Coffee & Beverages", "symbol": "VINCOFE.NS", "display": "VINCOFE"}
    }
    upper_q = query.upper()
    for k, v in alias_table.items():
        if upper_q in k or upper_q in v["name"].upper():
            results.append(v)
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(query)}&quotesCount=8&newsCount=0"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            for item in data.get("quotes", []):
                sym = item.get("symbol", "")
                if sym.endswith(".NS") or sym.endswith(".BO"):
                    clean = sym.replace(".NS", "").replace(".BO", "")
                    name = item.get("shortname") or item.get("longname") or clean
                    if not any(r["symbol"] == sym for r in results):
                        results.append({"name": name, "symbol": sym, "display": clean})
    except Exception:
        pass
    return results[:8]

class VerifyPinRequest(BaseModel):
    pin: str

@app.post("/api/verify-pin")
def api_verify_pin(req: VerifyPinRequest):
    db = load_db()
    user = db.get("pins", {}).get(req.pin)
    if not user or not user.get("active", False):
        raise HTTPException(status_code=403, detail="INVALID_PIN")
    return {"status": "ok", "role": user["role"], "name": user["name"]}

# --- FORENSIC PARSER WITH RSI 51-69 & PLAN B ENGINE ---
def parse_stock_technicals_and_forensics(sym: str):
    stock = yf.Ticker(sym)
    hist = stock.history(period="1y")
    if hist is None or hist.empty or len(hist) < 20:
        return None

    info = stock.info or {}
    cmp = round(float(hist['Close'].iloc[-1]), 2)
    mcap_cr = round(float(info.get('marketCap') or 0.0) / 1e7, 1)

    vol_10 = hist['Volume'].tail(10)
    turnover_cr = round(float((vol_10 * hist['Close'].tail(10)).mean()) / 1e7, 2)

    # EMAs
    hist['EMA_20'] = hist['Close'].ewm(span=20, adjust=False).mean()
    hist['EMA_50'] = hist['Close'].ewm(span=50, adjust=False).mean()
    hist['EMA_63'] = hist['Close'].ewm(span=63, adjust=False).mean()
    hist['EMA_200'] = hist['Close'].ewm(span=200, adjust=False).mean()

    ema_20 = round(float(hist['EMA_20'].iloc[-1]), 2)
    ema_50 = round(float(hist['EMA_50'].iloc[-1]), 2)
    ema_63 = round(float(hist['EMA_63'].iloc[-1]), 2)
    ema_200 = round(float(hist['EMA_200'].iloc[-1]), 2)

    # RSI
    delta = hist['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    hist['RSI'] = 100 - (100 / (1 + rs))
    rsi_14 = round(float(hist['RSI'].iloc[-1]), 2) if not pd.isna(hist['RSI'].iloc[-1]) else 50.0

    # Sparkline
    hist_weekly = hist['Close'].resample('W').last().dropna().tail(26)
    sparkline_series = [round(float(v), 2) for v in hist_weekly]

    # Fundamentals
    pe = float(info.get('trailingPE') or 0.0)
    eps = float(info.get('trailingEps') or 0.0)
    if pe <= 0 or pe > 250:
        pe = round(cmp / eps, 2) if eps > 0 else 24.5
    else:
        pe = round(pe, 2)

    peg = round(float(info.get('pegRatio') or 1.15), 2)
    de = float(info.get('debtToEquity') or 0.0)
    if de > 5.0:
        de = de / 100.0
    de = round(de, 2)
    pledged = round(float(info.get('pnlPledged') or 0.0), 2)

    inst = round(float(info.get('heldPercentInstitutions') or 0.0) * 100, 1)
    insider = round(float(info.get('heldPercentInsiders') or 0.0) * 100, 1)
    if inst == 0 and insider == 0:
        inst, insider = 28.5, 54.0

    annual_fin = stock.financials
    annual_bs = stock.balance_sheet
    q_fin = stock.quarterly_financials

    roce_val = 18.0
    if not annual_fin.empty and not annual_bs.empty:
        try:
            c0 = annual_fin.columns[0]
            ebit = float(annual_fin.loc['EBIT', c0]) if 'EBIT' in annual_fin.index else 0
            assets = float(annual_bs.loc['Total Assets', c0]) if 'Total Assets' in annual_bs.index else 1
            curr_liab = float(annual_bs.loc['Current Liabilities', c0]) if 'Current Liabilities' in annual_bs.index else 0
            cap_emp = assets - curr_liab
            if cap_emp > 0 and ebit > 0:
                roce_val = round((ebit / cap_emp) * 100, 1)
        except Exception:
            pass

    ocf_pat_ratio = 1.15
    try:
        cf = stock.cashflow
        if not cf.empty and not annual_fin.empty:
            c0 = cf.columns[0]
            f0 = annual_fin.columns[0]
            ocf_val = 0.0
            for r in ['Operating Cash Flow', 'Total Cash From Operating Activities']:
                if r in cf.index:
                    ocf_val = float(cf.loc[r, c0])
                    break
            pat_val = float(annual_fin.loc['Net Income', f0]) if 'Net Income' in annual_fin.index else 1.0
            if pat_val != 0:
                ocf_pat_ratio = round(ocf_val / pat_val, 2)
    except Exception:
        ocf_pat_ratio = 1.15

    # Pivots
    high_52w = round(float(hist['High'].max()), 2)
    low_52w = round(float(hist['Low'].min()), 2)
    pivot_p = round((high_52w + low_52w + cmp) / 3, 2)
    r1 = round((2 * pivot_p) - low_52w, 2)
    s1 = round((2 * pivot_p) - high_52w, 2)

    # Bhangar-cap check
    is_bhangar_cap = bool(mcap_cr < 2000.0 or turnover_cr < 1.5 or pledged > 10.0)
    bhangar_flags = []
    if mcap_cr < 2000.0:
        bhangar_flags.append(f"Sub-scale Microcap (₹{mcap_cr} Cr < ₹2,000 Cr)")
    if turnover_cr < 1.5:
        bhangar_flags.append(f"Low Daily Liquidity (Turnover ₹{turnover_cr} Cr < ₹1.5 Cr)")
    if pledged > 5.0:
        bhangar_flags.append(f"High Promoter Pledge ({pledged}% > 5%)")

    # Technical Alignment
    is_stage_2 = bool(cmp >= ema_20 and cmp >= ema_50 and cmp >= ema_63 and cmp >= ema_200)
    # RSI Corridor 51 - 69
    is_rsi_safe = bool(51.0 <= rsi_14 <= 69.0)

    # PLAN A: STRICT VALUE MOMENTUM
    is_plan_a_qualified = bool(
        not is_bhangar_cap and 
        is_stage_2 and 
        is_rsi_safe and 
        pe <= 38.0 and 
        de <= 0.30 and 
        pledged <= 2.0 and 
        ocf_pat_ratio >= 0.75 and 
        roce_val >= 16.0
    )

    # PLAN B: ELITE GROWTH EXPANSION FALLBACK (Allows P/E up to 45 if ROCE > 22% & clean leverage)
    is_plan_b_qualified = bool(
        not is_bhangar_cap and
        not is_plan_a_qualified and
        is_stage_2 and
        (48.0 <= rsi_14 <= 71.0) and
        pe <= 45.0 and
        de <= 0.35 and
        pledged <= 1.0 and
        ocf_pat_ratio >= 0.75 and
        roce_val >= 20.0
    )

    is_discarded = bool(not is_plan_a_qualified and not is_plan_b_qualified and not is_bhangar_cap)

    if is_bhangar_cap:
        badge_code = "BHANGAR_CAP"
        badge_title = "🚩 BHANGAR CAP / HIGH FRICTION"
        summary_headline = "Stock falls outside institutional liquidity & size safety rules. Ineligible for model portfolio."
    elif is_plan_a_qualified:
        badge_code = "SHOOTING_STAR"
        badge_title = "🌟 SUPER STOCK (PLAN A CORE)"
        summary_headline = "Grade-A setup: Full Stage-2 markup, RSI 51-69 accumulation, D/E <= 0.30, P/E <= 38."
    elif is_plan_b_qualified:
        badge_code = "PLAN_B_GROWTH"
        badge_title = "⚡ ELITE GROWTH (PLAN B FALLBACK)"
        summary_headline = "High capital efficiency backup: ROCE > 20% justifies slight valuation expansion."
    else:
        badge_code = "DISCARDED"
        badge_title = "❌ DISCARDED (POOR FORENSICS / EXPENSIVE)"
        summary_headline = "Fails institutional filters due to broken EMAs, high leverage, or weak cash flow conversion."

    clean_sym = sym.replace(".NS", "").replace(".BO", "")
    return {
        "ticker": clean_sym,
        "name": info.get('shortName') or info.get('longName') or clean_sym,
        "sector": info.get('sector') or "Diversified",
        "cmp": cmp,
        "mcap_cr": mcap_cr,
        "turnover_cr": turnover_cr,
        "is_bhangar_cap": is_bhangar_cap,
        "bhangar_flags": bhangar_flags,
        "badge_code": badge_code,
        "badge_title": badge_title,
        "summary_headline": summary_headline,
        "pe": pe,
        "peg": peg,
        "roce": f"{roce_val}%",
        "debt_equity": de,
        "pledged": f"{pledged}%",
        "rsi": rsi_14,
        "ema_20": ema_20,
        "ema_50": ema_50,
        "ema_63": ema_63,
        "ema_200": ema_200,
        "dual_sl": {
            "tier1_closing": f"Close < ₹{ema_20}",
            "tier2_intraday": f"Intraday <= ₹{round(cmp * 0.91, 1)}"
        },
        "pivots": {"S1": s1, "Pivot": pivot_p, "R1": r1},
        "ocf_pat_ratio": ocf_pat_ratio,
        "institutions": f"Institutions: {inst}% | Promoters: {insider}%",
        "sparkline_series": sparkline_series,
        "ftdb": {
            "F": f"P/E: {pe} | ROCE: {roce_val}% | D/E: {de} | OCF/PAT: {ocf_pat_ratio}x",
            "T": f"EMA-20: ₹{ema_20} | EMA-50: ₹{ema_50} | RSI: {rsi_14}",
            "D": f"Turnover: ₹{turnover_cr} Cr/day",
            "B": f"Inst: {inst}% | Prom: {insider}% | Pledge: {pledged}%"
        }
    }

class StockRequest(BaseModel):
    ticker: str

@app.post("/api/analyze")
def analyze_stock(req: StockRequest, user=Depends(verify_pin)):
    raw = req.ticker.strip().upper()
    if "/" in raw:
        raw = raw.split("/")[0].strip()

    alias = {
        "CGCL": "CGCL.NS", "SANDHAR": "SANDHAR.NS", "GANDHAR": "GANDHAR.NS",
        "TATATECH": "TATATECH.NS", "VINCOFE": "VINCOFE.NS", "TCS": "TCS.NS"
    }
    sym = alias.get(raw, raw)
    candidates = [sym] if sym.endswith(".NS") or sym.endswith(".BO") else [f"{sym}.NS", f"{sym}.BO", sym]

    for s in candidates:
        try:
            res = parse_stock_technicals_and_forensics(s)
            if res:
                return res
        except Exception:
            continue

    raise HTTPException(status_code=404, detail=f"Stock '{raw}' not found.")

@app.get("/api/screener/scan-universe")
def scan_universe(universe: str = "midcap_150", user=Depends(verify_pin)):
    tickers = INDEX_UNIVERSES.get(universe, INDEX_UNIVERSES["midcap_150"])
    results = []
    for s in tickers:
        try:
            d = parse_stock_technicals_and_forensics(s)
            if d and d["badge_code"] in ["SHOOTING_STAR", "PLAN_B_GROWTH"]:
                results.append({
                    "ticker": d["ticker"],
                    "name": d["name"],
                    "sector": d["sector"],
                    "cmp": d["cmp"],
                    "pe": d["pe"],
                    "roce": d["roce"],
                    "de": d["debt_equity"],
                    "ocf_pat": d["ocf_pat_ratio"],
                    "rsi": d["rsi"],
                    "badge_code": d["badge_code"],
                    "badge_title": d["badge_title"],
                    "sparkline": d["sparkline_series"],
                    "support": d["pivots"]["S1"],
                    "resistance": d["pivots"]["R1"]
                })
        except Exception:
            continue

    results.sort(key=lambda x: (x["badge_code"] == "SHOOTING_STAR", float(x["roce"].replace("%", ""))), reverse=True)
    return results

# --- PORTFOLIO & RUN HISTORY ---
class AddToPortfolioRequest(BaseModel):
    ticker: str
    name: str
    sector: str
    cmp: float
    badge_code: str
    roce: str
    ocf_pat_ratio: float
    support: float
    resistance: float

@app.get("/api/portfolio")
def get_portfolio(user=Depends(verify_pin)):
    p = load_portfolio()
    total_equities = len(p)
    equity_weight_pool = 60.0
    
    raw_weights = {item["ticker"]: (7.0 if item.get("badge_code") == "SHOOTING_STAR" else 5.0) for item in p}
    total_raw = sum(raw_weights.values()) or 1.0
    final_equities = []
    
    for item in p:
        sym = item["ticker"]
        assigned_w = round((raw_weights[sym] / total_raw) * equity_weight_pool, 1) if total_raw > 0 else 6.0
        assigned_w = min(assigned_w, 8.0) # Absolute 8% Cap

        final_equities.append({
            **item,
            "weight": assigned_w,
            "entry_zone": f"₹{item.get('support', item['cmp'])} - ₹{item['cmp']}",
            "stop_loss": f"₹{round(item['cmp'] * 0.91, 1)}",
            "target": f"₹{item.get('resistance', round(item['cmp'] * 1.2, 1))}"
        })

    return {
        "macro_allocation": {
            "core_equity": 60.0,
            "gold_hedge": 15.0,
            "liquid_arbitrage": 15.0,
            "tactical_cash_dry_powder": 10.0
        },
        "equity_basket": final_equities,
        "portfolio_stats": {
            "stock_count": total_equities,
            "target_range": "8–10 Stocks",
            "max_single_stock_cap": "8.0%"
        }
    }

@app.post("/api/portfolio/add")
def add_to_portfolio(req: AddToPortfolioRequest, user=Depends(verify_pin)):
    p = load_portfolio()
    if any(item["ticker"] == req.ticker for item in p):
        raise HTTPException(status_code=400, detail="Stock already in portfolio.")
    if len(p) >= 10:
        raise HTTPException(status_code=400, detail="Portfolio limit reached (10 stocks max).")
    p.append(req.dict())
    save_portfolio(p)
    return {"status": "ok", "message": f"{req.ticker} added to Quant Basket."}

@app.delete("/api/portfolio/{ticker}")
def remove_from_portfolio(ticker: str, user=Depends(verify_pin)):
    p = load_portfolio()
    p = [item for item in p if item["ticker"].upper() != ticker.upper()]
    save_portfolio(p)
    return {"status": "ok", "message": f"{ticker} removed from basket."}

class SavePortfolioRunRequest(BaseModel):
    capital: float
    gold_scheme: str
    arbitrage_scheme: str
    liquid_scheme: str

@app.post("/api/portfolio/save-run")
def save_portfolio_run(req: SavePortfolioRunRequest, user=Depends(verify_pin)):
    if req.capital < 500000.0:
        raise HTTPException(status_code=400, detail="Minimum capital required is ₹5,00,000.")
    
    p = load_portfolio()
    total_capital = req.capital
    r_pct = 1.0 if total_capital >= 1000000.0 else 1.5

    raw_weights = {item["ticker"]: (7.0 if item.get("badge_code") == "SHOOTING_STAR" else 5.0) for item in p}
    total_raw = sum(raw_weights.values()) or 1.0

    equity_records = []
    for item in p:
        sym = item["ticker"]
        w = round((raw_weights[sym] / total_raw) * 60.0, 2)
        w = min(w, 8.0)
        alloc_amt = round(total_capital * (w / 100.0), 2)
        qty = max(1, int(alloc_amt // item["cmp"]))
        sl_val = round(item["cmp"] * 0.91, 2)
        
        equity_records.append({
            "ticker": sym,
            "name": item["name"],
            "sector": item.get("sector", "General"),
            "weight_pct": w,
            "allocated_amt": alloc_amt,
            "cmp": item["cmp"],
            "quantity": qty,
            "entry_zone": f"₹{item.get('support', item['cmp'])} - ₹{item['cmp']}",
            "stop_loss": sl_val,
            "target": round(item.get("resistance", item["cmp"] * 1.20), 2),
            "dual_sl_alert": f"Close < EMA21 OR Tick <= ₹{sl_val}"
        })

    run_record = {
        "portfolio_id": f"AF-PORT-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_capital": total_capital,
        "risk_model": f"{r_pct}% 1R per trade",
        "allocations": {
            "core_equity": {"pct": 60.0, "amount": total_capital * 0.60, "basket": equity_records},
            "gold_hedge": {"pct": 15.0, "amount": total_capital * 0.15, "scheme": req.gold_scheme},
            "arbitrage_debt": {"pct": 15.0, "amount": total_capital * 0.15, "scheme": req.arbitrage_scheme},
            "tactical_liquid": {"pct": 10.0, "amount": total_capital * 0.10, "scheme": req.liquid_scheme}
        }
    }

    history = load_portfolio_history()
    history.append(run_record)
    save_portfolio_history(history)
    return {"status": "ok", "portfolio_id": run_record["portfolio_id"]}

@app.get("/api/portfolio/export-csv")
def export_portfolio_csv(user=Depends(verify_pin)):
    history = load_portfolio_history()
    if not history:
        raise HTTPException(status_code=404, detail="No portfolio saved yet.")
    
    latest = history[-1]
    csv_rows = ["Asset Class,Instrument / Scheme,Sector,Weight %,Allocated Amount (INR),CMP,Quantity,Entry Zone,Stop Loss,Target,Alert Trigger"]
    
    for eq in latest["allocations"]["core_equity"]["basket"]:
        csv_rows.append(f"Core Equity,{eq['name']} ({eq['ticker']}),{eq['sector']},{eq['weight_pct']}%,{eq['allocated_amt']},{eq['cmp']},{eq['quantity']},{eq['entry_zone']},{eq['stop_loss']},{eq['target']},{eq['dual_sl_alert']}")
    
    csv_rows.append(f"Gold Hedge,{latest['allocations']['gold_hedge']['scheme']},Precious Metals,15.0%,{latest['allocations']['gold_hedge']['amount']},NAV,Units/Lumpsum,Market Price,N/A,Macro Hedge,Rebalance on >20% move")
    csv_rows.append(f"Arbitrage / Low Duration,{latest['allocations']['arbitrage_debt']['scheme']},Fixed Income,15.0%,{latest['allocations']['arbitrage_debt']['amount']},NAV,Units,Accrual,N/A,Yield Buffer,Quarterly Review")
    csv_rows.append(f"Tactical Liquid Cash,{latest['allocations']['tactical_liquid']['scheme']},Cash Buffer,10.0%,{latest['allocations']['tactical_liquid']['amount']},NAV,Dry Powder,N/A,N/A,Opportunity Fund,Deploy on 5-8% index dip")

    csv_data = "\n".join(csv_rows)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={latest['portfolio_id']}_tracking.csv"}
    )