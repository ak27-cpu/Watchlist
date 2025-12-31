import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from supabase import create_client, Client

# --- 1. SUPABASE INITIALISIERUNG ---
# Wir verzichten auf Caching, um Secret-Fehler auszuschließen
if "SUPABASE_URL" in st.secrets and "SUPABASE_KEY" in st.secrets:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(url, key)
else:
    st.error("❌ Kritischer Fehler: Secrets 'SUPABASE_URL' oder 'SUPABASE_KEY' nicht gefunden.")
    st.info("Obwohl eine andere App funktioniert, prüfe bitte, ob diese spezifische App in den Streamlit-Cloud-Settings die Secrets hinterlegt hat.")
    st.stop()

# --- 2. HILFSFUNKTIONEN FÜR DATEN ---

def get_eur_usd():
    """Holt aktuellen EUR/USD Kurs."""
    try:
        data = yf.download("EURUSD=X", period="1d", interval="1m", progress=False)
        return data['Close'].iloc[-1]
    except:
        return 1.05

def get_data(ticker, eur_usd):
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="max")
        if hist.empty: return None
        
        info = tk.info
        hist_60d = hist.tail(60).copy()
        
        # Fair Value Kalkulation (Prompt-Logik)
        fwd_eps = info.get('forwardEps') or info.get('trailingEps') or 1.0
        growth = info.get('earningsGrowth', 0.1) 
        eps_2026 = fwd_eps * (1 + growth)**2
        
        kgv_median = info.get('forwardPE') or info.get('trailingPE') or 20
        unteres_kgv = kgv_median * 0.8
        
        fv_usd = (eps_2026 * unteres_kgv + eps_2026 * kgv_median) / 2
        price_usd = info.get('currentPrice') or hist['Close'].iloc[-1]
        
        # Umrechnungen
        price_eur = price_usd / eur_usd
        fv_eur = fv_usd / eur_usd
        
        # ATH & Tranchen
        ath_usd = hist['High'].max()
        ath_eur = ath_usd / eur_usd
        corr_ath = ((price_usd - ath_usd) / ath_usd) * 100
        
        # Indikatoren
        rsi = ta.rsi(hist_60d['Close'], length=14).iloc[-1]
        adx = ta.adx(hist_60d['High'], hist_60d['Low'], hist_60d['Close'])['ADX_14'].iloc[-1]
        
        vol_now = hist_60d['Volume'].iloc[-1]
        vol_ma = hist_60d['Volume'].mean()
        vol_sig = "Buy" if vol_now > (vol_ma * 1.5) else "Sell" if vol_now < (vol_ma * 0.8) else "Hold"
        
        # Bewertung & Ranking
        upside = ((fv_usd - price_usd) / price_usd) * 100
        if upside > 10 and rsi < 40 and vol_sig == "Buy":
            bewertung, sort_idx = "🟢 KAUF", 1
        elif 0 <= upside <= 10:
            bewertung, sort_idx = "🟡 BEOBACHTEN", 2
        else:
            bewertung, sort_idx = "🔴 WARTEN", 3

        return {
            "Ticker": ticker,
            "Bewertung": bewertung,
            "Upside(%)": round(float(upside), 1),
            "Kurs(€)": round(float(price_eur), 2),
            "Fair Value(€)": round(float(fv_eur), 2),
            "Fair Value(USD)": round(float(fv_usd), 2),
            "Tranche1(-10%)": round(float(ath_eur * 0.9), 2),
            "Tranche2(-20%)": round(float(ath_eur * 0.8), 2),
            "RSI": round(float(rsi), 1),
            "Korrektur": f"{round(float(corr_ath), 1)}%",
            "Trend": "Stark" if adx > 25 else "Mittel" if adx > 20 else "Schwach",
            "Volumen": vol_sig,
            "_sort": sort_idx
        }
    except:
        return None

# --- 3. UI ---

st.title("📈 Stock Strategy Analysis Pro")

# Ticker aus Supabase laden
try:
    # Wir laden aus der Tabelle 'watchlist'
    response = supabase.from_("watchlist").select("ticker").execute()
    ticker_list = [t['ticker'].strip().upper() for t in response.data]
    
    if not ticker_list:
        st.warning("⚠️ Tabelle 'watchlist' ist leer. Bitte Ticker hinzufügen.")
    else:
        eur_usd = get_eur_usd()
        st.write(f"Wechselkurs: **1 EUR = {round(eur_usd, 4)} USD**")
        
        results = []
        with st.spinner('Analysiere Aktien...'):
            for t in ticker_list:
                res = get_data(t, eur_usd)
                if res: results.append(res)
        
        if results:
            df = pd.DataFrame(results)
            # Sortierung: KAUF oben, dann nach Upside
            df = df.sort_values(by=["_sort", "Upside(%)"], ascending=[True, False])
            
            # Anzeige
            st.subheader("Marktanalyse & Ranking")
            st.dataframe(
                df.drop(columns=["_sort"]).style.applymap(
                    lambda x: 'background-color: #d4edda' if "🟢" in str(x) else 
                              ('background-color: #fff3cd' if "🟡" in str(x) else 
                               ('background-color: #f8d7da' if "🔴" in str(x) else '')),
                    subset=['Bewertung']
                ),
                use_container_width=True, hide_index=True
            )
            
            # Grafische Gauge Charts
            st.divider()
            cols = st.columns(3)
            for i, (idx, row) in enumerate(df.iterrows()):
                with cols[i % 3]:
                    fig = go.Figure(go.Indicator(
                        mode = "gauge+number",
                        value = row['Kurs(€)'],
                        title = {'text': f"{row['Ticker']} ({row['Bewertung']})"},
                        gauge = {
                            'axis': {'range': [0, max(row['Fair Value(€)'], row['Kurs(€)']) * 1.3]},
                            'steps': [
                                {'range': [0, row['Fair Value(€)']], 'color': "#E8F5E9"},
                                {'range': [row['Fair Value(€)'], 9999], 'color': "#FFEBEE"}
                            ],
                            'threshold': {'line': {'color': "green", 'width': 4}, 'value': row['Fair Value(€)']}
                        }
                    ))
                    fig.update_layout(height=230, margin=dict(l=15,r=15,t=45,b=15))
                    st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"⚠️ Fehler beim Zugriff auf die Tabelle 'watchlist': {e}")

# Sidebar für Admin-Funktionen
with st.sidebar:
    st.header("Verwaltung")
    new_t = st.text_input("Ticker hinzufügen").upper()
    if st.button("Speichern"):
        if new_t:
            supabase.table("watchlist").insert({"ticker": new_t}).execute()
            st.success(f"{new_t} hinzugefügt!")
            st.rerun()
    
    if st.button("🔄 Alles aktualisieren"):
        st.rerun()
