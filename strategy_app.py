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
    .stMetric { background-color: #1e2130; padding: 10px; border-radius: 8px; border: 1px solid #30363d; }
    .main { background-color: #0e1117; }
    [data-testid="stMetricDelta"] { color: #aaaaaa !important; }
    </style>
    """, unsafe_allow_html=True)

# --- 2. HILFSFUNKTIONEN (MIT CACHING!) ---
@st.cache_resource
def init_db():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

# WICHTIG: ttl=3600 bedeutet, die Daten werden für 1 Stunde gespeichert und nicht neu geladen
@st.cache_data(ttl=3600, show_spinner=False)
def get_market_data(ticker):
    try:
        tk = yf.Ticker(ticker)
        # history(period="max") kann teuer sein, wir laden es nur einmal pro Stunde
        hist = tk.history(period="max") 
        if hist.empty: return None
        return hist, tk.info
    except: return None

def calculate_avg_drawdown(hist):
    running_max = hist['Close'].cummax()
    drawdown = (hist['Close'] - running_max) / running_max
    significant_drawdowns = drawdown[drawdown < -0.10]
    return significant_drawdowns.mean() * 100 if not significant_drawdowns.empty else 0.0

# --- 3. HAUPTPROGRAMM ---
db = init_db()
st.title("💎 Equity Intelligence: Fair Value 2026")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Strategie Parameter")
    mos_pct = st.slider("Margin of Safety (Kauf-Zone)", min_value=1, max_value=30, value=10, step=1) / 100
    
    st.divider()
    # Button zum manuellen Neuladen der Daten
    if st.button("🔄 Daten aktualisieren"):
        st.cache_data.clear()
        st.rerun()
        
    st.divider()
    new_ticker = st.text_input("Ticker hinzufügen").upper()
    if st.button("Speichern"):
        if new_ticker:
            db.table("watchlist").insert({"ticker": new_ticker}).execute()
            st.cache_data.clear() # Cache leeren, damit neuer Ticker geladen wird
            st.rerun()

try:
    res = db.table("watchlist").select("ticker").execute()
    tickers = [t['ticker'].upper() for t in res.data]
    
    # Auch EUR/USD cachen wir, damit es nicht bremst
    eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
    # Sicherheits-Check falls Download fehlschlägt
    if not eur_usd_data.empty:
        eur_usd = float(eur_usd_data['Close'].iloc[-1])
    else:
        eur_usd = 1.05 # Fallback

    if tickers:
        all_results = []
        
        # Wir bauen die Liste auf
        with st.spinner('Lade Marktdaten (gecached)...'):
            for t in tickers:
                data = get_market_data(t) # Nutzt jetzt den Cache!
                if not data: continue
                hist, info = data
                
                # --- BERECHNUNG ---
                eps_2026 = info.get('forwardEps') or info.get('trailingEps') or 1.0
                kgv_normal = info.get('forwardPE') or 20.0
                kgv_konservativ = kgv_normal * 0.8
                
                fv_usd = ((eps_2026 * kgv_konservativ) + (eps_2026 * kgv_normal)) / 2
                fv_eur = fv_usd / eur_usd
                
                price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
                price_eur = price_usd / eur_usd
                
                rsi_now = ta.rsi(hist['Close'], length=14).iloc[-1]
                upside = ((fv_eur - price_eur) / price_eur) * 100
                
                # Trend & Vol
                ema200 = ta.ema(hist['Close'], length=200).iloc[-1] if len(hist) > 200 else 0
                trend_str = "Bullish" if price_usd > ema200 else "Bearish"
                
                vol_now = hist['Volume'].iloc[-1]
                vol_ma = hist['Volume'].tail(20).mean()
                vol_str = "BUY Vol" if vol_now > (vol_ma * 1.5) else ("SELL Vol" if vol_now < (vol_ma * 0.8) else "Normal")

                # Signal
                buy_limit = fv_eur * (1 - mos_pct)
                watch_limit = fv_eur * (1 + mos_pct)
                
                if price_eur <= buy_limit and rsi_now < 40:
                    signal = "🟢 KAUF"
                    rank = 1
                elif price_eur <= watch_limit:
                    signal = "🟡 BEOBACHTEN"
                    rank = 2
                else:
                    signal = "🔴 WARTEN"
                    rank = 3
                    
                ath = hist['High'].max()
                corr_ath = ((price_usd - ath) / ath) * 100
                avg_dd = calculate_avg_drawdown(hist)

                all_results.append({
                    "Ticker": t,
                    "Kurs (€)": price_eur,
                    "Fair Value (€)": fv_eur,
                    "Upside (%)": upside,
                    "RSI": rsi_now,
                    "Signal": signal,
                    "_price_usd": price_usd,
                    "_fv_usd": fv_usd,
                    "_corr_ath": corr_ath,
                    "_avg_dd": avg_dd,
                    "_trend": trend_str,
                    "_vol": vol_str,
                    "_rank": rank
                })

        # --- SICHERHEITS-CHECK: Ist die Liste voll? ---
        if all_results:
            df = pd.DataFrame(all_results).sort_values(["_rank", "Upside (%)"], ascending=[True, False])

            # --- TABELLE ---
            def highlight_rows(row):
                if "🟢" in row['Signal']: return ['background-color: #1e4620'] * len(row)
                elif "🟡" in row['Signal']: return ['background-color: #4d4d00'] * len(row)
                elif "🔴" in row['Signal']: return ['background-color: #4a1b1b'] * len(row)
                return [''] * len(row)

            st.subheader("Markt-Ranking")
            st.dataframe(
                df[["Ticker", "Kurs (€)", "Fair Value (€)", "Upside (%)", "RSI", "Signal"]]
                .style.apply(highlight_rows, axis=1)
                .format({"Kurs (€)": "{:.2f}", "Fair Value (€)": "{:.2f}", "Upside (%)": "{:.1f}", "RSI": "{:.1f}"}),
                use_container_width=True, hide_index=True
            )

            st.divider()

            # --- TIEFENANALYSE ---
            selected = st.selectbox("🔬 Tiefenanalyse starten", ["Wählen..."] + tickers)
            
            if selected != "Wählen..." and selected in df['Ticker'].values:
                # Daten sicher aus DF holen
                row = df[df['Ticker'] == selected].iloc[0]
                
                # Historie für Chart holen (schnell, da gecached)
                hist_data = get_market_data(selected)
                if hist_data:
                    hist, _ = hist_data # Info brauchen wir hier nicht nochmal
                    
                    # Metriken Reihe
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("Kurs", f"{row['_price_usd']:.2f} $", delta=f"≈ {row['Kurs (€)']:.2f} €", delta_color="off")
                    c2.metric("Fair Value Ø", f"{row['_fv_usd']:.2f} $", delta=f"≈ {row['Fair Value (€)']:.2f} €", delta_color="off")
                    c3.metric("RSI (14)", f"{row['RSI']:.1f}", delta="Überverkauft (<30)" if row['RSI'] < 30 else ("Kaufzone (<40)" if row['RSI'] < 40 else None), delta_color="inverse")
                    c4.metric("Korrektur (ATH)", f"{row['_corr_ath']:.1f}%", delta=f"Ø Hist: {row['_avg_dd']:.1f}%", delta_color="off")
                    c5.metric("Trend / Vol", f"{row['_trend']}", delta=f"{row['_vol']}")

                    # Chart
                    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.75, 0.25])
                    
                    hist_eur = hist['Close'] / eur_usd
                    fv_eur = row['Fair Value (€)']
                    
                    fig.add_trace(go.Scatter(x=hist.index, y=hist_eur, name="Kurs (€)", line=dict(color='#58a6ff', width=2)), row=1, col=1)
                    
                    if len(hist) > 200:
                        ema = ta.ema(hist['Close'], length=200) / eur_usd
                        fig.add_trace(go.Scatter(x=hist.index, y=ema, name="EMA 200", line=dict(color='orange', width=1)), row=1, col=1)

                    mos_upper = fv_eur * (1 + mos_pct)
                    mos_lower = fv_eur * (1 - mos_pct)
                    
                    fig.add_trace(go.Scatter(x=[hist.index[0], hist.index[-1]], y=[mos_upper, mos_upper], mode='lines', line=dict(width=0), showlegend=False), row=1, col=1)
                    fig.add_trace(go.Scatter(x=[hist.index[0], hist.index[-1]], y=[mos_lower, mos_lower], mode='lines', line=dict(width=0), 
                                             fill='tonexty', fillcolor='rgba(40, 167, 69, 0.2)', name=f"Fair Value Zone"), row=1, col=1)
                    fig.add_hline(y=fv_eur, line_dash="dash", line_color="#28a745", annotation_text="Fair Value Ø", row=1, col=1)

                    rsi = ta.rsi(hist['Close'], length=14)
                    fig.add_trace(go.Scatter(x=hist.index, y=rsi, name="RSI", line=dict(color='#ff7f0e')), row=2, col=1)
                    fig.add_hline(y=40, line_dash="dot", line_color="cyan", annotation_text="Buy (<40)", row=2, col=1)
                    fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
                    fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)

                    fig.update_layout(height=600, template="plotly_dark", hovermode="x unified", title=f"Chartanalyse: {selected}")
                    st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("Keine Daten verfügbar. Wahrscheinlich blockiert Yahoo Finance gerade zu viele Anfragen. Bitte warte eine Minute und drücke 'Daten aktualisieren'.")
            
    else:
        st.info("Bitte Ticker hinzufügen.")

except Exception as e:
    st.error(f"Systemfehler: {e}")
