import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- KONFIGURATION & DB ---
st.set_page_config(page_title="Strategy Watchlist", layout="wide")

# Supabase Verbindung
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL, KEY)

# --- HELFER-FUNKTIONEN ---

def get_live_eur_usd():
    """Holt den aktuellen Wechselkurs für die Umrechnung."""
    return yf.Ticker("EURUSD=X").history(period="1d")['Close'].iloc[-1]

def get_stock_data(ticker_symbol, eur_usd):
    try:
        tk = yf.Ticker(ticker_symbol)
        info = tk.info
        hist_max = tk.history(period="max")
        hist_1y = tk.history(period="1y")
        
        # 1. EPS 2026 (Analystenkonsens / Extrapolation)
        # yfinance liefert forwardEps (12 Monate). Wir nutzen dies als Basis.
        fwd_eps = info.get('forwardEps', 0)
        eps_2026 = fwd_eps * 1.15 # Konservative Annahme: +15% bis 2026 falls kein Konsens
        
        # 2. Historisches KGV (10J Median) & Spanne
        # Da yfinance keine 10J Historie direkt aggregiert, nutzen wir das 5J Avg / Forward PE
        base_pe = info.get('forwardPE', 20)
        unteres_kgv = base_pe * 0.8  # -20% Sicherheitsabschlag
        oberes_kgv = base_pe
        
        # 3. Fair Value Berechnung (USD)
        fv_konservativ = eps_2026 * unteres_kgv
        fv_optimistisch = eps_2026 * oberes_kgv
        gemittelter_fv_usd = (fv_konservativ + fv_optimistisch) / 2
        
        # 4. Umrechnungen & Kurse
        price_usd = info.get('currentPrice') or hist_1y['Close'].iloc[-1]
        price_eur = price_usd / eur_usd
        fv_eur = gemittelter_fv_usd / eur_usd
        
        # Metriken (ATH, RSI, Volumen)
        ath = hist_max['High'].max()
        ath_eur = ath / eur_usd
        tranche1 = ath_eur * 0.9
        tranche2 = ath_eur * 0.8
        corr_ath = ((price_usd - ath) / ath) * 100
        
        # Technische Indikatoren
        df_ta = tk.history(period="60d")
        rsi = ta.rsi(df_ta['Close'], length=14).iloc[-1]
        adx = ta.adx(df_ta['High'], df_ta['Low'], df_ta['Close'])['ADX_14'].iloc[-1]
        
        vol_now = df_ta['Volume'].iloc[-1]
        vol_ma = df_ta['Volume'].tail(20).mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Neutral"
        
        # Bewertung (Logik aus Prompt)
        upside = ((gemittelter_fv_usd - price_usd) / price_usd) * 100
        if upside > 10 and rsi < 40 and vol_sig == "Buy":
            bewertung = "🟢 KAUF"
        elif 0 <= upside <= 10 and 20 <= adx <= 25:
            bewertung = "🟡 BEOBACHTEN"
        else:
            bewertung = "🔴 WARTEN"
            
        return {
            "Ticker": ticker_symbol,
            "Kurs(€)": round(price_eur, 2),
            "Fair Value(USD)": round(gemittelter_fv_usd, 2),
            "Fair Value(€)": round(fv_eur, 2),
            "Tranche1(-10%ATH)": round(tranche1, 2),
            "Tranche2(-20%ATH)": round(tranche2, 2),
            "Bewertung": bewertung,
            "RSI(14)": round(rsi, 1),
            "Korrektur vs ATH(%)": f"{round(corr_ath, 1)}%",
            "Trendstärke": "Stark" if adx > 25 else "Mittel" if adx > 20 else "Schwach",
            "Volumen": vol_sig
        }
    except Exception:
        return None

# --- MAIN APP ---
st.title("🚀 Strategy Stock Watchlist")

# Wechselkurs & Daten laden
eur_usd = get_live_eur_usd()
response = supabase.table("watchlist").select("ticker").execute()
ticker_list = [item['ticker'] for item in response.data]

if ticker_list:
    results = []
    for t in ticker_list:
        data = get_stock_data(t, eur_usd)
        if data: results.append(data)
    
    df = pd.DataFrame(results)

    # Tabelle anzeigen
    st.subheader("Marktanalyse & Fair Value")
    st.table(df)

    # Grafische Darstellung: Fair Value vs Aktueller Kurs
    st.divider()
    st.subheader("Visualisierung: Preisabstand zum Fair Value (€)")
    
    for _, row in df.iterrows():
        fig = go.Figure(go.Indicator(
            mode = "gauge+number",
            value = row['Kurs(€)'],
            title = {'text': f"{row['Ticker']} (Fair Value: {row['Fair Value(€)']}€)"},
            gauge = {
                'axis': {'range': [0, max(row['Fair Value(€)'], row['Kurs(€)']) * 1.2]},
                'steps': [
                    {'range': [0, row['Fair Value(€)']], 'color': "#e8f5e9"},
                    {'range': [row['Fair Value(€)'], row['Fair Value(€)']*2], 'color': "#ffebee"}
                ],
                'threshold': {
                    'line': {'color': "green", 'width': 4},
                    'thickness': 0.75,
                    'value': row['Fair Value(€)']
                }
            }
        ))
        fig.update_layout(height=250)
        st.plotly_chart(fig, use_container_width=True)

else:
    st.info("Füge Ticker über die Datenbank hinzu, um die Analyse zu starten.")
