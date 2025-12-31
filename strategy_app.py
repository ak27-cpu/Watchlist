import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client
import requests

# --- SUPABASE CONNECT ---
def init_db():
    if "SUPABASE_URL" not in st.secrets or "SUPABASE_KEY" not in st.secrets:
        st.error("❌ Secrets fehlen in den Cloud-Settings!")
        st.stop()
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = init_db()

# --- ROBUSTER DATEN-DOWNLOAD (Fix für 401 Unauthorized) ---
def get_stock_data_robust(ticker):
    try:
        # Erstellt eine Session, um Yahoo-Sperren zu umgehen
        session = requests.Session()
        session.headers.update({'User-agent': 'Mozilla/5.0'})
        
        tk = yf.Ticker(ticker, session=session)
        
        # Nutze fast_info für Basisdaten (stabiler als info)
        fast_info = tk.fast_info
        hist = tk.history(period="max")
        
        if hist.empty: return None
        
        # Info-Daten (hier kann der 401 oft auftreten, daher mit try-except)
        try:
            full_info = tk.info
            fwd_eps = full_info.get('forwardEps') or full_info.get('trailingEps') or 1.0
            growth = full_info.get('earningsGrowth') or 0.1
            kgv_median = full_info.get('forwardPE') or 20
        except:
            # Fallback falls full_info blockiert wird
            fwd_eps = 1.0
            growth = 0.1
            kgv_median = 20

        return tk, hist, fast_info, fwd_eps, growth, kgv_median
    except Exception as e:
        return None

# --- ANALYSIS ENGINE ---
def get_stock_metrics(ticker, eur_usd):
    data_bundle = get_stock_data_robust(ticker)
    if not data_bundle: return None
    
    tk, hist, fast_info, fwd_eps, growth, kgv_median = data_bundle
    
    # EPS 2026 Kalkulation
    eps_2026 = fwd_eps * (1 + growth)**2
    
    # Fair Value USD
    fv_usd = (eps_2026 * (kgv_median * 0.8) + eps_2026 * kgv_median) / 2
    
    # Aktuelle Kurse
    price_usd = fast_info['last_price']
    price_eur = price_usd / eur_usd
    fv_eur = fv_usd / eur_usd
    
    # Metriken
    ath_eur = hist['High'].max() / eur_usd
    rsi = ta.rsi(hist['Close'].tail(60), length=14).iloc[-1]
    
    vol_now = hist['Volume'].iloc[-1]
    vol_ma = hist['Volume'].tail(20).mean()
    vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Hold"
    
    upside = ((fv_eur - price_eur) / price_eur) * 100
    
    if upside > 10 and rsi < 40 and vol_sig == "Buy":
        bewertung, rank = "🟢 KAUF", 1
    elif 0 <= upside <= 10:
        bewertung, rank = "🟡 BEOBACHTEN", 2
    else:
        bewertung, rank = "🔴 WARTEN", 3

    return {
        "Ticker": ticker,
        "Bewertung": bewertung,
        "Kurs(€)": round(price_eur, 2),
        "Fair Value(€)": round(fv_eur, 2),
        "Upside(%)": round(upside, 1),
        "Tranche1(-10%ATH)": round(ath_eur * 0.9, 2),
        "Tranche2(-20%ATH)": round(ath_eur * 0.8, 2),
        "RSI(14)": round(rsi, 1),
        "Korr. vs ATH": f"{round(((price_eur-ath_eur)/ath_eur)*100, 1)}%",
        "Volumen": vol_sig,
        "_rank": rank
    }

# --- UI ---
st.title("📈 Pro Watchlist (Fixed Version)")

try:
    res = supabase.table("watchlist").select("ticker").execute()
    ticker_list = [t['ticker'].upper() for t in res.data]
    
    if ticker_list:
        # EUR/USD Kurs
        eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
        eur_usd = eur_usd_data['Close'].iloc[-1]
        
        all_results = []
        with st.spinner('Daten werden von Yahoo abgerufen...'):
            for t in ticker_list:
                data = get_stock_metrics(t, eur_usd)
                if data: all_results.append(data)
        
        if all_results:
            df = pd.DataFrame(all_results).sort_values(by=["_rank", "Upside(%)"], ascending=[True, False])
            st.dataframe(
                df.drop(columns=["_rank"]).style.applymap(
                    lambda x: 'background-color: #d4edda' if "🟢" in str(x) else 
                              ('background-color: #f8d7da' if "🔴" in str(x) else ''),
                    subset=['Bewertung']
                ), 
                use_container_width=True, hide_index=True
            )
except Exception as e:
    st.error(f"Fehler: {e}")

with st.sidebar:
    if st.button("🔄 Refresh"):
        st.rerun()
