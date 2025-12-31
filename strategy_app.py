import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- PAGE CONFIG ---
st.set_page_config(page_title="Equity Intelligence Dashboard", layout="wide", initial_sidebar_state="collapsed")

# Custom CSS für einen moderneren Look
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { background-color: #1e2130; padding: 15px; border-radius: 10px; border: 1px solid #30363d; }
    [data-testid="stHeader"] { background: rgba(0,0,0,0); }
    </style>
    """, unsafe_allow_html=True)

# --- DB & DATA LOGIC ---
@st.cache_resource
def init_db():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def get_stock_metrics(ticker, eur_usd):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="1y") # 1 Jahr für Mini-Charts
        if hist.empty: return None
        
        info = tk.info
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('earningsGrowth') or 0.10
        kgv_median = info.get('forwardPE') or 20
        
        # KGV Methode
        eps_2026 = fwd_eps * (1 + growth)**2
        fv_kgv = eps_2026 * kgv_median
        
        # DDM Methode
        div_rate = info.get('dividendRate') or 0
        g = min(growth, 0.07)
        k = 0.09
        fv_ddm = (div_rate * (1 + g)) / (k - g) if div_rate > 0 and k > g else 0

        # Kombinierter Fair Value
        fv_final_usd = (fv_kgv + fv_ddm) / 2 if fv_ddm > 0 else fv_kgv
        
        price_eur = price_usd / eur_usd
        fv_eur = fv_final_usd / eur_usd
        upside = ((fv_final_usd - price_usd) / price_usd) * 100
        rsi = ta.rsi(hist['Close'], length=14).iloc[-1]

        # Ranking
        rank = 1 if upside > 15 and rsi < 45 else (2 if upside > 0 else 3)
        bewertung = "🟢 STRONG BUY" if rank == 1 else ("🟡 WATCH" if rank == 2 else "🔴 OVERVALUED")

        return {
            "Ticker": ticker,
            "Signal": bewertung,
            "Price (€)": round(price_eur, 2),
            "Fair Value (€)": round(fv_eur, 2),
            "Upside (%)": round(upside, 1),
            "RSI": round(rsi, 1),
            "Trend (1Y)": hist['Close'].tolist(), # Für Mini-Chart
            "_rank": rank
        }
    except: return None

# --- MAIN INTERFACE ---
db = init_db()
st.title("💎 Equity Intelligence")
st.caption("Multi-Model Valuation Dashboard (KGV + DDM)")

try:
    res = db.table("watchlist").select("ticker").execute()
    tickers = [t['ticker'].upper() for t in res.data]

    if tickers:
        # Header Metriken
        eur_usd = yf.download("EURUSD=X", period="1d", progress=False)['Close'].iloc[-1]
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Currency", "EUR/USD", round(eur_usd, 4))
        m2.metric("Watchlist Size", f"{len(tickers)} Stocks")
        m3.metric("Top Signal", f"{tickers[0]}") # Platzhalter für echte Logik

        st.divider()

        # Daten laden
        with st.spinner('Calculating Fair Values...'):
            data = [get_stock_metrics(t, eur_usd) for t in tickers]
            df = pd.DataFrame([d for d in data if d]).sort_values("_rank")

        # Hochwertige Tabelle
        st.subheader("Market Opportunities")
        st.dataframe(
            df.drop(columns=["_rank"]),
            column_config={
                "Ticker": st.column_config.TextColumn("Asset", help="Ticker Symbol", width="small"),
                "Upside (%)": st.column_config.ProgressColumn("Upside Pot.", format="%f%%", min_value=-50, max_value=50),
                "Trend (1Y)": st.column_config.LineChartColumn("1Y Price Trend"),
                "Price (€)": st.column_config.NumberColumn(format="€ %.2f"),
                "Fair Value (€)": st.column_config.NumberColumn(format="€ %.2f"),
                "RSI": st.column_config.NumberColumn(format="%.1f")
            },
            hide_index=True,
            use_container_width=True
        )

        # Gauge Charts in einem Grid
        st.divider()
        st.subheader("Valuation Gauges")
        cols = st.columns(4)
        for i, row in df.iterrows():
            with cols[i % 4]:
                fig = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=row['Price (€)'],
                    title={'text': row['Ticker'], 'font': {'size': 16, 'color': 'white'}},
                    gauge={
                        'axis': {'range': [0, row['Fair Value (€)'] * 1.5], 'tickcolor': "white"},
                        'bar': {'color': "#58a6ff"},
                        'bgcolor': "#1e2130",
                        'threshold': {'line': {'color': "#238636", 'width': 4}, 'value': row['Fair Value (€)']}
                    }
                ))
                fig.update_layout(height=180, margin=dict(l=20,r=20,t=40,b=20), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"Error loading dashboard: {e}")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Settings")
    new_t = st.text_input("Add Ticker").upper()
    if st.button("Add to List"):
        if new_t:
            db.table("watchlist").insert({"ticker": new_t}).execute()
            st.rerun()
    
    st.divider()
    if st.button("🔄 Force Refresh"):
        st.cache_data.clear()
        st.rerun()
