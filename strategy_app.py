import streamlit as st
from supabase import create_client
import yfinance as yf
import pandas as pd
from urllib.parse import quote

# --- 1. SETUP ---
st.set_page_config(page_title="Buffett-Kommer Strategie v4.4", layout="wide")

try:
    supabase = create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
except Exception as e:
    st.error(f"Datenbank-Fehler: {e}")
    st.stop()

# --- 2. VALUE LOGIK ---
def calculate_value_investing_fv(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        fcf = info.get('freeCashflow', 0)
        net_income = info.get('netIncomeToCommon', 0)
        shares = info.get('sharesOutstanding', 1)
        if shares <= 0: return 0
        
        # Buffett Owner Earnings Ansatz
        owner_earnings_ps = ((fcf + net_income) / 2) / shares
        fv_buffett = owner_earnings_ps * 15 
        
        # Kommer Substanz Ansatz (Buchwert)
        book_value = info.get('bookValue', 0)
        if book_value > 0:
            final_fv = (fv_buffett * 0.7) + (book_value * 1.5 * 0.3)
        else:
            final_fv = fv_buffett
        return round(final_fv, 2)
    except: return 0

@st.cache_data(ttl=3600)
def get_analysis_data(ticker):
    try:
        s = yf.Ticker(ticker)
        h = s.history(period="2y")
        if h.empty: return None
        cp = h['Close'].iloc[-1]
        ath = h['High'].max()
        corr = ((cp / ath) - 1) * 100
        delta = h['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        return {"Preis": round(cp, 2), "Korr": round(corr, 1), "RSI": round(rsi, 1), "FV": calculate_value_investing_fv(ticker)}
    except: return None

# --- 3. DASHBOARD HEADER ---
st.title("🛡️ Buffett-Kommer Value Cockpit")
st.info("💎 Diamant-Kriterien: RSI < 38 | Korrektur > 10% | Preis ±10% vom Fair Value")

# --- 4. DATEN VERARBEITUNG ---
res = supabase.table("watchlist").select("ticker").execute()
tickers = [r['ticker'] for r in res.data]

if tickers:
    data_list = []
    with st.spinner("Analysiere gesamte Watchlist..."):
        for t in tickers:
            d = get_analysis_data(t)
            if d:
                fv_diff = ((d['Preis'] / d['FV']) - 1) * 100 if d['FV'] > 0 else 999
                
                # Check Kriterien
                c1 = d['RSI'] < 38
                c2 = -10 <= fv_diff <= 10
                c3 = d['Korr'] <= -10
                
                if c1 and c2 and c3:
                    status, priority = "TOP-CHANCE 💎", 1
                elif c2:
                    status, priority = "FAIR BEWERTET 🟡", 2
                else:
                    status, priority = "WARTEN ⚪", 3
                
                data_list.append({
                    "Priority": priority,
                    "Ticker": t,
                    "Preis": d['Preis'],
                    "RSI": d['RSI'],
                    "Fair Value": d['FV'],
                    "FV-Diff %": round(fv_diff, 1),
                    "Korr %": d['Korr'],
                    "Status": status
                })

    if data_list:
        df = pd.DataFrame(data_list).sort_values(by=["Priority", "FV-Diff %"])
        
        # Falls kein Diamant gefunden wurde
        if not any(df['Priority'] == 1):
            st.warning("Aktuell erfüllt keine Aktie ALLE Diamant-Kriterien (RSI, Korrektur & Fair Value gleichzeitig).")

        # Styling & Anzeige
        st.dataframe(
            df.drop(columns=["Priority"]).style.apply(
                lambda x: ['background-color: #042f04' if "💎" in str(x.Status) else '' for i in x], axis=1
            ), use_container_width=True
        )

        # --- 5. PERPLEXITY ---
        st.divider()
        sel = st.selectbox("Deep-Dive Analyse:", df['Ticker'].tolist())
        r = df[df['Ticker'] == sel].iloc[0]
        prompt = f"Analysiere {sel} nach Buffett/Kommer: Kurs {r['Preis']}, FV {r['Fair Value']}, RSI {r['RSI']}, Korr {r['Korr']}%."
        st.link_button(f"🚀 {sel} Analyse auf Perplexity", f"https://www.perplexity.ai/?q={quote(prompt)}", use_container_width=True)
else:
    st.error("Keine Ticker in der Supabase-Watchlist gefunden!")
