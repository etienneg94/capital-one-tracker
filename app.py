import os
import streamlit as st
import pandas as pd
from datetime import datetime

from gmail_client import fetch_capital_one_offers, TYPE_LABELS

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Capital One Cashback Tracker",
    page_icon="💳",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

st.markdown("""
<style>
/* Tighter top padding */
.block-container { padding-top: 1.5rem !important; }

/* Metric cards */
[data-testid="metric-container"] {
    background: #f7f9ff;
    border: 1px solid #dde6f5;
    border-radius: 10px;
    padding: 12px 16px;
}

/* Refresh button */
div[data-testid="column"]:last-child .stButton > button {
    background: #0a3166;
    color: white;
    border: none;
    font-weight: 600;
}
div[data-testid="column"]:last-child .stButton > button:hover {
    background: #1a4f8a;
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if 'offers' not in st.session_state:
    st.session_state.offers = None
if 'last_refreshed' not in st.session_state:
    st.session_state.last_refreshed = None
if 'error' not in st.session_state:
    st.session_state.error = None

# ---------------------------------------------------------------------------
# Credentials path (resolve relative to app directory)
# ---------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CREDS_PATH = os.path.join(APP_DIR, 'credentials.json')
TOKEN_PATH = os.path.join(APP_DIR, 'token.json')


def load_data():
    try:
        st.session_state.offers = fetch_capital_one_offers(
            credentials_path=CREDS_PATH,
            token_path=TOKEN_PATH,
        )
        st.session_state.last_refreshed = datetime.now()
        st.session_state.error = None
    except FileNotFoundError:
        st.session_state.error = 'no_credentials'
    except Exception as e:
        st.session_state.error = str(e)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

col_title, col_refresh = st.columns([5, 1])
with col_title:
    st.markdown("## 💳 Capital One Shopping — Cashback Tracker")
    if st.session_state.last_refreshed:
        st.caption(f"Last refreshed: **{st.session_state.last_refreshed.strftime('%b %d, %Y · %H:%M')}**")
    else:
        st.caption("Emails from hello@capitaloneshopping.com · Last 7 days")

with col_refresh:
    st.write("")  # vertical spacing
    st.write("")
    if st.button("🔄 Refresh", use_container_width=True, type="primary"):
        with st.spinner("Fetching latest offers from Gmail…"):
            load_data()
        if not st.session_state.error:
            st.success(f"Loaded {len(st.session_state.offers)} offers!", icon="✅")

st.divider()

# ---------------------------------------------------------------------------
# Auto-load on first visit
# ---------------------------------------------------------------------------

if st.session_state.offers is None and st.session_state.error is None:
    with st.spinner("Connecting to Gmail…"):
        load_data()

# ---------------------------------------------------------------------------
# Credentials error — show setup guide
# ---------------------------------------------------------------------------

if st.session_state.error == 'no_credentials':
    st.error("**credentials.json not found.** Follow the steps below to connect Gmail.", icon="🔐")
    with st.expander("📋 Setup instructions (one-time)", expanded=True):
        st.markdown("""
1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project (or use an existing one).
2. Enable the **Gmail API**: APIs & Services → Library → search "Gmail API" → Enable.
3. Create OAuth credentials: APIs & Services → Credentials → **Create Credentials → OAuth client ID**.
   - Application type: **Desktop app**
   - Download the JSON file.
4. Rename it to **`credentials.json`** and place it in this folder:
   ```
   ~/Desktop/capital-one-tracker/credentials.json
   ```
5. Click **🔄 Refresh** — a browser window will open to authorize your Google account.
   After authorization a `token.json` is saved automatically for future runs.
        """)
    st.stop()

elif st.session_state.error:
    st.error(f"**Error:** {st.session_state.error}", icon="⚠️")
    st.stop()

offers = st.session_state.offers or []
if not offers:
    st.info("No offers found in the last 7 days. Try clicking Refresh.")
    st.stop()

# ---------------------------------------------------------------------------
# Stats bar
# ---------------------------------------------------------------------------

df = pd.DataFrame(offers)
top_idx = df['Cashback_num'].idxmax()
top_cb = df.loc[top_idx, 'Cashback_num']
top_store = df.loc[top_idx, 'Store']
c1, c2, c3 = st.columns(3)
c1.metric("Total Offers", len(df))
c2.metric("Top Cashback", f"{top_cb}%", delta=top_store)
c3.metric("Stores", df['Store'].nunique())

st.write("")

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

fc1, fc2 = st.columns([2, 2])

with fc1:
    store_opts = ["All stores"] + sorted(df['Store'].unique().tolist())
    store_filter = st.selectbox("Store", store_opts, label_visibility="collapsed",
                                 placeholder="Filter by store…")

with fc2:
    search = st.text_input("Search", placeholder="🔍  Search store or offer…",
                            label_visibility="collapsed")

# Apply filters
filtered = df.copy()
if store_filter != "All stores":
    filtered = filtered[filtered['Store'] == store_filter]
if search:
    mask = filtered.apply(
        lambda row: search.lower() in ' '.join(str(v) for v in row.values).lower(), axis=1
    )
    filtered = filtered[mask]

st.caption(f"Showing **{len(filtered)}** of {len(df)} offers")

# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

display_cols = ['Store', 'Cashback', 'Received', 'Email']
display = filtered[display_cols].reset_index(drop=True)

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    height=min(60 + len(display) * 38, 700),
    column_config={
        'Store':    st.column_config.TextColumn("Store",          width=200),
        'Cashback': st.column_config.TextColumn("Cashback %",     width=200),
        'Received': st.column_config.TextColumn("Received (UTC)", width=220),
        'Email':    st.column_config.LinkColumn(
                        "Open Email",
                        display_text="✉ View",
                        width=120,
                    ),
    },
)

st.caption("Click any column header to sort. Click ✉ View to open the email in Gmail.")
