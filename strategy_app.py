import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from supabase import create_client, Client
import time
import numpy as np

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
CACHE_TTL = 3600
WATCHLIST_CACHE_TTL = 600
RSI_LENGTH = 14
EMA_LENGTH = 200
VOLUME_THRESHOLD_UP = 1.5
VOLUME_THRESHOLD_DOWN = 0.8
DRAWDOWN_THRESHOLD = -0.10
FALLBACK_EUR_USD = 1.055

# DCF Parameter
WACC = 0.0989  # 9.89% (Standard für Quality Companies)
TERMINAL_GROWTH = 0.025  # 2.5% (langfristiges GDP-Wachstum)

# --- 3. HILFSFUNKTIONEN ---
@st.cache_resource
def init_db():
    """Initialisiere Datenbank einmalig"""
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_market_data(ticker: str) -> tuple | None:
    """Lade maximale historische Daten"""
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="max")
        if hist.empty:
            return None
        return hist, tk.info
    except Exception:
        return None

@st.cache_data(ttl=WATCHLIST_CACHE_TTL, show_spinner=False)
def get_watchlist():
    """Watchlist aus Supabase laden"""
    db = init_db()
    try:
        res = db.table("watchlist").select("ticker").execute()
        tickers = sorted(list(set(t['ticker'].upper() for t in res.data)))
        return tickers
    except Exception:
        return []

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_eur_usd():
    """EUR/USD Kurs laden mit Fallback"""
    try:
        eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
        if not eur_usd_data.empty:
            rate = float(eur_usd_data['Close'].iloc[-1])
            if 0.9 < rate < 1.2:
                return rate
    except Exception:
        pass
    return FALLBACK_EUR_USD

def calculate_fcf_per_share(info: dict) -> float:
    """Berechne Free Cash Flow pro Aktie"""
    try:
        fcf = info.get('freeCashflow') or 0
        shares = info.get('sharesOutstanding') or 1
        if fcf > 0 and shares > 0:
            return fcf / shares
    except Exception:
        pass
    return 0.0

def calculate_dcf_fair_value(fcf_per_share: float, growth_rate: float = 0.10, wacc: float = WACC, terminal_growth: float = TERMINAL_GROWTH) -> dict:
    """
    DCF-Bewertung (Buffett-Style)
    
    Simplified Gordon Growth Model:
    Fair Value = FCF_pro_Share × (1 + Growth) / (WACC - Growth)
    
    Plus 10-Jahres Projection für Details
    """
    
    if fcf_per_share <= 0:
        return {"fv": 0, "pv_10y": 0, "pv_terminal": 0, "error": True}
    
    try:
        # Vereinfachte Gordon Growth Model
        if wacc <= growth_rate:
            # Fallback wenn Wachstum >= WACC (unrealistisch)
            fv = fcf_per_share * 15  # Einfacher Multiplikator
            return {"fv": fv, "pv_10y": fv * 0.7, "pv_terminal": fv * 0.3, "method": "Fallback"}
        
        # DCF 10-Jahres Projection
        pv_10y = 0
        fcf_current = fcf_per_share
        
        for year in range(1, 11):
            fcf_projected = fcf_per_share * ((1 + growth_rate) ** year)
            pv = fcf_projected / ((1 + wacc) ** year)
            pv_10y += pv
        
        # Terminal Value (Gordon Growth nach Jahr 10)
        fcf_year10 = fcf_per_share * ((1 + growth_rate) ** 10)
        terminal_value = (fcf_year10 * (1 + terminal_growth)) / (wacc - terminal_growth)
        pv_terminal = terminal_value / ((1 + wacc) ** 10)
        
        # Fair Value = Sum PV 10 Jahre + PV Terminal Value
        fv = pv_10y + pv_terminal
        
        return {
            "fv": fv,
            "pv_10y": pv_10y,
            "pv_terminal": pv_terminal,
            "fcf_year10": fcf_year10,
            "terminal_value": terminal_value,
            "error": False,
            "method": "DCF 10-Year + Terminal"
        }
    
    except Exception as e:
        return {"fv": 0, "error": True, "error_msg": str(e)}

def calculate_avg_drawdown(hist: pd.DataFrame) -> float:
    """Berechne durchschnittlichen Drawdown"""
    running_max = hist['Close'].cummax()
    drawdown = (hist['Close'] - running_max) / running_max
    significant_drawdowns = drawdown[drawdown < DRAWDOWN_THRESHOLD]
    return significant_drawdowns.mean() * 100 if not significant_drawdowns.empty else 0.0

def calculate_technical_metrics(hist: pd.DataFrame, price_usd: float) -> dict:
    """Berechne technische Metriken"""
    metrics = {}
    metrics['rsi'] = ta.rsi(hist['Close'], length=RSI_LENGTH).iloc[-1]
    
    if len(hist) > EMA_LENGTH:
        ema200 = ta.ema(hist['Close'], length=EMA_LENGTH).iloc[-1]
        metrics['trend'] = "Bullish" if price_usd > ema200 else "Bearish"
        metrics['ema200'] = ema200
    else:
        metrics['trend'] = "N/A"
        metrics['ema200'] = None
    
    vol_now = hist['Volume'].iloc[-1]
    vol_ma = hist['Volume'].tail(20).mean()
    if vol_now > (vol_ma * VOLUME_THRESHOLD_UP):
        metrics['volume'] = "BUY Vol"
    elif vol_now < (vol_ma * VOLUME_THRESHOLD_DOWN):
        metrics['volume'] = "SELL Vol"
    else:
        metrics['volume'] = "Normal"
    
    metrics['ath'] = hist['High'].max()
    metrics['corr_ath'] = ((price_usd - metrics['ath']) / metrics['ath']) * 100
    metrics['avg_dd'] = calculate_avg_drawdown(hist)
    
    return metrics

def generate_signal(price_eur: float, fv_eur: float, rsi: float, mos_pct: float) -> tuple[str, int]:
    """Generiere Kauf-Signal"""
    buy_limit = fv_eur * (1 - mos_pct)
    watch_limit = fv_eur * (1 + mos_pct)
    
    if price_eur <= buy_limit and rsi < 40:
        return "🟢 KAUF", 1
    elif price_eur <= watch_limit:
        return "🟡 BEOBACHTEN", 2
    else:
        return "🔴 WARTEN", 3

# --- 4. HAUPTPROGRAMM ---
db = init_db()
st.title("💎 Equity Intelligence: DCF Fair Value (Buffett-Style)")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ DCF Parameter")
    
    growth_scenario = st.selectbox(
        "Wachstums-Szenario",
        ["🟢 Optimistisch (12%)", "🟡 Base Case (10%)", "🔴 Konservativ (8%)"],
        index=1
    )
    
    growth_map = {
        "🟢 Optimistisch (12%)": 0.12,
        "🟡 Base Case (10%)": 0.10,
        "🔴 Konservativ (8%)": 0.08
    }
    growth_rate = growth_map[growth_scenario]
    
    st.metric("WACC (Discount Rate)", f"{WACC*100:.2f}%")
    st.metric("Terminal Growth", f"{TERMINAL_GROWTH*100:.1f}%")
    
    st.divider()
    mos_pct = st.slider("Margin of Safety", min_value=1, max_value=30, value=15, step=1) / 100
    
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
            except:
                st.error("Fehler beim Speichern")

try:
    tickers = get_watchlist()
    
    if not tickers:
        st.info("Bitte Ticker hinzufügen (z.B. V für Visa)")
        st.stop()
    
    eur_usd = get_eur_usd()
    all_results = []

    with st.spinner(f'Lade Marktdaten für {len(tickers)} Ticker...'):
        start_time = time.time()
        market_data_map = {}
        
        for t in tickers:
            time.sleep(0.5)
            data = get_market_data(t)
            if data:
                market_data_map[t] = data
        
        load_time = time.time() - start_time

    if not market_data_map:
        st.warning("⚠️ Keine Daten geladen. Bitte später versuchen.")
        st.stop()

    col1, col2, col3 = st.columns([2, 1, 1])
    col1.info(f"✅ {len(market_data_map)}/{len(tickers)} Ticker | Growth: {growth_rate*100:.0f}%")
    
    for t in tickers:
        if t not in market_data_map:
            continue

        hist, info = market_data_map[t]
        
        # --- DCF BERECHNUNG ---
        fcf_per_share = calculate_fcf_per_share(info)
        dcf_result = calculate_dcf_fair_value(fcf_per_share, growth_rate=growth_rate)
        
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        fv_usd = dcf_result.get('fv', 0)
        
        if fv_usd <= 0:
            continue  # Überspringe wenn keine gültigen Daten
        
        fv_eur = fv_usd / eur_usd
        price_eur = price_usd / eur_usd
        upside = ((fv_eur - price_eur) / price_eur) * 100

        tech_metrics = calculate_technical_metrics(hist, price_usd)
        rsi_now = tech_metrics['rsi']
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
            "_fcf_per_share": fcf_per_share,
            "_pv_10y": dcf_result.get('pv_10y', 0),
            "_pv_terminal": dcf_result.get('pv_terminal', 0),
            "_fcf_year10": dcf_result.get('fcf_year10', 0),
            "_corr_ath": tech_metrics['corr_ath'],
            "_avg_dd": tech_metrics['avg_dd'],
            "_trend": tech_metrics['trend'],
            "_vol": tech_metrics['volume'],
            "_rank": rank,
            "_ema200": tech_metrics['ema200']
        })

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

        st.subheader("🔬 DCF-Tiefenanalyse")
        selected = st.selectbox("Ticker auswählen", df['Ticker'].values)
        
       # --- Ersetze diesen kompletten Abschnitt ---
if selected:
    row = df[df['Ticker'] == selected].iloc[0]
    hist, info = market_data_map[selected]

    st.subheader(f"DCF Fair Value Analyse: {selected}")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("FCF/Share", f"${row['_fcf_per_share']:.2f}")
    col2.metric("PV (10 Jahre)", f"${row['_pv_10y']:.2f}")
    col3.metric("PV Terminal", f"${row['_pv_terminal']:.2f}")
    col4.metric("Fair Value", f"${row['_fv_usd']:.2f}")
    
    # FIX: Variablen extrahieren für f-string
    fcf_share = row['_fcf_per_share']
    fv_usd = row['_fv_usd']
    fv_eur = row['Fair Value (€)']
    kurs_eur = row['Kurs (€)']
    upside_pct = row['Upside (%)']
    signal = row['Signal']
    
    st.markdown(f"""
    **DCF-Berechnung (Buffett-Style):**
    
    **Annahmen:**
    - Free Cash Flow pro Share: **${fcf_share:.2f}**
    - Wachstum 10 Jahre: **{growth_rate*100:.0f}%**
    - WACC (Discount Rate): **{WACC*100:.2f}%**
    - Terminal Growth: **{TERMINAL_GROWTH*100:.1f}%**
    
    **Bewertung:**
    - PV (Cash Flows Jahre 1-10): **${row['_pv_10y']:.2f}**
    - PV (Terminal Value): **${row['_pv_terminal']:.2f}**
    
    **Fair Value Gesamt: ${fv_usd:.2f} ≈ €{fv_eur:.2f}**
    
    **Einschätzung:**
    - Aktueller Kurs: **€{kurs_eur:.2f}**
    - Upside: **{upside_pct:.1f}%**
    - Signal: **{signal}**
    """)


            st.divider()

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Kurs", f"{row['_price_usd']:.2f} $", delta=f"≈ {row['Kurs (€)']:.2f} €", delta_color="off")
            col2.metric("Fair Value", f"{row['_fv_usd']:.2f} $", delta=f"≈ {row['Fair Value (€)']:.2f} €", delta_color="off")
            col3.metric("RSI (14)", f"{row['RSI']:.1f}", delta="Buy Zone" if row['RSI'] < 40 else "Neutral", delta_color="inverse")
            col4.metric("Korrektur", f"{row['_corr_ath']:.1f}%", delta=f"Avg: {row['_avg_dd']:.1f}%", delta_color="off")
            col5.metric("Trend", f"{row['_trend']}", delta=f"{row['_vol']}")

            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.08,
                row_heights=[0.7, 0.3],
                subplot_titles=(f"{selected} - Preis & DCF Fair Value", "RSI (14)")
            )
            
            hist_eur = hist['Close'] / eur_usd
            fv_eur = row['Fair Value (€)']
            
            fig.add_trace(
                go.Scatter(x=hist.index, y=hist_eur, name="Kurs (€)", 
                          line=dict(color='#58a6ff', width=2)),
                row=1, col=1
            )
            
            if row['_ema200'] is not None:
                ema = ta.ema(hist['Close'], length=EMA_LENGTH) / eur_usd
                fig.add_trace(
                    go.Scatter(x=hist.index, y=ema, name="EMA 200",
                              line=dict(color='orange', width=1, dash='dash')),
                    row=1, col=1
                )

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
                          name=f"Buy Zone (±{mos_pct*100:.0f}%)"),
                row=1, col=1
            )
            fig.add_hline(y=fv_eur, line_dash="dash", line_color="#28a745",
                         annotation_text="Fair Value (DCF)", row=1, col=1)

            rsi = ta.rsi(hist['Close'], length=RSI_LENGTH)
            fig.add_trace(
                go.Scatter(x=hist.index, y=rsi, name="RSI",
                          line=dict(color='#ff7f0e', width=1.5)),
                row=2, col=1
            )
            fig.add_hline(y=40, line_dash="dot", line_color="cyan", row=2, col=1)
            fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=1)

            fig.update_layout(
                height=600,
                template="plotly_dark",
                hovermode="x unified",
                title=f"DCF Chartanalyse: {selected} (Growth {growth_rate*100:.0f}%)",
                xaxis_rangeslider_visible=False
            )
            fig.update_yaxes(title_text="Preis (€)", row=1, col=1)
            fig.update_yaxes(title_text="RSI", row=2, col=1)
            
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Keine gültigen Daten. Stelle sicher, dass Aktien FCF haben.")

except Exception as e:
    st.error(f"Fehler: {e}")
    import traceback
    st.write(traceback.format_exc())
