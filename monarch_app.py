"""Streamlit dashboard — Fun Money Budget Tracker powered by Monarch Money."""

import os
from calendar import monthrange
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from monarch_client import (
    MonarchClient,
    actual_amount,
    budget_amount,
    category_group_type,
    category_name,
    guess_fun_categories,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Fun Money Tracker",
    page_icon="💸",
    layout="wide",
)

st.markdown("""
<style>
.block-container { padding-top: 1.5rem !important; }

[data-testid="metric-container"] {
    background: #f0faf2;
    border: 1px solid #b2dfbb;
    border-radius: 10px;
    padding: 12px 18px;
}
.over-budget [data-testid="metric-container"]:nth-child(2) {
    background: #fff3f3;
    border-color: #ffcdd2;
}

div.stDataFrame { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

for _k in ("client", "budget_cache", "txn_cache", "cache_key"):
    if _k not in st.session_state:
        st.session_state[_k] = None

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown("## 💸 Fun Money Tracker")
st.caption("Powered by Monarch Money · track your personal spending budget")
st.divider()

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _try_secrets_login() -> bool:
    """Attempt auto-login from Streamlit secrets. Returns True on success."""
    try:
        email = st.secrets.get("MONARCH_EMAIL", "")
        password = st.secrets.get("MONARCH_PASSWORD", "")
        mfa = st.secrets.get("MONARCH_MFA_SECRET", "")
        if email and password:
            c = MonarchClient()
            c.login(email, password, mfa or None)
            st.session_state.client = c
            return True
    except Exception:
        pass
    return False


if st.session_state.client is None:
    if not _try_secrets_login():
        st.markdown("### Connect to Monarch Money")
        with st.form("login_form"):
            email_in = st.text_input("Email", placeholder="you@example.com")
            pw_in = st.text_input("Password", type="password")
            mfa_in = st.text_input(
                "MFA Secret *(optional)*",
                placeholder="Base-32 TOTP secret — only if 2FA is on",
                help="This is the raw secret used to generate TOTP codes, "
                     "not a one-time 6-digit code.",
            )
            submitted = st.form_submit_button(
                "Connect  →", type="primary", use_container_width=True
            )

        if submitted:
            if not email_in or not pw_in:
                st.error("Email and password are required.")
            else:
                with st.spinner("Connecting to Monarch Money…"):
                    try:
                        c = MonarchClient()
                        c.login(email_in, pw_in, mfa_in.strip() or None)
                        st.session_state.client = c
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Login failed: {exc}")

        with st.expander("ℹ️  Where do I find my MFA secret?", expanded=False):
            st.markdown("""
If you have two-factor authentication enabled on Monarch Money, you need the
**base-32 TOTP secret** (shown when you first set up an authenticator app),
not the 6-digit rotating code.

If you no longer have the secret, disable & re-enable 2FA in Monarch Money
settings to retrieve it, then paste it here.

Alternatively, leave it blank and disable 2FA temporarily while using this app.
            """)
        st.stop()

client: MonarchClient = st.session_state.client

# ---------------------------------------------------------------------------
# Controls — month picker + refresh
# ---------------------------------------------------------------------------

today = date.today()


def _month_list(n: int = 12) -> list[date]:
    """Return last n month start dates, newest first."""
    result = []
    year, month = today.year, today.month
    for _ in range(n):
        result.append(date(year, month, 1))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return result


months = _month_list(12)
month_labels = {m.strftime("%B %Y"): m for m in months}

col_m, col_cat, col_r = st.columns([2, 3, 1])

with col_m:
    selected_label = st.selectbox(
        "Month",
        list(month_labels.keys()),
        index=0,
        label_visibility="collapsed",
    )

with col_r:
    st.write("")
    st.write("")
    force_refresh = st.button("🔄 Refresh", use_container_width=True)

selected_month = month_labels[selected_label]
last_day = monthrange(selected_month.year, selected_month.month)[1]
start_str = selected_month.strftime("%Y-%m-%d")
end_str = selected_month.replace(day=last_day).strftime("%Y-%m-%d")
cache_key = start_str

# ---------------------------------------------------------------------------
# Fetch data (cached per month in session state)
# ---------------------------------------------------------------------------

if force_refresh or st.session_state.cache_key != cache_key:
    with st.spinner(f"Loading {selected_label} data from Monarch Money…"):
        try:
            budgets_raw = client.get_budgets(start_str, end_str)
            txns_raw = client.get_transactions(start_str, end_str)
            st.session_state.budget_cache = budgets_raw
            st.session_state.txn_cache = txns_raw
            st.session_state.cache_key = cache_key
        except Exception as exc:
            st.error(f"Failed to fetch data: {exc}")
            st.stop()

budgets: list[dict] = st.session_state.budget_cache or []
txns_raw: list[dict] = st.session_state.txn_cache or []

if not budgets:
    st.warning("No budget data returned for this month. Make sure your Monarch Money account has budgets configured.")
    st.stop()

# ---------------------------------------------------------------------------
# Budget category selector
# ---------------------------------------------------------------------------

expense_budgets = [b for b in budgets if category_group_type(b) in ("expense", "")]
all_cat_names = sorted({category_name(b) for b in expense_budgets})
fun_defaults = guess_fun_categories(budgets)

with col_cat:
    selected_cat = st.selectbox(
        "Budget category",
        all_cat_names,
        index=all_cat_names.index(fun_defaults[0]) if fun_defaults and fun_defaults[0] in all_cat_names else 0,
        label_visibility="collapsed",
    )

# Find the matching budget row
matched = [b for b in budgets if category_name(b) == selected_cat]
budget_total = sum(budget_amount(b) for b in matched)
budget_spent = sum(actual_amount(b) for b in matched)
budget_remaining = budget_total - budget_spent
pct_used = (budget_spent / budget_total * 100) if budget_total > 0 else 0.0
is_over = budget_spent > budget_total

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------

k1, k2, k3, k4 = st.columns(4)
k1.metric("Budget", f"${budget_total:,.2f}")
k2.metric(
    "Spent",
    f"${budget_spent:,.2f}",
    delta=f"{'over' if is_over else 'of'} ${budget_total:,.2f}",
    delta_color="inverse",
)
k3.metric(
    "Remaining",
    f"${abs(budget_remaining):,.2f}",
    delta="over budget" if is_over else None,
    delta_color="inverse" if is_over else "normal",
)
k4.metric("Used", f"{pct_used:.1f}%")

st.write("")

# ---------------------------------------------------------------------------
# Gauge + breakdown side by side
# ---------------------------------------------------------------------------

def _make_gauge(spent: float, budget: float, label: str) -> go.Figure:
    if budget <= 0:
        axis_max = max(spent * 1.5, 100)
    else:
        axis_max = max(budget * 1.3, spent * 1.1)

    if pct_used < 75:
        bar_color = "#43A047"
    elif pct_used < 100:
        bar_color = "#FB8C00"
    else:
        bar_color = "#E53935"

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=spent,
        title={"text": f"<b>{label}</b><br><span style='font-size:13px;color:#666'>spent this month</span>",
               "font": {"size": 17}},
        number={"prefix": "$", "font": {"size": 40, "color": bar_color}},
        gauge={
            "axis": {
                "range": [0, axis_max],
                "tickprefix": "$",
                "tickfont": {"size": 11},
                "nticks": 6,
            },
            "bar": {"color": bar_color, "thickness": 0.28},
            "bgcolor": "white",
            "borderwidth": 0,
            "steps": [
                {"range": [0, budget * 0.75],  "color": "#E8F5E9"},
                {"range": [budget * 0.75, budget], "color": "#FFF9C4"},
                {"range": [budget, axis_max],    "color": "#FFEBEE"},
            ],
            "threshold": {
                "line": {"color": "#B71C1C", "width": 3},
                "thickness": 0.8,
                "value": budget,
            },
        },
    ))
    fig.update_layout(
        height=280,
        margin=dict(t=90, b=10, l=30, r=30),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# Build transactions dataframe for the selected category
def _parse_txns(raw: list[dict]) -> pd.DataFrame:
    rows = []
    for t in raw:
        cat = t.get("category") or {}
        merchant = t.get("merchant") or {}
        rows.append({
            "id": t.get("id", ""),
            "date": t.get("date", ""),
            "merchant": merchant.get("name") or t.get("description", ""),
            "category": cat.get("name", ""),
            "amount": float(t.get("amount", 0)),
            "is_income": bool(t.get("isIncome", False)),
            "is_pending": bool(t.get("isPending", False)),
            "notes": t.get("notes", "") or "",
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


txns_df = _parse_txns(txns_raw)
fun_txns = txns_df[(txns_df["category"] == selected_cat) & (~txns_df["is_income"])] if not txns_df.empty else txns_df

gauge_col, breakdown_col = st.columns([1, 1])

with gauge_col:
    st.plotly_chart(_make_gauge(budget_spent, budget_total, selected_cat), use_container_width=True)

with breakdown_col:
    st.markdown(f"#### Spending breakdown — {selected_label}")
    if fun_txns.empty:
        st.info("No transactions found for this category in the selected month.")
    else:
        merchant_summary = (
            fun_txns.groupby("merchant")["amount"]
            .sum()
            .reset_index()
            .rename(columns={"amount": "total"})
            .sort_values("total", ascending=False)
            .head(10)
        )
        fig_bar = px.bar(
            merchant_summary,
            x="total",
            y="merchant",
            orientation="h",
            labels={"total": "Amount ($)", "merchant": ""},
            color="total",
            color_continuous_scale=["#81C784", "#FFF176", "#EF9A9A"],
            text=merchant_summary["total"].map(lambda v: f"${v:,.2f}"),
        )
        fig_bar.update_traces(textposition="outside", cliponaxis=False)
        fig_bar.update_layout(
            height=280,
            margin=dict(t=10, b=10, l=10, r=80),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            coloraxis_showscale=False,
            xaxis_showgrid=False,
            yaxis={"categoryorder": "total ascending"},
        )
        st.plotly_chart(fig_bar, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Daily spending sparkline (full month)
# ---------------------------------------------------------------------------

if not fun_txns.empty:
    daily = (
        fun_txns.groupby(fun_txns["date"].dt.date)["amount"]
        .sum()
        .reset_index()
        .rename(columns={"date": "day", "amount": "spent"})
        .sort_values("day")
    )

    # running total line
    daily["cumulative"] = daily["spent"].cumsum()

    fig_daily = go.Figure()
    fig_daily.add_bar(
        x=daily["day"], y=daily["spent"],
        name="Daily", marker_color="#7986CB", opacity=0.7,
        hovertemplate="<b>%{x}</b><br>$%{y:,.2f}<extra></extra>",
    )
    fig_daily.add_scatter(
        x=daily["day"], y=daily["cumulative"],
        name="Running total", mode="lines+markers",
        line=dict(color="#E53935", width=2),
        hovertemplate="<b>%{x}</b><br>Cumulative $%{y:,.2f}<extra></extra>",
    )
    # Budget line
    fig_daily.add_hline(
        y=budget_total, line_dash="dot", line_color="#43A047",
        annotation_text=f"Budget ${budget_total:,.0f}",
        annotation_position="top right",
    )
    fig_daily.update_layout(
        title=f"Daily spend — {selected_label}  ·  {selected_cat}",
        height=320,
        margin=dict(t=50, b=30, l=20, r=20),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis_showgrid=False,
        yaxis_gridcolor="#f0f0f0",
        legend=dict(orientation="h", y=1.12),
    )
    st.plotly_chart(fig_daily, use_container_width=True)
    st.divider()

# ---------------------------------------------------------------------------
# Transactions table
# ---------------------------------------------------------------------------

st.markdown(f"#### Transactions — {selected_cat}")

if fun_txns.empty:
    st.info("No transactions for this category in the selected period.")
else:
    display_txns = (
        fun_txns[["date", "merchant", "amount", "notes", "is_pending"]]
        .sort_values("date", ascending=False)
        .copy()
    )
    display_txns["date"] = display_txns["date"].dt.date

    total_row_count = len(display_txns)
    show_all = st.checkbox(f"Show all {total_row_count} transactions", value=False)
    shown = display_txns if show_all else display_txns.head(15)

    st.dataframe(
        shown,
        use_container_width=True,
        hide_index=True,
        height=min(60 + len(shown) * 38, 620),
        column_config={
            "date":       st.column_config.DateColumn("Date",       width=120),
            "merchant":   st.column_config.TextColumn("Merchant",   width=220),
            "amount":     st.column_config.NumberColumn("Amount",   width=110, format="$%.2f"),
            "notes":      st.column_config.TextColumn("Notes",      width=200),
            "is_pending": st.column_config.CheckboxColumn("Pending", width=80),
        },
    )

    csv = display_txns.to_csv(index=False).encode()
    st.download_button(
        "⬇️  Download CSV",
        data=csv,
        file_name=f"fun_money_{start_str[:7]}.csv",
        mime="text/csv",
    )

# ---------------------------------------------------------------------------
# All budgets overview (expandable)
# ---------------------------------------------------------------------------

with st.expander("📊  All budget categories this month", expanded=False):
    budget_rows = []
    for b in budgets:
        bamt = budget_amount(b)
        aamt = actual_amount(b)
        rem = bamt - aamt
        pct = (aamt / bamt * 100) if bamt > 0 else 0.0
        budget_rows.append({
            "Category":   category_name(b),
            "Budget ($)": bamt,
            "Spent ($)":  aamt,
            "Remaining":  rem,
            "% Used":     round(pct, 1),
            "Over?":      aamt > bamt,
        })
    bdf = pd.DataFrame(budget_rows).sort_values("% Used", ascending=False)
    st.dataframe(
        bdf,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Category":   st.column_config.TextColumn(width=200),
            "Budget ($)": st.column_config.NumberColumn(format="$%.2f", width=120),
            "Spent ($)":  st.column_config.NumberColumn(format="$%.2f", width=120),
            "Remaining":  st.column_config.NumberColumn(format="$%.2f", width=120),
            "% Used":     st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%", width=130),
            "Over?":      st.column_config.CheckboxColumn(width=70),
        },
    )

st.caption("Data refreshed from Monarch Money · use the 🔄 button to pull the latest")
