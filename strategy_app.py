import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from supabase import create_client, Client
import datetime

# --- SETUP & KONFIGURATION ---
st.set_page_config(page_title="Aktien Watchlist PRO", layout="wide")

# Supabase Verbindung (aus st.secrets)
url: str = st.secrets["SUPABASE_URL"]
key: str = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

# --- FUNKTIONEN ---

def get_exchange_rate():
    """Holt den aktuellen EUR/USD Kurs."""
    ticker = yf.Ticker("EURUSD=X")
    return ticker.history(period="1d")['Close'].iloc[-1]

def calculate_metrics(ticker_symbol, eur_usd):
    try:
        tk = yf.Ticker(ticker_symbol)
        hist_1y = tk.history(period="1y")
        hist_max = tk.history(period="max")
        info = tk.info
        
        # 1. Kursdaten
        current_price_usd = info.get('currentPrice') or hist_1y['Close'].iloc[-1]
        price_eur = current_price_usd / eur_usd
        
        # 2. Fair Value (EPS 2026 & KGV Median)
        # Hinweis: yfinance liefert EPS Schätzungen oft unzuverlässig, wir nutzen forwardEPS als Proxy
        eps_2026 = info.get('forwardEps', 0) 
        hist_pe = info.get('forwardPE', 20) # Fallback auf 20
        # Vereinfachte Fair Value Spanne nach deiner Formel
        fv_usd = (eps_2026 * (hist_pe * 0.8) + eps_2026 * hist_pe) / 2
        fv_eur = fv_usd / eur_usd
        
        # 3. Korrekturen & ATH
        ath = hist_max['High'].max()
        current_corr = ((current_price_usd - ath) / ath) * 100
        tranche1 = (ath * 0.9) / eur_usd
        tranche2 = (ath * 0.8) / eur_usd
        
        # Hist. Durchschnitt Korrektur (Max Drawdown Proxy)
        roll_max = hist_max['Close'].cummax()
        drawdown = (hist_max['Close'] - roll_max) / roll_max
        hist_avg_corr = drawdown.mean() * 100
        
        # 4. Technische Indikatoren (TA-Lib / Pandas_ta)
        df_ta = tk.history(period="60d")
        rsi = ta.rsi(df_ta['Close'], length=14).iloc[-1]
        
        # ADX (Trendstärke)
        adx_df = ta.adx(df_ta['High'], df_ta['Low'], df_ta['Close'], length=14)
        adx = adx_df['ADX_14'].iloc[-1]
        trend = "Stark" if adx > 25 else "Mittel" if adx > 20 else "Schwach"
        
        # Volumen
        vol_now = df_ta['Volume'].iloc[-1]
        vol_ma = df_ta['Volume'].tail(20).mean()
        vol_ratio = vol_now / vol_ma
        vol_signal = "Buy" if vol_ratio > 1.5 else "Sell" if vol_ratio < 0.8 else "Neutral"
        
        # 5. Bewertung Logik
        upside = ((fv_eur - price_eur) / price_eur) * 100
        if upside > 10 and rsi < 40 and vol_signal == "Buy" and current_corr > -15:
            bewertung = "🟢 KAUF"
        elif 0 <= upside <= 10 and 40 <= rsi <= 70 and trend == "Mittel":
            bewertung = "🟡 BEOBACHTEN"
        else:
            bewertung = "🔴 WARTEN"
            
        return {
            "Ticker": ticker_symbol,
            "Kurs(€)": round(price_eur, 2),
            "Fair Value(€)": round(fv_eur, 2),
            "Tranche1": round(tranche1, 2),
            "Tranche2": round(tranche2, 2),
            "Bewertung": bewertung,
            "RSI(14)": round(rsi, 1),
            "Korrektur %": f"{round(current_corr, 1)}%",
            "Hist. Ø Korr": f"{round(hist_avg_corr, 1)}%",
            "Trend": trend,
            "Volumen": vol_signal
        }
    except Exception as e:
        return {"Ticker": ticker_symbol, "Bewertung": f"Fehler: {str(e)}"}

# --- UI ---

st.title("📈 Smart Stock Watchlist")

# Ticker hinzufügen
with st.sidebar:
    st.header("Verwaltung")
    new_ticker = st.text_input("Ticker hinzufügen (z.B. AAPL)").upper()
    if st.button("Hinzufügen"):
        if new_ticker:
            supabase.table("watchlist").insert({"ticker": new_ticker}).execute()
            st.success(f"{new_ticker} gespeichert!")

# Daten aus Supabase laden
response = supabase.table("watchlist").select("ticker").execute()
ticker_list = [item['ticker'] for item in response.data]

if ticker_list:
    with st.spinner('Berechne Kennzahlen...'):
        eur_usd = get_exchange_rate()
        results = [calculate_metrics(t, eur_usd) for t in ticker_list]
        df = pd.DataFrame(results)
        
        # Styling
        def color_bewertung(val):
            if 'KAUF' in val: color = '#d4edda'
            elif 'BEOBACHTEN' in val: color = '#fff3cd'
            else: color = '#f8d7da'
            return f'background-color: {color}'

        st.table(df.style.applymap(color_bewertung, subset=['Bewertung']))
else:
    st.info("Noch keine Ticker in der Datenbank. Nutze die Sidebar!")
