import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- SETUP & STYLING ---
st.set_page_config(page_title="Equity Intelligence", layout="wide")

# Dunkles Design & Abstände
st.markdown("""
    <style>
    .stMetric { background-color: #1e2130; padding: 15px; border-radius: 10px; border: 1px solid #30363d; }
    .main { background-color: #0e1117; }
    </style>
    """, unsafe_allow_html=True)

# --- DATEN LOGIK ---
@st.cache_resource
def init_db():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def fetch_data(ticker):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="1y")
        if hist.empty: return None
        info = tk.info
        return tk, hist, info
    except: return None

# --- APP START ---
db = init_db()
st.title("💎 Equity Intelligence Dashboard")

try:
    # 1. Ticker & FX laden
    res = db.table("watchlist").select("ticker").execute()
    tickers = [t['ticker'].upper() for t in res.data]
    
    eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
    eur_usd = float(eur_usd_data['Close'].iloc[-1])

    if tickers:
        # 2. Haupt-Tabelle berechnen
        all_results = []
        with st.spinner('Analysiere Portfolio...'):
            for t in tickers:
                bundle = fetch_data(t)
                if not bundle: continue
                tk, hist, info = bundle
                
                # Fair Value Logik (KGV + DDM Schnitt)
                fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
                growth = info.get('earningsGrowth') or 0.1
                kgv = info.get('forwardPE') or 20
                fv_kgv = (fwd_eps * (1+growth)**2) * kgv
                
                price_eur = (info.get('currentPrice') or hist['Close'].iloc[-1]) / eur_usd
                fv_eur = fv_kgv / eur_usd
                upside = ((fv_eur - price_eur) / price_eur) * 100
                
                all_results.append({
                    "Ticker": t,
                    "Preis (€)": round(price_eur, 2),
                    "Fair Value (€)": round(fv_eur, 2),
                    "Upside (%)": round(upside, 1),
                    "RSI": round(ta.rsi(hist['Close'], length=14).iloc[-1], 1),
                    "Status": "🟢 KAUF" if upside > 15 else ("🟡 HALTEN" if upside > 0 else "🔴 TEUER")
                })

        df = pd.DataFrame(all_results).sort_values("Upside (%)", ascending=False)
        
        # 3. UI: Übersicht
        st.subheader("Markt-Übersicht")
        st.dataframe(df, use_container_width=True, hide_index=True)

        # 4. UI: Detail-Analyse
        st.divider()
        selected = st.selectbox("🔬 Wähle einen Ticker für Details", tickers)
        
        if selected:
            tk, hist, info = fetch_data(selected)
            col1, col2 = st.columns([1, 2])
            
            with col1:
                st.metric("Aktueller Kurs", f"{round(df.loc[df['Ticker']==selected, 'Preis (€)'].values[0], 2)} €")
                st.metric("Fair Value", f"{round(df.loc[df['Ticker']==selected, 'Fair Value (€)'].values[0], 2)} €")
                st.write(f"**Sektor:** {info.get('sector', 'N/A')}")
                st.write(f"**Dividende:** {info.get('dividendYield', 0)*100:.2f} %")

            with col2:
                # Plotly Chart mit Fair Value Linie
                fv_val = df.loc[df['Ticker']==selected, 'Fair Value (€)'].values[0]
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=hist.index, y=hist['Close']/eur_usd, name="Kurs (EUR)", line=dict(color='#58a6ff')))
                fig.add_hline(y=fv_val, line_dash="dash", line_color="green", annotation_text="Fair Value")
                fig.update_layout(title=f"{selected} Kursverlauf vs. Fair Value", template="plotly_dark", height=400)
                st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"Fehler im Dashboard: {e}")
