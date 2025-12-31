import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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
st.title("💎 Equity Intelligence: MoS & RSI Strategy")

try:
    res = db.table("watchlist").select("ticker").execute()
    tickers = [t['ticker'].upper() for t in res.data]
    
    eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
    eur_usd = float(eur_usd_data['Close'].iloc[-1])

    if tickers:
        all_results = []
        with st.spinner('Analysiere Strategie-Parameter...'):
            for t in tickers:
                bundle = fetch_data(t)
                if not bundle: continue
                tk, hist, info = bundle
                
                # Fair Value Logik (KGV-Modell)
                fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
                growth = info.get('earningsGrowth') or 0.1
                kgv = info.get('forwardPE') or 20
                fv_eur = ((fwd_eps * (1+growth)**2) * kgv) / eur_usd
                
                price_eur = (info.get('currentPrice') or hist['Close'].iloc[-1]) / eur_usd
                upside = ((fv_eur - price_eur) / price_eur) * 100
                rsi_now = ta.rsi(hist['Close'], length=14).iloc[-1]
                
                # --- STRATEGIE LOGIK (MoS & RSI) ---
                # Kauf wenn Kurs im/unter dem 10% Band UND RSI < 40
                mos_upper = fv_eur * 1.10
                
                if price_eur <= mos_upper and rsi_now < 40:
                    signal = "🟢 KAUF"
                    rank = 1
                elif price_eur <= fv_eur * 1.20: # Puffer für Beobachten
                    signal = "🟡 BEOBACHTEN"
                    rank = 2
                else:
                    signal = "🔴 WARTEN"
                    rank = 3
                
                all_results.append({
                    "Ticker": t,
                    "Preis (€)": round(price_eur, 2),
                    "Fair Value (€)": round(fv_eur, 2),
                    "Upside (%)": round(upside, 1),
                    "RSI": round(rsi_now, 1),
                    "Signal": signal,
                    "_rank": rank
                })

        df = pd.DataFrame(all_results).sort_values(["_rank", "Upside (%)"], ascending=[True, False])
        
        st.subheader("📊 Strategie-Ranking")
        st.dataframe(df.drop(columns=["_rank"]), use_container_width=True, hide_index=True)

        st.divider()

        # --- DETAIL ANSICHT MIT DYNAMISCHER MoS ZONE ---
        selected = st.selectbox("🔬 Tiefenanalyse auswählen", ["Bitte wählen..."] + tickers)
        
        if selected != "Bitte wählen...":
            tk, hist, info = fetch_data(selected)
            fv_val = df.loc[df['Ticker'] == selected, 'Fair Value (€)'].values[0]
            
            # Subplots: Oben Kurs + MoS Zone, Unten RSI
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                               vertical_spacing=0.05, row_heights=[0.7, 0.3])

            hist_eur = hist['Close'] / eur_usd
            
            # 1. Kurs
            fig.add_trace(go.Scatter(x=hist.index, y=hist_eur, name="Kurs (EUR)", line=dict(color='#58a6ff', width=2)), row=1, col=1)
            
            # 2. Margin of Safety Zone (+/- 10%)
            mos_up = fv_val * 1.10
            mos_down = fv_val * 0.90
            
            fig.add_trace(go.Scatter(x=[hist.index[0], hist.index[-1]], y=[mos_up, mos_up], mode='lines', line=dict(width=0), showlegend=False), row=1, col=1)
            fig.add_trace(go.Scatter(x=[hist.index[0], hist.index[-1]], y=[mos_down, mos_down], mode='lines', line=dict(width=0), 
                                     fill='tonexty', fillcolor='rgba(40, 167, 69, 0.15)', name="MoS Zone (+/-10%)"), row=1, col=1)
            
            # Fair Value Mittellinie
            fig.add_hline(y=fv_val, line_dash="dash", line_color="#28a745", row=1, col=1, annotation_text="Fair Value")

            # 3. RSI Indikator
            rsi_series = ta.rsi(hist['Close'], length=14)
            fig.add_trace(go.Scatter(x=hist.index, y=rsi_series, name="RSI", line=dict(color='#ff7f0e')), row=2, col=1)
            
            # RSI Kauf-Marke bei 40
            fig.add_hline(y=40, line_dash="dot", line_color="cyan", row=2, col=1, annotation_text="Kauf-Limit (40)")
            fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)

            fig.update_layout(height=650, template="plotly_dark", hovermode="x unified", showlegend=True, 
                              legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            st.plotly_chart(fig, use_container_width=True)

    else:
        st.info("Watchlist leer. Ticker in Supabase hinzufügen.")

except Exception as e:
    st.error(f"Fehler: {e}")
