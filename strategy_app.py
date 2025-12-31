import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- SUPABASE CONNECT ---
def connect_db():
    if "SUPABASE_URL" not in st.secrets or "SUPABASE_KEY" not in st.secrets:
        return None
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

db = connect_db()

if db is None:
    st.error("❌ Secrets fehlen! Bitte unter 'Settings -> Secrets' in Streamlit Cloud eintragen.")
    st.stop()

# --- ANALYSE FUNKTION ---
def get_analysis(ticker, eur_usd):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="max")
        if hist.empty: return None
        
        info = tk.info
        # EPS 2026 Logik
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('earningsGrowth', 0.1) 
        eps_2026 = fwd_eps * (1 + growth)**2
        
        # Fair Value USD
        kgv_median = info.get('forwardPE') or 20
        fv_usd = (eps_2026 * (kgv_median * 0.8) + eps_2026 * kgv_median) / 2
        
        # Umrechnung & Kurs
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        price_eur = price_usd / eur_usd
        fv_eur = fv_usd / eur_usd
        
        # Metriken
        ath = hist['High'].max() / eur_usd
        rsi = ta.rsi(hist['Close'].tail(60), length=14).iloc[-1]
        vol_now = hist['Volume'].iloc[-1]
        vol_ma = hist['Volume'].tail(20).mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Hold"
        
        upside = ((fv_eur - price_eur) / price_eur) * 100
        
        # Bewertung & Ranking
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
            "Fair Value($)": round(fv_usd, 2),
            "Fair Value(€)": round(fv_eur, 2),
            "Upside(%)": round(upside, 1),
            "Tranche1(-10%)": round(ath * 0.9, 2),
            "Tranche2(-20%)": round(ath * 0.8, 2),
            "RSI(14)": round(rsi, 1),
            "Korr. vs ATH": f"{round(((price_eur-ath)/ath)*100, 1)}%",
            "Volumen": vol_sig,
            "_rank": rank
        }
    except: return None

# --- UI ---
st.title("📈 Watchlist Strategy App")

try:
    # Lade Ticker aus 'watchlist'
    res = db.from_("watchlist").select("ticker").execute()
    ticker_list = [t['ticker'].upper() for t in res.data]
    
    if ticker_list:
        eur_usd = yf.download("EURUSD=X", period="1d", interval="1m", progress=False)['Close'].iloc[-1]
        
        results = []
        for t in ticker_list:
            data = get_analysis(t, eur_usd)
            if data: results.append(data)
        
        if results:
            df = pd.DataFrame(results).sort_values(by=["_rank", "Upside(%)"], ascending=[True, False])
            
            # Styling der Tabelle
            st.dataframe(
                df.drop(columns=["_rank"]).style.applymap(
                    lambda x: 'background-color: #d4edda' if "🟢" in str(x) else 
                              ('background-color: #f8d7da' if "🔴" in str(x) else ''),
                    subset=['Bewertung']
                ), 
                use_container_width=True, hide_index=True
            )
    else:
        st.info("Datenbank ist leer. Füge Ticker in Supabase hinzu.")
except Exception as e:
    st.error(f"Fehler beim Datenbankzugriff: {e}")

# Sidebar für manuelle Updates
with st.sidebar:
    if st.button("🔄 Aktualisieren"):
        st.rerun()
