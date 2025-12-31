import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from supabase import create_client

# --- KONFIGURATION ---
# Ersetze diese Werte mit deinen echten Supabase-Daten (zu finden unter Project Settings -> API)
SUPABASE_URL = "DEINE_URL"
SUPABASE_KEY = "DEIN_KEY"
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

TICKERS = ["NVDA", "TSM", "V", "ASML", "GOOGL"]

def get_financial_data(ticker_symbol):
    stock = yf.Ticker(ticker_symbol)
    eur_usd = yf.Ticker("EURUSD=X").fast_info['last_price']
    
    # 1. Kurs & ATH Daten
    df_hist = stock.history(period="20y")
    current_price_usd = df_hist['Close'].iloc[-1]
    ath_usd = df_hist['Close'].max()
    
    # 2. Historische Durchschnittskorrektur (Max Drawdown over time)
    # Wir berechnen das rollierende Maximum und davon die Abweichung
    roll_max = df_hist['Close'].cummax()
    drawdowns = (df_hist['Close'] - roll_max) / roll_max
    avg_drawdown = drawdowns.mean() * 100 # Durchschnittliche "Tiefe" über 20J
    
    # 3. Fair Value Logik (EPS 2026 & KGV Median)
    info = stock.info
    fwd_eps = info.get('forwardEps', 1) 
    # Schätzung für 2026 (basiert auf 15% Wachstum p.a. falls Konsens nicht greifbar)
    eps_2026 = fwd_eps * 1.15 
    kgv_median = info.get('trailingPE', 25) # Nutzt Trailing PE als Basis für Median
    
    fv_usd = ( (eps_2026 * (kgv_median * 0.8)) + (eps_2026 * kgv_median) ) / 2
    
    # 4. Technische Indikatoren
    df_recent = df_hist.tail(60).copy()
    rsi = ta.rsi(df_recent['Close'], length=14).iloc[-1]
    adx_df = ta.adx(df_recent['High'], df_recent['Low'], df_recent['Close'], length=14)
    adx = adx_df['ADX_14'].iloc[-1]
    
    # Volumen Analyse
    vol_20ma = df_recent['Volume'].rolling(20).mean().iloc[-1]
    curr_vol = df_recent['Volume'].iloc[-1]
    vol_ratio = curr_vol / vol_20ma
    vol_status = "Buy" if vol_ratio > 1.5 else ("Sell" if vol_ratio < 0.8 else "Hold")

    # 5. Währungsumrechnung
    price_eur = current_price_usd / eur_usd
    fv_eur = fv_usd / eur_usd
    
    # 6. Bewertung
    upside = ((fv_eur - price_eur) / price_eur) * 100
    corr_now = ((current_price_usd - ath_usd) / ath_usd) * 100
    
    if upside > 10 and rsi < 50 and vol_status == "Buy" and corr_now > -15:
        rating = "🟢 KAUF"
    elif 0 <= upside <= 10 and 40 <= rsi <= 70:
        rating = "🟡 BEOBACHTEN"
    else:
        rating = "🔴 WARTEN"

    return {
        "ticker": ticker_symbol,
        "kurs_eur": round(price_eur, 2),
        "fv_eur": round(fv_eur, 2),
        "tranche1": round((ath_usd * 0.9) / eur_usd, 2),
        "tranche2": round((ath_usd * 0.8) / eur_usd, 2),
        "bewertung": rating,
        "rsi": round(rsi, 1),
        "corr_ath": round(corr_now, 1),
        "hist_corr": round(avg_drawdown, 1),
        "trend": "Stark" if adx > 25 else "Schwach",
        "volumen": vol_status
    }

# --- UI ---
st.title("Finanz-Dashboard & Supabase Sync")

if st.button("Marktdaten scannen"):
    results = []
    for t in TICKERS:
        with st.status(f"Analysiere {t}...", expanded=False):
            data = get_financial_data(t)
            results.append(data)
            # Sync mit deiner bestehenden Supabase Tabelle
            # WICHTIG: Spaltennamen in .upsert({}) müssen exakt wie in Supabase sein!
            supabase.table("DEIN_TABELLEN_NAME").upsert(data).execute()
    
    st.table(pd.DataFrame(results))
