import streamlit as st
from supabase import create_client
import yfinance as yf
import pandas as pd
from urllib.parse import quote

# --- 1. SETUP & SIDEBAR ---
st.set_page_config(page_title="Investment Cockpit v5", layout="wide")

with st.sidebar:
    st.header("⚙️ Strategie")
    t1_pct = st.slider("Tranche 1 Abstand (%)", 1, 30, 10)
    t2_pct = st.slider("Tranche 2 Abstand (%)", 1, 30, 15)
    st.divider()
    if st.button("🔄 Cache leeren & Refresh"):
        st.cache_data.clear()
        st.rerun()

try:
    supabase = create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
except Exception as e:
    st.error(f"Datenbank-Verbindung fehlgeschlagen: {e}")
    st.stop()

# --- 2. FINANZ-LOGIK ---
def get_backup_fv(ticker):
    """Berechnet einen konservativen FV, falls in Supabase keiner steht (0)."""
    try:
        s = yf.Ticker(ticker)
        eps = s.info.get('forwardEps', 0)
        # Sehr konservativ: KGV von 12
        return round(eps * 12, 2) if eps > 0 else 0
    except: return 0

@st.cache_data(ttl=1800)
def get_market_data(ticker):
    try:
        s = yf.Ticker(ticker)
        h = s.history(period="2y")
        if h.empty: return None
        
        cp = h['Close'].iloc[-1]
        ath = h['High'].max()
        # RSI
        delta = h['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        return {
            "Preis": round(cp, 2),
            "Korr_ATH": round(((cp / ath) - 1) * 100, 1),
            "RSI": round(rsi, 1)
        }
    except: return None

# --- 3. MARKT-HEADER (VIX / F&G) ---
st.title("🏛️ Professional Investment Cockpit")
try:
    vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
    spy = yf.Ticker("^GSPC").history(period="300d")
    sma125 = spy['Close'].rolling(125).mean().iloc[-1]
    fg = min(100, int((spy['Close'].iloc[-1] / sma125) * 50))
    
    c1, c2 = st.columns(2)
    c1.metric("VIX Index (Angst)", f"{vix:.2f}")
    c2.metric("Fear & Greed Index", f"{fg}/100")
except:
    st.warning("Markt-Indikatoren konnten nicht geladen werden.")
st.divider()

# --- 4. HAUPT-LOGIK (WATCHLIST) ---
res = supabase.table("watchlist").select("*").execute()
db_data = res.data

if db_data:
    final_list = []
    with st.spinner("Aktualisiere Kurse..."):
        for item in db_data:
            t = item['ticker']
            m = get_market_data(t)
            
            if m:
                # Fair Value Logik: Supabase Wert bevorzugen, sonst Backup
                manual_fv = float(item.get('fair_value', 0) or 0)
                fv = manual_fv if manual_fv > 0 else get_backup_fv(t)
                
                # Tranchen vom FV
                t1 = fv * (1 - t1_pct/100) if fv > 0 else 0
                t2 = fv * (1 - t2_pct/100) if fv > 0 else 0
                
                # STRENGE BEWERTUNG
                diff_fv = ((m['Preis'] / fv) - 1) * 100 if fv > 0 else 0
                
                # Kriterien für KAUFEN: RSI < 38 UND Korrektur > 10% UND Preis nah am FV (+/- 10%)
                is_rsi_low = m['RSI'] < 38
                is_near_fv = -10 <= diff_fv <= 10
                is_deep_corr = m['Korr_ATH'] <= -10
                
                if is_rsi_low and is_near_fv and is_deep_corr:
                    status, priority = "TOP CHANCE 💎", 1
                elif is_near_fv:
                    status, priority = "FAIR BEREICH 🟡", 2
                else:
                    status, priority = "WARTEN ⚪", 3
                
                final_list.append({
                    "Priority": priority,
                    "Ticker": t,
                    "Preis": m['Preis'],
                    "RSI": m['RSI'],
                    "Fair Value": fv,
                    "FV-Diff %": round(diff_fv, 1),
                    "Korr. %": m['Korr_ATH'],
                    "Tranche 1": round(t1, 2),
                    "Tranche 2": round(t2, 2),
                    "Empfehlung": status
                })

    if final_list:
        df = pd.DataFrame(final_list).sort_values(by=["Priority", "FV-Diff %"])
        
        # Tabelle anzeigen
        st.dataframe(
            df.drop(columns=["Priority"]).style.apply(
                lambda x: ['background-color: #042f04' if "💎" in str(x.Empfehlung) else '' for i in x], axis=1
            ), use_container_width=True, hide_index=True
        )

        # --- 5. FEHLERFREIER DEEP-DIVE ---
        st.divider()
        sel = st.selectbox("Aktie für Deep-Dive wählen:", df['Ticker'].tolist())
        r = df[df['Ticker'] == sel].iloc[0]
        
        # Prompt-Erstellung mit exakt passenden Spaltennamen
        p_prompt = f"""Analysiere {sel}:
1. Preis {r['Preis']} vs Fair Value {r['Fair Value']} (Abstand {r['FV-Diff %']}%).
2. RSI {r['RSI']} und Korrektur {r['Korr. %']}%.
Ist das ein kluger Value-Einstieg?"""

        st.link_button(f"🚀 {sel} auf Perplexity prüfen", f"https://www.perplexity.ai/?q={quote(p_prompt)}", use_container_width=True)

else:
    st.info("Deine Watchlist ist leer. Füge Ticker in Supabase hinzu.")
