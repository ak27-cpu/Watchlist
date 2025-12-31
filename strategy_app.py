import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- SETUP ---
st.set_page_config(page_title="Strategy Watchlist", layout="wide")

# Fehlerprüfung für Secrets
try:
    URL = st.secrets["SUPABASE_URL"]
    KEY = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(URL, KEY)
except Exception as e:
    st.error(f"Fehler bei den Secrets oder Supabase-Verbindung: {e}")
    st.stop()

def get_live_eur_usd():
    return yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1]

def get_stock_data(ticker_symbol, eur_usd):
    try:
        tk = yf.Ticker(ticker_symbol)
        info = tk.info
        
        # Validierung ob Ticker existiert
        if 'regularMarketPrice' not in info and 'currentPrice' not in info:
            st.warning(f"Ticker {ticker_symbol} nicht gefunden.")
            return None

        hist_max = tk.history(period="max")
        df_ta = tk.history(period="60d")
        
        # 1. EPS 2026 & KGV Logik
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 0
        growth = info.get('earningsGrowth', 0.1) # 10% Default
        eps_2026 = fwd_eps * (1 + growth)**2
        
        base_pe = info.get('forwardPE') or info.get('trailingPE') or 20
        unteres_kgv = base_pe * 0.8
        
        # 2. Fair Value
        fv_usd = (eps_2026 * unteres_kgv + eps_2026 * base_pe) / 2
        price_usd = info.get('currentPrice') or info.get('regularMarketPrice')
        
        # 3. Metriken berechnen
        ath = hist_max['High'].max()
        price_eur = price_usd / eur_usd
        fv_eur = fv_usd / eur_usd
        
        # Indikatoren
        rsi = ta.rsi(df_ta['Close'], length=14).iloc[-1]
        adx_df = ta.adx(df_ta['High'], df_ta['Low'], df_ta['Close'])
        adx = adx_df['ADX_14'].iloc[-1]
        
        vol_now = df_ta['Volume'].iloc[-1]
        vol_ma = df_ta['Volume'].tail(20).mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Neutral"
        
        upside = ((fv_usd - price_usd) / price_usd) * 100
        
        # Bewertung
        if upside > 10 and rsi < 45: bewertung = "🟢 KAUF"
        elif upside > 0: bewertung = "🟡 BEOBACHTEN"
        else: bewertung = "🔴 WARTEN"

        return {
            "Ticker": ticker_symbol,
            "Kurs(€)": round(price_eur, 2),
            "Fair Value(USD)": round(fv_usd, 2),
            "Fair Value(€)": round(fv_eur, 2),
            "Tranche1(-10%ATH)": round((ath*0.9)/eur_usd, 2),
            "Tranche2(-20%ATH)": round((ath*0.8)/eur_usd, 2),
            "Bewertung": bewertung,
            "RSI(14)": round(rsi, 1),
            "Korrektur vs ATH(%)": f"{round(((price_usd-ath)/ath)*100, 1)}%",
            "Trendstärke": "Stark" if adx > 25 else "Mittel",
            "Volumen": vol_sig
        }
    except Exception as e:
        st.error(f"Fehler bei {ticker_symbol}: {e}")
        return None

# --- UI ---
st.title("🚀 Strategy Watchlist")

if st.button("Daten jetzt aktualisieren"):
    st.rerun()

try:
    eur_usd = get_live_eur_usd()
    res = supabase.table("watchlist").select("ticker").execute()
    tickers = [item['ticker'] for item in res.data]

    if tickers:
        all_data = []
        for t in tickers:
            data = get_stock_data(t, eur_usd)
            if data: all_data.append(data)
        
        if all_data:
            df = pd.DataFrame(all_data)
            st.table(df)
            
            # Grafiken
            st.subheader("Fair Value Visualisierung")
            for d in all_data:
                fig = go.Figure(go.Indicator(
                    mode = "gauge+number",
                    value = d['Kurs(€)'],
                    domain = {'x': [0, 1], 'y': [0, 1]},
                    title = {'text': d['Ticker']},
                    gauge = {'axis': {'range': [0, d['Fair Value(€)']*1.5]},
                             'threshold': {'line': {'color': "green", 'width': 4}, 'value': d['Fair Value(€)']}}
                ))
                fig.update_layout(height=200)
                st.plotly_chart(fig)
    else:
        st.info("Keine Ticker in Supabase gefunden.")
except Exception as e:
    st.error(f"Hauptfehler: {e}")
