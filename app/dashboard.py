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
from common import (  # noqa: E402
    COMMODITIES, DB_PATH, get_app_password, get_smtp_config,
)
from brief.corridor_brief import assemble_facts, generate_brief  # noqa: E402
from brief.brief_pdf import build_brief_pdf  # noqa: E402
from features.alerts import compute_alerts  # noqa: E402
from features.alert_email import compose_digest, send_digest  # noqa: E402

st.set_page_config(page_title="QUTVIEW — Food Corridor Risk", layout="wide")


def require_login() -> None:
    """Optional demo-grade password gate. If QUTVIEW_PASSWORD is set in .env,
    the dashboard asks for it once per session; if not set, the app is open
    (open-demo mode). Not production auth — access control for a shared demo."""
    import hmac

    pw = get_app_password()
    if not pw or st.session_state.get("qv_auth"):
        return
    st.title("QUTVIEW — sign in")
    st.caption("Sovereign food-security corridor intelligence · demo access")
    name = st.text_input("Name (optional)")
    entered = st.text_input("Password", type="password")
    if st.button("Sign in", type="primary"):
        if hmac.compare_digest(entered, pw):
            st.session_state["qv_auth"] = True
            st.session_state["qv_user"] = name.strip() or "analyst"
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()


require_login()


@st.cache_data(ttl=300)
def load(query: str, params=()) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql(query, conn, params=params)


def risk_color(score: float) -> str:
    return "🔴" if score >= 50 else "🟠" if score >= 30 else "🟢"


def md_safe(text: str) -> str:
    """Escape '$' so Streamlit's markdown renderer doesn't treat a pair of
    them as LaTeX math (e.g. '+$42M ... $107M' rendering as garbled italics).
    Applied at the RENDER BOUNDARY only — never to stored strings, the
    downloadable report, or anything the grounding check sees. Not for st.text
    (which is literal and would show the backslash)."""
    return text.replace("$", "\\$")


def vintage_label(year, source: str) -> str:
    """One consistent data-vintage label used on cards, tables and charts, so
    UAE-reported and provisional mirror-derived years read the same everywhere.
    Labelling only — it never changes which year is scored."""
    return (f"{int(year)} · provisional (mirror)" if source == "mirror_derived"
            else f"{int(year)} · UAE-reported")


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

data_stamp = str(load("SELECT coalesce(max(loaded_at), '') AS s FROM data_source")["s"].iloc[0])


# ---------------- active alerts ----------------
@st.cache_data(ttl=300)
def load_alerts(stamp: str):
    with sqlite3.connect(DB_PATH) as conn:
        return compute_alerts(conn)


def _jump_to(commodity_id: str) -> None:
    st.session_state["commodity_detail"] = commodity_id


alerts = load_alerts(data_stamp)
high_alerts = [a for a in alerts if a["severity"] == "high"]
watch_alerts = [a for a in alerts if a["severity"] == "watch"]

if alerts:
    st.subheader(f"Active alerts — {len(high_alerts)} high · {len(watch_alerts)} watch")
    if high_alerts:
        st.error("\n\n".join(
            md_safe(f"🔴 **{a['name']}** · {a['rule'].replace('_', ' ')} — {a['message']}")
            for a in high_alerts))
        # Watch level stays one click away so a page with real alarms is
        # still readable, and a page with only watch items stays calm.
        with st.expander(f"🟠 Watch-level alerts ({len(watch_alerts)})",
                         expanded=False):
            for a in watch_alerts:
                st.markdown(md_safe(
                    f"🟠 **{a['name']}** · {a['rule'].replace('_', ' ')} — {a['message']}"))
    elif watch_alerts:
        with st.expander(f"🟠 Watch-level alerts ({len(watch_alerts)}) — "
                         f"no high alerts", expanded=False):
            for a in watch_alerts:
                st.markdown(md_safe(
                    f"🟠 **{a['name']}** · {a['rule'].replace('_', ' ')} — {a['message']}"))

    # One click to the corridor's detail + A1 brief below (which carry the
    # why and the recommended action — not duplicated here).
    alerted = []
    for a in alerts:  # already sorted high-first
        if a["commodity"] not in [c for c, _, _ in alerted]:
            alerted.append((a["commodity"], a["name"], a["severity"]))
    cols = st.columns(max(len(alerted), 1))
    for col, (acid, aname, asev) in zip(cols, alerted):
        col.button(("🔴 " if asev == "high" else "🟠 ") + aname,
                   key=f"jump_{acid}", on_click=_jump_to, args=(acid,),
                   help="Show this corridor's detail and brief below",
                   use_container_width=True)
    st.caption("Standing alerts from current data (stateless). Buttons open the "
               "corridor's detail and draft brief below.")
else:
    st.caption("No active alerts on current data.")

# ---- email the alert digest — an admin/demo feature, kept in the sidebar so
# it stays out of the corridor analysis flow ----
with st.sidebar:
    st.header("Admin")
    with st.expander("📧 Email alert digest", expanded=False):
        st.caption("Preview of the early-warning email subscribers receive.")
        with sqlite3.connect(DB_PATH) as _conn:
            _subject, _body = compose_digest(_conn)
        st.caption(f"**Subject:** {_subject}")
        st.text(_body)
        _smtp = get_smtp_config()
        if _smtp:
            if st.button("Send digest now", key="send_digest"):
                try:
                    send_digest(_subject, _body, _smtp)
                    st.success(f"Sent to {', '.join(_smtp['to_addrs'])}.")
                except Exception as exc:
                    st.error(f"Send failed: {exc}")
            st.caption(f"Recipient(s): {', '.join(_smtp['to_addrs'])}.")
        else:
            st.info("Live sending is off — set SMTP_HOST / SMTP_USER / "
                    "SMTP_PASSWORD (and ALERT_TO) in .env to enable it. "
                    "The preview above is exactly what would be sent.")

st.divider()

# ---------------- overview risk cards ----------------
st.subheader("Corridor risk overview — latest available year per corridor")
PER_ROW = 4
rows_list = list(risks.iterrows())
for start in range(0, len(rows_list), PER_ROW):
    cols = st.columns(PER_ROW)
    for col, (_, row) in zip(cols, rows_list[start:start + PER_ROW]):
        year = int(row.year)
        mirror = row.source == "mirror_derived"
        # Consistent vintage suffix (same on cards, detail, charts, tables).
        label = f"{risk_color(row.composite_risk)} {row['name']} — {vintage_label(year, row.source)}"
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

# ---------------- category risk rollup ----------------
# Curated grouping of the 9 commodities into Silal's essential categories, so
# risk reads in the language a food-security buyer manages. A rollup is a
# SUMMARY of its members — never an independent measurement.
CATEGORIES = {
    "Cereals": ["wheat", "rice"],
    "Edible oils": ["palm_oil", "sunflower_oil"],
    "Sugar": ["sugar"],
    "Protein / meat": ["poultry", "beef", "mutton"],
    "Animal feed": ["maize", "soybean_meal", "barley"],
    "Dairy": ["dairy"],
    "Pulses": ["lentils"],
}


def _latest_value(cid: str, source: str, year: int) -> float:
    table = "fact_imports_mirror_annual" if source == "mirror_derived" else "fact_imports"
    v = load(f"SELECT coalesce(sum(trade_value_usd), 0) AS v FROM {table} "
             f"WHERE commodity_id = ? AND year = ?", (cid, int(year)))
    return float(v["v"].iloc[0])


st.subheader("Category risk overview")
cat_rows = []
for cat, members in CATEGORIES.items():
    sub = risks[risks["commodity_id"].isin(members)]
    wsum = risksum = 0.0
    worst = None
    provisional = False
    for _, m in sub.iterrows():
        val = _latest_value(m.commodity_id, m.source, m.year)
        wsum += val
        risksum += m.composite_risk * val
        provisional = provisional or (m.source == "mirror_derived")
        if worst is None or m.composite_risk > worst[1]:
            worst = (m["name"], m.composite_risk)
    rolled = risksum / wsum if wsum else sub["composite_risk"].mean()
    cat_rows.append({
        "Category": cat,
        "Rolled-up risk": f"{rolled:.0f} / 100",
        "Headline driver (worst member)": f"{worst[0]} ({worst[1]:.0f})",
        "Members": ", ".join(sub["name"]),
        "Basis": "incl. provisional" if provisional else "UAE-reported",
    })
st.dataframe(pd.DataFrame(cat_rows), width="stretch", hide_index=True)
st.caption(
    "Derived summary — each category's rolled-up risk is the **import-value-"
    "weighted average** of its member commodities' composite risk (a larger "
    "corridor weighs more), using each member's latest-year import value. It is "
    "an aggregation of the per-commodity scores above, not a separate "
    "measurement. **incl. provisional** means at least one member's latest year "
    "is mirror-derived (see the corridor cards); such rollups mix a UAE-reported "
    "anchor with provisional data."
)

st.divider()

# ---------------- feed-dependency lens ----------------
# The animal-feed corridors. This is a curated grouping + honest framing,
# NOT a computed feed->output link (we have no data mapping feed tonnage to
# local production). Corridor figures below are QUTVIEW's own computed numbers;
# the 2006-ban / 90%-imported facts are sourced context, labelled as such.
FEED_COMMODITIES = ["maize", "soybean_meal", "barley"]

st.subheader("Feed-dependency lens — the hidden exposure behind “local” food")
feed_rows = []
for fcid in FEED_COMMODITIES:
    rr = load("SELECT c.name, r.year, r.source, r.composite_risk, r.top_origin, "
              "r.top_origin_share FROM risk_scores r JOIN dim_commodity c "
              "ON c.commodity_id = r.commodity_id WHERE r.commodity_id = ? "
              "ORDER BY r.year DESC LIMIT 1", (fcid,))
    dd = load("SELECT dependency_pct, import_kt, production_kt, year FROM dependency_ratios "
              "WHERE commodity_id = ? AND dependency_pct IS NOT NULL "
              "ORDER BY year DESC LIMIT 1", (fcid,))
    if rr.empty:
        continue
    r0 = rr.iloc[0]
    dep = None if dd.empty else dd.iloc[0]
    feed_rows.append({
        "Feed corridor": r0["name"],
        "Import dependency": f"{dep.dependency_pct:.0f}%" if dep is not None else "n/a",
        "Corridor risk": f"{r0.composite_risk:.0f} / 100",
        "Top origin (concentration)": f"{r0.top_origin} {r0.top_origin_share*100:.0f}%",
        "Data year": vintage_label(r0.year, r0.source),
    })
st.caption("QUTVIEW-computed corridor figures for the UAE's animal-feed imports "
           "(the complete feed complex — energy grain, protein meal, and barley):")
st.dataframe(pd.DataFrame(feed_rows), width="stretch", hide_index=True)
st.info(
    "**Why this matters — sourced context, not a QUTVIEW-computed figure:** "
    "the UAE banned domestic fodder cultivation in 2006 to preserve groundwater "
    "and imports **90%+ of animal feed** (USDA Grain & Feed Annual). So the "
    "country's *domestically-produced* poultry, dairy, and meat run on these "
    "imported feed corridors — meaning even the “local” share of those "
    "foods is not truly self-sufficient. The dependency and risk numbers above "
    "are QUTVIEW's own grounded output; the 2006 ban and the 90% figure are "
    "cited context. We do **not** compute a feed-tonnage → local-output link "
    "— no such data exists in the system, so none is claimed."
)

st.divider()

# ---------------- commodity detail ----------------
cid = st.selectbox(
    "Commodity detail",
    list(COMMODITIES),
    format_func=lambda c: COMMODITIES[c]["name"],
    key="commodity_detail",
)
detail = risks[risks["commodity_id"] == cid].iloc[0]
detail_year = int(detail.year)
detail_mirror = detail.source == "mirror_derived"

left, right = st.columns([1, 2])

with left:
    st.markdown(f"### {COMMODITIES[cid]['name']} — {vintage_label(detail_year, detail.source)}")
    if cid == "dairy":
        st.caption(
            "**Scope & price basis (dairy):** the corridor is milk powder (HS 0402), "
            "the storable, tradeable form the UAE imports. The UAE produces fresh "
            "milk domestically (~266 kt/yr raw milk, FAOSTAT) but essentially no milk "
            "powder — so the 100% dependency is for **powder specifically**, not all "
            "dairy. The price is the **FAO Dairy Price Index (2014–16 = 100)** — an "
            "index of broad dairy (skim/whole milk powder are major components, a "
            "reasonable price proxy for HS 0402), not a $/kg price; volatility, "
            "forecast, and momentum are returns-based so the index works unchanged."
        )
    if cid == "mutton":
        st.caption(
            "**Scope & price basis (sheep & goat meat):** dependency (≈51%) is for "
            "sheep + goat meat combined (HS 0204). Domestic production is ~93% goat; "
            "imports are predominantly sheep/lamb (Australia-led) — so the aggregate "
            "**understates** lamb-specific dependency (conservative, never overstated). "
            "Price uses the World Bank **“Lamb” (sheep) series ($/kg)** as a "
            "disclosed proxy for the combined corridor."
        )
    if cid == "mutton":
        st.caption(
            "**Scope & dependency basis (sheep & goat meat):** dependency (~51%) is for "
            "sheep + goat meat combined (HS 0204). Domestic production is ~93% goat; "
            "imports are predominantly sheep/lamb (Australia-led) — so the aggregate "
            "**understates** lamb-specific dependency (the conservative direction). "
            "Price uses the World Bank **\"Lamb\" (sheep) series ($/kg)** as a disclosed "
            "proxy for the combined corridor."
        )
    if cid == "lentils":
        st.caption(
            "**Scope & price basis (lentils):** the corridor is lentils (HS 0713.40). "
            "The price is the **US PPI for Dry Peas & Lentils (index, 2015 = 100)** — "
            "an index (lentils are a core component; peas are a related pulse, a "
            "reasonable proxy), not a $/kg price; volatility, forecast, and momentum "
            "are returns-based so the index works unchanged. Chickpeas (HS 0713.20) "
            "are a separate corridor this index does not cover — not blended in here, "
            "and not yet tracked (no clean chickpea price source)."
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
        title=f"Import value by origin · {vintage_label(detail_year, detail.source)}",
        xaxis_title="Import value ($M)",
        height=340, margin=dict(l=120, r=20, t=44, b=44),
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

# ---------------- what moved (A4) ----------------
@st.cache_data(ttl=300)
def load_drivers(commodity_id: str, stamp: str):
    with sqlite3.connect(DB_PATH) as conn:
        return assemble_facts(conn, commodity_id)["drivers"]


def render_what_moved(drivers: dict) -> None:
    mo, fd = drivers["price_momentum"], drivers["flow_decomposition"]
    if mo:
        m6, m12 = mo["m6_pct"], mo["m12_pct"]
        extra = f", {m12:+.1f}% over 12 months" if m12 is not None else ""
        st.markdown(f"**World price momentum:** {mo['class_6mo']} — "
                    f"{m6:+.1f}% over 6 months{extra}")
        st.caption("Context only — a price move *accompanies* corridor stress; it is "
                   "not asserted as the cause of the concentration.")
    if fd:
        inc = fd["incumbent"]
        inc_txt = ""
        if inc and inc["change_pct"] is not None:
            inc_txt = (f"incumbent {inc['origin']} {inc['change_pct']:+.0f}% "
                       f"(${inc['prev_value']/1e6:.0f}M→${inc['latest_value']/1e6:.0f}M)")
        gains = []
        for m in fd["risers"][:2]:
            pct = f"{m['change_pct']:+.0f}%" if m["change_pct"] is not None else "new"
            gains.append(f"{m['origin']} {pct} (+${m['change_usd']/1e6:.0f}M)")
        st.markdown(md_safe(
            f"**Flow attribution ({fd['prev_year']}→{fd['latest_year']} supply "
            f"mix):** " + inc_txt
            + (("; top gains: " + ", ".join(gains)) if gains else "")))
        unrep = fd["unreported"]
        if unrep:
            named = ", ".join(o["origin"] for o in unrep["origins"][:3])
            named_val = sum(o["prev_value"] for o in unrep["origins"][:3])
            st.warning(md_safe(
                f"**Reporting-lag guard:** {unrep['count']} origins "
                f"(${unrep['prev_value_usd']/1e6:.0f}M in {fd['prev_year']}) have no "
                f"{fd['latest_year']} data yet — including {named} "
                f"(${named_val/1e6:.0f}M of the ${unrep['prev_value_usd']/1e6:.0f}M). "
                f"Excluded from decline attribution as likely reporting lag, not real "
                f"exits — so an apparent concentration rise or origin shift here is "
                f"provisional."))


st.divider()
st.subheader("What moved")
render_what_moved(load_drivers(cid, data_stamp))


# ---------------- diversification candidates (A3) ----------------
@st.cache_data(ttl=300)
def load_alternatives(commodity_id: str, stamp: str):
    with sqlite3.connect(DB_PATH) as conn:
        return assemble_facts(conn, commodity_id)["alternatives"]


alts = load_alternatives(cid, data_stamp)
has_single_source = any(
    a["commodity"] == cid and a["rule"] == "single_source" for a in alerts)


def render_alternatives(alts: dict, mirror: bool) -> None:
    inc = alts["incumbent"]
    if inc:
        iuv = inc["implied_unit_value_usd_per_kg"]
        st.caption(
            f"Incumbent: **{inc['origin']}** at {inc['share']*100:.0f}% of supply"
            + (f" · implied unit value {iuv:.2f} USD/kg" if iuv is not None else ""))
    if not alts["has_alternatives"]:
        st.warning(f"**Finding:** {alts['note']}. This corridor cannot be "
                   f"diversified from origins already in the trade data.")
        return
    tag = " *(provisional, mirror-derived)*" if mirror else ""
    table = pd.DataFrame([{
        "Origin": c["origin"],
        "Share": f"{c['share']*100:.0f}%",
        "Years present": f"{c['years_present']}/{c['total_years']}",
        "Trend": c["trend"],
        "Implied unit value (USD/kg)†": (
            f"{c['implied_unit_value_usd_per_kg']:.2f}"
            if c["implied_unit_value_usd_per_kg"] is not None else "n/a"),
    } for c in alts["candidates"]])
    st.markdown(f"**Diversification candidates — origins already shipping to the "
                f"UAE{tag}**")
    st.dataframe(table, width="stretch", hide_index=True)
    st.caption(f"† {alts['unit_value_caveat']}. A candidate can look cheaper purely "
               f"because it ships a different product mix — not a price promise, "
               f"not landed cost (freight/duties are roadmap). Candidates are proven "
               f"corridors (>=1% of the latest year); hypothetical suppliers with no "
               f"UAE trade need global-export data (future step).")


st.divider()
st.subheader("Diversification candidates")
if has_single_source:
    render_alternatives(alts, detail_mirror)
else:
    with st.expander("Show alternative-sourcing candidates for this corridor",
                     expanded=False):
        render_alternatives(alts, detail_mirror)

# ---------------- generated intelligence brief ----------------
st.divider()
st.subheader("Generated intelligence brief")


@st.cache_data(ttl=3600, show_spinner="Drafting corridor brief...")
def cached_brief(commodity_id: str, data_stamp: str):
    """data_stamp keys the cache to the last pipeline refresh, so briefs
    regenerate when the data changes but an LLM call is never repeated for
    the same commodity on the same data."""
    with sqlite3.connect(DB_PATH) as conn:
        facts = assemble_facts(conn, commodity_id)
    text, mode, detail = generate_brief(facts)
    # Return the fact dict too — the PDF export is built from it (same single
    # source of truth as the brief), so nothing is recomputed downstream.
    return facts, text, mode, detail


facts, brief_text, brief_mode, brief_detail = cached_brief(cid, data_stamp)

badge = ("🤖 LLM-GENERATED · " + brief_detail if brief_mode == "llm"
         else "📋 TEMPLATE (deterministic, no AI) · " + brief_detail)
st.caption(f"Brief source — {badge} · draft for human review, not a decision")

# The brief's last line is a static "Analyst decision: [ ] approve [ ] revise"
# placeholder — drop it from the display and replace with a working control.
brief_body = brief_text.split("Analyst decision:")[0].rstrip()
st.text(brief_body)


def load_decision(commodity_id, stamp):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT decision, note, decided_at FROM brief_decisions "
            "WHERE commodity_id = ? AND data_stamp = ?", (commodity_id, str(stamp))
        ).fetchone()
    return dict(zip(("decision", "note", "decided_at"), row)) if row else None


def record_decision(commodity_id, stamp, decision, note):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO brief_decisions "
            "(commodity_id, data_stamp, decision, note, decided_at) "
            "VALUES (?,?,?,?, datetime('now'))",
            (commodity_id, str(stamp), decision, note or None))
        conn.commit()


# ---- working analyst decision: human signs off, logged with an audit trail ----
st.markdown("**Analyst decision** — the AI drafts and recommends; a human approves.")
dec = load_decision(cid, data_stamp)
if dec:
    if dec["decision"] == "approved":
        st.success(f"✓ Approved by analyst · {dec['decided_at']} UTC")
    else:
        st.warning(f"✎ Revision requested · {dec['decided_at']} UTC"
                   + (f" — “{dec['note']}”" if dec["note"] else ""))
else:
    st.caption("No decision recorded yet for this corridor on the current data.")

d1, d2 = st.columns(2)
if d1.button("✓ Approve", key=f"approve_{cid}", width="stretch"):
    record_decision(cid, data_stamp, "approved", None)
    st.rerun()
rev_note = d2.text_input("Revision note (optional)", key=f"revnote_{cid}",
                         placeholder="Reason for revision…", label_visibility="collapsed")
if d2.button("✎ Request revision", key=f"revise_{cid}", width="stretch"):
    record_decision(cid, data_stamp, "revision_requested", rev_note)
    st.rerun()
st.caption("The decision is tied to this data version — a pipeline refresh clears "
           "it so the corridor is re-reviewed on the new numbers, never carrying a "
           "stale approval.")

# ---- download the corridor report (walk through / share offline) ----
_dep_line = ""
if not dep.empty and pd.notna(dep.dependency_pct[0]):
    _dep_line = f"- Import dependency: {float(dep.dependency_pct[0]):.0f}%\n"
report_md = (
    f"# QUTVIEW corridor report — {COMMODITIES[cid]['name']}\n\n"
    f"Data year {detail_year} "
    f"({'provisional, mirror-derived' if detail_mirror else 'UAE-reported'}) · "
    f"brief source: {brief_mode.upper()} ({brief_detail})\n\n"
    f"## Headline\n"
    f"- Composite risk: {detail.composite_risk:.1f} / 100 (HHI {detail.hhi:.2f})\n"
    f"- Top origin: {detail.top_origin} ({detail.top_origin_share*100:.0f}%), "
    f"{int(detail.n_origins)} origins\n"
    f"{_dep_line}"
    + (f"- Forecast backtest MAPE: {float(bt.mape_pct[0]):.1f}%\n" if not bt.empty else "")
    + f"\n## Intelligence brief\n\n{brief_body}\n\n"
    f"## Analyst decision\n\n"
    + (f"{dec['decision']} · {dec['decided_at']} UTC"
       + (f" — {dec['note']}" if dec.get('note') else "")
       if dec else "not yet reviewed") + "\n"
)
# A polished, print-ready PDF is the artifact a procurement contact can forward
# to a director — built from the same fact dict as the brief (no recomputation),
# with provenance and caveats carried through. The .md stays for a plain-text /
# machine-readable copy.
@st.cache_data(ttl=3600, show_spinner=False)
def cached_pdf(commodity_id, stamp, vintage):
    facts_, text_, mode_, detail_ = cached_brief(commodity_id, stamp)
    return build_brief_pdf(facts_, text_, mode_, detail_, vintage)


pdf_bytes = cached_pdf(cid, data_stamp, vintage_label(detail_year, detail.source))
dl1, dl2 = st.columns(2)
dl1.download_button("⬇ Download corridor brief (PDF)", pdf_bytes,
                    file_name=f"QUTVIEW_{cid}_{detail_year}.pdf",
                    mime="application/pdf", key=f"dlpdf_{cid}", width="stretch")
dl2.download_button("⬇ Download corridor report (.md)", report_md,
                    file_name=f"qutview_{cid}_report.md", mime="text/markdown",
                    key=f"dl_{cid}", width="stretch")

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
            title="Who supplies this commodity, month by month",
            xaxis_title="Month", yaxis_title="Import value ($M)",
            height=460, margin=dict(l=64, r=20, t=48, b=96),
            showlegend=True,
            legend=dict(title="Origin", orientation="h", yanchor="top", y=-0.28,
                        xanchor="left", x=0),
            **CHART_BG,
        )
        st.plotly_chart(fig3, width="stretch")
        st.caption("Monthly mirror-derived flows by origin (top corridors, "
                   "origin-reported). Each band is one origin country — colour maps "
                   "to the country in the legend.")

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
                title="Is corridor risk rising or falling?",
                xaxis_title="Month", yaxis_title="Risk score (0–100)",
                height=360, margin=dict(l=64, r=20, t=48, b=52),
                yaxis=dict(range=[0, 100]), **CHART_BG,
            )
            st.plotly_chart(fig4, width="stretch")
            st.caption("Rolling 12-month composite risk (0–100).")

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
