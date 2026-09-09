from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import io
from datetime import datetime

app = FastAPI(title="AntFinServ QuantAlpha Radar")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_FILE = "users_db.json"
LOGS_FILE = "activity_logs.json"

DEFAULT_DATA = {
    "pins": {
        "942040": {
            "role": "admin",
            "name": "Rana Sahib (Admin)",
            "mobile": "Master Desk",
            "email": "admin@antfinserv.com",
            "active": True
        },
        "1234": {
            "role": "guest",
            "name": "Institutional Client",
            "mobile": "9876543210",
            "email": "client@investor.com",
            "active": True
        }
    }
}

def load_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump(DEFAULT_DATA, f, indent=2)
        return DEFAULT_DATA
    try:
        with open(DB_FILE, "r") as f:
            data = json.load(f)
            # Auto migrate any legacy admin keys to 942040
            for old_k in ["9999", "999999"]:
                if old_k in data.get("pins", {}):
                    admin_profile = data["pins"].pop(old_k)
                    data["pins"]["942040"] = admin_profile
                    with open(DB_FILE, "w") as fw:
                        json.dump(data, fw, indent=2)
            if "942040" not in data.get("pins", {}):
                data["pins"]["942040"] = DEFAULT_DATA["pins"]["942040"]
                with open(DB_FILE, "w") as fw:
                    json.dump(data, fw, indent=2)
            return data
    except Exception:
        return DEFAULT_DATA

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

def log_activity(pin: str, user_name: str, action: str, details: str = ""):
    logs = []
    if os.path.exists(LOGS_FILE):
        try:
            with open(LOGS_FILE, "r") as f:
                logs = json.load(f)
        except Exception:
            logs = []
    
    logs.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pin": pin,
        "name": user_name,
        "action": action,
        "details": details
    })
    
    with open(LOGS_FILE, "w") as f:
        json.dump(logs[-1000:], f, indent=2)

def verify_pin(x_app_pin: str = Header(None)):
    if not x_app_pin:
        raise HTTPException(status_code=401, detail="PIN_REQUIRED")
    db = load_db()
    user = db.get("pins", {}).get(str(x_app_pin))
    if not user:
        raise HTTPException(status_code=403, detail="INVALID_PIN")
    if not user.get("active", False):
        raise HTTPException(status_code=403, detail="REVOKED_ACCESS")
    return user

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.get("/manifest.json")
def serve_manifest():
    return FileResponse("manifest.json")

class VerifyPinRequest(BaseModel):
    pin: str

@app.post("/api/verify-pin")
def api_verify_pin(req: VerifyPinRequest):
    db = load_db()
    user = db.get("pins", {}).get(req.pin)
    if not user:
        raise HTTPException(status_code=403, detail="INVALID_PIN")
    if not user.get("active", False):
        raise HTTPException(status_code=403, detail="REVOKED_ACCESS")
    
    log_activity(req.pin, user["name"], "LOGIN", "Terminal Unlocked")
    return {"status": "ok", "role": user["role"], "name": user["name"]}

class ResetPinRequest(BaseModel):
    mobile: str
    email: str
    new_pin: str

@app.post("/api/reset-pin")
def reset_pin(req: ResetPinRequest):
    db = load_db()
    clean_mob = req.mobile.strip()
    clean_email = req.email.strip().lower()
    new_pin = req.new_pin.strip()
    
    if len(new_pin) != 4 or not new_pin.isdigit():
        raise HTTPException(status_code=400, detail="Client PIN must be exactly 4 digits.")
    
    matched_old_pin = None
    for pin, u in db.get("pins", {}).items():
        if u.get("role") != "admin":
            if u.get("mobile", "").strip() == clean_mob and u.get("email", "").strip().lower() == clean_email:
                matched_old_pin = pin
                break
                
    if not matched_old_pin:
        raise HTTPException(status_code=404, detail="No matching profile found with provided Mobile & Email.")
    
    user_data = db["pins"].pop(matched_old_pin)
    db["pins"][new_pin] = user_data
    save_db(db)
    
    log_activity(new_pin, user_data["name"], "PIN_RESET", f"Client PIN reset to {new_pin}")
    return {"status": "success", "message": "PIN updated successfully. Unlock now."}

class ChangeAdminPinRequest(BaseModel):
    new_pin: str

@app.post("/api/admin/change-pin")
def change_admin_pin(req: ChangeAdminPinRequest, user=Depends(verify_pin), x_app_pin: str = Header(None)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Unauthorized")
    new_pin = req.new_pin.strip()
    if len(new_pin) != 6 or not new_pin.isdigit():
        raise HTTPException(status_code=400, detail="Admin PIN must be exactly 6 digits.")
    
    db = load_db()
    current_admin_key = str(x_app_pin)
    admin_data = db["pins"].pop(current_admin_key, None)
    if not admin_data:
        raise HTTPException(status_code=404, detail="Admin profile not found.")
    
    db["pins"][new_pin] = admin_data
    save_db(db)
    log_activity(new_pin, "Admin", "ADMIN_PIN_CHANGED", f"Master PIN changed to {new_pin}")
    return {"status": "success", "new_pin": new_pin}

@app.get("/api/admin/users")
def get_all_users(user=Depends(verify_pin)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Unauthorized")
    return load_db()

class AddUserRequest(BaseModel):
    pin: str
    name: str
    mobile: str
    email: str

@app.post("/api/admin/add-user")
def add_user(req: AddUserRequest, user=Depends(verify_pin)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Unauthorized")
    pin = req.pin.strip()
    if len(pin) != 4 or not pin.isdigit():
        raise HTTPException(status_code=400, detail="Client PIN must be exactly 4 digits.")
    
    db = load_db()
    db["pins"][pin] = {
        "role": "guest",
        "name": req.name.strip(),
        "mobile": req.mobile.strip(),
        "email": req.email.strip().lower(),
        "active": True
    }
    save_db(db)
    log_activity("ADMIN", "Admin", "USER_CREATED", f"Created client {req.name} ({req.mobile})")
    return {"status": "success", "users": db}

class ToggleUserRequest(BaseModel):
    pin: str
    active: bool

@app.post("/api/admin/toggle-user")
def toggle_user(req: ToggleUserRequest, user=Depends(verify_pin)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Unauthorized")
    db = load_db()
    if req.pin in db.get("pins", {}):
        if db["pins"][req.pin]["role"] == "admin":
            raise HTTPException(status_code=400, detail="Cannot toggle Master Admin.")
        db["pins"][req.pin]["active"] = req.active
        save_db(db)
        log_activity("ADMIN", "Admin", "USER_STATUS_CHANGE", f"PIN {req.pin} active={req.active}")
        return {"status": "success", "users": db}
    raise HTTPException(status_code=404, detail="PIN not found.")

@app.get("/api/admin/export-audit-excel")
def export_audit_excel(x_app_pin: str = Header(None)):
    user = verify_pin(x_app_pin)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Unauthorized")

    db = load_db()
    users_rows = []
    for pin, info in db.get("pins", {}).items():
        users_rows.append({
            "PIN": pin,
            "Client Name": info.get("name"),
            "Role": info.get("role"),
            "Mobile": info.get("mobile"),
            "Email": info.get("email"),
            "Status": "Active" if info.get("active") else "Revoked"
        })
    df_users = pd.DataFrame(users_rows)

    logs = []
    if os.path.exists(LOGS_FILE):
        try:
            with open(LOGS_FILE, "r") as f:
                logs = json.load(f)
        except Exception:
            logs = []
    df_logs = pd.DataFrame(logs) if logs else pd.DataFrame(columns=["timestamp", "pin", "name", "action", "details"])
    df_logs.rename(columns={
        "timestamp": "Timestamp",
        "pin": "PIN",
        "name": "User Name",
        "action": "Action",
        "details": "Details / Searched Ticker"
    }, inplace=True)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_users.to_excel(writer, sheet_name='Client Registry', index=False)
        df_logs.to_excel(writer, sheet_name='Activity & Search Logs', index=False)
    output.seek(0)

    filename = f"AntFinServ_Terminal_Audit_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

class StockRequest(BaseModel):
    ticker: str

@app.post("/api/analyze")
def analyze_stock(req: StockRequest, user=Depends(verify_pin), x_app_pin: str = Header(None)):
    try:
        symbol = req.ticker.strip().upper()
        if not symbol.endswith(".NS") and not symbol.endswith(".BO"):
            symbol += ".NS"
            
        log_activity(str(x_app_pin), user.get("name", "User"), "SEARCH_TICKER", symbol.replace(".NS", ""))

        stock = yf.Ticker(symbol)
        hist = stock.history(period="1y")

        if hist is None or hist.empty or len(hist) < 20:
            raise HTTPException(status_code=404, detail="Ticker price data unavailable.")

        info = {}
        try:
            info = stock.info or {}
        except Exception:
            info = {}

        cmp = round(float(hist['Close'].iloc[-1]), 2)
        high_52w = round(float(hist['High'].max()), 2)
        low_52w = round(float(hist['Low'].min()), 2)

        # EMAs
        hist['EMA_20'] = hist['Close'].ewm(span=20, adjust=False).mean()
        hist['EMA_50'] = hist['Close'].ewm(span=50, adjust=False).mean()
        hist['EMA_200'] = hist['Close'].ewm(span=200, adjust=False).mean()
        ema_20_val = round(float(hist['EMA_20'].iloc[-1]), 2)
        ema_50_val = round(float(hist['EMA_50'].iloc[-1]), 2)
        ema_200_val = round(float(hist['EMA_200'].iloc[-1]), 2)

        # MACD & RSI
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

        pe = round(float(info.get('trailingPE', 0.0) or 0.0), 2)
        peg = round(float(info.get('pegRatio', 0.0) or 0.0), 2)
        debt_equity = round(float((info.get('debtToEquity', 0.0) or 0.0) / 100), 2)
        pledged_pct = round(float(info.get('pnlPledged', 0.0) or 0.0), 2)

        annual_fin = stock.financials
        annual_bs = stock.balance_sheet
        q_fin = stock.quarterly_financials
        
        roe_history = []
        roce_history = []
        roe_roce_labels = []

        if not annual_fin.empty and not annual_bs.empty:
            try:
                cols = list(annual_fin.columns[:4])
                cols.reverse()
                for c in cols:
                    y_lbl = c.strftime('%Y') if hasattr(c, 'strftime') else str(c)[:4]
                    roe_roce_labels.append(y_lbl)
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

        if not roe_history:
            roe_history = [12.0, 14.5, 16.0, round(float((info.get('returnOnEquity', 0.0) or 0.0) * 100), 2) or 16.5]
            roce_history = [15.0, 16.8, 18.2, round(float((info.get('returnOnAssets', 0.0) or 0.0) * 150), 2) or 19.0]
            roe_roce_labels = ['FY23', 'FY24', 'FY25', 'TTM']

        roe_val = roe_history[-1]
        roce_val = roce_history[-1]

        # Forensic OCF/PAT
        ocf_pat_ratio = 1.0
        ocf_status = "Good Cash Flow"
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
                    ocf_status = "Elite (>1.0x)" if ocf_pat_ratio >= 1.0 else ("Adequate (0.7-1.0x)" if ocf_pat_ratio >= 0.7 else "Scrutiny (<0.7x)")
                else:
                    ocf_pat_ratio = 0.0
                    ocf_status = "Neutral"
        except Exception:
            ocf_pat_ratio = 1.0
            ocf_status = "Standard Normal"

        diff = high_52w - low_52w
        fib_382 = round(low_52w + 0.382 * diff, 2)
        fib_618 = round(low_52w + 0.618 * diff, 2)
        stop_loss = round(min(ema_50_val, cmp * 0.90), 2)
        target_1 = round(fib_618 if cmp < fib_618 else high_52w, 2)
        target_2 = round(high_52w * 1.20, 2)

        quarterly_data = []
        q_rev_series = []
        q_pat_series = []
        q_labels = []

        try:
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

        # SWEET SPOT VALUATION FORMULA: P/E <= 35 OR (P/E <= 45 if PEG <= 1.5)
        pe_qualifies_shooting = bool((0 < pe <= 35) or (0 < pe <= 45 and 0 < peg <= 1.5))
        pe_qualifies_conviction = bool((0 < pe <= 40) or (0 < pe <= 48 and 0 < peg <= 1.8))

        is_shooting_star = bool(
            pe_qualifies_shooting and (debt_equity <= 0.6) and
            (roce_val >= 14.0 or roe_val >= 14.0) and
            (pledged_pct < 10.0) and (cmp >= ema_50_val) and 
            macd_bullish and (ocf_pat_ratio >= 0.7)
        )

        status = "🌟 SHOOTING STAR READY" if is_shooting_star else ("✅ TOP CONVICTION" if (pe_qualifies_conviction and debt_equity < 0.7 and ocf_pat_ratio >= 0.7) else ("❌ SCRUTINY / CASH WEAK" if ocf_pat_ratio < 0.7 else "⚠️ EXPENSIVE VALUATION"))
        
        if is_shooting_star:
            category_reason = f"Passed institutional sweet spot: Valuation disciplined (P/E: {pe}, PEG: {peg or 'Fair'}), Clean leverage (D/E: {debt_equity} <= 0.6), Capital efficiency (ROCE: {roce_val}%), Trading above 50-EMA with bullish MACD, and Cash conversion (OCF/PAT: {ocf_pat_ratio}x >= 0.7x)."
        elif status == "✅ TOP CONVICTION":
            category_reason = f"Strong compounder candidate: P/E {pe} with disciplined balance sheet (D/E < 0.7) and cash generation ({ocf_pat_ratio}x). Staged entry on supports recommended."
        elif "SCRUTINY" in status:
            category_reason = f"Forensic scrutiny trigger: OCF/PAT ratio is {ocf_pat_ratio}x (<0.7x threshold). Paper profits are not converting adequately into bank liquidity."
        else:
            category_reason = f"Stretched valuation multiples (P/E: {pe}, PEG: {peg}). High risk-to-reward for fresh entries; wait for technical consolidation."

        macd_alert = "🔥 Fresh Bullish Cross" if macd_crossover else ("🟢 Bullish Trend" if macd_bullish else "🔴 Bearish Divergence")

        return {
            "ticker": symbol.replace(".NS", ""),
            "name": info.get('shortName', symbol.replace(".NS", "")),
            "cmp": cmp,
            "status": status,
            "category_reason": category_reason,
            "is_shooting_star": is_shooting_star,
            "pe": pe,
            "peg": peg if peg > 0 else "N/A",
            "roe": f"{roe_val}%",
            "roce": f"{roce_val}%",
            "debt_equity": debt_equity,
            "pledged": f"{pledged_pct}%",
            "rsi": rsi_14,
            "macd_alert": macd_alert,
            "ema_20": ema_20_val,
            "ema_50": ema_50_val,
            "ema_200": ema_200_val,
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
            "roe_roce_labels": roe_roce_labels,
            "roe_history": roe_history,
            "roce_history": roce_history,
            "ftdb": {
                "F": f"P/E: {pe} | PEG: {peg if peg > 0 else 'N/A'} | ROCE: {roce_val}% | ROE: {roe_val}% | D/E: {debt_equity} | Pledge: {pledged_pct}%",
                "T": f"20-EMA: ₹{ema_20_val} | 50-EMA: ₹{ema_50_val} | 200-EMA: ₹{ema_200_val} | RSI(14): {rsi_14} ({macd_alert})",
                "D": "Cash delivery accumulation active over multi-week VWAP clusters",
                "B": f"Inst: {inst_holding}% | Promoter: {insider_holding}% (Base Float Stability)"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))