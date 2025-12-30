import streamlit as st
from supabase import create_client
import yfinance as yf
import pandas as pd
from urllib.parse import quote

# --- 1. SETUP & KONFIGURATION ---
st.set_page_config(page_title="Pure Strategy Cockpit", layout="wide")

# CSS Hack für saubere Tabellen-Anzeige ohne Index
st.markdown("""
<style>
thead tr th:first-child {display:none}
tbody tr td:first-child {display:none}
</style>
""", unsafe_allow_html=True)

try:
    supabase = create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
except Exception as e:
    st.error(f"Datenbank-Fehler: {e}")
    st.stop()

# --- 2. MARKT-DATEN & LOGIK ---

def get_market_indicators():
    """Holt VIX und berechnet Fear & Greed Proxy."""
    try:
        vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
        spy = yf.Ticker("^GSPC").history(period="300d")
        cp = spy['Close'].iloc[-1]
        sma125 = spy['Close'].rolling(125).mean().iloc[-1]
        fg_score = int((cp / sma125) * 50)
        return round(vix, 2), min(100, fg_score)
    except:
        return 20.00, 50

@st.cache_data(ttl=1800)
def get_stock_metrics(ticker):
    """Holt technische Daten und berechnet Strategie-Kennzahlen (v27 Logik)."""
    try:
        s = yf.Ticker(ticker)
        h = s.history(period="3y")
        if h.empty: return None
        
        info = s.info
        cp = h['Close'].iloc[-1]
        ath = h['High'].max()
        sma200 = h['Close'].rolling(200).mean().iloc[-1]

        # 1. Korrektur-Berechnung (Drawdown)
        roll_max = h['High'].cummax()
        avg_dd = ((h['Low'] - roll_max) / roll_max).mean() * 100
        curr_dd = ((cp / ath) - 1) * 100

        # 2. RSI (14)
        delta = h['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]

        # 3. Volumen & Trend
        vol_change = (h['Volume'].iloc[-1] / h['Volume'].tail(20).mean())
        trend_dist = ((cp / sma200) - 1) * 100
        
        vol_info = "Normal"
        if vol_change > 1.5:
            vol_info = "⚠️ LONG-Druck" if (cp - h['Close'].iloc[-2]) > 0 else "⚠️ SHORT-Druck"

        return {
            "Name": info.get('longName', ticker),
            "Sektor": info.get('sector', 'N/A'),
            "Preis": round(cp, 2),
            "ATH": round(ath, 2),
            "Korr_Akt": round(curr_dd, 2),
            "Korr_Avg": round(avg_dd, 2),
            "RSI": round(rsi, 2),
            "Trend": "Bull 📈" if cp > sma200 else "Bear 📉",
            "Trend_Dist": round(trend_dist, 2),
            "Vol_Info": vol_info
        }
    except: return None

# --- 3. UI HEADER ---
vix, fg = get_market_indicators()
st.title("🏛️ Pure Strategy Cockpit")

c1, c2 = st.columns(2)
c1.metric("VIX (Angst)", f"{vix:.2f}", delta="Volatil" if vix > 22 else "Ruhig", delta_color="inverse")
c2.metric("Fear & Greed", f"{fg}/100", delta="Gier" if fg > 55 else "Angst")
st.divider()

# --- 4. DATA PROCESSING ---
res = supabase.table("watchlist").select("*").execute()
df_db = pd.DataFrame(res.data)

if not df_db.empty:
    m_data = []
    with st.spinner("Berechne Strategie-Werte..."):
        for _, r in df_db.iterrows():
            m = get_stock_metrics(r['ticker'])
            if m:
                # FV aus Datenbank holen (nur für interne Strategie-Logik!)
                fv_db = float(r.get('fair_value', 0) or 0)
                
                # Tranchen-Berechnung nach v27 (Basierend auf ATH)
                t1 = m['ATH'] * 0.90
                t2 = m['ATH'] * 0.80

                # Strategie-Score (v27 Logik)
                # 1 Punkt: RSI < 35
                # 1 Punkt: Aktuelle Korrektur tiefer als Durchschnitt
                # 1 Punkt: Preis unter dem manuellen FV (falls vorhanden)
                score = 0
                if m['RSI'] < 35: score += 1
                if m['Korr_Akt'] < m['Korr_Avg']: score += 1
                if fv_db > 0 and m['Preis'] <= fv_db: score += 1

                rating = "KAUFEN 🟢" if score >= 2 else "BEOBACHTEN 🟡" if score == 1 else "WARTEN ⚪"

                m_data.append({
                    **m, 
                    "Ticker": r['ticker'], 
                    "DB_FV": round(fv_db, 2), # Wird angezeigt, aber nicht an KI gesendet
                    "T1 (ATH -10%)": round(t1, 2), 
                    "T2 (ATH -20%)": round(t2, 2), 
                    "Empfehlung": rating
                })

    if m_data:
        df = pd.DataFrame(m_data)

        # Tabs
        tab1, tab2, tab3 = st.tabs(["📊 Markt & FV", "🎯 Technik Details", "🚀 Signale & Tranchen"])
        
        with tab1:
            st.dataframe(
                df[["Ticker", "Name", "Sektor", "Preis", "DB_FV"]], 
                use_container_width=True, hide_index=True
            )

        with tab2:
            st.dataframe(
                df[["Ticker", "Korr_Akt", "Korr_Avg", "RSI", "Trend", "Trend_Dist", "Vol_Info"]], 
                use_container_width=True, hide_index=True
            )

        with tab3:
            # Styling für die Empfehlung
            st.dataframe(
                df[["Ticker", "Preis", "T1 (ATH -10%)", "T2 (ATH -20%)", "Empfehlung"]].style.apply(
                    lambda x: ['background-color: #004d00' if "🟢" in str(x.Empfehlung) else '' for i in x], axis=1
                ), 
                use_container_width=True, hide_index=True
            )

        # --- 5. PERPLEXITY PRO (AUTONOM) ---
        st.divider()
        st.subheader("🔍 KI-Analyse (Unvoreingenommen)")
        
        sel = st.selectbox("Aktie wählen:", df['Ticker'].tolist())
        d = next(item for item in m_data if item["Ticker"] == sel)
        
        # Bereinigter Prompt: Keine Vorgabe des FV, KI muss selbst bewerten
        perp_prompt = f"""Du bist ein Finanzanalyst. Erstelle eine fundierte Analyse zu {sel} ({d['Name']}).
        
Aktuelle Marktdaten:
- Kurs: {d['Preis']}
- RSI (14): {d['RSI']}
- Drawdown vom Hoch: {d['Korr_Akt']}% (Historischer Schnitt: {d['Korr_Avg']}%)

Deine Aufgaben (Recherchiere alle fehlenden Daten selbstständig):
1. Was waren die wichtigsten News/Events der letzten 10 Tage?
2. Wie bewertest du den Sektor aktuell?
3. Führe eine unabhängige Bewertung durch (KGV, Cashflow): Was ist dein fairer Wert?
4. Technische Einschätzung basierend auf RSI und Drawdown.
5. Fazit: Ist die Aktie auf dem aktuellen Niveau ein Kauf?
"""

        url = f"https://www.perplexity.ai/?q={quote(perp_prompt)}"
        st.link_button(f"🚀 {sel} Analyse auf Perplexity starten", url, use_container_width=True)

with st.sidebar:
    st.markdown("### ⚙️ Steuerung")
    if st.button("🔄 Daten neu laden"):
        st.cache_data.clear()
        st.rerun()
