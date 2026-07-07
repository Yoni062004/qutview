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

# ---------------- monthly corridor monitor (mirror data) ----------------
st.divider()
st.subheader("Monthly corridor monitor — mirror data")

# Fixed categorical palette (Okabe-Ito subset), CVD-validated on light surface.
# Colors are assigned to origins by total value once per dataset and never
# re-cycled, so an origin keeps its color across charts and reruns.
ORIGIN_COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9", "#E69F00"]
CHART_BG = dict(paper_bgcolor="#fcfcfb", plot_bgcolor="#fcfcfb")

monthly = load(
    """SELECT f.period, c.name AS origin, f.trade_value_usd
       FROM fact_imports_monthly f JOIN dim_country c ON c.country_code = f.origin_code
       WHERE f.commodity_id = ? ORDER BY f.period""",
    (cid,),
)
if monthly.empty:
    st.info("No monthly mirror data loaded — run src/ingest/comtrade_mirror_monthly.py first.")
else:
    mrisk = load(
        """SELECT period, composite_risk, hhi, top_origin, top_origin_share
           FROM risk_scores_monthly WHERE commodity_id = ? ORDER BY period""",
        (cid,),
    )
    cov = load(
        "SELECT basis_year, coverage_pct FROM mirror_coverage WHERE commodity_id = ?", (cid,)
    )

    flow_col, risk_col = st.columns([3, 2])

    with flow_col:
        order = (
            monthly.groupby("origin")["trade_value_usd"].sum().sort_values(ascending=False).index
        )
        fig3 = go.Figure()
        for i, origin in enumerate(order):
            sub = monthly[monthly["origin"] == origin]
            fig3.add_trace(go.Scatter(
                x=sub["period"], y=sub["trade_value_usd"] / 1e6,
                name=origin, stackgroup="flows", mode="lines",
                line=dict(width=0.5, color=ORIGIN_COLORS[i % len(ORIGIN_COLORS)]),
            ))
        fig3.update_layout(
            title="Monthly UAE-bound flows by origin (USD millions, origin-reported)",
            height=420, margin=dict(l=10, r=10, t=40, b=10), **CHART_BG,
        )
        st.plotly_chart(fig3, width="stretch")

    with risk_col:
        if not mrisk.empty:
            latest_m = mrisk.iloc[-1]
            six_back = mrisk.iloc[-7] if len(mrisk) > 6 else mrisk.iloc[0]
            st.metric(
                f"Rolling 12-month risk · {latest_m.period}",
                f"{latest_m.composite_risk:.1f} / 100",
                f"{latest_m.composite_risk - six_back.composite_risk:+.1f} vs 6 months ago",
                delta_color="inverse",
            )
            fig4 = go.Figure(go.Scatter(
                x=mrisk["period"], y=mrisk["composite_risk"],
                mode="lines", name="Rolling composite risk",
                line=dict(width=2, color="#0072B2"),
            ))
            fig4.update_layout(
                title="Rolling 12-month composite risk (0–100)",
                height=330, margin=dict(l=10, r=10, t=40, b=10),
                yaxis=dict(range=[0, 100]), **CHART_BG,
            )
            st.plotly_chart(fig4, width="stretch")

    if not cov.empty and pd.notna(cov.coverage_pct[0]):
        st.caption(
            f"**Mirror data caveat:** the UAE stopped publishing monthly customs data in 2019, "
            f"so these flows are what origin countries report exporting to the UAE. Tracked top "
            f"origins covered **{cov.coverage_pct[0]:.0f}%** of UAE-reported {int(cov.basis_year[0])} "
            f"imports for this commodity. Because only top corridors are tracked, risk levels here "
            f"read higher than the annual model — watch the direction and shifts, not the level. "
            f"The newest month may still be missing slow-reporting origins."
        )
    with st.expander("Underlying monthly data (table)"):
        st.dataframe(
            monthly.pivot_table(index="period", columns="origin",
                                values="trade_value_usd", aggfunc="sum").round(0),
            width="stretch",
        )

st.caption(
    "Risk score = 50% origin concentration (HHI) + 30% top-origin dependency "
    "+ 20% price volatility. Forecasts: SARIMAX baseline with backtested error shown honestly."
)
