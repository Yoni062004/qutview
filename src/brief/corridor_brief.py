"""Generative corridor intelligence brief (roadmap item A1).

Turns the database into a short written brief with one recommended action
per commodity corridor. Two clean layers, kept strictly separate:

  1. FACT ASSEMBLER — assemble_facts(): pure, deterministic Python. Pulls
     every number the brief is allowed to use out of the star schema into
     one dict, recording anything missing under "gaps". This dict is the
     single source of truth; nothing downstream may introduce a number
     that is not in it.
  2. BRIEF GENERATOR — two modes over the same fact dict:
     - LLM mode (preferred): Claude via the Anthropic API, key from
       ANTHROPIC_API_KEY in .env, model from ANTHROPIC_MODEL (default
       claude-sonnet-5). The model receives ONLY the fact dict and a
       system prompt that forbids inventing numbers. No sampling
       parameters are sent — current Claude models reject them; instead
       every LLM brief passes a mechanical GROUNDING CHECK: each number
       in the text must trace to a fact-dict value (allowing the brief's
       rounding and unit scaling), or the output is rejected and the
       deterministic template is used, with the reason surfaced.
     - Template mode (fallback): a deterministic rule-based text builder,
       so the module always runs offline (same philosophy as the
       sample-data fallbacks elsewhere in the project). Used when no key
       is configured or the API call fails; the output says which mode
       produced it.

Provenance discipline: the brief states whether its year is UAE-reported
or mirror-derived/provisional, and for corridors held back by the mirror
coverage gate (see features/risk_indicators.py) it says why newer years
are not scored instead of pretending they don't exist.

Decision-support constraint: the brief is a DRAFT for a human procurement
officer to approve — it recommends, it never decides or claims an action
was taken. Both modes carry the DRAFT header, phrase the action as
"Recommended for review", and end with an analyst sign-off line; the LLM
mode must additionally embed DECISION_SUPPORT_RULE in its system prompt.

Run:  python src/brief/corridor_brief.py wheat            (brief only)
      python src/brief/corridor_brief.py wheat --facts    (fact dict + brief)
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import COMMODITIES, get_anthropic_key, get_brief_model, get_connection
from features.risk_indicators import MIN_MIRROR_COVERAGE_PCT

# Non-negotiable framing rule. Baked into the template output below and
# embedded verbatim in the LLM system prompt.
DECISION_SUPPORT_RULE = (
    "You draft analysis and RECOMMEND an action for a human procurement "
    "officer to approve; you never state or imply an action was taken, and "
    "you never present yourself as the decision-maker. The human is always "
    "in the loop."
)

HEADER_LINE = "DRAFT — corridor intelligence for procurement review"
SIGNOFF_LINE = "Analyst decision: [ ] approve  [ ] revise"

LLM_SYSTEM_PROMPT = f"""You write corridor intelligence briefs for QUTVIEW, \
a UAE food-import risk monitor.

{DECISION_SUPPORT_RULE}

Non-negotiable rules:
- Use ONLY numbers present in the provided fact JSON. Never invent a figure, \
a supplier, a freight rate, or a landed cost. Every quantitative claim must \
trace to a provided field.
- If something is missing (see "gaps"), say so plainly instead of guessing.
- State provenance plainly: if provenance.provisional is true, the data year \
is reconstructed from mirror statistics and the brief must say so; if the \
mirror gate did not pass and newer_mirror_years_unscored is non-empty, \
explain that newer years exist in mirror data but are deliberately not \
scored because coverage is below the threshold.
- "What moved" framing (drivers section): price_momentum is CONTEXT — phrase \
it as "accompanied by" or "alongside" a price move, NEVER "because of". \
Correlation is not causation. flow_decomposition is ARITHMETIC ATTRIBUTION \
within our own trade data — "driven by" is acceptable but scope it strictly \
to the supply mix / share, never to world events. If \
flow_decomposition.unreported is present, the origins there dropped to $0 \
because they have not reported the latest year yet (reporting lag) — say so; \
do NOT describe them as having fallen, collapsed, or exited. NEVER speculate \
about real-world causes (war, drought, sanctions, policy): report only what \
moved in the numbers and let the human analyst supply the world.
- Format, plain text only (no markdown):
  line 1 exactly: {HEADER_LINE}
  then a brief of at most 200 words covering risk posture (with the \
previous-year comparison), origin concentration and supply mix, import \
dependency, price/forecast, and one "What moved" observation from the \
drivers section under the framing rules above.
  then one line starting "Recommended for review:" with ONE concrete action \
grounded in the data — name the numbers that justify it, no vague advice.
  then one line starting "Confidence & caveats:" including the forecast \
backtest MAPE, the annual mirror coverage, and any gaps.
  last line exactly: {SIGNOFF_LINE}"""

# Alternative-sourcing (A3) tuning — named, not magic numbers in logic.
ALT_MEANINGFUL_SHARE = 0.01  # a candidate must supply >= 1% of the latest year
                             # to count as a proven corridor, not a one-off
ALT_MAX_CANDIDATES = 5       # cap the shortlist
ALT_TREND_BAND = 0.15        # +/-15% relative move vs earlier years = rising/falling

# "Why it moved" drivers (A4) — price-momentum classification of the trailing
# 6-month world-price move. Context only: a price move accompanies corridor
# stress, it is not asserted as its cause.
MOM_SHARP_RISE = 0.15   # >= +15% over 6 months
MOM_RISE = 0.05         # >= +5%
MOM_FALL = -0.05        # <= -5%
MOM_SHARP_FALL = -0.15  # <= -15%
MOM_12MO_MATERIAL = 0.10  # a 12-month move worth flagging as context when 6mo is flat
FLAT_INCUMBENT_PCT = 5    # |incumbent value change| under this = "roughly flat"
# A riser is only named as a "driver" if it is BOTH big in absolute terms and a
# non-trivial share of the year's market — otherwise a rounding-error gain in a
# collapsed market gets false top billing (soybean meal 2025: Türkiye +$0.25M).
MATERIAL_RISER_USD = 1_000_000     # >= $1M absolute
MATERIAL_RISER_SHARE = 0.02        # AND >= 2% of the latest-year market
MARKET_CONTRACTION_PCT = 0.10      # when no origin grew materially and the market
                                   # shrank at least this much, the contraction
                                   # is the story (likely reporting lag)

# ---------------------------------------------------------------- layer 1


def _row(conn, query, params=()):
    cur = conn.execute(query, params)
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([c[0] for c in cur.description], row))


def _implied_unit_value(value, weight):
    """Trade value / shipped weight for one origin-year, in USD/kg. NOT a
    landed cost and NOT a price — product mix differs by origin, so it is
    only an honest like-for-like comparison basis. None when weight is
    missing or zero."""
    if value and weight and weight > 0:
        return value / weight
    return None


def _build_alternatives(conn, cid, flows_table, latest_year, top_origin, total_value):
    """Rank NON-top origins already shipping to the UAE as diversification
    candidates, from real trade rows only. Ranking rule (transparent, no
    black box): years_present desc, then latest-year share desc. Candidates
    below ALT_MEANINGFUL_SHARE are dropped; if none remain, that absence is
    itself the finding. Hypothetical suppliers with no UAE trade rows are
    out of scope (needs global-export data — a future step)."""
    rows = conn.execute(
        f"""SELECT c.name, f.year, f.trade_value_usd, f.net_weight_kg
            FROM {flows_table} f JOIN dim_country c ON c.country_code = f.origin_code
            WHERE f.commodity_id = ? AND f.trade_value_usd > 0""", (cid,)).fetchall()

    year_totals, per_origin = {}, {}
    for name, year, value, weight in rows:
        year_totals[year] = year_totals.get(year, 0) + value
        per_origin.setdefault(name, {})[year] = (value, weight)
    total_years = len(year_totals)

    def share_in(year, value):
        t = year_totals.get(year, 0)
        return value / t if t else 0.0

    def trend_for(name):
        # Latest-year share vs the mean of earlier years this origin shipped.
        yv = per_origin[name]
        latest = yv.get(latest_year)
        if latest is None:
            return "absent in latest year"
        prior = [share_in(y, v) for y, (v, _) in yv.items() if y < latest_year]
        if not prior:
            return "new (first appearance)"
        cur = share_in(latest_year, latest[0])
        mean_prior = sum(prior) / len(prior)
        if mean_prior == 0:
            return "new (first appearance)"
        if cur >= mean_prior * (1 + ALT_TREND_BAND):
            return "rising"
        if cur <= mean_prior * (1 - ALT_TREND_BAND):
            return "falling"
        return "stable"

    candidates = []
    for name, yv in per_origin.items():
        if name == top_origin or latest_year not in yv:
            continue
        value, weight = yv[latest_year]
        share = value / total_value if total_value else 0.0
        if share < ALT_MEANINGFUL_SHARE:
            continue
        candidates.append({
            "origin": name,
            "share": share,
            "value_usd": value,
            "years_present": sum(1 for _ in yv),
            "total_years": total_years,
            "trend": trend_for(name),
            "implied_unit_value_usd_per_kg": _implied_unit_value(value, weight),
        })
    candidates.sort(key=lambda c: (-c["years_present"], -c["share"]))
    candidates = candidates[:ALT_MAX_CANDIDATES]

    # Incumbent on the same honest basis, so candidates can be compared.
    incumbent = None
    top_yv = per_origin.get(top_origin, {}).get(latest_year)
    if top_yv:
        tv, tw = top_yv
        incumbent = {
            "origin": top_origin,
            "share": tv / total_value if total_value else 0.0,
            "implied_unit_value_usd_per_kg": _implied_unit_value(tv, tw),
        }

    return {
        "candidates": candidates,
        "incumbent": incumbent,
        "total_years": total_years,
        "has_alternatives": bool(candidates),
        "note": (None if candidates else
                 "no proven alternative corridor exists in current trade data "
                 "(all other origins under 1% of the latest year)"),
        "unit_value_caveat": ("implied unit value = trade value / shipped weight; "
                              "NOT landed cost — product mix differs by origin"),
    }


def _shift_period(period, months):
    y, m = int(period[:4]), int(period[5:7])
    idx = y * 12 + (m - 1) - months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _classify_momentum(frac):
    if frac is None:
        return None
    if frac >= MOM_SHARP_RISE:
        return "sharp rise"
    if frac >= MOM_RISE:
        return "rising"
    if frac <= MOM_SHARP_FALL:
        return "sharp fall"
    if frac <= MOM_FALL:
        return "falling"
    return "stable"


def _price_momentum(conn, cid):
    """Trailing 3/6/12-month % change in the world price — pure arithmetic on
    stored prices. Context signal only (see the framing rules)."""
    prices = dict(conn.execute(
        "SELECT period, price FROM fact_prices WHERE commodity_id = ?", (cid,)).fetchall())
    if not prices:
        return None
    latest_p = max(prices)
    latest_v = prices[latest_p]
    out = {"latest_period": latest_p, "latest_price": latest_v}
    for n in (3, 6, 12):
        ref_v = prices.get(_shift_period(latest_p, n))
        out[f"m{n}_pct"] = (100 * (latest_v - ref_v) / ref_v) if ref_v else None
    six = out["m6_pct"]
    out["class_6mo"] = _classify_momentum(six / 100 if six is not None else None)
    return out


def _flow_decomposition(conn, cid, flows_table, latest_year, provisional, top_origin):
    """Arithmetic attribution of the value change per origin, latest data year
    vs the previous year in the SAME flows table (so provenance is consistent).
    On a provisional/mirror year the $0-drop guard applies: origins present in
    the prior year but at exactly $0 in the latest year are excluded from
    faller attribution and disclosed separately as likely reporting lag, not
    counted as real declines."""
    prev_row = conn.execute(
        f"SELECT max(year) FROM {flows_table} WHERE commodity_id = ? AND year < ?",
        (cid, latest_year)).fetchone()
    prev_year = prev_row[0]
    if prev_year is None:
        return None
    rows = conn.execute(
        f"""SELECT c.name, f.year, f.trade_value_usd FROM {flows_table} f
            JOIN dim_country c ON c.country_code = f.origin_code
            WHERE f.commodity_id = ? AND f.year IN (?, ?) AND f.trade_value_usd > 0""",
        (cid, latest_year, prev_year)).fetchall()
    by_origin = {}
    for name, year, value in rows:
        by_origin.setdefault(name, {})[year] = value

    def mover(name):
        yv = by_origin[name]
        prev_v, latest_v = yv.get(prev_year, 0.0), yv.get(latest_year, 0.0)
        return {
            "origin": name, "prev_value": prev_v, "latest_value": latest_v,
            "change_usd": latest_v - prev_v,
            "change_pct": (100 * (latest_v - prev_v) / prev_v) if prev_v else None,
        }

    movers = [mover(n) for n in by_origin]

    # $0-drop guard (provisional years only).
    unreported = None
    if provisional:
        drops = [m for m in movers if m["latest_value"] == 0 and m["prev_value"] > 0]
        if drops:
            drops.sort(key=lambda m: -m["prev_value"])
            unreported = {
                "count": len(drops),
                "prev_value_usd": sum(m["prev_value"] for m in drops),
                "origins": [{"origin": m["origin"], "prev_value": m["prev_value"]}
                            for m in drops[:5]],
            }
        zero_drop = {m["origin"] for m in drops}
    else:
        zero_drop = set()

    risers = sorted((m for m in movers if m["change_usd"] > 0),
                    key=lambda m: -m["change_usd"])[:3]
    fallers = sorted((m for m in movers
                      if m["change_usd"] < 0 and m["origin"] not in zero_drop),
                     key=lambda m: m["change_usd"])[:3]
    incumbent = next((m for m in movers if m["origin"] == top_origin), None)
    total_prev = sum(m["prev_value"] for m in movers)
    total_latest = sum(m["latest_value"] for m in movers)
    return {
        "latest_year": latest_year, "prev_year": prev_year,
        "provisional": provisional,
        "incumbent": incumbent, "risers": risers, "fallers": fallers,
        "unreported": unreported,
        "total_prev": total_prev, "total_latest": total_latest,
        "market_change_pct": (100 * (total_latest - total_prev) / total_prev
                              if total_prev else None),
    }


def assemble_facts(conn, cid: str) -> dict:
    """Every number the brief may use, from the DB, with gaps recorded.
    Deterministic, no AI, no derived guesses beyond arithmetic."""
    if cid not in COMMODITIES:
        sys.exit(f"Unknown commodity '{cid}'. Choose from: {', '.join(COMMODITIES)}")
    gaps = []

    latest = _row(conn, """SELECT year, source, composite_risk, hhi, top_origin,
                                  top_origin_share, n_origins, price_volatility
                           FROM risk_scores WHERE commodity_id = ?
                           ORDER BY year DESC LIMIT 1""", (cid,))
    if latest is None:
        sys.exit(f"No risk scores for {cid} — run the pipeline first.")

    previous = _row(conn, """SELECT year, composite_risk, top_origin, top_origin_share
                             FROM risk_scores WHERE commodity_id = ? AND year < ?
                             ORDER BY year DESC LIMIT 1""", (cid, latest["year"]))
    if previous is None:
        gaps.append("no previous-year risk score to compare against")

    monthly = None
    mrows = conn.execute(
        """SELECT period, composite_risk FROM risk_scores_monthly
           WHERE commodity_id = ? ORDER BY period""", (cid,)).fetchall()
    if mrows:
        latest_m, prev_m = mrows[-1], (mrows[-2] if len(mrows) > 1 else None)
        six_m = mrows[-7] if len(mrows) > 6 else None
        change = (latest_m[1] - six_m[1]) if six_m else (
            latest_m[1] - prev_m[1] if prev_m else None)
        monthly = {
            "latest_period": latest_m[0],
            "latest_risk": latest_m[1],
            "change_window_months": 6 if six_m else (1 if prev_m else None),
            "change": change,
            "direction": (None if change is None else
                          "rising" if change > 1 else
                          "falling" if change < -1 else "stable"),
        }
    else:
        gaps.append("no monthly rolling risk series (mirror monitor not loaded)")

    flows_table = ("fact_imports_mirror_annual" if latest["source"] == "mirror_derived"
                   else "fact_imports")
    origin_rows = conn.execute(
        f"""SELECT c.name, f.trade_value_usd FROM {flows_table} f
            JOIN dim_country c ON c.country_code = f.origin_code
            WHERE f.commodity_id = ? AND f.year = ? AND f.trade_value_usd > 0
            ORDER BY f.trade_value_usd DESC""", (cid, latest["year"])).fetchall()
    total_value = sum(v for _, v in origin_rows)
    top_origins = [
        {"origin": name, "value_usd": value, "share": value / total_value}
        for name, value in origin_rows[:5]
    ]
    alternatives = _build_alternatives(
        conn, cid, flows_table, latest["year"],
        latest["top_origin"], total_value)

    momentum_price = _price_momentum(conn, cid)
    if momentum_price is None:
        gaps.append("no price series for momentum")
    flow_decomp = _flow_decomposition(
        conn, cid, flows_table, latest["year"],
        latest["source"] == "mirror_derived", latest["top_origin"])
    if flow_decomp is None:
        gaps.append("no previous year to decompose flow changes against")

    dependency = _row(conn, """SELECT year, dependency_pct, import_kt, production_kt
                               FROM dependency_ratios
                               WHERE commodity_id = ? AND dependency_pct IS NOT NULL
                               ORDER BY year DESC LIMIT 1""", (cid,))
    if dependency is None:
        gaps.append("no import-dependency data (FAOSTAT production not loaded)")
    exposure_adjusted = (latest["composite_risk"] * dependency["dependency_pct"] / 100
                         if dependency else None)

    price = _row(conn, """SELECT period, price FROM fact_prices
                          WHERE commodity_id = ? ORDER BY period DESC LIMIT 1""", (cid,))
    if price is None:
        gaps.append("no price series")

    forecast = None
    fc = _row(conn, """SELECT period, forecast FROM forecasts
                       WHERE commodity_id = ? ORDER BY period DESC LIMIT 1""", (cid,))
    if fc and price and price["price"]:
        pct = 100 * (fc["forecast"] - price["price"]) / price["price"]
        forecast = {
            "end_period": fc["period"], "value": fc["forecast"],
            "pct_vs_latest_price": pct,
            "direction": "rising" if pct > 1 else "falling" if pct < -1 else "roughly flat",
        }
    else:
        gaps.append("no price forecast")

    backtest = _row(conn, """SELECT horizon_months, mape_pct FROM backtest_metrics
                             WHERE commodity_id = ?""", (cid,))
    if backtest is None:
        gaps.append("no forecast backtest — forecast accuracy unknown")

    gate = _row(conn, """SELECT year, coverage_pct FROM mirror_coverage_annual
                         WHERE commodity_id = ? AND coverage_pct IS NOT NULL
                         ORDER BY year DESC LIMIT 1""", (cid,))
    newer_mirror_years = [y for (y,) in conn.execute(
        """SELECT DISTINCT year FROM fact_imports_mirror_annual
           WHERE commodity_id = ? AND year > ? ORDER BY year""",
        (cid, latest["year"]))]

    return {
        "commodity": {"id": cid, "name": COMMODITIES[cid]["name"],
                      "hs_code": COMMODITIES[cid]["hs"], "price_unit": COMMODITIES[cid]["unit"]},
        "risk_latest": latest,
        "risk_previous": previous,
        "monthly_trend": monthly,
        "top_origins": top_origins,
        "total_import_value_usd": total_value,
        "alternatives": alternatives,
        "drivers": {"price_momentum": momentum_price,
                    "flow_decomposition": flow_decomp},
        "dependency": dependency,
        "exposure_adjusted_risk": exposure_adjusted,
        "latest_price": price,
        "forecast": forecast,
        "backtest": backtest,
        "provenance": {
            "source": latest["source"],
            "provisional": latest["source"] == "mirror_derived",
            "mirror_gate": (None if gate is None else {
                "gate_year": gate["year"],
                "coverage_pct": gate["coverage_pct"],
                "threshold_pct": MIN_MIRROR_COVERAGE_PCT,
                "passed": gate["coverage_pct"] >= MIN_MIRROR_COVERAGE_PCT,
            }),
            "newer_mirror_years_unscored": newer_mirror_years,
        },
        "gaps": gaps,
    }


# ---------------------------------------------------------------- layer 2


def _pct(share: float) -> str:
    """Format a 0..1 share; tiny-but-real origins read '<1%', never '0%'."""
    return "<1%" if share < 0.005 else f"{share*100:.0f}%"


def _m(value_usd) -> str:
    return f"{value_usd/1e6:.0f}M"


def concentration_driver_note(f: dict):
    """Short arithmetic driver behind a concentration change, honest about the
    $0-drop reporting-lag effect. Shared by the brief and the alert feed so
    they never tell different stories. None when no decomposition exists."""
    fd = f["drivers"]["flow_decomposition"]
    if not fd:
        return None
    inc, unrep = fd["incumbent"], fd["unreported"]
    prev = f["risk_previous"]
    unrep_prev = ({o["origin"]: o["prev_value"] for o in unrep["origins"]}
                  if unrep else {})

    # Case 1 — the PREVIOUS top origin is itself unreported: the apparent
    # top-origin shift is substantially reporting lag, not a real supply-base
    # change. (Sunflower: Ukraine 2024 top, no 2025 data yet.)
    if prev and prev.get("top_origin") in unrep_prev:
        po = prev["top_origin"]
        riser = ""
        if fd["risers"]:
            r0 = fd["risers"][0]
            rp = f"+{r0['change_pct']:.0f}%" if r0["change_pct"] is not None else "new supplier"
            riser = (f"; real growth is {r0['origin']} {rp} "
                     f"(+${_m(r0['change_usd'])}) among reporting origins")
        return (f"the previous top origin {po} (${_m(unrep_prev[po])} in "
                f"{fd['prev_year']}) has no {fd['latest_year']} data yet — likely "
                f"reporting lag, so the apparent shift is provisional{riser}")

    inc_flat = (inc and inc["change_pct"] is not None
                and abs(inc["change_pct"]) < FLAT_INCUMBENT_PCT)
    if inc_flat and unrep:
        # Case 2 — Poultry: share rose but the incumbent was flat, so the rise
        # is the shrinking denominator from origins that have not reported yet.
        return (f"{inc['origin']}'s value was roughly flat "
                f"({_m(inc['prev_value'])}->{_m(inc['latest_value'])}); the share rise "
                f"reflects {unrep['count']} origins (${_m(unrep['prev_value_usd'])} in "
                f"{fd['prev_year']}) with no {fd['latest_year']} data yet — likely "
                f"reporting lag, not real movement")
    # Case 3 — a named "driver" must clear the materiality floor (big in $ AND a
    # non-trivial share of the year's market). A rounding-error gain in a
    # collapsed market must NOT get top billing.
    r0 = fd["risers"][0] if fd["risers"] else None
    tp, tl = fd.get("total_prev"), fd.get("total_latest")
    r0_material = (r0 is not None and r0["change_usd"] >= MATERIAL_RISER_USD
                   and tl and r0["change_usd"] >= MATERIAL_RISER_SHARE * tl)
    if r0_material:
        rp = f"+{r0['change_pct']:.0f}%" if r0["change_pct"] is not None else "new supplier"
        bits = [f"{r0['origin']} {rp} (+${_m(r0['change_usd'])})"]
        if inc and inc["change_pct"] is not None and inc["change_pct"] <= -FLAT_INCUMBENT_PCT:
            bits.append(f"{inc['origin']} {inc['change_pct']:+.0f}%")
        # Anchor the flow window so the (possibly older) data-year change is never
        # conflated with the current-month price momentum beside it.
        note = ("driven by " + ", ".join(bits)
                + f" in the {fd['prev_year']}->{fd['latest_year']} supply mix")
    elif tp and tl and tl <= tp * (1 - MARKET_CONTRACTION_PCT):
        # No origin grew materially and the reported market shrank — the
        # contraction is the story, not the noise.
        note = (f"the {fd['latest_year']} reported market contracted "
                f"{(tp - tl) / tp * 100:.0f}% (${_m(tp)}->${_m(tl)}), likely reporting "
                f"lag; no origin grew materially")
    elif inc and inc["change_pct"] is not None and abs(inc["change_pct"]) >= FLAT_INCUMBENT_PCT:
        # No material riser, market not contracted: describe the incumbent move.
        note = (f"{inc['origin']} {inc['change_pct']:+.0f}% in the "
                f"{fd['prev_year']}->{fd['latest_year']} supply mix; no origin grew "
                f"materially")
    else:
        note = None
    if unrep:
        # Don't repeat "likely reporting lag" if the note already said it
        # (the contraction case does); keep it otherwise (e.g. "driven by ...").
        tail = "" if (note and "reporting lag" in note) else " (likely reporting lag)"
        lag = (f"{unrep['count']} origins (${_m(unrep['prev_value_usd'])} in "
               f"{fd['prev_year']}) report no {fd['latest_year']} data yet{tail}")
        note = f"{note}; {lag}" if note else lag
    return note


def _momentum_context(mo) -> str | None:
    """Price-momentum phrased as CONTEXT ('accompanied by'), never cause."""
    if not mo:
        return None
    cls, m6, m12 = mo["class_6mo"], mo["m6_pct"], mo["m12_pct"]
    if cls in ("sharp rise", "rising", "sharp fall", "falling"):
        extra = (f", {m12:+.0f}% over 12 months"
                 if m12 is not None and abs(m12) >= MOM_SHARP_RISE * 100 else "")
        return f"accompanied by a {m6:+.0f}% 6-month price move ({cls}{extra})"
    if m12 is not None and abs(m12) >= MOM_12MO_MATERIAL * 100:
        return (f"the 6-month price was broadly stable but moved {m12:+.0f}% over 12 "
                f"months (price context, not a driver of concentration)")
    return "the world price was broadly stable"


def _what_moved(f: dict) -> str | None:
    """One 'What moved' line: arithmetic attribution + price context, kept to
    the 1-2 most material facts. Attribution is scoped to the supply mix;
    momentum is context only."""
    clauses = []
    note = concentration_driver_note(f)
    if note:
        clauses.append(note)
    ctx = _momentum_context(f["drivers"]["price_momentum"])
    if ctx:
        clauses.append(ctx)
    if not clauses:
        return None
    return "; ".join(clauses) + "."


def _pick_action(f: dict) -> str:
    """One concrete recommended action, chosen by rule and built only from
    fact-dict numbers. Ordered by what matters most for this corridor."""
    r = f["risk_latest"]
    top = f["top_origins"][0] if f["top_origins"] else None
    gate = f["provenance"]["mirror_gate"]

    if (gate and not gate["passed"] and f["provenance"]["newer_mirror_years_unscored"]
            and top):
        return (f"Validate the current supplier mix directly with contracted suppliers: "
                f"partner-country reporting covers only {gate['coverage_pct']:.0f}% of "
                f"UAE-reported {gate['gate_year']} value, so the {r['year']} picture "
                f"(top origin {top['origin']}, {top['share']*100:.0f}% of value) cannot "
                f"be confirmed from trade data for "
                f"{'/'.join(str(y) for y in f['provenance']['newer_mirror_years_unscored'])}.")
    if top and top["share"] >= 0.60:
        alts = f["alternatives"]
        lead = (f"Qualify a second origin for {f['commodity']['name'].lower()} — "
                f"{top['origin']} supplied {top['share']*100:.0f}% of "
                f"{r['year']} import value (${top['value_usd']/1e6:.0f}M), a single-source "
                f"exposure across {r['n_origins']} total origins.")
        if alts["has_alternatives"]:
            named = "; ".join(
                f"{c['origin']} ({_pct(c['share'])} share, present "
                f"{c['years_present']}/{c['total_years']} years)"
                for c in alts["candidates"][:3])
            return lead + f" Candidates from existing trade: {named}."
        return lead + " No proven alternative corridor exists in current trade data " \
                      "(all other origins under 1%)."
    if (f["monthly_trend"] and f["monthly_trend"]["direction"] == "rising"
            and r["composite_risk"] >= 50):
        return (f"Escalate monitoring: composite risk is {r['composite_risk']:.0f}/100 and "
                f"the rolling monitor moved {f['monthly_trend']['change']:+.1f} points over "
                f"the last {f['monthly_trend']['change_window_months']} months "
                f"(as of {f['monthly_trend']['latest_period']}).")
    if f["dependency"] and f["dependency"]["dependency_pct"] >= 80 and top:
        return (f"Review stock cover: {f['dependency']['dependency_pct']:.0f}% "
                f"import-dependent with {top['origin']} at {top['share']*100:.0f}% of "
                f"supply — domestic production ({f['dependency']['production_kt']:.0f} kt) "
                f"cannot buffer a corridor disruption.")
    return (f"Maintain current posture and keep the monthly monitor in view — risk "
            f"{r['composite_risk']:.0f}/100 with top origin {r['top_origin']} at "
            f"{r['top_origin_share']*100:.0f}%.")


def build_template_brief(f: dict) -> str:
    r = f["risk_latest"]
    name = f["commodity"]["name"]
    prov = f["provenance"]
    year_label = (f"{r['year']} (provisional, mirror-derived)" if prov["provisional"]
                  else f"{r['year']} (UAE-reported)")
    lines = [HEADER_LINE, f"{name} · data year {year_label}", ""]

    posture = (f"Composite corridor risk {r['composite_risk']:.1f}/100"
               f" (HHI {r['hhi']:.2f}, top origin {r['top_origin']} at"
               f" {r['top_origin_share']*100:.0f}%, {r['n_origins']} origins).")
    if f["risk_previous"]:
        p = f["risk_previous"]
        posture += (f" In {p['year']}: {p['composite_risk']:.1f}/100 with {p['top_origin']}"
                    f" at {p['top_origin_share']*100:.0f}%.")
    lines += [posture]

    if f["monthly_trend"] and f["monthly_trend"]["direction"]:
        m = f["monthly_trend"]
        lines += [f"Rolling monitor: {m['direction']} ({m['change']:+.1f} points over "
                  f"{m['change_window_months']} months, as of {m['latest_period']}; level "
                  f"not comparable to the annual score — read direction only)."]

    if f["top_origins"]:
        mix = ", ".join(f"{o['origin']} {_pct(o['share'])}" for o in f["top_origins"])
        lines += [f"Supply mix {r['year']} (${f['total_import_value_usd']/1e6:.0f}M "
                  f"import value): {mix}."]

    if f["dependency"]:
        d = f["dependency"]
        dep_line = (f"Import dependency {d['dependency_pct']:.0f}% by weight in {d['year']} "
                    f"({d['import_kt']:.0f} kt imported vs {d['production_kt']:.0f} kt "
                    f"domestic production)")
        if f["exposure_adjusted_risk"] is not None:
            dep_line += f" — exposure-adjusted risk {f['exposure_adjusted_risk']:.1f}/100"
        lines += [dep_line + "."]

    if f["latest_price"] and f["forecast"]:
        lines += [f"Price: {f['latest_price']['price']:.2f} {f['commodity']['price_unit']} "
                  f"({f['latest_price']['period']}); 6-month forecast {f['forecast']['direction']} "
                  f"({f['forecast']['pct_vs_latest_price']:+.1f}% by {f['forecast']['end_period']})."]

    moved = _what_moved(f)
    if moved:
        lines += [f"What moved: {moved}"]

    gate = prov["mirror_gate"]
    if prov["provisional"] and gate:
        lines += ["", f"Data currency: {r['year']} is reconstructed from partner countries' "
                  f"reported exports to the UAE (mirror statistics, FOB values); cross-check "
                  f"on the last overlap year ({gate['gate_year']}): mirror covered "
                  f"{gate['coverage_pct']:.0f}% of UAE-reported value."]
    elif gate and not gate["passed"] and prov["newer_mirror_years_unscored"]:
        years = "/".join(str(y) for y in prov["newer_mirror_years_unscored"])
        lines += ["", f"Data currency: {r['year']} is the last UAE-reported year. Mirror "
                  f"data exists for {years} but covers only {gate['coverage_pct']:.0f}% of "
                  f"UAE-reported {gate['gate_year']} value (threshold "
                  f"{gate['threshold_pct']:.0f}%) — a major origin is missing from partner "
                  f"reporting, so newer years are deliberately not scored rather than "
                  f"scored wrong."]

    lines += ["", f"Recommended for review: {_pick_action(f)}"]

    caveats = []
    if f["backtest"]:
        caveats.append(f"forecast backtest MAPE {f['backtest']['mape_pct']:.1f}% over "
                       f"{f['backtest']['horizon_months']} months")
    if gate:
        caveats.append(f"annual mirror coverage {gate['coverage_pct']:.0f}% "
                       f"({gate['gate_year']})")
    caveats += f["gaps"]
    lines += ["", "Confidence & caveats: " + ("; ".join(caveats) if caveats else "none") + ".",
              "", SIGNOFF_LINE]
    return "\n".join(lines)


# Numbers a brief may use without them appearing in the facts: share scale
# ("/100", "100%"), the "<1%" floor, and the 12 of "rolling 12-month".
STRUCTURAL_NUMBERS = {0.0, 1.0, 12.0, 100.0}
# A YYYY-MM period token, but NOT the leading half of a year range like
# "2024-2025" (the (?!\d) guard) — otherwise "2024-2025" mis-parses into a
# bogus "2024-20" period + a stray "25". A range falls through to the plain
# number branch and tokenizes as two years, each of which must trace.
_NUM_TOKEN = re.compile(r"\d{4}-\d{2}(?!\d)|\d+(?:\.\d+)?")


def _collect_fact_values(obj, nums: set, period_tokens: set) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "hs_code":  # identifier, not a quantity — must not
                continue        # vouch for nearby fabricated numbers
            _collect_fact_values(v, nums, period_tokens)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_fact_values(v, nums, period_tokens)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        v = float(obj)
        # The scalings the brief legitimately applies: shares → %,
        # USD → millions/billions, tonne-scale conversions.
        for scaled in (v, v * 100, v / 1e6, v / 1e9, v / 1e3):
            nums.add(abs(scaled))
    elif isinstance(obj, str):
        for tok in _NUM_TOKEN.findall(obj):
            if "-" in tok:  # 'YYYY-MM' period — keep whole, allow bare year too
                period_tokens.add(tok)
                nums.add(float(tok[:4]))
            else:
                nums.add(abs(float(tok)))


def verify_grounding(text: str, facts: dict) -> int:
    """Every number in the brief must trace to the fact dict, within the
    rounding the brief is allowed to apply (a token with d decimals may be
    off by half a unit in its last decimal, or 1.5% for round-number
    phrasing). Returns the count of numbers checked; raises ValueError
    naming the untraceable ones otherwise — the caller falls back to the
    deterministic template, so a fabricated number can never be displayed."""
    nums, period_tokens = set(), set()
    _collect_fact_values(facts, nums, period_tokens)
    allowed = nums | STRUCTURAL_NUMBERS
    checked, untraced = 0, []
    for tok in _NUM_TOKEN.findall(text.replace(",", "")):
        checked += 1
        if "-" in tok:
            if tok not in period_tokens:
                untraced.append(tok)
            continue
        x = float(tok)
        decimals = len(tok.partition(".")[2])
        tol = max(0.51 * 10 ** -decimals, 0.015 * x)
        if not any(abs(a - x) <= tol for a in allowed):
            untraced.append(tok)
    if untraced:
        raise ValueError(
            f"grounding check failed — numbers not traceable to the fact "
            f"dict: {sorted(set(untraced))}")
    return checked


def build_llm_brief(facts: dict) -> tuple[str, int]:
    """Claude writes the brief from the fact dict alone; the result must
    pass verify_grounding before it is returned. Raises on any API failure
    or grounding violation — the caller decides to fall back, never
    silently. No sampling parameters are sent (current Claude models reject
    them). Returns (text, numbers_traced)."""
    import anthropic  # lazy import — template mode must work without the SDK

    client = anthropic.Anthropic(api_key=get_anthropic_key())
    response = client.messages.create(
        model=get_brief_model(),
        max_tokens=8000,  # headroom includes the model's internal reasoning
        system=LLM_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (f"Fact JSON for the {facts['commodity']['name']} corridor:\n"
                        + json.dumps(facts, indent=2, default=str)),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise RuntimeError(f"empty response (stop_reason={response.stop_reason})")
    # Belt and braces: the human-in-the-loop framing is not negotiable, even
    # if the model drops a required line.
    if not text.startswith(HEADER_LINE):
        text = HEADER_LINE + "\n" + text
    if SIGNOFF_LINE not in text:
        text += "\n\n" + SIGNOFF_LINE
    return text, verify_grounding(text, facts)


def generate_brief(facts: dict, force_template: bool = False) -> tuple[str, str, str]:
    """Best-available brief for a fact dict. Returns (text, mode, detail):
    mode is 'llm' or 'template'; detail is the model name for LLM output,
    otherwise the honest reason template mode was used."""
    if force_template:
        return build_template_brief(facts), "template", "forced offline"
    if not get_anthropic_key():
        return (build_template_brief(facts), "template",
                "no ANTHROPIC_API_KEY configured")
    try:
        text, traced = build_llm_brief(facts)
        return (text, "llm",
                f"{get_brief_model()} · grounding check passed "
                f"({traced} numbers traced to the fact dict)")
    except Exception as exc:
        return (build_template_brief(facts), "template",
                f"LLM output rejected: {exc}")


# ---------------------------------------------------------------- CLI


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(f"Usage: python src/brief/corridor_brief.py <commodity> "
                 f"[--facts] [--template]\n"
                 f"Commodities: {', '.join(COMMODITIES)}")
    cid = args[0]
    conn = get_connection()
    facts = assemble_facts(conn, cid)
    conn.close()

    if "--facts" in sys.argv:
        print("FACT DICT (single source of truth for the brief):")
        print(json.dumps(facts, indent=2, default=str))
        print()

    text, mode, detail = generate_brief(facts, force_template="--template" in sys.argv)
    print(text)
    if mode == "llm":
        print(f"\n[mode: LLM-generated · {detail} · draft for human review]")
    else:
        print(f"\n[mode: template — deterministic, no AI · {detail}]")


if __name__ == "__main__":
    main()
