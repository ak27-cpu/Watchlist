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
FALLBACK_EUR_USD = 1.05
FALLBACK_KGV = 20.0
FALLBACK_EPS = 1.0

# --- 3. HILFSFUNKTIONEN ---
@st.cache_resource
def init_db():
    """Initialisiere Datenbank einmalig"""
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_market_data(ticker: str) -> tuple | None:
    """Lade maximale historische Daten (period='max')"""
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="max")
        if hist.empty:
            return None
        return hist, tk.info
    except Exception as e:
        return None

@st.cache_data(ttl=WATCHLIST_CACHE_TTL, show_spinner=False)
def get_watchlist():
    """Watchlist aus Supabase laden und 10 Minuten cachen"""
    db = init_db()
    try:
        res = db.table("watchlist").select("ticker").execute()
        tickers = sorted(list(set(t['ticker'].upper() for t in res.data)))
        return tickers
    except Exception as e:
        st.error(f"Fehler beim Laden der Watchlist: {e}")
        return []

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_eur_usd():
    """EUR/USD Kurs laden mit Fallback (Rate Limit Safe)"""
    try:
        eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
        if not eur_usd_data.empty:
            rate = float(eur_usd_data['Close'].iloc[-1])
            if 0.9 < rate < 1.2:  # Sanity check
                return rate
    except Exception:
        pass
    # Fallback: Hardcodierter Wert (aktuell ~1.055)
    return 1.055


def calculate_avg_drawdown(hist: pd.DataFrame) -> float:
    """Berechne durchschnittlichen Drawdown > 10%"""
    running_max = hist['Close'].cummax()
    drawdown = (hist['Close'] - running_max) / running_max
    significant_drawdowns = drawdown[drawdown < DRAWDOWN_THRESHOLD]
    return significant_drawdowns.mean() * 100 if not significant_drawdowns.empty else 0.0

def calculate_kgv_trend(hist: pd.DataFrame, info: dict) -> tuple[float, float]:
    """
    Berechne KGV aktuell und KGV Trend (nächster Punkt)
    - KGV aktuell: Basierend auf aktuellem Kurs und EPS
    - KGV nächster Punkt: Median der letzten 2 Jahre (Trend)
    """
    try:
        eps_ttm = info.get('trailingEps') or 1.0
        if eps_ttm <= 0:
            eps_ttm = 1.0
        
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        kgv_current = price_usd / eps_ttm
        
        # KGV Trend: Median der letzten 2 Jahre
        hist_2y = hist.tail(504)  # ~2 Jahre
        if not hist_2y.empty and len(hist_2y) > 50:
            kgv_series = hist_2y['Close'] / eps_ttm
            kgv_trend = kgv_series.median()
            
            # Validierung
            if kgv_trend <= 0 or kgv_trend > 1000:
                kgv_trend = kgv_current
        else:
            kgv_trend = kgv_current
        
        return kgv_current, kgv_trend
            
    except Exception:
        return FALLBACK_KGV, FALLBACK_KGV

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

def calculate_fair_value_dual(eps: float, fcf_per_share: float, kgv_current: float, kgv_trend: float) -> tuple[float, float, float]:
    """
    DUAL METHOD - wie Aktienfinder:
    - FV bereinigt (KGV): EPS × Mittelwert(KGV aktuell, KGV trend)
    - FV Cashflow (KCV): FCF per Share × KCV (vereinfacht: KGV * 0.9)
    - FV Gesamt: Mittelwert beider Methoden
    """
    # Methode 1: KGV-basiert
    kgv_avg = (kgv_current + kgv_trend) / 2
    fv_kgv = eps * kgv_avg
    
    # Methode 2: Cashflow-basiert (KCV ≈ KGV * 0.9)
    if fcf_per_share > 0:
        kcv = kgv_avg * 0.9
        fv_fcf = fcf_per_share * kcv
    else:
        fv_fcf = fv_kgv  # Fallback
    
    # Mittelwert
    fv_gesamt = (fv_kgv + fv_fcf) / 2
    
    return fv_kgv, fv_fcf, fv_gesamt

def calculate_technical_metrics(hist: pd.DataFrame, price_usd: float) -> dict:
    """Berechne alle technischen Metriken"""
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
    """Generiere Kauf-Signal und Ranking"""
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
st.title("💎 Equity Intelligence: Fair Value (Dual-Method)")

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
    tickers = get_watchlist()
    
    if not tickers:
        st.info("Bitte Ticker hinzufügen.")
        st.stop()
    
    eur_usd = get_eur_usd()
    all_results = []

    with st.spinner(f'Lade Marktdaten für {len(tickers)} Ticker...'):
        start_time = time.time()
        market_data_map = {}
        
        # SEQUENZIELL laden statt parallel (verhindert Rate Limiting)
        for idx, t in enumerate(tickers):
            time.sleep(0.5)  # Delay zwischen Requests
            data = get_market_data(t)
            if data:
                market_data_map[t] = data
        
        load_time = time.time() - start_time

    if not market_data_map:
        st.warning("⚠️ Keine Marktdaten geladen. Yahoo Finance blockiert wahrscheinlich die Anfragen. Bitte warte eine Minute und versuche erneut.")
        st.stop()

    col1, col2, col3 = st.columns([2, 1, 1])
    col1.info(f"✅ {len(market_data_map)}/{len(tickers)} Ticker geladen in {load_time:.2f}s")
    
    for t in tickers:
        if t not in market_data_map:
            continue

        hist, info = market_data_map[t]

        # --- BERECHNUNG ---
        eps = info.get('trailingEps') or FALLBACK_EPS
        fcf_per_share = calculate_fcf_per_share(info)
        kgv_current, kgv_trend = calculate_kgv_trend(hist, info)
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]

        # Fair Value (Dual Method)
        fv_kgv, fv_fcf, fv_gesamt = calculate_fair_value_dual(eps, fcf_per_share, kgv_current, kgv_trend)
        
        fv_eur = fv_gesamt / eur_usd
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
            "_fv_gesamt_usd": fv_gesamt,
            "_fv_kgv_usd": fv_kgv,
            "_fv_fcf_usd": fv_fcf,
            "_kgv_current": kgv_current,
            "_kgv_trend": kgv_trend,
            "_eps": eps,
            "_fcf_per_share": fcf_per_share,
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

        st.subheader("🔬 Tiefenanalyse")
        selected = st.selectbox("Ticker auswählen", df['Ticker'].values)
        
        if selected:
            row = df[df['Ticker'] == selected].iloc[0]
            hist, _ = market_data_map[selected]

            st.subheader(f"Fair-Value-Analyse: {selected} (Dual-Method)")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("EPS (TTM)", f"${row['_eps']:.2f}")
            col2.metric("KGV aktuell", f"{row['_kgv_current']:.1f}x")
            col3.metric("KGV Trend", f"{row['_kgv_trend']:.1f}x")
            col4.metric("FCF/Share", f"${row['_fcf_per_share']:.2f}")
            
            st.markdown(f"""
            **Dual-Method Berechnung:**
            
            **Methode 1 - KGV-basiert:**
            - KGV Ø = ({row['_kgv_current']:.1f} + {row['_kgv_trend']:.1f}) / 2 = **{(row['_kgv_current'] + row['_kgv_trend'])/2:.1f}x**
            - FV = ${row['_eps']:.2f} × {(row['_kgv_current'] + row['_kgv_trend'])/2:.1f} = **${row['_fv_kgv_usd']:.2f}**
            
            **Methode 2 - Cashflow-basiert:**
            - KCV = KGV Ø × 0.9 = **{(row['_kgv_current'] + row['_kgv_trend'])/2 * 0.9:.1f}**
            - FV = ${row['_fcf_per_share']:.2f} × {(row['_kgv_current'] + row['_kgv_trend'])/2 * 0.9:.1f} = **${row['_fv_fcf_usd']:.2f}**
            
            **Fair Value Gesamt (Mittelwert):**
            - (${row['_fv_kgv_usd']:.2f} + ${row['_fv_fcf_usd']:.2f}) / 2 = **${row['_fv_gesamt_usd']:.2f}** ≈ **€{row['Fair Value (€)']:.2f}**
            """)

            st.divider()

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Kurs", f"{row['_price_usd']:.2f} $", delta=f"≈ {row['Kurs (€)']:.2f} €", delta_color="off")
            col2.metric("Fair Value", f"{row['_fv_gesamt_usd']:.2f} $", delta=f"≈ {row['Fair Value (€)']:.2f} €", delta_color="off")
            
            rsi_status = "Überverkauft (<30)" if row['RSI'] < 30 else ("Kaufzone (<40)" if row['RSI'] < 40 else "Neutral")
            col3.metric("RSI (14)", f"{row['RSI']:.1f}", delta=rsi_status, delta_color="inverse")
            col4.metric("Korrektur (ATH)", f"{row['_corr_ath']:.1f}%", delta=f"Ø: {row['_avg_dd']:.1f}%", delta_color="off")
            col5.metric("Trend / Vol", f"{row['_trend']}", delta=f"{row['_vol']}")

            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.08,
                row_heights=[0.7, 0.3],
                subplot_titles=(f"{selected} - Preis & Fair Value", "RSI (14)")
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
                          name=f"Fair Value Zone (±{mos_pct*100:.0f}%)"),
                row=1, col=1
            )
            fig.add_hline(y=fv_eur, line_dash="dash", line_color="#28a745",
                         annotation_text="Fair Value", row=1, col=1)

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
        st.warning("Keine Daten verfügbar. Bitte versuche es später erneut.")

except Exception as e:
    st.error(f"Systemfehler: {e}")
    import traceback
    st.write(traceback.format_exc())
