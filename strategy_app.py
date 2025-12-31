import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- 1. SETUP & STYLE ---
st.set_page_config(page_title="Equity Intelligence Pro", layout="wide")

st.markdown("""
    <style>
    .stMetric { background-color: #1e2130; padding: 15px; border-radius: 10px; border: 1px solid #30363d; }
    .main { background-color: #0e1117; }
    </style>
    """, unsafe_allow_html=True)

# --- 2. DATEN FUNKTIONEN ---
@st.cache_resource
def init_db():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def fetch_data(ticker):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="1y")
        if hist.empty: return None
        return tk, hist, tk.info
    except: return None

# --- 3. HAUPTLOGIK ---
db = init_db()
st.title("💎 Equity Intelligence Dashboard")

try:
    # Ticker & FX
    res = db.table("watchlist").select("ticker").execute()
    tickers = [t['ticker'].upper() for t in res.data]
    
    eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
    eur_usd = float(eur_usd_data['Close'].iloc[-1])

    if tickers:
        all_results = []
        with st.spinner('Analysiere Portfolio...'):
            for t in tickers:
                bundle = fetch_data(t)
                if not bundle: continue
                tk, hist, info = bundle
                
                # Fair Value Logik
                fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
                growth = info.get('earningsGrowth') or 0.1
                kgv = info.get('forwardPE') or 20
                fv_eur = ((fwd_eps * (1+growth)**2) * kgv) / eur_usd
                
                price_eur = (info.get('currentPrice') or hist['Close'].iloc[-1]) / eur_usd
                upside = ((fv_eur - price_eur) / price_eur) * 100
                
                all_results.append({
                    "Ticker": t,
                    "Preis (€)": round(price_eur, 2),
                    "Fair Value (€)": round(fv_eur, 2),
                    "Upside (%)": round(upside, 1),
                    "RSI": round(ta.rsi(hist['Close'], length=14).iloc[-1], 1),
                    "Signal": "🟢 KAUF" if upside > 15 else ("🟡 HALTEN" if upside > 0 else "🔴 TEUER")
                })

        df = pd.DataFrame(all_results).sort_values("Upside (%)", ascending=False)
        st.subheader("📊 Markt-Übersicht")
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.divider()

        # --- DETAIL ANSICHT (Hier war der Fehler) ---
        selected = st.selectbox("🔬 Wähle einen Ticker für die dynamische Analyse", ["Bitte wählen..."] + tickers)
        
        if selected != "Bitte wählen...":
            tk, hist, info = fetch_data(selected)
            fv_val = df.loc[df['Ticker'] == selected, 'Fair Value (€)'].values[0]
            
            # Kennzahlen-Reihe
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Preis (€)", f"{round(hist['Close'].iloc[-1]/eur_usd, 2)}")
            c2.metric("Fair Value (€)", f"{round(fv_val, 2)}", delta=f"{df.loc[df['Ticker']==selected, 'Upside (%)'].values[0]}%")
            c3.metric("RSI (14)", f"{round(ta.rsi(hist['Close'], length=14).iloc[-1], 1)}")
            c4.metric("KGV (Fwd)", f"{info.get('forwardPE', 'N/A')}")

            # --- DYNAMISCHER CHART ---
            from plotly.subplots import make_subplots
            
            # Subplots: Oben Kurs + FV, Unten RSI
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                               vertical_spacing=0.1, row_heights=[0.7, 0.3])

            # 1. Kurs & FV Zone
            hist_eur = hist['Close'] / eur_usd
            fig.add_trace(go.Scatter(x=hist.index, y=hist_eur, name="Kurs (EUR)", line=dict(color='#58a6ff')), row=1, col=1)
            
            # FV Band
            fig.add_trace(go.Scatter(x=[hist.index[0], hist.index[-1]], y=[fv_val*1.05, fv_val*1.05], mode='lines', line=dict(width=0), showlegend=False), row=1, col=1)
            fig.add_trace(go.Scatter(x=[hist.index[0], hist.index[-1]], y=[fv_val*0.95, fv_val*0.95], mode='lines', line=dict(width=0), fill='tonexty', fillcolor='rgba(40, 167, 69, 0.2)', name="Fair Value Zone"), row=1, col=1)
            fig.add_hline(y=fv_val, line_dash="dash", line_color="#28a745", row=1, col=1)

            # 2. RSI Indikator
            rsi_series = ta.rsi(hist['Close'], length=14)
            fig.add_trace(go.Scatter(x=hist.index, y=rsi_series, name="RSI", line=dict(color='#ff7f0e')), row=2, col=1)
            fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)

            fig.update_layout(height=600, template="plotly_dark", hovermode="x unified", showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

    else:
        st.info("Deine Watchlist ist noch leer.")

except Exception as e:
    st.error(f"Fehler: {e}")
    
