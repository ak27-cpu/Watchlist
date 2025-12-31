import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from supabase import create_client, Client

# --- SETUP ---
st.set_page_config(page_title="Aktien Watchlist PRO", layout="wide")

url: str = st.secrets["SUPABASE_URL"]
key: str = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

def get_exchange_rate():
    return yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1]

def calculate_fair_value_metrics(ticker_symbol, eur_usd):
    try:
        tk = yf.Ticker(ticker_symbol)
        info = tk.info
        hist = tk.history(period="max")
        
        # 1. EPS 2026 Schätzung (Basis: Forward EPS + Wachstumsrate)
        fwd_eps = info.get('forwardEps', 0)
        growth_rate = info.get('earningsQuarterlyGrowth', 0.10) # Fallback 10%
        # Hochrechnung auf 2026 (vereinfacht 2 Jahre Wachstum)
        eps_2026 = fwd_eps * (1 + growth_rate)**2
        
        # 2. KGV Spanne (10J Median)
        # YFinance bietet keinen direkten 10J Median, wir nutzen das aktuelle forwardPE als Basis
        hist_pe_median = info.get('forwardPE', 20) 
        unteres_kgv = hist_pe_median * 0.8  # -20% Sicherheitsabschlag
        oberes_kgv = hist_pe_median
        
        # 3. Fair Value Szenarien
        fv_konservativ = eps_2026 * unteres_kgv
        fv_optimistisch = eps_2026 * oberes_kgv
        gemittelter_fv_usd = (fv_konservativ + fv_optimistisch) / 2
        
        # Währungsumrechnung
        current_price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        price_eur = current_price_usd / eur_usd
        fv_eur = gemittelter_fv_usd / eur_usd
        
        # 4. Zusätzliche Metriken (ATH, RSI, etc.)
        ath = hist['High'].max()
        tranche1 = (ath * 0.9) / eur_usd
        tranche2 = (ath * 0.8) / eur_usd
        current_corr = ((current_price_usd - ath) / ath) * 100
        
        # Technische Indikatoren
        df_ta = tk.history(period="60d")
        rsi = ta.rsi(df_ta['Close'], length=14).iloc[-1]
        adx_df = ta.adx(df_ta['High'], df_ta['Low'], df_ta['Close'], length=14)
        adx = adx_df['ADX_14'].iloc[-1]
        
        vol_now = df_ta['Volume'].iloc[-1]
        vol_ma = df_ta['Volume'].tail(20).mean()
        vol_signal = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Neutral"
        
        # Bewertung
        upside = ((gemittelter_fv_usd - current_price_usd) / current_price_usd) * 100
        if upside > 10 and rsi < 40 and vol_signal == "Buy":
            bewertung = "🟢 KAUF"
        elif 0 <= upside <= 10:
            bewertung = "🟡 BEOBACHTEN"
        else:
            bewertung = "🔴 WARTEN"

        return {
            "Ticker": ticker_symbol,
            "Kurs (€)": round(price_eur, 2),
            "Fair Value ($)": round(gemittelter_fv_usd, 2),
            "Fair Value (€)": round(fv_eur, 2),
            "Tranche1 (-10%)": round(tranche1, 2),
            "Tranche2 (-20%)": round(tranche2, 2),
            "Bewertung": bewertung,
            "RSI": round(rsi, 1),
            "Korr. vs ATH": f"{round(current_corr, 1)}%",
            "Trend": "Stark" if adx > 25 else "Mittel",
            "Volumen": vol_signal
        }
    except:
        return None

# --- UI ---
st.title("Watchlist & Fair Value Analyse")

# Ticker laden & berechnen
response = supabase.table("watchlist").select("ticker").execute()
ticker_list = [item['ticker'] for item in response.data]

if ticker_list:
    eur_usd = get_exchange_rate()
    data = []
    for t in ticker_list:
        res = calculate_fair_value_metrics(t, eur_usd)
        if res: data.append(res)
    
    df = pd.DataFrame(data)
    
    # Anzeige der Tabelle
    st.dataframe(df.style.highlight_max(subset=['Fair Value ($)'], color='#2E7D32'), use_container_width=True)
