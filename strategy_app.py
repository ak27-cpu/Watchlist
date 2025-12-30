import streamlit as st
from supabase import create_client
import yfinance as yf
import pandas as pd
from urllib.parse import quote

# --- 1. SETUP ---
st.set_page_config(page_title="Buffett-Kommer Strategie v4.3", layout="wide")

try:
    supabase = create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
except Exception as e:
    st.error(f"Datenbank-Fehler: {e}")
    st.stop()

# --- 2. BUFFETT/KOMMER FAIR VALUE LOGIK ---
def calculate_value_investing_fv(ticker):
    """Berechnet den fairen Wert basierend auf Gewinnrendite und Cashflow (Buffett-Stil)."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # Owner Earnings Approximation (Free Cash Flow + Net Income) / 2
        fcf = info.get('freeCashflow', 0)
        net_income = info.get('netIncomeToCommon', 0)
        shares = info.get('sharesOutstanding', 1)
        
        if shares <= 0: return 0
        
        owner_earnings_per_share = ((fcf + net_income) / 2) / shares
        
        # Buffett verlangt oft eine Gewinnrendite (Earnings Yield) von mind. 6-8%
        # Wir diskontieren die Owner Earnings mit einem Faktor von 12.5 bis 15 (entspricht 6.5% - 8% Rendite)
        fv_buffett = owner_earnings_per_share * 15 
        
        # Sicherheits-Check: Darf nicht extrem vom Buchwert abweichen (Kommer Fokus auf Substanz)
        book_value = info.get('bookValue', 0)
        if book_value > 0:
            # Gewichtung: 70% Ertragskraft (Buffett), 30% Substanz (Kommer)
            final_fv = (fv_buffett * 0.7) + (book_value * 1.5 * 0.3)
        else:
            final_fv = fv_buffett
            
        return round(final_fv, 2)
    except:
        return 0

@st.cache_data(ttl=3600)
def get_analysis_data(ticker):
    try:
        s = yf.Ticker(ticker)
        h = s.history(period="2y")
        if h.empty: return None
        
        cp = h['Close'].iloc[-1]
        ath = h['High'].max()
        corr = ((cp / ath) - 1) * 100
        
        # RSI
        delta = h['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        return {
            "Preis": round(cp, 2),
            "Korr_Akt": round(corr, 1),
            "RSI": round(rsi, 1),
            "FV": calculate_value_investing_fv(ticker)
        }
    except: return None

# --- 3. DASHBOARD ---
st.title("🛡️ Buffett-Kommer Value Cockpit")
st.markdown("**Kriterien:** RSI < 38 | Preis ±10% vom FV | Korrektur > 10%")

res = supabase.table("watchlist").select("ticker").execute()
tickers = [r['ticker'] for r in res.data]

if tickers:
    data_list = []
    with st.spinner("Prüfe Value-Kriterien..."):
        for t in tickers:
            d = get_analysis_data(t)
            if d and d['FV'] > 0:
                # Berechne Abweichung vom Fair Value
                fv_diff = ((d['Preis'] / d['FV']) - 1) * 100
                
                # DIE NEUEN STRENGEN FILTER
                is_rsi_ok = d['RSI'] < 38
                is_fv_ok = -10 <= fv_diff <= 10
                is_corr_ok = d['Korr_Akt'] <= -10
                
                if is_rsi_ok and is_fv_ok and is_corr_ok:
                    rec, priority = "KAUF-ZONE 💎", 1
                elif is_fv_ok or (is_rsi_ok and is_corr_ok):
                    rec, priority = "BEOBACHTEN 🟡", 2
                else:
                    rec, priority = "WARTEN ⚪", 3
                
                data_list.append({
                    "Priority": priority,
                    "Ticker": t,
                    "Preis": d['Preis'],
                    "RSI": d['RSI'],
                    "Fair Value": d['FV'],
                    "Abst. FV %": round(fv_diff, 1),
                    "Korr. %": d['Korr_Akt'],
                    "Status": rec
                })

    if data_list:
        df = pd.DataFrame(data_list).sort_values(by=["Priority", "Abst. FV %"])
        st.dataframe(
            df.drop(columns=["Priority"]).style.apply(
                lambda x: ['background-color: #042f04' if "💎" in str(x.Status) else '' for i in x], axis=1
            ), use_container_width=True, hide_index=True
        )

        # --- PERPLEXITY ---
        st.divider()
        sel = st.selectbox("Deep-Dive Analyse:", df['Ticker'].tolist())
        r = df[df['Ticker'] == sel].iloc[0]
        prompt = f"""Analysiere {sel} nach Buffett/Kommer:
1. Ist das Geschäftsmodell "burggrabenfähig" (Moat)?
2. Bewertung: Kurs {r['Preis']} bei FV {r['Fair Value']}.
3. Antizyklik: RSI {r['RSI']} und Korrektur {r['Korr. %']}%.
4. Warum ist JETZT der richtige Zeitpunkt für einen Value-Einstieg?"""
        
        st.link_button(f"🚀 {sel} Value-Analyse", f"https://www.perplexity.ai/?q={quote(prompt)}", use_container_width=True)
