import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- SETUP ---
st.set_page_config(page_title="Stock Strategy Watchlist", layout="wide")

# Supabase Verbindung
try:
    URL = st.secrets["SUPABASE_URL"]
    KEY = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(URL, KEY)
except Exception as e:
    st.error("⚠️ Secrets fehlerhaft. Prüfe SUPABASE_URL und SUPABASE_KEY.")
    st.stop()

# --- FUNKTIONEN ---

def get_exchange_rate():
    """Holt aktuellen EUR/USD Kurs."""
    try:
        data = yf.download("EURUSD=X", period="1d", interval="1m", progress=False)
        return data['Close'].iloc[-1]
    except:
        return 1.05

def get_analysis(ticker_symbol, eur_usd):
    try:
        tk = yf.Ticker(ticker_symbol)
        info = tk.info
        # Historie für ATH und Indikatoren
        hist_full = tk.history(period="max")
        hist_60d = hist_full.tail(60).copy()
        
        if hist_full.empty: return None

        # 1. EPS 2026 Kalkulation
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('earningsGrowth', 0.10) # Fallback 10%
        eps_2026 = fwd_eps * (1 + growth)**2
        
        # 2. KGV Spanne (10J Median Proxy)
        kgv_median = info.get('forwardPE') or info.get('trailingPE') or 20
        unteres_kgv = kgv_median * 0.8
        
        # 3. Fair Value (USD)
        fv_usd = (eps_2026 * unteres_kgv + eps_2026 * kgv_median) / 2
        
        # 4. Umrechnungen
        price_usd = info.get('currentPrice') or hist_full['Close'].iloc[-1]
        price_eur = price_usd / eur_usd
        fv_eur = fv_usd / eur_usd
        
        # ATH & Tranchen
        ath_usd = hist_full['High'].max()
        ath_eur = ath_usd / eur_usd
        corr_ath = ((price_usd - ath_usd) / ath_usd) * 100
        
        # Technische Metriken
        rsi = ta.rsi(hist_60d['Close'], length=14).iloc[-1]
        adx = ta.adx(hist_60d['High'], hist_60d['Low'], hist_60d['Close'])['ADX_14'].iloc[-1]
        
        vol_now = hist_60d['Volume'].iloc[-1]
        vol_ma = hist_60d['Volume'].mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Hold"
        
        # Bewertung Logik
        upside = ((fv_usd - price_usd) / price_usd) * 100
        if upside > 10 and rsi < 40 and vol_sig == "Buy":
            bewertung = "🟢 KAUF"
        elif 0 <= upside <= 10:
            bewertung = "🟡 BEOBACHTEN"
        else:
            bewertung = "🔴 WARTEN"

        return {
            "Ticker": ticker_symbol,
            "Kurs(€)": round(float(price_eur), 2),
            "Fair Value(USD)": round(float(fv_usd), 2),
            "Fair Value(€)": round(float(fv_eur), 2),
            "Tranche1(-10%ATH)": round(float(ath_eur * 0.9), 2),
            "Tranche2(-20%ATH)": round(float(ath_eur * 0.8), 2),
            "Bewertung": bewertung,
            "RSI(14)": round(float(rsi), 1),
            "Korrektur vs ATH(%)": f"{round(float(corr_ath), 1)}%",
            "Trendstärke": "Stark" if adx > 25 else "Mittel" if adx > 20 else "Schwach",
            "Volumen": vol_sig
        }
    except:
        return None

# --- UI ---
st.title("📈 Watchlist Analyse Tool")

# Verbindungstest & Datenabruf
try:
    # Abfrage der Tabelle 'watchlist'
    query = supabase.table("watchlist").select("ticker").execute()
    tickers = [item['ticker'] for item in query.data]
except Exception as e:
    st.error(f"Fehler beim Zugriff auf Tabelle 'watchlist': {e}")
    st.info("💡 Tipp: Prüfe in Supabase, ob RLS (Row Level Security) deaktiviert ist oder eine 'SELECT' Policy für anonyme Nutzer existiert.")
    tickers = []

if tickers:
    eur_usd = get_exchange_rate()
    st.write(f"Währungsbasis: 1 EUR = {round(eur_usd, 4)} USD")
    
    results = []
    with st.spinner('Berechne Kennzahlen...'):
        for t in tickers:
            res = get_analysis(t, eur_usd)
            if res: results.append(res)
    
    if results:
        df = pd.DataFrame(results)
        
        # Tabellenanzeige
        st.subheader("Analyse-Ergebnisse")
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Charts
        st.divider()
        st.subheader("Fair Value Visualisierung (€)")
        cols = st.columns(3)
        for i, row in df.iterrows():
            with cols[i % 3]:
                fig = go.Figure(go.Indicator(
                    mode = "gauge+number",
                    value = row['Kurs(€)'],
                    title = {'text': f"{row['Ticker']}"},
                    gauge = {
                        'axis': {'range': [0, max(row['Fair Value(€)'], row['Kurs(€)']) * 1.5]},
                        'bar': {'color': "darkblue"},
                        'steps': [
                            {'range': [0, row['Fair Value(€)']], 'color': "#d4edda"},
                            {'range': [row['Fair Value(€)'], 9999], 'color': "#f8d7da"}
                        ],
                        'threshold': {
                            'line': {'color': "green", 'width': 4},
                            'value': row['Fair Value(€)']
                        }
                    }
                ))
                fig.update_layout(height=250)
                st.plotly_chart(fig, use_container_width=True)
else:
    if not tickers:
        st.warning("Tabelle 'watchlist' gefunden, aber sie enthält keine Einträge.")

# Sidebar Controls
with st.sidebar:
    st.header("Einstellungen")
    if st.button("Daten neu laden"):
        st.rerun()
