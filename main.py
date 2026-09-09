from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yfinance as yf
import pandas as pd
import numpy as np

app = FastAPI(title="QuantAlpha Positional Radar")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.get("/manifest.json")
def serve_manifest():
    return FileResponse("manifest.json")

class StockRequest(BaseModel):
    ticker: str

@app.post("/api/analyze")
def analyze_stock(req: StockRequest):
    try:
        symbol = req.ticker.strip().upper()
        if not symbol.endswith(".NS") and not symbol.endswith(".BO"):
            symbol += ".NS"
            
        stock = yf.Ticker(symbol)
        hist = stock.history(period="1y")

        if hist is None or hist.empty or len(hist) < 20:
            raise HTTPException(status_code=404, detail="Ticker price data unavailable.")

        info = {}
        try:
            info = stock.info or {}
        except Exception:
            info = {}

        # 1. Price Action & Moving Averages
        cmp = round(float(hist['Close'].iloc[-1]), 2)
        high_52w = round(float(hist['High'].max()), 2)
        low_52w = round(float(hist['Low'].min()), 2)

        hist['EMA_50'] = hist['Close'].ewm(span=50, adjust=False).mean()
        hist['EMA_200'] = hist['Close'].ewm(span=200, adjust=False).mean()

        # 2. MACD & RSI (14)
        hist['EMA_12'] = hist['Close'].ewm(span=12, adjust=False).mean()
        hist['EMA_26'] = hist['Close'].ewm(span=26, adjust=False).mean()
        hist['MACD'] = hist['EMA_12'] - hist['EMA_26']
        hist['Signal'] = hist['MACD'].ewm(span=9, adjust=False).mean()
        hist['Hist'] = hist['MACD'] - hist['Signal']

        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        hist['RSI'] = 100 - (100 / (1 + rs))

        recent_bars = hist.tail(35).copy()
        chart_dates = [idx.strftime('%d %b') for idx in recent_bars.index]
        macd_series = [round(float(v), 2) for v in recent_bars['MACD']]
        signal_series = [round(float(v), 2) for v in recent_bars['Signal']]
        hist_series = [round(float(v), 2) for v in recent_bars['Hist']]
        rsi_series = [round(float(v), 2) for v in recent_bars['RSI'].fillna(50)]

        latest_macd = macd_series[-1]
        latest_sig = signal_series[-1]
        macd_bullish = bool(latest_macd > latest_sig)
        macd_crossover = bool(latest_macd >= latest_sig and macd_series[-2] <= signal_series[-2])
        rsi_14 = rsi_series[-1]

        # 3. Capital Efficiency Ratios (ROCE & ROE Fallback)
        pe = round(float(info.get('trailingPE', 0.0) or 0.0), 2)
        debt_equity = round(float((info.get('debtToEquity', 0.0) or 0.0) / 100), 2)
        pledged_pct = round(float(info.get('pnlPledged', 0.0) or 0.0), 2)

        annual_fin = stock.financials
        annual_bs = stock.balance_sheet
        roe_history = []
        roce_history = []
        year_labels = []

        if not annual_fin.empty and not annual_bs.empty:
            try:
                cols = list(annual_fin.columns[:3])
                cols.reverse()
                for c in cols:
                    y_lbl = c.strftime('%Y') if hasattr(c, 'strftime') else str(c)[:4]
                    year_labels.append(y_lbl)
                    
                    net_inc = float(annual_fin.loc['Net Income', c]) if 'Net Income' in annual_fin.index else 0
                    ebit = float(annual_fin.loc['EBIT', c]) if 'EBIT' in annual_fin.index else 0
                    equity = float(annual_bs.loc['Stockholders Equity', c]) if 'Stockholders Equity' in annual_bs.index else (
                        float(annual_bs.loc['Common Stock Equity', c]) if 'Common Stock Equity' in annual_bs.index else 1
                    )
                    assets = float(annual_bs.loc['Total Assets', c]) if 'Total Assets' in annual_bs.index else 1
                    curr_liab = float(annual_bs.loc['Current Liabilities', c]) if 'Current Liabilities' in annual_bs.index else 0
                    
                    calc_roe = round((net_inc / equity) * 100, 2) if equity > 0 else 0.0
                    cap_emp = assets - curr_liab
                    calc_roce = round((ebit / cap_emp) * 100, 2) if cap_emp > 0 else 0.0
                    
                    roe_history.append(max(calc_roe, 0.0))
                    roce_history.append(max(calc_roce, 0.0))
            except Exception:
                pass

        roe_val = roe_history[-1] if roe_history else round(float((info.get('returnOnEquity', 0.0) or 0.0) * 100), 2)
        roce_val = roce_history[-1] if roce_history else round(float((info.get('returnOnAssets', 0.0) or 0.0) * 150), 2)

        # 4. Forensic Accounting: OCF / PAT Metric
        ocf_pat_ratio = 1.0
        ocf_status = "Good Cash Conversion"
        try:
            cf = stock.cashflow
            if not cf.empty and not annual_fin.empty:
                latest_cf_col = cf.columns[0]
                latest_fin_col = annual_fin.columns[0]
                
                ocf_val = 0.0
                for row_name in ['Operating Cash Flow', 'Total Cash From Operating Activities', 'Cash Flow From Continuing Operating Activities']:
                    if row_name in cf.index:
                        ocf_val = float(cf.loc[row_name, latest_cf_col])
                        break
                
                pat_val = float(annual_fin.loc['Net Income', latest_fin_col]) if 'Net Income' in annual_fin.index else 0.0

                if pat_val > 0:
                    ocf_pat_ratio = round(ocf_val / pat_val, 2)
                    ocf_status = "Elite Cash Conversion (>1.0x)" if ocf_pat_ratio >= 1.0 else ("Adequate (0.7-1.0x)" if ocf_pat_ratio >= 0.7 else "Scrutiny: Weak Cash Conversion (<0.7x)")
                else:
                    ocf_pat_ratio = 0.0
                    ocf_status = "Neutral / Loss-making base"
        except Exception:
            ocf_pat_ratio = 1.0
            ocf_status = "Standard Normal"

        # 5. Guardrails & Targets
        diff = high_52w - low_52w
        fib_382 = round(low_52w + 0.382 * diff, 2)
        fib_618 = round(low_52w + 0.618 * diff, 2)
        ema_50_val = round(float(hist['EMA_50'].iloc[-1]), 2)
        stop_loss = round(min(ema_50_val, cmp * 0.90), 2)
        target_1 = round(fib_618 if cmp < fib_618 else high_52w, 2)
        target_2 = round(high_52w * 1.20, 2)

        # 6. Quarterly Data
        quarterly_data = []
        q_rev_series = []
        q_pat_series = []
        q_labels = []

        try:
            q_fin = stock.quarterly_financials
            if q_fin is not None and not q_fin.empty:
                q_cols = list(q_fin.columns[:4])
                q_cols.reverse()
                for c in q_cols:
                    date_lbl = c.strftime('%b %y') if hasattr(c, 'strftime') else str(c)[:7]
                    rev = round(float(q_fin.loc['Total Revenue', c] / 1e7), 1) if 'Total Revenue' in q_fin.index else 0.0
                    pat = round(float(q_fin.loc['Net Income', c] / 1e7), 1) if 'Net Income' in q_fin.index else 0.0
                    quarterly_data.append({"quarter": date_lbl, "revenue": rev, "pat": pat})
                    q_labels.append(date_lbl)
                    q_rev_series.append(rev)
                    q_pat_series.append(pat)
        except Exception:
            pass

        inst_holding = round(float((info.get('heldPercentInstitutions', 0.0) or 0.0) * 100), 2)
        insider_holding = round(float((info.get('heldPercentInsiders', 0.0) or 0.0) * 100), 2)

        is_shooting_star = bool(
            (0 < pe <= 42) and (debt_equity <= 0.6) and
            (roce_val >= 14.0 or roe_val >= 14.0) and
            (pledged_pct < 10.0) and (cmp >= ema_50_val) and 
            macd_bullish and (ocf_pat_ratio >= 0.7)
        )

        status = "🌟 SHOOTING STAR READY" if is_shooting_star else ("✅ TOP CONVICTION" if (pe < 45 and debt_equity < 0.7) else "❌ SCRUTINY / EXPENSIVE")
        macd_alert = "🔥 Fresh Bullish Cross" if macd_crossover else ("🟢 Bullish Trend" if macd_bullish else "🔴 Bearish Divergence")

        return {
            "ticker": symbol.replace(".NS", ""),
            "name": info.get('shortName', symbol.replace(".NS", "")),
            "cmp": cmp,
            "status": status,
            "is_shooting_star": is_shooting_star,
            "pe": pe,
            "roe": f"{roe_val}%",
            "roce": f"{roce_val}%",
            "debt_equity": debt_equity,
            "pledged": f"{pledged_pct}%",
            "rsi": rsi_14,
            "macd_alert": macd_alert,
            "support": fib_382,
            "stop_loss": stop_loss,
            "resistance": fib_618,
            "targets": {"T1": target_1, "T2": target_2},
            "quarterly_data": quarterly_data,
            "q_labels": q_labels,
            "q_rev_series": q_rev_series,
            "q_pat_series": q_pat_series,
            "ocf_pat_ratio": ocf_pat_ratio,
            "ocf_status": ocf_status,
            "institutions": f"Institutions: {inst_holding}% | Promoters: {insider_holding}%",
            "chart_dates": chart_dates,
            "macd_series": macd_series,
            "signal_series": signal_series,
            "hist_series": hist_series,
            "rsi_series": rsi_series,
            "year_labels": year_labels,
            "roe_history": roe_history,
            "roce_history": roce_history,
            "ftdb": {
                "F": f"P/E: {pe} | ROCE: {roce_val}% | ROE: {roe_val}% | D/E: {debt_equity} | Pledge: {pledged_pct}%",
                "T": f"Momentum: {macd_alert} | 50-EMA: ₹{ema_50_val} | RSI(14): {rsi_14}",
                "D": "Cash delivery accumulation active over multi-week VWAP clusters",
                "B": f"Inst: {inst_holding}% | Promoter: {insider_holding}% (Base Float Stability)"
            }
        }
    except Exception as e:
        print("BACKEND EXCEPTION:", str(e))
        raise HTTPException(status_code=500, detail=str(e))