"""Reporting-lag guard (the "$0-drop rule").

On a provisional (mirror-derived) year, an origin that reported last year and
has nothing this year has most likely just not reported yet. It must be
disclosed as unreported, never attributed as a real fall to zero. This guard
has caught a false story in production four times; these tests exist so it
can never be removed silently.

Runs end to end on a synthetic DB: production risk model -> assemble_facts
-> driver note -> alert feed.
"""
import pytest

from brief import corridor_brief as cb
from features import alerts

M = 1e6


@pytest.fixture
def poultry_lag(db, score):
    """Poultry: UAE-reported 2023; 2024 is mirror-derived and France (which
    shipped $100M in 2023) has not reported 2024 yet. Brazil is flat."""
    flows_2023 = {"Brazil": 700 * M, "USA": 200 * M, "France": 100 * M}
    db.add_flows("fact_imports", 2023, "poultry", flows_2023)
    db.add_flows("fact_imports_mirror_annual", 2023, "poultry", flows_2023)
    db.add_coverage("poultry", 2023, 100.0)
    db.add_flows("fact_imports_mirror_annual", 2024, "poultry",
                 {"Brazil": 700 * M, "USA": 120 * M})  # USA genuinely fell
    score()
    conn = db.connect()
    facts = cb.assemble_facts(conn, "poultry")
    conn.close()
    return facts


def test_latest_year_is_provisional(poultry_lag):
    assert poultry_lag["risk_latest"]["year"] == 2024
    assert poultry_lag["provenance"]["provisional"] is True


def test_missing_origin_is_unreported_not_a_faller(poultry_lag):
    fd = poultry_lag["drivers"]["flow_decomposition"]
    assert fd["unreported"] is not None
    assert [o["origin"] for o in fd["unreported"]["origins"]] == ["France"]
    assert fd["unreported"]["prev_value_usd"] == pytest.approx(100 * M)
    assert "France" not in {m["origin"] for m in fd["fallers"]}


def test_genuine_decline_is_still_a_faller(poultry_lag):
    # The guard must not swallow real declines — only exact $0 drops.
    fallers = {m["origin"]: m for m in poultry_lag["drivers"]["flow_decomposition"]["fallers"]}
    assert "USA" in fallers
    assert fallers["USA"]["change_usd"] == pytest.approx(-80 * M)


def test_driver_note_explains_share_rise_as_reporting_lag(poultry_lag):
    note = cb.concentration_driver_note(poultry_lag)
    assert "reporting lag" in note
    assert "roughly flat" in note  # Brazil 700 -> 700
    for word in ("collapse", "exited", "fell to zero"):
        assert word not in note.lower()


def test_template_brief_carries_the_lag_disclosure(poultry_lag):
    text = cb.build_template_brief(poultry_lag)
    assert "reporting lag" in text
    assert "provisional" in text


def test_alert_carries_the_lag_explanation(poultry_lag, db, point_at, monkeypatch):
    # Brazil 70% -> 85% looks like concentration climbing; the alert must say why.
    point_at(alerts)
    monkeypatch.setattr(alerts, "COMMODITIES", {"poultry": cb.COMMODITIES["poultry"]})
    conn = db.connect()
    feed = alerts.compute_alerts(conn)
    conn.close()
    climbing = [a for a in feed if a["rule"] == "concentration_climbing"]
    assert climbing, f"expected a concentration_climbing alert, got {feed}"
    assert "reporting lag" in climbing[0]["message"]
    assert "provisional" in climbing[0]["message"]


def test_previous_top_origin_unreported_is_not_a_real_shift(db, score):
    # Sunflower case: last year's TOP origin has no data yet. The apparent
    # top-origin shift must be called provisional reporting lag.
    flows_2023 = {"Ukraine": 500 * M, "Russia": 300 * M, "Argentina": 100 * M}
    db.add_flows("fact_imports", 2023, "sunflower_oil", flows_2023)
    db.add_flows("fact_imports_mirror_annual", 2023, "sunflower_oil", flows_2023)
    db.add_coverage("sunflower_oil", 2023, 100.0)
    db.add_flows("fact_imports_mirror_annual", 2024, "sunflower_oil",
                 {"Russia": 320 * M, "Argentina": 110 * M})
    score()
    conn = db.connect()
    facts = cb.assemble_facts(conn, "sunflower_oil")
    conn.close()
    note = cb.concentration_driver_note(facts)
    assert "previous top origin Ukraine" in note
    assert "reporting lag" in note


def test_guard_is_scoped_to_provisional_years(db, score):
    # Characterisation: on UAE-reported (customs) years every partner is
    # published at once, so an absent origin IS a real zero and counts as a
    # faller. If this changes, it should be a deliberate decision.
    db.add_flows("fact_imports", 2022, "rice", {"India": 500 * M, "Pakistan": 100 * M})
    db.add_flows("fact_imports", 2023, "rice", {"India": 520 * M})
    score()
    conn = db.connect()
    facts = cb.assemble_facts(conn, "rice")
    conn.close()
    fd = facts["drivers"]["flow_decomposition"]
    assert facts["provenance"]["provisional"] is False
    assert fd["unreported"] is None
    assert "Pakistan" in {m["origin"] for m in fd["fallers"]}
