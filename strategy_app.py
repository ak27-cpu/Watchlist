import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- BASIS KONFIGURATION ---
st.set_page_config(page_title="Stock Analysis Pro", layout="wide")

# Supabase Initialisierung
@st.cache_resource
def init_supabase():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Verbindungsfehler: {e}")
        return None

supabase = init_supabase()

# --- FUNKTIONEN ---

def get_eur_usd():
    """Holt aktuellen EUR/USD Kurs."""
    try:
        return yf.download("EURUSD=X", period="1d", interval="1m", progress=False)['Close'].iloc[-1]
    except:
        return 1.05

def get_data(ticker, eur_usd):
    try:
        tk = yf.Ticker(ticker)
        # Schnellerer Abruf
        hist = tk.history(period="max")
        if hist.empty: return None
        
        info = tk.info
        hist_60d = hist.tail(60).copy()
        
        # Fair Value Kalkulation (Prompt-Logik)
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('earningsGrowth', 0.1) 
        eps_2026 = fwd_eps * (1 + growth)**2
        
        kgv_median = info.get('forwardPE') or info.get('trailingPE') or 20
        unteres_kgv = kgv_median * 0.8
        
        fv_usd = (eps_2026 * unteres_kgv + eps_2026 * kgv_median) / 2
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        
        # Umrechnungen
        price_eur = price_usd / eur_usd
        fv_eur = fv_usd / eur_usd
        
        # ATH & Tranchen
        ath_usd = hist['High'].max()
        ath_eur = ath_usd / eur_usd
        corr_ath = ((price_usd - ath_usd) / ath_usd) * 100
        
        # Indikatoren
        rsi = ta.rsi(hist_60d['Close'], length=14).iloc[-1]
        adx = ta.adx(hist_60d['High'], hist_60d['Low'], hist_60d['Close'])['ADX_14'].iloc[-1]
        
        vol_now = hist_60d['Volume'].iloc[-1]
        vol_ma = hist_60d['Volume'].mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Hold"
        
        # Bewertung
        upside = ((fv_usd - price_usd) / price_usd) * 100
        if upside > 10 and rsi < 40 and vol_sig == "Buy":
            bewertung = "🟢 KAUF"
        elif 0 <= upside <= 10:
            bewertung = "🟡 BEOBACHTEN"
        else:
            bewertung = "🔴 WARTEN"

        return {
            "Ticker": ticker,
            "Kurs(€)": round(float(price_eur), 2),
            "Fair Value(USD)": round(float(fv_usd), 2),
            "Fair Value(€)": round(float(fv_eur), 2),
            "Tranche1(-10%)": round(float(ath_eur * 0.9), 2),
            "Tranche2(-20%)": round(float(ath_eur * 0.8), 2),
            "Bewertung": bewertung,
            "RSI": round(float(rsi), 1),
            "Korrektur": f"{round(float(corr_ath), 1)}%",
            "Trend": "Stark" if adx > 25 else "Mittel" if adx > 20 else "Schwach",
            "Volumen": vol_sig
        }
    except:
        return None

# --- UI ---
st.title("📈 Strategy App: Watchlist")

if supabase:
    # Daten abrufen
    try:
        # Explizite Abfrage
        response = supabase.from_("watchlist").select("ticker").execute()
        ticker_list = [t['ticker'].strip() for t in response.data]
        
        if not ticker_list:
            st.warning("⚠️ Tabelle 'watchlist' ist leer. Bitte Ticker hinzufügen.")
            st.stop()
            
        eur_usd = get_eur_usd()
        st.write(f"Wechselkurs: **1 EUR = {round(eur_usd, 4)} USD**")
        
        results = []
        with st.spinner('Berechne Kennzahlen...'):
            for t in ticker_list:
                res = get_data(t, eur_usd)
                if res: results.append(res)
        
        if results:
            df = pd.DataFrame(results)
            
            # 1. Tabelle
            st.subheader("Analyse Übersicht")
            st.dataframe(df, use_container_width=True, hide_index=True)
            
            # 2. Charts
            st.divider()
            st.subheader("Kurs vs. Fair Value (€)")
            cols = st.columns(3)
            for i, row in df.iterrows():
                with cols[i % 3]:
                    fig = go.Figure(go.Indicator(
                        mode = "gauge+number",
                        value = row['Kurs(€)'],
                        title = {'text': f"{row['Ticker']}"},
                        gauge = {
                            'axis': {'range': [0, max(row['Fair Value(€)'], row['Kurs(€)']) * 1.4]},
                            'steps': [
                                {'range': [0, row['Fair Value(€)']], 'color': "#E8F5E9"},
                                {'range': [row['Fair Value(€)'], 9999], 'color': "#FFEBEE"}
                            ],
                            'threshold': {'line': {'color': "green", 'width': 4}, 'value': row['Fair Value(€)']}
                        }
                    ))
                    fig.update_layout(height=220, margin=dict(l=10,r=10,t=40,b=10))
                    st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error(f"Fehler beim Laden der Tabelle 'watchlist': {e}")
        st.info("💡 Prüfe, ob die Tabelle wirklich 'watchlist' (kleingeschrieben) heißt.")

# Sidebar Admin
with st.sidebar:
    st.header("Steuerung")
    if st.button("🔄 Aktualisieren"):
        st.rerun()
    
    new_ticker = st.text_input("Neuer Ticker (z.B. TSLA)")
    if st.button("Hinzufügen"):
        if new_ticker:
            supabase.table("watchlist").insert({"ticker": new_ticker.upper()}).execute()
            st.success(f"{new_ticker} hinzugefügt!")
            st.rerun()
