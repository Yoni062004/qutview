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
- Format, plain text only (no markdown):
  line 1 exactly: {HEADER_LINE}
  then a brief of at most 180 words covering risk posture (with the \
previous-year comparison), origin concentration and supply mix, import \
dependency, and price/forecast.
  then one line starting "Recommended for review:" with ONE concrete action \
grounded in the data — name the numbers that justify it, no vague advice.
  then one line starting "Confidence & caveats:" including the forecast \
backtest MAPE, the annual mirror coverage, and any gaps.
  last line exactly: {SIGNOFF_LINE}"""

# ---------------------------------------------------------------- layer 1


def _row(conn, query, params=()):
    cur = conn.execute(query, params)
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([c[0] for c in cur.description], row))


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
        return (f"Qualify a second origin for {f['commodity']['name'].lower()} — "
                f"{top['origin']} supplied {top['share']*100:.0f}% of "
                f"{r['year']} import value (${top['value_usd']/1e6:.0f}M), a single-source "
                f"exposure across {r['n_origins']} total origins.")
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
_NUM_TOKEN = re.compile(r"\d{4}-\d{2}|\d+(?:\.\d+)?")


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
