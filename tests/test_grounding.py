"""Grounding check (src/brief/corridor_brief.py) — the core integrity guarantee.

Every number in an LLM brief must trace to the fact dict, or the brief is
rejected and the deterministic template is shown instead. The LLM is replaced
by a fake `anthropic` module so the real build_llm_brief -> verify_grounding
-> generate_brief path runs offline.
"""
import sys
from types import ModuleType, SimpleNamespace

import pytest

from brief import corridor_brief as cb

M = 1e6


@pytest.fixture
def facts(db, score):
    """A realistic, fully populated fact dict (provisional poultry year with a
    lagging origin, prices, forecast, backtest, dependency, monthly series)."""
    flows_2023 = {"Brazil": 700 * M, "USA": 200 * M, "France": 100 * M}
    db.add_flows("fact_imports", 2023, "poultry", flows_2023, weight_kg=4e8)
    db.add_flows("fact_imports_mirror_annual", 2023, "poultry", flows_2023, weight_kg=4e8)
    db.add_coverage("poultry", 2023, 96.4)
    db.add_flows("fact_imports_mirror_annual", 2024, "poultry",
                 {"Brazil": 712 * M, "USA": 150 * M, "Ukraine": 40 * M}, weight_kg=4e8)
    db.add_prices("poultry", {f"{y}-{m:02d}": 2.0 + 0.01 * m + (0.1 if y == 2025 else 0)
                              for y in (2023, 2024, 2025) for m in range(1, 13)})
    with db.write() as conn:
        conn.execute("INSERT INTO forecasts VALUES ('2026-06', 'poultry', 2.31, 2.0, 2.6)")
        conn.execute("INSERT INTO backtest_metrics VALUES ('poultry', 6, 7.8, '2026-01-01')")
        conn.execute("INSERT INTO dependency_ratios VALUES (2023, 'poultry', 520.0, 48.0, 91.5)")
        conn.executemany("INSERT INTO risk_scores_monthly VALUES (?, 'poultry', .5, 'Brazil',"
                         " .7, 3, .02, ?)",
                         [(f"2025-{m:02d}", 55 + m * 0.6) for m in range(1, 13)])
    score()
    conn = db.connect()
    f = cb.assemble_facts(conn, "poultry")
    conn.close()
    return f


def grounded_text(f):
    r = f["risk_latest"]
    top = f["top_origins"][0]
    return "\n".join([
        cb.HEADER_LINE,
        f"Poultry corridor risk is {r['composite_risk']:.1f}/100 for {r['year']} "
        f"(provisional), with {top['origin']} at {top['share'] * 100:.0f}% of "
        f"${f['total_import_value_usd'] / 1e6:,.0f}M import value across "
        f"{r['n_origins']} origins. Import dependency is "
        f"{f['dependency']['dependency_pct']:.0f}%.",
        f"Recommended for review: qualify a second origin; {top['origin']} holds "
        f"{top['share'] * 100:.0f}%.",
        f"Confidence & caveats: backtest MAPE {f['backtest']['mape_pct']:.1f}% over "
        f"{f['backtest']['horizon_months']} months.",
        cb.SIGNOFF_LINE,
    ])


@pytest.fixture
def fake_llm(monkeypatch):
    """fake_llm(text=..., exc=...) — install a stand-in Anthropic client."""
    calls = []

    def _install(text=None, exc=None, stop_reason="end_turn"):
        class _Messages:
            def create(self, **kw):
                calls.append(kw)
                if exc:
                    raise exc
                return SimpleNamespace(stop_reason=stop_reason,
                                       content=[SimpleNamespace(type="text", text=text)])

        mod = ModuleType("anthropic")
        mod.Anthropic = lambda api_key=None: SimpleNamespace(messages=_Messages())
        monkeypatch.setitem(sys.modules, "anthropic", mod)
        monkeypatch.setattr(cb, "get_anthropic_key", lambda: "test-key")
        monkeypatch.setattr(cb, "get_brief_model", lambda: "fake-model")
        return calls
    return _install


# --- end-to-end: generate_brief ---------------------------------------------


def test_grounded_llm_brief_passes(facts, fake_llm):
    text = grounded_text(facts)
    calls = fake_llm(text=text)
    out, mode, detail = cb.generate_brief(facts)
    assert mode == "llm"
    assert out == text
    assert "grounding check passed" in detail
    # The model is only ever given the fact dict and the no-invention rules.
    assert calls[0]["system"] == cb.LLM_SYSTEM_PROMPT
    assert cb.DECISION_SUPPORT_RULE in calls[0]["system"]


@pytest.mark.parametrize("fabrication", [
    "Freight from Santos is $87/t.",               # invented figure
    "Stock cover is 43 days.",                      # invented figure
    "Russia could take 35% of the market.",         # invented share
    "Brazil fell by $105M last year.",              # computed difference
])
def test_fabricated_figure_is_rejected_and_template_used(facts, fake_llm, fabrication):
    text = grounded_text(facts).replace("Recommended for review:",
                                        fabrication + "\nRecommended for review:")
    fake_llm(text=text)
    out, mode, detail = cb.generate_brief(facts)
    assert mode == "template"
    assert detail.startswith("LLM output rejected: grounding check failed")
    assert out == cb.build_template_brief(facts)
    assert fabrication not in out


def test_rejection_names_the_untraced_number(facts):
    with pytest.raises(ValueError, match=r"\['87'\]"):
        cb.verify_grounding(grounded_text(facts) + "\nFreight is $87/t.", facts)


def test_api_failure_falls_back_to_template(facts, fake_llm):
    fake_llm(exc=RuntimeError("503 overloaded"))
    out, mode, detail = cb.generate_brief(facts)
    assert mode == "template"
    assert "503 overloaded" in detail


def test_refusal_falls_back_to_template(facts, fake_llm):
    fake_llm(text="", stop_reason="refusal")
    _, mode, detail = cb.generate_brief(facts)
    assert mode == "template" and "declined" in detail


def test_no_key_uses_template_without_calling_api(facts, monkeypatch):
    monkeypatch.setattr(cb, "get_anthropic_key", lambda: None)
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import would fail
    _, mode, detail = cb.generate_brief(facts)
    assert mode == "template" and "no ANTHROPIC_API_KEY" in detail


def test_llm_brief_always_gets_draft_header_and_signoff(facts, fake_llm):
    body = grounded_text(facts).replace(cb.HEADER_LINE + "\n", "").replace(cb.SIGNOFF_LINE, "")
    fake_llm(text=body)
    out, mode, _ = cb.generate_brief(facts)
    assert mode == "llm"
    assert out.startswith(cb.HEADER_LINE)
    assert cb.SIGNOFF_LINE in out


# --- the deterministic template must itself be grounded ----------------------


def test_template_brief_is_grounded(facts):
    text = cb.build_template_brief(facts)
    assert cb.verify_grounding(text, facts) > 10


def test_template_brief_is_grounded_on_sparse_facts(db, score):
    db.add_flows("fact_imports", 2022, "rice", {"India": 500 * M, "Pakistan": 120 * M})
    db.add_flows("fact_imports", 2023, "rice", {"India": 520 * M, "Thailand": 30 * M})
    score()
    conn = db.connect()
    f = cb.assemble_facts(conn, "rice")
    conn.close()
    assert f["gaps"]  # no prices / forecast / dependency in this fixture
    cb.verify_grounding(cb.build_template_brief(f), f)


# --- tokenizer and allow-list details ----------------------------------------


def test_hs_code_does_not_vouch_for_numbers(facts):
    with pytest.raises(ValueError):
        cb.verify_grounding("HS code 0207 means 207 tonnes", facts)


def test_period_tokens_must_match_exactly(facts):
    latest_period = facts["latest_price"]["period"]
    cb.verify_grounding(f"as of {latest_period}", facts)
    with pytest.raises(ValueError):
        cb.verify_grounding("as of 2019-07", facts)


def test_year_range_is_two_years_not_a_period(facts):
    cb.verify_grounding("2023-2024", facts)  # both years are in the facts


def test_thousands_separators_are_ignored(facts):
    total = facts["total_import_value_usd"] / 1e6
    cb.verify_grounding(f"${total:,.0f}M", facts)


def test_rounding_within_half_a_unit_passes(facts):
    risk = facts["risk_latest"]["composite_risk"]
    cb.verify_grounding(f"{risk:.0f}/100 and {risk:.2f}/100", facts)


# --- years: exact, with +/-1 slack only -----------------------------------


@pytest.mark.parametrize("claim", ["Supply will tighten by 2030.",
                                   "The worst level since 2015.",
                                   "Unchanged since 2021."])
def test_fabricated_year_is_rejected(facts, claim):
    # Fact years in this fixture: 2023-2026. Was a hole: any year within +/-30
    # of a fact year passed under the old 1.5% relative tolerance.
    with pytest.raises(ValueError):
        cb.verify_grounding(claim, facts)


@pytest.mark.parametrize("year", [2022, 2023, 2026, 2027])
def test_year_within_one_of_a_fact_year_passes(facts, year):
    # "last year" / "next year" phrasing around the 2023-2026 fact years.
    cb.verify_grounding(f"compared with {year}", facts)


def test_year_like_amount_still_passes_when_it_is_a_fact_value(facts_factory):
    # "$2,031M" tokenizes as 2031 — an amount, not a year. It must pass on an
    # exact (rounding) match even though 2031 is far from any fact year...
    f = facts_factory(year=2024)
    f["total_import_value_usd"] = 2_031.4e6
    cb.verify_grounding("total $2,031M in 2024", f)
    # ...but not on the loose 1.5% integer tolerance.
    with pytest.raises(ValueError):
        cb.verify_grounding("total $2,040M in 2024", f)


# --- decimals: rounding-only ---------------------------------------------------


@pytest.fixture
def sparse_facts(db, score):
    db.add_flows("fact_imports", 2023, "wheat", {"A": 624, "B": 376})
    score()
    conn = db.connect()
    f = cb.assemble_facts(conn, "wheat")
    conn.close()
    return f


def test_decimal_misquote_is_rejected(sparse_facts):
    # Was a hole: 1.5% applied to decimals too, so 62.4 could be quoted as 61.5.
    risk = sparse_facts["risk_latest"]["composite_risk"]  # 100*(.5*.53+.3*.624)
    cb.verify_grounding(f"risk {risk:.1f}/100", sparse_facts)
    with pytest.raises(ValueError):
        cb.verify_grounding(f"risk {risk - 0.6:.1f}/100", sparse_facts)


@pytest.mark.parametrize("fmt", ["{:.1f}", "{:.2f}", "{:.3f}"])
def test_decimal_rounding_of_a_fact_passes(sparse_facts, fmt):
    risk = sparse_facts["risk_latest"]["composite_risk"]
    cb.verify_grounding(fmt.format(risk), sparse_facts)


@pytest.mark.parametrize("offset", [0.1, -0.1])
def test_decimal_one_step_off_is_rejected(sparse_facts, offset):
    risk = sparse_facts["risk_latest"]["composite_risk"]
    with pytest.raises(ValueError):
        cb.verify_grounding(f"{round(risk, 1) + offset:.1f}", sparse_facts)


# --- integers: accepted limitation (left failing on purpose) -----------------


@pytest.mark.xfail(strict=True, raises=pytest.fail.Exception, reason=(
    "ACCEPTED LIMITATION, NOT A BUG TO FIX: integers keep a 1.5% tolerance for "
    "round-number phrasing ('$900M' for 902M). A fact dict holds dozens of values "
    "at five scalings, so an invented integer can coincidentally land within "
    "tolerance and pass; tightening this would reject valid briefs. The real "
    "guarantee is architectural (figures computed in assemble_facts() before the "
    "model runs). Keep this test: it documents the limit honestly."))
def test_invented_integer_near_a_fact_is_rejected(facts):
    total_m = facts["total_import_value_usd"] / 1e6
    invented = round(total_m * 1.012)  # 1.2% off: not rounding, still passes
    with pytest.raises(ValueError):
        cb.verify_grounding(f"total imports ${invented}M", facts)
