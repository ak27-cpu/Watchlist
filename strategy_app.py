import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from supabase import create_client, Client

# --- SETUP & CONFIG ---
st.set_page_config(page_title="Aktien Watchlist PRO", layout="wide")


TICKERS = ["NVDA", "TSM", "V", "ASML", "GOOGL"]

def get_exchange_rate():
    return yf.Ticker("EURUSD=X").fast_info['last_price']

def calculate_metrics(ticker_symbol, eur_usd):
    stock = yf.Ticker(ticker_symbol)
    hist = stock.history(period="max")
    hist_20d = stock.history(period="20d")
    info = stock.info
    
    # 1. Kursdaten
    price_usd = info.get('currentPrice') or info.get('regularMarketPrice')
    price_eur = price_usd / eur_usd
    
    # 2. Fair Value Berechnung (EPS 2026 & KGV Median)
    # Hinweis: yfinance liefert EPS Estimates oft unter 'forwardEps'
    eps_2026 = info.get('forwardEps', 0) * 1.15 # Grobe Annäherung, da Konsens 2026 oft fehlt
    kgv_median = info.get('forwardPE', 20) # Fallback auf Forward PE
    
    fv_usd_low = eps_2026 * (kgv_median * 0.8)
    fv_usd_high = eps_2026 * kgv_median
    fv_usd_avg = (fv_usd_low + fv_usd_high) / 2
    fv_eur = fv_usd_avg / eur_usd
    
    # 3. ATH & Tranchen
    ath = hist['Close'].max()
    tranche1 = (ath * 0.9) / eur_usd
    tranche2 = (ath * 0.8) / eur_usd
    corr_pct = ((price_usd - ath) / ath) * 100
    
    # 4. Technische Indikatoren
    df_tech = stock.history(period="60d")
    rsi = ta.rsi(df_tech['Close'], length=14).iloc[-1]
    adx_df = ta.adx(df_tech['High'], df_tech['Low'], df_tech['Close'], length=14)
    adx = adx_df['ADX_14'].iloc[-1]
    
    avg_vol = df_tech['Volume'].tail(20).mean()
    curr_vol = df_tech['Volume'].iloc[-1]
    
    # 5. Logik
    upside = ((fv_eur - price_eur) / price_eur) * 100
    
    # Bewertungstext
    vol_status = "Buy" if curr_vol > 1.5 * avg_vol else ("Sell" if curr_vol < 0.8 * avg_vol else "Neutral")
    trend = "Stark" if adx > 25 else ("Mittel" if adx > 20 else "Schwach")
    
    if upside > 10 and rsi < 50 and vol_status == "Buy" and corr_pct > -15:
        rating = "🟢 KAUF"
    elif 0 <= upside <= 10 and 40 <= rsi <= 70 and trend == "Mittel":
        rating = "🟡 BEOBACHTEN"
    else:
        rating = "🔴 WARTEN"

    return {
        "Ticker": ticker_symbol,
        "Kurs(€)": round(price_eur, 2),
        "Fair Value(€)": round(fv_eur, 2),
        "Tranche1(-10%)": round(tranche1, 2),
        "Tranche2(-20%)": round(tranche2, 2),
        "Bewertung": rating,
        "RSI(14)": round(rsi, 1),
        "Korrektur vs ATH": f"{round(corr_pct, 1)}%",
        "Trend": trend,
        "Volumen": vol_status
    }

# --- APP UI ---
st.title("🚀 Smart Watchlist & Fair Value Analyzer")

if st.button("Daten aktualisieren"):
    eur_usd = get_exchange_rate()
    results = []
    
    with st.spinner('Berechne Daten...'):
        for t in TICKERS:
            data = calculate_metrics(t, eur_usd)
            results.append(data)
            
            # Sync to Supabase
            supabase.table("watchlist").upsert({
                "ticker": t,
                "last_price_eur": data["Kurs(€)"],
                "fair_value_eur": data["Fair Value(€)"],
                "status": data["Bewertung"]
            }).execute()
            
    df = pd.DataFrame(results)
    st.table(df)
else:
    st.info("Klicke auf den Button, um die Analyse zu starten.")
