import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- SETUP ---
st.set_page_config(page_title="Strategy Watchlist", layout="wide")

# Supabase Verbindung sicher laden
try:
    URL = st.secrets["SUPABASE_URL"]
    KEY = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(URL, KEY)
except Exception as e:
    st.error("⚠️ Supabase Secrets fehlen in der Streamlit Cloud!")
    st.stop()

def get_live_eur_usd():
    """Holt aktuellen EUR/USD Kurs."""
    try:
        data = yf.download("EURUSD=X", period="1d", interval="1m", progress=False)
        return data['Close'].iloc[-1]
    except:
        return 1.08 # Fallback falls API hakt

def calculate_metrics(ticker_symbol, eur_usd):
    try:
        tk = yf.Ticker(ticker_symbol)
        # Schnellerer Download der Kursdaten
        hist = tk.history(period="max")
        if hist.empty: return None
        
        info = tk.info
        df_ta = hist.tail(60).copy()
        
        # 1. EPS 2026 Logik (Deine Formel)
        # Wir nehmen das forwardEps und rechnen es 2 Jahre hoch
        current_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('longTermOutlookValue', 0.1) # Default 10%
        eps_2026 = current_eps * (1 + growth)**2
        
        # 2. KGV Spanne (10J Median Proxy)
        hist_pe = info.get('forwardPE') or info.get('trailingPE') or 15
        unteres_kgv = hist_pe * 0.8
        oberes_kgv = hist_pe
        
        # 3. Fair Value (USD & EUR)
        fv_usd = (eps_2026 * unteres_kgv + eps_2026 * oberes_kgv) / 2
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        
        fv_eur = fv_usd / eur_usd
        price_eur = price_usd / eur_usd
        
        # 4. ATH & Tranchen
        ath_usd = hist['High'].max()
        tranche1 = (ath_usd * 0.9) / eur_usd
        tranche2 = (ath_usd * 0.8) / eur_usd
        corr_ath = ((price_usd - ath_usd) / ath_usd) * 100
        
        # 5. Technische Metriken
        rsi = ta.rsi(df_ta['Close'], length=14).iloc[-1]
        adx_df = ta.adx(df_ta['High'], df_ta['Low'], df_ta['Close'])
        adx = adx_df['ADX_14'].iloc[-1]
        
        vol_now = df_ta['Volume'].iloc[-1]
        vol_ma = df_ta['Volume'].mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Hold"

        # Bewertung (Deine Logik)
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
            "Fair Value($)": round(float(fv_usd), 2),
            "Fair Value(€)": round(float(fv_eur), 2),
            "Tranche1(-10%)": round(float(tranche1), 2),
            "Tranche2(-20%)": round(float(tranche2), 2),
            "Bewertung": bewertung,
            "RSI(14)": round(float(rsi), 1),
            "Korrektur(%)": f"{round(float(corr_ath), 1)}%",
            "Trend": "Stark" if adx > 25 else "Schwach",
            "Volumen": vol_sig
        }
    except Exception as e:
        st.warning(f"Fehler bei {ticker_symbol}: {e}")
        return None

# --- UI ---
st.title("📊 My Strategy Watchlist")

if st.button("🔄 Daten aktualisieren"):
    st.cache_data.clear()
    st.rerun()

# Ticker aus Supabase laden
try:
    res = supabase.table("watchlist").select("ticker").execute()
    tickers = [item['ticker'] for item in res.data]
except Exception as e:
    st.error(f"Datenbankfehler: {e}")
    tickers = []

if tickers:
    eur_usd = get_live_eur_usd()
    all_data = []
    
    with st.spinner("Analysiere Märkte..."):
        for t in tickers:
            data = calculate_metrics(t, eur_usd)
            if data: all_data.append(data)

    if all_data:
        df = pd.DataFrame(all_data)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Grafik Sektion
        st.subheader("Visualer Check: Kurs vs. Fair Value (€)")
        cols = st.columns(3)
        for idx, row in df.iterrows():
            with cols[idx % 3]:
                fig = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=row['Kurs(€)'],
                    title={'text': row['Ticker']},
                    gauge={
                        'axis': {'range': [0, row['Fair Value(€)'] * 1.5]},
                        'threshold': {'line': {'color': "green", 'width': 4}, 'value': row['Fair Value(€)']}
                    }
                ))
                fig.update_layout(height=200, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Keine Ticker in der Supabase 'watchlist' Tabelle gefunden.")
