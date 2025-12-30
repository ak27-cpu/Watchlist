import streamlit as st
from supabase import create_client
import yfinance as yf
import pandas as pd
from urllib.parse import quote

# --- 1. SETUP & SIDEBAR ---
st.set_page_config(page_title="FairValue Watchlist v1", layout="wide")

with st.sidebar:
    st.header("⚙️ Strategie-Einstellungen")
    t1_pct = st.slider("Tranche 1 Abstand (%)", 1, 30, 10)
    t2_pct = st.slider("Tranche 2 Abstand (%)", 1, 30, 15)
    st.info(f"T1: -{t1_pct}% vom FV\nT2: -{t2_pct}% vom FV")
    
    if st.button("🔄 Daten neu laden"):
        st.cache_data.clear()
        st.rerun()

try:
    supabase = create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
except Exception as e:
    st.error(f"Supabase Fehler: {e}")
    st.stop()

# --- 2. FAIR VALUE LOGIK (KGV & CASHFLOW) ---
def calculate_smart_fv(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # Basis-Daten
        cp = info.get('currentPrice', 0)
        eps = info.get('forwardEps', 0)
        growth = info.get('earningsGrowth', 0.05) # Fallback 5%
        fcf = info.get('freeCashflow', 0)
        shares = info.get('sharesOutstanding', 1)
        
        # Methode A: Graham Formel (KGV-basiert)
        # FV = EPS * (8.5 + 2 * g) -> g in %
        fv_graham = eps * (8.5 + 2 * (growth * 100)) if eps > 0 else 0
        
        # Methode B: Cashflow-Basis
        # FV = (FCF / Shares) * Multiplikator (z.B. 20)
        fv_fcf = (fcf / shares) * 20 if fcf and shares else 0
        
        # Kombinierter Fair Value (Gewichtung Graham 60%, FCF 40%)
        if fv_graham > 0 and fv_fcf > 0:
            final_fv = (fv_graham * 0.6) + (fv_fcf * 0.4)
        else:
            final_fv = max(fv_graham, fv_fcf) # Falls eines fehlt
            
        return round(final_fv, 2) if final_fv > 0 else round(cp * 0.9, 2)
    except:
        return 0

@st.cache_data(ttl=3600)
def get_stock_data(ticker):
    try:
        s = yf.Ticker(ticker)
        info = s.info
        h = s.history(period="1mo")
        
        # RSI Berechnung
        delta = h['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        return {
            "Preis": info.get('currentPrice'),
            "RSI": round(rsi, 1),
            "FV": calculate_smart_fv(ticker)
        }
    except: return None

# --- 3. MARKT HEADER ---
try:
    vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
    spy = yf.Ticker("^GSPC").history(period="300d")
    sma125 = spy['Close'].rolling(125).mean().iloc[-1]
    fg = min(100, int((spy['Close'].iloc[-1] / sma125) * 50))
except: vix, fg = 20, 50

st.title("🎯 Strategie-Zentrale: Fair Value & Tranchen")
c1, c2 = st.columns(2)
c1.metric("VIX Index", f"{vix:.2f}")
c2.metric("Fear & Greed", f"{fg}/100")
st.divider()

# --- 4. WATCHLIST & TABELLE ---
res = supabase.table("watchlist").select("ticker").execute()
tickers = [r['ticker'] for r in res.data]

if tickers:
    data_list = []
    with st.spinner("Berechne faire Werte..."):
        for t in tickers:
            d = get_stock_data(t)
            if d:
                # Tranchen vom berechneten FV
                t1 = d['FV'] * (1 - t1_pct/100)
                t2 = d['FV'] * (1 - t2_pct/100)
                
                # Empfehlung
                diff = ((d['Preis'] / d['FV']) - 1) * 100
                if diff < -15: rec = "UNTERBEWERTET 🟢"
                elif diff > 15: rec = "ÜBERTEUERT 🔴"
                else: rec = "FAIR 🟡"
                
                data_list.append({
                    "Ticker": t, "Preis": d['Preis'], "RSI": d['RSI'],
                    "Faire Value": d['FV'], f"Tranche 1 (-{t1_pct}%)": round(t1, 2),
                    f"Tranche 2 (-{t2_pct}%)": round(t2, 2), "Empfehlung": rec
                })

    df = pd.DataFrame(data_list)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # --- 5. PERPLEXITY BUTTON ---
    st.divider()
    sel = st.selectbox("Deep-Dive Analyse:", df['Ticker'].tolist())
    row = df[df['Ticker'] == sel].iloc[0]
    
    prompt = f"Analysiere {sel}. Kurs {row['Preis']}, RSI {row['RSI']}. Mein berechneter Fair Value liegt bei {row['Faire Value']}. News 10 Tage & Prognose?"
    url = f"https://www.perplexity.ai/?q={quote(prompt)}"
    st.link_button(f"🚀 {sel} auf Perplexity prüfen", url, use_container_width=True)
