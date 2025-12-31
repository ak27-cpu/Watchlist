import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- BASIS KONFIGURATION ---
st.set_page_config(page_title="Stock Strategy Analysis", layout="wide")

# Supabase Initialisierung
@st.cache_resource
def init_supabase():
    try:
        url = st.secrets["https://zwpolcwutolohujqzrjt.supabase.co"]
        key = st.secrets["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp3cG9sY3d1dG9sb2h1anF6cmp0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjY3OTkzOTAsImV4cCI6MjA4MjM3NTM5MH0.Ca0hYmOZCyWslbjyWvmUew9mL5I8_FcRZiim-afdaWE"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Verbindungsfehler zu Supabase: {e}")
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
        hist = tk.history(period="max")
        if hist.empty: return None
        
        info = tk.info
        hist_60d = hist.tail(60).copy()
        
        # 1. EPS 2026 Kalkulation
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('earningsGrowth', 0.1) 
        eps_2026 = fwd_eps * (1 + growth)**2
        
        # 2. KGV Spanne (10J Median Proxy)
        kgv_median = info.get('forwardPE') or info.get('trailingPE') or 20
        unteres_kgv = kgv_median * 0.8
        
        # 3. Fair Value (USD)
        fv_usd = (eps_2026 * unteres_kgv + eps_2026 * kgv_median) / 2
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        
        # 4. Umrechnungen & Tranchen
        price_eur = price_usd / eur_usd
        fv_eur = fv_usd / eur_usd
        ath_usd = hist['High'].max()
        ath_eur = ath_usd / eur_usd
        corr_ath = ((price_usd - ath_usd) / ath_usd) * 100
        
        # 5. Indikatoren (RSI, ADX, Volumen)
        rsi = ta.rsi(hist_60d['Close'], length=14).iloc[-1]
        adx = ta.adx(hist_60d['High'], hist_60d['Low'], hist_60d['Close'])['ADX_14'].iloc[-1]
        
        vol_now = hist_60d['Volume'].iloc[-1]
        vol_ma = hist_60d['Volume'].mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Hold"
        
        # 6. Bewertung mit Sortier-Index
        upside = ((fv_usd - price_usd) / price_usd) * 100
        if upside > 10 and rsi < 40 and vol_sig == "Buy":
            bewertung, sort_idx = "🟢 KAUF", 1
        elif 0 <= upside <= 10:
            bewertung, sort_idx = "🟡 BEOBACHTEN", 2
        else:
            bewertung, sort_idx = "🔴 WARTEN", 3

        return {
            "Ticker": ticker,
            "Bewertung": bewertung,
            "Upside(%)": round(float(upside), 1),
            "Kurs(€)": round(float(price_eur), 2),
            "Fair Value(€)": round(float(fv_eur), 2),
            "Fair Value(USD)": round(float(fv_usd), 2),
            "Tranche1(-10%)": round(float(ath_eur * 0.9), 2),
            "Tranche2(-20%)": round(float(ath_eur * 0.8), 2),
            "RSI": round(float(rsi), 1),
            "vs ATH": f"{round(float(corr_ath), 1)}%",
            "Trend": "Stark" if adx > 25 else "Mittel" if adx > 20 else "Schwach",
            "Volumen": vol_sig,
            "_sort": sort_idx
        }
    except:
        return None

# --- UI ---
st.title("🚀 Smart Analysis: Watchlist Strategie")

if supabase:
    try:
        # Ticker aus der Tabelle 'watchlist' laden
        response = supabase.from_("watchlist").select("ticker").execute()
        ticker_list = [t['ticker'].strip() for t in response.data]
        
        if not ticker_list:
            st.warning("⚠️ Tabelle 'watchlist' ist leer. Füge Ticker in der Sidebar hinzu!")
            st.stop()
            
        eur_usd = get_eur_usd()
        st.write(f"Wechselkurs: **1 EUR = {round(eur_usd, 4)} USD**")
        
        results = []
        with st.spinner('Marktdaten werden geladen...'):
            for t in ticker_list:
                res = get_data(t, eur_usd)
                if res: results.append(res)
        
        if results:
            # In DataFrame umwandeln und sortieren
            df = pd.DataFrame(results)
            df = df.sort_values(by=["_sort", "Upside(%)"], ascending=[True, False])
            
            # Anzeige-Spalten (ohne Sortier-Hilfe)
            display_df = df.drop(columns=["_sort"])
            
            # 1. Tabelle mit Styling
            st.subheader("Analyse & Ranking")
            
            def color_rating(val):
                if "🟢" in str(val): return 'background-color: #d4edda'
                if "🟡" in str(val): return 'background-color: #fff3cd'
                if "🔴" in str(val): return 'background-color: #f8d7da'
                return ''

            st.dataframe(
                display_df.style.applymap(color_rating, subset=['Bewertung']),
                use_container_width=True, 
                hide_index=True
            )
            
            # 2. Charts
            st.divider()
            st.subheader("Grafischer Fair Value Check")
            cols = st.columns(3)
            for i, (idx, row) in enumerate(df.iterrows()):
                with cols[i % 3]:
                    fig = go.Figure(go.Indicator(
                        mode = "gauge+number",
                        value = row['Kurs(€)'],
                        title = {'text': f"{row['Ticker']} ({row['Bewertung']})"},
                        gauge = {
                            'axis': {'range': [0, max(row['Fair Value(€)'], row['Kurs(€)']) * 1.5]},
                            'steps': [
                                {'range': [0, row['Fair Value(€)']], 'color': "#E8F5E9"},
                                {'range': [row['Fair Value(€)'], 9999], 'color': "#FFEBEE"}
                            ],
                            'threshold': {'line': {'color': "green", 'width': 4}, 'value': row['Fair Value(€)']}
                        }
                    ))
                    fig.update_layout(height=230, margin=dict(l=15,r=15,t=45,b=15))
                    st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error(f"Fehler: {e}")

# Sidebar Administration
with st.sidebar:
    st.header("Watchlist Verwaltung")
    new_ticker = st.text_input("Ticker-Kürzel (z.B. MSFT)").upper()
    if st.button("Hinzufügen"):
        if new_ticker:
            supabase.table("watchlist").insert({"ticker": new_ticker}).execute()
            st.success(f"{new_ticker} gespeichert!")
            st.rerun()
    
    st.divider()
    if st.button("🔄 Ansicht aktualisieren"):
        st.rerun()
