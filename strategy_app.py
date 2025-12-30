import streamlit as st
from supabase import create_client
import yfinance as yf
import pandas as pd
from urllib.parse import quote

# --- 1. SETUP & SIDEBAR ---
st.set_page_config(page_title="FairValue Watchlist v4.2 - Strict Mode", layout="wide")

with st.sidebar:
    st.header("⚙️ Strategie-Einstellungen")
    t1_pct = st.slider("Tranche 1 Abstand (%)", 1, 30, 10)
    t2_pct = st.slider("Tranche 2 Abstand (%)", 1, 30, 15)
    st.divider()
    st.info("💡 Strict Mode: 15% Sicherheitsmarge & KGV-Deckel (max. 20) sind aktiv.")
    if st.button("🔄 Daten neu laden"):
        st.cache_data.clear()
        st.rerun()

try:
    supabase = create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
except Exception as e:
    st.error(f"Supabase Fehler: {e}")
    st.stop()

# --- 2. VERSCHÄRFTE FAIR VALUE LOGIK ---
def calculate_strict_fv(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        cp = info.get('currentPrice', 0)
        eps = info.get('forwardEps', 0)
        
        # Wachstum vorsichtig schätzen (halbiert)
        growth = info.get('earningsGrowth', 0.05) * 0.5 
        fcf = info.get('freeCashflow', 0)
        shares = info.get('sharesOutstanding', 1)
        
        # A: Graham Formel mit KGV-Deckel bei 20
        # FV = EPS * (Basis 8.5 + 2 * g)
        pe_multiplier = min(20, (8.5 + 2 * (growth * 100)))
        fv_graham = eps * pe_multiplier if eps > 0 else 0
        
        # B: Cashflow Basis mit Multiplikator-Deckel bei 15
        fv_fcf = (fcf / shares) * 15 if fcf and shares else 0
        
        # Kombinierter Wert
        if fv_graham > 0 and fv_fcf > 0:
            raw_fv = (fv_graham * 0.5) + (fv_fcf * 0.5)
        else:
            raw_fv = max(fv_graham, fv_fcf)
            
        # Sicherheitsmarge (Margin of Safety) von 15% abziehen
        strict_fv = raw_fv * 0.85
            
        return round(strict_fv, 2) if strict_fv > 0 else round(cp * 0.7, 2)
    except:
        return 0

@st.cache_data(ttl=3600)
def get_extended_data(ticker):
    try:
        s = yf.Ticker(ticker)
        h = s.history(period="3y")
        if h.empty: return None
        
        cp = h['Close'].iloc[-1]
        ath = h['High'].max()
        curr_corr = ((cp / ath) - 1) * 100
        roll_max = h['High'].cummax()
        avg_corr = ((h['Low'] - roll_max) / roll_max).mean() * 100
        
        # RSI
        delta = h['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        return {
            "Preis": round(cp, 2),
            "Korr_Akt": round(curr_corr, 1),
            "Korr_Avg": round(avg_corr, 1),
            "RSI": round(rsi, 1),
            "FV": calculate_strict_fv(ticker)
        }
    except: return None

# --- 3. MARKT HEADER ---
try:
    vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
    spy = yf.Ticker("^GSPC").history(period="300d")
    sma125 = spy['Close'].rolling(125).mean().iloc[-1]
    fg = min(100, int((spy['Close'].iloc[-1] / sma125) * 50))
except: vix, fg = 20, 50

st.title("🏛️ Deep-Value Cockpit (Strict Mode)")
c1, c2 = st.columns(2)
c1.metric("VIX Index", f"{vix:.2f}")
c2.metric("Fear & Greed", f"{fg}/100")
st.divider()

# --- 4. ANALYSE & SORTIERUNG ---
res = supabase.table("watchlist").select("ticker").execute()
tickers = [r['ticker'] for r in res.data]

df = pd.DataFrame()

if tickers:
    data_list = []
    with st.spinner("Strenge Bewertung läuft..."):
        for t in tickers:
            d = get_extended_data(t)
            if d:
                t1 = d['FV'] * (1 - t1_pct/100)
                t2 = d['FV'] * (1 - t2_pct/100)
                diff_fv = ((d['Preis'] / d['FV']) - 1) * 100
                
                # Strenge Empfehlung: Kaufen erst ab 20% Discount zum strengen FV
                if diff_fv < -10:
                    rec, priority = "KAUFEN 🟢", 1
                elif diff_fv < 0:
                    rec, priority = "BEOBACHTEN 🟡", 2
                else:
                    rec, priority = "ÜBERTEUERT 🔴", 3
                
                data_list.append({
                    "Priority": priority, "Ticker": t, "Preis": d['Preis'], 
                    "Abst. FV %": round(diff_fv, 1), "Korr. ATH %": d['Korr_Akt'], 
                    "Ø Korr %": d['Korr_Avg'], "RSI": d['RSI'], "Strict FV": d['FV'],
                    "T1": round(t1, 2), "T2": round(t2, 2), 
                    "Empfehlung": rec
                })

    if data_list:
        df = pd.DataFrame(data_list).sort_values(by=["Priority", "Abst. FV %"], ascending=[True, True])
        st.dataframe(
            df.drop(columns=["Priority"]).style.apply(
                lambda x: ['background-color: #042f04' if "🟢" in str(x.Empfehlung) else '' for i in x], axis=1
            ), use_container_width=True
        )

        # --- 5. PERPLEXITY LINK ---
        st.divider()
        sel = st.selectbox("Deep-Dive Analyse:", df['Ticker'].tolist())
        r = df[df['Ticker'] == sel].iloc[0]
        prompt = f"Analysten-Analyse für {sel}: Kurs {r['Preis']}, STRENGER Fair Value {r['Strict FV']}, RSI {r['RSI']}. Warum ist die Aktie trotz strenger Kriterien kaufenswert?"
        st.link_button(f"🚀 {sel} Experten-Analyse", f"https://www.perplexity.ai/?q={quote(prompt)}", use_container_width=True)
