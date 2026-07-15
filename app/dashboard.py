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

# Latest available year per commodity: UAE-reported where published,
# mirror-derived beyond that (only where mirror coverage passed the gate,
# so commodities like wheat honestly stay at their last UAE-reported year).
risks = load(
    """SELECT r.*, c.name FROM risk_scores r
       JOIN dim_commodity c ON c.commodity_id = r.commodity_id
       WHERE r.year = (SELECT max(year) FROM risk_scores r2
                       WHERE r2.commodity_id = r.commodity_id)
       ORDER BY r.composite_risk DESC"""
)
if risks.empty:
    st.error("No risk scores found. Run the pipeline scripts in README order first.")
    st.stop()

if "source" not in risks.columns:  # database predates provenance tracking
    risks["source"] = "uae_reported"

# Latest gate-year mirror coverage per commodity, for card tooltips.
cov_annual = load(
    """SELECT commodity_id, year, coverage_pct FROM mirror_coverage_annual m
       WHERE coverage_pct IS NOT NULL
         AND year = (SELECT max(year) FROM mirror_coverage_annual m2
                     WHERE m2.commodity_id = m.commodity_id
                       AND m2.coverage_pct IS NOT NULL)"""
)
gate_cov = {
    r.commodity_id: (int(r.year), float(r.coverage_pct)) for _, r in cov_annual.iterrows()
}
uae_max_year = load(
    "SELECT max(year) AS y FROM risk_scores WHERE source = 'uae_reported'"
)["y"].iloc[0]

# ---------------- overview risk cards ----------------
st.subheader("Corridor risk overview — latest available year per corridor")
PER_ROW = 4
rows_list = list(risks.iterrows())
for start in range(0, len(rows_list), PER_ROW):
    cols = st.columns(PER_ROW)
    for col, (_, row) in zip(cols, rows_list[start:start + PER_ROW]):
        year = int(row.year)
        mirror = row.source == "mirror_derived"
        # Keep the label short enough that st.metric doesn't truncate it —
        # the ⓘ tooltip and the caption below carry the mirror-derived detail.
        label = f"{risk_color(row.composite_risk)} {row['name']} — {year}"
        if mirror:
            label += " (provisional)"
        help_txt = None
        gate = gate_cov.get(row.commodity_id)
        if mirror and gate:
            help_txt = (
                f"The UAE has not published annual data for {year} yet; this score is "
                f"reconstructed from partner countries' reported exports to the UAE "
                f"(FOB values). Cross-check: mirror covered {gate[1]:.0f}% of "
                f"UAE-reported value in {gate[0]}."
            )
        elif not mirror and year < int(risks["year"].max()) and gate:
            help_txt = (
                f"Not extended past {year} (last UAE-reported year): partner-country "
                f"reporting covered only {gate[1]:.0f}% of UAE-reported value in "
                f"{gate[0]} — a major origin is missing from mirror data, so newer "
                f"mirror-derived scores would be misleading."
            )
        col.metric(
            label,
            f"{row.composite_risk:.0f} / 100",
            f"top origin: {row.top_origin} ({row.top_origin_share*100:.0f}%)",
            delta_color="off",
            help=help_txt,
        )

if (risks["source"] == "mirror_derived").any():
    st.caption(
        f"**Provisional years are mirror-derived:** the UAE has not yet published "
        f"annual customs data after {int(uae_max_year)}, so more recent years are "
        f"reconstructed from what partner countries report exporting to the UAE "
        f"(mirror statistics, FOB values). Corridors whose mirror picture is too "
        f"incomplete stay at their last UAE-reported year — hover a card's ⓘ for "
        f"the cross-check."
    )

st.divider()

# ---------------- commodity detail ----------------
cid = st.selectbox(
    "Commodity detail",
    list(COMMODITIES),
    format_func=lambda c: COMMODITIES[c]["name"],
)
detail = risks[risks["commodity_id"] == cid].iloc[0]
detail_year = int(detail.year)
detail_mirror = detail.source == "mirror_derived"

left, right = st.columns([1, 2])

with left:
    st.markdown(
        f"### {COMMODITIES[cid]['name']} — {detail_year}"
        + (" *(provisional, mirror-derived)*" if detail_mirror else "")
    )
    st.metric("Composite risk", f"{detail.composite_risk:.1f} / 100")
    dep = load(
        """SELECT dependency_pct, import_kt, production_kt FROM dependency_ratios
           WHERE commodity_id = ? AND year = (SELECT max(year) FROM dependency_ratios)""",
        (cid,),
    )
    if not dep.empty and pd.notna(dep.dependency_pct[0]):
        dep_pct = float(dep.dependency_pct[0])
        st.metric(
            "Import dependency (by weight)",
            f"{dep_pct:.0f}%",
            f"domestic production: {dep.production_kt[0]:.0f} kt vs {dep.import_kt[0]:.0f} kt imported",
            delta_color="off",
        )
        st.metric(
            "Exposure-adjusted risk",
            f"{detail.composite_risk * dep_pct / 100:.1f} / 100",
            "corridor risk × import dependency",
            delta_color="off",
        )
    st.metric("Origin concentration (HHI)", f"{detail.hhi:.2f}")
    st.metric("Distinct origin countries", int(detail.n_origins))
    bt = load("SELECT mape_pct, horizon_months FROM backtest_metrics WHERE commodity_id = ?", (cid,))
    if not bt.empty:
        st.metric(
            f"{int(bt.horizon_months[0])}-month forecast error (backtest MAPE)",
            f"{bt.mape_pct[0]:.1f}%",
        )

    flows_table = "fact_imports_mirror_annual" if detail_mirror else "fact_imports"
    origins = load(
        f"""SELECT c.name AS origin, f.trade_value_usd
           FROM {flows_table} f JOIN dim_country c ON c.country_code = f.origin_code
           WHERE f.commodity_id = ? AND f.year = ?
           ORDER BY f.trade_value_usd DESC LIMIT 8""",
        (cid, detail_year),
    )
    fig = go.Figure(go.Bar(
        x=origins["trade_value_usd"] / 1e6,
        y=origins["origin"],
        orientation="h",
    ))
    fig.update_layout(
        title=f"Import value by origin, {detail_year} (USD millions"
              + (", origin-reported)" if detail_mirror else ")"),
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
        "SELECT year, composite_risk, hhi, source FROM risk_scores "
        "WHERE commodity_id = ? ORDER BY year",
        (cid,),
    )
    reported = trend[trend["source"] == "uae_reported"]
    provisional = trend[trend["source"] == "mirror_derived"]
    fig2 = go.Figure(go.Scatter(
        x=reported["year"], y=reported["composite_risk"],
        mode="lines+markers", name="UAE-reported",
    ))
    if not provisional.empty:
        # Prepend the last reported point so the provisional segment connects.
        seg = pd.concat([reported.tail(1), provisional])
        fig2.add_trace(go.Scatter(
            x=seg["year"], y=seg["composite_risk"],
            mode="lines+markers", name="provisional (mirror-derived)",
            line=dict(dash="dash"), marker=dict(symbol="circle-open"),
        ))
    fig2.update_layout(
        title="Composite corridor risk over time (0–100)",
        height=260, margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig2, width="stretch")

    cov_hist = load(
        "SELECT year, coverage_pct FROM mirror_coverage_annual "
        "WHERE commodity_id = ? AND coverage_pct IS NOT NULL ORDER BY year DESC LIMIT 3",
        (cid,),
    )
    if detail_mirror and not cov_hist.empty:
        checks = ", ".join(
            f"{r.coverage_pct:.0f}% ({int(r.year)})" for _, r in cov_hist.iterrows()
        )
        st.caption(
            f"**Provisional-year caveat:** {detail_year} is reconstructed from partner "
            f"countries' reported exports to the UAE (mirror statistics). Mirror values "
            f"are FOB while UAE customs figures are CIF, and origins may count shipments "
            f"the UAE books as transit. Cross-check on overlap years — mirror vs "
            f"UAE-reported: {checks}."
        )
    elif not detail_mirror and detail_year < int(risks["year"].max()) and not cov_hist.empty:
        gate = cov_hist.iloc[0]
        st.caption(
            f"**Why this corridor stops at {detail_year}:** the UAE has published "
            f"nothing newer, and partner-country reporting covered only "
            f"{gate.coverage_pct:.0f}% of UAE-reported value in {int(gate.year)} — a "
            f"major origin is missing from mirror data (e.g. Russia stopped publishing "
            f"to Comtrade after 2021), so a mirror-derived score would name the wrong "
            f"top origin. Shown honestly instead of guessed."
        )

# ---------------- monthly corridor monitor (mirror data) ----------------
st.divider()
st.subheader("Monthly corridor monitor — mirror data")

# Fixed categorical palette (Okabe-Ito subset), CVD-validated on light surface.
# Colors are assigned to origins by total value once per dataset and never
# re-cycled, so an origin keeps its color across charts and reruns.
ORIGIN_COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9", "#E69F00"]
# These charts force a light surface, so the text color must be forced dark
# too — otherwise Plotly follows the viewer's dark theme and renders white
# titles/legends invisibly on the near-white background.
CHART_BG = dict(paper_bgcolor="#fcfcfb", plot_bgcolor="#fcfcfb",
                font=dict(color="#333333"))

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
    "+ 20% price volatility. Forecasts: SARIMAX baseline with backtested error shown honestly. "
    "Import dependency = imports / (imports + UAE production) by weight (FAOSTAT); "
    "re-exports are not netted out and HS headings map only approximately to FAO items."
)
