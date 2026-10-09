"""Alert thresholds (src/features/alerts.py) and the riser materiality floor.

compute_alerts() is driven by assemble_facts(); here it is fed hand-built
fact dicts so each threshold can be probed exactly at its boundary.
"""
import pytest

from brief import corridor_brief as cb
from features import alerts

M = 1e6


@pytest.fixture
def run_alerts(monkeypatch, facts_factory):
    """run_alerts(**facts_kwargs) -> alert list for a single fake commodity."""
    monkeypatch.setattr(alerts, "COMMODITIES", {"wheat": cb.COMMODITIES["wheat"]})

    def _run(**kw):
        facts = facts_factory(**kw)
        monkeypatch.setattr(alerts, "assemble_facts", lambda conn, cid: facts)
        return alerts.compute_alerts(conn=None)
    return _run


def _severity(feed, rule):
    hits = [a["severity"] for a in feed if a["rule"] == rule]
    assert len(hits) <= 1
    return hits[0] if hits else None


def test_documented_thresholds():
    assert alerts.CONCENTRATION_HIGH == 0.80
    assert alerts.CONCENTRATION_WATCH == 0.60
    assert alerts.RISK_HIGH == 65
    assert alerts.RISK_WATCH == 45
    assert cb.MATERIAL_RISER_USD == 1_000_000
    assert cb.MATERIAL_RISER_SHARE == 0.02


@pytest.mark.parametrize("share, expected", [
    (0.5999, None),
    (0.60, "watch"),
    (0.6001, "watch"),
    (0.7999, "watch"),
    (0.80, "high"),
    (0.8001, "high"),
    (1.0, "high"),
])
def test_concentration_boundaries(run_alerts, share, expected):
    assert _severity(run_alerts(share=share, risk=0), "single_source") == expected


@pytest.mark.parametrize("risk, expected", [
    (44.9, None),
    (45.0, "watch"),
    (45.1, "watch"),
    (64.9, "watch"),
    (65.0, "high"),
    (65.1, "high"),
])
def test_risk_boundaries(run_alerts, risk, expected):
    assert _severity(run_alerts(share=0.1, risk=risk), "elevated_risk") == expected


def test_concentration_crossing_down_from_high_lands_in_watch(run_alerts):
    assert _severity(run_alerts(share=0.85, risk=0), "single_source") == "high"
    after = run_alerts(share=0.79, risk=0)
    assert _severity(after, "single_source") == "watch"  # not cleared


def test_risk_crossing_down_from_high_lands_in_watch(run_alerts):
    assert _severity(run_alerts(share=0.1, risk=70), "elevated_risk") == "high"
    assert _severity(run_alerts(share=0.1, risk=64), "elevated_risk") == "watch"


def test_message_names_the_threshold_crossed(run_alerts):
    feed = run_alerts(share=0.80, risk=65)
    msgs = {a["rule"]: a["message"] for a in feed}
    assert "threshold 80%" in msgs["single_source"]
    assert "threshold 65" in msgs["elevated_risk"]


def test_provisional_alerts_are_labelled(run_alerts):
    feed = run_alerts(share=0.9, risk=70, provisional=True)
    assert feed and all("provisional, mirror-derived" in a["message"] for a in feed)


def test_high_sorts_before_watch(run_alerts):
    feed = run_alerts(share=0.65, risk=70)
    assert [a["severity"] for a in feed] == ["high", "watch"]


@pytest.mark.parametrize("change, fires", [(4.9, False), (5.0, True), (8.0, True)])
def test_rising_trend_boundary(run_alerts, change, fires):
    monthly = {"direction": "rising", "change": change, "change_window_months": 6,
               "latest_period": "2026-06", "latest_risk": 50}
    feed = run_alerts(share=0.1, risk=0, monthly=monthly)
    assert any(a["rule"] == "rising_trend" for a in feed) is fires


_FLOAT_BOUNDARY = pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "BUG (low impact): delta = share - prev_share is compared to 0.05 with no "
    "tolerance, so a rise of exactly 5 points fires or not depending on float "
    "noise: 0.60-0.55 = 0.04999... (does not fire), 0.65-0.60 = 0.05000...04 "
    "(fires). The message would print '+5 pts (threshold +5)' yet not alert."))


@pytest.mark.parametrize("share, prev_share, fires", [
    (0.60, 0.5501, False),
    (0.65, 0.60, True),
    pytest.param(0.60, 0.55, True, marks=_FLOAT_BOUNDARY),
    pytest.param(0.70, 0.65, True, marks=_FLOAT_BOUNDARY),
])
def test_yoy_concentration_climbing_boundary(run_alerts, share, prev_share, fires):
    prev = {"year": 2023, "composite_risk": 30, "top_origin": "Brazil",
            "top_origin_share": prev_share}
    feed = run_alerts(share=share, risk=0, previous=prev)
    assert any(a["rule"] == "concentration_climbing" for a in feed) is fires


def test_top_origin_change_is_a_shift_not_concentration(run_alerts):
    prev = {"year": 2023, "composite_risk": 30, "top_origin": "Ukraine",
            "top_origin_share": 0.40}
    feed = run_alerts(share=0.60, risk=0, top_origin="Russia", previous=prev)
    rules = {a["rule"] for a in feed}
    assert "top_origin_shift" in rules
    assert "concentration_climbing" not in rules


# --- materiality floor: who may be named as a "driver" -----------------------


def _fd(riser_change, total_latest, total_prev=None):
    """A flow decomposition with one riser (Oman) and a flat incumbent."""
    total_prev = total_prev if total_prev is not None else total_latest - riser_change
    return {
        "latest_year": 2024, "prev_year": 2023, "provisional": False,
        "incumbent": {"origin": "Brazil", "prev_value": 100 * M, "latest_value": 100 * M,
                      "change_usd": 0.0, "change_pct": 0.0},
        "risers": [{"origin": "Oman", "prev_value": 1.0, "latest_value": riser_change + 1,
                    "change_usd": riser_change, "change_pct": 999.0}],
        "fallers": [], "unreported": None,
        "total_prev": total_prev, "total_latest": total_latest,
        "market_change_pct": 0.0,
    }


@pytest.mark.parametrize("riser, market, named", [
    (0.99 * M, 10 * M, False),     # 9.9% of market but under $1M
    (5 * M, 300 * M, False),       # over $1M but only 1.7% of market
    (19.9 * M, 1000 * M, False),   # just under 2%
    (1 * M, 50 * M, True),         # exactly $1M AND exactly 2%
    (20 * M, 1000 * M, True),      # exactly 2%
    (30 * M, 300 * M, True),
])
def test_materiality_floor(facts_factory, riser, market, named):
    f = facts_factory(flow_decomposition=_fd(riser, market))
    note = cb.concentration_driver_note(f)
    assert ("Oman" in (note or "")) is named


def test_immaterial_riser_never_appears_in_an_alert(run_alerts):
    # The floor gates what an alert NAMES as the driver; the concentration
    # alert itself still fires on the share rule.
    prev = {"year": 2023, "composite_risk": 30, "top_origin": "Brazil",
            "top_origin_share": 0.50}
    feed = run_alerts(share=0.70, risk=0, previous=prev,
                      flow_decomposition=_fd(0.25 * M, 140 * M))
    climbing = [a for a in feed if a["rule"] == "concentration_climbing"]
    assert climbing
    assert all("Oman" not in a["message"] for a in feed)
