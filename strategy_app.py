import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from supabase import create_client, Client
import numpy as np

# --- 1. SETUP & STYLE ---
st.set_page_config(page_title="Equity Intelligence Pro", layout="wide")

# CSS für Metrik-Karten und Tabelle
st.markdown("""
    <style>
    .stMetric { background-color: #1e2130; padding: 10px; border-radius: 8px; border: 1px solid #30363d; }
    .main { background-color: #0e1117; }
    </style>
    """, unsafe_allow_html=True)

# --- 2. HILFSFUNKTIONEN ---
@st.cache_resource
def init_db():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def fetch_data(ticker):
    try:
        tk = yf.Ticker(ticker)
        # Wir brauchen "max" für die Berechnung der historischen durchschnittlichen Korrektur
        hist = tk.history(period="max") 
        if hist.empty: return None
        return tk, hist, tk.info
    except: return None

def calculate_avg_drawdown(hist):
    # Berechnet den durchschnittlichen Drawdown (Korrektur) vom jeweiligen Hoch
    # Wir betrachten nur Drawdowns, die größer als 10% waren, um Rauschen zu filtern
    running_max = hist['Close'].cummax()
    drawdown = (hist['Close'] - running_max) / running_max
    
    # Filtere Drawdowns, die tiefer als -10% waren
    significant_drawdowns = drawdown[drawdown < -0.10]
    
    if significant_drawdowns.empty:
        return 0.0
    return significant_drawdowns.mean() * 100 # Gibt z.B. -15.5 zurück

# --- 3. HAUPTPROGRAMM ---
db = init_db()
st.title("💎 Equity Intelligence: Dynamic Strategy")

# --- SIDEBAR EINSTELLUNGEN ---
with st.sidebar:
    st.header("⚙️ Strategie Parameter")
    
    # 1. Variable Margin of Safety
    mos_pct = st.slider("Margin of Safety (%)", min_value=1, max_value=20, value=10, step=1) / 100
    
    st.divider()
    st.header("Verwaltung")
    new_ticker = st.text_input("Ticker hinzufügen").upper()
    if st.button("Speichern"):
        if new_ticker:
            db.table("watchlist").insert({"ticker": new_ticker}).execute()
            st.rerun()

try:
    res = db.table("watchlist").select("ticker").execute()
    tickers = [t['ticker'].upper() for t in res.data]
    
    eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
    eur_usd = float(eur_usd_data['Close'].iloc[-1])

    if tickers:
        all_results = []
        
        # Ladebalken für bessere UX bei vielen Daten
        progress_text = "Analysiere Markt..."
        my_bar = st.progress(0, text=progress_text)
        
        for i, t in enumerate(tickers):
            bundle = fetch_data(t)
            if not bundle: continue
            tk, hist, info = bundle
            
            # --- DATEN BERECHNUNG ---
            # Kurse
            price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
            price_eur = price_usd / eur_usd
            
            # Fair Value (KGV Modell)
            fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
            growth = info.get('earningsGrowth') or 0.1
            kgv = info.get('forwardPE') or 20
            
            fv_usd = (fwd_eps * (1+growth)**2) * kgv
            fv_eur = fv_usd / eur_usd
            
            # Indikatoren
            rsi_now = ta.rsi(hist['Close'], length=14).iloc[-1]
            
            # EMA 200 Trend
            if len(hist) > 200:
                ema200 = ta.ema(hist['Close'], length=200).iloc[-1]
                trend_signal = "Bullish" if price_usd > ema200 else "Bearish"
            else:
                trend_signal = "N/A"
            
            # Volumen Check
            vol_now = hist['Volume'].iloc[-1]
            vol_ma = hist['Volume'].tail(20).mean()
            vol_status = "BUY Vol" if vol_now > (vol_ma * 1.5) else ("SELL Vol" if vol_now < (vol_ma * 0.8) else "Normal")

            # --- SIGNAL LOGIK MIT VARIABLER MoS ---
            # KAUF: Kurs <= Fair Value * (1 - MoS) UND RSI < 40
            buy_limit = fv_eur * (1 - mos_pct)
            watch_limit = fv_eur * (1 + mos_pct) # Beobachten bis +MoS über FV
            
            upside = ((fv_eur - price_eur) / price_eur) * 100
            
            if price_eur <= buy_limit and rsi_now < 40:
                signal = "🟢 KAUF"
                rank = 1
            elif price_eur <= watch_limit:
                signal = "🟡 BEOBACHTEN"
                rank = 2
            else:
                signal = "🔴 WARTEN"
                rank = 3
            
            # Historische Daten für Detailview speichern
            ath_usd = hist['High'].max()
            corr_ath = ((price_usd - ath_usd) / ath_usd) * 100
            avg_corr = calculate_avg_drawdown(hist)

            all_results.append({
                "Ticker": t,
                "Kurs (€)": price_eur,
                "Fair Value (€)": fv_eur,
                "Upside (%)": upside,
                "RSI": rsi_now,
                "Signal": signal,
                # Versteckte Daten für Detailansicht
                "_price_usd": price_usd,
                "_fv_usd": fv_usd,
                "_corr_ath": corr_ath,
                "_avg_corr": avg_corr,
                "_trend": trend_signal,
                "_vol": vol_status,
                "_rank": rank
            })
            my_bar.progress((i + 1) / len(tickers), text=f"Analysiere {t}...")

        my_bar.empty()
        
        df = pd.DataFrame(all_results).sort_values(["_rank", "Upside (%)"], ascending=[True, False])

        # --- TABELLE MIT ROW-HIGHLIGHTING ---
        st.subheader(f"Marktanalyse (MoS: {int(mos_pct*100)}%)")
        
        def highlight_rows(row):
            # Farben für Dark Mode optimiert
            if "🟢" in row['Signal']:
                return ['background-color: #1e4620'] * len(row) # Dunkles Grün
            elif "🟡" in row['Signal']:
                return ['background-color: #4d4d00'] * len(row) # Dunkles Gelb/Olive
            elif "🔴" in row['Signal']:
                return ['background-color: #4a1b1b'] * len(row) # Dunkles Rot
            return [''] * len(row)

        # Anzuzeigende Spalten definieren
        display_cols = ["Ticker", "Kurs (€)", "Fair Value (€)", "Upside (%)", "RSI", "Signal"]
        
        st.dataframe(
            df[display_cols].style.apply(highlight_rows, axis=1)
            .format({"Kurs (€)": "{:.2f}", "Fair Value (€)": "{:.2f}", "Upside (%)": "{:.1f}", "RSI": "{:.1f}"}),
            use_container_width=True, 
            hide_index=True
        )

        st.divider()

        # --- erweiterte DETAIL ANSICHT ---
        selected = st.selectbox("🔬 Tiefenanalyse starten", ["Wählen..."] + tickers)
        
        if selected != "Wählen...":
            # Daten aus dem DataFrame holen
            row = df[df['Ticker'] == selected].iloc[0]
            tk, hist, info = fetch_data(selected) # Historie für Chart neu laden
            
            # 1. METRIK REIHE (Preis & FV)
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Kurs aktuell", f"{row['_price_usd']:.2f} $", f"{row['Kurs (€)']:.2f} €")
            c2.metric("Fair Value (USD)", f"{row['_fv_usd']:.2f} $", delta=f"{row['Upside (%)']:.1f}% Upside")
            c3.metric("RSI (14)", f"{row['RSI']:.1f}", delta="-Überverkauft" if row['RSI'] < 30 else "Normal", delta_color="inverse")
            c4.metric("Korrektur (ATH)", f"{row['_corr_ath']:.1f}%", f"Ø Hist: {row['_avg_corr']:.1f}%")
            c5.metric("Trend (EMA200)", f"{row['_trend']}", f"{row['_vol']}")
            
            # 2. CHART
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])

            hist_eur = hist['Close'] / eur_usd
            fv_current = row['Fair Value (€)']
            
            # Kurs
            fig.add_trace(go.Scatter(x=hist.index, y=hist_eur, name="Kurs (€)", line=dict(color='#58a6ff', width=2)), row=1, col=1)
            
            # MoS Band (Variable aus Sidebar)
            mos_upper = fv_current * (1 + mos_pct)
            mos_lower = fv_current * (1 - mos_pct)
            
            # Band zeichnen
            fig.add_trace(go.Scatter(x=[hist.index[0], hist.index[-1]], y=[mos_upper, mos_upper], mode='lines', line=dict(width=0), showlegend=False), row=1, col=1)
            fig.add_trace(go.Scatter(x=[hist.index[0], hist.index[-1]], y=[mos_lower, mos_lower], mode='lines', line=dict(width=0), 
                                     fill='tonexty', fillcolor='rgba(40, 167, 69, 0.2)', name=f"Fair Value Zone (+/-{int(mos_pct*100)}%)"), row=1, col=1)
            
            # FV Linie
            fig.add_hline(y=fv_current, line_dash="dash", line_color="#28a745", annotation_text="Fair Value", row=1, col=1)
            
            # EMA 200 (falls vorhanden)
            if len(hist) > 200:
                ema = ta.ema(hist['Close'], length=200) / eur_usd
                fig.add_trace(go.Scatter(x=hist.index, y=ema, name="EMA 200", line=dict(color='orange', width=1)), row=1, col=1)

            # RSI
            rsi_series = ta.rsi(hist['Close'], length=14)
            fig.add_trace(go.Scatter(x=hist.index, y=rsi_series, name="RSI", line=dict(color='#ff7f0e')), row=2, col=1)
            fig.add_hline(y=40, line_dash="dot", line_color="cyan", annotation_text="Buy Zone (<40)", row=2, col=1)
            fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)

            fig.update_layout(height=700, template="plotly_dark", hovermode="x unified", title=f"Tiefenanalyse: {selected}")
            st.plotly_chart(fig, use_container_width=True)

    else:
        st.info("Watchlist ist leer.")

except Exception as e:
    st.error(f"Ein Fehler ist aufgetreten: {e}")
