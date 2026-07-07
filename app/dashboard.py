"""QUTVIEW — UAE food-corridor risk dashboard (Streamlit).

Run:  streamlit run app/dashboard.py
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from common import COMMODITIES, DB_PATH  # noqa: E402

st.set_page_config(page_title="QUTVIEW — Food Corridor Risk", layout="wide")


@st.cache_data(ttl=300)
def load(query: str, params=()) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql(query, conn, params=params)


def risk_color(score: float) -> str:
    return "🔴" if score >= 50 else "🟠" if score >= 30 else "🟢"


# ---------------- header ----------------
sources = load("SELECT source_name, kind FROM data_source")
kinds = dict(zip(sources["source_name"], sources["kind"])) if not sources.empty else {}
badge = " · ".join(
    f"{name}: {'LIVE' if kind == 'live' else 'SAMPLE'}" for name, kind in kinds.items()
) or "no data loaded"

st.title("QUTVIEW — UAE Food Import Corridor Risk")
st.caption(f"Sovereign food-security supply-chain intelligence · Data sources — {badge}")

risks = load(
    """SELECT r.*, c.name FROM risk_scores r
       JOIN dim_commodity c ON c.commodity_id = r.commodity_id
       WHERE r.year = (SELECT max(year) FROM risk_scores)
       ORDER BY r.composite_risk DESC"""
)
if risks.empty:
    st.error("No risk scores found. Run the pipeline scripts in README order first.")
    st.stop()

latest_year = int(risks["year"].iloc[0])

# ---------------- overview risk cards ----------------
st.subheader(f"Corridor risk overview — {latest_year}")
cols = st.columns(len(risks))
for col, (_, row) in zip(cols, risks.iterrows()):
    col.metric(
        f"{risk_color(row.composite_risk)} {row['name']}",
        f"{row.composite_risk:.0f} / 100",
        f"top origin: {row.top_origin} ({row.top_origin_share*100:.0f}%)",
        delta_color="off",
    )

st.divider()

# ---------------- commodity detail ----------------
cid = st.selectbox(
    "Commodity detail",
    list(COMMODITIES),
    format_func=lambda c: COMMODITIES[c]["name"],
)
detail = risks[risks["commodity_id"] == cid].iloc[0]

left, right = st.columns([1, 2])

with left:
    st.markdown(f"### {COMMODITIES[cid]['name']}")
    st.metric("Composite risk", f"{detail.composite_risk:.1f} / 100")
    st.metric("Origin concentration (HHI)", f"{detail.hhi:.2f}")
    st.metric("Distinct origin countries", int(detail.n_origins))
    bt = load("SELECT mape_pct, horizon_months FROM backtest_metrics WHERE commodity_id = ?", (cid,))
    if not bt.empty:
        st.metric(
            f"{int(bt.horizon_months[0])}-month forecast error (backtest MAPE)",
            f"{bt.mape_pct[0]:.1f}%",
        )

    origins = load(
        """SELECT c.name AS origin, f.trade_value_usd
           FROM fact_imports f JOIN dim_country c ON c.country_code = f.origin_code
           WHERE f.commodity_id = ? AND f.year = ?
           ORDER BY f.trade_value_usd DESC LIMIT 8""",
        (cid, latest_year),
    )
    fig = go.Figure(go.Bar(
        x=origins["trade_value_usd"] / 1e6,
        y=origins["origin"],
        orientation="h",
    ))
    fig.update_layout(
        title=f"Import value by origin, {latest_year} (USD millions)",
        height=320, margin=dict(l=10, r=10, t=40, b=10),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch")

with right:
    hist = load(
        "SELECT period, price FROM fact_prices WHERE commodity_id = ? ORDER BY period", (cid,)
    )
    fc = load(
        "SELECT period, forecast, lower_ci, upper_ci FROM forecasts "
        "WHERE commodity_id = ? ORDER BY period", (cid,)
    )
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist["period"], y=hist["price"], name="Price", mode="lines"))
    if not fc.empty:
        fig.add_trace(go.Scatter(
            x=fc["period"], y=fc["forecast"], name="Forecast",
            mode="lines", line=dict(dash="dash"),
        ))
        fig.add_trace(go.Scatter(
            x=pd.concat([fc["period"], fc["period"][::-1]]),
            y=pd.concat([fc["upper_ci"], fc["lower_ci"][::-1]]),
            fill="toself", opacity=0.15, line=dict(width=0),
            name="90% interval", showlegend=True,
        ))
    fig.update_layout(
        title=f"{COMMODITIES[cid]['name']} — global price ({COMMODITIES[cid]['unit']}) with 6-month forecast",
        height=420, margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, width="stretch")

    trend = load(
        "SELECT year, composite_risk, hhi FROM risk_scores WHERE commodity_id = ? ORDER BY year",
        (cid,),
    )
    fig2 = go.Figure(go.Scatter(
        x=trend["year"], y=trend["composite_risk"], mode="lines+markers", name="Composite risk"
    ))
    fig2.update_layout(
        title="Composite corridor risk over time (0–100)",
        height=260, margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig2, width="stretch")

st.caption(
    "Risk score = 50% origin concentration (HHI) + 30% top-origin dependency "
    "+ 20% price volatility. Forecasts: SARIMAX baseline with backtested error shown honestly."
)
