import os
import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta

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
.block-container { padding-top: 1.5rem !important; }

[data-testid="metric-container"] {
    background: #f7f9ff;
    border: 1px solid #dde6f5;
    border-radius: 10px;
    padding: 12px 16px;
}

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

APP_DIR    = os.path.dirname(os.path.abspath(__file__))
CREDS_PATH = os.path.join(APP_DIR, 'credentials.json')
TOKEN_PATH = os.path.join(APP_DIR, 'token.json')


def load_data(days: int = 7):
    try:
        st.session_state.offers = fetch_capital_one_offers(
            credentials_path=CREDS_PATH,
            token_path=TOKEN_PATH,
            days=days,
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
        st.caption("Emails from hello@capitaloneshopping.com")

with col_refresh:
    st.write("")
    st.write("")
    if st.button("🔄 Refresh", use_container_width=True, type="primary"):
        with st.spinner("Fetching latest offers from Gmail…"):
            load_data(days=st.session_state.get('days_window', 7))
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
# Credentials error
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
    st.info("No offers found. Try clicking Refresh.")
    st.stop()

# ---------------------------------------------------------------------------
# Build dataframe + deduplication
# ---------------------------------------------------------------------------

df = pd.DataFrame(offers)
now_utc = datetime.now(timezone.utc)

# Stores with at least one email in the last 24 h
new_stores = set(
    df[df['Received_dt'] >= now_utc - timedelta(hours=24)]['Store'].unique()
)

# Email count per store (before deduplication)
store_counts = df.groupby('Store').size().rename('Emails')

# Keep only the best (highest cashback) row per store
df_deduped = (
    df.sort_values('Cashback_num', ascending=False)
      .groupby('Store', sort=False)
      .first()
      .reset_index()
      .join(store_counts, on='Store')
)

# Prepend 🆕 to stores with a recent email
df_deduped['Store_display'] = df_deduped['Store'].apply(
    lambda s: f"🆕 {s}" if s in new_stores else s
)

# ---------------------------------------------------------------------------
# Stats bar
# ---------------------------------------------------------------------------

top_idx   = df_deduped['Cashback_num'].idxmax()
top_cb    = df_deduped.loc[top_idx, 'Cashback_num']
top_store = df_deduped.loc[top_idx, 'Store']
new_count = len(new_stores)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Stores", len(df_deduped))
c2.metric("Total Emails", len(df))
c3.metric("Top Cashback", f"{top_cb}%", delta=top_store)
c4.metric("New (24 h)", new_count)

st.write("")

# ---------------------------------------------------------------------------
# Controls row: time window + store filter + search + CSV
# ---------------------------------------------------------------------------

ctl1, ctl2, ctl3, ctl4 = st.columns([2, 2, 2, 1])

with ctl1:
    days_window = st.select_slider(
        "Window", options=[7, 14, 30],
        value=st.session_state.get('days_window', 7),
        format_func=lambda x: f"Last {x} days",
        label_visibility="collapsed",
    )
    if days_window != st.session_state.get('days_window', 7):
        st.session_state['days_window'] = days_window
        with st.spinner(f"Fetching last {days_window} days…"):
            load_data(days=days_window)
        st.rerun()

with ctl2:
    store_opts = ["All stores"] + sorted(df_deduped['Store'].unique().tolist())
    store_filter = st.selectbox("Store", store_opts, label_visibility="collapsed")

with ctl3:
    search = st.text_input("Search", placeholder="🔍  Search store or offer…",
                           label_visibility="collapsed")

# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

filtered = df_deduped.copy()
if store_filter != "All stores":
    filtered = filtered[filtered['Store'] == store_filter]
if search:
    mask = filtered.apply(
        lambda row: search.lower() in ' '.join(str(v) for v in row.values).lower(), axis=1
    )
    filtered = filtered[mask]

with ctl4:
    csv_bytes = filtered[['Store', 'Cashback', 'Emails', 'Received', 'Email']].to_csv(index=False).encode()
    st.download_button(
        "⬇️ CSV",
        data=csv_bytes,
        file_name=f"capital_one_offers_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        use_container_width=True,
    )

st.caption(f"Showing **{len(filtered)}** stores · {int(filtered['Emails'].sum())} emails")

# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

display = filtered[['Store_display', 'Cashback', 'Emails', 'Received', 'Email']].reset_index(drop=True)

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    height=min(60 + len(display) * 38, 700),
    column_config={
        'Store_display': st.column_config.TextColumn("Store",          width=200),
        'Cashback':      st.column_config.TextColumn("Cashback %",     width=180),
        'Emails':        st.column_config.NumberColumn("Emails",       width=90,  format="%d"),
        'Received':      st.column_config.TextColumn("Best Offer Received (UTC)", width=220),
        'Email':         st.column_config.LinkColumn(
                             "Open Email",
                             display_text="✉ View",
                             width=110,
                         ),
    },
)

st.caption("Deduplicated by store — showing best cashback per store. Click any header to sort.")
