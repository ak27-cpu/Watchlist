import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- SUPABASE CONNECT ---
def init_db():
    if "SUPABASE_URL" not in st.secrets or "SUPABASE_KEY" not in st.secrets:
        st.error("❌ Secrets fehlen in den Cloud-Settings!")
        st.stop()
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = init_db()

# --- ANALYSIS ENGINE ---
def get_stock_metrics(ticker, eur_usd):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="max")
        if hist.empty: return None
        
        info = tk.info
        
        # 1. BASIS DATEN
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('earningsGrowth') or 0.10
        kgv_median = info.get('forwardPE') or 20
        
        # 2. VARIANTE A: KGV METHODE (EPS 2026)
        eps_2026 = fwd_eps * (1 + growth)**2
        fv_kgv_neutral = eps_2026 * kgv_median
        fv_kgv_konservativ = eps_2026 * (kgv_median * 0.8)
        
        # 3. VARIANTE B: DDM METHODE (Dividend Discount Model)
        # Formel: D1 / (k - g) 
        # k = Erwartete Rendite (ca. 9%), g = Dividendenwachstum
        div_rate = info.get('dividendRate') or 0
        div_yield = info.get('dividendYield') or 0
        
        if div_rate > 0:
            k = 0.09 # Erwartete Marktrendite 9%
            # Wir nehmen das EPS-Wachstum als Proxy für Dividendenwachstum, gedeckelt auf 7% für Stabilität
            g = min(growth, 0.07) 
            if k > g:
                fv_ddm = (div_rate * (1 + g)) / (k - g)
            else:
                fv_ddm = div_rate / 0.03 # Fallback
        else:
            fv_ddm = 0 # Keine Dividende, kein DDM möglich

        # 4. KOMBINIERTER FAIR VALUE (Durchschnitt aus KGV & DDM falls vorhanden)
        if fv_ddm > 0:
            fv_final_usd = (fv_kgv_neutral + fv_ddm) / 2
        else:
            fv_final_usd = fv_kgv_neutral

        # Umrechnungen & Metriken
        price_eur = price_usd / eur_usd
        fv_eur = fv_final_usd / eur_usd
        ath_eur = hist['High'].max() / eur_usd
        rsi = ta.rsi(hist['Close'].tail(60), length=14).iloc[-1]
        
        upside = ((fv_final_usd - price_usd) / price_usd) * 100
        
        # Bewertung & Rank
        if upside > 10 and rsi < 45:
            bewertung, rank = "🟢 KAUF", 1
        elif 0 <= upside <= 10:
            bewertung, rank = "🟡 BEOBACHTEN", 2
        else:
            bewertung, rank = "🔴 WARTEN", 3

        return {
            "Ticker": ticker,
            "Bewertung": bewertung,
            "Kurs(€)": round(price_eur, 2),
            "FV KGV(€)": round(fv_kgv_neutral / eur_usd, 2),
            "FV DDM(€)": round(fv_ddm / eur_usd, 2) if fv_ddm > 0 else "N/A",
            "Fair Value(€)": round(fv_eur, 2),
            "Upside(%)": round(upside, 1),
            "RSI(14)": round(rsi, 1),
            "Korr. vs ATH": f"{round(((price_eur-ath_eur)/ath_eur)*100, 1)}%",
            "_rank": rank
        }
    except Exception as e:
        return None

# --- UI ---
st.title("📈 Multi-Modell Strategie (KGV + DDM)")

try:
    res = supabase.table("watchlist").select("ticker").execute()
    ticker_list = [t['ticker'].upper() for t in res.data]
    
    if ticker_list:
        with st.spinner('Marktdaten werden geladen...'):
            eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
            eur_usd = float(eur_usd_data['Close'].iloc[-1])
            
            all_results = []
            for t in ticker_list:
                data = get_stock_metrics(t, eur_usd)
                if data: all_results.append(data)
        
        if all_results:
            df = pd.DataFrame(all_results).sort_values(by=["_rank", "Upside(%)"], ascending=[True, False])
            
            # Tabellendarstellung
            st.dataframe(
                df.drop(columns=["_rank"]).style.applymap(
                    lambda x: 'background-color: #d4edda' if "🟢" in str(x) else 
                              ('background-color: #f8d7da' if "🔴" in str(x) else ''),
                    subset=['Bewertung']
                ), 
                use_container_width=True, hide_index=True
            )
            
            # Gauge Charts
            st.divider()
            cols = st.columns(3)
            for i, (idx, row) in enumerate(df.iterrows()):
                with cols[i % 3]:
                    # Wir zeigen den kombinierten Fair Value im Chart
                    fig = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=row['Kurs(€)'],
                        title={'text': f"{row['Ticker']} (KGV+DDM)"},
                        gauge={
                            'axis': {'range': [0, float(row['Fair Value(€)']) * 1.5]},
                            'threshold': {'line': {'color': "green", 'width': 4}, 'value': float(row['Fair Value(€)'])}
                        }
                    ))
                    fig.update_layout(height=230)
                    st.plotly_chart(fig, use_container_width=True)
except Exception as e:
    st.error(f"Fehler: {e}")

with st.sidebar:
    if st.button("🔄 Refresh"):
        st.rerun()
