import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- BERECHNUNGS-LOGIK (SCHRITT 1-4) ---

def get_exchange_rate():
    """Holt den aktuellen EUR/USD Kurs."""
    try:
        ticker = yf.Ticker("EURUSD=X")
        return ticker.history(period="1d")['Close'].iloc[-1]
    except:
        return 1.05  # Sicherer Fallback

def calculate_fair_value(ticker_symbol, eur_usd):
    try:
        tk = yf.Ticker(ticker_symbol)
        info = tk.info
        hist_max = tk.history(period="max")
        hist_60d = tk.history(period="60d")
        
        if hist_max.empty:
            return None

        # 1. EPS 2026 (Analysten-Konsens oder Extrapolation)
        # Wir nutzen forwardEps und das erwartete Wachstum (growth)
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('earningsGrowth', 0.12) # Fallback 12% p.a.
        eps_2026 = fwd_eps * (1 + growth)**2
        
        # 2. Historisches KGV (10J Median Proxy)
        # Wir nutzen das aktuelle forwardPE als Basis für die Spanne
        base_pe = info.get('forwardPE') or info.get('trailingPE') or 18
        unteres_kgv = base_pe * 0.8  # 20% Sicherheitsabschlag
        oberes_kgv = base_pe
        
        # 3. Fair Value Berechnung (Szenarien)
        fv_usd_konservativ = eps_2026 * unteres_kgv
        fv_usd_optimistisch = eps_2026 * oberes_kgv
        gemittelter_fv_usd = (fv_usd_konservativ + fv_usd_optimistisch) / 2
        
        # 4. Umrechnungen & Kurse
        price_usd = info.get('currentPrice') or hist_max['Close'].iloc[-1]
        price_eur = price_usd / eur_usd
        fv_eur = gemittelter_fv_usd / eur_usd
        
        # Weitere Metriken (ATH, RSI, Volumen)
        ath_usd = hist_max['High'].max()
        ath_eur = ath_usd / eur_usd
        corr_ath = ((price_usd - ath_usd) / ath_usd) * 100
        
        rsi = ta.rsi(hist_60d['Close'], length=14).iloc[-1]
        adx_df = ta.adx(hist_60d['High'], hist_60d['Low'], hist_60d['Close'])
        adx = adx_df['ADX_14'].iloc[-1]
        
        vol_now = hist_60d['Volume'].iloc[-1]
        vol_ma = hist_60d['Volume'].tail(20).mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Hold"
        
        # Bewertung (Deine Logik)
        upside = ((gemittelter_fv_usd - price_usd) / price_usd) * 100
        if upside > 10 and rsi < 40 and vol_sig == "Buy":
            bewertung = "🟢 KAUF"
        elif 0 <= upside <= 10:
            bewertung = "🟡 BEOBACHTEN"
        else:
            bewertung = "🔴 WARTEN"

        return {
            "Ticker": ticker_symbol,
            "Kurs(€)": round(float(price_eur), 2),
            "Fair Value(USD)": round(float(gemittelter_fv_usd), 2),
            "Fair Value(€)": round(float(fv_eur), 2),
            "Tranche1(-10%)": round(float(ath_eur * 0.9), 2),
            "Tranche2(-20%)": round(float(ath_eur * 0.8), 2),
            "Bewertung": bewertung,
            "RSI(14)": round(float(rsi), 1),
            "Korrektur vs ATH(%)": f"{round(float(corr_ath), 1)}%",
            "Trend": "Stark" if adx > 25 else "Mittel" if adx > 20 else "Schwach",
            "Volumen": vol_sig
        }
    except Exception as e:
        st.error(f"Fehler bei Ticker {ticker_symbol}: {e}")
        return None

# --- STREAMLIT UI ---

st.set_page_config(page_title="Stock Analysis Strategy", layout="wide")
st.title("📈 Watchlist & Fair Value Analyse")

# 1. Verbindung zu Supabase
try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(url, key)
except Exception as e:
    st.error("⚠️ Secrets (URL/KEY) nicht gefunden oder fehlerhaft!")
    st.stop()

# 2. Daten laden
eur_usd = get_exchange_rate()

# Versuche Ticker aus der Tabelle "watchlist" zu laden
try:
    # .table("watchlist") muss exakt so heißen wie in Supabase (meist kleingeschrieben)
    response = supabase.table("watchlist").select("ticker").execute()
    tickers = [item['ticker'] for item in response.data]
except Exception as e:
    st.error(f"Konnte Tabelle 'watchlist' nicht finden. Fehler: {e}")
    tickers = []

if tickers:
    st.info(f"Analysiere {len(tickers)} Aktien aus der Datenbank...")
    
    results = []
    # Ladebalken für die Analyse
    progress_bar = st.progress(0)
    for i, t in enumerate(tickers):
        data = calculate_fair_value(t, eur_usd)
        if data:
            results.append(data)
        progress_bar.progress((i + 1) / len(tickers))
    
    if results:
        df = pd.DataFrame(results)
        
        # Tabelle anzeigen
        st.subheader("Übersicht")
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Visualisierung
        st.divider()
        st.subheader("Grafische Auswertung: Kurs im Verhältnis zum Fair Value (€)")
        
        # Grid Layout für Charts
        cols = st.columns(3)
        for i, row in df.iterrows():
            with cols[i % 3]:
                fig = go.Figure(go.Indicator(
                    mode = "gauge+number",
                    value = row['Kurs(€)'],
                    title = {'text': f"{row['Ticker']}"},
                    gauge = {
                        'axis': {'range': [0, max(row['Fair Value(€)'], row['Kurs(€)']) * 1.3]},
                        'bar': {'color': "black"},
                        'steps': [
                            {'range': [0, row['Fair Value(€)']], 'color': "#c8e6c9"},
                            {'range': [row['Fair Value(€)'], 9999], 'color': "#ffcdd2"}
                        ],
                        'threshold': {
                            'line': {'color': "green", 'width': 4},
                            'thickness': 0.75,
                            'value': row['Fair Value(€)']
                        }
                    }
                ))
                fig.update_layout(height=250, margin=dict(l=20, r=20, t=50, b=20))
                st.plotly_chart(fig, use_container_width=True)
else:
    st.warning("Keine Ticker gefunden. Prüfe, ob die Tabelle 'watchlist' Daten enthält.")

# Sidebar zum schnellen Hinzufügen (optionaler Test)
with st.sidebar:
    st.header("Admin")
    if st.button("Cache leeren & Neu laden"):
        st.cache_data.clear()
        st.rerun()
