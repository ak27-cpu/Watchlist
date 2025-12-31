import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from supabase import create_client, Client
import time
from datetime import datetime

st.set_page_config(page_title="Equity Intelligence Pro", layout="wide")
st.markdown("""
    <style>
    .stMetric { background-color: #1e2130; padding: 10px; border-radius: 8px; border: 1px solid #30363d; }
    .main { background-color: #0e1117; }
    [data-testid="stMetricDelta"] { color: #aaaaaa !important; }
    </style>
    """, unsafe_allow_html=True)

CACHE_TTL = 3600
WATCHLIST_CACHE_TTL = 600
RSI_LENGTH = 14
EMA_LENGTH = 200
VOLUME_THRESHOLD_UP = 1.5
VOLUME_THRESHOLD_DOWN = 0.8
DRAWDOWN_THRESHOLD = -0.10
FALLBACK_EUR_USD = 1.055
WACC = 0.0989
TERMINAL_GROWTH = 0.025

@st.cache_resource
def init_db():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_market_data(ticker: str) -> tuple | None:
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
    db = init_db()
    try:
        res = db.table("watchlist").select("ticker").execute()
        tickers = sorted(list(set(t['ticker'].upper() for t in res.data)))
        return tickers
    except Exception:
        return []

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_eur_usd():
    try:
        eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
        if not eur_usd_data.empty:
            rate = float(eur_usd_data['Close'].iloc[-1])
            if 0.9 < rate < 1.2:
                return rate
    except Exception:
        pass
    return FALLBACK_EUR_USD

def get_fair_value_from_db(ticker: str) -> dict | None:
    """Lade Fair Value aus Datenbank"""
    db = init_db()
    try:
        res = db.table("fair_values").select("*").eq("ticker", ticker).execute()
        if res.data:
            return res.data[0]
        return None
    except Exception:
        return None

def save_fair_value_to_db(ticker: str, fv_usd: float, fv_eur: float, source: str = "manual"):
    """Speichere Fair Value in Datenbank"""
    db = init_db()
    try:
        existing = get_fair_value_from_db(ticker)
        data = {
            "ticker": ticker,
            "fair_value_usd": fv_usd,
            "fair_value_eur": fv_eur,
            "source": source,
            "updated_at": datetime.now().isoformat()
        }
        if existing:
            db.table("fair_values").update(data).eq("ticker", ticker).execute()
            return "✅ Fair Value aktualisiert"
        else:
            db.table("fair_values").insert(data).execute()
            return "✅ Fair Value gespeichert"
    except Exception as e:
        return f"❌ Fehler: {str(e)}"

def calculate_dcf_fair_value_eps(eps: float, growth_rate: float = 0.10) -> dict:
    """
    DCF-Bewertung basierend auf EPS (wie Aktienfinder)
    """
    
    if eps <= 0:
        return {"fv": 0, "pv_10y": 0, "pv_terminal": 0, "error": True}
    
    try:
        if WACC <= growth_rate:
            fv = eps * 35
            return {"fv": fv, "pv_10y": fv * 0.7, "pv_terminal": fv * 0.3, "method": "Fallback"}
        
        pv_10y = 0
        
        for year in range(1, 11):
            eps_projected = eps * ((1 + growth_rate) ** year)
            pv = eps_projected / ((1 + WACC) ** year)
            pv_10y += pv
        
        eps_year10 = eps * ((1 + growth_rate) ** 10)
        terminal_multiple = (1 + TERMINAL_GROWTH) / (WACC - TERMINAL_GROWTH)
        terminal_value = eps_year10 * terminal_multiple
        pv_terminal = terminal_value / ((1 + WACC) ** 10)
        
        fv = pv_10y + pv_terminal
        
        return {
            "fv": fv,
            "pv_10y": pv_10y,
            "pv_terminal": pv_terminal,
            "eps_year10": eps_year10,
            "terminal_multiple": terminal_multiple,
            "error": False,
            "method": "DCF 10-Year EPS + Terminal"
        }
    
    except Exception as e:
        return {"fv": 0, "error": True, "error_msg": str(e)}

def calculate_avg_drawdown(hist: pd.DataFrame) -> float:
    running_max = hist['Close'].cummax()
    drawdown = (hist['Close'] - running_max) / running_max
    significant_drawdowns = drawdown[drawdown < DRAWDOWN_THRESHOLD]
    return significant_drawdowns.mean() * 100 if not significant_drawdowns.empty else 0.0

def calculate_technical_metrics(hist: pd.DataFrame, price_usd: float) -> dict:
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
    buy_limit = fv_eur * (1 - mos_pct)
    watch_limit = fv_eur * (1 + mos_pct)
    
    if price_eur <= buy_limit and rsi < 40:
        return "🟢 KAUF", 1
    elif price_eur <= watch_limit:
        return "🟡 BEOBACHTEN", 2
    else:
        return "🔴 WARTEN", 3

db = init_db()
st.title("💎 Equity Intelligence: DCF Fair Value (Buffett-Style, EPS-basiert)")

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
    st.metric("Bewertung", "EPS-basiert (wie Aktienfinder)")
    
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
        
        eps = info.get('trailingEps') or info.get('forwardEps') or 1.0
        dcf_result = calculate_dcf_fair_value_eps(eps, growth_rate=growth_rate)
        
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        fv_usd = dcf_result.get('fv', 0)
        
        # Prüfe ob manueller Fair Value in DB existiert
        db_fv = get_fair_value_from_db(t)
        if db_fv:
            fv_usd = db_fv.get('fair_value_usd', fv_usd)
        
        if fv_usd <= 0:
            continue
        
        fv_eur = fv_usd / eur_usd
        price_eur = price_usd / eur_usd
        upside = ((fv_eur - price_eur) / price_eur) * 100

        tech_metrics = calculate_technical_metrics(hist, price_usd)
        rsi_now = tech_metrics['rsi']
        signal, rank = generate_signal(price_eur, fv_eur, rsi_now, mos_pct)

        all_results.append({
            "Ticker": t,
            "Kurs_EUR": price_eur,
            "Fair_Value_EUR": fv_eur,
            "Upside_PCT": upside,
            "RSI": rsi_now,
            "Signal": signal,
            "_price_usd": price_usd,
            "_fv_usd": fv_usd,
            "_eps": eps,
            "_pv_10y": dcf_result.get('pv_10y', 0),
            "_pv_terminal": dcf_result.get('pv_terminal', 0),
            "_eps_year10": dcf_result.get('eps_year10', 0),
            "_terminal_multiple": dcf_result.get('terminal_multiple', 0),
            "_corr_ath": tech_metrics['corr_ath'],
            "_avg_dd": tech_metrics['avg_dd'],
            "_trend": tech_metrics['trend'],
            "_vol": tech_metrics['volume'],
            "_rank": rank,
            "_ema200": tech_metrics['ema200'],
            "_ath": tech_metrics['ath']
        })

    if all_results:
        df = pd.DataFrame(all_results).sort_values(["_rank", "Upside_PCT"], ascending=[True, False])

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
            df[["Ticker", "Kurs_EUR", "Fair_Value_EUR", "Upside_PCT", "RSI", "Signal"]]
            .style.apply(highlight_rows, axis=1)
            .format({"Kurs_EUR": "{:.2f}", "Fair_Value_EUR": "{:.2f}", "Upside_PCT": "{:.1f}", "RSI": "{:.1f}"}),
            use_container_width=True,
            hide_index=True
        )

        st.divider()

        st.subheader("🔬 DCF-Tiefenanalyse")
        selected = st.selectbox("Ticker auswählen", df['Ticker'].values)
        
        if selected:
            row = df[df['Ticker'] == selected].iloc[0]
            hist, info = market_data_map[selected]

            st.subheader(f"DCF Fair Value Analyse: {selected}")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("EPS (TTM)", f"${row['_eps']:.2f}")
            col2.metric("PV (10 Jahre)", f"${row['_pv_10y']:.2f}")
            col3.metric("PV Terminal", f"${row['_pv_terminal']:.2f}")
            col4.metric("Fair Value", f"${row['_fv_usd']:.2f}")
            
            st.divider()
            
            # === MANUELLER FAIR VALUE EDIT ===
            st.subheader("✏️ Fair Value Editor")
            col_fv1, col_fv2 = st.columns([3, 1])
            
            with col_fv1:
                new_fv_usd = st.number_input(
                    f"Fair Value ({selected}) in USD anpassen:",
                    value=row['_fv_usd'],
                    step=1.0,
                    min_value=0.1
                )
            
            with col_fv2:
                if st.button("💾 Speichern", key=f"save_fv_{selected}"):
                    new_fv_eur = new_fv_usd / eur_usd
                    msg = save_fair_value_to_db(selected, new_fv_usd, new_fv_eur, source="manual")
                    st.success(msg)
                    st.cache_data.clear()
                    st.rerun()
            
            st.divider()
            
            # === ENTRY TRANCHES ===
            st.subheader("🎯 Entry Tranches (basierend auf ATH)")
            
            ath_price = row['_ath']
            current_price_usd = row['_price_usd']
            
            col_tranche1, col_tranche2, col_tranche3 = st.columns([2, 2, 2])
            
            with col_tranche1:
                st.markdown("**Tranche 1:**")
                tranche1_pct = st.slider(
                    "Abschlag von ATH (%)",
                    min_value=1, max_value=50, value=10, step=1, key=f"t1_{selected}"
                )
                tranche1_price = ath_price * (1 - tranche1_pct / 100)
                tranche1_eur = tranche1_price / eur_usd
                st.metric(
                    f"Einstieg -{tranche1_pct}% vom ATH",
                    f"${tranche1_price:.2f}",
                    delta=f"≈ €{tranche1_eur:.2f}"
                )
            
            with col_tranche2:
                st.markdown("**Tranche 2:**")
                tranche2_pct = st.slider(
                    "Abschlag von ATH (%)",
                    min_value=1, max_value=50, value=20, step=1, key=f"t2_{selected}"
                )
                tranche2_price = ath_price * (1 - tranche2_pct / 100)
                tranche2_eur = tranche2_price / eur_usd
                st.metric(
                    f"Einstieg -{tranche2_pct}% vom ATH",
                    f"${tranche2_price:.2f}",
                    delta=f"≈ €{tranche2_eur:.2f}"
                )
            
            with col_tranche3:
                st.markdown("**Aktuell:**")
                current_from_ath = ((current_price_usd - ath_price) / ath_price) * 100
                st.metric(
                    "Korrektur vom ATH",
                    f"{current_from_ath:.1f}%",
                    delta=f"${current_price_usd:.2f}"
                )
            
            st.info(f"📊 All-Time-High: ${ath_price:.2f} (≈ €{ath_price/eur_usd:.2f})")
            
            st.divider()
            
            eps_val = row['_eps']
            fv_usd_val = row['_fv_usd']
            fv_eur_val = row['Fair_Value_EUR']
            kurs_eur_val = row['Kurs_EUR']
            upside_val = row['Upside_PCT']
            eps_y10 = row['_eps_year10']
            term_mult = row['_terminal_multiple']
            
            st.markdown(f"""
            **DCF-Berechnung (EPS-basiert, Buffett-Style):**
            
            **Annahmen:**
            - EPS (Trailing Twelve Months): **${eps_val:.2f}**
            - Wachstum 10 Jahre: **{growth_rate*100:.0f}%**
            - WACC (Discount Rate): **{WACC*100:.2f}%**
            - Terminal Growth: **{TERMINAL_GROWTH*100:.1f}%**
            
            **Bewertung:**
            - PV (EPS Flows Jahre 1-10): **${row['_pv_10y']:.2f}**
            - PV (Terminal Value): **${row['_pv_terminal']:.2f}**
            - EPS Jahr 10: **${eps_y10:.2f}**
            - Terminal Multiple: **{term_mult:.1f}x**
            
            **Fair Value Gesamt: ${fv_usd_val:.2f} ≈ €{fv_eur_val:.2f}**
            
            **Einschätzung:**
            - Aktueller Kurs: **€{kurs_eur_val:.2f}**
            - Upside: **{upside_val:.1f}%**
            - Signal: **{row['Signal']}**
            """)

            st.divider()

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Kurs", f"{row['_price_usd']:.2f} $", delta=f"≈ {row['Kurs_EUR']:.2f} EUR", delta_color="off")
            col2.metric("Fair Value", f"{row['_fv_usd']:.2f} $", delta=f"≈ {row['Fair_Value_EUR']:.2f} EUR", delta_color="off")
            col3.metric("RSI (14)", f"{row['RSI']:.1f}", delta="Buy Zone" if row['RSI'] < 40 else "Neutral", delta_color="inverse")
            col4.metric("Korrektur", f"{row['_corr_ath']:.1f}%", delta=f"Avg: {row['_avg_dd']:.1f}%", delta_color="off")
            col5.metric("Trend", f"{row['_trend']}", delta=f"{row['_vol']}")

            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.08,
                row_heights=[0.7, 0.3],
                subplot_titles=(f"{selected} - Preis & DCF Fair Value + Tranchen", "RSI (14)")
            )
            
            hist_eur = hist['Close'] / eur_usd
            fv_eur = row['Fair_Value_EUR']
            
            fig.add_trace(
                go.Scatter(x=hist.index, y=hist_eur, name="Kurs (EUR)", 
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
            
            # Tranchen hinzufügen
            fig.add_hline(y=tranche1_eur, line_dash="dot", line_color="#ffa500",
                         annotation_text=f"Tranche 1 (-{tranche1_pct}%)", row=1, col=1)
            fig.add_hline(y=tranche2_eur, line_dash="dot", line_color="#ff0000",
                         annotation_text=f"Tranche 2 (-{tranche2_pct}%)", row=1, col=1)

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
                title=f"DCF Chartanalyse: {selected} (Growth {growth_rate*100:.0f}%, EPS-basiert)",
                xaxis_rangeslider_visible=False
            )
            fig.update_yaxes(title_text="Preis (EUR)", row=1, col=1)
            fig.update_yaxes(title_text="RSI", row=2, col=1)
            
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Keine gültigen Daten. Stelle sicher, dass Aktien EPS haben.")

except Exception as e:
    st.error(f"Fehler: {e}")
    import traceback
    st.write(traceback.format_exc())
