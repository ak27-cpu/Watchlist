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
        # KEINE manuelle Session mehr setzen, yfinance macht das jetzt selbst
        tk = yf.Ticker(ticker)
        
        # Basisdaten laden
        hist = tk.history(period="max")
        if hist.empty:
            st.warning(f"Keine Historie für {ticker} gefunden.")
            return None
            
        info = tk.info
        # Fallback-Logik für fehlende Info-Daten
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('earningsGrowth') or 0.1
        kgv_median = info.get('forwardPE') or 20
        
        # Fair Value Kalkulation (Schritt 1-4)
        eps_2026 = fwd_eps * (1 + growth)**2
        # Unteres KGV (-20%) und Oberes KGV (Median)
        fv_usd = (eps_2026 * (kgv_median * 0.8) + eps_2026 * kgv_median) / 2
        
        price_eur = price_usd / eur_usd
        fv_eur = fv_usd / eur_usd
        
        # ATH & RSI
        ath_eur = hist['High'].max() / eur_usd
        rsi = ta.rsi(hist['Close'].tail(60), length=14).iloc[-1]
        
        # Volumen Signal
        vol_now = hist['Volume'].iloc[-1]
        vol_ma = hist['Volume'].tail(20).mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Hold"
        
        upside = ((fv_eur - price_eur) / price_eur) * 100
        
        # Bewertung & Rank für Sortierung
        if upside > 10 and rsi < 40 and vol_sig == "Buy":
            bewertung, rank = "🟢 KAUF", 1
        elif 0 <= upside <= 10:
            bewertung, rank = "🟡 BEOBACHTEN", 2
        else:
            bewertung, rank = "🔴 WARTEN", 3

        return {
            "Ticker": ticker,
            "Bewertung": bewertung,
            "Kurs(€)": round(price_eur, 2),
            "Fair Value($)": round(fv_usd, 2),
            "Fair Value(€)": round(fv_eur, 2),
            "Upside(%)": round(upside, 1),
            "Tranche1(-10%)": round(ath_eur * 0.9, 2),
            "Tranche2(-20%)": round(ath_eur * 0.8, 2),
            "RSI(14)": round(rsi, 1),
            "Korr. vs ATH": f"{round(((price_eur-ath_eur)/ath_eur)*100, 1)}%",
            "Volumen": vol_sig,
            "_rank": rank
        }
    except Exception as e:
        st.error(f"Fehler bei Ticker {ticker}: {e}")
        return None

# --- UI ---
st.title("📈 Watchlist Strategy App")

try:
    # 1. Ticker aus Supabase laden
    res = supabase.table("watchlist").select("ticker").execute()
    ticker_list = [t['ticker'].upper() for t in res.data]
    
    if ticker_list:
        # 2. EUR/USD Kurs holen
        with st.spinner('Hole Wechselkurs...'):
            eur_usd_data = yf.download("EURUSD=X", period="1d", progress=False)
            eur_usd = float(eur_usd_data['Close'].iloc[-1])

        # 3. Aktien analysieren
        all_results = []
        with st.spinner('Analysiere Aktien...'):
            for t in ticker_list:
                data = get_stock_metrics(t, eur_usd)
                if data:
                    all_results.append(data)
        
        # 4. Tabelle anzeigen
        if all_results:
            df = pd.DataFrame(all_results).sort_values(by=["_rank", "Upside(%)"], ascending=[True, False])
            
            st.subheader("Analyse Übersicht")
            st.dataframe(
                df.drop(columns=["_rank"]).style.applymap(
                    lambda x: 'background-color: #d4edda' if "🟢" in str(x) else 
                              ('background-color: #f8d7da' if "🔴" in str(x) else ''),
                    subset=['Bewertung']
                ), 
                use_container_width=True, hide_index=True
            )
            
            # Gauge Charts zur Visualisierung
            st.divider()
            cols = st.columns(3)
            for i, (idx, row) in enumerate(df.iterrows()):
                with cols[i % 3]:
                    fig = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=row['Kurs(€)'],
                        title={'text': f"{row['Ticker']} ({row['Bewertung']})"},
                        gauge={
                            'axis': {'range': [0, max(row['Fair Value(€)'], row['Kurs(€)']) * 1.5]},
                            'threshold': {'line': {'color': "green", 'width': 4}, 'value': row['Fair Value(€)']}
                        }
                    ))
                    fig.update_layout(height=230)
                    st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("Keine Daten berechenbar. Prüfe die Ticker!")
    else:
        st.info("Datenbank ist leer.")

except Exception as e:
    st.error(f"Hauptfehler: {e}")

with st.sidebar:
    if st.button("🔄 Aktualisieren"):
        st.rerun()
