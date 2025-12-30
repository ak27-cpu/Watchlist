import streamlit as st
from supabase import create_client
import yfinance as yf
import pandas as pd
from urllib.parse import quote

# --- 1. SETUP & SIDEBAR ---
st.set_page_config(page_title="FairValue Watchlist v4.1", layout="wide")

with st.sidebar:
    st.header("⚙️ Strategie-Einstellungen")
    t1_pct = st.slider("Tranche 1 Abstand (%)", 1, 30, 10)
    t2_pct = st.slider("Tranche 2 Abstand (%)", 1, 30, 15)
    st.divider()
    if st.button("🔄 Daten neu laden"):
        st.cache_data.clear()
        st.rerun()

try:
    supabase = create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
except Exception as e:
    st.error(f"Supabase Fehler: {e}")
    st.stop()

# --- 2. FAIR VALUE LOGIK ---
def calculate_smart_fv(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        cp = info.get('currentPrice', 0)
        eps = info.get('forwardEps', 0)
        growth = info.get('earningsGrowth', 0.05)
        fcf = info.get('freeCashflow', 0)
        shares = info.get('sharesOutstanding', 1)
        
        fv_graham = eps * (8.5 + 2 * (growth * 100)) if eps > 0 else 0
        fv_fcf = (fcf / shares) * 20 if fcf and shares else 0
        
        if fv_graham > 0 and fv_fcf > 0:
            final_fv = (fv_graham * 0.6) + (fv_fcf * 0.4)
        else:
            final_fv = max(fv_graham, fv_fcf)
            
        return round(final_fv, 2) if final_fv > 0 else round(cp * 0.9, 2)
    except:
        return 0

@st.cache_data(ttl=3600)
def get_extended_data(ticker):
    try:
        s = yf.Ticker(ticker)
        h = s.history(period="3y")
        if h.empty: return None
        
        info = s.info
        cp = h['Close'].iloc[-1]
        ath = h['High'].max()
        
        curr_corr = ((cp / ath) - 1) * 100
        roll_max = h['High'].cummax()
        avg_corr = ((h['Low'] - roll_max) / roll_max).mean() * 100

        delta = h['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        return {
            "Preis": round(cp, 2),
            "ATH": round(ath, 2),
            "Korr_Akt": round(curr_corr, 1),
            "Korr_Avg": round(avg_corr, 1),
            "RSI": round(rsi, 1),
            "FV": calculate_smart_fv(ticker)
        }
    except: return None

# --- 3. MARKT HEADER ---
try:
    vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
    spy = yf.Ticker("^GSPC").history(period="300d")
    sma125 = spy['Close'].rolling(125).mean().iloc[-1]
    fg = min(100, int((spy['Close'].iloc[-1] / sma125) * 50))
except: vix, fg = 20, 50

st.title("🏛️ Deep-Value Strategie Cockpit")
c1, c2 = st.columns(2)
c1.metric("VIX Index", f"{vix:.2f}")
c2.metric("Fear & Greed", f"{fg}/100")
st.divider()

# --- 4. ANALYSE & SORTIERUNG ---
res = supabase.table("watchlist").select("ticker").execute()
tickers = [r['ticker'] for r in res.data]

# Initialisiere df als None, um NameError zu vermeiden
df = pd.DataFrame()

if tickers:
    data_list = []
    with st.spinner("Analysiere Watchlist..."):
        for t in tickers:
            d = get_extended_data(t)
            if d:
                t1 = d['FV'] * (1 - t1_pct/100)
                t2 = d['FV'] * (1 - t2_pct/100)
                diff_fv = ((d['Preis'] / d['FV']) - 1) * 100
                
                if diff_fv < -10 or (d['RSI'] < 35 and d['Korr_Akt'] < d['Korr_Avg']):
                    rec, priority = "KAUFEN 🟢", 1
                elif diff_fv < 5:
                    rec, priority = "BEOBACHTEN 🟡", 2
                else:
                    rec, priority = "WARTEN 🔴", 3
                
                data_list.append({
                    "Priority": priority, "Ticker": t, "Preis": d['Preis'], 
                    "Abst. FV %": round(diff_fv, 1), "Korr. ATH %": d['Korr_Akt'], 
                    "Ø Korr %": d['Korr_Avg'], "RSI": d['RSI'], "Fair Value": d['FV'],
                    f"T1 (-{t1_pct}%)": round(t1, 2), f"T2 (-{t2_pct}%)": round(t2, 2), 
                    "Empfehlung": rec
                })

    if data_list:
        df = pd.DataFrame(data_list).sort_values(by=["Priority", "Abst. FV %"], ascending=[True, True])
        
        # DataFrame Anzeige ohne das veraltete '=True'
        st.dataframe(
            df.drop(columns=["Priority"]).style.apply(
                lambda x: ['background-color: #042f04' if "🟢" in str(x.Empfehlung) else '' for i in x], axis=1
            ), use_container_width=True
        )

        # --- 5. PERPLEXITY LINK ---
        st.divider()
        st.subheader("🔍 Experten-Analyse (Professional Prompt)")
        
        # Selectbox greift jetzt nur zu, wenn df nicht leer ist
        sel = st.selectbox("Aktie für Deep-Dive wählen:", df['Ticker'].tolist())
        r = df[df['Ticker'] == sel].iloc[0]

        perp_prompt = f"""Du bist renommierter Analyst. Analysiere {sel} ({r['Ticker']}): 
Kurs {r['Preis']}, Fair Value {r['Fair Value']}, RSI {r['RSI']}, Korrektur {r['Korr. ATH %']}% (Schnitt {r['Ø Korr %']}%).
1. Kurz-Statement zu News & Marktstellung.
2. Fair Value Check (Aktienfinder-Style) & KGV/Cashflow-Bewertung.
3. Korrektur-Einordnung vs. Historie.
4. Kaufzonen-Urteil: Warum JETZT investieren?
5. Ausblick & Renditeerwartung für 2026."""

        url = f"https://www.perplexity.ai/?q={quote(perp_prompt)}"
        st.link_button(f"🚀 {sel} Experten-Analyse starten", url, use_container_width=True)
    else:
        st.info("Lade Daten oder keine Ticker gefunden...")
else:
    st.warning("Die Watchlist in Supabase ist leer.")
