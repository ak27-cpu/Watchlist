import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from supabase import create_client, Client
from functools import lru_cache
import requests
from datetime import datetime, timedelta

# --- 1. SETUP & STYLE ---
st.set_page_config(page_title="Equity Intelligence Pro", layout="wide")

st.markdown("""
    <style>
    .stMetric { background-color: #1e2130; padding: 10px; border-radius: 8px; border: 1px solid #30363d; }
    .main { background-color: #0e1117; }
    [data-testid="stMetricDelta"] { color: #aaaaaa !important; }
    </style>
    """, unsafe_allow_html=True)

# --- 2. KONSTANTEN ---
CACHE_TTL = 3600  # 1 Stunde
RSI_LENGTH = 14
EMA_LENGTH = 200
VOLUME_THRESHOLD_UP = 1.5
VOLUME_THRESHOLD_DOWN = 0.8
DRAWDOWN_THRESHOLD = -0.10
FALLBACK_EUR_USD = 1.05
FALLBACK_KGV = 20.0
FALLBACK_EPS = 1.0

# --- 3. HILFSFUNKTIONEN (OPTIMIERT) ---
@st.cache_resource
def init_db():
    """Initialisiere Datenbank einmalig"""
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_market_data(ticker: str) -> tuple | None:
    """Lade Marktdaten mit Fehlerbehandlung"""
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="max")
        if hist.empty:
            return None
        return hist, tk.info
    except Exception as e:
        st.warning(f"Fehler beim Laden von {ticker}: {e}")
        return None

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_exchange_rate(pair: str = "EURUSD=X") -> float:
    """Lade Wechselkurs mit Fallback"""
    try:
        data = yf.download(pair, period="1d", progress=False)
        if not data.empty:
            return float(data['Close'].iloc[-1])
    except Exception as e:
        st.warning(f"Fehler beim EUR/USD-Kurs: {e}")
    return FALLBACK_EUR_USD

def calculate_avg_drawdown(hist: pd.DataFrame) -> float:
    """Berechne durchschnittlichen Drawdown > 10%"""
    running_max = hist['Close'].cummax()
    drawdown = (hist['Close'] - running_max) / running_max
    significant_drawdowns = drawdown[drawdown < DRAWDOWN_THRESHOLD]
    return significant_drawdowns.mean() * 100 if not significant_drawdowns.empty else 0.0

def calculate_technical_metrics(hist: pd.DataFrame, price_usd: float) -> dict:
    """Berechne alle technischen Metriken in einer Funktion"""
    metrics = {}
    
    # RSI
    metrics['rsi'] = ta.rsi(hist['Close'], length=RSI_LENGTH).iloc[-1]
    
    # EMA 200 & Trend
    if len(hist) > EMA_LENGTH:
        ema200 = ta.ema(hist['Close'], length=EMA_LENGTH).iloc[-1]
        metrics['trend'] = "Bullish" if price_usd > ema200 else "Bearish"
        metrics['ema200'] = ema200
    else:
        metrics['trend'] = "N/A"
        metrics['ema200'] = None
    
    # Volume
    vol_now = hist['Volume'].iloc[-1]
    vol_ma = hist['Volume'].tail(20).mean()
    if vol_now > (vol_ma * VOLUME_THRESHOLD_UP):
        metrics['volume'] = "BUY Vol"
    elif vol_now < (vol_ma * VOLUME_THRESHOLD_DOWN):
        metrics['volume'] = "SELL Vol"
    else:
        metrics['volume'] = "Normal"
    
    # ATH & Drawdown
    metrics['ath'] = hist['High'].max()
    metrics['corr_ath'] = ((price_usd - metrics['ath']) / metrics['ath']) * 100
    metrics['avg_dd'] = calculate_avg_drawdown(hist)
    
    return metrics

def generate_signal(price_eur: float, fv_eur: float, rsi: float, mos_pct: float) -> tuple[str, int]:
    """Generiere Kauf-Signal und Ranking"""
    buy_limit = fv_eur * (1 - mos_pct)
    watch_limit = fv_eur * (1 + mos_pct)
    
    if price_eur <= buy_limit and rsi < 40:
        return "🟢 KAUF", 1
    elif price_eur <= watch_limit:
        return "🟡 BEOBACHTEN", 2
    else:
        return "🔴 WARTEN", 3

def calculate_fair_value(eps: float, kgv_normal: float) -> float:
    """Berechne Fair Value als Durchschnitt konservativer & normaler KGV"""
    kgv_konservativ = kgv_normal * 0.8
    return ((eps * kgv_konservativ) + (eps * kgv_normal)) / 2

# --- 4. HAUPTPROGRAMM ---
db = init_db()
st.title("💎 Equity Intelligence: Fair Value 2026")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Strategie Parameter")
    mos_pct = st.slider("Margin of Safety (Kauf-Zone)", min_value=1, max_value=30, value=10, step=1) / 100
    
    st.divider()
    if st.button("🔄 Daten aktualisieren"):
        st.cache_data.clear()
        st.rerun()
        
    st.divider()
    new_ticker = st.text_input("Ticker hinzufügen").upper().strip()
    if st.button("Speichern"):
        if new_ticker and len(new_ticker) <= 5:
            try:
                db.table("watchlist").insert({"ticker": new_ticker}).execute()
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Fehler beim Speichern: {e}")
        elif not new_ticker:
            st.warning("Bitte Ticker eingeben")
        else:
            st.warning("Ticker zu lang")

try:
    # Lade Watchlist
    res = db.table("watchlist").select("ticker").execute()
    tickers = sorted(list(set([t['ticker'].upper() for t in res.data])))  # Duplikate entfernen
    
    if not tickers:
        st.info("Bitte Ticker hinzufügen.")
        st.stop()
    
    # Lade EUR/USD
    eur_usd = get_exchange_rate()

    # --- BATCH-VERARBEITUNG (PARALLEL MÖGLICH) ---
    all_results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, t in enumerate(tickers):
        status_text.text(f"Verarbeite {t}... ({idx + 1}/{len(tickers)})")
        progress_bar.progress((idx + 1) / len(tickers))
        
        data = get_market_data(t)
        if not data:
            continue
        
        hist, info = data
        
        # --- EXTRAHIERE INFO (mit Fallbacks) ---
        eps_2026 = info.get('forwardEps') or info.get('trailingEps') or FALLBACK_EPS
        kgv_normal = info.get('forwardPE') or FALLBACK_KGV
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        
        # --- BERECHNUNGEN ---
        fv_usd = calculate_fair_value(eps_2026, kgv_normal)
        fv_eur = fv_usd / eur_usd
        price_eur = price_usd / eur_usd
        upside = ((fv_eur - price_eur) / price_eur) * 100
        
        # Technische Metriken
        tech_metrics = calculate_technical_metrics(hist, price_usd)
        rsi_now = tech_metrics['rsi']
        
        # Signal
        signal, rank = generate_signal(price_eur, fv_eur, rsi_now, mos_pct)
        
        all_results.append({
            "Ticker": t,
            "Kurs (€)": price_eur,
            "Fair Value (€)": fv_eur,
            "Upside (%)": upside,
            "RSI": rsi_now,
            "Signal": signal,
            "_price_usd": price_usd,
            "_fv_usd": fv_usd,
            "_corr_ath": tech_metrics['corr_ath'],
            "_avg_dd": tech_metrics['avg_dd'],
            "_trend": tech_metrics['trend'],
            "_vol": tech_metrics['volume'],
            "_rank": rank,
            "_ema200": tech_metrics['ema200']
        })
    
    progress_bar.empty()
    status_text.empty()
    
    # --- TABELLE & RANKING ---
    if all_results:
        df = pd.DataFrame(all_results).sort_values(["_rank", "Upside (%)"], ascending=[True, False])

        def highlight_rows(row):
            if "🟢" in row['Signal']:
                return ['background-color: #1e4620'] * len(row)
            elif "🟡" in row['Signal']:
                return ['background-color: #4d4d00'] * len(row)
            elif "🔴" in row['Signal']:
                return ['background-color: #4a1b1b'] * len(row)
            return [''] * len(row)

        st.subheader("📊 Markt-Ranking")
        st.dataframe(
            df[["Ticker", "Kurs (€)", "Fair Value (€)", "Upside (%)", "RSI", "Signal"]]
            .style.apply(highlight_rows, axis=1)
            .format({"Kurs (€)": "{:.2f}", "Fair Value (€)": "{:.2f}", "Upside (%)": "{:.1f}", "RSI": "{:.1f}"}),
            use_container_width=True,
            hide_index=True
        )

        st.divider()

        # --- TIEFENANALYSE ---
        st.subheader("🔬 Tiefenanalyse")
        selected = st.selectbox("Ticker auswählen", df['Ticker'].values)
        
        if selected:
            row = df[df['Ticker'] == selected].iloc[0]
            hist_data = get_market_data(selected)
            
            if hist_data:
                hist, _ = hist_data
                
                # Metriken
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("Kurs", f"{row['_price_usd']:.2f} $", delta=f"≈ {row['Kurs (€)']:.2f} €", delta_color="off")
                col2.metric("Fair Value Ø", f"{row['_fv_usd']:.2f} $", delta=f"≈ {row['Fair Value (€)']:.2f} €", delta_color="off")
                
                rsi_status = "Überverkauft (<30)" if row['RSI'] < 30 else ("Kaufzone (<40)" if row['RSI'] < 40 else "Neutral")
                col3.metric("RSI (14)", f"{row['RSI']:.1f}", delta=rsi_status, delta_color="inverse")
                col4.metric("Korrektur (ATH)", f"{row['_corr_ath']:.1f}%", delta=f"Ø: {row['_avg_dd']:.1f}%", delta_color="off")
                col5.metric("Trend / Vol", f"{row['_trend']}", delta=f"{row['_vol']}")

                # --- OPTIMIERTER CHART ---
                fig = make_subplots(
                    rows=2, cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.08,
                    row_heights=[0.7, 0.3],
                    subplot_titles=(f"{selected} - Preis & Fair Value", "RSI (14)")
                )
                
                hist_eur = hist['Close'] / eur_usd
                fv_eur = row['Fair Value (€)']
                
                # Kurs
                fig.add_trace(
                    go.Scatter(x=hist.index, y=hist_eur, name="Kurs (€)", 
                              line=dict(color='#58a6ff', width=2)),
                    row=1, col=1
                )
                
                # EMA 200 (nur wenn vorhanden)
                if row['_ema200'] is not None:
                    ema = ta.ema(hist['Close'], length=EMA_LENGTH) / eur_usd
                    fig.add_trace(
                        go.Scatter(x=hist.index, y=ema, name="EMA 200",
                                  line=dict(color='orange', width=1, dash='dash')),
                        row=1, col=1
                    )

                # Fair Value Zone
                mos_upper = fv_eur * (1 + mos_pct)
                mos_lower = fv_eur * (1 - mos_pct)
                
                fig.add_trace(
                    go.Scatter(x=[hist.index[0], hist.index[-1]], y=[mos_upper, mos_upper],
                              mode='lines', line=dict(width=0), showlegend=False),
                    row=1, col=1
                )
                fig.add_trace(
                    go.Scatter(x=[hist.index[0], hist.index[-1]], y=[mos_lower, mos_lower],
                              mode='lines', line=dict(width=0),
                              fill='tonexty', fillcolor='rgba(40, 167, 69, 0.2)',
                              name=f"Fair Value Zone (±{mos_pct*100:.0f}%)"),
                    row=1, col=1
                )
                fig.add_hline(y=fv_eur, line_dash="dash", line_color="#28a745",
                             annotation_text="Fair Value", row=1, col=1)

                # RSI
                rsi = ta.rsi(hist['Close'], length=RSI_LENGTH)
                fig.add_trace(
                    go.Scatter(x=hist.index, y=rsi, name="RSI",
                              line=dict(color='#ff7f0e', width=1.5)),
                    row=2, col=1
                )
                fig.add_hline(y=40, line_dash="dot", line_color="cyan", annotation_text="Buy", row=2, col=1)
                fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
                fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)

                fig.update_layout(
                    height=600,
                    template="plotly_dark",
                    hovermode="x unified",
                    title=f"Chartanalyse: {selected}",
                    xaxis_rangeslider_visible=False
                )
                fig.update_yaxes(title_text="Preis (€)", row=1, col=1)
                fig.update_yaxes(title_text="RSI", row=2, col=1)
                
                st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Keine Daten verfügbar. Yahoo Finance blockiert möglicherweise die Anfragen. Bitte warten und erneut versuchen.")

except Exception as e:
    st.error(f"Systemfehler: {e}")
    import traceback
    st.write(traceback.format_exc())
